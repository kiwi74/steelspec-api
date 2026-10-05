"""
Milestone J19 — WHAT A PROJECT'S PERSISTED REVIEW RECORD STATES.

This module is a READ. It loads one project's persisted review state and states
it as immutable plain data. It decides no engineering question, derives no
status, interprets no record and writes nothing.

WHAT IT READS, AND FROM WHERE
=============================
Only reads that already existed, called through the repository that owns them:

    repository.get_project(project_id)            the project row
    repository.drawing_sets_for_project(id)       the sets the project has
    repository.drawings_for_drawing_set(id)       the documents each was read from

and, since J61, ONE further read of the same kind:

    repository.document_for_drawing(drawing_id)   the source document a lineage read

That read is made ONLY for a drawing whose own row names a document. A lineage that
names none — one created before J61, or by the DXF path — has nothing to read, and its
document is stated as None rather than fetched. The document is exposed as the row's own
stored values; nothing here derives a role, a revision, a format or an identity from a
filename, a path, a page count or any other field, and a document whose role is UNKNOWN
is stated as UNKNOWN because UNKNOWN is what is stored.

and, for the two halves of the committed record, the modules that WRITE those
halves and therefore own their format:

    coverage_from_warnings(project["warnings"])        J15's reader
    parse_failures_from_warnings(project["warnings"])  J17's reader

No SQL was written here, and nothing here reads a table the repository does not
already expose.

The warnings are handed to those two readers as the tuple this record states, and
never as the raw value: a `warnings` value that is not a list of lines (a single
string, a mapping, a number) states NO warnings here, because iterating such a
value character by character is what the readers would otherwise do. The two
answers are the same — no record — and this one is stated rather than stumbled
into.

WHAT IT DELIBERATELY DOES NOT READ
==================================
`steel_members`, `connections` and `review_items` are NOT read, and that is a
decision rather than an omission:

  * The project's review state is `projects.status`, which J13 already DERIVED
    from those rows, fail-closed, and PERSISTED. Re-deriving it here from the
    same inputs would create the second status reading J19 is expressly
    forbidden to create, and reading the inputs to display a tally beside the
    status would be that second reading with a smaller name.
  * `review_items` is the connection-review intake `app/pipeline.py` writes
    (one row per connection it found, with the profile that reviewed it). It is
    a production-readable record of WHAT WAS FOUND, not a status source, and
    nothing in the existing architecture derives a project status from it.
  * What those rows would be displayed BY is the 7AK connection-review contract,
    which projects an in-process AI extraction capture that no table stores. That
    surface therefore has no persisted production source and is NOT bound by this
    milestone — see the J19 report. Reading rows for a surface that cannot be
    built would be machinery with no consumer, and presenting that surface as
    production-complete when it is not is the one thing that must not happen.

A record whose project does not exist is None — the absence of a project, never a
blank one (the same convention `repository.get_project` documents).
"""

from __future__ import annotations

from collections.abc import Mapping

from dataclasses import dataclass

from app.cad_engine.page_exception_contract import (
    PageExceptionContract,
    build_page_exception_contract,
)
from app.validation.page_coverage import PageCoverage, coverage_from_warnings
from app.validation.parse_failures import ParseFailures, parse_failures_from_warnings

__all__ = [
    "DocumentRecord",
    "DrawingRecord",
    "DrawingSetRecord",
    "ProjectReviewRecord",
    "read_project_record",
]


def _text(value) -> str | None:
    """The value when it is a non-empty string, else None. Never coerced."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _int(value) -> int | None:
    """The value when it is a whole number, else None. A bool is not a count."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _warnings(value) -> tuple[str, ...]:
    """The project's persisted warnings as lines, verbatim and in order.

    Nothing is parsed, filtered, reordered or dropped: these are the record's own
    lines and they are handed to the readers that wrote them exactly as they were
    written. A value that is not a list of lines states no warnings at all.
    """
    if isinstance(value, (list, tuple)):
        return tuple(line for line in value if isinstance(line, str))
    return ()


