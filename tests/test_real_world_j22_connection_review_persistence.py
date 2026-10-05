"""
Milestone J22 — CONNECTION-REVIEW PERSISTENCE: THE PROOFS.

J21 designed the two tables and proved, over the genuine Arkles capture, that a project's
7AK review contract rebuilds from them byte for byte. J22 writes the migration and the
writer. These tests prove the implementation, in the order the brief asks for it:

  1. THE SCHEMA. Every designed column is created with its designed type and nullability;
     the payload columns are `json` and nothing is `jsonb`; the primary keys, the composite
     foreign key and the project foreign key are the designed ones; the CHECK vocabularies
     are the OWNING MODULES' constants, not literals a test cannot disagree with; there is
     no index and no policy.
  2. THE WRITER. A genuine revision 0 and a genuine revision 1 are persisted from the
     genuine 7AJ workflow, and refused when the revision is a duplicate, a gap, an unknown
     project, or a state outside the recorded vocabulary.
  3. THE TRANSPORT. Payloads cross as JSON TEXT and are cast server-side, so nothing
     between the caller's `json.dumps` and the column can re-order a key.
  4. THE REPLAY. The stored rows alone rebuild the contracts the real 7AK builder produces,
     including the human answers, the provenance labels, the tasks and the output identity.
  5. IMMUTABILITY. A later revision never changes an earlier one; a rewrite is refused; no
     update, delete or upsert exists to call.
  6. STALENESS. Changed evidence makes a revision stale; a read that wrote no evidence does
     not; a stale revision stays exactly as it was recorded.
  7. ISOLATION AND AUTHORIZATION. The writer takes a J19 binding and no owner, role or
     project id; a request cannot write to a project it was not authorized for.
  8. THE ABSENCE. No existing project has review state, nothing backfilled it, and the read
     says so explicitly rather than rendering an empty review.
  9. THE BOUNDARIES. No route, no production binding, no second status or revision
     authority, no new dependency.
 10. THE LIVE DEPLOYMENT, read-only.

WHAT IS PROVEN WHERE
====================
The schema, the writer's composition, the transport, the replay, the immutability and the
staleness are proven IN PROCESS over the genuine capture and a double for the service-role
client. The double restates the deployed function's rules and is NOT the authority for
them: the deployed function, the trigger, the constraints and the grants were exercised
against the real database by a transaction that ended in `rollback;` (see the migration
header), and the live tests at the end of this file read the real deployment.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import re
import uuid
from pathlib import Path

import pytest

from app.cad_engine.automation_gate import AUTOMATION_DECISIONS
from app.cad_engine.connection_review_snapshot import (
    CITATION_TABLE,
    EVIDENCE_KIND,
    EVIDENCE_TABLES,
    ITEM_TABLE,
    ITEM_TABLE_COLUMNS,
    SNAPSHOT_REFUSED_PROJECT_MISMATCH,
    SNAPSHOT_REFUSED_REVISION_GAP,
    SNAPSHOT_TABLE,
    SNAPSHOT_TABLE_COLUMNS,
    SnapshotRefused,
    build_review_snapshot,
    evidence_identity,
    project_contract_from_snapshot,
    snapshot_is_stale,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUSES
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUSES
from app.cad_engine.project_workflow import resolve_project_connection
from app.cad_engine.review_contract import build_project_review_contract
from app.engineering_data import connection_review_repository as store
from app.production_review.authorization import ACCESS_ALLOWED, AccessDecision
from app.production_review.binding import BindingRefused, bind_project_review
from app.production_review.identity import ReviewerIdentity
from app.production_review.project_read import ProjectReviewRecord
from tests import test_real_world_j21_connection_review_data_model_design as j21
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests.test_real_world_j20_connection_review_persistence_gap import (
    FROZEN_ROUTES,
    _declared_routes,
)

REPO = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    REPO / "supabase" / "migrations" / "20260925000000_j22_connection_review_persistence.sql"
)
STORE_PATH = REPO / "app" / "engineering_data" / "connection_review_repository.py"

PROJECT_ID = j21.PROJECT_ID
OTHER_PROJECT_ID = "PROJ-7J22-OTHER"
SELECTED_PACKAGE_ID = j21.SELECTED_PACKAGE_ID
NO_EVIDENCE = j21.NO_EVIDENCE

production = j4.production

needs_real_capture = pytest.mark.skipif(
    not j21.ARKLES_REAL_AI_EXTRACTION,
    reason="the real Arkles connection extraction is not present; the persistence proof is "
           "skipped rather than run over a fabricated review state",
)


# =============================================================================
# THE GENUINE STATE — the same capture and the same 7AJ operations J21 proved the
# design over. Nothing here is re-invented: the workflow builder, the human answers
# and the resolution call are J21's own.
# =============================================================================
@pytest.fixture(scope="module")
def workflow():
    if not j21.ARKLES_REAL_AI_EXTRACTION:
        pytest.skip("the real Arkles capture is not present")
    return j21._arkles_workflow()


@pytest.fixture(scope="module")
def revision_zero(workflow):
    return build_review_snapshot(
        workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE, previous_revision=None
    )


@pytest.fixture(scope="module")
def resolved_workflow(workflow, tmp_path_factory):
    return resolve_project_connection(
        workflow,
        package_id=SELECTED_PACKAGE_ID,
        resolutions=j21._human_resolutions(workflow, SELECTED_PACKAGE_ID),
        output_dir=str(tmp_path_factory.mktemp("j22-artifacts")),
    )


@pytest.fixture(scope="module")
def revision_one(resolved_workflow):
    return build_review_snapshot(
        resolved_workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
        previous_revision=0,
    )


def _review_record(project_id=PROJECT_ID) -> ProjectReviewRecord:
    return ProjectReviewRecord(
        project_id=project_id, project_status="review", source_format="pdf",
        uploaded_file_path=None, warnings=(), coverage=None, failures=None, drawing_sets=(),
    )


def _identity() -> ReviewerIdentity:
    return ReviewerIdentity(
        user_id="reviewer-1", role="authenticated", issuer="https://issuer.invalid",
        expires_at=4102444800,
    )


def _binding(project_id=PROJECT_ID):
    """A genuine J19 binding for `project_id`, built by J19's own binder.

    The identity is a value object constructed here — WHO the reviewer is belongs to J19's
    own file, which proves it with real signed tokens. What this file proves is that the
    WRITER requires the BINDING: `bind_project_review` refuses anything but an ALLOWED
    decision, so a caller with no authorization has nothing to pass.
    """
    return bind_project_review(
        identity=_identity(),
        record=_review_record(project_id),
        decision=AccessDecision(
            allowed=True, code=ACCESS_ALLOWED, detail="allowed", project_id=project_id,
        ),
    )


# =============================================================================
# THE DOUBLE — an in-process stand-in for the service-role client.
# =============================================================================
class _ApiError(Exception):
    """The error shape `postgrest-py` raises for a statement the database refused."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = None
        self.hint = None


class _Result:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _Query:
    """`table(name).select(columns).eq(column, value)…execute()`."""

    def __init__(self, store, table, columns):
        self._store = store
        self._table = table
        self._columns = columns
        self._filters: list[tuple[str, object]] = []

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def execute(self):
        self._store.reads.append((self._table, tuple(self._filters), self._columns))
        return _Result([
            dict(row) for row in self._store.tables[self._table]
            if all(row.get(column) == value for column, value in self._filters)
        ])


