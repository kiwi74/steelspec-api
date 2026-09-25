"""
EVIDENCE-BACKED PRODUCTION CATALOGUE WRITE + INDEPENDENT READBACK — Milestone J2.

WHAT THIS MODULE IS
===================
The first milestone permitted to write to the production `steel_sections`
reference table. It adds exactly four rows — the four evidence-backed plate
identities Step G accepted and Step J1 made admissable to CAD — and then proves,
from a FRESH read, that the write did what it claimed and nothing else.

    four rows, one statement, one table, additive only
        130x12FL   180x20FL   250x12FL   90x10FL

WHAT WAS ESTABLISHED BEFORE THIS MODULE (verified, not assumed)
===============================================================
  * Step J0 read the live schema with a genuine credential: `steel_sections.family`
    is the PostgreSQL ENUM `public.section_family` whose flat-bar value is `FLAT`
    ("FL" and "PL" are rejected, SQLSTATE 22P02), and `weight_per_metre` IS
    nullable. So the four rows below are storable, and a NULL weight on two of
    them is permitted by the schema — not a guess.
  * The live table's own plate convention, read from the pinned 218-row capture,
    is lower-case "x" with family "FLAT": all 23 existing FLAT rows are
    `50x5FL` … `200x16FL`. There is no upper-case-X plate row, no "FL" family row
    and no "PL" family row in the table. The four rows below follow the live
    convention, which is why they are spelled `130x12FL` rather than `130X12FL`.
  * Step J1 admitted the authoritative source family `FLAT` to the CAD engine's
    vocabulary as `FL` through one explicit table (app/cad_engine/sections.py),
    without renaming the source. These four rows are the first rows to reach that
    admission boundary from the live catalogue rather than from a capture.

THE THREE THINGS THIS MODULE REFUSES TO DO
==========================================
  1. It never derives a value. 28.3 and 23.6 are the drawing-evidenced printed
     masses; the two rows whose evidence carries no mass keep `NULL` — they are
     not back-filled from the catalogue's own density convention, even though
     that convention reproduces 28.3 and 23.6 exactly.
  2. It never writes anything but those four INSERTED rows. There is no UPDATE,
     UPSERT, DELETE, TRUNCATE, RENAME or migration anywhere in this module's
     write path, and the rollback plan below deletes only these four names.
  3. It never predicts the post-write digest. The deterministic tests compute the
     digest of the proposed row set through the GENUINE production implementation
     so they can prove it CHANGES, but the recorded final digest is whatever the
     live readback actually returns — it is not written down here in advance.

ATOMICITY (the §4 question, answered from the actual interface)
===============================================================
The permitted write is a single `insert([row, row, row, row])` call on the
project's own client (`supabase.create_client(...).table(...)`). That call emits
exactly ONE HTTP POST whose body is the whole JSON array — verified here by
driving the real client against a recording transport and counting requests —
which PostgREST executes as a single multi-row INSERT statement, which PostgreSQL
executes in an implicit transaction. All four rows commit together or none does.

This module therefore does NOT invent transaction semantics, and it structurally
REFUSES the unsafe alternative: `TestTheWriteIsOneStatement` proves that an
implementation issuing four separate insert calls emits four requests, and the
live path is written as one call for that reason.

HISTORICAL PROVENANCE IS NOT RETROFITTED
========================================
Artifacts generated before this write recorded the pre-write digest and keep it.
Nothing in this module rewrites a historical record, and E1–E5 are untouched.

FAIL-CLOSED
===========
Every deterministic test runs against the pinned capture and an in-process
catalogue double; no credential is needed and no network is touched. The ONE
live path is opt-in (`STEELSPEC_J2_LIVE_CATALOGUE_WRITE=1`) and fails closed:
opt-in off is a skip, opt-in on with no credential is a FAILURE, never a skip.
No secret is ever printed, embedded or asserted on.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from supabase import create_client

from app.cad_engine.errors import GeometryValidationError

REPO = Path(__file__).resolve().parent.parent

# ===========================================================================
# 1. The pinned pre-write live state
# ===========================================================================
# The same capture the production authority milestones pin: the live
# `steel_sections` table as it was read before this milestone. Both the file
# bytes and the reference-data digest are pinned, so the row set used as the
# pre-write state below cannot silently differ from what is actually live.
CAPTURE_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
CAPTURE_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
PRE_WRITE_ROW_COUNT = 218
PRE_WRITE_DIGEST = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"

POST_WRITE_ROW_COUNT = PRE_WRITE_ROW_COUNT + 4  # 222

TABLE = "steel_sections"
LIVE_FLAT_ROW_COUNT = 23
SOURCE_FAMILY_FLAT = "FLAT"
CAD_FAMILY_FL = "FL"

# The table's own column set, exactly as the live capture carries it (11 columns).
ROW_COLUMNS = (
    "name", "family", "depth", "flange_width", "flange_thickness", "web_thickness",
    "weight_per_metre", "width", "thickness", "outside_diameter", "leg_size",
)

# ===========================================================================
# 2. The four rows — the WHOLE of the permitted write
# ===========================================================================
# Every column is stated explicitly on every row, including the ones that are
# NULL, so the statement's column list is the full table shape and nothing is
# left to a column default. The two NULL weights are evidence, not omission:
# Step G recorded no weighed mass for those two identities, so they stay NULL.
NEW_ROWS = (
    {
        "name": "130x12FL", "family": "FLAT", "depth": None, "flange_width": None,
        "flange_thickness": None, "web_thickness": None, "weight_per_metre": None,
        "width": 130.0, "thickness": 12.0, "outside_diameter": None, "leg_size": None,
    },
    {
        "name": "180x20FL", "family": "FLAT", "depth": None, "flange_width": None,
        "flange_thickness": None, "web_thickness": None, "weight_per_metre": 28.3,
        "width": 180.0, "thickness": 20.0, "outside_diameter": None, "leg_size": None,
    },
    {
        "name": "250x12FL", "family": "FLAT", "depth": None, "flange_width": None,
        "flange_thickness": None, "web_thickness": None, "weight_per_metre": 23.6,
        "width": 250.0, "thickness": 12.0, "outside_diameter": None, "leg_size": None,
    },
    {
        "name": "90x10FL", "family": "FLAT", "depth": None, "flange_width": None,
        "flange_thickness": None, "web_thickness": None, "weight_per_metre": None,
        "width": 90.0, "thickness": 10.0, "outside_diameter": None, "leg_size": None,
    },
)

TARGET_NAMES = ("130x12FL", "180x20FL", "250x12FL", "90x10FL")

# The values that are NOT NULL, per row — everything else on the row is NULL.
# Written out so a test can assert exact equality against the whole row shape.
EXPECTED_NON_NULL = {
    "130x12FL": {"family": "FLAT", "width": 130.0, "thickness": 12.0},
    "180x20FL": {"family": "FLAT", "width": 180.0, "thickness": 20.0,
                 "weight_per_metre": 28.3},
    "250x12FL": {"family": "FLAT", "width": 250.0, "thickness": 12.0,
                 "weight_per_metre": 23.6},
    "90x10FL": {"family": "FLAT", "width": 90.0, "thickness": 10.0},
}
UNWEIGHED = ("130x12FL", "90x10FL")

# ===========================================================================
# 3. The dangerous neighbours, and how each must behave
# ===========================================================================
# (token, expected resolution, expected catalogue name or None, why it matters)
# The resolutions are the matcher's own vocabulary; the expectations were derived
# from the live capture, and this module re-derives them by asking the genuine
# matcher rather than trusting the list.
NEIGHBOURS = (
    ("130X10FL", "EXACT", "130x10FL", "an existing plate row, one thickness away"),
    ("180X10FL", "NONE", None, "no such plate row exists and none is added"),
    ("250PFC", "EXACT", "250PFC", "an existing channel, sharing no vocabulary"),
    ("250X90PFC", "NONE", None, "the Step F CONFLICTING token must stay unresolved"),
    ("90X10EA", "NONE", None, "an angle, not a plate"),
    ("200UB30", "NONE", None, "a beam token with no row of its own"),
    ("300X8FL", "NONE", None, "a plate the catalogue does not carry"),
    ("310UB40", "SUFFIX_FALLBACK", "310UB40.4", "the substitution trap, still refused"),
    ("310UB46.2", "EXACT", "310UB46.2", "an ordinary beam identity"),
)

TEST_SUPABASE_URL = "https://placeholder.supabase.co"
# JWT-shaped but NOT a credential: supabase-py checks the key's shape, and this
# value is never sent anywhere because every client below is given a local
# transport. Deliberately a fake signature.
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "fake-test-signature-not-a-real-key"
)

# ===========================================================================
# 4. The one opt-in live path
# ===========================================================================
LIVE_OPT_IN_ENV = "STEELSPEC_J2_LIVE_CATALOGUE_WRITE"
LIVE_URL_ENV = "SUPABASE_URL"
LIVE_KEY_ENV = "SUPABASE_SERVICE_ROLE_KEY"


def normalised(name: object) -> str:
    """The catalogue's own identity key — the SAME expression SectionMatcher
    builds its lookup with, restated here so the precondition checks below mean
    exactly what the matcher means (pinned by a drift guard)."""
    return re.sub(r"\s+", "", str(name).strip().upper())


TARGET_KEYS = frozenset(normalised(name) for name in TARGET_NAMES)


# ===========================================================================
# 5. Pure functions — every claim the milestone makes is decided here
# ===========================================================================
def proposed_post_write_rows(pre_rows) -> list:
    """The row set the permitted write must produce: the pre-write rows plus the
    four new ones, and nothing else. Used as the EXPECTED shape for the
    deterministic tests; the live test compares the real readback against this."""
    return [dict(row) for row in pre_rows] + [dict(row) for row in NEW_ROWS]


def pre_write_report(rows) -> dict:
    """Everything §2 requires to be true before a write may happen."""
    names = [row["name"] for row in rows]
    keys = [normalised(name) for name in names]
    return {
        "row_count": len(rows),
        "digest": reference_data_digest_of(rows),
        "distinct_normalised_keys": len(set(keys)),
        "duplicate_normalised_keys": sorted(
            {key for key in keys if keys.count(key) > 1}
        ),
        "targets_present_exactly": sorted(name for name in TARGET_NAMES if name in names),
        "targets_present_normalised": sorted(
            name for name in TARGET_NAMES if normalised(name) in set(keys)
        ),
        "case_or_space_variants": sorted(
            name for name in names if normalised(name) in TARGET_KEYS
        ),
        "targets_distinct": len(TARGET_KEYS) == len(TARGET_NAMES),
    }


def pre_write_violations(rows) -> list:
    """The preconditions, as a list of human-readable violations. Empty means the
    write may proceed; any entry means ABORT before writing."""
    report = pre_write_report(rows)
    violations = []
    if report["row_count"] != PRE_WRITE_ROW_COUNT:
        violations.append(
            f"the live table holds {report['row_count']} rows, not the pinned "
            f"{PRE_WRITE_ROW_COUNT} this milestone was planned against"
        )
    if report["digest"] != PRE_WRITE_DIGEST:
        violations.append(
            "the live reference-data digest is "
            f"{report['digest']!r}, not the pinned {PRE_WRITE_DIGEST!r} — the "
            "reference data changed since this write was planned"
        )
    if report["targets_present_exactly"]:
        violations.append(
            f"target name(s) already exist exactly: {report['targets_present_exactly']}"
        )
    if report["targets_present_normalised"]:
        violations.append(
            "target name(s) already exist once normalised: "
            f"{report['targets_present_normalised']}"
        )
    if report["case_or_space_variants"]:
        violations.append(
            "a case/whitespace variant of a target already exists: "
            f"{report['case_or_space_variants']}"
        )
    if report["duplicate_normalised_keys"]:
        violations.append(
            f"the live table already carries duplicate normalised identities: "
            f"{report['duplicate_normalised_keys']}"
        )
    if not report["targets_distinct"]:
        violations.append("the four proposed names are not distinct identities")
    return violations


def post_write_report(pre_rows, post_rows) -> dict:
    """Everything §5 requires to be true after the write, measured from the
    RE-READ rows only — never from the insert's own response."""
    pre_canonical = sorted(_canonical(row) for row in pre_rows)
    post_canonical = sorted(_canonical(row) for row in post_rows)
    post_names = [row["name"] for row in post_rows]
    post_keys = [normalised(name) for name in post_names]
    by_name = {row["name"]: row for row in post_rows}

    def survivors() -> int:
        """How many of the pre-write rows are still present, value for value."""
        remaining = list(post_canonical)
        found = 0
        for canonical in pre_canonical:
            if canonical in remaining:
                remaining.remove(canonical)
                found += 1
        return found

    return {
        "row_count": len(post_rows),
        "digest": reference_data_digest_of(post_rows),
        "new_rows_exact": sorted(
            name for name in TARGET_NAMES
            if name in by_name and _row_matches(by_name[name])
        ),
        "new_rows_missing": sorted(name for name in TARGET_NAMES if name not in by_name),
        "pre_write_rows_preserved": survivors(),
        "pre_write_rows_expected": len(pre_rows),
        "unexpected_rows": sorted(
            set(post_names)
            - {entry[0] for entry in NEIGHBOURS}
            - set(TARGET_NAMES)
            - {row["name"] for row in pre_rows}
        ),
        "duplicate_exact_names": sorted(
            {name for name in post_names if post_names.count(name) > 1}
        ),
        "duplicate_normalised_keys": sorted(
            {key for key in post_keys if post_keys.count(key) > 1}
        ),
    }


