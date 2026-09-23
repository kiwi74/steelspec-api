"""
Milestone 7AI — REAL-WORLD EXCEPTION RESOLUTION PROOF (tests/test_real_world_exception_proof.py)

Proves, on the REAL Arkles Strand page-extraction capture, that genuine
AI uncertainty becomes structured human-review tasks (7AC), explicit
human resolutions are accepted through the existing contract, the
resolved connection re-runs through the same deterministic production
pipeline (7AD -> 7AA -> 7AE -> 7AF -> 7AG), and the safety gates still
hold for everything else — all through `real_world_exception_proof.py`,
which calls the genuine stages and only the genuine stages.

Coverage, keyed to the milestone's requirements:
  1-3   the acceptance matrix: exactly ONE fully resolved candidate
        re-decides AUTO -> 7AE AUTO -> 7AF GENERATED -> 7AG VERIFIED
        with a real PDF on disk; the other two stay REVIEW ->
        BLOCKED_REVIEW -> NO_ARTIFACT; the mixed project is never
        globally AUTO
  4-6   artifact lifecycle: the recorded filename, size, SHA-256 and
        page count describe the genuine file; the PDF carries the
        reviewer-given identity and the resolved geometry
  7-9   lossless scope and AI values: pages_received/parse failures/
        pages_not_analysed/drawing_set_page_count survive verbatim; the
        AI's nominal sizes ("M12", "SQ4 12mm") are readings, never
        converted to diameters; the raw capture is never mutated
  10-12 provenance: every resolved engineering field is HUMAN_
        SUPPLEMENTED; the connection identity carries no provenance
        label (7W's documented contract); a smuggled "provenance" label
        in a human answer can never forge AI provenance
  13-20 anti-bypass: approval alone; M12 without an explicit hole
        diameter; incomplete resolutions; invalid hole diameters
        (NaN/inf/-1/0/True/text); unknown member marks; swapped
        START/END attachment surfaces; a tampered assembly fails the
        existing verifier — none of them can reach AUTO, a drawing or a
        verified artifact
  21-25 nothing is mutated and every proof result is frozen; repeated
        runs are deterministic; resolutions for other candidates and
        unknown candidate ids are loud programming errors; bad argument
        types raise loudly; no parameter can inject a decision, a
        validation verdict or a verification result
  26-28 import purity (no config / AI / Supabase / FastAPI / DB / UI /
        drawing-generator imports, no network); runtime import without
        any secret environment; the module never calls the drawing
        generator, the connection-level gate/dispatch/verify entry
        points or the 7Z gate directly.
"""
import ast
import copy
import dataclasses
import hashlib
import inspect
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

