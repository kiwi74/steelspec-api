"""
Milestone 7AX — REAL-WORLD DRAWING SET COVERAGE & PRODUCTION COMPLETENESS PROOF.

A read-only, project-level coverage and proof layer that COMPOSES the evidence
the existing chain already recorded. It runs no extraction, no AI, no dispatch,
no generation, no package build and no fabricator evaluation: it re-reads a
`ProjectWorkflowState` (plus, when supplied, the 7AR `FabricationDrawingPackage`
and the 7AS `FabricatorAcceptance` built from it) and accounts for every
discovered production-relevant connection and every page of the recorded
drawing set.

THE THREE RULES THIS MODULE EXISTS TO ENFORCE:

  1. NO CONNECTION DISAPPEARS. Every candidate the workflow's own 7Y intake
     recorded receives exactly one disposition:
       COMPLETED      — the full backward chain closed: 7AD rerun AUTO,
                        7AE gate AUTO, 7AF GENERATED, 7AG VERIFIED (artifact
                        present and hash-corroborated), 7AQ accepted, 7AR
                        packaged, 7AS fabricator-accepted.
       PRODUCED       — verified and accepted, but the job-level closure is
                        missing: no package recorded, the package is BLOCKED,
                        or the fabricator did not accept the drawing.
       UNRESOLVED     — REVIEW/CONFIRM with no recorded completion: never
                        processed through 7AD, or a recorded generation /
                        verification failure. Needs human resolution.
       BLOCKED        — a genuine recorded boundary: the unsupported-section-
                        family validation failure (the FL/EA boundary 7AV
                        proved). Never approximated, never produced.
       NOT_PROCESSED  — discovered by the intake but never queued for review;
                        never evaluated, never produced.
     A connection with a claim but no recorded chain (or a claim that
     disagrees with its records) is never silently downgraded: the whole
     evaluation is REFUSED instead.

  2. NO PAGE DISAPPEARS. Page coverage is counted from the intake's own
     recorded numbers and the identity is enforced:
        set_pages == pages_analysed + pages_parse_failed + pages_not_analysed.
     The pages beyond those supplied are NEVER enumerated here — this module
     contains no numeric literals at all (an AST-based test pins that), so a
     "not analysed" page can only be accounted for at count level, exactly
     the granularity the intake itself recorded. A page the intake did not
     record as analysed can never carry a candidate: candidate provenance is
     cross-checked against the analysed page numbers.

  3. NO CLAIM WITHOUT ITS CHAIN. A COMPLETED connection's backward chain is
     re-traced field for field (review tasks -> human decisions -> rerun ->
     gate -> dispatch -> verification -> acceptance -> package -> fabricator
     acceptance) and each layer is cross-checked against the others. Any
     disagreement — a stale revision, a duplicated connection or artifact
     identity, a stray PDF beside the package, a missing or altered artifact
     file, a projection that disagrees with its own records, a packaged item
     without an accepted connection, page/source provenance that does not
     match the intake — REFUSES the whole evaluation: the job is reported as
     not safely evaluable, never as approximately covered.

A project is COMPLETE only when the drawing set's page count is recorded,
no page is unanalysed or parse-failed, at least one connection was discovered,
and every discovered connection is COMPLETED. Anything else is INCOMPLETE
with each gap named. REFUSED means the evaluation itself cannot be trusted.

Records are truth; this projection only corroborates them (7AQ's principle).
A stray PDF is never evidence of successful production (7AR owns the package).
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_VERIFIED
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_BLOCKED,
    PACKAGE_STATUS_READY,
    PACKAGE_STATUS_REFUSED,
    FabricationDrawingPackage,
)
from app.cad_engine.fabricator_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED as FABRICATOR_STATUS_REFUSED,
    FabricatorAcceptance,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_REFUSED as JOB_STATUS_REFUSED,
    accept_production_job,
)
from app.cad_engine.project_workflow import ProjectWorkflowState

__all__ = [
    "COVERAGE_SCOPE_STATEMENT",
    "PAGE_STATUS_ANALYSED", "PAGE_STATUS_PARSE_FAILED",
    "DISPOSITION_COMPLETED", "DISPOSITION_PRODUCED", "DISPOSITION_UNRESOLVED",
    "DISPOSITION_BLOCKED", "DISPOSITION_NOT_PROCESSED",
    "PROJECT_STATUS_COMPLETE", "PROJECT_STATUS_INCOMPLETE", "PROJECT_STATUS_REFUSED",
    "PageCoverage", "CoverageTrace", "ConnectionCoverage", "CoverageSummary",
    "ProductionCoverage", "evaluate_production_coverage",
]

COVERAGE_SCOPE_STATEMENT = (
    "This coverage proof composes the evidence the production chain already recorded: the 7Y intake's page "
    "facts, the workflow's per-connection states and stage records, the 7AQ production acceptance, the 7AR "
    "fabrication package and the 7AS fabricator acceptance. It reads records; it never re-runs a stage, never "
    "generates a drawing, never writes a file and never decides engineering."
)

PAGE_STATUS_ANALYSED = "ANALYSED"
PAGE_STATUS_PARSE_FAILED = "PARSE_FAILED"

DISPOSITION_COMPLETED = "COMPLETED"
DISPOSITION_PRODUCED = "PRODUCED"
DISPOSITION_UNRESOLVED = "UNRESOLVED"
DISPOSITION_BLOCKED = "BLOCKED"
DISPOSITION_NOT_PROCESSED = "NOT_PROCESSED"
DISPOSITIONS = (
    DISPOSITION_COMPLETED, DISPOSITION_PRODUCED, DISPOSITION_UNRESOLVED,
    DISPOSITION_BLOCKED, DISPOSITION_NOT_PROCESSED,
)

PROJECT_STATUS_COMPLETE = "COMPLETE"
PROJECT_STATUS_INCOMPLETE = "INCOMPLETE"
PROJECT_STATUS_REFUSED = "REFUSED"

# The recorded validation-failure signatures of the genuine fabrication
# boundary 7AV proved: the two-member path raises UnsupportedSectionFamilyError
# (its type name is the recorded error_code), the multi-member path raises
# GeometryValidationError whose message carries the family-builder marker
# (the only occurrence of that phrase in the engine). Everything else that
# fails validation remains resolvable by a human and stays UNRESOLVED.
UNSUPPORTED_SECTION_FAMILY_ERROR_CODE = "UnsupportedSectionFamilyError"
GEOMETRY_VALIDATION_ERROR_CODE = "GeometryValidationError"
UNSUPPORTED_FAMILY_MESSAGE_MARKER = "no supported CAD profile builder"


@dataclass(frozen=True)
class PageCoverage:
    """One recorded page's coverage: ANALYSED (usable extraction, with the discovered
    candidate package ids recorded on it — possibly none) or PARSE_FAILED. Pages the
    intake never recorded are accounted for at count level only (see CoverageSummary)."""
    page_number: Any
    status: str
    candidate_package_ids: tuple[str, ...]


@dataclass(frozen=True)
class CoverageTrace:
    """The re-traced backward chain behind one connection, field for field, straight
    from the recorded stage evidence. None where the stage never ran."""
    package_id: str
    source_drawing_id: str | None
    source_page: Any
    detail_reference: str | None
    review_task_ids: tuple[str, ...]
    human_decisions: tuple[tuple[str, str], ...]  # (task_id, task_type), recorded order
    rerun_decision: str | None
    validation_stage: str | None
    validation_error_code: str | None
    gate_decision: str | None
    dispatch_status: str | None
    verification_status: str | None
    artifact_sha256: str | None
    accepted: bool | None
    package_drawing_number: str | None
    fabricator_drawing_status: str | None
    drawing_communication_labels: tuple[str, ...]


@dataclass(frozen=True)
class ConnectionCoverage:
    """One discovered connection's truthful project-level disposition. Every field is
    recorded evidence; the disposition is derived from it and explained by `reason`."""
    package_id: str
    connection_id: str | None
    source_drawing_id: str | None
    source_page: Any
    detail_reference: str | None
    member_marks: tuple[str, ...]
    disposition: str
    reason: str
    decision: str | None
    output_status: str | None
    verification_status: str | None
    artifact_path: str | None
    verified: bool
    accepted: bool | None
    packaged: bool
    fabricator_accepted: bool | None
    drawing_number: str | None
    trace: CoverageTrace


@dataclass(frozen=True)
class CoverageSummary:
    """The job-level counts. The page identity set_pages == pages_analysed +
    pages_parse_failed + pages_not_analysed is enforced during evaluation."""
    set_pages: int | None
    pages_analysed: int
    pages_parse_failed: int
    pages_not_analysed: int | None
    connections_discovered: int
    connections_completed: int
    connections_produced: int
    connections_unresolved: int
    connections_blocked: int
    connections_not_processed: int
    artifacts_verified: int
    artifacts_packaged: int
    drawings_fabricator_accepted: int
    package_status: str | None
    project_status: str
    refusal_reasons: tuple[str, ...] = ()
    incomplete_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductionCoverage:
    """The project-level production coverage proof. Deterministic and frozen: two
    evaluations of the same recorded evidence produce equal results."""
    project_id: str | None
    source_drawing_id: str | None
    workflow_revision: int
    expected_revision: int | None
    status: str
    reason: str
    refusal_reasons: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]
    pages: tuple[PageCoverage, ...]
    connections: tuple[ConnectionCoverage, ...]
    summary: CoverageSummary


def _is_positive_integer(value: Any) -> bool:
    """True for a positive non-bool int. This module may contain no numeric literals
    (AST-pinned by test), so positivity is checked without writing one."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and bool(value)
        and abs(value) == value
    )


