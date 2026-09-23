"""
Milestone 7F — the final link in the real-data chain proven across
7A-7E:

    real member row -> 7A adapter -> ValidatedSteelMember
    human-reviewed connection record -> 7D adapter -> ValidatedConnection
                    -> existing generate_geometry()
                    -> GeneratedMemberGeometry
                    -> existing generate_fabrication_drawing_pdf()
                    -> a real, reviewed-connection fabrication PDF

No production code changes were needed for this milestone (see the
final report) — generate_fabrication_drawing_pdf()'s existing
signature already only accepts a GeneratedMemberGeometry plus
presentation metadata (quantity, status, material, revision, date);
there is no parameter through which a raw record, a ValidatedConnection,
or a ValidatedSteelMember could reach it. The architecture boundary
this milestone requires was already enforced by the existing API shape,
exactly as found in 7C.

FIXTURE PROVENANCE: the connection fixture reused here
(tests.test_real_connection_adapter.make_7e_connection_record) is the
same HUMAN-REVIEWED / SUPPLEMENTED record 7E used — 180x250x12mm end
plate, 4xO22mm holes, 90mm horizontal / 140mm vertical spacing,
explicit END position, explicit member association. It is derived from
7D's COMPLETE_REVIEWED_RECORD, itself derived from the real production
extraction schema (app/ai_analysis/pdf_vision_analyzer.py's own
EXTRACTION_SYSTEM_PROMPT) with the fields that schema cannot capture
(position, numeric hole diameter, hole spacing) explicitly supplied —
never an untouched AI extraction. See tests/test_real_connection_adapter.py's
own module docstring for the full provenance chain.

PHYSICAL BOLTS: the 7D adapter never populates `physical_bolts` — by
design, not by omission (see app/cad_engine/real_connection_adapter.py's
module docstring: nothing in the current extraction schema captures
physical hardware dimensions, and a nominal bolt size like "M20" is not
a reliable source for shaft/head geometry). This means the primary
7F integration necessarily demonstrates the no-physical-bolts path —
holes only, no bolt graphics, no "BOLTS:" line. A supplementary test
proves the 6F/6G hardware-rendering path remains reachable through
this exact same integration point once a validated connection's own
`physical_bolts` is supplied — the same "wire an already-existing
field after adapter construction" pattern 7B/7E already established
for `connection_refs`, not a redesign of the 7D contract.
"""
import datetime

import pypdf
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.drawing_generator.interface import generate_fabrication_drawing_pdf
from tests.test_real_connection_adapter import make_7e_connection_record
from tests.test_real_member_adapter import FULL_UB_SECTION, make_matcher, make_primary_7b_row


def _pdf_text(path) -> str:
    reader = pypdf.PdfReader(path)
    return reader.pages[0].extract_text()


def _build_primary_pdf(tmp_path, member_row=None, connection_record=None, filename="out.pdf", **pdf_kwargs):
    """
    The full 7F chain in one place: real member row -> 7A adapter,
    reviewed connection record -> 7D adapter, connection_refs wired
    afterward (never hand-built) -> existing generate_geometry() ->
    existing generate_fabrication_drawing_pdf(). Returns
    (geometry, pdf_path) so callers can inspect both the actual
    GeneratedMemberGeometry and the resulting file.
    """
    member_row = member_row or make_primary_7b_row()
    connection_record = connection_record or make_7e_connection_record()

    validated_member = real_member_to_validated_member(member_row, make_matcher())
    validated_connection = real_connection_to_validated_connection(connection_record)
    validated_member.connection_refs = [validated_connection.connection_id]

    geometry = generate_geometry(validated_member, connections=[validated_connection])
    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / filename, revision="A", date=datetime.date(2026, 1, 1), **pdf_kwargs,
    )
    return geometry, out_path


# === Definition-of-done 1-7: the primary chain produces a valid PDF ===
def test_primary_reviewed_connection_pdf_is_valid_and_non_empty(tmp_path):
    geometry, out_path = _build_primary_pdf(tmp_path)

    # Confirm the generated geometry itself before it ever reaches the PDF.
    assert geometry.solid is not None
    assert len(geometry.solid.solids().vals()) == 1
    assert geometry.connection_count == 1
    assert len(geometry.connections) == 1

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    reader = pypdf.PdfReader(out_path)  # raises if not a genuinely valid PDF
    assert len(reader.pages) == 1


# === Definition-of-done 8: real member identity reaches the PDF ===
def test_primary_pdf_contains_real_member_identity(tmp_path):
    _geometry, out_path = _build_primary_pdf(tmp_path)
    text = _pdf_text(out_path)

    assert "REAL-UB-CAD-001" in text
    assert "310UB40" in text
    assert "4000" in text
    assert "FABRICATION DRAWING" in text
    assert "ELEVATION" in text
    assert "CROSS SECTION" in text
    assert "UB-TEST-1" not in text  # never the generic synthetic mark