class _Table:
    def __init__(self, store, name):
        if name not in store.tables:
            raise _ApiError("PGRST205", f"Could not find the table public.{name}")
        self._store = store
        self._name = name

    def select(self, columns="*"):
        return _Query(self._store, self._name, columns)


class _Rpc:
    def __init__(self, store, fn, params):
        self._store = store
        self._fn = fn
        self._params = params

    def execute(self):
        return self._store.call(self._fn, self._params)


class _FakeStore:
    """Two tables and one function, standing in for the service-role client.

    It restates the rules the deployed function enforces — the revision guard, the project
    foreign key, the CHECK vocabularies, the item primary key — and it is NOT the authority
    for any of them. The deployed function, the append-only trigger, the constraints and the
    grants were exercised against the real database by a transaction that ended in
    `rollback;`, and the live tests below read the real deployment. What this double is for
    is driving the WRITER: the refusals it raises are the refusals the writer must
    translate, and the params it records are what actually crossed the wire.

    It validates every item BEFORE writing anything, because the deployed function is one
    transaction and an item that violates a CHECK leaves no header behind. A double that
    wrote the header first would model a failure mode the real writer does not have.
    """

    def __init__(self, projects=(PROJECT_ID,)):
        self.projects = set(projects)
        # J66 added a THIRD table to the review chain, and `read_connection_review_state`
        # reads it — so the double models it, empty, exactly as the deployed database has it.
        # Nothing else about this double changed: it still refuses every write except the one
        # writer it knows, and the assertions above it are untouched.
        self.tables = {SNAPSHOT_TABLE: [], ITEM_TABLE: [], CITATION_TABLE: []}
        self.calls: list[tuple] = []
        self.reads: list[tuple] = []

    def table(self, name):
        self.calls.append(("table", name))
        return _Table(self, name)

    def rpc(self, fn, params):
        self.calls.append(("rpc", fn, params))
        return _Rpc(self, fn, params)

    def rows(self, table):
        return [copy.deepcopy(row) for row in self.tables[table]]

    def writes(self):
        return [call for call in self.calls if call[0] == "rpc"]

    def call(self, fn, params):
        if fn != store.WRITER_FUNCTION:
            raise _ApiError("PGRST202", f"Could not find the function public.{fn}")
        project = params["p_project_id"]
        if project not in self.projects:
            raise _ApiError(
                "23503",
                'insert or update on table "connection_review_snapshots" violates foreign '
                'key constraint "connection_review_snapshots_project_fkey"',
            )
        revision = params["p_review_revision"]
        recorded = [
            row["review_revision"] for row in self.tables[SNAPSHOT_TABLE]
            if row["project_id"] == project
        ]
        expected = max(recorded) + 1 if recorded else 0
        if revision != expected:
            raise _ApiError(
                "P0001",
                f"SNAPSHOT_REFUSED_REVISION_GAP: project {project} records revision(s) up "
                f"to {expected - 1}; revision {revision} was offered, and a recorded "
                "revision is never overwritten",
            )
        if params["p_project_status"] not in AUTOMATION_DECISIONS:
            raise _ApiError(
                "23514",
                'new row for relation "connection_review_snapshots" violates check '
                'constraint "connection_review_snapshots_project_status_check"',
            )
        items = json.loads(params["p_items"])
        for item in items:
            if item["decision"] not in AUTOMATION_DECISIONS:
                raise _ApiError("23514", "violates check constraint "
                                         '"connection_review_items_decision_check"')
            if item["output_status"] is not None and item["output_status"] not in OUTPUT_STATUSES:
                raise _ApiError("23514", "violates check constraint "
                                         '"connection_review_items_output_status_check"')
            if (item["verification_status"] is not None
                    and item["verification_status"] not in VERIFICATION_STATUSES):
                raise _ApiError("23514", "violates check constraint "
                                         '"connection_review_items_verification_status_check"')
        ids = [item["review_package_id"] for item in items]
        if len(set(ids)) != len(ids):
            raise _ApiError("23505", "duplicate key value violates unique constraint "
                                     '"connection_review_items_pkey"')

        self.tables[SNAPSHOT_TABLE].append({
            "project_id": project,
            "review_revision": revision,
            "evidence_identity": json.loads(params["p_evidence_identity"]),
            "evidence_run_ids": json.loads(params["p_evidence_run_ids"]),
            "project_status": params["p_project_status"],
            "recorded_at": "2026-09-25T00:00:00+00:00",
        })
        for item in items:
            row = {"project_id": project, "review_revision": revision}
            row.update(item)
            self.tables[ITEM_TABLE].append(row)
        return _Result(None)


@pytest.fixture()
def fake():
    return _FakeStore()


# =============================================================================
# THE MIGRATION, READ AS A FILE.
# =============================================================================
def _sql() -> str:
    assert MIGRATION_PATH.exists(), f"missing {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _statement_lines() -> list[str]:
    return [
        line.strip().rstrip(";")
        for line in _sql().splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]


def _statements() -> str:
    """The migration's statements, comments removed and whitespace normalised."""
    return re.sub(r"\s+", " ", " ".join(_statement_lines())).strip()


def _block(table: str) -> str:
    """One `create table` body, as authored."""
    match = re.search(
        rf"create table if not exists public\.{table}\s*\((.*?)\n\);", _sql(), re.S
    )
    assert match, f"no create table for {table}"
    return match.group(1)


SQL_TYPES = frozenset(
    {"uuid", "integer", "bigint", "smallint", "text", "json", "jsonb", "boolean",
     "numeric", "timestamptz", "timestamp"}
)


def _column_definitions(table: str) -> dict[str, str]:
    """The columns of one `create table` body, as {name: rest-of-line}.

    A line counts as a column only when its second token is a SQL type. Table constraints
    (`constraint <name> ...`) and their continuation lines (`primary key (...)`,
    `foreign key (...)`, `check (...)`) are otherwise indistinguishable from columns by
    shape alone — `foreign key (project_id)` would read as a column named `foreign` of
    type `key (...)`. The type check is what separates them.
    """
    definitions = {}
    for raw in _block(table).splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--") or line.startswith("constraint"):
            continue
        match = re.match(r"([a-z_]+)\s+(.*)$", line)
        if match and match.group(2).split()[0] in SQL_TYPES:
            definitions[match.group(1)] = re.sub(r"\s+", " ", match.group(2)).strip()
    return definitions


def _check_literals(constraint: str) -> tuple[str, ...]:
    body = re.search(rf"constraint {constraint}\s+check \((.*?)\)", _sql(), re.S)
    assert body, f"no check constraint {constraint}"
    return tuple(re.findall(r"'([A-Z][A-Z_0-9]*)'", body.group(1)))


def _store_tree() -> ast.Module:
    return ast.parse(STORE_PATH.read_text(encoding="utf-8"))