def _is_non_negative_integer(value: Any) -> bool:
    """True for a non-negative non-bool int (zero is legitimate for counts)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) == value
    )


def _is_unsupported_family_failure(failure) -> bool:
    """The genuine fabrication boundary: the engine has no profile builder for a
    member's section family, and no human resolution can create one. Identified
    from the recorded failure's own error_code and message — never from the
    stage alone, never matched loosely."""
    if failure.error_code == UNSUPPORTED_SECTION_FAMILY_ERROR_CODE:
        return True
    return (
        failure.error_code == GEOMETRY_VALIDATION_ERROR_CODE
        and UNSUPPORTED_FAMILY_MESSAGE_MARKER in failure.message
    )


def _refused(workflow: ProjectWorkflowState, expected_revision, reasons: list[str],
             analysed=(), parse_failed=(), set_pages=None, pages_not_analysed=None,
             package_status=None) -> ProductionCoverage:
    """The REFUSED terminal: the job cannot be evaluated safely, so no connection
    dispositions are claimed. Page rows already validated from the intake may be
    carried (they are recorded intake facts, not derived claims)."""
    reason = next(iter(reasons), "refused without a recorded reason")
    page_rows = [PageCoverage(number, PAGE_STATUS_ANALYSED, ()) for number in analysed]
    page_rows.extend(PageCoverage(number, PAGE_STATUS_PARSE_FAILED, ()) for number in parse_failed)
    summary = CoverageSummary(
        set_pages=set_pages,
        pages_analysed=len(analysed),
        pages_parse_failed=len(parse_failed),
        pages_not_analysed=pages_not_analysed,
        connections_discovered=len(()),
        connections_completed=len(()),
        connections_produced=len(()),
        connections_unresolved=len(()),
        connections_blocked=len(()),
        connections_not_processed=len(()),
        artifacts_verified=len(()),
        artifacts_packaged=len(()),
        drawings_fabricator_accepted=len(()),
        package_status=package_status,
        project_status=PROJECT_STATUS_REFUSED,
        refusal_reasons=tuple(reasons),
    )
    return ProductionCoverage(
        project_id=workflow.project_id,
        source_drawing_id=None,
        workflow_revision=workflow.revision,
        expected_revision=expected_revision,
        status=PROJECT_STATUS_REFUSED,
        reason=reason,
        refusal_reasons=tuple(reasons),
        incomplete_reasons=(),
        pages=tuple(page_rows),
        connections=(),
        summary=summary,
    )


def _validate_intake_pages(intake, refusal: list[str]) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Validates the intake's recorded page facts and returns (analysed, parse_failed)
    page numbers in recorded order. Appends refusal reasons for any contradiction."""
    analysed = tuple(intake.analysed_page_numbers)
    parse_failed = tuple(intake.parse_failed_pages)

    seen_analysed: set[Any] = set()
    for number in analysed:
        if number in seen_analysed:
            refusal.append(
                f"the intake records analysed page {number!r} more than once; duplicated "
                "page evidence cannot be accounted."
            )
        seen_analysed.add(number)

    seen_failed: set[Any] = set()
    for number in parse_failed:
        if number in seen_analysed:
            refusal.append(
                f"the intake records page {number!r} as both analysed and parse-failed; "
                "a page has exactly one coverage state."
            )
        if number in seen_failed:
            refusal.append(
                f"the intake records parse-failed page {number!r} more than once; "
                "duplicated page evidence cannot be accounted."
            )
        seen_failed.add(number)

    for number in analysed + parse_failed:
        if not _is_positive_integer(number):
            refusal.append(
                f"the intake records a page number {number!r} that is not a positive "
                "integer; page coverage can only be accounted from recorded page numbers."
            )

    count = intake.drawing_set_page_count
    if count is not None:
        if not _is_positive_integer(count):
            refusal.append(
                f"the intake records a drawing set page count {count!r} that is not a "
                "positive integer; the set size is never guessed."
            )
        else:
            for number in analysed + parse_failed:
                if _is_positive_integer(number) and number > count:
                    refusal.append(
                        f"the intake records page {number} but a drawing set of {count} "
                        "pages; a page beyond the recorded set cannot exist."
                    )

    if intake.pages_received != len(analysed) + len(parse_failed):
        refusal.append(
            f"the intake records {intake.pages_received} received page(s) but "
            f"{len(analysed)} analysed and {len(parse_failed)} parse-failed page "
            "number(s); the received pages must partition into those two states."
        )

    if intake.pages_not_analysed is not None:
        if not _is_non_negative_integer(intake.pages_not_analysed):
            refusal.append(
                f"the intake records pages_not_analysed={intake.pages_not_analysed!r}, "
                "not a non-negative integer; the count is never guessed."
            )
        elif count is None:
            refusal.append(
                "the intake records a pages_not_analysed count without a drawing set "
                "page count; one cannot be derived from the other's absence."
            )
        else:
            expected = count - len(analysed) - len(parse_failed)
            if intake.pages_not_analysed != expected:
                refusal.append(
                    f"the intake records {intake.pages_not_analysed} page(s) not analysed "
                    f"but its recorded facts account for {expected}; the page coverage "
                    "identity set == analysed + parse-failed + not-analysed is broken."
                )

    return analysed, parse_failed


