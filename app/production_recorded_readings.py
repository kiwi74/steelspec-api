"""
J81 — the production read path's binding to the J80 boundary.

WHAT THIS MODULE IS
===================
The request-time adapter that answers, for every task of every PERSISTED review item:

    the item's OWN recorded address      ->  what candidate does the evidence establish?
    the task's OWN J79 statement         ->  which single engineering field does it ask about?

and answers them from persisted state alone. It is the FIRST production caller of J80
(`app/cad_engine/cited_candidate_resolution.py`), and it is a READER: it composes, it
returns, and it stores nothing.

THE CHAIN, and there is no shortcut in it:

    GET /production/review/{project_id}/workflow          the route (app/main.py)
      -> build_workflow_review(project_id)                the read entry (J25's composition)
         -> read_connection_review_state(project_id)      J22's own current-state rule
            -> load_review_snapshot(project, revision)    J21's own assembly
               -> ReviewSnapshot.items                    the PERSISTED items, verbatim
                  -> read_recorded_field_readings(...)    this module
                     -> capture_history_for_drawing(drawing_id)   the WHOLE reading history
                        -> resolve_cited_reading(item, task_id, captures)   J80

Every step is an existing production read. This module adds no route, opens no route, and
is reachable from nowhere else.

WHAT IT REFUSES TO DO, each of which would be a different and unsound thing
==========================================================================
  * it does not re-implement R3, or call it directly. J80 is the boundary and this module
    goes through it, so the seam J80 established is the seam that carries production
    traffic rather than a second, parallel path beside it;
  * it does not reconstruct a candidate, and it does not read `candidate.origin` off a
    freshly reconstructed workflow. The persisted item is authoritative for the address;
    a candidate rebuilt beside it is a SECOND answer to the same question, and the two can
    disagree about a superseded attempt;
  * it does not choose the latest attempt, and it never reaches
    `authoritative_captures`. A page read more than once has several readings, the item
    names ONE of them by attempt, and the selection rule that decides which attempt STANDS
    for a page is precisely the substitution R3 must not make. The load path is the J23
    read that keeps every attempt, and the seam it is called through is declared by name in
    `app/production_connection_review.py`;
  * it does not infer `analysis_run_id` or `candidate_index`, and does not fall back to
    `submission_index`, to `review_package_id`, to the task order, to RP numbering, to
    coordinates or to proximity. Every one of those is a resemblance, and a resemblance is
    not an address;
  * it does not backfill. An item recorded before J72 carries no origin keys, and it stays
    that way: its candidate half reads as R3's `RUN_ABSENT`, which is the true answer to
    "which candidate did this item's own evidence name?" — none. Nothing here repairs it,
    and nothing here records one on its behalf;
  * it does not write. It leaves no reading row, no revision, no database row, no storage
    object and no migration behind, and it returns evidence for a later decision rather
    than taking one.

WHAT ONE READING KEEPS APART, AND WHY
=====================================
A result carries three facts and never folds them into one:

    WHICH persisted task      review_package_id / connection_id / task_id
    WHAT it asks about        field_state, field_name   (the task's own statement)
    WHAT the evidence shows   outcome                  (J80's R3 result, unchanged)

They are independent because they are decided by different things. A task can name a field
whose evidence establishes no candidate, and a task can name no field at all while the
evidence establishes one perfectly well. A single `ok` value would hide exactly those two
cases, which are the ones a caller has to be able to tell apart.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Mapping

from app.cad_engine.cited_candidate_resolution import CitedCandidateReading, resolve_cited_reading

__all__ = [
    "DRAWING_ID_KEY",
    "RecordedFieldReading",
    "read_recorded_field_readings",
    "recorded_drawing_id",
]

#: The evidence key an item states its drawing under. It is the recorded address's own term
#: (J72's `RECORDED_ORIGIN_KEYS`), read here because the load path is addressed BY DRAWING —
#: and by the drawing the ITEM named, never one a reconstruction supplied.
DRAWING_ID_KEY = "source_drawing_id"


@dataclasses.dataclass(frozen=True)
class RecordedFieldReading:
    """
    ONE persisted task of ONE persisted item, read at that item's own recorded address.

    `reading` is J80's object, carried UNCHANGED — its `task_id`, its field state and name,
    and its R3 outcome are J80's own and are not copied, repaired or re-derived here. The
    three accessors below exist so a caller does not have to reach through the wrapper, and
    they are PROPERTIES rather than stored fields on purpose: there is one copy of each
    fact, so the wrapper cannot drift from the reading it wraps.

    There is deliberately no `resolved` / `ok` / `status` field and no derived summary of
    any kind. Collapsing the field axis and the evidence axis into one value is the one
    thing this boundary must not do.
    """

    review_package_id: str
    connection_id: str | None
    reading: CitedCandidateReading

    @property
    def task_id(self) -> str:
        """The task's own recorded id — an identity, never a position in the array."""
        return self.reading.task_id

    @property
    def field_state(self) -> str:
        """`FIELD_BOUND` when the task states one engineering field, else `FIELD_NOT_SINGLE`."""
        return self.reading.field_state

    @property
    def field_name(self) -> str | None:
        """The task's own J79 statement, verbatim, or `None` when it states none."""
        return self.reading.field_name

    @property
    def outcome(self) -> Any:
        """J80's R3 result, unchanged: a `ConnectionScopedReading` or an `Unresolved`."""
        return self.reading.outcome


