"""7AQ Production Job Acceptance — the human-to-output proof.

One production job, end to end: drawing set -> extraction -> connection
candidates -> review exceptions -> human resolutions -> incremental
reruns -> automation decisions -> fabrication output gate -> drawing
generation -> artifact verification -> final project acceptance.

PRODUCTION_ACCEPTANCE_SCOPE_STATEMENT (7AQ's own boundary, in force for
every result this module produces):

  No connection can reach a verified fabrication output unless every
  required engineering decision passed through the existing
  validation, automation, fabrication-output, drawing-generation and
  artifact-verification boundaries; and every human-supplied
  engineering decision must remain traceable back to the original AI
  observation and the exact review task that required human
  intervention.

This module NEVER decides engineering. It is a read-only, frozen,
deterministic proof layer over an existing `ProjectWorkflowState`
(the 7AJ workflow): it consumes the workflow's recorded stage
evidence — the 7AD rerun outcome, the 7AE fabrication-output gate
result, the 7AF dispatch manifest and the 7AG verification manifest —
and derives ACCEPTED / NOT_ACCEPTED / REFUSED from those genuine
outputs alone.

HARD RULES (never broken):

  * The visible `ProjectConnectionState` projection is corroboration,
    never evidence. An acceptance is derived from the connection's
    `ProjectConnectionRecord` stage results; a projection that
    disagrees with its own recorded evidence makes the connection
    NOT_ACCEPTED (injected AUTO/VERIFIED/artifacts are detected, not
    trusted).
  * No decision is inferred from file existence, filenames, counts or
    statuses. `VERIFICATION_STATUS_VERIFIED` means only what 7AG's
    manifest says it means. The single on-disk existence check below
    (7AJ's own `_artifact_presence_notes` precedent) corroborates that
    the RECORDED artifact is still present; it never substitutes for
    verification status.
  * Stale state is never accepted: a caller revision that is not the
    workflow's current revision produces REFUSED (the acceptance
    layer's equivalent of `resolve_project_connection`'s
    `StaleProjectWorkflowError`).
  * Human provenance vocabulary is the existing one
    (AI_EXTRACTED / HUMAN_REVIEWED / HUMAN_SUPPLEMENTED); no
    "trusted"/"verified by AI" status is invented. A connection
    identity is an identifier, not an engineering field, so it carries
    no provenance label (7W's documented contract) and none is
    required here.
  * Nothing is written, mutated or generated. No database, network,
    environment, UI or HTTP dependency. Pure frozen data in, pure
    frozen data out.
"""

from dataclasses import dataclass
from pathlib import Path
import hashlib

from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
    ArtifactCheck,
)
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_FIELD_DECISION,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
    FIELD_DECISION_CONFIRM_AI,
    FIELD_DECISION_SUPPLY,
    HumanResolution,
)
from app.cad_engine.project_automation import ProjectIntakeScope
from app.cad_engine.project_workflow import (
    ProjectConnectionRecord,
    ProjectConnectionState,
    ProjectWorkflowState,
)
from app.cad_engine.resolution_rerun import ConnectionRerunOutcome
from app.cad_engine.review_contract import (
    ConnectionReviewContract,
    ReviewEvidenceInfo,
    build_connection_review_contract,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)

PRODUCTION_ACCEPTANCE_SCOPE_STATEMENT = (
    "No connection can reach a verified fabrication output unless every required engineering "
    "decision passed through the existing validation, automation, fabrication-output, "
    "drawing-generation and artifact-verification boundaries; and every human-supplied "
    "engineering decision must remain traceable back to the original AI observation and the "
    "exact review task that required human intervention."
)

# --------------------------------------------------------------------------------------
# Deterministic presentation table. Keys are ONLY answer types the existing 7AC contract
# emits; the field mapping mirrors the existing supplement's own mapping
# (`_SUPPLIED_FIELD_BY_ANSWER` in exception_resolution) so nothing here can disagree with
# the workflow. ANSWER_FIELD_DECISION names its field inside the answer; ANSWER_CONFIRMED_FIELDS
# names a tuple of fields; everything else supplies no engineering field.
# --------------------------------------------------------------------------------------
_ANSWER_FIELDS: dict[str, tuple[str, ...]] = {
    ANSWER_MEMBER_SELECTION: ("connected_member_marks",),
    ANSWER_POSITION_VALUE: ("position",),
    ANSWER_PLATE_VALUE: ("plate",),
    ANSWER_HOLES_VALUE: ("holes",),
    ANSWER_LOCATION_VALUE: ("location",),
    ANSWER_ATTACHMENTS_VALUE: ("attachments",),
    ANSWER_MATERIAL_VALUE: ("material",),
    ANSWER_MEMBER_POSITION_ATTACHMENTS: ("connected_member_marks", "position", "attachments"),
}

