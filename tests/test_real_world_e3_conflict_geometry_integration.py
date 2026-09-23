"""
Milestone E3 — CONFLICT RESOLUTION INTO THE MEMBER PIPELINE: focused
end-to-end proofs.

Proven here, unit by unit:

  * the resolved section row: for each conflict, CAPTURE_AUTHORITATIVE
    assembles the captured evidence verbatim and
    LIVE_CATALOGUE_AUTHORITATIVE assembles the catalogue evidence
    supplied to the resolution — a NEW detached row, never a reference
    into the record and never a catalogue lookup; while blocked
    (unresolved, un-re-decided, KEEP_BOTH) no row exists;
  * the source-pinned matcher: matches only the selected source's own
    name, returns fresh detached copies, carries no catalogue and no
    fallback — a relookup against any other row is structurally
    impossible;
  * the genuine member pipeline: real_member_to_validated_member()
    (the existing adapter, unchanged) fed the pinned matcher, then
    generate_geometry() (the existing CAD engine, unchanged) — for
    both conflicts and both decisions, end to end;
  * the geometry-difference proof: the generated solid's measured
    edge lengths, bounding box and volume correspond exactly to the
    selected dimensions — capture and live decisions generate
    DIFFERENT profiles (Conflict A: flange/web thickness 15/8 vs
    12/7; Conflict B: flange thickness 11.8 vs 10.2), so the test
    fails if both decisions ever produce the same geometry;
  * the no-relookup negative proof: the E3 API has no matcher
    parameter and no catalogue access; an ambient matcher containing
    the alternative row can never be consulted; and the genuine
    ambient CatalogueMatcher on "250PFC" would yield the weight-only
    local row (generate_geometry then refuses to guess) — the exact
    hazard the pinned matcher removes;
  * unresolved conflicts block: unresolved, un-re-decided and
    KEEP_BOTH conflicts raise before any adapter call (the adapter
    and generate_geometry are spied and never invoked);
  * evidence preservation: the conflict record is unchanged and
    everything recoverable after geometry generation (both source
    rows, conflicting fields, E1 verdict, E2 decision, rationale, 7Z
    result, selected source); the real catalogue rows are
    byte-unchanged; no real fixture is mutated;
  * fail-closed validation: the genuine adapter's own rejections
    (untrustworthy mark, non-positive/boolean length) surface
    unchanged through the E3 path;
  * determinism and purity: repeated runs over the same record
    produce the same row, validated fields and generated dimensions;
    the module is deterministic, frozen, no-I/O, and referenced by no
    production module;
  * synthetic honesty: every test decision is an explicit synthetic
    test input — the real Selby evidence does not establish either
    side, and no real conflict is marked production-resolved.

No test here modifies any product module, the production matcher, the
DXF path, any existing gate, any fixture file or any real member
record.
"""

import ast
import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.cad_engine import catalogue_conflict_resolution as ccr
from app.cad_engine import resolved_conflict_geometry as rcd
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.exception_resolution import (
    STATUS_OPEN,
    TASK_RESOLVE_CONFLICT,
    HumanResolution,
)
from app.cad_engine.interface import (
    GeneratedMemberGeometry,
    ValidatedSteelMember,
    generate_geometry,
)
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.engineering_data.section_catalogue import CATALOGUE, CatalogueMatcher
from app.engineering_data.source_of_truth import (
    RESOLUTION_BLOCKED_REVIEW,
    VERDICT_CONFLICT_BLOCKED,
)

# The same two hard conflicts, with the same exact evidence, as the E2
# proofs (verbatim): Conflict A — captured 250X90PFC (flange 15.0 /
# web 8.0) vs live 250PFC (flange 12.0 / web 7.0); Conflict B —
# captured 310UB40 (flange 11.8) vs live 310UB40.4 (flange 10.2).
LIVE_250PFC = {
    "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
}
LIVE_310UB40_4 = {
    "name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
}
CAPTURE_250X90PFC = {
    "name": "250X90PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 15.0, "web_thickness": 8.0, "weight_per_metre": 35.5,
}
CAPTURE_310UB40 = {
    "name": "310UB40", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 11.8, "web_thickness": 6.1, "weight_per_metre": 40.4,
}