import app.cad_engine.real_world_exception_proof as proof_module
from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_REVIEW
from app.cad_engine.automation_pipeline import evaluate_reviewed_connection_for_automation
from app.cad_engine.connection_review_package import (
    ConnectionReviewSupplement,
    build_review_report,
    create_review_package,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_BLOCKED_REVIEW, OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import (
    CHECK_DRAWING_CONTENT_PRESENT,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
    CHECK_IDENTITY_VERIFIABLE,
    CHECK_PASSED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
    verify_project_drawing_outputs,
)
from app.cad_engine.exception_resolution import (
    STATUS_OPEN,
    STATUS_RESOLVED,
    TASK_COMPLETE_REVIEW,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    HumanResolution,
    build_exception_resolution_package,
)
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.real_world_exception_proof import (
    RealWorldConnectionOutcome,
    RealWorldExceptionProofResult,
    run_real_world_exception_proof,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_exception_resolution import ARKLES_AI_MARKS, ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import CAPTURE_PATH, load_capture, needs_real_capture, real_known_marks
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher

PROJECT_ID = "PROJ-7AI"
SOURCE_DRAWING_ID = "ARKLES-STRAND"
DRAWING_SET_PAGE_COUNT = 41
SELECTED_PACKAGE_ID = "RP-0001"
CANDIDATE_IDS = ("RP-0001", "RP-0002", "RP-0003")

# =============================================================================
# THE REAL AI EXTRACTION — the Arkles Strand capture, loaded verbatim. Nothing
# in this file modifies it; every test asserts values from it, never into it.
# =============================================================================
ARKLES_REAL_AI_EXTRACTION = load_capture(CAPTURE_PATH) if CAPTURE_PATH.exists() else []

# =============================================================================
# HUMAN-SUPPLIED TEST DATA — not extracted from the Arkles AI result
# =============================================================================
# Everything below is explicit human-reviewer input supplied by this TEST
# (playing the reviewer), never derived from the Arkles capture and never
# written into it. Only the member MARKS are real (they appear in the real
# capture's known-member list); the member rows, placements, plate, holes,
# location and connection identity are test data. The raw capture carries no
# member lengths, no grades, no plate, no hole diameter and no connection
# identity — and this file invents none for the AI.
HUMAN_SUPPLIED_CONNECTION_ID = "CONN-ARKLES-001"
HUMAN_SUPPLIED_MEMBER_MARKS = ("L2", "L3")  # real Arkles known marks only
HUMAN_SUPPLIED_MEMBER_ROWS = {
    # The reviewer names which members connect; the rows are this test's data.
    "L2": {**make_member_a_row(), "mark": "L2"},
    "L3": {**make_member_b_row(), "mark": "L3"},
}
HUMAN_SUPPLIED_MEMBER_PLACEMENTS = {
    "L2": make_member_a_placement(),
    "L3": make_member_b_placement(),
}
HUMAN_SUPPLIED_SECTION_MATCHER = make_multi_member_matcher()
HUMAN_SUPPLIED_POSITION = "END"
HUMAN_SUPPLIED_PLATE = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
HUMAN_SUPPLIED_HOLES = {
    "quantity": 4, "diameter_mm": 22.0,
    "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0,
}
HUMAN_SUPPLIED_LOCATION = {
    "x": 0.0, "y": 0.0, "z": 3994.0,
    "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0,
}
HUMAN_SUPPLIED_ATTACHMENTS = [
    {"member_mark": "L2", "surface_reference": "END"},
    {"member_mark": "L3", "surface_reference": "START"},
]
HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE = {
    TASK_COMPLETE_REVIEW: None,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT: (
        HUMAN_SUPPLIED_MEMBER_MARKS, HUMAN_SUPPLIED_POSITION, tuple(HUMAN_SUPPLIED_ATTACHMENTS),
    ),
    TASK_PROVIDE_PLATE: HUMAN_SUPPLIED_PLATE,
    TASK_PROVIDE_HOLE_DIAMETER: HUMAN_SUPPLIED_HOLES,
    TASK_PROVIDE_LOCATION: HUMAN_SUPPLIED_LOCATION,
    TASK_REVIEW_SPECIFICATION: None,
    TASK_REVIEW_VALIDATION: None,
    TASK_PROVIDE_CONNECTION_IDENTITY: HUMAN_SUPPLIED_CONNECTION_ID,
}

assert set(HUMAN_SUPPLIED_MEMBER_MARKS) <= set(real_known_marks()) or not ARKLES_REAL_AI_EXTRACTION


def _built_package():
    """7Y -> 7AB (no member context) -> 7AC (member context for choices): the
    same inputs the proof module itself uses, exposed so tests can read the
    task ids and construct resolutions against them."""
    intake = intake_page_extractions(
        list(ARKLES_REAL_AI_EXTRACTION),
        project_id=PROJECT_ID,
        source_drawing_id=SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
        drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
    )
    initial = evaluate_project_for_automation(intake.collection, intake=intake)
    built = build_exception_resolution_package(
        initial, intake.collection, member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
    )
    return intake, initial, built


def _task(built, package_id, task_type):
    group = next(g for g in built.connection_tasks if g.review_package_id == package_id)
    return next(t for t in group.tasks if t.task_type == task_type)


def _answer(task, value):
    """One human resolution, recorded against the task's own vocabulary."""
    return HumanResolution(
        task.task_id, task.task_type, task.answer_type, copy.deepcopy(value),
        evidence="HUMAN-SUPPLIED TEST DATA — the reviewer's explicit answer, not extracted by the AI",
    )


def _full_human_resolutions(built, package_id):
    group = next(g for g in built.connection_tasks if g.review_package_id == package_id)
    assert {t.task_type for t in group.tasks} == set(HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE)
    return [_answer(t, HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[t.task_type]) for t in group.tasks]


def _run_proof(tmp_path, *, resolutions=None, selected=SELECTED_PACKAGE_ID, **overrides):
    if resolutions is None:
        _, _, built = _built_package()
        resolutions = _full_human_resolutions(built, selected)
    kwargs = dict(
        arkles_pages=ARKLES_REAL_AI_EXTRACTION,
        arkles_project_id=PROJECT_ID,
        arkles_source_drawing_id=SOURCE_DRAWING_ID,
        arkles_known_member_marks=real_known_marks(),
        arkles_drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
        human_resolutions=resolutions,
        selected_package_id=selected,
        output_dir=tmp_path,
    )
    kwargs.update(overrides)
    return run_real_world_exception_proof(**kwargs)


def _outcome(result, package_id):
    return next(o for o in result.connection_outcomes if o.review_package_id == package_id)


# =============================================================================
# 1-3. The acceptance matrix: one resolved AUTO chain, two untouched REVIEW chains
# =============================================================================
@needs_real_capture
def test_acceptance_matrix_one_verified_artifact_two_blocked(tmp_path):
    result = _run_proof(tmp_path)
    assert isinstance(result, RealWorldExceptionProofResult)
    assert result.passed

    assert tuple(o.review_package_id for o in result.connection_outcomes) == CANDIDATE_IDS

    # The initial project: every candidate REVIEW — the real capture resolves nothing.
    assert result.initial_result.auto_count == 0
    assert result.initial_result.confirm_count == 0
    assert result.initial_result.review_count == 3
    for cid in CANDIDATE_IDS:
        outcome = _outcome(result, cid)
        assert outcome.initial_decision == AUTOMATION_DECISION_REVIEW
        # The genuine 7Z blocker findings, kept in their own order (the module
        # never sorts or re-invents them).
        assert set(outcome.initial_blocker_codes) == ARKLES_BLOCKER_CODES

    # The one resolved candidate: genuine AUTO end to end.
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    assert selected.rerun_decision == AUTOMATION_DECISION_AUTO
    assert selected.rerun_blocker_codes == ()
    assert selected.validation_passed is True
    assert selected.specification_accepted is True
    assert selected.assembly_built is True
    assert selected.gate_decision == AUTOMATION_DECISION_AUTO
    assert selected.output_status == OUTPUT_STATUS_GENERATED
    assert selected.verification_status == VERIFICATION_STATUS_VERIFIED
    assert selected.connection_id == HUMAN_SUPPLIED_CONNECTION_ID
    assert len(selected.resolutions_applied) == len(HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE)
    assert selected.resolutions_refused == ()

    # The untouched candidates: REVIEW end to end, no artifact of any kind.
    for cid in ("RP-0002", "RP-0003"):
        outcome = _outcome(result, cid)
        assert outcome.rerun_decision == AUTOMATION_DECISION_REVIEW
        assert outcome.gate_decision == AUTOMATION_DECISION_REVIEW
        assert outcome.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        assert outcome.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert outcome.connection_id is None
        assert outcome.artifact_path is None
        assert outcome.artifact_sha256 is None
        assert outcome.artifact_size_bytes is None
        assert outcome.artifact_page_count is None
        assert outcome.resolutions_applied == () and outcome.resolutions_refused == ()

    # The project gate derives, never asserts: one AUTO cannot make the project AUTO.
    assert result.fabrication_result.package_decision == AUTOMATION_DECISION_REVIEW
    assert result.fabrication_result.eligible_count == 1
    assert result.fabrication_result.blocked_outputs == ("RP-0002", "RP-0003")
    assert result.resolution_result.auto_count == 1
    assert result.resolution_result.review_count == 2

    # Exactly one genuine PDF exists — the selected connection's fabrication drawing.
    pdfs = sorted(tmp_path.rglob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].is_file() and pdfs[0].stat().st_size > 0
    assert pdfs[0].name == f"{HUMAN_SUPPLIED_CONNECTION_ID}-fabrication.pdf"

    # The real scope facts survive verbatim, straight from the genuine stage results.
    assert "7Y scope: pages_received = 30; parse_failures = 6; " \
           "pages_not_analysed = 11; drawing_set_page_count = 41" in result.summary
    assert "acceptance matrix: PASSED" in result.summary


@needs_real_capture
def test_every_human_resolution_was_recorded_on_the_selected_candidate_only(tmp_path):
    result = _run_proof(tmp_path)
    group = next(g for g in result.resolved_package.connection_tasks
                 if g.review_package_id == SELECTED_PACKAGE_ID)
    assert group.review_package_id == SELECTED_PACKAGE_ID
    assert all(task.status == STATUS_RESOLVED for task in group.tasks)
    for cid in ("RP-0002", "RP-0003"):
        untouched = next(g for g in result.resolved_package.connection_tasks
                         if g.review_package_id == cid)
        assert all(task.status == STATUS_OPEN for task in untouched.tasks)


# =============================================================================
# 4-6. The artifact: a real PDF whose records describe the genuine file
# =============================================================================
@needs_real_capture
def test_the_verified_artifact_is_the_real_pdf_with_the_resolved_identity_and_geometry(tmp_path):
    result = _run_proof(tmp_path)
    selected = _outcome(result, SELECTED_PACKAGE_ID)

    path = Path(selected.artifact_path)
    assert path.is_file()
    assert path.stat().st_size == selected.artifact_size_bytes
    assert selected.artifact_size_bytes > 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == selected.artifact_sha256
    assert selected.artifact_page_count == len(PdfReader(path).pages)
    assert selected.artifact_page_count > 0

    # The genuine 7AG checks all passed for the selected connection.
    verification = next(
        o.verification_result for o in result.verification_result.connection_results
        if o.review_package_id == SELECTED_PACKAGE_ID
    )
    check_statuses = {check.code: check.status for check in verification.checks}
    assert check_statuses[CHECK_IDENTITY_VERIFIABLE] == CHECK_PASSED
    assert check_statuses[CHECK_DRAWING_CONTENT_PRESENT] == CHECK_PASSED
    assert check_statuses[CHECK_GEOMETRY_FIELDS_VERIFIABLE] == CHECK_PASSED

    # The drawing carries the reviewer-given identity and the resolved geometry.
    text = "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)
    assert HUMAN_SUPPLIED_CONNECTION_ID in text
    assert "MEMBER A" in text and "MEMBER B" in text
    assert "L2" in text and "L3" in text and "250PFC" in text
    assert "Ø22" in text  # the HUMAN-SUPPLIED hole diameter — never the AI's "M12"


# =============================================================================
# 7-9. Lossless scope and AI values; the raw capture is never touched
# =============================================================================
@needs_real_capture
def test_ai_readings_stay_verbatim_and_nothing_is_invented(tmp_path):
    result = _run_proof(tmp_path)

    outcomes = {o.review_package_id: o for o in result.connection_outcomes}
    # The AI's member readings, exactly as extracted.
    assert outcomes["RP-0001"].ai_member_references == tuple(ARKLES_AI_MARKS[0])
    assert outcomes["RP-0002"].ai_member_references == tuple(ARKLES_AI_MARKS[1])
    assert outcomes["RP-0003"].ai_member_references == tuple(ARKLES_AI_MARKS[2])
    # The AI's bolt readings: the nominal sizes survive as readings, never converted.
    assert any("'M12'" in reading for reading in outcomes["RP-0001"].ai_bolt_readings)
    assert any("'M12'" in reading for reading in outcomes["RP-0002"].ai_bolt_readings)
    sq4 = next(r for r in outcomes["RP-0003"].ai_bolt_readings if "SQ4" in r)
    assert "SQ4 12mm" in sq4 and "Ø" not in sq4
    # No conversion appears anywhere in the proof record.
    assert "Ø" not in repr(result)

    # The resolved connection's rebuild kept the original AI extraction untouched
    # (the 7AD rerun rebuilds the supplement, never the extraction).
    rerun_outcome = next(o for o in result.resolution_result.outcomes
                         if o.review_package_id == SELECTED_PACKAGE_ID)
    intake, _, _ = _built_package()
    candidate = next(c for c in intake.collection.candidates if c.review_package_id == SELECTED_PACKAGE_ID)
    assert rerun_outcome.rebuilt_package.extraction == candidate.package.extraction
    assert any("'M12'" in repr(bolt) for bolt in candidate.package.extraction.bolts)


@needs_real_capture
def test_the_raw_capture_and_member_context_are_never_mutated(tmp_path):
    capture_snapshot = copy.deepcopy(ARKLES_REAL_AI_EXTRACTION)
    rows_snapshot = copy.deepcopy(HUMAN_SUPPLIED_MEMBER_ROWS)
    placements_snapshot = copy.deepcopy(HUMAN_SUPPLIED_MEMBER_PLACEMENTS)

    _run_proof(tmp_path)

    assert ARKLES_REAL_AI_EXTRACTION == capture_snapshot
    assert HUMAN_SUPPLIED_MEMBER_ROWS == rows_snapshot
    assert HUMAN_SUPPLIED_MEMBER_PLACEMENTS == placements_snapshot


# =============================================================================
# 10-12. Provenance: human fields are HUMAN_SUPPLEMENTED, identity has no label
# =============================================================================
@needs_real_capture
def test_resolved_engineering_fields_carry_human_provenance_and_identity_has_none(tmp_path):
    result = _run_proof(tmp_path)
    rerun_outcome = next(o for o in result.resolution_result.outcomes
                         if o.review_package_id == SELECTED_PACKAGE_ID)
    rebuilt = rerun_outcome.rebuilt_package

    # The 7W report carries a copy of the genuine 7V specification provenance.
    provenance = build_review_report(rebuilt).provenance
    for field in ("connected_member_marks", "position", "plate", "holes", "location", "attachments"):
        assert provenance[field] == PROVENANCE_HUMAN_SUPPLEMENTED, (field, provenance[field])
    # The connection identity is an identifier, not an engineering field (7W's contract).
    assert rebuilt.supplement.connection_id == HUMAN_SUPPLIED_CONNECTION_ID
    assert "connection_id" not in provenance

    # The AI's own readings remain AI-side data: nothing was relabelled AI_EXTRACTED.
    assert PROVENANCE_AI_EXTRACTED not in provenance.values()


# =============================================================================
# 13-20. Anti-bypass: nothing short of complete, valid human resolution reaches AUTO
# =============================================================================
@needs_real_capture
def test_A_approval_alone_never_resolves_anything(tmp_path):
    _, _, built = _built_package()
    approval = _answer(_task(built, SELECTED_PACKAGE_ID, TASK_COMPLETE_REVIEW), None)
    result = _run_proof(tmp_path, resolutions=[approval])

    assert result.passed is False
    assert result.resolution_result.auto_count == 0
    for cid in CANDIDATE_IDS:
        outcome = _outcome(result, cid)
        assert outcome.rerun_decision == AUTOMATION_DECISION_REVIEW
        assert outcome.gate_decision == AUTOMATION_DECISION_REVIEW
        assert outcome.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        assert outcome.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert outcome.artifact_path is None
    assert not list(tmp_path.rglob("*.pdf"))
    assert "acceptance matrix: NOT PASSED" in result.summary


@needs_real_capture
def test_B_nominal_size_M12_is_never_a_hole_diameter(tmp_path):
    _, _, built = _built_package()
    resolutions = _full_human_resolutions(built, SELECTED_PACKAGE_ID)
    # The reviewer names the bolt reading as the "holes" data — no diameter at all.
    no_diameter = _answer(
        _task(built, SELECTED_PACKAGE_ID, TASK_PROVIDE_HOLE_DIAMETER),
        {"quantity": None, "diameter_mm": None,
         "vertical_spacing_mm": None, "horizontal_spacing_mm": None},
    )
    resolutions = [r for r in resolutions if r.task_id != no_diameter.task_id] + [no_diameter]

    result = _run_proof(tmp_path, resolutions=resolutions)
    assert result.passed is False
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    assert selected.rerun_decision == AUTOMATION_DECISION_REVIEW
    assert selected.verification_status != VERIFICATION_STATUS_VERIFIED
    assert selected.artifact_path is None
    # The AI reading survived verbatim; nothing became a diameter.
    assert any("'M12'" in reading for reading in selected.ai_bolt_readings)
    assert "Ø" not in repr(result)


@needs_real_capture
def test_C_incomplete_resolution_stays_REVIEW_without_drawing_or_artifact(tmp_path):
    _, _, built = _built_package()
    partial = [
        _answer(_task(built, SELECTED_PACKAGE_ID, TASK_COMPLETE_REVIEW), None),
        _answer(_task(built, SELECTED_PACKAGE_ID, TASK_PROVIDE_CONNECTION_IDENTITY),
                HUMAN_SUPPLIED_CONNECTION_ID),
    ]
    result = _run_proof(tmp_path, resolutions=partial)

    assert result.passed is False
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    assert selected.rerun_decision == AUTOMATION_DECISION_REVIEW
    assert selected.rerun_blocker_codes  # the missing information is still visible
    assert selected.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
    assert selected.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert selected.artifact_path is None
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0, True, "22"])
@needs_real_capture
def test_D_invalid_hole_diameters_are_rejected_without_AUTO(bad, tmp_path):
    _, _, built = _built_package()
    resolutions = _full_human_resolutions(built, SELECTED_PACKAGE_ID)
    invalid = _answer(
        _task(built, SELECTED_PACKAGE_ID, TASK_PROVIDE_HOLE_DIAMETER),
        {**HUMAN_SUPPLIED_HOLES, "diameter_mm": bad},
    )
    resolutions = [r for r in resolutions if r.task_id != invalid.task_id] + [invalid]

    result = _run_proof(tmp_path, resolutions=resolutions)
    assert result.passed is False
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    assert selected.rerun_decision != AUTOMATION_DECISION_AUTO
    assert selected.verification_status != VERIFICATION_STATUS_VERIFIED
    assert selected.artifact_path is None
    assert "acceptance matrix: NOT PASSED" in result.summary


