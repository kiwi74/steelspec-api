"""
Milestone 7AB — PROJECT AUTOMATION ORCHESTRATION: the read-only,
deterministic project-level seam that runs the existing per-connection
automation pipeline (7AA) over an existing 7X review collection and
aggregates what comes back:

    Project (7X ProjectConnectionReviewCollection, already built by 7Y/7X)
            |
    evaluate_project_for_automation()
            |   one candidate at a time, in the collection's own submission order
            v
    7AA evaluate_reviewed_connection_for_automation(candidate.package, ...)
            |   which itself runs the genuine chain 7D/7J/7N -> 7A/7I ->
            |   7O assembly -> 7R validation -> 7Z automation gate
            v
    ProjectAutomationResult — one ProjectConnectionAutomationOutcome per
    candidate (AUTO / CONFIRM / REVIEW, decided by 7Z alone), plus factual
    counts and aggregate blocker counts computed FROM those outcomes.

HARD RULES:

  - THE DEPENDENCY CHAIN IS 7AB -> 7AA -> 7O -> 7R -> 7Z, and nothing
    else. This module calls evaluate_reviewed_connection_for_automation()
    (7AA) once per candidate and NEVER calls evaluate_automation_gate()
    or evaluate_candidate_automation() (7Z) itself: 7Z's decision always
    arrives inside 7AA's AutomationPipelineResult. The 7Z constants
    imported here (decision names, blocker-code order, the finding
    type) are its stable vocabulary, consumed for aggregation only —
    they never evaluate anything.
  - NO PROJECT DECISION IS INVENTED. There is deliberately no
    PROJECT_AUTO / PROJECT_CONFIRM / PROJECT_REVIEW and no automation
    score of any kind. The primary output is the collection of
    individual 7Z decisions; the project-level fields are counts and
    factual statements derived from them, plus 7Z's own stable
    blocker codes aggregated by count. A count of zero AUTO is never
    restated as a verdict about the project or the drawing set.
  - COUNTS ARE COMPUTED, NEVER ASSERTED. auto_count / confirm_count /
    review_count are summed from the individual outcomes; nothing is
    hard-coded, and the summary lines repeat exactly those computed
    values.
  - THE 7Y SCOPE IS CONSUMED, NEVER MANUFACTURED. pages_received,
    parse_failed_pages, drawing_set_page_count and pages_not_analysed
    come ONLY from the supplied ProjectExtractionIntake, carried
    verbatim (or None when no intake was supplied). These four facts
    are NOT interchangeable — each keeps its own field — and
    connections_total (candidates supplied) is a fifth, separate
    fact. Zero candidates means only "no candidates were supplied to
    this evaluator": it never claims the drawing set contains no
    connections, and zero AUTO never claims the project cannot be
    automated.
  - THE EXISTING COLLECTION IS CONSUMED AS-IS, NEVER MUTATED.
    Candidates are evaluated in the collection's own submission
    order. Raw AI candidates that no reviewer has touched stay
    exactly that — 7Z reports their review-status/provenance blockers
    and they can never become AUTO, because 7AA runs the genuine
    chain on the untouched package. Nothing is re-paired, dropped,
    merged or reordered, and no member is ever invented when member
    context is missing (7AA's own MEMBER_CONTEXT gap preserves the
    blocker).
  - FAILURE ISOLATION WITHOUT ERROR SWALLOWING. 7AA already preserves
    every documented connection-level failure (GeometryValidationError
    from any composed layer, and input-context gaps) as a REVIEW
    result with the failure recorded, so one candidate's failure never
    stops the others — WITHOUT this module catching anything. There is
    no try/except here: unexpected programming errors propagate loudly
    so tests expose them.
  - DETERMINISTIC, READ-ONLY, NO I/O. No database, no network, no
    Claude, no API route, no web UI, no CAD call, no file writes, no
    Supabase, no FastAPI, no AI-extraction import. The collection, its
    candidates, their packages, and every supplied mapping are never
    mutated. Two evaluations of the same inputs return equal results:
    everything in the result is plain frozen data (no CadQuery
    objects — per-candidate outcomes carry the gate findings, the
    validation evidence, the failure record and the validated layer
    names, not the geometry).

BOUNDARY: a project result reports what the existing automation
pipeline decided for the candidates that were supplied. It is not a
claim that the drawing set is complete, that the AI found every
connection, or that anything is structurally adequate, code-compliant
or fabrication-ready (see 7Z's and the 7Y intake's own scope
statements).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_CODES,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
    AutomationFinding,
)
from app.cad_engine.automation_pipeline import (
    AutomationValidationFailure,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.project_connection_review import ProjectConnectionReviewCollection
from app.cad_engine.project_extraction_intake import ProjectExtractionIntake

__all__ = [
    "PROJECT_WARNING_NO_AUTO", "PROJECT_WARNING_SCOPE_UNREPORTED",
    "PROJECT_WARNINGS",
    "EMPTY_PROJECT_STATEMENT", "NO_AUTO_STATEMENT", "NO_AUTO_SCOPE_STATEMENT",
    "ProjectConnectionAutomationOutcome", "ProjectIntakeScope", "ProjectAutomationResult",
    "evaluate_project_for_automation",
]

# Project-level (non-blocking) warnings — stable codes a project view can key on.
PROJECT_WARNING_NO_AUTO = "AUTOMATION_PROJECT_NO_AUTO"
PROJECT_WARNING_SCOPE_UNREPORTED = "AUTOMATION_PROJECT_SCOPE_UNREPORTED"
PROJECT_WARNINGS = (PROJECT_WARNING_NO_AUTO, PROJECT_WARNING_SCOPE_UNREPORTED)

EMPTY_PROJECT_STATEMENT = (
    "No connection candidates were supplied to this evaluator. This means only that no candidates "
    "were supplied — it does not mean the drawing set contains no connections."
)
NO_AUTO_STATEMENT = (
    "No automatic fabrication output is eligible from these {total} supplied candidate(s): none of "
    "them met the automation gate."
)
NO_AUTO_SCOPE_STATEMENT = (
    "This does not mean the project cannot be automated: it means only that none of the currently "
    "supplied candidates met the automation gate, and the supplied extraction may be incomplete."
)


@dataclass(frozen=True)
class ProjectConnectionAutomationOutcome:
    """
    One candidate's automation result, as 7AA produced it through the
    genuine 7O -> 7R -> 7Z chain. `decision`, `blockers`, `reasons`,
    `warnings` and `evidence_summary` are 7Z's own findings carried
    verbatim; `validation_passed`, `validation_failure`,
    `validation_layers` (the 7R-validated layer names, when 7R
    returned) and `assembly_built` record what the genuine validation
    path actually did. Everything here is plain frozen data — no
    geometry objects — so a whole project result stays serializable.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    decision: str
    blockers: tuple[AutomationFinding, ...]
    reasons: tuple[AutomationFinding, ...]
    warnings: tuple[AutomationFinding, ...]
    evidence_summary: tuple[str, ...]
    validation_passed: bool
    validation_failure: AutomationValidationFailure | None
    validation_layers: tuple[str, ...] | None
    assembly_built: bool


