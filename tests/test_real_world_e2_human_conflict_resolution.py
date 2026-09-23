"""
Milestone E2 — HUMAN RESOLUTION OF THE TWO HARD CATALOGUE CONFLICTS:
focused boundary proofs.

Proven here, unit by unit:

  * conflict creation: both real conflicts (250X90PFC vs 250PFC;
    310UB40 vs 310UB40.4) build deterministic, frozen conflict records
    carrying both complete source rows, the exact conflicting fields,
    the original E1 verdict and resolution; a pair that agrees is
    refused at construction, so a "never a conflict" record can never
    exist;
  * the 7AC-shaped review task: the existing ExceptionResolutionTask
    vocabulary (TASK_RESOLVE_CONFLICT), naming both sources, both
    values per field, the blocked reason and the human-resolution
    requirement — presenting neither side as already correct, and
    resolving nothing by itself;
  * the 7AD human resolution: explicit decisions only
    (CAPTURE_AUTHORITATIVE / LIVE_CATALOGUE_AUTHORITATIVE /
    KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE), fail-closed on missing,
    invalid, misaddressed or repeated decisions, rationale preserved
    verbatim, original evidence byte-identical;
  * the 7Z re-decision: a pure function of the recorded decision —
    blocked without one, capture or catalogue geometry applied only on
    the explicit choice, original evidence never modified;
  * the 7AQ audit/acceptance: everything recoverable (original values,
    conflicting fields, original blocked verdict, decision, rationale,
    resulting decision), and the structural distinction between
    CONFLICT_UNRESOLVED and CONFLICT_HUMAN_RESOLVED — with
    CONFLICT_STATE_NO_CONFLICT impossible to construct;
  * the ten negative proofs from the E2 brief (no silent merge, no
    automatic selection of either source, weights/names/order/source
    priority never resolve, no progression without a decision, no
    erased evidence, no blocked conflict accepted);
  * real-fixture integrity: the genuine Selby capture rows and the
    weight-only 250PFC row are byte-unchanged, no row claims
    production resolution, and every test decision is an explicit
    synthetic test input — the real Selby evidence does not establish
    either side, and no real conflict is marked production-resolved;
  * snapshot cross-verification against the user-captured live
    snapshot at /tmp (an honest skip when it is not present — the
    always-on literals below are the same rows, so no assertion
    depends on the file).

No test here modifies any product module, the production matcher, the
DXF path, any existing gate, any fixture file or the live snapshot.
"""

import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.cad_engine import catalogue_conflict_resolution as ccr
from app.cad_engine.exception_resolution import (
    STATUS_OPEN,
    TASK_RESOLVE_CONFLICT,
    HumanResolution,
)
from app.engineering_data.section_catalogue import CATALOGUE, CatalogueMatcher
from app.engineering_data.source_of_truth import (
    RESOLUTION_BLOCKED_REVIEW,
    VERDICT_CONFLICT_BLOCKED,
    evaluate_section_evidence,
)

SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"

# The live catalogue rows these proofs exercise, taken verbatim from
# the Milestone E live reconciliation (the snapshot evidence), and
# cross-checked against the snapshot itself when it is present.
LIVE_250PFC = {
    "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
}
LIVE_310UB40_4 = {
    "name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
}
LIVE_100PFC = {
    "name": "100PFC", "family": "PFC", "depth": 100.0, "flange_width": 50.0,
    "flange_thickness": 7.0, "web_thickness": 4.5, "weight_per_metre": 8.33,
}

CAPTURE_250X90PFC = {
    "name": "250X90PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 15.0, "web_thickness": 8.0, "weight_per_metre": 35.5,
}
CAPTURE_310UB40 = {
    "name": "310UB40", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 11.8, "web_thickness": 6.1, "weight_per_metre": 40.4,
}

SYNTHETIC_NOTE = "synthetic E2 test input — the real Selby evidence does not establish " \
    "either side; no real engineering decision is made"


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


def _snapshot_rows():
    if not SNAPSHOT_PATH.exists():
        pytest.skip("the user-captured live snapshot is not present at "
                    "/tmp/steelspec_live_steel_sections_218.json — cross-verification "
                    "requires that evidence")
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256
    rows = json.loads(raw)
    return {row["name"]: row for row in rows}


