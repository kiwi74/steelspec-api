"""
Connection geometry builders for the CAD engine.

Deliberately narrow: a single connection type (END_PLATE) — a flat
rectangular plate joined to one end of a member, with a bolt-hole
pattern cut through it (2-hole vertical line, or 4-hole symmetric
grid — see _compute_hole_centers()). No welds, no other connection
types. Kept separate from sections.py on purpose: that module
describes steel section profiles, this one describes what gets
attached to the end of an already-built member solid — and it works
the same way regardless of whether that member is open (PFC, UB) or
hollow (SHS, RHS): attach_end_plate() only ever unions a plate onto
ONE end of the member (START or END — see
CONNECTION_POSITION_START/END below); it has no family-specific logic
and needs none, because a touching-not-overlapping union never
reaches back into the member's own interior (see the SHS end-plate
regression test in tests/test_cad_engine.py for the explicit proof).
A member may carry more than one connection (Milestone 6A) —
interface.py's generate_geometry() calls attach_end_plate() once per
resolved connection, each independently specifying its own
plate/holes/position; this module has no concept of "how many
connections" or "which one is this" — it only ever handles the one
connection it was called with.

MILESTONE 6B: a connection may additionally request PHYSICAL bolt
solids (connection.physical_bolts — a hex-head-and-shaft spec,
optional, None by default) via build_connection_hardware(). These are
a genuinely separate concept from the hole VOIDS attach_end_plate()
already cut into the plate: the hole is fabrication geometry (part of
the steel), the bolt is hardware that passes through it (never
unioned into the steel). build_connection_hardware() derives each
bolt's (x, y) from the exact same _compute_hole_centers() call the
plate's own holes are cut from — never an independent recomputation —
so a bolt's position can never drift out of sync with its hole. See
build_bolt() for the actual hex-head + cylindrical-shaft solid.

MILESTONE 6D: attach_end_plate() now also returns the standalone
positioned plate solid it built (the exact same object it unions into
the member — not a copy rebuilt for inspection) and that plate's own
hole records, MEASURED from its real topology (via
_ordered_plate_hole_records()) rather than copied from the hole spec.
Measuring on the plate's own standalone face — before it is unioned
with the member — sidesteps a real issue discovered in Milestone 6C:
once unioned, a hole whose footprint happens to overlap the member's
own cross-section (e.g. a UB web) can have its cylindrical face split
into several partial arcs by the boolean operation, which breaks any
"just read the diameter off the first circular edge" approach applied
to the final assembled solid. The plate's own pre-union face never has
this problem. interface.py wraps this raw (x, y, z, diameter) data
into the public GeneratedHole contract, pairing each with the bolt
build_connection_hardware() built through the same physical hole.

Built with explicit absolute-coordinate primitives (cq.Solid.makeBox /
makeCylinder / extrudeLinear + boolean cut/union/fuse) rather than
Workplane's chained sketch-then-locate methods, whose local-origin-reset
behaviour after `.faces().workplane()` is easy to get subtly wrong.
Absolute coordinates keep this predictable and directly testable.
"""
import math

import cadquery as cq
from cadquery import Solid, Vector, Wire

from app.cad_engine.errors import GeometryValidationError

REQUIRED_PLATE_FIELDS = ("width", "height", "thickness")
REQUIRED_HOLE_FIELDS = ("diameter", "quantity", "vertical_spacing")
SUPPORTED_HOLE_QUANTITIES = (2, 4)

# Extra length added to each end of the hole-cutting cylinder so the
# boolean cut has no coincident/tangent faces with the plate's own
# top and bottom — a standard CAD robustness margin, not a real
# dimension (the resulting hole is still clipped exactly to the
# plate's actual thickness by the boolean operation itself).
_HOLE_CUT_MARGIN_MM = 5.0