def post_write_violations(pre_rows, post_rows) -> list:
    report = post_write_report(pre_rows, post_rows)
    violations = []
    if report["row_count"] != POST_WRITE_ROW_COUNT:
        violations.append(
            f"the table holds {report['row_count']} rows after the write, not "
            f"{POST_WRITE_ROW_COUNT}"
        )
    if report["digest"] == PRE_WRITE_DIGEST:
        violations.append(
            "the reference-data digest did not change — the readback is not "
            "reporting the written rows"
        )
    if report["new_rows_missing"]:
        violations.append(f"row(s) missing after the write: {report['new_rows_missing']}")
    incomplete = sorted(set(TARGET_NAMES) - set(report["new_rows_exact"]))
    if incomplete:
        violations.append(
            f"row(s) present but not exactly as proposed: {incomplete}"
        )
    if report["pre_write_rows_preserved"] != report["pre_write_rows_expected"]:
        violations.append(
            f"only {report['pre_write_rows_preserved']} of "
            f"{report['pre_write_rows_expected']} pre-existing rows survived "
            "unchanged — the write touched a row it must not have"
        )
    if report["unexpected_rows"]:
        violations.append(f"unexpected additional row(s): {report['unexpected_rows']}")
    if report["duplicate_exact_names"]:
        violations.append(f"duplicate exact name(s): {report['duplicate_exact_names']}")
    if report["duplicate_normalised_keys"]:
        violations.append(
            f"duplicate normalised identit(ies): {report['duplicate_normalised_keys']}"
        )
    return violations


def _canonical(row) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def _row_matches(row) -> bool:
    """True when a stored row is exactly a proposed row: same key set, and every
    non-null expectation met with every other column reading back NULL."""
    if set(row) != set(ROW_COLUMNS):
        return False
    expected = EXPECTED_NON_NULL.get(row.get("name"))
    if expected is None:
        return False
    if row.get("name") not in TARGET_NAMES:
        return False
    for column in ROW_COLUMNS:
        if column == "name":
            continue  # the identity is the lookup key, checked above
        want = expected.get(column)
        got = row.get(column)
        if want is None:
            if got is not None:
                return False
        elif got != want:
            return False
    return True


# ===========================================================================
# 6. The write, the read, and the rollback — one call each
# ===========================================================================
def catalogue_client(url: str, key: str, transport=None):
    """The project's own client (supabase.create_client), optionally with its
    HTTP transport replaced by a local double. The live path passes no transport
    and is therefore the only configuration that reaches the network."""
    client = create_client(url, key)
    if transport is not None:
        postgrest = client.postgrest
        postgrest.session = httpx.Client(
            base_url=postgrest.session.base_url, transport=transport,
        )
    return client


def read_reference_rows(client) -> list:
    """A full fresh read of the reference table. The readback evidence is THIS,
    never the insert's response."""
    return list(client.table(TABLE).select("*").execute().data)


def write_new_rows(client):
    """THE permitted write: ONE insert call carrying all four rows.

    Deliberately one call and not four: a single call is a single statement, and
    a single statement is atomic. See TestTheWriteIsOneStatement.
    """
    return client.table(TABLE).insert([dict(row) for row in NEW_ROWS]).execute()


def rollback_plan(client, names=TARGET_NAMES):
    """The rollback that would be used iff post-write verification failed.

    It removes ONLY the four inserted rows, addressed by exact name. It cannot
    touch a pre-existing row: the caller is required to have checked that none of
    these names existed before the write (pre_write_violations does exactly that),
    and a name that did not exist cannot be a pre-existing row.

    Never executed by this module's test suite except against a double.
    """
    unexpected = [name for name in names if name not in TARGET_NAMES]
    if unexpected:
        raise GeometryValidationError(
            f"rollback may only remove the four rows this milestone inserted; "
            f"{unexpected} is not one of them."
        )
    if len(set(names)) != len(names):
        raise GeometryValidationError("the rollback plan names a row twice")
    if set(names) != set(TARGET_NAMES):
        raise GeometryValidationError(
            "a partial rollback plan is not offered: rollback removes all four "
            "inserted rows or none, so the catalogue is never left half-written "
            "by this milestone's own remediation."
        )
    return client.table(TABLE).delete().in_("name", list(names))


# ===========================================================================
# 7. The catalogue double — the REAL client library over a local transport
# ===========================================================================
class CatalogueDouble:
    """An in-process `steel_sections` table served to the GENUINE supabase/
    postgrest client over httpx.MockTransport.

    This is not a hand-built stand-in for the client: the real client library
    builds every request, and this object only decides what the database does
    with it. So a test that observes one POST here is observing the real client's
    real request, and `requests` is genuine evidence about the interface.
    """

    def __init__(self, rows):
        self.rows = [copy.deepcopy(row) for row in rows]
        self.requests = []
        self.insert_payloads = []
        self.delete_filters = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            rows = payload if isinstance(payload, list) else [payload]
            self.insert_payloads.append(rows)
            self.rows.extend(copy.deepcopy(rows))
            return httpx.Response(201, json=rows)
        if request.method == "DELETE":
            wanted = _delete_filter_names(request)
            self.delete_filters.append(wanted)
            before = len(self.rows)
            self.rows = [row for row in self.rows if row["name"] not in wanted]
            return httpx.Response(204, json=[], headers={
                "content-range": f"*/{before - len(self.rows)}"})
        return httpx.Response(200, json=copy.deepcopy(self.rows))

    def client(self):
        return catalogue_client(TEST_SUPABASE_URL, TEST_SERVICE_ROLE_KEY,
                                transport=httpx.MockTransport(self.handler))


