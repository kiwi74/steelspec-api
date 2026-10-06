"""
J25 — the production connection-review surface, proved over genuine captures and real tokens.

WHAT THIS FILE IS

The proof that an authenticated project owner can inspect the real review of their own
project, and that nobody else can: one read-only route,

    GET /production/review/{project_id}/workflow

over the reconstruction J24A produces from the persisted J23 readings, through 7AK's
contract and 7AM's view, rendered by one page function that emits no form and no control.

The chain is not restated here. Every expected value is computed INDEPENDENTLY — J24A, 7AK
and 7AM called again in the test — and the composed value compared against it, so this file
cannot agree with the implementation by construction.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the route, the J19 identity and authorization boundary, the J24A producer,
              J23's capture selection, 7AZ's accumulation and validation, 7AJ's workflow
              construction, 7AK's two contract builders, 7AM's view model, J25's
              composition, and the renderer. The credentials are genuine ES256 tokens
              verified against a real in-process JWKS document (J19's own harness).
    DOUBLED   the database, and the section matcher — the two stages no test in this
              repository can perform, exactly as J24A, J19 and J22 double them.

The store is doubled twice over, on purpose:

  * `_Persisted` (J24A's) answers the producer's five reads AND the three it must never
    make, so "no reading was rebuilt from an engineering row" is asserted over a call that
    could have happened.
  * `_ReadOnlyStore` answers the review store's reads and raises on every write method the
    real client exposes, so "rendering writes nothing" is proved over a client that could
    have written.

TWO HONEST LIMITS, STATED RATHER THAN IMPLIED

  1. The recorded band is provable only with a snapshot this file builds. Nothing under
     `app/` records a review snapshot, so no test here exercises a persisted production row:
     the snapshot is built by J22's own builder and written by J22's own writer into J22's
     own store double — a genuine write through genuine code against a doubled database.
  2. J19's `test_no_second_status_source_exists` bans the token `review_items` in every file
     under `app/production_review/`. That rule is about the persisted `review_items` TABLE.
     This milestone's module lives OUTSIDE that package and legitimately reads 7AM's
     `ProjectReviewView.review_items` — the view model's own field, a projection of the
     reconstruction and not a table read at all. The rule's other five tokens are re-applied
     to the new module in section G; the one carve-out is asserted explicitly there rather
     than left as a silent weakening.
"""
from __future__ import annotations

import ast
import copy
import inspect
import re
import time
import types
from pathlib import Path

import pytest

import app.production_connection_review as workflow_review
from app.cad_engine.connection_review_snapshot import (
    build_review_snapshot,
    project_contract_from_snapshot,
)
from app.cad_engine.review_contract import build_project_review_contract
from app.cad_engine.review_view_model import render_project_view
from app.engineering_data import connection_review_repository as review_store

from tests import production_review_auth as auth
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j19_production_review_binding as j19
from tests import test_real_world_j20_connection_review_persistence_gap as j20
from tests import test_real_world_j22_connection_review_persistence as j22
from tests import test_real_world_j24a_revision_zero_producer as j24a

production = j4.production

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "production_connection_review.py"
RENDER_PATH = REPO / "app" / "review_ui" / "render.py"
MAIN_PATH = REPO / "app" / "main.py"
BINDING_PATH = REPO / "app" / "production_review" / "binding.py"

WORKFLOW_ROUTE = "/production/review/{project_id}/workflow"

SELBY_PROJECT = j24a.SELBY_PROJECT
ARKLES_PROJECT = j24a.ARKLES_PROJECT
SELBY_OWNER = "j25-selby-owner"
ARKLES_OWNER = "j25-arkles-owner"
OTHER_OWNER = "j25-some-other-reviewer"

#: Captured BEFORE any monkeypatch, so this file can persist its own recorded-band fixture
#: through the real writer while the request path's tripwire is armed.
REAL_PERSIST = review_store.persist_review_snapshot


# ===========================================================================
# The doubles.
# ===========================================================================
class _ProjectQuery:
    """`projects.select(...).eq("id", …).single().execute()` — the HTTP layer's own read."""

    def __init__(self, rows):
        self.rows = rows
        self.project_id = None

    def select(self, *columns):
        return self

    def eq(self, column, value):
        assert column == "id", column
        self.project_id = value
        return self

    def single(self):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self.rows.get(self.project_id))


class _ProjectsClient:
    """The HTTP layer's client: it answers the project read and refuses every other table.

    The route authorizes from the row it loads and reads nothing else, so a request that
    reached for members, connections or the review store on this client is an assertion
    failure rather than a silently answered query.
    """

    def __init__(self, rows):
        self.rows = dict(rows)
        self.tables_read: list[str] = []

    def table(self, name):
        self.tables_read.append(name)
        assert name == "projects", f"the HTTP layer read {name!r}"
        return _ProjectQuery(self.rows)


