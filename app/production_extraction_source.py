"""
J63 (S1) — DOCUMENT-ADDRESSED EXTRACTION: WHICH source document a run reads.

THE ONE QUESTION THIS MODULE ANSWERS
------------------------------------
Before this milestone, every extraction of a project had exactly one possible source:
`projects.uploaded_file_path`, read straight off the project row by each of the three
extraction routes in `app/main.py`. J61 gave a project source DOCUMENTS — a project may
now hold several, each with its own identity, its own storage path and its own analysis
lineage — but nothing at the extraction boundary could say WHICH of them a run was about.
The project-level column was not merely the default source; it was the only one.

This module is the boundary that lets a caller name a document:

    PROJECT
      ├── PROJECT_DOCUMENT A → DRAWING / ANALYSIS_RUN / CAPTURE
      └── PROJECT_DOCUMENT B → DRAWING / ANALYSIS_RUN / CAPTURE

The selection is `project_documents.id` and nothing else. A document is never chosen by
filename, by storage path alone, by page count, by role, by `created_at`, by being the
newest, by being the first, or by any ordering — this module contains no ordering of
documents at all, and `project_documents_for_project`'s own order (creation order, which
is `RECORDED FACT and not a ranking`) is never read as a preference. A caller that has not
named a document has not chosen one, and this module does not choose for it.

WHAT AN ADDRESSED DOCUMENT MUST PROVE
-------------------------------------
Two facts, both decided by a single read of the addressed project's own documents:

    EXISTS      the id names a row
    BELONGS     that row's `project_id` is this project

Both are decided by the same query — the addressed project's documents are listed, and the
addressed id is looked for AMONG THEM — so a document of another project can never be
reached, not even to be refused by name. There is no second lookup by bare id anywhere in
this module, which is what makes the cross-project case a structural impossibility rather
than a check that could be forgotten.

WHY IT IS A SEPARATE BOUNDARY, AND NOT A CHANGE TO J61
------------------------------------------------------
J61 established the identity and did not touch extraction; J62 designed the document model
and wrote nothing. This module is J62's S1 and only S1. It assigns no role, infers no role,
parses no transmittal, reconciles no documents, resolves no conflict, cites no field,
creates no document-set entity, computes no per-document coverage, changes no hole
diameter, no plate vocabulary, no grid or RL derivation, no attachment binding, and adds
no column to any table. `project_documents` is read here and never written: its role
column, its revision label and its identity column are exactly as J61 left them, and this
milestone deliberately cannot fill a NULL `content_sha256` — see the gap stated below.

WHAT IT DOES NOT DO TO THE DATABASE
-----------------------------------
No migration, no write, no backfill, no row created, no column updated. The module makes
exactly ONE repository read, and only for a request that addressed a document: the
project's own documents, read through the function J61 already publishes
(`project_documents_for_project`) and filtered by the addressed id. Nothing here hashes a
file, reads Storage, renders a page, calls a model, or persists anything.

THE OMITTED REQUEST, AND WHY IT IS THE OLD ONE EXACTLY
------------------------------------------------------
An omitted `document_id` is the pre-J63 request and is answered the pre-J63 way: the source
is `projects.uploaded_file_path`, the project-level column, exactly as every extraction
before this milestone read it. Nothing is read from `project_documents` on that path at all
— the omitted case makes NO new database read, which is what makes "a project that behaved
in some way before J63 behaves that way after it" a property this module can state rather
than argue.

That is deliberate, and there are two reasons for it.

The first is the upload flow this application already has: "put a file, set
`uploaded_file_path`, POST /extract". The file being extracted is in general NOT yet a
`project_documents` row, because a document row is created BY the run that reads it (and is
not created at all when the source's bytes could not be read — a real state, where a run
leaves a drawing that names no document). Consulting the document table for an omitted
request would have made a freshly uploaded file unextractable, and would have re-routed a
state J61 created explicitly for the unreadable-source case.

The second is that refusing the ambiguity is not this module's to do, because the guard
for it lives one layer up and is not the same guard on every route. This module answers
WHICH SOURCE A REQUEST NAMES; whether that source is ambiguous is a question about the
reading that is about to happen, and each operation asks it itself. A project can only
reach two documents by having had two runs that each created a drawing set, because a
document is created by the same function that creates the set, and every project's
documents were related to their lineages by J61 — so a project whose source is genuinely
ambiguous is a project with more than one drawing set, and
`CONTINUATION_DRAWING_SET_UNRESOLVED`, which already means exactly "which document these
pages belong to is ambiguous", refuses it at 409 on all three routes:

    continuation, retry   `_source_lineage`'s count checks (J16's, unchanged by J63)
    the first window      `pipeline.plan_first_window` (J63A), which counts the project's
                          own DOCUMENTS and refuses an omitted address when there is more
                          than one — the case J63 left open, where `/extract` started a new
                          reading of the project-level column on a project holding two

The one live project in that state, `87a20c06-df59-4d9c-975a-346a5d712e2d` (two documents
sharing one storage path: the same file extracted twice), is refused by all three routes,
at the same status and under the same code.

THE GAP THIS MILESTONE DOES NOT CLOSE, STATED PLAINLY
-----------------------------------------------------
Addressing a document selects WHICH BYTES a run reads. It does not — and cannot — assert
that those bytes ARE that document, because J61's documents all carry `content_sha256 =
NULL` (they are the backfill rows) and this milestone is forbidden from hashing anything
into them. So a run addressed at a legacy document reads that document's storage path and
records the document identity of the bytes it found, by J61's content rule: a NULL hash
proves nothing about sameness, and nothing here pretends otherwise. For every document
whose identity IS proven, the content rule resolves the run back to that same document row,
which is the property that makes a re-extraction a repeat rather than a second document.
"""
from __future__ import annotations

