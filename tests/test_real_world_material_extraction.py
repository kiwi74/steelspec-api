"""
Milestone 7AU — Real-World Material Extraction Proof.

7AT proved the material FIELD end-to-end (a real negative source that states
no grade, and a controlled synthetic positive source). 7AU closes the remaining
gap: a REAL structural PDF whose pages explicitly state a steel grade, run
through the REAL vision extraction, with the real AI-extracted value carried
through review -> automation -> drawing -> acceptance.

WHAT IS REAL AND WHAT IS NOT (read this first):

  REAL SOURCE — the repository's closest real structural fabrication drawing
  set on this machine: FABs.pdf, the "Selby Square" shop drawings by Urban
  Fabrication (32 pages, A3 C1136 001-032). Page 1 states in its notes
  "STEEL GRADE 300 UNO" (grade 300 unless noted otherwise) and its shop
  material list rows grade 300 against assembly 001 (250X90PFC + PL028).
  Every string in the source-evidence tests below is read from the PDF text
  layer — nothing is inferred from member size, filename, convention or
  project history.

  REAL EXTRACTION — scripts/capture_real_pdf_extraction.py runs the genuine
  app.ai_analysis.pdf_vision_analyzer.analyze_pdf_pages() path (page render
  + vision call per page) and writes the raw PageExtraction list to
  tests/data/selby_square_page_extractions.json. The value must originate
  there: nothing in this file or anywhere else may inject, edit or back-fill
  a material value into the capture.

  CAPTURE_HOWTO (needs ANTHROPIC_API_KEY; poppler required; page images are
  saved locally when Supabase is not configured — storage only, never
  extraction):

      ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py \
          "/Users/chad/Downloads/FABs.pdf" tests/data/selby_square_page_extractions.json --max-pages 5

  FULL JOURNEY (PHASE 2, sections 5-6 below): the genuine page-1 capture
  entry (verbatim, never rewritten) travels the existing chain — 7Y intake ->
  7AB/7AC review tasks -> human confirmation (AI_EXTRACTED -> HUMAN_REVIEWED,
  value unchanged) -> 7AD rerun -> 7AA/7Z genuine AUTO -> 7AE gate -> 7AF
  dispatch -> 7AG verification -> 7AQ acceptance -> 7AR package -> 7AS
  acceptance — and the real captured material reaches the real fabrication
  deliverable. These tests are skipped when no capture is present: a skipped
  real proof is a missing proof, not a pass — the milestone verdict depends
  on them.

  NEGATIVE SOURCE — the genuine Arkles Strand capture (7Y) states no material
  for any of its connection candidates; the 7AT negative proof runs unchanged
  alongside this milestone and must not be weakened by the new positive
  source.
"""
import copy
import dataclasses
import functools
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

import app.cad_engine.fabricator_acceptance as fa
import app.cad_engine.project_workflow as workflow_module
from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.connection_review_package import build_review_report
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_VERIFIED
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.exception_resolution import (
    ANSWER_CONFIRMED_FIELDS,
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
from app.cad_engine.fabrication_package import PACKAGE_STATUS_READY, build_fabrication_package
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_acceptance import ACCEPTANCE_STATUS_ACCEPTED, accept_production_job
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
)
from app.cad_engine.sections import PLATE_REQUIRED_FIELDS, PROFILE_BUILDERS, build_plate_profile
from tests.test_exception_resolution import _tasks
from tests.test_fabricator_acceptance import MATERIAL_FINDING, _answers
from tests.test_material_specification import (
    _q12_of,
    _strip_per_save_metadata,
    negative_journey,
)
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_project_workflow import _pdf_text, _sha256
from tests.test_real_member_adapter import FakeSectionMatcher, make_real_member_row

REPO = Path(__file__).resolve().parent.parent
REAL_PDF = Path("/Users/chad/Downloads/FABs.pdf")
CAPTURE_PATH = REPO / "tests" / "data" / "selby_square_page_extractions.json"

