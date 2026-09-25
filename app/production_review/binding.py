"""
Milestone J19 — BINDING ONE AUTHORIZED PROJECT INTO THE EXISTING REVIEW CHAIN.

This is the whole of the binding. It produces the SAME objects the existing
review UI consumes, from production state, by calling the same three builders it
calls and in the same order:

    ProjectReviewRecord.page_exception_contract()          J18's contract
      -> render_page_exception_list_view(contract, notice) J18's view model (7AM's
                                                           boundary, applied)
      -> render.render_page_exceptions_page(view, ...)     the existing page

and for the human action, the SAME retry handoff:

    session.render_page_retry_outcome(...)                 J18's one interpretation
      -> the existing J17 retry contract, called by its caller

Nothing is forked. There is no "production review UI": there is one contract,
one view model, one renderer and one retry handoff, and this module is the wiring
that feeds production state into them.

WHY THERE IS NO SESSION AND NO PROCESS-GLOBAL STATE HERE
========================================================
The internal review UI (7AN/7AO) keeps its binding in module globals: one bound
workflow per process, one bound page-exception record per process. That is
correct for what it is — an in-process vertical slice — and it is NOT safe for a
production HTTP surface, where two reviewers' requests are served by the same
process. A process-global binding would let reviewer A's request be answered from
reviewer B's project, which is precisely the cross-user leak this milestone is
required to prevent. Serialising requests around a global would hide the race
rather than remove it, and would still hand a stale project to a later request.

So the production binding is PER REQUEST and holds no global: it is a frozen
value assembled from the reviewer's established identity, the project record that
was loaded for it, and the access decision that allowed it. Two concurrent
requests cannot see each other's binding because there is nothing shared to see —
that is a structural property of this module, not a promise about it, and the J19
test module proves it by interleaving two reviewers' requests.

A binding is PROOF-CARRYING. `bind_project_review` accepts only an ALLOWED
`AccessDecision`, so there is no way to construct one for a project the
authenticated reviewer was not authorized for; a caller cannot skip the
authorization step by calling the binder directly.

NO AUTOMATIC MUTATION. Every function here is a read, a render or a handoff to
J17. Loading a project's review page performs no extraction, no AI call, no
retry, no evidence write and no status write, and it never calls J13: the status
it displays is the status J13 already persisted on the project row, printed by
the existing view as the record's own value. The ONLY mutating path this module
exposes is `bound_page_retry`, which does nothing itself and passes the page to
the retry contract its caller supplied.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.cad_engine.page_exception_view_model import (
    PageExceptionListView,
    render_page_exception_list_view,
)
from app.production_review.authorization import AccessDecision
from app.production_review.identity import ReviewerIdentity
from app.production_review.project_read import ProjectReviewRecord
from app.review_ui import render, session

__all__ = [
    "BINDING_NOT_AUTHORIZED",
    "BINDING_REFUSALS",
    "BINDING_UNIDENTIFIED",
    "BINDING_WRONG_PROJECT",
    "BindingRefused",
    "ProjectReviewBinding",
    "bind_project_review",
    "bound_page_retry",
    "render_bound_review",
    "review_view",
]

BINDING_UNIDENTIFIED = "BINDING_UNIDENTIFIED"
BINDING_NOT_AUTHORIZED = "BINDING_NOT_AUTHORIZED"
BINDING_WRONG_PROJECT = "BINDING_WRONG_PROJECT"

BINDING_REFUSALS = (BINDING_UNIDENTIFIED, BINDING_NOT_AUTHORIZED, BINDING_WRONG_PROJECT)


class BindingRefused(ValueError):
    """No binding was produced, for the stated reason (see `BINDING_REFUSALS`)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProjectReviewBinding:
    """One authenticated reviewer, one authorized project, one request.

    It holds the three things that were established and nothing else: the
    identity the token proved, the record that was loaded for it, and the
    decision that allowed it. No token, no session, no project row, no global —
    so nothing here can outlive the request that made it, and nothing here can be
    handed to a different one.
    """

    identity: ReviewerIdentity
    record: ProjectReviewRecord
    decision: AccessDecision

    @property
    def project_id(self) -> str:
        return self.record.project_id

    @property
    def contract(self):
        """The J18 page-exception contract for this project's persisted record."""
        return self.record.page_exception_contract()


