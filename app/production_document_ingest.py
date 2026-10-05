"""
E2E-001N — THE PRODUCTION DOCUMENT INGESTION PATH.

WHAT THIS MODULE IS

The missing half of a capability the architecture was already built for. J61 gave a project
many documents, J63 taught `/extract` to address one of them, and `plan_first_window`
already REFUSES an unaddressed request once a project holds two — but nothing could create
the second document. A `project_documents` row is created by exactly one caller,
`parse_pdf_and_save`, from the single `projects.uploaded_file_path`, and the browser cannot
create one at all: `project_documents` is service-role only, by design, and answers 401 to a
client. So a project could hold many documents in principle and exactly one in practice.

This module is the ingest. It takes an AUTHORIZED project and the bytes of one or more
files, stores each under the project's existing prefix in the existing `uploads` bucket, and
registers each as a `project_documents` row through `create_project_document` — the same
function the pipeline uses, so content identity and its deduplication rule are the existing
ones and not a second account of them.

WHAT IT DOES NOT DO

It creates no bucket, no policy, no table and no second document authority. It does not
extract — `POST /extract/{project_id}` remains the only thing that reads a document, and it
is addressed per document by the caller. It does not write `projects.uploaded_file_path`, and
it does not read it: once a project holds more than one document, that column is not the
source, it is a choice among sources, and this module does not make choices on a caller's
behalf. It does not name or rename the project.

WHAT IT WILL NOT DO, DELIBERATELY

It will not overwrite. A storage object is the source a document row's `storage_path` points
at, so replacing one would silently change what an already-registered document claims to be.
A file whose NAME is already taken on the project is therefore refused with a stated code,
and the storage upload itself is issued with `upsert` off.

It also performs no rollback. There is no transaction across Storage and Postgres, and
inventing one would be a lie about what happened. The failure model is per file and explicit:
each file is attempted, each outcome is reported, and a file that failed leaves at most an
inert storage object — inert because extraction reads a document by its registered identity,
and an object no document row points at is never read.
"""
from __future__ import annotations

import posixpath
from typing import Any, Callable, Iterable

__all__ = [
    "ACCEPTED_EXTENSIONS",
    "INGEST_REFUSED_EMPTY_FILE",
    "INGEST_REFUSED_NAME_TAKEN",
    "INGEST_REFUSED_UNSAFE_NAME",
    "INGEST_REFUSED_UNSUPPORTED_FORMAT",
    "IngestRefused",
    "UPLOAD_BUCKET",
    "ingest_documents",
    "upload_object_path",
]

#: The one bucket this module writes to. It is the bucket the upload flow has always used;
#: no second bucket exists and none is created here.
UPLOAD_BUCKET = "uploads"

#: The formats the backend can read, and the only two this path accepts. It matches the
#: frontend's own picker list, and it is stated here rather than inferred from the filename
#: so that an unsupported file is refused BEFORE anything is stored.
ACCEPTED_EXTENSIONS = (".pdf", ".dxf")

_SOURCE_FORMAT = {".pdf": "PDF", ".dxf": "DXF"}
_CONTENT_TYPE = {".pdf": "application/pdf", ".dxf": "application/dxf"}

INGEST_REFUSED_UNSUPPORTED_FORMAT = "INGEST_REFUSED_UNSUPPORTED_FORMAT"
INGEST_REFUSED_EMPTY_FILE = "INGEST_REFUSED_EMPTY_FILE"
INGEST_REFUSED_UNSAFE_NAME = "INGEST_REFUSED_UNSAFE_NAME"
INGEST_REFUSED_NAME_TAKEN = "INGEST_REFUSED_NAME_TAKEN"


class IngestRefused(Exception):
    """One file this path will not store, named by its own code and reason.

    A refusal is about the FILE, never about the project: the caller's authorization was
    settled before this module was reached, and nothing here re-asks that question.
    """

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(statement)
        self.code = code
        self.statement = statement


def _extension(file_name: str) -> str:
    lowered = file_name.lower()
    for extension in ACCEPTED_EXTENSIONS:
        if lowered.endswith(extension):
            return extension
    return ""


def _checked_name(file_name: object) -> str:
    """The file's own name as ONE safe path segment, or a refusal.

    The name reaches a storage path, so it is admitted only as a basename: no directory
    part, no traversal, no separator of either kind, nothing blank. It is not rewritten or
    sanitised into something else, because a source document stored under a name the user
    did not choose is a document the user cannot recognise.
    """
    if not isinstance(file_name, str) or not file_name.strip():
        raise IngestRefused(
            INGEST_REFUSED_UNSAFE_NAME, "the file has no usable name"
        )
    name = file_name.strip()
    if (
        name != posixpath.basename(name)
        or "/" in name
        or "\\" in name
        or name in (".", "..")
        or "\x00" in name
    ):
        raise IngestRefused(
            INGEST_REFUSED_UNSAFE_NAME,
            f"{name!r} is not a single file name; a document is stored by its own name",
        )
    return name


