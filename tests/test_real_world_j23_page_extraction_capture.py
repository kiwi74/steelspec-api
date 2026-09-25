"""
J23 — the durable raw AI-capture layer, proved over genuinely captured material.

WHAT THIS FILE IS

The proof that the milestone's one new capability is real: that what the vision model
actually returned for a page is now recorded, faithfully, append-only, anchored to the
genuine document and run identities, and accumulating across every window of a document
larger than one read.

It is NOT a proof of a producer. Nothing here reconstructs a connection-review workflow,
because nothing in `app/` can yet: the capture layer is the only newly completed
capability this milestone claims.

WHY THE MATERIAL IS GENUINE, AND WHY THAT IS THE WHOLE ARGUMENT

Every reading asserted over below is a file under `tests/data/`, captured from a real
model reading a real drawing set:

    selby_square_*.json          7 disjoint windows, 32 pages, 87 members, 52 connections
    selby_square_page_26_recovery.json   the same page 26 read a SECOND time
    arkles_strand_page_extractions.json 30 pages: 12 with objects, 12 genuinely empty,
                                         6 whose response could not be parsed

The capture layer's entire claim is fidelity — "what the model returned, preserved" — and
a fidelity claim proved over data this file also wrote would prove nothing. So the
payloads are read from those files and never constructed here, and the round-trip
assertion is a comparison against the file's own bytes.

The three-way split the brief demands is not staged either. The real Arkles capture
genuinely contains all three states at once: pages that parsed with objects, pages that
parsed and stated nothing (a cover sheet, a section), and pages the model answered but
whose answer could not be read. A layer that had collapsed the last two would pass a
synthetic proof and fail this one.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import types
from pathlib import Path

import pytest

from app.engineering_data import page_extraction_capture as captures

from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j16_pdf_continuation as j16
from tests import test_real_world_j17_parse_failure_retry as j17
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_production_section_authority as authority

REPO = j13.REPO
production = j4.production

MODULE_PATH = REPO / "app" / "engineering_data" / "page_extraction_capture.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"
PIPELINE_PATH = REPO / "app" / "pipeline.py"
MIGRATION_PATH = REPO / "supabase" / "migrations" / "20260925010000_j23_page_extraction_captures.sql"
MIGRATION_NAME = MIGRATION_PATH.name

MODEL = "claude-sonnet-4-6"  # the constant `app/pipeline.py` creates every run with


# ===========================================================================
# The genuine captures. Read, never written.
# ===========================================================================
DATA = REPO / "tests" / "data"

SELBY_WINDOW_FILES = (
    "selby_square_page_extractions.json",           # pages  1- 5
    "selby_square_pages_6_10_extractions.json",     # pages  6-10
    "selby_square_pages_11_15_extractions.json",    # pages 11-15
    "selby_square_pages_16_20_extractions.json",    # pages 16-20
    "selby_square_pages_21_25_extractions.json",    # pages 21-25
    "selby_square_pages_26_30_extractions.json",    # pages 26-30
    "selby_square_pages_31_32_extractions.json",    # pages 31-32
)
SELBY_RECOVERY_FILE = "selby_square_page_26_recovery.json"
ARKLES_FILE = "arkles_strand_page_extractions.json"


def _capture_file(name):
    """One genuine capture, as the list of page objects it is.

    Read at call time and asserted non-empty rather than skipped: the whole proof of
    this milestone is that these readings survive, so a missing capture is a failure
    here, not a test that quietly does not run.
    """
    path = DATA / name
    assert path.exists(), (
        f"the genuine capture at {path.relative_to(REPO)} is missing; it is the material "
        "this milestone's whole fidelity claim is made over"
    )
    data = json.loads(path.read_text())
    assert isinstance(data, list) and data and all(isinstance(p, dict) for p in data), (
        f"{path.name} is not a non-empty list of page objects"
    )
    return data


def _selby_pages():
    """Every page of the genuine Selby set, in page order, across its 7 real windows."""
    pages = [page for name in SELBY_WINDOW_FILES for page in _capture_file(name)]
    return sorted(pages, key=lambda p: p["page_number"])


def _arkles_pages():
    return _capture_file(ARKLES_FILE)


def _selby_recovery():
    """The genuine SECOND reading of page 26 — the file that exists because page 26
    failed when its window was read and the page was read again on its own."""
    pages = _capture_file(SELBY_RECOVERY_FILE)
    assert len(pages) == 1 and pages[0]["page_number"] == 26
    return pages[0]


# ===========================================================================
# The table, in memory — for the properties the design actually rests on.
# ===========================================================================
class _CaptureTable:
    """The capture table's own behaviour, reproduced for the four things that matter.

    1. `captured_at` is stamped by the STORE, never sent by the writer — and one
       instant covers a whole insert statement, because Postgres `now()` is transaction
       time. A later statement gets a later instant.
    2. A repeat of the same `(drawing_id, page_number, analysis_run_id)` is REFUSED,
       the way the primary key refuses it. Silently accepting it would let a test prove
       an accumulation that the real table would have rejected.
    3. There is no update and no delete. There is no method for either, as there is no
       path to either on the table.
    4. A read returns exactly the persisted column list, every attempt included.

    This is a stand-in for the DATABASE, not for the reader: `authoritative_captures`
    and `accumulated_page_mappings` — the rules under test — are the real ones, run over
    what this store hands back.
    """

    def __init__(self):
        self.rows: list[dict] = []
        self._instant = 0

    def insert(self, rows):
        rows = list(rows)
        if not rows:
            return 0
        self._instant += 1
        instant = f"2026-01-01T00:00:{self._instant:02d}+00:00"
        for row in rows:
            key = (row["drawing_id"], row["page_number"], row["analysis_run_id"])
            if any(
                (r["drawing_id"], r["page_number"], r["analysis_run_id"]) == key
                for r in self.rows
            ):
                raise AssertionError(
                    f"the primary key refused a second capture of {key}: one run reads a "
                    "page once, and a second reading by the same run is a mistake"
                )
            self.rows.append({**copy.deepcopy(row), "captured_at": instant})
        return len(rows)

    def for_drawing(self, drawing_id):
        return [
            {name: copy.deepcopy(row.get(name)) for name in captures.CAPTURE_COLUMNS}
            for row in self.rows
            if row["drawing_id"] == drawing_id
        ]


def _record(table, pages, *, run, drawing="drawing-A", drawing_set="set-A",
            project="project-A", model=MODEL):
    """One extraction run's readings, recorded as the production helper records them."""
    return table.insert(captures.capture_rows(
        pages,
        analysis_run_id=run,
        drawing_id=drawing,
        drawing_set_id=drawing_set,
        project_id=project,
        model=model,
    ))


