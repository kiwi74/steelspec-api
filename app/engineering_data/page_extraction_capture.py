"""
The durable raw AI-capture layer (Milestone J23).

WHAT THIS MODULE IS

The record of what the vision model actually returned, page by page, kept
verbatim — and the rule that decides which reading of a page stands for that page.

The connection-review workflow's authoritative input is exactly that reading: the
per-page `raw_members` / `raw_connections`. Until this milestone it had no persisted
source anywhere. `PageExtraction` (`app/ai_analysis/pdf_vision_analyzer.py:92`) is a
plain in-memory dataclass whose list lives only in the frame of its caller, and on a
parse failure line 208 of that module `continue`s past the page having discarded the
model's response entirely.

WHAT THIS MODULE IS NOT

It is not a second interpretation layer, and it is not a reader of the interpreted
evidence. Nothing here is derived from `connections`, `steel_members` or
`review_items`, and nothing is normalised, merged, deduplicated, reinterpreted or
repaired on the way in or out. The point is to preserve what those downstream
projections lose. A producer may build an intake from a reading recorded here; it may
never rebuild one from an engineering row.

This module is PURE: it holds no client, opens no connection, reads no environment
and imports nothing from `app.supabase_client` or `app.config`. It builds the rows
that are written, and it decides which recorded reading stands for a page — both
functions of the data alone. The write and the read live in
`app.engineering_data.repository`, which is the module that already owns this table's
transport.

THE ATTEMPT IDENTITY

`analysis_runs` already records one row per extraction invocation — one for the
whole-document run, one per continuation window, one per page retry. So
`(drawing_id, page_number, analysis_run_id)` is the attempt identity, and re-reading
a page needs no supersede column and no second protocol: a retry is a new run,
therefore a new row, and the earlier attempt is never touched. The history of a
page's readings is preserved by construction.

WHICH READING STANDS FOR A PAGE

`authoritative_captures` is the one place that rule is written down. The latest
attempt that parsed wins, ordered by `(captured_at, analysis_run_id)`; only when no
attempt of a page has ever parsed does the latest attempt stand, and it is the
parse-failed one. A permanently unparseable page therefore stays explicitly
represented and is never silently an empty success.

Ordering is taken from the capture row's OWN `captured_at`, never from the parent
run: `analysis_runs` is mutable in place and `completed_at` is NULL for any run whose
update never ran — which is exactly the run of a failed continuation. An append-only
ledger does not order itself by a table that can be rewritten.

This module does not decide completeness. The coverage record and the failure record
remain the completeness authority, and the presence of a capture is NOT proof that
the engineering rows of its run were written — the two are separate writes with no
shared transaction, and the capture is deliberately written first.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Mapping, Sequence

CAPTURE_TABLE = "page_extraction_captures"

# The columns the capture is written and read as. Written explicitly rather than as
# `*` so that a column added to the table later cannot silently enter a proof.
CAPTURE_COLUMNS = (
    "drawing_id",
    "drawing_set_id",
    "project_id",
    "page_number",
    "analysis_run_id",
    "model",
    "parse_failed",
    "payload",
    "captured_at",
)


class CaptureRefused(ValueError):
    """A reading cannot be recorded, or a recorded one cannot be read back."""


class _Missing:
    """A sentinel, so that a `None` parse flag is not confused with an absent one."""


_MISSING = _Missing()


# ===========================================================================
# Building the rows
# ===========================================================================
def _value(page: Any, name: str, default: Any = None) -> Any:
    """A `PageExtraction` attribute, or the same key of a plain mapping.

    Both forms are accepted because both occur: the extraction paths hold
    `PageExtraction` objects, while a capture already recorded and read back is a
    plain mapping. The genuine capture files under `tests/data/` are `asdict` dumps,
    so the two are the same shape.
    """
    if isinstance(page, Mapping):
        return page.get(name, default)
    return getattr(page, name, default)


def _payload_of(page: Any) -> dict[str, Any]:
    """The reading, verbatim — no field added, dropped, renamed or repaired.

    A `PageExtraction` is recorded as `dataclasses.asdict` of itself, which is the
    exact structure the model's response was parsed into; a plain mapping is recorded
    exactly as given, extra keys included.
    """
    if dataclasses.is_dataclass(page) and not isinstance(page, type):
        return dataclasses.asdict(page)
    if isinstance(page, Mapping):
        return dict(page)
    raise CaptureRefused(
        f"a page reading must be a PageExtraction or a mapping (got {type(page).__name__})."
    )


def _page_number_and_flag(payload: Mapping[str, Any], page: Any) -> tuple[int, bool]:
    """The page this reading is OF, and whether its response could be parsed.

    Neither is defaulted. A page whose parse status is unknown is not the same as a
    page that parsed and genuinely contained nothing, so an absent `parse_failed` is
    refused rather than assumed false — the one thing this layer must never do is turn
    a reading it cannot characterise into an apparent success.

    `page_number` is not the model's opinion: it is the page the analyzer told the
    model it was looking at ("This is page N of the drawing set",
    `app/ai_analysis/pdf_vision_analyzer.py:196`), which is why it can serve as the
    capture's page identity.
    """
    if _value(page, "parse_failed", _MISSING) is _MISSING:
        raise CaptureRefused(
            "a page reading was offered with no `parse_failed`; a reading whose parse status "
            "is unknown is never recorded as a success."
        )
    page_number = payload.get("page_number")
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise CaptureRefused(
            f"a page reading was offered with page_number {page_number!r}; the capture's page "
            "identity is the 1-based page the model was asked about, and it is required."
        )
    return page_number, bool(_value(page, "parse_failed"))


def capture_rows(
    pages: Sequence[Any],
    *,
    analysis_run_id: str,
    drawing_id: str,
    drawing_set_id: str,
    project_id: str,
    model: str,
) -> list[dict[str, Any]]:
    """The rows one run's readings are recorded as. Builds nothing else.

    The identity values are the ones the caller already holds — the same
    `drawing_set_id` / `drawing_id` the run's own `analysis_runs` row was created with —
    so a window's readings cannot be filed under another document.

    `captured_at` is NOT set here. It is the database's own default, so that the
    ordering of attempts is assigned by one clock and never by the caller.
    """
    rows = []
    for page in pages:
        payload = _payload_of(page)
        page_number, parse_failed = _page_number_and_flag(payload, page)
        rows.append({
            "drawing_id": drawing_id,
            "drawing_set_id": drawing_set_id,
            "project_id": project_id,
            "page_number": page_number,
            "analysis_run_id": analysis_run_id,
            "model": model,
            "parse_failed": parse_failed,
            "payload": payload,
        })
    return rows


# ===========================================================================
# Selecting the reading that stands for a page
# ===========================================================================
def _required(row: Mapping[str, Any], name: str) -> Any:
    if name not in row or row[name] is None:
        raise CaptureRefused(
            f"a recorded capture came back without `{name}`; a captured reading is never "
            "selected on a guessed value."
        )
    return row[name]


def _captured_at(value: Any) -> datetime:
    """The attempt's own timestamp, parsed so that ordering never depends on formatting.

    Comparing the raw strings would work only while every row carries the same UTC
    offset and the same precision — a property nothing enforces. Parsing makes the
    comparison a comparison of instants.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CaptureRefused(
                f"a recorded capture carries an unreadable captured_at ({value!r})."
            ) from exc
    raise CaptureRefused(
        f"a recorded capture carries a {type(value).__name__} captured_at; an instant is required."
    )


