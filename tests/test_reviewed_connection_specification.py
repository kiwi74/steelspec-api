"""
Milestone 7V — tests for the authoritative reviewed-connection
specification contract (app.cad_engine.reviewed_connection_specification)
and its completeness check.

FIXTURE PROVENANCE (corrected in the 7V correction pass — read this
before trusting any value below):

  - `make_synthetic_test_spec()` is SYNTHETIC / TEST-ONLY data. Its
    values (180x250x12mm end plate, 4xO22mm holes at 90mm H / 140mm V
    spacing, position=END, project-space location z=3994, attachments
    REAL-UB-CAD-001->END / L2->START) were chosen in earlier milestones
    (7D/7E/7K/7N) purely so the software path could be exercised. The
    plate numbers coincide with the ILLUSTRATIVE example JSON inside the
    AI extraction prompt; the repository contains NO source evidence
    (tests/test_arkles_strand.py holds member rows only, no connection
    data) that any of these values come from an actual Arkles drawing.
    They are NOT verified engineering data, and nothing here proves they
    are correct engineering decisions.
  - The HUMAN_REVIEWED / HUMAN_SUPPLEMENTED provenance labels and
    review_status="approved" on that fixture describe the software path
    being exercised (what a reviewer's sign-off would look like to the
    code). No real reviewer reviewed these values.
  - `AI_EXAMPLE_CONNECTION` IS copied verbatim from
    app/ai_analysis/pdf_vision_analyzer.py's own EXTRACTION_SYSTEM_PROMPT
    example connection object (asserted against that file's text below),
    so the gaps it demonstrates are the actual current AI schema's gaps.
    It is itself an illustrative prompt example, not extracted data.
  - Members: mark/section/row SHAPE follow the real steel_members row
    contract (7A) and are built through the unmodified real member
    adapter. "REAL-UB-CAD-001" is a synthetic test row (its
    source_drawing_id is "REAL-CAD-TEST"); "REAL" there names the row
    shape, not Arkles data. L2/250PFC (mark and section) come from a real
    Arkles extraction row (7G); both members' lengths are supplemented
    test values.

This file never hand-builds a ValidatedSteelMember or ValidatedConnection.
"""
from pathlib import Path

import copy

import pypdf
import pytest

from app.cad_engine.assembly import PlacedMember
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import place_member_geometry
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_assembly import build_reviewed_two_member_connection_assembly
from app.cad_engine.reviewed_connection_drawing_gate import generate_fabrication_drawing_from_reviewed_assembly
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    REQUIRED_PROVENANCE_FIELDS,
    REVIEW_STATUS_APPROVED,
    SUPPORTED_PROVENANCE_LABELS,
    ReviewedConnectionSpecification,
    check_reviewed_connection_specification_completeness,
    reviewed_connection_specification_to_connection_record,
    reviewed_connection_specification_to_detail_record,
    reviewed_connection_specification_to_location_record,
)
from app.cad_engine.reviewed_connection_validation_gate import validate_reviewed_connection_assembly
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher

import app.cad_engine.reviewed_connection_specification as spec_module

# Copied verbatim from pdf_vision_analyzer.EXTRACTION_SYSTEM_PROMPT's own
# example JSON "connections" entry — proof this file uses the actual
# current AI schema shape, not a reconstruction of it. Read directly from
# the source file's text (never `import app.ai_analysis.pdf_vision_analyzer`,
# which pulls in app.config -> the unrelated, pre-existing SUPABASE_URL
# environment dependency every other CAD-engine test file also avoids).
_PDF_VISION_ANALYZER_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "ai_analysis" / "pdf_vision_analyzer.py"
).read_text()
assert '"connects_members": ["B8", "C1"]' in _PDF_VISION_ANALYZER_SOURCE
assert '"bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}]' in _PDF_VISION_ANALYZER_SOURCE
assert '"plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}]' in \
    _PDF_VISION_ANALYZER_SOURCE

AI_EXAMPLE_CONNECTION = {
    "detail_reference": "D15",
    "grid_reference": "A-B/2",
    "connects_members": ["B8", "C1"],
    "connection_type": "bolted",
    "bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}],
    "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
    "welds": [{"type": "fillet", "size_mm": 8}],
    "confidence": 88,
}


def make_ai_only_spec(**overrides) -> ReviewedConnectionSpecification:
    """
    Everything the current AI schema could plausibly supply for
    AI_EXAMPLE_CONNECTION, PLUS the identity/member-association fields
    that only exist once persisted (connection_id, connected_member_marks
    — see real_connection_adapter.py's own docstring on this boundary)
    — but genuinely nothing else. No position, no location, no
    attachments, no numeric hole diameter: these are exactly the fields
    the current AI schema does not provide (Step 1/5).
    """
    base = dict(
        connection_id="CONN-B8-C1",
        review_status="extracted",  # the repo's own status for raw, unreviewed pipeline output
        connected_member_marks=list(AI_EXAMPLE_CONNECTION["connects_members"]),
        plate=dict(AI_EXAMPLE_CONNECTION["plates"][0]),
        holes=dict(AI_EXAMPLE_CONNECTION["bolts"][0]),  # quantity/size/grade only — no diameter_mm
        provenance={"connected_member_marks": PROVENANCE_AI_EXTRACTED, "plate": PROVENANCE_AI_EXTRACTED,
                    "holes": PROVENANCE_AI_EXTRACTED},
    )
    base.update(overrides)
    return ReviewedConnectionSpecification(**base)


