"""
Milestone 7N — proving that a HUMAN-REVIEWED connection-detail record
explicitly carrying attachment surfaces can be converted into the
existing 7M ConnectionAttachment contract and resolved against actual
project-space CAD geometry, with no inference and no loss of
provenance:

    reviewed connection-detail record (TEST / HUMAN-REVIEWED / SUPPLEMENTED)
        -> reviewed_connection_detail_to_attachments()
        -> list[ConnectionAttachment]
        -> existing 7M resolve_connection_attachment_surfaces() (unchanged)
        -> actual project-space CadQuery faces

7N proves only: when a connection-detail reviewer explicitly supplies
the attachment surface for each connected member, that reviewed
information can be converted into the existing CAD attachment contract
and resolved against actual project-space geometry without inference
or loss of provenance. It does NOT prove that AI can identify
attachment surfaces from drawings, that SteelSpec can automatically
choose END vs START, or any drawing-to-3D reconstruction — see
app/cad_engine/reviewed_connection_attachment.py's own docstring for
the exact, directly-quoted real AI extraction schema this milestone
confirms has NO attachment-surface concept at all.

FIXTURE PROVENANCE: the reviewed connection-detail record's
`attachments` field is entirely TEST / HUMAN-REVIEWED / SUPPLEMENTED
data — it does not exist anywhere in the real AI extraction schema
(see module docstring on app/cad_engine/reviewed_connection_attachment.py).
It is never labelled AI-detected, drawing-derived, or Arkles-derived.
Member A/B, their placements, and the connection identity all reuse
the exact same established 7K/7L/7M fixtures.
"""
import pytest

from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_attachment import resolve_connection_attachment_surfaces
from app.cad_engine.connection_location import real_connection_location_to_connection_location
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_attachment import reviewed_connection_detail_to_attachments
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher
from tests.test_real_multi_member_connection import make_multi_member_connection_record

_CONNECTED_MARKS = ["REAL-UB-CAD-001", "L2"]


def _build(row: dict, matcher) -> tuple:
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    return validated, geometry


def _build_placed_members(member_a_placement=None, member_b_placement=None, matcher=None):
    matcher = matcher or make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)
    placement_a = member_a_placement or make_member_a_placement()
    placement_b = member_b_placement or make_member_b_placement()
    placed_a = PlacedMember(
        mark="REAL-UB-CAD-001", geometry=place_member_geometry(geometry_a, placement_a), placement=placement_a,
    )
    placed_b = PlacedMember(
        mark="L2", geometry=place_member_geometry(geometry_b, placement_b), placement=placement_b,
    )
    return placed_a, placed_b


def make_reviewed_connection_detail_record(**overrides) -> dict:
    record = {
        "connection_id": "CONN-REAL-UB-CAD-001-L2",
        "review_status": "approved",
        "attachments": [
            {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
            {"member_mark": "L2", "surface_reference": "START"},
        ],
    }
    record.update(overrides)
    return record


def _attachments_by_mark(attachments):
    return {a.member_mark: a.surface_reference for a in attachments}


# === Primary chain: reviewed record -> ConnectionAttachment -> 7M resolver -> measured CAD ===
def test_primary_reviewed_record_resolves_through_7m_to_actual_cad_faces():
    placed_a, placed_b = _build_placed_members()
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())

    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    assert len(attachments) == 2
    attachment_a = next(a for a in attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in attachments if a.member_mark == "L2")

    resolved = resolve_connection_attachment_surfaces(
        validated_connection, placed_a, placed_b, attachment_a, attachment_b,
    )

    face_a, face_b = resolved.member_a_face, resolved.member_b_face
    assert abs(face_a.normalAt().z - 1.0) < 1e-6        # END -> +Z
    assert abs(face_a.BoundingBox().zmax - 4000.0) < 1e-6
    assert abs(face_b.normalAt().z - (-1.0)) < 1e-6     # START -> -Z
    assert abs(face_b.BoundingBox().zmin - 4000.0) < 1e-6


# === Step 9: attachment list order independence ===
def test_attachment_list_order_independence():
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())

    record_ab = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    record_ba = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "L2", "surface_reference": "START"},
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
    ])

    attachments_ab = reviewed_connection_detail_to_attachments(record_ab, validated_connection.connected_members)
    attachments_ba = reviewed_connection_detail_to_attachments(record_ba, validated_connection.connected_members)

    expected = {"REAL-UB-CAD-001": "END", "L2": "START"}
    assert _attachments_by_mark(attachments_ab) == expected
    assert _attachments_by_mark(attachments_ba) == expected


# === Step 8: no second connection identity ===
def test_attachments_carry_no_connection_identity():
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    for attachment in attachments:
        assert not hasattr(attachment, "connection_id")
        assert not hasattr(attachment, "connected_members")


# === Step 15 adversarial inference cases ===
def test_case_a_reviewed_face_selected_even_when_not_nearest():
    """Member A translated so its unreviewed START face is far closer
    to Member B than its own reviewed END face — the resolver must
    still honour the explicit review."""
    placed_a, placed_b = _build_placed_members(member_a_placement=make_member_a_placement(z=3000.0))
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    attachment_a = next(a for a in attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in attachments if a.member_mark == "L2")

    resolved = resolve_connection_attachment_surfaces(
        validated_connection, placed_a, placed_b, attachment_a, attachment_b,
    )
    assert abs(resolved.member_a_face.BoundingBox().zmax - 7000.0) < 1e-6  # the reviewed END face