# ===========================================================================
# A. Fidelity — the genuine reading survives, unchanged.
# ===========================================================================
class TestTheGenuineReadingSurvives:
    def test_a_real_page_payload_round_trips_byte_for_byte(self):
        """The reading that is stored is the reading that came back, byte for byte.

        Asserted against the FILE's own bytes, not against a dict this file built, and
        through a JSON encode/decode — which is what the REST transport does to the
        value on its way to a `json` column and back.
        """
        genuine = [p for p in _arkles_pages() if p["raw_members"] and p["raw_connections"]]
        assert genuine, "the genuine Arkles capture states no page with both members and connections"

        table = _CaptureTable()
        _record(table, genuine, run="run-extract")
        stored = {row["page_number"]: row["payload"] for row in table.for_drawing("drawing-A")}

        for page in genuine:
            after_transport = json.loads(json.dumps(stored[page["page_number"]]))
            assert after_transport == page, page["page_number"]
            assert json.dumps(after_transport, sort_keys=True) == json.dumps(page, sort_keys=True)
            # Field ORDER is preserved too, not merely the set of keys: the payload is
            # stored as `json` and not `jsonb` precisely so that it is not reordered.
            assert list(after_transport) == list(page)

    def test_real_page_numbers_members_and_connections_all_survive(self):
        """Each of the three, counted over the genuine capture, after the round trip."""
        pages = _arkles_pages()
        table = _CaptureTable()
        _record(table, pages, run="run-extract")
        stored = table.for_drawing("drawing-A")

        assert [row["page_number"] for row in stored] == [p["page_number"] for p in pages]
        assert [row["payload"] for row in stored] == pages
        assert sum(len(row["payload"]["raw_members"]) for row in stored) == sum(
            len(p["raw_members"]) for p in pages
        )
        assert sum(len(row["payload"]["raw_connections"]) for row in stored) == sum(
            len(p["raw_connections"]) for p in pages
        )
        # The genuine capture is not trivially empty, so the counts above assert something.
        assert sum(len(p["raw_members"]) for p in pages) > 0
        assert sum(len(p["raw_connections"]) for p in pages) > 0

    def test_the_models_own_inconsistent_naming_is_preserved_not_reconciled(self):
        """The model's own words for each sheet are kept exactly as returned.

        This is not a happy-path assertion. In the genuine Selby capture the
        `drawing_number` differs on every one of the 32 pages — "001" … "032", but also
        "006x", "007X", "013x" and "C1136 022X" — and one page carries a longer
        `drawing_title` than the other 31. A layer that "cleaned" that up, or that
        reconciled the two titles to one, would be inventing a consistency the model did
        not report. What is stored is the reading, inconsistency included.
        """
        pages = _selby_pages()
        numbers = {p["drawing_number"] for p in pages}
        titles = {p["drawing_title"] for p in pages}
        assert len(numbers) == len(pages) == 32, "the number varies page by page"
        assert len(titles) == 2, titles

        table = _CaptureTable()
        _record(table, pages, run="run-window")
        stored = {row["page_number"]: row["payload"] for row in table.for_drawing("drawing-A")}
        assert {row["drawing_number"] for row in stored.values()} == numbers
        assert {row["drawing_title"] for row in stored.values()} == titles
        # Page by page, not merely as a set: each page kept its own value.
        for page in pages:
            assert stored[page["page_number"]]["drawing_number"] == page["drawing_number"]
            assert stored[page["page_number"]]["drawing_title"] == page["drawing_title"]


# ===========================================================================
# B. The three states, kept apart — over a real capture that contains all three.
# ===========================================================================
class TestTheThreeStatesStayDistinct:
    def test_the_real_capture_contains_all_three_states(self):
        """The material can prove the distinction because it genuinely exhibits it."""
        pages = _arkles_pages()
        populated = [p for p in pages if not p["parse_failed"]
                     and (p["raw_members"] or p["raw_connections"])]
        empty_success = [p for p in pages if not p["parse_failed"]
                         and not p["raw_members"] and not p["raw_connections"]]
        failed = [p for p in pages if p["parse_failed"]]
        assert populated and empty_success and failed, (
            len(populated), len(empty_success), len(failed)
        )
        # The exact genuine pages, so a change to the capture file is noticed here.
        assert [p["page_number"] for p in failed] == [2, 11, 12, 13, 22, 29]

    def test_a_parse_failed_page_is_not_recorded_as_an_empty_success(self):
        """The one substitution this layer must never make, asserted in both directions.

        The two states carry the same empty lists — that is exactly why a flag is
        needed, and why it is a column rather than an inference. Both survive as
        themselves: the failed page stays `parse_failed = true`, and the page that
        genuinely held nothing stays `false` and is NOT reclassified as a failure.
        """
        table = _CaptureTable()
        _record(table, _arkles_pages(), run="run-extract")
        stored = {row["page_number"]: row for row in table.for_drawing("drawing-A")}

        failed = stored[11]
        assert failed["parse_failed"] is True
        assert failed["payload"]["raw_members"] == []
        assert failed["payload"]["raw_connections"] == []

        empty_success = stored[1]
        assert empty_success["parse_failed"] is False
        assert empty_success["payload"]["raw_members"] == []
        assert empty_success["payload"]["raw_connections"] == []

        # Same empty lists, different readings: the flag is the only thing separating
        # them, and it separates them.
        assert failed["payload"]["raw_members"] == empty_success["payload"]["raw_members"]
        assert failed["parse_failed"] != empty_success["parse_failed"]

    def test_an_absent_parse_flag_is_refused_rather_than_assumed_to_be_a_success(self):
        """A reading whose parse status is unknown is never recorded as a success."""
        page = {k: v for k, v in _arkles_pages()[0].items() if k != "parse_failed"}
        with pytest.raises(captures.CaptureRefused):
            captures.capture_rows([page], analysis_run_id="r", drawing_id="d",
                                  drawing_set_id="s", project_id="p", model=MODEL)

    def test_the_selected_reading_of_a_permanently_failed_page_is_the_failure(self):
        """When no attempt ever parsed, the latest attempt stands — and it is the failed one.

        A permanently unreadable page therefore stays explicitly represented. It is not
        silently dropped from the accumulated reading, which would make it look like a
        page the document does not have.
        """
        table = _CaptureTable()
        pages = _arkles_pages()
        _record(table, pages, run="run-extract")
        _record(table, [pages[10]], run="run-retry")  # page 11, read again and failed again

        chosen = captures.authoritative_captures(table.for_drawing("drawing-A"))
        by_page = {row["page_number"]: row for row in chosen}
        assert by_page[11]["parse_failed"] is True
        assert by_page[11]["analysis_run_id"] == "run-retry"  # the latest attempt
        assert len([p for p in chosen if p["page_number"] == 11]) == 1
        # Every page of the document is still represented, none dropped.
        assert [row["page_number"] for row in chosen] == [p["page_number"] for p in pages]


