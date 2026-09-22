"""
Milestone 7B6 — REAL-WORLD ASSISTED REVIEW EXECUTION PROOF (read-only layer).

The assistance layer turns the committed 7B5 workload classification into an
EXECUTION-facing assist view over the existing 7AK project contract: for every
real task it carries the 7B5 category, the contract's AI observation VERBATIM,
the fixed 7B5 proposal and reviewer-remaining texts, the existing source anchor
(drawing/page/detail/grid), the provenance entries the contract already carries,
the human boundary for human tasks, and fixed confirmation semantics stating
what the reviewer does and what the system never does.

It is presentation and navigation ONLY. It:

  - NEVER creates, infers, transforms or decides any engineering value, and
    never orders anything by importance. "22mm holes" stays "22mm holes";
    "300" stays an observation; missing material stays missing.
  - NEVER confirms, auto-accepts or pre-fills anything. Every resolution
    remains an explicit human act through the existing 7AC/7AJ public
    resolution path; the assistance layer has no resolution method and no
    path to one.
  - NEVER changes workflow state: the input is the frozen 7AK contract, the
    output is frozen plain data, and nothing here can reach the workflow,
    its revision, its provenance or its artifacts.

The 7B5 baseline is consumed through its PUBLIC API only
(build_reviewer_workload_baseline, the category constants, and the public
record types ProvenanceRef and HumanBoundary) — the 7B5 files are untouched.
"""

from dataclasses import dataclass

from app.cad_engine.review_contract import ProjectReviewContract
from app.cad_engine.reviewer_workload_reduction import (
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_ASSISTABLE,
    CATEGORY_EVIDENCE_LOOKUP,
    CATEGORY_HUMAN_ENGINEERING_DECISION,
    CATEGORY_NOT_APPLICABLE,
    HumanBoundary,
    ProvenanceRef,
    build_reviewer_workload_baseline,
)

__all__ = [
    "AssistCandidate", "AssistTask", "ConfirmationSemantics",
    "ReviewerAssistance", "build_reviewer_assistance",
    "confirmation_semantics",
]

# --------------------------------------------------------------------------------------
# Fixed confirmation semantics — one per 7B5 category. What the reviewer does, and what
# the system never does. These are value-free statements: they contain no engineering
# value and perform no conversion.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConfirmationSemantics:
    """What an assistance step means for the reviewer and for the system."""
    reviewer_action: str
    system_never: str


_CONFIRMATION_SEMANTICS = {
    CATEGORY_ASSISTABLE: ConfirmationSemantics(
        "Explicitly confirm or replace the recorded value; the value becomes "
        "authoritative only through this reviewer act.",
        "The system displays the recorded observation verbatim, performs no "
        "conversion of it, and never confirms, pre-fills or submits anything on "
        "the reviewer's behalf.",
    ),
    CATEGORY_HUMAN_ENGINEERING_DECISION: ConfirmationSemantics(
        "State the engineering value from the source drawing; the reviewer's "
        "stated value is the only value recorded.",
        "The system proposes no value, supplies no value and infers nothing; any "
        "value it proposed here would be the system making the reviewer's "
        "engineering decision.",
    ),
    CATEGORY_EVIDENCE_LOOKUP: ConfirmationSemantics(
        "Locate the value in the source drawing and enter it explicitly; the "
        "source anchor is navigation only.",
        "The system supplies nothing and derives nothing; it never turns the "
        "navigation hint into an answer.",
    ),
    CATEGORY_ADMINISTRATIVE: ConfirmationSemantics(
        "Acknowledge the task; the underlying engineering fields are answered "
        "by their own tasks.",
        "The task grants nothing by itself; the system never treats an "
        "acknowledgement as an engineering answer.",
    ),
    CATEGORY_NOT_APPLICABLE: ConfirmationSemantics(
        "Acknowledge the task; it carries no engineering field question.",
        "It grants nothing and forces nothing; the system never attaches a "
        "value to it.",
    ),
}


def confirmation_semantics(category: str) -> ConfirmationSemantics:
    """The fixed confirmation semantics for one of the five 7B5 categories.

    Anything outside the five categories is refused — the assistance layer
    never silently invents semantics for an unknown classification.
    """
    if category not in _CONFIRMATION_SEMANTICS:
        raise ValueError(
            f"category {category!r} is not one of the five workload categories; "
            "the assistance layer refuses to invent semantics for an unknown "
            "classification."
        )
    return _CONFIRMATION_SEMANTICS[category]


