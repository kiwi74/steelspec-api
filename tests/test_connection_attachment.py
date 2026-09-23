"""
Milestone 7M — proving that once a reviewed two-member connection
carries an EXPLICIT attachment reference for each member ("which
surface"), SteelSpec can resolve that intent against actual
project-space CAD geometry without silently substituting another face
or moving anything:

    ConnectionAttachment(member_mark, surface_reference)
        + PlacedMember (project-space, from 7I)
            |
       resolve_connection_attachment_surfaces()
            |
      ResolvedAttachmentSurfaces (actual project-space CadQuery faces)

7M proves only: when the attachment surface for each connected member
is explicitly reviewed/supplied, SteelSpec can preserve that intent
and resolve it against the actual project-space CAD geometry without
silently substituting another face or moving anything. It does NOT
prove drawing-derived attachment-face identification, AI face
inference, automatic primary/secondary member identification,
automatic connection alignment, coping/notching, connection design, or
fabrication readiness.

SCOPE: only END/START (the two axial end-cap faces every extruded
section family already has exactly one of) are supported surface
references — see app/cad_engine/connection_attachment.py's own
docstring for why a TOP/BOTTOM/LEFT/RIGHT taxonomy was deliberately
not invented here.

FIXTURE PROVENANCE — TEST / HUMAN-REVIEWED / SUPPLEMENTED throughout:
reuses 7K/7L's exact established members and placements (Member A:
REAL-UB-CAD-001, 310UB40, 4000mm, at x=0,y=0,z=0; Member B: L2,
250PFC, 3000mm, at x=0,y=0,z=4000 — the same end-to-end splice
arrangement) and 7H's exact reviewed connection identity fixture. The
attachment references themselves (Member A = END, Member B = START)
are this milestone's own explicit reviewed data, chosen to match that
same Z=4000 splice interface — never inferred from geometry, never
attributed to a real drawing.
"""
import pytest

from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_attachment import (
    ConnectionAttachment,
    ResolvedAttachmentSurfaces,
    resolve_connection_attachment_surfaces,
)
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import MemberPlacement, place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher
from tests.test_real_multi_member_connection import make_multi_member_connection_record


def _build(row: dict, matcher) -> tuple:
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    return validated, geometry


