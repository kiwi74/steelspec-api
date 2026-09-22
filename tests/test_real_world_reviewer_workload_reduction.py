"""MILESTONE 7B5 — Real-World Reviewer Workload Reduction Proof.

Proves, over the genuine 51-candidate Selby Square workload (7B1-7B4), where
SteelSpec's human reviewer workload can potentially be reduced — WITHOUT the
system inventing engineering answers, weakening provenance, bypassing
validation or making engineering decisions on the reviewer's behalf.

WHAT THIS FILE PROVES:
  - The five workload categories (ASSISTABLE_CANDIDATE /
    HUMAN_ENGINEERING_DECISION / EVIDENCE_LOOKUP / ADMINISTRATIVE /
    NOT_APPLICABLE) are a deterministic, total classification of the 7AC task
    vocabulary: 72 / 153 / 79 / 204 / 0 over the real 508 tasks.
  - Every ASSISTABLE record carries the existing AI observation VERBATIM and a
    fixed proposal text that proposes no value and performs no conversion
    ('22mm holes' stays '22mm holes'; '300' stays an observation).
  - Every HUMAN record carries the fixed three-part human boundary; no
    observation is attached, and any proposal there is flagged as the system
    making the reviewer's decision.
  - The 15 mandated negatives A-O, the 51-candidate regression (7B2/7B4
    distributions unchanged) and RP-0009's genuine AUTO->GENERATED->VERIFIED
    resolution, all unchanged.

WHAT IS NOT PROVEN HERE (and must never be): that any task can be answered
automatically. The categories are workload descriptions only — they are NOT
automation decisions. Nothing in this file or the module under proof calls an
external model or proposes an engineering value.

BRIEF ITEM MAP:
  deterministic classification ... TestDeterministicClassification
  real workload totals ........... TestRealWorkloadBaseline
  section-8 assist records ....... TestAssistableRecords
  section-8 human boundary ....... TestHumanBoundaryRecords
  section-8 evidence records ..... TestEvidenceLookupRecords
  the 15 mandated negatives ...... TestNegativeProofs7B5
  51-candidate regression ........ TestRealWorldRegression
  determinism / purity ........... TestDeterminismAndPurity
"""

import ast
import dataclasses
import os
from pathlib import Path

import pytest

from app.cad_engine.exception_resolution import (
    EXCEPTION_TASK_TYPES,
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_PLATE,
)
from app.cad_engine.exception_workload import build_exception_workload_map
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.reviewer_workload_reduction import (
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_ASSISTABLE,
    CATEGORY_EVIDENCE_LOOKUP,
    CATEGORY_HUMAN_ENGINEERING_DECISION,
    CATEGORY_NOT_APPLICABLE,
    CATEGORIES,
    ReviewerWorkloadBaseline,
    TaskWorkloadRecord,
    build_reviewer_workload_baseline,
    classify_task,
)
from tests.test_real_world_exception_workload import (
    CODE_TO_TASK_TYPE,
    EXPECTED_IDS,
    REAL_BLOCKER_COUNTS,
    REAL_PAGE_COUNTS,
    REAL_TASK_COUNTS,
)
from tests.test_real_world_human_exception_review import (
    MISSING_MATERIAL_IDS,
    RP0009_RESOLVED_PROVENANCE,
    _fresh,
    _item,
    _resolve_full_rp0009,
    _state,
)
from tests.test_real_world_reviewer_evidence import (
    REAL_BLOCKER_LINKS,
    REAL_DETAIL_COUNT,
    REAL_GRID_COUNT,
    REAL_MATERIAL_COUNTS,
    REAL_TOTAL_TASKS,
    _classify,
)

# ---------------------------------------------------------------------------
# Pinned real facts (recorded from the genuine baseline; asserted, never rebuilt)
# ---------------------------------------------------------------------------

CATEGORY_TASK_COUNTS = {
    CATEGORY_ASSISTABLE: 72,
    CATEGORY_HUMAN_ENGINEERING_DECISION: 153,
    CATEGORY_EVIDENCE_LOOKUP: 79,
    CATEGORY_ADMINISTRATIVE: 204,
    CATEGORY_NOT_APPLICABLE: 0,
}
CATEGORY_CANDIDATE_COUNTS = {
    CATEGORY_ASSISTABLE: 51,
    CATEGORY_HUMAN_ENGINEERING_DECISION: 51,
    CATEGORY_EVIDENCE_LOOKUP: 48,
    CATEGORY_ADMINISTRATIVE: 51,
    CATEGORY_NOT_APPLICABLE: 0,
}
# The three candidates with no EVIDENCE_LOOKUP task at all: RP-0032 has no
# plate task (its clean AI plate reading confirmed via CONFIRM) and an
# assistable hole task; RP-0045/0046 have assistable plate AND hole tasks.
NO_EVIDENCE_LOOKUP_IDS = ("RP-0032", "RP-0045", "RP-0046")
EVIDENCE_LOOKUP_IDS = tuple(
    pid for pid in EXPECTED_IDS if pid not in NO_EVIDENCE_LOOKUP_IDS)