# Human-owned provenance: the only labels that prove a human decision reached the
# engineering record. Confirming the AI value yields HUMAN_REVIEWED; supplying a value
# yields HUMAN_SUPPLEMENTED (the existing 7W labels, re-used — nothing invented).
_HUMAN_OWNED_PROVENANCE = frozenset((PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED))

ACCEPTANCE_STATUS_ACCEPTED = "ACCEPTED"
ACCEPTANCE_STATUS_NOT_ACCEPTED = "NOT_ACCEPTED"
ACCEPTANCE_STATUS_REFUSED = "REFUSED"
ACCEPTANCE_STATUSES = (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
)


# --------------------------------------------------------------------------------------
# The frozen acceptance models.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HumanDecisionTrace:
    """
    One human-supplied engineering decision, made auditable: the exact
    review task that required human intervention (task id, type,
    question and blocker codes), the answer exactly as supplied
    (verbatim, never interpreted), the engineering field(s) it
    touched, and the existing review report's provenance label for
    each such field after the rerun. `applied` is False for answers
    the existing rerun refused (the refusal reason is recorded
    verbatim — a refused answer is traced, never hidden and never
    silently treated as applied).
    """
    task_id: str
    task_type: str
    question: str | None
    blocker_codes: tuple[str, ...]
    answer_type: str
    answer: object
    evidence: str | None
    fields: tuple[str, ...]
    field_provenance: tuple[tuple[str, str | None], ...]
    applied: bool
    refusal_reason: str | None


@dataclass(frozen=True)
class AcceptanceTrace:
    """
    The complete audit chain for one connection, all of it verbatim
    from existing records: where the connection came from (the 7AK
    evidence), every AI observation (the full 7AK review contract —
    AI values, provenance, tasks and statuses, nothing re-derived),
    and every human decision traced above. Walking backward:
    artifact -> 7AG manifest -> 7AF dispatch -> 7AE gate -> 7AD rerun
    -> resolutions -> tasks -> blocker codes -> AI observations ->
    drawing evidence.
    """
    package_id: str
    connection_id: str | None
    evidence: ReviewEvidenceInfo
    contract: ConnectionReviewContract
    human_decisions: tuple[HumanDecisionTrace, ...]


@dataclass(frozen=True)
class ConnectionAcceptance:
    """
    One connection's derived acceptance. `accepted` is True exactly
    when every condition below held on the connection's RECORDED stage
    evidence; `reason` says which condition failed otherwise.
    `failure_notes` carries the individual findings (a rejected
    connection lists each one; an accepted connection has none).
    The artifact facts come from the 7AG manifest verbatim;
    `artifact_exists` is the one on-disk corroboration (None when no
    verification ran).
    """
    package_id: str
    connection_id: str | None
    accepted: bool
    reason: str
    decision: str | None
    output_status: str | None
    verification_status: str | None
    verified_artifact: str | None
    artifact_exists: bool | None
    sha256: str | None
    page_count: int | None
    checks: tuple[ArtifactCheck, ...]
    failures: tuple[ArtifactCheck, ...]
    failure_notes: tuple[str, ...]
    trace: AcceptanceTrace


@dataclass(frozen=True)
class AcceptanceSummary:
    """Project-level counts, computed from the individual connection acceptances
    (never asserted, never a score)."""
    connections_total: int
    connections_accepted: int
    connections_not_accepted: int
    connections_unresolved: int
    verified_artifacts: tuple[str, ...]
    human_decisions_total: int
    human_decisions_applied: int
    human_decisions_refused: int


