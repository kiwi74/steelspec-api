"""
Milestone J18 — DETERMINISTIC UI VIEW MODEL FOR PAGE-LEVEL EXCEPTIONS.

The 7AM boundary, applied to the 7AK-shaped page-exception contract: a pure
presentation adapter that turns the contract into immutable plain data a page
renderer can print. It answers only one question:

    "How should the already-established page-exception contract be presented?"

It never answers "which page should be retried". Every page number, state,
refusal code, reason and available action comes from the contract verbatim; this
module adds deterministic human labels alongside them and computes nothing:

  - NO NEW STATES. A page's state is the contract's own string — `retryable`, or
    the code J17 refused it with. A value with no presentation label is shown AS
    ITSELF (7AM's rule for an unknown value), never renamed, never softened.
  - NO INFERENCE FROM THE RECORD. The two record lines are printed exactly as the
    record states them, and a record the contract could not list is presented as
    not listable — the surface never shows an empty list where the contract says
    the record contradicts itself.
  - THE TRANSIENT RESULT IS NOT PART OF THE RECORD. `PageRetryNotice` describes
    what ONE human action just returned, and it is built from the values J17
    itself reported — never from what the surface hoped happened. It is passed in
    alongside the contract and never merged into it: the durable truth stays the
    record, and a notice is never evidence that the record changed.
  - MISSING IS MISSING. No coverage record, no status, no reason: the view holds
    None and says so. Nothing is filled in to make a screen look complete.
  - IMMUTABLE AND DETERMINISTIC. Frozen dataclasses of plain data; the page order
    is the contract's own; the same contract always renders to the same view.
  - PURE AND UI-NEUTRAL. No markup, no templates, no styling, no browser code,
    no API, no database, no network, no AI, no environment, no filesystem.
"""

from dataclasses import dataclass

from app.cad_engine.page_exception_contract import (
    ACTION_RETRY_PAGE,
    PAGE_EXCEPTION_STATE_RETRYABLE,
    PAGE_EXCEPTION_TYPE_PARSE_FAILURE,
    PageExceptionContract,
)
from app.cad_engine.review_view_model import ActionView
from app.validation.page_windows import (
    CONTINUATION_NO_COVERAGE_RECORD,
    CONTINUATION_RECORD_CONFLICT,
)
from app.validation.parse_failures import (
    RETRY_FAILURES_CONFLICT,
    RETRY_FAILURES_UNRECORDED,
)
from app.validation.project_status import STATUS_DONE, STATUS_REVIEW

__all__ = [
    "PageExceptionListView",
    "PageExceptionView",
    "PageRetryNotice",
    "render_page_exception_list_view",
    "retry_notice",
]

# --------------------------------------------------------------------------------------
# Deterministic presentation tables: an existing machine value -> a human label.
# A value with no entry is shown as itself (the 7AM rule) — never guessed at.
# --------------------------------------------------------------------------------------
# The contract's own exception type and the one state a page can be listed in.
_TYPE_LABELS = {
    PAGE_EXCEPTION_TYPE_PARSE_FAILURE: "PDF parse failure",
}
_STATE_LABELS = {
    PAGE_EXCEPTION_STATE_RETRYABLE: "Awaiting retry",
    # The two strings J17 itself reports for a retry's outcome, and the one the
    # retry route reports for a request it accepted (app/main.py). They are the
    # ONLY outcome states this surface knows; a refusal's own code is its label.
    "retry_started": "Retry started",
    "PARSED": "Read again successfully",
    "PARSE_FAILED": "Still could not be read",
}
_ACTION_LABELS = {
    ACTION_RETRY_PAGE: "Retry page",
}
# Why a committed record could not be listed, said in the words of the milestone
# that owns the code. The code itself is shown alongside this.
_RECORD_REFUSAL_MESSAGES = {
    CONTINUATION_NO_COVERAGE_RECORD: (
        "This project carries no PDF coverage record, so which pages have been read is "
        "unknown and no page-level exception can be stated."
    ),
    RETRY_FAILURES_UNRECORDED: (
        "This project carries no record of WHICH pages failed to parse — its coverage "
        "record predates page-level failure identity. The failure count it does state still "
        "keeps the project out of done, but no page can be named or retried."
    ),
    RETRY_FAILURES_CONFLICT: (
        "The coverage record's failure count and the parse-failure record's page list "
        "disagree, so the record contradicts itself and neither half is presented as fact."
    ),
    CONTINUATION_RECORD_CONFLICT: (
        "The drawing set's own counters and the coverage record disagree about how much has "
        "been read, so which page this is cannot be trusted."
    ),
}
_STATUS_LABELS = {
    STATUS_DONE: "Done",
    STATUS_REVIEW: "Needs review",
}


@dataclass(frozen=True)
class PageExceptionView:
    """One page-level exception as the contract states it, with labels added."""
    page_number: int
    exception_type: str
    type_label: str
    state: str
    state_label: str
    detail: str
    actions: tuple[ActionView, ...]


