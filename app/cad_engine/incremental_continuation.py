"""
Milestone 7AZ — deterministic multi-increment drawing-set analysis.

Extends the 7AY incremental-analysis capability from a single 5-page
increment into a multi-increment workflow over accumulated drawing-set
evidence: pages 1-5, then 6-10, then 11-15, then 16-20, without ever
silently rebuilding, replacing, mutating or losing prior evidence.

THE ACCUMULATED STATE (AccumulatedAnalysis) is the single continuation
point. It holds the accumulated page records in the capture's own
container form (a JSON list, prior evidence first, never re-numbered),
the drawing set's declared first page and page count, a preservation
digest over the accumulated pages (7AY's own evidence_digest — the
existing integrity system, nothing new), the increment history, and the
explicit page accounting: every page of the declared universe is in
exactly one of ANALYSED / PARSE_FAILED / NOT_ANALYSED. NOT_ANALYSED is
derived from the declared universe minus the recorded pages, so a gap
continuation (e.g. prior 1-10, new 16-20) explicitly records pages
11-15 as NOT_ANALYSED — never as a silent contiguous 1-20, and never as
analysed evidence.

CONTINUATION RULES (continue_incremental_analysis):
  - the accumulated state must be self-consistent: its digest must match
    its pages (modified, removed, reordered or replaced prior evidence
    is detected), every recorded page must lie within the declared
    universe, and no page may record a parse failure while carrying
    extraction content (contradictory status);
  - the caller's expected_next_first_page must equal the smallest
    NOT_ANALYSED page of the accumulated accounting — a misdeclared
    continuation is refused, never corrected silently;
  - a new slice starting before the next expected page is refused
    (backward/overlap); a slice starting after it is a gap: the skipped
    pages are recorded NOT_ANALYSED and nothing is invented for them;
  - the slice's recorded drawing titles must overlap the accumulated
    evidence's (the capture form's stable source-drawing identity — the
    per-sheet drawing NUMBER varies within one set and can never serve
    this role); a slice from a different source drawing is refused;
  - the slice itself is combined through 7AY's combine_page_extractions,
    unchanged, so duplicate pages (within the slice or against prior
    evidence) and pages outside the declared slice range are refused by
    the existing layer.

The API maps onto the milestone's conceptual continuation contract as
follows: begin_incremental_analysis is the first increment (the drawing
set's page count and first page are declared once, here);
continue_incremental_analysis is every later increment and takes the
accumulated state as its prior — per the multi-increment rule, the
second increment must not need to know that the first increment was
originally 1-5; intake_for_accumulated feeds the accumulated pages
through the genuine 7Y intake with the accumulated page count, the
unchanged entry point for 7AX production-coverage evaluation.

WHAT THIS MODULE NEVER DOES: no vision calls (the capture script owns
real extraction), no AI, no network, no database writes, no review
decisions, no engineering transformation (M12 stays M12, "300" stays
"300", SQ4 12mm stays SQ4 12mm, AI_EXTRACTED never becomes
HUMAN_REVIEWED), no CAD/DXF/PDF generation, no completeness claims —
7AX remains the only coverage judge and an incomplete set stays
INCOMPLETE there.

No numeric literals appear in this module (AST-pinned by test): every
page number, count and threshold is supplied or discovered.
"""
import copy
import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from app.cad_engine.incremental_analysis import (
    _as_mapping,
    _first,
    _is_positive_integer,
    _last,
    _page_number,
    combine_page_extractions,
    evidence_digest,
)
from app.cad_engine.project_extraction_intake import (
    ProjectExtractionIntake,
    intake_page_extractions,
)

__all__ = [
    "AccumulatedAnalysis",
    "IncrementRecord",
    "begin_incremental_analysis",
    "continue_incremental_analysis",
    "intake_for_accumulated",
]


