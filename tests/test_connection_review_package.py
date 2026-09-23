"""
Milestone 7W — tests for the AI connection review package
(app.cad_engine.connection_review_package): the typed, traceable
boundary between AI extraction and the 7V reviewed-specification gate.

FIXTURE PROVENANCE (read before trusting any value below):
  - `AI_EXAMPLE_CONNECTION` is copied verbatim from the illustrative
    example inside app/ai_analysis/pdf_vision_analyzer.py's
    EXTRACTION_SYSTEM_PROMPT (asserted against that file's text, which is
    READ, never imported — importing the module pulls in app.config and
    the unrelated pre-existing SUPABASE_URL dependency). It is a
    prompt example, not data extracted from any drawing.
  - `make_ai_raw()` builds a SYNTHETIC, AI-SHAPED connection object using
    the actual schema's keys, with the marks of the established synthetic
    test members. It is TEST data: no AI produced it and no drawing
    contains it.
  - `make_reviewer_supplement()` holds SYNTHETIC / TEST-ONLY reviewer
    values (END, 4xO22mm holes at 90/140mm, location z=3994, attachments
    A->END / L2->START) chosen in earlier milestones purely to exercise the
    software path. They are not verified Arkles data or engineering
    decisions, and no real reviewer made them. "approved" is the
    review-WORKFLOW status 7V requires, not engineering approval.
  - Nothing in this file proves any value is correct engineering.
"""
import copy
from pathlib import Path

import pypdf
import pytest

import app.cad_engine.connection_review_package as review_module
from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_review_package import (
    CATEGORY_HUMAN_REVIEWED,
    CATEGORY_HUMAN_SUPPLEMENTED,
    CATEGORY_MISSING,
    CATEGORY_NEEDS_CONFIRMATION,
    ENGINEERING_FIELDS,
    EXTRACTION_STATUS_AI_EXTRACTED,
    EXTRACTION_STATUS_AI_NOT_EXTRACTED,
    READY_FOR_PIPELINE_MEANING,
    REVIEW_STATE_AI_EXTRACTED,
    REVIEW_STATE_READY_FOR_PIPELINE,
    REVIEW_STATE_REVIEW_REQUIRED,
    AIExtractedConnection,
    ConnectionReviewSupplement,
    ai_connection_to_extraction,
    attempt_7v_completeness,
    build_review_report,
    build_reviewed_connection_specification,
    create_review_package,
)
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_assembly import build_reviewed_two_member_connection_assembly
from app.cad_engine.reviewed_connection_drawing_gate import generate_fabrication_drawing_from_reviewed_assembly
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    REQUIRED_PROVENANCE_FIELDS,
    check_reviewed_connection_specification_completeness,
)
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_real_connection_adapter import make_7e_connection_record
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher
from tests.test_reviewed_connection_specification import make_synthetic_test_spec

_PDF_VISION_ANALYZER_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "ai_analysis" / "pdf_vision_analyzer.py"
).read_text()
assert '"connects_members": ["B8", "C1"]' in _PDF_VISION_ANALYZER_SOURCE
assert '"bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}]' in _PDF_VISION_ANALYZER_SOURCE
assert '"plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}]' in \
    _PDF_VISION_ANALYZER_SOURCE
assert '"welds": [{"type": "fillet", "size_mm": 8}]' in _PDF_VISION_ANALYZER_SOURCE

AI_EXAMPLE_CONNECTION = {
    "detail_reference": "D15", "grid_reference": "A-B/2",
    "connects_members": ["B8", "C1"], "connection_type": "bolted",
    "bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}],
    "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
    "welds": [{"type": "fillet", "size_mm": 8}],
    "confidence": 88,
}

_MARKS = ["REAL-UB-CAD-001", "L2"]  # the established SYNTHETIC test members' marks


def make_ai_raw(**overrides) -> dict:
    """A SYNTHETIC, AI-shaped raw connection object (see module docstring)."""
    raw = {
        "detail_reference": "D15", "grid_reference": "A-B/2",
        "connects_members": list(_MARKS), "connection_type": "bolted",
        "bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}],
        "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
        "welds": [{"type": "fillet", "size_mm": 8}],
        "confidence": 88, "page_num": 15,
    }
    raw.update(overrides)
    return raw


def make_extraction(raw=None, **kwargs) -> AIExtractedConnection:
    kwargs.setdefault("project_id", "PROJECT-TEST")
    kwargs.setdefault("source_drawing_id", "DRAWING-TEST")
    kwargs.setdefault("drawing_number", "S101")
    kwargs.setdefault("persisted_review_status", "extracted")
    return ai_connection_to_extraction(make_ai_raw() if raw is None else raw, **kwargs)


