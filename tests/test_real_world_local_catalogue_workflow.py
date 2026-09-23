"""
Milestone C — VERSIONED LOCAL SECTION CATALOGUE: the real workflows.

The three genuine Selby Square journeys — page-1 (250X90PFC +
250X12FL), page-5 VIEW B-B (310UB40 + the three PL plates) and
page-5 VIEW A-A (310UB40 + 180X20FL + 90X10EA) — are re-driven
through the GENUINE stages (7Y intake -> 7AB -> 7AC -> human
resolutions -> 7AD rerun -> 7AA/7Z -> 7AE -> 7AF -> 7AG -> 7AQ ->
7AR) with the local CatalogueMatcher substituted for the fixture
matchers. Nothing is bypassed, injected or re-drawn by a test: every
stage is the existing production module, and the only substitution is
the section matcher the workflow is already parameterised on.

Proven along the way:

  * each journey reaches AUTO -> GENERATED -> VERIFIED -> ACCEPTED ->
    PACKAGED with a real generated PDF;
  * the produced drawing's dispatch manifest and the producing 7AA
    result record the catalogue version — the provenance boundary;
  * substituting the local catalogue for the fixture matcher changes
    NOTHING about the genuine run (same decisions, same stage
    evidence, byte-identical PDF) except the newly recorded
    catalogue_version — so no AI evidence digest is polluted;
  * a dimensionless catalogue row (200UB30.4) inside the genuine
    workflow fails closed at the member stage: REVIEW, no artifact,
    "Refusing to guess" — never a guessed geometry.
"""

import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.cad_engine.project_workflow as workflow_module
from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
)
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.exception_resolution import (
    build_exception_resolution_package,
)
from app.cad_engine.fabrication_package import build_fabrication_package
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    accept_production_job,
)
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_connection_review import (
    create_project_connection_collection,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.engineering_data.section_catalogue import (
    CATALOGUE_VERSION,
    CatalogueMatcher,
)
from tests.test_real_world_material_extraction import (
    SELBY_JOURNEY_PROJECT_ID,
    SELBY_JOURNEY_SOURCE_DRAWING_ID,
    SELBY_MEMBER_PLACEMENTS,
    SELBY_MEMBER_ROWS,
    SELBY_PACKAGE_ID,
    _selby_reviewer_resolutions,
)
from tests.test_real_world_multi_member_connection import (
    CAPTURE_PATH,
    JOURNEY_MEMBER_PLACEMENTS,
    JOURNEY_MEMBER_ROWS,
    JOURNEY_PACKAGE_ID,
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
    Page5SectionMatcher,
    _journey_reviewer_resolutions,
    _page5_capture,
    _page5_journey,
    _view_a_a_entry,
    _view_b_b_entry,
)
from tests.test_real_world_production_coverage import (
    VIEW_AA_ANSWERS_BY_TASK_TYPE,
    VIEW_AA_PACKAGE_ID,
    _resolutions_for,
)


def _page1_journey_catalogue(output_dir):
    """The genuine 7AU page-1 journey, matcher substituted."""
    data = json.loads(CAPTURE_PATH.read_text())
    page1 = copy.deepcopy(data[0])
    marks = tuple(m["mark"] for m in page1["raw_members"])
    intake = intake_page_extractions(
        [page1], project_id=SELBY_JOURNEY_PROJECT_ID,
        source_drawing_id=SELBY_JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=marks, drawing_set_page_count=SELBY_PAGE_COUNT,
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
        section_matcher=CatalogueMatcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=SELBY_PACKAGE_ID,
        resolutions=_selby_reviewer_resolutions(group), output_dir=output_dir,
    )
    return SimpleNamespace(page1=page1, intake=intake, workflow=workflow)


def _bb_journey_catalogue(output_dir):
    """The genuine 7AV page-5 VIEW B-B journey, matcher substituted."""
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
        section_matcher=CatalogueMatcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=JOURNEY_PACKAGE_ID,
        resolutions=_journey_reviewer_resolutions(group), output_dir=output_dir,
    )
    return SimpleNamespace(
        page5=page5, scoped_collection=scoped_collection,
        intake=intake, workflow=workflow,
    )