@dataclasses.dataclass(frozen=True)
class IncrementRecord:
    """One slice's place in the accumulation history: the page the slice
    was EXPECTED to start at (the accounting's next NOT_ANALYSED page),
    the pages it actually recorded, its slice digest (7AY's own digest
    of the slice) and its own state counts. A difference between the
    expected and actual first page is the recorded gap — never silent."""
    expected_first_page: Any
    first_page: Any
    last_page: Any
    slice_digest: str
    analysed_pages: int
    parse_failed_pages: int


@dataclasses.dataclass(frozen=True)
class AccumulatedAnalysis:
    """The accumulated drawing-set evidence, deterministic and frozen.
    `pages` keeps the capture's own container form (a JSON list), prior
    evidence first, byte-for-byte as combined by 7AY. Every field is
    derived from the recorded pages — no second source of truth, no
    timestamps, no environment-dependent ordering."""
    pages: list[Mapping[str, Any]]
    drawing_set_page_count: Any
    drawing_set_first_page: Any
    digest: str
    increments: tuple[IncrementRecord, ...]
    analysed_page_numbers: tuple[Any, ...]
    parse_failed_page_numbers: tuple[Any, ...]
    not_analysed_page_numbers: tuple[Any, ...]


def _recorded_drawing_titles(pages: Sequence[Mapping[str, Any]]) -> set[Any]:
    """The distinct non-null drawing titles the pages record. This is the
    capture form's stable source-drawing identity: unlike the per-sheet
    drawing number, which varies legitimately within one drawing set,
    the title is constant across the set's pages."""
    return {page.get("drawing_title") for page in pages if page.get("drawing_title")}


def _page_universe(drawing_set_first_page: Any, drawing_set_page_count: Any) -> list[Any]:
    """Every page of the declared universe, ascending — the first page
    through the page count, built without writing a numeric literal."""
    if drawing_set_first_page > drawing_set_page_count:
        return []
    return list(range(drawing_set_first_page, drawing_set_page_count)) + [drawing_set_page_count]


def _validate_slice(
    pages: Sequence[Any],
    *,
    drawing_set_first_page: Any,
    drawing_set_page_count: Any,
) -> tuple[list[Mapping[str, Any]], Any, Any]:
    """A slice's structural honesty: the declarations are positive
    integers, every page lies within the declared universe, no page is
    recorded twice, and no page records a parse failure while carrying
    extraction content (a contradictory status). Returns the deep-copied
    pages in recorded order and the slice's first and last page
    numbers."""
    if not (_is_positive_integer(drawing_set_first_page)
            and _is_positive_integer(drawing_set_page_count)):
        raise ValueError("the drawing set's first page and page count must be positive integers")
    if not pages:
        raise ValueError("the capture contains no pages")
    copied = [copy.deepcopy(_as_mapping(p)) for p in pages]
    seen: set[Any] = set()
    for page in copied:
        number = _page_number(page)
        if not (isinstance(number, int) and not isinstance(number, bool)):
            raise ValueError(f"page number {number!r} is not a positive integer")
        if number < drawing_set_first_page:
            raise ValueError(
                f"page {number} lies below the declared first page "
                f"{drawing_set_first_page} of the drawing set")
        if number > drawing_set_page_count:
            raise ValueError(
                f"page {number} lies beyond the drawing set's page count "
                f"{drawing_set_page_count}")
        if number in seen:
            raise ValueError(f"the capture records page {number} more than once")
        seen.add(number)
        if page.get("parse_failed") and (page.get("raw_members") or page.get("raw_connections")):
            raise ValueError(
                f"page {number} records a parse failure but carries extraction content — "
                "contradictory page status")
    return copied, _page_number(_first(copied)), _page_number(_last(copied))


def _accounting(
    pages: Sequence[Mapping[str, Any]],
    *,
    drawing_set_first_page: Any,
    drawing_set_page_count: Any,
) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]:
    """Explicit page accounting: every page of the declared universe in
    exactly one state. A page recorded with a parse failure stays
    PARSE_FAILED (never ANALYSED, even when it carries no candidates); a
    universe page with no record is NOT_ANALYSED — recorded explicitly,
    never silently absorbed."""
    analysed = tuple(_page_number(p) for p in pages if not p.get("parse_failed"))
    parse_failed = tuple(_page_number(p) for p in pages if p.get("parse_failed"))
    recorded = {_page_number(p) for p in pages}
    not_analysed = tuple(
        number
        for number in _page_universe(drawing_set_first_page, drawing_set_page_count)
        if number not in recorded
    )
    return analysed, parse_failed, not_analysed


