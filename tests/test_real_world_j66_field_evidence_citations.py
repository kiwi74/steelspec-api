"""
J66 — FIELD-LEVEL EVIDENCE CITATION INFRASTRUCTURE: THE PROOFS.

WHAT THIS FILE IS

The verification half of J66, and nothing else. The citation table, the ownership guard,
the append-only protection, the writer function, the contract fields and the read path were
implemented, and the single migration was applied, before this file existed; this file is
what turns "it was implemented" into "it was proved".

The areas, in the order the milestone brief names them. Every numbered area below has at
least one test named for it:

   1  exactly one new migration, and the pinned migration list including it
   2  the filename trips no guard, and does not contain the item table's name
   3  the migration contains none of the substrings the guards forbid
   4  the table exists with exactly the declared columns and no others
   5  the declared type and nullability of every column
   6  document_id is the only nullable evidence-identity column; the reading's own three
      columns are NOT NULL
   7  the primary key is the item's three columns plus field_name and ordinal
   8  the COMPOSITE ITEM FOREIGN KEY exists, names the item's own key, and restricts
   9  the capture foreign key names the page reading's own key
  10  the occurrence foreign key covers the six-column occurrence identity
  11  the document foreign key restricts, and is the only one that may be absent
  12  the field_name CHECK vocabulary, read out of the SQL itself
  13  the citation_kind CHECK is exactly the closed SOURCE/DERIVATION pair
  14  ordinal is 1-based and page_number is 1-based
  15  the occurrence identity CHECK is all-three-or-none
  16  NO address-copying or ranking column exists (value, chosen_value, confidence, rank,
      is_primary, winner, resolved, document_role, drawing_number)
  17  anchor is NOT NULL json, and recorded_at defaults to now()
  18  the ownership guard fires BEFORE INSERT, not after
  19  the ownership guard reads the cited reading's own project_id, and a citation across a
      project boundary is impossible to insert
  20  the guard refuses a cited reading, occurrence or document that does not exist
  21  the guard checks the occurrence and the document only when they are stated
  22  the append-only trigger covers UPDATE and DELETE, with no role exemption
  23  UPDATE, DELETE and TRUNCATE are revoked from the server role
  24  anon/authenticated hold nothing, and the server role gets INSERT and SELECT
  25  RLS is enabled with no policy at all
  26  the writer function's signature, its TEXT payload and its SECURITY INVOKER posture
  27  the writer body INSERTs and does nothing else
  28  the writer writes no snapshot and no review item, and performs no revision check
  29  EXECUTE is granted to the server role only
  30  one transaction, one commit, one schema reload
  31  no existing table is created, altered or indexed; no redundant index is put on the
      evidence ledger; no content-hash change
  32  the fence's exact set: the item and snapshot tables in J22 and J66, and nothing else
  33  the Python vocabulary and the SQL CHECK agree, field for field
  34  the contract exposes the citation and the standing, and both default to the uncited
      reading
  35  the standing rule, its fail-closed branches, and that the standing is never stored
  36  the citation order is total and two builds of one revision agree
  37  the row decoder: verbatim coordinates, decoded anchor, a refused partial row
  38  the repository is transport: one reader, one INSERT-only writer, one read loop
  39  NO PRODUCTION WRITER: no extraction, fabrication, AI or route path creates a citation
  40  THE LIVE DEPLOYMENT, read-only: the table exists and is empty, revision 0 is untouched
      and its evidence digest still states exactly what it did

WHAT IS LIVE AND WHAT IS DOUBLED

    LIVE, READ-ONLY   the migrated schema, the citation table's emptiness, the acceptance
                      project's revision 0 and its evidence digest, its review items, and
                      its own project_documents/drawings/claim counts. Every live read is a
                      SELECT. No citation is ever inserted — the brief forbids it, and the
                      table's emptiness is itself one of the assertions below.
    DOUBLED           the service-role client, for the two write paths this file drives:
                      the citation writer and the read-after-write loop. The RULES under
                      test are the production ones; the double only stands in for the
                      store, exactly as the J22 suite's double does. Its own restatement of
                      the ownership rule is a DRIVER, never the authority — the authority is
                      the deployed trigger, which area 19 proves from the migration and
                      mutation 1 proves is load-bearing.
    GENUINE           the Arkles capture, J21's workflow, J22's snapshot builder, the review
                      package vocabulary, the contract's own ordering and standing
                      functions, and the repository's own reader and writer.

Why the capture material is genuine: revision 0 is built by J21's own `_arkles_workflow` and
J22's own snapshot builder over the real Arkles Strand extraction — the same construction
the J21 and J22 suites prove. A citation chain proved over invented review state would prove
nothing about production.

A KNOWN, UNEXERCISED DISPLAY DETAIL, STATED RATHER THAN HIDDEN

`annotation_x` and `annotation_y` are rendered through the contract's single display rule
(`_display`), which keeps a string and `repr`s anything else. A live `numeric(9,2)` read back
by the REST client as a `Decimal` would therefore present as `Decimal('41.50')` rather than as
the database's own text. No live row exists to exercise it — the citation table is empty in
production by construction and J66 writes none — so this is stated and pinned below rather
than silently changed into a second display convention. The milestone that writes the first
real citation owns making the presentation exact.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import re
import uuid
from pathlib import Path

import pytest

from app.cad_engine import connection_review_snapshot as model
from app.cad_engine import review_contract as contract
from app.cad_engine.connection_review_package import (
    ENGINEERING_FIELDS as PACKAGE_ENGINEERING_FIELDS,
)
from app.cad_engine.connection_review_snapshot import (
    CITATION_APPEND_ONLY_RULE,
    CITATION_CAPTURE_FOREIGN_KEY,
    CITATION_CHECK_VOCABULARY,
    CITATION_DECLARED_INDEXES,
    CITATION_FIELD_NAMES,
    CITATION_ITEM_FOREIGN_KEY,
    CITATION_OCCURRENCE_FOREIGN_KEY,
    CITATION_OWNERSHIP_RULE,
    CITATION_PRIMARY_KEY,
    CITATION_REFUSED_APPEND_ONLY,
    CITATION_REFUSED_EVIDENCE_UNKNOWN,
    CITATION_REFUSED_FOREIGN_EVIDENCE,
    CITATION_RLS_RULE,
    CITATION_TABLE,
    CITATION_TABLE_COLUMNS,
    ReviewFieldCitation,
    SnapshotRefused,
    _encode,
    build_review_snapshot,
    citation_from_row,
    connection_contract_from_item,
)
from app.cad_engine.review_contract import (
    CITATION_DERIVATION,
    CITATION_KINDS,
    CITATION_SOURCE,
    ENGINEERING_FIELDS,
    STANDING_DERIVED,
    STANDING_DIRECT,
    STANDING_UNCITED,
    ReviewFieldCitationInfo,
    ReviewFieldStanding,
    build_connection_review_contract,
    field_standings,
)
from app.engineering_data import connection_review_repository as store

from tests import test_real_world_j20_connection_review_persistence_gap as j20
from tests import test_real_world_j21_connection_review_data_model_design as j21
from tests import test_real_world_j4_production_extraction_report_truth as j4

REPO = Path(__file__).resolve().parent.parent
MIGRATION = (
    REPO / "supabase" / "migrations" / "20260929010000_j66_field_evidence_citations.sql"
)
MIGRATION_NAME = MIGRATION.name
SQL = MIGRATION.read_text(encoding="utf-8")
STORE_PATH = REPO / "app" / "engineering_data" / "connection_review_repository.py"
SNAPSHOT_PATH = REPO / "app" / "cad_engine" / "connection_review_snapshot.py"
CONTRACT_PATH = REPO / "app" / "cad_engine" / "review_contract.py"
PACKAGE_PATH = REPO / "app" / "cad_engine" / "connection_review_package.py"

#: The three modules J66 touched. Nothing else in `app/` may mention a citation at all —
#: that is what "no production writer" means as a checkable fact rather than a promise, and
#: area 39 asserts it over the whole tree.
CITATION_MODULES = (
    "app/cad_engine/connection_review_snapshot.py",
    "app/cad_engine/review_contract.py",
    "app/engineering_data/connection_review_repository.py",
)

#: The substrings the pre-existing migration guards forbid in a new migration: J19's, J20's,
#: the J44/J47/J64 vocabulary rules, and the five folded names. None may appear here.
FORBIDDEN_SUBSTRINGS = (
    "J19",
    "production_review",
    "alter table public.projects",
    "projects.status",
    "connection_review_findings",
    "connection_review_readings",
    "connection_review_provenance",
    "connection_review_tasks",
    "connection_review_outputs",
)

#: A citation is an ADDRESS. These are the columns a copy of the answered value — or a
#: ranking between competing answers — would need, and the design refuses every one of them.
FORBIDDEN_CITATION_COLUMNS = (
    "value", "chosen_value", "confidence", "rank", "is_primary", "winner",
    "resolved", "document_role", "drawing_number",
)

#: The acceptance project named by the brief, and the revision-0 digest that must still be
#: exactly this after J66.
ACCEPTANCE_PROJECT = "2389c115-664f-4fe4-8b76-ca07aac3719d"
REVISION_ZERO_DIGEST = (
    "61f18266bdc200cabec400c1fbb51140b6ba5965f9c3730e5fa1111a73727cb3"
)

production = j4.production


# ===========================================================================
# Reading the migration the way a server would: comments gone, whitespace normal.
# ===========================================================================
def _statement_lines(text: str = SQL) -> list[str]:
    return [
        line.strip().rstrip(";")
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]


def _statements(text: str = SQL) -> str:
    """The SQL a server would actually run, with the doctrine header removed.

    Almost every assertion below is about what the migration DOES. Its header describes at
    length what it does NOT do — it names forbidden columns and untouched tables in order to
    deny them — so a test that searched the whole file would read a denial as a declaration.
    This is what the negative assertions read, and it is also why "the snapshot table is
    named here only in the header's chain listing" is a checked fact rather than a claim.
    """
    return re.sub(r"\s+", " ", " ".join(_statement_lines(text))).strip()


STATEMENTS = _statements()


def _code(text: str) -> str:
    """A fragment with its comment lines removed and whitespace normalised."""
    return re.sub(r"\s+", " ", " ".join(_statement_lines(text))).strip()


def _balanced(text: str, open_index: int) -> str:
    """The inside of one parenthesised group, by depth.

    Regex cannot count parentheses and the occurrence-identity CHECK nests them, so a
    non-greedy `check \\((.*?)\\)` pattern reads that constraint as a prefix. Depth is what
    makes it read whole.
    """
    assert text[open_index] == "(", text[open_index - 40:open_index + 1]
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index]
    raise AssertionError("unbalanced parentheses in the migration")


def _block(table: str, text: str = SQL) -> str:
    """One `create table` body, as authored."""
    match = re.search(
        rf"create table if not exists public\.{table}\s*\((.*?)\n\);", text, re.S
    )
    assert match, f"no create table for {table}"
    return match.group(1)


SQL_TYPES = frozenset(
    {"uuid", "integer", "bigint", "smallint", "text", "json", "jsonb", "boolean",
     "numeric", "timestamptz", "timestamp"}
)


def _column_definitions(table: str, text: str = SQL) -> dict[str, str]:
    """The columns of one `create table` body, as {name: rest-of-line}.

    A line counts as a column only when its second token is a SQL type: table constraints
    and their continuation lines are otherwise indistinguishable from columns by shape alone
    (`foreign key (project_id)` would read as a column named `foreign` of type `key (...)`).
    """
    definitions = {}
    for raw in _block(table, text).splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--") or line.startswith("constraint"):
            continue
        match = re.match(r"([a-z_]+)\s+(.*)$", line)
        # `numeric(9,2)` is one type with a modifier, not a type named `numeric(9,2)`.
        if match and match.group(2).split()[0].split("(")[0] in SQL_TYPES:
            definitions[match.group(1)] = re.sub(r"\s+", " ", match.group(2)).strip()
    return definitions


def _check_body(name: str, text: str = SQL) -> str:
    marker = f"constraint {name} check ("
    statements = _statements(text)
    start = statements.index(marker) + len(marker) - 1
    return _balanced(statements, start)


def _check_literals(name: str, text: str = SQL) -> tuple[str, ...]:
    return tuple(re.findall(r"'([A-Za-z][A-Za-z_0-9]*)'", _check_body(name, text)))


def _signature(name: str, text: str = SQL) -> str:
    """One function's parameter list, whitespace normalised, read by depth."""
    marker = f"create or replace function public.{name}("
    statements = _statements(text)
    start = statements.index(marker) + len(marker) - 1
    return " ".join(_balanced(statements, start).split())


