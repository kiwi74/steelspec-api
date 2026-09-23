"""
Milestone 7K — proving that a reviewed connection's geometry can be
represented in project space between two already-positioned real
members, without moving either member or the connection to make it
"work":

    REAL MEMBER A -7A-> ValidatedMember A -> local CAD -7I-> project CAD A
    REAL MEMBER B -7A-> ValidatedMember B -> local CAD -7I-> project CAD B
    HUMAN-REVIEWED CONNECTION -7D/7H-> ValidatedConnection
        -7J-> ConnectionLocation
        -> build_two_member_connection_assembly() -> GeneratedConnectionAssembly

7K proves only: given two explicitly positioned members and an
explicitly reviewed connection location, SteelSpec can begin
constructing and validating the connection geometry in project space
without silently moving or guessing anything. It does NOT prove real
drawing-to-3D connection reconstruction, automatic connection
placement, automatic member positioning, clash resolution, structural
adequacy, engineering compliance, fabrication readiness, or general
steel connection support.

FIXTURE PROVENANCE — TEST / HUMAN-REVIEWED / SUPPLEMENTED throughout:
  - Member A reuses 7G's established fixture (REAL-UB-CAD-001,
    310UB40, 4000mm) via make_member_a_row(), placed at
    MemberPlacement(x=0, y=0, z=0) — a deliberately simple test
    arrangement, not a real coordinate.
  - Member B reuses 7G's established fixture (L2, 250PFC, page-14
    provenance, HUMAN-REVIEWED/SUPPLEMENTED length) via
    make_member_b_row(), placed at MemberPlacement(x=0, y=0, z=4000) —
    a deliberately chosen test placement continuing Member A's run
    end-to-end, again explicit test data, never calculated from
    section/length/connection data.
  - The connection reuses 7H's exact reviewed plate/hole fixture
    (180x250x12mm end plate, 4xO22mm holes, 90mm H / 140mm V spacing)
    via make_multi_member_connection_record() — unchanged, per Step 4.
  - The ConnectionLocation used here (x=0, y=0, z=3994, rotations=0)
    is a FRESH test value, not 7J's own earlier example (x=500, z=4000)
    — 7J's value was chosen purely to exercise metadata-preservation
    and was never checked against any real member geometry. Reusing it
    literally here would place the plate at project X in [500, 680],
    which does not overlap either member's own footprint (both sit at
    X in [0, ~165]) and would never touch anything. This module's own
    location value was instead verified empirically, in a throwaway
    sandbox script, to genuinely contact both members at their
    Z=4000 interface (plate Z spans [3994, 4006] — 6mm into each
    member) BEFORE being used in any test below — the same way a human
    reviewer would check a real drawing before recording a number, and
    still never computed at runtime by any production code in this
    milestone. The production builder itself
    (build_two_member_connection_assembly()) never calculates a
    location — it only VALIDATES that an explicitly supplied one
    genuinely produces contact, and rejects it otherwise.
"""
import pytest

from app.cad_engine.assembly import GeneratedAssembly, PlacedMember
from app.cad_engine.connection_location import ConnectionLocation, real_connection_location_to_connection_location
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import MemberPlacement, place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.two_member_connection import GeneratedConnectionAssembly, build_two_member_connection_assembly
from tests.test_real_multi_member_cad import (
    FULL_PFC_SECTION,
    make_member_a_row,
    make_member_b_row,
    make_multi_member_matcher,
)
from tests.test_real_multi_member_connection import make_multi_member_connection_record

