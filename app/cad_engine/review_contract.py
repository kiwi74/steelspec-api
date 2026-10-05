"""
Milestone 7AK — UI-READY EXCEPTION REVIEW CONTRACT.

A read-only presentation layer ABOVE the existing workflow (7AJ) and
the existing exception-resolution contract. It answers one question
for a future user interface:

    "What does a human need to see and do to resolve this connection?"

Everything here is a projection of data the existing stages already
own: the workflow state's decisions/statuses/counts/revision, the
automation gate's blocker codes, the exception contract's resolution
tasks, the review package's evidence and field provenance, and the
AI extraction's values — verbatim. This module decides nothing,
infers nothing, computes no geometry and performs no engineering.

HARD RULES:

  - NO NEW ENGINEERING. Blocker codes stay the automation gate's own
    (this module only attaches deterministic human-readable titles,
    messages, severities and field/task hints to codes the existing
    system actually produced). Resolution tasks stay the exception
    contract's own (this module only presents them). A code or task
    type this module has no presentation entry for is shown verbatim
    with a generic message — never dropped, never replaced.
  - AI VALUES STAY EXACTLY AS EXTRACTED. Nominal bolt designations,
    marks, references and malformed values are exposed verbatim (as
    display strings); nothing is converted, interpreted, defaulted or
    "improved". Missing is missing: unavailable values are None.
  - EVIDENCE AND PROVENANCE ARE PASSED THROUGH. Source drawing, page,
    detail and grid references come only from the extraction record.
    Field provenance comes only from the existing review report's
    provenance vocabulary. Nothing is invented for display.
  - NO BYPASS. The available actions are REVIEW, RESOLVE (per
    unresolved connection) and REFRESH — the operations the existing
    workflow itself performs. There is no approve, no force, no
    generate, no mark-verified and no direct-decision action; the
    resolution path remains the existing workflow's own.
  - IMMUTABLE AND DETERMINISTIC. Every contract is a frozen dataclass
    of plain data (tuples and strings only — no internal object is
    exposed by reference); items follow the workflow's existing
    connection order; tasks follow the exception contract's own task
    order; repeated builds are equal.
  - PURE AND UI-NEUTRAL. No UI, no API, no database, no network, no
    AI, no environment variables, no filesystem access.
"""

from dataclasses import dataclass

from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_CONFLICT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_MALFORMED_FIELDS,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_PROVENANCE,
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE,
    AUTOMATION_WARNING_CONFIRMATION_DEFERRED,
    AUTOMATION_WARNING_HISTORICAL_AI_MARKS,
)
from app.cad_engine.exception_resolution import (
    STATUS_RESOLVED,
    TASK_COMPLETE_REVIEW,
    TASK_CONFIRM_AI_VALUES,
    TASK_CONFIRM_AUTOMATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_RESOLVE_CONFLICT,
    TASK_REVIEW_MALFORMED_FIELDS,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_MEMBER,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_SELECT_POSITION,
    ExceptionResolutionTask,
)
from app.cad_engine.project_connection_review import build_review_report
from app.cad_engine.project_workflow import (
    ProjectWorkflowState,
    UnknownProjectPackageError,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    REQUIRED_PROVENANCE_FIELDS,
)

__all__ = [
    "ACTION_REVIEW", "ACTION_RESOLVE", "ACTION_REFRESH", "ACTION_NAMES",
    "SEVERITY_BLOCKING", "SEVERITY_WARNING",
    "PROVENANCE_LABELS", "REVIEW_CONTRACT_SCOPE_STATEMENT",
    "ReviewBlockerInfo", "ReviewEvidenceInfo", "ReviewFieldProvenance",
    "ReviewFieldCitationInfo", "ReviewFieldStanding",
    "ENGINEERING_FIELDS", "CITATION_KINDS",
    "STANDING_UNCITED", "STANDING_DIRECT", "STANDING_DERIVED",
    "ReviewTaskInfo", "ConnectionReviewContract", "ProjectReviewContract",
    "build_connection_review_contract", "build_project_review_contract",
    "field_standings",
]

