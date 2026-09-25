"""
J20 — CONNECTION-REVIEW PERSISTENCE AND PRODUCTION BINDING: WHAT WAS FOUND.

The milestone asked for one thing: an authenticated, authorized real project
loading its connection-review state from persisted production truth (or from
state a deterministic reconstruction can rebuild from persisted production
truth) through the real HTTP path, into the EXISTING 7AK contract and the
existing view model and renderer — without a second contract, a second auth
system, a second authorization rule, or a second engineering truth.

This module pins the discovery that the brief required before any of that could
be written, and the honest outcome of it. It is READ-ONLY: it writes no
migration, adds no route, and changes no production file. Three things are
proven here.

1. WHAT THE CONTRACT NEEDS. `build_connection_review_contract(workflow,
   package_id)` takes a `ProjectWorkflowState` (7AJ) and reads 26 fields off it.
   Every one of those 26 is classified below against the LIVE schema as
   PERSISTED_VERBATIM, RECONSTRUCTABLE, LOSSY or ABSENT, with the persisted
   column (or the reason there is none) recorded per field. The counts are
   pinned, so the day 7AK's shape or the schema changes, this trace has to be
   redone rather than silently inherited.

2. WHY NO DETERMINISTIC RECONSTRUCTION EXISTS. The genuine connection writer
   (`app/pipeline.py::_persist_connections`) is driven here over the
   repository's REAL Arkles Strand AI extraction — not a synthetic stand-in —
   through a recording repository double, and the rows it actually tries to
   persist are asserted. The AI's silence becomes a default (`quantity: null`
   -> `1`, `grade: null -> "?"`), its own member marks become (at best) resolved
   member uuids, its stated connection type becomes a normalised token from a
   closed vocabulary, and the malformed/unrecognised readings it will be
   reviewed through are produced AFTER persistence by a review layer the
   pipeline never imports. So the database does not hold the review's inputs,
   and re-running the gates over it would read `"?"` and `1` as if the drawing
   had stated them. That is a new engineering conclusion from lossy evidence and
   is exactly the duplicate-truth failure the brief forbids.

3. WHAT A MIGRATION WOULD HAVE TO HOLD. Because 23 of the 26 fields have no
   faithful persisted source — including `decision`, `output_status`,
   `verification_status`, `blockers`, `tasks`, `provenance`,
   `generated_files`, `last_processed_revision` and the two derived fields —
   the brief's MIGRATION GATE applies: STOP and report the minimum data model
   instead of writing it. That model is stated as data at the bottom of this
   module, per field, so nothing about it is implicit.

The evidence for (2) and (3) is real, not constructed. The live project holding
the real Arkles Strand PDF (persisted status "review") has two connection rows,
both from page 7; one of them persists the AI's bolt reading as
`bolt_size: "304 SS 12mm Bolts"`, `bolt_grade: "?"`, `quantity: 1` and a
description reading `"Nonex 304 SS 12mm Bolts GrNone"` — the AI's silence
rendered into the database as if it were a reading. Neither row links a member,
and the 80 live `review_items` rows include not one carrying `reviewed_by` or
`reviewed_at`. Those live facts are asserted by the opt-in tests at the end,
which skip — never substitute — when the live configuration or the evidence is
absent. They are also a warning in their own right: the repository's capture
file holds THREE page-7 candidates with `M12` bolts and confidences 65/65/55,
so the persisted rows are not even a faithful image of the capture — nothing
relates a row to a candidate, and a reconstruction that assumed otherwise would
be wrong before it began.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import types
from pathlib import Path

import pytest

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.project_connection_review import build_project_review_summary
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.project_workflow import start_project_workflow
from app.cad_engine.review_contract import (
    ConnectionReviewContract,
    build_connection_review_contract,
    build_project_review_contract,
)
from app.validation.project_status import (
    COMPLETE_REVIEW_STATUSES,
    STATUS_DONE,
    STATUS_REVIEW,
    derive_project_status,
    review_status_of,
)
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests.test_project_extraction_intake import CAPTURE_PATH, real_known_marks
from tests.test_real_world_exception_proof import (
    ARKLES_REAL_AI_EXTRACTION,
    HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
    HUMAN_SUPPLIED_MEMBER_ROWS,
    HUMAN_SUPPLIED_SECTION_MATCHER,
)

REPO = Path(__file__).resolve().parent.parent

# The identity this proof's hermetic half runs under. Deliberately NOT the
# live project's id: nothing here may be mistaken for the live project's state.
PROJECT_ID = "PROJ-7J20"
SOURCE_DRAWING_ID = "ARKLES-STRAND"
DRAWING_ID = "dr-j20"

needs_real_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason="the real Arkles connection extraction is not present; the persistence "
           "trace is skipped rather than run over a fabricated capture",
)

# The `production` and `live` fixtures (and the `_RecordingRepository` double
# below) are J4's, reused rather than restated: one production configuration,
# imported one way, in every proof.
production = j4.production
live = j4.live


# =============================================================================
# 1. THE CLASSIFICATION OF EVERY FIELD THE CONTRACT READS
# =============================================================================
# Buckets. One of these is recorded per `ConnectionReviewContract` field, so a
# field can never be silently unclassified.
PERSISTED_VERBATIM = "PERSISTED_VERBATIM"   # a column holds exactly the value the contract reads
RECONSTRUCTABLE = "RECONSTRUCTABLE"         # recomputable exactly from persisted columns
LOSSY = "LOSSY"                             # something related persists, but not this value
ABSENT = "ABSENT"                           # no persisted source exists at all

#: field name -> (bucket, what the persisted schema holds, or why nothing can).
RECONSTRUCTION = {
    "package_id": (
        ABSENT,
        "The 7X collection's own ordinal key (RP-0001..), derived from candidate index. "
        "`connections.id` is a storage uuid and `review_items.item_id` repeats it; no column "
        "carries the candidate's position in the review collection. Guessing it from "
        "created_at insertion order would be inference, not reconstruction.",
    ),
    "connection_id": (
        ABSENT,
        "The REVIEWED connection's engineering identity from `state.connection_id` (None for "
        "an AI-only candidate, because no review has supplied one). Nothing persisted records "
        "whether an identity was ever supplied or refused; the row's own uuid is a storage key "
        "the contract never exposes.",
    ),
    "project_id": (
        PERSISTED_VERBATIM,
        "`connections.project_id` (the workflow's own project_id, written verbatim by the writer).",
    ),
    "revision": (
        ABSENT,
        "7AJ's optimistic-concurrency revision (0 at start, +1 per resolution). No table counts "
        "resolutions; `review_items` is never updated and carries no revision.",
    ),
    "decision": (
        ABSENT,
        "The 7Z/7AB automation-gate decision (AUTO/CONFIRM/REVIEW). The persisted "
        "`connections.review_status` is the WRITER's own low-confidence rule "
        "('extracted'/'review_required'), which is a different question from the gate's "
        "decision; reading it as the decision would be a second, contradicting engineering rule.",
    ),
    "output_status": (
        ABSENT,
        "7AE's fabrication-output permission. No column, no table, no row.",
    ),
    "verification_status": (
        ABSENT,
        "7AG's verification of the produced artifact. No column, no table, no row.",
    ),
    "requires_action": (
        ABSENT,
        "Derived as decision == REVIEW and last_processed_revision is None. Both inputs are "
        "absent, so the derivation has nothing to stand on.",
    ),
    "blockers": (
        ABSENT,
        "The 7AB blocker codes plus the task each is addressed by. The AUTOMATION_BLOCKER_* "
        "vocabulary appears in no table; `connections.warnings` is a free-text column the "
        "connection writer never sets.",
    ),
    "warnings": (
        ABSENT,
        "Same vocabulary problem as `blockers`; the warning codes are not stored anywhere.",
    ),
    "ai_member_references": (
        LOSSY,
        "The AI's own member MARK STRINGS. Persistence resolves them to member uuids via "
        "`connection_members` and drops every mark that does not resolve, so the strings, their "
        "order, and the unmatched marks are gone — and a connection with zero links is "
        "indistinguishable from one the AI gave no members for.",
    ),
    "ai_bolt_readings": (
        LOSSY,
        "`repr()` of the AI's bolt readings. `bolt_groups` stores bolt_size/bolt_grade/quantity "
        "with the writer's defaults (`or \"?\"`, `or 1`), so an absent reading is persisted as a "
        "stated-looking value and the reading object does not survive.",
    ),
    "ai_plate_readings": (
        LOSSY,
        "`repr()` of the AI's plate readings. `connection_plates` keeps thickness/grade as NULL "
        "when the drawing did not state them (fail-closed, Milestone J8B), but `plate_type` is "
        "defaulted to 'plate' and the reading object does not survive.",
    ),
    "ai_weld_readings": (
        LOSSY,
        "`repr()` of the AI's weld readings. `weld_details` keeps size/location/length but "
        "defaults `weld_type` to 'fillet'; the reading object does not survive.",
    ),
    "ai_malformed_readings": (
        ABSENT,
        "Produced by the review layer's own adapter (`ai_connection_to_extraction`) when a "
        "reading is present but unusable. It is created AFTER persistence — `app/pipeline.py` "
        "does not import the review layer at all (asserted below) — so no row can carry it.",
    ),
    "ai_unrecognised_readings": (
        ABSENT,
        "Same as `ai_malformed_readings`: created at review time, never at extraction time, so "
        "persistence has no opportunity to hold it.",
    ),
    "ai_connection_type": (
        LOSSY,
        "The AI's own stated type. Persistence normalises it into a closed vocabulary "
        "(bolted/welded/bolted_and_welded/unspecified), so the AI's token is unrecoverable and "
        "'unspecified' conflates 'the AI stated nothing' with 'the token was not in the list'.",
    ),
    "ai_confidence": (
        RECONSTRUCTABLE,
        "`connections.confidence_score` holds the AI's number verbatim (and `confidence` the "
        "tier). The contract reads `_display(confidence)`, which is `repr` for a number and None "
        "for None — and a NULL score is exactly the None case.",
    ),
    "ai_material": (
        ABSENT,
        "No material column exists on `connections`, and the connection writer's payload does "
        "not include one; the AI's material designation is never persisted.",
    ),
    "evidence": (
        RECONSTRUCTABLE,
        "`connections.source_drawing_id`, `.source_page`, `.detail_reference`, "
        "`.grid_reference` plus the `drawings` join for `drawing_number` — every member of "
        "`ReviewEvidenceInfo` has its own column.",
    ),
    "provenance": (
        ABSENT,
        "The per-field provenance (AI_EXTRACTED / HUMAN_REVIEWED / HUMAN_SUPPLEMENTED) the "
        "review report carries. No table records which engineering values a human reviewed. "
        "Re-deriving it would silently re-label human-reviewed values as AI-extracted — the "
        "single most dangerous regeneration in this milestone.",
    ),
    "tasks": (
        ABSENT,
        "The 7AC exception-resolution tasks (code, question, answer type, resolution). No task "
        "table exists. `review_items` is write-only intake whose `status` is the writer's own "
        "review_status and whose `notes` the writer never sets; it holds no task and is never "
        "updated.",
    ),
    "available_actions": (
        ABSENT,
        "Derived from `requires_action`, which has no persisted source; and its action "
        "vocabulary (review/resolve) is a 7AK concept stored nowhere.",
    ),
    "generated_files": (
        ABSENT,
        "The 7AF/7AR generated artifacts. The `drawings` table describes the SOURCE drawing, "
        "not generated output; nothing records a produced file.",
    ),
    "last_processed_revision": (
        ABSENT,
        "The revision at which this connection was last dispatched. Not a column; no table "
        "records a per-connection processing history.",
    ),
    "summary": (
        ABSENT,
        "A rendered sentence composed from decision, output_status, verification_status, "
        "requires_action, blockers and tasks — every one of which is absent, so the sentence has "
        "nothing to be recomposed from.",
    ),
}

#: The minimum data model, per field, that a migration would have to supply.
#: Slot 1: where the value would be stored — a new object, or the EXISTING
#: columns it can already be read from (`existing:`). Slot 2: the other contract
#: fields it is derived from, when it is derived rather than stored. Exactly one
#: of the two slots is set for every field.
MINIMUM_DATA_MODEL = {
    "package_id": ("connection_review_items.review_package_id", None),
    "connection_id": ("connection_review_items.connection_identity", None),
    "project_id": ("existing: connections.project_id", None),
    "revision": ("connection_review_items.revision", None),
    "decision": ("connection_review_items.decision", None),
    "output_status": ("connection_review_items.output_status", None),
    "verification_status": ("connection_review_items.verification_status", None),
    "requires_action": (None, ("decision", "last_processed_revision")),
    "blockers": ("connection_review_findings(connection_review_item_id, code, severity)", None),
    "warnings": ("connection_review_findings(connection_review_item_id, code, severity)", None),
    "ai_member_references": ("connection_review_readings.member_references (ordered, verbatim)", None),
    "ai_bolt_readings": ("connection_review_readings.bolts (verbatim, absent distinct from null)", None),
    "ai_plate_readings": ("connection_review_readings.plates (verbatim)", None),
    "ai_weld_readings": ("connection_review_readings.welds (verbatim)", None),
    "ai_malformed_readings": ("connection_review_readings.malformed (verbatim)", None),
    "ai_unrecognised_readings": ("connection_review_readings.unrecognised (verbatim)", None),
    "ai_connection_type": ("connection_review_readings.stated_connection_type (verbatim)", None),
    "ai_confidence": ("existing: connections.confidence_score", None),
    "ai_material": ("connection_review_readings.material (verbatim)", None),
    "evidence": ("existing: connections.source_drawing_id, .source_page, "
                 ".detail_reference, .grid_reference + drawings.drawing_number", None),
    "provenance": ("connection_review_provenance(connection_review_item_id, field, label)", None),
    "tasks": ("connection_review_tasks(connection_review_item_id, task_id, code, question, "
              "answer_type, resolved, resolution)", None),
    "available_actions": (None, ("requires_action",)),
    "generated_files": ("connection_review_outputs(connection_review_item_id, path, digest)", None),
    "last_processed_revision": ("connection_review_items.last_processed_revision", None),
    "summary": (None, ("decision", "output_status", "verification_status",
                       "requires_action", "blockers", "tasks")),
}


class TestEveryFieldTheContractReadsIsClassified:
    """The trace itself, and the verdict it forces."""

    def test_the_classification_covers_exactly_the_contracts_own_fields(self):
        fields = {f.name for f in dataclasses.fields(ConnectionReviewContract)}
        assert set(RECONSTRUCTION) == fields, (
            "the J20 trace must classify every field of the 7AK contract; "
            f"unclassified={sorted(fields - set(RECONSTRUCTION))}, "
            f"stale={sorted(set(RECONSTRUCTION) - fields)}"
        )
        assert set(MINIMUM_DATA_MODEL) == fields, (
            "the minimum data model must account for every field"
        )

    def test_every_classification_names_a_bucket_and_a_reason(self):
        for name, (bucket, reason) in RECONSTRUCTION.items():
            assert bucket in {PERSISTED_VERBATIM, RECONSTRUCTABLE, LOSSY, ABSENT}, name
            assert isinstance(reason, str) and len(reason) > 40, name

    def test_the_bucket_counts_are_pinned(self):
        counted = {}
        for bucket, _ in RECONSTRUCTION.values():
            counted[bucket] = counted.get(bucket, 0) + 1
        assert counted == {
            PERSISTED_VERBATIM: 1,
            RECONSTRUCTABLE: 2,
            LOSSY: 5,
            ABSENT: 18,
        }, counted

    def test_the_minimum_data_model_accounts_for_every_field(self):
        for name, (storage, derived_from) in MINIMUM_DATA_MODEL.items():
            assert (storage is None) != (derived_from is None), name
            if derived_from is not None:
                # A derived field can only be derived from fields that themselves
                # have somewhere to come from.
                assert all(other in MINIMUM_DATA_MODEL for other in derived_from), name
                assert all(RECONSTRUCTION[other][0] != LOSSY for other in derived_from), name
            else:
                assert isinstance(storage, str) and storage, name

    def test_the_model_reuses_persisted_columns_where_it_can(self):
        """The brief's source-of-truth rule, as a checked property: only a field
        with no faithful persisted source gets a new object."""
        reused = {n for n, (s, _) in MINIMUM_DATA_MODEL.items()
                  if isinstance(s, str) and s.startswith("existing:")}
        new_objects = {n for n, (s, _) in MINIMUM_DATA_MODEL.items()
                       if isinstance(s, str) and not s.startswith("existing:")}
        derived = {n for n, (_, d) in MINIMUM_DATA_MODEL.items() if d is not None}
        assert reused == {"project_id", "ai_confidence", "evidence"}
        assert not (reused & new_objects) and not (reused & derived)
        # Everything else either needs a new object or is derived from one.
        assert len(new_objects) + len(derived) + len(reused) == len(MINIMUM_DATA_MODEL)

    def test_no_deterministic_reconstruction_is_possible(self):
        """The verdict. Not a style preference — the fields that decide whether a
        connection is safe to fabricate have no persisted source, so a
        reconstruction would have to invent them or re-derive them from lossy
        evidence."""
        deciding = (
            "decision", "output_status", "verification_status", "requires_action",
            "blockers", "warnings", "tasks", "provenance", "generated_files",
            "last_processed_revision", "available_actions",
        )
        for name in deciding:
            assert RECONSTRUCTION[name][0] == ABSENT, name
        lossy = [n for n, (b, _) in RECONSTRUCTION.items() if b == LOSSY]
        assert sorted(lossy) == [
            "ai_bolt_readings", "ai_connection_type", "ai_member_references",
            "ai_plate_readings", "ai_weld_readings",
        ], lossy
        # The two buckets together are the reason the brief's MIGRATION GATE applies.
        assert len(deciding) + len(lossy) == 16

    def test_the_gate_that_would_have_to_run_at_read_time_would_read_defaults(self, production):
        """The forbidden move, stated as the reason it is forbidden: a persisted
        `1` the drawing stated and a persisted `1` the writer supplied are the
        SAME ROW, and no column can tell them apart — so re-running the gates
        over the database would read the writer's defaults as the AI's readings.
        """
        for name in ("ai_bolt_readings", "ai_plate_readings", "ai_weld_readings"):
            assert RECONSTRUCTION[name][0] == LOSSY, name
        # The writer's fallbacks are unconditional, and write the fallback itself.
        source = inspect.getsource(production.pipeline._persist_connections)
        assert 'b.get("size") or "?"' in source
        assert 'b.get("grade") or "?"' in source
        assert 'b.get("quantity") or 1' in source
        assert 'w.get("type") or "fillet"' in source
        assert 'p.get("type") or "plate"' in source
        # ...and the schema has nowhere to record that a value was absent.
        assert not [
            column for column in LIVE_CONNECTION_TABLES["bolt_groups"]
            if any(word in column for word in ("absent", "stated", "inferred", "default", "source"))
        ]


# =============================================================================
# 2. WHAT THE GENUINE WRITER ACTUALLY PERSISTS — over the REAL capture
# =============================================================================
def _flattened(pipeline):
    """The real Arkles extraction, flattened by the pipeline's own expression.

    Pages are wrapped in `SimpleNamespace` because the capture is stored as JSON
    and `_flatten_pages` reads attributes — the same shape the vision analyzer's
    `PageExtraction` objects present to it in production.
    """
    pages = [types.SimpleNamespace(**page) for page in ARKLES_REAL_AI_EXTRACTION]
    return pipeline._flatten_pages(pages, DRAWING_ID)


def _persisted(pipeline, monkeypatch, *, member_rows=()):
    """Drives the GENUINE connection writer over the REAL capture.

    Returns the recording repository, the raw connections the writer consumed,
    and the writer's own return value — so a test can compare what went in with
    what came out.
    """
    repository = j4._RecordingRepository()
    monkeypatch.setattr(pipeline, "repo", repository)
    _, connections_raw = _flattened(pipeline)
    result = pipeline._persist_connections(
        connections_raw, project_id=PROJECT_ID, drawing_id=DRAWING_ID,
        member_rows=list(member_rows),
    )
    return repository, connections_raw, result


@needs_real_capture
class TestWhatTheWriterPersists:
    """The persisted row shape, asserted against the real extraction."""

    def test_the_capture_is_real_and_non_empty(self):
        assert ARKLES_REAL_AI_EXTRACTION, "the real capture must be present for this trace"
        assert len(ARKLES_REAL_AI_EXTRACTION) > 1

    def test_the_real_capture_contains_a_bolt_the_ai_left_silent(self, production):
        """A precondition of the loss assertions below: this must be a real fact
        about the real data, not a constructed one."""
        silent = [
            (c, b)
            for c in _flattened(production.pipeline)[1]
            for b in (c.get("bolts") or [])
            if b.get("quantity") is None and b.get("grade") is None and b.get("size")
        ]
        assert silent, "expected the real capture to contain a bolt with no stated quantity/grade"
        _, bolt = silent[0]
        assert bolt["quantity"] is None and bolt["grade"] is None and bolt["size"]

    def test_the_writer_persists_exactly_these_connection_columns(self, production, monkeypatch):
        repository, connections_raw, _ = _persisted(production.pipeline, monkeypatch)
        assert connections_raw, "the real capture must flatten to at least one connection"
        assert len(repository.connections) == len(connections_raw)
        for row in repository.connections:
            assert set(row) - {"id"} == {
                "project_id", "connection_type", "grid_reference", "detail_reference",
                "description", "confidence", "confidence_score", "source_page",
                "source_drawing_id", "extraction_method", "review_status", "notes",
            }
        # The writer names a strict subset of the live table's own columns: the
        # twelve above plus the id the database assigns. Four live columns are
        # never written by it at all.
        assert set(repository.connections[0]) <= LIVE_CONNECTION_TABLES["connections"]
        assert LIVE_CONNECTION_TABLES["connections"] - set(repository.connections[0]) == {
            "created_at", "level", "location", "warnings",
        }

    def test_no_persisted_value_states_a_gate_decision(self, production, monkeypatch):
        """The blocker/warning/decision vocabulary is the review's, not the
        database's."""
        repository, _, _ = _persisted(production.pipeline, monkeypatch)
        persisted = set()
        for row in repository.connections:
            persisted.update(v for v in row.values() if isinstance(v, str))
        for item in repository.review_items:
            persisted.update(v for v in item.values() if isinstance(v, str))
        decisions = {AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_REVIEW}
        assert not (persisted & decisions), "a decision literal reached the database"
        assert not [v for v in persisted if v.startswith("AUTOMATION_")]

    def test_the_ais_silence_is_persisted_as_a_stated_looking_default(self, production, monkeypatch):
        """The single clearest piece of evidence for this milestone."""
        repository, connections_raw, _ = _persisted(production.pipeline, monkeypatch)
        index = next(
            i for i, c in enumerate(connections_raw)
            if any(b.get("quantity") is None and b.get("grade") is None and b.get("size")
                   for b in (c.get("bolts") or []))
        )
        raw = connections_raw[index]
        bolt = next(b for b in raw["bolts"]
                    if b.get("quantity") is None and b.get("grade") is None and b.get("size"))
        rows = [b for b in repository.bolt_groups if b.get("bolt_size") == bolt["size"]]
        assert rows, "the AI's bolt size must reach its own bolt_groups row"
        row = rows[0]
        # The AI said nothing about quantity or grade. The database says 1 and "?".
        assert bolt["quantity"] is None and row["quantity"] == 1
        assert bolt["grade"] is None and row["bolt_grade"] == "?"
        # And the description the writer composes states those defaults as if the
        # drawing had read them — computed from the raw bolt, not hand-written.
        expected_bit = (
            f'{bolt.get("quantity", "?")}x {bolt.get("size", "?")} '
            f'Gr{bolt.get("grade", "?")}'
        )
        assert expected_bit in repository.connections[index]["description"]
        assert "None" in expected_bit, (
            "a present-but-null AI reading is rendered into the persisted description"
        )

    def test_the_ais_member_marks_do_not_survive_as_marks(self, production, monkeypatch):
        """With no member rows, every mark is unresolvable and is dropped — an
        unlinked connection is persisted with no trace of what it named."""
        repository, connections_raw, _ = _persisted(production.pipeline, monkeypatch)
        referenced = [m for c in connections_raw for m in (c.get("connects_members") or [])]
        assert referenced, "the real capture must name at least one member"
        assert repository.connection_member_links == []
        marks = set(referenced)
        for row in repository.connections:
            values = {v for v in row.values() if isinstance(v, str)}
            assert not (values & marks), "an AI mark string reached the database verbatim"

    def test_only_resolvable_marks_reach_the_link_table_and_only_as_ids(self, production, monkeypatch):
        _, connections_raw, _ = _persisted(production.pipeline, monkeypatch)
        referenced = [m for c in connections_raw for m in (c.get("connects_members") or [])]
        resolved = sorted({m for m in referenced if m in {"A1", "B2"}})
        member_rows = [{"mark": mark, "id": f"m-{mark}"} for mark in resolved]
        repository, _, _ = _persisted(production.pipeline, monkeypatch, member_rows=member_rows)
        assert len(repository.connection_member_links) == len(resolved)
        for link in repository.connection_member_links:
            assert link["member_id"].startswith("m-")
            assert link["member_id"] not in referenced
        # Whatever resolved, the mark STRINGS and their order are unrecoverable,
        # and the unresolved ones left no trace at all.
        assert len(referenced) >= len(resolved)

    def test_the_malformed_and_unrecognised_readings_are_not_reachable_from_persistence(self):
        """`app/pipeline.py` never imports the review layer, so the readings a
        connection is reviewed through do not exist at the moment it is
        persisted. Proven structurally: by import, and by the writer's own source.
        """
        source = (REPO / "app" / "pipeline.py").read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert not [m for m in imported if "connection_review" in m or "review_contract" in m], (
            "the production pipeline must not import the review layer; the AI's "
            "malformed/unrecognised readings are created at review time"
        )
        writer = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_persist_connections"
        )
        used = {
            node.attr if isinstance(node, ast.Attribute) else node.id
            for node in ast.walk(writer)
            if isinstance(node, (ast.Attribute, ast.Name))
        }
        assert "malformed_fields" not in used and "unrecognised_fields" not in used
        # The writer's own key set, from its source: no reading object is stored.
        assert "material" not in used

    def test_the_persisted_rows_cannot_reproduce_the_contracts_readings(self, production, monkeypatch):
        """Reading the database back gives readings the AI never gave."""
        repository, connections_raw, _ = _persisted(production.pipeline, monkeypatch)
        raw_bolts = [b for c in connections_raw for b in (c.get("bolts") or [])]
        if not raw_bolts:
            pytest.skip("the real capture states no bolt reading")
        persisted_bolts = list(repository.bolt_groups)
        assert len(persisted_bolts) == len(raw_bolts)
        silence_became_a_value = [
            (raw, stored) for raw, stored in zip(raw_bolts, persisted_bolts)
            if (raw.get("quantity") is None and stored.get("quantity") == 1)
            or (raw.get("grade") is None and stored.get("bolt_grade") == "?")
        ]
        assert silence_became_a_value, (
            "expected at least one reading where the AI's silence is persisted as a "
            "stated-looking value"
        )


