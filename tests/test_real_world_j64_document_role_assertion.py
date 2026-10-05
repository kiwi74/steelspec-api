"""
J64 — EXPLICIT PROJECT DOCUMENT ROLE ASSERTION.

WHAT THIS FILE IS

The proof of `POST /production/review/{project_id}/document-role`: that an authenticated
project owner can say what one of their own project's source documents IS, that nobody else
can, that only the stored vocabulary is accepted, that asserting a role changes nothing else
in the system, and that no part of the system acts on a role.

    PROJECT
      └── PROJECT_DOCUMENT  ← one asserted role, or UNKNOWN

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the request contract and every one of its refusals
              (`app.production_document_role.py`), the vocabulary itself — read from
              `repository.DOCUMENT_ROLES`, the same list J61's live CHECK constraint
              enforces — the repository write (compiled from its own source, including in
              the mutation that removes its project filter), the route and its HTTP status
              mapping, the J19 identity and authorization boundary (genuine ES256 tokens
              against an in-process JWKS document), J63's source resolver and J63A's
              first-window guard as the pipeline applies them, J61's read model, and J61's
              own live table, read read-only in section Q.

    DOUBLED   the database: where a `project_documents` row goes and what a read serves
              back. The double applies the SAME pair — id and project — that the
              production repository function filters by, because a double that accepted a
              cross-project write would let a mutation the real store refuses look
              successful here. Every double RECORDS what it was asked, so "this step did
              not run" is asserted over a call that could have happened.

WHAT THE MUTATIONS ARE

They are not extra tests. Each removes the thing that protects one asserted property and
shows the assertion goes red: the write's project filter (against the real repository
function with that one term removed), the operation's project-scoped read, and the
vocabulary check. A guard that cannot fail is not a guard.

WHAT THIS FILE DOES NOT CLAIM

It writes no live row: no live document's role is asserted, no live project is touched, no
extraction is run, and nothing is ingested. It assigns no role by inference — there is no
filename, extension, page-count, path or ordering rule anywhere in this milestone — it
reconciles nothing, cites no field, parses no transmittal, and adds no migration.
"""
from __future__ import annotations

import ast
import copy
import pathlib
import types

import pytest

import app.production_document_role as role_boundary
from app.engineering_data import repository as store_module
from app.production_document_role import (
    DOCUMENT_ROLES,
    DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID,
    DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING,
    DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID,
    DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD,
    DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD,
    DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN,
    DOCUMENT_ROLE_REFUSED_NOT_RECORDED,
    DOCUMENT_ROLE_UNKNOWN,
    DocumentRoleInputRefused,
    DocumentRoleRefused,
    assert_document_role,
    parse_document_role_request,
)

from tests import production_review_auth as auth
from tests import test_real_world_j20_connection_review_persistence_gap as j20
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j50_production_review_opening as j50
from tests import test_real_world_j61_project_document_identity as j61

#: J4's own module-scoped fixture, aliased so this file's tests can take it as a parameter
#: exactly as J19/J25/J47/J50/J63's do. It is a fixture and NOT a module: nothing at this
#: file's import time may touch `production.main`.
production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "production_document_role.py"
MAIN_PATH = REPO / "app" / "main.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"
PIPELINE_PATH = REPO / "app" / "pipeline.py"
SOURCE_BOUNDARY_PATH = REPO / "app" / "production_extraction_source.py"
IDENTITY_PATH = REPO / "app" / "production_review" / "identity.py"
PROJECT_READ_PATH = REPO / "app" / "production_review" / "project_read.py"
J47_TEST_PATH = REPO / "tests" / "test_real_world_j47_production_review_route.py"
J61_MIGRATION = REPO / "supabase" / "migrations" / "20260929000000_j61_project_documents.sql"

ROUTE_PATH = "/production/review/{project_id}/document-role"
OPEN_ROUTE_PATH = "/production/review/{project_id}/open"

PROJECT_ONE = "11111111-2222-4333-8444-555555555555"
PROJECT_TWO = "22222222-3333-4444-8555-666666666666"
PROJECT_ABSENT = "33333333-4444-4555-8666-777777777777"
OWNER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_OWNER = "99999999-8888-4777-8666-555555555555"

DOC_A = "aaaaaaaa-0000-4000-8000-00000000000a"
DOC_B = "bbbbbbbb-0000-4000-8000-00000000000b"
DOC_C = "cccccccc-0000-4000-8000-00000000000c"
DOC_FOREIGN = "ffffffff-0000-4000-8000-00000000000f"
DOC_ABSENT = "00000000-0000-4000-8000-000000000000"

PATH_A = "j64-owner/drawings-a.pdf"
PATH_B = "j64-owner/drawings-b.pdf"
HASH_A = "a" * 64

#: The four role names J64's brief lists that this system has never stored: no column, no
#: constraint, no constant and no code has ever held them, so they are refused exactly as
#: any other out-of-vocabulary string is. Naming them here is the honest statement of the
#: gap rather than a quiet omission.
ROLES_THIS_SYSTEM_DOES_NOT_STORE = (
    "STRUCTURAL_DETAIL",
    "ENGINEERING_SCHEDULE",
    "GENERAL_NOTES",
    "OTHER",
)

#: The five roles the milestone's reference package (Selby) needs, used only as a
#: REPRESENTATIONAL test target: a transmittal, GAs, assembly drawings, fabrication
#: drawings and isometrics.
SELBY_ROLES = ("TRANSMITTAL", "STRUCTURAL_GA", "ASSEMBLY", "FABRICATION", "ISOMETRIC")

#: Every column of a `project_documents` row. The operation may read exactly two of them —
#: the document's id and its role — and section N asserts that from the source.
DOCUMENT_COLUMN_NAMES = (
    "id",
    "project_id",
    "storage_path",
    "file_name",
    "source_format",
    "byte_size",
    "page_count",
    "content_sha256",
    "role",
    "revision_label",
    "supersedes_document_id",
    "created_at",
)

#: The attributes the whole module is allowed to touch, pinned. This is the strongest
#: statement in this file that no inference machinery is present: a rule that read a
#: filename, a path, a page count, an ordering or a model output would have to appear in
#: this set, and it does not.
MODULE_ATTRIBUTES = {
    "DOCUMENT_ROLES",
    "DOCUMENT_ROLE_UNKNOWN",
    "__init__",
    "__name__",
    "changed",
    "code",
    "dataclass",
    "document_id",
    "get",
    "previous_role",
    "project_documents_for_project",
    "project_id",
    "role",
    "statement",
    "strip",
    "update_document_role",
}

#: Every call the module makes, pinned on the same terms.
MODULE_CALLS = {
    "DocumentRoleAssertion",
    "DocumentRoleInputRefused",
    "DocumentRoleRefused",
    "DocumentRoleRequest",
    "__init__",
    "dataclass",
    "get",
    "isinstance",
    "len",
    "list",
    "project_documents_for_project",
    "sorted",
    "strip",
    "super",
    "type",
    "update_document_role",
}

#: Everything this module must not be able to call: the extraction pipeline, the reporting
#: and artifact machinery, the fabrication dispatch, the review surfaces, the storage
#: client, hashing and file access.
FORBIDDEN_CALLS = {
    "run_extraction",
    "parse_pdf_and_save",
    "continue_pdf_extraction",
    "retry_pdf_page",
    "plan_first_window",
    "plan_continuation",
    "resolve_extraction_source",
    "build_review_snapshot",
    "acquire_project_review_claim",
    "release_project_review_claim",
    "resolve_project_connection",
    "dispatch_fabrication_drawing",
    "verify_drawing_artifact",
    "record_fabrication_pointer",
    "generate_report_pdf",
    "create_project_document",
    "update_document_page_count",
    "storage",
    "sha256",
    "md5",
    "open",
}

#: The tables this milestone may not name, as the table names appear in SQL or in a client
#: call. `project_documents` is the only table a role assertion touches.
FORBIDDEN_TABLES = (
    "steel_members",
    "connection_review_snapshots",
    "page_extraction_captures",
    "analysis_runs",
    "fabrication_artifacts",
    "drawing_sets",
    "review_items",
)


# ===========================================================================
# The doubles. Every one of them RECORDS what it was asked.
# ===========================================================================
def _projected(row):
    """One document row, in the columns the production read names and no others."""
    return {name: copy.deepcopy(row.get(name)) for name in DOCUMENT_COLUMN_NAMES}