# --------------------------------------------------------------------------------------
# Action vocabulary — the operations the existing workflow itself performs. These are
# ACTIONS (things a UI asks the workflow to do), not decisions: "REVIEW" here means
# "open this connection for review"; the decision vocabulary stays the automation
# gate's own.
# --------------------------------------------------------------------------------------
ACTION_REVIEW = "REVIEW"
ACTION_RESOLVE = "RESOLVE"
ACTION_REFRESH = "REFRESH"
ACTION_NAMES = (ACTION_REVIEW, ACTION_RESOLVE, ACTION_REFRESH)

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"

# The existing provenance vocabulary, re-exported for UI consumption — no new labels.
PROVENANCE_LABELS = (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)

REVIEW_CONTRACT_SCOPE_STATEMENT = (
    "This contract is a read-only view for a future user interface: what a human needs to see "
    "and do for each connection. Decisions, tasks, provenance, evidence and statuses all come "
    "from the existing workflow; nothing here can change them."
)

# --------------------------------------------------------------------------------------
# Deterministic presentation tables. Keys are ONLY codes/task types the existing system
# produces; unknown keys fall back to a verbatim generic presentation, never dropped.
# --------------------------------------------------------------------------------------
_BLOCKER_PRESENTATION = {
    AUTOMATION_BLOCKER_REVIEW_STATUS: (
        "Confirm the review status",
        "This connection needs a human reviewer to complete its review.",
        None,
    ),
    AUTOMATION_BLOCKER_MEMBER_IDENTITY: (
        "Confirm the connected members",
        "SteelSpec found a possible connection but needs confirmation of which members connect.",
        "connected_member_marks",
    ),
    AUTOMATION_BLOCKER_PROVENANCE: (
        "Confirm the extracted values",
        "Values the AI extracted need a human to confirm them before they can be used.",
        None,
    ),
    AUTOMATION_BLOCKER_POSITION: (
        "Confirm the connection position",
        "The position of the connection on the members needs confirmation.",
        "position",
    ),
    AUTOMATION_BLOCKER_PLATE: (
        "Provide the plate details",
        "The connection needs explicit plate details before it can be fabricated.",
        "plate",
    ),
    AUTOMATION_BLOCKER_HOLE_DIAMETER: (
        "Confirm the hole details",
        "SteelSpec found bolt information but does not have an explicit hole diameter.",
        "holes",
    ),
    AUTOMATION_BLOCKER_LOCATION: (
        "Provide the connection location",
        "The connection needs an explicit location before it can be placed.",
        "location",
    ),
    AUTOMATION_BLOCKER_ATTACHMENT: (
        "Confirm the attachment surfaces",
        "Which member surfaces the connection attaches to needs confirmation.",
        "attachments",
    ),
    AUTOMATION_BLOCKER_MALFORMED_FIELDS: (
        "Review the malformed values",
        "Some extracted values could not be read in their expected shape and need a human look.",
        None,
    ),
    AUTOMATION_BLOCKER_CONFLICT: (
        "Resolve the conflicting values",
        "A value already supplied conflicts with what the AI extracted and needs a decision.",
        None,
    ),
    AUTOMATION_BLOCKER_SPECIFICATION: (
        "Review the specification",
        "The assembled connection specification needs a human reviewer's sign-off.",
        None,
    ),
    AUTOMATION_BLOCKER_VALIDATION: (
        "Review the validation",
        "The validation result for this connection needs a human reviewer's sign-off.",
        None,
    ),
}

_WARNING_PRESENTATION = {
    AUTOMATION_WARNING_CONFIDENCE_NOT_EVIDENCE: (
        "Confidence is not evidence",
        "The AI's confidence score is recorded for traceability but is not treated as evidence.",
    ),
    AUTOMATION_WARNING_HISTORICAL_AI_MARKS: (
        "Historical mark references",
        "Member marks that appeared in the extraction and were later replaced are recorded for traceability.",
    ),
    AUTOMATION_WARNING_CONFIRMATION_DEFERRED: (
        "Confirmation deferred",
        "A confirmation request was recorded as deferred.",
    ),
}