# =============================================================================
# 3. THE GENUINE CONTRACT IS UNCHANGED (brief testing item A)
# =============================================================================
@pytest.fixture(scope="module")
def workflow():
    """The genuine 7AJ workflow over the real capture — the same construction the
    7AJ/7AK suites use, reused rather than restated."""
    intake = intake_page_extractions(
        list(ARKLES_REAL_AI_EXTRACTION),
        project_id=PROJECT_ID,
        source_drawing_id=SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
    )
    return start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )


@needs_real_capture
class TestTheGenuineContractIsUnchanged:
    """J20 changed no contract, no view model and no renderer. The 7AK contract
    over the real capture still behaves exactly as it did."""

    def test_the_workflow_holds_one_connection_per_real_candidate(self, workflow):
        assert len(workflow.connections) == len(workflow.collection.candidates)
        assert workflow.revision == 0
        assert len(workflow.connection_records) == len(workflow.connections)

    def test_every_connection_still_builds_its_contract(self, workflow):
        for state in workflow.connections:
            contract = build_connection_review_contract(workflow, state.package_id)
            assert isinstance(contract, ConnectionReviewContract)
            assert contract.package_id == state.package_id
            assert contract.project_id == PROJECT_ID
            assert contract.revision == 0
            assert contract.decision == state.decision
            assert contract.requires_action is (
                state.decision == AUTOMATION_DECISION_REVIEW
                and state.last_processed_revision is None
            )
            # Every one of the 26 fields is present on the object, whatever its
            # bucket above — the contract is complete; the DATABASE is what is
            # incomplete.
            assert set(RECONSTRUCTION) <= {f.name for f in dataclasses.fields(contract)}

    def test_the_project_contract_still_aggregates_its_connections(self, workflow):
        project = build_project_review_contract(workflow)
        assert len(project.items) == len(workflow.connections)

    def test_the_contracts_inputs_are_in_memory_only(self, workflow):
        """The two things the contract reads that have no persisted source at all."""
        assert workflow.collection is not None          # the 7X collection: from the capture
        assert workflow.exception_package is not None   # the 7AC contract: built from it
        assert workflow.collection.candidates
        assert workflow.exception_package.connection_tasks

    def test_the_capture_itself_is_not_persisted_anywhere(self):
        """The whole reconstruction problem, in one line: the review's inputs
        live in a file under `tests/data/`, not in a database."""
        assert CAPTURE_PATH.exists()
        summary = build_project_review_summary(
            intake_page_extractions(
                list(ARKLES_REAL_AI_EXTRACTION),
                project_id=PROJECT_ID,
                source_drawing_id=SOURCE_DRAWING_ID,
                known_member_marks=real_known_marks(),
            ).collection
        )
        assert summary.total_candidates > 0
        # ...and there is no table for it: the live section below pins the six
        # connection-review tables' own columns, none of which can hold a
        # capture, and asserts that no column in the schema names one.


