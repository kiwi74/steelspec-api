"""
PDF fabrication drawing construction.

IMPORTANT DISTINCTION: this is NOT app/report/pdf_generator.py. That
module produces tables/prose (a data report) via reportlab's
high-level Platypus flowables. This module produces an actual
dimensioned technical drawing — real vector lines, paths, and text on
an A3 landscape sheet — via reportlab's low-level Canvas API, which
Platypus doesn't expose. Same underlying library (already a project
dependency), used for a genuinely different purpose.

ARCHITECTURE RULE, followed throughout, identical to dxf_builder.py:
every shape and dimension drawn here is read back from actual
generated geometry, never re-derived from input specification
metadata. Flange/web thickness labels are derived by inspecting the
actual profile's Y-levels for a 4-level (flange/web/web/flange)
pattern — not copied from input section metadata. That pattern only
exists for I/H-shaped families (UB here); for other families those two
labels are simply omitted, not guessed. Connection detail views (see
MILESTONE 6E below) follow the same rule one level higher up: plate
outline, hole positions, edge distances and spacing are plain
arithmetic on coordinates read from GeneratedConnection — never from
`bolts[0]`/`plates[0]` input metadata.

HOLLOW SECTIONS (SHS, RHS): _extract_end_face_topology() below reads
BOTH the outer boundary and any inner wires from an end face — a
strict superset of what dxf_builder._extract_bottom_profile() does
(outer only), used here in its place. For PFC/UB, an end face has no
inner wires, so behaviour is unchanged. For SHS/RHS, the one inner
wire IS the actual hollow void, drawn as a second closed path — never
two independently-reconstructed rectangles. Wall thickness is derived
from the offset between the outer and inner boundary's own
coordinates. There is no `if family == "SHS"` (or "RHS"/"UB"/"PFC")
anywhere in this module — every view only asks the actual face "does
it have inner wires?" and draws what it finds.

MILESTONE 6E: per-connection detail views (_draw_connection_detail()
and friends) no longer read the member solid's own end face at all —
they consume app.cad_engine.interface.GeneratedConnection directly:
`connection.geometry_bounds` for the plate's own actual bounding box,
`connection.holes` (a list of GeneratedHole — center/diameter already
MEASURED from the real generated plate, not copied from any hole
spec) for every circle and dimension drawn. This replaced the old
_draw_end_plate_view(), which read geometry.solid.faces(">Z") and only
ever supported one connection, always at the member's far end. The
renderer is handed exactly one GeneratedConnection at a time — it has
no notion of "how many connections", "which position", or "which
section family"; build_pdf() decides layout (how many detail views,
which slot) purely from each connection's own `.position` field, never
from list order, so supplying connections in reversed order changes
nothing about which slot START or END geometry ends up in.

MILESTONE 6G: a compact fabrication-information block (PLATE/HOLES/
BOLTS/PATTERN — see _connection_info_lines() and friends) was added
beside each connection detail, summarising what was actually
generated rather than repeating every dimension already on the
drawing. Plate thickness comes from `connection.geometry_bounds.zlen`
(never `plates[0]['thickness']`, and never assuming the plate starts
at Z=0 — zlen is a max-min span, correct regardless of whether the
plate sits at negative Z (START) or beyond length_mm (END)). Hole and
bolt diameters are grouped from `connection.holes`/`connection.hardware`
(via _diameter_groups()) rather than assumed uniform — a connection
with mixed diameters gets one line per distinct value instead of a
single misleading count. A hole-pattern summary (_pattern_summary_line())
is only ever produced when the actual hole centres unambiguously form
a simple vertical line or symmetric rectangular grid; anything else is
silently omitted rather than mislabelled. This replaced the standalone
"Ø.. mm" and "N BOLTS" labels Milestones 6E/6F drew separately — that
information now lives in one place instead of being scattered and
partially duplicated.

MILESTONE 6F: connection details may now also show
a simple 2D orthographic representation of each connection's ACTUAL
physical bolts (connection.hardware — cq.Workplane solids the CAD
engine already built in Milestone 6B, never rebuilt here). See
_bolt_geometry_record() and _draw_connection_hardware(): position,
shaft radius and head extent are all measured directly off each
bolt's own solid (bounding box centre, cylindrical shaft face, overall
bounding-box width) — never read from GeneratedHole.center (a
different, independent geometry source that happens to coincide with
it) or from a physical_bolts input spec. A bolt is drawn as a dashed
"head extent" circle (larger than, and visually distinct from, the
solid hole circle already drawn) plus a small filled dot marking the
shaft/bolt axis — deliberately not a precise hex or a 3D rendering.
Z-axis quantities (shaft length, head thickness) have no meaningful
representation in this plate-face (XY) view and are not attempted
here — see _bolt_geometry_record()'s docstring. hardware remains
strictly optional: connection.hardware == [] draws nothing extra,
leaving the Milestone 6E view unchanged.

A PDF page has a fixed physical size, unlike DXF's infinite model
space — so unlike dxf_builder.py (which draws at true 1:1 scale),
this module applies an explicit drawing scale per view (round,
engineering-style ratios) to fit the real geometry onto the sheet.

DRAWING PRESENTATION STANDARD (current addition): a bordered A3 sheet,
a structured multi-field title block (drawing number, revision, date,
material, units, scale — see DrawingMetadata below), and one
consistent dimension style (line-weight hierarchy, extension-line
gap/overshoot, tick size, text height) applied uniformly across every
view. None of this changes what geometry is measured or how — it's
presentation only. MATERIAL is deliberately not read from anywhere:
GeneratedMemberGeometry doesn't carry it, so it always renders as
"NOT SPECIFIED" unless the caller explicitly supplies one (see
generate_fabrication_drawing_pdf's `material` parameter) — never
invented.

MILESTONE 7AW: the title blocks now COMMUNICATE the reviewed record
rather than only the generated geometry. Every reviewed title block
prints the reviewed CONNECTION LOCATION (the assembly's own
ConnectionLocation, with its documented x/y/z-mm / rotation-degree
semantics — see _connection_location_text(), never a 'FROM BASE'-style
reference frame the record does not name) and each member's reviewed
ATTACH semantics (that member's own ConnectionAttachment, matched by
member mark, refused when absent — never inferred from geometry or
member order). The bottom sheet row gains a deterministic PAGE cell
('1 OF 1' — every sheet here is a single page), and STATUS is no
longer defaulted to 'TEST': DrawingMetadata.status is None unless the
caller supplies one, and no STATUS cell is drawn when it is absent —
the drawing never implies an approval state SteelSpec did not record.
Nothing here reads raw AI extraction, Supabase, test fixtures or
invented values; every new printed value comes from the same reviewed
assembly the builder already consumes.

MILESTONE 7T: a second top-level builder, build_connection_pdf(), for
a REVIEWED TWO-MEMBER CONNECTION (app.cad_engine.reviewed_connection_assembly.
ReviewedTwoMemberConnectionAssembly) rather than a single
GeneratedMemberGeometry. STEP 1 FINDING: GeneratedConnectionAssembly
(the 7K/7L two-member connection geometry) already carries `.plate`
(the actual positioned CadQuery plate solid) and `.holes` (a list of
GeneratedHole, measured off that same plate) in EXACTLY the shape
GeneratedConnection (Milestone 6D, the single-member connection
contract this module already renders via _draw_connection_detail())
uses — the only fields GeneratedConnectionAssembly lacks are
connection_type/position/outward_normal, all copied straight from the
ValidatedConnection/ConnectionAttachment objects the assembly already
carries. _connection_assembly_as_generated_connection() below is
therefore a plain, lossless adapter (no new hole/plate representation,
no rebuild from spec metadata) letting _draw_connection_detail() be
reused COMPLETELY UNCHANGED for the connection detail view (View 2) —
its only new parameter, `title`, defaults to preserving the exact
single-member behaviour for every existing call site.

View 1 (connection elevation, _draw_two_member_elevation()) and View 3
(two-member title block, _draw_two_member_title_block()) are new,
since neither an existing single-member elevation (one member, local
[0, length_mm] frame) nor the existing title block (one member's
mark/section/length) has a project-space, two-member equivalent to
reuse. Both read geometry exclusively from each object's own actual,
already-generated BoundingBox()/mark/section_name/length_mm — never
from plate/hole/member input specs — matching this module's existing
architecture rule stated above. _draw_two_member_title_block() reuses
_draw_tb_grid_row() (the same title-block row primitive
_draw_title_block() already uses) with two member rows instead of one;
no new title-block rendering primitive was needed.
"""
import datetime as _datetime
import math
from collections import Counter
from dataclasses import dataclass
from io import BytesIO

from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.cad_engine.interface import GeneratedConnection
from app.cad_engine.multi_member_connection import MEMBER_LABEL_LETTERS, member_label
from app.drawing_generator.errors import DrawingValidationError

PAGE_SIZE = landscape(A3)  # 420 x 297 mm
PAGE_WIDTH_MM = PAGE_SIZE[0] / mm
PAGE_HEIGHT_MM = PAGE_SIZE[1] / mm

ELEVATION_SCALE = 1 / 20   # 4000mm member -> 200mm on the page
CROSS_SECTION_SCALE = 1 / 2

# --- Sheet border ---
OUTER_BORDER_INSET_MM = 8
INNER_BORDER_INSET_MM = 12
CONTENT_MARGIN_MM = 16  # nothing (views, dimensions, labels) may be drawn outside this inset

# --- View layout (mm, page coordinates) ---
ELEVATION_ORIGIN_MM = (CONTENT_MARGIN_MM, 246)
CROSS_SECTION_ORIGIN_MM = (30, 54)
VIEW_GAP_MM = 50
TITLE_BLOCK_WIDTH_MM = 140
TITLE_BLOCK_ORIGIN_MM = (PAGE_WIDTH_MM - CONTENT_MARGIN_MM - TITLE_BLOCK_WIDTH_MM, CONTENT_MARGIN_MM)

