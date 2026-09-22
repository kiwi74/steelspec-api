"""
Milestone 7B5 — DETERMINISTIC REVIEWER WORKLOAD BASELINE (read-only proof).

A pure analysis layer over the existing review contract (7AK). It answers
exactly one question:

    "Where can SteelSpec's human reviewer workload potentially be reduced —
    and where must the human remain the decision-maker?"

It answers that question as a frozen, deterministic CLASSIFICATION of the
existing workload only. It is NOT an automation layer:

  - NO ENGINEERING ANSWERS. Nothing here proposes a position, a plate, a
    hole diameter, a location, a material or any other engineering value.
    AI observations are carried VERBATIM and are never converted into
    answers ("22mm holes" never becomes a diameter; "300" never becomes
    a supplied material).
  - NO DECISIONS. The categories describe what kind of work a task is;
    they do not grant, withhold or change any decision. 7Z/7AC/7AD/7AJ
    remain the only decision-making layers.
  - NO EXTERNAL MODEL. No LLM, no network, no API. The result is
    reproducible from the existing SteelSpec state alone (the 7AK contract
    over the genuine 51-candidate Selby workload), so it can later serve
    as the ground truth a future assistant is evaluated against.
  - READ-ONLY. The input is the frozen 7AK project contract; the output is
    frozen plain data. Workflow state, revision, provenance, source
    identity and artifacts cannot change because nothing here can reach
    them.

THE FIVE CATEGORIES (workload classification only):

  ASSISTABLE_CANDIDATE        The task carries a bounded, already-observed
                              value (an existing AI observation) that could
                              be presented to the reviewer for confirmation
                              — never auto-accepted, never transformed.
  HUMAN_ENGINEERING_DECISION  The reviewer must decide something SteelSpec
                              possesses no authoritative answer for. Any
                              proposed value would be the system making the
                              reviewer's engineering decision.
  EVIDENCE_LOOKUP             No engineering judgement is required in
                              principle, but the value is not captured as a
                              trustworthy structured observation: the
                              reviewer must locate it in the source drawing
                              and enter it explicitly.
  ADMINISTRATIVE              Pure workflow/identity/review bookkeeping
                              that requires no engineering judgement.
  NOT_APPLICABLE              The task's own contract requires no
                              additional engineering/source evidence.

CLASSIFICATION RULES (deterministic, total over the 7AC task vocabulary):

  - SELECT_POSITION, SELECT_ATTACHMENT, PROVIDE_LOCATION, SELECT_MEMBER,
    SELECT_MEMBER_POSITION_ATTACHMENT, RESOLVE_CONFLICT are always
    HUMAN_ENGINEERING_DECISION: the workflow schema records no authoritative
    value for these fields and the task contract forbids inference,
    defaulting, derivation or reinterpretation.
  - CONFIRM_AI_VALUES is always ASSISTABLE_CANDIDATE: its whole content is
    the recorded AI values the reviewer confirms or replaces.
  - PROVIDE_PLATE and PROVIDE_HOLE_DIAMETER are ASSISTABLE_CANDIDATE when
    the contract shows an existing AI reading (plate reading / nominal bolt
    reading) beside the question, and EVIDENCE_LOOKUP when it shows none.
    The observation is shown; the value the reviewer enters is never
    derived from it.
  - COMPLETE_REVIEW, REVIEW_SPECIFICATION, REVIEW_VALIDATION and
    PROVIDE_CONNECTION_IDENTITY are ADMINISTRATIVE: their own wording
    states they grant nothing by themselves and identity is never AI output.
  - PROVIDE_MATERIAL_SPECIFICATION and REVIEW_MALFORMED_FIELDS are
    EVIDENCE_LOOKUP: the value must be located in an authoritative source
    and transcribed; the workflow records no trustworthy observation of it.
  - CONFIRM_AUTOMATION is NOT_APPLICABLE: a 7Z CONFIRM carries no
    engineering field question and requires no additional evidence.
  - Any task type outside the 7AC vocabulary is REFUSED (ValueError),
    never silently classified.
"""

from dataclasses import dataclass

