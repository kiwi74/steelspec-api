"""Milestone J29 — THE READ-ONLY PDF ANNOTATION EVIDENCE REVIEW SURFACE.

WHAT THIS IS
============
J28's annotation evidence is persisted in `pdf_annotation_occurrences`, populated by the
production extraction pipeline. J28's own parts: an extractor that reads the text-showing
operators of a page and derives, per drawn mark, a coordinate and the evidence the mark was
read from; a storage layer (`app.engineering_data.pdf_annotation_evidence`) that declares
one row per occurrence; and a transport in `app.engineering_data.repository`.

J29 is the surface, and it is a VIEWER. It lets an authenticated project owner read
the occurrences J28 recorded for their own project:

    GET /production/review/{project_id}/annotations[?page=N]

WHAT AN OCCURRENCE IS, AND IS NOT
=================================
An occurrence is a mark DRAWN on a page: where on the page it was drawn, whether it
could be read as a mark, how many text-showing operators assembled it, whether a tag
box or a leader line touched it, and the raw reading it was derived from.

It is NOT a member. Nothing J28 recorded says which physical member — if any — a mark
refers to, so this surface never makes that claim and has no field in which it could.
A leader endpoint is labelled here as annotation/leader evidence: it is where a leader
line was read to end on the page, and it is never read as where anything is attached.

WHAT THIS SURFACE DOES NOT DO
=============================
  * It writes nothing. The single transport it calls is a `.select()`; it calls no
    writer of any table, and it creates, resolves and advances no review state.
  * It renames nothing. Every field is shown under the name J28 gave it, and a field
    the record does not state is shown as absent rather than filled in.
  * It never decides which reading stands for a page, which columns exist, or how the
    page is drawn. Those belong to `authoritative_occurrences`, to the storage layer's
    own column tuple, and to the one presentation layer. This module composes.

THE STORE'S ABSENCE IS A STATE, NOT A CRASH
===========================================
J28's annotation evidence is persisted in the live `pdf_annotation_occurrences` store,
which the production extraction pipeline populates. This surface is READ-ONLY: it reads
that store and creates nothing. A surface that tried to create the table would be a
writer, and this one is not. The client's refusal for a store that is not there is
therefore rendered as its own named state — and ONLY that refusal: any other failure
propagates, because an unreachable database is not an absent table and must never be
reported as one.
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.engineering_data.pdf_annotation_evidence import authoritative_occurrences
from app.review_ui import render

__all__ = [
    "ANNOTATION_EVIDENCE_NONE",
    "ANNOTATION_EVIDENCE_NONE_FOR_PAGE",
    "ANNOTATION_EVIDENCE_RECORDED",
    "ANNOTATION_STORE_ABSENT",
    "ANNOTATION_SURFACE_STATES",
    "AnnotationEvidenceGroup",
    "AnnotationEvidenceOccurrence",
    "AnnotationEvidenceReview",
    "build_annotation_evidence_review",
    "render_annotation_evidence_review",
]

# The states this surface can be in. Each is a value, never an exception: a project
# with nothing recorded and a store that is not there are different facts, and they
# render differently.
ANNOTATION_STORE_ABSENT = "ANNOTATION_STORE_ABSENT"
ANNOTATION_EVIDENCE_NONE = "ANNOTATION_EVIDENCE_NONE"
ANNOTATION_EVIDENCE_NONE_FOR_PAGE = "ANNOTATION_EVIDENCE_NONE_FOR_PAGE"
ANNOTATION_EVIDENCE_RECORDED = "ANNOTATION_EVIDENCE_RECORDED"

ANNOTATION_SURFACE_STATES = frozenset({
    ANNOTATION_STORE_ABSENT,
    ANNOTATION_EVIDENCE_NONE,
    ANNOTATION_EVIDENCE_NONE_FOR_PAGE,
    ANNOTATION_EVIDENCE_RECORDED,
})

#: PostgREST's own code for "no such table in the schema cache". Read off the error,
#: never assumed: it is the ONLY refusal this surface converts into a state.
_UNKNOWN_TABLE_CODE = "PGRST205"

#: What this page is, stated by the layer that owns engineering statements rather than
#: by the renderer. Each sentence is a fact about J28's own contract.
_BOUNDARY = (
    "This page displays the PDF annotation evidence J28 recorded: marks drawn on a "
    "page, the coordinate each was read at, and the operators and geometry the reading "
    "was derived from.",
    "An occurrence is not a member. Nothing recorded here states which physical member, "
    "if any, a mark refers to, and this page does not decide that — it has no field in "
    "which to decide it.",
    "A leader endpoint is annotation/leader evidence: where a leader line was read to "
    "end on the page. It is not an attachment point and is not shown as one.",
    "Per page, the reading that stands for it is the one J28's own authority selects. "
    "Earlier readings stay recorded and are not shown here.",
    "Nothing on this page reads, writes, resolves or advances anything.",
)


@dataclasses.dataclass(frozen=True)
class AnnotationEvidenceOccurrence:
    """One recorded occurrence, as the row states it.

    Every field is the record's own value rendered as the record carries it — a string
    verbatim, anything else as its own JSON. A field the row does not state is None,
    which the page prints as absent. Nothing here is defaulted, inferred or renamed.
    """

    page_number: str | None
    annotation_x: str | None
    annotation_y: str | None
    mark_candidate: str | None
    mark_readable: str | None
    operator_count: str | None
    duplicate_operator_count: str | None
    schedule_row_candidate: str | None
    tag_box_present: str | None
    leader_present: str | None
    extractor_version: str | None
    extracted_at: str | None
    source_pdf_sha256: str | None
    rule_set_json: str | None
    evidence_json: str | None


@dataclasses.dataclass(frozen=True)
class AnnotationEvidenceGroup:
    """The occurrences that stand for one page of one drawing.

    The drawing is named rather than chosen: a project with more than one document
    shows one group per document, because picking between them would be deciding which
    document a mark belongs to, and that is not this surface's decision to make.
    """

    drawing_id: str
    page_number: int
    occurrences: tuple[AnnotationEvidenceOccurrence, ...]


@dataclasses.dataclass(frozen=True)
class AnnotationEvidenceReview:
    """One project's recorded annotation evidence, as this surface presents it."""

    project_id: str
    page_filter: int | None
    state_code: str
    state_detail: str
    boundary: tuple[str, ...]
    pages: tuple[int, ...]
    groups: tuple[AnnotationEvidenceGroup, ...]
    total_occurrences: int
    shown_occurrences: int