def make_member_a_placement(**overrides) -> MemberPlacement:
    base = {"x": 0.0, "y": 0.0, "z": 0.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    base.update(overrides)
    return MemberPlacement(**base)


def make_member_b_placement(**overrides) -> MemberPlacement:
    base = {"x": 0.0, "y": 0.0, "z": 4000.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}
    base.update(overrides)
    return MemberPlacement(**base)


def make_attachment_a(**overrides) -> ConnectionAttachment:
    base = {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"}
    base.update(overrides)
    return ConnectionAttachment(**base)


def make_attachment_b(**overrides) -> ConnectionAttachment:
    base = {"member_mark": "L2", "surface_reference": "START"}
    base.update(overrides)
    return ConnectionAttachment(**base)


def _build_placed(
    member_a_row=None, member_b_row=None,
    member_a_placement=None, member_b_placement=None, matcher=None,
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
    return placed_a, placed_b


def _build_validated_connection(**overrides):
    record = make_multi_member_connection_record(**overrides)
    return real_connection_to_validated_connection(record)


# === Primary: both attachments resolve (Step 21 #1/#2) ===
def test_primary_attachments_resolve_for_both_members():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    attachment_a = make_attachment_a()
    attachment_b = make_attachment_b()

    resolved = resolve_connection_attachment_surfaces(connection, placed_a, placed_b, attachment_a, attachment_b)

    assert isinstance(resolved, ResolvedAttachmentSurfaces)
    assert resolved.member_a_attachment is attachment_a
    assert resolved.member_b_attachment is attachment_b
    assert resolved.member_a_face is not None
    assert resolved.member_b_face is not None


# === Step 9: measured geometry, not merely "face is not None" ===
def test_resolved_faces_are_measured_not_merely_present():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    resolved = resolve_connection_attachment_surfaces(
        connection, placed_a, placed_b, make_attachment_a(), make_attachment_b(),
    )

    face_a = resolved.member_a_face  # UB, END -> project Z=4000 (placement z=0 + length 4000)
    face_b = resolved.member_b_face  # PFC, START -> project Z=4000 (placement z=4000 + local 0)

    assert abs(face_a.BoundingBox().zmin - 4000.0) < 1e-6
    assert abs(face_a.BoundingBox().zmax - 4000.0) < 1e-6
    assert abs(face_a.normalAt().z - 1.0) < 1e-6
    assert abs(face_a.Area() - 6248.0) < 1e-3  # UB cross-sectional area, verified empirically

    assert abs(face_b.BoundingBox().zmin - 4000.0) < 1e-6
    assert abs(face_b.BoundingBox().zmax - 4000.0) < 1e-6
    assert abs(face_b.normalAt().z - (-1.0)) < 1e-6
    assert abs(face_b.Area() - 4460.0) < 1e-3  # PFC cross-sectional area, verified empirically

    # Both faces genuinely coincide with the real Z=4000 splice interface.
    assert abs(face_a.BoundingBox().zmin - face_b.BoundingBox().zmin) < 1e-6


# === Step 10/16: placement sensitivity -- resolved face moves, reference doesn't ===
def test_member_b_placement_change_moves_resolved_face_not_reference():
    placed_a, placed_b_original = _build_placed()
    placed_a2, placed_b_moved = _build_placed(member_b_placement=make_member_b_placement(z=5000.0))

    connection = _build_validated_connection()
    attachment_a = make_attachment_a()
    attachment_b = make_attachment_b()

    resolved_original = resolve_connection_attachment_surfaces(
        connection, placed_a, placed_b_original, attachment_a, attachment_b,
    )
    resolved_moved = resolve_connection_attachment_surfaces(
        connection, placed_a2, placed_b_moved, attachment_a, attachment_b,
    )

    assert abs(resolved_original.member_b_face.BoundingBox().zmin - 4000.0) < 1e-6
    assert abs(resolved_moved.member_b_face.BoundingBox().zmin - 5000.0) < 1e-6

    # The reviewed reference itself is identical -- only the resolved geometry differs.
    assert resolved_original.member_b_attachment == resolved_moved.member_b_attachment == attachment_b


def test_member_a_placement_change_moves_its_face_not_member_b():
    placed_a_orig, placed_b = _build_placed()
    placed_a_moved, placed_b2 = _build_placed(member_a_placement=make_member_a_placement(z=-1000.0))

    connection = _build_validated_connection()
    resolved_orig = resolve_connection_attachment_surfaces(
        connection, placed_a_orig, placed_b, make_attachment_a(), make_attachment_b(),
    )
    resolved_moved = resolve_connection_attachment_surfaces(
        connection, placed_a_moved, placed_b2, make_attachment_a(), make_attachment_b(),
    )

    assert abs(resolved_orig.member_a_face.BoundingBox().zmax - 4000.0) < 1e-6
    assert abs(resolved_moved.member_a_face.BoundingBox().zmax - 3000.0) < 1e-6  # length 4000 + z=-1000

    # Member B's resolved face is unaffected by Member A's placement change.
    assert abs(
        resolved_orig.member_b_face.BoundingBox().zmin - resolved_moved.member_b_face.BoundingBox().zmin
    ) < 1e-6


# === Step 11: member-order independence ===
def test_member_order_independence():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    attachment_a = make_attachment_a()
    attachment_b = make_attachment_b()

    resolved_ab = resolve_connection_attachment_surfaces(connection, placed_a, placed_b, attachment_a, attachment_b)
    resolved_ba = resolve_connection_attachment_surfaces(connection, placed_b, placed_a, attachment_b, attachment_a)

    assert abs(
        resolved_ab.member_a_face.BoundingBox().zmax - resolved_ba.member_b_face.BoundingBox().zmax
    ) < 1e-6
    assert abs(
        resolved_ab.member_b_face.BoundingBox().zmin - resolved_ba.member_a_face.BoundingBox().zmin
    ) < 1e-6


def test_attachment_order_independent_of_member_argument_order():
    """
    Members passed [A, B] but attachments passed [attachment_b,
    attachment_a] (a mismatched argument order) -- matching is still
    correct because it is done by mark, not by list position.
    """
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    attachment_a = make_attachment_a()
    attachment_b = make_attachment_b()

    resolved = resolve_connection_attachment_surfaces(connection, placed_a, placed_b, attachment_b, attachment_a)

    assert resolved.member_a_attachment == attachment_b  # container slot is positional only
    assert resolved.member_b_attachment == attachment_a
    # Face resolution is still correct per the actual mark match.
    assert abs(resolved.member_a_face.BoundingBox().zmin - 4000.0) < 1e-6  # L2's START face
    assert abs(resolved.member_b_face.BoundingBox().zmax - 4000.0) < 1e-6  # UB's END face


# === Step 12: identity isolation ===
def test_identity_isolation_member_mark_and_connection_id_changes():
    matcher = make_multi_member_matcher()
    row_a_renamed = make_member_a_row(mark="RENAMED-A")
    placed_a, placed_b = _build_placed(member_a_row=row_a_renamed, matcher=matcher)
    connection = _build_validated_connection(
        connected_member_marks=["RENAMED-A", "L2"], connection_id="CONN-RENAMED",
    )
    attachment_a = make_attachment_a(member_mark="RENAMED-A")
    attachment_b = make_attachment_b()

    resolved = resolve_connection_attachment_surfaces(connection, placed_a, placed_b, attachment_a, attachment_b)

    assert resolved.member_b_attachment == attachment_b
    assert abs(resolved.member_b_face.BoundingBox().zmin - 4000.0) < 1e-6


# === Step 13: source independence ===
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

    record = make_multi_member_connection_record()
    validated_connection = real_connection_to_validated_connection(record)

    row_a.clear()
    row_b.clear()
    record.clear()

    resolved = resolve_connection_attachment_surfaces(
        validated_connection, placed_a, placed_b, make_attachment_a(), make_attachment_b(),
    )
    assert resolved.member_a_face is not None
    assert resolved.member_b_face is not None


# === Step 14: invalid attachment references are rejected ===
def test_missing_attachment_a_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="missing"):
        resolve_connection_attachment_surfaces(connection, placed_a, placed_b, None, make_attachment_b())


def test_missing_attachment_b_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="missing"):
        resolve_connection_attachment_surfaces(connection, placed_a, placed_b, make_attachment_a(), None)


def test_unknown_member_mark_in_attachment_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="do not match"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b, make_attachment_a(member_mark="UNKNOWN-MARK"), make_attachment_b(),
        )


