"""
Milestone 7AD — HUMAN RESOLUTION -> AUTOMATION RE-RUN (tests/test_resolution_rerun.py)

Proves the closed loop 7AC human resolutions -> 7W rebuild -> 7V -> 7AA ->
7O -> 7R -> 7Z re-decision, through `resolution_rerun.py`, against the
established synthetic fixtures (7Z/7AB/7AC) and the real Arkles capture.

Coverage, keyed to the milestone's 30 requirements:
  1-6   the genuine AUTO path; the genuine validation call; 7Z receives only
        7R's evidence; no caller-supplied validation_passed parameter exists;
        approval alone cannot create AUTO; confirmation alone cannot create
        AUTO; the hole diameter flows through the rebuild correctly
  7-11  invalid answers: "M20" is never converted (existing 7V/7D rejects);
        unknown member marks are refused; invalid positions are refused;
        invalid attachment surfaces are refused and wrong-but-vocabulary-
        valid geometry is caught by the genuine 7R; NaN/inf/bool/non-numeric
        flow verbatim and the existing rules decide
  12-16 partial resolution leaves blockers visible; multiple resolutions on
        one connection; isolation across connections with genuine counts;
        unknown task id / double resolution / wrong connection / wrong answer
        type are rejected; grouped answers obey the same vocabulary rules
  17-19 nothing is mutated and every result is frozen; AI values stay
        AI_EXTRACTED (ai_value) while human answers carry HUMAN_REVIEWED /
        HUMAN_SUPPLEMENTED; repeated re-runs are deterministic
  20-22 the real Arkles capture: approval-only stays REVIEW x3 with all
        blockers visible; legitimate real-mark resolutions still cannot
        reach AUTO; timber member answers are refused and the AI nominal
        sizes (M12 / "SQ4 12mm") are never converted
  23-30 import purity (no config / AI / pipeline / main / Supabase / FastAPI /
        DB / UI / drawing imports, no network); runtime import without any
        secret environment; 7AD never calls the gate, validation, assembly or
        drawing entry points directly (AST + runtime spies); loud
        programming errors.
"""
import ast
import copy
import dataclasses
import inspect
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.cad_engine.automation_pipeline as automation_pipeline
import app.cad_engine.reviewed_connection_assembly as reviewed_connection_assembly
import app.cad_engine.reviewed_connection_validation_gate as reviewed_connection_validation_gate
from app.cad_engine import resolution_rerun
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import PIPELINE_STAGE_CONNECTION, PIPELINE_STAGE_VALIDATION
from app.cad_engine.connection_review_package import build_review_report
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    TASK_COMPLETE_REVIEW,
    TASK_CONFIRM_AI_VALUES,
    TASK_CONFIRM_AUTOMATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_MEMBER,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_SELECT_POSITION,
    ExceptionResolutionPackage,
    HumanResolution,
    apply_human_resolution,
    build_exception_resolution_package,
)
from app.cad_engine.project_automation import PROJECT_WARNING_NO_AUTO, PROJECT_WARNING_SCOPE_UNREPORTED
from app.cad_engine.resolution_rerun import (
    RESOLUTION_RERUN_SCOPE_STATEMENT,
    ConnectionRerunOutcome,
    RefusedResolution,
    ResolutionRerunResult,
    rerun_connection_after_resolutions,
    rerun_project_after_resolutions,
)
from app.cad_engine.reviewed_connection_specification import PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED
from app.cad_engine.reviewed_connection_validation_gate import VALIDATED_LAYERS

from tests.test_automation_gate import KNOWN_MARKS, _holes, _reviewed_package
from tests.test_exception_resolution import (
    ARKLES_AI_MARKS,
    ARKLES_BLOCKER_CODES,
    _answer,
    _arkles_project,
    _tasks,
    _without_identity,
)
from tests.test_project_automation import _collection, _evaluate, _member_rows, _member_placements
from tests.test_project_extraction_intake import needs_real_capture, real_known_marks
from tests.test_real_multi_member_cad import make_multi_member_matcher

PROJECT_ID = "PROJ-7AD"


def _seven_ad_package(*packages, member_rows=None, require_confirmation_by_package_id=None, project_id=PROJECT_ID):
    """7AB evaluation followed by the 7AC contract build — the exact inputs of the 7AD re-run."""
    collection = _collection(*packages, project_id=project_id)
    result = _evaluate(collection, require_confirmation_by_package_id=require_confirmation_by_package_id)
    built = build_exception_resolution_package(
        result, collection, member_rows=_member_rows() if member_rows is None else member_rows,
    )
    return collection, built


def _rerun(built, collection, *, require_confirmation_by_package_id=None, member_context=True):
    """The 7AD closed loop over one exception package + collection, with the shared member context."""
    return rerun_project_after_resolutions(
        built,
        collection,
        member_rows=_member_rows() if member_context else None,
        member_placements=_member_placements() if member_context else None,
        section_matcher=make_multi_member_matcher() if member_context else None,
        require_confirmation_by_package_id=require_confirmation_by_package_id,
    )


