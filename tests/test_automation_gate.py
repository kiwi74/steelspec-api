"""
Milestone 7Z — tests for the automation gate (app.cad_engine.automation_gate).

The gate is a read-only routing decision over the existing 7W/7X review
pipeline. These tests prove, in order of the milestone's requirements:

  - AUTO only for a fully reviewed connection (approved status, human
    provenance on all six engineering fields, known members, complete
    and valid geometry) with genuinely passing downstream validation —
    the validation evidence in the AUTO test is REAL: the same reviewed
    values build the established 7O assembly and pass the real 7R gate.
  - REVIEW for every information gap or inconsistency, each with its
    stable machine-readable blocker code: unresolved member identity,
    missing hole diameter (with explicit proof that a nominal bolt size
    is never converted into a hole diameter), AI-only provenance on a
    required field, invalid/non-finite numerics, unresolved
    attachments, conflicts, malformed AI fields, failed validation.
  - CONFIRM only for a 7V-complete connection awaiting explicit human
    confirmation (or downstream-validation evidence) — never for
    anything missing or invalid.
  - The gate is read-only, deterministic, scoreless, makes no
    engineering-approval claim, imports only the review layers plus the
    standard library, and never calls CAD/validation/drawing code.
  - The REAL Arkles capture (three raw AI candidates naming timber
    members and nominal bolt sizes) flows through the existing 7Y/7X
    pathway to REVIEW for every candidate — never AUTO, never CONFIRM,
    with nothing reinterpreted. No Claude, no network calls anywhere.

FIXTURE PROVENANCE: the reviewed supplement used by the synthetic tests
mirrors the established 7E/7H/7N fixture values exactly (180x250x12mm
end plate, 4xO22mm holes, END position, END/START attachments,
z=3994) by BUILDING it from those fixtures' own record helpers — it is
TEST / HUMAN-REVIEWED / SUPPLEMENTED data, never presented as an AI
extraction. The raw AI-shaped objects in the REVIEW tests are SYNTHETIC
(actual schema keys, not produced by any AI), except the real Arkles
capture tests, which use only the captured values.
"""
import ast
import copy
import dataclasses
from pathlib import Path

import pytest

import app.cad_engine.automation_gate as automation_gate
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_CONFLICT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_MALFORMED_FIELDS,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_PROVENANCE,
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_DECISIONS,
    AUTOMATION_GATE_SCOPE_STATEMENT,
    AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED,
    AUTOMATION_REASON_CONFIRMABLE,
    AUTOMATION_REASON_CONFIRMATION_REQUESTED,
    AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED,
    AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE,
    AUTOMATION_WARNING_CONFIRMATION_DEFERRED,
    AUTOMATION_WARNING_HISTORICAL_AI_MARKS,
    AutomationFinding,
    AutomationGateResult,
    evaluate_automation_gate,
    evaluate_candidate_automation,
)
from app.cad_engine.connection_review_package import (
    ConnectionReviewSupplement,
    ai_connection_to_extraction,
    build_review_report,
    build_reviewed_connection_specification,
    create_review_package,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.reviewed_connection_specification import REVIEW_STATUS_APPROVED
from app.cad_engine.reviewed_connection_validation_gate import (
    VALIDATED_LAYERS,
    validate_reviewed_connection_assembly,
)
from tests.test_project_extraction_intake import (
    CAPTURE_PATH,
    flatten_reference,
    load_capture,
    needs_real_capture,
    real_known_marks,
)
from tests.test_real_multi_member_connection import make_multi_member_connection_record
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record
from tests.test_two_member_connection import make_test_reviewed_location_record

KNOWN_MARKS = ("REAL-UB-CAD-001", "L2")


def _plate(**changes) -> dict:
    plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    plate.update(changes)
    return plate


def _holes(**changes) -> dict:
    holes = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}
    holes.update(changes)
    return holes