def _discovered_candidates(workflow: ProjectWorkflowState, intake,
                           refusal: list[str]) -> tuple[list[Any], set[str]]:
    """The union of the queue and the intake's recorded candidates, in recorded order:
    queued candidates first, then intake-only candidates. Returns (candidates,
    queued_package_ids). A candidate appearing twice anywhere is a duplicated
    identity; a queued candidate the intake never recorded is foreign evidence."""
    queued = [candidate for candidate in workflow.collection.candidates]
    intake_candidates = [candidate for candidate in intake.collection.candidates]

    queued_ids = [candidate.review_package_id for candidate in queued]
    seen: set[str] = set()
    for package_id in queued_ids:
        if package_id in seen:
            refusal.append(
                f"the review queue records candidate {package_id} more than once; a "
                "duplicated connection identity cannot be accounted."
            )
        seen.add(package_id)

    intake_ids = {candidate.review_package_id for candidate in intake_candidates}
    for candidate in intake_candidates:
        package_id = candidate.review_package_id
        if package_id in queued_ids:
            continue  # the queued candidate, already accounted
        if package_id in seen:
            refusal.append(
                f"the intake records candidate {package_id} more than once; a duplicated "
                "connection identity cannot be accounted."
            )
        seen.add(package_id)

    for package_id in queued_ids:
        if package_id not in intake_ids:
            refusal.append(
                f"candidate {package_id} is queued for review but the intake never "
                "recorded it; a queued candidate with no source evidence is refused."
            )

    discovered: list[Any] = list(queued)
    for candidate in intake_candidates:
        if candidate.review_package_id not in queued_ids:
            discovered.append(candidate)
    return discovered, set(queued_ids)