# ============================================================================
# Milestone 7L — strengthening 7K with an explicit, non-zero MEMBER
# orientation (Member B rotated about its own local Z axis), proving
# MemberPlacement and ConnectionLocation remain genuinely independent
# coordinate contracts once a member is no longer axis-aligned with
# the connection's own frame.
#
# FIXTURE PROVENANCE (TEST / HUMAN-REVIEWED / SUPPLEMENTED): Member B's
# 7L placement (x=250, y=0, z=4000, rotation_z=90) reuses the SAME
# Z=4000 end-to-end interface as 7K, but adds a 90-degree rotation
# about Member B's own local Z axis plus an X translation. Both the
# rotation angle and the X offset were chosen by inspecting the actual
# rotated local PFC geometry in a throwaway sandbox script (confirming
# genuine, large-area contact with both members at the existing
# ConnectionLocation, z=3994) — never calculated by any production
# code, and never claimed to represent a real drawing's connection
# detail.
#
# STEP 8 FINDING, verified empirically before writing any assertion:
# rotating Member B to 180 degrees at the SAME translation makes
# member.solid.union(plate) still report ONE solid (the original 7K
# check alone would accept this as "touching"), while the actual
# bounding-box overlap on the Y axis is ~1e-14mm — a numerically
# degenerate knife-edge, not a real contact area. This is exactly why
# _require_contact() in app/cad_engine/two_member_connection.py now
# also checks meaningful X/Y overlap (see that module's docstring) —
# proven here via test_degenerate_edge_touch_is_rejected_not_a_false_positive.
# ============================================================================


def make_member_b_rotated_placement(**overrides) -> MemberPlacement:
    base = {"x": 250.0, "y": 0.0, "z": 4000.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 90.0}
    base.update(overrides)
    return MemberPlacement(**base)


def _build(row: dict, matcher) -> tuple:
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    return validated, geometry


def make_test_reviewed_location_record(**overrides) -> dict:
    record = make_multi_member_connection_record()
    record.update({
        "x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0,
    })
    record.update(overrides)
    return record


def make_member_a_placement(**overrides) -> MemberPlacement:
    base = {"x": 0.0, "y": 0.0, "z": 0.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    base.update(overrides)
    return MemberPlacement(**base)


def make_member_b_placement(**overrides) -> MemberPlacement:
    base = {"x": 0.0, "y": 0.0, "z": 4000.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    base.update(overrides)
    return MemberPlacement(**base)


def _build_two_member_assembly(
    member_a_row=None, member_b_row=None,
    member_a_placement=None, member_b_placement=None,
    location_record=None, matcher=None,
):
    matcher = matcher or make_multi_member_matcher()
    member_a_row = member_a_row or make_member_a_row()
    member_b_row = member_b_row or make_member_b_row()

    _, geometry_a = _build(member_a_row, matcher)
    _, geometry_b = _build(member_b_row, matcher)

    placement_a = member_a_placement or make_member_a_placement()
    placement_b = member_b_placement or make_member_b_placement()
    placed_a = PlacedMember(
        mark=member_a_row["mark"], geometry=place_member_geometry(geometry_a, placement_a), placement=placement_a,
    )
    placed_b = PlacedMember(
        mark=member_b_row["mark"], geometry=place_member_geometry(geometry_b, placement_b), placement=placement_b,
    )

    record = location_record or make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    connection_assembly = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    return connection_assembly, placed_a, placed_b, validated_connection, location


# === Primary happy path ===
def test_primary_two_member_connection_geometry_is_generated():
    connection_assembly, placed_a, placed_b, validated_connection, location = _build_two_member_assembly()

    assert isinstance(connection_assembly, GeneratedConnectionAssembly)
    assert connection_assembly.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert {connection_assembly.member_a.mark, connection_assembly.member_b.mark} == {"REAL-UB-CAD-001", "L2"}
    assert connection_assembly.plate is not None
    assert len(connection_assembly.holes) == 4
    assert {round(h.diameter, 6) for h in connection_assembly.holes} == {22.0}
    assert connection_assembly.hardware == []

    # Geometry representation, never boolean fusion: neither member's
    # own solid was replaced or altered by building this connection.
    assert len(placed_a.geometry.solid.solids().vals()) == 1
    assert len(placed_b.geometry.solid.solids().vals()) == 1


# === Step 13: identity ===
def test_connection_id_change_affects_only_identity():
    record = make_test_reviewed_location_record(connection_id="CONN-DIFFERENT-ID")
    assembly, *_ = _build_two_member_assembly(location_record=record)

    assert assembly.connection_id == "CONN-DIFFERENT-ID"
    assert len(assembly.holes) == 4  # plate geometry unaffected


def test_consistent_member_mark_change_is_reflected():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row(mark="RENAMED-A")
    record = make_test_reviewed_location_record(connected_member_marks=["RENAMED-A", "L2"])

    assembly, placed_a, placed_b, *_ = _build_two_member_assembly(
        member_a_row=row_a, location_record=record, matcher=matcher,
    )
    assert {assembly.member_a.mark, assembly.member_b.mark} == {"RENAMED-A", "L2"}
    assert len(assembly.holes) == 4


def test_member_mark_mismatch_with_connection_is_rejected():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    placed_a = PlacedMember(
        mark="SOME-OTHER-MARK", geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, make_member_b_placement()),
        placement=make_member_b_placement(),
    )

    record = make_test_reviewed_location_record()  # still names REAL-UB-CAD-001 and L2
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    with pytest.raises(GeometryValidationError, match="do not match"):
        build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)