SYNTHETIC_NOTE = "synthetic E3 test input — the real Selby evidence does not establish " \
    "either side; no real engineering decision is made"

E3_MODULE_PATH = Path(rcd.__file__)


def _conflict_a():
    return ccr.build_catalogue_conflict(dict(CATALOGUE["250X90PFC"]), dict(LIVE_250PFC))


def _conflict_b():
    return ccr.build_catalogue_conflict(dict(CATALOGUE["310UB40"]), dict(LIVE_310UB40_4))


def _resolve(conflict, decision, rationale="the engineer checked the source drawing",
             evidence=SYNTHETIC_NOTE):
    """One explicit synthetic human resolution, addressed to the conflict's own task."""
    task = ccr.build_conflict_review_task(conflict)
    resolution = HumanResolution(
        task_id=task.task_id,
        task_type=task.task_type,
        answer_type=task.answer_type,
        answer=ccr.SourceAuthorityDecision(decision, rationale),
        evidence=evidence,
    )
    return ccr.apply_conflict_resolution(conflict, resolution)


def _redecided(conflict, decision):
    """The complete E2 lifecycle up to the 7Z re-decision."""
    return ccr.redecide_conflict_after_resolution(_resolve(conflict, decision))


def _edge_lengths(geometry):
    """The distinct measured edge lengths of the generated solid, rounded
    to 6 decimals (OCCT reports exact arithmetic values with floating
    noise)."""
    return {round(edge.Length(), 6) for edge in geometry.solid.val().Edges()}


def _bbox(geometry):
    box = geometry.solid.val().BoundingBox()
    return (box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)


def _pfc_area(depth, flange_width, flange_thickness, web_thickness):
    return flange_width * depth - (flange_width - web_thickness) * (depth - 2 * flange_thickness)


def _ub_area(depth, flange_width, flange_thickness, web_thickness):
    return 2 * flange_width * flange_thickness + web_thickness * (depth - 2 * flange_thickness)


