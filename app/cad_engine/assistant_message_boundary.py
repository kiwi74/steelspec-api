"""
Milestone 7B7 — EXTERNAL ASSISTANT MESSAGE BOUNDARY PROOF (trust boundary).

This module is the smallest deterministic boundary between SteelSpec's trusted
review state and ANY future external assistant. It defines, as frozen plain
data, exactly three things:

  1. What SteelSpec is permitted to SEND to an external assistant —
     a minimal projection of one task (or of the aggregate counts), built
     from the committed 7B6 assistance view, field by field, by an explicit
     per-role permission table. Nothing else is reachable: the module holds
     no reference to the workflow, the contract or any object beyond the
     frozen strings it copies.
  2. What an external assistant is permitted to RETURN —
     untrusted prose text plus optionally claimed source references, wrapped
     in a frozen record that carries NO value, NO provenance capability, NO
     decision capability and NO mutation API of any kind.
  3. What SteelSpec does with the returned text —
     render it as DISPLAY-ONLY content. The response cannot reach the 7AC/7AJ
     resolution machinery because nothing in this module can name it: the
     resolution, gate, drawing and artifact modules are not imported here.

The trust boundary in one sentence: existing trusted observations may travel
OUT verbatim when a role explicitly permits them; nothing an assistant says
ever travels IN as anything but display text.

  - EXISTING TRUSTED OBSERVATION: released only for the one role whose whole
    purpose is describing the recorded observation, byte-verbatim. "300"
    stays "300"; "22mm holes" stays "22mm holes". No conversion exists here.
  - EXTERNAL-GENERATED ENGINEERING VALUE: never represented — the response
    has no field that could carry one into trusted state.
  - EXTERNAL CONVERSION: never represented, never performed.
  - EXTERNAL ANSWER PAYLOAD: never represented — the response is not a
    mapping, not a tuple payload, and no function here returns anything the
    resolution path accepts.
  - EXTERNAL SOURCE REFERENCE: representable only as an untrusted claim; a
    claim is flagged as matching a trusted anchor ONLY when it is exactly
    equal to a source anchor already released in the request, and even then
    it remains display-only.

The module is pure, frozen and deterministic: no I/O, no network, no
persistence, no external dependencies, and no state that survives a call.
The external assistant is treated as untrusted in every representation this
module produces.
"""

from dataclasses import dataclass

from app.cad_engine.reviewer_assistance import ReviewerAssistance
from app.cad_engine.reviewer_workload_reduction import (
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_ASSISTABLE,
    CATEGORY_EVIDENCE_LOOKUP,
)

__all__ = [
    "ROLE_DESCRIBE_OBSERVATION", "ROLE_DOCUMENTATION_QA",
    "ROLE_DRAFT_ACKNOWLEDGMENT", "ROLE_EVIDENCE_NAVIGATION",
    "ROLE_EXPLAIN_TASK", "ROLE_QUEUE_SUMMARY", "ROLES",
    "AssistantReferenceClaim", "AssistantRequest", "AssistantRequestField",
    "AssistantResponse", "build_assistant_request", "parse_assistant_response",
    "render_assistant_message", "response_context_matches", "DISPLAY_PREFIX",
]

# --------------------------------------------------------------------------------------
# The six value-free roles. ROLES is a frozenset: there is no ordering here, and the
# boundary never ranks or prioritises anything. Each role exists for one explicit
# reviewer purpose and may release only the fields its permission row names.
# --------------------------------------------------------------------------------------
ROLE_EXPLAIN_TASK = "EXPLAIN_TASK"
ROLE_DESCRIBE_OBSERVATION = "DESCRIBE_OBSERVATION"
ROLE_DRAFT_ACKNOWLEDGMENT = "DRAFT_ACKNOWLEDGMENT"
ROLE_EVIDENCE_NAVIGATION = "EVIDENCE_NAVIGATION"
ROLE_QUEUE_SUMMARY = "QUEUE_SUMMARY"
ROLE_DOCUMENTATION_QA = "DOCUMENTATION_QA"

ROLES = frozenset({
    ROLE_EXPLAIN_TASK, ROLE_DESCRIBE_OBSERVATION, ROLE_DRAFT_ACKNOWLEDGMENT,
    ROLE_EVIDENCE_NAVIGATION, ROLE_QUEUE_SUMMARY, ROLE_DOCUMENTATION_QA,
})

_TASK_SCOPED_ROLES = frozenset({
    ROLE_EXPLAIN_TASK, ROLE_DESCRIBE_OBSERVATION, ROLE_DRAFT_ACKNOWLEDGMENT,
    ROLE_EVIDENCE_NAVIGATION,
})

