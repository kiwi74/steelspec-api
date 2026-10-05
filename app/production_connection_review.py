"""
Milestone J25 — THE PRODUCTION CONNECTION-REVIEW SURFACE'S COMPOSITION.

WHAT THIS IS
============
The request-time seam behind exactly one route:

    GET /production/review/{project_id}/workflow

It joins five things that already exist and adds no sixth:

    the J24A revision-0 producer      -> the reconstructed 7AJ workflow
    the J22 store's own read          -> the persisted connection-review state
    7AK's two contract builders       -> the same contract for both bands
    7AM's view model                  -> the same view for both bands
    the J80 consumer boundary         -> each persisted task's own recorded address,
                                         read off the task's own item and the drawing's
                                         WHOLE reading history (J23's own read)

    build_workflow_review(project_id)  reads and composes;  pure afterwards
    render_workflow_review(review)     renders the composition; reads nothing

The route body is those two calls, in that order, behind the J19 boundary. Nothing
here is process-global: every value a request needs is built within the request and
returned to it, so two simultaneous requests for two projects cannot see each other.

THE FIFTH JOIN IS A READ, AND IT IS THE ONLY READ THE RECORDED BAND ADDS
========================================================================
J81 made this module the FIRST production caller of the J80 boundary. Everything the
recorded band needs for it is ALREADY IN THE ITEM the store returned: the address is
the persisted item's own `evidence`, the field is the persisted task's own statement,
and the drawing the history is loaded FOR is the drawing the item's own evidence names.
Nothing is reconstructed beside the item, so a superseded attempt cannot be answered
with the current one. The load path is J23's whole-history read, reached through the
`capture_history_for_drawing` seam below, and `authoritative_captures` — the rule that
decides which attempt STANDS for a page — is deliberately NOT the input: it would
replace the attempt the item cited with the attempt the selection kept. An item
recorded before J72 carries no origin keys and is left exactly that way: its candidate
half reads as R3's `RUN_ABSENT`, which is the true answer and not a gap to fill.

THE RECORDED BAND, AND WHY IT IS NOT REVISION ZERO
==================================================
A project's persisted review state is read through J22's own current-state rule
(`read_connection_review_state`: the HIGHEST revision recorded) and rendered through
7AK's own `project_contract_from_snapshot`, so the recorded band carries the
snapshot's OWN revision and status. The RECONSTRUCTION is revision 0 — that is what
J24A produces and what the identity band states. The two are printed separately and
neither is used to explain the other. Reading revision 0 specifically would be this
module inventing a second current-state rule, which J22 already owns.

WHAT THIS MODULE NEVER DOES
===========================
No SQL, no migration, no new table, no write of any kind. It never calls
`record_project_review`, `persist_review_snapshot`, `resolve_project_connection` or
`refresh_project_workflow`: rendering this page changes nothing, and a project with
no persisted review state is reported as `NO_PERSISTED_CONNECTION_REVIEW_STATE`
rather than given one. It adds no route that accepts a human decision, so no action
it lists can be performed by rendering it.

It also invents no identity: member identity comes from the persisted member record
through J24A, member placements are never inferred, and a revision >= 1 is never
resolved here. Where an action would need a member placement, the reason is the
pipeline's own (see `_limitation`), quoted rather than restated.

WHY THE REASONS LIVE HERE AND NOT IN THE RENDERER
=================================================
`app/review_ui/render.py` names no action of its own invention and carries no
engineering vocabulary — it escapes and prints what it is handed. An availability
reason is an engineering statement about the pipeline, so it is composed here and
crosses into the presentation layer as plain data.
"""

from __future__ import annotations

import dataclasses

from app.cad_engine.automation_pipeline import (
    PIPELINE_GAP_ERROR_CODE,
    PIPELINE_STAGE_MEMBER_CONTEXT,
)
from app.cad_engine.connection_review_snapshot import project_contract_from_snapshot
from app.cad_engine.review_contract import build_project_review_contract
from app.cad_engine.review_view_model import (
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    ProjectReviewView,
    render_project_view,
)
from app.engineering_data.connection_review_repository import (
    NO_PERSISTED_CONNECTION_REVIEW_STATE,
    read_connection_review_state,
)
from app.production_recorded_readings import (
    RecordedFieldReading,
    read_recorded_field_readings,
)
from app.production_review.project_workflow_reconstruction import (
    ReconstructionRefused,
    ReconstructedProjectWorkflow,
    reconstruct_project_workflow,
)
from app.review_ui import render