def test_missing_connected_member_is_rejected():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    placed_a = PlacedMember(
        mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, make_member_b_placement()),
        placement=make_member_b_placement(),
    )

    record = make_test_reviewed_location_record(connected_member_marks=["REAL-UB-CAD-001"])  # only one
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    with pytest.raises(GeometryValidationError, match="do not match"):
        build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)


# === Step 14: member-order independence ===
def test_member_order_independence():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    placed_a = PlacedMember(
        mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, make_member_b_placement()),
        placement=make_member_b_placement(),
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    assembly_ab = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    assembly_ba = build_two_member_connection_assembly(validated_connection, location, placed_b, placed_a)

    # Only the container labels swap — the connection contract has no
    # primary/secondary member concept (documented, not invented here).
    assert assembly_ab.member_a.mark == assembly_ba.member_b.mark
    assert assembly_ab.member_b.mark == assembly_ba.member_a.mark

    bbox_ab = assembly_ab.plate.val().BoundingBox()
    bbox_ba = assembly_ba.plate.val().BoundingBox()
    assert abs(bbox_ab.zmin - bbox_ba.zmin) < 1e-6
    assert abs(bbox_ab.xmin - bbox_ba.xmin) < 1e-6
    assert len(assembly_ab.holes) == len(assembly_ba.holes) == 4
    assert sorted(h.center[0] for h in assembly_ab.holes) == sorted(h.center[0] for h in assembly_ba.holes)


def test_connected_member_marks_order_in_record_does_not_matter():
    record_ab = make_test_reviewed_location_record(connected_member_marks=["REAL-UB-CAD-001", "L2"])
    record_ba = make_test_reviewed_location_record(connected_member_marks=["L2", "REAL-UB-CAD-001"])

    assembly_ab, *_ = _build_two_member_assembly(location_record=record_ab)
    assembly_ba, *_ = _build_two_member_assembly(location_record=record_ba)

    bbox_ab = assembly_ab.plate.val().BoundingBox()
    bbox_ba = assembly_ba.plate.val().BoundingBox()
    assert abs(bbox_ab.zmin - bbox_ba.zmin) < 1e-6


# === Step 15: connection-location sensitivity ===
def test_x_change_moves_plate_and_holes():
    assembly_before, *_ = _build_two_member_assembly()
    assembly_after, *_ = _build_two_member_assembly(location_record=make_test_reviewed_location_record(x=50.0))

    bbox_before = assembly_before.plate.val().BoundingBox()
    bbox_after = assembly_after.plate.val().BoundingBox()
    assert abs((bbox_after.xmin - bbox_before.xmin) - 50.0) < 1e-6
    assert abs(bbox_after.zmin - bbox_before.zmin) < 1e-6

    xs_before = sorted(h.center[0] for h in assembly_before.holes)
    xs_after = sorted(h.center[0] for h in assembly_after.holes)
    assert all(abs((a - b) - 50.0) < 1e-6 for a, b in zip(xs_after, xs_before))


def test_z_change_moves_plate_and_holes():
    assembly_before, *_ = _build_two_member_assembly()
    assembly_after, *_ = _build_two_member_assembly(location_record=make_test_reviewed_location_record(z=4000.0))

    bbox_before = assembly_before.plate.val().BoundingBox()
    bbox_after = assembly_after.plate.val().BoundingBox()
    assert abs((bbox_after.zmin - bbox_before.zmin) - 6.0) < 1e-6
    assert abs(bbox_after.xmin - bbox_before.xmin) < 1e-6