# The exact fallback wording the 7B5 baseline records when a candidate carries no
# source evidence. It is not an anchor, and the boundary refuses to release it as one.
NO_SOURCE_EVIDENCE_TEXT = "No source evidence recorded."

# The single permission table: for each role, the complete set of field names it may
# release. Field names not listed here can never appear in an AssistantRequest; the
# build function enforces this at runtime as well as by construction.
_ROLE_FIELD_NAMES = {
    ROLE_EXPLAIN_TASK: frozenset({
        "task_id", "task_type", "category",
        "proposal", "reviewer_remaining",
        "confirmation_reviewer_action", "confirmation_system_never",
        "why_human", "missing_information", "judgement_constitutes",
    }),
    ROLE_DESCRIBE_OBSERVATION: frozenset({
        "task_id", "task_type", "category", "current_value", "provenance",
    }),
    ROLE_DRAFT_ACKNOWLEDGMENT: frozenset({
        "task_id", "task_type", "category", "proposal", "reviewer_remaining",
    }),
    ROLE_EVIDENCE_NAVIGATION: frozenset({
        "task_id", "task_type", "category", "source_anchor",
    }),
    ROLE_QUEUE_SUMMARY: frozenset({
        "candidate_count", "task_count", "pending_task_count",
    }),
    ROLE_DOCUMENTATION_QA: frozenset(),
}


# --------------------------------------------------------------------------------------
# The request side: what SteelSpec is permitted to send.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AssistantRequestField:
    """One released field: a name from the role's permission row and the trusted
    value, copied byte-verbatim from the 7B6 assistance view (or, for aggregate
    roles, rendered from the view's own counts). Nothing else can be a field."""
    name: str
    value: str


@dataclass(frozen=True)
class AssistantRequest:
    """The complete request representation. `fields` is the ONLY content released
    to an external assistant. `role`, `package_id`, `task_id` and `revision` are
    SteelSpec-side bookkeeping that binds the request to the exact contract state
    it was built from (revision included, so a stale request never matches a newer
    context); they are NOT part of `fields` and are never transmitted.
    `reviewer_question` is the reviewer's own words, carried verbatim with no
    trimming or interpretation."""
    role: str
    package_id: str | None
    task_id: str | None
    revision: int
    reviewer_question: str
    fields: tuple[AssistantRequestField, ...]


def _assist_task_for(assistance, package_id, task_id):
    """The one 7B6 assist task for the given ids, or a loud refusal."""
    for candidate in assistance.candidates:
        if candidate.package_id == package_id:
            for task in candidate.tasks:
                if task.task_id == task_id:
                    return task
            raise ValueError(
                f"no task {task_id!r} on candidate {package_id!r}; the boundary "
                "refuses to release anything for a task identity the contract does "
                "not carry."
            )
    raise ValueError(
        f"no candidate {package_id!r} in the assistance view; the boundary refuses "
        "to release anything for a candidate identity the contract does not carry."
    )


def _identity_fields(task):
    return (
        AssistantRequestField("task_id", task.task_id),
        AssistantRequestField("task_type", task.task_type),
        AssistantRequestField("category", task.category),
    )


def _fixed_text_fields(task):
    """The fixed 7B5/7B6 wording, verbatim: the proposal and reviewer-remaining
    texts, the 7B6 confirmation semantics, and — for human-decision tasks — the
    three-part human boundary. All of it is fixed text; none of it is a value."""
    fields = [
        AssistantRequestField("proposal", task.proposal),
        AssistantRequestField("reviewer_remaining", task.reviewer_remaining),
        AssistantRequestField(
            "confirmation_reviewer_action", task.confirmation.reviewer_action),
        AssistantRequestField(
            "confirmation_system_never", task.confirmation.system_never),
    ]
    if task.human_boundary is not None:
        fields.append(
            AssistantRequestField("why_human", task.human_boundary.why_human))
        fields.append(AssistantRequestField(
            "missing_information", task.human_boundary.missing_information))
        fields.append(AssistantRequestField(
            "judgement_constitutes", task.human_boundary.judgement_constitutes))
    return tuple(fields)


def _provenance_fields(task):
    """The contract's own provenance metadata for the task's field, verbatim —
    one field per entry. Provenance labels are metadata, never values."""
    return tuple(
        AssistantRequestField("provenance", f"{ref.field}: {ref.provenance}")
        for ref in task.related_provenance
    )