def _location(**changes) -> dict:
    location = {"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    location.update(changes)
    return location


def _reviewed_supplement(**overrides) -> ConnectionReviewSupplement:
    """The fully reviewed supplement, built from the established 7E/7H/7N fixture records so the
    gate's specification and the 7O/7R assembly the AUTO test validates are the SAME values."""
    connection_record = make_multi_member_connection_record()
    location_record = make_test_reviewed_location_record()
    detail_record = make_reviewed_connection_detail_record()
    supplement = ConnectionReviewSupplement(
        connection_id=connection_record["connection_id"],
        review_status=REVIEW_STATUS_APPROVED,
        connected_member_marks=list(connection_record["connected_member_marks"]),
        position=connection_record["position"],
        plate=dict(connection_record["plates"][0]),
        holes={key: connection_record["bolts"][0][key] for key in
               ("quantity", "diameter_mm", "vertical_spacing_mm", "horizontal_spacing_mm")},
        location={key: location_record[key] for key in
                  ("x", "y", "z", "rotation_x", "rotation_y", "rotation_z")},
        attachments=[dict(a) for a in detail_record["attachments"]],
    )
    if overrides:
        supplement = dataclasses.replace(supplement, **overrides)
    return supplement


def _reviewed_package(**supplement_overrides):
    """A SYNTHETIC AI extraction (actual schema keys, not produced by any AI) plus the fully
    reviewed supplement above, with the project's known member marks supplied."""
    extraction = ai_connection_to_extraction({
        "detail_reference": "D15", "grid_reference": "A-B/2",
        "connects_members": ["REAL-UB-CAD-001", "L2"],
        "connection_type": "bolted",
        "bolts": [{"quantity": None, "size": "M20", "grade": "8.8"}],
        "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
        "welds": [], "confidence": 72,
    }, source_page=15)
    return create_review_package(
        extraction, _reviewed_supplement(**supplement_overrides), known_member_marks=KNOWN_MARKS,
    )


def _blocker_codes(result):
    return {b.code for b in result.blockers}


# =============================================================================
# AUTO
# =============================================================================
def test_fully_reviewed_connection_with_passing_downstream_validation_is_AUTO():
    package = _reviewed_package()
    report = build_review_report(package)
    assert report.ready_for_pipeline  # fixture sanity: 7W + 7V accept the reviewed specification

    result = evaluate_automation_gate(package, validation_passed=True)
    assert result.decision == AUTOMATION_DECISION_AUTO
    assert result.blockers == ()
    assert result.reasons[0].code == AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED
    assert any(w.code == AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE for w in result.warnings)
    assert "validation evidence: passed" in result.evidence_summary
    assert "7V completeness: passed" in result.evidence_summary

    # The validation evidence is genuine, not assumed: the SAME reviewed values (the supplement
    # mirrors the established 7E/7H/7N fixture) build the real assembly and pass the real 7R gate.
    assembly, *_ = _build_primary_assembly()
    validation = validate_reviewed_connection_assembly(assembly)
    assert validation.layers_validated == VALIDATED_LAYERS


def test_auto_is_automation_readiness_not_engineering_approval():
    result = evaluate_automation_gate(_reviewed_package(), validation_passed=True)
    assert result.decision == AUTOMATION_DECISION_AUTO
    all_text = " ".join(f.message for f in result.blockers + result.reasons + result.warnings)
    assert "NOT engineering approval" in result.reasons[0].message
    assert "fabrication-ready" not in all_text
    for phrase in ("engineering approval", "fabrication readiness", "code compliance"):
        assert phrase in AUTOMATION_GATE_SCOPE_STATEMENT, phrase


# =============================================================================
# CONFIRM — complete, internally consistent, held for explicit human confirmation
# =============================================================================
def test_complete_connection_with_explicit_confirmation_requests_is_CONFIRM():
    """
    Every required engineering datum is present and 7V-complete — nothing is missing, ambiguous or
    invalid — so this is NOT REVIEW. But automation must be held for explicit human confirmation of
    a low-risk, reused-pattern assumption (the milestone's CONFIRM case): a reused approved end-plate
    pattern a human must confirm against the project register before automation proceeds.
    """
    package = _reviewed_package()
    assert build_review_report(package).ready_for_pipeline

    item = "Reused approved end-plate pattern CONN-ENDPLATE-2: confirm against the project connection register."
    result = evaluate_automation_gate(package, require_confirmation=(item,), validation_passed=True)
    assert result.decision == AUTOMATION_DECISION_CONFIRM
    assert result.blockers == ()
    assert result.reasons[0].code == AUTOMATION_REASON_CONFIRMATION_REQUESTED
    assert item in result.reasons[0].message
    assert result.reasons[1].code == AUTOMATION_REASON_CONFIRMABLE


def test_complete_connection_without_validation_evidence_is_CONFIRM_then_AUTO_with_evidence():
    package = _reviewed_package()
    held = evaluate_automation_gate(package)  # no downstream-validation evidence supplied
    assert held.decision == AUTOMATION_DECISION_CONFIRM
    assert held.blockers == ()
    assert held.reasons[0].code == AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED
    assert "validation evidence: not supplied" in held.evidence_summary

    released = evaluate_automation_gate(package, validation_passed=True)
    assert released.decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# REVIEW — member identity
# =============================================================================
def test_unresolved_member_identity_is_REVIEW():
    known = KNOWN_MARKS

    # (a) AI-extracted marks matching no known project member — e.g. timber references.
    extraction = ai_connection_to_extraction({"connects_members": ["stringer 140x45 H4 SG8", "structure"]})
    result = evaluate_automation_gate(create_review_package(extraction, known_member_marks=known))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_MEMBER_IDENTITY in _blocker_codes(result)
    identity = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_MEMBER_IDENTITY)
    assert "stringer 140x45 H4 SG8" in identity.message

    # (b) member marks that could not be checked at all (no known_member_marks supplied) — even a
    # passing downstream validation cannot rescue unresolved identity.
    package = _reviewed_package()
    unchecked = create_review_package(package.extraction, package.supplement, known_member_marks=None)
    result = evaluate_automation_gate(unchecked, validation_passed=True)
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert _blocker_codes(result) == {AUTOMATION_BLOCKER_MEMBER_IDENTITY}

    # (c) no member marks anywhere.
    empty = ai_connection_to_extraction({"connects_members": []})
    result = evaluate_automation_gate(create_review_package(empty, known_member_marks=known))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_MEMBER_IDENTITY in _blocker_codes(result)