@needs_real_capture
def test_E_unknown_member_mark_is_refused_and_grants_nothing(tmp_path):
    _, _, built = _built_package()
    resolutions = _full_human_resolutions(built, SELECTED_PACKAGE_ID)
    unknown = _answer(
        _task(built, SELECTED_PACKAGE_ID, TASK_SELECT_MEMBER_POSITION_ATTACHMENT),
        (("L2", "ZZZ-NOT-A-MEMBER"), HUMAN_SUPPLIED_POSITION, tuple(HUMAN_SUPPLIED_ATTACHMENTS)),
    )
    resolutions = [r for r in resolutions if r.task_id != unknown.task_id] + [unknown]

    result = _run_proof(tmp_path, resolutions=resolutions)
    assert result.passed is False
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    assert selected.rerun_decision == AUTOMATION_DECISION_REVIEW
    assert len(selected.resolutions_refused) == 1
    assert "ZZZ-NOT-A-MEMBER" in selected.resolutions_refused[0].reason
    assert selected.artifact_path is None
    assert not list(tmp_path.rglob("*.pdf"))


@needs_real_capture
def test_F_swapped_attachment_surfaces_fail_the_real_geometry_check(tmp_path):
    _, _, built = _built_package()
    resolutions = _full_human_resolutions(built, SELECTED_PACKAGE_ID)
    swapped = _answer(
        _task(built, SELECTED_PACKAGE_ID, TASK_SELECT_MEMBER_POSITION_ATTACHMENT),
        (HUMAN_SUPPLIED_MEMBER_MARKS, HUMAN_SUPPLIED_POSITION, (
            {"member_mark": "L2", "surface_reference": "START"},
            {"member_mark": "L3", "surface_reference": "END"},
        )),
    )
    resolutions = [r for r in resolutions if r.task_id != swapped.task_id] + [swapped]

    result = _run_proof(tmp_path, resolutions=resolutions)
    assert result.passed is False
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    # The answer's vocabulary was valid, but the genuine 7R caught the wrong surfaces.
    assert selected.resolutions_refused == ()
    assert selected.rerun_decision == AUTOMATION_DECISION_REVIEW
    assert selected.validation_passed is False
    assert selected.artifact_path is None


