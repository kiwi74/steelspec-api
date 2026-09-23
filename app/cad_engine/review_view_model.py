"""
Milestone 7AM — DETERMINISTIC UI VIEW-MODEL RENDERING BOUNDARY.

A pure presentation adapter: it transforms the existing review-contract
objects (the 7AK layer) into immutable plain-data view models a future
frontend can consume directly. It answers only one question:

    "How should the already-established review contract be presented?"

It never answers "what should SteelSpec decide". Every engineering truth,
decision, blocker, task, provenance value, AI value, artifact fact and
action stays exactly as the contract states it; this module adds nothing
but deterministic presentation — human-readable labels for existing
machine values, a generated summary, and a convenient grouping.

HARD RULES:

  - CONSUMES CONTRACTS ONLY. The input is the review contract produced
    by the existing contract layer; workflow internals are never read.
  - NO DECISIONS, NO BUSINESS LOGIC. No gate rules, no validation, no
    inference, no new codes, no new task types, no new state vocabulary.
    A status is presented with its label; the underlying value is never
    changed, re-derived or replaced.
  - NO INFERENCE FROM OUTPUTS. `generated_files`, `verification_status`
    and `blockers` are presented verbatim; nothing is recomputed from
    them (an artifact that failed verification stays a failed artifact).
  - AI VALUES STAY EXACT. Extracted readings are shown byte-for-byte as
    the contract carries them — no conversion, no interpretation, no
    "cleaning up", no unit inference, no decoration.
  - MISSING IS MISSING. When the contract has no value (no identity, no
    page, no detail), the view shows none. Nothing is invented to make
    the screen look complete.
  - IMMUTABLE AND DETERMINISTIC. Every view is a frozen dataclass of
    plain data; ordering (items, blockers, warnings, tasks, provenance,
    actions) follows the contract's own order; the same contract always
    renders to the same view.
  - PURE AND UI-NEUTRAL. No markup, no templates, no styling, no
    browser code, no API, no database, no network, no AI, no
    environment variables, no filesystem access.
"""

from dataclasses import dataclass

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
)
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.review_contract import (
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    ConnectionReviewContract,
    ProjectReviewContract,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)

__all__ = [
    "ActionView", "AttentionView", "BlockerView", "ConnectionIdentityView",
    "ConnectionReviewView", "CountsView", "EvidenceView", "ExtractedValuesView",
    "OutputView", "ProjectReviewView", "ProvenanceView", "TaskView",
    "render_connection_view", "render_project_view",
]

# --------------------------------------------------------------------------------------
# Deterministic presentation tables: existing machine value -> human label. Unknown
# values are shown verbatim as their own label — never dropped, never guessed.
# --------------------------------------------------------------------------------------
_DECISION_LABELS = {
    AUTOMATION_DECISION_REVIEW: "Needs review",
    AUTOMATION_DECISION_CONFIRM: "Needs confirmation",
    AUTOMATION_DECISION_AUTO: "Automated",
}

_OUTPUT_LABELS = {
    OUTPUT_STATUS_GENERATED: "Generated",
    OUTPUT_STATUS_GENERATION_FAILED: "Generation failed",
    OUTPUT_STATUS_BLOCKED_REVIEW: "Blocked from output",
}

_VERIFICATION_LABELS = {
    VERIFICATION_STATUS_VERIFIED: "Verified",
    VERIFICATION_STATUS_FAILED: "Verification failed",
    VERIFICATION_STATUS_NO_ARTIFACT: "No artifact",
}

_SEVERITY_LABELS = {
    SEVERITY_BLOCKING: "Blocking",
    SEVERITY_WARNING: "Warning",
}

_ACTION_LABELS = {
    ACTION_REVIEW: "Review",
    ACTION_RESOLVE: "Resolve",
    ACTION_REFRESH: "Refresh",
}

_PROVENANCE_LABELS = {
    PROVENANCE_AI_EXTRACTED: "AI extracted",
    PROVENANCE_HUMAN_REVIEWED: "Human reviewed",
    PROVENANCE_HUMAN_SUPPLEMENTED: "Human supplied",
}


# --------------------------------------------------------------------------------------
# The immutable plain-data view models.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ActionView:
    """One existing workflow-facing action and its presentation label."""
    action: str
    label: str


@dataclass(frozen=True)
class AttentionView:
    """Whether this connection needs a human's attention right now, exactly as the
    contract's `requires_action` states it — never recomputed from blockers or files."""
    requires_attention: bool
    label: str


@dataclass(frozen=True)
class BlockerView:
    """One existing blocker/warning finding, rendered: the stable code verbatim, the
    contract's own title/message, the severity with its label, and the related field
    and task type when the contract names them."""
    code: str
    title: str
    message: str
    severity: str
    severity_label: str
    field: str | None
    task_type: str | None


