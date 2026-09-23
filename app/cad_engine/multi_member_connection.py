"""
Milestone 7AV — the smallest explicit MULTI-MEMBER (3+) connection path,
for the real Selby Square 4-member connection (page 5, VIEW B-B:
310UB40 '005' + PL018 + PL032 + PL025, welded and bolted, 2 x 18mm
holes), added BESIDE the proven two-member path — never replacing it,
never weakening it:

    2 connected members  ->  build_reviewed_two_member_connection_assembly() (7O, unchanged)
    3+ connected members ->  build_reviewed_multi_member_connection_assembly() (this module)
    0-1 connected members ->  still refused (nothing is paired or dropped to make it fit)

STEP 3 — WHERE THE TWO-MEMBER ASSUMPTION LIVED, EXACTLY (verified by
reading every module, not assumed): the 7V specification, 7D/7J/7N
adapters and 7A member geometry are already N-member capable
(connected_member_marks is a list; 7N requires the attachment list to
EXACTLY COVER the connected marks, whatever N is). The exactly-two
contract lives in 7AA's scope check (automation_pipeline.py rejects
len(marks) != 2), in 7O/7K/7L/7M/7P/7Q (member_a/member_b pairs), in
the 7T drawing title block (MEMBER A / MEMBER B rows), in 7AG's
expected artifact strings, in 7AR's observed member fields and in
7AS's fixed MEMBER_A/MEMBER_B checklist items. This module adds the
geometry/assembly/validation layer for N members; the pipeline,
drawing, verification, packaging and acceptance boundaries each get
the smallest explicit extension required (see their own modules).

WHAT THIS MODULE REUSES, NEVER DUPLICATES (the project's own rule):
  - connections.build_end_plate_with_holes()/_compute_hole_centers()/
    _ordered_plate_hole_records() — the exact local plate/hole shape
    algorithms (7K reuses them the same way), unchanged.
  - two_member_connection._require_contact() — the 7K/7L touching-
    union-arity + meaningful-overlap contact check, applied to EVERY
    member instead of two.
  - connection_attachment._resolve_single_face() (7M) — the explicit
    END/START geometric-extremum face resolution, per member.
  - connection_geometry_consistency.GEOMETRIC_ALIGNMENT_TOLERANCE_MM +
    _axial_position_mm() (7P) and connection_interface_consistency.
    NORMAL_CONSISTENCY_TOLERANCE + _pair_plate_face() +
    _check_member_normal() (7Q) — the per-member position and
    plane/normal consistency checks, applied to every member in
    member order. Importing those private helpers is deliberate: the
    check semantics are this project's single source of truth, and
    re-implementing them here would create a second, parallel rule.

HARD RULES (the same honesty rules as 7K/7L/7M/7O, generalized):

  - IDENTITY IS SET EQUALITY. {member.mark for member in members}
    must exactly equal set(connection.connected_members) — never
    order-derived, never inferred, never a subset. A 4-member
    connection with 3 supplied members is refused.
  - EVERY MEMBER MUST GENUINELY CONTACT THE PLATE. The touching-union
    arity and meaningful-overlap checks run per member; a member that
    does not touch the positioned plate is an explicit geometric
    inconsistency, never repaired by moving anything.
  - EVERY MEMBER HAS EXACTLY ONE EXPLICIT ATTACHMENT. Attachment
    marks must exactly match the member marks (7N already enforces
    coverage against the connected marks; the checked build here
    re-verifies against the supplied members defensively, like 7M).
  - NO INFERENCE, NO REPOSITIONING, NO PAIRING DOWN. Nothing here
    selects "main" members, drops extras, snaps geometry into contact
    or derives attachments from geometry. Members keep their own 7I
    placements; the plate is positioned from the explicit reviewed
    ConnectionLocation alone.
  - ROTATION SCOPE IS EXPLICIT. Like 7Q, per-member plane/normal
    validation requires every relevant face to stay Z-normal in
    project space: the connection location and every member placement
    must have rotation_x == rotation_y == 0. Anything else is
    REJECTED, never silently mis-measured.
  - VALIDATION IS PER MEMBER. validate_multi_member_connection_
    assembly() runs, for every member: the 7P position check (the
    resolved attachment face's axial position within the plate's own
    Z-extent, tolerance GEOMETRIC_ALIGNMENT_TOLERANCE_MM) and the 7Q
    normal check (the resolved face's normal must oppose the specific
    plate interface face nearest the member's own solid, paired by
    the member solid's bounding-box midpoint — the exact 7Q rule).

SCOPE: exactly one controlled connection shape (END_PLATE, matching
SUPPORTED_CONNECTION_TYPES), a single connection plate (the real
B-B detail's bolted plate — its real member's dims, supplied by the
reviewer via the existing PROVIDE_PLATE task), and physical bolt
hardware is NOT generated (same documented 7K limitation). This is
NOT a general topology/BIM/scene-graph engine (brief section 21).
"""
from dataclasses import dataclass, field
from typing import Any

