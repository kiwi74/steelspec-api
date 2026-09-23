"""
Milestone 7AB — tests for project automation orchestration
(app.cad_engine.project_automation), the project-level seam that runs
the GENUINE existing per-connection automation pipeline (7AA — which
itself runs 7D/7J/7N -> 7A/7I -> 7O -> 7R -> 7Z) over an existing 7X
review collection and aggregates the individual outcomes:

    7X collection -> 7AA per candidate -> 7Z decision per candidate
                    -> factual counts + blocker aggregation

These tests prove, per the milestone's requirements:

  - Empty projects, single AUTO / CONFIRM / REVIEW candidates, and a
    mixed 1/1/1 project — with the project counts DERIVED from the
    actual individual connection results, never hard-coded.
  - The genuine chain executes: AUTO outcomes carry real 7R evidence
    (validation_passed=True, the validated layer names, an assembly
    was built) — the project layer cannot invent any of it.
  - One candidate's failure never prevents independent candidates
    from being evaluated, and the collection's submission order is
    preserved — without the project layer catching exceptions.
  - Missing member context remains a blocker (7AA's MEMBER_CONTEXT
    gap is preserved, no member is invented); programming errors
    (wrong input type, unknown confirmation ids, mismatched intake)
    propagate loudly.
  - Determinism (equal serializable results — no geometry objects in
    the project result), input immutability, stable blocker
    aggregation by 7Z's own code order with individual blockers never
    hidden, and import purity (no Supabase/FastAPI/Anthropic/config/
    AI-extraction imports).
  - The REAL Arkles capture through the genuine 7Y/7X pathway: 3
    candidates -> 0 AUTO / 0 CONFIRM / 3 REVIEW, with the zero-AUTO
    wording in the milestone's own discipline ("does not mean the
    project cannot be automated") and the 7Y page-scope facts
    (30 received / 6 parse failures / 11 not analysed / 41-page set)
    consumed from the intake, each in its own field — never
    manufactured and never interchangeable.

FIXTURE PROVENANCE: the reviewed packages are the established
synthetic 7Z fixture (SYNTHETIC AI-shaped extraction — actual schema
keys, not produced by any AI — plus a TEST / HUMAN-REVIEWED /
SUPPLEMENTED supplement), the member rows/placements/matcher are the
established 7O/7R fixtures, and the collections are plain 7X
containers over those packages. Nothing is claimed to be
AI-extracted or drawing-derived, and nothing re-implements any
validation rule. The Arkles tests use the real captured JSON through
the existing 7Y intake — no Claude calls, no network.
"""
import ast
import copy
import dataclasses
from pathlib import Path

import pytest

import app.cad_engine.project_automation as project_automation
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
    AUTOMATION_REASON_CONFIRMATION_REQUESTED,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_GAP_ERROR_CODE,
    PIPELINE_STAGE_CONNECTION,
    PIPELINE_STAGE_MEMBER_CONTEXT,
)
from app.cad_engine.project_automation import (
    EMPTY_PROJECT_STATEMENT,
    NO_AUTO_SCOPE_STATEMENT,
    NO_AUTO_STATEMENT,
    PROJECT_WARNING_NO_AUTO,
    PROJECT_WARNING_SCOPE_UNREPORTED,
    ProjectIntakeScope,
    evaluate_project_for_automation,
)
from app.cad_engine.project_connection_review import (
    ConnectionCandidate,
    ProjectConnectionReviewCollection,
)
from app.cad_engine.project_extraction_intake import (
    ProjectExtractionIntake,
    intake_page_extractions,
)
from app.cad_engine.reviewed_connection_validation_gate import VALIDATED_LAYERS
from tests.test_automation_gate import KNOWN_MARKS, _reviewed_package
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_project_extraction_intake import (
    CAPTURE_PATH,
    flatten_reference,
    load_capture,
    needs_real_capture,
    real_known_marks,
)
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher

PROJECT_ID = "PROJ-7AB"


def _collection(*packages, project_id=PROJECT_ID, known_member_marks=KNOWN_MARKS):
    """A plain 7X collection over the given reviewed packages, in the order given (submission order)."""
    return ProjectConnectionReviewCollection(
        candidates=tuple(
            ConnectionCandidate(
                review_package_id=f"RP-{index + 1:04d}",
                submission_index=index,
                source_identity=None,
                package=package,
            )
            for index, package in enumerate(packages)
        ),
        known_member_marks=known_member_marks,
        project_id=project_id,
    )


