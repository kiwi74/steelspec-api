"""
Milestone J21 — CONNECTION-REVIEW PERSISTENCE DATA MODEL: THE PROOFS.

The brief's mandate: "First prove the data model." These tests are that
proof. They run entirely in process over the REAL Arkles Strand capture and
the GENUINE 7AJ workspace — no database, no route, no migration — and they
assert the design's central claim:

    a project's 7AK review contract can be rebuilt, byte for byte, from the
    two designed tables and nothing else.

The proofs, in the order the brief asks for them:

  1. COVERAGE. Every field of 7AK's two contracts is either stored in a
     named column or derived by a named rule, and nothing else is stored.
  2. FIDELITY. Both genuine revisions of the real project — the untouched
     one (three REVIEW candidates) and the resolved one (a human answer for
     every task, provenance labels, a generated artifact) — round-trip
     through the stored rows to contracts that compare EQUAL to the
     contracts the real 7AK builder produces.
  3. THE COLUMN TYPE. The payload must be `json`, not `jsonb`: a jsonb
     column reorders object keys, and the contract's own `repr()` display
     strings expose key order. The proof is a real divergence on the real
     human answer (`{'type': 'end_plate', ...}` reordered), not an argument.
  4. THE STATE DISTINCTIONS. The AI's silence is a present null, never a
     missing key; "never attempted" is a NULL column, never a status; and a
     distinction the review layer already collapsed (a raw JSON key 7W's
     parse dropped) is not claimed to be preserved.
  5. VERSIONING AND STALENESS. Revisions are 7AJ's own; a second review is a
     NEW row, an older one is never rewritten; an evidence change makes a
     snapshot stale; a read that changed no evidence (a J17 retry that only
     recorded a run) does not.
  6. THE GATES. Nothing here is production-bound: the route set, the
     migrations, the J19 binding and the J13 status authority are all
     asserted unchanged, and the design module is imported by nothing.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import importlib
import inspect
import json
import os
import sys
import types
from pathlib import Path

import pytest

from app.cad_engine import connection_review_snapshot as model
from app.cad_engine.automation_gate import (
    AUTOMATION_DECISIONS,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.connection_review_package import (
    AIExtractedBolt,
    AIExtractedConnection,
    AIExtractedPlate,
    AIExtractedWeld,
)
from app.cad_engine.connection_review_snapshot import (
    APPEND_ONLY_RULE,
    CITATION_TABLE,
    CITATION_TABLE_COLUMNS,
    EVIDENCE_KIND,
    EVIDENCE_TABLES,
    EXISTING_PROJECT_RULE,
    EXISTING_TABLE_REUSE,
    ITEM_CHECK_VOCABULARY,
    ITEM_TABLE,
    ITEM_TABLE_COLUMNS,
    NO_REUSE_RULES,
    PAYLOAD_COLUMNS,
    RLS_RULE,
    SNAPSHOT_REFUSED_DUPLICATE_PACKAGE_ID,
    SNAPSHOT_REFUSED_NO_REVIEW_LAYER,
    SNAPSHOT_REFUSED_PROJECT_MISMATCH,
    SNAPSHOT_REFUSED_PROJECT_UNKNOWN,
    SNAPSHOT_REFUSED_REVISION_GAP,
    SNAPSHOT_TABLE,
    SNAPSHOT_TABLE_COLUMNS,
    TUPLE_TAG,
    SnapshotRefused,
    build_review_snapshot,
    connection_contract_from_item,
    evidence_identity,
    latest_snapshot,
    project_contract_from_snapshot,
    snapshot_from_rows,
    snapshot_is_stale,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUSES
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUSES
from app.cad_engine.exception_resolution import HumanResolution, STATUS_RESOLVED
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.project_workflow import (
    resolve_project_connection,
    start_project_workflow,
)
from app.cad_engine.review_contract import (
    ConnectionReviewContract,
    ProjectReviewContract,
    build_project_review_contract,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests.test_project_extraction_intake import CAPTURE_PATH, real_known_marks
from tests.test_real_world_exception_proof import (
    ARKLES_REAL_AI_EXTRACTION,
    HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE,
    HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
    HUMAN_SUPPLIED_MEMBER_ROWS,
    HUMAN_SUPPLIED_SECTION_MATCHER,
)
from tests.test_real_world_j20_connection_review_persistence_gap import (
    FROZEN_ROUTES,
    _declared_routes,
)

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "cad_engine" / "connection_review_snapshot.py"

PROJECT_ID = "PROJ-7J21"
OTHER_PROJECT_ID = "PROJ-7J21-OTHER"
SOURCE_DRAWING_ID = "ARKLES-STRAND"
DRAWING_ID = "dr-j21"
DRAWING_SET_PAGE_COUNT = 41
SELECTED_PACKAGE_ID = "RP-0001"

# The evidence a fresh project has: no rows. An explicit empty row list per table — the
# fingerprint refuses a missing table rather than reading it as empty.
NO_EVIDENCE = {"steel_members": [], "connections": []}

needs_real_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason="the real Arkles connection extraction is not present; the data-model proof is "
           "skipped rather than run over a fabricated capture",
)

production = j4.production
live = j4.live


# =============================================================================
# THE GENUINE WORKFLOWS — the real capture, through the real 7AJ operations.
# =============================================================================
def _arkles_workflow():
    intake = intake_page_extractions(
        list(ARKLES_REAL_AI_EXTRACTION),
        project_id=PROJECT_ID,
        source_drawing_id=SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
        drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
    )
    return start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )


def _human_resolutions(workflow, package_id):
    """One human answer per task, in the candidate's own task order — the reviewer's own
    answers, recorded through 7AC's contract exactly as a reviewer would."""
    group = next(
        group for group in workflow.exception_package.connection_tasks
        if group.review_package_id == package_id
    )
    return [
        HumanResolution(
            task.task_id, task.task_type, task.answer_type,
            copy.deepcopy(HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[task.task_type]),
            evidence="HUMAN-SUPPLIED TEST DATA — the reviewer's explicit answer to this task",
        )
        for task in group.tasks
    ]


@pytest.fixture(scope="module")
def workflow():
    if not ARKLES_REAL_AI_EXTRACTION:
        pytest.skip("the real Arkles capture is not present")
    return _arkles_workflow()


@pytest.fixture(scope="module")
def revision_zero(workflow):
    return build_review_snapshot(
        workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE, previous_revision=None
    )


@pytest.fixture(scope="module")
def resolved_workflow(workflow, tmp_path_factory):
    if not ARKLES_REAL_AI_EXTRACTION:
        pytest.skip("the real Arkles capture is not present")
    return resolve_project_connection(
        workflow,
        package_id=SELECTED_PACKAGE_ID,
        resolutions=_human_resolutions(workflow, SELECTED_PACKAGE_ID),
        output_dir=str(tmp_path_factory.mktemp("j21-artifacts")),
    )


@pytest.fixture(scope="module")
def revision_one(resolved_workflow):
    return build_review_snapshot(
        resolved_workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
        previous_revision=0,
    )


