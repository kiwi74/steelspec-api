"""
Unit tests for the CAD engine: milestone 1 (a single straight,
unconnected member with a matched section and known length) and
milestone 2 (an optional END_PLATE connection with a 2-hole bolt
pattern, joined to the member's far end). Uses small synthetic
section/member/connection fixtures, not the real Arkles Strand
dataset — that dataset has no reliable member lengths (see
test_arkles_strand.py), so it cannot exercise this code path, and
inventing a length for it would violate this project's core
validation rule (never guess a missing dimension).
"""
import math

import pytest

from app.cad_engine import connections as connection_geometry
from app.cad_engine.errors import GeometryValidationError, UnsupportedSectionFamilyError
from app.cad_engine.interface import (
    GeneratedConnection, GeneratedHole, GeneratedMemberGeometry, ValidatedConnection, ValidatedSteelMember,
    generate_geometry,
)

# A small, controlled 250PFC fixture — explicit, round test values,
# not pulled from the real steel_sections table.
SECTION_250PFC = {
    "name": "250PFC", "family": "PFC",
    "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 15.0, "web_thickness": 8.0,
    "weight_per_metre": 35.5,
}

# A small, controlled 310UB40 fixture. IMPORTANT: this session has no
# credentials for the live Supabase steel_sections table (confirmed
# repeatedly earlier in this project's history), so these values are
# NOT verified against production data and must not be read as real
# AS/NZS 310UB40 dimensions — they are deliberately round numbers, in
# the same spirit as SECTION_250PFC above. The only thing confirmed
# from the accessible codebase is that a "310UB40.4" name with only a
# weight_per_metre (no geometry columns) appears in an unrelated
# validation-only test fixture (tests/test_validation_rules.py) — not
# usable as a geometry source either.
SECTION_310UB40 = {
    "name": "310UB40", "family": "UB",
    "depth": 310.0, "flange_width": 165.0,
    "flange_thickness": 12.0, "web_thickness": 8.0,
    "weight_per_metre": 40.4,
}


def make_ub_member(**overrides) -> ValidatedSteelMember:
    defaults = dict(
        mark="UB-TEST-1",
        section="310UB40",
        length_mm=4000.0,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_310UB40,
    )
    defaults.update(overrides)
    return ValidatedSteelMember(**defaults)


# A small, controlled 150x150x6 SHS fixture. Same caveat as
# SECTION_310UB40 above: this session has no live Supabase access, so
# these are deliberately round, explicitly NOT verified production
# dimensions — a geometry test fixture only, not a catalogue section.
SECTION_150X150X6_SHS = {
    "name": "150x150x6 SHS", "family": "SHS",
    "width": 150.0, "thickness": 6.0,
    "weight_per_metre": 26.0,
}


def make_shs_member(**overrides) -> ValidatedSteelMember:
    defaults = dict(
        mark="SHS-TEST-1",
        section="150x150x6 SHS",
        length_mm=3000.0,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_150X150X6_SHS,
    )
    defaults.update(overrides)
    return ValidatedSteelMember(**defaults)


# A small, controlled 200x100x6 RHS fixture. Same caveat as
# SECTION_150X150X6_SHS above: this session has no live Supabase
# access, so these are deliberately round, explicitly NOT verified
# production dimensions — a geometry test fixture only, not a
# catalogue section. width != depth on purpose, to prove the shared
# hollow-section builder genuinely handles unequal outer dimensions
# and isn't secretly routing through the SHS (square-only) path.
SECTION_200X100X6_RHS = {
    "name": "200x100x6 RHS", "family": "RHS",
    "width": 200.0, "depth": 100.0, "thickness": 6.0,
    "weight_per_metre": 26.0,
}


def make_rhs_member(**overrides) -> ValidatedSteelMember:
    defaults = dict(
        mark="RHS-TEST-1",
        section="200x100x6 RHS",
        length_mm=3000.0,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_200X100X6_RHS,
    )
    defaults.update(overrides)
    return ValidatedSteelMember(**defaults)


def _bottom_profile_vertices(result_solid) -> list[tuple[float, float]]:
    """Real vertices read off the generated solid's own bottom face — not a redrawn copy."""
    bottom_face = result_solid.faces("<Z").val()
    return [(round(v.X, 6), round(v.Y, 6)) for v in bottom_face.outerWire().Vertices()]


def make_member(**overrides) -> ValidatedSteelMember:
    defaults = dict(
        mark="P-TEST-1",
        section="250PFC",
        length_mm=3000.0,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SECTION_250PFC,
    )
    defaults.update(overrides)
    return ValidatedSteelMember(**defaults)


# A small, controlled END_PLATE connection fixture — explicit test
# values matching the task's synthetic spec exactly, not pulled from
# any real engineering drawing.
PLATE_SPEC = {"width": 90.0, "height": 250.0, "thickness": 10.0}
HOLES_SPEC = {"diameter": 18.0, "quantity": 2, "vertical_spacing": 150.0}


def make_connection(**overrides) -> ValidatedConnection:
    defaults = dict(
        connection_id="conn-1",
        connected_members=["P-TEST-1"],
        plates=[dict(PLATE_SPEC)],
        bolts=[dict(HOLES_SPEC)],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
    )
    defaults.update(overrides)
    return ValidatedConnection(**defaults)


def _cylindrical_faces(solid_workplane):
    return [f for f in solid_workplane.val().Faces() if f.geomType() == "CYLINDER"]


def _face_hole_diameter(face) -> float:
    """Derives a cylindrical face's diameter from its circular edges' circumference."""
    circle_edges = [e for e in face.Edges() if e.geomType() == "CIRCLE"]
    assert circle_edges, "expected at least one circular edge on a cylindrical hole face"
    radius = circle_edges[0].Length() / (2 * math.pi)
    return radius * 2


# === 1. Valid 250PFC with a known length produces geometry ===
def test_valid_250pfc_produces_geometry():
    result = generate_geometry(make_member())

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.mark == "P-TEST-1"
    assert result.section_name == "250PFC"
    assert result.section_family == "PFC"
    assert result.solid is not None


# === 2. Geometry has the requested member length ===
def test_geometry_has_requested_length():
    result = generate_geometry(make_member(length_mm=3000.0))

    bbox = result.solid.val().BoundingBox()
    assert result.length_mm == 3000.0
    # Extrusion runs along Z; allow a tiny float tolerance.
    assert abs(bbox.zlen - 3000.0) < 1e-6


# === 3. Geometry uses the supplied section dimensions, not hardcoded ones ===
def test_geometry_uses_supplied_dimensions_not_hardcoded():
    small = generate_geometry(make_member(
        section_properties={**SECTION_250PFC, "depth": 150.0, "flange_width": 75.0},
    ))
    large = generate_geometry(make_member(
        section_properties={**SECTION_250PFC, "depth": 400.0, "flange_width": 120.0},
    ))

    small_bbox = small.solid.val().BoundingBox()
    large_bbox = large.solid.val().BoundingBox()

    # depth -> Y extent, flange_width -> X extent of the profile
    assert abs(small_bbox.ylen - 150.0) < 1e-6
    assert abs(small_bbox.xlen - 75.0) < 1e-6
    assert abs(large_bbox.ylen - 400.0) < 1e-6
    assert abs(large_bbox.xlen - 120.0) < 1e-6
    assert large_bbox.ylen > small_bbox.ylen
    assert large_bbox.xlen > small_bbox.xlen


# === 4. Missing length raises a clear validation error ===
def test_missing_length_raises():
    with pytest.raises(GeometryValidationError, match="length_mm"):
        generate_geometry(make_member(length_mm=None))


# === 5. Missing/unmatched section raises a clear validation error ===
def test_unmatched_section_raises():
    with pytest.raises(GeometryValidationError, match="matched section reference data"):
        generate_geometry(make_member(section_properties=None))


# === 6. Review-required/conflicting member cannot generate geometry ===
def test_review_required_member_cannot_generate_geometry():
    with pytest.raises(GeometryValidationError, match="review_required"):
        generate_geometry(make_member(validation_status="review_required"))


# === 7. Unsupported section family fails clearly rather than guessing ===
# NOTE: this used "UB" as the unsupported-family example until milestone 3
# added real UB support (see the UB Test 11 / CHS-based equivalent further
# down) — switched to "UC" here since that family still has no builder.
def test_unsupported_section_family_fails_clearly():
    uc_section = {"name": "200UC46.2", "family": "UC", "depth": 203.0, "weight_per_metre": 46.2}
    with pytest.raises(UnsupportedSectionFamilyError, match="UC"):
        generate_geometry(make_member(section="200UC46.2", section_properties=uc_section))


# === Extra: connections attached also blocks generation (no connection geometry yet) ===
def test_member_with_connections_cannot_generate_geometry():
    with pytest.raises(GeometryValidationError, match="connection"):
        generate_geometry(make_member(connection_refs=["conn-1"]))


# === Extra: a matched PFC row missing one of the required geometry columns fails clearly ===
def test_pfc_section_missing_geometry_field_raises():
    incomplete = {"name": "250PFC", "family": "PFC", "depth": 250.0, "weight_per_metre": 35.5}
    with pytest.raises(GeometryValidationError, match="missing required PFC geometry field"):
        generate_geometry(make_member(section_properties=incomplete))


# ===================================================================
# Milestone 2: END_PLATE connection (plate + 2 bolt holes, no bolts)
# ===================================================================

# === Connection Test 1: PFC without a connection still works (unchanged behaviour) ===
def test_pfc_without_connection_still_works():
    result = generate_geometry(make_member())

    assert result.connection_count == 0
    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.zlen - 3000.0) < 1e-6  # no plate added, length unchanged


# === Connection Test 2: end plate is physically present, joined at the member's far end ===
def test_end_plate_is_physically_present():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    assert result.connection_count == 1
    # Exactly one solid: plate is unioned (joined), not a separate loose body.
    assert len(result.solid.solids().vals()) == 1

    bbox = result.solid.val().BoundingBox()
    # Extrusion runs 0 -> length_mm along Z (verified against sections.py's
    # profile construction); the plate is attached at the far end, so the
    # combined solid's Z-extent is length + plate thickness, not length.
    assert abs(bbox.zlen - (3000.0 + 10.0)) < 1e-6
    assert abs(bbox.zmax - 3010.0) < 1e-6


# === Connection Test 3: exactly two hole cuts exist ===
def test_exactly_two_holes_exist():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    cyl_faces = _cylindrical_faces(result.solid)
    assert len(cyl_faces) == 2


# === Connection Test 4: hole diameter is correct (18mm) for both holes ===
def test_hole_diameter_is_correct():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    for face in _cylindrical_faces(result.solid):
        assert abs(_face_hole_diameter(face) - 18.0) < 1e-3


# === Connection Test 5: hole spacing is correct (150mm vertical, same horizontal centreline) ===
def test_hole_spacing_is_correct():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    centers = sorted(f.Center().toTuple() for f in _cylindrical_faces(result.solid))
    (x1, y1, _z1), (x2, y2, _z2) = centers

    assert abs(x1 - x2) < 1e-6  # same horizontal centreline
    assert abs((y2 - y1) - 150.0) < 1e-3  # vertical spacing


# === Connection Test 6: plate dimensions are correct (90 x 250 x 10) ===
def test_plate_dimensions_are_correct():
    plate_solid = connection_geometry.build_end_plate_with_holes(PLATE_SPEC, HOLES_SPEC, "P-TEST-1")
    from cadquery import Workplane
    bbox = Workplane(obj=plate_solid).val().BoundingBox()

    assert abs(bbox.xlen - 90.0) < 1e-6
    assert abs(bbox.ylen - 250.0) < 1e-6
    assert abs(bbox.zlen - 10.0) < 1e-6


# === Connection Test 7: no physical bolts are modelled ===
def test_no_physical_bolts_modelled():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    # A modelled bolt solid would add extra bodies and/or extra
    # cylindrical faces beyond the two hole surfaces.
    assert len(result.solid.solids().vals()) == 1
    assert len(_cylindrical_faces(result.solid)) == 2


# === Connection Test 8: invalid connection data fails safely, never silently ===
def test_negative_plate_thickness_raises():
    connection = make_connection(plates=[{**PLATE_SPEC, "thickness": -10.0}])
    with pytest.raises(GeometryValidationError, match="positive"):
        generate_geometry(make_member(connection_refs=["conn-1"]), connections=[connection])


def test_zero_hole_diameter_raises():
    connection = make_connection(bolts=[{**HOLES_SPEC, "diameter": 0}])
    with pytest.raises(GeometryValidationError, match="positive"):
        generate_geometry(make_member(connection_refs=["conn-1"]), connections=[connection])


def test_zero_hole_count_raises():
    connection = make_connection(bolts=[{**HOLES_SPEC, "quantity": 0}])
    with pytest.raises(GeometryValidationError, match="positive"):
        generate_geometry(make_member(connection_refs=["conn-1"]), connections=[connection])


def test_hole_diameter_larger_than_plate_raises():
    connection = make_connection(bolts=[{**HOLES_SPEC, "diameter": 500.0}])
    with pytest.raises(GeometryValidationError, match="does not fit"):
        generate_geometry(make_member(connection_refs=["conn-1"]), connections=[connection])


