"""
The durable PDF annotation-evidence layer (Milestone J28).

WHAT THIS MODULE IS

The record of the annotation occurrences a deterministic PDF reading actually found, page
by page, and the rule that decides which reading stands for a page.

The reading itself is produced by `app.drawing_reading.pdf_annotation_extractor`, which
reads the PDF's own text-showing operators and vector geometry and nothing else. This
module is the layer that lays that reading down in rows, and it decides nothing about it.
It does not re-derive a coordinate, re-classify a run, re-tokenise a mark or re-run the
schedule test; it carries the fields across and refuses a reading it cannot carry whole.

WHAT THIS MODULE IS NOT

It is not a member-identity layer, and this is the point of the whole milestone. A row here
records that AN ANNOTATION OCCURRENCE EXISTS at a page coordinate. It does not record that
a steel member exists.

  * `mark_candidate` is a CANDIDATE. The grammar `^[A-Z]{1,3}\\d{1,3}[A-Z]?$` is a shape
    test on characters, and the real set proves it admits `S207` (14 times on one page),
    `X4`, `W01`, `SG8`, `D19`, `SDM049` and `SET153` — sheet codes, grid references and
    product codes that are not member marks. Nothing here may promote a candidate.
  * Two rows are TWO ANNOTATIONS. They are not two members, and two rows sharing a
    `mark_candidate` are not one member drawn twice. There is no column, and there may
    never be a column, joining two occurrences into a physical member.
  * There is deliberately NO `is_steel_member`, `member_id`, `placement` or `same_member`
    column, and there is deliberately no field on the extractor's payload that could be
    renamed into one. A reader wanting to know whether an occurrence names a member must
    ask a human, which is the review step this layer exists to feed.

Nothing here is derived from `steel_members`, `connections` or `review_items`, and nothing
is normalised, merged, deduplicated or repaired on the way in or out. In particular an
overprint is NOT a duplicate row: two byte-identical operators at one coordinate are one
occurrence with `duplicate_operator_count` set, and the count is preserved rather than the
second row being written or dropped.

WHY THE KEY IS (drawing_id, page_number, analysis_run_id, annotation_x, annotation_y,
extractor_version)

The occurrence identity is the ATTEMPT, the drawing, and the position on the page. The page
number alone is NOT a page identity — every whole-document run creates a new `drawings` row
(`app/pipeline.py` creates the drawing set and the drawing on each invocation), so the same
page number recurs across documents and only `(drawing_id, page_number)` names a page.

`analysis_run_id` is the ATTEMPT, and it is the identity J23 already uses rather than a
second one invented here: `analysis_runs` records one row per extraction invocation — the
whole-document run, each continuation window, each page retry — and a page read twice is two
readings. It is required, never optional: an occurrence is never recorded without the
attempt that found it, and an attempt the pipeline refused has no run and therefore writes
nothing. It is PROVENANCE, not chronology — a UUID is a name, not a time, and nothing here
orders runs by their ids.

The attempt is in the key because THE EXTRACTOR IS DETERMINISTIC. Ask it twice about the same
page of the same bytes under the same rule set and it reports the same coordinates, so on the
other columns alone the second reading of a page would collide with the first on every
occurrence it found. That reading is not a mistake: re-reading a parse-failed page is what
the retry route is for, and a reading that is identical because the drawing is unchanged is
evidence about the drawing.

The coordinate is the extractor's own reported value, rounded by the extractor to
`COORDINATE_PRECISION` decimal places and stored at exactly that precision, so the stored
number IS the evidence rather than a re-rounding of it: identity never depends on a
comparison tolerance, and two readings of one PDF produce identical keys.

The coordinate is also the one field whose STORED DOMAIN this layer enforces. The extractor
is a faithful producer, and a negative coordinate is something a real PDF can genuinely say:
content drawn outside the page's own box is reported as found. The table records only
non-negative coordinates (`numeric(9,2)`, CHECK `>= 0`), so a reading carrying one is refused
HERE and never reaches the writer. Which layer refuses matters: an
`AnnotationEvidenceRefused` is a state the extraction paths already treat as non-fatal and
write nothing for (J36), whereas the database's own refusal propagates and fails the entire
run — deterministically, on every retry, leaving a page permanently unretryable. The
extractor's semantics are deliberately unchanged; it still reports exactly what the content
stream says, and refusing a reading this layer cannot store is this layer's job.

`extractor_version` completes the key because a RULE SET IS PART OF THE READING. A
re-extraction under a new rule set is a new reading of the same document, not a correction
of the old one, so it appends its own rows and the earlier reading stays legible. It is a
rule-set version and never an attempt counter: a retry does not change it, and nothing may
increment it to make room for a re-read.

Two occurrences WITHIN ONE READING can therefore still collide on the key if they round to
the same hundredth of a point. That is refused loudly, not merged: the whole insert fails and
the reading is not recorded. Merging them would silently destroy the only thing this layer
guarantees — that a row is an occurrence that was actually found, at a position the PDF
actually gave. (On the real Arkles set, over the 653 occurrences the J28 reading reports
across its 11 acceptance pages, there are zero such collisions.)

WHICH READING STANDS FOR A PAGE

A query, not a column. `authoritative_occurrences` returns, for each `(drawing, page)`, the
occurrences of the reading with the greatest `(extracted_at, extractor_version)`. The order
is taken from the row's OWN `extracted_at`, never from a parent table: this is an
append-only ledger and an append-only ledger does not order itself by a table that can be
rewritten. `drawings` in particular IS rewritten in place (`update_drawing` writes its page
count, number, title and revision) and `analysis_runs` is rewritten too (`update_analysis_run`
writes its status and counters).

The attempt does NOT enter the selection. `analysis_run_id` says which invocation produced a
reading; `extracted_at` says when, and when is what orders. A UUID carries no chronology, so
comparing two of them would be inventing an order the data does not have — and the run of a
retry is a later attempt by the clock the ledger already trusts, not by its id.

`extracted_at` is the database's own `now()` default, and `now()` is fixed for the duration
of a transaction, so every row of one insert carries one instant. That is what makes the
instant a batch identity rather than a per-row accident.

This module does not decide completeness. A page with no rows is a page with no recorded
occurrences, which is not the same state as a page that was read and held none; absence is
absence and is never a fabricated empty page.

PROVENANCE

Every row carries `source_pdf_sha256` and the `rule_set` it was read under, so a reading can
be attributed to the bytes and the rules that produced it without consulting anything else.
`extracted_at` is an audit fact and is deliberately NOT part of the occurrence identity and
NOT part of the deterministic evidence payload: two extractions of one PDF must be
comparable byte for byte, and a timestamp in the evidence would make that impossible.

This module is PURE: it holds no client, opens no connection, reads no environment and
imports nothing from `app.supabase_client` or `app.config`. The write and the read live in
`app.engineering_data.repository`, which already owns this table's transport.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from app.drawing_reading.pdf_annotation_extractor import (
    AnnotationOccurrence,
    DocumentAnnotationEvidence,
    occurrence_payload,
)

ANNOTATION_TABLE = "pdf_annotation_occurrences"

# The columns an occurrence is written and read as. Written explicitly rather than as `*`
# so that a column added to the table later cannot silently enter a proof.
ANNOTATION_COLUMNS = (
    "drawing_id",
    "project_id",
    "analysis_run_id",
    "page_number",
    "annotation_x",
    "annotation_y",
    "extractor_version",
    "mark_candidate",
    "mark_readable",
    "operator_count",
    "duplicate_operator_count",
    "schedule_row_candidate",
    "tag_box_present",
    "leader_present",
    "source_pdf_sha256",
    "rule_set",
    "evidence",
    "extracted_at",
)

# The columns promoted out of the evidence payload so a reader can select on them without
# reading json. Each is a copy of the same value inside `evidence`; the two may not drift,
# and `occurrence_rows` builds both from one occurrence in one pass.
_PROMOTED = (
    "page_number",
    "annotation_x",
    "annotation_y",
    "mark_candidate",
    "mark_readable",
    "operator_count",
    "duplicate_operator_count",
    "schedule_row_candidate",
    "tag_box_present",
    "leader_present",
)


class AnnotationEvidenceRefused(ValueError):
    """An occurrence cannot be recorded, or a recorded one cannot be read back."""


class _Missing:
    """A sentinel, so that a `None` classification is not confused with an absent one."""


_MISSING = _Missing()


# ===========================================================================
# Building the rows
# ===========================================================================
def _value(source: Any, name: str, default: Any = None) -> Any:
    """A dataclass attribute, or the same key of a plain mapping.

    Both forms occur: the extractor hands back frozen dataclasses, while a row already
    recorded and read back is a plain mapping.
    """
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _required(source: Any, name: str) -> Any:
    value = _value(source, name, _MISSING)
    if value is _MISSING or value is None:
        raise AnnotationEvidenceRefused(
            f"the reading came without `{name}`; an occurrence is never recorded on a "
            "guessed value."
        )
    return value


def _text(source: Any, name: str) -> str:
    value = _required(source, name)
    if not isinstance(value, str) or not value.strip():
        raise AnnotationEvidenceRefused(
            f"the reading carries a {value!r} for `{name}`; provenance is required and is "
            "never recorded empty."
        )
    return value


# The scale the coordinate columns are STORED at. `annotation_x` and `annotation_y` are
# declared `numeric(9,2)` in the migration, so the database rounds an offered value to two
# decimal places before it evaluates its own `>= 0` CHECK. The rounded number, not the
# offered one, is therefore the value the table holds — and `-0.0042` becomes `0.00`, which
# the CHECK admits. The extractor happens to round to these same two places
# (`COORDINATE_PRECISION`), but that is the extractor's decision about its own reading; what
# this module mirrors is the COLUMN.
_COORDINATE_SCALE = 2


def _stored_coordinate(value: float) -> float:
    """`value` as the column will hold it: `numeric(9,2)` rounds on assignment."""
    return round(value, _COORDINATE_SCALE)


def _coordinate(source: Any, name: str) -> float:
    """A device coordinate, checked against the domain the table itself enforces.

    The stored column is `numeric(9,2)` and the table records only a non-negative one, so a
    value that would be STORED below zero is refused here — before any write — rather than
    left for the database to refuse as a constraint violation. A value the column would
    round to zero IS zero: `-0.0042` is stored as `0.00` and is accepted, `-0.005` is stored
    as `-0.01` and is refused. The boundary is the column's own rounding, not an epsilon of
    this module's invention.

    The refusal is of the WHOLE reading, because `occurrence_rows` raises: a row set missing
    an occurrence that was found would be an incomplete reading recorded as a complete one,
    which is the same reason the key-collision branch below refuses rather than merges.
    """
    value = _required(source, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnnotationEvidenceRefused(
            f"the occurrence carries a {type(value).__name__} for `{name}`; a device "
            "coordinate is required."
        )
    value = float(value)
    stored = _stored_coordinate(value)
    if stored < 0:
        raise AnnotationEvidenceRefused(
            f"the occurrence carries {value!r} for `{name}`, which the table would store as "
            f"{stored!r}; a stored annotation coordinate is never negative, so the reading "
            "is refused whole rather than this occurrence dropped."
        )
    return value


def _page_number(source: Any) -> int:
    value = _required(source, "page_number")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AnnotationEvidenceRefused(
            f"the occurrence carries {value!r} as page_number; pages are 1-based and the "
            "page identity is the page the operator was read from."
        )
    return value


def _count(source: Any, name: str, minimum: int) -> int:
    value = _required(source, name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AnnotationEvidenceRefused(
            f"the occurrence carries {value!r} for `{name}`; at least {minimum} is required."
        )
    return value


def _classification(source: Any, name: str) -> bool | None:
    """A three-valued classification: True, False, or None for not established."""
    value = _value(source, name, _MISSING)
    if value is _MISSING:
        raise AnnotationEvidenceRefused(
            f"the occurrence came without `{name}`; an unestablished classification is "
            "recorded as null and never omitted."
        )
    if value is None:
        return None
    if not isinstance(value, bool):
        raise AnnotationEvidenceRefused(
            f"the occurrence carries a {type(value).__name__} for `{name}`; it is "
            "true, false, or null for not established."
        )
    return value


def _occurrence_evidence(occurrence: Any) -> dict[str, Any]:
    """The occurrence verbatim, as the extractor itself serialises it.

    The serializer is the extractor's own, deliberately: a second definition of the
    evidence shape here would be a second shape, and the two would drift.
    """
    if isinstance(occurrence, AnnotationOccurrence):
        return occurrence_payload(occurrence)
    if isinstance(occurrence, Mapping):
        return dict(occurrence)
    raise AnnotationEvidenceRefused(
        f"an occurrence must be an AnnotationOccurrence or a mapping "
        f"(got {type(occurrence).__name__})."
    )


def occurrence_rows(
    evidence: Any,
    *,
    drawing_id: str,
    project_id: str,
    analysis_run_id: str,
) -> list[dict[str, Any]]:
    """The rows one reading is recorded as. Builds nothing else, decides nothing else.

    The identity values are the ones the caller already holds — the same `project_id`,
    `drawing_id` and `analysis_run_id` the reading was taken for — so a reading cannot be
    filed under another document, and cannot be filed without the attempt that produced it.
    `drawing_set_id` is deliberately not carried: it is one hop up through `drawings` and no
    reader of this table needs it.

    `analysis_run_id` is REQUIRED and is never defaulted. An occurrence whose attempt is
    unknown cannot be attributed to a reading, and the pipeline's refused attempts have no
    run — so a caller that does not hold one has nothing to record, and this refuses rather
    than writing rows that nothing can be attributed to.

    `extracted_at` is NOT set here. It is the database's own default, so that the ordering
    of readings is assigned by one clock and never by the caller.
    """
    if not isinstance(drawing_id, str) or not drawing_id.strip():
        raise AnnotationEvidenceRefused("a reading was offered with no drawing_id.")
    if not isinstance(project_id, str) or not project_id.strip():
        raise AnnotationEvidenceRefused("a reading was offered with no project_id.")
    if not isinstance(analysis_run_id, str) or not analysis_run_id.strip():
        raise AnnotationEvidenceRefused(
            "a reading was offered with no analysis_run_id; an occurrence is never recorded "
            "without the extraction invocation that found it."
        )

    version = _text(evidence, "extractor_version")
    digest = _text(evidence, "source_pdf_sha256")
    rules = _required(evidence, "rule_set")
    occurrences = _required(evidence, "occurrences")

    rule_set = [[str(name), str(value)] for name, value in rules]

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, float, float]] = set()
    for occurrence in occurrences:
        page_number = _page_number(occurrence)
        x = _coordinate(occurrence, "annotation_x")
        y = _coordinate(occurrence, "annotation_y")
        readable = _classification(occurrence, "mark_readable")
        candidate = _value(occurrence, "mark_candidate")

        # `mark_readable` is False exactly for a designation-shaped run the grammar could
        # not tokenise, so it has no token. A row claiming otherwise would be internally
        # contradictory, and a reader selecting on `mark_candidate` would be misled.
        if readable is False and candidate is not None:
            raise AnnotationEvidenceRefused(
                f"the occurrence at page {page_number} ({x}, {y}) is marked unreadable "
                f"yet carries the candidate {candidate!r}; the two may not disagree."
            )
        if candidate is not None and not isinstance(candidate, str):
            raise AnnotationEvidenceRefused(
                f"the occurrence at page {page_number} ({x}, {y}) carries a "
                f"{type(candidate).__name__} for mark_candidate."
            )

        key = (page_number, x, y)
        if key in seen:
            raise AnnotationEvidenceRefused(
                f"the reading holds two occurrences at page {page_number} ({x}, {y}); they "
                "would collide on the occurrence key, and silently keeping one would be "
                "silently dropping an occurrence that was found."
            )
        seen.add(key)

        operator_count = _count(occurrence, "operator_count", 1)
        duplicate_count = _count(occurrence, "duplicate_operator_count", 0)
        if duplicate_count > 0 and operator_count < 1:
            raise AnnotationEvidenceRefused(
                f"the occurrence at page {page_number} ({x}, {y}) records {duplicate_count} "
                "duplicate operator(s) with no operator to duplicate."
            )

        rows.append({
            "drawing_id": drawing_id,
            "project_id": project_id,
            "analysis_run_id": analysis_run_id,
            "page_number": page_number,
            "annotation_x": x,
            "annotation_y": y,
            "extractor_version": version,
            "mark_candidate": candidate,
            "mark_readable": bool(readable),
            "operator_count": operator_count,
            "duplicate_operator_count": duplicate_count,
            "schedule_row_candidate": _classification(occurrence, "schedule_row_candidate"),
            "tag_box_present": bool(_classification(occurrence, "tag_box_present")),
            "leader_present": _classification(occurrence, "leader_present"),
            "source_pdf_sha256": digest,
            "rule_set": rule_set,
            "evidence": _occurrence_evidence(occurrence),
        })
    return rows


# ===========================================================================
# Selecting the reading that stands for a page
# ===========================================================================
def _extracted_at(value: Any) -> datetime:
    """A reading's own timestamp, parsed so ordering never depends on formatting.

    Comparing the raw strings would work only while every row carries the same UTC offset
    and the same precision — a property nothing enforces.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AnnotationEvidenceRefused(
                f"a recorded occurrence carries an unreadable extracted_at ({value!r})."
            ) from exc
    raise AnnotationEvidenceRefused(
        f"a recorded occurrence carries a {type(value).__name__} extracted_at; an instant "
        "is required."
    )