@pytest.fixture()
def env_bound_modules():
    """
    The two production modules this file must import that read their environment at
    IMPORT time (`app/config.py` does `os.environ["SUPABASE_URL"]`), imported here under
    the test placeholder — and the modules this import ADDED to `sys.modules` are removed
    again at teardown.

    That teardown is load-bearing, not tidiness. `monkeypatch` restores `os.environ` but
    never `sys.modules`: left cached, these modules would hand a LATER file in the same
    session (the live-catalogue J4/J5/J6/J7 tests, which import `app.*` under the repo's
    real .env) a client already bound to the placeholder host, and those tests would fail
    on a DNS lookup instead of reading the live catalogue. The same rule the production
    authority milestone established, and the same teardown `tests/test_real_world_j4_*`'s
    `production` fixture performs for its own modules.
    """
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = PLACEHOLDER_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = PLACEHOLDER_SERVICE_KEY
    try:
        yield types.SimpleNamespace(
            matcher=importlib.import_module("app.engineering_data.section_matcher"),
            binding=importlib.import_module("app.production_review.binding"),
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name in set(sys.modules) - before:
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


# =============================================================================
# THE HARNESS — the wire (json column text) round trip, and the jsonb rejection.
# =============================================================================
def _wire(value):
    """What a `json` column carries: the text the client sent, parsed back."""
    return json.loads(json.dumps(value, ensure_ascii=False))


def _round_trip(snapshot):
    """The snapshot as it comes back from the two tables, through real JSON text."""
    header, rows = snapshot.to_rows()
    return snapshot_from_rows(_wire(header), _wire(list(rows)))


def _jsonb_like(value):
    """What a `jsonb` column does to a value: normalise every object's key order (and drop
    duplicate keys). Applied recursively, so the simulation is not partial."""
    if isinstance(value, list):
        return [_jsonb_like(entry) for entry in value]
    if isinstance(value, dict):
        return {key: _jsonb_like(value[key]) for key in sorted(value)}
    return value


def _jsonb_round_trip(snapshot):
    header, rows = snapshot.to_rows()
    return snapshot_from_rows(_jsonb_like(_wire(header)), _jsonb_like(_wire(list(rows))))


def _codes(contract):
    return tuple(info.code for info in contract.blockers)


def _all_fields(cls):
    return tuple(field.name for field in dataclasses.fields(cls))


# --------------------------------------------------------------------------------------
# Source-level helpers. These read the design module the way a compiler does, so a claim
# about it is about its CODE — a docstring that names a table the model refuses to read,
# or a rule that spells out why a table is not reused, is documentation, not a reference.
# --------------------------------------------------------------------------------------
def _tree():
    return ast.parse(MODULE_PATH.read_text())


def _docstrings(tree):
    """The Constant nodes that are docstrings: the prose a module carries about itself."""
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            found.add(id(first.value))
    return found


def _code_strings(tree):
    """Every string literal the code can hand around, docstrings excluded."""
    docstrings = _docstrings(tree)
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _identifiers(tree):
    """Every name the code can refer to: variables, attributes, parameters, definitions."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _calls(tree):
    """(plain call names, method names) — every call the module can make."""
    plain, methods = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            plain.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            methods.add(node.func.attr)
    return plain, methods


def _function_calls():
    """Function name -> every call made anywhere inside it. The unit of a claim about 'the
    read path' or 'the writer' is a function, not the file."""
    found = {}
    for node in ast.walk(_tree()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                if isinstance(inner.func, ast.Name):
                    names.add(inner.func.id)
                elif isinstance(inner.func, ast.Attribute):
                    names.add("." + inner.func.attr)
        found[node.name] = names
    return found


def _column_names(columns):
    return {name for name, _, _, _ in columns}


def _declared_prose():
    """Every string the design DECLARES as a rule, a column's justification or its own
    identity — the only places another table's name may legitimately appear."""
    return (
        {purpose for _, _, _, purpose in SNAPSHOT_TABLE_COLUMNS + ITEM_TABLE_COLUMNS}
        | {name for name, _ in EXISTING_TABLE_REUSE}
        | {name for name, _ in NO_REUSE_RULES}
        | {rule for _, rule in EXISTING_TABLE_REUSE}
        | {rule for _, rule in NO_REUSE_RULES}
        | {APPEND_ONLY_RULE, RLS_RULE, EXISTING_PROJECT_RULE, model.SNAPSHOT_SCOPE_STATEMENT}
        | set(EVIDENCE_TABLES)
        | {EVIDENCE_KIND, SNAPSHOT_TABLE, ITEM_TABLE}
        | _column_names(SNAPSHOT_TABLE_COLUMNS)
        | _column_names(ITEM_TABLE_COLUMNS)
    )


def _paths(value, prefix=()):
    """Every key path in a stored value: the shape of what was written, not its text."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(prefix + (key,))
            found |= _paths(item, prefix + (key,))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _paths(item, prefix)
    return found


#: The tables this model must never read: the engineering evidence, the intake ledger and
#: the production tables whose rows J20 proved do not relate to a review package.
ENGINEERING_TABLES = (
    "steel_members", "connections", "connection_members", "bolt_groups",
    "connection_plates", "weld_details", "review_items", "analysis_runs", "drawings",
    "drawing_sets",
)

#: The token a second authorization or ownership model would need. A column or a payload
#: key named any of these is the forbidden thing; a sentence mentioning one is not.
OWNERSHIP_TOKENS = (
    "user_id", "role", "role_id", "reviewer", "reviewed_by", "membership",
    "organization_id", "project_members", "owner_id",
)

#: The two keys `app/config.py` reads at import time, and the placeholder values the
#: `env_bound_modules` fixture lends them — a URL that resolves nowhere and a key that
#: grants access to nothing. Never the repo's real configuration.
_ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
PLACEHOLDER_URL = "https://placeholder.supabase.co"

#: A fake-but-JWT-shaped service key, so the matcher module can be imported in-process
#: without live credentials (the same placeholder pattern the capture tests use).
PLACEHOLDER_SERVICE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)


#: Where every one of 7AK's contract fields comes from under this design. Slot 1: a column
#: of one of the two tables. Slot 2: the stored facts the replay derives it from. Exactly
#: one slot is set, and the union must cover the contract's fields exactly (asserted).
CONTRACT_FIELD_SOURCE = {
    "package_id": (f"{ITEM_TABLE}.review_package_id", None),
    "connection_id": (f"{ITEM_TABLE}.connection_id", None),
    "project_id": (f"{SNAPSHOT_TABLE}.project_id", None),
    "revision": (f"{SNAPSHOT_TABLE}.review_revision", None),
    "decision": (f"{ITEM_TABLE}.decision", None),
    "output_status": (f"{ITEM_TABLE}.output_status", None),
    "verification_status": (f"{ITEM_TABLE}.verification_status", None),
    "last_processed_revision": (f"{ITEM_TABLE}.last_processed_revision", None),
    "ai_member_references": (f"{ITEM_TABLE}.ai_readings.member_references", None),
    "ai_material": (f"{ITEM_TABLE}.ai_readings.material", None),
    "evidence": (f"{ITEM_TABLE}.evidence", None),
    "provenance": (f"{ITEM_TABLE}.provenance", None),
    "tasks": (f"{ITEM_TABLE}.tasks", None),
    "generated_files": (f"{ITEM_TABLE}.generated_files", None),
    "requires_action": (None, ("decision", "last_processed_revision")),
    "blockers": (None, ("blocker_codes", "tasks")),
    "warnings": (None, ("warning_codes", "tasks")),
    "ai_bolt_readings": (None, ("ai_readings.bolts",)),
    "ai_plate_readings": (None, ("ai_readings.plates",)),
    "ai_weld_readings": (None, ("ai_readings.welds",)),
    "ai_malformed_readings": (None, ("ai_readings.malformed_fields",)),
    "ai_unrecognised_readings": (None, ("ai_readings.unrecognised_fields",)),
    "ai_connection_type": (None, ("ai_readings.connection_type",)),
    "ai_confidence": (None, ("ai_readings.confidence",)),
    "available_actions": (None, ("requires_action",)),
    "summary": (None, ("decision", "output_status", "verification_status",
                       "requires_action", "blockers", "tasks")),
    # J66's two fields. They are the first contract fields whose source is a THIRD table:
    # `field_citations` is the citation rows themselves (read in the design's own order,
    # `_PROVENANCE_FIELD_ORDER` then the rest sorted, ordinal within each field), and
    # `field_standings` is computed from them and deliberately never stored, so it cannot
    # become a second fact that disagrees with the citations it is derived from. An empty
    # citation table is the honest reading: no citations, and seven UNCITED standings.
    "field_citations": (f"{CITATION_TABLE}.field_name", None),
    "field_standings": (None, ("field_citations",)),
}

#: The project contract's own fields (its `items` are the connection contracts above).
PROJECT_FIELD_SOURCE = {
    "project_id": (f"{SNAPSHOT_TABLE}.project_id", None),
    "revision": (f"{SNAPSHOT_TABLE}.review_revision", None),
    "project_status": (f"{SNAPSHOT_TABLE}.project_status", None),
    "items": (None, ("review_package_id order",)),
    "review_count": (None, ("decision",)),
    "confirmation_count": (None, ("decision",)),
    "auto_count": (None, ("decision",)),
    "verified_count": (None, ("verification_status",)),
    "blocked_count": (None, ("decision",)),
    "available_actions": (None, ("requires_action",)),
    "summary": (None, ("project_status", "revision", "decision", "requires_action")),
}


# =============================================================================
# 1. THE DESIGN IS TWO TABLES, AND EVERY COLUMN EARNS ITS PLACE
# =============================================================================
class TestTheDesignIsTwoTablesAndNoMore:
    def test_the_public_api_is_pinned(self):
        """A new public function is a design change and must be argued for here.

        J66's argument for `citation_from_row`: it is the third table's row decoder, and it
        is the exact peer of `snapshot_item_from_row`, which this list already carries for
        the item table. It reads one stored citation row the way the store read it and
        NOTHING else — no lookup, no derivation, no policy — because the address it decodes
        is only ever meaningful against the evidence tables it names, and this module may
        not read those. Leaving it module-private would mean the store reimplemented the
        column-by-column decode that the design's own column list defines, which is exactly
        the drift this design exists to prevent."""
        public = tuple(
            name for name, value in vars(model).items()
            if not name.startswith("_") and inspect.isfunction(value)
            and value.__module__ == model.__name__
        )
        assert sorted(public) == [
            "build_review_snapshot", "citation_from_row", "connection_contract_from_item",
            "evidence_identity", "evidence_identity_matches", "latest_snapshot",
            "project_contract_from_snapshot", "snapshot_from_rows", "snapshot_is_stale",
            "snapshot_item_from_row",
        ]

    def test_every_j20_proposed_table_is_decided(self):
        proposed = {
            "connection_review_items", "connection_review_readings",
            "connection_review_findings", "connection_review_provenance",
            "connection_review_tasks", "connection_review_outputs",
        }
        decisions = dict(EXISTING_TABLE_REUSE)
        assert proposed <= set(decisions), sorted(proposed - set(decisions))
        assert SNAPSHOT_TABLE in decisions, "the added header must be declared too"

    def test_every_omitted_table_names_the_column_it_folded_into(self):
        """An OMITTED decision must point at a real column — the design may not delete a
        table without saying where its data went."""
        columns = {name for name, _, _, _ in ITEM_TABLE_COLUMNS} | {
            name for name, _, _, _ in SNAPSHOT_TABLE_COLUMNS
        }
        for name, decision in EXISTING_TABLE_REUSE:
            if not decision.startswith("OMITTED"):
                continue
            folded = [column for column in columns if f"`{column}`" in decision
                      or f".{column}" in decision]
            assert folded, f"{name}: no column named in {decision!r}"

    def test_the_two_tables_and_their_keys_are_declared(self):
        assert model.SNAPSHOT_PRIMARY_KEY == ("project_id", "review_revision")
        assert model.ITEM_PRIMARY_KEY == (
            "project_id", "review_revision", "review_package_id"
        )
        assert model.ITEM_FOREIGN_KEY == (
            ("project_id", "review_revision"), model.SNAPSHOT_PRIMARY_KEY
        )
        assert model.DECLARED_INDEXES == ()

    def test_every_column_is_typed_nullable_and_justified(self):
        for name, sql_type, nullable, purpose in SNAPSHOT_TABLE_COLUMNS + ITEM_TABLE_COLUMNS:
            assert name and isinstance(name, str)
            assert sql_type in {"uuid", "integer", "text", "json", "timestamptz"}, sql_type
            assert isinstance(nullable, bool)
            assert isinstance(purpose, str) and len(purpose) > 40, name

    def test_no_payload_column_is_jsonb_or_sql_null(self):
        """The fidelity rule and the state rule, as one assertion: a payload column is
        `json` (text-preserving) and NOT NULL (absence lives inside the payload)."""
        nullable = {
            name: nullable for name, sql_type, nullable, _ in
            SNAPSHOT_TABLE_COLUMNS + ITEM_TABLE_COLUMNS if sql_type == "json"
        }
        assert set(nullable) == set(PAYLOAD_COLUMNS)
        assert nullable, "the design must have payload columns"
        assert all(value is False for value in nullable.values())

    def test_only_the_never_attempted_columns_are_nullable(self):
        nullable = {
            name for name, _, nullable, _ in ITEM_TABLE_COLUMNS if nullable
        }
        assert nullable == {
            "connection_id", "output_status", "verification_status",
            "last_processed_revision",
        }

    def test_the_check_vocabulary_is_the_owning_modules_own(self):
        assert ITEM_CHECK_VOCABULARY["decision"] == AUTOMATION_DECISIONS
        assert ITEM_CHECK_VOCABULARY["output_status"] == OUTPUT_STATUSES
        assert ITEM_CHECK_VOCABULARY["verification_status"] == VERIFICATION_STATUSES

    def test_the_rules_are_stated(self):
        assert "no UPDATE" in APPEND_ONLY_RULE and "no DELETE" in APPEND_ONLY_RULE
        assert "service-role" in RLS_RULE and "projects.user_id" in RLS_RULE
        assert "NO persisted connection-review state" in EXISTING_PROJECT_RULE
        assert "review_items" in EXISTING_PROJECT_RULE
        assert NO_REUSE_RULES, "the tables that must NOT be reused are declared"


# =============================================================================
# 2. EXACT COVERAGE OF 7AK'S OWN FIELDS
# =============================================================================
class TestEveryContractFieldIsCovered:
    def test_the_connection_sources_cover_exactly_the_contracts_fields(self):
        assert sorted(CONTRACT_FIELD_SOURCE) == sorted(_all_fields(ConnectionReviewContract))

    def test_the_project_sources_cover_exactly_the_projects_fields(self):
        assert sorted(PROJECT_FIELD_SOURCE) == sorted(_all_fields(ProjectReviewContract))

    def test_every_field_is_stored_or_derived_and_never_both(self):
        for field, (stored, derived) in CONTRACT_FIELD_SOURCE.items():
            assert (stored is None) != (derived is None), field
        for field, (stored, derived) in PROJECT_FIELD_SOURCE.items():
            assert (stored is None) != (derived is None), field

    def test_every_stored_field_names_a_real_column(self):
        # J66 added the citation table, so its own declared columns are a legitimate source
        # too — declared here, from the module's own column list, never a free-form name.
        columns = {name for name, _, _, _ in SNAPSHOT_TABLE_COLUMNS + ITEM_TABLE_COLUMNS} | {
            name for name, _, _, _ in CITATION_TABLE_COLUMNS
        }
        for field, (stored, _) in list(CONTRACT_FIELD_SOURCE.items()) + list(
            PROJECT_FIELD_SOURCE.items()
        ):
            if stored is None:
                continue
            column = stored.split(".")[1]
            assert column in columns, (field, stored)

    def test_every_derivation_names_stored_facts(self):
        """A derived field is derived from stored facts or from another field that is —
        never from nothing, and never from an engineering table."""
        derivable = set(CONTRACT_FIELD_SOURCE) | set(PROJECT_FIELD_SOURCE)
        columns = {name for name, _, _, _ in ITEM_TABLE_COLUMNS}
        for field, (_, derived) in list(CONTRACT_FIELD_SOURCE.items()) + list(
            PROJECT_FIELD_SOURCE.items()
        ):
            if derived is None:
                continue
            for fact in derived:
                root = fact[:-len(" order")] if fact.endswith(" order") else fact
                column = root.split(".")[0]
                assert column in derivable or column in columns, (field, fact)

    def test_the_derived_set_is_only_presentation_and_arithmetic(self):
        """The fields that are NOT stored are exactly the display strings, the sums — the
        ones 7AK itself computes from the same facts — and, since J66, the field standing,
        which is computed from the citation rows for the same reason: a stored copy could
        disagree with the citations it is supposed to summarise."""
        derived = sorted(
            field for field, (stored, _) in CONTRACT_FIELD_SOURCE.items() if stored is None
        )
        assert derived == [
            "ai_bolt_readings", "ai_confidence", "ai_connection_type",
            "ai_malformed_readings", "ai_plate_readings", "ai_unrecognised_readings",
            "ai_weld_readings", "available_actions", "blockers", "field_standings",
            "requires_action", "summary", "warnings",
        ]


# =============================================================================
# 3. THE REAL CONTRACT ROUND-TRIPS BYTE FOR BYTE
# =============================================================================
@needs_real_capture
class TestTheRealContractRoundTripsByteForByte:
    def test_the_revision_zero_project_is_the_real_one(self, workflow, revision_zero):
        assert [connection.package_id for connection in workflow.connections] == [
            "RP-0001", "RP-0002", "RP-0003",
        ]
        assert revision_zero.review_revision == workflow.revision == 0
        assert [item.review_package_id for item in revision_zero.items] == [
            "RP-0001", "RP-0002", "RP-0003",
        ]
        assert revision_zero.project_status == workflow.project_decision

    def test_revision_zero_rebuilds_identically(self, workflow, revision_zero):
        expected = build_project_review_contract(workflow)
        assert project_contract_from_snapshot(_round_trip(revision_zero)) == expected

    def test_revision_one_rebuilds_identically(self, resolved_workflow, revision_one):
        expected = build_project_review_contract(resolved_workflow)
        replayed = project_contract_from_snapshot(_round_trip(revision_one))
        assert replayed == expected

    def test_the_resolved_connection_is_the_one_that_was_resolved(
        self, resolved_workflow, revision_one
    ):
        expected = build_project_review_contract(resolved_workflow)
        replayed = project_contract_from_snapshot(_round_trip(revision_one))
        resolved = next(
            item for item in replayed.items if item.package_id == SELECTED_PACKAGE_ID
        )
        original = next(
            item for item in expected.items if item.package_id == SELECTED_PACKAGE_ID
        )
        assert resolved.connection_id == "CONN-ARKLES-001"
        assert resolved.decision == original.decision
        assert resolved.output_status == original.output_status
        assert resolved.verification_status == original.verification_status
        assert resolved.requires_action is False
        assert resolved.last_processed_revision == 1

    def test_the_ai_readings_are_not_reprs_in_storage(self, revision_zero):
        """The stored reading is the AI's data, not the string 7AK renders from it: the
        snapshot holds no `repr(` output, and the replay produces it."""
        stored = revision_zero.items[0].ai_readings
        assert stored["bolts"], "the real capture has a bolt reading for RP-0001"
        for bolt in stored["bolts"]:
            assert set(bolt) == {"quantity", "size", "grade", "extra"}
        flat = json.dumps(revision_zero.to_rows(), ensure_ascii=False)
        assert "AIExtractedBolt" not in flat
        assert "ReviewTaskInfo" not in flat
        assert "ReviewBlockerInfo" not in flat

    def test_the_human_answer_is_stored_structured_not_as_a_string(self, revision_one):
        item = next(
            item for item in revision_one.items if item.review_package_id == SELECTED_PACKAGE_ID
        )
        plate_task = next(row for row in item.tasks if row["answer_type"] == "PLATE_VALUE")
        assert isinstance(plate_task["resolution"]["answer"], dict)
        assert plate_task["resolution"]["answer"]["type"] == "end_plate"

    def test_the_replay_is_deterministic_and_the_snapshot_is_immutable(
        self, workflow, revision_zero
    ):
        first = project_contract_from_snapshot(_round_trip(revision_zero))
        second = project_contract_from_snapshot(_round_trip(revision_zero))
        assert first == second
        assert build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE
        ) == revision_zero

    def test_a_caller_mutation_cannot_change_a_recorded_snapshot(self, workflow,
                                                                tmp_path_factory):
        """The snapshot was computed from live objects the caller still holds. Mutating one
        afterwards must leave the recorded rows byte-identical — and the mutation must be
        REAL: a fresh snapshot of the mutated workflow records the changed value, so the
        recorded one is provably a copy, not a reference."""
        resolutions = _human_resolutions(workflow, SELECTED_PACKAGE_ID)
        resolved = resolve_project_connection(
            workflow, package_id=SELECTED_PACKAGE_ID, resolutions=resolutions,
            output_dir=str(tmp_path_factory.mktemp("j21-mutation")),
        )
        snapshot = build_review_snapshot(
            resolved, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
            previous_revision=0,
        )
        before_rows = json.dumps(snapshot.to_rows(), ensure_ascii=False)
        before_contract = project_contract_from_snapshot(_round_trip(snapshot))

        answered = _live_answer(resolved, SELECTED_PACKAGE_ID, "PLATE_VALUE")
        answered["thickness_mm"] = 999

        assert json.dumps(snapshot.to_rows(), ensure_ascii=False) == before_rows
        assert project_contract_from_snapshot(_round_trip(snapshot)) == before_contract
        mutated = build_review_snapshot(
            resolved, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
            previous_revision=0,
        )
        assert json.dumps(mutated.to_rows(), ensure_ascii=False) != before_rows, (
            "the mutation must be visible to a NEW snapshot, or this proof proves nothing"
        )


def _live_answer(workflow, package_id, answer_type):
    """The answer object the given workflow's own task currently holds — the live one."""
    group = next(
        group for group in workflow.exception_package.connection_tasks
        if group.review_package_id == package_id
    )
    task = next(task for task in group.tasks if task.answer_type == answer_type)
    return task.resolution.answer


# =============================================================================
# 4. THE COLUMN TYPE: `json`, NOT `jsonb` — PROVEN ON REAL DATA
# =============================================================================
@needs_real_capture
class TestJsonbWouldBreakTheRealContract:
    def test_the_real_human_answers_are_objects_and_a_tuple(self, resolved_workflow):
        """The two mechanisms the storage must survive, on the real answers: a dict (whose
        key order the contract's repr exposes) and a nested tuple (whose repr differs from
        a list's)."""
        group = next(
            group for group in resolved_workflow.exception_package.connection_tasks
            if group.review_package_id == SELECTED_PACKAGE_ID
        )
        answers = {
            task.answer_type: task.resolution.answer
            for task in group.tasks if task.resolution is not None
        }
        assert isinstance(answers["PLATE_VALUE"], dict)
        assert isinstance(answers["MEMBER_POSITION_ATTACHMENTS"], tuple)
        assert any(isinstance(entry, tuple) for entry in answers["MEMBER_POSITION_ATTACHMENTS"])

    def test_a_jsonb_column_would_re_render_a_human_answer(self, resolved_workflow, revision_one):
        expected = build_project_review_contract(resolved_workflow)
        replayed = project_contract_from_snapshot(_jsonb_round_trip(revision_one))
        assert replayed != expected, (
            "if a jsonb round trip reproduced the contract, the `json` column type would be "
            "unjustified and this proof would be worthless"
        )
        before = _values(expected, "resolution_value")
        after = _values(replayed, "resolution_value")
        diverged = [(a, b) for a, b in zip(before, after) if a != b]
        assert diverged, "the divergence must be a recorded human answer, not incidental"
        first_expected, first_actual = diverged[0]
        assert first_expected.startswith("{'type': 'end_plate'")
        assert first_actual.startswith("{'depth_mm'")
        assert set(first_expected) == set(first_actual), "same values, different key order"

    def test_a_tuple_answer_would_become_a_list_without_the_tag(self, resolved_workflow):
        """The tuple tag's whole purpose, on the real answer: `repr(tuple) != repr(list)`."""
        group = next(
            group for group in resolved_workflow.exception_package.connection_tasks
            if group.review_package_id == SELECTED_PACKAGE_ID
        )
        answer = next(
            task.resolution.answer for task in group.tasks
            if task.answer_type == "MEMBER_POSITION_ATTACHMENTS"
        )
        assert repr(answer) != repr(json.loads(json.dumps(list(answer))))
        stored = next(
            row for row in _snapshot_item_rows(resolved_workflow, previous_revision=0)
            if row["answer_type"] == "MEMBER_POSITION_ATTACHMENTS"
        )
        assert TUPLE_TAG in json.dumps(stored), "the stored form must carry the tuple tag"

    def test_the_encoding_refuses_a_smuggled_tag(self):
        with pytest.raises(ValueError):
            model._payload({TUPLE_TAG: ["a"]}, where="test")

    def test_the_encoding_refuses_a_value_with_no_faithful_form(self):
        with pytest.raises(ValueError):
            model._payload({"thickness_mm": object()}, where="test")


def _values(contract, field):
    return [
        getattr(task, field)
        for item in contract.items for task in item.tasks
    ]


def _snapshot_item_rows(workflow, *, previous_revision):
    snapshot = build_review_snapshot(
        workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
        previous_revision=previous_revision,
    )
    item = next(
        item for item in snapshot.items if item.review_package_id == SELECTED_PACKAGE_ID
    )
    return list(item.tasks)


# =============================================================================
# 5. ABSENT, NULL, NEVER-ATTEMPTED: THE STATE DISTINCTIONS
# =============================================================================
@needs_real_capture
class TestAbsenceIsNeverCollapsed:
    def test_the_ais_silence_is_a_present_null_not_a_missing_key(self, revision_zero):
        """The real capture has a bolt whose quantity and grade are unstated. The stored
        reading keeps the KEY with a JSON null — so 'the AI said nothing' is a value, not
        an absence, and the replay's repr says `None` exactly as 7AK's does."""
        bolts = [
            bolt for item in revision_zero.items for bolt in item.ai_readings["bolts"]
        ]
        silent = [bolt for bolt in bolts if bolt["quantity"] is None and bolt["size"]]
        assert silent, "expected the real capture's silent-quantity bolt"
        for bolt in silent:
            assert "quantity" in bolt and "grade" in bolt
            assert bolt["quantity"] is None and bolt["grade"] is None
        replayed = project_contract_from_snapshot(_round_trip(revision_zero))
        assert any("quantity=None" in reading for reading in
                   replayed.items[0].ai_bolt_readings)

    def test_never_attempted_is_a_null_column_not_a_status(self, workflow, revision_zero,
                                                           resolved_workflow, revision_one):
        """The same connection, before and after it was processed: NULL before, a real
        status after. 'Not attempted' can never be read as a status."""
        before = next(item for item in revision_zero.items
                      if item.review_package_id == SELECTED_PACKAGE_ID)
        after = next(item for item in revision_one.items
                     if item.review_package_id == SELECTED_PACKAGE_ID)
        assert before.output_status is None and before.verification_status is None
        assert before.last_processed_revision is None
        assert after.output_status in OUTPUT_STATUSES
        assert after.verification_status in VERIFICATION_STATUSES
        assert after.last_processed_revision == 1
        assert before.connection_id is None
        assert after.connection_id == "CONN-ARKLES-001"
        assert dict(ITEM_TABLE_COLUMNS_BY_NAME)["output_status"][1] is True
        assert dict(ITEM_TABLE_COLUMNS_BY_NAME)["output_status"][0] == "text"

    def test_a_distinction_the_review_layer_already_collapsed_is_not_claimed(self, revision_zero):
        """7W's parse collapses a raw missing key and a raw null into `None` before 7AK
        exists, and the snapshot stores 7W's fields exactly. So the snapshot neither invents
        a distinction nor claims one it cannot have — and every 7W field it does NOT store is
        named here, so an omission can never be mistaken for preservation."""
        item = revision_zero.items[0]
        readings = item.ai_readings
        assert set(readings) == {
            "member_references", "bolts", "plates", "welds", "connection_type",
            "confidence", "material", "malformed_fields", "unrecognised_fields",
        }
        for group, reading_type in (
            ("bolts", AIExtractedBolt), ("plates", AIExtractedPlate), ("welds", AIExtractedWeld)
        ):
            for entry in readings[group]:
                assert set(entry) == set(_all_fields(reading_type)), (group, entry.keys())
        assert readings["bolts"], "the real capture reads bolts for RP-0001"

        carried = set(_all_fields(AIExtractedConnection))
        stored = set(readings) | {"member_references", "connection_type"}
        # The two renames, stated as renames: the stored key is the 7AK contract's name.
        assert "connected_member_references" in carried and "generic_connection_type" in carried
        # Everything else 7W carries, this payload does not — and each omission is accounted
        # for: the five source-identity fields are stored in this item's `evidence`; the
        # project is the snapshot header; `persisted_review_status` is 7W's INTAKE status,
        # which 7AK never exposes and the recorded review decision supersedes.
        assert carried - stored == {
            "project_id", "source_drawing_id", "source_page", "drawing_number",
            "persisted_review_status", "detail_reference", "grid_reference",
            "connected_member_references", "generic_connection_type",
        }
        assert set(item.evidence) == {
            "source_drawing_id", "drawing_number", "source_page",
            "detail_reference", "grid_reference",
        }
        assert "persisted_review_status" not in json.dumps(revision_zero.to_rows())

    def test_an_empty_reading_is_an_empty_array_never_a_null_column(self, revision_zero):
        readings = revision_zero.items[0].ai_readings
        for key in ("bolts", "plates", "welds"):
            assert isinstance(readings[key], list)
        assert isinstance(readings["malformed_fields"], dict)
        for name, sql_type, _, _ in ITEM_TABLE_COLUMNS:
            if sql_type == "json":
                assert name in model.PAYLOAD_COLUMNS


ITEM_TABLE_COLUMNS_BY_NAME = {name: (sql_type, nullable) for name, sql_type, nullable, _ in
                              ITEM_TABLE_COLUMNS}


# =============================================================================
# 6. HUMAN DECISIONS, BLOCKERS, TASKS AND PROVENANCE
# =============================================================================
@needs_real_capture
class TestHumanDecisionsSurvive:
    def test_every_resolved_task_carries_its_recorded_answer(self, resolved_workflow,
                                                             revision_one):
        item = next(item for item in revision_one.items
                    if item.review_package_id == SELECTED_PACKAGE_ID)
        assert item.tasks, "the real candidate has tasks"
        resolved = [row for row in item.tasks if row["resolution"] is not None]
        assert resolved, "the selected candidate's tasks were answered"
        for row in resolved:
            assert row["status"] == STATUS_RESOLVED
            resolution = row["resolution"]
            assert resolution["evidence"].startswith("HUMAN-SUPPLIED TEST DATA")
            assert resolution["task_id"] == row["task_id"]
            assert resolution["answer_type"] == row["answer_type"]

    def test_an_unresolved_candidate_stores_no_resolution(self, revision_one):
        other = next(item for item in revision_one.items
                     if item.review_package_id == "RP-0002")
        assert other.tasks
        assert all(row["resolution"] is None for row in other.tasks)
        assert all(row["status"] != STATUS_RESOLVED for row in other.tasks)

    def test_the_replayed_resolutions_equal_the_real_contracts(self, resolved_workflow,
                                                              revision_one):
        expected = build_project_review_contract(resolved_workflow)
        replayed = project_contract_from_snapshot(_round_trip(revision_one))
        for want, got in zip(expected.items, replayed.items):
            assert [(t.task_id, t.resolved, t.resolution_value, t.resolution_evidence)
                    for t in want.tasks] == \
                   [(t.task_id, t.resolved, t.resolution_value, t.resolution_evidence)
                    for t in got.tasks], want.package_id

    def test_only_the_selected_candidates_answers_are_recorded(self, revision_one):
        """The other two candidates were never resolved; their rows carry no answer."""
        selected = next(item for item in revision_one.items
                        if item.review_package_id == SELECTED_PACKAGE_ID)
        others = [item for item in revision_one.items
                  if item.review_package_id != SELECTED_PACKAGE_ID]
        assert any(row["resolution"] for row in selected.tasks)
        for item in others:
            assert not any(row["resolution"] for row in item.tasks), item.review_package_id


@needs_real_capture
class TestBlockersAndWarningsAreStoredAsCodes:
    def test_every_blocker_code_the_contract_reads_is_stored(self, workflow, revision_zero):
        expected = build_project_review_contract(workflow)
        stored = {
            item.review_package_id: set(item.blocker_codes) for item in revision_zero.items
        }
        for item in expected.items:
            assert stored[item.package_id] == {info.code for info in item.blockers}
            assert stored[item.package_id] | {
                code for code in item_warning_codes(revision_zero, item.package_id)
            } == {info.code for info in item.blockers} | {info.code for info in item.warnings}

    def test_the_presentation_is_not_stored(self, revision_zero):
        """Titles, messages, severities, fields and task hints are 7AK's presentation —
        re-derived, never duplicated into storage."""
        item = revision_zero.items[0]
        serialised = json.dumps(list(item.blocker_codes) + list(item.warning_codes))
        for leaked in ("title", "message", "severity", "task_type", "blocking"):
            assert leaked not in serialised

    def test_the_replayed_blockers_are_identical(self, workflow, revision_zero):
        expected = build_project_review_contract(workflow)
        replayed = project_contract_from_snapshot(_round_trip(revision_zero))
        for want, got in zip(expected.items, replayed.items):
            assert want.blockers == got.blockers
            assert want.warnings == got.warnings
            assert _codes(want) == _codes(got)

    def test_the_task_rule_is_derived_from_the_stored_tasks(self, workflow, revision_zero):
        """A blocker's `task_type` comes from the connection's own tasks: it is re-derived
        from stored task blocker codes, never stored beside the blocker."""
        replayed = project_contract_from_snapshot(_round_trip(revision_zero))
        item = next(item for item in replayed.items if item.blockers)
        assert any(blocker.task_type for blocker in item.blockers)


def item_warning_codes(snapshot, package_id):
    return next(item.warning_codes for item in snapshot.items
                if item.review_package_id == package_id)


@needs_real_capture
class TestProvenanceIsRecordedNeverRegenerated:
    def test_the_unreviewed_candidate_records_no_provenance(self, revision_zero):
        """The original package carries no provenance for an unprocessed candidate, so the
        snapshot stores an EMPTY mapping — it does not invent AI_EXTRACTED labels."""
        assert all(item.provenance == {} for item in revision_zero.items)

    def test_the_resolved_connection_records_its_human_labels(self, resolved_workflow,
                                                             revision_one):
        expected = build_project_review_contract(resolved_workflow)
        item = next(item for item in revision_one.items
                    if item.review_package_id == SELECTED_PACKAGE_ID)
        want = next(item for item in expected.items
                    if item.package_id == SELECTED_PACKAGE_ID)
        assert item.provenance == {info.field: info.provenance for info in want.provenance}
        assert set(item.provenance.values()) == {"HUMAN_SUPPLEMENTED"}
        assert item.provenance["plate"] == "HUMAN_SUPPLEMENTED"

    def test_the_label_is_read_not_recomputed(self, resolved_workflow, revision_one):
        """Changing the recorded label changes the replayed contract: the label is READ
        from storage, so nothing downstream can re-derive it from the values."""
        header, rows = revision_one.to_rows()
        edited = []
        for row in copy.deepcopy(list(rows)):
            if row["review_package_id"] == SELECTED_PACKAGE_ID:
                row["provenance"]["plate"] = "AI_EXTRACTED"
            edited.append(row)
        snapshot = snapshot_from_rows(_wire(header), _wire(edited))
        item = next(item for item in snapshot.items
                    if item.review_package_id == SELECTED_PACKAGE_ID)
        assert item.provenance["plate"] == "AI_EXTRACTED"
        replayed = project_contract_from_snapshot(snapshot)
        plate = next(entry for entry in
                     next(i for i in replayed.items
                          if i.package_id == SELECTED_PACKAGE_ID).provenance
                     if entry.field == "plate")
        assert plate.provenance == "AI_EXTRACTED", (
            "a provenance label must come from storage; re-deriving it would silently "
            "re-label a human-reviewed value"
        )

    def test_the_report_is_never_rebuilt_at_read_time(self):
        """The WRITER reads the genuine 7W report once, to record its provenance. Every READ
        path — the item rebuild, the project rebuild, the task/state rebuilds — calls no
        report builder, no package builder and no gate, and never calls 7AK's own contract
        builder: equality with 7AK's contract is therefore an independent reconstruction, not
        a reconstruction that delegated to the thing it claims to reproduce."""
        calls = _function_calls()
        assert "build_review_report" in calls["build_review_snapshot"], (
            "the writer reads the report it snapshots"
        )
        read_path = (
            "connection_contract_from_item", "project_contract_from_snapshot",
            "_state_from_item", "_task_from_row", "_item_summary", "_reading_row",
            "snapshot_from_rows", "snapshot_item_from_row", "latest_snapshot",
            "snapshot_is_stale", "evidence_identity", "evidence_identity_matches",
        )
        forbidden = {
            "build_review_report", "build_connection_review_contract",
            "build_project_review_contract", "build_review_snapshot",
            "evaluate_reviewed_connection_for_automation", "evaluate_project_for_automation",
            "build_exception_resolution_package", "apply_human_resolution",
            "review_package_for_connection", "build_bom",
        }
        for name in read_path:
            assert not (calls[name] & forbidden), (name, calls[name] & forbidden)

    def test_the_replay_reuses_only_the_presentation_helpers(self):
        """What the read path DOES reuse from 7AK (and from 7AJ for the counts): the pure
        display helpers. That is the whole shared surface between the two contracts."""
        calls = _function_calls()
        required = {"_display", "_finding_info", "_task_info", "_provenance_infos"}
        assert required <= calls["connection_contract_from_item"]
        assert "_counts" in calls["project_contract_from_snapshot"]
        assert required | {"_counts"} <= (
            calls["connection_contract_from_item"] | calls["project_contract_from_snapshot"]
        )


# =============================================================================
# 7. GENERATED OUTPUT IDENTITY
# =============================================================================
@needs_real_capture
class TestGeneratedOutputIdentity:
    def test_the_recorded_artifact_is_the_real_one(self, resolved_workflow, revision_one):
        expected = build_project_review_contract(resolved_workflow)
        item = next(item for item in revision_one.items
                    if item.review_package_id == SELECTED_PACKAGE_ID)
        want = next(item for item in expected.items
                    if item.package_id == SELECTED_PACKAGE_ID)
        assert item.generated_files == want.generated_files
        assert item.generated_files, "the resolved connection generated an artifact"
        assert Path(item.generated_files[0]).name == "CONN-ARKLES-001-fabrication.pdf"

    def test_the_artifact_is_referenced_never_copied(self, resolved_workflow, revision_one):
        """The model records the path verbatim and does not read the file: the artifact
        stays the single copy the generation path produced."""
        source = MODULE_PATH.read_text()
        for forbidden in ("pypdf", "PdfReader", "open(", "read_bytes", "hashlib.md5",
                          "sha256(path"):
            assert forbidden not in source, forbidden
        item = next(item for item in revision_one.items
                    if item.review_package_id == SELECTED_PACKAGE_ID)
        assert Path(item.generated_files[0]).exists(), (
            "the recorded path is a real artifact the model never had to read"
        )

    def test_an_unprocessed_connection_records_no_artifact(self, revision_zero):
        assert all(item.generated_files == () for item in revision_zero.items)


# =============================================================================
# 8. VERSIONING: A SECOND REVIEW IS A NEW ROW
# =============================================================================
class TestVersionIsolation:
    def test_the_revision_recorded_is_the_workflows_own(self, workflow, revision_zero,
                                                       resolved_workflow, revision_one):
        assert revision_zero.review_revision == workflow.revision == 0
        assert revision_one.review_revision == resolved_workflow.revision == 1

    def test_the_two_revisions_are_two_distinct_keys(self, revision_zero, revision_one):
        header_zero, rows_zero = revision_zero.to_rows()
        header_one, rows_one = revision_one.to_rows()
        assert (header_zero["project_id"], header_zero["review_revision"]) == (
            PROJECT_ID, 0)
        assert (header_one["project_id"], header_one["review_revision"]) == (
            PROJECT_ID, 1)
        assert {(row["project_id"], row["review_revision"], row["review_package_id"])
                for row in rows_zero}.isdisjoint(
            {(row["project_id"], row["review_revision"], row["review_package_id"])
             for row in rows_one})

    def test_recording_a_later_revision_leaves_the_earlier_rows_untouched(
        self, resolved_workflow, revision_zero, revision_one
    ):
        """The append-only rule, as a fact about the model's output: the earlier revision's
        rows are byte-identical before and after the later one exists."""
        before = json.dumps(revision_zero.to_rows(), ensure_ascii=False)
        build_review_snapshot(
            resolved_workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
            previous_revision=0,
        )
        assert json.dumps(revision_zero.to_rows(), ensure_ascii=False) == before
        assert revision_one.review_revision == 1
        assert revision_zero.review_revision == 0
        assert APPEND_ONLY_RULE

    def test_the_current_review_state_is_the_highest_revision(self, revision_zero,
                                                             revision_one):
        assert latest_snapshot([revision_zero, revision_one]) is revision_one
        assert latest_snapshot([revision_one, revision_zero]) is revision_one
        assert latest_snapshot([revision_zero]) is revision_zero

    def test_the_chain_refuses_a_gap(self, workflow):
        for previous, expected_code in ((0, SNAPSHOT_REFUSED_REVISION_GAP),
                                        (5, SNAPSHOT_REFUSED_REVISION_GAP)):
            with pytest.raises(SnapshotRefused) as refused:
                build_review_snapshot(
                    workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
                    previous_revision=previous,
                )
            assert refused.value.code == expected_code

    def test_a_missing_revision_zero_cannot_be_skipped(self, resolved_workflow):
        with pytest.raises(SnapshotRefused) as refused:
            build_review_snapshot(
                resolved_workflow, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE,
                previous_revision=None,
            )
        assert refused.value.code == SNAPSHOT_REFUSED_REVISION_GAP

    def test_the_model_never_renumbers_a_revision(self):
        source = MODULE_PATH.read_text()
        assert "review_revision=max(" not in source
        assert "review_revision += " not in source
        assert "previous_revision + 1" in source, (
            "the only arithmetic on a revision is the contiguity CHECK"
        )


# =============================================================================
# 9. STALENESS: AN EVIDENCE CHANGE, AND NOTHING ELSE
# =============================================================================
class TestStaleStateBehaviour:
    def _rows(self, *rows):
        return {table: list(rows) if table == "connections" else []
                for table in EVIDENCE_TABLES}

    def test_the_identity_is_a_detached_object_with_a_digest(self):
        identity = evidence_identity({"steel_members": [], "connections": []})
        assert identity["evidence_kind"] == EVIDENCE_KIND
        assert identity["evidence_tables"] == list(EVIDENCE_TABLES)
        assert len(identity["evidence_digest"]) == 64
        assert set(identity) == {"evidence_kind", "evidence_tables", "evidence_digest"}

    def test_the_fingerprint_is_order_independent_and_duplication_sensitive(self):
        one = {"id": "c1", "review_status": "review", "source_page": 7}
        two = {"id": "c2", "review_status": "extracted", "source_page": 8}
        assert evidence_identity(self._rows(one, two)) == evidence_identity(self._rows(two, one))
        assert evidence_identity(self._rows(one, two)) != evidence_identity(self._rows(one))
        assert evidence_identity(self._rows(one, one)) != evidence_identity(self._rows(one))
        assert evidence_identity(self._rows(one)) != evidence_identity(self._rows({**one,
                                                                                  "review_status": "extracted"}))

    def test_the_fingerprint_is_not_the_matchers_reference_data_identity(self, env_bound_modules):
        """Two identities that must never be confusable: J6's reference-data digest is over
        `steel_sections` with the matcher's own domain separator; this fingerprint is over
        the project's evidence tables. Different kind, different domain, different rows — so
        a review fingerprint can never be read as a reference-data identity."""
        matcher = env_bound_modules.matcher
        reference_table = matcher._REFERENCE_TABLE
        reference_data_digest = matcher.reference_data_digest

        rows = [{"id": "c1", "review_status": "review"}]
        fingerprint = evidence_identity({"steel_members": [], "connections": rows})
        assert fingerprint["evidence_kind"] != reference_table
        assert reference_table not in EVIDENCE_TABLES
        assert fingerprint["evidence_digest"] != reference_data_digest(rows)
        assert fingerprint["evidence_digest"] != reference_data_digest(
            [{"section": "FLAT 100x10"}]
        )
        assert "reference_data_digest" not in MODULE_PATH.read_text()

    def test_the_fingerprint_refuses_an_unknown_or_missing_table(self):
        with pytest.raises(ValueError):
            evidence_identity({"steel_members": [], "connections": [], "bolt_groups": []})
        with pytest.raises(ValueError):
            evidence_identity({"steel_members": []})

    def test_an_unchanged_evidence_set_is_not_stale(self, workflow, revision_zero):
        rows = self._rows({"id": "c1", "review_status": "review"})
        snapshot = build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=rows
        )
        assert snapshot_is_stale(snapshot, current_evidence_identity=evidence_identity(rows)) is False

    def test_a_changed_value_makes_the_snapshot_stale(self, workflow):
        rows = self._rows({"id": "c1", "review_status": "review"})
        snapshot = build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=rows
        )
        changed = self._rows({"id": "c1", "review_status": "extracted"})
        assert snapshot_is_stale(snapshot, current_evidence_identity=evidence_identity(changed))

    def test_a_new_row_from_a_retry_or_a_continuation_makes_it_stale(self, workflow):
        rows = self._rows({"id": "c1", "review_status": "review"})
        snapshot = build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=rows
        )
        added = self._rows({"id": "c1", "review_status": "review"},
                           {"id": "c2", "review_status": "review"})
        assert snapshot_is_stale(snapshot, current_evidence_identity=evidence_identity(added))

    def test_a_retry_that_recorded_no_evidence_does_not_make_it_stale(self, workflow):
        """A J17 retry whose page still could not be parsed writes one `analysis_runs` row
        and NO evidence. The run history changes; the evidence does not — and the snapshot
        must stay current, because no review fact changed."""
        rows = self._rows({"id": "c1", "review_status": "review"})
        first = build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=rows,
            evidence_run_ids=("run-1",),
        )
        second = build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=rows,
            evidence_run_ids=("run-1", "run-2"),
        )
        assert first.evidence_run_ids != second.evidence_run_ids
        assert first.evidence_identity == second.evidence_identity
        assert snapshot_is_stale(first, current_evidence_identity=second.evidence_identity) is False

    def test_a_stale_snapshot_is_still_readable(self, workflow):
        rows = self._rows({"id": "c1", "review_status": "review"})
        snapshot = build_review_snapshot(
            workflow, project_id=PROJECT_ID, evidence_rows=rows
        )
        stale = snapshot_is_stale(
            snapshot, current_evidence_identity=evidence_identity(self._rows())
        )
        assert stale is True
        assert project_contract_from_snapshot(snapshot).items, (
            "the stale snapshot is preserved and readable: nothing rewrites it"
        )

    def test_an_extraction_rerun_alone_creates_no_new_review_state(self, workflow):
        """Extraction running again adds evidence and makes the recorded review STALE — and
        nothing more. No new review revision appears, because a revision is what a REVIEW
        produced; the rerun changed the evidence, not the review."""
        rows = self._rows({"id": "c1", "review_status": "review"})
        snapshot = build_review_snapshot(workflow, project_id=PROJECT_ID, evidence_rows=rows)
        rerun = self._rows({"id": "c1", "review_status": "review"},
                           {"id": "c2", "review_status": "review"})
        assert snapshot_is_stale(snapshot, current_evidence_identity=evidence_identity(rerun))
        assert latest_snapshot([snapshot]) is snapshot
        assert snapshot.review_revision == workflow.revision

    def test_identities_of_different_kinds_are_refused(self, revision_zero):
        with pytest.raises(ValueError):
            snapshot_is_stale(
                revision_zero,
                current_evidence_identity={"evidence_kind": "SOMETHING_ELSE",
                                           "evidence_digest": "0" * 64},
            )


