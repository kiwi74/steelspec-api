"""
Milestone J24A — THE PRODUCTION REVISION-0 7AJ PRODUCER.

WHAT THIS IS
============
The smallest production read seam that reconstructs the initial 7AJ workflow
(revision 0) from the raw AI capture J23 persists, plus the project, drawing-set,
drawing and page identities that were already authoritative:

    page_extraction_captures              the AI readings           (J23)
      -> begin_incremental_analysis       7AZ's own accumulation    (unchanged)
      -> intake_for_accumulated           7AZ's own intake seam     (unchanged)
      -> start_project_workflow           7AJ revision 0            (unchanged)
      -> build_review_snapshot            J22's own writer          (unchanged)

Nothing between those steps is re-implemented here. This module chooses which
persisted reading to use, refuses when the persisted record cannot support the
choice, and hands the result to the existing builders. It writes nothing, calls
no AI, resolves nothing, dispatches nothing and generates nothing.

WHERE THE AI READINGS COME FROM — THE ONE RULE
==============================================
Every AI reading in the reconstructed workflow comes from a
`page_extraction_captures.payload`. There is no second path:

  * `connections`, `steel_members` and `review_items` are NOT read to recreate a
    reading. They are downstream projections of a reading and have already lost
    what the capture holds.
  * The readings are selected by J23's own `authoritative_captures` — the latest
    attempt that parsed, or the latest attempt when none ever parsed — so a page
    read twice contributes exactly one reading and a permanently unparseable page
    stays represented as unparseable.
  * The payloads are handed to 7AZ and 7Y unmodified. They are never normalised,
    merged, deduplicated, reinterpreted or repaired on the way in.

THE ONE THING THAT IS NOT A READING, AND WHY IT IS READ
======================================================
`steel_members` IS read — for the member CONTEXT 7AC requires, never for a
reading. `build_exception_resolution_package` takes the project's validated
members and uses their marks as the authoritative member choices
(`exception_resolution._member_choices` returns `tuple(member_rows)`, the keys).
That set is the validated, persisted member record; deriving it from the capture's
`raw_members` instead would substitute unvalidated AI output for the validated
record 7AC is designed to consume. The distinction is the whole point: this module
reads persisted member IDENTITY, and never persisted ENGINEERING INTERPRETATION.

`member_placements` is deliberately EMPTY. It has no persisted source anywhere
(recorded in the J23 report), and an empty placement mapping is the truth about
what is known — not a default standing in for a value. Its consequence at
revision 0 is nil and its consequence later is a refusal: `start_project_workflow`
evaluates every connection WITHOUT member context, so no raw candidate can become
AUTO, and a later `resolve_project_connection` fails closed with the pipeline's
own "No member placement was supplied for mark …" rather than inventing geometry.

WHY THE DOCUMENT IS RESOLVED HERE, AND WHY IT CAN REFUSE
=======================================================
A `page_extraction_captures` row names its drawing, and a workflow is per project.
So the producer resolves the project's SOURCE DOCUMENT, and it refuses rather than
chooses when the persisted record does not identify exactly one:

  * no document of the project carries a reading  -> RECONSTRUCTION_NO_CAPTURE
  * more than one document carries readings       -> RECONSTRUCTION_AMBIGUOUS_DOCUMENT

Picking one of several would be deciding WHICH document the project's reading is,
which is a lifecycle decision this layer is not entitled to make. A project with
no persisted reading is NOT reconstructed and NOT backfilled: it refuses, and the
refusal is the honest statement of the gap.

ABSENCE, AND THE ONE REFUSAL THIS MODULE DOES NOT INVENT
=======================================================
`None` means the PROJECT does not exist — the absence of a project, never a blank
one, the same convention `repository.get_project` and `project_read` document.
Every other unmet precondition is a refusal, because the project exists and its
record is insufficient.

Two failures are raised by the authorities that own them and are NOT re-wrapped:
J23's `CaptureRefused` (a recorded reading is missing a field it must be selected
on) and 7AZ's `ValueError` (a page outside the document, a page recorded twice, or
a parse failure carrying content). Neither is mapped to a code of this module's
invention: inventing a code for someone else's refusal would be inventing an
explanation for it.

WHAT THIS MODULE NEVER DOES
===========================
No SQL, no new repository read, no new table, no migration. It reads three
existing repository functions plus J23's own capture reader, and it constructs no
`SectionMatcher`: the matcher is the caller's, because constructing the production
matcher performs a live catalogue read (`section_matcher.SectionMatcher.__init__`
executes a table select) and a read seam must not perform I/O the caller did not
ask for.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from app.cad_engine.incremental_continuation import (
    AccumulatedAnalysis,
    begin_incremental_analysis,
    intake_for_accumulated,
)
from app.cad_engine.project_workflow import ProjectWorkflowState, start_project_workflow
from app.engineering_data.page_extraction_capture import (
    accumulated_page_mappings,
    authoritative_captures,
)

__all__ = [
    "RECONSTRUCTION_REFUSALS",
    "RECONSTRUCTION_AMBIGUOUS_DOCUMENT",
    "RECONSTRUCTION_MIXED_IDENTITY",
    "RECONSTRUCTION_NO_CAPTURE",
    "RECONSTRUCTION_PAGE_COUNT_UNKNOWN",
    "RECONSTRUCTION_PROJECT_UNKNOWN",
    "ReconstructionRefused",
    "ReconstructedProjectWorkflow",
    "reconstruct_project_workflow",
]

RECONSTRUCTION_PROJECT_UNKNOWN = "RECONSTRUCTION_PROJECT_UNKNOWN"
RECONSTRUCTION_NO_CAPTURE = "RECONSTRUCTION_NO_CAPTURE"
RECONSTRUCTION_AMBIGUOUS_DOCUMENT = "RECONSTRUCTION_AMBIGUOUS_DOCUMENT"
RECONSTRUCTION_MIXED_IDENTITY = "RECONSTRUCTION_MIXED_IDENTITY"
RECONSTRUCTION_PAGE_COUNT_UNKNOWN = "RECONSTRUCTION_PAGE_COUNT_UNKNOWN"

RECONSTRUCTION_REFUSALS = (
    RECONSTRUCTION_PROJECT_UNKNOWN,
    RECONSTRUCTION_NO_CAPTURE,
    RECONSTRUCTION_AMBIGUOUS_DOCUMENT,
    RECONSTRUCTION_MIXED_IDENTITY,
    RECONSTRUCTION_PAGE_COUNT_UNKNOWN,
)


class ReconstructionRefused(ValueError):
    """No workflow was reconstructed, for the stated reason.

    A refusal is fail-closed: a caller that receives one has no workflow, no
    partial one, and nothing to review.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class ReconstructedProjectWorkflow:
    """One project's revision-0 workflow, and the record it was built from.

    `workflow` is a genuine 7AJ `ProjectWorkflowState` — the same object the
    in-process chain produces and the only thing J22's `build_review_snapshot`
    accepts. The remaining fields are the provenance a caller needs to state what
    the reconstruction read, without re-reading anything:

      - `drawing_id` / `drawing_set_id` the document the readings belong to
      - `page_count`           the document's own page count (never guessed)
      - `captures_read`        how many readings stood for a page
      - `capture_run_ids`      the extraction runs those readings came from, in
                               page order — the provenance J22 records verbatim
                               as `evidence_run_ids`
      - `accumulated`          7AZ's own accumulated state, which names every
                               page the document has and did not have read

    `parse_failed_pages` and `not_analysed_pages` are read off that accumulated
    state rather than recomputed here: one accounting, stated once.
    """

    project_id: str
    drawing_id: str
    drawing_set_id: str
    page_count: int
    workflow: ProjectWorkflowState
    accumulated: AccumulatedAnalysis
    captures_read: int
    capture_run_ids: tuple[str, ...]
    #: The durable document this lineage read (Milestone J61), or None when the drawing
    #: names none — a lineage created before J61, or one whose source could not be
    #: identified. It is stated rather than derived a second time, so a caller that
    #: EXPLICITLY selected a document can see which one the reconstruction used.
    document_id: str | None = None

    @property
    def parse_failed_pages(self) -> tuple[Any, ...]:
        return tuple(self.accumulated.parse_failed_page_numbers)

    @property
    def not_analysed_pages(self) -> tuple[Any, ...]:
        return tuple(self.accumulated.not_analysed_page_numbers)

    @property
    def analysed_pages(self) -> tuple[Any, ...]:
        return tuple(self.accumulated.analysed_page_numbers)