class _Store:
    """`project_documents`, doubled — with the production write's own pair filter.

    The three functions this milestone calls are J61's project read, J61's document read
    and J64's write, and nothing else is served: a call to any other name is an
    AttributeError rather than a silent answer. `update_document_role` applies the SAME
    scope the production repository function filters by — the document's id AND its
    project — so a cross-project write that the real store refuses cannot succeed here.
    """

    def __init__(self):
        self.documents: list[dict] = []
        self.projects: dict[str, dict] = {}
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, str]] = []

    # -- seeding ------------------------------------------------------------------
    def seed_project(self, project_id, *, owner=OWNER, uploaded_file_path=PATH_A):
        row = {
            "id": project_id,
            "user_id": owner,
            "status": "review",
            "uploaded_file_path": uploaded_file_path,
            "source_format": "PDF",
            "warnings": [],
        }
        self.projects[project_id] = row
        return row

    def seed_document(self, project_id, *, document_id, storage_path=PATH_A,
                      file_name=None, role=DOCUMENT_ROLE_UNKNOWN, content_sha256=None,
                      page_count=41):
        """A `project_documents` row as J61 leaves it: UNKNOWN, until someone says."""
        row = {
            "id": document_id,
            "project_id": project_id,
            "storage_path": storage_path,
            "file_name": file_name or storage_path.rsplit("/", 1)[-1],
            "source_format": "PDF",
            "byte_size": 1024,
            "page_count": page_count,
            "content_sha256": content_sha256,
            "role": role,
            "revision_label": None,
            "supersedes_document_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        self.documents.append(row)
        return row

    # -- the calls this milestone makes -------------------------------------------
    def get_project(self, project_id):
        self.reads.append("get_project")
        return self.projects.get(project_id)

    def project_documents_for_project(self, project_id):
        self.reads.append("project_documents_for_project")
        return [
            _projected(row) for row in self.documents if row.get("project_id") == project_id
        ]

    def update_document_role(self, project_id, document_id, role):
        """J64's write, with the production pair filter. Records EVERY call."""
        self.writes.append((project_id, document_id, role))
        for row in self.documents:
            if row["id"] == document_id and row["project_id"] == project_id:
                row["role"] = role
                return _projected(row)
        return None

    # -- assertions the tests make about what happened -----------------------------
    def row(self, document_id):
        for row in self.documents:
            if row["id"] == document_id:
                return row
        raise AssertionError(f"no document {document_id!r} was seeded")


class _Http:
    """The real application, the real route, and one authenticated client per caller.

    The project load is `main.supabase` (doubled, and refusing to serve any table but
    `projects`); the document store is the module's own `repo` seam (doubled above). Both
    are the seams production uses, which is why the route under test is the route.
    """

    def __init__(self, production_module, monkeypatch, store, *, owner=OWNER):
        self.main = production_module.main
        self.store = store
        self.client = j50._ProjectsClient(store.projects)
        monkeypatch.setattr(self.main, "supabase", self.client)
        monkeypatch.setattr(role_boundary, "repo", store)
        tokens = auth.install(monkeypatch, production_module)
        from fastapi.testclient import TestClient

        self.tokens = tokens
        self.owner = owner
        self.http = TestClient(self.main.app, headers=tokens.headers(owner))
        self.other = TestClient(self.main.app, headers=tokens.headers(OTHER_OWNER))
        self.bare = TestClient(self.main.app)

    def post(self, body=None, *, project_id=PROJECT_ONE, client=None):
        client = client if client is not None else self.http
        return client.post(ROUTE_PATH.replace("{project_id}", project_id), json=body)

    def refusal(self, response):
        assert response.status_code in (401, 403, 409, 422), response.text
        return response.json()["detail"]["refusal"], response.json()["detail"]["reason"]


@pytest.fixture()
def store():
    return _Store()


@pytest.fixture()
def two_projects(store):
    """Two projects, one document each, both UNKNOWN — and nothing else seeded."""
    store.seed_project(PROJECT_ONE)
    store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
    store.seed_project(PROJECT_TWO, owner=OTHER_OWNER, uploaded_file_path=PATH_B)
    store.seed_document(PROJECT_TWO, document_id=DOC_FOREIGN, storage_path=PATH_B)
    return store


@pytest.fixture()
def one_document(two_projects):
    """The single-document project, which is the ordinary case."""
    return two_projects


@pytest.fixture()
def two_documents(store):
    """One project, two documents: the shape J63A refuses to choose between."""
    store.seed_project(PROJECT_ONE)
    store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
    store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B)
    return store


@pytest.fixture()
def http(production, monkeypatch, two_projects):
    """The route, over the two-project store: the caller owns one of them."""
    return _Http(production, monkeypatch, two_projects)


@pytest.fixture()
def http_no_documents(production, monkeypatch, store):
    """A real, owned project that holds no document at all."""
    store.seed_project(PROJECT_ONE)
    return _Http(production, monkeypatch, store)


@pytest.fixture(scope="module")
def live_client(production):
    """The production Supabase client, used for SELECTs and nothing else (J61's own)."""
    if not production.live_config:
        pytest.skip("no live Supabase configuration is available")
    return production.matcher_module.supabase


# ===========================================================================
# Source helpers — the milestone's own code, read as text.
# ===========================================================================
def _module_tree(path=MODULE_PATH):
    return ast.parse(path.read_text())


def _function_node(path, name):
    for node in _module_tree(path).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined at module level in {path.name}")


def _function_source(path, name):
    """One module-level function, unparsed, docstring included."""
    return ast.unparse(_function_node(path, name))


def _function_body(path, name):
    """The same function with its own docstring removed: the CODE, and only the code."""
    node = _function_node(path, name)
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _code_strings(path, name=None):
    """Every string literal the module's CODE carries — docstrings excluded.

    Prose may name anything it likes; a literal is a value the code can produce, so this is
    where a restated vocabulary or a parsing rule would have to appear.
    """
    tree = _module_tree(path) if name is None else _function_node(path, name)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and id(n) not in docstrings}


def _attribute_names(node):
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _called_names(node):
    names = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _imported_modules(node):
    imported = set()
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom) and child.module:
            imported.add(child.module)
        elif isinstance(child, ast.Import):
            imported.update(alias.name for alias in child.names)
    return imported


def _importers_of(module_name):
    """Every module under `app/` that actually imports `module_name`, by statement."""
    importers = []
    for path in sorted((REPO / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.endswith(module_name):
                    importers.append(path.name)
                    break
            if isinstance(node, ast.Import):
                if any(alias.name.endswith(module_name) for alias in node.names):
                    importers.append(path.name)
                    break
    return sorted(importers)


def _refused(exception_info):
    return exception_info.value.code, exception_info.value.statement


# ===========================================================================
# The repository write, and its own instrument: the SAME function with its one
# project filter removed.
# ===========================================================================
def _repository_function(*, project_filter=True):
    """The REAL `update_document_role`, compiled from its own source text.

    With `project_filter=False` the single `.eq('project_id', project_id)` term is removed
    and nothing else changes.
    """
    source = _function_source(REPOSITORY_PATH, "update_document_role")
    if not project_filter:
        source = source.replace(".eq('project_id', project_id)", "", 1)
        assert source != _function_source(REPOSITORY_PATH, "update_document_role")
    namespace: dict = {}
    exec("from __future__ import annotations\n" + source, namespace)
    return namespace["update_document_role"]


class _FakeTableQuery:
    """One `project_documents` statement, against a list of rows."""

    def __init__(self, rows):
        self.rows = rows
        self.values = None
        self.filters: list[tuple[str, object]] = []

    def update(self, values):
        self.values = values
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        matched = [row for row in self.rows
                   if all(row.get(column) == value for column, value in self.filters)]
        if self.values is None:
            return types.SimpleNamespace(data=list(matched))
        for row in matched:
            row.update(self.values)
        return types.SimpleNamespace(data=[dict(row) for row in matched])


class _FakeSupabase:
    """`supabase`, for the one table the repository write names — and no other."""

    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "project_documents", name
        return _FakeTableQuery(self.rows)


# ===========================================================================
# A. A DOCUMENT STARTS UNKNOWN, AND STAYS THAT WAY UNTIL SOMETHING SAYS OTHERWISE.
# ===========================================================================
class TestADocumentStartsUnknown:
    def test_a_seeded_document_is_unknown_and_a_read_changes_nothing(self, one_document):
        assert one_document.row(DOC_A)["role"] == DOCUMENT_ROLE_UNKNOWN

        first = one_document.project_documents_for_project(PROJECT_ONE)
        second = one_document.project_documents_for_project(PROJECT_ONE)

        assert [row["role"] for row in first] == [DOCUMENT_ROLE_UNKNOWN]
        assert first == second
        assert one_document.writes == []

    def test_reading_the_documents_does_not_write_a_role(self, one_document):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="FABRICATION",
                             repository=one_document)

        assert one_document.reads == ["project_documents_for_project"]
        assert one_document.writes == [(PROJECT_ONE, DOC_A, "FABRICATION")]

    def test_an_assertion_leaves_every_other_document_exactly_as_it_was(self, two_documents):
        before = copy.deepcopy(two_documents.row(DOC_B))

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="STRUCTURAL_GA",
                             repository=two_documents)

        assert two_documents.row(DOC_B) == before
        assert two_documents.row(DOC_A)["role"] == "STRUCTURAL_GA"

    def test_an_untouched_project_keeps_every_document_unknown(self, two_projects):
        """Nothing ran: the state the caller finds is the state J61 left."""
        assert [row["role"] for row in two_projects.documents] == [DOCUMENT_ROLE_UNKNOWN] * 2

    def test_unknown_is_a_member_of_the_vocabulary(self):
        assert DOCUMENT_ROLE_UNKNOWN == "UNKNOWN"
        assert DOCUMENT_ROLE_UNKNOWN in DOCUMENT_ROLES
        assert len(set(DOCUMENT_ROLES)) == len(DOCUMENT_ROLES)

    def test_the_vocabulary_is_the_repositorys_own_and_not_a_second_list(self):
        """One list, read from the module that already declared it. A second copy is how a
        schema and its code come to disagree."""
        assert DOCUMENT_ROLES is store_module.DOCUMENT_ROLES
        assert DOCUMENT_ROLE_UNKNOWN == store_module.DOCUMENT_ROLE_UNKNOWN


