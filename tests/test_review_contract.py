"""
Milestone 7AK — UI-READY EXCEPTION REVIEW CONTRACT.

These tests prove that `review_contract` presents the existing Arkles
project to a future UI: a read-only, immutable, deterministic projection
of what a human needs to see and do — the existing decisions/statuses/
counts, the existing blocker codes with human-readable presentation, the
existing exception-resolution tasks, the existing evidence/provenance,
and the AI's values verbatim.

The three resolutions are driven through the GENUINE 7AJ workflow (the
real 7AC contract, 7AD rerun, 7AE gate, 7AF dispatch, 7AG verification);
the contract module itself never resolves, decides or generates. The
human fixture is the SAME clearly-labelled HUMAN-SUPPLIED TEST DATA as
the 7AI/7AJ proofs.
"""

import ast
import copy
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.cad_engine.review_contract as review_contract_module
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
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE,
    AUTOMATION_WARNING_HISTORICAL_AI_MARKS,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_VERIFIED
from app.cad_engine.exception_resolution import (
    TASK_COMPLETE_REVIEW,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
)
from app.cad_engine.project_workflow import (
    ProjectConnectionState,
    ProjectWorkflowState,
    UnknownProjectPackageError,
)
from app.cad_engine.review_contract import (
    ACTION_NAMES,
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    PROVENANCE_LABELS,
    REVIEW_CONTRACT_SCOPE_STATEMENT,
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests.test_exception_resolution import ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    CONNECTION_IDENTITIES,
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _start,
)
from tests.test_real_world_exception_proof import _answer

# The genuine task order 7AC builds for each un-resolved Arkles candidate.
REV0_TASK_TYPES = (
    TASK_COMPLETE_REVIEW,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_PROVIDE_PLATE,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
)

# The blocker presentation's deterministic field hints (§5): a blocker that
# concerns one engineering field names it; whole-connection checks name none.
EXPECTED_BLOCKER_FIELDS = {
    AUTOMATION_BLOCKER_MEMBER_IDENTITY: "connected_member_marks",
    AUTOMATION_BLOCKER_POSITION: "position",
    AUTOMATION_BLOCKER_PLATE: "plate",
    AUTOMATION_BLOCKER_HOLE_DIAMETER: "holes",
    AUTOMATION_BLOCKER_LOCATION: "location",
    AUTOMATION_BLOCKER_ATTACHMENT: "attachments",
    AUTOMATION_BLOCKER_REVIEW_STATUS: None,
    AUTOMATION_BLOCKER_SPECIFICATION: None,
    AUTOMATION_BLOCKER_VALIDATION: None,
}

# The blocker -> task relation must come from the connection's ACTUAL tasks
# (§5: "related task"), so these spot checks are asserted against the real
# grouping 7AC produced, and the test also proves the contract matches the
# real grouping for every code.
EXPECTED_BLOCKER_TASK_TYPES = {
    AUTOMATION_BLOCKER_REVIEW_STATUS: TASK_COMPLETE_REVIEW,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY: TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    AUTOMATION_BLOCKER_POSITION: TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    AUTOMATION_BLOCKER_ATTACHMENT: TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    AUTOMATION_BLOCKER_PLATE: TASK_PROVIDE_PLATE,
    AUTOMATION_BLOCKER_HOLE_DIAMETER: TASK_PROVIDE_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION: TASK_PROVIDE_LOCATION,
    AUTOMATION_BLOCKER_SPECIFICATION: TASK_REVIEW_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION: TASK_REVIEW_VALIDATION,
}

SIX_ENGINEERING_FIELDS = (
    "connected_member_marks", "position", "plate", "holes", "location", "attachments",
)


@pytest.fixture(scope="module")
def revisions(tmp_path_factory):
    """Revision 0 -> 3 through the GENUINE 7AJ workflow (three real resolutions)."""
    return _full_sequence(tmp_path_factory.mktemp("7ak-review-contract"))


def _pc(workflow):
    return build_project_review_contract(workflow)