@dataclass(frozen=True)
class ProductionJobAcceptance:
    """
    The final production-job proof. ACCEPTED only when the extraction
    intake ran and produced candidates, and EVERY connection is
    accepted on its recorded evidence. NOT_ACCEPTED otherwise, with
    the per-connection detail naming exactly what failed. REFUSED when
    the request itself cannot be evaluated (stale caller revision, or
    a state that carries no workflow context) — a refusal never
    silently becomes an acceptance.
    """
    status: str
    reason: str
    project_id: str | None
    revision: int
    caller_revision: int | None
    intake_scope: ProjectIntakeScope | None
    extraction_sufficient: bool | None
    connections: tuple[ConnectionAcceptance, ...]
    accepted_package_ids: tuple[str, ...]
    unresolved_package_ids: tuple[str, ...]
    failed_package_ids: tuple[str, ...]
    verified_artifacts: tuple[str, ...]
    summary: AcceptanceSummary


# --------------------------------------------------------------------------------------
# Derivation helpers — pure functions over recorded data, never new evidence.
# --------------------------------------------------------------------------------------
def _resolution_fields(resolution: HumanResolution) -> tuple[str, ...]:
    """The engineering field(s) one resolution touches, from its own answer. Never guessed."""
    if resolution.answer_type == ANSWER_FIELD_DECISION:
        field, _action, _value = resolution.answer
        return (field,)
    if resolution.answer_type == ANSWER_CONFIRMED_FIELDS:
        return tuple(resolution.answer)
    return _ANSWER_FIELDS.get(resolution.answer_type, ())


def _resolution_requires_human_provenance(resolution: HumanResolution) -> bool:
    """A human decision that supplied or confirmed an engineering VALUE must show
    human-owned provenance for its field(s) in the rerun's review report. Clearing a
    field supplies no value, so it requires none (and the existing gate would block the
    connection for the now-missing field anyway)."""
    if resolution.answer_type == ANSWER_FIELD_DECISION:
        return resolution.answer[1] in (FIELD_DECISION_CONFIRM_AI, FIELD_DECISION_SUPPLY)
    return resolution.answer_type in _ANSWER_FIELDS or resolution.answer_type == ANSWER_CONFIRMED_FIELDS


def _human_decision_traces(
    workflow: ProjectWorkflowState,
    contract: ConnectionReviewContract,
    rerun_outcome: ConnectionRerunOutcome | None,
) -> tuple[HumanDecisionTrace, ...]:
    """The audit rows for one connection's human decisions — applied and refused alike.
    The task question and blocker codes come from the connection's own 7AC exception
    tasks (the exact review task that required human intervention — stable across
    revisions); the provenance comes from the 7AK contract's review report. A refused
    answer is traced with its refusal reason and requires nothing (it never reached the
    engineering record)."""
    group = next(
        (g for g in workflow.exception_package.connection_tasks
         if g.review_package_id == contract.package_id),
        None,
    )
    group_tasks_by_id = {task.task_id: task for task in group.tasks} if group is not None else {}
    provenance = {entry.field: entry.provenance for entry in contract.provenance}
    traces: list[HumanDecisionTrace] = []
    if rerun_outcome is None:
        return ()

    def row(resolution: HumanResolution, *, applied: bool, refusal_reason: str | None) -> HumanDecisionTrace:
        task = group_tasks_by_id.get(resolution.task_id)
        fields = _resolution_fields(resolution)
        field_provenance = tuple((field, provenance.get(field)) for field in fields)
        if task is None:
            return HumanDecisionTrace(
                task_id=resolution.task_id, task_type=resolution.task_type,
                question=None, blocker_codes=(),
                answer_type=resolution.answer_type, answer=resolution.answer,
                evidence=resolution.evidence, fields=fields,
                field_provenance=field_provenance, applied=applied,
                refusal_reason=refusal_reason,
            )
        return HumanDecisionTrace(
            task_id=resolution.task_id, task_type=task.task_type,
            question=task.question, blocker_codes=task.blocker_codes,
            answer_type=resolution.answer_type, answer=resolution.answer,
            evidence=resolution.evidence, fields=fields,
            field_provenance=field_provenance, applied=applied,
            refusal_reason=refusal_reason,
        )

    for resolution in rerun_outcome.resolutions_applied:
        traces.append(row(resolution, applied=True, refusal_reason=None))
    for refused in rerun_outcome.resolutions_refused:
        traces.append(row(refused.resolution, applied=False, refusal_reason=refused.reason))
    return tuple(traces)