@needs_real_capture
def test_G_a_smuggled_provenance_label_can_never_forge_ai_provenance(tmp_path):
    _, _, built = _built_package()
    resolutions = _full_human_resolutions(built, SELECTED_PACKAGE_ID)
    forged = _answer(
        _task(built, SELECTED_PACKAGE_ID, TASK_PROVIDE_HOLE_DIAMETER),
        {**HUMAN_SUPPLIED_HOLES, "provenance": "AI_EXTRACTED"},
    )
    resolutions = [r for r in resolutions if r.task_id != forged.task_id] + [forged]

    result = _run_proof(tmp_path, resolutions=resolutions)
    # Provenance is decided by the pipeline's own rules, never by caller labels: the
    # resolved fields are HUMAN_SUPPLEMENTED and the genuine chain still completes.
    assert result.passed
    rerun_outcome = next(o for o in result.resolution_result.outcomes
                         if o.review_package_id == SELECTED_PACKAGE_ID)
    provenance = build_review_report(rerun_outcome.rebuilt_package).provenance
    assert provenance["holes"] == PROVENANCE_HUMAN_SUPPLEMENTED
    assert PROVENANCE_AI_EXTRACTED not in provenance.values()


@needs_real_capture
def test_H_a_tampered_assembly_fails_the_existing_verifier(tmp_path):
    result = _run_proof(tmp_path)
    selected = _outcome(result, SELECTED_PACKAGE_ID)
    assert selected.verification_status == VERIFICATION_STATUS_VERIFIED

    # Build a DIFFERENT genuine assembly — same package, different hole diameter —
    # and re-verify the already-recorded artifact against it. No second verifier
    # is invented: the existing 7AG must catch the mismatch.
    rerun_outcome = next(o for o in result.resolution_result.outcomes
                         if o.review_package_id == SELECTED_PACKAGE_ID)
    rebuilt = rerun_outcome.rebuilt_package
    tampered_supplement = dataclasses.replace(
        rebuilt.supplement,
        holes={**rebuilt.supplement.holes, "diameter_mm": 24.0},
    )
    tampered_package = create_review_package(
        rebuilt.extraction, tampered_supplement, known_member_marks=rebuilt.known_member_marks,
    )
    tampered_pipeline = evaluate_reviewed_connection_for_automation(
        tampered_package,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )
    recheck = verify_project_drawing_outputs(
        result.drawing_result,
        assemblies={SELECTED_PACKAGE_ID: tampered_pipeline.reviewed_assembly},
    )
    rechecked = next(o for o in recheck.connection_results
                     if o.review_package_id == SELECTED_PACKAGE_ID)
    assert rechecked.verification_result.verification_status != VERIFICATION_STATUS_VERIFIED