def _resolve(built, task, answer, evidence=""):
    """Record one human resolution through the existing 7AC apply (never bypassed)."""
    return apply_human_resolution(built, _answer(task, answer, evidence=evidence))


def _blocks(outcome):
    return {b.code for b in outcome.blockers}


# =============================================================================
# 1-6. The genuine closed loop: real AUTO, real validation, no injected evidence
# =============================================================================
def test_genuine_resolution_produces_AUTO_only_through_the_existing_chain():
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    group = built.connection_tasks[0]
    tasks = _tasks(group)
    assert group.decision == AUTOMATION_DECISION_REVIEW
    assert _blocks(group) == {AUTOMATION_BLOCKER_HOLE_DIAMETER, AUTOMATION_BLOCKER_SPECIFICATION,
                              AUTOMATION_BLOCKER_VALIDATION}

    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes(), evidence="D15 hole schedule")
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]

    # The whole loop, one genuine outcome.
    assert (rerun.connections_total, rerun.auto_count, rerun.confirm_count, rerun.review_count) == (1, 1, 0, 0)
    assert (rerun.applied_count, rerun.refused_count) == (3, 0)
    assert outcome.before_decision == AUTOMATION_DECISION_REVIEW
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert outcome.blockers == ()
    assert any(r.code == "AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED" for r in outcome.reasons)
    assert outcome.specification_accepted is True
    assert outcome.validation_passed is True
    assert outcome.validation_failure is None
    assert outcome.validation_layers == VALIDATED_LAYERS
    assert outcome.assembly_built is True
    assert outcome.remaining_task_ids == ()
    assert len(outcome.resolutions_applied) == 3 and outcome.resolutions_refused == ()
    assert outcome.resolutions_applied[0].answer == _holes()

    # 7Z's own evidence summary carries only the genuine 7R evidence.
    assert "validation evidence: passed" in outcome.evidence_summary
    assert "7V completeness: passed" in outcome.evidence_summary

    # The hole diameter flowed through the rebuild verbatim, with human provenance (req 6).
    rebuilt = outcome.rebuilt_package
    assert rebuilt.supplement.holes == _holes()
    holes_entry = next(e for e in build_review_report(rebuilt).entries if e.field == "holes")
    assert holes_entry.current_value == _holes()
    assert holes_entry.provenance == PROVENANCE_HUMAN_SUPPLEMENTED

    # Project-level facts are recomputed, never asserted.
    assert any("RP-0001: REVIEW -> AUTO" in line for line in rerun.summary)
    assert [w.code for w in rerun.warnings] == [PROJECT_WARNING_SCOPE_UNREPORTED]
    assert rerun.intake_scope.pages_received is None
    assert rerun.scope_statement == RESOLUTION_RERUN_SCOPE_STATEMENT


def test_rerun_runs_the_genuine_validation_and_7Z_receives_only_7R_evidence(monkeypatch):
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes())
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)

    calls = []
    real_validate = automation_pipeline.validate_reviewed_connection_assembly
    real_gate = automation_pipeline.evaluate_automation_gate

    def recording_validate(assembly):
        calls.append("7R:validate")
        return real_validate(assembly)

    def recording_gate(package, *, validation_passed=None, require_confirmation=()):
        calls.append(("7Z:gate", validation_passed))
        return real_gate(package, validation_passed=validation_passed, require_confirmation=require_confirmation)

    monkeypatch.setattr(automation_pipeline, "validate_reviewed_connection_assembly", recording_validate)
    monkeypatch.setattr(automation_pipeline, "evaluate_automation_gate", recording_gate)

    # If 7AD (or anything but 7AA) reached these module-level entry points directly,
    # the rerun would raise — the genuine chain uses 7AA's own by-name bindings.
    import app.cad_engine.reviewed_connection_drawing_gate as drawing_gate_module
    import app.cad_engine.two_member_connection as two_member_module
    import app.drawing_generator.interface as drawing_interface
    import app.drawing_generator.pdf_builder as pdf_builder

    def forbidden(*args, **kwargs):
        raise AssertionError("this entry point must be reached only through 7AA")

    for module, name in [
        (reviewed_connection_assembly, "build_reviewed_two_member_connection_assembly"),
        (two_member_module, "build_two_member_connection_assembly"),
        (reviewed_connection_validation_gate, "validate_reviewed_connection_assembly"),
        (drawing_gate_module, "generate_fabrication_drawing_from_reviewed_assembly"),
        (drawing_interface, "generate_connection_fabrication_drawing_pdf"),
        (pdf_builder, "build_connection_pdf"),
    ]:
        monkeypatch.setattr(module, name, forbidden)

    rerun = _rerun(built, collection)

    # 7R genuinely ran exactly once, then 7Z was called exactly once — with the genuine
    # 7R outcome as its validation evidence, and nothing else.
    assert calls == ["7R:validate", ("7Z:gate", True)]
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert "validation evidence: passed" in outcome.evidence_summary


