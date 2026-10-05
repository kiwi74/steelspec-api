"""
Milestone J21 — CONNECTION-REVIEW SNAPSHOT DATA MODEL (DESIGN + PROOF, UNWIRED).

J20 proved that no persisted row set can be turned back into the 7AK
connection-review contract: 18 of the contract's 26 fields are absent from
the database, 5 are lossy, 2 are only reconstructable, and 1 is verbatim.
This module is the design answer to that finding, in executable form.

WHAT THIS MODULE IS

  A pure, database-free model of the MINIMUM persisted state that
  preserves the review contract exactly, plus the replay that rebuilds
  the contract from that state. It describes two tables, the evidence
  fingerprint a snapshot is anchored to, the staleness rule, and the
  refusals that keep a snapshot from being invented, renumbered or
  partially written.

WHAT THIS MODULE IS NOT

  - It is NOT the production persistence layer. It opens no database,
    sends no HTTP, reads no environment, touches no filesystem, and is
    imported by no route and by no production module. No migration has
    been written: the object names below are a design, and the SQL that
    would create them exists nowhere in `supabase/`.
  - It is NOT a reviewer of anything. It decides nothing, re-runs no
    gate, re-derives no provenance, and never promotes a persisted row
    into review state: a snapshot is recorded from a genuine 7AJ
    workflow state, and from nothing else.
  - It is NOT a second copy of the engineering evidence. The only
    engineering values it names live in the two review tables, and the
    engineering tables stay exactly where they are: a snapshot is a
    snapshot OF the review, never a source for the engineering data.

THE FIDELITY RULE (why the payload columns are `json`, not `jsonb`)

  The contract's own display strings are built with `repr()`: an AI bolt
  reading is `repr(AIExtractedBolt(...))`, a malformed reading is
  `f"{name}: {value!r}"`, a human answer is `repr(resolution.answer)`.
  `repr()` of a dict depends on the dict's KEY ORDER, and `repr()` of a
  tuple is not `repr()` of a list. A `jsonb` column normalises object key
  order and drops duplicate keys, so a payload stored as `jsonb` cannot
  reproduce those strings: the review would be silently re-rendered.
  Every verbatim payload column is therefore `json` (text-preserving),
  and a human answer payload is stored with an explicit tuple tag so a
  tuple stays a tuple across the round trip. This is proven, not
  asserted: the design tests rebuild every contract from the stored rows
  and compare the rebuilt contracts to the genuine ones byte for byte.

THE STATE RULE (nothing here collapses into anything else)

  - A payload column is NEVER SQL NULL: absence is expressed INSIDE the
    payload (a JSON `null` field, an empty array), so "the AI said
    nothing", "the AI said null" and "the AI said something" can never
    become the same stored value.
  - A gate decision, an output status, a verification status, a human
    answer and a provenance label are recorded verbatim and are never
    recomputed at read time: they are the outcomes of decisions that
    already happened.
  - A display string that is a pure function of persisted state
    (`requires_action`, `available_actions`, the blocker/warning
    presentations, the summaries) is re-derived, and only those.

THE WRITE AND READ PATHS

  WRITE: the snapshot is recorded by the process that holds the genuine
  7AJ workflow state — the review process itself. It records the
  workflow's OWN revision, and only for revisions it actually observed,
  so a project's recorded chain has no hole. The read server never
  writes a review.

  READ: authenticated user -> `projects.user_id` (J19, unchanged) ->
  authorized project -> this table's own `project_id` -> the rows at the
  highest recorded revision -> the replayed contract. A project with no
  snapshot has NO review state, and nothing here can invent one.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_DECISIONS,
)
from app.cad_engine.candidate_origin_address import CandidateOrigin, origin_of, with_origin
from app.cad_engine.connection_review_package import (
    AIExtractedBolt,
    AIExtractedPlate,
    AIExtractedWeld,
    ENGINEERING_FIELDS,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUSES
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUSES
from app.cad_engine.exception_resolution import (
    ExceptionResolutionTask,
    HumanResolution,
)
from app.cad_engine.project_connection_review import build_review_report
from app.cad_engine.project_workflow import (
    ProjectConnectionState,
    ProjectWorkflowState,
    _counts,
)
from app.cad_engine.review_contract import (
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    CITATION_DERIVATION,
    CITATION_KINDS,
    CITATION_SOURCE,
    REVIEW_CONTRACT_SCOPE_STATEMENT,
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    ConnectionReviewContract,
    ProjectReviewContract,
    ReviewEvidenceInfo,
    _citation_infos,
    _display,
    _finding_info,
    _provenance_infos,
    _task_info,
    field_standings,
)

SNAPSHOT_SCOPE_STATEMENT = (
    "This module describes the MINIMUM persisted state that preserves one project's 7AK "
    "connection-review contract at one workflow revision: a snapshot header (the project, "
    "7AJ's revision, the evidence the review was computed from, the project's own gate "
    "decision) and one item per connection carrying that connection's recorded review state "
    "verbatim. It is a design and its proofs; it writes nothing, reads nothing, and is "
    "bound to no route. It claims no approval, no completeness, no fabrication readiness "
    "and no verification of anything."
)


# ======================================================================================
# 1. The two tables, as data — so the report, the tests and the implementation milestone
#    cannot drift apart.
# ======================================================================================
# J20 proposed six tables (items / readings / findings / provenance / tasks / outputs).
# Five of them are 1:1 attribute groups of one connection at one revision, with no
# lifecycle of their own and no consumer that queries them apart from their connection —
# so they are columns of the item row. Two tables remain: the revision header and the item,
# and the header is the piece the proposal was missing.

SNAPSHOT_TABLE = "connection_review_snapshots"
ITEM_TABLE = "connection_review_items"

SNAPSHOT_PRIMARY_KEY = ("project_id", "review_revision")
ITEM_PRIMARY_KEY = ("project_id", "review_revision", "review_package_id")
ITEM_FOREIGN_KEY = (("project_id", "review_revision"), SNAPSHOT_PRIMARY_KEY)

# (column, SQL type, nullable, why it exists)
SNAPSHOT_TABLE_COLUMNS: tuple[tuple[str, str, bool, str], ...] = (
    ("project_id", "uuid", False,
     "The project the review belongs to. It is the SAME relationship J19 authorizes on "
     "(`projects.user_id`), one hop away, and it is the contract's own `project_id`."),
    ("review_revision", "integer", False,
     "7AJ's revision, recorded verbatim — the workflow's own counter (0 at start, +1 per "
     "resolve/refresh). It is the contract's own `revision`, so no second revision system "
     "is introduced."),
    ("evidence_identity", "json", False,
     "The fingerprint of the persisted evidence this review was computed from, plus its "
     "kind and its table list. Recorded once per revision, never recomputed."),
    ("evidence_run_ids", "json", False,
     "The `analysis_runs.id` values that existed when the snapshot was recorded, verbatim "
     "(reached through `analysis_runs.drawing_set_id` -> `drawing_sets.project_id`, the "
     "existing J16 path). Provenance only: it is NEVER the staleness authority (a corrected "
     "value adds no run), and it is NOT the anchor, because a run row records a READ EVENT "
     "— including the parse-failed attempts that wrote no evidence — and never denotes a "
     "set of evidence rows."),
    ("project_status", "text", False,
     "The composite fabrication-output gate's package decision at this revision (7AJ's "
     "`project_decision`), recorded verbatim and never re-run at read time."),
    ("recorded_at", "timestamptz", False,
     "When the snapshot row was written (database default). The only column that is not a "
     "7AK field: it is temporal provenance, read by nothing the contract exposes."),
)

# (column, SQL type, nullable, why it exists)
ITEM_TABLE_COLUMNS: tuple[tuple[str, str, bool, str], ...] = (
    ("project_id", "uuid", False, "The revision header's project; half of the composite key."),
    ("review_revision", "integer", False, "The revision header's revision; the other half."),
    ("review_package_id", "text", False,
     "The contract's `package_id` (e.g. RP-0001), verbatim. 7X derives it from submission "
     "order, so the item order is recoverable from the ids themselves — no order column."),
    ("connection_id", "text", True,
     "The contract's `connection_id`: the reviewed connection's identity, NULL until a "
     "human supplies one. NULL = none was ever supplied."),
    ("decision", "text", False,
     "The 7Z gate's decision for this connection (AUTO / CONFIRM / REVIEW), verbatim."),
    ("output_status", "text", True,
     "The dispatch outcome, NULL = the connection was never dispatched — DISTINCT from "
     "every status value, so 'not attempted' never becomes a status."),
    ("verification_status", "text", True,
     "The artifact verification outcome, NULL = never verified. Same distinction."),
    ("last_processed_revision", "integer", True,
     "7AJ's revision at which this connection was processed, NULL = never processed. "
     "Recorded as a number, not a foreign key: the workflow's revision semantics stay 7AJ's, "
     "and this table never renumbers them."),
    ("blocker_codes", "json", False,
     "The 7Z blocker codes, in the workflow's own order, verbatim. The blocker presentation "
     "(title/message/severity/field/task) is re-derived from these and this item's tasks."),
    ("warning_codes", "json", False,
     "The 7Z per-outcome warning codes, verbatim, in order."),
    ("ai_readings", "json", False,
     "The AI extraction layer verbatim: member references, bolts, plates, welds, the stated "
     "connection type, confidence and material, plus the malformed and unrecognised "
     "readings. The contract's `repr()` display strings are re-derived from this, never "
     "stored."),
    ("evidence", "json", False,
     "Where the reading came from: source drawing id, drawing number, source page and the "
     "detail/grid references (raw values; the contract's display form is re-derived)."),
    ("provenance", "json", False,
     "The review report's field-provenance labels (AI_EXTRACTED / HUMAN_REVIEWED / "
     "HUMAN_SUPPLEMENTED), verbatim. NEVER regenerated: a label records a human decision."),
    ("tasks", "json", False,
     "The 7AC resolution tasks and their recorded human answers, verbatim, in task order. "
     "This is where the human review decisions are preserved."),
    ("generated_files", "json", False,
     "The artifact paths this connection produced, in order, verbatim."),
)

# Every payload column above is `json`, and the CHECK vocabularies are the owning modules'
# own constants, so a vocabulary change here is a test failure, never a silent divergence.
PAYLOAD_COLUMNS: tuple[str, ...] = tuple(
    name for name, sql_type, _, _ in SNAPSHOT_TABLE_COLUMNS + ITEM_TABLE_COLUMNS
    if sql_type == "json"
)
ITEM_CHECK_VOCABULARY: dict[str, tuple[str, ...]] = {
    "decision": AUTOMATION_DECISIONS,
    "output_status": OUTPUT_STATUSES,
    "verification_status": VERIFICATION_STATUSES,
}

# ======================================================================================
# 1b. The citation table (J66) — the third table of the same review chain.
# ======================================================================================
# J21 folded five of J20's six proposed tables away because each was a 1:1 attribute group of
# one connection with no lifecycle and no query of its own. A CITATION is the case that
# argument does not reach, and J65 decided it on that ground rather than by taste:
#
#   * it is not 1:1 — one connection has at most one provenance label per field but ANY
#     number of citations per field, so there is no column it could be folded into;
#   * it has a query of its own — "where did this field's content come from, and is that
#     evidence still the evidence?" — which json cannot answer without reading every item;
#   * the association it must carry is STRUCTURAL. It has to be impossible to record a
#     citation of one project's connection that points at another project's reading, and a
#     json payload can promise that only in Python, where nothing enforces it. A composite
#     foreign key and an ownership trigger can.
#
# A citation is an ADDRESS, never a value: there is no value, chosen_value, confidence, rank,
# is_primary, winner or resolved column here, and no document_role or drawing_number either.
# The content stays in exactly one copy, in the reading the citation names.

CITATION_TABLE = "connection_review_item_citations"

# (project_id, review_revision, review_package_id) is not a standalone key: it is the ITEM's
# key, which is what makes the citation's owner a fact rather than a claim.
CITATION_PRIMARY_KEY = (
    "project_id", "review_revision", "review_package_id", "field_name", "ordinal",
)
CITATION_ITEM_FOREIGN_KEY = (
    ("project_id", "review_revision", "review_package_id"), ITEM_PRIMARY_KEY,
)
# The cited page reading, identified exactly as J23 identifies it, and the cited occurrence,
# identified exactly as J28 identifies it. The occurrence foreign key is INERT while its three
# coordinates are NULL, which is the wanted behaviour: it is enforced when it is stated.
CITATION_CAPTURE_FOREIGN_KEY = ("drawing_id", "page_number", "analysis_run_id")
CITATION_OCCURRENCE_FOREIGN_KEY = CITATION_CAPTURE_FOREIGN_KEY + (
    "annotation_x", "annotation_y", "extractor_version",
)
CITATION_FIELD_NAMES: tuple[str, ...] = ENGINEERING_FIELDS

# (column, SQL type, nullable, why it exists)
CITATION_TABLE_COLUMNS: tuple[tuple[str, str, bool, str], ...] = (
    ("project_id", "uuid", False,
     "The review item's project: the citation's owner, and half of the composite item key."),
    ("review_revision", "integer", False,
     "The review item's revision, recorded verbatim. The citation has no revision of its "
     "own — it is the ITEM's revision, so the two cannot drift apart."),
    ("review_package_id", "text", False,
     "The reviewed connection whose field this citation accounts for."),
    ("field_name", "text", False,
     "WHICH engineering field. The vocabulary is ENGINEERING_FIELDS, the review package's own "
     "authoritative list; an unrecognised field fails the CHECK rather than becoming a "
     "silent category."),
    ("ordinal", "integer", False,
     "RECORDING ORDER ONLY, 1-based, and never a priority: it orders the citations of one "
     "field and is compared with no other quantity. It is part of the primary key because "
     "two citations of one field are two facts."),
    ("citation_kind", "text", False,
     "How the field's content relates to the cited reading: SOURCE (taken from it) or "
     "DERIVATION (computed or interpreted from it). A closed pair with no quality, no "
     "confidence and no precedence."),
    ("document_id", "uuid", True,
     "The document the cited reading was taken over, or NULL when it predates document "
     "identity or was taken without it. NULL is NOT a wildcard: it matches nothing."),
    ("drawing_id", "uuid", False,
     "The cited reading's drawing. Part of the reading's identity, never a display name."),
    ("page_number", "integer", False,
     "The cited reading's page, 1-based, exactly as the evidence tables record it."),
    ("analysis_run_id", "uuid", False,
     "The cited reading's attempt. It is what makes a re-read a NEW READING rather than a "
     "collision, which is why a citation is never reducible to a page number."),
    ("annotation_x", "numeric(9,2)", True,
     "The cited occurrence's device x, or NULL when the citation names the page reading and "
     "not one particular occurrence on it."),
    ("annotation_y", "numeric(9,2)", True,
     "The cited occurrence's device y (top-left convention, as the extractor reports it)."),
    ("extractor_version", "text", True,
     "The rule set the occurrence was read under. Present exactly when the positions are: a "
     "position without its rule set is not an occurrence identity."),
    ("anchor", "json", False,
     "The locator WITHIN the cited reading — the drawn token, the detail reference, the "
     "nearby text. An attribute of the ADDRESS, never the field's value."),
    ("recorded_at", "timestamptz", False,
     "When the citation row was written (database default). Temporal provenance, read by "
     "nothing the contract exposes."),
)

CITATION_CHECK_VOCABULARY: dict[str, tuple[str, ...]] = {
    "field_name": CITATION_FIELD_NAMES,
    "citation_kind": CITATION_KINDS,
}

# No extra index: the read path asks for one revision's citations (the primary key's leading
# columns) and nothing else, and no cited row is ever deleted (every foreign key restricts).
CITATION_DECLARED_INDEXES: tuple[str, ...] = ()

CITATION_APPEND_ONLY_RULE = (
    "no UPDATE, no DELETE and no upsert, for every role: a later reading of a field is a NEW "
    "row with the next ordinal, never an edit of an old one, and a recorded citation is never "
    "removed. There is no mutable-table exception here"
)

CITATION_OWNERSHIP_RULE = (
    "a citation can never cross a project boundary. The drawing a reading was taken from "
    "carries no project_id of its own, so the foreign keys alone cannot connect the cited "
    "reading to the citation's project; a BEFORE "
    "INSERT guard reads the cited capture's, occurrence's and document's own project_id and "
    "refuses when one is not the citation's. A citation from project A to evidence belonging "
    "to project B is impossible to insert"
)

# The codes the citation table's own triggers and constraints raise. They are the DATABASE's
# words, preserved rather than retranslated, so a caller reads the same refusal the table
# made. None of them is decided in Python: an unknown field, a crossed project boundary and a
# rewrite are all refused by the table itself, where the fact is.
CITATION_REFUSED_EVIDENCE_UNKNOWN = "CITATION_REFUSED_EVIDENCE_UNKNOWN"
CITATION_REFUSED_FOREIGN_EVIDENCE = "CITATION_REFUSED_FOREIGN_EVIDENCE"
CITATION_REFUSED_APPEND_ONLY = "CITATION_REFUSED_APPEND_ONLY"

CITATION_RLS_RULE = (
    "RLS enabled with NO policy for anon/authenticated, exactly as the two tables above: the "
    "citations are unreadable except through the service-role server, which re-enforces "
    "projects.user_id through the review item. No reviewer role, no ownership column here, no "
    "membership table"
)

# No extra index is declared: the read path performs exactly two lookups and the primary
# keys serve both — (a) one snapshot of one project at one revision, (b) the highest
# revision of one project (the PK's leading column is project_id).
DECLARED_INDEXES: tuple[str, ...] = ()

APPEND_ONLY_RULE = (
    "no UPDATE, no DELETE and no upsert: a later review is a new (project_id, "
    "review_revision) row, an evidence change makes an old snapshot STALE, and a stale "
    "snapshot stays exactly as it was recorded"
)

RLS_RULE = (
    "RLS enabled with NO policy for anon/authenticated: the tables are unreadable except "
    "through the service-role server, which re-enforces projects.user_id (J19's single "
    "authorization path). No reviewer role, no second ownership column, no membership table."
)

EXISTING_PROJECT_RULE = (
    "a project with no (project_id, review_revision) row has NO persisted connection-review "
    "state; the read path shows that absence and refuses, and never rebuilds a review from "
    "connections, steel_members, review_items or a project id alone"
)

# What J20 proposed, and what happens to it. Every entry is a decision with a reason.
EXISTING_TABLE_REUSE: tuple[tuple[str, str], ...] = (
    ("connection_review_items",
     "KEPT as the item table: the one proposed table whose row identity (project, revision, "
     "package id) is real and whose fields are the contract's own per-connection state."),
    ("connection_review_readings",
     "OMITTED — folded into `connection_review_items.ai_readings`. A reading is a 1:1 "
     "attribute group of one connection at one revision; a table would add rows without "
     "adding a lifecycle, and would let a reading and its connection disagree."),
    ("connection_review_findings",
     "OMITTED — folded into `blocker_codes` / `warning_codes`. Findings are 7Z outputs "
     "already carried per connection, and their presentation is re-derived from the codes."),
    ("connection_review_provenance",
     "OMITTED — folded into `connection_review_items.provenance`. A provenance label has no "
     "identity, no lifecycle and no query of its own; a separate table would give a label a "
     "second place to live."),
    ("connection_review_tasks",
     "OMITTED — folded into `connection_review_items.tasks`. A task is meaningless without "
     "its connection and revision: it has no identity outside them."),
    ("connection_review_outputs",
     "OMITTED — folded into `generated_files` (the artifact paths, verbatim) plus the item's "
     "verification status. The artifact itself is stored by the existing generation path: "
     "this model never becomes a second copy of it."),
    ("connection_review_snapshots",
     "ADDED — the revision header the proposal was missing. It exists because the evidence "
     "identity, the run ids and the project's own gate decision are PROJECT-level facts at "
     "one revision: storing them on every item row would duplicate them N times and let the "
     "rows of one revision disagree with each other."),
)

# Tables this design deliberately does NOT reuse (and never becomes a second copy of).
NO_REUSE_RULES: tuple[tuple[str, str], ...] = (
    ("connections",
     "NOT a reuse and NOT a source: a persisted `connections` row carries normalised "
     "engineering values with no revision, no review decision and no recorded link to a "
     "review package id (J20: the live project's rows do not match its capture and nothing "
     "relates them). Reusing it would mean guessing which row a review item is."),
    ("review_items",
     "NOT a reuse: `review_items` is the intake ledger (project, item_type, item_id, status) "
     "with no payload and no revision. Promoting it into review state is exactly the "
     "historical reconstruction J21 forbids."),
    ("steel_members / bolt_groups / connection_plates / weld_details",
     "NEVER a source: this model reads none of them and writes none of them. The "
     "engineering evidence stays the single copy in its own tables."),
)


# ======================================================================================
# 2. The evidence fingerprint.
# ======================================================================================
# The tables whose rows define "the evidence this project has": the tables the existing J16
# readers already treat as a project's evidence.
EVIDENCE_TABLES: tuple[str, ...] = ("steel_members", "connections")
EVIDENCE_KIND = "PERSISTED_PROJECT_EVIDENCE"

# The canonicalisation follows the rule J6 established for reference-data provenance (rows
# sorted, compact JSON, sha256, a domain separator naming the tables) with ONE deliberate
# difference: here the keys ARE sorted, because a database row's key order is not
# information. In the payload columns it IS information, which is why those columns must
# preserve it. Same rule family, opposite treatment, for a reason.
FINGERPRINT_RULE = (
    "sha256 over the canonical text of the project's persisted evidence rows: each row as "
    "JSON with sorted keys and compact separators, the rows sorted, the evidence kind and "
    "the table names as the domain separator. No timestamp, no client, no credential, no "
    "URL and no runtime value enters the hash — the same rows in any order give the same "
    "digest, and a changed value gives a different one."
)


def _refuse_non_json(value):
    """Refuse a value the fingerprint cannot canonicalise deterministically."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        f"the evidence fingerprint cannot canonicalise a {type(value).__name__} value; the "
        "evidence tables are read as JSON, so only JSON values are expected, and no digest "
        "may be invented for an unexpected shape"
    )


