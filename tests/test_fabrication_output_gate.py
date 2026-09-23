"""
Milestone 7AE — FABRICATION OUTPUT GATE (tests/test_fabrication_output_gate.py)

Proves the final deterministic permission boundary between "SteelSpec
has successfully automated the engineering/CAD workflow" and "SteelSpec
is permitted to generate/export fabrication drawings", through
`app.cad_engine.fabrication_output_gate`, against the established
synthetic fixtures (7Z/7AB/7AC/7AD), the genuine 7AA pipeline results,
and the real Arkles capture.

The gate consumes ONE genuine AutomationPipelineResult per connection
(the existing 7V + 7O + 7R + 7Z evidence, exactly as 7AA produced it —
nothing re-run, no validation_passed / approved injection possible) and
derives per-connection AUTO / CONFIRM / REVIEW plus a derived project
package decision. It generates NO drawings, imports NO drawing module,
and the drawing generator is provably unreachable while the gate blocks.

Coverage, keyed to the milestone's 30 requirements:

  1-4   the genuine AUTO chain (7V PASS -> 7AA -> 7R PASS -> 7Z AUTO)
        ends in 7AE AUTO with the complete evidence restated; genuine
        REVIEW preserves 7Z's blocker identities verbatim; 7Z CONFIRM
        stays CONFIRM (never silently AUTO); a 7AE-level output hold is
        CONFIRM until the same genuine evidence is re-evaluated after
        the confirmation policy is satisfied
  5-8   incoherent or unresolved 7Z decisions block
        (OUTPUT_BLOCKER_AUTOMATION_DECISION); missing/failed 7R
        evidence blocks (OUTPUT_BLOCKER_VALIDATION); missing 7V
        specification/identity blocks (OUTPUT_BLOCKER_SPECIFICATION);
        missing 7O assembly blocks (OUTPUT_BLOCKER_ASSEMBLY)
  9-11  no injection: neither function accepts validation_passed /
        validation_evidence / approved; human approval alone never
        permits output (genuine 7AD rerun); AI confidence alone never
        permits output (the 99-confidence timber candidate); 7AC task
        resolution alone never permits output
  12-15 the real Arkles capture: 3 candidates -> 0 automatic outputs,
        package REVIEW, only the genuine 7Z blocker codes (never
        OUTPUT_* renames); the 7Y scope facts (30 / 6 / 11 / 41) are
        carried verbatim and the gate never claims the drawing set was
        fully processed; mixed AUTO/AUTO/REVIEW keeps both AUTO
        outcomes eligible but blocks the complete package; mixed
        AUTO/AUTO/CONFIRM holds the package until the confirmation
        policy is satisfied and then releases with identical evidence
  16-19 per-connection eligibility visible in mixed projects; counts
        derived from the outcomes; blocker aggregation deterministic
        with OUTPUT_* codes first then 7Z's own order; empty projects
        and unreported scope are stated, never claimed
  20-30 the boundary: the drawing generator is never called while the
        gate blocks (spy), and only the approved integration pattern
        reaches it on AUTO with the genuine assembly; the gate itself
        calls no validation/assembly/7Z/drawing entry point (spies
        raise); inputs never mutated; outputs frozen; repeated
        evaluations identical; import purity (no Supabase/FastAPI/
        Anthropic/config/AI-extraction/drawing imports); runtime import
        without any secret environment; programming errors propagate
        loudly.
"""
import ast
import copy
import dataclasses
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.cad_engine.automation_gate as automation_gate_module
import app.cad_engine.automation_pipeline as automation_pipeline_module
import app.cad_engine.reviewed_connection_assembly as reviewed_connection_assembly
import app.cad_engine.reviewed_connection_drawing_gate as reviewed_connection_drawing_gate
import app.cad_engine.reviewed_connection_validation_gate as reviewed_connection_validation_gate
import app.drawing_generator.interface as drawing_interface
import app.drawing_generator.pdf_builder as pdf_builder
from app.cad_engine import fabrication_output_gate
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_CODES,
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
    AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_STAGE_VALIDATION,
    AutomationValidationFailure,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.exception_resolution import (
    TASK_COMPLETE_REVIEW,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
)
from app.cad_engine.fabrication_output_gate import (
    EMPTY_FABRICATION_OUTPUT_STATEMENT,
    FABRICATION_PACKAGE_NOT_RELEASED_STATEMENT,
    FABRICATION_PACKAGE_RELEASED_STATEMENT,
    FABRICATION_OUTPUT_SCOPE_STATEMENT,
    OUTPUT_BLOCKER_ASSEMBLY,
    OUTPUT_BLOCKER_AUTOMATION_DECISION,
    OUTPUT_BLOCKER_SPECIFICATION,
    OUTPUT_BLOCKER_VALIDATION,
    OUTPUT_REASON_ALL_REQUIREMENTS_SATISFIED,
    OUTPUT_REASON_CONFIRMABLE,
    OUTPUT_REASON_CONFIRMATION_REQUESTED,
    OUTPUT_WARNING_CONFIRMATION_DEFERRED,
    PROJECT_FABRICATION_OUTPUT_SCOPE_STATEMENT,
    evaluate_fabrication_output_gate,
    evaluate_project_fabrication_output_gate,
)
from app.cad_engine.project_automation import PROJECT_WARNING_SCOPE_UNREPORTED
from app.cad_engine.reviewed_connection_validation_gate import VALIDATED_LAYERS

