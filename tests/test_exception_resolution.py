"""
Milestone 7AC — tests for the exception resolution contract
(app.cad_engine.exception_resolution), the backend-only deterministic
contract that turns an existing 7AB project automation result into
structured human-review tasks and turns structured human answers back
into the EXISTING 7W review-supplement mechanism:

    7AB ProjectAutomationResult + 7X collection
            -> build_exception_resolution_package()   (tasks per connection)
            -> apply_human_resolution()               (task RESOLVED; grants nothing)
            -> build_resolved_supplement()            (the existing 7W supplement)
            -> 7X update / create_review_package -> 7V -> 7AA -> 7R -> 7Z re-decide

These tests prove, per the milestone's requirements (numbered 1-34):

   1  AUTO connections get ZERO exception-resolution tasks.
   2  CONFIRM connections get exactly one confirmation task — which is
      not REVIEW (it is a confirmation, not missing information) and
      not AUTO (the hold remains until the pipeline re-runs).
   3  REVIEW connections get blocking tasks covering every 7Z blocker,
      in a fixed deterministic order; no blocker is ever hidden.
   4  Missing review status is a workflow task (COMPLETE_REVIEW),
      distinct from missing engineering information; approving can
      never substitute for supplying data.
   5  Grouping (SELECT_MEMBER_POSITION_ATTACHMENT) happens only when
      one human answer genuinely supplies all three blocked fields,
      and the grouped task lists every code it addresses.
   6  High AI confidence never suppresses review tasks.
   7  Allowed choices come ONLY from authoritative existing context:
      member rows, else the collection's known marks, else () —
      never guessed from AI strings; position/surface choices are the
      existing START/END geometry vocabulary (pinned to
      connections.SUPPORTED_CONNECTION_POSITIONS).
   8  current_ai_value is lossless: AI member references and nominal
      bolt sizes survive verbatim; "M12"/"SQ4 12mm" are never
      converted into hole diameters ("Ø" never appears); missing
      fields show None, never a fabricated value.
   9  Malformed AI fields are shown verbatim; conflicts surface one
      task per 7W issue; the 7V rejection and the preserved 7R failure
      are carried in their tasks' questions verbatim.
  10  Source facts are preserved (review_package_id, submission
      index, source page, detail reference None when none exists,
      connection_id) and the 7Y scope facts (pages received / parse
      failures / not analysed) stay visible on the package.
  11  Task/result/human-resolution structures are immutable (frozen
      dataclasses), resolutions snapshot their payloads, inputs are
      never mutated, and repeated builds are identical (stable repr).
  12  Resolutions map onto the EXISTING 7W supplement fields and its
      conflict semantics (confirm drops a supplied value, supply drops
      a confirmation — the ambiguous both-states is never produced),
      and the 7V/7R/7Z gates re-decide everything: an acknowledgment
      grants nothing, a confirmation never forces AUTO, an invalid
      answer cannot bypass 7V, invalid geometry cannot bypass 7R.
  13  The module is import-pure (stdlib + the review/automation
      layers only — no network, no Anthropic, no Supabase, no
      FastAPI, no app.config/ai_analysis/pipeline/main) and calls no
      CAD or drawing entry points at runtime.

FIXTURE PROVENANCE: synthetic tests reuse the established 7Z fixture
(SYNTHETIC AI-shaped extraction — actual schema keys, not produced by
any AI — plus a TEST / HUMAN-REVIEWED / SUPPLEMENTED supplement), the
established 7O/7R member fixtures and the 7AB helpers. The Arkles
tests use the real captured JSON through the existing 7Y intake — no
Claude calls, no network. Nothing here is claimed to be AI-extracted
or drawing-derived, and no validation rule is re-implemented: every
gate decision comes from the genuine existing modules.
"""
import ast
import copy
import dataclasses
from pathlib import Path

import pytest

import app.cad_engine.exception_resolution as exception_resolution
from app.cad_engine import connections as connections_module
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_CONFLICT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_MALFORMED_FIELDS,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_PROVENANCE,
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_STAGE_CONNECTION,
    PIPELINE_STAGE_VALIDATION,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.connection_review_package import (
    ConnectionReviewSupplement,
    ai_connection_to_extraction,
    build_review_report,
    create_review_package,
)
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_FIELD_DECISION,
    ANSWER_HOLES_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_POSITION_VALUE,
    CONNECTION_POSITION_CHOICES,
    EXCEPTION_RESOLUTION_SCOPE_STATEMENT,
    FIELD_DECISION_CONFIRM_AI,
    FIELD_DECISION_SUPPLY,
    STATUS_OPEN,
    STATUS_RESOLVED,
    TASK_COMPLETE_REVIEW,
    TASK_CONFIRM_AI_VALUES,
    TASK_CONFIRM_AUTOMATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_RESOLVE_CONFLICT,
    TASK_REVIEW_MALFORMED_FIELDS,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_MEMBER,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_SELECT_POSITION,
    ConnectionExceptionTasks,
    ExceptionResolutionPackage,
    HumanResolution,
    apply_human_resolution,
    build_exception_resolution_package,
    build_resolved_supplement,
)
from app.cad_engine.project_automation import (
    PROJECT_WARNING_NO_AUTO,
    evaluate_project_for_automation,
)
from app.cad_engine.project_connection_review import (
    ConnectionCandidate,
    ProjectConnectionReviewCollection,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    REVIEW_STATUS_APPROVED,
)
from app.cad_engine.reviewed_connection_validation_gate import VALIDATED_LAYERS
from tests.test_automation_gate import KNOWN_MARKS, _holes, _plate, _reviewed_package
from tests.test_project_automation import _collection, _evaluate, _member_rows, _member_placements
from tests.test_project_extraction_intake import (
    CAPTURE_PATH,
    load_capture,
    needs_real_capture,
    real_known_marks,
)
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher


def _build(collection, *, member_rows=None, **evaluate_overrides):
    """7AB evaluation followed by the 7AC contract build, over one collection."""
    result = _evaluate(collection, **evaluate_overrides)
    return result, build_exception_resolution_package(result, collection, member_rows=member_rows)


def _tasks(group):
    return {task.task_type: task for task in group.tasks}


def _answer(task, answer, evidence=""):
    return HumanResolution(task.task_id, task.task_type, task.answer_type, answer, evidence)