_TASK_TITLES = {
    TASK_COMPLETE_REVIEW: "Complete the review",
    TASK_SELECT_MEMBER: "Select the connected members",
    TASK_SELECT_POSITION: "Select the connection position",
    TASK_PROVIDE_PLATE: "Provide the plate details",
    TASK_PROVIDE_HOLE_DIAMETER: "Confirm the hole details",
    TASK_PROVIDE_LOCATION: "Provide the connection location",
    TASK_SELECT_ATTACHMENT: "Select the attachment surfaces",
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT: "Confirm members, position and attachments",
    TASK_CONFIRM_AI_VALUES: "Confirm the extracted values",
    TASK_REVIEW_MALFORMED_FIELDS: "Review the malformed values",
    TASK_RESOLVE_CONFLICT: "Resolve the conflicting values",
    TASK_REVIEW_SPECIFICATION: "Review the specification",
    TASK_REVIEW_VALIDATION: "Review the validation",
    TASK_CONFIRM_AUTOMATION: "Confirm automation",
    TASK_PROVIDE_CONNECTION_IDENTITY: "Provide the connection identity",
    TASK_PROVIDE_MATERIAL_SPECIFICATION: "Provide the material specification",
}

_TASK_FIELDS = {
    TASK_SELECT_MEMBER: "connected_member_marks",
    TASK_SELECT_POSITION: "position",
    TASK_PROVIDE_PLATE: "plate",
    TASK_PROVIDE_HOLE_DIAMETER: "holes",
    TASK_PROVIDE_LOCATION: "location",
    TASK_SELECT_ATTACHMENT: "attachments",
    TASK_PROVIDE_CONNECTION_IDENTITY: "connection_id",
    TASK_PROVIDE_MATERIAL_SPECIFICATION: "material",
    # Grouped/whole-connection tasks resolve several fields or none in particular.
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT: None,
    TASK_CONFIRM_AI_VALUES: None,
    TASK_REVIEW_MALFORMED_FIELDS: None,
    TASK_RESOLVE_CONFLICT: None,
    TASK_REVIEW_SPECIFICATION: None,
    TASK_REVIEW_VALIDATION: None,
    TASK_CONFIRM_AUTOMATION: None,
    TASK_COMPLETE_REVIEW: None,
}

_PROVENANCE_FIELD_ORDER = (
    "connected_member_marks", "position", "plate", "holes", "location", "attachments",
)

# --------------------------------------------------------------------------------------
# The citation vocabulary (J66).
#
# ENGINEERING_FIELDS is the fields a citation may address, and it is deliberately NOT a new
# list: it is the SAME expression the review package publishes
# (`app/cad_engine/connection_review_package.py`, `REQUIRED_PROVENANCE_FIELDS + ("material",)`),
# taken from the one authoritative list of provenance-required fields that this module already
# imports. The J66 tests pin this expression, that module's own constant and the SQL CHECK in
# the J66 migration `20260929010000_j66_field_evidence_citations.sql` all equal, so a field
# added to one of the three cannot silently disagree with the other two.
# --------------------------------------------------------------------------------------
ENGINEERING_FIELDS = REQUIRED_PROVENANCE_FIELDS + ("material",)

# How a citation relates to the reading it names. SOURCE is the reading a field's content was
# taken from; DERIVATION is a reading it was computed or interpreted from. The pair is CLOSED
# and carries no quality, no confidence, no rank and no precedence — which of two sources is
# better is a question this milestone deliberately did not answer.
CITATION_SOURCE = "SOURCE"
CITATION_DERIVATION = "DERIVATION"
CITATION_KINDS = (CITATION_SOURCE, CITATION_DERIVATION)

# A field's DERIVED citation standing. Computed from the citations and NEVER stored, so it
# cannot become a second fact that disagrees with the ones it is derived from. There is
# deliberately no INFERRED standing: this system records no inference, and a state naming one
# would claim a provenance nothing in the record supports.
STANDING_UNCITED = "UNCITED"
STANDING_DIRECT = "DIRECT"
STANDING_DERIVED = "DERIVED"