def begin_incremental_analysis(
    first_pages: Sequence[Any],
    *,
    drawing_set_page_count: Any,
    drawing_set_first_page: Any,
) -> AccumulatedAnalysis:
    """The first increment of a drawing set becomes the accumulated
    state. The slice is deep-copied, validated against the declared
    universe and accounted exactly; the digest is 7AY's evidence_digest
    over the recorded pages. Refuses (ValueError naming the reason) on
    a non-positive declaration, a page below the declared first page, a
    page beyond the page count, a duplicate page, an empty capture or a
    contradictory page status."""
    if not (_is_positive_integer(drawing_set_first_page)
            and _is_positive_integer(drawing_set_page_count)):
        raise ValueError("the drawing set's first page and page count must be positive integers")
    if drawing_set_first_page > drawing_set_page_count:
        raise ValueError("the declared first page lies beyond the drawing set's page count")
    pages, slice_first, slice_last = _validate_slice(
        first_pages,
        drawing_set_first_page=drawing_set_first_page,
        drawing_set_page_count=drawing_set_page_count,
    )
    analysed, parse_failed, not_analysed = _accounting(
        pages,
        drawing_set_first_page=drawing_set_first_page,
        drawing_set_page_count=drawing_set_page_count,
    )
    record = IncrementRecord(
        expected_first_page=drawing_set_first_page,
        first_page=slice_first,
        last_page=slice_last,
        slice_digest=evidence_digest(pages),
        analysed_pages=len(analysed),
        parse_failed_pages=len(parse_failed),
    )
    return AccumulatedAnalysis(
        pages=pages,
        drawing_set_page_count=drawing_set_page_count,
        drawing_set_first_page=drawing_set_first_page,
        digest=evidence_digest(pages),
        increments=(record,),
        analysed_page_numbers=analysed,
        parse_failed_page_numbers=parse_failed,
        not_analysed_page_numbers=not_analysed,
    )


def _validate_accumulated(accumulated: AccumulatedAnalysis) -> None:
    """The accumulated state must be self-consistent before any further
    use: its digest must match its pages (modified, removed, reordered
    or replaced prior evidence is detected), and its recorded pages must
    pass the slice checks against its own declared universe."""
    if accumulated.digest != evidence_digest(accumulated.pages):
        raise ValueError(
            "the accumulated evidence does not match its recorded digest; "
            "a continuation may not operate on modified prior evidence")
    _validate_slice(
        accumulated.pages,
        drawing_set_first_page=accumulated.drawing_set_first_page,
        drawing_set_page_count=accumulated.drawing_set_page_count,
    )