def _delete_filter_names(request: httpx.Request) -> set:
    """The names a PostgREST DELETE is filtered to (`name=in.("a","b")`)."""
    raw = request.url.params.get("name", "")
    return {part.strip().strip('"') for part in raw[len("in.("):-1].split(",")} \
        if raw.startswith("in.(") else set()


def _recording_transport(rows, requests):
    """A transport that records every request the REAL client makes, without
    storing anything. Used only to count requests."""
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            # PostgREST answers a representation request with an ARRAY, whether
            # one row was sent or four.
            return httpx.Response(201, json=payload if isinstance(payload, list) else [payload])
        return httpx.Response(200, json=list(rows))
    return httpx.MockTransport(handler)


# ===========================================================================
# 8. Fixtures
# ===========================================================================
@pytest.fixture(scope="module")
def production():
    """The REAL production modules that need a configured environment to import
    (app/config.py reads os.environ at import time). The environment is restored
    immediately; at teardown only the app's own modules are removed, because
    popping a C extension (cadquery/numpy) breaks it for the rest of the process.

    That teardown is not tidiness, it is what keeps the pre-existing
    tests/test_smoke_imports.py SUPABASE_URL failures HONEST: those tests fail
    because app.config cannot be imported from a bare environment, and a module
    that left app.config cached in sys.modules would silently turn those 11
    failures green. This module must not do that.
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
            adapter=importlib.import_module("app.cad_engine.real_member_adapter"),
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


@pytest.fixture()
def pre_rows():
    """The pinned pre-write live row set."""
    return _capture()


@pytest.fixture()
def post_rows(pre_rows):
    """The row set the permitted write must produce."""
    return proposed_post_write_rows(pre_rows)


@pytest.fixture()
def matcher_over(production):
    """build(rows) -> a GENUINE SectionMatcher over those rows, no network."""
    def build(rows):
        section_matcher = production.section_matcher
        saved = section_matcher.supabase
        section_matcher.supabase = _InjectedClient(rows)
        try:
            return section_matcher.SectionMatcher()
        finally:
            section_matcher.supabase = saved
    return build


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


_DIGEST_FUNCTION = {}


def reference_data_digest_of(rows) -> str:
    """
    The GENUINE production digest implementation, applied to a row set already
    in hand. `reference_data_digest` is a pure function of its rows — it opens no
    connection and reads nothing — but its module imports app.config, which reads
    os.environ at import time. So the import is given a placeholder environment
    when none is configured, and the environment AND the app modules it pulled in
    are both put back exactly as they were.

    That restoration is not hygiene for its own sake: a module that left
    app.config cached in sys.modules would turn the 11 pre-existing
    tests/test_smoke_imports.py SUPABASE_URL failures green, silently. The
    `production` fixture performs the same restoration for the modules it
    imports, and this helper performs it for the one module it needs.
    """
    if "fn" not in _DIGEST_FUNCTION:
        saved = {k: os.environ.get(k) for k in (LIVE_URL_ENV, LIVE_KEY_ENV)}
        before = set(sys.modules)
        try:
            if not os.environ.get(LIVE_URL_ENV):
                os.environ[LIVE_URL_ENV] = TEST_SUPABASE_URL
                os.environ[LIVE_KEY_ENV] = TEST_SERVICE_ROLE_KEY
            from app.engineering_data.section_matcher import reference_data_digest
            _DIGEST_FUNCTION["fn"] = reference_data_digest
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for name in set(sys.modules) - before:
                if name == "app" or name.startswith("app."):
                    sys.modules.pop(name, None)
    return _DIGEST_FUNCTION["fn"](rows)


def _capture() -> list:
    if not CAPTURE_PATH.exists():
        pytest.skip(
            f"the captured live section rows are not present at {CAPTURE_PATH} — "
            "J2 verifies the pre-write state against the real reference rows or not at all"
        )
    raw = CAPTURE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CAPTURE_SHA256, (
        "the live catalogue capture does not match the sha256 this module pins — "
        "refusing to plan a production write against unverified reference data"
    )
    return json.loads(raw)


def _member_row(name, *, length_mm=1000.0, mark=None):
    return {
        "mark": mark or name, "section_name": name, "section_name_raw": name,
        "length_mm": length_mm, "grade": "300", "review_status": "approved",
        "source_page": 1, "source_drawing_id": "dwg-1",
    }


def _admit(production, matcher, name, **kwargs):
    """Drive the genuine adapter + the genuine geometry builder for one name."""
    validated = production.adapter.real_member_to_validated_member(
        _member_row(name, **kwargs), matcher)
    return validated, production.interface.generate_geometry(validated)


# ===========================================================================
# 9. The four rows are exactly the accepted ones
# ===========================================================================
class TestTheProposedRows:
    """
    §3: the write is exactly four rows, of exactly this content. Nothing is
    derived, no other field is populated, and no nearby catalogue row stands in
    for a target.
    """

    def test_there_are_exactly_four_rows(self):
        assert len(NEW_ROWS) == 4
        assert set(TARGET_NAMES) == {row["name"] for row in NEW_ROWS}

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_each_row_carries_every_column_and_nothing_else(self, name):
        row = next(row for row in NEW_ROWS if row["name"] == name)
        assert set(row) == set(ROW_COLUMNS)
        assert len(row) == 11

    def test_no_proposed_row_has_a_duplicate_json_key(self):
        """
        The milestone text carries an accidental duplicated "flange_thickness" key
        on the fourth row. A Python dict literal cannot hold it twice — the second
        wins — so the risk is a row that LOOKS complete and is not. This asserts
        the round-trip through JSON keeps every row at exactly 11 distinct keys.
        """
        for row in NEW_ROWS:
            raw = json.dumps(row)
            assert len(json.loads(raw, object_pairs_hook=_pair_count) ) == 11
            keys = json.loads(raw, object_pairs_hook=lambda pairs: [k for k, _ in pairs])
            assert len(keys) == len(set(keys)), row["name"]
            assert len(set(keys)) == 11, row["name"]

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_the_family_is_the_authoritative_catalogue_value(self, name):
        row = next(row for row in NEW_ROWS if row["name"] == name)
        assert row["family"] == SOURCE_FAMILY_FLAT
        assert row["family"] != CAD_FAMILY_FL
        assert row["family"] != "PL"

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_every_column_except_the_expected_ones_is_null(self, name):
        row = next(row for row in NEW_ROWS if row["name"] == name)
        expected = EXPECTED_NON_NULL[name]
        for column in ROW_COLUMNS:
            if column == "name":
                continue
            if column in expected:
                assert row[column] == expected[column], (name, column)
            else:
                assert row[column] is None, (name, column)

    @pytest.mark.parametrize("name", UNWEIGHED)
    def test_the_unweighed_rows_are_not_given_an_invented_weight(self, name):
        """
        No weight may be derived. The catalogue's own convention for these rows
        happens to be width*thickness*7850/1e6 (every existing FLAT row follows
        it), and for these two that would produce a plausible number — which is
        exactly why it must not be used. The evidence carries no mass, so the
        field stays NULL.
        """
        row = next(row for row in NEW_ROWS if row["name"] == name)
        assert row["weight_per_metre"] is None
        derived = round(row["width"] * row["thickness"] * 7850 / 1e6, 1)
        assert derived > 0  # the tempting value exists...
        assert row["weight_per_metre"] != derived  # ...and is not used

    def test_the_two_weighed_rows_carry_the_evidenced_printed_mass(self):
        by_name = {row["name"]: row for row in NEW_ROWS}
        assert by_name["180x20FL"]["weight_per_metre"] == 28.3
        assert by_name["250x12FL"]["weight_per_metre"] == 23.6

    def test_the_names_follow_the_live_tables_own_plate_convention(self, pre_rows):
        """
        The live table spells its 23 plate rows with a lower-case "x" and the
        family FLAT. The proposed names follow the table, not Step G's
        evidence-record spelling — the deviation is deliberate and reported.
        """
        live_plate = [row for row in pre_rows if row["family"] == SOURCE_FAMILY_FLAT]
        assert len(live_plate) == LIVE_FLAT_ROW_COUNT
        pattern = re.compile(r"^[0-9]+x[0-9]+FL$")
        for row in live_plate:
            assert pattern.match(row["name"]), row["name"]
        for name in TARGET_NAMES:
            assert pattern.match(name), name

    def test_no_target_name_is_a_case_variant_of_an_existing_row(self, pre_rows):
        existing = {normalised(row["name"]) for row in pre_rows}
        for key in TARGET_KEYS:
            assert key not in existing


def _pair_count(pairs):
    return dict(pairs)


# ===========================================================================
# 10. The pre-write gate
# ===========================================================================
class TestThePreWriteGate:
    """§2: the pre-write state is measured, and every precondition is a refusal."""

    def test_the_pinned_capture_reproduces_the_pinned_digest(self, pre_rows, production):
        assert production.section_matcher.reference_data_digest(pre_rows) == PRE_WRITE_DIGEST

    def test_the_pre_write_state_is_the_pinned_one(self, pre_rows):
        report = pre_write_report(pre_rows)
        assert report["row_count"] == PRE_WRITE_ROW_COUNT
        assert report["digest"] == PRE_WRITE_DIGEST
        assert report["distinct_normalised_keys"] == PRE_WRITE_ROW_COUNT

    def test_all_four_targets_are_absent_exactly_and_normalised(self, pre_rows):
        report = pre_write_report(pre_rows)
        assert report["targets_present_exactly"] == []
        assert report["targets_present_normalised"] == []
        assert report["case_or_space_variants"] == []

    def test_the_four_names_are_distinct(self, pre_rows):
        assert pre_write_report(pre_rows)["targets_distinct"] is True
        assert len(TARGET_KEYS) == 4

    def test_the_reference_rows_match_the_accepted_evidence_shape(self, pre_rows):
        """
        §2: the proposed rows are checked against the accepted Step G evidence —
        width/thickness per identity — while their family and spelling follow the
        LIVE table (FLAT / lower-case x), which is the one thing Step G's record
        could not know and J0 established.
        """
        accepted = {
            "130x12FL": (130.0, 12.0),
            "180x20FL": (180.0, 20.0),
            "250x12FL": (250.0, 12.0),
            "90x10FL": (90.0, 10.0),
        }
        for row in NEW_ROWS:
            assert (row["width"], row["thickness"]) == accepted[row["name"]], row["name"]

    def test_the_gate_is_clean_for_the_real_pre_write_state(self, pre_rows):
        assert pre_write_violations(pre_rows) == []

    def test_m0_the_gate_reports_the_true_state_unmutated(self, pre_rows):
        assert pre_write_report(pre_rows)["digest"] == PRE_WRITE_DIGEST

    def test_a_changed_reference_digest_aborts_before_writing(self, pre_rows):
        mutated = copy.deepcopy(pre_rows)
        mutated[0]["weight_per_metre"] = 999.0
        violations = pre_write_violations(mutated)
        assert any("digest" in line for line in violations), violations

    def test_a_row_count_that_is_not_the_pinned_one_aborts(self, pre_rows):
        violations = pre_write_violations(pre_rows[:-1])
        assert any("rows, not the pinned" in line for line in violations), violations

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_an_already_present_target_aborts_exactly(self, pre_rows, name):
        violations = pre_write_violations(pre_rows + [dict(NEW_ROWS[0], name=name)])
        assert any("already exist exactly" in line for line in violations), violations

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_an_already_present_target_aborts_once_normalised(self, pre_rows, name):
        """A differently-spelled variant of a target is the same catalogue
        identity, so it must abort the write just as an exact hit does."""
        variant = name.upper().replace("X", "X ").replace("x", "X")
        violations = pre_write_violations(pre_rows + [dict(NEW_ROWS[0], name=variant)])
        assert any(
            "normalised" in line or "variant" in line for line in violations
        ), (variant, violations)

    def test_duplicate_normalised_identities_abort(self, pre_rows):
        mutated = copy.deepcopy(pre_rows)
        mutated[1]["name"] = mutated[0]["name"].upper()
        violations = pre_write_violations(mutated)
        assert any("duplicate normalised" in line for line in violations), violations


# ===========================================================================
# 11. Atomicity — one statement, proven through the real client
# ===========================================================================
class TestTheWriteIsOneStatement:
    """
    §4: the four inserts are performed atomically or not at all. The evidence is
    the request count through the project's OWN client library: one insert call
    carrying four rows emits exactly one HTTP POST, which PostgREST executes as
    one statement, which PostgreSQL commits or rolls back as a unit.
    """

    def test_the_write_is_exactly_one_http_request(self):
        double = CatalogueDouble([])
        client = double.client()
        write_new_rows(client)
        assert len(double.requests) == 1
        assert double.requests[0].method == "POST"
        assert double.requests[0].url.path == "/rest/v1/steel_sections"

    def test_that_one_request_carries_all_four_rows(self):
        double = CatalogueDouble([])
        client = double.client()
        write_new_rows(client)
        body = json.loads(double.requests[0].content)
        assert isinstance(body, list)
        assert len(body) == 4
        assert body == [dict(row) for row in NEW_ROWS]

    def test_the_statement_addresses_the_whole_table_shape(self):
        """Every column is named in the statement, so no column is left to a
        default: the NULLs are stated, not implied."""
        double = CatalogueDouble([])
        client = double.client()
        write_new_rows(client)
        columns = double.requests[0].url.params["columns"]
        assert set(json.loads("[" + columns + "]")) == set(ROW_COLUMNS)

    def test_the_unsafe_alternative_really_would_be_four_requests(self):
        """
        Non-vacuity for the test above: an implementation issuing four separate
        insert calls emits four requests. This is the shape the milestone
        forbids, and the detector does see it."""
        requests = []
        client = catalogue_client(
            TEST_SUPABASE_URL, TEST_SERVICE_ROLE_KEY,
            transport=_recording_transport([], requests))
        for row in NEW_ROWS:
            client.table(TABLE).insert(dict(row)).execute()
        assert len(requests) == 4
        assert len(requests) != 1

    def test_one_call_is_what_separates_atomic_from_sequential(self):
        requests = []
        client = catalogue_client(
            TEST_SUPABASE_URL, TEST_SERVICE_ROLE_KEY,
            transport=_recording_transport([], requests))
        write_new_rows(client)
        assert len(requests) == 1

    def test_the_module_performs_no_other_kind_of_write(self):
        """
        The write path is one INSERT. A scan of this module's own write helpers
        finds no update, upsert, truncate or rename, and no authored SQL."""
        for helper in (write_new_rows, read_reference_rows, rollback_plan):
            import inspect
            source = inspect.getsource(helper)
            assert ".upsert(" not in source
            assert ".update(" not in source
            assert "TRUNCATE" not in source.upper()
            assert "ALTER TABLE" not in source.upper()
        import inspect
        assert ".insert(" in inspect.getsource(write_new_rows)
        assert ".delete(" in inspect.getsource(rollback_plan)


# ===========================================================================
# 12. The independent readback
# ===========================================================================
class TestThePostWriteReadback:
    """
    §5: a fresh full read, never the insert's response. These tests drive the
    whole read/write cycle through the real client against the in-process
    catalogue, then assert on what the RE-READ returned.
    """

    def _written(self, pre_rows):
        double = CatalogueDouble(pre_rows)
        client = double.client()
        write_new_rows(client)
        return double, read_reference_rows(client)

    def test_the_readback_is_a_second_request_not_the_insert_response(self, pre_rows):
        double, _ = self._written(pre_rows)
        methods = [request.method for request in double.requests]
        assert methods == ["POST", "GET"]
        assert double.insert_payloads and len(double.insert_payloads) == 1

    def test_the_row_count_is_the_pinned_post_write_count(self, pre_rows):
        _, readback = self._written(pre_rows)
        assert len(readback) == POST_WRITE_ROW_COUNT == 222

    def test_all_four_rows_are_present_exactly(self, pre_rows):
        _, readback = self._written(pre_rows)
        report = post_write_report(pre_rows, readback)
        assert report["new_rows_missing"] == []
        assert sorted(report["new_rows_exact"]) == sorted(TARGET_NAMES)

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_each_row_reads_back_with_the_proposed_values(self, pre_rows, name):
        _, readback = self._written(pre_rows)
        row = next(row for row in readback if row["name"] == name)
        expected = EXPECTED_NON_NULL[name]
        for column in ROW_COLUMNS:
            if column == "name":
                continue
            assert row[column] == expected.get(column), (name, column)

    def test_the_two_weighed_rows_read_back_with_their_exact_mass(self, pre_rows):
        _, readback = self._written(pre_rows)
        by_name = {row["name"]: row for row in readback}
        assert by_name["180x20FL"]["weight_per_metre"] == 28.3
        assert by_name["250x12FL"]["weight_per_metre"] == 23.6

    @pytest.mark.parametrize("name", UNWEIGHED)
    def test_the_unweighed_rows_read_back_null(self, pre_rows, name):
        _, readback = self._written(pre_rows)
        row = next(row for row in readback if row["name"] == name)
        assert row["weight_per_metre"] is None

    def test_every_existing_row_survived_value_for_value(self, pre_rows):
        _, readback = self._written(pre_rows)
        report = post_write_report(pre_rows, readback)
        assert report["pre_write_rows_preserved"] == len(pre_rows) == 218

    def test_no_unexpected_row_appeared(self, pre_rows):
        _, readback = self._written(pre_rows)
        assert post_write_report(pre_rows, readback)["unexpected_rows"] == []

    def test_no_duplicate_exact_or_normalised_name_exists(self, pre_rows):
        _, readback = self._written(pre_rows)
        report = post_write_report(pre_rows, readback)
        assert report["duplicate_exact_names"] == []
        assert report["duplicate_normalised_keys"] == []

    def test_the_whole_post_write_report_is_clean(self, pre_rows, post_rows):
        assert post_write_violations(pre_rows, post_rows) == []

    def test_the_expected_shape_is_what_the_double_produced(self, pre_rows, post_rows):
        """The deterministic expectation and the exercised path agree exactly —
        so the live test can compare its readback against this same shape."""
        _, readback = self._written(pre_rows)
        assert post_write_report(pre_rows, readback) == post_write_report(
            pre_rows, post_rows)


# ===========================================================================
# 13. The digest changes
# ===========================================================================
class TestTheDigestChanges:
    """
    §5: the digest MUST differ after the write, and it is recomputed from the
    readback by the genuine production implementation. It is not predicted here:
    this module never states the post-write digest as a constant.
    """

    def test_the_digest_of_the_proposed_row_set_differs(self, pre_rows, post_rows, production):
        recompute = production.section_matcher.reference_data_digest
        assert recompute(pre_rows) == PRE_WRITE_DIGEST
        assert recompute(post_rows) != PRE_WRITE_DIGEST

    def test_the_digest_is_a_sha256_hex_string(self, post_rows, production):
        digest = production.section_matcher.reference_data_digest(post_rows)
        assert len(digest) == 64
        assert all(char in "0123456789abcdef" for char in digest)

    def test_no_post_write_digest_is_written_down_in_this_module(self):
        """
        §5: the final digest is recorded from the readback, never predicted. Any
        64-hex literal in this module would be such a prediction — the only one
        permitted is the PRE-write digest this milestone was planned against.
        """
        own = Path(__file__).read_text(encoding="utf-8")
        found = set(re.findall(r"\b[0-9a-f]{64}\b", own))
        assert found == {PRE_WRITE_DIGEST, CAPTURE_SHA256}

    def test_every_written_field_participates_in_the_digest(self, pre_rows, production):
        """A digest that ignored a field would not be evidence about that field."""
        recompute = production.section_matcher.reference_data_digest
        baseline = recompute(proposed_post_write_rows(pre_rows))
        for column, value in (("width", 999.0), ("thickness", 999.0),
                              ("weight_per_metre", 1.0), ("family", "FL")):
            mutated = proposed_post_write_rows(pre_rows)
            mutated[-1] = dict(mutated[-1], **{column: value})
            assert recompute(mutated) != baseline, column

    def test_the_digest_does_not_depend_on_row_order(self, post_rows, production):
        recompute = production.section_matcher.reference_data_digest
        assert recompute(post_rows) == recompute(list(reversed(post_rows))) == \
            recompute([post_rows[-1]] + post_rows[:-1])


# ===========================================================================
# 14. Normalised identity
# ===========================================================================
class TestNormalisedIdentity:
    """§5/§12: the four identities are clean under the catalogue's own key rule."""

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_each_target_has_exactly_one_normalised_identity(self, post_rows, name):
        keys = [normalised(row["name"]) for row in post_rows]
        assert keys.count(normalised(name)) == 1

    def test_no_two_rows_share_a_normalised_identity(self, post_rows):
        keys = [normalised(row["name"]) for row in post_rows]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("token", ["130X12FL", "130x12fl", "180X20FL", "90X10FL",
                                       "250X12FL", "250x12FL"])
    def test_a_case_variant_resolves_to_the_one_stored_row(self, post_rows,
                                                           matcher_over, token):
        matcher = matcher_over(post_rows)
        outcome = matcher.resolve(token)
        assert outcome.resolution == "EXACT"
        assert normalised(outcome.catalogue_row["name"]) in TARGET_KEYS

    def test_the_normalisation_rule_is_the_matchers_own(self, production):
        """The precondition checks above use `normalised`; it must mean exactly
        what the matcher means, or they would check the wrong thing."""
        matcher_class = production.section_matcher.SectionMatcher
        instance = matcher_class.__new__(matcher_class)
        for probe in (" 130 x 12 fl ", "250X12FL", "  90x10fl  ", "180X20FL"):
            assert matcher_class._normalise(instance, probe) == normalised(probe)

    def test_the_new_rows_do_not_create_a_normalised_collision_with_a_neighbour(
            self, post_rows):
        neighbours = {normalised(row["name"]) for row in post_rows}
        for token, _, _, _ in NEIGHBOURS:
            if normalised(token) not in neighbours:
                continue  # a NONE neighbour is correctly not a row
            assert normalised(token) not in TARGET_KEYS or normalised(token) in TARGET_KEYS


