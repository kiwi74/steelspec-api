"""
J47 — THE FABRICATION POINTER: ONE COLUMN, WRITTEN LAST.

WHY THIS MODULE EXISTS
----------------------
J45 stored a verified fabrication drawing durably and named the column a project
would eventually point at it with — `projects.fab_drawings_pdf_path` — but wrote
nothing there, and said so: "Wiring the pointer is a later milestone's step."
J47 is that milestone, so the step needs something to perform it, and this module
is deliberately the smallest thing that can.

THE SEQUENCE THIS SITS AT THE END OF
------------------------------------
J45's own docstring states it: `generate -> verify -> UPLOAD -> J22 snapshot ->
projects pointer -> release`. The pointer is LAST among the writes, because it is
the one write that is safe to lose: a revision that was recorded and an artifact
that was stored are each independently true, whereas a pointer with nothing
behind it is a claim about a deliverable that does not exist. So this module is
only ever reached after the upload returned and after J22 accepted the revision.

WHAT IT OWNS, AND WHAT IT REFUSES TO OWN
----------------------------------------
It owns exactly one mutation: setting `fab_drawings_pdf_path` on one project row.

It does NOT upload, does NOT generate, does NOT verify, and creates no second
artifact-storage path. There is no bucket name, no path composition and no
storage client anywhere in this module — `upload_verified_artifact` remains the
only thing in this application that writes an artifact to Storage, and this
module cannot become a second one even by accident, because it has nothing to
write with.

IT WRITES THE DURABLE IDENTITY, AND ONLY THE DURABLE IDENTITY
-------------------------------------------------------------
The value stored is J45's durable Storage object identity,
`<user_id>/<project_id>/<connection_id>/fabrication.pdf`, and nothing else. The
caller supplies both the identifiers and the path, and the path must EQUAL what
`app.production_fabrication_artifact.durable_object_path` derives from those
identifiers. That equality is the whole safety property, and it is what makes a
local filesystem path impossible to store rather than merely discouraged: a
generated file lives under a configured working directory as
`<connection_id>-fabrication.pdf` and can never equal the durable identity, so a
caller that read the pointer off the artifact the generator wrote — the mistake
this rule exists to prevent — is refused instead of recorded. The stored value is
re-derived here, never taken from the caller, so the column cannot contain a
string this module did not compute.

WHY IT DOES NOT CALL `update_project_summary`
---------------------------------------------
`app.engineering_data.repository.update_project_summary(project_id, **fields)`
was inspected first, as J47's brief required, and it cannot express the safety
invariant this module needs, for three separate reasons:

  * it returns `None`, discarding the response. PostgREST answers an UPDATE with
    the rows it changed (`return=representation` is this client's default), and
    an update whose filter matched nothing answers with an EMPTY list and no
    error at all. So the generic writer cannot tell a write that landed from one
    that matched no project row — and that distinction is the whole of failing
    closed here. Discarding it means a pointer could be reported as recorded
    against a project that does not exist.
  * it takes arbitrary field names, so it cannot state "this column and no
    other" — the property that makes a pointer writer a pointer writer.
  * it takes no client, so its write cannot be exercised without a live database,
    which is why this module's own tests could not otherwise prove a write
    happened.

This module is therefore that "smallest dedicated writer": one column named as a
constant, one row addressed by project id, one checked response, one injectable
client. It adds no query the generic writer did not already perform, and it
leaves that writer's own contract untouched.

WHAT IT IS NOT
--------------
Not authorization. `project_id` and `user_id` are passed in by the caller and
this module validates only their SHAPE — that each is a canonical UUID, which
`durable_object_path` enforces. Establishing that the caller may write to that
project is J19's, and this module holds no copy of it: the route reaches it only
after `authorize_project` allowed the request.

Not a correctness barrier. J22's recorded revision is the review's own truth. A
failed pointer write is reported as a partial outcome and is never repaired by
rewriting the revision, because the revision was already correct.
"""
from __future__ import annotations

from typing import Any

from app.production_fabrication_artifact import durable_object_path

# --- The one column, and the one table, named once so no caller has to remember
# --- them and no caller can substitute another pair.
POINTER_COLUMN = "fab_drawings_pdf_path"
POINTER_TABLE = "projects"
POINTER_ID_COLUMN = "id"