def test_missing_plate_dimensions_raises():
    connection = make_connection(plates=[{"width": 90.0}])  # height, thickness missing
    with pytest.raises(GeometryValidationError, match="missing required field"):
        generate_geometry(make_member(connection_refs=["conn-1"]), connections=[connection])


def test_unresolved_connection_ref_raises():
    with pytest.raises(GeometryValidationError, match="no matching ValidatedConnection"):
        generate_geometry(make_member(connection_refs=["conn-does-not-exist"]), connections=[make_connection()])


def test_unsupported_connection_type_raises():
    connection = make_connection(connection_type="WELDED_CLEAT")
    with pytest.raises(GeometryValidationError, match="no supported geometry builder"):
        generate_geometry(make_member(connection_refs=["conn-1"]), connections=[connection])


# ===================================================================
# Milestone 3: UB (Universal Beam) section geometry
# ===================================================================

# === UB Test 1: valid UB generates geometry ===
def test_valid_ub_generates_geometry():
    result = generate_geometry(make_ub_member())

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.mark == "UB-TEST-1"
    assert result.section_name == "310UB40"
    assert result.section_family == "UB"
    assert result.solid is not None


# === UB Test 2: overall length is correct (same axis as PFC) ===
def test_ub_overall_length_is_correct():
    result = generate_geometry(make_ub_member(length_mm=4000.0))

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.zlen - 4000.0) < 1e-6  # same Z-extrusion axis as the PFC builder


# === UB Test 3: depth is correct ===
def test_ub_depth_is_correct():
    result = generate_geometry(make_ub_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.ylen - SECTION_310UB40["depth"]) < 1e-6


# === UB Test 4: flange width is correct ===
def test_ub_flange_width_is_correct():
    result = generate_geometry(make_ub_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - SECTION_310UB40["flange_width"]) < 1e-6


# === UB Test 5: flange thickness is correct (from actual cross-section geometry) ===
def test_ub_flange_thickness_is_correct():
    result = generate_geometry(make_ub_member())
    points = _bottom_profile_vertices(result.solid)

    ys = sorted({y for _x, y in points})
    # 4 distinct Y-levels for an I-beam: 0, flange_thickness, depth-flange_thickness, depth.
    assert len(ys) == 4
    assert abs(ys[1] - SECTION_310UB40["flange_thickness"]) < 1e-6
    assert abs((SECTION_310UB40["depth"] - ys[2]) - SECTION_310UB40["flange_thickness"]) < 1e-6


# === UB Test 6: web thickness is correct (from actual cross-section geometry, not just bbox) ===
def test_ub_web_thickness_is_correct():
    result = generate_geometry(make_ub_member())
    points = _bottom_profile_vertices(result.solid)

    ys = sorted({y for _x, y in points})
    waist_y = ys[1]  # the flange/web boundary level
    xs_at_waist = sorted(x for x, y in points if y == waist_y)
    # 4 vertices at this level: [flange outer edge, web edge, web edge, flange outer edge].
    assert len(xs_at_waist) == 4
    web_span = xs_at_waist[2] - xs_at_waist[1]
    assert abs(web_span - SECTION_310UB40["web_thickness"]) < 1e-6


# === UB Test 7: cross-section is genuinely I/H shaped, not a rectangle ===
def test_ub_cross_section_is_i_shaped_not_rectangular():
    result = generate_geometry(make_ub_member())
    points = _bottom_profile_vertices(result.solid)

    # A rectangle has 4 vertices; a sharp-cornered I-beam has 12.
    assert len(points) == 12

    ys = sorted({y for _x, y in points})
    xs_at_top = sorted(x for x, y in points if y == ys[0])
    xs_at_waist = sorted(x for x, y in points if y == ys[1])
    # Outer flange extent is the full flange width at the outer edge...
    assert abs((xs_at_top[-1] - xs_at_top[0]) - SECTION_310UB40["flange_width"]) < 1e-6
    # ...but narrows to just the web at the waist — proof of an actual notch, not a solid rectangle.
    web_span = xs_at_waist[2] - xs_at_waist[1]
    assert web_span < SECTION_310UB40["flange_width"]


# === UB Test 8: different section properties produce different geometry ===
def test_ub_uses_supplied_dimensions_not_hardcoded():
    small = generate_geometry(make_ub_member(
        section_properties={**SECTION_310UB40, "depth": 200.0, "flange_width": 100.0},
    ))
    large = generate_geometry(make_ub_member(
        section_properties={**SECTION_310UB40, "depth": 450.0, "flange_width": 190.0},
    ))

    small_bbox = small.solid.val().BoundingBox()
    large_bbox = large.solid.val().BoundingBox()

    assert abs(small_bbox.ylen - 200.0) < 1e-6
    assert abs(small_bbox.xlen - 100.0) < 1e-6
    assert abs(large_bbox.ylen - 450.0) < 1e-6
    assert abs(large_bbox.xlen - 190.0) < 1e-6


# === UB Test 9: missing length fails ===
def test_ub_missing_length_raises():
    with pytest.raises(GeometryValidationError, match="length_mm"):
        generate_geometry(make_ub_member(length_mm=None))


# === UB Test 10: missing section properties fails safely ===
def test_ub_missing_geometry_fields_raises():
    incomplete = {"name": "310UB40", "family": "UB", "depth": 310.0, "weight_per_metre": 40.4}
    with pytest.raises(GeometryValidationError, match="missing required UB geometry field"):
        generate_geometry(make_ub_member(section_properties=incomplete))


# === UB Test 11: unsupported section family still fails (CHS not implemented) ===
def test_chs_section_family_still_unsupported():
    chs_section = {"name": "89x5CHS", "family": "CHS", "outside_diameter": 89.0, "weight_per_metre": 10.3}
    with pytest.raises(UnsupportedSectionFamilyError, match="CHS"):
        generate_geometry(make_ub_member(section="89x5CHS", section_properties=chs_section))


# === UB Test 12: existing PFC + END_PLATE connection regression ===
def test_pfc_end_plate_connection_still_works_after_adding_ub():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    assert result.connection_count == 1
    assert result.connection_types == ["END_PLATE"]
    cyl_faces = [f for f in result.solid.val().Faces() if f.geomType() == "CYLINDER"]
    assert len(cyl_faces) == 2


# ===================================================================
# Milestone 4A: SHS (square hollow section) geometry.
# SHS/RHS hollow-section geometry is implemented and verified as a
# synthetic CAD fixture — 150x150x6 SHS is NOT a verified real-world
# steel catalogue section (see SECTION_150X150X6_SHS's own comment).
# ===================================================================

def _shs_end_face_wires(result_solid):
    """Returns (outer_wire, inner_wires) from the solid's own bottom face."""
    bottom_face = result_solid.faces("<Z").val()
    return bottom_face.outerWire(), bottom_face.innerWires()


# === SHS Test 1: SHS generates ===
def test_shs_generates_geometry():
    result = generate_geometry(make_shs_member())

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.mark == "SHS-TEST-1"
    assert result.section_name == "150x150x6 SHS"
    assert result.section_family == "SHS"
    assert result.solid is not None


# === SHS Test 2: bounding box ~ 150 x 150 x 3000 ===
def test_shs_bounding_box_is_correct():
    result = generate_geometry(make_shs_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 150.0) < 1e-6
    assert abs(bbox.ylen - 150.0) < 1e-6
    assert abs(bbox.zlen - 3000.0) < 1e-6


# === SHS Test 3: exactly one solid — not four separate bars ===
def test_shs_is_a_single_solid():
    result = generate_geometry(make_shs_member())

    assert len(result.solid.solids().vals()) == 1


# === SHS Test 4: hollow profile — outer boundary AND inner boundary both exist ===
def test_shs_profile_has_outer_and_inner_boundary():
    result = generate_geometry(make_shs_member())

    outer_wire, inner_wires = _shs_end_face_wires(result.solid)
    assert len(outer_wire.Vertices()) == 4
    assert len(inner_wires) == 1
    assert len(inner_wires[0].Vertices()) == 4


# === SHS Test 5: internal opening ~ 138 x 138 mm, from actual topology ===
def test_shs_internal_opening_is_correct():
    result = generate_geometry(make_shs_member())

    _outer_wire, inner_wires = _shs_end_face_wires(result.solid)
    inner_pts = [(v.X, v.Y) for v in inner_wires[0].Vertices()]
    xs = [p[0] for p in inner_pts]
    ys = [p[1] for p in inner_pts]
    opening_width = max(xs) - min(xs)
    opening_depth = max(ys) - min(ys)

    assert abs(opening_width - 138.0) < 1e-6
    assert abs(opening_depth - 138.0) < 1e-6


# === SHS Test 6: wall thickness ~ 6mm, derived from outer-vs-inner boundary offset ===
def test_shs_wall_thickness_is_correct():
    result = generate_geometry(make_shs_member())

    outer_wire, inner_wires = _shs_end_face_wires(result.solid)
    outer_x_min = min(v.X for v in outer_wire.Vertices())
    inner_x_min = min(v.X for v in inner_wires[0].Vertices())

    wall_thickness = inner_x_min - outer_x_min
    assert abs(wall_thickness - 6.0) < 1e-6


# === SHS Test 7: volume matches (150^2 - 138^2) * 3000 exactly, not an approximation ===
def test_shs_volume_matches_expected_hollow_volume():
    result = generate_geometry(make_shs_member())

    outer_area = 150.0 * 150.0
    inner_area = 138.0 * 138.0  # 150 - 2*6 on each side
    expected_volume = (outer_area - inner_area) * 3000.0

    actual_volume = result.solid.val().Volume()
    assert abs(actual_volume - expected_volume) < 1e-3


# === SHS Test 8: PFC/UB regression after adding SHS ===
def test_pfc_and_ub_still_work_after_adding_shs():
    pfc_result = generate_geometry(make_member())
    assert pfc_result.section_family == "PFC"
    assert len(_bottom_profile_vertices(pfc_result.solid)) == 8

    ub_result = generate_geometry(make_ub_member())
    assert ub_result.section_family == "UB"
    assert len(_bottom_profile_vertices(ub_result.solid)) == 12


# === Extra: invalid SHS dimensions (wall thickness too large) fail clearly ===
def test_shs_oversized_wall_thickness_raises():
    invalid_section = {**SECTION_150X150X6_SHS, "thickness": 80.0}
    with pytest.raises(GeometryValidationError, match="geometrically inconsistent"):
        generate_geometry(make_shs_member(section_properties=invalid_section))


# === Extra: missing SHS geometry field fails clearly, not silently ===
def test_shs_missing_geometry_field_raises():
    incomplete_section = {"name": "150x150x6 SHS", "family": "SHS", "weight_per_metre": 26.0}
    with pytest.raises(GeometryValidationError, match="missing required SHS geometry field"):
        generate_geometry(make_shs_member(section_properties=incomplete_section))


# ===================================================================
# Milestone 4B: RHS (rectangular hollow section) geometry — reuses
# the same build_hollow_rectangular_profile() SHS already uses, with
# independent width/depth. RHS rectangular hollow-section geometry is
# implemented and verified as a synthetic CAD fixture using the
# shared hollow-section geometry architecture — 200x100x6 RHS is NOT
# a verified real-world steel catalogue section.
# ===================================================================

def _rhs_end_face_wires(result_solid):
    """Returns (outer_wire, inner_wires) from the solid's own bottom face."""
    bottom_face = result_solid.faces("<Z").val()
    return bottom_face.outerWire(), bottom_face.innerWires()


# === RHS Test 1: RHS generation succeeds ===
def test_rhs_generates_geometry():
    result = generate_geometry(make_rhs_member())

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.mark == "RHS-TEST-1"
    assert result.section_name == "200x100x6 RHS"
    assert result.section_family == "RHS"
    assert result.solid is not None


# === RHS Test 2: bounding box = 200 x 100 x 3000 — proves width != depth is honoured ===
def test_rhs_bounding_box_is_correct():
    result = generate_geometry(make_rhs_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 200.0) < 1e-6
    assert abs(bbox.ylen - 100.0) < 1e-6
    assert abs(bbox.zlen - 3000.0) < 1e-6
    # Explicit unequal-dimension regression: guards against RHS silently
    # being routed through the SHS (square-only) path.
    assert bbox.xlen != bbox.ylen


# === RHS Test 3: exactly one solid ===
def test_rhs_is_a_single_solid():
    result = generate_geometry(make_rhs_member())

    assert len(result.solid.solids().vals()) == 1


# === RHS Test 4 & 5: outer wire and inner wire both exist ===
def test_rhs_profile_has_outer_and_inner_boundary():
    result = generate_geometry(make_rhs_member())

    outer_wire, inner_wires = _rhs_end_face_wires(result.solid)
    assert len(outer_wire.Vertices()) == 4
    assert len(inner_wires) == 1
    assert len(inner_wires[0].Vertices()) == 4


# === RHS Test 6: outer dimensions = 200 x 100, derived from the outer wire itself ===
def test_rhs_outer_dimensions_from_geometry():
    result = generate_geometry(make_rhs_member())

    outer_wire, _inner_wires = _rhs_end_face_wires(result.solid)
    outer_pts = [(v.X, v.Y) for v in outer_wire.Vertices()]
    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]

    assert abs((max(xs) - min(xs)) - 200.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 100.0) < 1e-6


