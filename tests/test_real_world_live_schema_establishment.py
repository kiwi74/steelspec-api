"""
Step J0 — LIVE SCHEMA ESTABLISHMENT (READ-ONLY).

WHAT THIS MODULE IS
===================
The evidence record for the one thing Steps G/H/I could not establish from the
repository: **the actual live Supabase schema of `steel_sections`.**

Steps G, H and I could not answer three questions, and Step I refused to guess:

  1. is `steel_sections.weight_per_metre` NULL-able?
  2. what family values does the database actually permit?
  3. is `steel_sections.name` unique?

All three are answered here from the live database's own metadata, and the
answers are pinned below with their provenance. This module records facts; it
does not modify anything, and it does not perform a production write.

HOW THE FACTS WERE ESTABLISHED (read-only, no SQL authored for execution)
========================================================================
Three independent, read-only routes, all against the genuine project:

  A. the platform's server-generated schema types for the project —
     `/v1/projects/<ref>/types/typescript`. This is the platform reading
     `information_schema`/`pg_catalog` on our behalf; *we* author no SQL. It
     is the authority for column nullability, enum membership and foreign
     keys.
  B. PostgREST's own OpenAPI document for the project — it marks the primary
     key and lists the NOT NULL-without-default columns. An independent second
     source for the primary key and nullability.
  C. read-only PostgREST **filters** (`?family=eq.<value>`). PostgreSQL
     rejects an enum value it does not have, so a filtered read is an
     empirical membership test for the `section_family` enum that writes
     nothing.

Routes A and B agree on nullability; routes A and C agree on the enum.

WHAT WAS ESTABLISHED
====================
  * `family` is a PostgreSQL ENUM, `public.section_family`, NOT NULL, whose
    permitted values are exactly UB, UC, PFC, EA, UA, RHS, SHS, CHS, FLAT,
    OTHER. **`FL` and `PL` are NOT permitted** — the database rejects them
    (`22P02 invalid input value for enum section_family`). This resolves the
    family decision that Step I left UNRESOLVED: the only schema-legal way to
    store these four plate identities is `FLAT`.
  * `weight_per_metre` IS NULL-able.
  * `name` is the PRIMARY KEY — exact, case-sensitive uniqueness enforced by
    the database. It is NOT normalised uniqueness: the application folds case
    and whitespace (`SectionMatcher._normalise`), so `130x12FL` and
    `130X12FL` are two distinct primary keys that normalise to one key. The
    database does not prevent that; only a pre-write check does.
  * `steel_members.section_name` -> `steel_sections.name` is the only foreign
    key; `steel_sections` has none of its own.

WHAT THIS MODULE DOES **NOT** CLAIM
===================================
  * It does not claim the four rows are production-usable *at J0*. At J0
    they were not: `FLAT` had no CAD geometry builder, so a `FLAT` row
    resolved EXACT and then raised `UnsupportedSectionFamilyError` — that
    consequence was pinned here as its own test, and it was the whole
    reason Step J was blocked. NOTE (Milestone J1): that consequence has
    since been superseded by the accepted production FLAT -> FL CAD
    admission, so the test now pins the J0->J1 boundary instead — the
    source family is still `FLAT`, and a family with neither a builder nor
    an explicit projection is still refused. The J1 proof itself lives in
    tests/test_real_world_production_flat_cad_admission.py.
  * It does not claim the write is ready. `PRODUCTION_CATALOGUE_WRITE_READY`
    was NO at J0, and the reason was the CAD consequence above — not
    schema uncertainty. (J1 removed that reason; the write itself is still
    a separate, unperformed milestone.)
  * It does not claim CHECK constraints were enumerated. Constraint/index
    metadata beyond the primary key, the enum and the foreign keys is not
    reachable through the read-only metadata routes used here; that gap is
    named explicitly in `RESIDUAL_UNKNOWNS`.

FAIL-CLOSED
===========
The classifier below returns a blocked category for *every* missing fact. If
nullability, family admissibility or name uniqueness is UNKNOWN, no identity
can be classified READY_FOR_WRITE — the Gate refuses rather than assumes. The
mutation tests at the bottom prove that refusal is non-vacuous.

Nothing here prints, returns or asserts on a credential value.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

# ===========================================================================
# Provenance — where every pinned fact below came from
# ===========================================================================

PROJECT_REF = "yzwqhekpardekbczqtce"
PROJECT_NAME = "steelspec"
ORGANIZATION_ID = "avvvepycpfnamwkmqwkn"
REGION = "ap-southeast-1"
POSTGRES_ENGINE = "17"
POSTGREST_VERSION = "14.5"
REFERENCE_TABLE = "steel_sections"
ESTABLISHED_ON = "2026-09-24"

# The genuine credential mechanism, resolved at run time and never printed.
LIVE_OPT_IN_ENV = "STEELSPEC_J0_LIVE_SCHEMA"
ACCESS_TOKEN_ENV = "SUPABASE_ACCESS_TOKEN"
KEYCHAIN_SERVICE = "Supabase CLI"
KEYCHAIN_ACCOUNT = "supabase"

EVIDENCE_TYPES_ENDPOINT = f"/v1/projects/{PROJECT_REF}/types/typescript"
EVIDENCE_POSTGREST_OPENAPI = f"https://{PROJECT_REF}.supabase.co/rest/v1/"
EVIDENCE_ENUM_PROBE = (
    f"https://{PROJECT_REF}.supabase.co/rest/v1/{REFERENCE_TABLE}?family=eq.<value>"
)

# ===========================================================================
# THE ESTABLISHED FACTS
# ===========================================================================

# Route A + route C. The whole permitted family vocabulary, verbatim.
SECTION_FAMILY_ENUM = ("UB", "UC", "PFC", "EA", "UA", "RHS", "SHS", "CHS", "FLAT", "OTHER")

# Route C, measured: PostgreSQL rejects these with
#   22P02 invalid input value for enum section_family: "<value>"
FAMILY_VALUES_REJECTED_BY_DB = ("FL", "PL")

# Route A (`Row` marks nullable columns `| null`) and route B (`required`
# lists NOT NULL-without-default columns) agree exactly.
NOT_NULL_COLUMNS = ("family", "name")
NULLABLE_COLUMNS = (
    "depth",
    "flange_thickness",
    "flange_width",
    "leg_size",
    "outside_diameter",
    "thickness",
    "web_thickness",
    "weight_per_metre",
    "width",
)
ALL_COLUMNS = tuple(sorted(NOT_NULL_COLUMNS + NULLABLE_COLUMNS))

WEIGHT_PER_METRE_NULLABLE = True

# Route B, verbatim: 'Note: This is a Primary Key.<pk/>' on `name`.
PRIMARY_KEY = ("name",)
NAME_UNIQUENESS = "EXACT_CASE_SENSITIVE_PRIMARY_KEY"
NORMALISED_NAME_UNIQUENESS = "NOT_ENFORCED_BY_DATABASE"

# Route A, verbatim. steel_sections has no foreign keys of its own.
OUTBOUND_FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = ()
INBOUND_FOREIGN_KEYS = (("steel_members", "section_name", "steel_sections", "name"),)

# The two vocabularies are genuinely different, and that difference is the
# reason normalised uniqueness is not a database guarantee:
#   database identity : the literal value of `name`
#   application identity: re.sub(r"\s+", "", name.strip().upper())
NORMALISE_PATTERN = r"\s+"


def _normalise(name: str) -> str:
    """`SectionMatcher._normalise`, restated verbatim (regex + .strip().upper())."""
    return re.sub(NORMALISE_PATTERN, "", name.strip().upper())


NORMALISE_EXAMPLES = (
    # (stored name, another spelling, normalise to the same key?)
    ("130x12FL", "130X12FL", True),
    ("130x12FL", " 130 x12fl ", True),
    ("130x12FL", "130x10FL", False),
)

# --- the fresh live data read (routes: a read-only PostgREST GET) ----------
PINNED_218_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
PINNED_218_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
PINNED_218_ROW_COUNT = 218
PINNED_218_DIGEST = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"

FRESH_LIVE_ROW_COUNT = 218
FRESH_LIVE_DIGEST = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"
FRESH_LIVE_DISTINCT_NORMALISED_KEYS = 218
LIVE_STATE_CHANGED_FROM_PIN = False

# The live FLAT shape and naming convention, measured across all 23 rows.
LIVE_FLAT_ROW_COUNT = 23
LIVE_FLAT_POPULATED_COLUMNS = ("family", "name", "thickness", "weight_per_metre", "width")
LIVE_FLAT_NAMES_USE_LOWERCASE_X = True
LIVE_FLAT_WEIGHT_NULL_COUNT = 0

# ===========================================================================
# The four proposed identities, and the evidence Steps E/F/G/H/I accepted
# ===========================================================================

DIMENSION_EVIDENCE = (
    "the drawing's own printed SIZE cell and an accepted Milestone C/D row "
    "state the same two values (Step F classification A, Step G)"
)
WEIGHT_EVIDENCE_180 = (
    "accepted Milestone C row 180X20FL records 28.3 kg/m; the drawing prints "
    "PL008 180X20FL 300 340 1 0.14 9.6 (9.6 kg over 340 mm)"
)
WEIGHT_EVIDENCE_250 = (
    "accepted Milestone C row 250X12FL records 23.6 kg/m; the drawing prints "
    "PL028 250X12FL 300 155 1 0.09 3.7 (3.7 kg over 155 mm)"
)
NO_WEIGHT_EVIDENCE = (
    "no genuine repository evidence ever recorded a weight for this row, and "
    "none was invented, calculated or copied (Step D, Step G)"
)


@dataclass(frozen=True)
class ProposedIdentity:
    """One candidate row, in the only schema-legal shape."""
    live_name: str
    drawing_spellings: tuple[str, ...]
    width: float
    thickness: float
    weight_per_metre: float | None
    dimensions_evidence: str
    weight_evidence: str | None


PROPOSED_IDENTITIES = (
    ProposedIdentity(
        live_name="130x12FL",
        drawing_spellings=("130X12FL", "130x12FL"),
        width=130.0,
        thickness=12.0,
        weight_per_metre=None,
        dimensions_evidence=DIMENSION_EVIDENCE,
        weight_evidence=NO_WEIGHT_EVIDENCE,
    ),
    ProposedIdentity(
        live_name="180x20FL",
        drawing_spellings=("180X20FL", "180x20FL"),
        width=180.0,
        thickness=20.0,
        weight_per_metre=28.3,
        dimensions_evidence=DIMENSION_EVIDENCE,
        weight_evidence=WEIGHT_EVIDENCE_180,
    ),
    ProposedIdentity(
        live_name="250x12FL",
        drawing_spellings=("250X12FL",),
        width=250.0,
        thickness=12.0,
        weight_per_metre=23.6,
        dimensions_evidence=DIMENSION_EVIDENCE,
        weight_evidence=WEIGHT_EVIDENCE_250,
    ),
    ProposedIdentity(
        live_name="90x10FL",
        drawing_spellings=("90x10FL",),
        width=90.0,
        thickness=10.0,
        weight_per_metre=None,
        dimensions_evidence=DIMENSION_EVIDENCE,
        weight_evidence=NO_WEIGHT_EVIDENCE,
    ),
)

# All six raw drawing spellings, and the canonical identity each normalises to.
EXPECTED_CANONICAL = {
    "130X12FL": "130X12FL",
    "130x12FL": "130X12FL",
    "180X20FL": "180X20FL",
    "180x20FL": "180X20FL",
    "250X12FL": "250X12FL",
    "90x10FL": "90X10FL",
}

# The dangerous near-match matrix Step H measured against the frozen capture.
# Re-measured here through the genuine matcher on the live rows.
NEAR_MATCH_MATRIX = (
    # token, resolution, matched row name
    ("130X10FL", "EXACT", "130x10FL"),
    ("180X10FL", "NONE", None),
    ("250X90PFC", "NONE", None),
    ("90X10EA", "NONE", None),
    ("200UB30", "NONE", None),
    ("300X8FL", "NONE", None),
    ("250PFC", "EXACT", "250PFC"),
    ("200UB25", "SUFFIX_FALLBACK", "200UB25.4"),
    ("310UB40", "SUFFIX_FALLBACK", "310UB40.4"),
)

# ===========================================================================
# The readiness vocabulary — exactly the five categories Step I fixed
# ===========================================================================

READY_FOR_WRITE = "READY_FOR_WRITE"
BLOCKED_BY_FAMILY_DECISION = "BLOCKED_BY_FAMILY_DECISION"
BLOCKED_BY_SCHEMA = "BLOCKED_BY_SCHEMA"
BLOCKED_BY_EVIDENCE = "BLOCKED_BY_EVIDENCE"
BLOCKED_BY_WRITE_MECHANISM = "BLOCKED_BY_WRITE_MECHANISM"
CATEGORIES = (
    READY_FOR_WRITE,
    BLOCKED_BY_FAMILY_DECISION,
    BLOCKED_BY_SCHEMA,
    BLOCKED_BY_EVIDENCE,
    BLOCKED_BY_WRITE_MECHANISM,
)

RESOLVED_TO_FLAT = "RESOLVED_TO_FLAT"
RESOLVED_TO_FL = "RESOLVED_TO_FL"
RESOLVED_TO_PL = "RESOLVED_TO_PL"
UNRESOLVED = "UNRESOLVED"

# Named, explicitly, so nobody reads this module as claiming more than it did.
RESIDUAL_UNKNOWNS = (
    "CHECK constraints on steel_sections: not reachable through the read-only "
    "metadata routes used here. A CHECK can only REJECT the insert (the whole "
    "statement then fails as one unit), so it is bounded, not corrupting.",
    "secondary indexes and triggers: not observable through those routes; "
    "irrelevant to a four-row INSERT ONLY.",
    "column DEFAULT expressions on the nullable columns: not directly "
    "observable, and immaterial — every column the four rows populate is "
    "supplied explicitly, and `name`/`family` (the only NOT NULL columns) have "
    "no default (route B lists them as required).",
)


# ===========================================================================
# The classifier — pure, fail-closed
# ===========================================================================


@dataclass(frozen=True)
class SchemaFacts:
    """What the live database was actually established to permit.

    Every field is Optional precisely so that 'we do not know' is
    representable, and so the classifier can refuse on it.
    """
    inspected: bool
    family_enum: tuple[str, ...] | None
    weight_per_metre_nullable: bool | None
    name_unique: bool | None
    not_null_columns: tuple[str, ...] | None


ESTABLISHED_FACTS = SchemaFacts(
    inspected=True,
    family_enum=SECTION_FAMILY_ENUM,
    weight_per_metre_nullable=WEIGHT_PER_METRE_NULLABLE,
    name_unique=True,
    not_null_columns=NOT_NULL_COLUMNS,
)


def family_decision(facts: SchemaFacts) -> str:
    """What the SCHEMA permits. Never a preference, never a CAD convenience."""
    if not facts.inspected or facts.family_enum is None:
        return UNRESOLVED
    permitted = set(facts.family_enum)
    plate = permitted & {"FLAT", "FL", "PL"}
    if plate == {"FLAT"}:
        return RESOLVED_TO_FLAT
    if plate == {"FL"}:
        return RESOLVED_TO_FL
    if plate == {"PL"}:
        return RESOLVED_TO_PL
    return UNRESOLVED


def stored_family_for(facts: SchemaFacts) -> str | None:
    """The family value to store, or None when the schema does not decide."""
    decision = family_decision(facts)
    return {
        RESOLVED_TO_FLAT: "FLAT",
        RESOLVED_TO_FL: "FL",
        RESOLVED_TO_PL: "PL",
    }.get(decision)


def classify_identity(
    identity: ProposedIdentity,
    facts: SchemaFacts,
    *,
    write_mechanism_available: bool = True,
) -> str:
    """Exactly one of CATEGORIES. Fail-closed on every missing fact.

    NOTE ON `READY_FOR_WRITE`: it means only that no schema, evidence, family
    or identity blocker stands between this row and an INSERT INTO
    steel_sections. It is NOT a statement that the row is usable in
    production — see `test_flat_resolves_exact_and_then_fails_in_cad`.
    """
    if not facts.inspected:
        return BLOCKED_BY_SCHEMA
    if facts.family_enum is None:
        return BLOCKED_BY_FAMILY_DECISION
    if facts.weight_per_metre_nullable is None:
        return BLOCKED_BY_SCHEMA
    if facts.name_unique is None or facts.name_unique is False:
        return BLOCKED_BY_SCHEMA
    if family_decision(facts) is UNRESOLVED:
        return BLOCKED_BY_FAMILY_DECISION
    if identity.dimensions_evidence is None:
        return BLOCKED_BY_EVIDENCE
    # A row with no evidenced weight may only be written where the schema
    # permits NULL. We never derive one to satisfy a NOT NULL.
    if identity.weight_per_metre is None and not facts.weight_per_metre_nullable:
        return BLOCKED_BY_SCHEMA
    if not write_mechanism_available:
        return BLOCKED_BY_WRITE_MECHANISM
    return READY_FOR_WRITE


# ===========================================================================
# The genuine production modules, under the established test-only env boundary
# ===========================================================================

TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "fake-test-signature-not-a-real-key"
)


@pytest.fixture(scope="module")
def production():
    """The REAL production modules, under the test-only configuration boundary.

    `app/config.py` reads `os.environ` at import time. The environment is saved
    and restored immediately, AND every module this import adds to sys.modules
    is removed at teardown — only the app's own modules, because popping a C
    extension (cadquery/numpy/ezdxf) breaks it for the rest of the process.

    That teardown is not tidiness, it is what keeps the pre-existing
    `tests/test_smoke_imports.py` SUPABASE_URL failures HONEST: those tests fail
    because `app.config` cannot be imported from a bare environment, and a
    module that left `app.config` cached in sys.modules would silently turn
    those 11 failures green. This module must not do that.
    """
    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        modules = SimpleNamespace(
            section_matcher=importlib.import_module("app.engineering_data.section_matcher"),
            interface=importlib.import_module("app.cad_engine.interface"),
            sections=importlib.import_module("app.cad_engine.sections"),
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    yield modules

    for name in set(sys.modules) - before:
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


class _InjectedResult:
    def __init__(self, data):
        self.data = data


class _InjectedQuery:
    def __init__(self, client):
        self._client = client

    def select(self, *columns):
        return self

    def execute(self):
        return _InjectedResult(copy.deepcopy(self._client.rows))


class _InjectedClient:
    """Reference rows are injected; nothing here opens a connection."""

    def __init__(self, rows):
        self.rows = list(rows)

    def table(self, name):
        return _InjectedQuery(self)


def _matcher_with(rows, production):
    """A REAL SectionMatcher over injected rows — no network, no live client."""
    section_matcher = production.section_matcher
    saved = section_matcher.supabase
    section_matcher.supabase = _InjectedClient(rows)
    try:
        return section_matcher.SectionMatcher()
    finally:
        section_matcher.supabase = saved


def _live_rows() -> list:
    """The pinned live capture — this module reconciles against real rows or not at all."""
    if not PINNED_218_PATH.exists():
        pytest.skip(
            f"the captured live section rows are not present at {PINNED_218_PATH} — "
            "Step J0 verifies against the real reference rows or not at all"
        )
    raw = PINNED_218_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PINNED_218_SHA256, (
        "the live catalogue capture does not match the sha256 this module pins — "
        "refusing to verify against unverified reference data"
    )
    return json.loads(raw)


def _flat_row(identity: ProposedIdentity) -> dict:
    """The row exactly as the live FLAT convention shapes it."""
    return {
        "name": identity.live_name,
        "family": "FLAT",
        "depth": None,
        "flange_width": None,
        "flange_thickness": None,
        "web_thickness": None,
        "weight_per_metre": identity.weight_per_metre,
        "width": identity.width,
        "thickness": identity.thickness,
        "outside_diameter": None,
        "leg_size": None,
    }


# ===========================================================================
# 1. The pinned facts are internally consistent
# ===========================================================================


class TestEstablishedSchemaFacts:
    def test_the_category_vocabulary_is_exactly_the_five_step_i_fixed(self):
        assert CATEGORIES == (
            "READY_FOR_WRITE",
            "BLOCKED_BY_FAMILY_DECISION",
            "BLOCKED_BY_SCHEMA",
            "BLOCKED_BY_EVIDENCE",
            "BLOCKED_BY_WRITE_MECHANISM",
        )
        assert len(set(CATEGORIES)) == 5

    def test_the_whole_column_set_is_accounted_for(self):
        # every column is either NOT NULL or nullable, and the two sets are
        # disjoint and together cover the relation exactly
        assert set(NOT_NULL_COLUMNS).isdisjoint(NULLABLE_COLUMNS)
        assert set(NOT_NULL_COLUMNS + NULLABLE_COLUMNS) == set(ALL_COLUMNS)
        assert len(ALL_COLUMNS) == len(NOT_NULL_COLUMNS) + len(NULLABLE_COLUMNS) == 11
        # the FLAT shape Step H measured: these five populated, everything else NULL
        assert set(LIVE_FLAT_POPULATED_COLUMNS) == {
            "name", "family", "width", "thickness", "weight_per_metre"
        }
        assert set(LIVE_FLAT_POPULATED_COLUMNS) <= set(ALL_COLUMNS)
        assert set(LIVE_FLAT_POPULATED_COLUMNS) - set(NULLABLE_COLUMNS) == {"name", "family"}

    def test_weight_per_metre_is_declared_nullable_and_is_not_not_null(self):
        assert WEIGHT_PER_METRE_NULLABLE is True
        assert "weight_per_metre" in NULLABLE_COLUMNS
        assert "weight_per_metre" not in NOT_NULL_COLUMNS

    def test_the_enum_permits_flat_and_rejects_fl_and_pl(self):
        assert "FLAT" in SECTION_FAMILY_ENUM
        assert "FL" not in SECTION_FAMILY_ENUM
        assert "PL" not in SECTION_FAMILY_ENUM
        for rejected in FAMILY_VALUES_REJECTED_BY_DB:
            assert rejected not in SECTION_FAMILY_ENUM

    def test_the_enum_is_the_drawing_family_vocabulary_plus_flat_plus_other(self):
        # The live enum carries the drawing codes, but spells the plate family
        # FLAT and adds a catch-all. FL/PL are absent from both.
        for code in ("UB", "UC", "PFC", "EA", "RHS", "SHS", "CHS"):
            assert code in SECTION_FAMILY_ENUM
        assert SECTION_FAMILY_ENUM[-1] == "OTHER"

    def test_name_is_the_primary_key(self):
        assert PRIMARY_KEY == ("name",)
        assert NAME_UNIQUENESS == "EXACT_CASE_SENSITIVE_PRIMARY_KEY"

    def test_normalised_uniqueness_is_not_a_database_guarantee(self):
        assert NORMALISED_NAME_UNIQUENESS == "NOT_ENFORCED_BY_DATABASE"
        # Distinct primary keys that the application folds to one key.
        assert _normalise("130x12FL") == _normalise("130X12FL")
        assert _normalise("130x12FL") == "130X12FL"
        assert "130x12FL" != "130X12FL"  # ...but they are two distinct DB rows

    def test_the_normaliser_matches_production(self, production):
        section_matcher = production.section_matcher
        for stored, other, same in NORMALISE_EXAMPLES:
            assert section_matcher.SectionMatcher._normalise(None, stored) == _normalise(stored)
            assert (
                section_matcher.SectionMatcher._normalise(None, stored)
                == section_matcher.SectionMatcher._normalise(None, other)
            ) is same

    def test_steel_sections_has_no_outbound_foreign_key(self):
        assert OUTBOUND_FOREIGN_KEYS == ()
        assert INBOUND_FOREIGN_KEYS == (("steel_members", "section_name", "steel_sections", "name"),)

    def test_residual_unknowns_are_named_not_hidden(self):
        assert len(RESIDUAL_UNKNOWNS) == 3
        assert any("CHECK" in item for item in RESIDUAL_UNKNOWNS)

    def test_the_live_flat_convention_is_pinned(self):
        assert LIVE_FLAT_NAMES_USE_LOWERCASE_X is True
        assert LIVE_FLAT_WEIGHT_NULL_COUNT == 0
        for identity in PROPOSED_IDENTITIES:
            assert "x" in identity.live_name and "X" not in identity.live_name


class TestFreshLiveState:
    def test_the_fresh_live_read_matches_the_pinned_capture_exactly(self):
        assert FRESH_LIVE_ROW_COUNT == PINNED_218_ROW_COUNT
        assert FRESH_LIVE_DIGEST == PINNED_218_DIGEST
        assert LIVE_STATE_CHANGED_FROM_PIN is False

    def test_no_duplicate_normalised_identity_exists_live(self):
        rows = _live_rows()
        keys = [_normalise(row["name"]) for row in rows]
        assert len(rows) == FRESH_LIVE_ROW_COUNT
        assert len(set(keys)) == FRESH_LIVE_DISTINCT_NORMALISED_KEYS
        assert not [k for k in set(keys) if keys.count(k) > 1]

    def test_the_digest_of_the_pinned_rows_reproduces_the_pinned_value(self, production):
        assert production.section_matcher.reference_data_digest(_live_rows()) == PINNED_218_DIGEST

    def test_the_live_flat_shape_and_family_vocabulary_are_as_pinned(self):
        rows = _live_rows()
        flat = [r for r in rows if r["family"] == "FLAT"]
        assert len(flat) == LIVE_FLAT_ROW_COUNT
        assert not [r for r in rows if r["family"] in ("FL", "PL")]
        assert not [r for r in flat if r["weight_per_metre"] is None]
        for row in flat:
            for column in row:
                if column not in LIVE_FLAT_POPULATED_COLUMNS:
                    assert row[column] is None, f"{row['name']}.{column} is not NULL"


# ===========================================================================
# 2. The four identities, against the established schema
# ===========================================================================


class TestFamilyDecision:
    def test_the_schema_establishes_the_family_decision(self):
        assert family_decision(ESTABLISHED_FACTS) == RESOLVED_TO_FLAT
        assert stored_family_for(ESTABLISHED_FACTS) == "FLAT"

    def test_the_decision_is_taken_from_the_schema_not_from_cad_convenience(self, production):
        # CAD accepts FL and PL and refuses FLAT. The decision says FLAT anyway:
        # it is read off the database's enum, never off what CAD happens to build.
        sections = production.sections
        assert "FLAT" not in sections.PROFILE_BUILDERS
        assert "FL" in sections.PROFILE_BUILDERS and "PL" in sections.PROFILE_BUILDERS
        assert family_decision(ESTABLISHED_FACTS) == RESOLVED_TO_FLAT

    def test_an_unknown_enum_never_resolves(self):
        for enum in (None, ("UB", "UC"), ("FL", "PL"), ("FLAT", "FL")):
            facts = SchemaFacts(True, enum, True, True, NOT_NULL_COLUMNS)
            assert family_decision(facts) == UNRESOLVED
            assert stored_family_for(facts) is None


class TestIdentityClassification:
    def test_all_four_identities_are_write_ready_against_the_established_schema(self):
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, ESTABLISHED_FACTS) == READY_FOR_WRITE

    def test_a_row_without_an_evidenced_weight_rides_on_schema_nullability(self):
        # 130x12FL and 90x10FL carry no evidence-backed weight. NULL is
        # schema-permitted, so they are writable; their weight stays NOT SPECIFIED.
        for identity in PROPOSED_IDENTITIES:
            if identity.weight_per_metre is None:
                assert WEIGHT_PER_METRE_NULLABLE is True
                assert "no genuine repository evidence" in identity.weight_evidence
                assert classify_identity(identity, ESTABLISHED_FACTS) == READY_FOR_WRITE

    def test_the_evidenced_weights_are_the_accepted_ones_and_only_those(self):
        by_name = {i.live_name: i for i in PROPOSED_IDENTITIES}
        assert by_name["180x20FL"].weight_per_metre == 28.3
        assert by_name["250x12FL"].weight_per_metre == 23.6
        # the derived near-misses Step G named must not appear here
        for row in PROPOSED_IDENTITIES:
            assert row.weight_per_metre not in (28.2353, 23.8710)
        assert by_name["130x12FL"].weight_per_metre is None
        assert by_name["90x10FL"].weight_per_metre is None

    def test_every_classification_is_one_of_the_five_categories(self):
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, ESTABLISHED_FACTS) in CATEGORIES


# ===========================================================================
# 3. Fail-closed and non-vacuity — the gate must refuse, not assume
# ===========================================================================


class TestFailClosed:
    def test_an_uninspected_schema_blocks_every_identity(self):
        facts = SchemaFacts(False, None, None, None, None)
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, facts) == BLOCKED_BY_SCHEMA

    def test_unknown_weight_nullability_blocks(self):
        facts = SchemaFacts(True, SECTION_FAMILY_ENUM, None, True, NOT_NULL_COLUMNS)
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, facts) == BLOCKED_BY_SCHEMA

    def test_unknown_family_enum_blocks_on_the_family_decision(self):
        facts = SchemaFacts(True, None, True, True, NOT_NULL_COLUMNS)
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, facts) == BLOCKED_BY_FAMILY_DECISION

    def test_unknown_uniqueness_blocks(self):
        for unknown in (None, False):
            facts = SchemaFacts(True, SECTION_FAMILY_ENUM, True, unknown, NOT_NULL_COLUMNS)
            for identity in PROPOSED_IDENTITIES:
                assert classify_identity(identity, facts) == BLOCKED_BY_SCHEMA

    def test_an_absent_write_mechanism_blocks(self):
        for identity in PROPOSED_IDENTITIES:
            assert (
                classify_identity(identity, ESTABLISHED_FACTS, write_mechanism_available=False)
                == BLOCKED_BY_WRITE_MECHANISM
            )


class TestMutationNonVacuity:
    """Each mutation moves a measured result — the gate is not vacuous."""

    def test_M1_weight_becomes_not_null_and_the_unweighed_rows_are_blocked(self):
        facts = SchemaFacts(True, SECTION_FAMILY_ENUM, False, True, ("family", "name", "weight_per_metre"))
        results = {i.live_name: classify_identity(i, facts) for i in PROPOSED_IDENTITIES}
        assert results["130x12FL"] == BLOCKED_BY_SCHEMA
        assert results["90x10FL"] == BLOCKED_BY_SCHEMA
        # the two rows WITH evidenced weights are unaffected by the mutation
        assert results["180x20FL"] == READY_FOR_WRITE
        assert results["250x12FL"] == READY_FOR_WRITE
        # and the unmutated facts really did say otherwise — the mutation caused this
        unmutated = {i.live_name: classify_identity(i, ESTABLISHED_FACTS) for i in PROPOSED_IDENTITIES}
        assert unmutated["130x12FL"] == READY_FOR_WRITE
        assert unmutated != results

    def test_M2_enum_without_flat_and_no_identity_can_be_stored(self):
        facts = SchemaFacts(True, ("UB", "UC", "PFC", "FLAT", "OTHER"), True, True, NOT_NULL_COLUMNS)
        assert family_decision(facts) == RESOLVED_TO_FLAT
        # now the schema that permits only FL (or only PL) — neither is FLAT
        for enum, expected in ((("UB", "FL"), RESOLVED_TO_FL), (("UB", "PL"), RESOLVED_TO_PL)):
            mutated = SchemaFacts(True, enum, True, True, NOT_NULL_COLUMNS)
            assert family_decision(mutated) == expected
            assert stored_family_for(mutated) != "FLAT"
        no_plate = SchemaFacts(True, ("UB", "UC", "OTHER"), True, True, NOT_NULL_COLUMNS)
        assert family_decision(no_plate) == UNRESOLVED
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, no_plate) == BLOCKED_BY_FAMILY_DECISION

    def test_M3_removing_the_primary_key_prevents_ready(self):
        facts = SchemaFacts(True, SECTION_FAMILY_ENUM, True, None, NOT_NULL_COLUMNS)
        assert classify_identity(PROPOSED_IDENTITIES[0], facts) == BLOCKED_BY_SCHEMA
        assert classify_identity(PROPOSED_IDENTITIES[0], ESTABLISHED_FACTS) == READY_FOR_WRITE

    def test_M4_removing_dimension_evidence_blocks_on_evidence(self):
        stripped = ProposedIdentity(
            live_name="130x12FL",
            drawing_spellings=("130X12FL",),
            width=130.0,
            thickness=12.0,
            weight_per_metre=None,
            dimensions_evidence=None,
            weight_evidence=NO_WEIGHT_EVIDENCE,
        )
        assert classify_identity(stripped, ESTABLISHED_FACTS) == BLOCKED_BY_EVIDENCE

    def test_M5_a_lying_schema_claim_is_caught_by_the_pinned_facts(self):
        # If someone silently rewrote the established enum, the pinned facts and
        # the decision diverge — the module records the divergence rather than
        # quietly agreeing with the lie.
        lying = SchemaFacts(True, ("UB", "UC", "PFC", "FL", "PL", "FLAT", "OTHER"), True, True, NOT_NULL_COLUMNS)
        assert family_decision(lying) == UNRESOLVED
        assert family_decision(lying) != family_decision(ESTABLISHED_FACTS)
        assert stored_family_for(lying) is None


# ===========================================================================
# 4. The 250 special case — 250x12FL / 250PFC / 250X90PFC stay distinct
# ===========================================================================


class Test250SpecialCase:
    def test_250pfc_is_a_live_row_and_the_other_two_are_not(self):
        rows = _live_rows()
        names = {row["name"] for row in rows}
        assert "250PFC" in names
        assert "250X90PFC" not in names
        assert not [n for n in names if _normalise(n) == _normalise("250X90PFC")]
        assert not [n for n in names if _normalise(n) == _normalise("250X12FL")]

    def test_adding_250x12fl_leaves_250pfc_exactly_as_it_was(self, production):
        rows = _live_rows()
        before = _matcher_with(rows, production)
        after = _matcher_with(rows + [_flat_row(PROPOSED_IDENTITIES[2])], production)
        assert before.resolve("250PFC").resolution == "EXACT"
        assert after.resolve("250PFC").resolution == "EXACT"
        assert before.resolve("250PFC").catalogue_row == after.resolve("250PFC").catalogue_row
        # 250x12FL is a plate, 250PFC is a channel: different keys, no interaction
        assert _normalise("250X12FL") != _normalise("250PFC")

    def test_adding_250x12fl_does_not_resolve_250x90pfc(self, production):
        rows = _live_rows()
        before = _matcher_with(rows, production)
        after = _matcher_with(rows + [_flat_row(PROPOSED_IDENTITIES[2])], production)
        assert before.resolve("250X90PFC").resolution == "NONE"
        assert after.resolve("250X90PFC").resolution == "NONE"

    def test_the_e1_conflict_is_untouched_by_a_250x12fl_row(self):
        from app.engineering_data import source_of_truth as sot

        catalogue_250pfc = [r for r in _live_rows() if r["name"] == "250PFC"][0]
        capture_250x90pfc = {
            "name": "250X90PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
            "flange_thickness": 15.0, "web_thickness": 8.0, "weight_per_metre": 35.5,
            "width": None, "thickness": None, "outside_diameter": None, "leg_size": None,
        }
        verdict = sot.evaluate_section_evidence(capture_250x90pfc, catalogue_250pfc)
        assert verdict.status == "CONFLICT_BLOCKED"
        assert verdict.resolution == "BLOCKED_REVIEW"
        assert verdict.conflicting_fields == (
            ("flange_thickness", 15.0, 12.0),
            ("web_thickness", 8.0, 7.0),
        )
        # 250X12FL is not a geometry-defining field and not the compared row, so
        # it cannot legitimately repair this conflict.
        assert "250X12FL" not in sot.GEOMETRY_DEFINING_FIELDS
        assert _normalise("250X12FL") != _normalise("250X90PFC")
        assert verdict == sot.evaluate_section_evidence(capture_250x90pfc, catalogue_250pfc)


# ===========================================================================
# 5. The CAD consequence — stated, not hidden
# ===========================================================================


class TestCadConsequence:
    def test_flat_resolves_exact_and_the_j0_to_j1_boundary(self, production):
        """
        REWRITTEN BY MILESTONE J1 — deliberately, and reported as such.

        At J0 this test pinned the consequence that blocked Step J: a FLAT
        row resolved EXACT and then raised UnsupportedSectionFamilyError.
        J1 landed the production FLAT -> FL CAD admission, so that specific
        assertion is no longer true and could not be kept without lying
        about the code. What is pinned instead is the part J1 did NOT
        change, which is the part that matters for authority:

          - the resolution is still EXACT and the catalogue row still
            carries the AUTHORITATIVE family `FLAT` (never rewritten);
          - `section_family` on the generated geometry is still `FLAT` —
            J1's projection is a CAD-internal detail, not a new section
            family;
          - `FLAT` was NOT added to PROFILE_BUILDERS (the CAD vocabulary is
            unchanged; nothing was made "supported" by name);
          - a family with neither a builder nor an explicit projection is
            still refused, exactly as before.

        The full J1 proof — the real chain for all four identities, the
        refusals, and the mutation tests — is
        tests/test_real_world_production_flat_cad_admission.py.
        """
        rows = _live_rows()
        proposed = [_flat_row(i) for i in PROPOSED_IDENTITIES]
        matcher = _matcher_with(rows + proposed, production)
        interface = production.interface

        match = matcher.resolve("250X12FL")
        assert match.resolution == "EXACT"
        assert match.catalogue_row["family"] == "FLAT"

        member = interface.ValidatedSteelMember(
            mark="PL028", section=match.catalogue_row["name"], length_mm=155.0,
            material="300", orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted", section_properties=match.catalogue_row,
        )
        geometry = interface.generate_geometry(member)  # J1: now admitted
        assert geometry.section_family == "FLAT"  # ...as the SOURCE family
        assert match.catalogue_row["family"] == "FLAT"  # row never rewritten
        assert "FLAT" not in production.sections.PROFILE_BUILDERS

        refused = interface.ValidatedSteelMember(
            mark="PL029", section="200UC46.2", length_mm=155.0, material="300",
            orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted",
            section_properties={"name": "200UC46.2", "family": "UC", "depth": 200.0},
        )
        with pytest.raises(interface.UnsupportedSectionFamilyError):
            interface.generate_geometry(refused)

    def test_fl_and_pl_would_build_but_are_not_schema_legal(self, production):
        interface = production.interface
        row = _flat_row(PROPOSED_IDENTITIES[2])
        for family in ("FL", "PL"):
            properties = dict(row, family=family)
            member = interface.ValidatedSteelMember(
                mark="PL028", section=row["name"], length_mm=155.0, material="300",
                orientation=None, connection_refs=[], source_refs=[],
                validation_status="extracted", section_properties=properties,
            )
            interface.generate_geometry(member)  # builds
        assert family_decision(ESTABLISHED_FACTS) == RESOLVED_TO_FLAT  # ...and yet


# ===========================================================================
# 6. The matcher-level authority impact, on the genuine live rows
# ===========================================================================


class TestAuthorityImpact:
    def test_the_six_spellings_go_from_none_to_exact(self, production):
        rows = _live_rows()
        before = _matcher_with(rows, production)
        after = _matcher_with(rows + [_flat_row(i) for i in PROPOSED_IDENTITIES], production)
        for spelling, canonical in EXPECTED_CANONICAL.items():
            assert before.resolve(spelling).resolution == "NONE"
            assert before.resolve(spelling).catalogue_row is None
            match = after.resolve(spelling)
            assert match.resolution == "EXACT"
            assert match.catalogue_row is not None
            assert _normalise(match.catalogue_row["name"]) == canonical
            assert match.catalogue_row["family"] == "FLAT"

    def test_the_dangerous_near_match_matrix_is_unchanged(self, production):
        rows = _live_rows()
        before = _matcher_with(rows, production)
        after = _matcher_with(rows + [_flat_row(i) for i in PROPOSED_IDENTITIES], production)
        for token, resolution, name in NEAR_MATCH_MATRIX:
            for matcher, label in ((before, "before"), (after, "after")):
                match = matcher.resolve(token)
                assert match.resolution == resolution, f"{token} {label}: {match.resolution}"
                row = match.catalogue_row
                assert (row or {}).get("name") == name, f"{token} {label}"

    def test_a_stored_row_is_reached_by_every_case_and_space_variant(self, production):
        rows = _live_rows()
        matcher = _matcher_with(rows + [_flat_row(PROPOSED_IDENTITIES[0])], production)
        for variant in ("130X12FL", "130x12FL", " 130 x12fl ", "130X12fl"):
            match = matcher.resolve(variant)
            assert match.resolution == "EXACT"
            assert _normalise(match.catalogue_row["name"]) == "130X12FL"

    def test_the_reference_identity_vocabulary_is_unchanged(self, production):
        section_matcher = production.section_matcher
        matcher = _matcher_with(_live_rows() + [_flat_row(i) for i in PROPOSED_IDENTITIES], production)
        identity = matcher.reference_identity
        assert identity.source_kind == section_matcher.SOURCE_LIVE_SUPABASE
        assert identity.identity_status == section_matcher.IDENTITY_UNVERSIONED
        assert matcher.catalogue_version is None


# ===========================================================================
# 7. Live re-verification — opt-in, and fail-closed when it runs
# ===========================================================================


def _access_token() -> str | None:
    """The genuine credential, resolved but never printed.

    `SUPABASE_ACCESS_TOKEN` if set, else the platform CLI's keychain item.
    Returns None when neither is available; callers decide whether that is a
    skip or a failure.
    """
    token = os.environ.get(ACCESS_TOKEN_ENV)
    if token:
        return token.strip()
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except Exception:
        return None
    if raw.startswith("go-keyring-base64:"):
        import base64
        try:
            raw = base64.b64decode(raw[len("go-keyring-base64:"):]).decode()
        except Exception:
            return None
    return raw or None


def _live_schema_facts(token: str) -> SchemaFacts:
    """Read the live schema via the platform's server-generated types.

    Raises on any failure: under the opt-in this must fail closed, never
    silently report the pinned facts as if they had been re-verified.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"https://api.supabase.com{EVIDENCE_TYPES_ENDPOINT}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:  # pragma: no cover - only on a network/auth failure
        raise AssertionError(
            "the live schema could not be read, so the established facts could not be "
            f"re-verified (fail closed): {type(exc).__name__}"
        ) from exc

    types = payload["types"]

    relation = re.search(r"\n      steel_sections: \{\n(.*?)\n      \}\n", types, re.S)
    assert relation, "steel_sections is missing from the live schema"
    row_block = re.search(r"Row: \{(.*?)\n        \}", relation.group(1), re.S).group(1)
    columns = dict(re.findall(r"^\s+(\w+): (.+)$", row_block, re.M))
    nullable = tuple(sorted(c for c, t in columns.items() if t.endswith("| null")))
    not_null = tuple(sorted(c for c, t in columns.items() if not t.endswith("| null")))

    enum_block = re.search(r"\n      section_family:\n(.*?)\n(?=      \w+:|\n    \})", types, re.S)
    assert enum_block, "section_family is no longer an enum in the live schema"
    family_enum = tuple(re.findall(r'"([^"]+)"', enum_block.group(1)))

    # The types endpoint does not carry the primary key, but it does carry
    # foreign keys — and PostgreSQL only lets a foreign key reference a column
    # that is already unique or a primary key. So an inbound FK onto
    # steel_sections(name) IS live evidence that name is unique.
    inbound = re.findall(
        r'foreignKeyName: "([^"]+)"\s*\n\s*columns: \[([^\]]*)\]\s*\n\s*isOneToOne: (\w+)\s*\n'
        r'\s*referencedRelation: "steel_sections"\s*\n\s*referencedColumns: \[([^\]]*)\]',
        types,
    )
    name_unique = any(
        [c.strip().strip('"') for c in referenced.split(",")] == ["name"]
        for _, _, _, referenced in inbound
    )

    return SchemaFacts(
        inspected=True,
        family_enum=family_enum,
        weight_per_metre_nullable=columns.get("weight_per_metre", "").endswith("| null"),
        name_unique=name_unique or None,
        not_null_columns=not_null,
    )