def _fields_for_role(role, task):
    """The released fields for one task-scoped role, with the role's
    preconditions enforced loudly."""
    if role == ROLE_EXPLAIN_TASK:
        return _identity_fields(task) + _fixed_text_fields(task)
    if role == ROLE_DESCRIBE_OBSERVATION:
        if task.category != CATEGORY_ASSISTABLE or task.current_value is None:
            raise ValueError(
                f"DESCRIBE_OBSERVATION requires an existing trusted observation; "
                f"task {task.task_id!r} ({task.task_type!r}, {task.category!r}) has "
                "none, and the boundary never invents one."
            )
        return _identity_fields(task) + (
            AssistantRequestField("current_value", task.current_value),
        ) + _provenance_fields(task)
    if role == ROLE_DRAFT_ACKNOWLEDGMENT:
        if task.category != CATEGORY_ADMINISTRATIVE:
            raise ValueError(
                f"DRAFT_ACKNOWLEDGMENT applies to administrative tasks only; task "
                f"{task.task_id!r} is {task.category!r}, and the boundary refuses to "
                "draft an acknowledgment for an engineering task."
            )
        return _identity_fields(task) + (
            AssistantRequestField("proposal", task.proposal),
            AssistantRequestField("reviewer_remaining", task.reviewer_remaining),
        )
    if role == ROLE_EVIDENCE_NAVIGATION:
        anchor = task.source_anchor
        if (task.category != CATEGORY_EVIDENCE_LOOKUP or not anchor
                or anchor == NO_SOURCE_EVIDENCE_TEXT):
            raise ValueError(
                f"EVIDENCE_NAVIGATION requires a genuine source anchor; task "
                f"{task.task_id!r} ({task.category!r}) carries none, and the "
                "boundary never fabricates one."
            )
        return _identity_fields(task) + (
            AssistantRequestField("source_anchor", anchor),
        )
    # Unreachable: every task-scoped role is handled above.
    raise ValueError(f"role {role!r} is not a task-scoped role.")


def _aggregate_fields(role, assistance):
    """The released fields for the two aggregate roles: counts only, no identity."""
    if role == ROLE_QUEUE_SUMMARY:
        return (
            AssistantRequestField("candidate_count", str(assistance.candidate_count)),
            AssistantRequestField("task_count", str(assistance.task_count)),
            AssistantRequestField("pending_task_count", str(assistance.pending_task_count)),
        )
    if role == ROLE_DOCUMENTATION_QA:
        # The question concerns the workflow's own fixed documentation; no contract
        # content is released at all.
        return ()
    # Unreachable: every aggregate role is handled above.
    raise ValueError(f"role {role!r} is not an aggregate role.")


def _enforce_permission_row(role, fields):
    """Defence in depth: every released field must sit inside the role's own
    permission row, even if a future edit to an emitter forgets to."""
    permitted = _ROLE_FIELD_NAMES[role]
    for field in fields:
        if field.name not in permitted:
            raise ValueError(
                f"boundary permission failure: field {field.name!r} is not in the "
                f"permission row for role {role!r}; nothing is released."
            )


def build_assistant_request(assistance, *, role, package_id=None, task_id=None,
                            reviewer_question=""):
    """Builds the minimal request for one role over an existing 7B6 assistance
    view (any revision). Only the role's own permission row is released; the
    entire contract, the workflow and every unrelated candidate or task are
    unreachable from the result.

    Reads only. Raises TypeError for foreign objects and ValueError for unknown
    roles, unknown identities, and role preconditions the task does not meet.
    """
    if not isinstance(assistance, ReviewerAssistance):
        raise TypeError(
            f"assistance must be a ReviewerAssistance (got "
            f"{type(assistance).__name__}); the boundary projects the committed "
            "7B6 assistance view, nothing else."
        )
    if role not in ROLES:
        raise ValueError(
            f"role {role!r} is not one of the six boundary roles; the boundary "
            "refuses to invent a permission row for an unknown role."
        )
    if not isinstance(package_id, (str, type(None))):
        raise TypeError(f"package_id must be a str or None (got {type(package_id).__name__}).")
    if not isinstance(task_id, (str, type(None))):
        raise TypeError(f"task_id must be a str or None (got {type(task_id).__name__}).")
    if not isinstance(reviewer_question, str):
        raise TypeError(
            f"reviewer_question must be a str (got {type(reviewer_question).__name__}); "
            "the reviewer's own words are carried verbatim, never interpreted."
        )
    if role in _TASK_SCOPED_ROLES:
        if package_id is None or task_id is None:
            raise ValueError(
                f"role {role!r} is task-scoped and requires both package_id and "
                "task_id; the boundary refuses to build a task request without the "
                "task identity."
            )
        task = _assist_task_for(assistance, package_id, task_id)
        fields = _fields_for_role(role, task)
    else:
        if package_id is not None or task_id is not None:
            raise ValueError(
                f"role {role!r} is aggregate-scoped; it takes no package_id or "
                "task_id, and the boundary refuses to attach an identity to it."
            )
        fields = _aggregate_fields(role, assistance)
    _enforce_permission_row(role, fields)
    return AssistantRequest(
        role=role,
        package_id=package_id,
        task_id=task_id,
        revision=assistance.revision,
        reviewer_question=reviewer_question,
        fields=fields,
    )


