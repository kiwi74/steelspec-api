"""
Milestone 7AM — DETERMINISTIC UI VIEW-MODEL RENDERING BOUNDARY.

These tests prove the renderer is a pure presentation adapter over the
existing review contract: it transforms the 7AK contracts into immutable
plain-data views for a future frontend WITHOUT changing what SteelSpec
believes. Every view is compared field-for-field against the contract it
was rendered from; AI values, evidence, provenance, tasks, statuses and
actions must survive rendering verbatim.

The scenarios are the GENUINE ones established by 7AL: the real capture
at revisions 0-3 through the existing workflow, plus the two failure
scenarios injected through the existing drawing_entry seam. The renderer
itself never resolves, decides, generates or verifies anything.
"""

import ast
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.cad_engine.review_view_model as view_model_module
from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
)
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.project_workflow import StaleProjectWorkflowError
from app.cad_engine.review_contract import (
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    ConnectionReviewContract,
    ProjectReviewContract,
    ReviewBlockerInfo,
    ReviewEvidenceInfo,
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import (
    ActionView,
    AttentionView,
    BlockerView,
    ConnectionIdentityView,
    ConnectionReviewView,
    CountsView,
    EvidenceView,
    ExtractedValuesView,
    OutputView,
    ProjectReviewView,
    ProvenanceView,
    TaskView,
    render_connection_view,
    render_project_view,
)
from app.cad_engine.reviewed_connection_specification import PROVENANCE_HUMAN_SUPPLEMENTED
from tests.test_exception_resolution import ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    CONNECTION_IDENTITIES,
    GARBAGE_BYTES,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _resolve,
    _start,
    _state,
)
from tests.test_review_contract_consumption import _all_scenarios

SIX_ENGINEERING_FIELDS = (
    "connected_member_marks", "position", "plate", "holes", "location", "attachments",
)

FORBIDDEN_ACTION_NAMES = (
    "APPROVE", "FORCE_AUTO", "FORCE_CONFIRM", "GENERATE", "VERIFY", "MARK_VERIFIED",
    "BYPASS_VALIDATION", "BYPASS_DRAWING_GATE", "BYPASS_VERIFICATION",
)


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    """Revision 0 -> 3 through the GENUINE workflow, plus the output dir."""
    out = tmp_path_factory.mktemp("7am-sequence")
    return _full_sequence(out), out


@pytest.fixture(scope="module")
def failures(tmp_path_factory):
    """The two genuine 7AL failure scenarios: RP-0001 resolves for real, then
    RP-0002 hits the existing drawing_entry seam."""
    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("simulated generator failure for the 7AM test")

    def garbage_entry(assembly, output_path, **kwargs):
        Path(output_path).write_bytes(GARBAGE_BYTES)

    out_gen = tmp_path_factory.mktemp("7am-genfail")
    _, _, built_a, w0a = _start()
    w2a = _resolve(
        _resolve(w0a, built_a, "RP-0001", out_gen),
        built_a, "RP-0002", out_gen, drawing_entry=exploding_entry,
    )
    out_ver = tmp_path_factory.mktemp("7am-verfail")
    _, _, built_b, w0b = _start()
    w2b = _resolve(
        _resolve(w0b, built_b, "RP-0001", out_ver),
        built_b, "RP-0002", out_ver, drawing_entry=garbage_entry,
    )
    return {
        "generation_failure": (w2a, out_gen),
        "verification_failure": (w2b, out_ver),
    }


@pytest.fixture(scope="module")
def scenario_views(sequence, failures):
    """(label, project contract, rendered view) for every genuine scenario."""
    views = []
    for label, workflow in _all_scenarios(sequence, failures):
        contract = build_project_review_contract(workflow)
        views.append((label, contract, render_project_view(contract)))
    return views


def _view(sequence, index):
    return render_project_view(build_project_review_contract(sequence[0][index]))


def _connection_view(scenario_views, label, package_id):
    contract = next(c for l, c, v in scenario_views if l == label)
    return render_connection_view(next(i for i in contract.items if i.package_id == package_id))