# --- Connection detail views (Milestone 6E) ---
# A lone connection is drawn at the same size/position the single
# END_PLATE view has always used (to the right of the cross section).
# Two connections are drawn smaller, stacked in that same horizontal
# slot, ordered top-to-bottom by POSITION (never by list order) via
# _CONNECTION_POSITION_ORDER below.
CONNECTION_DETAIL_SINGLE_SCALE = CROSS_SECTION_SCALE
CONNECTION_DETAIL_STACKED_SCALE = 0.24
# The bottom slot's origin must clear the deepest dimension level drawn
# below a detail view's own origin (_DIM_LEVEL_DIAMETER_MM, plus a small
# margin) so nothing falls below CONTENT_MARGIN_MM; the top slot's origin
# must sit far enough above the bottom slot's own title line (its own
# origin + plate height + _TITLE_ABOVE_MM) to leave a clear gap, while
# staying clear of the elevation's dimension line above.
CONNECTION_DETAIL_SLOT_TOP_Y_MM = 148
CONNECTION_DETAIL_SLOT_BOTTOM_Y_MM = 44
_CONNECTION_POSITION_ORDER = {"START": 0, "END": 1}

# --- Dimension style (one consistent style, used everywhere) ---
DIM_TICK_MM = 1.8
EXT_GAP_MM = 1.2        # extension line starts this far from the measured feature
EXT_OVERSHOOT_MM = 1.5  # extension line continues this far past the dimension line

# --- Line-weight hierarchy ---
LW_OBJECT = 0.6      # real geometry: member outline, plate outline, hole circles
LW_BORDER_OUTER = 0.8
LW_BORDER_INNER = 0.4
LW_TITLE_BLOCK = 0.5
LW_DIMENSION = 0.3   # dimension line + ticks
LW_EXTENSION = 0.18  # extension lines
LW_CENTERLINE = 0.18
LW_HARDWARE = 0.35   # physical bolt head-extent circle (Milestone 6F)

CENTERLINE_DASH = [6, 2, 1, 2]  # long dash, short dash — conventional centreline pattern
HARDWARE_DASH = [1.2, 1.2]      # fine dots — visually distinct from centrelines and hole outlines

# --- Text styles ---
FONT_SHEET_TITLE = ("Helvetica-Bold", 13)
FONT_VIEW_TITLE = ("Helvetica-Bold", 11)
FONT_MARK_LABEL = ("Helvetica-Bold", 11)
FONT_DIM_TEXT = ("Helvetica", 7.5)     # one consistent size for every dimension value
FONT_INFO_LABEL = ("Helvetica", 7)     # e.g. "FLANGE THK:", "Ø18 mm"
FONT_TB_SUBTITLE = ("Helvetica", 9)
FONT_TB_LABEL = ("Helvetica", 5.5)
FONT_TB_VALUE = ("Helvetica-Bold", 8)


@dataclass
class DrawingMetadata:
    """
    Presentation metadata for the title block. Internal to this
    module — callers of generate_fabrication_drawing_pdf() supply
    plain values (material, revision, date); build_pdf() assembles
    this. Never a source of geometry: nothing here is drawn as a
    dimension, only as title-block text.
    """
    drawing_number: str
    revision: str = "A"
    date: str = ""
    drawn_by: str = "STEELSPEC"
    checked_by: str = "—"
    units: str = "mm"
    scale: str = "NTS"
    # None (the default) means NO status is drawn at all — the title
    # block carries no STATUS cell, so the drawing never implies an
    # approval/issue state SteelSpec did not record (MILESTONE 7AW).
    # A caller-supplied status is printed verbatim, exactly as before.
    status: str | None = None
    material: str = "NOT SPECIFIED"
    project_id: str | None = None
    source_drawing_id: str | None = None


def _connection_location_text(location) -> str:
    """
    MILESTONE 7AW: the reviewed ConnectionLocation rendered with its own
    established field semantics (connection_location.ConnectionLocation's
    documented contract — x/y/z translations in mm in the shared project
    coordinate system, rotations in degrees). Every number printed here
    IS the reviewed value, verbatim; no reference frame beyond that
    contract is claimed ('FROM BASE'/'FROM TOP'-style labels are never
    invented). All six fields are always printed — a zero rotation is a
    real reviewed value, not an omission.
    """
    return (
        f"X {location.x:g}, Y {location.y:g}, Z {location.z:g} mm, "
        f"RX {location.rotation_x:g}°, RY {location.rotation_y:g}°, "
        f"RZ {location.rotation_z:g}°"
    )


def _draw_sheet_status_row(c, ox: float, cursor: float, width_mm: float,
                           metadata: DrawingMetadata) -> float:
    """
    MILESTONE 7AW: the bottom title-block row every sheet ends with —
    MATERIAL/UNITS/SCALE plus a deterministic PAGE cell ('1 OF 1': every
    sheet this module builds is a single A3 page, so the page count is
    the sheet's own fact, never an input). STATUS is drawn ONLY when the
    caller genuinely supplied one (see DrawingMetadata.status); an absent
    status draws no STATUS cell, so the drawing never implies an approval
    state that was never recorded.
    """
    cols: list[tuple[str, str]] = [
        ("MATERIAL", metadata.material),
        ("UNITS", metadata.units),
        ("SCALE", metadata.scale),
    ]
    if metadata.status:
        cols.append(("STATUS", metadata.status))
    cols.append(("PAGE", "1 OF 1"))
    return _draw_tb_grid_row(c, ox, cursor, width_mm, cols)


def _pt(value_mm: float) -> float:
    return value_mm * mm


def _ext_range(from_v: float, to_v: float) -> tuple[float, float]:
    """
    Start/end (in the extension line's own axis) so the line begins a
    small gap away from the measured feature and overshoots slightly
    past the dimension line — standard drafting convention — working
    in either direction.
    """
    if to_v >= from_v:
        return from_v + EXT_GAP_MM, to_v + EXT_OVERSHOOT_MM
    return from_v - EXT_GAP_MM, to_v - EXT_OVERSHOOT_MM


def _draw_horizontal_dim(c, x1_mm: float, x2_mm: float, y_mm: float, label: str,
                          ext1_y: float | None = None, ext2_y: float | None = None) -> None:
    """
    A dimension line with end ticks and a centred label. If ext1_y/ext2_y
    are given, a thin extension line connects the actual feature point
    (x1, ext1_y) / (x2, ext2_y) to the dimension line, with a small gap
    at the feature end and a small overshoot past the dimension line —
    standard drafting practice.
    """
    c.setLineWidth(LW_EXTENSION)
    if ext1_y is not None:
        y_start, y_end = _ext_range(ext1_y, y_mm)
        c.line(_pt(x1_mm), _pt(y_start), _pt(x1_mm), _pt(y_end))
    if ext2_y is not None:
        y_start, y_end = _ext_range(ext2_y, y_mm)
        c.line(_pt(x2_mm), _pt(y_start), _pt(x2_mm), _pt(y_end))

    c.setLineWidth(LW_DIMENSION)
    c.line(_pt(x1_mm), _pt(y_mm), _pt(x2_mm), _pt(y_mm))
    c.line(_pt(x1_mm), _pt(y_mm - DIM_TICK_MM), _pt(x1_mm), _pt(y_mm + DIM_TICK_MM))
    c.line(_pt(x2_mm), _pt(y_mm - DIM_TICK_MM), _pt(x2_mm), _pt(y_mm + DIM_TICK_MM))
    c.setFont(*FONT_DIM_TEXT)
    c.drawCentredString(_pt((x1_mm + x2_mm) / 2), _pt(y_mm + 1.3), label)


def _draw_vertical_dim(c, y1_mm: float, y2_mm: float, x_mm: float, label: str,
                        ext1_x: float | None = None, ext2_x: float | None = None) -> None:
    """Same as _draw_horizontal_dim, rotated — see its docstring for ext1_x/ext2_x."""
    c.setLineWidth(LW_EXTENSION)
    if ext1_x is not None:
        x_start, x_end = _ext_range(ext1_x, x_mm)
        c.line(_pt(x_start), _pt(y1_mm), _pt(x_end), _pt(y1_mm))
    if ext2_x is not None:
        x_start, x_end = _ext_range(ext2_x, x_mm)
        c.line(_pt(x_start), _pt(y2_mm), _pt(x_end), _pt(y2_mm))

    c.setLineWidth(LW_DIMENSION)
    c.line(_pt(x_mm), _pt(y1_mm), _pt(x_mm), _pt(y2_mm))
    c.line(_pt(x_mm - DIM_TICK_MM), _pt(y1_mm), _pt(x_mm + DIM_TICK_MM), _pt(y1_mm))
    c.line(_pt(x_mm - DIM_TICK_MM), _pt(y2_mm), _pt(x_mm + DIM_TICK_MM), _pt(y2_mm))
    c.saveState()
    c.translate(_pt(x_mm - 4), _pt((y1_mm + y2_mm) / 2))
    c.rotate(90)
    c.setFont(*FONT_DIM_TEXT)
    c.drawCentredString(0, 0, label)
    c.restoreState()


def _draw_view_title(c, x_mm: float, y_mm: float, text: str) -> None:
    c.setFont(*FONT_VIEW_TITLE)
    c.drawString(_pt(x_mm), _pt(y_mm), text)