@dataclass(frozen=True)
class ConnectionIdentityView:
    """What a future screen is displaying: the package identity and, when the contract
    has one, the connection identity. `display_reference` is the contract's own
    connection identity — never derived from the package or the AI text; None when the
    contract has none."""
    package_id: str
    connection_id: str | None
    project_id: str | None
    revision: int
    display_reference: str | None


@dataclass(frozen=True)
class EvidenceView:
    """The extraction evidence exactly as the contract carries it, plus a deterministic
    plain `evidence_text` built from the present fields only."""
    source_drawing_id: str | None
    drawing_number: str | None
    source_page: int | None
    detail_reference: str | None
    grid_reference: str | None
    evidence_text: str


@dataclass(frozen=True)
class ExtractedValuesView:
    """The AI's extracted values verbatim, grouped for display — never converted."""
    member_references: tuple[str, ...]
    bolt_readings: tuple[str, ...]
    plate_readings: tuple[str, ...]
    weld_readings: tuple[str, ...]
    malformed_readings: tuple[str, ...]
    unrecognised_readings: tuple[str, ...]
    connection_type: str | None
    confidence: str | None
    material: str | None


@dataclass(frozen=True)
class ProvenanceView:
    """One engineering field's provenance: the authoritative label verbatim plus its
    human-readable presentation label."""
    field: str
    provenance: str
    provenance_label: str


@dataclass(frozen=True)
class TaskView:
    """One existing human-resolution task, ready for a future form renderer to inspect:
    identity, title, description, field, requiredness, resolution state, the current
    AI value and the recorded human answer verbatim, the authoritative allowed
    options, and the task's own answer payload shape (`answer_type`, 7AC's
    vocabulary — a form renderer needs it to build a valid HumanResolution; passed
    through verbatim, never interpreted). No form controls, no validation, no
    submission."""
    task_id: str
    task_type: str
    title: str
    description: str
    field: str | None
    required: bool
    resolved: bool
    current_value: str | None
    allowed_options: tuple[str, ...]
    evidence_requirement: str
    resolution_value: str | None
    resolution_evidence: str | None
    answer_type: str


@dataclass(frozen=True)
class OutputView:
    """The output and verification truth exactly as the contract states it: machine
    values plus labels, and the recorded artifacts. A failed verification artifact is
    shown as a failed artifact — file presence never means success."""
    output_status: str | None
    output_label: str | None
    verification_status: str | None
    verification_label: str | None
    generated_files: tuple[str, ...]


@dataclass(frozen=True)
class CountsView:
    """The project's authoritative counts, shaped for display."""
    review: int
    verified: int
    auto: int
    confirmation: int
    blocked: int


@dataclass(frozen=True)
class ConnectionReviewView:
    """The rendered view of ONE connection: identity, authoritative decision with its
    label, attention state, blockers and warnings, evidence, extracted values,
    provenance, tasks, the contract's own actions, output/verification truth and the
    contract's summary line."""
    identity: ConnectionIdentityView
    decision: str
    decision_label: str
    attention: AttentionView
    blockers: tuple[BlockerView, ...]
    warnings: tuple[BlockerView, ...]
    evidence: EvidenceView
    extracted: ExtractedValuesView
    provenance: tuple[ProvenanceView, ...]
    tasks: tuple[TaskView, ...]
    actions: tuple[ActionView, ...]
    output: OutputView
    summary: str


@dataclass(frozen=True)
class ProjectReviewView:
    """The rendered view of the whole project: identity, authoritative status with its
    label, a generated summary, the counts, how many connections need attention and
    how many are verified, the project's own actions, and the ordered connection views
    split into those still needing attention and those that are complete. Ordering
    within each group follows the contract's own order."""
    project_id: str | None
    revision: int
    project_status: str
    status_label: str
    summary: str
    counts: CountsView
    requiring_attention: int
    verified: int
    actions: tuple[ActionView, ...]
    review_items: tuple[ConnectionReviewView, ...]
    completed_items: tuple[ConnectionReviewView, ...]


# --------------------------------------------------------------------------------------
# Presentation helpers — pure functions over contract values.
# --------------------------------------------------------------------------------------
def _label(table, value):
    """A deterministic human label for an existing machine value; unknown or absent
    values fall back to themselves (or None) — never dropped, never guessed."""
    if value is None:
        return None
    return table.get(value, value)


def _actions(actions):
    return tuple(ActionView(action, _label(_ACTION_LABELS, action)) for action in actions)


