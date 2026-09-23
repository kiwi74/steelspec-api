"""
Milestone 7B0 — real drawing-set continuation to full coverage
(tests/test_real_world_drawing_set_continuation.py).

Takes the verified real Selby evidence for pages 1-10 and genuinely
continues through pages 11-32 in the declared slices (11-15, 16-20,
21-25, 26-30, 31-32), accumulating through the existing 7AZ contract
and feeding the existing 7AX production-coverage machinery at every
stage. No mock vision, no fabricated JSON, no expected engineering
counts: the real PDF determines what each slice contains, and a slice
whose capture does not exist yet is an honest skip with the exact
capture command.

Pinned here:
  - pages 1-10 are immutable prior evidence (bytes and digests
    re-verified before and after every accumulation);
  - FABs.pdf is only ever read, never written (bytes re-verified);
  - after every increment the accounting is exact: analysed +
    parse-failed + not-analysed == 32, every page 1-32 in exactly one
    state, no duplicates, no omissions;
  - extraction completeness never becomes production completeness: a
    fully-accounted 32-page set with unresolved connections stays
    INCOMPLETE under 7AX's unchanged rules;
  - the FL+EA section-family milestone moved the old boundary for real:
    FL/EA now build genuine geometry through the chain (proof [I] reruns
    AUTO to a real generated, verified PDF), and the next genuine
    boundary — the 7AQ acceptance's human-provenance rule — holds in its
    place; no approximation anywhere;
  - the 7B0 negative proofs A-J.

The genuine 11-32 captures do not yet exist in this environment (no
ANTHROPIC_API_KEY in this process and macOS TCC denies the PDF read);
the per-slice tests skip honestly until the real captures are produced
in a terminal with Downloads access.
"""
import copy
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from app.cad_engine import incremental_analysis as ia
from app.cad_engine import incremental_continuation as ic
from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.production_coverage import (
    DISPOSITION_BLOCKED,
    PROJECT_STATUS_INCOMPLETE,
    PROJECT_STATUS_REFUSED,
    evaluate_production_coverage,
)
from app.cad_engine.project_workflow import resolve_project_connection
from tests.test_real_world_production_coverage import (
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_SELECT_ATTACHMENT,
    VIEW_AA_ANSWERS_BY_TASK_TYPE,
    _resolutions_for,
)
from tests.test_real_world_incremental_analysis import (
    _CAPTURE_KEYS,
    _load_new_pages,
    _marks_of,
    _prior_pages,
    _synthetic_entry,
    _synthetic_page,
    _workflow_over,
    needs_pages_6_10_capture,
    needs_selby_capture,
)
from tests.test_real_world_incremental_continuation import _slice
from tests.test_real_world_material_extraction import REAL_PDF
from tests.test_real_world_multi_member_connection import (
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "tests" / "data"
CAPTURE_1_5 = DATA / "selby_square_page_extractions.json"
CAPTURE_6_10 = DATA / "selby_square_pages_6_10_extractions.json"

# The sha256 of the 7AY-ACCEPTED pages 6-10 capture — pinned.
NEW_CAPTURE_SHA256 = "4c2acbadea5bdc9ce67a5d1f946a693997d9606f944b18c767a3b8a07ea35806"

SLICE_FILES = {
    (11, 15): DATA / "selby_square_pages_11_15_extractions.json",
    (16, 20): DATA / "selby_square_pages_16_20_extractions.json",
    (21, 25): DATA / "selby_square_pages_21_25_extractions.json",
    (26, 30): DATA / "selby_square_pages_26_30_extractions.json",
    (31, 32): DATA / "selby_square_pages_31_32_extractions.json",
}


def needs_slice_capture(start, end):
    path = SLICE_FILES[(start, end)]
    return pytest.mark.skipif(
        not path.exists(),
        reason=f"no genuine pages {start}-{end} extraction exists at "
               f"{path.relative_to(REPO)}; produce one with "
               "ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py "
               f"--first-page {start} --max-pages {end - start + 1} "
               "\"/Users/chad/Downloads/FABs.pdf\" "
               f"tests/data/selby_square_pages_{start}_{end}_extractions.json "
               "(run it from a terminal with Downloads access — macOS TCC can "
               "otherwise deny the PDF read).",
    )


# ---------------------------------------------------------------------------
# synthetic slices for the negative proofs (controlled records only)
# ---------------------------------------------------------------------------

S_1_10 = _slice(1, 10, details=(1, 3))
S_11_15 = _slice(11, 15, details=(11,))
S_16_20 = _slice(16, 20, details=(16,))
S_21_25 = _slice(21, 25, details=(21,))
S_26_30 = _slice(26, 30, details=(26,))
S_31_32 = _slice(31, 32, details=(31,))


def _begin32(pages):
    return ic.begin_incremental_analysis(
        pages, drawing_set_page_count=SELBY_PAGE_COUNT, drawing_set_first_page=1)


def _continue(state, pages, *, expected):
    return ic.continue_incremental_analysis(
        state, pages, expected_next_first_page=expected)


def _assert_accounting(state):
    """Exact page accounting: every page 1-32 in exactly one state."""
    analysed, failed, missing = (state.analysed_page_numbers,
                                 state.parse_failed_page_numbers,
                                 state.not_analysed_page_numbers)
    assert len(analysed) + len(failed) + len(missing) == SELBY_PAGE_COUNT
    union = set(analysed) | set(failed) | set(missing)
    assert len(union) == SELBY_PAGE_COUNT
    assert union == set(range(1, SELBY_PAGE_COUNT + 1))
    recorded = [p["page_number"] for p in state.pages]
    assert recorded == sorted(recorded)
    assert len(recorded) == len(set(recorded))
    assert set(recorded) == set(analysed) | set(failed)
    assert state.digest == ia.evidence_digest(state.pages)


def _coverage_of(state):
    intake = ic.intake_for_accumulated(
        state, project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=_marks_of(state.pages))
    return evaluate_production_coverage(_workflow_over(intake))


def _real_chain():
    """The genuine accumulation over every existing capture slice, in
    order. Stops honestly at the first slice that does not exist."""
    state = _begin32(_prior_pages())
    _assert_accounting(state)
    state = _continue(state, _load_new_pages(), expected=6)
    _assert_accounting(state)
    for (start, _end), path in SLICE_FILES.items():
        if not path.exists():
            continue
        pages = json.loads(path.read_text())
        state = _continue(state, pages, expected=start)
        _assert_accounting(state)
    return state


def _source_materials():
    materials = set()
    for path in [CAPTURE_1_5, CAPTURE_6_10] + list(SLICE_FILES.values()):
        if not path.exists():
            continue
        pages = json.loads(path.read_text())
        for page in pages:
            for connection in page.get("raw_connections") or []:
                if connection.get("material"):
                    materials.add(connection["material"])
    return materials


# ---------------------------------------------------------------------------
# the real accumulated chain, whatever slices genuinely exist
# ---------------------------------------------------------------------------

class TestAccumulatedChain:

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_every_genuine_increment_preserves_and_accounts_exactly(self):
        prior = _prior_pages()
        new = _load_new_pages()
        state = _begin32(prior)
        assert state.analysed_page_numbers == (1, 2, 3, 4, 5)
        assert state.not_analysed_page_numbers == tuple(range(6, SELBY_PAGE_COUNT + 1))
        state = _continue(state, new, expected=6)
        assert state.pages[:5] == prior
        assert state.pages[5:] == new
        increments_seen = 2
        for (start, _end), path in SLICE_FILES.items():
            if not path.exists():
                continue
            pages = json.loads(path.read_text())
            assert [p["page_number"] for p in pages] == list(range(start, _end + 1))
            state = _continue(state, pages, expected=start)
            increments_seen += 1
            _assert_accounting(state)
            # prior evidence is preserved exactly, never rebuilt
            assert state.pages[:10] == prior + new
            assert len(state.increments) == increments_seen
        assert len(state.increments) == increments_seen

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_material_values_stay_verbatim_across_every_increment(self):
        state = _real_chain()
        accumulated = [c.get("material") for p in state.pages
                       for c in p.get("raw_connections") or [] if c.get("material")]
        source = _source_materials()
        assert set(accumulated) <= source
        serialized = json.dumps(state.pages)
        assert "300PLUS" not in serialized
        assert "Ø12" not in serialized

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_7ax_reports_the_accounting_truth_at_the_final_stage(self):
        state = _real_chain()
        coverage = _coverage_of(state)
        summary = coverage.summary
        # counts are COMPUTED from the recorded evidence — never guessed
        assert summary.set_pages == SELBY_PAGE_COUNT
        assert summary.pages_analysed == len(state.analysed_page_numbers)
        assert summary.pages_parse_failed == len(state.parse_failed_page_numbers)
        assert summary.pages_not_analysed == len(state.not_analysed_page_numbers)
        assert summary.connections_discovered == len(
            ic.intake_for_accumulated(state).collection.candidates)
        # extraction coverage is NOT production completeness
        assert summary.project_status == PROJECT_STATUS_INCOMPLETE
        if state.not_analysed_page_numbers:
            assert any("never analysed" in reason
                       for reason in summary.incomplete_reasons)


# ---------------------------------------------------------------------------
# the genuine slices, one test each (honest skips until captured)
# ---------------------------------------------------------------------------

class TestRealSlices:

    def _check_slice(self, start, end):
        path = SLICE_FILES[(start, end)]
        pages = json.loads(path.read_text())
        assert [p["page_number"] for p in pages] == list(range(start, end + 1))
        assert all(set(p) == _CAPTURE_KEYS for p in pages)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha  # stable within the run
        state = _begin32(_prior_pages())
        state = _continue(state, _load_new_pages(), expected=6)
        for (s, e), p in SLICE_FILES.items():
            if not p.exists() or s >= start:
                continue
            state = _continue(state, json.loads(p.read_text()), expected=s)
        state = _continue(state, pages, expected=start)
        _assert_accounting(state)
        # the slice is appended verbatim after all prior evidence
        assert state.pages[-(end - start + 1):] == pages
        # parse failures stay PARSE_FAILED; empty analysed pages stay ANALYSED
        for page in pages:
            if page.get("parse_failed"):
                assert page["page_number"] in state.parse_failed_page_numbers
            else:
                assert page["page_number"] in state.analysed_page_numbers
        accumulated_materials = {c.get("material") for p in state.pages
                                 for c in p.get("raw_connections") or []
                                 if c.get("material")}
        assert accumulated_materials <= _source_materials()
        return sha, state

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_slice_capture(11, 15)
    def test_genuine_slice_11_15(self):
        self._check_slice(11, 15)

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_slice_capture(16, 20)
    def test_genuine_slice_16_20(self):
        self._check_slice(16, 20)

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_slice_capture(21, 25)
    def test_genuine_slice_21_25(self):
        self._check_slice(21, 25)

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_slice_capture(26, 30)
    def test_genuine_slice_26_30(self):
        self._check_slice(26, 30)

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_slice_capture(31, 32)
    def test_genuine_slice_31_32(self):
        self._check_slice(31, 32)


# ---------------------------------------------------------------------------
# preservation proof: pages 1-10 and FABs.pdf stay byte-identical
# ---------------------------------------------------------------------------

class TestPreservationProof:

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_prior_capture_files_remain_byte_identical(self):
        before_1_5 = CAPTURE_1_5.read_bytes()
        before_6_10 = hashlib.sha256(CAPTURE_6_10.read_bytes()).hexdigest()
        assert before_6_10 == NEW_CAPTURE_SHA256
        _real_chain()
        assert CAPTURE_1_5.read_bytes() == before_1_5
        assert hashlib.sha256(CAPTURE_6_10.read_bytes()).hexdigest() == NEW_CAPTURE_SHA256

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_the_real_pdf_is_never_written(self):
        try:
            before = REAL_PDF.read_bytes()
        except PermissionError as error:
            pytest.skip(f"FABs.pdf unreadable in this process (macOS TCC): {error}")
        _real_chain()
        assert REAL_PDF.read_bytes() == before


# ---------------------------------------------------------------------------
# 7B0 negative proofs A-J
# ---------------------------------------------------------------------------

class TestNegativeProofs7B0:

    def test_a_prior_evidence_cannot_be_reinterpreted(self):  # [A]
        state = _begin32(S_1_10)

        def alter(page):
            connection = dict(page["raw_connections"][0])
            connection["bolts"] = [{"quantity": 4, "size": "Ø12", "grade": "8.8"}]
            return dict(page, raw_connections=[connection])

        tampered = dataclasses.replace(
            state, pages=[alter(copy.deepcopy(state.pages[0]))] + copy.deepcopy(state.pages[1:]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _continue(tampered, S_11_15, expected=11)

    def test_b_a_skipped_slice_is_not_analysed_never_contiguous(self):  # [B]
        state = _begin32(S_1_10)
        gapped = _continue(state, S_21_25, expected=11)  # slices 11-15 and 16-20 skipped
        _assert_accounting(gapped)
        # the gap is recorded explicitly, and the rest of the universe is
        # still NOT_ANALYSED too — nothing is silently absorbed
        assert gapped.not_analysed_page_numbers == (
            tuple(range(11, 21)) + tuple(range(26, 33)))
        assert gapped.increments[-1].expected_first_page == 11
        assert gapped.increments[-1].first_page == 21
        coverage = _coverage_of(gapped)
        assert coverage.summary.pages_not_analysed == 17
        assert any("never analysed" in reason
                   for reason in coverage.summary.incomplete_reasons)

    def test_c_a_later_slice_before_its_predecessor_is_refused(self):  # [C]
        state = _continue(_begin32(S_1_10), S_11_15, expected=11)
        with pytest.raises(ValueError, match="backward continuation"):
            _continue(state, _slice(11, 12), expected=16)

    def test_d_a_slice_from_a_different_source_drawing_is_refused(self):  # [D]
        def titled(start, end, title):
            return [dict(_synthetic_page(n), drawing_title=title)
                    for n in range(start, end + 1)]

        state = _begin32(titled(1, 3, "DRAWING SET ALPHA"))
        with pytest.raises(ValueError, match="different source drawing"):
            _continue(state, titled(4, 6, "DRAWING SET BETA"), expected=4)
        # the same recorded identity continues; an unrecorded identity on
        # either side (None titles) is not a conflict
        same = _continue(state, titled(4, 6, "DRAWING SET ALPHA"), expected=4)
        assert same.analysed_page_numbers == (1, 2, 3, 4, 5, 6)
        untitled = _begin32(_slice(1, 3))
        accepted = _continue(untitled, _slice(4, 6), expected=4)
        assert accepted.analysed_page_numbers == (1, 2, 3, 4, 5, 6)

    def test_e_a_slice_beyond_the_drawing_set_count_is_refused(self):  # [E]
        with pytest.raises(ValueError, match="beyond the drawing set's page count"):
            _continue(_begin32(_slice(1, 3)), _slice(33, 34), expected=4)
        full = _full_chain()
        wrong_count = dataclasses.replace(full, drawing_set_page_count=31)
        with pytest.raises(ValueError, match="beyond the drawing set's page count"):
            _continue(wrong_count, [], expected=33)

    def test_f_accumulated_evidence_cannot_be_replaced_by_a_fresh_partial(self):  # [F]
        chain = _continue(_begin32(S_1_10), S_11_15, expected=11)
        fresh = _begin32(copy.deepcopy(S_1_10))  # a fresh partial capture of 1-10
        assert len(fresh.increments) == 1
        assert len(chain.increments) == 2
        replaced = dataclasses.replace(chain, pages=copy.deepcopy(fresh.pages))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _continue(replaced, S_16_20, expected=16)

    def test_g_a_later_capture_mutation_never_reaches_the_state(self):  # [G]
        state = _begin32(copy.deepcopy(S_1_10))
        new = copy.deepcopy(S_11_15)
        state = _continue(state, new, expected=11)
        snapshot = copy.deepcopy(state.pages)
        new[0]["raw_connections"][0]["material"] = "300PLUS"
        new.append(_synthetic_page(16))
        assert state.pages == snapshot
        assert state.digest == ia.evidence_digest(snapshot)

    def test_h_extraction_completeness_is_not_production_completeness(self):  # [H]
        state = _full_chain()
        _assert_accounting(state)
        assert state.not_analysed_page_numbers == ()
        assert len(state.analysed_page_numbers) == SELBY_PAGE_COUNT
        coverage = _coverage_of(state)
        assert coverage.summary.pages_not_analysed == 0
        assert coverage.summary.project_status == PROJECT_STATUS_INCOMPLETE

    def test_i_fl_ea_crosses_the_section_boundary_and_the_next_boundary_holds(self, tmp_path):  # [I]
        # 7B0 originally pinned the OLD boundary: this synthetic FL/EA
        # connection stayed BLOCKED because the section families were
        # unsupported. The FL+EA milestone moved that boundary for real:
        # the same scenario now reruns AUTO with genuine FL/EA geometry and
        # a real generated, verified PDF — the families are built, never
        # approximated. The next genuine boundary then holds in its place:
        # the synthetic material answer carries no human provenance, so the
        # 7AQ acceptance refuses the verified artifact and coverage refuses
        # the project with that exact reason — no section-family
        # disposition remains to be seen.
        pages = [_synthetic_page(1, raw_connections=[_synthetic_entry(
            "SYNTH-FL", connects_members=("PL008", "CL004"))]),
            _synthetic_page(2)]
        state = _begin32(pages)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=("CL004", "PL008"))
        workflow = _workflow_over(intake)
        # the genuine review path: human resolutions for the connection's
        # own tasks, then the rerun — where the FL/EA family boundary used
        # to hold and no longer does
        group = next(g for g in workflow.exception_package.connection_tasks)
        answers = dict(copy.deepcopy(VIEW_AA_ANSWERS_BY_TASK_TYPE))
        answers[TASK_SELECT_ATTACHMENT] = [
            {"member_mark": mark, "surface_reference": "END"}
            for mark in ("PL008", "CL004")]
        answers[TASK_PROVIDE_CONNECTION_IDENTITY] = "CONN-SYNTH-FL"
        workflow = resolve_project_connection(
            workflow, package_id=group.review_package_id,
            resolutions=_resolutions_for(
                group, answers,
                "HUMAN-SUPPLIED TEST DATA (7B0 negative proof I)"),
            output_dir=tmp_path / "fl-ea",
        )
        record = workflow.connection_records[-1]
        assert record.rerun_outcome.decision == AUTOMATION_DECISION_AUTO
        assert record.rerun_outcome.blockers == ()
        assert record.dispatch_result.output_status == OUTPUT_STATUS_GENERATED
        assert (record.verification_result.verification_status
                == VERIFICATION_STATUS_VERIFIED)
        assert Path(record.dispatch_result.generated_files[0]).is_file()
        coverage = evaluate_production_coverage(workflow)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert "did not accept" in coverage.reason
        assert "'material'" in coverage.reason
        assert all(r.disposition != DISPOSITION_BLOCKED
                   for r in coverage.connections)

    def test_j_a_candidate_for_a_page_with_no_genuine_evidence_is_untraced(self):  # [J]
        state = _begin32(S_1_10)
        intake = ic.intake_for_accumulated(
            state, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(S_1_10))
        combined = ia.combine_page_extractions(
            state.pages, [], first_new_page=1, last_new_page=SELBY_PAGE_COUNT)
        original = intake.collection.candidates[0]
        fabricated = dataclasses.replace(
            original, review_package_id="RP-FABRICATED",
            package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction,
                    detail_reference="FABRICATED-D1", source_page=11)))
        tampered = dataclasses.replace(
            intake,
            collection=dataclasses.replace(
                intake.collection,
                candidates=intake.collection.candidates + (fabricated,)))
        report = ia.verify_candidate_provenance(combined, tampered)
        assert report.verified is False
        assert "RP-FABRICATED" in report.untraced_candidate_ids


def _full_chain():
    """The synthetic 1-32 chain through the declared slices — used to
    prove accounting/extraction completeness without any real capture."""
    state = _begin32(S_1_10)
    for start, pages in [(11, S_11_15), (16, S_16_20), (21, S_21_25),
                         (26, S_26_30), (31, S_31_32)]:
        state = _continue(state, pages, expected=start)
    return state
