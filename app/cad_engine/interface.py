"""
CAD engine (Slice B). This module defines the CONTRACT the CAD engine
consumes: validated, structured engineering data — never the original
PDF, and never re-interpreted here.

Per the architecture principle this project has held since its first
design review: AI interprets engineering documents; a deterministic
geometry engine (CadQuery, on OpenCascade/OCCT) generates actual
drawings. The CAD engine must never have to re-read a drawing or
re-interpret ambiguous data — by the time it receives a
ValidatedSteelMember, every field has already passed validation.

MILESTONE 1: a single straight member with a matched section and a
known length — the smallest case that produces a real,
dimensionally-accurate solid. See app/cad_engine/sections.py for
which section families are supported (PFC only, so far).

MILESTONE 2: a member may optionally carry one END_PLATE connection —
a flat plate with a fixed 2-hole bolt pattern, joined to the member's
far end. See app/cad_engine/connections.py. Still no welds, no bolts
modelled as solids, no other connection types.

MILESTONE 6A: a member may carry MULTIPLE independent connections —
nothing about the resolve/attach loop below was ever limited to one;
it already iterated member.connection_refs generically. What was
actually missing was a way to tell two END_PLATE connections apart
when both attach to the same member: ValidatedConnection.position
("START" or "END", default "END" for full backward compatibility with
every existing single-connection fixture) — consumed by
connections.attach_end_plate() to decide which end of the member the
plate is built against. _resolve_connections() additionally rejects
two resolved connections claiming the same position, since a member
end can only have one connection.

MILESTONE 6B: a connection may additionally request PHYSICAL bolt
hardware via ValidatedConnection.physical_bolts (a hex-head-and-shaft
spec, optional, None by default — every existing connection that
never sets it is unaffected). This is deliberately NOT folded into
`solid`: `GeneratedMemberGeometry.solid` keeps meaning exactly what it
always has — member + plate + hole voids, the steel fabrication
geometry, one connected solid. Bolts are hardware, not steel; they
were returned on GeneratedMemberGeometry.hardware (a flat list of
bolt solids across every connection on the member) and are never
unioned into `solid`. See app/cad_engine/connections.py's
build_connection_hardware() for how each bolt's position is derived
from the connection's own actual hole geometry, never recomputed
independently.

MILESTONE 6C: with two connections now able to carry independent
hardware (6A + 6B combined), the flat `hardware` list alone can no
longer answer "which connection owns this bolt?" without inferring it
from list order — exactly the kind of implicit, order-dependent
coupling this project avoids everywhere else. This milestone added
GeneratedConnection: one per resolved connection, carrying that
connection's own identity (connection_id, connection_type, position —
all copied straight from the ValidatedConnection that produced it, not
a new identity scheme) next to the hardware list THAT connection
generated. GeneratedMemberGeometry also carries
`connections: list[GeneratedConnection]`. `hardware` (the flat list)
is kept exactly as Milestone 6B left it — same values, same order,
computed as the concatenation of every GeneratedConnection's own
hardware — so nothing from 6B needs to change to keep working.
`_resolve_connections()` additionally rejects duplicate connection_id
values among the *supplied* connections list (previously silently
overwritten by a dict comprehension keyed on connection_id — a latent
ambiguity that milestone closed now that connection_id is a
first-class identity, not just a lookup key).

MILESTONE 6D (current addition): a small, stable GEOMETRY CONTRACT for
each GeneratedConnection, so a future drawing generator can answer
"where is this connection, what steel plate geometry belongs to it,
where are its holes, what hardware belongs to it, what are its
bounds" without touching `solid`'s raw topology or reconstructing
anything from plate.width/height/thickness. `GeneratedConnection.plate`
is the actual positioned CadQuery plate solid
connections.attach_end_plate() built for this connection — the exact
same object unioned into the member's shared `solid`, not a duplicate
rebuilt for inspection. `GeneratedConnection.holes` is a list of
GeneratedHole, each one MEASURED from that plate's own real topology
(diameter and centre read off its actual cut geometry, never copied
from the hole spec) and paired with the bolt that physically passes
through it (via connections.py's build_bolt()/build_connection_hardware(),
matched by hole coordinate, not list position). `geometry_bounds` is a
property computed fresh from `plate` on every access — never a stored,
independently-maintained bounding box that could drift out of sync.
`outward_normal` is a cheap (0,0,±1) Z-direction hint (from
connections.py's existing outward_dir) — not a general transformation
framework; this milestone still only supports the flat END_PLATE case,
where "normal to the plate" and "which member end" are the same fact.
"""
from dataclasses import dataclass, field
from typing import Any