from tests.test_automation_gate import KNOWN_MARKS, _holes, _reviewed_package
from tests.test_exception_resolution import _arkles_project, _raw_timber_package, _tasks
from tests.test_project_automation import _collection, _evaluate, _member_rows, _member_placements
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_real_multi_member_cad import make_multi_member_matcher
from tests.test_resolution_rerun import _rerun, _resolve, _seven_ad_package

PROJECT_ID = "PROJ-7AE"


def _pipeline(package, *, member_context=True, require_confirmation=()):
    """The genuine 7AA pipeline result for one package (7V -> 7O -> 7R -> 7Z), never 7AE's own doing."""
    kwargs = dict(require_confirmation=require_confirmation)
    if member_context:
        kwargs.update(
            member_rows=_member_rows(),
            member_placements=_member_placements(),
            section_matcher=make_multi_member_matcher(),
        )
    return evaluate_reviewed_connection_for_automation(package, **kwargs)


def _auto_pipeline():
    pipeline = _pipeline(_reviewed_package())
    assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_AUTO  # fixture precondition
    return pipeline


def _project_gate(*packages, holds=None, pipeline_by_id=None):
    """7AB over the packages + one genuine pipeline result per candidate -> the 7AE project gate."""
    collection = _collection(*packages, project_id=PROJECT_ID)
    project_result = _evaluate(collection)
    pipelines = {candidate.review_package_id: _pipeline(candidate.package) for candidate in collection.candidates}
    if pipeline_by_id:
        pipelines.update(pipeline_by_id)
    gate = evaluate_project_fabrication_output_gate(
        project_result, pipelines, require_confirmation_by_package_id=holds,
    )
    return collection, project_result, pipelines, gate


def _decision_set(result):
    return {outcome.gate_result.decision for outcome in result.connection_results}


# =============================================================================
# 1-4. The genuine evidence chain: AUTO is permission, CONFIRM stays CONFIRM
# =============================================================================
def test_genuine_AUTO_evidence_permits_fabrication_output():
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)

    assert gate.decision == AUTOMATION_DECISION_AUTO
    assert gate.blockers == ()
    assert any(r.code == OUTPUT_REASON_ALL_REQUIREMENTS_SATISFIED for r in gate.reasons)
    assert gate.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert gate.output_scope == FABRICATION_OUTPUT_SCOPE_STATEMENT

    # The evidence restated is exactly what 7AA produced — genuine values, never invented.
    summary = "\n".join(gate.evidence_summary)
    assert "7Z decision: AUTO" in summary
    assert "7V specification: built" in summary
    assert "7V completeness: accepted by 7Z" in summary
    assert "7R validation: passed" in summary
    assert f"validation layers: {', '.join(VALIDATED_LAYERS)}" in summary
    assert "assembly built: true" in summary
    assert "connection identity: 'CONN-REAL-UB-CAD-001-L2'" in summary
    assert "member marks: ['REAL-UB-CAD-001', 'L2']" in summary