def _rows(value) -> tuple[Mapping, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(row for row in value if isinstance(row, Mapping))
    return ()


@dataclass(frozen=True)
class DocumentRecord:
    """One source document, as the `project_documents` row states it (Milestone J61).

    Every field is the stored value and nothing else. `role` in particular is exposed
    exactly as it is stored — including `UNKNOWN`, which is what every document J61 creates
    carries — and this module never infers one from a filename, a page count, a path, a
    title or any model output. `content_sha256` is exposed as it is stored, and None means
    IDENTITY NOT PROVEN: it is not a wildcard and no caller may compare it as one.
    """

    document_id: str | None
    storage_path: str | None
    file_name: str | None
    source_format: str | None
    byte_size: int | None
    page_count: int | None
    content_sha256: str | None
    role: str | None
    revision_label: str | None
    supersedes_document_id: str | None


@dataclass(frozen=True)
class DrawingRecord:
    """One document of a drawing set, as the `drawings` row states it.

    `document` is the source document this analysis lineage read, or None when the lineage
    names none. The two are separate fields on purpose: a drawing is an ATTEMPT at reading a
    document, not the document, and a record that collapsed them would state that a
    re-extraction was a second document.
    """

    drawing_id: str | None
    file_name: str | None
    storage_path: str | None
    page_count: int | None
    document: DocumentRecord | None = None


@dataclass(frozen=True)
class DrawingSetRecord:
    """One drawing set of a project: the counters IT carries, and its drawings.

    The counters are the row's own values, not a count of anything this module
    read — J16 requires them to agree with the coverage record before either is
    rewritten, so a caller that wants to compare them can.
    """

    drawing_set_id: str | None
    name: str | None
    status: str | None
    total_pages: int | None
    pages_analysed: int | None
    drawings: tuple[DrawingRecord, ...]


@dataclass(frozen=True)
class ProjectReviewRecord:
    """One project's persisted review state, as this milestone reads it.

    Every field is the record's own value, verbatim: the status is J13's derived
    status as it was persisted (never re-derived), the warnings are the project's
    own lines, and the two halves are those lines as the milestones that write
    them read them back.

    `page_exception_contract()` is the whole of this record's interpretation, and
    it is not this module's: it is J18's own contract builder, called with the
    same three values the J18 surface calls it with, so the production surface
    and the internal review surface render from the SAME object.
    """

    project_id: str
    project_status: str | None
    source_format: str | None
    uploaded_file_path: str | None
    warnings: tuple[str, ...]
    coverage: PageCoverage | None
    failures: ParseFailures | None
    drawing_sets: tuple[DrawingSetRecord, ...]

    def page_exception_contract(self) -> PageExceptionContract:
        """This project's page-exception contract (J18), built from this record."""
        return build_page_exception_contract(
            project_id=self.project_id,
            coverage=self.coverage,
            failures=self.failures,
            project_status=self.project_status,
        )


def _document_record(repository, drawing_id, document_id) -> DocumentRecord | None:
    """The document one lineage names, read through the repository, or None.

    `None` is returned WITHOUT a read when the drawing names no document: there is nothing
    to look up, and reading anything to display an absence would be a read of a table for
    no fact. The read that follows is the repository's own `document_for_drawing`, which
    states the same absence as `None` for a pointer that names no row.

    A row that is not a mapping, or one whose own id is not a non-empty string, is stated
    as no document rather than as a blank one — the same convention every other absence in
    this module follows.
    """
    if document_id is None:
        return None
    row = repository.document_for_drawing(drawing_id)
    if not isinstance(row, Mapping) or _text(row.get("id")) is None:
        return None
    return DocumentRecord(
        document_id=_text(row.get("id")),
        storage_path=_text(row.get("storage_path")),
        file_name=_text(row.get("file_name")),
        source_format=_text(row.get("source_format")),
        byte_size=_int(row.get("byte_size")),
        page_count=_int(row.get("page_count")),
        content_sha256=_text(row.get("content_sha256")),
        role=_text(row.get("role")),
        revision_label=_text(row.get("revision_label")),
        supersedes_document_id=_text(row.get("supersedes_document_id")),
    )


def _drawing_set_records(repository, project_id) -> tuple[DrawingSetRecord, ...]:
    """The project's drawing sets, their drawings, and each drawing's document."""
    return tuple(
        DrawingSetRecord(
            drawing_set_id=_text(row.get("id")),
            name=_text(row.get("name")),
            status=_text(row.get("status")),
            total_pages=_int(row.get("total_pages")),
            pages_analysed=_int(row.get("pages_analysed")),
            drawings=tuple(
                DrawingRecord(
                    drawing_id=_text(drawing.get("id")),
                    file_name=_text(drawing.get("file_name")),
                    storage_path=_text(drawing.get("storage_path")),
                    page_count=_int(drawing.get("page_count")),
                    document=_document_record(
                        repository,
                        _text(drawing.get("id")),
                        _text(drawing.get("document_id")),
                    ),
                )
                for drawing in _rows(repository.drawings_for_drawing_set(row.get("id")))
            ),
        )
        for row in _rows(repository.drawing_sets_for_project(project_id))
    )


def read_project_record(
    project_id: str, *, project_row=None, repository=None,
) -> ProjectReviewRecord | None:
    """The persisted review record of one project, or None when there is none.

    `project_row` is the row the caller has ALREADY loaded and authorized — the
    production path passes it so that a single request reads the project row
    once, and so that the record is provably the row that was authorized rather
    than a second read that could disagree with it. When it is not supplied, the
    row is read here.

    `repository` is the existing repository module (or a double standing in for
    it). It must expose `get_project`, `drawing_sets_for_project` and
    `drawings_for_drawing_set`, and — since J61 — `document_for_drawing`, which is
    called only for a drawing whose row names a document. It defaults to the
    production repository, which is imported inside this function so that importing
    this module does not pull the database client into a process that never reads one.
    """
    if not (isinstance(project_id, str) and project_id.strip()):
        raise ValueError("project_id must be a non-empty str.")

    if repository is None:
        from app.engineering_data import repository as project_store
    else:
        project_store = repository

    if project_row is None:
        project_row = project_store.get_project(project_id)
        if project_row is None:
            return None
    if not isinstance(project_row, Mapping):
        raise TypeError(
            f"the project store returned {type(project_row).__name__} for {project_id!r} "
            "rather than a project row."
        )

    warnings = _warnings(project_row.get("warnings"))
    return ProjectReviewRecord(
        project_id=_text(project_row.get("id")) or project_id,
        project_status=_text(project_row.get("status")),
        source_format=_text(project_row.get("source_format")),
        uploaded_file_path=_text(project_row.get("uploaded_file_path")),
        warnings=warnings,
        coverage=coverage_from_warnings(warnings),
        failures=parse_failures_from_warnings(warnings),
        drawing_sets=_drawing_set_records(project_store, project_id),
    )