def evidence_identity(rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict:
    """
    The fingerprint of a project's persisted evidence, as a persistable object.

    `rows_by_table` must name exactly `EVIDENCE_TABLES` (an unknown table, or a table left
    out, is refused rather than treated as empty). The result is a DETACHED dict:

        {"evidence_kind": ..., "evidence_tables": [...], "evidence_digest": "<sha256>"}

    WHAT IT MEANS: "these are the rows this review was computed against." It is a change
    detector, NOT a version, NOT an approval, and NOT a claim that anything was reviewed.
    A mutation of a column no reader selects is not detected — the fingerprint is exactly
    as wide as the evidence the project's own readers read, and no wider.
    """
    if not isinstance(rows_by_table, Mapping):
        raise TypeError("rows_by_table must be a mapping of table name to rows")
    unknown = sorted(set(rows_by_table) - set(EVIDENCE_TABLES))
    if unknown:
        raise ValueError(
            f"the evidence fingerprint covers exactly {list(EVIDENCE_TABLES)}; unknown "
            f"table(s) {unknown} have no defined meaning here"
        )
    missing = [name for name in EVIDENCE_TABLES if name not in rows_by_table]
    if missing:
        raise ValueError(
            f"the evidence fingerprint needs an explicit row list for every table it "
            f"covers; no rows were supplied for {missing}, and an absent table is not an "
            "empty one"
        )

    canonical = []
    for table in EVIDENCE_TABLES:
        rows = rows_by_table[table]
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise TypeError(f"the rows of {table} must be a sequence of row mappings")
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError(f"every row of {table} must be a mapping")
            canonical.append(
                f"{table}\t"
                + json.dumps(row, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=_refuse_non_json)
            )
    canonical.sort()
    payload = "\n".join((EVIDENCE_KIND, *EVIDENCE_TABLES, *canonical))
    return {
        "evidence_kind": EVIDENCE_KIND,
        "evidence_tables": list(EVIDENCE_TABLES),
        "evidence_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


# ======================================================================================
# 3. The verbatim encoding rule.
# ======================================================================================
# A human answer or an AI reading can contain a tuple (e.g. (marks, position, attachments)),
# and `repr()` of a tuple is not `repr()` of the list a JSON round trip would return. The
# tag keeps the distinction INSIDE the payload, so nothing is collapsed into an
# approximation of itself.
TUPLE_TAG = "__steelspec_tuple__"

# The payload columns must keep object key order, because `repr()` of a dict is part of
# what the contract exposes. The writer therefore sends the encoded object as-is and never
# re-serialises with sorted keys.
SERIALISATION_RULE = (
    "payloads are sent as encoded (never with sorted keys, never normalised): key order is "
    "part of the recorded value because the contract's own display strings expose it"
)


def _encode(value, *, where: str):
    if isinstance(value, tuple):
        return {TUPLE_TAG: [_encode(entry, where=where) for entry in value]}
    if isinstance(value, list):
        return [_encode(entry, where=where) for entry in value]
    if isinstance(value, Mapping):
        if TUPLE_TAG in value:
            raise ValueError(
                f"{where}: a mapping carries the reserved key {TUPLE_TAG!r}; the tuple tag "
                "cannot be smuggled into a payload, and no recorded value is ever rewritten"
            )
        return {key: _encode(entry, where=f"{where}.{key}") for key, entry in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(
        f"{where}: a {type(value).__name__} value has no faithful JSON representation in "
        "this model; it is refused rather than stringified, because a stringified value "
        "would be a different value"
    )


def _decode(value):
    if isinstance(value, list):
        return [_decode(entry) for entry in value]
    if isinstance(value, Mapping):
        if set(value) == {TUPLE_TAG}:
            return tuple(_decode(entry) for entry in value[TUPLE_TAG])
        return {key: _decode(entry) for key, entry in value.items()}
    return value


def _payload(value, *, where: str):
    """The persistable form of a payload: encoded, then proven JSON-serialisable.

    The `json.dumps` here is the model's own fidelity check: a payload that cannot survive
    the round trip is refused at write time, never stored as something else.
    """
    encoded = _encode(value, where=where)
    json.dumps(encoded, ensure_ascii=False)
    return encoded


# ======================================================================================
# 4. Refusals — the snapshot is recorded or it is not; it is never approximated.
# ======================================================================================
SNAPSHOT_REFUSED_PROJECT_UNKNOWN = "SNAPSHOT_REFUSED_PROJECT_UNKNOWN"
SNAPSHOT_REFUSED_PROJECT_MISMATCH = "SNAPSHOT_REFUSED_PROJECT_MISMATCH"
SNAPSHOT_REFUSED_NO_REVIEW_LAYER = "SNAPSHOT_REFUSED_NO_REVIEW_LAYER"
SNAPSHOT_REFUSED_REVISION_GAP = "SNAPSHOT_REFUSED_REVISION_GAP"
SNAPSHOT_REFUSED_UNREPRESENTABLE = "SNAPSHOT_REFUSED_UNREPRESENTABLE"
SNAPSHOT_REFUSED_DUPLICATE_PACKAGE_ID = "SNAPSHOT_REFUSED_DUPLICATE_PACKAGE_ID"

# J77 — the ONE refusal this milestone adds, and it is deliberately one rather than a
# family. It is raised when the previous revision and this revision do not describe the
# same connections at the same recorded addresses, which is a single failure with two
# faces: a connection the previous revision recorded that this workflow does not have (or
# the reverse), and a candidate origin that the previous revision recorded and this
# reconstruction no longer derives. Both are the same statement — the record and the
# reconstruction have diverged — and the `statement` names which one, so a caller that
# only reads codes still loses nothing.
SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED = "SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED"


class SnapshotRefused(ValueError):
    """
    A review snapshot was not recorded. Every refusal carries a reason code and a statement
    of what would have had to be invented to proceed, so a caller can never mistake a
    refusal for a silently reduced snapshot.
    """

    def __init__(self, code: str, statement: str):
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


# ======================================================================================
# 5. The snapshot itself.
# ======================================================================================
@dataclass(frozen=True)
class ReviewSnapshotItem:
    """One connection's recorded review state at one revision — verbatim, no decisions."""

    review_package_id: str
    connection_id: str | None
    decision: str
    output_status: str | None
    verification_status: str | None
    last_processed_revision: int | None
    blocker_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    ai_readings: dict
    evidence: dict
    provenance: dict
    tasks: tuple[dict, ...]
    generated_files: tuple[str, ...]

    def __post_init__(self) -> None:
        # Payloads are snapshotted at creation (the 7AC precedent): a later mutation of the
        # caller's dicts cannot change what a recorded review said.
        for name in ("ai_readings", "evidence", "provenance"):
            object.__setattr__(self, name, copy.deepcopy(getattr(self, name)))
        object.__setattr__(self, "tasks", copy.deepcopy(self.tasks))


@dataclass(frozen=True)
class ReviewFieldCitation:
    """
    ONE recorded citation: an ADDRESS for one engineering field of one reviewed connection,
    decoded from its row and never repaired.

    It carries no value. The field's content is not here and is not copied here — it stays in
    exactly one place, in the reading this citation names, and a reader that wants it reads
    the evidence the citation points at. That is the whole difference between a citation and
    a second copy of the answer.

    `document_id` is None when the cited reading predates document identity or was taken
    without it; None matches nothing and is never a wildcard. The three occurrence columns
    are all present or all None. `ordinal` is the recording order within its field and is
    never a priority. `project_id` and `review_revision` are the ITEM's, carried so a row is
    self-describing; they are not a second revision system.
    """

    project_id: str
    review_revision: int
    review_package_id: str
    field_name: str
    ordinal: int
    citation_kind: str
    document_id: str | None
    drawing_id: str
    page_number: int
    analysis_run_id: str
    annotation_x: object
    annotation_y: object
    extractor_version: str | None
    anchor: object


@dataclass(frozen=True)
class ReviewSnapshot:
    """
    One project's review state at one 7AJ revision: the header facts (project, revision,
    the evidence the review was computed from, the project's own gate decision), one
    item per connection in the workflow's own connection order, and the citations recorded
    against those connections' fields.
    """

    project_id: str
    review_revision: int
    project_status: str
    evidence_identity: dict
    evidence_run_ids: tuple[str, ...]
    items: tuple[ReviewSnapshotItem, ...]
    recorded_at: str | None = None
    # J66, defaulted so every existing construction of this object is unchanged: a revision
    # built by the writer carries NO citations (nothing in this system invents one — J66's
    # own brief forbids every automatic writer), and a revision recorded before the citation
    # table existed reads back with none either. An empty tuple here means "none are
    # recorded", which the contract states as UNCITED rather than as an absence of a claim.
    citations: tuple[ReviewFieldCitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_identity", copy.deepcopy(self.evidence_identity))
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "citations", tuple(self.citations))

    def citations_for(self, review_package_id: str) -> tuple[ReviewFieldCitation, ...]:
        """One connection's citations, in recorded row order — the contract orders them."""
        return tuple(
            citation for citation in self.citations
            if citation.review_package_id == review_package_id
        )

    def citation_rows_for(self, review_package_id: str) -> tuple[dict, ...]:
        """One connection's citations as the rows a writer persists: plain data, in order.

        `recorded_at` is left to the database default — this model never reads a clock — and
        `project_id`/`review_revision` come from the item the citation belongs to, never from
        an argument that could disagree with it.
        """
        return tuple(
            {
                "field_name": citation.field_name,
                "ordinal": citation.ordinal,
                "citation_kind": citation.citation_kind,
                "document_id": citation.document_id,
                "drawing_id": citation.drawing_id,
                "page_number": citation.page_number,
                "analysis_run_id": citation.analysis_run_id,
                "annotation_x": citation.annotation_x,
                "annotation_y": citation.annotation_y,
                "extractor_version": citation.extractor_version,
                "anchor": copy.deepcopy(citation.anchor),
            }
            for citation in self.citations_for(review_package_id)
        )

    def to_rows(self) -> tuple[dict, tuple[dict, ...]]:
        """The persistable rows: one header row and its item rows, as plain data.

        `recorded_at` is left to the database default: this model never reads a clock.
        """
        header = {
            "project_id": self.project_id,
            "review_revision": self.review_revision,
            "evidence_identity": copy.deepcopy(self.evidence_identity),
            "evidence_run_ids": list(self.evidence_run_ids),
            "project_status": self.project_status,
        }
        rows = tuple(
            {
                "project_id": self.project_id,
                "review_revision": self.review_revision,
                "review_package_id": item.review_package_id,
                "connection_id": item.connection_id,
                "decision": item.decision,
                "output_status": item.output_status,
                "verification_status": item.verification_status,
                "last_processed_revision": item.last_processed_revision,
                "blocker_codes": list(item.blocker_codes),
                "warning_codes": list(item.warning_codes),
                "ai_readings": copy.deepcopy(item.ai_readings),
                "evidence": copy.deepcopy(item.evidence),
                "provenance": copy.deepcopy(item.provenance),
                "tasks": copy.deepcopy(list(item.tasks)),
                "generated_files": list(item.generated_files),
            }
            for item in self.items
        )
        return header, rows


def _reading_row(obj, keys: tuple[str, ...], *, where: str) -> dict:
    """One AI reading entry: exactly the fields 7W carries, each encoded verbatim."""
    return {key: _payload(getattr(obj, key), where=f"{where}.{key}") for key in keys}


def _task_payload(task, *, where: str) -> dict:
    resolution = task.resolution
    return _payload({
        "task_id": task.task_id,
        "task_type": task.task_type,
        "blocker_codes": list(task.blocker_codes),
        "question": task.question,
        "current_ai_value": task.current_ai_value,
        "answer_type": task.answer_type,
        "allowed_choices": list(task.allowed_choices),
        "evidence_requirement": task.evidence_requirement,
        # J79 — the one engineering field this task addresses, or None. Written verbatim:
        # it is the task model's own statement and is never re-derived from the task type
        # here, so a value the model did not state cannot appear in a stored payload.
        "field_name": task.field_name,
        "status": task.status,
        "resolution": None if resolution is None else {
            "task_id": resolution.task_id,
            "task_type": resolution.task_type,
            "answer_type": resolution.answer_type,
            "answer": resolution.answer,
            "evidence": resolution.evidence,
        },
    }, where=where)


def _evidence_with_origin(
    evidence: dict[str, Any], origin: CandidateOrigin | None, *, where: str
) -> dict[str, Any]:
    """The item's evidence with the candidate's origin carried into it (J72).

    An origin is never built here and never completed here: a caller either holds the
    address the candidate was read at or passes None, and a half-stated one is refused
    rather than filled in from the reading it sits beside. `with_origin` leaves the two
    keys ABSENT when there is none.
    """
    if origin is not None and not isinstance(origin, CandidateOrigin):
        raise ValueError(
            f"{where}.evidence: a candidate origin is a CandidateOrigin or None; "
            f"{type(origin).__name__} is neither, and an address is never assembled here."
        )
    return with_origin(evidence, origin)


def _item_from_stages(
    state: ProjectConnectionState, *, extraction, report, group, where: str,
    origin: CandidateOrigin | None = None,
) -> ReviewSnapshotItem:
    """One item, built from the genuine stage results — the only way an item can exist.

    `origin` (J72) is the candidate's own address — the analysis run that read its page
    and its ZERO-BASED position in that page's `raw_connections` — carried in from the
    candidate this item is built from. It is recorded inside `evidence` verbatim, and a
    caller that has none leaves the two keys ABSENT rather than recording a null, so a
    payload written before J72 stays exactly as it was and keeps loading.
    """
    readings = {
        "member_references": _payload(
            tuple(extraction.connected_member_references), where=f"{where}.member_references"
        ),
        "bolts": [
            _reading_row(bolt, ("quantity", "size", "grade", "extra"),
                         where=f"{where}.bolts[{index}]")
            for index, bolt in enumerate(extraction.bolts)
        ],
        "plates": [
            _reading_row(plate, ("type", "thickness_mm", "width_mm", "depth_mm", "extra"),
                         where=f"{where}.plates[{index}]")
            for index, plate in enumerate(extraction.plates)
        ],
        "welds": [
            _reading_row(weld, ("type", "size_mm", "extra"),
                         where=f"{where}.welds[{index}]")
            for index, weld in enumerate(extraction.welds)
        ],
        "connection_type": _payload(extraction.generic_connection_type,
                                    where=f"{where}.connection_type"),
        "confidence": _payload(extraction.confidence, where=f"{where}.confidence"),
        "material": _payload(extraction.material, where=f"{where}.material"),
        "malformed_fields": _payload(dict(extraction.malformed_fields),
                                     where=f"{where}.malformed_fields"),
        "unrecognised_fields": _payload(dict(extraction.unrecognised_fields),
                                        where=f"{where}.unrecognised_fields"),
    }
    return ReviewSnapshotItem(
        review_package_id=state.package_id,
        connection_id=state.connection_id,
        decision=state.decision,
        output_status=state.output_status,
        verification_status=state.verification_status,
        last_processed_revision=state.last_processed_revision,
        blocker_codes=tuple(state.blockers),
        warning_codes=tuple(state.warnings),
        ai_readings=readings,
        evidence=_evidence_with_origin({
            "source_drawing_id": _payload(extraction.source_drawing_id,
                                          where=f"{where}.evidence.source_drawing_id"),
            "drawing_number": _payload(extraction.drawing_number,
                                       where=f"{where}.evidence.drawing_number"),
            "source_page": _payload(extraction.source_page,
                                    where=f"{where}.evidence.source_page"),
            "detail_reference": _payload(extraction.detail_reference,
                                         where=f"{where}.evidence.detail_reference"),
            "grid_reference": _payload(extraction.grid_reference,
                                       where=f"{where}.evidence.grid_reference"),
        }, origin, where=where),
        provenance=_payload(dict(report.provenance), where=f"{where}.provenance"),
        tasks=tuple(
            _task_payload(task, where=f"{where}.tasks[{index}]")
            for index, task in enumerate(group.tasks)
        ),
        generated_files=tuple(state.generated_files),
    )


def _carried_origin(
    prior_origin: CandidateOrigin | None,
    derived_origin: CandidateOrigin | None,
    *,
    touched: bool,
    where: str,
) -> CandidateOrigin | None:
    """The origin ONE connection records at this revision (J77).

    Three cases, and nothing stands between them:

      * a PRIOR origin was recorded. It is carried, and it must equal what this
        reconstruction derives — an address is never replaced by a later one, because a
        revision that recorded RUN-A and whose evidence was rebuilt from RUN-B would be a
        payload that contradicts itself. A divergence is refused rather than resolved.
        (Equality is a sufficient condition for candidate equivalence: two equal origins
        name the same immutable capture row and the same position in its array. The
        converse does not hold — two different runs can state byte-identical candidates —
        so refusing is deliberately conservative, and a re-read that changed nothing is
        refused too.)
      * no prior origin, and this is the revision that genuinely processed the connection
        (`last_processed_revision == revision`). The derived origin is recorded. This is
        the ONLY point at which an origin is acquired, because it is the only point at
        which the connection is actually evaluated.
      * no prior origin, and the connection was untouched. NOTHING is recorded: the two
        keys stay ABSENT, exactly as J72 leaves them. Manufacturing an address here would
        state that a revision which recorded none had recorded one.

    The prior origin arrives already read by J72's own `origin_of`, so a payload that
    half-states an address is refused by the rule that owns that vocabulary rather than
    reinterpreted here.
    """
    if prior_origin is None:
        return derived_origin if touched else None
    if prior_origin != derived_origin:
        derived = (
            "no origin at all"
            if derived_origin is None
            else f"{derived_origin.analysis_run_id!r} at index {derived_origin.candidate_index}"
        )
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED,
            f"{where}.evidence: the previous revision recorded the candidate origin "
            f"{prior_origin.analysis_run_id!r} at index {prior_origin.candidate_index}, and "
            f"this reconstruction derives {derived}. The recorded address is the address "
            "this revision answers, and it is never silently replaced by the reading that "
            "stands for the page today — the two may name different candidates even when "
            "their contents agree, so what would have to be invented to proceed is which "
            "of them this revision's evidence describes.",
        )
    return prior_origin


def build_review_snapshot(
    workflow,
    *,
    project_id: str,
    evidence_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence_run_ids: Sequence[str] = (),
    previous_revision: int | None = None,
    prior_items: Mapping[str, ReviewSnapshotItem] | None = None,
) -> ReviewSnapshot:
    """
    Builds the persistable snapshot of one genuine `ProjectWorkflowState`.

    The revision recorded is the workflow's OWN revision, verbatim. The chain is contiguous
    by construction: `previous_revision` is the revision of the project's last recorded
    snapshot (None when there is none), and a workflow whose revision would leave a gap is
    REFUSED — a chain with a hole cannot answer "what was the review state at revision N?",
    and renumbering the workflow's revision to fit would be a second revision system.

    Nothing here reads a database, a clock, an environment variable or a file. The evidence
    fingerprint is computed from `evidence_rows`, which the caller reads through the
    project's existing readers, and the run ids are recorded verbatim.

    `prior_items` (J77) is the PREVIOUS revision's items, keyed by `review_package_id`,
    read by the caller from the recorded chain. It is optional and defaults to None, which
    is the revision-0 path and behaves exactly as it did before this milestone.

    Supplying it is what makes a recorded candidate origin survivable. Every connection's
    item is re-emitted by every revision, and the collection those items are built from is
    the CURRENT reconstruction — so without this argument an untouched connection would be
    re-addressed from the reading that stands for its page today, which may not be the
    reading the revision being extended recorded. With it:

      * the prior revision and this workflow must name exactly the same package ids, or
        the snapshot is refused. A package that has appeared or disappeared is a
        lifecycle change this layer is not entitled to decide, and it invents no deletion
        semantics to cover one.
      * a connection whose origin the prior revision recorded carries that origin, and a
        reconstruction that derives a different one is refused (see `_carried_origin`).
      * a connection whose origin the prior revision did not record acquires one only if
        this is the revision that genuinely processed it; otherwise the keys stay ABSENT.

    A prior revision that cannot be read back is a refusal at the caller, never a
    fallback to None here: reconstructing "no prior" from an unreadable prior is exactly
    the substitution this argument exists to prevent.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__}); a "
            "review snapshot is recorded from a genuine workflow revision and from nothing "
            "else — there is no way to build one from a project id"
        )
    if not isinstance(project_id, str) or not project_id:
        raise TypeError("project_id must be a non-empty str")
    if workflow.project_id is None:
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_PROJECT_UNKNOWN,
            "the workflow carries no project id, so a review snapshot would have to guess "
            "which project it belongs to",
        )
    if workflow.project_id != project_id:
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_PROJECT_MISMATCH,
            f"the workflow belongs to {workflow.project_id!r} while the snapshot was asked "
            f"for {project_id!r}; a review state is never recorded under another project",
        )
    if workflow.collection is None or workflow.exception_package is None:
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_NO_REVIEW_LAYER,
            "this workflow state carries no review collection or no exception-resolution "
            "contract, so the review state it would persist does not exist",
        )

    revision = workflow.revision
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_UNREPRESENTABLE,
            f"the workflow reports revision {revision!r}; a review revision is a "
            "non-negative integer (7AJ's own counter)",
        )
    expected = 0 if previous_revision is None else previous_revision + 1
    if revision != expected:
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_REVISION_GAP,
            f"the project's recorded review chain is at {previous_revision!r} and this "
            f"workflow is at revision {revision}; recording it would leave a gap, and "
            "renumbering it would be a second revision system",
        )

    collection = workflow.collection
    exception_package = workflow.exception_package
    package_ids = [connection.package_id for connection in workflow.connections]
    if len(set(package_ids)) != len(package_ids):
        raise SnapshotRefused(
            SNAPSHOT_REFUSED_DUPLICATE_PACKAGE_ID,
            f"the workflow lists a package id twice ({package_ids}); one connection has one "
            "review state per revision",
        )

    # J77 — the prior revision and this one must name the SAME connections before a single
    # item is built. This is checked AFTER the revision arithmetic above, deliberately: a
    # caller whose chain moved under it must still get the gap refusal it already handles,
    # not this one, and a prior revision this workflow is not the successor of is a gap
    # rather than a divergence.
    if prior_items is not None:
        for package_id, prior in prior_items.items():
            if not isinstance(prior, ReviewSnapshotItem):
                raise TypeError(
                    f"prior_items[{package_id!r}] is {type(prior).__name__}, not a review "
                    "item; a previous revision is handed over as it was recorded, never as "
                    "something that merely resembles an item"
                )
        appeared = sorted(set(package_ids) - set(prior_items))
        disappeared = sorted(set(prior_items) - set(package_ids))
        if appeared or disappeared:
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED,
                f"the previous revision and this workflow do not describe the same "
                f"package ids: {appeared} are in this workflow and not in the previous "
                f"revision, {disappeared} are in the previous revision and not in this "
                "workflow. A package appearing or disappearing is a lifecycle change "
                "this layer does not decide, and it invents no deletion semantics to cover "
                "one; the snapshot is refused rather than recorded against a different "
                "project picture.",
            )

    items = []
    for index, state in enumerate(workflow.connections):
        where = f"{project_id}@{revision}.{state.package_id}"
        candidate = next(
            (c for c in collection.candidates if c.review_package_id == state.package_id), None
        )
        group = next(
            (g for g in exception_package.connection_tasks
             if g.review_package_id == state.package_id), None
        )
        if candidate is None or group is None:
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_NO_REVIEW_LAYER,
                f"{where}: the workflow names a connection with no review candidate or no "
                "exception contract; a partial snapshot is never recorded",
            )
        record = workflow.connection_records[index]
        package = (
            record.rerun_outcome.rebuilt_package
            if record.rerun_outcome is not None
            else candidate.package
        )
        # The origin the candidate was read at, carried verbatim from the collection —
        # never re-derived from this loop's position, which is submission order, nor from
        # the rebuilt package's own numbering. J77 adds the other half: when a previous
        # revision exists, what IT recorded is what this revision answers, so the derived
        # address is either confirmed against it or the snapshot is refused.
        origin = candidate.origin
        if prior_items is not None:
            origin = _carried_origin(
                origin_of(prior_items[state.package_id].evidence),
                candidate.origin,
                touched=state.last_processed_revision == revision,
                where=where,
            )
        try:
            items.append(_item_from_stages(
                state, extraction=package.extraction,
                report=build_review_report(package), group=group, where=where,
                origin=origin,
            ))
        except SnapshotRefused:
            raise
        except (ValueError, TypeError) as exc:
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_UNREPRESENTABLE, f"{where}: {exc}"
            ) from exc

    return ReviewSnapshot(
        project_id=project_id,
        review_revision=revision,
        project_status=workflow.project_decision,
        evidence_identity=evidence_identity(evidence_rows),
        evidence_run_ids=tuple(str(run_id) for run_id in evidence_run_ids),
        items=tuple(items),
    )


# ======================================================================================
# 6. The replay — the contract rebuilt from the stored rows alone.
# ======================================================================================
def snapshot_item_from_row(row: Mapping[str, Any]) -> ReviewSnapshotItem:
    """One item as it comes back from the database — decoded, never repaired."""
    if not isinstance(row, Mapping):
        raise TypeError("a snapshot item row must be a mapping")
    missing = [name for name, _, _, _ in ITEM_TABLE_COLUMNS if name not in row]
    if missing:
        raise ValueError(
            f"the item row is missing {missing}; a partially read review state is never "
            "rendered, because a missing column and an empty one would look the same"
        )
    return ReviewSnapshotItem(
        review_package_id=row["review_package_id"],
        connection_id=row["connection_id"],
        decision=row["decision"],
        output_status=row["output_status"],
        verification_status=row["verification_status"],
        last_processed_revision=row["last_processed_revision"],
        blocker_codes=tuple(_decode(row["blocker_codes"])),
        warning_codes=tuple(_decode(row["warning_codes"])),
        ai_readings=_decode(row["ai_readings"]),
        evidence=_decode(row["evidence"]),
        provenance=_decode(row["provenance"]),
        tasks=tuple(_decode(row["tasks"])),
        generated_files=tuple(_decode(row["generated_files"])),
    )


def citation_from_row(row: Mapping[str, Any]) -> ReviewFieldCitation:
    """One citation as it comes back from the database — decoded, never repaired, never
    filled in. A missing column is refused for the same reason an item's is: a missing
    column and a NULL column would otherwise look the same."""
    if not isinstance(row, Mapping):
        raise TypeError("a citation row must be a mapping")
    missing = [name for name, _, _, _ in CITATION_TABLE_COLUMNS if name not in row]
    if missing:
        raise ValueError(
            f"the citation row is missing {missing}; a partially read citation is never "
            "presented, because a missing column and an empty one would look the same"
        )
    return ReviewFieldCitation(
        project_id=row["project_id"],
        review_revision=row["review_revision"],
        review_package_id=row["review_package_id"],
        field_name=row["field_name"],
        ordinal=row["ordinal"],
        citation_kind=row["citation_kind"],
        document_id=row["document_id"],
        drawing_id=row["drawing_id"],
        page_number=row["page_number"],
        analysis_run_id=row["analysis_run_id"],
        # Verbatim, NOT `_decode`d: a coordinate is not one of the encoded payloads — the
        # tuple tag exists for recorded human/AI values, and applying it here would be
        # normalising a value that was never encoded.
        annotation_x=row["annotation_x"],
        annotation_y=row["annotation_y"],
        extractor_version=row["extractor_version"],
        anchor=_decode(row["anchor"]),
    )


def snapshot_from_rows(
    header: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
) -> ReviewSnapshot:
    """A snapshot as it comes back from the two tables — assembled, never synthesised."""
    return ReviewSnapshot(
        project_id=header["project_id"],
        review_revision=header["review_revision"],
        project_status=header["project_status"],
        evidence_identity=_decode(header["evidence_identity"]),
        evidence_run_ids=tuple(_decode(header["evidence_run_ids"])),
        items=tuple(snapshot_item_from_row(row) for row in items),
    )


def _state_from_item(item: ReviewSnapshotItem) -> ProjectConnectionState:
    """The item's own 7AJ projection — the same nine fields a workflow state carries."""
    return ProjectConnectionState(
        package_id=item.review_package_id,
        connection_id=item.connection_id,
        decision=item.decision,
        output_status=item.output_status,
        verification_status=item.verification_status,
        generated_files=tuple(item.generated_files),
        blockers=tuple(item.blocker_codes),
        warnings=tuple(item.warning_codes),
        last_processed_revision=item.last_processed_revision,
    )


def _task_from_row(row: Mapping[str, Any]) -> ExceptionResolutionTask:
    resolution = row["resolution"]
    return ExceptionResolutionTask(
        task_id=row["task_id"],
        task_type=row["task_type"],
        blocker_codes=tuple(row["blocker_codes"]),
        question=row["question"],
        current_ai_value=_decode(row["current_ai_value"]),
        answer_type=row["answer_type"],
        allowed_choices=tuple(row["allowed_choices"]),
        evidence_requirement=row["evidence_requirement"],
        # J79 — read back through `.get`, so a payload recorded before this key existed reads
        # as None (the task addressed exactly what it used to) rather than failing to load.
        # A payload that states a value outside ENGINEERING_FIELDS is REFUSED by the task
        # model itself; it is never dropped, coerced or read as "no field".
        field_name=row.get("field_name"),
        status=row["status"],
        resolution=None if resolution is None else HumanResolution(
            task_id=resolution["task_id"],
            task_type=resolution["task_type"],
            answer_type=resolution["answer_type"],
            answer=_decode(resolution["answer"]),
            evidence=_decode(resolution["evidence"]),
        ),
    )


def connection_contract_from_item(
    snapshot: ReviewSnapshot, item: ReviewSnapshotItem
) -> ConnectionReviewContract:
    """
    ONE item's 7AK contract, rebuilt from the stored state.

    The blocker, warning and resolution-task presentations come from 7AK's OWN helpers
    (`_finding_info`, `_task_info`), deliberately: they are pure functions of the persisted
    codes and tasks, and copying them here would be a second copy of 7AK's presentation
    that could drift. Everything that is not a pure presentation — the gate decisions, the
    statuses, the AI readings, the provenance labels and the human answers — is read from
    the snapshot and never recomputed.
    """
    state = _state_from_item(item)
    task_rows = tuple(_task_from_row(row) for row in item.tasks)
    tasks = tuple(_task_info(task) for task in task_rows)
    readings = item.ai_readings

    # J66: this connection's citations, and the standing each of its fields derives from
    # them. Both are read from the snapshot's own recorded rows and neither is computed from
    # anything else — a revision with no citations yields an empty tuple and SEVEN UNCITED
    # standings, which is what the live band says too, so the two bands agree by construction
    # rather than by convention.
    citations = snapshot.citations_for(item.review_package_id)

    # `_finding_info` reads the 7AC tasks (their blocker codes); `_item_summary` reads the
    # contract's own task presentations. Both come from 7AK — one replay helper per use.
    blockers = tuple(
        _finding_info(code, SEVERITY_BLOCKING, task_rows) for code in state.blockers
    )
    warnings = tuple(
        _finding_info(code, SEVERITY_WARNING, task_rows) for code in state.warnings
    )
    requires_action = (
        state.decision == AUTOMATION_DECISION_REVIEW
        and state.last_processed_revision is None
    )
    malformed = readings["malformed_fields"]
    unrecognised = readings["unrecognised_fields"]

    return ConnectionReviewContract(
        package_id=state.package_id,
        connection_id=state.connection_id,
        project_id=snapshot.project_id,
        revision=snapshot.review_revision,
        decision=state.decision,
        output_status=state.output_status,
        verification_status=state.verification_status,
        requires_action=requires_action,
        blockers=blockers,
        warnings=warnings,
        ai_member_references=tuple(readings["member_references"]),
        ai_bolt_readings=tuple(
            repr(AIExtractedBolt(**{key: _decode(row[key]) for key in
                                    ("quantity", "size", "grade", "extra")}))
            for row in readings["bolts"]
        ),
        ai_plate_readings=tuple(
            repr(AIExtractedPlate(**{key: _decode(row[key]) for key in
                                     ("type", "thickness_mm", "width_mm", "depth_mm", "extra")}))
            for row in readings["plates"]
        ),
        ai_weld_readings=tuple(
            repr(AIExtractedWeld(**{key: _decode(row[key]) for key in
                                    ("type", "size_mm", "extra")}))
            for row in readings["welds"]
        ),
        ai_malformed_readings=tuple(
            f"{name}: {_decode(malformed[name])!r}" for name in sorted(malformed)
        ),
        ai_unrecognised_readings=tuple(
            f"{name}: {_decode(unrecognised[name])!r}" for name in sorted(unrecognised)
        ),
        ai_connection_type=_display(_decode(readings["connection_type"])),
        ai_confidence=_display(_decode(readings["confidence"])),
        ai_material=_decode(readings["material"]),
        evidence=ReviewEvidenceInfo(
            source_drawing_id=item.evidence["source_drawing_id"],
            drawing_number=item.evidence["drawing_number"],
            source_page=item.evidence["source_page"],
            detail_reference=_display(item.evidence["detail_reference"]),
            grid_reference=_display(item.evidence["grid_reference"]),
        ),
        provenance=_provenance_infos(dict(item.provenance)),
        tasks=tasks,
        available_actions=(ACTION_REVIEW, ACTION_RESOLVE) if requires_action else (),
        generated_files=tuple(state.generated_files),
        last_processed_revision=state.last_processed_revision,
        summary=_item_summary(state, blockers, tasks, requires_action),
        field_citations=_citation_infos(citations),
        field_standings=field_standings(citations),
    )


def _item_summary(state, blockers, tasks, requires_action: bool) -> str:
    """7AK's own per-connection summary composition, re-derived from persisted state.

    Byte-identical to `build_connection_review_contract`'s composition and pinned by the
    design tests; the implementation milestone should lift it into one shared place rather
    than keep two copies.
    """
    parts = [f"{state.package_id}: decision {state.decision}"]
    if state.output_status is not None:
        parts.append(f"output {state.output_status}")
    if state.verification_status is not None:
        parts.append(f"verification {state.verification_status}")
    parts.append("action required" if requires_action else "no action required")
    if blockers:
        parts.append(f"{len(blockers)} blocker(s): " + ", ".join(b.code for b in blockers))
    if tasks:
        parts.append(f"{len(tasks)} task(s); {sum(1 for t in tasks if t.resolved)} resolved")
    return "; ".join(parts)


def project_contract_from_snapshot(snapshot: ReviewSnapshot) -> ProjectReviewContract:
    """
    The whole project's 7AK contract at the snapshot's revision, rebuilt from stored state.

    Counts are recomputed over the items with 7AJ's own `_counts` rule (they are sums over
    recorded decisions, not judgements); the project status is the header's recorded gate
    decision; item order is the workflow's own connection order, recoverable from the
    submission-derived package ids.
    """
    items = tuple(
        connection_contract_from_item(snapshot, item)
        for item in sorted(snapshot.items, key=lambda entry: entry.review_package_id)
    )
    awaiting = tuple(item for item in items if item.requires_action)
    actions = (ACTION_REVIEW, ACTION_REFRESH) if awaiting else (ACTION_REFRESH,)

    review, confirm, auto, verified = _counts(
        tuple(_state_from_item(item) for item in snapshot.items)
    )
    lines = [
        f"project = {snapshot.project_id or 'unknown'}; revision = {snapshot.review_revision}; "
        f"status = {snapshot.project_status}",
        f"review = {review}; verified = {verified}; auto = {auto}; "
        f"confirmation = {confirm}; blocked = {review + confirm}",
    ]
    for item in items:
        lines.append(
            f"{item.package_id}: {item.decision}"
            + (" — action required" if item.requires_action else "")
        )
    lines.append(REVIEW_CONTRACT_SCOPE_STATEMENT)

    return ProjectReviewContract(
        project_id=snapshot.project_id,
        revision=snapshot.review_revision,
        project_status=snapshot.project_status,
        review_count=review,
        verified_count=verified,
        auto_count=auto,
        confirmation_count=confirm,
        blocked_count=review + confirm,
        items=items,
        available_actions=actions,
        summary="\n".join(lines),
    )


# ======================================================================================
# 7. Staleness and the current-state rule.
# ======================================================================================
def evidence_identity_matches(
    recorded: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    """
    Whether the evidence a snapshot recorded is still the project's evidence.

    The DIGEST is the authority. An identity of a different kind is refused rather than
    compared: two different identity shapes are not two versions of one answer.
    """
    if not isinstance(recorded, Mapping) or not isinstance(current, Mapping):
        raise TypeError("both evidence identities must be mappings")
    if recorded.get("evidence_kind") != current.get("evidence_kind"):
        raise ValueError(
            f"these are different kinds of evidence identity "
            f"({recorded.get('evidence_kind')!r} vs {current.get('evidence_kind')!r}); they "
            "are not two versions of one answer"
        )
    return recorded.get("evidence_digest") == current.get("evidence_digest")


def snapshot_is_stale(snapshot: ReviewSnapshot, *, current_evidence_identity) -> bool:
    """
    True when the project's persisted evidence is no longer the evidence this snapshot
    recorded. A stale snapshot is still readable and is NEVER rewritten: the read path
    presents it as stale and refuses to act on it (7AJ's own stance on an older revision).
    """
    return not evidence_identity_matches(snapshot.evidence_identity, current_evidence_identity)


def latest_snapshot(snapshots: Sequence[ReviewSnapshot]) -> ReviewSnapshot | None:
    """
    The project's CURRENT review state: the snapshot at the highest recorded revision, or
    None when the project has no persisted review state at all.

    None is the whole of Decision B. An existing project has no review state, and nothing
    in this module can invent one: no function here takes a project id, a connection row or
    a review item and returns review state.
    """
    if not snapshots:
        return None
    return max(snapshots, key=lambda snapshot: snapshot.review_revision)