def _member_rows():
    return {"REAL-UB-CAD-001": make_member_a_row(), "L2": make_member_b_row()}


def _member_placements():
    return {"REAL-UB-CAD-001": make_member_a_placement(), "L2": make_member_b_placement()}


def _evaluate(collection, **overrides):
    """The shared project member context (7O/7R fixtures) into the project evaluator."""
    kwargs = dict(
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )
    kwargs.update(overrides)
    return evaluate_project_for_automation(collection, **kwargs)


# =============================================================================
# Empty project: zero candidates says nothing about the drawing set
# =============================================================================
def test_empty_project_reports_zero_candidates_without_claiming_no_connections():
    result = evaluate_project_for_automation(ProjectConnectionReviewCollection(project_id="PROJ-EMPTY"))

    assert result.project_id == "PROJ-EMPTY"
    assert result.connections_total == 0
    assert (result.auto_count, result.confirm_count, result.review_count) == (0, 0, 0)
    assert result.connection_results == ()
    assert result.blockers_by_code == ()
    assert result.summary[:4] == ("connections_total = 0", "AUTO = 0", "CONFIRM = 0", "REVIEW = 0")
    # Zero candidates means "nothing was supplied", never "the drawing set contains no connections".
    assert EMPTY_PROJECT_STATEMENT in result.summary
    # No candidates were evaluated at all, so the zero-AUTO verdict does not apply.
    assert not any(w.code == PROJECT_WARNING_NO_AUTO for w in result.warnings)
    assert any(w.code == PROJECT_WARNING_SCOPE_UNREPORTED for w in result.warnings)
    assert result.intake_scope == ProjectIntakeScope(None, None, None, None, None)


# =============================================================================
# Single candidates: each decision, with genuine 7R evidence where it applies
# =============================================================================
def test_single_AUTO_candidate_carries_the_genuine_validation_evidence():
    result = _evaluate(_collection(_reviewed_package()))

    assert (result.connections_total, result.auto_count, result.confirm_count, result.review_count) == (1, 1, 0, 0)
    outcome = result.connection_results[0]
    assert (outcome.review_package_id, outcome.submission_index, outcome.source_identity) == ("RP-0001", 0, None)
    assert outcome.decision == AUTOMATION_DECISION_AUTO
    assert outcome.blockers == ()
    # 7R genuinely ran: the project layer could not have invented any of this.
    assert outcome.validation_passed is True
    assert outcome.validation_failure is None
    assert outcome.validation_layers == VALIDATED_LAYERS
    assert outcome.assembly_built is True
    assert result.blockers_by_code == ()
    assert [w.code for w in result.warnings] == [PROJECT_WARNING_SCOPE_UNREPORTED]
    assert "AUTO = 1" in result.summary


def test_single_CONFIRM_candidate_holds_for_confirmation_even_with_passing_validation():
    result = _evaluate(
        _collection(_reviewed_package()),
        require_confirmation_by_package_id={"RP-0001": ("Reused approved pattern P-7",)},
    )

    assert (result.connections_total, result.auto_count, result.confirm_count, result.review_count) == (1, 0, 1, 0)
    outcome = result.connection_results[0]
    assert outcome.decision == AUTOMATION_DECISION_CONFIRM
    assert outcome.blockers == ()
    # 7R genuinely passed; the hold is the requested human confirmation, never a guessed gate.
    assert outcome.validation_passed is True
    assert any(r.code == AUTOMATION_REASON_CONFIRMATION_REQUESTED for r in outcome.reasons)
    assert result.blockers_by_code == ()
    assert "CONFIRM = 1" in result.summary