def _ci(workflow, package_id):
    return build_connection_review_contract(workflow, package_id)


def _item(pc, package_id):
    return next(item for item in pc.items if item.package_id == package_id)


def _exposed_text(item):
    """Every string the contract hands to a UI for this connection."""
    return "\n".join([
        item.summary,
        repr(item.blockers), repr(item.warnings),
        repr(item.ai_member_references), repr(item.ai_bolt_readings),
        repr(item.ai_plate_readings), repr(item.ai_weld_readings),
        repr(item.ai_malformed_readings), repr(item.ai_unrecognised_readings),
        repr(item.ai_connection_type), repr(item.ai_confidence),
        repr(item.evidence), repr(item.provenance), repr(item.tasks),
    ])


# =============================================================================
# The real Arkles capture drives every contract below.
# =============================================================================
@needs_real_capture
class TestRealArklesContract:
    # -------------------------------------------------------------------------
    # Revision 0 — three unresolved REVIEW connections.
    # -------------------------------------------------------------------------
    def test_project_contract_at_rev0(self, revisions):
        w0 = revisions[0]
        pc = _pc(w0)
        assert pc.project_id == PROJECT_ID
        assert pc.revision == 0
        assert pc.project_status == AUTOMATION_DECISION_REVIEW
        assert [item.package_id for item in pc.items] == list(CANDIDATE_IDS)
        assert (pc.review_count, pc.verified_count, pc.auto_count,
                pc.confirmation_count, pc.blocked_count) == (3, 0, 0, 0, 3)
        assert pc.available_actions == (ACTION_REVIEW, ACTION_REFRESH)
        assert REVIEW_CONTRACT_SCOPE_STATEMENT in pc.summary

    def test_connection_contracts_at_rev0(self, revisions):
        pc = _pc(revisions[0])
        for item in pc.items:
            assert item.decision == AUTOMATION_DECISION_REVIEW
            assert item.connection_id is None
            assert item.output_status is None and item.verification_status is None
            assert item.requires_action is True
            assert item.available_actions == (ACTION_REVIEW, ACTION_RESOLVE)
            assert item.generated_files == ()
            assert item.last_processed_revision is None

    def test_blockers_are_the_existing_codes_only(self, revisions):
        for item in _pc(revisions[0]).items:
            assert {b.code for b in item.blockers} == ARKLES_BLOCKER_CODES

    def test_blocker_presentation_is_human_readable_and_deterministic(self, revisions):
        for item in _pc(revisions[0]).items:
            titles = []
            by_code = {}
            for blocker in item.blockers:
                assert blocker.severity == SEVERITY_BLOCKING
                assert isinstance(blocker.title, str) and blocker.title
                assert isinstance(blocker.message, str) and blocker.message
                assert blocker.field == EXPECTED_BLOCKER_FIELDS[blocker.code]
                titles.append(blocker.title)
                by_code[blocker.code] = blocker
            # Deterministic mapping: the same code always gets the same title.
            assert len(titles) == len(set(titles))
            # A second build presents identically.
            again = _ci(revisions[0], item.package_id)
            assert [b.title for b in again.blockers] == titles

    def test_blocker_task_types_come_from_the_connections_actual_tasks(self, revisions):
        w0 = revisions[0]
        for item in _pc(w0).items:
            group = next(
                g for g in w0.exception_package.connection_tasks
                if g.review_package_id == item.package_id
            )
            # Truthfulness first: the contract's related task for a code is the
            # connection's own first task that addresses it — nothing invented.
            for blocker in item.blockers:
                expected = next(
                    (t.task_type for t in group.tasks if blocker.code in t.blocker_codes),
                    None,
                )
                assert blocker.task_type == expected
            # And the real grouping is the documented one.
            by_code = {b.code: b for b in item.blockers}
            for code, task_type in EXPECTED_BLOCKER_TASK_TYPES.items():
                assert by_code[code].task_type == task_type

    def test_warning_presentation_at_rev0(self, revisions):
        for item in _pc(revisions[0]).items:
            assert [w.code for w in item.warnings] == [AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE]
            warning = item.warnings[0]
            assert warning.severity == SEVERITY_WARNING
            assert warning.field is None and warning.task_type is None
            assert isinstance(warning.title, str) and warning.title
            assert isinstance(warning.message, str) and warning.message

    def test_contracts_are_deterministic(self, revisions):
        pc_a = _pc(revisions[0])
        pc_b = _pc(revisions[0])
        assert pc_a == pc_b
        assert _ci(revisions[0], "RP-0001") == _ci(revisions[0], "RP-0001")
        # Determinism across independently-built workflows too.
        _, _, _, fresh = _start()
        assert _pc(fresh) == pc_a

    def test_contracts_are_frozen_and_mutation_safe(self, revisions):
        item = _ci(revisions[0], "RP-0001")
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.decision = AUTOMATION_DECISION_AUTO
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.blockers[0].message = "changed"
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.tasks[0].resolved = True
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.evidence.source_page = 9
        with pytest.raises(AttributeError):
            item.tasks.append(item.tasks[0])
        with pytest.raises(AttributeError):
            item.blockers[0].code = "NEW_CODE"

    def test_ai_values_are_lossless_verbatim(self, revisions):
        rp1 = _ci(revisions[0], "RP-0001")
        rp2 = _ci(revisions[0], "RP-0002")
        rp3 = _ci(revisions[0], "RP-0003")
        # The nominal bolt designation stays EXACTLY as extracted.
        assert any("'M12'" in reading for reading in rp1.ai_bolt_readings)
        assert any("'M12'" in reading for reading in rp2.ai_bolt_readings)
        assert any("SQ4 12mm" in reading for reading in rp3.ai_bolt_readings)
        assert rp1.ai_connection_type == "bolted"
        assert rp3.ai_connection_type == "bolted"
        assert rp1.ai_confidence == "65"
        assert rp3.ai_confidence == "55"
        # The holes task's current AI value carries the same verbatim reading.
        holes_task = next(t for t in rp1.tasks if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        assert "'M12'" in holes_task.current_value
        # Member references stay the raw extraction marks.
        assert "stringer 140x45 H4 SG8" in repr(rp1.ai_member_references)

    def test_no_inference_or_conversion(self, revisions):
        for item in _pc(revisions[0]).items:
            text = _exposed_text(item)
            assert "Ø" not in text
            # No VALUE carries a derived engineering quantity; the task's own
            # evidence_requirement wording is 7AC's verbatim guidance, not data.
            values = repr([t.current_value for t in item.tasks]) + repr(
                [t.resolution_value for t in item.tasks]
            )
            assert "diameter_mm" not in values
            assert "Ø" not in values
        # Values the AI did not provide are missing — not guessed.
        rp1 = _ci(revisions[0], "RP-0001")
        plate_task = next(t for t in rp1.tasks if t.task_type == TASK_PROVIDE_PLATE)
        location_task = next(t for t in rp1.tasks if t.task_type == TASK_PROVIDE_LOCATION)
        assert plate_task.current_value is None
        assert location_task.current_value is None

    def test_evidence_is_passed_through_not_invented(self, revisions):
        w0 = revisions[0]
        for item in _pc(w0).items:
            candidate = next(
                c for c in w0.collection.candidates if c.review_package_id == item.package_id
            )
            extraction = candidate.package.extraction
            evidence = item.evidence
            assert evidence.source_drawing_id == extraction.source_drawing_id == SOURCE_DRAWING_ID
            assert evidence.drawing_number == extraction.drawing_number
            assert evidence.source_page == extraction.source_page == 7
            assert evidence.detail_reference is None and extraction.detail_reference is None
            assert evidence.grid_reference is None and extraction.grid_reference is None

    def test_tasks_are_the_existing_exception_tasks(self, revisions):
        w0 = revisions[0]
        for item in _pc(w0).items:
            assert [t.task_type for t in item.tasks] == list(REV0_TASK_TYPES)
            group = next(
                g for g in w0.exception_package.connection_tasks
                if g.review_package_id == item.package_id
            )
            # Same tasks, same ids, same questions, same order.
            assert [t.task_id for t in item.tasks] == [t.task_id for t in group.tasks]
            assert [t.description for t in item.tasks] == [t.question for t in group.tasks]
            assert all(isinstance(t.description, str) and t.description for t in item.tasks)
            # Blocking tasks are required; the identity task is not.
            for task in item.tasks:
                assert task.required == bool(
                    next(g for g in group.tasks if g.task_id == task.task_id).blocker_codes
                )
            identity = next(t for t in item.tasks if t.task_type == TASK_PROVIDE_CONNECTION_IDENTITY)
            assert identity.required is False
            # The grouped member/position/attachment task offers the real marks.
            grouped = next(
                t for t in item.tasks if t.task_type == TASK_SELECT_MEMBER_POSITION_ATTACHMENT
            )
            assert grouped.allowed_options == ("L2", "L3")
            # Unresolved at rev 0.
            assert all(not t.resolved for t in item.tasks)
            assert all(t.resolution_value is None for t in item.tasks)
            assert all(t.resolution_evidence is None for t in item.tasks)
            # Every blocker code is addressed by at least one exposed task.
            assert set().union(*(t.blocker_codes for t in group.tasks)) == ARKLES_BLOCKER_CODES

    def test_actions_never_bypass_the_workflow(self, revisions):
        assert set(ACTION_NAMES) == {ACTION_REVIEW, ACTION_RESOLVE, ACTION_REFRESH}
        for workflow in revisions:
            pc = _pc(workflow)
            assert set(pc.available_actions) <= set(ACTION_NAMES)
            for item in pc.items:
                assert set(item.available_actions) <= set(ACTION_NAMES)

    # -------------------------------------------------------------------------
    # Revisions 1-3 — the three GENUINE resolutions.
    # -------------------------------------------------------------------------
    def test_rev1_first_resolution_via_genuine_workflow(self, revisions):
        pc = _pc(revisions[1])
        rp1 = _item(pc, "RP-0001")
        assert rp1.decision == AUTOMATION_DECISION_AUTO
        assert rp1.connection_id == CONNECTION_IDENTITIES["RP-0001"]
        assert rp1.output_status == OUTPUT_STATUS_GENERATED
        assert rp1.verification_status == VERIFICATION_STATUS_VERIFIED
        assert rp1.requires_action is False
        assert rp1.available_actions == ()
        # The workflow's recorded artifact paths, mirrored verbatim.
        assert [Path(f).name for f in rp1.generated_files] == ["CONN-ARKLES-001-fabrication.pdf"]
        assert rp1.last_processed_revision == 1
        # The other two are untouched.
        for package_id in ("RP-0002", "RP-0003"):
            other = _item(pc, package_id)
            assert other.decision == AUTOMATION_DECISION_REVIEW
            assert other.requires_action is True
            assert other.output_status is None

    def test_rev1_provenance_is_human_supplemented(self, revisions):
        rp1 = _ci(revisions[1], "RP-0001")
        assert [p.field for p in rp1.provenance] == list(SIX_ENGINEERING_FIELDS)
        assert all(p.provenance == PROVENANCE_HUMAN_SUPPLEMENTED for p in rp1.provenance)
        # The connection identity the human provided is never a provenance field.
        assert "connection_id" not in {p.field for p in rp1.provenance}

    def test_rev1_tasks_show_the_human_resolution(self, revisions):
        rp1 = _ci(revisions[1], "RP-0001")
        group = next(
            g for g in revisions[1].exception_package.connection_tasks
            if g.review_package_id == "RP-0001"
        )
        example_evidence = _answer(group.tasks[0], None).evidence
        assert all(t.resolved for t in rp1.tasks)
        assert all(t.resolution_evidence == example_evidence for t in rp1.tasks)
        grouped = next(
            t for t in rp1.tasks if t.task_type == TASK_SELECT_MEMBER_POSITION_ATTACHMENT
        )
        assert "'L2'" in grouped.resolution_value and "'L3'" in grouped.resolution_value
        identity = next(
            t for t in rp1.tasks if t.task_type == TASK_PROVIDE_CONNECTION_IDENTITY
        )
        assert identity.resolution_value == "'CONN-ARKLES-001'"

    def test_rev1_warnings_gain_historical_ai_marks(self, revisions):
        rp1 = _ci(revisions[1], "RP-0001")
        assert {w.code for w in rp1.warnings} == {
            AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE,
            AUTOMATION_WARNING_HISTORICAL_AI_MARKS,
        }
        assert all(w.severity == SEVERITY_WARNING for w in rp1.warnings)

    def test_rev2_second_resolution(self, revisions):
        pc = _pc(revisions[2])
        assert pc.project_status == AUTOMATION_DECISION_REVIEW
        assert (pc.review_count, pc.verified_count, pc.auto_count,
                pc.confirmation_count, pc.blocked_count) == (1, 2, 2, 0, 1)
        assert pc.available_actions == (ACTION_REVIEW, ACTION_REFRESH)
        rp2 = _item(pc, "RP-0002")
        assert rp2.decision == AUTOMATION_DECISION_AUTO
        assert rp2.connection_id == CONNECTION_IDENTITIES["RP-0002"]
        assert rp2.verification_status == VERIFICATION_STATUS_VERIFIED
        assert rp2.last_processed_revision == 2
        assert _item(pc, "RP-0003").requires_action is True

    def test_rev3_all_resolved(self, revisions):
        pc = _pc(revisions[3])
        assert pc.project_status == AUTOMATION_DECISION_AUTO
        assert (pc.review_count, pc.verified_count, pc.auto_count,
                pc.confirmation_count, pc.blocked_count) == (0, 3, 3, 0, 0)
        assert pc.available_actions == (ACTION_REFRESH,)
        assert all(not item.requires_action for item in pc.items)
        for item in pc.items:
            assert item.verification_status == VERIFICATION_STATUS_VERIFIED
            assert item.available_actions == ()

    # -------------------------------------------------------------------------
    # The contract mirrors the workflow verbatim, at every revision.
    # -------------------------------------------------------------------------
    def test_counts_and_status_match_the_workflow_verbatim(self, revisions):
        for workflow in revisions:
            pc = _pc(workflow)
            assert pc.project_status == workflow.project_decision
            assert pc.review_count == workflow.review_count
            assert pc.verified_count == workflow.verified_count
            assert pc.auto_count == workflow.auto_count
            assert pc.confirmation_count == workflow.confirm_count
            assert pc.blocked_count == workflow.blocked_count
            assert pc.project_id == workflow.project_id
            assert pc.revision == workflow.revision
            for item, state in zip(pc.items, workflow.connections):
                assert item.package_id == state.package_id
                assert item.decision == state.decision
                assert item.connection_id == state.connection_id
                assert item.output_status == state.output_status
                assert item.verification_status == state.verification_status
                assert item.generated_files == state.generated_files
                assert item.last_processed_revision == state.last_processed_revision
                assert tuple(b.code for b in item.blockers) == state.blockers
                assert tuple(w.code for w in item.warnings) == state.warnings

    def test_builders_never_mutate_the_workflow(self, revisions):
        w0 = revisions[0]
        tasks_before = copy.deepcopy(w0.exception_package.connection_tasks)
        extraction_before = copy.deepcopy(
            [c.package.extraction for c in w0.collection.candidates]
        )
        for workflow in revisions:
            _pc(workflow)
        assert w0.exception_package.connection_tasks == tasks_before
        assert [c.package.extraction for c in w0.collection.candidates] == extraction_before

    def test_provenance_vocabulary_is_the_existing_one(self, revisions):
        assert PROVENANCE_LABELS == (
            PROVENANCE_AI_EXTRACTED,
            PROVENANCE_HUMAN_REVIEWED,
            PROVENANCE_HUMAN_SUPPLEMENTED,
        )
        rp1 = _ci(revisions[1], "RP-0001")
        assert all(p.provenance in PROVENANCE_LABELS for p in rp1.provenance)

    # -------------------------------------------------------------------------
    # Safety: unknown packages, bad types, unsupported states.
    # -------------------------------------------------------------------------
    def test_unknown_package_raises_the_existing_error(self, revisions):
        with pytest.raises(UnknownProjectPackageError):
            _ci(revisions[0], "RP-9999")

    def test_bad_types_raise_type_errors(self, revisions):
        with pytest.raises(TypeError):
            build_project_review_contract("not a workflow state")
        with pytest.raises(TypeError):
            build_connection_review_contract("not a workflow state", "RP-0001")
        with pytest.raises(TypeError):
            build_connection_review_contract(revisions[0], 42)

    def test_states_without_the_workflows_payload_are_refused(self):
        bare = ProjectWorkflowState(
            project_id="P", revision=0,
            connections=(
                ProjectConnectionState(
                    "RP-1", None, AUTOMATION_DECISION_REVIEW, None, None, (), (), (), None,
                ),
            ),
            project_decision=AUTOMATION_DECISION_REVIEW,
            generated_files=(), review_count=1, confirm_count=0,
            auto_count=0, verified_count=0, blocked_count=1, summary="",
        )
        with pytest.raises(ValueError):
            build_connection_review_contract(bare, "RP-1")
        with pytest.raises(ValueError):
            build_project_review_contract(bare)


# =============================================================================
# Import purity — the presentation layer reaches nothing but the contract
# modules and cannot accidentally run the pipeline chain.
# =============================================================================
EXPECTED_IMPORTS = {
    "dataclasses",
    "app.cad_engine.automation_gate",
    "app.cad_engine.exception_resolution",
    "app.cad_engine.project_connection_review",
    "app.cad_engine.project_workflow",
    "app.cad_engine.reviewed_connection_specification",
}

FORBIDDEN_TOKENS = (
    "supabase", "anthropic", "fastapi", "cadquery", "reportlab", "ezdxf", "pypdf",
    "reviewed_connection_drawing_gate", "http", "socket",
    "310ub", "250pfc", "real-ub", "conn-real", "arkles", "con-arkles", "m12", "sq4", "Ø",
    # The chain entry points this read-only layer must never invoke:
    "resolve_project_connection", "apply_human_resolution", "dispatch_fabrication_drawing",
    "verify_drawing_artifact", "evaluate_reviewed_connection_for_automation",
    "evaluate_project_fabrication_output_gate", "build_exception_resolution_package",
    "rerun_connection_after_resolutions", "evaluate_project_for_automation",
    "generate_fabrication_drawing_from_reviewed_assembly", "evaluate_automation_gate",
    "evaluate_fabrication_output_gate", "generate_connection_fabrication_drawing_pdf",
)

FORBIDDEN_CALLS = {
    "resolve_project_connection", "apply_human_resolution", "dispatch_fabrication_drawing",
    "verify_drawing_artifact", "evaluate_reviewed_connection_for_automation",
    "evaluate_project_fabrication_output_gate", "build_exception_resolution_package",
    "rerun_connection_after_resolutions", "evaluate_project_for_automation",
    "generate_fabrication_drawing_from_reviewed_assembly", "evaluate_automation_gate",
    "evaluate_fabrication_output_gate", "generate_connection_fabrication_drawing_pdf",
}


def test_module_import_purity():
    source = Path(review_contract_module.__file__).read_text()
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports == EXPECTED_IMPORTS
    lowered = source.lower()
    for token in FORBIDDEN_TOKENS:
        assert token not in lowered, f"forbidden token {token!r} in review_contract.py"
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "build_review_report" in called
    assert not (FORBIDDEN_CALLS & called)


def test_subprocess_import_requires_no_secrets():
    env = {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("ANTHROPIC", "SUPABASE", "FIREWORKS", "OPENAI"))
    }
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import app.cad_engine.review_contract"],
        env=env, cwd=root, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "SUPABASE" not in completed.stderr and "ANTHROPIC" not in completed.stderr