# ===========================================================================
# C. Multi-window accumulation — the real 32-page set, window by window.
# ===========================================================================
class TestTheDocumentAccumulatesAcrossWindows:
    def test_window_one_plus_the_rest_equals_the_complete_capture(self):
        """Seven genuinely disjoint windows, recorded as seven runs, read back as one document.

        No duplicate successful page, no silent omission: 32 pages recorded, 32 pages
        selected, and each one exactly once.
        """
        table = _CaptureTable()
        rounds = []
        for index, name in enumerate(SELBY_WINDOW_FILES, start=1):
            pages = _capture_file(name)
            rounds.append(pages)
            _record(table, pages, run=f"run-window-{index}")

        stored = table.for_drawing("drawing-A")
        expected = [p for round_pages in rounds for p in round_pages]

        assert len(stored) == len(expected) == 32
        assert len({row["page_number"] for row in stored}) == 32, "a page was captured twice"
        assert sorted(row["page_number"] for row in stored) == list(range(1, 33))

        chosen = captures.authoritative_captures(stored)
        assert len(chosen) == 32
        assert [row["page_number"] for row in chosen] == list(range(1, 33))
        assert [row["payload"] for row in chosen] == expected

    def test_the_accumulated_reading_is_the_hand_off_a_producer_would_consume(self):
        """`accumulated_page_mappings` is the document's whole reading, in page order.

        It is the shape a future producer builds its intake from — persisted capture
        plus document identity, and nothing else.
        """
        table = _CaptureTable()
        for index, name in enumerate(SELBY_WINDOW_FILES, start=1):
            _record(table, _capture_file(name), run=f"run-window-{index}")

        accumulated = captures.accumulated_page_mappings(table.for_drawing("drawing-A"))
        assert [page["page_number"] for page in accumulated] == list(range(1, 33))
        assert accumulated == tuple(_selby_pages())

    def test_a_window_that_reads_no_page_records_no_capture(self):
        """A run that read nothing appends nothing — absence stays absence.

        It is never turned into a fabricated empty page, which is the same
        distinction the parse flag draws one level down.
        """
        table = _CaptureTable()
        assert _record(table, [], run="run-empty") == 0
        assert table.rows == []
        assert captures.authoritative_captures([]) == ()
        assert captures.accumulated_page_mappings([]) == ()

    def test_each_window_is_attributed_to_its_own_run_and_page_range(self):
        """The run identity is what makes each window's readings its own."""
        table = _CaptureTable()
        for index, name in enumerate(SELBY_WINDOW_FILES, start=1):
            _record(table, _capture_file(name), run=f"run-window-{index}")

        by_run = {}
        for row in table.for_drawing("drawing-A"):
            by_run.setdefault(row["analysis_run_id"], []).append(row["page_number"])
        assert by_run == {
            "run-window-1": [1, 2, 3, 4, 5],
            "run-window-2": [6, 7, 8, 9, 10],
            "run-window-3": [11, 12, 13, 14, 15],
            "run-window-4": [16, 17, 18, 19, 20],
            "run-window-5": [21, 22, 23, 24, 25],
            "run-window-6": [26, 27, 28, 29, 30],
            "run-window-7": [31, 32],
        }


# ===========================================================================
# D. Source identity — one document's readings are not another's.
# ===========================================================================
class TestReadingsAreNotMixedBetweenDocuments:
    def test_page_numbers_alone_are_not_a_page_identity(self):
        """Two documents both have a page 1, and both survive as themselves.

        Page 1 of the Arkles set and page 1 of the Selby set are different readings of
        different sheets. Keyed on the page number alone they would collide; keyed on
        (drawing, page) they do not, and the accumulated reading of each drawing
        contains only its own pages.
        """
        table = _CaptureTable()
        arkles = _arkles_pages()
        selby = _selby_pages()
        _record(table, arkles, run="run-a", drawing="drawing-A", drawing_set="set-A")
        _record(table, selby, run="run-b", drawing="drawing-B", drawing_set="set-B")

        assert len(table.rows) == len(arkles) + len(selby)

        for drawing, expected in (("drawing-A", arkles), ("drawing-B", selby)):
            accumulated = captures.accumulated_page_mappings(table.for_drawing(drawing))
            assert accumulated == tuple(expected), drawing
            assert {page["drawing_title"] for page in accumulated} == {
                page["drawing_title"] for page in expected
            }

        a_page_one = captures.accumulated_page_mappings(table.for_drawing("drawing-A"))[0]
        b_page_one = captures.accumulated_page_mappings(table.for_drawing("drawing-B"))[0]
        assert a_page_one != b_page_one, "two documents' page 1 readings must not be the same object"

    def test_one_selection_over_both_drawings_keeps_them_apart(self):
        """The selection key is (drawing, page), so a mixed read cannot merge two sheets."""
        table = _CaptureTable()
        _record(table, _arkles_pages(), run="run-a", drawing="drawing-A")
        _record(table, _selby_pages(), run="run-b", drawing="drawing-B")

        chosen = captures.authoritative_captures(table.rows)
        per_drawing = {}
        for row in chosen:
            per_drawing.setdefault(row["drawing_id"], []).append(row["page_number"])
        assert per_drawing == {
            "drawing-A": list(range(1, 31)),
            "drawing-B": list(range(1, 33)),
        }

    def test_every_row_carries_the_document_it_was_read_from(self):
        """The identity is on the row, not inferred at read time from a join.

        The reader is handed one drawing at a time and returns only that drawing's
        readings; a row that did not carry its own drawing could not be filtered.
        """
        table = _CaptureTable()
        _record(table, _arkles_pages(), run="run-a", drawing="drawing-A",
                drawing_set="set-A", project="project-A")
        for row in table.for_drawing("drawing-A"):
            assert row["drawing_id"] == "drawing-A"
            assert row["drawing_set_id"] == "set-A"
            assert row["project_id"] == "project-A"
            assert row["model"] == MODEL
            assert row["analysis_run_id"] == "run-a"
        assert table.for_drawing("drawing-B") == []


