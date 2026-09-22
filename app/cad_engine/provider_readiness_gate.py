"""
Milestone 7B9 — EXTERNAL PROVIDER READINESS GATE (provider-neutral).

This module is the deterministic, provider-neutral gate between the proven
7B7/7B8 assistant boundary and ANY future real external provider. It defines,
as frozen plain data, exactly two things:

  1. The readiness prerequisite set — a fixed inventory of independent
     requirements: 19 provider facts and 6 SteelSpec local controls. Each is
     an independent record (identity, kind, classification, status, source,
     note). Statuses are exactly SATISFIED / UNSATISFIED / UNVERIFIED. There
     is NO numerical composite, NO weighting, NO ordering-based precedence,
     and NO automatic provider choice of any kind. The only overall
     determination is READY or NOT_READY, derived from independent
     prerequisite satisfaction.
  2. The wire projection — the deterministic transport representation of a
     genuine 7B7 AssistantRequest, containing ONLY the role and the released
     fields. The 7B7 permission table remains the single source of truth for
     what a field is: fields were permission-checked when the boundary built
     the request, and this module copies them verbatim. Bookkeeping
     (package_id, revision, reviewer_question) never crosses the wire.

What this module NEVER does:

  - It never manufactures provider facts, never marks any requirement
    satisfied on its own authority, and never infers satisfaction from
    anything local (not from 7B7/7B8 passing, not from local tests, not from
    an API-shaped object, not from a provider name).
  - It never chooses, approves, recommends, selects or integrates a provider.
    READY means only: the prerequisites represented by this gate have been
    recorded satisfied. The decision to integrate remains a human decision
    this gate cannot make.
  - It never parses, converts, corrects or re-formats a released observation:
    the wire projection is transport serialization only and carries released
    values byte-for-byte.
  - It knows no provider. There are no provider names, no provider URLs, no
    credentials, no provider clients and no provider-specific configuration
    anywhere in this module.

The gate fails closed: a fresh gate with no records is NOT_READY, and every
input problem is refused loudly rather than repaired silently.
"""

from dataclasses import dataclass

from app.cad_engine.assistant_message_boundary import (
    AssistantRequest,
    AssistantRequestField,
)

__all__ = [
    # classification constants
    "CLASSIFICATION_BEFORE_ANY_REAL_CALL",
    "CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION",
    "CLASSIFICATION_BEFORE_PRODUCTION_USE",
    "CLASSIFICATION_OPTIONAL_LATER",
    "CLASSIFICATIONS",
    # status constants
    "STATUS_SATISFIED", "STATUS_UNSATISFIED", "STATUS_UNVERIFIED", "STATUSES",
    # kind constants
    "KIND_PROVIDER_FACT", "KIND_LOCAL_CONTROL", "KINDS",
    # gate determination constants
    "GATE_READY", "GATE_NOT_READY",
    # the pinned inventory
    "REQUIREMENT_INVENTORY", "REQUIREMENT_IDS", "PROVIDER_FACT_IDS",
    "LOCAL_CONTROL_IDS", "REQUIREMENT_CLASSIFICATIONS",
    # records and results
    "RequirementRecord", "RequirementEvaluation", "GateDetermination",
    "WireProjection",
    # functions
    "build_wire_projection", "evaluate_readiness",
]

# --------------------------------------------------------------------------------------
# Classifications. Four independent buckets, no ordering and no precedence: a
# requirement blocks readiness purely by not being SATISFIED while classified
# anything other than OPTIONAL_LATER.
# --------------------------------------------------------------------------------------
CLASSIFICATION_BEFORE_ANY_REAL_CALL = "BEFORE_ANY_REAL_CALL"
CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION = "BEFORE_TECHNICAL_INTEGRATION"
CLASSIFICATION_BEFORE_PRODUCTION_USE = "BEFORE_PRODUCTION_USE"
CLASSIFICATION_OPTIONAL_LATER = "OPTIONAL_LATER"

CLASSIFICATIONS = frozenset({
    CLASSIFICATION_BEFORE_ANY_REAL_CALL,
    CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
    CLASSIFICATION_BEFORE_PRODUCTION_USE,
    CLASSIFICATION_OPTIONAL_LATER,
})