# --------------------------------------------------------------------------------------
# The response side: what an external assistant is permitted to return, as untrusted
# display content.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AssistantReferenceClaim:
    """One reference the assistant's message claims. It is trusted ONLY when it is
    exactly equal to a source anchor already released in the request — and even a
    matching claim remains display-only text."""
    claimed_text: str
    matches_trusted_anchor: bool


@dataclass(frozen=True)
class AssistantResponse:
    """The whole untrusted assistant message, bound to the exact request it answers.
    `text` is prose, carried verbatim; `reference_claims` are the assistant's own
    claims, each flagged against the request's trusted anchors. The record has NO
    value field, NO provenance field, NO decision field and NO method at all — there
    is nothing here that any trusted layer could read as engineering state."""
    request: AssistantRequest
    text: str
    reference_claims: tuple[AssistantReferenceClaim, ...]


DISPLAY_PREFIX = "ASSISTANT MESSAGE — UNTRUSTED, DISPLAY ONLY — NOT PART OF THE REVIEW RECORD"


def _trusted_anchor_texts(request):
    """The source anchors the request actually released — the only strings an
    assistant reference claim may match."""
    return frozenset(
        field.value for field in request.fields if field.name == "source_anchor"
    )


def parse_assistant_response(request, text, reference_claims=()):
    """Wraps an external assistant's message as untrusted display content.

    Refusals (loud, before anything is built): a non-AssistantRequest request
    (a response can only exist bound to a boundary request), non-str text
    (malformed output), and any non-str claimed reference. Content of the text
    is NEVER judged here: the message is represented verbatim and contained by
    the record's own shape, whatever it says.
    """
    if not isinstance(request, AssistantRequest):
        raise TypeError(
            f"request must be an AssistantRequest (got {type(request).__name__}); "
            "an assistant response exists only attached to the exact boundary "
            "request it answers."
        )
    if not isinstance(text, str):
        raise TypeError(
            f"assistant text must be a str (got {type(text).__name__}); non-text "
            "assistant output is refused as malformed."
        )
    trusted_anchors = _trusted_anchor_texts(request)
    claims = []
    for claim in reference_claims:
        if not isinstance(claim, str):
            raise TypeError(
                f"every claimed reference must be a str (got {type(claim).__name__}); "
                "malformed reference claims are refused."
            )
        claims.append(AssistantReferenceClaim(
            claimed_text=claim,
            matches_trusted_anchor=claim in trusted_anchors,
        ))
    return AssistantResponse(request=request, text=text, reference_claims=tuple(claims))


def response_context_matches(response, request) -> bool:
    """True only when the response answers the EXACT request given — same role,
    candidate, task, contract revision, reviewer question and released fields.
    This is the staleness and identity check: a response built for another
    candidate, another task, or an older revision of the contract does not match
    a newer context, and the boundary reports that instead of attaching it."""
    if not isinstance(response, AssistantResponse):
        raise TypeError(
            f"response must be an AssistantResponse (got {type(response).__name__})."
        )
    if not isinstance(request, AssistantRequest):
        raise TypeError(
            f"request must be an AssistantRequest (got {type(request).__name__})."
        )
    return response.request == request


def render_assistant_message(response) -> str:
    """The ONLY thing SteelSpec does with assistant output: render it as a
    display-only string. The fixed prefix marks the message as untrusted; the
    text is carried verbatim; each reference claim is labelled by whether it
    matched a trusted anchor. The string is plain text — no trusted layer can
    consume it as anything but what it is."""
    if not isinstance(response, AssistantResponse):
        raise TypeError(
            f"response must be an AssistantResponse (got {type(response).__name__})."
        )
    lines = [DISPLAY_PREFIX, response.text]
    for claim in response.reference_claims:
        if claim.matches_trusted_anchor:
            lines.append(
                f"claimed source reference: {claim.claimed_text} "
                "[matches a trusted SteelSpec anchor — display only]"
            )
        else:
            lines.append(
                f"claimed source reference: {claim.claimed_text} "
                "[UNTRUSTED — not a SteelSpec source anchor]"
            )
    return "\n".join(lines)