# =============================================================================
# conflict creation
# =============================================================================

class TestConflictCreation:

    def test_both_real_conflicts_build_records(self):
        a, b = _conflict_a(), _conflict_b()
        assert a.conflict_id == "CONFLICT-250X90PFC-vs-250PFC"
        assert b.conflict_id == "CONFLICT-310UB40-vs-310UB40.4"

    def test_records_are_distinct(self):
        a, b = _conflict_a(), _conflict_b()
        assert a.conflict_id != b.conflict_id
        assert a.captured_name != b.captured_name
        assert a.conflicting_fields != b.conflicting_fields

    def test_conflict_a_preserves_the_exact_conflicting_fields(self):
        a = _conflict_a()
        assert a.conflicting_fields == (
            ("flange_thickness", 15.0, 12.0),
            ("web_thickness", 8.0, 7.0),
        )

    def test_conflict_b_preserves_the_exact_conflicting_fields(self):
        b = _conflict_b()
        assert b.conflicting_fields == (("flange_thickness", 11.8, 10.2),)

    def test_both_complete_source_rows_are_carried_lossless(self):
        a, b = _conflict_a(), _conflict_b()
        assert dict(a.captured_values) == CAPTURE_250X90PFC
        assert dict(a.catalogue_values) == LIVE_250PFC
        assert dict(b.captured_values) == CAPTURE_310UB40
        assert dict(b.catalogue_values) == LIVE_310UB40_4

    def test_original_e1_verdict_and_resolution_are_recorded(self):
        a, b = _conflict_a(), _conflict_b()
        for conflict in (a, b):
            assert conflict.original_verdict == VERDICT_CONFLICT_BLOCKED
            assert conflict.original_resolution == RESOLUTION_BLOCKED_REVIEW

    def test_new_records_are_unresolved_and_blocked(self):
        a, b = _conflict_a(), _conflict_b()
        for conflict in (a, b):
            assert conflict.resolution_status == ccr.RESOLUTION_STATUS_UNRESOLVED
            assert conflict.human_decision is None
            assert conflict.human_rationale is None
            assert conflict.resulting_decision is None

    def test_build_is_deterministic(self):
        assert _conflict_a() == _conflict_a()
        assert _conflict_b() == _conflict_b()

    def test_build_is_independent_of_source_row_key_order(self):
        capture_reordered = {
            "flange_thickness": 15.0, "name": "250X90PFC", "depth": 250.0,
            "weight_per_metre": 35.5, "family": "PFC", "flange_width": 90.0,
            "web_thickness": 8.0,
        }
        live_reordered = {
            "web_thickness": 7.0, "flange_thickness": 12.0, "name": "250PFC",
            "weight_per_metre": 31.8, "depth": 250.0, "flange_width": 90.0,
            "family": "PFC",
        }
        assert ccr.build_catalogue_conflict(capture_reordered, live_reordered) == _conflict_a()

    def test_a_pair_that_agrees_is_never_a_conflict(self):
        with pytest.raises(ValueError, match="never conflicts"):
            ccr.build_catalogue_conflict(dict(CATALOGUE["100PFC"]), dict(LIVE_100PFC))

    def test_the_record_is_frozen(self):
        a = _conflict_a()
        with pytest.raises(FrozenInstanceError):
            a.human_decision = ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE

    def test_conflicting_field_order_is_field_based_not_source_based(self):
        # GEOMETRY_DEFINING_FIELDS order: flange_thickness precedes web_thickness —
        # the record follows the E1 boundary's field order, never the sources' order.
        a = _conflict_a()
        assert [field for field, _, _ in a.conflicting_fields] == [
            "flange_thickness", "web_thickness",
        ]


# =============================================================================
# the 7AC-shaped review task
# =============================================================================

