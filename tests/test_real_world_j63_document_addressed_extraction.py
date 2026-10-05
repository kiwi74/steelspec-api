"""
J63 — DOCUMENT-ADDRESSED EXTRACTION (S1): WHICH source document a run reads.

WHAT THIS FILE IS

The proof of the one capability J63 added to the extraction boundary: a caller can name
one of a project's `project_documents` rows, and that document's own stored file is what
the run reads —

    PROJECT
      ├── PROJECT_DOCUMENT A → DRAWING / ANALYSIS_RUN / CAPTURE
      └── PROJECT_DOCUMENT B → DRAWING / ANALYSIS_RUN / CAPTURE

— without confusing a second document with a second extraction of the same document, with a
retry of one document, or with a continuation of one document's reading.

J63A, appended below, closes the one case J63 left open: `/extract` is the route that
STARTS a reading, and until J63A an omitted `document_id` on a project holding more than one
document started a new reading of `projects.uploaded_file_path` — a choice made on the
caller's behalf. Sections S and T (and mutations G and H) are the proof of the rule that
replaced it: no document or one document is the pre-J63 request, two or more documents and
no address is refused with J16's own code, and an addressed request is untouched.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the request contract and every one of its refusals, the source-resolution
              boundary (`app.production_extraction_source.py`), the J16/J17 lineage
              guards as the pipeline now applies them, the three HTTP routes and their
              status mapping, the J19 identity and authorization boundary (genuine ES256
              tokens against an in-process JWKS document), and — in sections G to I —
              the genuine pipeline over a REAL PDF: `parse_pdf_and_save` twice against
              one project, with the production hashing rule and the J61 content identity
              deciding that two files are two documents and one file is one.

    DOUBLED   the database: where `project_documents`, `drawing_sets`, `drawings`,
              `analysis_runs` and the evidence rows go, and what a read serves back.
              Every double RECORDS what it was asked, so "this step did not run" is
              asserted over a call that could have happened.

WHAT THE MUTATIONS ARE

They are not extra tests. Each takes one property asserted above, removes or inverts the
thing that protects it, and shows the assertion goes RED — including the milestone's own
headline: with the address dropped, a project that holds two documents is refused for a
request that names one of them.

WHAT THIS FILE DOES NOT CLAIM

It assigns no document role, infers none, parses no transmittal, reconciles no documents,
resolves no conflict, cites no field, computes no per-document coverage, and adds no column
to any table. It writes no live row: no live project is extracted, no live review is
opened, no live revision is recorded, no artifact is generated, and no Anthropic API is
called. The two genuine runs below happen over a PDF this file writes in a temporary
directory and a repository this file doubles.
"""
from __future__ import annotations

import ast
import copy
import inspect
import pathlib
import re
import types

import pytest

import app.production_extraction_source as source
from app.engineering_data import page_extraction_capture as captures
from app.engineering_data.repository import DOCUMENT_COLUMNS
from app.validation.page_coverage import coverage_of
from app.validation.parse_failures import failures_of
from app.validation.page_windows import (
    CONTINUATION_DRAWING_SET_UNRESOLVED,
    CONTINUATION_NO_COVERAGE_RECORD,
    CONTINUATION_RECORD_CONFLICT,
    CONTINUATION_SOURCE_MISMATCH,
    ContinuationRefused,
)
from app.validation.parse_failures import RetryRefused

from tests import production_review_auth as auth
from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j16_pdf_continuation as j16
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j50_production_review_opening as j50

#: J4's own module-scoped fixture, aliased so this file's tests can take it as a parameter
#: exactly as J16/J17/J50's do. It is a fixture and NOT a module: nothing at this file's
#: import time may touch `production.main`.
production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE_PATH = REPO / "app" / "production_extraction_source.py"
PIPELINE_PATH = REPO / "app" / "pipeline.py"
MAIN_PATH = REPO / "app" / "main.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"

EXACT_TOKEN = j13.EXACT_TOKEN

#: The document ids these tests address. They are ordinary opaque ids: nothing in the
#: boundary reads a document id's shape, and section D proves addressing a value that is
#: not a document at all is refused for what it is rather than for looking unlike one.
DOC_A = "aaaaaaaa-0000-4000-8000-00000000000a"
DOC_B = "bbbbbbbb-0000-4000-8000-00000000000b"
DOC_FOREIGN = "ffffffff-0000-4000-8000-00000000000f"
DOC_ABSENT = "00000000-0000-4000-8000-000000000000"

PATH_A = "j63-owner/drawings-a.pdf"
PATH_B = "j63-owner/drawings-b.pdf"
PATH_UNREAD = "j63-owner/drawings-never-read.pdf"

PROJECT_ONE = "11111111-2222-4333-8444-555555555555"
PROJECT_TWO = "22222222-3333-4444-8555-666666666666"
OWNER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_OWNER = "99999999-8888-4777-8666-555555555555"

#: The page count both source documents of the genuine project have. Equal counts are
#: what makes the two lineages' coverage arithmetic comparable; the documents differ in
#: their BYTES, which is the only thing that makes two documents two.
DOCUMENT_PAGES = j16.DOCUMENT_PAGES

#: The columns the production read names, split the way the production constant states
#: them, so the double serves exactly the columns production selects and no others.
DOCUMENT_COLUMN_NAMES = tuple(DOCUMENT_COLUMNS.split(","))

#: The JSON shapes of the extract route's service-owned payload (`app/config.py`), named
#: here so a preview cannot be mistaken for a document row and vice versa.
_HASH_A = "a" * 64


# ===========================================================================
# The harness: one store, two seams, and the real routes.
# ===========================================================================
def _fixture_function(fixture):
    """The plain function behind a pytest fixture (the J17 idiom)."""
    return fixture._get_wrapped_function()


@pytest.fixture(scope="module", autouse=True)
def _bind_j16_page_factory(production):
    """Binds the real production `PageExtraction` into J16's page helper.

    J16 binds it from a module-scoped autouse fixture of its own, which reaches only
    its own module. This file drives J16's harness from outside J16, so it has to bind
    the same global itself — which is what J17 does for the same reason.
    """
    previous = j16._PAGE
    j16._PAGE = production.PageExtraction
    yield
    j16._PAGE = previous


class _Documents(j16._ServingRepository):
    """J16's serving double, plus the ONE read J63 added.

    Everything else is J16's: the writes are recorded and SERVED back, so what a test
    reads is what production code wrote. The added read is
    `project_documents_for_project`, which J61 published and nothing called until this
    milestone, and which the omitted request deliberately does NOT call — section A
    asserts that over this double's own read log.
    """

    def seed_document(self, project_id, *, document_id, storage_path, file_name=None,
                      content_sha256=None, page_count=None, role="UNKNOWN"):
        """A `project_documents` row as the UPLOAD (or the J61 backfill) left it.

        Seeding a document is not seeding evidence: a document row is what a caller
        ADDRESSES, not what a run derives, and the state being addressed has to exist
        before the request that addresses it. A document a test's own run creates is
        created by the production write path, never here.
        """
        row = {
            "id": document_id,
            "project_id": project_id,
            "storage_path": storage_path,
            "file_name": file_name or storage_path.rsplit("/", 1)[-1],
            "source_format": "PDF",
            "byte_size": None,
            "page_count": page_count,
            "content_sha256": content_sha256,
            "role": role,
            "revision_label": None,
            "supersedes_document_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        self.documents.append(row)
        return row

    def project_documents_for_project(self, project_id):
        """Every document of this project, in the columns the production select names.

        The real function orders by `created_at`, which is creation order. This one
        preserves the order the rows were created in — the same ordering of RECORDED
        FACT — and nothing in the boundary reads it as a preference: section E proves
        that by making one document look likelier than the other in every way it can.
        """
        self.reads.append("project_documents_for_project")
        return [
            {name: copy.deepcopy(row.get(name)) for name in DOCUMENT_COLUMN_NAMES}
            for row in self.documents if row.get("project_id") == project_id
        ]

    def evidence_rows_for_page(self, drawing_id, page_number):
        """J17's read, carried here so a retry can be planned against this double.

        J17 subclasses J16's repository to add exactly this one function. This file
        subclasses the same base and adds the same function, worded the same way: it is
        answered from the rows the production code wrote through this object — never
        from rows a test typed in — and filters by the exact identity (source drawing +
        source page) the production read applies.
        """
        self.reads.append("evidence_rows_for_page")
        rows = []
        for table, stored in (("steel_members", self.members),
                              ("connections", self.connections)):
            for row in stored:
                if (row.get("source_drawing_id") == drawing_id
                        and row.get("source_page") == page_number):
                    rows.append({"table": table, "id": row["id"]})
        return rows


def _proven_document(store, project_id, *, storage_path, content_sha256, page_count):
    """A document whose identity IS proven, recorded by the production write path."""
    return store.create_project_document(
        project_id, storage_path=storage_path,
        file_name=storage_path.rsplit("/", 1)[-1], source_format="PDF",
        byte_size=1024, page_count=page_count, content_sha256=content_sha256,
    )


def _reading(store, project_id, *, document, storage_path, page_count):
    """The drawing set and drawing that read one document, through the production writers.

    The order is production's own: the document is recorded first, the drawing that
    reads it is created naming it, and the count the reading established is written
    against both rows.
    """
    drawing_set = store.create_drawing_set(project_id, f"Set {storage_path.rsplit('/', 1)[-1]}")
    drawing = store.create_drawing(
        drawing_set["id"], storage_path.rsplit("/", 1)[-1], storage_path, document["id"],
    )
    store.update_drawing_meta(drawing["id"], page_count, None, None, None)
    return drawing_set, drawing


def _record(store, project_id, *, total, analysed, failed=()):
    """A coverage record written by the production writers, and counters that agree.

    Nothing here is a transcription of a format: the lines are `coverage_of(...).as_line()`
    and `failures_of(...).as_line()`, the writers every commit uses, and the counters are
    the same two facts the pipeline writes beside them. J17 names the failed PAGES in a
    second line written with the coverage line, so a retry target exists only when both
    are present — which is why a record built here with `failed=()` is a project whose
    failures are UNRECORDED rather than one with none.
    """
    coverage = coverage_of(
        total_pages=total,
        page_numbers=range(1, analysed + 1),
        parse_failed_page_numbers=failed,
    )
    warnings = [coverage.as_line()]
    if failed:
        warnings.append(failures_of(list(failed)).as_line())
    store.projects[project_id]["warnings"] = warnings
    for row in store.drawing_sets.values():
        if row.get("project_id") == project_id:
            row["total_pages"] = total
            row["pages_analysed"] = analysed
    return coverage


@pytest.fixture()
def store(production, monkeypatch):
    """One double, and BOTH seams pointed at it.

    The pipeline resolves a source through `app.production_extraction_source`, which is
    handed this module's own `repo`; the routes resolve through the same boundary. So a
    test that replaces only the pipeline's store would leave the source resolution
    reading the live database, and this fixture exists to make that impossible.
    """
    repository = _Documents()
    monkeypatch.setattr(production.pipeline, "repo", repository)
    monkeypatch.setattr(source, "repo", repository)
    return repository


# ===========================================================================
# The HTTP surface — the mechanism production actually calls.
# ===========================================================================
class _Http:
    """The real application, the real routes, one authenticated client per caller.

    The COMPOSITION behind each route is doubled, and only the composition: these tests
    are about the route's mapping from a stated request to a status and a dispatched
    task. Doubling it is what keeps them from extracting a real document to look at a
    status code, and the dispatched task is RECORDED rather than run.
    """

    def __init__(self, production_module, monkeypatch, *, project_id, project_row):
        self.main = production_module.main
        self.project_id = project_id
        self.project_row = project_row
        clients = {project_id: project_row}
        monkeypatch.setattr(self.main, "supabase", j50._ProjectsClient(clients))
        tokens = auth.install(monkeypatch, production_module)
        from fastapi.testclient import TestClient

        self.http = TestClient(self.main.app, headers=tokens.headers(project_row["user_id"]))
        self.other = TestClient(self.main.app, headers=tokens.headers(OTHER_OWNER))
        self.bare = TestClient(self.main.app)
        self.dispatched: list[tuple] = []
        for name in ("run_extraction", "run_continuation", "run_retry"):
            monkeypatch.setattr(self.main, name, self._recorder(name))

    def _recorder(self, name):
        def record(*args, **kwargs):
            self.dispatched.append((name, args, kwargs))

        return record

    def url(self, route):
        return route.replace("{project_id}", self.project_id)

    def only(self):
        """The one dispatched task, or a failure that names what happened instead."""
        assert len(self.dispatched) == 1, self.dispatched
        return self.dispatched[0]


@pytest.fixture()
def http(production, monkeypatch, store):
    """One project, one document, its reading — and the real routes in front of them."""
    store.seed_project(PROJECT_ONE, storage_path=PATH_A)
    document = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
    _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
             page_count=DOCUMENT_PAGES)
    _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)
    return _Http(production, monkeypatch, project_id=PROJECT_ONE,
                 project_row=store.projects[PROJECT_ONE])


