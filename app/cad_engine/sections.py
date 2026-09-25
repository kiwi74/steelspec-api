"""
Section-family profile builders for the CAD engine.

Each builder takes the matched steel_sections row exactly as returned
by SectionMatcher (real columns from Supabase, never a hardcoded
standard-section table) and returns a closed 2D CadQuery profile in
the XY plane, ready for the caller to extrude along the member's
length. One builder per section family; interface.py dispatches to
the right one via PROFILE_BUILDERS and rejects anything unsupported.

PFC and UB both need the same four dimensional fields (depth,
flange_width, flange_thickness, web_thickness); SHS needs (width,
thickness) and RHS needs (width, depth, thickness) — matching
steel_sections' actual columns for hollow sections, not
`flange_width`/etc. (those are the open-section columns); PL and FL
(flat plate) need (width, thickness), the columns real shop drawings
carry for flat-plate members, and share the one flat-plate builder;
EA (equal angle) needs (width, thickness) — the angle's leg dimension
and thickness — and builds a genuine two-leg L-section, never a
flat-plate approximation. The field-presence check is shared via
_require_fields(); the actual profile shape (and each family's own
geometric sanity check) is not, and stays in each builder.

SOURCE FAMILY vs CAD FAMILY (Milestone J1): the catalogue's family
vocabulary and this module's are not the same one. `steel_sections.family`
is authoritative and stores a flat bar as "FLAT"; PROFILE_BUILDERS above
is the CAD vocabulary and spells it "FL". CAD_FAMILY_PROJECTION — one
entry only, "FLAT" -> "FL" — plus cad_family_for() is the single,
explicit, fail-closed boundary between them. The source family is never renamed:
see the block above CAD_FAMILY_PROJECTION.

HOLLOW SECTIONS (SHS, RHS): a genuine hollow profile — not a solid
bar — is built from two nested closed wires (outer boundary, inner
boundary) on one Workplane. CadQuery/OCCT's extrude() automatically
builds a face-with-a-hole from a contained inner wire and produces one
connected hollow solid — verified directly (bounding box, solid
count, and volume all matched the expected values exactly, for both
the square SHS case and the unequal-dimension RHS case) before
relying on it here. No boolean cut is needed for this shape, unlike
app/cad_engine/connections.py's bolt holes, which do need one because
they're voids through an otherwise-solid plate, not the profile's own
defining boundary. build_shs_profile() and build_rhs_profile() are
both thin wrappers over the one shared
build_hollow_rectangular_profile() — RHS is not a second, parallel
hollow-section implementation.
"""
from types import MappingProxyType
from typing import Any, Mapping

import cadquery as cq

from app.cad_engine.errors import GeometryValidationError

PROFILE_REQUIRED_FIELDS = ("depth", "flange_width", "flange_thickness", "web_thickness")
HOLLOW_RECT_REQUIRED_FIELDS = ("width", "thickness")
PLATE_REQUIRED_FIELDS = ("width", "thickness")
EA_REQUIRED_FIELDS = ("width", "thickness")


def _require_fields(section_properties: dict, mark: str, family_label: str,
                     fields: tuple[str, ...]) -> tuple[float, ...]:
    """
    Checks that every field in `fields` is present on the matched
    section row, and returns them as floats in the same order.
    Shared by every family builder below — each family supplies its
    own field list; this only enforces presence, not what counts as
    geometrically consistent, which stays in each builder.
    """
    name = section_properties.get("name")
    missing = [f for f in fields if section_properties.get(f) is None]
    if missing:
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' is missing required {family_label} "
            f"geometry field(s) {missing} in steel_sections. Refusing to guess "
            "standard-table values — the reference data must be corrected instead."
        )
    return tuple(float(section_properties[f]) for f in fields)