def _standing(row: Mapping[str, Any]) -> tuple[bool, datetime, str]:
    """How strongly a recorded reading stands for its page — greatest wins.

    A parsed reading outranks an unparsed one, and between two of the same kind the
    later attempt outranks the earlier. Written as `not parse_failed` so that "greater"
    and "wins" are the same direction: comparing the flag itself would sort `False`
    below `True`, which would let an unparsed reading displace a parsed one.
    """
    return (
        not bool(_required(row, "parse_failed")),
        _captured_at(_required(row, "captured_at")),
        str(_required(row, "analysis_run_id")),
    )


def _page_key(row: Mapping[str, Any]) -> tuple[str, int]:
    return (str(_required(row, "drawing_id")), int(_required(row, "page_number")))


def authoritative_captures(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """One recorded reading per page: the one that stands for it, deterministically.

    The latest attempt that parsed — ordered by `(captured_at, analysis_run_id)`, never
    by the order rows arrived in. Only when NO attempt of a page has ever parsed does
    the latest attempt stand, and it is the parse-failed one: the page stays represented
    as a page that could not be read, which is not the same state as a page that was
    read and held nothing.

    A page read more than once therefore yields exactly one entry here while every
    attempt remains recorded. Ordered by drawing, then page. Pages with no captures do
    not appear at all — absence is absence, never a fabricated empty page.
    """
    chosen: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in rows:
        key = _page_key(row)
        current = chosen.get(key)
        if current is None or _standing(row) > _standing(current):
            chosen[key] = row
    return tuple(chosen[key] for key in sorted(chosen))


def accumulated_page_mappings(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """The document's accumulated reading: each page's authoritative payload.

    Window 1 + window 2 + … + window N, one entry per page that actually has a
    reading — the shape a producer needs to rebuild an intake from persisted capture
    plus document identity alone. A page read by more than one attempt appears exactly
    once, as its authoritative reading; a parse-failed page appears too, carrying its
    own flag, never converted to an empty success.

    This is the hand-off to `intake_page_extractions`, which already accepts plain
    mappings with exactly these field names.
    """
    return tuple(
        _required(row, "payload") for row in authoritative_captures(rows)
    )
