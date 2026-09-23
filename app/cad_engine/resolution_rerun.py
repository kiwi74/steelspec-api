"""
Milestone 7AD — HUMAN RESOLUTION -> AUTOMATION RE-RUN: the read-only,
deterministic orchestration that closes the exception-resolution loop
by re-running the EXISTING automation architecture after the 7AC
human answers were recorded:

    7AC ExceptionResolutionPackage (tasks + recorded HumanResolutions)
            |
    rerun_project_after_resolutions()
            |   one connection at a time, in the collection's own submission order
            v
    build_resolved_supplement()                 (7AC -> the EXISTING 7W supplement)
            -> create_review_package()          (the existing 7W rebuild mechanism)
            -> evaluate_reviewed_connection_for_automation()   (7AA)
                   -> 7W/7V specification -> 7D/7J/7N adapters -> 7A/7I ->
                      7O assembly -> 7R validation -> 7Z automation gate
            |
    ConnectionRerunOutcome per candidate (before-state, applied/refused
    resolutions, the rebuilt package, 7AA's genuine outcome)
            |
    ResolutionRerunResult — counts recomputed from the genuine outcomes;
    no project decision, no score, no AUTO shortcut.

HARD RULES:

  - THE GATES DECIDE; 7AD NEVER DOES. The after-state decision is
    produced exclusively by 7AA (which builds the 7V specification,
    runs the genuine 7O assembly and 7R validation, and hands 7R's
    outcome to 7Z). 7AD calls only evaluate_reviewed_connection_for_
    automation() — it never calls evaluate_automation_gate(), never
    builds an assembly, never runs validation itself, and takes NO
    validation_passed parameter: there is no way for a caller to
    inject validation evidence, and no shortcut of any kind.
  - THE CORRECTION REPRESENTATION IS 7W's. Accepted resolutions are
    written onto the existing ConnectionReviewSupplement by 7AC's
    build_resolved_supplement() (its conflict semantics included),
    and the package is rebuilt by the existing create_review_package()
    — the original AI extraction is preserved untouched beside the
    human overlay. No parallel correction object exists here.
  - ANSWERS ARE CHECKED AGAINST THE TASK'S OWN VOCABULARY, NOTHING
    MORE. A resolution whose answer is outside its task's
    authoritative choices — member marks outside the task's
    allowed_choices when it has any, position or attachment surfaces
    outside the existing START/END vocabulary, a connection identity
    that is not a non-empty identifier string — is REFUSED: recorded
    with its reason, never applied, its task left unresolved.
    Everything else (a nominal size where a numeric diameter is
    required, unknown member marks when no authoritative context
    exists, malformed numerics, geometrically inconsistent
    attachments) flows through VERBATIM and is decided by the existing
    7V/7D/7J/7N/7O/7R/7Z chain. 7AD defines no new engineering rule:
    it checks only vocabularies 7AC already stated on the tasks.
  - ONE RESOLUTION PER TASK, ADDRESSED TO ITS OWN CONNECTION. The
    resolutions are read from the tasks that recorded them (each task
    is resolved at most once by 7AC); the project-level entry refuses
    any exception package whose groups do not align one-to-one with
    the collection's candidates — a resolution can never be applied
    to a different connection.
  - NOTHING IS MUTATED; EVERYTHING IS FROZEN PLAIN DATA. The exception
    package, the collection, the member context and every supplied
    mapping are never mutated. Results contain no CadQuery objects —
    per-candidate outcomes carry the gate findings, the validation
    evidence, the preserved failure and the rebuilt 7W package (frozen
    plain data), never geometry.
  - DETERMINISTIC, NO I/O, FAILURE ISOLATION WITHOUT ERROR
    SWALLOWING. No database, no network, no Claude, no API route, no
    web UI, no drawing generation, no CAD call outside the 7AA chain.
    7AA already preserves every documented connection-level failure as
    a REVIEW result with the failure recorded, so one candidate's
    failure never stops the others — WITHOUT this module catching
    anything: there is no try/except here, and unexpected programming
    errors propagate loudly. Two re-runs of the same inputs return
    equal results.

BOUNDARY: a re-run outcome reports what the existing automation
pipeline decided for the rebuilt package. It is not engineering
approval, structural adequacy, code compliance or fabrication
readiness, and RESOLVED still means only that the human supplied the
requested information (see 7AC's and 7Z's own scope statements).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_CODES,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
    AutomationFinding,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_STAGE_VALIDATION,
    AutomationValidationFailure,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.connection_review_package import (
    ConnectionReviewPackage,
    create_review_package,
)
from app.cad_engine.exception_resolution import (
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_POSITION_VALUE,
    CONNECTION_POSITION_CHOICES,
    ConnectionExceptionTasks,
    ExceptionResolutionPackage,
    ExceptionResolutionTask,
    HumanResolution,
    build_resolved_supplement,
)
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.project_automation import (
    NO_AUTO_STATEMENT,
    PROJECT_WARNING_NO_AUTO,
    PROJECT_WARNING_SCOPE_UNREPORTED,
    ProjectIntakeScope,
)
from app.cad_engine.project_connection_review import ProjectConnectionReviewCollection

__all__ = [
    "RESOLUTION_RERUN_SCOPE_STATEMENT",
    "RefusedResolution", "ConnectionRerunOutcome", "ResolutionRerunResult",
    "rerun_connection_after_resolutions", "rerun_project_after_resolutions",
]

RESOLUTION_RERUN_SCOPE_STATEMENT = (
    "This re-run reports only the automation outcomes the existing pipeline produced after the human "
    "resolutions supplied for the supplied candidates. AUTO is 7Z's automation-readiness decision on "
    "genuine 7R validation evidence — it is not engineering approval, structural adequacy, code "
    "compliance or fabrication readiness. A refused answer is never applied, and nothing here claims "
    "that the drawing set is complete or that every connection was found."
)


@dataclass(frozen=True)
class RefusedResolution:
    """
    One human answer the re-run refused to apply, and why. Refusal is
    against the task's OWN authoritative vocabulary only (its
    allowed_choices / the existing START-END surfaces) — never an
    engineering verdict; everything outside that vocabulary is left to
    the existing gates.
    """
    resolution: HumanResolution
    reason: str


@dataclass(frozen=True)
class ConnectionRerunOutcome:
    """
    One connection's closed loop: the before-state (the 7AC group's
    7AB findings, verbatim), the resolutions applied and refused, the
    rebuilt 7W package (the one correction representation, rebuilt by
    the existing mechanism), and the after-state — 7AA's genuine
    result (7Z findings verbatim, the real 7R evidence). Plain frozen
    data only, no geometry objects.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    before_decision: str
    before_blockers: tuple[AutomationFinding, ...]
    resolutions_applied: tuple[HumanResolution, ...]
    resolutions_refused: tuple[RefusedResolution, ...]
    rebuilt_package: ConnectionReviewPackage
    decision: str
    blockers: tuple[AutomationFinding, ...]
    reasons: tuple[AutomationFinding, ...]
    warnings: tuple[AutomationFinding, ...]
    evidence_summary: tuple[str, ...]
    specification_accepted: bool      # the genuine 7V signal: absent exactly when 7Z's SPECIFICATION blocker is absent
    validation_passed: bool
    validation_failure: AutomationValidationFailure | None
    validation_layers: tuple[str, ...] | None
    assembly_built: bool
    remaining_task_ids: tuple[str, ...]  # tasks not (successfully) resolved by the applied resolutions