def _traceability_findings(
    contract: ConnectionReviewContract,
    rerun_outcome: ConnectionRerunOutcome,
) -> list[str]:
    """Every applied human decision that touched an engineering value must be traceable
    to its task and to human-owned provenance in the rerun's review report. Refused
    answers are traced with their refusal reason and require nothing (they never reached
    the engineering record)."""
    findings: list[str] = []
    tasks_by_id = {task.task_id: task for task in contract.tasks}
    provenance = {entry.field: entry.provenance for entry in contract.provenance}
    for resolution in rerun_outcome.resolutions_applied:
        if resolution.task_id not in tasks_by_id:
            findings.append(
                f"human decision task {resolution.task_id!r} is not present in the review "
                "contract — the decision cannot be traced to its review task."
            )
            continue
        if not _resolution_requires_human_provenance(resolution):
            continue
        for field in _resolution_fields(resolution):
            label = provenance.get(field)
            if label not in _HUMAN_OWNED_PROVENANCE:
                findings.append(
                    f"field {field!r} supplied or confirmed by task {resolution.task_id} "
                    f"carries provenance {label!r}, not a human-owned label "
                    f"{sorted(_HUMAN_OWNED_PROVENANCE)!r}; the human decision left no "
                    "trace in the engineering record."
                )
    return findings


def _build_trace(
    workflow: ProjectWorkflowState,
    connection: ProjectConnectionState,
    contract: ConnectionReviewContract,
    rerun_outcome: ConnectionRerunOutcome | None,
) -> AcceptanceTrace:
    """The audit chain — nothing here is derived; everything is a verbatim existing object."""
    return AcceptanceTrace(
        package_id=connection.package_id,
        connection_id=connection.connection_id,
        evidence=contract.evidence,
        contract=contract,
        human_decisions=_human_decision_traces(workflow, contract, rerun_outcome),
    )