def _find_resolution(package, task):
    """The recorded resolution for one task of a package (all tasks are addressed at most once)."""
    for group in package.connection_tasks:
        for candidate in group.tasks:
            if candidate.task_id == task.task_id:
                assert candidate.status == STATUS_RESOLVED, candidate.task_id
                return candidate.resolution
    raise AssertionError(f"task {task.task_id} was never resolved")


def _rerun_pipeline(package, supplement):
    """The genuine re-run: the resolved supplement through 7W -> 7V -> 7AA -> 7R -> 7Z."""
    rebuilt = create_review_package(
        package.extraction, supplement, known_member_marks=package.known_member_marks,
    )
    return evaluate_reviewed_connection_for_automation(
        rebuilt,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )


def _raw_timber_package(known_member_marks):
    """A SYNTHETIC raw AI-shaped candidate (actual schema keys) naming timber members and a
    nominal bolt size — the milestone's own high-confidence example; not produced by any AI."""
    extraction = ai_connection_to_extraction({
        "connects_members": ["stringer 140x45 H4 SG8", "structure"],
        "connection_type": "bolted",
        "bolts": [{"quantity": None, "size": "M12", "grade": None}],
        "confidence": 99,
    })
    return create_review_package(extraction, known_member_marks=known_member_marks)


# =============================================================================
# 1. AUTO -> zero tasks; counts carried verbatim
# =============================================================================
def test_AUTO_connection_gets_zero_exception_resolution_tasks():
    result, package = _build(_collection(_reviewed_package()))

    assert (package.auto_count, package.confirm_count, package.review_count) == (1, 0, 0)
    group = package.connection_tasks[0]
    assert group.decision == AUTOMATION_DECISION_AUTO
    assert group.blockers == ()
    assert group.tasks == ()                      # no pointless human work is invented
    assert package.open_task_count == 0
    assert "SteelSpec found 0 task(s) that need your attention." in package.summary
    assert package.warnings == result.warnings    # project warnings carried verbatim, never tasks


def test_mixed_project_represents_every_connection_with_counts_verbatim():
    packages = [
        _reviewed_package(connection_id="D15/A-B-1"),  # AUTO
        _reviewed_package(connection_id="D15/A-B-2"),  # CONFIRM
        _reviewed_package(holes=None),                 # REVIEW
    ]
    _, package = _build(
        _collection(*packages),
        require_confirmation_by_package_id={"RP-0002": ("confirm reuse of the approved pattern",)},
    )

    assert [g.review_package_id for g in package.connection_tasks] == ["RP-0001", "RP-0002", "RP-0003"]
    assert [g.decision for g in package.connection_tasks] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_REVIEW,
    ]
    assert [len(g.tasks) for g in package.connection_tasks] == [0, 1, 3]
    assert package.open_task_count == 4
    assert (package.connections_total, package.auto_count, package.confirm_count, package.review_count) == (3, 1, 1, 1)


# =============================================================================
# 2. CONFIRM -> exactly one confirmation task, not REVIEW and not AUTO
# =============================================================================
def test_CONFIRM_connection_gets_a_confirmation_task_not_a_review_task():
    collection = _collection(_reviewed_package())
    item = "Reused approved end-plate pattern CONN-ENDPLATE-2: confirm against the project connection register."
    _, package = _build(
        collection, require_confirmation_by_package_id={"RP-0001": (item,)},
    )

    group = package.connection_tasks[0]
    assert group.decision == AUTOMATION_DECISION_CONFIRM
    assert group.blockers == ()                        # nothing is missing: NOT a REVIEW task
    assert group.validation_passed is True             # 7R genuinely passed
    assert len(group.tasks) == 1
    task = group.tasks[0]
    assert task.task_type == TASK_CONFIRM_AUTOMATION    # NOT one of the review task types
    assert task.blocker_codes == ()
    assert task.answer_type == ANSWER_AUTOMATION_CONFIRMATION
    assert item in task.question
    assert "never forces AUTO" in task.evidence_requirement
    # The hold remains: the contract did not flip the decision to AUTO by itself.
    assert package.confirm_count == 1 and package.auto_count == 0


def test_confirmation_resolution_changes_no_supplement_and_the_pipeline_redecides():
    collection = _collection(_reviewed_package())
    _, package = _build(
        collection, require_confirmation_by_package_id={"RP-0001": ("confirm the reused pattern",)},
    )
    task = package.connection_tasks[0].tasks[0]
    resolved = apply_human_resolution(package, _answer(task, None, evidence="register D15 checked"))

    original = collection.candidates[0].package
    assert build_resolved_supplement(original, [_find_resolution(resolved, task)]) \
        == original.supplement                        # a confirmation changes no engineering field

    # The existing pipeline still decides: re-run WITHOUT the confirmation hold -> genuine AUTO
    # (through the real 7V/7AA/7R/7Z chain) — never forced by the resolution itself.
    rerun = _rerun_pipeline(original, original.supplement)
    assert rerun.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
    assert rerun.validation_passed is True


