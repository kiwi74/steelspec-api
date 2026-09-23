"""
Milestone 7AZ — the deterministic multi-increment drawing-set analysis
workflow (tests/test_real_world_incremental_continuation.py).

Extends the 7AY incremental capability (single 5-page increment) into a
multi-increment workflow over ACCUMULATED evidence: pages 1-5, then
6-10, then 11-15, then 16-20, without ever silently rebuilding,
replacing, mutating or losing prior evidence. This file maps the 7AZ
contract section by section:

  - begin/continue build an AccumulatedAnalysis whose accounting puts
    every page of the declared universe in exactly one of
    ANALYSED / PARSE_FAILED / NOT_ANALYSED (the existing 7AX vocabulary;
    NOT_ANALYSED is derived, never guessed, and never inferred from
    filenames);
  - a continuation must declare the accounting's next NOT_ANALYSED page;
    a gap (prior 1-10, new 16-20) is recorded explicitly as NOT_ANALYSED
    pages 11-15 — never silently contiguous;
  - all refusals (duplicate, overlap, invented, below-first, beyond-
    count, contradictory status, backward, misdeclared, tampered
    accumulated evidence) name their reason;
  - M12 stays M12, SQ4 12mm stays SQ4 12mm, "300" stays "300",
    300PLUS never appears; prior evidence is preserved field-for-field;
  - the accumulated state feeds the genuine 7AX production-coverage
    evaluation through intake_for_accumulated without bypass, and an
    incomplete set stays INCOMPLETE there.

The negative proofs A-T are marked [A]...[T] in test names. The real
pages 1-5 and 6-10 captures are prior evidence — the tests here prove
they are preserved and never rewritten; the genuine 11-15 slice tests
skip (honestly, with the exact capture command) until that capture
exists.
"""
import ast
import copy
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.cad_engine import incremental_analysis as ia
from app.cad_engine import incremental_continuation as ic
from app.cad_engine.production_coverage import (
    DISPOSITION_UNRESOLVED,
    PAGE_STATUS_ANALYSED,
    PAGE_STATUS_PARSE_FAILED,
    PROJECT_STATUS_INCOMPLETE,
    evaluate_production_coverage,
)
from app.cad_engine.project_extraction_intake import INTAKE_SCOPE_STATEMENT
from tests.test_real_world_incremental_analysis import (
    _load_new_pages,
    _marks_of,
    _prior_pages,
    _synthetic_entry,
    _synthetic_page,
    _workflow_over,
    needs_pages_6_10_capture,
    needs_selby_capture,
)
from tests.test_real_world_material_extraction import SELBY_MATERIAL
from tests.test_real_world_multi_member_connection import (
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
)

REPO = Path(__file__).resolve().parents[1]
CAPTURE_11_15_PATH = REPO / "tests" / "data" / "selby_square_pages_11_15_extractions.json"

# The sha256 of the 7AY-ACCEPTED pages 6-10 capture — pinned so any
# change to that accepted artifact fails loudly here.
NEW_CAPTURE_SHA256 = "4c2acbadea5bdc9ce67a5d1f946a693997d9606f944b18c767a3b8a07ea35806"

needs_pages_11_15_capture = pytest.mark.skipif(
    not CAPTURE_11_15_PATH.exists(),
    reason=f"no genuine pages 11-15 extraction exists at {CAPTURE_11_15_PATH.relative_to(REPO)}; "
           "produce one with ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py "
           "--first-page 11 --max-pages 5 \"/Users/chad/Downloads/FABs.pdf\" "
           "tests/data/selby_square_pages_11_15_extractions.json (run it from a terminal with "
           "Downloads access — macOS TCC can otherwise deny the PDF read).",
)

# ---------------------------------------------------------------------------
# synthetic evidence (controlled records; the module never sees a fabricated
# page that these tests do not intend)
# ---------------------------------------------------------------------------

SYNTH_COUNT = 10
SYNTH_FIRST = 1


def _slice(start, end, *, details=(), parse_failed_at=()):
    pages = []
    for number in range(start, end + 1):
        raw_connections = ()
        if number in details:
            raw_connections = (_synthetic_entry(f"SYNTH-D{number}"),)
        pages.append(_synthetic_page(
            number, parse_failed=(number in parse_failed_at),
            raw_connections=raw_connections))
    return pages