import cadquery as cq

from app.cad_engine import connections as connection_geometry
from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_attachment import (
    ConnectionAttachment,
    _require_valid_attachment,
    _resolve_single_face,
)
from app.cad_engine.connection_geometry_consistency import (
    GEOMETRIC_ALIGNMENT_TOLERANCE_MM,
    _axial_position_mm,
)
from app.cad_engine.connection_interface_consistency import _check_member_normal
from app.cad_engine.connection_location import ConnectionLocation
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import SUPPORTED_CONNECTION_TYPES, GeneratedHole, ValidatedConnection
from app.cad_engine.two_member_connection import _require_contact

__all__ = [
    "MULTI_MEMBER_MIN_COUNT", "MEMBER_LABEL_LETTERS",
    "MultiMemberConnectionAssembly", "build_multi_member_connection_assembly",
    "MultiMemberResolvedSurfaces", "ReviewedMultiMemberConnectionAssembly",
    "build_reviewed_multi_member_connection_assembly",
    "MultiMemberValidationResult", "validate_multi_member_connection_assembly",
    "generate_fabrication_drawing_from_reviewed_multi_member_assembly",
]

# The explicit boundary: this path exists for 3-or-more-member
# connections; the two-member path is handled by 7O (unchanged).
MULTI_MEMBER_MIN_COUNT = 3

# The member label letters the drawing title block and the acceptance
# checklist use ("MEMBER A", "MEMBER B", "MEMBER C", ...) — the
# existing two-member vocabulary extended letter by letter, never a
# new parallel naming scheme.
MEMBER_LABEL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def member_label(index: int) -> str:
    """'MEMBER A'..'MEMBER Z' for a member's 0-based drawing order."""
    return f"MEMBER {MEMBER_LABEL_LETTERS[index]}"


@dataclass
class MultiMemberConnectionAssembly:
    """
    One reviewed connection's geometry, positioned in project space
    and associated with EVERY PlacedMember it connects — the N-member
    counterpart of GeneratedConnectionAssembly (7K/7L), a plain
    container (no scene graph, no automatic union/fuse/move/align/
    snap behaviour).
    """
    connection_id: str
    members: tuple[PlacedMember, ...]
    location: ConnectionLocation
    plate: Any  # cq.Workplane, positioned in project space via `location`
    holes: list[GeneratedHole] = field(default_factory=list)
    hardware: list[Any] = field(default_factory=list)  # always [] — same documented 7K limitation