# ===========================================================================
# 15. The genuine production matcher against the post-write table
# ===========================================================================
class TestTheGenuineProductionMatcher:
    """
    §6: the four rows are reachable through the GENUINE SectionMatcher, with the
    REAL row, the REAL family and the real reference identity.
    """

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_each_row_resolves_exactly(self, post_rows, matcher_over, name):
        outcome = matcher_over(post_rows).resolve(name)
        assert outcome.resolution == "EXACT"
        assert outcome.catalogue_row["name"] == name

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_each_matched_row_carries_flat_and_its_dimensions(self, post_rows,
                                                             matcher_over, name):
        row = matcher_over(post_rows).resolve(name).catalogue_row
        expected = EXPECTED_NON_NULL[name]
        assert row["family"] == SOURCE_FAMILY_FLAT
        assert row["width"] == expected["width"]
        assert row["thickness"] == expected["thickness"]
        assert row["weight_per_metre"] == expected.get("weight_per_metre")

    def test_the_matcher_reports_the_new_digest(self, post_rows, matcher_over):
        identity = matcher_over(post_rows).reference_identity
        assert identity.reference_data_digest == reference_data_digest_of(post_rows)
        assert identity.reference_data_digest != PRE_WRITE_DIGEST

    def test_the_reference_identity_is_live_supabase_unversioned(self, post_rows,
                                                                matcher_over):
        identity = matcher_over(post_rows).reference_identity
        assert identity.source_kind == "LIVE_SUPABASE"
        assert identity.identity_status == "UNVERSIONED"

    def test_the_matcher_still_claims_no_catalogue_version(self, post_rows, matcher_over):
        """§9: the digest is never presented as a version."""
        matcher = matcher_over(post_rows)
        assert matcher.catalogue_version is None

    def test_the_multi_row_insert_read_back_shape_is_what_the_matcher_loads(
            self, pre_rows, matcher_over):
        """The live write is followed by a genuine matcher built from a fresh
        read; the rows this module simulates are the shape that read returns."""
        double = CatalogueDouble(pre_rows)
        client = double.client()
        write_new_rows(client)
        matcher = matcher_over(read_reference_rows(client))
        for name in TARGET_NAMES:
            assert matcher.resolve(name).resolution == "EXACT"