@pytest.fixture()
def two_document_http(production, monkeypatch, store):
    """One project, TWO documents and TWO readings — the milestone's own diagram."""
    store.seed_project(PROJECT_ONE, storage_path=PATH_A)
    document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
    document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
    _reading(store, PROJECT_ONE, document=document_a, storage_path=PATH_A,
             page_count=DOCUMENT_PAGES)
    _reading(store, PROJECT_ONE, document=document_b, storage_path=PATH_B,
             page_count=DOCUMENT_PAGES)
    _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)
    return _Http(production, monkeypatch, project_id=PROJECT_ONE,
                 project_row=store.projects[PROJECT_ONE])


def _refused(exception_info):
    return exception_info.value.code, exception_info.value.detail


# ===========================================================================
# A. A PROJECT WITH ONE DOCUMENT BEHAVES EXACTLY AS IT DID BEFORE J63.
# ===========================================================================
class TestATheSingleDocumentProjectIsUnchanged:
    def test_an_omitted_address_resolves_the_project_level_source(self, store):
        """The pre-J63 source, read off the project row the caller already holds."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        project = store.get_project(PROJECT_ONE)

        resolved = source.resolve_extraction_source(
            PROJECT_ONE, project=project, repository=store,
        )

        assert resolved.storage_path == PATH_A
        assert resolved.document_id is None
        assert resolved.addressed is False

    def test_an_omitted_address_reads_no_document_at_all(self, store):
        """The omitted request makes NO new database read.

        This is the property that makes "a project that behaved in some way before J63
        behaves that way after it" a statement about code that did not run rather than
        an argument about code that did.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        project = store.get_project(PROJECT_ONE)
        store.reads.clear()

        source.resolve_extraction_source(PROJECT_ONE, project=project, repository=store)

        assert store.reads == [], store.reads

    def test_an_omitted_address_and_the_one_document_agree(self, store):
        """With one document there is nothing to choose, so naming it changes nothing."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        project = store.get_project(PROJECT_ONE)

        omitted = source.resolve_extraction_source(
            PROJECT_ONE, project=project, repository=store,
        )
        addressed = source.resolve_extraction_source(
            PROJECT_ONE, document_id=document["id"], project=project, repository=store,
        )

        assert omitted.storage_path == addressed.storage_path == PATH_A

    def test_the_extract_route_answers_the_pre_j63_response_and_source(self, http):
        """Same status, same keys, same source the task is handed."""
        response = http.http.post(http.url("/extract/{project_id}"))

        assert response.status_code == 200
        assert response.json() == {"status": "extraction_started", "project_id": PROJECT_ONE}
        name, args, kwargs = http.only()
        assert name == "run_extraction"
        assert args == (PROJECT_ONE, PATH_A, "PDF", store_user(http))
        assert kwargs == {}

    def test_an_empty_body_is_the_request(self, http):
        """`{}` says what an absent body says: this caller names no document."""
        assert http.http.post(http.url("/extract/{project_id}"), json={}).status_code == 200
        assert source.parse_extraction_source_request({}) is None
        assert source.parse_extraction_source_request(None) is None

    def test_a_project_with_no_source_is_still_a_400(self, store, production, monkeypatch):
        """The pre-existing guard, unchanged and still first."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.projects[PROJECT_ONE]["uploaded_file_path"] = None
        surface = _Http(production, monkeypatch, project_id=PROJECT_ONE,
                        project_row=store.projects[PROJECT_ONE])

        response = surface.http.post(surface.url("/extract/{project_id}"))

        assert response.status_code == 400
        assert surface.dispatched == []

    def test_the_continuation_route_is_unchanged_for_one_document(self, http):
        response = http.http.post(http.url("/continue-extraction/{project_id}"))

        assert response.status_code == 200
        assert response.json() == {
            "status": "continuation_started", "project_id": PROJECT_ONE,
            "first_page": 31, "last_page": DOCUMENT_PAGES,
        }
        name, args, kwargs = http.only()
        assert name == "run_continuation"
        assert args[-2:] == (31, DOCUMENT_PAGES)
        assert kwargs == {"document_id": None}

    def test_the_retry_route_is_unchanged_for_one_document(self, http, store):
        """A record that names no failed page is refused, exactly as before."""
        refused = http.http.post(http.url("/retry-extraction/{project_id}?page=1"))

        assert refused.status_code == 409
        assert refused.json()["detail"]["refusal"] == "RETRY_FAILURES_UNRECORDED"
        assert http.dispatched == []

    def test_the_retry_route_dispatches_the_page_the_record_names(self, http, store):
        """And with the failure recorded, the page is retried with no address at all."""
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30, failed=(1,))

        response = http.http.post(http.url("/retry-extraction/{project_id}?page=1"))

        assert response.status_code == 200, response.json()
        assert response.json() == {
            "status": "retry_started", "project_id": PROJECT_ONE, "page_number": 1,
        }
        name, args, kwargs = http.only()
        assert name == "run_retry"
        assert args[1] == PATH_A
        assert kwargs == {"document_id": None}


def store_user(surface):
    """The `user_id` the route passes to the background task: the project's own owner."""
    return surface.project_row["user_id"]