def _project_store():
    """The production repository, imported on use.

    Importing this module must not open a database client, so the module that does is
    reached here rather than at the top.
    """
    from app.engineering_data import repository

    return repository


def _store_is_absent(error: BaseException) -> bool:
    """Whether the store's own refusal says the table is not present at all.

    The client's own code when it carries one, and its own wording when it does not.
    Deliberately narrow: everything that is not this refusal is re-raised, so no other
    failure can be dressed up as an absent table.
    """
    if getattr(error, "code", None) == _UNKNOWN_TABLE_CODE:
        return True
    described = str(error)
    return _UNKNOWN_TABLE_CODE in described or "schema cache" in described.lower()


def _shown(value: Any) -> str | None:
    """One recorded value as the record carries it, or None when it carries none.

    A string is shown verbatim, including an empty one — that is what the record says.
    Anything else is shown as its own JSON, which is the form it was recorded in.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_or_none(value: Any) -> str | None:
    """The row's JSON column as text: a value already carried as text IS that text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _occurrence(row: Mapping[str, Any]) -> AnnotationEvidenceOccurrence:
    """One stored row, as the surface's own record of it."""
    return AnnotationEvidenceOccurrence(
        page_number=_shown(row.get("page_number")),
        annotation_x=_shown(row.get("annotation_x")),
        annotation_y=_shown(row.get("annotation_y")),
        mark_candidate=_shown(row.get("mark_candidate")),
        mark_readable=_shown(row.get("mark_readable")),
        operator_count=_shown(row.get("operator_count")),
        duplicate_operator_count=_shown(row.get("duplicate_operator_count")),
        schedule_row_candidate=_shown(row.get("schedule_row_candidate")),
        tag_box_present=_shown(row.get("tag_box_present")),
        leader_present=_shown(row.get("leader_present")),
        extractor_version=_shown(row.get("extractor_version")),
        extracted_at=_shown(row.get("extracted_at")),
        source_pdf_sha256=_shown(row.get("source_pdf_sha256")),
        rule_set_json=_json_or_none(row.get("rule_set")),
        evidence_json=_json_or_none(row.get("evidence")),
    )


