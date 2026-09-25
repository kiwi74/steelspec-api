"""
Milestone J16 — the production continuation contract for a PDF drawing set
larger than one extraction window.

A PDF extraction reads at most `MAX_PDF_PAGES` pages, because a mis-uploaded
300-page drawing set must not silently rack up a 300-page API bill. Until this
module there was no production way to read the rest: the cap was a wall, and
Milestone J15 could only record truthfully that pages were missing. This module
states what the NEXT window is allowed to be, so that the remainder can be read
in controlled steps without any page being read twice, skipped, or invented.

THE CONTRACT

    WINDOW_SIZE   the most pages one extraction may read (MAX_PDF_PAGES)
    window        a closed 1-based page range [first, last] of the document
    boundary      the next page the document has not had read yet. It is
                  `analysed_pages + 1` of the committed coverage record.

The boundary is derived from a COUNT, which means it is only sound because
every window this contract accepts is CONTIGUOUS with it: a window must begin
exactly at the boundary and must not reach past the boundary's window of
`WINDOW_SIZE` pages. That single rule is what makes "the pages read so far" and
"the pages not read yet" two halves of one document with no overlap and no
hole between them, and it is why an accepted window can be persisted as an
addition to the committed coverage (see `accumulate_coverage`) rather than as a
separate claim that has to be reconciled later.

Every window that is not that window is REFUSED, by name:

    WINDOW_MALFORMED         not two 1-based page numbers, first not after last
    WINDOW_BEYOND_DOCUMENT   a page past the document's own last page
    WINDOW_TOO_LARGE         wider than one extraction may read
    WINDOW_BACKWARD          ends before the boundary: those pages are read
    WINDOW_OVERLAP           starts before the boundary but reaches past it
    WINDOW_SKIP              starts after the boundary: pages between are unread
    WINDOW_ALREADY_COMPLETE  no page is left to read; nothing to continue
    WINDOW_WRONG_TOTAL       the caller's stated page count is not the record's
    COVERAGE_UNUSABLE        the record itself states no usable page count

The refusals above all answer "is this the next window?" — they never repair
one. A refused window changes nothing and reads nothing.

Milestone J17 added one refusal to this vocabulary,
`CONTINUATION_FAILURES_UNRECORDED`, because the second half of the coverage
record (WHICH pages failed, `app/validation/parse_failures.py`) is written by
this path too: a project whose committed record says pages failed but does not
name them cannot have that record extended truthfully by a later window, so the
window is refused instead. A window is never refused for merely HAVING a parse
failure — a failure is a reading outcome of a page that was read, it is recorded
and carried forward by `accumulate_coverage`, and J17 makes it repairable by
page; see that milestone's report for why a failure does not stop a continuation
from reading the pages after it.

WHAT THIS MODULE IS NOT. It is not the 7AY/7AZ continuation machinery
(`app/cad_engine/incremental_continuation.py`) and does not import it: that
module accumulates an operator-supplied JSON capture in memory, combines it
through the CAD review-queue intake, tolerates gaps by recording them NOT_
ANALYSED, and persists nothing. Production's substrate is the database, its
pages carry persisted engineering rows with no merge semantics, and its
coverage record is a committed count — so its equivalent is this small,
fail-closed contract rather than that subsystem. What IS reused is the
concept: a declared window must be exactly the next one, and every page outside
it must stay accounted for.

Pure: no Supabase, no filesystem, no AI, no page rendering. Every rule here is
decidable from the committed coverage record and the requested window, which is
what lets the whole of it be tested without a document at all.
"""

from dataclasses import dataclass

from app.validation.page_coverage import PageCoverage