def make_reviewer_supplement(**overrides) -> ConnectionReviewSupplement:
    """SYNTHETIC / TEST-ONLY reviewer decisions (see module docstring)."""
    base = dict(
        connection_id="CONN-TEST-001",
        review_status="approved",
        position="END",
        holes={"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0},
        location={"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
        attachments=[
            {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
            {"member_mark": "L2", "surface_reference": "START"},
        ],
        confirmed_ai_fields=frozenset({"connected_member_marks", "plate"}),
    )
    base.update(overrides)
    return ConnectionReviewSupplement(**base)


def package(raw=None, supplement=None, known=None, **extraction_kwargs):
    return create_review_package(make_extraction(raw, **extraction_kwargs), supplement, known)


def entry(report, field_name):
    return next(e for e in report.entries if e.field == field_name)


def _pdf_curve_ops(path):
    content = pypdf.PdfReader(path).pages[0].get_contents().get_data().decode("latin-1")
    return content.count(" c\n") + content.count(" c ")


def _run_pipeline(pkg, tmp_path, name="out.pdf"):
    result = attempt_7v_completeness(pkg)
    assert result.is_complete, result.error
    matcher = make_multi_member_matcher()
    va = real_member_to_validated_member(make_member_a_row(), matcher)
    vb = real_member_to_validated_member(make_member_b_row(), matcher)
    pa, pb = make_member_a_placement(), make_member_b_placement()
    placed_a = PlacedMember(va.mark, place_member_geometry(generate_geometry(va), pa), pa)
    placed_b = PlacedMember(vb.mark, place_member_geometry(generate_geometry(vb), pb), pb)
    by_mark = {a.member_mark: a for a in result.attachments}
    assembly = build_reviewed_two_member_connection_assembly(
        placed_a, placed_b, result.validated_connection, result.connection_location,
        by_mark["REAL-UB-CAD-001"], by_mark["L2"],
    )
    return generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / name)


# =============================================================================
# Deliverable 1 — the AI extraction domain model
# =============================================================================
def test_ai_record_with_all_currently_available_fields_is_preserved_losslessly():
    ex = ai_connection_to_extraction(
        AI_EXAMPLE_CONNECTION, project_id="P1", source_drawing_id="D1", source_page=15,
        drawing_number="S101", persisted_review_status="extracted",
    )
    assert ex.detail_reference == "D15" and ex.grid_reference == "A-B/2"
    assert ex.connected_member_references == ("B8", "C1")
    assert ex.generic_connection_type == "bolted"
    assert (ex.bolts[0].quantity, ex.bolts[0].size, ex.bolts[0].grade) == (4, "M20", "8.8")
    assert (ex.plates[0].type, ex.plates[0].thickness_mm, ex.plates[0].width_mm, ex.plates[0].depth_mm) == \
        ("end_plate", 12, 180, 250)
    assert (ex.welds[0].type, ex.welds[0].size_mm) == ("fillet", 8)
    assert ex.confidence == 88
    assert (ex.project_id, ex.source_drawing_id, ex.source_page, ex.drawing_number) == ("P1", "D1", 15, "S101")
    assert ex.persisted_review_status == "extracted"
    assert ex.malformed_fields == {} and ex.unrecognised_fields == {}


def test_extraction_values_are_kept_exactly_as_reported_never_coerced():
    raw = make_ai_raw(bolts=[{"quantity": "4", "size": "M20", "grade": None}],
                      plates=[{"type": "end_plate", "thickness_mm": "12", "width_mm": None, "depth_mm": 250}])
    ex = ai_connection_to_extraction(raw)
    assert ex.bolts[0].quantity == "4" and ex.bolts[0].grade is None
    assert ex.plates[0].thickness_mm == "12" and ex.plates[0].width_mm is None


def test_extraction_preserves_unrecognised_fields_and_excludes_pipeline_page_num():
    raw = make_ai_raw(surprise="kept", bolts=[{"quantity": 4, "size": "M20", "grade": "8.8", "finish": "HDG"}])
    ex = ai_connection_to_extraction(raw)
    assert ex.unrecognised_fields == {"surprise": "kept"}
    assert ex.bolts[0].extra == {"finish": "HDG"}
    assert "page_num" not in ex.unrecognised_fields
    assert ex.source_page == 15  # taken from the pipeline-injected page_num


def test_explicit_source_page_wins_over_pipeline_page_num():
    assert ai_connection_to_extraction(make_ai_raw(), source_page=3).source_page == 3


@pytest.mark.parametrize("field_name, bad_value", [
    ("bolts", "M20"), ("bolts", [1, 2]), ("plates", {"type": "end_plate"}), ("welds", "fillet"),
    ("connects_members", "REAL-UB-CAD-001"), ("connects_members", [1, None]),
])
def test_malformed_ai_fields_are_preserved_raw_and_never_crash(field_name, bad_value):
    ex = ai_connection_to_extraction(make_ai_raw(**{field_name: bad_value}))
    assert ex.malformed_fields[field_name] == bad_value
    assert getattr(ex, {"bolts": "bolts", "plates": "plates", "welds": "welds",
                        "connects_members": "connected_member_references"}[field_name]) == ()


def test_a_non_dict_raw_connection_is_a_programming_error_not_swallowed():
    with pytest.raises(TypeError):
        ai_connection_to_extraction(["not", "a", "dict"])


def test_extraction_neither_mutates_nor_aliases_the_raw_object():
    raw = make_ai_raw()
    before = copy.deepcopy(raw)
    ex = ai_connection_to_extraction(raw)
    assert raw == before
    raw["bolts"][0]["size"] = "M24"
    raw["plates"][0]["width_mm"] = 999
    assert ex.bolts[0].size == "M20" and ex.plates[0].width_mm == 180


# =============================================================================
# Deliverable 2/6 — explicit extraction gaps and the review-gap report
# =============================================================================
def test_ai_only_report_states_what_was_and_was_not_extracted():
    report = build_review_report(package())

    assert report.state == REVIEW_STATE_AI_EXTRACTED and report.ready_for_pipeline is False
    assert report.found == ("connected_member_marks", "plate")
    assert report.missing == ("position", "holes", "location", "attachments", "material")
    assert report.needs_confirmation == ("connected_member_marks", "plate")
    assert report.needs_supplementation == ("position", "holes", "location", "attachments", "material")
    assert report.human_reviewed == () and report.human_supplemented == ()
    assert report.identity_missing == ("connection_id",)
    for name in ("position", "holes", "location", "attachments", "material"):
        e = entry(report, name)
        assert e.extraction_status == EXTRACTION_STATUS_AI_NOT_EXTRACTED
        assert e.ai_value is None and e.current_value is None and e.provenance is None
        assert e.category == CATEGORY_MISSING and e.needs_supplementation is True
    for name in ("connected_member_marks", "plate"):
        e = entry(report, name)
        assert e.extraction_status == EXTRACTION_STATUS_AI_EXTRACTED
        assert e.category == CATEGORY_NEEDS_CONFIRMATION and e.provenance == PROVENANCE_AI_EXTRACTED


def test_report_covers_the_six_required_fields_plus_optional_material_in_a_fixed_order():
    # Material (7AT) is an OPTIONAL engineering field: it rides with the six required
    # fields in the report but is deliberately NOT one of them — missing material is
    # reported, never demanded.
    assert ENGINEERING_FIELDS == REQUIRED_PROVENANCE_FIELDS + ("material",)
    assert tuple(e.field for e in build_review_report(package()).entries) == ENGINEERING_FIELDS


def test_ai_only_package_is_rejected_by_7v_and_yields_nothing_pipeline_usable():
    report = build_review_report(package())
    result = report.completeness
    assert result.is_complete is False
    assert result.validated_connection is None and result.connection_location is None
    assert result.attachments is None


def test_generic_type_only_record_defines_nothing():
    pkg = package({"connection_type": "bolted"})
    report = build_review_report(pkg)
    assert report.found == ()
    assert report.missing == ENGINEERING_FIELDS
    assert "generic_connection_type" in report.advisories
    assert "not a complete connection definition" in report.advisories["generic_connection_type"]
    assert report.state == REVIEW_STATE_AI_EXTRACTED


def test_record_missing_connected_members_needs_them_supplied():
    pkg = package(make_ai_raw(connects_members=None))
    report = build_review_report(pkg)
    e = entry(report, "connected_member_marks")
    assert e.extraction_status == EXTRACTION_STATUS_AI_NOT_EXTRACTED and e.category == CATEGORY_MISSING

    supplied = build_review_report(package(make_ai_raw(connects_members=None), make_reviewer_supplement(
        connected_member_marks=["REAL-UB-CAD-001", "L2"], confirmed_ai_fields=frozenset({"plate"}))))
    e2 = entry(supplied, "connected_member_marks")
    assert e2.category == CATEGORY_HUMAN_SUPPLEMENTED and e2.provenance == PROVENANCE_HUMAN_SUPPLEMENTED
    assert supplied.ready_for_pipeline is True


def test_unknown_member_references_are_reported_and_cannot_be_confirmed():
    raw = make_ai_raw(connects_members=["REAL-UB-CAD-001", "GHOST-9"])
    sup = make_reviewer_supplement()  # confirms connected_member_marks
    report = build_review_report(package(raw, sup, known=["REAL-UB-CAD-001", "L2"]))

    codes = {(i.code, i.field) for i in report.issues}
    assert ("UNKNOWN_MEMBER_REFERENCE", "connected_member_marks") in codes
    assert ("CANNOT_CONFIRM", "connected_member_marks") in codes
    e = entry(report, "connected_member_marks")
    assert e.category == CATEGORY_MISSING and e.current_value is None
    assert e.ai_value == ["REAL-UB-CAD-001", "GHOST-9"]  # what the AI said is still shown
    assert "GHOST-9" in e.note
    assert report.ready_for_pipeline is False


def test_member_references_are_only_checked_when_known_marks_are_supplied():
    raw = make_ai_raw(connects_members=["REAL-UB-CAD-001", "GHOST-9"])
    report = build_review_report(package(raw))
    assert "member_references" in report.advisories
    assert all(i.code != "UNKNOWN_MEMBER_REFERENCE" for i in report.issues)


@pytest.mark.parametrize("size", ["M16", "M20", "M24", "M20 8.8"])
def test_nominal_bolt_sizes_never_become_a_hole_diameter(size):
    raw = make_ai_raw(bolts=[{"quantity": 4, "size": size, "grade": "8.8"}])
    pkg = package(raw)
    report = build_review_report(pkg)
    spec = build_reviewed_connection_specification(pkg)

    assert entry(report, "holes").category == CATEGORY_MISSING
    assert spec.holes is None
    assert size in report.advisories["bolts"]
    assert "not a hole diameter" in report.advisories["bolts"]
    assert "holes" not in spec.provenance


def test_plate_dimensions_without_a_numeric_hole_diameter_stays_incomplete():
    sup = make_reviewer_supplement(holes=None)  # everything supplied EXCEPT the hole pattern
    report = build_review_report(package(make_ai_raw(), sup))
    assert entry(report, "plate").category == CATEGORY_HUMAN_REVIEWED
    assert entry(report, "holes").category == CATEGORY_MISSING
    assert report.state == REVIEW_STATE_REVIEW_REQUIRED
    assert "holes" in report.completeness.error or "hole" in report.completeness.error


def test_multiple_plates_are_ambiguous_and_none_is_selected_by_position():
    plates = [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250},
              {"type": "end_plate", "thickness_mm": 20, "width_mm": 100, "depth_mm": 100}]
    for ordering in (plates, list(reversed(plates))):
        pkg = package(make_ai_raw(plates=ordering), make_reviewer_supplement())  # supplement confirms 'plate'
        report = build_review_report(pkg)
        e = entry(report, "plate")
        assert e.category == CATEGORY_MISSING and e.current_value is None
        assert len(e.ai_value) == 2  # both are shown, none chosen
        codes = {i.code for i in report.issues}
        assert "AMBIGUOUS_MULTIPLE_PLATES" in codes and "CANNOT_CONFIRM" in codes
        assert build_reviewed_connection_specification(pkg).plate is None


def test_multiple_plates_are_resolved_only_by_the_reviewer_supplying_one():
    plates = [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250},
              {"type": "end_plate", "thickness_mm": 20, "width_mm": 100, "depth_mm": 100}]
    chosen = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    sup = make_reviewer_supplement(plate=chosen, confirmed_ai_fields=frozenset({"connected_member_marks"}))
    report = build_review_report(package(make_ai_raw(plates=plates), sup))
    assert entry(report, "plate").category == CATEGORY_HUMAN_SUPPLEMENTED
    assert report.ready_for_pipeline is True


