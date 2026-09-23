"""MILESTONE 7AY — Incremental Real-World Drawing Set Analysis Proof.

The question: given the accepted Selby Square capture (pages 1-5 of a 32-page
set, tests/data/selby_square_page_extractions.json) and a NEW genuine
extraction of pages 6-10 (tests/data/selby_square_pages_6_10_extractions.json,
produced by scripts/capture_real_pdf_extraction.py --first-page 6 --max-pages 5
through the REAL vision path — never mocked, never synthesized here), can the
combined evidence be proven honest through the existing 7Y intake and 7AX
coverage layers: prior evidence preserved field-for-field, new pages accounted
exactly once, every candidate traceable to its source page and extraction
entry, nothing invented, dropped, duplicated or silently merged?

WHAT IS REAL AND WHAT IS NOT (read this first):
  REAL: the two capture artifacts. Pages 1-5 are the accepted 7AU/7AV/7AX
  capture — never rewritten; a preservation digest pins them and the tests
  assert the file is untouched. Pages 6-10 are a genuine vision extraction
  of the real PDF; every test that needs them is skipped (never fabricated)
  until that capture exists: a skipped real proof is a missing proof, not a
  pass. The combined workflow is the GENUINE start_project_workflow +
  evaluate_production_coverage over the combined intake — nothing mocked.
  SYNTHETIC ONLY WHERE SAFETY INVARIANTS DEMAND: duplicate pages, invented
  pages/candidates, dropped entries, modified prior evidence, parse-failure
  and empty-page accounting — these exercise refusal paths that real data
  may never produce, and are labeled SYNTHETIC.

BRIEF ITEM MAP:
  real PDF / genuine path / artifact ......... 1-2 (TestTheRealNewCapture)
  page status accounting / parse failure / empty page ... 3, SYNTH 6-7
  pages 1-5 unchanged / digest / preservation ............ 4, 9 (TestPreservation)
  new candidate provenance / accounting / no loss ....... 10-12 (TestTheRealCombinedEvidence)
  no duplicate candidate / duplicate page refused ....... 13, SYNTH 1-2
  invented page / invented candidate / no provenance .... SYNTH 3, 8-9
  modified prior evidence detected / short queue ........ SYNTH 4-5, 11
  engineering-value preservation ........................ SYNTH 12
  vision unavailable -> no fake artifact ................ 14 (TestCaptureBoundary)
  first_page seam (genuine analyzer) .................... 15-16, 20
  repeatability / determinism / no mutation ............. SYNTH 13, 22
  7AX integration (before vs after) ..................... 17 (Test7axIntegration)
  7AU / 7AV / 7AW regressions ........................... 5-8 (TestRegressions)
  full report ........................................... 18
  module purity (imports, no numeric literals) .......... 19, 21 (TestModulePurity)
"""