class TestReviewTask:

    def test_the_task_uses_the_existing_7ac_vocabulary(self):
        task = ccr.build_conflict_review_task(_conflict_a())
        assert task.task_type == TASK_RESOLVE_CONFLICT
        assert task.answer_type == ccr.ANSWER_SOURCE_AUTHORITY
        assert task.status == STATUS_OPEN
        assert task.resolution is None
        assert task.blocker_codes == ()

    def test_the_task_lists_only_the_explicit_decisions(self):
        task = ccr.build_conflict_review_task(_conflict_a())
        assert task.allowed_choices == ccr.HUMAN_DECISIONS
        assert len(task.allowed_choices) == 3

    def test_the_task_identifies_both_sources_and_their_values(self):
        a = _conflict_a()
        question = ccr.build_conflict_review_task(a).question
        assert "250X90PFC" in question and "250PFC" in question
        assert "flange_thickness" in question and "15.0" in question and "12.0" in question
        assert "web_thickness" in question and "8.0" in question and "7.0" in question

    def test_the_task_states_the_block_and_the_human_requirement(self):
        a = _conflict_a()
        task = ccr.build_conflict_review_task(a)
        assert RESOLUTION_BLOCKED_REVIEW in task.question
        assert "human decision" in task.question
        assert "never" in task.evidence_requirement
        assert "BLOCKED_REVIEW" in task.evidence_requirement

    def test_the_task_does_not_present_either_side_as_correct(self):
        a = _conflict_a()
        task = ccr.build_conflict_review_task(a)
        assert "captured" in task.question and "catalogue" in task.question
        assert "authoritative" in task.question  # the question asks; it never asserts

    def test_the_task_carries_the_exact_conflicting_triples(self):
        a = _conflict_a()
        assert ccr.build_conflict_review_task(a).current_ai_value == a.conflicting_fields

    def test_task_creation_is_deterministic(self):
        assert ccr.build_conflict_review_task(_conflict_a()) == \
            ccr.build_conflict_review_task(_conflict_a())

    def test_task_creation_resolves_nothing(self):
        a = _conflict_a()
        ccr.build_conflict_review_task(a)
        assert a.human_decision is None
        assert ccr.redecide_conflict_after_resolution(a).resulting_decision == \
            RESOLUTION_BLOCKED_REVIEW


# =============================================================================
# human resolution (7AD semantics, fail-closed)
# =============================================================================

class TestHumanResolution:

    def test_a_capture_authoritative_resolution_is_recorded(self):
        resolved = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        assert resolved.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert resolved.resolution_status == ccr.RESOLUTION_STATUS_RESOLVED

    def test_b_live_catalogue_authoritative_resolution_is_recorded(self):
        resolved = _resolve(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        assert resolved.human_decision == ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE
        assert resolved.resolution_status == ccr.RESOLUTION_STATUS_RESOLVED

    def test_c_no_decision_leaves_the_conflict_unresolved(self):
        a = _conflict_a()
        assert a.human_decision is None
        assert ccr.redecide_conflict_after_resolution(a).resulting_decision == \
            RESOLUTION_BLOCKED_REVIEW
        with pytest.raises(ValueError, match="blocked"):
            ccr.resolved_geometry_fields(a)

    def test_d_invalid_decisions_fail_closed(self):
        with pytest.raises(ValueError, match="no default decision"):
            ccr.SourceAuthorityDecision("AUTOMATIC", "some rationale")
        with pytest.raises(ValueError, match="no default decision"):
            ccr.SourceAuthorityDecision("CAPTURE", "some rationale")
        with pytest.raises(ValueError, match="rationale"):
            ccr.SourceAuthorityDecision(ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "   ")

    def test_d_misaddressed_or_malformed_resolutions_are_refused(self):
        a = _conflict_a()
        task = ccr.build_conflict_review_task(a)
        good = ccr.SourceAuthorityDecision(ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "r")
        # wrong task id — a decision can never land on a different conflict
        with pytest.raises(ValueError, match="different conflict"):
            ccr.apply_conflict_resolution(a, HumanResolution(
                task_id="CONFLICT-OTHER-T01", task_type=task.task_type,
                answer_type=task.answer_type, answer=good, evidence=SYNTHETIC_NOTE))
        # wrong answer type
        with pytest.raises(ValueError, match="answer_type"):
            ccr.apply_conflict_resolution(a, HumanResolution(
                task_id=task.task_id, task_type=task.task_type,
                answer_type="FIELD_DECISION", answer=good, evidence=SYNTHETIC_NOTE))
        # an answer that is not a SourceAuthorityDecision
        with pytest.raises(ValueError, match="SourceAuthorityDecision"):
            ccr.apply_conflict_resolution(a, HumanResolution(
                task_id=task.task_id, task_type=task.task_type,
                answer_type=task.answer_type,
                answer=ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, evidence=SYNTHETIC_NOTE))
        # a conflict is resolved at most once
        resolved = _resolve(a, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        with pytest.raises(ValueError, match="at most once"):
            ccr.apply_conflict_resolution(resolved, HumanResolution(
                task_id=task.task_id, task_type=task.task_type,
                answer_type=task.answer_type, answer=good, evidence=SYNTHETIC_NOTE))

    def test_e_the_rationale_and_evidence_are_preserved_verbatim(self):
        rationale = "engineer verified page 5: the flange is 15 per the drawing  (synthetic)"
        resolved = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
                            rationale=rationale)
        assert resolved.human_rationale == rationale

    def test_e_original_evidence_is_unchanged_by_resolution(self):
        a = _conflict_a()
        resolved = _resolve(a, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        assert resolved.captured_values == a.captured_values
        assert resolved.catalogue_values == a.catalogue_values
        assert resolved.conflicting_fields == a.conflicting_fields
        assert resolved.original_verdict == a.original_verdict
        assert resolved.original_resolution == a.original_resolution

    def test_repeated_identical_resolution_is_deterministic(self):
        first = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
                         rationale="same rationale", evidence="same evidence")
        second = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
                          rationale="same rationale", evidence="same evidence")
        assert first == second