def make_synthetic_test_spec(**overrides) -> ReviewedConnectionSpecification:
    """
    SYNTHETIC / TEST-ONLY fixture (see module docstring): every value is test
    data chosen to exercise the software path — not verified Arkles data and
    not a verified engineering decision. The provenance labels and
    review_status="approved" describe the software path, not a real review.
    """
    base = dict(
        connection_id="CONN-REAL-UB-CAD-001-L2",
        review_status="approved",
        connected_member_marks=["REAL-UB-CAD-001", "L2"],
        position="END",
        plate={"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250},
        holes={"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0},
        location={"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
        attachments=[
            {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
            {"member_mark": "L2", "surface_reference": "START"},
        ],
        provenance={
            "connected_member_marks": PROVENANCE_HUMAN_REVIEWED,
            "position": PROVENANCE_HUMAN_SUPPLEMENTED,
            "plate": PROVENANCE_HUMAN_REVIEWED,
            "holes": PROVENANCE_HUMAN_REVIEWED,
            "location": PROVENANCE_HUMAN_SUPPLEMENTED,
            "attachments": PROVENANCE_HUMAN_SUPPLEMENTED,
        },
    )
    base.update(overrides)
    return ReviewedConnectionSpecification(**base)


def _build_placed_members():
    matcher = make_multi_member_matcher()
    validated_a = real_member_to_validated_member(make_member_a_row(), matcher)
    validated_b = real_member_to_validated_member(make_member_b_row(), matcher)
    geometry_a = generate_geometry(validated_a)
    geometry_b = generate_geometry(validated_b)
    placement_a = make_member_a_placement()
    placement_b = make_member_b_placement()
    placed_a = PlacedMember(
        mark=validated_a.mark, geometry=place_member_geometry(geometry_a, placement_a), placement=placement_a,
    )
    placed_b = PlacedMember(
        mark=validated_b.mark, geometry=place_member_geometry(geometry_b, placement_b), placement=placement_b,
    )
    return placed_a, placed_b


def _pdf_circles_and_paths(path):
    reader = pypdf.PdfReader(path)
    content = reader.pages[0].get_contents().get_data().decode("latin-1")
    curve_ops = content.count(" c\n") + content.count(" c ")
    line_ops = content.count(" l\n") + content.count(" l ")
    return curve_ops, line_ops


# === 1. Missing position -> rejected ===
def test_missing_position_rejected():
    spec = make_synthetic_test_spec(position=None)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "position" in result.error


# === 2. Missing project-space location -> rejected ===
def test_missing_location_rejected():
    spec = make_synthetic_test_spec(location=None)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "location field" in result.error


# === 3. Missing plate thickness -> rejected ===
def test_missing_plate_thickness_rejected():
    plate = {"type": "end_plate", "width_mm": 180, "depth_mm": 250}  # no thickness_mm
    spec = make_synthetic_test_spec(plate=plate)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "thickness_mm" in result.error


# === 4. Missing hole diameter -> rejected ===
def test_missing_hole_diameter_rejected():
    holes = {"quantity": 4, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}  # no diameter_mm
    spec = make_synthetic_test_spec(holes=holes)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "diameter_mm" in result.error


# === 5. Missing attachment -> rejected ===
def test_missing_attachment_rejected():
    spec = make_synthetic_test_spec(attachments=[{"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"}])
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "do not exactly cover" in result.error
    # The connection/location layers DID succeed — only the attachment layer failed.
    assert result.validated_connection is not None
    assert result.connection_location is not None
    assert result.attachments is None


# === 6. Unknown attachment surface -> rejected ===
def test_unknown_attachment_surface_rejected():
    spec = make_synthetic_test_spec(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "MIDDLE"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "unsupported surface_reference" in result.error


# === 7. Unsupported connection type -> rejected ===
def test_unsupported_connection_type_rejected():
    plate = {"type": "gusset", "width_mm": 180, "depth_mm": 250, "thickness_mm": 12}
    spec = make_synthetic_test_spec(plate=plate)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "does not correspond to a supported connection type" in result.error


# === 8. Missing connected member -> rejected ===
def test_missing_connected_member_rejected():
    spec = make_synthetic_test_spec(connected_member_marks=[])
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "no member association is recorded" in result.error


# === 9. AI-only incomplete record cannot enter the CAD pipeline ===
def test_ai_only_record_cannot_enter_pipeline():
    spec = make_ai_only_spec()
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    # Nothing pipeline-usable exists for an unreviewed specification.
    assert result.validated_connection is None
    assert result.connection_location is None
    assert result.attachments is None
    assert "review_status" in result.error  # rejected because it is unreviewed, first of all
    # Provenance is retained on the result even though the spec was rejected.
    assert result.provenance == {
        "connected_member_marks": PROVENANCE_AI_EXTRACTED, "plate": PROVENANCE_AI_EXTRACTED,
        "holes": PROVENANCE_AI_EXTRACTED,
    }


def test_ai_only_record_is_rejected_even_if_someone_relabels_only_the_status():
    """review_status="approved" alone does not launder AI-extracted content."""
    spec = make_ai_only_spec(review_status="approved")
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert result.validated_connection is None
    assert "provenance" in result.error


def test_nominal_bolt_size_is_not_a_hole_diameter():
    """
    The M20 distinction, proven at the adapter level: everything else is
    reviewed/approved, but the holes are still the AI's nominal bolt
    designation — so the existing adapter refuses it.
    """
    spec = make_synthetic_test_spec(holes=dict(AI_EXAMPLE_CONNECTION["bolts"][0]))  # quantity/size/grade
    assert spec.holes["size"] == "M20"
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "diameter_mm" in result.error
    assert "M20" in result.error or "nominal bolt size" in result.error
    # Conversion never invents a diameter from the nominal size.
    assert "diameter_mm" not in reviewed_connection_specification_to_connection_record(spec)["bolts"][0]


# === 10. HUMAN-REVIEWED/SUPPLEMENTED complete record enters the existing pipeline ===
def test_complete_record_enters_existing_pipeline_and_produces_drawing(tmp_path):
    spec = make_synthetic_test_spec()
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is True
    assert result.error is None

    placed_a, placed_b = _build_placed_members()
    attachment_a = next(a for a in result.attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in result.attachments if a.member_mark == "L2")

    assembly = build_reviewed_two_member_connection_assembly(
        placed_a, placed_b, result.validated_connection, result.connection_location, attachment_a, attachment_b,
    )
    validate_reviewed_connection_assembly(assembly)  # 7R passes

    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")
    assert out_path.exists()
    text = pypdf.PdfReader(out_path).pages[0].extract_text()
    assert "REAL-UB-CAD-001" in text and "L2" in text
    assert "CONNECTION DETAIL" in text
    curve_ops, _ = _pdf_circles_and_paths(out_path)
    assert curve_ops == 16  # 4 real holes x 4 bezier curves


# === 11. No defaults are silently introduced ===
def test_no_defaults_silently_introduced():
    # rotation_z omitted entirely — must be rejected, never treated as 0.0.
    location = {"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0}
    spec = make_synthetic_test_spec(location=location)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "rotation_z" in result.error

    # Confirm the conversion function itself never fabricates a default —
    # it hands through exactly None for the missing field.
    record = reviewed_connection_specification_to_location_record(spec)
    assert record["rotation_z"] is None


# === 12. No mutation of the source specification ===
def test_no_mutation_of_source_specification():
    spec = make_synthetic_test_spec()
    plate_before = dict(spec.plate)
    holes_before = dict(spec.holes)
    location_before = dict(spec.location)
    attachments_before = [dict(a) for a in spec.attachments]
    marks_before = list(spec.connected_member_marks)
    provenance_before = dict(spec.provenance)

    check_reviewed_connection_specification_completeness(spec)
    reviewed_connection_specification_to_connection_record(spec)
    reviewed_connection_specification_to_location_record(spec)
    reviewed_connection_specification_to_detail_record(spec)

    assert spec.plate == plate_before
    assert spec.holes == holes_before
    assert spec.location == location_before
    assert spec.attachments == attachments_before
    assert spec.connected_member_marks == marks_before
    assert spec.provenance == provenance_before


# === 13. Repeated conversion produces the same result ===
def test_repeated_conversion_produces_same_result():
    spec = make_synthetic_test_spec()

    result_1 = check_reviewed_connection_specification_completeness(spec)
    result_2 = check_reviewed_connection_specification_completeness(spec)

    assert result_1.is_complete == result_2.is_complete is True
    assert result_1.validated_connection == result_2.validated_connection
    assert result_1.connection_location == result_2.connection_location
    assert result_1.attachments == result_2.attachments

    record_1 = reviewed_connection_specification_to_connection_record(spec)
    record_2 = reviewed_connection_specification_to_connection_record(spec)
    assert record_1 == record_2


# === 14. Field/order changes do not create positional semantics ===
def test_member_and_attachment_order_does_not_create_positional_semantics():
    spec_ab = make_synthetic_test_spec()
    spec_ba = make_synthetic_test_spec(
        connected_member_marks=["L2", "REAL-UB-CAD-001"],
        attachments=[
            {"member_mark": "L2", "surface_reference": "START"},
            {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        ],
    )

    result_ab = check_reviewed_connection_specification_completeness(spec_ab)
    result_ba = check_reviewed_connection_specification_completeness(spec_ba)

    assert result_ab.is_complete is result_ba.is_complete is True
    assert set(result_ab.validated_connection.connected_members) == \
        set(result_ba.validated_connection.connected_members)
    marks_ab = {a.member_mark: a.surface_reference for a in result_ab.attachments}
    marks_ba = {a.member_mark: a.surface_reference for a in result_ba.attachments}
    assert marks_ab == marks_ba == {"REAL-UB-CAD-001": "END", "L2": "START"}


# === 15. Provenance is preserved through conversion, and has no geometric effect ===
def test_provenance_preserved_through_conversion():
    spec = make_synthetic_test_spec()
    provenance_before = dict(spec.provenance)

    check_reviewed_connection_specification_completeness(spec)
    reviewed_connection_specification_to_connection_record(spec)

    assert spec.provenance == provenance_before  # never stripped, never mutated
    # None of the three converted records carry provenance at all — it is
    # not a field the existing adapters know about or need to.
    connection_record = reviewed_connection_specification_to_connection_record(spec)
    location_record = reviewed_connection_specification_to_location_record(spec)
    detail_record = reviewed_connection_specification_to_detail_record(spec)
    assert "provenance" not in connection_record
    assert "provenance" not in location_record
    assert "provenance" not in detail_record


def test_provenance_labels_do_not_alter_numeric_geometry():
    """
    HUMAN_REVIEWED vs HUMAN_SUPPLEMENTED (both acceptable) on every field:
    provenance is validated and retained, but it never reaches the adapters —
    the validated connection, location and attachments are identical.
    """
    reviewed = {name: PROVENANCE_HUMAN_REVIEWED for name in REQUIRED_PROVENANCE_FIELDS}
    supplemented = {name: PROVENANCE_HUMAN_SUPPLEMENTED for name in REQUIRED_PROVENANCE_FIELDS}

    result_reviewed = check_reviewed_connection_specification_completeness(
        make_synthetic_test_spec(provenance=reviewed))
    result_supplemented = check_reviewed_connection_specification_completeness(
        make_synthetic_test_spec(provenance=supplemented))

    assert result_reviewed.is_complete is result_supplemented.is_complete is True
    assert result_reviewed.validated_connection == result_supplemented.validated_connection
    assert result_reviewed.connection_location == result_supplemented.connection_location
    assert result_reviewed.attachments == result_supplemented.attachments
    # ...while the provenance itself is retained faithfully, per result.
    assert result_reviewed.provenance == reviewed
    assert result_supplemented.provenance == supplemented


def _track_adapter(monkeypatch, name, order=None):
    """Wraps a real production call site in spec_module, recording args and real return values."""
    calls, returned = [], []
    original = getattr(spec_module, name)

    def tracking(*args):
        calls.append(args)
        if order is not None:
            order.append(name)
        out = original(*args)
        returned.append(out)
        return out

    monkeypatch.setattr(spec_module, name, tracking)
    return calls, returned


# === Adapter authority (correction pass Step 6): all THREE adapters, at the real call sites ===
def test_completeness_invokes_real_connection_adapter_with_converted_record(monkeypatch):
    calls, returned = _track_adapter(monkeypatch, "real_connection_to_validated_connection")
    spec = make_synthetic_test_spec()
    result = check_reviewed_connection_specification_completeness(spec)

    assert len(calls) == 1
    assert calls[0] == (reviewed_connection_specification_to_connection_record(spec),)
    assert result.validated_connection is returned[0]  # the adapter's own output, not a look-alike


def test_completeness_invokes_real_location_adapter_with_converted_record(monkeypatch):
    calls, returned = _track_adapter(monkeypatch, "real_connection_location_to_connection_location")
    spec = make_synthetic_test_spec()
    result = check_reviewed_connection_specification_completeness(spec)

    assert len(calls) == 1
    assert calls[0] == (reviewed_connection_specification_to_location_record(spec),)
    assert result.connection_location is returned[0]


def test_completeness_invokes_real_attachment_adapter_with_converted_record(monkeypatch):
    calls, returned = _track_adapter(monkeypatch, "reviewed_connection_detail_to_attachments")
    spec = make_synthetic_test_spec()
    result = check_reviewed_connection_specification_completeness(spec)

    assert len(calls) == 1
    detail_record, connected_marks = calls[0]
    assert detail_record == reviewed_connection_specification_to_detail_record(spec)
    # The authoritative member list is the ValidatedConnection's own, not re-derived.
    assert connected_marks is result.validated_connection.connected_members
    assert result.attachments is returned[0]


def test_completeness_calls_the_three_adapters_in_order(monkeypatch):
    order = []
    for name in ("real_connection_to_validated_connection", "real_connection_location_to_connection_location",
                 "reviewed_connection_detail_to_attachments"):
        _track_adapter(monkeypatch, name, order)
    check_reviewed_connection_specification_completeness(make_synthetic_test_spec())
    assert order == ["real_connection_to_validated_connection", "real_connection_location_to_connection_location",
                     "reviewed_connection_detail_to_attachments"]


def test_no_adapter_is_called_when_review_gate_or_shape_check_fails(monkeypatch):
    order = []
    for name in ("real_connection_to_validated_connection", "real_connection_location_to_connection_location",
                 "reviewed_connection_detail_to_attachments"):
        _track_adapter(monkeypatch, name, order)

    check_reviewed_connection_specification_completeness(make_synthetic_test_spec(review_status="pending_review"))
    check_reviewed_connection_specification_completeness(make_ai_only_spec())
    check_reviewed_connection_specification_completeness(make_synthetic_test_spec(plate="end_plate"))
    check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance={}))

    assert order == []  # nothing unreviewed or malformed ever reaches an adapter


# === Adversarial: reject non-numeric / boolean / NaN / infinite location values ===
@pytest.mark.parametrize("field_name", ["x", "y", "z", "rotation_x", "rotation_y", "rotation_z"])
@pytest.mark.parametrize("bad_value", [None, "not-a-number", True, float("nan"), float("inf"), float("-inf")])
def test_non_numeric_or_non_finite_location_values_rejected(field_name, bad_value):
    location = {"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    location[field_name] = bad_value
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(location=location))
    assert result.is_complete is False
    assert field_name in result.error


# === Failure behaviour: no fallback, no partial artifact when the specification is incomplete ===
def test_incomplete_specification_never_reaches_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    calls = []
    monkeypatch.setattr(
        gate_module, "generate_connection_fabrication_drawing_pdf",
        lambda *a, **k: calls.append(True) or None,
    )

    spec = make_synthetic_test_spec(position=None)  # incomplete
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False

    # An incomplete specification never even reaches assembly construction —
    # there is no ValidatedConnection to build one from.
    assert result.validated_connection is None
    assert calls == []


# =============================================================================
# 7V CORRECTION PASS — review authority, provenance, malformed input, finiteness
# =============================================================================

# --- 1. Review-status gating (fixture is otherwise fully populated + human-labelled) ---
def test_fully_populated_but_pending_review_is_rejected():
    spec = make_synthetic_test_spec(review_status="pending_review")
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert "review_status" in result.error and "pending_review" in result.error
    assert result.validated_connection is None and result.connection_location is None
    assert result.attachments is None


@pytest.mark.parametrize("status", [None, "", "extracted", "review_required", "pending_review", "APPROVED",
                                    "approved ", 5])
def test_only_exactly_approved_review_status_passes_the_gate(status):
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(review_status=status))
    assert result.is_complete is False
    assert "review_status" in result.error


def test_approved_review_status_passes_the_gate():
    assert REVIEW_STATUS_APPROVED == "approved"
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec())
    assert result.is_complete is True


# --- 2/3/4. Provenance gating ---
def test_fully_populated_with_ai_only_provenance_is_rejected():
    ai_labels = {name: PROVENANCE_AI_EXTRACTED for name in REQUIRED_PROVENANCE_FIELDS}
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=ai_labels))
    assert result.is_complete is False
    assert "AI_EXTRACTED" in result.error
    assert result.validated_connection is None
    assert result.provenance == ai_labels  # retained, never rewritten as human provenance


@pytest.mark.parametrize("field_name", REQUIRED_PROVENANCE_FIELDS)
def test_a_single_ai_extracted_required_field_blocks_the_specification(field_name):
    provenance = dict(make_synthetic_test_spec().provenance)
    provenance[field_name] = PROVENANCE_AI_EXTRACTED
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=provenance))
    assert result.is_complete is False
    assert field_name in result.error and "AI_EXTRACTED" in result.error