# --------------------------------------------------------------------------------------
# The immutable contract models.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReviewBlockerInfo:
    """
    One blocker (or warning) finding, presented for a human: the existing stable
    `code` verbatim, a deterministic human-readable `title` and `message`, a
    `severity` ("blocking" for blockers, "warning" for warnings), the engineering
    `field` it relates to (None when it spans fields or is a whole-connection
    check), and the existing `task_type` of this connection's actual resolution
    task that addresses the code (None when none does).
    """
    code: str
    title: str
    message: str
    severity: str
    field: str | None
    task_type: str | None


@dataclass(frozen=True)
class ReviewEvidenceInfo:
    """
    Where this connection came from, exactly as the extraction record carries it.
    Every value is None when the record has none — nothing is invented. Non-string
    detail values are exposed verbatim as display strings.
    """
    source_drawing_id: str | None
    drawing_number: str | None
    source_page: int | None
    detail_reference: str | None
    grid_reference: str | None


@dataclass(frozen=True)
class ReviewFieldProvenance:
    """One engineering field's provenance label, from the existing review report's
    provenance vocabulary (AI extracted / human reviewed / human supplemented)."""
    field: str
    provenance: str


@dataclass(frozen=True)
class ReviewFieldCitationInfo:
    """
    ONE recorded citation of one engineering field, presented for a human: WHERE that
    field's content is accounted for — never what the content is.

    There is no value, no chosen value, no confidence, no rank and no winner here, because a
    citation is an ADDRESS. The content stays in exactly one copy, in the immutable reading
    the citation names, and reading it is the reader's own act through the evidence surface.
    A copy here would be a second truth that could disagree with the first, undetectably.

    `document_id` is None when the cited reading predates document identity or was taken
    without it. None is NOT a wildcard: it matches nothing, and no reader may treat it as
    "any document".

    `annotation_x`, `annotation_y` and `extractor_version` are all present or all None. A
    position is never stated without the rule set it was read under, because the pair is
    what makes the occurrence an identity rather than a coordinate.

    `anchor` is the locator WITHIN the cited reading as a display string — the drawn token,
    the detail reference, the nearby text. It is an attribute of the address.

    `ordinal` is the citation's RECORDING ORDER within its field, and nothing else: it is
    never a priority, a preference or a rank, and it is compared with no other quantity.
    """

    field: str
    ordinal: int
    citation_kind: str
    document_id: str | None
    drawing_id: str
    page_number: int
    analysis_run_id: str
    annotation_x: str | None
    annotation_y: str | None
    extractor_version: str | None
    anchor: str


@dataclass(frozen=True)
class ReviewFieldStanding:
    """
    One engineering field's DERIVED citation standing: DIRECT when every citation of it is a
    SOURCE, DERIVED when any citation of it is a DERIVATION (a mix fails closed to the weaker
    standing), UNCITED when it has no citation at all.

    It is computed for EVERY engineering field, so a field that is uncited is stated rather
    than omitted, and it is never persisted — the citations remain the only stored fact.
    """

    field: str
    standing: str


@dataclass(frozen=True)
class ReviewTaskInfo:
    """
    One existing human-resolution task, presented for a UI control. `description`
    is the task's own human question, verbatim; `required` is True exactly when
    the task addresses at least one existing blocker code; `current_value` is the
    task's existing AI value verbatim as a display string (None when there is
    none); `allowed_options` is the task's own authoritative choices (empty when
    the answer is free — a UI must never guess a list); `field` is the
    engineering field the task resolves (None for whole-connection tasks);
    `resolved` reflects the task's existing status; `resolution_value` and
    `resolution_evidence` carry the recorded human answer verbatim as display
    strings when one exists; `answer_type` is the task's own authoritative
    answer payload shape (7AC's vocabulary, e.g. CONNECTION_IDENTITY,
    PLATE_VALUE) — a UI form renderer needs it to build a valid
    HumanResolution, so it is passed through verbatim and never interpreted.
    """
    task_id: str
    task_type: str
    title: str
    description: str
    required: bool
    current_value: str | None
    allowed_options: tuple[str, ...]
    evidence_requirement: str
    field: str | None
    resolved: bool
    resolution_value: str | None
    resolution_evidence: str | None
    answer_type: str