# ===========================================================================
# B. A VALID ASSERTION SUCCEEDS.
# ===========================================================================
class TestBAnAssertionSucceeds:
    def test_the_operation_records_the_role_and_reports_the_change(self, one_document):
        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A,
                                         role="STRUCTURAL_GA", repository=one_document)

        assert assertion.project_id == PROJECT_ONE
        assert assertion.document_id == DOC_A
        assert assertion.role == "STRUCTURAL_GA"
        assert assertion.previous_role == DOCUMENT_ROLE_UNKNOWN
        assert assertion.changed is True

    def test_the_row_itself_now_carries_the_role(self, one_document):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ASSEMBLY",
                             repository=one_document)

        assert one_document.row(DOC_A)["role"] == "ASSEMBLY"

    def test_exactly_one_write_was_made_and_it_named_the_pair(self, one_document):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ISOMETRIC",
                             repository=one_document)

        assert one_document.writes == [(PROJECT_ONE, DOC_A, "ISOMETRIC")]

    def test_the_route_records_the_role_and_answers_with_the_facts(self, http):
        response = http.post({"document_id": DOC_A, "role": "FABRICATION"})

        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "role_asserted",
            "project_id": PROJECT_ONE,
            "document_id": DOC_A,
            "role": "FABRICATION",
            "previous_role": DOCUMENT_ROLE_UNKNOWN,
            "changed": True,
        }
        assert http.store.row(DOC_A)["role"] == "FABRICATION"

    def test_the_route_reads_the_projects_table_and_no_other_table_itself(self, http):
        http.post({"document_id": DOC_A, "role": "ASSEMBLY"})

        assert http.client.tables_read == ["projects"]

    def test_the_reported_role_is_the_stores_answer_and_not_the_argument(
        self, store, monkeypatch
    ):
        """A store that records something else is reported as having recorded something
        else: the answer is read back, never assumed."""
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A)

        def lying_write(project_id, document_id, role):
            store.writes.append((project_id, document_id, role))
            return _projected({**store.row(document_id), "role": "DETAIL"})

        monkeypatch.setattr(store, "update_document_role", lying_write)

        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ASSEMBLY",
                                 repository=store)

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_REFUSED_NOT_RECORDED
        assert "write was attempted" in statement
        assert "is not reported as a success" in statement
        assert "nothing here retries or undoes it" in statement

    def test_a_store_that_records_nothing_is_refused_not_reported_as_success(
        self, one_document, monkeypatch
    ):
        monkeypatch.setattr(one_document, "update_document_role", lambda *a, **k: None)

        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ASSEMBLY",
                                 repository=one_document)

        assert _refused(caught)[0] == DOCUMENT_ROLE_REFUSED_NOT_RECORDED

    def test_a_store_that_answers_with_the_row_it_already_had_is_refused_too(
        self, one_document, monkeypatch
    ):
        """The store must report the ASSERTED role. One that echoes the old row has not
        recorded the assertion, whatever else it did."""
        def stale_write(project_id, document_id, role):
            one_document.writes.append((project_id, document_id, role))
            return _projected(one_document.row(document_id))

        monkeypatch.setattr(one_document, "update_document_role", stale_write)

        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ASSEMBLY",
                                 repository=one_document)

        assert _refused(caught)[0] == DOCUMENT_ROLE_REFUSED_NOT_RECORDED


# ===========================================================================
# C. EVERY STORED ROLE IS ACCEPTED — AND THE LIST IS EXACTLY THE DATABASE'S.
# ===========================================================================
class TestCEveryStoredRoleIsAccepted:
    @pytest.mark.parametrize("role", DOCUMENT_ROLES)
    def test_the_operation_accepts_it(self, role):
        store = _Store()
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A)

        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A, role=role,
                                         repository=store)

        assert assertion.role == role
        assert store.row(DOC_A)["role"] == role

    @pytest.mark.parametrize("role", DOCUMENT_ROLES)
    def test_the_route_accepts_it(self, role, production, monkeypatch):
        store = _Store()
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A)
        surface = _Http(production, monkeypatch, store)

        response = surface.post({"document_id": DOC_A, "role": role})

        assert response.status_code == 200, response.text
        assert response.json()["role"] == role

    @pytest.mark.parametrize("role", DOCUMENT_ROLES)
    def test_the_parser_accepts_it(self, role):
        request = parse_document_role_request({"document_id": DOC_A, "role": role})

        assert request.role == role
        assert request.document_id == DOC_A

    def test_the_vocabulary_is_the_one_j61s_constraint_declares(self):
        """The live CHECK and this module's list are the same list, read from one place.

        The migration is the record of what the database enforces; `DOCUMENT_ROLES` is what
        this code accepts. A value in one and not the other is the defect this test exists
        to catch.
        """
        import re

        sql = J61_MIGRATION.read_text()
        match = re.search(r"check \(role in \(\s*([^)]*)\)\)", sql, re.S)
        assert match, "the role vocabulary is no longer declared as a CHECK"

        assert list(DOCUMENT_ROLES) == re.findall(r"'([A-Z_]+)'", match.group(1))

    def test_the_five_reference_package_roles_are_all_members(self):
        """The milestone's representational target — a transmittal, GAs, assembly,
        fabrication and isometric drawings — is expressible in the stored vocabulary."""
        for role in SELBY_ROLES:
            assert role in DOCUMENT_ROLES, role

    def test_no_role_the_system_has_never_stored_is_quietly_accepted(self):
        for role in ROLES_THIS_SYSTEM_DOES_NOT_STORE:
            assert role not in DOCUMENT_ROLES, role


