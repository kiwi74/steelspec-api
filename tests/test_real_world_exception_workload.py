"""
Milestone 7B2 — real 52-candidate exception workload map
(tests/test_real_world_exception_workload.py).

Drives the genuine 32-page Selby evidence through the existing chain
(7AZ accumulation -> 7Y intake -> 7X review queue -> 7AK contract) and
proves the 7B2 workload map (app/cad_engine/exception_workload.py) is a
faithful, deterministic, frozen view of it: exactly 52 candidates in
submission order, the real blocker-code / task-type / page / material
distributions, zero fabrication, zero prioritisation, and every negative
proof refused either at the map's own boundary or at the existing chain
boundaries the map sits on.

The map adds no vocabulary and no decisions: every projected value is
the contract's own, the real distribution below was recorded from the
genuine workflow run (never hand-built), and the constants are changed
only deliberately if the real evidence legitimately changes.
"""
import copy
import dataclasses
import json
from pathlib import Path

import pytest

from app.cad_engine import incremental_analysis as ia
from app.cad_engine.exception_workload import (
    build_exception_workload_map,
    workload_candidate,
)
from app.cad_engine.project_workflow import resolve_project_connection
from app.cad_engine.review_contract import build_project_review_contract
from tests.test_real_world_incremental_analysis import _workflow_over
from tests.test_real_world_material_extraction import REAL_PDF
from tests.test_real_world_multi_member_connection import (
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
)
from tests.test_real_world_production_coverage import (
    VIEW_AA_ANSWERS_BY_TASK_TYPE,
    _resolutions_for,
)
from tests.test_real_world_review_intake_provenance import (
    _accumulated,
    _combined,
    _intake,
    _rebuilt_intake,
    _with_modified_page,
    needs_all_captures,
)

EXPECTED_IDS = tuple(f"RP-{index + 1:04d}" for index in range(52))

# The genuine revision-0 distributions, recorded from the real workflow
# (probe output pinned in the milestone report) — asserted, never rebuilt.
REAL_BLOCKER_COUNTS = {
    "AUTOMATION_BLOCKER_REVIEW_STATUS": 52,
    "AUTOMATION_BLOCKER_PROVENANCE": 52,
    "AUTOMATION_BLOCKER_POSITION": 52,
    "AUTOMATION_BLOCKER_PLATE": 50,
    "AUTOMATION_BLOCKER_HOLE_DIAMETER": 52,
    "AUTOMATION_BLOCKER_LOCATION": 52,
    "AUTOMATION_BLOCKER_ATTACHMENT": 52,
    "AUTOMATION_BLOCKER_SPECIFICATION": 52,
    "AUTOMATION_BLOCKER_VALIDATION": 52,
}
REAL_TASK_COUNTS = {
    "COMPLETE_REVIEW": 52,
    "SELECT_POSITION": 52,
    "SELECT_ATTACHMENT": 52,
    "CONFIRM_AI_VALUES": 52,
    "PROVIDE_PLATE": 50,
    "PROVIDE_HOLE_DIAMETER": 52,
    "PROVIDE_LOCATION": 52,
    "REVIEW_SPECIFICATION": 52,
    "REVIEW_VALIDATION": 52,
    "PROVIDE_CONNECTION_IDENTITY": 52,
}
REAL_PAGE_COUNTS = {
    1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 1, 8: 2, 9: 3, 10: 1,
    11: 2, 13: 3, 14: 4, 15: 3, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1,
    21: 1, 22: 1, 23: 1, 24: 1, 25: 1, 26: 1, 27: 4, 28: 2, 29: 1, 30: 1,
    31: 1, 32: 2,
}
CODE_TO_TASK_TYPE = {
    "AUTOMATION_BLOCKER_REVIEW_STATUS": "COMPLETE_REVIEW",
    "AUTOMATION_BLOCKER_PROVENANCE": "CONFIRM_AI_VALUES",
    "AUTOMATION_BLOCKER_POSITION": "SELECT_POSITION",
    "AUTOMATION_BLOCKER_PLATE": "PROVIDE_PLATE",
    "AUTOMATION_BLOCKER_HOLE_DIAMETER": "PROVIDE_HOLE_DIAMETER",
    "AUTOMATION_BLOCKER_LOCATION": "PROVIDE_LOCATION",
    "AUTOMATION_BLOCKER_ATTACHMENT": "SELECT_ATTACHMENT",
    "AUTOMATION_BLOCKER_SPECIFICATION": "REVIEW_SPECIFICATION",
    "AUTOMATION_BLOCKER_VALIDATION": "REVIEW_VALIDATION",
}


