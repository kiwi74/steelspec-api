"""
Milestone 7O — proving one complete, explicit, reviewed two-member
connection assembly in project space, by composing every existing
reviewed/validated contract established across 7A-7N:

    real member rows -> 7A -> ValidatedSteelMember -> 7I -> PlacedMember (x2)
    reviewed connection -> 7D/7H -> ValidatedConnection
    reviewed location -> 7J -> ConnectionLocation
    reviewed connection detail -> 7N -> ConnectionAttachment (x2)
        -> 7M resolve_connection_attachment_surfaces()
        -> 7K/7L build_two_member_connection_assembly()
    -> build_reviewed_two_member_connection_assembly()
    -> ReviewedTwoMemberConnectionAssembly

This is an orchestration/integration milestone: it introduces no new
validation rule, no AI inference, and no automatic face/position
selection — every consistency check exercised below is enforced by
the existing 7M/7K/7L functions this module calls unchanged.

FIXTURE PROVENANCE — TEST / HUMAN-REVIEWED / SUPPLEMENTED throughout,
reusing the exact established fixtures rather than inventing new
geometry: Member A (REAL-UB-CAD-001, 310UB40, 4000mm) and Member B
(L2, 250PFC, 3000mm, HUMAN-REVIEWED/SUPPLEMENTED length) at their
established 7K unrotated placements (A: x=0,y=0,z=0; B: x=0,y=0,
z=4000); the 7H/7D reviewed connection fixture (180x250x12mm end
plate, 4xO22mm holes); the 7K reviewed ConnectionLocation
(x=0,y=0,z=3994, verified to genuinely contact both members); and the
7N reviewed attachment fixture (REAL-UB-CAD-001 -> END, L2 -> START).
None of this is claimed to be AI-extracted or drawing-derived.
"""
import pytest

from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_location import real_connection_location_to_connection_location
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_assembly import (
    ReviewedTwoMemberConnectionAssembly,
    build_reviewed_two_member_connection_assembly,
)
from app.cad_engine.reviewed_connection_attachment import reviewed_connection_detail_to_attachments
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_real_multi_member_cad import (
    FULL_PFC_SECTION,
    FULL_UB_SECTION,
    make_member_a_row,
    make_member_b_row,
    make_multi_member_matcher,
)
from tests.test_real_multi_member_connection import make_multi_member_connection_record
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record
from tests.test_two_member_connection import make_test_reviewed_location_record


def _build(row: dict, matcher) -> tuple:
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    return validated, geometry


