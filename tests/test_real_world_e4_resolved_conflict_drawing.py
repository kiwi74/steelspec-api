"""
Milestone E4 — tests for RESOLVED CONFLICT -> FABRICATION DRAWING ->
VERIFICATION (app.cad_engine.resolved_conflict_drawing), the milestone
that runs an explicitly human-resolved catalogue conflict through the
existing fabrication-output machinery end to end:

    SOURCE CONFLICT -> 7AC -> 7AD human resolution -> 7Z re-decision (E2)
        -> E3 RESOLVED GEOMETRY  (resolved_conflict_geometry)
        -> 7AA  evaluate_reviewed_connection_for_automation()
               with the E3 source-pinned matcher and synthetic member
               rows built only from the selected source evidence
        -> 7AE  evaluate_fabrication_output_gate()
        -> 7AF  dispatch_fabrication_drawing()   -> a real PDF on disk
        -> 7AG  verify_drawing_artifact()        -> VERIFIED ARTIFACT

and the blocked lifecycle:

    unresolved / un-re-decided / KEEP_BOTH  -> the entry raises before
    ANY downstream call -> no pipeline, no gate permission, no dispatch,
    no PDF, no output directory, no verification -> NO ARTIFACT.

These tests prove:

  - The acceptance matrix for both conflicts and both decisions (capture
    and live): 7Z AUTO -> 7AE AUTO -> 7AF GENERATED -> 7AG VERIFIED,
    every value read from the genuine stage results.
  - The selected geometry is the geometry downstream (§4): the drawing
    path consumes the E3-resolved member rows and the E3 source-pinned
    matcher — nothing else — and the pipeline's reviewed assembly (the
    very assembly 7AG verifies the PDF against) carries the selected
    section's measured edge lengths and volumes: flange/web thicknesses
    15/8 vs 12/7 (Conflict A) and flange 11.8 vs 10.2 (Conflict B).
  - Drawing content is proven, not name-only (§9): the PDF text carries
    the selected section name, the member lengths and the attach
    surfaces, and the strongest existing structured drawing record —
    the assembly solids 7AG verifies against — proves the geometry-
    defining differences (the PDF format renders no per-section
    thickness dimension, so no new engineering dimension is invented).
  - The no-relookup rule across the whole path (§10): the only matcher
    in the chain is the source-pinned ResolvedSectionMatcher; an
    ambient catalogue with the competing row is never consulted; the
    live decision uses the live evidence supplied to the resolution,
    not the local weight-only row (whose hazard is proven real).
  - Unresolved produces nothing (§15): zero calls to 7AA/7AE/7AF/7AG,
    zero adapter invocations, no output directory, no artifact.
  - The genuine 7AE gate and 7AF dispatch interlock (§11-13): AUTO only
    on genuine 7Z AUTO + passing validation; a genuinely failing
    pipeline (broken plate) stays REVIEW/BLOCKED_REVIEW/NO_ARTIFACT and
    writes nothing; 7AG reads the real file and records its real hash.
  - Evidence preservation (§14): the final accepted artifact still
    shows a conflict existed and was explicitly resolved — the E1
    blocked verdict, the E2 human decision and rationale, the selected
    source, the resolved geometry, the drawing identity and the
    verification result are all recoverable from one frozen result; the
    two sources never look like they originally agreed.
  - Synthetic honesty (§16): no decision is ever recorded here, no
    HUMAN_CONFIRMED provenance is invented, no production acceptance
    record exists, and the real catalogue rows stay byte-unchanged.
  - Determinism (§18) with intentional-artifact-nondeterminism honesty:
    every stage decision, check and measured dimension is identical
    across runs; the PDF bytes differ only because the genuine generator
    stamps a creation date, and each run's recorded SHA-256 is the real
    hash of that run's bytes.
  - Import purity, frozen public API, and zero production wiring (§21):
    the E4 module is additive and nothing in app/ references it.

FIXTURE PROVENANCE: the two conflicts, their captured and live evidence
and their synthetic human resolutions are the SAME verbatim evidence as
the E3 proofs (imported from that test module — the real Selby evidence
does not establish either side, so every decision here is explicitly a
synthetic TEST input, never presented as a real engineering decision).
The connection-level reviewer values (plate, holes, location) are the
established synthetic 7E/7H/7N fixture values: connection context
around the conflict's members, not section geometry. A passing proof
does NOT mean production-ready or engineering-approved; it means the
chain executed and the records agree.
"""
import ast
import hashlib
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.cad_engine import catalogue_conflict_resolution as ccr
from app.cad_engine import resolved_conflict_drawing as e4
from app.cad_engine import resolved_conflict_geometry as rcd
from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
)
from app.cad_engine.drawing_output_verification import (
    CHECK_PASSED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.exception_resolution import HumanResolution
from app.cad_engine.placement import MemberPlacement
from app.engineering_data.section_catalogue import CATALOGUE, CatalogueMatcher
from app.engineering_data.source_of_truth import VERDICT_CONFLICT_BLOCKED
from tests.test_real_world_e3_conflict_geometry_integration import (
    CAPTURE_250X90PFC,
    CAPTURE_310UB40,
    LIVE_250PFC,
    LIVE_310UB40_4,
    SYNTHETIC_NOTE,
    _conflict_a,
    _conflict_b,
    _edge_lengths,
    _pfc_area,
    _redecided,
    _resolve,
    _ub_area,
)

E4_MODULE_PATH = Path(e4.__file__)

# The established synthetic 7E/7H/7N connection fixture values — connection
# context around the conflict's members, never section geometry.
PLATE = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
HOLES = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}