@dataclass(frozen=True)
class ConnectionReviewContract:
    """
    The UI-safe, immutable view of ONE connection at the workflow's current
    revision: its existing decision and output/verification statuses (None until
    processed), whether a human needs to act on it, its blocker and warning
    presentations, the AI's extracted values verbatim, the extraction evidence,
    the field provenance, the existing resolution tasks, the actions actually
    valid right now, the artifacts it produced, and — where any have been
    recorded — the citations of this connection's engineering fields with the
    standing each field's citations derive. No internal package, pipeline or
    gate object is exposed.
    """
    package_id: str
    connection_id: str | None
    project_id: str | None
    revision: int
    decision: str
    output_status: str | None
    verification_status: str | None
    requires_action: bool
    blockers: tuple[ReviewBlockerInfo, ...]
    warnings: tuple[ReviewBlockerInfo, ...]
    ai_member_references: tuple[str, ...]
    ai_bolt_readings: tuple[str, ...]
    ai_plate_readings: tuple[str, ...]
    ai_weld_readings: tuple[str, ...]
    ai_malformed_readings: tuple[str, ...]
    ai_unrecognised_readings: tuple[str, ...]
    ai_connection_type: str | None
    ai_confidence: str | None
    ai_material: str | None
    evidence: ReviewEvidenceInfo
    provenance: tuple[ReviewFieldProvenance, ...]
    tasks: tuple[ReviewTaskInfo, ...]
    available_actions: tuple[str, ...]
    generated_files: tuple[str, ...]
    last_processed_revision: int | None
    summary: str
    # J66, both defaulted so every existing construction of this contract is unchanged: an
    # empty tuple is the honest statement for a revision whose citations were never recorded
    # (and for one read by a caller that does not load them), and `field_standings` is
    # derived for every engineering field whatever the citations say.
    field_citations: tuple[ReviewFieldCitationInfo, ...] = ()
    field_standings: tuple[ReviewFieldStanding, ...] = ()


@dataclass(frozen=True)
class ProjectReviewContract:
    """
    The UI-safe, immutable view of the whole project at the workflow's current
    revision: the existing project decision as `project_status`, the workflow's
    own counts (review items, verified, automatic, confirmation, blocked), the
    ordered connection contracts, the project-level actions actually valid right
    now, and a factual summary. Item order is the workflow's existing connection
    order — deterministic, never incidental.
    """
    project_id: str | None
    revision: int
    project_status: str
    review_count: int
    verified_count: int
    auto_count: int
    confirmation_count: int
    blocked_count: int
    items: tuple[ConnectionReviewContract, ...]
    available_actions: tuple[str, ...]
    summary: str


# --------------------------------------------------------------------------------------
# Presentation helpers — pure functions over the existing recorded data.
# --------------------------------------------------------------------------------------
def _display(value) -> str | None:
    """A value as a verbatim display string: None stays None, strings stay themselves,
    everything else uses its representation. Never interpreted."""
    if value is None:
        return None
    return value if isinstance(value, str) else repr(value)


def _task_type_for_code(tasks, code) -> str | None:
    """The task type of this connection's own task addressing `code`, if any."""
    for task in tasks:
        if code in task.blocker_codes:
            return task.task_type
    return None


def _finding_info(code, severity, tasks) -> ReviewBlockerInfo:
    table = _BLOCKER_PRESENTATION if severity == SEVERITY_BLOCKING else _WARNING_PRESENTATION
    entry = table.get(code)
    if entry is None:
        # A code the presentation has no entry for: shown verbatim, never dropped.
        title, message, field = code, "This item needs human attention.", None
    elif severity == SEVERITY_BLOCKING:
        title, message, field = entry
    else:
        title, message, field = entry[0], entry[1], None
    return ReviewBlockerInfo(
        code=code,
        title=title,
        message=message,
        severity=severity,
        field=field,
        task_type=_task_type_for_code(tasks, code) if severity == SEVERITY_BLOCKING else None,
    )