def continue_incremental_analysis(
    accumulated: AccumulatedAnalysis,
    new_pages: Sequence[Any],
    *,
    expected_next_first_page: Any,
) -> AccumulatedAnalysis:
    """The deterministic continuation: the next slice is appended to the
    accumulated state through 7AY's combine_page_extractions, unchanged,
    and a NEW frozen state is returned — the supplied state and slice
    are never mutated and the result is independent of them.

    Refuses (ValueError naming the reason) on: an inconsistent
    accumulated state (digest mismatch, out-of-universe recorded page,
    contradictory status); a misdeclared expected_next_first_page (it
    must equal the accounting's next NOT_ANALYSED page); a backward
    slice (starting before the next expected page); a fully accounted
    set; an empty slice; and everything combine_page_extractions already
    refuses (duplicate pages, overlap with prior evidence, a page
    outside the slice's own range).

    A slice starting AFTER the next expected page is a gap: the skipped
    pages are recorded NOT_ANALYSED in the returned accounting —
    explicitly, never as silent contiguous coverage."""
    _validate_accumulated(accumulated)
    next_expected = _first(accumulated.not_analysed_page_numbers)
    if next_expected is None:
        raise ValueError(
            "the drawing set is already fully accounted; there is no next page to analyse")
    if expected_next_first_page != next_expected:
        raise ValueError(
            f"the declared next first page {expected_next_first_page!r} does not match "
            f"the accumulated accounting's next page {next_expected!r}")
    slice_pages, slice_first, slice_last = _validate_slice(
        new_pages,
        drawing_set_first_page=accumulated.drawing_set_first_page,
        drawing_set_page_count=accumulated.drawing_set_page_count,
    )
    if slice_first < expected_next_first_page:
        raise ValueError(
            f"the new slice starts at page {slice_first}, before the next expected page "
            f"{expected_next_first_page} — a backward continuation is refused")
    accumulated_titles = _recorded_drawing_titles(accumulated.pages)
    slice_titles = _recorded_drawing_titles(slice_pages)
    if accumulated_titles and slice_titles and accumulated_titles.isdisjoint(slice_titles):
        raise ValueError(
            f"the new slice records drawing title(s) {sorted(slice_titles)!r} but the "
            f"accumulated evidence records {sorted(accumulated_titles)!r} — a slice from a "
            "different source drawing can never be combined")
    combined = combine_page_extractions(
        accumulated.pages,
        new_pages,
        first_new_page=slice_first,
        last_new_page=slice_last,
        expected_prior_digest=accumulated.digest,
    )
    analysed, parse_failed, not_analysed = _accounting(
        combined.combined_pages,
        drawing_set_first_page=accumulated.drawing_set_first_page,
        drawing_set_page_count=accumulated.drawing_set_page_count,
    )
    record = IncrementRecord(
        expected_first_page=expected_next_first_page,
        first_page=slice_first,
        last_page=slice_last,
        slice_digest=combined.new_digest,
        analysed_pages=len([p for p in slice_pages if not p.get("parse_failed")]),
        parse_failed_pages=len([p for p in slice_pages if p.get("parse_failed")]),
    )
    return AccumulatedAnalysis(
        pages=combined.combined_pages,
        drawing_set_page_count=accumulated.drawing_set_page_count,
        drawing_set_first_page=accumulated.drawing_set_first_page,
        digest=evidence_digest(combined.combined_pages),
        increments=accumulated.increments + (record,),
        analysed_page_numbers=analysed,
        parse_failed_page_numbers=parse_failed,
        not_analysed_page_numbers=not_analysed,
    )


def intake_for_accumulated(
    accumulated: AccumulatedAnalysis,
    *,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
    known_member_marks: Sequence[str] | None = None,
    page_analysis_run_ids: Mapping[Any, str] | None = None,
) -> ProjectExtractionIntake:
    """The accumulated pages through the genuine 7Y intake, carrying the
    accumulated drawing-set page count — the unchanged 7AX entry point.
    Prior pages come first, so submission-order candidate identities
    (RP-0001 ...) are preserved exactly as the combined evidence
    records them. An inconsistent accumulated state is refused before
    any intake is built.

    `page_analysis_run_ids` (J72) is keyed by the page's own number — the
    run that READ that page, so each candidate can record the address it
    was actually read at. It is keyed by number rather than by position
    because the accumulated state already refuses a page recorded twice,
    so a page number names exactly one page here. A page the caller does
    not name records no origin: the run that stands for the page today is
    a different question from which attempt produced this candidate, and
    it is never substituted for one."""
    _validate_accumulated(accumulated)
    return intake_page_extractions(
        accumulated.pages,
        project_id=project_id,
        source_drawing_id=source_drawing_id,
        known_member_marks=known_member_marks,
        drawing_set_page_count=accumulated.drawing_set_page_count,
        page_analysis_run_ids=(
            None if page_analysis_run_ids is None
            else [page_analysis_run_ids.get(_page_number(page)) for page in accumulated.pages]
        ),
    )