# === RHS Test 7: inner dimensions = 188 x 88, from actual topology (not inferred) ===
def test_rhs_internal_opening_is_correct():
    result = generate_geometry(make_rhs_member())

    _outer_wire, inner_wires = _rhs_end_face_wires(result.solid)
    inner_pts = [(v.X, v.Y) for v in inner_wires[0].Vertices()]
    xs = [p[0] for p in inner_pts]
    ys = [p[1] for p in inner_pts]

    assert abs((max(xs) - min(xs)) - 188.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 88.0) < 1e-6


# === RHS Test 8: derived wall thickness = 6mm, in BOTH X and Y ===
def test_rhs_wall_thickness_is_correct_in_both_axes():
    result = generate_geometry(make_rhs_member())

    outer_wire, inner_wires = _rhs_end_face_wires(result.solid)
    outer_x_min = min(v.X for v in outer_wire.Vertices())
    outer_y_min = min(v.Y for v in outer_wire.Vertices())
    inner_x_min = min(v.X for v in inner_wires[0].Vertices())
    inner_y_min = min(v.Y for v in inner_wires[0].Vertices())

    wall_x = inner_x_min - outer_x_min
    wall_y = inner_y_min - outer_y_min
    assert abs(wall_x - 6.0) < 1e-6
    assert abs(wall_y - 6.0) < 1e-6


# === RHS Test 9: volume matches (200*100 - 188*88) * 3000 exactly, and is NOT a solid bar ===
def test_rhs_volume_matches_expected_hollow_volume():
    result = generate_geometry(make_rhs_member())

    outer_area = 200.0 * 100.0
    inner_area = 188.0 * 88.0
    expected_volume = (outer_area - inner_area) * 3000.0
    # (200*100 - 188*88) * 3000 = 3456 * 3000 = 10,368,000 mm^3.
    assert expected_volume == 10_368_000.0

    actual_volume = result.solid.val().Volume()
    assert abs(actual_volume - expected_volume) < 1e-3

    solid_bar_volume = 200.0 * 100.0 * 3000.0
    assert abs(actual_volume - solid_bar_volume) > 1.0  # nowhere near a solid bar


# === RHS Test 10: zero thickness fails clearly ===
def test_rhs_zero_thickness_raises():
    invalid_section = {**SECTION_200X100X6_RHS, "thickness": 0.0}
    with pytest.raises(GeometryValidationError, match="geometrically inconsistent"):
        generate_geometry(make_rhs_member(section_properties=invalid_section))


# === RHS Test 11: excessive thickness (opening would collapse) fails clearly ===
def test_rhs_excessive_thickness_raises():
    # 50 >= depth(100)/2 -> the shorter side collapses first.
    invalid_section = {**SECTION_200X100X6_RHS, "thickness": 50.0}
    with pytest.raises(GeometryValidationError, match="geometrically inconsistent"):
        generate_geometry(make_rhs_member(section_properties=invalid_section))


# === RHS Test: missing geometry field fails clearly ===
def test_rhs_missing_geometry_field_raises():
    incomplete_section = {"name": "200x100x6 RHS", "family": "RHS", "width": 200.0, "weight_per_metre": 26.0}
    with pytest.raises(GeometryValidationError, match="missing required RHS geometry field"):
        generate_geometry(make_rhs_member(section_properties=incomplete_section))


# === RHS Test 12: SHS regression — unchanged after adding RHS ===
def test_shs_still_works_after_adding_rhs():
    result = generate_geometry(make_shs_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 150.0) < 1e-6
    assert abs(bbox.ylen - 150.0) < 1e-6
    assert abs(bbox.zlen - 3000.0) < 1e-6
    assert len(result.solid.solids().vals()) == 1

    outer_wire, inner_wires = _rhs_end_face_wires(result.solid)
    inner_pts = [(v.X, v.Y) for v in inner_wires[0].Vertices()]
    xs = [p[0] for p in inner_pts]
    ys = [p[1] for p in inner_pts]
    assert abs((max(xs) - min(xs)) - 138.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 138.0) < 1e-6

    expected_volume = (150.0 * 150.0 - 138.0 * 138.0) * 3000.0
    assert abs(result.solid.val().Volume() - expected_volume) < 1e-3


# === RHS Test 13 & 14: PFC / UB regression — unchanged after adding RHS ===
def test_pfc_and_ub_still_work_after_adding_rhs():
    pfc_result = generate_geometry(make_member())
    assert pfc_result.section_family == "PFC"
    assert len(_bottom_profile_vertices(pfc_result.solid)) == 8

    ub_result = generate_geometry(make_ub_member())
    assert ub_result.section_family == "UB"
    assert len(_bottom_profile_vertices(ub_result.solid)) == 12


# ===================================================================
# CAD Milestone 5A: SHS end plate. Proves the existing, generic
# END_PLATE connection machinery (attach_end_plate() in
# connections.py) — already used unchanged for PFC/UB — also works
# correctly for a HOLLOW member, without losing the SHS's own
# internal void. The only code change this milestone required was
# generalising the bolt-hole PATTERN (2-hole line -> also a 4-hole
# grid) in connections.py; attach_end_plate() itself needed zero
# changes, since a touching-not-overlapping union at the member's far
# end can never reach back into the member's own interior regardless
# of section family. 150x150x6 SHS remains a synthetic test fixture,
# not a verified catalogue section.
# ===================================================================

SHS_PLATE_SPEC = {"width": 150.0, "height": 150.0, "thickness": 10.0}
SHS_HOLES_SPEC = {"diameter": 18.0, "quantity": 4, "horizontal_spacing": 90.0, "vertical_spacing": 90.0}


def make_shs_connection(**overrides) -> ValidatedConnection:
    defaults = dict(
        connection_id="conn-1",
        connected_members=["SHS-TEST-1"],
        plates=[dict(SHS_PLATE_SPEC)],
        bolts=[dict(SHS_HOLES_SPEC)],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
    )
    defaults.update(overrides)
    return ValidatedConnection(**defaults)


def _generate_shs_with_end_plate():
    member = make_shs_member(connection_refs=["conn-1"])
    return generate_geometry(member, connections=[make_shs_connection()])


# === Test 1 & 2: SHS end plate generates successfully; geometry is valid ===
def test_shs_end_plate_generates_successfully():
    result = generate_geometry(
        make_shs_member(connection_refs=["conn-1"]), connections=[make_shs_connection()]
    )

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.mark == "SHS-TEST-1"
    assert result.section_family == "SHS"
    assert result.solid is not None


# === Test 3: resulting assembly is connected (a single solid, not a floating plate) ===
def test_shs_end_plate_assembly_is_one_connected_solid():
    result = _generate_shs_with_end_plate()

    assert len(result.solid.solids().vals()) == 1


# === Test 4: bounding box is 150 x 150 x 3010 ===
def test_shs_end_plate_bounding_box():
    result = _generate_shs_with_end_plate()

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 150.0) < 1e-6
    assert abs(bbox.ylen - 150.0) < 1e-6
    assert abs(bbox.zlen - 3010.0) < 1e-6
    assert abs(bbox.zmax - 3010.0) < 1e-6


# === Test 5: authoritative member length remains 3000, never 3010 ===
def test_shs_end_plate_length_mm_stays_authoritative():
    result = _generate_shs_with_end_plate()

    assert abs(result.length_mm - 3000.0) < 1e-6
    # The combined bounding box IS longer (proves the plate is really
    # attached) — but that is not the member's own cut length.
    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.zmax - 3010.0) < 1e-6


# === Test 6, 7, 8: plate width/depth/thickness, derived from the actual far face ===
def test_shs_end_plate_dimensions_from_geometry():
    result = _generate_shs_with_end_plate()

    far_face = result.solid.faces(">Z").val()
    outer_pts = [(v.X, v.Y) for v in far_face.outerWire().Vertices()]
    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]
    plate_width = max(xs) - min(xs)
    plate_height = max(ys) - min(ys)

    bbox = result.solid.val().BoundingBox()
    plate_thickness = bbox.zmax - result.length_mm

    assert abs(plate_width - 150.0) < 1e-6
    assert abs(plate_height - 150.0) < 1e-6
    assert abs(plate_thickness - 10.0) < 1e-6


# === Test 9, 10: exactly four hole voids exist, each Ø18mm ===
def test_shs_end_plate_has_four_holes_of_correct_diameter():
    result = _generate_shs_with_end_plate()

    far_face = result.solid.faces(">Z").val()
    inner_wires = far_face.innerWires()
    assert len(inner_wires) == 4

    for wire in inner_wires:
        circle_edges = [e for e in wire.Edges() if e.geomType() == "CIRCLE"]
        assert circle_edges
        radius = circle_edges[0].Length() / (2 * math.pi)
        assert abs(radius * 2 - 18.0) < 1e-6


# === Test 11 & 12: horizontal and vertical hole spacing are both 90mm ===
def test_shs_end_plate_hole_spacing_is_correct():
    result = _generate_shs_with_end_plate()

    far_face = result.solid.faces(">Z").val()
    centers = sorted(w.Center().toTuple()[:2] for w in far_face.innerWires())
    xs = sorted({round(c[0], 6) for c in centers})
    ys = sorted({round(c[1], 6) for c in centers})

    assert len(xs) == 2  # two distinct hole columns
    assert len(ys) == 2  # two distinct hole rows
    assert abs((xs[1] - xs[0]) - 90.0) < 1e-6  # horizontal spacing
    assert abs((ys[1] - ys[0]) - 90.0) < 1e-6  # vertical spacing


# === Test 13 & 14: THE KEY TEST — the SHS hollow void remains, verified from
# actual topology at the end of the member farthest from the plate (Z=0),
# which the plate-attachment union never touches. A boolean succeeding, a
# correct bounding box, or a single-solid result do NOT by themselves prove
# this — only inspecting the real face topology does. ===
def test_shs_end_plate_hollow_void_remains_present():
    result = _generate_shs_with_end_plate()

    bottom_face = result.solid.faces("<Z").val()
    inner_wires = bottom_face.innerWires()
    assert len(inner_wires) == 1, (
        "the SHS's own bottom face should still have exactly one inner wire "
        "(the hollow void) — zero would mean the tube got silently filled"
    )

    inner_pts = [(v.X, v.Y) for v in inner_wires[0].Vertices()]
    xs = [p[0] for p in inner_pts]
    ys = [p[1] for p in inner_pts]
    assert abs((max(xs) - min(xs)) - 138.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 138.0) < 1e-6


def test_shs_end_plate_hollow_void_unchanged_from_bare_shs():
    """
    A second, independent angle on the same proof: the bare SHS's own
    hollow opening (built and verified in Milestone 4A, before any
    connection existed) is compared directly against the connected
    assembly's bottom-face opening — they must be identical, proving
    the connection didn't alter the member's own geometry at all,
    only added to it.
    """
    bare_result = generate_geometry(make_shs_member())
    connected_result = _generate_shs_with_end_plate()

    bare_inner = bare_result.solid.faces("<Z").val().innerWires()[0]
    connected_inner = connected_result.solid.faces("<Z").val().innerWires()[0]

    bare_pts = sorted((round(v.X, 6), round(v.Y, 6)) for v in bare_inner.Vertices())
    connected_pts = sorted((round(v.X, 6), round(v.Y, 6)) for v in connected_inner.Vertices())
    assert bare_pts == connected_pts


# === Test 15, 16, 17: existing UB end plate, SHS, RHS all still pass ===
def test_ub_end_plate_still_works_after_shs_end_plate():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    assert result.connection_count == 1
    assert result.connection_types == ["END_PLATE"]
    cyl_faces = [f for f in result.solid.val().Faces() if f.geomType() == "CYLINDER"]
    assert len(cyl_faces) == 2  # still the original 2-hole pattern, unaffected


def test_shs_bare_geometry_still_works_after_shs_end_plate():
    result = generate_geometry(make_shs_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 150.0) < 1e-6
    assert abs(bbox.ylen - 150.0) < 1e-6
    assert abs(bbox.zlen - 3000.0) < 1e-6
    assert len(result.solid.solids().vals()) == 1


def test_rhs_bare_geometry_still_works_after_shs_end_plate():
    result = generate_geometry(make_rhs_member())

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 200.0) < 1e-6
    assert abs(bbox.ylen - 100.0) < 1e-6
    assert abs(bbox.zlen - 3000.0) < 1e-6
    assert len(result.solid.solids().vals()) == 1


# === Extra: connection metadata reports END_PLATE / connection_count=1, no new type invented ===
def test_shs_end_plate_connection_metadata_is_correct():
    result = _generate_shs_with_end_plate()

    assert result.connection_count == 1
    assert result.connection_types == ["END_PLATE"]


# === Extra: 4-hole pattern missing horizontal_spacing fails clearly ===
def test_shs_four_hole_pattern_missing_horizontal_spacing_raises():
    bad_holes = {"diameter": 18.0, "quantity": 4, "vertical_spacing": 90.0}  # no horizontal_spacing
    connection = make_shs_connection(bolts=[bad_holes])

    with pytest.raises(GeometryValidationError, match="horizontal_spacing"):
        generate_geometry(make_shs_member(connection_refs=["conn-1"]), connections=[connection])