TASK_TYPE_ORDER = (
    "COMPLETE_REVIEW", "SELECT_POSITION", "SELECT_ATTACHMENT", "CONFIRM_AI_VALUES",
    "PROVIDE_PLATE", "PROVIDE_HOLE_DIAMETER", "PROVIDE_LOCATION",
    "REVIEW_SPECIFICATION", "REVIEW_VALIDATION", "PROVIDE_CONNECTION_IDENTITY",
)
TASK_TYPE_COUNTS = {task_type: 51 for task_type in TASK_TYPE_ORDER}
TASK_TYPE_COUNTS["PROVIDE_PLATE"] = 49  # RP-0032/RP-0033: clean AI plate readings

BLOCKER_ORDER = (
    "AUTOMATION_BLOCKER_REVIEW_STATUS", "AUTOMATION_BLOCKER_PROVENANCE",
    "AUTOMATION_BLOCKER_POSITION", "AUTOMATION_BLOCKER_PLATE",
    "AUTOMATION_BLOCKER_HOLE_DIAMETER", "AUTOMATION_BLOCKER_LOCATION",
    "AUTOMATION_BLOCKER_ATTACHMENT", "AUTOMATION_BLOCKER_SPECIFICATION",
    "AUTOMATION_BLOCKER_VALIDATION",
)
BLOCKER_COUNTS = {code: 51 for code in BLOCKER_ORDER}
BLOCKER_COUNTS["AUTOMATION_BLOCKER_PLATE"] = 49

# The two candidates whose plate task shows an AI plate reading (assistable).
ASSISTABLE_PLATE_IDS = ("RP-0045", "RP-0046")

# 457 of the 508 tasks are blocker-linked (required); the 51 identity tasks
# are optional. At revision 0 every task is unresolved.
REQUIRED_TASK_COUNT = 457

# The module's fixed proposal texts — verbatim-only; none contains or
# transforms a value (pinned here so any drift fails the milestone).
PROPOSAL_TEXTS = (
    "Show the recorded AI observation verbatim beside the question so the reviewer can "
    "compare it with the source; the system proposes no engineering value and performs "
    "no conversion of the observation.",
    "No safe proposal exists: the workflow records no authoritative value for this field, "
    "and any value a system proposed here would be the system making the reviewer's "
    "engineering decision.",
    "No safe proposal exists: the required value is not captured as a trustworthy "
    "structured observation. The reviewer must locate the value in the source drawing "
    "and enter it explicitly.",
    "None required: the task is workflow/identity bookkeeping; the reviewer acknowledges "
    "it, and the task itself grants nothing.",
)

# The vocabulary mapping, pinned: every 7AC task type, exactly one category
# (the two observation-dependent types flip; tested separately).
VOCABULARY_CATEGORIES = {
    "COMPLETE_REVIEW": CATEGORY_ADMINISTRATIVE,
    "SELECT_MEMBER": CATEGORY_HUMAN_ENGINEERING_DECISION,
    "SELECT_POSITION": CATEGORY_HUMAN_ENGINEERING_DECISION,
    "PROVIDE_PLATE": None,  # flips on the presence of an AI reading
    "PROVIDE_HOLE_DIAMETER": None,  # flips
    "PROVIDE_LOCATION": CATEGORY_HUMAN_ENGINEERING_DECISION,
    "SELECT_ATTACHMENT": CATEGORY_HUMAN_ENGINEERING_DECISION,
    "SELECT_MEMBER_POSITION_ATTACHMENT": CATEGORY_HUMAN_ENGINEERING_DECISION,
    "CONFIRM_AI_VALUES": CATEGORY_ASSISTABLE,
    "REVIEW_MALFORMED_FIELDS": CATEGORY_EVIDENCE_LOOKUP,
    "RESOLVE_CONFLICT": CATEGORY_HUMAN_ENGINEERING_DECISION,
    "REVIEW_SPECIFICATION": CATEGORY_ADMINISTRATIVE,
    "REVIEW_VALIDATION": CATEGORY_ADMINISTRATIVE,
    "CONFIRM_AUTOMATION": CATEGORY_NOT_APPLICABLE,
    "PROVIDE_CONNECTION_IDENTITY": CATEGORY_ADMINISTRATIVE,
    "PROVIDE_MATERIAL_SPECIFICATION": CATEGORY_EVIDENCE_LOOKUP,
}