def test_rotation_change_rotates_plate_without_moving_members():
    assembly_before, placed_a_before, placed_b_before, *_ = _build_two_member_assembly()
    assembly_after, placed_a_after, placed_b_after, *_ = _build_two_member_assembly(
        location_record=make_test_reviewed_location_record(rotation_z=15.0),
    )

    bbox_before = assembly_before.plate.val().BoundingBox()
    bbox_after = assembly_after.plate.val().BoundingBox()
    # A genuine rotation changes the plate's footprint shape, not just its position.
    assert abs((bbox_after.xmax - bbox_after.xmin) - (bbox_before.xmax - bbox_before.xmin)) > 1.0

    # Members' own geometry is completely unaffected by the connection's rotation.
    bbox_a_before = placed_a_before.geometry.solid.val().BoundingBox()
    bbox_a_after = placed_a_after.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.xmax - bbox_a_before.xmax) < 1e-6
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6


# === Step 16: member-placement independence ===
def test_member_placement_change_does_not_move_connection_and_may_break_consistency():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)

    placement_a = make_member_a_placement()
    original_placement_b = make_member_b_placement()          # z=4000, touches
    moved_placement_b = make_member_b_placement(z=4500.0)      # creates a 494mm gap

    placed_a = PlacedMember(mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, placement_a), placement=placement_a)
    placed_b_original = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, original_placement_b), placement=original_placement_b,
    )
    placed_b_moved = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, moved_placement_b), placement=moved_placement_b,
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location_before = real_connection_location_to_connection_location(record)

    assembly_before = build_two_member_connection_assembly(validated_connection, location_before, placed_a, placed_b_original)
    assert assembly_before.plate is not None

    # The connection's own explicit location, re-read from the SAME
    # unchanged record, is identical — it never silently follows Member B.
    location_after = real_connection_location_to_connection_location(record)
    assert location_after == location_before

    # Rebuilding against the MOVED Member B correctly fails — nothing
    # silently repositions the connection or Member B to force a fit.
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        build_two_member_connection_assembly(validated_connection, location_after, placed_a, placed_b_moved)


def test_member_b_length_change_does_not_affect_connection_at_its_start_end():
    # Member B's length grows; the connection sits at B's START (near
    # Z=4000), far from the end that moves, so it should remain valid.
    matcher = make_multi_member_matcher()
    row_b_longer = make_member_b_row(length_mm=3500)
    assembly, placed_a, placed_b, *_ = _build_two_member_assembly(member_b_row=row_b_longer, matcher=matcher)

    assert assembly.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert placed_b.geometry.length_mm == 3500.0
    assert len(assembly.holes) == 4


# === Step 17: source independence ===
def test_source_independence_after_clearing_raw_dictionaries():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row()
    row_b = make_member_b_row()
    validated_a = real_member_to_validated_member(row_a, matcher)
    validated_b = real_member_to_validated_member(row_b, matcher)
    geometry_a = generate_geometry(validated_a)
    geometry_b = generate_geometry(validated_b)

    placed_a = PlacedMember(
        mark=validated_a.mark, geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark=validated_b.mark, geometry=place_member_geometry(geometry_b, make_member_b_placement()),
        placement=make_member_b_placement(),
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    row_a.clear()
    row_b.clear()
    record.clear()

    assembly = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    assert assembly.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert len(assembly.holes) == 4


# === Step 18: no mutation ===
def test_no_mutation_of_inputs():
    connection_assembly, placed_a, placed_b, validated_connection, location = _build_two_member_assembly()

    bbox_a_before = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_before = placed_b.geometry.solid.val().BoundingBox()

    connection_assembly_2 = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)

    bbox_a_after = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_after = placed_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.xmin - bbox_a_before.xmin) < 1e-6
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(bbox_b_after.zmin - bbox_b_before.zmin) < 1e-6

    assert connection_assembly.connection_id == connection_assembly_2.connection_id
    assert len(connection_assembly_2.holes) == 4
    assert location.x == 0.0 and location.z == 3994.0  # frozen ConnectionLocation, unaffected


# === Step 19: physical bolts stay optional / always absent from output ===
def test_hardware_is_always_empty_even_when_physical_bolts_requested():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    placed_a = PlacedMember(
        mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, make_member_b_placement()),
        placement=make_member_b_placement(),
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    validated_connection.physical_bolts = {
        "shaft_diameter": 20.0, "shaft_length": 50.0, "head_across_flats": 30.0, "head_thickness": 12.0,
    }
    location = real_connection_location_to_connection_location(record)

    assembly = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    assert assembly.hardware == []