__all__ = [
    "COVERAGE_UNUSABLE",
    "CONTINUATION_DOCUMENT_TOTAL_MISMATCH",
    "CONTINUATION_DRAWING_SET_UNRESOLVED",
    "CONTINUATION_FAILURES_UNRECORDED",
    "CONTINUATION_MEMBER_MARK_COLLISION",
    "CONTINUATION_NO_COVERAGE_RECORD",
    "CONTINUATION_RECORD_CONFLICT",
    "CONTINUATION_SOURCE_MISMATCH",
    "WINDOW_ALREADY_COMPLETE",
    "WINDOW_BACKWARD",
    "WINDOW_BEYOND_DOCUMENT",
    "WINDOW_MALFORMED",
    "WINDOW_OVERLAP",
    "WINDOW_SKIP",
    "WINDOW_TOO_LARGE",
    "WINDOW_WRONG_TOTAL",
    "ContinuationRefused",
    "PageWindow",
    "WindowDecision",
    "accumulate_coverage",
    "check_page_window",
]

# ---------------------------------------------------------------------------
# Window refusals — this is not the next window, and why.
# ---------------------------------------------------------------------------
WINDOW_MALFORMED = "WINDOW_MALFORMED"
WINDOW_BEYOND_DOCUMENT = "WINDOW_BEYOND_DOCUMENT"
WINDOW_TOO_LARGE = "WINDOW_TOO_LARGE"
WINDOW_BACKWARD = "WINDOW_BACKWARD"
WINDOW_OVERLAP = "WINDOW_OVERLAP"
WINDOW_SKIP = "WINDOW_SKIP"
WINDOW_ALREADY_COMPLETE = "WINDOW_ALREADY_COMPLETE"
WINDOW_WRONG_TOTAL = "WINDOW_WRONG_TOTAL"
COVERAGE_UNUSABLE = "COVERAGE_UNUSABLE"

# ---------------------------------------------------------------------------
# Continuation refusals — the production context around the window itself.
# These are decided against persisted state (project, drawing set, members)
# rather than against the coverage record alone, so they live here only to keep
# ONE vocabulary of reasons a continuation did not run.
# ---------------------------------------------------------------------------
# The project carries no coverage record at all: it predates J15, or its
# extraction never reached the point of committing one. Whether pages are
# missing is unknown, so continuing it could read pages twice.
CONTINUATION_NO_COVERAGE_RECORD = "CONTINUATION_NO_COVERAGE_RECORD"
# Exactly one drawing set and one drawing must describe this project; anything
# else is ambiguous about WHICH document a continuation would be adding to.
CONTINUATION_DRAWING_SET_UNRESOLVED = "CONTINUATION_DRAWING_SET_UNRESOLVED"
# The caller is not pointing at the document the project's extraction read.
CONTINUATION_SOURCE_MISMATCH = "CONTINUATION_SOURCE_MISMATCH"
# The document's own page count no longer agrees with the committed record.
CONTINUATION_DOCUMENT_TOTAL_MISMATCH = "CONTINUATION_DOCUMENT_TOTAL_MISMATCH"
# The persisted drawing-set counters and the coverage record disagree about how
# much has been read. Two sources of truth for one fact: refused, not merged.
CONTINUATION_RECORD_CONFLICT = "CONTINUATION_RECORD_CONFLICT"
# This window's members repeat marks the project has already persisted. Member
# identity across windows has no safe rule in production (persisted rows carry
# no merge semantics), so this is reported rather than silently consolidated.
CONTINUATION_MEMBER_MARK_COLLISION = "CONTINUATION_MEMBER_MARK_COLLISION"
# Milestone J17. The committed coverage record says pages failed to parse, but
# the project carries no readable record of WHICH pages — a record written
# between J15 and J17, or one this codebase did not write. A continuation can
# add this window's failures but cannot restate the earlier ones, and a failures
# record that named only the new ones would contradict the count it rides beside,
# so the window is refused rather than allowed to corrupt the record.
CONTINUATION_FAILURES_UNRECORDED = "CONTINUATION_FAILURES_UNRECORDED"


class ContinuationRefused(Exception):
    """A continuation that was refused, with the reason it was refused.

    Raised rather than returned because a refusal is not a result: the caller
    must not be able to proceed by ignoring a value. Nothing has been read and
    nothing has been written when this is raised.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _is_page_number(value: object) -> bool:
    """A 1-based page number: a whole number, and not a bool.

    `True == 1` in Python, so a truth value would otherwise be accepted as page
    one — a window that looks read and is not.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_page_count(value: object) -> bool:
    """A positive page count, on the same terms as a page number."""
    return _is_page_number(value)