# === Extra: an unsupported hole quantity (e.g. 3) still fails clearly, not silently ===
def test_shs_unsupported_hole_quantity_raises():
    bad_holes = {"diameter": 18.0, "quantity": 3, "vertical_spacing": 90.0}
    connection = make_shs_connection(bolts=[bad_holes])

    with pytest.raises(GeometryValidationError, match=r"only .*-hole end-plate patterns"):
        generate_geometry(make_shs_member(connection_refs=["conn-1"]), connections=[connection])


# ===================================================================
# CAD Milestone 5B: SHS end-plate robustness & parameter variation.
# Stress-tests the SAME generic connection code from 5A
# (attach_end_plate / build_end_plate_with_holes / _compute_hole_centers
# in connections.py) against a materially different hollow-section,
# plate, and hole configuration — with ZERO changes to connections.py.
# This milestone required no production-code change at all; every test
# below exercises the already-existing, already-generic implementation.
#
# NAMING NOTE, stated plainly rather than silently worked around: the
# brief's "200x100x6 SHS" fixture is dimensionally NOT square
# (200 != 100). This project's own established naming, from Milestones
# 4A/4B, is that SHS (build_shs_profile) is square-only — it reads only
# `width` and ignores `depth` entirely, deriving both outer dimensions
# from width alone. Verified empirically before writing any test: giving
# it family="SHS" with width=200 (ignoring the requested depth=100)
# silently produces a 200x200 SQUARE, contradicting the brief's explicit
# outer-depth=100mm / inner-opening=188x88mm requirements. The
# dimensionally-correct family for an independent width/depth hollow
# section is RHS (build_rhs_profile, from Milestone 4B), which reproduces
# the brief's stated geometry exactly (confirmed below). Using RHS here
# is not a deviation from what this milestone actually tests — the
# END_PLATE connection code in connections.py has no family-awareness
# whatsoever (see its own module docstring), so exercising it against an
# RHS hollow member is exactly as valid a stress test of "the generic
# hollow-section end-plate architecture" as SHS would be, and it's the
# only way to honour the brief's literal numeric requirements rather
# than silently substituting a square the brief never asked for.
# ===================================================================

SHS2_SECTION = {
    "name": "200x100x6 SHS", "family": "RHS",  # see naming note above
    "width": 200.0, "depth": 100.0, "thickness": 6.0,
    "weight_per_metre": 26.0,
}
SHS2_PLATE_SPEC = {"width": 200.0, "height": 100.0, "thickness": 12.0}
SHS2_HOLES_SPEC = {"diameter": 20.0, "quantity": 4, "horizontal_spacing": 120.0, "vertical_spacing": 60.0}


def make_shs2_member(**overrides) -> ValidatedSteelMember:
    defaults = dict(
        mark="SHS-TEST-2",
        section="200x100x6 SHS",
        length_mm=2500.0,
        material="300PLUS",
        orientation=None,
        connection_refs=[],
        source_refs=[{"page": 1}],
        validation_status="extracted",
        section_properties=SHS2_SECTION,
    )
    defaults.update(overrides)
    return ValidatedSteelMember(**defaults)


def make_shs2_connection(**overrides) -> ValidatedConnection:
    defaults = dict(
        connection_id="conn-1",
        connected_members=["SHS-TEST-2"],
        plates=[dict(SHS2_PLATE_SPEC)],
        bolts=[dict(SHS2_HOLES_SPEC)],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
    )
    defaults.update(overrides)
    return ValidatedConnection(**defaults)


def _generate_shs2_with_end_plate(connection=None):
    member = make_shs2_member(connection_refs=["conn-1"])
    return generate_geometry(member, connections=[connection or make_shs2_connection()])


# === Regression: the original 5A fixture (150x150x6 SHS / 150x150x10 plate
# / 4xO18 / 90x90) must remain completely intact and unmodified ===
def test_5a_fixture_still_produces_original_geometry():
    result = _generate_shs_with_end_plate()

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 150.0) < 1e-6
    assert abs(bbox.ylen - 150.0) < 1e-6
    assert abs(bbox.zlen - 3010.0) < 1e-6
    assert abs(result.length_mm - 3000.0) < 1e-6


# === Member: outer dimensions, length, and inner opening — all from actual geometry ===
def test_shs2_member_geometry_is_correct():
    bare_result = generate_geometry(make_shs2_member())

    assert bare_result.section_family == "RHS"
    bbox = bare_result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 200.0) < 1e-6
    assert abs(bbox.ylen - 100.0) < 1e-6
    assert abs(bbox.zlen - 2500.0) < 1e-6
    assert abs(bare_result.length_mm - 2500.0) < 1e-6

    bottom = bare_result.solid.faces("<Z").val()
    inner_pts = [(v.X, v.Y) for v in bottom.innerWires()[0].Vertices()]
    xs = [p[0] for p in inner_pts]
    ys = [p[1] for p in inner_pts]
    assert abs((max(xs) - min(xs)) - 188.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 88.0) < 1e-6


# === Test 1: SHS-TEST-2 end plate generates successfully ===
def test_shs2_end_plate_generates_successfully():
    result = _generate_shs2_with_end_plate()

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.mark == "SHS-TEST-2"
    assert result.solid is not None
    assert result.connection_count == 1
    assert result.connection_types == ["END_PLATE"]


# === Test 8: exactly 1 connected solid, plate not floating ===
def test_shs2_end_plate_assembly_is_one_connected_solid():
    result = _generate_shs2_with_end_plate()

    assert len(result.solid.solids().vals()) == 1


# === Test 9: bounding box is 200 x 100 x 2512 ===
def test_shs2_end_plate_bounding_box():
    result = _generate_shs2_with_end_plate()

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 200.0) < 1e-6
    assert abs(bbox.ylen - 100.0) < 1e-6
    assert abs(bbox.zlen - 2512.0) < 1e-6
    assert abs(bbox.zmax - 2512.0) < 1e-6


# === Test 9 (length): authoritative length_mm remains 2500, never 2512 ===
def test_shs2_end_plate_length_mm_stays_authoritative():
    result = _generate_shs2_with_end_plate()

    assert abs(result.length_mm - 2500.0) < 1e-6
    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.zmax - 2512.0) < 1e-6


# === Test 6 (plate): plate dimensions derived from the actual far face ===
def test_shs2_end_plate_dimensions_from_geometry():
    result = _generate_shs2_with_end_plate()

    far_face = result.solid.faces(">Z").val()
    outer_pts = [(v.X, v.Y) for v in far_face.outerWire().Vertices()]
    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]
    plate_width = max(xs) - min(xs)
    plate_height = max(ys) - min(ys)

    bbox = result.solid.val().BoundingBox()
    plate_thickness = bbox.zmax - result.length_mm

    assert abs(plate_width - 200.0) < 1e-6
    assert abs(plate_height - 100.0) < 1e-6
    assert abs(plate_thickness - 12.0) < 1e-6


# === Test 6 (holes): exactly 4 holes, each Ø20mm ===
def test_shs2_end_plate_has_four_holes_of_correct_diameter():
    result = _generate_shs2_with_end_plate()

    far_face = result.solid.faces(">Z").val()
    inner_wires = far_face.innerWires()
    assert len(inner_wires) == 4

    for wire in inner_wires:
        circle_edges = [e for e in wire.Edges() if e.geomType() == "CIRCLE"]
        assert circle_edges
        radius = circle_edges[0].Length() / (2 * math.pi)
        assert abs(radius * 2 - 20.0) < 1e-6


# === Test 6 (spacing): horizontal 120mm, vertical 60mm, centres exactly as specified ===
def test_shs2_end_plate_hole_spacing_and_positions_are_correct():
    result = _generate_shs2_with_end_plate()

    far_face = result.solid.faces(">Z").val()
    centers = sorted(tuple(round(c, 6) for c in w.Center().toTuple()[:2]) for w in far_face.innerWires())

    expected = sorted([(40.0, 20.0), (160.0, 20.0), (40.0, 80.0), (160.0, 80.0)])
    assert centers == expected

    xs = sorted({c[0] for c in centers})
    ys = sorted({c[1] for c in centers})
    assert abs((xs[1] - xs[0]) - 120.0) < 1e-6
    assert abs((ys[1] - ys[0]) - 60.0) < 1e-6


# === Test 7: THE KEY TEST — the 188x88mm hollow opening remains present after
# attaching the end plate, verified from the far-from-the-plate end's real
# topology, not from a successful boolean/bbox/solid-count alone ===
def test_shs2_end_plate_hollow_void_remains_present():
    result = _generate_shs2_with_end_plate()

    bottom_face = result.solid.faces("<Z").val()
    inner_wires = bottom_face.innerWires()
    assert len(inner_wires) == 1, "the hollow opening must still exist as a real inner wire"

    inner_pts = [(v.X, v.Y) for v in inner_wires[0].Vertices()]
    xs = [p[0] for p in inner_pts]
    ys = [p[1] for p in inner_pts]
    assert abs((max(xs) - min(xs)) - 188.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 88.0) < 1e-6


def test_shs2_end_plate_hollow_void_unchanged_from_bare_shs2():
    """Same cross-check technique as 5A: compare against an independently-built bare member."""
    bare_result = generate_geometry(make_shs2_member())
    connected_result = _generate_shs2_with_end_plate()

    bare_inner = bare_result.solid.faces("<Z").val().innerWires()[0]
    connected_inner = connected_result.solid.faces("<Z").val().innerWires()[0]

    bare_pts = sorted((round(v.X, 6), round(v.Y, 6)) for v in bare_inner.Vertices())
    connected_pts = sorted((round(v.X, 6), round(v.Y, 6)) for v in connected_inner.Vertices())
    assert bare_pts == connected_pts


# === Parameter sensitivity: Case A (5A fixture) vs Case B (5B fixture) produce
# genuinely different geometry — the implementation is not reusing old values ===
def test_end_plate_geometry_differs_between_5a_and_5b_fixtures():
    case_a = _generate_shs_with_end_plate()   # 150x150x6 / 150x150x10 / 4xO18 / 90x90
    case_b = _generate_shs2_with_end_plate()  # 200x100x6 / 200x100x12 / 4xO20 / 120x60

    bbox_a = case_a.solid.val().BoundingBox()
    bbox_b = case_b.solid.val().BoundingBox()
    assert (bbox_a.xlen, bbox_a.ylen, bbox_a.zlen) != (bbox_b.xlen, bbox_b.ylen, bbox_b.zlen)

    far_a = case_a.solid.faces(">Z").val()
    far_b = case_b.solid.faces(">Z").val()

    def hole_diameter(face):
        wire = face.innerWires()[0]
        edge = [e for e in wire.Edges() if e.geomType() == "CIRCLE"][0]
        return edge.Length() / (2 * math.pi) * 2

    assert abs(hole_diameter(far_a) - 18.0) < 1e-6
    assert abs(hole_diameter(far_b) - 20.0) < 1e-6
    assert hole_diameter(far_a) != hole_diameter(far_b)

    centers_a = sorted(w.Center().toTuple()[:2] for w in far_a.innerWires())
    centers_b = sorted(w.Center().toTuple()[:2] for w in far_b.innerWires())
    assert centers_a != centers_b


# === Stronger alternate-parameter test: SAME SHS-TEST-2 member, two different
# hole connections — proves _compute_hole_centers() and the cutting operation
# are genuinely parameter-driven, not just family/member-driven ===
def test_hole_geometry_changes_with_different_connection_params_on_same_member():
    connection_1 = make_shs2_connection(
        bolts=[{"diameter": 20.0, "quantity": 4, "horizontal_spacing": 120.0, "vertical_spacing": 60.0}]
    )
    connection_2 = make_shs2_connection(
        connection_id="conn-2",
        bolts=[{"diameter": 16.0, "quantity": 4, "horizontal_spacing": 100.0, "vertical_spacing": 40.0}]
    )

    result_1 = generate_geometry(make_shs2_member(connection_refs=["conn-1"]), connections=[connection_1])
    result_2 = generate_geometry(make_shs2_member(connection_refs=["conn-2"]), connections=[connection_2])

    def hole_info(result):
        far_face = result.solid.faces(">Z").val()
        wires = far_face.innerWires()
        diameters = set()
        for w in wires:
            edge = [e for e in w.Edges() if e.geomType() == "CIRCLE"][0]
            diameters.add(round(edge.Length() / (2 * math.pi) * 2, 6))
        centers = sorted(tuple(round(c, 6) for c in w.Center().toTuple()[:2]) for w in wires)
        return diameters, centers

    diameters_1, centers_1 = hole_info(result_1)
    diameters_2, centers_2 = hole_info(result_2)

    assert diameters_1 == {20.0}
    assert diameters_2 == {16.0}
    assert diameters_1 != diameters_2  # hole diameter genuinely changed

    assert centers_1 == sorted([(40.0, 20.0), (160.0, 20.0), (40.0, 80.0), (160.0, 80.0)])
    assert centers_2 == sorted([(50.0, 30.0), (150.0, 30.0), (50.0, 70.0), (150.0, 70.0)])
    assert centers_1 != centers_2  # hole centre positions genuinely changed


