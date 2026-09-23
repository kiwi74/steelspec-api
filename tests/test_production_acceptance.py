"""7AQ Production Job Acceptance tests — the human-to-output proof.

Every acceptance exercised here is derived from the GENUINE 7AJ project
workflow (start_project_workflow -> resolve_project_connection) running on the
real captured Arkles extraction — never from a synthetic acceptance object,
never hard-coded to a revision. The failure and tamper cases mutate the real
workflow state AFTER the fact and prove the acceptance layer still refuses.

Sections (per the 7AQ brief):
  * request validity and REFUSED semantics (stale state is never accepted)
  * the production proof: revision 0 -> 3, NOT_ACCEPTED until every
    connection is genuinely verified, then ACCEPTED
  * artifact proof: filename, existence, sha256, page count, identity and
    geometry checks, final status — all from the 7AG manifest, nothing
    invented
  * audit proof: the backward walk artifact -> verification -> dispatch ->
    gate -> rerun -> resolutions -> tasks -> blocker codes -> AI
    observations -> drawing evidence
  * incremental proof: each resolution reruns exactly the changed connection
  * failure paths: unresolved review, automation REVIEW, blocked fabrication,
    failed generation, failed verification, missing artifact, stale revision
  * tamper / false-positive refusal: injected AUTO / VERIFIED, fake
    artifacts, missing provenance, unresolved blockers, injected ids
  * purity: deterministic, side-effect free, no invented evidence
"""

import ast
import dataclasses
import hashlib
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
    AutomationFinding,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
)
from app.cad_engine.drawing_output_verification import (
    CHECK_DRAWING_CONTENT_PRESENT,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
    CHECK_IDENTITY_VERIFIABLE,
    CHECK_PASSED,
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.exception_resolution import (
    ANSWER_FIELD_DECISION,
    FIELD_DECISION_SUPPLY,
    HumanResolution,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
    PRODUCTION_ACCEPTANCE_SCOPE_STATEMENT,
    ProductionJobAcceptance,
    accept_production_job,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests.test_exception_resolution import ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    CONNECTION_IDENTITIES,
    DRAWING_SET_PAGE_COUNT,
    GARBAGE_BYTES,
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _partial_resolutions,
    _pdfs,
    _record,
    _resolve,
    _sha256,
    _spy_7aa,
    _spy_generator,
    _spy_rerun,
    _spy_verify,
    _start,
    _state,
)

MODULE_PATH = Path("app/cad_engine/production_acceptance.py")
MODULE_SOURCE = MODULE_PATH.read_text()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _by_package(job, package_id):
    return next(a for a in job.connections if a.package_id == package_id)


def _with_state(workflow, package_id, **changes):
    """One connection's visible projection replaced — a tampered state."""
    connections = tuple(
        dataclasses.replace(c, **changes) if c.package_id == package_id else c
        for c in workflow.connections
    )
    return dataclasses.replace(workflow, connections=connections)


def _with_record(workflow, package_id, **changes):
    """One connection's recorded evidence replaced — a tampered record."""
    records = tuple(
        dataclasses.replace(r, **changes) if c.package_id == package_id else r
        for c, r in zip(workflow.connections, workflow.connection_records)
    )
    return dataclasses.replace(workflow, connection_records=records)


def _applied(acceptance):
    return tuple(d for d in acceptance.trace.human_decisions if d.applied)


def _group(workflow, package_id):
    return next(
        g for g in workflow.exception_package.connection_tasks
        if g.review_package_id == package_id
    )


# ===========================================================================
# 1. Request validity and REFUSED semantics — stale state is never accepted.
# ===========================================================================
class TestRefusedRequests:
    def test_type_guards_raise_before_anything_runs(self):
        with pytest.raises(TypeError):
            accept_production_job(42)
        with pytest.raises(TypeError):
            accept_production_job("not a workflow")
        _, _, _, workflow = _start()
        with pytest.raises(TypeError):
            accept_production_job(workflow, expected_revision="0")
        with pytest.raises(TypeError):
            accept_production_job(workflow, expected_revision=True)

    @needs_real_capture
    def test_stale_caller_revision_is_refused_never_accepted(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3, expected_revision=2)
        assert isinstance(job, ProductionJobAcceptance)
        assert job.status == ACCEPTANCE_STATUS_REFUSED
        assert "revision 3" in job.reason and "revision 2" in job.reason
        assert job.revision == 3 and job.caller_revision == 2
        assert job.connections == ()
        assert job.accepted_package_ids == () and job.verified_artifacts == ()
        assert job.summary.connections_total == 0
        # the workflow itself was never touched
        assert w3.revision == 3 and w3.verified_count == 3

    @needs_real_capture
    def test_stale_acceptance_never_reads_newer_state(self, tmp_path):
        # A caller holding revision 0 must be refused against revision 1.
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)
        job = accept_production_job(w1, expected_revision=0)
        assert job.status == ACCEPTANCE_STATUS_REFUSED
        assert "revision 1" in job.reason and "revision 0" in job.reason

    def test_scope_statement_names_the_two_invariants(self):
        assert "No connection can reach a verified fabrication output" in PRODUCTION_ACCEPTANCE_SCOPE_STATEMENT
        assert "traceable back to the original AI observation" in PRODUCTION_ACCEPTANCE_SCOPE_STATEMENT


