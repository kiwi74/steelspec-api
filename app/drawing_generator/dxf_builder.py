"""
DXF construction for a single-member fabrication drawing.

Milestone 1: one elevation view, one cross-section view, and a title
block, all drawn at true size (1:1, millimetres) in model space.

Milestone 2 (current addition): if the member carries a connection
(only END_PLATE exists yet — see app/cad_engine/connections.py), also
draw an end-plate view (the actual plate outline and hole circles)
and show the plate edge-on in the elevation. This is not trying to
match a commercial drawing's layout or plotting conventions yet — it
exists to prove the geometry-to-DXF pipeline works end to end,
including connection geometry.

ARCHITECTURE RULE, followed throughout: every shape and dimension
drawn here is read back from the actual CadQuery solid (bounding
boxes, face wires, circular edges) — never recomputed from plate/hole
dimensions or any other side-channel metadata. The one deliberate
exception is the member's own cut length: `geometry.length_mm` is
used instead of the combined solid's raw Z-extent, because once a
plate is unioned onto the far end, the solid's overall Z-extent
includes the plate's thickness — that's connection geometry, not the
member's fabrication length. `length_mm` is itself real CAD-engine
output captured at extrusion time, not an invented number.
"""
import math

import ezdxf

# Layout constants (mm) — arbitrary but fixed, so views never overlap
# regardless of member length or section size within realistic ranges.
VIEW_GAP = 300
DIM_OFFSET = 150
TITLE_BLOCK_GAP_ABOVE_SECTION = 250
TITLE_LINE_HEIGHT = 40
TEXT_HEIGHT = 25


def _extract_bottom_profile(solid) -> list[tuple[float, float]]:
    """
    Reads the real cross-section polygon back off the extruded solid's
    bottom face (Z=0, where the CAD engine's profile was extruded
    from) — the member's own section shape, unaffected by anything
    unioned onto the far end.
    """
    bottom_face = solid.faces("<Z").val()
    wire = bottom_face.outerWire()
    return [(v.X, v.Y) for v in wire.Vertices()]


def _add_linear_dim(msp, base: tuple[float, float], p1: tuple[float, float],
                     p2: tuple[float, float], angle: float = 0) -> None:
    dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, dimstyle="EZDXF")
    dim.render()


def _draw_elevation_view(msp, geometry, depth: float) -> float:
    """
    Side view of the member: a rectangle length x depth, the overall
    cut-length dimensioned underneath, and the mark labelled above.
    If a connection extends the solid beyond the member's own length
    (e.g. an end plate), it's shown edge-on as an extra block at the
    correct end — read from the actual combined bounding box, not
    from connection metadata. Returns the drawing's full Z-extent
    (member + any connection), for the caller to lay out later views.
    """
    length = geometry.length_mm  # the member's own cut length — see module docstring
    bbox = geometry.solid.val().BoundingBox()

    msp.add_lwpolyline([(0, 0), (length, 0), (length, depth), (0, depth)], close=True)
    _add_linear_dim(msp, base=(0, -DIM_OFFSET), p1=(0, 0), p2=(length, 0))
    msp.add_text(geometry.mark, dxfattribs={"height": TEXT_HEIGHT}).set_placement(
        (length / 2, depth + TEXT_HEIGHT), align=ezdxf.enums.TextEntityAlignment.BOTTOM_CENTER
    )

    if bbox.zmax > length + 1e-6:
        msp.add_lwpolyline(
            [(length, 0), (bbox.zmax, 0), (bbox.zmax, depth), (length, depth)], close=True
        )

    return max(bbox.zmax, length)


def _draw_cross_section_view(msp, geometry, x_offset: float) -> tuple[float, float]:
    """
    The actual PFC profile (read from the solid's own defining face,
    not redrawn from raw dimensions), with overall depth and overall
    flange-width dimensioned. Returns (flange_width, depth) actually
    used, read from the solid.
    """
    points = _extract_bottom_profile(geometry.solid)
    translated = [(x + x_offset, y) for x, y in points]
    msp.add_lwpolyline(translated, close=True)

    xs, ys = [p[0] for p in points], [p[1] for p in points]
    flange_width, depth = max(xs) - min(xs), max(ys) - min(ys)

    _add_linear_dim(msp, base=(x_offset, -DIM_OFFSET), p1=(x_offset, 0),
                     p2=(x_offset + flange_width, 0))
    _add_linear_dim(msp, base=(x_offset - DIM_OFFSET, 0), p1=(x_offset, 0),
                     p2=(x_offset, depth), angle=90)

    msp.add_text("CROSS SECTION", dxfattribs={"height": TEXT_HEIGHT}).set_placement(
        (x_offset, -DIM_OFFSET * 2), align=ezdxf.enums.TextEntityAlignment.BOTTOM_LEFT
    )
    return flange_width, depth


