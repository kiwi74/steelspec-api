"""
Milestone J18 — PAGE-LEVEL EXCEPTION CONTRACT.

Milestone J17 gave a page whose response could not be read an IDENTITY and a way
to read exactly that page again:

    PDF coverage: total=60 analysed=60 parse_failed=1 not_analysed=0
    PDF parse failures: pages=37
    POST /retry-extraction/{project_id}?page=37

What a human has, until this module, is a route. Nothing in the review surface
says WHICH pages are in that state, so the capability cannot be acted on from
where a reviewer looks. This module answers one question for that surface:

    "Which pages of this drawing set could not be read, and which of them may be
     read again right now?"

Every part of that answer comes from somewhere that already owns it:

  - WHICH pages are exceptions: the J17 failures record's own page numbers, in
    the record's own canonical order. Never inferred from a count, never
    renumbered, never re-sorted.
  - WHETHER a page may be read again: J17's own `check_retry_request`, called
    with the same two records the retry route calls it with. This module has no
    opinion about it, which is why the surface cannot offer a Retry for a page
    that J17 would refuse for a reason already decidable from persisted state.
  - WHETHER the record can be listed at all: the two halves must agree, and the
    reason it cannot is stated with the code J16/J17 already own for it.
  - WHAT the project's status is: the caller's value, verbatim. J13 derives it;
    nothing here re-derives one, and this module has no status vocabulary.

WHY THIS IS NOT A 7AK CONNECTION CONTRACT. The 7AK contract projects a
CONNECTION: a package id, a decision, blocker codes, resolution tasks, extracted
values, field provenance. A page whose response could not be read has none of
those and cannot be given them — no evidence was read from it, so there is no
package, no decision and no task, and manufacturing a placeholder package would
put a connection into the review queue that does not exist. A parse failure is a
DOCUMENT-processing exception, not an engineering one. This is the smallest
dedicated extension that states it: one page number, one type, one state, the
reason, and the one action J17 makes available for it.

WHY IT READS THE COMMITTED RECORD AND NOT THE INTAKE. The in-process 7Y intake
also carries page numbers the model could not be parsed for
(`ProjectExtractionIntake.parse_failed_pages`), and it would be convenient to
project those instead, since a review session already holds a workflow. It would
also be WRONG: the retry does not act on the intake. `plan_retry` reads
`projects.warnings` and decides against that record, so an exception listed from
the intake could offer a page the committed record does not name — and the human
would press Retry on a page that is then refused. The two records agree when
everything worked and nothing keeps them in step when it did not, so this module
reads the one the retry will read.

WHAT THIS MODULE DOES NOT DO. It is pure: no Supabase, no filesystem, no AI, no
network, no rendering, no retry, no reconstruction of the record. It never reads
a page, never validates a document and never persists anything. It owns no
refusal vocabulary: every code it can state is imported from the milestone that
decides it.
"""

from dataclasses import dataclass

# The two halves of the committed record, each read by the module that owns it.
from app.validation.page_coverage import PageCoverage
from app.validation.page_windows import CONTINUATION_NO_COVERAGE_RECORD
from app.validation.parse_failures import (
    RETRY_FAILURES_CONFLICT,
    RETRY_FAILURES_UNRECORDED,
    ParseFailures,
    check_retry_request,
)

__all__ = [
    "ACTION_RETRY_PAGE",
    "PAGE_EXCEPTION_SCOPE_STATEMENT",
    "PAGE_EXCEPTION_STATE_RETRYABLE",
    "PAGE_EXCEPTION_TYPE_PARSE_FAILURE",
    "PageException",
    "PageExceptionContract",
    "build_page_exception_contract",
]

# The one exception type this contract states. Named for the record it comes
# from ("PDF parse failures") rather than for a cause this module cannot observe:
# the page WAS read and put to the model, and what could not be read is the
# response.
PAGE_EXCEPTION_TYPE_PARSE_FAILURE = "PDF_PARSE_FAILURE"

# A page the committed record names AND that J17's contract currently accepts may
# be read again. This is a STATE of the record, not a decision of this module:
# `check_retry_request` produced it, and a page it refuses carries that refusal's
# own code as its state instead — never a word this module made up.
PAGE_EXCEPTION_STATE_RETRYABLE = "retryable"

# The one action this contract offers: read this exact page again. It is named in
# the same shape as the 7AK action vocabulary (REVIEW / RESOLVE / REFRESH) and is
# deliberately the ONLY action here — this surface has no approve, no override,
# no resolve and no generate, because a page that could not be read is a
# document-processing exception with exactly one remedy.
ACTION_RETRY_PAGE = "RETRY_PAGE"

PAGE_EXCEPTION_SCOPE_STATEMENT = (
    "This contract is a read-only view of the pages a drawing set's committed record names as "
    "unread, for a human to decide whether to read one again. The page numbers, the refusal "
    "codes and the decision that a page may be read again all come from the existing coverage "
    "and parse-failure records; nothing here can change them, and nothing here reads a page."
)


@dataclass(frozen=True)
class PageException:
    """
    One page of one drawing set whose response could not be read, presented for a
    human: the page number exactly as the failures record states it, the exception
    type, the state, the reason, and the actions actually available for it right
    now.

    `state` is `retryable` when J17's own contract accepts this page, and
    otherwise the refusal code J17 decided it with (`RETRY_PAGE_BEYOND_DOCUMENT`,
    `COVERAGE_UNUSABLE`, …). `detail` is the refusal's own words in that case, so
    a refusal is never translated into something friendlier than what was decided.
    """

    project_id: str | None
    page_number: int
    exception_type: str
    state: str
    detail: str
    available_actions: tuple[str, ...]


