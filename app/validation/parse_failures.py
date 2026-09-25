"""
Milestone J17 — WHICH pages of a drawing set could not be read, so that one of
them can be read again.

Milestone J15 made a run state HOW MANY of the document's pages it analysed
without being able to read the response (`app/validation/page_coverage.py`). The
count is what "was the whole drawing set understood?" needs, and it is enough to
keep such a project out of "done".

It is not enough to ACT on. A count names no page, so the page that failed
cannot be found again, and J16 proved the consequence: retrying the WINDOW that
contained it would re-persist the evidence of that window's successful pages,
and the window contract refuses it — correctly — because after a window the
boundary has moved past it for the very reason that its pages were already read.

This module is the other half of that record: the page numbers themselves, in
one deterministic line, in the same column J11 and J15 already use
(`projects.warnings`), written in the SAME call as the coverage line so the two
cannot be persisted apart. Together they state:

    PDF coverage: total=60 analysed=60 parse_failed=1 not_analysed=0
    PDF parse failures: pages=37

and they must AGREE: the failures line's count IS the coverage line's
`parse_failed`. A reader that finds them disagreeing has found a record that
contradicts itself, and this module refuses to act on either half of it. That
agreement is what makes "page 37 is currently failed" a fact about the drawing
set rather than a fact about one line of text — and it is why the retry below
cannot be used to invent a failure, or to clear one that is not stated.

WHAT THIS MODULE DOES NOT DO. It is pure: no Supabase, no filesystem, no AI, no
network, no rendering, no matching, no persistence. It does not read a page and
it decides nothing about steel. A parse failure is a READING failure — the page
was rendered and put to the vision model and the response was not readable as
engineering evidence — so a page named here is inside the coverage record's
`analysed` pages, contributes no member and no connection, and is never counted
as an empty page. Nothing here changes that, and nothing here re-reads anything.

The two refusal vocabularies of a retry that this module does not own are
imported from the milestones that do: a source file, an ambiguous drawing set
and a coverage record are J16's (`app/validation/page_windows.py`), and the
counts in that record are J15's.
"""

from dataclasses import dataclass
from typing import Iterable

# A coverage record this milestone cannot read is J15's own refusal, owned by the
# module that decides the window contract — imported rather than restated so the
# retry and the continuation cannot name the same state two ways.
from app.validation.page_windows import COVERAGE_UNUSABLE

__all__ = [
    "PARSE_FAILURES_NONE",
    "PARSE_FAILURES_PREFIX",
    "ParseFailures",
    "RETRY_EVIDENCE_ALREADY_PERSISTED",
    "RETRY_FAILURES_CONFLICT",
    "RETRY_FAILURES_UNRECORDED",
    "RETRY_MEMBER_MARK_COLLISION",
    "RETRY_PAGE_BEYOND_DOCUMENT",
    "RETRY_PAGE_MALFORMED",
    "RETRY_PAGE_NOT_FAILED",
    "RETRY_READ_MISMATCH",
    "RetryDecision",
    "RetryRefused",
    "check_retry_request",
    "failures_of",
    "parse_failures_from_warnings",
]

# Every persisted failures line begins with this. Defined once, here, so the
# writer and the reader cannot drift apart.
PARSE_FAILURES_PREFIX = "PDF parse failures: "

# The one field this line states, and the word it states when no page failed.
# A stated "none" is not the same record as no line at all: the first says the
# run read every page it analysed, the second says the run kept no such record.
_PARSE_FAILURES_FIELD = "pages"
PARSE_FAILURES_NONE = "none"