# Source evidence — verbatim from the page-1 text layer of the real drawing
# (read from the PDF, never inferred from context or filename).
SELBY_PROJECT = "Selby Square"
SELBY_FABRICATOR = "Urban Fabrication"
# The title-block drawing number is A3 C1136 001 (the per-sheet suffix sits in
# a rotated title block, so the text layer yields "A3" and "C1136" as separate
# tokens); the material-list assembly marks pin each sheet's identity.
SELBY_DRAWING_NO_PREFIX = "A3 C1136"
SELBY_MATERIAL_NOTE = "STEEL GRADE 300 UNO"          # general note on page 1
SELBY_WELDS_NOTE = "ALL WELDS 6 mm FWAR UNO"         # general note on page 1
SELBY_MATERIAL_LIST_HEADER = "SHOP MATERIAL LIST FOR 1 ASSEMBLY"
SELBY_PAGE_1_MEMBER = "250X90PFC"                    # assembly 001's member
SELBY_PAGE_1_PLATE = "PL028"                         # plate in the same list
SELBY_PAGE_COUNT = 32
SELBY_CAPTURE_PAGES = 5                              # pages 1-5 analysed at capture

needs_real_pdf = pytest.mark.skipif(
    not REAL_PDF.exists(),
    reason=f"the real Selby Square fabrication PDF is not present at {REAL_PDF}",
)
needs_selby_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason=f"no captured real AI extraction exists at {CAPTURE_PATH.relative_to(REPO)}; "
           "produce one with scripts/capture_real_pdf_extraction.py (see CAPTURE_HOWTO above).",
)

_CAPTURE_KEYS = {"page_number", "drawing_number", "drawing_title", "revision",
                 "raw_members", "raw_connections", "parse_failed"}


@functools.lru_cache(maxsize=None)
def _page_text(page_number: int) -> str:
    """The real PDF's text layer for one (1-based) page — the source evidence."""
    return PdfReader(REAL_PDF).pages[page_number - 1].extract_text() or ""


def _collapsed(text: str) -> str:
    return " ".join(text.split())


# =============================================================================
# 1. Real source evidence — every string read from the PDF text layer.
# =============================================================================
class TestRealSourceEvidence:
    @needs_real_pdf
    def test_real_pdf_is_the_selby_square_fabrication_set(self):
        assert len(PdfReader(REAL_PDF).pages) == SELBY_PAGE_COUNT
        page1 = _collapsed(_page_text(1))
        for needle in (SELBY_PROJECT, SELBY_FABRICATOR, "A3", "C1136", "DRAWING NO"):
            assert needle in page1, f"page 1 text layer must state {needle!r}"
        # assembly marks pin each sheet's identity (page 2 and 3 of the set)
        assert "002" in _collapsed(_page_text(2))
        assert "003" in _collapsed(_page_text(3))

    @needs_real_pdf
    def test_page_1_states_material_explicitly(self):
        page1 = _collapsed(_page_text(1))
        assert SELBY_MATERIAL_NOTE in page1
        assert SELBY_WELDS_NOTE in page1
        assert SELBY_MATERIAL_LIST_HEADER in page1
        assert "GRADE" in page1

    @needs_real_pdf
    def test_page_1_material_list_rows_grade_300_against_the_assembly(self):
        page1 = _collapsed(_page_text(1))
        assert SELBY_PAGE_1_MEMBER in page1 and SELBY_PAGE_1_PLATE in page1
        assert "300" in page1  # the grade column value on the same page

    @needs_real_pdf
    def test_material_evidence_is_on_the_page_never_from_the_filename(self):
        assert "300" not in REAL_PDF.name
        assert "GRADE" not in REAL_PDF.name.upper()