# -----------------------------------------------------------------------------
# The renderer must not alter engineering truth: every view field traces back
# to the contract it was rendered from.
# -----------------------------------------------------------------------------
def _assert_connection_view_matches_contract(view, contract):
    assert view.identity.package_id == contract.package_id
    assert view.identity.connection_id == contract.connection_id
    assert view.identity.project_id == contract.project_id
    assert view.identity.revision == contract.revision
    # Display reference is the contract's own identity — never derived.
    assert view.identity.display_reference == contract.connection_id
    assert view.decision == contract.decision
    assert view.attention.requires_attention == contract.requires_action
    assert [b.code for b in view.blockers] == [b.code for b in contract.blockers]
    assert [b.title for b in view.blockers] == [b.title for b in contract.blockers]
    assert [b.message for b in view.blockers] == [b.message for b in contract.blockers]
    assert [b.severity for b in view.blockers] == [b.severity for b in contract.blockers]
    assert [b.field for b in view.blockers] == [b.field for b in contract.blockers]
    assert [b.task_type for b in view.blockers] == [b.task_type for b in contract.blockers]
    assert [w.code for w in view.warnings] == [w.code for w in contract.warnings]
    assert view.evidence.source_drawing_id == contract.evidence.source_drawing_id
    assert view.evidence.drawing_number == contract.evidence.drawing_number
    assert view.evidence.source_page == contract.evidence.source_page
    assert view.evidence.detail_reference == contract.evidence.detail_reference
    assert view.evidence.grid_reference == contract.evidence.grid_reference
    assert view.extracted.member_references == contract.ai_member_references
    assert view.extracted.bolt_readings == contract.ai_bolt_readings
    assert view.extracted.plate_readings == contract.ai_plate_readings
    assert view.extracted.weld_readings == contract.ai_weld_readings
    assert view.extracted.malformed_readings == contract.ai_malformed_readings
    assert view.extracted.unrecognised_readings == contract.ai_unrecognised_readings
    assert view.extracted.connection_type == contract.ai_connection_type
    assert view.extracted.confidence == contract.ai_confidence
    assert [(p.field, p.provenance) for p in view.provenance] == [
        (p.field, p.provenance) for p in contract.provenance
    ]
    assert [t.task_id for t in view.tasks] == [t.task_id for t in contract.tasks]
    assert [t.task_type for t in view.tasks] == [t.task_type for t in contract.tasks]
    assert [t.title for t in view.tasks] == [t.title for t in contract.tasks]
    assert [t.description for t in view.tasks] == [t.description for t in contract.tasks]
    assert [t.field for t in view.tasks] == [t.field for t in contract.tasks]
    assert [t.required for t in view.tasks] == [t.required for t in contract.tasks]
    assert [t.resolved for t in view.tasks] == [t.resolved for t in contract.tasks]
    assert [t.current_value for t in view.tasks] == [t.current_value for t in contract.tasks]
    assert [t.allowed_options for t in view.tasks] == [t.allowed_options for t in contract.tasks]
    assert [t.evidence_requirement for t in view.tasks] == [t.evidence_requirement for t in contract.tasks]
    assert [t.resolution_value for t in view.tasks] == [t.resolution_value for t in contract.tasks]
    assert [t.resolution_evidence for t in view.tasks] == [t.resolution_evidence for t in contract.tasks]
    assert [a.action for a in view.actions] == list(contract.available_actions)
    assert view.output.output_status == contract.output_status
    assert view.output.verification_status == contract.verification_status
    assert view.output.generated_files == contract.generated_files
    assert view.summary == contract.summary


def _assert_project_view_matches_contract(view, contract):
    assert view.project_id == contract.project_id
    assert view.revision == contract.revision
    assert view.project_status == contract.project_status
    assert view.counts.review == contract.review_count
    assert view.counts.verified == contract.verified_count
    assert view.counts.auto == contract.auto_count
    assert view.counts.confirmation == contract.confirmation_count
    assert view.counts.blocked == contract.blocked_count
    assert view.verified == contract.verified_count
    assert view.requiring_attention == sum(1 for i in contract.items if i.requires_action)
    assert [a.action for a in view.actions] == list(contract.available_actions)
    # The split preserves every item exactly once; within each group the
    # contract's own order is kept. The groups themselves reorder items by
    # design — that is the one deliberate presentation change.
    all_items = view.review_items + view.completed_items
    assert [i.identity.package_id for i in view.review_items] == [
        i.package_id for i in contract.items if i.requires_action
    ]
    assert [i.identity.package_id for i in view.completed_items] == [
        i.package_id for i in contract.items if not i.requires_action
    ]
    by_package = {i.package_id: i for i in contract.items}
    assert len(all_items) == len(contract.items)
    for rendered in all_items:
        _assert_connection_view_matches_contract(rendered, by_package[rendered.identity.package_id])