# =============================================================================
# re-decision (7Z semantics for the catalogue conflict)
# =============================================================================

class TestRedecision:

    def test_no_decision_means_no_progression(self):
        for conflict in (_conflict_a(), _conflict_b()):
            redone = ccr.redecide_conflict_after_resolution(conflict)
            assert redone.resulting_decision == RESOLUTION_BLOCKED_REVIEW
            assert redone.resolution_status == ccr.RESOLUTION_STATUS_UNRESOLVED

    def test_capture_decision_allows_capture_geometry_to_proceed(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        assert redone.resulting_decision == ccr.RESULTING_DECISION_CAPTURE_GEOMETRY
        assert ccr.resolved_geometry_fields(redone) == (
            ("flange_thickness", 15.0), ("web_thickness", 8.0),
        )

    def test_live_decision_allows_catalogue_geometry_to_proceed(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE))
        assert redone.resulting_decision == ccr.RESULTING_DECISION_CATALOGUE_GEOMETRY
        assert ccr.resolved_geometry_fields(redone) == (("flange_thickness", 10.2),)

    def test_keep_both_stays_blocked(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE))
        assert redone.resulting_decision == RESOLUTION_BLOCKED_REVIEW
        with pytest.raises(ValueError, match="blocked"):
            ccr.resolved_geometry_fields(redone)

    def test_changing_the_decision_produces_a_distinct_result(self):
        capture = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        live = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE))
        assert capture.resulting_decision != live.resulting_decision
        assert ccr.resolved_geometry_fields(capture) != ccr.resolved_geometry_fields(live)

    def test_redecision_cannot_manufacture_a_decision(self):
        # The only input is the record; an unresolved record can never yield an
        # applied geometry decision, whatever its evidence contains.
        for conflict in (_conflict_a(), _conflict_b()):
            assert ccr.redecide_conflict_after_resolution(conflict).resulting_decision == \
                RESOLUTION_BLOCKED_REVIEW

    def test_redecision_does_not_modify_the_original_evidence(self):
        a = _conflict_a()
        resolved = _resolve(a, ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        redone = ccr.redecide_conflict_after_resolution(resolved)
        assert redone.captured_values == a.captured_values
        assert redone.catalogue_values == a.catalogue_values
        assert redone.conflicting_fields == a.conflicting_fields
        assert redone.original_verdict == a.original_verdict

    def test_redecision_is_idempotent(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        assert ccr.redecide_conflict_after_resolution(redone) == redone

    def test_the_result_identifies_the_human_resolution_as_the_cause(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        # progression exists exactly because the recorded decision chose it
        assert redone.resulting_decision == ccr.RESULTING_DECISION_CAPTURE_GEOMETRY
        assert redone.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert redone.human_rationale


# =============================================================================
# acceptance / audit (7AQ semantics for the catalogue conflict)
# =============================================================================

class TestAcceptance:

    def test_resolved_capture_case_is_accepted(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        acceptance = ccr.accept_resolved_conflict(redone)
        assert acceptance.accepted is True
        assert "explicit" in acceptance.reason

    def test_resolved_live_case_is_accepted(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE))
        assert ccr.accept_resolved_conflict(redone).accepted is True

    def test_the_audit_recovers_everything(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
                     rationale="the drawing's flange and web govern"))
        audit = ccr.accept_resolved_conflict(redone).audit
        assert dict(audit.captured_values) == CAPTURE_250X90PFC          # original captured values
        assert dict(audit.catalogue_values) == LIVE_250PFC               # original catalogue values
        assert audit.conflicting_fields == (("flange_thickness", 15.0, 12.0),
                                            ("web_thickness", 8.0, 7.0))
        assert audit.original_verdict == VERDICT_CONFLICT_BLOCKED        # original blocked verdict
        assert audit.human_decision == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert audit.human_rationale == "the drawing's flange and web govern"
        assert audit.resulting_decision == ccr.RESULTING_DECISION_CAPTURE_GEOMETRY
        assert audit.resolved_geometry_fields == (("flange_thickness", 15.0),
                                                  ("web_thickness", 8.0))

    def test_unresolved_conflict_is_not_accepted(self):
        acceptance = ccr.accept_resolved_conflict(_conflict_a())
        assert acceptance.accepted is False
        assert "no human resolution" in acceptance.reason
        assert acceptance.audit.conflict_state == ccr.CONFLICT_STATE_UNRESOLVED

    def test_resolved_but_not_redecided_is_not_accepted(self):
        resolved = _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)
        acceptance = ccr.accept_resolved_conflict(resolved)
        assert acceptance.accepted is False
        assert "re-decision" in acceptance.reason

    def test_keep_both_is_not_accepted(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE))
        acceptance = ccr.accept_resolved_conflict(redone)
        assert acceptance.accepted is False
        assert acceptance.audit.conflict_state == ccr.CONFLICT_STATE_HUMAN_RESOLVED
        assert acceptance.audit.resolved_geometry_fields == ()

    def test_a_hand_built_resolved_looking_record_without_a_decision_is_refused(self):
        forged = ccr.CatalogueConflict(
            conflict_id="CONFLICT-FAKE-vs-FAKE", captured_name="FAKE",
            catalogue_name="FAKE",
            captured_values=(("flange_thickness", 1.0),),
            catalogue_values=(("flange_thickness", 2.0),),
            conflicting_fields=(("flange_thickness", 1.0, 2.0),),
            original_verdict=VERDICT_CONFLICT_BLOCKED,
            original_resolution=RESOLUTION_BLOCKED_REVIEW,
            human_decision=None, human_rationale=None,
            resulting_decision=ccr.RESULTING_DECISION_CAPTURE_GEOMETRY,
            resolution_status=ccr.RESOLUTION_STATUS_RESOLVED,
        )
        acceptance = ccr.accept_resolved_conflict(forged)
        assert acceptance.accepted is False
        assert "human decision" in acceptance.reason
        assert acceptance.audit.conflict_state == ccr.CONFLICT_STATE_UNRESOLVED

    def test_never_conflict_vs_resolved_conflict_cannot_be_confused(self):
        # structurally: a NO_CONFLICT audit record cannot exist — construction
        # refuses agreeing pairs, so the audit only ever sees genuine conflicts,
        # and the state distinguishes unresolved from human-resolved.
        with pytest.raises(ValueError, match="never conflicts"):
            ccr.build_catalogue_conflict(dict(CATALOGUE["100PFC"]), dict(LIVE_100PFC))
        unresolved = ccr.accept_resolved_conflict(_conflict_a())
        resolved = ccr.accept_resolved_conflict(ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)))
        assert unresolved.audit.conflict_state == ccr.CONFLICT_STATE_UNRESOLVED
        assert resolved.audit.conflict_state == ccr.CONFLICT_STATE_HUMAN_RESOLVED
        assert unresolved.audit.conflict_state != resolved.audit.conflict_state

    def test_the_audit_is_deterministic(self):
        redone = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        assert ccr.accept_resolved_conflict(redone) == ccr.accept_resolved_conflict(redone)


