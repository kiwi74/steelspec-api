"""
Milestone 7C2 — PROVIDER SELECTION RECORD CONSUMPTION (read-only
projection).

This module projects the frozen 7C1 provider-selection records into stable,
read-only inspection representations for a future audit or integration
workflow. It is CONSUMPTION ONLY.

The projection is a verbatim copy of the recorded fields — provider,
capture_digest, readiness_overall, the exact blocking_reasons, decision,
rationale and selected_against_not_ready — never a re-interpretation, never
a mutation, and never a new decision. A SELECT recorded against NOT_READY
projects as SELECT + NOT_READY with its reasons intact; the projection
cannot convert NOT_READY into READY, and DECLINE and DEFER remain distinct.

Collections list recorded decisions in a deterministic canonical order (a
content key over every record field), so the same records always produce
the same listing whatever their input order. Exact duplicate records are
refused loudly — never silently collapsed — while distinct records,
including distinct decisions about the same provider, are all preserved.

No network, no subprocess, no filesystem, no environment, no clock, no
randomness, no credentials, no provider calls, no adapter, no UI and no
decision-making anywhere in this module.
"""

from dataclasses import dataclass

from app.cad_engine.provider_readiness_gate import GATE_NOT_READY, GATE_READY
from app.cad_engine.provider_selection_decision import (
    DECISIONS,
    ProviderSelectionRecord,
)

__all__ = [
    "SelectionProjection", "SelectionCollection",
    "build_selection_projection", "build_selection_collection",
]

READINESS_VALUES = frozenset({GATE_READY, GATE_NOT_READY})


# --------------------------------------------------------------------------------------
# The projections: frozen plain data only. No methods, no I/O, no state.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SelectionProjection:
    """The read-only inspection view of one 7C1 record: every recorded
    field, copied verbatim. This is a snapshot — it cannot be changed
    (frozen), it never alters the record it was built from, and it never
    alters readiness or creates a decision."""
    provider: str
    capture_digest: str
    readiness_overall: str
    blocking_reasons: tuple[str, ...]
    decision: str
    rationale: str
    selected_against_not_ready: bool


@dataclass(frozen=True)
class SelectionCollection:
    """The read-only listing of recorded decisions: one projection per
    record, in deterministic canonical order. Duplicates are never present
    (they are refused at build time), and no source record was mutated to
    produce it."""
    projections: tuple[SelectionProjection, ...]


# --------------------------------------------------------------------------------------
# Building projections. The source of truth stays the frozen 7C1 record;
# nothing here re-derives, re-interprets or repairs it.
# --------------------------------------------------------------------------------------
def _validate_record(record):
    if not isinstance(record, ProviderSelectionRecord):
        raise TypeError(
            f"record must be a ProviderSelectionRecord (got "
            f"{type(record).__name__})."
        )
    if record.decision not in DECISIONS:
        raise ValueError(
            f"invalid decision {record.decision!r}; the projection only "
            "carries decisions the 7C1 layer accepts."
        )
    if record.readiness_overall not in READINESS_VALUES:
        raise ValueError(
            f"invalid readiness_overall {record.readiness_overall!r}; the "
            "projection only carries genuine 7B9 readiness values."
        )
    return record


def build_selection_projection(record) -> SelectionProjection:
    """Projects one 7C1 record into its stable inspection representation:
    every recorded field copied verbatim, in the record's own order of
    meaning. The projection is a snapshot — it can never alter the record,
    never alters readiness, and never creates a decision. Raises TypeError
    for a non-record and ValueError for a record carrying values the 7C1
    layer could not have recorded."""
    _validate_record(record)
    return SelectionProjection(
        provider=record.provider,
        capture_digest=record.capture_digest,
        readiness_overall=record.readiness_overall,
        blocking_reasons=record.blocking_reasons,
        decision=record.decision,
        rationale=record.rationale,
        selected_against_not_ready=record.selected_against_not_ready,
    )


def _collection_key(record):
    """The canonical ordering key: every record field, so the ordering is
    total, deterministic and independent of input order. Two records can
    only share a key by being identical — and identical records are refused
    as duplicates, never silently collapsed."""
    return (
        record.provider,
        record.capture_digest,
        record.readiness_overall,
        record.decision,
        record.rationale,
        record.blocking_reasons,
        record.selected_against_not_ready,
    )


def build_selection_collection(records) -> SelectionCollection:
    """Builds the read-only listing of recorded decisions: one projection
    per record, in deterministic canonical order. An empty tuple yields an
    empty collection. Exact duplicate records are refused loudly rather
    than collapsed; distinct records — including distinct decisions about
    the same provider — are all preserved. Raises TypeError for a non-tuple
    or a non-record element, and ValueError for duplicates or records
    carrying values the 7C1 layer could not have recorded."""
    if not isinstance(records, tuple):
        raise TypeError(
            f"records must be a tuple of ProviderSelectionRecord (got "
            f"{type(records).__name__})."
        )
    seen = set()
    for record in records:
        _validate_record(record)
        if record in seen:
            raise ValueError(
                f"duplicate selection record for {record.provider!r} "
                f"({record.decision} against {record.readiness_overall}); "
                "the collection refuses duplicates instead of silently "
                "collapsing them."
            )
        seen.add(record)
    ordered = sorted(records, key=_collection_key)
    return SelectionCollection(
        projections=tuple(
            build_selection_projection(record) for record in ordered))