def _aa_journey_catalogue(output_dir):
    """The genuine page-5 VIEW A-A journey, matcher substituted — the
    same single-candidate scoping the genuine 7AV B-B journey uses
    (create_project_connection_collection over the A-A entry), so the
    production-job acceptance covers exactly this connection."""
    page5 = _page5_capture()
    marks = tuple(m["mark"] for m in page5["raw_members"])
    full_intake = intake_page_extractions(
        [page5], project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID, known_member_marks=marks,
        drawing_set_page_count=SELBY_PAGE_COUNT,
    )
    scoped_collection = create_project_connection_collection(
        [_view_a_a_entry(page5)], project_id=JOURNEY_PROJECT_ID,
        source_drawing_id=JOURNEY_SOURCE_DRAWING_ID, known_member_marks=marks,
    )
    intake = dataclasses.replace(full_intake, collection=scoped_collection)
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
    initial = evaluate_project_for_automation(scoped_collection, intake=intake)
    built = build_exception_resolution_package(
        initial, scoped_collection, member_rows=rows)
    group = next(g for g in built.connection_tasks
                 if g.review_package_id == JOURNEY_PACKAGE_ID)
    workflow = workflow_module.start_project_workflow(
        scoped_collection, intake=intake, member_rows=rows,
        member_placements=placements, section_matcher=CatalogueMatcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=JOURNEY_PACKAGE_ID,
        resolutions=_resolutions_for(
            group, VIEW_AA_ANSWERS_BY_TASK_TYPE,
            "HUMAN-SUPPLIED TEST DATA (local catalogue VIEW A-A)"),
        output_dir=output_dir,
    )
    return SimpleNamespace(workflow=workflow, intake=intake)


@pytest.fixture(scope="module")
def page1_catalogue_journey(tmp_path_factory):
    if not CAPTURE_PATH.exists():
        pytest.skip("the genuine Selby Square captures are required")
    tmp = Path(tmp_path_factory.mktemp("genuine-catalogue-page1"))
    journey = _page1_journey_catalogue(tmp)
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    return SimpleNamespace(
        tmp=tmp, journey=journey, acceptance=acceptance, package=package,
    )


@pytest.fixture(scope="module")
def bb_catalogue_journey(tmp_path_factory):
    if not CAPTURE_PATH.exists():
        pytest.skip("the genuine Selby Square captures are required")
    tmp = Path(tmp_path_factory.mktemp("genuine-catalogue-bb"))
    journey = _bb_journey_catalogue(tmp)
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    return SimpleNamespace(
        tmp=tmp, journey=journey, acceptance=acceptance, package=package,
    )


@pytest.fixture(scope="module")
def aa_catalogue_journey(tmp_path_factory):
    if not CAPTURE_PATH.exists():
        pytest.skip("the genuine Selby Square captures are required")
    tmp = Path(tmp_path_factory.mktemp("genuine-catalogue-aa"))
    journey = _aa_journey_catalogue(tmp / "aa")
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    return SimpleNamespace(
        tmp=tmp, journey=journey, acceptance=acceptance, package=package,
    )


@pytest.fixture(scope="module")
def catalogue_equivalence(tmp_path_factory):
    if not CAPTURE_PATH.exists():
        pytest.skip("the genuine Selby Square captures are required")
    """The SAME genuine B-B journey twice: once with the fixture matcher
    (Page5SectionMatcher — no catalogue_version), once with the local
    catalogue. Everything must be identical except the recorded version."""
    base = tmp_path_factory.mktemp("genuine-catalogue-equiv")
    fixture_journey = _page5_journey(base / "fixture")
    catalogue_journey = _bb_journey_catalogue(base / "catalogue")
    return SimpleNamespace(
        base=base, fixture=fixture_journey, catalogue=catalogue_journey,
    )