# ===========================================================================
# E. Re-reads — history survives, and the selection is deterministic.
# ===========================================================================
class TestAReReadPreservesHistory:
    def test_the_genuine_recovery_of_page_26_is_a_second_attempt_beside_the_first(self):
        """The real re-read: the same page read twice, both readings kept.

        `selby_square_page_26_recovery.json` is byte-identical to page 26 of the
        26-30 window. It exists because the page was read again on its own. The capture
        layer keeps both attempts and selects exactly one.
        """
        table = _CaptureTable()
        window = _capture_file("selby_square_pages_26_30_extractions.json")
        recovery = _selby_recovery()
        _record(table, window, run="run-window-6")
        _record(table, [recovery], run="run-recovery")

        rows = table.for_drawing("drawing-A")
        page_26 = [row for row in rows if row["page_number"] == 26]
        assert len(page_26) == 2, "a re-read must not overwrite the attempt it supersedes"
        assert [row["analysis_run_id"] for row in page_26] == ["run-window-6", "run-recovery"]
        assert len(rows) == len(window) + 1

        chosen = captures.authoritative_captures(rows)
        selected = [row for row in chosen if row["page_number"] == 26]
        assert len(selected) == 1, "two attempts of a page still select exactly one reading"
        assert selected[0]["analysis_run_id"] == "run-recovery"  # the later attempt
        assert selected[0]["payload"] == recovery
        assert len(chosen) == len(window), "the re-read adds no page to the document"

    def test_a_later_failed_attempt_does_not_displace_an_earlier_parsed_one(self):
        """The rule that matters most, in the direction that would be catastrophic.

        The first attempt parsed; the second did not. A selection that ordered on the
        attempt time alone would hand back the failure and lose the reading that
        worked. The parsed attempt wins, and it wins because the rule says a parsed
        reading outranks an unparsed one — not because of which came later.

        The failure object is production's own: `PageExtraction(page_number=n,
        parse_failed=True)` is exactly what `pdf_vision_analyzer` appends at line 207.
        The successful reading is the genuine page-26 capture.
        """
        table = _CaptureTable()
        recovery = _selby_recovery()
        _record(table, [recovery], run="run-good")
        _record(table, [{"page_number": 26, "drawing_number": None, "drawing_title": None,
                         "revision": None, "raw_members": [], "raw_connections": [],
                         "parse_failed": True}], run="run-bad")

        rows = table.for_drawing("drawing-A")
        assert len(rows) == 2  # both attempts recorded
        chosen = captures.authoritative_captures(rows)
        assert len(chosen) == 1
        assert chosen[0]["analysis_run_id"] == "run-good"
        assert chosen[0]["parse_failed"] is False
        assert chosen[0]["payload"] == recovery

    def test_between_two_parsed_attempts_the_later_one_stands(self):
        """Two successful readings of one page: the latest is authoritative, and the
        earlier one is still there."""
        table = _CaptureTable()
        page_26 = [p for p in _selby_pages() if p["page_number"] == 26][0]
        _record(table, [page_26], run="run-first")
        _record(table, [page_26], run="run-second")

        chosen = captures.authoritative_captures(table.for_drawing("drawing-A"))
        assert len(chosen) == 1
        assert chosen[0]["analysis_run_id"] == "run-second"
        assert len(table.rows) == 2

    def test_the_selection_does_not_depend_on_the_order_rows_arrive_in(self):
        """Determinism, asserted by shuffling what the query returned.

        A database returns rows in whatever order it likes. The rule orders attempts by
        the capture row's own `(captured_at, analysis_run_id)`, so the answer is the
        same either way.
        """
        table = _CaptureTable()
        _record(table, _arkles_pages(), run="run-extract")
        _record(table, [_arkles_pages()[10]], run="run-retry-a")
        _record(table, [_arkles_pages()[10]], run="run-retry-b")

        rows = table.for_drawing("drawing-A")
        forward = captures.authoritative_captures(rows)
        backward = captures.authoritative_captures(list(reversed(rows)))
        assert [r["analysis_run_id"] for r in forward] == [r["analysis_run_id"] for r in backward]
        assert [r["payload"] for r in forward] == [r["payload"] for r in backward]

    def test_ordering_is_taken_from_the_capture_row_and_not_from_a_parent_run(self):
        """`captured_at` is a column on the capture, and the module has no other source.

        `analysis_runs` is mutable in place and its `completed_at` is NULL for a run
        whose update never ran — which is exactly the run of a failed continuation. An
        append-only ledger that ordered itself by that table could be reordered from
        outside it. Asserted over the module's CODE, not its prose: its docstring names
        the run table precisely to say it does not read it.
        """
        code = _code(MODULE_PATH)
        for forbidden in ("analysis_runs", "completed_at", "started_at", "model_used"):
            assert forbidden not in code, forbidden
        assert "captured_at" in code

    def test_a_row_without_its_timestamp_is_refused_rather_than_guessed_at(self):
        """When two attempts of a page must be ordered, an incomplete row is refused.

        The rule decides between attempts, so it is asked to decide here: one attempt
        has lost its `captured_at`, and rather than treat the missing instant as the
        beginning of time and hand back the wrong reading, it refuses.
        """
        table = _CaptureTable()
        page = _arkles_pages()[0]
        _record(table, [page], run="run-first")
        _record(table, [page], run="run-second")
        rows = table.for_drawing("drawing-A")

        with pytest.raises(captures.CaptureRefused):
            captures.authoritative_captures([{**rows[0], "captured_at": None}, rows[1]])
        with pytest.raises(captures.CaptureRefused):
            captures.authoritative_captures([{k: v for k, v in rows[0].items()
                                              if k != "captured_at"}, rows[1]])
        # Absent is absence everywhere else too: a row with no page, no drawing or no
        # parse flag is refused rather than keyed on a default.
        for missing in ("drawing_id", "page_number", "parse_failed", "analysis_run_id"):
            with pytest.raises(captures.CaptureRefused):
                captures.authoritative_captures(
                    [{k: v for k, v in rows[0].items() if k != missing}, rows[1]]
                )