import dataclasses
from typing import Mapping

from app.engineering_data import repository as repo
from app.validation.page_windows import (
    CONTINUATION_SOURCE_MISMATCH,
    ContinuationRefused,
)

__all__ = [
    "EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID",
    "EXTRACTION_INPUT_REFUSED_NOT_A_MAPPING",
    "EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD",
    "EXTRACTION_INPUT_REFUSED_UNKNOWN_FIELD",
    "EXTRACTION_INPUT_REFUSALS",
    "REQUEST_FIELDS",
    "ROUTE_PATHS",
    "SERVER_OWNED_FIELDS",
    "ExtractionInputRefused",
    "SourceDocument",
    "parse_extraction_source_request",
    "resolve_extraction_source",
]

#: The three routes this boundary serves, named here rather than only in `app.main` so
#: that "which paths read a project's source" has one answer in the codebase. All three
#: read one source document, so all three accept the same optional address.
ROUTE_PATHS = (
    "/extract/{project_id}",
    "/continue-extraction/{project_id}",
    "/retry-extraction/{project_id}",
)

#: The only key an extraction request may carry. It states WHICH of the project's source
#: documents the run is about; the project still comes from the URL, the reviewer from the
#: access token, the format from the project's own row and the bytes from Storage.
#:
#: It is OPTIONAL, and an absent body is still the whole of the request, because the
#: project-level source resolves the ordinary case exactly as it did before this field
#: existed. A caller that names a document is believed; a caller that names none has not
#: chosen, and this boundary never chooses on its behalf.
REQUEST_FIELDS: tuple[str, ...] = ("document_id",)

#: Everything a client may NOT supply to an extraction, refused BY NAME so that a client
#: which tries to name one is told which. Each of these is a fact this boundary already
#: owns — it is read from the project row, from the project's own documents, from the
#: drawing lineage the run persists into, or from the record it is continuing — and a
#: caller that could state one could make a run claim a source, a size, a page or a
#: coverage record that the persisted facts do not support.
SERVER_OWNED_FIELDS = (
    "project_id",
    "project",
    "user_id",
    "reviewer",
    "reviewer_id",
    "authorization",
    "token",
    "storage_path",
    "uploaded_file_path",
    "source_format",
    "file_name",
    "content_sha256",
    "byte_size",
    "page_count",
    "total_pages",
    "document",
    "documents",
    "document_role",
    "role",
    "drawing_id",
    "drawing_set_id",
    "analysis_run_id",
    "page",
    "page_number",
    "first_page",
    "last_page",
    "coverage",
    "failures",
    "warnings",
    "status",
)