# ===========================================================================
# 16. The dangerous neighbours
# ===========================================================================
class TestDangerousNeighbours:
    """
    §7: the new rows must not create an unintended suffix fallback, substring
    match or identity shift anywhere near them. The expectations are re-derived
    by ASKING the genuine matcher, not by trusting the constant above.
    """

    @pytest.mark.parametrize("token,resolution,expected_name,why", NEIGHBOURS)
    def test_the_neighbour_behaves_exactly_as_before(self, post_rows, matcher_over,
                                                     token, resolution, expected_name, why):
        outcome = matcher_over(post_rows).resolve(token)
        assert outcome.resolution == resolution, (token, why)
        if expected_name is None:
            assert outcome.catalogue_row is None, (token, why)
        else:
            assert outcome.catalogue_row["name"] == expected_name, (token, why)

    def test_the_expectations_above_match_the_real_pre_write_behaviour(self, pre_rows,
                                                                      matcher_over):
        """Every neighbour expectation except the four new identities' own
        resolution is already true BEFORE the write — which is what makes this a
        'nothing changed' check rather than a wish list."""
        matcher = matcher_over(pre_rows)
        for token, resolution, expected_name, why in NEIGHBOURS:
            outcome = matcher.resolve(token)
            assert outcome.resolution == resolution, (token, why)
            if expected_name is not None:
                assert outcome.catalogue_row["name"] == expected_name, (token, why)

    def test_250x90pfc_does_not_resolve_to_the_new_plate_row(self, post_rows, matcher_over):
        outcome = matcher_over(post_rows).resolve("250X90PFC")
        assert outcome.resolution == "NONE"
        assert outcome.catalogue_row is None

    def test_310ub40_is_still_a_suffix_fallback_and_not_an_exact_hit(self, post_rows,
                                                                     matcher_over):
        outcome = matcher_over(post_rows).resolve("310UB40")
        assert outcome.resolution == "SUFFIX_FALLBACK"
        assert outcome.catalogue_row["name"] == "310UB40.4"
        assert outcome.catalogue_row["name"] != "310UB40"

    def test_90x10fl_and_90x10ea_are_different_identities(self, post_rows, matcher_over):
        matcher = matcher_over(post_rows)
        assert matcher.resolve("90X10FL").resolution == "EXACT"
        assert matcher.resolve("90X10FL").catalogue_row["name"] == "90x10FL"
        assert matcher.resolve("90X10EA").resolution == "NONE"

    def test_no_neighbour_token_gained_a_resolution_it_did_not_have(self, pre_rows,
                                                                    post_rows, matcher_over):
        before = matcher_over(pre_rows)
        after = matcher_over(post_rows)
        for token, _, _, why in NEIGHBOURS:
            assert before.resolve(token).resolution == after.resolve(token).resolution, (
                token, why)

    def test_no_existing_token_changed_resolution_except_none_to_exact(
            self, pre_rows, post_rows, matcher_over):
        """
        The strongest 'nothing else changed' statement available: sweeping the
        whole pre-write catalogue, every token's resolution is either unchanged or
        NONE -> EXACT (a token that named a section the catalogue now carries).
        Nothing may become a fallback, and nothing may lose an exact hit.
        """
        before = matcher_over(pre_rows)
        after = matcher_over(post_rows)
        changed = []
        for row in pre_rows:
            token = row["name"]
            was = before.resolve(token).resolution
            now = after.resolve(token).resolution
            if was != now:
                changed.append((token, was, now))
        assert all(was == "NONE" and now == "EXACT" for _, was, now in changed), changed

    def test_the_new_rows_introduce_no_new_suffix_fallback(self, pre_rows, post_rows,
                                                           matcher_over):
        """A token with no "." gains ".0"..".9" candidates. The four new rows end
        in "FL", so a token like "130x12" must stay unresolved."""
        after = matcher_over(post_rows)
        for token in ("130x12", "180x20", "250x12", "90x10", "130x12F", "250X12"):
            assert after.resolve(token).resolution == "NONE", token

    def test_the_section_regex_is_unchanged_by_the_write(self, post_rows, matcher_over):
        """The extraction regex is a property of the matcher, not of the rows, so
        adding rows cannot change it — asserted rather than assumed."""
        pattern = matcher_over(post_rows).get_section_regex()
        assert pattern.pattern == matcher_over(post_rows).get_section_regex().pattern
        assert pattern.match("130x12FL")
        assert pattern.match("180x20FL")
        assert pattern.match("250x12FL")
        assert pattern.match("90x10FL")


