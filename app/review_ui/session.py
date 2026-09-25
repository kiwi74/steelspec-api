"""
Milestone 7AN — the review UI's session layer: the ONLY place the UI touches
the workflow.

The UI's single source of truth is the bound `ProjectWorkflowState`, and the
only operations allowed on it are the existing public workflow boundaries:

    resolve_project_connection(...)   for a submitted resolution
    refresh_project_workflow(...)     for the Refresh action

Every page is produced by the fixed chain workflow -> 7AK contract -> 7AM
view -> pages; the UI never mutates a workflow object, a view object, a
package or a displayed value. A stale resolution is refused by the workflow
itself (StaleProjectWorkflowError) and shown as such — never re-interpreted
as success.

Resolution inputs are parsed here from the form fields the render layer
defines (`task_input_names`), shaped ONLY according to each task's own
authoritative `answer_type` (7AC's vocabulary). Payload shape errors are
refused before anything is submitted; engineering validity remains the
existing gates' decision.

The session is an in-process vertical slice: one bound workflow per process,
no persistence, no auth — deliberately the smallest thing that proves the
chain. Nothing here reads the environment, the network or the filesystem
beyond the artifacts directory handed in at bind time.

J18 adds a SECOND, independent binding for the page-exception surface: the
pages of one drawing set whose response could not be read (J17) and the one
action a human may take on them — read that exact page again, through the
existing J17 retry contract. It does not need a workflow (the records it
projects live in the project row, not in a 7AJ workflow), and it does not own
the retry: the caller binds the retry entry, and this layer can only CALL it.
That is deliberate and structural — importing the pipeline here would pull
Supabase and the AI client into the UI process, which
`test_review_ui_imports_without_any_secret_environment` forbids. So this module
holds no retry algorithm, no page validation and no record arithmetic: it reads
the two record halves with the modules that write them, states each page's state
with J17's own decision function, and reports what J17's own result says —
including, verbatim, the code it refuses with.
"""

import json
from pathlib import Path

from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
    HumanResolution,
)
from app.cad_engine.project_workflow import (
    ConnectionAlreadyProcessedError,
    CrossProjectResolutionError,
    ProjectWorkflowState,
    StaleProjectWorkflowError,
    UnknownProjectPackageError,
    WorkflowStageFailureError,
    refresh_project_workflow,
    resolve_project_connection,
)
from app.cad_engine.page_exception_contract import (
    PageExceptionContract,
    build_page_exception_contract,
)
from app.cad_engine.page_exception_view_model import (
    PageExceptionListView,
    render_page_exception_list_view,
    retry_notice,
)
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import (
    TaskView,
    render_connection_view,
    render_project_view,
)
from app.review_ui import render
from app.validation.page_coverage import PageCoverage, coverage_from_warnings
from app.validation.parse_failures import failures_of, parse_failures_from_warnings

__all__ = [
    "ResolutionInputError", "bind_page_exceptions", "bind_workflow",
    "clear_page_exceptions", "clear_workflow", "current_workflow", "is_bound",
    "is_page_exceptions_bound", "page_exception_view", "parse_task_answer", "project_view",
    "refresh", "render_connection_page", "render_page_exceptions_page",
    "render_page_retry_outcome", "render_project_page", "submit_page_retry",
    "submit_resolution",
]

EVIDENCE_FIELD = "evidence"
REVISION_FIELD = "revision"


class ResolutionInputError(ValueError):
    """The submitted form cannot be turned into a valid resolution input —
    the workflow is never called and nothing changes."""


# --------------------------------------------------------------------------------------
# The bound session state: one workflow, one artifacts directory.
# --------------------------------------------------------------------------------------
_current_workflow: ProjectWorkflowState | None = None
_output_dir: Path | None = None


def bind_workflow(workflow, output_dir) -> None:
    """Binds the workflow this UI session displays and resolves against."""
    global _current_workflow, _output_dir
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__})."
        )
    if not isinstance(output_dir, (str, Path)):
        raise TypeError("output_dir must be a str or Path.")
    _current_workflow = workflow
    _output_dir = Path(output_dir)


def clear_workflow() -> None:
    """Unbinds the session (used by tests and future session teardown)."""
    global _current_workflow, _output_dir
    _current_workflow = None
    _output_dir = None


def is_bound() -> bool:
    return _current_workflow is not None


def current_workflow() -> ProjectWorkflowState:
    if _current_workflow is None:
        raise RuntimeError("no review workflow is bound to this session.")
    return _current_workflow


