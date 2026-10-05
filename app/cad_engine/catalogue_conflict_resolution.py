"""
Milestone E2 — CATALOGUE CONFLICT RESOLUTION: human resolution of the
hard source-of-truth conflicts, carried on the existing review
vocabulary.

Lifecycle proven here (additive — nothing is wired into the
production pipeline, matcher, DXF path or any connection gate):

    SOURCE CONFLICT    build_catalogue_conflict()            consumes the E1 boundary
        -> 7AC TASK    build_conflict_review_task()          the existing ExceptionResolutionTask shape
        -> HUMAN 7AD   apply_conflict_resolution()           explicit decision only, fail-closed
        -> 7Z REDECIDE redecide_conflict_after_resolution()  blocked without a decision; never chooses
        -> 7AQ AUDIT   build_conflict_audit() +
                       accept_resolved_conflict()            never-conflict vs human-resolved, never confused

Why the smallest contract instead of the existing packages: the
existing 7AC/7AD/7Z/7AQ packages (ExceptionResolutionPackage,
ConnectionExceptionTasks, ResolutionRerunResult, ProductionJobAcceptance)
are connection-shaped — they carry 7Z connection blockers, 7W
supplements and per-connection stage evidence. A catalogue-level
section conflict is not a connection blocker, and forcing it into a
connection container would misrepresent it. This module therefore
reuses the established per-task vocabulary verbatim
(ExceptionResolutionTask, HumanResolution, TASK_RESOLVE_CONFLICT,
STATUS_OPEN) and adds the smallest frozen catalogue-conflict record
that carries the evidence, the decision and the audit.

HARD RULES:

  - THE SOURCE-OF-TRUTH RULE IS UNCHANGED (Milestone E1,
    app/engineering_data/source_of_truth.py):

      The engineer's drawing is authoritative for what was actually
      specified on the project. The section catalogue is authoritative
      for standard catalogue properties only where those properties do
      not conflict with the engineer's drawing. If a catalogue value
      conflicts with a geometry-defining value extracted from the
      engineer's drawing, SteelSpec MUST NOT silently choose one — it
      must remain REVIEW/BLOCKED until the conflict is resolved by an
      explicit human decision or stronger authoritative evidence.

    A conflict record can only be BUILT from a genuine
    CONFLICT_BLOCKED verdict of the E1 boundary: a pair of rows that
    agree is refused at construction, so an audit record can never
    exist for something that was never in conflict.
  - NO DEFAULT DECISION, NO INFERENCE. The only accepted human
    decisions are the three explicit strings
    (CAPTURE_AUTHORITATIVE, LIVE_CATALOGUE_AUTHORITATIVE,
    KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE) with no ranking between
    them. A missing, unknown or malformed decision fails closed. The
    decision is never inferred from names, weights, dimensions,
    family, ordering, source priority or anything else; the rationale
    is required (non-empty) and preserved verbatim.
  - EVIDENCE IS PRESERVED EXACTLY. Both source rows (captured and
    catalogue), the conflicting fields with both recorded values, the
    original E1 verdict and resolution, the human decision and its
    rationale and the resulting decision all remain recoverable on
    every record produced here. Nothing is merged, averaged, rounded,
    renamed or dropped; inputs are never mutated.
  - REDECISION NEVER CHOOSES. The resulting decision is a pure
    function of the recorded human decision alone: an applied geometry
    decision exists only when the human explicitly chose that source;
    anything else (no decision, or KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
    yields BLOCKED_REVIEW.
  - SYNTHETIC DECISIONS ARE TEST INPUTS, NEVER REAL ENGINEERING
    DECISIONS. This module never decides which source is actually
    authoritative for the project. The real Selby evidence does not
    establish either side for either known conflict; test decisions are
    synthetic fixtures, and no real conflict is marked
    production-resolved.
  - DETERMINISTIC, FROZEN, NO I/O. No network calls, no database
    access, no clock, no nondeterminism, no CAD geometry, no drawing
    generation. Every result is frozen plain data; repeated builds are
    equal, and record construction is independent of the insertion
    order of the source row keys.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from app.cad_engine.exception_resolution import (
    STATUS_OPEN,
    TASK_RESOLVE_CONFLICT,
    ExceptionResolutionTask,
    HumanResolution,
)
from app.engineering_data.source_of_truth import (
    RESOLUTION_BLOCKED_REVIEW,
    VERDICT_CONFLICT_BLOCKED,
    evaluate_section_evidence,
)

__all__ = [
    # the explicit human decisions (no ranking between them)
    "HUMAN_DECISION_CAPTURE_AUTHORITATIVE",
    "HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE",
    "HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE",
    "HUMAN_DECISIONS",
    "ANSWER_SOURCE_AUTHORITY",
    # resulting decisions and resolution statuses
    "RESULTING_DECISION_CAPTURE_GEOMETRY",
    "RESULTING_DECISION_CATALOGUE_GEOMETRY",
    "RESULTING_GEOMETRY_DECISIONS",
    "RESOLUTION_STATUS_UNRESOLVED",
    "RESOLUTION_STATUS_RESOLVED",
    # audit states
    "CONFLICT_STATE_NO_CONFLICT",
    "CONFLICT_STATE_UNRESOLVED",
    "CONFLICT_STATE_HUMAN_RESOLVED",
    "CATALOGUE_CONFLICT_SCOPE_STATEMENT",
    # frozen records
    "SourceAuthorityDecision",
    "CatalogueConflict",
    "ConflictAuditRecord",
    "ConflictAcceptance",
    # the lifecycle
    "build_catalogue_conflict",
    "build_conflict_review_task",
    "apply_conflict_resolution",
    "redecide_conflict_after_resolution",
    "resolved_geometry_fields",
    "build_conflict_audit",
    "accept_resolved_conflict",
]

# --------------------------------------------------------------------------------------
# The explicit human decisions. Exactly three, no ranking: each means only what it says.
# --------------------------------------------------------------------------------------
HUMAN_DECISION_CAPTURE_AUTHORITATIVE = "CAPTURE_AUTHORITATIVE"
HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE = "LIVE_CATALOGUE_AUTHORITATIVE"
HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE = "KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE"
HUMAN_DECISIONS = (
    HUMAN_DECISION_CAPTURE_AUTHORITATIVE,
    HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE,
    HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE,
)

# The answer type the conflict task carries (a SOURCE_AUTHORITY decision
# with its rationale). Defined here, not in 7AC: the existing 7AC package
# machinery consumes connection tasks only, and a catalogue conflict is
# addressed by this module's own fail-closed apply.
ANSWER_SOURCE_AUTHORITY = "SOURCE_AUTHORITY"

# --------------------------------------------------------------------------------------
# Resulting decisions (the re-decision stage) and resolution statuses.
# A blocked outcome reuses the E1 resolution vocabulary verbatim.
# --------------------------------------------------------------------------------------
RESULTING_DECISION_CAPTURE_GEOMETRY = "CAPTURE_GEOMETRY_APPLIED"
RESULTING_DECISION_CATALOGUE_GEOMETRY = "CATALOGUE_GEOMETRY_APPLIED"
RESULTING_GEOMETRY_DECISIONS = (
    RESULTING_DECISION_CAPTURE_GEOMETRY,
    RESULTING_DECISION_CATALOGUE_GEOMETRY,
)

RESOLUTION_STATUS_UNRESOLVED = "UNRESOLVED"
RESOLUTION_STATUS_RESOLVED = "RESOLVED"

# Audit states. CONFLICT_STATE_NO_CONFLICT is never carried by a record:
# build_catalogue_conflict() refuses non-conflicting pairs, so the
# "there was never a conflict" case is structurally impossible to
# confuse with a resolved conflict — no audit record can exist for it.
CONFLICT_STATE_NO_CONFLICT = "NO_CONFLICT"
CONFLICT_STATE_UNRESOLVED = "CONFLICT_UNRESOLVED"
CONFLICT_STATE_HUMAN_RESOLVED = "CONFLICT_HUMAN_RESOLVED"

CATALOGUE_CONFLICT_SCOPE_STATEMENT = (
    "A recorded human decision here is an explicit human input, not engineering truth: "
    "CAPTURE_AUTHORITATIVE, LIVE_CATALOGUE_AUTHORITATIVE and "
    "KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE each mean only what they say, and no ranking between the "
    "two sources exists. This module never decides which source is actually authoritative for the "
    "project; the real Selby evidence does not establish either side, test decisions are synthetic "
    "test inputs, and no real conflict is marked production-resolved."
)


# --------------------------------------------------------------------------------------
# Frozen records
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceAuthorityDecision:
    """
    What a human explicitly decided for one catalogue conflict:
    `decision` is exactly one of HUMAN_DECISIONS (validated here,
    fail-closed) and `rationale` is the human's non-empty stated
    reason, preserved verbatim (never trimmed, never rewritten). No
    default decision exists.
    """
    decision: str
    rationale: str

    def __post_init__(self):
        if self.decision not in HUMAN_DECISIONS:
            raise ValueError(
                f"a human decision must be exactly one of {list(HUMAN_DECISIONS)} "
                f"(got {self.decision!r}); no default decision exists and no decision "
                "is ever inferred."
            )
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError(
                "a human decision requires a non-empty rationale; the rationale is "
                "preserved verbatim on the conflict record."
            )


def _snapshot_values(row: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """One source row's complete values, lossless, in canonical (field-name) order —
    never dependent on the insertion order of the caller's mapping."""
    return tuple(sorted((field, copy.deepcopy(value)) for field, value in row.items()))


