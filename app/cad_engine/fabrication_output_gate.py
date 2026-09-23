"""
Milestone 7AE — FABRICATION OUTPUT GATE: the final deterministic
permission boundary between "SteelSpec has successfully automated the
engineering/CAD workflow" and "SteelSpec is permitted to generate /
export fabrication drawings":

    7AA (genuine pipeline result: 7V specification, 7O assembly,
        7R validation, 7Z decision)
            |
    evaluate_fabrication_output_gate()                 (per connection)
            |   consumes ONLY the existing AutomationPipelineResult —
            |   nothing is re-run, re-derived or re-validated here
            v
    FabricationOutputGateResult (AUTO / CONFIRM / REVIEW + evidence)
            |
    evaluate_project_fabrication_output_gate()         (per project)
            |   aggregates the per-connection outcomes, carries the 7Y/
            |   7AB scope verbatim, derives the package permission
            v
    ProjectFabricationOutputGateResult

7AE is NOT another engineering validation engine. It answers only one
question: "Does the available evidence satisfy the explicit
prerequisites for automatic fabrication-output generation?" It never
decides structural adequacy, bolt appropriateness, plate design, code
compliance, drawing quality or safety — those belong to engineering
review and the existing validation architecture (7V/7R/7Z/7AA).

HARD RULES:

  - EVIDENCE ONLY, NEVER REASONING. Every check below reads an
    explicit result field the existing pipeline already produced:
    the 7Z decision, the 7V-built specification, the genuine 7O
    assembly and the genuine 7R validation result. Nothing is
    recomputed, no 7V/7R/7Z logic is duplicated, and 7AA is never
    re-run inside this module.
  - NO SUCCESSFUL-CALL SHORTCUT. "7AA returned without raising" is
    never treated as permission: the explicit result fields are the
    only evidence. There is deliberately no validation_passed
    parameter and no approved=True parameter anywhere in this API —
    a caller cannot inject evidence.
  - THE THREE QUESTIONS STAY SEPARATE. 7Z answers automation
    readiness, 7R answers geometric/interface validation, 7AE answers
    fabrication-output permission. 7Z AUTO is never automatically
    fabrication output: the remaining 7AE evidence (specification
    with connection identity, genuine 7R result, genuine assembly)
    is checked independently on the AUTO path.
  - 7Z REVIEW -> 7AE REVIEW, 7Z CONFIRM -> 7AE CONFIRM. 7Z's own
    blockers keep their exact identity when passed through (a 7Z
    blocker is never renamed to an OUTPUT_* code); OUTPUT_* blocker
    codes exist only for prerequisites that genuinely do not belong
    to 7Z: an unexplained or invalid 7Z decision, a missing 7V
    specification/identity, missing or failed 7R evidence, and a
    missing 7O assembly.
  - BLOCKERS WIN, THEN CONFIRMATION, THEN AUTO — exactly 7Z's own
    decision discipline. A confirmation hold (require_confirmation)
    never manufactures validation: the same genuine evidence is
    re-read once the hold is released.
  - NO DRAWING GENERATION HERE, EVER. This module produces permission
    and evidence only — no DXF, no PDF, no STEP, no CAD, no images, no
    files of any kind, and it imports no drawing module. The intended
    future integration is: evaluate the gate; only on AUTO may the
    drawing generator be called (a future milestone owns that
    dispatch; tests prove the blocked path never reaches it).
  - PROJECT SCOPE NEVER BLOCKS A CONNECTION. Unanalysed pages and
    parse failures are 7Y/7AB scope facts carried verbatim into the
    project result and restated in the scope statement — the gate
    never claims the entire drawing set was processed — but they do
    not downgrade a connection whose own evidence is complete
    (connection eligibility and drawing-set completeness are two
    different questions).
  - NO SCORES, NO RANKS, NO CONFIDENCE. Counts are computed from the
    individual outcomes, never asserted; AI confidence is never
    fabrication-output evidence (it does not even enter this module).
  - DETERMINISTIC, READ-ONLY, FROZEN PLAIN DATA. Inputs are never
    mutated; every result is a frozen dataclass of plain data (no
    CadQuery objects, no drawing objects); two evaluations of the
    same inputs return equal results. No I/O, no network, no Claude,
    no database, no API route, no UI.

BOUNDARY: AUTO here is permission to generate a fabrication drawing
for one validated connection through the approved integration
boundary — it is not structural adequacy, code compliance,
fabrication readiness of a whole project, or a claim that the drawing
set was fully processed (see the scope statements).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_CODES,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_DECISIONS,
    AutomationFinding,
    AutomationGateResult,
)
from app.cad_engine.automation_pipeline import AutomationPipelineResult
from app.cad_engine.project_automation import (
    PROJECT_WARNING_SCOPE_UNREPORTED,
    ProjectAutomationResult,
    ProjectIntakeScope,
)
from app.cad_engine.reviewed_connection_specification import ReviewedConnectionSpecification

__all__ = [
    "OUTPUT_BLOCKER_AUTOMATION_DECISION", "OUTPUT_BLOCKER_SPECIFICATION",
    "OUTPUT_BLOCKER_VALIDATION", "OUTPUT_BLOCKER_ASSEMBLY", "OUTPUT_BLOCKER_CODES",
    "OUTPUT_REASON_ALL_REQUIREMENTS_SATISFIED", "OUTPUT_REASON_CONFIRMATION_REQUESTED",
    "OUTPUT_REASON_CONFIRMABLE", "OUTPUT_REASON_BLOCKED",
    "OUTPUT_WARNING_CONFIRMATION_DEFERRED", "OUTPUT_WARNINGS",
    "FABRICATION_OUTPUT_SCOPE_STATEMENT", "PROJECT_FABRICATION_OUTPUT_SCOPE_STATEMENT",
    "EMPTY_FABRICATION_OUTPUT_STATEMENT", "FABRICATION_PACKAGE_RELEASED_STATEMENT",
    "FABRICATION_PACKAGE_NOT_RELEASED_STATEMENT",
    "FabricationOutputGateResult", "ProjectConnectionFabricationOutputOutcome",
    "ProjectFabricationOutputGateResult",
    "evaluate_fabrication_output_gate", "evaluate_project_fabrication_output_gate",
]

# 7AE-specific blocker codes — prerequisites that genuinely do not belong to 7Z.
# A 7Z blocker that still applies is passed through with its ORIGINAL identity
# (never renamed). The drawing generator's own contract consumes exactly the
# reviewed assembly (and re-runs 7R itself), so no further drawing-prerequisite
# blocker exists in the architecture — adding one would be over-gating.
OUTPUT_BLOCKER_AUTOMATION_DECISION = "OUTPUT_BLOCKER_AUTOMATION_DECISION"
OUTPUT_BLOCKER_SPECIFICATION = "OUTPUT_BLOCKER_SPECIFICATION"
OUTPUT_BLOCKER_VALIDATION = "OUTPUT_BLOCKER_VALIDATION"
OUTPUT_BLOCKER_ASSEMBLY = "OUTPUT_BLOCKER_ASSEMBLY"
OUTPUT_BLOCKER_CODES = (
    OUTPUT_BLOCKER_AUTOMATION_DECISION,
    OUTPUT_BLOCKER_SPECIFICATION,
    OUTPUT_BLOCKER_VALIDATION,
    OUTPUT_BLOCKER_ASSEMBLY,
)

OUTPUT_REASON_ALL_REQUIREMENTS_SATISFIED = "OUTPUT_REASON_ALL_REQUIREMENTS_SATISFIED"
OUTPUT_REASON_CONFIRMATION_REQUESTED = "OUTPUT_REASON_CONFIRMATION_REQUESTED"
OUTPUT_REASON_CONFIRMABLE = "OUTPUT_REASON_CONFIRMABLE"
OUTPUT_REASON_BLOCKED = "OUTPUT_REASON_BLOCKED"

OUTPUT_WARNING_CONFIRMATION_DEFERRED = "OUTPUT_WARNING_CONFIRMATION_DEFERRED"
OUTPUT_WARNINGS = (OUTPUT_WARNING_CONFIRMATION_DEFERRED,)

FABRICATION_OUTPUT_SCOPE_STATEMENT = (
    "AUTO here is permission to generate the fabrication drawing for ONE validated connection "
    "through the approved integration boundary. It is not structural adequacy, code compliance, "
    "fabrication readiness of a whole project, or a claim that the drawing set was fully processed."
)
PROJECT_FABRICATION_OUTPUT_SCOPE_STATEMENT = (
    "This result reports per-connection fabrication-output permission and whether a complete "
    "automatic fabrication package may be generated from the supplied connections. Page-level "
    "scope facts (pages received / parse failures / pages not analysed) come only from the 7Y "
    "intake carried through 7AB: the gate never claims the entire drawing set was processed, and "
    "connection eligibility is never downgraded by pages that were not analysed."
)
EMPTY_FABRICATION_OUTPUT_STATEMENT = (
    "No connections were supplied to this output gate. This means only that no automatic "
    "fabrication output was requested — it does not mean the drawing set contains no connections."
)
FABRICATION_PACKAGE_RELEASED_STATEMENT = (
    "Every supplied connection is individually eligible for automatic fabrication drawing "
    "generation, so a complete automatic fabrication package may be generated from these outputs."
)
FABRICATION_PACKAGE_NOT_RELEASED_STATEMENT = (
    "A complete automatic fabrication package is NOT released: at least one supplied connection "
    "is CONFIRM or REVIEW. Each AUTO connection keeps its own eligibility; only the automatic "
    "generation of the complete package is held."
)


@dataclass(frozen=True)
class FabricationOutputGateResult:
    """
    The fabrication-output permission for one connection, built
    entirely from one genuine 7AA AutomationPipelineResult. `decision`
    is exactly one of AUTOMATION_DECISIONS. `blockers` carries the 7Z
    blockers verbatim when the 7Z decision is REVIEW, and 7AE-specific
    OUTPUT_* findings when the AUTO path's own evidence is missing or
    contradictory. `evidence_summary` restates the genuine evidence
    fields (7Z decision, 7V specification/completeness, 7R validation
    with its layer labels, assembly existence, connection identity).
    Plain frozen data only — no geometry, no drawings, no files.
    """
    decision: str
    connection_id: str | None
    blockers: tuple[AutomationFinding, ...]
    reasons: tuple[AutomationFinding, ...]
    warnings: tuple[AutomationFinding, ...]
    evidence_summary: tuple[str, ...]
    output_scope: str = FABRICATION_OUTPUT_SCOPE_STATEMENT


@dataclass(frozen=True)
class ProjectConnectionFabricationOutputOutcome:
    """
    One candidate's fabrication-output permission in the project's own
    submission order: the 7X identity plus the 7AE gate result.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    gate_result: FabricationOutputGateResult