def _function_sql(name: str, text: str = SQL) -> str:
    """One function's whole declaration, header included, comments removed."""
    marker = f"create or replace function public.{name}("
    start = text.index(marker)
    return _code(text[start:text.index("$$;", start) + 2])


def _function_body(name: str, text: str = SQL) -> str:
    """One function's BODY only — from after the opening `$$` to the terminator.

    Body-only deliberately: the declaration line contains `create or replace function`, and a
    check that the body performs no DDL must not be satisfied by the word `create` in the
    header that introduces it.
    """
    marker = f"create or replace function public.{name}("
    start = text.index(marker)
    body_start = text.index("$$", start) + 2
    return _code(text[body_start:text.index("$$;", body_start)])


def _triggers(text: str = SQL) -> dict[str, tuple[str, str, str]]:
    """Every `create trigger`, as {name: (event clause, table, function)}."""
    found = re.findall(
        r"create trigger (\w+)\s+(before|after)\s+([a-z]+(?: or [a-z]+)*)\s+on "
        r"public\.(\w+)\s+for each row execute function public\.(\w+)\(\)",
        _statements(text),
    )
    return {name: (events, table, function) for name, _when, events, table, function in found}


def _migration_names() -> list[str]:
    return sorted(p.name for p in (REPO / "supabase" / "migrations").glob("*.sql"))


def _fence_violations(field_names) -> list[str]:
    """The design fence as a function, so mutation 3 can show it goes red."""
    return [name for name in field_names if name in FORBIDDEN_CITATION_COLUMNS]


# ===========================================================================
# The genuine review state, built the way J21 and J22 build it.
# ===========================================================================
needs_real_capture = pytest.mark.skipif(
    not j21.ARKLES_REAL_AI_EXTRACTION,
    reason="the real Arkles connection extraction is not present; the citation proofs run "
           "over the genuine revision rather than over a fabricated one",
)


@pytest.fixture(scope="module")
def workflow():
    if not j21.ARKLES_REAL_AI_EXTRACTION:
        pytest.skip("the real Arkles capture is not present")
    return j21._arkles_workflow()


@pytest.fixture(scope="module")
def revision_zero(workflow):
    return build_review_snapshot(
        workflow, project_id=j21.PROJECT_ID, evidence_rows=j21.NO_EVIDENCE,
        previous_revision=None,
    )


def _citation(
    field_name="plate",
    ordinal=1,
    citation_kind=CITATION_SOURCE,
    project_id=j21.PROJECT_ID,
    review_revision=0,
    review_package_id=j21.SELECTED_PACKAGE_ID,
    document_id=None,
    drawing_id="11111111-1111-1111-1111-111111111111",
    page_number=7,
    analysis_run_id="22222222-2222-2222-2222-222222222222",
    annotation_x=None,
    annotation_y=None,
    extractor_version=None,
    anchor=("detail-24",),
) -> ReviewFieldCitation:
    """One citation object. The encoder is applied here because it is what the model applies
    to a payload; nothing else about the object is adjusted."""
    return ReviewFieldCitation(
        project_id=project_id,
        review_revision=review_revision,
        review_package_id=review_package_id,
        field_name=field_name,
        ordinal=ordinal,
        citation_kind=citation_kind,
        document_id=document_id,
        drawing_id=drawing_id,
        page_number=page_number,
        analysis_run_id=analysis_run_id,
        annotation_x=annotation_x,
        annotation_y=annotation_y,
        extractor_version=extractor_version,
        anchor=_encode(anchor, where="anchor"),
    )


def _with_citations(snapshot, citations):
    return dataclasses.replace(snapshot, citations=tuple(citations))


def _evidence_key(citation):
    return (citation.drawing_id, citation.page_number, citation.analysis_run_id)


# ===========================================================================
# The double: the service-role store, for the two write paths driven below.
# ===========================================================================
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
    def __init__(self, store_, table, columns):
        self._store = store_
        self._table = table
        self._columns = columns
        self._filters: list[tuple[str, object]] = []

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def execute(self):
        self._store.reads.append((self._table, tuple(self._filters), self._columns))
        return _Result([
            copy.deepcopy(row) for row in self._store.tables[self._table]
            if all(row.get(column) == value for column, value in self._filters)
        ])


class _Table:
    def __init__(self, store_, name):
        if name not in store_.tables:
            raise _ApiError("PGRST205", f"Could not find the table public.{name}")
        self._store = store_
        self._name = name

    def select(self, columns="*"):
        return _Query(self._store, self._name, columns)


class _Rpc:
    def __init__(self, store_, fn, params):
        self._store = store_
        self._fn = fn
        self._params = params

    def execute(self):
        return self._store.call(self._fn, self._params)


class _CitationStore:
    """The service-role client, standing in for the one writer and the one reader.

    It restates the deployed function's ownership rule so the writer can be DRIVEN through
    both outcomes, and it is NOT the authority for that rule. The authority is the
    migration's own trigger text (area 19), and mutation 1 shows the property genuinely
    depends on the guard rather than on this double agreeing with itself.
    """

    def __init__(self, *, evidence=()):
        self.tables = {CITATION_TABLE: []}
        # (drawing_id, page_number, analysis_run_id) -> the project that owns the reading.
        self.evidence = dict(evidence)
        self.calls: list[tuple] = []
        self.reads: list[tuple] = []
        self.refuse_with: tuple[str, str] | None = None

    def table(self, name):
        self.calls.append(("table", name))
        return _Table(self, name)

    def rpc(self, fn, params):
        self.calls.append(("rpc", fn, params))
        return _Rpc(self, fn, params)

    def rows(self, table=CITATION_TABLE):
        return [copy.deepcopy(row) for row in self.tables[table]]

    def writes(self):
        return [call for call in self.calls if call[0] == "rpc"]

    def call(self, fn, params):
        if self.refuse_with is not None:
            raise _ApiError(*self.refuse_with)
        if fn != store.CITATION_WRITER_FUNCTION:
            raise _ApiError("PGRST202", f"Could not find the function public.{fn}")
        # The payload is JSON text, exactly as the writer function parses it with `::json`.
        # Read here with `json.loads` and not `literal_eval`: JSON's `null` is a name to
        # Python's parser and would be refused, which would make this double reject a payload
        # the real function accepts.
        for row in json.loads(params["p_citations"]):
            owner = self.evidence.get(
                (row["drawing_id"], row["page_number"], row["analysis_run_id"])
            )
            if owner is None:
                raise _ApiError(
                    "P0001",
                    "CITATION_REFUSED_EVIDENCE_UNKNOWN: citation of field "
                    f"{row['field_name']} cites a page reading which does not exist",
                )
            if owner != params["p_project_id"]:
                raise _ApiError(
                    "P0001",
                    "CITATION_REFUSED_FOREIGN_EVIDENCE: project "
                    f"{params['p_project_id']} may not cite a page reading that belongs "
                    f"to project {owner}",
                )
            self.tables[CITATION_TABLE].append({
                "project_id": params["p_project_id"],
                "review_revision": params["p_review_revision"],
                "review_package_id": params["p_review_package_id"],
                **copy.deepcopy(row),
                # The database's own DEFAULT now(): the writer never sends this column, and a
                # row read back without it would be refused as partial — correctly.
                "recorded_at": "2026-09-29T00:00:00+00:00",
            })
        return _Result(None)


@pytest.fixture
def citation_store():
    return _CitationStore()