SLICE_A = _slice(1, 3, details=(1, 3))
SLICE_B = _slice(4, 6, details=(4,))
SLICE_C = _slice(7, 9, details=(7,))

MATERIAL_PAGES = [
    _synthetic_page(1, raw_connections=[_synthetic_entry(
        "SYNTH-D1", bolts=[{"quantity": 4, "size": "M12", "grade": "8.8"}],
        material="300")]),
    _synthetic_page(2),
]


def _begin(pages, *, count=SYNTH_COUNT, first=SYNTH_FIRST):
    return ic.begin_incremental_analysis(
        pages, drawing_set_page_count=count, drawing_set_first_page=first)


def _tampered(accumulated, transform):
    """The accumulated state with its pages altered by `transform` — a
    controlled tamper used only to prove the digest refuses it."""
    return dataclasses.replace(
        accumulated, pages=transform(copy.deepcopy(accumulated.pages)))


def _continue_expecting(accumulated, pages, *, expected):
    return ic.continue_incremental_analysis(
        accumulated, pages, expected_next_first_page=expected)


# ---------------------------------------------------------------------------
# begin + page accounting
# ---------------------------------------------------------------------------

class TestBeginAndAccounting:

    def test_begin_accounts_every_universe_page_exactly_once(self):
        state = _begin(SLICE_A)
        assert state.pages == SLICE_A
        assert state.analysed_page_numbers == (1, 2, 3)
        assert state.parse_failed_page_numbers == ()
        assert state.not_analysed_page_numbers == (4, 5, 6, 7, 8, 9, 10)
        assert state.digest == ia.evidence_digest(SLICE_A)
        assert state.increments == (ic.IncrementRecord(
            expected_first_page=1, first_page=1, last_page=3,
            slice_digest=ia.evidence_digest(SLICE_A),
            analysed_pages=3, parse_failed_pages=0),)

    def test_a_page_number_below_one_is_refused(self):  # [C]
        with pytest.raises(ValueError, match="below the declared first page"):
            _begin(_slice(0, 2))

    def test_a_page_number_above_the_page_count_is_refused_at_begin(self):  # [D]
        with pytest.raises(ValueError, match="beyond the drawing set's page count"):
            _begin(_slice(9, 11))

    def test_a_duplicate_page_within_the_slice_is_refused(self):
        with pytest.raises(ValueError, match="more than once"):
            _begin([_synthetic_page(1), _synthetic_page(2), _synthetic_page(2)])

    def test_a_contradictory_page_status_is_refused_at_begin(self):  # [F]
        contradictory = _synthetic_page(2, parse_failed=True,
                                        raw_connections=[_synthetic_entry("SYNTH-D2")])
        with pytest.raises(ValueError, match="contradictory page status"):
            _begin([_synthetic_page(1), contradictory])

    def test_an_empty_capture_is_refused(self):
        with pytest.raises(ValueError, match="contains no pages"):
            _begin([])

    def test_non_positive_declarations_are_refused(self):
        with pytest.raises(ValueError, match="positive integers"):
            _begin(SLICE_A, first=0)
        with pytest.raises(ValueError, match="positive integers"):
            _begin(SLICE_A, count=0)

    def test_a_first_page_beyond_the_page_count_is_refused(self):
        with pytest.raises(ValueError, match="beyond the drawing set's page count"):
            _begin(SLICE_A, count=10, first=11)

    def test_a_non_integer_page_number_is_refused(self):
        with pytest.raises(ValueError, match="not a positive integer"):
            _begin([_synthetic_page("x")])

    def test_begin_deep_copies_so_caller_mutation_never_reaches_the_state(self):  # [P]
        originals = _slice(1, 3, details=(1, 3))
        snapshot = copy.deepcopy(originals)
        state = _begin(originals)
        originals[0]["raw_connections"][0]["material"] = "300PLUS"
        originals[0]["page_number"] = 99
        originals.append(_synthetic_page(11))
        assert state.pages == snapshot
        assert state.digest == ia.evidence_digest(snapshot)