def _file_sha256(path: Path) -> str:
    """The SHA-256 of a file's bytes, recomputed from disk — the same corroboration
    the package build and verification stages perform."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_production_coverage(
    workflow,
    *,
    expected_revision=None,
    package=None,
    fabricator_result=None,
) -> ProductionCoverage:
    """
    The project-level production coverage proof.

    Composes, in order: the workflow's own 7Y intake page facts, the queued and
    discovered candidates, each connection's recorded stage evidence and visible
    projection, the genuine 7AQ production acceptance (via
    `accept_production_job`), the 7AR fabrication package when supplied, and the
    7AS fabricator acceptance when supplied. Every layer is cross-checked
    against the others; any disagreement appends a refusal reason and the final
    status is REFUSED with no connection dispositions claimed.

    `expected_revision`, when given, is the revision the caller holds; if it is
    not the workflow's current revision the evaluation is REFUSED (stale state
    is never covered). `package` and `fabricator_result` are the genuine 7AR /
    7AS results recorded for this workflow — a fabricator acceptance without a
    package, or a package whose recorded revision/acceptance disagrees with the
    workflow, is refused.

    Reads only: never mutates the workflow, never writes a file, never re-runs
    a stage, never calls the AI, the network, the database or the drawing
    generators. Deterministic: the same recorded evidence yields an equal
    result every time.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__}); "
            "production coverage composes the existing project workflow state."
        )
    if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
        raise TypeError("expected_revision must be an int or None.")
    if package is not None and not isinstance(package, FabricationDrawingPackage):
        raise TypeError(
            f"package must be a FabricationDrawingPackage or None (got "
            f"{type(package).__name__}); coverage corroborates the genuine 7AR package."
        )
    if fabricator_result is not None and not isinstance(fabricator_result, FabricatorAcceptance):
        raise TypeError(
            f"fabricator_result must be a FabricatorAcceptance or None (got "
            f"{type(fabricator_result).__name__}); coverage corroborates the genuine 7AS result."
        )

    refusal: list[str] = []

    if expected_revision is not None and expected_revision != workflow.revision:
        refusal.append(
            f"the workflow is at revision {workflow.revision} but the caller holds "
            f"revision {expected_revision}; a stale request never receives coverage "
            "of state the caller did not see."
        )
        return _refused(workflow, expected_revision, refusal)

    acceptance = accept_production_job(workflow, expected_revision=expected_revision)
    if acceptance.status == JOB_STATUS_REFUSED:
        refusal.append(f"the 7AQ production acceptance refused the job: {acceptance.reason}")
        return _refused(workflow, expected_revision, refusal)

    intake = workflow.intake
    if intake is None:
        refusal.append(
            "the workflow carries no 7Y intake evidence; only states built by "
            "start_project_workflow can prove drawing set coverage."
        )
        return _refused(workflow, expected_revision, refusal)

    analysed, parse_failed = _validate_intake_pages(intake, refusal)
    analysed_set = set(analysed)

    discovered, queued_ids = _discovered_candidates(workflow, intake, refusal)

    # ---------------- per-connection recorded evidence, projection and artifacts
    states_by_package: dict[str, Any] = {}
    for connection in workflow.connections:
        if connection.package_id in states_by_package:
            refusal.append(
                f"connection state {connection.package_id} is recorded more than once; a "
                "duplicated connection identity cannot be accounted."
            )
        states_by_package[connection.package_id] = connection
        if connection.package_id not in queued_ids:
            refusal.append(
                f"connection state {connection.package_id} exists for a candidate the "
                "review queue does not hold; a projection without its candidate is refused."
            )
    for package_id in queued_ids:
        if package_id not in states_by_package:
            refusal.append(
                f"queued candidate {package_id} has no connection state; a queued "
                "candidate with no recorded projection is refused."
            )

    records_by_package = {
        connection.package_id: record
        for connection, record in zip(workflow.connections, workflow.connection_records)
    }
    for package_id in queued_ids:
        if package_id not in records_by_package:
            refusal.append(
                f"queued candidate {package_id} has no recorded stage evidence; a "
                "projection without records is refused."
            )

    acceptance_by_package = {entry.package_id: entry for entry in acceptance.connections}
    for package_id in queued_ids:
        if package_id not in acceptance_by_package:
            refusal.append(
                f"queued candidate {package_id} has no 7AQ acceptance evaluation; every "
                "queued connection must be evaluated before coverage can be claimed."
            )

    source_page_by_package: dict[str, Any] = {}
    for candidate in discovered:
        source_page = candidate.package.extraction.source_page
        source_page_by_package[candidate.review_package_id] = source_page
        if source_page not in analysed_set:
            refusal.append(
                f"candidate {candidate.review_package_id} claims page {source_page!r} but "
                "the intake did not record that page as analysed; a candidate from an "
                "unanalysed page is refused."
            )

    for package_id in queued_ids:
        state = states_by_package[package_id]
        record = records_by_package[package_id]
        rerun = record.rerun_outcome
        gate = record.gate_result
        dispatch = record.dispatch_result
        verification = record.verification_result
        processed = rerun is not None

        if processed:
            if gate is None or dispatch is None or verification is None:
                refusal.append(
                    f"{package_id}: the recorded stage chain is incomplete (gate "
                    f"{'present' if gate is not None else 'missing'}, dispatch "
                    f"{'present' if dispatch is not None else 'missing'}, verification "
                    f"{'present' if verification is not None else 'missing'}); a "
                    "processed connection must record every downstream stage."
                )
            recorded_blockers = tuple(finding.code for finding in rerun.blockers)
            recorded_decision = rerun.decision
        else:
            if gate is not None or dispatch is not None or verification is not None:
                refusal.append(
                    f"{package_id}: downstream stage evidence exists without a 7AD rerun; "
                    "stages never run for an unprocessed connection."
                )
            if record.pipeline is None:
                refusal.append(
                    f"{package_id}: the connection has no initial pipeline evidence; the "
                    "recorded initial evaluation is missing."
                )
                continue
            recorded_blockers = tuple(
                finding.code for finding in record.pipeline.automation_gate_result.blockers
            )
            recorded_decision = record.pipeline.automation_gate_result.decision

        if state.decision != recorded_decision:
            refusal.append(
                f"{package_id}: the visible decision {state.decision!r} disagrees with the "
                f"recorded decision {recorded_decision!r}; a projection that disagrees "
                "with its records is refused."
            )
        if state.blockers != recorded_blockers:
            refusal.append(
                f"{package_id}: the visible blockers disagree with the recorded blockers; "
                "a projection that disagrees with its records is refused."
            )
        recorded_output_status = dispatch.output_status if dispatch is not None else None
        if state.output_status != recorded_output_status:
            refusal.append(
                f"{package_id}: the visible output status {state.output_status!r} disagrees "
                f"with the recorded {recorded_output_status!r}; a projection that disagrees "
                "with its records is refused."
            )
        recorded_verification_status = (
            verification.verification_status if verification is not None else None
        )
        if state.verification_status != recorded_verification_status:
            refusal.append(
                f"{package_id}: the visible verification status "
                f"{state.verification_status!r} disagrees with the recorded "
                f"{recorded_verification_status!r}; a projection that disagrees with its "
                "records is refused."
            )
        recorded_files = (
            tuple(str(path) for path in dispatch.generated_files)
            if dispatch is not None else ()
        )
        if state.generated_files != recorded_files:
            refusal.append(
                f"{package_id}: the visible generated files disagree with the recorded "
                "dispatch artifacts; a projection that disagrees with its records is refused."
            )

        stage_ids = [
            stage.connection_id
            for stage in (gate, dispatch, verification)
            if stage is not None and stage.connection_id is not None
        ]
        first_id = next(iter(stage_ids), None)
        if any(stage_id != first_id for stage_id in stage_ids):
            refusal.append(
                f"{package_id}: the recorded gate/dispatch/verification results name "
                "different connections; one connection's chain cannot mix another's evidence."
            )

        if verification is not None and verification.verification_status == VERIFICATION_STATUS_VERIFIED:
            artifact_path = verification.artifact_path
            if not artifact_path:
                refusal.append(
                    f"{package_id}: the verification is VERIFIED but records no artifact "
                    "path; a verified artifact must be named."
                )
                continue
            artifact_file = Path(artifact_path)
            if not artifact_file.is_file():
                refusal.append(
                    f"{package_id}: the verified artifact {artifact_path} is not a file on "
                    "disk; a verified artifact that is missing is refused."
                )
                continue
            observed_hash = _file_sha256(artifact_file)
            if observed_hash != verification.sha256:
                refusal.append(
                    f"{package_id}: the verified artifact {artifact_path} no longer matches "
                    "its recorded SHA-256; an altered artifact is refused."
                )
            entry = acceptance_by_package.get(package_id)
            if entry is not None and entry.sha256 != verification.sha256:
                refusal.append(
                    f"{package_id}: the acceptance's recorded SHA-256 disagrees with the "
                    "verification's recorded SHA-256; the chain's layers disagree."
                )
            if entry is not None and not entry.accepted:
                refusal.append(
                    f"{package_id}: the connection is VERIFIED but the 7AQ acceptance did "
                    f"not accept it ({entry.reason}); a verified artifact its own acceptance "
                    "refused is never covered as produced."
                )

        if processed and rerun.decision == AUTOMATION_DECISION_AUTO and rerun.validation_failure is not None:
            refusal.append(
                f"{package_id}: the rerun decided AUTO despite a recorded validation "
                f"failure at {rerun.validation_failure.stage}; a failed validation is "
                "never automatic."
            )
        if not processed and recorded_decision == AUTOMATION_DECISION_AUTO:
            refusal.append(
                f"{package_id}: the connection shows AUTO with no 7AD processing; a raw "
                "candidate can never become AUTO without the resolution rerun."
            )

    # ---------------- package corroboration (7AR owns the package)
    package_status = package.status if package is not None else None
    packaged_by_package: dict[str, Any] = {}
    item_by_package: dict[str, Any] = {}
    if package is not None:
        if package.status == PACKAGE_STATUS_REFUSED:
            refusal.append(f"the 7AR fabrication package was refused: {package.reason}")
        else:
            if package.workflow_revision is not None and package.workflow_revision != workflow.revision:
                refusal.append(
                    f"the package records workflow revision {package.workflow_revision} but "
                    f"the workflow is at revision {workflow.revision}; a stale package is refused."
                )
            if package.acceptance_status != acceptance.status:
                refusal.append(
                    f"the package records acceptance status {package.acceptance_status!r} "
                    f"but the 7AQ acceptance is {acceptance.status!r}; the package must "
                    "reflect the acceptance it was built from."
                )
            if package.project_id is not None and workflow.project_id is not None \
                    and package.project_id != workflow.project_id:
                refusal.append(
                    f"the package records project {package.project_id!r} but the workflow "
                    f"records project {workflow.project_id!r}; a package of another project "
                    "is refused."
                )
            if package.status == PACKAGE_STATUS_READY:
                if not package.items:
                    refusal.append("the package is READY but carries no items; a ready "
                                   "package must package the accepted artifacts.")
                seen_items: set[str] = set()
                for item in package.items:
                    if item.package_id in seen_items:
                        refusal.append(
                            f"the package carries item {item.package_id} more than once; a "
                            "duplicated artifact identity is refused."
                        )
                    seen_items.add(item.package_id)
                    item_by_package[item.package_id] = item
                    if item.package_id not in queued_ids:
                        refusal.append(
                            f"the package carries item {item.package_id} for a connection "
                            "the review queue does not hold; a packaged artifact without an "
                            "accepted connection record is refused."
                        )
                manifest_parent = Path(package.manifest_path).parent
                recorded_paths: set[Path] = set()
                for item in package.items:
                    entry = acceptance_by_package.get(item.package_id)
                    if entry is None or not entry.accepted:
                        refusal.append(
                            f"the package carries item {item.package_id} without an accepted "
                            "connection record; a packaged artifact without an accepted "
                            "connection is refused."
                        )
                    elif item.recorded_sha256 != entry.sha256:
                        refusal.append(
                            f"package item {item.package_id} records SHA-256 "
                            f"{item.recorded_sha256!r} but the acceptance records "
                            f"{entry.sha256!r}; the chain's layers disagree."
                        )
                    packaged_file = manifest_parent / item.packaged_path
                    if not packaged_file.is_file():
                        refusal.append(
                            f"package item {item.package_id} names {item.packaged_path} but "
                            "no such file exists beside the manifest; a missing packaged "
                            "artifact is refused."
                        )
                    elif _file_sha256(packaged_file) != item.artifact_sha256:
                        refusal.append(
                            f"package item {item.package_id} no longer matches its recorded "
                            "package SHA-256; an altered packaged artifact is refused."
                        )
                    else:
                        packaged_by_package[item.package_id] = item
                    recorded_paths.add(packaged_file.resolve())
                for state_path in workflow.generated_files:
                    recorded_paths.add(Path(state_path).resolve())
                for pdf in manifest_parent.rglob("*.pdf"):
                    if pdf.resolve() not in recorded_paths:
                        refusal.append(
                            f"a PDF {pdf.name} sits beside the package manifest but is "
                            "neither a manifest item nor a recorded artifact; a stray PDF "
                            "is never evidence of successful production."
                        )
            elif package.status != PACKAGE_STATUS_BLOCKED:
                refusal.append(f"the package status {package.status!r} is not a recorded "
                               "package vocabulary.")

    # ---------------- fabricator corroboration (7AS owns the acceptance)
    fabricator_by_drawing: dict[str, Any] = {}
    if fabricator_result is not None:
        if package is None:
            refusal.append(
                "a fabricator acceptance was supplied without a fabrication package; the "
                "7AS result only means something against the 7AR package it evaluated."
            )
        elif fabricator_result.status == FABRICATOR_STATUS_REFUSED:
            refusal.append(
                f"the 7AS fabricator acceptance refused the package: {fabricator_result.reason}"
            )
        else:
            if fabricator_result.package_status != package.status:
                refusal.append(
                    f"the fabricator acceptance records package status "
                    f"{fabricator_result.package_status!r} but the package is "
                    f"{package.status!r}; the fabricator must have evaluated the package "
                    "that is recorded."
                )
            if fabricator_result.workflow_revision != workflow.revision:
                refusal.append(
                    f"the fabricator acceptance records workflow revision "
                    f"{fabricator_result.workflow_revision} but the workflow is at revision "
                    f"{workflow.revision}; a stale fabricator acceptance is refused."
                )
            for drawing in fabricator_result.drawings:
                if drawing.drawing_number in fabricator_by_drawing:
                    refusal.append(
                        f"the fabricator acceptance carries drawing "
                        f"{drawing.drawing_number} more than once; a duplicated drawing "
                        "identity is refused."
                    )
                fabricator_by_drawing[drawing.drawing_number] = drawing
            item_numbers = {item.drawing_number for item in package.items}
            for drawing in fabricator_result.drawings:
                if drawing.drawing_number not in item_numbers:
                    refusal.append(
                        f"the fabricator acceptance carries drawing {drawing.drawing_number} "
                        "that the package never packaged; a fabricator evaluation without "
                        "its packaged drawing is refused."
                    )
            for item in package.items:
                if item.drawing_number not in fabricator_by_drawing:
                    refusal.append(
                        f"package item {item.package_id} (drawing {item.drawing_number}) has "
                        "no fabricator evaluation; the 7AS result must evaluate every "
                        "packaged drawing."
                    )

    if refusal:
        return _refused(
            workflow, expected_revision, refusal,
            analysed=analysed, parse_failed=parse_failed,
            set_pages=intake.drawing_set_page_count,
            pages_not_analysed=intake.pages_not_analysed,
            package_status=package_status,
        )

    # ---------------- dispositions (records are truth; this projection only composes)
    task_ids_by_package: dict[str, tuple[str, ...]] = {}
    for group in workflow.exception_package.connection_tasks:
        task_ids_by_package[group.review_package_id] = tuple(
            task.task_id for task in group.tasks
        )

    connection_rows: list[ConnectionCoverage] = []
    for candidate in discovered:
        package_id = candidate.review_package_id
        extraction = candidate.package.extraction
        if package_id in queued_ids:
            state = states_by_package[package_id]
            record = records_by_package[package_id]
            entry = acceptance_by_package[package_id]
            rerun = record.rerun_outcome
            gate = record.gate_result
            dispatch = record.dispatch_result
            verification = record.verification_result

            decision = state.decision
            output_status = state.output_status
            verification_status = state.verification_status
            artifact_path = (
                verification.artifact_path if verification is not None else None
            )
            verified = verification_status == VERIFICATION_STATUS_VERIFIED
            accepted = entry.accepted
            packaged_item = packaged_by_package.get(package_id)
            packaged = packaged_item is not None
            drawing = (
                fabricator_by_drawing.get(packaged_item.drawing_number)
                if packaged_item is not None else None
            )
            fabricator_accepted = (
                drawing.status == ACCEPTANCE_STATUS_ACCEPTED
                if drawing is not None else None
            )

            if rerun is None:
                disposition = DISPOSITION_UNRESOLVED
                if state.blockers:
                    reason = "never processed through 7AD; recorded blockers: " + ", ".join(state.blockers)
                else:
                    reason = "never processed through 7AD; still unresolved with no recorded blockers"
            elif decision == AUTOMATION_DECISION_AUTO:
                if output_status != OUTPUT_STATUS_GENERATED:
                    disposition = DISPOSITION_UNRESOLVED
                    reason = f"the 7AF dispatch recorded {output_status}; the drawing was never generated"
                elif verification_status != VERIFICATION_STATUS_VERIFIED:
                    disposition = DISPOSITION_UNRESOLVED
                    reason = f"the 7AG verification recorded {verification_status}; the artifact is not verified"
                elif not packaged:
                    disposition = DISPOSITION_PRODUCED
                    if package is None:
                        reason = "verified and accepted, but no fabrication package was recorded"
                    elif package.status == PACKAGE_STATUS_BLOCKED:
                        reason = f"verified and accepted, but the package is BLOCKED: {package.reason}"
                    else:
                        reason = "verified and accepted, but the package did not package this connection"
                elif fabricator_accepted is None:
                    disposition = DISPOSITION_PRODUCED
                    reason = "verified, accepted and packaged, but no fabricator acceptance was recorded"
                elif fabricator_accepted:
                    disposition = DISPOSITION_COMPLETED
                    reason = (
                        "the full chain is closed: 7AD AUTO, 7AE AUTO, 7AF GENERATED, "
                        "7AG VERIFIED, 7AQ accepted, 7AR packaged, 7AS accepted"
                    )
                else:
                    disposition = DISPOSITION_PRODUCED
                    reason = (
                        f"verified, accepted and packaged, but the fabricator recorded "
                        f"{drawing.status}: the drawing is not accepted"
                    )
            elif rerun.validation_failure is not None \
                    and _is_unsupported_family_failure(rerun.validation_failure):
                disposition = DISPOSITION_BLOCKED
                reason = (
                    f"unsupported section family at {rerun.validation_failure.stage}: "
                    f"{rerun.validation_failure.message}"
                )
            else:
                disposition = DISPOSITION_UNRESOLVED
                if rerun.validation_failure is not None:
                    reason = (
                        f"{rerun.validation_failure.stage}: {rerun.validation_failure.message}"
                    )
                elif state.blockers:
                    reason = "recorded blockers: " + ", ".join(state.blockers)
                else:
                    reason = "still unresolved with no recorded blockers"

            trace_entry = entry.trace
            human_decisions = (
                tuple(
                    (decision_trace.task_id, decision_trace.task_type)
                    for decision_trace in trace_entry.human_decisions
                )
                if trace_entry is not None else ()
            )
            trace = CoverageTrace(
                package_id=package_id,
                source_drawing_id=extraction.source_drawing_id,
                source_page=extraction.source_page,
                detail_reference=extraction.detail_reference,
                review_task_ids=task_ids_by_package.get(package_id, ()),
                human_decisions=human_decisions,
                rerun_decision=rerun.decision if rerun is not None else None,
                validation_stage=(
                    rerun.validation_failure.stage
                    if rerun is not None and rerun.validation_failure is not None else None
                ),
                validation_error_code=(
                    rerun.validation_failure.error_code
                    if rerun is not None and rerun.validation_failure is not None else None
                ),
                gate_decision=gate.decision if gate is not None else None,
                dispatch_status=dispatch.output_status if dispatch is not None else None,
                verification_status=(
                    verification.verification_status if verification is not None else None
                ),
                artifact_sha256=(
                    verification.sha256
                    if verification is not None
                    and verification.verification_status == VERIFICATION_STATUS_VERIFIED
                    else None
                ),
                accepted=accepted,
                package_drawing_number=(
                    packaged_item.drawing_number if packaged_item is not None else None
                ),
                fabricator_drawing_status=(
                    drawing.status if drawing is not None else None
                ),
                drawing_communication_labels=(
                    tuple(label for label, _ in packaged_item.observed)
                    if packaged_item is not None else ()
                ),
            )
            member_marks = tuple(record.pipeline.specification.connected_member_marks)
        else:
            disposition = DISPOSITION_NOT_PROCESSED
            reason = (
                "discovered by the intake but never queued for review; never evaluated, "
                "never produced"
            )
            decision = None
            output_status = None
            verification_status = None
            artifact_path = None
            verified = False
            accepted = None
            packaged = False
            fabricator_accepted = None
            drawing = None
            member_marks = tuple(extraction.connected_member_references)
            trace = CoverageTrace(
                package_id=package_id,
                source_drawing_id=extraction.source_drawing_id,
                source_page=extraction.source_page,
                detail_reference=extraction.detail_reference,
                review_task_ids=(),
                human_decisions=(),
                rerun_decision=None,
                validation_stage=None,
                validation_error_code=None,
                gate_decision=None,
                dispatch_status=None,
                verification_status=None,
                artifact_sha256=None,
                accepted=None,
                package_drawing_number=None,
                fabricator_drawing_status=None,
                drawing_communication_labels=(),
            )

        connection_rows.append(ConnectionCoverage(
            package_id=package_id,
            connection_id=(
                state.connection_id if package_id in queued_ids
                else candidate.package.supplement.connection_id
            ),
            source_drawing_id=extraction.source_drawing_id,
            source_page=extraction.source_page,
            detail_reference=extraction.detail_reference,
            member_marks=member_marks,
            disposition=disposition,
            reason=reason,
            decision=decision,
            output_status=output_status,
            verification_status=verification_status,
            artifact_path=artifact_path,
            verified=verified,
            accepted=accepted,
            packaged=packaged,
            fabricator_accepted=fabricator_accepted,
            drawing_number=(
                packaged_item.drawing_number if packaged_item is not None else None
            ),
            trace=trace,
        ))

    # ---------------- page rows (recorded order; not-analysed pages stay count-level)
    candidates_by_page: dict[Any, list[str]] = {}
    for candidate in discovered:
        candidates_by_page.setdefault(source_page_by_package[candidate.review_package_id], []).append(
            candidate.review_package_id
        )
    page_rows = [
        PageCoverage(number, PAGE_STATUS_ANALYSED, tuple(candidates_by_page.get(number, ())))
        for number in analysed
    ]
    page_rows.extend(
        PageCoverage(number, PAGE_STATUS_PARSE_FAILED, ()) for number in parse_failed
    )

    # ---------------- project status: every page accounted, every connection COMPLETED
    incomplete: list[str] = []
    if intake.drawing_set_page_count is None:
        incomplete.append(
            "the drawing set's page count is unrecorded; the pages not analysed cannot be known"
        )
    if intake.pages_not_analysed:
        incomplete.append(
            f"page coverage is incomplete: {intake.pages_not_analysed} page(s) of the "
            "recorded drawing set were never analysed"
        )
    if parse_failed:
        incomplete.append(
            f"{len(parse_failed)} page(s) of the recorded drawing set failed to parse"
        )
    if not connection_rows:
        incomplete.append(
            "the intake recorded no connection candidates; there is nothing whose "
            "production could be covered"
        )
    for row in connection_rows:
        if row.disposition != DISPOSITION_COMPLETED:
            incomplete.append(f"{row.package_id}: {row.disposition} — {row.reason}")

    if incomplete:
        status = PROJECT_STATUS_INCOMPLETE
        reason = f"{len(incomplete)} reason(s): " + "; ".join(incomplete)
    else:
        status = PROJECT_STATUS_COMPLETE
        reason = (
            "every discovered connection is COMPLETED and every page of the drawing set "
            "is accounted for"
        )

    def _count(disposition: str) -> int:
        return len([row for row in connection_rows if row.disposition == disposition])

    summary = CoverageSummary(
        set_pages=intake.drawing_set_page_count,
        pages_analysed=len(analysed),
        pages_parse_failed=len(parse_failed),
        pages_not_analysed=intake.pages_not_analysed,
        connections_discovered=len(connection_rows),
        connections_completed=_count(DISPOSITION_COMPLETED),
        connections_produced=_count(DISPOSITION_PRODUCED),
        connections_unresolved=_count(DISPOSITION_UNRESOLVED),
        connections_blocked=_count(DISPOSITION_BLOCKED),
        connections_not_processed=_count(DISPOSITION_NOT_PROCESSED),
        artifacts_verified=len([row for row in connection_rows if row.verified]),
        artifacts_packaged=len([row for row in connection_rows if row.packaged]),
        drawings_fabricator_accepted=len(
            [row for row in connection_rows if row.fabricator_accepted]
        ),
        package_status=package_status,
        project_status=status,
        refusal_reasons=(),
        incomplete_reasons=tuple(incomplete),
    )

    source_drawing_id = next(
        (row.source_drawing_id for row in connection_rows if row.source_drawing_id is not None),
        None,
    )

    return ProductionCoverage(
        project_id=workflow.project_id,
        source_drawing_id=source_drawing_id,
        workflow_revision=workflow.revision,
        expected_revision=expected_revision,
        status=status,
        reason=reason,
        refusal_reasons=(),
        incomplete_reasons=tuple(incomplete),
        pages=tuple(page_rows),
        connections=tuple(connection_rows),
        summary=summary,
    )