def _workload():
    """The genuine chain: accumulated evidence -> intake -> workflow ->
    7AK contract -> 7B2 map."""
    workflow = _workflow_over(_intake(_accumulated()))
    contract = build_project_review_contract(workflow)
    return workflow, contract, build_exception_workload_map(contract)


def _fingerprint(workload_map):
    return json.dumps(dataclasses.asdict(workload_map), sort_keys=True)


def _tamper_item(contract, candidate_id, **changes):
    items = tuple(
        dataclasses.replace(item, **changes)
        if item.package_id == candidate_id else item
        for item in contract.items)
    return dataclasses.replace(contract, items=items)


def _resolve_rp0008(workflow, tmp_path):
    """The genuine VIEW A-A resolution of the real page-5 FL/EA candidate —
    with genuine FL/EA profile builders it advances the workflow truthfully
    to revision 1 and produces a real verified artifact, never fabricating
    one."""
    group = next(g for g in workflow.exception_package.connection_tasks
                 if g.review_package_id == "RP-0008")
    return resolve_project_connection(
        workflow, package_id="RP-0008",
        resolutions=_resolutions_for(
            group, copy.deepcopy(VIEW_AA_ANSWERS_BY_TASK_TYPE),
            "HUMAN-SUPPLIED TEST DATA (7B2 workload map)"),
        output_dir=tmp_path / "rp-0008",
    )


# =============================================================================
# the real 52-candidate workload map
# =============================================================================

