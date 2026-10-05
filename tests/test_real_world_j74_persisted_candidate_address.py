"""
J74 — THE PERSISTED CANDIDATE ADDRESS: the proof file.

WHAT THIS FILE IS

The proof for `resolve_candidate_at_recorded_address` — the third rule of
`app/cad_engine/connection_scoped_evidence.py`. J71 answers "which candidate does the
address my CALLER supplied name". J74 answers the other question: "which candidate does the
item's OWN recorded address name" — the address a producer records when it lifts a candidate
out of its page reading, and which it can only record while the page's own array is still in
hand.

It exists because one page reading can state several candidates that no recorded provenance
tells apart. Page 13 of the Selby fixture is exactly that: three candidates, the first
carrying a detail reference and the second and third carrying nothing at all. Nothing in the
recorded provenance distinguishes candidate 1 from candidate 2 — only the position does.

WHAT THIS FILE DOES NOT CLAIM

It does not claim the position is an identity. It is where a candidate sat in one attempt's
reading of one page, and nothing more. It does not claim R3 decides anything: it returns the
candidate at a recorded address, and it never says which candidate is RIGHT — the Selby
Ø18/Ø22 question and the 029X/030X/031X/032X question are untouched here, because a
position is not an opinion.
It does not claim the rule is wired into anything. It is not: no module under `app/`
calls it, and a test below says so.

WHAT IS REAL

Real: the module, the reason codes, the rules, the Selby readings under `tests/data/` (32
pages, 53 candidates over one document read in windows, one page — 26 — read twice), and the
product's own `ReviewSnapshotItem` for one test. Not real: the `drawing_id` and
`analysis_run_id` on the Selby rows are supplied by the helper below, because they are
PROMOTED COLUMNS of a recorded reading rather than payload keys — a payload file carries
neither. The resolver treats both as opaque names either way.

THE EIGHTEEN NUMBERED CASES

`TestTheSelbyFixtures` carries the brief's eighteen required cases, numbered and named:

     1 single candidate, position 0                 10 a negative position refuses
     2 several candidates, position 0               11 a boolean position refuses
     3 several candidates, a middle position        12 a fractional position refuses
     4 several candidates, the final position       13 a numeric string refuses
     5 page 13: candidates a reference cannot tell apart
                                                    14 a missing position refuses
     6 page 14: four candidates, three alike        15 an item with no position keeps R1/R2
     7 page 26: only the recorded attempt            16 the returned candidate is the indexed
     8 a missing attempt refuses                        entry, byte for byte
     9 a position past the end refuses              17 no other position is substituted
                                                    18 no other attempt is substituted

WHAT THE MUTATIONS ARE

Four controlled defects, each registered in `sys.modules` before it executes (the module
uses `from __future__ import annotations`, so a frozen dataclass resolves its annotations
through `sys.modules[cls.__module__]` at class-creation time) and each shown to redden the
tests that pin the property it removes: a position ignored, a recorded attempt ignored, a
position past the end accepted, and another attempt substituted for the recorded one. They
are the evidence that those tests are load-bearing rather than decorative.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import pathlib
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from app.cad_engine import connection_scoped_evidence as cse
from app.cad_engine.connection_review_snapshot import ReviewSnapshotItem

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "app"
MODULE_PATH = APP / "cad_engine" / "connection_scoped_evidence.py"
DATA = REPO / "tests" / "data"

#: Assembled rather than written out, so a scan over this file cannot find this file's own
#: list of the words it is looking for.
_IO_TOKENS = ("op" "en(", "print(", "environ", "getenv", "requests", "httpx", "supabase",
              "postgrest", "storage3", "anthropic", "claude", ".table(", ".rpc(",
              "insert into", "create table", "put_object", "upload(", "execute", " sql")


# =============================================================================
# The committed Selby readings, read-only. Identical shapes to J71's own helpers: the same
# fixture, read the same way, so a J74 result and a J71 result are comparable.
# =============================================================================
WINDOW_1_5 = "selby_square_page_extractions.json"
WINDOW_6_10 = "selby_square_pages_6_10_extractions.json"
WINDOW_11_15 = "selby_square_pages_11_15_extractions.json"
WINDOW_16_20 = "selby_square_pages_16_20_extractions.json"
WINDOW_21_25 = "selby_square_pages_21_25_extractions.json"
WINDOW_26_30 = "selby_square_pages_26_30_extractions.json"
WINDOW_31_32 = "selby_square_pages_31_32_extractions.json"
RECOVERY_26 = "selby_square_page_26_recovery.json"

SELBY_FILES = (WINDOW_1_5, WINDOW_6_10, WINDOW_11_15, WINDOW_16_20, WINDOW_21_25,
               WINDOW_26_30, WINDOW_31_32, RECOVERY_26)

#: The one document those windows are of: a window is an ATTEMPT at it, so the windows share
#: a drawing and differ by run — which is the whole reason a page can have two readings.
SELBY_DRAWING = "drawing-selby-view-bb"


def _run_of(name: str) -> str:
    return f"run-{name.removesuffix('.json')}"


def _reading(drawing_id: str, page_number: int, run_id: str, candidates: list[Any],
             **extra: Any) -> dict[str, Any]:
    """A recorded page reading in the storage layer's own column shape."""
    row = {
        "drawing_id": drawing_id,
        "drawing_set_id": "ds-selby",
        "project_id": "2389c115-664f-4fe4-8b76-ca07aac3719d",
        "page_number": page_number,
        "analysis_run_id": run_id,
        "model": "pdf-vision",
        "parse_failed": False,
        "payload": {
            "page_number": page_number,
            "drawing_number": f"{page_number:03d}",
            "revision": "A",
            "raw_members": [],
            "raw_connections": candidates,
            "parse_failed": False,
        },
    }
    row.update(extra)
    return row


def _all_selby_readings() -> list[dict[str, Any]]:
    """Every committed Selby reading, in the recorded-reading column shape."""
    rows: list[dict[str, Any]] = []
    for name in SELBY_FILES:
        pages = json.loads((DATA / name).read_text(encoding="utf-8"))
        for page in pages:
            rows.append(_reading(
                SELBY_DRAWING, page["page_number"], _run_of(name),
                page.get("raw_connections") or [],
            ))
    return rows


