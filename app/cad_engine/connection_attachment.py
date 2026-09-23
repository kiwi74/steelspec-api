"""
Milestone 7M — the smallest explicit contract for identifying WHICH
geometric surface of each positioned member a reviewed two-member
connection attaches to:

    ConnectionAttachment(member_mark, surface_reference)
        + PlacedMember (project-space geometry, from 7I)
            |
       resolve_connection_attachment_surfaces()
            |
      ResolvedAttachmentSurfaces (actual project-space faces)

STEP 2 — THE GAP THIS FILLS: by 7L, the architecture knew connection
identity (7H), connection project location (7J), and could validate
that a connection's plate genuinely contacts both members (7K/7L) —
but nothing said WHICH surface of Member A, or WHICH surface of Member
B, the connection was reviewed as attaching to. `member_a`/`member_b`
list order, `connection.position` ("START"/"END", which describes only
ONE member's own relationship to the connection, not the OTHER
member's), and "whichever face happens to be closest to the plate" are
all rejected here as hidden semantics — see Step 17's adversarial
tests proving the resolver never substitutes a nearer face for the
explicitly reviewed one.

STEP 4 — WHY THIS REUSES "END"/"START" RATHER THAN A FACE INDEX OR A
NEW TAXONOMY: a raw CadQuery face index (e.g. "face 7") is exactly the
kind of fragile reference this milestone was warned against — OCCT's
internal face ordering is not a stable public contract and can change
when geometry changes. Investigated instead: every section family this
project supports (PFC, UB, SHS, RHS — see sections.py) is built as a
single extrusion along local Z with exactly one flat cap at each Z
extreme (verified directly, in a throwaway sandbox script, for both UB
and PFC before writing any test: `solid.faces('>Z')` and
`solid.faces('<Z')` each return EXACTLY one face, with the expected
cross-sectional area and a normal of exactly (0,0,+1)/(0,0,-1) — even
after a 90-degree Z-axis MemberPlacement rotation, since rotating a
Z-pointing normal about the Z axis leaves it unchanged). This is a
robust GEOMETRIC EXTREMUM selector (a stable, semantic
`app.cad_engine.connections.CONNECTION_POSITION_END` /
`CONNECTION_POSITION_START` label resolved against real topology), not
an arbitrary index — and it is exactly the same vocabulary
ValidatedConnection.position already uses for the single-member case,
reused rather than duplicated.

SCOPE (Step 4/15): this milestone deliberately supports only the two
axial end-cap surfaces (END, START) — not TOP/BOTTOM/LEFT/RIGHT/
FRONT/BACK side faces. A semantic label for a section's flange or web
face would need a documented local-frame convention this project has
never established (which side is "TOP" for a PFC channel? relative to
what?), and inventing one here, unused by any existing connection
geometry, would be exactly the kind of universal face taxonomy this
milestone was told not to build. END/START is sufficient for every
connection scenario 7K/7L actually exercise (an axial end-to-end
splice) and is proven to work identically for both section families
tested (UB, PFC) — a documented limitation, not a silent gap.

STEP 5 — LOCAL vs PROJECT SEPARATION: ConnectionAttachment never holds
a CadQuery object — only two plain strings (member_mark,
surface_reference). Resolution happens fresh, on demand, against
whatever `PlacedMember.geometry.solid` currently is (already
project-space, per 7I) — never a face computed once and cached. This
is why Step 10/16's placement-sensitivity property holds for free:
moving a member's MemberPlacement changes what
`member.geometry.solid.faces(...)` returns the next time it's called,
while the ConnectionAttachment reference itself (two strings) cannot
change as a side effect of anything.

IDENTITY (Step 3): no connection_id, no connected_members list, and no
CadQuery geometry inside ConnectionAttachment — matching
ConnectionLocation's own precedent (Milestone 7J) of carrying no
identity. Matching between attachments and PlacedMembers is done BY
MARK (a set/dict lookup), never by positional/list-order
correspondence — this is what makes Step 11's member-order
independence hold structurally, not by convention.

GEOMETRY UNCHANGED (Step 18): this module never calls
build_two_member_connection_assembly() and never builds new plate/
bolt/weld geometry. It only resolves and returns actual CadQuery Face
objects for inspection — nothing here changes what 7K/7L's own
connection geometry looks like.
"""
from dataclasses import dataclass
from typing import Any

from app.cad_engine import connections as connection_geometry
from app.cad_engine.assembly import PlacedMember
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedConnection

__all__ = [
    "ConnectionAttachment", "ResolvedAttachmentSurfaces", "resolve_connection_attachment_surfaces",
]


@dataclass(frozen=True)
class ConnectionAttachment:
    """
    An explicit, reviewed reference to WHICH surface of WHICH member a
    connection attaches to — never a CadQuery object, never inferred.
    `surface_reference` reuses connections.py's own
    SUPPORTED_CONNECTION_POSITIONS vocabulary ("END" / "START" — see
    module docstring on why no other surface label is supported yet).
    """
    member_mark: str
    surface_reference: str