def _count_phrase(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


def _project_summary(contract):
    """A concise summary generated from the contract's own counts — never hard-coded,
    never a recommendation."""
    total = len(contract.items)
    if total and contract.verified_count == total:
        return "All connections verified"
    attention = sum(1 for item in contract.items if item.requires_action)
    remaining = total - attention - contract.verified_count
    return "; ".join((
        _count_phrase(attention, "connection needs review", "connections need review"),
        _count_phrase(contract.verified_count, "connection verified", "connections verified"),
        _count_phrase(remaining, "connection remains", "connections remain"),
    ))


def _evidence_text(evidence):
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


def _blocker_views(findings):
    return tuple(
        BlockerView(
            code=finding.code,
            title=finding.title,
            message=finding.message,
            severity=finding.severity,
            severity_label=_label(_SEVERITY_LABELS, finding.severity),
            field=finding.field,
            task_type=finding.task_type,
        )
        for finding in findings
    )


def _task_views(tasks):
    return tuple(
        TaskView(
            task_id=task.task_id,
            task_type=task.task_type,
            title=task.title,
            description=task.description,
            field=task.field,
            required=task.required,
            resolved=task.resolved,
            current_value=task.current_value,
            allowed_options=task.allowed_options,
            evidence_requirement=task.evidence_requirement,
            resolution_value=task.resolution_value,
            resolution_evidence=task.resolution_evidence,
            answer_type=task.answer_type,
        )
        for task in tasks
    )


def _provenance_views(provenance):
    return tuple(
        ProvenanceView(
            field=entry.field,
            provenance=entry.provenance,
            provenance_label=_label(_PROVENANCE_LABELS, entry.provenance),
        )
        for entry in provenance
    )


# --------------------------------------------------------------------------------------
# The two render functions.
# --------------------------------------------------------------------------------------
def render_connection_view(contract) -> ConnectionReviewView:
    """
    Renders ONE connection contract into an immutable plain-data view. Every field
    comes from the contract: the decision, attention, blockers, warnings, evidence,
    extracted values, provenance, tasks, actions and output facts are passed through
    verbatim (with presentation labels added alongside); nothing is inferred,
    invented, or made actionable that the contract does not already permit.
    """
    if not isinstance(contract, ConnectionReviewContract):
        raise TypeError(
            f"contract must be a ConnectionReviewContract (got {type(contract).__name__}); "
            "the view model renders the existing review contract, nothing else."
        )
    return ConnectionReviewView(
        identity=ConnectionIdentityView(
            package_id=contract.package_id,
            connection_id=contract.connection_id,
            project_id=contract.project_id,
            revision=contract.revision,
            display_reference=contract.connection_id,
        ),
        decision=contract.decision,
        decision_label=_label(_DECISION_LABELS, contract.decision),
        attention=AttentionView(
            requires_attention=contract.requires_action,
            label="Needs attention" if contract.requires_action else "No action required",
        ),
        blockers=_blocker_views(contract.blockers),
        warnings=_blocker_views(contract.warnings),
        evidence=EvidenceView(
            source_drawing_id=contract.evidence.source_drawing_id,
            drawing_number=contract.evidence.drawing_number,
            source_page=contract.evidence.source_page,
            detail_reference=contract.evidence.detail_reference,
            grid_reference=contract.evidence.grid_reference,
            evidence_text=_evidence_text(contract.evidence),
        ),
        extracted=ExtractedValuesView(
            member_references=contract.ai_member_references,
            bolt_readings=contract.ai_bolt_readings,
            plate_readings=contract.ai_plate_readings,
            weld_readings=contract.ai_weld_readings,
            malformed_readings=contract.ai_malformed_readings,
            unrecognised_readings=contract.ai_unrecognised_readings,
            connection_type=contract.ai_connection_type,
            confidence=contract.ai_confidence,
            material=contract.ai_material,
        ),
        provenance=_provenance_views(contract.provenance),
        tasks=_task_views(contract.tasks),
        actions=_actions(contract.available_actions),
        output=OutputView(
            output_status=contract.output_status,
            output_label=_label(_OUTPUT_LABELS, contract.output_status),
            verification_status=contract.verification_status,
            verification_label=_label(_VERIFICATION_LABELS, contract.verification_status),
            generated_files=contract.generated_files,
        ),
        summary=contract.summary,
    )


def render_project_view(contract) -> ProjectReviewView:
    """
    Renders a project contract into an immutable plain-data view. Connection views
    follow the contract's own order, split into the ones still requiring attention
    and the completed ones; the counts, status, actions and summary come from the
    contract's own values. Pure presentation — the contract stays authoritative.
    """
    if not isinstance(contract, ProjectReviewContract):
        raise TypeError(
            f"contract must be a ProjectReviewContract (got {type(contract).__name__}); "
            "the view model renders the existing review contract, nothing else."
        )
    items = tuple(render_connection_view(item) for item in contract.items)
    return ProjectReviewView(
        project_id=contract.project_id,
        revision=contract.revision,
        project_status=contract.project_status,
        status_label=_label(_DECISION_LABELS, contract.project_status),
        summary=_project_summary(contract),
        counts=CountsView(
            review=contract.review_count,
            verified=contract.verified_count,
            auto=contract.auto_count,
            confirmation=contract.confirmation_count,
            blocked=contract.blocked_count,
        ),
        requiring_attention=sum(1 for item in items if item.attention.requires_attention),
        verified=contract.verified_count,
        actions=_actions(contract.available_actions),
        review_items=tuple(item for item in items if item.attention.requires_attention),
        completed_items=tuple(item for item in items if not item.attention.requires_attention),
    )