def test_case_b_explicit_attachment_overrides_generic_connection_position():
    """The generic ValidatedConnection.position field says END, but the
    reviewed attachment explicitly says Member B = START — the
    explicit attachment must win, never the generic position field."""
    placed_a, placed_b = _build_placed_members()
    connection_record = make_multi_member_connection_record(position="END")
    validated_connection = real_connection_to_validated_connection(connection_record)
    assert validated_connection.position == "END"

    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    attachment_a = next(a for a in attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in attachments if a.member_mark == "L2")
    assert attachment_b.surface_reference == "START"

    resolved = resolve_connection_attachment_surfaces(
        validated_connection, placed_a, placed_b, attachment_a, attachment_b,
    )
    assert abs(resolved.member_b_face.normalAt().z - (-1.0)) < 1e-6  # START honoured, not overridden


def test_case_c_member_a_listed_first_with_start_attachment():
    """Member A listed first in the reviewed record but with surface_reference=START
    — the adapter must not assume 'listed first' means END."""
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "END"},
    ])
    attachments = reviewed_connection_detail_to_attachments(record, validated_connection.connected_members)
    by_mark = _attachments_by_mark(attachments)
    assert by_mark["REAL-UB-CAD-001"] == "START"
    assert by_mark["L2"] == "END"


def test_case_d_member_b_listed_second_with_end_attachment():
    """Member B listed second but with surface_reference=END — the
    adapter must not assume 'listed second' means START."""
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "END"},
    ])
    attachments = reviewed_connection_detail_to_attachments(record, validated_connection.connected_members)
    assert _attachments_by_mark(attachments)["L2"] == "END"


def test_case_e_member_section_and_length_change_does_not_affect_attachment():
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    detail_record = make_reviewed_connection_detail_record()
    attachments_before = reviewed_connection_detail_to_attachments(
        detail_record, validated_connection.connected_members,
    )

    matcher = make_multi_member_matcher()
    _build(make_member_b_row(section_name="310UB40", section_family="UB", length_mm=999), matcher)

    attachments_after = reviewed_connection_detail_to_attachments(
        detail_record, validated_connection.connected_members,
    )
    assert attachments_before == attachments_after


# === Step 12: source independence ===
def test_source_independence_after_clearing_raw_record():
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)

    detail_record.clear()

    assert _attachments_by_mark(attachments) == {"REAL-UB-CAD-001": "END", "L2": "START"}


# === Step 13: placement independence ===
def test_placement_independence_attachment_unchanged_face_moves():
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    attachment_a = next(a for a in attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in attachments if a.member_mark == "L2")

    placed_a1, placed_b1 = _build_placed_members()
    placed_a2, placed_b2 = _build_placed_members(member_b_placement=make_member_b_placement(z=6000.0))

    resolved1 = resolve_connection_attachment_surfaces(
        validated_connection, placed_a1, placed_b1, attachment_a, attachment_b,
    )
    resolved2 = resolve_connection_attachment_surfaces(
        validated_connection, placed_a2, placed_b2, attachment_a, attachment_b,
    )

    assert abs(resolved1.member_b_face.BoundingBox().zmin - 4000.0) < 1e-6
    assert abs(resolved2.member_b_face.BoundingBox().zmin - 6000.0) < 1e-6
    assert resolved1.member_b_attachment == resolved2.member_b_attachment == attachment_b


# === Step 14: connection-location independence ===
def test_connection_location_independence():
    placed_a, placed_b = _build_placed_members()
    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    detail_record = make_reviewed_connection_detail_record()
    attachments = reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members)
    attachment_a = next(a for a in attachments if a.member_mark == "REAL-UB-CAD-001")
    attachment_b = next(a for a in attachments if a.member_mark == "L2")

    location_record = make_multi_member_connection_record()
    location_record.update({
        "x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0,
    })
    location_1 = real_connection_location_to_connection_location(location_record)
    location_record["z"] = 5000.0
    location_2 = real_connection_location_to_connection_location(location_record)
    assert location_1 != location_2  # genuinely different locations

    # resolve_connection_attachment_surfaces() takes no ConnectionLocation
    # at all -- structurally independent of it by construction.
    resolved_1 = resolve_connection_attachment_surfaces(
        validated_connection, placed_a, placed_b, attachment_a, attachment_b,
    )
    resolved_2 = resolve_connection_attachment_surfaces(
        validated_connection, placed_a, placed_b, attachment_a, attachment_b,
    )
    assert resolved_1.member_a_face.BoundingBox().zmax == resolved_2.member_a_face.BoundingBox().zmax


# === Step 7: 12 required rejection cases ===
def test_missing_attachments_key_is_rejected():
    record = make_reviewed_connection_detail_record()
    del record["attachments"]
    with pytest.raises(GeometryValidationError, match="attachments is"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_empty_attachment_list_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[])
    with pytest.raises(GeometryValidationError, match="attachments is"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_missing_member_mark_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"surface_reference": "END"}, {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="member_mark"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_blank_member_mark_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "   ", "surface_reference": "END"}, {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="member_mark"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_missing_surface_reference_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001"}, {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="surface_reference"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_blank_surface_reference_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "  "},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="surface_reference"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_non_string_member_mark_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": 123, "surface_reference": "END"}, {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="member_mark"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_non_string_surface_reference_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": 1}, {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="surface_reference"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_duplicate_member_attachment_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="duplicate attachment"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_attachment_for_member_not_in_connected_marks_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
        {"member_mark": "UNRELATED-MARK", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="not among"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_unknown_surface_reference_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "TOP"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="unsupported"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_malformed_attachment_entry_is_rejected():
    record = make_reviewed_connection_detail_record(
        attachments=["not-a-dict", {"member_mark": "L2", "surface_reference": "START"}],
    )
    with pytest.raises(GeometryValidationError, match="malformed"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)


def test_incomplete_attachment_coverage_is_rejected():
    record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
    ])
    with pytest.raises(GeometryValidationError, match="do not exactly cover"):
        reviewed_connection_detail_to_attachments(record, _CONNECTED_MARKS)