# =============================================================================
# 21-25. Loud errors, immutability, determinism
# =============================================================================
@needs_real_capture
def test_resolutions_for_other_candidates_are_a_programming_error(tmp_path):
    _, _, built = _built_package()
    foreign = _answer(_task(built, "RP-0002", TASK_COMPLETE_REVIEW), None)
    with pytest.raises(ValueError, match="selected candidate"):
        _run_proof(tmp_path, resolutions=[foreign])


@needs_real_capture
def test_unknown_selected_package_id_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError, match="not a candidate"):
        _run_proof(tmp_path, resolutions=[], selected="RP-9999")


@pytest.mark.parametrize("override", [
    {"arkles_pages": "not pages"},
    {"arkles_pages": [{"page_number": 1}, "nope"]},
    {"arkles_project_id": None},
    {"arkles_source_drawing_id": 7},
    {"arkles_known_member_marks": "L2"},
    {"arkles_drawing_set_page_count": "41"},
    {"arkles_drawing_set_page_count": True},
    {"member_rows": [1]},
    {"member_placements": None},
    {"human_resolutions": [object()]},
    {"selected_package_id": 7},
    {"output_dir": 3.14},
])
def test_bad_argument_types_raise_loudly(override, tmp_path):
    with pytest.raises(TypeError):
        _run_proof(tmp_path, resolutions=[], **override)


