"""
J6 — PRODUCTION REFERENCE-DATA IDENTITY PERSISTENCE.

WHAT CHANGED
------------
`app/engineering_data/section_matcher.py` has always recorded WHICH reference
dataset a matcher consulted — LIVE_SUPABASE / UNVERSIONED, plus a deterministic
sha256 digest of the `steel_sections` rows that matcher actually loaded. Both
production paths then discarded that object with the matcher, so a persisted
member row could not say which catalogue snapshot its section had been resolved
against. The live table is UNVERSIONED and its content can change between runs,
so once the row was written the question became unanswerable.

J6 is the smallest production change that makes the identity survive:

  1. `reference_data_projection(identity)` — ONE conversion of a matcher's
     reference identity into the three-key form a caller persists, or None when
     there is none. Values re-validated through the identity type's own
     constructor; the digest is never recomputed.
  2. `app/pipeline.py` reads that projection once, beside the genuine matcher
     that decides every section in the run, and carries it verbatim onto every
     member row.
  3. `app/drawing_reading/dxf_parser.py` does the same, from the same in-scope
     matcher. The DXF authority behaviour was untouched by J6 — the NONE drop it
     recorded as J7's scope has since been removed by J7, and a non-EXACT
     resolution now carries its drawn identity in `section_name_raw` because
     `section_name` is FK-bound to the catalogue.
  4. `app/report/pdf_generator.py` renders a provenance line from the STORED
     identities only. It constructs no matcher, reads no catalogue and computes
     no digest, so it cannot present today's catalogue as a member's identity.

REQUIRED PRODUCTION SCHEMA CHANGE — APPLIED (corrected by J8B Phase 13)
-----------------------------------------------------------------------
One additive, nullable, default-free column:
`supabase/migrations/20260924010000_j6_reference_data_identity.sql`. Per the J6
brief the implementation task did **NOT execute** it, and nothing in this module
needs it to have run: persistence is intercepted at the existing
repository/parser seam, so the tests exercise the production row contract without
a database.

CURRENT STATE, RE-VERIFIED READ-ONLY 2026-09-24: the column IS live, so applying it
is no longer an outstanding deployment precondition. The paragraph above is
preserved because it records what was true when this module was written, and the
migration header now records both states. The tests below are unchanged: they
judge the migration FILE and the row contract, never the live schema.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
No J7. No CAD/fabrication orchestration, and `ReferenceIdentity` and
`ReferenceDataIdentity` are NOT merged — the CAD chain's key pin is read in a
test only, to show the two lineages agree on the persisted SHAPE while staying
separate implementations. No UI. No catalogue change, no database write, and no
backfill: NULL permanently means "no reference identity was recorded for this
row", and the 128 historical rows are never touched.

The boundary, doubles and fixtures are J4's, reused verbatim rather than
restated — see `tests/test_real_world_j4_production_extraction_report_truth.py`.
"""

from __future__ import annotations

import ast
import copy
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_real_world_j4_production_extraction_report_truth as j4

# The proven J4 seams, reused. `production` imports with the repo's real .env
# (so SectionMatcher is genuinely live) and tears `app.*` out of sys.modules
# afterwards, which is what keeps the pre-existing smoke-import failures honest.
production = j4.production
live = j4.live
pdf_boundary = j4.pdf_boundary

_member = j4._member
_single_row = j4._single_row
_rows_for = j4._rows_for
_pdf_text = j4._pdf_text
_write_dxf = j4._write_dxf
_RecordingSupabase = j4._RecordingSupabase
_ReportSupabase = j4._ReportSupabase

REPO = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    REPO / "supabase" / "migrations" / "20260924010000_j6_reference_data_identity.sql"
)

PIPELINE_PATH = REPO / "app" / "pipeline.py"
DXF_PATH = REPO / "app" / "drawing_reading" / "dxf_parser.py"
MATCHER_PATH = REPO / "app" / "engineering_data" / "section_matcher.py"
REPORT_PATH = REPO / "app" / "report" / "pdf_generator.py"

# The identity's own vocabulary, and the exact persisted shape.
SOURCE_LIVE_SUPABASE = "LIVE_SUPABASE"
IDENTITY_UNVERSIONED = "UNVERSIONED"
IDENTITY_KEYS = ("source_kind", "identity_status", "reference_data_digest")
IDENTITY_COLUMN = "reference_data_identity"
# The same three keys ordered, for "carries exactly these keys" comparisons that
# must not depend on the order the matcher happens to write them in.
IDENTITY_KEYS_SORTED = tuple(sorted(IDENTITY_KEYS))

# Pinned live facts (established by the accepted J2 milestone). FACTS, not
# targets. The CONTENT of an identity can legitimately differ between runs; what
# this module pins is that whatever was recorded stays recorded.
LIVE_ROW_COUNT_AFTER_J2 = j4.LIVE_ROW_COUNT_AFTER_J2
LIVE_DIGEST_AFTER_J2 = j4.LIVE_DIGEST_AFTER_J2
LIVE_DIGEST_BEFORE_J2 = j4.LIVE_DIGEST_BEFORE_J2

EXACT_TOKEN = j4.EXACT_TOKEN                              # 310UB46.2
SUFFIX_FALLBACK_TOKEN = j4.SUFFIX_FALLBACK_TOKEN          # 310UB40
SUFFIX_FALLBACK_CANDIDATE = j4.SUFFIX_FALLBACK_CANDIDATE  # 310UB40.4
NONE_TOKEN = j4.NONE_TOKEN                                # 250X90PFC

RESOLUTION_EXACT = "EXACT"
RESOLUTION_SUFFIX_FALLBACK = "SUFFIX_FALLBACK"
RESOLUTION_NONE = "NONE"

_PAGE = None


@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    global _PAGE
    _PAGE = production.PageExtraction
    yield
    _PAGE = None


def _page(number, members, *, connections=()):
    return _PAGE(
        page_number=number,
        drawing_number="J6-DWG-001",
        drawing_title="J6 reference identity persistence",
        revision="A",
        raw_members=list(members),
        raw_connections=list(connections),
    )


# ==========================================================================
# Test-only doubles — the established boundary shapes, plus a catalogue client
# so a test supplies the reference table instead of reaching the live one.
# ==========================================================================


class _Result:
    def __init__(self, data):
        self.data = data


class _CatalogueQuery:
    """The matcher's own read: `table(...).select("*").execute().data`."""

    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name

    def select(self, columns="*"):
        self._client.queries.append((self._table, columns))
        return self

    def execute(self):
        self._client.executed += 1
        return _Result(copy.deepcopy(self._client.rows))


class _CatalogueClient:
    """
    The reference table, supplied by a test.

    Two things this makes provable rather than assumed: the digest is asserted
    against a KNOWN row set instead of whatever the live table holds today, and
    "the reference table is read exactly once per run" becomes countable.
    """

    def __init__(self, rows):
        self.rows = [copy.deepcopy(row) for row in rows]
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _CatalogueQuery(self, name)