class _ReadQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.filters: list[tuple] = []

    def select(self, *columns):
        self.store.calls.append(("select", self.name, tuple(columns)))
        return self

    def eq(self, column, value):
        self.store.calls.append(("eq", self.name, column))
        self.filters.append((column, value))
        return self

    def execute(self):
        matched = [
            row for row in self.store.tables.get(self.name, [])
            if all(row.get(column) == value for column, value in self.filters)
        ]
        return types.SimpleNamespace(data=copy.deepcopy(matched))


class _ReadOnlyStore:
    """The review store as a GET may use it: reads only.

    Every write method the production client exposes raises with its own name, so a request
    that tried to persist anything fails here saying which call it made. The read sequence
    is recorded, which is how "only selects were issued" is checked.
    """

    WRITES = ("insert", "update", "upsert", "delete", "rpc")

    def __init__(self, tables=None):
        self.tables = dict(tables or {})
        self.calls: list[tuple] = []

    def table(self, name):
        self.calls.append(("table", name))
        return _ReadQuery(self, name)

    def __getattr__(self, name):
        if name in _ReadOnlyStore.WRITES:
            raise AssertionError(f"a GET called the write method {name!r}")
        raise AttributeError(name)


def _project_row(project_id, owner):
    return {"id": project_id, "user_id": owner, "status": "review"}


def _tripwires(monkeypatch):
    """Every writer a request must not reach, made to fail loudly if it does.

    `build_exception_resolution_package` is deliberately NOT tripwired:
    `start_project_workflow` calls it legitimately on every reconstruction, and a tripwire
    there would fail this milestone for doing the one thing it is supposed to do.
    """
    import app.cad_engine.project_workflow as project_workflow

    def refuse(name):
        def tripwire(*args, **kwargs):
            raise AssertionError(f"a GET reached the writer {name}")
        return tripwire

    monkeypatch.setattr(review_store, "persist_review_snapshot", refuse("persist_review_snapshot"))
    monkeypatch.setattr(review_store, "record_project_review", refuse("record_project_review"))
    monkeypatch.setattr(
        project_workflow, "resolve_project_connection", refuse("resolve_project_connection"),
    )
    monkeypatch.setattr(
        project_workflow, "refresh_project_workflow", refuse("refresh_project_workflow"),
    )


def _no_history(drawing_id):
    """The empty reading history — what a drawing that was never read returns.

    J23's own loader returns a list of rows, and a list is what it is answered with here.
    The point is only that a request over a project whose recorded items cite a drawing
    issues NO live read. WHICH history the composition reads — the whole one, every attempt
    kept, rather than the current attempt per page — is J81's own subject and is proved
    there, against the real loader.
    """
    return []


def _surface(production, monkeypatch, *, store, projects, owner, recorded=None, user_id=None):
    """The real application, the real route, and one authenticated client.

    The HTTP layer's own read is answered by a client that refuses every table but
    `projects`; the producer's five reads by the reconstruction double; the review store by
    the recording read-only double; the section matcher by J24A's honest stand-in. Nothing
    else about the request path is substituted.

    J81 added a fourth substitution and it is of the same kind: the recorded band's reading
    history now comes from J23's `page_extraction_captures_for_drawing`, so a request over a
    project whose recorded items cite a drawing would open a live read. `_NO_HISTORY`
    answers that read with the empty history. The recordings above already hold no review
    items, so nothing in this file's own expectations moves: the seam exists so the SURFACE
    stays offline, not so any band is answered differently.
    """
    client = _ProjectsClient(projects)
    monkeypatch.setattr(production.main, "supabase", client)
    for name in j24a.ALLOWED_READS:
        monkeypatch.setattr(production.repository, name, getattr(store, name))
    monkeypatch.setattr(
        workflow_review, "section_matcher_for_review", lambda: j24a._StubMatcher(),
    )
    store_client = recorded if recorded is not None else _ReadOnlyStore()
    monkeypatch.setattr(workflow_review, "review_store_client", lambda: store_client)
    monkeypatch.setattr(workflow_review, "capture_history_for_drawing", _no_history)
    _tripwires(monkeypatch)

    tokens = auth.install(monkeypatch, production)
    from fastapi.testclient import TestClient

    return types.SimpleNamespace(
        client=client,
        store=store,
        recorded=store_client,
        tokens=tokens,
        owner=owner,
        http=TestClient(production.main.app, headers=tokens.headers(user_id or owner)),
        bare=TestClient(production.main.app),
        other=TestClient(production.main.app, headers=tokens.headers(OTHER_OWNER)),
        url=lambda pid=SELBY_PROJECT: f"/production/review/{pid}/workflow",
    )


def _reconstruct(store, project_id):
    """J24A called again in the test — the independent expectation, never the page."""
    return j24a._reconstruct(store, project_id)


def _composition(surface, project_id=SELBY_PROJECT, *, recorded_state=None):
    """The composition driven by the same doubles the request used.

    The store read goes through the recording client, so a composition built here issues the
    same reads a request does.
    """
    if recorded_state is None:
        recorded_state = review_store.read_connection_review_state(
            project_id, client=surface.recorded,
        )
    return workflow_review.build_workflow_review(
        project_id,
        section_matcher=j24a._StubMatcher(),
        repository=surface.store,
        recorded_state=recorded_state,
    )


