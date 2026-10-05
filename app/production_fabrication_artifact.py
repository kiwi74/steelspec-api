"""
J45 — THE FABRICATION ARTIFACT: LOCAL WORKING DIRECTORY AND DURABLE STORAGE.

WHY THIS MODULE EXISTS
----------------------
A connection a human has reviewed eventually has to become a fabricated part,
and the drawing that says how is a PDF produced LOCALLY by the CAD engine,
verified LOCALLY against the reviewed record, and then kept somewhere that
outlives the process that made it. J43 designed that sequence; J44 built the
liveness device that stops two reviewers racing over it. Neither built the
storage half. The sequence they designed is:

    generate  ->  verify  ->  UPLOAD  ->  J22 snapshot  ->  projects pointer  ->  release

This module is the UPLOAD step, plus the local directory the generation step
needs before it. It is deliberately the smallest thing that can carry them.

IT IS NOT THE PRODUCTION ROUTE
------------------------------
Nothing here is reachable from HTTP. Nothing here authenticates a caller,
resolves a connection, records a review, acquires or releases a claim, reads a
project row, generates a drawing or calls a model. The route that will compose
this step with the others is a later milestone's; until it exists this module is
imported by path, and it is a module BESIDE the review package rather than a
module inside it — see WHY IT IS NOT IN THE REVIEW PACKAGE below.

THE FOUR THINGS IT OWNS
-----------------------
1. THE WORKING DIRECTORY. One configured location, `ARTIFACT_WORKING_DIR`, with
   no default. A fabricated drawing is a deliverable; writing it into whatever
   directory the process happened to start in, or into a shared /tmp, is how one
   project's drawing ends up beside another's. Unset is a refusal, not a guess.

2. THE WORKSPACE. `<working dir>/<project_id>/<connection_id>/` — derived from
   server-controlled identifiers, never from a path a caller supplies, so the
   two ids that name a connection are also the two ids that isolate its files.
   The resolved workspace is asserted to still be inside the working directory,
   which is the check that makes the segment rule below load-bearing rather
   than decorative.

3. THE DURABLE OBJECT PATH. `<user_id>/<project_id>/<connection_id>/fabrication.pdf`
   — deterministic, so re-running the same connection overwrites its own object
   and never produces a second one, and identical to the namespace the browser
   policies on `storage.objects` already require (`foldername(name)[1]` must
   equal the owner's auth id). There is no timestamp in it, no reviewer in it,
   and no caller-supplied part anywhere in it.

4. THE UPLOAD. One object, one bucket, `application/pdf`, upsert. It runs only
   for an artifact whose verification status is `VERIFIED`. Anything else is
   refused BEFORE the storage call, so a drawing that failed its own
   verification is never uploaded and can never be pointed at by
   `projects.fab_drawings_pdf_path`.

WHAT IT REFUSES, AND WHY REFUSING IS THE FEATURE
------------------------------------------------
Every refusal below is a value this module will not derive a path from or write
to storage with. They are named codes rather than bare exceptions so a caller
can tell them apart without parsing a message:

    ARTIFACT_REFUSED_NO_WORKING_DIR          nothing is configured
    ARTIFACT_REFUSED_RELATIVE_WORKING_DIR    configured, but not absolute
    ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR    not a directory, not creatable, not writable
    ARTIFACT_REFUSED_UNSAFE_IDENTIFIER       an id that is not one safe path segment
    ARTIFACT_REFUSED_NOT_VERIFIED            not VERIFIED — may never be uploaded
    ARTIFACT_REFUSED_NOT_A_PDF               content that is not a PDF

THERE IS NO FAILED-ARTIFACT STORAGE. No bucket, no path and no constant in this
module represents a failed drawing, because a failed drawing is not a
deliverable: it is a result the caller reports. A module that could file one
durably would invite a pointer to it.

WHAT A REFUSAL IS NOT
---------------------
These are not authentication. `user_id` is passed in by the caller and this
module asserts only its SHAPE. Establishing that the caller really is that user,
and that they own that project, is J19's, and this module deliberately contains
no copy of it — the same division J44 drew around its claim.

WHY IT IS NOT IN THE REVIEW PACKAGE
-----------------------------------
J19 pinned `app/production_review/` as a package that reaches no model, no
drawing and no network, and a test enforces it by rejecting the very tokens an
upload needs. A storage layer inside that package would break the boundary that
makes the package safe to import. So this module sits beside it, as the other
two production-layer modules do, and the package is left exactly as J19 built it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# --- The durable bucket. It is created private and NO `storage.objects` policy
# --- names it, so it is unreachable by the browser roles: every policy that
# --- exists on that table is scoped by an explicit `bucket_id = '<name>'`
# --- predicate, and a bucket no policy names admits nobody but the service role,
# --- which bypasses row-level security entirely.
FABRICATION_BUCKET = "fabrication-drawings"

# --- The object's name, used for both halves: the generated PDF in the workspace
# --- and the uploaded PDF in the bucket. One name, so the artifact does not
# --- change identity on the way out.
FABRICATION_OBJECT_NAME = "fabrication.pdf"

# --- Explicit, not sniffed. The bucket is created with `application/pdf` as its
# --- only allowed type, so this value is enforced twice: here and by Storage.
FABRICATION_CONTENT_TYPE = "application/pdf"

# --- The environment variable (and `app.config` field) the working directory is
# --- configured by. Read at USE time, never at import: `app.config` does
# --- `os.environ["SUPABASE_URL"]` at import, so importing it here would make this
# --- module unimportable in every environment that has no credentials.
WORKING_DIR_ENV_VAR = "ARTIFACT_WORKING_DIR"

# --- The ONLY verification status that may be uploaded. Written here rather than
# --- imported because its authority (`app/cad_engine/drawing_output_verification.py`)
# --- pulls in pypdf and the whole CAD engine, which this layer must not depend on.
# --- There is still one source of truth: a test pins this constant EQUAL to that
# --- module's VERIFICATION_STATUS_VERIFIED, so the two cannot drift apart silently.
VERIFIED_STATUS = "VERIFIED"

# --- A PDF begins with this. Checked as a prefix, not parsed: parsing a drawing is
# --- the verification layer's job and already has an owner, and this check exists
# --- only to stop non-PDF bytes reaching a bucket that accepts nothing else.
PDF_MAGIC = b"%PDF-"

# --- The most a single path segment may be. Real identifiers here are well inside
# --- it (the project and user ids are UUIDs); the bound exists so an
# --- unbounded string cannot become an unbounded path.
MAX_SEGMENT_LENGTH = 200

ARTIFACT_REFUSED_NO_WORKING_DIR = "ARTIFACT_REFUSED_NO_WORKING_DIR"
ARTIFACT_REFUSED_RELATIVE_WORKING_DIR = "ARTIFACT_REFUSED_RELATIVE_WORKING_DIR"
ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR = "ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR"
ARTIFACT_REFUSED_UNSAFE_IDENTIFIER = "ARTIFACT_REFUSED_UNSAFE_IDENTIFIER"
ARTIFACT_REFUSED_NOT_VERIFIED = "ARTIFACT_REFUSED_NOT_VERIFIED"
ARTIFACT_REFUSED_NOT_A_PDF = "ARTIFACT_REFUSED_NOT_A_PDF"

# --- The project and the owning user are `uuid NOT NULL` columns on `projects`, so
# --- a canonical lower-case UUID is what a correct caller passes and what an
# --- injected one would have to forge. A UUID cannot contain a separator or a
# --- dot-segment, which is why this rule needs no traversal clause of its own.
_UUID = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)

# --- The connection identifier is validated as ONE SAFE SEGMENT rather than as a
# --- UUID, and the difference is deliberate. In this schema the identifier is a
# --- UUID where it is a `uuid` column and `text` where it is not; a rule that
# --- demanded UUID shape would be wrong for the second case, and a rule that
# --- accepted anything but a single safe segment would be wrong for both. So the
# --- rule states exactly what this layer can guarantee: one segment, leading
# --- alphanumeric (which is what excludes "." and ".." for free), and drawn from
# --- an allowlist that contains no separator of either kind.
_SEGMENT = re.compile(
    r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,%d}\Z" % (MAX_SEGMENT_LENGTH - 1)
)


class ArtifactRefused(ValueError):
    """A path or an upload was refused before anything was written.

    Carries the named `code` so a caller can branch on it, and a `statement`
    naming the rule that refused — the same shape J44's `ClaimRefused` uses, so
    the two refusals a fabrication review can meet read alike.
    """

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


def _describe(value: object) -> str:
    """A short, safe rendering of a rejected value, for a refusal message.

    Truncated and stripped to printable ASCII, so a value carrying a newline or a
    terminal escape cannot rewrite the message it appears in — a refusal is
    logged, and a refusal that can forge log lines is worse than no message.
    """
    if not isinstance(value, str):
        return f"a {type(value).__name__}"
    shown = "".join(char if 32 <= ord(char) < 127 else "?" for char in value[:64])
    return repr(shown) + ("..." if len(value) > 64 else "")


def _owner_id(value: object, name: str) -> str:
    """Return `value` if it is a canonical UUID, else refuse."""
    if not isinstance(value, str) or not _UUID.match(value):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNSAFE_IDENTIFIER,
            f"{name} must be a canonical lower-case UUID; got {_describe(value)}",
        )
    return value


def _connection_segment(value: object) -> str:
    """Return `value` if it is one safe path segment, else refuse."""
    if not isinstance(value, str) or not _SEGMENT.match(value):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNSAFE_IDENTIFIER,
            f"connection_id must be a single safe path segment "
            f"(no '/', no '\\\\', no '..', at most {MAX_SEGMENT_LENGTH} characters, "
            f"starting with a letter or a digit); got {_describe(value)}",
        )
    return value


def working_directory(working_dir: object = None) -> Path:
    """The configured artifact working directory, created if it does not exist.

    Parameters
    ----------
    working_dir
        Overrides `app.config.ARTIFACT_WORKING_DIR`. Injected by tests; production
        callers pass nothing, and the value is then read from the environment at
        this moment rather than at import.

    Returns
    -------
    pathlib.Path
        An absolute, existing, writable directory.

    Raises
    ------
    ArtifactRefused
        `ARTIFACT_REFUSED_NO_WORKING_DIR` if nothing is configured — there is no
        default and no fallback; `ARTIFACT_REFUSED_RELATIVE_WORKING_DIR` if the
        configured value is not absolute; `ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR`
        if it exists as something other than a directory, cannot be created, or
        cannot be written to.

    Notes
    -----
    The write test is `os.access`, which reports the permission bits rather than
    whether a write would actually land — a read-only filesystem mounted over a
    writable-looking directory is not detected here. It is a check, not a
    guarantee; the write itself remains the proof, and it fails loudly.
    """
    if working_dir is None:
        from app.config import ARTIFACT_WORKING_DIR as working_dir

    if not isinstance(working_dir, (str, os.PathLike)) or (
        isinstance(working_dir, str) and not working_dir.strip()
    ):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_NO_WORKING_DIR,
            f"no artifact working directory is configured: set {WORKING_DIR_ENV_VAR} "
            f"to an absolute path. There is no default, by design.",
        )

    path = Path(working_dir)
    if not path.is_absolute():
        raise ArtifactRefused(
            ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
            f"the artifact working directory must be absolute; got {_describe(working_dir)}",
        )

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
            f"the artifact working directory {str(path)!r} could not be created: {exc}",
        ) from exc

    if not path.is_dir():
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
            f"the artifact working directory {str(path)!r} exists and is not a directory",
        )
    if not os.access(path, os.W_OK | os.X_OK):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
            f"the artifact working directory {str(path)!r} is not writable by this process",
        )
    return path


def connection_workspace(
    project_id: object, connection_id: object, *, working_dir: object = None
) -> Path:
    """The directory one connection's artifact is built in. Created if absent.

    The workspace is `<working dir>/<project_id>/<connection_id>/`. Both segments
    come from identifiers the caller holds server-side; neither is a path a
    request body could supply. Two connections in one project therefore cannot
    see each other's files, and the same connection always resolves to the same
    directory.

    Parameters
    ----------
    project_id, connection_id
        Server-controlled identifiers. Validated as one safe path segment each —
        see `_owner_id` and `_connection_segment` for why the two rules differ.
    working_dir
        Injected by tests; see `working_directory`.

    Returns
    -------
    pathlib.Path
        The resolved, existing workspace, asserted to lie inside the working
        directory.

    Raises
    ------
    ArtifactRefused
        Any code `working_directory` raises, or
        `ARTIFACT_REFUSED_UNSAFE_IDENTIFIER` if either identifier is not one safe
        segment, or if the resolved workspace escapes the working directory.
    """
    root = working_directory(working_dir)
    project = _owner_id(project_id, "project_id")
    connection = _connection_segment(connection_id)

    workspace = (root / project / connection).resolve()
    # The segment rules above already make an escape impossible; this asserts the
    # property that actually matters, on the resolved path, so a future change to
    # either rule cannot quietly reintroduce one. `root.resolve()` is recomputed
    # because `root` itself may be reached through a symlink.
    if not workspace.is_relative_to(root.resolve()):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNSAFE_IDENTIFIER,
            f"the workspace for project_id={_describe(project_id)} "
            f"connection_id={_describe(connection_id)} resolves outside the "
            f"working directory",
        )

    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def local_artifact_path(
    project_id: object, connection_id: object, *, working_dir: object = None
) -> Path:
    """Where in the workspace the generated PDF is written and verified.

    A name, not a write: nothing is created here but the workspace directory
    itself. The milestones that generate and verify the drawing read this path
    rather than composing one, so the artifact has exactly one local identity.
    """
    return connection_workspace(project_id, connection_id, working_dir=working_dir) / (
        FABRICATION_OBJECT_NAME
    )


def durable_object_path(user_id: object, project_id: object, connection_id: object) -> str:
    """The object path a verified artifact is stored at, and only that. Pure.

    `<user_id>/<project_id>/<connection_id>/fabrication.pdf`, from identifiers the
    server holds. Deterministic: the same connection always yields the same
    string, which is what makes re-uploading an overwrite of the artifact's own
    object rather than the creation of a second one. It contains no timestamp and
    no reviewer identity, so two reviewers of the same connection cannot produce
    two objects.

    The first segment is the owner's user id, which is not decoration: the
    deployed policies on `storage.objects` require exactly that — each is scoped
    to a bucket and to `foldername(name)[1] = auth.uid()` — so an object stored
    under this shape is one the owning browser session may read, and no other's.

    This function touches neither the filesystem nor the network.
    """
    owner = _owner_id(user_id, "user_id")
    project = _owner_id(project_id, "project_id")
    connection = _connection_segment(connection_id)
    return f"{owner}/{project}/{connection}/{FABRICATION_OBJECT_NAME}"


def upload_verified_artifact(
    *,
    user_id: object,
    project_id: object,
    connection_id: object,
    content: object,
    verification_status: object,
    client: Any = None,
) -> str:
    """Upload ONE verified PDF and return the path it was stored at.

    The bucket and the path are the module's, never the caller's: there is no
    parameter for either, and no caller-supplied string reaches the path.

    Parameters
    ----------
    user_id, project_id, connection_id
        Server-controlled. `user_id` is the project's owner, established by the
        caller's own authorization step — this module asserts its shape and
        nothing else.
    content
        The artifact's bytes. Not a path: this layer never opens a file the caller
        named, so no request body can steer a read.
    verification_status
        Required, and must be exactly `VERIFIED_STATUS`. There is no default, so a
        caller cannot omit it and have a drawing uploaded by omission.
    client
        The application's Supabase client. Defaults to
        `app.supabase_client.supabase`, imported lazily so this module can be
        imported without credentials. The client's own storage session is reused
        — this module creates no second client and holds no credential.

    Returns
    -------
    str
        The durable object path. Returned only after the upload call returned.

    Raises
    ------
    ArtifactRefused
        `ARTIFACT_REFUSED_NOT_VERIFIED` if the status is anything but `VERIFIED` —
        raised BEFORE the storage call, so an unverified drawing is never
        uploaded; `ARTIFACT_REFUSED_NOT_A_PDF` if the bytes are not a PDF or are
        not bytes; or `ARTIFACT_REFUSED_UNSAFE_IDENTIFIER` for an identifier.
    storage3.exceptions.StorageApiError
        The storage API answered a non-2xx. Propagated unchanged: this module
        does not retry, does not translate, and never returns a path for an
        upload that did not succeed.

    Notes
    -----
    Success is the upload call returning without raising — the same convention
    `app.export.storage_export.upload_and_record` already uses, and the reason no
    read-back follows: a second call could only fail in new ways while telling us
    what the first call's status code already said.

    `upsert` is on. Re-uploading a connection overwrites its own object rather
    than erroring, which is what the deterministic path is for.
    """
    owner = _owner_id(user_id, "user_id")
    project = _owner_id(project_id, "project_id")
    connection = _connection_segment(connection_id)

    if verification_status != VERIFIED_STATUS:
        raise ArtifactRefused(
            ARTIFACT_REFUSED_NOT_VERIFIED,
            f"only a {VERIFIED_STATUS} artifact may be uploaded; got "
            f"{_describe(verification_status)}. A drawing that did not verify is "
            f"reported to the caller, never stored.",
        )
    if not isinstance(content, (bytes, bytearray)):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_NOT_A_PDF,
            f"the artifact must be PDF bytes beginning {PDF_MAGIC!r}; got "
            f"{_describe(content)}. This layer uploads bytes, never a path.",
        )
    if not bytes(content).startswith(PDF_MAGIC):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_NOT_A_PDF,
            f"the artifact must begin {PDF_MAGIC!r}; got {len(bytes(content))} bytes "
            f"beginning {_describe(bytes(content)[:8].decode('latin-1'))}",
        )

    path = durable_object_path(owner, project, connection)
    if client is None:  # pragma: no cover - production path; tests inject a client
        from app.supabase_client import supabase as client

    client.storage.from_(FABRICATION_BUCKET).upload(
        path,
        bytes(content),
        file_options={"content-type": FABRICATION_CONTENT_TYPE, "upsert": "true"},
    )
    return path


__all__ = [
    "ARTIFACT_REFUSED_NOT_A_PDF",
    "ARTIFACT_REFUSED_NOT_VERIFIED",
    "ARTIFACT_REFUSED_NO_WORKING_DIR",
    "ARTIFACT_REFUSED_RELATIVE_WORKING_DIR",
    "ARTIFACT_REFUSED_UNSAFE_IDENTIFIER",
    "ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR",
    "ArtifactRefused",
    "FABRICATION_BUCKET",
    "FABRICATION_CONTENT_TYPE",
    "FABRICATION_OBJECT_NAME",
    "MAX_SEGMENT_LENGTH",
    "PDF_MAGIC",
    "VERIFIED_STATUS",
    "WORKING_DIR_ENV_VAR",
    "connection_workspace",
    "durable_object_path",
    "local_artifact_path",
    "upload_verified_artifact",
    "working_directory",
]
