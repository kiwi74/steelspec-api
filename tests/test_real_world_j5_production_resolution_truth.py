"""
J5 — PRODUCTION SECTION-RESOLUTION TRUTH.

WHAT CHANGED
------------
`app/validation/rules.py` has always decided HOW a member's section resolved —
EXACT, SUFFIX_FALLBACK or NONE — and `app/pipeline.py` then threw that decision
away before persisting the member, keeping only a free-text note. Downstream,
the report could not tell a confirmed catalogue section from a refused
substitution or an unresolved token, so its status pill was computed from the AI
extraction confidence instead, and an unmatched member could render as matched.

J5 is the smallest production change that makes that decision survive:

  1. `validate_extraction` records `section_resolution` and
     `section_substituted_candidate` on EVERY member — the outcome of the
     authority boundary it had already applied, never a new decision.
  2. `consolidate_members` no longer reduces a mark to a single winner when its
     pages disagree about how the section resolved, so unresolved and refused
     evidence cannot be silently discarded.
  3. `app/pipeline.py` carries both values into the persisted row verbatim.
  4. `app/report/pdf_generator.py` decides member status from
     `section_resolution` (engineering) and reports AI extraction confidence
     separately, so neither can impersonate the other.
  5. The DXF row contract carries the same two fields.

REQUIRED PRODUCTION SCHEMA CHANGE — APPLIED (corrected by J8B Phase 13)
-----------------------------------------------------------------------
HISTORICAL, AS AUTHORED: the live `steel_members` table did NOT have these
columns (established read-only at the time: PostgreSQL `42703 undefined_column`
for both). The smallest additive migration is
`supabase/migrations/20260924000000_j5_section_resolution_truth.sql` — two
nullable text columns, no default, no NOT NULL, no destructive operation. Per the
J5 brief it was **NOT executed** by the implementation task, and NOTHING in this
module needs it to have run: persistence is intercepted at the existing
repository/parser seam, so the tests exercise the production row contract without
a database.

CURRENT STATE, RE-VERIFIED READ-ONLY 2026-09-24: the two columns ARE live. The
paragraph above is preserved because it records what was true when this module was
written; it is no longer true of the live table, and the migration header now
records both states. No test in this module changed: it judges the migration FILE
and the row contract, never the live schema.

Until an operator applied that migration, the production PDF path would have
failed on its `steel_members` insert. That deployment precondition has since been
met: the columns are live, so the insert no longer fails on them.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
No reference identity (that is J6). No CAD/fabrication orchestration. No
catalogue change, no database write.

The DXF NONE drop and the DXF non-EXACT `section_name` were both left exactly as
they were by J5, and named here as J7's scope. J7 has since made both changes
deliberately (Option C): the NONE member is persisted rather than discarded, and
a non-EXACT resolution carries its drawn identity in `section_name_raw` because
`section_name` is FK-bound to the catalogue. The assertions in this module that
encoded the J5 spelling of that contract state the change in place.

The boundary, doubles and fixtures are J4's, reused verbatim rather than
restated — see `tests/test_real_world_j4_production_extraction_report_truth.py`.
"""

from __future__ import annotations

import re
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

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "supabase" / "migrations" / "20260924000000_j5_section_resolution_truth.sql"
)

EXACT_TOKEN = j4.EXACT_TOKEN                    # 310UB46.2
SUFFIX_FALLBACK_TOKEN = j4.SUFFIX_FALLBACK_TOKEN       # 310UB40
SUFFIX_FALLBACK_CANDIDATE = j4.SUFFIX_FALLBACK_CANDIDATE  # 310UB40.4
NONE_TOKEN = j4.NONE_TOKEN                      # 250X90PFC
FLAT_ROWS = j4.FLAT_ROWS_ADDED_BY_J2
OTHER_EXACT_TOKEN = "250UB25.7"

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
        drawing_number="J5-DWG-001",
        drawing_title="J5 resolution truth",
        revision="A",
        raw_members=list(members),
        raw_connections=list(connections),
    )