def _text(value) -> str | None:
    """The value when it is a non-empty string, else None. Never coerced."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _rows(value) -> tuple[Mapping, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(row for row in value if isinstance(row, Mapping))
    return ()


def _positive_int(value) -> int | None:
    """The value when it is a positive whole number, else None. A bool is not one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _drawings_of_project(store, project_id: str) -> tuple[tuple[str, Mapping], ...]:
    """Every (drawing_set_id, drawing_row) the project has, in read order.

    The two reads are the existing ones and are unchanged: the sets the project
    has, then the drawings of each. No new query, no join, no count.
    """
    pairs: list[tuple[str, Mapping]] = []
    for drawing_set in _rows(store.drawing_sets_for_project(project_id)):
        set_id = _text(drawing_set.get("id"))
        if set_id is None:
            continue
        for drawing in _rows(store.drawings_for_drawing_set(set_id)):
            pairs.append((set_id, drawing))
    return tuple(pairs)


def _verified_rows(
    rows, *, project_id: str, drawing_id: str, drawing_set_id: str,
) -> tuple[Mapping, ...]:
    """The drawing's capture rows, refused unless every row agrees about where it belongs.

    The reader already filters by `drawing_id`, so this is a check on the record
    rather than on the query: a row that names another project or another drawing
    set is a reading filed under two identities at once, and a reconstruction that
    accepted it would mix documents. It is refused, not repaired.
    """
    verified = _rows(rows)
    for row in verified:
        if (row.get("drawing_id") != drawing_id
                or row.get("drawing_set_id") != drawing_set_id
                or row.get("project_id") != project_id):
            raise ReconstructionRefused(
                RECONSTRUCTION_MIXED_IDENTITY,
                f"a recorded reading for drawing {drawing_id} names project "
                f"{row.get('project_id')!r} and drawing set {row.get('drawing_set_id')!r}; "
                "a reading filed under another identity is never combined with this document's",
            )
    return verified