# =============================================================================
# REVIEW — hole diameter: a nominal bolt size is NEVER a hole diameter
# =============================================================================
def test_missing_hole_diameter_is_REVIEW_and_nominal_size_never_becomes_diameter():
    package = _reviewed_package(holes=None)
    result = evaluate_automation_gate(package)
    assert result.decision == AUTOMATION_DECISION_REVIEW
    codes = _blocker_codes(result)
    assert AUTOMATION_BLOCKER_HOLE_DIAMETER in codes
    assert AUTOMATION_BLOCKER_SPECIFICATION in codes  # 7V's own gate rejects it too, verbatim

    hole_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_HOLE_DIAMETER)
    assert "M20" in hole_blocker.message
    assert "not a hole diameter" in hole_blocker.message
    assert "Ø" not in hole_blocker.message  # 'M20' was explicitly never turned into 'Ø20'

    spec = build_reviewed_connection_specification(package)
    assert spec.holes is None and "holes" not in spec.provenance  # nothing was invented

    report = build_review_report(package)
    assert "not a hole diameter" in report.advisories["bolts"]  # 7W's advisory preserved, not bypassed


# =============================================================================
# REVIEW — provenance: AI-only values on required fields
# =============================================================================
def test_ai_only_provenance_on_required_field_is_REVIEW():
    # Everything reviewed except the plate: the AI's complete plate remains AI_EXTRACTED/unconfirmed.
    result = evaluate_automation_gate(_reviewed_package(plate=None))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    codes = _blocker_codes(result)
    assert AUTOMATION_BLOCKER_PROVENANCE in codes
    provenance_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_PROVENANCE)
    assert "plate" in provenance_blocker.message

    spec_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert "AI_EXTRACTED" in spec_blocker.message  # 7V's authority gate, reported verbatim