@pytest.fixture()
def selby(production, monkeypatch):
    return _surface(
        production, monkeypatch, store=j24a._selby_store(),
        projects={SELBY_PROJECT: _project_row(SELBY_PROJECT, SELBY_OWNER)}, owner=SELBY_OWNER,
    )


@pytest.fixture()
def arkles(production, monkeypatch):
    return _surface(
        production, monkeypatch, store=j24a._arkles_store(),
        projects={ARKLES_PROJECT: _project_row(ARKLES_PROJECT, ARKLES_OWNER)}, owner=ARKLES_OWNER,
    )


# ===========================================================================
# A. One authorized owner reads one real project, and the page is the real review.
# ===========================================================================
class TestTheOwnerSeesTheRealReview:
    def test_an_authenticated_owner_loads_the_genuine_project(self, selby):
        response = selby.http.get(selby.url())
        assert response.status_code == 200, response.text
        assert SELBY_PROJECT in response.text
        assert selby.client.tables_read == ["projects"], selby.client.tables_read

    def test_the_page_is_the_views_own_view_of_the_producers_own_reconstruction(self, selby):
        """The composition equals 7AM's view of 7AK's contract of J24A's workflow, computed
        here from the producer rather than compared against the page."""
        review = _composition(selby)
        expected = render_project_view(
            build_project_review_contract(_reconstruct(selby.store, SELBY_PROJECT).workflow)
        )
        assert review.view == expected
        assert review.refusal_code == ""
        assert review.reconstructed.workflow.revision == 0
        assert review.view.revision == 0

    def test_the_identity_band_states_the_document_and_the_revision_read_off_the_view(self, selby):
        review = _composition(selby)
        rows = dict(review.identity)
        assert rows["Project"] == SELBY_PROJECT
        assert rows["Drawing"] == j24a.SELBY_DRAWING
        assert rows["Drawing set"] == j24a.SELBY_SET
        assert rows["Document pages"] == str(j24a.SELBY_PAGE_COUNT)
        assert rows["Reconstruction revision"] == str(review.view.revision) == "0"
        text = selby.http.get(selby.url()).text
        assert f"Revision {review.view.revision}" in text

    def test_the_reconstruction_is_request_scoped_and_rerun_per_request(self, selby):
        """Two requests reconstruct twice: nothing about a workflow survives between them,
        which is the whole reason this surface composes inside the request."""
        before = len(selby.store.calls)
        assert selby.http.get(selby.url()).status_code == 200
        first = len(selby.store.calls) - before
        assert selby.http.get(selby.url()).status_code == 200
        second = len(selby.store.calls) - before - first
        assert first > 0
        assert second == first, (first, second)

    def test_every_read_names_the_project_the_request_asked_for(self, selby):
        assert selby.http.get(selby.url()).status_code == 200
        for call in selby.store.calls:
            if call[0] in ("get_project", "drawing_sets_for_project", "member_rows_for_project"):
                assert call[1] == SELBY_PROJECT, call