class TestLiveReverification:
    """Opt-in re-verification of the pinned facts against the live database.

    Off by default so the suite stays hermetic; when switched on, every
    assertion must hold and a missing credential or failed read is a FAILURE,
    never a skip.
    """

    def _facts_or_skip(self) -> SchemaFacts:
        if os.environ.get(LIVE_OPT_IN_ENV) != "1":
            pytest.skip(
                f"live schema re-verification is opt-in: set {LIVE_OPT_IN_ENV}=1 with genuine "
                "credentials to re-read the live schema and re-assert the pinned facts"
            )
        token = _access_token()
        if not token:
            pytest.fail(
                "live re-verification was requested but no credential is available "
                f"({ACCESS_TOKEN_ENV} or the {KEYCHAIN_SERVICE} keychain item) — failing "
                "closed rather than reporting unverified facts as verified"
            )
        return _live_schema_facts(token)

    def test_live_family_enum_still_matches_the_pinned_enum(self):
        facts = self._facts_or_skip()
        assert set(facts.family_enum) == set(SECTION_FAMILY_ENUM)
        assert family_decision(facts) == RESOLVED_TO_FLAT

    def test_live_nullability_still_matches_the_pinned_nullability(self):
        facts = self._facts_or_skip()
        assert facts.weight_per_metre_nullable is True
        assert set(facts.not_null_columns) == set(NOT_NULL_COLUMNS)
        for identity in PROPOSED_IDENTITIES:
            assert classify_identity(identity, facts) != BLOCKED_BY_SCHEMA

    def test_live_metadata_would_change_the_classification_if_it_drifted(self):
        facts = self._facts_or_skip()
        assert classify_identity(PROPOSED_IDENTITIES[0], facts) == READY_FOR_WRITE
        drifted = SchemaFacts(
            True, facts.family_enum, False, facts.name_unique, facts.not_null_columns
        )
        assert classify_identity(PROPOSED_IDENTITIES[0], drifted) == BLOCKED_BY_SCHEMA
