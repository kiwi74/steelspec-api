"""
DXF DROP ACCOUNTING — what the DXF reader did NOT extract, and why.

The DXF reader can see evidence the drawing states and fail to turn it into a
row. Until this module existed, several of those failures produced nothing at
all: no row, no review item, no warning, no count — the drawing's own evidence
was simply absent from the schedule, the report and the reviewer's queue, and
nothing said so. A reader of that report could not tell a drawing with three
members from a drawing with thirty where twenty-seven were unreadable.

This module is the vocabulary and the collector for saying so. Its rule is that
a discard must be DETECTED, COUNTED, REASONED and then SURVIVED to the project
and the report — never silently absorbed.

WHAT A RECORD IS, AND WHAT IT IS NOT
------------------------------------
A record states that ONE drawn item was not extracted (or was extracted and not
associated), with a stable machine-readable `reason` and a short `context`
naming what it was. It never carries an engineering value: no length, no
weight, no section, no grade is invented to stand in for the missing item. What
was not read stays unread — the record only makes the absence visible, exactly
as app/drawing_reading/dxf_parser.py already persists an unresolved section
rather than substituting one.

`kind` says which part of the reader lost it; `reason` says why; `category`
groups the reasons into the three distinctions the accounting must not blur:

  NOT_EXTRACTED            evidence the drawing states that this reader did not
                           turn into a row — a callout it recognised but could
                           not place, an annotation on a layer it does not read,
                           connection detail it could not read out of a callout.
  UNSUPPORTED_BY_DESIGN    a shape this reader has never read (the reader's
                           geometry vocabulary is LINE, so a member drawn as a
                           polyline looks to it like a drawing with no members).
                           Reported so the loss is visible, not as an error.
  NOT_ASSOCIATED           the item WAS extracted, but could not be linked to
                           another one.

Nothing here is an error report. A discard may be perfectly correct — a detail
tag that is not a connection, a hatch boundary that is not a member. The
accounting exists so that the report never implies "everything was extracted"
when something was not, and so that the reader can never be silent about it.
The report's wording follows the same rule, and the phrases below are written
to be factual rather than alarming.

WHY THIS LIVES IN app/validation
--------------------------------
For the same reason SUBSTITUTION_NOTE and UNRESOLVED_SECTION_NOTE do: this is
vocabulary SHARED between the DXF reader that produces it and the report and
project record that must carry it, and both paths already depend on this
package. It also keeps app/drawing_reading/dxf_parser.py's structural guarantee
intact — that module imports nothing that could build geometry or a drawing
artifact — since a sibling module inside that package would have broken it.

WHY THE PERSISTED FORM IS AGGREGATED
------------------------------------
The individual records are what the parser returns; the strings this module
hands to the project record and the report are aggregated per reason, because a
real drawing can discard hundreds of items and a project's warning list is read
by a person. Both forms are deterministic: records keep encounter order (the
DXF's own entity order), and the reason lines keep the order the reasons were
first encountered.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

# --------------------------------------------------------------------------
# KIND — which part of the reader did not turn this into a row.
# --------------------------------------------------------------------------
KIND_MEMBER_CALLOUT = "MEMBER_CALLOUT"
KIND_SOURCE_GEOMETRY = "SOURCE_GEOMETRY"
KIND_CONNECTION_CALLOUT = "CONNECTION_CALLOUT"
KIND_CONNECTION_EVIDENCE = "CONNECTION_EVIDENCE"
KIND_CONNECTION_ASSOCIATION = "CONNECTION_ASSOCIATION"

# --------------------------------------------------------------------------
# REASON — why. Stable, machine-readable, and never reworded: these strings are
# persisted and are what a test or a later reader matches on.
# --------------------------------------------------------------------------
# A section callout this reader recognised, for which no unused LINE was found
# to pair it with. The member is NOT written, and no length is invented for it.
REASON_NO_PAIRED_LINE = "NO_PAIRED_LINE"
# A text that states a section this reader's own vocabulary recognises, sitting
# on a layer this reader does not read as member text.
REASON_UNRECOGNISED_TEXT_LAYER = "UNRECOGNISED_TEXT_LAYER"
# Geometry drawn as something other than a LINE. The reader's geometry
# vocabulary is LINE-only (see the module docstring).
REASON_UNSUPPORTED_SOURCE_GEOMETRY = "UNSUPPORTED_SOURCE_GEOMETRY"
# A connection callout that states none of the shapes the connection gate
# accepts. Deliberate and unchanged — recorded so the omission is visible.
REASON_UNSUPPORTED_CALLOUT_SHAPE = "UNSUPPORTED_CALLOUT_SHAPE"
# A connection callout that passed the gate, from which no bolt, plate or weld
# could be read. The connection row is still written; its detail is absent.
REASON_NO_CHILD_EVIDENCE_PARSED = "NO_CHILD_EVIDENCE_PARSED"
# An extracted connection with no member midpoint inside the association
# distance. The connection row is still written; it joins nothing.
REASON_NO_MEMBER_WITHIN_THRESHOLD = "NO_MEMBER_WITHIN_THRESHOLD"

# --------------------------------------------------------------------------
# CATEGORY — the distinction that must not be blurred (see the docstring).
# --------------------------------------------------------------------------
CATEGORY_NOT_EXTRACTED = "NOT_EXTRACTED"
CATEGORY_UNSUPPORTED_BY_DESIGN = "UNSUPPORTED_BY_DESIGN"
CATEGORY_NOT_ASSOCIATED = "NOT_ASSOCIATED"

REASON_CATEGORY = MappingProxyType({
    REASON_NO_PAIRED_LINE: CATEGORY_NOT_EXTRACTED,
    REASON_UNRECOGNISED_TEXT_LAYER: CATEGORY_NOT_EXTRACTED,
    REASON_NO_CHILD_EVIDENCE_PARSED: CATEGORY_NOT_EXTRACTED,
    REASON_UNSUPPORTED_SOURCE_GEOMETRY: CATEGORY_UNSUPPORTED_BY_DESIGN,
    REASON_UNSUPPORTED_CALLOUT_SHAPE: CATEGORY_UNSUPPORTED_BY_DESIGN,
    REASON_NO_MEMBER_WITHIN_THRESHOLD: CATEGORY_NOT_ASSOCIATED,
})

# What each reason is told to the reader. Factual, in the same voice
# app/validation/rules.py uses for its notes: it says what happened, and it
# does not call a discarded item an error.
REASON_PHRASE = MappingProxyType({
    REASON_NO_PAIRED_LINE: "Section callouts not extracted (no matching line found)",
    REASON_UNRECOGNISED_TEXT_LAYER: (
        "Section callouts not extracted (annotation on a layer this reader does not read)"
    ),
    REASON_UNSUPPORTED_SOURCE_GEOMETRY: (
        "Geometry not read (drawn as something other than a LINE)"
    ),
    REASON_UNSUPPORTED_CALLOUT_SHAPE: (
        "Connection callouts not extracted (not stated as bolts, plate or weld)"
    ),
    REASON_NO_CHILD_EVIDENCE_PARSED: (
        "Connection detail not extracted (no bolt, plate or weld readable in the callout)"
    ),
    REASON_NO_MEMBER_WITHIN_THRESHOLD: (
        "Connections not associated with a member (no member within the association distance)"
    ),
})

# Every persisted DXF accounting line begins with this, and the report finds the
# accounting among a project's warnings by it. Defined once, here, so the reader
# that writes it and the report that reads it cannot drift apart.
WARNING_PREFIX = "DXF extraction: "

# How many discarded items a single reason line names before it summarises the
# rest. The COUNT is never capped — only the list of names.
CONTEXT_LIMIT = 8


@dataclass(frozen=True)
class DXFDrop:
    """One drawn item this reader did not turn into a row, and why."""

    kind: str
    reason: str
    context: str = ""

    @property
    def category(self) -> str:
        # An unrecognised reason is reported as unsupported-by-design rather
        # than as an extraction failure: the reader's own vocabulary should
        # never claim more about a discard than it knows.
        return REASON_CATEGORY.get(self.reason, CATEGORY_UNSUPPORTED_BY_DESIGN)

    @property
    def phrase(self) -> str:
        return REASON_PHRASE.get(self.reason, self.reason)

    def as_dict(self) -> dict:
        """The machine-readable record: no engineering values, ever."""
        return {"kind": self.kind, "reason": self.reason, "context": self.context}


class DXFDropAccounting:
    """
    The collector the DXF reader records discards into, in encounter order.

    Deliberately tiny: `record` while reading, then `as_dicts` for the caller
    that wants each item and `warnings` for the project record and the report.
    It holds no engineering state and decides nothing — it only remembers what
    the reader says it could not read.
    """

    def __init__(self) -> None:
        self._drops: list[DXFDrop] = []

    def record(self, kind: str, reason: str, context: str = "") -> None:
        self._drops.append(DXFDrop(kind=kind, reason=reason, context=context))

    def __len__(self) -> int:
        return len(self._drops)

    def __bool__(self) -> bool:
        return bool(self._drops)

    @property
    def drops(self) -> tuple[DXFDrop, ...]:
        return tuple(self._drops)

    def as_dicts(self) -> list[dict]:
        return [drop.as_dict() for drop in self._drops]

    def by_reason(self) -> dict[str, int]:
        """Counts per reason, in the order the reasons were first encountered."""
        counts: dict[str, int] = {}
        for drop in self._drops:
            counts[drop.reason] = counts.get(drop.reason, 0) + 1
        return counts

    def by_category(self) -> dict[str, int]:
        """Counts per category, in the order the categories were first encountered."""
        counts: dict[str, int] = {}
        for drop in self._drops:
            counts[drop.category] = counts.get(drop.category, 0) + 1
        return counts

    def headline(self) -> str:
        """
        The one sentence that says the extraction was not complete.

        Returns "" when nothing was discarded, so a clean extraction persists no
        accounting at all — absence of a disclosure means nothing was discarded,
        never that nothing was checked.
        """
        by_category = self.by_category()
        not_extracted = sum(
            count for category, count in by_category.items()
            if category != CATEGORY_NOT_ASSOCIATED
        )
        not_associated = by_category.get(CATEGORY_NOT_ASSOCIATED, 0)
        if not not_extracted and not not_associated:
            return ""

        clauses = []
        if not_extracted:
            clauses.append(
                f"{not_extracted} drawing evidence {'item' if not_extracted == 1 else 'items'} "
                f"{'was' if not_extracted == 1 else 'were'} not extracted"
            )
        if not_associated:
            clauses.append(
                f"{not_associated} connection{'' if not_associated == 1 else 's'} "
                f"{'was' if not_associated == 1 else 'were'} not associated with a member"
            )
        return WARNING_PREFIX + " and ".join(clauses) + "."

    def warnings(self) -> list[str]:
        """
        The lines the project record and the report carry: the headline first,
        then one line per reason with its exact count and the names of what was
        discarded (capped, with the remainder summarised — the count is not).
        """
        headline = self.headline()
        if not headline:
            return []

        lines = [headline]
        for reason, count in self.by_reason().items():
            contexts = [drop.context for drop in self._drops if drop.reason == reason and drop.context]
            line = f"{REASON_PHRASE.get(reason, reason)}: {count}"
            if contexts:
                line += " — " + ", ".join(contexts[:CONTEXT_LIMIT])
                remaining = len(contexts) - CONTEXT_LIMIT
                if remaining > 0:
                    line += f", and {remaining} more"
            lines.append(line)
        return lines


def headline_from_warnings(warnings) -> str | None:
    """
    The DXF drop headline among a project's persisted warnings, or None.

    This is how the report knows the extraction was not complete without
    guessing from prose: the accounting writes the line, and this reads it back
    by the one prefix defined above.
    """
    for warning in warnings or []:
        if isinstance(warning, str) and warning.startswith(WARNING_PREFIX):
            return warning
    return None