@dataclass(frozen=True)
class CatalogueConflict:
    """
    The frozen conflict record: everything required to prove that a
    genuine source-of-truth conflict existed, was explicitly resolved
    or not, and remains recoverable afterwards.

    `captured_values` / `catalogue_values` are the COMPLETE source rows
    (every field, lossless, in canonical field-name order);
    `conflicting_fields` is the E1 verdict's tuple of
    (field, captured_value, catalogue_value) triples verbatim;
    `original_verdict` / `original_resolution` are the E1 verdict and
    resolution verbatim; `human_decision` / `human_rationale` are set
    exactly by an explicit recorded decision (None while unresolved);
    `resulting_decision` is set by the re-decision stage;
    `resolution_status` is UNRESOLVED or RESOLVED. The original
    conflict is never collapsed: nothing here stores only a final
    selected value.
    """
    conflict_id: str
    captured_name: str
    catalogue_name: str
    captured_values: tuple[tuple[str, Any], ...]
    catalogue_values: tuple[tuple[str, Any], ...]
    conflicting_fields: tuple[tuple[str, Any, Any], ...]
    original_verdict: str
    original_resolution: str
    human_decision: str | None
    human_rationale: str | None
    resulting_decision: str | None
    resolution_status: str
    scope_statement: str = CATALOGUE_CONFLICT_SCOPE_STATEMENT

    def __post_init__(self):
        object.__setattr__(
            self, "captured_values",
            tuple((field, copy.deepcopy(value)) for field, value in self.captured_values),
        )
        object.__setattr__(
            self, "catalogue_values",
            tuple((field, copy.deepcopy(value)) for field, value in self.catalogue_values),
        )
        object.__setattr__(
            self, "conflicting_fields",
            tuple((field, copy.deepcopy(capture_value), copy.deepcopy(catalogue_value))
                  for field, capture_value, catalogue_value in self.conflicting_fields),
        )