@pytest.fixture()
def render_report(production, monkeypatch):
    """
    Renders the ACTUAL production report over an explicit set of member rows.

    Only the generator's read boundary is supplied — the generator itself is the
    production one, and produces the same bytes the customer report would.
    """
    def render(members, *, project=None):
        client = _ReportSupabase(
            project={
                "id": "j5-report", "name": "J5 report fixture",
                "unmatched_sections": [], "warnings": [],
                **(project or {}),
            },
            members=[dict(m) for m in members],
            connections=[],
        )
        monkeypatch.setattr(production.report, "supabase", client)
        return _pdf_text(production.report.generate_report_pdf("j5-report"))

    return render


def _status_text(production, resolution):
    return production.report._section_status_pill(resolution, production.report._styles()).text


def _green_markup(production):
    return production.report._hex(production.report.GREEN)


# ==========================================================================
# 1. EXACT — the drawn token IS a catalogue key.
# ==========================================================================


class TestExactPersistence:
    def test_exact_member_persists_an_exact_resolution(self, production, pdf_boundary, live):
        assert EXACT_TOKEN in live.names
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_EXACT

    def test_exact_member_has_no_refused_candidate(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert "section_substituted_candidate" in row
        assert row["section_substituted_candidate"] is None

    def test_exact_member_retains_the_catalogue_identity(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] == EXACT_TOKEN
        assert row["section_family"] == live.by_name[EXACT_TOKEN]["family"]
        assert row["weight_per_metre"] == live.by_name[EXACT_TOKEN]["weight_per_metre"]

    def test_the_resolution_is_carried_not_recomputed(self, production, pdf_boundary):
        """The value persisted is the one validate_extraction recorded — the
        production row construction reads it off the validated row."""
        validated = production.rules.validate_extraction(
            [_member("M1", EXACT_TOKEN)], production.matcher_module.SectionMatcher(),
        )
        validated_row = validated["members"][0]
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        persisted = _single_row(result.members, "M1")

        assert validated_row["section_resolution"] == RESOLUTION_EXACT
        assert persisted["section_resolution"] == validated_row["section_resolution"]
        assert persisted["section_substituted_candidate"] == validated_row["section_substituted_candidate"]


# ==========================================================================
# 2. SUFFIX_FALLBACK — the catalogue offered a DIFFERENT section.
# ==========================================================================


class TestSuffixFallbackPersistence:
    def test_suffix_fallback_persists_its_own_state(self, production, pdf_boundary, live):
        assert SUFFIX_FALLBACK_TOKEN not in live.names
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK

    def test_suffix_fallback_names_the_refused_candidate(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE

    def test_suffix_fallback_receives_no_catalogue_enrichment(self, production, pdf_boundary):
        """CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C).

        J5 recorded `section_name == drawing_token` for a refused suffix
        fallback. That is no longer the contract: live `section_name` is FK-bound
        to `steel_sections(name)`, and a refused token is by definition not a
        catalogue key, so writing it there is what the FK forbids. The drawn
        identity is preserved in `section_name_raw`, so nothing about this
        refusal is lost — only the column that carries it has moved.
        """
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] is None
        assert row["section_name_raw"] == SUFFIX_FALLBACK_TOKEN
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        assert row["review_status"] == "review_required"

    def test_suffix_fallback_refusal_stays_visible(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")
        expected = production.rules.SUBSTITUTION_NOTE.format(
            token=SUFFIX_FALLBACK_TOKEN, candidate=SUFFIX_FALLBACK_CANDIDATE,
        )

        assert row["notes"] == expected
        assert SUFFIX_FALLBACK_CANDIDATE in row["notes"]


# ==========================================================================
# 3. NONE — nothing resolved.
# ==========================================================================


class TestNonePersistence:
    def test_none_member_persists_a_none_resolution(self, production, pdf_boundary, live):
        assert NONE_TOKEN not in live.names
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_NONE

    def test_none_member_has_no_candidate_and_no_identity(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_substituted_candidate"] is None
        assert row["section_name"] is None
        assert row["section_name_raw"] == NONE_TOKEN
        assert row["review_status"] == "review_required"

    def test_none_evidence_stays_visible_on_the_project(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])

        assert result.project_row["unmatched_sections"] == [NONE_TOKEN]


# ==========================================================================
# 4. FLAT — the four live rows, through the genuine live matcher.
# ==========================================================================


class TestFlatPersistence:
    def test_all_four_live_flat_rows_resolve_exact(self, production, live):
        matcher = production.matcher_module.SectionMatcher()
        for name in FLAT_ROWS:
            assert name in live.names, name
            assert matcher.resolve(name).resolution == RESOLUTION_EXACT, name

    def test_flat_members_persist_exact_with_the_flat_family(self, production, pdf_boundary):
        members = [_member(f"M{i}", name, length_mm=1000.0) for i, name in enumerate(FLAT_ROWS, start=1)]
        result = pdf_boundary([_page(1, members)])

        assert len(result.members) == len(FLAT_ROWS)
        for row in result.members:
            assert row["section_resolution"] == RESOLUTION_EXACT
            assert row["section_substituted_candidate"] is None
            assert row["section_family"] == "FLAT"

    def test_the_cad_projection_is_never_persisted_as_a_family(self, production, pdf_boundary):
        """`FL` is the CAD engine's internal projection of FLAT. The catalogue
        has no FL family and no persisted row may claim one."""
        members = [_member(f"M{i}", name, length_mm=1000.0) for i, name in enumerate(FLAT_ROWS, start=1)]
        result = pdf_boundary([_page(1, members)])

        assert {row["section_family"] for row in result.members} == {"FLAT"}
        assert production.sections.cad_family_for("FLAT") == "FL"
        assert "FL" not in {row["section_family"] for row in result.members}


# ==========================================================================
# 5–8. The report's section status.
# ==========================================================================


class TestReportSectionStatus:
    def test_exact_renders_a_confirmed_status(self, production):
        text = _status_text(production, RESOLUTION_EXACT)

        assert "Confirmed" in text
        assert _green_markup(production) in text

    def test_suffix_fallback_does_not_render_as_matched(self, production):
        text = _status_text(production, RESOLUTION_SUFFIX_FALLBACK)

        assert "Substitution refused" in text
        assert "Matched" not in text
        assert _green_markup(production) not in text

    def test_none_does_not_render_as_matched(self, production):
        text = _status_text(production, RESOLUTION_NONE)

        assert "Unresolved" in text
        assert "Matched" not in text
        assert _green_markup(production) not in text

    def test_an_unrecorded_resolution_gets_a_neutral_state(self, production):
        for missing in (None, "", "SOMETHING_ELSE"):
            text = _status_text(production, missing)
            assert "Review" in text, missing
            assert "Confirmed" not in text, missing
            assert "Matched" not in text, missing
            assert _green_markup(production) not in text, missing


class TestReportSectionStatusInTheRenderedReport:
    def test_a_low_confidence_exact_match_still_reads_confirmed(self, production, pdf_boundary):
        """J4 pinned the inverse: a confirmed catalogue section rendered "Verify"
        purely because the AI was unsure. Engineering resolution now decides."""
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, confidence=40)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["confidence"] == "low"
        assert "Confirmed" in result.pdf_text
        assert "Low" in result.pdf_text          # the AI confidence is still reported
        assert "Verify" not in result.pdf_text

    def test_the_report_distinguishes_the_two_kinds_of_uncertainty(self, production, pdf_boundary):
        """One page states an exact catalogue section the AI was unsure about; the
        other states a token nothing resolved. The report must show them as
        different things."""
        result = pdf_boundary([
            _page(1, [
                _member("M1", EXACT_TOKEN, confidence=40),
                _member("M2", NONE_TOKEN, confidence=99),
            ])
        ])
        rows = {row["mark"]: row for row in result.members}

        assert rows["M1"]["section_resolution"] == RESOLUTION_EXACT
        assert rows["M1"]["confidence"] == "low"
        assert rows["M2"]["section_resolution"] == RESOLUTION_NONE
        assert rows["M2"]["confidence"] == "high"

        assert "Confirmed" in result.pdf_text
        assert "Unresolved" in result.pdf_text

    def test_an_unmatched_member_never_renders_the_confirmed_status(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN, confidence=99)])])

        assert "Unresolved" in result.pdf_text
        # The report states one confirmed row at most — none, here.
        assert "Confirmed" not in result.pdf_text

    def test_a_refused_substitution_never_renders_the_confirmed_status(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN, confidence=99)])])

        assert "Substitution refused" in result.pdf_text
        assert "Confirmed" not in result.pdf_text

    def test_a_legacy_row_without_a_resolution_renders_neutrally(self, production, render_report):
        """A row written before J5 has no section_resolution at all. The report
        must not infer a match for it."""
        legacy = {
            "id": "legacy-1", "mark": "M1", "section_name": EXACT_TOKEN,
            "section_name_raw": EXACT_TOKEN, "confidence": "high",
            "review_status": "extracted", "quantity": 1, "total_weight_kg": 55.4,
        }
        assert "section_resolution" not in legacy

        text = render_report([legacy])

        assert EXACT_TOKEN in text
        assert "Review" in text
        assert "Confirmed" not in text

    def test_ai_confidence_is_reported_separately_from_resolution(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, confidence=96)])])

        assert "Confirmed" in result.pdf_text
        assert "High" in result.pdf_text
        assert "AI Confidence" in result.pdf_text
        assert "Section Status" in result.pdf_text


