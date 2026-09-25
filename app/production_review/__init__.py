"""
Milestone J19 — the PRODUCTION REVIEW BINDING.

J18 built the page-exception surface (the pages of a drawing set whose response
could not be read, and the one action a human may take on each) but bound it to
nothing: the review UI's session layer is a deliberately in-process slice with
NO authentication, and nothing under `app/` ever loaded a real project into it.

This package is the binding. It establishes, server-side and per request:

    app/production_review/identity.py       WHO the caller is
    app/production_review/authorization.py  WHICH project they may see
    app/production_review/project_read.py   WHAT that project's record states
    app/production_review/binding.py        the SAME contract/view/render chain
                                            the existing review UI consumes

WHAT ALREADY EXISTED, AND IS REUSED RATHER THAN REPLACED
========================================================
SteelSpec already has a production identity system and a production
authorization rule. Neither was built by this milestone:

  * the identity system is Supabase Auth. An authenticated user is an
    `auth.users` row, mirrored in `public.profiles` (`profiles.id` IS the auth
    user id), and `projects.user_id` is a foreign key onto it
    (`projects_user_id_fkey`). The frontend already authenticates against it.
  * the authorization rule is `projects.user_id == auth.uid()`. It is already
    enforced by the database itself, in 37 row-level-security policies across
    every table that hangs off a project ("Users can view own projects" and
    its siblings).

So this milestone does NOT create an authentication system, does NOT create a
role, and does NOT create an ownership column. It verifies the identity the
existing system already issues and restates the rule the database already
enforces — because the server talks to PostgreSQL with the service-role key,
which BYPASSES row-level security, so the rule has to hold in code as well.

THE ONE THING THAT IS NEW
=========================
The access token is verified against the project's own PUBLIC JSON Web Key Set
(`/auth/v1/.well-known/jwks.json`), which this project publishes because it
signs with an asymmetric ES256 key. That means verification needs NO shared
secret: the only credential involved is a public key, and the project's JWT
signing secret is never read, configured or held. See `identity.py`.

WHAT THIS PACKAGE NEVER DOES
============================
No extraction, no AI call, no retry, no evidence write, no status write, no
migration, no new column, no second retry implementation and no second status
source. Loading a project's review page is READ-ONLY; the only mutating route is
the explicit per-page retry, which crosses the same authorization boundary
again and then hands the page to the existing J17 retry authority unchanged.

ADDED BY J24A — THE REVISION-0 PRODUCER
=======================================
`project_workflow_reconstruction.py` is the read seam that lets a project's
initial 7AJ workflow be reconstructed from what is persisted — the raw AI
readings J23 records, plus the project, drawing-set, drawing and page
identities — instead of from a `list[PageExtraction]` alive in one process's
memory. It selects which recorded reading stands for each page, refuses when the
persisted record cannot support a reconstruction, and hands the result to the
existing 7AJ, 7Y and 7AZ builders unchanged.

It writes nothing, calls no AI, resolves nothing and generates nothing, and it
is the fifth module rather than a change to any of the four: the identity,
authorization, read and binding boundaries J19 established are untouched.
"""

from app.production_review import (
    authorization,
    binding,
    identity,
    project_read,
    project_workflow_reconstruction,
)

__all__ = [
    "authorization",
    "binding",
    "identity",
    "project_read",
    "project_workflow_reconstruction",
]