__all__ = [
    "ACTION_AVAILABLE",
    "ACTION_UNAVAILABLE",
    "PROJECT_NOT_FOUND",
    "WorkflowReview",
    "build_workflow_review",
    "capture_history_for_drawing",
    "recorded_review_state",
    "render_workflow_review",
    "review_store_client",
    "section_matcher_for_review",
]

#: An action the view carries that this surface can honour, and one it cannot.
ACTION_AVAILABLE = "AVAILABLE"
ACTION_UNAVAILABLE = "UNAVAILABLE"

#: The project does not exist. This is J24A's own documented meaning for a `None`
#: reconstruction, named here rather than left implicit. The route never reaches it:
#: `_authorized_project` answers 404 for an unknown project before any read.
PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"


@dataclasses.dataclass(frozen=True)
class WorkflowReview:
    """One project's review, as this request composed it — and nothing else.

    `reconstructed` / `view` are the LIVE revision-0 band (J24A -> 7AK -> 7AM).
    `persisted_code` / `persisted_revisions` / `persisted_view` are the RECORDED band
    (J22's store -> 7AK's replay -> 7AM); the recorded band names its own revision.

    `refusal_code` and `refusal_detail` are empty when a reconstruction happened, and
    carry J24A's own code and detail verbatim when it refused. A refusal does not
    suppress the recorded band: what is stored stays readable whatever the
    reconstruction could or could not do.

    `recorded_field_readings` (J81) is the RECORDED band's evidence half: one entry per
    persisted task of every persisted item, each carrying the task's own field statement
    beside the candidate its own item's recorded address names. It is empty exactly when
    no snapshot is recorded, because there is then no persisted item to read an address
    off. It is a READ-ONLY representation added to this composition — no persisted
    contract changes with it, and the readings are computed from the store's own returned
    items rather than from the reconstruction beside them.
    """

    project_id: str
    identity: tuple[tuple[str, str], ...]
    coverage: tuple[tuple[str, str], ...]
    capture_runs: tuple[str, ...]
    reconstructed: ReconstructedProjectWorkflow | None
    view: ProjectReviewView | None
    refusal_code: str
    refusal_detail: str
    persisted_code: str
    persisted_revisions: tuple[int, ...]
    persisted_view: ProjectReviewView | None
    recorded_field_readings: tuple[RecordedFieldReading, ...]
    limitations: tuple[tuple[str, str, str], ...]


# ======================================================================================
# The injectable seams — each reads something this module must not read at import.
# ======================================================================================
def section_matcher_for_review():
    """The section matcher J24A requires its caller to supply.

    J24A constructs none, because the production matcher performs a live catalogue
    select when it is built. It is therefore built per request, and this function is
    the seam a caller (or a test) replaces.
    """
    from app.engineering_data.section_matcher import SectionMatcher

    return SectionMatcher()


def review_store_client():
    """The client the J22 read uses, imported on use.

    Importing it at module import would open a database client behind every import of
    this module — including the route module that imports it.
    """
    from app.supabase_client import supabase

    return supabase


def capture_history_for_drawing(drawing_id: str):
    """One drawing's WHOLE reading history, every attempt kept — imported on use.

    This is J23's own read (`page_extraction_captures_for_drawing`): all windows, all
    retries, an earlier attempt never overwritten by a later one. It decides nothing, which
    is exactly why it is the right input: the rule that decides which attempt STANDS for a
    page is `authoritative_captures`, and handing that SELECTION to the consumer would
    answer with whichever attempt it kept instead of the one the persisted item cited.

    The seam exists for the same reason `review_store_client` does — importing the
    repository at module import would open a database client behind every import of this
    module — and it is declared here rather than buried inside the consumer so that "which
    history is read" is a decision made at the production call site, in the open.
    """
    from app.engineering_data.repository import page_extraction_captures_for_drawing

    return page_extraction_captures_for_drawing(drawing_id)