# ===========================================================================
# F. Append-only, and no second window protocol.
# ===========================================================================
class TestTheLedgerIsAppendOnly:
    def test_the_capture_module_writes_nothing_and_rewrites_nothing(self):
        """No client, no update, no delete, no upsert — it builds rows and reads rules."""
        plain, methods = _calls(MODULE_PATH)
        assert not {"open", "print", "input"} & plain
        assert not {"execute", "insert", "update", "delete", "upsert", "table", "post", "rpc"} & methods

    def test_the_one_writer_inserts_once_and_never_updates(self):
        """One statement per run, and no per-row loop that could half-record a window."""
        body = _function_code(REPOSITORY_PATH, "insert_page_extraction_captures")
        assert body.count(".insert(") == 1
        for forbidden in (".update(", ".delete(", "upsert"):
            assert forbidden not in body, forbidden
        # And the read half is a read: it selects, and does nothing else.
        read = _function_code(REPOSITORY_PATH, "page_extraction_captures_for_drawing")
        assert read.count(".select(") == 1
        for forbidden in (".insert(", ".update(", ".delete(", "upsert"):
            assert forbidden not in read, forbidden

    def test_a_capture_is_never_counted_as_evidence_for_a_page(self):
        """The invariant the append-only property does not cover, and that would break retries.

        A parse-failed page legitimately HAS a capture. If the per-page evidence reader
        counted one as evidence, every recorded failure would read as "evidence was
        already persisted" and every retry would be refused — permanently, and for the
        pages that most need one. Asserted over the reader's own code: the surrounding
        comment names the table precisely to say it is excluded.
        """
        reader = _function_code(REPOSITORY_PATH, "evidence_rows_for_page")
        assert "page_extraction_captures" not in reader
        assert captures.CAPTURE_TABLE not in reader
        assert "steel_members" in reader and "connections" in reader

    def test_no_second_window_protocol_was_introduced(self):
        """The window rules are J16's, untouched: this milestone adds a record of what a
        window read, not a second way to decide which window may be read."""
        windows = (REPO / "app" / "validation" / "page_windows.py").read_text(encoding="utf-8")
        assert "page_extraction_capture" not in windows
        # And the pipeline still decides windows with the existing contract.
        pipeline = PIPELINE_PATH.read_text(encoding="utf-8")
        assert "check_page_window(" in pipeline

    def test_the_selecting_rule_has_no_side_effect_and_no_counterparty(self):
        """A second call over the same rows gives the same answer, and the module holds
        no session state that could make it otherwise."""
        rows = [
            {"drawing_id": "d", "page_number": 1, "analysis_run_id": "r2", "model": MODEL,
             "drawing_set_id": "s", "project_id": "p", "parse_failed": False,
             "payload": {"page_number": 1}, "captured_at": "2026-01-01T00:00:02+00:00"},
            {"drawing_id": "d", "page_number": 1, "analysis_run_id": "r1", "model": MODEL,
             "drawing_set_id": "s", "project_id": "p", "parse_failed": False,
             "payload": {"page_number": 1}, "captured_at": "2026-01-01T00:00:01+00:00"},
        ]
        first = captures.authoritative_captures(rows)
        second = captures.authoritative_captures(rows)
        assert first == second
        assert first[0]["analysis_run_id"] == "r2"


# ===========================================================================
# G. Purity — nothing is read that is not the data.
# ===========================================================================
def _calls(path):
    """The module's plain calls and its method calls, from its AST.

    Scanned from the tree rather than the text because the module's own docstring
    NAMES the tables it is not derived from — a text scan could not tell the
    explanation from the deed.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    plain, methods = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                plain.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                methods.add(node.func.attr)
    return plain, methods


def _code(path):
    """A module's executable code as text, with every docstring removed.

    The same reason as `_calls`: these modules explain in prose what they are not
    ("it reads no `analysis_runs` row", "it imports nothing from `app.supabase_client`"),
    and an assertion meant to hold over the CODE must not be answered by the
    explanation of it.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    _strip_docstrings(tree)
    return ast.unparse(tree)


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]


