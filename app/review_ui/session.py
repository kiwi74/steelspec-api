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

__all__ = [
    "ResolutionInputError", "bind_workflow", "clear_workflow", "current_workflow",
    "is_bound", "parse_task_answer", "project_view", "refresh", "render_connection_page",
    "render_project_page", "submit_resolution",
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