def _draw_border(c) -> None:
    c.setLineWidth(LW_BORDER_OUTER)
    c.rect(_pt(OUTER_BORDER_INSET_MM), _pt(OUTER_BORDER_INSET_MM),
           _pt(PAGE_WIDTH_MM - 2 * OUTER_BORDER_INSET_MM), _pt(PAGE_HEIGHT_MM - 2 * OUTER_BORDER_INSET_MM),
           stroke=1, fill=0)
    c.setLineWidth(LW_BORDER_INNER)
    c.rect(_pt(INNER_BORDER_INSET_MM), _pt(INNER_BORDER_INSET_MM),
           _pt(PAGE_WIDTH_MM - 2 * INNER_BORDER_INSET_MM), _pt(PAGE_HEIGHT_MM - 2 * INNER_BORDER_INSET_MM),
           stroke=1, fill=0)


def _draw_elevation(c, geometry, origin_mm: tuple[float, float], depth_mm: float) -> float:
    """
    Simple side-view outline: a rectangle length x depth. The length
    dimension uses geometry.length_mm — the member's own cut length —
    never the combined solid's bounding box, which includes any
    attached connection's thickness (see module docstring /
    dxf_builder.py). If the solid extends past length_mm (an end
    plate), that extension is drawn edge-on as a second rectangle
    read from the real combined bounding box — not from plate
    thickness metadata. Returns the elevation's drawn length in page mm.
    """
    ox, oy = origin_mm
    length_page = geometry.length_mm * ELEVATION_SCALE
    depth_page = depth_mm * ELEVATION_SCALE

    _draw_view_title(c, ox, oy + depth_page + 20, "ELEVATION")

    c.setLineWidth(LW_OBJECT)
    c.rect(_pt(ox), _pt(oy), _pt(length_page), _pt(depth_page), stroke=1, fill=0)

    c.setFont(*FONT_MARK_LABEL)
    c.drawCentredString(_pt(ox + length_page / 2), _pt(oy + depth_page + 6), geometry.mark)

    _draw_horizontal_dim(c, ox, ox + length_page, oy - 10, f"{geometry.length_mm:g}", ext1_y=oy, ext2_y=oy)

    bbox = geometry.solid.val().BoundingBox()
    if bbox.zmax > geometry.length_mm + 1e-6:
        extra_page = (bbox.zmax - geometry.length_mm) * ELEVATION_SCALE
        c.rect(_pt(ox + length_page), _pt(oy), _pt(extra_page), _pt(depth_page), stroke=1, fill=0)

    return length_page