@dataclass(frozen=True)
class PageWindow:
    """A closed, 1-based page range of the drawing set: what one extraction reads."""

    first_page: int
    last_page: int

    @property
    def size(self) -> int:
        """How many pages the window covers."""
        return self.last_page - self.first_page + 1

    def as_line(self) -> str:
        """The window as one line, for a caller that wants to state it."""
        return f"pages {self.first_page}-{self.last_page}"


@dataclass(frozen=True)
class WindowDecision:
    """The contract's answer about one requested window.

    `accepted` and `refusal_code` cannot disagree: a decision is either the
    window to process or the reason there is none.
    """

    window: PageWindow | None
    refusal_code: str | None = None
    detail: str | None = None

    @property
    def accepted(self) -> bool:
        return self.refusal_code is None


def _derived_window(coverage: PageCoverage, window_size: int) -> PageWindow:
    """The next window: from the boundary, up to one window, never past the end."""
    first_page = coverage.analysed_pages + 1
    last_page = min(first_page + window_size - 1, coverage.total_pages)
    return PageWindow(first_page=first_page, last_page=last_page)


def check_page_window(
    coverage: PageCoverage,
    *,
    window_size: int,
    requested_first_page: int | None = None,
    requested_last_page: int | None = None,
    requested_total_pages: int | None = None,
) -> WindowDecision:
    """Whether the requested window is the next window of this document.

    With nothing requested, the derived window is checked and returned. With a
    window requested, that window is checked instead — same rules, so a caller
    cannot obtain a different outcome by asking for what it would have been
    given. `requested_total_pages` is the caller's own statement of the
    document's size; when it disagrees with the record, the caller is
    describing a different document and the window is refused.

    The checks run in the order below and the FIRST applicable refusal is the
    answer — so a window that is both past the end and backwards is reported as
    past the end, deterministically, rather than by whichever branch a reader
    happens to look at first.
    """
    if not _is_page_count(window_size):
        raise ValueError("the extraction window size must be a positive whole number of pages")

    # A record that contradicts itself (or states no page count) cannot say
    # which page comes next, so nothing can be continued from it. This is
    # checked before completeness: an unusable record cannot even claim that.
    if not coverage.identity_holds:
        return WindowDecision(
            None, COVERAGE_UNUSABLE,
            "the committed coverage record states no page count that adds up, so there is "
            "no boundary to continue from",
        )

    if coverage.is_complete:
        return WindowDecision(
            None, WINDOW_ALREADY_COMPLETE,
            f"all {coverage.total_pages} page(s) of this drawing set are already read",
        )

    if requested_total_pages is not None and (
        isinstance(requested_total_pages, bool)
        or not isinstance(requested_total_pages, int)
        or requested_total_pages != coverage.total_pages
    ):
        return WindowDecision(
            None, WINDOW_WRONG_TOTAL,
            f"the requested page count {requested_total_pages!r} is not the "
            f"{coverage.total_pages} page(s) this drawing set has been read as",
        )

    derived = _derived_window(coverage, window_size)

    # Every page of the document has been reached, yet the record is not
    # complete: at least one page's response could not be parsed (a parse failure
    # is a reading failure OF a page that was read, so those pages are inside
    # `analysed_pages`). There is no window left to derive, and the honest answer
    # is that there is nothing to continue — this milestone has no way to read a
    # page again (see its report's parse-failure / retry semantics).
    if requested_first_page is None and derived.first_page > coverage.total_pages:
        return WindowDecision(
            None, WINDOW_ALREADY_COMPLETE,
            f"all {coverage.total_pages} page(s) of this drawing set have been read, "
            f"{coverage.parse_failed_pages} of them without a response that could be read: "
            f"there is no page left for a continuation to read",
        )

    first_page = derived.first_page if requested_first_page is None else requested_first_page
    last_page = derived.last_page if requested_last_page is None else requested_last_page

    if not (_is_page_number(first_page) and _is_page_number(last_page)) or last_page < first_page:
        return WindowDecision(
            None, WINDOW_MALFORMED,
            f"a window must be two 1-based page numbers with the first not after the last "
            f"(got {first_page!r}-{last_page!r})",
        )

    if last_page > coverage.total_pages:
        return WindowDecision(
            None, WINDOW_BEYOND_DOCUMENT,
            f"page {last_page} is past the last page ({coverage.total_pages}) of this drawing set",
        )

    if last_page > first_page + window_size - 1:
        return WindowDecision(
            None, WINDOW_TOO_LARGE,
            f"pages {first_page}-{last_page} are more than the {window_size} page(s) one "
            f"extraction may read",
        )

    boundary = coverage.analysed_pages + 1
    if first_page < boundary:
        if last_page < boundary:
            return WindowDecision(
                None, WINDOW_BACKWARD,
                f"pages {first_page}-{last_page} are all before page {boundary}, which is the "
                f"next page this drawing set has not had read",
            )
        return WindowDecision(
            None, WINDOW_OVERLAP,
            f"pages {first_page}-{last_page} overlap pages 1-{coverage.analysed_pages}, which "
            f"have already been read and persisted",
        )

    if first_page > boundary:
        return WindowDecision(
            None, WINDOW_SKIP,
            f"pages {boundary}-{first_page - 1} have not been read, and a window that starts "
            f"at page {first_page} would leave them behind",
        )

    return WindowDecision(PageWindow(first_page=first_page, last_page=last_page))


