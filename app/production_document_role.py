"""
J64 — EXPLICIT PROJECT DOCUMENT ROLE ASSERTION.

THE ONE QUESTION THIS MODULE ANSWERS
------------------------------------
A project may hold several source documents (J61), a caller may say WHICH of them a run is
about (J63), and a project whose source is ambiguous is refused rather than chosen for
(J63A). None of that says what a document IS. This module lets a human say it:

    PROJECT
      └── PROJECT_DOCUMENT  ← role: one asserted value, or UNKNOWN

The role is an ASSERTION. It is not inferred from a filename, a file extension, a page
count, a page's contents, a drawing number, a storage path, a creation time or the order a
project's documents were read in — this module contains no such rule, and none of those
values is read here except the document's own stored role, which is the thing being
replaced. A caller that asserts nothing leaves the role exactly as it was found.

THE VOCABULARY IS THE ONE THE DATABASE ALREADY ENFORCES
-------------------------------------------------------
`project_documents.role` is `text not null default 'UNKNOWN'`, guarded by a CHECK
constraint that names a closed list, and that list and this module's are the same list:
`repository.DOCUMENT_ROLES`, read here rather than restated, so the code that accepts a
role and the schema that stores one cannot disagree.

    TRANSMITTAL  STRUCTURAL_GA  ASSEMBLY  FABRICATION  ISOMETRIC
    DETAIL  SCHEDULE  SPECIFICATION  ARCHITECTURAL  UNKNOWN

J64 adds NO value to it, NO second column and NO migration. Four role names that J64's
brief lists — `STRUCTURAL_DETAIL`, `ENGINEERING_SCHEDULE`, `GENERAL_NOTES` and `OTHER` —
are deliberately NOT accepted, because nothing in this schema, this repository or this
codebase has ever held them: they are refused as invalid, exactly as any other
out-of-vocabulary string is, and adding them is a schema decision for a later milestone
rather than something this one does quietly. Every role the milestone's own reference
package needs — a transmittal, GAs, assembly drawings, fabrication drawings and isometrics
— is already a member of the list above.

WHAT AN ASSERTION IS SCOPED TO
------------------------------
`project_id` + `document_id`, and the addressee must be one of THAT PROJECT's own
documents. The addressed id is looked for AMONG the project's documents, exactly as J63
resolves an addressed source, so a document of another project can never be reached — not
even to be refused by name — and there is no lookup by bare id anywhere in this module.
The write itself is filtered by the same pair, so the guard is structural twice over
rather than a check that could be forgotten. Authorization is J19's, applied by the route
before this module is called: the module reads no token and knows no reviewer, and a
project this caller does not own never reaches it.

THE THREE TRANSITIONS, AND WHY THEY ARE ONE OPERATION
-----------------------------------------------------
Replacing an UNKNOWN role with a stated one, replacing a stated role with another, and
stating that a document's role is UNKNOWN are the SAME act: the column holds the value the
caller asserted, and the previous value is not a precondition of anything. UNKNOWN is a
member of the vocabulary like any other — it is what a document carries before anyone has
said otherwise, and a human who states it is asserting that a document's role is not known
rather than asking for a reset. Nothing is refused for having been something else first,
and no revision, supersession, label or history is created by any of the three:
`revision_label` and `supersedes_document_id` are untouched and are not this module's.

A repeated assertion is not a second write. When the document already carries the asserted
role, nothing is written at all and the operation reports `changed = false` — the state the
caller asked for already holds, and a write that would change no value is not performed.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not classify anything. It reads no file, downloads nothing, opens no page, calls no
model, computes no hash, parses no transmittal, reconciles no documents, resolves no
conflict, cites no field, derives no location, and creates no document-set entity. It
changes no `storage_path`, no `content_sha256`, no `drawings.document_id`, no capture, no
analysis run, no evidence row, no review revision and no fabrication pointer.

Nothing in this application SELECTS a document by its role. Extraction, continuation,
retry, reconstruction and opening are all unchanged by this module, and J63A's ambiguity
rule in particular: a project holding a STRUCTURAL_GA document and a FABRICATION document
and no addressed `document_id` is refused for exactly the reason it was refused before,
because a role is a fact a human asserted and not a reason to choose.

THERE IS NO AUDIT TRAIL, AND THAT IS SAID RATHER THAN PAPERED OVER
------------------------------------------------------------------
`project_documents` records `created_at` and nothing else about time; a role assertion
overwrites the column and leaves no row, no timestamp and no record of the value it
replaced. This milestone did not build one, because building an audit mechanism for one
column is a larger decision than asserting a role, and the milestone that makes roles
load-bearing is the one that should make their history durable. What an assertion returns
therefore states the role it REPLACED (`previous_role`) so the caller can see the change it
made, and that is the whole of the record.
"""
from __future__ import annotations