def test_no_parameter_can_inject_a_decision_or_a_verification():
    names = set(inspect.signature(run_real_world_exception_proof).parameters)
    assert names == {
        "arkles_pages", "arkles_project_id", "arkles_source_drawing_id",
        "arkles_known_member_marks", "arkles_drawing_set_page_count",
        "member_rows", "member_placements", "section_matcher",
        "human_resolutions", "selected_package_id", "output_dir",
    }
    for forbidden in ("decision", "validation", "verification", "status"):
        assert not any(forbidden in name for name in names)


@needs_real_capture
def test_proof_results_are_frozen(tmp_path):
    result = _run_proof(tmp_path)
    assert isinstance(result.connection_outcomes[0], RealWorldConnectionOutcome)
    for frozen in (result, *result.connection_outcomes):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(frozen, "_not_a_field", "boom")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.passed = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.connection_outcomes[0].rerun_decision = AUTOMATION_DECISION_REVIEW


def _stable_summary(result):
    """The summary minus lines that legitimately differ per output directory or
    per PDF generation (the recorded path and the PDF's byte hash)."""
    return tuple(line for line in result.summary
                 if not line.startswith(("7AG artifact:", "7AG SHA-256:")))


@needs_real_capture
def test_repeated_runs_are_deterministic(tmp_path):
    first = _run_proof(tmp_path / "one")
    second = _run_proof(tmp_path / "two")
    assert first.passed and second.passed

    assert _stable_summary(first) == _stable_summary(second)
    for a, b in zip(first.connection_outcomes, second.connection_outcomes):
        # The artifact path differs by output directory and the PDF bytes (and so its
        # hash) legitimately differ per generation — everything else must be identical.
        assert dataclasses.replace(a, artifact_path=None, artifact_sha256=None) == \
            dataclasses.replace(b, artifact_path=None, artifact_sha256=None)