# ===========================================================================
# 2. The production proof: revision 0 -> 3.
# ===========================================================================
class TestProductionProofRevisionProgression:
    @needs_real_capture
    def test_revision_zero_is_not_accepted_with_three_unresolved(self, tmp_path):
        intake, initial, built, workflow = _start()
        job = accept_production_job(workflow)
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert job.project_id == PROJECT_ID and job.revision == 0
        assert job.extraction_sufficient is True
        # the intake facts come from the genuine intake, verbatim
        assert job.intake_scope.pages_received == intake.pages_received == 30
        assert len(job.intake_scope.parse_failed_pages) == 6
        assert job.intake_scope.drawing_set_page_count == DRAWING_SET_PAGE_COUNT == 41
        assert job.intake_scope.pages_not_analysed == 11
        # all three connections are unresolved review: none ever ran a stage
        assert job.accepted_package_ids == ()
        assert job.failed_package_ids == ()
        assert job.unresolved_package_ids == tuple(CANDIDATE_IDS)
        assert job.verified_artifacts == ()
        assert job.summary.connections_total == 3
        assert job.summary.connections_unresolved == 3
        assert job.summary.connections_accepted == 0
        assert job.summary.human_decisions_total == 0
        first = _by_package(job, "RP-0001")
        assert not first.accepted and first.decision is None
        assert "never processed through the exception-resolution rerun" in first.reason
        # the reason names the first failing connection — the job is not accepted
        assert "RP-0001" in job.reason

    @needs_real_capture
    def test_revision_one_is_not_accepted_while_two_are_unresolved(self, tmp_path):
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)
        job = accept_production_job(w1)
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert job.revision == 1
        assert job.accepted_package_ids == ("RP-0001",)
        assert job.unresolved_package_ids == ("RP-0002", "RP-0003")
        assert job.failed_package_ids == ()
        assert job.verified_artifacts == (_state(w1, "RP-0001").generated_files[0],)
        assert job.summary.connections_accepted == 1
        accepted = _by_package(job, "RP-0001")
        assert accepted.accepted and accepted.decision == AUTOMATION_DECISION_AUTO
        assert accepted.verification_status == VERIFICATION_STATUS_VERIFIED
        assert accepted.failure_notes == ()
        assert _by_package(job, "RP-0002").decision is None
        assert "RP-0002" in job.reason  # the unresolved connection blocks the job

    @needs_real_capture
    def test_revision_two_is_not_accepted_while_one_is_unresolved(self, tmp_path):
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)
        w2 = _resolve(w1, built, "RP-0002", tmp_path)
        job = accept_production_job(w2)
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert job.revision == 2
        assert job.accepted_package_ids == ("RP-0001", "RP-0002")
        assert job.unresolved_package_ids == ("RP-0003",)
        assert "RP-0003" in job.reason

    @needs_real_capture
    def test_revision_three_is_accepted_only_when_everything_is_verified(self, tmp_path):
        states = _full_sequence(tmp_path)
        w3 = states[-1]
        job = accept_production_job(w3)
        assert job.status == ACCEPTANCE_STATUS_ACCEPTED
        assert job.revision == 3 and job.project_id == PROJECT_ID
        assert job.accepted_package_ids == tuple(CANDIDATE_IDS)
        assert job.unresolved_package_ids == () and job.failed_package_ids == ()
        assert "every connection of project" in job.reason
        assert "revision 3" in job.reason
        assert len(job.verified_artifacts) == 3
        assert len(set(job.verified_artifacts)) == 3  # three distinct artifacts
        assert job.summary.connections_accepted == 3
        assert job.summary.connections_not_accepted == 0
        assert job.summary.human_decisions_refused == 0
        for package_id in CANDIDATE_IDS:
            acceptance = _by_package(job, package_id)
            assert acceptance.accepted
            assert acceptance.decision == AUTOMATION_DECISION_AUTO
            assert acceptance.output_status == OUTPUT_STATUS_GENERATED
            assert acceptance.verification_status == VERIFICATION_STATUS_VERIFIED
            assert acceptance.artifact_exists is True
            assert acceptance.failures == ()
            assert acceptance.failure_notes == ()

    @needs_real_capture
    def test_statuses_are_derived_not_hard_coded(self, tmp_path):
        # The acceptance module knows nothing about these revisions or ids —
        # every status in this file comes from the genuine workflow states.
        assert "RP-000" not in MODULE_SOURCE
        assert "ARKLES" not in MODULE_SOURCE
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)
        w2 = _resolve(w1, built, "RP-0002", tmp_path)
        w3 = _resolve(w2, built, "RP-0003", tmp_path)
        statuses = [accept_production_job(w).status for w in (workflow, w1, w2, w3)]
        assert statuses == [
            ACCEPTANCE_STATUS_NOT_ACCEPTED,
            ACCEPTANCE_STATUS_NOT_ACCEPTED,
            ACCEPTANCE_STATUS_NOT_ACCEPTED,
            ACCEPTANCE_STATUS_ACCEPTED,
        ]


