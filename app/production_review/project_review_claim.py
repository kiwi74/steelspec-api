"""
Milestone J44 — THE PROJECT REVIEW CLAIM.

J43 decided that a human review of a project would be performed under a claim, so that two
reviewers opening the same project do not both spend the work. This module is the only code
that acquires and releases that claim. It calls two database functions and translates their
refusals; it holds no state, decides nothing and remembers nothing.

WHAT THE CLAIM IS, AND WHAT IT IS NOT
=====================================
The claim is a LIVENESS AND WORK-SAVING DEVICE. It is not the correctness barrier, and no
caller may treat it as one:

  * Correctness lives in J22. A recorded review revision is protected by the advisory lock
    and the sequential-revision rule inside J22's own snapshot-recording function, and by
    7AJ's stale-revision and one-shot-connection guards. Those hold whether or not any
    claim exists, and they hold when two processes both believe they hold the claim.
  * Therefore a claim can be lost without any harm being done: if two reviewers acquire
    "the same" claim (the second after the first's lease expired), the second simply does
    the work and J22 refuses whatever the first tries to record that is no longer current.
    `took_over` reports exactly that situation, and it is NOT an error.
  * What the claim buys is that the ordinary case — one reviewer, one project — does not
    duplicate work, and that a crashed reviewer's project frees itself without an operator.

WHAT THIS MODULE KNOWS NOTHING ABOUT
====================================
No review package, no resolution, no connection, no fabrication, no drawing, no CAD, no
evidence table, no HTTP request, no caller identity, no authorization. The holder is a
string this module is GIVEN; it never derives one, never inspects a token, and never decides
whether the caller is allowed to review the project. Establishing that a caller may review a
project is `authorization.py`'s job, and it happens BEFORE this module is called — J43
Decision 3: the claim is acquired only after authentication and ownership authorization,
never as a substitute for either.

THE LEASE, AND WHY THERE IS NO HEARTBEAT
========================================
A claim is taken with a lease in seconds and expires on its own. There is no renewal call
and no heartbeat: a lease that must be kept alive needs a background process to keep it
alive, and a claim whose holder dies is exactly the case the lease exists to survive. A
reviewer who needs longer simply acquires again — after the lease expires, that is a
takeover, which is a supported and unremarkable transition.

The database owns the lease's bounds (one second to one day) and refuses anything outside
them with its own code. This module does not repeat that rule here: a second, weaker copy of
a database rule is the copy that runs first and is therefore the one that can be wrong.

TIME IS THE DATABASE'S
======================
`lease_expires_at` is computed and compared with `clock_timestamp()` — the real wall clock —
and not with `now()`, which is the transaction's start time and therefore frozen for the
whole transaction. A lease that could not be observed expiring inside the transaction that
tests it would not be a lease. Both timestamps below are returned exactly as PostgreSQL
rendered them, and are not reformatted, rounded or converted to a local timezone.

THE REFUSALS
============
Every refusal is raised as a `ClaimRefused` carrying the database's own code and statement:

    CLAIM_REFUSED_ACTIVE           another holder's claim is live; the lease expiry is
                                   carried on the refusal, and the holder's NAME is not
    CLAIM_REFUSED_NOT_HOLDER       this token did not acquire this project's claim
    CLAIM_REFUSED_NOT_ACTIVE       this token did acquire it, and the claim is no longer live
    CLAIM_REFUSED_PROJECT_UNKNOWN  no such project, so nothing was claimed
    CLAIM_REFUSED_NO_HOLDER        an empty holder
    CLAIM_REFUSED_LEASE            a lease outside the database's bounds

A failure this module cannot name is re-raised UNCHANGED. Inventing a refusal for it would
be inventing an explanation.

Content rules are the database's, too. An empty holder is refused here by
`CLAIM_REFUSED_NO_HOLDER` from the database rather than shadowed by a Python check, so there
is exactly one statement of that rule. Python-level type errors are the module's own and are
raised as `TypeError`, because they never reach the database at all.
"""

from __future__ import annotations

import json

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "ACQUIRE_FUNCTION",
    "CLAIM_REFUSED_ACTIVE",
    "CLAIM_REFUSED_LEASE",
    "CLAIM_REFUSED_NOT_ACTIVE",
    "CLAIM_REFUSED_NOT_HOLDER",
    "CLAIM_REFUSED_NO_HOLDER",
    "CLAIM_REFUSED_PROJECT_UNKNOWN",
    "DEFAULT_LEASE_SECONDS",
    "LEASE_BOUNDS_SECONDS",
    "RELEASE_FUNCTION",
    "ClaimRefused",
    "ProjectReviewClaim",
    "acquire_project_review_claim",
    "release_project_review_claim",
]

