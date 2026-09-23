"""MILESTONE 7B6 — Real-World Assisted Review Execution Proof.

Proves, over the genuine 52-candidate Selby Square workload, that the
workload-reduction opportunities 7B5 identified can actually be executed
through the existing review workflow — with the exact human engineering
boundary preserved and outcomes equivalent to genuine unassisted review.

WHAT THIS FILE PROVES:
  - The assistance layer (app/cad_engine/reviewer_assistance.py) is a pure,
    frozen, deterministic, read-only view over the 7AK project contract,
    classified by the committed 7B5 baseline through its public API.
  - Every AI observation is carried byte-verbatim ("22mm holes" stays
    "22mm holes"; "300" stays an observation; missing material stays
    missing). No conversion happens anywhere in the assistance texts.
  - The human boundary: 156 HUMAN tasks carry no value and the fixed
    boundary; 81 EVIDENCE_LOOKUP tasks carry no value, only the source
    anchor; the 73 ASSISTABLE tasks display the observation and demand an
    explicit reviewer act — the assistance layer has no resolution method
    and never confirms anything.
  - The existing 7AU provenance semantics execute unchanged: confirming a
    field keeps the AI value and produces HUMAN_REVIEWED (RP-0032 plate);
    supplying produces HUMAN_SUPPLEMENTED (RP-0008 holes, RP-0033
    material); no new provenance vocabulary.
  - Four genuine Selby cases run through the existing public resolution
    path (resolve_project_connection), and an assisted full resolution of
    RP-0009 is EQUIVALENT to the unassisted 7B3 resolution in every
    engineering/workflow field (only the artifact file paths differ, as
    they point at different output directories; both artifacts have the
    pinned genuine size).

NOTE ON PDF BYTES: the genuine drawing generator embeds the current date,
so two independently generated PDFs differ in bytes. Equivalence is proven
on every engineering/workflow field plus the pinned artifact size — never
by asserting byte-identity of regenerated artifacts.

BRIEF ITEM MAP:
  determinism / purity ......... TestAssistanceDeterminism
  verbatim observations ........ TestVerbatimObservations
  human boundary ............... TestHumanBoundary
  7AU provenance semantics ..... TestProvenanceSemantics
  real Selby execution ......... TestRealWorldExecution
  assisted == unassisted ....... TestEquivalence
  refusal / no-op negatives .... TestNegativeProofs
  52-candidate regression ...... TestRegression
"""

import ast
import dataclasses
import os
from pathlib import Path

import pytest

from app.cad_engine.exception_resolution import (
    HumanResolution,
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
)
from app.cad_engine.project_workflow import (
    StaleProjectWorkflowError,
    resolve_project_connection,
)
from app.cad_engine.review_contract import build_project_review_contract
from app.cad_engine.reviewer_assistance import (
    AssistCandidate,
    AssistTask,
    ConfirmationSemantics,
    ReviewerAssistance,
    build_reviewer_assistance,
    confirmation_semantics,
)
from app.cad_engine.reviewer_workload_reduction import (
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_ASSISTABLE,
    CATEGORY_EVIDENCE_LOOKUP,
    CATEGORY_HUMAN_ENGINEERING_DECISION,
    CATEGORY_NOT_APPLICABLE,
    build_reviewer_workload_baseline,
)
from tests.test_real_world_exception_workload import EXPECTED_IDS
from tests.test_real_world_human_exception_review import (
    MISSING_MATERIAL_IDS,
    RP0009_PDF_NAME,
    RP0009_PDF_SIZE,
    RP0009_RESOLVED_PROVENANCE,
    _fresh,
    _group,
    _item,
    _material_workflow,
    _resolve_full_rp0009,
    _state,
)
from tests.test_real_world_reviewer_workload_reduction import (
    CATEGORY_TASK_COUNTS,
    PROPOSAL_TEXTS,
)

# Every human answer in this file carries this evidence note: the values are
# TEST-SUPPLIED, standing in for a human reviewer — never machine-derived and
# never taken from the assistance layer.
EVIDENCE_7B6 = (
    "HUMAN-SUPPLIED TEST DATA (7B6 — a human reviewer reading drawing "
    "SELBY-C1136; the answers below were supplied by the test on the human "
    "reviewer's behalf, assisted only by verbatim displays, never by the "
    "assistance layer)."
)

