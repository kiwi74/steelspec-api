"""
Milestone 7D — tests for the boundary between a real extracted/
persisted connection detail and the CAD engine's ValidatedConnection
contract.

No CAD geometry is generated anywhere in this file — no
generate_geometry(), no app.cad_engine.connections builder call. This
file proves only: real connection record -> ValidatedConnection (or a
clear rejection). Milestone 7E is where the resulting ValidatedConnection
actually reaches the CAD connection engine.

PRIMARY FIXTURE — SOURCE EVIDENCE:

    SOURCE DRAWING:   none available. tests/test_arkles_strand.py's
                       real 54-row Arkles Strand extraction (the only
                       real drawing dataset this repository has test
                       fixtures for) captured MEMBER rows only — no
                       connection/detail was ever extracted into a test
                       fixture for that project. This is reported
                       honestly rather than pretending a real
                       connection record exists.

    SCHEMA SOURCE:     app/ai_analysis/pdf_vision_analyzer.py's own
                        EXTRACTION_SYSTEM_PROMPT — the actual, real
                        production JSON shape Claude's vision extraction
                        is instructed to return for a connection,
                        inspected directly (not guessed). PRIMARY_REAL_SCHEMA_RECORD
                        below reuses that exact example verbatim
                        (detail_reference "D15", grid_reference "A-B/2",
                        connects_members ["B8", "C1"], connection_type
                        "bolted", bolts [{"quantity": 4, "size": "M20",
                        "grade": "8.8"}], plates [{"type": "end_plate",
                        "thickness_mm": 12, "width_mm": 180,
                        "depth_mm": 250}]).

    EXPLICITLY AVAILABLE INFORMATION: connection identity, member
    association (B8, C1), a plate explicitly typed "end_plate" with
    width/depth/thickness all in mm, and a bolt group with quantity
    and a nominal size ("M20") + grade.

    EXPLICITLY NOT AVAILABLE: connection position (START/END) — no
    field for this exists anywhere in the real extraction schema.
    Numeric hole-void diameter and hole spacing — the schema only ever
    captures a nominal bolt designation string, never a mm diameter or
    any spacing value.

    This is why the PRIMARY test (test_primary_real_schema_connection_is_rejected)
    is a rejection, not a success — and why that is the correct,
    honest result for this milestone (see the module docstring in
    app/cad_engine/real_connection_adapter.py). A second fixture
    (COMPLETE_REVIEWED_RECORD), clearly labelled as NOT an as-extracted
    record but a plausible human-reviewed/supplemented one, proves the
    success path is real.
"""
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedConnection, generate_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from tests.test_real_member_adapter import FULL_UB_SECTION, make_matcher, make_primary_7b_row

# The exact real extraction schema, reused verbatim from
# app/ai_analysis/pdf_vision_analyzer.py's EXTRACTION_SYSTEM_PROMPT
# example — not invented for this test file.
PRIMARY_REAL_SCHEMA_RECORD = {
    "connection_id": "CONN-D15-1",
    "connection_type": "bolted",
    "detail_reference": "D15",
    "grid_reference": "A-B/2",
    "review_status": "extracted",
    "connected_member_marks": ["B8", "C1"],
    "position": None,  # never present in the real extraction schema
    "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
    "bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}],  # no diameter_mm, no spacing
    "welds": [],
    "source_page": 15,
    "source_drawing_id": "ARKLES-DWG-D15",
}

# A clearly-labelled, NOT as-extracted record: the same connection as
# above, with the fields the real extraction schema doesn't capture
# supplemented (e.g. by a human reviewer confirming position and
# dimensions from the physical drawing). This is what proves the
# adapter's success path is real, not merely untested.
COMPLETE_REVIEWED_RECORD = {
    "connection_id": "CONN-D15-1-REVIEWED",
    "connection_type": "bolted",
    "detail_reference": "D15",
    "grid_reference": "A-B/2",
    "review_status": "approved",
    "connected_member_marks": ["B8", "C1"],
    "position": "END",
    "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
    "bolts": [{
        "quantity": 4, "size": "M20", "grade": "8.8",
        "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0,
    }],
    "welds": [],
    "source_page": 15,
    "source_drawing_id": "ARKLES-DWG-D15",
}