from app.cad_engine.exception_resolution import (
    EXCEPTION_TASK_TYPES,
    TASK_COMPLETE_REVIEW,
    TASK_CONFIRM_AI_VALUES,
    TASK_CONFIRM_AUTOMATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
    TASK_PROVIDE_PLATE,
    TASK_RESOLVE_CONFLICT,
    TASK_REVIEW_MALFORMED_FIELDS,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_MEMBER,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_SELECT_POSITION,
)
from app.cad_engine.review_contract import ProjectReviewContract

__all__ = [
    "CATEGORY_ADMINISTRATIVE", "CATEGORY_ASSISTABLE", "CATEGORY_EVIDENCE_LOOKUP",
    "CATEGORY_HUMAN_ENGINEERING_DECISION", "CATEGORY_NOT_APPLICABLE",
    "CandidateWorkloadRecord", "CategoryCount", "BlockerCount", "HumanBoundary",
    "ProvenanceRef", "ReviewerWorkloadBaseline", "TaskTypeCount",
    "TaskWorkloadRecord", "build_reviewer_workload_baseline", "classify_task",
]

# --------------------------------------------------------------------------------------
# The five categories — workload descriptions only, never automation decisions.
# --------------------------------------------------------------------------------------
CATEGORY_ASSISTABLE = "ASSISTABLE_CANDIDATE"
CATEGORY_HUMAN_ENGINEERING_DECISION = "HUMAN_ENGINEERING_DECISION"
CATEGORY_EVIDENCE_LOOKUP = "EVIDENCE_LOOKUP"
CATEGORY_ADMINISTRATIVE = "ADMINISTRATIVE"
CATEGORY_NOT_APPLICABLE = "NOT_APPLICABLE"

CATEGORIES = (
    CATEGORY_ASSISTABLE,
    CATEGORY_HUMAN_ENGINEERING_DECISION,
    CATEGORY_EVIDENCE_LOOKUP,
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_NOT_APPLICABLE,
)

# --------------------------------------------------------------------------------------
# The deterministic classification table. Every 7AC task type maps to exactly one rule;
# anything else is refused. The 16 vocabulary types are covered exactly once below.
# --------------------------------------------------------------------------------------
_HUMAN_DECISION_TASK_TYPES = frozenset({
    TASK_SELECT_MEMBER,                      # AI references are never authoritative member marks
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,  # grouped member/position/attachment decision
    TASK_SELECT_POSITION,
    TASK_SELECT_ATTACHMENT,
    TASK_PROVIDE_LOCATION,
    TASK_RESOLVE_CONFLICT,                   # its own question begins "Decide:"
})

_ADMINISTRATIVE_TASK_TYPES = frozenset({
    TASK_COMPLETE_REVIEW,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
})

_EVIDENCE_LOOKUP_TASK_TYPES = frozenset({
    TASK_PROVIDE_MATERIAL_SPECIFICATION,  # locate the grade in project documentation, verbatim
    TASK_REVIEW_MALFORMED_FIELDS,         # "Review them against the drawing"
})

# These two types flip with the presence of an existing AI observation.
_OBSERVATION_ASSISTED_TASK_TYPES = frozenset({
    TASK_PROVIDE_PLATE,
    TASK_PROVIDE_HOLE_DIAMETER,
})

_NOT_APPLICABLE_TASK_TYPES = frozenset({TASK_CONFIRM_AUTOMATION})