def build_multi_member_connection_assembly(
    connection: ValidatedConnection,
    location: ConnectionLocation,
    members: tuple[PlacedMember, ...] | list[PlacedMember],
) -> MultiMemberConnectionAssembly:
    """
    Builds one END_PLATE connection's geometry in project space from
    an already-validated ValidatedConnection, an already-validated
    ConnectionLocation, and every already-placed member the connection
    names — the N-member generalization of 7K/7L, with the identical
    rules: the local plate shape is built by connections.py's own
    build_end_plate_with_holes() (unchanged), positioned purely from
    `location`, and every member must genuinely contact it
    (_require_contact, the 7K/7L check, applied per member).

    Raises GeometryValidationError when:
      - fewer than MULTI_MEMBER_MIN_COUNT members are supplied (the
        two-member path is 7O's, never this module's);
      - connection.connection_type is not supported (END_PLATE only);
      - the supplied members' marks do not EXACTLY match
        connection.connected_members (order-independent set
        comparison — a dropped or substituted member is refused);
      - the connection has no plate or hole data;
      - any member fails the touching-union arity or meaningful-
        overlap contact check against the positioned plate.

    Never mutates the members' solids, `connection` or `location`.
    """
    if not isinstance(members, (tuple, list)):
        raise TypeError(
            f"members must be a sequence of PlacedMember (got {type(members).__name__})."
        )
    members = tuple(members)
    if len(members) < MULTI_MEMBER_MIN_COUNT:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the multi-member path requires at least "
            f"{MULTI_MEMBER_MIN_COUNT} members (got {len(members)}); the two-member path is "
            "7O's own builder, never this one."
        )
    if connection.connection_type not in SUPPORTED_CONNECTION_TYPES:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': connection type "
            f"{connection.connection_type!r} has no supported multi-member geometry builder yet. "
            f"Supported types: {sorted(SUPPORTED_CONNECTION_TYPES)}."
        )

    expected_marks = set(connection.connected_members)
    actual_marks = {member.mark for member in members}
    if actual_marks != expected_marks:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the supplied members "
            f"{sorted(actual_marks)} do not match this connection's explicit connected_members "
            f"{sorted(expected_marks)}. Every member the reviewed connection names must be "
            "supplied, and nothing else — members are never inferred, dropped or substituted."
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

    # The pure LOCAL plate shape — the exact same algorithm the single-
    # member and two-member builders use, never duplicated.
    plate_solid = connection_geometry.build_end_plate_with_holes(
        plate_spec, holes_spec, connection.connection_id,
    )

    # Positioned into PROJECT space purely from the explicit, reviewed
    # ConnectionLocation — never from any member's MemberPlacement.
    project_location = cq.Location(
        location.x, location.y, location.z,
        location.rotation_x, location.rotation_y, location.rotation_z,
    )
    positioned_plate = cq.Workplane(obj=plate_solid.moved(project_location))

    for member in members:
        _require_contact(member, positioned_plate, connection.connection_id)

    canonical_xy_order = connection_geometry._compute_hole_centers(
        float(plate_spec["width"]), float(plate_spec["height"]), holes_spec, connection.connection_id,
    )
    hole_records = connection_geometry._ordered_plate_hole_records(positioned_plate, canonical_xy_order)
    holes = [
        GeneratedHole(hole_id=f"hole-{i + 1}", center=(x, y, z), diameter=diameter)
        for i, (x, y, z, diameter) in enumerate(hole_records)
    ]

    return MultiMemberConnectionAssembly(
        connection_id=connection.connection_id,
        members=members,
        location=location,
        plate=positioned_plate,
        holes=holes,
        hardware=[],
    )


@dataclass(frozen=True)
class MultiMemberResolvedSurfaces:
    """
    The result of resolving one explicit ConnectionAttachment per
    member against actual project-space member geometry — the N-member
    counterpart of 7M's ResolvedAttachmentSurfaces. `faces_by_mark`
    maps member mark -> the actual CadQuery face, freshly resolved at
    call time, never stored for reuse.
    """
    attachments: tuple[ConnectionAttachment, ...]
    faces_by_mark: dict[str, Any]


@dataclass
class ReviewedMultiMemberConnectionAssembly:
    """
    One explicit, project-space, fully reviewed multi-member
    connection — the N-member counterpart of
    ReviewedTwoMemberConnectionAssembly (7O), keeping every
    relationship separately identifiable:
      - members: every PlacedMember (project-space geometry + the
        MemberPlacement that produced it — from 7I), in the
        connection's own member order.
      - connection: the ValidatedConnection, unchanged.
      - location: the ConnectionLocation, unchanged.
      - attachments: the explicit, reviewed ConnectionAttachment
        references (one per member — from 7M/7N), unchanged.
      - resolved_surfaces: the actual project-space faces those
        attachments resolve to (7M's resolver, per member).
      - connection_geometry: the generated connection plate/hole
        geometry, independently positioned via `location`.
    """
    members: tuple[PlacedMember, ...]
    connection: ValidatedConnection
    location: ConnectionLocation
    attachments: tuple[ConnectionAttachment, ...]
    resolved_surfaces: MultiMemberResolvedSurfaces
    connection_geometry: MultiMemberConnectionAssembly