@dataclass(frozen=True)
class ResolutionRerunResult:
    """
    The whole project's closed loop, in the collection's own
    submission order. Counts are computed from the individual genuine
    outcomes (never asserted, never a score); there is no project
    decision and no project-level AUTO shortcut. `intake_scope` is the
    7AC package's 7AB-carried scope, kept verbatim so the page-level
    facts stay visible.
    """
    project_id: str | None
    connections_total: int
    auto_count: int
    confirm_count: int
    review_count: int
    outcomes: tuple[ConnectionRerunOutcome, ...]
    applied_count: int
    refused_count: int
    before_blockers_by_code: tuple[tuple[str, int], ...]
    after_blockers_by_code: tuple[tuple[str, int], ...]
    summary: tuple[str, ...]
    warnings: tuple[AutomationFinding, ...]
    intake_scope: ProjectIntakeScope
    scope_statement: str = RESOLUTION_RERUN_SCOPE_STATEMENT


def _refusal_reason(task: ExceptionResolutionTask, resolution: HumanResolution) -> str | None:
    """
    None when the recorded answer stays within its task's own
    authoritative vocabulary; otherwise the reason it is refused.
    ONLY the task's stated choices are checked (member choices when the
    task has any; the existing START/END position and surface
    vocabulary; a non-empty identifier string for a connection
    identity) — never an engineering rule, never a downstream gate
    duplicated.
    """
    answer = resolution.answer
    if task.answer_type == ANSWER_CONNECTION_IDENTITY:
        if not isinstance(answer, str) or not answer.strip():
            return (
                f"connection identity {answer!r} is not a non-empty identifier string; an "
                "identity is never invented or defaulted."
            )
        return None
    if task.answer_type == ANSWER_MATERIAL_VALUE:
        if not isinstance(answer, str) or not answer.strip():
            return (
                f"material {answer!r} is not a non-empty designation string; a material is "
                "never invented or defaulted."
            )
        return None
    if task.answer_type == ANSWER_POSITION_VALUE:
        if answer not in CONNECTION_POSITION_CHOICES:
            return (
                f"position {answer!r} is not one of the existing connection positions "
                f"{list(CONNECTION_POSITION_CHOICES)}; a position is never inferred or invented."
            )
        return None
    if task.answer_type == ANSWER_MEMBER_SELECTION:
        if task.allowed_choices and any(mark not in task.allowed_choices for mark in answer):
            invalid = [mark for mark in answer if mark not in task.allowed_choices]
            return (
                f"member mark(s) {invalid} are not among the authoritative member choices "
                f"{list(task.allowed_choices)}; a mark that appears nowhere in the validated member "
                "context is never accepted."
            )
        return None
    if task.answer_type == ANSWER_ATTACHMENTS_VALUE:
        for entry in answer:
            if not isinstance(entry, Mapping):
                return f"attachment entry {entry!r} is not a {{member_mark, surface_reference}} object."
            surface = entry.get("surface_reference")
            if surface not in CONNECTION_POSITION_CHOICES:
                return (
                    f"attachment surface_reference {surface!r} is not one of the existing surfaces "
                    f"{list(CONNECTION_POSITION_CHOICES)}; a surface reference is never invented or defaulted."
                )
        return None
    if task.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        marks, position, attachments = answer
        if task.allowed_choices and any(mark not in task.allowed_choices for mark in marks):
            invalid = [mark for mark in marks if mark not in task.allowed_choices]
            return (
                f"member mark(s) {invalid} are not among the authoritative member choices "
                f"{list(task.allowed_choices)}."
            )
        if position not in CONNECTION_POSITION_CHOICES:
            return (
                f"position {position!r} is not one of the existing connection positions "
                f"{list(CONNECTION_POSITION_CHOICES)}."
            )
        for entry in attachments:
            if not isinstance(entry, Mapping):
                return f"attachment entry {entry!r} is not a {{member_mark, surface_reference}} object."
            surface = entry.get("surface_reference")
            if surface not in CONNECTION_POSITION_CHOICES:
                return (
                    f"attachment surface_reference {surface!r} is not one of the existing surfaces "
                    f"{list(CONNECTION_POSITION_CHOICES)}."
                )
        return None
    return None