# =============================================================================
# 2. The capture itself — integrity and source traceability.
# =============================================================================
class TestRealCaptureIntegrity:
    @needs_selby_capture
    def test_capture_is_a_real_page_extraction_list(self):
        data = json.loads(CAPTURE_PATH.read_text())
        assert isinstance(data, list) and len(data) == SELBY_CAPTURE_PAGES
        for page in data:
            assert set(page) == _CAPTURE_KEYS, page.keys()
        assert [p["page_number"] for p in data] == list(range(1, len(data) + 1))
        assert data[0]["parse_failed"] is False

    @needs_selby_capture
    def test_captured_material_traces_back_to_the_source_page_text(self):
        # Every material value the vision reported must appear verbatim on the
        # very page it read it from: no downstream value may exist that the
        # source page does not state (a hallucinated grade fails here).
        data = json.loads(CAPTURE_PATH.read_text())
        checked = 0
        for page in data:
            if page["parse_failed"] or not page["raw_connections"]:
                continue
            source = _collapsed(_page_text(page["page_number"])).upper()
            for conn in page["raw_connections"]:
                material = conn.get("material")
                if material is None:
                    continue
                assert isinstance(material, str) and material.strip()
                checked += 1
                assert material.upper() in source, (
                    f"page {page['page_number']} material {material!r} is not stated "
                    "by the source page text layer"
                )
        assert checked >= 1, "the capture must carry at least one material value to prove 7AU"


# =============================================================================
# 3. The vision extraction layer itself (imported under the same deliberately
#    fake-but-JWT-shaped configuration boundary as 7AO, with sys.modules
#    teardown so the pre-existing SUPABASE_URL smoke failures stay honest).
# =============================================================================
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


class TestVisionExtractionLayer:
    def test_prompt_requires_material_only_when_explicitly_stated(self, vision_module):
        prompt = vision_module.EXTRACTION_SYSTEM_PROMPT
        assert '"material": "300PLUS" or null' in prompt
        assert "never normalise one designation into another" in prompt
        assert "Never infer a grade from" in prompt

    def test_page_extraction_schema_carries_raw_connection_data(self, vision_module):
        fields = {f.name for f in dataclasses.fields(vision_module.PageExtraction)}
        assert {"page_number", "raw_members", "raw_connections", "parse_failed"} <= fields


# =============================================================================
# 4. The negative source stands exactly as 7AT proved it — the new real
#    positive source must not weaken the no-material behaviour.
# =============================================================================
class TestNegativeProofUnaffected:
    @needs_real_capture  # the genuine Arkles capture marker (7Y)
    def test_arkles_negative_proof_stands_alongside_the_7au_positive_source(
        self, negative_journey
    ):
        result = negative_journey.result
        assert result.status == fa.ACCEPTANCE_STATUS_NOT_ACCEPTED
        q12 = _q12_of(result)
        assert q12.answer == fa.ANSWER_FAIL
        assert q12.finding == MATERIAL_FINDING


# =============================================================================
# 5. Phase 2 — the full journey: the REAL captured material through review ->
#    automation -> drawing -> acceptance (genuine stages only).
#
# Scope (deliberate, documented): the journey intakes the genuine PAGE-1
# capture entry VERBATIM — one connection: "001" 250X90PFC + "PL028"
# 250X12FL, welded, material "300". The whole 5-page capture's pages 4-5
# hold 3- and 4-member connections that exceed the existing two-member
# assembly contract (7K/7L/7O), and 7AQ's ACCEPTED contract requires EVERY
# connection accepted — a partially-resolved project is NOT_ACCEPTED. One
# fully traced real connection is the core positive proof; pages 2-4's
# two-member candidates can be added without changing anything else.
#
# WHAT IS FIXTURE DATA AND WHAT IS NOT:
#   - the capture entry, its marks, lengths, connection, weld and material
#     are the REAL vision extraction, used verbatim and never rewritten;
#   - the section NAMES (250X90PFC / 250X12FL) are read from the page-1 text
#     layer (pinned by TestRealSourceEvidence above — the capture's own
#     raw_members do not carry section_name);
#   - the controlled geometry (placements, attachments, connection plate,
#     hole pattern, connection location, connection id) is this test's
#     reviewer data — the same convention as the 7AI/7AJ/7AT fixtures. The
#     connection plate uses the REAL flat's dimensions (250 x 155 x 12); the
#     bolt holes are test data (the real connection is welded, with no bolts).
#
# 7A_MEMBER BOUNDARY (the one engine fix this milestone required): every real
# Selby connection involves at least one flat-plate member, and the CAD
# engine had no flat-plate profile family — "Member 'PL028' cannot generate
# CAD geometry: section '250X12FL' has family 'PL', which has no supported
# CAD profile builder yet" was the genuine 7AD rerun failure after every
# other blocker cleared. The fix is app.cad_engine.sections.build_plate_profile
# (pinned in section 6): a solid [0,width]x[0,thickness] rectangle in the
# same corner-origin convention as the PFC/UB builders. Nothing else was
# weakened: the material value still had to be confirmed by a human, and the
# automation/gate/dispatch stages are the existing ones unchanged.
# =============================================================================
SELBY_JOURNEY_PROJECT_ID = "PROJ-7AU-SELBY"
SELBY_JOURNEY_SOURCE_DRAWING_ID = "SELBY-C1136"
SELBY_MATERIAL = "300"
SELBY_PACKAGE_ID = "RP-0001"
SELBY_CONNECTION_ID = "CONN-SELBY-001"
SELBY_PLATE_SECTION = "250X12FL"