def _task_info(task: ExceptionResolutionTask) -> ReviewTaskInfo:
    resolved = task.status == STATUS_RESOLVED
    resolution = task.resolution
    return ReviewTaskInfo(
        task_id=task.task_id,
        task_type=task.task_type,
        title=_TASK_TITLES.get(task.task_type, task.task_type),
        description=task.question,
        required=bool(task.blocker_codes),
        current_value=_display(task.current_ai_value),
        allowed_options=task.allowed_choices,
        evidence_requirement=task.evidence_requirement,
        field=_TASK_FIELDS.get(task.task_type),
        resolved=resolved,
        resolution_value=None if resolution is None else repr(resolution.answer),
        resolution_evidence=None if resolution is None else resolution.evidence,
        answer_type=task.answer_type,
    )


def _provenance_infos(provenance: dict[str, str]) -> tuple[ReviewFieldProvenance, ...]:
    """Field provenance in the fixed field order first, then any remaining fields sorted."""
    ordered = tuple(
        ReviewFieldProvenance(field_name, provenance[field_name])
        for field_name in _PROVENANCE_FIELD_ORDER if field_name in provenance
    )
    rest = tuple(
        ReviewFieldProvenance(field_name, provenance[field_name])
        for field_name in sorted(name for name in provenance if name not in _PROVENANCE_FIELD_ORDER)
    )
    return ordered + rest


def _citation_infos(citations) -> tuple[ReviewFieldCitationInfo, ...]:
    """
    The citations of ONE connection, in the order the contract presents them: the fixed field
    order first, then any remaining fields sorted, and by ORDINAL within each field — the same
    ordering rule the provenance labels already follow.

    The input is whatever the read path decoded, in whatever order it arrived. The sort is
    total, so two builds over the same recorded rows produce the same sequence; a stable
    presentation is what lets a UI diff two revisions without reordering noise.

    Ordinal orders the citations OF ONE FIELD and is compared with nothing else. It is a
    RECORDING ORDER, not a priority: the contract makes no claim that the first citation of a
    field is better than the second, and no reader may infer one.
    """
    by_field: dict[str, list] = {}
    for citation in citations:
        by_field.setdefault(citation.field_name, []).append(citation)
    ordered: list = []
    for field_name in _PROVENANCE_FIELD_ORDER:
        ordered.extend(sorted(by_field.pop(field_name, ()), key=lambda entry: entry.ordinal))
    for field_name in sorted(by_field):
        ordered.extend(sorted(by_field.pop(field_name), key=lambda entry: entry.ordinal))
    return tuple(
        ReviewFieldCitationInfo(
            field=citation.field_name,
            ordinal=citation.ordinal,
            citation_kind=citation.citation_kind,
            document_id=citation.document_id,
            drawing_id=citation.drawing_id,
            page_number=citation.page_number,
            analysis_run_id=citation.analysis_run_id,
            annotation_x=_display(citation.annotation_x),
            annotation_y=_display(citation.annotation_y),
            extractor_version=citation.extractor_version,
            anchor=_display(citation.anchor),
        )
        for citation in ordered
    )