@dataclass(frozen=True)
class ProjectFabricationOutputGateResult:
    """
    The whole project's fabrication-output permission. `package_decision`
    is derived, never asserted: REVIEW if any connection is REVIEW, else
    CONFIRM if any connection is CONFIRM, else AUTO when every supplied
    connection is AUTO (empty project -> REVIEW with the empty
    statement). `eligible_count` / `confirm_count` / `review_count` are
    computed from the individual outcomes; `blocked_outputs` lists every
    review_package_id whose decision is not AUTO; `blockers_by_code`
    aggregates by 7AE's own code order first, then 7Z's, then any other
    codes sorted. `intake_scope` is the 7AB-carried 7Y scope, verbatim.
    No score, no rank, no "confidence".
    """
    project_id: str | None
    connections_total: int
    package_decision: str
    eligible_count: int
    confirm_count: int
    review_count: int
    connection_results: tuple[ProjectConnectionFabricationOutputOutcome, ...]
    blocked_outputs: tuple[str, ...]
    blockers_by_code: tuple[tuple[str, int], ...]
    summary: tuple[str, ...]
    warnings: tuple[AutomationFinding, ...]
    intake_scope: ProjectIntakeScope
    scope_statement: str = PROJECT_FABRICATION_OUTPUT_SCOPE_STATEMENT


