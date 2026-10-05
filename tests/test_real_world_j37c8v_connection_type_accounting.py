"""
J37C-8V (F1) — UNRECOGNISED CONNECTION TYPE ACCOUNTING.

The defect this milestone fixes, stated exactly: `connections.connection_type`
is a closed enum (bolted / welded / bolted_and_welded / unspecified) and a
drawing does not speak in those four words. `app/pipeline.py::_persist_connections`
replaced anything else with an inference from the connection's own bolt and weld
readings — and the persisted row then read `connection_type = 'unspecified'`
with `warnings = []`, which is precisely the row a drawing that stated NOTHING
produces. The substitution was real, correct, and invisible.

These tests drive the GENUINE writer over synthetic connections through the same
recording-repository double J20/J4 use, so what is asserted is the row the
production writer actually builds — not a restatement of it. Four properties are
pinned:

1. THE SUBSTITUTION IS UNCHANGED. The enum is not extended, the drawn token is
   never written to `connection_type`, and the fallback inference is the same
   one (`bracket` with no bolts/welds -> 'unspecified'; with bolts -> 'bolted').
2. THE SUBSTITUTION IS NOW VISIBLE, on the row itself, with the drawn token
   preserved verbatim.
3. ONLY A STATED-AND-UNRECOGNISED TOKEN IS ACCOUNTED. A recognised type, and a
   source that is absent or blank, get no accounting line at all — the second
   of those is the one that would otherwise re-create, in the warning, the very
   ambiguity the column already has.
4. NOTHING ELSE MOVED. A row written from a recognised type persists exactly the
   twelve columns J20 pinned, `warnings` is not written at all in that case, and
   the accounting is merged (never assigned) so a warning already on the row
   cannot be dropped.

READ-ONLY: no route, no migration, no schema change, no live write.
"""
from __future__ import annotations

import copy
import inspect

import pytest

from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests.test_real_world_j20_connection_review_persistence_gap import (
    LIVE_CONNECTION_TABLES,
)

PROJECT_ID = "PROJ-8V-F1"
DRAWING_ID = "dr-8v-f1"

# The writer's own vocabulary, pinned as a literal so the day the enum or the
# writer's set changes, this file has to be revisited rather than silently
# inheriting the change. (Proven equal to the live `public.connection_type`
# enum, read from the PostgREST schema, in J37C-8U.)
RECOGNISED = {"bolted", "welded", "bolted_and_welded", "unspecified"}

# The twelve columns J20 pins for a connection row the writer builds.
J20_WRITER_COLUMNS = {
    "project_id", "connection_type", "grid_reference", "detail_reference",
    "description", "confidence", "confidence_score", "source_page",
    "source_drawing_id", "extraction_method", "review_status", "notes",
}

production = j4.production


def _connection(connection_type, *, bolts=(), welds=(), confidence=55, page=7):
    """One raw connection in the shape `_flatten_pages` hands the writer."""
    return {
        "connection_type": connection_type,
        "grid_reference": "B/2",
        "detail_reference": "3/S-101",
        "confidence": confidence,
        "page_num": page,
        "bolts": list(bolts),
        "welds": list(welds),
        "plates": [],
        "connects_members": [],
    }


def _persist(pipeline, monkeypatch, connections, *, repository=None):
    """Drive the genuine writer over raw connections. Returns the repository."""
    repository = repository or j4._RecordingRepository()
    monkeypatch.setattr(pipeline, "repo", repository)
    pipeline._persist_connections(
        [copy.deepcopy(c) for c in connections],
        project_id=PROJECT_ID, drawing_id=DRAWING_ID, member_rows=[],
    )
    return repository


def _warnings_of(row):
    return list(row.get("warnings") or [])