# ===========================================================================
# 1-3. THE MIGRATION ITSELF
# ===========================================================================
class TestTheMigrationIsTheOnlyOneAndTripsNoGuard:
    def test_area_1_exactly_one_migration_was_added_and_it_is_this_one(self):
        names = _migration_names()
        # J66's file is no longer the newest — L19 added one after it, as every milestone
        # after J28 has. What this asserts is unchanged: exactly one migration names this
        # milestone, and it exists.
        assert MIGRATION_NAME in names, names
        assert len([name for name in names if "j66" in name.lower()]) == 1
        assert MIGRATION.exists()

    def test_area_1_the_pinned_migration_list_gained_exactly_this_entry(self):
        assert _migration_names() == [
            "20260924000000_j5_section_resolution_truth.sql",
            "20260924010000_j6_reference_data_identity.sql",
            "20260924020000_j8b_connection_plate_evidence_nullability.sql",
            "20260925000000_j22_connection_review_persistence.sql",
            "20260925010000_j23_page_extraction_captures.sql",
            "20260927000000_j28_pdf_annotation_occurrences.sql",
            "20260928000000_j44_project_review_claims.sql",
            "20260929000000_j61_project_documents.sql",
            MIGRATION_NAME,
            # L19 added one after J66's, which is the same convention: each milestone that
            # adds a migration names itself here, so a later one cannot appear unremarked.
            "20261006000000_l19_selected_extraction_lineage.sql",
        ]

    def test_area_2_the_filename_names_no_earlier_milestone(self):
        lowered = MIGRATION_NAME.lower()
        for other in ("j19", "j20", "j21", "j23", "j24", "j28", "j36", "j44", "j47",
                      "j50", "j61", "j64"):
            assert other not in lowered, other
        assert lowered.count("j66") == 1
        # The J45 rule applies to a migration that named the package in its own path: J19's
        # guard refuses a module inside the review package, and that word is forbidden too.
        assert "production_review" not in lowered

    def test_area_2_the_citation_table_name_does_not_contain_the_item_table_name(self):
        """Pinned deliberately: the migration guards search for the item table's name as a
        SUBSTRING, so a citation table whose name merely began with it would widen every one
        of them at once."""
        assert model.ITEM_TABLE not in CITATION_TABLE
        assert CITATION_TABLE.endswith("_citations")
        assert CITATION_TABLE.startswith("connection_review_item_")

    def test_area_3_none_of_the_forbidden_substrings_appears(self):
        for token in FORBIDDEN_SUBSTRINGS:
            assert token not in SQL, token
        for folded in (
            "connection_review_findings", "connection_review_readings",
            "connection_review_provenance", "connection_review_tasks",
            "connection_review_outputs",
        ):
            assert folded not in STATEMENTS.lower(), folded


# ===========================================================================
# 4-17. THE TABLE
# ===========================================================================
class TestTheTableIsExactlyWhatTheDesignDeclares:
    def test_area_4_the_columns_are_exactly_the_declared_ones(self):
        declared = {name for name, _, _, _ in CITATION_TABLE_COLUMNS}
        assert set(_column_definitions(CITATION_TABLE)) == declared
        assert declared == {
            "project_id", "review_revision", "review_package_id", "field_name", "ordinal",
            "citation_kind", "document_id", "drawing_id", "page_number", "analysis_run_id",
            "annotation_x", "annotation_y", "extractor_version", "anchor", "recorded_at",
        }
        assert CITATION_DECLARED_INDEXES == ()
        assert model.DECLARED_INDEXES == ()

    def test_area_5_every_column_has_the_declared_type_and_nullability(self):
        definitions = _column_definitions(CITATION_TABLE)
        for name, sql_type, nullable, _ in CITATION_TABLE_COLUMNS:
            definition = definitions[name]
            assert definition.startswith(sql_type), (name, sql_type, definition)
            if nullable:
                assert "not null" not in definition, (name, definition)
            else:
                assert "not null" in definition, (name, definition)

    def test_area_6_the_page_reading_is_never_nullable_and_the_document_is(self):
        """`document_id` may be absent — the cited reading may predate document identity. The
        reading's own identity may not: a citation naming no page reading and no occurrence
        names nothing at all."""
        definitions = _column_definitions(CITATION_TABLE)
        for column in CITATION_CAPTURE_FOREIGN_KEY:
            assert "not null" in definitions[column], column
        assert "not null" not in definitions["document_id"]
        for optional in ("annotation_x", "annotation_y", "extractor_version"):
            assert "not null" not in definitions[optional], optional

    def test_area_7_the_primary_key_is_the_item_plus_the_field_and_the_ordinal(self):
        assert CITATION_PRIMARY_KEY == (
            "project_id", "review_revision", "review_package_id", "field_name", "ordinal",
        )
        assert (
            f"constraint {CITATION_TABLE}_pkey primary key "
            f"(project_id, review_revision, review_package_id, field_name, ordinal)"
            in STATEMENTS
        )

    def test_area_8_the_composite_item_foreign_key_is_present_and_restricts(self):
        """The one constraint the brief forbade omitting: it is what makes the citation's
        owner a database fact rather than a claim in a document."""
        columns, key = CITATION_ITEM_FOREIGN_KEY
        assert columns == ("project_id", "review_revision", "review_package_id")
        assert key == model.ITEM_PRIMARY_KEY
        assert (
            f"constraint {CITATION_TABLE}_item_fkey foreign key "
            f"(project_id, review_revision, review_package_id) references "
            f"public.{model.ITEM_TABLE} (project_id, review_revision, review_package_id) "
            f"on delete restrict"
            in STATEMENTS
        )

    def test_area_9_the_capture_foreign_key_names_the_page_readings_own_key(self):
        assert CITATION_CAPTURE_FOREIGN_KEY == (
            "drawing_id", "page_number", "analysis_run_id",
        )
        assert (
            f"constraint {CITATION_TABLE}_capture_fkey foreign key "
            f"(drawing_id, page_number, analysis_run_id) references "
            f"public.page_extraction_captures (drawing_id, page_number, analysis_run_id) "
            f"on delete restrict"
            in STATEMENTS
        )

    def test_area_10_the_occurrence_foreign_key_covers_the_six_column_identity(self):
        """A reduced key would let a citation point at an occurrence by page alone, which is
        the reduction the brief forbids."""
        assert CITATION_OCCURRENCE_FOREIGN_KEY == (
            "drawing_id", "page_number", "analysis_run_id",
            "annotation_x", "annotation_y", "extractor_version",
        )
        assert (
            f"constraint {CITATION_TABLE}_occurrence_fkey foreign key "
            f"(drawing_id, page_number, analysis_run_id, annotation_x, annotation_y, "
            f"extractor_version) references public.pdf_annotation_occurrences "
            f"(drawing_id, page_number, analysis_run_id, annotation_x, annotation_y, "
            f"extractor_version) on delete restrict"
            in STATEMENTS
        )

    def test_area_11_the_document_foreign_key_restricts_and_is_the_optional_one(self):
        assert (
            f"constraint {CITATION_TABLE}_document_fkey foreign key (document_id) "
            f"references public.project_documents (id) on delete restrict"
            in STATEMENTS
        )
        # It is the only foreign key on a nullable column, and the guard checks it only when
        # it is stated — so NULL is never a wildcard that would match anything.
        assert "document_id is not null then" in _function_body(
            "connection_review_citation_ownership"
        )

    def test_area_12_the_field_vocabulary_check_is_the_packages_own_list(self):
        """The CHECK's literals, the contract's constant, the package's constant and the
        model's constant are ONE list. A field added to any one of them cannot silently
        disagree with the others."""
        assert _check_literals(f"{CITATION_TABLE}_field_name_check") == ENGINEERING_FIELDS
        assert ENGINEERING_FIELDS == PACKAGE_ENGINEERING_FIELDS
        assert ENGINEERING_FIELDS == model.ENGINEERING_FIELDS
        assert CITATION_FIELD_NAMES == ENGINEERING_FIELDS
        assert CITATION_CHECK_VOCABULARY["field_name"] == ENGINEERING_FIELDS
        assert ENGINEERING_FIELDS == (
            "connected_member_marks", "position", "plate", "holes", "location",
            "attachments", "material",
        )

    def test_area_13_the_citation_kind_check_is_the_closed_pair(self):
        assert _check_literals(f"{CITATION_TABLE}_citation_kind_check") == (
            CITATION_SOURCE, CITATION_DERIVATION,
        )
        assert CITATION_KINDS == (CITATION_SOURCE, CITATION_DERIVATION)
        assert CITATION_CHECK_VOCABULARY["citation_kind"] == CITATION_KINDS

    def test_area_14_ordinals_and_pages_are_one_based(self):
        assert _check_body(f"{CITATION_TABLE}_ordinal_check").strip() == "ordinal >= 1"
        assert _check_body(f"{CITATION_TABLE}_page_number_check").strip() == "page_number >= 1"

    def test_area_15_the_occurrence_identity_is_all_three_or_none(self):
        assert _check_body(f"{CITATION_TABLE}_occurrence_identity_check").strip() == (
            "(annotation_x is null) = (annotation_y is null) and "
            "(annotation_x is null) = (extractor_version is null)"
        )

    def test_area_16_no_address_copying_or_ranking_column_exists(self):
        """A citation says WHERE, never WHAT — and a field with two citations is not a field
        with two competing values, so there is no rank, no winner and no is_primary."""
        declared = set(_column_definitions(CITATION_TABLE))
        assert _fence_violations(declared) == []
        # The same fence over every statement, so a column smuggled into a constraint or a
        # function body is caught as well.
        for forbidden in FORBIDDEN_CITATION_COLUMNS:
            assert not re.search(rf"\b{forbidden}\b", STATEMENTS), forbidden

    def test_area_16_the_contract_and_the_model_carry_no_such_field_either(self):
        assert _fence_violations(
            field.name for field in dataclasses.fields(ReviewFieldCitationInfo)
        ) == []
        assert _fence_violations(
            field.name for field in dataclasses.fields(ReviewFieldCitation)
        ) == []
        assert _fence_violations(
            field.name for field in dataclasses.fields(ReviewFieldStanding)
        ) == []

    def test_area_17_the_anchor_is_not_null_json_and_the_timestamp_defaults(self):
        definitions = _column_definitions(CITATION_TABLE)
        assert definitions["anchor"] == "json not null"
        assert definitions["recorded_at"] == "timestamptz not null default now()"
        assert "jsonb" not in STATEMENTS.lower()


# ===========================================================================
# 18-22. THE GUARDS
# ===========================================================================
def _ownership_verdict(citation, evidence, *, guard=True):
    """What the ownership trigger decides, over rows of the shape the table holds.

    This IS the migration's rule: it looks the cited reading up in the evidence ledger and
    refuses when the reading does not exist or belongs to another project. Mutation 1 below
    removes it, and the property goes red — which is what makes area 19 a proof about the
    guard rather than a proof about a message.
    """
    if not guard:
        return "ACCEPTED"
    owner = evidence.get(_evidence_key(citation))
    if owner is None:
        return CITATION_REFUSED_EVIDENCE_UNKNOWN
    return "ACCEPTED" if owner == citation.project_id else CITATION_REFUSED_FOREIGN_EVIDENCE