# =============================================================================
# REVIEW — invalid numerics: None / NaN / infinity / zero-where-positive / negative
# =============================================================================
@pytest.mark.parametrize("override,error_fragment", [
    ({"plate": _plate(thickness_mm=0)}, "thickness_mm"),
    ({"plate": _plate(width_mm=-5)}, "width_mm"),
    ({"holes": _holes(diameter_mm="M20")}, "nominal bolt size"),
    ({"holes": _holes(diameter_mm=None)}, "diameter_mm"),
    ({"holes": _holes(diameter_mm=float("nan"))}, "diameter_mm"),
    ({"holes": _holes(quantity=4.5)}, "quantity"),
    ({"location": _location(x=float("inf"))}, "location field 'x'"),
    ({"location": _location(z=None)}, "location field 'z'"),
], ids=[
    "plate-thickness-zero", "plate-width-negative", "hole-diameter-nominal-string",
    "hole-diameter-missing", "hole-diameter-nan", "hole-quantity-fractional",
    "location-x-infinite", "location-z-missing",
])
def test_invalid_or_non_finite_geometry_is_REVIEW(override, error_fragment):
    result = evaluate_automation_gate(_reviewed_package(**override))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    spec_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert error_fragment in spec_blocker.message  # the existing 7D/7J rejection, verbatim
    assert "Ø" not in spec_blocker.message  # nothing was repaired or substituted


# =============================================================================
# REVIEW — attachments
# =============================================================================
def test_unresolved_attachments_are_REVIEW():
    # (a) no attachments at all.
    result = evaluate_automation_gate(_reviewed_package(attachments=None))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_ATTACHMENT in _blocker_codes(result)

    # (b) incomplete coverage — one connected member has no attachment.
    result = evaluate_automation_gate(_reviewed_package(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
    ]))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    spec_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert "exactly cover" in spec_blocker.message

    # (c) an attachment naming a member the connection does not connect.
    result = evaluate_automation_gate(_reviewed_package(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "GHOST", "surface_reference": "START"},
    ]))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    spec_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert "GHOST" in spec_blocker.message


# =============================================================================
# REVIEW — validation, conflicts, malformed fields
# =============================================================================
def test_failed_downstream_validation_is_REVIEW_and_validation_evidence_cannot_rescue_blockers():
    result = evaluate_automation_gate(_reviewed_package(), validation_passed=False)
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert _blocker_codes(result) == {AUTOMATION_BLOCKER_VALIDATION}  # nothing else is wrong

    # Passing validation can never rescue a candidate whose own information fails.
    rescued = evaluate_automation_gate(_reviewed_package(holes=None), validation_passed=True)
    assert rescued.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_VALIDATION not in _blocker_codes(rescued)


def test_conflicting_reviewer_decisions_are_REVIEW():
    # The reviewer both supplied the plate and confirmed the AI's plate: ambiguous, so neither applies.
    result = evaluate_automation_gate(_reviewed_package(confirmed_ai_fields=frozenset({"plate"})))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    codes = _blocker_codes(result)
    assert AUTOMATION_BLOCKER_CONFLICT in codes
    conflict = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_CONFLICT)
    assert "plate" in conflict.message
    # 7W's existing semantics: a conflicted field falls back to the AI value as NEEDS_CONFIRMATION —
    # so the gate reports provenance + the 7V authority rejection, not a missing plate.
    assert AUTOMATION_BLOCKER_PROVENANCE in codes
    provenance_blocker = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_PROVENANCE)
    assert "plate" in provenance_blocker.message