# --------------------------------------------------------------------------------------
# SOURCE CONFLICT — the E1 boundary decides; this only records.
# --------------------------------------------------------------------------------------
def build_catalogue_conflict(
    captured_row: Mapping[str, Any],
    catalogue_row: Mapping[str, Any],
) -> CatalogueConflict:
    """
    Builds the frozen conflict record for a genuine source-of-truth
    conflict. The E1 boundary (evaluate_section_evidence) is the ONLY
    authority on whether a conflict exists: a pair whose verdict is not
    CONFLICT_BLOCKED is refused here — rows that agree are never
    conflicts, so no conflict record (and therefore no audit record)
    can ever exist for them.

    Both source rows are snapshotted complete and lossless; the
    conflicting fields, verdict and resolution come from the E1
    verdict verbatim. Read-only and deterministic: nothing is mutated,
    repeated builds are equal, and the record is independent of the
    insertion order of the source row keys.
    """
    if not isinstance(captured_row, Mapping):
        raise TypeError(
            f"captured_row must be a mapping of section fields (got "
            f"{type(captured_row).__name__})."
        )
    if not isinstance(catalogue_row, Mapping):
        raise TypeError(
            f"catalogue_row must be a mapping of section fields (got "
            f"{type(catalogue_row).__name__})."
        )
    verdict = evaluate_section_evidence(captured_row, catalogue_row)
    if verdict.status != VERDICT_CONFLICT_BLOCKED:
        raise ValueError(
            f"a catalogue conflict can only be built from a CONFLICT_BLOCKED verdict of the "
            f"E1 boundary; this pair's verdict is {verdict.status!r} "
            f"({verdict.resolution!r}) — rows that agree are never conflicts."
        )
    captured_name = captured_row.get("name")
    catalogue_name = catalogue_row.get("name")
    if not isinstance(captured_name, str) or not captured_name.strip():
        raise ValueError(
            "the captured row must carry a non-empty section name; an unnamed conflict "
            "can never be routed to review."
        )
    if not isinstance(catalogue_name, str) or not catalogue_name.strip():
        raise ValueError(
            "the catalogue row must carry a non-empty section name; an unnamed conflict "
            "can never be routed to review."
        )
    return CatalogueConflict(
        conflict_id=f"CONFLICT-{captured_name}-vs-{catalogue_name}",
        captured_name=captured_name,
        catalogue_name=catalogue_name,
        captured_values=_snapshot_values(captured_row),
        catalogue_values=_snapshot_values(catalogue_row),
        conflicting_fields=verdict.conflicting_fields,
        original_verdict=verdict.status,
        original_resolution=verdict.resolution,
        human_decision=None,
        human_rationale=None,
        resulting_decision=None,
        resolution_status=RESOLUTION_STATUS_UNRESOLVED,
    )