def test_fully_populated_with_human_reviewed_provenance_is_complete():
    labels = {name: PROVENANCE_HUMAN_REVIEWED for name in REQUIRED_PROVENANCE_FIELDS}
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=labels))
    assert result.is_complete is True
    assert result.provenance == labels


def test_fully_populated_with_human_supplemented_provenance_is_complete():
    labels = {name: PROVENANCE_HUMAN_SUPPLEMENTED for name in REQUIRED_PROVENANCE_FIELDS}
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=labels))
    assert result.is_complete is True
    assert result.provenance == labels


def test_mixed_reviewed_and_supplemented_provenance_is_complete():
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec())
    assert result.is_complete is True
    assert PROVENANCE_HUMAN_REVIEWED in result.provenance.values()
    assert PROVENANCE_HUMAN_SUPPLEMENTED in result.provenance.values()


# --- 5. Unknown provenance label ---
@pytest.mark.parametrize("bad_label", ["MACHINE_CHECKED", "human_reviewed", "", "AI-EXTRACTED", "APPROVED"])
def test_unknown_provenance_label_is_rejected(bad_label):
    provenance = dict(make_synthetic_test_spec().provenance)
    provenance["plate"] = bad_label
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=provenance))
    assert result.is_complete is False
    assert "unknown provenance label" in result.error