# ---------------------------------------------------------------------------
# continuation: contiguity, multi-increment, gaps
# ---------------------------------------------------------------------------

class TestContinuation:

    def test_a_contiguous_continuation_appends_prior_first(self):
        state = _continue_expecting(_begin(SLICE_A), SLICE_B, expected=4)
        assert state.pages == SLICE_A + SLICE_B
        assert state.analysed_page_numbers == (1, 2, 3, 4, 5, 6)
        assert state.not_analysed_page_numbers == (7, 8, 9, 10)
        assert state.increments[0] == _begin(SLICE_A).increments[0]
        assert state.increments[1] == ic.IncrementRecord(
            expected_first_page=4, first_page=4, last_page=6,
            slice_digest=ia.evidence_digest(SLICE_B),
            analysed_pages=3, parse_failed_pages=0)
        assert state.digest == ia.evidence_digest(state.pages)

    def test_multi_increment_continuation_from_the_accumulated_state(self):
        first = _begin(SLICE_A)
        second = _continue_expecting(first, SLICE_B, expected=4)
        third = _continue_expecting(second, SLICE_C, expected=7)
        assert third.pages == SLICE_A + SLICE_B + SLICE_C
        assert third.analysed_page_numbers == tuple(range(1, 10))
        assert third.not_analysed_page_numbers == (10,)
        assert len(third.increments) == 3
        # the third increment's prior was the accumulated two-increment
        # state: earlier increments are preserved byte-for-byte
        assert third.increments[0] == second.increments[0]
        assert third.increments[1] == second.increments[1]
        assert third.increments[2].expected_first_page == 7

    def test_a_gap_continuation_records_skipped_pages_as_not_analysed(self):  # [E]
        state = _continue_expecting(_begin(SLICE_A), _slice(6, 7), expected=4)
        # NOT silently contiguous 1-7: pages 4-5 are explicitly NOT_ANALYSED
        assert state.analysed_page_numbers == (1, 2, 3, 6, 7)
        assert state.not_analysed_page_numbers == (4, 5, 8, 9, 10)
        assert state.increments[1] == ic.IncrementRecord(
            expected_first_page=4, first_page=6, last_page=7,
            slice_digest=ia.evidence_digest(_slice(6, 7)),
            analysed_pages=2, parse_failed_pages=0)

    def test_a_misdeclared_next_first_page_is_refused(self):
        with pytest.raises(ValueError, match="does not match"):
            _continue_expecting(_begin(SLICE_A), SLICE_B, expected=5)

    def test_a_backward_continuation_is_refused(self):
        with pytest.raises(ValueError, match="backward continuation"):
            _continue_expecting(_begin(SLICE_A), _slice(3, 4), expected=4)

    def test_an_overlapping_page_is_refused_by_the_7ay_layer(self):  # [A]
        gapped = _continue_expecting(_begin(SLICE_A), _slice(5, 6), expected=4)
        with pytest.raises(ValueError, match="duplicates a page of the prior evidence"):
            _continue_expecting(gapped, _slice(4, 6), expected=4)

    def test_a_fully_accounted_set_accepts_no_continuation(self):
        complete = _begin(_slice(1, 3), count=3)
        with pytest.raises(ValueError, match="already fully accounted"):
            _continue_expecting(complete, [], expected=4)

    def test_an_empty_new_capture_is_refused(self):
        with pytest.raises(ValueError, match="contains no pages"):
            _continue_expecting(_begin(SLICE_A), [], expected=4)

    def test_an_invented_page_beyond_the_page_count_is_refused(self):  # [B]
        with pytest.raises(ValueError, match="beyond the drawing set's page count"):
            _continue_expecting(_begin(SLICE_A), _slice(4, 11), expected=4)

    def test_a_duplicate_page_within_the_new_slice_is_refused(self):
        with pytest.raises(ValueError, match="more than once"):
            _continue_expecting(
                _begin(SLICE_A),
                [_synthetic_page(4), _synthetic_page(5), _synthetic_page(5)],
                expected=4)

    def test_a_contradictory_status_in_the_new_slice_is_refused(self):  # [F]
        contradictory = _synthetic_page(5, parse_failed=True,
                                        raw_connections=[_synthetic_entry("SYNTH-D5")])
        with pytest.raises(ValueError, match="contradictory page status"):
            _continue_expecting(
                _begin(SLICE_A), [_synthetic_page(4), contradictory], expected=4)

    def test_a_wrong_page_count_is_refused(self):  # [T]
        with pytest.raises(ValueError, match="beyond the drawing set's page count"):
            _begin(_slice(1, 6), count=5)