@needs_all_captures
class TestReal52CandidateWorkloadMap:

    def test_map_exposes_exactly_the_52_real_candidates(self):
        _, contract, wm = _workload()
        assert wm.project_id == JOURNEY_PROJECT_ID
        assert wm.candidate_count == 52
        # exactly once each, RP-0001..RP-0052, in submission order —
        # never renumbered, merged, deduplicated, reordered or replaced
        assert tuple(row.candidate_id for row in wm.rows) == EXPECTED_IDS
        for candidate_id in EXPECTED_IDS:
            row = workload_candidate(wm, candidate_id)
            assert row.candidate_id == candidate_id
            assert row == wm.rows[EXPECTED_IDS.index(candidate_id)]

    def test_all_52_unresolved_review_none_auto_none_verified(self):
        _, contract, wm = _workload()
        assert wm.decision_counts == (("REVIEW", 52),)
        assert wm.requires_action_count == 52
        assert wm.verification_status_counts == ((None, 52),)
        assert all(row.decision == "REVIEW" for row in wm.rows)
        assert all(row.revision == 0 for row in wm.rows)
        assert all(item.requires_action for item in contract.items)

    def test_no_fabrication_output_was_generated(self):
        _, contract, wm = _workload()
        assert wm.output_status_counts == ((None, 52),)
        assert all(row.output_status is None for row in wm.rows)
        assert all(row.verification_status is None for row in wm.rows)
        # nothing was dispatched or verified for any candidate
        assert all(item.generated_files == () for item in contract.items)

    def test_real_blocker_code_distribution(self):
        _, _, wm = _workload()
        groups = {g.blocker_code: g for g in wm.blocker_code_groups}
        assert {code: groups[code].count for code in groups} == REAL_BLOCKER_COUNTS
        assert set(groups) == set(REAL_BLOCKER_COUNTS)
        for code, group in groups.items():
            # every group member is a real candidate, in submission order
            assert group.candidate_ids == tuple(
                row.candidate_id for row in wm.rows
                if code in row.blocker_codes)
            # one code always means the same resolution task
            assert group.task_type == CODE_TO_TASK_TYPE[code]

    def test_real_task_type_distribution(self):
        _, _, wm = _workload()
        groups = {g.task_type: g for g in wm.task_type_groups}
        assert {task_type: groups[task_type].count for task_type in groups} \
            == REAL_TASK_COUNTS
        # everything is pending except the optional connection identity task
        for task_type, group in groups.items():
            if task_type == "PROVIDE_CONNECTION_IDENTITY":
                assert group.pending_count == 0
                assert group.pending_candidate_ids == ()
            else:
                assert group.pending_count == group.count
                assert group.pending_candidate_ids == group.candidate_ids
            assert set(group.candidate_ids) <= set(EXPECTED_IDS)

    def test_source_page_provenance_is_preserved(self):
        _, contract, wm = _workload()
        assert {g.source_page: g.count for g in wm.page_groups} == REAL_PAGE_COUNTS
        assert sum(g.count for g in wm.page_groups) == 52
        # page 12 recorded no candidates and the map invents none for it;
        # page 26 carries exactly its one genuinely recovered candidate
        assert 12 not in REAL_PAGE_COUNTS
        assert REAL_PAGE_COUNTS[26] == 1
        assert [row.source_page for row in wm.rows].count(26) == 1
        by_id = {item.package_id: item for item in contract.items}
        for row in wm.rows:
            assert row.source_page == by_id[row.candidate_id].evidence.source_page

    def test_source_drawing_identity_is_preserved(self):
        _, contract, wm = _workload()
        assert wm.source_drawing_ids == (JOURNEY_SOURCE_DRAWING_ID,)
        by_id = {item.package_id: item for item in contract.items}
        for row in wm.rows:
            assert row.source_drawing_id == JOURNEY_SOURCE_DRAWING_ID
            assert row.source_drawing_id == \
                by_id[row.candidate_id].evidence.source_drawing_id

    def test_material_distribution_is_real_and_missing_stays_missing(self):
        _, contract, wm = _workload()
        groups = {g.material: g for g in wm.material_groups}
        # 46 recorded "300", 6 genuinely missing — never filled, never renamed
        assert groups["300"].count == 46
        assert groups[None].count == 6
        by_id = {item.package_id: item for item in contract.items}
        expected_present = tuple(
            row.candidate_id for row in wm.rows if row.material == "300")
        expected_missing = tuple(
            row.candidate_id for row in wm.rows if row.material is None)
        assert groups["300"].candidate_ids == expected_present
        assert groups[None].candidate_ids == expected_missing
        assert [item.ai_material for item in contract.items].count("300") == 46
        assert [item.ai_material for item in contract.items].count(None) == 6

    def test_every_available_action_is_the_contracts_own(self):
        _, contract, wm = _workload()
        assert wm.available_action_counts == (("RESOLVE", 52), ("REVIEW", 52))
        for row in wm.rows:
            assert row.available_actions == ("REVIEW", "RESOLVE")
            item = next(i for i in contract.items if i.package_id == row.candidate_id)
            assert tuple(row.available_actions) == tuple(item.available_actions)

    def test_summary_lines_are_derived_from_the_map_alone(self):
        _, _, wm = _workload()
        summary = wm.summary
        assert summary[0] == "52 candidates across 31 pages of SELBY-C1136"
        assert "decision REVIEW: 52" in summary
        assert "blocker AUTOMATION_BLOCKER_PLATE: 50 candidates" in summary
        assert "task PROVIDE_CONNECTION_IDENTITY: 52 candidates (0 pending)" in summary
        assert "material missing: 6 candidates" in summary
        assert summary[-1] == "52 candidates require action"
        # 1 header + 1 decision + 9 blockers + 10 tasks + 31 pages
        # + 2 materials + 1 action line
        assert len(summary) == 55

    def test_every_projected_value_is_verbatim_from_the_contract(self):
        _, contract, wm = _workload()
        by_id = {item.package_id: item for item in contract.items}
        for row in wm.rows:
            item = by_id[row.candidate_id]
            assert row.candidate_id == item.package_id
            assert row.source_page == item.evidence.source_page
            assert row.source_drawing_id == item.evidence.source_drawing_id
            assert row.revision == item.revision
            assert row.decision == item.decision
            assert row.blocker_codes == tuple(b.code for b in item.blockers)
            assert row.task_types == tuple(t.task_type for t in item.tasks)
            assert row.pending_task_types == tuple(
                t.task_type for t in item.tasks if t.required and not t.resolved)
            assert row.material == item.ai_material
            assert row.output_status == item.output_status
            assert row.verification_status == item.verification_status
            assert row.available_actions == tuple(item.available_actions)


# =============================================================================
# determinism and purity
# =============================================================================

@needs_all_captures
class TestDeterminismAndPurity:

    def test_the_same_evidence_maps_to_the_same_map(self):
        first = _fingerprint(_workload()[2])
        assert _fingerprint(_workload()[2]) == first
        assert _fingerprint(_workload()[2]) == first  # a third, independent build

    def test_the_map_build_writes_nothing(self, tmp_path):
        _, contract, wm = _workload()
        assert list(tmp_path.iterdir()) == []
        assert all(item.generated_files == () for item in contract.items)
        assert REAL_PDF.exists()  # the source drawing is untouched


# =============================================================================
# 7B2 negative proofs A-M
# =============================================================================

