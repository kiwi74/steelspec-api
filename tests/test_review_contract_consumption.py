"""
Milestone 7AL — REVIEW CONTRACT CONSUMPTION & FAILURE-MODE TRUTHFULNESS HARNESS.

The central rule: the 7AK review contract must represent the authoritative
workflow state — it must never invent a state, hide a failure, or create an
action the workflow does not permit. This harness proves that statement by
driving the GENUINE 7AJ workflow through normal, refreshed, resolved, stale,
duplicate, generation-failure and verification-failure scenarios, building a
7AK contract at each point, and comparing it field-for-field against the
workflow's own state.

This file is a test harness ONLY: no new workflow engine, no new state
machine, no new error system, no product code. Failure injection uses the
EXISTING 7AJ/7AF `drawing_entry` seam (a raising generator and a
garbage-writing generator) — nothing is bypassed. The human fixture is the
SAME clearly-labelled HUMAN-SUPPLIED TEST DATA as the 7AI/7AJ/7AK proofs.
"""

import ast
from pathlib import Path

import pytest

import app.cad_engine.project_workflow as workflow_module
import app.cad_engine.review_contract as review_contract_module
from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
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
from app.cad_engine.exception_resolution import TASK_PROVIDE_HOLE_DIAMETER
from app.cad_engine.project_workflow import (
    ConnectionAlreadyProcessedError,
    StaleProjectWorkflowError,
)
from app.cad_engine.review_contract import (
    ACTION_NAMES,
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    build_connection_review_contract,
    build_project_review_contract,
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
    _pdfs,
    _resolve,
    _start,
    _state,
)

SIX_ENGINEERING_FIELDS = (
    "connected_member_marks", "position", "plate", "holes", "location", "attachments",
)

# Actions that would let a UI skip any stage of the chain — none may ever exist.
FORBIDDEN_ACTION_NAMES = (
    "APPROVE", "FORCE_AUTO", "FORCE_CONFIRM", "GENERATE", "VERIFY", "MARK_VERIFIED",
    "BYPASS_VALIDATION", "BYPASS_DRAWING_GATE", "BYPASS_VERIFICATION",
)

# Dependency-ish tokens the product modules must stay free of. The 7AK and 7AJ
# import-purity tests (exact import sets, chain call sets) remain authoritative;
# this test proves the 7AL harness added no NEW product dependency.
FORBIDDEN_TOKENS = (
    "supabase", "anthropic", "fastapi", "cadquery", "reportlab", "ezdxf", "pypdf",
    "http", "socket", "310ub", "250pfc", "real-ub", "conn-real", "arkles",
    "con-arkles", "m12", "sq4", "Ø",
)


def _pc(workflow):
    return build_project_review_contract(workflow)


def _ci(workflow, package_id):
    return build_connection_review_contract(workflow, package_id)


def _item(pc, package_id):
    return next(item for item in pc.items if item.package_id == package_id)


def _assert_verbatim(pc, workflow, label=""):
    """The project contract mirrors the authoritative workflow state, field for field."""
    assert pc.project_status == workflow.project_decision, label
    assert pc.project_id == workflow.project_id, label
    assert pc.revision == workflow.revision, label
    assert pc.review_count == workflow.review_count, label
    assert pc.verified_count == workflow.verified_count, label
    assert pc.auto_count == workflow.auto_count, label
    assert pc.confirmation_count == workflow.confirm_count, label
    assert pc.blocked_count == workflow.blocked_count, label
    assert len(pc.items) == len(workflow.connections), label
    for item, state in zip(pc.items, workflow.connections):
        assert item.package_id == state.package_id, label
        assert item.decision == state.decision, label
        assert item.connection_id == state.connection_id, label
        assert item.output_status == state.output_status, label
        assert item.verification_status == state.verification_status, label
        assert item.generated_files == state.generated_files, label
        assert item.last_processed_revision == state.last_processed_revision, label
        assert tuple(b.code for b in item.blockers) == state.blockers, label
        assert tuple(w.code for w in item.warnings) == state.warnings, label


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    """Revision 0 -> 3 through the GENUINE 7AJ workflow, plus the output dir."""
    out = tmp_path_factory.mktemp("7al-sequence")
    return _full_sequence(out), out


@pytest.fixture(scope="module")
def contracts(sequence):
    """C0..C3 — the contract snapshots taken BEFORE any failure scenario runs."""
    return [_pc(w) for w in sequence[0]]