# =============================================================================
# 4. NO PRODUCTION BINDING AND NO MIGRATION WERE ADDED (the STOP proof)
# =============================================================================
API_ROUTES_NOT_DECLARED_HERE = frozenset({
    "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc",
})

#: Every declared route of the production application, frozen. J20 added none.
FROZEN_ROUTES = (
    (("POST",), "/continue-extraction/{project_id}"),
    (("POST",), "/extract/{project_id}"),
    (("POST",), "/generate-report/{project_id}"),
    (("GET",), "/health"),
    (("GET",), "/production/review/{project_id}"),
    (("POST",), "/production/review/{project_id}/pages/{page_number}/retry"),
    # J25: the milestone that decided to bind. It is the READ-ONLY reconstruction
    # surface — it renders the queue and adds no route that accepts a decision, so the
    # vocabulary rule below still holds and the route carries none of its ten words.
    (("GET",), "/production/review/{project_id}/workflow"),
    (("POST",), "/retry-extraction/{project_id}"),
    ((), "/review"),
)


def _declared_routes(application):
    declared = []
    for route in application.routes:
        path = getattr(route, "path", None)
        if path is None or path in API_ROUTES_NOT_DECLARED_HERE:
            continue
        declared.append((tuple(sorted(getattr(route, "methods", None) or [])), path))
    return tuple(sorted(declared, key=lambda pair: (pair[1], pair[0])))


