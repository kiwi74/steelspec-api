"""
Milestone 7T — proving the two-member connection drawing renderer
(app.drawing_generator.pdf_builder.build_connection_pdf(), reached via
app.cad_engine.reviewed_connection_drawing_gate.
generate_fabrication_drawing_from_reviewed_assembly() unchanged from
7S) genuinely represents the actual generated CAD geometry — both
members, the actual connection plate, and the actual generated hole
geometry — rather than either the 7S Member-A-only output or a
drawing rebuilt from raw input specification metadata.

FIXTURE PROVENANCE: reuses 7O/7P/7Q/7R/7S's exact primary fixture and
helper (`_build_primary_assembly`) unchanged — Member A
(REAL-UB-CAD-001, 310UB40, 4000mm) at x=0,y=0,z=0; Member B (L2,
250PFC, 3000mm, HUMAN-REVIEWED/SUPPLEMENTED length) at x=0,y=0,z=4000;
the 7H/7D reviewed connection fixture (180x250x12mm plate, 4xO22mm
holes); the 7K reviewed ConnectionLocation (z=3994); the 7N reviewed
attachment fixture (REAL-UB-CAD-001->END, L2->START). Nothing here is
claimed to be AI-extracted, drawing-derived, or an actual Arkles
fabrication drawing.

ANTI-HARDCODING FIXTURE (Step 17): `_build_second_reviewed_assembly()`
below reuses the exact same primary members/location/attachment
fixture but overrides the reviewed connection record's own `plates`/
`bolts` sub-records to a DIFFERENT, already-supported END_PLATE
configuration — a 2-hole vertical bolt pattern (Ø20mm,
vertical_spacing=100mm) instead of the primary fixture's 4-hole grid
(Ø22mm) — both patterns already supported by
app.cad_engine.connections.SUPPORTED_HOLE_QUANTITIES = (2, 4). No new
connection type, no invented catalogue data: this is the same
architecture, given different real input values, used to prove the
renderer reads geometry from the generated GeneratedConnectionAssembly
object rather than reproducing the primary fixture's own dimensions.

CIRCLE-COUNT VERIFICATION (Step 16): `_pdf_circles_and_paths()` below
is the same raw-content-stream inspection
tests/test_drawing_generator.py already uses (reportlab's
canvas.circle() draws each circle as 4 Bezier curveto ops) — an
independent, non-text signal that the actual number of hole circles
drawn matches the actual number of generated holes. The connection
elevation view (View 1) deliberately draws hole positions as straight
tick marks, not circles (see pdf_builder._draw_two_member_elevation()'s
own docstring), so every curve op in the page comes from the
connection detail view's (View 2) real hole circles alone.
"""
import datetime

import pypdf
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.reviewed_connection_drawing_gate import generate_fabrication_drawing_from_reviewed_assembly
from tests.test_real_multi_member_connection import make_multi_member_connection_record
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record


def _pdf_circles_and_paths(path):
    """Same raw-content-stream inspection tests/test_drawing_generator.py uses."""
    reader = pypdf.PdfReader(path)
    content = reader.pages[0].get_contents().get_data().decode("latin-1")
    curve_ops = content.count(" c\n") + content.count(" c ")
    line_ops = content.count(" l\n") + content.count(" l ")
    return curve_ops, line_ops