def _baseline(workflow=None):
    return build_reviewer_workload_baseline(
        build_project_review_contract(_fresh() if workflow is None else workflow))


def _record(baseline, package_id):
    return next(r for r in baseline.candidates if r.package_id == package_id)


# =============================================================================
# The deterministic classification.
# =============================================================================

class TestDeterministicClassification:
    """The five categories form a total, deterministic table over the 7AC
    vocabulary; anything outside it is refused."""

    def test_vocabulary_is_total_and_pinned(self):
        assert len(EXCEPTION_TASK_TYPES) == 16
        observed = {}
        for task_type in EXCEPTION_TASK_TYPES:
            category = classify_task(task_type, None)
            assert category in CATEGORIES
            observed[task_type] = (
                None if task_type in ("PROVIDE_PLATE", "PROVIDE_HOLE_DIAMETER")
                else category)
        assert observed == VOCABULARY_CATEGORIES

    def test_observation_flips_plate_and_holes(self):
        assert classify_task("PROVIDE_PLATE", None) == CATEGORY_EVIDENCE_LOOKUP
        assert classify_task("PROVIDE_PLATE", "reading") == CATEGORY_ASSISTABLE
        assert classify_task("PROVIDE_HOLE_DIAMETER", None) == CATEGORY_EVIDENCE_LOOKUP
        assert classify_task("PROVIDE_HOLE_DIAMETER", "reading") == CATEGORY_ASSISTABLE

    def test_unknown_task_types_are_refused(self):
        for value in (None, "some observation"):
            with pytest.raises(ValueError, match="not part of the exception-resolution contract"):
                classify_task("FORGED_TASK_TYPE", value)

    def test_classification_returns_only_the_five_categories(self):
        for task_type in EXCEPTION_TASK_TYPES:
            for value in (None, "observation"):
                assert classify_task(task_type, value) in CATEGORIES


# =============================================================================
# The real 51-candidate workload totals.
# =============================================================================

class TestRealWorkloadBaseline:
    """Every aggregate reconciles to the genuine 51-candidate workflow."""

    def test_totals(self):
        baseline = _baseline()
        assert baseline.project_id == "PROJ-7AV-SELBY"
        assert baseline.revision == 0
        assert baseline.candidate_count == 51
        assert baseline.task_count == REAL_TOTAL_TASKS == 508
        assert baseline.pending_task_count == REAL_TOTAL_TASKS  # all open at rev 0

    def test_category_counts(self):
        baseline = _baseline()
        assert {c.category: c.task_count for c in baseline.categories} == \
            CATEGORY_TASK_COUNTS
        assert {c.category: len(c.candidate_ids) for c in baseline.categories} == \
            CATEGORY_CANDIDATE_COUNTS
        assert [c.category for c in baseline.categories] == list(CATEGORIES)
        # The five categories account for every task exactly once.
        assert sum(c.task_count for c in baseline.categories) == 508

    def test_evidence_lookup_candidate_ids(self):
        category = next(c for c in _baseline().categories
                        if c.category == CATEGORY_EVIDENCE_LOOKUP)
        assert category.candidate_ids == EVIDENCE_LOOKUP_IDS
        assert len(category.candidate_ids) == 48
        for pid in NO_EVIDENCE_LOOKUP_IDS:
            assert pid not in category.candidate_ids

    def test_task_type_counts_and_order(self):
        baseline = _baseline()
        assert [t.task_type for t in baseline.task_types] == list(TASK_TYPE_ORDER)
        assert {t.task_type: t.task_count for t in baseline.task_types} == \
            TASK_TYPE_COUNTS
        plate = next(t for t in baseline.task_types if t.task_type == "PROVIDE_PLATE")
        assert plate.candidate_ids == tuple(pid for pid in EXPECTED_IDS
                                            if pid not in ("RP-0032", "RP-0033"))

    def test_blocker_counts_and_order(self):
        baseline = _baseline()
        assert [b.blocker_code for b in baseline.blockers] == list(BLOCKER_ORDER)
        assert {b.blocker_code: b.candidate_count for b in baseline.blockers} == \
            BLOCKER_COUNTS
        assert sum(b.candidate_count for b in baseline.blockers) == REAL_BLOCKER_LINKS

    def test_derived_candidate_sets(self):
        baseline = _baseline()
        assert baseline.candidates_requiring_source_inspection == EXPECTED_IDS
        assert baseline.candidates_requiring_engineering_judgement == EXPECTED_IDS
        assert baseline.candidates_with_bounded_confirmation == EXPECTED_IDS
        assert baseline.candidates_without_safe_assistance == ()
        assert baseline.candidates_with_relevant_ai_observation == EXPECTED_IDS

    def test_required_and_pending_at_revision_zero(self):
        baseline = _baseline()
        required = sum(1 for r in baseline.candidates for t in r.tasks if t.required)
        assert required == REQUIRED_TASK_COUNT
        assert all(not t.resolved for r in baseline.candidates for t in r.tasks)
        assert all(r.decision == "REVIEW" and r.revision == 0
                   for r in baseline.candidates)

    def test_submission_order_preserved(self):
        baseline = _baseline()
        assert [r.package_id for r in baseline.candidates] == list(EXPECTED_IDS)
        for category in baseline.categories:
            if category.task_count == 0:
                assert category.candidate_ids == () and category.task_ids == ()
                continue
            # Candidate ids appear in submission order, each at most once.
            assert category.candidate_ids == tuple(
                pid for pid in EXPECTED_IDS if pid in set(category.candidate_ids))

    def test_per_candidate_tasks_reconcile_to_the_contract(self):
        contract = build_project_review_contract(_fresh())
        baseline = _baseline()
        for item, record in zip(contract.items, baseline.candidates):
            assert item.package_id == record.package_id
            assert len(record.tasks) == len(item.tasks)
            assert record.blocker_codes == tuple(b.code for b in item.blockers)