def _page_key(row: Mapping[str, Any]) -> tuple[str, int]:
    return (str(_required(row, "drawing_id")), int(_required(row, "page_number")))


def _identity_key(row: Mapping[str, Any]) -> tuple[int, float, float]:
    return (
        int(_required(row, "page_number")),
        float(_required(row, "annotation_x")),
        float(_required(row, "annotation_y")),
    )


def _standing(row: Mapping[str, Any]) -> tuple[datetime, str]:
    """How strongly a recorded reading stands for its page — greatest wins.

    The instant first, then the rule-set version. Both are the row's OWN values, so the
    comparison needs no other table: `drawings` and `analysis_runs` are both rewritten in
    place and an append-only ledger does not order itself by a table that can be rewritten.

    `analysis_run_id` deliberately does NOT appear here. It names the attempt, and a name is
    not an order: two run ids compare as strings by an ordering no clock ever produced, so
    including one would let a retry's reading lose to the reading it superseded whenever the
    UUIDs happened to sort the wrong way. Which attempt is later is already stated by
    `extracted_at`, which the database assigns at insert time.
    """
    return (
        _extracted_at(_required(row, "extracted_at")),
        str(_required(row, "extractor_version")),
    )


def authoritative_occurrences(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """One reading's occurrences per page: the reading that stands for it.

    For each `(drawing, page)`, every row whose `(extracted_at, extractor_version)` equals
    the greatest recorded for that page. A page read more than once therefore contributes
    exactly the rows of its most recent reading, while every earlier ATTEMPT stays recorded
    — including attempts whose occurrences are identical to the ones that stand, which is
    the ordinary case for a page re-read under an unchanged rule set. The attempt is named by
    `analysis_run_id` and is never used to order: it identifies, `extracted_at` ranks.

    Grouping is by `(drawing_id, page_number)` and never by run, so a page read by a
    continuation window and then re-read by a retry still yields one standing set.

    A page with no rows does not appear at all — absence is absence, never a fabricated
    empty page. Ordered by drawing, then page, then position on the page.
    """
    best: dict[tuple[str, int], tuple[datetime, str]] = {}
    for row in rows:
        key = _page_key(row)
        standing = _standing(row)
        current = best.get(key)
        if current is None or standing > current:
            best[key] = standing

    kept = [row for row in rows if _standing(row) == best[_page_key(row)]]
    kept.sort(key=lambda row: (_page_key(row), _identity_key(row)))
    return tuple(kept)