def rerun_connection_after_resolutions(
    group: ConnectionExceptionTasks,
    package: ConnectionReviewPackage,
    *,
    member_rows: Mapping[str, Mapping[str, object]] | None = None,
    member_placements: Mapping[str, MemberPlacement] | None = None,
    section_matcher: object = None,
    require_confirmation: Sequence[str] = (),
) -> ConnectionRerunOutcome:
    """
    Rebuilds one candidate's 7W package from the resolutions recorded
    on its 7AC task group and re-runs the genuine automation pipeline
    (7AA -> 7V/7D/7J/7N -> 7A/7I -> 7O -> 7R -> 7Z) on the rebuilt
    package. The caller must supply the candidate package that produced
    `group` (the project-level entry pairs them by review_package_id;
    there is no id on a package to cross-check here).

    Every recorded HumanResolution is first checked against its task's
    own authoritative vocabulary; an answer outside it is REFUSED
    (recorded with its reason, never applied, its task left
    unresolved). The accepted resolutions are written onto the EXISTING
    7W supplement by 7AC's build_resolved_supplement() and the package
    is rebuilt by the existing create_review_package() — the original
    AI extraction is preserved untouched. The genuine 7AA pipeline
    then runs on the rebuilt package with the supplied member context;
    7Z decides. The outcome carries both the before-state (the group's
    7AB findings) and the after-state (7AA's genuine result).

    Read-only and deterministic: nothing is mutated, and two re-runs of
    the same inputs return equal outcomes. There is deliberately no
    validation_passed parameter and no shortcut of any kind.
    """
    if not isinstance(group, ConnectionExceptionTasks):
        raise TypeError(
            f"group must be a ConnectionExceptionTasks (got {type(group).__name__}); the re-run "
            "consumes the 7AC exception tasks of one connection."
        )
    if not isinstance(package, ConnectionReviewPackage):
        raise TypeError(
            f"package must be a ConnectionReviewPackage (got {type(package).__name__}); the re-run "
            "rebuilds existing 7W review packages."
        )

    applied: list[HumanResolution] = []
    refused: list[RefusedResolution] = []
    resolved_ids: set[str] = set()
    for task in group.tasks:
        resolution = task.resolution
        if resolution is None:
            continue  # OPEN task — nothing recorded to apply
        reason = _refusal_reason(task, resolution)
        if reason is not None:
            refused.append(RefusedResolution(resolution, reason))
            continue
        applied.append(resolution)
        resolved_ids.add(task.task_id)

    supplement = build_resolved_supplement(package, applied)
    rebuilt = create_review_package(
        package.extraction, supplement, known_member_marks=package.known_member_marks,
    )

    pipeline_result = evaluate_reviewed_connection_for_automation(
        rebuilt,
        member_rows=member_rows,
        member_placements=member_placements,
        section_matcher=section_matcher,
        require_confirmation=require_confirmation,
    )
    gate = pipeline_result.automation_gate_result
    return ConnectionRerunOutcome(
        review_package_id=group.review_package_id,
        submission_index=group.submission_index,
        source_identity=group.source_identity,
        before_decision=group.decision,
        before_blockers=group.blockers,
        resolutions_applied=tuple(applied),
        resolutions_refused=tuple(refused),
        rebuilt_package=rebuilt,
        decision=gate.decision,
        blockers=gate.blockers,
        reasons=gate.reasons,
        warnings=gate.warnings,
        evidence_summary=gate.evidence_summary,
        specification_accepted=not any(b.code == AUTOMATION_BLOCKER_SPECIFICATION for b in gate.blockers),
        validation_passed=pipeline_result.validation_passed,
        validation_failure=pipeline_result.validation_failure,
        validation_layers=(
            pipeline_result.validation_result.layers_validated
            if pipeline_result.validation_result is not None
            else None
        ),
        assembly_built=pipeline_result.reviewed_assembly is not None,
        remaining_task_ids=tuple(task.task_id for task in group.tasks if task.task_id not in resolved_ids),
    )