# =============================================================================
# Section 8 — the assist/decide boundary, recorded per task.
# =============================================================================

class TestAssistableRecords:
    """Every ASSISTABLE record: the AI observation verbatim, a fixed
    verbatim-only proposal, no converted value anywhere."""

    def test_assistable_composition(self):
        baseline = _baseline()
        by_type = {}
        for record in baseline.candidates:
            for task in record.tasks:
                if task.category == CATEGORY_ASSISTABLE:
                    by_type[task.task_type] = by_type.get(task.task_type, 0) + 1
        assert by_type == {
            TASK_CONFIRM_AI_VALUES: 51, TASK_PROVIDE_PLATE: 2,
            TASK_PROVIDE_HOLE_DIAMETER: 19,
        }

    def test_every_assistable_record_carries_the_observation_verbatim(self):
        contract = build_project_review_contract(_fresh())
        baseline = _baseline()
        for item, record in zip(contract.items, baseline.candidates):
            for ctask, rtask in zip(item.tasks, record.tasks):
                assert rtask.current_value == ctask.current_value  # byte-verbatim
        assert all(
            task.current_value is not None
            for record in baseline.candidates for task in record.tasks
            if task.category == CATEGORY_ASSISTABLE
        )

    def test_proposals_are_fixed_verbatim_only_texts(self):
        baseline = _baseline()
        for record in baseline.candidates:
            for task in record.tasks:
                assert task.proposal in PROPOSAL_TEXTS
                assert "Ø" not in task.proposal
                assert "diameter_mm" not in task.proposal
                assert "300" not in task.proposal
        # The hole observations stay the AI's own wording; no record carries a
        # derived diameter.
        for record in baseline.candidates:
            for task in record.tasks:
                if task.task_type == TASK_PROVIDE_HOLE_DIAMETER and \
                        task.current_value is not None:
                    assert "diameter_mm" not in task.current_value

    def test_assistable_plate_records(self):
        baseline = _baseline()
        for package_id in ASSISTABLE_PLATE_IDS:
            record = _record(baseline, package_id)
            task = next(t for t in record.tasks if t.task_type == TASK_PROVIDE_PLATE)
            assert task.category == CATEGORY_ASSISTABLE
            assert task.current_value.startswith("[{")  # the AI's plate dicts, verbatim
            # Truthful pin: the contract carries no 'plate' provenance entry
            # for these two — their reading is shown by the task itself.
            assert task.related_provenance == ()

    def test_assistable_hole_records_carry_no_hole_provenance(self):
        # The AI bolt readings carry no provenance entries at revision 0
        # (the pinned 99 = 51 marks + 46 material + 2 plate) — the records
        # show none rather than inventing one.
        baseline = _baseline()
        holes = [t for r in baseline.candidates for t in r.tasks
                 if t.task_type == TASK_PROVIDE_HOLE_DIAMETER
                 and t.category == CATEGORY_ASSISTABLE]
        assert len(holes) == 19
        assert all(t.related_provenance == () for t in holes)

    def test_confirm_related_provenance_is_the_confirmed_fields(self):
        baseline = _baseline()
        for package_id in ("RP-0009", "RP-0001"):
            record = _record(baseline, package_id)
            confirm = next(t for t in record.tasks
                           if t.task_type == TASK_CONFIRM_AI_VALUES)
            assert {(p.field, p.provenance) for p in confirm.related_provenance} == {
                ("connected_member_marks", "AI_EXTRACTED"),
                ("material", "AI_EXTRACTED"),
            }
        record = _record(baseline, "RP-0033")
        confirm = next(t for t in record.tasks if t.task_type == TASK_CONFIRM_AI_VALUES)
        assert {(p.field, p.provenance) for p in confirm.related_provenance} == {
            ("connected_member_marks", "AI_EXTRACTED"),
            ("plate", "AI_EXTRACTED"),
        }

    def test_assistable_never_auto_accepted(self):
        # The records carry no acceptance/answer field of any kind: the
        # category never becomes a decision.
        assert {f.name for f in dataclasses.fields(TaskWorkloadRecord)} == {
            "task_id", "task_type", "category", "required", "resolved",
            "current_value", "field", "evidence_text", "related_provenance",
            "human_boundary", "proposal", "reviewer_remaining",
            "proposal_risks_engineering_decision",
        }