def classify_task(task_type: str, current_value: str | None) -> str:
    """One existing task -> exactly one workload category, by the table above.

    `current_value` is the contract's own display string for the task's existing
    AI observation (None when there is none). It selects between the two
    observation-dependent rules only; it is never interpreted, converted or
    answered. Unknown task types are refused rather than silently classified.
    """
    if task_type not in EXCEPTION_TASK_TYPES:
        raise ValueError(
            f"task type {task_type!r} is not part of the exception-resolution contract; "
            "the workload baseline refuses to classify anything outside the 7AC vocabulary."
        )
    if task_type in _HUMAN_DECISION_TASK_TYPES:
        return CATEGORY_HUMAN_ENGINEERING_DECISION
    if task_type == TASK_CONFIRM_AI_VALUES:
        return CATEGORY_ASSISTABLE
    if task_type in _OBSERVATION_ASSISTED_TASK_TYPES:
        return CATEGORY_ASSISTABLE if current_value is not None else CATEGORY_EVIDENCE_LOOKUP
    if task_type in _ADMINISTRATIVE_TASK_TYPES:
        return CATEGORY_ADMINISTRATIVE
    if task_type in _EVIDENCE_LOOKUP_TASK_TYPES:
        return CATEGORY_EVIDENCE_LOOKUP
    if task_type in _NOT_APPLICABLE_TASK_TYPES:
        return CATEGORY_NOT_APPLICABLE
    # Unreachable while the table is total over EXCEPTION_TASK_TYPES; fail loudly anyway.
    raise ValueError(f"task type {task_type!r} has no classification rule.")


# --------------------------------------------------------------------------------------
# The fixed proposal texts — one per category (the two observation-assisted types share
# their category's text). None of them contains or transforms a value; the AI observation
# itself is carried verbatim in `TaskWorkloadRecord.current_value`, never elsewhere.
# --------------------------------------------------------------------------------------
_PROPOSAL_BY_CATEGORY = {
    CATEGORY_ASSISTABLE: (
        "Show the recorded AI observation verbatim beside the question so the reviewer can "
        "compare it with the source; the system proposes no engineering value and performs "
        "no conversion of the observation."
    ),
    CATEGORY_HUMAN_ENGINEERING_DECISION: (
        "No safe proposal exists: the workflow records no authoritative value for this field, "
        "and any value a system proposed here would be the system making the reviewer's "
        "engineering decision."
    ),
    CATEGORY_EVIDENCE_LOOKUP: (
        "No safe proposal exists: the required value is not captured as a trustworthy "
        "structured observation. The reviewer must locate the value in the source drawing "
        "and enter it explicitly."
    ),
    CATEGORY_ADMINISTRATIVE: (
        "None required: the task is workflow/identity bookkeeping; the reviewer acknowledges "
        "it, and the task itself grants nothing."
    ),
    CATEGORY_NOT_APPLICABLE: (
        "None required: the task carries no engineering field question and its contract "
        "requires no additional evidence."
    ),
}

# The reviewer-side counterpart of each category: what the human still does.
_REVIEWER_REMAINING_BY_CATEGORY = {
    CATEGORY_ASSISTABLE: (
        "Confirm or replace each recorded AI value; no value becomes authoritative without "
        "the reviewer's explicit confirmation."
    ),
    CATEGORY_HUMAN_ENGINEERING_DECISION: (
        "State the engineering value from the source drawing; the system supplies nothing."
    ),
    CATEGORY_EVIDENCE_LOOKUP: (
        "Locate the value in the source drawing and enter it explicitly; the system supplies "
        "nothing."
    ),
    CATEGORY_ADMINISTRATIVE: (
        "Acknowledge the task; the underlying engineering fields are answered by their own "
        "tasks."
    ),
    CATEGORY_NOT_APPLICABLE: (
        "Acknowledge the task; it grants nothing and forces nothing."
    ),
}


# --------------------------------------------------------------------------------------
# The frozen plain-data records.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ProvenanceRef:
    """One engineering field's provenance as the contract carries it."""
    field: str
    provenance: str


@dataclass(frozen=True)
class HumanBoundary:
    """Why a HUMAN_ENGINEERING_DECISION task stays human: the reason, the information the
    workflow still lacks, and what the reviewer's actual engineering judgement consists of."""
    why_human: str
    missing_information: str
    judgement_constitutes: str