# The E3-measured geometry signatures at the E4 member lengths (E3's own
# numbers, reused verbatim): PFC A at L=1000, UB B at L=1500.
A_CAPTURE_EDGES = {90.0, 15.0, 82.0, 220.0, 250.0, 1000.0}
A_LIVE_EDGES = {90.0, 12.0, 83.0, 226.0, 250.0, 1000.0}
B_CAPTURE_EDGES = {165.0, 11.8, 79.45, 280.4, 1500.0}
B_LIVE_EDGES = {165.0, 10.2, 79.45, 283.6, 1500.0}


def _produce(conflict, output_dir, **overrides):
    """One E4 run through the genuine chain, with the established synthetic
    connection fixture (Conflict A lengths; overridden for Conflict B)."""
    kwargs = dict(
        output_dir=output_dir,
        plate=PLATE,
        holes=HOLES,
        location_z=994.0,
    )
    kwargs.update(overrides)
    return e4.produce_resolved_conflict_drawing(conflict, **kwargs)


def _a_capture():
    return _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)


def _a_live():
    return _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)


def _b_capture():
    return _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)


def _b_live():
    return _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)


def _b_kwargs():
    return dict(member_a_length_mm=1500.0, member_b_length_mm=1500.0, location_z=1494.0)


def _member_geometry(pipeline_result, which="member_a"):
    """The placed member solid 7AA generated and 7AG verified against."""
    return getattr(pipeline_result.reviewed_assembly, which).geometry


def _volume(geometry):
    return geometry.solid.val().Volume()


def _pdf_text(pdf_path):
    return PdfReader(str(pdf_path)).pages[0].extract_text()