# ===========================================================================
# D. AN INVALID ROLE IS REFUSED, DETERMINISTICALLY AND BY NAME.
# ===========================================================================
class TestDAnInvalidRoleIsRefused:
    @pytest.mark.parametrize("role", ROLES_THIS_SYSTEM_DOES_NOT_STORE)
    def test_a_role_this_system_does_not_store_is_refused(self, role):
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": DOC_A, "role": role})

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID
        assert repr(role) in statement

    @pytest.mark.parametrize("role", ("fabrication", "Fabrication", " fabrication",
                                      "FABRICATION ", "", "   ", None, 7, ["FABRICATION"],
                                      True))
    def test_a_role_that_is_not_a_stored_value_is_refused(self, role):
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": DOC_A, "role": role})

        assert _refused(caught)[0] == DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID

    def test_an_absent_role_is_refused(self):
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": DOC_A})

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID
        assert "asserts nothing" in statement

    def test_a_body_that_is_not_an_object_is_refused(self):
        for body in (["FABRICATION"], "FABRICATION", 7):
            with pytest.raises(DocumentRoleInputRefused) as caught:
                parse_document_role_request(body)

            assert _refused(caught)[0] == DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING

    def test_an_absent_body_is_refused(self):
        """Unlike an extraction, a role assertion cannot be omitted: the request IS the
        assertion, so an empty one says nothing rather than saying `UNKNOWN`."""
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request(None)

        assert _refused(caught)[0] == DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING

    @pytest.mark.parametrize("field", ("content_sha256", "storage_path", "page_count",
                                       "file_name", "created_at", "revision_label",
                                       "supersedes_document_id", "source_format",
                                       "byte_size", "uploaded_file_path", "drawing_id",
                                       "drawing_set_id", "analysis_run_id", "status",
                                       "project", "documents", "token", "authorization",
                                       "reviewer"))
    def test_a_field_this_boundary_never_takes_is_refused_by_name(self, field):
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": DOC_A, "role": "ASSEMBLY",
                                         field: "anything"})

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD
        assert field in statement

    def test_the_other_spelling_of_the_same_field_is_refused_rather_than_accommodated(self):
        """`document_role` is not the name this boundary takes, and a boundary that accepted
        both would have two names for one field."""
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": DOC_A, "document_role": "ASSEMBLY"})

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD
        assert "document_role" in statement

    def test_an_unknown_field_is_refused(self):
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": DOC_A, "role": "ASSEMBLY",
                                         "confidence": 0.9})

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD
        assert "confidence" in statement

    def test_the_operation_refuses_an_out_of_vocabulary_role_whatever_called_it(self, store):
        """The rule holds at a boundary a caller did NOT come through: a direct call with a
        role the database would refuse is refused here, and nothing is written."""
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A)

        with pytest.raises(DocumentRoleInputRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="OTHER",
                                 repository=store)

        assert _refused(caught)[0] == DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID
        assert store.writes == []
        assert store.reads == []
        assert store.row(DOC_A)["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_the_route_answers_an_invalid_role_with_422_and_writes_nothing(self, http):
        for role in ROLES_THIS_SYSTEM_DOES_NOT_STORE + ("fabrication", "", None):
            response = http.post({"document_id": DOC_A, "role": role})

            assert response.status_code == 422, response.text
            assert http.refusal(response)[0] == DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID

        assert http.store.writes == []
        assert http.store.reads == []
        assert http.store.row(DOC_A)["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_an_invalid_role_leaves_the_document_exactly_as_it_was(self, one_document):
        before = copy.deepcopy(one_document.row(DOC_A))

        with pytest.raises(DocumentRoleInputRefused):
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="OTHER",
                                 repository=one_document)

        assert one_document.row(DOC_A) == before
        assert one_document.reads == []


# ===========================================================================
# E. A DOCUMENT OF ANOTHER PROJECT IS NOT REACHABLE.
# ===========================================================================
class TestEAForeignDocumentIsUnreachable:
    def test_the_operation_refuses_a_document_of_another_project(self, two_projects):
        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_FOREIGN, role="FABRICATION",
                                 repository=two_projects)

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        assert repr(DOC_FOREIGN) in statement
        assert repr(PROJECT_ONE) in statement

    def test_the_other_projects_document_is_untouched_and_no_write_was_made(self, two_projects):
        before = copy.deepcopy(two_projects.row(DOC_FOREIGN))

        with pytest.raises(DocumentRoleRefused):
            assert_document_role(PROJECT_ONE, document_id=DOC_FOREIGN, role="FABRICATION",
                                 repository=two_projects)

        assert two_projects.row(DOC_FOREIGN) == before
        assert two_projects.writes == []

    def test_the_route_answers_a_foreign_document_with_409_and_writes_nothing(self, http):
        response = http.post({"document_id": DOC_FOREIGN, "role": "FABRICATION"})

        assert response.status_code == 409, response.text
        assert http.refusal(response)[0] == DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        assert http.store.writes == []
        assert http.store.row(DOC_FOREIGN)["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_the_refusal_learns_nothing_about_the_other_project(self, two_projects):
        """A document that exists in another project and a document that exists nowhere are
        answered identically: the id is looked for among THIS project's rows, so the other
        project's record is never read and cannot be described."""
        with pytest.raises(DocumentRoleRefused) as foreign_caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_FOREIGN, role="FABRICATION",
                                 repository=two_projects)
        with pytest.raises(DocumentRoleRefused) as absent_caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_ABSENT, role="FABRICATION",
                                 repository=two_projects)

        foreign_code, foreign_statement = _refused(foreign_caught)
        absent_code, _ = _refused(absent_caught)
        assert foreign_code == absent_code
        assert PROJECT_TWO not in foreign_statement
        assert PATH_B not in foreign_statement

    def test_the_foreign_project_cannot_be_asserted_on_through_its_own_id_either(self, http):
        """Naming the OTHER project does not make its document reachable through this
        caller: the caller does not own that project, so nothing is read at all."""
        response = http.post({"document_id": DOC_FOREIGN, "role": "FABRICATION"},
                             project_id=PROJECT_TWO)

        assert response.status_code == 403, response.text
        assert http.refusal(response)[0] == "ACCESS_NOT_OWNER"
        assert http.store.reads == []
        assert http.store.writes == []

    def test_one_projects_assertion_does_not_disturb_the_other_projects_document(
        self, two_projects
    ):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="STRUCTURAL_GA",
                             repository=two_projects)

        assert two_projects.row(DOC_FOREIGN)["role"] == DOCUMENT_ROLE_UNKNOWN
        assert two_projects.writes == [(PROJECT_ONE, DOC_A, "STRUCTURAL_GA")]


# ===========================================================================
# F. AN UNKNOWN DOCUMENT DOES NOT EXIST, AND IS TOLD SO.
# ===========================================================================
class TestFAnUnknownDocumentIsRefused:
    def test_the_operation_refuses_a_document_this_project_does_not_have(self, one_document):
        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_ABSENT, role="FABRICATION",
                                 repository=one_document)

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        assert "keeps 1" in statement

    def test_the_refusal_is_a_conflict_and_not_a_missing_project(self, http):
        response = http.post({"document_id": DOC_ABSENT, "role": "FABRICATION"})

        assert response.status_code == 409, response.text
        assert response.json()["detail"]["refusal"] == (
            DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        )

    def test_an_id_that_is_not_a_document_at_all_is_refused_for_what_it_is(self, one_document):
        """Nothing reads a document id's shape: an id that looks like no uuid is refused
        because no row of this project carries it, which is the same reason a well-formed id
        that names nothing is refused."""
        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id="not-a-document",
                                 role="FABRICATION", repository=one_document)

        assert _refused(caught)[0] == DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN

    def test_a_project_this_caller_owns_but_that_holds_no_document_is_refused(
        self, http_no_documents
    ):
        """The project is real and owned; its documents are none. The refusal states the
        count rather than pretending a document was there to be found."""
        response = http_no_documents.post({"document_id": DOC_A, "role": "FABRICATION"})

        assert response.status_code == 409, response.text
        assert http_no_documents.refusal(response)[0] == (
            DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        )
        assert "keeps 0" in http_no_documents.refusal(response)[1]
        assert http_no_documents.store.writes == []


# ===========================================================================
# G. MALFORMED IDENTIFIERS, AND WHO MAY ASK AT ALL.
# ===========================================================================
class TestGMalformedIdentifiers:
    @pytest.mark.parametrize("document_id", ("", "   ", None, 7, ["a"], {"id": "a"}, True))
    def test_a_malformed_document_id_is_refused_by_the_parser(self, document_id):
        with pytest.raises(DocumentRoleInputRefused) as caught:
            parse_document_role_request({"document_id": document_id, "role": "ASSEMBLY"})

        code, statement = _refused(caught)
        assert code == DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID
        assert "does not choose one" in statement

    def test_the_route_refuses_a_malformed_document_id_with_422_before_reading(self, http):
        response = http.post({"document_id": "  ", "role": "ASSEMBLY"})

        assert response.status_code == 422, response.text
        assert http.refusal(response)[0] == DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID
        assert http.store.reads == []

    def test_an_unknown_project_is_a_404(self, http):
        response = http.post({"document_id": DOC_A, "role": "ASSEMBLY"},
                             project_id=PROJECT_ABSENT)

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"
        assert http.store.reads == []

    def test_another_accounts_project_is_a_403_and_reads_no_document(self, http):
        response = http.post({"document_id": DOC_A, "role": "ASSEMBLY"},
                             project_id=PROJECT_TWO)

        assert response.status_code == 403
        assert http.refusal(response)[0] == "ACCESS_NOT_OWNER"
        assert http.store.reads == []
        assert http.store.writes == []

    def test_no_credential_is_a_401_and_reads_nothing(self, http):
        response = http.post({"document_id": DOC_A, "role": "ASSEMBLY"}, client=http.bare)

        assert response.status_code == 401
        assert http.refusal(response)[0] == "IDENTITY_NO_TOKEN"
        assert http.store.reads == []
        assert http.store.writes == []

    def test_no_field_can_grant_access_and_no_field_can_choose_the_project(self, http):
        """The project is the path and the reviewer is the token: a role assertion cannot
        name either, and a request that tries is refused before anything is read."""
        response = http.post({"document_id": DOC_A, "role": "ASSEMBLY",
                              "project_id": PROJECT_ONE, "user_id": OTHER_OWNER})

        assert response.status_code == 422
        assert http.refusal(response)[0] == DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD
        assert http.store.reads == []

    def test_the_other_authenticated_client_is_not_this_projects_owner(self, http):
        """The credential's own subject decides; a second authenticated session belonging to
        another account gets the same refusal as no session at all on a project it does not
        own."""
        response = http.post({"document_id": DOC_A, "role": "ASSEMBLY"}, client=http.other)

        assert response.status_code == 403
        assert http.refusal(response)[0] == "ACCESS_NOT_OWNER"


# ===========================================================================
# H. THE READ MODEL EXPOSES THE ROLE, AND DERIVES NOTHING FROM IT.
# ===========================================================================
class TestHTheReadModel:
    def _document_record(self, role, file_name):
        """J61's own read model, over a doubled repository, one document at a time."""
        from app.production_review import project_read

        row = {
            "id": DOC_A, "storage_path": PATH_A, "file_name": file_name,
            "source_format": "PDF", "byte_size": 1024, "page_count": 41,
            "content_sha256": None, "role": role, "revision_label": None,
            "supersedes_document_id": None,
        }

        class _Repository:
            def document_for_drawing(self, drawing_id):
                return row

        return project_read._document_record(_Repository(), "drawing-1", DOC_A)

    def test_the_asserted_role_is_exposed_verbatim(self):
        for role in DOCUMENT_ROLES:
            assert self._document_record(role, "x.pdf").role == role

    def test_the_asserted_role_is_visible_through_the_projects_own_read(self, one_document):
        """The role reaches the read model from the row the store holds, through J61's own
        reader — the same path a project page takes."""
        from app.production_review import project_read

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="FABRICATION",
                             repository=one_document)

        class _Repository:
            def document_for_drawing(self, drawing_id):
                return one_document.row(DOC_A)

        record = project_read._document_record(_Repository(), "drawing-1", DOC_A)

        assert record.role == "FABRICATION"
        assert record.document_id == DOC_A
        assert record.storage_path == PATH_A

    def test_an_unknown_role_is_exposed_as_unknown_and_not_as_an_absence(self):
        record = self._document_record(DOCUMENT_ROLE_UNKNOWN, "GAs (1).pdf")

        assert record.role == "UNKNOWN"
        assert record.role is not None

    def test_a_role_is_not_read_off_the_filename(self):
        """Every one of the reference package's own names, against a role that is not the
        one the name suggests: the record states what is stored."""
        for file_name in ("Transmittal 1.pdf", "GAs (1).pdf", "ASMs.pdf", "FABs.pdf",
                          "ISOs.pdf", "FABRICATION.pdf"):
            assert self._document_record("ARCHITECTURAL", file_name).role == (
                "ARCHITECTURAL"
            )

    def test_the_read_model_states_the_roles_this_milestone_can_assert(self):
        for role in SELBY_ROLES:
            assert self._document_record(role, "x.pdf").role == role