def _evidence_lines(
    gate: AutomationGateResult,
    pipeline_result: AutomationPipelineResult,
) -> tuple[str, ...]:
    """The genuine evidence the decision was based on — actual result fields only, restated, never invented."""
    spec: ReviewedConnectionSpecification | None = pipeline_result.specification
    validation_result = pipeline_result.validation_result
    lines = [
        f"7Z decision: {gate.decision}",
        f"7V specification: {'built' if spec is not None else 'missing'}",
        "7V completeness: " + (
            "accepted by 7Z" if not any(b.code == AUTOMATION_BLOCKER_SPECIFICATION for b in gate.blockers)
            else "rejected by 7Z"
        ),
        (
            "7R validation: passed" if pipeline_result.validation_passed is True
            else "7R validation: failed" if pipeline_result.validation_passed is False
            else "7R validation: not run"
        ),
        f"validation layers: "
        + (", ".join(validation_result.layers_validated) if validation_result is not None else "none"),
        f"assembly built: {'true' if pipeline_result.reviewed_assembly is not None else 'false'}",
        f"connection identity: {spec.connection_id!r}" if spec is not None else "connection identity: missing",
        f"member marks: {list(spec.connected_member_marks)!r}" if spec is not None else "member marks: missing",
        "fabrication output requires: an AUTO-compatible 7Z decision, a 7V-built specification with "
        "connection identity, genuine 7R validation evidence and a genuine 7O assembly.",
    ]
    # Material (7AT) is an OPTIONAL engineering value this gate sees on the specification.
    # The line appears only when the specification carries one — the gate never invents
    # "not specified" for a value that was never there, and the decision rules above are
    # unchanged: a present material never changes REVIEW/CONFIRM/AUTO.
    if spec is not None and spec.material is not None:
        lines.append(
            f"material: {spec.material!r} (provenance {spec.provenance.get('material')!r})"
        )
    return tuple(lines)