class TestHumanBoundaryRecords:
    """Every HUMAN_ENGINEERING_DECISION record: the fixed three-part boundary,
    no observation attached, any proposal flagged as the system deciding."""

    def test_three_human_types_each_51(self):
        baseline = _baseline()
        by_type = {}
        for record in baseline.candidates:
            for task in record.tasks:
                if task.category == CATEGORY_HUMAN_ENGINEERING_DECISION:
                    by_type[task.task_type] = by_type.get(task.task_type, 0) + 1
        assert by_type == {
            "SELECT_POSITION": 51, "SELECT_ATTACHMENT": 51, "PROVIDE_LOCATION": 51,
        }

    def test_human_records_carry_no_observation_and_a_flagged_proposal(self):
        baseline = _baseline()
        for record in baseline.candidates:
            for task in record.tasks:
                if task.category != CATEGORY_HUMAN_ENGINEERING_DECISION:
                    continue
                assert task.current_value is None
                assert task.human_boundary is not None
                assert task.proposal_risks_engineering_decision is True
                assert task.proposal == PROPOSAL_TEXTS[1]

    def test_boundary_texts_are_pinned_and_uniform(self):
        baseline = _baseline()
        expected = {
            "SELECT_POSITION": ("position value", "START/END position", "position on the member"),
            "SELECT_ATTACHMENT": ("defaulting a surface", "attachment surface/reference", "per member"),
            "PROVIDE_LOCATION": ("deriving coordinates", "x/y/z coordinates", "project space"),
        }
        for task_type, fragments in expected.items():
            records = [t for r in baseline.candidates for t in r.tasks
                       if t.task_type == task_type]
            assert len(records) == 51
            first = records[0].human_boundary
            assert all(t.human_boundary == first for t in records)  # uniform
            assert fragments[0] in first.why_human
            assert fragments[1] in first.missing_information
            assert fragments[2] in first.judgement_constitutes
            assert all(part for part in (
                first.why_human, first.missing_information, first.judgement_constitutes))

    def test_vocabulary_human_types_classify_human(self):
        for task_type in ("SELECT_MEMBER", "SELECT_MEMBER_POSITION_ATTACHMENT",
                          "RESOLVE_CONFLICT"):
            assert classify_task(task_type, None) == CATEGORY_HUMAN_ENGINEERING_DECISION


class TestEvidenceLookupRecords:
    """Every EVIDENCE_LOOKUP record: no observation, the fixed no-safe-proposal
    text, and a real source reference to navigate by."""

    def test_evidence_composition(self):
        baseline = _baseline()
        by_type = {}
        for record in baseline.candidates:
            for task in record.tasks:
                if task.category == CATEGORY_EVIDENCE_LOOKUP:
                    by_type[task.task_type] = by_type.get(task.task_type, 0) + 1
        assert by_type == {TASK_PROVIDE_PLATE: 47, TASK_PROVIDE_HOLE_DIAMETER: 32}

    def test_evidence_records_carry_no_observation(self):
        baseline = _baseline()
        for record in baseline.candidates:
            for task in record.tasks:
                if task.category == CATEGORY_EVIDENCE_LOOKUP:
                    assert task.current_value is None
                    assert task.proposal == PROPOSAL_TEXTS[2]
                    assert task.proposal_risks_engineering_decision is True

    def test_every_evidence_record_has_a_source_reference(self):
        baseline = _baseline()
        for record in baseline.candidates:
            for task in record.tasks:
                if task.category == CATEGORY_EVIDENCE_LOOKUP:
                    assert "SELBY-C1136" in task.evidence_text
                    assert "page" in task.evidence_text


# =============================================================================
# The 15 mandated negatives.
# =============================================================================

