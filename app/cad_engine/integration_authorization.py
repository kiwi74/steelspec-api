"""
Milestone 7C3 — INTEGRATION AUTHORIZATION BOUNDARY (deterministic system
rule).

This module determines whether a previously recorded 7C1 provider-selection
decision is eligible to be handed to a future integration layer. It is a
DETERMINISTIC SYSTEM RULE — never a decision of its own and never an
integration.

The critical distinction this boundary draws: provider selection is a human
decision (7C1); integration authorization is a system rule (this module).
The rule is exactly one combination:

    decision == SELECT  AND  readiness_overall == READY

Everything else is NOT_AUTHORIZED. A SELECT against NOT_READY stays
NOT_READY in the record and is NOT_AUTHORIZED here; a DECLINE or DEFER is
NOT_AUTHORIZED whatever the readiness; NOT_READY is never interpreted as
authorized whatever the decision.

AUTHORIZED means only: this recorded selection satisfies SteelSpec's
internal conditions for being handed to a future integration boundary. No
provider call occurs here, no adapter, no client, no registry, no
credentials and no integration of any kind — authorization is recorded
eligibility, nothing more.

The authorization record preserves the source selection record exactly —
provider, capture_digest, readiness_overall, the exact blocking_reasons,
decision, rationale and selected_against_not_ready — alongside the result
and, for NOT_AUTHORIZED, a deterministic reason drawn from a deliberately
small vocabulary (the decision is checked first, then the readiness). The
source record is never mutated, never re-interpreted and never repaired;
invalid or forged values are refused loudly rather than normalized.

No network, no subprocess, no filesystem, no environment, no clock, no
randomness, no credentials, no UI, no workflow mutation and no database
access.
"""

from dataclasses import dataclass

from app.cad_engine.provider_readiness_gate import GATE_NOT_READY, GATE_READY
from app.cad_engine.provider_selection_decision import (
    DECISION_SELECT,
    DECISIONS,
    ProviderSelectionRecord,
)

__all__ = [
    "AUTHORIZATION_AUTHORIZED", "AUTHORIZATION_NOT_AUTHORIZED",
    "AUTHORIZATION_RESULTS", "AuthorizationRecord", "authorize_selection",
]

# --------------------------------------------------------------------------------------
# The result vocabulary — exactly two values, deliberately no more.
# --------------------------------------------------------------------------------------
AUTHORIZATION_AUTHORIZED = "AUTHORIZED"
AUTHORIZATION_NOT_AUTHORIZED = "NOT_AUTHORIZED"

AUTHORIZATION_RESULTS = frozenset({
    AUTHORIZATION_AUTHORIZED, AUTHORIZATION_NOT_AUTHORIZED,
})

READINESS_VALUES = frozenset({GATE_READY, GATE_NOT_READY})


# --------------------------------------------------------------------------------------
# The authorization record: frozen plain data only. No methods, no I/O, no state.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AuthorizationRecord:
    """The audit record of one authorization: the source selection record's
    fields, copied verbatim, plus `result` and `reason`. `reason` is ""
    exactly when the result is AUTHORIZED; otherwise it is one of the two
    deterministic reasons (decision checked first, then readiness)."""
    provider: str
    capture_digest: str
    readiness_overall: str
    blocking_reasons: tuple[str, ...]
    decision: str
    rationale: str
    selected_against_not_ready: bool
    result: str
    reason: str


def authorize_selection(record) -> AuthorizationRecord:
    """Applies the authorization rule to one recorded 7C1 selection.

    AUTHORIZED exactly when decision is SELECT and readiness_overall is
    READY; every other combination is NOT_AUTHORIZED with a deterministic
    reason. The source record's fields are preserved verbatim — a SELECT
    against NOT_READY remains NOT_READY in the authorization record — and
    the source record itself is never mutated. Raises TypeError for a
    non-record or malformed blocking_reasons, and ValueError for decisions
    or readiness values the 7C1/7B9 layers could not have recorded; nothing
    is normalized or repaired, and no decision is ever reconstructed."""
    if not isinstance(record, ProviderSelectionRecord):
        raise TypeError(
            f"record must be a ProviderSelectionRecord (got "
            f"{type(record).__name__})."
        )
    if record.decision not in DECISIONS:
        raise ValueError(
            f"invalid decision {record.decision!r}; only decisions the 7C1 "
            "layer accepts can be authorized."
        )
    if record.readiness_overall not in READINESS_VALUES:
        raise ValueError(
            f"invalid readiness_overall {record.readiness_overall!r}; "
            "readiness is never normalized and never fabricated."
        )
    if not isinstance(record.blocking_reasons, tuple) or not all(
            isinstance(reason, str) for reason in record.blocking_reasons):
        raise TypeError(
            "blocking_reasons must be a tuple of str; the authorization "
            "never fabricates reasons, it only carries the recorded ones."
        )
    authorized = (
        record.decision == DECISION_SELECT
        and record.readiness_overall == GATE_READY)
    if authorized:
        result = AUTHORIZATION_AUTHORIZED
        reason = ""
    elif record.decision != DECISION_SELECT:
        result = AUTHORIZATION_NOT_AUTHORIZED
        reason = f"decision is not {DECISION_SELECT!r}"
    else:
        result = AUTHORIZATION_NOT_AUTHORIZED
        reason = f"readiness is {GATE_NOT_READY!r}"
    return AuthorizationRecord(
        provider=record.provider,
        capture_digest=record.capture_digest,
        readiness_overall=record.readiness_overall,
        blocking_reasons=record.blocking_reasons,
        decision=record.decision,
        rationale=record.rationale,
        selected_against_not_ready=record.selected_against_not_ready,
        result=result,
        reason=reason,
    )