import dataclasses
from typing import Mapping

from app.engineering_data import repository as repo

__all__ = [
    "DOCUMENT_ROLES",
    "DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID",
    "DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING",
    "DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID",
    "DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD",
    "DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD",
    "DOCUMENT_ROLE_INPUT_REFUSALS",
    "DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN",
    "DOCUMENT_ROLE_REFUSED_NOT_RECORDED",
    "DOCUMENT_ROLE_UNKNOWN",
    "REQUEST_FIELDS",
    "ROUTE_PATH",
    "SERVER_OWNED_FIELDS",
    "DocumentRoleAssertion",
    "DocumentRoleInputRefused",
    "DocumentRoleRefused",
    "DocumentRoleRequest",
    "assert_document_role",
    "parse_document_role_request",
]

#: The route this module composes. Declared here rather than only in `app.main` so that
#: "which path asserts a document's role" has one answer in the codebase.
ROUTE_PATH = "/production/review/{project_id}/document-role"

#: The only two keys a role assertion may carry: WHICH document, and WHAT role it is being
#: asserted to have. Both are required — the operation has no meaning without its subject,
#: and a request that names no role asserts nothing — and neither is taken from anywhere
#: else in the request: the project comes from the URL and the reviewer from the token.
REQUEST_FIELDS: tuple[str, ...] = ("document_id", "role")

#: The vocabulary, read from the one place that already declares it. NOT a second list: the
#: schema's CHECK constraint, the repository's own constant and this module are meant to be
#: read together, and a test asserts that the migration's list and this one are equal.
DOCUMENT_ROLES: tuple[str, ...] = repo.DOCUMENT_ROLES

#: What a document's role is until a human says otherwise.
DOCUMENT_ROLE_UNKNOWN: str = repo.DOCUMENT_ROLE_UNKNOWN

#: Everything a client may NOT supply to a role assertion, refused BY NAME so that a client
#: which tries to state one is told which. Each of these is a fact this boundary already
#: owns: it is read from the project row, from the document row the role is being asserted
#: on, or from the identity the request carried — and a caller that could state one could
#: make a record claim a path, a size, an identity or a revision that the persisted facts do
#: not support. `role` is deliberately NOT in this list: it is the one thing this boundary
#: asks for. `document_role` IS, because it is not the name this boundary takes — a caller
#: that spells the field that way is told so rather than silently accommodated.
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
    "file_name",
    "source_format",
    "byte_size",
    "page_count",
    "content_sha256",
    "document",
    "documents",
    "document_role",
    "revision_label",
    "supersedes_document_id",
    "created_at",
    "drawing_id",
    "drawing_set_id",
    "analysis_run_id",
    "status",
)

# ---------------------------------------------------------------- request-input refusal
# One vocabulary per boundary, in J47's/J50's/J63's own shape: a code that names the KIND of
# mistake and a statement of the facts. The codes are the same strings J63's extraction
# boundary uses for the same two shapes ("not an object", "a field this boundary never takes
# from a client") because a client that has learned to read one production refusal has
# learned to read this one.
DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING = "DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING"
DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD = (
    "DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD"
)
DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD = "DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD"
DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID = (
    "DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID"
)
DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID = "DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID"

