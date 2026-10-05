"""
E2E-002G — THE DOCUMENT A REVIEW IS ABOUT.

WHAT THIS FILE IS

The proof that a multi-document project can be reviewed by being TOLD which document the
review concerns — and that being told is the only way it happens.

E2E-002F established the shape of the gap: `reconstruct_project_workflow` has accepted a
`document_id` since J61, with a documented contract for it, and no production caller ever
supplied one. So a project whose readings belong to two documents refused with
`RECONSTRUCTION_AMBIGUOUS_DOCUMENT` — correctly — and nothing in the product could make the
choice the guard was waiting for.

This file proves the plumbing, not the guard. The guard is unchanged and section D below
asserts that it still refuses exactly as it did.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the routes and their status mapping, the J19 identity and authorization
              boundary, `build_workflow_review`'s forwarding, `review_documents`' own
              eligibility rule, and the wire form.

    DOUBLED   the database. Every double RECORDS what it was asked, so "the reconstruction
              was given this document" and "no reconstruction ran" are asserted over calls
              that could have happened.

WHAT THIS FILE DOES NOT CLAIM

It reads no real project, extracts nothing, opens no review, resolves nothing and creates
no artifact. The E2E-002 evidence project is never touched.
"""
from __future__ import annotations

import ast
import pathlib
import types

import pytest

from app.production_connection_review import review_documents

from tests import production_review_auth as auth
from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
MAIN_PATH = REPO / "app" / "main.py"
REVIEW_PATH = REPO / "app" / "production_connection_review.py"
RESOLUTION_PATH = REPO / "app" / "production_review_resolution.py"
RESUMPTION_PATH = REPO / "app" / "production_review/project_workflow_resumption.py"
GUARD_PATH = REPO / "app" / "production_review/project_workflow_reconstruction.py"

PROJECT = "11111111-2222-4333-8444-555555555555"
OTHER_PROJECT = "99999999-8888-4777-8666-555555555555"
OWNER = "owner-of-the-project"
SOMEONE_ELSE = "some-other-reviewer"

DOC_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
DOC_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
DOC_ELSEWHERE = "cccccccc-3333-4333-8333-cccccccccccc"


# ======================================================================================
# A doubled store, shaped the way `review_documents` reads one.
# ======================================================================================
class _Store:
    def __init__(self, *, documents, sets, drawings, captures):
        self._documents = documents
        self._sets = sets
        self._drawings = drawings
        self._captures = captures

    def project_documents_for_project(self, project_id):
        return [d for d in self._documents if d["project_id"] == project_id]

    def drawing_sets_for_project(self, project_id):
        return [s for s in self._sets if s["project_id"] == project_id]

    def drawings_for_drawing_set(self, drawing_set_id):
        return [d for d in self._drawings if d["drawing_set_id"] == drawing_set_id]

    def page_extraction_captures_for_drawing(self, drawing_id):
        return self._captures.get(drawing_id, [])


def _two_document_store(project_id=PROJECT):
    return _Store(
        documents=[
            {"id": DOC_A, "project_id": project_id, "file_name": "a.pdf", "role": "UNKNOWN"},
            {"id": DOC_B, "project_id": project_id, "file_name": "b.pdf", "role": "STRUCTURAL_GA"},
        ],
        sets=[{"id": "set-a", "project_id": project_id}, {"id": "set-b", "project_id": project_id}],
        drawings=[
            {"id": "drw-a", "drawing_set_id": "set-a", "document_id": DOC_A},
            {"id": "drw-b", "drawing_set_id": "set-b", "document_id": DOC_B},
        ],
        captures={"drw-a": [{"page_number": 1}], "drw-b": [{"page_number": 1}]},
    )


