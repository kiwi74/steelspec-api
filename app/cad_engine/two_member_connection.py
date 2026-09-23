"""
Milestone 7K — the smallest possible representation of a reviewed
connection's geometry, positioned in project space between two
already-positioned members, without moving either member or the
connection to make it "work":

    ValidatedConnection + ConnectionLocation
        + PlacedMember A (project-space geometry, from 7I)
        + PlacedMember B (project-space geometry, from 7I)
            |
       build_two_member_connection_assembly()
            |
      GeneratedConnectionAssembly

STEP 1 FINDINGS (architecture inspection):

  - GeneratedMemberGeometry.solid is a raw cq.Workplane in the
    member's own LOCAL frame; app.cad_engine.placement.MemberPlacement
    transforms it into project space via `.moved(cq.Location(...))`,
    returning a NEW GeneratedMemberGeometry (interface.py/placement.py
    unchanged, not touched by this module).
  - GeneratedAssembly (app/cad_engine/assembly.py) stores a plain list
    of PlacedMember(mark, geometry, placement) — each member's project
    geometry and the placement that produced it, side by side.
  - The EXISTING END_PLATE builder, connections.py's
    attach_end_plate(), assumes single-member ownership: it takes ONE
    member_solid (in that member's own LOCAL frame, before any 7I
    placement is applied), computes a plate offset purely from that
    member's own `connection.position` ("START"/"END") and its own
    `member_length_mm`, and unions the plate directly onto that one
    solid. There is no concept in attach_end_plate() of "this plate
    also relates to a second, independently-positioned member" — reusing
    it as-is for a two-member case would produce the wrong geometry
    (it would place the plate relative to whichever single member
    happened to be passed in, ignoring the other member and any
    project-space ConnectionLocation entirely).
  - What IS safely reusable, and reused here without duplication, is
    connections.py's pure LOCAL plate-shape algorithm
    (build_end_plate_with_holes() — builds the [0,width]x[0,height]x
    [0,thickness] plate with its hole voids, with no position baked
    in) and its hole-measurement helpers (_compute_hole_centers(),
    _ordered_plate_hole_records()). This module positions that same
    local plate into PROJECT space using the explicit, reviewed
    ConnectionLocation (Milestone 7J) instead of attach_end_plate()'s
    member-relative START/END offset — matching this milestone's Step
    10 requirement that connection orientation come only from the
    explicit ConnectionLocation, never from a position string, member
    order, or any other proxy.
  - ConnectionLocation (Milestone 7J) already carries no member
    identity (by design — see its own module docstring), so it slots
    in here purely as a transform, exactly like MemberPlacement does
    for a member's own solid.

GEOMETRY REPRESENTATION vs BOOLEAN FUSION (Step 11) — kept genuinely
separate: GeneratedConnectionAssembly.plate is the connection's own
positioned solid, associated with both members via this container; it
is NEVER unioned into either member's `.solid` and neither member's
GeneratedMemberGeometry is mutated or replaced. Boolean union IS used,
but only as a CONSISTENCY CHECK, computed and discarded: for each
member, `member.geometry.solid.union(positioned_plate)` must yield
EXACTLY ONE connected solid (proving genuine 3D contact — a shared
face or overlapping volume — the same "touching-not-overlapping union"
property attach_end_plate() already relies on for the single-member
case). If either member's check yields more than one disjoint solid,
that is reported as an explicit geometric inconsistency
(GeometryValidationError — already the right tool for this project;
no new error type is introduced) rather than silently moving the
member, the connection, or "snapping" anything into contact.

IDENTITY (Step 6/8): this module does not duplicate
`connection.connected_members` as new fields — it only checks that the
two PlacedMember objects the caller supplied are exactly the two
members the connection already, explicitly names (order-independent,
a set comparison), then stores those same PlacedMember references
directly. member_a/member_b are positional container slots only, not a
primary/secondary distinction — the current ValidatedConnection schema
has no such concept, and this module does not invent one (Step 14):
swapping which PlacedMember is passed as member_a vs member_b changes
only those two labels, never the plate's geometry, its holes, or
whether the consistency checks pass.

SCOPE (Step 9/19): exactly one controlled connection shape (END_PLATE)
is supported, matching interface.py's own SUPPORTED_CONNECTION_TYPES.
Physical bolt hardware is NOT generated for a two-member connection in
this milestone, even when `connection.physical_bolts` is set —
connections.py's existing build_bolt()/build_connection_hardware()
derive a bolt's orientation from ONE member's own outward direction
(_plate_placement()'s START/END-based outward_dir), which has no
established meaning for a plate spanning two independently-placed
members; inventing one here would be exactly the kind of
unreviewed convention this project avoids. `hardware` is therefore
always `[]` on GeneratedConnectionAssembly — a documented limitation,
not a silent gap (see test_hardware_is_always_empty_even_when_
physical_bolts_requested in tests/test_two_member_connection.py).

HOLE MEASUREMENT ASSUMPTION: _ordered_plate_hole_records() reads holes
off the plate's own `faces("<Z")` face, which stays correct for a pure
translation or a Z-axis rotation (the hole-bearing faces remain normal
to global Z either way), but is NOT verified here for a location with
a nonzero rotation_x/rotation_y (Step 9's "one controlled case" scope
matches 7I's own decision not to attempt general Euler-angle rotation
composition yet).

MILESTONE 7L — STRENGTHENING THE CONSISTENCY CHECK (Step 8): once a
MEMBER can carry its own explicit rotation (not just the connection),
the touching-union arity check alone is no longer sufficient. Proven
empirically before writing any test: rotate Member B 180 degrees about
its own local origin (keeping the SAME translation that produced a
genuine, large-area contact at 90 degrees), and
`member.solid.union(positioned_plate)` still reports exactly ONE
connected solid — but the two solids' actual bounding-box overlap on
one axis is ~1e-14mm (numerical noise, i.e. a knife-edge/near-zero-width
touch), not a real contact area. OCCT's boolean solver will happily
merge two solids that meet along a degenerate edge into one topological
solid; that is not the same thing as a genuine face-to-face contact,
and the original 7K check could not tell the two apart.

_require_contact() therefore runs a second, complementary check: the
plate's own bounding-box overlap against the member must exceed a
small margin (_MEANINGFUL_CONTACT_MARGIN_MM, framed exactly like
connections.py's own _HOLE_CUT_MARGIN_MM — a CAD robustness margin,
not a real dimension) on BOTH the global X and global Y axes. This is
deliberately not a general clash/intersection engine: it only checks
the plate's own two known face axes (width, height), which stay
aligned with global X/Y as long as the CONNECTION's own location has
no rotation — true for every fixture in 7K and 7L (only MEMBER
rotation is exercised; see module docstring's HOLE MEASUREMENT
ASSUMPTION for the matching restriction on the connection's own
rotation). A future milestone that rotates the connection itself would
need to re-derive these two axes from the plate's own rotated frame,
not global X/Y — not attempted here.

The touching-union check and the meaningful-overlap check catch
different failure modes and both remain necessary: union-arity catches
"not touching at all" (any real gap on any axis); meaningful-overlap
catches "touching along a degenerate edge/corner" (a case union-arity
alone reports as a false positive). Verified directly: 7K's original
"far away" rejection scenarios still fail on the union check alone
(their overlap axes are already negative before the margin check ever
runs); the newly-discovered 180-degree-rotation edge-touch case is
caught only by the new margin check.
"""
from dataclasses import dataclass, field
from typing import Any

