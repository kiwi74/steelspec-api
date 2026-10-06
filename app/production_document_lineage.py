"""
Milestone L19 — THE EXTRACTION LINEAGE A DOCUMENT IS REVIEWED FROM.

WHAT THIS MODULE IS

One document may carry several extraction lineages, because every reading of a document
writes a new drawing, a new drawing set and a new analysis run beside the ones already
there. The reconstruction deliberately refuses to choose between them — it counts the
readings and raises its ambiguity refusal when there is more than one — and that refusal is
correct. What was missing was a way for a human to SAY which lineage is the one to review.

This module states that choice and reads it back. It is the whole of the mechanism: a
document names ONE drawing, or it names none.

WHAT IT DELIBERATELY DOES NOT DO

It does not choose. There is no "latest wins", no "newest completed", no "best quality", no
"the one with the most members" and no fallback of any kind. A later reading never becomes
the selection by existing, and a document with several readings and no selection stays
exactly as ambiguous as it is today — this module cannot make such a document reviewable,
and must never be read as having tried to.

It does not create history. There is no chain of supersession between lineages, no graph, no
ancestry and no head pointer. A selection is a statement about the present: WHICH lineage is
the document's reading. How a document came to have several is settled by the rows
themselves and is not this module's business.

It writes no review state. Selecting a lineage records no revision, opens nothing, resolves
nothing, creates no claim and produces no artifact. It makes a reconstruction possible; it
performs none.

WHERE THE INVARIANT LIVES

The database enforces that a document may only name a drawing OF THAT DOCUMENT, in that
document's own project, through the guard added by this milestone's migration. This module
restates that rule before writing so a caller is told which rule it broke, and the database
refuses it anyway if this module were ever bypassed — the refusal a database can make is the
one that cannot be bypassed.
"""
from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "DOCUMENT_LINEAGE_INPUT_REFUSALS",
    "DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID",
    "DOCUMENT_LINEAGE_INPUT_REFUSED_NOT_A_MAPPING",
    "DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD",
    "DOCUMENT_LINEAGE_INPUT_REFUSED_UNKNOWN_FIELD",
    "DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN",
    "DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN",
    "DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT",
    "DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT",
    "DOCUMENT_LINEAGE_REFUSED_NO_READING",
    "LINEAGE_SELECTION_ROUTE_PATH",
    "REQUEST_FIELDS",
    "DocumentLineageInputRefused",
    "DocumentLineageRefused",
    "parse_lineage_selection_request",
    "select_document_lineage",
    "selected_drawing_id_for_document",
]

#: The one field a selection request may carry. Everything else about the operation — the
#: project, the document, the reviewer — is the caller's own address or their credential,
#: and a body naming any of those is refused rather than interpreted.
REQUEST_FIELDS: tuple[str, ...] = ("drawing_id",)

SERVER_OWNED_FIELDS = (
    "project_id", "document_id", "selected_drawing_id", "drawing_set_id",
    "analysis_run_id", "reviewer", "review_revision", "status",
)

LINEAGE_SELECTION_ROUTE_PATH = (
    "/production/review/{project_id}/documents/{document_id}/selected-drawing"
)

DOCUMENT_LINEAGE_INPUT_REFUSED_NOT_A_MAPPING = "DOCUMENT_LINEAGE_INPUT_REFUSED_NOT_A_MAPPING"
DOCUMENT_LINEAGE_INPUT_REFUSED_UNKNOWN_FIELD = "DOCUMENT_LINEAGE_INPUT_REFUSED_UNKNOWN_FIELD"
DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD = (
    "DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD"
)
DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID = (
    "DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID"
)

DOCUMENT_LINEAGE_INPUT_REFUSALS = (
    DOCUMENT_LINEAGE_INPUT_REFUSED_NOT_A_MAPPING,
    DOCUMENT_LINEAGE_INPUT_REFUSED_UNKNOWN_FIELD,
    DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD,
    DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID,
)

DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN = "DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN"
DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN = "DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN"
DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT = "DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT"
DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT = "DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT"
DOCUMENT_LINEAGE_REFUSED_NO_READING = "DOCUMENT_LINEAGE_REFUSED_NO_READING"

DOCUMENT_LINEAGE_REFUSALS = (
    DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN,
    DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN,
    DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT,
    DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT,
    DOCUMENT_LINEAGE_REFUSED_NO_READING,
)


class DocumentLineageInputRefused(ValueError):
    """The request itself was not a selection this operation can read."""

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


class DocumentLineageRefused(Exception):
    """The request was readable but the project's own rows refused it."""

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


def parse_lineage_selection_request(body: object) -> str:
    """The drawing the caller named, or a refusal saying what was wrong with the request.

    The ONLY field a selection may carry is `drawing_id`, and it is REQUIRED: this operation
    exists to state a choice, so a request that states none is not a selection and is
    refused rather than treated as "no change" — clearing a selection is a different act
    and is not this request.
    """
    if not isinstance(body, Mapping):
        raise DocumentLineageInputRefused(
            DOCUMENT_LINEAGE_INPUT_REFUSED_NOT_A_MAPPING,
            f"the request body is {type(body).__name__} rather than an object; selecting a "
            "lineage names the drawing it selects and nothing else",
        )
    owned = sorted(name for name in SERVER_OWNED_FIELDS if name in body)
    if owned:
        raise DocumentLineageInputRefused(
            DOCUMENT_LINEAGE_INPUT_REFUSED_SERVER_OWNED_FIELD,
            f"the request body names {owned}. The project and document come from the URL and "
            "the reviewer from the access token — none of them is ever taken from a caller",
        )
    unknown = sorted(name for name in body if name not in REQUEST_FIELDS)
    if unknown:
        raise DocumentLineageInputRefused(
            DOCUMENT_LINEAGE_INPUT_REFUSED_UNKNOWN_FIELD,
            f"the request body carries {unknown}; selecting a lineage names exactly one "
            "field, the drawing it selects",
        )
    if "drawing_id" not in body:
        raise DocumentLineageInputRefused(
            DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID,
            "no drawing_id was stated. This operation SELECTS a lineage, so a request that "
            "names none is not the operation, and one is never chosen on the caller's behalf",
        )
    drawing_id = body["drawing_id"]
    if not (isinstance(drawing_id, str) and drawing_id.strip()):
        raise DocumentLineageInputRefused(
            DOCUMENT_LINEAGE_INPUT_REFUSED_DRAWING_INVALID,
            f"the request names drawing_id={drawing_id!r}; a drawing is named by a non-empty "
            "string, and this operation does not choose one",
        )
    return drawing_id


def _project_store(repository: Any = None) -> Any:
    if repository is not None:
        return repository
    from app.engineering_data import repository as project_store

    return project_store


def _text(value: Any) -> str | None:
    """The value when it is a non-empty string, else None. Never coerced."""
    return value if isinstance(value, str) and value.strip() else None


def selected_drawing_id_for_document(document_id: str, *, repository: Any = None) -> str | None:
    """The drawing this document has been told to be reviewed from, or None.

    Read-only. `None` is the ordinary state and means "nothing has been selected", which is
    exactly how every document behaved before this column existed. It never means "choose
    one" — a reader that treats it as an invitation to infer a lineage has misread it, and
    the reconstruction is the reader that must not.
    """
    return _text(_project_store(repository).selected_drawing_id_for_document(document_id))


def _document_of(store: Any, project_id: str, document_id: str) -> Mapping | None:
    """This project's own document row, or None. Membership is the project's, by its rows."""
    for document in store.project_documents_for_project(project_id):
        if _text(document.get("id")) == document_id:
            return document
    return None