def test_unknown_provenance_label_on_a_non_required_key_is_still_rejected():
    provenance = dict(make_synthetic_test_spec().provenance)
    provenance["notes"] = "BOGUS"
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=provenance))
    assert result.is_complete is False
    assert "unknown provenance label" in result.error


def test_extra_provenance_key_with_a_supported_label_is_accepted():
    provenance = dict(make_synthetic_test_spec().provenance)
    provenance["connection_id"] = PROVENANCE_HUMAN_SUPPLEMENTED  # not a required field, but a valid label
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=provenance))
    assert result.is_complete is True


def test_supported_labels_constant_is_exactly_the_three_labels():
    assert SUPPORTED_PROVENANCE_LABELS == (
        PROVENANCE_AI_EXTRACTED, PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED)


# --- 6. Missing required provenance ---
@pytest.mark.parametrize("field_name", REQUIRED_PROVENANCE_FIELDS)
def test_missing_required_provenance_is_rejected(field_name):
    provenance = dict(make_synthetic_test_spec().provenance)
    del provenance[field_name]
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance=provenance))
    assert result.is_complete is False
    assert "no provenance recorded" in result.error and field_name in result.error


def test_empty_provenance_is_rejected():
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(provenance={}))
    assert result.is_complete is False
    assert "no provenance recorded" in result.error


