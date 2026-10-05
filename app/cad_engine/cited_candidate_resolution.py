"""
J80 — the R3 persisted-evidence consumer.

WHAT THIS MODULE IS. One read-only adapter that answers exactly one question, in the one
shape J78 established as the safe production seam:

    a PERSISTED ReviewSnapshotItem
      + the COMPLETE historical capture set for the drawing the item's own evidence names
      -> resolve_candidate_at_recorded_address(item, captures)
      -> the R3 result, unchanged

and, beside it, the field the TASK asks about — taken from the task's own J79 statement
and never derived.

WHAT IT DELIBERATELY DOES NOT DO, each of which would be a different and unsound thing:

  * it does not use reconstructed workflow candidates. The item is authoritative for the
    candidate address; a candidate rebuilt beside the item is a SECOND answer to the same
    question, and the two can disagree about a superseded attempt;
  * it does not use the CURRENT authoritative capture selection. A page read more than
    once has several readings and the item names ONE of them by attempt. Substituting the
    latest parsed attempt would silently answer with a reading the item never named;
  * it does not infer `analysis_run_id` or `candidate_index`, and does not fall back to
    `submission_index`, to `review_package_id`, to task order, to RP numbering, to
    coordinates or to proximity. Every one of those is a resemblance, and a resemblance is
    not an address;
  * it does not write. No field-evidence row, no review revision, no database write, no
    storage object, no migration. Recording which reading stands as evidence for which
    field is a LATER milestone's decision, and this module returns the evidence such a
    decision would need rather than taking it.

THE INTENDED CALLER, and this is the whole seam:

    snapshot = load_review_snapshot(project_id, revision)      # J21/J22's reader
    for item in snapshot.items:                                # the persisted item, verbatim
        drawing_id = item.evidence["source_drawing_id"]
        captures = page_extraction_captures_for_drawing(drawing_id)   # EVERY attempt
        resolution = resolve_cited_reading(item, task_id, captures)

`page_extraction_captures_for_drawing` is the ONLY load path: it returns the page's whole
reading history, every attempt included, and it decides nothing. `authoritative_captures`
is the wrong input for R3 and is deliberately not named here — it exists to decide which
attempt STANDS for a page, which is precisely the substitution R3 must not make. A test
fences this module against it.

This module imports the resolver, the review package's snapshot model and its field
vocabulary, and nothing else. It takes no database, storage or network dependency of its
own, calls no model, and mutates none of its arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.cad_engine.connection_review_snapshot import ReviewSnapshotItem, _task_from_row
from app.cad_engine.connection_scoped_evidence import (
    ConnectionScopedReading,
    Unresolved,
    resolve_candidate_at_recorded_address,
)
from app.cad_engine.review_contract import ENGINEERING_FIELDS

# --------------------------------------------------------------------------------------
# The field axis. This is the CONSUMER's vocabulary and not the resolver's: R3 states why
# a candidate could not be established, and it states nothing about fields, so the two
# axes are kept apart rather than folded into one closed set. Two states, and no third:
# a task either states the ONE engineering field it asks about, or it does not.
# --------------------------------------------------------------------------------------
FIELD_BOUND = "FIELD_BOUND"
FIELD_NOT_SINGLE = "FIELD_NOT_SINGLE"

#: Exactly the two field states. A test pins the tuple, so a third state cannot be added
#: without being declared here, the same way J71 pins its refusal codes.
FIELD_STATES = (FIELD_BOUND, FIELD_NOT_SINGLE)


@dataclass(frozen=True)
class CitedCandidateReading:
    """
    ONE persisted task's question, answered against the item's OWN recorded address.

    The two halves are kept apart on purpose, because two different things decide them and
    neither may decide the other:

      * `field_state` / `field_name` come from the TASK, and from nothing else. `field_name`
        is the task's own J79 statement, carried verbatim; it is `None` exactly when the
        task states no single engineering field, which is a fact about the task rather than
        a gap. Nothing here derives a field from the task type, the answer type, the AI
        value, the blocker order or the task order, and nothing reads the presentation
        layer's `ReviewTaskInfo.field` — that derivation is exactly what J79 replaced.
      * `outcome` is R3's result, IDENTICAL to what `resolve_cited_candidate` returned. It
        is not copied, repaired, re-addressed, or given a field it did not have.

    A reading can therefore be RECORDED against a field only when BOTH halves hold: a single
    engineering field for the record to name, and one resolved candidate for it to point at.
    A caller that finds `field_state == FIELD_NOT_SINGLE` has learned that this task's answer
    names no engineering field, however well the evidence resolved — and a caller that finds
    `Unresolved` has learned that the evidence establishes no candidate, whatever the task
    asks about. Neither half is ever repaired to suit the other.
    """

    task_id: str
    field_state: str
    field_name: str | None
    outcome: ConnectionScopedReading | Unresolved

    def __post_init__(self) -> None:
        # The two halves of the field axis cannot disagree, and neither can be stated in a
        # vocabulary the review package does not own. A name outside ENGINEERING_FIELDS is
        # refused rather than carried, for the same reason J79 refuses it on the task.
        if self.field_state not in FIELD_STATES:
            raise ValueError(
                f"{self.field_state!r} is not a field state (states: {list(FIELD_STATES)}); "
                "this boundary states a closed set, so a state outside it is a caller's "
                "error and never a new outcome."
            )
        bound = self.field_name is not None
        if bound != (self.field_state == FIELD_BOUND):
            raise ValueError(
                f"field_state {self.field_state!r} and field_name {self.field_name!r} "
                "disagree: a bound field is named and a task that states no single field "
                "names none, and the two halves are never repaired into agreement."
            )
        if bound and self.field_name not in ENGINEERING_FIELDS:
            raise ValueError(
                f"field_name must be one of {list(ENGINEERING_FIELDS)} or None "
                f"(got {self.field_name!r}); this boundary carries the task's own statement "
                "and never widens the vocabulary it is stated in."
            )
        if self.outcome is None:
            raise ValueError("a cited candidate reading always carries an R3 outcome")


# ======================================================================================
# 1. The seam. One call, one delegation, and no interpretation in between.
# ======================================================================================
def resolve_cited_candidate(
    item: ReviewSnapshotItem | Any,
    captures: Any,
) -> ConnectionScopedReading | Unresolved:
    """
    The candidate one PERSISTED review item's own recorded address names.

    `item` is the persisted item — the object `snapshot_item_from_row` returned, or any
    structure with the same `evidence` mapping. Its recorded address is the ONLY address
    read: `source_drawing_id`, `source_page`, `analysis_run_id` and `candidate_index`, all
    four of them from the item itself. `captures` is the COMPLETE historical capture set
    for that drawing, supplied by the caller — every attempt of every page, not one
    attempt's selection of them.

    The result is `resolve_candidate_at_recorded_address`'s, returned UNCHANGED. This
    function adds no rule, removes none, reorders none, and states no opinion of its own:
    every refusal R3 can state is reachable here with R3's own code and R3's own detail,
    and every resolution carries R3's own address terms and R3's own `matched_by`. A caller
    that wanted R3 could call R3; a caller that wants the SEAM calls this, and gets the
    same object.

    It is idempotent and it mutates neither argument.
    """
    return resolve_candidate_at_recorded_address(item, captures)


# ======================================================================================
# 2. The field axis, read from the task's own statement.
# ======================================================================================
def _find_task_row(item: Any, task_id: str) -> dict[str, Any]:
    """The item's OWN persisted row for one task, located by `task_id` — never by order.

    `task_id` is an identity. A task's position in the array is not one: it moves when a
    task earlier in the set appears or disappears, which is exactly why the field cannot be
    read off a position and why this function refuses to.
    """
    rows = item.tasks
    matches = [row for row in rows if row.get("task_id") == task_id]
    if not matches:
        raise KeyError(
            f"the item records no task {task_id!r}; it records "
            f"{[row.get('task_id') for row in rows]}. A task is addressed by its own id and "
            "never by its position."
        )
    if len(matches) > 1:
        raise ValueError(
            f"the item records {len(matches)} tasks with the id {task_id!r}; an item that "
            "names one id twice does not say which of them is meant, and neither does this."
        )
    return matches[0]


def cited_field_for_task(item: Any, task_id: str) -> str | None:
    """
    The ONE engineering field the named task states, or `None` when it states none.

    The value is the task's own J79 `field_name`, read back through the system's own
    deserializer and carried verbatim. Nothing is derived: not from `task_type`, not from
    `answer_type`, not from `current_ai_value`, not from the blocker codes, not from the
    task's position, and never from the review contract's `ReviewTaskInfo.field` — that
    presentation-layer label is a derivation of exactly the kind J79 removed, and it names
    `connection_id` for the identity task, which is not an engineering field at all.

    A row recorded before J79 states nothing and reads as `None`, which is what it recorded:
    a task that addressed no single engineering field. A row that states a name outside the
    vocabulary is REFUSED by the task model rather than dropped, so this function cannot
    quietly turn a bad value into an absent one.
    """
    return _task_from_row(_find_task_row(item, task_id)).field_name


# ======================================================================================
# 3. Both halves together — what a field-evidence recorder would need, and nothing more.
# ======================================================================================
def resolve_cited_reading(
    item: ReviewSnapshotItem | Any,
    task_id: str,
    captures: Any,
) -> CitedCandidateReading:
    """
    One persisted task's field, beside the candidate its own item's address names.

    `captures` must be the COMPLETE historical set for the item's drawing — the whole
    reading history, which is what `page_extraction_captures_for_drawing(drawing_id)`
    returns. A selection of it (one attempt per page, the latest parsed attempt, the
    current one) is a different input and answers a different question: a page read more
    than once would resolve to whichever attempt the selection kept rather than the attempt
    the item named. This function cannot detect that substitution, because it is given a set
    of rows and not a claim about them — which is why the load path is fenced in the module
    docstring and in the tests rather than repaired here.

    Both halves are computed independently and neither can influence the other: the field
    comes from the task, the candidate from the item's recorded address. A task that states
    no single field still gets R3's true answer, so a caller can report exactly which half
    failed; a task that states a field still gets R3's refusal when the evidence does not
    establish a candidate, and no candidate is invented to match the field.
    """
    field_name = cited_field_for_task(item, task_id)
    return CitedCandidateReading(
        task_id=task_id,
        field_state=FIELD_NOT_SINGLE if field_name is None else FIELD_BOUND,
        field_name=field_name,
        outcome=resolve_cited_candidate(item, captures),
    )