@dataclass(frozen=True)
class PageRetryNotice:
    """
    What ONE retry attempt just reported — the transient half of the surface.

    Every field is a value J17 itself stated: the page it was asked about, the
    string it answered with (an outcome, the route's `retry_started`, or the code
    it refused with), the reason in its own words, and whether it said the page
    stopped being a failure. `resolved` is None whenever J17 did not say — which
    includes every refusal and every request that was only accepted, so an
    accepted request can never be presented as a successful one.

    This is NOT part of the record: the record is the contract, and the surface
    re-renders the record as its own authority states it either way.
    """

    page_number: int | None
    state: str
    state_label: str
    detail: str
    resolved: bool | None


@dataclass(frozen=True)
class PageExceptionListView:
    """
    The whole page-exception surface: the project and its status, the committed
    record's own two lines verbatim, why the record could not be listed when it
    could not, the page exceptions in the record's own order — each carrying the
    actions actually available for it — and, when an action was just performed,
    what it reported.
    """

    project_id: str | None
    project_status: str
    status_label: str
    summary: str
    coverage_line: str
    failures_line: str
    record_refusal: str | None
    record_refusal_message: str | None
    exceptions: tuple[PageExceptionView, ...]
    notice: PageRetryNotice | None


# --------------------------------------------------------------------------------------
# Presentation helpers — pure functions over the contract's own values.
# --------------------------------------------------------------------------------------
def _label(table, value):
    """A deterministic human label for an existing machine value; a value with no
    entry falls back to itself — never dropped, never renamed."""
    if value is None:
        return None
    return table.get(value, value)


def _actions(actions, page_number):
    """One page exception's own actions, labelled. The retry label names the page
    it targets, because an action whose subject is a page number must say which
    one — a button reading "Retry" on a screen of three failed pages does not.

    The action view itself is 7AM's `ActionView`, not a second class with the same
    two fields: an available action and how to label it is the same thing here as
    it is for a connection, and a duplicate would be one more thing to keep in
    step for no gain."""
    return tuple(
        ActionView(
            action,
            f"{_ACTION_LABELS[action]} {page_number}"
            if action == ACTION_RETRY_PAGE else _label(_ACTION_LABELS, action),
        )
        for action in actions
    )


def retry_notice(*, page_number, state, detail, resolved=None) -> PageRetryNotice:
    """The transient result of one retry, presented from J17's own values.

    `state` is passed through verbatim — an outcome, the route's `retry_started`,
    or a refusal code — and gets a label only when one exists. `resolved` is
    forwarded exactly as J17 stated it (None when it stated nothing), so nothing
    here can turn an accepted request, or a refusal, into a success.
    """
    return PageRetryNotice(
        page_number=page_number,
        state=state,
        state_label=_label(_STATE_LABELS, state),
        detail=detail,
        resolved=resolved,
    )


def _exception_views(contract):
    return tuple(
        PageExceptionView(
            page_number=exception.page_number,
            exception_type=exception.exception_type,
            type_label=_label(_TYPE_LABELS, exception.exception_type),
            state=exception.state,
            state_label=_label(_STATE_LABELS, exception.state),
            detail=exception.detail,
            actions=_actions(exception.available_actions, exception.page_number),
        )
        for exception in contract.exceptions
    )


def render_page_exception_list_view(contract, notice=None) -> PageExceptionListView:
    """
    Renders a page-exception contract into an immutable plain-data view. Every
    field comes from the contract — the page numbers and their order, the states,
    the reasons, the actions and the record's own two lines — with presentation
    labels added alongside; nothing is inferred, invented, or made actionable that
    the contract does not already permit.

    `notice` is the transient result of the action the caller just performed, and
    it is passed through as what it is: see `PageRetryNotice`.
    """
    if not isinstance(contract, PageExceptionContract):
        raise TypeError(
            f"contract must be a PageExceptionContract (got {type(contract).__name__}); the "
            "view model renders the existing page-exception contract, nothing else."
        )
    if notice is not None and not isinstance(notice, PageRetryNotice):
        raise TypeError(
            f"notice must be a PageRetryNotice or None (got {type(notice).__name__}); a "
            "transient result is never merged into the record's own view."
        )
    return PageExceptionListView(
        project_id=contract.project_id,
        project_status=contract.project_status or "unknown",
        status_label=_label(_STATUS_LABELS, contract.project_status) or "Unknown",
        summary=contract.summary,
        coverage_line=contract.coverage.as_line() if contract.coverage is not None else "",
        failures_line=contract.failures.as_line() if contract.failures is not None else "",
        record_refusal=contract.record_refusal,
        record_refusal_message=(
            _RECORD_REFUSAL_MESSAGES.get(contract.record_refusal)
            if contract.record_refusal is not None else None
        ),
        exceptions=_exception_views(contract),
        notice=notice,
    )