def test_default_empty_specification_is_incomplete_and_does_not_crash():
    result = check_reviewed_connection_specification_completeness(ReviewedConnectionSpecification())
    assert result.is_complete is False
    assert result.provenance == {}


# --- 8. Human supplementation is not silently rewritten as AI extraction (and vice versa) ---
def test_provenance_labels_are_retained_verbatim_on_the_result_as_an_independent_copy():
    spec = make_synthetic_test_spec()
    result = check_reviewed_connection_specification_completeness(spec)

    assert result.provenance == spec.provenance
    assert result.provenance["position"] == PROVENANCE_HUMAN_SUPPLEMENTED
    assert result.provenance["plate"] == PROVENANCE_HUMAN_REVIEWED
    assert PROVENANCE_AI_EXTRACTED not in result.provenance.values()

    result.provenance["position"] = PROVENANCE_AI_EXTRACTED  # mutating the copy...
    assert spec.provenance["position"] == PROVENANCE_HUMAN_SUPPLEMENTED  # ...never touches the spec


def test_no_geometry_object_carries_provenance():
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec())
    for obj in (result.validated_connection, result.connection_location, *result.attachments):
        assert not hasattr(obj, "provenance")


# --- 4. Malformed input fails cleanly (no ValueError / TypeError / AttributeError) ---
@pytest.mark.parametrize("field_name, bad_value", [
    ("plate", "end_plate"), ("plate", ["end_plate"]), ("plate", 5),
    ("holes", "bolts"), ("holes", ["x"]), ("holes", 5),
    ("location", "here"), ("location", [1, 2, 3]), ("location", 5),
    ("attachments", [None]), ("attachments", ["END"]), ("attachments", [["member_mark", "L2"]]),
    ("attachments", "END"), ("attachments", {"member_mark": "L2"}), ("attachments", None),
    ("connected_member_marks", "REAL-UB-CAD-001"), ("connected_member_marks", [None, "L2"]),
    ("connected_member_marks", [1, 2]), ("connected_member_marks", None),
    ("provenance", "human"), ("provenance", ["position"]), ("provenance", None),
    ("provenance", {1: PROVENANCE_HUMAN_REVIEWED}), ("provenance", {"plate": None}), ("provenance", {"plate": 5}),
])
def test_malformed_structures_return_incomplete_instead_of_raising(field_name, bad_value):
    spec = make_synthetic_test_spec(**{field_name: bad_value})
    result = check_reviewed_connection_specification_completeness(spec)  # must not raise
    assert result.is_complete is False
    assert result.error
    assert result.validated_connection is None and result.connection_location is None
    assert result.attachments is None