def _by_name(run_name: str) -> str:
    return f"run-{run_name.removesuffix('.json')}"


def _page_row(run_name: str, page_number: int) -> dict[str, Any]:
    wanted = [row for row in _all_selby_readings()
              if row["page_number"] == page_number
              and row["analysis_run_id"] == _by_name(run_name)]
    assert len(wanted) == 1, (run_name, page_number, len(wanted))
    return wanted[0]


def _candidates_on(run_name: str, page_number: int) -> list[dict[str, Any]]:
    """The page reading's OWN array, in the order the reading states it."""
    return _page_row(run_name, page_number)["payload"]["raw_connections"]


# =============================================================================
# Builders. The item is a stand-in for the one attribute this rule reads — its recorded
# evidence — with the product's OWN review item proved separately.
# =============================================================================
@dataclass(frozen=True)
class _Item:
    evidence: dict[str, Any]
    review_package_id: Any = "RP-0004"


def _item(run: Any = None, page: Any = None, index: Any = None,
          drawing: Any = SELBY_DRAWING, detail: Any = None, grid: Any = None,
          **extra: Any) -> _Item:
    """A review item whose evidence records an address. Each term is stated explicitly, so
    a test that wants a term ABSENT simply never puts it in the mapping."""
    evidence: dict[str, Any] = {"drawing_number": "002"}
    if drawing is not None:
        evidence["source_drawing_id"] = drawing
    if page is not None:
        evidence[cse.PROVENANCE_PAGE_KEY] = page
    if run is not None:
        evidence[cse.CAPTURE_RUN_KEY] = run
    if index is not None:
        evidence[cse.CANDIDATE_POSITION_KEY] = index
    if detail is not None:
        evidence[cse.PROVENANCE_DETAIL_KEY] = detail
    if grid is not None:
        evidence[cse.PROVENANCE_GRID_KEY] = grid
    evidence.update(extra)
    return _Item(evidence=evidence)


def _resolve(item: Any, captures: Any):
    return cse.resolve_candidate_at_recorded_address(item, captures)


# =============================================================================
# 1. The recorded address, and what it names.
# =============================================================================
class TestTheRecordedAddressNamesOneCandidate:
    def test_a_single_candidate_page_resolves_at_position_zero(self):
        item = _item(run=_by_name(WINDOW_1_5), page=1, index=0)
        row = _page_row(WINDOW_1_5, 1)
        resolved = _resolve(item, [row])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.matched_by == "RESOLVED_CANDIDATE_INDEX"
        assert resolved.candidate_position == 0

    def test_a_single_candidate_page_resolves_even_when_the_provenance_matches_nothing(self):
        """R1 would answer this page anyway; R3 must answer it too, and the same way."""
        item = _item(run=_by_name(WINDOW_1_5), page=1, index=0,
                     detail="NOTHING LIKE THIS VIEW")
        resolved = _resolve(item, [_page_row(WINDOW_1_5, 1)])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.candidate == _candidates_on(WINDOW_1_5, 1)[0]

    def test_the_returned_candidate_is_the_indexed_entry_not_a_neighbour(self):
        """Case 16, on a page where every candidate is distinct: the returned object equals
        the array's own entry at that position and cannot be confused with either side."""
        for position in range(4):
            page = 27
            row = _page_row(WINDOW_26_30, page)
            item = _item(run=_by_name(WINDOW_26_30), page=page, index=position)
            resolved = _resolve(item, [row])
            assert resolved.candidate == _candidates_on(WINDOW_26_30, page)[position]
            if position:
                assert resolved.candidate != _candidates_on(WINDOW_26_30, page)[position - 1]

    def test_the_returned_candidate_is_a_snapshot_and_not_the_callers_object(self):
        row = _page_row(WINDOW_26_30, 27)
        item = _item(run=_by_name(WINDOW_26_30), page=27, index=1)
        resolved = _resolve(item, [row])
        assert resolved.candidate is not row["payload"]["raw_connections"][1]
        resolved.candidate["connection_type"] = "MUTATED"
        assert row["payload"]["raw_connections"][1]["connection_type"] != "MUTATED"

    def test_the_reading_carries_the_recorded_address_back_out(self):
        """The address a caller gets back is the one the ITEM recorded — the resolution was
        made AT that address, so it is never restated from anywhere else."""
        item = _item(run=_by_name(WINDOW_11_15), page=13, index=2)
        resolved = _resolve(item, [_page_row(WINDOW_11_15, 13)])
        assert resolved.drawing_id == SELBY_DRAWING
        assert resolved.page_number == 13
        assert resolved.analysis_run_id == _by_name(WINDOW_11_15)
        assert resolved.review_package_id == "RP-0004"

    def test_the_products_own_review_item_resolves_through_its_own_evidence(self):
        """The stand-in above is a stand-in; this is the genuine object."""
        item = ReviewSnapshotItem(
            review_package_id="RP-0004", connection_id="C1", decision="REVIEW",
            output_status=None, verification_status=None, last_processed_revision=None,
            blocker_codes=(), warning_codes=(), ai_readings={},
            evidence={
                "source_drawing_id": SELBY_DRAWING, "drawing_number": "002",
                cse.PROVENANCE_PAGE_KEY: 13, cse.PROVENANCE_DETAIL_KEY: None,
                cse.PROVENANCE_GRID_KEY: None,
                cse.CAPTURE_RUN_KEY: _by_name(WINDOW_11_15),
                cse.CANDIDATE_POSITION_KEY: 1,
            },
            provenance={}, tasks=(), generated_files=(),
        )
        resolved = _resolve(item, [_page_row(WINDOW_11_15, 13)])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.candidate_position == 1
        assert resolved.candidate == _candidates_on(WINDOW_11_15, 13)[1]

    def test_it_does_not_mutate_the_item_the_readings_or_the_payload(self):
        row = _page_row(WINDOW_11_15, 14)
        item = _item(run=_by_name(WINDOW_11_15), page=14, index=2)
        before_row = copy.deepcopy(row)
        before_item = dataclasses.asdict(item)
        _resolve(item, [row])
        assert row == before_row
        assert dataclasses.asdict(item) == before_item

    def test_it_is_idempotent(self):
        row = _page_row(WINDOW_11_15, 14)
        item = _item(run=_by_name(WINDOW_11_15), page=14, index=3)
        first = _resolve(item, [row])
        second = _resolve(item, [row])
        assert dataclasses.asdict(first) == dataclasses.asdict(second)

    def test_it_agrees_with_the_rules_above_wherever_they_answer(self):
        """Where a page's provenance already singles out one candidate, R3 must name the
        same one — otherwise the two rules would contradict each other."""
        page = 9
        item = _item(run=_by_name(WINDOW_6_10), page=page, index=1)
        recorded = _candidates_on(WINDOW_6_10, page)
        assert recorded[1]["detail_reference"] == "VIEW B-B"
        via_r3 = _resolve(item, [_page_row(WINDOW_6_10, page)])
        via_r2 = cse.resolve_connection_field_reading(
            _item(run=None, page=page, detail="VIEW B-B"),
            {"drawing_id": SELBY_DRAWING, "page_number": page,
             "analysis_run_id": _by_name(WINDOW_6_10)},
            [_page_row(WINDOW_6_10, page)],
        )
        assert isinstance(via_r2, cse.ConnectionScopedReading)
        assert via_r3.candidate == via_r2.candidate