def make_record(**overrides) -> dict:
    record = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
              for k, v in COMPLETE_REVIEWED_RECORD.items()}
    record["plates"] = [dict(p) for p in COMPLETE_REVIEWED_RECORD["plates"]]
    record["bolts"] = [dict(b) for b in COMPLETE_REVIEWED_RECORD["bolts"]]
    record.update(overrides)
    return record


# === Primary test: the connection as Claude's vision extraction actually
# reports it today is correctly REJECTED — the honest, valuable finding
# this milestone is designed to surface. ===
def test_primary_real_schema_connection_is_rejected():
    with pytest.raises(GeometryValidationError, match="position"):
        real_connection_to_validated_connection(PRIMARY_REAL_SCHEMA_RECORD)


# === Proves the rejection is specifically about the missing fields, not
# something else — plate/member/type data in the real record is fine. ===
def test_primary_real_schema_connection_plate_and_member_data_are_otherwise_valid():
    # Remove only the fields we already know are absent from the real
    # schema, to confirm the record fails on THOSE specifically, not on
    # some other unrelated problem — supply a position but still no hole
    # diameter/spacing.
    record = dict(PRIMARY_REAL_SCHEMA_RECORD)
    record["position"] = "END"
    with pytest.raises(GeometryValidationError, match="diameter_mm"):
        real_connection_to_validated_connection(record)


# === Success path: a genuinely complete (reviewed/supplemented) record ===
def test_complete_reviewed_connection_becomes_validated_connection():
    result = real_connection_to_validated_connection(COMPLETE_REVIEWED_RECORD)

    assert isinstance(result, ValidatedConnection)
    assert result.connection_id == "CONN-D15-1-REVIEWED"
    assert result.connection_type == "END_PLATE"
    assert result.position == "END"
    assert result.connected_members == ["B8", "C1"]
    assert result.plates == [{"width": 180.0, "height": 250.0, "thickness": 12.0}]
    assert result.bolts == [{
        "diameter": 22.0, "quantity": 4, "vertical_spacing": 140.0, "horizontal_spacing": 90.0,
    }]
    assert result.physical_bolts is None  # never fabricated from "size"/"grade"
    assert result.welds == []
    assert result.validation_status == "extracted"
    assert result.source_refs == [{"page": 15, "drawing_id": "ARKLES-DWG-D15"}]


# === Hole vs bolt distinction (Step 10): "M20"/"8.8" never leak into
# numeric CAD fields, even on an otherwise-complete record ===
def test_nominal_bolt_size_and_grade_never_become_cad_dimensions():
    result = real_connection_to_validated_connection(COMPLETE_REVIEWED_RECORD)

    assert result.bolts[0]["diameter"] == 22.0  # from diameter_mm, not parsed from "M20"
    assert "size" not in result.bolts[0]
    assert "grade" not in result.bolts[0]
    assert result.physical_bolts is None


# === Missing connection ID ===
def test_missing_connection_id_is_rejected():
    record = make_record(connection_id=None)
    with pytest.raises(GeometryValidationError, match="connection_id"):
        real_connection_to_validated_connection(record)


def test_blank_connection_id_is_rejected():
    record = make_record(connection_id="   ")
    with pytest.raises(GeometryValidationError, match="connection_id"):
        real_connection_to_validated_connection(record)


# === Missing member association ===
def test_missing_member_association_is_rejected():
    record = make_record(connected_member_marks=None)
    with pytest.raises(GeometryValidationError, match="member association"):
        real_connection_to_validated_connection(record)


def test_empty_member_association_is_rejected():
    record = make_record(connected_member_marks=[])
    with pytest.raises(GeometryValidationError, match="member association"):
        real_connection_to_validated_connection(record)


# === Ambiguous member reference ===
def test_ambiguous_member_association_blank_entry_is_rejected():
    record = make_record(connected_member_marks=["B8", ""])
    with pytest.raises(GeometryValidationError, match="ambiguous"):
        real_connection_to_validated_connection(record)