# The reviewer fixture: marks/sections/lengths are the capture's own readings
# (sections from the page-1 text layer); the geometry answers are this test's
# data. 7AI/7AJ established the 6mm half-embedding convention for end plates.
SELBY_MEMBER_ROWS = {
    "001": {"mark": "001", "section_name": "250X90PFC", "section_name_raw": "250X90PFC",
            "section_family": "PFC", "length_mm": 2613, "grade": "300", "quantity": 1,
            "review_status": "approved", "source_page": 1,
            "source_drawing_id": SELBY_JOURNEY_SOURCE_DRAWING_ID},
    "PL028": {"mark": "PL028", "section_name": SELBY_PLATE_SECTION,
              "section_name_raw": SELBY_PLATE_SECTION, "section_family": "PL",
              "length_mm": 155, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 1,
              "source_drawing_id": SELBY_JOURNEY_SOURCE_DRAWING_ID},
}


class SelbySectionMatcher:
    SECTIONS = {
        "250X90PFC": {"name": "250X90PFC", "family": "PFC", "depth": 250.0,
                      "flange_width": 90.0, "flange_thickness": 15.0,
                      "web_thickness": 8.0, "weight_per_metre": 35.5},
        SELBY_PLATE_SECTION: {"name": SELBY_PLATE_SECTION, "family": "PL",
                              "width": 250.0, "thickness": 12.0,
                              "weight_per_metre": 23.6},
    }

    def match(self, raw_name):
        return self.SECTIONS.get(raw_name.strip().upper())


SELBY_MEMBER_PLACEMENTS = {
    "001": MemberPlacement(x=0.0, y=0.0, z=0.0,
                           rotation_x=0.0, rotation_y=0.0, rotation_z=0.0),
    "PL028": MemberPlacement(x=0.0, y=0.0, z=2613.0,
                             rotation_x=0.0, rotation_y=0.0, rotation_z=0.0),
}

