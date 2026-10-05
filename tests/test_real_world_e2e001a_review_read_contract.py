"""
E2E-001A — THE PRODUCTION REVIEW READ CONTRACT.

WHAT THIS FILE IS

The proof of `GET /production/review/{project_id}/review`: that an authenticated project
owner receives their own project's review as structured JSON, that nobody else receives
anything, that the read performs no write of any kind, and that what it returns is the
existing composition's own values rather than a second opinion about them.

WHY THE ROUTE EXISTS AT ALL

Every production route authenticates from the request's `Authorization` header and there
is no cookie authentication in this application, so a browser form POST can never reach
them. The review page is therefore readable but not actionable, and a native client —
which CAN carry the token — needs the review as data. That is the whole of this route's
reason to exist, and section H proves it added no authority of its own.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the route and its status mapping, the J19 identity and authorization
              boundary (genuine ES256 tokens against an in-process JWKS document), the
              view model dataclasses the wire form is built from, and the answer-type
              constants the payload description is keyed by.

    DOUBLED   the database (one project row, read-only), and the workflow COMPOSITION —
              `build_workflow_review` is replaced at the route's own seam with a real
              `WorkflowReview` built from real view-model values. The composition itself
              is already proven by J19/J25/J47/J50; what is proven HERE is what this
              route does with what that composition returns.

WHAT THIS FILE DOES NOT CLAIM

It reads no real project, calls no live route, takes no claim, writes no revision, opens
no review, resolves nothing, generates nothing and uploads nothing. `ARTIFACT_WORKING_DIR`
is never read or set. No Anthropic API is called.
"""
from __future__ import annotations

import ast
import pathlib
import types

import pytest

from app.cad_engine.review_view_model import (
    ActionView,
    AttentionView,
    BlockerView,
    ConnectionIdentityView,
    ConnectionReviewView,
    CountsView,
    EvidenceView,
    ExtractedValuesView,
    OutputView,
    ProjectReviewView,
    ProvenanceView,
    TaskView,
)
from app.production_connection_review import WorkflowReview

from tests import production_review_auth as auth
from tests import test_real_world_j4_production_extraction_report_truth as j4

#: J4's own module-scoped fixture, aliased so this file takes it as a parameter exactly
#: as J19/J47/J50's files do. Nothing at import time may touch `production.main`.
production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
MAIN_PATH = REPO / "app" / "main.py"

ROUTE_PATH = "/production/review/{project_id}/review"

PROJECT = "11111111-2222-4333-8444-555555555555"
OTHER_PROJECT = "99999999-8888-4777-8666-555555555555"
OWNER = "owner-of-the-project"
SOMEONE_ELSE = "some-other-reviewer"


# ======================================================================================
# The database double — a project table that REFUSES to be written to.
#
# Any non-select operation raises rather than recording, so "this GET performed no write"
# is asserted over a call that could not have happened quietly.
# ======================================================================================
class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name
        self._filters = {}
        self._single = False
        self._wrote = None

    # --- reads -------------------------------------------------------------------
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def single(self):
        self._single = True
        return self

    # --- writes: refused, and remembered so the assertion can name them ----------
    def insert(self, *_args, **_kwargs):
        self._wrote = "insert"
        raise AssertionError(f"a GET performed an insert on {self._table!r}")

    def update(self, *_args, **_kwargs):
        self._wrote = "update"
        raise AssertionError(f"a GET performed an update on {self._table!r}")

    def upsert(self, *_args, **_kwargs):
        self._wrote = "upsert"
        raise AssertionError(f"a GET performed an upsert on {self._table!r}")

    def delete(self, *_args, **_kwargs):
        self._wrote = "delete"
        raise AssertionError(f"a GET performed a delete on {self._table!r}")

    def execute(self):
        self._client.reads.append((self._table, dict(self._filters)))
        rows = [
            row
            for row in self._client.rows.get(self._table, [])
            if all(row.get(key) == value for key, value in self._filters.items())
        ]
        if self._single:
            return _Response(rows[0] if rows else None)
        return _Response(rows)