# ======================================================================================
# G. ELIGIBILITY IS THE RECONSTRUCTION'S OWN CRITERION, NOT A HEURISTIC
# ======================================================================================
class TestEligibility:
    def test_a_document_whose_drawing_has_captures_has_readings(self):
        documents = review_documents(PROJECT, repository=_two_document_store())
        assert [(d["document_id"], d["has_readings"]) for d in documents] == [
            (DOC_A, True),
            (DOC_B, True),
        ]

    def test_a_document_with_no_captures_is_reported_as_having_none(self):
        store = _two_document_store()
        store._captures = {"drw-a": [{"page_number": 1}]}
        documents = review_documents(PROJECT, repository=store)
        assert {d["document_id"]: d["has_readings"] for d in documents} == {
            DOC_A: True,
            DOC_B: False,
        }

    def test_a_document_nothing_has_read_is_still_listed(self):
        """A project that holds a document nothing has read says so rather than omitting
        it: "two documents" and "two documents, one read" are different facts."""
        store = _two_document_store()
        store._drawings = [{"id": "drw-a", "drawing_set_id": "set-a", "document_id": DOC_A}]
        store._captures = {"drw-a": [{"page_number": 1}]}
        documents = review_documents(PROJECT, repository=store)
        assert [d["document_id"] for d in documents] == [DOC_A, DOC_B]
        assert documents[1]["has_readings"] is False

    def test_eligibility_is_not_taken_from_the_filename_role_or_page_count(self):
        """The only input is whether captures exist. Two documents differing in every other
        respect — name, role — differ in `has_readings` only by their captures."""
        store = _two_document_store()
        store._captures = {"drw-b": [{"page_number": 1}]}
        documents = review_documents(PROJECT, repository=store)
        assert {d["document_id"]: d["has_readings"] for d in documents} == {
            DOC_A: False,
            DOC_B: True,
        }

    def test_it_does_not_read_a_role(self):
        """A role is a human ASSERTION whose read model J64 deliberately keeps narrow, and
        nothing here needs it — a document is selected by its id and judged eligible by its
        captures. The fixture's rows carry roles; none is returned."""
        documents = review_documents(PROJECT, repository=_two_document_store())
        assert documents, "the fixture must carry documents, or this proves nothing"
        assert all("role" not in document for document in documents)

    def test_it_reads_only_the_project_it_was_given(self):
        store = _two_document_store()
        store._documents.append(
            {"id": DOC_ELSEWHERE, "project_id": OTHER_PROJECT, "file_name": "z.pdf", "role": None}
        )
        documents = review_documents(PROJECT, repository=store)
        assert DOC_ELSEWHERE not in {d["document_id"] for d in documents}


# ======================================================================================
# C/E. THE PLUMBING — every seam carries the scope
# ======================================================================================
class TestPlumbing:
    def _kwargs_at(self, path, function_name, callee):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == callee
                    ):
                        return {kw.arg for kw in inner.keywords}
        raise AssertionError(f"{callee} is not called from {function_name} in {path}")

    def test_build_workflow_review_forwards_the_document(self):
        kwargs = self._kwargs_at(REVIEW_PATH, "build_workflow_review", "reconstruct_project_workflow")
        assert "document_id" in kwargs

    def test_build_workflow_review_accepts_it(self):
        tree = ast.parse(REVIEW_PATH.read_text(encoding="utf-8"))
        fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "build_workflow_review"
        )
        assert "document_id" in {a.arg for a in fn.args.kwonlyargs}

    def test_the_resolution_reaches_the_reconstruction_with_it(self):
        kwargs = self._kwargs_at(RESOLUTION_PATH, "_resolve", "resume_project_workflow")
        assert "document_id" in kwargs

    def test_resume_project_workflow_forwards_it_to_the_reconstruction(self):
        kwargs = self._kwargs_at(
            RESUMPTION_PATH, "resume_project_workflow", "reconstruct_project_workflow"
        )
        assert "document_id" in kwargs

    def test_resolve_production_connection_carries_it_to_resolve(self):
        kwargs = self._kwargs_at(RESOLUTION_PATH, "resolve_production_connection", "_resolve")
        assert "document_id" in kwargs


# ======================================================================================
# A/B. THE GUARD IS UNCHANGED AND STILL REFUSES AN UNMADE CHOICE
# ======================================================================================
class TestGuardIsUntouched:
    def test_the_reconstruction_module_was_not_modified(self):
        """The guard is the thing this wave must not touch, so it is asserted from the
        source: the refusal still exists, still fires on more than one document, and the
        module still has no knowledge of `review_documents`."""
        source = GUARD_PATH.read_text(encoding="utf-8")
        assert "RECONSTRUCTION_AMBIGUOUS_DOCUMENT" in source
        assert "review_documents" not in source

        tree = ast.parse(source)
        fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "reconstruct_project_workflow"
        )
        unparsed = ast.unparse(fn)
        assert "len(documents) > 1" in unparsed
        assert "RECONSTRUCTION_AMBIGUOUS_DOCUMENT" in unparsed

    def test_the_guard_still_defaults_to_no_document(self):
        tree = ast.parse(GUARD_PATH.read_text(encoding="utf-8"))
        fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "reconstruct_project_workflow"
        )
        defaults = dict(zip([a.arg for a in fn.args.kwonlyargs], fn.args.kw_defaults))
        assert ast.unparse(defaults["document_id"]) == "None"


# ======================================================================================
# D/F. THE ROUTES — ownership, and the scope a client is allowed to see
# ======================================================================================
class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self._client, self._table, self._filters = client, table, {}
        self._single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        rows = [
            row for row in self._client.rows.get(self._table, [])
            if all(row.get(k) == v for k, v in self._filters.items())
        ]
        return _Response(rows[0] if self._single and rows else (None if self._single else rows))