# ===========================================================================
# B. Nobody else can — J19's rule, unmodified.
# ===========================================================================
class TestTheBoundary:
    def test_no_credential_is_refused_and_nothing_is_read(self, selby):
        response = selby.bare.get(selby.url())
        assert response.status_code == 401, response.text
        assert response.json()["detail"]["refusal"] == "IDENTITY_NO_TOKEN"
        assert selby.client.tables_read == [], selby.client.tables_read
        assert selby.store.calls == [], selby.store.calls

    @pytest.mark.parametrize("token", ["not-a-token", "a.b.c", ""])
    def test_a_malformed_token_is_refused(self, selby, token):
        response = selby.bare.get(selby.url(), headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, response.text
        assert selby.client.tables_read == []

    def test_a_token_signed_by_an_unpublished_key_is_refused(self, selby):
        response = selby.bare.get(
            selby.url(), headers={"Authorization": f"Bearer {selby.tokens.foreign_token()}"},
        )
        assert response.status_code == 401, response.text
        assert selby.client.tables_read == []

    def test_an_expired_token_is_refused(self, selby):
        headers = selby.tokens.headers(SELBY_OWNER, exp=int(time.time()) - 60)
        response = selby.bare.get(selby.url(), headers=headers)
        assert response.status_code == 401, response.text
        assert selby.client.tables_read == []

    def test_a_token_for_another_issuer_is_refused(self, selby):
        headers = selby.tokens.headers(SELBY_OWNER, iss="https://elsewhere.invalid")
        response = selby.bare.get(selby.url(), headers=headers)
        assert response.status_code == 401, response.text
        assert selby.client.tables_read == []

    def test_an_authenticated_non_owner_is_forbidden(self, selby):
        response = selby.other.get(selby.url())
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"
        assert selby.store.calls == [], selby.store.calls

    def test_an_unknown_project_is_not_found(self, selby):
        response = selby.http.get("/production/review/no-such-project/workflow")
        assert response.status_code == 404, response.text
        assert selby.store.calls == []

    @pytest.mark.parametrize("query", [
        "?user_id=" + SELBY_OWNER, "?owner=" + SELBY_OWNER, "?role=service_role",
        "?sub=" + SELBY_OWNER, "?project_id=" + SELBY_PROJECT, "?authorized=true",
    ])
    def test_no_client_supplied_field_can_grant_access(self, selby, query):
        response = selby.other.get(selby.url() + query)
        assert response.status_code == 403, (query, response.text)

    def test_one_projects_credential_cannot_read_another_project(self, production, monkeypatch):
        """Two projects, two owners, one client serving both by id. The refusal is the
        rule's, and the reads that DID happen are the requested project's own."""
        selby_store = j24a._selby_store()
        arkles_store = j24a._arkles_store()

        def get_project(project_id):
            if project_id == SELBY_PROJECT:
                return selby_store.get_project(project_id)
            if project_id == ARKLES_PROJECT:
                return arkles_store.get_project(project_id)
            return None

        surface = _surface(
            production, monkeypatch, store=selby_store,
            projects={
                SELBY_PROJECT: _project_row(SELBY_PROJECT, SELBY_OWNER),
                ARKLES_PROJECT: _project_row(ARKLES_PROJECT, ARKLES_OWNER),
            },
            owner=SELBY_OWNER,
        )
        monkeypatch.setattr(production.repository, "get_project", get_project)
        mine = surface.http.get(surface.url(SELBY_PROJECT))
        theirs = surface.http.get(surface.url(ARKLES_PROJECT))
        assert mine.status_code == 200, mine.text
        assert theirs.status_code == 403, theirs.text
        assert SELBY_PROJECT in mine.text and SELBY_PROJECT not in theirs.text

    def test_a_project_the_token_owner_does_not_own_is_never_reconstructed(self, selby):
        """A refusal reads nothing: the producer's store saw no call at all."""
        assert selby.other.get(selby.url()).status_code == 403
        assert selby.store.calls == []


# ===========================================================================
# C. The evidence is the persisted reading, and the coverage is the document's own.
# ===========================================================================
class TestTheEvidence:
    def test_the_runs_the_readings_came_from_reach_the_page(self, selby):
        review = _composition(selby)
        assert review.capture_runs, "the genuine capture was recorded in runs"
        text = selby.http.get(selby.url()).text
        for run in review.capture_runs:
            assert run in text, run
        assert "Extraction runs:" in text

    def test_the_coverage_accounts_for_every_page_of_the_selby_document(self, selby):
        review = _composition(selby)
        rows = dict(review.coverage)
        assert rows["Pages in document"] == str(j24a.SELBY_PAGE_COUNT)
        assert rows["Analysed"] == ", ".join(
            str(page) for page in range(1, j24a.SELBY_PAGE_COUNT + 1)
        )
        assert rows["Parse failed"] == "(none)"
        assert rows["Not analysed"] == "(none)"
        assert rows["Readings read"] == str(j24a.SELBY_PAGE_COUNT)
        assert review.reconstructed.captures_read == j24a.SELBY_PAGE_COUNT
        text = selby.http.get(selby.url()).text
        assert "Parse failed" in text and "Not analysed" in text

    def test_the_arkles_documents_unreadable_pages_are_named_not_hidden(self, arkles):
        """The genuine Arkles capture has six pages whose response could not be parsed.
        They are accounted for by number, and none is counted as analysed."""
        review = _composition(arkles, ARKLES_PROJECT)
        failed = j24a._arkles_parse_failed_pages()
        assert failed == (2, 11, 12, 13, 22, 29)
        assert review.reconstructed.parse_failed_pages == failed
        assert dict(review.coverage)["Parse failed"] == ", ".join(str(page) for page in failed)
        assert not set(failed) & set(review.reconstructed.analysed_pages)
        text = arkles.http.get(arkles.url(ARKLES_PROJECT)).text
        assert ", ".join(str(page) for page in failed) in text

    def test_the_reconstruction_reads_no_engineering_row(self, selby):
        """The reads a reconstruction must never make exist on the double and record
        themselves, so this is asserted over a call that could have happened."""
        _composition(selby)
        made = {call[0] for call in selby.store.calls}
        assert not (made & set(j24a.FORBIDDEN_READS)), sorted(made & set(j24a.FORBIDDEN_READS))
        assert set(j24a.ALLOWED_READS) <= made

    def test_the_repeated_selby_marks_keep_one_persisted_identity_each(self, selby):
        """The capture genuinely names some marks in more than one window — asserted here so
        the claim below is not vacuous — and the reconstruction gives each ONE identity, the
        persisted member record's own mark. This route adds no second identity."""
        cross = {
            mark: sorted(windows)
            for mark, windows in j24a._windows_naming_each_mark().items()
            if len(windows) > 1
        }
        assert cross, "the genuine capture repeats marks across windows"
        marks = _composition(selby).reconstructed.workflow.intake.collection.known_member_marks
        assert len(marks) == len(set(marks)), "a colliding mark was given a second identity"
        assert marks == tuple(j24a._member_rows_for(j24a._selby_pages()))
        assert set(cross) <= set(marks)

    def test_the_members_a_connection_names_are_the_persisted_marks(self, selby):
        """Every mark a connection references is one of the persisted member identities, so
        no member was invented to explain a connection. Read off 7AK's own contract — the
        reference list the reconstruction produced, not the view's prose."""
        workflow = _composition(selby).reconstructed.workflow
        marks = set(workflow.intake.collection.known_member_marks)
        contract = build_project_review_contract(workflow)
        referenced = {
            str(mark) for item in contract.items for mark in item.ai_member_references
        }
        assert referenced, "the genuine capture names members on its connections"
        assert referenced <= marks, sorted(referenced - marks)

    def test_no_member_placement_is_invented_and_the_page_says_so(self, selby):
        review = _composition(selby)
        assert review.reconstructed.workflow.revision == 0
        text = selby.http.get(selby.url()).text
        assert "member_placements" not in text
        assert "never inferred from a connection or another member" in text


# ===========================================================================
# D. The surface is read-only, and it says what it cannot do.
# ===========================================================================
class TestReadOnly:
    def test_the_page_emits_no_form_and_no_control(self, selby):
        text = selby.http.get(selby.url()).text
        assert re.findall(r"<form", text) == []
        assert re.findall(r"<button", text) == []
        assert re.findall(r"<input", text) == []
        assert "<script" not in text

    def test_the_store_is_read_and_never_written(self, selby):
        assert selby.http.get(selby.url()).status_code == 200
        issued = [call[0] for call in selby.recorded.calls]
        assert issued == ["table", "select", "eq"], issued
        assert selby.recorded.calls[0][1] == review_store.SNAPSHOT_TABLE
        assert selby.recorded.calls[1][2] == ("review_revision",)

    def test_the_write_methods_are_genuinely_unreachable_from_this_store(self, selby):
        """Stated so the read-only proof is not a proof about an unarmed harness: the
        recording store raises on every write the production client exposes."""
        for name in _ReadOnlyStore.WRITES:
            with pytest.raises(AssertionError, match=name):
                getattr(selby.recorded, name)

    def test_no_route_accepts_a_decision(self, production):
        paths = [path for _, path in j20._declared_routes(production.main.app)]
        assert WORKFLOW_ROUTE in paths
        review_paths = [path for path in paths if path.startswith("/production/review")]
        assert review_paths == [
            "/production/review/{project_id}",
            # J29: the annotation evidence viewer. A GET that renders the occurrences J28
            # recorded; it accepts no decision and no parameter beyond which page to show.
            "/production/review/{project_id}/annotations",
            # J47: the one route that DOES accept a decision, named here so the list stays
            # exact rather than filtered into silence. Every other path in this list is
            # still a GET or a retry, and this test's own claim is now about them: the
            # read surface this milestone renders remains a surface that cannot resolve.
            j20.J47_RESOLUTION_ROUTE,
            # J64: the document-role assertion. It accepts no decision about a connection,
            # a reading or a member — it records the role a human asserts for one of the
            # project's own source documents, which is a fact about a document rather than
            # about what a reading of one found. Named here so the list stays exact: the
            # read surface this milestone renders still cannot resolve anything.
            "/production/review/{project_id}/document-role",
            # L19: the lineage a document is reviewed from. It records which reading of a
            # source document is the one to reconstruct from and accepts no decision about a
            # connection — named here so the list stays exact rather than filtered into
            # silence: the read surface this milestone renders still cannot resolve.
            "/production/review/{project_id}/documents/{document_id}/selected-drawing",
            # J50: the baseline-opening operation. It accepts NO decision — an opening
            # request carries no fields at all — and records only the project's own
            # reconstructed revision-0 baseline. Named here so the list stays exact: the
            # read surface this milestone renders still cannot resolve anything.
            "/production/review/{project_id}/open",
            "/production/review/{project_id}/pages/{page_number}/retry",
            # E2E-001A: the review READ contract. A GET that returns the same composition
            # the workflow route renders, as data instead of as a page, because a browser
            # form POST cannot carry the Authorization header every production route
            # authenticates from. It accepts no decision and no body at all — it reads and
            # renders — so the claim this milestone makes still holds over it.
            "/production/review/{project_id}/review",
            WORKFLOW_ROUTE,
        ], review_paths

    def test_the_route_is_the_only_one_this_milestone_added(self, production):
        assert j20._declared_routes(production.main.app) == j20.FROZEN_ROUTES

    def test_the_workflow_route_declares_get_only(self, production):
        methods = [
            tuple(sorted(route.methods))
            for route in production.main.app.routes
            if getattr(route, "path", None) == WORKFLOW_ROUTE
        ]
        assert methods == [("GET",)], methods

    def test_the_route_requires_a_credential(self, selby):
        assert selby.bare.get(selby.url()).status_code == 401


# ===========================================================================
# E. Every action the view carries is stated with what can truthfully be done with it.
# ===========================================================================
class TestTheActions:
    @staticmethod
    def _labels_in_views(review):
        views = [view for view in (review.view, review.persisted_view) if view is not None]
        labels = {action.label for view in views for action in view.actions}
        labels |= {
            action.label
            for view in views
            for item in tuple(view.review_items) + tuple(view.completed_items)
            for action in item.actions
        }
        return labels

    def test_the_stated_actions_are_exactly_the_views_own(self, selby):
        review = _composition(selby)
        assert review.limitations, "the views carry actions"
        assert {label for label, _, _ in review.limitations} == self._labels_in_views(review)

    def test_review_is_available_because_reviewing_is_reading(self, selby):
        states = {label: state for label, state, _ in _composition(selby).limitations}
        assert states["Review"] == workflow_review.ACTION_AVAILABLE

    def test_resolve_is_unavailable_with_the_pipelines_own_reason(self, selby):
        reasons = {
            label: (state, reason) for label, state, reason in _composition(selby).limitations
        }
        assert "Resolve" in reasons, sorted(reasons)
        state, reason = reasons["Resolve"]
        assert state == workflow_review.ACTION_UNAVAILABLE
        assert workflow_review.PIPELINE_GAP_ERROR_CODE in reason
        assert workflow_review.PIPELINE_STAGE_MEMBER_CONTEXT in reason
        assert "never inferred from a connection or another member" in reason
        assert "Wherever" in reason, "the pipeline refuses per mark, so the reason stays conditional"
        assert "adds no route that applies a resolution" in reason

    def test_refresh_is_unavailable_because_this_surface_reads_no_files(self, selby):
        reasons = {
            label: (state, reason) for label, state, reason in _composition(selby).limitations
        }
        assert "Refresh" in reasons, sorted(reasons)
        state, reason = reasons["Refresh"]
        assert state == workflow_review.ACTION_UNAVAILABLE
        assert "reads no files" in reason

    def test_the_route_is_not_gated_on_a_member_placement_it_cannot_supply(self, selby):
        """The unavailable reason quotes the pipeline's own ruling rather than a claim
        invented here, and the pipeline's constant is the one this module imports."""
        from app.cad_engine.automation_pipeline import (
            PIPELINE_GAP_ERROR_CODE,
            PIPELINE_STAGE_MEMBER_CONTEXT,
        )
        assert PIPELINE_GAP_ERROR_CODE == workflow_review.PIPELINE_GAP_ERROR_CODE
        assert PIPELINE_STAGE_MEMBER_CONTEXT == workflow_review.PIPELINE_STAGE_MEMBER_CONTEXT

    def test_the_page_states_every_action_with_its_state_and_its_reason(self, selby):
        review = _composition(selby)
        text = selby.http.get(selby.url()).text
        for label, state, reason in review.limitations:
            assert label in text, label
            assert state in text, state
            assert reason[:60] in text, reason


# ===========================================================================
# F. Recorded state is represented; its absence is stated and never filled in.
# ===========================================================================
def _recorded(surface, project_id):
    """A genuine revision-0 snapshot of the genuine workflow, written by J22's own writer
    into J22's own store double and read back by J22's own reader.

    `REAL_PERSIST` is the function captured before the request path's tripwire was armed:
    persisting this fixture is the TEST's act, and the tripwire exists to catch the
    PRODUCTION path writing. Nothing here is hand-built — the snapshot is
    `build_review_snapshot`'s and the rows are the writer's.
    """
    workflow = _reconstruct(surface.store, project_id).workflow
    fake = j22._FakeStore(projects=(project_id,))
    snapshot = build_review_snapshot(
        workflow, project_id=project_id, evidence_rows=j22.NO_EVIDENCE, previous_revision=None,
    )
    assert REAL_PERSIST(snapshot, client=fake) is snapshot
    return review_store.read_connection_review_state(project_id, client=fake)


class TestTheRecordedBand:
    def test_a_recorded_revision_is_rendered_through_the_shared_chain(self, selby):
        """The recorded band is 7AK's OWN replay of the snapshot, so the two bands cannot
        drift. The expected value is computed here, independently of the page."""
        state = _recorded(selby, SELBY_PROJECT)
        assert state.code == review_store.REVIEW_STATE_RECORDED
        assert state.revisions == (0,)
        expected = render_project_view(project_contract_from_snapshot(state.snapshot))
        review = _composition(selby, recorded_state=state)
        assert review.persisted_view == expected
        assert review.persisted_revisions == (0,)
        # The live band is unchanged by the recorded band's presence.
        assert review.view == render_project_view(
            build_project_review_contract(_reconstruct(selby.store, SELBY_PROJECT).workflow)
        )

    def test_the_recorded_state_reaches_the_page_verbatim(self, selby, monkeypatch):
        state = _recorded(selby, SELBY_PROJECT)
        monkeypatch.setattr(workflow_review, "recorded_review_state", lambda project_id: state)
        text = selby.http.get(selby.url()).text
        assert review_store.REVIEW_STATE_RECORDED in text
        assert f"Recorded revision {state.snapshot.review_revision}" in text
        assert "Recorded revisions: 0" in text

    def test_a_project_with_no_recorded_state_says_exactly_that(self, selby):
        review = _composition(selby)
        assert review.persisted_code == review_store.NO_PERSISTED_CONNECTION_REVIEW_STATE
        assert review.persisted_view is None
        assert review.persisted_revisions == ()
        text = selby.http.get(selby.url()).text
        assert review_store.NO_PERSISTED_CONNECTION_REVIEW_STATE in text
        assert "Recorded revisions: (none)" in text
        assert "nothing was created, recorded or advanced" in text

    def test_absence_does_not_stop_the_live_band_from_rendering(self, selby):
        """The two bands are independent: nothing recorded is not nothing reconstructed."""
        text = selby.http.get(selby.url()).text
        assert review_store.NO_PERSISTED_CONNECTION_REVIEW_STATE in text
        assert SELBY_PROJECT in text
        assert "Connection review" in text

    def test_reading_a_project_with_no_recorded_state_writes_none(self, selby):
        """The absence is reported, not repaired: with the write tripwires armed, a project
        that has recorded nothing still answers 200 and the store saw only selects."""
        response = selby.http.get(selby.url())
        assert response.status_code == 200
        assert review_store.NO_PERSISTED_CONNECTION_REVIEW_STATE in response.text
        assert [call[0] for call in selby.recorded.calls] == ["table", "select", "eq"]

    def test_the_recorded_revision_is_the_stores_own_and_need_not_be_zero(self, selby):
        """The reconstruction is revision 0; the store's current revision is the STORE's
        (its own highest-recorded rule). The page reports both and uses neither to explain
        the other."""
        state = _recorded(selby, SELBY_PROJECT)
        review = _composition(
            selby,
            recorded_state=review_store.RecordedReviewState(
                project_id=SELBY_PROJECT, code=state.code, snapshot=state.snapshot,
                revisions=(0, 3),
            ),
        )
        assert review.view.revision == 0
        assert review.persisted_view.revision == state.snapshot.review_revision
        assert review.persisted_revisions == (0, 3)


# ===========================================================================
# G. The module's own source claims.
# ===========================================================================
class TestTheSource:
    def _code(self):
        return j19._code_only(MODULE_PATH)

    def test_the_composing_module_writes_nothing(self):
        code = self._code()
        for forbidden in ("insert(", "update(", "upsert(", "delete(", "rpc("):
            assert forbidden not in code, forbidden

    def test_the_composing_module_invents_no_identity_column(self):
        code = self._code()
        for invented in ("organization_id", "role_id", "membership", "project_members",
                         "project_owners", "profiles", "invitation", "rbac"):
            assert invented not in code, invented

    def test_the_composing_module_owns_no_second_status_source(self):
        """J19's vocabulary rule, minus the one token this milestone legitimately reads."""
        code = self._code()
        for banned in ("derive_project_status", "review_status", "steel_members",
                       "COMPLETE_REVIEW_STATUSES", "plan_tier"):
            assert banned not in code, banned

    def test_the_one_carve_out_is_the_view_models_field_and_not_the_table(self):
        """`review_items` IS present, and this asserts exactly why: it is read off 7AM's
        `ProjectReviewView`, the projection of the reconstruction — never the persisted
        `review_items` table, whose own header names the store module and which this module
        never queries."""
        assert "review_items" in MODULE_PATH.read_text()
        code = self._code()
        assert "connection_review_items" not in code
        assert "ITEM_TABLE" not in code
        # The rule J19 applies to its own package is untouched: J25 changed no file there.
        assert "review_items" not in j19._code_only(BINDING_PATH)

    def test_the_composing_module_holds_no_process_global_state(self):
        tree = ast.parse(MODULE_PATH.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            else:
                continue
            for name in names:
                assert name.isupper() or name.startswith("__"), name

    def test_the_composing_module_binds_no_workflow_across_requests(self):
        text = MODULE_PATH.read_text()
        assert "bind_workflow" not in text
        assert "_current_workflow" not in text

    def test_the_composing_module_calls_the_producer_and_never_starts_a_workflow(self):
        tree = ast.parse(MODULE_PATH.read_text())
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        called |= {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "reconstruct_project_workflow" in called
        for never in ("start_project_workflow", "resolve_project_connection",
                      "refresh_project_workflow", "persist_review_snapshot",
                      "record_project_review"):
            assert never not in called, never

    def test_the_composing_module_opens_no_client_at_import(self):
        """The store, the matcher and the renderer's own machinery are imported on use, so
        importing this module opens nothing and the route module that imports it stays
        importable with no environment configured."""
        tree = ast.parse(MODULE_PATH.read_text())
        module_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                module_imports.add(node.module)
            elif isinstance(node, ast.Import):
                module_imports.update(alias.name for alias in node.names)
        assert "app.supabase_client" not in module_imports
        assert "app.engineering_data.section_matcher" not in module_imports
        assert not [name for name in module_imports if name.startswith("fastapi")]

    def test_the_renderer_grew_exactly_one_public_name(self):
        tree = ast.parse(RENDER_PATH.read_text())
        exported = ()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            ):
                exported = tuple(element.value for element in node.value.elts)
        assert "render_workflow_review_page" in exported
        # J29 added the tenth: the annotation evidence viewer's page. It is an addition
        # beside this one, not a change to it — the renderer still states nothing and
        # still imports nothing new, and the pin stays exact so an eleventh name has to
        # be declared here rather than appear quietly.
        assert "render_annotation_evidence_page" in exported
        assert len(exported) == 10, exported

    def test_the_renderer_names_no_engineering_vocabulary(self):
        """The renderer escapes and prints what it is handed; the engineering statements
        live in the composition module. Scanned over the whole file, comments included."""
        text = RENDER_PATH.read_text().lower()
        for word in ("fabrication", "<script", "dxf", "dwg", "ifc", "jwt", "supabase",
                     "sqlalchemy", "review_status", "member_placements"):
            assert word not in text, word

    def test_the_route_module_names_no_layer_below_the_route(self):
        """The HTTP layer composes nothing: it names no contract builder, no renderer, no
        view model and no workflow-state type — it calls the composition and maps what the
        composition refuses to a status."""
        code = j19._code_only(MAIN_PATH)
        for forbidden in ("build_project_review_contract", "render_project_view",
                          "app.review_ui.render", "projectworkflowstate", "review_view_model"):
            assert forbidden not in code, forbidden

    def test_the_only_cad_engine_names_in_the_route_module_are_refusal_types(self):
        """J47's route has to answer 7AJ's own refusals, and a refusal is answered where
        the status is decided, so it imports the refusal TYPES. That is the whole of what
        it may take from below: the names end in `Error`, no module is imported, no module
        attribute is read, and a SECOND cad_engine import — of a builder, a state, or
        anything else — is still refused here."""
        tree = ast.parse(MAIN_PATH.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app.cad_engine"):
                    imported.add((node.module, tuple(alias.name for alias in node.names)))
            elif isinstance(node, ast.Import):
                assert not [a.name for a in node.names if a.name.startswith("app.cad_engine")]
        assert imported == {
            ("app.cad_engine.project_workflow", (
                "ConnectionAlreadyProcessedError",
                "CrossProjectResolutionError",
                "StaleProjectWorkflowError",
                "UnknownProjectPackageError",
                "WorkflowStageFailureError",
            )),
        }, imported

    def test_the_route_authorizes_before_it_reads(self, production):
        """The refusal precedes every store read because the authorization CALL precedes the
        composition call in the route body."""
        source = inspect.getsource(production.main.production_workflow_review)
        assert source.index("_authorized_project") < source.index("build_workflow_review")
        # E2E-002G — the route takes an optional document scope and passes it through. The
        # property is unchanged: the composition is given the project and the scope the
        # caller stated, and nothing else the caller could have supplied. The call is named
        # exactly, so a further argument still has to be declared here.
        assert "build_workflow_review(project_id, document_id=document_id)" in source


# ===========================================================================
# H. The response itself.
# ===========================================================================
class TestTheResponse:
    def test_the_page_carries_no_credential(self, selby):
        token = selby.tokens.token(SELBY_OWNER)
        text = selby.http.get(selby.url()).text
        for secret in (token, "Bearer", "eyJ", "service_role", "jwt_secret", "apikey",
                       "Authorization"):
            assert secret not in text, secret

    def test_two_requests_render_byte_identically(self, selby):
        first = selby.http.get(selby.url())
        second = selby.http.get(selby.url())
        assert first.status_code == second.status_code == 200
        assert first.text == second.text
        assert first.text.strip(), "the page is not empty"

    def test_rendering_the_same_composition_twice_is_byte_identical(self, selby):
        review = _composition(selby)
        assert workflow_review.render_workflow_review(review) == (
            workflow_review.render_workflow_review(review)
        )


# ===========================================================================
# I. The stated limit of this file, and the capture it declines to substitute for.
# ===========================================================================
needs_real_capture = pytest.mark.skipif(
    not j24a.SELBY_WINDOW_FILES,
    reason="the genuine Selby capture is not present; the proof is skipped rather than run "
           "over a fabricated review state",
)


class TestWhatThisFileDoesNotClaim:
    def test_no_project_driven_here_is_a_live_production_row(self):
        """Every project id this file drives is a double's, and every recorded band is a
        snapshot this file built. Nothing under `app/` records a review snapshot, so a test
        claiming to exercise a persisted production row would be false."""
        assert SELBY_PROJECT.startswith("PROJ-7J24A-")
        assert ARKLES_PROJECT.startswith("PROJ-7J24A-")
        tree = ast.parse(MODULE_PATH.read_text())
        called = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "record_project_review" not in called
        assert "record_project_review" in MODULE_PATH.read_text(), (
            "the module names the writer it never calls, in the paragraph saying so"
        )

    @needs_real_capture
    def test_the_offline_proof_runs_only_over_a_present_capture(self):
        assert j24a.SELBY_WINDOW_FILES
        assert j24a.ARKLES_FILE