def _catalogue(*extra_names):
    """
    A small but genuine reference table: the two rows the resolution behaviours
    need, plus any extra named rows a test adds to make the digest differ.

    Deliberately the same column set as the live table, so
    `reference_data_digest` canonicalises it exactly as it would the live one.
    """
    rows = [
        {"name": "310UB46.2", "family": "UB", "depth": 307.0, "flange_width": 166.0,
         "flange_thickness": 11.8, "web_thickness": 6.7, "weight_per_metre": 46.2,
         "leg_size": None, "thickness": None, "width": None, "outside_diameter": None},
        {"name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
         "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
         "leg_size": None, "thickness": None, "width": None, "outside_diameter": None},
    ]
    for name in extra_names:
        rows.append({
            "name": name, "family": "UB", "depth": 200.0, "flange_width": 100.0,
            "flange_thickness": 8.0, "web_thickness": 5.0, "weight_per_metre": 25.0,
            "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
        })
    return rows


class _MatcherProxy:
    """
    The production matcher with what it DECLARES about its reference data
    replaced or removed.

    Resolution is delegated verbatim to the wrapped production matcher, so every
    engineering outcome is the production one; only the identity declaration
    differs. Two cases are constructible, and both are real:

      identity given   a matcher whose recorded identity differs from the
                       catalogue it resolves against — used to isolate the
                       identity from every engineering field.
      identity absent  a matcher that records no identity at all (a foreign or
                       in-process catalogue, a stand-in, or a matcher built
                       before this milestone) — the case
                       `getattr(matcher, "reference_identity", None)` exists for.
    """

    def __init__(self, matcher, identity=None):
        self._matcher = matcher
        if identity is not None:
            self.reference_identity = identity

    def match(self, raw_name):
        return self._matcher.match(raw_name)

    def resolve(self, raw_name):
        return self._matcher.resolve(raw_name)

    def get_section_regex(self):
        return self._matcher.get_section_regex()


def _matcher_over(monkeypatch, production_module, rows):
    """A genuine SectionMatcher whose reference table the test supplies."""
    monkeypatch.setattr(production_module.matcher_module, "supabase", _CatalogueClient(rows))
    return production_module.matcher_module.SectionMatcher()


def _use_catalogue(monkeypatch, production_module, rows):
    """Point the production matcher's read at a supplied catalogue."""
    client = _CatalogueClient(rows)
    monkeypatch.setattr(production_module.matcher_module, "supabase", client)
    return client


def _identity(source_kind, identity_status, digest):
    return {
        "source_kind": source_kind,
        "identity_status": identity_status,
        "reference_data_digest": digest,
    }


def _squash(text: str) -> str:
    """
    The rendered text with every whitespace run removed.

    PDF text extraction and the cover's own line-wrapper both insert whitespace
    this module does not control, so squashing compares CONTENT — the identity
    values and the digests — without depending on where a renderer broke a line.
    """
    return re.sub(r"\s+", "", text or "")


def _run_dxf(production_module, monkeypatch, tmp_path, token, name, *, matcher=None):
    """Drive the genuine DXF parser and read back the rows it tried to insert."""
    recorder = _RecordingSupabase()
    monkeypatch.setattr(production_module.dxf, "supabase", recorder)
    path = _write_dxf(tmp_path / name, [token])
    production_module.dxf.parse_dxf_and_save(str(path), f"j6-dxf-{name}", matcher)
    return recorder.rows_inserted_into("steel_members")


def _report_member(mark, *, identity, section=EXACT_TOKEN, resolution=RESOLUTION_EXACT,
                   weight=46.2, length_mm=1200.0, quantity=1):
    """One row in the shape `generate_report_pdf` reads out of `steel_members`."""
    exact = resolution == RESOLUTION_EXACT
    return {
        "id": f"row-{mark}",
        "project_id": "j6-report",
        "mark": mark,
        "section_name": section,
        "section_name_raw": section,
        "section_resolution": resolution,
        "section_substituted_candidate": None,
        "section_family": "UB" if exact else None,
        "length_mm": length_mm,
        "quantity": quantity,
        "grade": "300",
        "weight_per_metre": weight if exact else None,
        "total_weight_kg": round((length_mm / 1000) * weight * quantity, 2) if exact else None,
        "confidence": "high",
        "confidence_score": 96,
        "source_page": 1,
        "detail_reference": None,
        IDENTITY_COLUMN: copy.deepcopy(identity),
    }


@pytest.fixture()
def render_report(production, monkeypatch):
    """
    Renders the ACTUAL production report over an explicit set of member rows,
    returning (text, client).

    Only the generator's read boundary is supplied — the generator itself is the
    production one, so the bytes are the customer report's. The client records
    which tables were asked for, so "the report never reads the reference table"
    is proven rather than asserted.
    """

    def render(members):
        client = _ReportSupabase(
            project={
                "id": "j6-report", "name": "J6 report fixture",
                "unmatched_sections": [], "warnings": [],
            },
            members=[dict(m) for m in members],
            connections=[],
        )
        monkeypatch.setattr(production.report, "supabase", client)
        return _pdf_text(production.report.generate_report_pdf("j6-report")), client

    return render


# ==========================================================================
# 0. What is under test is the production boundary.
# ==========================================================================