@dataclass(frozen=True)
class ProjectIntakeScope:
    """
    The 7Y intake's own page-coverage facts, carried verbatim from the
    supplied ProjectExtractionIntake — never computed or guessed here.
    All fields are None when no intake result was supplied. The four
    facts are NOT interchangeable: pages_received (pages handed to the
    intake), parse_failed_pages (pages whose AI response could not be
    parsed), drawing_set_page_count (the caller-stated set size, when
    known) and pages_not_analysed (set pages beyond those supplied).
    Candidates found is NOT one of these: it is the separate
    connections_total field of the project result.
    """
    pages_received: int | None
    parse_failed_pages: tuple[Any, ...] | None
    drawing_set_page_count: int | None
    pages_not_analysed: int | None
    scope_statement: str | None


@dataclass(frozen=True)
class ProjectAutomationResult:
    """
    The factual, serializable outcome of running the existing
    automation pipeline over one 7X review collection. The counts are
    computed from `connection_results` (they are never asserted and
    never carried as a score); `blockers_by_code` aggregates the 7Z
    blocker codes of every individual REVIEW outcome, ordered by 7Z's
    own AUTOMATION_BLOCKER_CODES (then any other codes, sorted);
    `summary` restates the counts, the consumed 7Y scope and the
    individual REVIEW blockers in plain lines; `warnings` holds the
    project-level notes (zero AUTO among supplied candidates; scope
    not reported). There is no project decision and no score.
    """
    project_id: str | None
    connections_total: int
    auto_count: int
    confirm_count: int
    review_count: int
    connection_results: tuple[ProjectConnectionAutomationOutcome, ...]
    summary: tuple[str, ...]
    blockers_by_code: tuple[tuple[str, int], ...]
    warnings: tuple[AutomationFinding, ...]
    intake_scope: ProjectIntakeScope