def _aggregate_by_code(findings_by_connection: Sequence[Sequence[AutomationFinding]]) -> tuple[tuple[str, int], ...]:
    """Aggregate blocker codes by 7Z's own order (then any other codes, sorted); individuals stay on their outcomes."""
    counts: dict[str, int] = {}
    for findings in findings_by_connection:
        for finding in findings:
            counts[finding.code] = counts.get(finding.code, 0) + 1
    return (
        tuple((code, counts[code]) for code in AUTOMATION_BLOCKER_CODES if code in counts)
        + tuple((code, counts[code]) for code in sorted(set(counts) - set(AUTOMATION_BLOCKER_CODES)))
    )


def _format_int(value) -> str:
    return "unknown" if value is None else str(value)


def _outcome_line(outcome: ConnectionRerunOutcome) -> str:
    blockers = ", ".join(b.code for b in outcome.blockers) or "none"
    if outcome.validation_passed:
        validation = "passed"
    elif outcome.validation_failure is not None and outcome.validation_failure.stage == PIPELINE_STAGE_VALIDATION:
        validation = "failed"  # 7R genuinely ran and rejected
    else:
        validation = "not reached"  # an earlier layer failed before any assembly existed
    return (
        f"{outcome.review_package_id}: {outcome.before_decision} -> {outcome.decision} — "
        f"{len(outcome.resolutions_applied)} answer(s) applied, {len(outcome.resolutions_refused)} refused; "
        f"specification {'accepted' if outcome.specification_accepted else 'rejected'}; "
        f"validation {validation}; blockers: {blockers}"
    )


