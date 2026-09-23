"""
FL + EA SECTION-FAMILY SUPPORT (tests).

The core-completion milestone that removes the last code-fixable
production-blocking gap in the real Selby drawing set: real shop
drawings list 180X20FL (flat bar) and 90X10EA (equal angle) members,
and the CAD engine used to refuse both families. This file proves:

  * FL is served by the EXISTING flat-plate builder (a registry entry,
    never duplicate geometry logic) and EA gets a GENUINE two-leg
    L-section builder (leg dimension + thickness) — never a flat-plate
    approximation, never a hard-coded 90x10 special case (the builder
    contains no numeric literals, AST-proven).
  * The real page-5 VIEW A-A connection (005 310UB40 / PL008 180x20FL /
    CL004 90x10EA) now travels the genuine chain end to end — 7Y intake,
    7AB/7AC review tasks, human resolutions, 7AD rerun, 7AA/7Z pipeline,
    7AE gate, 7AF dispatch, 7AG verification, 7AQ acceptance, 7AR
    package, 7AX coverage — to AUTO/GENERATED/VERIFIED with a real PDF
    that carries the reviewed record.
  * The 7AV geometry-consistency gate still guards: with the original
    (uncorrected) 7AX-era PL008 placement the genuine engine refuses at
    7AV_MULTI_VALIDATION — the section-family boundary is crossed, and
    nothing is repositioned or approximated to force agreement.
  * UC, CHS and UA remain explicitly refused at both enforcement points;
    no existing unsupported-family protection is weakened.

No network, no credentials, no database access; the journey uses only
the genuine modules and the real captured fixture.
"""

import ast
import copy
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.errors import (
    GeometryValidationError,
    UnsupportedSectionFamilyError,
)
from app.cad_engine.exception_resolution import build_exception_resolution_package
from app.cad_engine.fabrication_package import build_fabrication_package
from app.cad_engine.interface import ValidatedSteelMember, generate_geometry
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_acceptance import accept_production_job
from app.cad_engine.production_coverage import (
    DISPOSITION_PRODUCED,
    evaluate_production_coverage,
)
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_connection_review import (
    create_project_connection_collection,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine import project_workflow as workflow_module
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.sections import (
    EA_REQUIRED_FIELDS,
    PLATE_REQUIRED_FIELDS,
    PROFILE_BUILDERS,
    build_ea_profile,
    build_plate_profile,
)
from tests.test_real_world_multi_member_connection import (
    JOURNEY_MEMBER_PLACEMENTS,
    JOURNEY_MEMBER_ROWS,
    JOURNEY_PACKAGE_ID,
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
    Page5SectionMatcher,
    _page5_capture,
    _view_a_a_entry,
    needs_selby_capture,
)
from tests.test_real_world_production_coverage import (
    VIEW_AA_ANSWERS_BY_TASK_TYPE,
    _resolutions_for,
)

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "app" / "cad_engine" / "sections.py")

FL_SECTION = {"name": "180X20FL", "family": "FL",
              "width": 180.0, "thickness": 20.0, "weight_per_metre": 28.3}
EA_SECTION = {"name": "90X10EA", "family": "EA",
              "width": 90.0, "thickness": 10.0, "weight_per_metre": 13.3}


def _module_source():
    return MODULE_PATH.read_text(encoding="utf-8")


def _module_tree():
    return ast.parse(_module_source())


def _extrude_bbox_volume(profile, length_mm):
    solid = profile.extrude(length_mm)
    bbox = solid.val().BoundingBox()
    return bbox, solid.val().Volume()


