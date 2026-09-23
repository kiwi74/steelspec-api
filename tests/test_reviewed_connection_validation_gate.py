"""
Milestone 7R — proving the validation GATE runs every currently-
required geometric consistency check, in the correct single order,
without duplicating any lower-level validator's own algorithm.

A passing gate result means only: geometric consistency of the
explicitly reviewed connection assembly has been validated against the
currently-supported CAD geometry rules — see
app/cad_engine/reviewed_connection_validation_gate.py's own module
docstring for the full boundary statement (no structural, engineering,
or fabrication-readiness claim of any kind).

FIXTURE PROVENANCE: reuses 7O/7P/7Q's exact primary fixture and helper
(`_build_primary_assembly`) unchanged — Member A (REAL-UB-CAD-001,
310UB40, 4000mm) at x=0,y=0,z=0; Member B (L2, 250PFC, 3000mm,
HUMAN-REVIEWED/SUPPLEMENTED length) at x=0,y=0,z=4000; the 7H/7D
reviewed connection fixture (180x250x12mm plate, 4xO22mm holes); the
7K reviewed ConnectionLocation (z=3994); the 7N reviewed attachment
fixture (REAL-UB-CAD-001->END, L2->START). Nothing here is claimed to
be AI-extracted or drawing-derived.
"""
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.reviewed_connection_validation_gate import (
    VALIDATED_LAYERS,
    ReviewedConnectionValidationResult,
    validate_reviewed_connection_assembly,
)
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record
from tests.test_two_member_connection import make_test_reviewed_location_record


# === Valid assembly passes the complete gate ===
def test_valid_assembly_passes_complete_gate():
    assembly, *_ = _build_primary_assembly()

    result = validate_reviewed_connection_assembly(assembly)

    assert isinstance(result, ReviewedConnectionValidationResult)
    assert result.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert result.layers_validated == VALIDATED_LAYERS
    assert len(result.layers_validated) == 3
    assert abs(result.interface_result.member_a_normal_dot - (-1.0)) < 1e-9
    assert abs(result.interface_result.member_b_normal_dot - (-1.0)) < 1e-9


# === 7P failure is rejected by the gate ===
def test_7p_failure_rejected_by_gate():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    with pytest.raises(GeometryValidationError, match="REAL-UB-CAD-001"):
        validate_reviewed_connection_assembly(assembly)


# === 7Q failure: documented limitation, plus a direct proof of the
# underlying normal-inconsistency fact ===
def test_7q_normal_failure_documented_limitation_and_direct_proof():
    """
    Per 7Q's own honest finding (see app/cad_engine/
    connection_interface_consistency.py's module docstring): given the
    current two-valued (END/START) attachment vocabulary and this
    fixture's topology, every currently-constructible wrong-attachment
    case already fails 7P's position check (composed first inside 7Q,
    and thus inside this gate) before 7Q's own normal check would run.
    A clean end-to-end GATE failure uniquely attributable to 7Q's
    normal check (with 7P passing) cannot be constructed today without
    inventing unsupported geometry — documented here rather than
    fabricated.

    This test confirms both halves honestly: the gate DOES reject this
    case (via 7P), and the underlying normal signal is ALSO
    independently wrong for the same case, measured directly rather
    than through the full gate.
    """
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},  # wrong: actual interface is END
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    with pytest.raises(GeometryValidationError):
        validate_reviewed_connection_assembly(assembly)

    wrong_face_a = assembly.resolved_surfaces.member_a_face
    plate_lo_face = assembly.connection_geometry.plate.faces("<Z").vals()[0]
    dot = wrong_face_a.normalAt().dot(plate_lo_face.normalAt())
    assert dot > -1.0 + 0.01  # same direction, not opposing -> independently inconsistent


# === Upstream 7K/7L failure prevents the assembly from ever existing ===
def test_upstream_7k7l_failure_prevents_assembly_from_existing():
    """
    A 7K/7L-invalid configuration is rejected during assembly
    construction itself (build_two_member_connection_assembly()'s own
    contact check) -- there is no assembly object to ever pass to this
    gate. The gate is structurally unreachable for such a case, not
    merely expected to reject it.
    """
    with pytest.raises(GeometryValidationError, match="does not place the connection plate in contact"):
        _build_primary_assembly(location_record=make_test_reviewed_location_record(z=2000.0))


# === No mutation ===
def test_no_mutation():
    assembly, *_ = _build_primary_assembly()
    bbox_a_before = assembly.member_a.geometry.solid.val().BoundingBox()
    attachment_a_before = assembly.attachment_a
    location_before = assembly.location

    validate_reviewed_connection_assembly(assembly)

    bbox_a_after = assembly.member_a.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert assembly.attachment_a == attachment_a_before
    assert assembly.location == location_before


# === Repeated validation ===
def test_repeated_validation_same_result_no_mutation():
    assembly, *_ = _build_primary_assembly()

    result_1 = validate_reviewed_connection_assembly(assembly)
    result_2 = validate_reviewed_connection_assembly(assembly)

    assert result_1.connection_id == result_2.connection_id
    assert result_1.layers_validated == result_2.layers_validated
    assert abs(
        result_1.interface_result.member_a_normal_dot - result_2.interface_result.member_a_normal_dot
    ) < 1e-12
    assert abs(
        result_1.interface_result.member_a_face_position_mm
        - result_2.interface_result.member_a_face_position_mm
    ) < 1e-9


# === Order independence ===
def test_order_independence():
    assembly_default, *_ = _build_primary_assembly()
    detail_record_swapped = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "L2", "surface_reference": "START"},
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
    ])
    assembly_swapped, *_ = _build_primary_assembly(detail_record=detail_record_swapped)

    result_default = validate_reviewed_connection_assembly(assembly_default)
    result_swapped = validate_reviewed_connection_assembly(assembly_swapped)

    assert result_default.connection_id == result_swapped.connection_id
    assert abs(
        result_default.interface_result.member_a_normal_dot - result_swapped.interface_result.member_a_normal_dot
    ) < 1e-9
    assert abs(
        result_default.interface_result.member_b_normal_dot - result_swapped.interface_result.member_b_normal_dot
    ) < 1e-9