# =============================================================================
# 1. THE SUBSTITUTION IS UNCHANGED — and is now visible on the row
# =============================================================================
class TestAnUnrecognisedTypeIsAccountedFor:
    def test_the_persisted_value_is_still_the_existing_fallback_result(self, production, monkeypatch):
        """"bracket" with no bolts and no welds landed on 'unspecified' before
        this milestone and must still land on exactly that."""
        repository = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        assert len(repository.connections) == 1
        assert repository.connections[0]["connection_type"] == "unspecified"

    def test_the_fallback_inference_still_runs(self, production, monkeypatch):
        """With bolts present the same unrecognised token still infers 'bolted':
        the accounting records the substitution, it does not replace it."""
        repository = _persist(production.pipeline, monkeypatch, [
            _connection("bracket", bolts=[{"size": "M12", "grade": "8.8", "quantity": 2}]),
        ])
        assert repository.connections[0]["connection_type"] == "bolted"

    def test_a_multi_word_unrecognised_token_infers_from_its_own_evidence(self, production, monkeypatch):
        """The real corpus's `bolted - stringer to block wall`: outside the
        vocabulary, so it is inferred — and now accounted for as well."""
        repository = _persist(production.pipeline, monkeypatch, [
            _connection("bolted - stringer to block wall",
                        bolts=[{"size": "M16", "grade": "8.8", "quantity": 4}]),
        ])
        row = repository.connections[0]
        assert row["connection_type"] == "bolted"
        assert "bolted - stringer to block wall" in _warnings_of(row)[0]

    def test_the_row_carries_the_accounting_reason_and_the_verbatim_token(self, production, monkeypatch):
        repository = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        warnings = _warnings_of(repository.connections[0])
        assert len(warnings) == 1
        line = warnings[0]
        # The reason (machine-readable, stable) and the phrase it is told by.
        assert "Connection type accounting: " in line
        assert "unrecognised connection type" in line
        # The drawn token VERBATIM — not a cleaned-up restatement of it.
        assert "'bracket'" in line
        # What it became, and the vocabulary it was measured against.
        assert "'unspecified'" in line
        assert all(value in line for value in sorted(RECOGNISED))

    def test_the_accounting_names_the_value_actually_persisted(self, production, monkeypatch):
        """The line reports the persisted value, not a fixed 'unspecified'."""
        repository = _persist(production.pipeline, monkeypatch, [
            _connection("screwed", welds=[{"size_mm": 6, "type": "fillet"}]),
        ])
        row = repository.connections[0]
        assert row["connection_type"] == "welded"
        assert "'welded'" in _warnings_of(row)[0]
        assert "'screwed'" in _warnings_of(row)[0]

    def test_the_enum_is_not_extended_by_the_drawn_token(self, production, monkeypatch):
        repository = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        assert repository.connections[0]["connection_type"] in RECOGNISED


# =============================================================================
# 2. A RECOGNISED TYPE IS NEVER ACCOUNTED FOR
# =============================================================================
class TestARecognisedTypeIsNotAccountedFor:
    @pytest.mark.parametrize("source", ["bolted", "welded", "bolted_and_welded", "unspecified"])
    def test_a_recognised_source_gets_no_accounting_line(self, production, monkeypatch, source):
        repository = _persist(production.pipeline, monkeypatch, [_connection(source)])
        row = repository.connections[0]
        assert row["connection_type"] == source
        assert "warnings" not in row, (
            f"{source!r} is a recognised type; a row written from it must persist "
            "exactly the columns it always did"
        )

    @pytest.mark.parametrize("source", ["Bolted", "BOLTED", "bolted and welded", "Bolted_And_Welded"])
    def test_the_recognised_set_is_measured_after_the_writers_own_normalisation(
        self, production, monkeypatch, source
    ):
        """The writer has always lower-cased and underscored before checking the
        vocabulary, so 'bolted and welded' IS recognised. The accounting must
        measure the same value, or it would report a substitution that the
        writer never made."""
        repository = _persist(production.pipeline, monkeypatch, [_connection(source)])
        row = repository.connections[0]
        assert row["connection_type"] in RECOGNISED
        assert "warnings" not in row

    def test_a_whitespace_padded_token_is_unrecognised_to_both(
        self, production, monkeypatch
    ):
        """The writer's own expression does not strip, so ' Bolted ' normalises to
        '_bolted_' and IS substituted. The accounting must agree with the writer
        rather than with what the token looked like — and it reports the token
        itself, without the padding that is not part of it."""
        repository = _persist(production.pipeline, monkeypatch, [_connection(" Bolted ")])
        row = repository.connections[0]
        assert row["connection_type"] == "unspecified"
        warnings = _warnings_of(row)
        assert len(warnings) == 1 and "'Bolted'" in warnings[0]


# =============================================================================
# 3. AN ABSENT SOURCE IS NOT AN UNRECOGNISED ONE
# =============================================================================
class TestAnAbsentSourceIsNotAccountedFor:
    @pytest.mark.parametrize("source", [None, "", "   "])
    def test_a_missing_or_blank_source_gets_no_accounting_line(self, production, monkeypatch, source):
        repository = _persist(production.pipeline, monkeypatch, [_connection(source)])
        row = repository.connections[0]
        assert row["connection_type"] == "unspecified"
        assert "warnings" not in row, (
            "a drawing that stated nothing must not be reported as having stated "
            "something unrecognised — that is the ambiguity this column already has"
        )

    def test_the_distinction_is_the_modules_own_rule(self, production):
        """The rule the writer applies, asserted directly on the module: a stated
        and unrecognised token is returned verbatim; absent, blank and recognised
        sources are all None."""
        accounting = production.pipeline.connection_type_accounting
        assert accounting.unrecognised_source_token("bracket") == "bracket"
        for absent in (None, "", "   "):
            assert accounting.unrecognised_source_token(absent) is None
        for recognised in RECOGNISED:
            assert accounting.unrecognised_source_token(recognised) is None