def recorded_review_state(project_id: str):
    """J22's own current-state read, through this module's client seam.

    Returning J22's own `RecordedReviewState` unchanged means the absence code and the
    revision list this surface prints are the store's, not a re-statement of them.
    """
    return read_connection_review_state(project_id, client=review_store_client())


# ======================================================================================
# Composition.
# ======================================================================================
def _page_numbers(pages) -> str:
    """Every page the accumulation named, as numbers, or the explicit absence."""
    listed = ", ".join(str(page) for page in pages)
    return listed or "(none)"


def _identity_rows(reconstructed: ReconstructedProjectWorkflow, revision) -> tuple:
    return (
        ("Project", reconstructed.project_id),
        ("Drawing", reconstructed.drawing_id),
        ("Drawing set", reconstructed.drawing_set_id),
        ("Document pages", str(reconstructed.page_count)),
        ("Reconstruction revision", str(revision)),
    )


def _coverage_rows(reconstructed: ReconstructedProjectWorkflow) -> tuple:
    """The document's pages, accounted for by J24A's own accumulation.

    The page NUMBERS are printed, not only the counts: a count says how many pages are
    unread and only the numbers say which, which is what a reviewer acts on.
    """
    return (
        ("Pages in document", str(reconstructed.page_count)),
        ("Analysed", _page_numbers(reconstructed.analysed_pages)),
        ("Parse failed", _page_numbers(reconstructed.parse_failed_pages)),
        ("Not analysed", _page_numbers(reconstructed.not_analysed_pages)),
        ("Readings read", str(reconstructed.captures_read)),
    )


def _limitation(action) -> tuple[str, str, str]:
    """One of the view's own actions, with what this surface can truthfully do with it.

    The action set is the views' own — this function never adds an action a view did
    not carry, and never drops one it did. The labels are the view's labels.
    """
    if action.action == ACTION_REVIEW:
        return (
            action.label,
            ACTION_AVAILABLE,
            "Reviewing is reading, and this surface is where it happens: the connection's "
            "decision, blockers, warnings, drawing evidence, extracted values, tasks and "
            "provenance are printed in full below. This surface performs no decision.",
        )
    if action.action == ACTION_RESOLVE:
        # The pipeline's own code, stage and ruling sentence — imported, not retyped.
        # The gap fires per mark and only where a mark actually needs placing, so the
        # reason is conditional: it is never "resolution always fails".
        return (
            action.label,
            ACTION_UNAVAILABLE,
            f"{PIPELINE_GAP_ERROR_CODE} at {PIPELINE_STAGE_MEMBER_CONTEXT}: a member "
            "placement is never inferred from a connection or another member. Wherever a "
            "member placement would be required, the persisted record carries none to "
            "resolve with — and this surface adds no route that applies a resolution to "
            "any revision, so a resolution cannot be performed here at all.",
        )
    if action.action == ACTION_REFRESH:
        return (
            action.label,
            ACTION_UNAVAILABLE,
            "Refreshing re-projects the stored pipelines against the artifact directory on "
            "disk and reports which artifacts are present. This surface reads no files and "
            "adds no route that re-projects.",
        )
    return (
        action.label,
        ACTION_UNAVAILABLE,
        "This surface adds no route that performs this action.",
    )


def _action_views(view: ProjectReviewView | None) -> tuple:
    """Every action the views carry, project-level first, each one once."""
    if view is None:
        return ()
    seen: dict = {}
    for action in tuple(view.actions) + tuple(
        action for item in view.review_items for action in item.actions
    ) + tuple(action for item in view.completed_items for action in item.actions):
        seen.setdefault(action.action, action)
    return tuple(seen.values())


def _limitations(*views: ProjectReviewView | None) -> tuple:
    seen: dict = {}
    for view in views:
        for action in _action_views(view):
            seen.setdefault(action.action, action)
    return tuple(_limitation(action) for action in seen.values())