# --------------------------------------------------------------------------------------
# The fixed view chain: workflow -> contract -> view model.
# --------------------------------------------------------------------------------------
def project_view():
    return render_project_view(build_project_review_contract(current_workflow()))


def connection_view(package_id: str):
    return render_connection_view(build_connection_review_contract(current_workflow(), package_id))


# --------------------------------------------------------------------------------------
# Resolution input parsing — the deliberately small supported set, shaped by the
# task's own answer_type. Anything malformed or unsupported is refused here,
# before the workflow is called.
# --------------------------------------------------------------------------------------
def _parse_attachments(text: str, task_id: str):
    attachments = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ResolutionInputError(
                f"task {task_id}: attachment line {line!r} must be 'member_mark|surface_reference'."
            )
        attachments.append(
            {"member_mark": parts[0].strip(), "surface_reference": parts[1].strip()}
        )
    if not attachments:
        raise ResolutionInputError(f"task {task_id}: at least one attachment is required.")
    return tuple(attachments)


def _parse_mapping(text: str, task_id: str):
    mapping = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, sep, raw = line.partition(":")
        if not sep or not key.strip() or not raw.strip():
            raise ResolutionInputError(f"task {task_id}: line {line!r} must be 'key: value'.")
        value = raw.strip()
        try:
            value = json.loads(value)  # numbers/bools parse to their types
        except ValueError:
            pass  # otherwise the raw string stays as typed
        mapping[key.strip()] = value
    if not mapping:
        raise ResolutionInputError(f"task {task_id}: at least one field value is required.")
    return mapping


def _comma_list(text: str, task_id: str):
    values = tuple(part.strip() for part in text.split(",") if part.strip())
    if not values:
        raise ResolutionInputError(
            f"task {task_id}: at least one comma-separated value is required."
        )
    return values


def parse_task_answer(task: TaskView, form: dict) -> object:
    """One task's submitted form input -> the payload its answer_type requires.

    The payload is shaped here (never evaluated): the existing workflow's own
    validation remains authoritative for whether the answer is acceptable.
    """
    names = render.task_input_names(task)
    text = lambda i: form.get(names[i], "")
    if task.answer_type in (ANSWER_APPROVE_REVIEW, ANSWER_AUTOMATION_CONFIRMATION):
        return None
    if task.answer_type == ANSWER_ACKNOWLEDGMENT:
        answer = text(0).strip()
        return answer or None
    if task.answer_type in (ANSWER_CONNECTION_IDENTITY, ANSWER_POSITION_VALUE, ANSWER_MATERIAL_VALUE):
        answer = text(0).strip()
        if not answer:
            raise ResolutionInputError(f"task {task.task_id}: a value is required.")
        return answer
    if task.answer_type in (ANSWER_MEMBER_SELECTION, ANSWER_CONFIRMED_FIELDS):
        return _comma_list(text(0), task.task_id)
    if task.answer_type == ANSWER_ATTACHMENTS_VALUE:
        return _parse_attachments(text(0), task.task_id)
    if task.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        marks = _comma_list(text(0), task.task_id)
        position = text(1).strip()
        if not position:
            raise ResolutionInputError(f"task {task.task_id}: a position is required.")
        return (marks, position, _parse_attachments(text(2), task.task_id))
    if task.answer_type in (ANSWER_PLATE_VALUE, ANSWER_HOLES_VALUE, ANSWER_LOCATION_VALUE):
        return _parse_mapping(text(0), task.task_id)
    raise ResolutionInputError(
        f"task {task.task_id} has answer type {task.answer_type!r}, which this review UI "
        "slice does not support; the connection was not resolved."
    )


def _resolutions_for(package_id: str, form: dict):
    contract = build_connection_review_contract(current_workflow(), package_id)
    evidence = form.get(EVIDENCE_FIELD, "")
    resolutions = []
    for task in contract.tasks:
        payload = parse_task_answer(task, form)
        resolutions.append(
            HumanResolution(
                task.task_id, task.task_type, task.answer_type, payload, evidence=evidence,
            )
        )
    return resolutions