def _drawing_set_of(store: Any, project_id: str, drawing_set_id: str) -> Mapping | None:
    for drawing_set in store.drawing_sets_for_project(project_id):
        if _text(drawing_set.get("id")) == drawing_set_id:
            return drawing_set
    return None


def select_document_lineage(
    *,
    project_id: str,
    document_id: str,
    drawing_id: str,
    repository: Any = None,
) -> dict[str, Any]:
    """Records `drawing_id` as the one lineage `document_id` is reviewed from.

    Six things are established before anything is written, in this order, and the write
    happens only if all six hold:

    1. the document exists, and is this project's;
    2. the drawing exists;
    3. the drawing belongs to this project;
    4. the drawing's own document is THIS document;
    5. the drawing carries a persisted reading — a lineage with nothing read from it is not
       a lineage a review could be reconstructed from, so selecting one would be selecting
       a reconstruction that cannot happen;
    6. the caller stated the selection explicitly, which the request parser has already
       established.

    A drawing this document does not own is REFUSED. There is no fallback, no substitution,
    no nearest match and no choosing another lineage instead — the explicit selection is
    authoritative, and a selection that cannot be honoured is reported rather than adjusted.

    Raises `DocumentLineageRefused` (the project's rows) or `DocumentLineageInputRefused`
    (the request), both carrying the code a caller branches on.
    """
    store = _project_store(repository)

    document = _document_of(store, project_id, document_id)
    if document is None:
        raise DocumentLineageRefused(
            DOCUMENT_LINEAGE_REFUSED_DOCUMENT_UNKNOWN,
            f"no document {document_id!r} exists, so there is nothing for a lineage to be "
            "selected for",
        )
    drawing = store.drawing_by_id(drawing_id)
    if drawing is None:
        raise DocumentLineageRefused(
            DOCUMENT_LINEAGE_REFUSED_DRAWING_UNKNOWN,
            f"no drawing {drawing_id!r} exists, so there is no lineage to select",
        )
    if _text(drawing.get("document_id")) != document_id:
        raise DocumentLineageRefused(
            DOCUMENT_LINEAGE_REFUSED_FOREIGN_DOCUMENT,
            f"drawing {drawing_id!r} belongs to document "
            f"{_text(drawing.get('document_id'))!r}, not to {document_id!r}. A document "
            "selects its own lineage and never another's",
        )

    drawing_set_id = _text(drawing.get("drawing_set_id"))
    drawing_set = _drawing_set_of(store, project_id, drawing_set_id) if drawing_set_id else None
    if drawing_set is None:
        raise DocumentLineageRefused(
            DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT,
            f"drawing {drawing_id!r} belongs to drawing set {drawing_set_id!r}, which is not "
            f"one of project {project_id!r}; a lineage is selected within the project that "
            "owns it",
        )
    if _text(drawing_set.get("project_id")) != project_id:
        raise DocumentLineageRefused(
            DOCUMENT_LINEAGE_REFUSED_FOREIGN_PROJECT,
            f"drawing {drawing_id!r} belongs to project "
            f"{_text(drawing_set.get('project_id'))!r}, not to {project_id!r}",
        )

    captures = store.page_extraction_captures_for_drawing(drawing_id) or []
    if not captures:
        raise DocumentLineageRefused(
            DOCUMENT_LINEAGE_REFUSED_NO_READING,
            f"drawing {drawing_id!r} carries no persisted AI reading, so a review could not "
            "be reconstructed from it; a lineage with nothing read from it is not a lineage "
            "this document may be reviewed from",
        )

    store.set_document_selected_drawing(document_id, drawing_id)

    run_ids: list[str] = []
    for row in captures:
        run_id = _text(row.get("analysis_run_id"))
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)

    return {
        "document_id": document_id,
        "selected_drawing_id": drawing_id,
        "selected_drawing_set_id": drawing_set_id,
        "selected_analysis_run_ids": sorted(run_ids),
    }