# ---------------------------------------------------------------------------
# integrity: tampered accumulated evidence is detected, never absorbed
# ---------------------------------------------------------------------------

class TestIntegrity:

    def _expect_digest_refusal(self, accumulated, transform):
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _continue_expecting(_tampered(accumulated, transform), SLICE_B, expected=4)

    def test_a_modified_prior_page_is_refused(self):  # [G]
        state = _begin(MATERIAL_PAGES)
        self._expect_digest_refusal(
            state, lambda pages: [dict(pages[0], raw_connections=[_synthetic_entry("CHANGED")]),
                                  pages[1]])

    def test_a_modified_prior_candidate_is_refused(self):  # [H]
        state = _begin(MATERIAL_PAGES)

        def alter_candidate(pages):
            connection = dict(pages[0]["raw_connections"][0])
            connection["connects_members"] = ["999"]
            return [dict(pages[0], raw_connections=[connection]), pages[1]]

        self._expect_digest_refusal(state, alter_candidate)

    def test_a_changed_source_drawing_identity_is_refused(self):  # [I]
        state = _begin(MATERIAL_PAGES)

        def alter_identity(pages):
            return [dict(pages[0], drawing_number="SELBY-C9999"), pages[1]]

        self._expect_digest_refusal(state, alter_identity)

    def test_a_shortened_prior_queue_is_refused(self):  # [K]
        self._expect_digest_refusal(_begin(SLICE_A), lambda pages: pages[:2])

    def test_reordered_prior_evidence_is_refused(self):  # [L]
        self._expect_digest_refusal(_begin(SLICE_A), lambda pages: list(reversed(pages)))

    def test_a_changed_material_value_is_refused(self):  # [M]
        state = _begin(MATERIAL_PAGES)

        def alter_material(pages):
            connection = dict(pages[0]["raw_connections"][0], material="300PLUS")
            return [dict(pages[0], raw_connections=[connection]), pages[1]]

        self._expect_digest_refusal(state, alter_material)

    def test_a_changed_engineering_value_is_refused(self):  # [N]
        state = _begin(MATERIAL_PAGES)

        def alter_bolt(pages):
            connection = dict(pages[0]["raw_connections"][0])
            connection["bolts"] = [{"quantity": 4, "size": "Ø12", "grade": "8.8"}]
            return [dict(pages[0], raw_connections=[connection]), pages[1]]

        self._expect_digest_refusal(state, alter_bolt)

    def test_an_altered_page_provenance_is_refused(self):  # [O]
        self._expect_digest_refusal(
            _begin(SLICE_A), lambda pages: [dict(pages[0], page_number=4)] + pages[1:])

    def test_a_parse_failed_page_converted_to_analysed_is_refused(self):  # [R]
        state = _begin(_slice(1, 3, parse_failed_at=(2,)))
        self._expect_digest_refusal(
            state, lambda pages: [pages[0], dict(pages[1], parse_failed=False), pages[2]])

    def test_a_hand_rebuilt_state_with_altered_pages_is_refused(self):
        state = _begin(SLICE_A)
        rebuilt = dataclasses.replace(state, pages=copy.deepcopy(SLICE_B))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _continue_expecting(rebuilt, SLICE_C, expected=4)

    def test_intake_for_accumulated_also_refuses_tampered_evidence(self):
        state = _tampered(_begin(SLICE_A), lambda pages: pages[:2])
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            ic.intake_for_accumulated(state)


# ---------------------------------------------------------------------------
# determinism + immutability
# ---------------------------------------------------------------------------