def test_genuine_programming_errors_are_not_hidden():
    """No broad except: passing something that is not a specification at all still raises."""
    with pytest.raises(AttributeError):
        check_reviewed_connection_specification_completeness(None)


# --- 5. Non-finite / non-numeric engineering geometry ---
_BAD_NUMBERS = [float("nan"), float("inf"), float("-inf"), True, False, "22", None, 0, -5.0]


@pytest.mark.parametrize("bad_value", _BAD_NUMBERS)
@pytest.mark.parametrize("field_name", ["width_mm", "depth_mm", "thickness_mm"])
def test_non_finite_or_invalid_plate_dimensions_are_rejected(field_name, bad_value):
    plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    plate[field_name] = bad_value
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(plate=plate))
    assert result.is_complete is False
    assert field_name in result.error


@pytest.mark.parametrize("bad_value", _BAD_NUMBERS)
@pytest.mark.parametrize("field_name", ["diameter_mm", "vertical_spacing_mm", "horizontal_spacing_mm"])
def test_non_finite_or_invalid_hole_dimensions_are_rejected(field_name, bad_value):
    holes = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}
    holes[field_name] = bad_value
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(holes=holes))
    assert result.is_complete is False
    assert field_name in result.error


@pytest.mark.parametrize("bad_quantity", [float("nan"), float("inf"), float("-inf"), True, 4.5, 0, -4, "4", None])
def test_non_finite_or_non_integral_hole_quantity_is_rejected_without_crashing(bad_quantity):
    holes = {"quantity": bad_quantity, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0,
             "horizontal_spacing_mm": 90.0}
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(holes=holes))
    assert result.is_complete is False
    assert "quantity" in result.error