def evaluate_fabrication_output_gate(
    pipeline_result: AutomationPipelineResult,
    *,
    require_confirmation: Sequence[str] = (),
) -> FabricationOutputGateResult:
    """
    The deterministic fabrication-output permission for one connection,
    consumed from one genuine 7AA AutomationPipelineResult — nothing is
    re-run, and nothing about the CAD pipeline is duplicated here.

    Decision discipline (mirrors 7Z's own: blockers win, then
    confirmation, then AUTO):

      - 7Z decision REVIEW: the 7Z blockers pass through with their
        original identities; the result is REVIEW. (An unexplained
        REVIEW — no blockers recorded — is refused with
        OUTPUT_BLOCKER_AUTOMATION_DECISION.)
      - 7Z decision CONFIRM: the result is CONFIRM — never AUTO.
      - 7Z decision AUTO: the remaining 7AE evidence is then checked
        against the explicit result fields, independently of the
        decision:
            1. the 7Z result itself must be coherent (no blockers
               recorded beside an AUTO decision);
            2. the 7V product must exist and carry a connection
               identity (OUTPUT_BLOCKER_SPECIFICATION otherwise);
            3. genuine 7R evidence must exist: validation_passed True,
               a validation_result present and no validation_failure
               (OUTPUT_BLOCKER_VALIDATION otherwise — 7R not run, or
               run and failed, both block);
            4. the genuine 7O assembly must exist
               (OUTPUT_BLOCKER_ASSEMBLY otherwise).
        With any finding the result is REVIEW; with none, an explicit
        `require_confirmation` hold keeps the result at CONFIRM (the
        confirmation never manufactures validation — re-evaluating the
        same genuine evidence after the hold is released is what
        permits AUTO).

    Read-only, deterministic, no I/O: never mutates anything, never
    calls any 7V/7R/7Z/7O function, never touches the drawing
    generator, never writes a file.
    """
    if not isinstance(pipeline_result, AutomationPipelineResult):
        raise TypeError(
            f"pipeline_result must be an AutomationPipelineResult (got {type(pipeline_result).__name__}); "
            "the fabrication-output gate consumes the genuine 7AA result, never anything else."
        )
    gate = pipeline_result.automation_gate_result
    confirmation_items = tuple(require_confirmation)

    blockers: list[AutomationFinding] = []
    if gate.decision not in AUTOMATION_DECISIONS:
        blockers.append(AutomationFinding(
            OUTPUT_BLOCKER_AUTOMATION_DECISION,
            f"The 7Z automation decision is {gate.decision!r}, which is not one of the existing decisions "
            f"{list(AUTOMATION_DECISIONS)}. An unresolved automation decision never permits fabrication output.",
        ))
    elif gate.decision == AUTOMATION_DECISION_REVIEW:
        blockers.extend(gate.blockers)  # original 7Z identities, verbatim
        if not gate.blockers:
            blockers.append(AutomationFinding(
                OUTPUT_BLOCKER_AUTOMATION_DECISION,
                "The 7Z automation decision is REVIEW but no blocker was recorded; unexplained review "
                "states never permit fabrication output.",
            ))
    elif gate.decision == AUTOMATION_DECISION_AUTO:
        # AUTO-compatible 7Z decision: inspect the remaining 7AE evidence independently.
        if gate.blockers:
            blockers.append(AutomationFinding(
                OUTPUT_BLOCKER_AUTOMATION_DECISION,
                f"The 7Z result is internally inconsistent: decision AUTO with "
                f"{len(gate.blockers)} blocker(s) recorded. Automatic fabrication output is refused.",
            ))
            blockers.extend(gate.blockers)
        specification = pipeline_result.specification
        if specification is None or specification.connection_id is None:
            blockers.append(AutomationFinding(
                OUTPUT_BLOCKER_SPECIFICATION,
                "No 7V-built specification with a connection identity is available. Fabrication output "
                "requires the genuine reviewed specification (7V's product); a decision alone is never "
                "sufficient evidence.",
            ))
        if (
            pipeline_result.validation_passed is not True
            or pipeline_result.validation_result is None
            or pipeline_result.validation_failure is not None
        ):
            blockers.append(AutomationFinding(
                OUTPUT_BLOCKER_VALIDATION,
                "Genuine 7R validation evidence is missing or failed. Fabrication output requires the "
                "validation result the existing pipeline actually produced (validation_passed=True with a "
                "7R result and no preserved failure); a successful function call is never evidence, and "
                "caller-supplied validation claims are never accepted.",
            ))
        if pipeline_result.reviewed_assembly is None:
            blockers.append(AutomationFinding(
                OUTPUT_BLOCKER_ASSEMBLY,
                "No genuine 7O reviewed assembly exists. Fabrication drawing generation consumes the "
                "built assembly; a missing assembly never permits output.",
            ))

    warnings = list(gate.warnings)
    if confirmation_items and blockers:
        warnings.append(AutomationFinding(
            OUTPUT_WARNING_CONFIRMATION_DEFERRED,
            f"{len(confirmation_items)} explicit output-confirmation item(s) were requested but "
            "fabrication output is blocked; they will be reconsidered once the blockers are resolved.",
        ))

    if blockers:
        decision = AUTOMATION_DECISION_REVIEW
        reasons = tuple(blockers) + (AutomationFinding(
            OUTPUT_REASON_BLOCKED,
            f"{len(blockers)} finding(s) block automatic fabrication output; the drawing generator must "
            "not be called for this connection.",
        ),)
    elif gate.decision == AUTOMATION_DECISION_CONFIRM or confirmation_items:
        # 7Z CONFIRM stays CONFIRM — never silently AUTO. The 7Z reasons
        # (confirmation requested / validation not evidenced / confirmable)
        # pass through verbatim; a 7AE-level output hold adds the 7AE reason
        # pair. The confirmation itself never manufactures validation: only a
        # re-evaluation over the same genuine evidence, once the confirmation
        # policy is satisfied, permits AUTO.
        decision = AUTOMATION_DECISION_CONFIRM
        reasons = tuple(gate.reasons if gate.decision == AUTOMATION_DECISION_CONFIRM else ())
        if confirmation_items:
            reasons = reasons + (
                AutomationFinding(
                    OUTPUT_REASON_CONFIRMATION_REQUESTED,
                    "Explicit fabrication-output confirmation was requested: " + "; ".join(confirmation_items),
                ),
                AutomationFinding(
                    OUTPUT_REASON_CONFIRMABLE,
                    "Every fabrication-output prerequisite on the available evidence is satisfied; only "
                    "explicit output confirmation remains. Re-evaluate the same genuine evidence after the "
                    "confirmation policy is satisfied — the confirmation itself never manufactures validation.",
                ),
            )
    else:
        decision = AUTOMATION_DECISION_AUTO
        reasons = (AutomationFinding(
            OUTPUT_REASON_ALL_REQUIREMENTS_SATISFIED,
            "Every fabrication-output prerequisite is satisfied: an AUTO-compatible 7Z decision, a "
            "7V-built specification with connection identity, genuine 7R validation evidence and a "
            "genuine 7O assembly. AUTO is permission to generate the fabrication drawing through the "
            "approved integration boundary — it is NOT engineering approval.",
        ),)

    return FabricationOutputGateResult(
        decision=decision,
        connection_id=(
            pipeline_result.specification.connection_id if pipeline_result.specification is not None else None
        ),
        blockers=tuple(blockers),
        reasons=reasons,
        warnings=tuple(warnings),
        evidence_summary=_evidence_lines(gate, pipeline_result),
    )