# Pinned real facts (recorded from the genuine workflow; asserted, never rebuilt)
RP0008_HOLE_READING = "((4, '22mm holes', None, ()),)"
RP0009_HOLE_READING = "((2, '18mm holes', None, ()),)"
RP0008_HOLE_ANSWER = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 100.0}
RP0033_MATERIAL_ANSWER = "300PLUS"
RP0033_MATERIAL_PAGE = "page 18"


def _assistance(workflow=None):
    return build_reviewer_assistance(
        build_project_review_contract(_fresh() if workflow is None else workflow))


def _candidate(assistance, package_id):
    return next(c for c in assistance.candidates if c.package_id == package_id)


def _task(assistance, package_id, task_type):
    return next(t for t in _candidate(assistance, package_id).tasks
                if t.task_type == task_type)


def _snapshot(workflow, output_dir):
    """The full project contract, with one output directory scrubbed so two
    runs into different directories compare byte-equal on everything real."""
    def scrub(value):
        if isinstance(value, str):
            return value.replace(str(output_dir), "<OUTPUT_DIR>")
        if isinstance(value, tuple):
            return tuple(scrub(v) for v in value)
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, dict):
            return {key: scrub(v) for key, v in value.items()}
        return value
    return scrub(dataclasses.asdict(build_project_review_contract(workflow)))


# =============================================================================
# Determinism and purity.
# =============================================================================