def _member_rows(store, project_id: str) -> dict[str, Mapping]:
    """The project's validated members, keyed by mark — 7AC's member choices.

    The marks and their first-seen order come from the persisted member rows,
    exactly as `pipeline.py` consolidates them within a run (`{row["mark"]: row}`
    over the inserted members). A member without a mark carries no identity and is
    skipped rather than given one.
    """
    keyed: dict[str, Mapping] = {}
    for row in _rows(store.member_rows_for_project(project_id)):
        mark = row.get("mark")
        if mark and mark not in keyed:
            keyed[mark] = row
    return keyed


def reconstruct_project_workflow(
    project_id: str,
    *,
    section_matcher,
    repository=None,
    request_material_specification: bool = False,
    document_id: str | None = None,
) -> ReconstructedProjectWorkflow | None:
    """Reconstructs one project's revision-0 workflow from its persisted reading.

    Returns the reconstruction, or `None` when the PROJECT does not exist. Refuses
    (`ReconstructionRefused`, see `RECONSTRUCTION_REFUSALS`) when the project exists
    but its persisted reading cannot support a reconstruction, and lets J23's
    `CaptureRefused` and 7AZ's `ValueError` propagate unchanged for the reasons the
    module docstring gives.

    `section_matcher` is required: the caller supplies the same matcher the
    extraction used, and this module constructs none (see the module docstring).

    `repository` is the existing repository module, or a double standing in for it;
    it defaults to the production repository, imported inside this function so that
    importing this module does not pull the database client into a process that
    never reads one.

    `document_id` (Milestone J61) names WHICH source document of the project this
    workflow is about, and it is `None` by default so that a caller which has never
    heard of it gets exactly the behaviour it got before:

      - `None`, one document with readings        -> that document, as before
      - `None`, no document with readings         -> RECONSTRUCTION_NO_CAPTURE
      - `None`, more than one with readings       -> RECONSTRUCTION_AMBIGUOUS_DOCUMENT
      - a document id, that document has readings -> THAT document, however many others
                                                     the project has

    A document is NEVER chosen here. Not by page count, not by how many readings a
    document has, not by recency, not by role, not by filename, and not by how many
    evidence rows it produced: a guess between two documents would record a review of
    one while the other's evidence sat beside it, indistinguishable afterwards. So
    when the caller names none and more than one is readable, the refusal stands.

    A NAMED document that has no reading of this project's is refused as
    `RECONSTRUCTION_NO_CAPTURE` — there is nothing to reconstruct — and the detail
    states the document, so the two no-reading cases are told apart by the statement
    rather than by a second refusal code. A named document with more than one reading
    lineage is still `RECONSTRUCTION_AMBIGUOUS_DOCUMENT`: which lineage's readings
    those are is exactly as unstated as it was before.
    """
    if not (isinstance(project_id, str) and project_id.strip()):
        raise ValueError("project_id must be a non-empty str.")
    if document_id is not None and not (isinstance(document_id, str) and document_id.strip()):
        raise ValueError("document_id must be a non-empty str, or None.")

    if repository is None:
        from app.engineering_data import repository as project_store
    else:
        project_store = repository

    project_row = project_store.get_project(project_id)
    if project_row is None:
        return None
    if not isinstance(project_row, Mapping):
        raise ReconstructionRefused(
            RECONSTRUCTION_PROJECT_UNKNOWN,
            f"the project store returned {type(project_row).__name__} for {project_id!r} "
            "rather than a project row.",
        )

    documents: list[tuple[str, Mapping, tuple[Mapping, ...]]] = []
    for drawing_set_id, drawing in _drawings_of_project(project_store, project_id):
        drawing_id = _text(drawing.get("id"))
        if drawing_id is None:
            continue
        rows = _rows(project_store.page_extraction_captures_for_drawing(drawing_id))
        if rows:
            documents.append((drawing_set_id, drawing, rows))

    # Milestone J61 — the caller's own document, when it named one. This is a FILTER over
    # the project's own documents, never a lookup that could reach another project's, and
    # it removes a candidate only by the identity the caller stated.
    if document_id is not None:
        documents = [
            candidate for candidate in documents
            if _text(candidate[1].get("document_id")) == document_id
        ]

    # Milestone L19 — the lineage THIS DOCUMENT has been told to be reviewed from, when it
    # has been told. This is a FILTER on the caller's own statement, exactly as the document
    # filter above is: it removes a candidate only by an identity a human selected.
    #
    # It is not a choice, and it must never become one. Nothing here ranks, dates, counts or
    # scores the candidates; nothing compares two readings' completeness, recency, run
    # status or quality; and a selection that names a drawing this document does not have is
    # REFUSED rather than resolved by falling back to another lineage. The store that
    # supplies the selection is the production store; an injected one that does not carry
    # the read is exercising the behaviour that predates the column, which is why its
    # absence is read as "nothing has been selected" rather than as an error.
    selected_drawing_id = None
    read_selection = getattr(project_store, "selected_drawing_id_for_document", None)
    if callable(read_selection):
        selected_drawing_id = _text(read_selection(document_id))
    if selected_drawing_id is not None:
        selected = [
            candidate for candidate in documents
            if _text(candidate[1].get("id")) == selected_drawing_id
        ]
        if not selected:
            raise ReconstructionRefused(
                RECONSTRUCTION_NO_CAPTURE,
                f"document {document_id!r} of project {project_id!r} names drawing "
                f"{selected_drawing_id!r} as the lineage it is reviewed from, and no drawing "
                "of that document with a persisted AI reading carries that id. The "
                "selection is authoritative and another lineage is not substituted for it",
            )
        documents = selected

    if not documents:
        if document_id is not None:
            raise ReconstructionRefused(
                RECONSTRUCTION_NO_CAPTURE,
                f"document {document_id!r} of project {project_id!r} has no persisted AI "
                "reading; a workflow is reconstructed from a reading and from nothing else",
            )
        raise ReconstructionRefused(
            RECONSTRUCTION_NO_CAPTURE,
            f"no document of project {project_id!r} has a persisted AI reading; the "
            "review workflow is not reconstructed from anything else and the project "
            "is not backfilled",
        )
    if len(documents) > 1:
        if document_id is not None:
            raise ReconstructionRefused(
                RECONSTRUCTION_AMBIGUOUS_DOCUMENT,
                f"document {document_id!r} of project {project_id!r} has "
                f"{len(documents)} drawings carrying persisted AI readings; which of them "
                "these pages belong to is exactly as unstated as it was before, and a "
                "reading lineage is not chosen here",
            )
        raise ReconstructionRefused(
            RECONSTRUCTION_AMBIGUOUS_DOCUMENT,
            f"{len(documents)} documents of project {project_id!r} carry persisted AI "
            "readings; a workflow is per project and the source document is not chosen "
            "here",
        )

    drawing_set_id, drawing, rows = documents[0]
    drawing_id = _text(drawing.get("id"))
    rows = _verified_rows(
        rows, project_id=project_id, drawing_id=drawing_id, drawing_set_id=drawing_set_id,
    )

    page_count = _positive_int(drawing.get("page_count"))
    if page_count is None:
        raise ReconstructionRefused(
            RECONSTRUCTION_PAGE_COUNT_UNKNOWN,
            f"the drawing {drawing_id} of project {project_id!r} does not state its page "
            "count; the pages beyond the reading are unknown rather than absent, and a "
            "page count is never guessed",
        )

    # One reading per page, chosen by J23's own rule. A page read more than once
    # contributes exactly one reading; every attempt stays recorded where it was.
    #
    # Both calls are J23's, and both are pure and deterministic over the same rows, so
    # they agree by construction: `authoritative_captures` yields the ROWS (this module
    # needs each one's run id, and validates the column it selects on), and
    # `accumulated_page_mappings` yields the PAYLOADS through J23's own `_required`, so a
    # reading that came back without its payload is refused by the layer that owns the
    # rule rather than by a bare lookup here.
    chosen = authoritative_captures(rows)
    payloads = accumulated_page_mappings(rows)
    run_ids = tuple(dict.fromkeys(str(row["analysis_run_id"]) for row in chosen))
    # J72 — WHICH run read each page. `chosen` is the reading that stands for each page
    # (J23's own rule), so its `analysis_run_id` is the attempt that produced the
    # candidates this page contributes — the run a candidate's origin names. It is taken
    # from the row that was actually selected, never from the run that merely stands for
    # the document today.
    page_run_ids = {row["page_number"]: str(row["analysis_run_id"]) for row in chosen}

    # 7AZ's own accumulation, from the document's own page count. It validates the
    # reading (no page outside the document, no page twice, no parse failure
    # carrying content) and accounts for every page of the document — so a page
    # nobody read is stated as not analysed rather than silently absent.
    accumulated = begin_incremental_analysis(
        payloads,
        drawing_set_page_count=page_count,
        drawing_set_first_page=1,
    )

    member_rows = _member_rows(project_store, project_id)
    intake = intake_for_accumulated(
        accumulated,
        project_id=project_id,
        source_drawing_id=drawing_id,
        known_member_marks=tuple(member_rows),
        page_analysis_run_ids=page_run_ids,
    )

    # The collection is the intake's OWN collection object, not an equal copy:
    # 7AB refuses an intake whose collection is not the object being evaluated.
    workflow = start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=member_rows,
        member_placements={},
        section_matcher=section_matcher,
        request_material_specification=request_material_specification,
    )

    return ReconstructedProjectWorkflow(
        project_id=project_id,
        drawing_id=drawing_id,
        drawing_set_id=drawing_set_id,
        page_count=page_count,
        workflow=workflow,
        accumulated=accumulated,
        captures_read=len(chosen),
        capture_run_ids=run_ids,
        document_id=_text(drawing.get("document_id")),
    )