def test_a_single_plate_missing_a_dimension_is_incomplete_and_never_estimated():
    raw = make_ai_raw(plates=[{"type": "end_plate", "width_mm": 180, "depth_mm": 250}])  # no thickness
    report = build_review_report(package(raw, make_reviewer_supplement()))
    e = entry(report, "plate")
    assert e.category == CATEGORY_MISSING and e.current_value is None
    assert "thickness_mm" in e.note
    assert any(i.code == "INCOMPLETE_AI_PLATE" for i in report.issues)


def test_multiple_bolt_groups_select_nothing_by_order():
    bolts = [{"quantity": 4, "size": "M20", "grade": "8.8"}, {"quantity": 2, "size": "M16", "grade": "4.6"}]
    reports = [build_review_report(package(make_ai_raw(bolts=b))) for b in (bolts, list(reversed(bolts)))]
    for r in reports:
        assert entry(r, "holes").category == CATEGORY_MISSING
        assert "none is selected by order" in r.advisories["bolts"]
    assert reports[0].missing == reports[1].missing


def test_weld_information_is_preserved_and_has_no_effect_on_readiness():
    with_welds = package(make_ai_raw(), make_reviewer_supplement())
    without = package(make_ai_raw(welds=None), make_reviewer_supplement())
    assert with_welds.extraction.welds[0].size_mm == 8
    assert "welds" in build_review_report(with_welds).advisories
    assert "welds" not in build_review_report(without).advisories
    assert build_review_report(with_welds).ready_for_pipeline is build_review_report(without).ready_for_pipeline is True
    assert not hasattr(build_reviewed_connection_specification(with_welds), "welds")


@pytest.mark.parametrize("confidence", [0, 30, 79, 100])
def test_confidence_is_preserved_but_is_never_approval_and_never_changes_readiness(confidence):
    ai_only = build_review_report(package(make_ai_raw(confidence=confidence)))
    assert ai_only.state == REVIEW_STATE_AI_EXTRACTED and ai_only.ready_for_pipeline is False
    assert "not an engineering approval" in ai_only.advisories["confidence"]

    reviewed = build_review_report(package(make_ai_raw(confidence=confidence), make_reviewer_supplement()))
    assert reviewed.ready_for_pipeline is True  # low confidence does not block a reviewed record either
    spec = build_reviewed_connection_specification(package(make_ai_raw(confidence=confidence)))
    assert spec.review_status == "extracted"  # never promoted to "approved" by a high score
    assert not hasattr(spec, "confidence")


# =============================================================================
# Deliverable 5 — human supplementation, one field at a time
# =============================================================================
def _only(**fields):
    """A supplement supplying just the named fields and confirming nothing."""
    return ConnectionReviewSupplement(**fields)