# =============================================================================
# 1. Module surface: two registry entries, no special cases.
# =============================================================================
class TestModuleSurface:
    def test_fl_is_served_by_the_existing_flat_plate_builder(self):
        # FL is a flat plate: one registry entry, zero duplicate geometry
        # logic. The builder itself is the PL one, unchanged.
        assert PROFILE_BUILDERS["FL"] is build_plate_profile
        assert PROFILE_BUILDERS["FL"] is PROFILE_BUILDERS["PL"]

    def test_ea_has_its_own_genuine_builder(self):
        assert PROFILE_BUILDERS["EA"] is build_ea_profile
        assert PROFILE_BUILDERS["EA"] is not build_plate_profile

    def test_supported_families_are_exactly_the_seven(self):
        assert sorted(PROFILE_BUILDERS) == [
            "EA", "FL", "PFC", "PL", "RHS", "SHS", "UB",
        ]

    def test_the_ea_builder_has_no_nonzero_numeric_literals(self):
        # The whole builder is parameterised by the matched row. Its only
        # numeric literals are zeros — the local-frame origin and the
        # positivity bound, the same coordinate-frame idiom as the plate
        # builder. A hard-coded 90x10 (or any other) special case would
        # need a nonzero dimension literal, and none can exist.
        tree = _module_tree()
        builder = next(n for n in tree.body
                       if isinstance(n, ast.FunctionDef)
                       and n.name == "build_ea_profile")
        literals = [node.value for node in ast.walk(builder)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, (int, float))]
        assert literals
        assert all(value == 0 for value in literals)

    def test_no_fl_or_ea_size_is_hard_coded_in_executable_code(self):
        # Docstrings may name 90X10EA / 250X12FL as examples; executable
        # string constants may not.
        tree = _module_tree()
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                if (node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings):
                lowered = node.value.lower()
                assert "90x10" not in lowered
                assert "180x20" not in lowered