class TestTheBoundaryIsProduction:
    def test_the_projection_is_the_production_one(self, production):
        assert production.matcher_module.reference_data_projection.__module__ == (
            "app.engineering_data.section_matcher"
        )
        # The extraction paths use THAT function, not a copy of its logic.
        assert production.pipeline.reference_data_projection is (
            production.matcher_module.reference_data_projection
        )
        assert production.dxf.reference_data_projection is (
            production.matcher_module.reference_data_projection
        )

    def test_the_report_cannot_recompute_or_query_anything(self, production):
        """The report has no matcher, no digest function and no projection, so
        presenting a later catalogue read as a member's original identity is
        impossible by construction rather than merely absent today."""
        for name in ("SectionMatcher", "reference_data_digest",
                     "reference_data_projection", "reference_identity"):
            assert not hasattr(production.report, name), name

    def test_nothing_on_the_extraction_path_recomputes_a_digest(self):
        """The digest is computed in exactly one place — the matcher. Every other
        module on this path is structurally incapable of recomputing it."""
        for path in (PIPELINE_PATH, DXF_PATH, REPORT_PATH,
                     REPO / "app" / "engineering_data" / "repository.py",
                     REPO / "app" / "validation" / "rules.py",
                     REPO / "app" / "main.py",
                     REPO / "app" / "export" / "storage_export.py"):
            source = path.read_text(encoding="utf-8")
            assert "reference_data_digest(" not in source, path
            assert "hashlib" not in source, path

    def test_each_extraction_path_builds_exactly_one_matcher(self):
        """One matcher per run, so one identity per run: the persisted value
        cannot come from a second, differently-configured matcher.

        Milestone J16 gave the PDF path a SECOND entry point (a continuation
        reads the next window of a drawing set larger than one extraction
        window), so the construction is counted per FUNCTION rather than per
        module. The property this pins is unchanged: each run builds exactly one
        matcher, reads its identity once, and persists that identity on the rows
        that run writes — a run that built a second matcher could persist a row
        whose section came from one catalogue and whose identity came from
        another.
        """
        for path in (PIPELINE_PATH, DXF_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            per_function = {}
            for function in ast.walk(tree):
                if not isinstance(function, ast.FunctionDef):
                    continue
                per_function[function.name] = sum(
                    1 for node in ast.walk(function)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "SectionMatcher"
                )
            builders = {name: count for name, count in per_function.items() if count}
            assert builders, (path.name, "no extraction path builds a matcher")
            for name, count in builders.items():
                assert count == 1, (path.name, name, count)


# ==========================================================================
# A. Identity creation.
# ==========================================================================


class TestIdentityCreation:
    def test_a_live_matcher_records_the_accepted_identity(self, production, live):
        identity = production.matcher_module.SectionMatcher().reference_identity

        assert identity.source_kind == SOURCE_LIVE_SUPABASE
        assert identity.identity_status == IDENTITY_UNVERSIONED
        assert identity.source_kind == production.matcher_module.SOURCE_LIVE_SUPABASE
        assert identity.identity_status == production.matcher_module.IDENTITY_UNVERSIONED

    def test_the_live_identity_is_the_pinned_accepted_identity(self, production, live):
        identity = production.matcher_module.SectionMatcher().reference_identity

        assert len(live.rows) == LIVE_ROW_COUNT_AFTER_J2
        assert identity.reference_data_digest == LIVE_DIGEST_AFTER_J2
        assert live.digest == LIVE_DIGEST_AFTER_J2

    def test_the_digest_is_of_the_rows_the_matcher_actually_loaded(
        self, production, monkeypatch
    ):
        """Not of the table as it stands later, and not of anything the matcher
        did not read: the digest of exactly the rows this matcher was handed."""
        rows = _catalogue()
        matcher = _matcher_over(monkeypatch, production, rows)

        assert matcher.reference_identity.reference_data_digest == (
            production.matcher_module.reference_data_digest(rows)
        )

    def test_the_identity_is_read_only(self, production, live):
        """A property with no setter, so the binding cannot be replaced — the
        value a run persists cannot be swapped after the fact."""
        matcher = production.matcher_module.SectionMatcher()
        before = matcher.reference_identity

        with pytest.raises(AttributeError):
            matcher.reference_identity = None
        assert matcher.reference_identity is before

    def test_the_projection_preserves_the_three_keys_and_values_exactly(self, production):
        identity = production.matcher_module.ReferenceIdentity(
            source_kind=SOURCE_LIVE_SUPABASE,
            identity_status=IDENTITY_UNVERSIONED,
            reference_data_digest=LIVE_DIGEST_BEFORE_J2,
        )
        projection = production.matcher_module.reference_data_projection(identity)

        assert tuple(sorted(projection)) == IDENTITY_KEYS_SORTED
        assert projection["source_kind"] == SOURCE_LIVE_SUPABASE
        assert projection["identity_status"] == IDENTITY_UNVERSIONED
        assert projection["reference_data_digest"] == LIVE_DIGEST_BEFORE_J2

    def test_the_projection_returns_a_detached_dict(self, production):
        """Mutating the result must not reach the matcher's frozen identity, and
        the result must not be able to smuggle extra keys into the row."""
        identity = production.matcher_module.ReferenceIdentity(
            source_kind=SOURCE_LIVE_SUPABASE,
            identity_status=IDENTITY_UNVERSIONED,
            reference_data_digest=LIVE_DIGEST_BEFORE_J2,
        )
        project = production.matcher_module.reference_data_projection

        first = project(identity)
        first["identity_status"] = "MUTATED"
        first["smuggled"] = True

        assert project(identity) == _identity(
            SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, LIVE_DIGEST_BEFORE_J2
        )
        assert identity.identity_status == IDENTITY_UNVERSIONED

    def test_the_projection_never_recomputes_the_digest(self, production):
        """A digest that belongs to no catalogue this process can see is returned
        verbatim: the projection converts a recorded identity, it does not check
        it against the table as it stands now."""
        invented = "0" * 64
        identity = production.matcher_module.ReferenceIdentity(
            source_kind=SOURCE_LIVE_SUPABASE,
            identity_status=IDENTITY_UNVERSIONED,
            reference_data_digest=invented,
        )
        assert production.matcher_module.reference_data_projection(identity)[
            "reference_data_digest"
        ] == invented

    def test_no_identity_projects_to_none(self, production):
        assert production.matcher_module.reference_data_projection(None) is None

    def test_the_projection_refuses_values_it_does_not_understand(self, production):
        """It reuses the identity type's OWN validation rather than restating it,
        so an unknown source, an unknown status or a malformed digest raises
        instead of being persisted."""

        class _ForeignSource:
            source_kind = "SOMEWHERE_ELSE"
            identity_status = IDENTITY_UNVERSIONED
            reference_data_digest = LIVE_DIGEST_BEFORE_J2

        class _UnknownStatus:
            source_kind = SOURCE_LIVE_SUPABASE
            identity_status = "VERSIONED"
            reference_data_digest = LIVE_DIGEST_BEFORE_J2

        class _MalformedDigest:
            source_kind = SOURCE_LIVE_SUPABASE
            identity_status = IDENTITY_UNVERSIONED
            reference_data_digest = "not-a-digest"

        class _NoDeclaration:
            pass

        refused = (
            _identity(SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, LIVE_DIGEST_BEFORE_J2),
            _ForeignSource(),
            _UnknownStatus(),
            _MalformedDigest(),
            _NoDeclaration(),
        )
        for candidate in refused:
            with pytest.raises(ValueError):
                production.matcher_module.reference_data_projection(candidate)

    def test_the_projection_introduces_no_new_vocabulary(self, production):
        """The closed vocabularies the identity already had are the whole set."""
        assert production.matcher_module.SOURCE_KINDS == (SOURCE_LIVE_SUPABASE,)
        assert production.matcher_module.IDENTITY_STATUSES == (IDENTITY_UNVERSIONED,)


# ==========================================================================
# B. PDF persistence.
# ==========================================================================


class TestPdfPersistence:
    def test_every_member_row_carries_the_identity_of_the_matcher_that_resolved_it(
        self, production, monkeypatch, pdf_boundary
    ):
        _use_catalogue(monkeypatch, production, _catalogue())
        expected = production.matcher_module.reference_data_projection(
            production.matcher_module.SectionMatcher().reference_identity
        )

        result = pdf_boundary([_page(1, [
            _member("M1", EXACT_TOKEN),
            _member("M2", SUFFIX_FALLBACK_TOKEN),
        ])])

        assert len(result.members) == 2
        for row in result.members:
            assert row[IDENTITY_COLUMN] == expected
            assert tuple(sorted(row[IDENTITY_COLUMN])) == IDENTITY_KEYS_SORTED

    def test_the_live_catalogue_run_persists_the_pinned_live_identity(
        self, production, live, pdf_boundary
    ):
        """The genuine path with no catalogue substitution: the identity recorded
        on the row is the live matcher's own."""
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row[IDENTITY_COLUMN]["reference_data_digest"] == LIVE_DIGEST_AFTER_J2
        assert row[IDENTITY_COLUMN]["source_kind"] == SOURCE_LIVE_SUPABASE
        assert row[IDENTITY_COLUMN]["identity_status"] == IDENTITY_UNVERSIONED

    def test_the_identity_is_recorded_even_when_nothing_resolved(
        self, production, monkeypatch, pdf_boundary
    ):
        """It describes the SOURCE, not the outcome. A run whose token was not in
        the catalogue still consulted the catalogue, and says which."""
        _use_catalogue(monkeypatch, production, _catalogue())
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_NONE
        assert len(row[IDENTITY_COLUMN]["reference_data_digest"]) == 64
        assert row[IDENTITY_COLUMN]["source_kind"] == SOURCE_LIVE_SUPABASE

    def test_a_run_reads_the_reference_table_exactly_once(
        self, production, monkeypatch, pdf_boundary
    ):
        """The persisted identity comes from the one read the matcher made — not
        from a second lookup performed while building the rows."""
        client = _use_catalogue(monkeypatch, production, _catalogue())

        pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN), _member("M2", EXACT_TOKEN)])])

        assert client.executed == 1
        assert client.queries == [("steel_sections", "*")]

    def test_every_page_of_a_run_shares_one_identity(
        self, production, monkeypatch, pdf_boundary
    ):
        _use_catalogue(monkeypatch, production, _catalogue())
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN)]),
            _page(2, [_member("M2", SUFFIX_FALLBACK_TOKEN)]),
        ])

        assert len({repr(row[IDENTITY_COLUMN]) for row in result.members}) == 1