def test_no_caller_supplied_validation_evidence_parameter_exists():
    for function in (rerun_connection_after_resolutions, rerun_project_after_resolutions):
        parameters = inspect.signature(function).parameters
        assert "validation_passed" not in parameters
        assert "validation_evidence" not in parameters
        for name in ("member_rows", "member_placements", "section_matcher"):
            assert name in parameters


def test_approval_alone_cannot_create_AUTO():
    # Review status 'in_review' AND missing holes: approving is the human's workflow
    # decision only — the engineering gap must still be supplied by data that passes.
    collection, built = _seven_ad_package(_reviewed_package(review_status="in_review", holes=None))
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_COMPLETE_REVIEW], None)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert {AUTOMATION_BLOCKER_HOLE_DIAMETER, AUTOMATION_BLOCKER_SPECIFICATION,
            AUTOMATION_BLOCKER_VALIDATION} <= _blocks(outcome)
    assert len(outcome.resolutions_applied) == 1
    assert outcome.rebuilt_package.supplement.review_status == "approved"  # 7AC semantics: approval recorded
    assert outcome.rebuilt_package.supplement.holes is None                # ...but approval supplies no engineering data
    assert len(outcome.remaining_task_ids) == 3
    assert outcome.specification_accepted is False
    assert any("holes" in line for line in outcome.evidence_summary)
    assert rerun.auto_count == 0


def test_confirmation_alone_never_forces_AUTO():
    hold = {"RP-0001": ("ENGINEER",)}
    collection, built = _seven_ad_package(_reviewed_package(), require_confirmation_by_package_id=hold)
    group = built.connection_tasks[0]
    assert group.decision == AUTOMATION_DECISION_CONFIRM
    tasks = _tasks(group)
    assert set(tasks) == {TASK_CONFIRM_AUTOMATION}
    built = _resolve(built, tasks[TASK_CONFIRM_AUTOMATION], None)

    # The human answered — but the existing 7Z hold is a genuine pipeline requirement: CONFIRM again.
    rerun = _rerun(built, collection, require_confirmation_by_package_id=hold)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_CONFIRM
    assert len(outcome.resolutions_applied) == 1
    # The confirmation changed no supplement value (7AC semantics, preserved through the rebuild).
    assert outcome.rebuilt_package.supplement == collection.candidates[0].package.supplement

    # Only when the genuine chain's remaining requirement (the hold) is released does 7Z say AUTO.
    rerun = _rerun(built, collection)
    assert rerun.outcomes[0].decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# 7-11. Invalid answers: never converted, never invented, never bypass the gates
# =============================================================================
def test_nominal_size_never_becomes_a_hole_diameter():
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    tasks = _tasks(built.connection_tasks[0])
    nominal = _holes(diameter_mm="M20")
    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], nominal)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]

    # Not refused (holes carry no authoritative choice list) — but NOT converted, NOT
    # interpreted: the value flows verbatim into the supplement and the existing 7V/7D
    # rules reject it.
    assert outcome.resolutions_refused == ()
    assert outcome.rebuilt_package.supplement.holes["diameter_mm"] == "M20"
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    specification = next(b for b in outcome.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert "M20" in specification.message
    assert "Ø" not in specification.message
    assert "Ø" not in repr(outcome.rebuilt_package)
    assert outcome.specification_accepted is False
    assert outcome.validation_failure is not None
    assert outcome.validation_failure.stage == PIPELINE_STAGE_CONNECTION  # the genuine 7D adapter rejected it
    assert "validation evidence: failed" in outcome.evidence_summary


def test_unknown_member_mark_is_refused_and_remains_unresolved():
    # The reviewer supplied unknown marks; the authoritative choices come from the validated
    # member context only — an unknown mark is refused outright and never reaches the supplement.
    collection, built = _seven_ad_package(_reviewed_package(connected_member_marks=["A", "B"]))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER]
    assert task.allowed_choices == KNOWN_MARKS
    built = _resolve(built, task, ("NOT-A-MARK",))

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert len(outcome.resolutions_refused) == 1 and outcome.resolutions_applied == ()
    refused = outcome.resolutions_refused[0]
    assert refused.resolution.task_id == task.task_id
    assert "NOT-A-MARK" in refused.reason
    assert task.task_id in outcome.remaining_task_ids
    assert AUTOMATION_BLOCKER_MEMBER_IDENTITY in _blocks(outcome)
    # The unknown marks stay exactly as the reviewer left them — nothing was substituted.
    assert outcome.rebuilt_package.supplement.connected_member_marks == ["A", "B"]