# === UB regression: existing 2-hole pattern, spacing, diameter, metadata all unchanged ===
def test_ub_end_plate_full_regression_after_5b():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])

    assert result.connection_count == 1
    assert result.connection_types == ["END_PLATE"]

    far_face = result.solid.faces(">Z").val()
    inner_wires = far_face.innerWires()
    assert len(inner_wires) == 2  # still the 2-hole pattern, not upgraded to 4

    for wire in inner_wires:
        edge = [e for e in wire.Edges() if e.geomType() == "CIRCLE"][0]
        diameter = edge.Length() / (2 * math.pi) * 2
        assert abs(diameter - 18.0) < 1e-6  # existing PFC/UB fixture's hole diameter

    ys = sorted(w.Center().toTuple()[1] for w in inner_wires)
    assert abs((ys[1] - ys[0]) - 150.0) < 1e-6  # existing vertical_spacing


# === Validation: zero hole diameter still fails clearly on the 5B fixture ===
def test_shs2_zero_hole_diameter_raises():
    connection = make_shs2_connection(bolts=[{**SHS2_HOLES_SPEC, "diameter": 0.0}])
    with pytest.raises(GeometryValidationError, match="positive"):
        generate_geometry(make_shs2_member(connection_refs=["conn-1"]), connections=[connection])


# === Validation: a hole pattern too large for the plate still fails clearly ===
def test_shs2_hole_pattern_too_large_for_plate_raises():
    connection = make_shs2_connection(
        bolts=[{**SHS2_HOLES_SPEC, "horizontal_spacing": -500.0}]  # pushes a centre outside the plate
    )
    with pytest.raises(GeometryValidationError, match="outside the plate"):
        generate_geometry(make_shs2_member(connection_refs=["conn-1"]), connections=[connection])


# === Validation: hole diameter too large for the plate still fails clearly ===
def test_shs2_hole_diameter_incompatible_with_plate_raises():
    connection = make_shs2_connection(bolts=[{**SHS2_HOLES_SPEC, "diameter": 250.0}])
    with pytest.raises(GeometryValidationError, match="does not fit within the plate"):
        generate_geometry(make_shs2_member(connection_refs=["conn-1"]), connections=[connection])


# === Full CAD regression after 5B: SHS/RHS bare geometry unaffected ===
def test_shs_and_rhs_bare_geometry_unaffected_by_5b():
    shs_result = generate_geometry(make_shs_member())
    bbox = shs_result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 150.0) < 1e-6 and abs(bbox.ylen - 150.0) < 1e-6 and abs(bbox.zlen - 3000.0) < 1e-6

    rhs_result = generate_geometry(make_rhs_member())
    bbox = rhs_result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 200.0) < 1e-6 and abs(bbox.ylen - 100.0) < 1e-6 and abs(bbox.zlen - 3000.0) < 1e-6


# ===================================================================
# CAD Milestone 6A: multiple independent connections on one member.
# The resolve/attach loop in interface.py already iterated
# member.connection_refs generically before this milestone — nothing
# there was ever limited to one connection. The one real gap was that
# attach_end_plate() always placed the plate at the member's far end;
# there was no way to say "this connection is at the OTHER end." This
# milestone adds exactly that: ValidatedConnection.position
# ("START"/"END", default "END" for full backward compatibility),
# consumed only by connections.attach_end_plate() to pick which face
# to build against. No family-specific logic, no connection-index
# logic — attach_end_plate() only ever looks at the one connection
# it's called with, and only ever branches on connection.position,
# a plain data value the caller (interface.py) has already validated.
# ===================================================================

START_PLATE_SPEC = PLATE_SPEC  # 90x250x10, Ø18x2 @ 150 vertical — same as the existing single-END-PLATE fixture
START_HOLES_SPEC = HOLES_SPEC
END_PLATE_SPEC_B = {"width": 90.0, "height": 250.0, "thickness": 12.0}
END_HOLES_SPEC_B = {"diameter": 20.0, "quantity": 4, "horizontal_spacing": 60.0, "vertical_spacing": 120.0}


def make_start_connection(**overrides) -> ValidatedConnection:
    defaults = dict(
        connection_id="conn-start",
        connected_members=["UB-TEST-1"],
        plates=[dict(START_PLATE_SPEC)],
        bolts=[dict(START_HOLES_SPEC)],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
        position="START",
    )
    defaults.update(overrides)
    return ValidatedConnection(**defaults)


def make_end_connection(**overrides) -> ValidatedConnection:
    defaults = dict(
        connection_id="conn-end",
        connected_members=["UB-TEST-1"],
        plates=[dict(END_PLATE_SPEC_B)],
        bolts=[dict(END_HOLES_SPEC_B)],
        welds=[],
        dimensions=None,
        source_refs=[{"page": 1}],
        validation_status="extracted",
        connection_type="END_PLATE",
        position="END",
    )
    defaults.update(overrides)
    return ValidatedConnection(**defaults)


def _generate_ub_with_both_end_plates(start_connection=None, end_connection=None):
    member = make_ub_member(connection_refs=["conn-start", "conn-end"])
    return generate_geometry(
        member, connections=[start_connection or make_start_connection(), end_connection or make_end_connection()]
    )


def _face_holes(face):
    """(diameter, (x, y)) for each hole on a face, derived from actual topology."""
    results = []
    for wire in face.innerWires():
        edges = [e for e in wire.Edges() if e.geomType() == "CIRCLE"]
        radius = edges[0].Length() / (2 * math.pi)
        cx, cy, _cz = wire.Center().toTuple()
        results.append((round(radius * 2, 6), (round(cx, 6), round(cy, 6))))
    return sorted(results)


# === Test 1: two-connection generation succeeds; metadata reports both ===
def test_two_connections_generate_successfully():
    result = _generate_ub_with_both_end_plates()

    assert isinstance(result, GeneratedMemberGeometry)
    assert result.connection_count == 2
    assert result.connection_types == ["END_PLATE", "END_PLATE"]


# === Test 7: exactly 1 connected solid — member + both plates, none floating ===
def test_two_connections_produce_one_connected_solid():
    result = _generate_ub_with_both_end_plates()

    assert len(result.solid.solids().vals()) == 1


# === Test 8: actual bounding box, derived from geometry, not assumed ===
def test_two_connections_bounding_box_is_correct():
    result = _generate_ub_with_both_end_plates()

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.xlen - 165.0) < 1e-6   # UB flange width, unaffected
    assert abs(bbox.ylen - 310.0) < 1e-6   # UB depth, unaffected
    assert abs(bbox.zmin - (-10.0)) < 1e-6  # start plate extends 10mm before Z=0
    assert abs(bbox.zmax - 4012.0) < 1e-6   # end plate extends 12mm past Z=4000
    assert abs(bbox.zlen - 4022.0) < 1e-6   # total assembly length

    # The member's own authoritative length must NOT be redefined by the assembly.
    assert abs(result.length_mm - 4000.0) < 1e-6


# === Start connection: exists, correct dimensions/holes, derived from actual geometry ===
def test_start_connection_plate_and_holes_from_geometry():
    result = _generate_ub_with_both_end_plates()

    # Start plate is the solid's own minimum-Z face (its own outer face).
    start_face = result.solid.faces("<Z").val()
    outer_pts = [(v.X, v.Y) for v in start_face.outerWire().Vertices()]
    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]
    assert abs((max(xs) - min(xs)) - 90.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 250.0) < 1e-6

    bbox = result.solid.val().BoundingBox()
    start_thickness = 0.0 - bbox.zmin  # plate spans [zmin, 0]
    assert abs(start_thickness - 10.0) < 1e-6

    holes = _face_holes(start_face)
    assert len(holes) == 2
    assert all(abs(d - 18.0) < 1e-6 for d, _c in holes)
    ys_centers = sorted(c[1] for _d, c in holes)
    assert abs((ys_centers[1] - ys_centers[0]) - 150.0) < 1e-6


# === End connection: exists, correct dimensions/holes, derived from actual geometry ===
def test_end_connection_plate_and_holes_from_geometry():
    result = _generate_ub_with_both_end_plates()

    end_face = result.solid.faces(">Z").val()
    outer_pts = [(v.X, v.Y) for v in end_face.outerWire().Vertices()]
    xs = [p[0] for p in outer_pts]
    ys = [p[1] for p in outer_pts]
    assert abs((max(xs) - min(xs)) - 90.0) < 1e-6
    assert abs((max(ys) - min(ys)) - 250.0) < 1e-6

    bbox = result.solid.val().BoundingBox()
    end_thickness = bbox.zmax - result.length_mm  # plate spans [length_mm, zmax]
    assert abs(end_thickness - 12.0) < 1e-6

    holes = _face_holes(end_face)
    assert len(holes) == 4
    assert all(abs(d - 20.0) < 1e-6 for d, _c in holes)
    expected_centers = sorted([(15.0, 65.0), (75.0, 65.0), (15.0, 185.0), (75.0, 185.0)])
    actual_centers = sorted(c for _d, c in holes)
    assert actual_centers == expected_centers


# === Test 10: independence — changing Connection A does not affect Connection B ===
def test_connection_a_change_does_not_affect_connection_b():
    baseline = _generate_ub_with_both_end_plates()
    baseline_end_thickness = baseline.solid.val().BoundingBox().zmax - baseline.length_mm

    changed_start = make_start_connection(plates=[{**START_PLATE_SPEC, "thickness": 15.0}])
    result = _generate_ub_with_both_end_plates(start_connection=changed_start)

    bbox = result.solid.val().BoundingBox()
    new_start_thickness = 0.0 - bbox.zmin
    end_thickness = bbox.zmax - result.length_mm

    assert abs(new_start_thickness - 15.0) < 1e-6   # Connection A's change took effect
    assert abs(end_thickness - baseline_end_thickness) < 1e-6  # Connection B is untouched
    assert abs(end_thickness - 12.0) < 1e-6


# === Test 10: independence — changing Connection B does not affect Connection A ===
def test_connection_b_change_does_not_affect_connection_a():
    changed_end = make_end_connection(bolts=[{**END_HOLES_SPEC_B, "diameter": 16.0}])
    result = _generate_ub_with_both_end_plates(end_connection=changed_end)

    start_face = result.solid.faces("<Z").val()
    end_face = result.solid.faces(">Z").val()

    start_holes = _face_holes(start_face)
    end_holes = _face_holes(end_face)

    assert all(abs(d - 18.0) < 1e-6 for d, _c in start_holes)  # Connection A untouched
    assert all(abs(d - 16.0) < 1e-6 for d, _c in end_holes)    # Connection B's change took effect
    assert len(end_holes) == 4


# === Regression: existing single-connection UB behaviour (default position="END") unchanged ===
def test_single_connection_ub_regression_after_6a():
    member = make_member(connection_refs=["conn-1"])
    result = generate_geometry(member, connections=[make_connection()])  # no `position` set -> defaults to "END"

    assert result.connection_count == 1
    assert result.connection_types == ["END_PLATE"]

    far_face = result.solid.faces(">Z").val()
    holes = _face_holes(far_face)
    assert len(holes) == 2
    assert all(abs(d - 18.0) < 1e-6 for d, _c in holes)

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.zmin - 0.0) < 1e-6  # nothing at the start end — single-connection behaviour unchanged


# === Regression: SHS end plate (single, position defaults to "END") unaffected ===
def test_shs_end_plate_regression_after_6a():
    result = _generate_shs_with_end_plate()

    bbox = result.solid.val().BoundingBox()
    assert abs(bbox.zmax - 3010.0) < 1e-6
    assert abs(bbox.zmin - 0.0) < 1e-6

    bottom_face = result.solid.faces("<Z").val()
    assert len(bottom_face.innerWires()) == 1  # hollow void still present at the untouched start end


# === Validation: two connections both claiming the same position ("END") are rejected ===
def test_duplicate_position_raises():
    member = make_ub_member(connection_refs=["conn-a", "conn-b"])
    conn_a = make_end_connection(connection_id="conn-a")
    conn_b = make_end_connection(connection_id="conn-b")  # also "END" -> duplicate

    with pytest.raises(GeometryValidationError, match="both claim position"):
        generate_geometry(member, connections=[conn_a, conn_b])


# === Validation: an unsupported position value fails clearly ===
def test_unsupported_position_raises():
    member = make_ub_member(connection_refs=["conn-1"])
    connection = make_end_connection(connection_id="conn-1", position="MIDDLE")

    with pytest.raises(GeometryValidationError, match="not supported"):
        generate_geometry(member, connections=[connection])


# === Validation: a missing (None) position value fails clearly, not silently defaulting ===
def test_missing_position_raises():
    member = make_ub_member(connection_refs=["conn-1"])
    connection = make_end_connection(connection_id="conn-1", position=None)

    with pytest.raises(GeometryValidationError, match="not supported"):
        generate_geometry(member, connections=[connection])


# === Full regression: PFC/UB/SHS/RHS bare geometry, and the 5A/5B fixtures, unaffected ===
def test_full_regression_after_6a():
    pfc_result = generate_geometry(make_member())
    assert pfc_result.section_family == "PFC"

    ub_result = generate_geometry(make_ub_member())
    assert ub_result.section_family == "UB"

    shs_result = generate_geometry(make_shs_member())
    assert len(shs_result.solid.faces("<Z").val().innerWires()) == 1

    rhs_result = generate_geometry(make_rhs_member())
    assert len(rhs_result.solid.faces("<Z").val().innerWires()) == 1

    shs2_result = _generate_shs2_with_end_plate()
    assert shs2_result.connection_count == 1