@needs_all_captures
class TestNegativeProofs7B2:

    def test_a_unknown_candidate_id_is_refused(self):  # [A]
        _, _, wm = _workload()
        for unknown in ("RP-0053", "RP-0000", "RP-FABRICATED"):
            with pytest.raises(ValueError, match="unknown candidate id"):
                workload_candidate(wm, unknown)

    def test_b_duplicate_candidate_is_refused(self):  # [B]
        _, contract, _ = _workload()
        duplicated = dataclasses.replace(
            contract, items=contract.items + (contract.items[0],))
        with pytest.raises(ValueError, match="duplicate candidate id"):
            build_exception_workload_map(duplicated)

    def test_c_candidate_absent_from_the_collection_is_never_resurrected(self):  # [C]
        _, contract, _ = _workload()
        # a contract whose last candidate has vanished: the map projects
        # exactly what it carries and refuses to invent the missing one back
        shortened = dataclasses.replace(contract, items=contract.items[:-1])
        wm = build_exception_workload_map(shortened)
        assert wm.candidate_count == 51
        assert tuple(row.candidate_id for row in wm.rows) == EXPECTED_IDS[:-1]
        with pytest.raises(ValueError, match="unknown candidate id"):
            workload_candidate(wm, "RP-0052")
        # upstream, a candidate dropped from the intake leaves a named orphan
        # — the bijection proof refuses it rather than absorbing the gap
        state = _accumulated()
        intake = _intake(state)
        dropped = _rebuilt_intake(
            intake, candidates=intake.collection.candidates[1:])
        report = ia.verify_candidate_provenance(_combined(state), dropped)
        assert report.verified is False
        assert report.untraced_entry_positions

    def test_d_a_stale_map_stays_visibly_stale(self, tmp_path):  # [D]
        workflow, _, wm = _workload()
        before = _fingerprint(wm)
        assert wm.revision == 0
        # the real workflow advances (genuine VIEW A-A resolution) ...
        advanced = _resolve_rp0008(workflow, tmp_path)
        new_contract = build_project_review_contract(advanced)
        new_wm = build_exception_workload_map(new_contract)
        # ... but the old map is frozen: byte-identical, still revision 0,
        # still showing the old truth for the changed candidate
        assert _fingerprint(wm) == before
        assert wm.revision == 0
        old_row = workload_candidate(wm, "RP-0008")
        assert old_row.revision == 0 and old_row.decision == "REVIEW"
        # staleness is visible, never silently self-corrected
        assert new_wm.revision == 1
        assert new_wm.rows[EXPECTED_IDS.index("RP-0008")] != old_row

    def test_e_mutated_candidate_identity_is_refused(self):  # [E]
        intake = _intake(_accumulated())
        renamed = _rebuilt_intake(intake, candidates=(
            dataclasses.replace(intake.collection.candidates[0],
                                review_package_id="RP-0001-TAMPERED"),
        ) + intake.collection.candidates[1:])
        with pytest.raises(ValueError, match="renamed or renumbered"):
            _workflow_over(renamed)
        reindexed = _rebuilt_intake(intake, candidates=(
            dataclasses.replace(intake.collection.candidates[0], submission_index=4),
        ) + intake.collection.candidates[1:])
        with pytest.raises(ValueError, match="renamed or renumbered"):
            _workflow_over(reindexed)
        # and the map itself never renumbers: only the derived ids exist
        _, _, wm = _workload()
        assert tuple(row.candidate_id for row in wm.rows) == EXPECTED_IDS

    def test_f_mutated_source_page_provenance_is_never_absorbed(self):  # [F]
        state = _accumulated()
        _, contract, _ = _workload()
        # contract layer: a tampered page is projected verbatim — the map
        # never repairs provenance behind anyone's back
        item = contract.items[0]
        tampered = _tamper_item(
            contract, "RP-0001",
            evidence=dataclasses.replace(item.evidence, source_page=12))
        wm = build_exception_workload_map(tampered)
        assert workload_candidate(wm, "RP-0001").source_page == 12
        assert (12, 1, ("RP-0001",)) in [
            (g.source_page, g.count, g.candidate_ids) for g in wm.page_groups]
        # evidence layer: the same tamper is refused outright
        tampered_state = _with_modified_page(state, 1, lambda p: dict(
            p, raw_connections=[dict(p["raw_connections"][0],
                                     grid_reference="TAMPERED")]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered_state)
        # intake layer: an untraced candidate is named, never absorbed
        intake = _intake(state)
        original = intake.collection.candidates[0]
        moved = dataclasses.replace(
            original, package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction, source_page=12)))
        report = ia.verify_candidate_provenance(_combined(state), _rebuilt_intake(
            intake, candidates=(moved,) + intake.collection.candidates[1:]))
        assert report.verified is False
        assert "RP-0001" in report.untraced_candidate_ids

    def test_g_mutated_source_drawing_provenance_is_never_absorbed(self):  # [G]
        state = _accumulated()
        _, contract, _ = _workload()
        item = contract.items[0]
        tampered = _tamper_item(
            contract, "RP-0001",
            evidence=dataclasses.replace(item.evidence,
                                         source_drawing_id="OTHER-DRAWING"))
        wm = build_exception_workload_map(tampered)
        assert workload_candidate(wm, "RP-0001").source_drawing_id == "OTHER-DRAWING"
        assert wm.source_drawing_ids == ("OTHER-DRAWING", JOURNEY_SOURCE_DRAWING_ID)
        intake = _intake(state)
        original = intake.collection.candidates[0]
        moved = dataclasses.replace(
            original, package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction, source_drawing_id="OTHER-DRAWING")))
        report = ia.verify_candidate_provenance(_combined(state), _rebuilt_intake(
            intake, candidates=(moved,) + intake.collection.candidates[1:]))
        assert report.verified is False
        assert report.untraced_candidate_ids or report.multiplicity_mismatches

    def test_h_fabricated_blocker_code_is_refused_or_shown_verbatim(self):  # [H]
        _, contract, _ = _workload()
        item = contract.items[0]
        # (a) a fabricated code pointing at a fabricated task type breaks
        # the code->task pairing and is refused at the map boundary
        blockers = tuple(
            dataclasses.replace(b, code="AUTOMATION_BLOCKER_FABRICATED",
                                task_type="FABRICATED_TASK")
            if b is item.blockers[0] else b for b in item.blockers)
        with pytest.raises(ValueError, match="inconsistent blocker/task pairings"):
            build_exception_workload_map(
                _tamper_item(contract, "RP-0001", blockers=blockers))
        # (b) a self-consistent unknown code is projected verbatim — the map
        # never drops or re-labels an unknown contract value
        blockers = tuple(
            dataclasses.replace(b, code="AUTOMATION_BLOCKER_FABRICATED")
            if b is item.blockers[0] else b for b in item.blockers)
        wm = build_exception_workload_map(
            _tamper_item(contract, "RP-0001", blockers=blockers))
        assert "AUTOMATION_BLOCKER_FABRICATED" in \
            workload_candidate(wm, "RP-0001").blocker_codes
        group = next(g for g in wm.blocker_code_groups
                     if g.blocker_code == "AUTOMATION_BLOCKER_FABRICATED")
        assert group.count == 1 and group.candidate_ids == ("RP-0001",)
        assert group.task_type == "COMPLETE_REVIEW"  # the real task it still maps to

    def test_i_fabricated_task_type_is_refused_or_shown_verbatim(self):  # [I]
        _, contract, _ = _workload()
        item = contract.items[0]
        # (a) a blocker referencing a task type no task carries is refused
        blockers = tuple(
            dataclasses.replace(b, task_type="FABRICATED_TASK")
            if b is item.blockers[0] else b for b in item.blockers)
        with pytest.raises(ValueError, match="inconsistent blocker/task pairings"):
            build_exception_workload_map(
                _tamper_item(contract, "RP-0001", blockers=blockers))
        # (b) a fabricated task type carried by the contract (no blocker
        # references it, exactly like the optional identity task) is
        # projected verbatim, never dropped
        identity_task = next(
            t for t in item.tasks if t.task_type == "PROVIDE_CONNECTION_IDENTITY")
        tasks = tuple(
            dataclasses.replace(t, task_type="FABRICATED_TASK")
            if t is identity_task else t for t in item.tasks)
        wm = build_exception_workload_map(
            _tamper_item(contract, "RP-0001", tasks=tasks))
        assert "FABRICATED_TASK" in workload_candidate(wm, "RP-0001").task_types
        group = next(g for g in wm.task_type_groups
                     if g.task_type == "FABRICATED_TASK")
        assert group.count == 1 and group.pending_count == 0

    def test_j_fabricated_provenance_label_is_neither_consumed_nor_invented(self):  # [J]
        _, contract, wm = _workload()
        genuine = _fingerprint(wm)
        item = contract.items[0]
        provenance = (
            dataclasses.replace(item.provenance[0], provenance="FABRICATED-LABEL"),
        ) + item.provenance[1:]
        tampered = _tamper_item(contract, "RP-0001", provenance=provenance)
        # the map consumes no provenance labels at all: the projected map is
        # byte-identical and invents no provenance vocabulary of its own
        assert _fingerprint(build_exception_workload_map(tampered)) == genuine
        assert "FABRICATED-LABEL" not in _fingerprint(wm)
        # the contract (the provenance source of truth) carries the tampered
        # value untouched — the map neither reads nor alters it
        assert contract.items[0].provenance[0].provenance != "FABRICATED-LABEL"
        # upstream, fabricated content with no source evidence is untraced
        state = _accumulated()
        intake = _intake(state)
        original = intake.collection.candidates[0]
        fabricated = dataclasses.replace(
            original, review_package_id="RP-FABRICATED",
            package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction,
                    detail_reference="FABRICATED-D1", material="400")))
        report = ia.verify_candidate_provenance(_combined(state), _rebuilt_intake(
            intake, candidates=intake.collection.candidates + (fabricated,)))
        assert report.verified is False
        assert "RP-FABRICATED" in report.untraced_candidate_ids

    def test_k_invented_material_value_is_never_corrected(self):  # [K]
        state = _accumulated()
        _, contract, wm = _workload()
        # the map projects the contract's material values exactly — it never
        # "corrects" an invented value and never fills the missing six
        tampered = _tamper_item(contract, "RP-0001", ai_material="400")
        tampered_wm = build_exception_workload_map(tampered)
        groups = {g.material: g for g in tampered_wm.material_groups}
        assert groups["400"].count == 1
        assert groups["400"].candidate_ids == ("RP-0001",)
        assert groups["300"].count == 45
        assert groups[None].count == 6
        assert workload_candidate(wm, "RP-0001").material == "300"
        # upstream, material invention in the evidence is refused outright
        tampered_state = _with_modified_page(state, 1, lambda p: dict(
            p, raw_connections=[dict(p["raw_connections"][0], material="300PLUS")]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered_state)

    def test_l_candidate_disappearing_from_the_collection_is_refused(self):  # [L]
        state = _accumulated()
        intake = _intake(state)
        # intake layer: the dropped candidate's orphaned entry is named
        dropped = _rebuilt_intake(
            intake, candidates=intake.collection.candidates[1:])
        report = ia.verify_candidate_provenance(_combined(state), dropped)
        assert report.verified is False
        assert report.untraced_entry_positions
        # evidence layer: a silently removed page is refused outright
        shortened = dataclasses.replace(
            state, pages=[p for p in copy.deepcopy(state.pages)
                          if p["page_number"] != 12])
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(shortened)

    def test_m_underlying_review_state_change_is_frozen_per_map(self, tmp_path):  # [M]
        workflow, _, wm = _workload()
        before = _fingerprint(wm)
        advanced = _resolve_rp0008(workflow, tmp_path)
        new_wm = build_exception_workload_map(
            build_project_review_contract(advanced))
        # the old map is a point-in-time snapshot: byte-identical
        assert _fingerprint(wm) == before
        # the new map reflects the real project-wide revision counter and
        # exactly the one changed candidate — every untouched candidate is
        # byte-identical apart from that counter, nothing bleeds
        old_rows = {row.candidate_id: row for row in wm.rows}
        new_rows = {row.candidate_id: row for row in new_wm.rows}
        changed = new_rows["RP-0008"]
        assert changed.decision == "AUTO"  # the FL/EA pair genuinely converts
        assert changed.blocker_codes == ()
        assert changed.pending_task_types == ()
        assert changed.output_status == "GENERATED"
        assert changed.verification_status == "VERIFIED"
        for candidate_id in EXPECTED_IDS:
            if candidate_id == "RP-0008":
                assert dataclasses.asdict(changed) != \
                    dataclasses.asdict(old_rows[candidate_id])
            else:
                assert dataclasses.asdict(new_rows[candidate_id]) == {
                    **dataclasses.asdict(old_rows[candidate_id]), "revision": 1}
        assert new_wm.revision == 1
        # the genuine resolution produced exactly one verified PDF, nothing
        # fabricated and no other artifact of any kind
        pdfs = list(tmp_path.glob("**/*.pdf"))
        assert len(pdfs) == 1
        assert pdfs[0].name == "CONN-SELBY-VIEW-AA-fabrication.pdf"
        assert not list(tmp_path.glob("**/*.dxf"))