# ======================================================================================
# J81 — the recorded readings, as plain data for the presentation layer.
# ======================================================================================
def _field_statement(reading: RecordedFieldReading) -> str:
    """The task's own field statement, printed as it was stated and never re-derived.

    The value is the reading's, verbatim: a task that names one engineering field prints
    `FIELD_BOUND(name)`, and a task that names none prints `FIELD_NOT_SINGLE`. No third
    wording is invented for either, and neither is described in the other's terms.
    """
    if reading.field_name is None:
        return reading.field_state
    return f"{reading.field_state}({reading.field_name})"


def _evidence_statement(reading: RecordedFieldReading) -> str:
    """What the item's OWN recorded address established, in the resolver's own words.

    A resolution prints the rule that established it and the address terms the reading
    carries; a refusal prints R3's own code and R3's own detail sentence. Nothing is
    summarised, no candidate is quoted, and a refusal is never printed as an empty result:
    the reason is the whole answer, because "the evidence establishes no candidate" is a
    fact about the evidence rather than a failure of this surface.
    """
    outcome = reading.outcome
    reason_code = getattr(outcome, "reason_code", None)
    if reason_code is not None:
        return f"{reason_code}: {outcome.detail}"
    return (
        f"{outcome.matched_by} · run {outcome.analysis_run_id} · page "
        f"{outcome.page_number} · position {outcome.candidate_position}"
    )


def _recorded_reading_rows(readings: tuple[RecordedFieldReading, ...]) -> tuple:
    """Each reading as (task, field statement, evidence statement) — plain strings.

    Composed here rather than in the renderer, for the same reason the limitations are:
    what a field state or an R3 refusal MEANS is an engineering statement, and
    `app/review_ui/render.py` escapes and prints what it is handed while deciding nothing.
    """
    return tuple(
        (
            f"{reading.review_package_id} · {reading.task_id}",
            _field_statement(reading),
            _evidence_statement(reading),
        )
        for reading in readings
    )


def review_documents(project_id: str, *, repository=None) -> list[dict]:
    """The project's source documents, each with whether it carries persisted readings.

    E2E-002G. This exists so a caller can be TOLD which document a review is about instead
    of the reconstruction having to guess between them — and so the route can refuse a
    document that is not this project's before anything is reconstructed.

    `has_readings` is not a heuristic. It is the same criterion the reconstruction itself
    applies when it builds its candidate list: a document's drawing has at least one
    `page_extraction_captures` row. Nothing here infers eligibility from a filename, a page
    count, a role, a recency or how many evidence rows a document produced — a document with
    no captures is reported as having none, and never as eligible.

    A document with no drawing at all — nothing has read it yet — is returned with
    `has_readings` false rather than omitted, so a caller can tell "this project has two
    documents" from "this project has two documents and one of them has been read".

    Read-only, and scoped to the project it is given: every read below is filtered by
    `project_id`, so no identifier a caller states could reach another project's documents.
    """
    store = repository
    if store is None:
        from app.engineering_data import repository as project_store

        store = project_store

    readings_by_document: dict[str, bool] = {}
    for drawing_set in store.drawing_sets_for_project(project_id):
        for drawing in store.drawings_for_drawing_set(drawing_set.get("id")):
            document_id = drawing.get("document_id")
            if not document_id:
                continue
            rows = store.page_extraction_captures_for_drawing(drawing.get("id")) or []
            readings_by_document[document_id] = (
                readings_by_document.get(document_id, False) or bool(rows)
            )

    # No `role`. A document's role is a human ASSERTION with a deliberately narrow read
    # model — J64's guard pins how many modules may read one — and nothing here needs it:
    # a document is selected by its id and judged eligible by its captures. Widening a role
    # reader to label a button would be a poor trade for the invariant it costs.
    return [
        {
            "document_id": document.get("id"),
            "file_name": document.get("file_name"),
            "has_readings": readings_by_document.get(document.get("id"), False),
        }
        for document in store.project_documents_for_project(project_id)
    ]