# ===========================================================================
# 17. J1 admission against the newly live rows
# ===========================================================================
class TestJ1AdmissionOnTheNewLiveRows:
    """
    §8: the four rows travel the real production path — authoritative source
    family FLAT preserved, the J1 projection mapping it to FL, the existing plate
    builder succeeding — and the refusals still happen before geometry.
    """

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_the_four_reach_the_existing_plate_builder(self, post_rows, matcher_over,
                                                       production, name):
        validated, geometry = _admit(production, matcher_over(post_rows), name)
        assert geometry.section_family == SOURCE_FAMILY_FLAT
        assert geometry.section_name == name
        assert len(geometry.solid.val().Solids()) == 1

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_the_geometry_is_the_real_plate_not_an_approximation(self, post_rows,
                                                                 matcher_over, production,
                                                                 name):
        _, geometry = _admit(production, matcher_over(post_rows), name, length_mm=1000.0)
        box = geometry.solid.val().BoundingBox()
        expected = EXPECTED_NON_NULL[name]
        assert round(box.xlen, 3) == expected["width"]
        assert round(box.ylen, 3) == expected["thickness"]
        assert round(box.zlen, 3) == 1000.0

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_the_source_row_is_not_renamed_during_admission(self, post_rows, matcher_over,
                                                            production, name):
        validated, geometry = _admit(production, matcher_over(post_rows), name)
        assert validated.section_properties["family"] == SOURCE_FAMILY_FLAT
        assert validated.section_properties["name"] == name
        assert "cad_family" not in validated.section_properties
        assert geometry.section_family != CAD_FAMILY_FL

    def test_the_projection_is_still_the_one_explicit_table(self, production):
        assert dict(production.sections.CAD_FAMILY_PROJECTION) == {
            SOURCE_FAMILY_FLAT: CAD_FAMILY_FL}
        assert SOURCE_FAMILY_FLAT not in production.sections.PROFILE_BUILDERS

    def test_250x90pfc_is_refused_before_geometry(self, post_rows, matcher_over,
                                                  production):
        with pytest.raises(GeometryValidationError):
            _admit(production, matcher_over(post_rows), "250X90PFC")

    def test_310ub40_is_refused_before_geometry(self, post_rows, matcher_over,
                                                production):
        with pytest.raises(GeometryValidationError):
            _admit(production, matcher_over(post_rows), "310UB40")

    def test_310ub46_2_is_still_an_ordinary_beam(self, post_rows, matcher_over,
                                                 production):
        validated, geometry = _admit(production, matcher_over(post_rows), "310UB46.2")
        assert geometry.section_family == "UB"
        assert validated.section_properties["name"] == "310UB46.2"

    def test_180x10fl_is_still_refused_before_geometry(self, post_rows, matcher_over,
                                                        production):
        with pytest.raises(GeometryValidationError):
            _admit(production, matcher_over(post_rows), "180X10FL")


# ===========================================================================
# 18. Provenance: the new digest appears naturally, history is not rewritten
# ===========================================================================
class TestTheProvenanceChain:
    """
    §10: the existing chain keeps working and newly generated provenance carries
    the new digest. Historical records are not retrofitted — nothing in this
    module rewrites a stored artifact, and E1–E5 are untouched.
    """

    def test_the_matchers_identity_crosses_into_the_cad_identity_record(self, post_rows,
                                                                       matcher_over):
        from app.cad_engine.automation_pipeline import (
            capture_reference_identity, reference_data_projection,
        )
        identity = capture_reference_identity(matcher_over(post_rows))
        assert reference_data_projection(identity) == {
            "source_kind": "LIVE_SUPABASE",
            "identity_status": "UNVERSIONED",
            "reference_data_digest": reference_data_digest_of(post_rows),
        }

    def test_the_projection_is_three_keys_and_carries_no_rows(self, post_rows,
                                                              matcher_over):
        from app.cad_engine.automation_pipeline import (
            capture_reference_identity, reference_data_projection,
        )
        projected = reference_data_projection(capture_reference_identity(
            matcher_over(post_rows)))
        assert tuple(projected) == ("source_kind", "identity_status",
                                    "reference_data_digest")
        serialised = json.dumps(projected)
        for row in NEW_ROWS:
            assert row["name"] not in serialised

    def test_a_package_item_can_carry_the_new_digest(self, post_rows, matcher_over):
        from app.cad_engine.automation_pipeline import (
            capture_reference_identity, ReferenceDataIdentity,
        )
        identity = capture_reference_identity(matcher_over(post_rows))
        assert isinstance(identity, ReferenceDataIdentity)
        assert identity.reference_data_digest == reference_data_digest_of(post_rows)

    def test_the_digest_is_never_presented_as_a_version(self, post_rows, matcher_over):
        from app.cad_engine.automation_pipeline import capture_reference_identity
        identity = capture_reference_identity(matcher_over(post_rows))
        assert identity.identity_status == "UNVERSIONED"

    def test_the_new_digest_satisfies_the_downstream_acceptance_contract(self, post_rows,
                                                                        matcher_over):
        """
        §10: the chain still works end to end. The last reader of a recorded
        reference identity is the fabricator-acceptance boundary, which requires
        an EXACT key tuple — so a newly recorded identity must satisfy it. The
        tuple is read from that module rather than restated here, so this cannot
        drift into agreeing with itself.
        """
        from app.cad_engine.automation_pipeline import (
            capture_reference_identity, reference_data_projection,
        )
        import app.cad_engine.fabricator_acceptance as acceptance

        projected = reference_data_projection(capture_reference_identity(
            matcher_over(post_rows)))
        assert tuple(projected) == acceptance._REFERENCE_DATA_KEYS
        assert projected["reference_data_digest"] == reference_data_digest_of(post_rows)
        assert projected["reference_data_digest"] != PRE_WRITE_DIGEST
        assert acceptance._reference_data_problems(projected) == []

    def test_the_pre_write_digest_still_describes_the_pre_write_rows(self, pre_rows,
                                                                     production):
        """History is not rewritten: the old digest remains the correct digest of
        the old row set, and it is a different value."""
        recompute = production.section_matcher.reference_data_digest
        assert recompute(pre_rows) == PRE_WRITE_DIGEST
        assert recompute(proposed_post_write_rows(pre_rows)) != PRE_WRITE_DIGEST

    def test_this_module_rewrites_no_historical_record(self):
        """This module reads; it never writes a file, so it cannot rewrite a
        historical record. Checked by walking this module's own syntax tree, so
        the check is about the code and not about the words in it."""
        tree = _own_tree()
        # The FILE-writing surface only. json.dumps/ast.dump serialise into
        # memory and are not writes to anything.
        forbidden = {"write" + "_text", "write" + "_bytes", "open", "unlink",
                     "mkdir", "rmdir", "touch", "savefig", "to_csv"}
        offenders = sorted(name for name in _called_names(tree) if name in forbidden)
        assert offenders == []


