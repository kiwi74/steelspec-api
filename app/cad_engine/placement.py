"""
Milestone 7I — the smallest possible spatial contract: explicit
placement of an already-generated, local-frame member solid into one
shared project coordinate system.

    section data
        |
    local member geometry (existing generate_geometry(), unchanged)
        |
    explicit placement (this module)
        |
    project-space geometry

ARCHITECTURE (Step 1/3 finding): the existing CAD engine had no
transform/placement abstraction at all — GeneratedMemberGeometry.solid
is a raw cq.Workplane sitting in its own LOCAL frame (footprint
[0, flange_width] x [0, depth] in XY, [0, length_mm] along Z — see
sections.py's profile builders and interface.py's
`profile.extrude(member.length_mm)`). Nothing bakes project
coordinates into build_ub_profile() / build_pfc_profile() /
build_shs_profile() / build_rhs_profile(), and this module doesn't
either — it only ever transforms the finished solid generate_geometry()
already built. The one existing precedent for moving a solid in this
codebase is connections.py's `plate_solid.moved(cq.Location(Vector(0,
0, z_offset)))` — this module reuses that exact idiom
(Shape.moved(cq.Location(...))), not a second, incompatible transform
representation.

ROTATION UNITS: cq.Location(x, y, z, rx, ry, rz)'s rotation arguments
are DEGREES — verified empirically (a `rotation_z=90` Location applied
to a [0,90]x[0,250] rectangular footprint swapped its X/Y bounding-box
extents exactly as a 90 degree rotation should, and left volume/solid
count unchanged) before relying on it here, not assumed from the
OCCT/CadQuery docs alone.

SCOPE BOUNDARY (Step 13/14) — deliberately narrow: `place_member_geometry()`
refuses to place a member that has any resolved connection
(`geometry.connection_count > 0`). A connection's plate/holes/hardware
are separate objects from `solid` (see interface.py's
GeneratedConnection — "Deliberately NOT part of `solid`"), so moving
`solid` alone while leaving those stale would silently desynchronise a
connection's own geometry from the member it's supposed to sit on.
Rather than solve that here (which would mean deciding how a
connection's geometry participates in a multi-member assembly — this
milestone's explicit non-goal, see its own docstring), this module
simply refuses the input. Placing members with connections attached is
future work, not a scope this module pretends to already cover.

This module does NOT infer placement from a connection, a member mark,
drawing order, source page, section type, member length, or the
connected-member list — see app/cad_engine/real_connection_adapter.py
and tests/test_real_multi_member_connection.py for the (deliberately
unrelated) member-identity-association contract. It does NOT attempt
automatic structural assembly, drawing-based placement inference,
grid-to-coordinate conversion, clash/intersection detection, or
connection geometry generation between two members — see
tests/test_real_multi_member_placement.py's module docstring for the
full list of explicitly out-of-scope claims this milestone does not
make.
"""
import math
from dataclasses import dataclass, replace
from typing import Any

import cadquery as cq

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import GeneratedMemberGeometry

__all__ = ["MemberPlacement", "place_member_geometry"]


@dataclass(frozen=True)
class MemberPlacement:
    """
    A minimal, explicit rigid-body transform: translation (x, y, z) in
    mm and rotation (rotation_x, rotation_y, rotation_z) in degrees,
    applied about the member's own local origin before translating —
    matching cq.Location(x, y, z, rx, ry, rz)'s own composition order
    (verified empirically, see module docstring).

    Every field must be an explicit, real project-space value supplied
    by the caller — this dataclass has no notion of "where a member
    probably goes" and never will; see place_member_geometry() for the
    validation that keeps obviously-invalid values (non-numeric,
    boolean, NaN/infinite) from ever reaching CAD.

    This is a plain data container, not a general transformation
    framework — deliberately no matrix composition, no multi-step
    transform chains, no coordinate-system abstraction beyond "one
    shared project frame". If the project ever needs more than a
    single explicit rigid-body placement per member, that is a future
    milestone's decision to make, not an assumption baked in here.
    """
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rotation_x: float = 0.0
    rotation_y: float = 0.0
    rotation_z: float = 0.0


def _require_finite_numeric(value: Any, field_name: str, mark: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryValidationError(
            f"Member '{mark}': placement field '{field_name}' must be a real number "
            f"(got {value!r})."
        )
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise GeometryValidationError(
            f"Member '{mark}': placement field '{field_name}' must be finite, not "
            f"NaN/infinite (got {value})."
        )
    return value


def place_member_geometry(
    geometry: GeneratedMemberGeometry, placement: MemberPlacement
) -> GeneratedMemberGeometry:
    """
    Transforms `geometry.solid` (and only `geometry.solid` — see
    module docstring's scope boundary on connections) into project
    space per `placement`, returning a NEW GeneratedMemberGeometry with
    every other field copied unchanged (mark, section_name,
    section_family, length_mm, connection_count, connection_types,
    hardware, connections) — this never modifies the existing
    GeneratedMemberGeometry contract, it only constructs another
    instance of it via dataclasses.replace().

    Raises GeometryValidationError when:
      - any placement field is non-numeric, boolean, NaN, or infinite;
      - `geometry` has one or more resolved connections
        (connection_count > 0) — placing a connected member's
        secondary geometry (plate/holes/hardware) consistently is out
        of scope for this milestone (see module docstring).

    A pure translation (all rotations 0) changes only the solid's
    location — section dimensions, length, volume, and topology
    (solid count) are preserved exactly, since `.moved()` is a rigid
    transform, never a reshape.
    """
    if geometry.connection_count > 0:
        raise GeometryValidationError(
            f"Member '{geometry.mark}' has {geometry.connection_count} resolved connection(s). "
            "place_member_geometry() only places bare member geometry — placing a member with "
            "attached connection geometry (plate/holes/hardware) is out of scope for this "
            "milestone; see app/cad_engine/placement.py's module docstring."
        )

    x = _require_finite_numeric(placement.x, "x", geometry.mark)
    y = _require_finite_numeric(placement.y, "y", geometry.mark)
    z = _require_finite_numeric(placement.z, "z", geometry.mark)
    rx = _require_finite_numeric(placement.rotation_x, "rotation_x", geometry.mark)
    ry = _require_finite_numeric(placement.rotation_y, "rotation_y", geometry.mark)
    rz = _require_finite_numeric(placement.rotation_z, "rotation_z", geometry.mark)

    location = cq.Location(x, y, z, rx, ry, rz)
    placed_solid = cq.Workplane(obj=geometry.solid.val().moved(location))

    return replace(geometry, solid=placed_solid)