# =============================================================================
# 1. The acceptance matrix — both conflicts, both decisions, every stage genuine.
# =============================================================================
class TestResolvedConflictChainAcceptance:
    @pytest.mark.parametrize("conflict_fn,decision,expected_name,expected_edges,kwargs", [
        ("a", ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "250X90PFC", A_CAPTURE_EDGES, {}),
        ("a", ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE, "250PFC", A_LIVE_EDGES, {}),
        ("b", ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "310UB40", B_CAPTURE_EDGES, _b_kwargs()),
        ("b", ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE, "310UB40.4", B_LIVE_EDGES, _b_kwargs()),
    ])
    def test_chain_reaches_verified(self, tmp_path, conflict_fn, decision,
                                    expected_name, expected_edges, kwargs):
        conflict = _redecided(
            _conflict_a() if conflict_fn == "a" else _conflict_b(), decision,
        )
        result = _produce(conflict, tmp_path / "run", **kwargs)

        pipeline, gate, dispatch, verification = (
            result.pipeline_result, result.gate_result,
            result.dispatch_result, result.verification_result,
        )
        # 7Z genuinely decided AUTO and the validation evidence is genuine.
        assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
        assert pipeline.validation_passed is True
        assert gate.decision == AUTOMATION_DECISION_AUTO
        assert dispatch.output_status == OUTPUT_STATUS_GENERATED
        assert verification.verification_status == VERIFICATION_STATUS_VERIFIED

        # The artifact is a real PDF: exists, non-empty, recorded hash matches.
        (pdf,) = dispatch.generated_files
        assert Path(pdf).exists() and Path(pdf).stat().st_size > 0
        assert pdf.name == f"{result.connection_id}-fabrication.pdf"
        assert Path(verification.artifact_path) == Path(pdf)
        assert verification.sha256 == hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
        assert verification.page_count >= 1

        # 7AG's own checks genuinely passed against the real file.
        for code in ("DRAWING_CONTENT_PRESENT", "IDENTITY_VERIFIABLE", "GEOMETRY_FIELDS_VERIFIABLE"):
            check = next(c for c in verification.checks if c.code == code)
            assert check.status == CHECK_PASSED, (code, check.detail)

        # The assembly the drawing was generated from and verified against is
        # the selected section, with the selected geometry.
        assert _member_geometry(pipeline).section_name == expected_name
        assert _edge_lengths(_member_geometry(pipeline)) == expected_edges
        assert _edge_lengths(_member_geometry(pipeline, "member_b")) == expected_edges

    @pytest.mark.parametrize("conflict_fn,expected_name", [
        ("a", "250X90PFC"), ("b", "310UB40"),
    ])
    def test_drawing_carries_the_connection_identity_and_selected_section(
            self, tmp_path, conflict_fn, expected_name):
        conflict = _redecided(
            _conflict_a() if conflict_fn == "a" else _conflict_b(),
            ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
        )
        kwargs = _b_kwargs() if conflict_fn == "b" else {}
        result = _produce(conflict, tmp_path / "run", **kwargs)
        (pdf,) = result.dispatch_result.generated_files
        text = _pdf_text(pdf)

        assert f"CONNECTION DETAIL — {result.connection_id}" in text
        assert f"FAB-{result.connection_id}" in text
        assert f"SECTION\n{expected_name}" in text  # both member rows
        assert text.count(f"{expected_name}") >= 2
        assert "MEMBER A\nE4-M1" in text and "MEMBER B\nE4-M2" in text
        length = "1000 mm" if conflict_fn == "a" else "1500 mm"
        assert f"LENGTH\n{length}" in text
        assert "ATTACH\nEND" in text and "ATTACH\nSTART" in text
        assert "HOLES: 4 × Ø22" in text
        assert "MATERIAL\nNOT SPECIFIED" in text

    def test_connection_identity_is_derived_from_the_conflict_record(self):
        conflict = _a_capture()
        assert e4.build_connection_identity(conflict) == \
            f"E4-CONN-{conflict.conflict_id}"


