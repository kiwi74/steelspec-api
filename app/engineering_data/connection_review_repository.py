"""
Milestone J22 — THE CONNECTION-REVIEW STORE.

J21 designed the durable state a connection review leaves behind, and proved the design
against the genuine capture. This module is the only code that puts that state into the two
tables it designed, and the only code that reads it back. It is persistence and nothing
else: it decides no engineering question, derives no status, resolves no exception, and
renders nothing.

WHAT IT WRITES, AND FROM WHERE
==============================
From a genuine `ProjectWorkflowState` — 7AJ's own in-process review state — and from
nothing else. `record_project_review` hands that workflow to J21's `build_review_snapshot`
and persists what comes back:

    J19 binding (authorized project)  ->  project_id
    latest persisted revision         ->  previous_revision
    J21 build_review_snapshot(...)    ->  one ReviewSnapshot  (every refusal is J21's)
    J21 ReviewSnapshot.to_rows()      ->  one header row + its item rows
    public.record_connection_review_snapshot(...)  ->  one transaction

There is NO function here that takes a project id, a `connections` row, a `steel_members`
row, a `review_items` row or an extraction result and returns review state. That absence is
J21 Decision B (no backfill) enforced by the shape of the module: a review state can only be
built from a workflow that already exists in this process, by the module that owns building
it.

WHAT IT READS
=============
Reads of the two tables this module owns, and — for the evidence fingerprint only — the
project's existing evidence readers:

    latest_recorded_revision(project_id)     the highest revision this project has recorded
    load_review_snapshot(project_id, rev)    one recorded revision, assembled by J21
    read_connection_review_state(project_id) the current recorded state, or its absence
    project_evidence_rows(project_id)        the rows J21's fingerprint is computed over

`project_evidence_rows` calls `repository.member_rows_for_project` and
`repository.connection_rows_for_project`, which already existed. What it returns feeds
J21's `evidence_identity` — a DIGEST — and can never become review state: no decision,
status, reading, task or provenance value is produced from it anywhere in this module.

THE REFUSALS ARE THE DATABASE'S
===============================
The writer does not check for a duplicate revision and then insert. It inserts, and the
database refuses. Two reasons, and both are about truth rather than style:

  * A read-then-write check is a race. Two requests that both read "revision 0 is the
    latest" would both insert revision 1. The function in the migration reads the highest
    revision and inserts inside one transaction, so a duplicate and a gap are refused where
    they can actually be detected.
  * The revision rule is a property of the stored chain, not of what this process believes.
    A check here would be a second, weaker copy of the rule — and the copy that runs first is
    the one that can be wrong.

So `SNAPSHOT_REFUSED_REVISION_GAP`, `SNAPSHOT_REFUSED_PROJECT_UNKNOWN` and
`SNAPSHOT_REFUSED_UNREPRESENTABLE` are raised here from the SQLSTATE the database returned,
with the database's own message preserved. Everything this module refuses BEFORE the
database is refused by J21, in J21's vocabulary, unchanged.

NULL IS NOT A STATUS
====================
`output_status`, `verification_status`, `connection_id` and `last_processed_revision` are
persisted as NULL when they are NULL — a connection that was never dispatched is not one
whose dispatch produced some status. Nothing here defaults, substitutes or infers one.
"""

from __future__ import annotations

import json

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.cad_engine.connection_review_snapshot import (
    CITATION_TABLE,
    ITEM_TABLE,
    SNAPSHOT_REFUSED_PROJECT_UNKNOWN,
    SNAPSHOT_REFUSED_REVISION_GAP,
    SNAPSHOT_REFUSED_UNREPRESENTABLE,
    SNAPSHOT_TABLE,
    ReviewFieldCitation,
    ReviewSnapshot,
    ReviewSnapshotItem,
    SnapshotRefused,
    build_review_snapshot,
    citation_from_row,
    snapshot_from_rows,
)

__all__ = [
    "NO_PERSISTED_CONNECTION_REVIEW_STATE",
    "REVIEW_STATE_RECORDED",
    "WRITER_FUNCTION",
    "RecordedReviewState",
    "latest_recorded_revision",
    "load_review_citations",
    "load_review_snapshot",
    "persist_connection_review_citations",
    "persist_review_snapshot",
    "project_evidence_rows",
    "read_connection_review_state",
    "record_project_review",
]

#: The one writer, by name. It is a function rather than an insert sequence because a
#: header with half its items is a wrong revision, not a partial one.
WRITER_FUNCTION = "record_connection_review_snapshot"