def test_valid_member_answer_repairs_identity_through_the_chain():
    collection, built = _seven_ad_package(_reviewed_package(connected_member_marks=["A", "B"]))
    group = built.connection_tasks[0]
    tasks = _tasks(group)
    built = _resolve(built, tasks[TASK_SELECT_MEMBER], KNOWN_MARKS, evidence="member schedule 7")
    # The remaining tasks of this fixture are the spec/validation acknowledgments.
    assert set(tasks) - {TASK_SELECT_MEMBER} <= {TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION}
    for task_type in (TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION):
        if task_type in tasks:
            built = _resolve(built, tasks[task_type], None)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert outcome.resolutions_refused == ()
    assert outcome.rebuilt_package.supplement.connected_member_marks == list(KNOWN_MARKS)


def test_invalid_position_is_refused_but_valid_position_flows():
    collection, built = _seven_ad_package(_reviewed_package(position=None))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_POSITION]
    built = _resolve(built, task, "MIDDLE")

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert len(outcome.resolutions_refused) == 1
    assert "MIDDLE" in outcome.resolutions_refused[0].reason
    assert task.task_id in outcome.remaining_task_ids
    assert AUTOMATION_BLOCKER_POSITION in _blocks(outcome)
    assert outcome.rebuilt_package.supplement.position is None


def test_invalid_attachment_surface_is_refused_and_7R_catches_wrong_but_valid_vocabulary():
    # (a) An invented surface reference is refused — no nearest face, no face indices.
    collection, built = _seven_ad_package(_reviewed_package(attachments=None))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_ATTACHMENT]
    built = _resolve(built, task, [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "FACE1"},
        {"member_mark": "L2", "surface_reference": "END"},
    ])

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert len(outcome.resolutions_refused) == 1
    assert "FACE1" in outcome.resolutions_refused[0].reason
    assert AUTOMATION_BLOCKER_ATTACHMENT in _blocks(outcome)
    assert outcome.rebuilt_package.supplement.attachments is None

    # (b) A vocabulary-valid but geometrically wrong answer is NOT refused — the genuine
    # 7R catches the resulting geometry/interface problem.
    collection, built = _seven_ad_package(_reviewed_package(attachments=None))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_ATTACHMENT]
    swapped = [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "END"},
    ]
    built = _resolve(built, task, swapped)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.resolutions_refused == ()
    assert list(outcome.rebuilt_package.supplement.attachments) == swapped
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert outcome.specification_accepted is True          # 7V accepted the records...
    assert outcome.validation_passed is False              # ...and the genuine 7R rejected the geometry
    assert outcome.validation_failure is not None
    assert outcome.validation_failure.stage == PIPELINE_STAGE_VALIDATION
    assert AUTOMATION_BLOCKER_VALIDATION in _blocks(outcome)


@pytest.mark.parametrize("bad,check", [
    (float("nan"), lambda value: math.isnan(value)),
    (float("inf"), lambda value: math.isinf(value)),
    (True, lambda value: value is True),
    ("not-a-number", lambda value: value == "not-a-number"),
])
def test_malformed_numeric_answers_flow_verbatim_and_existing_rules_decide(bad, check):
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    task = _tasks(built.connection_tasks[0])[TASK_PROVIDE_HOLE_DIAMETER]
    built = _resolve(built, task, _holes(diameter_mm=bad))

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    # Not refused, not fixed, not coerced: the value is stored exactly as supplied.
    assert outcome.resolutions_refused == ()
    assert check(outcome.rebuilt_package.supplement.holes["diameter_mm"])
    # The existing 7V/7D rules are authoritative and reject it.
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    specification = next(b for b in outcome.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert "diameter_mm" in specification.message
    assert outcome.specification_accepted is False


# =============================================================================
# 12-16. Partial resolution, isolation, task consistency
# =============================================================================
def test_partial_resolution_leaves_remaining_blockers_visible():
    collection, built = _seven_ad_package(_reviewed_package(position=None, holes=None))
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes())

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_POSITION in _blocks(outcome)
    assert AUTOMATION_BLOCKER_HOLE_DIAMETER not in _blocks(outcome)
    assert AUTOMATION_BLOCKER_SPECIFICATION in _blocks(outcome)
    assert AUTOMATION_BLOCKER_VALIDATION in _blocks(outcome)
    assert len(outcome.remaining_task_ids) == 3
    assert any("RP-0001: REVIEW -> REVIEW" in line for line in rerun.summary)
    assert rerun.review_count == 1 and rerun.auto_count == 0


def test_multiple_resolutions_on_one_connection_work_together():
    collection, built = _seven_ad_package(_reviewed_package(position=None))
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_SELECT_POSITION], "END")
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert len(outcome.resolutions_applied) == 3
    assert outcome.resolutions_refused == ()
    assert outcome.rebuilt_package.supplement.position == "END"
    assert outcome.remaining_task_ids == ()


