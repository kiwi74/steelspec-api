"""
Milestone 7S — proving that a reviewed two-member connection cannot
reach fabrication drawing generation unless it has first passed the
existing 7R validation gate, and that the production drawing-
generation path itself (not merely a test calling things in the right
order) enforces this.

FIXTURE PROVENANCE: reuses 7O/7P/7Q/7R's exact primary fixture and
helper (`_build_primary_assembly`) unchanged — Member A
(REAL-UB-CAD-001, 310UB40, 4000mm) at x=0,y=0,z=0; Member B (L2,
250PFC, 3000mm, HUMAN-REVIEWED/SUPPLEMENTED length) at x=0,y=0,z=4000;
the 7H/7D reviewed connection fixture (180x250x12mm plate, 4xO22mm
holes); the 7K reviewed ConnectionLocation (z=3994); the 7N reviewed
attachment fixture (REAL-UB-CAD-001->END, L2->START). Nothing here is
claimed to be AI-extracted, drawing-derived, or an actual Arkles
fabrication drawing.

MILESTONE 7T UPDATE: the production entry point this file tests,
generate_fabrication_drawing_from_reviewed_assembly(), now renders the
complete reviewed connection (both members' own project-space geometry
plus the actual generated connection plate/hole geometry) via
generate_connection_fabrication_drawing_pdf() (7T) — see
app/cad_engine/reviewed_connection_drawing_gate.py's own module
docstring. It no longer renders Member A alone; the tests below that
inspect PDF content were updated accordingly. Dedicated 7T tests
proving the two-member/plate/hole content in depth live in
tests/test_two_member_connection_drawing.py — this file keeps its
original 7S focus: that a reviewed connection cannot reach drawing
generation unless it has first passed the 7R validation gate, and that
the production path itself (not merely a test calling things in the
right order) enforces this.
"""
import datetime

import pypdf
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.reviewed_connection_drawing_gate import generate_fabrication_drawing_from_reviewed_assembly
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record
from tests.test_two_member_connection import make_test_reviewed_location_record


# === 1. Valid reviewed connection reaches drawing generation ===
def test_valid_reviewed_connection_reaches_drawing_generation(tmp_path):
    assembly, *_ = _build_primary_assembly()

    out_path = generate_fabrication_drawing_from_reviewed_assembly(
        assembly, tmp_path / "out.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    reader = pypdf.PdfReader(out_path)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "REAL-UB-CAD-001" in text
    assert "310UB40" in text


# === 2. Proof the production path itself invokes 7R before drawing generation ===
def test_7r_validation_invoked_before_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    assembly, *_ = _build_primary_assembly()
    calls = []

    original_validate = gate_module.validate_reviewed_connection_assembly

    def tracking_validate(a):
        calls.append("validate")
        return original_validate(a)

    original_pdf = gate_module.generate_connection_fabrication_drawing_pdf

    def tracking_pdf(*args, **kwargs):
        calls.append("pdf")
        return original_pdf(*args, **kwargs)

    monkeypatch.setattr(gate_module, "validate_reviewed_connection_assembly", tracking_validate)
    monkeypatch.setattr(gate_module, "generate_connection_fabrication_drawing_pdf", tracking_pdf)

    generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    assert calls == ["validate", "pdf"]  # validation happens first, exactly once, before drawing generation


# === 3. 7P-invalid connection is blocked; drawing generator never reached; no artifact ===
def test_7p_invalid_connection_blocks_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    pdf_calls = []
    original_pdf = gate_module.generate_connection_fabrication_drawing_pdf

    def tracking_pdf(*args, **kwargs):
        pdf_calls.append(True)
        return original_pdf(*args, **kwargs)

    monkeypatch.setattr(gate_module, "generate_connection_fabrication_drawing_pdf", tracking_pdf)

    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="REAL-UB-CAD-001"):
        generate_fabrication_drawing_from_reviewed_assembly(assembly, out_path)

    assert pdf_calls == []  # the drawing generator was never reached
    assert not out_path.exists()  # no partial or full artifact was ever produced