def build_reviewed_multi_member_connection_assembly(
    members: tuple[PlacedMember, ...] | list[PlacedMember],
    connection: ValidatedConnection,
    location: ConnectionLocation,
    attachments: tuple[ConnectionAttachment, ...] | list[ConnectionAttachment],
) -> ReviewedMultiMemberConnectionAssembly:
    """
    Composes every already-validated member, the connection, the
    location and one explicit attachment per member into one
    ReviewedMultiMemberConnectionAssembly — the N-member
    generalization of 7O, reusing 7M's own face resolver
    (_resolve_single_face, per member) and this module's geometry
    builder unchanged.

    Raises GeometryValidationError when:
      - fewer than MULTI_MEMBER_MIN_COUNT members;
      - two supplied members carry the same mark;
      - the members' marks do not exactly match
        connection.connected_members (7K/7M's identity rule);
      - any attachment is missing/blank/unsupported (7M's own
        validity rule, applied per attachment), duplicated, or does
        not exactly cover the members' marks (7M's rule, generalized);
      - the geometry builder rejects the inputs (unsupported type,
        missing plate/hole data, per-member contact failure).

    Never mutates any input; faces are resolved fresh, never cached.
    """
    members = tuple(members)
    if len(members) < MULTI_MEMBER_MIN_COUNT:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the reviewed multi-member assembly "
            f"requires at least {MULTI_MEMBER_MIN_COUNT} members (got {len(members)})."
        )

    member_marks = [member.mark for member in members]
    if len(set(member_marks)) != len(member_marks):
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the supplied members carry duplicate "
            f"marks {sorted(member_marks)} — every member must be distinct."
        )
    members_by_mark = {member.mark: member for member in members}

    expected_marks = set(connection.connected_members)
    if set(members_by_mark) != expected_marks:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the supplied members "
            f"{sorted(members_by_mark)} do not match this connection's explicit "
            f"connected_members {sorted(expected_marks)}."
        )

    attachments = tuple(attachments)
    for attachment in attachments:
        _require_valid_attachment(attachment)  # 7M's own validity rule, per attachment

    attachment_marks = [attachment.member_mark for attachment in attachments]
    if len(set(attachment_marks)) != len(attachment_marks):
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': multiple attachments reference the same "
            "member — a duplicate attachment. Each connected member must have exactly one."
        )
    if set(attachment_marks) != expected_marks:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': attachment member marks "
            f"{sorted(attachment_marks)} do not match this connection's explicit "
            f"connected_members {sorted(expected_marks)}."
        )

    faces_by_mark = {
        attachment.member_mark: _resolve_single_face(
            members_by_mark[attachment.member_mark], attachment,
        )
        for attachment in attachments
    }
    resolved_surfaces = MultiMemberResolvedSurfaces(
        attachments=attachments, faces_by_mark=faces_by_mark,
    )
    connection_geometry = build_multi_member_connection_assembly(
        connection, location, members,
    )

    return ReviewedMultiMemberConnectionAssembly(
        members=members,
        connection=connection,
        location=location,
        attachments=attachments,
        resolved_surfaces=resolved_surfaces,
        connection_geometry=connection_geometry,
    )


@dataclass(frozen=True)
class MultiMemberValidationResult:
    """
    Confirms only that geometric consistency of the explicitly
    reviewed multi-member connection assembly has been validated
    against the per-member application of the existing 7P/7Q checks —
    see the module docstring for the precise boundary. NOT structural
    adequacy, code compliance or fabrication readiness.
    """
    connection_id: str
    layers_validated: tuple[str, ...]
    member_face_positions_mm: tuple[tuple[str, float], ...]


# The geometric consistency layers this validator confirms, in the
# actual order they are satisfied — the same layer semantics as 7R's
# VALIDATED_LAYERS, applied to every member instead of a pair.
MULTI_VALIDATED_LAYERS = (
    "7K/7L member-plate contact, per member (already satisfied at assembly construction time)",
    "7P attachment-face position consistency, per member",
    "7Q project-space interface plane/normal consistency, per member",
)