class TestNoProductionBindingWasAdded:
    """The brief's HONESTY RULE: a route rendering is not the milestone. These
    proofs exist so that "we added no binding" is a checked fact, not a claim."""

    def test_the_production_route_set_is_unchanged(self, production):
        assert _declared_routes(production.main.app) == FROZEN_ROUTES

    def test_no_route_touches_a_connection_review(self, production):
        """No path names a connection, a contract, a package, a decision, a
        blocker, a task or a gate."""
        forbidden = ("connection", "contract", "package", "candidate", "decision",
                     "blocker", "task", "gate", "resolve", "review-items")
        for _, path in _declared_routes(production.main.app):
            lowered = path.lower()
            assert not [word for word in forbidden if word in lowered], path

    def test_the_production_review_package_does_not_import_the_contract(self):
        """No production module reads the 7AK contract or its view model — so
        connection review cannot have been bound by this milestone."""
        directory = REPO / "app" / "production_review"
        files = sorted(p for p in directory.glob("*.py"))
        assert files, "the J19 production review package must still exist"
        for path in files:
            source = path.read_text()
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
            assert not [m for m in imported if "review_contract" in m or "review_view_model" in m], path.name
            assert "build_connection_review_contract" not in source, path.name

    def test_the_j19_binding_still_holds_three_things_and_no_workflow(self):
        """The J19 binding binds ONE project's page-exception record. It holds no
        workflow, so there is no place a connection review could have been
        smuggled in."""
        from app.production_review.binding import ProjectReviewBinding
        from app.production_review.project_read import ProjectReviewRecord

        fields = dataclasses.fields(ProjectReviewBinding)
        assert [f.name for f in fields] == ["identity", "record", "decision"]
        assert fields[1].type in (ProjectReviewRecord, "ProjectReviewRecord")

    def test_no_process_global_workflow_state_was_introduced(self):
        """The 7AJ workflow's only process-global holder was, and remains, the
        internal review UI's session module.

        J20's finding had two halves: nothing in `app/` STARTS a workflow, so no
        production module could hold one across requests. J24A changed the first half and
        not the second — `project_workflow_reconstruction` starts workflows, because that
        is what a revision-0 producer is, and it holds the state for exactly as long as it
        takes to return it to its caller. The caller is named explicitly rather than the
        assertion relaxed, so a second caller still has to be declared here, and the
        holder assertion below is unchanged: one process-global holder, still the review
        UI's, and the producer is not among them.
        """
        callers, holders = [], []
        for path in sorted((REPO / "app").rglob("*.py")):
            source = path.read_text()
            if path.name != "project_workflow.py":
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    # A CALL, not a mention in a docstring or an error message.
                    if isinstance(node, ast.Call) and (
                        (isinstance(node.func, ast.Name)
                         and node.func.id == "start_project_workflow")
                        or (isinstance(node.func, ast.Attribute)
                            and node.func.attr == "start_project_workflow")
                    ):
                        callers.append(str(path.relative_to(REPO)))
            if "bind_workflow" in source or "_current_workflow" in source:
                holders.append(str(path.relative_to(REPO)))
        assert callers == [
            # J24A: the production revision-0 producer. It builds a state and returns it;
            # it binds nothing, caches nothing and keeps no reference afterwards, which is
            # why it is absent from `holders` below.
            "app/production_review/project_workflow_reconstruction.py",
        ], callers
        assert holders == ["app/review_ui/session.py"], holders
        assert "bind_workflow" not in (
            REPO / "app" / "production_review" / "project_workflow_reconstruction.py"
        ).read_text()
        assert not (REPO / "app" / "production_review" / "session.py").exists()
        assert "bind_workflow" not in "\n".join(
            p.read_text() for p in (REPO / "app" / "production_review").glob("*.py")
        )

    def test_no_migration_was_added(self):
        """J20 authored none. The one migration added since it is J22's, which implements
        J21's accepted design and not this milestone's report; the second is J23's, which
        records the raw AI reading and likewise implements neither this milestone's report
        nor its four folded-away tables. The four are still nowhere."""
        migrations = sorted(p.name for p in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert migrations == [
            "20260924000000_j5_section_resolution_truth.sql",
            "20260924010000_j6_reference_data_identity.sql",
            "20260924020000_j8b_connection_plate_evidence_nullability.sql",
            "20260925000000_j22_connection_review_persistence.sql",
            "20260925010000_j23_page_extraction_captures.sql",
        ], migrations
        for name in migrations:
            assert "j20" not in name.lower()

    def test_the_minimum_data_model_is_reported_not_implemented(self):
        """This milestone's own finding is unchanged: the four tables it folded away —
        findings, readings, provenance, tasks, outputs — are absent from every migration.
        The remaining two names are the ones J21 designed, and they appear in exactly one
        file, J22's migration, because that is what the next milestone implemented."""
        text = "\n".join(p.read_text() for p in (REPO / "supabase").rglob("*.sql")).lower()
        for absent in (
            "connection_review_findings", "connection_review_readings",
            "connection_review_provenance", "connection_review_tasks",
            "connection_review_outputs",
        ):
            assert absent not in text, absent
        appearing_in = [
            p.name for p in (REPO / "supabase").rglob("*.sql")
            if "connection_review_items" in p.read_text().lower()
        ]
        assert appearing_in == [
            "20260925000000_j22_connection_review_persistence.sql"
        ], appearing_in

    def test_the_read_only_entry_points_of_the_contract_still_require_a_workflow(self, production):
        """The one honest boundary that does exist: no function consults a
        database behind the contract's back. A non-workflow is refused."""
        with pytest.raises(TypeError):
            build_connection_review_contract(object(), "RP-0001")


# =============================================================================
# 5. THE LIVE SCHEMA CARRIES NO SUCH STATE (opt-in; skips, never substitutes)
# =============================================================================
#: The six connection-review-relevant tables' live column sets, pinned.
LIVE_CONNECTION_TABLES = {
    "connections": {
        "confidence", "confidence_score", "connection_type", "created_at", "description",
        "detail_reference", "extraction_method", "grid_reference", "id", "level", "location",
        "notes", "project_id", "review_status", "source_drawing_id", "source_page", "warnings",
    },
    "bolt_groups": {
        "bolt_grade", "bolt_size", "columns", "connection_id", "edge_distance", "gauge",
        "id", "pitch", "quantity", "rows",
    },
    "connection_plates": {"connection_id", "depth", "grade", "id", "plate_type", "thickness", "width"},
    "weld_details": {"connection_id", "id", "length", "location", "size", "weld_type"},
    "connection_members": {"connection_id", "member_id"},
    "review_items": {
        "created_at", "id", "item_id", "item_type", "notes", "project_id",
        "reviewed_at", "reviewed_by", "status",
    },
}

#: Column names whose presence in ANY table would mean review state had been
#: persisted after all.
FORBIDDEN_COLUMN_NAMES = frozenset({
    "decision", "blockers", "warnings_by_gate", "provenance", "extraction",
    "review_contract", "available_actions", "requires_action", "generated_files",
    "last_processed_revision", "malformed_fields", "unrecognised_fields",
    "automation_decision", "output_status", "verification_status",
})


@pytest.fixture(scope="module")
def live_schema(live, production):
    """Every live table's column set, read once, read-only.

    Uses the same production client the J4 `live` fixture already established —
    no new HTTP machinery, no second connection, nothing written.
    """
    client = production.matcher_module.supabase
    tables = {}
    for name in sorted(LIVE_CONNECTION_TABLES):
        try:
            rows = client.table(name).select("*").limit(1).execute().data
        except Exception as exc:  # network/credentials unavailable in this process
            pytest.skip(f"live table {name!r} could not be read: {type(exc).__name__}")
        tables[name] = sorted(rows[0]) if rows else None
    return tables


@needs_real_capture
class TestTheLiveSchemaCarriesNoReviewState:
    def test_the_connection_review_tables_hold_exactly_these_columns(self, live_schema):
        for name, expected in LIVE_CONNECTION_TABLES.items():
            actual = live_schema[name]
            if actual is None:
                pytest.skip(f"live table {name!r} holds no row to read a column set from")
            assert actual == sorted(expected), name

    def test_no_table_carries_a_review_contract_field(self, live_schema):
        for name, columns in live_schema.items():
            if columns is None:
                continue
            assert not (set(columns) & FORBIDDEN_COLUMN_NAMES), name

    def test_no_live_connection_row_can_name_its_review_package(self, live, production):
        """The 7AK contract is addressed by `package_id` (RP-0001..). No column
        anywhere holds it."""
        client = production.matcher_module.supabase
        rows = client.table("connections").select("*").execute().data
        if not rows:
            pytest.skip("the live connections table holds no rows")
        for row in rows:
            assert not [k for k, v in row.items() if isinstance(v, str) and v.startswith("RP-0")]

    def test_review_items_is_write_only_intake_and_has_never_carried_a_reviewer(self, live, production):
        """The one table that could have been mistaken for review state: it is
        written by the pipeline at extraction time and never updated. Not one of
        its rows has ever been reviewed."""
        client = production.matcher_module.supabase
        rows = client.table("review_items").select(
            "id,item_type,status,notes,reviewed_by,reviewed_at"
        ).execute().data
        if not rows:
            pytest.skip("the live review_items table holds no rows")
        assert {row["reviewed_by"] for row in rows} == {None}
        assert {row["reviewed_at"] for row in rows} == {None}
        assert {row["notes"] for row in rows} == {None}
        assert {row["status"] for row in rows} <= {"extracted", "review_required"}
        assert {row["item_type"] for row in rows} <= {"connection", "steel_member"}

class TestJ13RemainsTheOnlyStatusAuthority:
    """Brief item G. Connection review was NOT bound, and the binding that does
    exist reads no connection-review state — proven by J13's own inputs."""

    def test_the_status_function_consults_only_persisted_evidence(self):
        """Its parameter list is the proof: no decision, no blockers, no
        provenance, no contract — nothing from the review layer's vocabulary."""
        params = set(inspect.signature(derive_project_status).parameters)
        assert params == {
            "member_review_statuses", "connection_review_statuses",
            "unmatched_sections", "warnings", "coverage",
        }, params
        # One name collides — `warnings` — and it is a collision of words, not of
        # things: J13's is the sequence of persisted warning STRINGS the project
        # row already carries; the contract's is a tuple of `ReviewBlockerInfo`
        # findings. Nothing here is a contract field's value.
        assert (params & set(RECONSTRUCTION)) == {"warnings"}, params & set(RECONSTRUCTION)
        assert "str" in str(inspect.signature(derive_project_status).parameters["warnings"].annotation)

    def test_a_review_required_connection_still_holds_the_project(self):
        assert COMPLETE_REVIEW_STATUSES == frozenset({"extracted"})
        assert review_status_of({"review_status": "extracted"}) in COMPLETE_REVIEW_STATUSES
        assert review_status_of({"review_status": "review_required"}) not in COMPLETE_REVIEW_STATUSES
        held = derive_project_status(
            member_review_statuses=["extracted"],
            connection_review_statuses=["review_required"],
            unmatched_sections=[], warnings=[],
        )
        assert held.status == STATUS_REVIEW and not held.is_done
        assert held.blockers, "a held project must state why"

    def test_the_status_derivation_has_exactly_one_definition(self):
        """One calculation, in one place — no second 'done' rule to disagree
        with it."""
        definers = []
        for path in sorted((REPO / "app").rglob("*.py")):
            for sub in ast.walk(ast.parse(path.read_text())):
                if isinstance(sub, ast.FunctionDef) and sub.name == "derive_project_status":
                    definers.append(str(path.relative_to(REPO)))
        assert definers == ["app/validation/project_status.py"], definers

    def test_the_production_pipeline_still_uses_that_one_rule(self, production):
        source = (REPO / "app" / "pipeline.py").read_text()
        assert "derive_project_status" in source
        assert STATUS_DONE in source, "the terminal status comes from J13, not a literal"


@needs_real_capture
class TestTheRealProjectShowsWhereReconstructionStops:
    """The brief's TEST DATA rule, answered with the real persisted project rather
    than a fixture: exactly where a reconstruction of the 7AK package stops."""

    @staticmethod
    def _arkles_rows(client):
        projects = client.table("projects").select("id,name,status").execute().data
        arkles = [p for p in projects if "Arkles Strand" in (p.get("name") or "")]
        if not arkles:
            pytest.skip("the real Arkles Strand project is not present in this database")
        rows = []
        for project in arkles:
            rows.extend(
                client.table("connections").select("*").eq("project_id", project["id"]).execute().data
            )
        if not rows:
            pytest.skip("the real Arkles Strand project holds no connection rows to read")
        return arkles, rows

    def test_every_connection_row_it_persisted_can_name_nothing_the_review_needs(self, live, production):
        client = production.matcher_module.supabase
        projects, rows = self._arkles_rows(client)
        # J13's status authority reads these rows; the review's does not.
        assert {p["status"] for p in projects} <= {"review", "failed", "processing", "done"}
        for row in rows:
            values = [value for value in row.values() if isinstance(value, str)]
            assert not [v for v in values if v.startswith("RP-0")], "a review package id is persisted"
            assert not [v for v in values if v.startswith("AUTOMATION_")], "a gate code is persisted"
            assert not (set(values) & {AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM,
                                       AUTOMATION_DECISION_REVIEW}), "a gate decision is persisted"
            assert row["review_status"] in {"extracted", "review_required"}
        # The readings layer is where the loss is visible: the writer's own
        # placeholders, standing where the AI said nothing.
        bolts, links = [], []
        for row in rows:
            bolts.extend(
                client.table("bolt_groups").select("*").eq("connection_id", row["id"]).execute().data
            )
            links.extend(
                client.table("connection_members").select("member_id").eq("connection_id", row["id"]).execute().data
            )
        assert bolts, "the real project's connections have persisted bolt groups"
        assert any(
            (bolt.get("bolt_grade") or "").strip() == "?"
            or (bolt.get("bolt_size") or "").strip() == "?"
            or bolt.get("quantity") == 1
            for bolt in bolts
        ), "expected at least one bolt group holding the writer's own placeholder values"
        assert links == [] or all("member_id" in link for link in links)

    def test_the_persisted_rows_are_not_the_capture_and_nothing_relates_them(self, live, production):
        client = production.matcher_module.supabase
        _, rows = self._arkles_rows(client)
        captured = [
            c for page in ARKLES_REAL_AI_EXTRACTION if page.get("page_number") == 7
            for c in (page.get("raw_connections") or [])
        ]
        assert captured, "the capture must hold page-7 candidates"
        captured_scores = sorted(c.get("confidence") for c in captured)
        persisted_scores = sorted(row.get("confidence_score") for row in rows)
        # The database is not a copy of the capture, by count and by confidence,
        # so no reconstruction can assume the one is the other.
        assert len(rows) != len(captured) or persisted_scores != captured_scores, (
            "the persisted rows and the capture now agree, so this observation must be re-derived"
        )
        # And no column anywhere records which capture produced a row.
        for row in rows:
            assert "capture" not in " ".join(row).lower()
            assert "extraction_record" not in " ".join(row).lower()


# =============================================================================
# 6. WHAT WAS FROZEN AND WHY (the module's own scope guard)
# =============================================================================
class TestThisModuleIsReadOnly:
    """J20 added no behaviour, so this proof module must not have either."""

    def test_the_module_writes_nothing(self):
        source = Path(__file__).read_text()
        tree = ast.parse(source)
        forbidden = {"open", "write_text", "write_bytes", "mkdir", "unlink", "remove", "rmdir"}
        used = {
            node.attr if isinstance(node, ast.Attribute) else node.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Attribute, ast.Name))
        }
        assert not (used & forbidden), sorted(used & forbidden)

    def test_the_module_reaches_no_database_outside_its_opt_in_tests(self):
        """The only database access is through the `live` fixture, which is
        itself opt-in. The hermetic tests drive the genuine writer through a
        recording double that holds no client at all."""
        repository = j4._RecordingRepository()
        assert not hasattr(repository, "client")
        assert not hasattr(repository, "table")
        for name in ("insert_connection", "insert_bolt_groups", "insert_review_items"):
            assert callable(getattr(repository, name))