def test_connections_are_isolated_and_project_counts_are_genuine():
    collection, built = _seven_ad_package(_reviewed_package(holes=None), _reviewed_package(position=None))
    # Resolve ONLY the first connection's tasks; the second connection is untouched.
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes())
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)

    rerun = _rerun(built, collection)
    first, second = rerun.outcomes
    # Submission order preserved; decisions coexist; counts recomputed from genuine outcomes.
    assert [o.review_package_id for o in rerun.outcomes] == ["RP-0001", "RP-0002"]
    assert (rerun.auto_count, rerun.confirm_count, rerun.review_count) == (1, 0, 1)
    assert first.decision == AUTOMATION_DECISION_AUTO and second.decision == AUTOMATION_DECISION_REVIEW
    assert first.before_decision == AUTOMATION_DECISION_REVIEW
    # The untouched connection applied nothing and its package was rebuilt to the same content.
    assert second.resolutions_applied == () and second.resolutions_refused == ()
    assert len(second.remaining_task_ids) == len(_tasks(built.connection_tasks[1]))
    assert second.rebuilt_package.supplement == collection.candidates[1].package.supplement
    assert AUTOMATION_BLOCKER_POSITION in _blocks(second)
    assert dict(rerun.before_blockers_by_code) == {
        AUTOMATION_BLOCKER_HOLE_DIAMETER: 1, AUTOMATION_BLOCKER_POSITION: 1,
        AUTOMATION_BLOCKER_SPECIFICATION: 2, AUTOMATION_BLOCKER_VALIDATION: 2,
    }
    assert dict(rerun.after_blockers_by_code) == {
        AUTOMATION_BLOCKER_POSITION: 1, AUTOMATION_BLOCKER_SPECIFICATION: 1,
        AUTOMATION_BLOCKER_VALIDATION: 1,
    }


def test_unknown_task_double_resolution_and_wrong_connection_are_rejected():
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    task = _tasks(built.connection_tasks[0])[TASK_REVIEW_SPECIFICATION]

    # Unknown task id.
    with pytest.raises(ValueError):
        apply_human_resolution(built, HumanResolution("RP-0001-T99", TASK_REVIEW_SPECIFICATION,
                                                      ANSWER_ACKNOWLEDGMENT, None))
    # Double resolution of the same task.
    built = _resolve(built, task, None)
    with pytest.raises(ValueError):
        apply_human_resolution(built, _answer(task, None))
    # Answer type incompatible with the task.
    with pytest.raises(ValueError):
        apply_human_resolution(built, HumanResolution(task.task_id, task.task_type, "MEMBER_SELECTION", ()))

    # A resolution can never be applied to a different connection: misaligned collections refuse.
    other_collection, other_built = _seven_ad_package(_reviewed_package(), _reviewed_package())
    with pytest.raises(ValueError, match="one-to-one"):
        rerun_project_after_resolutions(built, other_collection)
    with pytest.raises(ValueError, match="one-to-one"):
        rerun_project_after_resolutions(other_built, collection)
    # Unknown confirmation hold ids.
    with pytest.raises(ValueError, match="unknown review_package_id"):
        _rerun(built, collection, require_confirmation_by_package_id={"RP-9999": ("ENGINEER",)})


def test_grouped_answers_obey_the_same_vocabulary_rules():
    fixture = _reviewed_package(connected_member_marks=["A", "B"], position=None, attachments=None)

    # (a) Unknown member mark inside the grouped answer.
    collection, built = _seven_ad_package(fixture)
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    built = _resolve(built, task, (("NOT-A-MARK",), "END", [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
    ]))
    rerun = _rerun(built, collection)
    assert len(rerun.outcomes[0].resolutions_refused) == 1
    assert "NOT-A-MARK" in rerun.outcomes[0].resolutions_refused[0].reason

    # (b) Invalid position inside the grouped answer.
    collection, built = _seven_ad_package(fixture)
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    built = _resolve(built, task, (KNOWN_MARKS, "MIDDLE", [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
    ]))
    rerun = _rerun(built, collection)
    assert len(rerun.outcomes[0].resolutions_refused) == 1
    assert "MIDDLE" in rerun.outcomes[0].resolutions_refused[0].reason

    # (c) Invented surface inside the grouped answer.
    collection, built = _seven_ad_package(fixture)
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    built = _resolve(built, task, (KNOWN_MARKS, "END", [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "FACE1"},
        {"member_mark": "L2", "surface_reference": "START"},
    ]))
    rerun = _rerun(built, collection)
    assert len(rerun.outcomes[0].resolutions_refused) == 1
    assert "FACE1" in rerun.outcomes[0].resolutions_refused[0].reason
    assert rerun.outcomes[0].rebuilt_package.supplement.position is None

    # (d) A fully valid grouped answer goes through the whole genuine chain.
    collection, built = _seven_ad_package(fixture)
    tasks = _tasks(built.connection_tasks[0])
    attachments = [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
    ]
    built = _resolve(built, tasks[TASK_SELECT_MEMBER_POSITION_ATTACHMENT], (KNOWN_MARKS, "END", attachments))
    for task_type in (TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION):
        if task_type in tasks:
            built = _resolve(built, tasks[task_type], None)
    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert outcome.resolutions_refused == ()
    assert outcome.rebuilt_package.supplement.connected_member_marks == list(KNOWN_MARKS)
    assert outcome.rebuilt_package.supplement.position == "END"
    assert list(outcome.rebuilt_package.supplement.attachments) == attachments