# =============================================================================
# 10. THE REAL EVIDENCE THE PRODUCTION WRITER PERSISTED
# =============================================================================
@needs_real_capture
class TestTheFingerprintOverRealPersistedRows:
    def test_the_real_writers_rows_fingerprint_consistently(self, production, monkeypatch):
        """The rows are the GENUINE writer's, over the real capture: no fabricated row is
        involved in this proof."""
        from tests.test_real_world_j20_connection_review_persistence_gap import _persisted
        repository, _, _ = _persisted(production.pipeline, monkeypatch)
        rows = {"steel_members": repository.members, "connections": repository.connections}
        assert rows["connections"], "the real capture persists connection rows"
        first = evidence_identity(rows)
        assert first == evidence_identity(copy.deepcopy(rows))
        assert first["evidence_digest"] == evidence_identity(rows)["evidence_digest"]

    def test_a_duplicated_real_row_changes_the_fingerprint(self, production, monkeypatch):
        from tests.test_real_world_j20_connection_review_persistence_gap import _persisted
        repository, _, _ = _persisted(production.pipeline, monkeypatch)
        rows = {"steel_members": [], "connections": list(repository.connections)}
        doubled = {"steel_members": [], "connections": list(repository.connections) * 2}
        assert evidence_identity(rows)["evidence_digest"] != \
            evidence_identity(doubled)["evidence_digest"]