# The human boundary for every human-decision task type. The three types present in the
# real 51-candidate workload have full wording; the vocabulary-only types carry the same
# three-part shape so the classification is total.
_HUMAN_BOUNDARY = {
    TASK_SELECT_POSITION: HumanBoundary(
        "The workflow schema records no position value and the task contract forbids "
        "inferring it from list order, page context or member geometry.",
        "An authoritative START/END position for this connection.",
        "Reading the connection's position on the member from the source drawing and "
        "stating it.",
    ),
    TASK_SELECT_ATTACHMENT: HumanBoundary(
        "The workflow schema records no attachment surfaces and the task contract forbids "
        "defaulting a surface to START or END.",
        "The attachment surface/reference for each connected member.",
        "Reading each member's attachment surface from the source drawing and stating it "
        "per member.",
    ),
    TASK_PROVIDE_LOCATION: HumanBoundary(
        "The workflow schema records no project-space location and the task contract "
        "forbids deriving coordinates from member placements, plate dimensions or page "
        "position.",
        "Explicit x/y/z coordinates with rotations for this connection.",
        "Determining the connection's location in the project space from the source "
        "drawing.",
    ),
    TASK_SELECT_MEMBER: HumanBoundary(
        "AI-extracted references are never reinterpreted as validated member marks; the "
        "reviewer selects the connected members.",
        "Which validated project member(s) the connection attaches to.",
        "Identifying the connected members from the source drawing and selecting them "
        "explicitly.",
    ),
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT: HumanBoundary(
        "The grouped member/position/attachment question is the reviewer's identification "
        "decision; AI references are never authoritative.",
        "The connected members, the position and the attachment surface for each.",
        "Identifying members, position and attachment surfaces from the source drawing.",
    ),
    TASK_RESOLVE_CONFLICT: HumanBoundary(
        "The task's own question begins 'Decide': conflicting reviewer decisions are "
        "resolved only by the reviewer.",
        "A single resolution of the conflicted field (confirm, supply, or drop both).",
        "Choosing how the conflicted field is resolved.",
    ),
}


@dataclass(frozen=True)
class TaskWorkloadRecord:
    """One existing task, classified for workload only. `current_value` is the contract's
    own display string of the existing AI observation — carried verbatim, never converted;
    `related_provenance` is the contract's provenance entries for the task's own field (for
    CONFIRM_AI_VALUES, every AI-extracted provenance entry — the confirmation is exactly the
    mechanism that labels them human-owned); `proposal` and `reviewer_remaining` are the
    fixed category texts; `proposal_risks_engineering_decision` is True exactly for the two
    categories where any proposed value would be the system deciding or inventing."""
    task_id: str
    task_type: str
    category: str
    required: bool
    resolved: bool
    current_value: str | None
    field: str | None
    evidence_text: str
    related_provenance: tuple[ProvenanceRef, ...]
    human_boundary: HumanBoundary | None
    proposal: str
    reviewer_remaining: str
    proposal_risks_engineering_decision: bool


@dataclass(frozen=True)
class CandidateWorkloadRecord:
    """One candidate's full workload picture, in the contract's own submission order."""
    package_id: str
    revision: int
    decision: str
    blocker_codes: tuple[str, ...]
    tasks: tuple[TaskWorkloadRecord, ...]
    ai_observation_fields: tuple[str, ...]
    has_source_page: bool
    has_detail_reference: bool
    has_grid_reference: bool
    requires_source_inspection: bool
    requires_engineering_judgement: bool
    bounded_confirmation_available: bool
    no_safe_assistance_opportunity: bool
    has_relevant_ai_observation: bool


@dataclass(frozen=True)
class CategoryCount:
    """One category's totals: task count, and the candidate and task ids it covers, in
    submission order — information only, no ordering of importance."""
    category: str
    task_count: int
    candidate_ids: tuple[str, ...]
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskTypeCount:
    """One task type's totals across the workload, in first-appearance order."""
    task_type: str
    task_count: int
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class BlockerCount:
    """One blocker code's totals, in first-appearance order."""
    blocker_code: str
    candidate_count: int
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewerWorkloadBaseline:
    """The frozen, deterministic 51-candidate workload analysis. Submission order is the
    contract's own order throughout; nothing is ordered by importance."""
    project_id: str | None
    revision: int
    candidate_count: int
    task_count: int
    pending_task_count: int
    categories: tuple[CategoryCount, ...]
    task_types: tuple[TaskTypeCount, ...]
    blockers: tuple[BlockerCount, ...]
    candidates_requiring_source_inspection: tuple[str, ...]
    candidates_requiring_engineering_judgement: tuple[str, ...]
    candidates_with_bounded_confirmation: tuple[str, ...]
    candidates_without_safe_assistance: tuple[str, ...]
    candidates_with_relevant_ai_observation: tuple[str, ...]
    candidates: tuple[CandidateWorkloadRecord, ...]