def _strip_docstrings(node: ast.AST) -> ast.AST:
    """Blank every docstring in the tree, in place."""
    for inner in ast.walk(node):
        body = getattr(inner, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body[0] = ast.copy_location(ast.Pass(), first)
    return node


def _store_code() -> str:
    """The store's source, docstrings removed.

    The prose in this module discusses the very things the guards below forbid — `rebuild`,
    `reconstruct`, `review_items`, `decision` — because explaining why something is NOT done
    requires naming it. A guard that reads the prose cannot tell explaining from doing, so
    these guards read the code.
    """
    return ast.unparse(_strip_docstrings(_store_tree()))


def _functions_mentioning(name: str) -> list[str]:
    """The names of the functions whose own CODE (body, not docstring) names `name`."""
    return [
        node.name
        for node in ast.walk(_store_tree())
        if isinstance(node, ast.FunctionDef)
        and name in ast.unparse(_strip_docstrings(node))
    ]


# =============================================================================
# 1. THE SCHEMA
# =============================================================================
class TestTheMigrationIsTheDesignedSchema:
    def test_the_two_designed_tables_are_created_and_no_other(self):
        created = set(re.findall(r"create table if not exists public\.([a-z_]+)", _sql()))
        assert created == {SNAPSHOT_TABLE, ITEM_TABLE}
        altered = re.findall(r"alter table\s+(?:public\.)?([a-z_]+)", _sql())
        assert set(altered) == {SNAPSHOT_TABLE, ITEM_TABLE}, altered
        assert not re.search(r"(?i)\bdrop\s+table", _sql())

    def test_every_designed_column_is_created_with_its_type_and_nullability(self):
        for table, columns in (
            (SNAPSHOT_TABLE, SNAPSHOT_TABLE_COLUMNS),
            (ITEM_TABLE, ITEM_TABLE_COLUMNS),
        ):
            definitions = _column_definitions(table)
            for name, sql_type, nullable, _ in columns:
                assert name in definitions, f"{table}.{name} is not created"
                definition = definitions[name]
                assert re.match(rf"^{sql_type}\b", definition), (table, name, definition)
                assert ("not null" in definition) is (not nullable), (table, name, definition)

    def test_no_column_is_created_that_the_design_does_not_name(self):
        assert set(_column_definitions(SNAPSHOT_TABLE)) == {
            name for name, _, _, _ in SNAPSHOT_TABLE_COLUMNS
        } | {"recorded_at"}
        assert set(_column_definitions(ITEM_TABLE)) == {
            name for name, _, _, _ in ITEM_TABLE_COLUMNS
        }

    def test_the_four_never_attempted_columns_are_exactly_the_nullable_ones(self):
        """NULL is a state — never dispatched, never verified, never processed, no identity
        supplied — and the columns that carry one are exactly these four."""
        nullable = {name for name, _, is_nullable, _ in ITEM_TABLE_COLUMNS if is_nullable}
        assert nullable == {
            "connection_id", "output_status", "verification_status",
            "last_processed_revision",
        }
        assert all(is_nullable is False for _, _, is_nullable, _ in SNAPSHOT_TABLE_COLUMNS)

    def test_the_primary_keys_are_the_designed_ones(self):
        assert "primary key (project_id, review_revision)" in _statements()
        assert "primary key (project_id, review_revision, review_package_id)" in _statements()

    def test_the_item_table_references_its_header(self):
        assert (
            "foreign key (project_id, review_revision) references "
            "public.connection_review_snapshots (project_id, review_revision) "
            "on delete restrict" in _statements()
        )

    def test_the_header_references_the_project_without_a_cascade(self):
        """`on delete restrict`, not the cascade the four existing project-child tables use:
        a review snapshot records a human decision, and a cascade would let a project
        deletion silently destroy the only copy of it."""
        assert (
            "foreign key (project_id) references public.projects (id) on delete restrict"
            in _statements()
        )
        assert "on delete cascade" not in _sql().lower()

    def test_every_payload_column_is_json_and_nothing_is_jsonb(self):
        """`jsonb` appears in this migration's COMMENTS, explaining why it is not used. The
        check runs against the statements, where the word must not appear at all."""
        assert "jsonb" not in _statements().lower()
        for table, columns in (
            (SNAPSHOT_TABLE, SNAPSHOT_TABLE_COLUMNS),
            (ITEM_TABLE, ITEM_TABLE_COLUMNS),
        ):
            definitions = _column_definitions(table)
            for name, sql_type, _, _ in columns:
                assert definitions[name].startswith(sql_type), (table, name, sql_type)
            payloads = [name for name, sql_type, _, _ in columns if sql_type == "json"]
            assert payloads, f"{table} declares no payload column at all"
            assert all(definitions[n].startswith("json") for n in payloads)

    def test_the_check_vocabularies_are_the_owning_modules_constants(self):
        """A vocabulary change in an owning module must fail HERE, not diverge silently."""
        assert _check_literals("connection_review_items_decision_check") == AUTOMATION_DECISIONS
        assert _check_literals("connection_review_items_output_status_check") == OUTPUT_STATUSES
        assert (
            _check_literals("connection_review_items_verification_status_check")
            == VERIFICATION_STATUSES
        )

    def test_the_project_status_check_is_the_gate_vocabulary(self):
        """7AK's `project_status` is 7AJ's `workflow.project_decision`, which the composite
        fabrication-output gate assigns from exactly these three values."""
        assert (
            _check_literals("connection_review_snapshots_project_status_check")
            == AUTOMATION_DECISIONS
        )

    def test_the_revision_check_refuses_a_negative_revision(self):
        assert "check (review_revision >= 0)" in _statements()

    def test_no_index_is_created(self):
        assert not re.search(r"(?i)\bcreate\s+(unique\s+)?index\b", _sql())

    def test_rls_is_enabled_with_no_policy(self):
        assert _statements().count("enable row level security") == 2
        assert not re.search(r"(?i)\bcreate\s+policy\b", _sql())

    def test_anon_and_authenticated_hold_nothing_and_the_server_role_cannot_rewrite(self):
        for table in (SNAPSHOT_TABLE, ITEM_TABLE):
            assert (
                f"revoke all on table public.{table} from public, anon, authenticated"
                in _statements()
            )
            assert (
                f"grant insert, select on table public.{table} to service_role" in _statements()
            )
            assert (
                f"revoke update, delete, truncate on table public.{table} from service_role"
                in _statements()
            )

    def test_the_append_only_trigger_covers_both_tables_and_both_operations(self):
        for table in (SNAPSHOT_TABLE, ITEM_TABLE):
            assert f"before update or delete on public.{table}" in _statements()
        assert _statements().count(
            "for each row execute function public.connection_review_append_only()"
        ) == 2
        assert "SNAPSHOT_REFUSED_APPEND_ONLY" in _sql()

    def test_the_writer_is_one_function_taking_the_payloads_as_text(self):
        """The payloads are TEXT parameters cast `::json` inside. Handing the transport a
        parsed object instead would make the payload's key order the transport's promise."""
        signature = re.search(
            r"create or replace function public\.record_connection_review_snapshot\((.*?)\)\s*"
            r"returns void",
            _sql(), re.S,
        )
        assert signature, "the writer function is not declared"
        body = re.sub(r"\s+", " ", signature.group(1))
        assert "p_project_id uuid" in body
        assert "p_review_revision integer" in body
        assert "p_evidence_identity text" in body
        assert "p_evidence_run_ids text" in body
        assert "p_project_status text" in body
        assert "p_items text" in body
        # The casts are `::json`, and the item payloads are extracted with `->` on a `json`
        # value, which returns the ORIGINAL text rather than a normalised one.
        assert "p_evidence_identity::json" in _sql()
        assert "p_evidence_run_ids::json" in _sql()
        assert "json_array_elements(p_items::json)" in _statements()
        assert "::jsonb" not in _statements()

    def test_the_writer_refuses_a_revision_that_is_not_the_next_one(self):
        assert "SNAPSHOT_REFUSED_REVISION_GAP" in _sql()
        assert "coalesce(max(review_revision) + 1, 0)" in _statements()

    def test_the_writer_execute_is_granted_to_the_server_role_only(self):
        assert (
            "grant execute on function public.record_connection_review_snapshot("
            " uuid, integer, text, text, text, text) to service_role" in _statements()
        )
        assert "security definer" not in _statements().lower()

    def test_the_migration_is_one_transaction_that_reloads_the_rest_schema(self):
        lines = _statement_lines()
        assert lines[0] == "begin"
        assert lines[-2:] == ["commit", "notify pgrst, 'reload schema'"]

    def test_the_migration_creates_no_rows_and_touches_no_existing_table(self):
        joined = _statements().lower()
        # The writer inserts, but only into the two tables this migration creates. Every
        # `insert into` in the file must name one of them.
        inserted = set(re.findall(r"insert into public\.([a-z_]+)", joined))
        assert inserted <= {SNAPSHOT_TABLE, ITEM_TABLE}, inserted
        for forbidden in (
            "update public.", "delete from public.", "alter table public.projects",
            "alter table public.steel_members", "alter table public.connections",
            "alter table public.review_items", "alter table public.drawing_sets",
            "alter table public.analysis_runs",
        ):
            assert forbidden not in joined, forbidden
        # The only ALTERs are the two RLS enables, which name the two new tables.
        assert _statements().count("alter table") == 2


# =============================================================================
# 2. THE WRITER — REVISION 0, REVISION 1
# =============================================================================
@needs_real_capture
class TestTheWriterPersistsGenuineRevisions:
    def test_revision_zero_is_persisted_with_its_items(self, fake, revision_zero):
        assert store.persist_review_snapshot(revision_zero, client=fake) is revision_zero
        header, items = revision_zero.to_rows()
        stored = fake.rows(SNAPSHOT_TABLE)
        assert len(stored) == 1
        assert {key: stored[0][key] for key in header} == header
        assert len(fake.rows(ITEM_TABLE)) == len(items) == 3
        for row, expected in zip(fake.rows(ITEM_TABLE), items):
            assert {key: row[key] for key in expected} == expected

    def test_revision_one_is_persisted_after_revision_zero(self, fake, revision_zero,
                                                          revision_one):
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        assert [row["review_revision"] for row in fake.rows(SNAPSHOT_TABLE)] == [0, 1]
        assert len(fake.rows(ITEM_TABLE)) == 6

    def test_the_revision_recorded_is_the_workflows_own(self, revision_one,
                                                        resolved_workflow):
        assert revision_one.review_revision == resolved_workflow.revision == 1

    def test_the_project_is_the_one_that_was_authorized(self, fake, workflow):
        store.record_project_review(
            _binding(), workflow, evidence_rows=NO_EVIDENCE, client=fake,
        )
        assert fake.rows(SNAPSHOT_TABLE)[0]["project_id"] == PROJECT_ID

    def test_the_previous_revision_is_read_from_the_store_not_remembered(self, fake,
                                                                       workflow,
                                                                       revision_zero):
        """The store already holds revision 0, so a workflow still at revision 0 cannot be
        recorded again: the chain's own answer is what the writer builds against."""
        store.persist_review_snapshot(revision_zero, client=fake)
        with pytest.raises(SnapshotRefused) as refused:
            store.record_project_review(
                _binding(), workflow, evidence_rows=NO_EVIDENCE, client=fake,
            )
        assert refused.value.code == SNAPSHOT_REFUSED_REVISION_GAP

    def test_a_resolved_workflow_records_the_next_revision(self, fake, resolved_workflow,
                                                          revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        snapshot = store.record_project_review(
            _binding(), resolved_workflow, evidence_rows=NO_EVIDENCE, client=fake,
        )
        assert snapshot.review_revision == 1
        assert [row["review_revision"] for row in fake.rows(SNAPSHOT_TABLE)] == [0, 1]


@needs_real_capture
class TestTheWriterRefusesWhatTheDatabaseRefuses:
    def test_a_duplicate_revision_is_refused_not_overwritten(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        before = fake.rows(SNAPSHOT_TABLE)
        with pytest.raises(SnapshotRefused) as refused:
            store.persist_review_snapshot(revision_zero, client=fake)
        assert refused.value.code == SNAPSHOT_REFUSED_REVISION_GAP
        assert fake.rows(SNAPSHOT_TABLE) == before

    def test_a_gap_is_refused(self, fake, revision_one):
        with pytest.raises(SnapshotRefused) as refused:
            store.persist_review_snapshot(revision_one, client=fake)
        assert refused.value.code == SNAPSHOT_REFUSED_REVISION_GAP
        assert fake.rows(SNAPSHOT_TABLE) == []

    def test_an_unknown_project_is_refused(self, revision_zero):
        empty = _FakeStore(projects=())
        with pytest.raises(SnapshotRefused) as refused:
            store.persist_review_snapshot(revision_zero, client=empty)
        assert refused.value.code == "SNAPSHOT_REFUSED_PROJECT_UNKNOWN"
        assert empty.rows(SNAPSHOT_TABLE) == []
        assert empty.rows(ITEM_TABLE) == []

    def test_a_vocabulary_violation_is_refused_as_unrepresentable(self, workflow):
        """A status outside the owning module's vocabulary cannot be recorded, and the
        refusal is J21's "cannot be represented" — not a silent coercion to a legal one."""
        fake = _FakeStore()
        broken = dataclasses.replace(workflow, project_decision="LOOKS_FINE")
        snapshot = build_review_snapshot(
            broken, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE, previous_revision=None,
        )
        with pytest.raises(SnapshotRefused) as refused:
            store.persist_review_snapshot(snapshot, client=fake)
        assert refused.value.code == "SNAPSHOT_REFUSED_UNREPRESENTABLE"
        assert fake.rows(SNAPSHOT_TABLE) == []

    def test_a_failure_the_module_cannot_name_is_re_raised_unchanged(self):
        """An unrecognised failure is not dressed up as a J21 refusal."""
        assert store._refusal_from(RuntimeError("boom")) is None
        assert store._refusal_from(_ApiError("08006", "connection failure")) is None

    def test_a_write_that_refuses_leaves_no_header_behind(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        with pytest.raises(SnapshotRefused):
            store.persist_review_snapshot(revision_zero, client=fake)
        assert len(fake.rows(SNAPSHOT_TABLE)) == 1
        assert len(fake.rows(ITEM_TABLE)) == 3

    def test_a_binding_for_another_project_cannot_carry_this_workflow(self, fake, workflow):
        with pytest.raises(SnapshotRefused) as refused:
            store.record_project_review(
                _binding(OTHER_PROJECT_ID), workflow, evidence_rows=NO_EVIDENCE, client=fake,
            )
        assert refused.value.code == SNAPSHOT_REFUSED_PROJECT_MISMATCH
        assert fake.rows(SNAPSHOT_TABLE) == []

    def test_the_writer_builds_no_snapshot_it_was_not_given(self, fake):
        for not_a_snapshot in (None, {}, "RP-0001", 0):
            with pytest.raises(TypeError):
                store.persist_review_snapshot(not_a_snapshot, client=fake)
        assert fake.calls == []


# =============================================================================
# 3. THE TRANSPORT — JSON TEXT, AND NOTHING NORMALISED
# =============================================================================
@needs_real_capture
class TestThePayloadsCrossAsJsonText:
    def test_the_params_are_text_and_the_cast_happens_server_side(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        _marker, _fn, params = fake.writes()[0]
        assert isinstance(params["p_evidence_identity"], str)
        assert isinstance(params["p_evidence_run_ids"], str)
        assert isinstance(params["p_items"], str)
        assert isinstance(params["p_project_status"], str)
        assert json.loads(params["p_items"])[0]["review_package_id"] == "RP-0001"

    def test_the_sent_text_is_what_the_serialiser_produced_verbatim(self, fake,
                                                                  revision_zero):
        """Nothing between the caller's `json.dumps` and the column may touch the text, so
        the text the writer sent is the text the module produced, character for character."""
        header, items = revision_zero.to_rows()
        store.persist_review_snapshot(revision_zero, client=fake)
        _marker, _fn, params = fake.writes()[0]
        assert params["p_evidence_identity"] == store._json_text(header["evidence_identity"])
        assert params["p_evidence_run_ids"] == store._json_text(header["evidence_run_ids"])
        assert params["p_items"] == store._json_text([dict(item) for item in items])

    def test_the_serialiser_preserves_the_key_order_it_is_given(self):
        """Key order is part of 7AK's contract (its display strings are `repr()`), so the
        serialiser must not sort, and it must be deterministic."""
        payload = {"z": 1, "a": {"beta": 2, "alpha": [3, {"q": 4, "b": 5}]}, "m": [{"y": 1}]}
        text = store._json_text(payload)
        assert text == '{"z":1,"a":{"beta":2,"alpha":[3,{"q":4,"b":5}]},"m":[{"y":1}]}'
        assert store._json_text(payload) == store._json_text(copy.deepcopy(payload))

    def test_the_stored_payloads_are_equal_to_the_originals(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        row = fake.rows(SNAPSHOT_TABLE)[0]
        header, items = revision_zero.to_rows()
        assert row["evidence_identity"] == header["evidence_identity"]
        assert row["evidence_run_ids"] == header["evidence_run_ids"]
        for stored, expected in zip(fake.rows(ITEM_TABLE), items):
            for column in (
                "connection_id", "decision", "output_status", "verification_status",
                "last_processed_revision", "blocker_codes", "warning_codes", "ai_readings",
                "evidence", "provenance", "tasks", "generated_files",
            ):
                assert stored[column] == expected[column], column

    def test_a_never_attempted_column_crosses_as_a_present_null(self, fake, revision_zero):
        """The review layer's silence is a NULL VALUE in a column that exists, never a
        missing key: a state recorded as absent must not become a state never recorded."""
        store.persist_review_snapshot(revision_zero, client=fake)
        _marker, _fn, params = fake.writes()[0]
        for item in json.loads(params["p_items"]):
            assert set(item) == {name for name, _, _, _ in ITEM_TABLE_COLUMNS}
        for row in fake.rows(ITEM_TABLE):
            for column in ("connection_id", "output_status", "verification_status",
                           "last_processed_revision"):
                assert column in row
            assert row["output_status"] is None or row["output_status"] in OUTPUT_STATUSES
            assert (row["verification_status"] is None
                    or row["verification_status"] in VERIFICATION_STATUSES)


# =============================================================================
# 4. THE REPLAY — THE STORED ROWS ALONE REBUILD THE GENUINE CONTRACT
# =============================================================================
@needs_real_capture
class TestTheStoredRevisionReplaysThroughJ21:
    """The reference for "what the two tables give back" is J21's own `_round_trip`, which is
    the accepted milestone's statement of that property.

    Note the asymmetry this class is written around, because it is easy to get wrong: a
    snapshot built in memory holds the ENCODED form of its tuples
    (`{"__steelspec_tuple__": [...]}`), while one read back from rows holds the DECODED form
    (real Python tuples), since `snapshot_item_from_row` decodes as it reads. So
    `load(...) == build(...)` is FALSE by design, and so is `load(...).items ==
    build(...).items`. The properties that ARE the design are the ones asserted below: the
    JSON text the rows carry, and the contract those rows rebuild. The contract comparison is
    also asymmetric the other way — `project_contract_from_snapshot` must be handed the
    LOADED form, never a built one, which `test_the_store_never_hands_the_builder_a_built_
    snapshot` pins.
    """

    def test_revision_zero_replays_byte_equivalent(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        replayed = store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        header, rows = replayed.to_rows()
        expected_header, expected_rows = j21._round_trip(revision_zero).to_rows()
        assert header == expected_header
        assert rows == expected_rows

    def test_revision_zero_replays_to_the_genuine_contract(self, fake, workflow,
                                                          revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        replayed = store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        assert project_contract_from_snapshot(replayed) == build_project_review_contract(
            workflow
        )

    def test_revision_one_replays_to_the_genuine_contract(self, fake, resolved_workflow,
                                                         workflow, revision_zero,
                                                         revision_one):
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        replayed = store.load_review_snapshot(PROJECT_ID, 1, client=fake)
        assert project_contract_from_snapshot(replayed) == build_project_review_contract(
            resolved_workflow
        )
        # And revision 0 still rebuilds the contract it was recorded at: the later revision
        # did not travel backwards into the earlier row.
        older = store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        assert project_contract_from_snapshot(older) == build_project_review_contract(
            workflow
        )

    def test_the_human_answer_the_provenance_and_the_tasks_survive(self, fake,
                                                                  revision_zero,
                                                                  revision_one):
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        loaded = store.load_review_snapshot(PROJECT_ID, 1, client=fake)
        replayed = next(
            item for item in loaded.items if item.review_package_id == SELECTED_PACKAGE_ID
        )
        # The reference is J21's own round trip of the same snapshot: the accepted milestone's
        # statement of what the rows give back (see the class docstring on the tuple form).
        original = next(
            item for item in j21._round_trip(revision_one).items
            if item.review_package_id == SELECTED_PACKAGE_ID
        )
        assert replayed.connection_id == "CONN-ARKLES-001"
        assert replayed.tasks == original.tasks
        assert replayed.provenance == original.provenance
        assert replayed.generated_files == original.generated_files
        assert replayed.decision == original.decision
        assert replayed.blocker_codes == original.blocker_codes
        assert replayed.warning_codes == original.warning_codes
        assert replayed.ai_readings == original.ai_readings
        assert replayed.evidence == original.evidence
        assert replayed.last_processed_revision == 1
        assert replayed.output_status == original.output_status
        assert replayed.verification_status == original.verification_status
        assert any(
            task["resolution"] is not None for task in replayed.tasks
        ), "the human's recorded answer is the point of the revision"

    def test_every_payload_column_of_every_item_survives_verbatim(self, fake,
                                                                 revision_zero,
                                                                 revision_one):
        """The complete form of the check above: for EVERY item of both revisions and EVERY
        payload column the design names, the loaded value equals J21's own round trip of the
        same value. A column missed here would be a column whose replay is unproven."""
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        payload_columns = [
            name for name, sql_type, _, _ in ITEM_TABLE_COLUMNS if sql_type == "json"
        ]
        assert payload_columns, "the design names no item payload column"
        for revision, expected in ((0, revision_zero), (1, revision_one)):
            loaded = store.load_review_snapshot(PROJECT_ID, revision, client=fake)
            reference = j21._round_trip(expected)
            assert [i.review_package_id for i in loaded.items] == [
                i.review_package_id for i in reference.items
            ]
            for replayed, original in zip(loaded.items, reference.items):
                for column in payload_columns:
                    assert getattr(replayed, column) == getattr(original, column), (
                        revision, replayed.review_package_id, column,
                    )
            # And the rows themselves — every column, payload and scalar, as plain data.
            assert loaded.to_rows() == reference.to_rows()

    def test_the_store_never_hands_the_builder_a_built_snapshot(self, fake, revision_zero,
                                                               revision_one):
        """The asymmetry the class docstring names, pinned directly: the contract builder
        reads the DECODED form. Given a freshly built snapshot it does not fail — it silently
        produces a contract whose member references are the tuple tag
        (`('__steelspec_tuple__',)`), which is why the store must load before it rebuilds."""
        built = revision_one
        wrong = project_contract_from_snapshot(built)
        right = project_contract_from_snapshot(j21._round_trip(built))
        assert wrong != right, "the asymmetry is gone; this guard is now meaningless"

        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(built, client=fake)
        loaded = store.load_review_snapshot(PROJECT_ID, built.review_revision, client=fake)
        assert project_contract_from_snapshot(loaded) == right
        assert project_contract_from_snapshot(loaded) != wrong

    def test_the_items_come_back_in_package_id_order(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        replayed = store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        ids = [item.review_package_id for item in replayed.items]
        assert ids == ["RP-0001", "RP-0002", "RP-0003"]
        assert ids == sorted(item.review_package_id for item in replayed.items)

    def test_the_replay_reads_only_the_two_review_tables(self, fake, revision_zero):
        """The rows of the review tables are the whole input: nothing reads `connections`,
        `steel_members` or anything else to fill a review field in."""
        store.persist_review_snapshot(revision_zero, client=fake)
        fake.reads.clear()
        store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        assert {table for table, _filters, _columns in fake.reads} == {
            SNAPSHOT_TABLE, ITEM_TABLE,
        }

    def test_a_missing_revision_replays_as_absence(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        assert store.load_review_snapshot(PROJECT_ID, 7, client=fake) is None

    def test_the_replay_never_calls_the_workflows_contract_builder(self):
        """The replay path is J21's own `project_contract_from_snapshot`, and the store does
        not name either builder: the contract is rebuilt by the module that owns rebuilding."""
        assert _functions_mentioning("build_project_review_contract") == []
        assert _functions_mentioning("project_contract_from_snapshot") == []


# =============================================================================
# 5. IMMUTABILITY
# =============================================================================
@needs_real_capture
class TestARevisionIsNeverRewritten:
    def test_revision_zero_is_byte_unchanged_after_revision_one(self, fake, revision_zero,
                                                               revision_one):
        store.persist_review_snapshot(revision_zero, client=fake)
        before_header = fake.rows(SNAPSHOT_TABLE)
        before_items = fake.rows(ITEM_TABLE)
        store.persist_review_snapshot(revision_one, client=fake)
        assert fake.rows(SNAPSHOT_TABLE)[:1] == before_header
        assert fake.rows(ITEM_TABLE)[:3] == before_items
        assert json.dumps(fake.rows(SNAPSHOT_TABLE)[0], sort_keys=True) == json.dumps(
            before_header[0], sort_keys=True
        )
        # Revision 0 still reads back as revision 0 — compared at the JSON text the rows
        # carry, which is where "byte-unchanged" lives (a built snapshot holds the encoded
        # tuple form and a loaded one the decoded form, so the dataclasses themselves are not
        # the comparison; see TestTheStoredRevisionReplaysThroughJ21).
        assert store.load_review_snapshot(PROJECT_ID, 0, client=fake).to_rows() == (
            j21._round_trip(revision_zero).to_rows()
        )
        # …and it is still the contract revision 0 recorded, not revision 1's.
        assert project_contract_from_snapshot(
            store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        ) != project_contract_from_snapshot(
            store.load_review_snapshot(PROJECT_ID, 1, client=fake)
        )

    def test_the_store_exposes_no_update_delete_or_upsert(self):
        source = STORE_PATH.read_text(encoding="utf-8")
        for forbidden in (".update(", ".delete(", "upsert", ".insert(", "drop ", "truncate"):
            assert forbidden not in source.lower(), forbidden
        # J66 added a SECOND writer RPC — the citation writer — and nothing else. The pin stays
        # exact: it is the two calls, in order, each naming its own constant, so a third
        # transport (or a mutation smuggled in behind either name) fails here.
        assert source.count(".rpc(") == 2
        assert re.findall(r"\.rpc\(\s*([A-Za-z_][A-Za-z0-9_]*)", source) == [
            "WRITER_FUNCTION", "CITATION_WRITER_FUNCTION",
        ]
        assert store.CITATION_WRITER_FUNCTION == "record_connection_review_citations"

    def test_the_only_write_is_the_one_writer_function(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        assert [fn for _marker, fn, _params in fake.writes()] == [store.WRITER_FUNCTION]
        assert store.WRITER_FUNCTION == "record_connection_review_snapshot"

    def test_there_is_no_second_revision_counter(self):
        """The revision recorded is 7AJ's own. Nothing here computes, increments or renumbers
        one — the chain's contiguity is J21's rule and the database's guard."""
        source = STORE_PATH.read_text(encoding="utf-8")
        assert "revision + 1" not in source
        assert "revision += " not in source
        assert "next_revision" not in source
        assert "counter" not in source.lower()

    def test_the_current_state_is_the_highest_recorded_revision(self, fake, revision_zero,
                                                               revision_one):
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        state = store.read_connection_review_state(PROJECT_ID, client=fake)
        assert state.code == store.REVIEW_STATE_RECORDED
        assert state.latest_revision == 1
        assert state.revisions == (0, 1)
        # "Current" is the highest RECORDED revision, and its rows are revision 1's rows.
        assert state.snapshot.review_revision == revision_one.review_revision
        assert state.snapshot.to_rows() == j21._round_trip(revision_one).to_rows()

    def test_only_revisions_that_were_recorded_are_listed(self, fake, revision_zero,
                                                         revision_one):
        """A project's chain is exactly the revisions written. Nothing fills a range, so a
        hole would be reported as a hole rather than as a revision nobody wrote."""
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        state = store.read_connection_review_state(PROJECT_ID, client=fake)
        assert state.revisions == tuple(sorted(state.revisions))
        assert state.latest_revision == max(state.revisions)
        assert state.revisions == (0, 1)


# =============================================================================
# 6. STALENESS — THE EVIDENCE DIGEST, AND NOTHING ELSE
# =============================================================================
@needs_real_capture
class TestStalenessIsTheEvidenceDigest:
    def test_an_extraction_rerun_that_changed_evidence_makes_a_revision_stale(self):
        rows = {"steel_members": [{"id": "m1", "review_status": "review"}], "connections": []}
        recorded = build_review_snapshot(
            j21._arkles_workflow(), project_id=PROJECT_ID, evidence_rows=rows,
            previous_revision=None,
        )
        current = evidence_identity({
            "steel_members": [{"id": "m1", "review_status": "extracted"}],
            "connections": [],
        })
        assert snapshot_is_stale(recorded, current_evidence_identity=current) is True

    def test_a_read_that_wrote_no_evidence_leaves_the_revision_current(self):
        """A J17 retry that recorded a run and no evidence adds a RUN ID and changes no
        digest. The run ids are provenance; the digest is the authority."""
        rows = {"steel_members": [{"id": "m1", "review_status": "review"}], "connections": []}
        recorded = build_review_snapshot(
            j21._arkles_workflow(), project_id=PROJECT_ID, evidence_rows=rows,
            evidence_run_ids=("run-1",), previous_revision=None,
        )
        assert recorded.evidence_run_ids == ("run-1",)
        assert snapshot_is_stale(
            recorded, current_evidence_identity=evidence_identity(rows)
        ) is False

    def test_evidence_modified_after_a_human_review_is_readable_and_stale(self, fake,
                                                                        revision_zero,
                                                                        revision_one):
        store.persist_review_snapshot(revision_zero, client=fake)
        store.persist_review_snapshot(revision_one, client=fake)
        replayed = store.load_review_snapshot(PROJECT_ID, 1, client=fake)
        # Readable, verbatim: the rows are unchanged by the evidence moving underneath them.
        assert replayed.to_rows() == j21._round_trip(revision_one).to_rows()
        assert replayed.evidence_identity == revision_one.evidence_identity
        changed = {"steel_members": [], "connections": [{"id": "c9"}]}
        assert snapshot_is_stale(
            replayed, current_evidence_identity=evidence_identity(changed)
        ) is True
        # And the staleness is the DIGEST's, not a re-derivation of the review: the human's
        # recorded answer is still there to be read.
        assert any(task["resolution"] is not None for task in replayed.items[0].tasks)

    def test_a_different_kind_of_identity_is_refused_rather_than_compared(self,
                                                                        revision_zero):
        with pytest.raises(ValueError):
            snapshot_is_stale(
                revision_zero,
                current_evidence_identity={
                    "evidence_kind": "SOMETHING_ELSE", "evidence_tables": [],
                    "evidence_digest": "0" * 64,
                },
            )

    def test_a_stale_revision_is_never_silently_rewritten(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        before = fake.rows(SNAPSHOT_TABLE)
        with pytest.raises(SnapshotRefused):
            store.persist_review_snapshot(revision_zero, client=fake)
        assert fake.rows(SNAPSHOT_TABLE) == before

    def test_the_fingerprint_tables_are_the_ones_the_evidence_reader_reads(self):
        assert EVIDENCE_TABLES == ("steel_members", "connections")
        assert store.project_evidence_rows.__doc__

    def test_the_fingerprint_kind_is_the_persisted_one(self, revision_zero):
        assert revision_zero.evidence_identity["evidence_kind"] == EVIDENCE_KIND
        assert set(revision_zero.evidence_identity) == {
            "evidence_kind", "evidence_tables", "evidence_digest",
        }


# =============================================================================
# 7. ISOLATION AND THE AUTHORIZATION BOUNDARY
# =============================================================================
@needs_real_capture
class TestTheAuthorizationBoundaryIsUnchanged:
    def test_the_writer_requires_a_j19_binding(self, fake, workflow):
        for not_a_binding in (None, PROJECT_ID, object(), {"project_id": PROJECT_ID}):
            with pytest.raises(TypeError):
                store.record_project_review(
                    not_a_binding, workflow, evidence_rows=NO_EVIDENCE, client=fake,
                )
        assert fake.calls == []

    def test_a_binding_can_only_be_produced_by_j19s_authorization(self):
        """The type the writer demands cannot be constructed by an unauthorized caller:
        J19's binder refuses any decision that is not ALLOWED."""
        with pytest.raises(BindingRefused):
            bind_project_review(
                identity=_identity(),
                record=_review_record(),
                decision=AccessDecision(
                    allowed=False, code="ACCESS_NOT_OWNER", detail="not the owner",
                    project_id=PROJECT_ID,
                ),
            )

    def test_the_writer_takes_no_owner_role_or_user_argument(self):
        tree = _store_tree()
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        identifiers |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not identifiers & {
            "user_id", "owner", "owner_id", "role", "token", "sub", "email", "claims",
        }
        parameters = {
            argument.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            for argument in node.args.args + node.args.kwonlyargs
        }
        assert not parameters & {"user_id", "owner", "role", "token"}
        assert list(inspect.signature(store.record_project_review).parameters) == [
            "binding", "workflow", "evidence_rows", "evidence_run_ids", "client",
        ]

    def test_the_store_never_verifies_a_token_itself(self):
        source = STORE_PATH.read_text(encoding="utf-8")
        for forbidden in ("jwt", "jose", "bearer", "https://", "http://", "Authorization"):
            assert forbidden not in source, forbidden
        assert "production_review.authorization" not in source
        assert "production_review.binding" in source

    def test_the_read_filters_by_project_at_the_database(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        fake.reads.clear()
        store.load_review_snapshot(PROJECT_ID, 0, client=fake)
        assert fake.reads
        assert all(
            ("project_id", PROJECT_ID) in filters for _table, filters, _columns in fake.reads
        )

    def test_reading_another_projects_state_is_isolated(self, fake, revision_zero):
        store.persist_review_snapshot(revision_zero, client=fake)
        other = store.read_connection_review_state(OTHER_PROJECT_ID, client=fake)
        assert other.code == store.NO_PERSISTED_CONNECTION_REVIEW_STATE
        assert other.snapshot is None
        assert other.latest_revision is None
        assert store.load_review_snapshot(OTHER_PROJECT_ID, 0, client=fake) is None

    def test_a_project_id_is_required_and_never_guessed(self, fake):
        for bad in (None, "", "   ", 0, 7):
            with pytest.raises(ValueError):
                store.read_connection_review_state(bad, client=fake)
        assert fake.calls == []


# =============================================================================
# 8. EXISTING PROJECTS HAVE NO REVIEW STATE, AND NOTHING INVENTS ONE
# =============================================================================
class TestExistingProjectsHaveNoReviewState:
    def test_a_project_with_no_rows_reads_as_an_explicit_absence(self, fake):
        state = store.read_connection_review_state(PROJECT_ID, client=fake)
        assert state.code == store.NO_PERSISTED_CONNECTION_REVIEW_STATE
        assert state.snapshot is None
        assert state.revisions == ()
        assert state.has_review_state is False
        assert state.latest_revision is None

    def test_the_absence_is_a_code_and_never_an_empty_review(self, fake):
        """The distinction that matters: an existing project with no recorded review is
        reported as having NONE, never as a review with nothing in it — so a caller cannot
        display an empty review that looks like an answer."""
        state = store.read_connection_review_state(PROJECT_ID, client=fake)
        assert state.code == "NO_PERSISTED_CONNECTION_REVIEW_STATE"
        assert state.snapshot is None and state.revisions == ()
        assert not state.has_review_state

    def test_the_reads_that_could_reconstruct_a_review_do_not_exist(self):
        """No function here takes a project id and returns review state built from
        engineering tables: the one call to J21's builder is inside `record_project_review`,
        whose second parameter is a genuine `ProjectWorkflowState`."""
        assert _functions_mentioning("build_review_snapshot") == ["record_project_review"]
        code = _store_code().lower()
        for absent in ("from_connections", "rebuild", "reconstruct", "backfill",
                       "synthesise", "synthesize"):
            assert absent not in code, absent

    def test_the_snapshot_builder_refuses_anything_but_a_workflow(self):
        with pytest.raises(TypeError):
            build_review_snapshot(object(), project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE)

    def test_the_only_engineering_readers_serve_the_digest_and_nothing_else(self):
        assert _functions_mentioning("member_rows_for_project") == ["project_evidence_rows"]
        assert _functions_mentioning("connection_rows_for_project") == [
            "project_evidence_rows"
        ]

    def test_the_evidence_reader_returns_rows_and_decides_nothing(self):
        """`project_evidence_rows` reads through the existing repository readers and returns
        what they return. It reads no review table and produces no decision."""
        source = ast.unparse(_strip_docstrings(
            next(
                node for node in ast.walk(_store_tree())
                if isinstance(node, ast.FunctionDef) and node.name == "project_evidence_rows"
            )
        ))
        for forbidden in ("decision", "status", "provenance", "task", "ai_readings",
                          "SNAPSHOT_TABLE", "ITEM_TABLE"):
            assert forbidden not in source, forbidden
        assert "member_rows_for_project" in source
        assert "connection_rows_for_project" in source

    def test_the_store_owns_no_intake_ledger_and_no_child_table(self):
        code = _store_code()
        for forbidden in ("review_items", "insert_connection", "steel_members_from",
                          "evidence_rows_for_page", "drawings_for_drawing_set",
                          "connection_plates", "bolt_groups", "weld_details"):
            assert forbidden not in code, forbidden


# =============================================================================
# 9. THE PRODUCTION BOUNDARY
# =============================================================================
class TestTheProductionBoundaryIsUnchanged:
    def test_no_route_was_added(self, production):
        assert _declared_routes(production.main.app) == FROZEN_ROUTES

    def test_the_store_has_exactly_one_consumer(self):
        """Every consumer of the store is named here, and which side of it they use.

        J25's read surface reads it and writes nothing. J46's resumer reads the persisted
        chain and writes nothing. J47's route is the FIRST writer side consumer this
        application has: it is the module that reaches `record_project_review`, and it
        does so without owning a second builder, a second chain or a second revision
        counter — the store still has exactly one writer PATH, and one consumer of it.
        J50's opening operation is the SECOND, and it reaches the same writer: it records
        the reconstruction's revision-0 baseline through `record_project_review`, reads
        the head and reads the baseline back — the same three calls, no fourth path.
        The list stays exact, so a further consumer is declared rather than absorbed."""
        importers = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if "connection_review_repository" in path.read_text():
                importers.append(str(path.relative_to(REPO)))
        assert importers == [
            "app/production_connection_review.py",
            # J46: the resumer reads the persisted chain to rebuild the current workflow.
            # It reads only — `latest_recorded_revision` and `load_review_snapshot` — and
            # writes nothing to the store. Declared here, as this test requires.
            "app/production_review/project_workflow_resumption.py",
            # J50: the baseline-opening operation. It reaches the same writer side —
            # `record_project_review` — for the baseline J47's first resolve records over,
            # and reads `latest_recorded_revision`, `project_evidence_rows` and
            # `load_review_snapshot`. It owns no snapshot, no chain and no revision rule.
            "app/production_review_opening.py",
            # J47: the route that records a review. It calls the store's own
            # `record_project_review` and `latest_recorded_revision` — the writer side and
            # the reader side — and re-implements neither the snapshot nor the chain.
            "app/production_review_resolution.py",
        ], importers

    def test_the_store_opens_no_connection_at_import(self):
        """The production client is imported on use, not at module import: importing this
        module must not open a client."""
        source = STORE_PATH.read_text(encoding="utf-8")
        assert "from app.supabase_client import supabase" in source
        assert source.index("def _client(") < source.index("from app.supabase_client")
        local_imports = {
            node.module
            for node in ast.walk(_store_tree())
            if isinstance(node, ast.ImportFrom) and node.col_offset > 0 and node.module
        }
        assert "app.supabase_client" in local_imports

    def test_the_store_imports_no_web_framework_and_no_database_driver(self):
        """Read against the code, not the prose: this module's docstrings explain what it does
        NOT reach for, and one of them names `requests` to do so."""
        code = _store_code().lower()
        for forbidden in ("fastapi", "flask", "starlette", "requests", "httpx",
                          "psycopg", "sqlalchemy", "create_client"):
            assert forbidden not in code, forbidden

    def test_j13_remains_the_only_status_authority(self):
        code = _store_code()
        for forbidden in ("derive_project_status", "projects.status", "review_status_of"):
            assert forbidden not in code, forbidden

    def test_the_store_does_not_touch_the_retry_or_coverage_contract(self):
        code = _store_code()
        for forbidden in ("page_windows", "parse_failures", "page_coverage",
                          "retry_extraction", "page_exception", "analysis_runs"):
            assert forbidden not in code, forbidden

    def test_no_dependency_was_added(self):
        requirements = (REPO / "requirements.txt").read_text(encoding="utf-8").lower()
        for absent in ("psycopg", "sqlalchemy", "alembic", "psycopg2"):
            assert absent not in requirements, absent

    def test_the_store_composes_j21_rather_than_restating_it(self):
        """Every refusal this module raises before the database is J21's own code, and every
        builder it calls is J21's own builder."""
        from app.cad_engine import connection_review_snapshot as model

        # The store names refusal codes only to MAP the database's SQLSTATEs onto them, so
        # every code it writes in code must be one the model itself defines. A code this
        # module invented would be a second refusal vocabulary.
        defined = {
            value for name, value in vars(model).items()
            if name.startswith("SNAPSHOT_REFUSED_") and isinstance(value, str)
        }
        used = set(re.findall(r"SNAPSHOT_REFUSED_[A-Z_]+", _store_code()))
        assert used, "the store names no refusal code at all"
        assert used <= defined, used - defined
        assert store.build_review_snapshot is model.build_review_snapshot
        assert store.snapshot_from_rows is model.snapshot_from_rows
        assert not hasattr(store, "evidence_identity")
        assert not hasattr(store, "snapshot_is_stale")

    def test_j19_is_still_the_only_authorization_module(self):
        rule = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if "def authorize_project" in path.read_text():
                rule.append(str(path.relative_to(REPO)))
        assert rule == ["app/production_review/authorization.py"], rule


# =============================================================================
# 10. THE LIVE DEPLOYMENT — READ-ONLY
# =============================================================================
@pytest.fixture(scope="module")
def live_client(production):
    """The real deployment's client. Skips — never substitutes — when the configuration is
    the test placeholder or the network is unavailable."""
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live-deployment tests "
                    "are skipped rather than run against a synthetic database")
    client = production.repository.supabase
    try:
        client.table(SNAPSHOT_TABLE).select("*").execute()
        client.table(ITEM_TABLE).select("*").execute()
    except Exception as exc:  # network/credentials unavailable in this process
        pytest.skip(f"the live review tables could not be read: {type(exc).__name__}")
    return client


class TestTheLiveDeployment:
    def test_both_tables_are_exposed_on_the_rest_surface_and_empty(self, live_client):
        """A readable table that returns no rows is the surface working; a table that is
        absent raises. The migration backfilled nothing and no writer has run against a real
        project, so both tables are empty."""
        assert live_client.table(SNAPSHOT_TABLE).select("project_id").execute().data == []
        assert live_client.table(ITEM_TABLE).select("project_id").execute().data == []

    def test_a_write_against_an_unknown_project_is_refused_live(self, live_client):
        """The one call this file makes against the real writer is one that cannot succeed:
        a random project id fails the foreign key, so nothing is written. It proves the
        deployed function is reachable through the REST surface and refuses."""
        with pytest.raises(Exception) as refused:
            live_client.rpc(store.WRITER_FUNCTION, {
                "p_project_id": str(uuid.uuid4()),
                "p_review_revision": 0,
                "p_evidence_identity": store._json_text({
                    "evidence_kind": EVIDENCE_KIND,
                    "evidence_tables": list(EVIDENCE_TABLES),
                    "evidence_digest": "0" * 64,
                }),
                "p_evidence_run_ids": "[]",
                "p_project_status": "REVIEW",
                "p_items": "[]",
            }).execute()
        assert getattr(refused.value, "code", None) == "23503"
        assert live_client.table(SNAPSHOT_TABLE).select("*").execute().data == []

    def test_the_read_path_answers_for_a_project_that_has_none(self, live_client):
        """Against the real deployment, a project with no rows reads as the explicit
        absence — the state every existing project is in today."""
        state = store.read_connection_review_state(str(uuid.uuid4()), client=live_client)
        assert state.code == store.NO_PERSISTED_CONNECTION_REVIEW_STATE
        assert state.snapshot is None
        assert state.revisions == ()