# =============================================================================
# 11. AN EXISTING PROJECT: NO REVIEW STATE, AND NOTHING THAT COULD INVENT ONE
# =============================================================================
class TestExistingProjectsFailClosed:
    def test_a_project_with_no_snapshot_has_no_review_state(self):
        assert latest_snapshot(()) is None
        assert latest_snapshot([]) is None

    def test_no_function_accepts_a_project_id_alone(self):
        """The reconstruction J21 forbids needs a function that takes a project (or its
        rows) and returns review state. There is none: every public entry point needs a
        genuine workflow, a recorded snapshot or stored rows."""
        for name in ("build_review_snapshot", "snapshot_from_rows",
                     "snapshot_item_from_row", "connection_contract_from_item",
                     "project_contract_from_snapshot", "snapshot_is_stale",
                     "evidence_identity_matches"):
            signature = inspect.signature(getattr(model, name))
            required = {
                parameter.name for parameter in signature.parameters.values()
                if parameter.default is inspect.Parameter.empty
                and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
            }
            assert required - {"project_id"}, name

    def test_a_project_id_is_not_a_workflow(self):
        with pytest.raises(TypeError):
            build_review_snapshot(
                OTHER_PROJECT_ID, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE
            )

    def test_every_engineering_table_is_named_only_in_a_rule_that_refuses_it(self):
        """The design names the engineering tables — to say it does not read them. Every
        such mention must be one of the declared rules, a column's own justification, or
        the fingerprint's input list: a mention anywhere else is a reference to a table."""
        for value in _code_strings(_tree()):
            for table in ENGINEERING_TABLES:
                if table in value:
                    assert value in _declared_prose(), (
                        f"{table!r} is named outside a declared rule: {value[:90]!r}"
                    )

    def test_no_review_items_ledger_is_promoted(self):
        """`review_items` appears only as a declared rule label and inside the rules that
        refuse to promote it — never as an identifier, an argument or a code value."""
        tree = _tree()
        mentions = [value for value in _code_strings(tree) if "review_items" in value]
        assert mentions
        for value in mentions:
            assert value in _declared_prose(), value[:90]
        assert "review_items" not in _identifiers(tree)

    def test_the_model_reads_no_engineering_table(self):
        tree = _tree()
        plain, methods = _calls(tree)
        imported = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not {name for name in imported if name.startswith("app.engineering_data")}
        assert "supabase" not in imported and "app.config" not in imported
        assert "supabase" not in _code_strings(tree)
        assert not {handled for handled in plain | methods
                    if handled in {"table", "from_", "execute", "select", "select_"}}

    def test_the_real_project_status_is_recorded_not_derived(self, workflow, revision_zero):
        """The project status is 7AJ's own gate decision, recorded verbatim. It is not
        re-derived at read time from the items (J13 stays the only status authority, and
        this table never becomes a second one)."""
        assert revision_zero.project_status == workflow.project_decision
        header, rows = revision_zero.to_rows()
        edited = copy.deepcopy(list(rows))
        edited[0]["decision"] = AUTOMATION_DECISION_AUTO
        replayed = project_contract_from_snapshot(snapshot_from_rows(_wire(header), _wire(edited)))
        assert replayed.project_status == workflow.project_decision, (
            "the project status must come from the recorded header, not from the items"
        )


