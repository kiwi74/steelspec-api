"""
Milestone 7Y — the intake seam between the REAL project AI extraction output
and the 7X project review queue. It reads extraction results; it interprets
nothing, generates no CAD, and invents nothing:

    app.ai_analysis.pdf_vision_analyzer.analyze_pdf_pages()   -> list[PageExtraction]
            |
    intake_page_extractions()          (this module: flatten exactly as app/pipeline.py does)
            |
    create_project_connection_collection()                      (7X)
            |
    one 7W ConnectionReviewPackage per candidate ... build_project_review_summary() ... 7V

WHAT THE REAL EXTRACTION ACTUALLY EMITS (read from source, not assumed):
analyze_pdf_pages() returns one PageExtraction per analysed page:
`page_number`, `drawing_number`, `drawing_title`, `revision`, `raw_members`,
`raw_connections`, `parse_failed`. Each `raw_connections` entry is exactly the
AI's connection object (detail_reference, grid_reference, connects_members,
connection_type, bolts[], plates[], welds[], confidence — the schema 7W's
AIExtractedConnection preserves). app/pipeline.py then tags each with
`{**c, "page_num": p.page_number}` and takes the drawing number from page 1.
This module reproduces exactly that flattening (a test pins it against
pipeline.py's source) and hands the result to 7X unchanged. It does not import
app.ai_analysis or app.pipeline (both pull in app.config and its unrelated
SUPABASE_URL requirement): pages may be real PageExtraction objects or plain
mappings with the same field names, e.g. a captured JSON extraction.

WHAT IT REFUSES TO DO: create a candidate the AI did not report; give a page
with an unparseable response an empty-but-successful reading; assume a page
that produced no connections was inspected and found empty; supply a
`connection_id`, START/END, location, attachment or hole value; derive
project members from geometry. `parse_failed_pages` and
`pages_not_analysed` are surfaced precisely because "no candidates from this
page" can mean the page was never read.

KNOWN MEMBER MARKS: app/pipeline.py links a connection's `connects_members`
to members by exact mark against the validated steel members it just
inserted (`mark_to_id`). known_member_marks_from_validated_members() reuses
that same convention — nothing invented, nothing inferred from geometry, no
counting of members. These marks are themselves AI-derived (the extraction
may have missed a member), so an "unknown" mark means "not among the members
the extraction found", not "not a real member"; 7W/7X report it as they
already do. If no member context is available, pass None: 7X's existing
missing-context behaviour applies and an advisory says the marks were unchecked.

SCOPE: no human review is applied here, no CAD or drawing code is imported or
called, and the review queue makes no claim that the AI found every connection.
"""
import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.cad_engine.project_connection_review import (
    SUMMARY_SCOPE_STATEMENT,
    ProjectConnectionReviewCollection,
    create_project_connection_collection,
)

__all__ = [
    "INTAKE_SCOPE_STATEMENT", "ProjectExtractionIntake",
    "intake_page_extractions", "known_member_marks_from_validated_members",
]

INTAKE_SCOPE_STATEMENT = (
    "This queue was built only from the page extractions supplied. A page whose AI response could not be parsed "
    "was not read, and a page absent from the extraction (or beyond the pipeline's page cap) was never analysed, "
    "so 'no candidates from this page' does not mean 'no connections on this page'. " + SUMMARY_SCOPE_STATEMENT
)


@dataclass(frozen=True)
class ProjectExtractionIntake:
    """
    The 7X collection plus what the intake itself knows about the extraction's coverage, so a reviewer can tell an
    empty reading from an unread page.

      - parse_failed_pages: pages the AI's response could not be parsed for (their content was never read).
      - unusable_connection_entries: (page_number, raw value) for `raw_connections` entries that were not objects;
        preserved verbatim, never turned into candidates.
      - pages_not_analysed: drawing-set pages beyond those supplied, when the caller states the set's page count
        (None when unknown — never guessed).
      - analysed_page_numbers: the page numbers of the supplied pages whose extractions were usable
        (parse-failed pages are recorded in `parse_failed_pages` instead, never here). Recorded as supplied —
        page order, values verbatim; this module never numbers pages itself.
    """
    collection: ProjectConnectionReviewCollection
    pages_received: int
    parse_failed_pages: tuple[Any, ...]
    unusable_connection_entries: tuple[tuple[Any, Any], ...]
    drawing_set_page_count: int | None
    pages_not_analysed: int | None
    scope_statement: str
    analysed_page_numbers: tuple[Any, ...] = ()


def _field(page: Any, name: str, default: Any = None) -> Any:
    """A PageExtraction attribute, or the same key of a plain mapping (e.g. a captured JSON extraction)."""
    if isinstance(page, Mapping):
        return page.get(name, default)
    return getattr(page, name, default)


def intake_page_extractions(
    pages: Iterable[Any],
    *,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
    known_member_marks: Sequence[str] | None = None,
    drawing_set_page_count: int | None = None,
) -> ProjectExtractionIntake:
    """
    Flattens the pages' AI connection objects exactly as app/pipeline.py does
    (`{**c, "page_num": page_number}`, page order then AI order) and builds the 7X review collection.

    `project_id`/`source_drawing_id` come from the caller (the pipeline knows them; the AI does not). The drawing
    number is taken from the page numbered 1, as the pipeline does. The pages and their entries are never mutated.
    """
    pages = list(pages)
    raw_connections: list[dict[str, Any]] = []
    parse_failed: list[Any] = []
    analysed: list[Any] = []
    unusable: list[tuple[Any, Any]] = []

    for page in pages:
        page_number = _field(page, "page_number")
        if _field(page, "parse_failed", False):
            parse_failed.append(page_number)
        else:
            analysed.append(page_number)
        entries = _field(page, "raw_connections")
        if entries is None:
            continue
        if not isinstance(entries, (list, tuple)):
            unusable.append((page_number, copy.deepcopy(entries)))
            continue
        for entry in entries:
            if isinstance(entry, dict):
                raw_connections.append({**entry, "page_num": page_number})
            else:
                unusable.append((page_number, copy.deepcopy(entry)))

    drawing_number = next((_field(p, "drawing_number") for p in pages if _field(p, "page_number") == 1), None)

    collection = create_project_connection_collection(
        raw_connections,
        project_id=project_id,
        source_drawing_id=source_drawing_id,
        drawing_number=drawing_number,
        known_member_marks=known_member_marks,
    )
    return ProjectExtractionIntake(
        collection=collection,
        pages_received=len(pages),
        parse_failed_pages=tuple(parse_failed),
        unusable_connection_entries=tuple(unusable),
        drawing_set_page_count=drawing_set_page_count,
        pages_not_analysed=(
            None if drawing_set_page_count is None else max(0, drawing_set_page_count - len(pages))
        ),
        scope_statement=INTAKE_SCOPE_STATEMENT,
        analysed_page_numbers=tuple(analysed),
    )


def known_member_marks_from_validated_members(members: Iterable[Mapping[str, Any]] | None) -> tuple[str, ...] | None:
    """
    The marks of the validated steel members, by app/pipeline.py's own `mark_to_id` convention
    (`{row["mark"]: ... for row in inserted_members if row.get("mark")}`): exact strings, first-seen order,
    duplicates collapsed, members without a mark ignored. Returns None — no member context — when `members`
    is None. Nothing here counts members, reads geometry, or invents a mark.
    """
    if members is None:
        return None
    marks: list[str] = []
    for member in members:
        mark = member.get("mark")
        if mark and mark not in marks:
            marks.append(mark)
    return tuple(marks)