# --------------------------------------------------------------------------------------
# 7AC — the review task, in the existing task shape.
# --------------------------------------------------------------------------------------
def build_conflict_review_task(conflict: CatalogueConflict) -> ExceptionResolutionTask:
    """
    The 7AC-shaped review task for one catalogue conflict: the existing
    ExceptionResolutionTask, with task_type TASK_RESOLVE_CONFLICT,
    answer_type ANSWER_SOURCE_AUTHORITY, allowed_choices exactly
    HUMAN_DECISIONS, current_ai_value the exact conflicting
    (field, captured, catalogue) triples, and a question that names
    both sources, both values per field, why the item is blocked, and
    that human resolution is required. Neither side is presented as
    already correct. The task is OPEN and resolves nothing by itself;
    it is deterministic (same conflict -> same task).
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    fields = "; ".join(
        f"{field} (captured {capture_value!r}, catalogue {catalogue_value!r})"
        for field, capture_value, catalogue_value in conflict.conflicting_fields
    )
    question = (
        f"Which source is authoritative for the conflicting geometry of captured section "
        f"{conflict.captured_name!r} vs catalogue section {conflict.catalogue_name!r}? "
        f"Conflicting field(s): {fields}. The E1 source-of-truth boundary blocked automatic "
        f"resolution ({conflict.original_resolution}) and the two source records remain "
        "distinct. An explicit human decision is required."
    )
    return ExceptionResolutionTask(
        task_id=f"{conflict.conflict_id}-T01",
        task_type=TASK_RESOLVE_CONFLICT,
        blocker_codes=(),
        question=question,
        current_ai_value=conflict.conflicting_fields,
        answer_type=ANSWER_SOURCE_AUTHORITY,
        allowed_choices=HUMAN_DECISIONS,
        evidence_requirement=(
            "An explicit human decision is required — exactly one of "
            "CAPTURE_AUTHORITATIVE, LIVE_CATALOGUE_AUTHORITATIVE or "
            "KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE — with a non-empty rationale. No default "
            "decision exists and the choice between the two sources is never made "
            "automatically: without a recorded decision the conflict remains BLOCKED_REVIEW."
        ),
        # Stated rather than defaulted (J79): this task addresses the SECTION GEOMETRY fields
        # of `conflict.conflicting_fields`, which are the catalogue's own vocabulary and not
        # the review package's ENGINEERING_FIELDS. No engineering field is addressed, and
        # none is inferred from the conflicting fields, so the task states None.
        field_name=None,
    )


# --------------------------------------------------------------------------------------
# 7AD — the explicit human resolution, fail-closed.
# --------------------------------------------------------------------------------------
def apply_conflict_resolution(
    conflict: CatalogueConflict,
    resolution: HumanResolution,
) -> CatalogueConflict:
    """
    Records one explicit human decision on the conflict: returns a NEW
    record in which the decision and its rationale are recorded and the
    resolution status is RESOLVED. The original evidence (both source
    rows, conflicting fields, original verdict and resolution) is
    byte-identical; the decision is recorded separately from the source
    evidence, never written into it.

    Fail-closed, mirroring the existing 7AC apply semantics: a
    resolution that does not address this conflict's task, carries the
    wrong answer type, or carries anything other than a
    SourceAuthorityDecision (whose decision/rationale are themselves
    validated at construction) raises. A conflict is resolved at most
    once. Nothing is mutated and the resulting decision is NOT computed
    here — the re-decision stage does that.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    if not isinstance(resolution, HumanResolution):
        raise TypeError(
            f"resolution must be a HumanResolution (got {type(resolution).__name__}); the "
            "catalogue conflict resolution consumes the existing 7AC resolution vocabulary."
        )
    if conflict.resolution_status == RESOLUTION_STATUS_RESOLVED:
        raise ValueError(
            f"conflict {conflict.conflict_id!r} is already RESOLVED; a conflict is "
            "resolved at most once."
        )
    task = build_conflict_review_task(conflict)
    if resolution.task_id != task.task_id:
        raise ValueError(
            f"resolution addresses task {resolution.task_id!r}, but this conflict's review "
            f"task is {task.task_id!r}; a decision is never applied to a different conflict."
        )
    if resolution.answer_type != ANSWER_SOURCE_AUTHORITY:
        raise ValueError(
            f"task {task.task_id!r} requires answer_type {ANSWER_SOURCE_AUTHORITY!r}, got "
            f"{resolution.answer_type!r}."
        )
    answer = resolution.answer
    if not isinstance(answer, SourceAuthorityDecision):
        raise ValueError(
            f"task {task.task_id!r} requires a SourceAuthorityDecision answer "
            f"(got {answer!r}); no other payload can carry a human decision."
        )
    if answer.decision not in HUMAN_DECISIONS:
        raise ValueError(
            f"the recorded decision {answer.decision!r} is not one of {list(HUMAN_DECISIONS)}; "
            "no default decision exists and no decision is ever inferred."
        )
    return replace(
        conflict,
        human_decision=answer.decision,
        human_rationale=answer.rationale,
        resolution_status=RESOLUTION_STATUS_RESOLVED,
    )