@pytest.fixture(scope="module")
def dimensionless_workflow(tmp_path_factory):
    if not CAPTURE_PATH.exists():
        pytest.skip("the genuine Selby Square captures are required")
    """The genuine page-1 journey with member 001 citing the weight-only
    200UB30.4 row: the workflow must fail closed at the member stage."""
    tmp = Path(tmp_path_factory.mktemp("genuine-catalogue-dimensionless"))
    rows = copy.deepcopy(SELBY_MEMBER_ROWS)
    rows["001"] = dict(rows["001"])
    rows["001"]["section_name"] = "200UB30.4"
    rows["001"]["section_name_raw"] = "200UB30.4"
    rows["001"]["section_family"] = "UB"
    data = json.loads(CAPTURE_PATH.read_text())
    page1 = copy.deepcopy(data[0])
    marks = tuple(m["mark"] for m in page1["raw_members"])
    intake = intake_page_extractions(
        [page1], project_id=SELBY_JOURNEY_PROJECT_ID,
        source_drawing_id=SELBY_JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=marks, drawing_set_page_count=SELBY_PAGE_COUNT,
    )
    initial = evaluate_project_for_automation(intake.collection, intake=intake)
    built = build_exception_resolution_package(
        initial, intake.collection, member_rows=rows)
    group = next(g for g in built.connection_tasks
                 if g.review_package_id == SELBY_PACKAGE_ID)
    workflow = workflow_module.start_project_workflow(
        intake.collection, intake=intake, member_rows=rows,
        member_placements=SELBY_MEMBER_PLACEMENTS,
        section_matcher=CatalogueMatcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=SELBY_PACKAGE_ID,
        resolutions=_selby_reviewer_resolutions(group), output_dir=tmp,
    )
    return SimpleNamespace(workflow=workflow)


def _record(journey):
    return journey.workflow.connection_records[0]


def _record_for(workflow, package_id):
    """The connection record for one package id, paired with the visible
    connection state in submission order (records and states share the
    submission order)."""
    index = next(i for i, state in enumerate(workflow.connections)
                 if state.package_id == package_id)
    return workflow.connection_records[index]


def _assert_genuine_stages(record, acceptance, package):
    """AUTO -> GENERATED -> VERIFIED -> ACCEPTED -> PACKAGED."""
    assert record.pipeline.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
    assert record.dispatch_result.output_status == OUTPUT_STATUS_GENERATED
    assert record.verification_result.verification_status == VERIFICATION_STATUS_VERIFIED
    assert acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
    assert len(package.items) >= 1
    for item in package.items:
        assert Path(item.filename).exists() or Path(item.source_artifact).exists()


# =============================================================================
# page-1: 250X90PFC + 250X12FL through the genuine chain
# =============================================================================

class TestPage1JourneyWithTheLocalCatalogue:

    def test_reaches_auto_generated_verified_accepted_packaged(
            self, page1_catalogue_journey):
        record = _record(page1_catalogue_journey.journey)
        _assert_genuine_stages(
            record, page1_catalogue_journey.acceptance,
            page1_catalogue_journey.package)

    def test_the_packaged_pdf_is_the_real_generated_artifact(
            self, page1_catalogue_journey):
        record = _record(page1_catalogue_journey.journey)
        pdf = Path(page1_catalogue_journey.journey.workflow.generated_files[0])
        assert pdf.exists() and pdf.stat().st_size > 0
        assert record.verification_result.sha256 is not None
        assert record.dispatch_result.generated_files

    def test_the_members_were_resolved_from_the_catalogue_rows(
            self, page1_catalogue_journey):
        record = _record(page1_catalogue_journey.journey)
        assembly = record.pipeline.reviewed_assembly
        sections = {assembly.member_a.geometry.section_name,
                    assembly.member_b.geometry.section_name}
        families = {assembly.member_a.geometry.section_family,
                    assembly.member_b.geometry.section_family}
        assert sections == {"250X90PFC", "250X12FL"}
        assert families == {"PFC", "PL"}

    def test_the_produced_drawing_identifies_the_catalogue_version(
            self, page1_catalogue_journey):
        record = _record(page1_catalogue_journey.journey)
        assert record.pipeline.catalogue_version == CATALOGUE_VERSION
        assert record.dispatch_result.catalogue_version == CATALOGUE_VERSION


# =============================================================================
# page-5 VIEW B-B: 310UB40 + the three PL plates through the genuine chain
# =============================================================================

class TestViewBBJourneyWithTheLocalCatalogue:

    def test_reaches_auto_generated_verified_accepted_packaged(
            self, bb_catalogue_journey):
        record = _record(bb_catalogue_journey.journey)
        _assert_genuine_stages(
            record, bb_catalogue_journey.acceptance,
            bb_catalogue_journey.package)

    def test_the_four_members_came_from_the_four_catalogue_rows(
            self, bb_catalogue_journey):
        record = _record(bb_catalogue_journey.journey)
        assembly = record.pipeline.reviewed_assembly
        sections = {member.geometry.section_name for member in assembly.members}
        families = {member.geometry.section_family for member in assembly.members}
        assert sections == {"310UB40", "160x10PL", "165x10PL", "81x10PL"}
        assert families == {"UB", "PL"}

    def test_the_produced_drawing_identifies_the_catalogue_version(
            self, bb_catalogue_journey):
        record = _record(bb_catalogue_journey.journey)
        assert record.pipeline.catalogue_version == CATALOGUE_VERSION
        assert record.dispatch_result.catalogue_version == CATALOGUE_VERSION


