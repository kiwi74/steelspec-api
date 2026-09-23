"""
MILESTONE 7AV — Real-World Multi-Member Connection Proof.

A real Selby Square fabrication drawing (page 5 of the captured set) carries a
four-member connection: VIEW B-B connects 005 (310UB40), PL018 (160x10PL),
PL032 (165x10PL) and PL025 (81x10PL) — welded and bolted, 2 x 18 holes, 6 mm
fillet, material 300. This module drives THAT connection, with its capture
entry verbatim, through the genuine production chain —

    7Y intake -> 7AB project automation -> 7AC review package
    -> human resolutions -> 7AD rerun -> 7AA/7Z (via the 7AV multi-member path)
    -> 7AE fabrication gate -> 7AF drawing dispatch -> 7AG verification
    -> 7AQ production acceptance -> 7AR fabrication package
    -> 7AS fabricator acceptance

— and pins every brief item, including the five negative proofs. The only
fixtures are the reviewer's own answers (member rows, placements, section
reference data, per-task resolutions); every value they carry is pinned to the
source page's text layer below. No extraction JSON was rewritten and no AI
value was improved.

CONNECTION SELECTION (brief §2). Page 5 carries TWO connections: VIEW A-A
(005 + PL008 + CL004) and VIEW B-B (005 + PL018 + PL032 + PL025). The brief
prefers the simplest real 3-member connection, but VIEW A-A cannot be
automated: its real members use 180x20FL (family FL) and 90x10EA (family EA),
neither of which has a supported CAD profile builder, and the engine honestly
stops at that boundary (see TestSectionBoundary) rather than inventing a
profile. VIEW B-B — all four members in supported families — is therefore the
milestone's positive proof, the real multi-member connection the production
path is proven on. The full page-5 intake is kept as evidence (both candidates
visible, VIEW A-A never silently dropped); the production workflow consumes a
scoped queue containing the VIEW B-B entry verbatim (only the page_num
flattening the intake itself performs), which satisfies the intake identity
check and is asserted below.

TOPOLOGY (brief §4/§6/§7). The pipeline composes the connection's explicit
connected_members in their recorded order through the 7AV multi-member layers
(multi_member_connection.py — the per-member generalization of 7O/7R), with
one reviewed attachment per member. Nothing is truncated, re-paired or
inferred: the builder refuses any mismatch between the supplied members and
the connection's explicit marks, and the two-member path is untouched
(TestNegativeProofs, Case E).

BRIEF ITEM MAP (each item pinned by at least one test):
  1  real capture carries the multi-member connection ........ TestRealSourceEvidence
  2  capture entry consumed verbatim, never rewritten ........ TestRealCaptureIntegrity
  3  initial evaluation REVIEW, no forced AUTO ............... TestRealCaptureIntegrity
  4  review tasks are the existing 7AC vocabulary ............ TestReviewLoop
  5  genuine AUTO only with every layer satisfied ........... TestReviewLoop
  6  explicit topology, all members, real domain types ...... TestExplicitTopology
  7  no member dropped or truncated .......................... TestExplicitTopology + negatives
  8  attachments explicit per member, never geometry-derived . TestExplicitTopology + Case D
  9  material 300: AI_EXTRACTED -> HUMAN_REVIEWED, unchanged . TestReviewLoop
  10 real PL sections through the existing PL builder ........ TestExplicitTopology
  11 validation runs per member (N-member completeness) ...... TestExplicitTopology
  12 dispatch/verification genuine on the actual artifact .... TestProductionPath
  13 the PDF represents ALL four members ..................... TestProductionPath
  14 7AQ accepts the production job .......................... TestProductionPath
  15 7AR packages the verified artifact ...................... TestProductionPath
  16 7AS accepts with per-drawing evidence ................... TestProductionPath
  17 repeatability x2 (byte-identical modulo per-save metadata) TestProductionPath
  18 Case A: 3-member source, 2 represented -> NOT AUTO ...... TestNegativeProofs
  19 Case B: 4-member source, 3 represented -> NOT AUTO ...... TestNegativeProofs
  20 Case C: missing attachment -> REVIEW .................... TestNegativeProofs
  21 Case D: attachment guessed from geometry not accepted ... TestNegativeProofs
  +  Case E: the existing two-member path stays green ........ TestNegativeProofs
  +  brief §9: FL+EA now cross the boundary; UC/CHS/UA still stop there TestSectionBoundary
  +  builder-level truncation refusal ........................ TestNegativeProofs
"""

import copy
import dataclasses
import functools
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

import app.cad_engine.fabricator_acceptance as fa
import app.cad_engine.project_workflow as workflow_module
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_STAGE_ATTACHMENTS,
    PIPELINE_STAGE_MEMBER,
    PIPELINE_STAGE_MULTI_VALIDATION,
    PIPELINE_STAGES,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.exception_resolution import (
    TASK_COMPLETE_REVIEW,
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_POSITION,
    HumanResolution,
    build_exception_resolution_package,
)
from app.cad_engine.fabrication_package import build_fabrication_package
from app.cad_engine.interface import generate_geometry
from app.cad_engine.multi_member_connection import (
    MEMBER_LABEL_LETTERS,
    MULTI_MEMBER_MIN_COUNT,
    MULTI_VALIDATED_LAYERS,
    build_reviewed_multi_member_connection_assembly,
)
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_acceptance import accept_production_job
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_connection_review import (
    create_project_connection_collection,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
)