def test_single_REVIEW_candidate_preserves_its_failure_and_blockers():
    package = _reviewed_package(holes=None)
    result = _evaluate(_collection(package))

    assert (result.connections_total, result.auto_count, result.confirm_count, result.review_count) == (1, 0, 0, 1)
    outcome = result.connection_results[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    codes = {b.code for b in outcome.blockers}
    assert {AUTOMATION_BLOCKER_HOLE_DIAMETER, AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION} <= codes
    # The genuine chain failed at the first adapter (7D — no hole information), and that failure is preserved.
    assert outcome.validation_passed is False
    assert outcome.validation_failure is not None
    assert outcome.validation_failure.stage == PIPELINE_STAGE_CONNECTION
    assert outcome.validation_failure.error_code == "GeometryValidationError"
    assert outcome.validation_layers is None and outcome.assembly_built is False
    assert dict(result.blockers_by_code) == {
        AUTOMATION_BLOCKER_HOLE_DIAMETER: 1,
        AUTOMATION_BLOCKER_SPECIFICATION: 1,
        AUTOMATION_BLOCKER_VALIDATION: 1,
    }
    assert "RP-0001: REVIEW — blockers:" in next(
        line for line in result.summary if line.startswith("RP-0001")
    )


# =============================================================================
# Mixed project: 1 AUTO / 1 CONFIRM / 1 REVIEW, counts computed from the results
# =============================================================================
def test_mixed_project_counts_are_computed_from_the_individual_connection_results():
    packages = [
        _reviewed_package(connection_id="D15/A-B-1"),  # AUTO
        _reviewed_package(connection_id="D15/A-B-2"),  # CONFIRM (confirmation requested)
        _reviewed_package(holes=None),                 # REVIEW (7D fails: no hole information)
    ]
    result = _evaluate(
        _collection(*packages),
        require_confirmation_by_package_id={"RP-0002": ("confirm reuse of the approved pattern",)},
    )

    assert result.connections_total == 3
    decisions = [o.decision for o in result.connection_results]
    assert decisions == [AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_REVIEW]

    # CRITICAL ACCEPTANCE: the project counts are the SUM of the actual individual results,
    # recomputed here from the outcomes — not hard-coded anywhere.
    assert result.auto_count == sum(1 for d in decisions if d == AUTOMATION_DECISION_AUTO)
    assert result.confirm_count == sum(1 for d in decisions if d == AUTOMATION_DECISION_CONFIRM)
    assert result.review_count == sum(1 for d in decisions if d == AUTOMATION_DECISION_REVIEW)
    assert (result.auto_count, result.confirm_count, result.review_count) == (1, 1, 1)

    # Submission order is preserved and the individual failures are never hidden.
    assert [o.review_package_id for o in result.connection_results] == ["RP-0001", "RP-0002", "RP-0003"]
    assert result.connection_results[0].validation_passed is True
    assert result.connection_results[1].validation_passed is True
    assert result.connection_results[2].validation_failure is not None
    assert result.summary[:4] == ("connections_total = 3", "AUTO = 1", "CONFIRM = 1", "REVIEW = 1")
    assert any(line.startswith("RP-0003: REVIEW — blockers:") for line in result.summary)


# =============================================================================
# Failure isolation + ordering: a failing candidate stops nothing else
# =============================================================================
def test_one_failing_candidate_does_not_prevent_independent_candidates():
    packages = [
        _reviewed_package(connection_id="D15/A-B-1"),  # AUTO
        _reviewed_package(holes=None),                 # REVIEW, in the middle
        _reviewed_package(connection_id="D15/A-B-2"),  # AUTO
    ]
    result = _evaluate(_collection(*packages))

    assert (result.auto_count, result.confirm_count, result.review_count) == (2, 0, 1)
    assert [o.decision for o in result.connection_results] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_REVIEW, AUTOMATION_DECISION_AUTO,
    ]
    # The collection's own submission order survives the evaluation.
    assert [o.review_package_id for o in result.connection_results] == ["RP-0001", "RP-0002", "RP-0003"]
    first, failing, last = result.connection_results
    assert first.validation_passed and last.validation_passed and not failing.validation_passed
    assert failing.validation_failure is not None  # the failure stays visible on its own outcome


# =============================================================================
# Determinism and immutability
# =============================================================================
def test_repeated_evaluation_is_equivalent_and_fully_serializable():
    collection = _collection(
        _reviewed_package(connection_id="D15/A-B-1"),
        _reviewed_package(holes=None),
        _reviewed_package(connection_id="D15/A-B-2"),
    )
    first = _evaluate(collection)
    second = _evaluate(collection)

    assert first == second          # everything in the result is plain frozen data
    assert first.summary == second.summary
    assert first.blockers_by_code == second.blockers_by_code