def field_standings(citations) -> tuple[ReviewFieldStanding, ...]:
    """
    EVERY engineering field's derived citation standing, in the field vocabulary's own order,
    so a field with no citations is stated as UNCITED rather than omitted.

    The rule, and it fails closed in both directions:

      * a field whose citations include ANY derivation is DERIVED, whatever else it has — a
        mixed SOURCE/DERIVATION field takes the WEAKER standing, so a derivation can never be
        presented as though the content were taken directly from a reading;
      * a field whose citations are all SOURCE is DIRECT;
      * a field with no citations is UNCITED;
      * a citation kind this module does not recognise is treated as a derivation rather than
        skipped, so an unknown kind can never make a field look more directly sourced than it
        is. (The citation table's CHECK refuses an unknown kind outright; this is the second
        line, not the first.)

    A citation of a field OUTSIDE the vocabulary is refused rather than ignored: silently
    dropping it would compute a standing over a record this module cannot read, and the
    standing would then be a claim about evidence it never saw.
    """
    kinds: dict[str, list[str]] = {}
    for citation in citations:
        if citation.field_name not in ENGINEERING_FIELDS:
            raise ValueError(
                f"{citation.field_name!r} is not an engineering field of this contract "
                f"(fields: {list(ENGINEERING_FIELDS)}); a standing is never computed over a "
                "citation this module cannot read."
            )
        kinds.setdefault(citation.field_name, []).append(citation.citation_kind)
    return tuple(
        ReviewFieldStanding(field_name, _standing_of(kinds.get(field_name, ())))
        for field_name in ENGINEERING_FIELDS
    )


def _standing_of(kinds) -> str:
    """One field's standing from its citation kinds. See `field_standings` for the rule."""
    if not kinds:
        return STANDING_UNCITED
    if any(kind != CITATION_SOURCE for kind in kinds):
        return STANDING_DERIVED
    return STANDING_DIRECT


