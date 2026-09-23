"""
Milestone 7I — proving the smallest possible spatial assembly
capability: two independently validated real members, each with its
own local-frame CAD solid (via the unmodified 7A adapter and
generate_geometry()), explicitly placed into one shared project
coordinate system without losing identity, geometry, or independence.

    REAL MEMBER A -> 7A -> ValidatedMember A -> Local CAD A
        -> place_member_geometry() -> Project CAD A

    REAL MEMBER B -> 7A -> ValidatedMember B -> Local CAD B
        -> place_member_geometry() -> Project CAD B

    Project CAD A + Project CAD B -> GeneratedAssembly
        -> 2 independently identifiable solids

7I does NOT prove that SteelSpec can determine member placement from a
real drawing. It proves only that once placement is explicitly
known/reviewed, the CAD architecture can represent multiple members in
one shared coordinate system without losing member identity or
geometry.

EXPLICITLY NOT CLAIMED (Step 14) — none of the following is
implemented or tested anywhere in this file or in
app/cad_engine/placement.py / app/cad_engine/assembly.py:
  - automatic drawing-based member positioning;
  - grid-to-coordinate conversion;
  - arbitrary 3D orientation inference (this file exercises exactly
    one explicit rotation, per Step 9's own instruction not to attempt
    Euler-angle composition yet — multi-axis rotation ORDER is
    whatever cq.Location's own gp_Trsf convention does, and is not
    independently verified here beyond the single-axis case);
  - clash/intersection detection between members;
  - connection placement or connection geometry generation between
    two members (see test_connection_metadata_remains_identity_based_
    not_used_for_placement below, and
    test_placing_a_member_with_a_resolved_connection_is_rejected,
    which proves the placement layer actively refuses a member that
    already carries connection geometry rather than silently
    mishandling it);
  - structural analysis;
  - fabrication readiness.

FIXTURE PROVENANCE: Member A and Member B are the exact same two real
members established in Milestone 7G
(tests/test_real_multi_member_cad.py) — Member A is the established
primary fixture (REAL-UB-CAD-001, 310UB40, 4000mm); Member B's
mark/section come from the real Arkles Strand extraction row for "L2"
(250PFC, page 14), with its length remaining explicitly HUMAN-REVIEWED/
SUPPLEMENTED (3000mm — no Arkles row carries a length). The connection
record reused for the identity-only test is 7H's exact reviewed
multi-member fixture. All numeric PLACEMENT values in this file (x=500,
z=1000, rotation_z=90, etc.) are SYNTHETIC TEST COORDINATES chosen only
to exercise the transform arithmetic — they are not derived from
Arkles, from any real drawing, or from any grid reference, and must
never be read as real project placement data.
"""
import pytest

from app.cad_engine.assembly import GeneratedAssembly, PlacedMember
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import MemberPlacement, place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from tests.test_real_connection_adapter import make_7e_connection_record
from tests.test_real_multi_member_cad import (
    FULL_PFC_SECTION,
    FULL_UB_SECTION,
    make_member_a_row,
    make_member_b_row,
    make_multi_member_matcher,
)
from tests.test_real_multi_member_connection import make_multi_member_connection_record


def _build(row: dict, matcher) -> tuple:
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    return validated, geometry