# =============================================================================
# page-5 VIEW A-A: 310UB40 + 180X20FL + 90X10EA through the genuine chain
# =============================================================================

class TestViewAAJourneyWithTheLocalCatalogue:

    def test_reaches_auto_generated_verified_accepted_packaged(
            self, aa_catalogue_journey):
        record = _record(aa_catalogue_journey.journey)
        _assert_genuine_stages(
            record, aa_catalogue_journey.acceptance,
            aa_catalogue_journey.package)

    def test_the_aa_members_came_from_the_catalogue_rows(
            self, aa_catalogue_journey):
        record = _record(aa_catalogue_journey.journey)
        assembly = record.pipeline.reviewed_assembly
        sections = {member.geometry.section_name for member in assembly.members}
        families = {member.geometry.section_family for member in assembly.members}
        assert sections == {"310UB40", "180x20FL", "90x10EA"}
        assert families == {"UB", "FL", "EA"}

    def test_exactly_one_aa_drawing_is_packaged(self, aa_catalogue_journey):
        package = aa_catalogue_journey.package
        assert len(package.items) == 1
        assert package.items[0].package_id == JOURNEY_PACKAGE_ID
        assert aa_catalogue_journey.journey.workflow.verified_count == 1

    def test_the_produced_drawing_identifies_the_catalogue_version(
            self, aa_catalogue_journey):
        record = _record(aa_catalogue_journey.journey)
        assert record.pipeline.catalogue_version == CATALOGUE_VERSION
        assert record.dispatch_result.catalogue_version == CATALOGUE_VERSION


# =============================================================================
# equivalence: the substitution changes nothing except the recorded version
# =============================================================================