SELBY_ANSWERS_BY_TASK_TYPE = {
    TASK_COMPLETE_REVIEW: None,
    TASK_SELECT_POSITION: "END",
    TASK_SELECT_ATTACHMENT: [
        {"member_mark": "001", "surface_reference": "END"},
        {"member_mark": "PL028", "surface_reference": "START"},
    ],
    TASK_CONFIRM_AI_VALUES: ("connected_member_marks", "material"),
    TASK_PROVIDE_PLATE: {"type": "end_plate", "thickness_mm": 12.0,
                         "width_mm": 250.0, "depth_mm": 155.0},
    # Holes that genuinely fit the 250 x 155 plate (every circle fully inside
    # it): the real connection is welded and has none, so this is test data.
    TASK_PROVIDE_HOLE_DIAMETER: {"quantity": 4, "diameter_mm": 22.0,
                                 "vertical_spacing_mm": 100.0, "horizontal_spacing_mm": 90.0},
    TASK_PROVIDE_LOCATION: {"x": 0.0, "y": 0.0, "z": 2607.0,
                            "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
    TASK_REVIEW_SPECIFICATION: None,
    TASK_REVIEW_VALIDATION: None,
    TASK_PROVIDE_CONNECTION_IDENTITY: SELBY_CONNECTION_ID,
}


def _selby_reviewer_resolutions(group):
    """One HumanResolution per task the ENGINE demanded — the review answers
    above, never an injected decision, never a fabricated blocker clearance."""
    resolutions = []
    for task in group.tasks:
        resolutions.append(HumanResolution(
            task.task_id, task.task_type, task.answer_type,
            copy.deepcopy(SELBY_ANSWERS_BY_TASK_TYPE[task.task_type]),
            evidence="HUMAN-SUPPLIED TEST DATA (Selby reviewer fixture)",
        ))
    return resolutions


def _conn(acceptance, package_id):
    return next(c for c in acceptance.connections if c.package_id == package_id)


def _selby_journey(output_dir):
    """The genuine page-1 journey: real capture entry (verbatim) -> 7Y intake
    -> 7AB -> 7AC tasks -> human resolutions -> 7AD rerun -> 7AA/7Z -> 7AE ->
    7AF -> 7AG. Returns the pieces the tests assert on."""
    data = json.loads(CAPTURE_PATH.read_text())
    page1 = copy.deepcopy(data[0])
    marks = tuple(m["mark"] for m in page1["raw_members"])
    intake = intake_page_extractions(
        [page1],
        project_id=SELBY_JOURNEY_PROJECT_ID,
        source_drawing_id=SELBY_JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=marks,
        drawing_set_page_count=SELBY_PAGE_COUNT,
    )
    initial = evaluate_project_for_automation(intake.collection, intake=intake)
    built = build_exception_resolution_package(
        initial, intake.collection, member_rows=SELBY_MEMBER_ROWS)
    group = next(g for g in built.connection_tasks
                 if g.review_package_id == SELBY_PACKAGE_ID)
    workflow = workflow_module.start_project_workflow(
        intake.collection, intake=intake,
        member_rows=SELBY_MEMBER_ROWS,
        member_placements=SELBY_MEMBER_PLACEMENTS,
        section_matcher=SelbySectionMatcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=SELBY_PACKAGE_ID,
        resolutions=_selby_reviewer_resolutions(group), output_dir=output_dir,
    )
    return SimpleNamespace(page1=page1, marks=marks, intake=intake,
                           initial=initial, built=built, group=group,
                           workflow=workflow)


@pytest.fixture(scope="module")
def selby_journey(tmp_path_factory):
    """The completed real positive path, through 7AS: the actual generated
    PDF is the acceptance evidence, read back from the package records."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7au"))
    journey = _selby_journey(tmp)
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_answers(package, q12=fa.ANSWER_PASS))
    pdf_path = Path(journey.workflow.generated_files[0])
    return SimpleNamespace(
        tmp=tmp, page1=journey.page1, marks=journey.marks,
        intake=journey.intake, initial=journey.initial, built=journey.built,
        group=journey.group, workflow=journey.workflow,
        acceptance=acceptance, package=package, result=result,
        pdf_path=pdf_path, pdf_text=_pdf_text(pdf_path),
    )


@needs_selby_capture
class TestRealMaterialJourney:
    """The 7AU positive proof: the REAL captured material travels the genuine
    chain and 7AS accepts the actual deliverable."""

    def test_the_real_page_1_connection_carries_material_300(self, selby_journey):
        page1 = json.loads(CAPTURE_PATH.read_text())[0]
        conn = page1["raw_connections"][0]
        assert conn["material"] == SELBY_MATERIAL      # the exact extracted value
        assert conn["material"] != "300PLUS"           # never a designation swap
        assert conn["connects_members"] == ["001", "PL028"]
        assert selby_journey.page1 == page1            # the journey consumed it verbatim

    def test_the_journey_candidate_carries_the_captured_value(self, selby_journey):
        # Exactly one candidate; its AI observation is the capture's own value.
        assert len(selby_journey.intake.collection.candidates) == 1
        candidate = selby_journey.intake.collection.candidates[0]
        assert candidate.review_package_id == SELBY_PACKAGE_ID
        report = build_review_report(candidate.package)
        assert "material" in report.found
        entry = next(e for e in report.entries if e.field == "material")
        assert entry.ai_value == SELBY_MATERIAL

    def test_initial_automation_is_review_never_forced_auto(self, selby_journey):
        initial = selby_journey.initial
        assert initial.auto_count == 0
        outcome = next(o for o in initial.connection_results
                       if o.review_package_id == SELBY_PACKAGE_ID)
        assert outcome.decision == AUTOMATION_DECISION_REVIEW
        assert outcome.blockers  # genuine unresolved fields existed

    def test_provenance_begins_ai_extracted_and_awaits_confirmation(self, selby_journey):
        candidate = selby_journey.intake.collection.candidates[0]
        report = build_review_report(candidate.package)
        assert "material" in report.needs_confirmation
        assert report.provenance["material"] == PROVENANCE_AI_EXTRACTED
        confirm = _tasks(selby_journey.group)[TASK_CONFIRM_AI_VALUES]
        assert confirm.answer_type == ANSWER_CONFIRMED_FIELDS
        # both pending AI fields await confirmation; the material value is the
        # capture's own, verbatim.
        assert dict(confirm.current_ai_value)["material"] == SELBY_MATERIAL
        assert "connected_member_marks" in dict(confirm.current_ai_value)

    def test_human_confirmation_yields_human_reviewed_with_value_unchanged(
            self, selby_journey):
        conn = _conn(selby_journey.acceptance, SELBY_PACKAGE_ID)
        assert conn.trace.contract.ai_material == SELBY_MATERIAL
        provenance = {e.field: e.provenance for e in conn.trace.contract.provenance}
        assert provenance["material"] == PROVENANCE_HUMAN_REVIEWED
        confirms = [d for d in conn.trace.human_decisions
                    if d.task_type == TASK_CONFIRM_AI_VALUES and d.applied]
        assert len(confirms) == 1
        assert confirms[0].refusal_reason is None
        assert "material" in confirms[0].answer        # the confirmed field
        assert "material" in confirms[0].fields        # and its trace

    def test_automation_is_genuine_auto_with_every_gate_satisfied(self, selby_journey):
        # The reviewer never injected a decision: the validation/specification
        # answers were clear-review (None), so 7Z's AUTO is the engine's own.
        assert SELBY_ANSWERS_BY_TASK_TYPE[TASK_REVIEW_VALIDATION] is None
        assert SELBY_ANSWERS_BY_TASK_TYPE[TASK_REVIEW_SPECIFICATION] is None
        workflow = selby_journey.workflow
        assert workflow.revision == 1
        record = workflow.connection_records[0]
        rerun = record.rerun_outcome
        assert rerun.decision == AUTOMATION_DECISION_AUTO
        assert rerun.blockers == ()
        assert rerun.remaining_task_ids == ()
        assert rerun.validation_passed is True
        assert rerun.assembly_built is True
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO
        assert any("material" in line and SELBY_MATERIAL in line
                   and "HUMAN_REVIEWED" in line
                   for line in record.gate_result.evidence_summary)

    def test_workflow_completes_and_7ag_verifies_the_actual_artifact(
            self, selby_journey):
        workflow = selby_journey.workflow
        connection = workflow.connections[0]
        assert connection.decision == AUTOMATION_DECISION_AUTO
        assert connection.output_status == OUTPUT_STATUS_GENERATED
        assert connection.verification_status == VERIFICATION_STATUS_VERIFIED
        assert len(connection.generated_files) == 1
        verification = workflow.connection_records[0].verification_result
        assert verification.verification_status == VERIFICATION_STATUS_VERIFIED
        assert Path(verification.artifact_path) == selby_journey.pdf_path
        assert verification.sha256 == _sha256(selby_journey.pdf_path)

    def test_the_generated_pdf_carries_the_real_material(self, selby_journey):
        text = selby_journey.pdf_text
        assert "MATERIAL" in text
        assert SELBY_MATERIAL in text
        assert "NOT SPECIFIED" not in text
        assert "300PLUS" not in text

    def test_no_300plus_anywhere_unless_the_source_states_it(self, selby_journey):
        source_page = _collapsed(_page_text(1)).upper()
        assert "300PLUS" not in source_page     # the source never states it, so...
        assert "300PLUS" not in json.dumps(selby_journey.page1).upper()  # the capture must not,
        assert "300PLUS" not in selby_journey.pdf_text.upper()          # nor the drawing.

    def test_7aq_accepts_the_production_job(self, selby_journey):
        acceptance = selby_journey.acceptance
        assert acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        assert acceptance.summary.connections_total == 1
        assert acceptance.summary.connections_accepted == 1
        assert acceptance.accepted_package_ids == (SELBY_PACKAGE_ID,)
        assert any(Path(a).name == selby_journey.pdf_path.name
                   for a in acceptance.verified_artifacts)

    def test_7ar_packages_the_verified_artifact_with_the_material(self, selby_journey):
        package = selby_journey.package
        assert package.status == PACKAGE_STATUS_READY
        item = package.items[0]
        assert item.drawing_number == "STEELSPEC-001"
        audit = dict(item.audit)
        assert audit["ai_observations"]["material"] == SELBY_MATERIAL
        assert ["material", PROVENANCE_HUMAN_REVIEWED] in audit["field_provenance"]
        # The packaged bytes ARE the generated artifact's bytes.
        assert Path(item.source_artifact) == selby_journey.pdf_path
        assert item.artifact_sha256 == _sha256(selby_journey.pdf_path)
        packaged_pdf = Path(package.manifest_path).parent / "drawings" / item.filename
        assert packaged_pdf.exists()
        assert _pdf_text(packaged_pdf) == selby_journey.pdf_text
        assert (Path(package.manifest_path).parent / "project-manifest.json").exists()

    def test_7as_accepts_with_evidence_from_the_actual_pdf(self, selby_journey):
        result = selby_journey.result
        assert result.status == fa.ACCEPTANCE_STATUS_ACCEPTED
        assert result.refusal_reasons == ()
        assert result.summary.total_drawings == 1
        assert result.summary.accepted_drawings == 1
        # no fabrication blockers (MILESTONE 7AW: the drawing now prints its
        # reviewed location/attachments, so no findings remain either — see
        # test_7as_independently_recognizes_the_drawings_reviewed_location)
        assert result.summary.fabrication_blockers == 0
        q12 = _q12_of(result)
        assert q12.answer == fa.ANSWER_PASS and q12.finding is None

    def test_7as_independently_recognizes_the_drawings_reviewed_location(
            self, selby_journey):
        # MILESTONE 7AW: the drawing text now STATES where the connection
        # occurs — each member row prints its own reviewed ATTACH semantics
        # (001 -> END, PL028 -> START, straight from the package's recorded
        # decisions) and the title block prints the reviewed CONNECTION
        # LOCATION plus a deterministic PAGE 1 OF 1; no STATUS cell is drawn
        # at all. 7AS reads the actual PDF: the former AMBIGUITY finding and
        # the two NOTE findings are genuinely gone — never hidden, never
        # waived, never upgraded into an approval claim.
        result = selby_journey.result
        lines = selby_journey.pdf_text.splitlines()
        assert "ATTACH" in lines
        assert "END" in lines and "START" in lines
        assert "CONNECTION LOCATION" in lines
        assert "PAGE" in lines and "1 OF 1" in lines
        assert "STATUS" not in lines
        location = {r.checklist_item: r for r in result.drawings[0].results}[
            fa.CHECKLIST_ITEM_CONNECTION_LOCATION]
        assert location.answer == fa.ANSWER_PASS
        assert location.evidence_status == fa.EVIDENCE_PRESENT
        assert location.visible_in_drawing is True
        assert location.evaluator_findings == ()
        assert result.status == fa.ACCEPTANCE_STATUS_ACCEPTED
        assert result.summary.ambiguity_findings == 0
        assert result.summary.fabrication_blockers == 0
        assert result.summary.findings_total == 0
        assert result.drawings[0].findings == ()

    def test_journey_repeats_with_the_same_material_semantics(
            self, selby_journey, tmp_path):
        # Two INDEPENDENT runs of the real positive path: same filename, same
        # drawing text (material "300"), byte-identical apart from reportlab's
        # per-save metadata — the existing 7AT determinism convention.
        second = _selby_journey(tmp_path / "second-run")
        second_pdf = Path(second.workflow.generated_files[0])
        assert second_pdf.name == selby_journey.pdf_path.name
        second_text = _pdf_text(second_pdf)
        assert second_text == selby_journey.pdf_text
        assert SELBY_MATERIAL in second_text and "300PLUS" not in second_text
        assert (_strip_per_save_metadata(second_pdf.read_bytes())
                == _strip_per_save_metadata(selby_journey.pdf_path.read_bytes()))
        acceptance2 = accept_production_job(second.workflow)
        assert acceptance2.status == ACCEPTANCE_STATUS_ACCEPTED
        conn2 = _conn(acceptance2, SELBY_PACKAGE_ID)
        assert conn2.trace.contract.ai_material == SELBY_MATERIAL
        assert {e.field: e.provenance for e in conn2.trace.contract.provenance}["material"] \
            == PROVENANCE_HUMAN_REVIEWED


# =============================================================================
# 6. The PL profile builder — the one engine fix 7AU required (the genuine
#    7A_MEMBER boundary: "family 'PL' has no supported CAD profile builder").
# =============================================================================
PL_SECTION = {"name": SELBY_PLATE_SECTION, "family": "PL",
              "width": 250.0, "thickness": 12.0, "weight_per_metre": 23.6}


class TestPlateProfileBuilder:
    def test_pl_is_a_dispatched_family_with_its_own_columns(self):
        assert PROFILE_BUILDERS["PL"] is build_plate_profile
        assert set(PLATE_REQUIRED_FIELDS) == {"width", "thickness"}

    def test_plate_profile_is_a_solid_rectangle_in_the_corner_origin_convention(self):
        profile = build_plate_profile(PL_SECTION, "PL028")
        solid = profile.extrude(155.0)  # the real flat's length
        bbox = solid.val().BoundingBox()
        assert abs(bbox.xlen - 250.0) < 1e-6     # the plate's real width
        assert abs(bbox.ylen - 12.0) < 1e-6      # the plate's real thickness
        assert abs(bbox.zlen - 155.0) < 1e-6     # the extrude length
        assert len(solid.solids().vals()) == 1   # solid, not hollow

    def test_a_real_plate_member_row_reaches_the_existing_cad_engine(self):
        row = make_real_member_row(mark="PL028", section_name=SELBY_PLATE_SECTION,
                                   section_family="PL", length_mm=155.0)
        validated = real_member_to_validated_member(row, FakeSectionMatcher(
            {SELBY_PLATE_SECTION: PL_SECTION}))
        geometry = generate_geometry(validated)
        assert geometry.section_family == "PL"
        bbox = geometry.solid.val().BoundingBox()
        assert abs(bbox.xlen - 250.0) < 1e-6     # actual width from the matched section
        assert abs(bbox.ylen - 12.0) < 1e-6      # actual thickness
        assert abs(bbox.zlen - 155.0) < 1e-6     # the member length, not a section dim
        assert len(geometry.solid.solids().vals()) == 1

    def test_plate_profile_requires_its_real_columns(self):
        with pytest.raises(GeometryValidationError, match="missing required PL geometry"):
            build_plate_profile({"name": SELBY_PLATE_SECTION}, "PL028")

    def test_geometrically_inconsistent_plate_is_rejected(self):
        with pytest.raises(GeometryValidationError, match="geometrically inconsistent"):
            build_plate_profile({"name": "BAD", "width": 250.0, "thickness": 0.0}, "BAD")
        with pytest.raises(GeometryValidationError, match="geometrically inconsistent"):
            build_plate_profile({"name": "BAD", "width": -1.0, "thickness": 12.0}, "BAD")