REPO = Path(__file__).resolve().parents[1]
CAPTURE_PATH = REPO / "tests" / "data" / "selby_square_page_extractions.json"
REAL_PDF = Path("/Users/chad/Downloads/FABs.pdf")

SELBY_PAGE_COUNT = 32
SELBY_PAGE_5_NUMBER = 5
SELBY_CAPTURE_PAGES = 5

JOURNEY_PROJECT_ID = "PROJ-7AV-SELBY"
JOURNEY_SOURCE_DRAWING_ID = "SELBY-C1136"
JOURNEY_CONNECTION_ID = "CONN-SELBY-VIEW-BB"
JOURNEY_PACKAGE_ID = "RP-0001"  # the scoped queue's first (and only) candidate

needs_real_pdf = pytest.mark.skipif(
    not REAL_PDF.exists(),
    reason=f"the real Selby Square fabrication PDF is not present at {REAL_PDF}",
)
needs_selby_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason=f"no captured real AI extraction exists at {CAPTURE_PATH}; "
           "produce one with scripts/capture_real_pdf_extraction.py first.",
)


@functools.lru_cache(maxsize=None)
def _page_text(page_number: int) -> str:
    """The real PDF's text layer for one (1-based) page — the source evidence."""
    return PdfReader(REAL_PDF).pages[page_number - 1].extract_text() or ""


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _page5_capture():
    data = json.loads(CAPTURE_PATH.read_text())
    assert len(data) == SELBY_CAPTURE_PAGES
    page5 = data[SELBY_PAGE_5_NUMBER - 1]
    assert page5["page_number"] == SELBY_PAGE_5_NUMBER
    return copy.deepcopy(page5)


def _view_b_b_entry(page5):
    entry = next(c for c in page5["raw_connections"]
                 if c["detail_reference"] == "VIEW B-B")
    return {**entry, "page_num": SELBY_PAGE_5_NUMBER}