# ==========================================================================
# C. The persistence payload.
# ==========================================================================


class TestPersistencePayload:
    def test_the_pdf_payload_carries_j5_and_j6_together(
        self, production, monkeypatch, pdf_boundary
    ):
        _use_catalogue(monkeypatch, production, _catalogue())
        result = pdf_boundary([_page(1, [
            _member("M1", EXACT_TOKEN),
            _member("M2", SUFFIX_FALLBACK_TOKEN),
        ])])
        rows = {row["mark"]: row for row in result.members}

        # J5's values, unchanged.
        assert rows["M1"]["section_resolution"] == RESOLUTION_EXACT
        assert rows["M1"]["section_substituted_candidate"] is None
        assert rows["M2"]["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert rows["M2"]["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE

        # J6's one addition, on both rows.
        for row in rows.values():
            assert tuple(sorted(row[IDENTITY_COLUMN])) == IDENTITY_KEYS_SORTED

    def test_the_dxf_insert_payload_carries_the_same_contract(
        self, production, monkeypatch, tmp_path
    ):
        matcher = _matcher_over(monkeypatch, production, _catalogue())
        rows = _run_dxf(production, monkeypatch, tmp_path, EXACT_TOKEN, "payload.dxf",
                        matcher=matcher)

        assert len(rows) == 1
        row = rows[0]
        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["section_substituted_candidate"] is None
        assert tuple(sorted(row[IDENTITY_COLUMN])) == IDENTITY_KEYS_SORTED
        assert row[IDENTITY_COLUMN]["source_kind"] == SOURCE_LIVE_SUPABASE
        assert row[IDENTITY_COLUMN]["identity_status"] == IDENTITY_UNVERSIONED
        assert len(row[IDENTITY_COLUMN]["reference_data_digest"]) == 64

    def test_the_dxf_identity_is_the_identity_of_the_catalogue_it_read(
        self, production, monkeypatch, tmp_path
    ):
        matcher = _matcher_over(monkeypatch, production, _catalogue())
        expected = production.matcher_module.reference_data_projection(
            matcher.reference_identity
        )

        rows = _run_dxf(production, monkeypatch, tmp_path, EXACT_TOKEN, "identity.dxf",
                        matcher=matcher)

        assert rows[0][IDENTITY_COLUMN] == expected


# ==========================================================================
# D. Report / output.
# ==========================================================================


class TestReportProvenance:
    def test_case_a_renders_the_stored_identity(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        stored = _single_row(result.members, "M1")[IDENTITY_COLUMN]

        assert stored["reference_data_digest"] in _squash(result.pdf_text)
        assert stored["source_kind"] in result.pdf_text
        assert stored["identity_status"] in result.pdf_text

    def test_case_a_reads_the_stored_values_not_todays_catalogue(
        self, production, render_report
    ):
        """The decisive proof. A STORED digest that is not the current
        catalogue's — the pre-J2 value — is rendered as stored, and the current
        digest does not appear anywhere in the report."""
        text, _ = render_report([
            _report_member("M1", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, LIVE_DIGEST_BEFORE_J2)),
        ])

        assert LIVE_DIGEST_BEFORE_J2 in _squash(text)
        assert LIVE_DIGEST_AFTER_J2 not in text

    def test_case_b_says_nothing_was_recorded_and_shows_no_digest(
        self, production, render_report
    ):
        text, _ = render_report([_report_member("M1", identity=None)])

        assert "noreference-dataidentitywasrecorded" in _squash(text).lower()
        assert not re.search(r"\b[0-9a-f]{64}\b", text)

    def test_case_b_never_substitutes_todays_catalogue_digest(
        self, production, live, render_report
    ):
        """Even with the live catalogue available to the process, a row that
        recorded nothing is reported as having recorded nothing."""
        text, _ = render_report([_report_member("M1", identity=None)])

        assert LIVE_DIGEST_AFTER_J2 not in text
        assert LIVE_DIGEST_BEFORE_J2 not in text

    def test_case_c_lists_every_identity_and_selects_none(self, production, render_report):
        newer, older = LIVE_DIGEST_AFTER_J2, LIVE_DIGEST_BEFORE_J2
        members = [
            _report_member("M1", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, newer)),
            _report_member("M2", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, older)),
        ]
        text, _ = render_report(members)
        squashed = _squash(text)

        assert "morethanonereference-dataidentity" in squashed.lower()
        assert older in squashed and newer in squashed
        # Deterministic: sorted, so the order does not depend on row order.
        assert squashed.index(newer) < squashed.index(older)
        # Nothing is presented as current, and no single identity is claimed.
        assert "current" not in text.lower()
        for digest in (newer, older):
            assert _squash(
                f"Reference data: {SOURCE_LIVE_SUPABASE} · "
                f"{IDENTITY_UNVERSIONED} · {digest}"
            ) not in squashed

    def test_case_c_sorts_the_identities_whatever_order_the_rows_arrive_in(
        self, production, render_report
    ):
        """The sentence is a sorted set of stored digests, not a transcription of
        the row order. Asserted on the production function itself, because the
        rendered schedule's own order follows the rows it is given."""
        newer, older = LIVE_DIGEST_AFTER_J2, LIVE_DIGEST_BEFORE_J2
        forward = [
            _report_member("M1", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, newer)),
            _report_member("M2", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, older)),
        ]
        sentence = production.report._reference_identity_provenance

        assert sentence(forward) == sentence(list(reversed(forward)))
        assert sentence(forward).index(newer) < sentence(forward).index(older)

        # And the rendered report carries exactly that sentence.
        text, _ = render_report(forward)
        assert _squash(sentence(forward)) in _squash(text)

    def test_an_unreadable_stored_record_is_reported_as_unreadable(
        self, production, render_report
    ):
        """Neither "nothing recorded" nor a value the report prints anyway — a
        record that is present but cannot be read is its own outcome."""
        text, _ = render_report([
            _report_member("M1", identity={"source_kind": SOURCE_LIVE_SUPABASE}),
        ])
        squashed = _squash(text).lower()

        assert "couldnotberead" in squashed
        assert "noreference-dataidentitywasrecorded" not in squashed
        assert LIVE_DIGEST_AFTER_J2 not in text

    def test_the_report_reads_only_the_tables_it_always_did(self, production, render_report):
        _, client = render_report([_report_member("M1", identity=None)])

        assert sorted(set(client.queries)) == ["connections", "projects", "steel_members"]
        assert "steel_sections" not in client.queries

    def test_the_provenance_line_changes_nothing_else_in_the_report(
        self, production, render_report
    ):
        """Same members, one with a recorded identity and one without: remove the
        provenance sentence itself and the two reports are identical."""
        recorded, _ = render_report([
            _report_member("M1", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, LIVE_DIGEST_BEFORE_J2)),
        ])
        unrecorded, _ = render_report([_report_member("M1", identity=None)])

        def without_provenance(text):
            for sentence in (
                f"Reference data: {SOURCE_LIVE_SUPABASE} · {IDENTITY_UNVERSIONED} "
                f"· {LIVE_DIGEST_BEFORE_J2}",
                production.report.IDENTITY_NOT_RECORDED_TEXT,
            ):
                text = text.replace(_squash(sentence), "")
            return text

        assert without_provenance(_squash(recorded)) == without_provenance(_squash(unrecorded))