def _extract_end_face_topology(
    solid, face_selector: str = "<Z"
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    """
    Returns (outer_points, inner_wires_points) from one end face of
    the solid. For a simple closed profile (PFC, UB) inner_wires_points
    is empty — a strict superset of what dxf_builder._extract_bottom_profile()
    returns (outer only), so behaviour for PFC/UB is unchanged. For a
    hollow section (SHS, RHS) it has exactly one entry — the actual
    void boundary, read from the real solid, never reconstructed from
    width/depth/thickness metadata.
    """
    face = solid.faces(face_selector).val()
    outer_points = [(v.X, v.Y) for v in face.outerWire().Vertices()]
    inner_wires_points = [[(v.X, v.Y) for v in w.Vertices()] for w in face.innerWires()]
    return outer_points, inner_wires_points


def _draw_closed_path(c, points_page: list[tuple[float, float]]) -> None:
    path = c.beginPath()
    path.moveTo(_pt(points_page[0][0]), _pt(points_page[0][1]))
    for x, y in points_page[1:]:
        path.lineTo(_pt(x), _pt(y))
    path.close()
    c.setLineWidth(LW_OBJECT)
    c.drawPath(path, stroke=1, fill=0)


def _draw_cross_section(c, geometry, origin_mm: tuple[float, float]) -> tuple[float, float]:
    """
    The actual profile, read from the solid's own bottom face (never
    redrawn from depth/flange_width/width/thickness/etc.), scaled and
    drawn as real closed vector path(s) — the outer boundary always,
    plus each inner wire (a hollow section's actual void — SHS, RHS)
    when the face has one. Overall width/depth dimensions, flange/web
    thickness labels (I/H families), and wall-thickness labels (hollow
    families) are added where the profile's own geometry supports
    deriving them. Returns (width_mm, depth_mm) actually used, so the
    caller can lay out any further views without overlap.
    """
    ox, oy = origin_mm
    outer_points, inner_wires_points = _extract_end_face_topology(geometry.solid)
    xs = [p[0] for p in outer_points]
    ys = [p[1] for p in outer_points]
    x_min, y_min = min(xs), min(ys)
    width_mm = max(xs) - x_min
    depth_mm = max(ys) - y_min

    def to_page(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [
            (ox + (x - x_min) * CROSS_SECTION_SCALE, oy + (y - y_min) * CROSS_SECTION_SCALE)
            for x, y in points
        ]

    _draw_closed_path(c, to_page(outer_points))
    for inner_points in inner_wires_points:
        _draw_closed_path(c, to_page(inner_points))

    _draw_view_title(c, ox, oy + depth_mm * CROSS_SECTION_SCALE + 8, "CROSS SECTION")

    width_page = width_mm * CROSS_SECTION_SCALE
    depth_page = depth_mm * CROSS_SECTION_SCALE
    _draw_horizontal_dim(c, ox, ox + width_page, oy - 10, f"{width_mm:g}", ext1_y=oy, ext2_y=oy)
    _draw_vertical_dim(c, oy, oy + depth_page, ox - 10, f"{depth_mm:g}", ext1_x=ox, ext2_x=ox)

    # Flange/web thickness, derived from the outer boundary's own
    # geometry — ONLY when the waist level has 4 distinct X-crossings
    # (two flanges, symmetric about a central web: an I/H topology). A
    # 4-level Y-pattern alone is NOT sufficient to detect this: PFC's
    # 8-point C-shape also has exactly 4 distinct Y-levels, but only 2
    # X-crossings at the waist (one-sided web, not a symmetric I/H) —
    # so checking the waist X-crossing count first is what correctly
    # keeps these labels off a PFC (or SHS/RHS, which has only 2
    # Y-levels on its outer boundary anyway) drawing, without any
    # per-family branch anywhere in this module.
    label_y = oy - 24
    y_levels = sorted({round(y, 6) for y in ys})
    if len(y_levels) == 4:
        waist_y = y_levels[1]
        xs_at_waist = sorted(x for x, y in outer_points if abs(y - waist_y) < 1e-6)
        if len(xs_at_waist) == 4:
            flange_thickness_mm = y_levels[1] - y_levels[0]
            web_thickness_mm = xs_at_waist[2] - xs_at_waist[1]
            c.setFont(*FONT_INFO_LABEL)
            c.drawString(_pt(ox), _pt(label_y), f"FLANGE THK: {flange_thickness_mm:g} mm")
            label_y -= 5
            c.drawString(_pt(ox), _pt(label_y), f"WEB THK: {web_thickness_mm:g} mm")

    # Wall thickness, derived from the offset between the outer and
    # inner boundary's own coordinates — only meaningful when there's
    # exactly one inner wire (a genuine hollow section). Checked on
    # both axes (never assuming width == depth) even though only one
    # value is shown, since a uniform-wall hollow profile has the same
    # offset on both.
    if len(inner_wires_points) == 1:
        inner_points = inner_wires_points[0]
        inner_x_min = min(p[0] for p in inner_points)
        inner_y_min = min(p[1] for p in inner_points)
        wall_x_mm = inner_x_min - x_min
        wall_y_mm = inner_y_min - y_min
        c.setFont(*FONT_INFO_LABEL)
        c.drawString(_pt(ox), _pt(label_y), f"WALL THK: {wall_x_mm:g} mm")
        label_y -= 5
        if abs(wall_y_mm - wall_x_mm) > 1e-6:
            # A genuinely asymmetric wall would show up here — the
            # current CAD builder always produces a uniform wall, so
            # this branch is not expected to fire, but the drawing
            # would report it honestly rather than hiding it.
            c.drawString(_pt(ox), _pt(label_y), f"WALL THK (Y): {wall_y_mm:g} mm")

    return width_mm, depth_mm


# --- Connection detail dimension levels (mm, below/above a detail view's
# own origin — one consistent set of offsets regardless of scale/slot,
# chosen so the tightest (stacked, two-connection) layout still clears
# the page's bottom content margin). ---
_DIM_LEVEL_WIDTH_MM = 7      # width dimension, just below the plate
_DIM_LEVEL_SECONDARY_MM = 15  # hole offset / edge-distance chain
_DIM_LEVEL_DIAMETER_MM = 23   # "Ø.. mm" label
_TITLE_ABOVE_MM = 9
_CONNECTION_ID_ABOVE_MM = 2
_CHAIN_RIGHT_GAP_MM = 10


def _connection_view_title(connection) -> str:
    return f"{connection.position} CONNECTION"


def _connection_hole_records(connection) -> list[tuple[float, float, float]]:
    """
    (radius_mm, rel_x_mm, rel_y_mm) for every hole on this connection,
    relative to its own plate's actual bottom-left corner. The ONLY
    geometry sources here are GeneratedConnection.geometry_bounds and
    GeneratedConnection.holes — nothing is recomputed from a
    hole-diameter/spacing specification.
    """
    bounds = connection.geometry_bounds
    return [
        (hole.diameter / 2, hole.center[0] - bounds.xmin, hole.center[1] - bounds.ymin)
        for hole in connection.holes
    ]


def _draw_hole_line_dims(c, ox, oy, plate_right, plate_top, holes,
                          x_rel: float, ys_rel: tuple[float, float], height_mm: float, scale: float) -> None:
    """
    A single vertical line of holes (e.g. a 2-hole END_PLATE pattern):
    one shared vertical centreline through both holes plus a short
    horizontal tick through each; a horizontal offset dimension from
    the plate's left edge to the hole centreline; and a bottom-edge ->
    hole -> hole -> top-edge vertical dimension chain. Every value
    comes from the actual (x_rel, ys_rel) hole coordinates already
    extracted from GeneratedConnection.holes — never a vertical_spacing
    input value.
    """
    y_lo_rel, y_hi_rel = ys_rel
    hole_x_page = ox + x_rel * scale
    y_lo_page, y_hi_page = oy + y_lo_rel * scale, oy + y_hi_rel * scale
    radius_page = holes[0][0] * scale

    c.setLineWidth(LW_CENTERLINE)
    c.setDash(CENTERLINE_DASH)
    c.line(_pt(hole_x_page), _pt(y_lo_page - 2 * radius_page), _pt(hole_x_page), _pt(y_hi_page + 2 * radius_page))
    for _r, rel_x, rel_y in holes:
        page_x, page_y = ox + rel_x * scale, oy + rel_y * scale
        c.line(_pt(page_x - radius_page - 4), _pt(page_y), _pt(page_x + radius_page + 4), _pt(page_y))
    c.setDash([])

    _draw_horizontal_dim(c, ox, hole_x_page, oy - _DIM_LEVEL_SECONDARY_MM, f"{x_rel:g}", ext1_y=oy, ext2_y=oy)

    chain_x = plate_right + _CHAIN_RIGHT_GAP_MM
    _draw_vertical_dim(c, oy, y_lo_page, chain_x, f"{y_lo_rel:g}", ext1_x=plate_right, ext2_x=plate_right)
    _draw_vertical_dim(c, y_lo_page, y_hi_page, chain_x, f"{y_hi_rel - y_lo_rel:g}",
                        ext1_x=plate_right, ext2_x=plate_right)
    _draw_vertical_dim(c, y_hi_page, plate_top, chain_x, f"{height_mm - y_hi_rel:g}",
                        ext1_x=plate_right, ext2_x=plate_right)


def _draw_hole_grid_dims(c, ox, oy, plate_right, plate_top, holes,
                          xs_rel: tuple[float, float], ys_rel: tuple[float, float],
                          width_mm: float, height_mm: float, scale: float) -> None:
    """
    A symmetric 2x2 hole grid (e.g. a 4-hole END_PLATE pattern): two
    vertical centrelines (one per hole column) and two horizontal
    centrelines (one per hole row); a left-edge -> spacing -> right-edge
    horizontal dimension chain below the plate, and the equivalent
    bottom-edge -> spacing -> top-edge vertical chain to its right.
    Every value comes from the actual (xs_rel, ys_rel) distinct hole
    coordinates already extracted from GeneratedConnection.holes —
    never horizontal_spacing/vertical_spacing input values.
    """
    x_lo_rel, x_hi_rel = xs_rel
    y_lo_rel, y_hi_rel = ys_rel
    x_lo_page, x_hi_page = ox + x_lo_rel * scale, ox + x_hi_rel * scale
    y_lo_page, y_hi_page = oy + y_lo_rel * scale, oy + y_hi_rel * scale
    radius_page = holes[0][0] * scale

    c.setLineWidth(LW_CENTERLINE)
    c.setDash(CENTERLINE_DASH)
    for x_page in (x_lo_page, x_hi_page):
        c.line(_pt(x_page), _pt(y_lo_page - 2 * radius_page), _pt(x_page), _pt(y_hi_page + 2 * radius_page))
    for y_page in (y_lo_page, y_hi_page):
        c.line(_pt(x_lo_page - 2 * radius_page), _pt(y_page), _pt(x_hi_page + 2 * radius_page), _pt(y_page))
    c.setDash([])

    chain_y = oy - _DIM_LEVEL_SECONDARY_MM
    _draw_horizontal_dim(c, ox, x_lo_page, chain_y, f"{x_lo_rel:g}", ext1_y=oy, ext2_y=oy)
    _draw_horizontal_dim(c, x_lo_page, x_hi_page, chain_y, f"{x_hi_rel - x_lo_rel:g}", ext1_y=oy, ext2_y=oy)
    _draw_horizontal_dim(c, x_hi_page, plate_right, chain_y, f"{width_mm - x_hi_rel:g}", ext1_y=oy, ext2_y=oy)

    chain_x = plate_right + _CHAIN_RIGHT_GAP_MM
    _draw_vertical_dim(c, oy, y_lo_page, chain_x, f"{y_lo_rel:g}", ext1_x=plate_right, ext2_x=plate_right)
    _draw_vertical_dim(c, y_lo_page, y_hi_page, chain_x, f"{y_hi_rel - y_lo_rel:g}",
                        ext1_x=plate_right, ext2_x=plate_right)
    _draw_vertical_dim(c, y_hi_page, plate_top, chain_x, f"{height_mm - y_hi_rel:g}",
                        ext1_x=plate_right, ext2_x=plate_right)


def _bolt_geometry_record(bolt) -> tuple[float, float, float, float]:
    """
    (x_mm, y_mm, shaft_radius_mm, head_radius_mm) for one actual
    generated bolt solid (a cq.Workplane from GeneratedConnection.hardware,
    Milestone 6B), measured entirely from its own real CadQuery geometry
    — never from a physical_bolts input spec, and never from
    GeneratedHole.center (a different, independently-measured geometry
    source that happens to coincide with this one by construction, not
    by substitution):

      - (x, y): the bolt's own bounding-box centre in X/Y. A bolt is
        axisymmetric about its own vertical (Z) axis, so this IS the
        actual bolt axis position — measured from the bolt solid
        itself.
      - shaft_radius_mm: read off the bolt's own cylindrical (shaft)
        face — its circular edge's circumference — the same technique
        this project's CAD tests already use to verify shaft diameter.
      - head_radius_mm: half of the bolt's own overall bounding-box
        width. Every bolt's hex head is wider than its shaft, so the
        bolt's XY bounding box is governed by the head — this is the
        head's actual circumscribed (corner-to-corner) extent, a real
        measured quantity that scales directly with head_across_flats,
        though not numerically equal to it. A schematic circle (not a
        hex outline) is drawn from it — see _draw_connection_hardware()
        — sufficient for "a bolt exists here, at roughly this size"
        without claiming a precise hex drawing.

    Z-axis quantities (shaft length, head thickness) have no
    meaningful representation in the plate-face (XY-projection) view
    this feeds and are deliberately not extracted here.
    """
    bbox = bolt.val().BoundingBox()
    x = (bbox.xmin + bbox.xmax) / 2
    y = (bbox.ymin + bbox.ymax) / 2
    head_radius_mm = max(bbox.xlen, bbox.ylen) / 2

    shaft_faces = [f for f in bolt.val().Faces() if f.geomType() == "CYLINDER"]
    shaft_radius_mm = 0.0
    if shaft_faces:
        circle_edges = [e for e in shaft_faces[0].Edges() if e.geomType() == "CIRCLE"]
        if circle_edges:
            shaft_radius_mm = circle_edges[0].Length() / (2 * math.pi)

    return x, y, shaft_radius_mm, head_radius_mm


def _draw_connection_hardware(c, connection, origin_mm: tuple[float, float], scale: float) -> int:
    """
    A simple 2D orthographic representation of each ACTUAL physical
    bolt in connection.hardware (Milestone 6F): a dashed circle at the
    bolt's own measured head extent, plus a small centre-mark cross at
    its own measured shaft/axis position. A filled dot was tried first
    but rejected: this fixture's shaft radius is close to (START) or
    exactly equal to (END, both Ø20) the hole radius, so a filled
    circle there visually swallows the hole outline underneath it —
    exactly the "double-drawn/confusing" outcome this milestone warns
    against. A centre-mark cross stays legible and clearly distinct
    from the hole circle regardless of the shaft/hole size relationship.
    Consumes ONLY `connection` (never GeneratedMemberGeometry.solid,
    never GeneratedMemberGeometry.hardware, never a ValidatedConnection
    spec). connection.hardware == [] (physical bolts not requested)
    draws nothing — the view is unchanged from Milestone 6E. Returns
    the number of bolts drawn.
    """
    ox, oy = origin_mm
    bounds = connection.geometry_bounds
    x_min, y_min = bounds.xmin, bounds.ymin

    for bolt in connection.hardware:
        bolt_x, bolt_y, shaft_radius_mm, head_radius_mm = _bolt_geometry_record(bolt)
        page_x = ox + (bolt_x - x_min) * scale
        page_y = oy + (bolt_y - y_min) * scale

        c.setLineWidth(LW_HARDWARE)
        c.setDash(HARDWARE_DASH)
        c.circle(_pt(page_x), _pt(page_y), _pt(head_radius_mm * scale), stroke=1, fill=0)
        c.setDash([])

        mark_mm = max(shaft_radius_mm * scale * 0.55, 1.1)  # legible regardless of scale/shaft size
        c.setLineWidth(LW_HARDWARE)
        c.line(_pt(page_x - mark_mm), _pt(page_y), _pt(page_x + mark_mm), _pt(page_y))
        c.line(_pt(page_x), _pt(page_y - mark_mm), _pt(page_x), _pt(page_y + mark_mm))

    return len(connection.hardware)


_INFO_BLOCK_LINE_HEIGHT_MM = 4.0
_INFO_BLOCK_RIGHT_GAP_MM = 20  # beyond the vertical dimension chain, clear of its rotated labels


def _diameter_groups(diameters: list[float]) -> list[tuple[int, float]]:
    """
    (count, diameter_mm) for each distinct diameter present, sorted by
    diameter ascending — groups actual measured diameters, never
    assumes every hole/bolt in a connection shares one value.
    """
    counts = Counter(round(d, 3) for d in diameters)
    return sorted(((count, dia) for dia, count in counts.items()), key=lambda t: t[1])


def _diameter_summary_lines(label: str, diameters: list[float], suffix: str = "") -> list[str]:
    """
    "LABEL: n x Ø.." on one line when every value is the same diameter;
    a "LABEL:" header plus one indented line per distinct diameter when
    they differ (Milestone 6G's mixed-diameter handling). Returns []
    when `diameters` is empty — no invented "0 x Ø0" line.
    """
    if not diameters:
        return []
    groups = _diameter_groups(diameters)
    if len(groups) == 1:
        count, dia = groups[0]
        return [f"{label}: {count} × Ø{dia:g}{suffix}"]
    lines = [f"{label}:"]
    lines.extend(f"  {count} × Ø{dia:g}{suffix}" for count, dia in groups)
    return lines


def _pattern_summary_line(connection) -> str | None:
    """
    "PATTERN: .. V" for a genuine 2-hole vertical line, or
    "PATTERN: .. H x .. V" for a genuine symmetric 4-hole rectangular
    grid — determined from the actual distinct X/Y coordinates in
    connection.holes, and ONLY when the hole count exactly matches
    what that coordinate shape implies (2 holes for 1 distinct X / 2
    distinct Y; 4 holes for 2 distinct X / 2 distinct Y — with only 2
    unique values on each axis, 4 distinct points can only mean every
    combination is present, i.e. a genuine rectangular grid, not a
    coincidence). Any other hole count or coordinate shape returns
    None — Milestone 6G's "never label arbitrary geometry a pattern"
    safety rule — so an irregular layout gets no pattern line at all,
    rather than a misleading one.
    """
    holes = connection.holes
    if len(holes) < 2:
        return None

    xs = sorted({round(h.center[0], 3) for h in holes})
    ys = sorted({round(h.center[1], 3) for h in holes})

    if len(xs) == 1 and len(ys) == 2 and len(holes) == 2:
        return f"PATTERN: {ys[1] - ys[0]:g} V"
    if len(xs) == 2 and len(ys) == 2 and len(holes) == 4:
        return f"PATTERN: {xs[1] - xs[0]:g} H × {ys[1] - ys[0]:g} V"
    return None


def _connection_info_lines(connection) -> list[str]:
    """
    The Milestone 6G fabrication-information summary for one
    connection: PLATE / HOLES / BOLTS / PATTERN, built entirely from
    GeneratedConnection's own generated geometry:
      - PLATE width/height/thickness: connection.geometry_bounds
        (xlen/ylen/zlen) — thickness is a max-min Z span, so it is
        correct whether the plate sits at negative Z (START) or beyond
        the member's length (END); never plates[0]['thickness'].
      - HOLES: connection.holes' own diameters; never bolts[0]['diameter'].
      - BOLTS: each hardware solid's own measured shaft diameter (see
        _bolt_geometry_record()); never physical_bolts['shaft_diameter'].
        Omitted entirely when connection.hardware is empty.
      - PATTERN: see _pattern_summary_line() — omitted unless the
        actual hole layout unambiguously supports it.
    """
    bounds = connection.geometry_bounds
    lines = [f"PLATE: {bounds.xlen:g} × {bounds.ylen:g} × {bounds.zlen:g} mm"]

    lines.extend(_diameter_summary_lines("HOLES", [hole.diameter for hole in connection.holes]))

    bolt_shaft_diameters = [2 * _bolt_geometry_record(bolt)[2] for bolt in connection.hardware]
    lines.extend(_diameter_summary_lines("BOLTS", bolt_shaft_diameters, suffix=" SHAFT"))

    pattern_line = _pattern_summary_line(connection)
    if pattern_line:
        lines.append(pattern_line)

    return lines


def _draw_connection_info_block(c, connection, plate_right: float, plate_top: float) -> None:
    """
    Draws _connection_info_lines() as a small left-aligned text block
    to the right of the plate's own dimension chain — a region with no
    other content at any connection-detail scale/slot this module
    uses, chosen specifically to avoid crossing dimension lines or
    colliding with bolt graphics (Milestone 6G's explicit visual
    requirement).
    """
    lines = _connection_info_lines(connection)
    if not lines:
        return
    info_x = plate_right + _CHAIN_RIGHT_GAP_MM + _INFO_BLOCK_RIGHT_GAP_MM
    c.setFont(*FONT_INFO_LABEL)
    y = plate_top
    for line in lines:
        c.drawString(_pt(info_x), _pt(y), line)
        y -= _INFO_BLOCK_LINE_HEIGHT_MM


def _draw_connection_detail(c, connection, origin_mm: tuple[float, float], scale: float,
                             title: str | None = None) -> tuple[float, float]:
    """
    One connection's detail view — plate outline, hole circles,
    centrelines and dimensions — drawn entirely from the Milestone 6D
    geometry contract: GeneratedConnection.geometry_bounds for the
    plate's own actual bounding box, GeneratedConnection.holes for
    every circle/dimension. This function is handed exactly one
    GeneratedConnection; it never touches GeneratedMemberGeometry.solid,
    so there is no connection topology here to rediscover from raw
    CadQuery faces/wires — unlike the Milestone-2/3 _draw_end_plate_view()
    this replaces, which read geometry.solid.faces(">Z") directly. Works
    identically for any hole pattern shape or section family: it only
    ever asks "how many distinct hole X/Y coordinates are there", never
    "which family" or "how many holes did the input spec request".
    Returns (width_page, height_page) actually drawn.

    `title`, added in Milestone 7T, overrides the default
    "{connection.position} CONNECTION" heading (_connection_view_title())
    — needed because a two-member GeneratedConnectionAssembly's
    `.position`-equivalent field has no single-member positional
    meaning (see pdf_builder.build_connection_pdf()). Every existing
    single-member call site omits it and keeps its exact prior heading.
    """
    ox, oy = origin_mm
    bounds = connection.geometry_bounds
    width_mm, height_mm = bounds.xlen, bounds.ylen
    width_page, height_page = width_mm * scale, height_mm * scale
    plate_right, plate_top = ox + width_page, oy + height_page

    c.setLineWidth(LW_OBJECT)
    c.rect(_pt(ox), _pt(oy), _pt(width_page), _pt(height_page), stroke=1, fill=0)

    _draw_view_title(c, ox, plate_top + _TITLE_ABOVE_MM, title or _connection_view_title(connection))
    c.setFont(*FONT_INFO_LABEL)
    c.drawString(_pt(ox), _pt(plate_top + _CONNECTION_ID_ABOVE_MM), connection.connection_id)

    holes = _connection_hole_records(connection)
    for radius_mm, rel_x, rel_y in holes:
        page_x, page_y = ox + rel_x * scale, oy + rel_y * scale
        c.setLineWidth(LW_OBJECT)
        c.circle(_pt(page_x), _pt(page_y), _pt(radius_mm * scale), stroke=1, fill=0)

    # Physical bolt hardware (Milestone 6F) — optional; draws nothing when
    # connection.hardware is empty, leaving the Milestone 6E view unchanged.
    # Drawn from connection.hardware alone (this connection's own generated
    # bolts, never GeneratedMemberGeometry.hardware's flat cross-connection
    # list), so ownership is inherently correct — there is nothing here to
    # get wrong by mixing up which bolts belong to this connection.
    _draw_connection_hardware(c, connection, (ox, oy), scale)

    # Fabrication-information summary (Milestone 6G) — PLATE/HOLES/BOLTS/
    # PATTERN, the one place this information now lives (replacing the
    # standalone "Ø.. mm" / "N BOLTS" labels earlier milestones drew).
    _draw_connection_info_block(c, connection, plate_right, plate_top)

    _draw_horizontal_dim(c, ox, plate_right, oy - _DIM_LEVEL_WIDTH_MM, f"{width_mm:g}", ext1_y=oy, ext2_y=oy)
    _draw_vertical_dim(c, oy, plate_top, ox - 10, f"{height_mm:g}", ext1_x=ox, ext2_x=ox)

    distinct_x = sorted({round(rel_x, 6) for _r, rel_x, _ry in holes})
    distinct_y = sorted({round(rel_y, 6) for _r, _rx, rel_y in holes})

    if len(distinct_x) == 1 and len(distinct_y) == 2:
        _draw_hole_line_dims(c, ox, oy, plate_right, plate_top, holes,
                              distinct_x[0], (distinct_y[0], distinct_y[1]), height_mm, scale)
    elif len(distinct_x) == 2 and len(distinct_y) == 2:
        _draw_hole_grid_dims(c, ox, oy, plate_right, plate_top, holes,
                              (distinct_x[0], distinct_x[1]), (distinct_y[0], distinct_y[1]),
                              width_mm, height_mm, scale)

    return width_page, height_page


def _draw_tb_grid_row(c, ox: float, top_y: float, width_mm: float,
                       cols: list[tuple[str, str]]) -> float:
    """
    One title-block row: a label sub-line above a bold value sub-line,
    in `len(cols)` evenly-split columns, with vertical dividers
    between them and a horizontal divider at the bottom. Returns the
    row's bottom Y.
    """
    label_h, value_h = 4.2, 6.3
    row_h = label_h + value_h
    bottom_y = top_y - row_h
    col_w = width_mm / len(cols)

    for i, (label, value) in enumerate(cols):
        cx = ox + i * col_w
        if i > 0:
            c.setLineWidth(LW_TITLE_BLOCK * 0.5)
            c.line(_pt(cx), _pt(bottom_y), _pt(cx), _pt(top_y))
        c.setFont(*FONT_TB_LABEL)
        c.drawString(_pt(cx + 2), _pt(top_y - label_h + 1), label)
        c.setFont(*FONT_TB_VALUE)
        c.drawString(_pt(cx + 2), _pt(bottom_y + 1.6), value)

    c.setLineWidth(LW_TITLE_BLOCK * 0.5)
    c.line(_pt(ox), _pt(bottom_y), _pt(ox + width_mm), _pt(bottom_y))
    return bottom_y


def _draw_title_block(c, geometry, quantity: int, metadata: DrawingMetadata,
                       origin_mm: tuple[float, float]) -> None:
    """
    Structured title block: sheet title, drawing-admin row (number/
    rev/date), member row (mark/section/length/qty), the sheet status
    row (material/units/scale/page — and status only when supplied),
    and a connection row only when the geometry actually has one —
    never shown on a bare member.
    """
    ox, oy_bottom = origin_mm
    width_mm = TITLE_BLOCK_WIDTH_MM

    connection_types = getattr(geometry, "connection_types", None) or []
    title_h, subtitle_h = 9.0, 6.5
    grid_row_h = 4.2 + 6.3
    n_grid_rows = 3 + (1 if connection_types else 0)
    total_h = title_h + subtitle_h + grid_row_h * n_grid_rows

    top_y = oy_bottom + total_h
    c.setLineWidth(LW_TITLE_BLOCK)
    c.rect(_pt(ox), _pt(oy_bottom), _pt(width_mm), _pt(total_h), stroke=1, fill=0)

    cursor = top_y
    c.setFont(*FONT_SHEET_TITLE)
    c.drawString(_pt(ox + 3), _pt(cursor - title_h + 2.5), "STEELSPEC")
    cursor -= title_h
    c.setLineWidth(LW_TITLE_BLOCK)
    c.line(_pt(ox), _pt(cursor), _pt(ox + width_mm), _pt(cursor))

    c.setFont(*FONT_TB_SUBTITLE)
    c.drawString(_pt(ox + 3), _pt(cursor - subtitle_h + 2), "FABRICATION DRAWING")
    cursor -= subtitle_h
    c.line(_pt(ox), _pt(cursor), _pt(ox + width_mm), _pt(cursor))

    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("DRAWING NO.", metadata.drawing_number),
        ("REV", metadata.revision),
        ("DATE", metadata.date),
    ])
    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("MARK", geometry.mark),
        ("SECTION", geometry.section_name),
        ("LENGTH", f"{geometry.length_mm:g} mm"),
        ("QTY", str(quantity)),
    ])
    cursor = _draw_sheet_status_row(c, ox, cursor, width_mm, metadata)
    if connection_types:
        label = " + ".join(t.replace("_", " ") for t in connection_types)
        _draw_tb_grid_row(c, ox, cursor, width_mm, [("CONNECTION", label)])