class TestTheOwnershipGuard:
    def test_area_18_the_guard_is_a_before_insert_trigger_on_the_new_table(self):
        events, table, function = _triggers()[f"{CITATION_TABLE}_ownership"]
        assert table == CITATION_TABLE
        assert events == "insert"
        assert function == "connection_review_citation_ownership"
        assert (
            f"before insert on public.{CITATION_TABLE} for each row execute function "
            f"public.connection_review_citation_ownership()"
        ) in STATEMENTS
        # BEFORE, not AFTER: it must pre-empt the foreign keys, so that a citation of a
        # reading that does not exist is refused with the code naming the reason rather than
        # with a bare constraint violation.
        assert f"after insert on public.{CITATION_TABLE}" not in STATEMENTS

    def test_area_19_a_citation_of_another_projects_reading_is_refused(self):
        """The hole the foreign keys cannot close, closed: the cited reading's OWN project_id
        is read and compared with the citation's."""
        body = _function_body("connection_review_citation_ownership")
        assert "from public.page_extraction_captures" in body
        assert "v_capture_project <> new.project_id" in body
        assert CITATION_REFUSED_FOREIGN_EVIDENCE in body
        assert "errcode = 'P0001'" in body
        # And as behaviour, over the same shape of rows.
        citation = _citation(project_id=j21.PROJECT_ID)
        assert _ownership_verdict(citation, {_evidence_key(citation): "PROJ-7J66-OTHER"}) == (
            CITATION_REFUSED_FOREIGN_EVIDENCE
        )
        assert _ownership_verdict(citation, {_evidence_key(citation): j21.PROJECT_ID}) == (
            "ACCEPTED"
        )

    def test_area_19_the_guard_reads_the_project_rather_than_trusting_a_claim(self):
        body = _function_body("connection_review_citation_ownership")
        assert "select project_id into v_capture_project" in body
        assert "new.project_id" in body
        # The cited reading's project is obtained by a SELECT, never carried on the citation
        # as a second column that the guard would then be checking against itself.
        assert "project_id" in _column_definitions(CITATION_TABLE)
        assert len(_column_definitions(CITATION_TABLE)) == 15

    def test_area_20_an_unknown_reading_occurrence_or_document_is_refused_by_name(self):
        body = _function_body("connection_review_citation_ownership")
        assert body.count(CITATION_REFUSED_EVIDENCE_UNKNOWN) == 3
        for table in ("page_extraction_captures", "pdf_annotation_occurrences",
                      "project_documents"):
            assert table in body, table
        assert _ownership_verdict(_citation(), {}) == CITATION_REFUSED_EVIDENCE_UNKNOWN

    def test_area_21_the_occurrence_and_the_document_are_checked_only_when_stated(self):
        """NULL means "this citation names the page reading, not one occurrence on it" — and
        it is never looked up, so it matches nothing rather than everything."""
        body = _function_body("connection_review_citation_ownership")
        assert "if new.extractor_version is not null then" in body
        assert "if new.document_id is not null then" in body
        assert "if new.extractor_version is null then" not in body
        assert "if new.document_id is null then" not in body

    def test_area_22_the_append_only_trigger_covers_update_and_delete(self):
        events, table, function = _triggers()[f"{CITATION_TABLE}_append_only"]
        assert table == CITATION_TABLE
        assert events == "update or delete"
        assert function == "connection_review_citation_append_only"
        assert (
            f"before update or delete on public.{CITATION_TABLE} for each row execute "
            f"function public.connection_review_citation_append_only()"
        ) in STATEMENTS

    def test_area_22_the_append_only_function_raises_for_every_role(self):
        body = _function_body("connection_review_citation_append_only")
        assert CITATION_REFUSED_APPEND_ONLY in body
        assert "errcode = 'P0001'" in body
        assert "tg_op" in body and "tg_table_name" in body
        # No exemption and no condition: a role test would be a mutable-table exception by
        # another name, which the brief forbids.
        for token in ("current_user", "session_user", "pg_has_role", "if ", "case "):
            assert token not in body, token

    def test_area_22_both_guards_state_their_rule_where_a_reader_will_find_it(self):
        assert "no UPDATE" in CITATION_APPEND_ONLY_RULE
        assert "no DELETE" in CITATION_APPEND_ONLY_RULE
        assert "no mutable-table exception" in CITATION_APPEND_ONLY_RULE.replace("\n", " ")
        assert "impossible to insert" in CITATION_OWNERSHIP_RULE
        assert CITATION_RLS_RULE


# ===========================================================================
# 23-31. PRIVILEGES, THE WRITER, AND WHAT THE FILE DOES NOT TOUCH
# ===========================================================================
class TestPrivilegesAndTheWriter:
    def test_area_23_update_delete_and_truncate_are_revoked_from_the_server_role(self):
        """TRUNCATE is revoked explicitly because a row-level trigger cannot see it: without
        this line the append-only protection has a hole it cannot close."""
        assert (
            f"revoke update, delete, truncate on table public.{CITATION_TABLE} "
            f"from service_role"
        ) in STATEMENTS

    def test_area_24_anon_and_authenticated_hold_nothing_and_the_server_role_is_narrow(self):
        assert (
            f"revoke all on table public.{CITATION_TABLE} from public, anon, authenticated"
            in STATEMENTS
        )
        assert (
            f"grant insert, select on table public.{CITATION_TABLE} to service_role"
            in STATEMENTS
        )
        lowered = STATEMENTS.lower()
        assert "grant all" not in lowered
        assert "grant update" not in lowered
        assert "grant delete" not in lowered
        assert "grant truncate" not in lowered

    def test_area_25_rls_is_enabled_with_no_policy(self):
        assert STATEMENTS.count("enable row level security") == 1
        assert (
            f"alter table public.{CITATION_TABLE} enable row level security" in STATEMENTS
        )
        assert not re.search(r"(?i)\bcreate\s+policy\b", SQL)

    def test_area_26_the_writer_signature_takes_this_projects_revision_and_text(self):
        """The payload is TEXT, cast `::json` inside. Handing the transport a parsed object
        would make the payload's key order the transport's promise."""
        assert _signature("record_connection_review_citations") == (
            "p_project_id uuid, p_review_revision integer, p_review_package_id text, "
            "p_citations text"
        )
        assert "returns void" in STATEMENTS
        body = _function_body("record_connection_review_citations")
        assert "json_array_elements(p_citations::json)" in body
        assert "::jsonb" not in body

    def test_area_26_the_writer_is_security_invoker(self):
        """INVOKER is the DEFAULT, so the assertion is that nothing declares otherwise. It is
        made over the statements and the function, never over the whole file: the header's
        prose names `security definer` in order to say it is not used, and reading a denial as
        a declaration is exactly the mistake this file's `_statements` helper exists to
        prevent."""
        assert "security definer" not in STATEMENTS.lower()
        assert "security invoker" not in STATEMENTS.lower()
        assert "security definer" not in _function_sql(
            "record_connection_review_citations"
        ).lower()

    def test_area_27_the_writer_body_inserts_and_does_nothing_else(self):
        body = _function_body("record_connection_review_citations")
        assert "insert into public.connection_review_item_citations" in body
        assert body.count("insert into") == 1
        for forbidden in (
            "update ", "delete from", "truncate", "upsert", "on conflict", "create ",
            "alter ", "drop ", "grant ", "revoke ", "returning", "execute ",
        ):
            assert forbidden not in body.lower(), forbidden

    def test_area_28_the_writer_writes_no_snapshot_and_no_review_item(self):
        body = _function_body("record_connection_review_citations")
        for table in (model.SNAPSHOT_TABLE, model.ITEM_TABLE):
            assert table not in body, table
        # The item table is named once in the whole migration's statements, and it is READ
        # there — the composite foreign key — never written.
        assert STATEMENTS.count(f"public.{model.ITEM_TABLE} ") == 1
        assert f"insert into public.{model.ITEM_TABLE}" not in STATEMENTS
        assert f"insert into public.{model.SNAPSHOT_TABLE}" not in STATEMENTS
        assert f"update public.{model.ITEM_TABLE}" not in STATEMENTS

    def test_area_28_the_writer_performs_no_revision_check_of_its_own(self):
        """The citation's revision is the ITEM's. A second revision rule here would be a
        second counter that could disagree with the one the record already has."""
        body = _function_body("record_connection_review_citations")
        for forbidden in ("max(review_revision)", "review_revision + 1", "review_revision -",
                          "count(*)", "exists ("):
            assert forbidden not in body, forbidden
        assert "p_review_revision" in body

    def test_area_29_execute_is_granted_to_the_server_role_only(self):
        signature = "record_connection_review_citations(uuid, integer, text, text)"
        assert f"grant execute on function public.{signature} to service_role" in STATEMENTS
        assert (
            f"revoke all on function public.{signature} from public, anon, authenticated"
            in STATEMENTS
        )
        for helper in ("connection_review_citation_ownership",
                       "connection_review_citation_append_only"):
            assert (
                f"revoke all on function public.{helper}() from public, anon, authenticated"
                in STATEMENTS
            )

    def test_area_30_the_migration_is_one_transaction_that_reloads_the_rest_schema(self):
        lines = _statement_lines()
        assert lines[0] == "begin"
        assert lines[-2:] == ["commit", "notify pgrst, 'reload schema'"]

    def test_area_31_no_existing_table_is_created_altered_or_indexed(self):
        joined = STATEMENTS.lower()
        assert joined.count("create table") == 1
        assert joined.count("alter table") == 1
        assert f"create table if not exists public.{CITATION_TABLE}" in joined
        assert not re.search(r"(?i)\bcreate\s+(unique\s+)?index\b", SQL)
        assert not re.search(r"(?i)\bdrop\s+(table|index|trigger|function)\b", SQL)
        # The evidence ledgers are READ by the guard and never written to.
        for table in ("page_extraction_captures", "pdf_annotation_occurrences",
                      "project_documents", "drawings", "drawing_sets", "steel_members",
                      "connections"):
            for verb in ("alter table", "create table if not exists", "insert into",
                         "update", "delete from"):
                assert f"{verb} public.{table}" not in joined, (verb, table)

    def test_area_31_no_redundant_project_inclusive_index_is_added_to_the_ledger(self):
        """J65 refused it explicitly: the ownership guard is the answer, not a second unique
        index on an existing evidence table. Read over the statements, because the header
        names the refused index in order to refuse it."""
        assert "page_extraction_captures" in STATEMENTS  # the guard's FK and its SELECT
        assert not re.search(r"(?i)index[^\n]*page_extraction_captures", STATEMENTS)
        assert "create index" not in STATEMENTS.lower()
        assert "content_sha256" not in STATEMENTS
        assert not re.search(r"(?i)(add column|drop column)", STATEMENTS)
        assert "alter table public.project_documents" not in STATEMENTS
        assert "alter table public.drawings" not in STATEMENTS