@dataclass(frozen=True)
class PageExceptionContract:
    """
    The immutable, UI-safe view of every page-level exception a drawing set's
    committed record states, at the record's own revision.

    `coverage` and `failures` are the record's two halves as its own readers read
    them back, or None when the project carries no such line. `record_refusal` is
    None when the record could be listed, and otherwise the code of the reason it
    could not — in which case `exceptions` is EMPTY rather than partial: a record
    that contradicts itself states no page count and no page list, and listing
    half of it would be presenting one half of a contradiction as fact.

    `project_status` is the caller's value verbatim (J13's derived status, read
    from the project row, or None when the caller does not know it). Nothing in
    this dataclass is a claim this module made.

    There is deliberately NO project-level action list. Every action in this
    surface is per-page, and a project-level "Retry page" would be a page-less
    retry — at best meaningless, at worst read as retry-everything. The actions a
    human actually has are on the exceptions that carry them.
    """

    project_id: str | None
    project_status: str | None
    coverage: PageCoverage | None
    failures: ParseFailures | None
    record_refusal: str | None
    exceptions: tuple[PageException, ...]
    summary: str


def _record_refusal(coverage, failures) -> str | None:
    """Why this record cannot be listed at all, or None when it can.

    The three cases are the three ways the committed record fails to say which
    pages are exceptions, and each is answered with the code the milestone that
    already refuses that state uses — a reader here and a retry there name the
    same state the same way.
    """
    if coverage is None:
        return CONTINUATION_NO_COVERAGE_RECORD
    if failures is None:
        return RETRY_FAILURES_UNRECORDED
    if failures.count != coverage.parse_failed_pages:
        return RETRY_FAILURES_CONFLICT
    return None


def _exception(project_id, page_number, coverage, failures) -> PageException:
    """One page as J17's own contract currently decides it."""
    decision = check_retry_request(
        page_number=page_number, coverage=coverage, failures=failures,
    )
    if decision.accepted:
        return PageException(
            project_id=project_id,
            page_number=page_number,
            exception_type=PAGE_EXCEPTION_TYPE_PARSE_FAILURE,
            state=PAGE_EXCEPTION_STATE_RETRYABLE,
            detail=(
                "this page was read and its response could not be parsed as engineering "
                "evidence, so it contributes no member and no connection; the record names it, "
                "so it can be read again"
            ),
            available_actions=(ACTION_RETRY_PAGE,),
        )
    # A page the record names but that cannot currently be read again is still an
    # exception — it is one of the pages that keeps the drawing set incomplete.
    # Its state is the refusal's own code, so the surface states what J17 would
    # answer rather than offering an action that would be refused.
    return PageException(
        project_id=project_id,
        page_number=page_number,
        exception_type=PAGE_EXCEPTION_TYPE_PARSE_FAILURE,
        state=decision.refusal_code,
        detail=decision.detail,
        available_actions=(),
    )


def build_page_exception_contract(
    *, project_id, coverage, failures, project_status=None,
) -> PageExceptionContract:
    """
    Builds the page-exception contract for one drawing set from its committed
    record: the J15 coverage record and the J17 failures record, as their own
    readers read them back from the project's warnings, plus the project's status
    as its own authority derived it.

    Every argument is a value the caller already has; nothing is read here. A
    record whose two halves disagree, or which is missing a half, is not listed
    partially — the contract states the reason and carries no exceptions.

    Reads nothing, decides no engineering question, writes nothing, and offers the
    Retry action only for the pages J17's own contract accepts right now.
    """
    if coverage is not None and not isinstance(coverage, PageCoverage):
        raise TypeError(
            f"coverage must be a PageCoverage or None (got {type(coverage).__name__}); this "
            "contract projects the existing coverage record."
        )
    if failures is not None and not isinstance(failures, ParseFailures):
        raise TypeError(
            f"failures must be a ParseFailures or None (got {type(failures).__name__}); this "
            "contract projects the existing parse-failure record."
        )

    refusal = _record_refusal(coverage, failures)
    # `failures` is non-None exactly when `refusal` is None, so the page list is
    # the record's own and is never rebuilt from a count.
    exceptions = (
        ()
        if refusal is not None
        else tuple(
            _exception(project_id, page_number, coverage, failures)
            for page_number in failures.page_numbers
        )
    )
    lines = [
        f"project = {project_id or 'unknown'}; status = {project_status or 'unknown'}",
        coverage.as_line() if coverage is not None else "no PDF coverage record",
        failures.as_line() if failures is not None else "no PDF parse-failure record",
    ]
    if refusal is not None:
        lines.append(f"record not listable: {refusal}")
    for exception in exceptions:
        lines.append(
            f"page {exception.page_number}: {exception.exception_type} — {exception.state}"
        )
    lines.append(PAGE_EXCEPTION_SCOPE_STATEMENT)

    return PageExceptionContract(
        project_id=project_id,
        project_status=project_status,
        coverage=coverage,
        failures=failures,
        record_refusal=refusal,
        exceptions=exceptions,
        summary="\n".join(lines),
    )