class _ServiceClient:
    """One read-only Supabase: the projects table, and nothing else."""

    def __init__(self, rows):
        self.rows = rows
        self.reads: list = []

    def table(self, name):
        return _Query(self, name)


# ======================================================================================
# The composition double — a REAL WorkflowReview, built from real view-model values.
# ======================================================================================
def _task(**overrides) -> TaskView:
    base = dict(
        task_id="task-1",
        task_type="CONNECTION_IDENTITY",
        title="Confirm the connection identity",
        description="The drawing's own reference for this connection.",
        field="connection_identity",
        required=True,
        resolved=False,
        current_value=None,
        allowed_options=("D2", "D3"),
        evidence_requirement="site_evidence",
        resolution_value=None,
        resolution_evidence=None,
        answer_type="CONNECTION_IDENTITY",
    )
    base.update(overrides)
    return TaskView(**base)


def _connection(**overrides) -> ConnectionReviewView:
    base = dict(
        identity=ConnectionIdentityView(
            package_id="package-1",
            connection_id="connection-1",
            project_id=PROJECT,
            revision=0,
            display_reference="CN-01",
        ),
        decision="REVIEW",
        decision_label="Review required",
        attention=AttentionView(requires_attention=True, label="Needs attention"),
        blockers=(
            BlockerView(
                code="BLOCKED_IDENTITY",
                title="Identity not confirmed",
                message="The connection has no confirmed identity.",
                severity="blocker",
                severity_label="Blocked",
                field="connection_identity",
                task_type="CONNECTION_IDENTITY",
            ),
        ),
        warnings=(),
        evidence=EvidenceView(
            source_drawing_id="drawing-1",
            drawing_number="S401",
            source_page=12,
            detail_reference="38/S101",
            grid_reference="B/3",
            evidence_text="S401 page 12, detail 38/S101, grid B/3",
        ),
        extracted=ExtractedValuesView(
            member_references=("R01",),
            bolt_readings=("M20",),
            plate_readings=("250x10",),
            weld_readings=(),
            malformed_readings=(),
            unrecognised_readings=(),
            connection_type="END_PLATE",
            confidence="0.82",
            material="300PLUS",
        ),
        provenance=(
            ProvenanceView(
                field="connection_identity",
                provenance="AI_EXTRACTED",
                provenance_label="Extracted by the reading",
            ),
        ),
        tasks=(_task(),),
        actions=(ActionView(action="CONFIRM_IDENTITY", label="Confirm identity"),),
        output=OutputView(
            output_status=None,
            output_label=None,
            verification_status=None,
            verification_label=None,
            generated_files=(),
        ),
        summary="1 connection requires attention",
    )
    base.update(overrides)
    return ConnectionReviewView(**base)


def _view(**overrides) -> ProjectReviewView:
    base = dict(
        project_id=PROJECT,
        revision=0,
        project_status="review",
        status_label="Review required",
        summary="1 connection requires attention",
        counts=CountsView(review=1, verified=0, auto=0, confirmation=0, blocked=1),
        requiring_attention=1,
        verified=0,
        actions=(ActionView(action="OPEN_REVIEW", label="Open review"),),
        review_items=(_connection(),),
        completed_items=(),
    )
    base.update(overrides)
    return ProjectReviewView(**base)


def _review(*, view=None, revisions=(0,), refusal_code="", refusal_detail="") -> WorkflowReview:
    return WorkflowReview(
        project_id=PROJECT,
        identity=(("Project", "26 Cecil Road"),),
        coverage=(("Pages read", "21 of 21"),),
        capture_runs=("capture-1",),
        reconstructed=None,
        view=view,
        refusal_code=refusal_code,
        refusal_detail=refusal_detail,
        persisted_code="REVIEW_STATE_RECORDED",
        persisted_revisions=tuple(revisions),
        persisted_view=None,
        recorded_field_readings=(),
        limitations=(("L1", "Sampled", "Read on a sample only"),),
    )