@pytest.mark.parametrize("field_name, value", [
    ("holes", {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}),
    ("position", "END"),
    ("location", {"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}),
    ("attachments", [{"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
                     {"member_mark": "L2", "surface_reference": "START"}]),
    ("material", "300PLUS"),
])
def test_each_missing_field_can_be_explicitly_supplied_and_is_marked_human_supplemented(field_name, value):
    pkg = package(supplement=_only(**{field_name: value}))
    report = build_review_report(pkg)
    e = entry(report, field_name)
    assert e.category == CATEGORY_HUMAN_SUPPLEMENTED and e.provenance == PROVENANCE_HUMAN_SUPPLEMENTED
    assert e.current_value == value and e.ai_value is None
    assert report.provenance[field_name] == PROVENANCE_HUMAN_SUPPLEMENTED
    assert getattr(build_reviewed_connection_specification(pkg), field_name) == value
    # Supplying one field changes only that field.
    others = [f for f in ENGINEERING_FIELDS if f != field_name]
    assert [entry(report, f).category for f in others if f not in ("connected_member_marks", "plate")] == \
        [CATEGORY_MISSING] * 4
    assert [entry(report, f).category for f in ("connected_member_marks", "plate")] == \
        [CATEGORY_NEEDS_CONFIRMATION] * 2


def test_a_supplied_value_overrides_an_ai_value_and_is_labelled_supplemented_not_extracted():
    new_plate = {"type": "end_plate", "thickness_mm": 16, "width_mm": 160, "depth_mm": 220}
    report = build_review_report(package(supplement=_only(plate=new_plate)))
    e = entry(report, "plate")
    assert e.provenance == PROVENANCE_HUMAN_SUPPLEMENTED and e.current_value == new_plate
    assert e.ai_value == {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    assert "overrides" in e.note


def test_supplementation_never_merges_ai_bolt_data_into_the_supplied_holes():
    holes = {"quantity": 2, "diameter_mm": 20.0, "vertical_spacing_mm": 100.0}
    spec = build_reviewed_connection_specification(package(supplement=_only(holes=holes)))
    assert spec.holes == holes  # exactly what was supplied: no "size"/"grade" carried over from bolts[]


# =============================================================================
# Deliverable 4 — provenance integrity
# =============================================================================
def test_mixed_ai_extracted_and_human_supplemented_fields_keep_their_own_labels():
    pkg = package(supplement=_only(position="END", review_status="approved"))  # nothing confirmed
    report = build_review_report(pkg)
    assert report.provenance == {
        "connected_member_marks": PROVENANCE_AI_EXTRACTED, "plate": PROVENANCE_AI_EXTRACTED,
        "position": PROVENANCE_HUMAN_SUPPLEMENTED,
    }
    assert report.state == REVIEW_STATE_REVIEW_REQUIRED
    # 7V's provenance gate (not this module) rejects it — here first for the fields with no label at all;
    # the AI_EXTRACTED-specific rejection is asserted in test_passing_validation_never_upgrades_...
    assert report.completeness.is_complete is False and "provenance" in report.completeness.error


def test_mixed_ai_extracted_and_human_reviewed_fields_keep_their_own_labels():
    pkg = package(supplement=_only(confirmed_ai_fields=frozenset({"plate"})))
    report = build_review_report(pkg)
    assert report.provenance == {"connected_member_marks": PROVENANCE_AI_EXTRACTED,
                                 "plate": PROVENANCE_HUMAN_REVIEWED}
    assert report.human_reviewed == ("plate",) and report.needs_confirmation == ("connected_member_marks",)
    assert report.ready_for_pipeline is False


def test_provenance_survives_the_package_to_specification_to_7v_boundary_unchanged():
    pkg = package(supplement=make_reviewer_supplement())
    report = build_review_report(pkg)
    spec = build_reviewed_connection_specification(pkg)
    result = attempt_7v_completeness(pkg)

    expected = {
        "connected_member_marks": PROVENANCE_HUMAN_REVIEWED, "position": PROVENANCE_HUMAN_SUPPLEMENTED,
        "plate": PROVENANCE_HUMAN_REVIEWED, "holes": PROVENANCE_HUMAN_SUPPLEMENTED,
        "location": PROVENANCE_HUMAN_SUPPLEMENTED, "attachments": PROVENANCE_HUMAN_SUPPLEMENTED,
    }
    assert report.provenance == spec.provenance == result.provenance == expected
    assert report.ready_for_pipeline is True


def test_passing_validation_never_upgrades_ai_extracted_to_human_reviewed():
    """Everything except the confirmations is supplied: the AI values are valid but were never reviewed."""
    pkg = package(supplement=make_reviewer_supplement(confirmed_ai_fields=frozenset()))
    spec = build_reviewed_connection_specification(pkg)
    # The AI plate is numerically valid, yet it stays AI_EXTRACTED and 7V refuses it.
    assert spec.plate == {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    assert spec.provenance["plate"] == PROVENANCE_AI_EXTRACTED
    assert spec.provenance["connected_member_marks"] == PROVENANCE_AI_EXTRACTED
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False and "AI_EXTRACTED" in result.error
    assert result.provenance["plate"] == PROVENANCE_AI_EXTRACTED  # still not rewritten by 7V


def test_adapters_do_not_rewrite_provenance_and_geometry_objects_never_carry_it():
    pkg = package(supplement=make_reviewer_supplement())
    spec = build_reviewed_connection_specification(pkg)
    before = dict(spec.provenance)
    result = check_reviewed_connection_specification_completeness(spec)
    assert spec.provenance == before == result.provenance
    for obj in (result.validated_connection, result.connection_location, *result.attachments):
        assert not hasattr(obj, "provenance")


def test_missing_required_provenance_is_rejected_by_7v():
    spec = build_reviewed_connection_specification(package(supplement=make_reviewer_supplement()))
    del spec.provenance["holes"]
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False and "no provenance recorded" in result.error


def test_an_ai_only_package_cannot_masquerade_as_a_complete_reviewed_specification():
    """Even a reviewer setting review_status='approved' on an otherwise untouched AI record changes nothing."""
    pkg = package(supplement=_only(review_status="approved"))
    report = build_review_report(pkg)
    assert report.state == REVIEW_STATE_REVIEW_REQUIRED
    assert report.provenance == {"connected_member_marks": PROVENANCE_AI_EXTRACTED,
                                 "plate": PROVENANCE_AI_EXTRACTED}
    assert report.completeness.is_complete is False
    assert report.completeness.validated_connection is None


# =============================================================================
# Confirm / supply rules
# =============================================================================
def test_confirming_and_supplying_the_same_field_is_rejected_as_ambiguous():
    sup = make_reviewer_supplement(plate={"type": "end_plate", "thickness_mm": 99, "width_mm": 99, "depth_mm": 99})
    report = build_review_report(package(supplement=sup))  # sup also confirms 'plate'
    assert any(i.code == "CONFIRM_AND_SUPPLY_CONFLICT" and i.field == "plate" for i in report.issues)
    e = entry(report, "plate")
    assert e.category == CATEGORY_NEEDS_CONFIRMATION  # neither applied; the unreviewed AI value remains
    assert e.current_value == {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    assert report.ready_for_pipeline is False


@pytest.mark.parametrize("field_name", ["position", "holes", "location", "attachments"])
def test_a_field_the_ai_did_not_extract_cannot_be_confirmed(field_name):
    sup = ConnectionReviewSupplement(confirmed_ai_fields=frozenset({field_name}))
    pkg = package(supplement=sup)
    report = build_review_report(pkg)
    assert any(i.code == "CANNOT_CONFIRM" and i.field == field_name for i in report.issues)
    assert entry(report, field_name).category == CATEGORY_MISSING
    assert getattr(build_reviewed_connection_specification(pkg), field_name) in (None, [])


def test_confirming_an_unknown_field_name_is_reported():
    report = build_review_report(package(supplement=ConnectionReviewSupplement(
        confirmed_ai_fields=frozenset({"weld_size"}))))
    assert any(i.code == "UNKNOWN_CONFIRMED_FIELD" and i.field == "weld_size" for i in report.issues)


def test_connection_id_alone_is_not_a_review_decision():
    sup = ConnectionReviewSupplement(connection_id="CONN-1")
    assert sup.has_review_decisions is False
    report = build_review_report(package(supplement=sup))
    assert report.state == REVIEW_STATE_AI_EXTRACTED and report.identity_missing == ()


# =============================================================================
# Critical anti-inference rules (1-10)
# =============================================================================
def test_anti_inference_1_m20_is_not_a_20mm_hole_and_a_bolt_only_holes_spec_is_rejected():
    pkg = package(supplement=make_reviewer_supplement(holes=None, confirmed_ai_fields=frozenset(
        {"connected_member_marks", "plate", "holes"})))  # even a request to 'confirm' holes is refused
    spec = build_reviewed_connection_specification(pkg)
    assert spec.holes is None
    assert any(i.code == "CANNOT_CONFIRM" and i.field == "holes" for i in build_review_report(pkg).issues)

    # And if a reviewer pastes the AI's nominal bolt data in as 'holes', 7V/7D rejects it for the diameter.
    bolt_only = make_reviewer_supplement(holes={"quantity": 4, "size": "M20", "grade": "8.8"})
    result = attempt_7v_completeness(package(supplement=bolt_only))
    assert result.is_complete is False and "diameter_mm" in result.error


def test_anti_inference_2_3_4_member_order_never_yields_start_end_or_attachments():
    for members in (["REAL-UB-CAD-001", "L2"], ["L2", "REAL-UB-CAD-001"]):
        sup = ConnectionReviewSupplement(confirmed_ai_fields=frozenset({"connected_member_marks"}))
        pkg = package(make_ai_raw(connects_members=members), sup)
        spec = build_reviewed_connection_specification(pkg)
        assert spec.position is None                 # not START from the first member, not END from the second
        assert spec.attachments == []                # no surface inferred from ordering
        assert spec.connected_member_marks == members  # order preserved verbatim, with no meaning attached
        report = build_review_report(pkg)
        assert {"position", "attachments"} <= set(report.missing)


def test_anti_inference_5_location_is_never_derived_from_grid_detail_or_page_order():
    for grid, detail, page in [("A-B/2", "D15", 1), ("C-D/7", "D99", 40), (None, None, None)]:
        raw = make_ai_raw(grid_reference=grid, detail_reference=detail, page_num=page)
        pkg = package(raw, ConnectionReviewSupplement(confirmed_ai_fields=frozenset({"plate"})))
        assert build_reviewed_connection_specification(pkg).location is None
        assert "location" in build_review_report(pkg).missing


def test_anti_inference_6_confidence_is_never_engineering_approval():
    report = build_review_report(package(make_ai_raw(confidence=100)))
    assert report.ready_for_pipeline is False
    assert report.state == REVIEW_STATE_AI_EXTRACTED
    assert build_reviewed_connection_specification(package(make_ai_raw(confidence=100))).review_status != "approved"


def test_anti_inference_7_generic_connection_type_is_not_a_connection_definition():
    for generic in ("bolted", "welded", "bolted_and_welded", "unspecified", "end_plate"):
        pkg = package({"connection_type": generic})  # no plates, no members, nothing else
        spec = build_reviewed_connection_specification(pkg)
        assert spec.plate is None and spec.connected_member_marks == []  # not even the word "end_plate" defines a plate
        assert build_review_report(pkg).missing == ENGINEERING_FIELDS


def test_anti_inference_8_ai_extracted_is_never_automatically_human_reviewed():
    pkg = package(supplement=make_reviewer_supplement(confirmed_ai_fields=frozenset()))
    report = build_review_report(pkg)
    assert report.human_reviewed == ()
    assert report.provenance["connected_member_marks"] == PROVENANCE_AI_EXTRACTED
    assert report.provenance["plate"] == PROVENANCE_AI_EXTRACTED
    assert PROVENANCE_HUMAN_REVIEWED not in report.provenance.values()


def test_anti_inference_9_a_complete_package_claims_neither_approval_nor_fabrication_readiness():
    report = build_review_report(package(supplement=make_reviewer_supplement()))
    assert report.state == REVIEW_STATE_READY_FOR_PIPELINE
    assert report.state_meaning == READY_FOR_PIPELINE_MEANING
    assert "not engineering approval" in READY_FOR_PIPELINE_MEANING
    assert "fabrication readiness" in READY_FOR_PIPELINE_MEANING and "not" in READY_FOR_PIPELINE_MEANING
    forbidden = [n for n in dir(review_module)
                 if any(word in n.upper() for word in ("ENGINEERING_APPROVED", "FABRICATION_READY", "STRUCTURALLY"))]
    assert forbidden == []
    assert report.state not in ("ENGINEERING_APPROVED", "FABRICATION_READY", "APPROVED")


def test_anti_inference_10_no_dimension_or_coordinate_is_created_from_geometry():
    holes = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0}  # deliberately no horizontal spacing
    location = {"x": 5.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    spec = build_reviewed_connection_specification(package(supplement=make_reviewer_supplement(
        holes=holes, location=location)))
    assert spec.holes == holes and "horizontal_spacing_mm" not in spec.holes  # not derived from plate width
    assert spec.location == location
    assert spec.plate == {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}  # only what the AI said
    assert attempt_7v_completeness(package(supplement=make_reviewer_supplement(holes=holes))).is_complete is False


# =============================================================================
# Deliverable 7 — integration with 7V / 7U (no second CAD path)
# =============================================================================
def test_the_built_specification_matches_the_established_7v_fixture_structure():
    spec = build_reviewed_connection_specification(package(supplement=make_reviewer_supplement()))
    fixture = make_synthetic_test_spec(connection_id="CONN-TEST-001")
    for attr in ("connection_id", "review_status", "position", "plate", "holes", "location", "attachments"):
        assert getattr(spec, attr) == getattr(fixture, attr), attr
    assert spec.connected_member_marks == fixture.connected_member_marks


def test_readiness_is_decided_by_7vs_own_completeness_check_and_nothing_else(monkeypatch):
    calls = []
    original = review_module.check_reviewed_connection_specification_completeness

    def tracking(spec):
        calls.append(spec)
        return original(spec)

    monkeypatch.setattr(review_module, "check_reviewed_connection_specification_completeness", tracking)
    pkg = package(supplement=make_reviewer_supplement())
    report = build_review_report(pkg)
    assert len(calls) == 1 and calls[0] == build_reviewed_connection_specification(pkg)
    assert report.completeness is not None and report.ready_for_pipeline is (report.completeness.is_complete)


def test_complete_human_reviewed_package_enters_the_existing_7u_pipeline(tmp_path):
    out = _run_pipeline(package(supplement=make_reviewer_supplement()), tmp_path)
    text = pypdf.PdfReader(out).pages[0].extract_text()
    assert "REAL-UB-CAD-001" in text and "L2" in text and "CONNECTION DETAIL" in text
    assert "PLATE: 180" in text
    assert _pdf_curve_ops(out) == 16  # 4 real holes x 4 bezier curves


def test_two_materially_different_reviewed_inputs_produce_different_drawings(tmp_path):
    raw_a = make_ai_raw()  # AI plate 180x250x12
    sup_a = make_reviewer_supplement()  # 4 x O22, 90 x 140
    raw_b = make_ai_raw(plates=[{"type": "end_plate", "thickness_mm": 16, "width_mm": 160, "depth_mm": 220}])
    sup_b = make_reviewer_supplement(holes={"quantity": 2, "diameter_mm": 20.0, "vertical_spacing_mm": 100.0})

    out_a = _run_pipeline(package(raw_a, sup_a), tmp_path, "a.pdf")
    out_b = _run_pipeline(package(raw_b, sup_b), tmp_path, "b.pdf")
    text_a = pypdf.PdfReader(out_a).pages[0].extract_text()
    text_b = pypdf.PdfReader(out_b).pages[0].extract_text()

    assert "PLATE: 180 × 250 × 12" in text_a and "HOLES: 4 × Ø22" in text_a
    assert "PLATE: 160 × 220 × 16" in text_b and "HOLES: 2 × Ø20" in text_b
    assert (_pdf_curve_ops(out_a), _pdf_curve_ops(out_b)) == (16, 8)


def test_incomplete_review_package_never_reaches_assembly_or_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module
    drawing_calls = []
    monkeypatch.setattr(gate_module, "generate_connection_fabrication_drawing_pdf",
                        lambda *a, **k: drawing_calls.append(True) or None)

    for pkg in (package(), package(supplement=make_reviewer_supplement(position=None)),
                package(supplement=make_reviewer_supplement(review_status="pending_review"))):
        result = attempt_7v_completeness(pkg)
        assert result.is_complete is False
        assert result.validated_connection is None and result.connection_location is None
        assert result.attachments is None
    assert drawing_calls == [] and list(tmp_path.iterdir()) == []


def test_review_state_progression_is_ai_extracted_then_review_required_then_ready():
    assert build_review_report(package()).state == REVIEW_STATE_AI_EXTRACTED
    assert build_review_report(package(supplement=make_reviewer_supplement(position=None))).state == \
        REVIEW_STATE_REVIEW_REQUIRED
    assert build_review_report(package(supplement=make_reviewer_supplement())).state == \
        REVIEW_STATE_READY_FOR_PIPELINE


# =============================================================================
# Determinism, immutability, and consistency with the adapter it feeds
# =============================================================================
def test_the_report_is_deterministic_and_never_mutates_the_package():
    pkg = package(supplement=make_reviewer_supplement())
    before = copy.deepcopy(pkg)
    first, second = build_review_report(pkg), build_review_report(pkg)
    assert first == second
    assert pkg == before
    assert build_reviewed_connection_specification(pkg) == build_reviewed_connection_specification(pkg)


def test_supplement_values_are_copied_so_later_edits_cannot_change_a_built_specification():
    sup = make_reviewer_supplement()
    spec = build_reviewed_connection_specification(package(supplement=sup))
    sup.holes["diameter_mm"] = 999.0
    sup.location["z"] = -1.0
    assert spec.holes["diameter_mm"] == 22.0 and spec.location["z"] == 3994.0


def test_plate_dimension_keys_used_here_match_the_keys_the_7d_adapter_actually_requires():
    """Pins review_module's plate-key list to real_connection_adapter (no silent drift)."""
    assert review_module._PLATE_DIMENSION_KEYS == ("width_mm", "depth_mm", "thickness_mm")
    for missing_key in review_module._PLATE_DIMENSION_KEYS:
        record = make_7e_connection_record()
        del record["plates"][0][missing_key]
        with pytest.raises(GeometryValidationError, match=missing_key):
            real_connection_to_validated_connection(record)
    assert real_connection_to_validated_connection(make_7e_connection_record()).plates[0]["width"] == 180.0


# =============================================================================
# Reviewer-supplied member marks are validated against the known project members
# too (7W correction). Supplying a value establishes provenance, not project identity.
# =============================================================================
_KNOWN = ["A", "B"]


def _attachments_for(marks):
    """SYNTHETIC attachments for whatever marks are under test (first END, second START)."""
    marks = marks if isinstance(marks, (list, tuple)) else ["A", "B"]  # malformed marks under test: any valid attachments
    return [{"member_mark": m, "surface_reference": ("END", "START")[i % 2]} for i, m in enumerate(marks)]


def _supplied_marks_package(marks, known=_KNOWN, ai_members=("A", "B"), confirm=("plate",)):
    raw = make_ai_raw(connects_members=None if ai_members is None else list(ai_members))
    sup = make_reviewer_supplement(
        connected_member_marks=marks, attachments=_attachments_for(marks),
        confirmed_ai_fields=frozenset(confirm),
    )
    return package(raw, sup, known=known)


def test_the_exact_defect_pattern_reviewer_supplied_ghost_mark_blocks_readiness():
    pkg = _supplied_marks_package(["A", "GHOST"])  # known members: ["A", "B"]
    report = build_review_report(pkg)
    result = attempt_7v_completeness(pkg)

    # 1. GHOST is identified as unknown — in the report field and in the issues.
    assert report.unknown_member_marks == ("GHOST",)
    issue = next(i for i in report.issues if i.code == "UNKNOWN_MEMBER_REFERENCE")
    assert issue.field == "connected_member_marks" and "GHOST" in issue.message
    # 2. Not READY_FOR_PIPELINE.
    assert report.state == REVIEW_STATE_REVIEW_REQUIRED and report.ready_for_pipeline is False
    # 3. Present in the review-gap representation (entry note as well as issues).
    assert "GHOST" in entry(report, "connected_member_marks").note
    # 4. No downstream-ready objects are produced.
    assert result.is_complete is False and "GHOST" in result.error
    assert result.validated_connection is None and result.connection_location is None
    assert result.attachments is None and report.completeness.is_complete is False
    # 5. Provenance is not altered merely because the mark is invalid; nothing is removed or replaced.
    e = entry(report, "connected_member_marks")
    assert e.provenance == PROVENANCE_HUMAN_SUPPLEMENTED and e.category == CATEGORY_HUMAN_SUPPLEMENTED
    assert report.provenance["connected_member_marks"] == PROVENANCE_HUMAN_SUPPLEMENTED
    assert result.provenance["connected_member_marks"] == PROVENANCE_HUMAN_SUPPLEMENTED
    assert build_reviewed_connection_specification(pkg).connected_member_marks == ["A", "GHOST"]


def test_7v_is_unchanged_and_would_accept_the_same_specification_so_the_gate_lives_in_7w():
    """Documents where the check is: 7V validates a specification, not project identity."""
    spec = build_reviewed_connection_specification(_supplied_marks_package(["A", "GHOST"]))
    assert check_reviewed_connection_specification_completeness(spec).is_complete is True
    assert attempt_7v_completeness(_supplied_marks_package(["A", "GHOST"])).is_complete is False


def test_all_human_valid_member_marks_are_still_ready():
    pkg = _supplied_marks_package(["A", "B"])
    report = build_review_report(pkg)
    assert report.unknown_member_marks == () and report.ready_for_pipeline is True
    assert report.state == REVIEW_STATE_READY_FOR_PIPELINE
    assert attempt_7v_completeness(pkg).is_complete is True
    assert entry(report, "connected_member_marks").provenance == PROVENANCE_HUMAN_SUPPLEMENTED


@pytest.mark.parametrize("marks, confirm", [
    (None, ("connected_member_marks", "plate")),   # AI members confirmed by the reviewer (HUMAN_REVIEWED)
    (["A", "B"], ("plate",)),                      # reviewer-supplied members, AI plate confirmed
    (["B", "A"], ("plate",)),                      # order carries no meaning
])
def test_mixed_ai_and_human_valid_marks_are_still_ready(marks, confirm):
    raw = make_ai_raw(connects_members=["A", "B"])
    sup = make_reviewer_supplement(connected_member_marks=marks, attachments=_attachments_for(["A", "B"]),
                                   confirmed_ai_fields=frozenset(confirm))
    report = build_review_report(package(raw, sup, known=_KNOWN))
    assert report.unknown_member_marks == () and report.ready_for_pipeline is True
    assert report.provenance["plate"] == PROVENANCE_HUMAN_REVIEWED


def test_mixed_ai_and_human_with_one_unknown_mark_is_not_ready():
    # AI plate confirmed (HUMAN_REVIEWED) + reviewer-supplied members with one unknown mark.
    report = build_review_report(_supplied_marks_package(["A", "GHOST"]))
    assert report.provenance["plate"] == PROVENANCE_HUMAN_REVIEWED  # unrelated provenance untouched
    assert report.unknown_member_marks == ("GHOST",) and report.ready_for_pipeline is False


def test_human_only_unknown_mark_is_not_ready():
    """The AI extracted no members at all; the reviewer supplied them, and one is unknown."""
    pkg = _supplied_marks_package(["GHOST", "B"], ai_members=None)
    report = build_review_report(pkg)
    e = entry(report, "connected_member_marks")
    assert e.extraction_status == EXTRACTION_STATUS_AI_NOT_EXTRACTED and e.ai_value is None
    assert e.provenance == PROVENANCE_HUMAN_SUPPLEMENTED
    assert report.unknown_member_marks == ("GHOST",) and report.ready_for_pipeline is False


def test_multiple_unknown_human_marks_are_all_reported():
    report = build_review_report(_supplied_marks_package(["GHOST1", "A", "GHOST2"]))
    assert report.unknown_member_marks == ("GHOST1", "GHOST2")
    message = next(i.message for i in report.issues if i.code == "UNKNOWN_MEMBER_REFERENCE")
    assert "GHOST1" in message and "GHOST2" in message and "'A'" not in message
    assert report.ready_for_pipeline is False


def test_a_repeated_unknown_mark_is_reported_once_in_first_occurrence_order():
    report = build_review_report(_supplied_marks_package(["Z", "GHOST", "Z", "GHOST", "A"]))
    assert report.unknown_member_marks == ("Z", "GHOST")


@pytest.mark.parametrize("bad", ["", " ", None, 7])
def test_blank_or_non_string_human_member_marks_remain_invalid(bad):
    pkg = _supplied_marks_package(["A", bad])
    report = build_review_report(pkg)
    assert report.ready_for_pipeline is False
    assert bad in report.unknown_member_marks  # a blank / non-string mark is not a known member
    assert attempt_7v_completeness(pkg).is_complete is False
    # ...and without known marks 7V's own checks still reject it (blank marks were never valid).
    assert attempt_7v_completeness(_supplied_marks_package(["A", bad], known=None)).is_complete is False


def test_ai_unknown_member_behaviour_is_unchanged():
    raw = make_ai_raw(connects_members=["A", "GHOST-9"])
    sup = make_reviewer_supplement(confirmed_ai_fields=frozenset({"connected_member_marks", "plate"}))
    pkg = package(raw, sup, known=_KNOWN)
    report = build_review_report(pkg)
    e = entry(report, "connected_member_marks")

    assert {(i.code, i.field) for i in report.issues} >= {
        ("UNKNOWN_MEMBER_REFERENCE", "connected_member_marks"), ("CANNOT_CONFIRM", "connected_member_marks")}
    assert e.category == CATEGORY_MISSING and e.current_value is None and e.provenance is None
    assert e.ai_value == ["A", "GHOST-9"]
    assert report.unknown_member_marks == ("GHOST-9",)
    assert build_reviewed_connection_specification(pkg).connected_member_marks == []  # never pushed into the spec
    assert report.ready_for_pipeline is False


def test_valid_ai_members_confirmed_by_the_reviewer_are_ready_against_known_members():
    raw = make_ai_raw(connects_members=["A", "B"])
    sup = make_reviewer_supplement(attachments=_attachments_for(["A", "B"]))  # confirms members + plate
    report = build_review_report(package(raw, sup, known=_KNOWN))
    assert report.ready_for_pipeline is True
    assert report.provenance["connected_member_marks"] == PROVENANCE_HUMAN_REVIEWED


def test_without_known_member_marks_nothing_can_be_validated_and_an_advisory_says_so():
    report = build_review_report(_supplied_marks_package(["A", "GHOST"], known=None))
    assert report.unknown_member_marks == ()
    assert "member_references" in report.advisories
    assert "reviewer-supplied" in report.advisories["member_references"]
    assert report.ready_for_pipeline is True  # nothing to resolve against; 7V's own rules are met


def test_an_empty_known_member_set_makes_every_mark_unknown():
    report = build_review_report(_supplied_marks_package(["A", "B"], known=[]))
    assert report.unknown_member_marks == ("A", "B") and report.ready_for_pipeline is False


@pytest.mark.parametrize("marks", [["A ", "B"], ["a", "B"], ["A\t", "B"]])
def test_matching_is_exact_nothing_is_stripped_or_case_folded(marks):
    report = build_review_report(_supplied_marks_package(marks))
    assert report.unknown_member_marks == (marks[0],) and report.ready_for_pipeline is False


@pytest.mark.parametrize("malformed", ["A", 5, [["A"], "B"], {"A": 1}])
def test_malformed_supplied_marks_never_crash_and_are_never_ready(malformed):
    report = build_review_report(_supplied_marks_package(malformed))  # no TypeError from unhashable elements
    assert report.ready_for_pipeline is False


def test_a_blocked_result_reports_7vs_own_error_too_when_both_apply():
    sup = make_reviewer_supplement(connected_member_marks=["A", "GHOST"], attachments=_attachments_for(["A", "GHOST"]),
                                   position=None, confirmed_ai_fields=frozenset({"plate"}))
    result = attempt_7v_completeness(package(make_ai_raw(), sup, known=_KNOWN))
    assert result.is_complete is False and "GHOST" in result.error and "7V also reports" in result.error


def test_unknown_marks_never_change_any_other_fields_provenance():
    report = build_review_report(_supplied_marks_package(["A", "GHOST"]))
    assert report.provenance == {
        "connected_member_marks": PROVENANCE_HUMAN_SUPPLEMENTED, "position": PROVENANCE_HUMAN_SUPPLEMENTED,
        "plate": PROVENANCE_HUMAN_REVIEWED, "holes": PROVENANCE_HUMAN_SUPPLEMENTED,
        "location": PROVENANCE_HUMAN_SUPPLEMENTED, "attachments": PROVENANCE_HUMAN_SUPPLEMENTED,
    }


def test_the_project_member_check_is_deterministic_and_mutates_nothing():
    pkg = _supplied_marks_package(["A", "GHOST"])
    before = copy.deepcopy(pkg)
    first, second = build_review_report(pkg), build_review_report(pkg)
    assert first == second and pkg == before
    assert attempt_7v_completeness(pkg) == attempt_7v_completeness(pkg)


def test_readiness_meaning_names_the_project_member_requirement_without_claiming_approval():
    assert "known project member" in READY_FOR_PIPELINE_MEANING
    assert "not engineering approval" in READY_FOR_PIPELINE_MEANING
    assert build_review_report(_supplied_marks_package(["A", "B"])).state_meaning == READY_FOR_PIPELINE_MEANING


# =============================================================================
# Audit trail (7W correction): a reviewer may correct an AI extraction error, and the package is then
# ready — but the historical AI problem must not disappear because it was corrected.
#   final value          -> reviewer's marks (what 7V / CAD see)
#   historical AI issue  -> report.unknown_member_marks + a non-blocking AI_UNKNOWN_MEMBER_CORRECTED issue
#   current problems     -> report.current_unknown_member_marks (only these block readiness)
# =============================================================================
def _codes(report):
    return {i.code for i in report.issues}


def test_ai_unknown_mark_corrected_by_the_reviewer_is_ready_and_still_traceable():
    pkg = _supplied_marks_package(["A", "B"], ai_members=("A", "GHOST"))  # AI: [A, GHOST]  reviewer: [A, B]
    before = copy.deepcopy(pkg)
    report = build_review_report(pkg)
    result = attempt_7v_completeness(pkg)
    spec = build_reviewed_connection_specification(pkg)
    e = entry(report, "connected_member_marks")

    # 1. still ready.
    assert report.ready_for_pipeline is True and report.state == REVIEW_STATE_READY_FOR_PIPELINE
    # 2. the final connected-member marks are the reviewer's.
    assert spec.connected_member_marks == ["A", "B"]
    assert result.validated_connection.connected_members == ["A", "B"]
    # 3. GHOST is not in the final specification, the validated connection, or any downstream object.
    assert "GHOST" not in repr(spec)
    assert "GHOST" not in repr(result.validated_connection) and "GHOST" not in repr(result.attachments)
    assert {a.member_mark for a in result.attachments} == {"A", "B"}
    # 4. GHOST remains in the review/audit information.
    assert report.unknown_member_marks == ("GHOST",)
    corrected = [i for i in report.issues if i.code == "AI_UNKNOWN_MEMBER_CORRECTED"]
    assert len(corrected) == 1 and corrected[0].field == "connected_member_marks" and "GHOST" in corrected[0].message
    assert "GHOST" in e.note
    # 5. The original AI value stays AI-originated; the final value's provenance is the reviewer's.
    assert pkg.extraction.connected_member_references == ("A", "GHOST")
    assert e.extraction_status == EXTRACTION_STATUS_AI_EXTRACTED and e.ai_value == ["A", "GHOST"]
    assert e.provenance == PROVENANCE_HUMAN_SUPPLEMENTED and e.current_value == ["A", "B"]
    assert PROVENANCE_AI_EXTRACTED not in report.provenance.values()  # the AI's bad value labels nothing final
    # 6. No false current unknown-member blocking issue remains.
    assert report.current_unknown_member_marks == ()
    assert "UNKNOWN_MEMBER_REFERENCE" not in _codes(report) and "CANNOT_CONFIRM" not in _codes(report)
    assert report.completeness.is_complete is True and report.completeness.error is None
    # ...and building the report changed nothing.
    assert pkg == before


def test_the_corrected_package_yields_exactly_the_same_specification_as_a_clean_one():
    """The historical AI error is audit information only: it cannot leak into what 7V receives."""
    corrected = _supplied_marks_package(["A", "B"], ai_members=("A", "GHOST"))
    clean = _supplied_marks_package(["A", "B"], ai_members=("A", "B"))
    assert build_reviewed_connection_specification(corrected) == build_reviewed_connection_specification(clean)
    assert attempt_7v_completeness(corrected) == attempt_7v_completeness(clean)
    assert build_review_report(corrected).ready_for_pipeline is build_review_report(clean).ready_for_pipeline is True


@pytest.mark.parametrize("reviewer_marks", [["A", "B"], ["B", "A"]])
def test_correction_is_independent_of_the_order_of_the_reviewers_marks(reviewer_marks):
    report = build_review_report(_supplied_marks_package(reviewer_marks, ai_members=("A", "GHOST")))
    assert report.ready_for_pipeline is True and report.unknown_member_marks == ("GHOST",)


def test_ai_unknown_mark_with_reviewer_decisions_but_no_correction_is_review_required():
    pkg = _supplied_marks_package(None, ai_members=("A", "GHOST"), confirm=("plate", "connected_member_marks"))
    report = build_review_report(pkg)
    assert report.state == REVIEW_STATE_REVIEW_REQUIRED and report.ready_for_pipeline is False
    assert report.unknown_member_marks == ("GHOST",) and report.current_unknown_member_marks == ("GHOST",)
    assert {"UNKNOWN_MEMBER_REFERENCE", "CANNOT_CONFIRM"} <= _codes(report)
    assert "AI_UNKNOWN_MEMBER_CORRECTED" not in _codes(report)  # nothing was corrected


def test_ai_unknown_mark_with_no_reviewer_decisions_at_all_stays_ai_extracted_and_not_ready():
    report = build_review_report(package(make_ai_raw(connects_members=["A", "GHOST"]), known=_KNOWN))
    assert report.state == REVIEW_STATE_AI_EXTRACTED and report.ready_for_pipeline is False
    assert report.unknown_member_marks == report.current_unknown_member_marks == ("GHOST",)


def test_reviewer_supplied_unknown_mark_is_still_review_required_and_is_current():
    report = build_review_report(_supplied_marks_package(["A", "GHOST"]))
    assert report.state == REVIEW_STATE_REVIEW_REQUIRED
    assert report.unknown_member_marks == report.current_unknown_member_marks == ("GHOST",)
    assert "UNKNOWN_MEMBER_REFERENCE" in _codes(report) and "AI_UNKNOWN_MEMBER_CORRECTED" not in _codes(report)


def test_valid_ai_marks_and_valid_reviewer_marks_are_ready_with_nothing_to_audit():
    report = build_review_report(_supplied_marks_package(["A", "B"], ai_members=("A", "B")))
    assert report.ready_for_pipeline is True
    assert report.unknown_member_marks == () and report.current_unknown_member_marks == ()
    assert not _codes(report) & {"UNKNOWN_MEMBER_REFERENCE", "AI_UNKNOWN_MEMBER_CORRECTED", "CANNOT_CONFIRM"}


def test_multiple_ai_unknown_marks_corrected_by_the_reviewer_are_all_retained():
    pkg = _supplied_marks_package(["A", "B"], ai_members=("G1", "A", "G2", "G1"))
    report = build_review_report(pkg)
    assert report.ready_for_pipeline is True
    assert report.unknown_member_marks == ("G1", "G2")  # first-occurrence order, de-duplicated
    assert report.current_unknown_member_marks == ()
    message = next(i.message for i in report.issues if i.code == "AI_UNKNOWN_MEMBER_CORRECTED")
    assert "G1" in message and "G2" in message
    assert build_reviewed_connection_specification(pkg).connected_member_marks == ["A", "B"]


def test_correcting_one_unknown_with_another_keeps_both_but_only_the_new_one_is_current():
    report = build_review_report(_supplied_marks_package(["A", "GHOST2"], ai_members=("A", "GHOST")))
    assert report.ready_for_pipeline is False
    assert report.unknown_member_marks == ("GHOST", "GHOST2")   # historical: both
    assert report.current_unknown_member_marks == ("GHOST2",)   # current: only the reviewer's
    assert {"AI_UNKNOWN_MEMBER_CORRECTED", "UNKNOWN_MEMBER_REFERENCE"} <= _codes(report)


def test_confirming_and_supplying_together_leaves_the_ai_unknown_mark_current():
    sup = make_reviewer_supplement(connected_member_marks=["A", "B"], attachments=_attachments_for(["A", "B"]),
                                   confirmed_ai_fields=frozenset({"plate", "connected_member_marks"}))
    report = build_review_report(package(make_ai_raw(connects_members=["A", "GHOST"]), sup, known=_KNOWN))
    assert "CONFIRM_AND_SUPPLY_CONFLICT" in _codes(report)
    assert report.current_unknown_member_marks == ("GHOST",) and report.ready_for_pipeline is False


def test_without_known_member_marks_no_historical_unknown_can_be_recorded():
    report = build_review_report(_supplied_marks_package(["A", "B"], known=None, ai_members=("A", "GHOST")))
    assert report.ready_for_pipeline is True
    assert report.unknown_member_marks == () and "AI_UNKNOWN_MEMBER_CORRECTED" not in _codes(report)
    assert "member_references" in report.advisories  # honest: the marks were never checked


def test_the_audit_trail_never_mutates_the_extraction_or_the_raw_record():
    raw = make_ai_raw(connects_members=["A", "GHOST"])
    raw_before = copy.deepcopy(raw)
    pkg = package(raw, make_reviewer_supplement(connected_member_marks=["A", "B"],
                                                attachments=_attachments_for(["A", "B"]),
                                                confirmed_ai_fields=frozenset({"plate"})), known=_KNOWN)
    extraction_before, supplement_before = copy.deepcopy(pkg.extraction), copy.deepcopy(pkg.supplement)
    first, second = build_review_report(pkg), build_review_report(pkg)
    assert first == second
    assert raw == raw_before and pkg.extraction == extraction_before and pkg.supplement == supplement_before
    assert pkg.extraction.connected_member_references == ("A", "GHOST")  # the historical AI fact is untouched


def test_a_corrected_package_enters_the_existing_pipeline_and_ghost_never_reaches_the_drawing(tmp_path):
    raw = make_ai_raw(connects_members=["REAL-UB-CAD-001", "GHOST"])
    pkg = package(raw, make_reviewer_supplement(connected_member_marks=["REAL-UB-CAD-001", "L2"],
                                                confirmed_ai_fields=frozenset({"plate"})),
                  known=["REAL-UB-CAD-001", "L2"])
    report = build_review_report(pkg)
    assert report.ready_for_pipeline is True and report.unknown_member_marks == ("GHOST",)

    out = _run_pipeline(pkg, tmp_path)
    text = pypdf.PdfReader(out).pages[0].extract_text()
    assert "REAL-UB-CAD-001" in text and "L2" in text and "GHOST" not in text