# --------------------------------------------------------------------------------------
# The two workflow operations the UI may perform.
# --------------------------------------------------------------------------------------
def submit_resolution(package_id: str, form: dict) -> str:
    """Submits one connection's resolution through the existing workflow API.

    On success the bound workflow is REPLACED by the workflow's own next
    state (the state change came from the workflow, never from the UI) and
    the project page is rendered from that new state. On a stale revision
    the workflow's refusal is shown as a stale-state message with a Refresh
    action; the bound state is unchanged.
    """
    if not is_bound():
        return render.render_unbound_page()
    try:
        resolutions = _resolutions_for(package_id, form)
    except ResolutionInputError as error:
        return render.render_message_page("Resolution not submitted", str(error))
    except UnknownProjectPackageError as error:
        return render.render_message_page("Resolution refused", f"{error} Nothing was processed.")
    try:
        expected_revision = int(form.get(REVISION_FIELD, ""))
    except (TypeError, ValueError):
        return render.render_message_page(
            "Resolution not submitted", "the revision field is missing or not a number."
        )
    workflow = current_workflow()
    try:
        next_workflow = resolve_project_connection(
            workflow,
            package_id=package_id,
            resolutions=resolutions,
            output_dir=_output_dir,
            expected_revision=expected_revision,
        )
    except StaleProjectWorkflowError as error:
        return render.render_message_page(
            "This review is out of date",
            f"{error} Another change has updated this project. Nothing was processed — "
            "refresh the review to see the current state.",
            refresh_form=True,
        )
    except (ConnectionAlreadyProcessedError, UnknownProjectPackageError,
            CrossProjectResolutionError) as error:
        return render.render_message_page(
            "Resolution refused", f"{error} Nothing was processed."
        )
    except WorkflowStageFailureError as error:
        return render.render_message_page(
            "Workflow stage failure", f"{error} Nothing was processed."
        )
    except (TypeError, ValueError) as error:
        return render.render_message_page("Resolution refused", f"{error}")
    bind_workflow(next_workflow, _output_dir)
    return render.render_project_page(project_view())


def refresh() -> str:
    """Runs the existing workflow refresh and re-renders from its result."""
    if not is_bound():
        return render.render_unbound_page()
    workflow = current_workflow()
    refreshed = refresh_project_workflow(workflow, output_dir=_output_dir)
    bind_workflow(refreshed, _output_dir)
    return render.render_project_page(project_view())


# --------------------------------------------------------------------------------------
# Page rendering for the web layer.
# --------------------------------------------------------------------------------------
def render_project_page() -> str:
    if not is_bound():
        return render.render_unbound_page()
    return render.render_project_page(project_view())


def render_connection_page(package_id: str) -> str:
    if not is_bound():
        return render.render_unbound_page()
    try:
        contract = build_connection_review_contract(current_workflow(), package_id)
    except UnknownProjectPackageError as error:
        return render.render_message_page("Unknown connection", str(error))
    return render.render_connection_detail_page(
        project_view(), render_connection_view(contract)
    )


# --------------------------------------------------------------------------------------
# J18 — the page-exception surface: the pages of one drawing set whose response
# could not be read, and the ONE action a human may take on each of them.
#
# This binding is separate from the workflow binding, and deliberately so: the
# records it projects are the project row's own two warnings lines, not anything a
# 7AJ workflow holds, so a session can surface a project's unread pages without a
# workflow for it. It holds the project id, the two record halves read back with
# the modules that write them, and the retry entry the CALLER supplies — which is
# how this layer performs a retry without owning one. The entry IS the existing
# J17 contract (the route that wraps it, or `app.pipeline.retry_pdf_page` itself);
# this module can neither import it nor reimplement it, and every refusal it
# reports is the entry's own code and reason, passed through untouched.
#
# Importing the pipeline here is not merely avoided by policy: it would pull the
# database client and the AI client into the UI process, which
# `test_review_ui_imports_without_any_secret_environment` forbids. The retry entry
# is therefore injected, which makes "this surface has no retry algorithm" a
# structural property rather than a promise.
# --------------------------------------------------------------------------------------
# `app/main.py`'s own status for a retry request it accepted — the read has not
# happened yet when a caller sees this, so it is never an outcome.
RETRY_STARTED_STATUS = "retry_started"
# `app/pipeline.py`'s own two outcome literals for a retry that read the page.
RETRY_OUTCOMES = ("PARSED", "PARSE_FAILED")

_page_project_id: str | None = None
_page_contract: PageExceptionContract | None = None
_page_retry_entry = None