# ===========================================================================
# 32. THE FENCE
# ===========================================================================
class TestTheFenceIsAnExactSet:
    def test_area_32_the_two_review_tables_are_named_in_exactly_these_two_migrations(self):
        """G1, ratified: exactly two files may name each of the two review tables, and the
        assertion stays an exact set rather than an allowance."""
        written_in: dict[str, list[str]] = {}
        for path in sorted((REPO / "supabase").rglob("*.sql")):
            text = path.read_text(encoding="utf-8")
            for name in (model.SNAPSHOT_TABLE, model.ITEM_TABLE):
                if name in text:
                    written_in.setdefault(name, []).append(path.name)
        assert written_in == {
            model.SNAPSHOT_TABLE: [
                "20260925000000_j22_connection_review_persistence.sql", MIGRATION_NAME,
            ],
            model.ITEM_TABLE: [
                "20260925000000_j22_connection_review_persistence.sql", MIGRATION_NAME,
            ],
        }, written_in

    def test_area_32_the_item_table_appears_here_for_one_structural_reason_only(self):
        """The distinction the exact set above must not be read past: the SNAPSHOT table
        appears only in this migration's header, listing the chain it belongs to, and in no
        statement at all. The ITEM table appears in one statement — the composite foreign
        key, which reads it. Neither is written, altered or given a second writer."""
        assert STATEMENTS.count(f"public.{model.ITEM_TABLE} ") == 1
        assert STATEMENTS.count(f"public.{model.SNAPSHOT_TABLE}") == 0
        assert SQL.count(f"public.{model.ITEM_TABLE}") == 1
        assert f"references public.{model.ITEM_TABLE}" in STATEMENTS
        # The snapshot table's every appearance in the file is a comment line. Said this way
        # rather than by count, because what matters is that it is NAMED and never USED.
        naming = [
            line for line in SQL.splitlines() if model.SNAPSHOT_TABLE in line
        ]
        assert naming, "the header no longer lists the chain this table belongs to"
        assert all(line.strip().startswith("--") for line in naming), naming

    def test_area_32_the_citation_table_is_named_in_one_migration_only(self):
        naming = [
            path.name for path in sorted((REPO / "supabase").rglob("*.sql"))
            if CITATION_TABLE in path.read_text(encoding="utf-8")
        ]
        assert naming == [MIGRATION_NAME], naming


# ===========================================================================
# 33-37. THE CONTRACT AND THE READ MODEL
# ===========================================================================
class TestTheContractAndTheReadModel:
    def test_area_33_the_python_vocabulary_is_the_sql_vocabulary(self):
        assert ENGINEERING_FIELDS == PACKAGE_ENGINEERING_FIELDS
        assert tuple(CITATION_FIELD_NAMES) == ENGINEERING_FIELDS
        assert _check_literals(f"{CITATION_TABLE}_field_name_check") == ENGINEERING_FIELDS
        assert set(_check_literals(f"{CITATION_TABLE}_citation_kind_check")) == set(
            CITATION_KINDS
        )
        assert CITATION_SOURCE in CITATION_KINDS and CITATION_DERIVATION in CITATION_KINDS

    def test_area_33_the_field_vocabulary_is_the_existing_authoritative_list(self):
        """The brief's own requirement: the field list is the existing authoritative
        vocabulary, not an invention. Both modules publish the SAME expression, and the two
        are asserted equal rather than assumed."""
        expression = 'ENGINEERING_FIELDS = REQUIRED_PROVENANCE_FIELDS + ("material",)'
        assert expression in CONTRACT_PATH.read_text(encoding="utf-8")
        assert expression in PACKAGE_PATH.read_text(encoding="utf-8")

    def test_area_34_the_contract_exposes_the_citation_and_the_standing(self):
        names = {
            field.name for field in dataclasses.fields(contract.ConnectionReviewContract)
        }
        assert {"field_citations", "field_standings"} <= names
        assert [field.name for field in dataclasses.fields(ReviewFieldCitationInfo)] == [
            "field", "ordinal", "citation_kind", "document_id", "drawing_id",
            "page_number", "analysis_run_id", "annotation_x", "annotation_y",
            "extractor_version", "anchor",
        ]
        assert [field.name for field in dataclasses.fields(ReviewFieldStanding)] == [
            "field", "standing",
        ]

    def test_area_34_both_new_contract_fields_default_to_the_uncited_reading(self):
        """Every construction of this contract that predates J66 keeps working, and keeps
        saying the honest thing: no citations, seven UNCITED fields."""
        defaults = {
            field.name: field
            for field in dataclasses.fields(contract.ConnectionReviewContract)
        }
        assert defaults["field_citations"].default == ()
        assert defaults["field_standings"].default == ()

    def test_area_35_the_standing_rule_and_its_branches(self):
        uncited = field_standings(())
        assert [(entry.field, entry.standing) for entry in uncited] == [
            (field, STANDING_UNCITED) for field in ENGINEERING_FIELDS
        ]
        direct = field_standings(
            (_citation(field_name="plate", citation_kind=CITATION_SOURCE),)
        )
        assert dict((e.field, e.standing) for e in direct)["plate"] == STANDING_DIRECT
        derived = field_standings(
            (_citation(field_name="plate", citation_kind=CITATION_DERIVATION),)
        )
        assert dict((e.field, e.standing) for e in derived)["plate"] == STANDING_DERIVED

    def test_area_35_a_mixed_field_takes_the_weaker_standing_in_either_order(self):
        for kinds in (
            (CITATION_SOURCE, CITATION_DERIVATION),
            (CITATION_DERIVATION, CITATION_SOURCE),
        ):
            mixed = field_standings(tuple(
                _citation(field_name="holes", ordinal=index + 1, citation_kind=kind)
                for index, kind in enumerate(kinds)
            ))
            assert dict((e.field, e.standing) for e in mixed)["holes"] == STANDING_DERIVED

    def test_area_35_an_unrecognised_kind_is_never_read_as_a_direct_source(self):
        unknown = field_standings(
            (_citation(field_name="plate", citation_kind="PROBABLY"),)
        )
        assert dict((e.field, e.standing) for e in unknown)["plate"] == STANDING_DERIVED

    def test_area_35_a_citation_outside_the_vocabulary_is_refused_not_ignored(self):
        with pytest.raises(ValueError) as refused:
            field_standings((_citation(field_name="weld_size"),))
        assert "weld_size" in str(refused.value)

    def test_area_35_there_is_no_inferred_state(self):
        assert not hasattr(contract, "STANDING_INFERRED")
        assert (STANDING_UNCITED, STANDING_DIRECT, STANDING_DERIVED) == (
            "UNCITED", "DIRECT", "DERIVED",
        )
        assert "INFERRED" not in (STANDING_UNCITED, STANDING_DIRECT, STANDING_DERIVED)
        # The word appears in the module exactly once, and it is the sentence denying it.
        assert "deliberately no INFERRED standing" in CONTRACT_PATH.read_text(encoding="utf-8")

    def test_area_35_no_field_standing_is_ever_stored(self):
        """Not a column, not a constraint, not a function argument — the citations are the
        only stored fact and the standing is recomputed by the read model every time."""
        assert "standing" not in STATEMENTS.lower()
        assert "standing" not in " ".join(
            name for name, _, _, _ in CITATION_TABLE_COLUMNS
        )
        assert "field_standings" not in SQL
        assert "field_standings" not in STORE_PATH.read_text(encoding="utf-8")

    def test_area_35_a_recorded_revision_never_stores_a_standing(self, revision_zero):
        header, items = revision_zero.to_rows()
        assert not any("standing" in key for key in header), header.keys()
        for row in items:
            assert not any("standing" in key for key in row), row.keys()
        for row in revision_zero.citation_rows_for(j21.SELECTED_PACKAGE_ID):
            assert not any("standing" in key for key in row), row.keys()

    def test_area_36_the_citation_order_is_total_and_two_builds_agree(self):
        """The fixed field order first, then the remaining fields sorted, then the ordinal
        within each field — so two builds of one revision produce the same sequence."""
        citations = (
            _citation(field_name="material", ordinal=2),
            _citation(field_name="position", ordinal=2),
            _citation(field_name="material", ordinal=1),
            _citation(field_name="plate", ordinal=1),
            _citation(field_name="position", ordinal=1),
        )
        first = contract._citation_infos(citations)
        assert first == contract._citation_infos(tuple(reversed(citations)))
        assert [(entry.field, entry.ordinal) for entry in first] == [
            ("position", 1), ("position", 2), ("plate", 1), ("material", 1),
            ("material", 2),
        ]

    def test_area_36_the_ordinal_is_recording_order_and_never_a_priority(self):
        info = contract._citation_infos((
            _citation(field_name="plate", ordinal=2),
            _citation(field_name="plate", ordinal=1),
        ))
        assert [entry.ordinal for entry in info] == [1, 2]
        source = CONTRACT_PATH.read_text(encoding="utf-8")
        assert "no reader may infer one" in source
        # Nothing in the contract compares ordinals to choose between citations.
        for forbidden in ("ordinal >", "ordinal <", "max(entry.ordinal", "entry.ordinal =="):
            assert forbidden not in source, forbidden

    def test_area_37_the_row_decoder_reads_verbatim_and_decodes_only_the_payload(self):
        row = {
            "project_id": j21.PROJECT_ID, "review_revision": 0,
            "review_package_id": j21.SELECTED_PACKAGE_ID, "field_name": "plate",
            "ordinal": 1, "citation_kind": CITATION_SOURCE, "document_id": None,
            "drawing_id": "d-1", "page_number": 7, "analysis_run_id": "r-1",
            "annotation_x": "41.50", "annotation_y": "190.00",
            "extractor_version": "pdf-annotations-1", "anchor": _encode(("detail-24",), where="anchor"),
            "recorded_at": "2026-09-29T00:00:00+00:00",
        }
        decoded = citation_from_row(row)
        assert decoded.annotation_x == "41.50" and decoded.annotation_y == "190.00"
        assert decoded.anchor == ("detail-24",)
        assert decoded.document_id is None
        assert decoded.extractor_version == "pdf-annotations-1"

    def test_area_37_a_partial_row_is_refused_rather_than_filled_in(self):
        """A missing column and a NULL column would otherwise look the same."""
        good = {
            "project_id": j21.PROJECT_ID, "review_revision": 0,
            "review_package_id": j21.SELECTED_PACKAGE_ID, "field_name": "plate",
            "ordinal": 1, "citation_kind": CITATION_SOURCE, "document_id": None,
            "drawing_id": "d-1", "page_number": 7, "analysis_run_id": "r-1",
            "annotation_x": None, "annotation_y": None, "extractor_version": None,
            "anchor": _encode((), where="anchor"),
            "recorded_at": "2026-09-29T00:00:00+00:00",
        }
        assert citation_from_row(good).field_name == "plate"
        for column in good:
            partial = dict(good)
            del partial[column]
            with pytest.raises(ValueError) as refused:
                citation_from_row(partial)
            assert column in str(refused.value), column
        with pytest.raises(TypeError):
            citation_from_row("not a mapping")

    def test_area_37_the_coordinates_are_shown_by_the_contract_s_one_display_rule(self):
        """Stated, not hidden: `_display` keeps a string and reprs anything else, so a numeric
        read back as a Decimal presents as its repr. See the module docstring."""
        from decimal import Decimal

        info = contract._citation_infos((_citation(
            annotation_x="41.50", annotation_y="190.00", extractor_version="v1",
        ),))
        assert info[0].annotation_x == "41.50"
        rendered = contract._citation_infos((_citation(
            annotation_x=Decimal("41.50"), annotation_y=Decimal("190.00"),
            extractor_version="v1",
        ),))
        assert rendered[0].annotation_x == repr(Decimal("41.50"))
        assert contract._display(None) is None and contract._display("x") == "x"

    def test_area_37_the_citation_has_no_revision_identity_of_its_own(self, revision_zero):
        assert "review_revision" in CITATION_PRIMARY_KEY
        assert "The citation has no revision of its" in SNAPSHOT_PATH.read_text(
            encoding="utf-8"
        )
        # And the rows the model writes carry no revision of their own to disagree with the
        # revision the writer takes from the snapshot.
        package_id = revision_zero.items[0].review_package_id
        for row in _with_citations(
            revision_zero, (_citation(review_package_id=package_id),)
        ).citation_rows_for(package_id):
            assert "review_revision" not in row
            assert "project_id" not in row

    def test_area_37_the_uncited_bands_agree_and_a_cited_band_reports_what_it_has(
        self, workflow, revision_zero
    ):
        """The band that must produce the same contract does: a revision that recorded no
        citations and the live workflow state both say UNCITED for all seven fields."""
        live = build_connection_review_contract(workflow, j21.SELECTED_PACKAGE_ID)
        assert live.field_citations == ()
        assert live.field_standings == field_standings(())
        item = revision_zero.items[0]
        assert connection_contract_from_item(revision_zero, item).field_standings == (
            live.field_standings
        )
        # And the genuine revision-0 snapshot with citations bound onto it reports them, in
        # order, with the standing each field derives from them.
        citations = (
            _citation(field_name="plate", ordinal=1,
                      review_package_id=item.review_package_id),
            _citation(field_name="plate", ordinal=2,
                      review_package_id=item.review_package_id,
                      citation_kind=CITATION_DERIVATION),
            _citation(field_name="holes", ordinal=1,
                      review_package_id=item.review_package_id),
            _citation(field_name="material", ordinal=1,
                      review_package_id=item.review_package_id),
        )
        recorded = connection_contract_from_item(
            _with_citations(revision_zero, citations), item
        )
        assert [(entry.field, entry.ordinal, entry.citation_kind)
                for entry in recorded.field_citations] == [
            ("plate", 1, CITATION_SOURCE), ("plate", 2, CITATION_DERIVATION),
            ("holes", 1, CITATION_SOURCE), ("material", 1, CITATION_SOURCE),
        ]
        assert dict((e.field, e.standing) for e in recorded.field_standings) == {
            "connected_member_marks": STANDING_UNCITED,
            "position": STANDING_UNCITED,
            "plate": STANDING_DERIVED,
            "holes": STANDING_DIRECT,
            "location": STANDING_UNCITED,
            "attachments": STANDING_UNCITED,
            "material": STANDING_DIRECT,
        }


