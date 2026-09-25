"""
Milestone J15 — how much of the uploaded PDF drawing set this run actually read.

The production PDF path renders pages and analyses them, but until this module
it accounted for only the pages it DID read: `app/pipeline.py` wrote
`total_pages = pages_processed`, so a 35-page drawing set analysed up to a
30-page cap was persisted as a 30-page drawing set. Nothing anywhere recorded
that five pages had never been looked at, and nothing recorded that a page
which failed to parse was not the same thing as a page that was read and found
to contain no steel. Both absences read as "complete".

This module states the four quantities that make coverage checkable, and the
identity that must hold between them:

    TOTAL_PAGES          the drawing set's OWN page count, read from the
                         document itself — never the number of pages this run
                         chose to process
    ANALYSED_PAGES       pages that were rendered, uploaded and put to the
                         vision model
    PARSE_FAILED_PAGES   analysed pages whose response could not be read as
                         engineering evidence
    NOT_ANALYSED_PAGES   document pages this run never reached

    total == analysed + not_analysed

PARSE_FAILED_PAGES is a SUBSET of ANALYSED_PAGES, not a fifth state: a page
reaches `parse_failed` only after it was rendered and uploaded, so it was
analysed. A parse failure is therefore never "not analysed", and it is never
"an analysed page that contained no steel" either — that page parsed perfectly
and the model's answer was that the drawing states nothing. The three outcomes
are kept distinct here because only one of them is a reading failure.

WHY THIS IS A SEPARATE MODULE. The four counts are the J13 lifecycle's missing
input: "all required evidence is explicitly resolved" cannot be answered
truthfully about a drawing set whose pages were partly never read. Keeping the
arithmetic here — pure, free of any Supabase, filesystem or AI call — is what
lets `app/validation/project_status.py` consult coverage without acquiring a
dependency, and what makes every rule below unit-testable on counts alone.

The persisted form follows the accepted Milestone J11 precedent
(`app/validation/dxf_drop_accounting.py`): an extraction that cannot claim
completeness states so, in one deterministic line, in `projects.warnings` — the
column that already carries what a run did not read. The line is written for
EVERY run, complete or not, so that its absence in a stored project means
"this run predates coverage accounting" rather than "nothing to report", and it
is read back through this module's own parser rather than by matching prose.
"""

from dataclasses import dataclass
from typing import Iterable

__all__ = [
    "COVERAGE_PREFIX",
    "COVERAGE_UNKNOWN",
    "PageCoverage",
    "coverage_from_warnings",
    "coverage_of",
]

# Every persisted coverage line begins with this. Defined once, here, so the
# writer and the reader cannot drift apart.
COVERAGE_PREFIX = "PDF coverage: "

# Written in place of a count that could not be established. Deliberately a
# word, not a 0: a zero page count is a statement, and this is the absence of
# one.
COVERAGE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageCoverage:
    """What one extraction run read of the drawing set it was given.

    `total_pages` and `not_analysed_pages` are `None` when the document's own
    page count could not be established. That is not a coverage failure to be
    smoothed over with the counts this run does have: an unknown total is
    exactly the state in which "did we read all of it?" has no answer, and
    `is_complete` refuses it.
    """

    total_pages: int | None
    analysed_pages: int
    parse_failed_pages: int
    not_analysed_pages: int | None

    @property
    def identity_holds(self) -> bool:
        """Whether `total == analysed + not_analysed` can be checked and holds.

        False when either side is unstated, and false when the three counts
        contradict each other. This is the one arithmetic claim a coverage
        record makes, so a record whose own counts disagree proves nothing and
        is refused rather than read selectively.
        """
        if self.total_pages is None or self.not_analysed_pages is None:
            return False
        return self.analysed_pages + self.not_analysed_pages == self.total_pages

    @property
    def is_complete(self) -> bool:
        """True only when every page of the document was read AND parsed.

        Every clause is stated rather than inferred: a page that was never
        reached, a page whose response could not be read, and counts that do
        not add up all fail here — and anything that is not a proven complete
        coverage is not completeness.
        """
        return (
            self.identity_holds
            and self.not_analysed_pages == 0
            and self.parse_failed_pages == 0
        )

    @property
    def is_known(self) -> bool:
        """True when the document's own page count was established."""
        return self.total_pages is not None

    def as_line(self) -> str:
        """The deterministic line this run persists, in every case.

        Written even when coverage is complete: a reader must be able to tell
        "this run read all 10 pages" from "this run kept no record", and only a
        stated count can do that.
        """
        return (
            f"{COVERAGE_PREFIX}"
            f"total={self.total_pages if self.total_pages is not None else COVERAGE_UNKNOWN} "
            f"analysed={self.analysed_pages} "
            f"parse_failed={self.parse_failed_pages} "
            f"not_analysed="
            f"{self.not_analysed_pages if self.not_analysed_pages is not None else COVERAGE_UNKNOWN}"
        )