# ---------------------------------------------------------------- request-input refusal
# The shape of an input refusal is J47's and J50's: a code that names the KIND of mistake,
# and a statement of the facts. The vocabulary is declared per boundary rather than shared,
# because what a client may not say is a property of the boundary it is talking to — the
# review-opening boundary owns revisions and decisions, this one owns sources and pages.
#
# The CODES are deliberately the same strings J50 uses for the same two shapes of mistake
# ("not an object", "a field this boundary never takes from a client"), because a client
# that has learned to read one production refusal has learned to read this one.
EXTRACTION_INPUT_REFUSED_NOT_A_MAPPING = "EXTRACTION_INPUT_REFUSED_NOT_A_MAPPING"
EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD = "EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD"
EXTRACTION_INPUT_REFUSED_UNKNOWN_FIELD = "EXTRACTION_INPUT_REFUSED_UNKNOWN_FIELD"
EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID = "EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID"

EXTRACTION_INPUT_REFUSALS = (
    EXTRACTION_INPUT_REFUSED_NOT_A_MAPPING,
    EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD,
    EXTRACTION_INPUT_REFUSED_UNKNOWN_FIELD,
    EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID,
)


class ExtractionInputRefused(ValueError):
    """The request body was not an extraction request. Nothing was read or changed.

    Raised before any project state is touched, so a request refused here has read no
    document, resolved no source, queued no background task and written nothing.
    """

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


# ======================================================================================
# The request surface.
# ======================================================================================
def parse_extraction_source_request(body: object) -> str | None:
    """The document this extraction names, or None. Reads nothing, changes nothing.

    An ABSENT body is the request: `None` is accepted, and so is an empty object, because
    both say the same thing — this caller is naming no document, and the project-level
    source resolves as it always did. Every other body is refused, and a body naming a
    server-controlled field is refused by that field's own name so the caller learns which
    one it should not have sent.

    Returns the `document_id` the caller stated, or `None` when it stated none. An explicit
    `null` states none as completely as an absent key does — it is the only way a JSON
    client can say "no value" for a field it holds — so it is read as the same request
    rather than as a half-stated address. Every OTHER non-string, and a blank or
    whitespace-only string, IS a half-stated address: it is refused rather than coerced,
    stripped or interpreted, because a caller who has not chosen is not a caller this
    boundary may choose for.

    The value is returned VERBATIM. Nothing here checks that the id is a UUID, that it
    exists, or that it belongs to the project: whether the document exists is a fact about
    persisted state and is decided by `resolve_extraction_source`, which reads it — not by
    the shape of the string a client sent.
    """
    if body is None:
        return None
    if not isinstance(body, Mapping):
        raise ExtractionInputRefused(
            EXTRACTION_INPUT_REFUSED_NOT_A_MAPPING,
            f"the request body is {type(body).__name__} rather than an object; an extraction "
            "is requested with no fields at all, or with the document it is about",
        )
    owned = sorted(name for name in SERVER_OWNED_FIELDS if name in body)
    if owned:
        raise ExtractionInputRefused(
            EXTRACTION_INPUT_REFUSED_SERVER_OWNED_FIELD,
            f"the request body names {owned}. The project comes from the URL, the reviewer "
            "from the access token, the source from the project's own documents and the "
            "coverage from the record being continued — none of them is ever taken from a "
            "caller",
        )
    unknown = sorted(name for name in body if name not in REQUEST_FIELDS)
    if unknown:
        raise ExtractionInputRefused(
            EXTRACTION_INPUT_REFUSED_UNKNOWN_FIELD,
            f"the request body carries {unknown}; an extraction is requested with no fields "
            "at all, or with the document it is about",
        )

    if "document_id" not in body:
        return None
    document_id = body["document_id"]
    if document_id is None:
        return None
    if not (isinstance(document_id, str) and document_id.strip()):
        raise ExtractionInputRefused(
            EXTRACTION_INPUT_REFUSED_DOCUMENT_INVALID,
            f"the request names document_id={document_id!r}; a document is addressed by a "
            "non-empty string or not at all, and this boundary does not choose one",
        )
    return document_id