# ==========================================================================
# 9–12. Consolidation must not discard unresolved or refused evidence.
# ==========================================================================


class TestConsolidationRetainsEvidence:
    def test_exact_and_none_keep_both_rows(self, production, pdf_boundary):
        """J4 pinned the defect: the NONE row vanished. It now survives."""
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 2
        by_resolution = {row["section_resolution"]: row for row in rows}
        assert set(by_resolution) == {RESOLUTION_EXACT, RESOLUTION_NONE}

    def test_exact_and_none_keep_the_unresolved_token_reachable(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])

        assert NONE_TOKEN in result.project_row["unmatched_sections"]
        assert NONE_TOKEN in result.pdf_text

    def test_exact_and_none_do_not_claim_confirmation(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        for row in rows:
            assert row["review_status"] == "review_required"
            assert "Confirmed on" not in (row["notes"] or "")

    def test_exact_and_none_raise_a_resolution_conflict(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])
        issues = [w for w in result.project_row["warnings"] if "resolves differently" in w]

        assert len(issues) == 1
        assert RESOLUTION_EXACT in issues[0]
        assert RESOLUTION_NONE in issues[0]

    def test_exact_and_suffix_fallback_keep_the_refusal(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", SUFFIX_FALLBACK_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")
        by_resolution = {row["section_resolution"]: row for row in rows}

        assert RESOLUTION_SUFFIX_FALLBACK in by_resolution
        refused = by_resolution[RESOLUTION_SUFFIX_FALLBACK]
        assert refused["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE
        assert refused["notes"] == production.rules.SUBSTITUTION_NOTE.format(
            token=SUFFIX_FALLBACK_TOKEN, candidate=SUFFIX_FALLBACK_CANDIDATE,
        )

    def test_exact_and_suffix_fallback_neither_row_is_promoted(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", SUFFIX_FALLBACK_TOKEN, length_mm=1200.0)]),
        ])

        for row in _rows_for(result.members, "M1"):
            assert row["review_status"] == "review_required"
            assert "Confirmed on" not in (row["notes"] or "")

    def test_exact_agreed_across_pages_still_consolidates_normally(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 1
        assert rows[0]["section_resolution"] == RESOLUTION_EXACT
        assert rows[0]["review_status"] == "extracted"
        assert rows[0]["notes"] == "Confirmed on 2 page(s): [1, 2]"

    def test_a_single_exact_page_is_untouched_by_consolidation(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)])])
        row = _single_row(result.members, "M1")

        assert row["review_status"] == "extracted"
        assert row["notes"] is None
        assert row["section_resolution"] == RESOLUTION_EXACT

    def test_genuinely_different_sections_keep_the_existing_distinct_row_behaviour(self, production, pdf_boundary):
        """Unchanged by J5: both rows kept, both held for review, and no
        reconciliation invented between the two drawings."""
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", OTHER_EXACT_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 2
        assert {row["section_name"] for row in rows} == {EXACT_TOKEN, OTHER_EXACT_TOKEN}
        assert {row["section_resolution"] for row in rows} == {RESOLUTION_EXACT}
        for row in rows:
            assert row["review_status"] == "review_required"

    def test_an_unresolved_mark_repeated_across_pages_is_not_promoted(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 1
        assert rows[0]["section_resolution"] == RESOLUTION_NONE
        assert rows[0]["review_status"] == "review_required"
        assert rows[0]["notes"] != ("Confirmed on 2 page(s): [1, 2]")


# ==========================================================================
# 13. DXF — the same persistence contract, as far as the architecture allows.
# ==========================================================================


class TestDxfResolutionPersistence:
    def _run_dxf(self, production, monkeypatch, tmp_path, token, name):
        recorder = _RecordingSupabase()
        monkeypatch.setattr(production.dxf, "supabase", recorder)
        path = _write_dxf(tmp_path / name, [token])
        production.dxf.parse_dxf_and_save(str(path), f"j5-dxf-{name}")
        return recorder.rows_inserted_into("steel_members")

    def test_dxf_exact_carries_an_exact_resolution(self, production, monkeypatch, tmp_path):
        rows = self._run_dxf(production, monkeypatch, tmp_path, EXACT_TOKEN, "exact.dxf")

        assert len(rows) == 1
        assert rows[0]["section_resolution"] == RESOLUTION_EXACT
        assert rows[0]["section_substituted_candidate"] is None

    def test_dxf_suffix_fallback_carries_the_refusal(self, production, monkeypatch, tmp_path):
        rows = self._run_dxf(production, monkeypatch, tmp_path, SUFFIX_FALLBACK_TOKEN, "fallback.dxf")

        assert len(rows) == 1
        assert rows[0]["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert rows[0]["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE
        assert rows[0]["weight_per_metre"] is None

    def test_dxf_none_is_now_persisted_without_a_catalogue_claim(
        self, production, monkeypatch, tmp_path
    ):
        """CONTRACT CHANGED DELIBERATELY BY MILESTONE J7.

        J5 recorded that the DXF parser `continue`d on a NONE resolution, so no
        row — and therefore no NONE resolution — was ever persisted from that
        path, and named the change as the J7 redesign. J7 has now made it: the
        member is persisted with section_name NULL and its drawn token in
        section_name_raw, so both paths record the same resolution vocabulary for
        the same state, and a member the drawing stated is no longer deleted.
        Nothing is enriched, substituted or fabricated to achieve that.
        """
        rows = self._run_dxf(production, monkeypatch, tmp_path, NONE_TOKEN, "none.dxf")

        assert len(rows) == 1
        assert rows[0]["section_resolution"] == RESOLUTION_NONE
        assert rows[0]["section_name"] is None
        # The matcher's OWN extracted token, verbatim — not the page text
        # ("250X90PFC" truncates to "90PFC" under the matcher's own regex).
        assert rows[0]["section_name_raw"] == "90PFC"
        assert rows[0]["section_substituted_candidate"] is None
        assert rows[0]["section_family"] is None
        assert rows[0]["weight_per_metre"] is None
        assert rows[0]["total_weight_kg"] is None
        assert rows[0]["review_status"] == "review_required"
        assert "90PFC" in rows[0]["notes"]

    def test_the_two_paths_use_one_resolution_vocabulary(self, production, live):
        """The PDF path's persisted values and the DXF path's come from the same
        three spellings, so a query that filters on them means the same thing
        whichever parser wrote the row."""
        import app.engineering_data.section_matcher as matcher_module

        assert matcher_module.RESOLUTION_EXACT == RESOLUTION_EXACT
        assert matcher_module.RESOLUTION_SUFFIX_FALLBACK == RESOLUTION_SUFFIX_FALLBACK
        assert matcher_module.RESOLUTION_NONE == RESOLUTION_NONE
        assert production.rules.RESOLUTION_EXACT == RESOLUTION_EXACT
        assert production.rules.RESOLUTION_NONE == RESOLUTION_NONE
        assert production.rules.SUFFIX_FALLBACK == RESOLUTION_SUFFIX_FALLBACK


# ==========================================================================
# 14. Backward compatibility.
# ==========================================================================


class TestBackwardCompatibility:
    def test_a_matcher_that_cannot_report_a_resolution_persists_none(self, production):
        """The legacy matcher contract (match() only) returns no resolution, and
        that absence is preserved as an absence — never guessed into EXACT."""
        class _LegacyMatcher:
            def __init__(self, matcher):
                self._matcher = matcher

            def match(self, raw_name):
                return self._matcher.match(raw_name)

        validated = production.rules.validate_extraction(
            [_member("M1", EXACT_TOKEN)],
            _LegacyMatcher(production.matcher_module.SectionMatcher()),
        )
        row = validated["members"][0]

        assert row["section_resolution"] is None
        assert row["section_substituted_candidate"] is None
        # The pre-J5 identity rule is unchanged for such a matcher.
        assert row["section_name"] == EXACT_TOKEN

    def test_rows_written_before_j5_consolidate_as_they_always_did(self, production):
        legacy_rows = [
            {"mark": "M1", "section_name": EXACT_TOKEN, "source_page": 1, "category": "steel_confirmed"},
            {"mark": "M1", "section_name": EXACT_TOKEN, "source_page": 2, "category": "steel_confirmed"},
        ]
        final, issues = production.rules.consolidate_members(legacy_rows)

        assert len(final) == 1
        assert final[0]["review_status"] == "extracted"
        assert final[0]["validation_note"] == "Confirmed on 2 page(s): [1, 2]"
        assert issues == []

    def test_the_persistence_api_is_unchanged(self, production):
        import inspect

        assert list(inspect.signature(production.repository.insert_members).parameters) == ["rows"]

    def test_a_legacy_row_is_readable_and_is_not_asserted_as_matched(self, production, render_report):
        rows = [
            {"id": "l1", "mark": "M1", "section_name": NONE_TOKEN, "section_name_raw": NONE_TOKEN,
             "confidence": "high", "review_status": "review_required", "quantity": 1},
            {"id": "l2", "mark": "M2", "section_name": EXACT_TOKEN, "section_name_raw": EXACT_TOKEN,
             "confidence": "high", "review_status": "extracted", "quantity": 1},
        ]
        text = render_report(rows)

        assert NONE_TOKEN in text and EXACT_TOKEN in text
        assert "Review" in text
        assert "Confirmed" not in text


# ==========================================================================
# 15. The authority invariant, restated over the persisted fields.
# ==========================================================================


class TestAuthorityInvariant:
    def test_exact_permits_catalogue_enrichment(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, length_mm=2000.0)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["section_family"] == live.by_name[EXACT_TOKEN]["family"]
        assert row["weight_per_metre"] == live.by_name[EXACT_TOKEN]["weight_per_metre"]
        assert row["total_weight_kg"] is not None

    def test_suffix_fallback_forbids_catalogue_enrichment(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN, length_mm=2000.0)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        # Named only as provenance — the candidate's own properties are absent.
        assert row["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE
        assert live.by_name[SUFFIX_FALLBACK_CANDIDATE]["weight_per_metre"] != row["weight_per_metre"]

    def test_none_forbids_catalogue_enrichment(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN, length_mm=2000.0)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == RESOLUTION_NONE
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None

    def test_the_persisted_fields_are_not_a_substitution_mechanism(self, production, pdf_boundary):
        """The candidate name is recorded and nothing else about it is used: it
        never becomes the member's identity, family or weight.

        Under the J7 Option C contract that invariant is stronger, not weaker:
        the FK-bound section_name is NULL for this row, so the candidate cannot
        be mistaken for an identity there even by accident, and the member's
        drawn identity is the FK-free section_name_raw."""
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN, length_mm=2000.0)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] != row["section_substituted_candidate"]
        assert row["section_name"] is None
        assert row["section_name_raw"] == SUFFIX_FALLBACK_TOKEN
        assert row["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE

    def test_the_recording_does_not_alter_the_authority_decision(self, production):
        """Recording the resolution must not change which rows get enriched.
        The same input yields the same identity, category and enrichment as the
        rule requires — the two new fields are purely additive."""
        matcher = production.matcher_module.SectionMatcher()
        validated = production.rules.validate_extraction(
            [
                _member("M1", EXACT_TOKEN),
                _member("M2", SUFFIX_FALLBACK_TOKEN),
                _member("M3", NONE_TOKEN),
            ],
            matcher,
        )
        rows = {row["mark"]: row for row in validated["members"]}

        assert rows["M1"]["category"] == "steel_confirmed"
        assert rows["M1"]["matched"] is not None
        assert rows["M2"]["category"] == "unmatched_steel"
        assert rows["M2"]["matched"] is None
        assert rows["M3"]["category"] == "unmatched_steel"
        assert rows["M3"]["matched"] is None
        assert rows["M1"]["section_resolution"] == RESOLUTION_EXACT
        assert rows["M2"]["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert rows["M3"]["section_resolution"] == RESOLUTION_NONE

    def test_no_invented_resolution_states_are_produced(self, production, live):
        """Only the three documented values ever reach a persisted row."""
        allowed = {RESOLUTION_EXACT, RESOLUTION_SUFFIX_FALLBACK, RESOLUTION_NONE, None}
        matcher = production.matcher_module.SectionMatcher()

        for token in (EXACT_TOKEN, SUFFIX_FALLBACK_TOKEN, NONE_TOKEN, *FLAT_ROWS):
            assert matcher.resolve(token).resolution in allowed, token


# ==========================================================================
# The migration artifact — minimal and additive. Named "created" rather than
# "unapplied" since J8B Phase 13: what these tests judge is the FILE, and the
# file's own header now records that the change is live.
# ==========================================================================


class TestTheMigrationIsMinimalAndAdditive:
    def test_the_migration_file_exists(self):
        assert MIGRATION_PATH.exists(), f"missing {MIGRATION_PATH}"

    def test_the_migration_is_purely_additive_and_nullable(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        statements = [
            line.strip().rstrip(";")
            for line in sql.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        joined = " ".join(statements).lower()

        assert joined.count("add column if not exists") == 2
        for column in ("section_resolution", "section_substituted_candidate"):
            assert f"add column if not exists {column} text" in joined, column

        # The properties the brief requires, asserted rather than assumed.
        assert "not null" not in joined
        assert "default" not in joined
        assert "drop " not in joined
        assert "alter column" not in joined
        assert "update " not in joined
        assert "insert " not in joined
        assert "delete " not in joined

    def test_the_two_columns_are_the_whole_change(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        tables = set(re.findall(r"(?i)\b(?:alter|create|drop)\s+table\s+([a-z_]+)", sql))

        assert tables == {"steel_members"}