DOCUMENT_ROLE_INPUT_REFUSALS = (
    DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING,
    DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD,
    DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD,
    DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID,
    DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID,
)

# --------------------------------------------------------------- operation-time refusal
# Two states a role assertion can be answered with, and neither is a mistake in the
# request: the document is not one this project keeps, or the store did not record what it
# was asked to record.
DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN = "DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN"
DOCUMENT_ROLE_REFUSED_NOT_RECORDED = "DOCUMENT_ROLE_REFUSED_NOT_RECORDED"


class DocumentRoleInputRefused(ValueError):
    """The request body was not a role assertion. Nothing was read or changed.

    Raised before any document state is touched, so a request refused here has read no
    document row, resolved no project and written nothing. It is also raised by the
    operation itself for a role outside the vocabulary, so the rule holds whichever
    boundary a caller entered through.
    """

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


class DocumentRoleRefused(Exception):
    """The assertion was well formed and was refused by the project's own state.

    `code` names the state and `statement` states the facts. Nothing has been written when
    this is raised for an unknown document; for `DOCUMENT_ROLE_REFUSED_NOT_RECORDED` the
    statement says plainly that the write was attempted and what came back.
    """

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


@dataclasses.dataclass(frozen=True)
class DocumentRoleRequest:
    """A well-formed role assertion, before anything is read.

    `document_id` is returned VERBATIM: nothing here checks that it is a UUID or that it
    names a row. Whether the document exists is a fact about persisted state, and it is
    decided by `assert_document_role`, which reads it — not by the shape of a string.
    """

    document_id: str
    role: str


@dataclasses.dataclass(frozen=True)
class DocumentRoleAssertion:
    """What one assertion did, stated from what the store reports.

    `role` is the role the document now holds, `previous_role` is the role it held before
    this request (which is the role the store held, not this module's memory of one), and
    `changed` says whether anything was written — false when the document already carried
    the asserted role, in which case no write was performed at all.
    """

    project_id: str
    document_id: str
    role: str
    previous_role: str | None
    changed: bool

    def to_response(self) -> dict[str, object]:
        """The assertion as the route answers it. Facts only, and no inference."""
        return {
            "status": "role_asserted",
            "project_id": self.project_id,
            "document_id": self.document_id,
            "role": self.role,
            "previous_role": self.previous_role,
            "changed": self.changed,
        }


# ======================================================================================
# The request surface.
# ======================================================================================
def parse_document_role_request(body: object) -> DocumentRoleRequest:
    """The document and the role this request asserts, or a refusal. Reads nothing.

    The body is REQUIRED to be an object, and it carries exactly two keys: `document_id`,
    which names one of the project's own documents, and `role`, which must be one of
    `DOCUMENT_ROLES`. A body naming a server-controlled field is refused by that field's
    own name so the caller learns which one it should not have sent; any other key is
    refused as unknown. An absent, null or non-string `document_id`, and an absent, null,
    non-string or out-of-vocabulary `role`, are each refused as what they are, because a
    caller who has not said which document, or which role, has asserted nothing — and this
    boundary does not choose either on their behalf.

    The vocabulary is checked HERE as well as in `assert_document_role`, deliberately: the
    route is not the only thing that could call this module, and the schema's own CHECK
    constraint must never be the first place an invalid role is discovered.
    """
    if body is None:
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING,
            "no request body was sent; a role is asserted by naming the document and the "
            "role it has, and nothing can be asserted without them",
        )
    if not isinstance(body, Mapping):
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_NOT_A_MAPPING,
            f"the request body is {type(body).__name__} rather than an object; a role is "
            "asserted with the document it is about and the role it is being asserted to "
            "have",
        )
    owned = sorted(name for name in SERVER_OWNED_FIELDS if name in body)
    if owned:
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_SERVER_OWNED_FIELD,
            f"the request body names {owned}. The project comes from the URL, the reviewer "
            "from the access token and the document's own attributes from the document "
            "itself — none of them is ever taken from a caller",
        )
    unknown = sorted(name for name in body if name not in REQUEST_FIELDS)
    if unknown:
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_UNKNOWN_FIELD,
            f"the request body carries {unknown}; a role is asserted with the document it "
            "is about and the role it is being asserted to have",
        )

    document_id = body.get("document_id")
    if not (isinstance(document_id, str) and document_id.strip()):
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_DOCUMENT_INVALID,
            f"the request names document_id={document_id!r}; a document is addressed by a "
            "non-empty string, and this boundary does not choose one",
        )

    role = body.get("role")
    if not (isinstance(role, str) and role.strip()):
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID,
            f"the request names role={role!r}; a role is asserted by one of "
            f"{list(DOCUMENT_ROLES)}, and an assertion with no role in it asserts nothing",
        )
    if role not in DOCUMENT_ROLES:
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID,
            f"the request names role={role!r}, which is not one of the roles this system "
            f"stores: {list(DOCUMENT_ROLES)}. A role outside that list is refused here "
            "rather than stored as a category nothing recognises",
        )
    return DocumentRoleRequest(document_id=document_id, role=role)