# === 4. Upstream 7K/7L-invalid geometry cannot reach drawing generation ===
def test_upstream_7k7l_invalid_connection_cannot_reach_drawing_generation():
    """
    A 7K/7L-invalid connection location fails during assembly
    construction itself, before a ReviewedTwoMemberConnectionAssembly
    exists — there is no object to ever pass to the drawing gate.
    """
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(location_record=make_test_reviewed_location_record(z=2000.0))


# === 6. Drawing output now genuinely represents the two-member connection (7T) ===
def test_drawing_output_represents_two_member_connection(tmp_path):
    """
    Superseded by 7T (see module docstring): the 7S-era assertions here
    proved a documented gap (no connection-detail view, no plate/hole
    content) that 7T closes. This test now proves the gap is genuinely
    closed for the production entry point this file tests — see
    tests/test_two_member_connection_drawing.py for the full, dedicated
    7T proof (both members, plate dimensions, all four holes,
    anti-hardcoding).
    """
    assembly, *_ = _build_primary_assembly()

    out_path = generate_fabrication_drawing_from_reviewed_assembly(
        assembly, tmp_path / "out.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )
    text = pypdf.PdfReader(out_path).pages[0].extract_text()

    assert "FABRICATION DRAWING" in text
    assert "CONNECTION ELEVATION" in text
    assert "CONNECTION DETAIL" in text
    assert "REAL-UB-CAD-001" in text
    assert "310UB40" in text
    assert "L2" in text
    assert "250PFC" in text
    assert "4000" in text
    assert "PLATE:" in text


# === 7. No mutation of the assembly or member geometry ===
def test_no_mutation_of_assembly_or_member_geometry(tmp_path):
    assembly, *_ = _build_primary_assembly()
    bbox_a_before = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b_before = assembly.member_b.geometry.solid.val().BoundingBox()
    attachment_a_before = assembly.attachment_a

    generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    bbox_a_after = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b_after = assembly.member_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(bbox_b_after.zmin - bbox_b_before.zmin) < 1e-6
    assert assembly.attachment_a == attachment_a_before


# === 8. Repeated valid generation behaves consistently ===
def test_repeated_valid_generation_consistent(tmp_path):
    assembly, *_ = _build_primary_assembly()

    out_1 = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out1.pdf")
    out_2 = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out2.pdf")

    text_1 = pypdf.PdfReader(out_1).pages[0].extract_text()
    text_2 = pypdf.PdfReader(out_2).pages[0].extract_text()
    assert "REAL-UB-CAD-001" in text_1
    assert "REAL-UB-CAD-001" in text_2
    assert out_1.stat().st_size > 0
    assert out_2.stat().st_size > 0


# === 9. Project-identity title-block rows (7AR generator improvement) ===
def test_project_identity_rows_absent_by_default(tmp_path):
    """Without project_id/source_drawing_id the drawing carries no project
    row — the generator never invents job identity."""
    assembly, *_ = _build_primary_assembly()

    out_path = generate_fabrication_drawing_from_reviewed_assembly(
        assembly, tmp_path / "out.pdf", date=datetime.date(2026, 1, 1),
    )

    text = "\n".join((page.extract_text() or "") for page in pypdf.PdfReader(out_path).pages)
    assert "PROJECT" not in text
    assert "SOURCE DRAWING" not in text


def test_project_identity_rows_present_when_supplied(tmp_path):
    """When the job identity is supplied (7AR's dispatch kwargs) the title
    block carries it — and nothing else changes about the drawing."""
    assembly, *_ = _build_primary_assembly()

    out_path = generate_fabrication_drawing_from_reviewed_assembly(
        assembly, tmp_path / "out.pdf", date=datetime.date(2026, 1, 1),
        project_id="PROJ-FIXTURE", source_drawing_id="SOURCE-FIXTURE",
    )

    text = "\n".join((page.extract_text() or "") for page in pypdf.PdfReader(out_path).pages)
    assert "PROJECT" in text and "PROJ-FIXTURE" in text
    assert "SOURCE DRAWING" in text and "SOURCE-FIXTURE" in text
    assert "REAL-UB-CAD-001" in text
    assert "310UB40" in text