# =============================================================================
# 12. CROSS-PROJECT ISOLATION
# =============================================================================
class TestCrossProjectIsolation:
    def test_a_workflow_without_a_project_is_refused(self, workflow):
        isolated = dataclasses.replace(workflow, project_id=None)
        with pytest.raises(SnapshotRefused) as refused:
            build_review_snapshot(
                isolated, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE
            )
        assert refused.value.code == SNAPSHOT_REFUSED_PROJECT_UNKNOWN

    def test_another_projects_workflow_is_refused(self, workflow):
        with pytest.raises(SnapshotRefused) as refused:
            build_review_snapshot(
                workflow, project_id=OTHER_PROJECT_ID, evidence_rows=NO_EVIDENCE
            )
        assert refused.value.code == SNAPSHOT_REFUSED_PROJECT_MISMATCH

    def test_a_state_without_the_review_layer_is_refused(self, workflow):
        stripped = dataclasses.replace(workflow, _collection=None)
        with pytest.raises(SnapshotRefused) as refused:
            build_review_snapshot(
                stripped, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE
            )
        assert refused.value.code == SNAPSHOT_REFUSED_NO_REVIEW_LAYER

    def test_a_duplicated_package_id_is_refused(self, workflow):
        duplicated = dataclasses.replace(
            workflow, connections=(workflow.connections[0], workflow.connections[0])
        )
        with pytest.raises(SnapshotRefused) as refused:
            build_review_snapshot(
                duplicated, project_id=PROJECT_ID, evidence_rows=NO_EVIDENCE
            )
        assert refused.value.code == SNAPSHOT_REFUSED_DUPLICATE_PACKAGE_ID

    def test_every_row_carries_the_project_it_belongs_to(self, revision_zero, revision_one):
        for snapshot in (revision_zero, revision_one):
            header, rows = snapshot.to_rows()
            assert header["project_id"] == PROJECT_ID
            assert {row["project_id"] for row in rows} == {PROJECT_ID}
            assert {row["review_revision"] for row in rows} == {snapshot.review_revision}