def coverage_of(
    *,
    total_pages: int | None,
    page_numbers: Iterable[int],
    parse_failed_page_numbers: Iterable[int] = (),
) -> PageCoverage:
    """The coverage of one run, from what it read and what the document states.

    `total_pages` is the document's own page count, read before any processing
    cap is applied; `page_numbers` are the pages this run actually analysed;
    `parse_failed_page_numbers` are those of them whose response could not be
    read.

    Every inconsistency collapses the total to unknown rather than producing a
    number that looks like a measurement and is not:

      * a parse failure on a page that was never analysed, or
      * the same page counted twice, or
      * an analysed page outside 1..total, or
      * more analysed pages than the document has, or
      * a "total" that is not a positive whole page count,

    are all states in which the two sides of the identity disagree. A run that
    cannot account for its own pages has not established coverage, and this
    function says so instead of guessing which side was wrong.
    """
    analysed = tuple(page_numbers or ())
    failed = tuple(parse_failed_page_numbers or ())

    # A "total" that is not a positive whole page count states nothing, so it is
    # read as unknown before it is used to check anything else.
    if total_pages is not None and (
        isinstance(total_pages, bool)
        or not isinstance(total_pages, int)
        or total_pages < 1
    ):
        total_pages = None

    self_consistent = (
        total_pages is not None
        and set(failed) <= set(analysed)
        and len(set(analysed)) == len(analysed)
        and all(1 <= page <= total_pages for page in analysed)
        and len(analysed) <= total_pages
    )
    if not self_consistent:
        return PageCoverage(
            total_pages=None,
            analysed_pages=len(analysed),
            parse_failed_pages=len(failed),
            not_analysed_pages=None,
        )

    return PageCoverage(
        total_pages=total_pages,
        analysed_pages=len(analysed),
        parse_failed_pages=len(failed),
        not_analysed_pages=total_pages - len(analysed),
    )


def coverage_from_warnings(warnings) -> PageCoverage | None:
    """The coverage record among a project's persisted warnings, or None.

    Read back from the line this module wrote, by the one prefix defined above —
    never by pattern-matching prose. `None` means the project carries no
    coverage record at all, which is not the same as a complete one.
    """
    for warning in warnings or []:
        if not (isinstance(warning, str) and warning.startswith(COVERAGE_PREFIX)):
            continue
        fields: dict[str, int | None] = {}
        for part in warning[len(COVERAGE_PREFIX):].split():
            name, separator, value = part.partition("=")
            if not name or not separator:
                return None
            if value == COVERAGE_UNKNOWN:
                fields[name] = None
            elif value.isdigit():
                fields[name] = int(value)
            else:
                # A count this module did not write is not a count it will read:
                # an unreadable record is no record.
                return None
        if set(fields) != {"total", "analysed", "parse_failed", "not_analysed"}:
            return None
        if fields["analysed"] is None or fields["parse_failed"] is None:
            return None
        return PageCoverage(
            total_pages=fields["total"],
            analysed_pages=fields["analysed"],
            parse_failed_pages=fields["parse_failed"],
            not_analysed_pages=fields["not_analysed"],
        )
    return None