def _format_int(value: Any) -> str:
    return "unknown" if value is None else str(value)


def evaluate_project_for_automation(
    collection: ProjectConnectionReviewCollection,
    *,
    intake: ProjectExtractionIntake | None = None,
    member_rows: Mapping[str, Mapping[str, object]] | None = None,
    member_placements: Mapping[str, MemberPlacement] | None = None,
    section_matcher: object = None,
    require_confirmation_by_package_id: Mapping[str, Sequence[str]] | None = None,
) -> ProjectAutomationResult:
    """
    Runs the existing per-connection automation pipeline (7AA — which
    runs the genuine 7D/7J/7N -> 7A/7I -> 7O -> 7R -> 7Z chain) over
    every candidate of the supplied 7X collection, in the collection's
    own submission order, and aggregates the results.

    `collection` is consumed as-is and never mutated: raw, unreviewed
    candidates are evaluated exactly as they exist and 7Z keeps them
    in REVIEW (they can never become AUTO). `member_rows`,
    `member_placements` and `section_matcher` are the shared project
    member context passed unchanged to 7AA for every candidate; when
    absent, each affected candidate's failure is preserved as 7AA's
    own MEMBER_CONTEXT gap — no member is ever invented.

    `intake`, when supplied, must be the intake that produced
    `collection` (checked by identity): its page-scope facts are
    carried into `intake_scope` verbatim. `require_confirmation_by_package_id`
    maps a candidate's review_package_id to the explicit confirmation
    items 7Z should apply to it; ids that do not exist in the
    collection are a programming error and raise ValueError.

    There is no try/except around the per-candidate evaluation: 7AA
    already preserves every documented connection-level failure as a
    REVIEW result, and unexpected programming errors propagate loudly.
    Deterministic, read-only, no I/O.
    """
    if not isinstance(collection, ProjectConnectionReviewCollection):
        raise TypeError(
            f"collection must be a ProjectConnectionReviewCollection (got {type(collection).__name__}); "
            "the project evaluator consumes the existing 7X review queue, never anything else."
        )
    if intake is not None and intake.collection is not collection:
        raise ValueError(
            "intake.collection is not the collection being evaluated; the 7Y page-scope facts of one "
            "collection can never be attached to another."
        )
    confirmation_items = require_confirmation_by_package_id or {}
    unknown_ids = sorted(set(confirmation_items) - {c.review_package_id for c in collection.candidates})
    if unknown_ids:
        raise ValueError(
            f"require_confirmation_by_package_id names unknown review_package_id(s) {unknown_ids}; "
            "confirmation items address existing candidates only."
        )

    outcomes: list[ProjectConnectionAutomationOutcome] = []
    for candidate in collection.candidates:
        pipeline_result = evaluate_reviewed_connection_for_automation(
            candidate.package,
            member_rows=member_rows,
            member_placements=member_placements,
            section_matcher=section_matcher,
            require_confirmation=confirmation_items.get(candidate.review_package_id, ()),
        )
        gate = pipeline_result.automation_gate_result
        outcomes.append(ProjectConnectionAutomationOutcome(
            review_package_id=candidate.review_package_id,
            submission_index=candidate.submission_index,
            source_identity=candidate.source_identity,
            decision=gate.decision,
            blockers=gate.blockers,
            reasons=gate.reasons,
            warnings=gate.warnings,
            evidence_summary=gate.evidence_summary,
            validation_passed=pipeline_result.validation_passed,
            validation_failure=pipeline_result.validation_failure,
            validation_layers=(
                pipeline_result.validation_result.layers_validated
                if pipeline_result.validation_result is not None
                else None
            ),
            assembly_built=pipeline_result.reviewed_assembly is not None,
        ))

    total = len(outcomes)
    auto_count = sum(1 for o in outcomes if o.decision == AUTOMATION_DECISION_AUTO)
    confirm_count = sum(1 for o in outcomes if o.decision == AUTOMATION_DECISION_CONFIRM)
    review_count = sum(1 for o in outcomes if o.decision == AUTOMATION_DECISION_REVIEW)

    # Aggregate the individual blockers by 7Z's stable code — 7Z's own order first, then
    # any codes it does not define (none today), sorted. Individual blockers are never
    # hidden: they stay on their outcomes and are restated in the summary lines.
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for blocker in outcome.blockers:
            counts[blocker.code] = counts.get(blocker.code, 0) + 1
    blockers_by_code = tuple(
        (code, counts[code]) for code in AUTOMATION_BLOCKER_CODES if code in counts
    ) + tuple(
        (code, counts[code]) for code in sorted(set(counts) - set(AUTOMATION_BLOCKER_CODES))
    )

    scope = (
        ProjectIntakeScope(
            pages_received=intake.pages_received,
            parse_failed_pages=intake.parse_failed_pages,
            drawing_set_page_count=intake.drawing_set_page_count,
            pages_not_analysed=intake.pages_not_analysed,
            scope_statement=intake.scope_statement,
        )
        if intake is not None
        else ProjectIntakeScope(None, None, None, None, None)
    )

    warnings: list[AutomationFinding] = []
    if intake is None:
        warnings.append(AutomationFinding(
            PROJECT_WARNING_SCOPE_UNREPORTED,
            "No 7Y intake result was supplied, so page-level extraction coverage (pages received / "
            "parse failures / pages not analysed) is not reported for this evaluation.",
        ))
    if total > 0 and auto_count == 0:
        warnings.append(AutomationFinding(
            PROJECT_WARNING_NO_AUTO,
            f"None of the {total} supplied candidate(s) met the automation gate (0 AUTO). This does "
            "NOT mean SteelSpec cannot automate this project: the supplied extraction may be "
            "incomplete, and this says nothing about connections outside the supplied candidates.",
        ))

    lines = [
        f"connections_total = {total}",
        f"AUTO = {auto_count}",
        f"CONFIRM = {confirm_count}",
        f"REVIEW = {review_count}",
    ]
    if intake is not None:
        lines.append(
            f"pages_received = {scope.pages_received}; parse_failures = {len(scope.parse_failed_pages)}; "
            f"pages_not_analysed = {_format_int(scope.pages_not_analysed)}; "
            f"drawing_set_page_count = {_format_int(scope.drawing_set_page_count)}"
        )
        if scope.scope_statement is not None:
            lines.append(scope.scope_statement)
    for outcome in outcomes:
        if outcome.blockers:
            lines.append(
                f"{outcome.review_package_id}: REVIEW — blockers: "
                + ", ".join(b.code for b in outcome.blockers)
            )
    if total == 0:
        lines.append(EMPTY_PROJECT_STATEMENT)
    elif auto_count == 0:
        lines.append(NO_AUTO_STATEMENT.format(total=total))
        lines.append(NO_AUTO_SCOPE_STATEMENT)

    return ProjectAutomationResult(
        project_id=collection.project_id,
        connections_total=total,
        auto_count=auto_count,
        confirm_count=confirm_count,
        review_count=review_count,
        connection_results=tuple(outcomes),
        summary=tuple(lines),
        blockers_by_code=blockers_by_code,
        warnings=tuple(warnings),
        intake_scope=scope,
    )