def validate_multi_member_connection_assembly(
    assembly: ReviewedMultiMemberConnectionAssembly,
) -> MultiMemberValidationResult:
    """
    Runs the existing geometric consistency checks against EVERY
    member of `assembly`, in member order — the per-member
    generalization of 7R's composed check:

      1. Rotation scope (7Q's rule, per object): the connection
         location and every member placement must have zero
         rotation_x/rotation_y — anything else is REJECTED, never
         silently mis-measured.
      2. Position (7P's rule, per member): each resolved attachment
         face's axial position must lie within the generated plate's
         own Z-extent (GEOMETRIC_ALIGNMENT_TOLERANCE_MM).
      3. Normal (7Q's rule, per member): each resolved face's normal
         must oppose the normal of the specific plate interface face
         nearest that member's own solid (paired by the member solid's
         bounding-box midpoint — the exact 7Q pairing rule, including
         its tie-break to the lower face).

    Reuses the exact helper functions and constants those layers own
    (imported above) — never re-implemented. Raises
    GeometryValidationError, propagated unchanged, on any
    inconsistency; never repairs, infers, repositions or mutates
    anything.
    """
    connection_id = assembly.connection.connection_id

    location = assembly.location
    if location.rotation_x != 0.0 or location.rotation_y != 0.0:
        raise GeometryValidationError(
            f"Connection '{connection_id}': project-space interface plane/normal validation is "
            f"not supported for a ConnectionLocation with nonzero rotation_x/rotation_y (got "
            f"rotation_x={location.rotation_x}, rotation_y={location.rotation_y}). This is a "
            "validated scope limitation, not an unverified generalization."
        )
    for member in assembly.members:
        placement = member.placement
        if placement.rotation_x != 0.0 or placement.rotation_y != 0.0:
            raise GeometryValidationError(
                f"Connection '{connection_id}': project-space interface plane/normal validation "
                f"is not supported for member '{member.mark}' with nonzero rotation_x/rotation_y "
                f"(got rotation_x={placement.rotation_x}, rotation_y={placement.rotation_y})."
            )

    plate = assembly.connection_geometry.plate
    plate_bbox = plate.val().BoundingBox()
    lo = plate_bbox.zmin - GEOMETRIC_ALIGNMENT_TOLERANCE_MM
    hi = plate_bbox.zmax + GEOMETRIC_ALIGNMENT_TOLERANCE_MM

    face_by_mark = assembly.resolved_surfaces.faces_by_mark
    attachment_by_mark = {
        attachment.member_mark: attachment for attachment in assembly.attachments
    }

    # 7P's position check, per member (before any normal check — 7Q's own order).
    positions: dict[str, float] = {}
    for member in assembly.members:
        face = face_by_mark[member.mark]
        attachment = attachment_by_mark[member.mark]
        position = _axial_position_mm(face)
        positions[member.mark] = position
        if not (lo <= position <= hi):
            raise GeometryValidationError(
                f"Connection '{connection_id}': the reviewed attachment "
                f"'{attachment.surface_reference}' for member '{member.mark}' resolves to a face "
                f"at Z={position:.3f}mm, which does not lie within the generated connection "
                f"plate's own Z-extent [{plate_bbox.zmin:.3f}, {plate_bbox.zmax:.3f}]mm "
                f"(tolerance {GEOMETRIC_ALIGNMENT_TOLERANCE_MM}mm). The reviewed attachment "
                "surface does not correspond to where the generated connection geometry "
                "actually sits. Reported as an explicit geometric inconsistency — no "
                "attachment, member, or connection is repositioned to force agreement."
            )

    plate_lo_faces = plate.faces("<Z").vals()
    plate_hi_faces = plate.faces(">Z").vals()
    if len(plate_lo_faces) != 1 or len(plate_hi_faces) != 1:
        raise GeometryValidationError(  # defensive; unreachable for the currently-supported plate geometry
            f"Connection '{connection_id}': expected exactly one plate face on each of <Z/>Z, "
            f"found {len(plate_lo_faces)}/{len(plate_hi_faces)}."
        )
    plate_lo_face, plate_hi_face = plate_lo_faces[0], plate_hi_faces[0]

    # 7Q's normal check, per member (the exact 7Q helper, unchanged).
    for member in assembly.members:
        _check_member_normal(
            member,
            face_by_mark[member.mark],
            attachment_by_mark[member.mark].surface_reference,
            plate_lo_face, plate_hi_face,
            connection_id,
        )

    return MultiMemberValidationResult(
        connection_id=connection_id,
        layers_validated=MULTI_VALIDATED_LAYERS,
        member_face_positions_mm=tuple(
            (member.mark, positions[member.mark]) for member in assembly.members
        ),
    )


def generate_fabrication_drawing_from_reviewed_multi_member_assembly(
    assembly: ReviewedMultiMemberConnectionAssembly,
    output_path,
    **pdf_kwargs,
):
    """
    The multi-member counterpart of the 7S/7T production boundary:
    validates `assembly` through validate_multi_member_connection_
    assembly() and only on success renders the complete reviewed
    connection — EVERY member's own project-space geometry and the
    actual generated connection plate/hole geometry — via the new
    multi-member PDF entry (generate_multi_member_connection_
    fabrication_drawing_pdf, which reuses the existing drawing
    primitives). Raises GeometryValidationError, propagated unchanged,
    on any inconsistency — the drawing generator is never called in
    that case, no output file is written, and there is no fallback to
    a partial or two-member-only drawing.

    `**pdf_kwargs` forwards unchanged (status, material, revision,
    date, project_id, source_drawing_id) — no new drawing content and
    no approval/compliance labelling of its own.
    """
    from app.drawing_generator.interface import (
        generate_multi_member_connection_fabrication_drawing_pdf,
    )

    validate_multi_member_connection_assembly(assembly)

    return generate_multi_member_connection_fabrication_drawing_pdf(
        assembly, output_path, **pdf_kwargs,
    )