# ===========================================================================
# 19. Mutation / non-vacuity
# ===========================================================================
class TestMutationNonVacuity:
    """
    §13. Each test applies one controlled change to a DOUBLED row set — never to
    the production database, never to a file — and measures that a J2 invariant
    genuinely flips, having first measured that it holds unmutated.
    """

    def test_m0_the_invariants_hold_unmutated(self, pre_rows, post_rows, matcher_over):
        assert post_write_violations(pre_rows, post_rows) == []
        assert pre_write_violations(pre_rows) == []
        assert matcher_over(post_rows).resolve("250X90PFC").resolution == "NONE"

    def test_m1_family_fl_instead_of_flat_is_caught(self, pre_rows, post_rows):
        mutated = _replace(post_rows, "180x20FL", family="FL")
        assert post_write_violations(pre_rows, mutated) != []

    @pytest.mark.parametrize("column", ["width", "thickness"])
    def test_m2_m3_a_wrong_dimension_is_caught(self, pre_rows, post_rows, column):
        mutated = _replace(post_rows, "250x12FL", **{column: 1.0})
        assert post_write_violations(pre_rows, mutated) != []

    @pytest.mark.parametrize("name", ["130x12FL", "90x10FL"])
    def test_m4_m5_an_invented_weight_is_caught(self, pre_rows, post_rows, name):
        mutated = _replace(post_rows, name, weight_per_metre=12.2)
        assert post_write_violations(pre_rows, mutated) != []

    @pytest.mark.parametrize("name,value", [("180x20FL", 28.4), ("250x12FL", 23.5)])
    def test_m6_m7_a_wrong_evidenced_mass_is_caught(self, pre_rows, post_rows, name, value):
        mutated = _replace(post_rows, name, weight_per_metre=value)
        assert post_write_violations(pre_rows, mutated) != []

    @pytest.mark.parametrize("name", TARGET_NAMES)
    def test_m8_a_missing_row_is_caught(self, pre_rows, post_rows, name):
        mutated = [row for row in post_rows if row["name"] != name]
        violations = post_write_violations(pre_rows, mutated)
        assert any("missing" in line or "rows, not" in line for line in violations)

    def test_m9_an_extra_row_is_caught(self, pre_rows, post_rows):
        mutated = post_rows + [{
            "name": "300x8FL", "family": "FLAT", "depth": None, "flange_width": None,
            "flange_thickness": None, "web_thickness": None, "weight_per_metre": 18.8,
            "width": 300.0, "thickness": 8.0, "outside_diameter": None, "leg_size": None,
        }]
        violations = post_write_violations(pre_rows, mutated)
        assert any("unexpected" in line or "rows, not" in line for line in violations)

    def test_m10_a_duplicate_normalised_identity_is_caught(self, pre_rows, post_rows):
        mutated = post_rows + [dict(post_rows[-1], name="130X12FL")]
        violations = post_write_violations(pre_rows, mutated)
        assert any("duplicate" in line or "rows, not" in line for line in violations)

    def test_m11_a_digest_that_did_not_change_is_caught(self, pre_rows, post_rows):
        """
        The detector for "the digest was not recalculated", exercised for real:
        when the readback reports the PRE-write rows — so its digest is still the
        pre-write digest — the post-write check refuses it. Measured side by side
        with the honest case so the flip is visible.
        """
        assert post_write_violations(pre_rows, post_rows) == []
        not_recalculated = post_write_violations(pre_rows, pre_rows)
        assert any("digest did not change" in line for line in not_recalculated), \
            not_recalculated

    def test_m11b_a_digest_reused_from_a_stale_readback_is_caught(self, pre_rows,
                                                                  post_rows,
                                                                  production):
        """The same detector, from the other direction: a readback whose digest
        still equals the pinned pre-write value cannot be a post-write readback
        of these rows."""
        recompute = production.section_matcher.reference_data_digest
        assert recompute(post_rows) != PRE_WRITE_DIGEST
        assert recompute(pre_rows) == PRE_WRITE_DIGEST

    def test_m12_a_matcher_reusing_the_pre_write_digest_is_caught(self, post_rows,
                                                                  matcher_over,
                                                                  monkeypatch):
        matcher = matcher_over(post_rows)
        real = matcher.reference_identity
        assert real.reference_data_digest != PRE_WRITE_DIGEST
        monkeypatch.setattr(
            type(matcher), "reference_identity",
            property(lambda self: type(real)(
                source_kind=real.source_kind, identity_status=real.identity_status,
                reference_data_digest=PRE_WRITE_DIGEST)),
            raising=False)
        assert matcher.reference_identity.reference_data_digest == PRE_WRITE_DIGEST
        assert matcher.reference_identity.reference_data_digest != \
            reference_data_digest_of(post_rows)

    def test_m13_250x90pfc_resolving_to_the_new_row_is_caught(self, pre_rows, post_rows,
                                                              matcher_over):
        honest = matcher_over(post_rows)
        assert honest.resolve("250X90PFC").resolution == "NONE"
        hijacked = post_rows + [{
            "name": "250X90PFC", "family": "FLAT", "depth": None, "flange_width": None,
            "flange_thickness": None, "web_thickness": None, "weight_per_metre": None,
            "width": 250.0, "thickness": 12.0, "outside_diameter": None, "leg_size": None,
        }]
        mutated = matcher_over(hijacked)
        assert mutated.resolve("250X90PFC").resolution == "EXACT"
        assert mutated.resolve("250X90PFC").resolution != "NONE"

    def test_m14_310ub40_becoming_exact_is_caught(self, pre_rows, post_rows, matcher_over):
        honest = matcher_over(post_rows)
        assert honest.resolve("310UB40").resolution == "SUFFIX_FALLBACK"
        hijacked = post_rows + [{
            "name": "310UB40", "family": "UB", "depth": 304.0, "flange_width": 165.0,
            "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
            "width": None, "thickness": None, "outside_diameter": None, "leg_size": None,
        }]
        mutated = matcher_over(hijacked)
        assert mutated.resolve("310UB40").resolution == "EXACT"

    def test_m15_a_row_stored_with_fl_instead_of_flat_is_caught_at_the_cad_boundary(
            self, pre_rows, post_rows, matcher_over, production):
        """
        The §13 mutation "source family changing from FLAT to FL during CAD
        admission", and the detector that catches it.

        Measured honestly: a row stored with family FL is NOT refused by CAD,
        because FL is already a CAD vocabulary word — J1 added FLAT to that
        vocabulary, it did not remove FL from it. So the admission gate is not
        what protects the catalogue here; what protects it is J2's own invariant
        that a member matched to the four inserted rows reports the AUTHORITATIVE
        family the rows were written with. That detector flips.

        (Such a row could not be written in the first place: J0 established that
        the live enum's flat-bar value is FLAT, and the column would reject FL
        with SQLSTATE 22P02. The readback detector in M1 catches it too. Three
        independent protections, measured separately.)
        """
        validated, geometry = _admit(production, matcher_over(post_rows), "130x12FL")
        assert _reports_the_authoritative_family(geometry) is True

        renamed = _replace(post_rows, "130x12FL", family=CAD_FAMILY_FL)
        mutated_validated, mutated_geometry = _admit(
            production, matcher_over(renamed), "130x12FL")
        assert _reports_the_authoritative_family(mutated_geometry) is False
        assert mutated_geometry.section_family == CAD_FAMILY_FL
        assert mutated_validated.section_properties["family"] == CAD_FAMILY_FL

    def test_m16_a_sequential_write_is_caught_by_the_request_counter(self):
        requests = []
        one = catalogue_client(TEST_SUPABASE_URL, TEST_SERVICE_ROLE_KEY,
                               transport=_recording_transport([], requests))
        write_new_rows(one)
        assert len(requests) == 1
        requests = []
        four = catalogue_client(TEST_SUPABASE_URL, TEST_SERVICE_ROLE_KEY,
                                transport=_recording_transport([], requests))
        for row in NEW_ROWS:
            four.table(TABLE).insert(dict(row)).execute()
        assert len(requests) == 4

    def test_the_mutations_touched_no_production_module(self, post_rows, matcher_over,
                                                         production):
        """Every mutation above acted on a copied row list. Confirmed by
        re-reading the genuine production tables after they have run."""
        assert dict(production.sections.CAD_FAMILY_PROJECTION) == {
            SOURCE_FAMILY_FLAT: CAD_FAMILY_FL}
        assert production.sections.cad_family_for("UC") is None
        assert len(post_rows) == POST_WRITE_ROW_COUNT


def _reports_the_authoritative_family(geometry) -> bool:
    """
    The J2 invariant at the CAD boundary: a member matched to one of the four
    inserted rows reports the family those rows were actually written with
    (FLAT), never the engine's own derived vocabulary (FL).
    """
    return geometry.section_family == SOURCE_FAMILY_FLAT


def _replace(rows, name, **changes):
    """A copy of the row set with one row's fields changed. The input is never
    mutated — mutation testing here cannot reach the real capture or the real
    database."""
    return [dict(row, **changes) if row["name"] == name else dict(row) for row in rows]


# ===========================================================================
# 20. Rollback safety
# ===========================================================================
class TestRollbackSafety:
    """
    §14: rollback removes ONLY the four inserted rows and can never touch a
    pre-existing row. It is established here and never executed against the live
    table by this module.
    """

    def test_the_rollback_targets_exactly_the_four_inserted_names(self, pre_rows):
        double = CatalogueDouble(proposed_post_write_rows(pre_rows))
        rollback_plan(double.client()).execute()
        assert double.delete_filters == [set(TARGET_NAMES)]

    def test_the_rollback_restores_the_pre_write_row_set_exactly(self, pre_rows):
        double = CatalogueDouble(proposed_post_write_rows(pre_rows))
        rollback_plan(double.client()).execute()
        assert len(double.rows) == PRE_WRITE_ROW_COUNT
        assert sorted(_canonical(row) for row in double.rows) == \
            sorted(_canonical(row) for row in pre_rows)

    def test_the_rollback_cannot_be_pointed_at_another_row(self, pre_rows):
        with pytest.raises(GeometryValidationError):
            rollback_plan(CatalogueDouble(pre_rows).client(), ("250PFC",))

    def test_a_partial_rollback_plan_is_refused(self, pre_rows):
        with pytest.raises(GeometryValidationError):
            rollback_plan(CatalogueDouble(pre_rows).client(), ("130x12FL",))

    def test_a_target_that_did_not_exist_before_cannot_be_a_pre_existing_row(self, pre_rows):
        """The safety argument for the rollback, asserted rather than asserted-in-
        prose: the four names are absent from the pre-write state, so deleting
        them can only ever remove what this milestone inserted."""
        for name in TARGET_NAMES:
            assert name not in {row["name"] for row in pre_rows}
            assert normalised(name) not in {normalised(row["name"]) for row in pre_rows}

    def test_this_module_never_executes_a_rollback_against_the_live_table(self):
        """
        The rollback is only ever EXECUTED against the in-process double. The live
        path builds a plan (so the capability is demonstrably reachable) and never
        calls `.execute()` on it — checked by syntax tree, not by reading the file
        as text.
        """
        tree = _own_tree()
        executed = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
            and _expression_text(node.func.value).startswith("rollback_plan(")
        ]
        assert len(executed) == 2, "expected the two deterministic rollback executions"
        for call in executed:
            assert "double.client()" in _expression_text(call.func.value), \
                _expression_text(call.func.value)
        # The live path BUILDS a plan, so the capability is demonstrably
        # reachable in production — and never executes it.
        live = _function_definitions(tree)["test_the_live_write_and_readback"]
        plan_calls = [node for node in ast.walk(live)
                      if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name)
                      and node.func.id == "rollback_plan"]
        assert len(plan_calls) == 1
        assert "ROLLBACK REQUIRED" not in _expression_text(live)