def build_pfc_profile(section_properties: dict, mark: str) -> cq.Workplane:
    """
    Builds a parallel-flange-channel (PFC) cross-section from the
    section's actual depth / flange_width / flange_thickness /
    web_thickness — never generic/standard-table values.

    Simplification (deliberate, not an oversight): steel_sections has
    no root-radius column for this family, and this project does not
    invent missing dimensions, so the internal web/flange corners are
    drawn sharp rather than filleted. A future milestone can add
    fillets once the reference data actually carries a radius.
    """
    name = section_properties.get("name")
    depth, flange_width, flange_thickness, web_thickness = _require_fields(
        section_properties, mark, "PFC", PROFILE_REQUIRED_FIELDS
    )

    if not (0 < web_thickness < flange_width and 0 < flange_thickness < depth / 2):
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' has geometrically inconsistent "
            f"PFC dimensions (depth={depth}, flange_width={flange_width}, "
            f"flange_thickness={flange_thickness}, web_thickness={web_thickness})."
        )

    # C-shaped channel, web on the x=0 edge, opening faces +x, going
    # counter-clockwise from the bottom-outer corner of the web.
    profile_points = [
        (0, 0),
        (flange_width, 0),
        (flange_width, flange_thickness),
        (web_thickness, flange_thickness),
        (web_thickness, depth - flange_thickness),
        (flange_width, depth - flange_thickness),
        (flange_width, depth),
        (0, depth),
    ]
    return cq.Workplane("XY").polyline(profile_points).close()


def build_ub_profile(section_properties: dict, mark: str) -> cq.Workplane:
    """
    Builds a Universal Beam (I/H) cross-section from the section's
    actual depth / flange_width / flange_thickness / web_thickness —
    a true I-shape (top flange, web, bottom flange), not a
    rectangular stand-in.

    Simplification (deliberate, not an oversight): steel_sections has
    no root-radius column, so — same as the PFC builder — the internal
    corners where the web meets each flange are drawn sharp rather
    than filleted, and the web is centred exactly on the flange width.
    """
    name = section_properties.get("name")
    depth, flange_width, flange_thickness, web_thickness = _require_fields(
        section_properties, mark, "UB", PROFILE_REQUIRED_FIELDS
    )

    if not (0 < web_thickness < flange_width and 0 < flange_thickness < depth / 2):
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' has geometrically inconsistent "
            f"UB dimensions (depth={depth}, flange_width={flange_width}, "
            f"flange_thickness={flange_thickness}, web_thickness={web_thickness})."
        )

    web_left = flange_width / 2 - web_thickness / 2
    web_right = flange_width / 2 + web_thickness / 2

    # I-shaped profile, footprint [0, flange_width] x [0, depth] — same
    # local-frame convention as the PFC builder — going clockwise from
    # the bottom-left corner of the bottom flange, up one side of the
    # web, across the top flange, and back down the other side.
    profile_points = [
        (0, 0),
        (flange_width, 0),
        (flange_width, flange_thickness),
        (web_right, flange_thickness),
        (web_right, depth - flange_thickness),
        (flange_width, depth - flange_thickness),
        (flange_width, depth),
        (0, depth),
        (0, depth - flange_thickness),
        (web_left, depth - flange_thickness),
        (web_left, flange_thickness),
        (0, flange_thickness),
    ]
    return cq.Workplane("XY").polyline(profile_points).close()


def build_plate_profile(section_properties: dict, mark: str) -> cq.Workplane:
    """
    Builds a flat-plate (PL/FL) cross-section from the section's
    actual `width` and `thickness` columns: a solid rectangle,
    footprint [0, width] x [0, thickness] — same local-frame
    convention as the PFC/UB builders. Real shop drawings list flat
    plates as members in their material lists (e.g. 250X12FL); the
    engine must build their real geometry rather than reject them.
    """
    name = section_properties.get("name")
    family = section_properties.get("family") or "PL"
    width, thickness = _require_fields(section_properties, mark, family, PLATE_REQUIRED_FIELDS)
    if not (0 < width and 0 < thickness):
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' has geometrically inconsistent "
            f"plate dimensions (width={width}, thickness={thickness})."
        )
    return cq.Workplane("XY").polyline([(0, 0), (width, 0), (width, thickness), (0, thickness)]).close()