# --- Milestone 7T: two-member reviewed connection drawing ---

_TWO_MEMBER_HOLE_TICK_HALF_MM = 2.0  # a projected mark, not a real dimension


def _connection_assembly_as_generated_connection(assembly):
    """
    Wraps a GeneratedConnectionAssembly's own `.plate`/`.holes`/
    `.hardware` (7K/7L) in the existing GeneratedConnection (Milestone
    6D) shape, so _draw_connection_detail() can be reused completely
    unchanged for the two-member case — and, unchanged, for 7AV's
    multi-member assembly, which carries the same
    connection_geometry.connection_id/plate/holes/hardware and
    connection.connection_type/position attributes. Not a new
    hole/plate representation and not a copy: `.plate` and `.holes`
    are the exact same objects the assembly already carries (see
    module docstring). `connection_type`/`position` are copied from
    the assembly's own ValidatedConnection — real, already-validated
    values, never invented — even though `position` has no
    single-member positional meaning for a two-member connection
    assembly (see build_connection_pdf() and
    build_multi_member_connection_pdf(), which always supply an
    explicit `title` to _draw_connection_detail() so `position` is
    never read for the heading here).
    """
    return GeneratedConnection(
        connection_id=assembly.connection_geometry.connection_id,
        connection_type=assembly.connection.connection_type,
        position=assembly.connection.position,
        plate=assembly.connection_geometry.plate,
        holes=assembly.connection_geometry.holes,
        hardware=assembly.connection_geometry.hardware,
    )