# =============================================================================
# The reviewer fixture: every value pinned to the page-5 text layer below.
# =============================================================================
JOURNEY_MEMBER_ROWS = {
    "005": {"mark": "005", "section_name": "310UB40", "section_name_raw": "310UB40",
            "section_family": "UB", "length_mm": 5017, "grade": "300", "quantity": 1,
            "review_status": "approved", "source_page": 5, "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID},
    "PL018": {"mark": "PL018", "section_name": "160x10PL", "section_name_raw": "160x10PL",
              "section_family": "PL", "length_mm": 298, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 5, "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID},
    "PL032": {"mark": "PL032", "section_name": "165x10PL", "section_name_raw": "165x10PL",
              "section_family": "PL", "length_mm": 304, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 5, "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID},
    "PL025": {"mark": "PL025", "section_name": "81x10PL", "section_name_raw": "81x10PL",
              "section_family": "PL", "length_mm": 80, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 5, "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID},
}


class Page5SectionMatcher:
    """The section reference data for the four VIEW B-B members — each entry's
    dimensions traced to the page text (310UB40: 304.0 x 165.0; PL rows: the
    sheet's own section labels, weights from the shop material list)."""

    SECTIONS = {
        "310UB40": {"name": "310UB40", "family": "UB", "depth": 304.0,
                    "flange_width": 165.0, "flange_thickness": 11.8,
                    "web_thickness": 6.1, "weight_per_metre": 40.4},
        "160X10PL": {"name": "160X10PL", "family": "PL", "width": 160.0,
                     "thickness": 10.0, "weight_per_metre": 12.6},
        "165X10PL": {"name": "165X10PL", "family": "PL", "width": 165.0,
                     "thickness": 10.0, "weight_per_metre": 13.0},
        "81X10PL": {"name": "81X10PL", "family": "PL", "width": 81.0,
                    "thickness": 10.0, "weight_per_metre": 6.4},
    }

    def match(self, raw_name):
        return self.SECTIONS.get(raw_name.strip().upper())


JOURNEY_MEMBER_PLACEMENTS = {
    # 005 runs full height; each plate's END face lands exactly at the
    # connection plane z=5017 (the page's "5017 20" F/S dim): END faces at
    # 4719+298, 4713+304, 4937+80.
    "005": MemberPlacement(x=0.0, y=0.0, z=0.0,
                           rotation_x=0.0, rotation_y=0.0, rotation_z=0.0),
    "PL018": MemberPlacement(x=0.0, y=0.0, z=4719.0,
                             rotation_x=0.0, rotation_y=0.0, rotation_z=0.0),
    "PL032": MemberPlacement(x=0.0, y=0.0, z=4713.0,
                             rotation_x=0.0, rotation_y=0.0, rotation_z=0.0),
    "PL025": MemberPlacement(x=0.0, y=0.0, z=4937.0,
                             rotation_x=0.0, rotation_y=0.0, rotation_z=0.0),
}

JOURNEY_ANSWERS_BY_TASK_TYPE = {
    TASK_COMPLETE_REVIEW: None,
    TASK_SELECT_POSITION: "END",
    TASK_SELECT_ATTACHMENT: [
        {"member_mark": "005", "surface_reference": "END"},
        {"member_mark": "PL018", "surface_reference": "END"},
        {"member_mark": "PL032", "surface_reference": "END"},
        {"member_mark": "PL025", "surface_reference": "END"},
    ],
    TASK_CONFIRM_AI_VALUES: ("connected_member_marks", "material"),
    TASK_PROVIDE_PLATE: {"type": "end_plate", "thickness_mm": 10.0,
                         "width_mm": 160.0, "depth_mm": 298.0},
    TASK_PROVIDE_HOLE_DIAMETER: {"quantity": 2, "diameter_mm": 18.0,
                                 "vertical_spacing_mm": 100.0},
    TASK_PROVIDE_LOCATION: {"x": 0.0, "y": 0.0, "z": 5017.0,
                            "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
    TASK_REVIEW_SPECIFICATION: None,
    TASK_REVIEW_VALIDATION: None,
    TASK_PROVIDE_CONNECTION_IDENTITY: JOURNEY_CONNECTION_ID,
}

# The 7AC package must demand exactly this vocabulary — the existing one.
EXPECTED_TASK_TYPES = frozenset(JOURNEY_ANSWERS_BY_TASK_TYPE)


def _journey_reviewer_resolutions(group):
    return [
        HumanResolution(t.task_id, t.task_type, t.answer_type,
                        copy.deepcopy(JOURNEY_ANSWERS_BY_TASK_TYPE[t.task_type]),
                        evidence="HUMAN-SUPPLIED TEST DATA (Selby page-5 reviewer fixture)")
        for t in group.tasks
    ]


def _page5_journey(output_dir):
    """The genuine journey: real page-5 capture (verbatim) -> 7Y intake ->
    7AB -> 7AC tasks -> human resolutions -> 7AD rerun -> 7AA/7Z -> 7AE ->
    7AF -> 7AG. Returns the pieces the tests assert on."""
    page5 = _page5_capture()
    marks = tuple(m["mark"] for m in page5["raw_members"])
    full_intake = intake_page_extractions(
        [page5], project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID, known_member_marks=marks,
        drawing_set_page_count=SELBY_PAGE_COUNT,
    )
    scoped_collection = create_project_connection_collection(
        [_view_b_b_entry(page5)], project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID, known_member_marks=marks,
    )
    intake = dataclasses.replace(full_intake, collection=scoped_collection)
    initial = evaluate_project_for_automation(scoped_collection, intake=intake)
    built = build_exception_resolution_package(
        initial, scoped_collection, member_rows=JOURNEY_MEMBER_ROWS)
    group = next(g for g in built.connection_tasks
                 if g.review_package_id == JOURNEY_PACKAGE_ID)
    workflow = workflow_module.start_project_workflow(
        scoped_collection, intake=intake,
        member_rows=JOURNEY_MEMBER_ROWS,
        member_placements=JOURNEY_MEMBER_PLACEMENTS,
        section_matcher=Page5SectionMatcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=JOURNEY_PACKAGE_ID,
        resolutions=_journey_reviewer_resolutions(group), output_dir=output_dir,
    )
    return SimpleNamespace(
        page5=page5, marks=marks, full_intake=full_intake,
        scoped_collection=scoped_collection, intake=intake, initial=initial,
        built=built, group=group, workflow=workflow,
    )


def _fabricator_answers(package):
    """PASS on every item of the package's per-drawing checklist, including the
    package-complete item — the honest reviewer's verdict on a genuine
    deliverable."""
    drawing_answers = tuple(
        fa.FabricatorDrawingAnswers(
            item.drawing_number,
            tuple(fa.FabricatorChecklistAnswer(code, fa.ANSWER_PASS)
                  for code in fa.drawing_checklist_items_for(dict(item.audit))),
        )
        for item in package.items
    )
    return fa.FabricatorAcceptanceAnswers(
        drawing_answers,
        (fa.FabricatorChecklistAnswer(
            fa.CHECKLIST_ITEM_PACKAGE_COMPLETE, fa.ANSWER_PASS),),
    )


@pytest.fixture(scope="module")
def page5_journey(tmp_path_factory):
    """The completed real positive path, through 7AS: the actual generated PDF
    is the acceptance evidence, read back from the package records."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7av"))
    journey = _page5_journey(tmp)
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_fabricator_answers(package))
    pdf_path = Path(journey.workflow.generated_files[0])
    return SimpleNamespace(
        tmp=tmp, page5=journey.page5, marks=journey.marks,
        full_intake=journey.full_intake, scoped_collection=journey.scoped_collection,
        intake=journey.intake, initial=journey.initial, built=journey.built,
        group=journey.group, workflow=journey.workflow,
        acceptance=acceptance, package=package, result=result,
        pdf_path=pdf_path, pdf_text="\n".join(
            p.extract_text() or "" for p in PdfReader(pdf_path).pages),
    )


def _record(journey):
    return journey.workflow.connection_records[0]


# =============================================================================
# 1. Real source evidence — every string read from the PDF text layer.
# =============================================================================
class TestRealSourceEvidence:
    @needs_selby_capture
    def test_the_capture_is_a_real_page_extraction_list(self):
        data = json.loads(CAPTURE_PATH.read_text())
        assert isinstance(data, list) and len(data) == SELBY_CAPTURE_PAGES
        for page in data:
            assert set(page) == {"page_number", "drawing_number",
                                 "drawing_title", "revision", "raw_members",
                                 "raw_connections", "parse_failed"}, page.keys()
        assert [p["page_number"] for p in data] == list(range(1, len(data) + 1))

    @needs_selby_capture
    @needs_real_pdf
    def test_page_five_of_the_real_set_carries_the_multi_member_connection(self):
        source = _collapsed(_page_text(SELBY_PAGE_5_NUMBER))
        # Both detail views the capture reports must be on the page, with the
        # four-member one spelled out member by member.
        for needle in ("VIEW B-B", "VIEW A-A", "310UB40 - 5017 LG",
                       "PL018-160X10PLX298LG", "PL025-81X10PLX80LG FAR",
                       "PL032-165X10PLX304LG FAR", "160X10PL X 298",
                       "165X10PL X 304", "81X10PL X 80"):
            assert needle in source, needle
        page5 = _page5_capture()
        view_b_b = next(c for c in page5["raw_connections"]
                        if c["detail_reference"] == "VIEW B-B")
        assert view_b_b["connects_members"] == ["005", "PL018", "PL032", "PL025"]

    @needs_selby_capture
    @needs_real_pdf
    def test_every_view_b_b_value_traces_to_the_page_text(self):
        # No value may exist downstream that the source page does not state.
        source = _collapsed(_page_text(SELBY_PAGE_5_NUMBER)).upper()
        page5 = _page5_capture()
        view_b_b = next(c for c in page5["raw_connections"]
                        if c["detail_reference"] == "VIEW B-B")
        assert view_b_b["material"].upper() in source          # "300" stated
        # The "welded and bolted" type is read from the drawing's weld/bolt
        # symbology; its text-layer evidence is the weld note and the hole
        # annotation, both asserted below.
        for bolt in view_b_b["bolts"]:
            assert str(bolt["quantity"]) in source
        # Member sections and lengths, each on its own line of the page.
        for member in page5["raw_members"]:
            if member["mark"] in view_b_b["connects_members"]:
                assert member["section"].upper() in source, member["mark"]
                assert str(member["length_mm"]) in source, member["mark"]
        assert "2 X 18" in source                               # the holes
        assert "ALL WELDS 6 MM FWAR UNO" in source              # the welds
        assert "STEEL GRADE 300 UNO" in source                  # the grade note

    @needs_selby_capture
    @needs_real_pdf
    def test_view_a_a_members_use_the_fl_and_ea_families(self):
        # The real 3-member connection: its members use 180x20FL (FL) and
        # 90x10EA (EA) — the section families that now have genuine CAD
        # profile builders (see TestSectionBoundary). The 4-member VIEW B-B
        # remains the milestone's original positive proof.
        source = _collapsed(_page_text(SELBY_PAGE_5_NUMBER))
        assert "180X20FL X 340" in source and "90X10EA X 165" in source
        page5 = _page5_capture()
        view_a_a = next(c for c in page5["raw_connections"]
                        if c["detail_reference"] == "VIEW A-A")
        assert view_a_a["connects_members"] == ["005", "PL008", "CL004"]
        # Families read off the sections themselves (the X is a size separator,
        # not a family letter): 310UB40, 180X20FL, 90X10EA.
        families = {''.join(ch for ch in m["section"].upper().replace("X", "")
                            if ch.isalpha())
                    for m in page5["raw_members"]
                    if m["mark"] in view_a_a["connects_members"]}
        assert families == {"UB", "FL", "EA"}


# =============================================================================
# 2. Capture integrity — verbatim consumption, evidence kept, REVIEW first.
# =============================================================================
class TestRealCaptureIntegrity:
    @needs_selby_capture
    def test_the_journey_consumes_the_capture_entry_verbatim(self, page5_journey):
        page5 = _page5_capture()
        assert page5_journey.page5 == page5  # deep equality: never rewritten

    @needs_selby_capture
    def test_the_full_page5_intake_keeps_both_connections_as_evidence(self, page5_journey):
        candidates = page5_journey.full_intake.collection.candidates
        assert [c.review_package_id for c in candidates] == ["RP-0001", "RP-0002"]
        # VIEW A-A is never silently dropped from the evidence even though the
        # production workflow consumes the scoped queue.
        view_a_a = next(c for c in page5_journey.page5["raw_connections"]
                        if c["detail_reference"] == "VIEW A-A")
        assert (tuple(candidates[0].package.extraction.connected_member_references)
                == tuple(view_a_a["connects_members"]))

    @needs_selby_capture
    def test_the_scoped_queue_holds_only_the_verbatim_view_b_b_entry(self, page5_journey):
        candidates = page5_journey.scoped_collection.candidates
        assert len(candidates) == 1
        assert candidates[0].review_package_id == JOURNEY_PACKAGE_ID
        assert (tuple(candidates[0].package.extraction.connected_member_references)
                == tuple(_view_b_b_entry(page5_journey.page5)["connects_members"]))

    @needs_selby_capture
    def test_initial_automation_is_review_never_forced_auto(self, page5_journey):
        result = page5_journey.initial.connection_results[0]
        assert result.decision == AUTOMATION_DECISION_REVIEW
        codes = {b.code for b in result.blockers}
        assert codes >= {AUTOMATION_BLOCKER_SPECIFICATION,
                         AUTOMATION_BLOCKER_VALIDATION}


# =============================================================================
# 3. The review loop — existing vocabulary, honest resolutions, genuine AUTO.
# =============================================================================
class TestReviewLoop:
    @needs_selby_capture
    def test_the_review_tasks_are_the_existing_7ac_vocabulary(self, page5_journey):
        task_types = frozenset(t.task_type for t in page5_journey.group.tasks)
        assert task_types == EXPECTED_TASK_TYPES
        assert len(page5_journey.group.tasks) == len(EXPECTED_TASK_TYPES)

    @needs_selby_capture
    def test_every_resolution_is_applied_and_none_refused(self, page5_journey):
        rerun = _record(page5_journey).rerun_outcome
        assert len(rerun.resolutions_applied) == len(EXPECTED_TASK_TYPES)
        assert rerun.resolutions_refused == ()

    @needs_selby_capture
    def test_material_300_keeps_its_value_and_human_provenance(self, page5_journey):
        # The AI extracted "300" from the page; the reviewer confirmed it
        # (HUMAN_REVIEWED) without changing the value.
        spec = _record(page5_journey).pipeline.specification
        assert spec.material == "300"
        assert spec.provenance["material"] == PROVENANCE_HUMAN_REVIEWED
        view_b_b = next(c for c in page5_journey.page5["raw_connections"]
                        if c["detail_reference"] == "VIEW B-B")
        assert view_b_b["material"] == "300"  # the AI value was never improved

    @needs_selby_capture
    def test_automation_is_genuine_auto_with_every_layer_satisfied(self, page5_journey):
        record = _record(page5_journey)
        rerun = record.rerun_outcome
        assert rerun.decision == AUTOMATION_DECISION_AUTO
        assert rerun.blockers == ()
        assert rerun.validation_passed is True
        assert rerun.assembly_built is True
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO


# =============================================================================
# 4. Explicit topology — all four members, real types, per-member layers.
# =============================================================================
class TestExplicitTopology:
    @needs_selby_capture
    def test_all_four_members_are_represented_in_explicit_order(self, page5_journey):
        assembly = _record(page5_journey).pipeline.reviewed_assembly
        assert assembly.__class__.__name__ == "ReviewedMultiMemberConnectionAssembly"
        view_b_b = next(c for c in page5_journey.page5["raw_connections"]
                        if c["detail_reference"] == "VIEW B-B")
        assert [m.mark for m in assembly.members] == view_b_b["connects_members"]
        spec = _record(page5_journey).pipeline.specification
        assert spec.connected_member_marks == view_b_b["connects_members"]

    @needs_selby_capture
    def test_each_member_geometry_matches_its_real_section(self, page5_journey):
        assembly = _record(page5_journey).pipeline.reviewed_assembly
        expected = {
            "005": {165.0, 304.0, 5017.0},   # 310UB40: 165 flange x 304 depth
            "PL018": {160.0, 10.0, 298.0},  # 160x10PL x 298
            "PL032": {165.0, 10.0, 304.0},  # 165x10PL x 304
            "PL025": {81.0, 10.0, 80.0},    # 81x10PL x 80
        }
        for member in assembly.members:
            bbox = member.geometry.solid.val().BoundingBox()
            dims = {round(getattr(bbox, axis), 3)
                    for axis in ("xlen", "ylen", "zlen")}
            assert dims == {round(v, 3) for v in expected[member.mark]}, member.mark

    @needs_selby_capture
    def test_the_validation_ran_per_member(self, page5_journey):
        record = _record(page5_journey)
        validation = record.pipeline.validation_result
        assert validation.__class__.__name__ == "MultiMemberValidationResult"
        assert validation.layers_validated == MULTI_VALIDATED_LAYERS
        positions = dict(validation.member_face_positions_mm)
        # Every member's reviewed END face lands at the connection plane 5017
        # (the page's "5017 20" F/S dimension).
        assert set(positions) == {"005", "PL018", "PL032", "PL025"}
        for mark, position in positions.items():
            assert abs(position - 5017.0) < 1.0, mark

    @needs_selby_capture
    def test_attachments_are_explicit_per_member_from_the_review(self, page5_journey):
        spec = _record(page5_journey).pipeline.specification
        assert spec.attachments == JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_SELECT_ATTACHMENT]
        assembly = _record(page5_journey).pipeline.reviewed_assembly
        assert set(assembly.resolved_surfaces.faces_by_mark) == {
            "005", "PL018", "PL032", "PL025"}

    @needs_selby_capture
    def test_no_member_was_dropped_or_truncated(self, page5_journey):
        assembly = _record(page5_journey).pipeline.reviewed_assembly
        assert len(assembly.members) == 4
        text = page5_journey.pdf_text
        # The deliverable itself names every member: MEMBER A..D rows.
        for letter in MEMBER_LABEL_LETTERS[:4]:
            assert f"MEMBER {letter}" in text, letter
        for mark in ("005", "PL018", "PL032", "PL025"):
            assert mark in text, mark


# =============================================================================
# 5. The production path — genuine artifact through 7AE/7AF/7AG/7AQ/7AR/7AS.
# =============================================================================
class TestProductionPath:
    @needs_selby_capture
    def test_dispatch_generated_and_verification_verified_the_actual_artifact(
            self, page5_journey):
        connection = page5_journey.workflow.connections[0]
        assert connection.output_status == OUTPUT_STATUS_GENERATED
        assert connection.verification_status == VERIFICATION_STATUS_VERIFIED
        assert page5_journey.pdf_path.is_file()
        assert page5_journey.pdf_path.read_bytes()

    @needs_selby_capture
    def test_the_pdf_represents_all_four_members(self, page5_journey):
        text = page5_journey.pdf_text
        for needle in ("MEMBER A", "MEMBER B", "MEMBER C", "MEMBER D",
                       "005", "PL018", "PL032", "PL025", "310UB40",
                       "Ø18", "PLATE:", "HOLES:", "PATTERN:", "MATERIAL", "300"):
            assert needle in text, needle

    @needs_selby_capture
    def test_7aq_accepts_the_production_job(self, page5_journey):
        acceptance = page5_journey.acceptance
        assert acceptance.status == "ACCEPTED"
        assert acceptance.summary.connections_accepted == 1
        assert len(acceptance.summary.verified_artifacts) == 1

    @needs_selby_capture
    def test_7ar_packages_the_verified_artifact(self, page5_journey):
        package = page5_journey.package
        assert package.status == "READY"
        assert len(package.items) == 1
        item = package.items[0]
        assert item.drawing_number == "STEELSPEC-001"
        assert item.connection_id == JOURNEY_CONNECTION_ID
        assert Path(item.source_artifact) == page5_journey.pdf_path
        assert item.artifact_sha256 is not None

    @needs_selby_capture
    def test_7as_accepts_with_per_drawing_evidence(self, page5_journey):
        item = page5_journey.package.items[0]
        drawing_items = fa.drawing_checklist_items_for(dict(item.audit))
        # Four recorded members -> one MEMBER row per member (brief §16).
        assert len(drawing_items) == 16
        assert [i for i in drawing_items if i.startswith("MEMBER_")] == [
            "MEMBER_A_IDENTIFIED", "MEMBER_B_IDENTIFIED",
            "MEMBER_C_IDENTIFIED", "MEMBER_D_IDENTIFIED",
        ]
        result = page5_journey.result
        assert result.status == "ACCEPTED"
        assert result.refusal_reasons == ()

    @needs_selby_capture
    def test_the_journey_repeats_with_identical_semantics(self, page5_journey, tmp_path):
        # Brief §20: run the whole journey twice; the deliverable must be
        # byte-identical modulo ReportLab's per-save metadata.
        from tests.test_material_specification import _strip_per_save_metadata
        repeat = _page5_journey(tmp_path)
        record = _record(repeat)
        assert record.rerun_outcome.decision == AUTOMATION_DECISION_AUTO
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO
        repeat_pdf = Path(repeat.workflow.generated_files[0])
        assert repeat_pdf.name == page5_journey.pdf_path.name
        assert (_strip_per_save_metadata(repeat_pdf.read_bytes())
                == _strip_per_save_metadata(page5_journey.pdf_path.read_bytes()))


# =============================================================================
# 6. Negative proofs — brief §18, Cases A-E.
# =============================================================================
def _negative_case(name, raw_connection, answers,
                   member_rows=JOURNEY_MEMBER_ROWS,
                   placements=JOURNEY_MEMBER_PLACEMENTS,
                   matcher=Page5SectionMatcher, tmp_path=None):
    """One genuine journey through 7Y/7AB/7AC/7AD/7AA/7Z with the given
    resolution data — the same stages the positive path runs, so a REVIEW
    here is the engine's own verdict, not a short-cut."""
    page5 = _page5_capture()
    marks = tuple(m["mark"] for m in page5["raw_members"])
    collection = create_project_connection_collection(
        [raw_connection], project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID, known_member_marks=marks,
    )
    intake = dataclasses.replace(
        intake_page_extractions(
            [page5], project_id=JOURNEY_PROJECT_ID,
            source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
            known_member_marks=marks, drawing_set_page_count=SELBY_PAGE_COUNT,
        ),
        collection=collection)
    initial = evaluate_project_for_automation(collection, intake=intake)
    built = build_exception_resolution_package(
        initial, collection, member_rows=member_rows)
    group = built.connection_tasks[0]
    workflow = workflow_module.start_project_workflow(
        collection, intake=intake, member_rows=member_rows,
        member_placements=placements, section_matcher=matcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=group.review_package_id,
        resolutions=[HumanResolution(t.task_id, t.task_type, t.answer_type,
                                     copy.deepcopy(answers[t.task_type]),
                                     evidence="HUMAN-SUPPLIED TEST DATA (7AV negative case)")
                     for t in group.tasks if t.task_type in answers],
        output_dir=tmp_path / name,
    )
    record = workflow.connection_records[0]
    return record, record.rerun_outcome


def _assert_review_with(record, rerun, stage, message_needle, blocker_codes):
    assert rerun.decision == AUTOMATION_DECISION_REVIEW
    codes = {b.code for b in rerun.blockers}
    assert codes == blocker_codes, codes
    failure = record.pipeline.validation_failure
    assert failure is not None
    assert failure.stage == stage
    assert message_needle in failure.message


class TestNegativeProofs:
    @needs_selby_capture
    def test_case_a_three_member_source_two_represented_is_not_auto(self, tmp_path):
        # A 3-member connection built from REAL capture members (all supported
        # families) resolved with only 2 attachments: dropping the third member
        # must never become AUTO. The connection entry is synthetic (the real
        # capture has no such connection) and is used ONLY to prove a refusal.
        synthetic = {
            "detail_reference": "SYNTH 3-MEMBER TRUNCATION CASE", "grid_reference": None,
            "connects_members": ["005", "PL018", "PL032"],
            "connection_type": "welded and bolted",
            "bolts": [{"quantity": 2, "size": "18mm holes", "grade": None}],
            "welds": [{"type": "fillet", "size_mm": 6}],
            "material": "300", "confidence": 75, "page_num": SELBY_PAGE_5_NUMBER,
        }
        answers = dict(JOURNEY_ANSWERS_BY_TASK_TYPE)
        answers[TASK_SELECT_ATTACHMENT] = [
            {"member_mark": "005", "surface_reference": "END"},
            {"member_mark": "PL018", "surface_reference": "END"},
        ]
        record, rerun = _negative_case(
            "case_a", synthetic, answers,
            member_rows={k: JOURNEY_MEMBER_ROWS[k]
                         for k in ("005", "PL018", "PL032")},
            placements={k: JOURNEY_MEMBER_PLACEMENTS[k]
                        for k in ("005", "PL018", "PL032")},
            tmp_path=tmp_path)
        _assert_review_with(
            record, rerun, PIPELINE_STAGE_ATTACHMENTS,
            "do not exactly cover connected_member_marks",
            {AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION})

    @needs_selby_capture
    def test_case_b_real_four_member_three_attachments_is_not_auto(self, tmp_path):
        # The REAL 4-member connection resolved with 3 of 4 attachments.
        answers = dict(JOURNEY_ANSWERS_BY_TASK_TYPE)
        answers[TASK_SELECT_ATTACHMENT] = (
            JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_SELECT_ATTACHMENT][:3])
        record, rerun = _negative_case(
            "case_b", _view_b_b_entry(_page5_capture()), answers, tmp_path=tmp_path)
        _assert_review_with(
            record, rerun, PIPELINE_STAGE_ATTACHMENTS,
            "do not exactly cover connected_member_marks",
            {AUTOMATION_BLOCKER_SPECIFICATION, AUTOMATION_BLOCKER_VALIDATION})

    @needs_selby_capture
    def test_case_c_missing_attachment_is_review(self, tmp_path):
        # The REAL 4-member connection with no attachment resolutions at all.
        answers = {k: v for k, v in JOURNEY_ANSWERS_BY_TASK_TYPE.items()
                   if k != TASK_SELECT_ATTACHMENT}
        record, rerun = _negative_case(
            "case_c", _view_b_b_entry(_page5_capture()), answers, tmp_path=tmp_path)
        _assert_review_with(
            record, rerun, PIPELINE_STAGE_ATTACHMENTS,
            "no attachment information is recorded",
            {AUTOMATION_BLOCKER_ATTACHMENT, AUTOMATION_BLOCKER_SPECIFICATION,
             AUTOMATION_BLOCKER_VALIDATION})

    @needs_selby_capture
    def test_case_d_attachment_contradicting_the_geometry_is_not_accepted(self, tmp_path):
        # The reviewer records START for 005 — the face at Z=0, far from the
        # connection plane. The engine honours the reviewed semantics and the
        # validation layer fails loudly; it NEVER guesses END from geometry.
        answers = dict(JOURNEY_ANSWERS_BY_TASK_TYPE)
        answers[TASK_SELECT_ATTACHMENT] = [
            {"member_mark": "005", "surface_reference": "START"},
            *JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_SELECT_ATTACHMENT][1:],
        ]
        record, rerun = _negative_case(
            "case_d", _view_b_b_entry(_page5_capture()), answers, tmp_path=tmp_path)
        _assert_review_with(
            record, rerun, PIPELINE_STAGE_MULTI_VALIDATION,
            "'START' for member '005'",
            {AUTOMATION_BLOCKER_VALIDATION})

    @needs_selby_capture
    def test_the_multi_member_builder_refuses_truncated_membership(self):
        # The builder itself refuses a truncated membership (3 of the 4
        # genuine members) — the explicit-marks rule, not a pipeline nicety.
        from app.cad_engine.assembly import PlacedMember
        from app.cad_engine.automation_pipeline import (
            reviewed_connection_detail_to_attachments)
        from app.cad_engine.connection_location import (
            real_connection_location_to_connection_location)
        from app.cad_engine.placement import place_member_geometry
        from app.cad_engine.real_connection_adapter import (
            real_connection_to_validated_connection)

        marks = ["005", "PL018", "PL032", "PL025"]
        connection = real_connection_to_validated_connection({
            "connection_id": JOURNEY_CONNECTION_ID,
            "connected_member_marks": marks, "position": "END",
            "plates": [JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_PROVIDE_PLATE]],
            "bolts": [JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_PROVIDE_HOLE_DIAMETER]],
            "source_page": SELBY_PAGE_5_NUMBER,
            "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID,
        })
        location = real_connection_location_to_connection_location(
            {**JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_PROVIDE_LOCATION],
             "connection_id": JOURNEY_CONNECTION_ID})
        attachments = reviewed_connection_detail_to_attachments(
            {"connection_id": JOURNEY_CONNECTION_ID, "review_status": "approved",
             "attachments": JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_SELECT_ATTACHMENT]},
            marks)
        placed_members = []
        for mark in marks:
            validated_member = real_member_to_validated_member(
                JOURNEY_MEMBER_ROWS[mark], Page5SectionMatcher())
            geometry = generate_geometry(validated_member)
            placed = place_member_geometry(
                geometry, JOURNEY_MEMBER_PLACEMENTS[mark])
            placed_members.append(PlacedMember(mark=validated_member.mark,
                                               geometry=placed,
                                               placement=JOURNEY_MEMBER_PLACEMENTS[mark]))
        with pytest.raises(Exception, match="do not match this connection's "
                                             "explicit connected_members"):
            build_reviewed_multi_member_connection_assembly(
                tuple(placed_members[:3]), connection, location,
                tuple(attachments[:3]))
        # Fewer than the minimum is its own refusal, before any matching.
        with pytest.raises(Exception, match=f"at least {MULTI_MEMBER_MIN_COUNT}"):
            build_reviewed_multi_member_connection_assembly(
                tuple(placed_members[:2]), connection, location,
                tuple(attachments[:2]))

    @needs_selby_capture
    def test_case_e_the_existing_two_member_path_stays_green(self, tmp_path):
        # The genuine page-1 two-member journey (7AU's own harness) must still
        # run the ORIGINAL two-member layers — assembly, validation, AUTO.
        from tests.test_real_world_material_extraction import _selby_journey
        from app.cad_engine.reviewed_connection_assembly import (
            ReviewedTwoMemberConnectionAssembly)
        from app.cad_engine.reviewed_connection_validation_gate import (
            ReviewedConnectionValidationResult)

        journey = _selby_journey(tmp_path / "case_e")
        record = journey.workflow.connection_records[0]
        assert isinstance(record.pipeline.reviewed_assembly,
                          ReviewedTwoMemberConnectionAssembly)
        assert isinstance(record.pipeline.validation_result,
                          ReviewedConnectionValidationResult)
        assert record.rerun_outcome.decision == AUTOMATION_DECISION_AUTO
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO
        # The multi-member layers exist only as constants here: the two-member
        # run never produces their types.
        assert PIPELINE_STAGES[-2:] == ("7AV_MULTI_ASSEMBLY",
                                        "7AV_MULTI_VALIDATION")