# === Step 4/6: local geometry, before any placement is applied ===
def test_local_geometry_before_placement():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)

    bbox_a = geometry_a.solid.val().BoundingBox()
    bbox_b = geometry_b.solid.val().BoundingBox()

    assert abs(bbox_a.xmin - 0.0) < 1e-6
    assert abs(bbox_a.xmax - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs(bbox_a.ymax - FULL_UB_SECTION["depth"]) < 1e-6
    assert abs(bbox_a.zmax - 4000.0) < 1e-6

    assert abs(bbox_b.xmin - 0.0) < 1e-6
    assert abs(bbox_b.xmax - FULL_PFC_SECTION["flange_width"]) < 1e-6
    assert abs(bbox_b.ymax - FULL_PFC_SECTION["depth"]) < 1e-6
    assert abs(bbox_b.zmax - 3000.0) < 1e-6

    assert geometry_a.connection_count == 0
    assert geometry_b.connection_count == 0


# === Step 6/7: translation changes location, never geometry ===
def test_translation_changes_location_not_geometry():
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)
    bbox_before = geometry_b.solid.val().BoundingBox()
    volume_before = geometry_b.solid.val().Volume()

    placed = place_member_geometry(geometry_b, MemberPlacement(x=500.0, y=0.0, z=0.0))
    bbox_after = placed.solid.val().BoundingBox()

    assert abs((bbox_after.xmin - bbox_before.xmin) - 500.0) < 1e-6
    assert abs((bbox_after.xmax - bbox_before.xmax) - 500.0) < 1e-6
    assert abs(bbox_after.ymin - bbox_before.ymin) < 1e-6
    assert abs(bbox_after.zmin - bbox_before.zmin) < 1e-6

    # Dimensions (X/Y/Z extents) and volume are exactly preserved — a
    # pure translation is a rigid transform, never a reshape.
    assert abs((bbox_after.xmax - bbox_after.xmin) - (bbox_before.xmax - bbox_before.xmin)) < 1e-6
    assert abs((bbox_after.ymax - bbox_after.ymin) - (bbox_before.ymax - bbox_before.ymin)) < 1e-6
    assert abs((bbox_after.zmax - bbox_after.zmin) - (bbox_before.zmax - bbox_before.zmin)) < 1e-6
    assert abs(placed.solid.val().Volume() - volume_before) < 1e-6
    assert len(placed.solid.solids().vals()) == 1

    # Identity/section/length metadata is carried through unchanged.
    assert placed.mark == geometry_b.mark == "L2"
    assert placed.section_name == geometry_b.section_name == "250PFC"
    assert placed.length_mm == geometry_b.length_mm == 3000.0


# === Step 8: Z placement ===
def test_z_placement_moves_exactly_1000mm():
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)
    bbox_before = geometry_b.solid.val().BoundingBox()
    volume_before = geometry_b.solid.val().Volume()

    placed = place_member_geometry(geometry_b, MemberPlacement(z=1000.0))
    bbox_after = placed.solid.val().BoundingBox()

    assert abs((bbox_after.zmin - bbox_before.zmin) - 1000.0) < 1e-6
    assert abs((bbox_after.zmax - bbox_before.zmax) - 1000.0) < 1e-6
    assert abs(bbox_after.xmin - bbox_before.xmin) < 1e-6
    assert abs(bbox_after.ymin - bbox_before.ymin) < 1e-6

    assert abs((bbox_after.xmax - bbox_after.xmin) - (bbox_before.xmax - bbox_before.xmin)) < 1e-6
    assert abs((bbox_after.ymax - bbox_after.ymin) - (bbox_before.ymax - bbox_before.ymin)) < 1e-6
    assert abs((bbox_after.zmax - bbox_after.zmin) - (bbox_before.zmax - bbox_before.zmin)) < 1e-6
    assert abs(placed.solid.val().Volume() - volume_before) < 1e-6