def bind_project_review(*, identity, record, decision) -> ProjectReviewBinding:
    """Binds one project's review record to the reviewer who is allowed to see it.

    Refuses unless all three are true: an identity was established, the record is
    a project review record, and the access decision ALLOWED it for that project.
    The last one is what makes the binding proof-carrying — an unauthorized
    caller has nothing to bind, so no amount of calling this differently produces
    a binding for someone else's project.
    """
    if not isinstance(identity, ReviewerIdentity):
        raise BindingRefused(
            BINDING_UNIDENTIFIED,
            "no reviewer identity was established, so no project can be bound to this request.",
        )
    if not isinstance(record, ProjectReviewRecord):
        raise BindingRefused(
            BINDING_UNIDENTIFIED,
            f"no project review record was loaded (got {type(record).__name__}), so there is "
            "nothing to bind.",
        )
    if not isinstance(decision, AccessDecision) or not decision.allowed:
        code = getattr(decision, "code", None)
        raise BindingRefused(
            BINDING_NOT_AUTHORIZED,
            "this reviewer is not authorized for this project, so it is not bound to this "
            f"request (access decision: {code or 'none'}).",
        )
    if decision.project_id is not None and decision.project_id != record.project_id:
        # The decision was about a different project than the record in hand. Two
        # project identities for one binding is exactly how an authorization
        # answer gets attached to the wrong subject, so it is refused rather than
        # resolved in either direction.
        raise BindingRefused(
            BINDING_WRONG_PROJECT,
            "the access decision and the loaded record name different projects, so neither is "
            "bound.",
        )
    return ProjectReviewBinding(identity=identity, record=record, decision=decision)


def review_view(binding: ProjectReviewBinding, notice=None) -> PageExceptionListView:
    """The bound record as the existing view model states it. Reads nothing."""
    if not isinstance(binding, ProjectReviewBinding):
        raise TypeError(
            f"binding must be a ProjectReviewBinding (got {type(binding).__name__})."
        )
    return render_page_exception_list_view(binding.contract, notice)


def render_bound_review(
    binding: ProjectReviewBinding, *, notice=None, action_prefix: str = "",
) -> str:
    """The bound project's page-exception surface, rendered by the existing renderer.

    `action_prefix` is where this surface is mounted, so its one control and its
    back link name the surface that rendered it rather than a route that is not
    there. Nothing else is passed: the page states the record's own coverage and
    failure lines, the pages J17's own contract names, and the status J13
    persisted — verbatim.
    """
    return render.render_page_exceptions_page(
        review_view(binding, notice), action_prefix=action_prefix,
    )


def bound_page_retry(
    binding: ProjectReviewBinding, page_number: int, *, retry_entry, action_prefix: str = "",
) -> str:
    """Requests the existing J17 retry for ONE page of the bound project.

    The page is the one addressed and the project is the bound one, so no other
    project and no other page can be reached from here — the binding carries the
    project, and `retry_entry` performs J17's own validation of the page. This
    function holds no retry logic, no validation and no state: it is the J18
    handoff (`session.render_page_retry_outcome`), called with a production
    contract instead of a session one, and it returns exactly what that page
    says — including, verbatim, the code a refusal was refused with.

    This is the only function in this module that can lead to a write, and it
    writes nothing itself: whether anything is read, stored or changed is J17's
    decision, made against the persisted record, after the caller has been
    authenticated and authorized again on this request.
    """
    if not isinstance(binding, ProjectReviewBinding):
        raise TypeError(
            f"binding must be a ProjectReviewBinding (got {type(binding).__name__})."
        )
    _contract_after, html = session.render_page_retry_outcome(
        project_id=binding.project_id,
        contract=binding.contract,
        retry_entry=retry_entry,
        page_number=page_number,
        action_prefix=action_prefix,
    )
    return html