def test_valid_integral_quantities_and_finite_numbers_are_still_accepted():
    for quantity in (4, 4.0):
        holes = {"quantity": quantity, "diameter_mm": 22, "vertical_spacing_mm": 140, "horizontal_spacing_mm": 90}
        result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(holes=holes))
        assert result.is_complete is True, result.error
        assert result.validated_connection.bolts[0]["quantity"] == 4
    two_holes = {"quantity": 2, "diameter_mm": 20.0, "vertical_spacing_mm": 100.0}
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(holes=two_holes))
    assert result.is_complete is True


def test_non_finite_values_are_never_normalised_into_defaults():
    plate = {"type": "end_plate", "thickness_mm": float("nan"), "width_mm": 180, "depth_mm": 250}
    spec = make_synthetic_test_spec(plate=plate)
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is False
    assert result.validated_connection is None  # no ValidatedConnection with thickness 0 (or anything) exists
    assert spec.plate["thickness_mm"] != spec.plate["thickness_mm"]  # still NaN on the spec: not rewritten


# --- 9. No mutation (success and every kind of failure) + deterministic repeatability ---
@pytest.mark.parametrize("make_spec", [
    lambda: make_synthetic_test_spec(),
    lambda: make_synthetic_test_spec(review_status="pending_review"),
    lambda: make_ai_only_spec(),
    lambda: make_synthetic_test_spec(plate="end_plate"),
    lambda: make_synthetic_test_spec(location={"x": float("nan"), "y": 0.0, "z": 0.0, "rotation_x": 0.0,
                                               "rotation_y": 0.0, "rotation_z": 0.0}),
])
def test_specification_is_never_mutated_by_the_completeness_check(make_spec):
    spec = make_spec()
    before = copy.deepcopy(spec)
    check_reviewed_connection_specification_completeness(spec)
    assert spec.plate == before.plate or (spec.plate != spec.plate and before.plate != before.plate)
    assert spec.holes == before.holes
    assert spec.attachments == before.attachments
    assert spec.connected_member_marks == before.connected_member_marks
    assert spec.provenance == before.provenance
    assert (spec.review_status, spec.position, spec.connection_id) == \
        (before.review_status, before.position, before.connection_id)


def test_completeness_results_are_deterministic_across_repeated_calls():
    for make_spec in (make_synthetic_test_spec, make_ai_only_spec,
                      lambda: make_synthetic_test_spec(review_status="pending_review")):
        spec = make_spec()
        assert check_reviewed_connection_specification_completeness(spec) == \
            check_reviewed_connection_specification_completeness(spec)


# --- Critical distinction: unreviewed / AI-only / pending specifications cannot reach the pipeline ---
@pytest.mark.parametrize("make_spec", [
    lambda: make_ai_only_spec(),
    lambda: make_synthetic_test_spec(review_status="pending_review"),
    lambda: make_synthetic_test_spec(provenance={name: PROVENANCE_AI_EXTRACTED
                                                 for name in REQUIRED_PROVENANCE_FIELDS}),
])
def test_unreviewed_specifications_yield_nothing_a_pipeline_could_consume(make_spec, tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    drawing_calls = []
    monkeypatch.setattr(gate_module, "generate_connection_fabrication_drawing_pdf",
                        lambda *a, **k: drawing_calls.append(True) or None)

    result = check_reviewed_connection_specification_completeness(make_spec())

    assert result.is_complete is False
    # build_reviewed_two_member_connection_assembly() needs all three of these:
    assert result.validated_connection is None
    assert result.connection_location is None
    assert result.attachments is None
    assert drawing_calls == []
    assert list(tmp_path.iterdir()) == []


def test_reviewed_synthetic_fixture_still_reaches_the_existing_7u_pipeline(tmp_path):
    """Regression: the correction pass did not close the door on genuinely reviewed input."""
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec())
    assert result.is_complete is True

    placed_a, placed_b = _build_placed_members()
    attachment_a = next(a for a in result.attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in result.attachments if a.member_mark == "L2")
    assembly = build_reviewed_two_member_connection_assembly(
        placed_a, placed_b, result.validated_connection, result.connection_location, attachment_a, attachment_b,
    )
    validate_reviewed_connection_assembly(assembly)
    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    assert out_path.exists()
    curve_ops, _ = _pdf_circles_and_paths(out_path)
    assert curve_ops == 16