# === Definition-of-done 9, 12: real connection identity reaches the PDF, END only ===
def test_primary_pdf_contains_real_connection_identity_and_is_end_only(tmp_path):
    _geometry, out_path = _build_primary_pdf(tmp_path)
    text = _pdf_text(out_path)

    assert "END CONNECTION" in text
    assert "CONN-REAL-UB-CAD-001-END" in text
    assert "START CONNECTION" not in text  # no START connection exists in this fixture


# === Definition-of-done 10-11: PDF content is traceable to actual generated
# CAD geometry (plate + holes), never fixture values asserted blindly ===
def test_primary_pdf_dimensions_match_generated_geometry(tmp_path):
    geometry, out_path = _build_primary_pdf(tmp_path)

    connection = geometry.connections[0]
    bounds = connection.geometry_bounds
    plate_width, plate_height, plate_thickness = round(bounds.xlen, 6), round(bounds.ylen, 6), round(bounds.zlen, 6)
    assert plate_width == 180.0
    assert plate_height == 250.0
    assert plate_thickness == 12.0

    holes = connection.holes
    assert len(holes) == 4
    diameters = sorted({round(h.diameter, 6) for h in holes})
    assert diameters == [22.0]
    xs = sorted({round(h.center[0], 3) for h in holes})
    ys = sorted({round(h.center[1], 3) for h in holes})
    horizontal_spacing = round(xs[1] - xs[0], 6)
    vertical_spacing = round(ys[1] - ys[0], 6)
    assert horizontal_spacing == 90.0
    assert vertical_spacing == 140.0

    text = _pdf_text(out_path)
    for value in (plate_width, plate_height, plate_thickness, horizontal_spacing, vertical_spacing):
        assert f"{value:g}" in text
    assert "Ø22" in text  # Ø22, the measured hole diameter

    # The PDF call itself only ever received `geometry` — never the reviewed
    # record, the ValidatedConnection, or the ValidatedSteelMember — so
    # there is no code path by which it could have recomputed these numbers
    # from anything but the generated solid/connection.


# === Definition-of-done 13: physical bolts are never invented ===
def test_primary_pdf_has_no_bolt_graphics_or_text_since_7d_never_populates_physical_bolts(tmp_path):
    geometry, out_path = _build_primary_pdf(tmp_path)

    assert geometry.connections[0].hardware == []
    assert geometry.hardware == []

    text = _pdf_text(out_path).upper()
    assert "BOLTS" not in text


# === Supplementary: the 6F/6G hardware-rendering path remains reachable
# through this exact integration point once physical_bolts IS supplied —
# not part of 7D's own contract, but proves nothing downstream needs to
# change if a future reviewer/extraction enhancement adds it. The field
# is wired onto the already-adapter-built ValidatedConnection, the same
# "supplement after construction" pattern already used for connection_refs. ===
def test_hardware_rendering_path_remains_reachable_if_physical_bolts_is_supplied(tmp_path):
    member_row = make_primary_7b_row()
    connection_record = make_7e_connection_record(connection_id="CONN-WITH-HARDWARE")

    validated_member = real_member_to_validated_member(member_row, make_matcher())
    validated_connection = real_connection_to_validated_connection(connection_record)
    validated_connection.physical_bolts = {
        "shaft_diameter": 20.0, "shaft_length": 50.0, "head_across_flats": 30.0, "head_thickness": 12.0,
    }
    validated_member.connection_refs = [validated_connection.connection_id]

    geometry = generate_geometry(validated_member, connections=[validated_connection])
    assert len(geometry.connections[0].hardware) == 4

    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "with_hardware.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )
    text = _pdf_text(out_path)
    assert "BOLTS: 4" in text


# === Definition-of-done 14: source records can be discarded after
# CAD generation and the PDF still generates correctly ===
def test_pdf_generation_is_independent_of_the_original_raw_records(tmp_path):
    member_row = make_primary_7b_row()
    connection_record = make_7e_connection_record(connection_id="CONN-INDEPENDENCE")

    validated_member = real_member_to_validated_member(member_row, make_matcher())
    validated_connection = real_connection_to_validated_connection(connection_record)
    validated_member.connection_refs = [validated_connection.connection_id]

    geometry = generate_geometry(validated_member, connections=[validated_connection])

    member_row.clear()
    connection_record.clear()

    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "independence.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )
    text = _pdf_text(out_path)

    assert "REAL-UB-CAD-001" in text
    assert "END CONNECTION" in text
    assert "CONN-INDEPENDENCE" in text
    assert "180" in text and "250" in text and "12" in text
    assert "Ø22" in text


# === Definition-of-done 15: plate thickness sensitivity, full chain ===
def test_plate_thickness_change_reaches_pdf(tmp_path):
    baseline_geometry, baseline_pdf = _build_primary_pdf(tmp_path, filename="baseline.pdf")

    modified_record = make_7e_connection_record(connection_id="CONN-THICK-15")
    modified_record["plates"][0]["thickness_mm"] = 15.0
    modified_geometry, modified_pdf = _build_primary_pdf(
        tmp_path, connection_record=modified_record, filename="thick15.pdf",
    )

    assert abs(baseline_geometry.connections[0].geometry_bounds.zlen - 12.0) < 1e-6
    assert abs(modified_geometry.connections[0].geometry_bounds.zlen - 15.0) < 1e-6

    baseline_text = _pdf_text(baseline_pdf)
    modified_text = _pdf_text(modified_pdf)
    assert "PLATE: 180 × 250 × 12 mm" in baseline_text
    assert "PLATE: 180 × 250 × 15 mm" in modified_text