def _draw_end_plate_view(msp, geometry, x_offset: float) -> None:
    """
    A simple orthographic end view of the connection: looks along the
    member's axis at its far face, which — for an END_PLATE
    connection — is the plate's own flat face. Its outer wire is the
    plate outline; its inner wires are the actual hole voids. Nothing
    here is redrawn from plate/hole metadata — every coordinate comes
    from this face as CadQuery/OCCT actually built it.
    """
    far_face = geometry.solid.faces(">Z").val()

    outer_pts = [(v.X, v.Y) for v in far_face.outerWire().Vertices()]
    msp.add_lwpolyline([(x + x_offset, y) for x, y in outer_pts], close=True)
    xs, ys = [p[0] for p in outer_pts], [p[1] for p in outer_pts]
    width, height = max(xs) - min(xs), max(ys) - min(ys)

    hole_centers: list[tuple[float, float]] = []
    hole_radius = None
    for wire in far_face.innerWires():
        circle_edges = [e for e in wire.Edges() if e.geomType() == "CIRCLE"]
        if not circle_edges:
            continue
        radius = circle_edges[0].Length() / (2 * math.pi)
        cx, cy, _cz = wire.Center().toTuple()
        hole_radius = radius
        hole_centers.append((cx + x_offset, cy))
        msp.add_circle(center=(cx + x_offset, cy), radius=radius)

    _add_linear_dim(msp, base=(x_offset, -DIM_OFFSET), p1=(x_offset, 0), p2=(x_offset + width, 0))
    _add_linear_dim(msp, base=(x_offset - DIM_OFFSET, 0), p1=(x_offset, 0), p2=(x_offset, height), angle=90)

    if len(hole_centers) == 2 and hole_radius is not None:
        (x1, y1), (x2, y2) = hole_centers
        dia_dim = msp.add_diameter_dim(center=(x1, y1), radius=hole_radius, angle=45, dimstyle="EZDXF")
        dia_dim.render()
        _add_linear_dim(msp, base=(x_offset + width + DIM_OFFSET, min(y1, y2)),
                         p1=(x1, y1), p2=(x2, y2), angle=90)

    msp.add_text("END PLATE VIEW", dxfattribs={"height": TEXT_HEIGHT}).set_placement(
        (x_offset, -DIM_OFFSET * 2), align=ezdxf.enums.TextEntityAlignment.BOTTOM_LEFT
    )


def _draw_title_block(msp, geometry, quantity: int, status: str, origin: tuple[float, float]) -> None:
    """A simple bordered box with the member's fabrication-relevant metadata."""
    ox, oy = origin
    lines = [
        "STEELSPEC",
        "FABRICATION DRAWING",
        f"MARK: {geometry.mark}",
        f"SECTION: {geometry.section_name}",
        f"LENGTH: {geometry.length_mm:g} mm",
        f"QTY: {quantity}",
        f"STATUS: {status}",
    ]
    connection_types = getattr(geometry, "connection_types", None) or []
    if connection_types:
        label = " + ".join(t.replace("_", " ") for t in connection_types)
        lines.append(f"CONNECTION: {label}")

    width = 500
    height = TITLE_LINE_HEIGHT * len(lines) + TITLE_LINE_HEIGHT
    msp.add_lwpolyline(
        [(ox, oy), (ox + width, oy), (ox + width, oy + height), (ox, oy + height)], close=True
    )
    for i, line in enumerate(lines):
        y = oy + height - TITLE_LINE_HEIGHT * (i + 1)
        msp.add_text(line, dxfattribs={"height": TEXT_HEIGHT}).set_placement(
            (ox + 20, y), align=ezdxf.enums.TextEntityAlignment.BOTTOM_LEFT
        )


def build_drawing(geometry, quantity: int, status: str) -> ezdxf.document.Drawing:
    """
    Builds the full single-sheet fabrication drawing for one member:
    elevation view, cross-section view, an end-plate view (only if
    the member has a connection), and a title block. Returns the
    in-memory ezdxf document — saving it to disk is the caller's job
    (see app/drawing_generator/interface.py).
    """
    doc = ezdxf.new(dxfversion="R2010", setup=True)
    msp = doc.modelspace()

    # Cross section is drawn first (off to the side) purely to learn the
    # member's own depth, needed to lay out the elevation view; drawing
    # order in the file is otherwise unaffected.
    bottom_points = _extract_bottom_profile(geometry.solid)
    depth = max(y for _x, y in bottom_points) - min(y for _x, y in bottom_points)

    drawing_extent = _draw_elevation_view(msp, geometry, depth)

    section_x_offset = drawing_extent + VIEW_GAP
    flange_width, _ = _draw_cross_section_view(msp, geometry, section_x_offset)

    has_connection = getattr(geometry, "connection_count", 0) > 0
    end_plate_x_offset = section_x_offset + flange_width + VIEW_GAP
    if has_connection:
        _draw_end_plate_view(msp, geometry, end_plate_x_offset)

    title_x = end_plate_x_offset if has_connection else section_x_offset
    title_origin = (title_x, depth + TITLE_BLOCK_GAP_ABOVE_SECTION)
    _draw_title_block(msp, geometry, quantity, status, title_origin)

    return doc