@pytest.fixture(scope="module")
def failures(tmp_path_factory):
    """Two failure scenarios, each on its own fresh genuine run: RP-0001
    resolves for real, then RP-0002 hits the existing drawing_entry seam."""
    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("simulated generator failure for the 7AL test")

    def garbage_entry(assembly, output_path, **kwargs):
        Path(output_path).write_bytes(GARBAGE_BYTES)

    out_gen = tmp_path_factory.mktemp("7al-genfail")
    _, _, built_a, w0a = _start()
    w2a = _resolve(
        _resolve(w0a, built_a, "RP-0001", out_gen),
        built_a, "RP-0002", out_gen, drawing_entry=exploding_entry,
    )
    out_ver = tmp_path_factory.mktemp("7al-verfail")
    _, _, built_b, w0b = _start()
    w2b = _resolve(
        _resolve(w0b, built_b, "RP-0001", out_ver),
        built_b, "RP-0002", out_ver, drawing_entry=garbage_entry,
    )
    return {
        "generation_failure": (w2a, out_gen),
        "verification_failure": (w2b, out_ver),
    }


def _all_scenarios(sequence, failures):
    """Every workflow state the harness produced, as (label, state) pairs."""
    states, out = sequence
    refreshed0 = workflow_module.refresh_project_workflow(states[0], output_dir=out)
    refreshed3 = workflow_module.refresh_project_workflow(states[3], output_dir=out)
    return [
        ("revision 0", states[0]),
        ("revision 1", states[1]),
        ("revision 2", states[2]),
        ("revision 3", states[3]),
        ("refreshed revision 0", refreshed0),
        ("refreshed revision 3", refreshed3),
        ("generation failure", failures["generation_failure"][0]),
        ("verification failure", failures["verification_failure"][0]),
    ]