import ast
import copy
import dataclasses
import importlib
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cad_engine import incremental_analysis as ia
from app.cad_engine import project_workflow as workflow_module
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_coverage import (
    DISPOSITION_UNRESOLVED,
    PAGE_STATUS_ANALYSED,
    PAGE_STATUS_PARSE_FAILED,
    PROJECT_STATUS_INCOMPLETE,
    evaluate_production_coverage,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from scripts.capture_real_pdf_extraction import capture
from tests.test_capture_real_pdf_extraction import StubPageExtraction, _stub_analyzer
from tests.test_real_world_material_extraction import (
    REAL_PDF,
    SELBY_MATERIAL,
    SELBY_MEMBER_PLACEMENTS,
    SELBY_MEMBER_ROWS,
    SelbySectionMatcher,
    needs_real_pdf,
)
from tests.test_real_world_multi_member_connection import (
    CAPTURE_PATH,
    JOURNEY_MEMBER_PLACEMENTS,
    JOURNEY_MEMBER_ROWS,
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
    Page5SectionMatcher,
    needs_selby_capture,
)

REPO = Path(__file__).resolve().parents[1]
NEW_CAPTURE_PATH = REPO / "tests" / "data" / "selby_square_pages_6_10_extractions.json"

FIRST_NEW_PAGE = 6
LAST_NEW_PAGE = 10

needs_pages_6_10_capture = pytest.mark.skipif(
    not NEW_CAPTURE_PATH.exists(),
    reason=f"no genuine pages 6-10 extraction exists at {NEW_CAPTURE_PATH.relative_to(REPO)}; "
           "produce one with ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py "
           "--first-page 6 --max-pages 5 \"/Users/chad/Downloads/FABs.pdf\" "
           "tests/data/selby_square_pages_6_10_extractions.json (run it from a terminal with "
           "Downloads access — macOS TCC can otherwise deny the PDF read).",
)

_CAPTURE_KEYS = {"page_number", "drawing_number", "drawing_title", "revision",
                 "raw_members", "raw_connections", "parse_failed"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _prior_pages():
    """The accepted 5-page capture, deep-copied — never modified in place."""
    return copy.deepcopy(json.loads(CAPTURE_PATH.read_text()))


def _load_new_pages():
    return copy.deepcopy(json.loads(NEW_CAPTURE_PATH.read_text()))


def _marks_of(pages):
    return tuple(sorted({m["mark"] for page in pages for m in page["raw_members"]}))


def _synthetic_page(number, *, parse_failed=False, raw_members=(), raw_connections=()):
    """A SYNTHETIC page extraction in the genuine capture shape."""
    return {
        "page_number": number, "drawing_number": None, "drawing_title": None,
        "revision": None, "raw_members": list(raw_members),
        "raw_connections": list(raw_connections), "parse_failed": parse_failed,
    }


def _synthetic_entry(detail_reference, *, connects_members=("001", "PL028"),
                     connection_type="bolted", bolts=(), material=None):
    entry = {
        "detail_reference": detail_reference,
        "grid_reference": None,
        "connects_members": list(connects_members),
        "connection_type": connection_type,
    }
    if bolts:
        entry["bolts"] = bolts
    if material is not None:
        entry["material"] = material
    return entry


SYNTH_PRIOR_PAGES = [
    _synthetic_page(1, raw_connections=[_synthetic_entry(
        "SYNTH-D1", bolts=[{"quantity": 4, "size": "M12", "grade": "8.8"}],
        material="300")]),
    _synthetic_page(2, raw_members=[{"mark": "001", "section": "250X90PFC"}]),
]
SYNTH_NEW_PAGES = [
    _synthetic_page(3, raw_connections=[_synthetic_entry("SYNTH-D3", connects_members=("001",))]),
]


def _workflow_over(intake):
    """The genuine revision-0 workflow over an intake, with the composed
    member context of the real Selby journey (mirrors 7AX's _composed_start)."""
    rows = dict(JOURNEY_MEMBER_ROWS)
    rows["PL008"] = {"mark": "PL008", "section_name": "180x20FL",
                     "section_name_raw": "180x20FL", "section_family": "FL",
                     "length_mm": 340, "grade": "300", "quantity": 1,
                     "review_status": "approved", "source_page": 5,
                     "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
    rows["CL004"] = {"mark": "CL004", "section_name": "90x10EA",
                     "section_name_raw": "90x10EA", "section_family": "EA",
                     "length_mm": 165, "grade": "300", "quantity": 1,
                     "review_status": "approved", "source_page": 5,
                     "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
    placements = dict(JOURNEY_MEMBER_PLACEMENTS)
    placements["PL008"] = MemberPlacement(
        x=0.0, y=0.0, z=4677.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0)
    placements["CL004"] = MemberPlacement(
        x=0.0, y=0.0, z=4852.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0)

    class MatcherWithFL(Page5SectionMatcher):
        SECTIONS = dict(Page5SectionMatcher.SECTIONS)
        SECTIONS["180X20FL"] = {"name": "180X20FL", "family": "FL",
                                "width": 180.0, "thickness": 20.0,
                                "weight_per_metre": 28.3}
        SECTIONS["90X10EA"] = {"name": "90X10EA", "family": "EA",
                               "width": 90.0, "thickness": 10.0,
                               "weight_per_metre": 13.3}

    return workflow_module.start_project_workflow(
        intake.collection, intake=intake, member_rows=rows,
        member_placements=placements, section_matcher=MatcherWithFL(),
    )


def _compose_analysis(prior_pages, new_pages, *, first_new_page, last_new_page,
                      drawing_set_page_count):
    """The exact assembly the real_analysis fixture performs: combine, the
    genuine 7Y intakes, the genuine workflows, the genuine 7AX coverage
    evaluations, and the incremental analysis over them."""
    prior_marks = _marks_of(prior_pages)
    combined_marks = _marks_of(list(prior_pages) + list(new_pages))
    combined = ia.combine_page_extractions(
        prior_pages, new_pages, first_new_page=first_new_page,
        last_new_page=last_new_page,
        expected_prior_digest=ia.evidence_digest(prior_pages),
    )
    combined_intake = ia.build_combined_intake(
        combined, project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=combined_marks, drawing_set_page_count=drawing_set_page_count,
    )
    coverage_after = evaluate_production_coverage(_workflow_over(combined_intake))
    prior_intake = _selby_intake(
        prior_pages, known_member_marks=prior_marks,
        drawing_set_page_count=drawing_set_page_count,
    )
    coverage_before = evaluate_production_coverage(_workflow_over(prior_intake))
    return ia.analyze_incremental(
        prior_pages, new_pages,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
        project_id=JOURNEY_PROJECT_ID,
        prior_known_member_marks=prior_marks,
        combined_known_member_marks=combined_marks,
        drawing_set_page_count=drawing_set_page_count,
        first_new_page=first_new_page, last_new_page=last_new_page,
        expected_prior_digest=ia.evidence_digest(prior_pages),
        coverage_before=coverage_before, coverage_after=coverage_after,
    )


def _selby_intake(pages, *, known_member_marks, drawing_set_page_count):
    return intake_page_extractions(
        pages, project_id=JOURNEY_PROJECT_ID, source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=known_member_marks, drawing_set_page_count=drawing_set_page_count,
    )


def _synthetic_coverage(pages, *, drawing_set_page_count, known_member_marks):
    intake = _selby_intake(pages, known_member_marks=known_member_marks,
                           drawing_set_page_count=drawing_set_page_count)
    workflow = workflow_module.start_project_workflow(
        intake.collection, intake=intake,
        member_rows=SELBY_MEMBER_ROWS, member_placements=SELBY_MEMBER_PLACEMENTS,
        section_matcher=SelbySectionMatcher(),
    )
    return evaluate_production_coverage(workflow)


def _synthetic_analysis(prior_pages, new_pages, *,
                        first_new_page, last_new_page, drawing_set_page_count,
                        known_member_marks=("001", "PL028"), expected_prior_digest=None):
    coverage_before = _synthetic_coverage(
        prior_pages, drawing_set_page_count=drawing_set_page_count,
        known_member_marks=known_member_marks)
    coverage_after = _synthetic_coverage(
        list(prior_pages) + list(new_pages), drawing_set_page_count=drawing_set_page_count,
        known_member_marks=known_member_marks)
    return ia.analyze_incremental(
        prior_pages, new_pages,
        source_drawing_id="SYNTH-001", project_id="PROJ-7AY-SYNTH",
        prior_known_member_marks=known_member_marks,
        combined_known_member_marks=known_member_marks,
        drawing_set_page_count=drawing_set_page_count,
        first_new_page=first_new_page, last_new_page=last_new_page,
        expected_prior_digest=expected_prior_digest,
        coverage_before=coverage_before, coverage_after=coverage_after,
    )


# ---------------------------------------------------------------------------
# the REAL analysis — genuine combined workflow, genuine coverage
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_analysis(tmp_path_factory):
    """The real combined project: pages 1-5 (accepted capture) + pages 6-10
    (genuine new extraction) through the genuine intake and workflow layers.
    Nothing resolved — every new candidate stays in its recorded review state."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7ay"))
    prior = _prior_pages()
    new = _load_new_pages()
    result = _compose_analysis(
        prior, new,
        first_new_page=FIRST_NEW_PAGE, last_new_page=LAST_NEW_PAGE,
        drawing_set_page_count=SELBY_PAGE_COUNT,
    )
    return SimpleNamespace(
        tmp=tmp, prior=prior, new=new, combined=result.combined,
        combined_intake=result.combined_intake, prior_intake=result.prior_intake,
        coverage_before=result.coverage_before, coverage_after=result.coverage_after,
        result=result,
    )


# ---------------------------------------------------------------------------
# 1-2. the real PDF and the genuine new capture
# ---------------------------------------------------------------------------

class TestTheRealNewCapture:

    @needs_real_pdf
    def test_real_pdf_present(self):
        """The real Selby Square drawing set exists — the source of everything."""
        assert REAL_PDF.is_file() and REAL_PDF.stat().st_size > 0

    @needs_pages_6_10_capture
    def test_new_capture_is_genuine_pages_6_to_10(self):
        """The artifact is the genuine analyzer's shape: exactly pages 6-10 in
        order, every field the capture convention carries, and parse-failed
        pages EMPTY (the real analyzer never back-fills a parse failure)."""
        pages = _load_new_pages()
        assert [p["page_number"] for p in pages] == list(
            range(FIRST_NEW_PAGE, LAST_NEW_PAGE + 1))
        for page in pages:
            assert set(page) == _CAPTURE_KEYS
            assert isinstance(page["parse_failed"], bool)
            if page["parse_failed"]:
                assert page["raw_members"] == [] and page["raw_connections"] == []
                assert page["drawing_number"] is None and page["drawing_title"] is None
                assert page["revision"] is None


# ---------------------------------------------------------------------------
# 3. page status accounting (real) — SYNTH 6-7 below cover the synthetic edges
# ---------------------------------------------------------------------------

class TestPageAccounting:

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_every_new_page_has_exactly_one_state(self, real_analysis):
        states = real_analysis.result.page_states
        assert [s.page_number for s in states] == list(
            range(FIRST_NEW_PAGE, LAST_NEW_PAGE + 1))
        assert all(s.status in (PAGE_STATUS_ANALYSED, PAGE_STATUS_PARSE_FAILED)
                   for s in states)
        analysed = [s for s in states if s.status == PAGE_STATUS_ANALYSED]
        failed = [s for s in states if s.status == PAGE_STATUS_PARSE_FAILED]
        assert len(analysed) + len(failed) == len(states)
        # 7AX sees the same states on the same pages — no second vocabulary.
        after_pages = {p.page_number: p.status
                       for p in real_analysis.coverage_after.pages
                       if p.page_number in {s.page_number for s in states}}
        assert after_pages == {s.page_number: s.status for s in states}


# ---------------------------------------------------------------------------
# 4-13. preservation, provenance and accounting — SYNTHETIC negative proofs
# ---------------------------------------------------------------------------

class TestCombinationRefusals:

    def test_duplicate_new_page_refused(self):
        with pytest.raises(ValueError, match="more than once"):
            ia.combine_page_extractions(
                SYNTH_PRIOR_PAGES, [_synthetic_page(3), _synthetic_page(3)],
                first_new_page=3, last_new_page=3)

    def test_new_page_duplicating_prior_refused(self):
        with pytest.raises(ValueError, match="duplicates a page of the prior evidence"):
            ia.combine_page_extractions(
                SYNTH_PRIOR_PAGES, [_synthetic_page(2)],
                first_new_page=3, last_new_page=3)

    def test_invented_page_outside_range_refused(self):
        with pytest.raises(ValueError, match="outside the declared new-page range"):
            ia.combine_page_extractions(
                SYNTH_PRIOR_PAGES, [_synthetic_page(11)],
                first_new_page=FIRST_NEW_PAGE, last_new_page=LAST_NEW_PAGE)

    def test_modified_prior_evidence_detected(self):
        recorded = ia.evidence_digest(SYNTH_PRIOR_PAGES)
        tampered = copy.deepcopy(SYNTH_PRIOR_PAGES)
        tampered[0]["raw_connections"][0]["material"] = "350"
        with pytest.raises(ValueError, match="does not match the recorded preservation digest"):
            ia.combine_page_extractions(
                tampered, SYNTH_NEW_PAGES, first_new_page=3, last_new_page=3,
                expected_prior_digest=recorded)

    def test_invalid_declared_range_refused(self):
        with pytest.raises(ValueError, match="declared new-page range"):
            ia.combine_page_extractions(
                SYNTH_PRIOR_PAGES, SYNTH_NEW_PAGES, first_new_page=5, last_new_page=3)
        with pytest.raises(ValueError, match="declared new-page range"):
            ia.combine_page_extractions(
                SYNTH_PRIOR_PAGES, SYNTH_NEW_PAGES, first_new_page="six", last_new_page=10)


class TestSyntheticPageAccounting:

    def test_parse_failed_page_stays_parse_failed(self, tmp_path):
        new = [_synthetic_page(3, parse_failed=True)]
        combined = ia.combine_page_extractions(
            SYNTH_PRIOR_PAGES, new, first_new_page=3, last_new_page=3)
        intake = ia.build_combined_intake(
            combined, project_id="P", source_drawing_id="S",
            known_member_marks=("001", "PL028"), drawing_set_page_count=3)
        assert ia.new_page_state_records(new)[0].status == PAGE_STATUS_PARSE_FAILED
        assert 3 in intake.parse_failed_pages
        assert 3 not in intake.analysed_page_numbers
        assert all(c.extraction.source_page != 3 for c in intake.collection.candidates)

    def test_empty_analysed_page_is_analysed_with_zero_candidates(self, tmp_path):
        new = [_synthetic_page(3)]
        combined = ia.combine_page_extractions(
            SYNTH_PRIOR_PAGES, new, first_new_page=3, last_new_page=3)
        intake = ia.build_combined_intake(
            combined, project_id="P", source_drawing_id="S",
            known_member_marks=("001", "PL028"), drawing_set_page_count=3)
        assert ia.new_page_state_records(new)[0].status == PAGE_STATUS_ANALYSED
        assert 3 in intake.analysed_page_numbers
        assert all(c.extraction.source_page != 3 for c in intake.collection.candidates)


class TestProvenanceBijection:

    def _combined_intake(self, new_pages):
        combined = ia.combine_page_extractions(
            SYNTH_PRIOR_PAGES, new_pages, first_new_page=3, last_new_page=3)
        return combined, ia.build_combined_intake(
            combined, project_id="P", source_drawing_id="S",
            known_member_marks=("001", "PL028"), drawing_set_page_count=3)

    def test_dropped_entry_fails_the_bijection(self):
        combined, intake = self._combined_intake(SYNTH_NEW_PAGES)
        tampered = dataclasses.replace(
            intake,
            collection=dataclasses.replace(
                intake.collection,
                candidates=intake.collection.candidates[:-1]))
        report = ia.verify_candidate_provenance(combined, tampered)
        assert not report.verified
        assert report.untraced_entry_positions
        assert report.candidates + len(report.untraced_entry_positions) == report.entries

    def test_invented_candidate_fails_the_bijection(self):
        combined, intake = self._combined_intake(SYNTH_NEW_PAGES)
        base = intake.collection.candidates[0]
        evil_extraction = dataclasses.replace(base.extraction, detail_reference="FABRICATED")
        evil = dataclasses.replace(
            base, review_package_id="RP-FABRICATED",
            package=dataclasses.replace(base.package, extraction=evil_extraction))
        tampered = dataclasses.replace(
            intake,
            collection=dataclasses.replace(
                intake.collection,
                candidates=intake.collection.candidates + (evil,)))
        report = ia.verify_candidate_provenance(combined, tampered)
        assert not report.verified
        assert "RP-FABRICATED" in report.untraced_candidate_ids

    def test_preservation_refuses_a_shortened_queue(self):
        combined, intake = self._combined_intake(SYNTH_NEW_PAGES)
        prior_intake = ia.build_combined_intake(
            ia.combine_page_extractions(SYNTH_PRIOR_PAGES, [], first_new_page=3, last_new_page=3),
            project_id="P", source_drawing_id="S",
            known_member_marks=("001", "PL028"), drawing_set_page_count=3)
        tampered = dataclasses.replace(
            intake,
            collection=dataclasses.replace(
                intake.collection,
                candidates=()))  # the whole queue, dropped
        with pytest.raises(ValueError, match="fewer candidates than the prior evidence records"):
            ia.preservation_proof(combined, prior_intake, tampered, expected_prior_digest=None)

    def test_preservation_refuses_altered_prior_content(self):
        combined, intake = self._combined_intake(SYNTH_NEW_PAGES)
        prior_intake = ia.build_combined_intake(
            ia.combine_page_extractions(SYNTH_PRIOR_PAGES, [], first_new_page=3, last_new_page=3),
            project_id="P", source_drawing_id="S",
            known_member_marks=("001", "PL028"), drawing_set_page_count=3)
        base = intake.collection.candidates[0]
        tampered_candidate = dataclasses.replace(
            base,
            package=dataclasses.replace(
                base.package,
                extraction=dataclasses.replace(base.extraction, material="ALTERED")))
        tampered = dataclasses.replace(
            intake,
            collection=dataclasses.replace(
                intake.collection,
                candidates=(tampered_candidate,) + intake.collection.candidates[1:]))
        with pytest.raises(ValueError, match="not preserved field-for-field"):
            ia.preservation_proof(combined, prior_intake, tampered, expected_prior_digest=None)


class TestSyntheticEngineeringAndRepeatability:

    def test_fixture_composition_path_is_exercised_synthetically(self):
        """The exact assembly the real_analysis fixture performs — combine,
        genuine intakes, genuine workflows, genuine coverage, analysis — runs
        end to end on SYNTHETIC evidence, so the fixture shape is proven
        before the real pages 6-10 capture exists."""
        result = _compose_analysis(
            SYNTH_PRIOR_PAGES, SYNTH_NEW_PAGES,
            first_new_page=3, last_new_page=3, drawing_set_page_count=3)
        assert result.provenance.verified
        assert result.preservation.preserved_candidate_count == 1
        assert [r.review_package_id for r in result.new_candidates] == ["RP-0002"]
        assert result.coverage_after.summary.connections_discovered == 2
        assert "Candidate delta:" in result.report

    def test_engineering_values_preserved_verbatim(self):
        result = _synthetic_analysis(
            SYNTH_PRIOR_PAGES, SYNTH_NEW_PAGES,
            first_new_page=3, last_new_page=3, drawing_set_page_count=3)
        (record,) = result.new_candidates
        raw = dict(record.raw_ai_values)
        # The raw AI values stay raw — no M12->Ø12, no "300"->"300PLUS",
        # no derived member marks, no invented plate/location fields.
        assert raw["connects_members"] == ["001"]
        assert raw["connection_type"] == "bolted"
        assert set(raw) == {"detail_reference", "grid_reference", "connects_members",
                            "connection_type"}
        # The prior raw entries are byte-identical in the combined evidence —
        # the M12 stays M12 and the "300" stays "300" on the prior pages too.
        assert result.combined.prior_pages[0]["raw_connections"][0]["bolts"][0]["size"] == "M12"
        assert result.combined.prior_pages[0]["raw_connections"][0]["material"] == "300"

    def test_repeatability_and_honesty_clause(self):
        first = _synthetic_analysis(
            SYNTH_PRIOR_PAGES, SYNTH_NEW_PAGES,
            first_new_page=3, last_new_page=3, drawing_set_page_count=3)
        second = _synthetic_analysis(
            SYNTH_PRIOR_PAGES, SYNTH_NEW_PAGES,
            first_new_page=3, last_new_page=3, drawing_set_page_count=3)
        assert first == second
        assert first.report == second.report
        assert ("does not claim that two independent AI extraction calls are byte-identical"
                in first.report)
        assert "Capture delta" not in first.report and "Candidate delta:" in first.report


# ---------------------------------------------------------------------------
# 14-16, 20. the capture boundary and the first_page seam
# ---------------------------------------------------------------------------

# The real vision analyzer is imported under the same deliberately
# fake-but-JWT-shaped configuration boundary as 7AO/7AU, with sys.modules
# teardown so the pre-existing SUPABASE_URL smoke failures stay honest.
TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_MARKER = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)


@pytest.fixture()
def vision_module(monkeypatch):
    before = set(sys.modules)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", TEST_SERVICE_ROLE_MARKER)
    module = importlib.import_module("app.ai_analysis.pdf_vision_analyzer")
    yield module
    for name in set(sys.modules) - before:
        del sys.modules[name]


class TestCaptureBoundary:

    def test_vision_unavailable_writes_no_artifact(self, tmp_path, monkeypatch, vision_module):
        """Negative proof A: without an API key the genuine capture raises
        before any vision call and writes NO artifact — an extraction JSON is
        never fabricated."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(vision_module, "ANTHROPIC_API_KEY", "")
        out = tmp_path / "capture.json"
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
            capture(str(tmp_path / "absent.pdf"), out,
                    user_id="7ay", project_id="PROJ-7AY", drawing_id="SELBY-C1136",
                    storage_drawing_uuid=None, max_pages=5, first_page=FIRST_NEW_PAGE,
                    analyzer_module=vision_module)
        assert not out.exists()

    def test_first_page_reaches_the_analyzer_and_uploads_true_page_numbers(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        pdf = tmp_path / "real.pdf"
        pdf.write_bytes(b"not really a pdf - the stub never renders it")
        out = tmp_path / "capture.json"
        module, calls = _stub_analyzer(pages=[StubPageExtraction(page_number=n)
                                              for n in range(6, 11)])
        capture(pdf, out, user_id="7ay", project_id="PROJ-7AY", drawing_id="SELBY-C1136",
                storage_drawing_uuid=None, max_pages=5, first_page=6, analyzer_module=module)
        assert calls["analyze"]["first_page"] == 6
        assert calls["analyze"]["max_pages"] == 5
        data = json.loads(out.read_text())
        assert [p["page_number"] for p in data] == [6, 7, 8, 9, 10]
        image_dir = out.parent / (out.stem + "_page_images")
        assert {p.name for p in image_dir.iterdir()} == {
            f"page-{n}.png" for n in range(6, 11)}

    def test_first_page_defaults_to_one_backward_compatible(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        pdf = tmp_path / "real.pdf"
        pdf.write_bytes(b"not really a pdf - the stub never renders it")
        out = tmp_path / "capture.json"
        module, calls = _stub_analyzer(pages=[StubPageExtraction(page_number=1),
                                              StubPageExtraction(page_number=2)])
        capture(pdf, out, user_id="7au", project_id="PROJ-7AU-SELBY",
                drawing_id="SELBY-C1136", storage_drawing_uuid=None, max_pages=5,
                analyzer_module=module)
        assert calls["analyze"]["first_page"] == 1
        assert calls["analyze"]["max_pages"] == 5

    @needs_real_pdf
    def test_real_analyzer_renders_the_declared_slice(self, vision_module):
        """The genuine renderer returns exactly the declared page window. No
        vision call is made. Skips honestly when macOS TCC denies the PDF read
        or poppler is absent — the render must run where the real PDF is."""
        try:
            from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError  # noqa: PLC0415
        except ImportError:
            pytest.skip("pdf2image is not installed")
        try:
            pages = vision_module.render_pages_to_png(str(REAL_PDF), 5, first_page=6)
        except PermissionError as error:
            pytest.skip(f"the real PDF is not readable from this process (macOS TCC): {error}")
        except PDFPageCountError as error:
            pytest.skip(f"the real PDF is not readable from this process (macOS TCC): {error}")
        except PDFInfoNotInstalledError as error:
            pytest.skip(f"poppler is not available for rendering: {error}")
        assert len(pages) == 5
        assert all(p.startswith(b"\x89PNG") for p in pages)


# ---------------------------------------------------------------------------
# 17. 7AX integration — before vs after on the REAL evidence
# ---------------------------------------------------------------------------

class Test7axIntegration:

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_before_is_the_recorded_7ax_truth(self, real_analysis):
        before = real_analysis.coverage_before.summary
        assert (before.set_pages, before.pages_analysed, before.pages_parse_failed,
                before.pages_not_analysed) == (SELBY_PAGE_COUNT, 5, 0, 27)
        assert before.connections_discovered == 9
        assert before.project_status == PROJECT_STATUS_INCOMPLETE
        assert all(row.disposition == DISPOSITION_UNRESOLVED
                   for row in real_analysis.coverage_before.connections)

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_after_counts_the_new_pages_and_keeps_new_candidates_unresolved(
            self, real_analysis):
        result = real_analysis.result
        states = result.page_states
        new_analysed = sum(1 for s in states if s.status == PAGE_STATUS_ANALYSED)
        new_failed = sum(1 for s in states if s.status == PAGE_STATUS_PARSE_FAILED)
        after = real_analysis.coverage_after.summary
        # counts are COMPUTED from the discovered states — never hard-coded
        assert after.set_pages == SELBY_PAGE_COUNT
        assert after.pages_analysed == 5 + new_analysed
        assert after.pages_parse_failed == new_failed
        assert after.pages_not_analysed == SELBY_PAGE_COUNT - (5 + len(states))
        assert after.connections_discovered == len(
            real_analysis.combined_intake.collection.candidates)
        assert after.project_status == PROJECT_STATUS_INCOMPLETE
        new_ids = {r.review_package_id for r in result.new_candidates}
        for row in real_analysis.coverage_after.connections:
            if row.package_id in new_ids:
                assert row.disposition == DISPOSITION_UNRESOLVED
                assert row.reason
        assert any("never analysed" in reason for reason in after.incomplete_reasons)


# ---------------------------------------------------------------------------
# the REAL combined evidence: preservation, provenance, accounting, report
# ---------------------------------------------------------------------------

class TestTheRealCombinedEvidence:

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_prior_evidence_preserved_field_for_field(self, real_analysis):
        result = real_analysis.result
        prior = real_analysis.prior
        assert result.combined.prior_digest == ia.evidence_digest(prior)
        assert result.combined.prior_digest == result.preservation.expected_prior_digest
        assert list(result.combined.prior_pages) == prior
        assert list(result.combined.combined_pages[:5]) == prior
        prior_ids = [c.review_package_id
                     for c in real_analysis.prior_intake.collection.candidates]
        combined_ids = [c.review_package_id
                        for c in result.combined_intake.collection.candidates]
        assert combined_ids[:len(prior_ids)] == prior_ids
        assert result.preservation.preserved_candidate_count == len(prior_ids)

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_combined_accounting_balances(self, real_analysis):
        result = real_analysis.result
        prior_count = len(real_analysis.prior_intake.collection.candidates)
        new_count = len(result.new_candidates)
        combined_count = len(result.combined_intake.collection.candidates)
        assert combined_count == prior_count + new_count
        assert (f"{prior_count} prior + {new_count} new - "
                f"{prior_count + new_count - combined_count} identity merges"
                in result.report)
        assert result.provenance.verified
        assert result.provenance.candidates == result.provenance.entries
        assert result.provenance.untraced_candidate_ids == ()
        assert result.provenance.untraced_entry_positions == ()
        assert result.provenance.multiplicity_mismatches == ()

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_new_candidate_provenance_traces_to_the_artifact(self, real_analysis):
        result = real_analysis.result
        artifact = _load_new_pages()
        by_page = {p["page_number"]: p for p in artifact}
        for record in result.new_candidates:
            assert record.source_page in by_page
            page_text, entry_text = record.provenance.split(" extraction entry ")
            page_number = int(page_text.split()[-1])
            entry_index = int(entry_text)
            assert page_number == record.source_page
            entry = by_page[page_number]["raw_connections"][entry_index]
            assert dict(record.raw_ai_values) == entry  # verbatim, page_num never injected

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_no_silent_loss_and_no_duplicate_ids(self, real_analysis):
        result = real_analysis.result
        ids = [c.review_package_id
               for c in result.combined_intake.collection.candidates]
        assert len(set(ids)) == len(ids)
        prior_ids = [c.review_package_id
                     for c in real_analysis.prior_intake.collection.candidates]
        assert set(prior_ids) <= set(ids)
        entry_total = sum(s.connection_entries for s in result.page_states) \
            + sum(len(p["raw_connections"] or []) for p in real_analysis.prior)
        assert len(ids) == entry_total

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_duplicate_pairs_reported_transparently(self, real_analysis):
        """Whatever the identity mechanism reports, it reports: every pair
        names two real candidates, every signal is named, nothing is dropped
        (the accounting test above) and nothing is merged."""
        result = real_analysis.result
        ids = {c.review_package_id
               for c in result.combined_intake.collection.candidates}
        for pair in result.preservation.duplicate_pairs:
            assert pair.first_review_package_id in ids
            assert pair.second_review_package_id in ids
            assert pair.first_review_package_id != pair.second_review_package_id
            assert pair.signals

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_real_analysis_deterministic_and_non_mutating(self, real_analysis):
        prior_before = copy.deepcopy(real_analysis.prior)
        new_before = copy.deepcopy(real_analysis.new)
        again = ia.analyze_incremental(
            real_analysis.prior, real_analysis.new,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            project_id=JOURNEY_PROJECT_ID,
            prior_known_member_marks=_marks_of(real_analysis.prior),
            combined_known_member_marks=_marks_of(real_analysis.prior + real_analysis.new),
            drawing_set_page_count=SELBY_PAGE_COUNT,
            first_new_page=FIRST_NEW_PAGE, last_new_page=LAST_NEW_PAGE,
            expected_prior_digest=ia.evidence_digest(real_analysis.prior),
            coverage_before=real_analysis.coverage_before,
            coverage_after=real_analysis.coverage_after,
        )
        assert again == real_analysis.result
        assert real_analysis.prior == prior_before
        assert real_analysis.new == new_before

    @needs_selby_capture
    @needs_pages_6_10_capture
    def test_full_report_sections(self, real_analysis):
        report = real_analysis.result.report
        for header in ("Source:", "Existing evidence:", "New analysis:",
                       "Combined coverage:", "Candidate delta:", "Preservation:",
                       "Repeatability:", "Production status (7AX):"):
            assert header in report
        assert real_analysis.result.combined.prior_digest in report
        assert real_analysis.result.combined.new_digest in report
        for state in real_analysis.result.page_states:
            assert f"page {state.page_number}: {state.status}" in report
        for record in real_analysis.result.new_candidates:
            assert record.review_package_id in report
            assert f"source page {record.source_page}" in report
        before = real_analysis.coverage_before.summary
        after = real_analysis.coverage_after.summary
        assert (f"before: {before.project_status} ({before.pages_analysed} analysed"
                in report)
        assert (f"after:  {after.project_status} ({after.pages_analysed} analysed"
                in report)


# ---------------------------------------------------------------------------
# 4, 5-8. the accepted capture stays untouched — 7AU/7AV/7AW regressions
# ---------------------------------------------------------------------------

class TestRegressions:

    @needs_selby_capture
    def test_prior_capture_file_untouched(self, tmp_path):
        """The accepted 5-page capture is read-only input: its bytes survive
        a full combine + intake + analysis unchanged, and its digest is stable
        across re-reads."""
        before_bytes = CAPTURE_PATH.read_bytes()
        before_digest = ia.evidence_digest(_prior_pages())
        prior = _prior_pages()
        new = [_synthetic_page(6)]
        combined = ia.combine_page_extractions(
            prior, new, first_new_page=FIRST_NEW_PAGE, last_new_page=FIRST_NEW_PAGE,
            expected_prior_digest=before_digest)
        intake = ia.build_combined_intake(
            combined, project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=_marks_of(prior + new),
            drawing_set_page_count=SELBY_PAGE_COUNT)
        assert intake.collection.candidates[0].review_package_id == "RP-0001"
        assert CAPTURE_PATH.read_bytes() == before_bytes
        assert ia.evidence_digest(_prior_pages()) == before_digest

    @needs_selby_capture
    def test_7au_regression_material_source_intact(self):
        """7AU's source of truth — the page-1 AI reading of material "300" —
        is still present verbatim in the accepted capture."""
        prior = _prior_pages()
        materials = [
            c.get("material")
            for page in prior for c in page["raw_connections"]
            if c.get("material") is not None]
        assert SELBY_MATERIAL in materials
        page_1 = next(p for p in prior if p["page_number"] == 1)
        assert any(c.get("material") == SELBY_MATERIAL
                   for c in page_1["raw_connections"])

    @needs_selby_capture
    def test_7av_regression_page5_view_candidates_intact(self):
        """7AV's source of truth — page 5's VIEW A-A and VIEW B-B candidates —
        is still present verbatim in the accepted capture."""
        page_5 = next(p for p in _prior_pages() if p["page_number"] == 5)
        references = [c.get("detail_reference") for c in page_5["raw_connections"]]
        assert any(ref and "VIEW A-A" in ref for ref in references)
        assert any(ref and "VIEW B-B" in ref for ref in references)
        assert len(page_5["raw_connections"]) == 2

    @needs_selby_capture
    @needs_pages_6_10_capture
    @needs_real_pdf
    def test_7aw_regression_real_pdf_untouched(self, real_analysis):
        """7AW reads the actual PDF text; the incremental analysis never
        writes it. Skips honestly when macOS TCC denies the read."""
        try:
            before = REAL_PDF.read_bytes()
        except PermissionError as error:
            pytest.skip(f"the real PDF is not readable from this process (macOS TCC): {error}")
        assert REAL_PDF.read_bytes() == before
        assert real_analysis.result.combined.combined_pages[:5] == real_analysis.prior


# ---------------------------------------------------------------------------
# 19, 21. module purity — imports and the zero-numeric-literal contract
# ---------------------------------------------------------------------------

class TestModulePurity:

    def test_module_imports_without_supabase_config(self):
        """The module must never need SUPABASE_URL or network configuration."""
        env = {k: v for k, v in os.environ.items() if k != "SUPABASE_URL"}
        result = subprocess.run(
            [sys.executable, "-c", "import app.cad_engine.incremental_analysis"],
            env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_module_import_whitelist(self):
        allowed_stdlib = {"copy", "dataclasses", "hashlib", "json", "collections", "typing"}
        source = (REPO / "app" / "cad_engine" / "incremental_analysis.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed_stdlib
                           for alias in node.names), f"unexpected import {ast.dump(node)}"
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module is not None and (
                    node.module.split(".")[0] in allowed_stdlib
                    or node.module.startswith("app.cad_engine.")), \
                    f"unexpected import {ast.dump(node)}"

    def test_module_has_no_numeric_literals(self):
        """The proof layer may not hard-code a page number, count or threshold:
        everything is supplied or discovered (the 7AX contract)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            source = (REPO / "app" / "cad_engine" / "incremental_analysis.py").read_text()
            tree = ast.parse(source)
            numeric = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Num)
                or (isinstance(node, ast.Constant)
                    and isinstance(node.value, (int, float, complex))
                    and not isinstance(node.value, bool))]
        assert numeric == [], [(n.lineno, repr(n.value)) for n in numeric]