def bind_page_exceptions(*, project_id, warnings, project_status=None, retry_entry) -> None:
    """Binds the drawing set whose unread pages this session surfaces, and the
    existing retry contract the human action will call.

    `warnings` are the project's own persisted warnings, read back here through
    the coverage and parse-failure modules' own readers — the caller hands over
    the record, never a chosen half of it. `retry_entry` is called with one page
    number and returns J17's own result; it is the ONLY thing that reads, decides
    or writes anything, and a refusal it raises is reported as what it is.
    """
    global _page_project_id, _page_contract, _page_retry_entry
    if not (project_id is None or isinstance(project_id, str)):
        raise TypeError("project_id must be a str or None.")
    if not callable(retry_entry):
        raise TypeError(
            "retry_entry must be callable: this surface performs no retry of its own, so the "
            "existing J17 retry contract has to be handed in."
        )
    _page_project_id = project_id
    _page_contract = build_page_exception_contract(
        project_id=project_id,
        coverage=coverage_from_warnings(warnings),
        failures=parse_failures_from_warnings(warnings),
        project_status=project_status,
    )
    _page_retry_entry = retry_entry


def clear_page_exceptions() -> None:
    """Unbinds the page-exception surface (used by tests and session teardown)."""
    global _page_project_id, _page_contract, _page_retry_entry
    _page_project_id = None
    _page_contract = None
    _page_retry_entry = None


def is_page_exceptions_bound() -> bool:
    return _page_contract is not None


def _bound_page_contract() -> PageExceptionContract:
    if _page_contract is None:
        raise RuntimeError("no page-exception record is bound to this session.")
    return _page_contract


def page_exception_view(notice=None) -> PageExceptionListView:
    """The bound record as the view model states it, with an optional notice from
    the action just performed. Rendering this reads nothing and changes nothing."""
    return render_page_exception_list_view(_bound_page_contract(), notice)


def render_page_exceptions_page(notice=None) -> str:
    if not is_page_exceptions_bound():
        return render.render_message_page(
            "Page exceptions",
            "No project's page-exception record is bound to this server session. Nothing is "
            "shown: which pages could not be read is read from a project's own coverage and "
            "parse-failure records, and this session has none.",
        )
    return render.render_page_exceptions_page(page_exception_view(notice))


def _unread_pages_message() -> str:
    return render.render_message_page(
        "Page exceptions",
        "No project's page-exception record is bound to this server session, so no page "
        "exception can be addressed and nothing was requested.",
    )


def _adopt_contract(contract: PageExceptionContract) -> None:
    """Replaces the bound record with J17's own restatement of it."""
    global _page_contract
    _page_contract = contract


def _rebound_contract(result, project_id):
    """The contract restated from J17's OWN returned record, or None.

    A retry's result carries the reconciled coverage counts and the reconciled
    failure list — J17's own statement of what the record says after the attempt.
    Restating the surface from those keys is the most truthful thing available
    here: this layer cannot re-read the project row, and inventing an update from
    anything but J17's own numbers would be exactly the claim it must not make.
    Anything missing or unreadable states nothing rather than a guess.
    """
    try:
        coverage = PageCoverage(
            total_pages=result["total_pages"],
            analysed_pages=result["analysed_pages"],
            parse_failed_pages=result["parse_failed_pages"],
            not_analysed_pages=result["not_analysed_pages"],
        )
        failures = failures_of(result["parse_failure_pages"])
    except (KeyError, TypeError, ValueError):
        return None
    return build_page_exception_contract(
        project_id=project_id,
        coverage=coverage,
        failures=failures,
        project_status=result.get("project_status"),
    )