# ===========================================================================
# I. UNKNOWN REMAINS VALID — IT IS AN ASSERTION LIKE ANY OTHER.
# ===========================================================================
class TestIUnknownRemainsValid:
    def test_a_stated_role_can_be_replaced_by_unknown(self, one_document):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="FABRICATION",
                             repository=one_document)

        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A,
                                         role=DOCUMENT_ROLE_UNKNOWN,
                                         repository=one_document)

        assert assertion.previous_role == "FABRICATION"
        assert assertion.role == DOCUMENT_ROLE_UNKNOWN
        assert assertion.changed is True
        assert one_document.row(DOC_A)["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_a_document_may_stay_unknown_without_being_written_at_all(self, one_document):
        before = copy.deepcopy(one_document.row(DOC_A))

        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A,
                                         role=DOCUMENT_ROLE_UNKNOWN,
                                         repository=one_document)

        assert assertion.changed is False
        assert assertion.previous_role == DOCUMENT_ROLE_UNKNOWN
        assert one_document.writes == []
        assert one_document.row(DOC_A) == before

    def test_the_round_trip_is_two_writes_and_three_states(self, one_document):
        first = assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ISOMETRIC",
                                     repository=one_document)
        second = assert_document_role(PROJECT_ONE, document_id=DOC_A,
                                      role=DOCUMENT_ROLE_UNKNOWN,
                                      repository=one_document)

        assert (first.previous_role, first.role) == ("UNKNOWN", "ISOMETRIC")
        assert (second.previous_role, second.role) == ("ISOMETRIC", "UNKNOWN")
        assert one_document.writes == [
            (PROJECT_ONE, DOC_A, "ISOMETRIC"),
            (PROJECT_ONE, DOC_A, "UNKNOWN"),
        ]

    def test_the_route_answers_an_unknown_assertion_as_an_assertion(self, http):
        response = http.post({"document_id": DOC_A, "role": DOCUMENT_ROLE_UNKNOWN})

        assert response.status_code == 200, response.text
        assert response.json()["role"] == DOCUMENT_ROLE_UNKNOWN
        assert response.json()["changed"] is False
        assert http.store.writes == []

    def test_unknown_is_never_silently_replaced_by_a_guess(self, one_document):
        """The milestone's own prohibition, asserted: a document nobody has spoken about
        keeps exactly the role J61 gave it — through a refusal and a failed assertion
        alike."""
        with pytest.raises(DocumentRoleRefused):
            assert_document_role(PROJECT_ONE, document_id=DOC_B, role="FABRICATION",
                                 repository=one_document)
        with pytest.raises(DocumentRoleInputRefused):
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="OTHER",
                                 repository=one_document)

        assert one_document.row(DOC_A)["role"] == DOCUMENT_ROLE_UNKNOWN
        assert one_document.writes == []


# ===========================================================================
# J. AN ASSERTION CHANGES THE ROLE AND NOTHING ELSE IN THE SYSTEM.
# ===========================================================================
class TestJNothingElseChanges:
    def test_only_the_role_column_differs_after_an_assertion(self):
        store = _Store()
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, content_sha256=HASH_A,
                            file_name="Transmittal 1.pdf")
        before = copy.deepcopy(store.row(DOC_A))

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="TRANSMITTAL",
                             repository=store)

        after = copy.deepcopy(store.row(DOC_A))
        assert {k: v for k, v in after.items() if k != "role"} == {
            k: v for k, v in before.items() if k != "role"
        }
        assert after["role"] == "TRANSMITTAL"
        assert after["storage_path"] == PATH_A
        assert after["content_sha256"] == HASH_A
        assert after["file_name"] == "Transmittal 1.pdf"
        assert after["created_at"] == before["created_at"]
        assert after["revision_label"] is None
        assert after["supersedes_document_id"] is None
        assert after["page_count"] == before["page_count"]
        assert after["project_id"] == PROJECT_ONE

    def test_no_column_but_the_role_is_ever_sent_to_the_store(self, one_document):
        """The write this module makes is one column wide, and the store sees nothing else:
        a document's identity, path, name and count are not rewritten by a role."""
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="SCHEDULE",
                             repository=one_document)

        assert one_document.writes == [(PROJECT_ONE, DOC_A, "SCHEDULE")]

    def test_the_module_imports_nothing_but_its_own_store(self):
        assert _imported_modules(_module_tree()) == {
            "__future__", "app.engineering_data", "dataclasses", "typing",
        }

    def test_the_module_calls_none_of_the_other_authorities(self):
        assert not _called_names(_module_tree()) & FORBIDDEN_CALLS

    def test_the_module_touches_no_attribute_outside_its_own_operation(self):
        assert _attribute_names(_module_tree()) == MODULE_ATTRIBUTES

    def test_the_module_names_no_other_table(self):
        source = MODULE_PATH.read_text()
        for table in FORBIDDEN_TABLES:
            assert table not in source, table
        assert "project_documents" in source

    def test_one_assertion_is_at_most_one_read_and_one_write(self, one_document):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="DETAIL",
                             repository=one_document)

        assert one_document.reads == ["project_documents_for_project"]
        assert len(one_document.writes) == 1

    def test_a_route_assertion_reads_no_table_but_projects(self, http):
        """The route's own store access is the project row; the document is reached through
        the module's own seam, and no other table is named."""
        http.post({"document_id": DOC_A, "role": "SPECIFICATION"})

        assert http.client.tables_read == ["projects"]

    def test_the_repository_write_is_one_update_of_one_column(self):
        body = _function_body(REPOSITORY_PATH, "update_document_role")

        assert body.count("update(") == 1
        assert ".update({'role': role})" in body
        assert "insert" not in body
        assert "delete" not in body
        assert "upsert" not in body


# ===========================================================================
# K, L, M. EXTRACTION IS UNCHANGED — INCLUDING J63'S ADDRESSING AND J63A'S REFUSAL.
# ===========================================================================
class TestKExtractionIsUnchanged:
    def test_the_source_boundary_reads_no_role(self):
        source = SOURCE_BOUNDARY_PATH.read_text()

        # `role` appears in the boundary's own list of fields a client may not state, and in
        # prose. What it must never do is READ one.
        assert 'get("role")' not in source
        assert "['role']" not in source
        assert "[role]" not in source

    def test_the_first_window_guard_reads_no_role(self):
        body = _function_body(PIPELINE_PATH, "plan_first_window")

        assert "role" not in body
        assert "project_documents_for_project" in body
        assert "len(documents) > 1" in body

    def test_the_two_extraction_paths_are_not_reachable_from_the_role_module(self):
        """Neither file imports it — the direction that would matter. J63's boundary NAMES
        `role` and `document_role`, and it names them in the list of fields it REFUSES from
        a client; that is a refusal of a role, which is what this milestone wants, and it is
        asserted as a refusal below rather than read as an import."""
        for path in (SOURCE_BOUNDARY_PATH, PIPELINE_PATH):
            assert "production_document_role" not in path.read_text(), path.name

        assert _importers_of("production_document_role") == ["main.py"]
        assert "production_document_role" not in PIPELINE_PATH.read_text()

    def test_the_extraction_boundary_refuses_a_role_rather_than_reading_one(self):
        """A caller may not say what a document IS by asking for an extraction: J63's
        boundary refuses the field by name, before any project or document is read."""
        from app import production_extraction_source

        assert "role" in production_extraction_source.SERVER_OWNED_FIELDS
        assert "document_role" in production_extraction_source.SERVER_OWNED_FIELDS

    def test_the_role_module_is_imported_by_the_application_and_nowhere_deeper(self):
        """One import site, at the surface that routes requests — the module is not reachable
        from anything an extraction calls."""
        assert _importers_of("production_document_role") == ["main.py"]

    def test_an_addressed_document_resolves_to_its_own_path_whatever_its_role(
        self, two_documents
    ):
        """J63's addressing is by id and by nothing else: a role asserted on a document does
        not make it the source, and does not stop it being addressable."""
        from app.production_extraction_source import resolve_extraction_source

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="FABRICATION",
                             repository=two_documents)
        assert_document_role(PROJECT_ONE, document_id=DOC_B, role="STRUCTURAL_GA",
                             repository=two_documents)

        addressed_a = resolve_extraction_source(PROJECT_ONE, document_id=DOC_A,
                                                repository=two_documents)
        addressed_b = resolve_extraction_source(PROJECT_ONE, document_id=DOC_B,
                                                repository=two_documents)

        assert addressed_a.storage_path == PATH_A
        assert addressed_b.storage_path == PATH_B
        assert (addressed_a.document_id, addressed_b.document_id) == (DOC_A, DOC_B)

    def test_a_role_change_does_not_move_the_source_of_an_address(self, two_documents):
        from app.production_extraction_source import resolve_extraction_source

        before = resolve_extraction_source(PROJECT_ONE, document_id=DOC_A,
                                          repository=two_documents)
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ISOMETRIC",
                             repository=two_documents)
        after = resolve_extraction_source(PROJECT_ONE, document_id=DOC_A,
                                          repository=two_documents)

        assert (before.storage_path, before.document_id) == (
            after.storage_path, after.document_id)

    def test_an_omitted_address_still_reads_the_project_column_on_one_document(
        self, one_document
    ):
        from app.production_extraction_source import resolve_extraction_source

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="FABRICATION",
                             repository=one_document)

        resolved = resolve_extraction_source(
            PROJECT_ONE, project=one_document.projects[PROJECT_ONE],
            repository=one_document,
        )

        assert resolved.storage_path == PATH_A
        assert resolved.document_id is None

    def test_the_omitted_address_reads_no_document_table_at_all(self, one_document):
        """The pre-J63 path, verbatim: a role cannot be involved in a read that does not
        happen."""
        from app.production_extraction_source import resolve_extraction_source

        resolve_extraction_source(PROJECT_ONE, project=one_document.projects[PROJECT_ONE],
                                  repository=one_document)

        assert one_document.reads == []