# ===========================================================================
# 3. Artifact proof — the 7AG manifest is the only artifact authority.
# ===========================================================================
class TestArtifactProof:
    @needs_real_capture
    def test_accepted_artifacts_match_their_manifests_and_their_bytes(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3)
        for package_id in CANDIDATE_IDS:
            acceptance = _by_package(job, package_id)
            record = _record(w3, package_id)
            state = _state(w3, package_id)
            # the recorded path, exactly as the workflow and 7AG recorded it
            assert acceptance.verified_artifact == str(record.verification_result.artifact_path)
            assert acceptance.verified_artifact == state.generated_files[0]
            assert acceptance.verified_artifact.endswith("-fabrication.pdf")
            assert CONNECTION_IDENTITIES[package_id] in acceptance.verified_artifact
            # existence, hash and page count corroborate the manifest's own facts
            path = Path(acceptance.verified_artifact)
            assert path.exists()
            assert acceptance.sha256 == record.verification_result.sha256
            assert acceptance.sha256 == _sha256(path)
            assert acceptance.page_count == record.verification_result.page_count
            assert acceptance.page_count == len(PdfReader(path).pages)
            # identity and geometry checks passed in the genuine 7AG manifest
            by_code = {check.code: check.status for check in acceptance.checks}
            assert by_code[CHECK_IDENTITY_VERIFIABLE] == CHECK_PASSED
            assert by_code[CHECK_GEOMETRY_FIELDS_VERIFIABLE] == CHECK_PASSED
            assert by_code[CHECK_DRAWING_CONTENT_PRESENT] == CHECK_PASSED
            assert acceptance.failures == ()

    @needs_real_capture
    def test_no_second_artifact_status_is_invented(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3)
        for acceptance in job.connections:
            # the acceptance repeats the manifest's status — byte for byte,
            # never a re-verdict of its own
            assert acceptance.verification_status == _record(w3, acceptance.package_id).verification_result.verification_status
            assert acceptance.checks == _record(w3, acceptance.package_id).verification_result.checks