def _draw_two_member_elevation(c, assembly, origin_mm: tuple[float, float]) -> tuple[float, float]:
    """
    Project-space side elevation of the actual reviewed connection:
    Member A's own placed solid, Member B's own placed solid, and the
    connection plate's own positioned solid, each drawn as a rectangle
    read directly from that solid's own project-space BoundingBox() —
    never from length_mm/plate width-height metadata, and never
    assuming either member starts at project Z=0. The project-space Z
    axis (shared by both members and the connection, per the currently
    supported axial-splice topology — see two_member_connection.py) is
    horizontal; each solid's own Y-extent ("depth", the same axis
    _draw_elevation() already uses for a single member) is vertical.
    This is a drawing PROJECTION only — nothing here mutates the
    underlying CAD geometry or the solids' own coordinates.

    Hole positions (where the actual generated hole geometry supports
    it) are shown as short tick marks — not circles, which are reserved
    for the connection detail view (View 2) so a raw-content-stream
    hole count stays unambiguous between the two views — at each
    hole's own measured (z, y) centre, projected onto this view; holes
    that differ only in X (not visible from this viewing direction)
    correctly coincide into one mark, matching a genuine engineering
    side elevation rather than being an error.

    Returns (width_page, height_page) actually drawn.
    """
    ox, oy = origin_mm
    bbox_a = assembly.member_a.geometry.solid.val().BoundingBox()
    bbox_b = assembly.member_b.geometry.solid.val().BoundingBox()
    bbox_plate = assembly.connection_geometry.plate.val().BoundingBox()

    z_min = min(bbox_a.zmin, bbox_b.zmin, bbox_plate.zmin)
    z_max = max(bbox_a.zmax, bbox_b.zmax, bbox_plate.zmax)

    def to_page_x(z_mm: float) -> float:
        return ox + (z_mm - z_min) * ELEVATION_SCALE

    def to_page_y(y_mm: float) -> float:
        return oy + y_mm * ELEVATION_SCALE

    max_depth_page = max(bbox_a.ymax, bbox_b.ymax) * ELEVATION_SCALE
    _draw_view_title(c, ox, oy + max_depth_page + 20, "CONNECTION ELEVATION")

    c.setLineWidth(LW_OBJECT)
    for bbox, mark in ((bbox_a, assembly.member_a.mark), (bbox_b, assembly.member_b.mark)):
        x1, x2 = to_page_x(bbox.zmin), to_page_x(bbox.zmax)
        y1, y2 = to_page_y(bbox.ymin), to_page_y(bbox.ymax)
        c.rect(_pt(x1), _pt(y1), _pt(x2 - x1), _pt(y2 - y1), stroke=1, fill=0)
        c.setFont(*FONT_MARK_LABEL)
        c.drawCentredString(_pt((x1 + x2) / 2), _pt(y2 + 6), mark)

    plate_x1, plate_x2 = to_page_x(bbox_plate.zmin), to_page_x(bbox_plate.zmax)
    plate_y1, plate_y2 = to_page_y(bbox_plate.ymin), to_page_y(bbox_plate.ymax)
    c.setLineWidth(LW_OBJECT)
    c.rect(_pt(plate_x1), _pt(plate_y1), _pt(plate_x2 - plate_x1), _pt(plate_y2 - plate_y1), stroke=1, fill=0)

    plate_mid_x = to_page_x((bbox_plate.zmin + bbox_plate.zmax) / 2)
    seen_y_page: set[float] = set()
    for hole in assembly.connection_geometry.holes:
        hy_page = round(to_page_y(hole.center[1]), 3)
        if hy_page in seen_y_page:
            continue
        seen_y_page.add(hy_page)
        c.setLineWidth(LW_OBJECT)
        c.line(_pt(plate_mid_x - _TWO_MEMBER_HOLE_TICK_HALF_MM), _pt(hy_page),
               _pt(plate_mid_x + _TWO_MEMBER_HOLE_TICK_HALF_MM), _pt(hy_page))

    x_end_page = to_page_x(z_max)
    _draw_horizontal_dim(c, ox, x_end_page, oy - 10, f"{z_max - z_min:g}", ext1_y=oy, ext2_y=oy)

    return x_end_page - ox, max_depth_page