class TestMJ63AsAmbiguityRuleIsUntouched:
    def _plan(self, production, store, project_id, monkeypatch, document_id=None):
        from app.production_extraction_source import resolve_extraction_source

        monkeypatch.setattr(production.pipeline, "repo", store)
        resolved = resolve_extraction_source(
            project_id, document_id=document_id, project=store.projects[project_id],
            repository=store,
        )
        return production.pipeline.plan_first_window(project_id, resolved)

    def test_an_omitted_address_on_a_two_document_project_is_still_refused(
        self, two_documents, production, monkeypatch
    ):
        from app.validation.page_windows import (
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            ContinuationRefused,
        )

        with pytest.raises(ContinuationRefused) as caught:
            self._plan(production, two_documents, PROJECT_ONE, monkeypatch)

        assert caught.value.code == CONTINUATION_DRAWING_SET_UNRESOLVED
        assert "2 source documents" in caught.value.detail

    def test_the_milestones_own_example_stays_refused(
        self, two_documents, production, monkeypatch
    ):
        """A is UNKNOWN, B is STRUCTURAL_GA, and the request names neither: refused for
        exactly the reason it was refused before roles existed."""
        from app.validation.page_windows import (
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            ContinuationRefused,
        )

        assert_document_role(PROJECT_ONE, document_id=DOC_B, role="STRUCTURAL_GA",
                             repository=two_documents)

        with pytest.raises(ContinuationRefused) as caught:
            self._plan(production, two_documents, PROJECT_ONE, monkeypatch)

        assert caught.value.code == CONTINUATION_DRAWING_SET_UNRESOLVED

    def test_a_fabrication_document_is_not_the_choice_either(
        self, two_documents, production, monkeypatch
    ):
        from app.validation.page_windows import (
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            ContinuationRefused,
        )

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="UNKNOWN",
                             repository=two_documents)
        assert_document_role(PROJECT_ONE, document_id=DOC_B, role="FABRICATION",
                             repository=two_documents)

        with pytest.raises(ContinuationRefused) as caught:
            self._plan(production, two_documents, PROJECT_ONE, monkeypatch)

        assert caught.value.code == CONTINUATION_DRAWING_SET_UNRESOLVED

    def test_every_role_in_the_vocabulary_leaves_the_ambiguity_refused(
        self, production, monkeypatch
    ):
        """All ten roles against an UNKNOWN sibling: not one of them is a reason to
        choose."""
        from app.validation.page_windows import (
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            ContinuationRefused,
        )

        for role in DOCUMENT_ROLES:
            store = _Store()
            store.seed_project(PROJECT_ONE)
            store.seed_document(PROJECT_ONE, document_id=DOC_A, storage_path=PATH_A)
            store.seed_document(PROJECT_ONE, document_id=DOC_B, storage_path=PATH_B,
                                role=role)

            with pytest.raises(ContinuationRefused) as caught:
                self._plan(production, store, PROJECT_ONE, monkeypatch)

            assert caught.value.code == CONTINUATION_DRAWING_SET_UNRESOLVED, role

    def test_an_addressed_request_is_returned_unchanged_and_reads_nothing(
        self, two_documents, production, monkeypatch
    ):
        from app.production_extraction_source import resolve_extraction_source

        monkeypatch.setattr(production.pipeline, "repo", two_documents)
        addressed = resolve_extraction_source(
            PROJECT_ONE, document_id=DOC_B, project=two_documents.projects[PROJECT_ONE],
            repository=two_documents,
        )
        before = list(two_documents.reads)

        planned = production.pipeline.plan_first_window(PROJECT_ONE, addressed)

        assert planned is addressed
        assert two_documents.reads == before

    def test_a_one_document_project_is_still_planned_as_before(
        self, one_document, production, monkeypatch
    ):
        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ISOMETRIC",
                             repository=one_document)

        planned = self._plan(production, one_document, PROJECT_ONE, monkeypatch)

        assert planned.storage_path == PATH_A
        assert planned.document_id is None

    def test_a_project_with_no_documents_is_still_the_upload_flow(
        self, store, production, monkeypatch
    ):
        """J61's rule deliberately allows a project holding no document yet: the file being
        extracted is not a row until a run reads it."""
        store.seed_project(PROJECT_ONE)

        planned = self._plan(production, store, PROJECT_ONE, monkeypatch)

        assert planned.storage_path == PATH_A
        assert planned.document_id is None


# ===========================================================================
# N. NOTHING IS INFERRED — READ FROM THE SOURCE, AND FROM BEHAVIOUR.
# ===========================================================================
class TestNNothingIsInferred:
    def test_the_operation_reads_exactly_two_fields_of_a_document_row(self):
        """The whole of what a role assertion knows about a document: WHICH row it is, and
        what role that row already states. Nothing else is available to infer from."""
        literals = _code_strings(MODULE_PATH, "assert_document_role")

        assert literals & set(DOCUMENT_COLUMN_NAMES) == {"id", "role"}

    def test_the_module_restates_no_role_name_as_a_value(self):
        """The vocabulary appears in the code only as `repo.DOCUMENT_ROLES`. A role name
        written out here would be a second list — and the place a rule about roles would have
        to live."""
        assert not (set(DOCUMENT_ROLES) & _code_strings(MODULE_PATH))

    def test_the_module_calls_no_parsing_hashing_matching_or_file_machinery(self):
        assert not _called_names(_module_tree()) & {
            "sha256", "md5", "digest", "encode", "compile", "match", "search", "find",
            "endswith", "startswith", "lower", "upper", "split", "open", "loads", "dumps",
            "read_bytes", "exists", "glob", "iterdir", "download",
        }

    def test_the_module_calls_exactly_what_it_is_allowed_to_call(self):
        assert _called_names(_module_tree()) == MODULE_CALLS

    def test_a_role_that_contradicts_every_attribute_of_the_file_is_still_the_role(self):
        """The milestone's reference names, against a role deliberately NOT what the name
        suggests: what is stored is what was asserted, and no field of the row is consulted
        for a better answer."""
        store = _Store()
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A,
                            storage_path="j64-owner/FABs.pdf",
                            file_name="Transmittal 1.pdf")

        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A, role="ISOMETRIC",
                                         repository=store)

        assert assertion.role == "ISOMETRIC"
        assert store.row(DOC_A)["file_name"] == "Transmittal 1.pdf"
        assert store.row(DOC_A)["storage_path"] == "j64-owner/FABs.pdf"
        assert store.row(DOC_A)["role"] == "ISOMETRIC"

    def test_the_route_states_no_inference_rule_in_its_code(self):
        body = _function_body(MAIN_PATH, "production_document_role").lower()

        for word in ("filename", "extension", "infer", "guess", "page_count", "sha256"):
            assert word not in body, word

    def test_the_route_produces_exactly_one_string_and_it_is_its_own_path(self):
        assert _code_strings(MAIN_PATH, "production_document_role") == {ROUTE_PATH}