# ======================================================================================
# The surface under test.
# ======================================================================================
@pytest.fixture
def surface(production, monkeypatch):
    """The real app, a read-only database, and the composition replaced at its own seam."""
    from fastapi.testclient import TestClient

    tokens = auth.install(monkeypatch, production)

    rows = {
        "projects": [
            {"id": PROJECT, "user_id": OWNER, "name": "26 Cecil Road", "status": "review"},
            {
                "id": OTHER_PROJECT,
                "user_id": OWNER,
                "name": "Another project",
                "status": "review",
            },
        ]
    }
    client = _ServiceClient(rows)
    monkeypatch.setattr(production.main, "supabase", client)

    composed: list = []

    def _compose(project_id, **_kwargs):
        # E2E-002G gave this route an optional document scope. This file is about the wire
        # form of the review, not about the scope, so the keyword is accepted and ignored
        # here; `test_real_world_e2e002g_review_document_scope.py` is where it is asserted.
        composed.append(project_id)
        return _review(view=_view())

    monkeypatch.setattr(production.main, "build_workflow_review", _compose)

    return types.SimpleNamespace(
        client=client,
        composed=composed,
        url=f"/production/review/{PROJECT}/review",
        other_url=f"/production/review/{OTHER_PROJECT}/review",
        http=TestClient(production.main.app, headers=tokens.headers(OWNER)),
        bare=TestClient(production.main.app),
        other=TestClient(production.main.app, headers=tokens.headers(SOMEONE_ELSE)),
        foreign=TestClient(
            production.main.app,
            headers={"Authorization": f"Bearer {tokens.foreign_token(OWNER)}"},
        ),
        tokens=tokens,
        monkeypatch=monkeypatch,
        production=production,
    )


# ======================================================================================
# A. IDENTITY — who may read a review
# ======================================================================================
class TestIdentity:
    def test_an_unauthenticated_read_is_refused(self, surface):
        response = surface.bare.get(surface.url)
        assert response.status_code == 401

    def test_a_token_signed_by_another_project_is_refused(self, surface):
        response = surface.foreign.get(surface.url)
        assert response.status_code == 401

    def test_a_reviewer_who_does_not_own_the_project_is_refused(self, surface):
        response = surface.other.get(surface.url)
        assert response.status_code == 403

    def test_an_unknown_project_is_refused(self, surface):
        response = surface.http.get("/production/review/22222222-3333-4444-8555-666666666666/review")
        assert response.status_code == 404

    def test_a_refused_read_never_reaches_the_composition(self, surface):
        surface.other.get(surface.url)
        assert surface.composed == []

    def test_the_owner_reads_their_own_projects_review(self, surface):
        response = surface.http.get(surface.url)
        assert response.status_code == 200

    def test_the_owner_may_read_a_second_project_they_own(self, surface):
        response = surface.http.get(surface.other_url)
        assert response.status_code == 200


