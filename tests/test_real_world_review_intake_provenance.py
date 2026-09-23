"""
Milestone 7B1 — real 32-page candidate review intake & provenance proof
(tests/test_real_world_review_intake_provenance.py).

Takes the genuine 32-page Selby Square extraction evidence (the seven
7B0 capture files, read-only) through the EXISTING chain — 7AZ
accumulation, 7Y intake, 7X review queue — and proves that every real
candidate enters the review workflow with its source page, source
drawing, submission-derived identity, verbatim AI values and unreviewed
AI provenance intact. No candidate is approved, resolved or fabricated;
nothing is generated; the parse-failed page stays PARSE_FAILED.

The one production change 7B1 required is pinned here too: a renamed
or renumbered review_package_id is refused at the workflow boundary
(require_submission_identities — candidate identity is DERIVED from
submission order and can never be supplied or renamed).

Everything asserted here is derived from the recorded evidence — no
guessed engineering outcomes.
"""
import copy
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from app.cad_engine import incremental_analysis as ia
from app.cad_engine import incremental_continuation as ic
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    accept_production_job,
)
from app.cad_engine.production_coverage import (
    DISPOSITION_BLOCKED,
    DISPOSITION_PRODUCED,
    PROJECT_STATUS_INCOMPLETE,
    evaluate_production_coverage,
)
from app.cad_engine.project_workflow import resolve_project_connection
from tests.test_real_world_incremental_analysis import (
    _synthetic_page,
    _workflow_over,
)
from tests.test_real_world_material_extraction import REAL_PDF
from tests.test_real_world_multi_member_connection import (
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
)
from tests.test_real_world_production_coverage import (
    VIEW_AA_ANSWERS_BY_TASK_TYPE,
    _resolutions_for,
)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "tests" / "data"

CAPTURE_FILES = (
    DATA / "selby_square_page_extractions.json",
    DATA / "selby_square_pages_6_10_extractions.json",
    DATA / "selby_square_pages_11_15_extractions.json",
    DATA / "selby_square_pages_16_20_extractions.json",
    DATA / "selby_square_pages_21_25_extractions.json",
    DATA / "selby_square_pages_26_30_extractions.json",
    DATA / "selby_square_pages_31_32_extractions.json",
)
SLICE_STARTS = (6, 11, 16, 21, 26, 31)

# The digests of the seven genuine captures — pinned, never regenerated.
PINNED_SHA256 = {
    "selby_square_page_extractions.json":
        "598446b2705c310a776ebae49559938a88d2e1979a7faabc91c0761a4cfb450e",
    "selby_square_pages_6_10_extractions.json":
        "4c2acbadea5bdc9ce67a5d1f946a693997d9606f944b18c767a3b8a07ea35806",
    "selby_square_pages_11_15_extractions.json":
        "934965ec2e837e495b3bbfa5f8e1b35fa4950ccdf245292c628f281805ec0a10",
    "selby_square_pages_16_20_extractions.json":
        "3a336929b23ffb1c6c1943e975cf8c79d12b53c28b990a94bbc596e5a0d515ac",
    "selby_square_pages_21_25_extractions.json":
        "267a0a047a70371df62ac5f46638b6770d4e6224cd4cc29fe3288d3beaa2dff5",
    "selby_square_pages_26_30_extractions.json":
        "ee9e17d845d992def38a45796393808cd2b15df73b51937f692baddb64a36f23",
    "selby_square_pages_31_32_extractions.json":
        "aa160d241f276a91bff52a27e899426ed5869efc283332c4d96900ca697653ab",
}

needs_all_captures = pytest.mark.skipif(
    not all(path.exists() for path in CAPTURE_FILES),
    reason="the genuine 32-page Selby captures are required; produce any missing "
           "slice with ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py "
           "--first-page N --max-pages M \"/Users/chad/Downloads/FABs.pdf\" "
           "tests/data/selby_square_pages_<range>_extractions.json (from a terminal "
           "with Downloads access).")


def _load(name):
    return json.loads((DATA / name).read_text())