def test_genuine_REVIEW_connection_blocks_output_and_preserves_7Z_blocker_identity():
    pipeline = _pipeline(_reviewed_package(holes=None))
    assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW  # fixture precondition
    gate = evaluate_fabrication_output_gate(pipeline)

    assert gate.decision == AUTOMATION_DECISION_REVIEW
    # The 7Z blockers pass through VERBATIM — same codes, same messages, never renamed to OUTPUT_*.
    assert gate.blockers == pipeline.automation_gate_result.blockers
    assert all(blocker.code in AUTOMATION_BLOCKER_CODES for blocker in gate.blockers)
    assert any(r.code == "OUTPUT_REASON_BLOCKED" for r in gate.reasons)


def test_genuine_CONFIRM_connection_stays_CONFIRM_and_never_becomes_AUTO():
    pipeline = _pipeline(_reviewed_package(), require_confirmation=("ENGINEER",))
    assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_CONFIRM  # fixture precondition
    gate = evaluate_fabrication_output_gate(pipeline)

    assert gate.decision == AUTOMATION_DECISION_CONFIRM
    # 7Z's own confirmation reasons pass through verbatim; 7AE adds no silent upgrade.
    assert gate.reasons[: len(pipeline.automation_gate_result.reasons)] == pipeline.automation_gate_result.reasons


def test_output_confirmation_hold_is_CONFIRM_then_AUTO_over_identical_evidence():
    pipeline = _auto_pipeline()

    held = evaluate_fabrication_output_gate(pipeline, require_confirmation=("FAB RELEASE SIGN-OFF",))
    assert held.decision == AUTOMATION_DECISION_CONFIRM
    assert held.blockers == ()
    assert any(r.code == OUTPUT_REASON_CONFIRMATION_REQUESTED for r in held.reasons)
    assert any(r.code == OUTPUT_REASON_CONFIRMABLE for r in held.reasons)

    # After the confirmation policy is satisfied: re-evaluate the SAME genuine evidence.
    released = evaluate_fabrication_output_gate(pipeline)
    assert released.decision == AUTOMATION_DECISION_AUTO
    # The confirmation itself manufactured nothing: the evidence is byte-identical.
    assert held.evidence_summary == released.evidence_summary


# =============================================================================
# 5-8. The 7AE-specific evidence checks: decision / validation / specification / assembly
# =============================================================================
def test_unresolved_or_incoherent_7Z_decisions_block():
    pipeline = _auto_pipeline()

    unknown_decision = dataclasses.replace(
        pipeline.automation_gate_result, decision="MAYBE",
    )
    gate = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, automation_gate_result=unknown_decision))
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert {b.code for b in gate.blockers} == {OUTPUT_BLOCKER_AUTOMATION_DECISION}

    # A REVIEW that records no blocker is unexplained review — never permission.
    unexplained_review = dataclasses.replace(
        pipeline.automation_gate_result, decision=AUTOMATION_DECISION_REVIEW, blockers=(),
    )
    gate = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, automation_gate_result=unexplained_review))
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert {b.code for b in gate.blockers} == {OUTPUT_BLOCKER_AUTOMATION_DECISION}

    # An AUTO decision that still carries blockers is contradictory — refused with both.
    incoherent = dataclasses.replace(
        pipeline.automation_gate_result,
        blockers=(pipeline.automation_gate_result.reasons[0],),  # a genuine finding, wrong place
    )
    gate = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, automation_gate_result=incoherent))
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    # The 7AE finding leads; the misplaced 7Z reason passes through with its ORIGINAL code.
    assert [b.code for b in gate.blockers] == [
        OUTPUT_BLOCKER_AUTOMATION_DECISION, AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED,
    ]


def test_missing_or_failed_7R_validation_evidence_blocks():
    pipeline = _auto_pipeline()
    failure = AutomationValidationFailure(PIPELINE_STAGE_VALIDATION, "GeometryValidationError", "boom")

    variants = (
        dict(validation_passed=False, validation_result=None, validation_failure=failure),
        dict(validation_passed=True, validation_result=None, validation_failure=None),
        dict(validation_passed=True, validation_result=pipeline.validation_result, validation_failure=failure),
    )
    for overrides in variants:
        gate = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, **overrides))
        assert gate.decision == AUTOMATION_DECISION_REVIEW, overrides
        assert {b.code for b in gate.blockers} == {OUTPUT_BLOCKER_VALIDATION}, overrides

    failed = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, **variants[0]))
    assert "7R validation: failed" in failed.evidence_summary