# ===================================================================
# CAD Milestone 6B: physical bolt hardware (hex head + cylindrical
# shaft), optional per-connection, always kept separate from the
# steel solid. GeneratedMemberGeometry.solid keeps its Milestone-2
# meaning unchanged (member + plate + hole voids, one connected steel
# solid); GeneratedMemberGeometry.hardware is a new, independent list
# of bolt solids, each positioned from the connection's own ACTUAL
# hole geometry (never recomputed independently) and never unioned
# into `solid`.
# ===================================================================

BOLT_SPEC = {"shaft_diameter": 16.0, "shaft_length": 40.0, "head_across_flats": 24.0, "head_thickness": 10.0}


def make_bolted_connection(**overrides) -> ValidatedConnection:
    defaults = dict(connected_members=["UB-TEST-1"], physical_bolts=dict(BOLT_SPEC))
    defaults.update(overrides)
    return make_connection(**defaults)


def _generate_ub_with_bolted_end_plate(connection=None):
    member = make_ub_member(connection_refs=["conn-1"])
    return generate_geometry(member, connections=[connection or make_bolted_connection()])


def _bolt_shaft_face(bolt_wp):
    cyl = [f for f in bolt_wp.val().Faces() if f.geomType() == "CYLINDER"]
    assert len(cyl) == 1, "expected exactly one cylindrical (shaft) face per bolt"
    return cyl[0]


def _bolt_shaft_diameter(bolt_wp) -> float:
    face = _bolt_shaft_face(bolt_wp)
    edges = [e for e in face.Edges() if e.geomType() == "CIRCLE"]
    radius = edges[0].Length() / (2 * math.pi)
    return radius * 2


def _bolt_head_across_flats(bolt_wp) -> float:
    """
    Derived independently of the production formula: across-flats is
    twice the perpendicular distance from the bolt's own central axis
    to each in-plane (hex side) planar face — not a re-assertion of
    build_bolt()'s own head_radius calculation.
    """
    bbox = bolt_wp.val().BoundingBox()
    cx, cy = (bbox.xmin + bbox.xmax) / 2, (bbox.ymin + bbox.ymax) / 2
    side_faces = [
        f for f in bolt_wp.val().Faces()
        if f.geomType() == "PLANE" and abs(f.normalAt().z) < 1e-6
    ]
    assert len(side_faces) == 6, f"expected 6 hexagonal side faces, got {len(side_faces)}"
    apothems = []
    for f in side_faces:
        n = f.normalAt()
        pt = f.Center()
        apothems.append(abs((pt.x - cx) * n.x + (pt.y - cy) * n.y))
    across_flats_values = [2 * a for a in apothems]
    assert max(across_flats_values) - min(across_flats_values) < 1e-6
    return across_flats_values[0]


def _bolt_center_xy(bolt_wp) -> tuple:
    bbox = bolt_wp.val().BoundingBox()
    return (round((bbox.xmin + bbox.xmax) / 2, 6), round((bbox.ymin + bbox.ymax) / 2, 6))


def _steel_hole_centers_xy(result) -> list:
    return sorted(
        (round(f.Center().x, 6), round(f.Center().y, 6))
        for f in _cylindrical_faces(result.solid)
    )


# === Optionality: no physical_bolts spec -> no hardware, steel/plate unaffected ===
def test_physical_bolts_are_optional_and_default_off():
    member = make_ub_member(connection_refs=["conn-1"])
    connection = make_connection(connected_members=["UB-TEST-1"])  # physical_bolts left unset

    assert connection.physical_bolts is None
    result = generate_geometry(member, connections=[connection])

    assert result.hardware == []
    assert result.connection_count == 1


# === Requesting physical_bolts generates exactly 2 bolts ===
def test_physical_bolts_generate_exactly_two_bolts():
    result = _generate_ub_with_bolted_end_plate()

    assert len(result.hardware) == 2


# === Steel solid is provably unaffected by enabling physical bolts ===
def test_steel_solid_unaffected_by_physical_bolts():
    without = _generate_ub_with_bolted_end_plate(connection=make_connection(connected_members=["UB-TEST-1"]))
    with_bolts = _generate_ub_with_bolted_end_plate()

    bbox_a = without.solid.val().BoundingBox()
    bbox_b = with_bolts.solid.val().BoundingBox()
    assert abs(bbox_a.xlen - bbox_b.xlen) < 1e-6
    assert abs(bbox_a.ylen - bbox_b.ylen) < 1e-6
    assert abs(bbox_a.zlen - bbox_b.zlen) < 1e-6

    assert abs(without.solid.val().Volume() - with_bolts.solid.val().Volume()) < 1e-3
    assert len(without.solid.val().Faces()) == len(with_bolts.solid.val().Faces())

    assert len(without.solid.solids().vals()) == 1
    assert len(with_bolts.solid.solids().vals()) == 1
    assert len(_cylindrical_faces(without.solid)) == 2
    assert len(_cylindrical_faces(with_bolts.solid)) == 2


# === Steel solid: exactly 2 Ø18 holes remain, plate stays connected, with bolts enabled ===
def test_steel_solid_holes_unchanged_with_bolts_enabled():
    result = _generate_ub_with_bolted_end_plate()

    assert len(result.solid.solids().vals()) == 1
    cyl_faces = _cylindrical_faces(result.solid)
    assert len(cyl_faces) == 2
    for face in cyl_faces:
        assert abs(_face_hole_diameter(face) - 18.0) < 1e-3


# === Each bolt is one connected solid, independently identifiable ===
def test_each_bolt_is_one_connected_solid():
    result = _generate_ub_with_bolted_end_plate()

    for bolt in result.hardware:
        assert len(bolt.solids().vals()) == 1


def test_bolts_are_independent_non_overlapping_solids():
    result = _generate_ub_with_bolted_end_plate()
    bolt_a, bolt_b = result.hardware

    bbox_a = bolt_a.val().BoundingBox()
    bbox_b = bolt_b.val().BoundingBox()
    # 150mm hole spacing vs. a ~27.7mm hex head width -> bounding boxes
    # cannot overlap in Y unless the two bolts were wrongly fused/placed.
    assert bbox_a.ymax < bbox_b.ymin or bbox_b.ymax < bbox_a.ymin


def test_physical_bolts_are_independently_identifiable():
    result = _generate_ub_with_bolted_end_plate()

    centers = [_bolt_center_xy(b) for b in result.hardware]
    assert len(centers) == 2
    assert centers[0] != centers[1]


# === Each bolt has a cylindrical shaft (Ø16) and a hexagonal head (24mm across flats) ===
def test_each_bolt_has_cylindrical_shaft_and_hexagonal_head():
    result = _generate_ub_with_bolted_end_plate()

    for bolt in result.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 16.0) < 1e-3
        assert abs(_bolt_head_across_flats(bolt) - 24.0) < 1e-3


# === Shaft length and head thickness are derivable separately from geometry ===
def test_bolt_shaft_length_and_head_thickness_from_geometry():
    result = _generate_ub_with_bolted_end_plate()

    for bolt in result.hardware:
        # The shaft's own cylindrical face spans its full length regardless
        # of the head fused onto one end - isolates shaft_length from head_thickness.
        shaft_bbox = _bolt_shaft_face(bolt).BoundingBox()
        assert abs(shaft_bbox.zlen - 40.0) < 1e-6

        overall_bbox = bolt.val().BoundingBox()
        assert abs(overall_bbox.zlen - 50.0) < 1e-6  # shaft_length + head_thickness


# === Hardware bounding box: derived from actual geometry, not asserted from input metadata ===
def test_hardware_bolt_bounding_box_matches_derived_geometry():
    result = _generate_ub_with_bolted_end_plate()
    bolt = result.hardware[0]
    bbox = bolt.val().BoundingBox()

    # build_bolt() places the hexagon's first vertex on the +X axis (relative
    # to the hole centre), so the head's X-extent is the full circumscribed
    # diameter (vertex-to-vertex) while its Y-extent is the across-flats
    # distance (flat-to-flat) — both independently derivable from the spec.
    expected_circumdiameter = BOLT_SPEC["head_across_flats"] * 2 / math.sqrt(3)
    assert abs(bbox.xlen - expected_circumdiameter) < 1e-6
    assert abs(bbox.ylen - BOLT_SPEC["head_across_flats"]) < 1e-6
    assert abs(bbox.zlen - (BOLT_SPEC["shaft_length"] + BOLT_SPEC["head_thickness"])) < 1e-6

    # END position: the plate's outward face sits at length_mm + plate_thickness
    # = 4000 + 10 = 4010; the head extends further outward (larger Z) from
    # there, the shaft extends inward (smaller Z) through the plate.
    outward_face_z = 4000.0 + 10.0
    assert abs(bbox.zmax - (outward_face_z + BOLT_SPEC["head_thickness"])) < 1e-6
    assert abs(bbox.zmin - (outward_face_z - BOLT_SPEC["shaft_length"])) < 1e-6


# === Alignment: actual hole centre == actual bolt axis centre, for both bolts ===
def test_bolt_axis_aligns_with_actual_hole_centres():
    result = _generate_ub_with_bolted_end_plate()

    hole_centers = _steel_hole_centers_xy(result)          # from the steel solid's own hole faces
    bolt_centers = sorted(_bolt_center_xy(b) for b in result.hardware)  # from the hardware solids

    assert len(hole_centers) == 2
    assert len(bolt_centers) == 2
    for (hx, hy), (bx, by) in zip(hole_centers, bolt_centers):
        assert abs(hx - bx) < 1e-6
        assert abs(hy - by) < 1e-6


# === Parameter sensitivity 1: changing hole spacing moves both the holes and the bolts ===
def test_hole_spacing_change_moves_holes_and_bolts_together():
    baseline = _generate_ub_with_bolted_end_plate()
    baseline_holes = _steel_hole_centers_xy(baseline)
    baseline_spacing = abs(baseline_holes[1][1] - baseline_holes[0][1])
    assert abs(baseline_spacing - 150.0) < 1e-6

    modified_connection = make_bolted_connection(bolts=[{**HOLES_SPEC, "vertical_spacing": 180.0}])
    modified = _generate_ub_with_bolted_end_plate(connection=modified_connection)
    modified_holes = _steel_hole_centers_xy(modified)
    modified_spacing = abs(modified_holes[1][1] - modified_holes[0][1])

    assert abs(modified_spacing - 180.0) < 1e-6
    assert abs(modified_spacing - baseline_spacing) > 1e-3  # holes actually moved

    modified_bolt_centers = sorted(_bolt_center_xy(b) for b in modified.hardware)
    for (hx, hy), (bx, by) in zip(modified_holes, modified_bolt_centers):
        assert abs(hx - bx) < 1e-6
        assert abs(hy - by) < 1e-6  # bolts followed the NEW hole positions, not the old ones


# === Parameter sensitivity 2: changing shaft diameter changes the bolt, not the steel holes ===
def test_bolt_shaft_diameter_change_does_not_affect_plate_holes():
    modified_connection = make_bolted_connection(physical_bolts={**BOLT_SPEC, "shaft_diameter": 14.0})
    result = _generate_ub_with_bolted_end_plate(connection=modified_connection)

    for face in _cylindrical_faces(result.solid):
        assert abs(_face_hole_diameter(face) - 18.0) < 1e-3  # steel holes: untouched

    for bolt in result.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 14.0) < 1e-3  # bolt shafts: actually changed


# === Validation: invalid bolt specs fail loudly, consistent with the existing error architecture ===
def test_bolt_zero_shaft_diameter_raises():
    connection = make_bolted_connection(physical_bolts={**BOLT_SPEC, "shaft_diameter": 0})
    with pytest.raises(GeometryValidationError, match="positive"):
        _generate_ub_with_bolted_end_plate(connection=connection)


def test_bolt_negative_shaft_length_raises():
    connection = make_bolted_connection(physical_bolts={**BOLT_SPEC, "shaft_length": -40.0})
    with pytest.raises(GeometryValidationError, match="positive"):
        _generate_ub_with_bolted_end_plate(connection=connection)


def test_bolt_zero_head_across_flats_raises():
    connection = make_bolted_connection(physical_bolts={**BOLT_SPEC, "head_across_flats": 0})
    with pytest.raises(GeometryValidationError, match="positive"):
        _generate_ub_with_bolted_end_plate(connection=connection)


def test_bolt_zero_head_thickness_raises():
    connection = make_bolted_connection(physical_bolts={**BOLT_SPEC, "head_thickness": 0})
    with pytest.raises(GeometryValidationError, match="positive"):
        _generate_ub_with_bolted_end_plate(connection=connection)


def test_bolt_head_smaller_than_shaft_raises():
    connection = make_bolted_connection(physical_bolts={**BOLT_SPEC, "head_across_flats": 10.0})
    with pytest.raises(GeometryValidationError, match="larger than its shaft"):
        _generate_ub_with_bolted_end_plate(connection=connection)


def test_bolt_missing_field_raises():
    connection = make_bolted_connection(physical_bolts={"shaft_diameter": 16.0})
    with pytest.raises(GeometryValidationError, match="missing required field"):
        _generate_ub_with_bolted_end_plate(connection=connection)