# ======================================================================================
# The refusal vocabulary of a retry — what a caller is told, and why.
# ======================================================================================
# Each code names a state in which page-level retry must NOT run. They are
# reported rather than grouped because each has a different remedy: a malformed
# page number is a caller bug, an unrecorded failures line is a record this
# milestone cannot repair, a disagreement between the two lines is a record that
# is no longer trustworthy, a page past the document does not exist, a page that
# is not named as failed is a page that already has an answer, and a colliding
# mark is a member identity a human must decide (the same rule J16 applies to a
# window).
RETRY_PAGE_MALFORMED = "RETRY_PAGE_MALFORMED"
RETRY_FAILURES_UNRECORDED = "RETRY_FAILURES_UNRECORDED"
RETRY_FAILURES_CONFLICT = "RETRY_FAILURES_CONFLICT"
RETRY_PAGE_BEYOND_DOCUMENT = "RETRY_PAGE_BEYOND_DOCUMENT"
RETRY_PAGE_NOT_FAILED = "RETRY_PAGE_NOT_FAILED"
RETRY_MEMBER_MARK_COLLISION = "RETRY_MEMBER_MARK_COLLISION"
# The page the retry read is already persisted against the project as evidence
# (a member or a connection whose `source_page` is that page of that drawing).
# The record says the page produced nothing — a parse failure persists no
# evidence — so evidence and record disagree, and the honest reading is that an
# earlier attempt wrote the evidence and did not live to record it. Decided from
# persisted rows alone, before the retry is allowed to read, and by EXACT
# identity (drawing + page), never by resemblance: this refusal exists so a
# second attempt cannot insert a second copy of evidence that already exists.
RETRY_EVIDENCE_ALREADY_PERSISTED = "RETRY_EVIDENCE_ALREADY_PERSISTED"
# The read did not come back as the ONE page it was asked for. The evidence of a
# page may only be persisted under that page's own number, so a reader that
# returned a different page (or a different number of pages) leaves nothing that
# can be written truthfully; the retry stops rather than attributing a reading
# to a page it does not belong to.
RETRY_READ_MISMATCH = "RETRY_READ_MISMATCH"


class RetryRefused(Exception):
    """A retry that must not be attempted, with the reason it was refused.

    `code` is one of the constants above (or a J16 code this milestone reuses,
    because the rule is the same one); `detail` states the facts that produced
    it. A refusal means nothing was read and nothing was written — it is not a
    failed retry, and it never changes the record it was refused against.
    """

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _is_page_number(value) -> bool:
    """A 1-based page number: a positive whole number that is not a bool.

    `True` is an `int` in Python, and "page True" is not a page, so it is
    rejected rather than read as page 1.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


@dataclass(frozen=True)
class ParseFailures:
    """The pages of one drawing set whose response could not be read.

    `page_numbers` is always sorted and free of duplicates: it is a SET of
    pages, and its canonical order is what makes the persisted line
    deterministic — two runs that found the same failures write the same text,
    so a record that changed can be told from one that was merely re-stated.
    """

    page_numbers: tuple[int, ...] = ()

    @property
    def count(self) -> int:
        """How many pages failed. This IS the coverage record's `parse_failed`."""
        return len(self.page_numbers)

    @property
    def is_empty(self) -> bool:
        return not self.page_numbers

    def names(self, page_number) -> bool:
        """Whether this record names `page_number` as failed."""
        return page_number in self.page_numbers

    def including(self, page_numbers: Iterable[int]) -> "ParseFailures":
        """This record plus more failed pages (a later window's, say).

        A union, not a replacement: J16 reads windows forward, so a failure
        recorded earlier is still a failure until it is repaired — a
        continuation that dropped it would be stating that a page nobody re-read
        had been read after all.
        """
        return failures_of((*self.page_numbers, *(page_numbers or ())))

    def without(self, page_number: int) -> "ParseFailures":
        """This record with `page_number` removed — a repair, and nothing else.

        Raises ValueError when the page is not named: this is the writer-side
        invariant of a successful retry (a page that was not failed cannot have
        been repaired), so an unstated page here means the caller's premise was
        wrong and the record must not be rewritten on it.
        """
        if page_number not in self.page_numbers:
            raise ValueError(
                f"page {page_number!r} is not recorded as failed, so it cannot be "
                f"reconciled as repaired"
            )
        return ParseFailures(tuple(n for n in self.page_numbers if n != page_number))

    def as_line(self) -> str:
        """The deterministic line this record persists.

        Written for EVERY run, including a run with no failures: a reader must be
        able to tell "this run read every page it analysed" from "this run kept
        no record of which pages failed", and only a stated record can do that.
        """
        pages = (
            ",".join(str(number) for number in self.page_numbers)
            if self.page_numbers
            else PARSE_FAILURES_NONE
        )
        return f"{PARSE_FAILURES_PREFIX}{_PARSE_FAILURES_FIELD}={pages}"


def failures_of(page_numbers: Iterable[int]) -> ParseFailures:
    """The failure record for a set of page numbers, canonicalised.

    Raises ValueError on anything that is not a 1-based page number: a record
    the writer cannot state is a record the reader would have to guess at, and
    this one is read back to decide whether a page may be read again.
    """
    numbers = []
    for number in page_numbers or ():
        if not _is_page_number(number):
            raise ValueError(f"{number!r} is not a 1-based page number")
        numbers.append(number)
    return ParseFailures(tuple(sorted(set(numbers))))