def test_missing_7V_specification_or_identity_blocks():
    pipeline = _auto_pipeline()

    no_spec = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, specification=None))
    assert no_spec.decision == AUTOMATION_DECISION_REVIEW
    assert {b.code for b in no_spec.blockers} == {OUTPUT_BLOCKER_SPECIFICATION}
    assert "7V specification: missing" in no_spec.evidence_summary
    assert "connection identity: missing" in no_spec.evidence_summary

    no_identity = evaluate_fabrication_output_gate(dataclasses.replace(
        pipeline, specification=dataclasses.replace(pipeline.specification, connection_id=None),
    ))
    assert no_identity.decision == AUTOMATION_DECISION_REVIEW
    assert {b.code for b in no_identity.blockers} == {OUTPUT_BLOCKER_SPECIFICATION}


def test_missing_7O_assembly_blocks():
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(dataclasses.replace(pipeline, reviewed_assembly=None))
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert {b.code for b in gate.blockers} == {OUTPUT_BLOCKER_ASSEMBLY}
    assert "assembly built: false" in gate.evidence_summary


# =============================================================================
# 9-11. Nothing but the genuine pipeline evidence: no injection, approval, confidence, tasks
# =============================================================================
def test_no_validation_or_approval_injection_parameters_exist():
    for function, expected in (
        (evaluate_fabrication_output_gate, {"pipeline_result", "require_confirmation"}),
        (
            evaluate_project_fabrication_output_gate,
            {"project_result", "pipeline_results", "require_confirmation_by_package_id"},
        ),
    ):
        parameters = inspect.signature(function).parameters
        assert set(parameters) == expected
        for forbidden in ("validation_passed", "validation_evidence", "approved", "approve"):
            assert forbidden not in parameters


def test_human_approval_alone_does_not_permit_fabrication_output():
    # The genuine 7AD loop: the human approves the review; the engineering gap stays.
    collection, built = _seven_ad_package(
        _reviewed_package(review_status="in_review", holes=None), project_id=PROJECT_ID,
    )
    tasks = _tasks(built.connection_tasks[0])
    built = _resolve(built, tasks[TASK_COMPLETE_REVIEW], None)
    rerun = _rerun(built, collection)
    assert rerun.outcomes[0].decision == AUTOMATION_DECISION_REVIEW  # approval recorded, no AUTO

    # The 7AE gate consumes the genuine pipeline result over the REBUILT package — the approval
    # itself supplies no engineering data, so output stays blocked.
    pipeline = _pipeline(rerun.outcomes[0].rebuilt_package)
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_HOLE_DIAMETER in {b.code for b in gate.blockers}


def test_AI_confidence_alone_does_not_permit_fabrication_output():
    # The milestone's own high-confidence example: timber marks, nominal bolt size, confidence 99.
    pipeline = _pipeline(_raw_timber_package(KNOWN_MARKS))
    assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_REVIEW  # fixture precondition
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    # Confidence is never evidence: it does not even appear in the output gate's evidence.
    assert not any("confidence" in line.lower() for line in gate.evidence_summary)


def test_task_resolution_alone_does_not_permit_fabrication_output():
    # Resolve every acknowledgment task but leave the engineering gap: resolved tasks alone
    # change nothing about the evidence the output gate reads.
    collection, built = _seven_ad_package(_reviewed_package(holes=None), project_id=PROJECT_ID)
    tasks = _tasks(built.connection_tasks[0])
    # Acknowledge every resolvable task but the engineering gap itself (the missing holes):
    # resolved tasks alone change nothing about the evidence the output gate reads.
    for task_type in (TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION):
        built = _resolve(built, tasks[task_type], None)
    rerun = _rerun(built, collection)
    assert rerun.outcomes[0].decision == AUTOMATION_DECISION_REVIEW

    pipeline = _pipeline(rerun.outcomes[0].rebuilt_package)
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert AUTOMATION_BLOCKER_HOLE_DIAMETER in {b.code for b in gate.blockers}