# ==========================================================================
# E. Historical rows — NULL stays NULL, and nothing fills it in.
# ==========================================================================


class TestLegacyRows:
    def test_a_matcher_that_declares_no_identity_persists_null(
        self, production, monkeypatch, pdf_boundary
    ):
        monkeypatch.setattr(
            production.pipeline, "SectionMatcher",
            lambda: _MatcherProxy(production.matcher_module.SectionMatcher()),
        )
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row[IDENTITY_COLUMN] is None
        # Absence of provenance is not absence of engineering evidence.
        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["section_name"] == EXACT_TOKEN
        assert row["review_status"] == "extracted"

    def test_null_is_a_different_persisted_value_from_a_recorded_identity(
        self, production, render_report
    ):
        """Which is why the three keys travel together rather than as a bare
        digest: a recorded UNVERSIONED identity and "nothing recorded" must not
        collapse into one value."""
        recorded, _ = render_report([
            _report_member("M1", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, LIVE_DIGEST_AFTER_J2)),
        ])
        unrecorded, _ = render_report([_report_member("M1", identity=None)])

        assert _squash(recorded) != _squash(unrecorded)
        assert LIVE_DIGEST_AFTER_J2 in recorded
        assert LIVE_DIGEST_AFTER_J2 not in unrecorded

    def test_the_migration_adds_one_nullable_column_and_nothing_else(self):
        assert MIGRATION_PATH.exists(), f"missing {MIGRATION_PATH}"
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        # The header carries the rationale; only the statements are judged.
        statements = [
            line.strip().rstrip(";")
            for line in sql.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        joined = " ".join(statements).lower()

        assert joined.count("add column if not exists") == 1
        assert "add column if not exists reference_data_identity jsonb" in joined
        assert joined.startswith("alter table steel_members")

        # The properties the brief requires, asserted rather than assumed.
        for forbidden in ("not null", "default", "drop ", "alter column",
                          "update ", "insert ", "delete ", "references",
                          "check", "index", "trigger", "generated"):
            assert forbidden not in joined, forbidden

    def test_the_migration_touches_one_table_and_no_other(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        tables = set(re.findall(r"(?i)\b(?:alter|create|drop)\s+table\s+([a-z_]+)", sql))
        assert tables == {"steel_members"}

    def test_no_source_file_carries_a_digest_it_could_fill_in(self):
        """A hardcoded digest, or an assignment to the column, is what a backfill
        or a fallback-to-current-catalogue would look like. Neither exists."""
        for path in (PIPELINE_PATH, DXF_PATH, REPORT_PATH, MATCHER_PATH):
            source = path.read_text(encoding="utf-8")
            assert not re.search(r"\b[0-9a-f]{64}\b", source), path
            assert "reference_data_identity =" not in source, path

    def test_the_repository_offers_no_way_to_rewrite_a_written_row(self, production):
        """Structural: there is no member-update API to reach for, so a stored
        identity cannot be revised after the fact through this path.

        Milestone J16 added the READ half of this module (a continuation has to
        know what the project, its drawing set and its members already state
        before it may add a window to them), and Milestone J17 added one more
        read (a retry asks whether a page the record calls failed already has
        evidence persisted against it, by exact drawing + page). Those six
        functions are listed here as reads, and the property is unchanged:
        nothing in this module can rewrite a written member row.

        Milestone J23 added the append-and-read pair for the raw AI capture
        ledger: one insert of newly read pages, one read of a drawing's
        readings. An insert appends and a read reads; neither is an update, and
        neither touches a member row, so the property this test guards is
        unchanged.

        Milestone J28 added the append for the PDF annotation occurrence ledger,
        and Milestone J29 added the read beside it. J28 did not declare its name
        here; J29 declares both, because leaving the list stale would have made
        this pin fail for a reason that is not the property it guards. The
        property is still unchanged: the append writes rows to a table that
        holds no member and no identity of any kind and that carries no update
        path — the J28 tests assert that function's own code names no update,
        delete or upsert — and the read is a `.select()` of that same table,
        filtered by project. Neither can rewrite a written member row, because
        neither reaches a member row at all.

        Milestone J61 added the identity of a project's source DOCUMENT: one
        content-idempotent append, two reads and one write of the document's own
        page count. The append creates a `project_documents` row keyed by the
        hash of the file's bytes, so a repeated extraction of one document is a
        repeat rather than a second identity; it is an insert, and it reads
        before it inserts, never after. The two reads return persisted facts
        about a document, and about the document a lineage names. The write sets
        `page_count` on one document — a nullable ATTRIBUTE column that the J61
        migration declares as the lineage's own count where it has one and NULL
        where it does not, and that the pipeline fills once the reading has
        established it. None of the four can reach a member row: a document is a
        fact about a FILE, and this table carries no member, no section and no
        reference identity. The property this test guards is unchanged, and the
        second assertion below admits `update_document_page_count` for the same
        reason it admits the writers above — it writes a recorded attribute, not
        an identity.

        Milestone J64 added the role a human ASSERTS for one of a project's own
        source documents: one read of the project's documents and one write of
        one column of one `project_documents` row. It is not an update of
        anything a reading produced — it revises no member, no section, no
        connection and no reference identity, and it reaches no member row: it
        sets `role` on a row that describes a FILE. The role is an assertion and
        never an inference, the J64 tests assert that removing either of its two
        ownership guards changes what the operation does, and the column it
        writes already existed with its own DEFAULT and CHECK before this
        milestone — no migration was written for it. The property this test
        guards is unchanged, and the second assertion below admits
        `update_document_role` for the same reason it admits the writers above:
        it writes a recorded attribute, not an identity.
        """
        import inspect

        public = {
            name
            for name, member in inspect.getmembers(production.repository, inspect.isfunction)
            if not name.startswith("_")
            and member.__module__ == "app.engineering_data.repository"
        }
        assert public == {
            "upload_page_image", "create_drawing_set", "create_drawing",
            "create_analysis_run", "update_analysis_run", "update_drawing_set",
            "update_drawing_meta", "insert_members", "insert_review_items",
            "insert_connection", "insert_bolt_groups", "insert_connection_plates",
            "insert_weld_details", "link_connection_members", "update_project_summary",
            # J16 reads — readers only; the J16 tests assert each returns columns
            # and writes nothing.
            "get_project", "drawing_sets_for_project", "drawings_for_drawing_set",
            "member_rows_for_project", "connection_rows_for_project",
            # J17 read — reader only; the J17 tests assert it returns row
            # identities and writes nothing.
            "evidence_rows_for_page",
            # J23 append and read for the raw AI capture ledger. The append is
            # structurally unable to rewrite: the J23 tests assert its own code
            # carries no update, delete or upsert, and the live table refuses
            # both at the append-only trigger and at the grant.
            "insert_page_extraction_captures", "page_extraction_captures_for_drawing",
            # J28 append and J29 read for the PDF annotation occurrence ledger,
            # declared together here because J28 left this list untouched. The
            # append writes into a table with no member column and no update
            # path, and the read is a select of that table; see the docstring.
            "insert_pdf_annotation_occurrences", "pdf_annotation_occurrences_for_project",
            # J61 append and reads for the document identity. The append is
            # content-idempotent: the J61 tests assert it reads before it inserts,
            # never after, and that a second call for the same bytes returns the
            # row the first one wrote. See the docstring.
            "create_project_document", "project_documents_for_project",
            "document_for_drawing", "update_document_page_count",
            # J64: sets `role` on one project_documents row — the value a human
            # ASSERTED, replacing the UNKNOWN the row carried. It reads this
            # project's own documents first (through J61's read, above) and
            # writes one column of one of them. A document is a fact about a
            # FILE, not an identity, and it reaches no member row; see the
            # docstring.
            "update_document_role",
            # E2E-001N reads — readers only, and they reach no member row. The
            # first reads one drawing set's analysis runs; the second composes it
            # with J61's and J16's reads to pair each of a project's documents
            # with the run that read it, so a project holding several documents
            # can be summarised as a SET rather than as whichever run finished
            # last. Both are `select` and nothing else — neither carries an
            # insert, an update, a delete or an rpc, so neither can rewrite a
            # written row, and neither touches `steel_members`, a section or a
            # reference identity, which is what this test guards.
            "analysis_runs_for_drawing_set", "document_extraction_states",
        }
        assert not any(
            name.startswith("update") and "member" in name for name in public
        )
        assert not any(
            name.startswith("update") and name not in (
                "update_analysis_run", "update_drawing_set", "update_drawing_meta",
                "update_project_summary",
                # J61: sets `page_count` on one project_documents row. A recorded
                # attribute of a FILE, not an identity — and it reaches no member.
                "update_document_page_count",
                # J64: sets `role` on one project_documents row — the role a human
                # asserted. The same shape as the line above: a recorded
                # attribute of a FILE, scoped by project and document, reaching
                # no member row and no identity.
                "update_document_role",
            )
            for name in public
        )


# ==========================================================================
# F. Identity immutability and history.
# ==========================================================================


class TestIdentityImmutability:
    def test_two_different_catalogues_produce_different_digests(self, production):
        digest = production.matcher_module.reference_data_digest
        assert digest(_catalogue()) != digest(_catalogue("200UB25.4"))

    def test_row_order_does_not_change_the_digest(self, production):
        digest = production.matcher_module.reference_data_digest
        rows = _catalogue("200UB25.4")
        assert digest(rows) == digest(list(reversed(rows)))

    def test_changing_one_value_changes_the_digest(self, production):
        digest = production.matcher_module.reference_data_digest
        rows = _catalogue()
        changed = copy.deepcopy(rows)
        changed[0]["weight_per_metre"] = 46.3
        assert digest(rows) != digest(changed)

    def test_re_reading_unchanged_rows_reproduces_the_digest(self, production, monkeypatch):
        rows = _catalogue()
        first = _matcher_over(monkeypatch, production, rows)
        second = _matcher_over(monkeypatch, production, rows)

        assert first.reference_identity.reference_data_digest == (
            second.reference_identity.reference_data_digest
        )

    def test_a_persisted_row_keeps_its_identity_when_a_later_run_uses_another(
        self, production, monkeypatch, pdf_boundary
    ):
        """THE POINT OF THE MILESTONE. The catalogue is unversioned and its
        content can change between runs; a row already written must keep the
        identity it was resolved against, and the later run must not rewrite it."""
        _use_catalogue(monkeypatch, production, _catalogue())
        run_one = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        first_row = _single_row(run_one.members, "M1")
        first_identity = copy.deepcopy(first_row[IDENTITY_COLUMN])

        # A DIFFERENT catalogue — one extra row, so a different digest.
        _use_catalogue(monkeypatch, production, _catalogue("200UB25.4"))
        run_two = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        second_identity = _single_row(run_two.members, "M1")[IDENTITY_COLUMN]

        assert second_identity != first_identity
        assert second_identity["reference_data_digest"] != (
            first_identity["reference_data_digest"]
        )
        # Run one's row is untouched, and both identities are the accepted shape.
        assert first_row[IDENTITY_COLUMN] == first_identity
        for identity in (first_identity, second_identity):
            assert tuple(sorted(identity)) == IDENTITY_KEYS_SORTED
            assert identity["identity_status"] == IDENTITY_UNVERSIONED


# ==========================================================================
# G. Missing identity.
# ==========================================================================


class TestMissingIdentity:
    def test_the_pdf_path_persists_null_for_a_matcher_without_an_identity(
        self, production, monkeypatch, pdf_boundary
    ):
        monkeypatch.setattr(
            production.pipeline, "SectionMatcher",
            lambda: _MatcherProxy(production.matcher_module.SectionMatcher()),
        )
        result = pdf_boundary([_page(1, [
            _member("M1", EXACT_TOKEN), _member("M2", NONE_TOKEN),
        ])])

        assert len(result.members) == 2
        assert all(row[IDENTITY_COLUMN] is None for row in result.members)

    def test_the_dxf_path_persists_null_for_a_matcher_without_an_identity(
        self, production, monkeypatch, tmp_path
    ):
        matcher = _matcher_over(monkeypatch, production, _catalogue())

        rows = _run_dxf(production, monkeypatch, tmp_path, EXACT_TOKEN, "identityless.dxf",
                        matcher=_MatcherProxy(matcher))

        assert rows[0][IDENTITY_COLUMN] is None
        # The resolution is unaffected: the proxy resolves exactly as production.
        assert rows[0]["section_resolution"] == RESOLUTION_EXACT
        assert rows[0]["section_name"] == EXACT_TOKEN

    def test_absence_is_never_filled_in(self, production, monkeypatch):
        """`getattr(matcher, "reference_identity", None)` on a matcher that
        declares none is None, and None projects to None. There is no default
        identity and no fallback to the current catalogue."""
        matcher = _matcher_over(monkeypatch, production, _catalogue())
        proxy = _MatcherProxy(matcher)

        declared = getattr(proxy, "reference_identity", None)
        assert declared is None
        assert production.matcher_module.reference_data_projection(declared) is None
        # The wrapped matcher still HAS one — the absence belongs to the proxy,
        # not to a degraded global.
        assert matcher.reference_identity.reference_data_digest


# ==========================================================================
# H. Cross-run distinct identities.
# ==========================================================================


class TestCrossRunIdentities:
    def test_each_run_retains_its_own_identity(self, production, monkeypatch, pdf_boundary):
        _use_catalogue(monkeypatch, production, _catalogue())
        run_one = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])

        _use_catalogue(monkeypatch, production, _catalogue("200UB25.4"))
        run_two = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])

        first = _single_row(run_one.members, "M1")[IDENTITY_COLUMN]
        second = _single_row(run_two.members, "M1")[IDENTITY_COLUMN]

        assert first != second
        assert first["reference_data_digest"] != second["reference_data_digest"]
        assert first["identity_status"] == second["identity_status"] == IDENTITY_UNVERSIONED
        assert first["source_kind"] == second["source_kind"] == SOURCE_LIVE_SUPABASE

    def test_the_report_does_not_collapse_two_runs_into_one_identity(
        self, production, render_report
    ):
        """A project's rows can legitimately come from runs against different
        snapshots. The report says so rather than picking one."""
        digests = [LIVE_DIGEST_BEFORE_J2, LIVE_DIGEST_AFTER_J2]
        text, _ = render_report([
            _report_member("M1", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, digests[0])),
            _report_member("M2", identity=_identity(
                SOURCE_LIVE_SUPABASE, IDENTITY_UNVERSIONED, digests[1])),
        ])
        squashed = _squash(text)

        assert "morethanonereference-dataidentity" in squashed.lower()
        for digest in digests:
            assert digest in squashed
        # The CASE A sentence is not claimed for either identity.
        for digest in digests:
            assert _squash(
                f"Reference data: {SOURCE_LIVE_SUPABASE} · "
                f"{IDENTITY_UNVERSIONED} · {digest}"
            ) not in squashed


