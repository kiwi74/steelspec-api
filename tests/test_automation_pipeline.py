"""
Milestone 7AA — tests for the automation pipeline
(app.cad_engine.automation_pipeline), the orchestration seam that runs
the GENUINE existing validation path (7O assembly, 7R validation gate)
and hands its outcome to 7Z as evidence — never a guessed value:

    reviewed package -> 7D/7J/7N -> 7A/7I member pair -> 7O -> 7R -> 7Z

These tests prove, per the milestone's requirements:

  - The valid fixture reaches AUTO through the real 7O -> 7R -> 7Z
    implementation — the happy-path test passes NO validation
    evidence by hand, and the pipeline's public API has no parameter
    to accept any.
  - Spies on the pipeline's own imports prove the existing 7R
    function is the one called, and that its outcome reaches 7Z as
    validation_passed=True; a 7R failure reaches 7Z as
    validation_passed=False (proved by 7Z's VALIDATION blocker, which
    exists only when False was received).
  - Invalid geometry (wrong attachment surfaces — 7R's 7P check)
    cannot reach AUTO even though review_status is "approved"; the
    failure (stage, error_code, verbatim message) is preserved.
  - An assembly that cannot be constructed (7K/7L contact failure
    inside 7O) is preserved and cannot reach AUTO.
  - Incomplete connections (missing hole diameter / position /
    attachments / unresolved member identity) each stay REVIEW with
    the appropriate 7Z blocker visible — nothing is defaulted.
  - require_confirmation forwards to 7Z: validation can pass while
    the final decision is CONFIRM — proving the pipeline never writes
    "if validation_passed: AUTO".
  - Read-only, deterministic, import-pure (no Supabase/FastAPI/
    Anthropic/config/AI-extraction imports), TypeError on non-package
    input, and the REAL Arkles capture's three candidates stay REVIEW
    through this pipeline too (no member context supplied for them —
    no fabricated human corrections).

FIXTURE PROVENANCE: the reviewed package is the established synthetic
7Z fixture (SYNTHETIC AI-shaped extraction — actual schema keys, not
produced by any AI — plus a TEST / HUMAN-REVIEWED / SUPPLEMENTED
supplement built from the 7E/7H/7N fixtures). The member rows,
placements and section matcher are the established 7O/7R fixtures
(REAL-UB-CAD-001 310UB40 x4000mm, L2 250PFC x3000mm, the 7I
placements). Nothing here is claimed to be AI-extracted or
drawing-derived, and nothing re-implements any validation rule.
"""
import ast
import copy
import dataclasses
import inspect
from pathlib import Path

import pytest

import app.cad_engine.automation_pipeline as pipeline_module
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_GAP_ERROR_CODE,
    PIPELINE_STAGE_ASSEMBLY,
    PIPELINE_STAGE_ATTACHMENTS,
    PIPELINE_STAGE_CONNECTION,
    PIPELINE_STAGE_MEMBER_CONTEXT,
    PIPELINE_STAGE_SCOPE,
    PIPELINE_STAGE_VALIDATION,
    AutomationPipelineResult,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.connection_review_package import build_review_report
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly
from app.cad_engine.reviewed_connection_specification import REVIEW_STATUS_APPROVED
from app.cad_engine.reviewed_connection_validation_gate import (
    VALIDATED_LAYERS,
    ReviewedConnectionValidationResult,
)
from tests.test_automation_gate import _reviewed_package
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_project_extraction_intake import (
    CAPTURE_PATH,
    flatten_reference,
    load_capture,
    needs_real_capture,
    real_known_marks,
)
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher


def _member_rows():
    return {"REAL-UB-CAD-001": make_member_a_row(), "L2": make_member_b_row()}


def _member_placements(**overrides):
    placements = {
        "REAL-UB-CAD-001": make_member_a_placement(),
        "L2": make_member_b_placement(),
    }
    placements.update(overrides)
    return placements