from app.cad_engine import connections as connection_geometry
from app.cad_engine import sections
from app.cad_engine.errors import GeometryValidationError, UnsupportedSectionFamilyError

# Connection types with a supported geometry builder. Only one exists
# yet — a flat end plate with a fixed 2-hole pattern (see
# app/cad_engine/connections.py). Anything else fails clearly rather
# than being silently ignored or guessed at.
SUPPORTED_CONNECTION_TYPES = {"END_PLATE"}

__all__ = [
    "ValidatedSteelMember", "ValidatedConnection", "GeneratedHole", "GeneratedConnection",
    "GeneratedMemberGeometry", "GeometryValidationError", "UnsupportedSectionFamilyError", "generate_geometry",
]


@dataclass
class ValidatedSteelMember:
    mark: str
    section: str
    length_mm: float | None
    material: str
    orientation: dict | None
    connection_refs: list[str]
    source_refs: list[dict]
    validation_status: str
    # The matched steel_sections row (name, family, depth, flange_width,
    # flange_thickness, web_thickness, weight_per_metre, ...) — the actual
    # reference geometry, not just the matched name in `section`. None
    # when the member's section didn't match anything in steel_sections.
    section_properties: dict[str, Any] | None = None


@dataclass
class ValidatedConnection:
    connection_id: str
    connected_members: list[str]
    # For END_PLATE (the only supported type): plates[0] is
    # {"width", "height", "thickness"} and bolts[0] is the bolt-hole
    # pattern spec {"diameter", "quantity", "vertical_spacing"} — the
    # hole geometry a bolt would pass through, not a modelled bolt
    # solid. See app/cad_engine/connections.py.
    plates: list[dict]
    bolts: list[dict]
    welds: list[dict]
    dimensions: dict | None
    source_refs: list[dict]
    validation_status: str
    connection_type: str = ""
    # Which end of the member this connection attaches to: "START"
    # (Z=0) or "END" (Z=length_mm). Defaults to "END" — the only
    # position that existed before Milestone 6A — so every existing
    # single-connection fixture that never set this keeps behaving
    # exactly as before.
    position: str = "END"
    # Optional physical bolt hardware spec: {"shaft_diameter",
    # "shaft_length", "head_across_flats", "head_thickness"}. None (the
    # default) means no physical bolt solids are generated for this
    # connection — its END_PLATE geometry (plate + hole voids) is
    # unaffected either way. See app/cad_engine/connections.py's
    # build_connection_hardware(). Distinct from `bolts` above, which
    # is the hole-VOID pattern cut into the steel plate, not a modelled
    # bolt solid.
    physical_bolts: dict | None = None


@dataclass
class GeneratedHole:
    """
    One hole VOID cut into a connection's plate (Milestone 6D) — a
    small, stable record so a downstream consumer never has to open
    the plate's own topology itself. `center` and `diameter` are
    MEASURED from the plate's actual generated geometry (see
    connections.py's attach_end_plate() /
    _ordered_plate_hole_records()), not copied from
    ValidatedConnection.bolts[0]['diameter'] or recomputed from spacing
    — if the real cut geometry ever disagreed with the spec, this
    record would report the real geometry. `hole_id` is a stable local
    identity within the parent connection only ("hole-1", "hole-2",
    ...) — not a globally unique identifier, which this milestone
    doesn't need. `bolt` is the physical bolt (a cq.Workplane) whose
    axis passes through this exact hole, or None if the connection
    requested no physical_bolts; paired by matching this hole's own
    measured (x, y) against the ordered hole-centre list
    build_connection_hardware() builds bolts from — never by raw list
    position.
    """
    hole_id: str
    center: tuple[float, float, float]
    diameter: float
    bolt: Any = None


@dataclass
class GeneratedConnection:
    """
    One resolved connection's generated result. Beyond identity
    (connection_id, connection_type, position — copied straight from
    the ValidatedConnection that produced it, not a new identity
    scheme) and hardware ownership (Milestone 6C), this connection also
    carries its own geometry contract (Milestone 6D):

      - `plate`: the ACTUAL positioned CadQuery plate solid
        connections.attach_end_plate() built for this connection — the
        exact same object unioned into the member's shared `solid`,
        never a duplicate rebuilt merely for inspection.
      - `holes`: a list of GeneratedHole, each measured from that same
        plate's own real topology.
      - `geometry_bounds`: this connection's own bounding box (its
        plate alone, never the full member), computed fresh from
        `plate` on every access.
      - `outward_normal`: a cheap (0, 0, ±1) direction hint — not a
        general transformation framework.

    This is what lets a downstream consumer (e.g. a future drawing
    generator) answer "what connection is this, where is it, what
    steel geometry belongs to it, where are its holes, what hardware
    belongs to it, what are its bounds" without reverse-engineering
    `solid`'s raw topology or inferring ownership from list order.
    """
    connection_id: str
    connection_type: str
    position: str
    plate: Any = None
    holes: list["GeneratedHole"] = field(default_factory=list)
    hardware: list[Any] = field(default_factory=list)
    outward_normal: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def geometry_bounds(self):
        """
        This connection's own actual bounding box — its plate alone,
        never the full 4000mm member — computed fresh from `plate`
        every time so it can never drift out of sync with the real
        geometry (never a separately stored/maintained record).
        """
        return self.plate.val().BoundingBox()