# === Full regression: every pre-6B fixture generates zero hardware and unaffected geometry ===
def test_full_regression_after_6b():
    pfc_result = generate_geometry(make_member())
    assert pfc_result.hardware == []

    ub_result = generate_geometry(make_ub_member())
    assert ub_result.hardware == []

    shs_result = generate_geometry(make_shs_member())
    assert shs_result.hardware == []

    rhs_result = generate_geometry(make_rhs_member())
    assert rhs_result.hardware == []

    shs_endplate_result = _generate_shs_with_end_plate()
    assert shs_endplate_result.hardware == []
    assert len(shs_endplate_result.solid.solids().vals()) == 1

    multi_conn_result = _generate_ub_with_both_end_plates()
    assert multi_conn_result.hardware == []
    assert multi_conn_result.connection_count == 2


# ===================================================================
# CAD Milestone 6C: connection identity & hardware ownership. Two
# connections on one member can now each carry their own physical
# hardware (6A + 6B combined) — this milestone makes "which connection
# owns this bolt?" an explicit, structural question rather than one
# answered by inferring list order. GeneratedMemberGeometry.connections
# is a list of GeneratedConnection, each carrying its own identity
# (connection_id/connection_type/position, reused straight from the
# ValidatedConnection that produced it) alongside the hardware THAT
# connection generated. `hardware` (the flat list from 6B) is kept
# byte-for-byte equivalent — same objects, same order — so nothing
# from 6B needs to change.
# ===================================================================

START_BOLT_SPEC = BOLT_SPEC  # shaft Ø16 x40, hex head 24 across flats x10 thick (Connection A, from 6B)
END_BOLT_SPEC = {"shaft_diameter": 20.0, "shaft_length": 50.0, "head_across_flats": 30.0, "head_thickness": 12.0}


def make_identified_start_connection(**overrides) -> ValidatedConnection:
    defaults = dict(connection_id="START-ENDPLATE-1", physical_bolts=dict(START_BOLT_SPEC))
    defaults.update(overrides)
    return make_start_connection(**defaults)


def make_identified_end_connection(**overrides) -> ValidatedConnection:
    defaults = dict(connection_id="END-ENDPLATE-1", physical_bolts=dict(END_BOLT_SPEC))
    defaults.update(overrides)
    return make_end_connection(**defaults)


def _generate_ub_two_connections_with_bolts(start_connection=None, end_connection=None):
    start = start_connection or make_identified_start_connection()
    end = end_connection or make_identified_end_connection()
    member = make_ub_member(connection_refs=[start.connection_id, end.connection_id])
    return generate_geometry(member, connections=[start, end])


def _connection_by_id(result, connection_id: str) -> GeneratedConnection:
    matches = [c for c in result.connections if c.connection_id == connection_id]
    assert len(matches) == 1, f"expected exactly one generated connection with id {connection_id!r}"
    return matches[0]


def _plate_hole_records(result, position: str) -> list:
    """
    (diameter, x, y) for each hole void on the named plate's own OUTWARD
    face (<Z for START, >Z for END) — the same face-selection approach
    6A's tests use, and for the same reason: two of this fixture's END
    holes geometrically overlap the UB web's footprint where the plate
    unions with the member, which splits the hole's cylindrical face
    deeper in the solid into several partial arcs. The plate's own flat
    outward face has no such interference (nothing but the plate
    contributes to it), so its innerWires() are always clean, single-wire
    circles — reusing _face_holes(), the same helper 6A already proved
    correct for this exact fixture.
    """
    face = result.solid.faces("<Z" if position == "START" else ">Z").val()
    return [(diameter, x, y) for diameter, (x, y) in _face_holes(face)]


# === Connection identity is reused from ValidatedConnection.connection_id, not invented ===
def test_generated_connections_reuse_existing_connection_identity():
    result = _generate_ub_two_connections_with_bolts()

    assert len(result.connections) == 2
    ids = sorted(c.connection_id for c in result.connections)
    assert ids == ["END-ENDPLATE-1", "START-ENDPLATE-1"]

    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")
    assert start.position == "START"
    assert end.position == "END"
    assert start.connection_type == "END_PLATE"
    assert end.connection_type == "END_PLATE"


# === Primary geometry test: steel side (plates, holes) unaffected by 6C's hardware association ===
def test_primary_assembly_steel_geometry():
    result = _generate_ub_two_connections_with_bolts()

    assert len(result.solid.solids().vals()) == 1

    bbox = result.solid.val().BoundingBox()
    start_thickness = 0.0 - bbox.zmin
    end_thickness = bbox.zmax - result.length_mm
    assert abs(start_thickness - 10.0) < 1e-6
    assert abs(end_thickness - 12.0) < 1e-6
    assert abs(result.length_mm - 4000.0) < 1e-6

    start_holes = _plate_hole_records(result, "START")
    end_holes = _plate_hole_records(result, "END")

    assert len(start_holes) == 2
    assert all(abs(d - 18.0) < 1e-3 for d, _x, _y in start_holes)
    assert len(end_holes) == 4
    assert all(abs(d - 20.0) < 1e-3 for d, _x, _y in end_holes)


# === Hardware ownership: START owns exactly 2 bolts, END owns exactly 4, proved via structure ===
def test_start_connection_owns_exactly_two_bolts():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")

    assert len(start.hardware) == 2


def test_end_connection_owns_exactly_four_bolts():
    result = _generate_ub_two_connections_with_bolts()
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert len(end.hardware) == 4


def test_total_hardware_count_is_six():
    result = _generate_ub_two_connections_with_bolts()

    assert len(result.hardware) == 6


# === Backward compatibility: the flat `hardware` list is exactly the concatenation
# of each GeneratedConnection's own hardware, same objects, same order ===
def test_flat_hardware_list_matches_per_connection_ownership():
    result = _generate_ub_two_connections_with_bolts()

    expected = [bolt for conn in result.connections for bolt in conn.hardware]
    assert len(result.hardware) == len(expected)
    assert all(a is b for a, b in zip(result.hardware, expected))


# === Every bolt remains real, separately-inspectable CadQuery geometry ===
def test_every_bolt_is_a_real_connected_solid_matching_its_own_connections_spec():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    for bolt in start.hardware:
        assert len(bolt.solids().vals()) == 1
        assert abs(_bolt_shaft_diameter(bolt) - 16.0) < 1e-3
        assert abs(_bolt_head_across_flats(bolt) - 24.0) < 1e-3
        assert abs(_bolt_shaft_face(bolt).BoundingBox().zlen - 40.0) < 1e-6
        assert abs(bolt.val().BoundingBox().zlen - 50.0) < 1e-6  # shaft_length + head_thickness

    for bolt in end.hardware:
        assert len(bolt.solids().vals()) == 1
        assert abs(_bolt_shaft_diameter(bolt) - 20.0) < 1e-3
        assert abs(_bolt_head_across_flats(bolt) - 30.0) < 1e-3
        assert abs(_bolt_shaft_face(bolt).BoundingBox().zlen - 50.0) < 1e-6
        assert abs(bolt.val().BoundingBox().zlen - 62.0) < 1e-6  # 50 + 12


# === Steel solid is topologically identical with and without physical hardware enabled ===
def test_steel_solid_unaffected_by_two_connection_hardware():
    without = _generate_ub_two_connections_with_bolts(
        start_connection=make_start_connection(connection_id="START-ENDPLATE-1"),
        end_connection=make_end_connection(connection_id="END-ENDPLATE-1"),
    )
    with_bolts = _generate_ub_two_connections_with_bolts()

    bbox_a = without.solid.val().BoundingBox()
    bbox_b = with_bolts.solid.val().BoundingBox()
    assert abs(bbox_a.xlen - bbox_b.xlen) < 1e-6
    assert abs(bbox_a.ylen - bbox_b.ylen) < 1e-6
    assert abs(bbox_a.zlen - bbox_b.zlen) < 1e-6

    assert abs(without.solid.val().Volume() - with_bolts.solid.val().Volume()) < 1e-3
    assert len(without.solid.val().Faces()) == len(with_bolts.solid.val().Faces())
    assert len(without.solid.solids().vals()) == 1
    assert len(with_bolts.solid.solids().vals()) == 1
    assert len(_cylindrical_faces(without.solid)) == len(_cylindrical_faces(with_bolts.solid)) == 6


# === Hole-to-bolt alignment, per connection, from actual generated geometry ===
def test_start_bolt_axes_align_with_actual_start_hole_centres():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")

    start_hole_xy = sorted((x, y) for _d, x, y in _plate_hole_records(result, "START"))
    start_bolt_xy = sorted(_bolt_center_xy(b) for b in start.hardware)

    assert len(start_hole_xy) == 2
    assert len(start_bolt_xy) == 2
    for (hx, hy), (bx, by) in zip(start_hole_xy, start_bolt_xy):
        assert abs(hx - bx) < 1e-6
        assert abs(hy - by) < 1e-6


def test_end_bolt_axes_align_with_actual_end_hole_centres():
    result = _generate_ub_two_connections_with_bolts()
    end = _connection_by_id(result, "END-ENDPLATE-1")

    end_hole_xy = sorted((x, y) for _d, x, y in _plate_hole_records(result, "END"))
    end_bolt_xy = sorted(_bolt_center_xy(b) for b in end.hardware)

    assert len(end_hole_xy) == 4
    assert len(end_bolt_xy) == 4
    for (hx, hy), (bx, by) in zip(end_hole_xy, end_bolt_xy):
        assert abs(hx - bx) < 1e-6
        assert abs(hy - by) < 1e-6


# === Connection-specific hardware parameters do not leak between connections ===
def test_start_and_end_hardware_specs_do_not_leak():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert len(start.hardware) == 2
    for bolt in start.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 16.0) < 1e-3
        assert abs(_bolt_head_across_flats(bolt) - 24.0) < 1e-3

    assert len(end.hardware) == 4
    for bolt in end.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 20.0) < 1e-3
        assert abs(_bolt_head_across_flats(bolt) - 30.0) < 1e-3


# === Independence 1: changing ONLY START's bolt spec leaves END's hardware untouched ===
def test_changing_start_hardware_does_not_affect_end_hardware():
    modified_start = make_identified_start_connection(physical_bolts={**START_BOLT_SPEC, "shaft_diameter": 14.0})
    result = _generate_ub_two_connections_with_bolts(start_connection=modified_start)

    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    for bolt in start.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 14.0) < 1e-3  # START: actually changed

    assert len(end.hardware) == 4  # END: count unaffected
    for bolt in end.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 20.0) < 1e-3       # END: shaft unaffected
        assert abs(_bolt_head_across_flats(bolt) - 30.0) < 1e-3    # END: head unaffected


# === Independence 2 (reverse direction): changing ONLY END's bolt spec leaves START untouched ===
def test_changing_end_hardware_does_not_affect_start_hardware():
    modified_end = make_identified_end_connection(physical_bolts={**END_BOLT_SPEC, "shaft_diameter": 18.0})
    result = _generate_ub_two_connections_with_bolts(end_connection=modified_end)

    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    for bolt in end.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 18.0) < 1e-3  # END: actually changed

    assert len(start.hardware) == 2  # START: otherwise unchanged
    for bolt in start.hardware:
        assert abs(_bolt_shaft_diameter(bolt) - 16.0) < 1e-3
        assert abs(_bolt_head_across_flats(bolt) - 24.0) < 1e-3


# === Validation: duplicate connection_id among supplied connections is rejected ===
def test_duplicate_connection_id_among_supplied_connections_raises():
    member = make_ub_member(connection_refs=["DUP-1"])
    conn_a = make_start_connection(connection_id="DUP-1")
    conn_b = make_end_connection(connection_id="DUP-1")  # same id, different data -> ambiguous

    with pytest.raises(GeometryValidationError, match="share connection_id"):
        generate_geometry(member, connections=[conn_a, conn_b])


# === Mutable-default regression: separate generate_geometry() calls never share list state ===
def test_hardware_and_connections_defaults_are_not_shared_between_results():
    result_a = generate_geometry(make_member())
    result_b = generate_geometry(make_member())

    assert result_a.hardware is not result_b.hardware
    assert result_a.connections is not result_b.connections

    result_a.hardware.append("sentinel")
    result_a.connections.append("sentinel")
    assert result_b.hardware == []
    assert result_b.connections == []


def test_generated_connection_hardware_default_not_shared():
    gc_a = GeneratedConnection(connection_id="a", connection_type="END_PLATE", position="END")
    gc_b = GeneratedConnection(connection_id="b", connection_type="END_PLATE", position="END")

    assert gc_a.hardware is not gc_b.hardware
    gc_a.hardware.append("sentinel")
    assert gc_b.hardware == []


# === Regression: single-connection (6B) UB assembly gets exactly one GeneratedConnection ===
def test_single_connection_regression_has_one_generated_connection():
    result = _generate_ub_with_bolted_end_plate()

    assert len(result.connections) == 1
    conn = result.connections[0]
    assert conn.connection_id == "conn-1"
    assert conn.position == "END"
    assert len(conn.hardware) == 2
    assert result.hardware == conn.hardware


# === Regression: SHS end-plate connection (no physical_bolts) still gets a GeneratedConnection ===
def test_shs_end_plate_regression_has_generated_connection_with_no_hardware():
    result = _generate_shs_with_end_plate()

    assert len(result.connections) == 1
    assert result.connections[0].hardware == []
    assert result.hardware == []