# =============================================================================
# The valid fixture reaches AUTO through the GENUINE 7O -> 7R -> 7Z path
# =============================================================================
def test_valid_reviewed_connection_reaches_AUTO_through_the_genuine_7O_7R_7Z_path():
    package = _reviewed_package()
    assert build_review_report(package).ready_for_pipeline  # fixture sanity: 7W + 7V accept it

    # No validation evidence is passed anywhere — the pipeline must produce it by running 7R.
    result = evaluate_reviewed_connection_for_automation(
        package,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )

    assert result.validation_passed is True
    assert result.validation_failure is None
    assert isinstance(result.validation_result, ReviewedConnectionValidationResult)
    assert result.validation_result.layers_validated == VALIDATED_LAYERS
    assert isinstance(result.reviewed_assembly, ReviewedTwoMemberConnectionAssembly)
    assert len(result.reviewed_assembly.connection_geometry.holes) == 4  # the real 7K/7L geometry

    gate = result.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_AUTO
    assert gate.blockers == ()
    assert "validation evidence: passed" in gate.evidence_summary


# =============================================================================
# The wiring is genuine: the existing 7R is called, and its outcome reaches 7Z
# =============================================================================
def test_pipeline_calls_the_existing_7R_and_hands_its_outcome_to_7Z(monkeypatch):
    calls = {}
    real_validate = pipeline_module.validate_reviewed_connection_assembly
    real_gate = pipeline_module.evaluate_automation_gate

    def spy_validate(assembly):
        calls["assembly"] = assembly
        return real_validate(assembly)  # the genuine implementation runs

    def spy_gate(package, *, validation_passed=None, require_confirmation=()):
        calls["validation_passed"] = validation_passed
        return real_gate(package, validation_passed=validation_passed, require_confirmation=require_confirmation)

    monkeypatch.setattr(pipeline_module, "validate_reviewed_connection_assembly", spy_validate)
    monkeypatch.setattr(pipeline_module, "evaluate_automation_gate", spy_gate)

    result = evaluate_reviewed_connection_for_automation(
        _reviewed_package(),
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )

    assert calls["assembly"] is result.reviewed_assembly       # 7R validated exactly this assembly
    assert calls["validation_passed"] is True                  # genuine evidence, never guessed
    assert result.automation_gate_result.decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# Invalid geometry: 7R rejects -> validation_passed=False -> NOT AUTO