def accumulate_coverage(
    previous: PageCoverage,
    *,
    window: PageWindow,
    analysed_page_numbers,
    parse_failed_page_numbers=(),
) -> PageCoverage:
    """The committed coverage record plus one window's own measured reading.

    This is the only place one window's pages are added to what the document
    already had read, and it adds them only when the window was read IN FULL:
    a window that rendered fewer pages than it covers would leave a hole in the
    middle of a record that can only count, so it raises instead of advancing
    the boundary past a page nobody read. Nothing here persists anything — a
    caller that advances the record does so with the value this returns.

    The page numbers are checked against the window, not trusted: every page
    from `first_page` to `last_page` exactly once, no page outside it, and no
    parse failure on a page that was not analysed (the J15 rule that a parse
    failure is a reading failure OF a page that was read).

    Raises ValueError — a run that did not read its window is a failed run, not
    a refused continuation, and it fails before anything is written.
    """
    if not previous.identity_holds:
        raise ValueError(
            "cannot accumulate onto a coverage record whose own counts do not add up"
        )
    if previous.is_complete:
        raise ValueError("cannot accumulate onto a drawing set whose pages are all read")
    boundary = previous.analysed_pages + 1
    if window.first_page != boundary:
        raise ValueError(
            f"the window starts at page {window.first_page}, but the next page this drawing "
            f"set has not had read is page {boundary}"
        )
    if window.last_page > previous.total_pages:
        raise ValueError(
            f"the window reaches page {window.last_page}, past the last page "
            f"({previous.total_pages}) of this drawing set"
        )

    analysed = tuple(analysed_page_numbers)
    if len(set(analysed)) != len(analysed):
        raise ValueError("the window reports the same page twice")
    if sorted(analysed) != list(range(window.first_page, window.last_page + 1)):
        raise ValueError(
            f"the window covers pages {window.first_page}-{window.last_page} but reports "
            f"{sorted(analysed)} as read: a window that was not read in full is not "
            f"accumulated into the coverage record"
        )

    failed = tuple(parse_failed_page_numbers)
    if not set(failed) <= set(analysed):
        raise ValueError("the window reports a parse failure on a page it did not analyse")

    accumulated_analysed = previous.analysed_pages + window.size
    return PageCoverage(
        total_pages=previous.total_pages,
        analysed_pages=accumulated_analysed,
        parse_failed_pages=previous.parse_failed_pages + len(failed),
        not_analysed_pages=previous.total_pages - accumulated_analysed,
    )
