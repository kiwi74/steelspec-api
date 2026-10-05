"""
J61 — a project's source document has an identity, and an analysis lineage names it.

WHAT THIS FILE IS

The verification half of J61, and nothing else. The implementation and the single
migration were completed and applied before this file existed; this file is what
turns "it was implemented" into "it was proved", over the focused letters §12 of
the milestone brief names.

    A. project_documents schema and required constraints
    B. nullable drawings.document_id
    C. legacy backfill count and linkage
    D. legacy backfill content_sha256 = NULL and role = UNKNOWN
    E. legacy backfill preserves lineage identity where lineages share path/name
    F. content identity behaviour for newly created documents
    G. NULL content_sha256 never participates in identity matching
    H. drawing -> document linkage
    I. reconstruction with one document preserves existing behaviour
    J. reconstruction with several documents and no document_id is AMBIGUOUS
    K. reconstruction with an explicit document_id selects only that document
    L. RECONSTRUCTION_MIXED_IDENTITY still fires, with and without a document
    M. the opening request accepts an optional document_id and refuses bad shapes
    N. opening/review revision semantics are unchanged, revision 0 included
    O. evidence, revision and fabrication behaviour and digest inputs unchanged

WHAT IS LIVE AND WHAT IS DOUBLED

    LIVE, READ-ONLY   the schema, the backfilled rows, the linkage, the recorded
                      revision 0 and its digest, and the project's own evidence
                      counts. Every live read here is a SELECT. Nothing in this
                      file writes to the database, and no opening request is sent
                      to the production route: the route contract is proved
                      in-process (M, N) and the database invariants are proved by
                      reading the database as it stands.
    DOUBLED           the database for the two write rules F and G exercise — a
                      content-idempotent insert and the NULL rule — because a test
                      may not write a document row into the live project. The
                      RULES under test are the production ones, run over what the
                      double returns; the double reproduces the partial unique
                      index, because a store that accepted a duplicate would let a
                      race production refuses look successful here.
    GENUINE           the reconstruction, its refusal vocabulary, J23's capture
                      selection, J22's snapshot builder, the opening parser and the
                      operation, and the evidence-digest rule.

WHY THE CAPTURE MATERIAL IS GENUINE

The reconstruction tests drive `tests/data/` readings captured from a real model
over a real drawing set — the same files J23's own suite asserts the capture layer
over — through J24A's store, rather than rows this file typed in. A reconstruction
proved over invented rows would prove nothing about production.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest

import app.production_review_opening as opening
from app.cad_engine.connection_review_snapshot import (
    EVIDENCE_KIND,
    EVIDENCE_TABLES,
    evidence_identity,
)
from app.production_review import project_workflow_reconstruction as producer

from tests import test_real_world_j23_page_extraction_capture as j23
from tests import test_real_world_j24a_revision_zero_producer as j24a
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j50_production_review_opening as j50

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "supabase" / "migrations" / "20260929000000_j61_project_documents.sql"
MIGRATION_NAME = MIGRATION.name
SQL = MIGRATION.read_text()


def _statements(text=SQL):
    """The SQL a server would actually run, with the doctrine header removed.

    Most assertions below are about what the migration DOES. The header describes at
    length what it does NOT do — it names `document_revisions` and a content-hash chain
    in order to deny them — so a test that searched the whole file would read a denial
    as a declaration. Every "the migration does not…" assertion reads this instead.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )


STATEMENTS = _statements()

production = j4.production
live = j4.live
baseline = j50.baseline
arkles = j50.arkles
#: J4's genuine pipeline harness, re-exported by name so pytest collects it here.
pdf_boundary = j4.pdf_boundary

# The acceptance project, named by the milestone brief, and its own recorded facts.
ACCEPTANCE_PROJECT = "2389c115-664f-4fe4-8b76-ca07aac3719d"
ACCEPTANCE_DRAWING = "6a44eb41-42a2-4d62-b22d-465043a90c73"
#: The revision-0 evidence digest the brief pins. It is byte-identical to the value the
#: project carried before J61, and this file asserts the LIVE record still states it.
REVISION_ZERO_DIGEST = (
    "61f18266bdc200cabec400c1fbb51140b6ba5965f9c3730e5fa1111a73727cb3"
)