# ===========================================================================
# 4. Audit proof — the backward walk, link by link.
# ===========================================================================
class TestAuditProof:
    @needs_real_capture
    def test_walk_backward_from_artifact_to_drawing_evidence(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3)
        for package_id in CANDIDATE_IDS:
            acceptance = _by_package(job, package_id)
            record = _record(w3, package_id)
            state = _state(w3, package_id)
            trace = acceptance.trace
            # artifact -> 7AG verification manifest
            verification = record.verification_result
            assert trace.contract.verification_status == VERIFICATION_STATUS_VERIFIED
            assert verification.verification_status == VERIFICATION_STATUS_VERIFIED
            assert verification.artifact_path == Path(acceptance.verified_artifact)
            # verification -> 7AF dispatch manifest
            assert verification.dispatch_output_status == record.dispatch_result.output_status
            assert verification.connection_id == record.dispatch_result.connection_id
            assert record.dispatch_result.connection_id == CONNECTION_IDENTITIES[package_id]
            assert trace.connection_id == CONNECTION_IDENTITIES[package_id]
            # dispatch -> 7AE fabrication-output gate
            assert record.gate_result.decision == AUTOMATION_DECISION_AUTO
            # gate -> 7AD rerun and its workflow revision
            rerun = record.rerun_outcome
            assert rerun.decision == AUTOMATION_DECISION_AUTO
            assert state.last_processed_revision == CANDIDATE_IDS.index(package_id) + 1
            # rerun -> resolutions: every review task of the connection was answered
            group = _group(w3, package_id)
            assert {d.task_id for d in _applied(acceptance)} == {t.task_id for t in group.tasks}
            # tasks -> blocker codes: every row addresses the exact real codes
            row_codes = {code for d in _applied(acceptance) for code in d.blocker_codes}
            assert row_codes <= set(ARKLES_BLOCKER_CODES)
            assert row_codes == {code for t in group.tasks for code in t.blocker_codes}
            # resolutions -> engineering record: human-owned provenance per field
            for decision in _applied(acceptance):
                assert decision.applied and decision.refusal_reason is None
                assert decision.evidence is not None
                for field, label in decision.field_provenance:
                    assert label == PROVENANCE_HUMAN_SUPPLEMENTED
            # the identity answer is traced verbatim (an identifier — no
            # provenance label, by the existing 7W contract)
            identity_rows = [
                d for d in _applied(acceptance)
                if CONNECTION_IDENTITIES[package_id] == d.answer
            ]
            assert identity_rows and identity_rows[0].fields == ()
            assert identity_rows[0].field_provenance == ()
            # AI observations -> drawing evidence, from the real capture
            assert trace.evidence.source_drawing_id == SOURCE_DRAWING_ID
            candidate = next(
                c for c in w3.collection.candidates if c.review_package_id == package_id
            )
            assert trace.evidence.source_page == candidate.package.extraction.source_page
            # the real capture records no detail or grid reference — the trace
            # invents none either
            assert trace.evidence.detail_reference is None
            assert trace.evidence.grid_reference is None

    @needs_real_capture
    def test_ai_observations_are_lossless_and_uninvented(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3)
        all_bolt_readings = []
        all_plate_readings = []
        for package_id in CANDIDATE_IDS:
            trace = _by_package(job, package_id).trace
            all_bolt_readings.extend(trace.contract.ai_bolt_readings)
            all_plate_readings.extend(trace.contract.ai_plate_readings)
        # the real capture's own nominal sizes, byte-exact — never Ø-inferred
        assert any("'M12'" in reading for reading in all_bolt_readings)
        sq4 = next(
            reading for reading in all_bolt_readings + all_plate_readings
            if "SQ4 12mm" in reading
        )
        assert "Ø" not in sq4
        # and the acceptance module itself invents none of it
        assert "M12" not in MODULE_SOURCE and "SQ4" not in MODULE_SOURCE
        assert "Ø" not in MODULE_SOURCE

    @needs_real_capture
    def test_three_independent_traces(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3)
        traces = [_by_package(job, p).trace for p in CANDIDATE_IDS]
        # each trace carries its own connection identity and its own AI
        # member observations (the real capture's three distinct candidate
        # readings on page 7 — never merged, never duplicated)
        assert {t.connection_id for t in traces} == set(CONNECTION_IDENTITIES.values())
        member_readings = {tuple(t.contract.ai_member_references) for t in traces}
        assert len(member_readings) == 3
        assert member_readings == {
            ("stringer 140x45 H4 SG8", "structure"),
            ("stringer 190x45 H4 SG8", "structure"),
            ("deck joist", "bearer"),
        }
        # and its own independent human decision rows
        for package_id, trace in zip(CANDIDATE_IDS, traces):
            identity = CONNECTION_IDENTITIES[package_id]
            answers = [d.answer for d in _applied(_by_package(job, package_id))]
            assert identity in answers
            assert all(
                other_identity not in answers for other_identity in CONNECTION_IDENTITIES.values()
                if other_identity != identity
            )