# === Step 20: assembly architecture — coexistence, no auto union ===
def test_coexists_with_generated_assembly_no_auto_union():
    connection_assembly, placed_a, placed_b, *_ = _build_two_member_assembly()

    assembly = GeneratedAssembly(members=[placed_a, placed_b])
    assert len(assembly.members) == 2
    assert assembly.members[0].geometry.solid is not assembly.members[1].geometry.solid
    assert len(assembly.members[0].geometry.solid.solids().vals()) == 1
    assert len(assembly.members[1].geometry.solid.solids().vals()) == 1

    # GeneratedAssembly was never modified to hold connection assemblies.
    assert not hasattr(assembly, "connections")
    assert connection_assembly.member_a in assembly.members
    assert connection_assembly.member_b in assembly.members


# === Step 21: remaining adversarial/rejection cases ===
def test_invalid_connection_location_rejected_before_geometry():
    record = make_test_reviewed_location_record(x=float("nan"))
    with pytest.raises(GeometryValidationError):
        real_connection_location_to_connection_location(record)


def test_missing_connection_location_rejected_before_geometry():
    record = make_test_reviewed_location_record()
    del record["z"]
    with pytest.raises(GeometryValidationError):
        real_connection_location_to_connection_location(record)


def test_connection_not_meeting_member_a_is_rejected():
    with pytest.raises(GeometryValidationError, match="member 'REAL-UB-CAD-001'"):
        _build_two_member_assembly(location_record=make_test_reviewed_location_record(z=6500.0))


def test_connection_not_meeting_member_b_is_rejected():
    with pytest.raises(GeometryValidationError, match="member 'L2'"):
        _build_two_member_assembly(location_record=make_test_reviewed_location_record(z=100.0))


# ============================================================================
# Milestone 7L tests — explicit, non-zero Member B orientation
# ============================================================================

# === Primary valid case (Step 3/4) ===
def test_7l_primary_two_member_connection_with_rotated_member_b():
    assembly, placed_a, placed_b, validated_connection, location = _build_two_member_assembly(
        member_b_placement=make_member_b_rotated_placement(),
    )
    assert assembly.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert {assembly.member_a.mark, assembly.member_b.mark} == {"REAL-UB-CAD-001", "L2"}
    assert len(assembly.holes) == 4
    assert placed_b.placement.rotation_z == 90.0
    assert len(placed_a.geometry.solid.solids().vals()) == 1
    assert len(placed_b.geometry.solid.solids().vals()) == 1


# === Step 6: actual transformed CAD geometry, not just the placement value ===
def test_member_b_rotation_genuinely_transforms_its_solid():
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)
    volume_before = geometry_b.solid.val().Volume()

    placed_unrotated = place_member_geometry(geometry_b, make_member_b_placement())
    placed_rotated = place_member_geometry(geometry_b, make_member_b_rotated_placement())

    bbox_unrot = placed_unrotated.solid.val().BoundingBox()
    bbox_rot = placed_rotated.solid.val().BoundingBox()

    # Genuine rotation: footprint dimensions swap (90 <-> 250), not just position.
    assert abs((bbox_unrot.xmax - bbox_unrot.xmin) - FULL_PFC_SECTION["flange_width"]) < 1e-6
    assert abs((bbox_unrot.ymax - bbox_unrot.ymin) - FULL_PFC_SECTION["depth"]) < 1e-6
    assert abs((bbox_rot.xmax - bbox_rot.xmin) - FULL_PFC_SECTION["depth"]) < 1e-6
    assert abs((bbox_rot.ymax - bbox_rot.ymin) - FULL_PFC_SECTION["flange_width"]) < 1e-6

    # Length (Z extent), volume, and topology are unaffected by a rigid rotation.
    assert abs((bbox_rot.zmax - bbox_rot.zmin) - 3000.0) < 1e-6
    assert abs(placed_rotated.solid.val().Volume() - volume_before) < 1e-6
    assert len(placed_rotated.solid.solids().vals()) == 1