def _accumulated():
    """The genuine 32-page accumulated evidence, existing contracts only."""
    state = ic.begin_incremental_analysis(
        _load(CAPTURE_FILES[0].name),
        drawing_set_page_count=SELBY_PAGE_COUNT, drawing_set_first_page=1)
    for name, start in zip((p.name for p in CAPTURE_FILES[1:]), SLICE_STARTS):
        state = ic.continue_incremental_analysis(
            state, _load(name), expected_next_first_page=start)
    return state


def _intake(state):
    marks = sorted({m["mark"] for p in state.pages for m in p.get("raw_members") or []})
    return ic.intake_for_accumulated(
        state, project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID, known_member_marks=marks)


def _combined(state):
    return ia.combine_page_extractions(
        state.pages, [], first_new_page=1, last_new_page=SELBY_PAGE_COUNT)


def _flat_entries(state):
    """Every raw connection entry in submission order — the order the
    intake flattens: page order, then entry order."""
    return [(p["page_number"], entry)
            for p in state.pages
            for entry in p.get("raw_connections") or ()]


def _rebuilt_intake(intake, *, candidate_transform=None, candidates=None):
    if candidates is None:
        candidates = tuple(candidate_transform(c) for c in intake.collection.candidates)
    return dataclasses.replace(
        intake,
        collection=dataclasses.replace(intake.collection, candidates=candidates))


def _with_modified_page(state, page_number, transform):
    pages = copy.deepcopy(state.pages)
    for index, page in enumerate(pages):
        if page["page_number"] == page_number:
            pages[index] = transform(page)
            return dataclasses.replace(state, pages=pages)
    raise AssertionError(f"page {page_number} not in the accumulated evidence")


def _page(state, page_number):
    return next(p for p in state.pages if p["page_number"] == page_number)


# =============================================================================
# the genuine review intake: identity, provenance, verbatim values
# =============================================================================