#: J66's writer, by name: ONE review package's citations, INSERT only. It is a function for
#: the same reason the writer above is — a set of citations half-written is not a partial
#: record, it is a wrong one — and it performs no revision check of its own: the citation's
#: revision is the ITEM's, and the composite foreign key is what enforces that.
CITATION_WRITER_FUNCTION = "record_connection_review_citations"

#: What `read_connection_review_state` says when the project has recorded nothing. J21
#: Decision B: an existing project has NO review state, and its absence is a state the read
#: path states — never an empty review that a caller could mistake for one.
NO_PERSISTED_CONNECTION_REVIEW_STATE = "NO_PERSISTED_CONNECTION_REVIEW_STATE"
REVIEW_STATE_RECORDED = "REVIEW_STATE_RECORDED"

# The PostgreSQL conditions this module translates, and the J21 refusal each one IS. The
# vocabulary is J21's; only the words are PostgreSQL's.
_FOREIGN_KEY_VIOLATION = "23503"
_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_RAISED = "P0001"


@dataclass(frozen=True)
class RecordedReviewState:
    """What a project's review store holds, or the explicit statement that it holds nothing.

    `code` is the whole of the answer when `snapshot` is None: the project has no persisted
    connection-review state. It is NOT an error, and it is not an empty review — a caller
    that wanted to display a review has nothing to display, which is what J21 requires an
    existing project to say until a revision is recorded against it.
    """

    project_id: str
    code: str
    snapshot: ReviewSnapshot | None
    revisions: tuple[int, ...]

    @property
    def has_review_state(self) -> bool:
        return self.snapshot is not None

    @property
    def latest_revision(self) -> int | None:
        """The highest recorded revision, or None when nothing has been recorded."""
        return self.revisions[-1] if self.revisions else None


def _client(client):
    """The store to call: the caller's, or the production one, imported on use.

    Importing the production client at module import time would put a database connection
    behind every import of this module, including the ones that never read or write.
    """
    if client is not None:
        return client
    from app.supabase_client import supabase

    return supabase


def _rows(response) -> tuple[Mapping[str, Any], ...]:
    """The response's rows when it carries rows, else none. Never a partial row."""
    data = getattr(response, "data", response)
    if isinstance(data, (list, tuple)):
        return tuple(row for row in data if isinstance(row, Mapping))
    if isinstance(data, Mapping):
        return (data,)
    return ()