def _draw_two_member_title_block(c, assembly, metadata: DrawingMetadata, origin_mm: tuple[float, float]) -> None:
    """
    The Milestone 7T title block: the same sheet-title/subtitle header
    and admin/status rows _draw_title_block() already draws (built with
    the identical _draw_tb_grid_row() primitive — no new title-block
    rendering logic), with one grid row per member (mark/section/length,
    from that member's own already-validated GeneratedMemberGeometry)
    instead of one grid row for a single member. QTY is omitted — a
    two-member connection drawing names two specific, individually
    marked members, not a repeated quantity of one identical part.

    MILESTONE 7AW: each member row also carries the member's OWN
    reviewed attachment semantics (ATTACH — the ConnectionAttachment
    the reviewed assembly pairs with that member, never inferred), and
    a CONNECTION LOCATION row prints the reviewed ConnectionLocation
    (see _connection_location_text()) between the member rows and the
    sheet status row.
    """
    ox, oy_bottom = origin_mm
    width_mm = TITLE_BLOCK_WIDTH_MM
    member_a = assembly.member_a.geometry
    member_b = assembly.member_b.geometry

    title_h, subtitle_h = 9.0, 6.5
    grid_row_h = 4.2 + 6.3
    # Admin, member A, member B, connection location, status — plus the
    # project row when the caller supplied job identity (Milestone 7AR).
    # Absent identity is never invented: the row is simply not drawn,
    # and the default title-block height is unchanged.
    has_project_row = metadata.project_id is not None or metadata.source_drawing_id is not None
    n_grid_rows = 6 if has_project_row else 5
    total_h = title_h + subtitle_h + grid_row_h * n_grid_rows

    top_y = oy_bottom + total_h
    c.setLineWidth(LW_TITLE_BLOCK)
    c.rect(_pt(ox), _pt(oy_bottom), _pt(width_mm), _pt(total_h), stroke=1, fill=0)

    cursor = top_y
    c.setFont(*FONT_SHEET_TITLE)
    c.drawString(_pt(ox + 3), _pt(cursor - title_h + 2.5), "STEELSPEC")
    cursor -= title_h
    c.setLineWidth(LW_TITLE_BLOCK)
    c.line(_pt(ox), _pt(cursor), _pt(ox + width_mm), _pt(cursor))

    c.setFont(*FONT_TB_SUBTITLE)
    c.drawString(_pt(ox + 3), _pt(cursor - subtitle_h + 2), "FABRICATION DRAWING")
    cursor -= subtitle_h
    c.line(_pt(ox), _pt(cursor), _pt(ox + width_mm), _pt(cursor))

    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("DRAWING NO.", metadata.drawing_number),
        ("REV", metadata.revision),
        ("DATE", metadata.date),
    ])
    if has_project_row:
        # Job identity, exactly as the caller supplied it (7AR: the
        # workflow passes its own project id and source drawing id —
        # identifiers, not engineering values). A cell without a value
        # reads NOT SPECIFIED, the title block's established honest
        # vocabulary for missing information.
        cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
            ("PROJECT", metadata.project_id or "NOT SPECIFIED"),
            ("SOURCE DRAWING", metadata.source_drawing_id or "NOT SPECIFIED"),
        ])
    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("MEMBER A", member_a.mark),
        ("SECTION", member_a.section_name),
        ("LENGTH", f"{member_a.length_mm:g} mm"),
        ("ATTACH", assembly.attachment_a.surface_reference),
    ])
    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("MEMBER B", member_b.mark),
        ("SECTION", member_b.section_name),
        ("LENGTH", f"{member_b.length_mm:g} mm"),
        ("ATTACH", assembly.attachment_b.surface_reference),
    ])
    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("CONNECTION LOCATION", _connection_location_text(assembly.location)),
    ])
    _draw_sheet_status_row(c, ox, cursor, width_mm, metadata)


def build_connection_pdf(
    assembly,
    status: str | None = None,
    *,
    material: str | None = None,
    revision: str = "A",
    date: _datetime.date | None = None,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
) -> bytes:
    """
    Builds the full single-sheet PDF connection drawing (border,
    project-space connection elevation, connection detail view, and a
    two-member title block) for one reviewed two-member connection
    assembly (app.cad_engine.reviewed_connection_assembly.
    ReviewedTwoMemberConnectionAssembly), on an A3 landscape page.

    Every dimension and every drawn shape is read from the assembly's
    own already-generated CAD geometry (member_a.geometry.solid,
    member_b.geometry.solid, connection_geometry.plate,
    connection_geometry.holes) — never rebuilt from plate/hole/member
    input specs (see module docstring's MILESTONE 7T note). The title
    block prints the reviewed CONNECTION LOCATION and each member's
    reviewed ATTACH semantics from the assembly itself (MILESTONE 7AW).
    Returns the PDF as bytes — writing it to disk is the caller's job,
    matching build_pdf()'s own convention.

    `status` (MILESTONE 7AW) is now optional and never defaulted: None
    draws no STATUS cell at all — the drawing never implies an approval
    state that was never recorded. A supplied status is printed
    verbatim, exactly as before.
    """
    resolved_date = (date or _datetime.date.today()).strftime("%d/%m/%Y")
    metadata = DrawingMetadata(
        drawing_number=f"FAB-{assembly.connection.connection_id}",
        revision=revision,
        date=resolved_date,
        status=status,
        material=material or "NOT SPECIFIED",
        project_id=project_id,
        source_drawing_id=source_drawing_id,
    )

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    _draw_border(c)

    _draw_two_member_elevation(c, assembly, ELEVATION_ORIGIN_MM)

    generated_connection = _connection_assembly_as_generated_connection(assembly)
    _draw_connection_detail(
        c, generated_connection, CROSS_SECTION_ORIGIN_MM, CONNECTION_DETAIL_SINGLE_SCALE,
        title=f"CONNECTION DETAIL — {assembly.connection_geometry.connection_id}",
    )

    _draw_two_member_title_block(c, assembly, metadata, TITLE_BLOCK_ORIGIN_MM)

    c.showPage()
    c.save()
    return buffer.getvalue()


# --- Milestone 7AV: multi-member (3+) reviewed connection drawing ---
#
# The two-member drawing above is left COMPLETELY unchanged. The
# multi-member sheet below is the smallest extension that represents
# EVERY member a 3+-member connection names — built with the same
# drawing primitives (_draw_tb_grid_row, _draw_view_title,
# _draw_horizontal_dim, _draw_connection_detail) and the same
# already-validated project-space solids, one grid row and one
# elevation rectangle per member, never a subset and never a
# "main member" selection.


def _draw_multi_member_elevation(c, assembly, origin_mm: tuple[float, float]) -> tuple[float, float]:
    """
    Project-space side elevation of the actual reviewed multi-member
    connection: EVERY member's own placed solid (in the connection's
    own member order — assembly.members) and the connection plate's
    own positioned solid, each drawn as a rectangle read directly
    from that solid's own project-space BoundingBox() — never from
    length_mm/plate width-height metadata, never assuming a member
    starts at project Z=0, and never omitting a member to keep the
    view tidy. Same projection and hole-tick conventions as
    _draw_two_member_elevation() (see its docstring): project Z is
    horizontal, each solid's own Y-extent is vertical, and holes are
    drawn as short tick marks at each hole's own measured (z, y)
    centre, so a raw-content-stream hole count stays unambiguous
    between this view and the connection detail view. A drawing
    PROJECTION only — nothing here mutates the underlying CAD
    geometry or the solids' own coordinates.

    Returns (width_page, height_page) actually drawn.
    """
    ox, oy = origin_mm
    member_bboxes = [
        (member.mark, member.geometry.solid.val().BoundingBox()) for member in assembly.members
    ]
    bbox_plate = assembly.connection_geometry.plate.val().BoundingBox()

    z_min = min([bbox_plate.zmin] + [bbox.zmin for _mark, bbox in member_bboxes])
    z_max = max([bbox_plate.zmax] + [bbox.zmax for _mark, bbox in member_bboxes])

    def to_page_x(z_mm: float) -> float:
        return ox + (z_mm - z_min) * ELEVATION_SCALE

    def to_page_y(y_mm: float) -> float:
        return oy + y_mm * ELEVATION_SCALE

    max_depth_page = max(
        [bbox_plate.ymax] + [bbox.ymax for _mark, bbox in member_bboxes]
    ) * ELEVATION_SCALE
    _draw_view_title(c, ox, oy + max_depth_page + 20, "CONNECTION ELEVATION")

    c.setLineWidth(LW_OBJECT)
    for mark, bbox in member_bboxes:
        x1, x2 = to_page_x(bbox.zmin), to_page_x(bbox.zmax)
        y1, y2 = to_page_y(bbox.ymin), to_page_y(bbox.ymax)
        c.rect(_pt(x1), _pt(y1), _pt(x2 - x1), _pt(y2 - y1), stroke=1, fill=0)
        c.setFont(*FONT_MARK_LABEL)
        c.drawCentredString(_pt((x1 + x2) / 2), _pt(y2 + 6), mark)

    plate_x1, plate_x2 = to_page_x(bbox_plate.zmin), to_page_x(bbox_plate.zmax)
    plate_y1, plate_y2 = to_page_y(bbox_plate.ymin), to_page_y(bbox_plate.ymax)
    c.setLineWidth(LW_OBJECT)
    c.rect(_pt(plate_x1), _pt(plate_y1), _pt(plate_x2 - plate_x1), _pt(plate_y2 - plate_y1), stroke=1, fill=0)

    plate_mid_x = to_page_x((bbox_plate.zmin + bbox_plate.zmax) / 2)
    # Same projected-mark constant the two-member elevation uses (its
    # name is historical; the semantics — a projected tick, not a real
    # dimension — are identical).
    seen_y_page: set[float] = set()
    for hole in assembly.connection_geometry.holes:
        hy_page = round(to_page_y(hole.center[1]), 3)
        if hy_page in seen_y_page:
            continue
        seen_y_page.add(hy_page)
        c.setLineWidth(LW_OBJECT)
        c.line(_pt(plate_mid_x - _TWO_MEMBER_HOLE_TICK_HALF_MM), _pt(hy_page),
               _pt(plate_mid_x + _TWO_MEMBER_HOLE_TICK_HALF_MM), _pt(hy_page))

    x_end_page = to_page_x(z_max)
    _draw_horizontal_dim(c, ox, x_end_page, oy - 10, f"{z_max - z_min:g}", ext1_y=oy, ext2_y=oy)

    return x_end_page - ox, max_depth_page


