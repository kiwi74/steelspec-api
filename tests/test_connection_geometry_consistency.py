"""
Milestone 7P — proving that the generated connection geometry is (or
is not) geometrically consistent with the explicitly reviewed
attachment surfaces, closing the seam 7O's own final report
identified between:

    resolved attachment faces (7M/7N)
        and
    generated connection geometry (7K/7L)

A passing result means only that the generated connection geometry
occupies the expected project-space relationship to the explicitly
reviewed attachment surfaces — see
app/cad_engine/connection_geometry_consistency.py's own module
docstring for the full boundary statement (no structural, engineering,
or fabrication-readiness claim of any kind).

FIXTURE PROVENANCE: reuses 7O's exact primary fixture and helper
(`_build_primary_assembly`) unchanged — Member A (REAL-UB-CAD-001,
310UB40, 4000mm) at x=0,y=0,z=0; Member B (L2, 250PFC, 3000mm,
HUMAN-REVIEWED/SUPPLEMENTED length) at x=0,y=0,z=4000; the 7H/7D
reviewed connection fixture; the 7K reviewed ConnectionLocation
(z=3994); the 7N reviewed attachment fixture (REAL-UB-CAD-001->END,
L2->START). Nothing here is claimed to be AI-extracted or
drawing-derived.
"""
import pytest

from app.cad_engine.connection_geometry_consistency import (
    ConnectionGeometryConsistencyResult,
    validate_connection_geometry_against_attachments,
)
from app.cad_engine.errors import GeometryValidationError
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record
from tests.test_two_member_connection import make_member_b_rotated_placement, make_test_reviewed_location_record


# === Primary valid case ===
def test_valid_reviewed_assembly_passes_geometry_consistency():
    assembly, *_ = _build_primary_assembly()

    result = validate_connection_geometry_against_attachments(assembly)

    assert isinstance(result, ConnectionGeometryConsistencyResult)
    assert abs(result.member_a_face_position_mm - 4000.0) < 1e-6
    assert abs(result.member_b_face_position_mm - 4000.0) < 1e-6
    assert abs(result.plate_zmin_mm - 3994.0) < 1e-6
    assert abs(result.plate_zmax_mm - 4006.0) < 1e-6
    # Both resolved faces genuinely fall within the plate's own Z-extent.
    assert result.plate_zmin_mm <= result.member_a_face_position_mm <= result.plate_zmax_mm
    assert result.plate_zmin_mm <= result.member_b_face_position_mm <= result.plate_zmax_mm


# === Case B (the milestone's "critical proof"): wrong reviewed attachment surface ===
def test_case_b_wrong_attachment_surface_for_member_a_is_rejected():
    """
    Member A's attachment is explicitly (wrongly) reviewed as START,
    while the actual connection geometry sits at Member A's END. The
    assembly itself still builds successfully — 7K/7L's contact check
    never reads attachment data, so it cannot notice this mismatch.
    This validator must be the one that catches it, and must NOT
    silently revert START back to END.
    """
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)
    assert assembly.attachment_a.surface_reference == "START"  # not silently reverted

    with pytest.raises(GeometryValidationError, match="REAL-UB-CAD-001"):
        validate_connection_geometry_against_attachments(assembly)


def test_case_b_variant_wrong_attachment_surface_for_member_b_is_rejected():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "END"},  # actual connection is at L2's START
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)
    assert assembly.attachment_b.surface_reference == "END"

    with pytest.raises(GeometryValidationError, match="L2"):
        validate_connection_geometry_against_attachments(assembly)


# === Case A: wrong connection location ===
def test_case_a_wrong_connection_location_is_rejected_upstream_by_7l():
    """
    For this fixture's end-to-end splice topology, a connection
    location far enough to misalign from the attachment faces is
    already rejected by 7K/7L's own contact check before this
    validator would ever run — see
    app/cad_engine/connection_geometry_consistency.py's own module
    docstring for why this is the correct, honestly-reported
    consequence of this fixture's geometry, not a gap in coverage.
    """
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(location_record=make_test_reviewed_location_record(z=2000.0))


# === Case C: move Member B ===
def test_case_c_moved_member_b_is_rejected_upstream_by_7l():
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(member_b_placement=make_member_b_rotated_placement(z=10000.0, rotation_z=0.0))


# === Case D: rotated member (reusing 7L's established arrangement) ===
def test_case_d_rotated_member_b_genuinely_consistent_passes():
    assembly, *_ = _build_primary_assembly(member_b_placement=make_member_b_rotated_placement())

    result = validate_connection_geometry_against_attachments(assembly)
    assert abs(result.member_b_face_position_mm - 4000.0) < 1e-6
    assert result.plate_zmin_mm <= result.member_b_face_position_mm <= result.plate_zmax_mm


# === Case E: connection geometry moved independently (closest equivalent
# via ConnectionLocation, per the milestone's own fallback instruction) ===
def test_case_e_connection_moved_independently_via_location_is_rejected():
    """
    The architecture provides no way to move generated connection
    geometry independently of ConnectionLocation (by design — see
    7K/7L's own docstrings). This tests the closest equivalent: an
    extreme ConnectionLocation change, rejected upstream by 7K/7L's
    own contact check for the same reason as Case A.
    """
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(location_record=make_test_reviewed_location_record(z=9000.0))


# === No automatic repair on rejection ===
def test_no_repair_or_mutation_on_rejection():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    plate_bbox_before = assembly.connection_geometry.plate.val().BoundingBox()
    attachment_a_before = assembly.attachment_a
    location_before = assembly.location

    with pytest.raises(GeometryValidationError):
        validate_connection_geometry_against_attachments(assembly)

    plate_bbox_after = assembly.connection_geometry.plate.val().BoundingBox()
    assert abs(plate_bbox_after.zmin - plate_bbox_before.zmin) < 1e-6
    assert assembly.attachment_a == attachment_a_before  # never swapped START -> END
    assert assembly.location == location_before


# === No mutation on success ===
def test_no_mutation_on_success():
    assembly, *_ = _build_primary_assembly()
    bbox_a_before = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b_before = assembly.member_b.geometry.solid.val().BoundingBox()

    validate_connection_geometry_against_attachments(assembly)

    bbox_a_after = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b_after = assembly.member_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(bbox_b_after.zmin - bbox_b_before.zmin) < 1e-6