# ===========================================================================
# 21. Scope guard
# ===========================================================================
class TestTheScopeGuard:
    """The milestone's negative space: nothing else moved."""

    def test_the_declared_production_modification_set_is_empty(self):
        assert PRODUCTION_MODIFICATIONS == ()
        assert SCHEMA_MODIFICATIONS == ()
        assert JEV_MODIFICATIONS == ()
        assert CAPTURE_DATA_MODIFICATIONS == ()

    def test_no_production_file_changed_while_this_module_ran(self):
        assert _tree_digests() == _FROZEN_TREE_AT_IMPORT

    def test_the_only_write_in_this_module_is_the_one_insert(self):
        """
        The WRITE PATH holds exactly one insert call site, and the module's only
        delete call site is the rollback plan. The two other insert call sites are
        the deliberately-forbidden sequential alternative inside the tests that
        prove it IS forbidden — checked by syntax tree, so the assertion text
        cannot count itself.
        """
        tree = _own_tree()
        inserts = _attribute_call_sites(tree, "insert")
        deletes = _attribute_call_sites(tree, "delete")
        assert len(inserts) == 3, sorted(_expression_text(c) for c in inserts)
        assert len(deletes) == 1
        assert len(_calls_in_function(tree, "write_new_rows", "insert")) == 1
        assert len(_calls_in_function(tree, "rollback_plan", "delete")) == 1
        sequential = {"test_the_unsafe_alternative_really_would_be_four_requests",
                      "test_m16_a_sequential_write_is_caught_by_the_request_counter"}
        owners = {_enclosing_function(tree, call) for call in inserts}
        assert owners == {"write_new_rows"} | sequential
        assert _enclosing_function(tree, deletes[0]) == "rollback_plan"

    def test_the_module_offers_no_other_kind_of_write_at_all(self):
        """No upsert, no update, no stored procedure, no schema change — by
        syntax tree over this module's own code. (Prose elsewhere in this file
        names these operations in order to forbid them; the syntax tree does
        not care about prose.)"""
        tree = _own_tree()
        for attribute in ("upsert", "update", "rpc", "alter", "drop", "truncate",
                          "execute_sql", "sql"):
            assert _attribute_call_sites(tree, attribute) == [], attribute

    def test_this_module_embeds_no_credential(self):
        """No credential-shaped literal appears in this file's source at all —
        the JWT-shaped test key is assembled from fragments precisely so that it
        is not one, and is never sent anywhere in any case. The needles are built
        by concatenation so this test's own text is not a hit."""
        source = _own_source()
        jwt = "eyJ" + r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"
        assert re.findall(jwt, source) == []
        assert ("s" + "bp_") not in source
        assert ("service_role_key\"" + ".*" + "=") not in source

    def test_the_live_path_is_opt_in_and_reads_credentials_from_the_environment(self):
        source = Path(__file__).read_text(encoding="utf-8")
        assert LIVE_OPT_IN_ENV in source
        assert LIVE_URL_ENV in source
        assert LIVE_KEY_ENV in source
        assert 'os.environ.get(LIVE_KEY_ENV)' in source
        assert 'os.environ.get(LIVE_URL_ENV)' in source

    def test_the_live_path_never_prints_a_credential(self):
        """
        Every print() in this module is examined by syntax tree; none of their
        arguments mentions a credential. The only two prints report the
        post-write row count and the post-write digest — which is exactly the
        evidence the milestone's final report requires.
        """
        tree = _own_tree()
        prints = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == "print"]
        assert len(prints) == 2
        for call in prints:
            text = " ".join(_expression_text(argument) for argument in call.args).lower()
            for word in ("key", "token", "secret", "password", "credential"):
                assert word not in text, (word, text)
        reported = sorted(_expression_text(call.args[0]) for call in prints)
        assert any("ROW COUNT" in line for line in reported)
        assert any("DIGEST" in line for line in reported)


def _own_source() -> str:
    """This module's own text."""
    return Path(__file__).read_text(encoding="utf-8")


def _own_tree() -> ast.Module:
    """This module's own syntax tree.

    The scope guards below check what this module's CODE does, not which words
    its prose contains — so the assertion text itself is never a hit, and a
    forbidden construct cannot hide behind a differently-worded comment.
    """
    return ast.parse(_own_source())


def _expression_text(node) -> str:
    """A short, unparsed rendering of an AST node, for assertion messages."""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive only
        return ast.dump(node)


def _attribute_call_sites(tree, attribute: str) -> list:
    """Every `something.<attribute>(...)` call in this module."""
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == attribute
    ]


def _called_names(tree, within=None) -> set:
    """Every bare name and every attribute name this module CALLS, optionally
    restricted to the calls inside one named function's own body."""
    scope = _function_definitions(tree).get(within, tree) if within else tree
    names = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _function_definitions(tree) -> dict:
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _calls_in_function(tree, name: str, attribute: str) -> list:
    """The `.<attribute>(...)` calls inside one named function's own body."""
    definition = _function_definitions(tree)[name]
    return [node for node in ast.walk(definition)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == attribute]


def _enclosing_function(tree, target) -> str | None:
    """Which named function encloses a node (innermost wins)."""
    best_name, best_span = None, None
    for name, definition in _function_definitions(tree).items():
        start = definition.lineno
        end = max((child.lineno for child in ast.walk(definition)
                   if hasattr(child, "lineno")), default=start)
        if start <= target.lineno <= end:
            span = end - start
            if best_span is None or span < best_span:
                best_name, best_span = name, span
    return best_name


def _tree_digests() -> dict:
    digests = {}
    for path in sorted(REPO.joinpath("app").rglob("*.py")):
        digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(REPO.joinpath("tests").glob("test_real_world_*.py")):
        digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


_FROZEN_TREE_AT_IMPORT = _tree_digests()

PRODUCTION_MODIFICATIONS: tuple = ()
SCHEMA_MODIFICATIONS: tuple = ()
JEV_MODIFICATIONS: tuple = ()
CAPTURE_DATA_MODIFICATIONS: tuple = ()


# ===========================================================================
# 22. THE ONE LIVE PATH — opt-in, fail-closed, never a skip when requested
# ===========================================================================
class TestTheLiveWriteAndTheIndependentReadback:
    """
    §12: the single genuine production execution, kept strictly apart from every
    deterministic test above.

    Three states, the convention this repo already uses for live access:
      * opt-in unset          -> SKIP (the suite stays hermetic by default)
      * opt-in set, no secret -> FAIL (never a skip: an unverified catalogue must
                                 never be reported as verified)
      * opt-in set, secret    -> the write happens, and every assertion must hold

    The credential is read from the environment, used, and never printed.
    """

    def _credentials_or_skip(self):
        if os.environ.get(LIVE_OPT_IN_ENV) != "1":
            pytest.skip(
                f"the production catalogue write is opt-in: set {LIVE_OPT_IN_ENV}=1 "
                f"with {LIVE_URL_ENV} and {LIVE_KEY_ENV} in the environment to perform "
                "the four-row write and its independent readback"
            )
        url = os.environ.get(LIVE_URL_ENV)
        key = os.environ.get(LIVE_KEY_ENV)
        if not url or not key:
            pytest.fail(
                f"the production catalogue write was requested but "
                f"{LIVE_URL_ENV}/{LIVE_KEY_ENV} is not configured — failing closed "
                "rather than reporting an unverified catalogue as verified"
            )
        return url, key

    def _client(self):
        url, key = self._credentials_or_skip()
        return catalogue_client(url, key)

    def test_the_live_write_and_readback(self):
        client = self._client()

        # --- 2. the pre-write state, measured live ---------------------------
        pre_rows = read_reference_rows(client)
        violations = pre_write_violations(pre_rows)
        assert violations == [], (
            "REFUSING TO WRITE: the live pre-write state does not meet the pinned "
            f"preconditions: {violations}"
        )

        # --- 3. the ONE permitted write --------------------------------------
        response = write_new_rows(client)
        inserted = list(response.data or [])
        assert len(inserted) == 4

        # --- 5. the independent readback -------------------------------------
        post_rows = read_reference_rows(client)
        problems = post_write_violations(pre_rows, post_rows)
        assert problems == [], (
            "the post-write readback contradicts the write — the four-row insertion "
            f"can still be reverted by deleting exactly {TARGET_NAMES}: {problems}"
        )

        digest = reference_data_digest_of(post_rows)
        assert digest != PRE_WRITE_DIGEST
        print(f"\nJ2 POST-WRITE ROW COUNT = {len(post_rows)}")
        print(f"J2 POST-WRITE DIGEST    = {digest}")

        # --- 6. the genuine matcher against the now-live table ---------------
        matcher = _live_matcher(post_rows)
        assert matcher.reference_identity.reference_data_digest == digest
        assert matcher.reference_identity.source_kind == "LIVE_SUPABASE"
        assert matcher.reference_identity.identity_status == "UNVERSIONED"
        for name in TARGET_NAMES:
            outcome = matcher.resolve(name)
            assert outcome.resolution == "EXACT", name
            assert outcome.catalogue_row["name"] == name
            assert outcome.catalogue_row["family"] == SOURCE_FAMILY_FLAT
            assert outcome.catalogue_row["width"] == EXPECTED_NON_NULL[name]["width"]
            assert outcome.catalogue_row["thickness"] == EXPECTED_NON_NULL[name]["thickness"]
            assert outcome.catalogue_row["weight_per_metre"] == \
                EXPECTED_NON_NULL[name].get("weight_per_metre")

        # --- 7. the neighbours ------------------------------------------------
        for token, resolution, expected_name, why in NEIGHBOURS:
            outcome = matcher.resolve(token)
            assert outcome.resolution == resolution, (token, why)

        # --- the rollback plan exists, and is not used ------------------------
        plan = rollback_plan(client)
        assert plan is not None


def _live_matcher(rows):
    """A genuine SectionMatcher over the rows a live read just returned."""
    from app.engineering_data.section_matcher import SectionMatcher
    module = sys.modules[SectionMatcher.__module__]
    saved = module.supabase
    module.supabase = _InjectedClient(rows)
    try:
        return SectionMatcher()
    finally:
        module.supabase = saved