# =============================================================================
# 12-15. The real Arkles capture and mixed projects
# =============================================================================
@needs_real_capture
def test_real_arkles_zero_automatic_outputs_with_genuine_blockers_and_scope_facts():
    intake, project_result = _arkles_project()
    # One genuine pipeline result per candidate, exactly as the 7AB evaluation ran them (no
    # member context exists for the raw Arkles candidates — none is invented here either).
    pipelines = {
        candidate.review_package_id: evaluate_reviewed_connection_for_automation(candidate.package)
        for candidate in intake.collection.candidates
    }
    gate = evaluate_project_fabrication_output_gate(project_result, pipelines)

    # Zero automatic fabrication outputs: every candidate stays REVIEW.
    assert gate.connections_total == 3
    assert (gate.eligible_count, gate.confirm_count, gate.review_count) == (0, 0, 3)
    assert gate.package_decision == AUTOMATION_DECISION_REVIEW
    assert gate.blocked_outputs == ("RP-0001", "RP-0002", "RP-0003")

    # Only the genuine 7Z blocker codes, never OUTPUT_* renames, in 7Z's own order — the exact
    # nine that 7AB recorded for these candidates, each firing for all three.
    expected_codes = (
        AUTOMATION_BLOCKER_REVIEW_STATUS,
        AUTOMATION_BLOCKER_MEMBER_IDENTITY,
        AUTOMATION_BLOCKER_POSITION,
        AUTOMATION_BLOCKER_PLATE,
        AUTOMATION_BLOCKER_HOLE_DIAMETER,
        AUTOMATION_BLOCKER_LOCATION,
        AUTOMATION_BLOCKER_ATTACHMENT,
        AUTOMATION_BLOCKER_SPECIFICATION,
        AUTOMATION_BLOCKER_VALIDATION,
    )
    assert [code for code, _ in gate.blockers_by_code] == list(expected_codes)
    assert all(count == 3 for _, count in gate.blockers_by_code)
    assert all(code in AUTOMATION_BLOCKER_CODES for code, _ in gate.blockers_by_code)
    for outcome in gate.connection_results:
        assert outcome.gate_result.decision == AUTOMATION_DECISION_REVIEW
        assert outcome.gate_result.blockers  # every candidate has visible blockers

    # The 7Y scope facts are carried verbatim — 30 received / 6 parse failures / 11 not analysed
    # / 41-page set — and the gate never claims the entire drawing set was processed.
    scope = gate.intake_scope
    assert scope == project_result.intake_scope
    assert scope.pages_received == 30
    assert scope.parse_failed_pages == (2, 11, 12, 13, 22, 29)
    assert scope.drawing_set_page_count == 41
    assert scope.pages_not_analysed == 11
    assert scope.scope_statement in gate.summary
    assert "pages_received = 30; parse_failures = 6; pages_not_analysed = 11; drawing_set_page_count = 41" in \
        "\n".join(gate.summary)
    assert gate.scope_statement == PROJECT_FABRICATION_OUTPUT_SCOPE_STATEMENT
    assert "never claims the entire drawing set was processed" in gate.scope_statement
    assert not any(w.code == PROJECT_WARNING_SCOPE_UNREPORTED for w in gate.warnings)


def test_mixed_project_keeps_AUTO_connections_eligible_but_blocks_the_complete_package():
    collection, project_result, pipelines, gate = _project_gate(
        _reviewed_package(), _reviewed_package(), _reviewed_package(holes=None),
    )

    # Per-connection eligibility is preserved exactly as the evidence states it.
    assert gate.connections_total == 3
    assert [o.review_package_id for o in gate.connection_results] == ["RP-0001", "RP-0002", "RP-0003"]
    assert [o.gate_result.decision for o in gate.connection_results] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_REVIEW,
    ]
    assert gate.connection_results[0].gate_result.blockers == ()
    assert gate.connection_results[0].gate_result.connection_id == "CONN-REAL-UB-CAD-001-L2"

    # But a complete automatic package is NOT released while any required output is blocked.
    assert (gate.eligible_count, gate.confirm_count, gate.review_count) == (2, 0, 1)
    assert gate.package_decision == AUTOMATION_DECISION_REVIEW
    assert gate.blocked_outputs == ("RP-0003",)
    assert FABRICATION_PACKAGE_NOT_RELEASED_STATEMENT in gate.summary
    assert "RP-0003: REVIEW" in "\n".join(gate.summary)

    # The blocked connection's 7Z blockers are aggregated with their own identities.
    assert (AUTOMATION_BLOCKER_HOLE_DIAMETER, 1) in gate.blockers_by_code