# --------------------------------------------------------------------------------------
# 7Z — the re-decision: a pure function of the recorded human decision.
# --------------------------------------------------------------------------------------
def redecide_conflict_after_resolution(conflict: CatalogueConflict) -> CatalogueConflict:
    """
    The re-decision stage: computes the resulting decision as a pure
    function of the recorded human decision alone, and returns a new
    record carrying it. An applied geometry decision exists only when
    the human explicitly chose that source; anything else — no decision,
    or KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE — yields BLOCKED_REVIEW
    (the E1 resolution vocabulary, reused). The original evidence is
    never modified and the original conflict is never collapsed.
    Deterministic and idempotent: re-deciding an already re-decided
    record returns an equal record.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    decision = conflict.human_decision
    if decision == HUMAN_DECISION_CAPTURE_AUTHORITATIVE:
        resulting = RESULTING_DECISION_CAPTURE_GEOMETRY
    elif decision == HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE:
        resulting = RESULTING_DECISION_CATALOGUE_GEOMETRY
    else:
        resulting = RESOLUTION_BLOCKED_REVIEW
    return replace(conflict, resulting_decision=resulting)


def resolved_geometry_fields(conflict: CatalogueConflict) -> tuple[tuple[str, Any], ...]:
    """
    The geometry values that may proceed, derived ONLY from the
    recorded resulting decision: the chosen source's values for the
    conflicting fields, verbatim. Raises while the conflict is blocked
    — no geometry may proceed without a recorded applied decision, and
    nothing is ever merged or averaged.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    if conflict.resulting_decision == RESULTING_DECISION_CAPTURE_GEOMETRY:
        return tuple((field, capture_value) for field, capture_value, _ in conflict.conflicting_fields)
    if conflict.resulting_decision == RESULTING_DECISION_CATALOGUE_GEOMETRY:
        return tuple((field, catalogue_value) for field, _, catalogue_value in conflict.conflicting_fields)
    raise ValueError(
        f"conflict {conflict.conflict_id!r} is blocked "
        f"({conflict.resulting_decision or 'no re-decision has run'}); no geometry may "
        "proceed without an explicit recorded human decision that applied one source."
    )