def _compute_hole_centers(width: float, height: float, holes: dict, mark: str) -> list[tuple[float, float]]:
    """
    Returns hole centres (in the plate's own [0,width] x [0,height]
    frame) for the requested pattern — generic across every family
    (not "SHS's pattern" or "UB's pattern"), selected purely by
    `holes['quantity']`:

      - 2: a vertical line, centred horizontally, separated by
        `vertical_spacing` (the pattern this project already had).
      - 4: a symmetric grid, centred both ways, separated by
        `horizontal_spacing` x `vertical_spacing`.

    Any other quantity is rejected — this milestone still doesn't
    support an arbitrary bolt-hole layout, only these two fixed,
    named patterns.
    """
    quantity = int(holes["quantity"])
    center_x, center_y = width / 2, height / 2

    if quantity == 2:
        vertical_spacing = float(holes["vertical_spacing"])
        centers = [
            (center_x, center_y - vertical_spacing / 2),
            (center_x, center_y + vertical_spacing / 2),
        ]
    elif quantity == 4:
        if holes.get("horizontal_spacing") is None:
            raise GeometryValidationError(
                f"Member '{mark}': a 4-hole end-plate pattern requires 'horizontal_spacing' "
                "in addition to 'vertical_spacing'."
            )
        horizontal_spacing = float(holes["horizontal_spacing"])
        vertical_spacing = float(holes["vertical_spacing"])
        centers = [
            (center_x - horizontal_spacing / 2, center_y - vertical_spacing / 2),
            (center_x + horizontal_spacing / 2, center_y - vertical_spacing / 2),
            (center_x - horizontal_spacing / 2, center_y + vertical_spacing / 2),
            (center_x + horizontal_spacing / 2, center_y + vertical_spacing / 2),
        ]
    else:
        raise GeometryValidationError(
            f"Member '{mark}': only {sorted(SUPPORTED_HOLE_QUANTITIES)}-hole end-plate patterns "
            f"are supported in this milestone (got quantity={quantity})."
        )

    for x, y in centers:
        if not (0 < x < width and 0 < y < height):
            raise GeometryValidationError(
                f"Member '{mark}': the requested hole pattern places a hole centre outside "
                f"the plate ({width}x{height}mm)."
            )
    return centers


def build_end_plate_with_holes(plate: dict, holes: dict, mark: str) -> Solid:
    """
    Builds a flat rectangular plate (width x height x thickness),
    footprint [0, width] x [0, height] x [0, thickness] in its own
    local frame, with circular through-holes cut through its
    thickness at the pattern _compute_hole_centers() selects.
    """
    missing_plate = [f for f in REQUIRED_PLATE_FIELDS if plate.get(f) is None]
    if missing_plate:
        raise GeometryValidationError(
            f"Member '{mark}': end plate is missing required field(s) {missing_plate}."
        )
    width = float(plate["width"])
    height = float(plate["height"])
    thickness = float(plate["thickness"])
    if width <= 0 or height <= 0 or thickness <= 0:
        raise GeometryValidationError(
            f"Member '{mark}': end plate dimensions must all be positive "
            f"(width={width}, height={height}, thickness={thickness})."
        )

    missing_holes = [f for f in REQUIRED_HOLE_FIELDS if holes.get(f) is None]
    if missing_holes:
        raise GeometryValidationError(
            f"Member '{mark}': bolt hole pattern is missing required field(s) {missing_holes}."
        )
    diameter = float(holes["diameter"])
    quantity = int(holes["quantity"])

    if diameter <= 0:
        raise GeometryValidationError(f"Member '{mark}': hole diameter must be positive (got {diameter}).")
    if quantity <= 0:
        raise GeometryValidationError(f"Member '{mark}': hole quantity must be positive (got {quantity}).")
    if diameter >= width or diameter >= height:
        raise GeometryValidationError(
            f"Member '{mark}': hole diameter ({diameter}mm) does not fit within the plate "
            f"({width}x{height}mm)."
        )

    hole_centers = _compute_hole_centers(width, height, holes, mark)

    plate_solid = Solid.makeBox(width, height, thickness)
    for x, y in hole_centers:
        cutter = Solid.makeCylinder(
            radius=diameter / 2,
            height=thickness + 2 * _HOLE_CUT_MARGIN_MM,
            pnt=Vector(x, y, -_HOLE_CUT_MARGIN_MM),
            dir=Vector(0, 0, 1),
        )
        plate_solid = plate_solid.cut(cutter)

    return plate_solid


CONNECTION_POSITION_START = "START"
CONNECTION_POSITION_END = "END"
SUPPORTED_CONNECTION_POSITIONS = (CONNECTION_POSITION_START, CONNECTION_POSITION_END)


def _plate_placement(connection, plate_spec: dict, member_length_mm: float) -> tuple[float, float]:
    """
    Returns (z_offset, outward_dir) for the plate connection.position
    names: z_offset is where the plate's own local Z=0 face lands in
    the member's global frame (exactly the offset attach_end_plate()
    has always used), and outward_dir is the unit Z direction (+1 or
    -1) pointing from the member's face further out, away from the
    member, through the plate. One branch, shared by attach_end_plate()
    (which positions the plate) and build_connection_hardware() (which
    positions bolts relative to that same plate) so the two can never
    disagree about which way "outward" points.
    """
    thickness = float(plate_spec["thickness"])
    if connection.position == CONNECTION_POSITION_START:
        return -thickness, -1.0
    return member_length_mm, 1.0