# ======================================================================================
# The source itself.
# ======================================================================================
@dataclasses.dataclass(frozen=True)
class SourceDocument:
    """WHICH source an extraction of this project reads, and where its bytes are.

    `document_id` is the document the CALLER addressed, or None when the caller addressed
    none. It is never a document this boundary picked: the omitted case leaves it None and
    carries the project-level source, so a run can always be asked which of the two ways
    its source was decided and answer truthfully.

    `storage_path` is the Storage key the run must read, and it is the only place a caller
    may obtain one from. It is `projects.uploaded_file_path` for an omitted request — the
    project's own column, unchanged — and the addressed document's own `storage_path`
    otherwise.
    """

    project_id: str
    storage_path: str
    document_id: str | None = None

    @property
    def addressed(self) -> bool:
        """Whether a caller NAMED the document, rather than the project's own file.

        This is the one bit that decides which source rules a run is held to: an addressed
        document scopes the drawing lineage to the one that read it, and an omitted one is
        decided by the project-level guards exactly as it was before J63.
        """
        return self.document_id is not None


def resolve_extraction_source(
    project_id: str,
    *,
    document_id: str | None = None,
    project: Mapping | None = None,
    repository=None,
) -> SourceDocument:
    """The source document an extraction of this project reads, or a refusal.

    Raises `ContinuationRefused` — J16's own exception, carrying J16's own codes — because
    the facts it refuses on are J16's: which document a run reads is the same question the
    window contract already answers for a continuation, and a second vocabulary for it
    would name one state two ways. Nothing has been read, downloaded or written when this
    raises, and the omitted case below is the pre-J63 path verbatim, so no project that
    could be extracted before this milestone is refused by it.

    An ADDRESSED document must exist and belong to this project, and its own `storage_path`
    is the source — `projects.uploaded_file_path` is NOT consulted on that path. An
    addressed document that names no row of this project is `CONTINUATION_SOURCE_MISMATCH`:
    the caller is not pointing at a document this project read.

    An OMITTED `document_id` is the project-level source, which resolves as it always did,
    and reads nothing. This function makes a database read ONLY for a request that
    addressed a document; the module header states why the omitted case stays exactly as
    it was rather than being checked against the document table too. An omitted request on
    a project that holds MORE THAN ONE document is still refused — by the operation that
    was about to read it, at the same code and status on every route: `_source_lineage`
    for a continuation or a retry, and `plan_first_window` (`app/pipeline.py`, J63A) for
    the first window. This function is not that guard and does not become it.
    """
    if document_id is None:
        # The pre-J63 request, resolved from the project row the caller already holds.
        # `projects.uploaded_file_path` is the source, exactly as it was before a project
        # could hold more than one document — and, deliberately, no document is read.
        store = repository if repository is not None else repo
        if project is None:
            project = store.get_project(project_id)
        if project is None:
            raise ContinuationRefused(
                CONTINUATION_SOURCE_MISMATCH,
                f"there is no project {project_id!r} whose source could be resolved",
            )
        return SourceDocument(
            project_id=project_id,
            storage_path=project.get("uploaded_file_path") or "",
        )

    store = repository if repository is not None else repo
    if project is None:
        project = store.get_project(project_id)
    if project is None:
        raise ContinuationRefused(
            CONTINUATION_SOURCE_MISMATCH,
            f"there is no project {project_id!r} whose source document could be addressed",
        )

    # The addressed document, looked for among THIS project's own documents. There is no
    # lookup by bare id anywhere in this module: a document of another project cannot be
    # reached, so the cross-project case is structurally impossible rather than checked.
    documents = store.project_documents_for_project(project_id)
    addressed = [row for row in documents if row.get("id") == document_id]
    if not addressed:
        raise ContinuationRefused(
            CONTINUATION_SOURCE_MISMATCH,
            f"no source document {document_id!r} belongs to project {project_id!r}; a "
            f"document is addressed by the id of one of this project's own "
            f"project_documents rows, and this project keeps {len(documents)}",
        )
    document = addressed[0]
    return SourceDocument(
        project_id=project_id,
        storage_path=document.get("storage_path") or "",
        document_id=document.get("id"),
    )