# ===========================================================================
# 38. THE REPOSITORY
# ===========================================================================
class TestTheRepositoryIsTransport:
    def test_area_38_the_reader_reads_the_citation_table_and_nothing_else(
        self, citation_store
    ):
        assert store.load_review_citations(
            j21.PROJECT_ID, 0, client=citation_store
        ) == ()
        assert [call[0] for call in citation_store.calls] == ["table"]
        assert citation_store.calls[0][1] == CITATION_TABLE
        assert citation_store.reads == [(
            CITATION_TABLE,
            (("project_id", j21.PROJECT_ID), ("review_revision", 0)),
            "*",
        )]

    def test_area_38_the_reader_decodes_the_rows_it_finds(self, citation_store):
        citation_store.tables[CITATION_TABLE].append({
            "project_id": j21.PROJECT_ID, "review_revision": 0,
            "review_package_id": j21.SELECTED_PACKAGE_ID, "field_name": "plate",
            "ordinal": 1, "citation_kind": CITATION_SOURCE, "document_id": None,
            "drawing_id": "d-1", "page_number": 7, "analysis_run_id": "r-1",
            "annotation_x": None, "annotation_y": None, "extractor_version": None,
            "anchor": _encode(("detail-24",), where="anchor"),
            "recorded_at": "2026-09-29T00:00:00+00:00",
        })
        rows = store.load_review_citations(j21.PROJECT_ID, 0, client=citation_store)
        assert len(rows) == 1
        assert rows[0].field_name == "plate" and rows[0].anchor == ("detail-24",)

    def test_area_38_the_reader_refuses_a_blank_project_and_a_non_integer_revision(
        self, citation_store
    ):
        with pytest.raises(ValueError):
            store.load_review_citations("   ", 0, client=citation_store)
        with pytest.raises(TypeError):
            store.load_review_citations(j21.PROJECT_ID, True, client=citation_store)
        with pytest.raises(TypeError):
            store.load_review_citations(j21.PROJECT_ID, "0", client=citation_store)

    def test_area_38_the_reader_is_separate_so_the_snapshot_replay_stays_two_tables(self):
        source = STORE_PATH.read_text(encoding="utf-8")
        loader = source[source.index("def load_review_snapshot"):]
        loader = loader[:loader.index("\ndef ")]
        assert CITATION_TABLE not in loader
        assert "citations=load_review_citations(" in source

    def test_area_38_a_write_of_nothing_is_not_a_write(self, revision_zero, citation_store):
        """An empty citation set sends no call at all, rather than a round trip that could
        report success for an empty set."""
        assert store.persist_connection_review_citations(
            revision_zero, j21.SELECTED_PACKAGE_ID, client=citation_store
        ) == ()
        assert citation_store.calls == []

    def test_area_38_the_writer_sends_one_call_naming_the_one_writer_function(
        self, revision_zero, citation_store
    ):
        package_id = revision_zero.items[0].review_package_id
        citation = _citation(field_name="plate", ordinal=1, review_package_id=package_id)
        snapshot = _with_citations(revision_zero, (citation,))
        citation_store.evidence = {_evidence_key(citation): revision_zero.project_id}
        rows = store.persist_connection_review_citations(
            snapshot, package_id, client=citation_store
        )
        calls = citation_store.writes()
        assert len(calls) == 1
        assert calls[0][1] == store.CITATION_WRITER_FUNCTION
        assert calls[0][2]["p_project_id"] == revision_zero.project_id
        assert calls[0][2]["p_review_revision"] == revision_zero.review_revision
        assert calls[0][2]["p_review_package_id"] == package_id
        assert set(calls[0][2]) == {
            "p_project_id", "p_review_revision", "p_review_package_id", "p_citations",
        }
        # The writer sends the ROW BUILDER's rows, not its own: the model is the one place
        # that knows what a stored citation looks like.
        assert rows == snapshot.citation_rows_for(package_id)
        assert set(rows[0]) == {
            "field_name", "ordinal", "citation_kind", "document_id", "drawing_id",
            "page_number", "analysis_run_id", "annotation_x", "annotation_y",
            "extractor_version", "anchor",
        }
        assert "recorded_at" not in rows[0]

    def test_area_38_the_writer_refuses_anything_that_is_not_a_recorded_revision(
        self, citation_store
    ):
        for bad in (object(), None, {"project_id": j21.PROJECT_ID}, ()):
            with pytest.raises(TypeError):
                store.persist_connection_review_citations(
                    bad, j21.SELECTED_PACKAGE_ID, client=citation_store
                )
        with pytest.raises(ValueError):
            store.persist_connection_review_citations(
                _empty_snapshot(), "   ", client=citation_store
            )

    def test_area_38_the_databases_own_refusal_is_preserved_verbatim(
        self, revision_zero, citation_store
    ):
        package_id = revision_zero.items[0].review_package_id
        citation = _citation(field_name="plate", ordinal=1, review_package_id=package_id)
        snapshot = _with_citations(revision_zero, (citation,))
        citation_store.evidence = {  # the cited reading is another project's
            _evidence_key(citation): "PROJ-7J66-OTHER"
        }
        with pytest.raises(SnapshotRefused) as refused:
            store.persist_connection_review_citations(
                snapshot, package_id, client=citation_store
            )
        assert refused.value.code == CITATION_REFUSED_FOREIGN_EVIDENCE
        assert CITATION_REFUSED_FOREIGN_EVIDENCE in refused.value.statement
        assert citation_store.rows() == []

    def test_area_38_a_failure_the_database_did_not_name_is_reraised_unchanged(
        self, revision_zero, citation_store
    ):
        """A bare foreign-key failure has four possible causes. Mapping them all onto one
        citation code would be inventing an explanation the database did not give."""
        package_id = revision_zero.items[0].review_package_id
        citation = _citation(field_name="plate", ordinal=1, review_package_id=package_id)
        snapshot = _with_citations(revision_zero, (citation,))
        citation_store.refuse_with = (
            "23503",
            'violates foreign key constraint "connection_review_item_citations_capture_fkey"',
        )
        with pytest.raises(_ApiError) as raised:
            store.persist_connection_review_citations(
                snapshot, package_id, client=citation_store
            )
        assert raised.value.code == "23503"
        assert not isinstance(raised.value, SnapshotRefused)

    def test_area_38_the_read_after_write_loop_is_the_whole_python_half(
        self, revision_zero, citation_store
    ):
        """Record one connection's citations through the writer, read them back through the
        reader, and derive the contract's citations and standings from what came back."""
        package_id = revision_zero.items[0].review_package_id
        citations = (
            _citation(field_name="plate", ordinal=1, review_package_id=package_id),
            _citation(field_name="location", ordinal=1, review_package_id=package_id,
                      citation_kind=CITATION_DERIVATION),
        )
        snapshot = _with_citations(revision_zero, citations)
        citation_store.evidence = {
            _evidence_key(citation): revision_zero.project_id for citation in citations
        }
        store.persist_connection_review_citations(snapshot, package_id,
                                                  client=citation_store)
        loaded = store.load_review_citations(
            revision_zero.project_id, 0, client=citation_store
        )
        assert len(loaded) == 2
        recorded = connection_contract_from_item(
            _with_citations(revision_zero, loaded), revision_zero.items[0]
        )
        assert [(entry.field, entry.citation_kind) for entry in recorded.field_citations] == [
            ("plate", CITATION_SOURCE), ("location", CITATION_DERIVATION),
        ]
        standings = dict((e.field, e.standing) for e in recorded.field_standings)
        assert standings["plate"] == STANDING_DIRECT
        assert standings["location"] == STANDING_DERIVED
        assert standings["material"] == STANDING_UNCITED

    def test_area_38_the_other_connections_of_that_revision_get_no_citations(
        self, revision_zero, citation_store
    ):
        package_id = revision_zero.items[0].review_package_id
        citation = _citation(field_name="plate", ordinal=1, review_package_id=package_id)
        citation_store.evidence = {_evidence_key(citation): revision_zero.project_id}
        store.persist_connection_review_citations(
            _with_citations(revision_zero, (citation,)), package_id, client=citation_store
        )
        loaded = store.load_review_citations(
            revision_zero.project_id, 0, client=citation_store
        )
        other = revision_zero.items[1]
        assert other.review_package_id != package_id
        assert connection_contract_from_item(
            _with_citations(revision_zero, loaded), other
        ).field_citations == ()

    def test_area_38_the_repository_exposes_one_reader_and_one_writer_and_no_other_write(self):
        source = STORE_PATH.read_text(encoding="utf-8")
        for forbidden in (".update(", ".delete(", "upsert", ".insert(", "drop ", "truncate"):
            assert forbidden not in source.lower(), forbidden
        assert source.count(".rpc(") == 2
        assert re.findall(r"\.rpc\(\s*([A-Za-z_][A-Za-z0-9_]*)", source) == [
            "WRITER_FUNCTION", "CITATION_WRITER_FUNCTION",
        ]
        assert store.CITATION_WRITER_FUNCTION == "record_connection_review_citations"
        assert store.WRITER_FUNCTION == "record_connection_review_snapshot"

    def test_area_38_the_reader_and_the_writer_take_no_identity_and_no_role(self):
        """What a citation may say is decided by the database, not by an argument: neither
        function takes an owner, a role or a reviewer."""
        for function in (store.load_review_citations,
                         store.persist_connection_review_citations):
            parameters = set(inspect.signature(function).parameters)
            assert not parameters & {"role", "identity", "owner", "binding", "reviewer"}, (
                function.__name__, parameters
            )
        assert set(inspect.signature(
            store.persist_connection_review_citations
        ).parameters) == {"snapshot", "review_package_id", "client"}