# === Full regression after 6C: connections list stays consistent with connection_count everywhere ===
def test_full_regression_after_6c():
    pfc_result = generate_geometry(make_member())
    assert pfc_result.connections == []

    ub_result = generate_geometry(make_ub_member())
    assert ub_result.connections == []

    shs_result = generate_geometry(make_shs_member())
    assert shs_result.connections == []

    rhs_result = generate_geometry(make_rhs_member())
    assert rhs_result.connections == []

    multi_conn_result = _generate_ub_with_both_end_plates()  # 6A fixture, no physical bolts
    assert len(multi_conn_result.connections) == 2
    assert multi_conn_result.connection_count == 2
    assert multi_conn_result.connection_types == ["END_PLATE", "END_PLATE"]
    assert all(c.hardware == [] for c in multi_conn_result.connections)

    shs2_result = _generate_shs2_with_end_plate()
    assert len(shs2_result.connections) == 1


# ===================================================================
# CAD Milestone 6D: a small, stable geometry contract per
# GeneratedConnection. `plate` is the actual positioned CadQuery plate
# solid connections.attach_end_plate() built (never a duplicate
# rebuilt for inspection); `holes` are GeneratedHole records measured
# from that plate's own real topology (never copied from the hole
# spec), each paired with the physical bolt that passes through it;
# `geometry_bounds` is a property computed fresh from `plate`, never a
# stored/duplicated bounding box. These tests specifically avoid
# asserting fixture values directly — they extract bounds/centres from
# the generated contract and, where practical, cross-check them
# against an independently-computed reading of the same real geometry.
# ===================================================================

def _independent_plate_hole_records(plate_wp):
    """
    (diameter, (x, y)) read directly off the plate Workplane's own
    face topology, using the same technique _face_holes() already uses
    elsewhere in this file — independent of connections.py's own
    _ordered_plate_hole_records(), so this is a genuine cross-check of
    the contract's `holes`, not a re-assertion of the same code path.
    """
    return _face_holes(plate_wp.faces("<Z").val())


# === Plate geometry: present, and its bounds are the ACTUAL generated plate, not fixture echo ===
def test_start_and_end_plate_geometry_bounds_are_connection_specific():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert start.plate is not None
    assert end.plate is not None

    start_bounds = start.geometry_bounds
    end_bounds = end.geometry_bounds

    # Each connection's bounds are its OWN plate — not the 4022mm full assembly.
    assert abs(start_bounds.xlen - 90.0) < 1e-6
    assert abs(start_bounds.ylen - 250.0) < 1e-6
    assert abs(start_bounds.zlen - 10.0) < 1e-6

    assert abs(end_bounds.xlen - 90.0) < 1e-6
    assert abs(end_bounds.ylen - 250.0) < 1e-6
    assert abs(end_bounds.zlen - 12.0) < 1e-6

    full_member_bbox = result.solid.val().BoundingBox()
    assert abs(start_bounds.zlen - full_member_bbox.zlen) > 1.0
    assert abs(end_bounds.zlen - full_member_bbox.zlen) > 1.0


# === geometry_bounds reflects the connection's actual generated location relative to the member ===
def test_geometry_bounds_reflect_actual_connection_location():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    # START: plate extends backward from Z=0 (physical geometry beyond the nominal member end).
    assert abs(start.geometry_bounds.zmax - 0.0) < 1e-6
    assert abs(start.geometry_bounds.zmin - (-10.0)) < 1e-6

    # END: plate extends forward from Z=length_mm.
    assert abs(end.geometry_bounds.zmin - result.length_mm) < 1e-6
    assert abs(end.geometry_bounds.zmax - (result.length_mm + 12.0)) < 1e-6

    # length_mm remains authoritative and untouched by either connection's own bounds.
    assert abs(result.length_mm - 4000.0) < 1e-6


# === Orientation: outward_normal matches position, derived from the same value connections.py uses ===
def test_outward_normal_matches_position():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert start.outward_normal == (0.0, 0.0, -1.0)
    assert end.outward_normal == (0.0, 0.0, 1.0)


# === Holes: correct count/diameter/centres, cross-checked against an independent geometry read ===
def test_start_holes_exposed_with_actual_measured_geometry():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")

    assert len(start.holes) == 2
    independent = sorted(_independent_plate_hole_records(start.plate))
    contract_centers = sorted((h.diameter, (h.center[0], h.center[1])) for h in start.holes)

    for (i_dia, i_xy), (c_dia, c_xy) in zip(independent, contract_centers):
        assert abs(i_dia - c_dia) < 1e-6
        assert abs(i_xy[0] - c_xy[0]) < 1e-6
        assert abs(i_xy[1] - c_xy[1]) < 1e-6
        assert abs(c_dia - 18.0) < 1e-3


def test_end_holes_exposed_with_actual_measured_geometry():
    result = _generate_ub_two_connections_with_bolts()
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert len(end.holes) == 4
    independent = sorted(_independent_plate_hole_records(end.plate))
    contract_centers = sorted((h.diameter, (h.center[0], h.center[1])) for h in end.holes)

    for (i_dia, i_xy), (c_dia, c_xy) in zip(independent, contract_centers):
        assert abs(i_dia - c_dia) < 1e-6
        assert abs(i_xy[0] - c_xy[0]) < 1e-6
        assert abs(i_xy[1] - c_xy[1]) < 1e-6
        assert abs(c_dia - 20.0) < 1e-3


# === Hole identity is stable and local to its own parent connection ===
def test_hole_ids_are_stable_and_local_to_parent_connection():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert [h.hole_id for h in start.holes] == ["hole-1", "hole-2"]
    assert [h.hole_id for h in end.holes] == ["hole-1", "hole-2", "hole-3", "hole-4"]  # local, not globally unique


# === Hardware-to-hole association: each hole's own bolt is real geometry, correctly paired ===
def test_start_hole_bolt_association_is_real_and_correctly_paired():
    result = _generate_ub_two_connections_with_bolts()
    start = _connection_by_id(result, "START-ENDPLATE-1")

    assert len(start.holes) == 2
    for hole in start.holes:
        assert hole.bolt is not None
        assert len(hole.bolt.solids().vals()) == 1
        assert abs(_bolt_shaft_diameter(hole.bolt) - 16.0) < 1e-3

        bolt_xy = _bolt_center_xy(hole.bolt)
        assert abs(bolt_xy[0] - hole.center[0]) < 1e-6
        assert abs(bolt_xy[1] - hole.center[1]) < 1e-6


def test_end_hole_bolt_association_is_real_and_correctly_paired():
    result = _generate_ub_two_connections_with_bolts()
    end = _connection_by_id(result, "END-ENDPLATE-1")

    assert len(end.holes) == 4
    for hole in end.holes:
        assert hole.bolt is not None
        assert len(hole.bolt.solids().vals()) == 1
        assert abs(_bolt_shaft_diameter(hole.bolt) - 20.0) < 1e-3

        bolt_xy = _bolt_center_xy(hole.bolt)
        assert abs(bolt_xy[0] - hole.center[0]) < 1e-6
        assert abs(bolt_xy[1] - hole.center[1]) < 1e-6


# === Without physical_bolts, holes are still exposed but carry no bolt ===
def test_holes_without_physical_bolts_have_no_bolt_association():
    connection = make_start_connection(connection_id="START-ENDPLATE-1")  # no physical_bolts
    member = make_ub_member(connection_refs=["START-ENDPLATE-1"])
    result = generate_geometry(member, connections=[connection])

    start = _connection_by_id(result, "START-ENDPLATE-1")
    assert len(start.holes) == 2
    assert all(h.bolt is None for h in start.holes)
    assert start.hardware == []


# === Parameter sensitivity 1: changing the plate dimensions changes the CONTRACT's bounds ===
def test_plate_dimension_change_updates_generated_connection_bounds():
    modified_start = make_identified_start_connection(
        plates=[{"width": 100.0, "height": 250.0, "thickness": 15.0}]
    )
    result = _generate_ub_two_connections_with_bolts(start_connection=modified_start)
    start = _connection_by_id(result, "START-ENDPLATE-1")

    bounds = start.geometry_bounds
    assert abs(bounds.xlen - 100.0) < 1e-6
    assert abs(bounds.ylen - 250.0) < 1e-6
    assert abs(bounds.zlen - 15.0) < 1e-6


# === Parameter sensitivity 2: changing hole spacing changes the CONTRACT's exposed hole centres ===
def test_hole_spacing_change_updates_generated_hole_centres():
    baseline = _generate_ub_two_connections_with_bolts()
    baseline_start = _connection_by_id(baseline, "START-ENDPLATE-1")
    baseline_ys = sorted(h.center[1] for h in baseline_start.holes)
    assert abs((baseline_ys[1] - baseline_ys[0]) - 150.0) < 1e-6

    modified_start = make_identified_start_connection(bolts=[{**START_HOLES_SPEC, "vertical_spacing": 180.0}])
    result = _generate_ub_two_connections_with_bolts(start_connection=modified_start)
    start = _connection_by_id(result, "START-ENDPLATE-1")

    modified_ys = sorted(h.center[1] for h in start.holes)
    assert abs((modified_ys[1] - modified_ys[0]) - 180.0) < 1e-6


# === Hardware regression: bolt spec change is isolated, and never confused with hole geometry ===
def test_bolt_diameter_change_does_not_alter_hole_geometry_or_other_connection():
    modified_start = make_identified_start_connection(physical_bolts={**START_BOLT_SPEC, "shaft_diameter": 14.0})
    result = _generate_ub_two_connections_with_bolts(start_connection=modified_start)
    start = _connection_by_id(result, "START-ENDPLATE-1")
    end = _connection_by_id(result, "END-ENDPLATE-1")

    for hole in start.holes:
        assert abs(hole.diameter - 18.0) < 1e-3  # hole geometry: untouched by the bolt spec change
        assert abs(_bolt_shaft_diameter(hole.bolt) - 14.0) < 1e-3  # bolt geometry: actually changed

    for hole in end.holes:
        assert abs(hole.diameter - 20.0) < 1e-3
        assert abs(_bolt_shaft_diameter(hole.bolt) - 20.0) < 1e-3  # END hardware: unaffected


# === Steel solid regression: the geometry contract does not create a second competing steel model ===
def test_steel_solid_unaffected_by_geometry_contract():
    without_bolts = _generate_ub_two_connections_with_bolts(
        start_connection=make_start_connection(connection_id="START-ENDPLATE-1"),
        end_connection=make_end_connection(connection_id="END-ENDPLATE-1"),
    )
    with_bolts = _generate_ub_two_connections_with_bolts()

    bbox_a = without_bolts.solid.val().BoundingBox()
    bbox_b = with_bolts.solid.val().BoundingBox()
    assert abs(bbox_a.xlen - bbox_b.xlen) < 1e-6
    assert abs(bbox_a.ylen - bbox_b.ylen) < 1e-6
    assert abs(bbox_a.zlen - bbox_b.zlen) < 1e-6
    assert abs(without_bolts.solid.val().Volume() - with_bolts.solid.val().Volume()) < 1e-3
    assert len(without_bolts.solid.val().Faces()) == len(with_bolts.solid.val().Faces())
    assert len(without_bolts.solid.solids().vals()) == 1
    assert len(with_bolts.solid.solids().vals()) == 1


# === Single-connection regression: the geometry contract works for the original 6B single-plate case ===
def test_single_connection_geometry_contract_regression():
    result = _generate_ub_with_bolted_end_plate()

    assert len(result.connections) == 1
    conn = result.connections[0]
    assert len(conn.holes) == 2
    assert len(conn.hardware) == 2
    assert all(h.bolt is not None for h in conn.holes)

    bounds = conn.geometry_bounds
    assert abs(bounds.xlen - 90.0) < 1e-6
    assert abs(bounds.ylen - 250.0) < 1e-6
    assert abs(bounds.zlen - 10.0) < 1e-6


# === SHS/RHS regression: the geometry contract is family-agnostic, no special-casing required ===
def test_shs_end_plate_geometry_contract_regression():
    result = _generate_shs_with_end_plate()

    assert len(result.connections) == 1
    conn = result.connections[0]
    assert conn.plate is not None
    assert len(conn.holes) == 4
    bounds = conn.geometry_bounds
    assert abs(bounds.zlen - 10.0) < 1e-6  # 150x150x10 end plate, from the established 5A fixture


def test_rhs_end_plate_geometry_contract_regression():
    result = _generate_shs2_with_end_plate()

    assert len(result.connections) == 1
    conn = result.connections[0]
    assert conn.plate is not None
    assert len(conn.holes) == 4


# === Full regression after 6D ===
def test_full_regression_after_6d():
    pfc_result = generate_geometry(make_member())
    assert pfc_result.connections == []

    ub_result = generate_geometry(make_ub_member())
    assert ub_result.connections == []

    multi_conn_result = _generate_ub_with_both_end_plates()  # 6A fixture, no physical bolts
    assert len(multi_conn_result.connections) == 2
    for conn in multi_conn_result.connections:
        assert conn.plate is not None
        assert all(h.bolt is None for h in conn.holes)
    start_conn = [c for c in multi_conn_result.connections if c.position == "START"][0]
    end_conn = [c for c in multi_conn_result.connections if c.position == "END"][0]
    assert len(start_conn.holes) == 2
    assert len(end_conn.holes) == 4