# =============================================================================
# 17-19. Immutability, provenance, determinism
# =============================================================================
def test_nothing_is_mutated_and_every_result_is_frozen():
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    snapshot_collection = copy.deepcopy(collection)
    snapshot_built = copy.deepcopy(built)
    original_package = collection.candidates[0].package

    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes())
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)
    resolved_snapshot = copy.deepcopy(built)

    rerun = _rerun(built, collection)

    # The exception package, the collection and the original package are untouched.
    assert built == resolved_snapshot
    assert collection == snapshot_collection
    assert collection.candidates[0].package == snapshot_collection.candidates[0].package
    # The rebuild created NEW structures — the original package object and its supplement are intact.
    assert rerun.outcomes[0].rebuilt_package is not original_package
    assert rerun.outcomes[0].rebuilt_package.supplement is not original_package.supplement
    assert rerun.outcomes[0].rebuilt_package.extraction == original_package.extraction
    # The rebuilt package still carries the original AI extraction verbatim.

    # Every result structure is frozen plain data.
    for frozen in (rerun, rerun.outcomes[0], rerun.outcomes[0].resolutions_applied[0], rerun.intake_scope):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(frozen, "_not_a_field", "boom")
    with pytest.raises(dataclasses.FrozenInstanceError):
        rerun.auto_count = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        rerun.outcomes[0].decision = AUTOMATION_DECISION_REVIEW


def test_ai_values_stay_ai_extracted_and_human_answers_carry_human_provenance():
    # plate=None: the AI's own plate record exists but is unconfirmed -> confirming it is the
    # human's correction, represented with 7W's own HUMAN_REVIEWED semantics.
    collection, built = _seven_ad_package(_reviewed_package(plate=None))
    tasks = _tasks(built.connection_tasks[0])
    confirm = tasks[TASK_CONFIRM_AI_VALUES]
    expected_plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    assert confirm.current_ai_value == (("plate", expected_plate),)
    built = _resolve(built, confirm, ("plate",), evidence="D15 checked")
    for task_type in (TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION):
        if task_type in tasks:
            built = _resolve(built, tasks[task_type], None)

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    entry = next(e for e in build_review_report(outcome.rebuilt_package).entries if e.field == "plate")
    assert entry.provenance == PROVENANCE_HUMAN_REVIEWED
    assert entry.ai_value == expected_plate        # "AI originally said X" — preserved exactly
    assert entry.current_value == expected_plate   # "human subsequently confirmed Y" — the same record, human label
    # The AI extraction object itself was never rewritten.
    assert outcome.rebuilt_package.extraction == collection.candidates[0].package.extraction


def test_repeated_reruns_are_deterministic():
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes())
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)

    first = _rerun(built, collection)
    second = _rerun(built, collection)
    assert first == second
    assert repr(first) == repr(second)
    assert first.outcomes[0].decision == second.outcomes[0].decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# 20-22. The real Arkles capture: REVIEW stays REVIEW; AI values never converted
# =============================================================================
@needs_real_capture
def test_real_arkles_approval_only_stays_REVIEW_with_all_blockers_visible():
    intake, result = _arkles_project()
    built = build_exception_resolution_package(result, intake.collection)
    snapshot = copy.deepcopy(intake)
    for group in built.connection_tasks:
        built = _resolve(built, _tasks(group)[TASK_COMPLETE_REVIEW], None)

    rerun = rerun_project_after_resolutions(built, intake.collection)
    assert (rerun.connections_total, rerun.auto_count, rerun.confirm_count, rerun.review_count) == (3, 0, 0, 3)
    assert (rerun.applied_count, rerun.refused_count) == (3, 0)
    expected_after = ARKLES_BLOCKER_CODES - {AUTOMATION_BLOCKER_REVIEW_STATUS}
    for outcome in rerun.outcomes:
        assert outcome.before_decision == AUTOMATION_DECISION_REVIEW
        assert outcome.decision == AUTOMATION_DECISION_REVIEW
        assert {b.code for b in outcome.before_blockers} == ARKLES_BLOCKER_CODES
        assert _blocks(outcome) == expected_after
        assert len(outcome.remaining_task_ids) == 7  # 8 tasks: everything but the approval
    assert dict(rerun.before_blockers_by_code) == {code: 3 for code in ARKLES_BLOCKER_CODES}
    assert dict(rerun.after_blockers_by_code) == {code: 3 for code in expected_after}
    assert any("REVIEW -> REVIEW" in line for line in rerun.summary)
    assert [w.code for w in rerun.warnings] == [PROJECT_WARNING_NO_AUTO]
    assert rerun.intake_scope.pages_received == 30

    # Lossless: the rebuilt packages carry the intake candidates' extractions verbatim,
    # and the AI's nominal sizes survive everywhere unconverted.
    for outcome, candidate in zip(rerun.outcomes, intake.collection.candidates):
        assert outcome.rebuilt_package.extraction == candidate.package.extraction
        assert outcome.rebuilt_package.supplement.review_status == "approved"
    assert "M12" in repr(rerun.outcomes[0].rebuilt_package)
    assert "SQ4 12mm" in repr(rerun.outcomes[2].rebuilt_package)
    assert "Ø" not in repr(rerun)
    # The intake itself was never mutated by the whole loop.
    assert intake.collection == snapshot.collection