def build_workflow_review(
    project_id: str,
    *,
    document_id: str | None = None,
    section_matcher=None,
    repository=None,
    recorded_state=None,
) -> WorkflowReview:
    """One project's review: the recorded band, the reconstructed band, and the limits.

    The persisted state is read FIRST, so a project whose reading cannot be
    reconstructed still renders the review state that IS recorded against it. A
    refusal is a statement about the reconstruction, never a reason to hide the store.

    J23's `CaptureRefused` and 7AZ's `ValueError` are not caught here: J24A
    deliberately does not re-wrap another authority's refusal, and neither does this.
    The recorded readings (J81) are read under the same standing: they go through J80's
    boundary, which re-states none of R3's refusals and swallows none of them either, so
    a refusal arrives as R3's own outcome rather than as an absence.

    `repository` is J24A's own seam (the project store, or a double for it);
    `section_matcher`, `recorded_state` and `capture_history_for_drawing` are this
    module's (see the seams above). The capture seam is a module-level function rather
    than a parameter so that a test replaces the SAME seam a request uses, instead of a
    different route through the same read.
    """
    state = recorded_review_state(project_id) if recorded_state is None else recorded_state

    reconstructed: ReconstructedProjectWorkflow | None = None
    refusal_code = ""
    refusal_detail = ""
    try:
        reconstructed = reconstruct_project_workflow(
            project_id,
            section_matcher=(
                section_matcher if section_matcher is not None else section_matcher_for_review()
            ),
            repository=repository,
            # E2E-002G — the document this review is ABOUT, when the caller named one.
            # `None` is the pre-existing request and behaves exactly as it always has: a
            # project whose readings belong to one document is reconstructed, and a project
            # with several refuses rather than choosing. Nothing here infers a document.
            document_id=document_id,
        )
    except ReconstructionRefused as refused:
        refusal_code = refused.code
        refusal_detail = refused.detail

    if reconstructed is None:
        # J24A's documented meaning for None: the PROJECT does not exist.
        refusal_code = refusal_code or PROJECT_NOT_FOUND
        refusal_detail = refusal_detail or (
            f"no project exists with id {project_id!r}; there is no persisted reading to "
            "reconstruct a review from and none is invented"
        )
        view = None
        identity: tuple = (("Project", project_id), ("Reconstruction revision", "—"))
        coverage: tuple = ()
        capture_runs: tuple = ()
    else:
        view = render_project_view(build_project_review_contract(reconstructed.workflow))
        identity = _identity_rows(reconstructed, view.revision)
        coverage = _coverage_rows(reconstructed)
        capture_runs = tuple(reconstructed.capture_run_ids)

    persisted_view = (
        render_project_view(project_contract_from_snapshot(state.snapshot))
        if state.snapshot is not None else None
    )

    # J81: the recorded band's evidence half, read off the PERSISTED items the store returned
    # and off nothing else. An item recorded before J72 recorded an origin carries none, and
    # its candidate half reads as R3's `RUN_ABSENT` — the true answer, left as it is.
    recorded_field_readings = (
        read_recorded_field_readings(
            state.snapshot, captures_for_drawing=capture_history_for_drawing,
        )
        if state.snapshot is not None else ()
    )

    return WorkflowReview(
        project_id=project_id,
        identity=identity,
        coverage=coverage,
        capture_runs=capture_runs,
        reconstructed=reconstructed,
        view=view,
        refusal_code=refusal_code,
        refusal_detail=refusal_detail,
        persisted_code=state.code,
        persisted_revisions=tuple(state.revisions),
        persisted_view=persisted_view,
        recorded_field_readings=recorded_field_readings,
        limitations=_limitations(view, persisted_view),
    )


def render_workflow_review(review: WorkflowReview) -> str:
    """The composed review as a page. Pure: it reads nothing and writes nothing.

    Kept separate from `build_workflow_review` so that "rendering changes no state" is
    a property of a function that has no way to change any, and so the same
    composition can be rendered twice and compared.
    """
    return render.render_workflow_review_page(
        review.view,
        identity=review.identity,
        coverage=review.coverage,
        capture_runs=review.capture_runs,
        persisted_code=review.persisted_code,
        persisted_revisions=review.persisted_revisions,
        persisted_view=review.persisted_view,
        recorded_readings=_recorded_reading_rows(review.recorded_field_readings),
        limitations=review.limitations,
        refusal_code=review.refusal_code,
        refusal_detail=review.refusal_detail,
        action_prefix=f"/production/review/{review.project_id}",
    )