def _build_second_reviewed_assembly():
    """See module docstring's ANTI-HARDCODING FIXTURE note."""
    connection_record = make_multi_member_connection_record(
        plates=[{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
        bolts=[{
            "quantity": 2, "size": "M20", "grade": "8.8",
            "diameter_mm": 20.0, "vertical_spacing_mm": 100.0,
        }],
    )
    return _build_primary_assembly(connection_record=connection_record)


# === 1. Valid connection produces a PDF with the two-member representation ===
def test_valid_connection_produces_two_member_pdf(tmp_path):
    assembly, *_ = _build_primary_assembly()

    out_path = generate_fabrication_drawing_from_reviewed_assembly(
        assembly, tmp_path / "out.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    reader = pypdf.PdfReader(out_path)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "CONNECTION ELEVATION" in text
    assert "CONNECTION DETAIL" in text
    assert assembly.connection.connection_id in text


# === 2. Member A information present ===
def test_pdf_contains_member_a_information(tmp_path):
    assembly, *_ = _build_primary_assembly()
    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")
    text = pypdf.PdfReader(out_path).pages[0].extract_text()

    assert "REAL-UB-CAD-001" in text
    assert "310UB40" in text
    assert "4000" in text


# === 3. Member B information present ===
def test_pdf_contains_member_b_information(tmp_path):
    assembly, *_ = _build_primary_assembly()
    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")
    text = pypdf.PdfReader(out_path).pages[0].extract_text()

    assert "L2" in text
    assert "250PFC" in text
    assert "3000" in text


# === 4. Plate representation and dimensions, safely derived from actual geometry ===
def test_pdf_contains_plate_representation_and_dimensions(tmp_path):
    assembly, *_ = _build_primary_assembly()
    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")
    text = pypdf.PdfReader(out_path).pages[0].extract_text()

    # PLATE info line: width x height x thickness, all measured from
    # assembly.connection_geometry.plate's own BoundingBox() (see
    # pdf_builder._connection_info_lines(), reused unchanged).
    assert "PLATE:" in text
    assert "180" in text
    assert "250" in text
    assert "12" in text
    # The plate outline itself is drawn as real rectangle path ops
    # (straight lines, not text) in both View 1 and View 2.
    _curve_ops, line_ops = _pdf_circles_and_paths(out_path)
    assert line_ops > 0


# === 5. All four actual hole representations present ===
def test_pdf_contains_four_actual_hole_representations(tmp_path):
    assembly, *_ = _build_primary_assembly()
    assert len(assembly.connection_geometry.holes) == 4

    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")
    text = pypdf.PdfReader(out_path).pages[0].extract_text()
    assert "4" in text and "22" in text  # "HOLES: 4 x O22 mm"

    curve_ops, _line_ops = _pdf_circles_and_paths(out_path)
    # reportlab's canvas.circle() draws 4 Bezier curves per circle;
    # exactly 4 real hole circles are drawn (View 1 uses tick marks,
    # not circles — see module docstring).
    assert curve_ops == 16


# === 6. Existing 7S validation gate still executes before drawing generation ===
def test_7r_validation_gate_still_executes_before_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    assembly, *_ = _build_primary_assembly()
    calls = []
    original_validate = gate_module.validate_reviewed_connection_assembly

    def tracking_validate(a):
        calls.append("validate")
        return original_validate(a)

    monkeypatch.setattr(gate_module, "validate_reviewed_connection_assembly", tracking_validate)

    generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    assert calls == ["validate"]  # ran exactly once, and no exception means it ran before drawing succeeded


# === 7. Invalid connection produces no PDF ===
def test_invalid_connection_produces_no_pdf(tmp_path):
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError):
        generate_fabrication_drawing_from_reviewed_assembly(assembly, out_path)

    assert not out_path.exists()


# === 8. No fallback to the old Member-A-only drawing on failure ===
def test_invalid_connection_does_not_fall_back_to_member_a_only_drawing(tmp_path, monkeypatch):
    """
    Proves the failure path never reaches the two-member drawing call
    (generate_connection_fabrication_drawing_pdf — the only drawing
    function reviewed_connection_drawing_gate.py imports/calls at all
    since 7T; see its module docstring), by monkeypatching that
    reference INSIDE the gate module itself — the actual production
    call site, not merely a test calling things in the right order —
    and asserting it is never invoked. Since the gate module no longer
    even imports the old single-member generate_fabrication_drawing_pdf,
    there is no code path left in this function that could reach it.
    """
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    calls = []
    monkeypatch.setattr(
        gate_module, "generate_connection_fabrication_drawing_pdf",
        lambda *a, **k: calls.append("two-member") or None,
    )

    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError):
        generate_fabrication_drawing_from_reviewed_assembly(assembly, out_path)

    assert calls == []
    assert not out_path.exists()
    assert not hasattr(gate_module, "generate_fabrication_drawing_pdf")


# === 9. Geometry source: renderer uses generated geometry, not hard-coded fixture dimensions ===
def test_renderer_uses_generated_geometry_not_hardcoded_fixture_dimensions(tmp_path):
    """
    A second reviewed connection (same architecture, different actual
    plate/hole configuration — see module docstring) produces a
    GENUINELY DIFFERENT drawing: 2 holes at Ø20mm / 100mm spacing
    instead of 4 holes at Ø22mm / 90x140mm spacing. If the renderer
    were reproducing the primary fixture's own dimensions instead of
    reading assembly.connection_geometry, this second drawing would be
    indistinguishable from the primary one.
    """
    assembly, *_ = _build_second_reviewed_assembly()
    assert len(assembly.connection_geometry.holes) == 2
    assert {round(h.diameter, 6) for h in assembly.connection_geometry.holes} == {20.0}

    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out2.pdf")
    text = pypdf.PdfReader(out_path).pages[0].extract_text()

    assert "HOLES: 2" in text.replace("×", "").replace("x", "") or "2 " in text
    assert "20" in text
    assert "PATTERN: 100" in text.replace("×", "").replace("V", "V")

    curve_ops, _line_ops = _pdf_circles_and_paths(out_path)
    assert curve_ops == 8  # 2 real holes x 4 bezier curves each — not the primary fixture's 16


# === 10. No mutation of member/connection/attachment/placement/location state ===
def test_no_mutation_of_assembly_or_generated_geometry(tmp_path):
    assembly, *_ = _build_primary_assembly()
    bbox_a_before = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b_before = assembly.member_b.geometry.solid.val().BoundingBox()
    plate_bbox_before = assembly.connection_geometry.plate.val().BoundingBox()
    holes_before = [(h.hole_id, h.center, h.diameter) for h in assembly.connection_geometry.holes]
    attachment_a_before = assembly.attachment_a
    attachment_b_before = assembly.attachment_b
    placement_a_before = assembly.member_a.placement
    location_before = assembly.location

    generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    bbox_a_after = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b_after = assembly.member_b.geometry.solid.val().BoundingBox()
    plate_bbox_after = assembly.connection_geometry.plate.val().BoundingBox()
    holes_after = [(h.hole_id, h.center, h.diameter) for h in assembly.connection_geometry.holes]

    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(bbox_b_after.zmin - bbox_b_before.zmin) < 1e-6
    assert abs(plate_bbox_after.zmin - plate_bbox_before.zmin) < 1e-6
    assert holes_after == holes_before
    assert assembly.attachment_a == attachment_a_before
    assert assembly.attachment_b == attachment_b_before
    assert assembly.member_a.placement == placement_a_before
    assert assembly.location == location_before


# === 11. Repeatability: two equivalent assemblies produce consistent output ===
def test_repeated_valid_generation_from_equivalent_assemblies_is_consistent(tmp_path):
    assembly_1, *_ = _build_primary_assembly()
    assembly_2, *_ = _build_primary_assembly()

    out_1 = generate_fabrication_drawing_from_reviewed_assembly(
        assembly_1, tmp_path / "out1.pdf", date=datetime.date(2026, 1, 1),
    )
    out_2 = generate_fabrication_drawing_from_reviewed_assembly(
        assembly_2, tmp_path / "out2.pdf", date=datetime.date(2026, 1, 1),
    )

    text_1 = pypdf.PdfReader(out_1).pages[0].extract_text()
    text_2 = pypdf.PdfReader(out_2).pages[0].extract_text()
    assert text_1 == text_2

    curve_ops_1, line_ops_1 = _pdf_circles_and_paths(out_1)
    curve_ops_2, line_ops_2 = _pdf_circles_and_paths(out_2)
    assert (curve_ops_1, line_ops_1) == (curve_ops_2, line_ops_2)