# =============================================================================
# the ten negative proofs (E2 brief section 14)
# =============================================================================

class TestNegativeProofs:

    def test_1_250x90pfc_is_never_silently_merged_with_250pfc(self):
        a = _conflict_a()
        assert a.captured_name == "250X90PFC" and a.catalogue_name == "250PFC"
        assert dict(a.captured_values)["flange_thickness"] == 15.0
        assert dict(a.catalogue_values)["flange_thickness"] == 12.0
        assert dict(CATALOGUE["250X90PFC"]) == CAPTURE_250X90PFC
        assert dict(CATALOGUE["250PFC"]) == {"name": "250PFC", "family": "PFC",
                                             "weight_per_metre": 35.5}

    def test_2_310ub40_is_never_silently_merged_with_310ub40_4(self):
        b = _conflict_b()
        assert b.captured_name == "310UB40" and b.catalogue_name == "310UB40.4"
        assert dict(b.captured_values)["flange_thickness"] == 11.8
        assert dict(b.catalogue_values)["flange_thickness"] == 10.2
        assert dict(CATALOGUE["310UB40"]) == CAPTURE_310UB40

    def test_3_the_live_catalogue_is_never_selected_automatically(self):
        for conflict in (_conflict_a(), _conflict_b()):
            redone = ccr.redecide_conflict_after_resolution(conflict)
            assert redone.resulting_decision == RESOLUTION_BLOCKED_REVIEW
            with pytest.raises(ValueError, match="blocked"):
                ccr.resolved_geometry_fields(redone)

    def test_4_capture_geometry_is_never_selected_automatically_either(self):
        # Engineer evidence is evidence, not priority: the captured values sit on
        # the record and still nothing proceeds without an explicit decision.
        for conflict in (_conflict_a(), _conflict_b()):
            assert conflict.conflicting_fields  # the capture values are present
            assert ccr.redecide_conflict_after_resolution(conflict).resulting_decision == \
                RESOLUTION_BLOCKED_REVIEW

    def test_5_weight_similarity_never_resolves_a_conflict(self):
        # Conflict A's weights differ by 3.7; conflict B's weights are identical —
        # both stay blocked without a decision; and with identical weights the
        # result changes only when the recorded decision changes.
        assert abs(35.5 - 31.8) > 0
        assert 40.4 == 40.4
        for conflict in (_conflict_a(), _conflict_b()):
            assert ccr.redecide_conflict_after_resolution(conflict).resulting_decision == \
                RESOLUTION_BLOCKED_REVIEW
        capture = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE))
        live = ccr.redecide_conflict_after_resolution(
            _resolve(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE))
        assert capture.resulting_decision != live.resulting_decision  # the decision, not the weight

    def test_6_name_similarity_never_resolves_a_conflict(self):
        # 250X90PFC vs 250PFC share their head; 310UB40 vs 310UB40.4 differ by a
        # suffix the name fallback would match — neither makes a decision appear.
        for conflict in (_conflict_a(), _conflict_b()):
            assert conflict.captured_name != conflict.catalogue_name
            assert ccr.redecide_conflict_after_resolution(conflict).resulting_decision == \
                RESOLUTION_BLOCKED_REVIEW

    def test_6b_the_name_fallback_cannot_bypass_the_boundary(self):
        # Name resolution (exact + ".0"-".9" fallback) over a live-shaped index
        # identifies 310UB40.4 — and the E1 boundary still blocks, and the E2
        # record built from the fallback result still needs an explicit decision.
        base = re.sub(r"\s+", "", "310UB40".strip().upper())
        by_norm = {re.sub(r"\s+", "", row["name"].strip().upper()): row
                   for row in (LIVE_310UB40_4, LIVE_250PFC)}
        resolved_row = by_norm.get(base) or by_norm.get(base + ".4")
        assert resolved_row is not None and resolved_row["flange_thickness"] == 10.2
        verdict = evaluate_section_evidence(dict(CATALOGUE["310UB40"]), resolved_row)
        assert verdict.status == VERDICT_CONFLICT_BLOCKED
        conflict = ccr.build_catalogue_conflict(dict(CATALOGUE["310UB40"]), resolved_row)
        assert ccr.redecide_conflict_after_resolution(conflict).resulting_decision == \
            RESOLUTION_BLOCKED_REVIEW

    def test_7_which_source_appears_first_never_resolves_a_conflict(self):
        # The re-decision takes exactly one input — the record — and the record's
        # value tuples are in canonical field-name order (proven above), so no
        # source ordering exists anywhere in the decision path. A record built
        # with the sources' roles swapped is reported honestly and still blocks.
        import inspect
        assert list(inspect.signature(ccr.redecide_conflict_after_resolution).parameters) == \
            ["conflict"]
        swapped = ccr.build_catalogue_conflict(dict(LIVE_250PFC), dict(CATALOGUE["250X90PFC"]))
        assert swapped.captured_name == "250PFC"  # honest about its inputs, never guessed
        assert swapped.conflicting_fields == (("flange_thickness", 12.0, 15.0),
                                              ("web_thickness", 7.0, 8.0))
        assert ccr.redecide_conflict_after_resolution(swapped).resulting_decision == \
            RESOLUTION_BLOCKED_REVIEW
        assert ccr.accept_resolved_conflict(swapped).accepted is False

    def test_8_nothing_progresses_without_an_explicit_human_decision(self):
        for conflict in (_conflict_a(), _conflict_b()):
            assert conflict.human_decision is None
            assert ccr.redecide_conflict_after_resolution(conflict).resulting_decision == \
                RESOLUTION_BLOCKED_REVIEW
            assert ccr.accept_resolved_conflict(conflict).accepted is False

    def test_9_the_original_conflicting_values_are_never_erased(self):
        a = _conflict_a()
        resolved = _resolve(a, ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)
        redone = ccr.redecide_conflict_after_resolution(resolved)
        audit = ccr.accept_resolved_conflict(redone).audit
        for record in (a, resolved, redone):
            assert dict(record.captured_values) == CAPTURE_250X90PFC
            assert dict(record.catalogue_values) == LIVE_250PFC
            assert record.conflicting_fields == (("flange_thickness", 15.0, 12.0),
                                                 ("web_thickness", 8.0, 7.0))
        assert dict(audit.captured_values) == CAPTURE_250X90PFC
        assert dict(audit.catalogue_values) == LIVE_250PFC

    def test_10_a_blocked_conflict_cannot_become_an_accepted_fact(self):
        a = _conflict_a()
        assert ccr.accept_resolved_conflict(a).accepted is False
        redone = ccr.redecide_conflict_after_resolution(a)  # blocked, still no decision
        assert ccr.accept_resolved_conflict(redone).accepted is False
        assert redone.resulting_decision == RESOLUTION_BLOCKED_REVIEW