# =============================================================================
# 3. REVIEW -> blocking tasks covering every 7Z blocker, in stable order
# =============================================================================
def test_REVIEW_connection_tasks_cover_every_blocker_in_stable_order():
    collection = _collection(_reviewed_package(holes=None))
    result, package = _build(collection)
    outcome = result.connection_results[0]
    group = package.connection_tasks[0]

    assert isinstance(group, ConnectionExceptionTasks)
    assert group.decision == AUTOMATION_DECISION_REVIEW
    assert group.blockers == outcome.blockers          # the complete 7Z findings, verbatim
    assert [t.task_type for t in group.tasks] == [
        TASK_PROVIDE_HOLE_DIAMETER, TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION,
    ]
    # Every blocker code appears in at least one task; nothing is hidden.
    assert {c for t in group.tasks for c in t.blocker_codes} == {b.code for b in group.blockers} == {
        AUTOMATION_BLOCKER_HOLE_DIAMETER, AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION,
    }
    # The task ids are deterministic and sequential per connection.
    assert [t.task_id for t in group.tasks] == ["RP-0001-T01", "RP-0001-T02", "RP-0001-T03"]

    # The nominal bolt size the AI extracted is shown exactly — and is never a diameter.
    holes_task = _tasks(group)[TASK_PROVIDE_HOLE_DIAMETER]
    assert holes_task.current_ai_value == ((None, "M20", "8.8", ()),)
    assert holes_task.answer_type == ANSWER_HOLES_VALUE
    assert holes_task.allowed_choices == ()            # no guessed diameter list

    # The 7V rejection and the preserved 7R failure are carried verbatim into their tasks.
    spec_blocker = next(b for b in group.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert spec_blocker.message in _tasks(group)[TASK_REVIEW_SPECIFICATION].question
    assert group.validation_passed is False
    assert group.validation_failure.stage == PIPELINE_STAGE_CONNECTION
    assert group.validation_failure.error_code == "GeometryValidationError"
    validation_question = _tasks(group)[TASK_REVIEW_VALIDATION].question
    assert PIPELINE_STAGE_CONNECTION in validation_question
    assert "GeometryValidationError" in validation_question


# =============================================================================
# 4. Review status is a workflow task, distinct from missing engineering data
# =============================================================================
def test_missing_review_status_is_a_workflow_task_not_an_engineering_shortcut():
    fixture = _reviewed_package(review_status="in_review")
    _, package = _build(_collection(fixture))
    group = package.connection_tasks[0]

    assert [t.task_type for t in group.tasks] == [TASK_COMPLETE_REVIEW, TASK_REVIEW_SPECIFICATION]
    complete = _tasks(group)[TASK_COMPLETE_REVIEW]
    assert complete.blocker_codes == (AUTOMATION_BLOCKER_REVIEW_STATUS,)
    assert complete.current_ai_value == "in_review"    # the current workflow state, verbatim
    assert complete.answer_type == ANSWER_APPROVE_REVIEW
    # Approval alone does not make the rejected specification go away — it stays its own task.
    spec = _tasks(group)[TASK_REVIEW_SPECIFICATION]
    assert spec.answer_type == ANSWER_ACKNOWLEDGMENT

    # Approving resolves exactly the workflow gap; everything else is unchanged.
    resolved = apply_human_resolution(package, _answer(complete, None, evidence="checked D15"))
    assert resolved.open_task_count == 1
    supplement = build_resolved_supplement(fixture, [_find_resolution(resolved, complete)])
    assert supplement.review_status == REVIEW_STATUS_APPROVED
    # The genuine chain now passes: the fixture's engineering fields were ALREADY complete and
    # human-owned, so approval legitimately completes it — and only 7V/7R/7Z decide that.
    rerun = _rerun_pipeline(fixture, supplement)
    assert rerun.automation_gate_result.decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# 5. Grouping: only when one answer supplies all three, and it lists every code
# =============================================================================
def test_grouped_member_position_attachment_task_lists_all_three_codes_and_high_confidence_still_reviews():
    package = _raw_timber_package(KNOWN_MARKS)
    result, built = _build(_collection(package, known_member_marks=None))
    outcome = result.connection_results[0]
    group = built.connection_tasks[0]

    # confidence = 99 must NOT suppress review: the engineering blockers still produce tasks.
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert any(w.code == AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE for w in group.warnings)
    assert len(group.tasks) > 0

    grouped = _tasks(group)[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    assert grouped.blocker_codes == (
        AUTOMATION_BLOCKER_MEMBER_IDENTITY, AUTOMATION_BLOCKER_POSITION, AUTOMATION_BLOCKER_ATTACHMENT,
    )
    assert grouped.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS
    # The AI's member references are shown exactly as extracted, never reinterpreted.
    assert grouped.current_ai_value == ["stringer 140x45 H4 SG8", "structure"]


def test_individual_tasks_appear_when_not_all_three_are_blocked():
    # (a) member identity alone: the marks could not be checked against any known members.
    reviewed = _reviewed_package()
    package = create_review_package(reviewed.extraction, reviewed.supplement, known_member_marks=None)
    _, built = _build(_collection(package, known_member_marks=None))
    group = built.connection_tasks[0]
    assert [t.task_type for t in group.tasks] == [TASK_SELECT_MEMBER]
    member_task = _tasks(group)[TASK_SELECT_MEMBER]
    assert member_task.blocker_codes == (AUTOMATION_BLOCKER_MEMBER_IDENTITY,)
    assert member_task.answer_type == ANSWER_MEMBER_SELECTION
    assert member_task.current_ai_value == ["REAL-UB-CAD-001", "L2"]

    # (b) position alone.
    _, built = _build(_collection(_reviewed_package(position=None)))
    group = built.connection_tasks[0]
    assert [t.task_type for t in group.tasks] == [
        TASK_SELECT_POSITION, TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION,
    ]
    position_task = _tasks(group)[TASK_SELECT_POSITION]
    assert position_task.blocker_codes == (AUTOMATION_BLOCKER_POSITION,)
    assert position_task.answer_type == ANSWER_POSITION_VALUE
    assert position_task.current_ai_value is None        # no position exists anywhere — none is guessed

    # (c) attachments alone.
    _, built = _build(_collection(_reviewed_package(attachments=None)))
    group = built.connection_tasks[0]
    assert [t.task_type for t in group.tasks] == [
        TASK_SELECT_ATTACHMENT, TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION,
    ]
    attachment_task = _tasks(group)[TASK_SELECT_ATTACHMENT]
    assert attachment_task.blocker_codes == (AUTOMATION_BLOCKER_ATTACHMENT,)
    assert attachment_task.answer_type == ANSWER_ATTACHMENTS_VALUE
    assert attachment_task.current_ai_value is None


# =============================================================================
# 7. Allowed choices come only from authoritative existing context
# =============================================================================
def test_position_and_surface_choices_are_the_existing_geometry_vocabulary():
    # 7AC references the START/END vocabulary without importing the CadQuery module.
    assert CONNECTION_POSITION_CHOICES == connections_module.SUPPORTED_CONNECTION_POSITIONS

    _, built = _build(_collection(_reviewed_package(position=None)))
    position_task = _tasks(built.connection_tasks[0])[TASK_SELECT_POSITION]
    assert position_task.allowed_choices == CONNECTION_POSITION_CHOICES

    _, built = _build(_collection(_reviewed_package(attachments=None)))
    attachment_task = _tasks(built.connection_tasks[0])[TASK_SELECT_ATTACHMENT]
    assert attachment_task.allowed_choices == CONNECTION_POSITION_CHOICES


def test_member_choices_come_only_from_authoritative_context_never_from_ai_strings():
    package = _raw_timber_package(None)
    collection = _collection(package, known_member_marks=None)
    result = evaluate_project_for_automation(collection)

    # No authoritative context at all -> no choices. The AI strings are never promoted to choices.
    built = build_exception_resolution_package(result, collection)
    grouped = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    assert grouped.allowed_choices == ()

    # The collection's known marks are authoritative context.
    collection = _collection(package, known_member_marks=KNOWN_MARKS)
    result = evaluate_project_for_automation(collection)
    built = build_exception_resolution_package(result, collection)
    grouped = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    assert grouped.allowed_choices == KNOWN_MARKS
    assert "stringer 140x45 H4 SG8" not in grouped.allowed_choices
    assert "structure" not in grouped.allowed_choices

    # Supplied member rows (the context the pipeline consumed) take precedence.
    rows = {"REAL-UB-CAD-001": make_member_a_row(), "L2": make_member_b_row()}
    built = build_exception_resolution_package(result, collection, member_rows=rows)
    grouped = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    assert grouped.allowed_choices == tuple(rows)


# =============================================================================
# 8. Provenance confirmation: the AI value is shown losslessly; confirming is 7W's semantics
# =============================================================================
def test_provenance_task_shows_the_exact_ai_value_and_confirmation_uses_7W_semantics():
    fixture = _reviewed_package(plate=None)
    _, built = _build(_collection(fixture))
    group = built.connection_tasks[0]

    task = _tasks(group)[TASK_CONFIRM_AI_VALUES]
    assert task.blocker_codes == (AUTOMATION_BLOCKER_PROVENANCE,)
    assert task.answer_type == ANSWER_CONFIRMED_FIELDS
    # Lossless: the exact AI plate record, nothing re-typed.
    expected_plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    assert task.current_ai_value == (("plate", expected_plate),)

    resolved = apply_human_resolution(built, _answer(task, ("plate",), evidence="D15 checked"))
    supplement = build_resolved_supplement(fixture, [_find_resolution(resolved, task)])
    assert supplement.confirmed_ai_fields == frozenset({"plate"})
    assert supplement.plate is None                     # confirming drops the supplied value — never both states

    rebuilt = create_review_package(fixture.extraction, supplement, known_member_marks=KNOWN_MARKS)
    entry = next(e for e in build_review_report(rebuilt).entries if e.field == "plate")
    assert entry.provenance == PROVENANCE_HUMAN_REVIEWED
    assert entry.ai_value == expected_plate             # the AI value it keeps is the exact same record


# =============================================================================
# 9. Malformed fields and conflicts: shown verbatim, resolved through 7W's semantics
# =============================================================================
def test_malformed_ai_fields_are_shown_verbatim_and_acknowledgment_grants_nothing():
    extraction = ai_connection_to_extraction({"connects_members": ["A", "B"], "bolts": "not-a-list"})
    fixture = create_review_package(extraction, known_member_marks=KNOWN_MARKS)
    _, built = _build(_collection(fixture, known_member_marks=None))
    group = built.connection_tasks[0]

    task = _tasks(group)[TASK_REVIEW_MALFORMED_FIELDS]
    assert task.blocker_codes == (AUTOMATION_BLOCKER_MALFORMED_FIELDS,)
    assert task.current_ai_value == (("bolts", "not-a-list"),)  # the raw AI value, never coerced
    assert task.answer_type == ANSWER_ACKNOWLEDGMENT
    assert "'bolts'" in task.question

    resolved = apply_human_resolution(built, _answer(task, None))
    supplement = build_resolved_supplement(fixture, [_find_resolution(resolved, task)])
    assert supplement == fixture.supplement            # an acknowledgment changes no field


def test_conflict_task_carries_field_ai_and_supplied_values_and_field_decision_resolves():
    fixture = _reviewed_package(confirmed_ai_fields=frozenset({"plate"}))
    _, built = _build(_collection(fixture))
    group = built.connection_tasks[0]

    task = _tasks(group)[TASK_RESOLVE_CONFLICT]
    assert task.blocker_codes == (AUTOMATION_BLOCKER_CONFLICT,)
    assert task.answer_type == ANSWER_FIELD_DECISION
    ai_plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    field, ai_value, supplied = task.current_ai_value
    assert field == "plate"
    assert ai_value == ai_plate                        # the AI's value, verbatim
    assert supplied == _plate()                        # the reviewer's supplied value, verbatim

    # The human decides "keep the AI value" through the EXISTING 7W semantics.
    resolved = apply_human_resolution(
        built, _answer(task, ("plate", FIELD_DECISION_CONFIRM_AI, None), evidence="D15 checked"),
    )
    supplement = build_resolved_supplement(fixture, [_find_resolution(resolved, task)])
    assert supplement.confirmed_ai_fields == frozenset({"plate"})
    assert supplement.plate is None                    # the supplied plate is dropped, never both states

    rebuilt = create_review_package(fixture.extraction, supplement, known_member_marks=KNOWN_MARKS)
    entry = next(e for e in build_review_report(rebuilt).entries if e.field == "plate")
    assert entry.provenance == PROVENANCE_HUMAN_REVIEWED
    rerun = _rerun_pipeline(fixture, supplement)
    assert rerun.automation_gate_result.decision == AUTOMATION_DECISION_AUTO

    # Supplying your own value drops the confirmation instead.
    supplied_resolution = _answer(task, ("plate", FIELD_DECISION_SUPPLY, _plate(thickness_mm=10)))
    supplied_supplement = build_resolved_supplement(fixture, [supplied_resolution])
    assert supplied_supplement.plate == _plate(thickness_mm=10)
    assert "plate" not in supplied_supplement.confirmed_ai_fields


# =============================================================================
# 11. Immutability, non-mutation, determinism
# =============================================================================
def test_tasks_results_and_human_resolutions_are_frozen():
    _, package = _build(_collection(_reviewed_package(holes=None)))
    task = package.connection_tasks[0].tasks[0]
    assert isinstance(package, ExceptionResolutionPackage)
    assert dataclasses.is_dataclass(package) and package.__dataclass_params__.frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.status = "RESOLVED"
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.open_task_count = 99


def test_human_resolution_is_immutable_and_snapshots_its_payload():
    holes = _holes()
    resolution = HumanResolution("RP-0001-T01", TASK_PROVIDE_HOLE_DIAMETER, ANSWER_HOLES_VALUE, holes)
    holes["quantity"] = 99                            # mutating the caller's dict afterwards...
    assert resolution.answer["quantity"] == 4         # ...cannot change the recorded answer
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolution.answer = None


def test_apply_human_resolution_records_exactly_one_resolution_and_mutates_nothing():
    _, package = _build(_collection(_reviewed_package(holes=None)))
    group = package.connection_tasks[0]
    task = _tasks(group)[TASK_PROVIDE_HOLE_DIAMETER]
    other = _tasks(group)[TASK_REVIEW_SPECIFICATION]

    resolved = apply_human_resolution(package, _answer(task, _holes(), evidence="D15 detail"))

    # The original package is untouched.
    assert _tasks(package.connection_tasks[0])[TASK_PROVIDE_HOLE_DIAMETER].status == STATUS_OPEN
    assert package.open_task_count == 3

    # The new package has exactly this one task RESOLVED with its resolution; the others are the SAME objects.
    new_group = resolved.connection_tasks[0]
    new_task = _tasks(new_group)[TASK_PROVIDE_HOLE_DIAMETER]
    assert new_task.status == STATUS_RESOLVED
    assert new_task.resolution.answer == _holes()
    assert new_task.resolution.evidence == "D15 detail"
    assert _tasks(new_group)[TASK_REVIEW_SPECIFICATION] is other
    assert resolved.open_task_count == 2
    assert "RP-0001-T01: RESOLVED (PROVIDE_HOLE_DIAMETER)." in resolved.summary


def test_apply_human_resolution_rejects_programming_errors():
    _, package = _build(_collection(_reviewed_package(holes=None)))
    group = package.connection_tasks[0]
    holes_task = _tasks(group)[TASK_PROVIDE_HOLE_DIAMETER]

    with pytest.raises(TypeError):
        apply_human_resolution(package, object())
    with pytest.raises(ValueError):
        apply_human_resolution(package, HumanResolution(
            "NOPE", holes_task.task_type, holes_task.answer_type, _holes(),
        ))
    applied = apply_human_resolution(package, _answer(holes_task, _holes()))
    with pytest.raises(ValueError):
        apply_human_resolution(applied, _answer(holes_task, _holes()))           # already RESOLVED
    with pytest.raises(ValueError):
        apply_human_resolution(package, HumanResolution(
            holes_task.task_id, holes_task.task_type, ANSWER_ACKNOWLEDGMENT, None,
        ))                                                                       # wrong answer_type
    with pytest.raises(ValueError):
        apply_human_resolution(package, _answer(holes_task, "a diameter"))       # not a mapping
    approve_task = HumanResolution("RP-0001-T09", TASK_COMPLETE_REVIEW, ANSWER_APPROVE_REVIEW, "yes")
    with pytest.raises(ValueError):
        apply_human_resolution(package, approve_task)                            # approve carries no payload


def test_inputs_are_never_mutated_and_repeated_builds_are_identical():
    collection = _collection(_reviewed_package(holes=None), _reviewed_package(connection_id="D15/A-B-2"))
    result = _evaluate(collection)
    before = (copy.deepcopy(collection), copy.deepcopy(result))

    first = build_exception_resolution_package(result, collection)
    second = build_exception_resolution_package(result, collection)

    assert (collection, result) == before               # nothing leaked back into the inputs
    assert first == second                              # plain frozen data — two builds are equal
    assert repr(first) == repr(second)                  # stable serialization/repr


def test_build_rejects_programming_errors_loudly():
    result = _evaluate(_collection(_reviewed_package(holes=None)))

    with pytest.raises(TypeError):
        build_exception_resolution_package(object(), _collection(_reviewed_package(holes=None)))
    with pytest.raises(TypeError):
        build_exception_resolution_package(result, object())
    # A result paired with a different collection (different candidate ids) is refused, never guessed.
    other = ProjectConnectionReviewCollection(candidates=(
        ConnectionCandidate("X-0001", 0, None, _reviewed_package(holes=None)),),
        project_id="OTHER",
    )
    with pytest.raises(ValueError):
        build_exception_resolution_package(result, other)
    with pytest.raises(ValueError):
        build_exception_resolution_package(
            result, _collection(_reviewed_package(holes=None), _reviewed_package()),  # count mismatch
        )


# =============================================================================
# 13. Import purity + no CAD/drawing entry points at runtime
# =============================================================================
def test_exception_resolution_module_imports_are_pure():
    source = Path(exception_resolution.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported == {
        "copy",
        "collections.abc",
        "dataclasses",
        "typing",
        "app.cad_engine.automation_gate",              # 7Z vocabulary/types only — never its evaluate functions
        "app.cad_engine.automation_pipeline",          # the preserved-failure type only
        "app.cad_engine.connection_review_package",    # the EXISTING 7W correction representation
        "app.cad_engine.project_automation",           # the 7AB result/scope types
        "app.cad_engine.project_connection_review",    # the 7X collection type
        "app.cad_engine.reviewed_connection_specification",  # provenance/review-status vocabulary
    }


def test_the_contract_never_calls_cad_or_drawing_entry_points(monkeypatch):
    collection = _collection(_reviewed_package(holes=None))
    result = _evaluate(collection)                     # the genuine 7AA chain runs its CAD HERE, before the patch

    import app.cad_engine.reviewed_connection_assembly as assembly_module
    import app.cad_engine.reviewed_connection_drawing_gate as drawing_gate_module
    import app.cad_engine.reviewed_connection_validation_gate as validation_module
    import app.cad_engine.two_member_connection as two_member_module
    import app.drawing_generator.interface as drawing_interface
    import app.drawing_generator.pdf_builder as pdf_builder

    def forbidden(*args, **kwargs):
        raise AssertionError("7AC must not generate CAD, validate geometry or produce drawings")

    for module, name in [
        (assembly_module, "build_reviewed_two_member_connection_assembly"),
        (two_member_module, "build_two_member_connection_assembly"),
        (validation_module, "validate_reviewed_connection_assembly"),
        (drawing_gate_module, "generate_fabrication_drawing_from_reviewed_assembly"),
        (drawing_interface, "generate_connection_fabrication_drawing_pdf"),
        (pdf_builder, "build_connection_pdf"),
    ]:
        monkeypatch.setattr(module, name, forbidden)

    package = build_exception_resolution_package(result, collection)
    task = _tasks(package.connection_tasks[0])[TASK_PROVIDE_HOLE_DIAMETER]
    resolved = apply_human_resolution(package, _answer(task, _holes()))
    supplement = build_resolved_supplement(
        collection.candidates[0].package, [_find_resolution(resolved, task)],
    )
    assert supplement.holes == _holes()


# =============================================================================
# 12. Resolutions -> the existing 7W supplement -> the gates re-decide
# =============================================================================
def test_resolutions_flow_into_the_existing_7W_supplement_and_the_gates_redecide():
    collection = _collection(_reviewed_package(holes=None))
    original_package = collection.candidates[0].package
    _, package = _build(collection)
    group = package.connection_tasks[0]
    tasks = _tasks(group)

    current = package
    resolutions = []
    for task, answer in [
        (tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes()),
        (tasks[TASK_REVIEW_SPECIFICATION], None),
        (tasks[TASK_REVIEW_VALIDATION], None),
    ]:
        current = apply_human_resolution(current, _answer(task, answer, evidence="D15 detail"))
        resolutions.append(_find_resolution(current, task))

    assert current.open_task_count == 0
    # All three resolutions are recorded; the acknowledgments carried no payload.
    by_id = {r.task_id: r for r in resolutions}
    assert by_id[tasks[TASK_PROVIDE_HOLE_DIAMETER].task_id].answer == _holes()

    # The ONLY correction representation: the existing 7W supplement, its fields, its semantics.
    supplement = build_resolved_supplement(original_package, resolutions)
    assert isinstance(supplement, ConnectionReviewSupplement)
    assert supplement.holes == _holes()
    assert supplement.review_status == REVIEW_STATUS_APPROVED   # untouched from the base supplement
    assert original_package.supplement.holes is None            # the input package was never mutated

    rebuilt = create_review_package(original_package.extraction, supplement, known_member_marks=KNOWN_MARKS)
    entry = next(e for e in build_review_report(rebuilt).entries if e.field == "holes")
    assert entry.provenance == PROVENANCE_HUMAN_SUPPLEMENTED     # 7W's own label, not a new system's

    # The genuine re-run decides AUTO — through 7V, 7AA, 7R and 7Z, with real 7R evidence.
    rerun = _rerun_pipeline(original_package, supplement)
    assert rerun.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
    assert rerun.validation_passed is True
    assert rerun.validation_result.layers_validated == VALIDATED_LAYERS
    assert rerun.reviewed_assembly is not None


def test_acknowledgment_resolutions_grant_nothing_and_the_pipeline_still_decides():
    collection = _collection(_reviewed_package(holes=None))
    original_package = collection.candidates[0].package
    _, package = _build(collection)
    group = package.connection_tasks[0]
    tasks = _tasks(group)

    resolutions = [
        _answer(tasks[TASK_REVIEW_SPECIFICATION], None),
        _answer(tasks[TASK_REVIEW_VALIDATION], None),
    ]
    supplement = build_resolved_supplement(original_package, resolutions)
    assert supplement == original_package.supplement        # nothing changed, nothing granted

    rerun = _rerun_pipeline(original_package, supplement)
    assert rerun.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW


def test_a_resolution_cannot_bypass_7V():
    collection = _collection(_reviewed_package(holes=None))
    original_package = collection.candidates[0].package
    _, package = _build(collection)
    group = package.connection_tasks[0]
    tasks = _tasks(group)

    # The human answers with a nominal size where a numeric diameter is required.
    resolutions = [
        _answer(tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes(diameter_mm="M20")),
        _answer(tasks[TASK_REVIEW_SPECIFICATION], None),
        _answer(tasks[TASK_REVIEW_VALIDATION], None),
    ]
    supplement = build_resolved_supplement(original_package, resolutions)
    assert supplement.holes["diameter_mm"] == "M20"        # the answer is stored exactly as given

    rerun = _rerun_pipeline(original_package, supplement)
    gate = rerun.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_REVIEW     # 7V rejected it — the resolution granted nothing
    spec_blocker = next(b for b in gate.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
    assert "M20" in spec_blocker.message                   # preserved verbatim, never converted
    assert "Ø" not in spec_blocker.message


def test_a_resolution_cannot_bypass_7R():
    collection = _collection(_reviewed_package(attachments=None))
    original_package = collection.candidates[0].package
    _, package = _build(collection)
    group = package.connection_tasks[0]
    tasks = _tasks(group)

    # 7V-valid vocabulary (START/END) but geometrically wrong: member A is attached at its
    # START face (Z=0) and L2 at its END face (Z=7000), while the connection sits at Z~3994 —
    # the genuine 7R geometry-consistency gate must reject the contradiction.
    swapped = (
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "END"},
    )
    resolutions = [
        _answer(tasks[TASK_SELECT_ATTACHMENT], swapped),
        _answer(tasks[TASK_REVIEW_SPECIFICATION], None),
        _answer(tasks[TASK_REVIEW_VALIDATION], None),
    ]
    supplement = build_resolved_supplement(original_package, resolutions)
    assert supplement.attachments == list(swapped)

    rerun = _rerun_pipeline(original_package, supplement)
    gate = rerun.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_REVIEW     # all tasks resolved, still REVIEW
    assert rerun.validation_passed is False
    assert rerun.validation_failure.stage == PIPELINE_STAGE_VALIDATION
    assert AUTOMATION_BLOCKER_VALIDATION in {b.code for b in gate.blockers}


def test_a_resolution_cannot_force_AUTO():
    collection = _collection(_reviewed_package(position=None, holes=None))
    original_package = collection.candidates[0].package
    _, package = _build(collection)
    group = package.connection_tasks[0]
    tasks = _tasks(group)

    assert TASK_SELECT_POSITION in tasks
    assert TASK_PROVIDE_HOLE_DIAMETER in tasks
    # Resolve ONLY the hole task; the position is still missing.
    resolutions = [_answer(tasks[TASK_PROVIDE_HOLE_DIAMETER], _holes())]
    supplement = build_resolved_supplement(original_package, resolutions)
    assert supplement.holes == _holes()
    assert supplement.position is None

    rerun = _rerun_pipeline(original_package, supplement)
    gate = rerun.automation_gate_result
    assert gate.decision == AUTOMATION_DECISION_REVIEW     # one answered task cannot force AUTO
    assert AUTOMATION_BLOCKER_POSITION in {b.code for b in gate.blockers}


def test_position_attachment_and_grouped_answers_reach_the_supplement_fields():
    # Position answer.
    fixture = _reviewed_package(position=None)
    _, built = _build(_collection(fixture))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_POSITION]
    supplement = build_resolved_supplement(fixture, [_answer(task, "END")])
    assert supplement.position == "END"

    # Attachment answer.
    fixture = _reviewed_package(attachments=None)
    _, built = _build(_collection(fixture))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_ATTACHMENT]
    attachments = (
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
    )
    supplement = build_resolved_supplement(fixture, [_answer(task, attachments)])
    assert supplement.attachments == list(attachments)

    # Grouped answer supplies all three fields at once.
    raw = _raw_timber_package(KNOWN_MARKS)
    _, built = _build(_collection(raw, known_member_marks=None))
    task = _tasks(built.connection_tasks[0])[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
    supplement = build_resolved_supplement(raw, [_answer(task, (
        ["REAL-UB-CAD-001", "L2"], "END",
        ({"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
         {"member_mark": "L2", "surface_reference": "START"}),
    ))])
    assert supplement.connected_member_marks == ["REAL-UB-CAD-001", "L2"]
    assert supplement.position == "END"
    assert supplement.attachments == [
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
    ]


# =============================================================================
# CONNECTION IDENTITY — reviewer-supplied, never AI output, never invented
# =============================================================================
def _without_identity(fixture):
    """The fully reviewed fixture with its connection identity removed — the supplement a
    never-persisted REVIEW candidate carries before its identity task is answered."""
    supplement = fixture.supplement
    return create_review_package(
        fixture.extraction,
        ConnectionReviewSupplement(
            review_status=supplement.review_status,
            connected_member_marks=supplement.connected_member_marks,
            position=supplement.position,
            plate=supplement.plate,
            holes=supplement.holes,
            location=supplement.location,
            attachments=supplement.attachments,
            confirmed_ai_fields=supplement.confirmed_ai_fields,
        ),
        known_member_marks=fixture.known_member_marks,
    )


def test_candidate_without_a_connection_identity_gets_exactly_one_identity_task():
    # No identity in the supplement -> the one non-blocker task, appended after the 7Z
    # blocker tasks so their order and ids are unchanged.
    raw = _raw_timber_package(KNOWN_MARKS)
    _, built = _build(_collection(raw, known_member_marks=None))
    group = built.connection_tasks[0]
    task = _tasks(group)[TASK_PROVIDE_CONNECTION_IDENTITY]
    assert task.answer_type == ANSWER_CONNECTION_IDENTITY
    assert task.blocker_codes == ()
    assert task.current_ai_value is None
    assert task.allowed_choices == ()
    assert group.tasks[-1].task_id == task.task_id

    # A supplement that already carries an identity gets no such task.
    _, built = _build(_collection(_reviewed_package()))
    assert TASK_PROVIDE_CONNECTION_IDENTITY not in _tasks(built.connection_tasks[0])


def test_reviewer_supplied_identity_reaches_the_supplement_without_provenance():
    fixture = _without_identity(_reviewed_package(holes=None))
    _, built = _build(_collection(fixture))
    task = _tasks(built.connection_tasks[0])[TASK_PROVIDE_CONNECTION_IDENTITY]

    supplement = build_resolved_supplement(fixture, [_answer(task, "CONN-D15-001")])
    assert supplement.connection_id == "CONN-D15-001"

    # 7W documents the identifier as needing no provenance: nothing is labelled, and the
    # review report's identity_missing flips from ("connection_id",) to ().
    rebuilt = create_review_package(
        fixture.extraction, supplement, known_member_marks=fixture.known_member_marks,
    )
    assert build_review_report(fixture).identity_missing == ("connection_id",)
    report = build_review_report(rebuilt)
    assert report.identity_missing == ()
    assert "connection_id" not in report.provenance

    # ... and an identifier grants nothing by itself: the genuine rerun still ends REVIEW
    # while the holes are missing.
    pipeline = _rerun_pipeline(fixture, supplement)
    assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_HOLE_DIAMETER in {
        b.code for b in pipeline.automation_gate_result.blockers
    }


@pytest.mark.parametrize("bad", ["", "   ", 42, None])
def test_connection_identity_payload_validation_rejects_non_identifiers(bad):
    fixture = _without_identity(_reviewed_package())
    _, built = _build(_collection(fixture))
    task = _tasks(built.connection_tasks[0])[TASK_PROVIDE_CONNECTION_IDENTITY]
    with pytest.raises(ValueError, match="non-empty identifier"):
        apply_human_resolution(built, _answer(task, bad))


# =============================================================================
# REAL ARKLES CAPTURE — deterministic task set, lossless values, nothing invented
# =============================================================================
ARKLES_TASK_TYPES = (
    TASK_COMPLETE_REVIEW,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_PROVIDE_PLATE,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
)
ARKLES_AI_MARKS = (
    ["stringer 140x45 H4 SG8", "structure"],
    ["stringer 190x45 H4 SG8", "structure"],
    ["deck joist", "bearer"],
)
ARKLES_BOLT_VALUES = (
    ((None, "M12", None, ()),),
    ((None, "M12", None, ()),),
    ((None, "SQ4 12mm", None, ()),),
)
ARKLES_BLOCKER_CODES = {
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
}


def _arkles_project():
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, drawing_set_page_count=41, known_member_marks=real_known_marks())
    result = evaluate_project_for_automation(intake.collection, intake=intake)
    return intake, result


@needs_real_capture
def test_real_arkles_deterministic_task_set_represents_all_three_candidates():
    intake, result = _arkles_project()
    package = build_exception_resolution_package(result, intake.collection)

    # All three captured candidates are represented, in submission order, each REVIEW.
    assert [g.review_package_id for g in package.connection_tasks] == ["RP-0001", "RP-0002", "RP-0003"]
    assert [g.submission_index for g in package.connection_tasks] == [0, 1, 2]
    assert all(g.decision == AUTOMATION_DECISION_REVIEW for g in package.connection_tasks)

    # Each candidate gets the SAME deterministic task set, in the same order.
    for group in package.connection_tasks:
        assert [t.task_type for t in group.tasks] == list(ARKLES_TASK_TYPES)
        assert [t.task_id for t in group.tasks] == [
            f"{group.review_package_id}-T{index:02d}" for index in range(1, 9)
        ]

    # 3 candidates x 8 tasks: the attention count is derived, never asserted.
    assert sum(len(g.tasks) for g in package.connection_tasks) == 24
    assert package.open_task_count == 24
    assert "SteelSpec found 24 task(s) that need your attention." in package.summary
    assert (package.connections_total, package.auto_count, package.confirm_count, package.review_count) == (3, 0, 0, 3)

    # Deterministic: rebuilding from the same inputs is identical.
    rebuilt = build_exception_resolution_package(result, intake.collection)
    assert rebuilt == package
    assert repr(rebuilt) == repr(package)


@needs_real_capture
def test_real_arkles_ai_values_are_preserved_exactly_and_nothing_is_invented():
    intake, result = _arkles_project()
    package = build_exception_resolution_package(result, intake.collection)

    for group, marks, bolt_values in zip(package.connection_tasks, ARKLES_AI_MARKS, ARKLES_BOLT_VALUES):
        tasks = _tasks(group)
        # The AI member references survive verbatim — never reinterpreted as steel marks.
        grouped = tasks[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
        assert grouped.current_ai_value == marks
        # The AI strings are never promoted to allowed choices; only real member marks are.
        assert grouped.allowed_choices == real_known_marks()
        assert not any(mark in grouped.allowed_choices for mark in marks)
        # Nominal bolt sizes survive verbatim — never converted into a hole diameter.
        holes_task = tasks[TASK_PROVIDE_HOLE_DIAMETER]
        assert holes_task.current_ai_value == bolt_values
        # Missing engineering values are shown as absent — none is guessed.
        assert tasks[TASK_PROVIDE_PLATE].current_ai_value is None
        assert tasks[TASK_PROVIDE_LOCATION].current_ai_value is None

    # No "Ø" anywhere — nothing was ever converted into a hole diameter.
    assert "Ø" not in repr(package)
    assert "M12" in repr(package) and "SQ4 12mm" in repr(package)
    # No fabricated geometry anywhere: no task carries a plate, location or diameter mapping,
    # and no position value is invented.
    for group in package.connection_tasks:
        for task in group.tasks:
            if isinstance(task.current_ai_value, dict):
                assert "diameter_mm" not in task.current_ai_value
                assert "thickness_mm" not in task.current_ai_value
                assert "x" not in task.current_ai_value
            assert task.current_ai_value not in (CONNECTION_POSITION_CHOICES[0], CONNECTION_POSITION_CHOICES[1])


@needs_real_capture
def test_real_arkles_approval_alone_never_resolves_missing_information():
    intake, result = _arkles_project()
    package = build_exception_resolution_package(result, intake.collection)

    # Resolve ONLY the review-workflow task of each candidate (approval) — everything else stays open.
    current = package
    for group in package.connection_tasks:
        complete = _tasks(group)[TASK_COMPLETE_REVIEW]
        current = apply_human_resolution(current, _answer(complete, None))
    assert current.open_task_count == 24 - 3  # 21 unresolved tasks remain (engineering + connection identity)

    # The approved supplement still carries NONE of the engineering fields — approval granted nothing.
    for candidate in intake.collection.candidates:
        resolution = next(
            _find_resolution(current, _tasks(group)[TASK_COMPLETE_REVIEW])
            for group in current.connection_tasks
            if group.review_package_id == candidate.review_package_id
        )
        supplement = build_resolved_supplement(candidate.package, [resolution])
        assert supplement.review_status == REVIEW_STATUS_APPROVED
        assert supplement.connected_member_marks is None
        assert supplement.position is None and supplement.plate is None
        assert supplement.holes is None and supplement.location is None
        assert supplement.attachments is None

        # The genuine re-run still routes to REVIEW — the gates, not the approval, decide.
        rerun = evaluate_reviewed_connection_for_automation(
            create_review_package(candidate.package.extraction, supplement,
                                  known_member_marks=candidate.package.known_member_marks),
        )
        assert rerun.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW


@needs_real_capture
def test_real_arkles_grouping_never_hides_any_blocker():
    intake, result = _arkles_project()
    package = build_exception_resolution_package(result, intake.collection)

    for group, outcome in zip(package.connection_tasks, result.connection_results):
        assert group.blockers == outcome.blockers
        # All nine blocker codes stay visible across the task set; nothing disappears into a group.
        assert len(group.blockers) == 9
        assert {b.code for b in group.blockers} == ARKLES_BLOCKER_CODES
        assert {c for t in group.tasks for c in t.blocker_codes} == ARKLES_BLOCKER_CODES
        grouped = _tasks(group)[TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
        assert set(grouped.blocker_codes) == {
            AUTOMATION_BLOCKER_MEMBER_IDENTITY, AUTOMATION_BLOCKER_POSITION, AUTOMATION_BLOCKER_ATTACHMENT,
        }


@needs_real_capture
def test_real_arkles_source_and_scope_facts_are_visible_and_nothing_is_claimed():
    intake, result = _arkles_project()
    package = build_exception_resolution_package(result, intake.collection)
    scope = package.intake_scope

    # The 7Y scope facts stay visible, each in its own field (parse-failed / unanalysed pages included).
    assert scope.pages_received == 30
    assert scope.parse_failed_pages == (2, 11, 12, 13, 22, 29)
    assert scope.drawing_set_page_count == 41
    assert scope.pages_not_analysed == 11
    assert scope.scope_statement == intake.scope_statement
    assert any(w.code == PROJECT_WARNING_NO_AUTO for w in package.warnings)
    assert package.warnings == result.warnings

    # Source facts are preserved; nothing is invented where nothing exists.
    for group in package.connection_tasks:
        assert group.source_page == 7
        assert group.detail_reference is None      # no drawing reference exists in the capture — none is invented
        assert group.connection_id is None
        assert group.source_identity is None

    # The package's own statement refuses every claim of approval/completeness/readiness.
    assert "is not engineering approval" in package.scope_statement
    assert "RESOLVED means only that the human supplied the requested information" in package.scope_statement
    assert package.scope_statement == EXCEPTION_RESOLUTION_SCOPE_STATEMENT