def test_inputs_are_never_mutated_and_the_result_is_frozen():
    collection = _collection(_reviewed_package(), _reviewed_package(holes=None))
    rows, placements = _member_rows(), _member_placements()
    before = (copy.deepcopy(collection), copy.deepcopy(rows), copy.deepcopy(placements))

    result = _evaluate(collection, member_rows=rows, member_placements=placements)

    assert (collection, rows, placements) == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.auto_count = 99  # goes through the frozen dataclass __setattr__


# =============================================================================
# Blocker aggregation: stable codes, 7Z's own order, individuals never hidden
# =============================================================================
def test_blocker_aggregation_uses_stable_codes_and_hides_no_individual():
    packages = [
        _reviewed_package(connection_id="D15/A-B-1"),   # AUTO
        _reviewed_package(holes=None),                  # REVIEW: hole information missing
        _reviewed_package(review_status="in_review"),   # REVIEW: reviewer not approved
    ]
    result = _evaluate(_collection(*packages))

    # 7Z's own AUTOMATION_BLOCKER_CODES order; counts are sums across the individual REVIEW outcomes.
    # (The in_review candidate also fails 7V's completeness, which requires approved review status.)
    assert result.blockers_by_code == (
        (AUTOMATION_BLOCKER_REVIEW_STATUS, 1),
        (AUTOMATION_BLOCKER_HOLE_DIAMETER, 1),
        (AUTOMATION_BLOCKER_SPECIFICATION, 2),
        (AUTOMATION_BLOCKER_VALIDATION, 1),
    )
    # The summary must not hide individual blockers: each REVIEW outcome keeps its own, in its own words.
    assert {b.code for b in result.connection_results[1].blockers} == {
        AUTOMATION_BLOCKER_HOLE_DIAMETER, AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION,
    }
    assert {b.code for b in result.connection_results[2].blockers} == {
        AUTOMATION_BLOCKER_REVIEW_STATUS, AUTOMATION_BLOCKER_SPECIFICATION,
    }
    summary = "\n".join(result.summary)
    assert "RP-0002: REVIEW — blockers:" in summary
    assert "RP-0003: REVIEW — blockers:" in summary


# =============================================================================
# Missing member context stays a blocker; no member is ever invented
# =============================================================================
def test_missing_member_context_remains_a_blocker_and_no_member_is_invented():
    collection = _collection(_reviewed_package())
    # Section matcher supplied, member rows deliberately absent — 7AA's own MEMBER_CONTEXT gap.
    result = evaluate_project_for_automation(collection, section_matcher=make_multi_member_matcher())

    assert (result.auto_count, result.confirm_count, result.review_count) == (0, 0, 1)
    outcome = result.connection_results[0]
    assert outcome.decision == AUTOMATION_DECISION_REVIEW
    assert outcome.validation_passed is False
    assert outcome.validation_failure is not None
    assert outcome.validation_failure.stage == PIPELINE_STAGE_MEMBER_CONTEXT
    assert outcome.validation_failure.error_code == PIPELINE_GAP_ERROR_CODE
    assert "No member row was supplied for mark 'REAL-UB-CAD-001'" in outcome.validation_failure.message
    assert AUTOMATION_BLOCKER_VALIDATION in {b.code for b in outcome.blockers}
    assert outcome.assembly_built is False


# =============================================================================
# Programming errors propagate loudly — nothing is swallowed at project level
# =============================================================================
def test_programming_errors_propagate_loudly():
    with pytest.raises(TypeError):
        evaluate_project_for_automation(object())  # not a 7X collection

    collection = _collection(_reviewed_package())
    with pytest.raises(ValueError):
        evaluate_project_for_automation(
            collection, require_confirmation_by_package_id={"RP-9999": ("some item",)},
        )

    other_collection = _collection(_reviewed_package(connection_id="D15/A-B-9"))
    other_intake = ProjectExtractionIntake(other_collection, 1, (), (), None, None, "other scope")
    with pytest.raises(ValueError):
        evaluate_project_for_automation(collection, intake=other_intake)  # scope of a different collection