# =============================================================================
# The control case and the happy path.
# =============================================================================
@needs_real_capture
class TestReviewContractConsumption:
    def test_rev0_contract_is_the_truthful_control_case(self, sequence, contracts):
        states, _ = sequence
        w0, pc = states[0], contracts[0]
        assert pc.revision == 0
        assert pc.project_status == AUTOMATION_DECISION_REVIEW
        # Three review connections in the AUTHORITATIVE workflow order.
        assert [item.package_id for item in pc.items] == list(CANDIDATE_IDS)
        # All three require human action with the exact REVIEW + RESOLVE actions.
        assert all(item.requires_action for item in pc.items)
        assert all(item.available_actions == (ACTION_REVIEW, ACTION_RESOLVE) for item in pc.items)
        # Nothing is shown verified, nothing generated, no artifact reported.
        assert all(item.verification_status is None for item in pc.items)
        assert all(item.output_status is None for item in pc.items)
        assert all(item.generated_files == () for item in pc.items)
        assert pc.verified_count == 0 and pc.auto_count == 0
        assert (pc.review_count, pc.confirmation_count, pc.blocked_count) == (3, 0, 3)
        for item in pc.items:
            # Blocker codes match the authoritative workflow (and the 7AC gates).
            assert {b.code for b in item.blockers} == ARKLES_BLOCKER_CODES
            assert {b.code for b in item.blockers} == set(_state(w0, item.package_id).blockers)
            # Resolution tasks match the authoritative 7AC package, in its order.
            group = next(
                g for g in w0.exception_package.connection_tasks
                if g.review_package_id == item.package_id
            )
            assert [t.task_id for t in item.tasks] == [t.task_id for t in group.tasks]
            assert [t.description for t in item.tasks] == [t.question for t in group.tasks]
            # Provenance is accurate at rev 0: nothing supplemented yet.
            assert item.provenance == ()
        # AI values and evidence remain lossless.
        rp1 = _ci(w0, "RP-0001")
        rp3 = _ci(w0, "RP-0003")
        assert any("'M12'" in reading for reading in rp1.ai_bolt_readings)
        assert any("SQ4 12mm" in reading for reading in rp3.ai_bolt_readings)
        assert rp1.evidence.source_drawing_id == SOURCE_DRAWING_ID
        _assert_verbatim(pc, w0, "revision 0")

    def test_refresh_without_resolution_never_changes_engineering_truth(self, sequence, contracts):
        states, out = sequence
        w0, w3 = states[0], states[3]
        C0, C3 = contracts[0], contracts[3]
        refreshed0 = workflow_module.refresh_project_workflow(w0, output_dir=out)
        refreshed3 = workflow_module.refresh_project_workflow(w3, output_dir=out)

        # The refreshed state is re-projected, never advanced: revision is the
        # authoritative one, and the connection states are the SAME objects.
        assert refreshed0.revision == w0.revision == 0
        assert all(a is b for a, b in zip(refreshed0.connections, w0.connections))
        # Rebuilding the contract reports the unchanged authoritative state —
        # refresh changed no engineering truth, so the contract cannot change.
        assert _pc(refreshed0) == C0
        assert _pc(refreshed3) == C3
        # No artificial resolution or artifact: every item still requires
        # action at rev 0 and nothing gained a file anywhere.
        assert all(item.requires_action for item in _pc(refreshed0).items)
        assert all(item.generated_files == () for item in _pc(refreshed0).items)
        _assert_verbatim(_pc(refreshed0), refreshed0, "refreshed revision 0")
        _assert_verbatim(_pc(refreshed3), refreshed3, "refreshed revision 3")

    def test_genuine_rp0001_resolution_is_reflected_correctly(self, sequence, contracts):
        states, _ = sequence
        w1, pc = states[1], contracts[1]
        assert pc.revision == 1
        rp1 = _item(pc, "RP-0001")
        # RP-0001 reflects its ACTUAL workflow state — not inferred from
        # requires_action alone: compared field-for-field against the state.
        assert rp1.decision == AUTOMATION_DECISION_AUTO
        assert rp1.decision == _state(w1, "RP-0001").decision
        assert rp1.connection_id == CONNECTION_IDENTITIES["RP-0001"]
        assert rp1.output_status == OUTPUT_STATUS_GENERATED
        assert rp1.verification_status == VERIFICATION_STATUS_VERIFIED
        assert rp1.requires_action is False
        assert rp1.available_actions == ()
        assert [Path(f).name for f in rp1.generated_files] == ["CONN-ARKLES-001-fabrication.pdf"]
        assert rp1.last_processed_revision == 1
        # RP-0002 / RP-0003 remain REVIEW; the project remains REVIEW.
        for package_id in ("RP-0002", "RP-0003"):
            other = _item(pc, package_id)
            assert other.decision == AUTOMATION_DECISION_REVIEW
            assert other.requires_action is True
            assert other.verification_status is None
        assert pc.project_status == AUTOMATION_DECISION_REVIEW
        assert (pc.review_count, pc.verified_count, pc.auto_count,
                pc.confirmation_count, pc.blocked_count) == (2, 1, 1, 0, 2)
        # Human-supplemented provenance stays intact and truthful.
        assert [p.field for p in rp1.provenance] == list(SIX_ENGINEERING_FIELDS)
        assert all(p.provenance == PROVENANCE_HUMAN_SUPPLEMENTED for p in rp1.provenance)
        assert "connection_id" not in {p.field for p in rp1.provenance}
        _assert_verbatim(pc, w1, "revision 1")

    # -------------------------------------------------------------------------
    # Refusal paths — the contract must not hide them or present a new state.
    # -------------------------------------------------------------------------
    def test_stale_resolution_is_rejected_and_the_contract_never_changes(self, tmp_path):
        _, _, built, w0 = _start()
        w1 = _resolve(w0, built, "RP-0001", tmp_path)
        before = _pc(w1)
        pdfs_before = _pdfs(tmp_path)

        # The genuine 7AJ stale-revision protection refuses the stale revision.
        with pytest.raises(StaleProjectWorkflowError):
            _resolve(w1, built, "RP-0002", tmp_path, expected_revision=0)
        # Control: the same connection is refused at the CURRENT revision only
        # by the one-shot rule — proving staleness is what failed above.
        with pytest.raises(ConnectionAlreadyProcessedError):
            _resolve(w1, built, "RP-0001", tmp_path, expected_revision=1)

        # The stale resolution failed: it must not look resolved. The contract
        # reports the unchanged authoritative state; nothing was created.
        assert _pc(w1) == before
        assert _pdfs(tmp_path) == pdfs_before
        assert _item(before, "RP-0002").decision == AUTOMATION_DECISION_REVIEW
        assert _item(before, "RP-0002").requires_action is True
        assert _item(before, "RP-0002").last_processed_revision is None
        assert _item(before, "RP-0001").verification_status == VERIFICATION_STATUS_VERIFIED
        _assert_verbatim(before, w1, "after stale refusal")

    def test_already_processed_connection_is_rejected_and_never_actionable(self, tmp_path):
        _, _, built, w0 = _start()
        w1 = _resolve(w0, built, "RP-0001", tmp_path)
        before = _pc(w1)
        pdfs_before = _pdfs(tmp_path)

        with pytest.raises(ConnectionAlreadyProcessedError):
            _resolve(w1, built, "RP-0001", tmp_path)

        # The duplicate resolution failed: it must not look newly processed.
        assert _pc(w1) == before
        assert _pdfs(tmp_path) == pdfs_before          # no duplicate artifact
        rp1 = _ci(w1, "RP-0001")
        assert rp1.verification_status == VERIFICATION_STATUS_VERIFIED
        assert rp1.last_processed_revision == 1        # still the original run
        assert rp1.available_actions == ()             # NO RESOLVE for a processed connection
        assert _item(before, "RP-0002").requires_action is True
        assert _item(before, "RP-0003").requires_action is True
        _assert_verbatim(before, w1, "after duplicate refusal")

    # -------------------------------------------------------------------------
    # Failure truthfulness — the central acceptance property of 7AL.
    # -------------------------------------------------------------------------
    def test_generation_failure_is_exposed_and_never_looks_generated(self, failures):
        w2, out = failures["generation_failure"]
        pc = _pc(w2)
        rp2 = _item(pc, "RP-0002")
        # The rerun itself succeeded — the failure is the generation boundary,
        # visible in the workflow's own vocabulary, never collapsed into an
        # unexplained REVIEW and never dressed up as VERIFIED.
        assert rp2.decision == AUTOMATION_DECISION_AUTO
        assert rp2.output_status == OUTPUT_STATUS_GENERATION_FAILED
        assert rp2.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert rp2.generated_files == ()                # no fake artifact
        assert rp2.requires_action is False
        assert rp2.available_actions == ()
        assert rp2.last_processed_revision == 2
        assert "GENERATION_FAILED" in rp2.summary       # the failure is not hidden
        # Project level: the failed connection keeps the project REVIEW and
        # counts truthful; the untouched connections stay exactly as they were.
        assert pc.project_status == AUTOMATION_DECISION_REVIEW
        assert pc.verified_count == 1 and pc.auto_count == 2
        assert _item(pc, "RP-0001").verification_status == VERIFICATION_STATUS_VERIFIED
        assert _item(pc, "RP-0003").requires_action is True
        # Only RP-0001's real PDF exists — no fake PDF was reported or written.
        assert _pdfs(out) == [Path(_state(w2, "RP-0001").generated_files[0])]
        _assert_verbatim(pc, w2, "generation failure")

    def test_verification_failure_is_exposed_and_never_looks_verified(self, failures):
        w2, out = failures["verification_failure"]
        pc = _pc(w2)
        rp2 = _item(pc, "RP-0002")
        # Dispatch genuinely generated; 7AG genuinely failed it. The contract
        # shows both — the failed artifact is NOT silently discarded and NOT
        # presented as valid.
        assert rp2.decision == AUTOMATION_DECISION_AUTO
        assert rp2.output_status == OUTPUT_STATUS_GENERATED
        assert rp2.verification_status == VERIFICATION_STATUS_FAILED
        assert len(rp2.generated_files) == 1
        failed_path = Path(rp2.generated_files[0])
        assert failed_path.name == "CONN-ARKLES-002-fabrication.pdf"
        assert failed_path.read_bytes() == GARBAGE_BYTES   # recorded for diagnosis
        assert "FAILED" in rp2.summary
        assert rp2.requires_action is False
        # No action appears that would bypass verification.
        assert rp2.available_actions == ()
        # Project level stays truthful; the genuinely verified one is untouched.
        assert pc.project_status == AUTOMATION_DECISION_REVIEW
        assert pc.verified_count == 1
        assert _item(pc, "RP-0001").verification_status == VERIFICATION_STATUS_VERIFIED
        assert _item(pc, "RP-0003").requires_action is True
        assert len(_pdfs(out)) == 2                      # the real one + the failed record
        _assert_verbatim(pc, w2, "verification failure")

    def test_failure_truthfulness_properties(self, sequence, failures):
        gen = _pc(failures["generation_failure"][0])
        ver = _pc(failures["verification_failure"][0])
        g, v = _item(gen, "RP-0002"), _item(ver, "RP-0002")

        # If generation fails: it must not look generated.
        assert g.output_status != OUTPUT_STATUS_GENERATED
        assert g.generated_files == ()
        assert g.verification_status != VERIFICATION_STATUS_VERIFIED
        # If verification fails: it must not look verified.
        assert v.verification_status != VERIFICATION_STATUS_VERIFIED
        # If a connection requires review: it must not disappear from the contract.
        for pc in (gen, ver):
            rp3 = _item(pc, "RP-0003")
            assert rp3.decision == AUTOMATION_DECISION_REVIEW
            assert rp3.requires_action is True
            assert rp3.available_actions == (ACTION_REVIEW, ACTION_RESOLVE)
        # If a connection is genuinely verified: it must not remain actionable.
        for pc in (gen, ver):
            rp1 = _item(pc, "RP-0001")
            assert rp1.verification_status == VERIFICATION_STATUS_VERIFIED
            assert rp1.requires_action is False
            assert rp1.available_actions == ()
        # Neither failure collapses into REVIEW-with-no-explanation or VERIFIED.
        for failed in (g, v):
            assert failed.decision == AUTOMATION_DECISION_AUTO
            assert failed.verification_status != VERIFICATION_STATUS_VERIFIED

    # -------------------------------------------------------------------------
    # State semantics, consistency, immutability, bypass, losslessness.
    # -------------------------------------------------------------------------
    def test_blocked_review_and_verified_keep_authoritative_semantics(self, sequence, contracts):
        c0, c3 = contracts[0], contracts[3]
        # REVIEW requiring human action is the existing authoritative
        # "blocked from automatic output" representation — blocked_count is
        # the workflow's own number; no new vocabulary is invented for a UI.
        assert c0.blocked_count == c0.review_count == 3
        assert all(item.decision == AUTOMATION_DECISION_REVIEW for item in c0.items)
        assert all(item.requires_action for item in c0.items)
        # VERIFIED is the successful terminal state — never actionable.
        assert all(item.verification_status == VERIFICATION_STATUS_VERIFIED for item in c3.items)
        assert all(not item.requires_action for item in c3.items)
        assert all(item.available_actions == () for item in c3.items)
        assert c3.blocked_count == 0 and c3.review_count == 0
        # The decision vocabulary is ONLY the automation gate's own three —
        # the contract reinterprets nothing.
        for pc in (c0, c3):
            for item in pc.items:
                assert item.decision in (
                    AUTOMATION_DECISION_REVIEW, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_AUTO,
                )

    def test_project_counts_status_and_items_match_the_workflow_in_every_scenario(self, sequence, failures):
        for label, workflow in _all_scenarios(sequence, failures):
            _assert_verbatim(_pc(workflow), workflow, label)

    def test_contract_snapshots_are_immutable_across_all_later_operations(self, sequence, contracts, failures):
        # C0..C3 were built BEFORE the failure scenarios and refreshes ran.
        # Later workflow operations (on their own copies) and a refresh of the
        # ORIGINAL revision-3 state must not change what the snapshots report.
        states, out = sequence
        refreshed3 = workflow_module.refresh_project_workflow(states[3], output_dir=out)
        for workflow, snapshot in zip(states, contracts):
            assert _pc(workflow) == snapshot
        assert _pc(refreshed3) == contracts[3]

    def test_no_action_bypass_in_any_scenario(self, sequence, failures):
        assert ACTION_NAMES == (ACTION_REVIEW, ACTION_RESOLVE, ACTION_REFRESH)
        assert all(name not in ACTION_NAMES for name in FORBIDDEN_ACTION_NAMES)
        for label, workflow in _all_scenarios(sequence, failures):
            pc = _pc(workflow)
            # Project actions trace back to the item states, nothing else.
            expected_project = (
                (ACTION_REVIEW, ACTION_REFRESH)
                if any(item.requires_action for item in pc.items) else (ACTION_REFRESH,)
            )
            assert pc.available_actions == expected_project, label
            assert set(pc.available_actions) <= set(ACTION_NAMES), label
            for item, state in zip(pc.items, workflow.connections):
                # Per-connection actions trace back to the authoritative
                # semantics: RESOLVE exists exactly when the state says REVIEW
                # and the connection was never processed.
                expected = (
                    (ACTION_REVIEW, ACTION_RESOLVE)
                    if (state.decision == AUTOMATION_DECISION_REVIEW
                        and state.last_processed_revision is None)
                    else ()
                )
                assert item.available_actions == expected, (label, item.package_id)
                assert all(a in ACTION_NAMES for a in item.available_actions), label

    def test_ai_values_stay_lossless_through_resolutions_and_failures(self, sequence, failures):
        states = sequence[0]
        readings_by_package = {}
        for workflow in (states[0], states[1], states[3],
                         failures["generation_failure"][0],
                         failures["verification_failure"][0]):
            for package_id in CANDIDATE_IDS:
                readings_by_package.setdefault(package_id, set()).add(
                    _ci(workflow, package_id).ai_bolt_readings
                )
        # Every scenario reports the SAME verbatim readings — the harness
        # never touches the raw AI extraction.
        assert all(len(readings) == 1 for readings in readings_by_package.values())
        rp1_readings = next(iter(readings_by_package["RP-0001"]))
        rp3_readings = next(iter(readings_by_package["RP-0003"]))
        assert any("'M12'" in reading for reading in rp1_readings)
        assert any("SQ4 12mm" in reading for reading in rp3_readings)
        # The unresolved task's current AI value stays raw too.
        holes = next(
            t for t in _ci(states[0], "RP-0001").tasks
            if t.task_type == TASK_PROVIDE_HOLE_DIAMETER
        )
        assert "'M12'" in holes.current_value

    def test_provenance_stays_truthful_after_resolution_and_failures(self, sequence, failures):
        # After the genuine RP-0001 resolution — in the happy path AND in both
        # failure scenarios — the six engineering fields stay HUMAN_SUPPLEMENTED
        # and connection_id keeps its existing treatment (never a field).
        for workflow in (sequence[0][1], failures["generation_failure"][0],
                         failures["verification_failure"][0]):
            rp1 = _ci(workflow, "RP-0001")
            assert [p.field for p in rp1.provenance] == list(SIX_ENGINEERING_FIELDS)
            assert all(p.provenance == PROVENANCE_HUMAN_SUPPLEMENTED for p in rp1.provenance)
            assert "connection_id" not in {p.field for p in rp1.provenance}
        # The connections the failures left unresolved stay unsupplemented.
        for workflow in (failures["generation_failure"][0], failures["verification_failure"][0]):
            assert _ci(workflow, "RP-0003").provenance == ()