def _ordered_plate_hole_records(
    plate_wp: cq.Workplane, canonical_xy_order: list[tuple[float, float]]
) -> list[tuple[float, float, float, float]]:
    """
    (x, y, z, diameter) per hole, MEASURED from the plate's own real
    topology (its flat face's innerWires() — never from the hole spec),
    then reordered to match canonical_xy_order: the exact (x, y)
    ordering _compute_hole_centers() produced and
    build_connection_hardware() therefore uses to build bolts. A face's
    innerWires() come back in whatever order OCCT happens to store
    them internally, which is not guaranteed to match that ordering —
    reordering by nearest coordinate match (not by index) is what lets
    interface.py pair GeneratedHole[i] with the bolt actually built
    through that exact physical hole.
    """
    face = plate_wp.faces("<Z").val()
    measured = []
    for wire in face.innerWires():
        edges = [e for e in wire.Edges() if e.geomType() == "CIRCLE"]
        radius = edges[0].Length() / (2 * math.pi)
        cx, cy, cz = wire.Center().toTuple()
        measured.append((cx, cy, cz, radius * 2))

    remaining = list(measured)
    ordered = []
    for target_x, target_y in canonical_xy_order:
        closest = min(remaining, key=lambda r: (r[0] - target_x) ** 2 + (r[1] - target_y) ** 2)
        ordered.append(closest)
        remaining.remove(closest)
    return ordered


def attach_end_plate(
    member_solid: cq.Workplane, connection, member_length_mm: float, mark: str
) -> tuple[cq.Workplane, cq.Workplane, list[tuple[float, float, float, float]], float]:
    """
    Builds the END_PLATE connection's geometry and joins it to the
    member's solid at the end `connection.position` names — "END"
    (Z = member_length_mm, extending the assembly forward, unchanged
    since Milestone 2) or "START" (Z = 0, extending it backward by the
    plate's own thickness). Either way, in the same X/Y frame the
    section profile was built in (see sections.py) — the plate's own
    [0,width] x [0,height] footprint lines up directly with the
    member's [0,flange_width] x [0,depth] footprint with no extra
    alignment step needed. Position validity itself is checked by the
    caller (interface.py's _resolve_connections) before this runs;
    this function only ever branches on the two data values that
    checkpoint already guaranteed — never on section family or a
    connection's index/order.

    Returns (updated_member_solid, positioned_plate, hole_records,
    outward_dir):
      - updated_member_solid: the steel solid (member + plate, minus
        hole voids) — unchanged in meaning since Milestone 2. Physical
        bolt hardware (Milestone 6B) is never unioned in here; see
        build_connection_hardware() for that, kept deliberately separate.
      - positioned_plate: the SAME standalone plate solid that was
        unioned above (Milestone 6D) — not a copy rebuilt for
        inspection — so a caller can reference this connection's own
        steel geometry without re-deriving it from plate.width/height/
        thickness or reverse-engineering `updated_member_solid`.
      - hole_records: that plate's own actual (x, y, z, diameter) hole
        voids, measured from its real topology (see
        _ordered_plate_hole_records()).
      - outward_dir: +1.0 (END) or -1.0 (START) — the same value
        build_connection_hardware() uses to place bolts, exposed so
        interface.py can build a cheap Z-axis orientation hint without
        recomputing it from connection.position a second time.
    """
    if not connection.plates:
        raise GeometryValidationError(f"Member '{mark}': END_PLATE connection has no plate data.")
    if not connection.bolts:
        raise GeometryValidationError(f"Member '{mark}': END_PLATE connection has no bolt-hole data.")

    plate_spec = connection.plates[0]
    holes_spec = connection.bolts[0]

    plate_solid = build_end_plate_with_holes(plate_spec, holes_spec, mark)

    z_offset, outward_dir = _plate_placement(connection, plate_spec, member_length_mm)
    positioned_plate = cq.Workplane(obj=plate_solid.moved(cq.Location(Vector(0, 0, z_offset))))

    canonical_xy_order = _compute_hole_centers(
        float(plate_spec["width"]), float(plate_spec["height"]), holes_spec, mark
    )
    hole_records = _ordered_plate_hole_records(positioned_plate, canonical_xy_order)

    updated_member_solid = member_solid.union(positioned_plate)

    return updated_member_solid, positioned_plate, hole_records, outward_dir


REQUIRED_BOLT_FIELDS = ("shaft_diameter", "shaft_length", "head_across_flats", "head_thickness")