def _build_primary_assembly(
    member_a_row=None, member_b_row=None,
    member_a_placement=None, member_b_placement=None,
    connection_record=None, location_record=None, detail_record=None,
    matcher=None,
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

    connection_record = connection_record or make_multi_member_connection_record()
    validated_connection = real_connection_to_validated_connection(connection_record)

    location_record = location_record or make_test_reviewed_location_record()
    location = real_connection_location_to_connection_location(location_record)

    detail_record = detail_record or make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    attachment_a = next(a for a in attachments if a.member_mark == placed_a.mark)
    attachment_b = next(a for a in attachments if a.member_mark == placed_b.mark)

    assembly = build_reviewed_two_member_connection_assembly(
        placed_a, placed_b, validated_connection, location, attachment_a, attachment_b,
    )
    return assembly, placed_a, placed_b, validated_connection, location, attachment_a, attachment_b


def _attachment_for_mark(assembly, mark):
    return assembly.attachment_a if assembly.attachment_a.member_mark == mark else assembly.attachment_b


def _face_for_mark(assembly, mark):
    if assembly.resolved_surfaces.member_a_attachment.member_mark == mark:
        return assembly.resolved_surfaces.member_a_face
    return assembly.resolved_surfaces.member_b_face


# === Primary chain: full reviewed two-member assembly reaches actual geometry ===
def test_primary_reviewed_two_member_assembly_reaches_actual_geometry():
    assembly, placed_a, placed_b, validated_connection, location, attachment_a, attachment_b = (
        _build_primary_assembly()
    )

    assert isinstance(assembly, ReviewedTwoMemberConnectionAssembly)
    # 1/2: actual generated solids, each one connected solid
    assert len(assembly.member_a.geometry.solid.solids().vals()) == 1
    assert len(assembly.member_b.geometry.solid.solids().vals()) == 1
    # 3: project-space placements
    assert assembly.member_a.placement.z == 0.0
    assert assembly.member_b.placement.z == 4000.0
    # 4: validated connection
    assert assembly.connection.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert set(assembly.connection.connected_members) == {"REAL-UB-CAD-001", "L2"}
    # 5: explicit connection location
    assert assembly.location.z == 3994.0
    # 6: explicit attachment references, distinguishable from resolved geometry
    assert {assembly.attachment_a.member_mark, assembly.attachment_b.member_mark} == {"REAL-UB-CAD-001", "L2"}
    assert _attachment_for_mark(assembly, "REAL-UB-CAD-001").surface_reference == "END"
    assert _attachment_for_mark(assembly, "L2").surface_reference == "START"
    # 7: resolved project-space attachment faces (separate objects from the attachments)
    assert assembly.resolved_surfaces.member_a_face is not None
    assert assembly.resolved_surfaces.member_b_face is not None
    # 8: generated connection plate/hole geometry
    assert len(assembly.connection_geometry.holes) == 4


# === Measured geometry, not merely "exists" ===
def test_primary_assembly_geometry_is_measured_and_correct():
    assembly, *_ = _build_primary_assembly()

    bbox_a = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b = assembly.member_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a.xmax - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs(bbox_a.ymax - FULL_UB_SECTION["depth"]) < 1e-6
    assert abs(bbox_a.zmax - 4000.0) < 1e-6
    assert abs(bbox_b.xmax - FULL_PFC_SECTION["flange_width"]) < 1e-6
    assert abs(bbox_b.zmin - 4000.0) < 1e-6
    assert abs(bbox_b.zmax - 7000.0) < 1e-6

    face_a = _face_for_mark(assembly, "REAL-UB-CAD-001")
    face_b = _face_for_mark(assembly, "L2")
    assert abs(face_a.normalAt().z - 1.0) < 1e-6           # END -> +Z
    assert abs(face_a.BoundingBox().zmax - 4000.0) < 1e-6
    assert abs(face_b.normalAt().z - (-1.0)) < 1e-6        # START -> -Z
    assert abs(face_b.BoundingBox().zmin - 4000.0) < 1e-6

    plate_bbox = assembly.connection_geometry.plate.val().BoundingBox()
    assert abs((plate_bbox.xmax - plate_bbox.xmin) - 180.0) < 1e-6
    assert abs((plate_bbox.ymax - plate_bbox.ymin) - 250.0) < 1e-6
    assert abs((plate_bbox.zmax - plate_bbox.zmin) - 12.0) < 1e-6
    assert {round(h.diameter, 6) for h in assembly.connection_geometry.holes} == {22.0}


# === Test 1: reverse attachment/member argument order -> equivalent result ===
def test_1_reverse_attachment_order_produces_equivalent_assembly():
    assembly_ab, placed_a, placed_b, validated_connection, location, attachment_a, attachment_b = (
        _build_primary_assembly()
    )

    assembly_ba = build_reviewed_two_member_connection_assembly(
        placed_b, placed_a, validated_connection, location, attachment_b, attachment_a,
    )

    face_a_via_ab = _face_for_mark(assembly_ab, "REAL-UB-CAD-001")
    face_a_via_ba = _face_for_mark(assembly_ba, "REAL-UB-CAD-001")
    assert abs(face_a_via_ab.BoundingBox().zmax - face_a_via_ba.BoundingBox().zmax) < 1e-6

    diam_ab = {round(h.diameter, 6) for h in assembly_ab.connection_geometry.holes}
    diam_ba = {round(h.diameter, 6) for h in assembly_ba.connection_geometry.holes}
    assert diam_ab == diam_ba == {22.0}


# === Test 2: explicit "wrong"/unusual surface is honoured, never reverted ===
def test_2_explicit_unusual_surface_reference_is_honoured_not_reverted():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},  # unusual but explicit
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    attachment_a = _attachment_for_mark(assembly, "REAL-UB-CAD-001")
    assert attachment_a.surface_reference == "START"

    face_a = _face_for_mark(assembly, "REAL-UB-CAD-001")
    assert abs(face_a.BoundingBox().zmin - 0.0) < 1e-6
    assert abs(face_a.normalAt().z - (-1.0)) < 1e-6