# ======================================================================================
# The assertion itself.
# ======================================================================================
def assert_document_role(
    project_id: str,
    *,
    document_id: str,
    role: str,
    repository=None,
) -> DocumentRoleAssertion:
    """Records `role` as the asserted role of one of this project's documents.

    Raises `DocumentRoleInputRefused` for a role outside `DOCUMENT_ROLES`, whatever the
    caller did first, and `DocumentRoleRefused` for a document this project does not keep
    or a write the store did not record. Nothing has been written when either raises for
    the unknown-document case, and the statement of `NOT_RECORDED` says the write was
    attempted rather than reporting a success the store did not confirm.

    Exactly one read — this project's own documents, through the repository `project_documents_for_project`
    which J61 published — and, when the role actually changes, one single-column write.
    The addressed document is looked for among the rows that read returned, so a document
    of another project is unreachable; the write is filtered by the same pair, so it could
    not reach one even if the read had been wrong.

    The assertion is reported from what the STORE says after the write, never from the
    argument this function was handed: a store that recorded something else is reported as
    having recorded something else, and a store that recorded nothing is refused.
    """
    if role not in DOCUMENT_ROLES:
        raise DocumentRoleInputRefused(
            DOCUMENT_ROLE_INPUT_REFUSED_ROLE_INVALID,
            f"{role!r} is not one of the roles this system stores: {list(DOCUMENT_ROLES)}",
        )

    store = repository if repository is not None else repo

    documents = store.project_documents_for_project(project_id)
    addressed = [row for row in documents if row.get("id") == document_id]
    if not addressed:
        raise DocumentRoleRefused(
            DOCUMENT_ROLE_REFUSED_DOCUMENT_UNKNOWN,
            f"no source document {document_id!r} belongs to project {project_id!r}; a "
            f"document's role is asserted on the id of one of this project's own "
            f"project_documents rows, and this project keeps {len(documents)}",
        )

    previous_role = addressed[0].get("role")
    if previous_role == role:
        # The state the caller asked for already holds. Not a second write, and not a
        # guess about the store: `previous_role` is what the row itself says.
        return DocumentRoleAssertion(
            project_id=project_id,
            document_id=document_id,
            role=role,
            previous_role=previous_role,
            changed=False,
        )

    updated = store.update_document_role(project_id, document_id, role)
    if not isinstance(updated, Mapping) or updated.get("role") != role:
        raise DocumentRoleRefused(
            DOCUMENT_ROLE_REFUSED_NOT_RECORDED,
            f"the role {role!r} was written to document {document_id!r} of project "
            f"{project_id!r} and the store did not report it back; the write was attempted "
            "and is not reported as a success, and nothing here retries or undoes it",
        )

    return DocumentRoleAssertion(
        project_id=project_id,
        document_id=document_id,
        role=updated.get("role"),
        previous_role=previous_role,
        changed=True,
    )