def test_confirmation_holds_block_the_complete_package_until_the_policy_is_satisfied():
    collection, project_result, pipelines, gate = _project_gate(
        _reviewed_package(), _reviewed_package(), _reviewed_package(),
        holds={"RP-0002": ("FAB RELEASE",)},
    )

    assert [o.gate_result.decision for o in gate.connection_results] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_AUTO,
    ]
    assert (gate.eligible_count, gate.confirm_count, gate.review_count) == (2, 1, 0)
    assert gate.package_decision == AUTOMATION_DECISION_CONFIRM
    assert gate.blocked_outputs == ("RP-0002",)
    assert FABRICATION_PACKAGE_NOT_RELEASED_STATEMENT in gate.summary

    # The confirmation policy satisfied: re-evaluate the SAME genuine evidence, no hold.
    released = evaluate_project_fabrication_output_gate(project_result, pipelines)
    assert released.package_decision == AUTOMATION_DECISION_AUTO
    assert released.blocked_outputs == ()
    assert FABRICATION_PACKAGE_RELEASED_STATEMENT in released.summary

    # The confirmation itself manufactured nothing: identical evidence for the held connection.
    held = next(o for o in gate.connection_results if o.review_package_id == "RP-0002").gate_result
    released_connection = next(
        o for o in released.connection_results if o.review_package_id == "RP-0002"
    ).gate_result
    assert held.evidence_summary == released_connection.evidence_summary


def test_blocked_plus_held_connection_reports_the_deferred_confirmation_warning():
    review_pipeline = _pipeline(_reviewed_package(holes=None))
    gate = evaluate_fabrication_output_gate(review_pipeline, require_confirmation=("ENGINEER",))

    assert gate.decision == AUTOMATION_DECISION_REVIEW
    assert gate.warnings[-1].code == OUTPUT_WARNING_CONFIRMATION_DEFERRED
    # 7Z's own warnings pass through verbatim ahead of the 7AE one.
    assert gate.warnings[:-1] == review_pipeline.automation_gate_result.warnings


# =============================================================================
# 16-19. Derived counts, stable aggregation, empty projects, scope reporting
# =============================================================================
def test_project_counts_and_aggregation_are_derived_and_deterministic():
    collection, project_result, pipelines, gate = _project_gate(
        _reviewed_package(), _reviewed_package(holes=None),
    )
    outcomes = gate.connection_results
    assert gate.eligible_count == sum(1 for o in outcomes if o.gate_result.decision == AUTOMATION_DECISION_AUTO)
    assert gate.confirm_count == sum(1 for o in outcomes if o.gate_result.decision == AUTOMATION_DECISION_CONFIRM)
    assert gate.review_count == sum(1 for o in outcomes if o.gate_result.decision == AUTOMATION_DECISION_REVIEW)
    assert gate.blocked_outputs == tuple(
        o.review_package_id for o in outcomes if o.gate_result.decision != AUTOMATION_DECISION_AUTO
    )

    again = evaluate_project_fabrication_output_gate(project_result, pipelines)
    assert again == gate
    assert repr(again) == repr(gate)
    assert again.blockers_by_code == gate.blockers_by_code


