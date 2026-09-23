"""
Milestone 7Z — the AUTOMATION GATE: a read-only, deterministic routing
decision over the existing 7W/7X review pipeline, deciding how a
reviewed connection candidate may proceed toward CAD and drawing
generation:

    PDF -> AI extraction -> 7Y intake -> 7X queue -> 7Z AUTOMATION GATE
                                                        |
                     AUTO ----------------- CONFIRM -------------- REVIEW
                 proceed without       explicit human        human must resolve
                 further confirmation   confirmation first    missing/ambiguous
                                                        |
                       7V completeness -> 7O assembly -> 7R validation -> 7S/7T drawing
                       (already checked inside the gate; 7O/7R/7S/7T are outside it)

THE THREE OUTCOMES, and nothing else (AUTOMATION_DECISIONS):

  AUTO    — every automation requirement is satisfied: approved review
            status, human-owned provenance on all six engineering
            fields, known member identity, complete and valid geometry
            fields (the existing 7V completeness gate accepts the
            specification), and the downstream validation gate (7R)
            has been run and passed. AUTO is an AUTOMATION-READINESS
            decision only. It is NOT engineering approval, structural
            adequacy, code compliance or fabrication readiness.
  CONFIRM — the reviewed information itself satisfies every
            requirement (7V completeness passes, nothing is missing or
            invalid), but automation is held for explicit human
            confirmation: confirmation items were requested (e.g. a
            reused approved pattern to confirm against a register), or
            no downstream-validation evidence has been supplied yet.
  REVIEW  — insufficient trustworthy information: unresolved member
            identity, missing position/plate/holes/location/
            attachments, AI-only provenance on a required field,
            malformed AI fields, conflicting reviewer decisions, a
            specification the 7V gate rejects (invalid numerics
            included), or failed downstream validation. REVIEW is the
            conservative default: whenever a candidate is between
            CONFIRM and REVIEW, it is REVIEW.

RULES THIS GATE ENFORCES (each one a hard rule, never a score):

  - CONFIDENCE IS NOT EVIDENCE. There is no automation_score, no
    weighted sum, and no `if confidence >= threshold: AUTO`. A
    confidence of 99 on an unreviewed extraction is still REVIEW. AI
    confidence is reported in the evidence summary as what it is: a
    reading-quality signal, never an engineering approval.
  - NOTHING IS INVENTED. A nominal bolt size ("M20", "M12",
    "SQ4 12mm") is never converted into a hole diameter ("Ø20" is
    never produced anywhere). Missing values are never defaulted to
    zero, one, "?" or anything else. A member mark is never inferred.
    A position is never inferred from list order. An attachment is
    never defaulted to END/START.
  - PROVENANCE IS NOT WEAKENED. 7V's rule stands unchanged: every
    required field (connected_member_marks, position, plate, holes,
    location, attachments) must be HUMAN_REVIEWED or
    HUMAN_SUPPLEMENTED — never AI_EXTRACTED — and review_status must
    be exactly "approved". The gate only *reports* this; it never
    relabels anything.
  - MEMBER IDENTITY IS REQUIRED. A connected member mark that matches
    no known project member blocks automation, as does a member set
    that could not be checked at all (no known_member_marks
    supplied). The gate never classifies materials: a timber
    reference like "stringer 140x45 H4 SG8" is blocked because it is
    not a known structural steel member mark, not because the gate
    recognises timber.
  - THE EXISTING GATES ARE REUSED, NEVER DUPLICATED. The gate calls
    build_review_report() (7W), whose completeness result IS 7V's own
    check_reviewed_connection_specification_completeness() plus 7W's
    project-member gate — so every numeric rule (positive finite
    plate dimensions, numeric hole diameter, finite location fields,
    attachment coverage) is enforced by the existing adapters
    (7D/7J/7N), never re-implemented here. A rejected specification's
    error text is carried verbatim in the SPECIFICATION blocker.
  - READ-ONLY. The gate never mutates AIExtractedConnection,
    AIExtractedBolt, the supplement, corrections, or provenance
    records. It builds its own copies via 7W (which is itself
    side-effect free) and returns frozen results.
  - NO CAD, NO I/O. No database, no network, no Claude, no API route,
    no web UI, no CadQuery call. 7R's result is accepted only as
    caller-supplied EVIDENCE (`validation_passed`), derived by the
    caller from validate_reviewed_connection_assembly() succeeding;
    the gate itself never builds an assembly.

BLOCKERS are machine-readable AutomationFinding.code values, stable
strings a review UI can key on, each appearing at most once per
result: AUTOMATION_BLOCKER_REVIEW_STATUS, _MEMBER_IDENTITY,
_PROVENANCE, _POSITION, _PLATE, _HOLE_DIAMETER, _LOCATION,
_ATTACHMENT, _MALFORMED_FIELDS, _CONFLICT, _SPECIFICATION,
_VALIDATION (see AUTOMATION_BLOCKER_CODES).

SCOPE: this gate says how to ROUTE a candidate. It says nothing about
whether the connection is structurally adequate or code-compliant,
and passing it is never a claim that a connection is engineering-
approved or fabrication-ready.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from app.cad_engine.connection_review_package import (
    CATEGORY_MISSING,
    CATEGORY_NEEDS_CONFIRMATION,
    ENGINEERING_FIELDS,
    ConnectionReviewPackage,
    build_review_report,
    build_reviewed_connection_specification,
)
from app.cad_engine.project_connection_review import ConnectionCandidate
from app.cad_engine.reviewed_connection_specification import (
    REVIEW_STATUS_APPROVED,
    ReviewedConnectionSpecification,
)

__all__ = [
    "AUTOMATION_DECISION_AUTO", "AUTOMATION_DECISION_CONFIRM", "AUTOMATION_DECISION_REVIEW",
    "AUTOMATION_DECISIONS",
    "AUTOMATION_BLOCKER_REVIEW_STATUS", "AUTOMATION_BLOCKER_MEMBER_IDENTITY",
    "AUTOMATION_BLOCKER_PROVENANCE", "AUTOMATION_BLOCKER_POSITION", "AUTOMATION_BLOCKER_PLATE",
    "AUTOMATION_BLOCKER_HOLE_DIAMETER", "AUTOMATION_BLOCKER_LOCATION",
    "AUTOMATION_BLOCKER_ATTACHMENT", "AUTOMATION_BLOCKER_MALFORMED_FIELDS",
    "AUTOMATION_BLOCKER_CONFLICT", "AUTOMATION_BLOCKER_SPECIFICATION",
    "AUTOMATION_BLOCKER_VALIDATION", "AUTOMATION_BLOCKER_CODES",
    "AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED", "AUTOMATION_REASON_CONFIRMATION_REQUESTED",
    "AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED", "AUTOMATION_REASON_CONFIRMABLE",
    "AUTOMATION_REASON_BLOCKED",
    "AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE", "AUTOMATION_WARNING_HISTORICAL_AI_MARKS",
    "AUTOMATION_WARNING_CONFIRMATION_DEFERRED",
    "AUTOMATION_GATE_SCOPE_STATEMENT",
    "AutomationFinding", "AutomationGateResult",
    "evaluate_automation_gate", "evaluate_candidate_automation",
]

AUTOMATION_DECISION_AUTO = "AUTO"
AUTOMATION_DECISION_CONFIRM = "CONFIRM"
AUTOMATION_DECISION_REVIEW = "REVIEW"
AUTOMATION_DECISIONS = (AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_REVIEW)

# Blocking findings, in the fixed order they are reported. Stable codes a review UI can key on.
AUTOMATION_BLOCKER_REVIEW_STATUS = "AUTOMATION_BLOCKER_REVIEW_STATUS"
AUTOMATION_BLOCKER_MEMBER_IDENTITY = "AUTOMATION_BLOCKER_MEMBER_IDENTITY"
AUTOMATION_BLOCKER_PROVENANCE = "AUTOMATION_BLOCKER_PROVENANCE"
AUTOMATION_BLOCKER_POSITION = "AUTOMATION_BLOCKER_POSITION"
AUTOMATION_BLOCKER_PLATE = "AUTOMATION_BLOCKER_PLATE"
AUTOMATION_BLOCKER_HOLE_DIAMETER = "AUTOMATION_BLOCKER_HOLE_DIAMETER"
AUTOMATION_BLOCKER_LOCATION = "AUTOMATION_BLOCKER_LOCATION"
AUTOMATION_BLOCKER_ATTACHMENT = "AUTOMATION_BLOCKER_ATTACHMENT"
AUTOMATION_BLOCKER_MALFORMED_FIELDS = "AUTOMATION_BLOCKER_MALFORMED_FIELDS"
AUTOMATION_BLOCKER_CONFLICT = "AUTOMATION_BLOCKER_CONFLICT"
AUTOMATION_BLOCKER_SPECIFICATION = "AUTOMATION_BLOCKER_SPECIFICATION"
AUTOMATION_BLOCKER_VALIDATION = "AUTOMATION_BLOCKER_VALIDATION"
AUTOMATION_BLOCKER_CODES = (
    AUTOMATION_BLOCKER_REVIEW_STATUS, AUTOMATION_BLOCKER_MEMBER_IDENTITY, AUTOMATION_BLOCKER_PROVENANCE,
    AUTOMATION_BLOCKER_POSITION, AUTOMATION_BLOCKER_PLATE, AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION, AUTOMATION_BLOCKER_ATTACHMENT, AUTOMATION_BLOCKER_MALFORMED_FIELDS,
    AUTOMATION_BLOCKER_CONFLICT, AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION,
)

# Non-blocking reason/warning codes.
AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED = "AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED"
AUTOMATION_REASON_CONFIRMATION_REQUESTED = "AUTOMATION_REASON_CONFIRMATION_REQUESTED"
AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED = "AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED"
AUTOMATION_REASON_CONFIRMABLE = "AUTOMATION_REASON_CONFIRMABLE"
AUTOMATION_REASON_BLOCKED = "AUTOMATION_REASON_BLOCKED"
AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE = "AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE"
AUTOMATION_WARNING_HISTORICAL_AI_MARKS = "AUTOMATION_WARNING_HISTORICAL_AI_MARKS"
AUTOMATION_WARNING_CONFIRMATION_DEFERRED = "AUTOMATION_WARNING_CONFIRMATION_DEFERRED"

AUTOMATION_GATE_SCOPE_STATEMENT = (
    "AUTO is an automation-readiness decision: the reviewed information is complete, human-owned and "
    "internally consistent, and downstream validation has passed. It is NOT engineering approval, "
    "structural adequacy, code compliance or fabrication readiness. CONFIRM holds an otherwise-complete "
    "candidate for explicit human confirmation. REVIEW means human work is required: missing, ambiguous, "
    "unconfirmed or invalid information, or failed downstream validation."
)

# 7W issue codes recording an incoherent reviewer decision — each blocks automation until resolved.
_CONFLICT_ISSUE_CODES = frozenset({"CONFIRM_AND_SUPPLY_CONFLICT", "UNKNOWN_CONFIRMED_FIELD"})

# Which engineering field each missing-field blocker reports on.
_FIELD_BLOCKER = {
    "position": AUTOMATION_BLOCKER_POSITION,
    "plate": AUTOMATION_BLOCKER_PLATE,
    "holes": AUTOMATION_BLOCKER_HOLE_DIAMETER,
    "location": AUTOMATION_BLOCKER_LOCATION,
    "attachments": AUTOMATION_BLOCKER_ATTACHMENT,
}


@dataclass(frozen=True)
class AutomationFinding:
    """One machine-readable finding: a stable `code` plus a readable `message`."""
    code: str
    message: str


@dataclass(frozen=True)
class AutomationGateResult:
    """
    The gate's complete answer for one candidate. `decision` is exactly
    one of AUTOMATION_DECISIONS. `blockers` is empty unless the
    decision is REVIEW; `reasons` explains the decision; `warnings`
    are non-blocking notes (AI confidence, historical AI marks,
    deferred confirmation requests); `evidence_summary` is a fixed
    set of human-readable lines describing exactly what the decision
    was based on. Nothing here is or contains a score.
    """
    decision: str
    blockers: tuple[AutomationFinding, ...]
    reasons: tuple[AutomationFinding, ...]
    warnings: tuple[AutomationFinding, ...]
    evidence_summary: tuple[str, ...]


def _member_identity_blocker(
    spec: ReviewedConnectionSpecification, package: ConnectionReviewPackage, unknown: tuple,
) -> str | None:
    if unknown:
        return (
            f"Connected member mark(s) {list(unknown)} match no known project member — member identity is "
            "unresolved. The marks are never reinterpreted, and a non-steel reference is never assumed to be a "
            "structural steel member mark."
        )
    if not spec.connected_member_marks:
        return (
            "No connected member marks are available (neither AI-extracted nor reviewer-supplied); "
            "member identity is unresolved."
        )
    if package.known_member_marks is None:
        return (
            f"No known_member_marks were supplied, so connected member marks {spec.connected_member_marks!r} "
            "cannot be verified against the project — member identity is unresolved."
        )
    return None


def _missing_field_message(field: str, extraction) -> str:
    if field == "position":
        return (
            "No reviewed connection position (START/END) is available. Position is never inferred from list "
            "order, page context or member geometry."
        )
    if field == "plate":
        message = (
            "No reviewed plate is available. The pipeline consumes exactly one end plate with explicit "
            "positive finite dimensions; a typical plate dimension is never substituted."
        )
        if len(extraction.plates) > 1:
            message += (
                f" The AI extracted {len(extraction.plates)} plates and selected none of them by order — "
                "the reviewer must supply the plate."
            )
        return message
    if field == "holes":
        message = (
            "No reviewed hole geometry is available: an explicit numeric hole-void diameter (diameter_mm) "
            "with quantity and spacing is required."
        )
        sizes = [b.size for b in extraction.bolts if b.size is not None]
        if sizes:
            message += (
                f" The AI reported nominal bolt size(s) {sizes}; a nominal bolt size is not a hole diameter "
                "and was never converted into one."
            )
        return message
    if field == "location":
        return (
            "No reviewed project-space location (x/y/z with rotations) is available. Coordinates are never "
            "derived from member placements, plate dimensions or page position."
        )
    return (
        "No reviewed attachment(s) are available. Every connected member needs exactly one explicit "
        "{member_mark, surface_reference} attachment, and a surface reference is never defaulted to END or START."
    )


def evaluate_automation_gate(
    package: ConnectionReviewPackage,
    *,
    validation_passed: bool | None = None,
    require_confirmation: Sequence[str] = (),
) -> AutomationGateResult:
    """
    The automation-readiness decision for one 7W review package, built
    entirely from the existing 7W report (which runs 7V's own
    completeness check plus the project-member gate) — nothing is
    re-derived or re-implemented here.

    `validation_passed` is caller-supplied EVIDENCE: True only when the
    caller has built the reviewed assembly (7O) and
    validate_reviewed_connection_assembly() (7R) returned without
    raising. The gate never builds CAD itself. None means "no evidence
    yet" — an otherwise-complete candidate is then CONFIRM, never AUTO.
    False always forces REVIEW (AUTOMATION_BLOCKER_VALIDATION), and
    True can never rescue a candidate whose own blockers fail.

    `require_confirmation` lists explicit human confirmation items; a
    blocker-free candidate with items is CONFIRM, never AUTO.

    Deterministic and side-effect free: never mutates `package` or
    anything it references; two calls with the same inputs return
    equal results.
    """
    if validation_passed is not None and not isinstance(validation_passed, bool):
        raise TypeError(
            f"validation_passed must be None or a bool (got {validation_passed!r}); it is evidence "
            "supplied by the caller, never coerced."
        )
    confirmation_items = tuple(require_confirmation)

    report = build_review_report(package)
    spec = build_reviewed_connection_specification(package)
    extraction = package.extraction

    blockers: list[AutomationFinding] = []
    warnings: list[AutomationFinding] = []
    missing = {e.field for e in report.entries if e.category == CATEGORY_MISSING}

    # 1. Review workflow status — "approved" is the one status 7V accepts.
    if spec.review_status != REVIEW_STATUS_APPROVED:
        blockers.append(AutomationFinding(
            AUTOMATION_BLOCKER_REVIEW_STATUS,
            f"review_status is {spec.review_status!r}; automation requires the reviewer's approved workflow "
            f"status ({REVIEW_STATUS_APPROVED!r}). Information that has not completed human review cannot "
            "drive automation.",
        ))

    # 2. Member identity — unresolved marks, no marks, or no project context to check against.
    member_blocker = _member_identity_blocker(spec, package, report.current_unknown_member_marks)
    if member_blocker is not None:
        blockers.append(AutomationFinding(AUTOMATION_BLOCKER_MEMBER_IDENTITY, member_blocker))

    # 3. Provenance — an AI value that no reviewer confirmed or replaced still blocks (7V's own rule).
    needs_confirmation = tuple(e.field for e in report.entries if e.category == CATEGORY_NEEDS_CONFIRMATION)
    if needs_confirmation:
        blockers.append(AutomationFinding(
            AUTOMATION_BLOCKER_PROVENANCE,
            f"Field(s) {list(needs_confirmation)} still carry AI-extracted values that no reviewer has "
            "explicitly confirmed. AI-extracted engineering information cannot drive automation: each such "
            "field must be HUMAN_REVIEWED (explicitly confirmed) or HUMAN_SUPPLEMENTED (reviewer-supplied).",
        ))

    # 4. Missing engineering fields — one blocker per missing field, in ENGINEERING_FIELDS order.
    for field in ENGINEERING_FIELDS:
        code = _FIELD_BLOCKER.get(field)
        if code is not None and field in missing:
            blockers.append(AutomationFinding(code, _missing_field_message(field, extraction)))

    # 5. Malformed AI fields — the raw values are preserved verbatim and never coerced into usable fields.
    if extraction.malformed_fields:
        blockers.append(AutomationFinding(
            AUTOMATION_BLOCKER_MALFORMED_FIELDS,
            f"The preserved AI extraction contains malformed field(s) {sorted(extraction.malformed_fields)}. "
            "The raw values were never coerced, and automation must not proceed on unparseable evidence.",
        ))

    # 6. Conflicting reviewer decisions.
    conflict_issues = [i for i in report.issues if i.code in _CONFLICT_ISSUE_CODES]
    if conflict_issues:
        blockers.append(AutomationFinding(
            AUTOMATION_BLOCKER_CONFLICT,
            "The reviewer's decisions conflict and cannot be applied coherently: "
            + "; ".join(f"{i.field}: {i.message}" for i in conflict_issues),
        ))

    # 7. The existing 7V completeness gate (adapters 7D/7J/7N included) — its error carried verbatim.
    if not report.completeness.is_complete:
        blockers.append(AutomationFinding(
            AUTOMATION_BLOCKER_SPECIFICATION,
            "The existing 7V completeness gate does not accept the reviewed specification: "
            + (report.completeness.error or "(no error recorded)"),
        ))

    # 8. Downstream validation evidence.
    if validation_passed is False:
        blockers.append(AutomationFinding(
            AUTOMATION_BLOCKER_VALIDATION,
            "The downstream validation gate (7R — geometric consistency of the reviewed assembly) did not "
            "pass. Automated progression requires validated geometry.",
        ))

    # Warnings — never blocking.
    if extraction.confidence is not None:
        warnings.append(AutomationFinding(
            AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE,
            f"AI confidence {extraction.confidence!r} describes how well the vision model read the page. "
            "It is not evidence of engineering approval and never counts toward automation readiness.",
        ))
    if report.unknown_member_marks and not report.current_unknown_member_marks:
        warnings.append(AutomationFinding(
            AUTOMATION_WARNING_HISTORICAL_AI_MARKS,
            f"The AI extracted member reference(s) {list(report.unknown_member_marks)} that match no known "
            "project member; the reviewer supplied a different member set. Historical audit note only — "
            "this does not block automation.",
        ))
    if confirmation_items and blockers:
        warnings.append(AutomationFinding(
            AUTOMATION_WARNING_CONFIRMATION_DEFERRED,
            f"{len(confirmation_items)} explicit confirmation item(s) were requested but automation is "
            "blocked; they will be reconsidered once the blockers are resolved.",
        ))

    # Decision — blockers always win; otherwise confirmation items and missing validation
    # evidence each hold the candidate at CONFIRM; only a fully evidenced candidate is AUTO.
    if blockers:
        decision = AUTOMATION_DECISION_REVIEW
        reasons = tuple(blockers) + (AutomationFinding(
            AUTOMATION_REASON_BLOCKED,
            f"{len(blockers)} finding(s) block automation; human review is required before this candidate "
            "can be reconsidered. REVIEW is chosen whenever any required information is missing, "
            "ambiguous, unconfirmed or invalid.",
        ),)
    else:
        confirm_reasons: list[AutomationFinding] = []
        if confirmation_items:
            confirm_reasons.append(AutomationFinding(
                AUTOMATION_REASON_CONFIRMATION_REQUESTED,
                "Explicit human confirmation was requested: " + "; ".join(confirmation_items),
            ))
        if validation_passed is None:
            confirm_reasons.append(AutomationFinding(
                AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED,
                "The downstream validation gate (7R) has not yet been run against a built assembly; "
                "explicit confirmation is required before automated progression.",
            ))
        if confirm_reasons:
            decision = AUTOMATION_DECISION_CONFIRM
            reasons = tuple(confirm_reasons) + (AutomationFinding(
                AUTOMATION_REASON_CONFIRMABLE,
                "Every automation requirement on the reviewed information itself is satisfied; only "
                "explicit human confirmation remains.",
            ),)
        else:
            decision = AUTOMATION_DECISION_AUTO
            reasons = (AutomationFinding(
                AUTOMATION_REASON_ALL_REQUIREMENTS_SATISFIED,
                "Every automation requirement is satisfied: approved review status, human-owned provenance "
                "on all six engineering fields, known member identity, complete and valid geometry fields, "
                "and passing downstream validation. AUTO is an automation-readiness decision only — it is "
                "NOT engineering approval.",
            ),)

    evidence = [
        f"review_status: {spec.review_status!r} (automation requires 'approved')",
        f"known member context: {'supplied' if package.known_member_marks is not None else 'not supplied'}",
        f"member marks: {list(spec.connected_member_marks)!r}",
        "provenance: " + (", ".join(
            f"{f}={spec.provenance[f]}" for f in ENGINEERING_FIELDS if f in spec.provenance
        ) or "none recorded"),
        (
            "7V completeness: passed"
            if report.completeness.is_complete
            else "7V completeness: failed — " + (report.completeness.error or "(no error recorded)")
        ),
        f"missing fields: {', '.join(sorted(missing)) or 'none'}",
        (
            "validation evidence: passed" if validation_passed is True
            else "validation evidence: failed" if validation_passed is False
            else "validation evidence: not supplied"
        ),
        f"7W review state: {report.state}",
    ]
    if extraction.confidence is not None:
        evidence.append(f"AI confidence: {extraction.confidence!r} (never evidence of engineering approval)")

    return AutomationGateResult(
        decision=decision,
        blockers=tuple(blockers),
        reasons=reasons,
        warnings=tuple(warnings),
        evidence_summary=tuple(evidence),
    )


def evaluate_candidate_automation(
    candidate: ConnectionCandidate,
    *,
    validation_passed: bool | None = None,
    require_confirmation: Sequence[str] = (),
) -> AutomationGateResult:
    """
    The same automation-readiness decision for one 7X queue candidate,
    evaluated on its 7W review package. See evaluate_automation_gate().
    """
    return evaluate_automation_gate(
        candidate.package, validation_passed=validation_passed, require_confirmation=require_confirmation,
    )