# =============================================================================
# 13. AUTHORIZATION: J19 IS REUSED, NOT EXTENDED
# =============================================================================
class TestJ19AuthorizationIsReused:
    def test_the_design_names_no_second_ownership_or_role(self):
        """No column, no payload key and no identifier introduces an owner, a reviewer or a
        membership: the only identity in either table is `project_id`. Sentences in a rule
        that spell out the reused path are not a second ownership field."""
        tree = _tree()
        columns = _column_names(SNAPSHOT_TABLE_COLUMNS) | _column_names(ITEM_TABLE_COLUMNS)
        assert not (columns & set(OWNERSHIP_TOKENS))
        assert not (_identifiers(tree) & set(OWNERSHIP_TOKENS))
        assert not (_code_strings(tree) & set(OWNERSHIP_TOKENS)), (
            "an exact `user_id`/`role` string would be a column or a payload key"
        )

    def test_the_rls_rule_reuses_the_single_authorization_path(self):
        rule = RLS_RULE.lower()
        assert "projects.user_id" in rule
        assert "service-role" in rule
        assert "no policy for anon/authenticated" in rule
        assert "no reviewer role" in rule
        assert "second ownership column" in rule and "no membership table" in rule

    def test_the_project_relationship_is_one_hop_from_projects(self):
        """The one authorization hop is stated where it lives: on `project_id`, the same
        relationship J19 already authorizes — not a new one."""
        columns = {name: (sql_type, purpose) for name, sql_type, _, purpose in
                   SNAPSHOT_TABLE_COLUMNS}
        assert columns["project_id"][0] == "uuid"
        assert "projects.user_id" in columns["project_id"][1]
        assert "one hop away" in columns["project_id"][1]
        item_types = {name: sql_type for name, sql_type, _, _ in ITEM_TABLE_COLUMNS}
        assert item_types["project_id"] == "uuid"
        assert item_types["review_revision"] == "integer"

    def test_the_j19_binding_is_unchanged(self, env_bound_modules):
        ProjectReviewBinding = env_bound_modules.binding.ProjectReviewBinding
        assert [field.name for field in dataclasses.fields(ProjectReviewBinding)] == [
            "identity", "record", "decision",
        ]
        assert not (REPO / "app" / "production_review" / "connection_review_snapshot.py").exists()

    def test_no_route_was_added(self, production):
        assert _declared_routes(production.main.app) == FROZEN_ROUTES

    def test_the_production_review_package_does_not_import_this_model(self):
        # J46 is the one declared exception, and it is named rather than waved through.
        # The whole job of `project_workflow_resumption.py` is to REPLAY this model's
        # persisted revisions, so it reads the model through J22's own codec; a second
        # codec, or a copy of the model inside the package, is what this guard is for and
        # is still refused here (`connection_review_snapshot.py` must not exist beside it,
        # as the test above asserts). Every other module in the package stays bound by the
        # original rule, so a THIRD reader still has to be declared here.
        READERS = {"project_workflow_resumption.py"}
        for path in sorted((REPO / "app" / "production_review").glob("*.py")):
            if path.name in READERS:
                continue
            source = path.read_text()
            assert "connection_review_snapshot" not in source, path.name
            assert "connection_review_items" not in source, path.name