def recorded_drawing_id(item: Any) -> str | None:
    """
    The drawing the PERSISTED item's own evidence names, or `None` when it names none.

    `None` is not repaired into a lookup, and not filled in from the reconstruction's own
    drawing id or from another item's. An item that names no drawing has no reading history
    to load — there is no drawing to load one FOR — and the empty history is what R3 is
    given, so R3 states that absence itself rather than this adapter inventing a code for it.
    """
    evidence = getattr(item, "evidence", None)
    if not isinstance(evidence, Mapping):
        return None
    drawing_id = evidence.get(DRAWING_ID_KEY)
    return drawing_id if isinstance(drawing_id, str) and drawing_id else None


def _task_ids_of(item: Any, where: str) -> tuple[str, ...]:
    """
    Every task the item recorded, each addressed by its OWN id.

    The array's order is preserved because it is the item's own recorded order and a reader
    should see it as recorded — but order is never what a task is FOUND by. The ids below are
    what `resolve_cited_reading` looks the task up with, so a row that moved cannot be
    answered as though it were the row that moved there.
    """
    ids = []
    for row in item.tasks:
        task_id = row.get("task_id") if isinstance(row, Mapping) else None
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(
                f"{where}: a recorded task row states no task id ({row!r}). A task is "
                "addressed by its own id; a row that states none could only be addressed by "
                "its position, and a position is not an identity."
            )
        ids.append(task_id)
    return tuple(ids)


def read_recorded_field_readings(
    snapshot: Any,
    *,
    captures_for_drawing: Callable[[str], Any],
) -> tuple[RecordedFieldReading, ...]:
    """
    Every persisted task of every persisted item, with its field and its cited candidate.

    `snapshot` is the assembled `ReviewSnapshot` — the object J21's `snapshot_from_rows`
    returns and J22 hands back — and its `.items` are the PERSISTED items, one per
    connection, in the store's own `review_package_id` order. Nothing here orders, filters,
    re-keys or rebuilds them.

    `captures_for_drawing` is supplied by the caller and is the ONLY way a reading history
    enters this module. The production caller passes J23's `page_extraction_captures_for_drawing`,
    which returns the drawing's whole history with every attempt kept; it is a required
    argument, with no default, so that "which history was read" is always a visible decision
    at the call site rather than an unstated one inside here.

    The history is loaded once per DISTINCT drawing of THIS call, in first-appearance order,
    and never cached beyond it: two calls cannot see each other's, so a page re-read between
    them is read afresh rather than answered from a stale history.

    The result is deterministic: the store's item order, the item's own task order, and for
    each task exactly J80's answer for the address that item recorded.
    """
    history: dict[str, Any] = {}
    readings: list[RecordedFieldReading] = []
    for item in snapshot.items:
        where = f"recorded item {item.review_package_id!r}"
        drawing_id = recorded_drawing_id(item)
        if drawing_id is None:
            # No drawing was named, so there is no history to load. R3 is given the empty
            # history and states the absence in its own words.
            captures: Any = ()
        else:
            if drawing_id not in history:
                history[drawing_id] = captures_for_drawing(drawing_id)
            captures = history[drawing_id]
        for task_id in _task_ids_of(item, where):
            readings.append(
                RecordedFieldReading(
                    review_package_id=item.review_package_id,
                    connection_id=item.connection_id,
                    reading=resolve_cited_reading(item, task_id, captures),
                )
            )
    return tuple(readings)