def _group_key(row: Mapping[str, Any]) -> tuple[str, int]:
    """Which drawing and page a standing row belongs to.

    Both values are required by the storage authority that selected the row, so this
    reads them rather than re-deriving or defaulting them.
    """
    return (str(row["drawing_id"]), int(row["page_number"]))


def _groups(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[AnnotationEvidenceGroup, ...]:
    """The standing rows, grouped by drawing and page, in the authority's own order.

    The order is the one `authoritative_occurrences` returned, so the surface and the
    storage layer cannot drift: nothing here re-sorts a page's occurrences.
    """
    order: list[tuple[str, int]] = []
    buckets: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = _group_key(row)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(row)
    return tuple(
        AnnotationEvidenceGroup(
            drawing_id=key[0],
            page_number=key[1],
            occurrences=tuple(_occurrence(row) for row in buckets[key]),
        )
        for key in order
    )


def _state(rows: tuple[Mapping[str, Any], ...], groups, page: int | None) -> tuple[str, str]:
    """Which of the four states this read is in, and the sentence that says so."""
    if not rows:
        return (
            ANNOTATION_EVIDENCE_NONE,
            "This project has no recorded annotation occurrence. Nothing is missing from "
            "this page: the evidence layer recorded none for it.",
        )
    if page is not None and not groups:
        return (
            ANNOTATION_EVIDENCE_NONE_FOR_PAGE,
            f"No recorded annotation occurrence stands for page {page}. The pages this "
            "project does have recorded evidence for are listed below.",
        )
    return (
        ANNOTATION_EVIDENCE_RECORDED,
        "Recorded annotation evidence, exactly as J28 read it. Every value below is the "
        "record's own.",
    )


def build_annotation_evidence_review(
    project_id: str, *, page: int | None = None, repository=None,
) -> AnnotationEvidenceReview:
    """One project's recorded PDF annotation evidence, as plain reviewable data.

    Read-only: one `.select()` through the repository, then the storage layer's own
    authority over which reading stands for each page. Nothing is written, nothing is
    created and no state is advanced.

    Absence is a value rather than an exception — a store that is not present, a project
    with nothing recorded, and a page with nothing recorded are three states named by
    their own code. Every OTHER failure propagates unchanged: an unreachable database
    must not render as an absent table.

    `page`, when given, narrows which pages are shown. It never narrows what is read,
    so the page list is the project's whole recorded set whatever is being shown.

    `repository` is the existing repository module, or a double standing in for it. It
    must expose `pdf_annotation_occurrences_for_project`.
    """
    if not (isinstance(project_id, str) and project_id.strip()):
        raise ValueError("project_id must be a non-empty str.")
    if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
        raise ValueError("page must be a positive integer when it is given.")

    store = repository if repository is not None else _project_store()
    try:
        read = tuple(store.pdf_annotation_occurrences_for_project(project_id))
    except Exception as error:  # noqa: BLE001 — re-raised unless it is the one refusal
        if not _store_is_absent(error):
            raise
        return AnnotationEvidenceReview(
            project_id=project_id,
            page_filter=page,
            state_code=ANNOTATION_STORE_ABSENT,
            state_detail=(
                "The annotation evidence store is not present in this deployment, so "
                "there is nothing to show. This page created nothing: the schema is not "
                "this surface's to change."
            ),
            boundary=_BOUNDARY,
            pages=(),
            groups=(),
            total_occurrences=0,
            shown_occurrences=0,
        )

    standing = authoritative_occurrences(read)
    every_group = _groups(standing)
    shown = every_group if page is None else tuple(
        group for group in every_group if group.page_number == page
    )
    state_code, state_detail = _state(standing, shown, page)
    return AnnotationEvidenceReview(
        project_id=project_id,
        page_filter=page,
        state_code=state_code,
        state_detail=state_detail,
        boundary=_BOUNDARY,
        pages=tuple(sorted({group.page_number for group in every_group})),
        groups=shown,
        total_occurrences=len(standing),
        shown_occurrences=sum(len(group.occurrences) for group in shown),
    )


def render_annotation_evidence_review(review: AnnotationEvidenceReview) -> str:
    """The review as a page. Pure: it reads nothing, writes nothing and holds nothing.

    The action prefix is this surface's own root, so the back link returns to the
    project's review page rather than to a route this surface does not own.
    """
    return render.render_annotation_evidence_page(
        review, action_prefix=f"/production/review/{review.project_id}"
    )