# ===========================================================================
# O. NO SOURCE IS EVER SELECTED BY A ROLE.
# ===========================================================================
class TestONoSelectionByRole:
    def test_the_module_selects_nothing(self):
        assert not _called_names(_module_tree()) & {
            "plan_first_window", "plan_continuation", "plan_retry",
            "resolve_extraction_source", "run_extraction", "choose", "prefer",
            "select_document", "first", "sorted_documents",
        }

    def test_an_assertion_does_not_change_which_documents_a_project_keeps(
        self, two_documents
    ):
        before = [row["id"] for row in two_documents.project_documents_for_project(
            PROJECT_ONE)]

        assert_document_role(PROJECT_ONE, document_id=DOC_A, role="TRANSMITTAL",
                             repository=two_documents)

        after = [row["id"] for row in two_documents.project_documents_for_project(
            PROJECT_ONE)]
        assert after == before

    def test_only_the_role_read_model_and_this_operation_ever_read_a_role(self):
        """The whole application, searched: three modules read a `role` off a mapping, and
        one of them is reading the TOKEN's own role claim rather than a document's."""
        readers = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if 'get("role")' in path.read_text():
                readers.append(path.name)

        assert sorted(readers) == [
            "identity.py", "production_document_role.py", "project_read.py",
        ]
        assert 'claims.get("role")' in IDENTITY_PATH.read_text()
        assert "claims" not in PROJECT_READ_PATH.read_text()

    def test_the_extraction_routes_are_still_declared_exactly_once(self, production):
        """The routes the milestone may not have touched, still declared once each."""
        paths = [getattr(route, "path", "") for route in production.main.app.routes]

        assert paths.count("/extract/{project_id}") == 1
        assert paths.count("/continue-extraction/{project_id}") == 1
        assert paths.count("/retry-extraction/{project_id}") == 1
        assert paths.count(OPEN_ROUTE_PATH) == 1
        assert paths.count("/production/review/{project_id}/workflow") == 1


# ===========================================================================
# P. REASSIGNING A ROLE — THE SAME ACT, EVERY TIME.
# ===========================================================================
class TestPReassignment:
    @pytest.mark.parametrize("first", DOCUMENT_ROLES)
    def test_every_role_can_be_reassigned_to_any_other(self, first):
        second = "FABRICATION" if first != "FABRICATION" else "ARCHITECTURAL"
        store = _Store()
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, role=first)

        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A, role=second,
                                         repository=store)

        assert (assertion.previous_role, assertion.role) == (first, second)
        assert store.row(DOC_A)["role"] == second

    @pytest.mark.parametrize("role", DOCUMENT_ROLES)
    def test_reasserting_the_role_a_document_already_holds_writes_nothing(self, role):
        store = _Store()
        store.seed_project(PROJECT_ONE)
        store.seed_document(PROJECT_ONE, document_id=DOC_A, role=role)

        assertion = assert_document_role(PROJECT_ONE, document_id=DOC_A, role=role,
                                         repository=store)

        assert assertion.changed is False
        assert assertion.previous_role == role
        assert store.writes == []

    def test_a_reassignment_creates_no_revision_label_and_no_supersession(self, one_document):
        for role in ("TRANSMITTAL", "STRUCTURAL_GA", "UNKNOWN"):
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role=role,
                                 repository=one_document)

        row = one_document.row(DOC_A)
        assert row["revision_label"] is None
        assert row["supersedes_document_id"] is None

    def test_no_second_document_is_created_by_a_reassignment(self, one_document):
        for role in ("ASSEMBLY", "DETAIL", "SCHEDULE"):
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role=role,
                                 repository=one_document)

        kept = [row["id"] for row in one_document.documents
                if row["project_id"] == PROJECT_ONE]
        assert kept == [DOC_A]

    def test_the_route_reports_the_change_it_made_and_the_state_it_replaced(self, http):
        first = http.post({"document_id": DOC_A, "role": "STRUCTURAL_GA"})
        second = http.post({"document_id": DOC_A, "role": "FABRICATION"})

        assert first.json()["previous_role"] == DOCUMENT_ROLE_UNKNOWN
        assert second.json()["previous_role"] == "STRUCTURAL_GA"
        assert second.json()["role"] == "FABRICATION"
        assert http.store.writes == [
            (PROJECT_ONE, DOC_A, "STRUCTURAL_GA"),
            (PROJECT_ONE, DOC_A, "FABRICATION"),
        ]

    def test_the_five_reference_roles_can_be_placed_and_addressable_in_turn(self):
        """The milestone's representational target, exercised as a set: a transmittal, GAs,
        an assembly, a fabrication and isometric pack, each asserted on its own document —
        and each of them nonetheless a source only when it is ADDRESSED by id."""
        from app.production_extraction_source import resolve_extraction_source

        store = _Store()
        store.seed_project(PROJECT_ONE)
        documents = [DOC_A, DOC_B, DOC_C, DOC_FOREIGN, DOC_ABSENT]
        for index, document_id in enumerate(documents):
            store.seed_document(PROJECT_ONE, document_id=document_id,
                                storage_path=f"j64-owner/{index}.pdf")

        for document_id, role in zip(documents, SELBY_ROLES):
            assertion = assert_document_role(PROJECT_ONE, document_id=document_id,
                                             role=role, repository=store)
            assert (assertion.document_id, assertion.role) == (document_id, role)

        assert [store.row(document_id)["role"] for document_id in documents] == list(
            SELBY_ROLES)

        for index, (document_id, role) in enumerate(zip(documents, SELBY_ROLES)):
            resolved = resolve_extraction_source(PROJECT_ONE, document_id=document_id,
                                                 repository=store)
            assert resolved.storage_path == f"j64-owner/{index}.pdf", role


# ===========================================================================
# Q. THE LIVE PROJECT, READ ONLY — NOTHING WAS WRITTEN BY THIS MILESTONE.
# ===========================================================================
class TestQTheLiveProjectIsUntouched:
    def test_the_acceptance_project_still_has_one_unknown_document(self, live_client):
        """J64 asserted no live role. The acceptance project's own document carries the role
        J61 left it with — UNKNOWN, identity unproven."""
        documents = j61._read(
            live_client, "project_documents", "*",
            project_id=j61.ACCEPTANCE_PROJECT,
        )

        assert len(documents) == 1
        assert documents[0]["role"] == DOCUMENT_ROLE_UNKNOWN
        assert documents[0]["content_sha256"] is None

    def test_every_live_document_still_carries_the_role_the_backfill_gave_it(
        self, live_client
    ):
        """Every row J61's backfill created was inserted with the literal 'UNKNOWN' — the
        migration's own values list states it — and nothing has written the column since:
        this milestone's writer has never run against the live database. A row holding
        anything else would be a role nothing in this history asserted."""
        rows = j61._read(live_client, "project_documents", "id,project_id,role")

        assert rows, "the live project_documents table holds no row to describe"
        roles = {row["role"] for row in rows}
        assert roles == {DOCUMENT_ROLE_UNKNOWN}, sorted(roles)

    def test_the_live_count_of_documents_is_the_one_j63a_recorded(self, live_client):
        rows = j61._read(live_client, "project_documents", "id")

        assert len(rows) == 8

    def test_revision_zero_is_still_recorded_exactly_once_with_its_own_digest(
        self, live_client
    ):
        snapshots = j61._read(
            live_client, "connection_review_snapshots", "review_revision,evidence_identity",
            project_id=j61.ACCEPTANCE_PROJECT,
        )

        assert len(snapshots) == 1
        assert snapshots[0]["review_revision"] == 0
        assert snapshots[0]["evidence_identity"]["evidence_digest"] == (
            j61.REVISION_ZERO_DIGEST
        )

    def test_the_live_vocabulary_is_the_one_tested_here(self):
        """Read from the migration the live table was created by: the roles this milestone
        accepts are the roles the live database will store, and there are no others — the
        column's own default is UNKNOWN and its CHECK is the list above."""
        import re

        sql = J61_MIGRATION.read_text()
        declared = re.findall(
            r"'([A-Z_]+)'", re.search(r"check \(role in \(\s*([^)]*)\)\)", sql, re.S).group(1)
        )

        assert declared == list(DOCUMENT_ROLES)
        assert sql.count("role text not null default 'UNKNOWN'") == 1

    def test_the_revision_zero_record_the_brief_pins_is_still_the_one_recorded(
        self, live_client
    ):
        snapshots = j61._read(live_client, "connection_review_snapshots", "*",
                              project_id=j61.ACCEPTANCE_PROJECT)

        assert [snapshot["review_revision"] for snapshot in snapshots] == [0]
        assert j61.REVISION_ZERO_DIGEST == (
            "61f18266bdc200cabec400c1fbb51140b6ba5965f9c3730e5fa1111a73727cb3"
        )