# =============================================================================
# 26-28. Import purity, wiring purity, no secrets
# =============================================================================
ALLOWED_IMPORTS = {
    "collections.abc",
    "dataclasses",
    "pathlib",
    "app.cad_engine.automation_gate",
    "app.cad_engine.automation_pipeline",
    "app.cad_engine.drawing_dispatch",
    "app.cad_engine.drawing_output_verification",
    "app.cad_engine.exception_resolution",
    "app.cad_engine.fabrication_output_gate",
    "app.cad_engine.project_automation",
    "app.cad_engine.project_extraction_intake",
    "app.cad_engine.resolution_rerun",
}


def test_proof_module_imports_are_pure_and_calls_only_the_project_level_stages():
    source = Path(proof_module.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == ALLOWED_IMPORTS

    lowered = source.lower()
    for forbidden in (
        "supabase", "anthropic", "fastapi", "cadquery", "reportlab", "ezdxf",
        "pypdf", "reviewed_connection_drawing_gate", "http", "socket",
        "310ub", "250pfc", "real-ub", "conn-real", "m12", "sq4",
        "arkles-strand", "con-arkles",
    ):
        assert forbidden not in lowered, forbidden

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    # The genuine project-level chain must be exactly the chain it calls — the
    # proof never calls the drawing generator, the connection-level
    # 7AE/7AF/7AG entry points or the 7Z gate directly.
    chain = {
        "evaluate_reviewed_connection_for_automation",
        "evaluate_project_for_automation",
        "intake_page_extractions",
        "build_exception_resolution_package",
        "apply_human_resolution",
        "rerun_project_after_resolutions",
        "evaluate_project_fabrication_output_gate",
        "dispatch_project_fabrication_drawings",
        "verify_project_drawing_outputs",
    }
    assert chain <= called
    assert "generate_fabrication_drawing_from_reviewed_assembly" not in called
    assert "dispatch_fabrication_drawing" not in called
    assert "verify_drawing_artifact" not in called
    assert "evaluate_fabrication_output_gate" not in called
    assert "evaluate_automation_gate" not in called


def test_module_imports_without_any_secret_environment():
    env = {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("ANTHROPIC", "SUPABASE", "FIREWORKS", "OPENAI"))
    }
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import app.cad_engine.real_world_exception_proof"],
        cwd=str(root), env=env, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