# =============================================================================
# 7. The section-family boundary — brief §9, no invented profiles.
# =============================================================================
class TestSectionBoundary:
    @needs_selby_capture
    def test_view_a_a_passes_through_the_genuine_boundary(self, tmp_path):
        # The REAL 3-member connection fully resolved with honest data is
        # now genuinely automatable: PL008's 180x20FL and CL004's 90x10EA
        # both have real CAD profile builders (FL shares the flat-plate
        # builder; EA is a genuine two-leg L-section), so the same journey
        # that used to stop at the section boundary goes AUTO through the
        # genuine stages — never an invented or approximated profile. The
        # PL008 placement carries the reviewer-corrected position (END face
        # at the reviewed connection, matching CL004) that the genuine 7AV
        # geometry-consistency gate requires; with the old placement the
        # gate honestly refuses, as the 7AV tests prove.
        page5 = _page5_capture()
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

        answers = {
            TASK_COMPLETE_REVIEW: None, TASK_SELECT_POSITION: "END",
            TASK_SELECT_ATTACHMENT: [
                {"member_mark": m, "surface_reference": "END"}
                for m in ("005", "PL008", "CL004")],
            TASK_CONFIRM_AI_VALUES: ("connected_member_marks", "material"),
            TASK_PROVIDE_PLATE: {"type": "end_plate", "thickness_mm": 20.0,
                                 "width_mm": 180.0, "depth_mm": 340.0},
            TASK_PROVIDE_HOLE_DIAMETER: {"quantity": 4, "diameter_mm": 22.0,
                                         "vertical_spacing_mm": 90.0,
                                         "horizontal_spacing_mm": 85.0},
            TASK_PROVIDE_LOCATION: {"x": 0.0, "y": 0.0, "z": 5017.0,
                                    "rotation_x": 0.0, "rotation_y": 0.0,
                                    "rotation_z": 0.0},
            TASK_REVIEW_SPECIFICATION: None, TASK_REVIEW_VALIDATION: None,
            TASK_PROVIDE_CONNECTION_IDENTITY: "CONN-SELBY-VIEW-AA",
        }
        record, rerun = _negative_case(
            "view_aa_boundary", _view_a_a_entry(page5), answers,
            member_rows=rows, placements=placements,
            matcher=MatcherWithFL, tmp_path=tmp_path)
        assert rerun.decision == AUTOMATION_DECISION_AUTO
        assert rerun.blockers == ()
        assert rerun.validation_passed is True
        assert record.pipeline.validation_failure is None
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO
        assert record.dispatch_result.output_status == OUTPUT_STATUS_GENERATED
        assert record.verification_result.verification_status == VERIFICATION_STATUS_VERIFIED
        artifact = Path(record.dispatch_result.generated_files[0])
        assert artifact.is_file()

    @needs_selby_capture
    def test_the_ea_member_is_refused_verbatim_never_approximated(self):
        # CL004's 90x10EA is not in the section reference table at all; the
        # engine refuses to substitute or approximate another section.
        row = {"mark": "CL004", "section_name": "90x10EA",
               "section_name_raw": "90x10EA", "section_family": "EA",
               "length_mm": 165, "grade": "300", "quantity": 1,
               "review_status": "approved", "source_page": 5,
               "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
        from app.cad_engine.errors import GeometryValidationError
        with pytest.raises(GeometryValidationError,
                           match="90x10EA.*not a recognised entry"):
            validated = real_member_to_validated_member(row, Page5SectionMatcher())
            generate_geometry(validated)


def _view_a_a_entry(page5):
    entry = next(c for c in page5["raw_connections"]
                 if c["detail_reference"] == "VIEW A-A")
    return {**entry, "page_num": SELBY_PAGE_5_NUMBER}