# =============================================================================
# 2. The brief's eighteen numbered cases, over the real fixtures.
# =============================================================================
class TestTheSelbyFixtures:
    def test_case_01_single_candidate_position_zero(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_16_20), page=17, index=0),
                            [_page_row(WINDOW_16_20, 17)])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.candidate == _candidates_on(WINDOW_16_20, 17)[0]

    def test_case_02_several_candidates_position_zero(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_1_5), page=2, index=0),
                            [_page_row(WINDOW_1_5, 2)])
        assert resolved.candidate == _candidates_on(WINDOW_1_5, 2)[0]
        assert resolved.candidate["detail_reference"] == "VIEW A-A"

    def test_case_03_several_candidates_a_middle_position(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=1),
                            [_page_row(WINDOW_11_15, 13)])
        assert resolved.candidate == _candidates_on(WINDOW_11_15, 13)[1]
        assert resolved.candidate["connects_members"] == ["013X", "PL001"]

    def test_case_04_several_candidates_the_final_position(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=2),
                            [_page_row(WINDOW_11_15, 13)])
        assert resolved.candidate == _candidates_on(WINDOW_11_15, 13)[2]
        assert resolved.candidate["connects_members"] == ["013X", "PL003"]

    def test_case_05_page_thirteen_candidates_a_reference_cannot_tell_apart(self):
        """The reason R3 exists. Page 13 states THREE candidates; the second and the third
        carry no detail reference and no grid reference at all, so nothing the item can
        record about its own connection distinguishes them. The rules above refuse this
        page — several candidates, and nothing the item records matches one of them — and
        R3 answers instead, because the item recorded WHICH one. The two answers really are
        different candidates."""
        page = 13
        row = _page_row(WINDOW_11_15, page)
        first = _resolve(_item(run=_by_name(WINDOW_11_15), page=page, index=1), [row])
        second = _resolve(_item(run=_by_name(WINDOW_11_15), page=page, index=2), [row])
        assert isinstance(first, cse.ConnectionScopedReading)
        assert isinstance(second, cse.ConnectionScopedReading)
        assert first.candidate != second.candidate
        assert first.candidate["connects_members"] == ["013X", "PL001"]
        assert second.candidate["connects_members"] == ["013X", "PL003"]

        # And the rules above really do refuse this page for an item that records nothing.
        ambiguous = cse.resolve_connection_field_reading(
            _item(run=None, page=page, detail=None, grid=None),
            {"drawing_id": SELBY_DRAWING, "page_number": page,
             "analysis_run_id": _by_name(WINDOW_11_15)},
            [row],
        )
        assert isinstance(ambiguous, cse.Unresolved)
        assert ambiguous.reason_code == "AMBIGUOUS_PAGE"

    def test_case_06_page_fourteen_four_candidates_three_alike(self):
        """Page 14 states four candidates; three of them state the SAME grid reference, so
        a grid match is ambiguous there. Every position is still addressable, and each
        returns a different candidate."""
        page = 14
        row = _page_row(WINDOW_11_15, page)
        recorded = _candidates_on(WINDOW_11_15, page)
        assert len(recorded) == 4
        assert [c["grid_reference"] for c in recorded] == [
            "N/S FITTINGS", "N/S FITTINGS", "B/F FITTINGS", "N/S FITTINGS"]
        seen = []
        for position in range(4):
            resolved = _resolve(
                _item(run=_by_name(WINDOW_11_15), page=page, index=position), [row])
            assert resolved.candidate == recorded[position]
            assert resolved.candidate_position == position
            seen.append(resolved.candidate["connects_members"])
        assert len({tuple(members) for members in seen}) == 4

    def test_case_07_page_twenty_six_only_the_recorded_attempt(self):
        """Page 26 was read twice. The two readings state byte-identical candidates, so
        what distinguishes them is the ATTEMPT — and the address that comes back must name
        the one the item recorded, never the other."""
        window = _by_name(WINDOW_26_30)
        recovery = _by_name(RECOVERY_26)
        both = [_page_row(WINDOW_26_30, 26), _page_row(RECOVERY_26, 26)]
        assert (both[0]["payload"]["raw_connections"]
                == both[1]["payload"]["raw_connections"])

        for recorded in (window, recovery):
            resolved = _resolve(_item(run=recorded, page=26, index=0), both)
            assert isinstance(resolved, cse.ConnectionScopedReading)
            assert resolved.analysis_run_id == recorded
            assert resolved.analysis_run_id != (recovery if recorded == window else window)

    def test_case_08_a_missing_attempt_refuses(self):
        resolved = _resolve(_item(run="run-never-happened", page=17, index=0),
                            [_page_row(WINDOW_16_20, 17)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "RUN_ABSENT"

    def test_case_09_a_position_past_the_end_refuses(self):
        page = 13
        row = _page_row(WINDOW_11_15, page)
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=page, index=3), [row])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_OUT_OF_RANGE"

    def test_case_10_a_negative_position_refuses(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=-1),
                            [_page_row(WINDOW_11_15, 13)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_INVALID"

    def test_case_11_a_boolean_position_refuses(self):
        """`True` is an `int` to Python and is NOT a position here."""
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=True),
                            [_page_row(WINDOW_11_15, 13)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_INVALID"

    def test_case_12_a_fractional_position_refuses(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=1.0),
                            [_page_row(WINDOW_11_15, 13)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_INVALID"

    def test_case_13_a_numeric_string_refuses(self):
        for stated in ("1", " 1 ", "0"):
            resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=stated),
                                [_page_row(WINDOW_11_15, 13)])
            assert isinstance(resolved, cse.Unresolved), stated
            assert resolved.reason_code == "CANDIDATE_INDEX_INVALID", stated

    def test_case_14_a_missing_position_keeps_the_rules_above(self):
        """An item that records no position is not refused — it is answered by R1/R2, as
        it was before R3 existed."""
        page = 13
        row = _page_row(WINDOW_11_15, 13)
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=page, detail="VIEW A-A"),
                            [row])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.matched_by == "RESOLVED_PROVENANCE"
        assert resolved.candidate_position == 0

    def test_case_15_an_item_with_no_position_can_still_be_ambiguous(self):
        """The other half of case 14: an item with no position is not quietly resolved to
        the first candidate — where the rules above cannot single one out, it is refused,
        exactly as it was before R3 existed."""
        page = 13
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=page,
                                  grid="NOT A GRID ON THIS PAGE"),
                            [_page_row(WINDOW_11_15, page)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "AMBIGUOUS_PAGE"

    def test_case_16_the_returned_candidate_is_the_indexed_entry(self):
        for run_name, page, position in ((WINDOW_1_5, 3, 1), (WINDOW_6_10, 9, 2),
                                         (WINDOW_26_30, 28, 1), (WINDOW_31_32, 32, 0)):
            resolved = _resolve(_item(run=_by_name(run_name), page=page, index=position),
                                [_page_row(run_name, page)])
            assert resolved.candidate == _candidates_on(run_name, page)[position], (
                run_name, page, position)
            assert resolved.candidate_position == position

    def test_case_17_no_other_position_is_substituted(self):
        """Page 27's four candidates are all distinct. Asking for one must never return
        another — not the first, not the last, not the nearest."""
        page = 27
        row = _page_row(WINDOW_26_30, page)
        recorded = _candidates_on(WINDOW_26_30, page)
        for position in range(len(recorded)):
            resolved = _resolve(
                _item(run=_by_name(WINDOW_26_30), page=page, index=position), [row])
            neighbours = [c for other, c in enumerate(recorded) if other != position]
            assert resolved.candidate not in neighbours

    def test_case_18_no_other_attempt_is_substituted(self):
        """The recorded attempt is absent from the captures; an attempt that IS present must
        not be used in its place, even though it is the very attempt a caller might call
        authoritative."""
        resolved = _resolve(_item(run=_by_name(RECOVERY_26), page=26, index=0),
                            [_page_row(WINDOW_26_30, 26)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "RUN_ABSENT"


# =============================================================================
# 3. Every way a position can be wrong, named.
# =============================================================================
class TestEveryWrongPositionIsRefusedByName:
    def test_a_position_stated_as_none_refuses(self):
        """`None` IS stated — the key is present — so it is a wrong position and not an
        absent one. The two are different facts and get different answers."""
        resolved = _resolve(_item(run=_by_name(WINDOW_1_5), page=1, index=None,
                                  **{cse.CANDIDATE_POSITION_KEY: None}),
                            [_page_row(WINDOW_1_5, 1)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_INVALID"

    def test_a_blank_string_position_refuses(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_1_5), page=1, index=""),
                            [_page_row(WINDOW_1_5, 1)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_INVALID"

    def test_a_list_or_mapping_position_refuses(self):
        for stated in ([0], {"index": 0}, (0,), 0.5, -0.5):
            resolved = _resolve(
                _item(run=_by_name(WINDOW_1_5), page=1,
                      **{cse.CANDIDATE_POSITION_KEY: stated}),
                [_page_row(WINDOW_1_5, 1)])
            assert isinstance(resolved, cse.Unresolved), stated
            assert resolved.reason_code == "CANDIDATE_INDEX_INVALID", stated

    def test_the_invalid_refusal_is_never_reused_for_an_absent_position(self):
        """An absent position is not an invalid one: it reaches the rules above instead."""
        resolved = _resolve(_item(run=_by_name(WINDOW_1_5), page=1),
                            [_page_row(WINDOW_1_5, 1)])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.matched_by == "RESOLVED_SINGLE_CANDIDATE"

    def test_no_other_code_is_stated_for_a_wrong_position(self):
        for stated in (True, 1.0, "1", -1, None, []):
            resolved = _resolve(
                _item(run=_by_name(WINDOW_11_15), page=13,
                      **{cse.CANDIDATE_POSITION_KEY: stated}),
                [_page_row(WINDOW_11_15, 13)])
            assert resolved.reason_code == "CANDIDATE_INDEX_INVALID", stated

    def test_a_position_that_is_merely_large_is_out_of_range_and_not_invalid(self):
        for stated in (3, 12, 999999):
            resolved = _resolve(
                _item(run=_by_name(WINDOW_11_15), page=13, index=stated),
                [_page_row(WINDOW_11_15, 13)])
            assert isinstance(resolved, cse.Unresolved), stated
            assert resolved.reason_code == "CANDIDATE_INDEX_OUT_OF_RANGE", stated

    def test_a_position_into_a_zero_candidate_page_is_out_of_range(self):
        """Page 12 was read and states NO candidate at all. Position 0 is not a position
        there, and the refusal that says so is about the position, not about the page."""
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=12, index=0),
                            [_page_row(WINDOW_11_15, 12)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_OUT_OF_RANGE"

    def test_a_position_into_a_page_that_was_never_read_refuses_the_page_first(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=33, index=0),
                            _all_selby_readings())
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "EVIDENCE_PAGE_ABSENT"

    def test_the_range_check_is_over_the_cited_attempts_own_array(self):
        """Page 26's two attempts both state one candidate; a position of 1 is out of range
        for both, and the refusal must come from the cited one."""
        resolved = _resolve(_item(run=_by_name(RECOVERY_26), page=26, index=1),
                            [_page_row(WINDOW_26_30, 26), _page_row(RECOVERY_26, 26)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "CANDIDATE_INDEX_OUT_OF_RANGE"
        assert _by_name(RECOVERY_26) in resolved.detail


# =============================================================================
# 4. Where the address comes from — and, decisively, where it does not.
# =============================================================================
class TestTheAddressIsReadFromTheItemAndNowhereElse:
    def test_a_position_without_an_attempt_refuses_rather_than_choosing_one(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_26_30), page=26, index=0,
                                  **{cse.CAPTURE_RUN_KEY: None}),
                            [_page_row(WINDOW_26_30, 26), _page_row(RECOVERY_26, 26)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "RUN_ABSENT"

    def test_an_item_stating_no_attempt_at_all_refuses_the_same_way(self):
        resolved = _resolve(_item(page=26, index=0),
                            [_page_row(WINDOW_26_30, 26), _page_row(RECOVERY_26, 26)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "RUN_ABSENT"

    def test_the_recorded_drawing_is_the_one_searched_and_no_other(self):
        resolved = _resolve(
            _item(run=_by_name(WINDOW_26_30), page=26, index=0, drawing="drawing-selby"),
            _all_selby_readings())
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "EVIDENCE_PAGE_ABSENT"

    def test_a_submission_order_would_answer_differently_and_is_never_used(self):
        """The decisive negative. The captures are supplied in an order that puts OTHER
        pages first and the cited page last, and in a different order again on the second
        call. If any order were being read, one of these would answer."""
        page = 13
        item = _item(run=_by_name(WINDOW_11_15), page=page, index=2)
        rows = _all_selby_readings()
        forward = _resolve(item, rows)
        reversed_rows = list(reversed(rows))
        backward = _resolve(item, reversed_rows)
        assert dataclasses.asdict(forward) == dataclasses.asdict(backward)
        assert forward.candidate == _candidates_on(WINDOW_11_15, page)[2]

    def test_an_unrelated_reading_of_the_same_numbered_page_elsewhere_is_not_consulted(self):
        """A page number is only a page number inside a drawing. A reading of page 13 of a
        DIFFERENT drawing must not answer for this one."""
        other = _reading("drawing-other", 13, _by_name(WINDOW_11_15),
                         [{"detail_reference": "VIEW Z-Z"}])
        resolved = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=1),
                            [other, _page_row(WINDOW_11_15, 13)])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.candidate == _candidates_on(WINDOW_11_15, 13)[1]
        assert resolved.candidate != other["payload"]["raw_connections"][0]

    def test_the_position_the_item_records_is_the_only_position_used(self):
        """The capture row's own payload page number, model, drawing set and ids are all
        present and none of them is read."""
        row = _page_row(WINDOW_26_30, 27)
        decorated = dict(row, model="something-else", drawing_set_id="ds-other",
                         page_number_label=27)
        resolved = _resolve(_item(run=_by_name(WINDOW_26_30), page=27, index=3), [decorated])
        assert resolved.candidate == _candidates_on(WINDOW_26_30, 27)[3]

    def test_a_missing_drawing_term_refuses_the_page_and_does_not_fall_back(self):
        resolved = _resolve(
            _item(run=_by_name(WINDOW_26_30), page=26, index=0, drawing=None),
            _all_selby_readings())
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "EVIDENCE_PAGE_ABSENT"

    def test_a_missing_page_term_refuses_the_page_and_does_not_fall_back(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_26_30), index=0),
                            _all_selby_readings())
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "EVIDENCE_PAGE_ABSENT"


# =============================================================================
# 5. Items recorded before positions were recorded. No backfill, no synthesis.
# =============================================================================
class TestHistoricalItemsKeepTheRulesAbove:
    def test_a_historical_item_with_one_candidate_resolves_as_it_always_did(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_16_20), page=17),
                            [_page_row(WINDOW_16_20, 17)])
        assert isinstance(resolved, cse.ConnectionScopedReading)
        assert resolved.matched_by == "RESOLVED_SINGLE_CANDIDATE"
        assert resolved.candidate_position == 0

    def test_a_historical_item_with_provenance_resolves_as_it_always_did(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_1_5), page=4, detail="VIEW B-B"),
                            [_page_row(WINDOW_1_5, 4)])
        assert resolved.matched_by == "RESOLVED_PROVENANCE"
        assert resolved.candidate == _candidates_on(WINDOW_1_5, 4)[1]

    def test_a_historical_item_that_cannot_be_resolved_is_still_ambiguous(self):
        resolved = _resolve(_item(run=_by_name(WINDOW_1_5), page=4,
                                  detail="NOT A VIEW ON THIS PAGE"),
                            [_page_row(WINDOW_1_5, 4)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "AMBIGUOUS_PAGE"

    def test_a_historical_item_with_no_attempt_still_refuses_the_attempt(self):
        resolved = _resolve(_item(page=4, detail="VIEW B-B"),
                            [_page_row(WINDOW_1_5, 4)])
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "RUN_ABSENT"

    def test_a_historical_item_with_no_address_still_refuses_the_page(self):
        resolved = _resolve(_item(), _all_selby_readings())
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "EVIDENCE_PAGE_ABSENT"

    def test_no_position_is_synthesised_for_a_historical_item(self):
        """The one candidate on this page is at position 0 — a rule that guessed a position
        where none was recorded would have to say so, and it says nothing: the rule named is
        the one that was always named."""
        resolved = _resolve(_item(run=_by_name(WINDOW_21_25), page=23),
                            [_page_row(WINDOW_21_25, 23)])
        assert resolved.matched_by == "RESOLVED_SINGLE_CANDIDATE"
        assert resolved.matched_by != "RESOLVED_CANDIDATE_INDEX"

    def test_an_item_with_no_evidence_at_all_still_refuses_the_page(self):
        @dataclass(frozen=True)
        class NoEvidence:
            pass
        resolved = _resolve(NoEvidence(), _all_selby_readings())
        assert isinstance(resolved, cse.Unresolved)
        assert resolved.reason_code == "EVIDENCE_PAGE_ABSENT"


# =============================================================================
# 6. The shape of the answer, and the closed vocabulary it is stated in.
# =============================================================================
class TestTheShapeOfTheAnswer:
    def test_the_refusal_codes_are_the_six_and_the_recorded_position_adds_two(self):
        assert cse.REASON_CODES == (
            "EVIDENCE_PAGE_ABSENT", "AMBIGUOUS_PAGE", "CANDIDATE_ABSENT", "RUN_ABSENT",
            "CANDIDATE_INDEX_INVALID", "CANDIDATE_INDEX_OUT_OF_RANGE",
        )

    def test_the_resolution_rules_are_the_three_and_the_recorded_position_adds_one(self):
        assert cse.RESOLUTION_RULES == (
            "RESOLVED_SINGLE_CANDIDATE", "RESOLVED_PROVENANCE", "RESOLVED_CANDIDATE_INDEX",
        )

    def test_the_four_recorded_terms_are_the_evidence_models_own(self):
        assert cse.RECORDED_ORIGIN_KEYS == (
            "source_drawing_id", "source_page", "analysis_run_id", "candidate_index",
        )
        assert cse.PROVENANCE_DRAWING_KEY == "source_drawing_id"
        assert cse.CANDIDATE_POSITION_KEY == "candidate_index"

    def test_the_two_new_terms_are_not_provenance_keys(self):
        """R2 compares its keys against a candidate's own values, and a candidate carries
        neither of these: adding them there would make every provenance match fail."""
        assert cse.CANDIDATE_POSITION_KEY not in cse.PROVENANCE_KEYS
        assert cse.PROVENANCE_DRAWING_KEY not in cse.PROVENANCE_KEYS
        assert cse.PROVENANCE_KEYS == ("source_page", "detail_reference", "grid_reference")

    def test_the_two_entry_points_are_separate_and_neither_calls_the_other(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert source.count("def resolve_candidate_at_recorded_address(") == 1
        assert source.count("def resolve_connection_field_reading(") == 1
        # R3 delegates the historical path to the rules above; the rules above never reach
        # back into R3, so there is no cycle and no silent preference between them.
        above = source.split("def resolve_connection_field_reading(", 1)[1]
        above = above.split("def resolve_candidate_at_recorded_address(", 1)[0]
        assert "resolve_candidate_at_recorded_address(" not in above

    def test_the_address_terms_are_unaffected_by_the_new_rule(self):
        assert cse.ADDRESS_TERMS == (
            "drawing_id", "page_number", "analysis_run_id", "document_id",
            "annotation_x", "annotation_y", "extractor_version", "anchor",
        )
        assert cse.REQUIRED_ADDRESS_TERMS == ("drawing_id", "page_number", "analysis_run_id")

    def test_a_seventh_refusal_and_a_fourth_rule_cannot_be_stated(self):
        for code in ("", "OTHER", "candidate_index_invalid", "CANDIDATE_INDEX_OUT_OF_RANGE "):
            with pytest.raises(ValueError):
                cse.Unresolved(reason_code=code, detail="x")
        for rule in ("", "RESOLVED_NEAREST", "resolved_candidate_index"):
            with pytest.raises(ValueError):
                cse.ConnectionScopedReading(
                    field_name=None, review_package_id="RP-0004", drawing_id="d",
                    page_number=1, analysis_run_id="r", document_id=None, annotation_x=None,
                    annotation_y=None, extractor_version=None, anchor=(), candidate_position=0,
                    matched_by=rule, candidate={"detail_reference": None},
                )

    def test_the_details_are_deterministic(self):
        for stated in (-1, True, "1", 99):
            first = _resolve(
                _item(run=_by_name(WINDOW_11_15), page=13, index=stated),
                [_page_row(WINDOW_11_15, 13)])
            second = _resolve(
                _item(run=_by_name(WINDOW_11_15), page=13, index=stated),
                [_page_row(WINDOW_11_15, 13)])
            assert first.detail == second.detail
            assert first.reason_code == second.reason_code


# =============================================================================
# 7. Mutation controls — proof that the tests above are load-bearing.
# =============================================================================
def _mutant(old: str, new: str, *, name: str = "j74_mutant") -> types.ModuleType:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(MODULE_PATH)
    sys.modules[name] = module
    try:
        exec(compile(source.replace(old, new), "<j74-mutant>", "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


#: The recorded position ignored: every page resolves to its first candidate.
_M_POSITION_IGNORED = (
    "    return _resolved(reference, item, recorded, candidates[recorded],\n"
    "                     RESOLVED_CANDIDATE_INDEX)\n",
    "    return _resolved(reference, item, 0, candidates[0],\n"
    "                     RESOLVED_CANDIDATE_INDEX)\n",
)

#: The recorded attempt ignored: the page's FIRST recorded attempt is used instead, so an
#: item that records no attempt resolves, and one whose attempt is the second reading is
#: answered with the first.
_M_RUN_IGNORED = (
    "    recorded_run = _term(reference, CAPTURE_RUN_KEY)\n",
    "    recorded_run = _term(list(captures)[0] if captures else {}, CAPTURE_RUN_KEY)\n",
)

#: A position the reading does not reach accepted: it is quietly moved onto the LAST
#: candidate instead of being refused, which is the clamping the brief forbids.
_M_OUT_OF_RANGE_ACCEPTED = (
    "    if not 0 <= recorded < len(candidates):\n",
    "    if not 0 <= recorded < len(candidates):\n"
    "        recorded = len(candidates) - 1\n"
    "    if False and not 0 <= recorded < len(candidates):\n",
)

#: Another attempt substituted for the cited one whenever the cited one is not recorded.
_M_RUN_SUBSTITUTED = (
    "    if not cited:\n",
    "    if not cited:\n"
    "        cited = list(page_readings)\n"
    "    if len(cited) == 0:\n",
)


class TestMutations:
    def test_m01_ignoring_the_recorded_position_reddens_the_position_tests(self):
        module = _mutant(*_M_POSITION_IGNORED)
        row = _page_row(WINDOW_11_15, 13)
        item = _item(run=_by_name(WINDOW_11_15), page=13, index=2)
        # The page's own second and third candidates are DIFFERENT objects, so a rule that
        # ignores the recorded position answers the wrong one.
        assert module.resolve_candidate_at_recorded_address(item, [row]).candidate \
            != _candidates_on(WINDOW_11_15, 13)[2]
        assert module.resolve_candidate_at_recorded_address(item, [row]).candidate \
            == _candidates_on(WINDOW_11_15, 13)[0]

    def test_m01b_every_position_test_reddens_under_ignoring_the_recorded_position(self):
        module = _mutant(*_M_POSITION_IGNORED, name="j74_mutant_position")
        page = 27
        row = _page_row(WINDOW_26_30, page)
        wrong = []
        for position in range(4):
            got = module.resolve_candidate_at_recorded_address(
                _item(run=_by_name(WINDOW_26_30), page=page, index=position), [row])
            if got.candidate != _candidates_on(WINDOW_26_30, page)[position]:
                wrong.append(position)
        assert wrong == [1, 2, 3], "positions 1..3 must all be answered wrongly"

    def test_m02_ignoring_the_recorded_attempt_reddens_the_attempt_tests(self):
        """Two attempts at one page, stating DIFFERENT candidates, with the item recording
        the second. The real rule answers the second; the mutant answers the first."""
        first = _reading("drawing-two-attempts", 5, "run-first",
                         [{"detail_reference": "FIRST"}])
        second = _reading("drawing-two-attempts", 5, "run-second",
                          [{"detail_reference": "SECOND"}])
        item = _item(run="run-second", page=5, index=0, drawing="drawing-two-attempts")
        real = _resolve(item, [first, second])
        assert real.candidate == {"detail_reference": "SECOND"}
        assert real.analysis_run_id == "run-second"

        module = _mutant(*_M_RUN_IGNORED)
        got = module.resolve_candidate_at_recorded_address(item, [first, second])
        assert got.candidate == {"detail_reference": "FIRST"}

    def test_m02b_the_missing_attempt_refusal_reddens_under_ignoring_the_attempt(self):
        first = _reading("drawing-two-attempts", 5, "run-first",
                         [{"detail_reference": "FIRST"}])
        real = _resolve(_item(page=5, index=0, drawing="drawing-two-attempts"), [first])
        assert isinstance(real, cse.Unresolved)
        assert real.reason_code == "RUN_ABSENT"

        module = _mutant(*_M_RUN_IGNORED, name="j74_mutant_run")
        got = module.resolve_candidate_at_recorded_address(
            _item(page=5, index=0, drawing="drawing-two-attempts"), [first])
        assert not isinstance(got, module.Unresolved)

    def test_m02c_the_address_carried_back_cannot_detect_the_attempt_mutation(self):
        """The reading carries the address the ITEM recorded, never the one consulted, so
        comparing that address would pass under the mutation. This is why the controls
        above compare the CANDIDATE."""
        first = _reading("drawing-two-attempts", 5, "run-first",
                         [{"detail_reference": "FIRST"}])
        second = _reading("drawing-two-attempts", 5, "run-second",
                          [{"detail_reference": "SECOND"}])
        item = _item(run="run-second", page=5, index=0, drawing="drawing-two-attempts")
        module = _mutant(*_M_RUN_IGNORED, name="j74_mutant_run_address")
        got = module.resolve_candidate_at_recorded_address(item, [first, second])
        assert got.analysis_run_id == "run-second", "the address is the item's own"
        assert got.candidate == {"detail_reference": "FIRST"}, "the reading consulted is not"

    def test_m03_accepting_an_out_of_range_position_reddens_the_range_tests(self):
        row = _page_row(WINDOW_11_15, 13)
        item = _item(run=_by_name(WINDOW_11_15), page=13, index=99)
        real = _resolve(item, [row])
        assert isinstance(real, cse.Unresolved)
        assert real.reason_code == "CANDIDATE_INDEX_OUT_OF_RANGE"

        module = _mutant(*_M_OUT_OF_RANGE_ACCEPTED)
        got = module.resolve_candidate_at_recorded_address(item, [row])
        # Not refused any more: quietly moved onto the last candidate.
        assert not isinstance(got, module.Unresolved)
        assert got.candidate == _candidates_on(WINDOW_11_15, 13)[-1]
        assert got.candidate_position == 2

    def test_m03b_a_position_exactly_one_past_the_end_wraps_under_the_mutation(self):
        module = _mutant(*_M_OUT_OF_RANGE_ACCEPTED, name="j74_mutant_range")
        got = module.resolve_candidate_at_recorded_address(
            _item(run=_by_name(WINDOW_11_15), page=13, index=3),
            [_page_row(WINDOW_11_15, 13)])
        assert not isinstance(got, module.Unresolved)
        assert got.candidate == _candidates_on(WINDOW_11_15, 13)[-1]
        # The real rule refuses this same position by name.
        real = _resolve(_item(run=_by_name(WINDOW_11_15), page=13, index=3),
                        [_page_row(WINDOW_11_15, 13)])
        assert real.reason_code == "CANDIDATE_INDEX_OUT_OF_RANGE"

    def test_m04_substituting_another_attempt_reddens_the_substitution_test(self):
        """The cited attempt is absent from the readings; the mutant silently answers with
        the one attempt that IS recorded, which the rule forbids."""
        other = _reading("drawing-substituted", 7, "run-other",
                         [{"detail_reference": "OTHER"}])
        item = _item(run="run-recorded", page=7, index=0, drawing="drawing-substituted")
        real = _resolve(item, [other])
        assert isinstance(real, cse.Unresolved)
        assert real.reason_code == "RUN_ABSENT"

        module = _mutant(*_M_RUN_SUBSTITUTED)
        got = module.resolve_candidate_at_recorded_address(item, [other])
        assert not isinstance(got, module.Unresolved)
        assert got.candidate == {"detail_reference": "OTHER"}

    def test_m04b_page_26_reveals_the_substitution_only_as_a_lost_refusal(self):
        """Page 26's two readings state byte-identical candidates, so nothing about the
        candidate can tell the recorded attempt from the superseded one. What the real rule
        does here is REFUSE, and the mutation removes that refusal — the only visible
        difference there is."""
        both = [_page_row(WINDOW_26_30, 26), _page_row(RECOVERY_26, 26)]
        assert (both[0]["payload"]["raw_connections"]
                == both[1]["payload"]["raw_connections"])
        real = _resolve(_item(run=_by_name(RECOVERY_26), page=26, index=0),
                        [_page_row(WINDOW_26_30, 26)])
        assert isinstance(real, cse.Unresolved)
        assert real.reason_code == "RUN_ABSENT"

        module = _mutant(*_M_RUN_SUBSTITUTED, name="j74_mutant_substitute")
        got = module.resolve_candidate_at_recorded_address(
            _item(run=_by_name(RECOVERY_26), page=26, index=0),
            [_page_row(WINDOW_26_30, 26)])
        assert not isinstance(got, module.Unresolved)
        assert got.candidate == both[0]["payload"]["raw_connections"][0]
        assert got.candidate == both[1]["payload"]["raw_connections"][0]

    def test_every_mutation_anchor_is_unique_in_the_module(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for anchor, _ in (_M_POSITION_IGNORED, _M_RUN_IGNORED, _M_OUT_OF_RANGE_ACCEPTED,
                          _M_RUN_SUBSTITUTED):
            assert source.count(anchor) == 1, anchor


# =============================================================================
# 8. The boundary: what this milestone did not touch.
# =============================================================================
class TestNothingWasWiredAndNothingElseWasTouched:
    def test_no_module_under_app_calls_the_new_rule(self):
        """J74's rule had no caller. J80 built the one declared exception — the consumer
        boundary, named here rather than absorbed — and the guard still holds over the rest
        of the tree: the consumer is the ONLY module that names R3.

        J81 then bound that boundary to the production read path, and the port that does the
        binding — `app/production_recorded_readings.py` — is DECLARED here rather than
        absorbed. Both lists stay exact, so the two facts this guard has always stated are
        still stated: the resolver has exactly one namer, and the boundary has exactly one
        caller, which reaches it through `resolve_cited_reading` and never through the rule
        itself."""
        callers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if path.name != "connection_scoped_evidence.py"
            and "resolve_candidate_at_recorded_address" in path.read_text(encoding="utf-8")
        )
        assert callers == ["app/cad_engine/cited_candidate_resolution.py"], callers
        reaching_it = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "cited_candidate_resolution" in path.read_text(encoding="utf-8")
        )
        assert reaching_it == ["app/production_recorded_readings.py"], reaching_it

    def test_no_route_names_the_new_rule(self):
        for route_file in (APP / "main.py", APP / "production_review", APP / "review_ui"):
            if route_file.is_file():
                assert "resolve_candidate_at_recorded_address" not in \
                    route_file.read_text(encoding="utf-8")
            else:
                for path in route_file.rglob("*.py"):
                    assert "resolve_candidate_at_recorded_address" not in \
                        path.read_text(encoding="utf-8"), path

    def test_the_module_still_imports_no_application_module(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith("import ") or line.startswith("from "):
                assert "import app" not in line and "from app" not in line, line

    def test_the_module_still_names_no_engineering_vocabulary(self):
        low = MODULE_PATH.read_text(encoding="utf-8").lower()
        for token in ("connection_id", "connected_member_marks", "plate", "holes",
                      "location", "attachments", "material", "welds", "bolts",
                      "citation", "reconcil"):
            assert token not in low, token

    def test_the_module_still_reaches_no_io(self):
        low = MODULE_PATH.read_text(encoding="utf-8").lower()
        for token in _IO_TOKENS:
            assert token not in low, token

    def test_no_citation_row_and_no_conflict_state_is_written_or_named(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("review_revision", "persist_", "connection_review_item_citations",
                          "v_capture_project", "submission_index", "ordinal",
                          "review_package_id\":"):
            assert forbidden not in source, forbidden

    def test_the_disputed_engineering_literals_are_absent(self):
        low = MODULE_PATH.read_text(encoding="utf-8").lower()
        for token in ("029x", "030x", "031x", "032x", "transmittal", "diameter", "ø",
                      "fab", "assembly", "isometric"):
            assert token not in low, token

    def test_the_two_questions_j74_was_forbidden_to_resolve_are_untouched(self):
        """Ø18 vs Ø22 and 029X vs 030X/031X/032X are engineering disagreements. A rule that
        returns the candidate at a recorded address has no view on either, and the fixtures
        that carry them — pages 13 and 14, whose candidates state `22mm diameter` and `M22`
        — are resolved by POSITION only, with no comparison of what they say."""
        page = 13
        first = _resolve(_item(run=_by_name(WINDOW_11_15), page=page, index=0),
                         [_page_row(WINDOW_11_15, page)])
        assert first.candidate["bolts"] == [{"grade": None, "quantity": 4,
                                             "size": "22mm diameter"}]
        # The same page's other candidates are returned just as literally, and nothing in
        # the answer prefers one diameter, grade or mark over another.
        third = _resolve(_item(run=_by_name(WINDOW_11_15), page=page, index=2),
                         [_page_row(WINDOW_11_15, page)])
        assert third.candidate["connects_members"] == ["013X", "PL003"]
        assert "ø" not in json.dumps(third.candidate).lower()