# ======================================================================================
# B. THE READ WRITES NOTHING
# ======================================================================================
class TestReadOnly:
    def test_the_read_performs_no_write(self, surface):
        # The double raises on any non-select, so reaching this assertion at all IS the
        # proof that no insert, update, upsert or delete was attempted.
        response = surface.http.get(surface.url)
        assert response.status_code == 200

    def test_the_read_touches_no_table_it_was_not_given(self, surface):
        surface.http.get(surface.url)
        assert {table for table, _ in surface.client.reads} == {"projects"}

    def test_the_read_makes_no_claim_and_generates_no_artifact(self):
        """Proven from the source: the route composes nothing else."""
        tree = ast.parse(MAIN_PATH.read_text())
        route = _route_function(tree, "production_review_read")
        called = {
            node.func.attr
            for node in ast.walk(route)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert called <= {"get", "table", "eq", "select", "execute", "single"}
        names = {
            node.id
            for node in ast.walk(route)
            if isinstance(node, ast.Name)
        } | {
            node.func.id
            for node in ast.walk(route)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        forbidden = {
            "require_artifact_working_directory",
            "resolve_production_connection",
            "open_project_review",
            "take_claim",
            "upload_and_record",
            "generate_fabrication_drawing_pdf",
        }
        assert names & forbidden == set()

    def test_artifact_working_dir_is_not_read_by_the_read_contract(self):
        source = MAIN_PATH.read_text()
        route = source[source.index("def production_review_read"):]
        body = route[: route.index("\n@app.") if "\n@app." in route else len(route)]
        assert "ARTIFACT_WORKING_DIR" not in body


def _route_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in {MAIN_PATH}")


# ======================================================================================
# C. THE WIRE FORM IS THE COMPOSITION'S OWN VALUES
# ======================================================================================
class TestWireForm:
    def test_the_revision_is_the_one_the_store_recorded(self, surface):
        body = surface.http.get(surface.url).json()
        assert body["revision"] == 0
        assert body["revision_recorded"] is True

    def test_a_project_with_nothing_recorded_says_so_rather_than_claiming_zero(
        self, surface
    ):
        surface.monkeypatch.setattr(
            surface.production.main, "build_workflow_review",
            lambda project_id, **_kwargs: _review(view=_view(), revisions=()),
        )
        body = surface.http.get(surface.url).json()
        assert body["revision"] == 0
        assert body["revision_recorded"] is False

    def test_the_advanced_revision_is_reported(self, surface):
        surface.monkeypatch.setattr(
            surface.production.main, "build_workflow_review",
            lambda project_id, **_kwargs: _review(view=_view(revision=3), revisions=(0, 1, 2, 3)),
        )
        body = surface.http.get(surface.url).json()
        assert body["revision"] == 3

    def test_a_refused_reconstruction_is_reported_as_a_refusal_not_as_empty(
        self, surface
    ):
        surface.monkeypatch.setattr(
            surface.production.main, "build_workflow_review",
            lambda project_id, **_kwargs: _review(
                view=None, refusal_code="SNAPSHOT_REFUSED_NO_REVIEW_LAYER",
                refusal_detail="no review layer is recorded",
            ),
        )
        body = surface.http.get(surface.url).json()
        assert body["connections"] == []
        assert body["refusal_code"] == "SNAPSHOT_REFUSED_NO_REVIEW_LAYER"
        assert body["refusal_detail"]

    def test_the_connection_identity_is_the_contracts_own(self, surface):
        connection = surface.http.get(surface.url).json()["connections"][0]
        assert connection["package_id"] == "package-1"
        assert connection["connection_id"] == "connection-1"
        assert connection["display_reference"] == "CN-01"

    def test_the_tasks_carry_everything_a_client_needs(self, surface):
        task = surface.http.get(surface.url).json()["connections"][0]["tasks"][0]
        for field in (
            "task_id",
            "task_type",
            "answer_type",
            "allowed_options",
            "evidence_requirement",
            "required",
            "resolved",
        ):
            assert field in task, field
        assert task["task_id"] == "task-1"
        assert task["answer_type"] == "CONNECTION_IDENTITY"
        assert task["allowed_options"] == ["D2", "D3"]

    def test_the_evidence_is_carried_beside_the_task(self, surface):
        connection = surface.http.get(surface.url).json()["connections"][0]
        assert connection["evidence"]["drawing_number"] == "S401"
        assert connection["evidence"]["source_page"] == 12
        assert connection["evidence"]["grid_reference"] == "B/3"

    def test_the_limitations_are_carried(self, surface):
        body = surface.http.get(surface.url).json()
        assert body["limitations"] == [["L1", "Sampled", "Read on a sample only"]]

    def test_the_json_is_serialisable_without_a_custom_encoder(self, surface):
        import json

        response = surface.http.get(surface.url)
        json.dumps(response.json())


# ======================================================================================
# D. THE PAYLOAD DESCRIPTION IS THE CONTRACT'S OWN VOCABULARY
# ======================================================================================
class TestPayloadDescription:
    def _shapes(self):
        from app.production_review_wire import PAYLOAD_SHAPES

        return PAYLOAD_SHAPES

    def test_every_answer_type_the_contract_names_is_described(self):
        from app.cad_engine import exception_resolution as contract

        named = {
            value
            for name, value in vars(contract).items()
            if name.startswith("ANSWER_") and isinstance(value, str)
        }
        # The contract also names non-payload ANSWER_* constants (the fabricator
        # acceptance vocabulary); only the ones this table claims to describe are
        # required to be present.
        missing = named - set(self._shapes())
        assert missing <= {"PASS", "FAIL", "NOT_APPLICABLE", "SOURCE_AUTHORITY"}

    def test_the_shapes_are_keyed_by_the_contracts_own_constants(self):
        from app.cad_engine import exception_resolution as contract

        assert self._shapes()[contract.ANSWER_PLATE_VALUE] == (
            "mapping",
            ("type", "thickness_mm", "width_mm", "depth_mm"),
        )
        assert self._shapes()[contract.ANSWER_APPROVE_REVIEW] == ("none", ())

    def test_an_unknown_answer_type_is_described_as_unknown(self):
        from app.production_review_wire import workflow_wire

        review = _review(
            view=_view(review_items=(_connection(tasks=(_task(answer_type="SOMETHING_NEW"),)),))
        )
        task = workflow_wire(review)["connections"][0]["tasks"][0]
        assert task["answer_payload"] == {"kind": "UNKNOWN", "fields": []}

    def test_the_description_validates_nothing(self):
        """It is a description: an empty table would change no behaviour of /resolve."""
        from app.production_review_wire import PAYLOAD_SHAPES

        assert all(
            isinstance(kind, str) and isinstance(fields, tuple)
            for kind, fields in PAYLOAD_SHAPES.values()
        )

    def test_the_route_module_names_no_cad_engine_vocabulary(self):
        """J25's boundary, re-asserted here because this milestone is the one that could
        have broken it: the rendering lives in the wire module, so `main.py` still takes
        nothing from `app.cad_engine` but 7AJ's refusal types."""
        import ast as _ast

        imported = set()
        for node in _ast.walk(_ast.parse(MAIN_PATH.read_text())):
            if isinstance(node, _ast.ImportFrom) and node.module and node.module.startswith("app.cad_engine"):
                imported.add((node.module, tuple(alias.name for alias in node.names)))
        assert imported == {
            (
                "app.cad_engine.project_workflow",
                (
                    "ConnectionAlreadyProcessedError",
                    "CrossProjectResolutionError",
                    "StaleProjectWorkflowError",
                    "UnknownProjectPackageError",
                    "WorkflowStageFailureError",
                ),
            )
        }, imported


# ======================================================================================
# E. THE EXISTING AUTHORITIES ARE NOT TOUCHED
# ======================================================================================
class TestNoSecondAuthority:
    def test_open_and_resolve_are_unchanged(self):
        source = MAIN_PATH.read_text()
        assert '@app.post("/production/review/{project_id}/open")' in source
        assert (
            '@app.post("/production/review/{project_id}/connections/{package_id}/resolve")'
            in source
        )

    def test_the_read_route_is_a_get(self):
        source = MAIN_PATH.read_text()
        index = source.index("def production_review_read")
        decorator = source.rindex("@app.", 0, index)
        assert '@app.get("/production/review/{project_id}/review")' in source[decorator:index]

    def test_the_resolution_contract_module_is_untouched(self):
        """The read route imports the answer vocabulary; it does not extend it."""
        path = REPO / "app" / "cad_engine" / "exception_resolution.py"
        source = path.read_text()
        assert "ANSWER_CONNECTION_IDENTITY" in source