#: The two functions in `20260928000000_j44_project_review_claims.sql`, by name.
ACQUIRE_FUNCTION = "acquire_project_review_claim"
RELEASE_FUNCTION = "release_project_review_claim"

#: A quarter of an hour: long enough that a reviewer working normally never loses the claim
#: to expiry, short enough that a reviewer whose process died frees the project well inside
#: the time it takes anyone else to notice. It is a default the CALLER omits, not a policy —
#: a caller that wants a different lease passes one.
DEFAULT_LEASE_SECONDS = 900

#: The database's bounds, stated here so a caller can choose a legal lease without reading
#: the migration. They are NOT enforced in this module: `CLAIM_REFUSED_LEASE` is, and it is
#: the database's answer.
LEASE_BOUNDS_SECONDS = (1, 86400)

CLAIM_REFUSED_ACTIVE = "CLAIM_REFUSED_ACTIVE"
CLAIM_REFUSED_NOT_HOLDER = "CLAIM_REFUSED_NOT_HOLDER"
CLAIM_REFUSED_NOT_ACTIVE = "CLAIM_REFUSED_NOT_ACTIVE"
CLAIM_REFUSED_PROJECT_UNKNOWN = "CLAIM_REFUSED_PROJECT_UNKNOWN"
CLAIM_REFUSED_NO_HOLDER = "CLAIM_REFUSED_NO_HOLDER"
CLAIM_REFUSED_LEASE = "CLAIM_REFUSED_LEASE"

#: The refusal codes the database itself raises, and the only ones this module translates
#: from SQLSTATE P0001. A raised message with any other prefix is not one of ours.
_RAISED_CODES = (
    CLAIM_REFUSED_ACTIVE,
    CLAIM_REFUSED_NOT_HOLDER,
    CLAIM_REFUSED_NOT_ACTIVE,
    CLAIM_REFUSED_NO_HOLDER,
    CLAIM_REFUSED_LEASE,
)

_RAISED = "P0001"
_FOREIGN_KEY_VIOLATION = "23503"


class ClaimRefused(ValueError):
    """A claim was not acquired, or not released. The database's reasons, preserved.

    `code` is the database's own refusal code, `statement` its own message, and
    `lease_expires_at` the lease expiry it reported when the refusal names one — that is,
    when another holder's claim is live and the caller needs to know when it frees. The
    holder's NAME is never part of this, because the refusal does not contain it.
    """

    def __init__(self, code: str, statement: str, *, lease_expires_at: str | None = None):
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement
        self.lease_expires_at = lease_expires_at


@dataclass(frozen=True)
class ProjectReviewClaim:
    """One acquired claim, as the database returned it. No value here is derived.

    `took_over` is True when the claim was free — because it had been released, because the
    lease had expired, or because no claim had ever existed for this project. It is False
    only when this acquisition created the project's first claim. A takeover is not an
    error and not a conflict: it may mean a previous holder's process died, and it may mean
    a previous holder is still working and will be refused by J22 when it tries to record.
    """

    project_id: str
    claim_token: str
    acquired_at: str
    lease_expires_at: str
    took_over: bool


def _client(client):
    """The store to call: the caller's, or the production one, imported on use.

    Importing the production client at module import time would put a database connection
    behind every import of this module, including the ones that never call a claim.
    """
    if client is not None:
        return client
    from app.supabase_client import supabase

    return supabase


def _text(value, name: str) -> str:
    """A parameter that must be a string, refused HERE because it never reaches the database.

    Emptiness is NOT checked. An empty holder is a content rule the database states as
    `CLAIM_REFUSED_NO_HOLDER`, and a second statement of it here would be the weaker copy.
    """
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str (got {type(value).__name__}).")
    return value


def _data(response):
    """The RPC's payload: the returned JSON object, whichever way the client wrapped it."""
    data = getattr(response, "data", response)
    if isinstance(data, (list, tuple)):
        return data[0] if data else None
    return data


def _error_fields(exc) -> dict[str, str]:
    """The PostgREST error's fields, from the exception or from its message. Never guessed."""
    fields: dict[str, str] = {}
    for name in ("code", "message", "details", "hint"):
        value = getattr(exc, name, None)
        if isinstance(value, str):
            fields[name] = value
    if "code" not in fields:
        try:
            parsed = json.loads(str(exc))
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, Mapping):
            for name in ("code", "message", "details", "hint"):
                value = parsed.get(name)
                if isinstance(value, str):
                    fields[name] = value
    return fields


def _lease_from_hint(hint: str | None) -> str | None:
    """The lease expiry a refusal reported, verbatim, or None when it reported none."""
    if not hint or not hint.startswith("lease_expires_at="):
        return None
    return hint.split("=", 1)[1]