# === Step 7: connection remains independently located, unaffected by member rotation ===
def test_connection_location_independent_of_member_b_rotation():
    record = make_test_reviewed_location_record()
    location_before = real_connection_location_to_connection_location(record)

    _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(), location_record=record)

    location_after = real_connection_location_to_connection_location(record)
    assert location_after == location_before


# === Step 8: the false positive the original 7K check alone could not catch ===
def test_degenerate_edge_touch_is_rejected_not_a_false_positive():
    """
    Member B rotated 180 degrees at the SAME translation that produces
    a genuine large-area contact at 90 degrees. Verified empirically
    (see module docstring): the touching-union arity check alone
    reports exactly one solid here — a false positive, since the true
    contact is a near-zero-width sliver (Y overlap ~1e-14mm), not a
    real face. This is exactly why _require_contact() also checks
    meaningful X/Y overlap.
    """
    with pytest.raises(GeometryValidationError, match="degenerate edge"):
        _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(rotation_z=180.0))


# === Step 9: orientation sensitivity ===
def test_orientation_sensitivity_member_b_rotation_changes_geometry_not_connection():
    assembly_90, *_ = _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(rotation_z=90.0))
    assembly_45, *_ = _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(rotation_z=45.0))

    bbox_90 = assembly_90.member_b.geometry.solid.val().BoundingBox()
    bbox_45 = assembly_45.member_b.geometry.solid.val().BoundingBox()
    assert abs((bbox_90.xmax - bbox_90.xmin) - (bbox_45.xmax - bbox_45.xmin)) > 1.0  # genuinely different footprint

    # The connection's own explicit location is identical regardless of Member B's rotation.
    assert assembly_90.location == assembly_45.location
    bbox_plate_90 = assembly_90.plate.val().BoundingBox()
    bbox_plate_45 = assembly_45.plate.val().BoundingBox()
    assert abs(bbox_plate_90.zmin - bbox_plate_45.zmin) < 1e-6


# === Step 10: translation sensitivity for the rotated member ===
def test_translation_sensitivity_member_b_x_change_with_rotation():
    assembly_before, *_ = _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(x=250.0))
    assembly_after, *_ = _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(x=300.0))

    bbox_before = assembly_before.member_b.geometry.solid.val().BoundingBox()
    bbox_after = assembly_after.member_b.geometry.solid.val().BoundingBox()
    assert abs((bbox_after.xmin - bbox_before.xmin) - 50.0) < 1e-6

    # Connection unaffected by Member B's translation.
    assert assembly_before.location == assembly_after.location


# === Step 11: connection-location sensitivity with a rotated member present ===
def test_connection_location_sensitivity_with_rotated_member_present():
    assembly_before, *_ = _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement())
    assembly_after, *_ = _build_two_member_assembly(
        member_b_placement=make_member_b_rotated_placement(),
        location_record=make_test_reviewed_location_record(x=50.0),
    )
    bbox_before = assembly_before.plate.val().BoundingBox()
    bbox_after = assembly_after.plate.val().BoundingBox()
    assert abs((bbox_after.xmin - bbox_before.xmin) - 50.0) < 1e-6

    bbox_a_before = assembly_before.member_a.geometry.solid.val().BoundingBox()
    bbox_a_after = assembly_after.member_a.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.xmin - bbox_a_before.xmin) < 1e-6
    bbox_b_before = assembly_before.member_b.geometry.solid.val().BoundingBox()
    bbox_b_after = assembly_after.member_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_b_after.xmin - bbox_b_before.xmin) < 1e-6


# === Step 12: identity preserved alongside the rotated member ===
def test_identity_preserved_with_rotated_member_b():
    assembly, *_ = _build_two_member_assembly(
        member_b_placement=make_member_b_rotated_placement(),
        location_record=make_test_reviewed_location_record(connection_id="CONN-7L-ROTATED"),
    )
    assert assembly.connection_id == "CONN-7L-ROTATED"
    assert {assembly.member_a.mark, assembly.member_b.mark} == {"REAL-UB-CAD-001", "L2"}