@dataclass
class GeneratedMemberGeometry:
    """
    What the CAD engine hands back for one member — enough for a
    future drawing_generator to project into 2D views without it
    ever needing to touch ValidatedSteelMember or steel_sections
    directly.
    """
    mark: str
    section_name: str
    section_family: str
    length_mm: float
    solid: Any  # cadquery.Workplane holding the extruded (and possibly connected) solid
    connection_count: int = 0
    # Types of the connections actually built onto `solid` (e.g. ["END_PLATE"]).
    # An identifying label alongside the real geometry, exactly like `section_family`
    # already is — not a redescription of the connection's dimensions, which live only
    # in `solid` itself.
    connection_types: list[str] = field(default_factory=list)
    # Physical bolt hardware (Milestone 6B) — one cadquery.Workplane per
    # bolt, flattened across every connection on this member, in the same
    # order as `connections` below (each GeneratedConnection's own hardware,
    # concatenated). Deliberately NOT part of `solid`: `solid` is the steel
    # fabrication geometry alone (member + plate + hole voids); hardware is
    # never unioned into it. Empty unless at least one resolved connection
    # set `physical_bolts`. Kept for backward compatibility with Milestone
    # 6B consumers that only need "every bolt on this member" and don't
    # care which connection owns which — see `connections` for ownership.
    hardware: list[Any] = field(default_factory=list)
    # One GeneratedConnection per resolved connection (Milestone 6C) —
    # the explicit answer to "which connection owns this hardware?".
    connections: list["GeneratedConnection"] = field(default_factory=list)


def _resolve_connections(
    member: ValidatedSteelMember, connections: list[ValidatedConnection]
) -> list[ValidatedConnection]:
    """
    Matches member.connection_refs against the supplied connections by
    connection_id, in order. Every ref must resolve to a supplied,
    supported-type, supported-position connection — an unresolved ref,
    an unsupported type, or an unsupported/duplicate position fails
    loudly rather than being silently skipped or overwritten. This
    already supports any number of connections (not just one or two):
    nothing here assumes a fixed count, only that every resolved
    connection occupies a distinct, valid position.
    """
    if not member.connection_refs:
        return []

    seen_ids: set[str] = set()
    for c in connections:
        if c.connection_id in seen_ids:
            raise GeometryValidationError(
                f"Member '{member.mark}': multiple supplied connections share connection_id "
                f"'{c.connection_id}'. connection_id must be unique among the connections "
                "passed to generate_geometry() — it is this project's connection identity, "
                "not just a lookup key."
            )
        seen_ids.add(c.connection_id)

    by_id = {c.connection_id: c for c in connections}
    resolved = []
    positions_used: dict[str, str] = {}  # position -> connection_id that claimed it
    for ref in member.connection_refs:
        connection = by_id.get(ref)
        if connection is None:
            raise GeometryValidationError(
                f"Member '{member.mark}' references connection '{ref}', but no matching "
                "ValidatedConnection was supplied to generate_geometry()."
            )
        if connection.connection_type not in SUPPORTED_CONNECTION_TYPES:
            raise GeometryValidationError(
                f"Member '{member.mark}': connection '{ref}' has type "
                f"'{connection.connection_type or '(unset)'}', which has no supported "
                f"geometry builder yet. Supported types: {sorted(SUPPORTED_CONNECTION_TYPES)}."
            )
        if connection.position not in connection_geometry.SUPPORTED_CONNECTION_POSITIONS:
            raise GeometryValidationError(
                f"Member '{member.mark}': connection '{ref}' has position "
                f"'{connection.position or '(unset)'}', which is not supported. Supported "
                f"positions: {sorted(connection_geometry.SUPPORTED_CONNECTION_POSITIONS)}."
            )
        if connection.position in positions_used:
            raise GeometryValidationError(
                f"Member '{member.mark}': connections '{positions_used[connection.position]}' "
                f"and '{ref}' both claim position '{connection.position}' — a member end can "
                "only have one connection."
            )
        positions_used[connection.position] = ref
        resolved.append(connection)
    return resolved