# =============================================================================
# 2. The selected geometry is the geometry downstream — drawing content proof.
# =============================================================================
class TestSelectedGeometryFlowsToTheArtifact:
    def test_conflict_a_flange_and_web_thickness_differences_are_in_the_assembly(self, tmp_path):
        capture = _produce(_a_capture(), tmp_path / "cap")
        live = _produce(_a_live(), tmp_path / "live")

        cap_edges = _edge_lengths(_member_geometry(capture.pipeline_result))
        liv_edges = _edge_lengths(_member_geometry(live.pipeline_result))
        # The geometry-defining difference (flange 15/8 vs 12/7) is measured in
        # the very assembly solids 7AG verified the PDF against.
        assert cap_edges == A_CAPTURE_EDGES and liv_edges == A_LIVE_EDGES
        assert 15.0 in cap_edges and 15.0 not in liv_edges
        assert 12.0 in liv_edges and 12.0 not in cap_edges
        assert 220.0 in cap_edges and 220.0 not in liv_edges
        assert 226.0 in liv_edges and 226.0 not in cap_edges

        assert _volume(_member_geometry(capture.pipeline_result)) == pytest.approx(
            _pfc_area(250.0, 90.0, 15.0, 8.0) * 1000.0, abs=0.01)  # 4,460,000
        assert _volume(_member_geometry(live.pipeline_result)) == pytest.approx(
            _pfc_area(250.0, 90.0, 12.0, 7.0) * 1000.0, abs=0.01)    # 3,742,000

    def test_conflict_b_flange_thickness_difference_is_in_the_assembly(self, tmp_path):
        capture = _produce(_b_capture(), tmp_path / "cap", **_b_kwargs())
        live = _produce(_b_live(), tmp_path / "live", **_b_kwargs())

        cap_edges = _edge_lengths(_member_geometry(capture.pipeline_result))
        liv_edges = _edge_lengths(_member_geometry(live.pipeline_result))
        assert cap_edges == B_CAPTURE_EDGES and liv_edges == B_LIVE_EDGES
        assert 11.8 in cap_edges and 11.8 not in liv_edges
        assert 10.2 in liv_edges and 10.2 not in cap_edges
        assert 280.4 in cap_edges and 280.4 not in liv_edges
        assert 283.6 in liv_edges and 283.6 not in cap_edges

        assert _volume(_member_geometry(capture.pipeline_result)) == pytest.approx(
            _ub_area(304.0, 165.0, 11.8, 6.1) * 1500.0, abs=0.01)
        assert _volume(_member_geometry(live.pipeline_result)) == pytest.approx(
            _ub_area(304.0, 165.0, 10.2, 6.1) * 1500.0, abs=0.01)

    def test_the_two_decisions_produce_two_different_artifacts(self, tmp_path):
        capture = _produce(_a_capture(), tmp_path / "cap")
        live = _produce(_a_live(), tmp_path / "live")

        (cap_pdf,) = capture.dispatch_result.generated_files
        (live_pdf,) = live.dispatch_result.generated_files
        assert cap_pdf != live_pdf
        assert hashlib.sha256(Path(cap_pdf).read_bytes()).hexdigest() != \
            hashlib.sha256(Path(live_pdf).read_bytes()).hexdigest()
        assert "SECTION\n250X90PFC" in _pdf_text(cap_pdf)
        assert "SECTION\n250PFC" in _pdf_text(live_pdf)
        # (The conflict id embedded in the drawing title names both sources —
        # the section lines must carry exactly the selected one.)
        assert "SECTION\n250X90PFC" not in _pdf_text(live_pdf)

    def test_the_downstream_path_consumed_exactly_the_e3_resolved_geometry(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        # The carried E3 resolved member (member A, same mark and length) and the
        # assembly member the pipeline produced from the E3-built row are the same
        # geometry: identical section properties source and identical measured solid.
        resolved = result.resolved_geometry
        assembled = _member_geometry(result.pipeline_result)
        assert resolved.validated_member.section_properties == dict(
            {key: value for key, value in resolved.resolved_section_row})
        assert assembled.section_name == resolved.geometry.section_name
        assert _edge_lengths(assembled) == _edge_lengths(resolved.geometry)
        assert _volume(assembled) == pytest.approx(
            _volume(resolved.geometry), abs=0.01)

    def test_member_rows_carry_only_the_selected_source_section_data(self, tmp_path):
        capture = _produce(_a_capture(), tmp_path / "cap")
        live = _produce(_a_live(), tmp_path / "live")

        cap_rows = dict(capture.member_rows)
        liv_rows = dict(live.member_rows)
        cap_a = {k: v for k, v in cap_rows["E4-M1"]}
        liv_a = {k: v for k, v in liv_rows["E4-M1"]}
        assert cap_a["section_name"] == "250X90PFC"
        assert liv_a["section_name"] == "250PFC"
        assert cap_a["section_family"] == liv_a["section_family"] == "PFC"
        # The rows carry no section dimensions of their own (same shape as the
        # existing dimensionless-row adapter proofs): every dimension comes from
        # the selected source evidence via the pinned matcher.
        assert not any(k in cap_a for k in ("depth", "flange_width", "flange_thickness", "web_thickness"))

    def test_member_placements_meet_at_the_shared_attachment_plane(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        placements = dict(result.member_placements)
        assert placements["E4-M1"] == MemberPlacement()
        assert placements["E4-M2"].z == 1000.0  # B's start face meets A's end face

    def test_custom_lengths_and_grade_flow_through(self, tmp_path):
        result = _produce(
            _a_capture(), tmp_path / "run",
            member_a_length_mm=1234.0, member_b_length_mm=5678.0,
            location_z=1228.0, grade="350L0",
        )
        rows = {k: {kk: vv for kk, vv in v} for k, v in result.member_rows}
        assert rows["E4-M1"]["length_mm"] == 1234.0
        assert rows["E4-M2"]["length_mm"] == 5678.0
        assert rows["E4-M1"]["grade"] == rows["E4-M2"]["grade"] == "350L0"
        (pdf,) = result.dispatch_result.generated_files
        text = _pdf_text(pdf)
        assert "LENGTH\n1234 mm" in text and "LENGTH\n5678 mm" in text


# =============================================================================
# 3. The no-relookup proof — the pinned matcher is the only matcher anywhere.
# =============================================================================
class TestNoRelookupProof:
    def test_the_only_matcher_handed_to_the_pipeline_is_the_source_pinned_matcher(
            self, tmp_path, monkeypatch):
        conflict = _a_live()
        received = {}

        def spy(package, *, member_rows=None, member_placements=None, section_matcher=None,
                require_confirmation=()):
            received["matcher"] = section_matcher
            received["rows"] = member_rows
            return _pipeline(
                package, member_rows=member_rows, member_placements=member_placements,
                section_matcher=section_matcher, require_confirmation=require_confirmation,
            )

        import app.cad_engine.automation_pipeline as pipeline_module
        _pipeline = pipeline_module.evaluate_reviewed_connection_for_automation
        monkeypatch.setattr(e4, "evaluate_reviewed_connection_for_automation", spy)

        result = _produce(conflict, tmp_path / "run")
        matcher = received["matcher"]
        assert isinstance(matcher, rcd.ResolvedSectionMatcher)
        assert matcher.resolved_name == "250PFC"
        assert dict(matcher.resolved_fields) == LIVE_250PFC
        # The pipeline recorded the honest catalogue_version: the pinned matcher
        # declares none, so None — never a substituted catalogue version.
        assert result.pipeline_result.catalogue_version is None

    def test_live_decision_never_falls_through_to_the_local_weight_only_row(self, tmp_path):
        result = _produce(_a_live(), tmp_path / "run")
        # The ambient hazard is real: the local 250PFC row is weight-only.
        assert CatalogueMatcher().match("250PFC") == {
            "name": "250PFC", "family": "PFC", "weight_per_metre": 35.5,
        }
        # But the E4 path never consults it: the assembled member carries the
        # LIVE evidence supplied to the resolution (flange 12 / web 7).
        assembled = _member_geometry(result.pipeline_result)
        assert _edge_lengths(assembled) == A_LIVE_EDGES
        assert 12.0 in _edge_lengths(assembled)
        assert dict(result.matcher.resolved_fields) == LIVE_250PFC

    def test_capture_decision_ignores_an_ambient_competing_row(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        assert dict(result.matcher.resolved_fields) == CAPTURE_250X90PFC
        assert _edge_lengths(_member_geometry(result.pipeline_result)) == A_CAPTURE_EDGES

    def test_pinned_matcher_matches_only_the_selected_name(self):
        conflict = _a_capture()
        pinned = rcd.build_resolved_matcher(conflict)
        assert pinned.match("250X90PFC") == CAPTURE_250X90PFC
        assert pinned.match("250PFC") is None
        assert pinned.match("310UB40") is None


# =============================================================================
# 4. Unresolved produces nothing — zero downstream calls, no directory, no PDF.
# =============================================================================
class TestUnresolvedProducesNothing:
    @pytest.mark.parametrize("blocked_fn", [
        lambda: _conflict_a(),                                    # no resolution at all
        lambda: _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE),  # no re-decision
        lambda: _redecided(_conflict_a(), ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE),
    ])
    def test_every_blocked_state_raises_before_any_downstream_call(
            self, tmp_path, monkeypatch, blocked_fn):
        conflict = blocked_fn()
        calls = {name: 0 for name in (
            "evaluate_reviewed_connection_for_automation",
            "evaluate_fabrication_output_gate",
            "dispatch_fabrication_drawing",
            "verify_drawing_artifact",
        )}
        for name in calls:
            monkeypatch.setattr(
                e4, name,
                lambda *args, _n=name, **kwargs: calls.__setitem__(_n, calls[_n] + 1),
            )
        adapter_calls = []
        monkeypatch.setattr(
            rcd, "real_member_to_validated_member",
            lambda *args, **kwargs: adapter_calls.append(1),
        )

        output_dir = tmp_path / "blocked"
        with pytest.raises(ValueError, match="blocked"):
            _produce(conflict, output_dir)

        assert all(count == 0 for count in calls.values()), calls
        assert adapter_calls == []  # the E3 member adapter was never invoked either
        assert not output_dir.exists()

    def test_no_resolved_row_and_no_matcher_exist_while_blocked(self):
        conflict = _conflict_a()
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_section_row(conflict)
        with pytest.raises(ValueError, match="blocked"):
            rcd.build_resolved_matcher(conflict)

    def test_blocked_means_no_result_object_at_all(self, tmp_path):
        with pytest.raises(ValueError, match="blocked"):
            _produce(_conflict_a(), tmp_path / "blocked")


# =============================================================================
# 5. The genuine gate, dispatch and verification — and their honest failure path.
# =============================================================================
class TestGenuineGateDispatchVerification:
    def test_auto_requires_the_genuine_7z_auto_and_validation_evidence(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        pipeline, gate = result.pipeline_result, result.gate_result
        # 7AE's AUTO was decided over the genuine 7Z AUTO and genuine passing
        # validation evidence — all three are the stage's own recorded values.
        assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
        assert pipeline.validation_passed is True
        assert pipeline.validation_result is not None
        assert pipeline.reviewed_assembly is not None
        assert gate.decision == AUTOMATION_DECISION_AUTO
        assert gate.connection_id == result.connection_id

    @pytest.mark.parametrize("broken_plate", [
        {"type": "end_plate", "width_mm": 180, "depth_mm": 250},  # no thickness
        {"type": "end_plate", "thickness_mm": 0, "width_mm": 180, "depth_mm": 250},
    ])
    def test_a_genuinely_failing_pipeline_stays_review_and_writes_nothing(
            self, tmp_path, broken_plate):
        output_dir = tmp_path / "broken"
        result = _produce(_a_capture(), output_dir, plate=broken_plate)

        # The genuine 7AA failed validation honestly -> 7Z REVIEW -> 7AE REVIEW
        # -> 7AF BLOCKED_REVIEW -> 7AG NO_ARTIFACT. Nothing is forced anywhere.
        assert result.pipeline_result.validation_passed is False
        assert result.pipeline_result.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW
        assert result.gate_result.decision == AUTOMATION_DECISION_REVIEW
        assert result.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        assert result.dispatch_result.generated_files == ()
        assert result.verification_result.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert not output_dir.exists()

    def test_the_recorded_hash_is_the_hash_of_the_real_artifact(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        (pdf,) = result.dispatch_result.generated_files
        assert result.verification_result.sha256 == \
            hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
        assert Path(result.verification_result.artifact_path) == Path(pdf)

    def test_verification_ran_against_the_pipelines_own_assembly(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        # 7AG was given the pipeline's OWN reviewed assembly — the geometry
        # strings it required are those of the selected section and marks.
        assembly = result.pipeline_result.reviewed_assembly
        assert assembly.member_a.geometry.mark == "E4-M1"
        assert assembly.member_b.geometry.mark == "E4-M2"
        assert assembly.member_a.geometry.section_name == "250X90PFC"
        assert result.verification_result.verification_status == VERIFICATION_STATUS_VERIFIED


# =============================================================================
# 6. Evidence preservation — the full audit trail from one frozen result.
# =============================================================================
class TestEvidencePreservation:
    def test_full_audit_trail_is_recoverable_from_the_result(self, tmp_path):
        conflict = _a_capture()
        result = _produce(conflict, tmp_path / "run")

        # (1) original captured source, (2) original catalogue source, (3) the
        # conflicting fields, (4) the E1 blocked verdict, (5) the E2 human
        # decision, (6) the human rationale.
        assert result.conflict is conflict
        assert dict(conflict.captured_values) == CAPTURE_250X90PFC
        assert dict(conflict.catalogue_values) == LIVE_250PFC
        assert conflict.conflicting_fields != ()
        assert conflict.original_verdict == VERDICT_CONFLICT_BLOCKED
        assert conflict.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert conflict.human_rationale
        # (7) E3 selected source, (8) the resolved geometry.
        assert result.selected_source == rcd.SELECTED_SOURCE_CAPTURE
        assert dict(result.resolved_geometry.resolved_section_row) == CAPTURE_250X90PFC
        # (9) drawing artifact identity, (10) verification result.
        (pdf,) = result.dispatch_result.generated_files
        assert Path(pdf).exists()
        assert result.verification_result.sha256 == \
            hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
        assert result.verification_result.verification_status == VERIFICATION_STATUS_VERIFIED

    def test_the_artifact_never_looks_like_the_sources_originally_agreed(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        conflict = result.conflict
        # The two sources still disagree on the record: the conflict is explicit.
        assert dict(conflict.captured_values) != dict(conflict.catalogue_values)
        for field, capture_value, catalogue_value in conflict.conflicting_fields:
            assert capture_value != catalogue_value
        # The drawing's section lines carry exactly ONE source's name — the
        # selected one — never a merge of the two (the conflict id in the
        # title names both sources, as the historical record must).
        (pdf,) = result.dispatch_result.generated_files
        text = _pdf_text(pdf)
        assert "SECTION\n250X90PFC" in text
        assert "SECTION\n250PFC" not in text
        assert result.selected_source == rcd.SELECTED_SOURCE_CAPTURE

    def test_conflict_b_live_audit_preserves_the_catalogue_selection(self, tmp_path):
        conflict = _b_live()
        result = _produce(conflict, tmp_path / "run", **_b_kwargs())
        assert result.selected_source == rcd.SELECTED_SOURCE_CATALOGUE
        assert dict(conflict.captured_values) == CAPTURE_310UB40
        assert dict(conflict.catalogue_values) == LIVE_310UB40_4
        (pdf,) = result.dispatch_result.generated_files
        assert "310UB40.4" in _pdf_text(pdf)
        assert _edge_lengths(_member_geometry(result.pipeline_result)) == B_LIVE_EDGES

    def test_source_rows_are_never_mutated(self, tmp_path):
        for run in (
            lambda: _produce(_a_capture(), tmp_path / "cap"),
            lambda: _produce(_a_live(), tmp_path / "live"),
            lambda: _produce(_b_capture(), tmp_path / "bc", **_b_kwargs()),
            lambda: _produce(_b_live(), tmp_path / "bl", **_b_kwargs()),
        ):
            run()
        assert dict(CATALOGUE["250X90PFC"]) == CAPTURE_250X90PFC
        assert dict(CATALOGUE["310UB40"]) == CAPTURE_310UB40
        assert CatalogueMatcher().match("250PFC") == {
            "name": "250PFC", "family": "PFC", "weight_per_metre": 35.5,
        }


# =============================================================================
# 7. Determinism, purity, and no production wiring.
# =============================================================================
class TestDeterminismAndPurity:
    def test_repeated_runs_are_equal_except_the_recorded_artifact_hash(self, tmp_path):
        output_dir = tmp_path / "run"
        first = _produce(_a_capture(), output_dir)
        (pdf,) = first.dispatch_result.generated_files
        first_bytes = Path(pdf).read_bytes()
        first_text = _pdf_text(pdf)
        second = _produce(_a_capture(), output_dir)
        second_bytes = Path(pdf).read_bytes()

        # Every stage decision, check and measured dimension is identical.
        assert first.pipeline_result.automation_gate_result == \
            second.pipeline_result.automation_gate_result
        assert first.gate_result.decision == second.gate_result.decision == "AUTO"
        assert first.dispatch_result.output_status == second.dispatch_result.output_status
        assert first.verification_result.verification_status == \
            second.verification_result.verification_status
        assert _edge_lengths(_member_geometry(first.pipeline_result)) == \
            _edge_lengths(_member_geometry(second.pipeline_result))
        assert first.verification_result.page_count == second.verification_result.page_count
        # Every check is identical except the ARTIFACT_HASH check, whose detail
        # records the (per-run different) SHA-256 of that run's real bytes.
        for a, b in zip(first.verification_result.checks, second.verification_result.checks):
            if a.code == "ARTIFACT_HASH":
                assert a.status == b.status == CHECK_PASSED
                continue
            assert a == b
        assert first_text == _pdf_text(pdf)

        # The intentional artifact nondeterminism is the generator's own creation
        # date: two generations are two different files, and each run's recorded
        # SHA-256 is the real hash of the bytes that run produced.
        assert hashlib.sha256(first_bytes).hexdigest() == first.verification_result.sha256
        assert hashlib.sha256(second_bytes).hexdigest() == second.verification_result.sha256

    def test_module_source_purity(self):
        source = E4_MODULE_PATH.read_text()
        forbidden = ("open(", "environ", "requests", "http", "supabase",
                     "random", "time.", "datetime", "os.")
        for token in forbidden:
            assert token not in source, f"forbidden token {token!r} found in module source"
        # No conflict evidence or section dimension may be hard-coded anywhere.
        lowered = source.lower()
        for token in ("250x90pfc", "250pfc", "310ub", "11.8", "10.2",
                      "15.0", "12.0", "8.0", "7.0", "6.1", "22.0"):
            assert token not in lowered, token

    def test_module_import_whitelist(self):
        allowed = {
            "copy", "dataclasses", "pathlib", "typing",
            "app.cad_engine.automation_pipeline",
            "app.cad_engine.catalogue_conflict_resolution",
            "app.cad_engine.connection_review_package",
            "app.cad_engine.drawing_dispatch",
            "app.cad_engine.drawing_output_verification",
            "app.cad_engine.fabrication_output_gate",
            "app.cad_engine.placement",
            "app.cad_engine.resolved_conflict_geometry",
            "app.cad_engine.reviewed_connection_specification",
        }
        tree = ast.parse(E4_MODULE_PATH.read_text())
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in allowed, f"unexpected import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module in allowed, f"unexpected import: {node.module}"

    def test_public_api_is_frozen(self):
        assert set(e4.__all__) == {
            "E4_MEMBER_MARK_A", "E4_MEMBER_MARK_B",
            "ResolvedConflictDrawingResult",
            "build_connection_identity", "produce_resolved_conflict_drawing",
        }

    def test_no_production_module_references_e4(self):
        refs = []
        for path in Path("app").rglob("*.py"):
            if path.resolve() == E4_MODULE_PATH.resolve():
                continue
            # Milestone E5 (resolved_conflict_deliverable.py) is the
            # downstream deliverable milestone, explicitly designed to
            # consume this milestone's VERIFIED drawing result — its
            # reference to this module IS the integration, not production
            # wiring (the same exemption the E3 proofs make for E4). Every
            # other module must still never reference E4.
            if path.name == "resolved_conflict_deliverable.py":
                continue
            if "resolved_conflict_drawing" in path.read_text():
                refs.append(str(path))
        assert refs == []

    def test_production_files_untouched(self):
        protected = [
            Path("app/pipeline.py"),
            Path("app/main.py"),
            Path("app/cad_engine/interface.py"),
            Path("app/ai_analysis/pdf_vision_analyzer.py"),
            Path("app/drawing_generator/interface.py"),
        ]
        for path in protected:
            assert "resolved_conflict" not in path.read_text(), path


# =============================================================================
# 8. Synthetic honesty and real-fixture integrity.
# =============================================================================
class TestSyntheticHonestyAndFixtureIntegrity:
    def test_the_module_never_writes_a_decision(self, tmp_path):
        conflict = _a_capture()
        before = (conflict.human_decision, conflict.human_rationale, conflict.resulting_decision)
        _produce(conflict, tmp_path / "run")
        assert (conflict.human_decision, conflict.human_rationale,
                conflict.resulting_decision) == before
        assert "apply" not in e4.__all__ and "resolve" not in e4.__all__

    def test_no_human_confirmed_provenance_is_invented(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        supplement = result.package.supplement
        # Every required field is HUMAN_SUPPLEMENTED (the reviewer supplied it);
        # nothing is claimed to be an AI value a human confirmed.
        assert supplement.confirmed_ai_fields == frozenset()
        assert supplement.review_status == "approved"
        assert "HUMAN_CONFIRMED" not in str(supplement)

    def test_no_production_acceptance_record_exists(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "run")
        assert not hasattr(result, "acceptance")
        assert "acceptance" not in e4.__all__

    def test_real_catalogue_rows_byte_unchanged(self):
        assert dict(CATALOGUE["250X90PFC"]) == CAPTURE_250X90PFC
        assert dict(CATALOGUE["310UB40"]) == CAPTURE_310UB40
        assert CatalogueMatcher().match("250PFC") == {
            "name": "250PFC", "family": "PFC", "weight_per_metre": 35.5,
        }

    def test_the_human_resolution_carries_the_synthetic_note(self):
        # The decision on this record was supplied with the synthetic note as its
        # stated evidence — a real engineering decision is never claimed.
        conflict = _conflict_a()
        task = ccr.build_conflict_review_task(conflict)
        resolution = HumanResolution(
            task_id=task.task_id,
            task_type=task.task_type,
            answer_type=task.answer_type,
            answer=ccr.SourceAuthorityDecision(
                ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
                "the engineer checked the source drawing",
            ),
            evidence=SYNTHETIC_NOTE,
        )
        resolved = ccr.apply_conflict_resolution(conflict, resolution)
        assert resolution.evidence == SYNTHETIC_NOTE
        assert resolution.answer.rationale
        assert resolved.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE


# =============================================================================
# 9. Loud input validation.
# =============================================================================
class TestLoudInputValidation:
    def test_wrong_conflict_type_refused(self, tmp_path):
        with pytest.raises(TypeError):
            _produce({"conflict_id": "X"}, tmp_path / "run")

    def test_non_path_output_dir_refused(self, tmp_path):
        with pytest.raises(TypeError):
            _produce(_a_capture(), "not-a-path")

    def test_build_connection_identity_refuses_non_conflicts(self):
        with pytest.raises(TypeError):
            e4.build_connection_identity("CONFLICT-X")