# === Step 13: member-order independence with a rotated member present ===
def test_member_order_independence_with_rotated_member_b():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    rotated_placement = make_member_b_rotated_placement()
    placed_a = PlacedMember(
        mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, rotated_placement), placement=rotated_placement,
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    assembly_ab = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    assembly_ba = build_two_member_connection_assembly(validated_connection, location, placed_b, placed_a)

    assert assembly_ab.member_a.mark == assembly_ba.member_b.mark
    assert assembly_ab.member_b.mark == assembly_ba.member_a.mark
    bbox_ab = assembly_ab.plate.val().BoundingBox()
    bbox_ba = assembly_ba.plate.val().BoundingBox()
    assert abs(bbox_ab.zmin - bbox_ba.zmin) < 1e-6
    assert len(assembly_ab.holes) == len(assembly_ba.holes) == 4


# === Step 14: physical bolts remain out of scope, even with a rotated member ===
def test_hardware_still_empty_with_rotated_member_b():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    rotated_placement = make_member_b_rotated_placement()
    placed_a = PlacedMember(
        mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, rotated_placement), placement=rotated_placement,
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    validated_connection.physical_bolts = {
        "shaft_diameter": 20.0, "shaft_length": 50.0, "head_across_flats": 30.0, "head_thickness": 12.0,
    }
    location = real_connection_location_to_connection_location(record)

    assembly = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    assert assembly.hardware == []


# === Step 15: no mutation ===
def test_no_mutation_with_rotated_member_b():
    assembly_1, placed_a, placed_b, validated_connection, location = _build_two_member_assembly(
        member_b_placement=make_member_b_rotated_placement(),
    )
    bbox_a_before = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_before = placed_b.geometry.solid.val().BoundingBox()

    assembly_2 = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)

    bbox_a_after = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_after = placed_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.xmax - bbox_a_before.xmax) < 1e-6
    assert abs(bbox_b_after.xmax - bbox_b_before.xmax) < 1e-6
    assert placed_b.placement.rotation_z == 90.0  # MemberPlacement itself, untouched
    assert assembly_1.connection_id == assembly_2.connection_id
    assert len(assembly_2.holes) == 4


# === Step 16: source independence ===
def test_source_independence_with_rotated_member_b():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row()
    row_b = make_member_b_row()
    validated_a = real_member_to_validated_member(row_a, matcher)
    validated_b = real_member_to_validated_member(row_b, matcher)
    geometry_a = generate_geometry(validated_a)
    geometry_b = generate_geometry(validated_b)

    rotated_placement = make_member_b_rotated_placement()
    placed_a = PlacedMember(
        mark=validated_a.mark, geometry=place_member_geometry(geometry_a, make_member_a_placement()),
        placement=make_member_a_placement(),
    )
    placed_b = PlacedMember(
        mark=validated_b.mark, geometry=place_member_geometry(geometry_b, rotated_placement),
        placement=rotated_placement,
    )

    record = make_test_reviewed_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)

    row_a.clear()
    row_b.clear()
    record.clear()

    assembly = build_two_member_connection_assembly(validated_connection, location, placed_a, placed_b)
    assert assembly.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert len(assembly.holes) == 4


# === Step 17: remaining adversarial cases ===
def test_member_b_moved_away_with_rotation_is_rejected():
    with pytest.raises(GeometryValidationError, match="member 'L2'"):
        _build_two_member_assembly(member_b_placement=make_member_b_rotated_placement(z=10000.0))


def test_connection_moved_away_with_rotated_member_is_rejected():
    with pytest.raises(GeometryValidationError):
        _build_two_member_assembly(
            member_b_placement=make_member_b_rotated_placement(),
            location_record=make_test_reviewed_location_record(z=20000.0),
        )


def test_member_a_moved_away_with_rotated_member_b_present():
    with pytest.raises(GeometryValidationError, match="member 'REAL-UB-CAD-001'"):
        _build_two_member_assembly(
            member_a_placement=make_member_a_placement(z=-10000.0),
            member_b_placement=make_member_b_rotated_placement(),
        )


def test_existing_7k_unrotated_arrangement_still_passes():
    """Explicit regression proof: 7L's stricter _require_contact() does
    not break 7K's own original (unrotated) valid arrangement."""
    assembly, *_ = _build_two_member_assembly()
    assert len(assembly.holes) == 4
    assert assembly.member_b.placement.rotation_z == 0.0