def _format_int(value) -> str:
    return "unknown" if value is None else str(value)


def evaluate_project_fabrication_output_gate(
    project_result: ProjectAutomationResult,
    pipeline_results: Mapping[str, AutomationPipelineResult],
    *,
    require_confirmation_by_package_id: Mapping[str, Sequence[str]] | None = None,
) -> ProjectFabricationOutputGateResult:
    """
    The project-level fabrication-output permission: evaluates the
    connection-level gate over the genuine 7AA pipeline results (one
    per 7AB outcome — supplied by the caller, never re-run here) and
    aggregates deterministically.

    `project_result` supplies the project identity, the per-candidate
    order/identity and the 7AB-carried 7Y intake scope; `pipeline_results`
    must align one-to-one with its connection_results by
    review_package_id (anything else is a programming error and
    raises). `require_confirmation_by_package_id` applies the explicit
    output-confirmation hold per candidate (unknown ids raise
    ValueError); held candidates are CONFIRM, and the complete
    automatic package stays unreleased until the policy is satisfied.

    `package_decision` is derived from the individual outcomes only:
    any REVIEW makes the package REVIEW, else any CONFIRM makes it
    CONFIRM, else every supplied connection is AUTO and the package is
    AUTO (zero connections -> REVIEW with the empty statement). Per-
    connection eligibility is never downgraded by another connection's
    outcome or by page-level scope facts. No score, no rank.
    """
    if not isinstance(project_result, ProjectAutomationResult):
        raise TypeError(
            f"project_result must be a ProjectAutomationResult (got {type(project_result).__name__}); "
            "the project output gate aggregates the genuine 7AB project result."
        )
    if not isinstance(pipeline_results, Mapping):
        raise TypeError(
            f"pipeline_results must be a mapping of review_package_id -> AutomationPipelineResult "
            f"(got {type(pipeline_results).__name__})."
        )
    expected_ids = [outcome.review_package_id for outcome in project_result.connection_results]
    if sorted(pipeline_results) != sorted(expected_ids):
        raise ValueError(
            "pipeline_results must align one-to-one with the project result's connection_results by "
            "review_package_id; fabrication-output evidence of one connection can never be applied to "
            "another."
        )
    confirmation_items = require_confirmation_by_package_id or {}
    unknown_ids = sorted(set(confirmation_items) - set(expected_ids))
    if unknown_ids:
        raise ValueError(
            f"require_confirmation_by_package_id names unknown review_package_id(s) {unknown_ids}; "
            "confirmation items address existing candidates only."
        )

    outcomes = tuple(
        ProjectConnectionFabricationOutputOutcome(
            review_package_id=outcome.review_package_id,
            submission_index=outcome.submission_index,
            source_identity=outcome.source_identity,
            gate_result=evaluate_fabrication_output_gate(
                pipeline_results[outcome.review_package_id],
                require_confirmation=confirmation_items.get(outcome.review_package_id, ()),
            ),
        )
        for outcome in project_result.connection_results
    )

    total = len(outcomes)
    eligible_count = sum(1 for o in outcomes if o.gate_result.decision == AUTOMATION_DECISION_AUTO)
    confirm_count = sum(1 for o in outcomes if o.gate_result.decision == AUTOMATION_DECISION_CONFIRM)
    review_count = sum(1 for o in outcomes if o.gate_result.decision == AUTOMATION_DECISION_REVIEW)
    blocked_outputs = tuple(
        o.review_package_id for o in outcomes if o.gate_result.decision != AUTOMATION_DECISION_AUTO
    )

    # The complete automatic package: derived from the individual outcomes only.
    if review_count:
        package_decision = AUTOMATION_DECISION_REVIEW
    elif confirm_count:
        package_decision = AUTOMATION_DECISION_CONFIRM
    elif total:
        package_decision = AUTOMATION_DECISION_AUTO
    else:
        package_decision = AUTOMATION_DECISION_REVIEW

    counts: dict[str, int] = {}
    for outcome in outcomes:
        for blocker in outcome.gate_result.blockers:
            counts[blocker.code] = counts.get(blocker.code, 0) + 1
    blockers_by_code = tuple(
        (code, counts[code]) for code in OUTPUT_BLOCKER_CODES if code in counts
    ) + tuple(
        (code, counts[code]) for code in AUTOMATION_BLOCKER_CODES if code in counts
    ) + tuple(
        (code, counts[code]) for code in sorted(set(counts) - set(OUTPUT_BLOCKER_CODES) - set(AUTOMATION_BLOCKER_CODES))
    )

    scope = project_result.intake_scope
    warnings: list[AutomationFinding] = []
    if scope.pages_received is None:
        warnings.append(AutomationFinding(
            PROJECT_WARNING_SCOPE_UNREPORTED,
            "No 7Y intake scope was carried into the project result, so page-level extraction coverage "
            "(pages received / parse failures / pages not analysed) is not reported for this output gate.",
        ))

    lines = [
        f"connections_total = {total}",
        f"eligible_outputs = {eligible_count}",
        f"CONFIRM = {confirm_count}",
        f"REVIEW = {review_count}",
        f"fabrication package: {package_decision}",
        (
            FABRICATION_PACKAGE_RELEASED_STATEMENT
            if package_decision == AUTOMATION_DECISION_AUTO
            else FABRICATION_PACKAGE_NOT_RELEASED_STATEMENT
        ),
        f"blocked_outputs = {', '.join(blocked_outputs) or 'none'}",
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
        lines.append(
            f"{outcome.review_package_id}: {outcome.gate_result.decision} — blockers: "
            + (", ".join(b.code for b in outcome.gate_result.blockers) or "none")
        )
    if total == 0:
        lines.append(EMPTY_FABRICATION_OUTPUT_STATEMENT)

    return ProjectFabricationOutputGateResult(
        project_id=project_result.project_id,
        connections_total=total,
        package_decision=package_decision,
        eligible_count=eligible_count,
        confirm_count=confirm_count,
        review_count=review_count,
        connection_results=outcomes,
        blocked_outputs=blocked_outputs,
        blockers_by_code=blockers_by_code,
        summary=tuple(lines),
        warnings=tuple(warnings),
        intake_scope=scope,
    )