def _json_text(value) -> str:
    """One payload as the JSON TEXT it will be stored as.

    The payload crosses the wire as a string and is cast `::json` inside the writer, so no
    serialiser between here and the column can re-order its keys: `json` stores the text it
    is given, and a jsonb column — which normalises key order and discards the input text —
    is what J21 Decision A forbids. Key order is preserved by `json.dumps`; nothing is
    sorted, filtered or coerced.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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


def _refusal_from(exc) -> SnapshotRefused | None:
    """The J21 refusal a failed write IS, or None when it is not one of them.

    Returning None is not a fallback: a failure this module cannot name is re-raised
    unchanged, because inventing a refusal for it would be inventing an explanation.
    """
    fields = _error_fields(exc)
    code = fields.get("code")
    message = fields.get("message", "")
    if code == _RAISED and message.startswith("SNAPSHOT_REFUSED_"):
        return SnapshotRefused(message.split(":", 1)[0], message)
    if code == _FOREIGN_KEY_VIOLATION:
        return SnapshotRefused(
            SNAPSHOT_REFUSED_PROJECT_UNKNOWN,
            "the review names a project the database does not have, so no revision was "
            f"recorded against it ({message})",
        )
    if code == _UNIQUE_VIOLATION:
        return SnapshotRefused(
            SNAPSHOT_REFUSED_REVISION_GAP,
            "this (project, revision) pair is already recorded; a recorded revision is never "
            f"overwritten ({message})",
        )
    if code == _CHECK_VIOLATION:
        return SnapshotRefused(
            SNAPSHOT_REFUSED_UNREPRESENTABLE,
            "this review state does not fit the recorded vocabulary of the review tables, so "
            f"it was not recorded ({message})",
        )
    return None


def _citation_refusal_from(exc) -> SnapshotRefused | None:
    """The refusal a failed CITATION write IS, from the database's own words, or None.

    The ownership guard (a crossed project boundary, or a reading that does not exist) and
    the append-only trigger name themselves with a CITATION_REFUSED_ code, and that name is
    preserved verbatim.

    Everything else is re-raised unchanged, and the restraint is the point: a foreign-key
    failure here has four possible causes — the review item, the page reading, the occurrence,
    the document — and mapping them all onto one code would be inventing an explanation the
    database did not give. The database's own message says which one it was.
    """
    fields = _error_fields(exc)
    message = fields.get("message", "")
    if fields.get("code") == _RAISED and message.startswith("CITATION_REFUSED_"):
        return SnapshotRefused(message.split(":", 1)[0], message)
    return None


def _project_id(project_id) -> str:
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id must be a non-empty str.")
    return project_id


# ======================================================================================
# Reading what has been recorded.
# ======================================================================================
def latest_recorded_revision(project_id: str, *, client=None) -> int | None:
    """The highest revision this project has recorded, or None when it has recorded none.

    This reads the revision HEADERS only. It is the chain's own answer to "where is this
    project's review?", and it is read here rather than remembered anywhere: a cached
    revision would be a second revision system, which J21 forbids.
    """
    rows = _rows(
        _client(client)
        .table(SNAPSHOT_TABLE)
        .select("review_revision")
        .eq("project_id", _project_id(project_id))
        .execute()
    )
    revisions = [
        row["review_revision"]
        for row in rows
        if isinstance(row.get("review_revision"), int)
        and not isinstance(row.get("review_revision"), bool)
    ]
    return max(revisions) if revisions else None


def load_review_snapshot(
    project_id: str, review_revision: int, *, client=None
) -> ReviewSnapshot | None:
    """One recorded revision, assembled from its own rows — or None when it is not recorded.

    Assembly is J21's (`snapshot_from_rows`), so a stored revision replays through exactly
    the code the design proved, and a row set missing a column is refused there rather than
    rendered as a review with holes in it.

    Items come back in `review_package_id` order. That is not an arbitrary choice: 7X
    derives the package id from submission order and 7AK's own project contract rebuilds its
    items in that same order, so the ordering rule is the reviewed one and not a second one.
    """
    store = _client(client)
    project = _project_id(project_id)
    if not isinstance(review_revision, int) or isinstance(review_revision, bool):
        raise TypeError("review_revision must be an int.")

    header = _rows(
        store.table(SNAPSHOT_TABLE)
        .select("*")
        .eq("project_id", project)
        .eq("review_revision", review_revision)
        .execute()
    )
    if not header:
        return None
    items = _rows(
        store.table(ITEM_TABLE)
        .select("*")
        .eq("project_id", project)
        .eq("review_revision", review_revision)
        .execute()
    )
    ordered = sorted(items, key=lambda row: row.get("review_package_id") or "")
    return snapshot_from_rows(header[0], ordered)


def load_review_citations(
    project_id: str, review_revision: int, *, client=None
) -> tuple[ReviewFieldCitation, ...]:
    """One recorded revision's citations, decoded — or none when it has none recorded.

    A revision recorded before the citation table existed, and every revision this system
    recorded so far, has NONE. That is an empty tuple and not an error: it is the honest
    answer, and the contract states it as UNCITED rather than as an absence of a claim.

    It is a reader of its own rather than part of `load_review_snapshot`, deliberately: the
    snapshot's replay is pinned to the two tables it owns (J22's own assertion), and a third
    table read smuggled into it would make that pin false. Nothing here writes, decides or
    advances anything.
    """
    store = _client(client)
    project = _project_id(project_id)
    if not isinstance(review_revision, int) or isinstance(review_revision, bool):
        raise TypeError("review_revision must be an int.")
    rows = _rows(
        store.table(CITATION_TABLE)
        .select("*")
        .eq("project_id", project)
        .eq("review_revision", review_revision)
        .execute()
    )
    return tuple(citation_from_row(row) for row in rows)


def read_connection_review_state(project_id: str, *, client=None) -> RecordedReviewState:
    """What this project's review store holds: the current revision, or its explicit absence.

    The current revision is the HIGHEST one recorded, which is J21's `latest_snapshot` rule.
    Nothing here recomputes, repairs or advances a revision; a project with no rows returns
    `NO_PERSISTED_CONNECTION_REVIEW_STATE` and no snapshot.
    """
    store = _client(client)
    project = _project_id(project_id)
    rows = _rows(
        store.table(SNAPSHOT_TABLE)
        .select("review_revision")
        .eq("project_id", project)
        .execute()
    )
    revisions = tuple(
        sorted(
            row["review_revision"]
            for row in rows
            if isinstance(row.get("review_revision"), int)
            and not isinstance(row.get("review_revision"), bool)
        )
    )
    if not revisions:
        return RecordedReviewState(
            project_id=project,
            code=NO_PERSISTED_CONNECTION_REVIEW_STATE,
            snapshot=None,
            revisions=(),
        )
    snapshot = load_review_snapshot(project, revisions[-1], client=client)
    if snapshot is None:
        # The header listed a revision whose own rows could not be read back. A review is
        # never half-reported: the absence is stated rather than a partial one rendered.
        return RecordedReviewState(
            project_id=project,
            code=NO_PERSISTED_CONNECTION_REVIEW_STATE,
            snapshot=None,
            revisions=(),
        )
    # J66: this revision's citations are bound onto the snapshot here, so that "the project's
    # current recorded state" is ONE object and every reader of it gets the citations for
    # free rather than each assembling its own. The binding is a copy of the same snapshot
    # with one more recorded field; nothing is recomputed and no citation is invented.
    snapshot = replace(
        snapshot,
        citations=load_review_citations(project, snapshot.review_revision, client=client),
    )
    return RecordedReviewState(
        project_id=project,
        code=REVIEW_STATE_RECORDED,
        snapshot=snapshot,
        revisions=revisions,
    )


def project_evidence_rows(project_id: str, *, repository=None) -> dict[str, tuple]:
    """The project's persisted evidence rows, as J21's fingerprint tables name them.

    This exists so that the fingerprint has ONE production source rather than each caller
    assembling its own. It reads through the repository's existing readers and returns what
    they return, verbatim. It is NOT a review-state reader: nothing here is a decision, a
    status, a task or a reading, and no function in this module turns these rows into
    anything but a digest.
    """
    if repository is None:
        from app.engineering_data import repository as project_store
    else:
        project_store = repository
    project = _project_id(project_id)
    return {
        "steel_members": tuple(project_store.member_rows_for_project(project) or ()),
        "connections": tuple(project_store.connection_rows_for_project(project) or ()),
    }


# ======================================================================================
# Writing one revision.
# ======================================================================================
def persist_review_snapshot(snapshot: ReviewSnapshot, *, client=None) -> ReviewSnapshot:
    """Persists ONE already-built revision: its header and all of its items, or nothing.

    The snapshot must already exist — this function builds no part of it and takes no
    project id it could build one from. What it does is transport it: the two payload sets
    go as JSON text, the writer function inserts the header and every item inside one
    transaction, and a refusal from the database comes back as the J21 refusal it is.

    Returns the snapshot it was given, so a caller can chain the read that proves what was
    stored.
    """
    if not isinstance(snapshot, ReviewSnapshot):
        raise TypeError(
            f"snapshot must be a ReviewSnapshot (got {type(snapshot).__name__}); this "
            "function persists a review state that already exists and can build none"
        )
    header, items = snapshot.to_rows()
    params = {
        "p_project_id": header["project_id"],
        "p_review_revision": header["review_revision"],
        "p_evidence_identity": _json_text(header["evidence_identity"]),
        "p_evidence_run_ids": _json_text(header["evidence_run_ids"]),
        "p_project_status": header["project_status"],
        "p_items": _json_text([dict(item) for item in items]),
    }
    response = _client(client).rpc(WRITER_FUNCTION, params)
    try:
        response.execute()
    except Exception as exc:
        refusal = _refusal_from(exc)
        if refusal is None:
            raise
        raise refusal from exc
    return snapshot


def persist_connection_review_citations(
    snapshot: ReviewSnapshot,
    review_package_id: str,
    *,
    client=None,
) -> tuple[dict, ...]:
    """Persists ONE reviewed connection's citations, or none of them. INSERT only.

    The snapshot must already exist and must already carry the citations: this function
    builds none of them, derives none of them and takes no field, drawing or value it could
    build one from. It is transport — J21's `citation_rows_for` produces the rows, the
    writer function inserts them all inside one transaction, and a refusal from the database
    comes back as the refusal it is.

    There is deliberately NO caller of this function in the production path. J66 built the
    infrastructure and left it unreachable: nothing in extraction, in the AI analysis, in
    the review opening or in fabrication generation creates a citation, and no route exposes
    this writer. Writing citations is a later milestone's decision, and until it is taken the
    live table stays empty and every field reads UNCITED.

    Returns the rows it sent, so a caller can chain the read that proves what was stored.
    """
    if not isinstance(snapshot, ReviewSnapshot):
        raise TypeError(
            f"snapshot must be a ReviewSnapshot (got {type(snapshot).__name__}); citations "
            "belong to a recorded review and this function records none of them"
        )
    if not isinstance(review_package_id, str) or not review_package_id.strip():
        raise ValueError("review_package_id must be a non-empty str.")
    rows = snapshot.citation_rows_for(review_package_id)
    if not rows:
        # Nothing to write is not a write. A call that would insert zero rows is a no-op
        # rather than a round trip that could report success for an empty set.
        return ()
    params = {
        "p_project_id": snapshot.project_id,
        "p_review_revision": snapshot.review_revision,
        "p_review_package_id": review_package_id,
        "p_citations": _json_text([dict(row) for row in rows]),
    }
    response = _client(client).rpc(CITATION_WRITER_FUNCTION, params)
    try:
        response.execute()
    except Exception as exc:
        refusal = _citation_refusal_from(exc)
        if refusal is None:
            raise
        raise refusal from exc
    return rows


def record_project_review(
    binding,
    workflow,
    *,
    evidence_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence_run_ids: Sequence[str] = (),
    client=None,
) -> ReviewSnapshot:
    """Records one genuine workflow revision against the project this request authorized.

    The four steps are J21's, in J21's order, and every one of them can refuse:

      1. the project is the BOUND one — the caller's authorization proof, never a project id
         passed in a request body;
      2. the previous revision is the project's own highest recorded one;
      3. the previous revision's ITEMS are read back, once, from that same revision (J77);
      4. J21 builds the snapshot from the workflow and from those items (and refuses a
         mismatch, a gap, a duplicate package id, a divergence from the previous revision's
         connections or recorded candidate origins, or a state it cannot represent);
      5. the database writes it, or refuses the whole revision.

    Step 3 is the one J77 adds, and it is the only read on this path that is not the head.
    It is performed against the revision step 2 already returned rather than against a
    second read of the head, so the items the builder compares against and the revision it
    expects are guaranteed to be the same revision.

    `binding` is a J19 `ProjectReviewBinding`. Taking the binding rather than a project id is
    the authorization boundary expressed as a type: a binding exists only for a project an
    authenticated reviewer was ALLOWED, so there is no argument to this function that could
    make it write against someone else's project. No owner, user or role is read from
    anywhere — J19 remains the single authorization path.
    """
    binding_type = _binding_type()
    if not isinstance(binding, binding_type):
        raise TypeError(
            f"binding must be a ProjectReviewBinding (got {type(binding).__name__}); a "
            "review revision is recorded against a project this request is authorized for, "
            "and a binding is the proof of that authorization"
        )
    project_id = _project_id(binding.project_id)
    previous_revision = latest_recorded_revision(project_id, client=client)

    # J77 — the previous revision's own items, read ONCE and read from the SAME
    # `previous_revision` the builder is told to expect. Reading it from a second head
    # would let the two describe different revisions, which is the confusion this
    # argument exists to prevent.
    #
    # An unreadable previous revision is a REFUSAL and never a fallback to "no prior": a
    # snapshot built as though this were revision 0 would re-address every untouched
    # connection from the current reconstruction, which is precisely what J77 stops.
    prior_items: dict[str, ReviewSnapshotItem] | None = None
    if previous_revision is not None:
        prior = load_review_snapshot(project_id, previous_revision, client=client)
        if prior is None:
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_UNREPRESENTABLE,
                f"the project's review chain reaches revision {previous_revision} but that "
                "revision could not be read back; the candidate origins it recorded cannot "
                "be carried, and a snapshot recorded without them would state that every "
                "connection this revision did not process is addressed at the reading that "
                "stands for its page today, which is not what the chain recorded",
            )
        prior_items = {item.review_package_id: item for item in prior.items}

    snapshot = build_review_snapshot(
        workflow,
        project_id=project_id,
        evidence_rows=evidence_rows,
        evidence_run_ids=evidence_run_ids,
        previous_revision=previous_revision,
        prior_items=prior_items,
    )
    return persist_review_snapshot(snapshot, client=client)


def _binding_type():
    """J19's binding class, imported on use.

    `app.production_review.binding` pulls the review UI in behind it, and this module is
    imported by processes that never render a page.
    """
    from app.production_review.binding import ProjectReviewBinding

    return ProjectReviewBinding