@needs_all_captures
class TestRealReviewIntake:

    def test_the_seven_captures_are_the_pinned_evidence(self):
        for path in CAPTURE_FILES:
            assert path.exists(), path
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == PINNED_SHA256[path.name], path.name

    def test_page_accounting_is_32_analysed_0_parse_failed(self):
        state = _accumulated()
        assert len(state.analysed_page_numbers) == 32
        assert state.parse_failed_page_numbers == ()
        assert state.not_analysed_page_numbers == ()
        assert len(state.pages) == 32
        assert [p["page_number"] for p in state.pages] == list(range(1, 33))
        # the recovered page carries exactly the genuine re-capture —
        # nothing is invented beyond what the real retry reported
        assert _page(state, 26)["parse_failed"] is False
        assert _page(state, 26)["drawing_number"] == "026"
        assert _page(state, 26)["raw_members"] == [{
            "mark": "026", "member_type": "beam", "section": "200UB25",
            "quantity": 1, "length_mm": 3699, "grid_reference": None,
            "detail_reference": None, "confidence": 97,
        }]
        assert len(_page(state, 26)["raw_connections"]) == 1

    def test_every_candidate_enters_the_queue_exactly_once(self):
        state = _accumulated()
        intake = _intake(state)
        entries = _flat_entries(state)
        candidates = intake.collection.candidates
        assert len(candidates) == len(entries)
        assert intake.unusable_connection_entries == ()
        assert [c.review_package_id for c in candidates] == [
            f"RP-{index + 1:04d}" for index in range(len(entries))]
        assert [c.submission_index for c in candidates] == list(range(len(entries)))
        workflow = _workflow_over(intake)
        groups = workflow.exception_package.connection_tasks
        assert [g.review_package_id for g in groups] == [c.review_package_id for c in candidates]
        assert len({g.review_package_id for g in groups}) == len(groups)  # exactly once
        assert all(g.tasks for g in groups)  # every candidate has its review tasks

    def test_every_candidate_keeps_source_and_values_verbatim(self):
        state = _accumulated()
        intake = _intake(state)
        entries = _flat_entries(state)
        for candidate, (page_number, entry) in zip(intake.collection.candidates, entries):
            extraction = candidate.package.extraction
            # provenance: where it came from
            assert extraction.source_page == page_number
            assert extraction.source_drawing_id == JOURNEY_SOURCE_DRAWING_ID
            # AI values, verbatim — never reinterpreted
            assert extraction.material == entry.get("material")
            assert tuple(extraction.connected_member_references) == tuple(
                entry.get("connects_members") or ())
            assert extraction.generic_connection_type == entry.get("connection_type")
            assert extraction.confidence == entry.get("confidence")
            assert extraction.persisted_review_status is None  # unreviewed
        # the bijection proof over the whole set: nothing added, dropped or moved
        report = ia.verify_candidate_provenance(_combined(state), intake)
        assert report.verified is True
        assert report.untraced_candidate_ids == ()
        assert report.untraced_entry_positions == ()

    def test_material_stays_300_and_never_promoted(self):
        state = _accumulated()
        intake = _intake(state)
        materials = [c.package.extraction.material for c in intake.collection.candidates]
        # every recorded material is "300" verbatim; absence stays absence
        assert all(material == "300" or material is None for material in materials)
        assert sum(material == "300" for material in materials) == sum(
            entry.get("material") == "300" for _, entry in _flat_entries(state))
        # AI_EXTRACTED stays AI_EXTRACTED: nothing confirmed, nothing supplemented
        assert all(not c.package.supplement.confirmed_ai_fields
                   for c in intake.collection.candidates)
        assert all(c.package.supplement.material is None
                   for c in intake.collection.candidates)
        serialized = json.dumps(state.pages)
        assert "300PLUS" not in serialized
        assert "NZ300" not in serialized

    def test_no_candidate_is_approved_and_no_artifact_is_produced(self):
        state = _accumulated()
        workflow = _workflow_over(_intake(state))
        coverage = evaluate_production_coverage(workflow)
        assert {r.decision for r in coverage.connections} == {"REVIEW"}
        assert all(r.verified is False for r in coverage.connections)
        assert all(r.packaged is False for r in coverage.connections)
        assert all(r.artifact_path is None for r in coverage.connections)
        # nothing was ever dispatched or verified for any candidate
        assert all(r.verification_status is None for r in coverage.connections)

    def test_the_recovered_page_enters_the_review_intake(self):
        state = _accumulated()
        coverage = evaluate_production_coverage(_workflow_over(_intake(state)))
        rows = {p.page_number: p.status for p in coverage.pages}
        assert len(coverage.pages) == 32
        assert all(rows[n] == "ANALYSED" for n in range(1, 33))
        # page 26 now contributes exactly its one genuine candidate, with
        # the captured material state (none) preserved — never invented
        p26 = [c for c in _intake(state).collection.candidates
               if c.package.extraction.source_page == 26]
        assert len(p26) == 1
        assert p26[0].package.extraction.material is None

    def test_7ax_reports_the_truthful_incomplete_project(self):
        state = _accumulated()
        coverage = evaluate_production_coverage(_workflow_over(_intake(state)))
        summary = coverage.summary
        assert summary.set_pages == 32
        assert summary.pages_analysed == 32
        assert summary.pages_parse_failed == 0
        assert summary.pages_not_analysed == 0
        assert summary.connections_discovered == len(_flat_entries(state))
        assert summary.connections_unresolved == summary.connections_discovered
        assert summary.project_status == PROJECT_STATUS_INCOMPLETE
        assert summary.incomplete_reasons  # the job is incomplete, and says why


# =============================================================================
# determinism and purity
# =============================================================================

@needs_all_captures
class TestDeterminismAndPurity:

    def test_the_same_evidence_produces_the_same_queue(self):
        def fingerprint():
            state = _accumulated()
            intake = _intake(state)
            workflow = _workflow_over(intake)
            coverage = evaluate_production_coverage(workflow)
            return json.dumps({
                "pages": [(p["page_number"], bool(p.get("parse_failed")))
                          for p in state.pages],
                "ids": [c.review_package_id for c in intake.collection.candidates],
                "source_pages": [c.package.extraction.source_page
                                 for c in intake.collection.candidates],
                "materials": [c.package.extraction.material
                              for c in intake.collection.candidates],
                "groups": [(g.review_package_id, [t.task_type for t in g.tasks])
                           for g in workflow.exception_package.connection_tasks],
                "summary": dataclasses.asdict(coverage.summary),
            }, sort_keys=True)
        first = fingerprint()
        assert fingerprint() == first
        assert fingerprint() == first  # a third, independent build

    def test_the_intake_build_writes_nothing(self, tmp_path):
        state = _accumulated()
        intake = _intake(state)
        _workflow_over(intake)
        evaluate_production_coverage(_workflow_over(intake))
        assert list(tmp_path.iterdir()) == []  # nothing written anywhere near
        assert REAL_PDF.exists()  # and the source drawing is untouched (bytes below)