# ===========================================================================
# 5. Incremental proof — only the changed connection reruns, ever.
# ===========================================================================
class TestIncrementalProof:
    @needs_real_capture
    def test_each_resolution_reruns_exactly_its_own_connection(self, monkeypatch, tmp_path):
        _, _, built, workflow = _start()
        # spies armed AFTER the initial workflow — the initial pipeline
        # evaluations of _start() are not resolution reruns
        rerun_calls = _spy_rerun(monkeypatch)
        aa_calls = _spy_7aa(monkeypatch)
        gen_calls = _spy_generator(monkeypatch)
        verify_calls = _spy_verify(monkeypatch)
        by_identity = {identity: package for package, identity in CONNECTION_IDENTITIES.items()}

        current = workflow
        for expected_rerun in (("RP-0001",), ("RP-0001", "RP-0002"), CANDIDATE_IDS):
            package_id = expected_rerun[-1]
            current = _resolve(current, built, package_id, tmp_path)
            # each resolve reran ONLY the connection it was asked to resolve
            assert rerun_calls == list(expected_rerun)
            assert [by_identity[identity] for identity in aa_calls] == list(expected_rerun)
            assert [by_identity[identity] for identity in gen_calls] == list(expected_rerun)
            assert [by_identity[identity] for identity in verify_calls] == list(expected_rerun)

        # zero unnecessary reruns across the whole job — 3 reruns, 3 pipelines,
        # 3 generators, 3 verifications for 3 connections
        assert len(rerun_calls) == len(aa_calls) == len(gen_calls) == len(verify_calls) == 3
        assert accept_production_job(current).status == ACCEPTANCE_STATUS_ACCEPTED

    @needs_real_capture
    def test_full_sequence_reruns_each_connection_once_in_order(self, monkeypatch, tmp_path):
        rerun_calls = _spy_rerun(monkeypatch)
        aa_calls = _spy_7aa(monkeypatch)
        by_identity = {identity: package for package, identity in CONNECTION_IDENTITIES.items()}
        states = _full_sequence(tmp_path)
        assert rerun_calls == list(CANDIDATE_IDS)
        # the initial intake pipelines carry no connection id (identity is
        # human-supplied later); every identified 7AA evaluation is a rerun
        identified = [by_identity[identity] for identity in aa_calls if identity is not None]
        assert identified == list(CANDIDATE_IDS)
        assert accept_production_job(states[-1]).status == ACCEPTANCE_STATUS_ACCEPTED