def test_malformed_ai_fields_are_REVIEW():
    extraction = ai_connection_to_extraction({"connects_members": ["A", "B"], "bolts": "not-a-list"})
    result = evaluate_automation_gate(create_review_package(extraction))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    codes = _blocker_codes(result)
    assert AUTOMATION_BLOCKER_MALFORMED_FIELDS in codes
    malformed = next(b for b in result.blockers if b.code == AUTOMATION_BLOCKER_MALFORMED_FIELDS)
    assert "bolts" in malformed.message


# =============================================================================
# Confidence is NOT evidence — and there is no score
# =============================================================================
def test_high_confidence_never_upgrades_an_unreviewed_candidate():
    extraction = ai_connection_to_extraction({
        "connects_members": ["stringer 140x45 H4 SG8", "structure"],
        "bolts": [{"quantity": None, "size": "M12", "grade": None}],
        "confidence": 99,
    })
    result = evaluate_automation_gate(create_review_package(extraction, known_member_marks=("P1",)))
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert any(w.code == AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE for w in result.warnings)
    assert result.evidence_summary[-1] == "AI confidence: 99 (never evidence of engineering approval)"
    assert not hasattr(result, "automation_score") and not hasattr(result, "score")


# =============================================================================
# Read-only, deterministic, scoreless
# =============================================================================
def test_gate_is_read_only_deterministic_and_scoreless():
    package = _reviewed_package()
    before = copy.deepcopy(package)

    first = evaluate_automation_gate(package, validation_passed=True)
    second = evaluate_automation_gate(package, validation_passed=True)

    assert package == before  # nothing the gate built leaked back into the input
    assert first == second and first.decision == AUTOMATION_DECISION_AUTO
    assert first.decision in AUTOMATION_DECISIONS
    assert all(isinstance(f, AutomationFinding) for f in first.blockers + first.reasons + first.warnings)
    assert isinstance(first, AutomationGateResult)
    assert dataclasses.is_dataclass(first) and first.__dataclass_params__.frozen
    assert not hasattr(first, "automation_score") and not hasattr(first, "score")


def test_validation_evidence_is_never_coerced():
    with pytest.raises(TypeError):
        evaluate_automation_gate(_reviewed_package(), validation_passed="yes")


def test_confirmation_requests_while_blocked_are_REVIEW_with_deferred_warning():
    result = evaluate_automation_gate(
        _reviewed_package(holes=None), require_confirmation=("confirm the hole pattern",),
    )
    assert result.decision == AUTOMATION_DECISION_REVIEW
    assert any(w.code == AUTOMATION_WARNING_CONFIRMATION_DEFERRED for w in result.warnings)


def test_historical_ai_member_corrections_do_not_block_but_stay_visible():
    # The AI extracted an unknown mark; the reviewer replaced the member set. The correction is
    # historical (AI_UNKNOWN_MEMBER_CORRECTED) and must not block automation, but must stay visible.
    extraction = ai_connection_to_extraction({"connects_members": ["stringer 140x45 H4 SG8", "L2"]})
    package = create_review_package(extraction, _reviewed_supplement(), known_member_marks=KNOWN_MARKS)

    result = evaluate_automation_gate(package, validation_passed=True)
    assert result.decision == AUTOMATION_DECISION_AUTO
    historical = next(w for w in result.warnings if w.code == AUTOMATION_WARNING_HISTORICAL_AI_MARKS)
    assert "stringer 140x45 H4 SG8" in historical.message


