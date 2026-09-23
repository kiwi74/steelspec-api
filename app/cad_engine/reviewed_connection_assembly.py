"""
Milestone 7O — the narrowest possible orchestration layer combining
every existing reviewed/validated contract into one explicit,
project-space, two-member reviewed connection assembly:

    PlacedMember A + PlacedMember B
        + ValidatedConnection + ConnectionLocation
        + ConnectionAttachment A + ConnectionAttachment B
            |
       build_reviewed_two_member_connection_assembly()
            |
      ReviewedTwoMemberConnectionAssembly

STEP 1/2 FINDING: every one of the six inputs above already has
exactly one authoritative source, established across 7A-7N:

    real member row       -> real_member_adapter.py           -> ValidatedSteelMember -> GeneratedMemberGeometry
    MemberPlacement        -> placement.py                     -> place_member_geometry() -> project-space GeneratedMemberGeometry
    reviewed connection    -> real_connection_adapter.py       -> ValidatedConnection
    reviewed location      -> connection_location.py           -> ConnectionLocation
    reviewed attachment    -> reviewed_connection_attachment.py -> ConnectionAttachment[]
    surface resolution     -> connection_attachment.py (7M)    -> resolve_connection_attachment_surfaces()
    connection geometry    -> two_member_connection.py (7K/7L) -> build_two_member_connection_assembly()

This module performs NO validation of its own beyond what those
existing pieces already do (Step 6: reuse, never duplicate). It calls
exactly two existing, UNMODIFIED functions:

  - resolve_connection_attachment_surfaces() (7M) — already validates
    member identity ({member_a.mark, member_b.mark} ==
    set(connection.connected_members)), attachment completeness/
    duplication, and resolves the explicit reviewed faces against
    actual project-space geometry.
  - build_two_member_connection_assembly() (7K/7L) — already validates
    member identity (the same check, independently), connection type,
    plate/hole data, and genuine geometric contact (touching-union
    arity + meaningful-overlap, from 7L's strengthened check).

Both independently check member identity against
connection.connected_members; this module does not re-check it a
third time — it only composes their two return values, plus the
already-validated identity/location objects the caller supplies, into
one container. If either existing call rejects its input, that
GeometryValidationError propagates unchanged; this module adds no new
rejection rule.

WHAT THIS MODULE DOES NOT DO (Step 11): no AI inference, no automatic
face/position selection, no nearest-face substitution, no primary/
secondary classification, no member or connection repositioning, no
structural/engineering calculation, no fabrication-readiness claim.
It is pure composition of already-existing, already-validated pieces
— an orchestration layer, not a new capability.
"""
from dataclasses import dataclass

from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_attachment import (
    ConnectionAttachment,
    ResolvedAttachmentSurfaces,
    resolve_connection_attachment_surfaces,
)
from app.cad_engine.connection_location import ConnectionLocation
from app.cad_engine.interface import ValidatedConnection
from app.cad_engine.two_member_connection import GeneratedConnectionAssembly, build_two_member_connection_assembly

__all__ = ["ReviewedTwoMemberConnectionAssembly", "build_reviewed_two_member_connection_assembly"]


@dataclass
class ReviewedTwoMemberConnectionAssembly:
    """
    One explicit, project-space, fully reviewed two-member connection.
    Every relationship stays separately identifiable (Step 4) — never
    folded into a dict, never re-derived, never duplicated:

      - member_a / member_b: the two PlacedMembers (project-space
        geometry + the MemberPlacement that produced it — from 7I).
      - connection: the ValidatedConnection (identity, connected
        members, plate/hole spec — from 7D/7H), unchanged.
      - location: the ConnectionLocation (explicit project-space
        transform — from 7J), unchanged.
      - attachment_a / attachment_b: the explicit, reviewed
        ConnectionAttachment references (member_mark +
        surface_reference only — from 7M/7N), unchanged.
      - resolved_surfaces: the ACTUAL project-space CadQuery faces
        those two attachments resolve to (from 7M's resolver).
      - connection_geometry: the generated connection plate/hole
        geometry (from 7K/7L), independently positioned via
        `location` — never derived from resolved_surfaces or from the
        attachments.

    `resolved_surfaces` (explicit intent, resolved) and
    `connection_geometry` (the plate/hole solid, positioned by
    `location` alone) are deliberately two separate computations —
    the connection's own geometry was never made to depend on the
    attachment surfaces, and vice versa (see module docstring).
    """
    member_a: PlacedMember
    member_b: PlacedMember
    connection: ValidatedConnection
    location: ConnectionLocation
    attachment_a: ConnectionAttachment
    attachment_b: ConnectionAttachment
    resolved_surfaces: ResolvedAttachmentSurfaces
    connection_geometry: GeneratedConnectionAssembly


def build_reviewed_two_member_connection_assembly(
    member_a: PlacedMember,
    member_b: PlacedMember,
    connection: ValidatedConnection,
    location: ConnectionLocation,
    attachment_a: ConnectionAttachment,
    attachment_b: ConnectionAttachment,
) -> ReviewedTwoMemberConnectionAssembly:
    """
    Composes an already-validated member pair, connection, location,
    and attachment pair into one ReviewedTwoMemberConnectionAssembly
    by calling the existing 7M resolver and 7K/7L geometry builder
    unchanged — never re-implementing their validation, contact
    checking, or geometry construction.

    Raises GeometryValidationError whenever either existing call does
    (member identity mismatch, missing/duplicate/unknown attachment,
    unsupported connection type, missing plate/hole data, or the
    connection plate failing to genuinely contact one or both
    members) — this function introduces no new rejection rule of its
    own.

    Never mutates any of its six inputs: both calls below are already
    side-effect free (see their own docstrings), and this function
    only reads their return values.
    """
    resolved_surfaces = resolve_connection_attachment_surfaces(
        connection, member_a, member_b, attachment_a, attachment_b,
    )
    connection_geometry = build_two_member_connection_assembly(
        connection, location, member_a, member_b,
    )

    return ReviewedTwoMemberConnectionAssembly(
        member_a=member_a,
        member_b=member_b,
        connection=connection,
        location=location,
        attachment_a=attachment_a,
        attachment_b=attachment_b,
        resolved_surfaces=resolved_surfaces,
        connection_geometry=connection_geometry,
    )