# === Test 3: connection.position conflict -- explicit attachment wins ===
def test_3_connection_position_conflict_attachment_stays_explicit():
    connection_record = make_multi_member_connection_record(position="END")
    detail_record = make_reviewed_connection_detail_record()  # L2 -> START
    assembly, *_ = _build_primary_assembly(connection_record=connection_record, detail_record=detail_record)

    assert assembly.connection.position == "END"
    attachment_b = _attachment_for_mark(assembly, "L2")
    assert attachment_b.surface_reference == "START"  # unaffected by connection.position


# === Test 4: moving a member never auto-repairs the connection ===
def test_4_moving_member_b_does_not_repair_automatically_rejects_if_inconsistent():
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(member_b_placement=make_member_b_placement(z=10000.0))


# === Test 5: connection-location change moves the connection, not the attachments ===
def test_5_moving_connection_location_moves_connection_not_attachments():
    assembly_before, *_ = _build_primary_assembly()
    assembly_after, *_ = _build_primary_assembly(location_record=make_test_reviewed_location_record(x=50.0))

    bbox_before = assembly_before.connection_geometry.plate.val().BoundingBox()
    bbox_after = assembly_after.connection_geometry.plate.val().BoundingBox()
    assert abs((bbox_after.xmin - bbox_before.xmin) - 50.0) < 1e-6

    assert assembly_before.attachment_a == assembly_after.attachment_a
    assert assembly_before.attachment_b == assembly_after.attachment_b


# === Test 6: member section/length change leaves the attachment unchanged ===
def test_6_member_b_length_change_leaves_attachment_unchanged():
    row_b_longer = make_member_b_row(length_mm=3500)
    assembly, *_ = _build_primary_assembly(member_b_row=row_b_longer)

    attachment_b = _attachment_for_mark(assembly, "L2")
    assert attachment_b.member_mark == "L2"
    assert attachment_b.surface_reference == "START"
    assert assembly.member_b.geometry.length_mm == 3500.0


# === Test 7: unknown member in attachment is rejected ===
def test_7_unknown_member_in_attachment_is_rejected():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "UNKNOWN-MARK", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError):
        _build_primary_assembly(detail_record=detail_record)


# === Test 8: missing attachment for a connected member is rejected ===
def test_8_missing_attachment_for_connected_member_is_rejected():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
    ])
    with pytest.raises(GeometryValidationError):
        _build_primary_assembly(detail_record=detail_record)


# === Test 9: extra attachment for an unconnected member is rejected ===
def test_9_extra_attachment_for_unconnected_member_is_rejected():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
        {"member_mark": "THIRD-MEMBER", "surface_reference": "END"},
    ])
    with pytest.raises(GeometryValidationError):
        _build_primary_assembly(detail_record=detail_record)


# === Test 10: no mutation of source dictionaries or existing geometry ===
def test_10_no_mutation_of_source_dictionaries_and_geometry():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row()
    row_b = make_member_b_row()
    connection_record = make_multi_member_connection_record()
    location_record = make_test_reviewed_location_record()
    detail_record = make_reviewed_connection_detail_record()

    validated_a = real_member_to_validated_member(row_a, matcher)
    validated_b = real_member_to_validated_member(row_b, matcher)
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

    validated_connection = real_connection_to_validated_connection(connection_record)
    location = real_connection_location_to_connection_location(location_record)
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    attachment_a = next(a for a in attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in attachments if a.member_mark == "L2")

    bbox_a_before = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_before = placed_b.geometry.solid.val().BoundingBox()

    row_a.clear()
    row_b.clear()
    connection_record.clear()
    location_record.clear()
    detail_record.clear()

    assembly = build_reviewed_two_member_connection_assembly(
        placed_a, placed_b, validated_connection, location, attachment_a, attachment_b,
    )

    bbox_a_after = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_after = placed_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.xmax - bbox_a_before.xmax) < 1e-6
    assert abs(bbox_b_after.zmin - bbox_b_before.zmin) < 1e-6
    assert assembly.connection.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert len(assembly.connection_geometry.holes) == 4