# =============================================================================
# 2. FL (flat bar): real geometry through the shared builder.
# =============================================================================
class TestFlatBarGeometry:
    def test_180x20fl_builds_a_180_by_20_flat_bar(self):
        profile = PROFILE_BUILDERS["FL"](dict(FL_SECTION), "PL008")
        bbox, volume = _extrude_bbox_volume(profile, 340.0)
        assert abs(bbox.xlen - 180.0) < 1e-6
        assert abs(bbox.ylen - 20.0) < 1e-6
        assert abs(bbox.zlen - 340.0) < 1e-6  # the member length, not a dim
        assert abs(volume - 180.0 * 20.0 * 340.0) < 1e-6

    def test_a_different_flat_bar_size_builds(self):
        # No 180 dependency: any matched flat-bar row builds.
        profile = PROFILE_BUILDERS["FL"](
            {"name": "250X12FL", "family": "FL",
             "width": 250.0, "thickness": 12.0}, "PL099")
        bbox, _ = _extrude_bbox_volume(profile, 100.0)
        assert abs(bbox.xlen - 250.0) < 1e-6
        assert abs(bbox.ylen - 12.0) < 1e-6

    def test_the_adapter_accepts_a_real_fl_row_and_geometry_generates(self):
        row = {"mark": "PL008", "section_name": "180x20FL",
               "section_name_raw": "180x20FL", "section_family": "FL",
               "length_mm": 340.0, "grade": "300", "quantity": 1,
               "review_status": "approved", "source_page": 5,
               "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}

        class Matcher(Page5SectionMatcher):
            SECTIONS = dict(Page5SectionMatcher.SECTIONS)
            SECTIONS["180X20FL"] = dict(FL_SECTION)

        validated = real_member_to_validated_member(row, Matcher())
        geometry = generate_geometry(validated)
        assert geometry.section_family == "FL"
        bbox = geometry.solid.val().BoundingBox()
        assert abs(bbox.xlen - 180.0) < 1e-6
        assert abs(bbox.ylen - 20.0) < 1e-6
        assert abs(bbox.zlen - 340.0) < 1e-6

    def test_fl_missing_fields_are_refused_with_the_fl_label(self):
        with pytest.raises(GeometryValidationError,
                           match="missing required FL geometry"):
            PROFILE_BUILDERS["FL"](
                {"name": "180X20FL", "family": "FL"}, "PL008")

    def test_flat_bar_sanity_checks_are_unchanged(self):
        for width, thickness in ((0.0, 20.0), (-1.0, 20.0), (180.0, 0.0)):
            with pytest.raises(GeometryValidationError,
                               match="geometrically inconsistent"):
                PROFILE_BUILDERS["FL"](
                    {"name": "BAD", "family": "FL",
                     "width": width, "thickness": thickness}, "BAD")


# =============================================================================
# 3. EA (equal angle): a genuine two-leg L-section, never a plate.
# =============================================================================
class TestEqualAngleGeometry:
    def test_90x10ea_builds_a_90_by_90_l_section(self):
        profile = PROFILE_BUILDERS["EA"](dict(EA_SECTION), "CL004")
        bbox, volume = _extrude_bbox_volume(profile, 1.0)
        assert abs(bbox.xlen - 90.0) < 1e-6
        assert abs(bbox.ylen - 90.0) < 1e-6
        assert abs(bbox.zlen - 1.0) < 1e-6

    def test_the_profile_is_two_perpendicular_legs_not_a_flat_plate(self):
        # The closed wire has 6 corners (an L) where a flat plate has 4;
        # the cross-section area is 2*w*t - t**2 (the two legs, overlap
        # counted once) — 1700 mm2, not 900 (a plate) and not 8100 (a
        # solid square).
        profile = PROFILE_BUILDERS["EA"](dict(EA_SECTION), "CL004")
        assert len(profile.vertices().vals()) == 6
        plate = build_plate_profile(dict(FL_SECTION), "PL008")
        assert len(plate.vertices().vals()) == 4
        _, volume = _extrude_bbox_volume(profile, 1.0)
        assert abs(volume - (2 * 90.0 * 10.0 - 10.0 * 10.0)) < 1e-6
        assert abs(volume - 90.0 * 10.0) > 1.0      # never the flat-plate area
        assert abs(volume - 90.0 * 90.0) > 1.0      # never the solid square

    def test_a_different_equal_angle_size_builds(self):
        # No 90x10 dependency: any matched equal-angle row builds.
        profile = PROFILE_BUILDERS["EA"](
            {"name": "75X6EA", "family": "EA",
             "width": 75.0, "thickness": 6.0}, "CL099")
        bbox, volume = _extrude_bbox_volume(profile, 1.0)
        assert abs(bbox.xlen - 75.0) < 1e-6
        assert abs(bbox.ylen - 75.0) < 1e-6
        assert abs(volume - (2 * 75.0 * 6.0 - 6.0 * 6.0)) < 1e-6

    def test_the_adapter_accepts_a_real_ea_row_and_geometry_generates(self):
        row = {"mark": "CL004", "section_name": "90x10EA",
               "section_name_raw": "90x10EA", "section_family": "EA",
               "length_mm": 165.0, "grade": "300", "quantity": 1,
               "review_status": "approved", "source_page": 5,
               "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}

        class Matcher(Page5SectionMatcher):
            SECTIONS = dict(Page5SectionMatcher.SECTIONS)
            SECTIONS["90X10EA"] = dict(EA_SECTION)

        validated = real_member_to_validated_member(row, Matcher())
        geometry = generate_geometry(validated)
        assert geometry.section_family == "EA"
        bbox = geometry.solid.val().BoundingBox()
        assert abs(bbox.xlen - 90.0) < 1e-6
        assert abs(bbox.ylen - 90.0) < 1e-6
        assert abs(bbox.zlen - 165.0) < 1e-6

    def test_ea_missing_fields_are_refused(self):
        with pytest.raises(GeometryValidationError,
                           match="missing required EA geometry"):
            build_ea_profile({"name": "90X10EA", "family": "EA"}, "CL004")

    def test_ea_thickness_must_be_positive_and_less_than_the_leg(self):
        for width, thickness in ((90.0, 0.0), (90.0, -1.0),
                                 (90.0, 90.0), (90.0, 100.0)):
            with pytest.raises(GeometryValidationError,
                               match="geometrically inconsistent"):
                build_ea_profile({"name": "BAD", "family": "EA",
                                  "width": width, "thickness": thickness},
                                 "BAD")

    def test_unequal_legs_are_refused_not_averaged(self):
        with pytest.raises(GeometryValidationError,
                           match="equal angle must have equal legs"):
            build_ea_profile({"name": "BAD", "family": "EA",
                              "width": 90.0, "thickness": 10.0,
                              "depth": 80.0}, "BAD")

    def test_equal_legs_are_accepted(self):
        profile = build_ea_profile({"name": "90X10EA", "family": "EA",
                                    "width": 90.0, "thickness": 10.0,
                                    "depth": 90.0}, "CL004")
        bbox, _ = _extrude_bbox_volume(profile, 1.0)
        assert abs(bbox.xlen - 90.0) < 1e-6


# =============================================================================
# 4. Unsupported families remain explicitly refused — nothing weakened.
# =============================================================================
class TestUnsupportedFamiliesRemainRefused:
    def _row(self, family):
        return {"mark": "M-1", "section_name": f"TEST-{family}",
                "section_name_raw": f"TEST-{family}",
                "section_family": family, "length_mm": 1000.0,
                "grade": "300", "quantity": 1, "review_status": "approved",
                "source_page": 1, "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}

    def _matcher(self, family):
        class Matcher(Page5SectionMatcher):
            SECTIONS = dict(Page5SectionMatcher.SECTIONS)
            SECTIONS[f"TEST-{family}"] = {"name": f"TEST-{family}",
                                          "family": family, "depth": 200.0}
        return Matcher()

    @pytest.mark.parametrize("family", ["UC", "CHS", "UA"])
    def test_the_adapter_refuses_the_family(self, family):
        with pytest.raises(GeometryValidationError,
                           match="no supported CAD profile builder"):
            real_member_to_validated_member(self._row(family),
                                            self._matcher(family))

    @pytest.mark.parametrize("family", ["UC", "CHS", "UA"])
    def test_generate_geometry_refuses_the_family(self, family):
        member = ValidatedSteelMember(
            mark="M-1", section=f"TEST-{family}", length_mm=1000.0,
            material="300", orientation=None, connection_refs=[],
            source_refs=[{"page": 1}], validation_status="extracted",
            section_properties={"name": f"TEST-{family}", "family": family,
                                "depth": 200.0},
        )
        with pytest.raises(UnsupportedSectionFamilyError, match=family):
            generate_geometry(member)

    def test_the_supported_families_message_is_truthful(self):
        member = ValidatedSteelMember(
            mark="M-1", section="TEST-UA", length_mm=1000.0,
            material="300", orientation=None, connection_refs=[],
            source_refs=[{"page": 1}], validation_status="extracted",
            section_properties={"name": "TEST-UA", "family": "UA",
                                "depth": 200.0},
        )
        with pytest.raises(UnsupportedSectionFamilyError,
                           match=r"Supported families: \['EA', 'FL',"):
            generate_geometry(member)


# =============================================================================
# 5. The real VIEW A-A: the genuine chain, end to end.
# =============================================================================
def _view_aa_workflow(pl008_z, output_dir):
    """The genuine A-A journey: real page-5 capture (verbatim) -> 7Y intake
    -> 7AB -> 7AC tasks -> human resolutions -> 7AD rerun -> 7AA/7Z -> 7AE ->
    7AF -> 7AG, with the real FL and EA rows in the section table and the
    reviewer-corrected PL008 placement (END face at the reviewed connection,
    matching CL004)."""
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
        x=0.0, y=0.0, z=pl008_z, rotation_x=0.0, rotation_y=0.0,
        rotation_z=0.0)
    placements["CL004"] = MemberPlacement(
        x=0.0, y=0.0, z=4852.0, rotation_x=0.0, rotation_y=0.0,
        rotation_z=0.0)

    class Matcher(Page5SectionMatcher):
        SECTIONS = dict(Page5SectionMatcher.SECTIONS)
        SECTIONS["180X20FL"] = dict(FL_SECTION)
        SECTIONS["90X10EA"] = dict(EA_SECTION)

    initial = evaluate_project_for_automation(scoped_collection, intake=intake)
    built = build_exception_resolution_package(initial, scoped_collection,
                                               member_rows=rows)
    group = next(g for g in built.connection_tasks
                 if g.review_package_id == JOURNEY_PACKAGE_ID)
    workflow = workflow_module.start_project_workflow(
        scoped_collection, intake=intake, member_rows=rows,
        member_placements=placements, section_matcher=Matcher(),
    )
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=group.review_package_id,
        resolutions=_resolutions_for(
            group, copy.deepcopy(VIEW_AA_ANSWERS_BY_TASK_TYPE),
            "HUMAN-SUPPLIED TEST DATA (FL+EA milestone: real page-5 VIEW A-A)"),
        output_dir=output_dir,
    )
    return SimpleNamespace(
        page5=page5, full_intake=full_intake, intake=intake, workflow=workflow,
    )


@pytest.fixture(scope="module")
def view_aa_journey(tmp_path_factory):
    tmp = Path(tmp_path_factory.mktemp("fl-ea-aa"))
    return _view_aa_workflow(4677.0, tmp)


@needs_selby_capture
class TestTheRealViewAAJourney:
    def test_automation_is_genuinely_auto(self, view_aa_journey):
        record = view_aa_journey.workflow.connection_records[0]
        rerun = record.rerun_outcome
        assert rerun.decision == AUTOMATION_DECISION_AUTO
        assert rerun.blockers == ()
        assert rerun.validation_passed is True
        assert record.pipeline.validation_failure is None
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO

    def test_dispatch_generated_and_verification_verified_a_real_artifact(
            self, view_aa_journey):
        record = view_aa_journey.workflow.connection_records[0]
        assert record.dispatch_result.output_status == OUTPUT_STATUS_GENERATED
        assert record.dispatch_result.generation_error is None
        assert record.verification_result.verification_status == (
            VERIFICATION_STATUS_VERIFIED)
        artifact = Path(record.dispatch_result.generated_files[0])
        assert artifact.is_file()
        assert artifact.read_bytes()

    def test_the_pdf_carries_the_reviewed_fl_ea_record(self, view_aa_journey):
        record = view_aa_journey.workflow.connection_records[0]
        text = "".join(p.extract_text() or "" for p in
                       PdfReader(record.dispatch_result.generated_files[0]).pages)
        for needle in ("CONN-SELBY-VIEW-AA", "005", "PL008", "CL004",
                       "310UB40", "180x20FL", "90x10EA", "MATERIAL", "300",
                       "ATTACH", "END", "PAGE"):
            assert needle in text, needle

    def test_acceptance_package_and_coverage_close_the_loop(
            self, view_aa_journey, tmp_path):
        workflow = view_aa_journey.workflow
        acceptance = accept_production_job(workflow)
        assert acceptance.status == "ACCEPTED"
        assert acceptance.summary.connections_accepted == 1
        package = build_fabrication_package(acceptance, output_dir=tmp_path)
        assert package.status == "READY"
        assert len(package.items) == 1
        coverage = evaluate_production_coverage(workflow, package=package)
        assert coverage.summary.connections_discovered == 1
        row = coverage.connections[0]
        assert row.package_id == JOURNEY_PACKAGE_ID
        assert row.disposition == DISPOSITION_PRODUCED
        assert row.decision == AUTOMATION_DECISION_AUTO
        assert row.output_status == OUTPUT_STATUS_GENERATED
        assert row.verification_status == VERIFICATION_STATUS_VERIFIED
        assert row.verified is True
        assert row.accepted is True
        assert row.packaged is True
        assert Path(row.artifact_path).is_file()
        assert coverage.summary.artifacts_packaged == 1


# =============================================================================
# 6. The 7AV gate still guards: the original placement is honestly refused.
# =============================================================================
@pytest.fixture(scope="module")
def original_placement_journey(tmp_path_factory):
    tmp = Path(tmp_path_factory.mktemp("fl-ea-aa-original"))
    return _view_aa_workflow(4987.0, tmp)


@needs_selby_capture
class TestTheOriginalPlacementIsHonestlyRefused:
    def test_the_family_boundary_is_crossed_and_the_next_gate_refuses(
            self, original_placement_journey):
        # With the ORIGINAL 7AX-era PL008 placement (END face at Z=5327),
        # the genuine engine no longer stops at the section family — it
        # advances to the 7AV geometry-consistency check, which honestly
        # refuses: the reviewed attachment does not sit where the generated
        # connection plate is. Nothing is repositioned or approximated to
        # force agreement.
        record = original_placement_journey.workflow.connection_records[0]
        rerun = record.rerun_outcome
        assert rerun.decision != AUTOMATION_DECISION_AUTO
        failure = record.pipeline.validation_failure
        assert failure is not None
        assert failure.stage == "7AV_MULTI_VALIDATION"
        assert "PL008" in failure.message
        assert "5327" in failure.message
        assert record.dispatch_result.generated_files == ()