# =============================================================================
# Import purity — no Supabase / FastAPI / Anthropic / config / AI-extraction imports
# =============================================================================
def test_project_automation_module_imports_are_pure():
    source = Path(project_automation.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported == {
        "app.cad_engine.automation_gate",       # 7Z's stable codes/types only — never its evaluate functions
        "app.cad_engine.automation_pipeline",   # the one seam 7AB calls
        "app.cad_engine.placement",
        "app.cad_engine.project_connection_review",
        "app.cad_engine.project_extraction_intake",
        "collections.abc",
        "dataclasses",
        "typing",
    }


# =============================================================================
# REAL ARKLES CAPTURE — through the genuine 7Y/7X pathway, no Claude, no network
# =============================================================================
@needs_real_capture
def test_real_arkles_project_automation_routes_all_three_to_REVIEW():
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, drawing_set_page_count=41, known_member_marks=real_known_marks())
    assert len(intake.collection.candidates) == len(flatten_reference(pages)) == 3

    result = evaluate_project_for_automation(intake.collection, intake=intake)

    assert result.connections_total == 3
    assert (result.auto_count, result.confirm_count, result.review_count) == (0, 0, 3)
    # Every engineering field is missing from the raw candidates, so every missing-field blocker
    # fires, plus review status, member identity, the rejected 7V specification and failed 7R
    # validation — all three candidates alike, in 7Z's own code order.
    assert result.blockers_by_code == (
        (AUTOMATION_BLOCKER_REVIEW_STATUS, 3),
        (AUTOMATION_BLOCKER_MEMBER_IDENTITY, 3),
        (AUTOMATION_BLOCKER_POSITION, 3),
        (AUTOMATION_BLOCKER_PLATE, 3),
        (AUTOMATION_BLOCKER_HOLE_DIAMETER, 3),
        (AUTOMATION_BLOCKER_LOCATION, 3),
        (AUTOMATION_BLOCKER_ATTACHMENT, 3),
        (AUTOMATION_BLOCKER_SPECIFICATION, 3),
        (AUTOMATION_BLOCKER_VALIDATION, 3),
    )

    # The zero-AUTO result is stated in the milestone's own discipline: it is about these
    # candidates, never a verdict about the project or the drawing set.
    assert NO_AUTO_STATEMENT.format(total=3) in result.summary
    assert NO_AUTO_SCOPE_STATEMENT in result.summary
    no_auto = next(w for w in result.warnings if w.code == PROJECT_WARNING_NO_AUTO)
    assert "None of the 3 supplied candidate(s) met the automation gate" in no_auto.message
    assert "does NOT mean SteelSpec cannot automate this project" in no_auto.message
    assert not any(w.code == PROJECT_WARNING_SCOPE_UNREPORTED for w in result.warnings)

    # Every individual outcome stays REVIEW with its own blockers and preserved failure.
    for outcome in result.connection_results:
        assert outcome.decision == AUTOMATION_DECISION_REVIEW
        assert outcome.blockers  # never AUTO: every candidate has visible blockers
        assert outcome.validation_passed is False
        assert outcome.validation_failure is not None
        assert outcome.assembly_built is False


@needs_real_capture
def test_real_arkles_7Y_scope_facts_are_consumed_not_manufactured():
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, drawing_set_page_count=41, known_member_marks=real_known_marks())
    result = evaluate_project_for_automation(intake.collection, intake=intake)

    scope = result.intake_scope
    assert scope.pages_received == 30
    assert scope.parse_failed_pages == (2, 11, 12, 13, 22, 29)
    assert scope.drawing_set_page_count == 41
    assert scope.pages_not_analysed == 11
    assert scope.scope_statement == intake.scope_statement  # the 7Y intake's own words, verbatim

    # The four scope facts are NOT interchangeable: each keeps its own field and its own value.
    assert result.connections_total == 3                     # candidates found is a fifth, separate fact
    assert scope.pages_received != result.connections_total  # 30 pages received ≠ 3 candidates
    assert len(scope.parse_failed_pages) != scope.pages_not_analysed  # 6 parse failures ≠ 11 not analysed
    assert scope.pages_received + scope.pages_not_analysed == scope.drawing_set_page_count

    summary = "\n".join(result.summary)
    assert "pages_received = 30; parse_failures = 6; pages_not_analysed = 11; drawing_set_page_count = 41" in summary
    assert intake.scope_statement in result.summary