# === Step 9: one rotation, proven against actual CAD geometry ===
def test_rotation_z_90_degrees_swaps_xy_extent():
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)
    bbox_before = geometry_b.solid.val().BoundingBox()
    volume_before = geometry_b.solid.val().Volume()

    placed = place_member_geometry(geometry_b, MemberPlacement(rotation_z=90.0))
    bbox_after = placed.solid.val().BoundingBox()

    x_extent_before = bbox_before.xmax - bbox_before.xmin
    y_extent_before = bbox_before.ymax - bbox_before.ymin
    x_extent_after = bbox_after.xmax - bbox_after.xmin
    y_extent_after = bbox_after.ymax - bbox_after.ymin

    # A genuine 90 degree rotation about Z swaps the local footprint's
    # X/Y extents (90mm <-> 250mm) — the bounding box shape itself
    # changes, not merely its position. Never assume X/Y stay identical
    # after a rotation.
    assert abs(x_extent_after - y_extent_before) < 1e-6
    assert abs(y_extent_after - x_extent_before) < 1e-6
    assert abs(x_extent_after - FULL_PFC_SECTION["depth"]) < 1e-6
    assert abs(y_extent_after - FULL_PFC_SECTION["flange_width"]) < 1e-6

    # Z (member length) is unaffected by a Z-axis rotation.
    assert abs((bbox_after.zmax - bbox_after.zmin) - (bbox_before.zmax - bbox_before.zmin)) < 1e-6

    # Rigid transform: volume and topology (solid count) unchanged.
    assert abs(placed.solid.val().Volume() - volume_before) < 1e-6
    assert len(placed.solid.solids().vals()) == 1


# === Step 10: two-member independence — the spatial equivalent of 7G's
# cross-member contamination test ===
def test_two_member_independence_changing_b_does_not_affect_a():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)

    placed_a = place_member_geometry(geometry_a, MemberPlacement(x=0, y=0, z=0))
    bbox_a_before = placed_a.solid.val().BoundingBox()
    volume_a_before = placed_a.solid.val().Volume()

    placed_b_1 = place_member_geometry(geometry_b, MemberPlacement(x=500, y=0, z=0))
    placed_b_2 = place_member_geometry(geometry_b, MemberPlacement(x=500, y=0, z=1000, rotation_z=90))
    bbox_b1 = placed_b_1.solid.val().BoundingBox()
    bbox_b2 = placed_b_2.solid.val().BoundingBox()
    assert abs(bbox_b1.zmin - bbox_b2.zmin) > 1.0  # genuinely different placements

    # Member A, re-measured after multiple Member B placements.
    bbox_a_after = placed_a.solid.val().BoundingBox()
    assert abs(bbox_a_after.xmin - bbox_a_before.xmin) < 1e-6
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(placed_a.solid.val().Volume() - volume_a_before) < 1e-6
    assert len(placed_a.solid.solids().vals()) == 1

    # A's ORIGINAL (unplaced) geometry object was never mutated either.
    bbox_a_original = geometry_a.solid.val().BoundingBox()
    assert abs(bbox_a_original.xmin - 0.0) < 1e-6
    assert abs(bbox_a_original.xmax - FULL_UB_SECTION["flange_width"]) < 1e-6


# === Step 11/12: assembly container — 2 independently identifiable solids ===
def test_assembly_holds_two_independently_identifiable_solids():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)

    placement_a = MemberPlacement(x=0, y=0, z=0)
    placement_b = MemberPlacement(x=500, y=0, z=0)
    placed_a = place_member_geometry(geometry_a, placement_a)
    placed_b = place_member_geometry(geometry_b, placement_b)

    assembly = GeneratedAssembly(members=[
        PlacedMember(mark=placed_a.mark, geometry=placed_a, placement=placement_a),
        PlacedMember(mark=placed_b.mark, geometry=placed_b, placement=placement_b),
    ])

    assert len(assembly.members) == 2
    assert {m.mark for m in assembly.members} == {"REAL-UB-CAD-001", "L2"}

    for member in assembly.members:
        assert len(member.geometry.solid.solids().vals()) == 1  # each remains one connected solid

    # Never fused — each member's solid is its own independent object.
    solid_a, solid_b = assembly.members[0].geometry.solid, assembly.members[1].geometry.solid
    assert solid_a is not solid_b
    assert solid_a.val() is not solid_b.val()

    bbox_a = solid_a.val().BoundingBox()
    bbox_b = solid_b.val().BoundingBox()
    assert abs(bbox_a.xmin - 0.0) < 1e-6
    assert abs(bbox_b.xmin - 500.0) < 1e-6