class TestNegativeProofs7B5:
    """A-O, each against the genuine boundary; none weakens a validation."""

    def test_a_no_candidate_can_disappear(self):
        contract = build_project_review_contract(_fresh())
        baseline = _baseline()
        assert [r.package_id for r in baseline.candidates] == \
            [i.package_id for i in contract.items]
        assert len(baseline.candidates) == 51

    def test_b_no_candidate_can_appear_twice(self):
        baseline = _baseline()
        ids = [r.package_id for r in baseline.candidates]
        assert len(ids) == len(set(ids)) == 51
        for category in baseline.categories:
            assert len(category.candidate_ids) == len(set(category.candidate_ids))
            assert len(category.task_ids) == len(set(category.task_ids)) == \
                category.task_count

    def test_c_task_counts_reconcile_exactly(self):
        contract = build_project_review_contract(_fresh())
        baseline = _baseline()
        assert sum(len(i.tasks) for i in contract.items) == REAL_TOTAL_TASKS
        assert baseline.task_count == REAL_TOTAL_TASKS
        assert sum(c.task_count for c in baseline.categories) == REAL_TOTAL_TASKS
        assert sum(t.task_count for t in baseline.task_types) == REAL_TOTAL_TASKS
        assert baseline.pending_task_count == REAL_TOTAL_TASKS
        for item, record in zip(contract.items, baseline.candidates):
            assert len(record.tasks) == len(item.tasks)

    def test_d_unknown_task_types_are_refused(self):
        for value in (None, "some observation"):
            with pytest.raises(ValueError, match="not part of the exception-resolution contract"):
                classify_task("FORGED_TASK_TYPE", value)

    def test_e_ai_observations_are_never_converted_into_answers(self):
        contract = build_project_review_contract(_fresh())
        baseline = _baseline()
        for item, record in zip(contract.items, baseline.candidates):
            for ctask, rtask in zip(item.tasks, record.tasks):
                assert rtask.current_value == ctask.current_value  # byte-verbatim
        for record in baseline.candidates:
            for task in record.tasks:
                assert task.proposal in PROPOSAL_TEXTS
                assert "Ø" not in task.proposal
                assert "diameter_mm" not in task.proposal
        # The classifier uses the observation only to pick between two
        # categories — it never returns or transforms it.
        for task_type in ("PROVIDE_PLATE", "PROVIDE_HOLE_DIAMETER"):
            assert classify_task(task_type, "((4, '22mm holes', None, ()),)") == \
                CATEGORY_ASSISTABLE

    def test_f_provenance_is_never_changed_by_the_analysis(self):
        workflow = _fresh()
        before = build_project_review_contract(workflow)
        prov_before = tuple(
            (i.package_id, tuple((p.field, p.provenance) for p in i.provenance))
            for i in before.items)
        _baseline(workflow)
        after = build_project_review_contract(workflow)
        prov_after = tuple(
            (i.package_id, tuple((p.field, p.provenance) for p in i.provenance))
            for i in after.items)
        assert prov_after == prov_before
        labels = {p.provenance for r in _baseline(workflow).candidates
                  for t in r.tasks for p in t.related_provenance}
        assert labels == {"AI_EXTRACTED"}  # rev 0: no human label exists yet

    def test_g_missing_material_remains_missing(self):
        baseline = _baseline()
        for package_id in MISSING_MATERIAL_IDS:
            record = _record(baseline, package_id)
            assert "material" not in record.ai_observation_fields
            confirm = next(t for t in record.tasks
                           if t.task_type == TASK_CONFIRM_AI_VALUES)
            assert "'material'" not in confirm.current_value
            assert not any(p.field == "material" for p in confirm.related_provenance)
        assert _item(_fresh(), "RP-0033").ai_material is None
        assert sum(1 for r in baseline.candidates
                   if "material" in r.ai_observation_fields) == 46

    def test_h_missing_source_evidence_is_never_fabricated(self):
        contract = build_project_review_contract(_fresh())
        baseline = _baseline()
        for item, record in zip(contract.items, baseline.candidates):
            assert record.has_source_page == (item.evidence.source_page is not None)
            assert record.has_detail_reference == (item.evidence.detail_reference is not None)
            assert record.has_grid_reference == (item.evidence.grid_reference is not None)
            assert len({t.evidence_text for t in record.tasks}) == 1
            assert "SELBY-C1136" in record.tasks[0].evidence_text
            assert f"page {item.evidence.source_page}" in record.tasks[0].evidence_text
        assert all(r.has_source_page for r in baseline.candidates)
        assert sum(1 for r in baseline.candidates if r.has_detail_reference) == \
            REAL_DETAIL_COUNT
        assert sum(1 for r in baseline.candidates if r.has_grid_reference) == \
            REAL_GRID_COUNT

    def test_i_source_identity_cannot_change_through_the_analysis(self):
        workflow = _fresh()
        before = dataclasses.asdict(build_project_review_contract(workflow))
        _baseline(workflow)
        after = dataclasses.asdict(build_project_review_contract(workflow))
        assert after == before
        record = _record(_baseline(workflow), "RP-0009")
        assert record.tasks[0].evidence_text == \
            "source drawing SELBY-C1136; drawing number 001; page 5; detail VIEW B-B"

    def test_j_analysis_cannot_modify_workflow_revision(self, tmp_path):
        workflow = _fresh()
        assert workflow.revision == 0
        assert _baseline(workflow).revision == 0
        assert workflow.revision == 0
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        assert advanced.revision == 1
        assert _baseline(advanced).revision == 1
        assert advanced.revision == 1

    def test_k_analysis_cannot_generate_a_fabrication_artifact(self):
        before = set(os.listdir("."))
        for _ in range(2):
            _baseline(_fresh())
        assert set(os.listdir(".")) == before
        for directory in (Path("app"), Path("tests")):
            assert not [p for p in directory.rglob("*.pdf")], directory

    def test_l_analysis_cannot_change_gate_decisions(self, tmp_path):
        workflow = _fresh()
        before = _baseline(workflow)
        assert all(r.decision == "REVIEW" for r in before.candidates)
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        after = _baseline(advanced)
        # The genuine 7Z/7AD/7AE/7AF/7AG chain is untouched and decides:
        state = _state(advanced, "RP-0009")
        assert (state.decision, state.output_status, state.verification_status) == \
            ("AUTO", "GENERATED", "VERIFIED")
        # The baseline mirrors that truth — and only that truth.
        assert _record(after, "RP-0009").decision == "AUTO"
        assert _record(before, "RP-0009").decision == "REVIEW"
        # An untouched candidate: same record, only the real project-wide
        # revision counter advanced; its 7AC tasks are all still open.
        expected = {**dataclasses.asdict(_record(before, "RP-0001")), "revision": 1}
        assert dataclasses.asdict(_record(after, "RP-0001")) == expected
        assert all(not t.resolved for t in _record(after, "RP-0001").tasks)

    def test_m_no_ranking_scoring_or_prioritisation(self):
        source = Path("app/cad_engine/reviewer_workload_reduction.py").read_text()
        lowered = source.lower()
        for forbidden in ("rank", "score", "priorit", "difficul", " easy", " hard",
                          "weight", "sorted("):
            assert forbidden not in lowered, forbidden
        baseline = _baseline()
        assert [r.package_id for r in baseline.candidates] == list(EXPECTED_IDS)
        for category in baseline.categories:
            assert category.candidate_ids == tuple(
                pid for pid in EXPECTED_IDS if pid in set(category.candidate_ids))
        # No ordering field exists anywhere on the records.
        for dataclass in (ReviewerWorkloadBaseline, TaskWorkloadRecord):
            for field in dataclasses.fields(dataclass):
                assert "order" not in field.name
                assert "rank" not in field.name
                assert "score" not in field.name
                assert "weight" not in field.name

    def test_n_distribution_identical_to_the_7b2_workload_map(self):
        workflow = _fresh()
        contract = build_project_review_contract(workflow)
        workload = build_exception_workload_map(contract)
        assert {g.blocker_code: g.count for g in workload.blocker_code_groups} == \
            REAL_BLOCKER_COUNTS
        assert {g.task_type: g.count for g in workload.task_type_groups} == \
            REAL_TASK_COUNTS
        assert {g.source_page: g.count for g in workload.page_groups} == \
            REAL_PAGE_COUNTS
        assert all(g.task_type == CODE_TO_TASK_TYPE[g.blocker_code]
                   for g in workload.blocker_code_groups)
        baseline = _baseline(workflow)
        assert {b.blocker_code: b.candidate_count for b in baseline.blockers} == \
            REAL_BLOCKER_COUNTS
        assert {t.task_type: t.task_count for t in baseline.task_types} == \
            REAL_TASK_COUNTS

    def test_o_a_stale_analysis_cannot_mutate(self, tmp_path):
        old = _baseline(_fresh())
        snapshot = dataclasses.asdict(old)
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        new = _baseline(advanced)
        assert _record(old, "RP-0009").decision == "REVIEW"
        assert old.revision == 0
        assert dataclasses.asdict(old) == snapshot
        assert _record(new, "RP-0009").decision == "AUTO"
        assert new.revision == 1
        assert new.pending_task_count == 498  # RP-0009's 10 tasks now resolved
        with pytest.raises(dataclasses.FrozenInstanceError):
            old.candidates[0].decision = "AUTO"