def render_page_retry_outcome(
    *, project_id, contract, retry_entry, page_number, action_prefix="",
):
    """Calls the existing retry contract for ONE page and reports what it said.

    This is the ONE interpretation of a retry's result, and it lives here rather
    than in either caller so that the internal review UI (7AN/7AO) and the
    authenticated production review surface (J19) cannot drift apart: both call
    this, and neither has an opinion of its own about what a retry meant.

    It returns `(contract_after, html)` — the record as it now stands, and the
    page. `contract_after` is `contract` unchanged for everything except an
    outcome J17 actually reported, which restates it from J17's own returned
    record (`_rebound_contract`); the caller decides where that record now lives.
    The ONLY thing that reads, decides or writes anything is `retry_entry`, which
    is J17's own contract: this function performs no validation, no read and no
    write, and it never translates a refusal — a refusal's own code and reason are
    stated as the notice's state and detail.

    `action_prefix` is the surface the page is rendered from, passed through to
    the renderer: empty for the internal review UI, the production surface's own
    path when the production route is the caller.
    """
    if not isinstance(contract, PageExceptionContract):
        raise TypeError(
            f"contract must be a PageExceptionContract (got {type(contract).__name__})."
        )
    if not callable(retry_entry):
        raise TypeError(
            "retry_entry must be callable: this surface performs no retry of its own, so the "
            "existing J17 retry contract has to be handed in."
        )

    def page(view_contract, notice):
        return render.render_page_exceptions_page(
            render_page_exception_list_view(view_contract, notice),
            action_prefix=action_prefix,
        )

    try:
        result = retry_entry(page_number)
    except Exception as error:
        code = getattr(error, "code", None)
        detail = getattr(error, "detail", None)
        if isinstance(code, str) and isinstance(detail, str):
            # J17's own refusal, read by the public shape it documents. It cannot
            # be imported here (see the section comment) and it is not translated:
            # the code becomes the notice's state and the detail its reason.
            return contract, page(
                contract, retry_notice(page_number=page_number, state=code, detail=detail)
            )
        return contract, page(
            contract,
            retry_notice(
                page_number=page_number,
                state=type(error).__name__,
                detail=(
                    f"the retry of page {page_number} raised {type(error).__name__}: {error}. "
                    "Nothing is claimed about the page — the record below is the one this "
                    "surface already held."
                ),
            ),
        )
    if not isinstance(result, dict):
        return contract, render.render_message_page(
            "Retry result not understood",
            f"The retry of page {page_number} returned {type(result).__name__}, which this "
            "surface cannot read. Nothing is claimed about the page.",
            action_prefix=action_prefix,
        )
    state = result.get("outcome") or result.get("status")
    if state == RETRY_STARTED_STATUS:
        return contract, page(
            contract,
            retry_notice(
                page_number=page_number,
                state=RETRY_STARTED_STATUS,
                detail=(
                    "the retry request was accepted and the read has not reported yet. This "
                    "surface states no outcome until the record does — the record below is "
                    "the one this surface already held."
                ),
            ),
        )
    if state in RETRY_OUTCOMES:
        returned = result.get("resolved")
        resolved = returned if isinstance(returned, bool) else None
        rebound = _rebound_contract(result, project_id)
        detail = (
            "the page was read again and its response parsed as engineering evidence; the record "
            "no longer names it as unread."
            if state == "PARSED" else
            "the page was read again and its response still could not be parsed as engineering "
            "evidence; the record is unchanged and the page is still unread."
        )
        if rebound is None:
            detail += (
                " This surface could not restate the record from the retry's own result, so the "
                "record below is the one it already held."
            )
        contract_after = contract if rebound is None else rebound
        return contract_after, page(
            contract_after,
            retry_notice(page_number=page_number, state=state, detail=detail, resolved=resolved),
        )
    return contract, render.render_message_page(
        "Retry result not understood",
        f"The retry of page {page_number} returned a result this surface cannot read "
        f"(keys: {sorted(result)}). Nothing is claimed about the page.",
        action_prefix=action_prefix,
    )


def submit_page_retry(page_number: int) -> str:
    """Reads ONE page of the bound drawing set again, through the existing J17
    retry contract.

    This is the human action, and it is the ONLY thing on this surface that does
    anything. The project is the bound one and the page is the addressed one, so
    no other page can be requested; the entry performs J17's own validation,
    source check, failure-state check, page bound, evidence-collision check, read,
    reconciliation and failure persistence, and this layer reports what it says:

      * a refusal it raises -> the refusal's own code and reason, with the record
        unchanged (never "retry started", never a success);
      * a request merely accepted -> stated as accepted and not yet read;
      * an outcome -> stated as J17's own outcome, with the surface restated from
        J17's own returned record;
      * anything else -> said to be unreadable, with nothing claimed.

    The interpretation is NOT restated here: it is `render_page_retry_outcome`,
    which the production review surface calls too (J19). This function owns only
    what is specific to a session — that the record it restates is the one bound
    to this process, and that a restated record replaces it.
    """
    if not is_page_exceptions_bound():
        return _unread_pages_message()
    contract_after, html = render_page_retry_outcome(
        project_id=_page_project_id,
        contract=_bound_page_contract(),
        retry_entry=_page_retry_entry,
        page_number=page_number,
    )
    _adopt_contract(contract_after)
    return html