@needs_real_capture
def test_real_arkles_legitimate_resolutions_still_cannot_reach_AUTO():
    intake, result = _arkles_project()
    built = build_exception_resolution_package(result, intake.collection)
    for group in built.connection_tasks:
        built = _resolve(built, _tasks(group)[TASK_COMPLETE_REVIEW], None)
    # Legitimate data the capture really does allow: real member marks from the validated
    # context, an existing position, existing attachment surfaces.
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    marks = tuple(real_known_marks())[:2]
    attachments = [{"member_mark": mark, "surface_reference": "END"} for mark in marks]
    built = _resolve(built, task, (marks, "END", attachments))

    rerun = rerun_project_after_resolutions(built, intake.collection)
    outcome = rerun.outcomes[0]
    assert len(outcome.resolutions_applied) == 2 and outcome.resolutions_refused == ()
    # Members, position and attachments are now supplied — the engineering gaps that remain
    # (plate, hole diameters, location) keep the connection genuinely REVIEW. No AUTO.
    assert {b.code for b in outcome.blockers} == {
        AUTOMATION_BLOCKER_PLATE, AUTOMATION_BLOCKER_HOLE_DIAMETER, AUTOMATION_BLOCKER_LOCATION,
        AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION,
    }
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert outcome.rebuilt_package.supplement.connected_member_marks == list(marks)
    assert outcome.rebuilt_package.supplement.position == "END"
    assert list(outcome.rebuilt_package.supplement.attachments) == attachments
    assert all(o.decision == AUTOMATION_DECISION_REVIEW for o in rerun.outcomes)
    # Nominal sizes still unconverted.
    assert "M12" in repr(outcome.rebuilt_package)


@needs_real_capture
def test_real_arkles_timber_member_answers_are_refused_and_ai_values_never_converted():
    intake, result = _arkles_project()
    built = build_exception_resolution_package(result, intake.collection)
    for group in built.connection_tasks:
        built = _resolve(built, _tasks(group)[TASK_COMPLETE_REVIEW], None)
    # The AI's own timber strings are not authoritative members — answering with them is refused.
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    timber = tuple(ARKLES_AI_MARKS[0])
    built = _resolve(built, task, (timber, "END", [{"member_mark": m, "surface_reference": "END"} for m in timber]))

    rerun = rerun_project_after_resolutions(built, intake.collection)
    outcome = rerun.outcomes[0]
    assert len(outcome.resolutions_refused) == 1 and len(outcome.resolutions_applied) == 1  # the approval only
    refused = outcome.resolutions_refused[0]
    assert refused.resolution.task_id == task.task_id
    assert "stringer 140x45 H4 SG8" in refused.reason
    assert task.task_id in outcome.remaining_task_ids
    # Nothing of the refused answer reached the supplement; the blockers stay.
    assert outcome.rebuilt_package.supplement.connected_member_marks is None
    assert outcome.rebuilt_package.supplement.position is None
    assert outcome.rebuilt_package.supplement.attachments is None
    assert _blocks(outcome) == ARKLES_BLOCKER_CODES - {AUTOMATION_BLOCKER_REVIEW_STATUS}
    assert rerun.refused_count == 1
    assert "M12" in repr(outcome.rebuilt_package)
    assert "Ø" not in repr(rerun)