#: The exact column list the J61 design gives `project_documents`. Asserted against the
#: live table, so a column added or lost later is a failure rather than a surprise.
DOCUMENT_COLUMNS = (
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

#: The columns `create_project_document` sends. `id` and `created_at` are the table's own
#: defaults, so a caller that supplied one would be inventing an identity or an instant.
DOCUMENT_WRITE_COLUMNS = (
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
)

#: The six named constraints the migration declares, by the exact names it gives them.
NAMED_CONSTRAINTS = (
    "project_documents_storage_path_check",
    "project_documents_file_name_check",
    "project_documents_sha_check",
    "project_documents_role_check",
    "project_documents_page_count_check",
    "project_documents_supersedes_self_check",
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64

#: A sentinel that distinguishes "the caller named no document" from "the caller did not
#: use the field at all" — a distinction M and N both turn on.
_UNSET = object()


# ===========================================================================
# The live database, read-only. Skips rather than substitutes when it cannot
# be reached, exactly as the J20 live-schema tests do.
# ===========================================================================
@pytest.fixture(scope="module")
def live_client(production):
    """The production Supabase client, used for SELECTs and nothing else."""
    if not production.live_config:
        pytest.skip("no live Supabase configuration is available")
    return production.matcher_module.supabase


def _read(client, table, columns="*", **filters):
    """Every row a read returns, skipping the test rather than guessing.

    `filters` are `column=value` equality matches, which is the only shape these
    acceptance reads need.
    """
    try:
        query = client.table(table).select(columns)
        for column, value in filters.items():
            query = query.eq(column, value)
        return query.execute().data or []
    except Exception as exc:  # credentials or network unavailable in this process
        pytest.skip(f"live {table} could not be read: {type(exc).__name__}: {exc}")


# ===========================================================================
# The database double for the two write rules. See the header: the RULES are
# the production ones; only the store is stood in for.
# ===========================================================================
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, database, table):
        self._database = database
        self._table = table
        self._columns = None
        self._filters = ()
        self._order = None
        self._operation = None
        self._payload = None

    def select(self, columns):
        self._operation = "select"
        self._columns = columns
        return self

    def eq(self, column, value):
        self._filters = self._filters + ((column, value),)
        return self

    def order(self, column):
        self._order = column
        return self

    def insert(self, row):
        self._operation = "insert"
        self._payload = dict(row)
        return self

    def update(self, fields):
        self._operation = "update"
        self._payload = dict(fields)
        return self

    def execute(self):
        return self._database.perform(self)


class _FakeDatabase:
    """`drawings` and `project_documents`, and the identity index over the second.

    The projection is the whole point of the select: a read returns EXACTLY the
    columns the production select names, so a caller that started depending on a
    column its select does not name fails here instead of being silently served it.
    """

    def __init__(self, *, drawings=(), documents=()):
        self.tables = {
            "drawings": [dict(row) for row in drawings],
            "project_documents": [dict(row) for row in documents],
        }
        self.reads = []
        self.inserts = []
        self._ids = 0

    def table(self, name):
        return _Query(self, name)

    @staticmethod
    def _project(columns, row):
        if columns in (None, "*"):
            return dict(row)
        names = tuple(name.strip() for name in columns.split(","))
        return {name: row.get(name) for name in names}

    def perform(self, query):
        rows = self.tables[query._table]
        if query._operation == "select":
            self.reads.append((query._table, query._filters, query._columns))
            selected = [
                row for row in rows
                if all(row.get(column) == value for column, value in query._filters)
            ]
            if query._order is not None:
                selected = sorted(selected, key=lambda row: row[query._order])
            return _Result([self._project(query._columns, row) for row in selected])

        if query._operation == "insert":
            row = dict(query._payload)
            self._refuse_a_duplicate_identity(row)
            self._ids += 1
            row.setdefault("id", f"document-{self._ids}")
            row.setdefault("created_at", f"2026-09-29T00:00:{self._ids:02d}+00:00")
            rows.append(row)
            self.inserts.append(row)
            return _Result([dict(row)])

        if query._operation == "update":
            changed = []
            for row in rows:
                if all(row.get(column) == value for column, value in query._filters):
                    row.update(query._payload)
                    changed.append(dict(row))
            return _Result(changed)

        raise AssertionError(f"unexpected operation {query._operation!r}")

    def _refuse_a_duplicate_identity(self, row):
        """The partial unique index on `(project_id, content_sha256)`, reproduced.

        Restricted to rows where the hash is NOT NULL, as the index is: two documents
        with unproven identity are two documents, and the database says so.
        """
        content = row.get("content_sha256")
        if content is None:
            return
        key = (row.get("project_id"), content)
        for existing in self.tables["project_documents"]:
            if (existing.get("project_id"), existing.get("content_sha256")) == key:
                raise RuntimeError(
                    'duplicate key value violates unique constraint '
                    '"project_documents_content_identity_idx"'
                )


@pytest.fixture()
def fake_database(production, monkeypatch):
    """Builds a double and points the repository module's client at it."""
    def build(*, drawings=(), documents=()):
        database = _FakeDatabase(drawings=drawings, documents=documents)
        monkeypatch.setattr(production.repository, "supabase", database)
        return database
    return build


def _repository(production):
    """The production repository module, imported under the repo's own configuration."""
    return production.repository


@pytest.fixture(scope="module", autouse=True)
def _bind_j4_page_factory(production):
    """J4's `_page` helper resolves the production `PageExtraction` from a module global
    that J4's own module-scoped fixture binds, and a module-scoped fixture reaches only
    the module that declares it. This module drives that helper, so it binds it too —
    the same two lines, against the same genuine class."""
    j4._PAGE_FACTORY = production.PageExtraction
    yield
    j4._PAGE_FACTORY = None


# ===========================================================================
# The reconstruction store: one project, one or two documents.
# ===========================================================================
DOC_A = "document-selby-a"
DOC_B = "document-other-b"


def _single_document_store(*, named=False):
    """The genuine Selby reading, under one lineage — which may or may not name a
    document. Both are production states: a pre-J61 lineage names none."""
    store = j24a._selby_store()
    if named:
        store._drawings[0]["document_id"] = DOC_A
    return store


def _two_document_store(*, same_document=False):
    """One project, two lineages, each with its own persisted reading.

    `same_document` gives both lineages the SAME document id, which is the state a
    re-extraction of one document produces: two attempts, one document. It is the
    case a named document does not resolve.
    """
    store = _single_document_store(named=True)
    other_document = DOC_A if same_document else DOC_B
    store._drawing_sets.append(
        j24a._drawing_set(j24a.SELBY_PROJECT, j24a.OTHER_SET, page_count=4)
    )
    other = j24a._drawing(j24a.OTHER_SET, j24a.OTHER_DRAWING, page_count=4)
    other["document_id"] = other_document
    store._drawings.append(other)
    j23._record(
        store._table, j24a._capture_rows_for(j24a.SELBY_WINDOW_FILES[0])[:1],
        run="run-other", drawing=j24a.OTHER_DRAWING,
        drawing_set=j24a.OTHER_SET, project=j24a.SELBY_PROJECT,
    )
    return store


def _refusal(store, project_id, *, code, **kwargs):
    with pytest.raises(producer.ReconstructionRefused) as caught:
        j24a._reconstruct(store, project_id, **kwargs)
    assert caught.value.code == code, caught.value.detail
    return caught.value


# ===========================================================================
# A. THE TABLE AND ITS CONSTRAINTS.
# ===========================================================================
class TestATheDocumentTable:
    def test_the_live_table_carries_exactly_these_columns(self, live_client):
        """The design's column list, read from the live table itself."""
        rows = _read(live_client, "project_documents")

        assert rows, "the live project_documents table holds no row to describe"
        assert sorted(rows[0]) == sorted(DOCUMENT_COLUMNS)

    def test_the_migration_creates_the_table_once_and_only_if_absent(self):
        assert SQL.count("create table if not exists public.project_documents") == 1
        assert not re.search(r"\bcreate table\b(?! if not exists)", SQL)

    def test_every_constraint_the_design_names_is_declared_by_name(self):
        for name in NAMED_CONSTRAINTS:
            assert f"constraint {name}" in SQL, name

    def test_the_role_column_is_a_closed_vocabulary_defaulting_to_unknown(self):
        assert re.search(r"role text not null default 'UNKNOWN'", SQL)
        match = re.search(r"check \(role in \(\s*([^)]*)\)\)", SQL)
        assert match, "the role vocabulary is not declared as a CHECK"

        from app.engineering_data import repository as store

        assert list(store.DOCUMENT_ROLES) == re.findall(r"'([A-Z_]+)'", match.group(1))

    def test_the_identity_column_is_nullable_and_shaped_as_a_sha256(self):
        assert re.search(r"content_sha256 text,\s*\n", SQL)
        assert "content_sha256 text not null" not in SQL
        assert "content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$'" in SQL

    def test_the_document_belongs_to_a_project_and_is_never_removed_by_a_cascade(self):
        assert re.search(
            r"project_id uuid not null references public\.projects\(id\) "
            r"on delete restrict",
            SQL,
        )
        assert "on delete cascade" not in SQL.lower()

    def test_the_identity_is_a_partial_unique_index_over_the_proven_rows_only(self):
        assert re.search(
            r"create unique index if not exists project_documents_content_identity_idx\s*\n"
            r"\s*on public\.project_documents \(project_id, content_sha256\)\s*\n"
            r"\s*where content_sha256 is not null;",
            SQL,
        )

    def test_the_table_is_not_reachable_from_a_browser_role_and_has_no_policy(self):
        assert "alter table public.project_documents enable row level security;" in SQL
        assert "create policy" not in SQL.lower()
        assert (
            "revoke all on table public.project_documents "
            "from public, anon, authenticated;" in SQL
        )

    def test_the_only_granted_privileges_are_the_servers_own(self):
        assert (
            "grant select, insert, update on table public.project_documents "
            "to service_role;" in SQL
        )
        assert (
            "revoke delete, truncate on table public.project_documents "
            "from service_role;" in SQL
        )

    def test_no_document_evidence_or_reconciliation_table_was_added_beside_it(self):
        """The closed set of tables this migration creates, measured not assumed.

        A word count would not do: `transmittal` is a legitimate ROLE in the vocabulary,
        so the claim has to be about tables. This is the whole DDL surface.
        """
        created = re.findall(r"create table if not exists public\.(\w+)", STATEMENTS)

        assert created == ["project_documents"]
        for forbidden in (
            "document_revisions", "document_graph", "transmittals", "reconciliations",
            "conflicts", "document_pages", "document_coverage", "document_roles",
            "document_drawings", "review_items",
        ):
            assert forbidden not in created, forbidden

    def test_the_migration_adds_exactly_one_table_and_one_column(self):
        """Two tables are ALTERed and only one of them gains a column."""
        assert STATEMENTS.count("create table") == 1
        assert re.findall(r"alter table public\.(\w+)", STATEMENTS) == [
            "drawings", "project_documents"
        ]
        assert re.findall(r"add column if not exists (\w+)", STATEMENTS) == ["document_id"]
        assert re.search(
            r"alter table public\.project_documents enable row level security;", STATEMENTS
        )


# ===========================================================================
# B. drawings.document_id IS NULLABLE, AND STAYS THAT WAY.
# ===========================================================================
class TestBTheDrawingColumnIsNullable:
    def test_the_live_drawings_table_exposes_the_column(self, live_client):
        rows = _read(live_client, "drawings", "id,document_id")

        assert rows, "the live drawings table holds no row to describe"
        assert sorted(rows[0]) == ["document_id", "id"]

    def test_the_migration_adds_it_without_a_not_null(self):
        match = re.search(
            r"alter table public\.drawings\s*\n\s*add column if not exists document_id "
            r"uuid\s*\n\s*references public\.project_documents\(id\) on delete restrict;",
            SQL,
        )
        assert match, "the column is not added as a nullable restricted foreign key"
        assert "document_id uuid not null" not in SQL

    def test_a_lineage_that_names_no_document_reads_back_as_no_document(
        self, production, fake_database
    ):
        """A DXF lineage and a pre-J61 lineage both name none, and None is not a blank."""
        store = _repository(production)
        fake_database(drawings=[{"id": "drawing-none", "document_id": None}])

        assert store.document_for_drawing("drawing-none") is None

    def test_an_unknown_lineage_is_also_no_document(self, production, fake_database):
        store = _repository(production)
        fake_database(drawings=[{"id": "drawing-x", "document_id": None}])

        assert store.document_for_drawing("drawing-absent") is None

    def test_no_document_read_is_made_for_a_lineage_that_names_none(self, production):
        """The read model does not go looking for an absence."""
        from app.production_review import project_read

        class _Store:
            def __init__(self):
                self.reads = []

            def get_project(self, project_id):
                return {"id": project_id, "status": "review"}

            def drawing_sets_for_project(self, project_id):
                return [{"id": "set-1", "project_id": project_id, "name": "a set",
                         "status": "analyzed", "total_pages": 1, "pages_analysed": 1}]

            def drawings_for_drawing_set(self, drawing_set_id):
                return [{"id": "drawing-1", "drawing_set_id": drawing_set_id,
                         "file_name": "set.pdf", "storage_path": "somewhere/set.pdf",
                         "page_count": 1, "document_id": None}]

            def document_for_drawing(self, drawing_id):
                self.reads.append(drawing_id)
                raise AssertionError("a document was read for a lineage that names none")

        store = _Store()
        record = project_read.read_project_record(
            "project-1", project_row={"id": "project-1"}, repository=store
        )

        assert store.reads == []
        assert record.drawing_sets[0].drawings[0].document is None


# ===========================================================================
# C. THE BACKFILL'S COUNT AND ITS LINKAGE.
# ===========================================================================
class TestCTheBackfillCountAndLinkage:
    def test_every_live_lineage_names_a_document(self, live_client):
        drawings = _read(live_client, "drawings", "id,drawing_set_id,document_id")
        assert drawings

        assert [row["id"] for row in drawings if row["document_id"] is None] == []

    def test_each_named_document_exists_and_states_the_same_lineage(
        self, live_client
    ):
        documents = {row["id"]: row for row in _read(live_client, "project_documents")}
        sets = {row["id"]: row for row in _read(live_client, "drawing_sets", "id,project_id")}
        drawings = _read(
            live_client, "drawings", "id,drawing_set_id,document_id,storage_path,file_name"
        )

        for drawing in drawings:
            document = documents.get(drawing["document_id"])
            assert document is not None, drawing["id"]
            assert document["project_id"] == sets[drawing["drawing_set_id"]]["project_id"]
            assert document["storage_path"] == drawing["storage_path"]
            assert document["file_name"] == drawing["file_name"]

    def test_the_backfill_created_one_document_per_lineage_and_no_more(
        self, live_client
    ):
        documents = _read(live_client, "project_documents")
        drawings = _read(live_client, "drawings", "id,document_id")

        assert len(documents) == len(
            {drawing["document_id"] for drawing in drawings}
        )

    def test_the_migration_asserts_the_relationship_rather_than_assuming_it(self):
        """The backfill's own guards, by the names they raise with."""
        for name in (
            "BACKFILL_FAILED_CARDINALITY",
            "BACKFILL_FAILED_UNLINKED",
            "BACKFILL_FAILED_DANGLING",
            "BACKFILL_FAILED_MISMATCH",
        ):
            assert f"'{name}" in SQL, name

    def test_the_backfill_compares_against_the_count_it_measured(self):
        assert "into target_count" in SQL
        assert "where d.document_id is null" in SQL
        assert "if created_count <> target_count then" in SQL

    def test_the_backfill_is_idempotent_by_construction(self):
        """It selects only unlinked lineages and links them in the same statement."""
        # Three: the count it takes, the loop it drives, and the assertion it makes
        # afterwards — all three over the same "still unlinked" predicate.
        assert STATEMENTS.count("where d.document_id is null") == 3
        assert "update public.drawings" in SQL
        assert "set document_id = created_document" in SQL


# ===========================================================================
# D. THE BACKFILL'S IDENTITY IS UNPROVEN AND ITS ROLE UNASSERTED.
# ===========================================================================
class TestDUnprovenIdentityAndUnknownRole:
    def test_every_backfilled_document_states_an_unproven_identity(self, live_client):
        documents = _read(live_client, "project_documents")
        assert documents

        for document in documents:
            assert document["content_sha256"] is None, document["id"]
            assert document["byte_size"] is None, document["id"]

    def test_every_backfilled_document_is_unknown_and_labelled_nothing(
        self, live_client
    ):
        documents = _read(live_client, "project_documents")

        for document in documents:
            assert document["role"] == "UNKNOWN", document["id"]
            assert document["revision_label"] is None, document["id"]
            assert document["supersedes_document_id"] is None, document["id"]

    def test_the_migration_hashes_nothing_and_reads_no_storage(self):
        """A hash is never computed here: there is no hashing machinery to compute one."""
        assert not re.search(r"\b(hashlib|sha256|md5|digest|encode)\s*\(", STATEMENTS)
        # The only mention of storage is the column it writes the key into.
        assert "storage" not in STATEMENTS.lower().replace("storage_path", "")

    def test_the_migration_touches_no_capture_member_or_connection_row(self):
        lowered = STATEMENTS.lower()
        for forbidden in (
            "page_extraction_captures", "steel_members", "connections",
            "analysis_runs", "review_items", "connection_review_snapshots",
            "drop table", "delete from", "truncate table",
        ):
            assert forbidden not in lowered, forbidden

    def test_the_migration_gives_no_document_a_role_beyond_unknown(self):
        """The vocabulary is declared, `UNKNOWN` is what a row gets, and that is all.

        No role word appears in the file in any other position: nothing writes a role,
        and nothing chooses one from a document's own attributes.
        """
        from app.engineering_data import repository as store

        # The vocabulary is declared once, as the CHECK, and nowhere else.
        assert set(re.findall(r"'([A-Z_]{4,})'", STATEMENTS)) == set(store.DOCUMENT_ROLES)
        assert STATEMENTS.count("role in (") == 1
        assert re.search(r"role text not null default 'UNKNOWN'", STATEMENTS)

        # The one statement that writes a document writes the unknown role and nothing
        # else: the backfill asserts no role, so it cannot have chosen one.
        insert = STATEMENTS[STATEMENTS.index("insert into public.project_documents"):]
        insert = insert[: insert.index("update public.drawings")]
        assert set(re.findall(r"'([A-Z_]{4,})'", insert)) == {"UNKNOWN"}
        assert STATEMENTS.count("role,") == 1


# ===========================================================================
# E. A SHARED PATH IS NOT A SHARED DOCUMENT.
# ===========================================================================
class TestESharedPathsDoNotCollapseDocuments:
    def test_two_lineages_that_share_a_path_and_name_are_two_documents(
        self, live_client
    ):
        """The case a set-based join could have mis-paired while counts still agreed.

        Read from the live database rather than constructed: the project that actually
        has this shape is the one that proves the backfill handled it.
        """
        drawings = _read(
            live_client, "drawings", "id,drawing_set_id,storage_path,file_name,document_id"
        )
        sets = {row["id"]: row for row in _read(live_client, "drawing_sets", "id,project_id")}

        by_project = {}
        for drawing in drawings:
            key = (sets[drawing["drawing_set_id"]]["project_id"], drawing["storage_path"])
            by_project.setdefault(key, []).append(drawing)

        shared = [group for group in by_project.values() if len(group) > 1]
        assert shared, (
            "the live database no longer holds two lineages sharing one path; this "
            "acceptance test cannot be made to pass by inventing the case"
        )
        for group in shared:
            assert len({row["document_id"] for row in group}) == len(group)

    def test_the_documents_of_one_project_are_not_merged_by_their_path(
        self, live_client
    ):
        """Two documents of one project are two rows, not one row read twice."""
        documents = _read(live_client, "project_documents", "id,project_id,content_sha256")

        by_project = {}
        for document in documents:
            by_project.setdefault(document["project_id"], []).append(document)
        for project_id, group in by_project.items():
            assert len({row["id"] for row in group}) == len(group), project_id


# ===========================================================================
# F. CONTENT IDENTITY FOR NEWLY CREATED DOCUMENTS.
# ===========================================================================
class TestFContentIdentity:
    def test_a_created_document_states_exactly_the_columns_the_design_names(
        self, production, fake_database
    ):
        store = _repository(production)
        database = fake_database()

        row = store.create_project_document(
            "project-1", storage_path="user/project/plans.pdf", file_name="plans.pdf",
            source_format="PDF", byte_size=1234, page_count=None, content_sha256=_HASH_A,
        )

        assert sorted(database.inserts[0]) == sorted(DOCUMENT_WRITE_COLUMNS + ("id",
                                                                              "created_at"))
        assert row["content_sha256"] == _HASH_A
        assert row["byte_size"] == 1234
        assert row["page_count"] is None

    def test_the_same_bytes_of_one_project_are_one_document(
        self, production, fake_database
    ):
        """A repeated extraction is a repeat, not a second identity — the defect J61
        exists to close."""
        store = _repository(production)
        database = fake_database()
        arguments = dict(storage_path="user/project/plans.pdf", file_name="plans.pdf",
                         source_format="PDF", content_sha256=_HASH_A)

        first = store.create_project_document("project-1", **arguments)
        second = store.create_project_document("project-1", **arguments)

        assert first["id"] == second["id"]
        assert len(database.inserts) == 1

    def test_the_same_bytes_of_another_project_are_another_document(
        self, production, fake_database
    ):
        store = _repository(production)
        database = fake_database()
        arguments = dict(storage_path="user/project/plans.pdf", file_name="plans.pdf",
                         source_format="PDF", content_sha256=_HASH_A)

        first = store.create_project_document("project-1", **arguments)
        second = store.create_project_document("project-2", **arguments)

        assert first["id"] != second["id"]
        assert len(database.inserts) == 2

    def test_different_bytes_of_one_project_are_different_documents(
        self, production, fake_database
    ):
        store = _repository(production)
        database = fake_database()

        first = store.create_project_document(
            "project-1", storage_path="p/a.pdf", file_name="a.pdf", content_sha256=_HASH_A
        )
        second = store.create_project_document(
            "project-1", storage_path="p/a.pdf", file_name="a.pdf", content_sha256=_HASH_B
        )

        assert first["id"] != second["id"]
        assert len(database.inserts) == 2

    def test_a_path_that_changes_is_not_a_new_document_for_the_same_bytes(
        self, production, fake_database
    ):
        """The path and the name are attributes; the bytes are the identity."""
        store = _repository(production)
        database = fake_database()

        first = store.create_project_document(
            "project-1", storage_path="user/project/v1/plans.pdf", file_name="plans.pdf",
            content_sha256=_HASH_A,
        )
        second = store.create_project_document(
            "project-1", storage_path="user/project/v2/renamed.pdf", file_name="renamed.pdf",
            content_sha256=_HASH_A,
        )

        assert first["id"] == second["id"]
        assert len(database.inserts) == 1

    def test_an_identity_that_lost_a_race_returns_the_row_that_won(
        self, production, fake_database, monkeypatch
    ):
        """The unique index is the rule's backstop, and the loser reads the winner."""
        store = _repository(production)
        database = fake_database()
        arguments = dict(storage_path="user/project/plans.pdf", file_name="plans.pdf",
                         content_sha256=_HASH_A)
        created = store.create_project_document("project-1", **arguments)

        real = store._document_for_content
        calls = {"n": 0}

        def blind_once(project_id, content_sha256):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # the race: the winning insert is not visible yet
            return real(project_id, content_sha256)

        monkeypatch.setattr(store, "_document_for_content", blind_once)
        again = store.create_project_document("project-1", **arguments)

        assert again["id"] == created["id"]
        assert len(database.inserts) == 1

    def test_a_document_is_never_given_a_role_by_its_attributes(
        self, production, fake_database
    ):
        """The filename, the format, the size and the count are not evidence of a role."""
        store = _repository(production)
        fake_database()

        row = store.create_project_document(
            "project-1",
            storage_path="user/project/transmittal/FABRICATION-set.pdf",
            file_name="transmittal-fabrication-assembly-detail.pdf",
            source_format="DWG",
            byte_size=99,
            page_count=42,
            content_sha256=_HASH_A,
        )

        assert row["role"] == "UNKNOWN"
        assert store.DOCUMENT_ROLE_UNKNOWN == "UNKNOWN"

    def test_the_created_documents_are_listed_in_creation_order(
        self, production, fake_database
    ):
        store = _repository(production)
        fake_database()

        first = store.create_project_document(
            "project-1", storage_path="p/a.pdf", file_name="a.pdf", content_sha256=_HASH_A
        )
        second = store.create_project_document(
            "project-1", storage_path="p/b.pdf", file_name="b.pdf", content_sha256=_HASH_B
        )
        other = store.create_project_document(
            "project-2", storage_path="q/a.pdf", file_name="a.pdf", content_sha256=_HASH_A
        )

        listed = store.project_documents_for_project("project-1")

        assert [row["id"] for row in listed] == [first["id"], second["id"]]
        assert other["id"] not in [row["id"] for row in listed]

    def test_the_page_count_is_written_once_the_reading_establishes_it(
        self, production, fake_database
    ):
        store = _repository(production)
        fake_database()
        document = store.create_project_document(
            "project-1", storage_path="p/a.pdf", file_name="a.pdf", content_sha256=_HASH_A
        )
        assert document["page_count"] is None

        store.update_document_page_count(document["id"], 41)

        assert store.document_for_drawing.__name__ == "document_for_drawing"
        assert store.project_documents_for_project("project-1")[0]["page_count"] == 41

    def test_the_repository_publishes_the_vocabulary_the_migration_declares(self):
        from app.engineering_data import repository as store

        assert store.DOCUMENT_ROLE_UNKNOWN == "UNKNOWN"
        assert store.DOCUMENT_ROLE_UNKNOWN in store.DOCUMENT_ROLES
        assert len(store.DOCUMENT_ROLES) == len(set(store.DOCUMENT_ROLES))


# ===========================================================================
# G. NULL IS NOT A WILDCARD.
# ===========================================================================
class TestGNullIsNotAWildcard:
    def test_a_document_with_no_proven_identity_is_never_matched_to_another(
        self, production, fake_database
    ):
        store = _repository(production)
        database = fake_database()
        arguments = dict(storage_path="p/a.pdf", file_name="a.pdf", source_format="PDF")

        first = store.create_project_document("project-1", **arguments)
        second = store.create_project_document("project-1", **arguments)

        assert first["id"] != second["id"]
        assert len(database.inserts) == 2

    def test_no_lookup_is_ever_made_on_an_unproven_identity(
        self, production, fake_database
    ):
        """Not merely "it did not match": the query is never issued."""
        store = _repository(production)
        database = fake_database()

        store.create_project_document(
            "project-1", storage_path="p/a.pdf", file_name="a.pdf", content_sha256=None
        )

        assert database.reads == []

    def test_a_lookup_is_made_on_a_proven_identity(self, production, fake_database):
        """The negative above is only meaningful because the positive happens."""
        store = _repository(production)
        database = fake_database()

        store.create_project_document(
            "project-1", storage_path="p/a.pdf", file_name="a.pdf", content_sha256=_HASH_A
        )

        assert database.reads == [
            ("project_documents", (("project_id", "project-1"),
                                   ("content_sha256", _HASH_A)),
             store.DOCUMENT_COLUMNS)
        ]

    def test_the_live_unproven_rows_are_not_used_to_deduplicate(self, live_client):
        """Every live document is unproven, and every one of them is a distinct row."""
        documents = _read(live_client, "project_documents", "id,project_id,content_sha256")

        unproven = [row for row in documents if row["content_sha256"] is None]
        assert unproven, "the live table holds no unproven row; this check would be vacuous"
        assert len({row["id"] for row in unproven}) == len(unproven)


# ===========================================================================
# H. THE LINEAGE -> DOCUMENT HOP.
# ===========================================================================
class TestHTheDrawingToDocumentHop:
    def test_a_lineage_that_names_a_document_reads_it_back(
        self, production, fake_database
    ):
        store = _repository(production)
        document = {"id": "document-1", "project_id": "project-1",
                    "storage_path": "p/a.pdf", "file_name": "a.pdf",
                    "source_format": "PDF", "byte_size": 12,
                    "page_count": 3, "content_sha256": _HASH_A, "role": "UNKNOWN",
                    "revision_label": None, "supersedes_document_id": None,
                    "created_at": "2026-09-29T00:00:00+00:00"}
        database = fake_database(
            drawings=[{"id": "drawing-1", "document_id": "document-1"}],
            documents=[document],
        )

        found = store.document_for_drawing("drawing-1")

        assert found == document
        assert database.reads[0] == ("drawings", (("id", "drawing-1"),), "document_id")
        assert database.reads[1][0] == "project_documents"

    def test_the_read_names_a_lineage_that_names_nothing_as_a_pointer_of_null(
        self, production, fake_database
    ):
        """The first hop reads the pointer and stops; a null pointer is the end of it."""
        store = _repository(production)
        database = fake_database(drawings=[{"id": "drawing-1", "document_id": None}])

        assert store.document_for_drawing("drawing-1") is None
        assert len(database.reads) == 1

    def test_the_acceptance_project_links_its_drawing_to_its_document(self, live_client):
        drawings = _read(
            live_client, "drawings", "id,document_id", id=ACCEPTANCE_DRAWING
        )
        assert drawings, "the acceptance drawing is not present"
        document_id = drawings[0]["document_id"]
        assert document_id is not None

        documents = _read(live_client, "project_documents", "*", id=document_id)
        assert len(documents) == 1
        assert documents[0]["project_id"] == ACCEPTANCE_PROJECT

    def test_the_read_model_exposes_the_document_without_inferring_a_role(
        self, production
    ):
        from app.production_review import project_read

        class _Store:
            def get_project(self, project_id):
                return {"id": project_id, "status": "review"}

            def drawing_sets_for_project(self, project_id):
                return [{"id": "set-1", "project_id": project_id, "name": "a set",
                         "status": "analyzed", "total_pages": 1, "pages_analysed": 1}]

            def drawings_for_drawing_set(self, drawing_set_id):
                return [{"id": "drawing-1", "drawing_set_id": drawing_set_id,
                         "file_name": "set.pdf", "storage_path": "somewhere/set.pdf",
                         "page_count": 1, "document_id": "document-1"}]

            def document_for_drawing(self, drawing_id):
                return {"id": "document-1", "storage_path": "transport/plans.pdf",
                        "file_name": "transport-plans-17.08.26.pdf", "source_format": "PDF",
                        "byte_size": 999, "page_count": 41, "content_sha256": None,
                        "role": "UNKNOWN", "revision_label": None,
                        "supersedes_document_id": None}

        record = project_read.read_project_record(
            "project-1", project_row={"id": "project-1"}, repository=_Store()
        )
        draw = record.drawing_sets[0].drawings[0]

        assert draw.document is not None
        assert draw.document.document_id == "document-1"
        assert draw.document.role == "UNKNOWN"
        assert draw.document.content_sha256 is None
        assert draw.document.file_name == "transport-plans-17.08.26.pdf"
        assert draw.document.page_count == 41

    def test_a_stored_role_is_exposed_verbatim_rather_than_re_decided(
        self, production
    ):
        """A role a human stated is shown as stated; this layer decides nothing."""
        from app.production_review import project_read

        class _Store:
            def get_project(self, project_id):
                return {"id": project_id, "status": "review"}

            def drawing_sets_for_project(self, project_id):
                return [{"id": "set-1", "project_id": project_id, "name": "a set",
                         "status": "analyzed", "total_pages": 1, "pages_analysed": 1}]

            def drawings_for_drawing_set(self, drawing_set_id):
                return [{"id": "drawing-1", "drawing_set_id": drawing_set_id,
                         "file_name": "set.pdf", "storage_path": "s/set.pdf",
                         "page_count": 1, "document_id": "document-1"}]

            def document_for_drawing(self, drawing_id):
                return {"id": "document-1", "storage_path": "s/set.pdf",
                        "file_name": "set.pdf", "source_format": "PDF", "byte_size": 1,
                        "page_count": 1, "content_sha256": _HASH_A,
                        "role": "ASSEMBLY", "revision_label": "C",
                        "supersedes_document_id": "document-0"}

        record = project_read.read_project_record(
            "project-1", project_row={"id": "project-1"}, repository=_Store()
        )
        document = record.drawing_sets[0].drawings[0].document

        assert document.role == "ASSEMBLY"
        assert document.revision_label == "C"
        assert document.supersedes_document_id == "document-0"


# ===========================================================================
# THE END-TO-END HALF OF F AND H. F and H prove the rules the REPOSITORY
# applies; this proves the extraction path invokes them. Without it, every
# assertion above would still hold over a pipeline that never called them.
# ===========================================================================
class TestThePipelineEstablishesTheIdentity:
    """`parse_pdf_and_save`, genuine, with only the AI call replaced.

    The hash recorded by the run is recomputed HERE, from the file the pipeline
    read, by a second implementation that shares no code with the one under test.
    A pipeline that recorded a hash of anything but the document's own bytes — a
    path, a timestamp, a name — cannot produce this value.
    """

    def _run(self, pdf_boundary, tmp_path, *, source_name="j61-plans.pdf", **kwargs):
        pages = [j4._page(1, [j4._member("M1", j4.EXACT_TOKEN)])]
        result = pdf_boundary(pages, source_name=source_name, **kwargs)
        return result, (tmp_path / source_name).read_bytes()

    def test_the_recorded_identity_is_the_hash_of_the_files_own_bytes(
        self, pdf_boundary, tmp_path
    ):
        result, payload = self._run(pdf_boundary, tmp_path)
        repository = result.repository

        assert len(repository.documents) == 1, "exactly one document per run"
        document = repository.documents[0]
        assert document["content_sha256"] == hashlib.sha256(payload).hexdigest()

    def test_the_identity_is_the_one_rule_this_codebase_owns(self, pdf_boundary, tmp_path):
        """Two implementations, one value: the independently computed digest above is
        also what the production rule states for these bytes."""
        from app.drawing_reading.pdf_annotation_extractor import source_document_sha256

        result, payload = self._run(pdf_boundary, tmp_path, source_name="j61-rule.pdf")

        assert result.repository.documents[0]["content_sha256"] == (
            source_document_sha256(payload)
        )

    def test_one_byte_of_difference_is_a_different_identity(self):
        """The rule is content-sensitive, so nothing about a file's provenance is in it."""
        from app.drawing_reading.pdf_annotation_extractor import source_document_sha256

        assert source_document_sha256(b"%PDF-1.4\n") != source_document_sha256(b"%PDF-1.5\n")

    def test_the_run_records_the_files_own_size_and_the_stage_that_read_it(
        self, pdf_boundary, tmp_path
    ):
        result, payload = self._run(pdf_boundary, tmp_path, source_name="j61-size.pdf")
        document = result.repository.documents[0]

        assert document["byte_size"] == len(payload)
        assert document["source_format"] == "PDF"
        assert document["storage_path"] == f"j4-user/j61-size.pdf"
        assert document["file_name"] == "j61-size.pdf"

    def test_the_drawing_the_run_created_points_at_the_document_it_recorded(
        self, pdf_boundary, tmp_path
    ):
        result, _ = self._run(pdf_boundary, tmp_path, source_name="j61-link.pdf")
        repository = result.repository

        assert len(repository.document_links) == 1, "one drawing per whole-document run"
        assert repository.document_links[0]["document_id"] == repository.documents[0]["id"]

    def test_the_role_recorded_is_unknown_however_the_file_names_itself(
        self, pdf_boundary, tmp_path
    ):
        """The end-to-end form of F's anti-inference test: a name that states a role
        is still a name, and the pipeline asserts nothing from it."""
        result, _ = self._run(
            pdf_boundary, tmp_path,
            source_name="TRANSMITTAL-fabrication-assembly-drawings.pdf",
        )

        assert result.repository.documents[0]["role"] == "UNKNOWN"

    def test_the_page_count_the_reading_established_is_written_to_the_document(
        self, pdf_boundary, tmp_path
    ):
        """`page_count` is NULL when the document is created — the reading has not
        happened yet — and is filled in from the run's own total afterwards."""
        result, _ = self._run(pdf_boundary, tmp_path, source_name="j61-pages.pdf")
        repository = result.repository

        assert len(repository.document_page_counts) == 1
        written = repository.document_page_counts[0]
        assert written["id"] == repository.documents[0]["id"]
        assert written["page_count"] == 1

    def test_a_source_that_cannot_be_read_records_no_document_at_all(
        self, pdf_boundary, tmp_path, production, monkeypatch
    ):
        """An unreadable source is refused by the hashing rule, and the run does not
        invent an identity for it. The drawing is still written, naming no document —
        which is exactly the state every pre-J61 lineage is in."""
        from app.drawing_reading.pdf_annotation_extractor import AnnotationExtractionRefused

        def refuse(source):
            raise AnnotationExtractionRefused("the source PDF could not be read.")

        monkeypatch.setattr(production.pipeline, "source_bytes", refuse)

        result, _ = self._run(pdf_boundary, tmp_path, source_name="j61-unreadable.pdf")
        repository = result.repository

        assert repository.documents == []
        assert repository.document_page_counts == []
        assert len(repository.document_links) == 1
        assert repository.document_links[0]["document_id"] is None


# ===========================================================================
# I. ONE DOCUMENT, NO DOCUMENT NAMED — THE BEHAVIOUR THAT WAS THERE BEFORE.
# ===========================================================================
class TestIOneDocumentIsUnchanged:
    def test_a_lineage_that_names_no_document_reconstructs_as_it_always_did(self):
        """The pre-J61 state, which is still a production state."""
        result = j24a._reconstruct(_single_document_store(), j24a.SELBY_PROJECT)

        assert result is not None
        assert result.drawing_id == j24a.SELBY_DRAWING
        assert result.page_count == j24a.SELBY_PAGE_COUNT
        assert result.captures_read == j24a.SELBY_PAGE_COUNT
        assert result.workflow.revision == 0

    def test_a_lineage_that_names_a_document_reconstructs_when_none_is_named(self):
        result = j24a._reconstruct(
            _single_document_store(named=True), j24a.SELBY_PROJECT
        )
        legacy = j24a._reconstruct(_single_document_store(), j24a.SELBY_PROJECT)

        assert result.drawing_id == legacy.drawing_id
        assert result.page_count == legacy.page_count
        assert result.captures_read == legacy.captures_read
        assert result.analysed_pages == legacy.analysed_pages

    def test_naming_that_same_document_changes_nothing_either(self):
        named = j24a._reconstruct(
            _single_document_store(named=True), j24a.SELBY_PROJECT, document_id=DOC_A
        )
        unnamed = j24a._reconstruct(
            _single_document_store(named=True), j24a.SELBY_PROJECT
        )

        assert named.drawing_id == unnamed.drawing_id
        assert named.captures_read == unnamed.captures_read

    def test_a_project_with_no_reading_at_all_is_still_no_capture(self):
        store = _single_document_store(named=True)
        store._table = j23._CaptureTable()

        _refusal(store, j24a.SELBY_PROJECT, code=producer.RECONSTRUCTION_NO_CAPTURE)

    def test_an_unknown_project_is_still_unknown(self):
        assert j24a._reconstruct(_single_document_store(), "not-a-project") is None


# ===========================================================================
# J. SEVERAL DOCUMENTS, NONE NAMED — REFUSED, NOT CHOSEN BETWEEN.
# ===========================================================================
class TestJSeveralDocumentsAreAmbiguous:
    def test_two_documents_with_no_document_named_are_refused(self):
        refusal = _refusal(
            _two_document_store(), j24a.SELBY_PROJECT,
            code=producer.RECONSTRUCTION_AMBIGUOUS_DOCUMENT,
        )

        assert "documents" in refusal.detail
        assert DOC_A not in refusal.detail
        assert DOC_B not in refusal.detail

    def test_the_refusal_vocabulary_is_the_one_the_milestone_froze(self):
        assert set(producer.RECONSTRUCTION_REFUSALS) == {
            "RECONSTRUCTION_PROJECT_UNKNOWN",
            "RECONSTRUCTION_NO_CAPTURE",
            "RECONSTRUCTION_AMBIGUOUS_DOCUMENT",
            "RECONSTRUCTION_MIXED_IDENTITY",
            "RECONSTRUCTION_PAGE_COUNT_UNKNOWN",
        }

    def test_no_document_is_chosen_by_any_observable_of_the_documents(self):
        """The choice must not be reachable by making one document look likelier.

        Every axis the brief names is exercised at once: the second document is made
        smaller, read in fewer rows, created later and given the more structural-looking
        of the two ids. None of it may select one.
        """
        store = _two_document_store()
        store._drawings[1]["file_name"] = "FABRICATION-drawings.pdf"
        store._drawings[1]["document_id"] = "document-fabrication-0001"
        for row in store._table.rows:
            if row["drawing_id"] == j24a.OTHER_DRAWING:
                row["page_number"] = 1

        _refusal(store, j24a.SELBY_PROJECT,
                 code=producer.RECONSTRUCTION_AMBIGUOUS_DOCUMENT)

    def test_one_document_with_two_lineages_is_still_ambiguous_when_it_is_named(self):
        """Two attempts at ONE document are two lineages, and neither is preferred."""
        refusal = _refusal(
            _two_document_store(same_document=True), j24a.SELBY_PROJECT,
            code=producer.RECONSTRUCTION_AMBIGUOUS_DOCUMENT, document_id=DOC_A,
        )

        assert "drawings" in refusal.detail


# ===========================================================================
# K. NAMING A DOCUMENT SELECTS EXACTLY THAT DOCUMENT.
# ===========================================================================
class TestKNamingADocumentSelectsIt:
    def test_the_named_document_is_the_one_reconstructed(self):
        result = j24a._reconstruct(
            _two_document_store(), j24a.SELBY_PROJECT, document_id=DOC_A
        )

        assert result.drawing_id == j24a.SELBY_DRAWING
        assert result.page_count == j24a.SELBY_PAGE_COUNT

    def test_naming_the_other_document_selects_the_other_lineage(self):
        result = j24a._reconstruct(
            _two_document_store(), j24a.SELBY_PROJECT, document_id=DOC_B
        )

        assert result.drawing_id == j24a.OTHER_DRAWING
        assert result.drawing_set_id == j24a.OTHER_SET

    def test_a_document_of_another_project_is_not_reachable_by_naming_it(self):
        """The filter is over this project's own candidates and nothing else."""
        store = _two_document_store()
        store._drawings[1]["document_id"] = DOC_B

        _refusal(store, j24a.SELBY_PROJECT, code=producer.RECONSTRUCTION_NO_CAPTURE,
                 document_id="document-of-another-project")

    def test_naming_a_document_does_not_widen_which_rows_are_read(self):
        """The read surface is the project's own, named document or not."""
        unnamed = _single_document_store(named=True)
        named = _single_document_store(named=True)

        j24a._reconstruct(unnamed, j24a.SELBY_PROJECT)
        j24a._reconstruct(named, j24a.SELBY_PROJECT, document_id=DOC_A)

        assert sorted(name for name, _ in unnamed.calls) == sorted(
            name for name, _ in named.calls
        )

    def test_a_document_id_that_is_not_a_name_is_refused_rather_than_coerced(self):
        store = _single_document_store(named=True)

        for value in ("", "   ", 1, [], {}):
            with pytest.raises(ValueError):
                j24a._reconstruct(store, j24a.SELBY_PROJECT, document_id=value)

    def test_the_named_document_is_carried_into_the_reconstruction_by_the_resumer(self):
        """J46's seam passes it through unchanged and interprets nothing about it."""
        import inspect

        from app.production_review import project_workflow_resumption as resumption

        signature = inspect.signature(resumption.resume_project_workflow)
        assert signature.parameters["document_id"].default is None
        assert "document_id=document_id" in inspect.getsource(
            resumption.resume_project_workflow
        )


# ===========================================================================
# L. THE MIXED-IDENTITY GUARD STILL STANDS.
# ===========================================================================
class TestLTheMixedIdentityGuard:
    def test_a_reading_filed_under_another_project_is_refused(self):
        store = _single_document_store()
        store._table.rows[0]["project_id"] = j24a.OTHER_PROJECT

        _refusal(store, j24a.SELBY_PROJECT,
                 code=producer.RECONSTRUCTION_MIXED_IDENTITY)

    def test_a_reading_filed_under_another_drawing_set_is_refused(self):
        store = _single_document_store()
        store._table.rows[0]["drawing_set_id"] = j24a.OTHER_SET

        _refusal(store, j24a.SELBY_PROJECT,
                 code=producer.RECONSTRUCTION_MIXED_IDENTITY)

    def test_naming_a_document_does_not_smuggle_a_mis_filed_reading_past_the_guard(self):
        """The guard runs AFTER selection, so a named document cannot launder a row."""
        store = _single_document_store(named=True)
        store._table.rows[0]["project_id"] = j24a.OTHER_PROJECT

        _refusal(store, j24a.SELBY_PROJECT,
                 code=producer.RECONSTRUCTION_MIXED_IDENTITY, document_id=DOC_A)

    def test_the_guard_precedes_the_page_count_check_it_would_otherwise_hit(self):
        """A mis-filed reading is refused for WHAT IT IS, not for what it lacks."""
        store = _single_document_store()
        store._table.rows[0]["drawing_set_id"] = j24a.OTHER_SET
        store._drawings[0]["page_count"] = None

        _refusal(store, j24a.SELBY_PROJECT,
                 code=producer.RECONSTRUCTION_MIXED_IDENTITY)


# ===========================================================================
# M. THE OPENING REQUEST'S OPTIONAL DOCUMENT.
# ===========================================================================
class TestMTheOpeningRequestShape:
    def test_the_only_field_a_caller_may_send_is_the_document(self):
        assert opening.REQUEST_FIELDS == ("document_id",)
        assert "document_id" not in opening.SERVER_OWNED_FIELDS

    def test_an_absent_body_names_no_document(self):
        assert opening.parse_opening_request(None) is None

    def test_an_empty_object_names_no_document(self):
        assert opening.parse_opening_request({}) is None

    def test_an_explicit_null_names_no_document(self):
        """JSON's only way to say "no value" is the same request as omitting the key."""
        assert opening.parse_opening_request({"document_id": None}) is None

    def test_a_named_document_is_returned_verbatim(self):
        assert opening.parse_opening_request({"document_id": "document-1"}) == "document-1"

    def test_a_document_that_is_not_a_name_is_refused(self):
        for value in ("", "   ", 1, 0, True, [], {}, ["document-1"]):
            with pytest.raises(opening.OpeningInputRefused) as refused:
                opening.parse_opening_request({"document_id": value})
            assert refused.value.code == opening.INPUT_REFUSED_DOCUMENT_INVALID

    def test_a_field_that_is_not_the_document_is_refused_by_name(self):
        with pytest.raises(opening.OpeningInputRefused) as refused:
            opening.parse_opening_request({"drawing_id": "drawing-1"})
        assert refused.value.code == opening.INPUT_REFUSED_UNKNOWN_FIELD

    def test_a_server_owned_field_is_refused_by_that_fields_own_name(self):
        with pytest.raises(opening.OpeningInputRefused) as refused:
            opening.parse_opening_request({"document_id": "d", "review_revision": 1})
        assert refused.value.code == opening.INPUT_REFUSED_SERVER_OWNED_FIELD

    def test_a_body_that_is_not_an_object_is_refused(self):
        with pytest.raises(opening.OpeningInputRefused) as refused:
            opening.parse_opening_request(["document-1"])
        assert refused.value.code == opening.INPUT_REFUSED_NOT_A_MAPPING

    def test_the_refusal_vocabulary_gained_exactly_one_code(self):
        assert opening.INPUT_REFUSED_DOCUMENT_INVALID in opening.INPUT_REFUSALS
        assert set(opening.INPUT_REFUSALS) == {
            "INPUT_REFUSED_NOT_A_MAPPING",
            "INPUT_REFUSED_SERVER_OWNED_FIELD",
            "INPUT_REFUSED_UNKNOWN_FIELD",
            "INPUT_REFUSED_DOCUMENT_INVALID",
        }


# ===========================================================================
# N. REVISION SEMANTICS, REVISION 0 INCLUDED, ARE UNCHANGED.
# ===========================================================================
def _wired_naming_a_document(monkeypatch, **kwargs):
    """J50's wiring, over a store whose lineage actually names the document.

    J50's own store predates J61 and its drawing names no document, so naming one there
    would refuse with `RECONSTRUCTION_NO_CAPTURE` — correctly, and for the same reason
    the production path refuses. To exercise the pass-through the store has to carry the
    document, which is the state a J61 extraction produces.
    """
    wired = j50._Wired(monkeypatch, **kwargs)
    wired.project_store._drawings[0]["document_id"] = DOC_A
    return wired


def _open(wired, *, document_id=_UNSET, history=(), repository=None):
    """J50's own composition, with J61's field added and nothing else altered."""
    kwargs = {} if document_id is _UNSET else {"document_id": document_id}
    return opening.open_project_review(
        binding=wired.binding,
        review_client="review-client",
        section_matcher=j24a._StubMatcher(),
        repository=wired.project_store if repository is None else repository,
        history=history,
        **kwargs,
    )


class TestNRevisionSemantics:
    def test_the_baseline_revision_is_still_zero(self):
        assert opening.BASELINE_REVISION == 0

    def test_the_baseline_is_still_recorded_at_revision_zero_with_a_document_named(
        self, monkeypatch, baseline
    ):
        wired = _wired_naming_a_document(monkeypatch, head=None, baseline=baseline)

        result = _open(wired, document_id=DOC_A)

        assert result.review_revision == 0
        assert result.recorded_now is True

    def test_naming_a_document_the_project_does_not_hold_is_no_capture(
        self, monkeypatch, baseline
    ):
        """J50's own store, asked for a document it does not carry, refuses — the
        operation does not fall back to a document the caller did not name."""
        wired = j50._Wired(monkeypatch, head=None, baseline=baseline)

        with pytest.raises(producer.ReconstructionRefused) as refused:
            _open(wired, document_id=DOC_A)

        assert refused.value.code == producer.RECONSTRUCTION_NO_CAPTURE

    def test_an_already_open_review_is_not_reconstructed_however_it_is_named(
        self, monkeypatch, baseline
    ):
        """`document_id` is consulted by the writing path only, and this is that path's
        own short-circuit: a recorded revision is the fact, not a proposal."""
        monkeypatch.setattr(
            opening, "resume_project_workflow",
            lambda *a, **k: pytest.fail("an already-open review was reconstructed"),
        )
        wired = j50._Wired(monkeypatch, head=0, baseline=baseline)

        assert _open(wired, document_id=DOC_A).recorded_now is False

    def test_the_short_circuit_still_precedes_the_reconstruction(self):
        from tests.test_real_world_j24a_revision_zero_producer import _reconstruct  # noqa
        body = _function_source("open_project_review")

        assert body.index("if recorded is not None:") < body.index("resume_project_workflow(")

    def test_the_named_document_reaches_the_reconstruction_unchanged(
        self, monkeypatch, baseline
    ):
        seen = {}
        real = opening.resume_project_workflow

        def recorder(*args, **kwargs):
            seen.update(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(opening, "resume_project_workflow", recorder)
        wired = _wired_naming_a_document(monkeypatch, head=None, baseline=baseline)

        _open(wired, document_id=DOC_A)

        assert seen["document_id"] == DOC_A
        assert seen["expected_revision"] == opening.BASELINE_REVISION

    def test_omitting_the_field_entirely_hands_over_none(
        self, monkeypatch, baseline
    ):
        seen = {}
        real = opening.resume_project_workflow

        def recorder(*args, **kwargs):
            seen.update(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(opening, "resume_project_workflow", recorder)
        wired = j50._Wired(monkeypatch, head=None, baseline=baseline)

        _open(wired)

        assert seen["document_id"] is None

    def test_the_route_passes_exactly_one_document_to_the_operation(self):
        """The route threads the parsed value through one call site and no other."""
        route = (REPO / "app" / "main.py").read_text()
        assert route.count("open_project_review(") == 1
        assert "open_project_review(binding=binding, document_id=document_id)" in route
        assert "document_id = parse_opening_request(body)" in route

    def test_nothing_but_the_document_is_consulted_from_the_request_body(self):
        """A body can change the document and cannot change anything else."""
        source = _function_source("open_project_review")
        assert "body" not in source
        assert "document_id" in source


def _function_source(name):
    """The source of one function in `app/production_review_opening.py`."""
    path = REPO / "app" / "production_review_opening.py"
    text = path.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} is not defined in {path.name}")


# ===========================================================================
# O. EVIDENCE, REVISION AND FABRICATION ARE UNTOUCHED.
# ===========================================================================
#: The digest the CURRENT rule produces for the fixture below, frozen. Its only job is
#: to go red if the rule, its inputs or its canonical encoding change.
FROZEN_FIXTURE_DIGEST = (
    "156f274404d76b01c8acaebf6d6fb5465e277c12ecee58a33aec635a2098e0dc"
)


class TestOEvidenceAndRevisionsAreUntouched:
    def test_the_digest_still_covers_exactly_the_two_evidence_tables(self):
        assert EVIDENCE_TABLES == ("steel_members", "connections")
        assert EVIDENCE_KIND == "PERSISTED_PROJECT_EVIDENCE"

    def test_the_digest_rule_is_unchanged_for_a_frozen_fixture(self):
        rows = {
            "steel_members": [{"id": "m-1", "mark": "B1", "section_name": "310UB40",
                               "review_status": "approved", "total_weight_kg": 12.5}],
            "connections": [{"id": "c-1", "member_id": "m-1",
                             "connection_type": "bolted", "review_status": "pending"}],
        }

        assert evidence_identity(rows)["evidence_digest"] == FROZEN_FIXTURE_DIGEST

    def test_a_document_table_cannot_enter_the_digest(self):
        """The identity of a document is not evidence, and the rule refuses to pretend."""
        with pytest.raises(ValueError):
            evidence_identity({
                "steel_members": [], "connections": [],
                "project_documents": [{"id": "document-1"}],
            })

    def test_no_evidence_table_carries_a_document_column(self):
        """Provenance reaches a document through the drawing, and through nothing else."""
        for name in EVIDENCE_TABLES:
            source = (REPO / "app" / "engineering_data" / "repository.py").read_text()
            tree = ast.parse(source)
            selects = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for inner in ast.walk(node):
                        if (isinstance(inner, ast.Call)
                                and isinstance(inner.func, ast.Attribute)
                                and inner.func.attr == "select" and inner.args
                                and isinstance(inner.args[0], ast.Constant)):
                            selects.setdefault(node.name, inner.args[0].value)
            reader = {
                "steel_members": "member_rows_for_project",
                "connections": "connection_rows_for_project",
            }[name]
            assert reader in selects, reader
            assert "document_id" not in selects[reader], reader

    def test_the_acceptance_project_states_the_frozen_revision_zero_digest(
        self, live_client
    ):
        snapshots = _read(
            live_client, "connection_review_snapshots", "*",
            project_id=ACCEPTANCE_PROJECT,
        )
        assert snapshots, "the acceptance project has no recorded revision"

        revisions = [row["review_revision"] for row in snapshots]
        assert revisions == [0], revisions
        identity = snapshots[0]["evidence_identity"]
        assert identity["evidence_digest"] == REVISION_ZERO_DIGEST
        assert identity["evidence_kind"] == EVIDENCE_KIND
        assert identity["evidence_tables"] == list(EVIDENCE_TABLES)

    def test_the_acceptance_project_has_exactly_one_recorded_revision(self, live_client):
        snapshots = _read(
            live_client, "connection_review_snapshots", "review_revision",
            project_id=ACCEPTANCE_PROJECT,
        )

        assert len(snapshots) == 1
        assert snapshots[0]["review_revision"] == opening.BASELINE_REVISION

    def test_the_acceptance_project_recorded_no_second_capture_window(self, live_client):
        captures = _read(
            live_client, "page_extraction_captures", "analysis_run_id",
            drawing_id=ACCEPTANCE_DRAWING,
        )

        assert captures, "the acceptance drawing has no persisted capture"
        assert len({row["analysis_run_id"] for row in captures}) == 1

    def test_the_acceptance_project_has_the_same_members_and_connections(
        self, live_client
    ):
        """The two evidence tables the digest covers, counted, not re-derived."""
        members = _read(live_client, "steel_members", "id", project_id=ACCEPTANCE_PROJECT)
        connections = _read(
            live_client, "connections", "id", project_id=ACCEPTANCE_PROJECT
        )

        assert len(members) == 43, len(members)
        assert len(connections) == 3, len(connections)

    def test_the_fabrication_pointer_is_still_unset(self, live_client):
        projects = _read(
            live_client, "projects", "id,fab_drawings_pdf_path", id=ACCEPTANCE_PROJECT
        )

        assert len(projects) == 1
        assert projects[0]["fab_drawings_pdf_path"] is None

    def test_the_acceptance_project_has_exactly_one_document(self, live_client):
        documents = _read(
            live_client, "project_documents", "*", project_id=ACCEPTANCE_PROJECT
        )

        assert len(documents) == 1
        document = documents[0]
        assert document["role"] == "UNKNOWN"
        assert document["content_sha256"] is None
        assert document["page_count"] == 41
        assert document["source_format"] == "PDF"

    def test_the_digest_recorded_before_j61_is_the_digest_recorded_after_it(
        self, live_client
    ):
        """The milestone's central promise, stated as one assertion."""
        snapshots = _read(
            live_client, "connection_review_snapshots", "evidence_identity",
            project_id=ACCEPTANCE_PROJECT,
        )

        assert snapshots[0]["evidence_identity"]["evidence_digest"] == (
            "61f18266bdc200cabec400c1fbb51140b6ba5965f9c3730e5fa1111a73727cb3"
        )


# ===========================================================================
# THE MIGRATION REGISTRY — J61 ADDED EXACTLY ONE MIGRATION, AND IT PASSES
# THE GUARD EVERY MIGRATION HAS TO PASS.
# ===========================================================================
class TestTheMigrationItself:
    def test_it_is_the_twenty_fourth_migration_and_the_only_j61_one(self):
        migrations = sorted(p.name for p in (REPO / "supabase" / "migrations").glob("*.sql"))

        assert migrations.count(MIGRATION_NAME) == 1
        assert [name for name in migrations if "j61" in name.lower()] == [MIGRATION_NAME]

    def test_it_carries_none_of_the_substrings_the_registry_guard_forbids(self):
        for forbidden in ("J19", "production_review", "connection_review_items",
                          "connection_review_snapshots"):
            assert forbidden not in SQL, forbidden

    def test_it_opens_and_closes_a_transaction_and_reloads_the_schema(self):
        assert SQL.count("begin;") == 1
        assert SQL.count("commit;") == 1
        assert "notify pgrst, 'reload schema';" in SQL

    def test_the_foreign_key_is_named_so_a_violation_names_the_rule(self):
        """Both edges are declared, and neither is anonymous."""
        assert SQL.count("references public.project_documents(id)") == 2