# =============================================================================
# Isolation — imports and entry points
# =============================================================================
def test_the_gate_module_imports_only_the_review_layers_and_the_standard_library():
    tree = ast.parse(Path(automation_gate.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {
        "collections.abc", "dataclasses",
        "app.cad_engine.connection_review_package",
        "app.cad_engine.project_connection_review",
        "app.cad_engine.reviewed_connection_specification",
    }


def test_the_gate_never_calls_cad_validation_or_drawing_entry_points(monkeypatch):
    import app.cad_engine.reviewed_connection_assembly as assembly_module
    import app.cad_engine.reviewed_connection_drawing_gate as drawing_gate_module
    import app.cad_engine.reviewed_connection_validation_gate as validation_module
    import app.cad_engine.two_member_connection as two_member_module
    import app.drawing_generator.interface as drawing_interface
    import app.drawing_generator.pdf_builder as pdf_builder

    def forbidden(*args, **kwargs):
        raise AssertionError("7Z must not generate CAD, validate geometry or produce drawings")

    for module, name in [
        (assembly_module, "build_reviewed_two_member_connection_assembly"),
        (two_member_module, "build_two_member_connection_assembly"),
        (validation_module, "validate_reviewed_connection_assembly"),
        (drawing_gate_module, "generate_fabrication_drawing_from_reviewed_assembly"),
        (drawing_interface, "generate_connection_fabrication_drawing_pdf"),
        (pdf_builder, "build_connection_pdf"),
    ]:
        monkeypatch.setattr(module, name, forbidden)

    result = evaluate_automation_gate(_reviewed_package(), validation_passed=True)
    assert result.decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# REAL ARKLES CAPTURE — through the existing 7Y/7X pathway, no Claude, no network
# =============================================================================
@needs_real_capture
def test_real_arkles_capture_candidates_are_all_REVIEW():
    """
    The real capture's raw AI candidates reference timber members ("stringer 140x45 H4 SG8",
    "stringer 190x45 H4 SG8", "deck joist", "bearer") and nominal bolt sizes (M12, "SQ4 12mm").
    They must NOT be treated as fabrication-ready structural steel connections: every candidate is
    REVIEW, never AUTO or CONFIRM.
    """
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, drawing_set_page_count=41, known_member_marks=real_known_marks())
    expected = flatten_reference(pages)
    assert len(intake.collection.candidates) == len(expected) > 0

    unknown = [
        candidate for candidate in intake.collection.candidates
        if build_review_report(candidate.package).current_unknown_member_marks
    ]
    assert len(unknown) == len(intake.collection.candidates)  # none of the captured marks are known members

    for candidate in intake.collection.candidates:
        report = build_review_report(candidate.package)
        result = evaluate_candidate_automation(candidate)
        assert result.decision == AUTOMATION_DECISION_REVIEW
        codes = {b.code for b in result.blockers}
        assert AUTOMATION_BLOCKER_REVIEW_STATUS in codes       # never reviewed, never approved
        assert AUTOMATION_BLOCKER_MEMBER_IDENTITY in codes     # the captured marks resolve to no known member
        assert AUTOMATION_BLOCKER_HOLE_DIAMETER in codes       # only nominal sizes were extracted
        assert AUTOMATION_BLOCKER_SPECIFICATION in codes       # 7V's own gate rejects it too, verbatim
        assert "holes" in report.missing


@needs_real_capture
def test_real_arkles_nominal_sizes_survive_the_gate_unconverted():
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, known_member_marks=real_known_marks())
    assert intake.collection.candidates  # the capture is not empty

    for candidate in intake.collection.candidates:
        report = build_review_report(candidate.package)
        result = evaluate_candidate_automation(candidate)
        all_text = " ".join(f.message for f in result.blockers + result.reasons + result.warnings)
        assert "Ø" not in all_text                              # nothing became a hole diameter
        for bolt in candidate.extraction.bolts:                 # each nominal size survives verbatim
            if bolt.size is not None:
                assert str(bolt.size) in all_text
        spec = build_reviewed_connection_specification(candidate.package)
        assert spec.holes is None                               # no hole geometry was invented
        identity = next((b for b in result.blockers if b.code == AUTOMATION_BLOCKER_MEMBER_IDENTITY), None)
        if identity is not None:                                # every unknown mark is named, not reinterpreted
            for mark in report.current_unknown_member_marks:
                assert str(mark) in identity.message