def test_blocker_aggregation_orders_OUTPUT_codes_first_then_7Z_codes():
    # RP-0002's pipeline loses its genuine 7R evidence -> an OUTPUT_* blocker enters the
    # project alongside RP-0003's passed-through 7Z blockers.
    collection, project_result, pipelines, _ = _project_gate(
        _reviewed_package(), _reviewed_package(), _reviewed_package(holes=None),
    )
    failure = AutomationValidationFailure(PIPELINE_STAGE_VALIDATION, "GeometryValidationError", "boom")
    pipelines["RP-0002"] = dataclasses.replace(
        pipelines["RP-0002"], validation_passed=False, validation_result=None, validation_failure=failure,
    )
    gate = evaluate_project_fabrication_output_gate(project_result, pipelines)

    codes = [code for code, _ in gate.blockers_by_code]
    assert codes[0] == OUTPUT_BLOCKER_VALIDATION
    assert codes[1:] == [code for code in AUTOMATION_BLOCKER_CODES if code in codes]
    assert gate.blocked_outputs == ("RP-0002", "RP-0003")
    assert (OUTPUT_BLOCKER_VALIDATION, 1) in gate.blockers_by_code
    # Both AUTO and both blocked connections stay individually visible.
    assert gate.eligible_count == 1 and gate.review_count == 2


def test_empty_project_and_unreported_scope_are_stated_never_claimed():
    collection = _collection()
    project_result = _evaluate(collection)
    gate = evaluate_project_fabrication_output_gate(project_result, {})

    assert gate.connections_total == 0
    assert gate.package_decision == AUTOMATION_DECISION_REVIEW
    assert gate.blocked_outputs == ()
    assert EMPTY_FABRICATION_OUTPUT_STATEMENT in gate.summary
    assert "eligible_outputs = 0" in gate.summary

    # No 7Y scope was carried into this project result: reported as a warning, never invented.
    assert any(w.code == PROJECT_WARNING_SCOPE_UNREPORTED for w in gate.warnings)
    assert gate.intake_scope == project_result.intake_scope


# =============================================================================
# 20-30. The drawing boundary, purity, immutability, determinism, loud errors
# =============================================================================
def test_drawing_generator_unreachable_while_blocked_and_only_through_the_boundary_on_AUTO(monkeypatch, tmp_path):
    calls = []

    def recording_drawing_entry(assembly, output_path, **kwargs):
        calls.append((assembly, output_path))
        return Path(output_path)

    monkeypatch.setattr(
        reviewed_connection_drawing_gate,
        "generate_fabrication_drawing_from_reviewed_assembly",
        recording_drawing_entry,
    )

    def sanctioned_dispatch(pipeline_result, output_path):
        """The approved integration boundary: only an AUTO gate result may reach the drawing generator."""
        gate = evaluate_fabrication_output_gate(pipeline_result)
        if gate.decision != AUTOMATION_DECISION_AUTO:
            return None
        return reviewed_connection_drawing_gate.generate_fabrication_drawing_from_reviewed_assembly(
            pipeline_result.reviewed_assembly, output_path,
        )

    review_pipeline = _pipeline(_reviewed_package(holes=None))
    confirm_pipeline = _pipeline(_reviewed_package(), require_confirmation=("ENGINEER",))
    auto_pipeline = _auto_pipeline()

    # REVIEW and CONFIRM never reach the drawing generator.
    assert sanctioned_dispatch(review_pipeline, tmp_path / "blocked.pdf") is None
    assert sanctioned_dispatch(confirm_pipeline, tmp_path / "confirm.pdf") is None
    assert calls == []

    # AUTO reaches it exactly once, with the genuine assembly and nothing else.
    assert sanctioned_dispatch(auto_pipeline, tmp_path / "auto.pdf") == tmp_path / "auto.pdf"
    assert len(calls) == 1
    assert calls[0][0] is auto_pipeline.reviewed_assembly


def test_gate_calls_no_validation_assembly_7Z_or_drawing_entry_points(monkeypatch, tmp_path):
    # Build every input BEFORE the spies are armed: the gate itself must call none of these.
    pipeline = _auto_pipeline()
    collection, project_result, pipelines, _ = _project_gate(_reviewed_package())

    def boom(*args, **kwargs):
        raise AssertionError("7AE must never call this entry point")

    monkeypatch.setattr(automation_gate_module, "evaluate_automation_gate", boom)
    monkeypatch.setattr(automation_pipeline_module, "evaluate_reviewed_connection_for_automation", boom)
    monkeypatch.setattr(reviewed_connection_validation_gate, "validate_reviewed_connection_assembly", boom)
    monkeypatch.setattr(reviewed_connection_assembly, "build_reviewed_two_member_connection_assembly", boom)
    monkeypatch.setattr(
        reviewed_connection_drawing_gate, "generate_fabrication_drawing_from_reviewed_assembly", boom,
    )
    monkeypatch.setattr(drawing_interface, "generate_connection_fabrication_drawing_pdf", boom)
    monkeypatch.setattr(pdf_builder, "build_connection_pdf", boom)

    # Both gate levels evaluate fine — and create NO files of any kind.
    monkeypatch.chdir(tmp_path)
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_AUTO
    project_gate = evaluate_project_fabrication_output_gate(project_result, pipelines)
    assert project_gate.package_decision == AUTOMATION_DECISION_AUTO
    assert list(tmp_path.iterdir()) == []