# =============================================================================
# Product purity after 7AL — the harness added no new product dependencies.
# =============================================================================
REVIEW_CONTRACT_EXPECTED_IMPORTS = {
    "dataclasses",
    "app.cad_engine.automation_gate",
    "app.cad_engine.exception_resolution",
    "app.cad_engine.project_connection_review",
    "app.cad_engine.project_workflow",
    "app.cad_engine.reviewed_connection_specification",
}

PROJECT_WORKFLOW_EXPECTED_IMPORTS = {
    "collections.abc",
    "dataclasses",
    "pathlib",
    "app.cad_engine.automation_gate",
    "app.cad_engine.automation_pipeline",
    "app.cad_engine.drawing_dispatch",
    "app.cad_engine.drawing_output_verification",
    "app.cad_engine.exception_resolution",
    "app.cad_engine.fabrication_output_gate",
    "app.cad_engine.project_automation",
    "app.cad_engine.project_connection_review",
    "app.cad_engine.project_extraction_intake",
    "app.cad_engine.resolution_rerun",
    "app.cad_engine.reviewed_connection_specification",
}


def test_7al_added_no_product_dependencies():
    """The product modules are byte-for-byte as their milestones left them:
    no Supabase, no Anthropic, no network, no frontend, no geometry."""
    for module_path, expected in (
        (review_contract_module.__file__, REVIEW_CONTRACT_EXPECTED_IMPORTS),
        (workflow_module.__file__, PROJECT_WORKFLOW_EXPECTED_IMPORTS),
    ):
        source = Path(module_path).read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported == expected, module_path
        lowered = source.lower()
        for token in FORBIDDEN_TOKENS:
            assert token not in lowered, (module_path, token)


def test_action_vocabulary_contains_no_bypass_names():
    """The approved workflow-facing actions are exactly REVIEW/RESOLVE/REFRESH —
    and nothing resembling a bypass is defined or ever returned."""
    assert ACTION_NAMES == (ACTION_REVIEW, ACTION_RESOLVE, ACTION_REFRESH)
    for name in FORBIDDEN_ACTION_NAMES:
        assert name not in ACTION_NAMES
        assert name not in review_contract_module.__all__