# --------------------------------------------------------------------------------------
# Statuses. Exactly three, for provider facts and local controls alike.
# --------------------------------------------------------------------------------------
STATUS_SATISFIED = "SATISFIED"
STATUS_UNSATISFIED = "UNSATISFIED"
STATUS_UNVERIFIED = "UNVERIFIED"

STATUSES = frozenset({STATUS_SATISFIED, STATUS_UNSATISFIED, STATUS_UNVERIFIED})

# --------------------------------------------------------------------------------------
# Kinds: provider facts (documented evidence from outside SteelSpec) versus local
# controls (SteelSpec-side guarantees proven by SteelSpec's own tests).
# --------------------------------------------------------------------------------------
KIND_PROVIDER_FACT = "PROVIDER_FACT"
KIND_LOCAL_CONTROL = "LOCAL_CONTROL"

KINDS = frozenset({KIND_PROVIDER_FACT, KIND_LOCAL_CONTROL})

# --------------------------------------------------------------------------------------
# Overall gate determination. Only these two values ever exist.
# --------------------------------------------------------------------------------------
GATE_READY = "READY"
GATE_NOT_READY = "NOT_READY"

# --------------------------------------------------------------------------------------
# The pinned requirement inventory — the 7B9 discovery report's retained set.
#
# REQUIREMENT_INVENTORY is a fixed authored presentation order ONLY: it carries no
# precedence, no weighting and no ordering-based meaning. The semantic inventory is
# the frozensets below (REQUIREMENT_IDS, PROVIDER_FACT_IDS, LOCAL_CONTROL_IDS,
# REQUIREMENT_CLASSIFICATIONS); evaluation iterates the authored order purely so
# results are deterministic and readable.
#
# Provider facts (19): API input/output contract; authentication and credential
# handling; data retention; data training/use policy; data residency/location;
# subprocessors; privacy/contractual terms; security controls for submitted
# information; error semantics; availability SLO; timeout/cancellation semantics;
# rate limits; cost model; model/version recording; model/version pinning;
# deprecation/version-change behaviour; request/response size limits;
# incident/breach notification terms; streaming behaviour.
#
# Local controls (6): the wire projection (only role + released fields cross);
# no tools (no tool/function definitions are ever sent); no retry resend (released
# content is never silently resent); stateless turns (no cross-turn conversation
# state); response containment (provider responses stay behind the 7B7/7B8
# containment); local cost cap (a per-session cost guard exists).
# --------------------------------------------------------------------------------------
REQUIREMENT_INVENTORY = (
    # --- provider facts, BEFORE_ANY_REAL_CALL ---
    ("DATA_RETENTION", KIND_PROVIDER_FACT, CLASSIFICATION_BEFORE_ANY_REAL_CALL),
    ("DATA_TRAINING_USE_POLICY", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_ANY_REAL_CALL),
    ("SUBPROCESSORS", KIND_PROVIDER_FACT, CLASSIFICATION_BEFORE_ANY_REAL_CALL),
    ("PRIVACY_CONTRACTUAL_TERMS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_ANY_REAL_CALL),
    ("SECURITY_CONTROLS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_ANY_REAL_CALL),
    # --- provider facts, BEFORE_TECHNICAL_INTEGRATION ---
    ("API_INPUT_OUTPUT_CONTRACT", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("AUTHENTICATION_CREDENTIAL_HANDLING", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("ERROR_SEMANTICS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("TIMEOUT_CANCELLATION_SEMANTICS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("RATE_LIMITS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("REQUEST_RESPONSE_SIZE_LIMITS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("MODEL_VERSION_RECORDING", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    # --- provider facts, BEFORE_PRODUCTION_USE ---
    ("AVAILABILITY_SLO", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_PRODUCTION_USE),
    ("COST_MODEL", KIND_PROVIDER_FACT, CLASSIFICATION_BEFORE_PRODUCTION_USE),
    ("MODEL_VERSION_PINNING", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_PRODUCTION_USE),
    ("DEPRECATION_VERSION_CHANGE_BEHAVIOUR", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_PRODUCTION_USE),
    ("DATA_RESIDENCY", KIND_PROVIDER_FACT, CLASSIFICATION_BEFORE_PRODUCTION_USE),
    ("INCIDENT_BREACH_NOTIFICATION_TERMS", KIND_PROVIDER_FACT,
     CLASSIFICATION_BEFORE_PRODUCTION_USE),
    # --- provider facts, OPTIONAL_LATER ---
    ("STREAMING_BEHAVIOUR", KIND_PROVIDER_FACT, CLASSIFICATION_OPTIONAL_LATER),
    # --- local controls, BEFORE_TECHNICAL_INTEGRATION ---
    ("WIRE_PROJECTION", KIND_LOCAL_CONTROL,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("NO_TOOLS", KIND_LOCAL_CONTROL, CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("NO_RETRY_RESEND", KIND_LOCAL_CONTROL,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("STATELESS_TURNS", KIND_LOCAL_CONTROL,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("RESPONSE_CONTAINMENT", KIND_LOCAL_CONTROL,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
    ("LOCAL_COST_CAP", KIND_LOCAL_CONTROL,
     CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION),
)

REQUIREMENT_IDS = frozenset(entry[0] for entry in REQUIREMENT_INVENTORY)
PROVIDER_FACT_IDS = frozenset(
    entry[0] for entry in REQUIREMENT_INVENTORY
    if entry[1] == KIND_PROVIDER_FACT)
LOCAL_CONTROL_IDS = frozenset(
    entry[0] for entry in REQUIREMENT_INVENTORY
    if entry[1] == KIND_LOCAL_CONTROL)
REQUIREMENT_CLASSIFICATIONS = frozenset(
    (entry[0], entry[2]) for entry in REQUIREMENT_INVENTORY)


# --------------------------------------------------------------------------------------
# The requirement record: one independent prerequisite, as supplied to the gate.
# The gate reads these; it never writes them and never supplies its own.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RequirementRecord:
    """One recorded prerequisite. For a provider fact, `source` is the reference
    for the recorded status (a SATISFIED provider fact without a source is
    refused — the gate never takes a bare claim). For a local control the source
    is optional: the control's proof is SteelSpec's own test suite. `note` is
    optional factual context and changes no determination."""
    requirement_id: str
    kind: str
    classification: str
    status: str
    source: str = ""
    note: str = ""


# --------------------------------------------------------------------------------------
# The wire projection: the ONLY thing that may cross the provider boundary.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class WireProjection:
    """The exact provider-facing wire representation of one approved 7B7 request:
    `role` and the released `fields`, nothing else. The fields are the boundary's
    own AssistantRequestField records, copied byte-for-byte — values are never
    normalised, converted, interpreted, corrected, parsed or re-formatted here;
    this is transport serialization only, and it makes no engineering decision.
    Bookkeeping (package_id, revision, reviewer_question), the request object
    itself, workflow state and credentials have no representation here."""
    role: str
    fields: tuple[AssistantRequestField, ...]


def build_wire_projection(request) -> WireProjection:
    """Projects a genuine 7B7 AssistantRequest onto the wire: role + released
    fields only. The 7B7 permission table remains the single source of truth for
    what a released field is — the request's fields were permission-checked when
    the boundary built them, and this function copies them verbatim without a
    second, different permission table. Raises TypeError for anything that is not
    an AssistantRequest; never serializes bookkeeping."""
    if not isinstance(request, AssistantRequest):
        raise TypeError(
            f"request must be an AssistantRequest (got {type(request).__name__}); "
            "the wire projection projects the approved 7B7 boundary request and "
            "nothing else."
        )
    return WireProjection(role=request.role, fields=request.fields)


# --------------------------------------------------------------------------------------
# The gate determination: one frozen result, with the per-requirement evaluations
# and the blocking reasons that explain it. READY means only that the prerequisites
# represented by this gate are recorded satisfied.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RequirementEvaluation:
    """The gate's reading of one requirement: the recorded (or missing) status and
    whether it blocks readiness. `blocks` is True only when the requirement is
    required (not OPTIONAL_LATER) and its status is not SATISFIED; `reason` is
    non-empty exactly when the requirement blocks."""
    requirement_id: str
    kind: str
    classification: str
    status: str
    required: bool
    blocks: bool
    reason: str


@dataclass(frozen=True)
class GateDetermination:
    """The whole gate result. `overall` is READY or NOT_READY. READY does not
    mean provider suitability, quality, production approval, engineering
    approval, user approval or commercial approval, and it does not authorise
    integration — the decision to integrate a provider is a human decision this
    record cannot make."""
    overall: str
    evaluations: tuple[RequirementEvaluation, ...]
    not_ready_reasons: tuple[str, ...]


def _validate_records(records):
    """Loud refusal of every malformed or illegitimate input. Nothing here is
    repaired silently: the gate fails closed."""
    if not isinstance(records, tuple):
        raise TypeError(
            f"records must be a tuple of RequirementRecord (got "
            f"{type(records).__name__})."
        )
    seen = set()
    by_id = {}
    for record in records:
        if not isinstance(record, RequirementRecord):
            raise TypeError(
                f"every record must be a RequirementRecord (got "
                f"{type(record).__name__})."
            )
        if not isinstance(record.source, str):
            raise TypeError(
                f"source must be a str (got {type(record.source).__name__})."
            )
        if not isinstance(record.note, str):
            raise TypeError(
                f"note must be a str (got {type(record.note).__name__})."
            )
        if record.requirement_id not in REQUIREMENT_IDS:
            raise ValueError(
                f"unknown requirement {record.requirement_id!r}; the gate refuses "
                "a record for a requirement the inventory does not carry."
            )
        if record.requirement_id in seen:
            raise ValueError(
                f"duplicate record for requirement {record.requirement_id!r}; the "
                "gate refuses contradictory duplicate records instead of choosing "
                "between them."
            )
        seen.add(record.requirement_id)
        if record.kind not in KINDS:
            raise ValueError(
                f"invalid kind {record.kind!r} for requirement "
                f"{record.requirement_id!r}."
            )
        if record.status not in STATUSES:
            raise ValueError(
                f"invalid status {record.status!r} for requirement "
                f"{record.requirement_id!r}; status must be one of SATISFIED, "
                "UNSATISFIED, UNVERIFIED."
            )
        if record.classification not in CLASSIFICATIONS:
            raise ValueError(
                f"invalid classification {record.classification!r} for "
                f"requirement {record.requirement_id!r}."
            )
        expected_classification = next(
            classification
            for (requirement_id, classification) in REQUIREMENT_CLASSIFICATIONS
            if requirement_id == record.requirement_id)
        if record.classification != expected_classification:
            raise ValueError(
                f"classification {record.classification!r} for requirement "
                f"{record.requirement_id!r} does not match the inventory "
                f"classification {expected_classification!r}; the inventory "
                "classifications are fixed."
            )
        if (record.kind == KIND_PROVIDER_FACT
                and record.status == STATUS_SATISFIED
                and not record.source.strip()):
            raise ValueError(
                f"requirement {record.requirement_id!r} is recorded SATISFIED "
                "without a source; a provider fact cannot be satisfied merely by "
                "being asserted, and the gate never manufactures a source."
            )
        by_id[record.requirement_id] = record
    return by_id


def evaluate_readiness(records) -> GateDetermination:
    """The whole gate: validates the supplied records (loudly refusing anything
    malformed) and evaluates every inventory requirement independently.

    READY iff every required requirement (anything not OPTIONAL_LATER) is
    SATISFIED. A missing record counts as UNVERIFIED and blocks when required.
    OPTIONAL_LATER requirements never block: their status is reported but cannot
    change the determination. Provider facts are never auto-satisfied — a fresh
    gate with no records is NOT_READY, with one reason per required requirement.
    """
    by_id = _validate_records(records)
    evaluations = []
    blocking_reasons = []
    for requirement_id, kind, classification in REQUIREMENT_INVENTORY:
        required = classification != CLASSIFICATION_OPTIONAL_LATER
        record = by_id.get(requirement_id)
        if record is None:
            status = STATUS_UNVERIFIED
            if required:
                reason = (
                    f"required {kind.lower().replace('_', ' ')} "
                    f"{requirement_id} has no record and is UNVERIFIED."
                )
                blocks = True
            else:
                reason = ""
                blocks = False
        else:
            status = record.status
            if required and status != STATUS_SATISFIED:
                reason = (
                    f"required {kind.lower().replace('_', ' ')} "
                    f"{requirement_id} is {status}."
                )
                blocks = True
            else:
                reason = ""
                blocks = False
        evaluations.append(RequirementEvaluation(
            requirement_id=requirement_id,
            kind=kind,
            classification=classification,
            status=status,
            required=required,
            blocks=blocks,
            reason=reason,
        ))
        if blocks:
            blocking_reasons.append(reason)
    overall = GATE_NOT_READY if blocking_reasons else GATE_READY
    return GateDetermination(
        overall=overall,
        evaluations=tuple(evaluations),
        not_ready_reasons=tuple(blocking_reasons),
    )