import cadquery as cq

from app.cad_engine import connections as connection_geometry
from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_location import ConnectionLocation
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import SUPPORTED_CONNECTION_TYPES, GeneratedHole, ValidatedConnection

__all__ = ["GeneratedConnectionAssembly", "build_two_member_connection_assembly"]


@dataclass
class GeneratedConnectionAssembly:
    """
    One reviewed connection's geometry, positioned in project space
    and associated with the two PlacedMembers it connects — a plain
    container (Step 20: no scene graph, no automatic union/fuse/move/
    align/snap behaviour anywhere on this type).
    """
    connection_id: str
    member_a: PlacedMember
    member_b: PlacedMember
    location: ConnectionLocation
    plate: Any  # cq.Workplane, the plate solid positioned in project space via `location`
    holes: list[GeneratedHole] = field(default_factory=list)
    hardware: list[Any] = field(default_factory=list)  # always [] in 7K — see module docstring


# A CAD robustness margin (matching connections.py's own
# _HOLE_CUT_MARGIN_MM convention), not a real engineering dimension —
# see module docstring's "MILESTONE 7L" note on why the touching-union
# check alone is not sufficient once a member can be rotated.
_MEANINGFUL_CONTACT_MARGIN_MM = 10.0


def _require_contact(member: PlacedMember, positioned_plate: cq.Workplane, connection_id: str) -> None:
    """
    Two independent, complementary checks (see module docstring):
      1. Touching-union arity — catches "not touching at all".
      2. Meaningful contact area — catches "touching only along a
         degenerate edge", which check 1 alone cannot distinguish from
         a genuine face-to-face contact.
    """
    combined = member.geometry.solid.union(positioned_plate)
    solid_count = len(combined.solids().vals())
    if solid_count != 1:
        raise GeometryValidationError(
            f"Connection '{connection_id}': the reviewed connection location does not place the "
            f"connection plate in contact with member '{member.mark}' ({solid_count} disjoint "
            "solids resulted from a touching-union test, expected 1). Reported as an explicit "
            "geometric inconsistency — neither the member nor the connection is repositioned to "
            "force a fit."
        )

    member_bbox = member.geometry.solid.val().BoundingBox()
    plate_bbox = positioned_plate.val().BoundingBox()
    x_overlap = min(member_bbox.xmax, plate_bbox.xmax) - max(member_bbox.xmin, plate_bbox.xmin)
    y_overlap = min(member_bbox.ymax, plate_bbox.ymax) - max(member_bbox.ymin, plate_bbox.ymin)
    if x_overlap < _MEANINGFUL_CONTACT_MARGIN_MM or y_overlap < _MEANINGFUL_CONTACT_MARGIN_MM:
        raise GeometryValidationError(
            f"Connection '{connection_id}': the connection plate meets member '{member.mark}' "
            f"only along a degenerate edge (X overlap={x_overlap:.3f}mm, Y overlap={y_overlap:.3f}mm; "
            f"both must exceed {_MEANINGFUL_CONTACT_MARGIN_MM}mm), not a genuine contact area. "
            "Reported as an explicit geometric inconsistency — neither the member nor the "
            "connection is repositioned to force a fit."
        )