# =============================================================================
# 1. The resolved section row — the selected source evidence, detached.
# =============================================================================
class TestResolvedSectionRow:
    def test_conflict_a_capture_row_is_captured_evidence_verbatim(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        assert rcd.build_resolved_section_row(rec) == CAPTURE_250X90PFC

    def test_conflict_a_live_row_is_live_evidence_verbatim(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        assert rcd.build_resolved_section_row(rec) == LIVE_250PFC

    def test_conflict_b_capture_row_is_captured_evidence_verbatim(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        assert rcd.build_resolved_section_row(rec) == CAPTURE_310UB40

    def test_conflict_b_live_row_is_live_evidence_verbatim(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        assert rcd.build_resolved_section_row(rec) == LIVE_310UB40_4

    def test_row_is_a_detached_copy(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        row = rcd.build_resolved_section_row(rec)
        row["flange_thickness"] = 999.0
        row.pop("name")
        assert rcd.build_resolved_section_row(rec) == CAPTURE_250X90PFC
        assert dict(rec.captured_values) == CAPTURE_250X90PFC

    def test_deterministic(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        assert rcd.build_resolved_section_row(rec) == rcd.build_resolved_section_row(rec)

    def test_unresolved_conflict_has_no_resolved_row(self):
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_section_row(_conflict_a())

    def test_resolved_but_not_redecided_has_no_resolved_row(self):
        rec = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        assert rec.resolution_status == ccr.RESOLUTION_STATUS_RESOLVED
        assert rec.resulting_decision is None
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_section_row(rec)

    def test_keep_both_stays_blocked(self):
        rec = _redecided(_conflict_a(),
                         ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
        assert rec.resulting_decision == RESOLUTION_BLOCKED_REVIEW
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_section_row(rec)

    def test_wrong_type_refused(self):
        with pytest.raises(TypeError):
            rcd.build_resolved_section_row({"captured_values": []})


# =============================================================================
# 2. The source-pinned matcher — no catalogue, no fallback, no other row.
# =============================================================================
class TestPinnedMatcher:
    def test_matches_only_the_selected_sources_own_name(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        matcher = rcd.build_resolved_matcher(rec)
        assert matcher.match("250X90PFC") == CAPTURE_250X90PFC
        assert matcher.match("250PFC") is None
        assert matcher.match("310UB40") is None
        assert matcher.match("") is None

    def test_live_matcher_returns_the_live_evidence_not_a_local_row(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        matcher = rcd.build_resolved_matcher(rec)
        assert matcher.match("250PFC") == LIVE_250PFC
        assert matcher.match("250X90PFC") is None

    def test_conflict_b_matcher_names(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        matcher = rcd.build_resolved_matcher(rec)
        assert matcher.match("310UB40.4") == LIVE_310UB40_4
        assert matcher.match("310UB40") is None

    def test_match_returns_fresh_copies(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        matcher = rcd.build_resolved_matcher(rec)
        first = matcher.match("250X90PFC")
        first["depth"] = 1.0
        assert matcher.match("250X90PFC") == CAPTURE_250X90PFC

    def test_no_matcher_while_blocked(self):
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_matcher(_conflict_a())
        rec = _redecided(_conflict_a(),
                         ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_matcher(rec)

    def test_selected_source_mapping_and_blocked_raise(self):
        capture = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        live = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        assert rcd.selected_source(capture) == rcd.SELECTED_SOURCE_CAPTURE
        assert rcd.selected_source(live) == rcd.SELECTED_SOURCE_CATALOGUE
        with pytest.raises(ValueError, match="blocked"):
            rcd.selected_source(_conflict_a())
        keep_both = _redecided(_conflict_a(),
                               ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
        with pytest.raises(ValueError, match="blocked"):
            rcd.selected_source(keep_both)


# =============================================================================
# 3. The genuine end-to-end pipeline, through the unchanged adapter and CAD engine.
# =============================================================================
class TestMemberPipelineEndToEnd:
    def test_conflict_a_capture_full_lifecycle(self):
        conflict = _conflict_a()
        task = ccr.build_conflict_review_task(conflict)
        assert task.task_type == TASK_RESOLVE_CONFLICT
        assert task.status == STATUS_OPEN
        assert task.allowed_choices == ccr.HUMAN_DECISIONS
        rec = _redecided(conflict, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="A-CAP", length_mm=1000)
        assert isinstance(result.validated_member, ValidatedSteelMember)
        assert isinstance(result.geometry, GeneratedMemberGeometry)
        assert result.selected_source == rcd.SELECTED_SOURCE_CAPTURE
        validated = result.validated_member
        assert validated.mark == "A-CAP"
        assert validated.section == "250X90PFC"
        assert validated.length_mm == 1000.0
        assert validated.material == "300"
        assert validated.validation_status == "extracted"
        assert validated.section_properties == CAPTURE_250X90PFC
        geometry = result.geometry
        assert geometry.mark == "A-CAP"
        assert geometry.section_name == "250X90PFC"
        assert geometry.section_family == "PFC"
        assert geometry.length_mm == 1000.0
        assert geometry.connection_count == 0

    def test_conflict_a_live_full_lifecycle(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="A-LIVE", length_mm=1000)
        assert result.selected_source == rcd.SELECTED_SOURCE_CATALOGUE
        validated = result.validated_member
        assert validated.section == "250PFC"
        assert validated.section_properties == LIVE_250PFC
        assert result.geometry.section_name == "250PFC"

    def test_conflict_b_both_decisions_through_the_pipeline(self):
        capture = _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        live = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        cap = rcd.resolve_member_geometry(capture, mark="B-CAP", length_mm=1500)
        liv = rcd.resolve_member_geometry(live, mark="B-LIVE", length_mm=1500)
        assert cap.validated_member.section == "310UB40"
        assert cap.validated_member.section_properties == CAPTURE_310UB40
        assert liv.validated_member.section == "310UB40.4"
        assert liv.validated_member.section_properties == LIVE_310UB40_4
        assert cap.geometry.section_family == "UB"
        assert liv.geometry.section_family == "UB"


# =============================================================================
# 4. The generated geometry corresponds exactly to the selected dimensions.
# =============================================================================
class TestGeometryCorrespondsToSelectedDimensions:
    def test_conflict_a_capture_edge_lengths(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="G-A-C", length_mm=1000)
        # PFC profile edges: flange_width, flange_thickness,
        # flange_width - web_thickness, depth - 2*flange_thickness, depth;
        # plus the member length. Capture: 90, 15, 82, 220, 250, 1000.
        assert _edge_lengths(result.geometry) == {90.0, 15.0, 82.0, 220.0, 250.0, 1000.0}

    def test_conflict_a_live_edge_lengths(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="G-A-L", length_mm=1000)
        # Live: 90, 12, 83, 226, 250, 1000 — same depth/flange_width/length,
        # DIFFERENT flange and web thicknesses (15/8 vs 12/7).
        assert _edge_lengths(result.geometry) == {90.0, 12.0, 83.0, 226.0, 250.0, 1000.0}

    def test_conflict_a_decisions_generate_different_profiles(self):
        capture = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        live = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        cap = rcd.resolve_member_geometry(capture, mark="G-A-C", length_mm=1000)
        liv = rcd.resolve_member_geometry(live, mark="G-A-L", length_mm=1000)
        cap_lens = _edge_lengths(cap.geometry)
        liv_lens = _edge_lengths(liv.geometry)
        assert cap_lens != liv_lens
        assert 15.0 in cap_lens and 15.0 not in liv_lens
        assert 12.0 in liv_lens and 12.0 not in cap_lens
        assert 82.0 in cap_lens and 82.0 not in liv_lens
        assert 83.0 in liv_lens and 83.0 not in cap_lens
        assert 220.0 in cap_lens and 220.0 not in liv_lens
        assert 226.0 in liv_lens and 226.0 not in cap_lens

    def test_conflict_a_bounding_box(self):
        # Both decisions share depth/flange_width/length, so the bounding
        # box is identical — the profile difference lives in the thicknesses.
        for decision in (ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
                         ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE):
            rec = _redecided(_conflict_a(), decision)
            result = rcd.resolve_member_geometry(rec, mark="G-A", length_mm=1000)
            assert _bbox(result.geometry) == pytest.approx(
                (0.0, 0.0, 0.0, 90.0, 250.0, 1000.0), abs=1e-9)

    def test_conflict_a_volume_matches_the_selected_cross_section(self):
        capture = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        live = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        cap = rcd.resolve_member_geometry(capture, mark="V-A-C", length_mm=1000)
        liv = rcd.resolve_member_geometry(live, mark="V-A-L", length_mm=1000)
        cap_expected = _pfc_area(250.0, 90.0, 15.0, 8.0) * 1000.0  # 4,460,000
        liv_expected = _pfc_area(250.0, 90.0, 12.0, 7.0) * 1000.0  # 3,742,000
        assert cap_expected == 4460000.0
        assert liv_expected == 3742000.0
        assert cap.geometry.solid.val().Volume() == pytest.approx(cap_expected, abs=0.01)
        assert liv.geometry.solid.val().Volume() == pytest.approx(liv_expected, abs=0.01)
        assert cap.geometry.solid.val().Volume() != pytest.approx(liv_expected)

    def test_conflict_b_capture_edge_lengths(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="G-B-C", length_mm=1500)
        # UB I-shape edges: flange_width, flange_thickness,
        # (flange_width - web_thickness)/2, depth - 2*flange_thickness,
        # plus the member length. Capture: 165, 11.8, 79.45, 280.4, 1500.
        assert _edge_lengths(result.geometry) == {165.0, 11.8, 79.45, 280.4, 1500.0}

    def test_conflict_b_live_edge_lengths(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="G-B-L", length_mm=1500)
        # Live: 165, 10.2, 79.45, 283.6, 1500 — flange thickness 10.2,
        # not the captured 11.8.
        assert _edge_lengths(result.geometry) == {165.0, 10.2, 79.45, 283.6, 1500.0}

    def test_conflict_b_flange_thickness_difference(self):
        capture = _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        live = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        cap = rcd.resolve_member_geometry(capture, mark="G-B-C", length_mm=1500)
        liv = rcd.resolve_member_geometry(live, mark="G-B-L", length_mm=1500)
        cap_lens = _edge_lengths(cap.geometry)
        liv_lens = _edge_lengths(liv.geometry)
        assert cap_lens != liv_lens
        assert 11.8 in cap_lens and 11.8 not in liv_lens
        assert 10.2 in liv_lens and 10.2 not in cap_lens
        assert 280.4 in cap_lens and 280.4 not in liv_lens
        assert 283.6 in liv_lens and 283.6 not in cap_lens

    def test_conflict_b_volume_matches_the_selected_cross_section(self):
        capture = _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        live = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        cap = rcd.resolve_member_geometry(capture, mark="V-B-C", length_mm=1500)
        liv = rcd.resolve_member_geometry(live, mark="V-B-L", length_mm=1500)
        cap_expected = _ub_area(304.0, 165.0, 11.8, 6.1) * 1500.0
        liv_expected = _ub_area(304.0, 165.0, 10.2, 6.1) * 1500.0
        assert cap.geometry.solid.val().Volume() == pytest.approx(cap_expected, abs=0.01)
        assert liv.geometry.solid.val().Volume() == pytest.approx(liv_expected, abs=0.01)
        assert cap_expected != liv_expected


# =============================================================================
# 5. The no-relookup negative proofs.
# =============================================================================
class TestNoRelookupNegativeProofs:
    def test_no_entry_point_takes_a_matcher(self):
        for func in (rcd.build_resolved_section_row, rcd.build_resolved_matcher,
                     rcd.build_synthetic_member_row, rcd.resolve_member_geometry):
            assert "matcher" not in inspect.signature(func).parameters

    def test_capture_resolution_ignores_an_ambient_index_with_the_alternative_row(self):
        # A live-shaped matcher/catalogue containing BOTH rows exists in the
        # test scope — but the E3 API has no parameter for it, so it cannot
        # be consulted. The resolved geometry must come from the captured
        # evidence alone.
        ambient = {"250X90PFC": dict(CAPTURE_250X90PFC), "250PFC": dict(LIVE_250PFC)}
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="N-C", length_mm=1000)
        assert result.validated_member.section_properties == ambient["250X90PFC"]
        assert result.validated_member.section_properties != ambient["250PFC"]
        assert 15.0 in _edge_lengths(result.geometry)
        assert 12.0 not in _edge_lengths(result.geometry)

    def test_live_resolution_uses_the_live_evidence_supplied_to_the_resolution(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="N-L", length_mm=1000)
        props = result.validated_member.section_properties
        assert props == LIVE_250PFC
        # ...not an unrelated catalogue row: the ambient local "250PFC" row
        # is weight-only and carries none of these geometry fields, and its
        # weight differs from the live evidence supplied to the resolution.
        local = CatalogueMatcher().match("250PFC")
        assert local["weight_per_metre"] == 35.5
        assert "depth" not in local
        assert props["weight_per_metre"] == 31.8
        assert 12.0 in _edge_lengths(result.geometry)
        assert 15.0 not in _edge_lengths(result.geometry)

    def test_the_ambient_matcher_hazard_is_real(self):
        # The genuine hazard this milestone removes: the same member row,
        # fed the genuine ambient CatalogueMatcher instead of the pinned
        # matcher, resolves "250PFC" to the weight-only local row — and the
        # genuine CAD engine then refuses to guess the missing geometry.
        live = _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        member_row = rcd.build_synthetic_member_row(live, mark="H", length_mm=1000)
        ambient_validated = real_member_to_validated_member(member_row, CatalogueMatcher())
        assert ambient_validated.section_properties == CatalogueMatcher().match("250PFC")
        assert "flange_thickness" not in ambient_validated.section_properties
        with pytest.raises(GeometryValidationError, match="Refusing to guess"):
            generate_geometry(ambient_validated)

    def test_pinned_matcher_never_falls_back_to_any_other_row(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        matcher = rcd.build_resolved_matcher(rec)
        # "310UB40" exists in the LOCAL catalogue with the captured values —
        # but the matcher must not consult it: it answers only from its own
        # pinned evidence.
        assert matcher.match("310UB40") == CAPTURE_310UB40
        assert matcher.match("310UB40.4") is None
        assert matcher.match("200UB30.4") is None


# =============================================================================
# 6. Unresolved conflicts block — the adapter is never invoked.
# =============================================================================
class TestUnresolvedBlocks:
    def _spy_both(self, monkeypatch):
        calls = {"adapter": 0, "generate": 0}

        def spy_adapter(*args, **kwargs):
            calls["adapter"] += 1
            return real_member_to_validated_member(*args, **kwargs)

        def spy_generate(*args, **kwargs):
            calls["generate"] += 1
            return generate_geometry(*args, **kwargs)

        monkeypatch.setattr(rcd, "real_member_to_validated_member", spy_adapter)
        monkeypatch.setattr(rcd, "generate_geometry", spy_generate)
        return calls

    @pytest.mark.parametrize("state", ["unresolved", "resolved_not_redecided", "keep_both"])
    def test_every_blocked_state_raises_before_any_adapter_call(self, monkeypatch, state):
        if state == "unresolved":
            conflict = _conflict_a()
        elif state == "resolved_not_redecided":
            conflict = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        else:
            conflict = _redecided(_conflict_a(),
                                  ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
        calls = self._spy_both(monkeypatch)
        with pytest.raises(ValueError, match="blocked"):
            rcd.resolve_member_geometry(conflict, mark="BLK", length_mm=1000)
        assert calls == {"adapter": 0, "generate": 0}

    def test_conflict_b_unresolved_blocks_too(self):
        with pytest.raises(ValueError, match="blocked"):
            rcd.resolve_member_geometry(_conflict_b(), mark="BLK", length_mm=1000)

    def test_no_synthetic_member_row_while_blocked(self):
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_synthetic_member_row(_conflict_a(), mark="M", length_mm=1000)

    def test_keep_both_is_never_an_applied_geometry_for_7aq(self):
        rec = _redecided(_conflict_a(),
                         ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
        acceptance = ccr.accept_resolved_conflict(rec)
        assert acceptance.accepted is False
        assert rec.resulting_decision == RESOLUTION_BLOCKED_REVIEW


# =============================================================================
# 7. Evidence preservation after geometry generation.
# =============================================================================
class TestEvidencePreservation:
    def test_conflict_a_capture_record_fully_recoverable_after_geometry(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="P-C", length_mm=1000)
        assert result.conflict is rec  # the same record, unchanged
        conflict = result.conflict
        assert dict(conflict.captured_values) == CAPTURE_250X90PFC
        assert dict(conflict.catalogue_values) == LIVE_250PFC
        assert conflict.conflicting_fields == (("flange_thickness", 15.0, 12.0),
                                               ("web_thickness", 8.0, 7.0))
        assert conflict.original_verdict == VERDICT_CONFLICT_BLOCKED
        assert conflict.original_resolution == RESOLUTION_BLOCKED_REVIEW
        assert conflict.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert conflict.human_rationale == "the engineer checked the source drawing"
        assert conflict.resulting_decision == ccr.RESULTING_DECISION_CAPTURE_GEOMETRY
        assert dict(result.resolved_section_row) == CAPTURE_250X90PFC

    def test_conflict_b_live_record_fully_recoverable_after_geometry(self):
        rec = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="P-L", length_mm=1500)
        conflict = result.conflict
        assert dict(conflict.captured_values) == CAPTURE_310UB40
        assert dict(conflict.catalogue_values) == LIVE_310UB40_4
        assert conflict.conflicting_fields == (("flange_thickness", 11.8, 10.2),)
        assert conflict.human_decision == ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE
        assert conflict.resulting_decision == ccr.RESULTING_DECISION_CATALOGUE_GEOMETRY
        assert result.selected_source == rcd.SELECTED_SOURCE_CATALOGUE
        assert dict(result.resolved_section_row) == LIVE_310UB40_4

    def test_source_rows_never_mutated(self):
        captured_a = dict(CATALOGUE["250X90PFC"])
        captured_b = dict(CATALOGUE["310UB40"])
        live_a = dict(LIVE_250PFC)
        _ = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        _ = _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        assert dict(CATALOGUE["250X90PFC"]) == captured_a
        assert dict(CATALOGUE["310UB40"]) == captured_b
        assert LIVE_250PFC == live_a

    def test_validated_section_properties_is_detached_from_the_record(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="P", length_mm=1000)
        result.validated_member.section_properties["flange_thickness"] = 999.0
        assert dict(rec.captured_values)["flange_thickness"] == 15.0
        assert rcd.build_resolved_section_row(rec)["flange_thickness"] == 15.0

    def test_result_container_is_frozen(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        result = rcd.resolve_member_geometry(rec, mark="P", length_mm=1000)
        with pytest.raises(FrozenInstanceError):
            result.selected_source = "CATALOGUE"


# =============================================================================
# 8. Fail-closed validation — the genuine adapter's rejections surface.
# =============================================================================
class TestFailClosedValidation:
    def _rec(self):
        return _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)

    def test_untrustworthy_mark_rejected(self):
        with pytest.raises(GeometryValidationError, match="no trustworthy mark"):
            rcd.resolve_member_geometry(self._rec(), mark="?", length_mm=1000)
        with pytest.raises(GeometryValidationError, match="no trustworthy mark"):
            rcd.resolve_member_geometry(self._rec(), mark="  ", length_mm=1000)

    def test_non_positive_length_rejected(self):
        with pytest.raises(GeometryValidationError, match="positive number"):
            rcd.resolve_member_geometry(self._rec(), mark="F", length_mm=0)
        with pytest.raises(GeometryValidationError, match="positive number"):
            rcd.resolve_member_geometry(self._rec(), mark="F", length_mm=-500)

    def test_non_numeric_length_rejected(self):
        with pytest.raises(GeometryValidationError, match="not numeric"):
            rcd.resolve_member_geometry(self._rec(), mark="F", length_mm=True)
        with pytest.raises(GeometryValidationError, match="not numeric"):
            rcd.resolve_member_geometry(self._rec(), mark="F", length_mm=None)

    def test_grade_defaults_and_custom_grade_flow_through(self):
        rec = self._rec()
        default = rcd.resolve_member_geometry(rec, mark="F", length_mm=1000)
        assert default.validated_member.material == "300"
        custom = rcd.resolve_member_geometry(rec, mark="F", length_mm=1000, grade="350")
        assert custom.validated_member.material == "350"

    def test_wrong_conflict_type_refused(self):
        with pytest.raises(TypeError):
            rcd.resolve_member_geometry({"conflict_id": "X"}, mark="F", length_mm=1000)


# =============================================================================
# 9. Determinism, purity, and no production wiring.
# =============================================================================
class TestDeterminismAndPurity:
    def test_repeated_runs_are_equal(self):
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        first = rcd.resolve_member_geometry(rec, mark="D", length_mm=1000)
        second = rcd.resolve_member_geometry(rec, mark="D", length_mm=1000)
        assert first.selected_source == second.selected_source
        assert first.resolved_section_row == second.resolved_section_row
        assert first.validated_member.section == second.validated_member.section
        assert first.validated_member.section_properties == second.validated_member.section_properties
        assert _bbox(first.geometry) == pytest.approx(_bbox(second.geometry), abs=1e-9)
        assert _edge_lengths(first.geometry) == _edge_lengths(second.geometry)
        assert first.geometry.solid.val().Volume() == pytest.approx(
            second.geometry.solid.val().Volume(), abs=0.01)

    def test_module_source_purity(self):
        source = E3_MODULE_PATH.read_text()
        forbidden = ("open(", "environ", "requests", "http", "supabase",
                     "random", "time.", "datetime", "os.")
        for token in forbidden:
            assert token not in source, f"forbidden token {token!r} found in module source"

    def test_module_import_whitelist(self):
        allowed = {
            "copy", "dataclasses", "typing",
            "app.cad_engine.catalogue_conflict_resolution",
            "app.cad_engine.interface",
            "app.cad_engine.real_member_adapter",
        }
        tree = ast.parse(E3_MODULE_PATH.read_text())
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in allowed, f"unexpected import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module in allowed, f"unexpected import: {node.module}"

    def test_public_api_is_frozen(self):
        assert set(rcd.__all__) == {
            "SELECTED_SOURCE_CAPTURE", "SELECTED_SOURCE_CATALOGUE", "SELECTED_SOURCES",
            "ResolvedSectionMatcher", "ResolvedMemberGeometry",
            "selected_source", "build_resolved_section_row", "build_resolved_matcher",
            "build_synthetic_member_row", "resolve_member_geometry",
        }

    def test_no_production_module_references_e3(self):
        refs = []
        for path in Path("app").rglob("*.py"):
            if path.resolve() == E3_MODULE_PATH.resolve():
                continue
            # Milestone E4 (resolved_conflict_drawing.py) is the downstream
            # integration milestone, explicitly designed to consume the E3
            # resolved geometry — its reference to this module IS the
            # integration, not production wiring. Milestone E5
            # (resolved_conflict_deliverable.py) is the NEXT such milestone:
            # it consumes the E4 result, and reads the selected-source
            # vocabulary (SELECTED_SOURCES and the two source labels) from
            # this module by import rather than redefining it — that
            # reference is the same integration, not production wiring.
            # Every other module must still never reference E3.
            if path.name in ("resolved_conflict_drawing.py", "resolved_conflict_deliverable.py"):
                continue
            if "resolved_conflict_geometry" in path.read_text():
                refs.append(str(path))
        assert refs == []


# =============================================================================
# 10. Synthetic honesty and real-fixture integrity.
# =============================================================================
class TestSyntheticHonestyAndFixtureIntegrity:
    def test_e3_module_never_writes_decisions(self):
        # The E3 API consumes a decision the E2 boundary recorded; nothing
        # here records or changes human_decision itself.
        conflict = _conflict_a()
        assert conflict.human_decision is None
        rec = _redecided(conflict, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        before = rec.human_decision
        result = rcd.resolve_member_geometry(rec, mark="S", length_mm=1000)
        assert result.conflict.human_decision == before
        assert "apply" not in rcd.__all__

    def test_real_catalogue_rows_byte_unchanged(self):
        assert dict(CATALOGUE["250X90PFC"]) == CAPTURE_250X90PFC
        assert dict(CATALOGUE["310UB40"]) == CAPTURE_310UB40
        # the local 250PFC row remains weight-only — no test or module adds
        # the live geometry to it
        assert CatalogueMatcher().match("250PFC") == {
            "name": "250PFC", "family": "PFC", "weight_per_metre": 35.5,
        }

    def test_no_real_conflict_marked_production_resolved(self):
        # The real rows carry no provenance claiming a production decision,
        # and the synthetic decisions live only on test-built records.
        assert "provenance" not in CATALOGUE["250X90PFC"]
        assert "provenance" not in CATALOGUE["310UB40"]
        rec = _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        assert rec.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert CATALOGUE["250X90PFC"]["name"] == "250X90PFC"