# ==========================================================================
# I. The identity is metadata only — it decides nothing.
# ==========================================================================


class TestIdentityIsMetadataOnly:
    #: Every persisted field that could carry an engineering claim.
    AUTHORITY_FIELDS = (
        "mark", "section_name", "section_name_raw", "section_family",
        "section_resolution", "section_substituted_candidate", "length_mm",
        "grade", "quantity", "weight_per_metre", "total_weight_kg",
        "confidence", "confidence_score", "source_page", "extraction_method",
        "review_status", "detail_reference", "notes",
    )

    def test_a_different_identity_changes_no_engineering_value(
        self, production, monkeypatch, pdf_boundary
    ):
        """Same catalogue, so the same resolutions and weights; only the recorded
        identity differs. Every engineering field must be identical."""
        _use_catalogue(monkeypatch, production, _catalogue())
        baseline = pdf_boundary([_page(1, [
            _member("M1", EXACT_TOKEN), _member("M2", SUFFIX_FALLBACK_TOKEN),
        ])])

        relabelled = production.matcher_module.ReferenceIdentity(
            source_kind=SOURCE_LIVE_SUPABASE,
            identity_status=IDENTITY_UNVERSIONED,
            reference_data_digest=LIVE_DIGEST_BEFORE_J2,
        )
        monkeypatch.setattr(
            production.pipeline, "SectionMatcher",
            lambda: _MatcherProxy(production.matcher_module.SectionMatcher(), relabelled),
        )
        substituted = pdf_boundary([_page(1, [
            _member("M1", EXACT_TOKEN), _member("M2", SUFFIX_FALLBACK_TOKEN),
        ])])

        assert len(baseline.members) == len(substituted.members) == 2
        for left, right in zip(baseline.members, substituted.members):
            for field in self.AUTHORITY_FIELDS:
                assert left[field] == right[field], field
            assert left[IDENTITY_COLUMN]["reference_data_digest"] != (
                right[IDENTITY_COLUMN]["reference_data_digest"]
            )

        # `project_id` names the RUN, not the project's engineering content, and
        # the two fixture runs necessarily differ there. Everything the rollup
        # says about the members must be identical.
        assert {k: v for k, v in baseline.project_row.items() if k != "project_id"} == (
            {k: v for k, v in substituted.project_row.items() if k != "project_id"}
        )
        assert baseline.summary == substituted.summary

    def test_the_validation_boundary_never_mentions_the_identity(self):
        """`validate_extraction` decides the resolution and knows nothing about
        the identity, so the identity cannot be read to decide anything."""
        source = (REPO / "app" / "validation" / "rules.py").read_text(encoding="utf-8")
        assert IDENTITY_COLUMN not in source
        assert "reference_data_projection" not in source

    def test_the_dxf_authority_behaviour_is_unchanged(self, production, monkeypatch, tmp_path):
        """The DXF identity rule — the catalogue may CONFIRM a drawn identity but
        never CHANGE it — is untouched by J6.

        WHICH COLUMN carries that identity changed in J7 (Option C), and the NONE
        drop that was J7's to remove has been removed: section_name is FK-bound
        to steel_sections(name), so only an EXACT resolution may populate it, and
        every other member's drawn identity lives in section_name_raw. The rule
        J6 is asserting here is unchanged — the catalogue still confirms and
        never changes an identity.
        """
        matcher = _matcher_over(monkeypatch, production, _catalogue())

        exact = _run_dxf(production, monkeypatch, tmp_path, EXACT_TOKEN,
                         "authority-exact.dxf", matcher=matcher)
        refused = _run_dxf(production, monkeypatch, tmp_path, SUFFIX_FALLBACK_TOKEN,
                           "authority-fallback.dxf", matcher=matcher)
        unresolved = _run_dxf(production, monkeypatch, tmp_path, NONE_TOKEN,
                              "authority-none.dxf", matcher=matcher)

        assert exact[0]["section_name"] == EXACT_TOKEN
        assert exact[0]["section_family"] == "UB"
        # Confirmed as drawn, never changed to the offered substitute.
        assert refused[0]["section_name"] is None
        assert refused[0]["section_name_raw"] == SUFFIX_FALLBACK_TOKEN
        assert refused[0]["section_family"] is None
        assert refused[0]["weight_per_metre"] is None
        assert refused[0]["total_weight_kg"] is None
        assert refused[0]["review_status"] == "review_required"
        # Persisted, not dropped — and claiming nothing (J7).
        assert len(unresolved) == 1
        assert unresolved[0]["section_name"] is None
        assert unresolved[0]["section_resolution"] == "NONE"
        assert unresolved[0]["review_status"] == "review_required"