# --------------------------------------------------------------------------------------
# 7AQ — the audit: everything recoverable, and the never-conflict /
# conflict-resolved distinction made structural.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConflictAuditRecord:
    """
    The complete audit for one conflict, every item verbatim from the
    conflict record: the original captured values, the original
    catalogue values, the conflicting field(s), the original blocked
    verdict and resolution, the human decision and rationale, the
    resulting decision, the resolution status and the conflict state.

    `conflict_state` is the mandatory distinction: CONFLICT_UNRESOLVED
    (no recorded human decision) vs CONFLICT_HUMAN_RESOLVED (a recorded
    human decision exists). NO_CONFLICT can never appear — a record can
    only be built from a genuine CONFLICT_BLOCKED verdict — so "there
    was never a conflict" is structurally impossible to confuse with
    "there was a conflict and a human explicitly resolved it".
    `resolved_geometry_fields` is the applied geometry (empty while
    blocked).
    """
    conflict_id: str
    captured_name: str
    catalogue_name: str
    captured_values: tuple[tuple[str, Any], ...]
    catalogue_values: tuple[tuple[str, Any], ...]
    conflicting_fields: tuple[tuple[str, Any, Any], ...]
    original_verdict: str
    original_resolution: str
    human_decision: str | None
    human_rationale: str | None
    resulting_decision: str | None
    resolution_status: str
    conflict_state: str
    resolved_geometry_fields: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class ConflictAcceptance:
    """
    One conflict's derived acceptance. `accepted` is True exactly when
    the audit proves a genuine conflict, an explicit recorded human
    decision with a rationale, and an applied re-decision — both source
    records and the original blocked verdict recoverable on the audit.
    `reason` names exactly which condition failed otherwise. An
    unresolved, un-re-decided or KEEP_BOTH conflict is never accepted.
    """
    conflict_id: str
    accepted: bool
    reason: str
    audit: ConflictAuditRecord