def parse_failures_from_warnings(warnings) -> ParseFailures | None:
    """The failures record among a project's persisted warnings, or None.

    Read back from the line this module wrote, by the one prefix defined above —
    never by pattern-matching prose. `None` means the project carries no
    READABLE failures record, which covers both "this run predates J17" and "the
    line is not one this module wrote": an unreadable record is no record, and a
    caller that needs a page's state must not derive it from one.

    A malformed line is refused rather than repaired — including one whose page
    numbers are not in the canonical ascending order, because this module writes
    them that way and a record that differs was not written by a run of this
    code describing these pages.
    """
    for warning in warnings or []:
        if not (isinstance(warning, str) and warning.startswith(PARSE_FAILURES_PREFIX)):
            continue
        name, separator, value = warning[len(PARSE_FAILURES_PREFIX):].partition("=")
        if name != _PARSE_FAILURES_FIELD or not separator:
            return None
        if value == PARSE_FAILURES_NONE:
            return ParseFailures(())
        numbers = []
        for token in value.split(","):
            if not token.isdigit() or int(token) < 1:
                return None
            numbers.append(int(token))
        if numbers != sorted(set(numbers)):
            return None
        return ParseFailures(tuple(numbers))
    return None


@dataclass(frozen=True)
class RetryDecision:
    """Whether one page may be read again, and — when it may not — why not.

    `accepted` is True exactly when `page_number` is set; a refusal always
    carries both a code and the facts behind it.
    """

    page_number: int | None
    refusal_code: str | None = None
    detail: str | None = None

    @property
    def accepted(self) -> bool:
        return self.page_number is not None


def check_retry_request(
    *,
    page_number,
    coverage,
    failures: ParseFailures | None,
) -> RetryDecision:
    """Whether `page_number` is a page this drawing set may read again.

    The decision is made entirely from PERSISTED state — the J15 coverage record
    and this module's failures record — so it is answered before anything is
    downloaded, rendered or put to the model. That ordering is the point: a
    request that must not be served is refused without spending an AI call, and
    refused without any chance of writing evidence for a page that did not
    qualify.

    The checks run in the order below and the FIRST applicable refusal is the
    answer, so a request that is wrong in two ways is answered deterministically
    rather than by whichever branch a reader happens to look at first.

    Nothing here re-reads anything or clears a failure: a page is no longer
    failed only when the record that names it is rewritten WITHOUT it, which
    happens when a retry of that page has succeeded and for no other reason.
    """
    if not _is_page_number(page_number):
        return RetryDecision(
            None, RETRY_PAGE_MALFORMED,
            f"a retry must name one 1-based page number (got {page_number!r})",
        )

    # A record that contradicts itself states no page count, so it cannot say
    # what the document's pages are — and a page number can only be checked
    # against a page count that adds up.
    if not coverage.identity_holds:
        return RetryDecision(
            None, COVERAGE_UNUSABLE,
            "the committed coverage record states no page count that adds up, so it "
            "cannot say which pages this drawing set has",
        )

    if failures is None:
        return RetryDecision(
            None, RETRY_FAILURES_UNRECORDED,
            "this project carries no readable record of WHICH pages failed to parse "
            "(its coverage record predates page-level failure identity), so a page "
            "cannot be identified for retry; the failure count it does state still "
            "keeps the project out of done",
        )

    # The two records are written together and must agree. Disagreement means one
    # of them no longer describes this drawing set, and a retry decided on a
    # record that contradicts itself would be acting on neither half truthfully.
    if failures.count != coverage.parse_failed_pages:
        return RetryDecision(
            None, RETRY_FAILURES_CONFLICT,
            f"the coverage record states {coverage.parse_failed_pages} parse-failed "
            f"page(s), but the failures record names {failures.count} "
            f"({', '.join(str(n) for n in failures.page_numbers) or 'none'}); the record "
            f"contradicts itself and neither half may be acted on",
        )

    if page_number > coverage.total_pages:
        return RetryDecision(
            None, RETRY_PAGE_BEYOND_DOCUMENT,
            f"page {page_number} is past the last page ({coverage.total_pages}) of this "
            f"drawing set",
        )

    if not failures.names(page_number):
        return RetryDecision(
            None, RETRY_PAGE_NOT_FAILED,
            f"page {page_number} is not recorded as a page whose response could not be "
            f"read, so it is already answered — by evidence, if it was read, or by "
            f"nothing, if it was never analysed; a retry would re-read a page that has "
            f"one",
        )

    return RetryDecision(page_number)