class TestDeterminismAndImmutability:

    def test_caller_mutation_after_continuation_never_reaches_the_state(self):  # [P]
        prior = copy.deepcopy(SLICE_A)
        new = copy.deepcopy(SLICE_B)
        state = _continue_expecting(_begin(prior), new, expected=4)
        snapshot = copy.deepcopy(state.pages)
        prior[0]["raw_connections"][0]["detail_reference"] = "MUTATED"
        new[0]["raw_connections"][0]["connects_members"] = ["999"]
        assert state.pages == snapshot
        assert state.digest == ia.evidence_digest(snapshot)

    def test_identical_inputs_produce_identical_states(self):  # [Q]
        first = _continue_expecting(_begin(copy.deepcopy(SLICE_A)),
                                    copy.deepcopy(SLICE_B), expected=4)
        second = _continue_expecting(_begin(copy.deepcopy(SLICE_A)),
                                     copy.deepcopy(SLICE_B), expected=4)
        assert first == second
        assert first.digest == second.digest

    def test_dict_key_insertion_order_never_changes_the_digest(self):  # [Q]
        ordered = {"page_number": 1, "drawing_number": None, "drawing_title": None,
                   "revision": None, "raw_members": [], "raw_connections": [],
                   "parse_failed": False}
        shuffled = {"parse_failed": False, "raw_connections": [], "raw_members": [],
                    "revision": None, "drawing_title": None, "drawing_number": None,
                    "page_number": 1}
        assert ia.evidence_digest([ordered]) == ia.evidence_digest([shuffled])

    def test_the_state_carries_no_timestamps_or_environment_fields(self):
        assert {f.name for f in dataclasses.fields(ic.AccumulatedAnalysis)} == {
            "pages", "drawing_set_page_count", "drawing_set_first_page", "digest",
            "increments", "analysed_page_numbers", "parse_failed_page_numbers",
            "not_analysed_page_numbers"}


# ---------------------------------------------------------------------------
# 7AX compatibility through the genuine intake and workflow
# ---------------------------------------------------------------------------

class Test7axCompatibility:

    def test_intake_for_accumulated_builds_the_genuine_intake(self):
        state = _begin(SLICE_A)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(SLICE_A))
        assert intake.pages_received == 3
        assert intake.drawing_set_page_count == SYNTH_COUNT
        assert intake.pages_not_analysed == SYNTH_COUNT - 3
        assert intake.analysed_page_numbers == (1, 2, 3)
        assert intake.scope_statement == INTAKE_SCOPE_STATEMENT

    def test_candidate_identities_are_preserved_in_submission_order(self):
        state = _continue_expecting(_begin(SLICE_A), SLICE_B, expected=4)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(state.pages))
        ids = [c.review_package_id for c in intake.collection.candidates]
        assert ids == ["RP-0001", "RP-0002", "RP-0003"]
        # the 7X truth: submission indices are zero-based; prior candidates
        # keep theirs, the new slice's candidate appends
        assert [c.submission_index for c in intake.collection.candidates] == [0, 1, 2]

    def test_a_duplicate_candidate_identity_is_refused_never_merged(self):  # [J]
        state = _begin(SLICE_A)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(SLICE_A))
        duplicated = dataclasses.replace(
            intake.collection,
            candidates=intake.collection.candidates
            + (intake.collection.candidates[0],))
        tampered = dataclasses.replace(intake, collection=duplicated)
        # the genuine workflow refuses the duplicated identity — nothing is
        # merged, deduplicated or renumbered around it
        with pytest.raises(ValueError, match="one-to-one"):
            _workflow_over(tampered)

    def test_a_fabricated_candidate_without_source_provenance_is_untraced(self):  # [S]
        state = _begin(SLICE_A)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(SLICE_A))
        combined = ia.combine_page_extractions(
            state.pages, [], first_new_page=SYNTH_FIRST, last_new_page=SYNTH_COUNT)
        original = intake.collection.candidates[0]
        fabricated = dataclasses.replace(
            original, review_package_id="RP-FABRICATED",
            package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction, detail_reference="FABRICATED-D1")))
        tampered = dataclasses.replace(
            intake,
            collection=dataclasses.replace(
                intake.collection,
                candidates=intake.collection.candidates + (fabricated,)))
        report = ia.verify_candidate_provenance(combined, tampered)
        assert report.verified is False
        assert "RP-FABRICATED" in report.untraced_candidate_ids

    def test_a_parse_failed_page_stays_parse_failed_through_7ax(self):  # [R]
        pages = _slice(1, 3, details=(1,), parse_failed_at=(2,))
        state = _begin(pages)
        assert state.parse_failed_page_numbers == (2,)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(pages))
        assert intake.parse_failed_pages == (2,)
        coverage = evaluate_production_coverage(_workflow_over(intake))
        page_rows = {p.page_number: p for p in coverage.pages}
        assert page_rows[2].status == PAGE_STATUS_PARSE_FAILED
        assert page_rows[1].status == PAGE_STATUS_ANALYSED
        assert all(conn.source_page != 2 for conn in coverage.connections)

    def test_incomplete_stays_incomplete_after_a_successful_continuation(self):
        state = _continue_expecting(_begin(SLICE_A), SLICE_B, expected=4)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(state.pages))
        coverage = evaluate_production_coverage(_workflow_over(intake))
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert coverage.summary.project_status == PROJECT_STATUS_INCOMPLETE
        assert coverage.summary.pages_not_analysed == 4
        assert any("never analysed" in reason
                   for reason in coverage.summary.incomplete_reasons)
        assert all(row.disposition == DISPOSITION_UNRESOLVED
                   for row in coverage.connections)

    def test_a_fully_accounted_set_still_never_becomes_complete(self):
        state = _begin(_slice(1, 3, details=(1, 3)), count=3)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(state.pages))
        coverage = evaluate_production_coverage(_workflow_over(intake))
        assert coverage.summary.pages_not_analysed == 0
        assert coverage.status == PROJECT_STATUS_INCOMPLETE