def _draw_multi_member_title_block(c, assembly, metadata: DrawingMetadata, origin_mm: tuple[float, float]) -> None:
    """
    The Milestone 7AV title block: the same sheet-title/subtitle
    header and admin/status rows _draw_two_member_title_block() draws
    (identical _draw_tb_grid_row() primitive — no new title-block
    rendering logic), with one grid row PER MEMBER instead of two:
    labels "MEMBER A", "MEMBER B", "MEMBER C", ... in the
    connection's own member order (member_label() — the same letter
    vocabulary 7AR/7AS use), each from that member's own
    already-validated GeneratedMemberGeometry. QTY is omitted, for
    the same reason as the two-member block: a multi-member
    connection drawing names specific, individually marked members,
    not a repeated quantity of one identical part. The block's height
    grows with the member count — every member gets its own row, and
    no member is ever dropped to keep the block short.

    MILESTONE 7AW: each member row also carries that member's OWN
    reviewed attachment semantics (ATTACH — the ConnectionAttachment
    whose member_mark is that member's mark, matched explicitly and
    refused when absent, never guessed from member order), and a
    CONNECTION LOCATION row prints the reviewed ConnectionLocation
    (see _connection_location_text()) between the member rows and the
    sheet status row.
    """
    ox, oy_bottom = origin_mm
    width_mm = TITLE_BLOCK_WIDTH_MM

    title_h, subtitle_h = 9.0, 6.5
    grid_row_h = 4.2 + 6.3
    # Admin, one row per member, connection location, status — plus the
    # project row when the caller supplied job identity (Milestone 7AR).
    # Absent identity is never invented: the row is simply not drawn.
    has_project_row = metadata.project_id is not None or metadata.source_drawing_id is not None
    n_grid_rows = len(assembly.members) + 3 + (1 if has_project_row else 0)
    total_h = title_h + subtitle_h + grid_row_h * n_grid_rows

    top_y = oy_bottom + total_h
    c.setLineWidth(LW_TITLE_BLOCK)
    c.rect(_pt(ox), _pt(oy_bottom), _pt(width_mm), _pt(total_h), stroke=1, fill=0)

    cursor = top_y
    c.setFont(*FONT_SHEET_TITLE)
    c.drawString(_pt(ox + 3), _pt(cursor - title_h + 2.5), "STEELSPEC")
    cursor -= title_h
    c.setLineWidth(LW_TITLE_BLOCK)
    c.line(_pt(ox), _pt(cursor), _pt(ox + width_mm), _pt(cursor))

    c.setFont(*FONT_TB_SUBTITLE)
    c.drawString(_pt(ox + 3), _pt(cursor - subtitle_h + 2), "FABRICATION DRAWING")
    cursor -= subtitle_h
    c.line(_pt(ox), _pt(cursor), _pt(ox + width_mm), _pt(cursor))

    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("DRAWING NO.", metadata.drawing_number),
        ("REV", metadata.revision),
        ("DATE", metadata.date),
    ])
    if has_project_row:
        # Job identity, exactly as the caller supplied it (7AR) — the
        # same row and vocabulary the two-member block draws.
        cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
            ("PROJECT", metadata.project_id or "NOT SPECIFIED"),
            ("SOURCE DRAWING", metadata.source_drawing_id or "NOT SPECIFIED"),
        ])
    # The reviewed assembly guarantees exactly one attachment per
    # member mark; the lookup is still explicit per member, and an
    # attachment the assembly does not carry is refused — never
    # guessed from member order (MILESTONE 7AW).
    attachment_by_mark = {a.member_mark: a for a in assembly.attachments}
    for index, member in enumerate(assembly.members):
        geometry = member.geometry
        attachment = attachment_by_mark.get(member.mark)
        if attachment is None:
            raise DrawingValidationError(
                f"Connection '{assembly.connection.connection_id}': the reviewed assembly "
                f"carries no attachment for member '{member.mark}' — refusing to draw an "
                "attachment semantic that was never reviewed."
            )
        cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
            (member_label(index), geometry.mark),
            ("SECTION", geometry.section_name),
            ("LENGTH", f"{geometry.length_mm:g} mm"),
            ("ATTACH", attachment.surface_reference),
        ])
    cursor = _draw_tb_grid_row(c, ox, cursor, width_mm, [
        ("CONNECTION LOCATION", _connection_location_text(assembly.location)),
    ])
    _draw_sheet_status_row(c, ox, cursor, width_mm, metadata)


def build_multi_member_connection_pdf(
    assembly,
    status: str | None = None,
    *,
    material: str | None = None,
    revision: str = "A",
    date: _datetime.date | None = None,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
) -> bytes:
    """
    Builds the full single-sheet PDF connection drawing (border,
    project-space connection elevation, connection detail view, and a
    per-member title block) for one reviewed multi-member connection
    assembly (app.cad_engine.multi_member_connection.
    ReviewedMultiMemberConnectionAssembly), on an A3 landscape page.

    Every dimension and every drawn shape is read from the assembly's
    own already-generated CAD geometry (each member's own
    geometry.solid, connection_geometry.plate,
    connection_geometry.holes) — never rebuilt from plate/hole/member
    input specs, and never from a subset of the members (see module
    docstring's MILESTONE 7AV note). The connection detail view
    reuses _connection_assembly_as_generated_connection() unchanged:
    a multi-member assembly carries the same
    connection_geometry.connection_id/plate/holes/hardware and
    connection.connection_type/position attributes, and no new
    detail-view representation is needed.

    Refuses (DrawingValidationError) rather than mislabelling when
    the assembly names more members than the single-letter vocabulary
    ("MEMBER A".."MEMBER Z") can label — an explicit boundary, never
    a silent truncation. Returns the PDF as bytes — writing it to
    disk is the caller's job, matching build_pdf()'s own convention.

    `status` (MILESTONE 7AW) is now optional and never defaulted: None
    draws no STATUS cell at all — the drawing never implies an approval
    state that was never recorded. A supplied status is printed
    verbatim, exactly as before.
    """
    if len(assembly.members) > len(MEMBER_LABEL_LETTERS):
        raise DrawingValidationError(
            f"Connection '{assembly.connection.connection_id}': this drawing generator labels "
            f"members 'MEMBER A'..'MEMBER {MEMBER_LABEL_LETTERS[-1]}' only, but the reviewed "
            f"assembly names {len(assembly.members)} members. Refusing rather than truncating or "
            "re-using a label — the member labelling vocabulary itself must be extended first."
        )

    resolved_date = (date or _datetime.date.today()).strftime("%d/%m/%Y")
    metadata = DrawingMetadata(
        drawing_number=f"FAB-{assembly.connection.connection_id}",
        revision=revision,
        date=resolved_date,
        status=status,
        material=material or "NOT SPECIFIED",
        project_id=project_id,
        source_drawing_id=source_drawing_id,
    )

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    _draw_border(c)

    _draw_multi_member_elevation(c, assembly, ELEVATION_ORIGIN_MM)

    generated_connection = _connection_assembly_as_generated_connection(assembly)
    _draw_connection_detail(
        c, generated_connection, CROSS_SECTION_ORIGIN_MM, CONNECTION_DETAIL_SINGLE_SCALE,
        title=f"CONNECTION DETAIL — {assembly.connection_geometry.connection_id}",
    )

    _draw_multi_member_title_block(c, assembly, metadata, TITLE_BLOCK_ORIGIN_MM)

    c.showPage()
    c.save()
    return buffer.getvalue()


def build_pdf(
    geometry,
    quantity: int,
    status: str | None = None,
    *,
    material: str | None = None,
    revision: str = "A",
    date: _datetime.date | None = None,
) -> bytes:
    """
    Builds the full single-sheet PDF fabrication drawing (border,
    elevation, cross section, one connection detail view per resolved
    connection when present, and a structured title block) for one
    member, on an A3 landscape page.
    Returns the PDF as bytes — writing it to disk (or anywhere else)
    is the caller's job, matching how app/report/pdf_generator.py
    already returns bytes rather than writing files itself.
    """
    resolved_date = (date or _datetime.date.today()).strftime("%d/%m/%Y")
    metadata = DrawingMetadata(
        drawing_number=f"FAB-{geometry.mark}",
        revision=revision,
        date=resolved_date,
        status=status,
        material=material or "NOT SPECIFIED",
    )

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGE_SIZE)
    _draw_border(c)

    outer_points, _inner_wires_points = _extract_end_face_topology(geometry.solid)
    depth_mm = max(y for _x, y in outer_points) - min(y for _x, y in outer_points)

    _draw_elevation(c, geometry, ELEVATION_ORIGIN_MM, depth_mm)

    cs_width_mm, _cs_depth_mm = _draw_cross_section(c, geometry, CROSS_SECTION_ORIGIN_MM)

    # Connection detail views (Milestone 6E) — driven entirely by
    # GeneratedMemberGeometry.connections (GeneratedConnection objects),
    # never by connection_count/connection_types alone and never by
    # rediscovering plate/hole geometry from `geometry.solid`. Ordered by
    # each connection's own `.position` (_CONNECTION_POSITION_ORDER), not
    # by list order, so callers may supply connections in any order.
    connections = list(getattr(geometry, "connections", None) or [])
    if connections:
        detail_x = CROSS_SECTION_ORIGIN_MM[0] + cs_width_mm * CROSS_SECTION_SCALE + VIEW_GAP_MM
        ordered = sorted(connections, key=lambda conn: _CONNECTION_POSITION_ORDER.get(conn.position, 99))
        if len(ordered) == 1:
            _draw_connection_detail(
                c, ordered[0], (detail_x, CROSS_SECTION_ORIGIN_MM[1]), CONNECTION_DETAIL_SINGLE_SCALE
            )
        else:
            slot_ys = (CONNECTION_DETAIL_SLOT_TOP_Y_MM, CONNECTION_DETAIL_SLOT_BOTTOM_Y_MM)
            for connection, slot_y in zip(ordered, slot_ys):
                _draw_connection_detail(c, connection, (detail_x, slot_y), CONNECTION_DETAIL_STACKED_SCALE)

    _draw_title_block(c, geometry, quantity, metadata, TITLE_BLOCK_ORIGIN_MM)

    c.showPage()
    c.save()
    return buffer.getvalue()