# =============================================================================
# 14. J13 STAYS THE ONLY STATUS AUTHORITY
# =============================================================================
class TestJ13RemainsTheOnlyStatusAuthority:
    def test_the_status_derivation_is_not_defined_here(self):
        tree = ast.parse(MODULE_PATH.read_text())
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "derive_project_status" not in defined
        assert "review_status_of" not in defined
        assert "derive_project_status" not in MODULE_PATH.read_text()

    def test_the_status_derivation_still_has_one_definition(self):
        definers = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if "def derive_project_status" in path.read_text():
                definers.append(str(path.relative_to(REPO)))
        assert definers == ["app/validation/project_status.py"]


# =============================================================================
# 15. NO DUPLICATE ENGINEERING TRUTH
# =============================================================================
class TestNoDuplicateEngineeringTruth:
    def test_the_dependency_surface_is_pinned(self):
        tree = ast.parse(MODULE_PATH.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert imported == {
            "__future__", "copy", "hashlib", "json",
            "collections.abc", "dataclasses", "typing",
            "app.cad_engine.automation_gate",
            "app.cad_engine.candidate_origin_address",
            "app.cad_engine.connection_review_package",
            "app.cad_engine.drawing_dispatch",
            "app.cad_engine.drawing_output_verification",
            "app.cad_engine.exception_resolution",
            "app.cad_engine.project_connection_review",
            "app.cad_engine.project_workflow",
            "app.cad_engine.review_contract",
        }, sorted(imported - {"__future__", "copy", "hashlib", "json",
                              "collections.abc", "dataclasses", "typing"})

    def test_no_engineering_data_or_persistence_module_is_imported(self):
        source = MODULE_PATH.read_text()
        for forbidden in ("app.engineering_data", "app.pipeline", "app.report",
                          "app.export", "app.drawing_reading", "os", "pathlib",
                          "requests", "httpx", "pypdf", "tempfile"):
            assert f"import {forbidden}" not in source, forbidden
            assert f"from {forbidden} " not in source, forbidden

    def test_the_item_payload_keys_are_pinned(self):
        """Any newly stored fact is a design change and must be declared in the column
        table — this test is the gate that notices one."""
        names = [name for name, _, _, _ in ITEM_TABLE_COLUMNS]
        assert names == [
            "project_id", "review_revision", "review_package_id", "connection_id",
            "decision", "output_status", "verification_status", "last_processed_revision",
            "blocker_codes", "warning_codes", "ai_readings", "evidence", "provenance",
            "tasks", "generated_files",
        ]
        snapshot_names = [name for name, _, _, _ in SNAPSHOT_TABLE_COLUMNS]
        assert snapshot_names == [
            "project_id", "review_revision", "evidence_identity", "evidence_run_ids",
            "project_status", "recorded_at",
        ]

    def test_the_payload_holds_no_engineering_measurement_of_its_own(self, revision_zero,
                                                                   revision_one):
        """The stored shape is the design's own vocabulary. The only keys that are NOT part
        of it are inside a verbatim passthrough — the human's own recorded answer, the AI's
        own reading text, its stated extras. No section, weight, length or member row is
        stored as a fact of this model."""
        structural = (
            _column_names(SNAPSHOT_TABLE_COLUMNS) | _column_names(ITEM_TABLE_COLUMNS)
            | {"member_references", "bolts", "plates", "welds", "connection_type",
               "confidence", "material", "malformed_fields", "unrecognised_fields",
               "quantity", "size", "grade", "type", "thickness_mm", "width_mm", "depth_mm",
               "size_mm", "extra", "source_drawing_id", "drawing_number", "source_page",
               "detail_reference", "grid_reference",
               # J72 — the candidate's own origin, inside `evidence`: the run that read the
               # page and the candidate's index in that page's own array. Neither is an
               # engineering measurement, which is why the gate below stays exactly as strict.
               "analysis_run_id", "candidate_index",
               "task_id", "task_type", "blocker_codes",
               "question", "current_ai_value", "answer_type", "allowed_choices",
               "evidence_requirement", "status", "resolution", "answer", "evidence",
               # J79 — the ONE engineering field a task addresses, inside `tasks`. It is a
               # member of the review package's own ENGINEERING_FIELDS vocabulary, so it is
               # a KEY and never a measurement; the gate below is unchanged and still strict.
               "field_name",
               "evidence_kind", "evidence_tables", "evidence_digest", TUPLE_TAG}
        )
        passthrough = {"answer", "current_ai_value", "malformed_fields",
                       "unrecognised_fields", "extra", "allowed_choices",
                       "provenance"}
        labels = {PROVENANCE_AI_EXTRACTED, PROVENANCE_HUMAN_REVIEWED,
                  PROVENANCE_HUMAN_SUPPLEMENTED}
        for snapshot in (revision_zero, revision_one):
            header, rows = snapshot.to_rows()
            for path in _paths([header, list(rows)]):
                smuggled = set(path) - structural
                assert not smuggled or set(path) & passthrough, path
            for item in snapshot.items:
                # The provenance mapping's keys are the report's own field names (verbatim);
                # its values are the 7V labels and nothing else — no datum hides in a label.
                assert set(item.provenance.values()) <= labels, item.provenance
            for path in _paths([header, list(rows)]):
                if path[0] in _column_names(ITEM_TABLE_COLUMNS) | _column_names(
                    SNAPSHOT_TABLE_COLUMNS
                ):
                    assert not set(path) & {
                        "section_name", "total_weight_kg", "mass_kg", "mark", "length_mm",
                        "solid", "geometry", "dxf", "step_file",
                    }, path

    def test_the_module_writes_nothing(self):
        """No persistence call of any kind exists in the module: it computes rows, it never
        sends them anywhere."""
        plain, methods = _calls(_tree())
        for forbidden in ("insert", "update", "upsert", "delete", "commit", "execute",
                          "table", "rpc", "send", "write", "write_text", "write_bytes"):
            assert forbidden not in plain and forbidden not in methods, forbidden
        assert not {"open", "urlopen", "Popen", "system"} & plain


# =============================================================================
# 16. THIS DESIGN IS UNWIRED AND NOTHING ELSE CHANGED
# =============================================================================
class TestThisDesignIsUnwired:
    def test_no_production_module_imports_the_design(self):
        """J21 wrote no migration and bound the design to nothing. Every module that
        imports it today is named below, and each reads the design rather than adding a
        table, a codec or a second write path — J22's store is the implementation this
        design was accepted to make possible, and J47's route is the first production
        route to reach it. This design's own milestone added no route; the list is exact
        so that a consumer it did not foresee has to be declared rather than absorbed."""
        importers = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if path.name == MODULE_PATH.name:
                continue
            if "connection_review_snapshot" in path.read_text():
                importers.append(str(path.relative_to(REPO)))
        assert importers == [
            # J80: the consumer boundary. It imports exactly one thing from this design —
            # `ReviewSnapshotItem`, the codec `snapshot_item_from_row` returns — and one
            # thing from the task model (`_task_from_row`), because an item's tasks are read
            # back through the system's own decoder or not at all. It writes none of it: no
            # table, no codec, no revision. It has no caller of its own, so this design is
            # still not reachable from any production path.
            "app/cad_engine/cited_candidate_resolution.py",
            "app/engineering_data/connection_review_repository.py",
            # J25: the production surface REPLAYS a recorded snapshot through 7AK's own
            # `project_contract_from_snapshot` so the recorded band and the live band share
            # one presentation chain. It reads this design; it writes none of it and adds
            # no table. The assertion stays exact, so a third consumer must be declared.
            "app/production_connection_review.py",
            # J46: the resumer decodes persisted items back into 7AJ connection states with
            # this codec's own `_state_from_item` rather than restating the projection, so
            # a codec change cannot silently drift from what replay rebuilds. It reads the
            # design and writes none of it. Declared here, as the assertion requires.
            "app/production_review/project_workflow_resumption.py",
            # J50: the baseline-opening operation. It reaches the design the way every
            # writer must — through J22's repository — and imports exactly one thing from
            # it: the gap refusal it has to recognise when two opens race. It names none
            # of the design's tables, columns or codecs itself, and adds no table.
            "app/production_review_opening.py",
            # J47: the route that records a review. It reaches the design the way every
            # writer must — through J22's repository, which owns the builder and the
            # persist — and names none of the design's tables, columns or codecs itself.
            # It is the module that finally BINDS the design to a production route, which
            # is what the docstring above said had not happened; the guard is updated to
            # name it rather than widened, so a further consumer still has to be declared.
            "app/production_review_resolution.py",
        ], importers

    def test_no_migration_was_written(self):
        """J21 wrote none; J22 later wrote exactly one, placing the two tables this design
        declares. J23 wrote a second, and this design reads none of it: a raw AI capture is
        not a snapshot and not a review item, and the two tables below appear in J22's
        migration and nowhere else. The list is pinned so that a migration appearing here
        unremarked fails this file rather than passing quietly — which is what happened when
        J22 added one, and again when J23 did, and again when J28 did. J28's table records PDF
        annotation occurrences and is not a snapshot and not a review item either: the two
        tables below are still written in J22's migration and nowhere else.

        J44 added one, and it is not this design's either. It places a claim row keyed by
        project — who is reviewing a project and until when — and neither table below is
        written in it: a claim is not a snapshot and not a review item, and it records no
        decision, no answer and no reading. It is named here by J44 so that the next
        migration has to name itself too. J61 added one, and it is not this design's
        either: it gives a source document of a project an identity and points a drawing
        at it, and neither table below is written in it — a document identity is not a
        snapshot and not a review item, and it records no decision, no answer and no
        reading. It is named here by J61 so that the next migration has to name itself
        too."""
        migrations = sorted(
            path.name for path in (REPO / "supabase" / "migrations").glob("*.sql")
        )
        assert migrations == [
            "20260924000000_j5_section_resolution_truth.sql",
            "20260924010000_j6_reference_data_identity.sql",
            "20260924020000_j8b_connection_plate_evidence_nullability.sql",
            "20260925000000_j22_connection_review_persistence.sql",
            "20260925010000_j23_page_extraction_captures.sql",
            "20260927000000_j28_pdf_annotation_occurrences.sql",
            "20260928000000_j44_project_review_claims.sql",
            "20260929000000_j61_project_documents.sql",
            "20260929010000_j66_field_evidence_citations.sql",
            "20261006000000_l19_selected_extraction_lineage.sql",
        ], migrations
        written_in = {}
        for path in sorted((REPO / "supabase").rglob("*.sql")):
            text = path.read_text()
            for name in (SNAPSHOT_TABLE, ITEM_TABLE):
                if name in text:
                    written_in.setdefault(name, []).append(path.name)
        # J65 RATIFIED THE WIDENING, AND ONLY THE WIDENING. The fence was one file when it
        # was written, because J22's migration was the only migration that implemented this
        # design. J66's migration hangs one more table from the ITEM through a composite
        # foreign key, so it has to name the item table; it also names the snapshot table
        # once, in the header's listing of the chain the new table belongs to. The assertion
        # stays an EXACT SET rather than a lower bound: a third file naming either table has
        # to be declared here rather than absorbed. The two files below are named by two
        # different rights, and the difference is stated so it cannot be over-read — the
        # item's second mention is STRUCTURAL (a foreign key the database enforces), while
        # the snapshot's is the chain listing and nothing else: J66 writes no row of it,
        # alters nothing in it, and adds no second snapshot writer or second item writer.
        # Nothing here is relaxed; the permission is for these two migrations to name these
        # two tables, not a licence to name them anywhere.
        assert written_in == {
            SNAPSHOT_TABLE: [
                "20260925000000_j22_connection_review_persistence.sql",
                "20260929010000_j66_field_evidence_citations.sql",
            ],
            ITEM_TABLE: [
                "20260925000000_j22_connection_review_persistence.sql",
                "20260929010000_j66_field_evidence_citations.sql",
            ],
        }, written_in

    def test_the_design_reads_no_environment_no_secret_and_no_file(self, monkeypatch):
        for name in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY",
                     "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        source = MODULE_PATH.read_text()
        for forbidden in ("os.environ", "getenv", "environ[", "SUPABASE", "app.config",
                          "open(", "Path(", "read_text(", "read_bytes(", "requests",
                          "httpx", "urllib"):
            assert forbidden not in source, forbidden
        plain, methods = _calls(_tree())
        assert not {"open", "print", "input"} & plain
        assert not {"table", "execute", "post", "send", "rpc"} & methods

    def test_the_scope_statement_claims_nothing(self):
        statement = model.SNAPSHOT_SCOPE_STATEMENT
        assert "writes nothing" in statement
        assert "bound to no route" in statement
        for forbidden in ("approved", "fabrication-ready", "verified", "safe"):
            assert forbidden not in statement.lower(), forbidden