def _refusal_from(exc) -> ClaimRefused | None:
    """The claim refusal a failed call IS, or None when it is not one of them.

    Returning None is not a fallback: a failure this module cannot name is re-raised
    unchanged, because inventing a refusal for it would be inventing an explanation.
    """
    fields = _error_fields(exc)
    code = fields.get("code")
    message = fields.get("message", "")
    if code == _RAISED:
        raised = message.split(":", 1)[0]
        if raised in _RAISED_CODES:
            return ClaimRefused(
                raised,
                message.split(":", 1)[1].strip() if ":" in message else message,
                lease_expires_at=_lease_from_hint(fields.get("hint")),
            )
    if code == _FOREIGN_KEY_VIOLATION:
        return ClaimRefused(
            CLAIM_REFUSED_PROJECT_UNKNOWN,
            "the project this claim names is not in the database, so nothing was claimed "
            f"({message})",
        )
    return None


def _call(store, function: str, params: Mapping[str, object]):
    """One RPC, with every refusal the database raises translated and everything else raised."""
    response = store.rpc(function, dict(params))
    try:
        return response.execute()
    except Exception as exc:
        refusal = _refusal_from(exc)
        if refusal is None:
            raise
        raise refusal from exc


def acquire_project_review_claim(
    project_id: str,
    holder: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    client=None,
) -> ProjectReviewClaim:
    """Take this project's review claim, or refuse and say why someone else holds it.

    One transaction in the database reads the claim, decides whether it is live, inserts or
    takes it over, and returns it. There is no read-then-write here and there must never be
    one: a caller that read the claim and then decided would be deciding from a stale
    reading, and the database would still have the last word.

    `holder` is the caller's authenticated identity, decided by the caller. This module does
    not check it, and it must never be filled in from a request body: J43 Decision 3 puts the
    claim behind authentication and authorization, and a holder taken from user input would
    put an unauthenticated name into the record of who is reviewing a project.

    Returns the claim with its token. The token is the ONLY thing that can release it, and
    it is not recoverable: a holder that loses it cannot release, and must wait for the
    lease to expire.
    """
    project = _text(project_id, "project_id")
    who = _text(holder, "holder")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool):
        raise TypeError(f"lease_seconds must be an int (got {type(lease_seconds).__name__}).")

    response = _call(
        _client(client),
        ACQUIRE_FUNCTION,
        {
            "p_project_id": project,
            "p_holder": who,
            "p_lease_seconds": lease_seconds,
        },
    )
    payload = _data(response)
    if not isinstance(payload, Mapping):
        raise RuntimeError(
            f"{ACQUIRE_FUNCTION} returned no claim for {project}; an acquisition that "
            "reports neither a claim nor a refusal is not one this module can describe."
        )
    return ProjectReviewClaim(
        project_id=project,
        claim_token=str(payload.get("claim_token")),
        acquired_at=str(payload.get("acquired_at")),
        lease_expires_at=str(payload.get("lease_expires_at")),
        took_over=bool(payload.get("took_over")),
    )


def release_project_review_claim(
    project_id: str, claim_token: str, *, client=None
) -> None:
    """Release this project's claim, if this token is the one that holds it.

    Release matches on the TOKEN and never on the holder. Two reviewers can share a name,
    and a name is not a secret: a release by holder would let one review session end
    another's, which is the opposite of what the claim is for.

    Releasing twice is refused with `CLAIM_REFUSED_NOT_ACTIVE` rather than reported as
    success. A claim that has already been released is not one this call released, and
    saying otherwise would make a double release indistinguishable from a single one.

    A claim that was already taken over is refused with `CLAIM_REFUSED_NOT_HOLDER`: this
    token no longer owns the project's claim, and, importantly, releasing must not cut short
    the holder that does.

    CALLERS RELEASING IN A `finally` SHOULD EXPECT A REFUSAL. Because every refusal here is
    an exception, a release in a `finally` raises when the claim is no longer this token's —
    which is precisely the case where the lease expired and another reviewer took over — and
    that exception would replace whatever was already travelling. A caller that releases
    unconditionally handles `ClaimRefused`: the claim it wanted free is already free or
    already someone else's, and neither is a failure of the work being finished. This is a
    consequence of the refusal-not-idempotent choice above, and it is stated here rather than
    smoothed over with a flag, because a variant that silently swallows the refusal would put
    the same decision back into every call site without any of them knowing.
    """
    project = _text(project_id, "project_id")
    token = _text(claim_token, "claim_token")
    _call(
        _client(client),
        RELEASE_FUNCTION,
        {"p_project_id": project, "p_claim_token": token},
    )