def test_reviewer_given_identity_completes_the_AUTO_path_through_the_rerun():
    # The never-persisted case: every engineering resolution is in place except the
    # identity. The task addresses no 7Z blocker, but 7AE requires the identifier — so
    # this is the step that makes the downstream fabrication chain possible.
    collection, built = _seven_ad_package(_without_identity(_reviewed_package(holes=None)))
    tasks = _tasks(built.connection_tasks[0])
    identity = tasks[TASK_PROVIDE_CONNECTION_IDENTITY]
    assert identity.blocker_codes == ()

    built = _resolve(built, tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes(), evidence="D15 hole schedule")
    built = _resolve(built, tasks[TASK_REVIEW_SPECIFICATION], None)
    built = _resolve(built, tasks[TASK_REVIEW_VALIDATION], None)
    built = _resolve(built, identity, "CONN-D15-001")

    rerun = _rerun(built, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert outcome.blockers == ()
    assert outcome.remaining_task_ids == ()
    assert (rerun.applied_count, rerun.refused_count) == (4, 0)

    # The identifier reached the rebuilt supplement — and, as 7W documents, with no
    # provenance label: it is an identifier, not an engineering field.
    assert outcome.rebuilt_package.supplement.connection_id == "CONN-D15-001"
    report = build_review_report(outcome.rebuilt_package)
    assert report.identity_missing == ()
    assert "connection_id" not in report.provenance


def test_non_identifier_identity_answer_is_refused_even_if_shape_validation_is_bypassed():
    # 7AD re-checks every resolution against the task vocabulary itself — an identity
    # resolution smuggled past the 7AC payload validation is still refused.
    collection, built = _seven_ad_package(_without_identity(_reviewed_package(holes=None)))
    group = built.connection_tasks[0]
    task = _tasks(group)[TASK_PROVIDE_CONNECTION_IDENTITY]

    smuggled = dataclasses.replace(
        task,
        resolution=HumanResolution(task.task_id, task.task_type, task.answer_type, "   ", "?"),
    )
    tampered = dataclasses.replace(
        built,
        connection_tasks=(
            dataclasses.replace(
                group,
                tasks=tuple(t if t.task_id != task.task_id else smuggled for t in group.tasks),
            ),
        ),
    )

    rerun = _rerun(tampered, collection)
    outcome = rerun.outcomes[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert len(outcome.resolutions_refused) == 1
    assert "identifier" in outcome.resolutions_refused[0].reason
    assert task.task_id in outcome.remaining_task_ids
    assert outcome.rebuilt_package.supplement.connection_id is None


# =============================================================================
# 23-30. Import purity, wiring purity, loud errors
# =============================================================================
def test_resolution_rerun_module_imports_are_pure():
    source = Path(resolution_rerun.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "collections.abc",
        "dataclasses",
        "app.cad_engine.automation_gate",
        "app.cad_engine.automation_pipeline",
        "app.cad_engine.connection_review_package",
        "app.cad_engine.exception_resolution",
        "app.cad_engine.placement",
        "app.cad_engine.project_automation",
        "app.cad_engine.project_connection_review",
    }
    # No network, no AI, no config, no web/db/UI, no drawing, no CAD entry beyond the 7AA seam.
    assert "http" not in source.lower()
    assert "socket" not in imported and "requests" not in imported


def test_module_imports_without_any_secret_environment():
    env = {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("ANTHROPIC", "SUPABASE", "FIREWORKS", "OPENAI"))
    }
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import app.cad_engine.resolution_rerun"],
        cwd=str(root), env=env, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_7AD_never_calls_gate_validation_assembly_or_drawing_entry_points_directly():
    source = Path(resolution_rerun.__file__).read_text()
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "evaluate_automation_gate" not in called
    assert "evaluate_candidate_automation" not in called
    assert "validate_reviewed_connection_assembly" not in called
    assert "build_reviewed_two_member_connection_assembly" not in called
    assert "generate_fabrication_drawing_from_reviewed_assembly" not in called
    assert "generate_connection_fabrication_drawing_pdf" not in called
    assert "build_connection_pdf" not in called
    # The ONLY pipeline entry 7AD ever calls is 7AA's own evaluation function.
    assert called & {"evaluate_reviewed_connection_for_automation"} == {"evaluate_reviewed_connection_for_automation"}


def test_programming_errors_propagate_loudly():
    collection, built = _seven_ad_package(_reviewed_package(holes=None))
    group = built.connection_tasks[0]
    candidate = collection.candidates[0]

    with pytest.raises(TypeError):
        rerun_project_after_resolutions("not-an-exception-package", collection)
    with pytest.raises(TypeError):
        rerun_project_after_resolutions(built, "not-a-collection")
    with pytest.raises(TypeError):
        rerun_connection_after_resolutions("not-a-group", candidate.package)
    with pytest.raises(TypeError):
        rerun_connection_after_resolutions(group, "not-a-package")

    assert isinstance(rerun_project_after_resolutions(built, collection), ResolutionRerunResult)
    assert isinstance(rerun_connection_after_resolutions(group, candidate.package), ConnectionRerunOutcome)
    assert isinstance(RefusedResolution(HumanResolution("t", "TASK", "ANSWER", None), "reason"), RefusedResolution)
    assert isinstance(ExceptionResolutionPackage, type)