class _ReadOnlyClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Query(self, name)


@pytest.fixture
def surface(production, monkeypatch):
    from fastapi.testclient import TestClient

    tokens = auth.install(monkeypatch, production)
    monkeypatch.setattr(
        production.main, "supabase",
        _ReadOnlyClient({"projects": [
            {"id": PROJECT, "user_id": OWNER, "name": "A project", "status": "review"},
        ]}),
    )
    # The project's documents are this project's own: DOC_A and DOC_B, and nothing else.
    monkeypatch.setattr(
        production.main, "review_documents",
        lambda project_id: [
            {"document_id": DOC_A, "file_name": "a.pdf", "has_readings": True},
            {"document_id": DOC_B, "file_name": "b.pdf", "has_readings": True},
        ],
    )
    composed: list = []

    def _compose(project_id, *, document_id=None, **_kwargs):
        composed.append(document_id)
        return types.SimpleNamespace(
            project_id=project_id, identity=(), coverage=(), capture_runs=(),
            reconstructed=None, view=None, refusal_code="", refusal_detail="",
            persisted_code="", persisted_revisions=(), persisted_view=None,
            recorded_field_readings=(), limitations=(),
        )

    monkeypatch.setattr(production.main, "build_workflow_review", _compose)
    monkeypatch.setattr(production.main, "workflow_wire", lambda review, **kw: {
        "project_id": review.project_id,
        "documents": kw.get("documents", []),
    })
    monkeypatch.setattr(production.main, "render_workflow_review", lambda review: "rendered")

    return types.SimpleNamespace(
        composed=composed,
        read=f"/production/review/{PROJECT}/review",
        workflow=f"/production/review/{PROJECT}/workflow",
        http=TestClient(production.main.app, headers=tokens.headers(OWNER)),
        bare=TestClient(production.main.app),
        other=TestClient(production.main.app, headers=tokens.headers(SOMEONE_ELSE)),
    )


class TestRouteScope:
    def test_the_read_contract_lists_the_projects_own_documents(self, surface):
        body = surface.http.get(surface.read).json()
        assert [d["document_id"] for d in body["documents"]] == [DOC_A, DOC_B]

    def test_each_document_states_whether_it_has_readings(self, surface):
        body = surface.http.get(surface.read).json()
        assert all(d["has_readings"] is True for d in body["documents"])
        assert {d["file_name"] for d in body["documents"]} == {"a.pdf", "b.pdf"}

    def test_a_document_of_this_project_is_passed_to_the_composition(self, surface):
        assert surface.http.get(f"{surface.read}?document_id={DOC_A}").status_code == 200
        assert surface.composed == [DOC_A]

    def test_no_document_is_passed_when_none_was_named(self, surface):
        assert surface.http.get(surface.read).status_code == 200
        assert surface.composed == [None]

    def test_a_document_of_another_project_is_refused(self, surface):
        response = surface.http.get(f"{surface.read}?document_id={DOC_ELSEWHERE}")
        assert response.status_code == 404
        assert "REVIEW_DOCUMENT_UNKNOWN" in response.text

    def test_a_refused_document_never_reaches_the_composition(self, surface):
        surface.http.get(f"{surface.read}?document_id={DOC_ELSEWHERE}")
        assert surface.composed == []

    def test_the_page_route_takes_the_same_scope(self, surface):
        assert surface.http.get(f"{surface.workflow}?document_id={DOC_B}").status_code == 200
        assert surface.composed == [DOC_B]

    def test_the_page_route_refuses_a_document_of_another_project(self, surface):
        assert surface.http.get(f"{surface.workflow}?document_id={DOC_ELSEWHERE}").status_code == 404

    def test_an_unauthenticated_read_is_still_refused(self, surface):
        assert surface.bare.get(surface.read).status_code == 401

    def test_a_non_owner_is_still_refused(self, surface):
        assert surface.other.get(surface.read).status_code == 403

    def test_no_credential_appears_in_the_response(self, surface):
        body = surface.http.get(surface.read).text
        for forbidden in ("service_role", "eyJ", "SUPABASE_SERVICE_ROLE_KEY"):
            assert forbidden not in body


# ======================================================================================
# The routes that carry the scope must all exist and still be declared once.
# ======================================================================================
class TestRouteSurface:
    def test_the_scope_is_a_query_parameter_on_all_three_routes(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        assert source.count('document_id: str | None = Query(default=None)') == 3

    def test_the_scope_helper_refuses_rather_than_guesses(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        start = source.index("def _review_document_scope(")
        body = source[start:source.index("\n@app.", start)]
        assert "REVIEW_DOCUMENT_UNKNOWN" in body
        # It is a membership test over the project's own documents, not an inference.
        assert "known = {document.get(\"document_id\")" in body