def build_bolt(
    x: float, y: float, outward_face_z: float, outward_dir: float, bolt_spec: dict, mark: str
) -> Solid:
    """
    One physical bolt — a hexagonal head fused to a cylindrical shaft,
    forming a single connected solid — centred on (x, y): an ACTUAL
    hole centre in the same global X/Y frame the plate and its holes
    are built in (see build_connection_hardware()), never an
    independently recomputed position. Its axis runs along Z, normal
    to the plate: the head sits flush against the plate's
    outward-facing face (outward_face_z) and extends further outward
    (away from the member, in the outward_dir direction); the shaft
    starts at that same face and runs the opposite way, inward through
    the plate's hole void (and, since shaft_length is deliberately
    longer than the plate is thick, on into where the member's own
    solid sits — geometrically overlapping it, exactly like a real
    bolt shaft would overlap the threaded material it engages; this is
    never a problem because hardware is never unioned with the steel).

    Purely geometric: this function only ever receives a point, a
    direction and a dimension spec. It has no notion of section
    family, connection position/index, or which hole this is — that
    is entirely the caller's concern.
    """
    missing = [f for f in REQUIRED_BOLT_FIELDS if bolt_spec.get(f) is None]
    if missing:
        raise GeometryValidationError(
            f"Member '{mark}': physical bolt is missing required field(s) {missing}."
        )

    shaft_diameter = float(bolt_spec["shaft_diameter"])
    shaft_length = float(bolt_spec["shaft_length"])
    head_across_flats = float(bolt_spec["head_across_flats"])
    head_thickness = float(bolt_spec["head_thickness"])

    if shaft_diameter <= 0 or shaft_length <= 0 or head_across_flats <= 0 or head_thickness <= 0:
        raise GeometryValidationError(
            f"Member '{mark}': physical bolt dimensions must all be positive "
            f"(shaft_diameter={shaft_diameter}, shaft_length={shaft_length}, "
            f"head_across_flats={head_across_flats}, head_thickness={head_thickness})."
        )
    if head_across_flats <= shaft_diameter:
        raise GeometryValidationError(
            f"Member '{mark}': physical bolt head (across flats {head_across_flats}mm) must be "
            f"larger than its shaft (diameter {shaft_diameter}mm)."
        )

    shaft = Solid.makeCylinder(
        radius=shaft_diameter / 2,
        height=shaft_length,
        pnt=Vector(x, y, outward_face_z),
        dir=Vector(0, 0, -outward_dir),
    )

    # Regular hexagon: across-flats (flat-to-flat) = circumradius * sqrt(3).
    head_radius = head_across_flats / math.sqrt(3)
    hex_points = [
        Vector(
            x + head_radius * math.cos(math.radians(60 * k)),
            y + head_radius * math.sin(math.radians(60 * k)),
            outward_face_z,
        )
        for k in range(6)
    ]
    hex_wire = Wire.makePolygon(hex_points, close=True)
    head = Solid.extrudeLinear(hex_wire, [], Vector(0, 0, outward_dir * head_thickness))

    return head.fuse(shaft)


def build_connection_hardware(connection, member_length_mm: float, mark: str) -> list[cq.Workplane]:
    """
    Builds one physical bolt per ACTUAL hole centre for a connection's
    optional `physical_bolts` hardware spec. Returns [] when
    `physical_bolts` is not set (the default) — physical hardware is
    strictly opt-in; every existing END_PLATE connection that only
    ever specified hole voids keeps generating exactly the same steel
    geometry, with no hardware list attached.

    Hole centres come from the exact same _compute_hole_centers() call
    build_end_plate_with_holes() uses to cut the plate's hole voids —
    not a separate recomputation from plate width/height/spacing — so
    a bolt can never disagree with the hole it is meant to pass
    through, even if the hole pattern changes. Returned bolts are
    plain cq.Workplane solids in the member's global frame; the caller
    (interface.py) is responsible for keeping them out of `solid`
    (never unioned with the steel).
    """
    if not connection.physical_bolts:
        return []
    if not connection.plates or not connection.bolts:
        raise GeometryValidationError(
            f"Member '{mark}': physical bolts require plate and hole data on the same connection."
        )

    plate_spec = connection.plates[0]
    holes_spec = connection.bolts[0]
    bolt_spec = connection.physical_bolts

    width = float(plate_spec["width"])
    height = float(plate_spec["height"])
    hole_centers = _compute_hole_centers(width, height, holes_spec, mark)

    z_offset, outward_dir = _plate_placement(connection, plate_spec, member_length_mm)
    thickness = float(plate_spec["thickness"])
    outward_face_z = z_offset + thickness if outward_dir > 0 else z_offset

    return [
        cq.Workplane(obj=build_bolt(x, y, outward_face_z, outward_dir, bolt_spec, mark))
        for x, y in hole_centers
    ]