# =============================================================================
# 4. NOTHING ELSE MOVED — warnings are merged, never assigned
# =============================================================================
class TestTheJ20PersistenceContractIsUnchanged:
    def test_the_recognised_vocabulary_is_still_the_four_enum_members(self, production):
        assert set(production.pipeline.connection_type_accounting
                   .RECOGNISED_CONNECTION_TYPES) == RECOGNISED

    def test_the_writer_still_normalises_through_the_same_expression(self, production):
        """One definition, and it is the writer's own historical expression —
        character for character. Two of them would be two things to drift."""
        accounting = production.pipeline.connection_type_accounting
        assert accounting.normalise_source_token(None) == "unspecified"
        assert accounting.normalise_source_token("Bolted") == "bolted"
        assert accounting.normalise_source_token("bolted and welded") == "bolted_and_welded"
        source = inspect.getsource(production.pipeline._persist_connections)
        assert 'connection_type_accounting.normalise_source_token(' in source
        assert '.lower().replace(" ", "_")' not in source, (
            "the normalisation must exist in one place — the accounting module"
        )

    def test_a_row_from_a_recognised_type_persists_exactly_the_j20_columns(
        self, production, monkeypatch
    ):
        repository = _persist(production.pipeline, monkeypatch, [_connection("bolted")])
        row = repository.connections[0]
        assert set(row) - {"id"} == J20_WRITER_COLUMNS
        assert LIVE_CONNECTION_TABLES["connections"] - set(row) == {
            "created_at", "level", "location", "warnings",
        }

    def test_the_accounting_adds_no_column_the_live_table_does_not_have(
        self, production, monkeypatch
    ):
        """The one column the accounting writes is the one the table already
        carries: `connections.warnings`. Nothing here needs a migration."""
        repository = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        row = repository.connections[0]
        assert set(row) - {"id"} == J20_WRITER_COLUMNS | {"warnings"}
        assert set(row) <= LIVE_CONNECTION_TABLES["connections"]

    def test_the_accounting_is_merged_into_the_rows_warnings_not_assigned(self, production):
        """The reason 'existing warnings are preserved' holds structurally: the
        writer appends to whatever is already on the row, so a later accounting
        path cannot be silently overwritten by this one."""
        source = inspect.getsource(production.pipeline._persist_connections)
        assert 'warnings = list(conn_row.get("warnings") or [])' in source
        assert 'conn_row["warnings"] = warnings' in source

    def test_the_real_capture_still_produces_rows_this_accounting_never_touches(
        self, production, monkeypatch
    ):
        """Over the repository's REAL Arkles capture, every row is written from a
        recognised type, so every row keeps exactly the twelve columns J20 pins
        — the two proofs are about the same writer and cannot contradict."""
        import types

        from tests.test_real_world_exception_proof import ARKLES_REAL_AI_EXTRACTION

        pages = [types.SimpleNamespace(**page) for page in ARKLES_REAL_AI_EXTRACTION]
        _, connections_raw = production.pipeline._flatten_pages(pages, DRAWING_ID)
        assert connections_raw, "the real capture must yield connections"
        repository = _persist(production.pipeline, monkeypatch, connections_raw)
        assert len(repository.connections) == len(connections_raw)
        for row in repository.connections:
            assert set(row) - {"id"} == J20_WRITER_COLUMNS
            assert "warnings" not in row


class TestTheAccountingIsDeterministic:
    def test_the_same_input_writes_the_same_line_twice(self, production, monkeypatch):
        first = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        second = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        assert (_warnings_of(first.connections[0])
                == _warnings_of(second.connections[0])
                == [production.pipeline.connection_type_accounting
                    .warning_for_unrecognised("bracket", "unspecified")])

    def test_one_row_carries_one_line_however_many_connections_are_written(
        self, production, monkeypatch
    ):
        """"Do not duplicate the warning": the line belongs to the row it
        accounts for, so two connections give two rows with one line each —
        never one row accumulating a line per connection."""
        repository = _persist(production.pipeline, monkeypatch, [
            _connection("bracket"), _connection("screwed"),
        ])
        assert len(repository.connections) == 2
        assert [len(_warnings_of(row)) for row in repository.connections] == [1, 1]

    def test_the_line_is_not_a_gate_decision(self, production, monkeypatch):
        """The persisted vocabulary is the accounting's, never the review gate's
        (J20's rule, kept true by this milestone's line)."""
        repository = _persist(production.pipeline, monkeypatch, [_connection("bracket")])
        for line in _warnings_of(repository.connections[0]):
            assert not line.startswith("AUTOMATION_")
        persisted = {
            value
            for row in repository.connections
            for value in row.values()
            if isinstance(value, str)
        }
        assert not [value for value in persisted if value.startswith("AUTOMATION_")]