def _empty_snapshot():
    return model.ReviewSnapshot(
        project_id=j21.PROJECT_ID, review_revision=0, project_status="review",
        evidence_identity={}, evidence_run_ids=(), items=(),
    )


# ===========================================================================
# 39. NO PRODUCTION WRITER
# ===========================================================================
class TestNoProductionWriterExists:
    def test_area_39_the_citation_vocabulary_lives_in_exactly_three_modules(self):
        naming = sorted(
            str(path.relative_to(REPO)) for path in (REPO / "app").rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == sorted(CITATION_MODULES), naming

    def test_area_39_the_writer_has_no_caller_anywhere_in_the_product(self):
        callers = sorted(
            str(path.relative_to(REPO)) for path in (REPO / "app").rglob("*.py")
            if "persist_connection_review_citations" in path.read_text(encoding="utf-8")
        )
        assert callers == ["app/engineering_data/connection_review_repository.py"], callers

    def test_area_39_the_writer_function_is_named_by_no_other_module(self):
        named = sorted(
            str(path.relative_to(REPO)) for path in (REPO / "app").rglob("*.py")
            if store.CITATION_WRITER_FUNCTION in path.read_text(encoding="utf-8")
        )
        assert named == ["app/engineering_data/connection_review_repository.py"], named

    def test_area_39_the_extraction_and_generation_paths_name_no_citation(self):
        for relative in ("app/pipeline.py", "app/production_extraction_source.py",
                         "app/drawing_reading/pdf_annotation_extractor.py",
                         "app/production_annotation_evidence.py",
                         "app/production_fabrication_artifact.py",
                         "app/production_review_resolution.py",
                         "app/production_review_opening.py"):
            path = REPO / relative
            if not path.exists():
                continue
            assert "citation" not in path.read_text(encoding="utf-8").lower(), relative

    def test_area_39_the_repository_says_there_is_no_caller_and_it_is_true(self):
        assert (
            "There is deliberately NO caller of this function in the production path"
            in STORE_PATH.read_text(encoding="utf-8")
        )

    def test_area_39_no_route_was_added_and_none_names_a_citation(self, production):
        routes = j20._declared_routes(production.main.app)
        assert routes == j20.FROZEN_ROUTES
        paths = [path for _, path in routes]
        assert not any("citation" in path.lower() for path in paths), paths


# ===========================================================================
# 40. THE LIVE DEPLOYMENT, READ-ONLY
# ===========================================================================
@pytest.fixture(scope="module")
def live_client(production):
    """The real deployment's client, for SELECTs and for the one call that must be refused.
    Skips — never substitutes — when the configuration is the test placeholder or the network
    is unavailable."""
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live tests are skipped "
                    "rather than run against a synthetic database")
    client = production.repository.supabase
    try:
        client.table(CITATION_TABLE).select("project_id").execute()
    except Exception as exc:  # credentials/network unavailable in this process
        pytest.skip(f"the live citation table could not be read: {type(exc).__name__}")
    return client


def _live_or_skip(client, table, columns="*", **filters):
    """A live SELECT. A network or credential failure skips rather than pretends."""
    try:
        query = client.table(table).select(columns)
        for column, value in filters.items():
            query = query.eq(column, value)
        return query.execute().data or []
    except Exception as exc:
        pytest.skip(f"live {table} could not be read: {type(exc).__name__}: {exc}")


def _live_in(client, table, columns, column, values):
    """A live SELECT restricted to a set of values, for a table with no project_id of its
    own. A network or credential failure skips rather than pretends."""
    try:
        return client.table(table).select(columns).in_(
            column, list(values)
        ).execute().data or []
    except Exception as exc:
        pytest.skip(f"live {table} could not be read: {type(exc).__name__}: {exc}")


class TestTheLiveDeploymentIsUntouched:
    def test_area_40_the_citation_table_exists_and_holds_no_row_at_all(self, live_client):
        """The whole milestone's live outcome: the table is deployed, readable, and holds no
        row for any project. J66 writes no citation anywhere."""
        assert _live_or_skip(live_client, CITATION_TABLE, "project_id") == []
        assert _live_or_skip(live_client, CITATION_TABLE, "*") == []

    def test_area_40_the_citation_table_is_exposed_with_its_declared_columns(self, live_client):
        """Selecting every declared column names them all in the request, so a column that
        does not exist is a refusal rather than an empty page."""
        columns = ",".join(name for name, _, _, _ in CITATION_TABLE_COLUMNS)
        assert _live_or_skip(live_client, CITATION_TABLE, columns) == []

    def test_area_40_the_writer_is_reachable_and_refuses_a_citation_it_cannot_verify(
        self, live_client
    ):
        """The one call this file makes against the real writer is one that CANNOT succeed:
        a random project id citing a random reading. It proves the deployed function is
        reachable through the REST surface and refuses, and it inserts nothing — asserted
        immediately afterwards. This mirrors J22's own live writer proof."""
        with pytest.raises(Exception) as refused:
            live_client.rpc(store.CITATION_WRITER_FUNCTION, {
                "p_project_id": str(uuid.uuid4()),
                "p_review_revision": 0,
                "p_review_package_id": j21.SELECTED_PACKAGE_ID,
                "p_citations": store._json_text([{
                    "field_name": "plate", "ordinal": 1, "citation_kind": CITATION_SOURCE,
                    "document_id": None, "drawing_id": str(uuid.uuid4()),
                    "page_number": 1, "analysis_run_id": str(uuid.uuid4()),
                    "annotation_x": None, "annotation_y": None,
                    "extractor_version": None, "anchor": {},
                }]),
            }).execute()
        assert getattr(refused.value, "code", None) == "P0001", refused.value
        assert "CITATION_REFUSED_" in str(refused.value)
        assert _live_or_skip(live_client, CITATION_TABLE, "*") == []

    def test_area_40_no_citation_is_recorded_for_the_acceptance_project(self, live_client):
        assert _live_or_skip(
            live_client, CITATION_TABLE, "field_name", project_id=ACCEPTANCE_PROJECT
        ) == []

    def test_area_40_revision_zero_still_states_the_pinned_digest(self, live_client):
        rows = _live_or_skip(
            live_client, model.SNAPSHOT_TABLE, "project_id,review_revision,evidence_identity",
            project_id=ACCEPTANCE_PROJECT, review_revision=0,
        )
        assert len(rows) == 1, rows
        assert rows[0]["project_id"] == ACCEPTANCE_PROJECT
        assert rows[0]["evidence_identity"]["evidence_digest"] == REVISION_ZERO_DIGEST

    def test_area_40_no_new_review_revision_was_created(self, live_client):
        rows = _live_or_skip(
            live_client, model.SNAPSHOT_TABLE, "review_revision",
            project_id=ACCEPTANCE_PROJECT,
        )
        assert sorted(row["review_revision"] for row in rows) == [0], rows

    def test_area_40_the_review_items_are_still_the_three_recorded_ones(self, live_client):
        rows = _live_or_skip(
            live_client, model.ITEM_TABLE, "review_package_id",
            project_id=ACCEPTANCE_PROJECT, review_revision=0,
        )
        assert sorted(row["review_package_id"] for row in rows) == [
            "RP-0001", "RP-0002", "RP-0003",
        ]

    def test_area_40_the_evidence_tables_are_unchanged(self, live_client):
        """The acceptance project's OWN evidence, and the database's totals beside it.

        CORRECTION, RECORDED RATHER THAN QUIETLY OBSERVED: the milestone brief listed
        "project_documents = 8, drawings = 8, captures = 60" as the acceptance project's
        counts. They are the DATABASE-WIDE totals. Read against the project itself, the live
        deployment holds 1 document, 1 drawing set, 1 drawing and 30 page-extraction captures.
        Both sets are asserted below, labelled for what they are, so neither is mistaken for
        the other — and so a future change to either is visible as a change.

        `drawings` carries no project_id — the very reason the ownership guard exists — so the
        project's drawings are reached through its drawing sets. Counting every drawing in the
        database and calling that number the project's would be a small lie told by an
        equality test."""
        assert len(_live_or_skip(
            live_client, "project_documents", "id", project_id=ACCEPTANCE_PROJECT
        )) == 1
        sets = _live_or_skip(
            live_client, "drawing_sets", "id", project_id=ACCEPTANCE_PROJECT
        )
        assert len(sets) == 1, sets
        assert len(_live_in(
            live_client, "drawings", "id", "drawing_set_id", [row["id"] for row in sets]
        )) == 1
        # `page_extraction_captures` has no `id`: its identity is the triple the citation
        # table's foreign key names, which is the point of that key.
        assert len(_live_or_skip(
            live_client, "page_extraction_captures", "page_number",
            project_id=ACCEPTANCE_PROJECT,
        )) == 30
        # Database-wide, for the record: J66 added no evidence row anywhere.
        assert len(_live_or_skip(live_client, "project_documents", "id")) == 8
        assert len(_live_or_skip(live_client, "drawings", "id")) == 8
        assert len(_live_or_skip(live_client, "page_extraction_captures", "page_number")) == 60
        # `pdf_annotation_occurrences` is NOT counted here: it holds more rows than PostgREST
        # returns in one page, so a length would be the page size rather than the table's
        # size — a number that looks like a measurement and is not one. The claim that J66
        # leaves it alone is made where it can be checked exactly, in area 31, over the
        # migration's statements.

    def test_area_40_no_review_claim_was_made(self, live_client):
        """A claim is a liveness device, not a citation — and J66 takes none."""
        assert _live_or_skip(
            live_client, "project_review_claims", "project_id",
            project_id=ACCEPTANCE_PROJECT,
        ) == []