def _function_code(path, name):
    """One function's executable code, docstring removed — nothing else's."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            _strip_docstrings(node)
            return ast.unparse(node)
    raise AssertionError(f"{path} defines no function {name!r}")


def _sql(path):
    """A migration's statements, with its `--` commentary removed.

    The headers of these migrations explain at length what they deliberately did not
    do — that one says `json` and not `jsonb`, that another adds no index. An assertion
    about the SQL must be answered by the SQL.
    """
    text = Path(path).read_text(encoding="utf-8")
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


class TestTheCaptureModuleIsPure:
    def test_it_imports_nothing_from_the_application(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported == {"__future__", "dataclasses", "datetime", "typing"}, sorted(imported)

    def test_it_reads_no_environment_no_secret_and_no_file(self):
        code = _code(MODULE_PATH)
        for forbidden in ("os.environ", "getenv", "environ[", "open(", "Path(",
                          "read_text(", "read_bytes(", "requests", "httpx", "urllib"):
            assert forbidden not in code, forbidden

    def test_it_holds_no_client_and_the_write_and_read_live_in_the_repository(self):
        """The transport belongs to the module that already owns it; this one decides."""
        assert "supabase" not in _code(MODULE_PATH)
        repository = REPOSITORY_PATH.read_text(encoding="utf-8")
        assert "def insert_page_extraction_captures" in repository
        assert "def page_extraction_captures_for_drawing" in repository


# ===========================================================================
# H. The migration — the table, the key, the grants, the refusal.
# ===========================================================================
class TestTheMigration:
    def test_it_follows_the_naming_and_type_conventions_the_repo_already_uses(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert MIGRATION_PATH.exists() and MIGRATION_NAME.startswith("20260925010000_j23_")
        assert "create table if not exists public.page_extraction_captures" in sql
        assert "primary key (drawing_id, page_number, analysis_run_id)" in sql
        for constraint in ("page_extraction_captures_pkey", "page_extraction_captures_drawing_fkey",
                           "page_extraction_captures_drawing_set_fkey",
                           "page_extraction_captures_project_fkey",
                           "page_extraction_captures_run_fkey",
                           "page_extraction_captures_page_number_check"):
            assert constraint in sql, constraint
        assert "on delete restrict" in sql
        assert sql.count("on delete restrict") == 4

    def test_the_payload_is_json_and_not_jsonb(self):
        """jsonb rewrites the stored value — it sorts keys and drops duplicates — and this
        column exists precisely to hold what the model returned."""
        sql = _sql(MIGRATION_PATH)
        payload = next(line for line in sql.splitlines() if line.strip().startswith("payload"))
        assert re.search(r"\bjson\b\s+not null", payload), payload
        assert "jsonb" not in sql.lower()

    def test_the_timestamp_is_the_databases_own_default(self):
        sql = _sql(MIGRATION_PATH)
        assert re.search(r"captured_at\s+timestamptz\s+not null\s+default now\(\)", sql)

    def test_it_is_append_only_for_every_role_and_not_by_the_trigger_alone(self):
        sql = _sql(MIGRATION_PATH)
        assert "before update or delete on public.page_extraction_captures" in sql
        assert "CAPTURE_REFUSED_APPEND_ONLY" in sql
        assert "revoke update, delete, truncate on table public.page_extraction_captures from service_role" in sql
        # TRUNCATE cannot be seen by a row-level trigger, so the revoke is not decoration.
        assert "truncate" in sql

    def test_the_server_may_append_and_read_and_nothing_else(self):
        sql = _sql(MIGRATION_PATH)
        assert "grant insert, select on table public.page_extraction_captures to service_role" in sql
        assert "revoke all on table public.page_extraction_captures from public, anon, authenticated" in sql

    def test_rls_is_enabled_with_no_policy_admitting_a_browser_role(self):
        sql = _sql(MIGRATION_PATH)
        assert "alter table public.page_extraction_captures enable row level security" in sql
        assert "create policy" not in sql.lower()

    def test_it_names_no_other_milestones_table_and_no_second_authorization_system(self):
        """No ownership column, no membership table — ownership stays the project's own.

        Also asserted globally: no migration may mention the milestone identifiers that
        other files scan for, and none may place the review tables anywhere but J22's
        own migration.
        """
        for path in (REPO / "supabase").rglob("*.sql"):
            text = path.read_text()
            assert "J19" not in text, path.name
            assert "production_review" not in text, path.name
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "user_id" not in sql
        assert "connection_review_items" not in sql
        assert "connection_review_snapshots" not in sql

    def test_it_touches_nothing_this_milestone_derives(self):
        """No trigger on, and no statement against, a table anything else is derived from."""
        sql = _sql(MIGRATION_PATH)
        for untouched in ("public.projects", "public.steel_members", "public.connections",
                          "public.review_items", "public.drawings", "public.drawing_sets",
                          "public.analysis_runs"):
            assert f"alter table {untouched}" not in sql, untouched
            # `references public.<t>` is the foreign key this table NEEDS; an `on` or an
            # `update` against one of them is a statement against it, and there is none.
            assert f"on {untouched}" not in sql, untouched
            assert f"update {untouched}" not in sql, untouched
            assert f"insert into {untouched}" not in sql, untouched
        assert sql.count("create trigger") == 1  # exactly one, on the capture table
        assert "create trigger page_extraction_captures_append_only" in sql


# ===========================================================================
# I. The genuine production paths record the capture.
# ===========================================================================
@pytest.fixture()
def drive(production, monkeypatch, tmp_path):
    """The genuine production path over a real PDF, fed the REAL captured readings.

    Nothing is replaced except the vision call itself. The document, its page count,
    the window arithmetic, the run rows, the coverage and failure record, the
    persistence and the retry contract are production's; the readings handed back are
    the captured ones, parsed into production's own `PageExtraction` and returned for
    exactly the window production asked for.

    The repository double is J17's, which is J16's SERVING double plus the one read a
    retry makes. It answers reads from the rows production wrote through it, so an
    accumulation proved here is an accumulation production produced.
    """
    counter = [0]

    def build(*, pages, served, source_name, fails=(), window=30, project_id=None):
        counter[0] += 1
        project_id = project_id or f"j23-project-{counter[0]}"
        source = j16._write_pdf(tmp_path / source_name, pages)
        storage_path = f"j23-user/{source_name}"

        repository = j17._RetryRepository()
        repository.seed_project(project_id, storage_path=storage_path)

        doc = types.SimpleNamespace(
            project_id=project_id, source=source, storage_path=storage_path,
            repository=repository, user_id="j23-user", pages=pages,
            # The constant production creates every run with, taken from the resolved
            # module here so a test can assert the recorded provenance against it.
            model=production.pipeline.PDF_VISION_MODEL,
        )

        def serve(mapping, *, failed=()):
            """Install the reading this pass will receive, for the window it asks for.

            The signature mirrors the real reader's — including `store_page_image`, which
            a retry passes and a window read does not — so a caller that reaches the
            vision stage differently is not silently given the wrong reading.
            """
            def stand_in(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1,
                         **kwargs):
                last = first_page + max_pages - 1
                out = []
                for number in range(first_page, last + 1):
                    if number in failed:
                        # Exactly the object production builds when a response cannot be
                        # read (app/ai_analysis/pdf_vision_analyzer.py:207).
                        out.append(production.PageExtraction(page_number=number, parse_failed=True))
                    elif number in mapping:
                        payload = mapping[number]
                        out.append(production.PageExtraction(
                            page_number=payload["page_number"],
                            drawing_number=payload["drawing_number"],
                            drawing_title=payload["drawing_title"],
                            revision=payload["revision"],
                            raw_members=copy.deepcopy(payload["raw_members"]),
                            raw_connections=copy.deepcopy(payload["raw_connections"]),
                            parse_failed=payload["parse_failed"],
                        ))
                return out
            monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", stand_in)

        serve({page["page_number"]: page for page in served}, failed=fails)
        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "MAX_PDF_PAGES", window)
        # NO NETWORK. The matcher's own reference read is answered from the pinned index
        # the accepted section-authority harness uses.
        monkeypatch.setattr(production.matcher_module, "supabase",
                            authority._FakeSupabaseClient(authority._pinned_index()))

        doc.serve = serve
        doc.extract = lambda: production.pipeline.parse_pdf_and_save(
            source, project_id, "j23-user", storage_path)
        doc.read_next_window = lambda **kw: production.pipeline.continue_pdf_extraction(
            source, project_id, "j23-user", storage_path, **kw)
        doc.retry = lambda **kw: production.pipeline.retry_pdf_page(
            source, project_id, "j23-user", storage_path, **kw)
        return doc

    return build


class TestTheProductionPathsRecordTheReading:
    def test_the_whole_document_run_records_every_page_it_read(self, drive):
        """`/extract` over the genuine 30-page Arkles set: one capture per page, verbatim."""
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j23-arkles.pdf")
        doc.extract()

        recorded = doc.repository.captures
        assert len(recorded) == len(pages) == 30
        rows = sorted(recorded, key=lambda r: r["page_number"])
        assert [row["page_number"] for row in rows] == [p["page_number"] for p in pages]
        assert [row["payload"] for row in rows] == pages
        # The genuine three-way split survives the whole production path.
        assert [row["page_number"] for row in rows if row["parse_failed"]] == [2, 11, 12, 13, 22, 29]
        assert [row["page_number"] for row in rows
                if not row["parse_failed"]
                and not row["payload"]["raw_members"]
                and not row["payload"]["raw_connections"]] == [1, 3, 4, 5, 16, 17, 18, 19, 20, 24, 25, 30]

    def test_every_recorded_row_carries_the_genuine_document_run_and_model_identity(self, drive):
        """The identity is the one production already held: the drawing and run it wrote.

        Not a second identity invented for the capture — the same `drawing_id` the run's
        own `analysis_runs` row was created for, and the same model constant it was
        created with.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j23-identity.pdf")
        doc.extract()

        drawing_id = next(iter(doc.repository.drawings))
        drawing_set_id = doc.repository.drawings[drawing_id]["drawing_set_id"]
        runs = list(doc.repository.analysis_runs)
        assert len(runs) == 1, runs

        for row in doc.repository.captures:
            assert row["drawing_id"] == drawing_id
            assert row["drawing_set_id"] == drawing_set_id
            assert row["project_id"] == doc.project_id
            assert row["analysis_run_id"] == runs[0]
            assert row["model"] == doc.model

    def test_the_capture_is_written_before_any_row_derived_from_it(self, drive):
        """The documented ordering, asserted on the sequence of calls production made.

        Of the two possible orders only one fails safe. A capture that fails aborts the
        run before a single member or connection row exists; the other order leaves
        engineering rows whose reading was never recorded — and on the retry path that
        state is permanent, because a page that already has evidence is refused a retry
        forever.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j23-order.pdf")
        doc.extract()

        calls = doc.repository.order
        assert "insert_page_extraction_captures" in calls
        assert "insert_members" in calls
        assert calls.index("insert_page_extraction_captures") < calls.index("insert_members")

    def test_the_real_30_page_set_accumulates_across_two_genuine_windows(self, drive):
        """A real document read in two windows: 28 pages, then the last 2.

        Window 1 is `/extract`; window 2 is `continue_pdf_extraction`. Neither writes a
        page the other wrote, and the two together are the whole document.

        WHY THE SPLIT IS AT 28 AND NOT ANYWHERE ELSE — a genuine finding, not a
        convenience. On BOTH genuine captures, member marks repeat across sheets: on the
        32-page Selby set every window after the first shares at least one mark with the
        pages already persisted, and the genuine Arkles set is the same (a cut at 10, 15,
        22 or 25 collides on five or more marks). J16's continuation refuses a
        cross-window mark collision outright, because member identity across windows has
        no merge rule — and that refusal is PRE-EXISTING, asserted by J16's own tests and
        untouched by this milestone. 28 is the largest cut on this capture at which the
        second window's pages carry no already-persisted mark: pages 29 and 30 hold no
        members at all, one because its response could not be parsed and one because it
        genuinely states nothing.

        That makes this the ONLY second window the genuine record currently permits — so
        it is the one that gets driven, and the collision that forbids the others is
        asserted on its own, below, rather than avoided in silence.
        """
        pages = _arkles_pages()
        doc = drive(pages=30, served=pages, source_name="j23-arkles-windows.pdf", window=28)

        doc.extract()
        first = len(doc.repository.captures)
        doc.read_next_window()
        recorded = doc.repository.captures

        assert first == 28, first
        assert len(recorded) == 30
        assert sorted(row["page_number"] for row in recorded) == list(range(1, 31))
        assert len({(row["drawing_id"], row["page_number"]) for row in recorded}) == 30
        assert len({row["analysis_run_id"] for row in recorded}) == 2

        chosen = captures.authoritative_captures(recorded)
        assert [row["page_number"] for row in chosen] == list(range(1, 31))
        assert [row["payload"] for row in chosen] == pages
        # The second window's two pages are the two genuinely awkward states, and both
        # arrive through the continuation as themselves.
        second = [row for row in recorded if row["analysis_run_id"] == max(
            row["analysis_run_id"] for row in recorded)]
        assert [row["page_number"] for row in second] == [29, 30]
        assert [row["parse_failed"] for row in second] == [True, False]
        assert all(not row["payload"]["raw_members"] for row in second)

    def test_a_window_refused_after_its_read_records_no_capture(self, drive):
        """The exact boundary, asserted rather than assumed — and a discovery about real data.

        J16's continuation reads the window and then refuses it outright if a member mark
        on it is already persisted, because member identity across windows has no merge
        rule. On the genuine Arkles capture that refusal fires for every window after the
        first except the degenerate one above: a cut at 10 collides on `P1`…`P5`.

        The refusal happens BEFORE the window's `analysis_runs` row is created, and a
        capture is attributed to a run — so a refused window records nothing at all. That
        is the right direction, and not merely the convenient one: a capture recorded for
        it would put pages into the accumulated reading that the coverage record still
        says were never analysed, and a producer building its intake from capture alone
        would then include pages the project never accepted. The record's completeness
        authority stays the coverage record, exactly as it was.

        What this boundary costs: a window that was genuinely put to the model and then
        refused leaves no trace in the ledger. That cost is stated here and in the
        milestone report rather than designed around.
        """
        pages = _arkles_pages()
        doc = drive(pages=30, served=pages, source_name="j23-arkles-refused.pdf", window=10)

        doc.extract()
        assert len(doc.repository.captures) == 10
        before = copy.deepcopy(doc.repository.captures)
        runs_before = dict(doc.repository.analysis_runs)

        import app.validation.page_windows as windows

        with pytest.raises(windows.ContinuationRefused) as refusal:
            doc.read_next_window()
        assert "CONTINUATION_MEMBER_MARK_COLLISION" in str(refusal.value)
        assert "P1" in str(refusal.value)

        # Nothing was written: no capture, no run, no member, and the coverage record is
        # exactly where it was, so the window is still readable if the collision is
        # resolved. No misleading state, and no half-recorded window.
        assert doc.repository.captures == before
        assert doc.repository.analysis_runs == runs_before

    def test_no_page_is_captured_twice_by_the_window_sequence(self, drive):
        """The window contract's own guarantee, restated where it matters.

        `check_page_window` refuses overlapping, backward, skipping and already-complete
        windows, so a document's windows are disjoint. Duplicate-resistance therefore
        needs no unique index here — and the primary key would refuse a second reading
        by the SAME run anyway.
        """
        pages = _arkles_pages()
        doc = drive(pages=30, served=pages, source_name="j23-disjoint.pdf", window=28)
        doc.extract()
        doc.read_next_window()

        recorded = doc.repository.captures
        assert len(recorded) == 30
        assert len({(row["drawing_id"], row["page_number"]) for row in recorded}) == 30
        assert len({row["analysis_run_id"] for row in recorded}) == 2

    def test_nothing_derived_from_the_capture_is_needed_to_rebuild_the_reading(self, drive):
        """The memory constraint the brief sets for the future producer.

        Every page payload that comes back is the model's own reading, taken from the
        capture rows alone. The interpreted members and connections are present in the
        same run — and are not consulted, because they are a projection that has already
        lost what the capture holds.
        """
        pages = _selby_pages()
        doc = drive(pages=32, served=pages, source_name="j23-source-only.pdf", window=32)
        doc.extract()
        assert doc.repository.members, "the run persisted members; the proof is not vacuous"
        assert doc.repository.connections, "the run persisted connections; the proof is not vacuous"

        captured = captures.accumulated_page_mappings(doc.repository.captures)
        assert captured == tuple(pages)
        for payload in captured:
            assert set(payload) == {
                "page_number", "drawing_number", "drawing_title", "revision",
                "raw_members", "raw_connections", "parse_failed",
            }

    def test_a_failed_page_is_recorded_as_failed_and_writes_no_evidence(self, drive):
        """A parse failure is recorded, and it does not become an empty success.

        The genuine Arkles set fails on six pages. Each is captured with its flag set,
        and none of the six produces a member or a connection row attributed to it.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j23-failed.pdf")
        doc.extract()

        failed = [row for row in doc.repository.captures if row["parse_failed"]]
        assert [row["page_number"] for row in failed] == [2, 11, 12, 13, 22, 29]
        for row in failed:
            assert row["payload"]["raw_members"] == []
            assert row["payload"]["raw_connections"] == []
        drawing_id = next(iter(doc.repository.drawings))
        for page in (2, 11, 12, 13, 22, 29):
            assert not [
                m for m in doc.repository.members
                if m.get("source_drawing_id") == drawing_id and m.get("source_page") == page
            ], page

    def test_the_retry_records_its_own_attempt_and_keeps_the_failed_one(self, drive):
        """A retry that recovers: two captures for the page, one selected, none erased.

        The page is failed when its window is read — that is how a page enters the
        record as failed. The reading the retry then receives is the GENUINE recovery
        capture: `selby_square_page_26_recovery.json` is the file that exists because
        this page was failed in its window and read again on its own, so the reading
        that lands here is the one that was really returned.
        """
        served = {page["page_number"]: page for page in _selby_pages()}
        recovery = _selby_recovery()
        doc = drive(pages=32, served=list(served.values()) + [], source_name="j23-retry.pdf",
                    window=32, fails=(26,))
        doc.extract()

        failed = [row for row in doc.repository.captures if row["page_number"] == 26]
        assert len(failed) == 1 and failed[0]["parse_failed"] is True
        assert len(doc.repository.captures) == 32

        doc.serve({**served, 26: recovery})           # the retry receives the real recovery
        doc.retry(page_number=26)

        page_26 = [row for row in doc.repository.captures if row["page_number"] == 26]
        assert len(page_26) == 2, "the failed attempt must survive its own recovery"
        assert [row["parse_failed"] for row in page_26] == [True, False]
        assert page_26[0]["analysis_run_id"] != page_26[1]["analysis_run_id"]
        assert page_26[1]["payload"] == recovery
        assert page_26[1]["captured_at"] > page_26[0]["captured_at"]

        chosen = captures.authoritative_captures(doc.repository.captures)
        assert len([row for row in chosen if row["page_number"] == 26]) == 1
        recovered = captures.accumulated_page_mappings(doc.repository.captures)[25]
        assert recovered == recovery, "the accumulated reading carries the recovered page"

    def test_a_retry_that_fails_again_records_the_attempt_and_creates_no_evidence(self, drive):
        """The still-failing branch: the attempt is recorded, and that is all that happens.

        It writes no engineering row, so it creates no review state. The page is left
        exactly as it was — still failed, still retryable, still keeping the project out
        of "done" — and the earlier failed attempt is untouched beside the new one.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j23-retry-again.pdf",
                    window=30)
        doc.extract()
        before = len(doc.repository.captures)
        members_before = len(doc.repository.members)
        connections_before = len(doc.repository.connections)
        assert before == 30

        doc.retry(page_number=11)  # the same genuinely unreadable page, failing again

        page_11 = [row for row in doc.repository.captures if row["page_number"] == 11]
        assert len(page_11) == 2
        assert all(row["parse_failed"] for row in page_11)
        assert page_11[0]["analysis_run_id"] != page_11[1]["analysis_run_id"]
        assert len(doc.repository.captures) == before + 1
        assert len(doc.repository.members) == members_before
        assert len(doc.repository.connections) == connections_before

        chosen = captures.authoritative_captures(doc.repository.captures)
        selected = [row for row in chosen if row["page_number"] == 11]
        assert len(selected) == 1 and selected[0]["parse_failed"] is True
        assert selected[0]["analysis_run_id"] == page_11[1]["analysis_run_id"]

    def test_the_retry_receives_exactly_the_page_it_was_named_for(self, drive):
        """The capture's page identity is the page the model was asked about.

        The analyzer tells the model which page it is looking at; the retry reads one
        page. A capture attributed to the wrong page would be a reading filed under
        another sheet, which is the mixing this layer exists to prevent.
        """
        served = {page["page_number"]: page for page in _selby_pages()}
        doc = drive(pages=32, served=list(served.values()), source_name="j23-one-page.pdf",
                    window=32, fails=(26,))
        doc.extract()
        doc.serve(served)
        doc.retry(page_number=26)

        recorded = doc.repository.captures
        assert [row["page_number"] for row in recorded if not row["parse_failed"]] == \
            [n for n in range(1, 33) if n != 26] + [26]
        run_of = {row["analysis_run_id"] for row in recorded if row["page_number"] == 26}
        assert len(run_of) == 2