def test_unknown_surface_reference_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="not a supported attachment surface"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b, make_attachment_a(surface_reference="TOP"), make_attachment_b(),
        )


def test_empty_surface_reference_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="non-blank string"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b, make_attachment_a(surface_reference=""), make_attachment_b(),
        )


def test_blank_surface_reference_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="non-blank string"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b, make_attachment_a(surface_reference="   "), make_attachment_b(),
        )


def test_non_string_surface_reference_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="non-blank string"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b, make_attachment_a(surface_reference=123), make_attachment_b(),
        )


def test_duplicate_attachment_for_same_member_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    with pytest.raises(GeometryValidationError, match="duplicate attachment"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b,
            make_attachment_a(), make_attachment_b(member_mark="REAL-UB-CAD-001"),
        )


def test_attachment_for_member_not_in_connected_members_is_rejected():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()  # names REAL-UB-CAD-001 and L2
    with pytest.raises(GeometryValidationError, match="do not match"):
        resolve_connection_attachment_surfaces(
            connection, placed_a, placed_b,
            make_attachment_a(), make_attachment_b(member_mark="SOME-THIRD-MEMBER"),
        )


# === Step 17: no automatic nearest-face substitution ===
def test_resolver_never_substitutes_nearest_face_for_reviewed_reference():
    """
    Member A is deliberately translated so its START face ends up much
    closer to Member B than its own reviewed END face — proving the
    resolver has no "closest face" logic and always honours the
    explicit reference.
    """
    placed_a, placed_b = _build_placed(member_a_placement=make_member_a_placement(z=3000.0))
    # Member A now spans project Z in [3000, 7000]; its START face
    # (z=3000) is far closer to Member B (Z starts at 4000) than its
    # own reviewed END face (z=7000) is.
    connection = _build_validated_connection()

    resolved = resolve_connection_attachment_surfaces(
        connection, placed_a, placed_b, make_attachment_a(surface_reference="END"), make_attachment_b(),
    )
    assert abs(resolved.member_a_face.BoundingBox().zmax - 7000.0) < 1e-6


# === Step 21 #20: no mutation ===
def test_no_mutation_of_input_geometry():
    placed_a, placed_b = _build_placed()
    connection = _build_validated_connection()
    bbox_a_before = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_before = placed_b.geometry.solid.val().BoundingBox()

    resolve_connection_attachment_surfaces(connection, placed_a, placed_b, make_attachment_a(), make_attachment_b())

    bbox_a_after = placed_a.geometry.solid.val().BoundingBox()
    bbox_b_after = placed_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(bbox_b_after.zmin - bbox_b_before.zmin) < 1e-6