# --------------------------------------------------------------------------------------
# The frozen assist records.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AssistTask:
    """One real task, rendered for assistance. `current_value` is the contract's own
    display string of the existing AI observation, carried byte-verbatim; `proposal`,
    `reviewer_remaining` and `human_boundary` are the 7B5 baseline's fixed texts;
    `related_provenance` is the contract's provenance entries for the task's own
    field; `confirmation` is the fixed category semantics. No field carries or
    derives a value."""
    task_id: str
    task_type: str
    category: str
    required: bool
    resolved: bool
    current_value: str | None
    field: str | None
    source_anchor: str
    related_provenance: tuple[ProvenanceRef, ...]
    human_boundary: HumanBoundary | None
    proposal: str
    reviewer_remaining: str
    confirmation: ConfirmationSemantics
    proposal_risks_engineering_decision: bool


@dataclass(frozen=True)
class AssistCandidate:
    """One candidate's assistance view, in the contract's own submission order."""
    package_id: str
    revision: int
    decision: str
    blocker_codes: tuple[str, ...]
    tasks: tuple[AssistTask, ...]
    has_source_page: bool
    has_detail_reference: bool
    has_grid_reference: bool
    requires_source_inspection: bool
    requires_engineering_judgement: bool
    bounded_confirmation_available: bool
    no_safe_assistance_opportunity: bool
    has_relevant_ai_observation: bool


@dataclass(frozen=True)
class ReviewerAssistance:
    """The frozen, deterministic assistance view over one 7AK project contract."""
    project_id: str | None
    revision: int
    candidate_count: int
    task_count: int
    pending_task_count: int
    candidates: tuple[AssistCandidate, ...]


def _assist_task(record) -> AssistTask:
    category = record.category
    return AssistTask(
        task_id=record.task_id,
        task_type=record.task_type,
        category=category,
        required=record.required,
        resolved=record.resolved,
        current_value=record.current_value,
        field=record.field,
        source_anchor=record.evidence_text,
        related_provenance=record.related_provenance,
        human_boundary=record.human_boundary,
        proposal=record.proposal,
        reviewer_remaining=record.reviewer_remaining,
        confirmation=confirmation_semantics(category),
        proposal_risks_engineering_decision=record.proposal_risks_engineering_decision,
    )


def _assist_candidate(item, record) -> AssistCandidate:
    if item.package_id != record.package_id:
        # Cannot happen while both sides project the same contract; fail loudly anyway.
        raise ValueError(
            f"assistance alignment failure: contract item {item.package_id!r} against "
            f"workload record {record.package_id!r}."
        )
    return AssistCandidate(
        package_id=item.package_id,
        revision=item.revision,
        decision=item.decision,
        blocker_codes=record.blocker_codes,
        tasks=tuple(_assist_task(task_record) for task_record in record.tasks),
        has_source_page=record.has_source_page,
        has_detail_reference=record.has_detail_reference,
        has_grid_reference=record.has_grid_reference,
        requires_source_inspection=record.requires_source_inspection,
        requires_engineering_judgement=record.requires_engineering_judgement,
        bounded_confirmation_available=record.bounded_confirmation_available,
        no_safe_assistance_opportunity=record.no_safe_assistance_opportunity,
        has_relevant_ai_observation=record.has_relevant_ai_observation,
    )


def build_reviewer_assistance(contract) -> ReviewerAssistance:
    """Builds the frozen assistance view from an existing 7AK project contract (any
    revision), classified by the committed 7B5 baseline through its public API.

    Reads only: the contract and the workflow behind it are never touched, and
    nothing here can change a decision, a provenance label, a revision or a file.
    """
    if not isinstance(contract, ProjectReviewContract):
        raise TypeError(
            f"contract must be a ProjectReviewContract (got {type(contract).__name__}); "
            "the assistance layer projects the existing review contract, nothing else."
        )
    baseline = build_reviewer_workload_baseline(contract)
    if len(contract.items) != len(baseline.candidates):
        raise ValueError(
            f"assistance alignment failure: contract has {len(contract.items)} items "
            f"against {len(baseline.candidates)} workload records."
        )
    candidates = tuple(
        _assist_candidate(item, record)
        for item, record in zip(contract.items, baseline.candidates)
    )
    return ReviewerAssistance(
        project_id=contract.project_id,
        revision=contract.revision,
        candidate_count=len(candidates),
        task_count=sum(len(candidate.tasks) for candidate in candidates),
        pending_task_count=sum(
            1 for candidate in candidates for task in candidate.tasks if not task.resolved
        ),
        candidates=candidates,
    )