# --------------------------------------------------------------------------------------
# The two build functions.
# --------------------------------------------------------------------------------------
def build_connection_review_contract(workflow, package_id) -> ConnectionReviewContract:
    """
    Builds the read-only review contract for ONE connection from an existing
    `ProjectWorkflowState` (any revision): decisions/statuses/counts from the
    workflow state, blocker codes from the existing gate findings the state
    carries, tasks from the existing exception contract, evidence and AI values
    from the connection's extraction record, provenance from the existing review
    report (the rebuilt package once the connection was processed, the original
    package otherwise). Reads only; never mutates, never decides, never generates.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__}); the "
            "review contract projects the existing workflow state."
        )
    if not isinstance(package_id, str):
        raise TypeError("package_id must be a str.")

    index = next(
        (i for i, connection in enumerate(workflow.connections)
         if connection.package_id == package_id),
        None,
    )
    if index is None:
        raise UnknownProjectPackageError(
            f"{package_id!r} is not a connection of this project (connections: "
            f"{[c.package_id for c in workflow.connections]})."
        )
    collection = workflow.collection
    if collection is None:
        raise ValueError(
            "this workflow state carries no review collection; only states built by "
            "start_project_workflow support review contracts."
        )
    exception_package = workflow.exception_package
    if exception_package is None:
        raise ValueError(
            "this workflow state carries no exception-resolution contract; only states built "
            "by start_project_workflow support review contracts."
        )

    state = workflow.connections[index]
    record = workflow.connection_records[index]
    candidate = next(
        candidate for candidate in collection.candidates
        if candidate.review_package_id == package_id
    )
    group = next(
        group for group in exception_package.connection_tasks
        if group.review_package_id == package_id
    )
    package = record.rerun_outcome.rebuilt_package if record.rerun_outcome is not None else candidate.package
    extraction = package.extraction
    report = build_review_report(package)

    blockers = tuple(
        _finding_info(code, SEVERITY_BLOCKING, group.tasks) for code in state.blockers
    )
    warnings = tuple(
        _finding_info(code, SEVERITY_WARNING, group.tasks) for code in state.warnings
    )
    tasks = tuple(_task_info(task) for task in group.tasks)

    requires_action = (
        state.decision == AUTOMATION_DECISION_REVIEW
        and state.last_processed_revision is None
    )

    evidence = ReviewEvidenceInfo(
        source_drawing_id=extraction.source_drawing_id,
        drawing_number=extraction.drawing_number,
        source_page=extraction.source_page,
        detail_reference=_display(extraction.detail_reference),
        grid_reference=_display(extraction.grid_reference),
    )

    parts = [f"{package_id}: decision {state.decision}"]
    if state.output_status is not None:
        parts.append(f"output {state.output_status}")
    if state.verification_status is not None:
        parts.append(f"verification {state.verification_status}")
    parts.append("action required" if requires_action else "no action required")
    if blockers:
        parts.append(f"{len(blockers)} blocker(s): " + ", ".join(b.code for b in blockers))
    if tasks:
        parts.append(f"{len(tasks)} task(s); {sum(1 for t in tasks if t.resolved)} resolved")

    return ConnectionReviewContract(
        package_id=state.package_id,
        connection_id=state.connection_id,
        project_id=workflow.project_id,
        revision=workflow.revision,
        decision=state.decision,
        output_status=state.output_status,
        verification_status=state.verification_status,
        requires_action=requires_action,
        blockers=blockers,
        warnings=warnings,
        ai_member_references=extraction.connected_member_references,
        ai_bolt_readings=tuple(repr(bolt) for bolt in extraction.bolts),
        ai_plate_readings=tuple(repr(plate) for plate in extraction.plates),
        ai_weld_readings=tuple(repr(weld) for weld in extraction.welds),
        ai_malformed_readings=tuple(
            f"{name}: {extraction.malformed_fields[name]!r}"
            for name in sorted(extraction.malformed_fields)
        ),
        ai_unrecognised_readings=tuple(
            f"{name}: {extraction.unrecognised_fields[name]!r}"
            for name in sorted(extraction.unrecognised_fields)
        ),
        ai_connection_type=_display(extraction.generic_connection_type),
        ai_confidence=_display(extraction.confidence),
        ai_material=extraction.material,  # the designation VERBATIM (a str), never a display transform
        evidence=evidence,
        provenance=_provenance_infos(report.provenance),
        tasks=tasks,
        available_actions=(
            (ACTION_REVIEW, ACTION_RESOLVE) if requires_action else ()
        ),
        generated_files=state.generated_files,
        last_processed_revision=state.last_processed_revision,
        summary="; ".join(parts),
        # J66. The LIVE band has no citations and can have none: a citation is a PERSISTED
        # fact, recorded against a stored revision, and nothing in this process invents one.
        # So every field's standing here is UNCITED — which is exactly what the recorded band
        # says for a revision that recorded no citations, so the two bands agree by
        # construction rather than by convention.
        field_citations=(),
        field_standings=field_standings(()),
    )


def build_project_review_contract(workflow) -> ProjectReviewContract:
    """
    Builds the read-only project review contract from an existing
    `ProjectWorkflowState` (any revision). Items are the per-connection contracts
    in the workflow's existing connection order; counts and `project_status` are
    the workflow's own; the available actions are REVIEW (when at least one
    connection requires action) and REFRESH (always) — both operations the
    existing workflow itself performs. Reads only.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__}); the "
            "project review contract projects the existing workflow state."
        )

    items = tuple(
        build_connection_review_contract(workflow, connection.package_id)
        for connection in workflow.connections
    )
    awaiting = tuple(item for item in items if item.requires_action)
    actions = (ACTION_REVIEW, ACTION_REFRESH) if awaiting else (ACTION_REFRESH,)

    lines = [
        f"project = {workflow.project_id or 'unknown'}; revision = {workflow.revision}; "
        f"status = {workflow.project_decision}",
        f"review = {workflow.review_count}; verified = {workflow.verified_count}; "
        f"auto = {workflow.auto_count}; confirmation = {workflow.confirm_count}; "
        f"blocked = {workflow.blocked_count}",
    ]
    for item in items:
        lines.append(
            f"{item.package_id}: {item.decision}"
            + (" — action required" if item.requires_action else "")
        )
    lines.append(REVIEW_CONTRACT_SCOPE_STATEMENT)

    return ProjectReviewContract(
        project_id=workflow.project_id,
        revision=workflow.revision,
        project_status=workflow.project_decision,
        review_count=workflow.review_count,
        verified_count=workflow.verified_count,
        auto_count=workflow.auto_count,
        confirmation_count=workflow.confirm_count,
        blocked_count=workflow.blocked_count,
        items=items,
        available_actions=actions,
        summary="\n".join(lines),
    )
