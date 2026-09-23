"""
Milestone 7Q — proving explicit project-space PLANE + NORMAL
consistency between the reviewed, resolved attachment faces and the
generated connection interface geometry, strengthening 7P's bare
global-Z bounding-box check.

A passing result means only that the generated connection interface
geometry occupies a compatible project-space plane and normal
relationship with the explicitly reviewed attachment faces — see
app/cad_engine/connection_interface_consistency.py's own module
docstring for the full boundary statement (no structural, engineering,
or fabrication-readiness claim of any kind).

FIXTURE PROVENANCE: reuses 7O/7P's exact primary fixture and helper
(`_build_primary_assembly`) unchanged — Member A (REAL-UB-CAD-001,
310UB40, 4000mm) at x=0,y=0,z=0; Member B (L2, 250PFC, 3000mm,
HUMAN-REVIEWED/SUPPLEMENTED length) at x=0,y=0,z=4000; the 7H/7D
reviewed connection fixture (180x250x12mm plate, 4xO22mm holes); the
7K reviewed ConnectionLocation (z=3994); the 7N reviewed attachment
fixture (REAL-UB-CAD-001->END, L2->START). Nothing here is claimed to
be AI-extracted or drawing-derived.
"""
from dataclasses import replace

import pytest

from app.cad_engine.connection_interface_consistency import (
    NORMAL_CONSISTENCY_TOLERANCE,
    ProjectSpaceInterfaceConsistencyResult,
    validate_connection_interface_planes_and_normals,
)
from app.cad_engine.errors import GeometryValidationError
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record
from tests.test_real_multi_member_connection import make_multi_member_connection_record
from tests.test_two_member_connection import make_member_b_rotated_placement, make_test_reviewed_location_record


# === 1. Valid project-space interface passes ===
def test_valid_interface_planes_and_normals_pass():
    assembly, *_ = _build_primary_assembly()

    result = validate_connection_interface_planes_and_normals(assembly)

    assert isinstance(result, ProjectSpaceInterfaceConsistencyResult)
    assert abs(result.member_a_face_position_mm - 4000.0) < 1e-6
    assert abs(result.member_a_face_normal[2] - 1.0) < 1e-9
    assert abs(result.member_a_plate_face_position_mm - 3994.0) < 1e-6
    assert abs(result.member_a_plate_face_normal[2] - (-1.0)) < 1e-9
    assert abs(result.member_a_normal_dot - (-1.0)) < 1e-9

    assert abs(result.member_b_face_position_mm - 4000.0) < 1e-6
    assert abs(result.member_b_face_normal[2] - (-1.0)) < 1e-9
    assert abs(result.member_b_plate_face_position_mm - 4006.0) < 1e-6
    assert abs(result.member_b_plate_face_normal[2] - 1.0) < 1e-9
    assert abs(result.member_b_normal_dot - (-1.0)) < 1e-9


# === 2/3. Wrong attachment fails (each member) ===
def test_wrong_member_a_attachment_fails():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    with pytest.raises(GeometryValidationError, match="REAL-UB-CAD-001"):
        validate_connection_interface_planes_and_normals(assembly)


def test_wrong_member_b_attachment_fails():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "END"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    with pytest.raises(GeometryValidationError, match="L2"):
        validate_connection_interface_planes_and_normals(assembly)


# === 4. Plane inconsistency: rejected via composed 7P position check ===
def test_plane_inconsistency_rejected_via_composed_7p_check():
    """
    A connection location far enough to misalign from the attachment
    faces is rejected by 7P's own position check, which this validator
    calls first (composition, never duplication — see module
    docstring's Step 7 note).
    """
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(location_record=make_test_reviewed_location_record(z=2000.0))