# =============================================================================
# The real 51-candidate regression.
# =============================================================================

class TestRealWorldRegression:
    """The queue stays exactly as 7B1-7B4 pinned it; RP-0009's genuine
    resolution is unchanged."""

    def test_51_candidates_review_revision_zero(self):
        workflow = _fresh()
        contract = build_project_review_contract(workflow)
        baseline = _baseline(workflow)
        assert [i.package_id for i in contract.items] == list(EXPECTED_IDS)
        assert all(i.decision == "REVIEW" for i in contract.items)
        assert all(i.requires_action for i in contract.items)
        assert all(i.output_status is None for i in contract.items)
        assert all(i.verification_status is None for i in contract.items)
        assert workflow.generated_files == ()
        assert all(r.decision == "REVIEW" and r.revision == 0
                   for r in baseline.candidates)

    def test_selby_c1136_everywhere_and_page_26_zero(self):
        contract = build_project_review_contract(_fresh())
        assert all(i.evidence.source_drawing_id == "SELBY-C1136"
                   for i in contract.items)
        workload = build_exception_workload_map(contract)
        assert 26 not in {g.source_page for g in workload.page_groups}

    def test_material_distribution_unchanged(self):
        contract = build_project_review_contract(_fresh())
        counts = {}
        for item in contract.items:
            counts[item.ai_material] = counts.get(item.ai_material, 0) + 1
        assert counts == REAL_MATERIAL_COUNTS
        workload = build_exception_workload_map(contract)
        assert {g.material: g.count for g in workload.material_groups} == \
            REAL_MATERIAL_COUNTS

    def test_7b4_evidence_distribution_unchanged(self):
        items = build_project_review_contract(_fresh()).items
        assert sum(1 for i in items if i.evidence.detail_reference) == REAL_DETAIL_COUNT
        assert sum(1 for i in items if i.evidence.grid_reference) == REAL_GRID_COUNT
        assert sum(1 for i in items if i.ai_member_references) == 51
        assert sum(1 for i in items if i.ai_bolt_readings) == 19
        assert sum(1 for i in items if i.ai_plate_readings) == 4
        assert sum(1 for i in items if i.ai_weld_readings) == 51
        assert sum(1 for i in items if i.ai_connection_type is not None) == 51
        assert sum(1 for i in items if i.ai_confidence is not None) == 51
        assert sum(1 for i in items if i.ai_material is not None) == 46
        assert sum(len(i.blockers) for i in items) == REAL_BLOCKER_LINKS
        assert sum(len(i.tasks) for i in items) == REAL_TOTAL_TASKS
        # 7B4's own classification matrix is unchanged too.
        counts = {}
        for item in items:
            for task in item.tasks:
                counts[_classify(item, task)] = counts.get(_classify(item, task), 0) + 1
        assert counts == {
            "AI_OBSERVATION_PRESENT": 72,
            "SOURCE_REFERENCE_ONLY": 232,
            "NOT_APPLICABLE": 204,
        }

    def test_rp0009_genuine_resolution_unchanged(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        state = _state(advanced, "RP-0009")
        assert (state.decision, state.output_status, state.verification_status) == \
            ("AUTO", "GENERATED", "VERIFIED")
        item = _item(advanced, "RP-0009")
        assert {(p.field, p.provenance) for p in item.provenance} == \
            RP0009_RESOLVED_PROVENANCE
        baseline = _baseline(advanced)
        record = _record(baseline, "RP-0009")
        assert record.decision == "AUTO"
        assert all(t.resolved for t in record.tasks)


# =============================================================================
# Determinism and purity.
# =============================================================================

class TestDeterminismAndPurity:
    def test_repeated_builds_are_identical(self):
        assert _baseline(_fresh()) == _baseline(_fresh())

    def test_records_are_frozen(self):
        baseline = _baseline(_fresh())
        with pytest.raises(dataclasses.FrozenInstanceError):
            baseline.candidates[0].decision = "AUTO"
        with pytest.raises(dataclasses.FrozenInstanceError):
            baseline.candidates[0].tasks[0].category = "FORGED"
        with pytest.raises(dataclasses.FrozenInstanceError):
            baseline.categories[0].task_count = 0

    def test_module_is_pure_and_read_only(self):
        source = Path("app/cad_engine/reviewer_workload_reduction.py").read_text()
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert set(imported) <= {
            "dataclasses",
            "app.cad_engine.exception_resolution",
            "app.cad_engine.review_contract",
        }
        for forbidden in ("sorted(", "open(", "os.", "sys.", "pathlib", "random",
                          "http", "requests", "time.sleep"):
            assert forbidden not in source, forbidden

    def test_building_the_baseline_writes_nothing(self):
        before = set(os.listdir("."))
        _baseline(_fresh())
        _baseline(_fresh())
        assert set(os.listdir(".")) == before

    def test_baseline_consumes_only_the_contract(self):
        with pytest.raises(TypeError, match="must be a ProjectReviewContract"):
            build_reviewer_workload_baseline({"not": "a contract"})