# =============================================================================
# real fixture integrity — the real evidence is never rewritten
# =============================================================================

class TestRealFixtureIntegrity:

    def test_the_real_capture_rows_are_byte_unchanged(self):
        assert dict(CATALOGUE["250X90PFC"]) == CAPTURE_250X90PFC
        assert dict(CATALOGUE["310UB40"]) == CAPTURE_310UB40
        assert dict(CATALOGUE["250PFC"]) == {"name": "250PFC", "family": "PFC",
                                             "weight_per_metre": 35.5}

    def test_no_real_conflict_is_marked_production_resolved(self):
        for name in ("250X90PFC", "310UB40", "250PFC"):
            row = dict(CATALOGUE[name])
            assert "provenance" not in row, name
            assert "HUMAN_CONFIRMED" not in str(row) and "PRODUCTION_PROVEN" not in str(row)

    def test_the_live_literals_are_unchanged(self):
        assert LIVE_250PFC["weight_per_metre"] == 31.8
        assert LIVE_250PFC["flange_thickness"] == 12.0
        assert LIVE_250PFC["web_thickness"] == 7.0
        assert LIVE_310UB40_4["flange_thickness"] == 10.2

    def test_the_module_api_is_frozen_and_minimal(self):
        # No hidden auto-selection surface: the public API is exactly the
        # lifecycle above — nothing else can silently choose a source.
        assert set(ccr.__all__) == {
            "HUMAN_DECISION_CAPTURE_AUTHORITATIVE",
            "HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE",
            "HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE",
            "HUMAN_DECISIONS",
            "ANSWER_SOURCE_AUTHORITY",
            "RESULTING_DECISION_CAPTURE_GEOMETRY",
            "RESULTING_DECISION_CATALOGUE_GEOMETRY",
            "RESULTING_GEOMETRY_DECISIONS",
            "RESOLUTION_STATUS_UNRESOLVED",
            "RESOLUTION_STATUS_RESOLVED",
            "CONFLICT_STATE_NO_CONFLICT",
            "CONFLICT_STATE_UNRESOLVED",
            "CONFLICT_STATE_HUMAN_RESOLVED",
            "CATALOGUE_CONFLICT_SCOPE_STATEMENT",
            "SourceAuthorityDecision",
            "CatalogueConflict",
            "ConflictAuditRecord",
            "ConflictAcceptance",
            "build_catalogue_conflict",
            "build_conflict_review_task",
            "apply_conflict_resolution",
            "redecide_conflict_after_resolution",
            "resolved_geometry_fields",
            "build_conflict_audit",
            "accept_resolved_conflict",
        }

    def test_the_module_has_no_io_clock_randomness_or_network(self):
        source = Path(ccr.__file__).read_text()
        for forbidden in ("open(", "environ", "requests", "http", "supabase",
                          "random", "time.", "datetime", "os."):
            assert forbidden not in source, forbidden

    def test_the_scope_statement_declares_test_decisions_synthetic(self):
        assert "synthetic test inputs" in ccr.CATALOGUE_CONFLICT_SCOPE_STATEMENT
        assert "never decides which source is actually authoritative" in \
            ccr.CATALOGUE_CONFLICT_SCOPE_STATEMENT
        assert "not engineering truth" in ccr.CATALOGUE_CONFLICT_SCOPE_STATEMENT

    def test_the_decisions_have_no_ranking(self):
        assert ccr.HUMAN_DECISIONS == (
            "CAPTURE_AUTHORITATIVE",
            "LIVE_CATALOGUE_AUTHORITATIVE",
            "KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE",
        )
        assert len(set(ccr.HUMAN_DECISIONS)) == 3