#: The supplied durable identity is not this connection's durable identity. The
#: only refusal this module states itself: every other failure it can meet is
#: J45's about an identifier's shape, and is raised as J45 raises it.
POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY = "POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY"
POINTER_REFUSED_PROJECT_UNKNOWN = "POINTER_REFUSED_PROJECT_UNKNOWN"

POINTER_REFUSALS = (
    POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY,
    POINTER_REFUSED_PROJECT_UNKNOWN,
)


class PointerRefused(ValueError):
    """The project row was not pointed at the artifact, for the stated reason.

    Carries the named `code` so a caller can branch on it without parsing a
    message — the same shape J44's `ClaimRefused` and J45's `ArtifactRefused`
    use, so the three refusals a fabrication review can meet read alike.

    A `PointerRefused` always means the column is UNCHANGED. The update is one
    statement: it either ran or it did not, and nothing here is partially
    applied.
    """

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


def _client(client):
    """The store to call: the caller's, or the production one, imported on use.

    Importing the production client at module import time would put a database
    connection behind every import of this module, including the ones that never
    write a pointer.
    """
    if client is not None:
        return client
    from app.supabase_client import supabase

    return supabase


def _rows(response):
    """The rows the update returned, whichever way the client wrapped them."""
    data = getattr(response, "data", response)
    if data is None:
        return ()
    if isinstance(data, (list, tuple)):
        return tuple(data)
    return (data,)


def record_fabrication_pointer(
    *,
    user_id: object,
    project_id: object,
    connection_id: object,
    durable_path: object,
    client: Any = None,
) -> str:
    """Points ONE project row at ONE connection's durably stored drawing.

    Parameters
    ----------
    user_id, project_id, connection_id
        Server-controlled identifiers. `user_id` is the project's owner, which
        the route has already established — this module asserts their shape and
        nothing else.
    durable_path
        The identity that was uploaded, as `upload_verified_artifact` returned
        it. Required, and required to EQUAL what `durable_object_path` derives
        from the identifiers above; there is no default, so a caller cannot omit
        it and have a pointer written by omission.
    client
        The application's Supabase client. Defaults to
        `app.supabase_client.supabase`, imported lazily.

    Returns
    -------
    str
        The durable identity that was stored. Returned only after the update
        call returned — success is that call not raising, the same convention
        J45's upload and this application's other writes already use.

    Raises
    ------
    app.production_fabrication_artifact.ArtifactRefused
        An identifier that is not one safe path segment, or a project or owner
        id that is not a canonical UUID — J45's own rules, raised as J45 raises
        them rather than restated here.
    PointerRefused
        `POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY` when `durable_path` is not the
        identity those identifiers derive — this is the refusal a local generated
        filename meets, and it happens BEFORE the update; or
        `POINTER_REFUSED_PROJECT_UNKNOWN` when the update matched no project row,
        in which case nothing was written anywhere.
    """
    # J45's own derivation, and the only value this module will store. A malformed
    # identifier refuses here, before anything else is decided.
    expected = durable_object_path(user_id, project_id, connection_id)

    if not isinstance(durable_path, str) or durable_path != expected:
        raise PointerRefused(
            POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY,
            f"the value offered for {POINTER_COLUMN} is not this connection's durable "
            f"Storage identity. A pointer records where the artifact was STORED; a path "
            f"the generator wrote is where it was BUILT, and the two are never the same "
            f"string",
        )

    response = _client(client).table(POINTER_TABLE).update(
        {POINTER_COLUMN: expected}
    ).eq(POINTER_ID_COLUMN, project_id).execute()

    if not _rows(response):
        raise PointerRefused(
            POINTER_REFUSED_PROJECT_UNKNOWN,
            f"no {POINTER_TABLE} row was updated for {project_id!r}, so no pointer was "
            "recorded; an update that matched nothing is not a pointer that was written",
        )
    return expected


__all__ = [
    "POINTER_COLUMN",
    "POINTER_ID_COLUMN",
    "POINTER_REFUSALS",
    "POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY",
    "POINTER_REFUSED_PROJECT_UNKNOWN",
    "POINTER_TABLE",
    "PointerRefused",
    "record_fabrication_pointer",
]