def test_inputs_never_mutated_outputs_frozen_and_repeats_deterministic():
    pipeline = _auto_pipeline()
    gate_before = pipeline.automation_gate_result
    spec_before = copy.deepcopy(pipeline.specification)  # plain data — safe to copy
    validation_before = pipeline.validation_result
    assembly_before = pipeline.reviewed_assembly
    validation_passed_before = pipeline.validation_passed

    first = evaluate_fabrication_output_gate(pipeline, require_confirmation=("HOLD",))
    second = evaluate_fabrication_output_gate(pipeline, require_confirmation=("HOLD",))
    assert first == second and repr(first) == repr(second)

    # The pipeline result object is untouched: the 7Z result, the specification and the
    # validation evidence are the same objects with the same contents; the assembly is the
    # same object (never rebuilt, never replaced).
    assert pipeline.automation_gate_result == gate_before
    assert pipeline.specification == spec_before
    assert pipeline.validation_result == validation_before
    assert pipeline.validation_passed == validation_passed_before
    assert pipeline.reviewed_assembly is assembly_before

    # Every output is frozen plain data.
    for frozen in (first, second):
        with pytest.raises(dataclasses.FrozenInstanceError):
            frozen.decision = AUTOMATION_DECISION_REVIEW
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(frozen, "_not_a_field", "boom")

    collection, project_result, pipelines, gate = _project_gate(_reviewed_package())
    project_before = copy.deepcopy(project_result)  # plain frozen data — safe to copy
    with pytest.raises(dataclasses.FrozenInstanceError):
        gate.package_decision = AUTOMATION_DECISION_REVIEW
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(gate, "_not_a_field", "boom")
    assert project_result == project_before
    assert gate.connection_results[0].gate_result.decision == AUTOMATION_DECISION_AUTO


def test_programming_errors_propagate_loudly():
    pipeline = _auto_pipeline()
    with pytest.raises(TypeError):
        evaluate_fabrication_output_gate("not a pipeline result")

    collection = _collection(_reviewed_package())
    project_result = _evaluate(collection)
    pipelines = {"RP-0001": pipeline}
    with pytest.raises(TypeError):
        evaluate_project_fabrication_output_gate("not a project result", pipelines)
    with pytest.raises(TypeError):
        evaluate_project_fabrication_output_gate(project_result, [("RP-0001", pipeline)])
    with pytest.raises(ValueError):
        evaluate_project_fabrication_output_gate(project_result, {})  # a connection's evidence missing
    with pytest.raises(ValueError):
        evaluate_project_fabrication_output_gate(project_result, {"RP-0001": pipeline, "RP-0002": pipeline})
    with pytest.raises(ValueError):
        evaluate_project_fabrication_output_gate(
            project_result, pipelines, require_confirmation_by_package_id={"RP-9999": ("X",)},
        )


def test_fabrication_output_gate_module_imports_are_pure():
    source = Path(fabrication_output_gate.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported == {
        "app.cad_engine.automation_gate",                # 7Z's stable codes/types only — never its evaluate function
        "app.cad_engine.automation_pipeline",            # the pipeline RESULT type only — never re-run
        "app.cad_engine.project_automation",             # the 7AB project result + intake scope
        "app.cad_engine.reviewed_connection_specification",  # the 7V product type (evidence identity)
        "collections.abc",
        "dataclasses",
    }
    assert "supabase" not in source.lower()


def test_module_imports_without_any_secret_environment():
    code = "import app.cad_engine.fabrication_output_gate; print('ok')"
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"SUPABASE_URL", "SUPABASE_KEY", "ANTHROPIC_API_KEY"}
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