# =============================================================================
# The real capture drives every scenario below.
# =============================================================================
@needs_real_capture
class TestReviewViewModel:
    # -------------------------------------------------------------------------
    # Revision 0 — the control rendering.
    # -------------------------------------------------------------------------
    def test_project_view_renders_at_rev0(self, sequence):
        view = _view(sequence, 0)
        assert view.project_status == AUTOMATION_DECISION_REVIEW
        assert view.status_label == "Needs review"
        assert [i.identity.package_id for i in view.review_items] == list(CANDIDATE_IDS)
        assert view.completed_items == ()
        assert (view.counts.review, view.counts.verified, view.counts.auto,
                view.counts.confirmation, view.counts.blocked) == (3, 0, 0, 0, 3)
        assert view.requiring_attention == 3 and view.verified == 0
        assert [a.label for a in view.actions] == ["Review", "Refresh"]

    def test_connection_views_are_complete_at_rev0(self, scenario_views):
        for label, contract, view in scenario_views:
            if label != "revision 0":
                continue
            for item_contract in contract.items:
                rendered = next(
                    i for i in view.review_items if i.identity.package_id == item_contract.package_id
                )
                _assert_connection_view_matches_contract(rendered, item_contract)
                assert rendered.decision_label == "Needs review"
                assert rendered.attention.requires_attention is True
                assert rendered.attention.label == "Needs attention"
                assert {b.code for b in rendered.blockers} == ARKLES_BLOCKER_CODES
                assert all(b.severity_label == "Blocking" for b in rendered.blockers)
                assert len(rendered.tasks) == 8
                assert [a.action for a in rendered.actions] == [ACTION_REVIEW, ACTION_RESOLVE]
                assert [a.label for a in rendered.actions] == ["Review", "Resolve"]
                # Identity: no connection identity yet, and none invented.
                assert rendered.identity.connection_id is None
                assert rendered.identity.display_reference is None
                assert rendered.output.output_status is None
                assert rendered.output.generated_files == ()
                assert rendered.provenance == ()

    # -------------------------------------------------------------------------
    # Evidence and AI values — verbatim, nothing invented, nothing decorated.
    # -------------------------------------------------------------------------
    def test_evidence_is_rendered_without_invention(self, scenario_views):
        rendered = _connection_view(scenario_views, "revision 0", "RP-0001")
        evidence = rendered.evidence
        assert evidence.source_drawing_id == SOURCE_DRAWING_ID
        assert evidence.source_page == 7
        assert evidence.detail_reference is None
        assert evidence.grid_reference is None
        # Only what the capture has: the drawing and the page — no fake
        # detail or grid locator can appear.
        assert evidence.evidence_text == f"source drawing {SOURCE_DRAWING_ID}; page 7"
        assert "detail" not in evidence.evidence_text
        assert "grid" not in evidence.evidence_text

    def test_ai_values_stay_lossless(self, sequence, failures):
        for label, workflow in _all_scenarios(sequence, failures):
            contract = build_project_review_contract(workflow)
            rp1 = next(i for i in contract.items if i.package_id == "RP-0001")
            rp3 = next(i for i in contract.items if i.package_id == "RP-0003")
            view1 = render_connection_view(rp1)
            view3 = render_connection_view(rp3)
            assert any("'M12'" in reading for reading in view1.extracted.bolt_readings), label
            assert any("SQ4 12mm" in reading for reading in view3.extracted.bolt_readings), label
            # No decoration, no interpretation, no unit inference anywhere.
            for reading in view1.extracted.bolt_readings + view3.extracted.bolt_readings:
                assert "M12 bolt" not in reading
                assert "Ø" not in reading

    # -------------------------------------------------------------------------
    # Genuine resolutions — the view follows the workflow, revision by revision.
    # -------------------------------------------------------------------------
    def test_rev1_after_genuine_resolution(self, sequence):
        view = _view(sequence, 1)
        assert view.revision == 1
        # RP-0001 leaves the review list; RP-0002/RP-0003 remain.
        assert [i.identity.package_id for i in view.review_items] == ["RP-0002", "RP-0003"]
        assert [i.identity.package_id for i in view.completed_items] == ["RP-0001"]
        rp1 = view.completed_items[0]
        assert rp1.decision == AUTOMATION_DECISION_AUTO
        assert rp1.decision_label == "Automated"
        assert rp1.attention.requires_attention is False
        assert rp1.actions == ()
        assert rp1.identity.connection_id == CONNECTION_IDENTITIES["RP-0001"]
        assert rp1.identity.display_reference == CONNECTION_IDENTITIES["RP-0001"]
        assert rp1.output.output_status == OUTPUT_STATUS_GENERATED
        assert rp1.output.output_label == "Generated"
        assert rp1.output.verification_status == VERIFICATION_STATUS_VERIFIED
        assert rp1.output.verification_label == "Verified"
        # Provenance survives rendering with its human-readable labels.
        assert [p.field for p in rp1.provenance] == list(SIX_ENGINEERING_FIELDS)
        assert all(p.provenance == PROVENANCE_HUMAN_SUPPLEMENTED for p in rp1.provenance)
        assert all(p.provenance_label == "Human supplied" for p in rp1.provenance)
        assert "connection_id" not in {p.field for p in rp1.provenance}

    def test_rev3_complete_project(self, sequence):
        view = _view(sequence, 3)
        assert view.project_status == AUTOMATION_DECISION_AUTO
        assert view.status_label == "Automated"
        assert view.review_items == ()
        assert [i.identity.package_id for i in view.completed_items] == list(CANDIDATE_IDS)
        assert all(i.output.verification_status == VERIFICATION_STATUS_VERIFIED
                   for i in view.completed_items)
        assert all(i.actions == () for i in view.completed_items)
        assert view.summary == "All connections verified"
        assert [a.label for a in view.actions] == ["Refresh"]

    def test_summary_is_generated_not_hard_coded(self, sequence, failures):
        expected = {
            "revision 0": "3 connections need review; 0 connections verified; 0 connections remain",
            "revision 1": "2 connections need review; 1 connection verified; 0 connections remain",
            "revision 2": "1 connection needs review; 2 connections verified; 0 connections remain",
            "revision 3": "All connections verified",
            # Refresh never changes engineering truth, so it changes no summary.
            "refreshed revision 0": "3 connections need review; 0 connections verified; 0 connections remain",
            "refreshed revision 3": "All connections verified",
            "generation failure": "1 connection needs review; 1 connection verified; 1 connection remains",
            "verification failure": "1 connection needs review; 1 connection verified; 1 connection remains",
        }
        for label, workflow in _all_scenarios(sequence, failures):
            rendered = render_project_view(build_project_review_contract(workflow))
            assert rendered.summary == expected[label], label

    # -------------------------------------------------------------------------
    # Failure states — the view keeps the workflow's truth, verbatim.
    # -------------------------------------------------------------------------
    def test_generation_failure_is_rendered_truthfully(self, failures):
        contract = build_project_review_contract(failures["generation_failure"][0])
        view = render_project_view(contract)
        rp2 = next(i for i in view.completed_items if i.identity.package_id == "RP-0002")
        # The rerun succeeded (AUTO); the generation boundary failed. Neither
        # collapses into REVIEW nor dresses up as success.
        assert rp2.decision == AUTOMATION_DECISION_AUTO
        assert rp2.decision_label == "Automated"
        assert rp2.output.output_status == OUTPUT_STATUS_GENERATION_FAILED
        assert rp2.output.output_label == "Generation failed"
        assert rp2.output.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert rp2.output.verification_label == "No artifact"
        assert rp2.output.generated_files == ()
        # The contract says not actionable — the view must not manufacture one.
        assert rp2.attention.requires_attention is False
        assert rp2.actions == ()
        # No fake success: nothing claims Generated or Verified for RP-0002.
        assert rp2.output.output_label != "Generated"
        assert rp2.output.verification_label != "Verified"
        # The genuinely verified connection and the pending one are untouched.
        rp1 = next(i for i in view.completed_items if i.identity.package_id == "RP-0001")
        assert rp1.output.verification_status == VERIFICATION_STATUS_VERIFIED
        assert [i.identity.package_id for i in view.review_items] == ["RP-0003"]
        _assert_project_view_matches_contract(view, contract)

    def test_verification_failure_is_rendered_truthfully(self, failures):
        contract = build_project_review_contract(failures["verification_failure"][0])
        view = render_project_view(contract)
        rp2 = next(i for i in view.completed_items if i.identity.package_id == "RP-0002")
        # Dispatch generated; 7AG failed it. Both shown; the failed artifact
        # stays represented — file presence never becomes success.
        assert rp2.output.output_status == OUTPUT_STATUS_GENERATED
        assert rp2.output.output_label == "Generated"
        assert rp2.output.verification_status == VERIFICATION_STATUS_FAILED
        assert rp2.output.verification_label == "Verification failed"
        assert len(rp2.output.generated_files) == 1
        failed_path = Path(rp2.output.generated_files[0])
        assert failed_path.name == "CONN-ARKLES-002-fabrication.pdf"
        assert failed_path.read_bytes() == GARBAGE_BYTES
        assert rp2.attention.requires_attention is False
        assert rp2.actions == ()
        assert rp2.output.verification_label != "Verified"
        assert [i.identity.package_id for i in view.review_items] == ["RP-0003"]
        _assert_project_view_matches_contract(view, contract)

    def test_stale_resolution_stays_a_workflow_error(self, tmp_path):
        # The renderer does not catch or reinterpret workflow errors: the stale
        # attempt raises from the workflow itself, and the view keeps showing
        # the unchanged authoritative state.
        _, _, built, w0 = _start()
        w1 = _resolve(w0, built, "RP-0001", tmp_path)
        view_before = render_project_view(build_project_review_contract(w1))
        with pytest.raises(StaleProjectWorkflowError):
            _resolve(w1, built, "RP-0002", tmp_path, expected_revision=0)
        view_after = render_project_view(build_project_review_contract(w1))
        assert view_after == view_before
        assert [i.identity.package_id for i in view_after.review_items] == ["RP-0002", "RP-0003"]

    # -------------------------------------------------------------------------
    # Consistency, determinism, immutability, snapshot isolation, actions.
    # -------------------------------------------------------------------------
    def test_every_scenario_view_matches_its_contract(self, scenario_views):
        for label, contract, view in scenario_views:
            _assert_project_view_matches_contract(view, contract)

    def test_rendering_is_deterministic(self, sequence, failures):
        for label, workflow in _all_scenarios(sequence, failures):
            contract = build_project_review_contract(workflow)
            first = render_project_view(contract)
            second = render_project_view(contract)
            assert first == second, label
            assert dataclasses.asdict(first) == dataclasses.asdict(second), label

    def test_views_are_immutable(self, sequence):
        view = _view(sequence, 1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.status_label = "changed"
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.counts.review = 99
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.completed_items[0].decision = AUTOMATION_DECISION_REVIEW
        # A processed connection's blockers are legitimately empty (the gate
        # cleared them when it passed) — freeze-test a review item's blockers.
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.review_items[0].blockers[0].message = "changed"
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.completed_items[0].tasks[0].resolved = True
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.completed_items[0].output.generated_files = ()
        with pytest.raises(AttributeError):
            view.review_items.append(view.completed_items[0])
        with pytest.raises(AttributeError):
            view.completed_items[0].actions.append(ActionView("X", "X"))

    def test_rendering_does_not_mutate_the_contract(self, sequence):
        contract = build_project_review_contract(sequence[0][0])
        before = dataclasses.asdict(contract)
        render_project_view(contract)
        for item in contract.items:
            render_connection_view(item)
        assert dataclasses.asdict(contract) == before

    def test_snapshot_isolation_across_later_states(self, sequence, failures):
        # A view rendered earlier stays byte-identical after later states
        # (including the failure scenarios) are rendered too.
        early = _view(sequence, 0)
        for label, workflow in _all_scenarios(sequence, failures):
            render_project_view(build_project_review_contract(workflow))
        assert _view(sequence, 0) == early

    def test_actions_are_only_the_contracts_own(self, scenario_views):
        for label, contract, view in scenario_views:
            assert [a.action for a in view.actions] == list(contract.available_actions), label
            assert all(a.action in (ACTION_REVIEW, ACTION_RESOLVE, ACTION_REFRESH)
                       for a in view.actions), label
            assert all(a.action not in FORBIDDEN_ACTION_NAMES for a in view.actions), label
            for item in view.review_items + view.completed_items:
                item_contract = next(
                    i for i in contract.items if i.package_id == item.identity.package_id
                )
                assert [a.action for a in item.actions] == list(item_contract.available_actions), label
                assert all(a.action not in FORBIDDEN_ACTION_NAMES for a in item.actions), label

    # -------------------------------------------------------------------------
    # Unknown-value fallbacks — deterministic, verbatim, never dropped.
    # -------------------------------------------------------------------------
    def test_unknown_values_render_verbatim_not_dropped(self):
        # A contract carrying values outside the known vocabulary is still
        # rendered: every unknown machine value appears as its own label.
        contract = ConnectionReviewContract(
            package_id="RP-X", connection_id=None, project_id=None, revision=0,
            decision="SOME_FUTURE_DECISION", output_status=None, verification_status=None,
            requires_action=False,
            blockers=(ReviewBlockerInfo("SOME_FUTURE_BLOCKER", "T", "M", "odd", None, None),),
            warnings=(),
            ai_member_references=(), ai_bolt_readings=(), ai_plate_readings=(),
            ai_weld_readings=(), ai_malformed_readings=(), ai_unrecognised_readings=(),
            ai_connection_type=None, ai_confidence=None, ai_material=None,
            evidence=ReviewEvidenceInfo(None, None, None, None, None),
            provenance=(), tasks=(),
            available_actions=("SOME_FUTURE_ACTION",),
            generated_files=(), last_processed_revision=None, summary="s",
        )
        view = render_connection_view(contract)
        assert view.decision_label == "SOME_FUTURE_DECISION"
        assert view.blockers[0].code == "SOME_FUTURE_BLOCKER"
        assert view.blockers[0].severity_label == "odd"
        assert view.actions[0].action == "SOME_FUTURE_ACTION"
        assert view.actions[0].label == "SOME_FUTURE_ACTION"

    def test_renderers_refuse_non_contracts(self):
        with pytest.raises(TypeError):
            render_project_view("not a contract")
        with pytest.raises(TypeError):
            render_connection_view("not a contract")
        with pytest.raises(TypeError):
            render_connection_view(ProjectReviewContract(
                project_id=None, revision=0, project_status="REVIEW", review_count=0,
                verified_count=0, auto_count=0, confirmation_count=0, blocked_count=0,
                items=(), available_actions=(), summary="",
            ))


# =============================================================================
# Import purity — the renderer reaches only the contract and vocabulary modules.
# =============================================================================
EXPECTED_IMPORTS = {
    "dataclasses",
    "app.cad_engine.automation_gate",
    "app.cad_engine.drawing_dispatch",
    "app.cad_engine.drawing_output_verification",
    "app.cad_engine.review_contract",
    "app.cad_engine.reviewed_connection_specification",
}

FORBIDDEN_TOKENS = (
    "supabase", "anthropic", "fastapi", "cadquery", "reportlab", "ezdxf", "pypdf",
    "reviewed_connection_drawing_gate", "http", "socket",
    "310ub", "250pfc", "real-ub", "conn-real", "arkles", "con-arkles", "m12", "sq4", "Ø",
    "html", "react", "jsx", "css", "javascript",
    "resolve_project_connection", "apply_human_resolution", "dispatch_fabrication_drawing",
    "verify_drawing_artifact", "evaluate_reviewed_connection_for_automation",
    "evaluate_project_fabrication_output_gate", "build_exception_resolution_package",
    "rerun_connection_after_resolutions", "evaluate_project_for_automation",
    "generate_fabrication_drawing_from_reviewed_assembly", "evaluate_automation_gate",
    "evaluate_fabrication_output_gate", "generate_connection_fabrication_drawing_pdf",
    "build_review_report", "build_connection_review_contract", "build_project_review_contract",
)


def test_module_import_purity():
    source = Path(view_model_module.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == EXPECTED_IMPORTS
    lowered = source.lower()
    for token in FORBIDDEN_TOKENS:
        assert token not in lowered, token
    # The renderer calls nothing but its own pure helpers and dataclass classes.
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    outside = called - {
        "render_connection_view", "render_project_view", "_label", "_actions",
        "_count_phrase", "_project_summary", "_evidence_text", "_blocker_views",
        "_task_views", "_provenance_views", "sum", "len", "tuple", "isinstance",
        "type", "TypeError", "next", "sorted", "repr", "range", "enumerate",
        "ProjectReviewView", "ConnectionReviewView", "ActionView", "AttentionView",
        "BlockerView", "ConnectionIdentityView", "EvidenceView", "ExtractedValuesView",
        "ProvenanceView", "TaskView", "OutputView", "CountsView", "dataclass",
    }
    assert outside == set(), outside


def test_module_imports_without_any_secret_environment():
    env = {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("ANTHROPIC", "SUPABASE", "FIREWORKS", "OPENAI"))
    }
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import app.cad_engine.review_view_model"],
        env=env, cwd=root, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "SUPABASE" not in completed.stderr and "ANTHROPIC" not in completed.stderr