# ===========================================================================
# B. AN ADDRESSED DOCUMENT RESOLVES TO THAT DOCUMENT.
# ===========================================================================
class TestBAnAddressedDocumentResolves:
    def test_the_addressed_document_is_the_one_resolved(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        project = store.get_project(PROJECT_ONE)

        for document in (document_a,):
            resolved = source.resolve_extraction_source(
                PROJECT_ONE, document_id=document["id"], project=project, repository=store,
            )
            assert resolved.storage_path == document["storage_path"]
            assert resolved.document_id == document["id"]
            assert resolved.addressed is True

    def test_each_of_two_documents_resolves_to_its_own_path(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        project = store.get_project(PROJECT_ONE)

        first = source.resolve_extraction_source(
            PROJECT_ONE, document_id=DOC_A, project=project, repository=store,
        )
        second = source.resolve_extraction_source(
            PROJECT_ONE, document_id=DOC_B, project=project, repository=store,
        )

        assert (first.storage_path, second.storage_path) == (PATH_A, PATH_B)

    def test_the_project_level_column_is_not_consulted_when_a_document_is_addressed(
        self, store
    ):
        """The project's own column points at A; the address says B, and B is what is read."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        project = store.get_project(PROJECT_ONE)
        assert project["uploaded_file_path"] == PATH_A

        resolved = source.resolve_extraction_source(
            PROJECT_ONE, document_id=DOC_B, project=project, repository=store,
        )

        assert resolved.storage_path == PATH_B

    def test_the_extract_route_dispatches_the_addressed_documents_path(
        self, two_document_http
    ):
        surface = two_document_http

        response = surface.http.post(
            surface.url("/extract/{project_id}"), json={"document_id": DOC_B},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "extraction_started", "project_id": PROJECT_ONE}
        name, args, kwargs = surface.only()
        assert name == "run_extraction"
        assert args[1] == PATH_B

    def test_the_boundary_reads_exactly_two_columns_of_an_addressed_row(self):
        """A frozen pin on what an address consults: the id looked for, the path taken.

        Anything else a document row carries — its role, its revision label, its page
        count, its hash, when it was created — is not consulted by this milestone, and
        a change that started consulting one would land here.
        """
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
        keys = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args
            and isinstance(node.args[0], ast.Constant)
        }

        # `uploaded_file_path` is the omitted request's own key, read off the PROJECT row.
        # No other key of any row is consulted on either path.
        assert keys == {"id", "storage_path", "uploaded_file_path"}, keys


# ===========================================================================
# C. A DOCUMENT OF ANOTHER PROJECT IS UNREACHABLE, NOT MERELY REFUSED.
# ===========================================================================
class TestCAForeignDocumentCannotBeReached:
    def _two_projects(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_project(PROJECT_TWO, storage_path=PATH_B)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        store.seed_document(PROJECT_TWO, document_id=DOC_FOREIGN, storage_path=PATH_B)
        return store.get_project(PROJECT_ONE)

    def test_a_document_of_another_project_is_refused(self, store):
        project = self._two_projects(store)

        with pytest.raises(ContinuationRefused) as refused:
            source.resolve_extraction_source(
                PROJECT_ONE, document_id=DOC_FOREIGN, project=project, repository=store,
            )

        code, detail = _refused(refused)
        assert code == CONTINUATION_SOURCE_MISMATCH
        assert PROJECT_ONE in detail and DOC_FOREIGN in detail

    def test_only_this_projects_own_documents_are_listed(self, store):
        """The refusal is decided by a read of THIS project's documents, so a foreign
        document is never even loaded — a cross-project lookup cannot be forgotten
        because it does not exist."""
        project = self._two_projects(store)
        store.reads.clear()

        with pytest.raises(ContinuationRefused):
            source.resolve_extraction_source(
                PROJECT_ONE, document_id=DOC_FOREIGN, project=project, repository=store,
            )

        assert store.reads == ["project_documents_for_project"], store.reads

    def test_the_route_answers_a_foreign_address_with_409(self, two_document_http):
        surface = two_document_http

        response = surface.http.post(
            surface.url("/extract/{project_id}"), json={"document_id": DOC_FOREIGN},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_SOURCE_MISMATCH
        assert surface.dispatched == []

    def test_the_route_answers_before_anything_is_downloaded_or_queued(
        self, two_document_http
    ):
        """A refused address is a conflict with persisted state, decided while the
        caller is listening: the response IS the whole of the outcome."""
        surface = two_document_http
        before = copy.deepcopy(surface.main.supabase.table("projects").select("id").eq(
            "id", PROJECT_ONE).single().execute().data)

        surface.http.post(surface.url("/extract/{project_id}"), json={"document_id": DOC_FOREIGN})

        assert surface.dispatched == []
        after = surface.main.supabase.table("projects").select("id").eq(
            "id", PROJECT_ONE).single().execute().data
        assert after == before


# ===========================================================================
# D. A DOCUMENT THAT DOES NOT EXIST IS REFUSED, NOT INVENTED.
# ===========================================================================
class TestDAbsentDocumentIsRefused:
    def test_an_id_no_row_carries_is_refused(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        project = store.get_project(PROJECT_ONE)

        with pytest.raises(ContinuationRefused) as refused:
            source.resolve_extraction_source(
                PROJECT_ONE, document_id=DOC_ABSENT, project=project, repository=store,
            )

        code, detail = _refused(refused)
        assert code == CONTINUATION_SOURCE_MISMATCH
        assert "1" in detail              # the refusal states how many the project keeps

    def test_a_project_with_no_documents_refuses_any_address(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        project = store.get_project(PROJECT_ONE)

        with pytest.raises(ContinuationRefused) as refused:
            source.resolve_extraction_source(
                PROJECT_ONE, document_id=DOC_A, project=project, repository=store,
            )

        assert _refused(refused)[0] == CONTINUATION_SOURCE_MISMATCH

    def test_a_value_that_is_not_a_name_is_refused_rather_than_coerced(self):
        """A half-stated address is not an address, and this boundary does not choose."""
        for value in ("", "   ", 0, 1, True, [], {}, ["document-1"]):
            with pytest.raises(source.ExtractionInputRefused) as refused:
                source.parse_extraction_source_request({"document_id": value})
            assert refused.value.code == source.EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID

    def test_an_explicit_null_names_no_document(self):
        """JSON's only way to say "no value" is the same request as omitting the key."""
        assert source.parse_extraction_source_request({"document_id": None}) is None

    def test_a_named_document_is_returned_verbatim(self):
        """The value is not inspected, normalised or validated for shape."""
        assert source.parse_extraction_source_request(
            {"document_id": "  'not-a-uuid'  "}
        ) == "  'not-a-uuid'  "

    def test_the_route_refuses_a_malformed_address_with_422(self, http):
        response = http.http.post(
            http.url("/extract/{project_id}"), json={"document_id": ""},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["refusal"] == (
            source.EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID
        )
        assert http.dispatched == []


# ===========================================================================
# E. SEVERAL DOCUMENTS, NONE ADDRESSED: REFUSED, NEVER CHOSEN BETWEEN.
# ===========================================================================
class TestESeveralDocumentsAreAmbiguous:
    def test_the_planner_refuses_and_names_the_ambiguity(self, store, production):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        _reading(store, PROJECT_ONE, document=document_a, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _reading(store, PROJECT_ONE, document=document_b, storage_path=PATH_B,
                 page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A)

        code, detail = _refused(refused)
        assert code == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert "ambiguous" in detail

    def test_no_observable_of_a_document_can_select_one(self, store, production):
        """The choice must not be reachable by making one document look likelier.

        Every axis the brief forbids is exercised at once: the second document is made
        smaller, read in fewer rows, created later, given the more structural-looking of
        the two ids and the more official-looking name. None of it may select one —
        because the omitted request reads no document row at all.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        _reading(store, PROJECT_ONE, document=document_a, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _reading(store, PROJECT_ONE, document=document_b, storage_path=PATH_B,
                 page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)
        store.documents[1]["file_name"] = "FABRICATION-drawings.pdf"
        store.documents[1]["page_count"] = 1
        store.documents[1]["role"] = "FABRICATION"
        store.documents[1]["created_at"] = "2030-01-01T00:00:00+00:00"
        store.reads.clear()

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A)

        assert _refused(refused)[0] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert "project_documents_for_project" not in store.reads

    def test_the_route_refuses_an_unaddressed_two_document_project(self, two_document_http):
        """The same code and the same status as the milestone found, not a new one."""
        surface = two_document_http

        response = surface.http.post(surface.url("/continue-extraction/{project_id}"))

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert surface.dispatched == []


# ===========================================================================
# F. SEVERAL DOCUMENTS, ONE ADDRESSED: THAT ONE IS SELECTED.
# ===========================================================================
class TestFAnAddressedDocumentIsSelected:
    def _two_lineages(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        set_a, drawing_a = _reading(store, PROJECT_ONE, document=document_a,
                                    storage_path=PATH_A, page_count=DOCUMENT_PAGES)
        set_b, drawing_b = _reading(store, PROJECT_ONE, document=document_b,
                                    storage_path=PATH_B, page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)
        return (document_a, set_a, drawing_a), (document_b, set_b, drawing_b)

    def test_the_addressed_documents_lineage_is_the_one_planned(self, store, production):
        """The heart of the milestone: a second document does not refuse the first."""
        first, second = self._two_lineages(store)

        plan_a = production.pipeline.plan_continuation(
            PROJECT_ONE, PATH_A, document_id=DOC_A,
        )
        plan_b = production.pipeline.plan_continuation(
            PROJECT_ONE, PATH_B, document_id=DOC_B,
        )

        assert plan_a.drawing["id"] == first[2]["id"]
        assert plan_b.drawing["id"] == second[2]["id"]
        assert plan_a.drawing_set["id"] == first[1]["id"]
        assert plan_b.drawing_set["id"] == second[1]["id"]

    def test_the_request_addressed_at_one_document_is_not_refused(self, store, production):
        """Stated on its own, because it is the defect this milestone exists to close."""
        self._two_lineages(store)

        plan = production.pipeline.plan_continuation(
            PROJECT_ONE, PATH_B, document_id=DOC_B,
        )

        assert (plan.window.first_page, plan.window.last_page) == (31, DOCUMENT_PAGES)

    def test_the_route_dispatches_the_addressed_documents_window(self, two_document_http):
        surface = two_document_http

        response = surface.http.post(
            surface.url("/continue-extraction/{project_id}"), json={"document_id": DOC_B},
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "continuation_started", "project_id": PROJECT_ONE,
            "first_page": 31, "last_page": DOCUMENT_PAGES,
        }
        name, args, kwargs = surface.only()
        assert name == "run_continuation"
        assert args[1] == PATH_B
        assert kwargs == {"document_id": DOC_B}

    def test_two_attempts_at_one_document_are_still_ambiguous_when_it_is_addressed(
        self, store
    , production):
        """One document read twice is two lineages, and neither is preferred."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A, document_id=DOC_A)

        code, detail = _refused(refused)
        assert code == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert "2" in detail


# ===========================================================================
# G. THE SAME DOCUMENT EXTRACTED TWICE IS NOT A SECOND DOCUMENT.
# ===========================================================================
@pytest.fixture()
def genuine_documents(production, monkeypatch, tmp_path):
    """ONE project, TWO source documents, both driven through the GENUINE pipeline.

    J16's own harness — a real PDF, a real `page_count_of`, a page-honest stand-in for
    the vision stage, a pinned reference index — with its repository subclassed for the
    one read J63 added, exactly as J17 subclasses it for its own one read.
    """
    # pytest resolves a fixture by name within its own module, so J16's `windows` and
    # `document` fixtures are reached by calling their own functions with J16's own
    # collaborators — the J17 idiom, and the reason there is one harness and not two.
    windows_module = _fixture_function(j16.windows)(production)
    monkeypatch.setattr(j16, "_ServingRepository", _Documents)
    build = _fixture_function(j16.document)(production, windows_module, monkeypatch, tmp_path)

    first = build(pages=DOCUMENT_PAGES, source_name="j63-a.pdf", storage_path=PATH_A)
    first.first_window()

    # The second document: the SAME page count (so the two lineages' records agree on
    # the document's size, which is one project-level record for the whole project),
    # DIFFERENT bytes (a different page size — which is what makes it a different
    # document under the J61 content rule), and different marks.
    second_source = _write_pdf(tmp_path / "j63-b.pdf", DOCUMENT_PAGES, width=595, height=842)
    _, reader = _reader(DOCUMENT_PAGES, prefix="N")
    monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", reader)
    second = production.pipeline.parse_pdf_and_save(
        second_source, first.project_id, "j16-user", PATH_B,
    )

    monkeypatch.setattr(source, "repo", first.repository)
    documents = first.repository.project_documents_for_project(first.project_id)
    drawings = list(first.repository.drawings.values())
    by_path = {document["storage_path"]: document for document in documents}
    by_document = {drawing["document_id"]: drawing for drawing in drawings}

    return types.SimpleNamespace(
        first=first,
        second=second,
        repository=first.repository,
        project_id=first.project_id,
        documents=documents,
        drawings=drawings,
        document_for_path=lambda path: by_path[path],
        drawing_for_document=lambda document_id: by_document[document_id],
        second_source=second_source,
    )


def _write_pdf(path, pages, *, width=612, height=792):
    """A REAL PDF, written by pypdf itself — J16's writer, with the page size exposed.

    The size is what makes two documents two: `page_count_of` reads the real document,
    and the J61 identity is the hash of the real bytes, so a second file that differed
    only in its page count would be a different SIZE as well as a different document.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _reader(pages, *, prefix="M"):
    """The vision stage, replaced: one genuine `PageExtraction` per page, then recorded.

    J16's own stand-in is closed over the members of the FIRST document; a second
    document needs its own marks, so this one is parameterised by prefix. Nothing else
    differs: the pages are real production objects and every call is recorded.
    """
    calls = []

    def read(filepath, user_id, project_id, drawing_id, max_pages, *,
             first_page=1, store_page_image=True):
        calls.append({"first_page": first_page, "max_pages": max_pages})
        last_page = min(first_page + max_pages - 1, pages)
        return [
            j16._page(number, [j4._member(f"{prefix}{number}", EXACT_TOKEN)])
            for number in range(first_page, last_page + 1)
        ]

    return calls, read


class TestGTheSameDocumentIsNotASecondDocument:
    def test_two_different_files_are_two_documents(self, genuine_documents):
        doc = genuine_documents

        assert len(doc.documents) == 2
        assert {document["storage_path"] for document in doc.documents} == {PATH_A, PATH_B}

    def test_each_documents_identity_is_the_hash_of_the_bytes_it_was_read_from(
        self, genuine_documents, production
    ):
        """Rule 3: the identity is content, and nothing here invents a second rule."""
        from app.drawing_reading.pdf_annotation_extractor import (
            source_bytes,
            source_document_sha256,
        )

        doc = genuine_documents
        for path, stored_path in ((doc.first.source, PATH_A), (doc.second_source, PATH_B)):
            document = doc.document_for_path(stored_path)
            assert document["content_sha256"] == source_document_sha256(source_bytes(path))

    def test_a_repeat_read_of_one_file_is_not_a_second_row(self, store):
        """Rule 4 at the write the run performs: a repeat is a repeat."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)

        first = _proven_document(store, PROJECT_ONE, storage_path=PATH_A,
                                 content_sha256=_HASH_A, page_count=DOCUMENT_PAGES)
        second = _proven_document(store, PROJECT_ONE, storage_path=PATH_A,
                                  content_sha256=_HASH_A, page_count=DOCUMENT_PAGES)

        assert first["id"] == second["id"]
        assert len(store.documents) == 1

    def test_re_reading_one_document_makes_a_second_reading_not_a_second_document(
        self, genuine_documents, production, monkeypatch
    ):
        """The genuine case, and the reason the address is a document and not a drawing.

        A whole-document run creates a drawing set and a drawing every time — the
        lineage is per attempt. The DOCUMENT is not: the run that read the same bytes
        again resolves to the document the first run recorded, and the new drawing
        names that same row. So a caller addressing the document after this is
        addressing one thing that two lineages read — which is ambiguity, and is
        refused rather than chosen between.
        """
        doc = genuine_documents
        before_documents = [row["id"] for row in doc.repository.documents]
        before_sets = len(doc.repository.drawing_sets)

        _, reader = _reader(DOCUMENT_PAGES, prefix="R")
        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", reader)
        production.pipeline.parse_pdf_and_save(
            doc.first.source, doc.project_id, "j16-user", PATH_A,
        )

        assert [row["id"] for row in doc.repository.documents] == before_documents
        assert len(doc.repository.drawing_sets) == before_sets + 1
        third = [
            drawing for drawing in doc.repository.drawings.values()
            if drawing["storage_path"] == PATH_A
        ]
        assert len(third) == 2
        assert len({drawing["document_id"] for drawing in third}) == 1


# ===========================================================================
# H. TWO DOCUMENTS ARE INDEPENDENTLY ADDRESSABLE — GENUINELY.
# ===========================================================================
class TestHTwoDocumentsAreIndependentlyAddressable:
    def test_each_document_resolves_to_its_own_stored_file(self, genuine_documents):
        doc = genuine_documents

        first = source.resolve_extraction_source(
            doc.project_id, document_id=doc.document_for_path(PATH_A)["id"],
            project=doc.repository.get_project(doc.project_id), repository=doc.repository,
        )
        second = source.resolve_extraction_source(
            doc.project_id, document_id=doc.document_for_path(PATH_B)["id"],
            project=doc.repository.get_project(doc.project_id), repository=doc.repository,
        )

        assert (first.storage_path, second.storage_path) == (PATH_A, PATH_B)

    def test_both_documents_can_be_continued_at_once(self, genuine_documents, production):
        """The milestone's own diagram, over the genuine pipeline's own writes."""
        doc = genuine_documents

        plan_a = production.pipeline.plan_continuation(
            doc.project_id, PATH_A, document_id=doc.document_for_path(PATH_A)["id"],
        )
        plan_b = production.pipeline.plan_continuation(
            doc.project_id, PATH_B, document_id=doc.document_for_path(PATH_B)["id"],
        )

        assert plan_a.drawing["storage_path"] == PATH_A
        assert plan_b.drawing["storage_path"] == PATH_B
        assert plan_a.drawing["id"] != plan_b.drawing["id"]

    def test_the_second_document_is_unreachable_without_addressing_it(
        self, genuine_documents
    , production):
        """The project-level column still points at the first upload, and the address is
        the only thing that reaches the second document — which is the capability."""
        doc = genuine_documents
        project = doc.repository.get_project(doc.project_id)

        assert project["uploaded_file_path"] == PATH_A
        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(doc.project_id, PATH_A)

        assert _refused(refused)[0] == CONTINUATION_DRAWING_SET_UNRESOLVED

    def test_the_route_continues_the_addressed_document_of_the_genuine_project(
        self, genuine_documents, production, monkeypatch
    ):
        doc = genuine_documents
        surface = _Http(production, monkeypatch, project_id=doc.project_id,
                        project_row=doc.repository.get_project(doc.project_id))

        response = surface.http.post(
            surface.url("/continue-extraction/{project_id}"),
            json={"document_id": doc.document_for_path(PATH_B)["id"]},
        )

        assert response.status_code == 200
        name, args, kwargs = surface.only()
        assert name == "run_continuation"
        assert args[1] == PATH_B
        assert kwargs == {"document_id": doc.document_for_path(PATH_B)["id"]}


# ===========================================================================
# I. THE DRAWING THE RUN CREATED NAMES THE DOCUMENT IT READ.
# ===========================================================================
class TestITheDrawingNamesItsDocument:
    def test_every_drawing_names_a_document_and_every_document_is_named(
        self, genuine_documents
    ):
        """Rule 8: the chain is `drawings.document_id → project_documents.id`, and the
        extraction boundary adds no second edge to it."""
        doc = genuine_documents

        assert len(doc.drawings) == 2
        assert {drawing["document_id"] for drawing in doc.drawings} == {
            document["id"] for document in doc.documents
        }

    def test_each_drawing_names_the_document_whose_bytes_it_read(self, genuine_documents):
        doc = genuine_documents

        for drawing in doc.drawings:
            document = doc.repository.document_for_drawing(drawing["id"])
            assert document is not None
            assert document["id"] == drawing["document_id"]
            assert document["storage_path"] == drawing["storage_path"]

    def test_the_extraction_path_writes_no_document_id_to_any_other_row(
        self, genuine_documents
    ):
        """The run recorded two links, one per drawing, and no other document write."""
        doc = genuine_documents

        links = doc.repository.document_links
        assert len(links) == len(doc.drawings)
        assert {link["drawing_set_id"] for link in links} == set(
            doc.repository.drawing_sets
        )


# ===========================================================================
# J. PROVENANCE: CAPTURE → DRAWING → DOCUMENT.
# ===========================================================================
class TestJProvenanceIsIntact:
    def test_every_capture_reaches_its_document_through_the_drawing_it_names(
        self, genuine_documents
    ):
        doc = genuine_documents
        documents_by_id = {document["id"]: document for document in doc.documents}

        assert doc.repository.captures, "the genuine runs recorded no reading"
        reached = set()
        for capture in doc.repository.captures:
            drawing = doc.repository.drawings[capture["drawing_id"]]
            reached.add(documents_by_id[drawing["document_id"]]["storage_path"])

        assert reached == {PATH_A, PATH_B}, reached

    def test_the_capture_columns_carry_no_document_of_their_own(self):
        """The hop stays the drawing's, which is the only edge J61 added."""
        assert "document_id" not in captures.CAPTURE_COLUMNS
        assert set(captures.CAPTURE_COLUMNS) == {
            "drawing_id", "drawing_set_id", "project_id", "page_number", "analysis_run_id",
            "model", "parse_failed", "payload", "captured_at",
        }

    def test_the_document_of_a_capture_is_read_the_way_production_reads_it(
        self, genuine_documents
    ):
        """`document_for_drawing` is the production hop, and it is the only one used."""
        doc = genuine_documents
        drawing = doc.drawing_for_document(doc.document_for_path(PATH_B)["id"])

        assert doc.repository.document_for_drawing(drawing["id"])["storage_path"] == PATH_B
        assert doc.repository.document_for_drawing("no-such-drawing") is None


# ===========================================================================
# K. A NULL `content_sha256` IS NOT AN IDENTITY, AND NEVER MATCHES.
# ===========================================================================
class TestKAnUnprovenIdentityIsNotAnIdentity:
    def test_a_legacy_document_is_addressable_without_its_identity_being_proven(
        self, store
    ):
        """Rule 7: the backfilled rows are NULL-hash and UNKNOWN-role, and the address
        works with them. Nothing here infers or fills either value."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        legacy = store.seed_document(
            PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A,
            content_sha256=None, role="UNKNOWN",
        )
        project = store.get_project(PROJECT_ONE)

        resolved = source.resolve_extraction_source(
            PROJECT_ONE, document_id=legacy["id"], project=project, repository=store,
        )

        assert resolved.document_id == DOC_A
        assert resolved.storage_path == PATH_A
        assert store.documents[0]["content_sha256"] is None
        assert store.documents[0]["role"] == "UNKNOWN"

    def test_a_null_hash_is_never_matched_by_a_hash(self, store):
        """The write the run performs. A NULL is the ABSENCE of an identity, so it
        cannot be the identity of any bytes — including the bytes it was read from."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A,
                            content_sha256=None)

        recorded = _proven_document(store, PROJECT_ONE, storage_path=PATH_A,
                                    content_sha256=_HASH_A, page_count=DOCUMENT_PAGES)

        assert recorded["id"] != DOC_A
        assert len(store.documents) == 2
        assert store.documents[0]["content_sha256"] is None

    def test_the_boundary_never_reads_or_computes_a_hash(self):
        """The address is an id. No hash is consulted, matched or recomputed here."""
        text = SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(text)
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        imported = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }

        assert not any("sha" in name.lower() or "hash" in name.lower() for name in names), names
        assert not any("sha" in name.lower() for name in imported), imported
        assert "content_sha256" not in {name for name in names}, names

    def test_the_omitted_request_is_unaffected_by_an_unproven_document(self, store):
        """A NULL-hash document cannot change what an unaddressed request reads."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_B,
                            content_sha256=None)
        project = store.get_project(PROJECT_ONE)
        store.reads.clear()

        resolved = source.resolve_extraction_source(
            PROJECT_ONE, project=project, repository=store,
        )

        assert resolved.storage_path == PATH_A
        assert store.reads == []


# ===========================================================================
# L. THE CONTINUATION GUARDS STAY FAIL-CLOSED.
# ===========================================================================
class TestLTheContinuationGuardsStayFailClosed:
    def _one_lineage(self, store, *, drawing_path=None):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        if drawing_path is not None:
            drawing = next(iter(store.drawings.values()))
            drawing["storage_path"] = drawing_path
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)
        return document

    def test_a_source_that_disagrees_with_the_addressed_document_is_refused(self, store, production):
        self._one_lineage(store)

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(
                PROJECT_ONE, PATH_UNREAD, document_id=DOC_A,
            )

        assert _refused(refused)[0] == CONTINUATION_SOURCE_MISMATCH

    def test_a_lineage_that_read_a_different_file_is_refused(self, store, production):
        """Guard D's other half: the bytes about to be read must be the bytes the
        lineage read, or a window would be added to a record made from other bytes."""
        self._one_lineage(store, drawing_path=PATH_UNREAD)

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A, document_id=DOC_A)

        code, detail = _refused(refused)
        assert code == CONTINUATION_SOURCE_MISMATCH
        assert PATH_UNREAD in detail

    def test_a_project_with_no_coverage_record_is_still_refused(self, store, production):
        self._one_lineage(store)
        store.projects[PROJECT_ONE]["warnings"] = None

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A, document_id=DOC_A)

        assert _refused(refused)[0] == CONTINUATION_NO_COVERAGE_RECORD

    def test_a_record_that_disagrees_with_its_counters_is_still_refused(self, store, production):
        self._one_lineage(store)
        for row in store.drawing_sets.values():
            row["pages_analysed"] = 7

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A, document_id=DOC_A)

        assert _refused(refused)[0] == CONTINUATION_RECORD_CONFLICT

    def test_an_addressed_continuation_writes_nothing_when_it_refuses(
        self, two_document_http
    ):
        """A refusal is decided from persisted state and leaves all of it where it was."""
        surface = two_document_http
        before = copy.deepcopy(surface.main.supabase.table("projects").select("id").eq(
            "id", PROJECT_ONE).single().execute().data)

        response = surface.http.post(
            surface.url("/continue-extraction/{project_id}"),
            json={"document_id": DOC_FOREIGN},
        )

        assert response.status_code == 409
        assert surface.dispatched == []
        after = surface.main.supabase.table("projects").select("id").eq(
            "id", PROJECT_ONE).single().execute().data
        assert after == before


# ===========================================================================
# THE RETRY PATH CARRIES THE SAME ADDRESS AND THE SAME GUARDS. (Section L's rules
# apply to it verbatim: a retry is a continuation of exactly one page.)
# ===========================================================================
class TestTheRetryPath:
    def _one_lineage(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30, failed=(3,))
        return document

    def test_the_retry_vocabulary_is_j17s_and_the_code_is_j16s(self, store, production):
        """One code, two exception types — the precedent J17 set for carrying J16."""
        self._one_lineage(store)

        with pytest.raises(RetryRefused) as refused:
            production.pipeline.plan_retry(
                PROJECT_ONE, PATH_UNREAD, page_number=3, document_id=DOC_A,
            )

        assert _refused(refused)[0] == CONTINUATION_SOURCE_MISMATCH

    def test_an_absent_address_on_the_retry_path_is_the_project_level_source(self, store, production):
        self._one_lineage(store)

        plan = production.pipeline.plan_retry(PROJECT_ONE, PATH_A, page_number=3)

        assert plan.page_number == 3
        assert plan.drawing["storage_path"] == PATH_A

    def test_an_addressed_retry_plans_the_addressed_documents_lineage(self, store, production):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        _reading(store, PROJECT_ONE, document=document_a, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _, drawing_b = _reading(store, PROJECT_ONE, document=document_b,
                                storage_path=PATH_B, page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30, failed=(3,))
        drawing_b["page_count"] = DOCUMENT_PAGES - 1       # B's own reading disagrees

        with pytest.raises(RetryRefused) as refused:
            production.pipeline.plan_retry(
                PROJECT_ONE, PATH_B, page_number=3, document_id=DOC_B,
            )

        code, detail = _refused(refused)
        assert code == "CONTINUATION_DOCUMENT_TOTAL_MISMATCH"
        assert str(DOCUMENT_PAGES - 1) in detail

    def test_the_route_passes_the_address_to_the_retry_it_dispatches(self, store, production,
                                                                     monkeypatch):
        document = self._one_lineage(store)
        surface = _Http(production, monkeypatch, project_id=PROJECT_ONE,
                        project_row=store.projects[PROJECT_ONE])

        response = surface.http.post(
            surface.url("/retry-extraction/{project_id}?page=3"),
            json={"document_id": document["id"]},
        )

        assert response.status_code == 200, response.json()
        assert response.json() == {
            "status": "retry_started", "project_id": PROJECT_ONE, "page_number": 3,
        }
        name, args, kwargs = surface.only()
        assert name == "run_retry"
        assert args[1] == PATH_A
        assert kwargs == {"document_id": DOC_A}

    def test_a_page_that_already_has_evidence_is_still_refused(self, store, production):
        document = self._one_lineage(store)
        drawing = next(iter(store.drawings.values()))
        store.members.append({"id": "m-1", "project_id": PROJECT_ONE,
                              "source_drawing_id": drawing["id"], "source_page": 3})

        with pytest.raises(RetryRefused) as refused:
            production.pipeline.plan_retry(
                PROJECT_ONE, PATH_A, page_number=3, document_id=document["id"],
            )

        assert _refused(refused)[0] == "RETRY_EVIDENCE_ALREADY_PERSISTED"


# ===========================================================================
# M. `CONTINUATION_SOURCE_MISMATCH` MEANS A GENUINELY MISMATCHED SOURCE — AND NOTHING
#    ELSE. The most dangerous way to get this milestone wrong is to widen the code until
#    it also refuses the case the milestone exists to allow: a second document.
# ===========================================================================
class TestMTheSourceMismatchCode:
    def _two_lineages(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        _reading(store, PROJECT_ONE, document=document_a, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _reading(store, PROJECT_ONE, document=document_b, storage_path=PATH_B,
                 page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)

    def test_a_different_document_is_not_a_mismatch(self, store, production):
        """The whole point: a second document is a second source, not a wrong one."""
        self._two_lineages(store)

        plan = production.pipeline.plan_continuation(
            PROJECT_ONE, PATH_B, document_id=DOC_B,
        )

        assert plan.drawing["storage_path"] == PATH_B

    def test_a_path_that_is_not_the_addressed_documents_is_a_mismatch(self, store,
                                                                      production):
        self._two_lineages(store)

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_A, document_id=DOC_B)

        code, detail = _refused(refused)
        assert code == CONTINUATION_SOURCE_MISMATCH
        assert PATH_A in detail and PATH_B in detail

    def test_an_unresolvable_address_is_a_mismatch_and_says_which_project(self, store,
                                                                          production):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        project = store.get_project(PROJECT_ONE)

        with pytest.raises(ContinuationRefused) as refused:
            source.resolve_extraction_source(
                PROJECT_ONE, document_id=DOC_ABSENT, project=project, repository=store,
            )

        code, detail = _refused(refused)
        assert code == CONTINUATION_SOURCE_MISMATCH
        assert PROJECT_ONE in detail and DOC_ABSENT in detail

    def test_the_route_answers_the_same_code_the_planner_raises(self, two_document_http):
        """One meaning for one code, at both layers: the route maps, it does not decide."""
        surface = two_document_http

        response = surface.http.post(
            surface.url("/continue-extraction/{project_id}"), json={"document_id": DOC_A},
        )

        assert response.status_code == 200            # A's own path is A's own path
        assert surface.dispatched != []
        surface.dispatched.clear()

        refused = surface.http.post(
            surface.url("/retry-extraction/{project_id}?page=1"),
            json={"document_id": DOC_FOREIGN},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["refusal"] == CONTINUATION_SOURCE_MISMATCH

    def test_the_code_is_reachable_from_every_route_that_reads_a_source(self, store,
                                                                        production,
                                                                        monkeypatch):
        for route, kwargs in (
            ("plan_continuation", {"document_id": DOC_FOREIGN}),
        ):
            store.seed_project(PROJECT_ONE, storage_path=PATH_A)
            document = store.seed_document(PROJECT_ONE, document_id=DOC_A,
                                           storage_path=PATH_A)
            _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                     page_count=DOCUMENT_PAGES)
            _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)
            with pytest.raises(ContinuationRefused) as refused:
                getattr(production.pipeline, route)(PROJECT_ONE, PATH_A, **kwargs)
            assert _refused(refused)[0] == CONTINUATION_SOURCE_MISMATCH

            with pytest.raises(RetryRefused) as retried:
                production.pipeline.plan_retry(
                    PROJECT_ONE, PATH_A, page_number=1, document_id=DOC_FOREIGN,
                )
            assert _refused(retried)[0] == CONTINUATION_SOURCE_MISMATCH


# ===========================================================================
# N. NO PROJECT WARNING, COUNT OR STATUS MOVES.
# ===========================================================================
class TestNWarningsAreUntouched:
    def test_an_addressed_continuation_adds_no_coverage_record_of_its_own(
        self, two_document_http
    ):
        """Rule 9: the coverage record is the project's, and this milestone writes none."""
        surface = two_document_http
        before = surface.main.supabase.table("projects").select("id").eq(
            "id", PROJECT_ONE).single().execute().data["warnings"]

        surface.http.post(surface.url("/continue-extraction/{project_id}"),
                          json={"document_id": DOC_B})

        after = surface.main.supabase.table("projects").select("id").eq(
            "id", PROJECT_ONE).single().execute().data["warnings"]
        assert after == before

    def test_the_boundary_holds_no_write_of_any_kind(self):
        """The module's own surface: it reads a project, it writes nothing."""
        text = SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(text)
        called = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        assert not {name for name in called if name.startswith(("update", "create", "insert",
                                                                "delete", "upsert"))}, called

    def test_the_pipeline_side_of_this_milestone_is_lifted_out_unread(self):
        """`_source_lineage` returns a lineage; it does not touch a warning, a counter or
        a status — the coverage record is read by the planner above it, as it always was."""
        body = _function_source(PIPELINE_PATH, "_source_lineage")

        assert "warnings" not in body
        assert "update_" not in body
        assert "create_" not in body


# ===========================================================================
# O. NO REVIEW REVISION IS RECORDED OR CONSULTED.
# ===========================================================================
class TestONoReviewRevisionIsTouched:
    def test_no_extraction_route_reaches_the_review_tables(self):
        """An extraction reads a source and writes evidence. A review revision is J22's,
        recorded by J50's operation against a claim the extraction never takes."""
        text = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(text)
        routes = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name in {"extract", "continue_extraction", "retry_extraction",
                              "_extraction_source"}
        }
        assert set(routes) == {"extract", "continue_extraction", "retry_extraction",
                               "_extraction_source"}

        for name, node in routes.items():
            body = ast.unparse(node)
            for forbidden in ("connection_review", "review_revision", "snapshot",
                              "acquire_project_review_claim", "open_project_review"):
                assert forbidden not in body, (name, forbidden)

    def test_the_address_is_not_an_argument_of_anything_that_takes_a_claim(self):
        """The claim seam and the extraction seam are different seams."""
        from app.production_review import project_review_claim as claim

        for name in ("acquire_project_review_claim", "release_project_review_claim"):
            signature = inspect.signature(getattr(claim, name))
            assert "document_id" not in signature.parameters

    def test_the_source_module_names_no_review_authority(self):
        text = SOURCE_PATH.read_text(encoding="utf-8")
        for forbidden in ("connection_review", "review_revision", "snapshot", "claim",
                          "artifact", "fabrication"):
            assert forbidden not in ast.unparse(ast.parse(text)), forbidden


# ===========================================================================
# P. NO EVIDENCE DIGEST INPUT MOVED.
# ===========================================================================
class TestPTheEvidenceDigestInputsAreUnchanged:
    def test_the_digest_still_covers_exactly_the_two_evidence_tables(self):
        from app.cad_engine.connection_review_snapshot import EVIDENCE_TABLES

        assert tuple(EVIDENCE_TABLES) == ("steel_members", "connections")

    def test_the_source_module_imports_nothing_that_could_compute_one(self):
        """A frozen pin on the boundary's whole reach: four imports, no more.

        Anything that hashes, digests, canonicalises or reconciles would have to arrive
        through one of these, and none of them is that.
        """
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
        imported = {
            (node.module or "") for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert imported == {
            "__future__",
            "dataclasses",
            "typing",
            "app.engineering_data",         # `from ... import repository as repo`
            "app.validation.page_windows",  # the source-mismatch code and its exception
        }, imported

    def test_the_address_is_not_an_input_of_the_digest_rule(self):
        """Rule 3's third sentence: identity stays `project_id + content_sha256`, and a
        document id is not evidence — the rule refuses a document table outright."""
        import pytest as _pytest

        from app.cad_engine.connection_review_snapshot import evidence_identity

        with _pytest.raises(ValueError):
            evidence_identity({
                "steel_members": [], "connections": [],
                "project_documents": [{"id": DOC_A}],
            })

    def test_the_digest_of_a_frozen_fixture_is_unchanged(self):
        from app.cad_engine.connection_review_snapshot import evidence_identity

        rows = {
            "steel_members": [{"id": "m-1", "mark": "B1", "section_name": "310UB40",
                               "review_status": "approved", "total_weight_kg": 12.5}],
            "connections": [{"id": "c-1", "member_id": "m-1",
                             "connection_type": "bolted", "review_status": "pending"}],
        }

        assert evidence_identity(rows)["evidence_digest"] == (
            "156f274404d76b01c8acaebf6d6fb5465e277c12ecee58a33aec635a2098e0dc"
        )


# ===========================================================================
# Q. NO `document_id` ON ANY APPEND-ONLY EVIDENCE TABLE.
# ===========================================================================
class TestQNoDocumentIdOnAppendOnlyEvidence:
    def test_the_capture_table_carries_no_document(self):
        assert "document_id" not in captures.CAPTURE_COLUMNS

    def test_the_repository_writes_no_document_id_to_evidence_tables(self):
        """Rule 8's own list, checked at the writer rather than at the schema."""
        for name in ("insert_members", "insert_page_extraction_captures",
                     "insert_pdf_annotation_occurrences", "insert_connection",
                     "insert_review_items"):
            body = _function_source(REPOSITORY_PATH, name)
            assert "document_id" not in body, name

    def test_the_evidence_reads_name_no_document_column(self):
        for name in ("member_rows_for_project", "connection_rows_for_project",
                     "evidence_rows_for_page"):
            body = _function_source(REPOSITORY_PATH, name)
            assert "document_id" not in body, name

    def test_the_only_document_writes_are_the_three_provenance_ones(self):
        """Which functions write a document column at all — frozen, so a new one lands
        on this list and has to be argued for."""
        tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
        writers = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and "document_id" in ast.unparse(node):
                writers.add(node.name)

        assert writers == {
            "create_drawing",           # the drawing names the document it read
            "create_project_document",  # the document row itself
            "document_for_drawing",     # the read of that edge
            "drawings_for_drawing_set",  # the select that carries the edge back
            "update_document_page_count",
            # J64: the role assertion. It names a document by id and writes one column of
            # its row — the role a human asserted — and it is the only writer here that
            # touches no drawing edge, no page count and no evidence table. It is on this
            # list because it addresses a document, which is what this list is about.
            "update_document_role",
        }, writers

    def test_the_pipeline_writes_a_document_id_only_where_J61_put_it(self):
        """One keyword on one writer: the drawing's own document edge."""
        body = _function_source(PIPELINE_PATH, "parse_pdf_and_save")

        assert body.count("document_id=") == 1

    def test_the_lineage_filter_reads_the_drawing_column_and_nothing_else(self):
        body = _function_source(PIPELINE_PATH, "_source_lineage")

        assert 'drawing.get("document_id")' in body
        assert "document_id=document_id" in body


# ===========================================================================
# R. THE FROZEN INTERFACES J44/J46/J48/J50 DEPEND ON ARE UNCHANGED.
# ===========================================================================
class TestRTheFrozenInterfaces:
    def test_the_opening_request_contract_is_still_J61s(self):
        import app.production_review_opening as opening

        assert opening.REQUEST_FIELDS == ("document_id",)
        assert opening.BASELINE_REVISION == 0
        assert "document_id" not in opening.SERVER_OWNED_FIELDS

    def test_the_resumption_seam_still_accepts_a_document_and_nothing_more(self):
        from app.production_review.project_workflow_resumption import resume_project_workflow

        signature = inspect.signature(resume_project_workflow)
        assert signature.parameters["document_id"].default is None
        assert "document_id=document_id" in inspect.getsource(resume_project_workflow)

    def test_the_extraction_request_contract_is_this_milestones_own(self):
        assert source.REQUEST_FIELDS == ("document_id",)
        assert "document_id" not in source.SERVER_OWNED_FIELDS
        assert source.ROUTE_PATHS == (
            "/extract/{project_id}",
            "/continue-extraction/{project_id}",
            "/retry-extraction/{project_id}",
        )

    def test_the_refusal_vocabulary_gained_no_new_code(self):
        """J16's and J17's codes are the whole of it: an address that cannot be resolved
        is a source mismatch, and a document with more than one lineage is an unresolved
        drawing set — both of which already meant exactly that."""
        assert set(source.EXTRACTION_INPUT_REFUSALS) == {
            "EXTRACTION_INPUT_REFUSED_NOT_A_MAPPING",
            "EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD",
            "EXTRACTION_INPUT_REFUSED_UNKNOWN_FIELD",
            "EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID",
        }
        assert not any(
            code.startswith("CONTINUATION_") for code in source.EXTRACTION_INPUT_REFUSALS
        )

    def test_the_three_routes_share_one_source_resolution(self):
        """One statement of the contract in the codebase, not three that could drift."""
        text = MAIN_PATH.read_text(encoding="utf-8")

        assert text.count("= _extraction_source(") == 3            # one per route
        assert text.count("parse_extraction_source_request(") == 1  # one parse
        assert text.count("resolve_extraction_source(") == 1        # one resolution

    def test_the_routes_dispatch_the_path_the_plan_proved(self):
        """The task downloads the path the PLAN proved, not the one the caller stated."""
        text = MAIN_PATH.read_text(encoding="utf-8")

        assert text.count('plan.drawing["storage_path"],') == 2
        assert "project_id, source.storage_path, document_id=source.document_id," in text
        assert "page_number=page, document_id=source.document_id," in text
        assert text.count("document_id=source.document_id,") == 4   # 2 plans, 2 dispatches


# ===========================================================================
# S. J63A — THE FIRST WINDOW REFUSES AN OMITTED ADDRESS IT WOULD HAVE TO CHOOSE.
#
# J63 left one case open. `/extract` did not consult the document table on an omitted
# request, so on a project holding TWO documents it started a NEW reading of
# `projects.uploaded_file_path` — which is not a default there, it is a choice among
# documents. Continuation and retry already refused that state; the first window did not.
#
# The rule this section proves is the contract's three cases and nothing else:
#
#     NO DOCUMENT     the pre-J63 request, and no document is invented for it
#     ONE DOCUMENT    the pre-J63 request, unchanged
#     TWO OR MORE     refused, with J16's own code and J16's own status
#
# The guard is the first window's own (`plan_first_window`), and an ADDRESSED request
# never reaches its decision: it is returned unchanged.
# ===========================================================================
def _two_document_project(store, *, project_id=PROJECT_ONE):
    """One project, two documents and two readings — the state that must not be chosen in.

    The two documents are the production write path's own rows (each created by the run
    that read it), and the project's column names ONE of them, which is the shape the
    forbidden shortcut would find easiest to take: a "pick the document matching
    `uploaded_file_path`" rule would resolve here, and the refusal below is what proves
    the boundary does not have one.
    """
    store.seed_project(project_id, storage_path=PATH_A)
    document_a = store.seed_document(project_id, document_id=DOC_A, storage_path=PATH_A)
    document_b = store.seed_document(project_id, document_id=DOC_B, storage_path=PATH_B)
    _reading(store, project_id, document=document_a, storage_path=PATH_A,
             page_count=DOCUMENT_PAGES)
    _reading(store, project_id, document=document_b, storage_path=PATH_B,
             page_count=DOCUMENT_PAGES)
    _record(store, project_id, total=DOCUMENT_PAGES, analysed=30)
    return document_a, document_b


def _first_window(production, store, project_id, *, document_id=None):
    """The two steps the `/extract` route takes, in the route's own order.

    Resolution first — the same call the route makes, over the same boundary — and then
    the first window's own guard. Nothing here is a transcription of the route: a change
    to either step is a change this file's route tests would see.
    """
    project = store.get_project(project_id)
    resolved = source.resolve_extraction_source(
        project_id, document_id=document_id, project=project, repository=store,
    )
    return production.pipeline.plan_first_window(project_id, resolved)


class TestSTheFirstWindowRefusesAnAmbiguousOmission:
    # --- CASE A: no document at all — the upload flow, preserved --------------
    def test_a_project_with_no_document_resolves_the_project_column(self, store, production):
        """The flow J61's rule creates: a file that is not a document row yet.

        The guard has nothing to count and nothing to refuse — and, crucially, nothing to
        INVENT: a project whose bytes have never been read keeps the source it always had.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.reads.clear()

        window = _first_window(production, store, PROJECT_ONE)

        assert window.storage_path == PATH_A
        assert window.document_id is None

    def test_a_project_with_no_document_is_not_given_one(self, store, production):
        """No write of any kind, and no document row for a source nobody read."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.reads.clear()
        store.order.clear()

        _first_window(production, store, PROJECT_ONE)

        assert store.documents == []
        assert store.order == []

    def test_the_route_answers_a_project_with_no_document_as_it_always_did(
        self, store, production, monkeypatch
    ):
        """With a column, the pre-J63 200; with none, the pre-J63 400."""
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        surface = _Http(production, monkeypatch, project_id=PROJECT_ONE,
                        project_row=store.projects[PROJECT_ONE])

        assert surface.http.post(surface.url("/extract/{project_id}")).status_code == 200
        name, args, kwargs = surface.only()
        assert args == (PROJECT_ONE, PATH_A, "PDF", store_user(surface))

        store.projects[PROJECT_ONE]["uploaded_file_path"] = None
        refused = surface.http.post(surface.url("/extract/{project_id}"))
        assert refused.status_code == 400
        assert refused.json()["detail"] == "Project has no uploaded file"

    # --- CASE B: exactly one document — the pre-J63 request, unchanged --------
    def test_a_single_document_project_is_the_pre_j63_request(self, store, production):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)

        window = _first_window(production, store, PROJECT_ONE)

        assert window.storage_path == PATH_A
        assert window.document_id is None
        assert window.addressed is False

    def test_one_document_is_not_a_choice_even_when_its_path_differs(self, store, production):
        """The compatibility path, stated as the fact it is.

        The pre-J63 source is the PROJECT'S COLUMN. A project holding one document whose
        stored path differs from that column is still read from the column under J63A, and
        this test says so rather than leaving it to be discovered: one document is not a
        choice, and J63A is forbidden from changing what a one-document project does.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_UNREAD)

        window = _first_window(production, store, PROJECT_ONE)

        assert window.storage_path == PATH_A      # the column, not the document's own path

    def test_two_lineages_of_one_document_are_not_two_sources(self, store, production):
        """The count is of DOCUMENTS. One document read twice is one source.

        The lineage ambiguity of that state belongs to the operations that ADD to a
        lineage (`_source_lineage`'s guard C); the first window starts a new one, so the
        project's source is as unambiguous here as it is anywhere else.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _reading(store, PROJECT_ONE, document=document, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)

        window = _first_window(production, store, PROJECT_ONE)

        assert window.storage_path == PATH_A

    # --- CASE C: two or more documents, none addressed — refused --------------
    def test_two_documents_and_no_address_are_refused_by_name(self, store, production):
        _two_document_project(store)

        with pytest.raises(ContinuationRefused) as refused:
            _first_window(production, store, PROJECT_ONE)

        code, detail = _refused(refused)
        assert code == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert "2 source documents" in detail
        assert "ambiguous" in detail

    def test_the_route_refuses_an_unaddressed_extract_and_queues_nothing(
        self, two_document_http
    ):
        """The gap J63 left, stated as the request that used to be answered with 200."""
        surface = two_document_http

        response = surface.http.post(surface.url("/extract/{project_id}"))

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert surface.dispatched == []

    def test_an_empty_body_is_refused_exactly_as_an_absent_one_is(self, two_document_http):
        """`{}` names no document, so on an ambiguous project it says what omission says."""
        surface = two_document_http

        response = surface.http.post(surface.url("/extract/{project_id}"), json={})

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED

    def test_the_project_column_is_not_what_the_refusal_is_measured_against(
        self, store, production
    ):
        """The forbidden shortcut, made unavailable in the two shapes it could take.

        First the column is made to AGREE with one of the two documents — the shape a
        "the document whose path is the project's file" rule would resolve — and then it
        is made to agree with NO document at all. Both are refused, so the refusal is a
        count of documents and not an agreement with anything.
        """
        _two_document_project(store)
        store.projects[PROJECT_ONE]["uploaded_file_path"] = PATH_B

        with pytest.raises(ContinuationRefused) as agreeing:
            _first_window(production, store, PROJECT_ONE)

        store.projects[PROJECT_ONE]["uploaded_file_path"] = PATH_UNREAD
        with pytest.raises(ContinuationRefused) as stranger:
            _first_window(production, store, PROJECT_ONE)

        # and with NO column at all: the ambiguity is answered before the pre-existing
        # "this project has no source" guard, because it does not depend on that column
        store.projects[PROJECT_ONE]["uploaded_file_path"] = None
        with pytest.raises(ContinuationRefused) as absent:
            _first_window(production, store, PROJECT_ONE)

        assert _refused(agreeing)[0] == _refused(stranger)[0]
        assert _refused(stranger)[0] == _refused(absent)[0]
        assert _refused(absent)[0] == CONTINUATION_DRAWING_SET_UNRESOLVED

    def test_the_refusal_is_answered_before_the_missing_source_guard(self, production,
                                                                    monkeypatch, store):
        """409 rather than 400 on a two-document project whose column is empty.

        Both answers are about this project, and the ambiguity is the one a caller has to
        fix: no document is named and there is more than one to name. The route reaches
        the guard before it reads the column for its 400.
        """
        _two_document_project(store)
        store.projects[PROJECT_ONE]["uploaded_file_path"] = None
        surface = _Http(production, monkeypatch, project_id=PROJECT_ONE,
                        project_row=store.projects[PROJECT_ONE])

        response = surface.http.post(surface.url("/extract/{project_id}"))

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert surface.dispatched == []

    def test_the_refused_request_reads_the_documents_and_nothing_else(self, two_document_http,
                                                                     store):
        """What the refusal cost: one read, no write, and the two routes unchanged."""
        surface = two_document_http
        store.reads.clear()
        store.order.clear()

        surface.http.post(surface.url("/extract/{project_id}"))

        assert store.reads == ["project_documents_for_project"], store.reads
        assert store.order == []

    def test_no_document_and_no_evidence_row_is_written_by_the_refusal(self, store, production
                                                                      ):
        """Nothing was created, updated or queued: the refusal is the whole of it."""
        _two_document_project(store)
        documents = list(store.documents)
        members = list(store.members)
        connections = list(store.connections)
        store.order.clear()
        store.reads.clear()

        with pytest.raises(ContinuationRefused):
            _first_window(production, store, PROJECT_ONE)

        assert store.documents == documents
        assert store.members == members
        assert store.connections == connections
        assert store.order == []
        # the project row (which the route is handed by its own authorization read) and
        # the project's documents (which is the whole of the guard's evidence)
        assert store.reads == ["get_project", "project_documents_for_project"]

    # --- CASE D and E: an addressed request is still resolved, and still alone --
    def test_the_addressed_document_of_a_two_document_project_is_selected(self, store,
                                                                          production):
        """CASE D, at the guard: a choice that WAS made is not ambiguous."""
        _two_document_project(store)

        first = _first_window(production, store, PROJECT_ONE, document_id=DOC_A)
        second = _first_window(production, store, PROJECT_ONE, document_id=DOC_B)

        assert (first.storage_path, first.document_id) == (PATH_A, DOC_A)
        assert (second.storage_path, second.document_id) == (PATH_B, DOC_B)
        assert first.addressed and second.addressed

    def test_each_addressed_document_is_dispatched_by_the_extract_route(self, production,
                                                                       monkeypatch, store):
        """CASE D, at the route: two requests, two documents, two dispatches."""
        _two_document_project(store)
        surface = _Http(production, monkeypatch, project_id=PROJECT_ONE,
                        project_row=store.projects[PROJECT_ONE])

        for document_id, path in ((DOC_A, PATH_A), (DOC_B, PATH_B)):
            surface.dispatched.clear()
            response = surface.http.post(
                surface.url("/extract/{project_id}"), json={"document_id": document_id},
            )

            assert response.status_code == 200, response.json()
            name, args, kwargs = surface.only()
            assert name == "run_extraction"
            assert args == (PROJECT_ONE, path, "PDF", store_user(surface))

    def test_a_document_of_another_project_is_still_refused(self, two_document_http):
        """CASE E, unchanged: the cross-project address never reaches the guard."""
        surface = two_document_http

        response = surface.http.post(
            surface.url("/extract/{project_id}"), json={"document_id": DOC_FOREIGN},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_SOURCE_MISMATCH
        assert surface.dispatched == []

    def test_a_malformed_address_is_still_422_on_an_ambiguous_project(self,
                                                                      two_document_http):
        """The request contract is answered before any persisted state is read."""
        surface = two_document_http

        response = surface.http.post(
            surface.url("/extract/{project_id}"), json={"document_id": ""},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["refusal"] == (
            "EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID"
        )
        assert surface.dispatched == []

    def test_a_server_owned_field_is_still_refused_by_name(self, two_document_http):
        """The client still may not state the source it is about to read."""
        surface = two_document_http

        response = surface.http.post(
            surface.url("/extract/{project_id}"), json={"storage_path": PATH_B},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["refusal"] == (
            "EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD"
        )
        assert surface.dispatched == []

    # --- the other two routes answer this state exactly as they did -----------
    def test_the_continuation_route_is_unchanged_by_this_milestone(self, two_document_http,
                                                                  store):
        """Same status, same code — and the same refusal it returned before J63A."""
        surface = two_document_http
        store.reads.clear()

        response = surface.http.post(surface.url("/continue-extraction/{project_id}"))

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert surface.dispatched == []
        # the continuation's own guard decided it, and it reads drawing sets rather than
        # documents: the omitted continuation is refused one layer down, as it always was
        assert "project_documents_for_project" not in store.reads

    def test_the_retry_route_is_unchanged_by_this_milestone(self, two_document_http):
        surface = two_document_http

        response = surface.http.post(surface.url("/retry-extraction/{project_id}?page=1"))

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert surface.dispatched == []

    def test_an_addressed_continuation_is_still_dispatched(self, two_document_http):
        """CASE D across the routes: J63's addressed continuation is untouched."""
        surface = two_document_http

        response = surface.http.post(
            surface.url("/continue-extraction/{project_id}"), json={"document_id": DOC_B},
        )

        assert response.status_code == 200, response.json()
        name, args, kwargs = surface.only()
        assert args[1] == PATH_B
        assert kwargs == {"document_id": DOC_B}


# ===========================================================================
# T. THE MILESTONE'S OWN SHAPE — ONE GUARD, ONE ROUTE, NO NEW VOCABULARY.
# ===========================================================================
class TestTTheGuardIsOneCallInOnePlace:
    def test_the_first_window_guard_is_called_by_the_extract_route_and_no_other(self):
        """`/extract` starts a reading; the other two extend one. One call, one branch."""
        text = MAIN_PATH.read_text(encoding="utf-8")

        assert text.count("plan_first_window(") == 1
        body = _function_source(MAIN_PATH, "extract")
        assert "plan_first_window(" in body
        for other in ("continue_extraction", "retry_extraction"):
            assert "plan_first_window(" not in _function_source(MAIN_PATH, other), other

    def test_the_guard_does_not_replace_the_shared_source_resolution(self):
        """An addressed request is still resolved by the boundary, and by nothing else."""
        text = MAIN_PATH.read_text(encoding="utf-8")

        assert text.count("= _extraction_source(") == 3
        assert text.count("resolve_extraction_source(") == 1

    def test_the_guard_added_no_refusal_code_and_no_new_vocabulary(self):
        """It raises a code J16 already publishes, and that code already means this.

        The REFUSAL is read off the module's own source rather than its prose: the code the
        `raise` names is the whole of what this milestone added to the vocabulary, and it
        is a name a caller could already branch on before J63A existed.
        """
        assert CONTINUATION_DRAWING_SET_UNRESOLVED in j16.REFUSAL_CODES
        body = _function_source(PIPELINE_PATH, "plan_first_window")
        raised = [
            node for node in ast.walk(ast.parse(body))
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        ]

        assert len(raised) == 1, raised
        assert ast.unparse(raised[0].exc.args[0]) == "CONTINUATION_DRAWING_SET_UNRESOLVED"
        assert CONTINUATION_SOURCE_MISMATCH not in body

    def test_the_guard_counts_documents_and_reads_nothing_else(self):
        """One read, of the project's own documents, through this module's own store."""
        body = _function_source(PIPELINE_PATH, "plan_first_window")
        tree = ast.parse(body)
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }

        assert "repo.project_documents_for_project" in calls
        assert len(calls) == 3, calls          # addressed, the read, the refusal's f-string
        assert "source.addressed" in body
        # and it chooses nothing: no ordering, no sorting, no comparison of documents
        for forbidden in ("sort", "sorted", "min(", "max(", "next(", "[0]"):
            assert forbidden not in body, forbidden

    def test_the_guard_refuses_only_when_there_is_more_than_one_document(self, production):
        """The boundary of the boundary: one allows, two refuses, and it is the same call."""
        assert "len(documents) > 1" in _function_source(PIPELINE_PATH, "plan_first_window")

    def test_a_path_that_creates_no_document_can_never_be_ambiguous(self):
        """Documents are created by the PDF reading and by nothing else.

        The guard counts `project_documents` rows, so an extraction path that writes none
        holds zero and is allowed on every request: a DXF project cannot reach the
        refusal, and J63A cannot change what any DXF extraction does. That is a property
        of where the document is written, and this test holds it there.
        """
        text = PIPELINE_PATH.read_text(encoding="utf-8")

        assert text.count("create_project_document(") == 1
        assert "create_project_document(" in _function_source(PIPELINE_PATH, "parse_pdf_and_save")
        assert "create_project_document(" not in _function_source(MAIN_PATH, "run_extraction")


def _function_source(path, name):
    """The source of one function in one module, as written."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} is not defined in {path}")


def _without_j64s_role_surface(tree):
    """`app/main.py`'s own tree, minus J64's endpoint and the import that binds it.

    J64 asserts a document's role deliberately, and it is a later milestone than this file.
    Removing exactly its one function and its one import lets this file's claim — that
    nothing in ITS milestones is role work — be checked over the whole of the application
    with one named exception, instead of the scan being narrowed until it stops looking.
    """
    kept = [
        node for node in tree.body
        if not (isinstance(node, ast.FunctionDef) and node.name == "production_document_role")
        and not (isinstance(node, ast.ImportFrom)
                 and (node.module or "").endswith("production_document_role"))
    ]
    return ast.Module(body=kept, type_ignores=[])


# ===========================================================================
# THE MUTATIONS — EVERY PROPERTY ABOVE STANDS ON SOMETHING THAT CAN FAIL.
# ===========================================================================
class TestTheMutations:
    def _two_lineages(self, store):
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        document_a = store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        document_b = store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
        _reading(store, PROJECT_ONE, document=document_a, storage_path=PATH_A,
                 page_count=DOCUMENT_PAGES)
        _reading(store, PROJECT_ONE, document=document_b, storage_path=PATH_B,
                 page_count=DOCUMENT_PAGES)
        _record(store, PROJECT_ONE, total=DOCUMENT_PAGES, analysed=30)

    def test_mutation_a_the_address_is_dropped_and_the_second_document_refuses(
        self, two_document_http, monkeypatch
    ):
        """The milestone's own headline, mutated.

        With the address dropped — the pre-J63 behaviour, restored deliberately — a
        request that names one of the project's two documents is refused because
        ANOTHER DOCUMENT EXISTS. That is the defect J63 closes, and section F's
        assertion is what goes red when the address is removed.
        """
        surface = two_document_http
        monkeypatch.setattr(
            surface.main, "_extraction_source",
            lambda project, body, **kwargs: source.resolve_extraction_source(
                project["id"], document_id=None, project=project,
                repository=source.repo,
            ),
        )

        response = surface.http.post(
            surface.url("/continue-extraction/{project_id}"), json={"document_id": DOC_B},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert surface.dispatched == []

    def test_mutation_b_the_id_filter_is_dropped_and_a_foreign_document_resolves(
        self, store
    ):
        """The filter mutated from "the row with this id" to "the first row".

        The cross-project refusal is section C's assertion, and it goes red the moment
        the id stops being looked for — which is what makes section C a proof about the
        filter rather than a proof about a message.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_project(PROJECT_TWO, storage_path=PATH_B)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
        store.seed_document(PROJECT_TWO, document_id=DOC_FOREIGN, storage_path=PATH_B)

        def mutated(project_id, *, document_id=None, project=None, repository=None):
            documents = store.project_documents_for_project(project_id)
            if not documents:
                raise ContinuationRefused(CONTINUATION_SOURCE_MISMATCH, "none")
            return source.SourceDocument(
                project_id=project_id,
                storage_path=documents[0].get("storage_path") or "",
                document_id=documents[0].get("id"),
            )

        resolved = mutated(PROJECT_ONE, document_id=DOC_FOREIGN,
                           project=store.get_project(PROJECT_ONE), repository=store)
        assert resolved.document_id == DOC_A     # the address was ignored entirely

        with pytest.raises(ContinuationRefused):
            source.resolve_extraction_source(
                PROJECT_ONE, document_id=DOC_FOREIGN,
                project=store.get_project(PROJECT_ONE), repository=store,
            )

    def test_mutation_c_the_address_is_ignored_by_the_lineage_lookup(
        self, store, monkeypatch
    , production):
        """The lineage filter mutated to the project-level one.

        Section F asserts the ADDRESSED document's lineage is planned. With the filter
        replaced by the pre-J63 one-and-only-one rule, that same request is refused —
        so the assertion was standing on the filter.
        """
        self._two_lineages(store)

        def pre_j63(project_id, project, storage_path, *, document_id, refuse):
            drawing_sets = store.drawing_sets_for_project(project_id)
            if len(drawing_sets) != 1:
                raise refuse(CONTINUATION_DRAWING_SET_UNRESOLVED, "ambiguous")
            drawing = store.drawings_for_drawing_set(drawing_sets[0]["id"])[0]
            return drawing_sets[0], drawing

        monkeypatch.setattr(production.pipeline, "_source_lineage", pre_j63)

        with pytest.raises(ContinuationRefused) as refused:
            production.pipeline.plan_continuation(PROJECT_ONE, PATH_B, document_id=DOC_B)

        assert _refused(refused)[0] == CONTINUATION_DRAWING_SET_UNRESOLVED

    def test_mutation_d_the_project_column_is_used_as_the_source(
        self, store, monkeypatch
    ):
        """Rule 2's forbidden behaviour, installed: the project column as the truth.

        Section B asserts the addressed document's own path is read. With the boundary
        resolving the project-level column instead, that assertion goes red — the second
        document becomes unreachable again, which is the state J63 exists to leave.
        """
        store.seed_project(PROJECT_ONE, storage_path=PATH_A)
        store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)

        def project_column(project_id, *, document_id=None, project=None, repository=None):
            project = project or store.get_project(project_id)
            return source.SourceDocument(
                project_id=project_id,
                storage_path=project.get("uploaded_file_path") or "",
                document_id=document_id,
            )

        resolved = project_column(
            PROJECT_ONE, document_id=DOC_B,
            project=store.get_project(PROJECT_ONE), repository=store,
        )
        assert resolved.storage_path == PATH_A     # not the addressed document's file

        genuine = source.resolve_extraction_source(
            PROJECT_ONE, document_id=DOC_B,
            project=store.get_project(PROJECT_ONE), repository=store,
        )
        assert genuine.storage_path == PATH_B

    def test_mutation_e_a_malformed_address_is_coerced(self, monkeypatch):
        """A half-stated address accepted as if it were one.

        Section D asserts a value that is not a name is refused. With the value coerced
        to a string, that assertion goes red — and a caller who had not chosen would
        have been given a document anyway.
        """
        def coercing(body):
            if body is None:
                return None
            value = body.get("document_id") if isinstance(body, dict) else None
            return None if value is None else str(value)

        assert coercing({"document_id": 1}) == "1"
        with pytest.raises(source.ExtractionInputRefused):
            source.parse_extraction_source_request({"document_id": 1})

    def test_mutation_f_the_request_surface_grows_a_server_owned_field(self, monkeypatch):
        """A caller allowed to state the source it is about to read.

        The contract is a contract because ONE field is accepted and every other name is
        refused — `storage_path` among them, by name. Widen the accepted set and drop the
        name from the refused set and a caller can state the bytes its run will read:
        section R's assertion that the surface is exactly `document_id` is what goes red.
        """
        monkeypatch.setattr(source, "REQUEST_FIELDS", ("document_id", "storage_path"))
        monkeypatch.setattr(
            source, "SERVER_OWNED_FIELDS",
            tuple(name for name in source.SERVER_OWNED_FIELDS if name != "storage_path"),
        )

        accepted = source.parse_extraction_source_request(
            {"storage_path": PATH_B, "document_id": DOC_A},
        )
        assert accepted == DOC_A
        # and the field is no longer refused at all: a body naming a source path is
        # taken as a request rather than answered with the name of the field it may not send
        assert source.parse_extraction_source_request({"storage_path": PATH_B}) is None

    def test_mutation_g_the_first_window_guard_is_dropped(
        self, production, monkeypatch, store
    ):
        """J63A's own headline, mutated: the omission chosen for the caller again.

        With the guard removed — the J63 behaviour, restored deliberately — the same
        request section S refuses is answered 200 and queues a reading of the PROJECT'S
        OWN COLUMN, on a project holding two documents. That is the gap, and section S's
        route assertion is what goes red when the guard is taken away.
        """
        _two_document_project(store)
        surface = _Http(production, monkeypatch, project_id=PROJECT_ONE,
                        project_row=store.projects[PROJECT_ONE])
        monkeypatch.setattr(surface.main, "plan_first_window", lambda project_id, source: source)

        response = surface.http.post(surface.url("/extract/{project_id}"))

        assert response.status_code == 200
        assert surface.only()[1][1] == PATH_A       # the column, chosen silently

        with pytest.raises(ContinuationRefused):
            _first_window(production, store, PROJECT_ONE)

    def test_mutation_h_the_guard_chooses_the_document_matching_the_project_column(
        self, production, monkeypatch, store
    ):
        """The shortcut the brief forbids by name, installed and shown to be reachable.

        "The document matching `uploaded_file_path`" is a rule that would produce a source
        here rather than a refusal — so the refusal is a decision this milestone made, not
        a state the data forces. With the column naming document A, the mutated guard hands
        back A's path; the genuine guard refuses the same request, because on a project
        holding two documents the column is a choice among sources and not a source.
        """
        _two_document_project(store)
        project = store.get_project(PROJECT_ONE)
        assert project["uploaded_file_path"] == PATH_A      # the column names one of them

        def by_column(project_id, source):
            if source.addressed:
                return source
            for row in store.project_documents_for_project(project_id):
                if row.get("storage_path") == project.get("uploaded_file_path"):
                    return source.__class__(
                        project_id=project_id,
                        storage_path=row["storage_path"],
                        document_id=row["id"],
                    )
            return source

        chosen = by_column(PROJECT_ONE, source.SourceDocument(PROJECT_ONE, PATH_A))
        assert (chosen.storage_path, chosen.document_id) == (PATH_A, DOC_A)

        with pytest.raises(ContinuationRefused) as refused:
            _first_window(production, store, PROJECT_ONE)

        assert _refused(refused)[0] == CONTINUATION_DRAWING_SET_UNRESOLVED


# ===========================================================================
# SCOPE AND PURITY — WHAT THIS FILE DOES NOT CLAIM.
# ===========================================================================
class TestScopeAndPurity:
    def test_no_live_client_and_no_storage_read_is_reachable_from_this_file(self):
        """No row of the live database is named anywhere in this file.

        Every id-shaped literal here is one this file defines; none is a live project, a
        live document or a live reviewer. The only client these tests ever see is the one
        J16's own fixture installs for the matcher's reference read, over the pinned index.
        """
        text = pathlib.Path(__file__).read_text(encoding="utf-8")
        # Assembled rather than written, so that this test's own statement of the rule is
        # not itself an instance of what it forbids.
        live_project = "".join(("2389", "c115"))

        assert live_project not in text, "a live project is named by this file"
        found = set(re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", text,
        ))
        assert found == {
            DOC_A, DOC_B, DOC_FOREIGN, DOC_ABSENT, PROJECT_ONE, PROJECT_TWO, OWNER,
            OTHER_OWNER,
        }, found

    def test_the_modules_under_test_construct_no_client_and_read_no_bucket(self):
        """The milestone's whole surface: three modules, none of which reaches out."""
        for path in (SOURCE_PATH, PIPELINE_PATH, MAIN_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            attributes = {node.attr for node in ast.walk(tree)
                          if isinstance(node, ast.Attribute)}
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

            assert "create_client" not in names | attributes, path.name
            assert "storage" not in names, path.name

    def test_the_source_module_is_importable_and_inert(self):
        """Importing it performs no work: no module-level call at all."""
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
        module_level_calls = [
            node for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]

        assert module_level_calls == [], "the module calls something at import time"

    def test_the_boundary_refuses_by_code_and_statement_only(self):
        """The request refusal carries a code and a statement, like J47's and J50's."""
        refused = source.ExtractionInputRefused("A_CODE", "a statement")

        assert refused.code == "A_CODE"
        assert refused.statement == "a statement"
        assert isinstance(refused, ValueError)

    def test_no_role_is_assigned_or_inferred_anywhere_in_this_milestone(self):
        """The role column is J61's, and J61 left it UNKNOWN; nothing here is a role work.

        The check is over IDENTIFIERS and KEYWORDS rather than over the modules' text,
        because the boundary's own prose says "no role is assigned" and "no transmittal is
        parsed" — a sentence refusing a thing is not the thing. What must not exist is a
        NAME, an ATTRIBUTE or a KEYWORD that could carry one.

        `app/main.py` is checked with ONE declared exception: J64's document-role endpoint
        and its import, which are a later milestone's role work and are named here rather
        than filtered away. Everything else in the application — every extraction, review,
        opening and reconstruction surface — is still held to the whole of the rule, so the
        exception is exactly one handler wide.
        """
        forbidden = {"role", "revision_label", "supersedes_document_id", "transmittal",
                     "document_role", "DOCUMENT_ROLE"}

        for path in (SOURCE_PATH, PIPELINE_PATH, MAIN_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if path == MAIN_PATH:
                tree = _without_j64s_role_surface(tree)

            vocabulary = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            vocabulary |= {node.attr for node in ast.walk(tree)
                           if isinstance(node, ast.Attribute)}
            vocabulary |= {keyword.arg for node in ast.walk(tree)
                           if isinstance(node, ast.Call) for keyword in node.keywords}

            assert not (vocabulary & forbidden), (path.name, vocabulary & forbidden)

    def test_the_exception_this_file_makes_is_one_handler_and_nothing_more(self):
        """The scan above exempts J64's endpoint. This asserts the exemption is exactly
        that: one function named `production_document_role` and one import from the module
        it lives in, with the rest of `app/main.py` untouched."""
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        stripped = _without_j64s_role_surface(tree)

        removed_functions = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        } - {node.name for node in ast.walk(stripped) if isinstance(node, ast.FunctionDef)}
        removed_imports = [
            ast.unparse(node) for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").endswith("production_document_role")
        ]
        kept_imports = [
            ast.unparse(node) for node in stripped.body if isinstance(node, ast.ImportFrom)
        ]

        assert removed_functions == {"production_document_role"}
        assert len(removed_imports) == 1
        assert not [name for name in kept_imports if "document_role" in name]
        # Exactly two statements: the import and the handler. Nothing else in the module
        # was set aside for J64's sake.
        assert len(stripped.body) == len(tree.body) - 2

    def test_no_per_document_coverage_and_no_document_set_entity_is_introduced(self):
        code = ast.unparse(ast.parse(PIPELINE_PATH.read_text(encoding="utf-8")))

        assert "per_document" not in code
        assert "document_set" not in code
        assert "document_coverage" not in code

    def test_the_milestone_adds_no_migration_and_no_column(self):
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
        code = ast.unparse(tree)

        for forbidden in ("alter table", "create table", "supabase/migrations"):
            assert forbidden.lower() not in code.lower()