# ===========================================================================
# 6. Failure paths — every failure is visible and blocks acceptance.
# ===========================================================================
class TestFailurePaths:
    @needs_real_capture
    def test_unresolved_review_is_never_accepted(self, tmp_path):
        _, _, _, workflow = _start()
        job = accept_production_job(workflow)
        for package_id in CANDIDATE_IDS:
            acceptance = _by_package(job, package_id)
            assert not acceptance.accepted
            assert acceptance.decision is None
            assert acceptance.verification_status is None
            assert acceptance.verified_artifact is None
            assert acceptance.artifact_exists is None

    @needs_real_capture
    def test_automation_review_is_never_accepted(self, tmp_path):
        # approval + identity only — the genuine anti-bypass: no engineering
        # answers, so the rerun decides REVIEW and nothing is fabricated
        _, _, built, workflow = _start()
        w1 = _resolve(
            workflow, built, "RP-0001", tmp_path,
            resolutions=_partial_resolutions(built, "RP-0001"),
        )
        job = accept_production_job(w1)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert acceptance.decision == AUTOMATION_DECISION_REVIEW
        assert "automation decision" in acceptance.reason and "'REVIEW'" in acceptance.reason
        # the genuine workflow still ran the dispatch — blocked, never generated
        assert acceptance.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        assert acceptance.verified_artifact is None
        assert acceptance.verification_status == "NO_ARTIFACT"
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert job.failed_package_ids == ("RP-0001",)

    @needs_real_capture
    def test_blocked_fabrication_gate_is_never_accepted(self, tmp_path):
        # The genuine workflow never produces a REVIEW gate on an AUTO rerun;
        # a tampered gate must still be refused.
        w3 = _full_sequence(tmp_path)[-1]
        record = _record(w3, "RP-0001")
        forged_gate = dataclasses.replace(record.gate_result, decision=AUTOMATION_DECISION_REVIEW)
        tampered = _with_record(w3, "RP-0001", gate_result=forged_gate)
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "fabrication-output gate decided 'REVIEW'" in acceptance.reason
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert job.accepted_package_ids == ("RP-0002", "RP-0003")

    @needs_real_capture
    def test_generation_failure_is_never_accepted(self, tmp_path):
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)

        def exploding_entry(assembly, output_path, **kwargs):
            raise RuntimeError("simulated generator failure for the test")

        w2 = _resolve(w1, built, "RP-0002", tmp_path, drawing_entry=exploding_entry)
        job = accept_production_job(w2)
        acceptance = _by_package(job, "RP-0002")
        assert not acceptance.accepted
        assert "GENERATION_FAILED" in acceptance.reason
        assert acceptance.output_status == "GENERATION_FAILED"
        assert acceptance.verified_artifact is None
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        # RP-0001 is untouched and still accepted
        assert _by_package(job, "RP-0001").accepted

    @needs_real_capture
    def test_verification_failure_is_never_accepted(self, tmp_path):
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)

        def garbage_entry(assembly, output_path, **kwargs):
            Path(output_path).write_bytes(GARBAGE_BYTES)

        w2 = _resolve(w1, built, "RP-0002", tmp_path, drawing_entry=garbage_entry)
        job = accept_production_job(w2)
        acceptance = _by_package(job, "RP-0002")
        assert not acceptance.accepted
        assert acceptance.verification_status == VERIFICATION_STATUS_FAILED
        assert "not 'VERIFIED'" in acceptance.reason
        assert acceptance.failures  # the genuine 7AG failure checks are visible
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_missing_recorded_artifact_is_never_accepted(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        victim = _state(w3, "RP-0002").generated_files[0]
        Path(victim).unlink()
        job = accept_production_job(w3)
        acceptance = _by_package(job, "RP-0002")
        assert not acceptance.accepted
        assert "no longer exists on disk" in acceptance.reason
        assert acceptance.artifact_exists is False
        assert acceptance.verified_artifact == victim
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        # the other two artifacts are still accepted, and only RP-0002 failed
        assert job.accepted_package_ids == ("RP-0001", "RP-0003")
        assert job.failed_package_ids == ("RP-0002",)

    @needs_real_capture
    def test_stale_revision_is_refused(self, tmp_path):
        # 14G: the acceptance layer's own stale-state failure — never accepted.
        w3 = _full_sequence(tmp_path)[-1]
        job = accept_production_job(w3, expected_revision=1)
        assert job.status == ACCEPTANCE_STATUS_REFUSED
        assert job.connections == () and job.verified_artifacts == ()


# ===========================================================================
# 7. Tamper / false-positive refusal — injected state is detected, not trusted.
# ===========================================================================
class TestTamperFalsePositives:
    @needs_real_capture
    def test_injected_auto_without_records_is_refused(self, tmp_path):
        _, _, _, workflow = _start()
        tampered = _with_state(workflow, "RP-0001", decision=AUTOMATION_DECISION_AUTO)
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "disagrees with the recorded initial automation decision" in acceptance.reason
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_injected_verified_status_on_failed_connection_is_refused(self, tmp_path):
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)

        def garbage_entry(assembly, output_path, **kwargs):
            Path(output_path).write_bytes(GARBAGE_BYTES)

        w2 = _resolve(w1, built, "RP-0002", tmp_path, drawing_entry=garbage_entry)
        assert _state(w2, "RP-0002").verification_status == VERIFICATION_STATUS_FAILED
        tampered = _with_state(w2, "RP-0002", verification_status=VERIFICATION_STATUS_VERIFIED)
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0002")
        assert not acceptance.accepted
        assert "disagrees with the recorded verification status" in acceptance.reason
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_forged_manifest_naming_a_different_file_is_refused(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        record = _record(w3, "RP-0001")
        dummy = tmp_path / "fake-artifact.pdf"
        dummy.write_bytes(b"a forged artifact that was never dispatched")
        forged = dataclasses.replace(
            record.verification_result,
            artifact_path=dummy,
            sha256=hashlib.sha256(dummy.read_bytes()).hexdigest(),
        )
        tampered = _with_record(w3, "RP-0001", verification_result=forged)
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "names an artifact that the dispatch manifest did not produce" in acceptance.reason
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_changed_artifact_bytes_after_verification_are_refused(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        victim = _state(w3, "RP-0001").generated_files[0]
        Path(victim).write_bytes(b"bytes changed after verification")
        job = accept_production_job(w3)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "no longer matches its verification manifest hash" in acceptance.reason
        assert acceptance.artifact_exists is True  # exists, but no longer the verified bytes
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_stale_artifact_after_failed_regeneration_is_never_accepted(self, tmp_path):
        # A file on disk at the expected name does NOT rescue a failed
        # generation — the recorded dispatch manifest is the truth.
        _, _, built, workflow = _start()
        w1 = _resolve(workflow, built, "RP-0001", tmp_path)

        def exploding_entry(assembly, output_path, **kwargs):
            raise RuntimeError("simulated generator failure for the test")

        w2 = _resolve(w1, built, "RP-0002", tmp_path, drawing_entry=exploding_entry)
        (tmp_path / "CONN-ARKLES-002-fabrication.pdf").write_bytes(b"a stray file")
        job = accept_production_job(w2)
        acceptance = _by_package(job, "RP-0002")
        assert not acceptance.accepted
        assert acceptance.output_status == "GENERATION_FAILED"
        assert acceptance.verified_artifact is None
        assert "GENERATION_FAILED" in acceptance.reason

    @needs_real_capture
    def test_untraceable_human_decision_is_refused(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        record = _record(w3, "RP-0001")
        rerun = record.rerun_outcome
        forged_decision = HumanResolution(
            "RP-0001-FORGED", "FORGED_TASK", ANSWER_FIELD_DECISION,
            ("holes", FIELD_DECISION_SUPPLY, {"quantity": 2, "diameter_mm": 12.0, "spacings": [50.0]}),
            evidence="forged human decision",
        )
        tampered_rerun = dataclasses.replace(
            rerun, resolutions_applied=rerun.resolutions_applied + (forged_decision,),
        )
        tampered = _with_record(w3, "RP-0001", rerun_outcome=tampered_rerun)
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "cannot be traced to its review task" in acceptance.reason
        # the forged decision is still visible in the audit, marked for what it is
        assert any(not d.applied or d.task_id == "RP-0001-FORGED" for d in acceptance.trace.human_decisions)
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_injected_unresolved_blockers_are_refused(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        record = _record(w3, "RP-0001")
        rerun = record.rerun_outcome
        tampered_rerun = dataclasses.replace(
            rerun,
            blockers=rerun.blockers + (AutomationFinding(AUTOMATION_BLOCKER_REVIEW_STATUS, "forged"),),
        )
        tampered = _with_record(w3, "RP-0001", rerun_outcome=tampered_rerun)
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "automation blocker(s) remain" in acceptance.reason
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_injected_connection_id_without_resolved_blockers_is_refused(self, tmp_path):
        _, _, _, workflow = _start()
        tampered = _with_state(workflow, "RP-0001", connection_id="CONN-FORGED-001")
        job = accept_production_job(tampered)
        acceptance = _by_package(job, "RP-0001")
        assert not acceptance.accepted
        assert "has no drawing dispatch manifest behind it" in acceptance.reason
        assert job.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    @needs_real_capture
    def test_an_approved_field_is_ignored_and_changes_nothing(self, tmp_path):
        # A fabricated "approved" attribute on the state is never read: the
        # acceptance is identical with or without it.
        w3 = _full_sequence(tmp_path)[-1]
        genuine = accept_production_job(w3)
        object.__setattr__(w3, "approved", True)
        assert accept_production_job(w3) == genuine
        assert not hasattr(ProductionJobAcceptance, "approved")


# ===========================================================================
# 8. Purity — deterministic, side-effect free, isolated, uninvented.
# ===========================================================================
class TestPurity:
    @needs_real_capture
    def test_deterministic_and_side_effect_free(self, tmp_path):
        w3 = _full_sequence(tmp_path)[-1]
        before_hashes = {path.name: _sha256(path) for path in _pdfs(tmp_path)}
        first = accept_production_job(w3)
        second = accept_production_job(w3)
        assert first == second
        after_hashes = {path.name: _sha256(path) for path in _pdfs(tmp_path)}
        assert after_hashes == before_hashes  # nothing was written or changed
        assert len(_pdfs(tmp_path)) == 3

    def test_imports_only_cad_engine_and_stdlib(self):
        tree = ast.parse(MODULE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name in ("hashlib",) for alias in node.names), node.names
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or node.module.startswith(("app.cad_engine", "dataclasses", "pathlib", "hashlib")), node.module

    def test_no_network_database_env_or_ui_dependency(self):
        lowered = MODULE_SOURCE.lower()
        for banned in (
            "supabase", "os.environ", "getenv", "import http", "requests",
            "flask", "fastapi", "review_ui", "app.main", "socket",
        ):
            assert banned not in lowered

    def test_no_engineering_evidence_is_invented(self):
        # No nominal sizes, units, diameters or coordinates appear in the
        # module — everything it reports comes from the workflow it inspects.
        assert "M12" not in MODULE_SOURCE and "SQ4" not in MODULE_SOURCE
        assert "Ø" not in MODULE_SOURCE
        assert not re.search(r"\bmm\b", MODULE_SOURCE)
        # the only numeric literals are 0 and 1 (indices / emptiness checks)
        numbers = {m.group() for m in re.finditer(r"\b\d+(?:\.\d+)?\b", MODULE_SOURCE)}
        assert numbers <= {"0", "1"}, numbers
        # no fabricated artifact hashes
        assert not re.search(r"[0-9a-f]{32,}", MODULE_SOURCE)