@dataclass
class ResolvedAttachmentSurfaces:
    """
    The result of resolving two ConnectionAttachments against actual
    project-space member geometry — an explicit, external result (Step
    19), never folded into GeneratedAssembly or GeneratedConnectionAssembly.
    `member_a_face`/`member_b_face` are actual CadQuery Face objects,
    freshly resolved at call time — not stored anywhere for reuse.
    """
    member_a_attachment: ConnectionAttachment
    member_b_attachment: ConnectionAttachment
    member_a_face: Any
    member_b_face: Any


def _require_valid_attachment(attachment: ConnectionAttachment) -> None:
    if attachment is None:
        raise GeometryValidationError("Connection attachment is missing (got None).")
    mark = attachment.member_mark
    if not isinstance(mark, str) or not mark.strip():
        raise GeometryValidationError(
            f"Connection attachment has no trustworthy member_mark (got {mark!r})."
        )
    ref = attachment.surface_reference
    if not isinstance(ref, str) or not ref.strip():
        raise GeometryValidationError(
            f"Attachment for member '{mark}': surface_reference must be a non-blank string "
            f"(got {ref!r})."
        )
    if ref not in connection_geometry.SUPPORTED_CONNECTION_POSITIONS:
        raise GeometryValidationError(
            f"Attachment for member '{mark}': surface_reference {ref!r} is not a supported "
            f"attachment surface. Supported: {sorted(connection_geometry.SUPPORTED_CONNECTION_POSITIONS)}."
        )


def _resolve_single_face(member: PlacedMember, attachment: ConnectionAttachment) -> Any:
    """
    Resolves ONE explicit, reviewed surface reference against the
    member's OWN CURRENT project-space solid — a fresh geometric-extremum
    selection (`faces('>Z')` for END, `faces('<Z')` for START; see
    module docstring), never a face computed once and cached, never a
    "nearest face" search.
    """
    if attachment.surface_reference == connection_geometry.CONNECTION_POSITION_END:
        faces = member.geometry.solid.faces(">Z").vals()
    else:
        faces = member.geometry.solid.faces("<Z").vals()

    if len(faces) != 1:
        raise GeometryValidationError(  # defensive; every supported section family has exactly one
            f"Member '{member.mark}': expected exactly one '{attachment.surface_reference}' face, "
            f"found {len(faces)}."
        )
    return faces[0]


def resolve_connection_attachment_surfaces(
    connection: ValidatedConnection,
    member_a: PlacedMember,
    member_b: PlacedMember,
    attachment_a: ConnectionAttachment,
    attachment_b: ConnectionAttachment,
) -> ResolvedAttachmentSurfaces:
    """
    Resolves two explicit, reviewed ConnectionAttachments against the
    already-positioned project-space geometry of the two members a
    connection explicitly names, or raises GeometryValidationError
    explaining exactly why it cannot.

    Matching between attachments and PlacedMembers is done BY MARK
    (never by argument position), so swapping which PlacedMember/
    attachment pair is passed as "a" vs "b" changes only the resulting
    container's own labels — never which member resolves to which
    face (see module docstring on identity / Step 11).

    Raises GeometryValidationError when:
      - member_a.mark == member_b.mark (not two distinct members);
      - {member_a.mark, member_b.mark} != set(connection.connected_members)
        (same identity check 7K's build_two_member_connection_assembly()
        already performs — never inferred or substituted here);
      - either attachment is missing (None), has a blank/non-string
        member_mark, or a blank/non-string/unsupported surface_reference;
      - both attachments reference the same member (a duplicate
        attachment) rather than one each;
      - the two attachments' member_mark values do not exactly match
        {member_a.mark, member_b.mark} (an unknown or unlisted member).

    Never mutates member_a.geometry.solid, member_b.geometry.solid,
    `connection`, attachment_a, or attachment_b — every returned face
    is a fresh query result, not a stored/cached reference.
    """
    if member_a.mark == member_b.mark:
        raise GeometryValidationError(
            f"member_a and member_b must be two distinct members (both were '{member_a.mark}')."
        )
    members_by_mark = {member_a.mark: member_a, member_b.mark: member_b}

    expected_marks = set(connection.connected_members)
    if set(members_by_mark) != expected_marks:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': the supplied members "
            f"{sorted(members_by_mark)} do not match this connection's explicit "
            f"connected_members {sorted(expected_marks)}."
        )

    _require_valid_attachment(attachment_a)
    _require_valid_attachment(attachment_b)

    if attachment_a.member_mark == attachment_b.member_mark:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': both attachments reference member "
            f"'{attachment_a.member_mark}' — a duplicate attachment. Each connected member must "
            "have exactly one explicit attachment."
        )
    attachment_marks = {attachment_a.member_mark, attachment_b.member_mark}
    if attachment_marks != expected_marks:
        raise GeometryValidationError(
            f"Connection '{connection.connection_id}': attachment member marks "
            f"{sorted(attachment_marks)} do not match this connection's explicit "
            f"connected_members {sorted(expected_marks)}."
        )

    face_a = _resolve_single_face(members_by_mark[attachment_a.member_mark], attachment_a)
    face_b = _resolve_single_face(members_by_mark[attachment_b.member_mark], attachment_b)

    return ResolvedAttachmentSurfaces(
        member_a_attachment=attachment_a,
        member_b_attachment=attachment_b,
        member_a_face=face_a,
        member_b_face=face_b,
    )