def generate_geometry(
    member: ValidatedSteelMember,
    connections: list[ValidatedConnection] | None = None,
) -> GeneratedMemberGeometry:
    """
    Builds a real 3D solid for a single validated steel member, plus
    any of its connections that resolve to a supported type (only
    END_PLATE, currently — see app/cad_engine/connections.py).

    Every precondition below fails loudly (GeometryValidationError or
    a subclass) rather than guessing — matching this project's
    validation philosophy (see app/validation/rules.py): a missing or
    ambiguous input is a reason to stop, never a reason to invent a
    plausible-looking default.
    """
    if member.validation_status == "review_required":
        raise GeometryValidationError(
            f"Member '{member.mark}' is review_required and cannot generate geometry "
            "until its extraction is resolved (e.g. a conflicting section definition "
            "across pages) — see its validation issue for details."
        )

    resolved_connections = _resolve_connections(member, connections or [])

    if member.length_mm is None:
        raise GeometryValidationError(
            f"Member '{member.mark}' has no length_mm. Fabrication geometry requires an "
            "explicit, extracted length — it is never inferred or defaulted."
        )
    if member.length_mm <= 0:
        raise GeometryValidationError(
            f"Member '{member.mark}' has a non-positive length_mm ({member.length_mm})."
        )

    if not member.section or not member.section_properties:
        raise GeometryValidationError(
            f"Member '{member.mark}' has no matched section reference data. It must be "
            "matched against the steel_sections table before geometry can be generated."
        )

    # THE CAD FAMILY ADMISSION BOUNDARY (Milestone J1). `source_family` is the
    # AUTHORITATIVE catalogue family, carried on the matched steel_sections row
    # and never rewritten. `cad_family` is this engine's own derived vocabulary
    # for building a profile, obtained from the ONE explicit table in
    # app/cad_engine/sections.py (CAD_FAMILY_PROJECTION / cad_family_for()) —
    # never from the section name, never from a builder-availability search,
    # and never from the caller: there is deliberately no cad_family parameter
    # on ValidatedSteelMember for a caller to set. The only thing that admits a
    # family here is its authoritative source value.
    source_family = member.section_properties.get("family")
    cad_family = sections.cad_family_for(source_family)
    if cad_family is None:
        raise UnsupportedSectionFamilyError(
            f"Member '{member.mark}' has section family '{source_family}', which has no "
            f"supported geometry builder yet. Supported families: "
            f"{sorted(sections.PROFILE_BUILDERS)}; source families projected onto them: "
            f"{sorted(sections.CAD_FAMILY_PROJECTION)}."
        )

    profile = sections.PROFILE_BUILDERS[cad_family](member.section_properties, member.mark)
    solid = profile.extrude(member.length_mm)

    hardware: list[Any] = []
    generated_connections: list[GeneratedConnection] = []
    for connection in resolved_connections:
        solid, positioned_plate, hole_records, outward_dir = connection_geometry.attach_end_plate(
            solid, connection, member.length_mm, member.mark
        )
        connection_hardware = connection_geometry.build_connection_hardware(
            connection, member.length_mm, member.mark
        )
        holes = [
            GeneratedHole(
                hole_id=f"hole-{i + 1}",
                center=(x, y, z),
                diameter=diameter,
                bolt=connection_hardware[i] if i < len(connection_hardware) else None,
            )
            for i, (x, y, z, diameter) in enumerate(hole_records)
        ]

        hardware.extend(connection_hardware)
        generated_connections.append(
            GeneratedConnection(
                connection_id=connection.connection_id,
                connection_type=connection.connection_type,
                position=connection.position,
                plate=positioned_plate,
                holes=holes,
                hardware=connection_hardware,
                outward_normal=(0.0, 0.0, outward_dir),
            )
        )

    return GeneratedMemberGeometry(
        mark=member.mark,
        section_name=member.section,
        # The AUTHORITATIVE source family — not the CAD family the profile was
        # built with. A flat bar admitted through the FLAT -> FL projection is
        # still reported as "FLAT", because that is what the catalogue, the
        # drawing and steel_members.section_family all say this member is. The
        # CAD family is an internal construction detail (see sections.py), and
        # reporting it here instead would silently replace the reference
        # vocabulary with the engine's own.
        section_family=source_family,
        length_mm=member.length_mm,
        solid=solid,
        connection_count=len(resolved_connections),
        connection_types=[c.connection_type for c in resolved_connections],
        hardware=hardware,
        connections=generated_connections,
    )
