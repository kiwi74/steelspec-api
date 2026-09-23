"""
Unit tests for the fabrication drawing generator: milestone 1 (one
DXF sheet — elevation + cross section + title block — for a single
straight, unconnected member), milestone 2 (an end-plate view with
real hole circles, for a member whose CAD geometry carries an
END_PLATE connection), and milestone 3 (a separate PDF output format,
for bare members, using a synthetic UB fixture). Builds real geometry
via the CAD engine first (app.cad_engine.interface.generate_geometry)
using small synthetic fixtures — the same 250PFC/3000mm fixture as
test_cad_engine.py for the DXF tests, and a synthetic 310UB40/4000mm
fixture for the new PDF tests (see test_cad_engine.py's own
SECTION_310UB40 for why these numbers are explicitly NOT verified
production dimensions) — then feeds that real GeneratedMemberGeometry
into the drawing generator. Not hand-built stand-ins. The Arkles
Strand dataset is not used here either, for the same reason as the
CAD engine tests: it has no reliable member lengths.

pypdf is used here purely to read PDF content back for verification —
it is a test-only tool, not a production dependency, and (like pytest
itself in this project) is intentionally not added to requirements.txt.
"""
import datetime
import math

import ezdxf
import pypdf
import pytest

from app.cad_engine.interface import (
    ValidatedConnection, ValidatedSteelMember, generate_geometry,
)
from app.drawing_generator.dxf_builder import _extract_bottom_profile
from app.drawing_generator.errors import DrawingValidationError
from app.drawing_generator.interface import (
    generate_fabrication_drawing_pdf, generate_fabrication_drawings,
)
from app.drawing_generator.pdf_builder import _extract_end_face_topology

SECTION_250PFC = {
    "name": "250PFC", "family": "PFC",
    "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 15.0, "web_thickness": 8.0,
    "weight_per_metre": 35.5,
}
PLATE_SPEC = {"width": 90.0, "height": 250.0, "thickness": 10.0}
HOLES_SPEC = {"diameter": 18.0, "quantity": 2, "vertical_spacing": 150.0}


def make_geometry(length_mm=3000.0, mark="P-TEST-1"):
    member = ValidatedSteelMember(
        mark=mark,
        section="250PFC",
        length_mm=length_mm,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_250PFC,
    )
    return generate_geometry(member)


def make_connected_geometry(length_mm=3000.0, mark="P1"):
    member = ValidatedSteelMember(
        mark=mark,
        section="250PFC",
        length_mm=length_mm,
        material="300PLUS",
        orientation=None,
        connection_refs=["conn-1"],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_250PFC,
    )
    connection = ValidatedConnection(
        connection_id="conn-1",
        connected_members=[mark],
        plates=[dict(PLATE_SPEC)],
        bolts=[dict(HOLES_SPEC)],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
    )
    return generate_geometry(member, connections=[connection])


@pytest.fixture
def geometry():
    return make_geometry()


@pytest.fixture
def connected_geometry():
    return make_connected_geometry()


def _read_texts(msp) -> list[str]:
    return [e.dxf.text for e in msp if e.dxftype() == "TEXT"]


def _dim_measurements(msp) -> list[float]:
    return [e.get_measurement() for e in msp if e.dxftype() == "DIMENSION"]


def _circles(msp):
    return [e for e in msp if e.dxftype() == "CIRCLE"]


# === 1 & 2. Valid geometry produces a real DXF file that ezdxf can open ===
def test_valid_geometry_produces_readable_dxf_file(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "P-TEST-1.dxf")

    assert out_path.exists()
    doc = ezdxf.readfile(out_path)  # raises if the file isn't a valid DXF
    assert doc is not None