# --------------------------------------------------------------------------------------
# Presentation helpers — pure functions over contract values.
# --------------------------------------------------------------------------------------
_OBSERVATION_GROUPS = (
    ("member_references", "ai_member_references"),
    ("bolt_readings", "ai_bolt_readings"),
    ("plate_readings", "ai_plate_readings"),
    ("weld_readings", "ai_weld_readings"),
    ("malformed_readings", "ai_malformed_readings"),
    ("unrecognised_readings", "ai_unrecognised_readings"),
    ("connection_type", "ai_connection_type"),
    ("confidence", "ai_confidence"),
    ("material", "ai_material"),
)


def _evidence_text(evidence) -> str:
    parts = []
    if evidence.source_drawing_id:
        parts.append(f"source drawing {evidence.source_drawing_id}")
    if evidence.drawing_number:
        parts.append(f"drawing number {evidence.drawing_number}")
    if evidence.source_page is not None:
        parts.append(f"page {evidence.source_page}")
    if evidence.detail_reference:
        parts.append(f"detail {evidence.detail_reference}")
    if evidence.grid_reference:
        parts.append(f"grid {evidence.grid_reference}")
    return "; ".join(parts) if parts else "No source evidence recorded."


def _observation_fields(item) -> tuple[str, ...]:
    present = []
    for name, attribute in _OBSERVATION_GROUPS:
        value = getattr(item, attribute)
        if value is not None and (not isinstance(value, tuple) or value):
            present.append(name)
    return tuple(present)


def _related_provenance(item, task) -> tuple[ProvenanceRef, ...]:
    """The contract's provenance entries for the task's own field. For CONFIRM_AI_VALUES
    the whole-connection task is the mechanism by which every AI-extracted field becomes
    HUMAN_REVIEWED or HUMAN_SUPPLEMENTED, so all AI-extracted entries are related."""
    if task.field is not None:
        return tuple(
            ProvenanceRef(entry.field, entry.provenance)
            for entry in item.provenance if entry.field == task.field
        )
    if task.task_type == TASK_CONFIRM_AI_VALUES:
        return tuple(
            ProvenanceRef(entry.field, entry.provenance) for entry in item.provenance
        )
    return ()


def _candidate_record(item) -> CandidateWorkloadRecord:
    evidence_text = _evidence_text(item.evidence)
    task_records = []
    for task in item.tasks:
        category = classify_task(task.task_type, task.current_value)
        task_records.append(TaskWorkloadRecord(
            task_id=task.task_id,
            task_type=task.task_type,
            category=category,
            required=task.required,
            resolved=task.resolved,
            current_value=task.current_value,
            field=task.field,
            evidence_text=evidence_text,
            related_provenance=_related_provenance(item, task),
            human_boundary=(
                _HUMAN_BOUNDARY[task.task_type]
                if category == CATEGORY_HUMAN_ENGINEERING_DECISION else None
            ),
            proposal=_PROPOSAL_BY_CATEGORY[category],
            reviewer_remaining=_REVIEWER_REMAINING_BY_CATEGORY[category],
            proposal_risks_engineering_decision=(
                category in (CATEGORY_HUMAN_ENGINEERING_DECISION, CATEGORY_EVIDENCE_LOOKUP)
            ),
        ))
    categories_present = {record.category for record in task_records}
    return CandidateWorkloadRecord(
        package_id=item.package_id,
        revision=item.revision,
        decision=item.decision,
        blocker_codes=tuple(blocker.code for blocker in item.blockers),
        tasks=tuple(task_records),
        ai_observation_fields=_observation_fields(item),
        has_source_page=item.evidence.source_page is not None,
        has_detail_reference=item.evidence.detail_reference is not None,
        has_grid_reference=item.evidence.grid_reference is not None,
        requires_source_inspection=(
            CATEGORY_EVIDENCE_LOOKUP in categories_present
            or CATEGORY_HUMAN_ENGINEERING_DECISION in categories_present
        ),
        requires_engineering_judgement=CATEGORY_HUMAN_ENGINEERING_DECISION in categories_present,
        bounded_confirmation_available=CATEGORY_ASSISTABLE in categories_present,
        no_safe_assistance_opportunity=CATEGORY_ASSISTABLE not in categories_present,
        has_relevant_ai_observation=any(
            record.current_value is not None and not record.resolved
            for record in task_records
        ),
    )