def _evaluate_connection(
    workflow: ProjectWorkflowState,
    connection: ProjectConnectionState,
    record: ProjectConnectionRecord,
) -> ConnectionAcceptance:
    """One connection's acceptance, from its recorded stage evidence only. The visible
    projection is compared against that evidence — a projection that disagrees with its
    own records is treated as tampered and NOT accepted."""
    findings: list[str] = []
    contract = build_connection_review_contract(workflow, connection.package_id)
    rerun = record.rerun_outcome
    gate = record.gate_result
    dispatch = record.dispatch_result
    verification = record.verification_result

    if rerun is None:
        # Never processed through 7AD: no automation, fabrication, drawing or
        # verification stage ever ran for it. Its human review tasks are
        # unresolved. The projection is still held against the recorded initial
        # pipeline — a visible AUTO/status/artifact with nothing recorded behind
        # it is reported, not trusted.
        unresolved_findings: list[str] = []
        if record.pipeline is not None:
            initial_decision = record.pipeline.automation_gate_result.decision
            if connection.decision != initial_decision:
                unresolved_findings.append(
                    f"the visible decision {connection.decision!r} disagrees with the recorded "
                    f"initial automation decision {initial_decision!r}."
                )
        if connection.output_status is not None:
            unresolved_findings.append(
                f"the visible output status {connection.output_status!r} has no drawing dispatch "
                "manifest behind it."
            )
        if connection.verification_status is not None:
            unresolved_findings.append(
                f"the visible verification status {connection.verification_status!r} has no "
                "artifact verification manifest behind it."
            )
        if connection.connection_id is not None:
            unresolved_findings.append(
                f"the visible connection id {connection.connection_id!r} has no drawing dispatch "
                "manifest behind it."
            )
        if connection.generated_files:
            unresolved_findings.append(
                "the visible generated-file list records artifacts that were never dispatched."
            )
        trace = _build_trace(workflow, connection, contract, None)
        reason = unresolved_findings[0] if unresolved_findings else (
            f"{connection.package_id} was never processed through the exception-resolution "
            "rerun; no automation decision, fabrication gate, drawing dispatch or artifact "
            "verification exists for it."
        )
        return ConnectionAcceptance(
            package_id=connection.package_id, connection_id=connection.connection_id,
            accepted=False,
            reason=reason,
            decision=None, output_status=None, verification_status=None,
            verified_artifact=None, artifact_exists=None, sha256=None, page_count=None,
            checks=(), failures=(), failure_notes=tuple(unresolved_findings[1:]),
            trace=trace,
        )

    # ---- the visible projection must agree with its own recorded evidence ----
    if connection.decision != rerun.decision:
        findings.append(
            f"the visible decision {connection.decision!r} disagrees with the recorded "
            f"rerun decision {rerun.decision!r}."
        )
    if connection.last_processed_revision is None:
        findings.append("the connection was processed but records no processing revision.")
    if dispatch is not None:
        if connection.output_status != dispatch.output_status:
            findings.append(
                f"the visible output status {connection.output_status!r} disagrees with the "
                f"recorded dispatch status {dispatch.output_status!r}."
            )
        if connection.connection_id != dispatch.connection_id:
            findings.append(
                f"the visible connection id {connection.connection_id!r} disagrees with the "
                f"recorded dispatch id {dispatch.connection_id!r}."
            )
        if tuple(connection.generated_files) != tuple(str(path) for path in dispatch.generated_files):
            findings.append(
                "the visible generated-file list disagrees with the recorded dispatch manifest."
            )
    elif connection.connection_id is not None:
        findings.append(
            f"the visible connection id {connection.connection_id!r} has no drawing dispatch "
            "manifest behind it."
        )
    if verification is not None and connection.verification_status != verification.verification_status:
        findings.append(
            f"the visible verification status {connection.verification_status!r} disagrees "
            f"with the recorded verification status {verification.verification_status!r}."
        )

    # ---- the genuine 7AD/7AA/7Z automation decision ----
    if rerun.decision != AUTOMATION_DECISION_AUTO:
        findings.append(
            f"the automation decision is {rerun.decision!r}, not "
            f"{AUTOMATION_DECISION_AUTO!r}; the reviewed information did not satisfy every "
            "automation requirement."
        )
    if rerun.blockers:
        findings.append(
            "automation blocker(s) remain: " + ", ".join(finding.code for finding in rerun.blockers) + "."
        )
    if rerun.remaining_task_ids:
        findings.append(
            "unresolved exception task(s): " + ", ".join(rerun.remaining_task_ids) + "."
        )

    # ---- the genuine 7AE fabrication-output gate ----
    if gate is None:
        findings.append("no fabrication-output gate result was recorded for this connection.")
    elif gate.decision != AUTOMATION_DECISION_AUTO:
        findings.append(
            f"the fabrication-output gate decided {gate.decision!r}, not "
            f"{AUTOMATION_DECISION_AUTO!r}; fabrication output was not permitted."
        )

    # ---- the genuine 7AF drawing dispatch ----
    if dispatch is None:
        findings.append("no drawing dispatch manifest was recorded for this connection.")
    elif dispatch.output_status != OUTPUT_STATUS_GENERATED:
        findings.append(
            f"the drawing dispatch recorded output status {dispatch.output_status!r}, not "
            f"{OUTPUT_STATUS_GENERATED!r}."
        )
    if dispatch is not None and not dispatch.generated_files:
        findings.append("the dispatch manifest records no generated artifact.")

    # ---- the genuine 7AG artifact verification ----
    if verification is None:
        findings.append("no artifact verification manifest was recorded for this connection.")
    elif verification.verification_status != VERIFICATION_STATUS_VERIFIED:
        findings.append(
            f"the artifact verification recorded status {verification.verification_status!r}, "
            f"not {VERIFICATION_STATUS_VERIFIED!r}."
        )
    if verification is not None and verification.artifact_path is None:
        findings.append("the verification manifest records no artifact path.")
    if verification is not None and dispatch is not None:
        if verification.dispatch_output_status != dispatch.output_status:
            findings.append(
                f"the verification manifest names dispatch status "
                f"{verification.dispatch_output_status!r} but the dispatch manifest records "
                f"{dispatch.output_status!r}."
            )
        if dispatch.generated_files and Path(verification.artifact_path) != Path(dispatch.generated_files[0]):
            findings.append(
                "the verification manifest names an artifact that the dispatch manifest did "
                "not produce."
            )

    # ---- artifact presence and integrity: the recorded artifact, nothing else ----
    # The on-disk checks corroborate the RECORDED artifact only (7AJ's
    # `_artifact_presence_notes` precedent): the path the manifest names must
    # still exist, and when the manifest records a sha256 the file's bytes must
    # still match it. Neither check ever substitutes for the 7AG verification
    # status above.
    artifact_path: str | None = None
    artifact_exists: bool | None = None
    if verification is not None and verification.artifact_path is not None:
        artifact_path = str(verification.artifact_path)
        artifact_exists = Path(artifact_path).exists()
        if not artifact_exists:
            findings.append(f"the recorded artifact {artifact_path!r} no longer exists on disk.")
        elif verification.sha256 is not None:
            actual_sha256 = hashlib.sha256(Path(artifact_path).read_bytes()).hexdigest()
            if actual_sha256 != verification.sha256:
                findings.append(
                    f"the recorded artifact {artifact_path!r} no longer matches its verification "
                    f"manifest hash ({verification.sha256!r}); the bytes were changed after "
                    "verification."
                )

    # ---- human-to-record traceability ----
    findings.extend(_traceability_findings(contract, rerun))

    accepted = not findings
    reason = (
        "every required engineering decision passed the existing validation, automation, "
        "fabrication-output, drawing-generation and artifact-verification boundaries, and "
        "every human-supplied engineering decision is traceable to its review task and the "
        "engineering record."
        if accepted else findings[0]
    )
    return ConnectionAcceptance(
        package_id=connection.package_id, connection_id=connection.connection_id,
        accepted=accepted, reason=reason,
        decision=rerun.decision,
        output_status=dispatch.output_status if dispatch is not None else None,
        verification_status=verification.verification_status if verification is not None else None,
        verified_artifact=artifact_path,
        artifact_exists=artifact_exists,
        sha256=verification.sha256 if verification is not None else None,
        page_count=verification.page_count if verification is not None else None,
        checks=verification.checks if verification is not None else (),
        failures=verification.failures if verification is not None else (),
        failure_notes=tuple(findings[1:]) if not accepted else (),
        trace=_build_trace(workflow, connection, contract, rerun),
    )