def test_ambiguous_member_association_duplicate_entry_is_rejected():
    record = make_record(connected_member_marks=["B8", "B8"])
    with pytest.raises(GeometryValidationError, match="ambiguous"):
        real_connection_to_validated_connection(record)


# === Missing / unsupported connection type ===
def test_missing_plate_data_means_no_connection_type_can_be_established():
    record = make_record(plates=[])
    with pytest.raises(GeometryValidationError, match="no plate information"):
        real_connection_to_validated_connection(record)


def test_unsupported_connection_type_is_rejected():
    record = make_record(plates=[{"type": "gusset", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}])
    with pytest.raises(GeometryValidationError, match="does not correspond to a supported connection type"):
        real_connection_to_validated_connection(record)


def test_base_plate_type_is_rejected_not_treated_as_end_plate():
    record = make_record(plates=[{"type": "base_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}])
    with pytest.raises(GeometryValidationError, match="does not correspond to a supported connection type"):
        real_connection_to_validated_connection(record)


# === Missing position ===
def test_missing_position_is_rejected():
    record = make_record(position=None)
    with pytest.raises(GeometryValidationError, match="position"):
        real_connection_to_validated_connection(record)


def test_invalid_position_value_is_rejected():
    record = make_record(position="MIDDLE")
    with pytest.raises(GeometryValidationError, match="position"):
        real_connection_to_validated_connection(record)


# === Missing required plate dimension ===
def test_missing_plate_width_is_rejected():
    record = make_record()
    record["plates"][0]["width_mm"] = None
    with pytest.raises(GeometryValidationError, match="width_mm"):
        real_connection_to_validated_connection(record)


def test_missing_plate_thickness_is_rejected():
    record = make_record()
    record["plates"][0]["thickness_mm"] = None
    with pytest.raises(GeometryValidationError, match="thickness_mm"):
        real_connection_to_validated_connection(record)


def test_zero_plate_thickness_is_rejected_not_treated_as_valid():
    # app/pipeline.py's own `p.get("thickness_mm") or 0` means a real stored
    # row can already have this false zero — it must never be read as a
    # genuine zero-thickness plate.
    record = make_record()
    record["plates"][0]["thickness_mm"] = 0
    with pytest.raises(GeometryValidationError, match="thickness_mm"):
        real_connection_to_validated_connection(record)


# === Missing required hole/bolt information ===
def test_missing_hole_diameter_is_rejected():
    record = make_record()
    del record["bolts"][0]["diameter_mm"]
    with pytest.raises(GeometryValidationError, match="diameter_mm"):
        real_connection_to_validated_connection(record)


def test_missing_vertical_spacing_is_rejected():
    record = make_record()
    del record["bolts"][0]["vertical_spacing_mm"]
    with pytest.raises(GeometryValidationError, match="vertical_spacing_mm"):
        real_connection_to_validated_connection(record)


def test_missing_horizontal_spacing_for_four_hole_pattern_is_rejected():
    record = make_record()
    del record["bolts"][0]["horizontal_spacing_mm"]
    with pytest.raises(GeometryValidationError, match="horizontal_spacing_mm"):
        real_connection_to_validated_connection(record)


def test_no_bolt_data_at_all_is_rejected():
    record = make_record(bolts=[])
    with pytest.raises(GeometryValidationError, match="no hole/bolt information"):
        real_connection_to_validated_connection(record)


def test_unsupported_hole_quantity_is_rejected():
    record = make_record()
    record["bolts"][0]["quantity"] = 3  # not in SUPPORTED_HOLE_QUANTITIES (2, 4)
    record["bolts"][0]["horizontal_spacing_mm"] = 90.0  # avoid tripping the 4-hole-specific check first
    with pytest.raises(GeometryValidationError, match="not a supported pattern"):
        real_connection_to_validated_connection(record)


# === A 2-hole pattern does not require horizontal_spacing ===
def test_two_hole_pattern_does_not_require_horizontal_spacing():
    record = make_record(bolts=[{
        "quantity": 2, "size": "M16", "grade": "8.8",
        "diameter_mm": 18.0, "vertical_spacing_mm": 150.0,
    }])
    result = real_connection_to_validated_connection(record)
    assert result.bolts == [{"diameter": 18.0, "quantity": 2, "vertical_spacing": 150.0}]
    assert "horizontal_spacing" not in result.bolts[0]


# === Multiple connected members is legitimate, not ambiguous ===
def test_multiple_distinct_connected_members_is_not_ambiguous():
    record = make_record(connected_member_marks=["B8", "C1", "C2"])
    result = real_connection_to_validated_connection(record)
    assert result.connected_members == ["B8", "C1", "C2"]


# ===================================================================
# Milestone 7E — reviewed real connection -> 7D adapter ->
# ValidatedConnection -> existing generate_geometry() -> actual
# CadQuery connection geometry. The connection fixture below is a
# HUMAN-REVIEWED / SUPPLEMENTED record, derived from 7D's
# COMPLETE_REVIEWED_RECORD (same plate/hole values), with only
# `connection_id` and `connected_member_marks` adjusted to reference
# the real UB member this milestone attaches it to. It is NOT an
# untouched AI extraction — see this file's own module docstring and
# 7D's final report for why the current extraction schema alone can
# never supply position or numeric hole geometry.
# ===================================================================

def make_7e_connection_record(**overrides) -> dict:
    record = {
        "connection_id": "CONN-REAL-UB-CAD-001-END",
        "connection_type": "bolted",
        "detail_reference": "D15",
        "grid_reference": "A-B/2",
        "review_status": "approved",
        "connected_member_marks": ["REAL-UB-CAD-001"],
        "position": "END",
        "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
        "bolts": [{
            "quantity": 4, "size": "M20", "grade": "8.8",
            "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0,
        }],
        "welds": [],
        "source_page": 15,
        "source_drawing_id": "ARKLES-DWG-D15",
    }
    record["plates"] = [dict(p) for p in record["plates"]]
    record["bolts"] = [dict(b) for b in record["bolts"]]
    record.update(overrides)
    return record


def _build_primary_geometry(member_row=None, connection_record=None):
    """
    The full 7E chain in one place: real member row -> 7A adapter,
    reviewed connection record -> 7D adapter, connection_refs wired
    afterward (never hand-built) -> existing generate_geometry().
    """
    member_row = member_row or make_primary_7b_row()
    connection_record = connection_record or make_7e_connection_record()

    validated_member = real_member_to_validated_member(member_row, make_matcher())
    validated_connection = real_connection_to_validated_connection(connection_record)
    # The 7A adapter never adds connections (explicitly out of its scope,
    # same as tests/test_real_member_adapter.py's own precedent) — wiring
    # this association is not hand-building either validated object.
    validated_member.connection_refs = [validated_connection.connection_id]

    geometry = generate_geometry(validated_member, connections=[validated_connection])
    return geometry, validated_member, validated_connection


# === Definition-of-done 7-10: genuine CAD geometry, one solid, identity/position preserved ===
def test_reviewed_connection_reaches_genuine_cad_geometry():
    geometry, _validated_member, validated_connection = _build_primary_geometry()

    assert geometry.solid is not None
    assert len(geometry.solid.solids().vals()) == 1
    assert geometry.connection_count == 1
    assert len(geometry.connections) == 1

    generated_connection = geometry.connections[0]
    assert generated_connection.connection_id == validated_connection.connection_id
    assert generated_connection.connection_type == "END_PLATE"
    assert generated_connection.position == "END"
    assert generated_connection.plate is not None
    assert len(generated_connection.holes) == 4


# === Definition-of-done 11: actual generated plate geometry matches the reviewed source ===
def test_reviewed_connection_plate_geometry_matches_reviewed_source():
    geometry, *_ = _build_primary_geometry()
    bounds = geometry.connections[0].geometry_bounds

    assert abs(bounds.xlen - 180.0) < 1e-6  # width_mm
    assert abs(bounds.ylen - 250.0) < 1e-6  # depth_mm -> the CAD schema's plate "height"
    assert abs(bounds.zlen - 12.0) < 1e-6   # thickness_mm


# === Definition-of-done 12: actual generated hole geometry matches the reviewed source,
# measured from real topology (never from the input dictionaries) ===
def test_reviewed_connection_hole_geometry_matches_reviewed_source():
    geometry, *_ = _build_primary_geometry()
    holes = geometry.connections[0].holes
    assert len(holes) == 4

    diameters = sorted({round(h.diameter, 6) for h in holes})
    assert diameters == [22.0]

    xs = sorted({round(h.center[0], 3) for h in holes})
    ys = sorted({round(h.center[1], 3) for h in holes})
    assert len(xs) == 2 and len(ys) == 2  # a genuine 2x2 grid, not a coincidence
    assert abs((xs[1] - xs[0]) - 90.0) < 1e-6    # horizontal_spacing_mm
    assert abs((ys[1] - ys[0]) - 140.0) < 1e-6   # vertical_spacing_mm


# === Definition-of-done 13: member length stays authoritative, distinct from assembly bbox ===
def test_reviewed_connection_member_length_is_authoritative():
    geometry, *_ = _build_primary_geometry()

    assert geometry.length_mm == 4000.0
    bbox = geometry.solid.val().BoundingBox()
    assert abs(bbox.zmax - 4012.0) < 1e-6           # plate extends 12mm past the member's own end
    assert abs(bbox.zlen - 4012.0) < 1e-6
    assert bbox.zlen != geometry.length_mm           # explicit distinction, not a coincidence


# === Definition-of-done 14: position is explicitly respected (END, then START) ===
def test_reviewed_connection_position_end_places_plate_at_far_end():
    geometry, *_ = _build_primary_geometry()
    bounds = geometry.connections[0].geometry_bounds

    assert abs(bounds.zmin - 4000.0) < 1e-6  # touches the member's own END face
    assert abs(bounds.zmax - 4012.0) < 1e-6  # extends beyond it


def test_reviewed_connection_position_start_places_plate_at_start_end():
    connection_record = make_7e_connection_record(connection_id="CONN-REAL-UB-CAD-001-START", position="START")
    geometry, *_ = _build_primary_geometry(connection_record=connection_record)
    bounds = geometry.connections[0].geometry_bounds

    assert abs(bounds.zmax - 0.0) < 1e-6      # touches the member's own START face
    assert abs(bounds.zmin - (-12.0)) < 1e-6  # extends backward beyond it


# === Definition-of-done 15: parameter sensitivity ===
def test_plate_thickness_change_reflected_in_generated_geometry():
    baseline_geometry, *_ = _build_primary_geometry()

    modified_record = make_7e_connection_record(connection_id="CONN-THICK-15")
    modified_record["plates"][0]["thickness_mm"] = 15.0
    modified_geometry, *_ = _build_primary_geometry(connection_record=modified_record)

    assert abs(baseline_geometry.connections[0].geometry_bounds.zlen - 12.0) < 1e-6
    assert abs(modified_geometry.connections[0].geometry_bounds.zlen - 15.0) < 1e-6


def test_hole_diameter_change_reflected_in_generated_geometry():
    modified_record = make_7e_connection_record(connection_id="CONN-DIA-20")
    modified_record["bolts"][0]["diameter_mm"] = 20.0
    geometry, *_ = _build_primary_geometry(connection_record=modified_record)

    diameters = {round(h.diameter, 6) for h in geometry.connections[0].holes}
    assert diameters == {20.0}


def test_hole_spacing_change_reflected_in_generated_hole_centres():
    # The authoritative hole-position data in the 7D contract IS
    # vertical_spacing_mm/horizontal_spacing_mm (there is no separate raw
    # XY coordinate list in this schema) — changing these, not a merely
    # descriptive field, is what Step 10 requires.
    modified_record = make_7e_connection_record(connection_id="CONN-SPACING")
    modified_record["bolts"][0]["vertical_spacing_mm"] = 160.0
    modified_record["bolts"][0]["horizontal_spacing_mm"] = 100.0
    geometry, *_ = _build_primary_geometry(connection_record=modified_record)

    holes = geometry.connections[0].holes
    xs = sorted({round(h.center[0], 3) for h in holes})
    ys = sorted({round(h.center[1], 3) for h in holes})
    assert abs((xs[1] - xs[0]) - 100.0) < 1e-6
    assert abs((ys[1] - ys[0]) - 160.0) < 1e-6
    assert abs((xs[1] - xs[0]) - 90.0) > 1.0    # old spacing must not leak through
    assert abs((ys[1] - ys[0]) - 140.0) > 1.0


def test_member_length_change_reflected_geometry_cross_section_unchanged():
    member_row = make_primary_7b_row(mark="REAL-UB-CAD-001", length_mm=2750)
    connection_record = make_7e_connection_record(connection_id="CONN-LEN-2750")
    geometry, *_ = _build_primary_geometry(member_row=member_row, connection_record=connection_record)

    assert geometry.length_mm == 2750.0

    # The member's own cross-section, read from its START face — untouched by
    # the END-position plate. NOT the overall solid bounding box: this
    # reviewed plate (180mm wide) is actually wider than the UB's own
    # flange_width (165mm), so the assembly bbox is governed by the plate,
    # not the member section — checking the START face is what genuinely
    # isolates "section geometry unchanged" from "plate geometry unchanged".
    start_face = geometry.solid.faces("<Z").val()
    xs = [v.X for v in start_face.outerWire().Vertices()]
    ys = [v.Y for v in start_face.outerWire().Vertices()]
    assert abs((max(xs) - min(xs)) - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs((max(ys) - min(ys)) - FULL_UB_SECTION["depth"]) < 1e-6

    assert geometry.connection_count == 1  # connection remains attached

    bounds = geometry.connections[0].geometry_bounds
    assert abs(bounds.zmin - 2750.0) < 1e-6  # plate now attaches at the member's new end


# === Definition-of-done: source independence — CAD reads only the
# ValidatedConnection object, never the original raw dict ===
def test_generated_geometry_is_independent_of_the_original_raw_record():
    connection_record = make_7e_connection_record(connection_id="CONN-INDEPENDENCE")
    validated_connection = real_connection_to_validated_connection(connection_record)
    connection_record.clear()  # destroy the original raw dict after conversion

    member_row = make_primary_7b_row()
    validated_member = real_member_to_validated_member(member_row, make_matcher())
    validated_member.connection_refs = [validated_connection.connection_id]

    geometry = generate_geometry(validated_member, connections=[validated_connection])
    assert geometry.connection_count == 1
    assert len(geometry.connections[0].holes) == 4


# === Definition-of-done 16: 7D rejections still happen before any CAD call ===
def test_missing_position_never_reaches_cad():
    connection_record = make_7e_connection_record(connection_id="CONN-NOPOS", position=None)
    with pytest.raises(GeometryValidationError, match="position"):
        real_connection_to_validated_connection(connection_record)


def test_missing_plate_dimension_never_reaches_cad():
    connection_record = make_7e_connection_record(connection_id="CONN-NOPLATE")
    connection_record["plates"][0]["width_mm"] = None
    with pytest.raises(GeometryValidationError, match="width_mm"):
        real_connection_to_validated_connection(connection_record)


def test_missing_hole_geometry_never_reaches_cad():
    connection_record = make_7e_connection_record(connection_id="CONN-NOHOLE")
    del connection_record["bolts"][0]["diameter_mm"]
    with pytest.raises(GeometryValidationError, match="diameter_mm"):
        real_connection_to_validated_connection(connection_record)


def test_unsupported_connection_type_never_reaches_cad():
    connection_record = make_7e_connection_record(connection_id="CONN-BADTYPE")
    connection_record["plates"][0]["type"] = "gusset"
    with pytest.raises(GeometryValidationError, match="does not correspond to a supported connection type"):
        real_connection_to_validated_connection(connection_record)


def test_missing_member_association_never_reaches_cad():
    connection_record = make_7e_connection_record(connection_id="CONN-NOMEMBER", connected_member_marks=[])
    with pytest.raises(GeometryValidationError, match="member association"):
        real_connection_to_validated_connection(connection_record)


# === 7V correction pass: non-finite / non-integral numbers are rejected AT THE ADAPTER ===
# The original `value <= 0` checks accepted NaN and +infinity (every NaN
# comparison is False), and int(quantity) crashed on NaN/infinity or silently
# truncated 4.5 to 4. These tests pin the fix where the rule lives.
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field_name", ["width_mm", "depth_mm", "thickness_mm"])
def test_non_finite_plate_dimension_is_rejected_by_the_adapter(field_name, bad_value):
    record = make_7e_connection_record()
    record["plates"][0][field_name] = bad_value
    with pytest.raises(GeometryValidationError, match=field_name):
        real_connection_to_validated_connection(record)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field_name", ["diameter_mm", "vertical_spacing_mm", "horizontal_spacing_mm"])
def test_non_finite_hole_dimension_is_rejected_by_the_adapter(field_name, bad_value):
    record = make_7e_connection_record()
    record["bolts"][0][field_name] = bad_value
    with pytest.raises(GeometryValidationError, match=field_name):
        real_connection_to_validated_connection(record)


@pytest.mark.parametrize("bad_quantity", [float("nan"), float("inf"), float("-inf"), 4.5, 3.9])
def test_non_finite_or_non_integral_hole_quantity_raises_the_adapters_own_error(bad_quantity):
    record = make_7e_connection_record()
    record["bolts"][0]["quantity"] = bad_quantity
    with pytest.raises(GeometryValidationError, match="quantity"):  # never ValueError/OverflowError, never truncated
        real_connection_to_validated_connection(record)


def test_integral_float_quantity_and_ordinary_finite_values_are_still_accepted():
    record = make_7e_connection_record()
    record["bolts"][0]["quantity"] = 4.0
    connection = real_connection_to_validated_connection(record)
    assert connection.bolts[0]["quantity"] == 4
    assert connection.plates[0]["width"] == 180.0


# === Oversized Python integers (final 7V correction): rejected, never an OverflowError ===
_OVERSIZED_INTS = [pytest.param(10**400, id="pos_1e400"), pytest.param(-(10**400), id="neg_1e400"), pytest.param(10**309, id="pos_1e309"), pytest.param(10**5000, id="pos_1e5000")]  # 10**309 is just beyond float range


@pytest.mark.parametrize("huge", _OVERSIZED_INTS)
@pytest.mark.parametrize("field_name", ["width_mm", "depth_mm", "thickness_mm"])
def test_oversized_integer_plate_dimension_is_rejected_by_the_adapter(field_name, huge):
    record = make_7e_connection_record()
    record["plates"][0][field_name] = huge
    with pytest.raises(GeometryValidationError, match=field_name):  # not OverflowError
        real_connection_to_validated_connection(record)


@pytest.mark.parametrize("huge", _OVERSIZED_INTS)
@pytest.mark.parametrize("field_name", ["quantity", "diameter_mm", "vertical_spacing_mm", "horizontal_spacing_mm"])
def test_oversized_integer_hole_value_is_rejected_by_the_adapter(field_name, huge):
    record = make_7e_connection_record()
    record["bolts"][0][field_name] = huge
    with pytest.raises(GeometryValidationError, match=field_name):
        real_connection_to_validated_connection(record)


def test_finite_but_large_values_are_not_capped_by_the_adapter():
    """No CAD maximum exists as a project rule, so none is invented: a finite value is kept exactly."""
    record = make_7e_connection_record()
    record["plates"][0]["width_mm"] = 10**300
    assert real_connection_to_validated_connection(record).plates[0]["width"] == float(10**300)


def test_finite_but_absurd_hole_quantity_is_rejected_as_unsupported_not_as_overflow():
    record = make_7e_connection_record()
    record["bolts"][0]["quantity"] = 10**300  # representable, integral, positive — but not a supported pattern
    with pytest.raises(GeometryValidationError, match="not a supported pattern"):
        real_connection_to_validated_connection(record)