def _distinct(candidate_ids):
    """Submission-order ids, each once, without changing the order."""
    return tuple(dict.fromkeys(candidate_ids))


# --------------------------------------------------------------------------------------
# The build function.
# --------------------------------------------------------------------------------------
def build_reviewer_workload_baseline(contract) -> ReviewerWorkloadBaseline:
    """
    Builds the frozen workload analysis from an existing 7AK project contract (any
    revision). Every value is projected from the contract verbatim; the classification
    follows the deterministic table above. Reads only — the contract and the workflow
    behind it are never touched, and nothing here can change a decision, a provenance
    label, a revision or a file.
    """
    if not isinstance(contract, ProjectReviewContract):
        raise TypeError(
            f"contract must be a ProjectReviewContract (got {type(contract).__name__}); "
            "the workload baseline projects the existing review contract, nothing else."
        )
    candidates = tuple(_candidate_record(item) for item in contract.items)

    category_counts = []
    for category in CATEGORIES:
        matching = [record for record in candidates
                    if any(task.category == category for task in record.tasks)]
        task_ids = tuple(
            task.task_id
            for record in candidates
            for task in record.tasks if task.category == category
        )
        category_counts.append(CategoryCount(
            category=category,
            task_count=len(task_ids),
            candidate_ids=tuple(record.package_id for record in matching),
            task_ids=task_ids,
        ))

    task_type_counts = []
    seen_types = []
    for record in candidates:
        for task in record.tasks:
            if task.task_type not in seen_types:
                seen_types.append(task.task_type)
    for task_type in seen_types:
        matching = [record for record in candidates
                    if any(task.task_type == task_type for task in record.tasks)]
        task_type_counts.append(TaskTypeCount(
            task_type=task_type,
            task_count=sum(1 for record in candidates
                           for task in record.tasks if task.task_type == task_type),
            candidate_ids=tuple(record.package_id for record in matching),
        ))

    blocker_counts = []
    seen_codes = []
    for record in candidates:
        for code in record.blocker_codes:
            if code not in seen_codes:
                seen_codes.append(code)
    for code in seen_codes:
        matching = [record for record in candidates if code in record.blocker_codes]
        blocker_counts.append(BlockerCount(
            blocker_code=code,
            candidate_count=len(matching),
            candidate_ids=tuple(record.package_id for record in matching),
        ))

    return ReviewerWorkloadBaseline(
        project_id=contract.project_id,
        revision=contract.revision,
        candidate_count=len(candidates),
        task_count=sum(len(record.tasks) for record in candidates),
        pending_task_count=sum(
            1 for record in candidates for task in record.tasks if not task.resolved
        ),
        categories=tuple(category_counts),
        task_types=tuple(task_type_counts),
        blockers=tuple(blocker_counts),
        candidates_requiring_source_inspection=tuple(
            record.package_id for record in candidates
            if record.requires_source_inspection
        ),
        candidates_requiring_engineering_judgement=tuple(
            record.package_id for record in candidates
            if record.requires_engineering_judgement
        ),
        candidates_with_bounded_confirmation=tuple(
            record.package_id for record in candidates
            if record.bounded_confirmation_available
        ),
        candidates_without_safe_assistance=tuple(
            record.package_id for record in candidates
            if record.no_safe_assistance_opportunity
        ),
        candidates_with_relevant_ai_observation=tuple(
            record.package_id for record in candidates
            if record.has_relevant_ai_observation
        ),
        candidates=candidates,
    )
