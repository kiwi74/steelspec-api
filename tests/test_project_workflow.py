"""
Milestone 7AJ — PRODUCTION JOB WORKFLOW & INCREMENTAL EXCEPTION PROOF.

These tests drive the real Arkles Strand capture through the complete
job workflow: one project, three REVIEW connections, three separate
human resolution rounds, and three fabrication drawings — with every
unaffected connection's state, record and artifact proven untouched at
every revision.

The human fixture is the SAME clearly-labelled HUMAN-SUPPLIED TEST
DATA as the 7AI proof (member rows/placements/plate/holes/location):
the marks are the real Arkles known marks, everything else is this
test's controlled reviewer input — never derived from, and never
written into, the real AI extraction. Per the brief, the RP-0002 and
RP-0003 rounds reuse the SAME controlled geometry (deterministic CAD
output requires it) with clearly distinct connection identities:
a controlled test resolution, not AI-extracted Arkles data.
"""

import ast
import copy
import dataclasses
import hashlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

import app.cad_engine.project_workflow as workflow_module
import app.cad_engine.reviewed_connection_drawing_gate as reviewed_connection_drawing_gate
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
)
from app.cad_engine.drawing_output_verification import (
    CHECK_DRAWING_CONTENT_PRESENT,
    CHECK_FAILED,
    CHECK_FORMAT_READABLE,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
    CHECK_IDENTITY_VERIFIABLE,
    CHECK_PASSED,
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.exception_resolution import (
    TASK_COMPLETE_REVIEW,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    HumanResolution,
    build_exception_resolution_package,
)
from app.cad_engine.fabrication_output_gate import evaluate_project_fabrication_output_gate
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_connection_review import build_review_report
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.project_workflow import (
    ConnectionAlreadyProcessedError,
    CrossProjectResolutionError,
    ProjectConnectionRecord,
    ProjectConnectionState,
    ProjectWorkflowState,
    StaleProjectWorkflowError,
    UnknownProjectPackageError,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests.test_exception_resolution import ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import needs_real_capture, real_known_marks
from tests.test_real_world_exception_proof import (
    ARKLES_REAL_AI_EXTRACTION,
    HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE,
    HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
    HUMAN_SUPPLIED_MEMBER_ROWS,
    HUMAN_SUPPLIED_SECTION_MATCHER,
    _answer,
)

PROJECT_ID = "PROJ-7AJ"
SOURCE_DRAWING_ID = "ARKLES-STRAND"
DRAWING_SET_PAGE_COUNT = 41
CANDIDATE_IDS = ("RP-0001", "RP-0002", "RP-0003")

# =============================================================================
# CONTROLLED TEST RESOLUTIONS for RP-0002 / RP-0003 — not AI-extracted Arkles
# data. The reviewer fixture (member rows, plate, holes, location) is reused
# unchanged so the CAD geometry is deterministic; ONLY the connection identity
# differs per candidate. These identities are this test's data.
# =============================================================================
CONNECTION_IDENTITIES = {
    "RP-0001": "CONN-ARKLES-001",
    "RP-0002": "CONN-ARKLES-002",
    "RP-0003": "CONN-ARKLES-003",
}

GARBAGE_BYTES = b"this is deliberately not a pdf"


# =============================================================================
# Fixture helpers — the same deterministic inputs as the 7AI proof, but the
# project carries THIS milestone's id so the states are unmistakably 7AJ's.
# =============================================================================
def _workflow_built():
    intake = intake_page_extractions(
        list(ARKLES_REAL_AI_EXTRACTION),
        project_id=PROJECT_ID,
        source_drawing_id=SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
        drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
    )
    initial = evaluate_project_for_automation(intake.collection, intake=intake)
    built = build_exception_resolution_package(
        initial, intake.collection, member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
    )
    return intake, initial, built


def _start():
    intake, initial, built = _workflow_built()
    workflow = workflow_module.start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )
    return intake, initial, built, workflow


def _state(workflow, package_id):
    return next(c for c in workflow.connections if c.package_id == package_id)


def _record(workflow, package_id):
    index = next(
        i for i, c in enumerate(workflow.connections) if c.package_id == package_id
    )
    return workflow.connection_records[index]


def _resolutions_for(built, package_id):
    """Every task of the candidate answered, with THIS connection's identity."""
    group = next(g for g in built.connection_tasks if g.review_package_id == package_id)
    assert {t.task_type for t in group.tasks} == set(HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE)
    resolutions = []
    for task in group.tasks:
        value = HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[task.task_type]
        if task.task_type == TASK_PROVIDE_CONNECTION_IDENTITY:
            value = CONNECTION_IDENTITIES[package_id]
        resolutions.append(_answer(task, value))
    return resolutions


def _partial_resolutions(built, package_id):
    """Approval + identity only — the minimum that can be recorded without
    answering a single engineering question."""
    group = next(g for g in built.connection_tasks if g.review_package_id == package_id)
    resolutions = []
    for task in group.tasks:
        if task.task_type == TASK_COMPLETE_REVIEW:
            resolutions.append(_answer(task, None))
        elif task.task_type == TASK_PROVIDE_CONNECTION_IDENTITY:
            resolutions.append(_answer(task, CONNECTION_IDENTITIES[package_id]))
    return resolutions


def _resolve(workflow, built, package_id, output_dir, **overrides):
    kwargs = dict(package_id=package_id, resolutions=_resolutions_for(built, package_id),
                  output_dir=output_dir)
    kwargs.update(overrides)
    return workflow_module.resolve_project_connection(workflow, **kwargs)


def _full_sequence(output_dir):
    """Revision 0 -> revision 3: three separate resolution rounds."""
    _, _, built, workflow = _start()
    states = [workflow]
    for package_id in CANDIDATE_IDS:
        states.append(_resolve(states[-1], built, package_id, output_dir))
    return states


def _pdfs(dir_path):
    return sorted(Path(dir_path).glob("*-fabrication.pdf"))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pdf_text(path):
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


# -----------------------------------------------------------------------------
# Spy helpers — call-through wrappers that count genuine stage invocations.
# -----------------------------------------------------------------------------
def _spy_rerun(monkeypatch):
    calls = []
    original = workflow_module.rerun_connection_after_resolutions

    def wrapper(group, package, **kwargs):
        calls.append(group.review_package_id)
        return original(group, package, **kwargs)

    monkeypatch.setattr(workflow_module, "rerun_connection_after_resolutions", wrapper)
    return calls


def _spy_7aa(monkeypatch):
    calls = []
    original = workflow_module.evaluate_reviewed_connection_for_automation

    def wrapper(package, **kwargs):
        calls.append(package.supplement.connection_id)
        return original(package, **kwargs)

    monkeypatch.setattr(workflow_module, "evaluate_reviewed_connection_for_automation", wrapper)
    return calls


def _spy_generator(monkeypatch):
    calls = []
    original = reviewed_connection_drawing_gate.generate_connection_fabrication_drawing_pdf

    def wrapper(assembly, output_path, **kwargs):
        calls.append(getattr(getattr(assembly, "connection", None), "connection_id", None))
        return original(assembly, output_path, **kwargs)

    monkeypatch.setattr(
        reviewed_connection_drawing_gate, "generate_connection_fabrication_drawing_pdf", wrapper,
    )
    return calls


def _spy_verify(monkeypatch):
    calls = []
    original = workflow_module.verify_drawing_artifact

    def wrapper(dispatch_result, **kwargs):
        calls.append(dispatch_result.connection_id)
        return original(dispatch_result, **kwargs)

    monkeypatch.setattr(workflow_module, "verify_drawing_artifact", wrapper)
    return calls


# =============================================================================
# 1-5. Revision 0: the honest initial project view
# =============================================================================
@needs_real_capture
def test_initial_workflow_is_revision_zero_with_three_review_connections():
    _, _, _, workflow = _start()

    assert workflow.project_id == PROJECT_ID
    assert workflow.revision == 0
    assert workflow.project_decision == AUTOMATION_DECISION_REVIEW
    assert workflow.generated_files == ()
    assert workflow.review_count == 3
    assert workflow.confirm_count == 0
    assert workflow.auto_count == 0
    assert workflow.verified_count == 0
    assert workflow.blocked_count == 3

    for package_id in CANDIDATE_IDS:
        connection = _state(workflow, package_id)
        assert connection.package_id == package_id
        assert connection.connection_id is None  # identity is unresolved
        assert connection.decision == AUTOMATION_DECISION_REVIEW
        assert set(connection.blockers) == ARKLES_BLOCKER_CODES
        assert connection.output_status is None
        assert connection.verification_status is None
        assert connection.generated_files == ()
        assert connection.last_processed_revision is None


@needs_real_capture
def test_initial_workflow_generates_no_artifacts_or_drawings(tmp_path):
    _, _, _, workflow = _start()

    assert workflow.generated_files == ()
    assert all(c.generated_files == () for c in workflow.connections)
    assert "generated files = 0" in workflow.summary
    assert _pdfs(tmp_path) == []


@needs_real_capture
def test_initial_summary_is_the_project_view():
    _, _, _, workflow = _start()

    assert "revision = 0" in workflow.summary
    assert f"project = {PROJECT_ID}; project decision = {AUTOMATION_DECISION_REVIEW}" in workflow.summary
    assert "AUTO = 0; CONFIRM = 0; REVIEW = 3; VERIFIED = 0; blocked = 3" in workflow.summary
    for package_id in CANDIDATE_IDS:
        line = next(ln for ln in workflow.summary.splitlines() if ln.startswith(package_id + ":"))
        assert "decision=REVIEW" in line
        assert "output=" not in line and "verification=" not in line  # nothing processed yet
    assert workflow_module.PROJECT_WORKFLOW_SCOPE_STATEMENT in workflow.summary


@needs_real_capture
def test_initial_scope_facts_come_from_the_genuine_intake():
    _, initial, _, workflow = _start()

    # The workflow consumes the existing 7AB result — the scope facts are the
    # intake's own, never recomputed or invented.
    assert workflow._initial == initial
    joined = "\n".join(initial.summary)  # 7AB's summary is a tuple of lines
    assert "pages_received = 30" in joined
    assert "parse_failures = 6" in joined
    assert "pages_not_analysed = 11" in joined
    assert "drawing_set_page_count = 41" in joined


@needs_real_capture
def test_initial_records_carry_the_genuine_initial_pipelines():
    intake, initial, _, workflow = _start()

    for candidate, outcome in zip(intake.collection.candidates, initial.connection_results):
        record = _record(workflow, candidate.review_package_id)
        assert record.pipeline is not None
        # Stored 7AA objects agree with the 7AB outcomes they were re-obtained for.
        assert record.pipeline.automation_gate_result.decision == outcome.decision
        assert record.rerun_outcome is None
        assert record.gate_result is None
        assert record.dispatch_result is None
        assert record.verification_result is None


# =============================================================================
# 6-12. The incremental progression: three rounds, three PDFs, nothing re-run
# =============================================================================
@needs_real_capture
def test_acceptance_matrix_revisions_zero_through_three(tmp_path):
    _, _, built, workflow = _start()

    # ---- revision 0: everything REVIEW, nothing produced
    assert workflow.revision == 0
    assert workflow.project_decision == AUTOMATION_DECISION_REVIEW
    assert _pdfs(tmp_path) == []
    for package_id in CANDIDATE_IDS:
        assert _state(workflow, package_id).decision == AUTOMATION_DECISION_REVIEW

    # ---- revision 1: RP-0001 fully resolved; the project stays REVIEW
    w1 = _resolve(workflow, built, "RP-0001", tmp_path)
    assert w1.revision == 1
    assert w1.project_decision == AUTOMATION_DECISION_REVIEW
    assert w1.auto_count == 1 and w1.review_count == 2
    assert w1.verified_count == 1 and w1.blocked_count == 2
    rp1 = _state(w1, "RP-0001")
    assert rp1.decision == AUTOMATION_DECISION_AUTO
    assert rp1.output_status == OUTPUT_STATUS_GENERATED
    assert rp1.verification_status == VERIFICATION_STATUS_VERIFIED
    assert rp1.last_processed_revision == 1
    assert [Path(p).name for p in _pdfs(tmp_path)] == ["CONN-ARKLES-001-fabrication.pdf"]

    # ---- revision 2: RP-0002 resolved too; the project stays REVIEW
    w2 = _resolve(w1, built, "RP-0002", tmp_path)
    assert w2.revision == 2
    assert w2.project_decision == AUTOMATION_DECISION_REVIEW
    assert w2.auto_count == 2 and w2.review_count == 1
    assert w2.verified_count == 2 and w2.blocked_count == 1
    assert _state(w2, "RP-0002").verification_status == VERIFICATION_STATUS_VERIFIED
    assert _state(w2, "RP-0002").last_processed_revision == 2
    assert _state(w2, "RP-0003").last_processed_revision is None
    assert [Path(p).name for p in _pdfs(tmp_path)] == [
        "CONN-ARKLES-001-fabrication.pdf", "CONN-ARKLES-002-fabrication.pdf",
    ]

    # ---- revision 3: everything resolved; the existing project semantics
    # ---- give a fully resolved project its automatic decision
    w3 = _resolve(w2, built, "RP-0003", tmp_path)
    assert w3.revision == 3
    assert w3.project_decision == AUTOMATION_DECISION_AUTO
    assert w3.auto_count == 3 and w3.review_count == 0 and w3.confirm_count == 0
    assert w3.verified_count == 3 and w3.blocked_count == 0
    assert [Path(p).name for p in _pdfs(tmp_path)] == [
        "CONN-ARKLES-001-fabrication.pdf",
        "CONN-ARKLES-002-fabrication.pdf",
        "CONN-ARKLES-003-fabrication.pdf",
    ]
    for package_id in CANDIDATE_IDS:
        connection = _state(w3, package_id)
        assert connection.connection_id == CONNECTION_IDENTITIES[package_id]
        assert connection.verification_status == VERIFICATION_STATUS_VERIFIED
        assert connection.last_processed_revision is not None


@needs_real_capture
def test_resolving_rp0001_reruns_only_rp0001(monkeypatch, tmp_path):
    _, _, built, workflow = _start()
    rerun_calls = _spy_rerun(monkeypatch)
    aa_calls = _spy_7aa(monkeypatch)
    generator_calls = _spy_generator(monkeypatch)
    verify_calls = _spy_verify(monkeypatch)

    w1 = _resolve(workflow, built, "RP-0001", tmp_path)

    # Exactly one connection-level rerun, one pipeline re-evaluation, one
    # drawing generation and one verification — all for RP-0001 only.
    assert rerun_calls == ["RP-0001"]
    assert aa_calls == ["CONN-ARKLES-001"]
    assert generator_calls == ["CONN-ARKLES-001"]
    assert verify_calls == ["CONN-ARKLES-001"]
    # The project-level rerun entry is not even imported by the workflow.
    assert not hasattr(workflow_module, "rerun_project_after_resolutions")


@needs_real_capture
def test_rp0001_is_verified_with_the_real_pdf_and_resolved_geometry(tmp_path):
    _, _, built, workflow = _start()
    w1 = _resolve(workflow, built, "RP-0001", tmp_path)

    connection = _state(w1, "RP-0001")
    assert connection.connection_id == "CONN-ARKLES-001"
    assert connection.decision == AUTOMATION_DECISION_AUTO
    assert connection.output_status == OUTPUT_STATUS_GENERATED
    assert connection.verification_status == VERIFICATION_STATUS_VERIFIED
    assert connection.generated_files == w1.generated_files == (str(_pdfs(tmp_path)[0]),)

    path = Path(connection.generated_files[0])
    verification = _record(w1, "RP-0001").verification_result
    assert verification.verification_status == VERIFICATION_STATUS_VERIFIED
    assert Path(verification.artifact_path) == path
    assert verification.file_size_bytes == path.stat().st_size > 0
    assert verification.sha256 == _sha256(path)
    assert verification.page_count == 1 == len(PdfReader(path).pages)
    check_statuses = {check.code: check.status for check in verification.checks}
    assert check_statuses[CHECK_IDENTITY_VERIFIABLE] == CHECK_PASSED
    assert check_statuses[CHECK_DRAWING_CONTENT_PRESENT] == CHECK_PASSED
    assert check_statuses[CHECK_GEOMETRY_FIELDS_VERIFIABLE] == CHECK_PASSED

    text = _pdf_text(path)
    assert "CONN-ARKLES-001" in text
    assert "MEMBER A" in text and "MEMBER B" in text
    assert "L2" in text and "L3" in text and "250PFC" in text
    assert "Ø22" in text  # the HUMAN-SUPPLIED hole diameter — never the AI's "M12"


@needs_real_capture
def test_unaffected_connections_are_carried_untouched_at_every_revision(tmp_path):
    _, _, built, workflow = _start()
    w0 = workflow

    w1 = _resolve(w0, built, "RP-0001", tmp_path)
    # The SAME state objects — not copies — survive for the untouched connections.
    assert _state(w1, "RP-0002") is _state(w0, "RP-0002")
    assert _state(w1, "RP-0003") is _state(w0, "RP-0003")
    assert _record(w1, "RP-0002") is _record(w0, "RP-0002")
    assert _record(w1, "RP-0003") is _record(w0, "RP-0003")
    for package_id in ("RP-0002", "RP-0003"):
        connection = _state(w1, package_id)
        assert connection.decision == AUTOMATION_DECISION_REVIEW
        assert set(connection.blockers) == ARKLES_BLOCKER_CODES
        assert connection.output_status is None and connection.verification_status is None
        assert connection.last_processed_revision is None

    w2 = _resolve(w1, built, "RP-0002", tmp_path)
    assert _state(w2, "RP-0003") is _state(w1, "RP-0003")
    assert _state(w2, "RP-0001") is _state(w1, "RP-0001")  # already-processed states stay too
    assert _record(w2, "RP-0001").verification_result is _record(w1, "RP-0001").verification_result

    w3 = _resolve(w2, built, "RP-0003", tmp_path)
    assert _state(w3, "RP-0001") is _state(w2, "RP-0001")
    assert _state(w3, "RP-0002") is _state(w2, "RP-0002")


@needs_real_capture
def test_prior_artifacts_are_byte_identical_across_revisions(monkeypatch, tmp_path):
    _, _, built, workflow = _start()

    w1 = _resolve(workflow, built, "RP-0001", tmp_path)
    p1 = _state(w1, "RP-0001").generated_files[0]
    facts_1 = (p1, _sha256(p1), Path(p1).stat().st_size, Path(p1).stat().st_mtime_ns)

    generator_calls = _spy_generator(monkeypatch)  # installed AFTER revision 1
    w2 = _resolve(w1, built, "RP-0002", tmp_path)
    p2 = _state(w2, "RP-0002").generated_files[0]
    facts_2 = (p2, _sha256(p2), Path(p2).stat().st_size, Path(p2).stat().st_mtime_ns)
    # RP-0001's artifact is byte-identical — and was not even rewritten.
    assert (_sha256(p1), Path(p1).stat().st_size, Path(p1).stat().st_mtime_ns) == facts_1[1:]
    assert generator_calls == ["CONN-ARKLES-002"]

    w3 = _resolve(w2, built, "RP-0003", tmp_path)
    assert (_sha256(p1), Path(p1).stat().st_size, Path(p1).stat().st_mtime_ns) == facts_1[1:]
    assert (_sha256(p2), Path(p2).stat().st_size, Path(p2).stat().st_mtime_ns) == facts_2[1:]
    assert generator_calls == ["CONN-ARKLES-002", "CONN-ARKLES-003"]


@needs_real_capture
def test_verification_results_are_preserved_never_redone(monkeypatch, tmp_path):
    _, _, built, workflow = _start()
    w1 = _resolve(workflow, built, "RP-0001", tmp_path)
    verify_calls = _spy_verify(monkeypatch)  # installed AFTER revision 1

    w2 = _resolve(w1, built, "RP-0002", tmp_path)
    # RP-0001's complete 7AG manifest is the SAME object — never re-verified.
    assert _record(w2, "RP-0001").verification_result is _record(w1, "RP-0001").verification_result
    assert verify_calls == ["CONN-ARKLES-002"]

    w3 = _resolve(w2, built, "RP-0003", tmp_path)
    assert _record(w3, "RP-0001").verification_result is _record(w1, "RP-0001").verification_result
    assert _record(w3, "RP-0002").verification_result is _record(w2, "RP-0002").verification_result
    assert verify_calls == ["CONN-ARKLES-002", "CONN-ARKLES-003"]


@needs_real_capture
def test_three_resolves_three_pdfs_no_duplicates_no_regeneration(monkeypatch, tmp_path):
    _, _, built, workflow = _start()
    rerun_calls = _spy_rerun(monkeypatch)
    aa_calls = _spy_7aa(monkeypatch)
    generator_calls = _spy_generator(monkeypatch)

    for package_id in CANDIDATE_IDS:
        workflow = _resolve(workflow, built, package_id, tmp_path)

    # Three resolutions -> three reruns, three pipeline re-evaluations, three
    # genuine generator invocations. Zero unnecessary reruns, zero duplicate
    # drawings: every earlier artifact was left exactly as generated.
    assert rerun_calls == list(CANDIDATE_IDS)
    assert aa_calls == [CONNECTION_IDENTITIES[p] for p in CANDIDATE_IDS]
    assert generator_calls == [CONNECTION_IDENTITIES[p] for p in CANDIDATE_IDS]
    pdfs = _pdfs(tmp_path)
    assert [p.name for p in pdfs] == [
        f"{identity}-fabrication.pdf" for identity in CONNECTION_IDENTITIES.values()
    ]
    for path in pdfs:
        assert path.stat().st_size > 0
    assert len({_sha256(p) for p in pdfs}) == 3  # three distinct artifacts


# =============================================================================
# 13-19. Idempotency, staleness and guard rails
# =============================================================================
@needs_real_capture
def test_duplicate_resolution_is_refused(monkeypatch, tmp_path):
    _, _, built, workflow = _start()
    w1 = _resolve(workflow, built, "RP-0001", tmp_path)
    before = _sha256(_state(w1, "RP-0001").generated_files[0])
    generator_calls = _spy_generator(monkeypatch)

    with pytest.raises(ConnectionAlreadyProcessedError):
        _resolve(w1, built, "RP-0001", tmp_path)

    # Nothing new was produced and nothing was re-generated.
    assert _pdfs(tmp_path) == [Path(_state(w1, "RP-0001").generated_files[0])]
    assert _sha256(_state(w1, "RP-0001").generated_files[0]) == before
    assert generator_calls == []


@needs_real_capture
def test_stale_revision_is_refused(tmp_path):
    _, _, built, workflow = _start()
    w3 = _full_sequence(tmp_path)[-1]
    stale = _resolutions_for(built, "RP-0003")

    with pytest.raises(StaleProjectWorkflowError):
        workflow_module.resolve_project_connection(
            w3, package_id="RP-0003", resolutions=stale, output_dir=tmp_path,
            expected_revision=2,
        )
    # The same call at the CURRENT revision passes the staleness check and is
    # refused only by the one-shot rule — proving staleness is what failed above.
    with pytest.raises(ConnectionAlreadyProcessedError):
        workflow_module.resolve_project_connection(
            w3, package_id="RP-0003", resolutions=stale, output_dir=tmp_path,
            expected_revision=3,
        )
    assert len(_pdfs(tmp_path)) == 3  # nothing new was written by any refusal


@needs_real_capture
def test_unknown_package_is_refused_without_processing(tmp_path):
    _, _, built, workflow = _start()
    # A valid resolution bundle for a REAL connection — the refusal must come
    # from the workflow's own unknown-package guard, not from the resolutions.
    resolutions = _resolutions_for(built, "RP-0001")

    with pytest.raises(UnknownProjectPackageError) as excinfo:
        workflow_module.resolve_project_connection(
            workflow, package_id="RP-9999", resolutions=resolutions, output_dir=tmp_path,
        )
    assert "RP-9999" in str(excinfo.value)

    assert _pdfs(tmp_path) == []
    assert workflow.revision == 0


@needs_real_capture
def test_resolutions_for_another_connection_are_refused(tmp_path):
    _, _, built, workflow = _start()
    foreign = _resolutions_for(built, "RP-0002")  # RP-0002's tasks...

    with pytest.raises(CrossProjectResolutionError):
        _resolve(workflow, built, "RP-0001", tmp_path, resolutions=foreign)

    assert _pdfs(tmp_path) == []


@needs_real_capture
def test_resolutions_for_another_project_are_refused(tmp_path):
    _, _, built, workflow = _start()
    resolutions = _resolutions_for(built, "RP-0001")
    smuggled = dataclasses.replace(resolutions[0], task_id="OTHER-0007-T01")

    with pytest.raises(CrossProjectResolutionError):
        _resolve(workflow, built, "RP-0001", tmp_path,
                 resolutions=[smuggled] + resolutions[1:])

    assert _pdfs(tmp_path) == []


@needs_real_capture
@pytest.mark.parametrize("package_id, answer", [
    ("RP-0001", 42),          # identity answer that is not a string at all
    ("RP-0001", ""),          # identity answer that is not a non-empty identifier string
])
def test_invalid_resolution_raises_before_anything_runs(package_id, answer, tmp_path):
    _, _, built, workflow = _start()
    resolutions = _resolutions_for(built, package_id)
    identity = next(
        r for r in resolutions if r.task_type == TASK_PROVIDE_CONNECTION_IDENTITY
    )
    bogus = HumanResolution(
        identity.task_id, identity.task_type, identity.answer_type, answer,
        evidence="HUMAN-SUPPLIED TEST DATA — deliberately malformed answer",
    )
    resolutions = [bogus if r is identity else r for r in resolutions]

    with pytest.raises(ValueError):  # the genuine 7AC contract rejects the payload
        _resolve(workflow, built, package_id, tmp_path, resolutions=resolutions)

    # Atomic: no state change, no files, no partial application.
    assert _pdfs(tmp_path) == []
    assert workflow.revision == 0


@needs_real_capture
def test_empty_resolutions_are_refused(tmp_path):
    _, _, built, workflow = _start()
    with pytest.raises(ValueError):
        _resolve(workflow, built, "RP-0001", tmp_path, resolutions=[])
    assert _pdfs(tmp_path) == []


# =============================================================================
# 20-22. Failure safety: blocked, generation-failed and verification-failed
# =============================================================================
@needs_real_capture
def test_incomplete_resolution_stays_review_without_artifact(tmp_path):
    _, _, built, workflow = _start()
    output_dir = tmp_path / "blocked-subdir"

    w1 = workflow_module.resolve_project_connection(
        workflow, package_id="RP-0001",
        resolutions=_partial_resolutions(built, "RP-0001"),
        output_dir=output_dir,
    )

    connection = _state(w1, "RP-0001")
    assert connection.decision == AUTOMATION_DECISION_REVIEW
    assert connection.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
    assert connection.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert connection.generated_files == ()
    assert set(connection.blockers) == ARKLES_BLOCKER_CODES - {AUTOMATION_BLOCKER_REVIEW_STATUS}
    assert connection.last_processed_revision == 1  # the attempt WAS recorded
    assert w1.project_decision == AUTOMATION_DECISION_REVIEW
    assert not output_dir.exists()  # nothing was ever written
    assert _pdfs(tmp_path) == []

    # The single resolution attempt was consumed: the connection is processed
    # (even though it stayed REVIEW) and cannot be silently retried.
    with pytest.raises(ConnectionAlreadyProcessedError):
        _resolve(w1, built, "RP-0001", tmp_path)


@needs_real_capture
def test_generation_failure_is_recorded_honestly_and_touches_nothing_else(tmp_path):
    _, _, built, workflow = _start()
    w1 = _resolve(workflow, built, "RP-0001", tmp_path)
    before = _sha256(_state(w1, "RP-0001").generated_files[0])

    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("simulated generator failure for the test")

    w2 = _resolve(w1, built, "RP-0002", tmp_path, drawing_entry=exploding_entry)

    connection = _state(w2, "RP-0002")
    assert connection.decision == AUTOMATION_DECISION_AUTO  # the rerun DID succeed
    assert connection.output_status == OUTPUT_STATUS_GENERATION_FAILED
    assert connection.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert connection.generated_files == ()
    assert connection.last_processed_revision == 2
    dispatch = _record(w2, "RP-0002").dispatch_result
    assert "simulated generator failure" in dispatch.generation_error

    # No false success, and the earlier connection's artifact is untouched.
    assert w2.verified_count == 1 and w2.auto_count == 2
    assert w2.project_decision == AUTOMATION_DECISION_REVIEW
    assert "GENERATION_FAILED" in w2.summary
    assert _sha256(_state(w1, "RP-0001").generated_files[0]) == before
    assert _record(w2, "RP-0001").verification_result is _record(w1, "RP-0001").verification_result
    assert _pdfs(tmp_path) == [Path(_state(w2, "RP-0001").generated_files[0])]


@needs_real_capture
def test_verification_failure_is_recorded_honestly_and_the_artifact_stays(tmp_path):
    _, _, built, workflow = _start()
    w1 = _resolve(workflow, built, "RP-0001", tmp_path)

    def garbage_entry(assembly, output_path, **kwargs):
        Path(output_path).write_bytes(GARBAGE_BYTES)

    w2 = _resolve(w1, built, "RP-0002", tmp_path, drawing_entry=garbage_entry)

    connection = _state(w2, "RP-0002")
    assert connection.decision == AUTOMATION_DECISION_AUTO
    assert connection.output_status == OUTPUT_STATUS_GENERATED  # dispatch did its job
    assert connection.verification_status == VERIFICATION_STATUS_FAILED
    assert connection.generated_files == (str(tmp_path / "CONN-ARKLES-002-fabrication.pdf"),)

    # The genuine 7AG checks failed the corrupt artifact — and recorded the
    # facts needed to diagnose it.
    path = Path(connection.generated_files[0])
    assert path.read_bytes() == GARBAGE_BYTES
    verification = _record(w2, "RP-0002").verification_result
    assert verification.verification_status == VERIFICATION_STATUS_FAILED
    check_statuses = {check.code: check.status for check in verification.checks}
    assert check_statuses[CHECK_FORMAT_READABLE] == CHECK_FAILED
    assert Path(verification.artifact_path) == path
    assert verification.file_size_bytes == len(GARBAGE_BYTES)
    assert verification.sha256 == hashlib.sha256(GARBAGE_BYTES).hexdigest()

    # No false success anywhere; the earlier connection stays exactly as it was.
    assert w2.verified_count == 1
    assert w2.project_decision == AUTOMATION_DECISION_REVIEW
    assert "FAILED" in w2.summary
    assert _record(w2, "RP-0001").verification_result is _record(w1, "RP-0001").verification_result
    assert _record(w2, "RP-0001").dispatch_result is _record(w1, "RP-0001").dispatch_result


# =============================================================================
# 23-25. Provenance, losslessness, no invention
# =============================================================================
@needs_real_capture
def test_human_provenance_stays_human_supplemented(tmp_path):
    states = _full_sequence(tmp_path)
    w3 = states[-1]

    for package_id in CANDIDATE_IDS:
        record = _record(w3, package_id)
        rebuilt = record.rerun_outcome.rebuilt_package
        provenance = build_review_report(rebuilt).provenance
        for field in ("connected_member_marks", "position", "plate", "holes",
                      "location", "attachments"):
            assert provenance[field] == PROVENANCE_HUMAN_SUPPLEMENTED, (package_id, field)
        assert rebuilt.supplement.connection_id == CONNECTION_IDENTITIES[package_id]
        assert "connection_id" not in provenance  # identity is an identifier, not a field
        assert PROVENANCE_AI_EXTRACTED not in provenance.values()


@needs_real_capture
def test_the_raw_capture_and_human_context_are_never_mutated(tmp_path):
    capture_snapshot = copy.deepcopy(ARKLES_REAL_AI_EXTRACTION)
    rows_snapshot = copy.deepcopy(HUMAN_SUPPLIED_MEMBER_ROWS)
    placements_snapshot = copy.deepcopy(HUMAN_SUPPLIED_MEMBER_PLACEMENTS)

    _full_sequence(tmp_path)

    assert ARKLES_REAL_AI_EXTRACTION == capture_snapshot
    assert HUMAN_SUPPLIED_MEMBER_ROWS == rows_snapshot
    assert HUMAN_SUPPLIED_MEMBER_PLACEMENTS == placements_snapshot


@needs_real_capture
def test_the_workflow_never_invents_or_converts_engineering_values(tmp_path):
    states = _full_sequence(tmp_path)
    for state in states:
        assert "Ø" not in repr(state)  # no diameter was ever derived or converted
        assert "Ø" not in state.summary
        assert "M12" not in state.summary
        assert "SQ4" not in state.summary

    # The real drawing carries the HUMAN-SUPPLIED diameter and none of the
    # AI's nominal bolt sizes.
    text = _pdf_text(_pdfs(tmp_path)[0])
    assert "Ø22" in text
    assert "M12" not in text


# =============================================================================
# 26-29. Anti-bypass, freezing, purity, no secrets
# =============================================================================
def test_no_parameter_can_inject_a_decision_or_a_verification():
    start_params = inspect.signature(workflow_module.start_project_workflow).parameters
    assert list(start_params) == [
        "collection", "intake", "member_rows", "member_placements", "section_matcher",
        "request_material_specification",
    ]
    resolve_params = inspect.signature(workflow_module.resolve_project_connection).parameters
    assert list(resolve_params) == [
        "workflow", "package_id", "resolutions", "output_dir", "expected_revision", "drawing_entry",
    ]
    refresh_params = inspect.signature(workflow_module.refresh_project_workflow).parameters
    assert list(refresh_params) == ["workflow", "output_dir"]

    for name, param in resolve_params.items():
        assert param.kind == inspect.Parameter.KEYWORD_ONLY or name == "workflow"
    for forbidden in ("decision", "validation_passed", "verification", "output_status",
                      "approved", "force_auto", "connection_id"):
        for params in (start_params, resolve_params, refresh_params):
            assert forbidden not in params


def test_workflow_states_are_frozen():
    _, _, _, workflow = _start()
    with pytest.raises(dataclasses.FrozenInstanceError):
        workflow.revision = 99
    connection = workflow.connections[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        connection.decision = AUTOMATION_DECISION_AUTO
    record = workflow.connection_records[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.rerun_outcome = None


ALLOWED_IMPORTS = {
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


def test_imports_are_pure_and_only_the_incremental_chain_is_called():
    source = Path(workflow_module.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == ALLOWED_IMPORTS

    lowered = source.lower()
    for forbidden in (
        "supabase", "anthropic", "fastapi", "cadquery", "reportlab", "ezdxf",
        "pypdf", "reviewed_connection_drawing_gate", "http", "socket",
        "310ub", "250pfc", "real-ub", "conn-real", "arkles", "con-arkles",
        "m12", "sq4", "Ø",
        "dispatch_project_fabrication_drawings", "verify_project_drawing_outputs",
        "generate_connection_fabrication_drawing_pdf",
        "generate_fabrication_drawing_from_reviewed_assembly",
        "validate_reviewed_connection_assembly",
        "evaluate_automation_gate", "evaluate_fabrication_output_gate",
        "rerun_project_after_resolutions", "intake_page_extractions",
    ):
        assert forbidden not in lowered, forbidden

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    # The genuine incremental chain — the workflow calls ONLY these stage
    # entries, and never the generator, 7Z, or the project-level loops.
    chain = {
        "evaluate_project_for_automation",
        "evaluate_reviewed_connection_for_automation",
        "build_exception_resolution_package",
        "apply_human_resolution",
        "rerun_connection_after_resolutions",
        "evaluate_project_fabrication_output_gate",
        "dispatch_fabrication_drawing",
        "verify_drawing_artifact",
    }
    assert chain <= called
    assert not hasattr(workflow_module, "rerun_project_after_resolutions")
    assert not hasattr(workflow_module, "dispatch_project_fabrication_drawings")
    assert not hasattr(workflow_module, "verify_project_drawing_outputs")


def test_module_imports_without_any_secret_environment():
    env = {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("ANTHROPIC", "SUPABASE", "FIREWORKS", "OPENAI"))
    }
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import app.cad_engine.project_workflow"],
        env=env, cwd=root, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "SUPABASE" not in completed.stderr and "ANTHROPIC" not in completed.stderr


# =============================================================================
# 30-33. Consistency, refresh, determinism
# =============================================================================
@needs_real_capture
def test_counts_are_consistent_with_the_genuine_composite_gate_at_every_revision(tmp_path):
    _, initial, _, _ = _start()
    states = _full_sequence(tmp_path)

    for state in states:
        pipelines = {
            connection.package_id: record.pipeline
            for connection, record in zip(state.connections, state.connection_records)
        }
        gate = evaluate_project_fabrication_output_gate(initial, pipelines)
        assert gate.package_decision == state.project_decision
        assert state.review_count == sum(
            1 for c in state.connections if c.decision == AUTOMATION_DECISION_REVIEW)
        assert state.confirm_count == sum(
            1 for c in state.connections if c.decision == AUTOMATION_DECISION_CONFIRM)
        assert state.auto_count == sum(
            1 for c in state.connections if c.decision == AUTOMATION_DECISION_AUTO)
        assert state.verified_count == sum(
            1 for c in state.connections
            if c.verification_status == VERIFICATION_STATUS_VERIFIED)
        assert state.blocked_count == state.review_count + state.confirm_count
        assert state.generated_files == tuple(
            path for c in state.connections for path in c.generated_files)


@needs_real_capture
def test_refresh_reprojects_without_reprocessing(monkeypatch, tmp_path):
    w3 = _full_sequence(tmp_path)[-1]
    rerun_calls = _spy_rerun(monkeypatch)
    aa_calls = _spy_7aa(monkeypatch)
    generator_calls = _spy_generator(monkeypatch)
    verify_calls = _spy_verify(monkeypatch)

    refreshed = workflow_module.refresh_project_workflow(w3, output_dir=tmp_path)

    assert refreshed.revision == w3.revision == 3  # refresh never advances the revision
    assert refreshed.project_decision == AUTOMATION_DECISION_AUTO
    assert refreshed.connections == w3.connections
    for a, b in zip(refreshed.connections, w3.connections):
        assert a is b
    assert refreshed.generated_files == w3.generated_files
    assert "refresh: every recorded artifact is present" in refreshed.summary
    # Nothing anywhere in the chain was re-run.
    assert rerun_calls == [] and aa_calls == []
    assert generator_calls == [] and verify_calls == []


@needs_real_capture
def test_refresh_reports_a_missing_artifact_without_repairing(monkeypatch, tmp_path):
    w3 = _full_sequence(tmp_path)[-1]
    missing = Path(_state(w3, "RP-0001").generated_files[0])
    missing.unlink()
    generator_calls = _spy_generator(monkeypatch)

    refreshed = workflow_module.refresh_project_workflow(w3, output_dir=tmp_path)

    assert refreshed.revision == 3
    assert "RP-0001: recorded artifact" in refreshed.summary
    assert "missing" in refreshed.summary
    assert not missing.exists()  # reported, never repaired or regenerated
    assert refreshed.generated_files == w3.generated_files  # the record is history
    for a, b in zip(refreshed.connections, w3.connections):
        assert a is b
    assert generator_calls == []


@needs_real_capture
def test_two_independent_runs_are_deterministic(tmp_path):
    states_a = _full_sequence(tmp_path / "a")
    states_b = _full_sequence(tmp_path / "b")

    for state_a, state_b in zip(states_a, states_b):
        assert state_a.revision == state_b.revision
        assert state_a.project_decision == state_b.project_decision
        assert state_a.summary == state_b.summary
        assert (state_a.review_count, state_a.confirm_count, state_a.auto_count,
                state_a.verified_count, state_a.blocked_count) == \
            (state_b.review_count, state_b.confirm_count, state_b.auto_count,
             state_b.verified_count, state_b.blocked_count)
        for conn_a, conn_b in zip(state_a.connections, state_b.connections):
            # Only the recorded artifact paths differ (per output directory).
            assert dataclasses.replace(conn_a, generated_files=()) == \
                dataclasses.replace(conn_b, generated_files=())


# =============================================================================
# 34-35. Loud type errors on every public entry
# =============================================================================
@needs_real_capture
@pytest.mark.parametrize("bad_kwargs", [
    dict(collection=None),
    dict(intake=None),
    dict(member_rows=None),
    dict(member_placements=None),
])
def test_start_project_workflow_rejects_bad_inputs_loudly(bad_kwargs):
    intake, _, _, _ = _start()
    kwargs = dict(
        collection=intake.collection,
        intake=intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )
    kwargs.update(bad_kwargs)
    with pytest.raises(TypeError):
        workflow_module.start_project_workflow(**kwargs)


@needs_real_capture
@pytest.mark.parametrize("overrides", [
    dict(workflow=None),
    dict(package_id=7),
    dict(resolutions="not-a-sequence"),
    dict(resolutions=[object()]),
    dict(output_dir=123),
    dict(expected_revision="1"),
    dict(expected_revision=True),
    dict(drawing_entry=42),
])
def test_resolve_rejects_bad_inputs_loudly(overrides, tmp_path):
    _, _, built, workflow = _start()
    kwargs = dict(
        package_id="RP-0001",
        resolutions=_resolutions_for(built, "RP-0001"),
        output_dir=tmp_path,
    )
    kwargs.update(overrides)
    with pytest.raises(TypeError):
        workflow_module.resolve_project_connection(workflow, **kwargs)
    assert _pdfs(tmp_path) == []