def upload_object_path(owner_id: str, project_id: str, file_name: str) -> str:
    """Where one source file is stored: `<owner>/<project>/<name>`.

    Composed from identifiers the SERVER holds — the owner established by authorization and
    the project from the URL — and never from anything a request body supplies. The first
    segment is the owner's id because the deployed storage policies require exactly that
    (`foldername(name)[1]` must equal the caller's auth id), which is what makes the object
    one the owning session may read and no other's.
    """
    return f"{owner_id}/{project_id}/{file_name}"


def _default_storage() -> Any:
    from app.supabase_client import supabase

    return supabase


def _default_store() -> Any:
    # Imported at USE time, not at import: `app.engineering_data.repository` reaches
    # `app.config`, which reads its environment at import, so a module-level import here
    # would make this module unimportable in every environment without credentials — the
    # same reason the artifact layer imports its client lazily.
    from app.engineering_data import repository as repo

    return repo


def ingest_documents(
    *,
    owner_id: str,
    project_id: str,
    files: Iterable[tuple[str, bytes]],
    storage: Any = None,
    store: Any = None,
    hash_rule: Callable[[Any], str] | None = None,
) -> list[dict]:
    """Ingest each file as a document of this project, reporting every outcome.

    `files` is `(file_name, content)` pairs — bytes, never a path, so this layer never opens
    a file a caller named. The caller is already authenticated and the project already
    authorized; this function asks no identity question of its own.

    Returns one outcome per file, IN THE ORDER GIVEN, each of:

        {"file_name", "status": "created",      "document_id", "storage_path"}
        {"file_name", "status": "deduplicated", "document_id", "storage_path"}
        {"file_name", "status": "failed",       "reason", "code"}

    `"deduplicated"` means these exact bytes are already a document of this project: the
    EXISTING row is returned, nothing is inserted and nothing is uploaded. Identity is
    content, so the same file under a second name is the same document, and the document's
    own name does not change to whichever name arrived last.

    A `"failed"` outcome carries the refusal's own code and a statement that never contains
    exception text, a storage URL, a credential or a row value.
    """
    storage = storage if storage is not None else _default_storage()
    store = store if store is not None else _default_store()
    if hash_rule is None:
        # The ONE hashing rule, taken from the neutral module that owns it rather than from
        # the annotation extractor. Ingestion has nothing to do with annotations, and
        # reaching into that layer for a hash is the coupling J28 exists to refuse.
        from app.drawing_reading.source_document import source_document_sha256_of_bytes

        hash_rule = source_document_sha256_of_bytes

    existing = store.project_documents_for_project(project_id)
    hash_to_document = {
        document.get("content_sha256"): document
        for document in existing
        if document.get("content_sha256")
    }
    names_taken = {
        document.get("file_name") for document in existing if document.get("file_name")
    }

    outcomes: list[dict] = []
    for raw_name, content in files:
        try:
            file_name = _checked_name(raw_name)
            extension = _extension(file_name)
            if not extension:
                raise IngestRefused(
                    INGEST_REFUSED_UNSUPPORTED_FORMAT,
                    f"{file_name!r} is not a {', '.join(ACCEPTED_EXTENSIONS)} file",
                )
            if not isinstance(content, (bytes, bytearray)) or not bytes(content):
                raise IngestRefused(
                    INGEST_REFUSED_EMPTY_FILE, f"{file_name!r} is empty"
                )
            payload = bytes(content)
            content_sha256 = hash_rule(payload)

            already = hash_to_document.get(content_sha256)
            if already is not None:
                outcomes.append(
                    {
                        "file_name": already.get("file_name") or file_name,
                        "status": "deduplicated",
                        "document_id": already.get("id"),
                        "storage_path": already.get("storage_path"),
                    }
                )
                continue

            if file_name in names_taken:
                raise IngestRefused(
                    INGEST_REFUSED_NAME_TAKEN,
                    f"a file named {file_name!r} is already stored on this project; "
                    "SteelSpec does not overwrite a source document",
                )

            storage_path = upload_object_path(owner_id, project_id, file_name)
            try:
                storage.storage.from_(UPLOAD_BUCKET).upload(
                    storage_path,
                    payload,
                    {"content_type": _CONTENT_TYPE[extension], "upsert": False},
                )
            except Exception:
                outcomes.append(
                    {
                        "file_name": file_name,
                        "status": "failed",
                        "code": "INGEST_FAILED_STORAGE",
                        "reason": f"{file_name} could not be stored. Nothing was registered for it.",
                    }
                )
                continue

            document = store.create_project_document(
                project_id,
                storage_path=storage_path,
                file_name=file_name,
                source_format=_SOURCE_FORMAT[extension],
                byte_size=len(payload),
                page_count=None,
                content_sha256=content_sha256,
            )
            names_taken.add(file_name)
            hash_to_document[content_sha256] = document
            outcomes.append(
                {
                    "file_name": file_name,
                    "status": "created",
                    "document_id": document.get("id"),
                    "storage_path": document.get("storage_path"),
                }
            )
        except IngestRefused as refused:
            outcomes.append(
                {
                    "file_name": raw_name if isinstance(raw_name, str) else "",
                    "status": "failed",
                    "code": refused.code,
                    "reason": refused.statement,
                }
            )
    return outcomes