# ==========================================================================
# J. J5 regression — the accepted behaviour this milestone must not disturb.
# ==========================================================================


class TestJ5Regression:
    def test_exact_stays_exact(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, confidence=40)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["section_substituted_candidate"] is None
        assert "Confirmed" in result.pdf_text
        assert "Low" in result.pdf_text
        assert "Verify" not in result.pdf_text

    def test_suffix_fallback_stays_a_refusal(self, production, pdf_boundary, live):
        result = pdf_boundary([
            _page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN, confidence=99)]),
        ])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert row["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["review_status"] == "review_required"
        assert "Substitution refused" in result.pdf_text
        assert "Confirmed" not in result.pdf_text

    def test_none_stays_unresolved(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN, confidence=99)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_NONE
        assert row["section_name"] is None
        assert row["review_status"] == "review_required"
        assert "Unresolved" in result.pdf_text
        assert NONE_TOKEN in result.pdf_text

    def test_consolidation_conflict_still_keeps_every_row(self, production, pdf_boundary, live):
        """J5's rule: a mark whose pages disagree about the resolution keeps one
        row per page rather than silently discarding the unresolved evidence."""
        result = pdf_boundary([
            _page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 2
        assert {row["section_resolution"] for row in rows} == {
            RESOLUTION_SUFFIX_FALLBACK, RESOLUTION_NONE,
        }
        for row in rows:
            assert row["review_status"] == "review_required"
            assert tuple(sorted(row[IDENTITY_COLUMN])) == IDENTITY_KEYS_SORTED

    def test_ai_confidence_is_still_reported_separately_from_resolution(
        self, production, pdf_boundary, live
    ):
        result = pdf_boundary([_page(1, [
            _member("M1", EXACT_TOKEN, confidence=40),
            _member("M2", NONE_TOKEN, confidence=99),
        ])])
        rows = {row["mark"]: row for row in result.members}

        assert (rows["M1"]["confidence"], rows["M1"]["section_resolution"]) == (
            "low", RESOLUTION_EXACT)
        assert (rows["M2"]["confidence"], rows["M2"]["section_resolution"]) == (
            "high", RESOLUTION_NONE)

    def test_the_accepted_j4_and_j5_suites_still_pass(self, production):
        """The accepted suites, run as they are. No test in them was rewritten for
        J6 except the persisted-key pin, which J6's own addition widened."""
        modules = [
            REPO / "tests" / "test_real_world_j4_production_extraction_report_truth.py",
            REPO / "tests" / "test_real_world_j5_production_resolution_truth.py",
        ]
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *map(str, modules), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=str(REPO), capture_output=True, text=True, timeout=1800,
            env={**os.environ},
        )
        assert completed.returncode == 0, "\n".join(completed.stdout.strip().splitlines()[-3:])


# ==========================================================================
# K. Cross-lineage key pin — the two identity implementations must agree on
#    the persisted SHAPE, without being merged.
# ==========================================================================


class TestCrossLineageKeyPin:
    def test_the_persisted_keys_match_the_accepted_cad_chain_pin(self, production):
        """The milestone chain that already carries a reference identity pins the
        same three keys. Read here, in the test only — the two lineages are not
        merged and neither imports the other."""
        from app.cad_engine import fabricator_acceptance

        assert fabricator_acceptance._REFERENCE_DATA_KEYS == IDENTITY_KEYS

        projection = production.matcher_module.reference_data_projection(
            production.matcher_module.ReferenceIdentity(
                source_kind=SOURCE_LIVE_SUPABASE,
                identity_status=IDENTITY_UNVERSIONED,
                reference_data_digest=LIVE_DIGEST_AFTER_J2,
            )
        )
        assert tuple(sorted(projection)) == tuple(
            sorted(fabricator_acceptance._REFERENCE_DATA_KEYS)
        )

    def test_the_two_implementations_remain_separate_and_unconnected(self, production):
        from app.cad_engine import automation_pipeline

        assert automation_pipeline.ReferenceDataIdentity is not (
            production.matcher_module.ReferenceIdentity
        )
        assert automation_pipeline.reference_data_projection is not (
            production.matcher_module.reference_data_projection
        )
        # And no module on the production extraction path IMPORTS the CAD chain
        # at all. (Asserted on the imports, not on the text: one of these modules
        # names the adapter in a docstring.)
        for path in (PIPELINE_PATH, DXF_PATH, REPORT_PATH, MATCHER_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert not any(
                        alias.name.startswith("app.cad_engine") for alias in node.names
                    ), path
                elif isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("app.cad_engine"), path
