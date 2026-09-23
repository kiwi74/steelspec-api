"""
Milestone 7J — proving that once a reviewed connection's project-space
location is explicitly supplied, SteelSpec can validate and preserve
it independently from member identity, member placement, and member
geometry — never inferring it from any of those.

    REAL MEMBER A -> 7A -> ValidatedMember A -> 7I placement -> Project Member A
    REAL MEMBER B -> 7A -> ValidatedMember B -> 7I placement -> Project Member B
    HUMAN-REVIEWED CONNECTION -> 7D/7H -> ValidatedConnection
        + real_connection_location_to_connection_location() -> ConnectionLocation
        -> Project Assembly Metadata (identity + location, never fused, never CAD)

7J does NOT prove that SteelSpec can determine connection coordinates
from a drawing. It proves only that once a connection location has
been explicitly reviewed/supplied, SteelSpec can preserve that
location independently from member identity and member placement,
ready for a later assembly-geometry milestone.

NOT DONE HERE, DELIBERATELY: no connection CAD geometry is generated;
no member is repositioned; no plate/bolt/weld location, midpoint,
intersection, or clash calculation is performed anywhere in this file
or in app/cad_engine/connection_location.py. Every test below reads
its ConnectionLocation from a plain reviewed dict's explicit x/y/z/
rotation_* fields and nothing else.

FIXTURE PROVENANCE: the reviewed connection record reuses 7H's exact
multi-member fixture (tests.test_real_multi_member_connection.
make_multi_member_connection_record — connected_member_marks =
["REAL-UB-CAD-001", "L2"], the same two real members established in
7G/7I), extended with an explicit location. The location values
(x=500, y=0, z=4000, rotations=0) are TEST / REVIEWED CONNECTION
LOCATION coordinates — synthetic values chosen only to exercise the
validation and independence properties below. They are not derived
from Arkles, from any real drawing, from either member's length or
placement, from the connection's own END/START position, from its
plate/hole geometry, or from any member intersection — and must never
be read as real project coordinates.
"""
import pytest