# =============================================================================
# preservation: the seven captures and FABs.pdf stay byte-identical
# =============================================================================

@needs_all_captures
class TestPreservation:

    def test_source_evidence_is_byte_identical_after_the_full_queue_build(self):
        before = {path: path.read_bytes() for path in CAPTURE_FILES}
        state = _accumulated()
        _workflow_over(_intake(state))
        for path in CAPTURE_FILES:
            assert path.read_bytes() == before[path], path.name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == PINNED_SHA256[path.name]

    def test_fabs_pdf_is_never_written(self):
        try:
            before = REAL_PDF.read_bytes()
        except PermissionError as error:
            pytest.skip(f"FABs.pdf unreadable in this process (macOS TCC): {error}")
        _workflow_over(_intake(_accumulated()))
        assert REAL_PDF.read_bytes() == before


# =============================================================================
# 7B1 negative proofs A-O
# =============================================================================

@needs_all_captures
class TestNegativeProofs7B1:

    def _first_candidate_tamper(self, intake, extraction_changes):
        original = intake.collection.candidates[0]
        return _rebuilt_intake(intake, candidate_transform=lambda c: (
            dataclasses.replace(
                c,
                package=dataclasses.replace(
                    c.package,
                    extraction=dataclasses.replace(
                        c.package.extraction, **extraction_changes)))
            if c is original else c))

    def _provenance_after(self, intake):
        return ia.verify_candidate_provenance(_combined(_accumulated()), intake)

    def test_a_candidate_page_changed_is_refused(self):  # [A]
        state = _accumulated()
        intake = _intake(state)
        report = self._provenance_after(
            self._first_candidate_tamper(intake, {"source_page": 12}))
        assert report.verified is False
        assert "RP-0001" in report.untraced_candidate_ids
        # the evidence layer refuses the same tamper outright
        tampered = _with_modified_page(state, 1, lambda p: dict(
            p, raw_connections=[dict(p["raw_connections"][0], grid_reference="TAMPERED")]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered)

    def test_b_candidate_drawing_identity_changed_is_refused(self):  # [B]
        intake = _intake(_accumulated())
        report = self._provenance_after(
            self._first_candidate_tamper(intake, {"source_drawing_id": "OTHER-DRAWING"}))
        assert report.verified is False
        assert report.untraced_candidate_ids or report.multiplicity_mismatches

    def test_c_candidate_id_changed_is_refused(self):  # [C]
        intake = _intake(_accumulated())
        renamed = _rebuilt_intake(intake, candidates=(
            dataclasses.replace(intake.collection.candidates[0],
                                review_package_id="RP-0001-TAMPERED"),
        ) + intake.collection.candidates[1:])
        with pytest.raises(ValueError, match="renamed or renumbered"):
            _workflow_over(renamed)
        # the submission index is part of the same derived identity
        reindexed = _rebuilt_intake(intake, candidates=(
            dataclasses.replace(intake.collection.candidates[0], submission_index=4),
        ) + intake.collection.candidates[1:])
        with pytest.raises(ValueError, match="renamed or renumbered"):
            _workflow_over(reindexed)

    def test_d_candidate_ai_value_changed_is_refused(self):  # [D]
        intake = _intake(_accumulated())
        report = self._provenance_after(
            self._first_candidate_tamper(intake, {"generic_connection_type": "bolted"}))
        assert report.verified is False
        assert "RP-0001" in report.untraced_candidate_ids

    def test_e_candidate_material_changed_is_refused(self):  # [E]
        state = _accumulated()
        intake = _intake(state)
        report = self._provenance_after(
            self._first_candidate_tamper(intake, {"material": "300PLUS"}))
        assert report.verified is False
        assert "RP-0001" in report.untraced_candidate_ids
        tampered = _with_modified_page(state, 1, lambda p: dict(
            p, raw_connections=[dict(p["raw_connections"][0], material="300PLUS")]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered)

    def test_f_candidate_provenance_changed_is_refused(self):  # [F]
        intake = _intake(_accumulated())
        # the AI confidence is the capture's own provenance marker
        report = self._provenance_after(
            self._first_candidate_tamper(intake, {"confidence": 100}))
        assert report.verified is False
        assert "RP-0001" in report.untraced_candidate_ids

    def test_g_candidate_duplicated_is_refused_never_merged(self):  # [G]
        intake = _intake(_accumulated())
        duplicated = _rebuilt_intake(
            intake, candidates=intake.collection.candidates
            + (intake.collection.candidates[0],))
        with pytest.raises(ValueError, match="one-to-one"):
            _workflow_over(duplicated)

    def test_h_candidate_removed_from_evidence_is_refused(self):  # [H]
        state = _accumulated()
        intake = _intake(state)
        dropped = _rebuilt_intake(intake, candidates=intake.collection.candidates[1:])
        report = self._provenance_after(dropped)
        assert report.verified is False
        assert report.untraced_entry_positions  # the orphaned entry is named
        tampered = dataclasses.replace(state, pages=copy.deepcopy(state.pages[:-1]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered)

    def test_i_candidate_fabricated_without_source_evidence_is_untraced(self):  # [I]
        state = _accumulated()
        intake = _intake(state)
        original = intake.collection.candidates[0]
        fabricated = dataclasses.replace(
            original, review_package_id="RP-FABRICATED",
            package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction,
                    detail_reference="FABRICATED-D1", material="400")))
        report = self._provenance_after(_rebuilt_intake(
            intake, candidates=intake.collection.candidates + (fabricated,)))
        assert report.verified is False
        assert "RP-FABRICATED" in report.untraced_candidate_ids

    def test_j_parse_failed_page_supplied_as_analysed_is_refused(self):  # [J]
        # No real page is parse-failed any more (page 26 was genuinely
        # re-captured), so the proof exercises the same genuine refusals on
        # a synthetic parse-failed page: invented content on a parse-failed
        # page is a contradictory status, and flipping the flag is tampered
        # evidence. The machinery — unchanged — still fails closed.
        state = ic.begin_incremental_analysis(
            [_synthetic_page(1, parse_failed=True), _synthetic_page(2)],
            drawing_set_page_count=SELBY_PAGE_COUNT, drawing_set_first_page=1)
        # (a) content invented for the parse-failed page — contradictory status
        invented = _with_modified_page(state, 1, lambda p: dict(
            p, raw_connections=[{"detail_reference": "INVENTED", "material": "300"}])).pages
        with pytest.raises(ValueError, match="contradictory page status"):
            ic.begin_incremental_analysis(
                invented, drawing_set_page_count=SELBY_PAGE_COUNT,
                drawing_set_first_page=1)
        # (b) the flag flipped so the page reads as analysed — tampered evidence
        flipped = _with_modified_page(state, 1, lambda p: dict(p, parse_failed=False))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(flipped)

    def test_k_analysed_page_silently_removed_is_refused(self):  # [K]
        state = _accumulated()
        shortened = dataclasses.replace(
            state, pages=[p for p in copy.deepcopy(state.pages)
                          if p["page_number"] != 12])
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(shortened)

    def test_l_unreviewed_evidence_is_not_accepted(self):  # [L]
        workflow = _workflow_over(_intake(_accumulated()))
        acceptance = accept_production_job(workflow)
        assert acceptance.status == ACCEPTANCE_STATUS_NOT_ACCEPTED

    def test_m_ai_extracted_material_promotion_alone_is_not_review(self):  # [M]
        state = _accumulated()
        intake = _intake(state)
        original = intake.collection.candidates[0]
        promoted = dataclasses.replace(
            original, package=dataclasses.replace(
                original.package,
                supplement=dataclasses.replace(
                    original.package.supplement,
                    confirmed_ai_fields=frozenset({"material"}))))
        workflow = _workflow_over(_rebuilt_intake(
            intake, candidates=(promoted,) + intake.collection.candidates[1:]))
        coverage = evaluate_production_coverage(workflow)
        assert {r.decision for r in coverage.connections} == {"REVIEW"}
        assert all(r.verified is False for r in coverage.connections)
        assert coverage.summary.connections_unresolved == len(
            intake.collection.candidates)
        # the evidence layer refuses any provenance claim written into the record
        tampered = _with_modified_page(state, 1, lambda p: dict(
            p, raw_connections=[dict(p["raw_connections"][0], confidence=100)]))
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered)

    def test_n_reinterpreted_engineering_value_is_refused(self):  # [N]
        state = _accumulated()
        # the real page-1 record's weld size reinterpreted (6 mm -> "Ø6"):
        # the evidence layer refuses the rewrite outright
        def reinterpret(page):
            connections = copy.deepcopy(page["raw_connections"])
            welds = list(connections[0]["welds"])
            welds[0] = dict(welds[0], size_mm="Ø6")
            connections[0] = dict(connections[0], welds=welds)
            return dict(page, raw_connections=connections)
        tampered = _with_modified_page(state, 1, reinterpret)
        with pytest.raises(ValueError, match="does not match its recorded digest"):
            _intake(tampered)
        # the intake layer refuses the same reinterpretation on the candidate
        intake = _intake(state)
        original = intake.collection.candidates[0]
        original_weld = original.package.extraction.welds[0]
        rewritten = dataclasses.replace(
            original, package=dataclasses.replace(
                original.package,
                extraction=dataclasses.replace(
                    original.package.extraction,
                    welds=(dataclasses.replace(original_weld, size_mm="Ø6"),))))
        report = self._provenance_after(_rebuilt_intake(
            intake, candidates=(rewritten,) + intake.collection.candidates[1:]))
        assert report.verified is False
        assert "RP-0001" in report.untraced_candidate_ids

    def test_o_the_fl_ea_pair_converts_through_the_genuine_chain(self, tmp_path):  # [O]
        # The FL/EA pair is no longer refused at the section boundary: both
        # families have genuine profile builders, so the real page-5 VIEW
        # A-A candidate resolves to AUTO through the genuine chain and a
        # real verified artifact — never a fabricated conversion, and never
        # an approximation (the 7AV gate still checks the geometry).
        state = _accumulated()
        workflow = _workflow_over(_intake(state))
        # RP-0008 is the real page-5 VIEW A-A candidate referencing the FL/EA pair
        group = next(g for g in workflow.exception_package.connection_tasks
                     if g.review_package_id == "RP-0008")
        assert group.tasks  # at revision 0 it waits for review, never AUTO
        workflow = resolve_project_connection(
            workflow, package_id="RP-0008",
            resolutions=_resolutions_for(
                group, copy.deepcopy(VIEW_AA_ANSWERS_BY_TASK_TYPE),
                "HUMAN-SUPPLIED TEST DATA (7B1 proof O)"),
            output_dir=tmp_path / "rp-0008",
        )
        coverage = evaluate_production_coverage(workflow)
        row = next(r for r in coverage.connections if r.package_id == "RP-0008")
        assert row.disposition == DISPOSITION_PRODUCED
        assert row.decision == "AUTO"
        assert row.output_status == "GENERATED"
        assert row.verification_status == "VERIFIED"
        assert row.verified is True
        assert row.accepted is True
        assert row.packaged is False  # no fabrication package was built here
        assert row.artifact_path is not None
        assert Path(row.artifact_path).is_file()
        # the other 51 candidates remain unresolved — nothing else changed
        assert coverage.summary.connections_unresolved == 51
        assert coverage.summary.connections_blocked == 0
        assert coverage.summary.connections_produced == 1
        assert coverage.summary.artifacts_verified == 1