# ===========================================================================
# THE MUTATIONS. Each removes one guard and shows a property goes red.
# ===========================================================================
class TestTheMutations:
    def test_mutation_a_the_writes_project_filter_is_what_refuses_a_foreign_document(self):
        """The REAL repository write, compiled twice from its own source: once as it is, once
        with the single `.eq('project_id', ...)` term removed. Asked to write another
        project's document with the wrong project named, the genuine function writes nothing
        and reports nothing; the mutated one rewrites that document."""
        rows = [
            {"id": DOC_FOREIGN, "project_id": PROJECT_TWO, "role": DOCUMENT_ROLE_UNKNOWN},
            {"id": DOC_A, "project_id": PROJECT_ONE, "role": DOCUMENT_ROLE_UNKNOWN},
        ]

        genuine = _repository_function()
        genuine.__globals__["supabase"] = _FakeSupabase(rows)
        assert genuine(PROJECT_ONE, DOC_FOREIGN, "FABRICATION") is None
        assert [row["role"] for row in rows] == [DOCUMENT_ROLE_UNKNOWN] * 2

        mutated = _repository_function(project_filter=False)
        mutated.__globals__["supabase"] = _FakeSupabase(rows)
        written = mutated(PROJECT_ONE, DOC_FOREIGN, "FABRICATION")
        assert written["role"] == "FABRICATION"
        assert rows[0]["role"] == "FABRICATION"
        assert rows[1]["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_mutation_a2_the_operation_still_refuses_what_the_filter_would_have_allowed(
        self, two_projects, monkeypatch
    ):
        """Even with a store whose WRITE is unscoped, the operation refuses: the read that
        precedes it is scoped to the project, so the foreign document is never addressed in
        the first place. Two independent guards, and this is the outer one."""
        def unscoped_write(project_id, document_id, role):
            two_projects.writes.append((project_id, document_id, role))
            return _projected({**two_projects.row(document_id), "role": role})

        monkeypatch.setattr(two_projects, "update_document_role", unscoped_write)

        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_FOREIGN, role="FABRICATION",
                                 repository=two_projects)

        assert _refused(caught)[0] == DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        assert two_projects.writes == []
        assert two_projects.row(DOC_FOREIGN)["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_mutation_b_the_scoped_read_is_what_makes_a_foreign_document_unreachable(
        self, two_projects, monkeypatch
    ):
        """Unscoped, the read serves every project's documents, and the same request that was
        refused as a document of another project gets past that refusal and reaches the
        write — where the pair filter (mutation A) refuses it instead. The KIND of refusal
        is what changes; the write still never lands."""
        with pytest.raises(DocumentRoleRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_FOREIGN, role="FABRICATION",
                                 repository=two_projects)

        assert _refused(caught)[0] == DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN
        assert two_projects.writes == []

        def unscoped_read(project_id):
            two_projects.reads.append("project_documents_for_project")
            return [_projected(row) for row in two_projects.documents]

        monkeypatch.setattr(two_projects, "project_documents_for_project", unscoped_read)

        with pytest.raises(DocumentRoleRefused) as mutated_caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_FOREIGN, role="FABRICATION",
                                 repository=two_projects)

        assert _refused(mutated_caught)[0] == DOCUMENT_ROLE_REFUSED_NOT_RECORDED
        assert two_projects.writes == [(PROJECT_ONE, DOC_FOREIGN, "FABRICATION")]
        assert two_projects.row(DOC_FOREIGN)["role"] == DOCUMENT_ROLE_UNKNOWN

    def test_mutation_c_the_vocabulary_check_is_what_refuses_a_role_nothing_stores(
        self, one_document, monkeypatch
    ):
        """A vocabulary admitting `OTHER` would accept it and write it. The genuine one
        refuses it, and the database's own CHECK sits behind that."""
        monkeypatch.setattr(role_boundary, "DOCUMENT_ROLES", DOCUMENT_ROLES + ("OTHER",))

        mutated = assert_document_role(PROJECT_ONE, document_id=DOC_A, role="OTHER",
                                       repository=one_document)

        assert mutated.role == "OTHER"
        assert one_document.row(DOC_A)["role"] == "OTHER"

        monkeypatch.undo()

        with pytest.raises(DocumentRoleInputRefused) as caught:
            assert_document_role(PROJECT_ONE, document_id=DOC_A, role="OTHER",
                                 repository=one_document)

        assert _refused(caught)[0] == DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID
        # Exactly one write in the whole story: the mutated vocabulary allowed it, and the
        # genuine one refused the second attempt before it could write a thing.
        assert one_document.writes == [(PROJECT_ONE, DOC_A, "OTHER")]


# ===========================================================================
# SCOPE — WHAT THIS MILESTONE ADDED, AND WHAT IT DID NOT.
# ===========================================================================
class TestScope:
    def test_the_route_is_registered_once_as_a_post(self, production):
        routes = [
            route for route in production.main.app.routes
            if getattr(route, "path", "") == ROUTE_PATH
        ]

        assert len(routes) == 1
        assert routes[0].methods == {"POST"}
        assert role_boundary.ROUTE_PATH == ROUTE_PATH

    def test_the_route_set_is_the_one_j20_freezes(self, production):
        assert j20._declared_routes(production.main.app) == j20.FROZEN_ROUTES
        assert ROUTE_PATH in [path for _, path in j20.FROZEN_ROUTES]

    def test_the_route_touches_no_connection_review_vocabulary(self):
        """J20's prohibition, applied to this route's own path: a role assertion is about a
        source document and is not a review, a decision, a blocker or a task."""
        for forbidden in ("connection", "contract", "package", "candidate", "decision",
                          "blocker", "task", "gate", "resolve", "review-items"):
            assert forbidden not in ROUTE_PATH

    def test_the_two_frozen_route_sets_elsewhere_in_the_suite_name_this_route(self):
        """Two sets elsewhere declare the production surface, and the milestone that adds a
        route appends it to both. This asserts both did, rather than trusting it."""
        j47 = J47_TEST_PATH.read_text()

        assert f'"{ROUTE_PATH}"' in j47
        assert ROUTE_PATH in [path for _, path in j20.FROZEN_ROUTES]
        assert j20.FROZEN_ROUTES.count((("POST",), ROUTE_PATH)) == 1

    def test_the_route_authorizes_before_it_reads_the_request(self):
        """Order is the whole of the safety story: the project is loaded and the reviewer
        checked BEFORE a single field of the body is looked at."""
        body = _function_body(MAIN_PATH, "production_document_role")

        assert body.index("_authorized_project(") < body.index(
            "parse_document_role_request(")

    def test_the_route_calls_exactly_what_it_is_allowed_to_call(self):
        assert _called_names(_function_node(MAIN_PATH, "production_document_role")) == {
            "Body", "Depends", "HTTPException", "_authorized_project",
            "_route_refusal_detail", "assert_document_role", "parse_document_role_request",
            "post", "to_response",
        }

    def test_the_route_is_wired_to_the_reviewers_own_credential(self):
        tree = _function_node(MAIN_PATH, "production_document_role")

        assert "require_reviewer" in ast.unparse(tree)
        # Two mentions in the whole application: the endpoint's own name, and the import
        # that binds it.
        assert MAIN_PATH.read_text().count("production_document_role") == 2

    def test_the_two_statuses_the_route_maps_are_422_and_409(self):
        body = _function_body(MAIN_PATH, "production_document_role")

        assert body.count("status_code=422") == 2
        assert body.count("status_code=409") == 1
        assert "status_code=200" not in body

    def test_no_migration_was_created_by_this_milestone(self):
        migrations = sorted(p.name for p in (REPO / "supabase" / "migrations").glob("*.sql"))

        assert not [name for name in migrations if "j64" in name.lower()]
        assert migrations.count(J61_MIGRATION.name) == 1

    def test_the_j61_migration_is_unchanged_by_this_milestone(self):
        """No line was added to the migration that declares the vocabulary: J64 lives
        entirely on the column J61 already published."""
        sql = J61_MIGRATION.read_text()

        assert sql.count("constraint project_documents_role_check") == 1
        assert "j64" not in sql.lower()

    def test_the_repository_gained_exactly_one_function(self):
        names = [node.name for node in _module_tree(REPOSITORY_PATH).body
                 if isinstance(node, ast.FunctionDef)]

        assert names.count("update_document_role") == 1
        assert names.count("update_document_page_count") == 1

    def test_the_repository_write_is_scoped_to_the_project_as_well_as_the_document(self):
        body = _function_body(REPOSITORY_PATH, "update_document_role")

        assert ".eq('id', document_id)" in body
        assert ".eq('project_id', project_id)" in body
        assert ".table('project_documents')" in body

    def test_the_route_is_declared_with_the_modules_own_path(self):
        """One path in the codebase for asserting a role, and the decorator takes it from
        the module's own constant rather than spelling it again."""
        node = _function_node(MAIN_PATH, "production_document_role")

        assert len(node.decorator_list) == 1
        decorator = ast.unparse(node.decorator_list[0])

        assert decorator.startswith("app.post(")
        assert decorator.count(ROUTE_PATH) == 1
        assert _code_strings(MAIN_PATH, "production_document_role") == {ROUTE_PATH}

    def test_the_handler_body_states_no_path_at_all(self):
        """The handler addresses nothing by name: the project comes from the path FastAPI
        matched and the document from the request, so a path literal in the body could only
        be a second, unowned way to reach something."""
        node = _function_node(MAIN_PATH, "production_document_role")
        bare = copy.deepcopy(node)
        bare.decorator_list = []
        body = ast.unparse(ast.Module(body=[bare], type_ignores=[]))

        for literal in (ROUTE_PATH, "/production/review/", "/extract/",
                        "/continue-extraction/", "/retry-extraction/"):
            assert literal not in body, literal