def build_ea_profile(section_properties: dict, mark: str) -> cq.Workplane:
    """
    Builds a genuine equal-angle (EA) cross-section from the section's
    actual `width` (the leg dimension) and `thickness` columns: an
    L-shape made of two perpendicular legs, each `width` long and
    `thickness` thick — footprint [0, width] x [0, width] minus the
    unoccupied square, same local-frame convention as the other
    builders. Real shop drawings list equal angles as members in their
    material lists (e.g. 90X10EA); the engine must build their real
    two-leg geometry rather than reject them or approximate them as a
    flat plate. An equal angle must have equal legs, so a matched
    `depth` that contradicts `width` is refused, never averaged.
    """
    name = section_properties.get("name")
    width, thickness = _require_fields(section_properties, mark, "EA", EA_REQUIRED_FIELDS)
    if not (0 < thickness < width):
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' has geometrically inconsistent "
            f"equal-angle dimensions (width={width}, thickness={thickness})."
        )
    depth = section_properties.get("depth")
    if depth is not None and depth != width:
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' has unequal legs "
            f"(width={width}, depth={depth}); an equal angle must have equal legs."
        )
    return cq.Workplane("XY").polyline(
        [(0, 0), (width, 0), (width, thickness),
         (thickness, thickness), (thickness, width), (0, width)]).close()


def build_hollow_rectangular_profile(width_mm: float, depth_mm: float, wall_thickness_mm: float,
                                      mark: str, name: str | None = None) -> cq.Workplane:
    """
    Builds a genuine hollow rectangular profile: an outer boundary and
    an inner boundary (the void), footprint [0, width_mm] x
    [0, depth_mm] — same local-frame convention as the PFC/UB
    builders. Not family-specific by itself — build_shs_profile()
    calls this with width == depth; build_rhs_profile() calls it with
    independent width/depth, unchanged.
    """
    if not (0 < wall_thickness_mm < min(width_mm, depth_mm) / 2):
        raise GeometryValidationError(
            f"Member '{mark}': matched section '{name}' has geometrically inconsistent "
            f"hollow-section dimensions (width={width_mm}, depth={depth_mm}, "
            f"wall_thickness={wall_thickness_mm}) — wall thickness must be less than "
            "half the smaller outer dimension."
        )

    outer_points = [(0, 0), (width_mm, 0), (width_mm, depth_mm), (0, depth_mm)]
    inner_points = [
        (wall_thickness_mm, wall_thickness_mm),
        (width_mm - wall_thickness_mm, wall_thickness_mm),
        (width_mm - wall_thickness_mm, depth_mm - wall_thickness_mm),
        (wall_thickness_mm, depth_mm - wall_thickness_mm),
    ]
    return (
        cq.Workplane("XY")
        .polyline(outer_points).close()
        .polyline(inner_points).close()
    )


def build_shs_profile(section_properties: dict, mark: str) -> cq.Workplane:
    """
    Builds a Square Hollow Section from the section's actual `width`
    and `thickness` columns. SHS is square by definition, so the
    profile's depth equals its width — steel_sections has a single
    `width` column for hollow sections (no separate `depth` column
    the way UB/PFC have), which is itself consistent with that: there
    is nothing to disagree with width for a genuinely square section.
    """
    name = section_properties.get("name")
    width, thickness = _require_fields(section_properties, mark, "SHS", HOLLOW_RECT_REQUIRED_FIELDS)
    return build_hollow_rectangular_profile(width, width, thickness, mark, name=name)


RHS_REQUIRED_FIELDS = ("width", "depth", "thickness")