def rerun_project_after_resolutions(
    exception_package: ExceptionResolutionPackage,
    collection: ProjectConnectionReviewCollection,
    *,
    member_rows: Mapping[str, Mapping[str, object]] | None = None,
    member_placements: Mapping[str, MemberPlacement] | None = None,
    section_matcher: object = None,
    require_confirmation_by_package_id: Mapping[str, Sequence[str]] | None = None,
) -> ResolutionRerunResult:
    """
    Re-runs the genuine automation pipeline for every candidate of the
    7X collection after the human resolutions recorded on the 7AC
    exception package, in the collection's own submission order, and
    aggregates the individual genuine outcomes.

    The exception package's connection groups must align one-to-one
    with the collection's candidates by review_package_id (anything
    else is a programming error and raises) — a resolution can never
    be applied to a different connection. `member_rows`,
    `member_placements` and `section_matcher` are the shared project
    member context passed unchanged to 7AA for every candidate; when
    absent, each affected candidate's failure is preserved as 7AA's
    own MEMBER_CONTEXT gap — no member is ever invented.
    `require_confirmation_by_package_id` forwards 7Z's confirmation
    hold per candidate (ids that do not exist raise ValueError); the
    confirmation hold, like everything else, is decided by 7Z, never
    by this module.

    The result carries per-candidate before/after states, applied and
    refused resolutions, rebuilt packages, genuine validation evidence
    and the recomputed project counts; there is no project decision,
    no score and no AUTO shortcut. Read-only and deterministic: nothing
    is mutated, and two re-runs of the same inputs return equal
    results.
    """
    if not isinstance(exception_package, ExceptionResolutionPackage):
        raise TypeError(
            f"exception_package must be an ExceptionResolutionPackage (got "
            f"{type(exception_package).__name__}); the re-run consumes the 7AC exception contract."
        )
    if not isinstance(collection, ProjectConnectionReviewCollection):
        raise TypeError(
            f"collection must be a ProjectConnectionReviewCollection (got {type(collection).__name__})."
        )
    if len(exception_package.connection_tasks) != len(collection.candidates):
        raise ValueError(
            f"the exception package has {len(exception_package.connection_tasks)} connection groups for "
            f"{len(collection.candidates)} collection candidates; they must align one-to-one — a "
            "resolution is never applied to a different connection."
        )
    by_id = {c.review_package_id: c for c in collection.candidates}
    if any(group.review_package_id not in by_id for group in exception_package.connection_tasks):
        raise ValueError(
            "the exception package's connection groups do not match the collection's candidates by "
            "review_package_id; a resolution can never be applied to a different connection."
        )
    confirmation_items = require_confirmation_by_package_id or {}
    unknown_ids = sorted(set(confirmation_items) - {c.review_package_id for c in collection.candidates})
    if unknown_ids:
        raise ValueError(
            f"require_confirmation_by_package_id names unknown review_package_id(s) {unknown_ids}; "
            "confirmation items address existing candidates only."
        )

    outcomes = tuple(
        rerun_connection_after_resolutions(
            group,
            by_id[group.review_package_id].package,
            member_rows=member_rows,
            member_placements=member_placements,
            section_matcher=section_matcher,
            require_confirmation=confirmation_items.get(group.review_package_id, ()),
        )
        for group in exception_package.connection_tasks
    )

    total = len(outcomes)
    auto_count = sum(1 for o in outcomes if o.decision == AUTOMATION_DECISION_AUTO)
    confirm_count = sum(1 for o in outcomes if o.decision == AUTOMATION_DECISION_CONFIRM)
    review_count = sum(1 for o in outcomes if o.decision == AUTOMATION_DECISION_REVIEW)
    applied_count = sum(len(o.resolutions_applied) for o in outcomes)
    refused_count = sum(len(o.resolutions_refused) for o in outcomes)

    scope = exception_package.intake_scope
    warnings: list[AutomationFinding] = []
    if scope.pages_received is None:
        warnings.append(AutomationFinding(
            PROJECT_WARNING_SCOPE_UNREPORTED,
            "The 7AC exception package carries no 7Y intake scope, so page-level extraction coverage "
            "(pages received / parse failures / pages not analysed) is not reported for this re-run.",
        ))
    if total > 0 and auto_count == 0:
        warnings.append(AutomationFinding(PROJECT_WARNING_NO_AUTO, NO_AUTO_STATEMENT.format(total=total)))

    lines = [
        f"connections_total = {total}",
        f"AUTO = {auto_count}",
        f"CONFIRM = {confirm_count}",
        f"REVIEW = {review_count}",
        f"answers_applied = {applied_count}; answers_refused = {refused_count}",
    ]
    if scope.pages_received is not None:
        lines.append(
            f"pages_received = {scope.pages_received}; parse_failures = {len(scope.parse_failed_pages)}; "
            f"pages_not_analysed = {_format_int(scope.pages_not_analysed)}; "
            f"drawing_set_page_count = {_format_int(scope.drawing_set_page_count)}"
        )
        if scope.scope_statement is not None:
            lines.append(scope.scope_statement)
    for outcome in outcomes:
        lines.append(_outcome_line(outcome))
        for refused in outcome.resolutions_refused:
            lines.append(f"{outcome.review_package_id}: REFUSED {refused.resolution.task_id} — {refused.reason}")

    return ResolutionRerunResult(
        project_id=exception_package.project_id,
        connections_total=total,
        auto_count=auto_count,
        confirm_count=confirm_count,
        review_count=review_count,
        outcomes=outcomes,
        applied_count=applied_count,
        refused_count=refused_count,
        before_blockers_by_code=_aggregate_by_code([g.blockers for g in exception_package.connection_tasks]),
        after_blockers_by_code=_aggregate_by_code([o.blockers for o in outcomes]),
        summary=tuple(lines),
        warnings=tuple(warnings),
        intake_scope=scope,
    )
