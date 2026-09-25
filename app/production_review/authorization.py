"""
Milestone J19 — WHICH PROJECT AN AUTHENTICATED REVIEWER MAY SEE.

One rule, stated once, applied server-side, before anything is read:

    an authenticated reviewer may see a project exactly when the project's own
    `user_id` is the reviewer's own subject.

THAT RULE IS NOT NEW. It is `projects.user_id`, and it is already how this
database decides who may see a project: 37 row-level-security policies across
every table that hangs off a project say `auth.uid() = user_id`, and
`projects.user_id` is a foreign key onto `profiles.id` (`projects_user_id_fkey`),
which IS the auth user id. Nothing here invents a role, a membership, a share or
an organisation.

It is restated in code because the server talks to PostgreSQL with the
service-role key, and the service-role key BYPASSES row-level security. An
authenticated server request would otherwise be subject to no ownership rule at
all: the database would hand back any project it was asked for, so the rule has
to hold in the process that asks. Restating the database's own rule in the
process that bypasses it is not a second authorization model — it is the same
model, applied where the guard is missing.

WHY THIS IS A PURE FUNCTION OF TWO VALUES. `authorize_project` takes the
reviewer's established identity and the row that was actually loaded, and returns
a decision. It reads nothing, so it cannot be argued with by a request body:
there is no query parameter, form field, header, cookie or session variable that
can reach it. A caller that wanted to grant itself access would have to change
the project row or forge a token, which are the two things the rest of this
package exists to prevent.

FAIL-CLOSED. Every fact the rule needs must be present and non-empty. An
unidentified caller, a project that was not found, and a project whose owner was
not recorded are three different refusals with three different codes, and none of
them is "allowed". An absent owner is never treated as "no owner to check
against".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.production_review.identity import ReviewerIdentity

__all__ = [
    "ACCESS_ALLOWED",
    "ACCESS_NOT_OWNER",
    "ACCESS_PROJECT_OWNER_UNKNOWN",
    "ACCESS_PROJECT_UNKNOWN",
    "ACCESS_UNIDENTIFIED",
    "AccessDecision",
    "authorize_project",
]

ACCESS_ALLOWED = "ACCESS_ALLOWED"
ACCESS_UNIDENTIFIED = "ACCESS_UNIDENTIFIED"
ACCESS_PROJECT_UNKNOWN = "ACCESS_PROJECT_UNKNOWN"
ACCESS_PROJECT_OWNER_UNKNOWN = "ACCESS_PROJECT_OWNER_UNKNOWN"
ACCESS_NOT_OWNER = "ACCESS_NOT_OWNER"

# The column the rule reads, named once so no caller has to remember it.
OWNER_COLUMN = "user_id"
PROJECT_ID_COLUMN = "id"


@dataclass(frozen=True)
class AccessDecision:
    """Whether this reviewer may see this project, and why not when not.

    A decision is never partial: `allowed` is True only for `ACCESS_ALLOWED`, and
    every other code is a refusal a caller must act on by reading nothing.
    """

    allowed: bool
    code: str
    detail: str
    project_id: str | None


def _text(value) -> str | None:
    """The value when it is a non-empty string, else None. Never coerced: an id
    that is not a string is not an identity, and `None`/`0`/`""` name nobody."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def authorize_project(identity, project_row) -> AccessDecision:
    """May `identity` see `project_row`?

    `identity` is a `ReviewerIdentity` — which only `verify_access_token`
    produces — and `project_row` is the row that was loaded from the project
    store. Both are needed; neither is guessed at.

    A refusal never restates the other owner's id: the caller learns that the
    project is not theirs and nothing about whose it is.
    """
    project_id = None
    if isinstance(project_row, Mapping):
        project_id = _text(project_row.get(PROJECT_ID_COLUMN))

    if not isinstance(identity, ReviewerIdentity):
        return AccessDecision(
            allowed=False,
            code=ACCESS_UNIDENTIFIED,
            detail=(
                "no reviewer identity was established for this request, so no project can be "
                "shown — access is decided from an established identity, never from a request."
            ),
            project_id=project_id,
        )
    reviewer_id = _text(identity.user_id)
    if reviewer_id is None:
        return AccessDecision(
            allowed=False,
            code=ACCESS_UNIDENTIFIED,
            detail="the established identity names no user, so it identifies no reviewer.",
            project_id=project_id,
        )

    if not isinstance(project_row, Mapping):
        return AccessDecision(
            allowed=False,
            code=ACCESS_PROJECT_UNKNOWN,
            detail="no project was loaded, so there is nothing this reviewer may be shown.",
            project_id=project_id,
        )

    owner_id = _text(project_row.get(OWNER_COLUMN))
    if owner_id is None:
        # An unowned project is not a shared one. This is a refusal because the
        # rule's whole subject is missing: without a recorded owner there is
        # nothing to compare, and "nothing to compare" is not "equal".
        return AccessDecision(
            allowed=False,
            code=ACCESS_PROJECT_OWNER_UNKNOWN,
            detail=(
                "this project records no owner, so no reviewer can be shown to be its owner — "
                "an unrecorded owner is not an ownerless project."
            ),
            project_id=project_id,
        )

    if reviewer_id != owner_id:
        return AccessDecision(
            allowed=False,
            code=ACCESS_NOT_OWNER,
            detail=(
                "this project belongs to another account, so this reviewer may not see it or "
                "act on it."
            ),
            project_id=project_id,
        )

    return AccessDecision(
        allowed=True,
        code=ACCESS_ALLOWED,
        detail="this project's owner is the authenticated reviewer.",
        project_id=project_id,
    )