def build_rhs_profile(section_properties: dict, mark: str) -> cq.Workplane:
    """
    Builds a Rectangular Hollow Section from the section's actual
    `width` / `depth` / `thickness` columns. Unlike SHS, RHS genuinely
    needs two independent outer dimensions — reuses the `depth`
    column PFC/UB already populate for their own depth (nothing about
    that column is family-specific) alongside SHS's `width` column,
    rather than requiring a new schema column just for this family.

    This is a thin wrapper only: all actual geometry construction
    (and the wall-thickness/aspect-ratio validation) stays in
    build_hollow_rectangular_profile(), unchanged from what SHS
    already uses — RHS is not a second, parallel hollow-section
    implementation.
    """
    name = section_properties.get("name")
    width, depth, thickness = _require_fields(section_properties, mark, "RHS", RHS_REQUIRED_FIELDS)
    return build_hollow_rectangular_profile(width, depth, thickness, mark, name=name)


PROFILE_BUILDERS = {
    "PFC": build_pfc_profile,
    "UB": build_ub_profile,
    "SHS": build_shs_profile,
    "RHS": build_rhs_profile,
    "PL": build_plate_profile,
    "FL": build_plate_profile,
    "EA": build_ea_profile,
}

# ---------------------------------------------------------------------------
# SOURCE FAMILY -> CAD FAMILY ADMISSION (Milestone J1)
#
# The section CATALOGUE and the CAD ENGINE are two different vocabularies.
# `steel_sections.family` is the AUTHORITATIVE source family: a PostgreSQL
# ENUM (public.section_family) whose flat-bar value is "FLAT" — the value
# every live plate row actually carries, and the only one the database
# permits for it ("FL" and "PL" are rejected by the enum; see
# tests/test_real_world_live_schema_establishment.py). This module's
# PROFILE_BUILDERS above is the CAD vocabulary, which spells that same
# family "FL".
#
# The single explicit table below is the whole of the mapping between them.
#
# It holds exactly ONE entry, and that narrowness is the point. This is
# NOT a "closest supported family" fallback, NOT a section-name or
# name-suffix rule, NOT a geometry-similarity guess, and NOT a
# builder-availability search — activating it requires one thing only:
# the authoritative source family value. Adding a second entry, or mapping
# "PL" (which shares the plate builder today) or any other family, is a
# separate accepted milestone with its own evidence, never an edit made in
# passing.
#
# The source family is never renamed by this table. Nothing here mutates a
# catalogue row, and an admitted member keeps reporting the authoritative
# source family it came in with — see cad_family_for() below and
# interface.generate_geometry()'s section_family.
# ---------------------------------------------------------------------------
CAD_FAMILY_PROJECTION: Mapping[str, str] = MappingProxyType({"FLAT": "FL"})


def cad_family_for(source_family: Any) -> str | None:
    """
    The CAD geometry family an AUTHORITATIVE source family is admissible
    as, or None when this engine has no geometry builder for it.

      - a source family named in CAD_FAMILY_PROJECTION ("FLAT") is
        admitted as its one explicit CAD entry ("FL");
      - a source family this engine already supports is its own CAD
        family — plain identity, exactly what PROFILE_BUILDERS[family]
        already did for every currently-supported family;
      - everything else returns None, i.e. refuses: an unsupported
        family, an unknown family, a missing/None family, a non-string
        value. Nothing is guessed, no similar family is substituted, and
        the section name is never consulted.

    Total and fail-closed by construction. It returns a decision rather
    than raising so each caller keeps its own refusal vocabulary and
    error type (interface.generate_geometry() raises
    UnsupportedSectionFamilyError, real_member_adapter raises
    GeometryValidationError) — the admission decision itself cannot
    differ between them, because it is only ever made here.
    """
    if not isinstance(source_family, str):
        return None
    if source_family in CAD_FAMILY_PROJECTION:
        return CAD_FAMILY_PROJECTION[source_family]
    if source_family in PROFILE_BUILDERS:
        return source_family
    return None