def build_two_member_connection_assembly(
    connection: ValidatedConnection,
    location: ConnectionLocation,
    member_a: PlacedMember,
    member_b: PlacedMember,
) -> GeneratedConnectionAssembly:
    """
    Builds one END_PLATE connection's geometry in project space from
    an already-validated ValidatedConnection, an already-validated
    ConnectionLocation, and two already-placed members — never reading
    any raw source dictionary, never inspecting a raw extraction
    record, and never repositioning either member or recomputing the
    connection's own location.

    Raises GeometryValidationError when:
      - connection.connection_type is not a supported type (only
        END_PLATE, matching interface.py's own SUPPORTED_CONNECTION_TYPES);
      - {member_a.mark, member_b.mark} does not exactly match
        connection.connected_members (order-independent set comparison
        — see module docstring on identity);
      - the connection has no plate or hole data (same precondition
        connections.py's attach_end_plate() already enforces for the
        single-member case);
      - the positioned plate does not genuinely contact member_a's or
        member_b's own project-space solid — either no touching-union
        results, or the touch is a degenerate edge rather than a
        meaningful contact area (see _require_contact()).

    Never mutates member_a.geometry.solid, member_b.geometry.solid,
    `connection`, or `location` — every touching-union computed here is
    discarded after its solid count is checked; the returned
    GeneratedConnectionAssembly.plate is the connection's own separate
    solid, never unioned into either member.
    """
    if connection.connection_type not in SUPPORTED_CONNECTION_TYPES:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': connection type "
            f"{connection.connection_type!r} has no supported two-member geometry builder yet. "
            f"Supported types: {sorted(SUPPORTED_CONNECTION_TYPES)}."
        )

    expected_marks = set(connection.connected_members)
    actual_marks = {member_a.mark, member_b.mark}
    if actual_marks != expected_marks:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the supplied members {sorted(actual_marks)} "
            f"do not match this connection's explicit connected_members "
            f"{sorted(expected_marks)}. The two members passed to this function must be exactly "
            "the ones the reviewed connection already names — never inferred or substituted."
        )

    if not connection.plates:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': END_PLATE connection has no plate data."
        )
    if not connection.bolts:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': END_PLATE connection has no bolt-hole data."
        )

    plate_spec = connection.plates[0]
    holes_spec = connection.bolts[0]

    # The pure LOCAL plate shape — the exact same algorithm the
    # single-member builder uses, never duplicated.
    plate_solid = connection_geometry.build_end_plate_with_holes(
        plate_spec, holes_spec, connection.connection_id,
    )

    # Positioned into PROJECT space purely from the explicit, reviewed
    # ConnectionLocation — never from either member's MemberPlacement,
    # never from connection.position.
    project_location = cq.Location(
        location.x, location.y, location.z,
        location.rotation_x, location.rotation_y, location.rotation_z,
    )
    positioned_plate = cq.Workplane(obj=plate_solid.moved(project_location))

    _require_contact(member_a, positioned_plate, connection.connection_id)
    _require_contact(member_b, positioned_plate, connection.connection_id)

    canonical_xy_order = connection_geometry._compute_hole_centers(
        float(plate_spec["width"]), float(plate_spec["height"]), holes_spec, connection.connection_id,
    )
    hole_records = connection_geometry._ordered_plate_hole_records(positioned_plate, canonical_xy_order)
    holes = [
        GeneratedHole(hole_id=f"hole-{i + 1}", center=(x, y, z), diameter=diameter)
        for i, (x, y, z, diameter) in enumerate(hole_records)
    ]

    return GeneratedConnectionAssembly(
        connection_id=connection.connection_id,
        member_a=member_a,
        member_b=member_b,
        location=location,
        plate=positioned_plate,
        holes=holes,
        hardware=[],
    )