# === 5. Normal inconsistency, measured directly ===
def test_normal_inconsistency_measured_directly_for_wrong_attachment():
    """
    For the currently-constructible wrong-attachment fixtures, 7P's
    composed position check already rejects them (see
    test_wrong_member_a_attachment_fails) before this module's own
    normal check would run. This test proves, independently and
    directly — reading the already-resolved face and plate face
    normals without going through the full validator — that the
    NORMAL signal also independently disagrees for the same wrong
    case: same-direction (dot=+1.0), not opposing. This confirms the
    new invariant is a genuinely separate geometric fact, not merely a
    relabelling of 7P's own check (see module docstring's honest
    finding on responsibility boundaries).
    """
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},  # wrong: actual interface is END
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    wrong_face_a = assembly.resolved_surfaces.member_a_face
    plate_lo_face = assembly.connection_geometry.plate.faces("<Z").vals()[0]

    dot = wrong_face_a.normalAt().dot(plate_lo_face.normalAt())
    assert dot > -1.0 + NORMAL_CONSISTENCY_TOLERANCE  # same direction, not opposing -> inconsistent
    assert abs(dot - 1.0) < 1e-9


# === 6. Established 7L 90-degree Z rotation still passes ===
def test_established_z_rotation_passes():
    assembly, *_ = _build_primary_assembly(member_b_placement=make_member_b_rotated_placement())

    result = validate_connection_interface_planes_and_normals(assembly)
    assert abs(result.member_b_face_position_mm - 4000.0) < 1e-6
    assert abs(result.member_b_normal_dot - (-1.0)) < 1e-9


# === 7. Attachment order independence ===
def test_attachment_order_independence():
    assembly_default, *_ = _build_primary_assembly()
    detail_record_swapped = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "L2", "surface_reference": "START"},
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
    ])
    assembly_swapped, *_ = _build_primary_assembly(detail_record=detail_record_swapped)

    result_default = validate_connection_interface_planes_and_normals(assembly_default)
    result_swapped = validate_connection_interface_planes_and_normals(assembly_swapped)
    assert abs(result_default.member_a_normal_dot - result_swapped.member_a_normal_dot) < 1e-9
    assert abs(result_default.member_b_normal_dot - result_swapped.member_b_normal_dot) < 1e-9


# === 8. Conflicting ValidatedConnection.position does not override the explicit attachment ===
def test_conflicting_connection_position_does_not_override_attachment():
    connection_record = make_multi_member_connection_record(position="END")
    assembly, *_ = _build_primary_assembly(connection_record=connection_record)
    assert assembly.connection.position == "END"
    assert assembly.attachment_b.surface_reference == "START"  # unaffected by the generic position field

    result = validate_connection_interface_planes_and_normals(assembly)
    assert abs(result.member_b_normal_dot - (-1.0)) < 1e-9


# === 9. No mutation on success ===
def test_no_mutation_on_success():
    assembly, *_ = _build_primary_assembly()
    bbox_a_before = assembly.member_a.geometry.solid.val().BoundingBox()
    attachment_a_before = assembly.attachment_a
    location_before = assembly.location

    validate_connection_interface_planes_and_normals(assembly)

    bbox_a_after = assembly.member_a.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert assembly.attachment_a == attachment_a_before
    assert assembly.location == location_before


# === 10. Source-object independence ===
def test_source_independence_raw_records_cleared_after_assembly_built():
    detail_record = make_reviewed_connection_detail_record()
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)
    detail_record.clear()

    result = validate_connection_interface_planes_and_normals(assembly)
    assert abs(result.member_a_normal_dot - (-1.0)) < 1e-9


# === 11. Unsupported rotation/configuration is explicitly rejected ===
def test_unsupported_connection_rotation_x_is_rejected():
    assembly, *_ = _build_primary_assembly()
    bad_location = replace(assembly.location, rotation_x=5.0)
    bad_assembly = replace(assembly, location=bad_location)

    with pytest.raises(GeometryValidationError, match="rotation_x"):
        validate_connection_interface_planes_and_normals(bad_assembly)


def test_unsupported_member_rotation_x_is_rejected():
    assembly, *_ = _build_primary_assembly()
    bad_placement = replace(assembly.member_a.placement, rotation_x=5.0)
    bad_member_a = replace(assembly.member_a, placement=bad_placement)
    bad_assembly = replace(assembly, member_a=bad_member_a)

    with pytest.raises(GeometryValidationError, match="rotation_x"):
        validate_connection_interface_planes_and_normals(bad_assembly)