# === Definition-of-done 16: hole diameter sensitivity, full chain ===
def test_hole_diameter_change_reaches_pdf(tmp_path):
    modified_record = make_7e_connection_record(connection_id="CONN-DIA-20")
    modified_record["bolts"][0]["diameter_mm"] = 20.0
    geometry, out_path = _build_primary_pdf(tmp_path, connection_record=modified_record, filename="dia20.pdf")

    diameters = {round(h.diameter, 6) for h in geometry.connections[0].holes}
    assert diameters == {20.0}

    text = _pdf_text(out_path)
    assert "Ø20" in text
    assert "HOLES: 4 × Ø20" in text


# === Definition-of-done 16 (hole spacing): full chain ===
def test_hole_spacing_change_reaches_pdf(tmp_path):
    modified_record = make_7e_connection_record(connection_id="CONN-SPACING")
    modified_record["bolts"][0]["vertical_spacing_mm"] = 160.0
    modified_record["bolts"][0]["horizontal_spacing_mm"] = 100.0
    geometry, out_path = _build_primary_pdf(tmp_path, connection_record=modified_record, filename="spacing.pdf")

    holes = geometry.connections[0].holes
    xs = sorted({round(h.center[0], 3) for h in holes})
    ys = sorted({round(h.center[1], 3) for h in holes})
    assert abs((xs[1] - xs[0]) - 100.0) < 1e-6
    assert abs((ys[1] - ys[0]) - 160.0) < 1e-6

    text = _pdf_text(out_path)
    assert "PATTERN: 100 H × 160 V" in text
    assert "PATTERN: 90 H × 140 V" not in text  # old spacing must not leak through


# === Definition-of-done 17: member-length sensitivity, full chain ===
def test_member_length_change_reaches_pdf_connection_remains_attached_at_end(tmp_path):
    member_row = make_primary_7b_row(mark="REAL-UB-CAD-001", length_mm=2750)
    geometry, out_path = _build_primary_pdf(tmp_path, member_row=member_row, filename="len2750.pdf")

    assert geometry.length_mm == 2750.0
    assert geometry.connection_count == 1
    bounds = geometry.connections[0].geometry_bounds
    assert abs(bounds.zmin - 2750.0) < 1e-6  # plate now attaches at the member's new END

    # Member's own cross-section (read from its untouched START face — see
    # tests/test_real_connection_adapter.py's equivalent test for why the
    # overall assembly bbox is NOT used: this 180mm-wide plate is wider
    # than the UB's own 165mm flange) is unaffected by the length change.
    start_face = geometry.solid.faces("<Z").val()
    xs = [v.X for v in start_face.outerWire().Vertices()]
    ys = [v.Y for v in start_face.outerWire().Vertices()]
    assert abs((max(xs) - min(xs)) - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs((max(ys) - min(ys)) - FULL_UB_SECTION["depth"]) < 1e-6

    text = _pdf_text(out_path)
    assert "2750" in text
    assert "310UB40" in text
    assert "PLATE: 180 × 250 × 12 mm" in text  # connection dimensions unchanged


# === Definition-of-done 18: invalid 7D records never reach CAD or PDF ===
def test_missing_position_never_reaches_cad_or_pdf(tmp_path):
    record = make_7e_connection_record(connection_id="CONN-NOPOS", position=None)
    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="position"):
        real_connection_to_validated_connection(record)
    assert not out_path.exists()


def test_missing_plate_dimension_never_reaches_cad_or_pdf(tmp_path):
    record = make_7e_connection_record(connection_id="CONN-NOPLATE")
    record["plates"][0]["width_mm"] = None
    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="width_mm"):
        real_connection_to_validated_connection(record)
    assert not out_path.exists()


def test_missing_hole_diameter_never_reaches_cad_or_pdf(tmp_path):
    record = make_7e_connection_record(connection_id="CONN-NOHOLE")
    del record["bolts"][0]["diameter_mm"]
    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="diameter_mm"):
        real_connection_to_validated_connection(record)
    assert not out_path.exists()


def test_unsupported_connection_type_never_reaches_cad_or_pdf(tmp_path):
    record = make_7e_connection_record(connection_id="CONN-BADTYPE")
    record["plates"][0]["type"] = "gusset"
    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="does not correspond to a supported connection type"):
        real_connection_to_validated_connection(record)
    assert not out_path.exists()


def test_missing_member_association_never_reaches_cad_or_pdf(tmp_path):
    record = make_7e_connection_record(connection_id="CONN-NOMEMBER", connected_member_marks=[])
    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="member association"):
        real_connection_to_validated_connection(record)
    assert not out_path.exists()