def build_conflict_audit(conflict: CatalogueConflict) -> ConflictAuditRecord:
    """The frozen audit record for one conflict; deterministic and read-only."""
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    resolved = (
        conflict.resolution_status == RESOLUTION_STATUS_RESOLVED
        and conflict.human_decision is not None
    )
    return ConflictAuditRecord(
        conflict_id=conflict.conflict_id,
        captured_name=conflict.captured_name,
        catalogue_name=conflict.catalogue_name,
        captured_values=conflict.captured_values,
        catalogue_values=conflict.catalogue_values,
        conflicting_fields=conflict.conflicting_fields,
        original_verdict=conflict.original_verdict,
        original_resolution=conflict.original_resolution,
        human_decision=conflict.human_decision,
        human_rationale=conflict.human_rationale,
        resulting_decision=conflict.resulting_decision,
        resolution_status=conflict.resolution_status,
        conflict_state=(
            CONFLICT_STATE_HUMAN_RESOLVED if resolved else CONFLICT_STATE_UNRESOLVED
        ),
        resolved_geometry_fields=(
            resolved_geometry_fields(conflict)
            if conflict.resulting_decision in RESULTING_GEOMETRY_DECISIONS
            else ()
        ),
    )


def accept_resolved_conflict(conflict: CatalogueConflict) -> ConflictAcceptance:
    """
    The acceptance boundary for one conflict's audit. Accepted only
    when the record proves: a genuine conflict (original verdict
    CONFLICT_BLOCKED), an explicit recorded human decision from the
    accepted set with a non-empty rationale, a re-decision that applied
    one source's geometry, and an audit state of CONFLICT_HUMAN_RESOLVED.
    Everything else — unresolved, un-re-decided, or
    KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE (still blocked) — is not
    accepted, with the exact failing condition named. A record that
    was never a conflict cannot exist here (construction refused it),
    so acceptance can never silently treat "no conflict" as "resolved
    conflict".
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__}); an "
            "acceptance is derived only from a genuine conflict record."
        )
    audit = build_conflict_audit(conflict)
    findings: list[str] = []
    if conflict.original_verdict != VERDICT_CONFLICT_BLOCKED:
        findings.append(
            f"the original verdict is {conflict.original_verdict!r}, not "
            f"{VERDICT_CONFLICT_BLOCKED!r}; a record that was never in conflict cannot be "
            "accepted as a resolved conflict."
        )
    if conflict.resolution_status != RESOLUTION_STATUS_RESOLVED:
        findings.append(
            f"resolution status is {conflict.resolution_status!r}; no human resolution is "
            "recorded, so the conflict is still blocked."
        )
    if conflict.human_decision not in HUMAN_DECISIONS:
        findings.append(
            f"the recorded human decision is {conflict.human_decision!r}, not one of the "
            f"explicit decisions {list(HUMAN_DECISIONS)}; without an explicit recorded "
            "decision nothing may proceed."
        )
    if not isinstance(conflict.human_rationale, str) or not conflict.human_rationale.strip():
        findings.append(
            "no human rationale is recorded; a decision without its stated reason is not "
            "an auditable human decision."
        )
    if conflict.resulting_decision not in RESULTING_GEOMETRY_DECISIONS:
        findings.append(
            f"the re-decision recorded {conflict.resulting_decision!r}, not an applied "
            f"geometry decision {list(RESULTING_GEOMETRY_DECISIONS)}; the conflict remains "
            "blocked and no geometry may proceed."
        )
    if audit.conflict_state != CONFLICT_STATE_HUMAN_RESOLVED:
        findings.append(
            f"the audit state is {audit.conflict_state!r}, not "
            f"{CONFLICT_STATE_HUMAN_RESOLVED!r}; the conflict does not carry a recorded "
            "human resolution."
        )
    accepted = not findings
    reason = (
        "the conflict is a genuine source-of-truth conflict, explicitly resolved by a "
        "recorded human decision with its rationale, and the re-decision applied the chosen "
        "source's geometry; both original source records and the original blocked verdict "
        "remain recoverable on the audit record."
        if accepted else findings[0]
    )
    return ConflictAcceptance(
        conflict_id=conflict.conflict_id,
        accepted=accepted,
        reason=reason,
        audit=audit,
    )
