"""
Milestone 7C1 — PROVIDER SELECTION DECISION BOUNDARY (human decision
recording only).

This module records a HUMAN provider-selection decision against (a) the
frozen 7C0 provider fact capture and (b) the genuine, unmodified 7B9
readiness determination for that capture. It is RECORDING ONLY.

The boundary is absolute: this module records the human decision; it NEVER
makes it. There is no default decision, no derived decision, no
readiness-based decision and no path in this module from gate state to a
decision value. The three accepted decisions are exactly SELECT, DECLINE and
DEFER, supplied by the human caller; anything else is refused loudly.

A SELECT decision does not override 7B9. If the genuine determination for
the capture is NOT_READY, the record keeps NOT_READY — with the exact
blocking reasons the gate returned — and marks, honestly, that the human
selected against NOT_READY. DECLINE and DEFER never touch the gate either,
and no decision mutates any gate or capture state: every value here is
frozen, and a determination that the gate did not produce for this capture
is refused rather than recorded.

No network, no subprocess, no filesystem, no environment, no clock, no
randomness, no credentials, no provider calls, no adapter, no UI and no
workflow integration.
"""

import hashlib
from dataclasses import dataclass

from app.cad_engine.provider_fact_capture import (
    CapturedFact,
    ProviderFactCapture,
    build_requirement_records,
)
from app.cad_engine.provider_readiness_gate import (
    GATE_NOT_READY,
    GateDetermination,
    evaluate_readiness,
)

__all__ = [
    "DECISION_SELECT", "DECISION_DECLINE", "DECISION_DEFER", "DECISIONS",
    "ProviderSelectionRecord", "capture_digest", "record_provider_selection",
]

# --------------------------------------------------------------------------------------
# The accepted decisions — exactly three, exact strings, nothing else.
# --------------------------------------------------------------------------------------
DECISION_SELECT = "SELECT"
DECISION_DECLINE = "DECLINE"
DECISION_DEFER = "DEFER"

DECISIONS = frozenset({DECISION_SELECT, DECISION_DECLINE, DECISION_DEFER})


# --------------------------------------------------------------------------------------
# The recorded decision: frozen plain data only. No methods, no I/O, no state.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderSelectionRecord:
    """One human provider-selection decision, frozen against the readiness
    state it was decided on. `readiness_overall` and `blocking_reasons` are
    the genuine 7B9 determination for the capture, preserved exactly — a
    SELECT never changes them. `selected_against_not_ready` is True only
    when the human chose SELECT while the genuine readiness was NOT_READY:
    honest metadata, not an override. `rationale` is the human's own
    optional note, recorded verbatim."""
    provider: str
    capture_digest: str
    readiness_overall: str
    blocking_reasons: tuple[str, ...]
    decision: str
    rationale: str
    selected_against_not_ready: bool


def capture_digest(capture) -> str:
    """A deterministic representation of a 7C0 capture: the sha256 of its
    fields in authored order. The same capture always digests identically;
    a different capture digests differently. Raises TypeError for anything
    that is not a ProviderFactCapture of CapturedFacts."""
    if not isinstance(capture, ProviderFactCapture):
        raise TypeError(
            f"capture must be a ProviderFactCapture (got "
            f"{type(capture).__name__})."
        )
    if not isinstance(capture.provider, str) or not capture.provider.strip():
        raise ValueError(
            "capture provider must be a non-empty str; the record must name "
            "what the decision was about."
        )
    parts = [capture.provider]
    for fact in capture.facts:
        if not isinstance(fact, CapturedFact):
            raise TypeError(
                f"every fact must be a CapturedFact (got "
                f"{type(fact).__name__})."
            )
        parts.extend([
            fact.provider, fact.requirement_id, fact.kind,
            fact.classification, fact.status, fact.description,
            fact.source, fact.source_type, fact.note,
        ])
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def record_provider_selection(*, capture, determination, decision,
                              rationale="") -> ProviderSelectionRecord:
    """Records ONE human provider-selection decision against the genuine 7B9
    readiness for the capture.

    The decision must be explicitly supplied — it is never inferred,
    defaulted or derived from the readiness state, and anything outside
    SELECT / DECLINE / DEFER is refused. The recorded readiness is the real
    gate result for this capture: a determination the gate did not produce
    is refused, and a SELECT over NOT_READY leaves NOT_READY recorded (with
    its exact blocking reasons) and only sets the honest
    `selected_against_not_ready` flag. Raises TypeError for malformed
    inputs and ValueError for invalid decisions or mismatched
    determinations; the capture's own completeness/status/source validation
    from 7C0 applies unchanged."""
    if not isinstance(capture, ProviderFactCapture):
        raise TypeError(
            f"capture must be a ProviderFactCapture (got "
            f"{type(capture).__name__})."
        )
    if not isinstance(determination, GateDetermination):
        raise TypeError(
            f"determination must be a GateDetermination (got "
            f"{type(determination).__name__}); the recorded readiness is the "
            "7B9 gate's genuine result and nothing else."
        )
    if not isinstance(decision, str):
        raise TypeError(
            f"decision must be a str (got {type(decision).__name__}); the "
            "decision is supplied by a human and is never derived."
        )
    if decision not in DECISIONS:
        raise ValueError(
            f"invalid decision {decision!r}; the only accepted decisions are "
            "SELECT, DECLINE and DEFER."
        )
    if not isinstance(rationale, str):
        raise TypeError(
            f"rationale must be a str (got {type(rationale).__name__})."
        )
    genuine = evaluate_readiness(build_requirement_records(capture))
    if determination != genuine:
        raise ValueError(
            "determination does not match the genuine 7B9 determination for "
            "this capture; the record refuses a readiness state that the "
            "gate did not produce, so a decision can never be recorded "
            "against a forged or altered readiness state."
        )
    return ProviderSelectionRecord(
        provider=capture.provider,
        capture_digest=capture_digest(capture),
        readiness_overall=determination.overall,
        blocking_reasons=determination.not_ready_reasons,
        decision=decision,
        rationale=rationale,
        selected_against_not_ready=(
            decision == DECISION_SELECT
            and determination.overall == GATE_NOT_READY),
    )