from app.cad_engine.assembly import GeneratedAssembly, PlacedMember
from app.cad_engine.connection_location import (
    ConnectionLocation,
    real_connection_location_to_connection_location,
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


def make_reviewed_connection_location_record(**overrides) -> dict:
    record = make_multi_member_connection_record()
    record.update({
        "x": 500.0, "y": 0.0, "z": 4000.0,
        "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0,
    })
    record.update(overrides)
    return record


# === Step 4/6: the primary reviewed connection has an explicit,
# validated project-space location ===
def test_primary_reviewed_connection_has_explicit_location():
    record = make_reviewed_connection_location_record()
    location = real_connection_location_to_connection_location(record)

    assert isinstance(location, ConnectionLocation)
    assert location.x == 500.0
    assert location.y == 0.0
    assert location.z == 4000.0
    assert location.rotation_x == 0.0
    assert location.rotation_y == 0.0
    assert location.rotation_z == 0.0

    # Step 8: identity stays on the separate ValidatedConnection —
    # never duplicated onto ConnectionLocation.
    validated_connection = real_connection_to_validated_connection(record)
    assert validated_connection.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2"}
    assert not hasattr(location, "connection_id")
    assert not hasattr(location, "connected_members")


def test_missing_x_key_entirely_absent_is_rejected():
    record = make_reviewed_connection_location_record()
    del record["x"]
    with pytest.raises(GeometryValidationError, match="'x'"):
        real_connection_location_to_connection_location(record)


# === Step 6: validation — reject, never default to zero ===
_INVALID_LOCATION_CASES = [
    ("missing_x", {"x": None}),
    ("missing_y", {"y": None}),
    ("missing_z", {"z": None}),
    ("non_numeric_x", {"x": "500"}),
    ("boolean_x", {"x": True}),
    ("nan_z", {"z": float("nan")}),
    ("infinite_z", {"z": float("inf")}),
    ("non_numeric_rotation_z", {"rotation_z": "90"}),
    ("boolean_rotation_z", {"rotation_z": False}),
]


@pytest.mark.parametrize(
    "case_id,overrides", _INVALID_LOCATION_CASES, ids=[c[0] for c in _INVALID_LOCATION_CASES],
)
def test_invalid_or_missing_location_values_are_rejected(case_id, overrides):
    record = make_reviewed_connection_location_record(**overrides)
    with pytest.raises(GeometryValidationError):
        real_connection_location_to_connection_location(record)


# === Step 7: source independence ===
def test_source_independence_location_survives_record_clearing():
    record = make_reviewed_connection_location_record()
    location = real_connection_location_to_connection_location(record)
    record.clear()
    assert location.x == 500.0
    assert location.z == 4000.0


# === Step 9: independent of connected-member list order ===
def test_location_independent_of_connected_member_order():
    record_ab = make_reviewed_connection_location_record(
        connected_member_marks=["REAL-UB-CAD-001", "L2"],
    )
    record_ba = make_reviewed_connection_location_record(
        connected_member_marks=["L2", "REAL-UB-CAD-001"],
    )

    location_ab = real_connection_location_to_connection_location(record_ab)
    location_ba = real_connection_location_to_connection_location(record_ba)
    assert location_ab == location_ba


# === Step 10: independent of member placement (source-authoritative,
# not derived from where members currently sit) ===
def test_location_independent_of_member_placement():
    matcher = make_multi_member_matcher()
    _, geometry_b = _build(make_member_b_row(), matcher)

    record = make_reviewed_connection_location_record()
    location_before = real_connection_location_to_connection_location(record)

    place_member_geometry(geometry_b, MemberPlacement(x=500, y=0, z=0))
    place_member_geometry(geometry_b, MemberPlacement(x=1000, y=0, z=0))

    location_after = real_connection_location_to_connection_location(record)
    assert location_after == location_before
    assert location_after.x == 500.0  # its own explicit value, unrelated to Member B's X


# === Step 12: coexists with the existing, unmodified 7I assembly —
# associated externally, never bolted onto GeneratedAssembly itself ===
def test_location_coexists_with_7i_assembly_without_modifying_it():
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

    record = make_reviewed_connection_location_record()
    validated_connection = real_connection_to_validated_connection(record)
    location = real_connection_location_to_connection_location(record)
    connection_locations = {validated_connection.connection_id: location}  # plain external mapping

    assert len(assembly.members) == 2
    assert {m.mark for m in assembly.members} == {"REAL-UB-CAD-001", "L2"}
    assert connection_locations[validated_connection.connection_id].x == 500.0

    # Changing one does not mutate the others.
    assembly.members.append(PlacedMember(mark="EXTRA", geometry=placed_a, placement=placement_a))
    assert connection_locations[validated_connection.connection_id].x == 500.0
    assert assembly.members[0].placement.x == 0.0
    assert assembly.members[1].placement.x == 500.0


# === Step 13: proof the connection is not attached/fused to either member ===
def test_connection_not_fused_or_attached_to_either_member():
    matcher = make_multi_member_matcher()
    validated_a = real_member_to_validated_member(make_member_a_row(), matcher)
    validated_b = real_member_to_validated_member(make_member_b_row(), matcher)

    assert validated_a.connection_refs == []
    assert validated_b.connection_refs == []

    geometry_a = generate_geometry(validated_a)
    geometry_b = generate_geometry(validated_b)
    assert geometry_a.connection_count == 0
    assert geometry_b.connection_count == 0

    record = make_reviewed_connection_location_record()
    real_connection_location_to_connection_location(record)  # never touches either geometry object

    assert geometry_a.connection_count == 0
    assert geometry_b.connection_count == 0


# === Step 14: adversarial — location changes ONLY with its own explicit input ===
def test_location_unaffected_by_connection_id_change():
    record_1 = make_reviewed_connection_location_record(connection_id="CONN-ONE")
    record_2 = make_reviewed_connection_location_record(connection_id="CONN-TWO")
    location_1 = real_connection_location_to_connection_location(record_1)
    location_2 = real_connection_location_to_connection_location(record_2)
    assert location_1 == location_2


def test_location_unaffected_by_member_section_or_length_change():
    matcher = make_multi_member_matcher()
    record = make_reviewed_connection_location_record()
    location_before = real_connection_location_to_connection_location(record)

    _build(make_member_b_row(section_name="310UB40", section_family="UB", length_mm=999), matcher)

    location_after = real_connection_location_to_connection_location(record)
    assert location_after == location_before


def test_location_unaffected_by_member_conversion_order():
    matcher = make_multi_member_matcher()
    record = make_reviewed_connection_location_record()

    real_member_to_validated_member(make_member_b_row(), matcher)
    real_member_to_validated_member(make_member_a_row(), matcher)
    location_1 = real_connection_location_to_connection_location(record)

    real_member_to_validated_member(make_member_a_row(), matcher)
    real_member_to_validated_member(make_member_b_row(), matcher)
    location_2 = real_connection_location_to_connection_location(record)

    assert location_1 == location_2


# === Step 15: parameter sensitivity — each field changes independently ===
def test_x_change_affects_only_x():
    loc_before = real_connection_location_to_connection_location(
        make_reviewed_connection_location_record(x=500.0),
    )
    loc_after = real_connection_location_to_connection_location(
        make_reviewed_connection_location_record(x=750.0),
    )

    assert loc_before.x == 500.0
    assert loc_after.x == 750.0
    assert loc_after.y == loc_before.y
    assert loc_after.z == loc_before.z
    assert loc_after.rotation_x == loc_before.rotation_x
    assert loc_after.rotation_y == loc_before.rotation_y
    assert loc_after.rotation_z == loc_before.rotation_z


def test_z_change_affects_only_z():
    loc_before = real_connection_location_to_connection_location(
        make_reviewed_connection_location_record(z=4000.0),
    )
    loc_after = real_connection_location_to_connection_location(
        make_reviewed_connection_location_record(z=4250.0),
    )

    assert loc_before.z == 4000.0
    assert loc_after.z == 4250.0
    assert loc_after.x == loc_before.x
    assert loc_after.y == loc_before.y
    assert loc_after.rotation_x == loc_before.rotation_x
    assert loc_after.rotation_y == loc_before.rotation_y
    assert loc_after.rotation_z == loc_before.rotation_z


def test_rotation_change_affects_only_that_rotation_field():
    loc_before = real_connection_location_to_connection_location(
        make_reviewed_connection_location_record(rotation_z=0.0),
    )
    loc_after = real_connection_location_to_connection_location(
        make_reviewed_connection_location_record(rotation_z=45.0),
    )

    assert loc_before.rotation_z == 0.0
    assert loc_after.rotation_z == 45.0
    assert loc_after.x == loc_before.x
    assert loc_after.y == loc_before.y
    assert loc_after.z == loc_before.z
    assert loc_after.rotation_x == loc_before.rotation_x
    assert loc_after.rotation_y == loc_before.rotation_y


# === Oversized Python integers (final 7V correction): rejected, never an OverflowError ===
@pytest.mark.parametrize("huge", [pytest.param(10**400, id="pos_1e400"), pytest.param(-(10**400), id="neg_1e400"), pytest.param(10**309, id="pos_1e309"), pytest.param(10**5000, id="pos_1e5000")])
@pytest.mark.parametrize("field_name", ["x", "y", "z", "rotation_x", "rotation_y", "rotation_z"])
def test_oversized_integer_coordinate_is_rejected_by_the_location_adapter(field_name, huge):
    record = {"connection_id": "CONN-OVERSIZED", "x": 0, "y": 0, "z": 0,
              "rotation_x": 0, "rotation_y": 0, "rotation_z": 0}
    record[field_name] = huge
    with pytest.raises(GeometryValidationError, match=field_name):  # not OverflowError
        real_connection_location_to_connection_location(record)


def test_large_but_finite_integer_coordinate_is_kept_exactly_not_capped():
    record = {"x": 10**308, "y": 0, "z": 0, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0}
    assert real_connection_location_to_connection_location(record).x == float(10**308)