# === Step 13: connection association is identity metadata only, never
# used to derive or influence placement ===
def test_connection_metadata_remains_identity_based_not_used_for_placement():
    connection_record = make_multi_member_connection_record()
    validated_connection = real_connection_to_validated_connection(connection_record)
    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2"}

    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)  # connection_refs never set
    _, geometry_b = _build(make_member_b_row(), matcher)  # connection_refs never set
    assert geometry_a.connection_count == 0
    assert geometry_b.connection_count == 0

    # Placements below are explicit synthetic test coordinates, chosen
    # with no reference to validated_connection at all — position,
    # plate dimensions, and hole geometry never enter this function.
    placed_a = place_member_geometry(geometry_a, MemberPlacement(x=0, y=0, z=0))
    placed_b = place_member_geometry(geometry_b, MemberPlacement(x=500, y=0, z=0))
    assert placed_a.connection_count == 0
    assert placed_b.connection_count == 0


# === Step 13/14 boundary, enforced not just documented: a member with
# a resolved connection cannot go through this milestone's placement path ===
def test_placing_a_member_with_a_resolved_connection_is_rejected():
    matcher = make_multi_member_matcher()
    row = make_member_a_row()
    validated_member = real_member_to_validated_member(row, matcher)

    connection_record = make_7e_connection_record()  # single-member reviewed connection
    validated_connection = real_connection_to_validated_connection(connection_record)
    validated_member.connection_refs = [validated_connection.connection_id]

    geometry = generate_geometry(validated_member, connections=[validated_connection])
    assert geometry.connection_count == 1

    with pytest.raises(GeometryValidationError, match="resolved connection"):
        place_member_geometry(geometry, MemberPlacement(x=0, y=0, z=0))


# === Step 15: invalid placement values are rejected ===
_INVALID_PLACEMENT_CASES = [
    ("non_numeric_x", {"x": "500"}),
    ("boolean_x", {"x": True}),
    ("non_numeric_rotation_z", {"rotation_z": "90"}),
    ("boolean_rotation_z", {"rotation_z": False}),
    ("nan_x", {"x": float("nan")}),
    ("infinite_x", {"x": float("inf")}),
    ("infinite_rotation_z", {"rotation_z": float("-inf")}),
]


@pytest.mark.parametrize(
    "case_id,overrides", _INVALID_PLACEMENT_CASES, ids=[c[0] for c in _INVALID_PLACEMENT_CASES],
)
def test_invalid_placement_values_are_rejected(case_id, overrides):
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)

    base = {"x": 0.0, "y": 0.0, "z": 0.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    base.update(overrides)
    placement = MemberPlacement(**base)

    with pytest.raises(GeometryValidationError):
        place_member_geometry(geometry_b, placement)


# === Step 16: source independence — placement never needs the raw row ===
def test_source_independence_placement_never_needs_raw_row():
    matcher = make_multi_member_matcher()
    row_b = make_member_b_row()
    validated_b = real_member_to_validated_member(row_b, matcher)
    geometry_b = generate_geometry(validated_b)

    row_b.clear()

    placed = place_member_geometry(geometry_b, MemberPlacement(x=500, y=0, z=0))
    bbox = placed.solid.val().BoundingBox()
    assert abs(bbox.xmin - 500.0) < 1e-6
    assert placed.mark == "L2"


# === place_member_geometry() never mutates its input ===
def test_place_member_geometry_does_not_mutate_original():
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)
    original_bbox = geometry_b.solid.val().BoundingBox()

    placed = place_member_geometry(geometry_b, MemberPlacement(x=500, y=0, z=0))

    assert placed is not geometry_b
    assert placed.solid is not geometry_b.solid
    after_bbox = geometry_b.solid.val().BoundingBox()
    assert abs(after_bbox.xmin - original_bbox.xmin) < 1e-6
    assert abs(after_bbox.xmax - original_bbox.xmax) < 1e-6