# =============================================================================
def test_invalid_geometry_cannot_reach_AUTO_even_with_approved_status():
    # Every workflow box is ticked — approved status, human provenance on all six fields, known
    # members — but the reviewer attached member A to the wrong surface, and 7R's 7P check
    # (composed inside the gate) rejects the geometry. 'approved' is NOT a substitute for
    # geometric validation.
    package = _reviewed_package(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assert package.supplement.review_status == REVIEW_STATUS_APPROVED

    result = evaluate_reviewed_connection_for_automation(
        package,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )

    assert result.validation_passed is False
    assert result.validation_failure is not None
    assert result.validation_failure.stage == PIPELINE_STAGE_VALIDATION
    assert result.validation_failure.error_code == "GeometryValidationError"
    assert "REAL-UB-CAD-001" in result.validation_failure.message
    assert result.validation_result is None
    assert result.reviewed_assembly is not None  # the assembly existed; the 7R gate rejected it

    gate = result.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    # The VALIDATION blocker exists only because 7Z received validation_passed=False.
    assert AUTOMATION_BLOCKER_VALIDATION in {b.code for b in gate.blockers}


# =============================================================================
# An assembly that cannot be constructed is preserved and cannot reach AUTO
# =============================================================================
def test_assembly_construction_failure_is_preserved_and_cannot_reach_AUTO():
    # Member B moved 6m away: 7O's construction-time 7K/7L contact check rejects, so no assembly
    # object ever exists. The failure is preserved; 7Z is told validation did not pass.
    result = evaluate_reviewed_connection_for_automation(
        _reviewed_package(),
        member_rows=_member_rows(),
        member_placements=_member_placements(L2=make_member_b_placement(z=10000.0)),
        section_matcher=make_multi_member_matcher(),
    )

    assert result.validation_passed is False
    assert result.validation_failure is not None
    assert result.validation_failure.stage == PIPELINE_STAGE_ASSEMBLY
    assert result.validation_failure.error_code == "GeometryValidationError"
    assert "does not place the connection plate in contact" in result.validation_failure.message
    assert result.reviewed_assembly is None and result.validation_result is None

    gate = result.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_VALIDATION in {b.code for b in gate.blockers}


# =============================================================================
# Incomplete connections: each missing fabrication-critical field stays REVIEW
# =============================================================================
@pytest.mark.parametrize("package_kwargs,expected_blocker,expected_stage", [
    ({"holes": None}, AUTOMATION_BLOCKER_HOLE_DIAMETER, PIPELINE_STAGE_CONNECTION),
    ({"position": None}, AUTOMATION_BLOCKER_POSITION, PIPELINE_STAGE_CONNECTION),
    ({"attachments": None}, AUTOMATION_BLOCKER_ATTACHMENT, PIPELINE_STAGE_ATTACHMENTS),
], ids=["missing-hole-diameter", "missing-position", "missing-attachments"])
def test_incomplete_connection_stays_REVIEW_with_its_7Z_blocker_visible(
    package_kwargs, expected_blocker, expected_stage,
):
    result = evaluate_reviewed_connection_for_automation(
        _reviewed_package(**package_kwargs),
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )

    assert result.validation_passed is False
    assert result.validation_failure is not None
    assert result.validation_failure.stage == expected_stage
    assert result.validation_failure.error_code == "GeometryValidationError"
    assert result.reviewed_assembly is None  # failed at the first adapter that requires the field

    gate = result.automation_gate_result
    codes = {b.code for b in gate.blockers}
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert expected_blocker in codes          # 7Z's own blocker remains visible
    assert AUTOMATION_BLOCKER_VALIDATION in codes


def test_unresolved_member_identity_is_REVIEW_and_blocks_before_geometry():
    # Reviewer-supplied marks matching no known project member: the adapters accept the values
    # (provenance is human), but no member data can exist for them, and 7Z reports the identity
    # blocker. Nothing is substituted to make the connection fit.
    package = _reviewed_package(
        connected_member_marks=["GHOST1", "GHOST2"],
        attachments=[
            {"member_mark": "GHOST1", "surface_reference": "END"},
            {"member_mark": "GHOST2", "surface_reference": "START"},
        ],
    )
    result = evaluate_reviewed_connection_for_automation(
        package,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )

    assert result.validation_passed is False
    assert result.validation_failure.stage == PIPELINE_STAGE_MEMBER_CONTEXT
    assert result.validation_failure.error_code == PIPELINE_GAP_ERROR_CODE
    assert "GHOST1" in result.validation_failure.message

    gate = result.automation_gate_result
    codes = {b.code for b in gate.blockers}
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_MEMBER_IDENTITY in codes
    assert AUTOMATION_BLOCKER_VALIDATION in codes


# =============================================================================
# The pipeline never decides: confirmation holds even with passing validation
# =============================================================================
def test_confirmation_requests_are_forwarded_and_7Z_still_decides():
    item = "Reused approved end-plate pattern CONN-ENDPLATE-2: confirm against the project connection register."
    result = evaluate_reviewed_connection_for_automation(
        _reviewed_package(),
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
        require_confirmation=(item,),
    )

    assert result.validation_passed is True and result.validation_result is not None
    gate = result.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_CONFIRM  # NOT AUTO: 7Z applies the hold
    assert gate.blockers == ()


# =============================================================================
# The caller cannot inject validation evidence
# =============================================================================
def test_pipeline_api_cannot_accept_manual_validation_evidence():
    parameters = inspect.signature(evaluate_reviewed_connection_for_automation).parameters
    assert "validation_passed" not in parameters
    assert parameters["package"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in list(parameters.values())[1:])


# =============================================================================
# Input contract and type safety
# =============================================================================
def test_pipeline_rejects_non_package_input():
    with pytest.raises(TypeError, match="ConnectionReviewPackage"):
        evaluate_reviewed_connection_for_automation({"extraction": None})


def test_otherwise_valid_package_without_member_context_is_REVIEW():
    # The package itself is perfect; the caller supplied no member context. The gap is reported
    # as an input-contract failure (never patched by inference), and 7Z still runs.
    result = evaluate_reviewed_connection_for_automation(_reviewed_package())

    assert result.validation_passed is False
    assert result.validation_failure.stage == PIPELINE_STAGE_MEMBER_CONTEXT
    assert result.validation_failure.error_code == PIPELINE_GAP_ERROR_CODE
    assert "section_matcher" in result.validation_failure.message
    assert result.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW


def test_more_than_two_members_is_reported_not_repaired():
    package = _reviewed_package(
        connected_member_marks=["REAL-UB-CAD-001", "L2", "P1"],
        attachments=[
            {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
            {"member_mark": "L2", "surface_reference": "START"},
            {"member_mark": "P1", "surface_reference": "END"},
        ],
    )
    result = evaluate_reviewed_connection_for_automation(package)
    # 7AV: three or more members are now SUPPORTED (the multi-member path) —
    # the reported-not-repaired boundary for them is a genuine input gap
    # (no member row supplied), never a silent drop or re-pairing.
    assert result.validation_passed is False
    assert result.validation_failure.stage == PIPELINE_STAGE_MEMBER_CONTEXT
    assert result.validation_failure.error_code == PIPELINE_GAP_ERROR_CODE
    assert "section_matcher" in result.validation_failure.message
    assert result.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW


# =============================================================================
# Read-only, deterministic, import-pure
# =============================================================================
def test_pipeline_is_read_only_and_deterministic():
    package = _reviewed_package()
    before = copy.deepcopy(package)

    first = evaluate_reviewed_connection_for_automation(
        package,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )
    second = evaluate_reviewed_connection_for_automation(
        package,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )

    assert package == before
    # The results carry live CadQuery solids, so whole-object equality is object-identity based;
    # determinism is asserted on everything decision-relevant plus measured geometry (the house
    # pattern from the 7R repeated-validation test).
    assert first.automation_gate_result == second.automation_gate_result
    assert first.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
    assert first.validation_passed is second.validation_passed is True
    assert first.validation_failure == second.validation_failure is None
    assert first.validation_result == second.validation_result
    assert {round(h.diameter, 6) for h in first.reviewed_assembly.connection_geometry.holes} == \
        {round(h.diameter, 6) for h in second.reviewed_assembly.connection_geometry.holes} == {22.0}
    assert abs(
        first.validation_result.interface_result.member_a_normal_dot
        - second.validation_result.interface_result.member_a_normal_dot
    ) < 1e-12
    assert isinstance(first, AutomationPipelineResult)
    assert dataclasses.is_dataclass(first) and first.__dataclass_params__.frozen
    assert all(
        dataclasses.is_dataclass(value) and value.__dataclass_params__.frozen
        for value in (first.automation_gate_result, first.validation_result)
    )


def test_pipeline_module_imports_only_the_cad_engine_and_the_standard_library():
    tree = ast.parse(Path(pipeline_module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {
        "collections.abc", "dataclasses",
        "app.cad_engine.assembly",
        "app.cad_engine.automation_gate",
        "app.cad_engine.connection_location",
        "app.cad_engine.connection_review_package",
        "app.cad_engine.errors",
        "app.cad_engine.interface",
        "app.cad_engine.multi_member_connection",
        "app.cad_engine.placement",
        "app.cad_engine.real_connection_adapter",
        "app.cad_engine.real_member_adapter",
        "app.cad_engine.reviewed_connection_assembly",
        "app.cad_engine.reviewed_connection_attachment",
        "app.cad_engine.reviewed_connection_specification",
        "app.cad_engine.reviewed_connection_validation_gate",
    }


# =============================================================================
# REAL ARKLES CAPTURE — through the existing 7Y/7X pathway, no Claude, no network
# =============================================================================
@needs_real_capture
def test_real_arkles_candidates_cannot_enter_the_auto_path():
    """
    The real capture's three raw AI candidates run through the genuine pipeline with NO member
    context supplied (no fabricated rows/placements, no human corrections). They fail at the
    first existing adapter (7D — no trustworthy connection_id) and 7Z is told validation did
    not pass: every candidate stays REVIEW, never AUTO.
    """
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, drawing_set_page_count=41, known_member_marks=real_known_marks())
    expected = flatten_reference(pages)
    assert len(intake.collection.candidates) == len(expected) > 0

    for candidate in intake.collection.candidates:
        result = evaluate_reviewed_connection_for_automation(candidate.package)

        assert result.validation_passed is False
        assert result.validation_failure is not None
        assert result.validation_failure.stage == PIPELINE_STAGE_CONNECTION
        assert result.validation_failure.error_code == "GeometryValidationError"
        assert result.reviewed_assembly is None and result.validation_result is None

        gate = result.automation_gate_result
        assert gate.decision == AUTOMATION_DECISION_REVIEW
        assert AUTOMATION_BLOCKER_VALIDATION in {b.code for b in gate.blockers}