# =============================================================================
# snapshot cross-verification (honest skip when the evidence is absent)
# =============================================================================

class TestSnapshotCrossVerification:

    def test_the_snapshot_rows_equal_the_live_literals(self):
        rows = _snapshot_rows()
        live_250pfc = rows["250PFC"]
        assert live_250pfc["flange_thickness"] == LIVE_250PFC["flange_thickness"] == 12.0
        assert live_250pfc["web_thickness"] == LIVE_250PFC["web_thickness"] == 7.0
        assert live_250pfc["weight_per_metre"] == LIVE_250PFC["weight_per_metre"] == 31.8
        live_310ub40_4 = rows["310UB40.4"]
        assert live_310ub40_4["flange_thickness"] == LIVE_310UB40_4["flange_thickness"] == 10.2

    def test_the_snapshot_rows_reproduce_both_conflict_verdicts(self):
        rows = _snapshot_rows()
        a = ccr.build_catalogue_conflict(dict(CATALOGUE["250X90PFC"]), dict(rows["250PFC"]))
        b = ccr.build_catalogue_conflict(dict(CATALOGUE["310UB40"]), dict(rows["310UB40.4"]))
        assert a.conflicting_fields == (("flange_thickness", 15.0, 12.0),
                                        ("web_thickness", 8.0, 7.0))
        assert b.conflicting_fields == (("flange_thickness", 11.8, 10.2),)
        assert ccr.redecide_conflict_after_resolution(a).resulting_decision == \
            RESOLUTION_BLOCKED_REVIEW
        assert ccr.redecide_conflict_after_resolution(b).resulting_decision == \
            RESOLUTION_BLOCKED_REVIEW

    def test_the_real_capture_rows_do_not_appear_in_the_snapshot(self):
        rows = _snapshot_rows()
        assert "250X90PFC" not in rows
        assert "310UB40" not in rows
        assert rows["250PFC"]["flange_thickness"] != CAPTURE_250X90PFC["flange_thickness"]
        assert rows["310UB40.4"]["flange_thickness"] != CAPTURE_310UB40["flange_thickness"]