# === 3. Member mark appears in the drawing ===
def test_member_mark_appears_in_drawing(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    texts = _read_texts(msp)
    assert any("P-TEST-1" in t for t in texts)


# === 4. Section name appears ===
def test_section_name_appears_in_drawing(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    texts = _read_texts(msp)
    assert any("250PFC" in t for t in texts)


# === 5. The exact supplied length appears ===
def test_exact_length_appears_in_drawing(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    texts = _read_texts(msp)
    assert any("3000" in t for t in texts)
    measurements = _dim_measurements(msp)
    assert any(abs(m - 3000.0) < 1e-6 for m in measurements)


# === 6. The main elevation geometry exists ===
def test_elevation_view_geometry_exists(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    polylines = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
    # The elevation view is a 4-point rectangle: length x depth.
    rectangles = [p for p in polylines if len(p) == 4]
    assert rectangles, "expected a 4-vertex rectangle for the elevation view"

    xs = [pt[0] for pt in rectangles[0].get_points()]
    ys = [pt[1] for pt in rectangles[0].get_points()]
    assert abs((max(xs) - min(xs)) - 3000.0) < 1e-6  # length
    assert abs((max(ys) - min(ys)) - 250.0) < 1e-6   # depth


# === 7. The cross-section geometry exists ===
def test_cross_section_view_geometry_exists(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    polylines = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
    # The PFC profile is an 8-vertex C-shape — distinct from the 4-vertex
    # elevation rectangle and the title block's rectangle.
    channel_shapes = [p for p in polylines if len(p) == 8]
    assert len(channel_shapes) == 1

    xs = [pt[0] for pt in channel_shapes[0].get_points()]
    ys = [pt[1] for pt in channel_shapes[0].get_points()]
    assert abs((max(ys) - min(ys)) - 250.0) < 1e-6         # overall depth
    assert abs((max(xs) - min(xs)) - 90.0) < 1e-6           # overall flange width


# === 8. Dimension values correspond to the supplied member/section dimensions ===
def test_dimension_values_match_supplied_geometry(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    measurements = sorted(_dim_measurements(msp))
    # Expect: overall length (3000), overall depth (250), overall flange width (90).
    assert any(abs(m - 3000.0) < 1e-6 for m in measurements)
    assert any(abs(m - 250.0) < 1e-6 for m in measurements)
    assert any(abs(m - 90.0) < 1e-6 for m in measurements)


# === 9. No connection geometry is generated ===
def test_no_connection_geometry_generated(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    entity_types = {e.dxftype() for e in msp}
    # No bolt holes (circles), no separate plate/weld entities of any kind —
    # only what this milestone actually draws.
    assert "CIRCLE" not in entity_types
    assert entity_types.issubset({"LWPOLYLINE", "TEXT", "DIMENSION", "LINE", "POINT"})


# === 10. Invalid/missing geometry fails clearly ===
def test_none_geometry_raises(tmp_path):
    with pytest.raises(DrawingValidationError, match="No geometry"):
        generate_fabrication_drawings(None, tmp_path / "out.dxf")


def test_geometry_without_solid_raises(tmp_path):
    class FakeGeometry:
        mark = "X1"
        section_name = "250PFC"
        section_family = "PFC"
        length_mm = 3000.0
        solid = None

    with pytest.raises(DrawingValidationError, match="no CAD solid"):
        generate_fabrication_drawings(FakeGeometry(), tmp_path / "out.dxf")


def test_geometry_with_invalid_length_raises(tmp_path):
    class FakeGeometry:
        mark = "X1"
        section_name = "250PFC"
        section_family = "PFC"
        length_mm = None
        solid = object()

    with pytest.raises(DrawingValidationError, match="invalid length_mm"):
        generate_fabrication_drawings(FakeGeometry(), tmp_path / "out.dxf")


# ===================================================================
# Milestone 2: end-plate view (real plate outline + real hole circles)
# ===================================================================

# === Connection Test 1 & 2: connected geometry produces a readable DXF ===
def test_connected_geometry_produces_readable_dxf_file(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "P1.dxf")

    assert out_path.exists()
    doc = ezdxf.readfile(out_path)  # raises if the file isn't a valid DXF
    assert doc is not None


# === Connection Test 3: existing member information remains ===
def test_member_information_remains_in_connected_drawing(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    texts = _read_texts(ezdxf.readfile(out_path).modelspace())

    assert any("P1" in t for t in texts)
    assert any("250PFC" in t for t in texts)
    assert any("3000" in t for t in texts)


# === Connection Test 4: end plate exists in the drawing ===
def test_end_plate_view_geometry_exists(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    polylines = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
    # Elevation rectangle (4), plate sliver in elevation (4), PFC cross
    # section (8), end-plate outline (4), title block (4) — the end-plate
    # outline is a second 4-vertex rectangle whose size (90x250) matches
    # the plate, not the elevation/title block rectangles' positions.
    four_vertex = [p for p in polylines if len(p) == 4]
    assert len(four_vertex) >= 3  # elevation + plate sliver + end-plate outline (+ title block)

    def size(poly):
        xs = [pt[0] for pt in poly.get_points()]
        ys = [pt[1] for pt in poly.get_points()]
        return (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))

    sizes = [size(p) for p in four_vertex]
    assert (90.0, 250.0) in sizes


# === Connection Test 5: plate dimensions (90, 250) appear as DIMENSION entities ===
def test_plate_dimensions_appear(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    measurements = _dim_measurements(ezdxf.readfile(out_path).modelspace())

    assert any(abs(m - 90.0) < 1e-3 for m in measurements)
    assert any(abs(m - 250.0) < 1e-3 for m in measurements)


# === Connection Test 6: exactly two hole circles represent the holes ===
def test_exactly_two_hole_circles(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    circles = _circles(ezdxf.readfile(out_path).modelspace())

    assert len(circles) == 2


# === Connection Test 7: hole diameter is correct (Ø18mm) ===
def test_hole_circle_diameter_is_correct(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    circles = _circles(ezdxf.readfile(out_path).modelspace())

    for c in circles:
        assert abs(c.dxf.radius * 2 - 18.0) < 1e-3


# === Connection Test 8: hole centre spacing is correct (150mm) ===
def test_hole_circle_spacing_is_correct(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    circles = _circles(ezdxf.readfile(out_path).modelspace())

    centers = sorted((c.dxf.center.x, c.dxf.center.y) for c in circles)
    (x1, y1), (x2, y2) = centers
    assert abs(x1 - x2) < 1e-6  # same horizontal centreline
    assert abs((y2 - y1) - 150.0) < 1e-3


# === Connection Test 9: overall member length is 3000, not 3010 ===
def test_overall_member_length_is_not_combined_with_plate(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    measurements = _dim_measurements(ezdxf.readfile(out_path).modelspace())

    assert any(abs(m - 3000.0) < 1e-3 for m in measurements)
    assert not any(abs(m - 3010.0) < 1e-3 for m in measurements)


# === Connection Test 10: connection label appears ===
def test_connection_label_appears(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    texts = _read_texts(ezdxf.readfile(out_path).modelspace())

    assert any("END PLATE" in t for t in texts)


# === Connection Test 11: no physical bolts — only the two real hole circles ===
def test_no_physical_bolt_geometry(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    # No 3D solid/mesh entities, no extra circles beyond the two holes.
    entity_types = {e.dxftype() for e in msp}
    assert entity_types.issubset({"LWPOLYLINE", "TEXT", "DIMENSION", "CIRCLE", "LINE", "POINT"})
    assert len(_circles(msp)) == 2


# === Connection Test 12: bare-member regression — no connection geometry appears ===
def test_bare_member_drawing_has_no_connection_geometry(geometry, tmp_path):
    out_path = generate_fabrication_drawings(geometry, tmp_path / "out.dxf")
    msp = ezdxf.readfile(out_path).modelspace()

    texts = _read_texts(msp)
    assert not _circles(msp)
    assert not any("END PLATE" in t or "CONNECTION" in t for t in texts)
    # Same style as milestone 1: exactly the original entity types, no CIRCLE.
    entity_types = {e.dxftype() for e in msp}
    assert "CIRCLE" not in entity_types
    assert entity_types.issubset({"LWPOLYLINE", "TEXT", "DIMENSION", "LINE", "POINT"})


# === Extra: hole circle radius/spacing sanity-checked independently via math ===
def test_hole_circle_geometry_matches_expected_circumference(connected_geometry, tmp_path):
    out_path = generate_fabrication_drawings(connected_geometry, tmp_path / "out.dxf")
    circles = _circles(ezdxf.readfile(out_path).modelspace())

    expected_circumference = math.pi * 18.0
    for c in circles:
        assert abs(2 * math.pi * c.dxf.radius - expected_circumference) < 1e-3


# ===================================================================
# Milestone 3: PDF fabrication drawing (separate output format, bare
# member only). SECTION_310UB40 mirrors test_cad_engine.py's own
# fixture of the same name — deliberately round, explicitly NOT
# verified production dimensions (no live Supabase access in this
# session — see that file's comment for the full explanation).
# ===================================================================

SECTION_310UB40 = {
    "name": "310UB40", "family": "UB",
    "depth": 310.0, "flange_width": 165.0,
    "flange_thickness": 12.0, "web_thickness": 8.0,
    "weight_per_metre": 40.4,
}


def make_ub_geometry(length_mm=4000.0, mark="UB-TEST-1"):
    member = ValidatedSteelMember(
        mark=mark,
        section="310UB40",
        length_mm=length_mm,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_310UB40,
    )
    return generate_geometry(member)


@pytest.fixture
def ub_geometry():
    return make_ub_geometry()


def _pdf_text(path) -> str:
    reader = pypdf.PdfReader(path)
    return "\n".join(page.extract_text() for page in reader.pages)


# === PDF Test 1 & 2: UB geometry can be passed to the PDF generator; PDF is created ===
def test_ub_geometry_produces_a_pdf_file(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "UB-TEST-1.pdf")

    assert out_path.exists()
    assert out_path.suffix == ".pdf"


# === PDF Test 3: PDF is readable/openable ===
def test_pdf_is_readable(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "out.pdf")

    reader = pypdf.PdfReader(out_path)  # raises if the file isn't a valid PDF
    assert len(reader.pages) == 1


# === PDF Test 4: PDF is not empty ===
def test_pdf_is_not_empty(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "out.pdf")

    assert out_path.stat().st_size > 1000  # a genuinely empty/blank PDF is a few hundred bytes
    text = _pdf_text(out_path)
    assert text.strip() != ""


# === PDF Test 5: required member/section text is present ===
def test_pdf_contains_member_information(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "out.pdf", quantity=1, status="TEST")
    text = _pdf_text(out_path)

    assert "UB-TEST-1" in text
    assert "310UB40" in text
    # Title block draws label/value as separate lines (grid layout) —
    # check both are present rather than a combined "LABEL: value" string.
    assert "LENGTH" in text and "4000" in text
    assert "QTY" in text and "1" in text


# === PDF Test 6: required dimensional values are present ===
def test_pdf_contains_dimensional_values(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "4000" in text
    assert "310" in text
    assert "165" in text


# === PDF Test 7: the cross section is genuinely non-rectangular, verified from the
# actual geometry passed into the PDF builder (not just PDF text) ===
def test_pdf_cross_section_geometry_is_non_rectangular():
    geometry = make_ub_geometry()

    # Independent check on the exact geometry object the PDF builder
    # will consume — proves the fixture (and therefore the drawing)
    # is a real I-beam, not a 4-point rectangle standing in for one.
    points = _extract_bottom_profile(geometry.solid)
    assert len(points) == 12

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width_mm = max(xs) - min(xs)
    depth_mm = max(ys) - min(ys)
    assert abs(width_mm - 165.0) < 1e-6
    assert abs(depth_mm - 310.0) < 1e-6
    assert abs(geometry.length_mm - 4000.0) < 1e-6


# === PDF Test 8: no connection information is present (bare member) ===
def test_pdf_has_no_connection_information(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "CONNECTION" not in text.upper()
    assert "END PLATE" not in text.upper()


# === Invalid/missing geometry fails clearly (mirrors the DXF path's own tests) ===
def test_pdf_none_geometry_raises(tmp_path):
    with pytest.raises(DrawingValidationError, match="No geometry"):
        generate_fabrication_drawing_pdf(None, tmp_path / "out.pdf")


def test_pdf_geometry_without_solid_raises(tmp_path):
    class FakeGeometry:
        mark = "X1"
        section_name = "310UB40"
        section_family = "UB"
        length_mm = 4000.0
        solid = None

    with pytest.raises(DrawingValidationError, match="no CAD solid"):
        generate_fabrication_drawing_pdf(FakeGeometry(), tmp_path / "out.pdf")


# ===================================================================
# Milestone 3.1: PFC + UB PDF regression. Proves the PDF generator's
# cross-section handling is genuinely family-agnostic (reads whatever
# profile app.cad_engine.sections built, via the same
# _extract_bottom_profile() used for DXF) rather than containing any
# `if family == "PFC" / "UB"` branch. In particular: the flange/web
# thickness labels must NOT appear for PFC, since PFC's single-sided
# channel has no symmetric flange/web waist to derive them from — see
# pdf_builder.py's _draw_cross_section() for the topological (not
# family-name) check that enforces this.
# ===================================================================

@pytest.fixture
def pfc_geometry():
    return make_geometry(mark="P1")  # reuses SECTION_250PFC / 3000mm from milestone 1's DXF fixtures


# === PFC PDF Test 1 & 2: PFC geometry generates a readable PDF ===
def test_pfc_geometry_produces_a_readable_pdf(pfc_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(pfc_geometry, tmp_path / "P1_250PFC_3000.pdf")

    assert out_path.exists()
    reader = pypdf.PdfReader(out_path)  # raises if the file isn't a valid PDF
    assert len(reader.pages) == 1


# === PFC PDF Test 3 & 4: correct mark/section/length/qty/status, and 3000mm present ===
def test_pfc_pdf_contains_member_information(pfc_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(pfc_geometry, tmp_path / "out.pdf", quantity=1, status="TEST")
    text = _pdf_text(out_path)

    assert "P1" in text
    assert "250PFC" in text
    assert "LENGTH" in text and "3000" in text
    assert "QTY" in text and "1" in text
    assert "STATUS" in text and "TEST" in text


# === PFC PDF Test 5: 90mm and 250mm (flange width / depth) both present ===
def test_pfc_pdf_contains_cross_section_dimensions(pfc_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(pfc_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "90" in text
    assert "250" in text


# === PFC PDF Test 6: cross-section is genuinely PFC-shaped, not rectangular —
# verified independently from the actual geometry passed into the builder ===
def test_pfc_pdf_cross_section_geometry_is_not_rectangular():
    geometry = make_geometry(mark="P1")

    points = _extract_bottom_profile(geometry.solid)
    assert len(points) == 8  # PFC's C-shape; a rectangle would be 4

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    assert abs((max(xs) - min(xs)) - 90.0) < 1e-6   # flange width
    assert abs((max(ys) - min(ys)) - 250.0) < 1e-6  # depth
    assert abs(geometry.length_mm - 3000.0) < 1e-6


# === PFC PDF Test 7 & 8: UB-specific FLANGE THK / WEB THK labels must NOT appear ===
def test_pfc_pdf_does_not_contain_ub_flange_thickness_label(pfc_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(pfc_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path).upper()

    assert "FLANGE THK" not in text
    assert "FLANGE THK: 12 MM" not in text  # the literal UB value — doubly confirms it's absent


def test_pfc_pdf_does_not_contain_ub_web_thickness_label(pfc_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(pfc_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path).upper()

    assert "WEB THK" not in text
    assert "WEB THK: 8 MM" not in text


# === UB PDF Test 9: UB regression — behaviour from milestone 1 remains intact ===
def test_ub_pdf_regression_still_correct(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(
        ub_geometry, tmp_path / "UB-TEST-1_310UB40_4000.pdf", quantity=1, status="TEST"
    )
    text = _pdf_text(out_path)

    assert "UB-TEST-1" in text
    assert "310UB40" in text
    assert "LENGTH" in text and "4000" in text
    assert "165" in text
    assert "310" in text
    assert "FLANGE THK: 12 mm" in text
    assert "WEB THK: 8 mm" in text

    points = _extract_bottom_profile(ub_geometry.solid)
    assert len(points) == 12  # genuine I/H shape
    assert abs(ub_geometry.length_mm - 4000.0) < 1e-6


# ===================================================================
# Milestone: PDF Connections 1 — UB + END_PLATE + 2 holes.
# Reuses the existing SECTION_310UB40, PLATE_SPEC, HOLES_SPEC fixtures
# and the existing CAD-engine connection machinery
# (build_end_plate_with_holes / attach_end_plate via generate_geometry)
# unchanged. vertical_spacing is a parameter, not hardcoded, so the
# "strong test requirement" below can prove the drawing isn't hardcoded.
# ===================================================================

def make_ub_connected_geometry(length_mm=4000.0, mark="UB-TEST-1", vertical_spacing=150.0):
    member = ValidatedSteelMember(
        mark=mark,
        section="310UB40",
        length_mm=length_mm,
        material="300PLUS",
        orientation=None,
        connection_refs=["conn-1"],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_310UB40,
    )
    connection = ValidatedConnection(
        connection_id="conn-1",
        connected_members=[mark],
        plates=[dict(PLATE_SPEC)],
        bolts=[{**HOLES_SPEC, "vertical_spacing": vertical_spacing}],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
    )
    return generate_geometry(member, connections=[connection])


@pytest.fixture
def ub_connected_geometry():
    return make_ub_connected_geometry()


def _pdf_circles_and_paths(path):
    """
    Raw content-stream inspection: counts drawn circle-arc operators
    ('c' Bezier curveto, which reportlab's canvas.circle() emits 4 of
    per circle) versus straight-line path operators ('l' lineto),
    giving an independent, non-text signal that real circular and
    rectangular vector entities exist in the PDF.
    """
    reader = pypdf.PdfReader(path)
    content = reader.pages[0].get_contents().get_data().decode("latin-1")
    curve_ops = content.count(" c\n") + content.count(" c ")
    line_ops = content.count(" l\n") + content.count(" l ")
    return curve_ops, line_ops


# === Connection regression 1-4: existing PFC/UB/DXF/CAD behaviour untouched ===
# (Covered by re-running the full existing test files — see the earlier
# PFC/UB PDF tests and test_cad_engine.py / DXF tests above and elsewhere
# in this file, all of which are re-run as part of "run everything" below.
# No separate duplicate tests needed here.)


# === Connection Test 5, 6, 7: UB + END_PLATE geometry generates a readable PDF ===
def test_ub_end_plate_geometry_produces_readable_pdf(ub_connected_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(
        ub_connected_geometry, tmp_path / "UB-TEST-1_310UB40_4000_ENDPLATE.pdf"
    )

    assert out_path.exists()
    reader = pypdf.PdfReader(out_path)  # raises if not a valid PDF
    assert len(reader.pages) == 1


# === Connection Test 8: required member/connection text present ===
def test_ub_end_plate_pdf_contains_member_and_connection_info(ub_connected_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_connected_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "UB-TEST-1" in text
    assert "310UB40" in text
    assert "LENGTH" in text and "4000" in text
    assert "CONNECTION" in text and "END PLATE" in text


# === Connection Test 9: required dimensional values present ===
def test_ub_end_plate_pdf_contains_dimensional_values(ub_connected_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_connected_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "90" in text
    assert "250" in text
    assert "18" in text
    assert "150" in text


# === Connection Test 10 & 12: exactly two hole circles, genuinely non-empty and
# real vector CIRCLE entities (not just text) ===
def test_ub_end_plate_pdf_has_exactly_two_hole_circles(ub_connected_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_connected_geometry, tmp_path / "out.pdf")

    curve_ops, _line_ops = _pdf_circles_and_paths(out_path)
    # reportlab's canvas.circle() draws each circle as 4 bezier curves;
    # this drawing has exactly 2 real holes, so exactly 8 curve ops.
    assert curve_ops == 8


# === Connection Test 11: end plate is visibly rectangular (4-vertex outline) ===
def test_ub_end_plate_outline_is_rectangular():
    geometry = make_ub_connected_geometry()

    far_face = geometry.solid.faces(">Z").val()
    outer_pts = [(v.X, v.Y) for v in far_face.outerWire().Vertices()]
    assert len(outer_pts) == 4

    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]
    assert abs((max(xs) - min(xs)) - 90.0) < 1e-6   # plate width
    assert abs((max(ys) - min(ys)) - 250.0) < 1e-6  # plate height


# === Connection Test 13 & 14: main member dimension stays 4000, never the
# combined member+plate bounding box (4010) ===
def test_ub_end_plate_pdf_member_length_not_combined_with_plate(ub_connected_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_connected_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "LENGTH" in text and "4000" in text
    assert "4010" not in text

    bbox = ub_connected_geometry.solid.val().BoundingBox()
    assert abs(bbox.zmax - 4010.0) < 1e-6  # the real combined solid IS 4010...
    assert abs(ub_connected_geometry.length_mm - 4000.0) < 1e-6  # ...but the member's own length stays 4000


# === Connection Test 15: bare-member PDFs do not gain connection info ===
def test_bare_pdf_has_no_connection_line(ub_geometry, pfc_geometry, tmp_path):
    for geom, name in [(ub_geometry, "ub.pdf"), (pfc_geometry, "pfc.pdf")]:
        out_path = generate_fabrication_drawing_pdf(geom, tmp_path / name)
        text = _pdf_text(out_path).upper()
        assert "CONNECTION" not in text


# === Strong test requirement: changing the hole spacing changes the drawing ===
def test_pdf_hole_spacing_change_is_reflected_in_drawing(tmp_path):
    default_geometry = make_ub_connected_geometry(vertical_spacing=150.0)
    changed_geometry = make_ub_connected_geometry(mark="UB-TEST-2", vertical_spacing=180.0)

    # Confirm the two fixtures really do produce different real geometry first.
    def hole_centers(geometry):
        far_face = geometry.solid.faces(">Z").val()
        centers = []
        for wire in far_face.innerWires():
            _cx, cy, _cz = wire.Center().toTuple()
            centers.append(cy)
        return sorted(centers)

    default_centers = hole_centers(default_geometry)
    changed_centers = hole_centers(changed_geometry)
    assert abs((default_centers[1] - default_centers[0]) - 150.0) < 1e-6
    assert abs((changed_centers[1] - changed_centers[0]) - 180.0) < 1e-6

    default_path = generate_fabrication_drawing_pdf(default_geometry, tmp_path / "default.pdf")
    changed_path = generate_fabrication_drawing_pdf(changed_geometry, tmp_path / "changed.pdf")

    default_text = _pdf_text(default_path)
    changed_text = _pdf_text(changed_path)

    assert "150" in default_text
    assert "180" in changed_text
    # The default drawing must not accidentally also show 180, and vice versa —
    # proving the number in the PDF tracks the real geometry, not a constant.
    assert "180" not in default_text
    assert "150" not in changed_text


# ===================================================================
# Milestone: PDF Connections 1.1 — hole-position edge-distance
# dimensions (plate edge -> hole centreline) and a hole-to-hole
# vertical dimension chain, both computed from the actual extracted
# hole/plate coordinates — never from bolts[0]['vertical_spacing'] or
# plates[0] metadata.
# ===================================================================

# === Strong test: change the actual CAD hole positions (not just spacing) and
# confirm the drawing reflects the new positions, not the old ones ===
def test_pdf_alternate_hole_positions_reflected_in_drawing(tmp_path):
    # vertical_spacing=130, still centred on the plate's mid-height (125mm)
    # by the existing CAD connection builder, gives hole centres at
    # 60mm and 190mm — the example configuration suggested for this test.
    geometry = make_ub_connected_geometry(mark="UB-TEST-3", vertical_spacing=130.0)

    far_face = geometry.solid.faces(">Z").val()
    centers = sorted(w.Center().toTuple()[1] for w in far_face.innerWires())
    assert abs(centers[0] - 60.0) < 1e-6
    assert abs(centers[1] - 190.0) < 1e-6

    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "alt.pdf")
    text = _pdf_text(out_path)

    assert "130" in text       # new hole-to-hole spacing
    assert "60" in text        # new bottom/top edge distances (both are 60, symmetric)
    assert "150" not in text   # the old (default fixture's) spacing must not leak in


# === Strong test: every standard-fixture dimension is independently derived
# from the actual solid, then confirmed present in the generated PDF ===
def test_pdf_dimension_values_match_independently_computed_geometry(tmp_path):
    geometry = make_ub_connected_geometry()  # standard fixture: 150mm spacing

    far_face = geometry.solid.faces(">Z").val()
    outer_pts = [(v.X, v.Y) for v in far_face.outerWire().Vertices()]
    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]
    x_min, y_min = min(xs), min(ys)
    plate_width = max(xs) - x_min
    plate_height = max(ys) - y_min

    holes = []
    for wire in far_face.innerWires():
        edges = [e for e in wire.Edges() if e.geomType() == "CIRCLE"]
        radius = edges[0].Length() / (2 * math.pi)
        cx, cy, _cz = wire.Center().toTuple()
        holes.append((radius, cx - x_min, cy - y_min))
    holes.sort(key=lambda h: h[2])
    (radius, hole_x_offset, y_lo), (_r2, _x2, y_hi) = holes

    bottom_edge_dist = y_lo
    top_edge_dist = plate_height - y_hi
    spacing = y_hi - y_lo
    diameter = radius * 2

    # Sanity-check the independently computed values against the known fixture.
    assert abs(plate_width - 90.0) < 1e-6
    assert abs(plate_height - 250.0) < 1e-6
    assert abs(hole_x_offset - 45.0) < 1e-6
    assert abs(bottom_edge_dist - 50.0) < 1e-6
    assert abs(spacing - 150.0) < 1e-6
    assert abs(top_edge_dist - 50.0) < 1e-6
    assert abs(diameter - 18.0) < 1e-6

    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "std.pdf")
    text = _pdf_text(out_path)
    for value in (plate_width, plate_height, hole_x_offset, bottom_edge_dist, spacing, diameter):
        assert f"{value:g}" in text


# ===================================================================
# Milestone: PDF Fabrication Drawing 2 — professional drawing standard
# (bordered sheet, structured title block with drawing number/rev/
# date/material/units/scale, standardised dimension style). Every
# geometry-derived value keeps coming from the actual CadQuery solid,
# exactly as before — this milestone only changes presentation.
# ===================================================================

# === Test 1: UB metadata — full title block field set ===
def test_ub_pdf_contains_full_title_block_metadata(tmp_path):
    geometry = make_ub_geometry()
    fixed_date = datetime.date(2026, 9, 18)

    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "out.pdf", quantity=1, status="TEST",
        material=None, revision="A", date=fixed_date,
    )
    text = _pdf_text(out_path)

    assert "STEELSPEC" in text
    assert "FABRICATION DRAWING" in text
    assert "DRAWING NO" in text
    assert "FAB-UB-TEST-1" in text
    assert "REV" in text and "A" in text
    assert "DATE" in text and "18/09/2026" in text
    assert "UNITS" in text and "mm" in text
    assert "SCALE" in text and "NTS" in text
    assert "MARK" in text and "UB-TEST-1" in text
    assert "SECTION" in text and "310UB40" in text
    assert "LENGTH" in text and "4000" in text
    assert "QTY" in text and "1" in text
    assert "MATERIAL" in text and "NOT SPECIFIED" in text
    assert "STATUS" in text and "TEST" in text


# === MILESTONE 7AW: no STATUS cell when none was supplied; PAGE 1 OF 1 always ===
def test_ub_pdf_defaults_to_no_status_and_prints_page_1_of_1(tmp_path):
    geometry = make_ub_geometry()
    fixed_date = datetime.date(2026, 9, 18)
    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "out.pdf", quantity=1, date=fixed_date,
    )
    text = _pdf_text(out_path)
    lines = text.splitlines()
    # the drawing never implies an approval state SteelSpec did not record:
    # no STATUS/TEST cell exists as a drawn label/value line at all
    assert "STATUS" not in lines and "TEST" not in lines
    # deterministic single-page numbering is part of the actual artifact
    assert "PAGE" in lines and "1 OF 1" in lines
    # a single-member drawing has no reviewed connection record — it must
    # not manufacture location or attachment semantics
    assert "CONNECTION LOCATION" not in text
    assert "ATTACH" not in text


# === Test 2: PFC metadata — same structure works for a different family ===
def test_pfc_pdf_contains_full_title_block_metadata(tmp_path):
    geometry = make_geometry(mark="P1")
    fixed_date = datetime.date(2026, 9, 18)

    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "out.pdf", quantity=1, status="TEST", date=fixed_date,
    )
    text = _pdf_text(out_path)

    assert "STEELSPEC" in text
    assert "FABRICATION DRAWING" in text
    assert "DRAWING NO" in text
    assert "FAB-P1" in text
    assert "REV" in text and "A" in text
    assert "DATE" in text and "18/09/2026" in text
    assert "MARK" in text and "P1" in text
    assert "SECTION" in text and "250PFC" in text
    assert "MATERIAL" in text and "NOT SPECIFIED" in text


# === Test 7: date determinism — injected date, not the real system date ===
def test_pdf_date_is_injectable_not_system_date(tmp_path):
    geometry = make_ub_geometry()
    injected_date = datetime.date(2020, 1, 15)

    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf", date=injected_date)
    text = _pdf_text(out_path)

    assert "15/01/2020" in text
    today_str = datetime.date.today().strftime("%d/%m/%Y")
    if today_str != "15/01/2020":
        assert today_str not in text


# === Explicit material passthrough: supplied material is never invented, never overwritten ===
def test_pdf_material_is_shown_when_explicitly_supplied(tmp_path):
    geometry = make_ub_geometry()
    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf", material="300PLUS")
    text = _pdf_text(out_path)

    assert "300PLUS" in text
    assert "NOT SPECIFIED" not in text


# ===================================================================
# PDF Fabrication Drawing 3: SHS + RHS. 150x150x6 SHS and 200x100x6
# RHS are synthetic geometry fixtures only — not verified real-world
# steel catalogue sections (same caveat as test_cad_engine.py's own
# fixtures of the same names). Every dimension/topology check below
# reads the actual generated CadQuery solid via
# pdf_builder._extract_end_face_topology() — the exact function
# _draw_cross_section() itself uses — never the input fixture dict,
# so these tests prove the renderer follows the geometry.
# ===================================================================

SECTION_150X150X6_SHS = {
    "name": "150x150x6 SHS", "family": "SHS",
    "width": 150.0, "thickness": 6.0,
    "weight_per_metre": 26.0,
}
SECTION_200X100X6_RHS = {
    "name": "200x100x6 RHS", "family": "RHS",
    "width": 200.0, "depth": 100.0, "thickness": 6.0,
    "weight_per_metre": 26.0,
}


def make_shs_geometry(section_properties=None, length_mm=3000.0, mark="SHS-TEST-1"):
    section_properties = section_properties or SECTION_150X150X6_SHS
    member = ValidatedSteelMember(
        mark=mark, section=section_properties["name"], length_mm=length_mm,
        material="300PLUS", orientation=None, connection_refs=[],
        source_refs=[{"page": 1}], validation_status="extracted",
        section_properties=section_properties,
    )
    return generate_geometry(member)


def make_rhs_geometry(section_properties=None, length_mm=3000.0, mark="RHS-TEST-1"):
    section_properties = section_properties or SECTION_200X100X6_RHS
    member = ValidatedSteelMember(
        mark=mark, section=section_properties["name"], length_mm=length_mm,
        material="300PLUS", orientation=None, connection_refs=[],
        source_refs=[{"page": 1}], validation_status="extracted",
        section_properties=section_properties,
    )
    return generate_geometry(member)


@pytest.fixture
def shs_geometry():
    return make_shs_geometry()


@pytest.fixture
def rhs_geometry():
    return make_rhs_geometry()


# --- SHS ---

# === SHS Test 1 & 2: SHS PDF generates and is readable ===
def test_shs_pdf_generates(shs_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(shs_geometry, tmp_path / "SHS-TEST-1_150x150x6_3000.pdf")

    assert out_path.exists()
    reader = pypdf.PdfReader(out_path)  # raises if not a valid PDF
    assert len(reader.pages) == 1


# === SHS Test 3: title block is correct ===
def test_shs_pdf_title_block_is_correct(shs_geometry, tmp_path):
    fixed_date = datetime.date(2026, 9, 18)
    out_path = generate_fabrication_drawing_pdf(
        shs_geometry, tmp_path / "out.pdf", date=fixed_date, status="TEST")
    text = _pdf_text(out_path)

    assert "STEELSPEC" in text
    assert "FABRICATION DRAWING" in text
    assert "DRAWING NO" in text and "FAB-SHS-TEST-1" in text
    assert "SHS-TEST-1" in text
    assert "150x150x6 SHS" in text
    assert "UNITS" in text and "mm" in text
    assert "SCALE" in text and "NTS" in text
    assert "MATERIAL" in text and "NOT SPECIFIED" in text
    assert "STATUS" in text and "TEST" in text


# === SHS Test 4: elevation contains the member length ===
def test_shs_pdf_elevation_contains_length(shs_geometry, tmp_path):
    assert abs(shs_geometry.length_mm - 3000.0) < 1e-6  # independently confirmed first

    out_path = generate_fabrication_drawing_pdf(shs_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)
    assert "ELEVATION" in text
    assert "3000" in text


# === SHS Test 5: cross-section shows 150 x 150 geometry ===
def test_shs_pdf_cross_section_dimensions(shs_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(shs_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "CROSS SECTION" in text
    assert "150" in text


# === SHS Test 6 & 7: inner opening is rendered — hollow topology confirmed via the
# actual function _draw_cross_section() itself uses ===
def test_shs_inner_opening_is_rendered():
    geometry = make_shs_geometry()

    outer_points, inner_wires_points = _extract_end_face_topology(geometry.solid)
    assert len(outer_points) == 4
    assert len(inner_wires_points) == 1
    assert len(inner_wires_points[0]) == 4

    inner_xs = [p[0] for p in inner_wires_points[0]]
    inner_ys = [p[1] for p in inner_wires_points[0]]
    assert abs((max(inner_xs) - min(inner_xs)) - 138.0) < 1e-6
    assert abs((max(inner_ys) - min(inner_ys)) - 138.0) < 1e-6


# === SHS Test: wall thickness = 6 ===
def test_shs_pdf_wall_thickness_label(shs_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(shs_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "WALL THK: 6 mm" in text
    # UB-specific labels must never appear for a hollow section.
    assert "FLANGE THK" not in text
    assert "WEB THK" not in text


# === SHS Test: real vector paths exist for both outer and inner boundary (not just text) ===
def test_shs_pdf_has_two_closed_rectangular_paths(shs_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(shs_geometry, tmp_path / "out.pdf")

    _curve_ops, line_ops = _pdf_circles_and_paths(out_path)
    # Elevation rect (4 lines) + outer profile (4) + inner profile (4)
    # + title block rect (4) = at least 16 line-to operators; a
    # non-hollow drawing (e.g. bare UB) has 4 fewer (no inner wire).
    assert line_ops >= 16


# === SHS Test 8: alternate SHS geometry follows the actual solid, not hardcoded values ===
def test_shs_alternate_geometry_reflected_in_drawing(tmp_path):
    alt_section = {"name": "120x120x5 SHS", "family": "SHS", "width": 120.0, "thickness": 5.0,
                   "weight_per_metre": 17.0}
    geometry = make_shs_geometry(section_properties=alt_section, mark="SHS-TEST-2")

    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "alt.pdf")
    text = _pdf_text(out_path)

    assert "120" in text
    assert "WALL THK: 5 mm" in text
    assert "150" not in text
    assert "WALL THK: 6 mm" not in text


# --- RHS ---

# === RHS Test 9 & 10: RHS PDF generates, readable, title block correct ===
def test_rhs_pdf_generates_with_correct_title_block(rhs_geometry, tmp_path):
    fixed_date = datetime.date(2026, 9, 18)
    out_path = generate_fabrication_drawing_pdf(
        rhs_geometry, tmp_path / "RHS-TEST-1_200x100x6_3000.pdf", date=fixed_date,
        status="TEST",
    )

    assert out_path.exists()
    reader = pypdf.PdfReader(out_path)
    assert len(reader.pages) == 1

    text = _pdf_text(out_path)
    assert "STEELSPEC" in text
    assert "FABRICATION DRAWING" in text
    assert "DRAWING NO" in text and "FAB-RHS-TEST-1" in text
    assert "RHS-TEST-1" in text
    assert "200x100x6 RHS" in text
    assert "UNITS" in text and "mm" in text
    assert "SCALE" in text and "NTS" in text
    assert "MATERIAL" in text and "NOT SPECIFIED" in text
    assert "STATUS" in text and "TEST" in text


# === RHS Test 11: elevation contains the member length ===
def test_rhs_pdf_elevation_contains_length(rhs_geometry, tmp_path):
    assert abs(rhs_geometry.length_mm - 3000.0) < 1e-6

    out_path = generate_fabrication_drawing_pdf(rhs_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)
    assert "ELEVATION" in text
    assert "3000" in text


# === RHS Test 12: cross-section shows 200 x 100 geometry ===
def test_rhs_pdf_cross_section_dimensions(rhs_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(rhs_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "CROSS SECTION" in text
    assert "200" in text
    assert "100" in text


# === RHS Test 13 & 14: inner opening rendered; hollow topology confirmed ===
def test_rhs_inner_opening_is_rendered():
    geometry = make_rhs_geometry()

    outer_points, inner_wires_points = _extract_end_face_topology(geometry.solid)
    assert len(outer_points) == 4
    assert len(inner_wires_points) == 1
    assert len(inner_wires_points[0]) == 4

    inner_xs = [p[0] for p in inner_wires_points[0]]
    inner_ys = [p[1] for p in inner_wires_points[0]]
    assert abs((max(inner_xs) - min(inner_xs)) - 188.0) < 1e-6
    assert abs((max(inner_ys) - min(inner_ys)) - 88.0) < 1e-6


# === RHS Test: wall thickness = 6 ===
def test_rhs_pdf_wall_thickness_label(rhs_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(rhs_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "WALL THK: 6 mm" in text
    assert "FLANGE THK" not in text
    assert "WEB THK" not in text


# === RHS Test 16: unequal width/depth is preserved — not silently squared ===
def test_rhs_outer_dimensions_are_genuinely_unequal():
    geometry = make_rhs_geometry()

    outer_points, _inner_wires_points = _extract_end_face_topology(geometry.solid)
    xs = [p[0] for p in outer_points]
    ys = [p[1] for p in outer_points]
    outer_width = max(xs) - min(xs)
    outer_depth = max(ys) - min(ys)

    assert abs(outer_width - 200.0) < 1e-6
    assert abs(outer_depth - 100.0) < 1e-6
    assert outer_width != outer_depth  # guards against an accidental 200x200 or 100x100


# === RHS Test 17: alternate RHS geometry follows the actual solid ===
def test_rhs_alternate_geometry_reflected_in_drawing(tmp_path):
    alt_section = {"name": "180x80x5 RHS", "family": "RHS", "width": 180.0, "depth": 80.0,
                   "thickness": 5.0, "weight_per_metre": 19.0}
    geometry = make_rhs_geometry(section_properties=alt_section, mark="RHS-TEST-2")

    # Independently confirm the actual solid's inner opening first.
    outer_points, inner_wires_points = _extract_end_face_topology(geometry.solid)
    inner_xs = [p[0] for p in inner_wires_points[0]]
    inner_ys = [p[1] for p in inner_wires_points[0]]
    assert abs((max(inner_xs) - min(inner_xs)) - 170.0) < 1e-6  # 180 - 2*5
    assert abs((max(inner_ys) - min(inner_ys)) - 70.0) < 1e-6   # 80 - 2*5

    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "alt.pdf")
    text = _pdf_text(out_path)

    assert "180" in text
    assert "80" in text
    assert "WALL THK: 5 mm" in text
    assert "200" not in text
    assert "WALL THK: 6 mm" not in text


# === Geometry-source regression: two different fixtures of the same family
# produce genuinely different drawings, proving no hardcoded per-family values ===
def test_shs_geometry_source_regression(tmp_path):
    small = make_shs_geometry(
        section_properties={"name": "100x100x4 SHS", "family": "SHS", "width": 100.0,
                             "thickness": 4.0, "weight_per_metre": 12.0},
        mark="SHS-SMALL",
    )
    large = make_shs_geometry(
        section_properties={"name": "250x250x8 SHS", "family": "SHS", "width": 250.0,
                             "thickness": 8.0, "weight_per_metre": 60.0},
        mark="SHS-LARGE",
    )

    small_outer, _small_inner = _extract_end_face_topology(small.solid)
    large_outer, _large_inner = _extract_end_face_topology(large.solid)
    small_width = max(p[0] for p in small_outer) - min(p[0] for p in small_outer)
    large_width = max(p[0] for p in large_outer) - min(p[0] for p in large_outer)
    assert small_width != large_width  # the fixtures really are different geometry

    small_text = _pdf_text(generate_fabrication_drawing_pdf(small, tmp_path / "small.pdf"))
    large_text = _pdf_text(generate_fabrication_drawing_pdf(large, tmp_path / "large.pdf"))

    assert "100" in small_text and "WALL THK: 4 mm" in small_text
    assert "250" in large_text and "WALL THK: 8 mm" in large_text
    assert "250" not in small_text
    assert "100" not in large_text


# --- Regression: PFC, UB, UB + end plate (18, 19, 20) ---

# === Regression 18: PFC PDF remains correct after adding SHS/RHS ===
def test_pfc_pdf_regression_after_shs_rhs(pfc_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(pfc_geometry, tmp_path / "P1_250PFC_3000.pdf")
    text = _pdf_text(out_path)

    assert "90" in text
    assert "250" in text
    assert "FLANGE THK" not in text
    assert "WEB THK" not in text


# === Regression 19: UB PDF remains correct after adding SHS/RHS ===
def test_ub_pdf_regression_after_shs_rhs(ub_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(ub_geometry, tmp_path / "UB-TEST-1_310UB40_4000.pdf")
    text = _pdf_text(out_path)

    assert "165" in text
    assert "310" in text
    assert "FLANGE THK: 12 mm" in text
    assert "WEB THK: 8 mm" in text


# === Regression 20: UB + end plate PDF remains correct after adding SHS/RHS ===
def test_ub_end_plate_pdf_regression_after_shs_rhs(ub_connected_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(
        ub_connected_geometry, tmp_path / "UB-TEST-1_310UB40_4000_ENDPLATE.pdf"
    )
    text = _pdf_text(out_path)

    assert "90" in text and "250" in text  # plate width/height
    assert "45" in text  # hole centre offset
    assert "50" in text  # edge distances
    assert "150" in text  # hole spacing
    assert "18" in text  # hole diameter (as part of "Ø18 mm")
    assert "CONNECTION" in text and "END PLATE" in text


# ===================================================================
# Milestone 6E — PDF connection detail views, driven by
# app.cad_engine.interface.GeneratedConnection (Milestone 6D's geometry
# contract) rather than by re-reading geometry.solid or the input
# ValidatedConnection specification. Reuses the established 6C/6D
# two-connection synthetic fixture (310UB40 x 4000mm, START-ENDPLATE-1
# / END-ENDPLATE-1) so these tests exercise the exact same geometry
# the CAD-side milestones already proved correct.
# ===================================================================

START_BOLT_SPEC = {"shaft_diameter": 16.0, "shaft_length": 40.0, "head_across_flats": 24.0, "head_thickness": 10.0}
END_BOLT_SPEC = {"shaft_diameter": 20.0, "shaft_length": 50.0, "head_across_flats": 30.0, "head_thickness": 12.0}


def make_two_connection_geometry(
    mark="UB-TEST-1",
    start_vertical_spacing=150.0,
    end_horizontal_spacing=60.0,
    end_vertical_spacing=120.0,
    start_plate=None,
    reversed_order=False,
    start_bolt_spec=START_BOLT_SPEC,
    end_bolt_spec=END_BOLT_SPEC,
):
    member = ValidatedSteelMember(
        mark=mark, section="310UB40", length_mm=4000.0, material="300PLUS",
        orientation=None, connection_refs=["START-ENDPLATE-1", "END-ENDPLATE-1"],
        source_refs=[{"page": 1}], validation_status="extracted", section_properties=SECTION_310UB40,
    )
    start = ValidatedConnection(
        connection_id="START-ENDPLATE-1", connected_members=[mark],
        plates=[dict(start_plate or {"width": 90.0, "height": 250.0, "thickness": 10.0})],
        bolts=[{"diameter": 18.0, "quantity": 2, "vertical_spacing": start_vertical_spacing}],
        welds=[], dimensions=None, source_refs=[{"page": 1}], validation_status="extracted",
        connection_type="END_PLATE", position="START",
        physical_bolts=dict(start_bolt_spec) if start_bolt_spec else None,
    )
    end = ValidatedConnection(
        connection_id="END-ENDPLATE-1", connected_members=[mark],
        plates=[{"width": 90.0, "height": 250.0, "thickness": 12.0}],
        bolts=[{
            "diameter": 20.0, "quantity": 4,
            "horizontal_spacing": end_horizontal_spacing, "vertical_spacing": end_vertical_spacing,
        }],
        welds=[], dimensions=None, source_refs=[{"page": 1}], validation_status="extracted",
        connection_type="END_PLATE", position="END",
        physical_bolts=dict(end_bolt_spec) if end_bolt_spec else None,
    )
    connections = [end, start] if reversed_order else [start, end]
    return generate_geometry(member, connections=connections)


@pytest.fixture
def two_connection_geometry():
    return make_two_connection_geometry()


# === Primary PDF: readable, both connection details present ===
def test_two_connection_pdf_is_readable(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(
        two_connection_geometry, tmp_path / "UB-TEST-1_310UB40_4000_2CONN.pdf"
    )
    assert out_path.exists()
    reader = pypdf.PdfReader(out_path)
    assert len(reader.pages) == 1


# === Connection identity: both connection IDs and labels appear (Acceptance: START/END identity) ===
def test_two_connection_pdf_contains_connection_identities(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "START CONNECTION" in text
    assert "START-ENDPLATE-1" in text
    assert "END CONNECTION" in text
    assert "END-ENDPLATE-1" in text


# === START/END dimensions: all required values present (Acceptance: START/END dimensions) ===
def test_two_connection_pdf_contains_expected_dimensions(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    for value in ("90", "250", "150", "60", "120"):
        assert value in text
    assert "Ø18" in text  # Ø18 — START hole diameter
    assert "Ø20" in text  # Ø20 — END hole diameter


# === Real vector geometry: 2 + 4 = 6 hole circles actually drawn (not just text).
# Uses a hardware-disabled variant so this test still isolates HOLE geometry alone,
# unaffected by Milestone 6F's additional bolt-head circles on the same fixture. ===
def test_two_connection_pdf_has_six_hole_circles(tmp_path):
    geometry = make_two_connection_geometry(mark="UB-TEST-NOBOLT", start_bolt_spec=None, end_bolt_spec=None)
    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf")
    curve_ops, _line_ops = _pdf_circles_and_paths(out_path)
    assert curve_ops == 24  # 6 real holes x 4 bezier curves per reportlab circle


# === Architecture: connection-detail rendering never touches GeneratedMemberGeometry.solid ===
def test_draw_connection_detail_does_not_require_member_solid(two_connection_geometry):
    from io import BytesIO

    from reportlab.pdfgen import canvas as _canvas

    from app.drawing_generator.pdf_builder import PAGE_SIZE, _draw_connection_detail

    # A GeneratedConnection detached from `geometry` — proves the renderer
    # only ever needs the connection object itself, never geometry.solid.
    connection = two_connection_geometry.connections[0]
    del two_connection_geometry  # the connection object below carries everything needed

    buffer = BytesIO()
    c = _canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    width_page, height_page = _draw_connection_detail(c, connection, (100.0, 100.0), 0.3)
    c.showPage()
    c.save()

    assert width_page > 0
    assert height_page > 0


# === Geometry-driven test (Acceptance §18): changing the actual generated plate
# geometry changes the PDF; the old value disappears ===
def test_pdf_plate_dimension_change_reflected_via_generated_connection(tmp_path):
    baseline = make_two_connection_geometry(mark="UB-TEST-P1")
    modified = make_two_connection_geometry(
        mark="UB-TEST-P2", start_plate={"width": 100.0, "height": 250.0, "thickness": 15.0}
    )

    baseline_text = _pdf_text(generate_fabrication_drawing_pdf(baseline, tmp_path / "baseline.pdf"))
    modified_text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "modified.pdf"))

    assert "45" in baseline_text    # old START hole offset (width/2 = 90/2 = 45)
    assert "100" in modified_text   # new START plate width, actually generated
    assert "45" not in modified_text  # the old offset must not leak into the new drawing


# === Hole pattern sensitivity (Acceptance §19) ===
def test_pdf_hole_spacing_change_reflected_via_generated_connection(tmp_path):
    baseline = make_two_connection_geometry(mark="UB-TEST-H1")
    modified = make_two_connection_geometry(mark="UB-TEST-H2", start_vertical_spacing=180.0)

    baseline_text = _pdf_text(generate_fabrication_drawing_pdf(baseline, tmp_path / "baseline.pdf"))
    modified_text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "modified.pdf"))

    assert "150" in baseline_text
    assert "180" in modified_text
    assert "150" not in modified_text  # old spacing must not persist


# === Connection independence (Acceptance §20): START-only and END-only changes ===
def test_pdf_start_change_does_not_affect_end_detail(tmp_path):
    modified = make_two_connection_geometry(mark="UB-TEST-I1", start_vertical_spacing=180.0)
    text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "out.pdf"))

    assert "180" in text            # START: actually changed
    assert "60" in text and "120" in text  # END: unaffected
    assert "Ø20" in text        # END: hole diameter unaffected


def test_pdf_end_change_does_not_affect_start_detail(tmp_path):
    # 66mm (not 80mm): large enough to differ clearly from the 60mm baseline
    # while keeping each Ø20 hole (radius 10) safely within the 90mm-wide
    # plate — hole centres sit at 45 +/- 33 = 12/78, so the holes span
    # 2-22 and 68-88, both comfortably inside [0, 90].
    modified = make_two_connection_geometry(mark="UB-TEST-I2", end_horizontal_spacing=66.0)
    text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "out.pdf"))

    assert "66" in text   # END: actually changed
    assert "150" in text  # START: unaffected
    assert "Ø18" in text  # START: hole diameter unaffected


# === Connection order independence (Acceptance §21) ===
def test_pdf_connection_order_does_not_affect_identity_or_placement(tmp_path):
    forward = make_two_connection_geometry(mark="UB-TEST-O1", reversed_order=False)
    reversed_geometry = make_two_connection_geometry(mark="UB-TEST-O2", reversed_order=True)

    for geometry, name in ((forward, "fwd.pdf"), (reversed_geometry, "rev.pdf")):
        text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / name))

        assert "START CONNECTION" in text
        assert "END CONNECTION" in text

        start_idx = text.index("START CONNECTION")
        end_idx = text.index("END CONNECTION")
        spacing_150_idx = text.index("150")  # START's own actual vertical spacing
        spacing_60_idx = text.index("60")    # END's own actual horizontal spacing

        # START is always labelled/placed before END regardless of the order
        # `connections=[...]` was supplied in, and each connection's own
        # dimension values land in its own labelled region, not the other's.
        assert start_idx < end_idx
        assert spacing_150_idx < end_idx
        assert spacing_60_idx > start_idx


# === Missing connections (Acceptance §22): a lone connection renders correctly,
# and no phantom detail is invented for the absent one ===
def make_single_connection_geometry(mark: str, connection_id: str, position: str):
    member = ValidatedSteelMember(
        mark=mark, section="310UB40", length_mm=4000.0, material="300PLUS",
        orientation=None, connection_refs=[connection_id], source_refs=[{"page": 1}],
        validation_status="extracted", section_properties=SECTION_310UB40,
    )
    connection = ValidatedConnection(
        connection_id=connection_id, connected_members=[mark],
        plates=[{"width": 90.0, "height": 250.0, "thickness": 10.0}],
        bolts=[{"diameter": 18.0, "quantity": 2, "vertical_spacing": 150.0}],
        welds=[], dimensions=None, source_refs=[{"page": 1}], validation_status="extracted",
        connection_type="END_PLATE", position=position,
    )
    return generate_geometry(member, connections=[connection])


def test_pdf_start_only_connection_produces_only_start_detail(tmp_path):
    geometry = make_single_connection_geometry("UB-START-ONLY", "conn-start-only", "START")
    text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf"))

    assert "START CONNECTION" in text
    assert "conn-start-only" in text
    assert "END CONNECTION" not in text


def test_pdf_end_only_connection_produces_only_end_detail(tmp_path):
    geometry = make_single_connection_geometry("UB-END-ONLY", "conn-end-only", "END")
    text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf"))

    assert "END CONNECTION" in text
    assert "conn-end-only" in text
    assert "START CONNECTION" not in text


# === Bare-member regression (Acceptance §23): no connection views are invented ===
def test_bare_members_produce_no_connection_detail_views(ub_geometry, pfc_geometry, shs_geometry, rhs_geometry,
                                                           tmp_path):
    for geometry, name in (
        (ub_geometry, "ub.pdf"), (pfc_geometry, "pfc.pdf"), (shs_geometry, "shs.pdf"), (rhs_geometry, "rhs.pdf"),
    ):
        text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / name))
        assert "START CONNECTION" not in text
        assert "END CONNECTION" not in text


# ===================================================================
# Milestone 6F — PDF physical bolt representation, consuming
# GeneratedConnection.hardware (Milestone 6B's actual generated bolt
# solids) rather than reconstructing bolts from a physical_bolts input
# spec or from GeneratedHole.center. Reuses the 6E two-connection
# fixture, which already carries physical_bolts on both connections —
# 6E generated that hardware but never drew it; 6F is what actually
# renders it.
# ===================================================================

# === Real vector geometry: with hardware enabled, hole circles (24) plus
# bolt head-extent circles (6 bolts x 4 curves = 24) are both actually drawn ===
def test_two_connection_pdf_with_hardware_has_hole_and_bolt_circles(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    curve_ops, _line_ops = _pdf_circles_and_paths(out_path)
    assert curve_ops == 48  # 6 holes + 6 bolt head circles, x4 bezier curves each


# === START/END bolt counts are visually/textually obvious ===
# NOTE: Milestone 6G replaced the standalone "N BOLTS" label with the
# consolidated "BOLTS: n x Ø.. SHAFT" line in the fabrication-info block —
# see the 6G test block below for the format-specific tests.
def test_two_connection_pdf_shows_bolt_counts(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "BOLTS: 2" in text  # START: 2 physical bolts
    assert "BOLTS: 4" in text  # END: 4 physical bolts


# === No bolt-standard/grade claims are made — synthetic geometry only ===
def test_two_connection_pdf_does_not_claim_a_bolt_standard(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path).upper()

    for forbidden in ("M16", "M20", "8.8", "10.9", "GRADE", "WASHER", "NUT", "THREAD"):
        assert forbidden not in text


# === Optional hardware (Acceptance: optional hardware): holes-only connection
# renders exactly as Milestone 6E — no bolt graphics, no "BOLTS" text ===
def test_connection_without_physical_bolts_has_no_bolt_representation(tmp_path):
    geometry = make_two_connection_geometry(mark="UB-TEST-NB", start_bolt_spec=None, end_bolt_spec=None)
    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "BOLTS" not in text
    curve_ops, _line_ops = _pdf_circles_and_paths(out_path)
    assert curve_ops == 24  # holes only, no bolt-head circles


# === Architecture: hardware rendering never touches GeneratedMemberGeometry.solid,
# GeneratedMemberGeometry.hardware, or a ValidatedConnection spec (Acceptance §28-30) ===
def test_draw_connection_hardware_does_not_require_member_solid(two_connection_geometry):
    from io import BytesIO

    from reportlab.pdfgen import canvas as _canvas

    from app.drawing_generator.pdf_builder import PAGE_SIZE, _draw_connection_detail

    connection = two_connection_geometry.connections[0]
    assert connection.hardware  # sanity: this connection really does carry hardware
    del two_connection_geometry  # the connection object below carries everything needed

    buffer = BytesIO()
    c = _canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    width_page, height_page = _draw_connection_detail(c, connection, (100.0, 100.0), 0.3)
    c.showPage()
    c.save()

    assert width_page > 0
    assert height_page > 0


# === Geometry authority (Acceptance §27): the bolt-geometry extraction pipeline
# is driven by the actual generated bolt solid, independent of the hole and the
# input spec. This calls the exact function build_pdf() itself uses. ===
def test_bolt_geometry_record_follows_actual_generated_hardware_not_hole_or_spec():
    from app.drawing_generator.pdf_builder import _bolt_geometry_record, _connection_hole_records

    baseline = make_two_connection_geometry(mark="UB-TEST-GA1")
    modified = make_two_connection_geometry(
        mark="UB-TEST-GA2", start_bolt_spec={**START_BOLT_SPEC, "shaft_diameter": 14.0}
    )

    baseline_start = next(c for c in baseline.connections if c.position == "START")
    modified_start = next(c for c in modified.connections if c.position == "START")
    baseline_end = next(c for c in baseline.connections if c.position == "END")
    modified_end = next(c for c in modified.connections if c.position == "END")

    baseline_shaft_radii = sorted(_bolt_geometry_record(b)[2] for b in baseline_start.hardware)
    modified_shaft_radii = sorted(_bolt_geometry_record(b)[2] for b in modified_start.hardware)
    assert all(abs(r - 8.0) < 1e-3 for r in baseline_shaft_radii)   # Ø16 baseline shaft
    assert all(abs(r - 7.0) < 1e-3 for r in modified_shaft_radii)   # Ø14 modified shaft

    # The hole stays Ø18 regardless of the bolt-spec change — hole and bolt are
    # genuinely separate, independently measured geometry sources.
    baseline_hole_diam = sorted(2 * r for r, _x, _y in _connection_hole_records(baseline_start))
    modified_hole_diam = sorted(2 * r for r, _x, _y in _connection_hole_records(modified_start))
    assert all(abs(d - 18.0) < 1e-3 for d in baseline_hole_diam)
    assert all(abs(d - 18.0) < 1e-3 for d in modified_hole_diam)

    # END hardware is completely untouched by the START-only spec change.
    end_shaft_radii = sorted(_bolt_geometry_record(b)[2] for b in modified_end.hardware)
    baseline_end_shaft_radii = sorted(_bolt_geometry_record(b)[2] for b in baseline_end.hardware)
    assert end_shaft_radii == baseline_end_shaft_radii
    assert all(abs(r - 10.0) < 1e-3 for r in end_shaft_radii)  # Ø20 END shaft, unaffected


# === Hardware independence (Acceptance §19): START change vs END change, each way ===
def test_pdf_start_bolt_diameter_change_does_not_affect_end_bolt_representation(tmp_path):
    baseline = make_two_connection_geometry(mark="UB-TEST-HI1")
    modified = make_two_connection_geometry(
        mark="UB-TEST-HI2", start_bolt_spec={**START_BOLT_SPEC, "shaft_diameter": 14.0}
    )

    from app.drawing_generator.pdf_builder import _bolt_geometry_record

    baseline_end = next(c for c in baseline.connections if c.position == "END")
    modified_end = next(c for c in modified.connections if c.position == "END")
    baseline_start = next(c for c in baseline.connections if c.position == "START")
    modified_start = next(c for c in modified.connections if c.position == "START")

    assert sorted(_bolt_geometry_record(b)[2] for b in baseline_end.hardware) == \
        sorted(_bolt_geometry_record(b)[2] for b in modified_end.hardware)
    assert sorted(_bolt_geometry_record(b)[2] for b in baseline_start.hardware) != \
        sorted(_bolt_geometry_record(b)[2] for b in modified_start.hardware)

    # Both PDFs still render successfully and both bolt counts still show.
    text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "out.pdf"))
    assert "BOLTS: 2" in text
    assert "BOLTS: 4" in text


def test_pdf_end_bolt_diameter_change_does_not_affect_start_bolt_representation(tmp_path):
    baseline = make_two_connection_geometry(mark="UB-TEST-HI3")
    modified = make_two_connection_geometry(
        mark="UB-TEST-HI4", end_bolt_spec={**END_BOLT_SPEC, "shaft_diameter": 18.0}
    )

    from app.drawing_generator.pdf_builder import _bolt_geometry_record

    baseline_start = next(c for c in baseline.connections if c.position == "START")
    modified_start = next(c for c in modified.connections if c.position == "START")
    baseline_end = next(c for c in baseline.connections if c.position == "END")
    modified_end = next(c for c in modified.connections if c.position == "END")

    assert sorted(_bolt_geometry_record(b)[2] for b in baseline_start.hardware) == \
        sorted(_bolt_geometry_record(b)[2] for b in modified_start.hardware)
    assert sorted(_bolt_geometry_record(b)[2] for b in baseline_end.hardware) != \
        sorted(_bolt_geometry_record(b)[2] for b in modified_end.hardware)


# === Hardware position sensitivity (Acceptance §20): bolt positions follow the
# actual generated hole pattern; only the changed connection's bolts move ===
def test_pdf_hole_spacing_change_moves_bolt_positions_for_that_connection_only(tmp_path):
    from app.drawing_generator.pdf_builder import _bolt_geometry_record

    baseline = make_two_connection_geometry(mark="UB-TEST-PS1")
    modified = make_two_connection_geometry(mark="UB-TEST-PS2", start_vertical_spacing=180.0)

    baseline_start = next(c for c in baseline.connections if c.position == "START")
    modified_start = next(c for c in modified.connections if c.position == "START")
    baseline_end = next(c for c in baseline.connections if c.position == "END")
    modified_end = next(c for c in modified.connections if c.position == "END")

    baseline_ys = sorted(_bolt_geometry_record(b)[1] for b in baseline_start.hardware)
    modified_ys = sorted(_bolt_geometry_record(b)[1] for b in modified_start.hardware)
    assert abs((baseline_ys[1] - baseline_ys[0]) - 150.0) < 1e-3
    assert abs((modified_ys[1] - modified_ys[0]) - 180.0) < 1e-3  # START bolts followed the new hole pattern

    baseline_end_positions = sorted(_bolt_geometry_record(b)[:2] for b in baseline_end.hardware)
    modified_end_positions = sorted(_bolt_geometry_record(b)[:2] for b in modified_end.hardware)
    assert baseline_end_positions == modified_end_positions  # END bolts completely unaffected


# === Connection order independence (Acceptance: ordering) — hardware follows
# the correct connection regardless of input list order ===
def test_pdf_reversed_connection_order_still_shows_correct_bolt_counts(tmp_path):
    forward = make_two_connection_geometry(mark="UB-TEST-RO1", reversed_order=False)
    reversed_geometry = make_two_connection_geometry(mark="UB-TEST-RO2", reversed_order=True)

    for geometry, name in ((forward, "fwd.pdf"), (reversed_geometry, "rev.pdf")):
        text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / name))
        start_idx = text.index("START CONNECTION")
        end_idx = text.index("END CONNECTION")
        two_bolts_idx = text.index("BOLTS: 2")
        four_bolts_idx = text.index("BOLTS: 4")

        assert start_idx < end_idx
        assert two_bolts_idx < end_idx    # "BOLTS: 2" belongs to the START region
        assert four_bolts_idx > start_idx  # "BOLTS: 4" belongs to the END region (after START's own block)


# === SHS/RHS regression (Acceptance §33): hardware rendering is generic,
# no section-family-specific logic is required for it to work ===
def test_shs_end_plate_with_physical_bolts_renders_generically(tmp_path):
    shs_member = ValidatedSteelMember(
        mark="SHS-BOLT-TEST", section="150x150x6 SHS", length_mm=3000.0, material="300PLUS",
        orientation=None, connection_refs=["conn-shs"], source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties={
            "name": "150x150x6 SHS", "family": "SHS", "width": 150.0, "thickness": 6.0, "weight_per_metre": 26.0,
        },
    )
    shs_connection = ValidatedConnection(
        connection_id="conn-shs", connected_members=["SHS-BOLT-TEST"],
        plates=[{"width": 150.0, "height": 150.0, "thickness": 10.0}],
        bolts=[{"diameter": 18.0, "quantity": 4, "horizontal_spacing": 90.0, "vertical_spacing": 90.0}],
        welds=[], dimensions=None, source_refs=[{"page": 1}], validation_status="extracted",
        connection_type="END_PLATE",
        physical_bolts={"shaft_diameter": 16.0, "shaft_length": 40.0, "head_across_flats": 24.0, "head_thickness": 10.0},
    )
    geometry = generate_geometry(shs_member, connections=[shs_connection])

    out_path = generate_fabrication_drawing_pdf(geometry, tmp_path / "shs_bolt.pdf")
    text = _pdf_text(out_path)
    assert "BOLTS: 4" in text
    assert "END CONNECTION" in text  # position defaults to END


# ===================================================================
# Milestone 6G — compact fabrication-information annotations
# (PLATE/HOLES/BOLTS/PATTERN), consuming GeneratedConnection's own
# generated geometry (geometry_bounds, holes, hardware) rather than
# the original ValidatedConnection specification. Reuses the
# established two-connection fixture from 6E/6F.
# ===================================================================

class _StubHole:
    """A minimal GeneratedHole-like stand-in (just `.center`) for unit-testing
    _pattern_summary_line() directly, without needing real CAD geometry."""
    def __init__(self, x, y):
        self.center = (x, y, 0.0)


class _StubConnection:
    """A minimal GeneratedConnection-like stand-in (just `.holes`) for the same purpose."""
    def __init__(self, holes):
        self.holes = holes


# === Plate thickness is annotated, from actual generated plate geometry ===
def test_plate_thickness_is_annotated(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "PLATE: 90 × 250 × 10 mm" in text  # START
    assert "PLATE: 90 × 250 × 12 mm" in text  # END


# === Hole summary: single diameter, compact form ===
def test_hole_summary_single_diameter(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "HOLES: 2 × Ø18" in text  # START
    assert "HOLES: 4 × Ø20" in text  # END


# === Bolt summary: single diameter, compact form, distinct from hole diameter ===
def test_bolt_summary_single_diameter(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "BOLTS: 2 × Ø16 SHAFT" in text  # START
    assert "BOLTS: 4 × Ø20 SHAFT" in text  # END

    # Hole/bolt separation (Acceptance §17): never collapsed into one line.
    assert "Ø18 BOLTS" not in text
    assert "Ø20 BOLTS" not in text


# === Pattern summary: simple vertical line and simple rectangular grid ===
def test_pattern_summary_for_both_connections(two_connection_geometry, tmp_path):
    out_path = generate_fabrication_drawing_pdf(two_connection_geometry, tmp_path / "out.pdf")
    text = _pdf_text(out_path)

    assert "PATTERN: 150 V" in text                  # START: 2-hole vertical line
    assert "PATTERN: 60 H × 120 V" in text        # END: 4-hole rectangular grid


# === Plate thickness authority (Acceptance §19): comes from connection.plate,
# never plates[0]['thickness'] ===
def test_plate_thickness_authority(tmp_path):
    baseline = make_two_connection_geometry(mark="UB-TEST-PT1")
    modified = make_two_connection_geometry(
        mark="UB-TEST-PT2", start_plate={"width": 90.0, "height": 250.0, "thickness": 15.0}
    )

    baseline_text = _pdf_text(generate_fabrication_drawing_pdf(baseline, tmp_path / "baseline.pdf"))
    modified_text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "modified.pdf"))

    assert "PLATE: 90 × 250 × 10 mm" in baseline_text
    assert "PLATE: 90 × 250 × 15 mm" in modified_text
    assert "PLATE: 90 × 250 × 10 mm" not in modified_text  # old thickness must not leak


# === Hole authority (Acceptance §20): hole diameter is independent of bolt shaft diameter ===
def test_hole_diameter_authority_independent_of_bolt_diameter(tmp_path):
    member = ValidatedSteelMember(
        mark="UB-TEST-HA", section="310UB40", length_mm=4000.0, material="300PLUS",
        orientation=None, connection_refs=["conn-a"], source_refs=[{"page": 1}],
        validation_status="extracted", section_properties=SECTION_310UB40,
    )
    connection = ValidatedConnection(
        connection_id="conn-a", connected_members=["UB-TEST-HA"],
        plates=[{"width": 90.0, "height": 250.0, "thickness": 10.0}],
        bolts=[{"diameter": 20.0, "quantity": 2, "vertical_spacing": 150.0}],  # hole = Ø20
        welds=[], dimensions=None, source_refs=[{"page": 1}], validation_status="extracted",
        connection_type="END_PLATE", position="START",
        physical_bolts={  # bolt shaft stays Ø16
            "shaft_diameter": 16.0, "shaft_length": 40.0, "head_across_flats": 24.0, "head_thickness": 10.0,
        },
    )
    geometry = generate_geometry(member, connections=[connection])

    text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf"))
    assert "HOLES: 2 × Ø20" in text
    assert "BOLTS: 2 × Ø16 SHAFT" in text


# === Bolt/hole independence, reverse direction (Acceptance §21) ===
def test_bolt_diameter_change_leaves_hole_diameter_unchanged(tmp_path):
    modified = make_two_connection_geometry(
        mark="UB-TEST-BI", start_bolt_spec={**START_BOLT_SPEC, "shaft_diameter": 14.0}
    )
    text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "out.pdf"))

    assert "HOLES: 2 × Ø18" in text          # hole: unaffected
    assert "BOLTS: 2 × Ø14 SHAFT" in text     # bolt: actually changed


# === Connection independence for the full info block (Acceptance: independence) ===
def test_start_info_block_changes_do_not_affect_end_info_block(tmp_path):
    modified = make_two_connection_geometry(
        mark="UB-TEST-CI1",
        start_plate={"width": 90.0, "height": 250.0, "thickness": 15.0},
        start_vertical_spacing=180.0,
        start_bolt_spec={**START_BOLT_SPEC, "shaft_diameter": 14.0},
    )
    text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "out.pdf"))

    # START: everything actually changed.
    assert "PLATE: 90 × 250 × 15 mm" in text
    assert "PATTERN: 180 V" in text
    assert "BOLTS: 2 × Ø14 SHAFT" in text

    # END: completely untouched.
    assert "PLATE: 90 × 250 × 12 mm" in text
    assert "HOLES: 4 × Ø20" in text
    assert "BOLTS: 4 × Ø20 SHAFT" in text
    assert "PATTERN: 60 H × 120 V" in text


def test_end_info_block_changes_do_not_affect_start_info_block(tmp_path):
    # 66mm (not 80mm): large enough to differ clearly from the 60mm baseline
    # while keeping each Ø20 hole (radius 10) safely within the 90mm-wide
    # plate — hole centres sit at 45 +/- 33 = 12/78, so the holes span
    # 2-22 and 68-88, both comfortably inside [0, 90].
    modified = make_two_connection_geometry(
        mark="UB-TEST-CI2",
        end_horizontal_spacing=66.0,
        end_bolt_spec={**END_BOLT_SPEC, "shaft_diameter": 18.0},
    )
    text = _pdf_text(generate_fabrication_drawing_pdf(modified, tmp_path / "out.pdf"))

    # END: actually changed.
    assert "PATTERN: 66 H × 120 V" in text
    assert "BOLTS: 4 × Ø18 SHAFT" in text

    # START: completely untouched.
    assert "PLATE: 90 × 250 × 10 mm" in text
    assert "HOLES: 2 × Ø18" in text
    assert "BOLTS: 2 × Ø16 SHAFT" in text
    assert "PATTERN: 150 V" in text


# === Optional hardware (Acceptance: optional hardware): holes remain, bolts do not ===
def test_no_physical_bolts_omits_bolt_summary_but_keeps_hole_summary(tmp_path):
    geometry = make_two_connection_geometry(mark="UB-TEST-OPT", start_bolt_spec=None, end_bolt_spec=None)
    text = _pdf_text(generate_fabrication_drawing_pdf(geometry, tmp_path / "out.pdf"))

    assert "HOLES: 2 × Ø18" in text
    assert "HOLES: 4 × Ø20" in text
    assert "BOLTS" not in text
    # Plate/pattern lines are unaffected by hardware being absent.
    assert "PLATE: 90 × 250 × 10 mm" in text
    assert "PATTERN: 150 V" in text


# === Detached connection (Acceptance: detached rendering) — the info block
# needs nothing beyond the GeneratedConnection object itself ===
def test_connection_info_lines_work_on_a_detached_connection(two_connection_geometry):
    from app.drawing_generator.pdf_builder import _connection_info_lines

    connection = next(c for c in two_connection_geometry.connections if c.position == "START")
    del two_connection_geometry  # the connection object below carries everything needed

    lines = _connection_info_lines(connection)
    assert lines == [
        "PLATE: 90 × 250 × 10 mm",
        "HOLES: 2 × Ø18",
        "BOLTS: 2 × Ø16 SHAFT",
        "PATTERN: 150 V",
    ]


# === Diameter grouping — the underlying formatting logic, unit-tested directly ===
def test_diameter_summary_single_group_is_one_line():
    from app.drawing_generator.pdf_builder import _diameter_summary_lines

    assert _diameter_summary_lines("HOLES", [18.0, 18.0]) == ["HOLES: 2 × Ø18"]


def test_diameter_summary_mixed_diameters_are_grouped(tmp_path):
    from app.drawing_generator.pdf_builder import _diameter_summary_lines

    lines = _diameter_summary_lines("HOLES", [18.0, 18.0, 20.0, 20.0])
    assert lines[0] == "HOLES:"
    assert any("2 × Ø18" in line for line in lines[1:])
    assert any("2 × Ø20" in line for line in lines[1:])
    assert len(lines) == 3  # header + one line per distinct diameter


def test_diameter_summary_empty_list_is_omitted():
    from app.drawing_generator.pdf_builder import _diameter_summary_lines

    assert _diameter_summary_lines("BOLTS", []) == []


# === Pattern derivation and safety — unit-tested directly against synthetic
# hole-centre records (Acceptance: pattern) ===
def test_pattern_summary_vertical_two_hole():
    from app.drawing_generator.pdf_builder import _pattern_summary_line

    connection = _StubConnection([_StubHole(45, 50), _StubHole(45, 200)])
    assert _pattern_summary_line(connection) == "PATTERN: 150 V"


def test_pattern_summary_rectangular_four_hole():
    from app.drawing_generator.pdf_builder import _pattern_summary_line

    connection = _StubConnection(
        [_StubHole(15, 65), _StubHole(75, 65), _StubHole(15, 185), _StubHole(75, 185)]
    )
    assert _pattern_summary_line(connection) == "PATTERN: 60 H × 120 V"


def test_pattern_summary_omitted_for_single_hole():
    from app.drawing_generator.pdf_builder import _pattern_summary_line

    assert _pattern_summary_line(_StubConnection([_StubHole(45, 125)])) is None


def test_pattern_summary_omitted_for_irregular_three_hole_layout():
    from app.drawing_generator.pdf_builder import _pattern_summary_line

    connection = _StubConnection([_StubHole(10, 10), _StubHole(50, 60), _StubHole(80, 20)])
    assert _pattern_summary_line(connection) is None


def test_pattern_summary_omitted_for_five_holes_with_coincidental_grid_coordinates():
    from app.drawing_generator.pdf_builder import _pattern_summary_line

    # 5 holes but only 2 distinct X / 2 distinct Y (one coordinate repeated) —
    # must NOT be mislabelled as a clean 4-hole rectangular grid.
    connection = _StubConnection([
        _StubHole(15, 65), _StubHole(75, 65), _StubHole(15, 185), _StubHole(75, 185), _StubHole(15, 65),
    ])
    assert _pattern_summary_line(connection) is None


# === No-hole safety (Acceptance §24): a holeless connection gets no fabricated summary ===
def test_no_holes_produces_no_hole_or_pattern_lines():
    from app.drawing_generator.pdf_builder import _connection_info_lines

    connection = _StubConnection([])
    connection.geometry_bounds = type("B", (), {"xlen": 90.0, "ylen": 250.0, "zlen": 10.0})()
    connection.hardware = []

    lines = _connection_info_lines(connection)
    assert lines == ["PLATE: 90 × 250 × 10 mm"]  # no "HOLES: 0 x Ø0", no pattern, no bolts


# === Ordering (Acceptance: ordering) — reversed input still attaches the
# correct fabrication info to each labelled connection ===
def test_reversed_connection_order_still_attaches_correct_info_block(tmp_path):
    reversed_geometry = make_two_connection_geometry(mark="UB-TEST-ORD", reversed_order=True)
    text = _pdf_text(generate_fabrication_drawing_pdf(reversed_geometry, tmp_path / "out.pdf"))

    start_idx = text.index("START CONNECTION")
    end_idx = text.index("END CONNECTION")
    start_plate_idx = text.index("PLATE: 90 × 250 × 10 mm")   # START's own plate
    end_plate_idx = text.index("PLATE: 90 × 250 × 12 mm")     # END's own plate

    assert start_idx < end_idx
    assert start_idx < start_plate_idx < end_idx  # START's info sits within START's own region
    assert end_plate_idx > start_idx