# =============================================================================
# Oversized Python integers (final 7V correction): a value with no finite float
# representation must be REJECTED deterministically — never an OverflowError,
# never clamped/truncated, and never an invented CAD maximum.
# =============================================================================
_OVERSIZED_INTS = [pytest.param(10**400, id="pos_1e400"), pytest.param(-(10**400), id="neg_1e400"), pytest.param(10**309, id="pos_1e309"), pytest.param(10**5000, id="pos_1e5000")]  # 10**309 is just beyond float range


@pytest.mark.parametrize("huge", _OVERSIZED_INTS)
@pytest.mark.parametrize("field_name", ["width_mm", "depth_mm", "thickness_mm"])
def test_oversized_integer_plate_dimension_is_rejected_cleanly(field_name, huge):
    plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    plate[field_name] = huge
    spec = make_synthetic_test_spec(plate=plate)

    result = check_reviewed_connection_specification_completeness(spec)  # must not raise OverflowError

    assert result.is_complete is False
    assert field_name in result.error
    assert result.validated_connection is None
    assert check_reviewed_connection_specification_completeness(spec) == result  # deterministic
    assert spec.plate[field_name] == huge  # never clamped, truncated or replaced


@pytest.mark.parametrize("huge", _OVERSIZED_INTS)
@pytest.mark.parametrize("field_name", ["diameter_mm", "vertical_spacing_mm", "horizontal_spacing_mm"])
def test_oversized_integer_hole_dimension_is_rejected_cleanly(field_name, huge):
    holes = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}
    holes[field_name] = huge
    spec = make_synthetic_test_spec(holes=holes)

    result = check_reviewed_connection_specification_completeness(spec)

    assert result.is_complete is False
    assert field_name in result.error
    assert check_reviewed_connection_specification_completeness(spec) == result
    assert spec.holes[field_name] == huge


@pytest.mark.parametrize("huge", _OVERSIZED_INTS)
def test_oversized_integer_hole_quantity_is_rejected_cleanly(huge):
    holes = {"quantity": huge, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}
    spec = make_synthetic_test_spec(holes=holes)

    result = check_reviewed_connection_specification_completeness(spec)

    assert result.is_complete is False
    assert "quantity" in result.error
    assert check_reviewed_connection_specification_completeness(spec) == result
    assert spec.holes["quantity"] == huge


@pytest.mark.parametrize("huge", _OVERSIZED_INTS)
@pytest.mark.parametrize("field_name", ["x", "y", "z", "rotation_x", "rotation_y", "rotation_z"])
def test_oversized_integer_location_coordinate_is_rejected_cleanly(field_name, huge):
    location = {"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    location[field_name] = huge
    spec = make_synthetic_test_spec(location=location)

    result = check_reviewed_connection_specification_completeness(spec)

    assert result.is_complete is False
    assert field_name in result.error
    assert result.connection_location is None
    assert check_reviewed_connection_specification_completeness(spec) == result
    assert spec.location[field_name] == huge


def test_ordinary_integer_and_float_values_still_pass_after_the_oversized_int_fix():
    spec = make_synthetic_test_spec(
        plate={"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250},              # ints
        holes={"quantity": 4, "diameter_mm": 22, "vertical_spacing_mm": 140, "horizontal_spacing_mm": 90},  # ints
        location={"x": 0, "y": 0, "z": 3994, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0},         # ints
    )
    result = check_reviewed_connection_specification_completeness(spec)
    assert result.is_complete is True, result.error
    assert result.connection_location.z == 3994.0

    floats = make_synthetic_test_spec(
        plate={"type": "end_plate", "thickness_mm": 12.5, "width_mm": 180.25, "depth_mm": 250.75},
        holes={"quantity": 4.0, "diameter_mm": 22.5, "vertical_spacing_mm": 140.5, "horizontal_spacing_mm": 90.5},
        location={"x": 1.5, "y": -2.5, "z": 3994.25, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
    )
    assert check_reviewed_connection_specification_completeness(floats).is_complete is True


def test_no_invented_maximum_a_large_but_finite_coordinate_is_not_capped():
    """10**308 is representable as a finite float: numerically valid, kept exactly, not clamped."""
    location = {"x": 10**308, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    result = check_reviewed_connection_specification_completeness(make_synthetic_test_spec(location=location))
    assert result.is_complete is True
    assert result.connection_location.x == float(10**308)