def _refused(workflow: ProjectWorkflowState, reason: str, caller_revision: int | None) -> ProductionJobAcceptance:
    """A request that cannot be evaluated. Nothing is accepted, and the refusal says why."""
    return ProductionJobAcceptance(
        status=ACCEPTANCE_STATUS_REFUSED,
        reason=reason,
        project_id=workflow.project_id,
        revision=workflow.revision,
        caller_revision=caller_revision,
        intake_scope=None,
        extraction_sufficient=None,
        connections=(),
        accepted_package_ids=(),
        unresolved_package_ids=(),
        failed_package_ids=(),
        verified_artifacts=(),
        summary=AcceptanceSummary(
            connections_total=0, connections_accepted=0, connections_not_accepted=0,
            connections_unresolved=0, verified_artifacts=(),
            human_decisions_total=0, human_decisions_applied=0, human_decisions_refused=0,
        ),
    )


# --------------------------------------------------------------------------------------
# The public acceptance entry.
# --------------------------------------------------------------------------------------
def accept_production_job(workflow, *, expected_revision=None) -> ProductionJobAcceptance:
    """
    The final production-job proof for an existing `ProjectWorkflowState`.

    ACCEPTED only when the extraction intake ran and produced connection
    candidates, and EVERY connection is accepted on its recorded stage
    evidence (7AD rerun decision AUTO with no blockers and no remaining
    tasks, 7AE gate AUTO, 7AF dispatch GENERATED with an artifact, 7AG
    verification VERIFIED with the recorded artifact still present, the
    visible projection agreeing with its records, and every applied human
    engineering decision traceable to human-owned provenance).

    NOT_ACCEPTED otherwise, with each connection's failure named exactly.

    REFUSED when the request cannot be evaluated at all: `expected_revision`
    (when given) is not the workflow's current revision — a stale caller
    never receives an acceptance for state they did not see — or the state
    carries no workflow context (exception-resolution contract / review
    collection) to prove anything against.

    Reads only. Never mutates, never generates, never decides engineering,
    never touches the network, database, environment, UI or HTTP layer.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__}); a "
            "production job is accepted against the existing project workflow state."
        )
    if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
        raise TypeError("expected_revision must be an int or None.")

    if expected_revision is not None and expected_revision != workflow.revision:
        return _refused(
            workflow,
            f"the workflow is at revision {workflow.revision} but the caller holds revision "
            f"{expected_revision}; a stale acceptance request is never satisfied by state the "
            "caller did not see.",
            expected_revision,
        )
    if workflow.exception_package is None:
        return _refused(
            workflow,
            "the workflow carries no exception-resolution contract; only states built by "
            "start_project_workflow can prove a production job.",
            expected_revision,
        )
    if workflow.collection is None:
        return _refused(
            workflow,
            "the workflow carries no review collection; only states built by "
            "start_project_workflow can prove a production job.",
            expected_revision,
        )

    intake_scope = workflow.exception_package.intake_scope
    extraction_sufficient = (
        intake_scope is not None
        and intake_scope.pages_received is not None
        and intake_scope.pages_received > 0
    )

    if len(workflow.connections) != len(workflow.connection_records):
        return _refused(
            workflow,
            "the workflow's connection projection and its recorded stage evidence do not "
            "align one-to-one; nothing is accepted from an inconsistent state.",
            expected_revision,
        )

    connection_acceptances = tuple(
        _evaluate_connection(workflow, connection, record)
        for connection, record in zip(workflow.connections, workflow.connection_records)
    )

    if not extraction_sufficient:
        status, reason = ACCEPTANCE_STATUS_NOT_ACCEPTED, (
            "the extraction intake recorded no processed pages; a production job with no "
            "extraction evidence cannot be accepted."
        )
    elif not connection_acceptances:
        status, reason = ACCEPTANCE_STATUS_NOT_ACCEPTED, (
            "the intake produced no connection candidates; there is no fabrication output "
            "to accept."
        )
    elif all(acceptance.accepted for acceptance in connection_acceptances):
        status, reason = ACCEPTANCE_STATUS_ACCEPTED, (
            f"every connection of project {workflow.project_id!r} at revision "
            f"{workflow.revision} is accepted on its recorded stage evidence: "
            f"{len(connection_acceptances)} verified artifact(s), no unresolved exceptions, "
            "and the complete audit chain from drawing evidence through every human "
            "decision to verified output is intact."
        )
    else:
        first_failure = next(
            acceptance for acceptance in connection_acceptances if not acceptance.accepted
        )
        status, reason = ACCEPTANCE_STATUS_NOT_ACCEPTED, (
            f"connection {first_failure.package_id} is not accepted: {first_failure.reason}"
        )

    accepted_ids = tuple(
        acceptance.package_id for acceptance in connection_acceptances if acceptance.accepted
    )
    unresolved_ids = tuple(
        acceptance.package_id for acceptance in connection_acceptances
        if acceptance.decision is None
    )
    failed_ids = tuple(
        acceptance.package_id for acceptance in connection_acceptances
        if acceptance.decision is not None and not acceptance.accepted
    )
    verified_artifacts = tuple(
        acceptance.verified_artifact for acceptance in connection_acceptances
        if acceptance.accepted and acceptance.verified_artifact is not None
    )
    decision_count = sum(
        len(acceptance.trace.human_decisions) for acceptance in connection_acceptances
    )
    applied_count = sum(
        sum(1 for decision in acceptance.trace.human_decisions if decision.applied)
        for acceptance in connection_acceptances
    )
    return ProductionJobAcceptance(
        status=status,
        reason=reason,
        project_id=workflow.project_id,
        revision=workflow.revision,
        caller_revision=expected_revision,
        intake_scope=intake_scope,
        extraction_sufficient=extraction_sufficient,
        connections=connection_acceptances,
        accepted_package_ids=accepted_ids,
        unresolved_package_ids=unresolved_ids,
        failed_package_ids=failed_ids,
        verified_artifacts=verified_artifacts,
        summary=AcceptanceSummary(
            connections_total=len(connection_acceptances),
            connections_accepted=len(accepted_ids),
            connections_not_accepted=len(connection_acceptances) - len(accepted_ids),
            connections_unresolved=len(unresolved_ids),
            verified_artifacts=verified_artifacts,
            human_decisions_total=decision_count,
            human_decisions_applied=applied_count,
            human_decisions_refused=decision_count - applied_count,
        ),
    )