class TestSubstitutionChangesNothingButTheRecordedVersion:

    def test_same_decisions_and_stage_evidence(self, catalogue_equivalence):
        fixture_record = _record(catalogue_equivalence.fixture)
        catalogue_record = _record(catalogue_equivalence.catalogue)
        for fixture_attr, catalogue_attr in (
                ("automation_gate_result", "automation_gate_result"),
                ("validation_passed", "validation_passed"),
                ("validation_failure", "validation_failure"),
                ("specification", "specification"),
        ):
            assert getattr(fixture_record.pipeline, fixture_attr) == \
                getattr(catalogue_record.pipeline, catalogue_attr)
        assert fixture_record.dispatch_result.connection_id == \
            catalogue_record.dispatch_result.connection_id
        assert fixture_record.dispatch_result.decision == \
            catalogue_record.dispatch_result.decision
        assert fixture_record.dispatch_result.output_status == \
            catalogue_record.dispatch_result.output_status
        assert fixture_record.dispatch_result.requested_formats == \
            catalogue_record.dispatch_result.requested_formats
        assert fixture_record.dispatch_result.generation_error == \
            catalogue_record.dispatch_result.generation_error
        fixture_verification = fixture_record.verification_result
        catalogue_verification = catalogue_record.verification_result
        assert fixture_verification.verification_status == \
            catalogue_verification.verification_status
        assert fixture_verification.artifact_format == \
            catalogue_verification.artifact_format
        assert fixture_verification.file_size_bytes == \
            catalogue_verification.file_size_bytes
        assert fixture_verification.page_count == \
            catalogue_verification.page_count
        # the checks are equal in code/status/detail; the detail strings
        # embed each run's own tmp-dir path, so compare with the artifact
        # path reduced to its basename — and the ARTIFACT_HASH digest
        # differs only through reportlab's per-file /ID and timestamps,
        # which test_the_generated_pdfs_are_byte_identical proves are the
        # ONLY byte differences, so the recorded digest is normalised here
        import re as _re

        def _check_signature(check):
            detail = _re.sub(
                r"/(?:[^/ ]+/)+([^/ ]+-fabrication\.pdf)",
                r"\1", check.detail)
            detail = _re.sub(
                r"sha256 = [0-9a-f]{64}", "sha256 = <artifact bytes>", detail)
            return (check.code, check.status, detail)

        assert [_check_signature(c) for c in fixture_verification.checks] == \
            [_check_signature(c) for c in catalogue_verification.checks]
        # the artifact PATH itself differs (each run writes into its own
        # tmp dir); every identity field above is equal

    def test_the_ai_evidence_is_identical(self, catalogue_equivalence):
        # the review package (the AI-extracted content) is byte-for-byte
        # the same data regardless of which matcher resolves the sections
        fixture_package = catalogue_equivalence.fixture.scoped_collection.candidates[
            0].package.extraction
        catalogue_package = catalogue_equivalence.catalogue.scoped_collection.candidates[
            0].package.extraction
        assert fixture_package == catalogue_package
        # and no catalogue identity ever appears inside the AI evidence
        assert "catalogue" not in json.dumps(dataclasses.asdict(catalogue_package))

    def test_the_generated_pdfs_are_byte_identical(self, catalogue_equivalence):
        # reportlab writes three per-file values into every PDF: a random
        # /ID trailer entry and the /CreationDate, /ModDate timestamps.
        # Strip those and the drawings are byte-identical — the
        # substitution changed nothing the generator wrote.
        import re as _re

        def _content_digest(path):
            body = path.read_bytes()
            body = _re.sub(
                rb"/ID \s*\[<[0-9a-fA-F]+><[0-9a-fA-F]+>\]", b"/ID []", body)
            body = _re.sub(
                rb"(?<=/)(CreationDate|ModDate) \(D:[^)]*\)",
                rb"\1 ()", body)
            return hashlib.sha256(body).hexdigest()

        fixture_pdf = Path(
            catalogue_equivalence.fixture.workflow.generated_files[0])
        catalogue_pdf = Path(
            catalogue_equivalence.catalogue.workflow.generated_files[0])
        assert fixture_pdf.name == catalogue_pdf.name
        assert fixture_pdf.stat().st_size == catalogue_pdf.stat().st_size
        assert _content_digest(fixture_pdf) == _content_digest(catalogue_pdf)
        from pypdf import PdfReader
        assert PdfReader(fixture_pdf).pages[0].extract_text() == \
            PdfReader(catalogue_pdf).pages[0].extract_text()

    def test_only_the_catalogue_version_field_differs(
            self, catalogue_equivalence):
        fixture_manifest = _record(catalogue_equivalence.fixture).dispatch_result
        catalogue_manifest = _record(catalogue_equivalence.catalogue).dispatch_result
        assert fixture_manifest.catalogue_version is None
        assert catalogue_manifest.catalogue_version == CATALOGUE_VERSION
        assert _record(catalogue_equivalence.fixture).pipeline.catalogue_version is None
        assert _record(catalogue_equivalence.catalogue).pipeline.catalogue_version \
            == CATALOGUE_VERSION


# =============================================================================
# dimensionless in the genuine workflow: REVIEW, no artifact, no guessing
# =============================================================================

class TestDimensionlessSectionInsideTheGenuineWorkflow:

    def test_the_run_stays_review_with_the_real_refusal(
            self, dimensionless_workflow):
        record = _record(dimensionless_workflow)
        assert record.pipeline.validation_passed is False
        assert record.pipeline.validation_failure is not None
        assert "Refusing to guess" in record.pipeline.validation_failure.message
        assert record.pipeline.automation_gate_result.decision == \
            AUTOMATION_DECISION_REVIEW
        assert record.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        assert record.dispatch_result.generated_files == ()
        assert dimensionless_workflow.workflow.generated_files == ()

    def test_the_refusal_names_the_missing_dimensions_never_guesses_them(
            self, dimensionless_workflow):
        record = _record(dimensionless_workflow)
        message = record.pipeline.validation_failure.message
        for missing in ("depth", "flange_width", "flange_thickness", "web_thickness"):
            assert missing in message

    def test_the_failed_run_still_identifies_the_catalogue_it_used(
            self, dimensionless_workflow):
        record = _record(dimensionless_workflow)
        # the run used the versioned catalogue even though geometry was
        # refused: the record keeps the honest provenance
        assert record.pipeline.catalogue_version == CATALOGUE_VERSION
        assert record.dispatch_result.catalogue_version == CATALOGUE_VERSION