class TestAssistanceDeterminism:
    def test_repeated_builds_are_identical(self):
        assert _assistance(_fresh()) == _assistance(_fresh())

    def test_records_are_frozen(self):
        assistance = _assistance(_fresh())
        with pytest.raises(dataclasses.FrozenInstanceError):
            assistance.candidates[0].tasks[0].category = "FORGED"
        with pytest.raises(dataclasses.FrozenInstanceError):
            assistance.candidates[0].decision = "AUTO"
        with pytest.raises(dataclasses.FrozenInstanceError):
            confirmation_semantics(CATEGORY_ASSISTABLE).reviewer_action = "changed"

    def test_module_is_pure_and_has_no_resolution_surface(self):
        source = Path("app/cad_engine/reviewer_assistance.py").read_text()
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert set(imported) <= {
            "dataclasses",
            "app.cad_engine.review_contract",
            "app.cad_engine.reviewer_workload_reduction",
        }
        for forbidden in ("sorted(", "open(", "os.", "sys.", "pathlib", "random",
                          "http", "requests", "time.sleep"):
            assert forbidden not in source, forbidden
        # The module defines exactly the two public functions — no resolution path.
        function_names = {
            node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function_names == {
            "confirmation_semantics", "build_reviewer_assistance",
            "_assist_task", "_assist_candidate",
        }

    def test_building_the_assistance_view_writes_nothing(self):
        before = set(os.listdir("."))
        for _ in range(2):
            _assistance(_fresh())
        assert set(os.listdir(".")) == before
        for directory in (Path("app"), Path("tests")):
            assert not [p for p in directory.rglob("*.pdf")], directory

    def test_refuses_anything_but_the_project_contract(self):
        with pytest.raises(TypeError, match="must be a ProjectReviewContract"):
            build_reviewer_assistance({"not": "a contract"})

    def test_confirmation_semantics_refuses_unknown_categories(self):
        with pytest.raises(ValueError, match="not one of the five workload categories"):
            confirmation_semantics("FORGED_CATEGORY")


# =============================================================================
# Verbatim observations — never converted.
# =============================================================================

class TestVerbatimObservations:
    def test_all_508_current_values_are_byte_identical_to_the_contract(self):
        contract = build_project_review_contract(_fresh())
        assistance = _assistance(_fresh())
        for item, candidate in zip(contract.items, assistance.candidates):
            for ctask, atask in zip(item.tasks, candidate.tasks):
                assert atask.current_value == ctask.current_value  # byte-verbatim

    def test_real_hole_readings_stay_the_ais_own_strings(self):
        assistance = _assistance(_fresh())
        assert _task(assistance, "RP-0008", TASK_PROVIDE_HOLE_DIAMETER).current_value == \
            RP0008_HOLE_READING
        assert _task(assistance, "RP-0009", TASK_PROVIDE_HOLE_DIAMETER).current_value == \
            RP0009_HOLE_READING

    def test_no_conversion_in_any_assistance_generated_text(self):
        assistance = _assistance(_fresh())
        for candidate in assistance.candidates:
            for task in candidate.tasks:
                texts = [task.proposal, task.reviewer_remaining,
                         task.confirmation.reviewer_action,
                         task.confirmation.system_never]
                if task.human_boundary is not None:
                    texts += [task.human_boundary.why_human,
                              task.human_boundary.missing_information,
                              task.human_boundary.judgement_constitutes]
                for text in texts:
                    assert "Ø" not in text
                    assert "diameter_mm" not in text
                    assert "mm" not in text  # no unit-bearing value in fixed texts

    def test_material_stays_an_observation(self):
        assistance = _assistance(_fresh())
        # RP-0008's confirm task shows the material observation verbatim, unconverted.
        confirm = _task(assistance, "RP-0008", TASK_CONFIRM_AI_VALUES)
        assert "'material', '300'" in confirm.current_value
        # The five genuinely missing materials are missing in the assistance too.
        for package_id in MISSING_MATERIAL_IDS:
            confirm = _task(assistance, package_id, TASK_CONFIRM_AI_VALUES)
            assert "'material'" not in confirm.current_value
            assert _item(_fresh(), package_id).ai_material is None

    def test_missing_material_remains_missing_in_the_material_route(self):
        workflow = _material_workflow()
        assistance = build_reviewer_assistance(build_project_review_contract(workflow))
        for package_id in MISSING_MATERIAL_IDS:
            task = _task(assistance, package_id, TASK_PROVIDE_MATERIAL_SPECIFICATION)
            assert task.category == CATEGORY_EVIDENCE_LOOKUP
            assert task.current_value is None  # the assistance supplies nothing
            assert task.source_anchor.startswith("source drawing SELBY-C1136")
        assert RP0033_MATERIAL_PAGE in _task(
            assistance, "RP-0033", TASK_PROVIDE_MATERIAL_SPECIFICATION).source_anchor


# =============================================================================
# The human boundary.
# =============================================================================

class TestHumanBoundary:
    def test_human_tasks_carry_no_value_and_the_fixed_boundary(self):
        assistance = _assistance(_fresh())
        human = [t for c in assistance.candidates for t in c.tasks
                 if t.category == CATEGORY_HUMAN_ENGINEERING_DECISION]
        assert len(human) == 156
        for task in human:
            assert task.current_value is None
            assert task.human_boundary is not None
            assert all((task.human_boundary.why_human,
                        task.human_boundary.missing_information,
                        task.human_boundary.judgement_constitutes))
            assert task.proposal == PROPOSAL_TEXTS[1]
            assert task.proposal_risks_engineering_decision is True
            assert task.confirmation == confirmation_semantics(
                CATEGORY_HUMAN_ENGINEERING_DECISION)

    def test_evidence_tasks_carry_no_value_only_the_anchor(self):
        assistance = _assistance(_fresh())
        evidence = [t for c in assistance.candidates for t in c.tasks
                    if t.category == CATEGORY_EVIDENCE_LOOKUP]
        assert len(evidence) == 81
        for task in evidence:
            assert task.current_value is None
            assert task.human_boundary is None
            assert task.proposal == PROPOSAL_TEXTS[2]
            assert task.proposal_risks_engineering_decision is True
            assert "SELBY-C1136" in task.source_anchor

    def test_assistable_tasks_display_the_observation_and_demand_the_reviewer(self):
        assistance = _assistance(_fresh())
        assistable = [t for c in assistance.candidates for t in c.tasks
                      if t.category == CATEGORY_ASSISTABLE]
        assert len(assistable) == 73
        for task in assistable:
            assert task.current_value is not None
            assert task.proposal == PROPOSAL_TEXTS[0]
            assert task.confirmation == confirmation_semantics(CATEGORY_ASSISTABLE)
            assert task.confirmation.system_never.startswith(
                "The system displays the recorded observation verbatim")

    def test_no_value_carrying_field_exists_on_the_assist_records(self):
        assert {f.name for f in dataclasses.fields(AssistTask)} == {
            "task_id", "task_type", "category", "required", "resolved",
            "current_value", "field", "source_anchor", "related_provenance",
            "human_boundary", "proposal", "reviewer_remaining", "confirmation",
            "proposal_risks_engineering_decision",
        }
        assert {f.name for f in dataclasses.fields(AssistCandidate)} == {
            "package_id", "revision", "decision", "blocker_codes", "tasks",
            "has_source_page", "has_detail_reference", "has_grid_reference",
            "requires_source_inspection", "requires_engineering_judgement",
            "bounded_confirmation_available", "no_safe_assistance_opportunity",
            "has_relevant_ai_observation",
        }
        assert {f.name for f in dataclasses.fields(ReviewerAssistance)} == {
            "project_id", "revision", "candidate_count", "task_count",
            "pending_task_count", "candidates",
        }


# =============================================================================
# The existing 7AU provenance semantics, executed unchanged.
# =============================================================================

class TestProvenanceSemantics:
    def test_confirmation_keeps_the_value_and_produces_human_reviewed(self, tmp_path):
        # RP-0032: the reviewer confirms the AI's plate reading through the
        # genuine CONFIRM_AI_VALUES task. 7AU semantics: the value stays
        # byte-unchanged and is relabelled HUMAN_REVIEWED.
        workflow = _fresh()
        before = _item(workflow, "RP-0032")
        confirm = next(t for t in _group(workflow, "RP-0032").tasks
                       if t.task_type == TASK_CONFIRM_AI_VALUES)
        # The genuine pending tuple carries the plate observation; the assistance
        # layer displays it verbatim (contract display string).
        assert dict(confirm.current_ai_value)["plate"]["thickness_mm"] == 20
        assert "'plate'" in _task(_assistance(workflow), "RP-0032",
                                  TASK_CONFIRM_AI_VALUES).current_value
        assert "'thickness_mm': 20" in _task(_assistance(workflow), "RP-0032",
                                             TASK_CONFIRM_AI_VALUES).current_value
        advanced = resolve_project_connection(
            workflow, package_id="RP-0032",
            resolutions=[HumanResolution(
                confirm.task_id, confirm.task_type, confirm.answer_type,
                ("plate",), evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        after = _item(advanced, "RP-0032")
        assert ("plate", "HUMAN_REVIEWED") in {(p.field, p.provenance)
                                               for p in after.provenance}
        assert after.ai_plate_readings == before.ai_plate_readings  # value unchanged
        assert after.provenance != ()  # the relabel is visible
        assert next(t for t in after.tasks
                    if t.task_type == TASK_CONFIRM_AI_VALUES).resolved is True

    def test_supply_produces_human_supplemented(self, tmp_path):
        # RP-0008: the reviewer supplies the hole answer (their own act — the
        # test stands in for the human; the assistance layer contributed none
        # of it). The genuine label is HUMAN_SUPPLEMENTED.
        workflow = _fresh()
        hole = next(t for t in _group(workflow, "RP-0008").tasks
                    if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0008",
            resolutions=[HumanResolution(
                hole.task_id, hole.task_type, hole.answer_type,
                dict(RP0008_HOLE_ANSWER), evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        after = _item(advanced, "RP-0008")
        assert ("holes", "HUMAN_SUPPLEMENTED") in {(p.field, p.provenance)
                                                   for p in after.provenance}
        resolved = next(t for t in after.tasks
                        if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        assert resolved.resolved is True
        assert resolved.resolution_value == \
            "{'quantity': 4, 'diameter_mm': 22.0, 'vertical_spacing_mm': 100.0}"

    def test_material_supply_produces_human_supplemented(self, tmp_path):
        workflow = _material_workflow()
        material = next(t for t in _group(workflow, "RP-0033").tasks
                        if t.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0033",
            resolutions=[HumanResolution(
                material.task_id, material.task_type, material.answer_type,
                RP0033_MATERIAL_ANSWER, evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        after = _item(advanced, "RP-0033")
        assert ("material", "HUMAN_SUPPLEMENTED") in {(p.field, p.provenance)
                                                      for p in after.provenance}
        assert after.ai_material is None  # the AI field stays missing; the human's value is separate
        resolved = next(t for t in after.tasks
                        if t.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION)
        assert resolved.resolved is True
        assert resolved.resolution_value == "'300PLUS'"

    def test_no_new_provenance_vocabulary_is_introduced(self, tmp_path):
        workflow = _fresh()
        hole = next(t for t in _group(workflow, "RP-0008").tasks
                    if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0008",
            resolutions=[HumanResolution(
                hole.task_id, hole.task_type, hole.answer_type,
                dict(RP0008_HOLE_ANSWER), evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        labels = {p.provenance for i in build_project_review_contract(advanced).items
                  for p in i.provenance}
        assert labels <= {"AI_EXTRACTED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED"}


# =============================================================================
# Real-world execution — the four genuine Selby cases.
# =============================================================================

class TestRealWorldExecution:
    def test_rp0008_hole_confirmation(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        task = _task(assistance, "RP-0008", TASK_PROVIDE_HOLE_DIAMETER)
        assert task.category == CATEGORY_ASSISTABLE
        assert task.current_value == RP0008_HOLE_READING
        # The reviewer's answer is an independent literal — never taken from
        # the assistance record (which carries no diameter anywhere).
        answer = dict(RP0008_HOLE_ANSWER)
        assert "diameter_mm" not in task.proposal
        hole = next(t for t in _group(workflow, "RP-0008").tasks
                    if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0008",
            resolutions=[HumanResolution(hole.task_id, hole.task_type,
                                         hole.answer_type, answer,
                                         evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        # The gates still decide: the other blockers stay open, so no artifact.
        state = _state(advanced, "RP-0008")
        assert advanced.revision == 1
        assert state.decision == "REVIEW"
        assert state.output_status == "BLOCKED_REVIEW"
        assert state.generated_files == ()
        # Exactly one task resolved; the other nine stay open.
        resolved = [t.resolved for t in _item(advanced, "RP-0008").tasks]
        assert resolved.count(True) == 1
        assert [t.task_type for t in _item(advanced, "RP-0008").tasks
                if t.resolved] == [TASK_PROVIDE_HOLE_DIAMETER]
        # Every other candidate untouched (only the project revision advanced).
        untouched = _item(advanced, "RP-0009")
        assert untouched.decision == "REVIEW"
        assert all(not t.resolved for t in untouched.tasks)

    def test_rp0032_plate_confirmation(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        task = _task(assistance, "RP-0032", TASK_CONFIRM_AI_VALUES)
        assert task.category == CATEGORY_ASSISTABLE
        # The pending plate observation is displayed verbatim in the confirm task.
        assert "'plate'" in task.current_value
        assert "'thickness_mm': 20" in task.current_value
        confirm = next(t for t in _group(workflow, "RP-0032").tasks
                       if t.task_type == TASK_CONFIRM_AI_VALUES)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0032",
            resolutions=[HumanResolution(confirm.task_id, confirm.task_type,
                                         confirm.answer_type, ("plate",),
                                         evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        state = _state(advanced, "RP-0032")
        assert state.decision == "REVIEW"
        assert state.output_status == "BLOCKED_REVIEW"
        assert state.generated_files == ()
        after = _item(advanced, "RP-0032")
        # Only the confirmed field was relabelled; the other pending fields
        # stay AI_EXTRACTED — a partial confirmation relabels only itself.
        assert {(p.field, p.provenance) for p in after.provenance} == {
            ("connected_member_marks", "AI_EXTRACTED"),
            ("plate", "HUMAN_REVIEWED"),
            ("material", "AI_EXTRACTED"),
        }
        resolved = next(t for t in after.tasks
                        if t.task_type == TASK_CONFIRM_AI_VALUES)
        assert resolved.resolved is True
        assert resolved.resolution_value == "('plate',)"

    def test_rp0033_missing_material_case(self, tmp_path):
        workflow = _material_workflow()
        assistance = build_reviewer_assistance(build_project_review_contract(workflow))
        task = _task(assistance, "RP-0033", TASK_PROVIDE_MATERIAL_SPECIFICATION)
        assert task.category == CATEGORY_EVIDENCE_LOOKUP
        assert task.current_value is None  # nothing to display — the value is missing
        assert RP0033_MATERIAL_PAGE in task.source_anchor  # navigation only
        material = next(t for t in _group(workflow, "RP-0033").tasks
                        if t.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0033",
            resolutions=[HumanResolution(material.task_id, material.task_type,
                                         material.answer_type,
                                         RP0033_MATERIAL_ANSWER,
                                         evidence=EVIDENCE_7B6)],
            output_dir=tmp_path,
        )
        state = _state(advanced, "RP-0033")
        assert state.decision == "REVIEW"  # other blockers stay open
        assert state.generated_files == ()
        after = _item(advanced, "RP-0033")
        assert ("material", "HUMAN_SUPPLEMENTED") in {(p.field, p.provenance)
                                                      for p in after.provenance}
        assert after.ai_material is None  # the AI-extracted field was never filled by anyone but the AI

    def test_rp0009_full_resolution_case(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        candidate = _candidate(assistance, "RP-0009")
        assert candidate.decision == "REVIEW"
        # The assist views for the genuine B-B candidate.
        confirm = _task(assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        assert confirm.category == CATEGORY_ASSISTABLE
        assert "'connected_member_marks'" in confirm.current_value
        assert "'material', '300'" in confirm.current_value
        hole = _task(assistance, "RP-0009", TASK_PROVIDE_HOLE_DIAMETER)
        assert hole.category == CATEGORY_ASSISTABLE
        assert hole.current_value == RP0009_HOLE_READING
        anchor = hole.source_anchor
        assert "SELBY-C1136" in anchor and "page 5" in anchor and "VIEW B-B" in anchor
        human = [t for t in candidate.tasks
                 if t.category == CATEGORY_HUMAN_ENGINEERING_DECISION]
        assert len(human) == 3  # position, attachment, location
        assert all(t.current_value is None for t in human)
        # The genuine full resolution — the same human answers 7B3 recorded.
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        state = _state(advanced, "RP-0009")
        assert (state.decision, state.output_status, state.verification_status) == \
            ("AUTO", "GENERATED", "VERIFIED")
        assert {(p.field, p.provenance) for p in _item(advanced, "RP-0009").provenance} == \
            RP0009_RESOLVED_PROVENANCE
        assert len(state.generated_files) == 1
        artifact = Path(state.generated_files[0])
        assert artifact.name == RP0009_PDF_NAME
        assert artifact.stat().st_size == RP0009_PDF_SIZE


# =============================================================================
# Equivalence — assisted and unassisted resolution produce the same truth.
# =============================================================================

class TestEquivalence:
    def test_building_the_assistance_view_changes_no_workflow_state(self, tmp_path):
        workflow = _fresh()
        before = _snapshot(workflow, tmp_path)
        assert workflow.revision == 0
        _assistance(workflow)
        assert workflow.revision == 0
        assert _snapshot(workflow, tmp_path) == before

    def test_assisted_full_resolution_equals_unassisted(self, tmp_path):
        assisted_dir = tmp_path / "assisted"
        unassisted_dir = tmp_path / "unassisted"
        # Assisted: the assistance view is built and checked first, then the
        # SAME human resolutions 7B3 recorded are applied through the same
        # genuine public path. The assistance layer adds no input.
        assisted_workflow = _fresh()
        _assistance(assisted_workflow)
        assisted = _resolve_full_rp0009(assisted_workflow, assisted_dir)
        # Unassisted: the genuine 7B3 path, untouched.
        unassisted = _resolve_full_rp0009(_fresh(), unassisted_dir)
        assert assisted.revision == unassisted.revision == 1
        for workflow, directory in ((assisted, assisted_dir), (unassisted, unassisted_dir)):
            state = _state(workflow, "RP-0009")
            assert (state.decision, state.output_status, state.verification_status) == \
                ("AUTO", "GENERATED", "VERIFIED")
            assert len(state.generated_files) == 1
            assert Path(state.generated_files[0]).stat().st_size == RP0009_PDF_SIZE
        # Every engineering/workflow field is equal; only the artifact paths
        # point into different output directories (scrubbed in the snapshot).
        assert _snapshot(assisted, assisted_dir) == _snapshot(unassisted, unassisted_dir)
        assert {(p.field, p.provenance) for p in _item(assisted, "RP-0009").provenance} == \
            {(p.field, p.provenance) for p in _item(unassisted, "RP-0009").provenance} == \
            RP0009_RESOLVED_PROVENANCE


# =============================================================================
# Refusal and no-op negatives.
# =============================================================================

class TestNegativeProofs:
    def test_unknown_task_types_are_refused(self):
        from app.cad_engine.reviewer_workload_reduction import classify_task
        with pytest.raises(ValueError, match="not part of the exception-resolution contract"):
            classify_task("FORGED_TASK_TYPE", None)

    def test_invalid_categories_are_refused(self):
        with pytest.raises(ValueError, match="not one of the five workload categories"):
            confirmation_semantics("FORGED_CATEGORY")

    def test_stale_contracts_cannot_be_applied(self, tmp_path):
        workflow = _fresh()
        stale_snapshot = dataclasses.asdict(build_project_review_contract(workflow))
        # The workflow advances through a genuine resolution...
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        assert advanced.revision == 1
        # ...so the rev-0 snapshot is stale: the genuine guard refuses, and
        # nothing is processed.
        hole = next(t for t in _group(_fresh(), "RP-0008").tasks
                    if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        with pytest.raises(StaleProjectWorkflowError):
            resolve_project_connection(
                advanced, package_id="RP-0008",
                resolutions=[HumanResolution(hole.task_id, hole.task_type,
                                             hole.answer_type, dict(RP0008_HOLE_ANSWER),
                                             evidence=EVIDENCE_7B6)],
                output_dir=tmp_path, expected_revision=0,
            )
        assert advanced.revision == 1
        assert _state(advanced, "RP-0008").last_processed_revision is None
        # The stale snapshot is frozen truth of revision 0 — byte-equal still.
        assert dataclasses.asdict(build_project_review_contract(_fresh())) == stale_snapshot

    def test_attempted_mutation_is_refused(self):
        assistance = _assistance(_fresh())
        with pytest.raises(dataclasses.FrozenInstanceError):
            assistance.candidates[0].tasks[0].current_value = "22mm"
        with pytest.raises(dataclasses.FrozenInstanceError):
            assistance.candidates[0].revision = 99
        with pytest.raises(dataclasses.FrozenInstanceError):
            assistance.task_count = 0

    def test_no_engineering_value_proposal_on_human_tasks(self):
        assistance = _assistance(_fresh())
        for candidate in assistance.candidates:
            for task in candidate.tasks:
                if task.category != CATEGORY_HUMAN_ENGINEERING_DECISION:
                    continue
                assert task.current_value is None
                for text in (task.proposal, task.reviewer_remaining,
                             task.confirmation.reviewer_action,
                             task.confirmation.system_never,
                             task.human_boundary.why_human,
                             task.human_boundary.missing_information,
                             task.human_boundary.judgement_constitutes):
                    assert not any(char.isdigit() for char in text), text

    def test_attempted_value_conversion_is_impossible(self):
        contract = build_project_review_contract(_fresh())
        assistance = _assistance(_fresh())
        for item, candidate in zip(contract.items, assistance.candidates):
            for ctask, atask in zip(item.tasks, candidate.tasks):
                assert atask.current_value == ctask.current_value
        # The classification flips only on presence, never on content.
        from app.cad_engine.reviewer_workload_reduction import classify_task
        for reading in (RP0008_HOLE_READING, RP0009_HOLE_READING,
                        "((4, 'Ø18mm holes', None, ()),)", "((4, 'M22', None, ()),)"):
            assert classify_task(TASK_PROVIDE_HOLE_DIAMETER, reading) == \
                CATEGORY_ASSISTABLE

    def test_missing_material_is_never_fabricated_by_assistance(self):
        assistance = _assistance(_fresh())
        for package_id in MISSING_MATERIAL_IDS:
            candidate = _candidate(assistance, package_id)
            for task in candidate.tasks:
                assert "300" not in task.proposal
                assert task.current_value is None or "'material'" not in task.current_value
            assert _item(_fresh(), package_id).ai_material is None

    def test_no_ranking_scoring_or_prioritisation(self):
        source = Path("app/cad_engine/reviewer_assistance.py").read_text()
        lowered = source.lower()
        for forbidden in ("rank", "score", "priorit", "difficul", " easy", " hard",
                          "weight", "sorted(", "triage"):
            assert forbidden not in lowered, forbidden
        assistance = _assistance(_fresh())
        assert [c.package_id for c in assistance.candidates] == list(EXPECTED_IDS)

    def test_no_candidate_duplication_or_disappearance(self):
        contract = build_project_review_contract(_fresh())
        assistance = _assistance(_fresh())
        ids = [c.package_id for c in assistance.candidates]
        assert ids == [i.package_id for i in contract.items] == list(EXPECTED_IDS)
        assert len(ids) == len(set(ids)) == 52

    def test_provenance_is_never_manipulated_by_assistance(self, tmp_path):
        workflow = _fresh()
        before = tuple(
            (i.package_id, tuple((p.field, p.provenance) for p in i.provenance))
            for i in build_project_review_contract(workflow).items)
        assistance = build_reviewer_assistance(build_project_review_contract(workflow))
        after = tuple(
            (i.package_id, tuple((p.field, p.provenance) for p in i.provenance))
            for i in build_project_review_contract(workflow).items)
        assert after == before
        # The assistance carries the contract's entries verbatim — no relabel.
        for candidate in assistance.candidates:
            for task in candidate.tasks:
                assert {p.provenance for p in task.related_provenance} <= \
                    {"AI_EXTRACTED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED"}

    def test_no_artifact_generation_through_the_assistance_layer(self):
        source = Path("app/cad_engine/reviewer_assistance.py").read_text()
        for forbidden in ("open(", "write(", "Path(", "os.", "sys."):
            assert forbidden not in source, forbidden
        before = set(os.listdir("."))
        _assistance(_fresh())
        assert set(os.listdir(".")) == before


# =============================================================================
# The 52-candidate regression.
# =============================================================================

class TestRegression:
    def test_51_candidates_unchanged(self):
        workflow = _fresh()
        contract = build_project_review_contract(workflow)
        assert [i.package_id for i in contract.items] == list(EXPECTED_IDS)
        assert all(i.decision == "REVIEW" for i in contract.items)
        assert all(i.requires_action for i in contract.items)
        assert workflow.generated_files == ()

    def test_7b5_baseline_unchanged_through_the_assistance_layer(self):
        contract = build_project_review_contract(_fresh())
        baseline = build_reviewer_workload_baseline(contract)
        assert {c.category: c.task_count for c in baseline.categories} == \
            CATEGORY_TASK_COUNTS
        assistance = build_reviewer_assistance(contract)
        assert assistance.revision == 0
        assert assistance.candidate_count == 52
        assert assistance.task_count == 518
        assert assistance.pending_task_count == 518
        observed = {}
        for candidate in assistance.candidates:
            for task in candidate.tasks:
                observed[task.category] = observed.get(task.category, 0) + 1
        assert {category: observed.get(category, 0) for category in CATEGORY_TASK_COUNTS} == \
            CATEGORY_TASK_COUNTS
        assert {c.category for c in baseline.categories} == {
            CATEGORY_ASSISTABLE, CATEGORY_HUMAN_ENGINEERING_DECISION,
            CATEGORY_EVIDENCE_LOOKUP, CATEGORY_ADMINISTRATIVE,
            CATEGORY_NOT_APPLICABLE,
        }

    def test_every_source_anchor_carries_the_genuine_identity(self):
        assistance = _assistance(_fresh())
        for candidate in assistance.candidates:
            anchors = {t.source_anchor for t in candidate.tasks}
            assert len(anchors) == 1  # uniform per candidate
            anchor = candidate.tasks[0].source_anchor
            assert anchor.startswith("source drawing SELBY-C1136")
            assert "page" in anchor