# ---------------------------------------------------------------------------
# the REAL world: pages 1-5 then 6-10, prior evidence preserved
# ---------------------------------------------------------------------------

class TestRealWorldAccumulation:

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_real_begin_then_continue_accumulates_pages_1_to_10(self):
        prior = _prior_pages()
        new = _load_new_pages()
        state = _begin(prior, count=SELBY_PAGE_COUNT, first=1)
        assert state.analysed_page_numbers == (1, 2, 3, 4, 5)
        assert state.not_analysed_page_numbers == tuple(range(6, SELBY_PAGE_COUNT + 1))
        state = _continue_expecting(state, new, expected=6)
        # the strict 7AY invariant: prior evidence preserved exactly, then new
        assert state.pages[:5] == prior
        assert state.pages[5:] == new
        assert state.pages == prior + new
        assert state.analysed_page_numbers == tuple(range(1, 11))
        assert state.not_analysed_page_numbers == tuple(range(11, SELBY_PAGE_COUNT + 1))
        assert state.increments[0] == ic.IncrementRecord(
            expected_first_page=1, first_page=1, last_page=5,
            slice_digest=ia.evidence_digest(prior), analysed_pages=5, parse_failed_pages=0)
        assert state.increments[1] == ic.IncrementRecord(
            expected_first_page=6, first_page=6, last_page=10,
            slice_digest=ia.evidence_digest(new), analysed_pages=5, parse_failed_pages=0)
        assert state.digest == ia.evidence_digest(state.pages)

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_the_accepted_captures_remain_byte_identical(self):
        prior_bytes_before = (REPO / "tests" / "data"
                              / "selby_square_page_extractions.json").read_bytes()
        new_bytes_before = hashlib.sha256(
            (REPO / "tests" / "data"
             / "selby_square_pages_6_10_extractions.json").read_bytes()).hexdigest()
        assert new_bytes_before == NEW_CAPTURE_SHA256
        state = _continue_expecting(_begin(_prior_pages(), count=SELBY_PAGE_COUNT, first=1),
                                    _load_new_pages(), expected=6)
        assert state.pages[:5] == _prior_pages()
        assert (REPO / "tests" / "data"
                / "selby_square_page_extractions.json").read_bytes() == prior_bytes_before
        assert hashlib.sha256(
            (REPO / "tests" / "data"
             / "selby_square_pages_6_10_extractions.json").read_bytes()
        ).hexdigest() == NEW_CAPTURE_SHA256

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_real_values_stay_verbatim_through_two_increments(self):
        state = _continue_expecting(_begin(_prior_pages(), count=SELBY_PAGE_COUNT, first=1),
                                    _load_new_pages(), expected=6)
        materials = [c.get("material") for p in state.pages
                     for c in p.get("raw_connections", []) if c.get("material")]
        assert materials and set(materials) == {SELBY_MATERIAL}
        serialized = json.dumps(state.pages)
        assert "300PLUS" not in serialized
        assert "Ø12" not in serialized

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_the_real_accumulated_state_feeds_7ax_without_bypass(self):
        state = _continue_expecting(_begin(_prior_pages(), count=SELBY_PAGE_COUNT, first=1),
                                    _load_new_pages(), expected=6)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(state.pages))
        coverage = evaluate_production_coverage(_workflow_over(intake))
        summary = coverage.summary
        # counts are COMPUTED from the recorded evidence — never hard-coded
        assert summary.set_pages == SELBY_PAGE_COUNT
        assert summary.pages_analysed == len(state.analysed_page_numbers)
        assert summary.pages_parse_failed == 0
        assert summary.pages_not_analysed == SELBY_PAGE_COUNT - 10
        assert summary.connections_discovered == len(intake.collection.candidates)
        assert summary.project_status == PROJECT_STATUS_INCOMPLETE
        assert any("never analysed" in reason for reason in summary.incomplete_reasons)
        assert all(row.disposition == DISPOSITION_UNRESOLVED
                   for row in coverage.connections)

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_a_third_increment_operates_on_the_accumulated_state(self):
        # STEP 3/4: the third increment is proven against the real
        # accumulated 1-10 evidence with a controlled synthetic 11-15
        # slice — the continuation contract is deterministic whether or
        # not the environment has the genuine 11-15 capture yet.
        state = _continue_expecting(_begin(_prior_pages(), count=SELBY_PAGE_COUNT, first=1),
                                    _load_new_pages(), expected=6)
        slice_11_15 = _slice(11, 15, details=(11,))
        third = _continue_expecting(state, slice_11_15, expected=11)
        assert third.pages[:10] == state.pages
        assert third.pages[10:] == slice_11_15
        assert len(third.increments) == 3
        assert third.increments[0] == state.increments[0]
        assert third.increments[1] == state.increments[1]
        assert third.analysed_page_numbers == tuple(range(1, 16))
        assert third.not_analysed_page_numbers == tuple(range(16, SELBY_PAGE_COUNT + 1))

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_pages_11_15_capture
    def test_a_genuine_third_increment_pages_11_to_15(self):
        state = _continue_expecting(_begin(_prior_pages(), count=SELBY_PAGE_COUNT, first=1),
                                    _load_new_pages(), expected=6)
        genuine = json.loads(CAPTURE_11_15_PATH.read_text())
        third = _continue_expecting(state, genuine, expected=11)
        assert third.analysed_page_numbers == tuple(range(1, 16))
        assert third.pages[:10] == state.pages


# ---------------------------------------------------------------------------
# module purity (the 7AY constraints, pinned for the new module too)
# ---------------------------------------------------------------------------

class TestModulePurity:

    def test_the_module_imports_without_supabase_url(self):
        env = {key: value for key, value in os.environ.items()
               if key not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        proc = subprocess.run(
            [sys.executable, "-c", "import app.cad_engine.incremental_continuation"],
            cwd=REPO, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_imports_stay_within_the_whitelist(self):
        tree = ast.parse(Path(ic.__file__).read_text())
        allowed_roots = {"copy", "dataclasses", "collections", "typing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert (alias.name.split(".")[0] in allowed_roots
                            or alias.name.startswith("app.cad_engine.")), alias.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module and (
                    node.module.split(".")[0] in allowed_roots
                    or node.module.startswith("app.cad_engine.")), node.module

    def test_no_numeric_literals_appear_in_the_module(self):
        tree = ast.parse(Path(ic.__file__).read_text())
        offenders = [
            (node.value, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float, complex))
            and not isinstance(node.value, bool)
        ]
        assert offenders == []