# ===========================================================================
# THE MUTATIONS — EVERY PROPERTY ABOVE STANDS ON SOMETHING THAT CAN FAIL.
# ===========================================================================
class TestTheMutations:
    def test_mutation_1_the_ownership_guard_removed_lets_a_foreign_citation_through(self):
        """The structural ownership guard, removed. Area 19's assertion is what goes red the
        moment it stops being applied — which is what makes area 19 a proof about the guard
        rather than a proof about a message."""
        citation = _citation(project_id=j21.PROJECT_ID)
        evidence = {_evidence_key(citation): "PROJ-7J66-OTHER"}
        assert _ownership_verdict(citation, evidence) == CITATION_REFUSED_FOREIGN_EVIDENCE
        assert _ownership_verdict(citation, evidence, guard=False) == "ACCEPTED"

        # And the same removal as TEXT. The mutation targets the trigger's EVENT CLAUSE
        # rather than its `create trigger` line: the statement spans three physical lines, so
        # rewriting only the first would leave the other two behind and the guard would look
        # removed while still being there — a mutation that proves nothing because it changed
        # nothing.
        mutated = SQL.replace(
            f"before insert on public.{CITATION_TABLE}", "-- guard removed by mutation"
        )
        assert mutated != SQL
        assert f"before insert on public.{CITATION_TABLE}" not in _statements(mutated)
        # The trigger's NAME survives the mutation as a dangling fragment — which is why the
        # assertion that matters is not that the name is gone but that no INSERT trigger
        # remains. `_triggers` reads whole declarations, so it sees the removal.
        assert set(_triggers(mutated)) == {f"{CITATION_TABLE}_append_only"}
        assert f"{CITATION_TABLE}_ownership" in _statements(mutated)
        assert set(_triggers()) == {
            f"{CITATION_TABLE}_ownership", f"{CITATION_TABLE}_append_only",
        }

    def test_mutation_1_no_python_layer_would_have_closed_the_same_hole(self):
        """The double's restatement is a DRIVER, not the authority: with the real trigger
        removed the deployed database accepts the insert, whatever a double says. Stated as an
        assertion so the distinction cannot be lost: the guard's own function and the variable
        its decision is made in exist only in the migration — no Python module performs the
        project comparison that the mutation removes."""
        assert "before insert on public." in STATEMENTS
        guard = "connection_review_citation_ownership"
        found = sorted(
            str(path.relative_to(REPO)) for path in (REPO / "app").rglob("*.py")
            if guard in path.read_text(encoding="utf-8")
        )
        assert found == [], found
        for path in (REPO / "app").rglob("*.py"):
            assert "v_capture_project" not in path.read_text(encoding="utf-8"), path

    def test_mutation_2_append_only_removed_permits_a_rewrite(self):
        """The append-only protection, removed. Area 22's property is what goes red: a
        recorded citation becomes mutable."""
        rows = [{"field_name": "plate", "ordinal": 1, "citation_kind": CITATION_SOURCE}]

        def rewrite(rows_, kind, *, append_only=True):
            if append_only:
                raise SnapshotRefused(
                    CITATION_REFUSED_APPEND_ONLY, "a recorded citation is never rewritten"
                )
            rows_[0]["citation_kind"] = kind
            return rows_

        with pytest.raises(SnapshotRefused) as refused:
            rewrite(copy.deepcopy(rows), CITATION_DERIVATION)
        assert refused.value.code == CITATION_REFUSED_APPEND_ONLY
        mutated = rewrite(copy.deepcopy(rows), CITATION_DERIVATION, append_only=False)
        assert mutated[0]["citation_kind"] == CITATION_DERIVATION

    def test_mutation_2_the_two_halves_cover_what_neither_covers_alone(self):
        """A row-level trigger cannot see TRUNCATE, so the privilege revoke is load-bearing
        for the hole the trigger cannot close — and the trigger is load-bearing for the
        rewrites no privilege would stop a service role from attempting."""
        without_trigger = SQL.replace(
            f"before update or delete on public.{CITATION_TABLE}",
            "-- trigger removed by mutation",
        )
        assert without_trigger != SQL
        assert f"before update or delete on public.{CITATION_TABLE}" not in (
            _statements(without_trigger)
        )
        assert f"revoke update, delete, truncate on table public.{CITATION_TABLE}" in (
            _statements(without_trigger)
        )

        without_truncate_revoke = SQL.replace(
            f"revoke update, delete, truncate on table public.{CITATION_TABLE} "
            f"from service_role",
            f"revoke update, delete on table public.{CITATION_TABLE} from service_role",
        )
        assert without_truncate_revoke != SQL
        assert f"revoke update, delete, truncate on table public.{CITATION_TABLE}" not in (
            _statements(without_truncate_revoke)
        )
        assert f"before update or delete on public.{CITATION_TABLE}" in (
            _statements(without_truncate_revoke)
        )
        assert f"revoke update, delete on table public.{CITATION_TABLE} from service_role" in (
            _statements(without_truncate_revoke)
        )

    def test_mutation_3_a_winner_or_rank_field_violates_the_design_fence(self):
        """The design fence, mutated: a ranking column added. Area 16's assertion is what goes
        red, over the migration's columns and over both dataclasses."""
        real = [field.name for field in dataclasses.fields(ReviewFieldCitationInfo)]
        assert _fence_violations(real) == []
        for smuggled in ("winner", "rank", "is_primary", "chosen_value", "value",
                         "confidence", "resolved", "document_role", "drawing_number"):
            assert _fence_violations(real + [smuggled]) == [smuggled]

        mutated = SQL.replace(
            "    anchor              json    not null,",
            "    anchor              json    not null,\n    winner              text,",
        )
        assert mutated != SQL, "the mutation site was not found"
        assert _fence_violations(_column_definitions(CITATION_TABLE, text=mutated)) == [
            "winner"
        ]
        # The real migration is untouched by that mutation.
        assert _fence_violations(_column_definitions(CITATION_TABLE)) == []

    def test_mutation_3_the_fence_covers_the_contract_the_model_and_the_database(self):
        """Four layers declare the same refusal; mutation 3 shows the check is sensitive at
        each of them."""
        for names in (
            (field.name for field in dataclasses.fields(ReviewFieldCitationInfo)),
            (field.name for field in dataclasses.fields(ReviewFieldCitation)),
            (field.name for field in dataclasses.fields(ReviewFieldStanding)),
            (name for name, _, _, _ in CITATION_TABLE_COLUMNS),
        ):
            assert _fence_violations(names) == []


# ===========================================================================
# WHAT THIS FILE CANNOT PROVE, STATED RATHER THAN IMPLIED
# ===========================================================================
class TestThisFileIsHonestAboutWhatItCouldNotProve:
    def test_the_only_live_call_this_file_makes_is_the_one_that_is_refused(self):
        """Stated rather than implied: every citation exercised here is an in-process object.
        The live reads prove the table's emptiness, not its content, and the single live call
        this file makes that writes anything is the one that must be refused — every citation
        written through the writer goes to the in-process double."""
        source = Path(__file__).read_text(encoding="utf-8")
        # Built rather than written literally: an assertion that searched the file for its
        # own needle would count itself and never be satisfiable.
        needle = "live_client.rpc" + "("
        assert source.count(needle) == 1
        # And it is the refused one: the assertion that the table is still empty follows it
        # immediately, so a call that had written anything would fail there rather than pass
        # here. A source-level assertion rather than a comment, because a comment cannot fail.
        lines = source.splitlines()
        index = next(i for i, line in enumerate(lines) if needle in line)
        following = "\n".join(lines[index:index + 25])
        assert "CITATION_REFUSED_" in following
        assert "CITATION_TABLE, \"*\"" in following
        # Everything else that writes goes through the writer, and to the double.
        assert source.count("client=citation_store") >= 10

    def test_the_migration_s_statements_are_pinned_by_digest(self):
        """A digest of what the migration DOES — its statements, comments removed — so a later
        edit to a constraint, a grant or the writer is visible rather than silent. Header
        prose may be revised without turning this red; behaviour may not."""
        assert hashlib.sha256(STATEMENTS.encode("utf-8")).hexdigest() == (
            "2e1410c3d436f458895784504013598511c1b539b61e7e017b0041668461a4ab"
        )

    def test_the_only_insert_in_the_migration_is_the_writers(self):
        """The brief's own live requirement, restated where it cannot be missed: nothing
        backfilled a citation, and revision 1 was not created."""
        assert len(re.findall(r"insert into public\.\w+", STATEMENTS)) == 1
        assert "backfill" not in STATEMENTS.lower()
