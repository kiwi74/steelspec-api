"""
Milestone 7P — closing the seam 7O's own final report identified:

    reviewed attachment references -> resolved project-space member faces   (7M/7N)
    ValidatedConnection + ConnectionLocation -> generated connection geometry  (7K/7L)

These two branches are computed completely independently (see
reviewed_connection_assembly.py's own docstring) — nothing previously
checked that the generated connection plate actually sits where the
explicitly reviewed attachment faces say it should. This module adds
exactly that one check, and nothing else.

STEP 2 — THE EXACT QUESTION: for the reviewed two-member connection,
does the generated connection plate occupy the project-space location
implied by the two explicitly resolved attachment faces? This is a
GEOMETRY-CONSISTENCY question only — see module docstring's boundary
statement below for what a passing result does and does not mean.

STEP 4 — THE CHOSEN INVARIANT, AND WHY (derived from actual generated
geometry, verified empirically in a throwaway sandbox before writing
any test): every attachment face this architecture resolves (7M:
END/START only) is a flat face normal to global Z — proven directly
(a Z-normal of exactly (0,0,+1)/(0,0,-1), even after a 90-degree
Z-axis MemberPlacement rotation, and a degenerate Z-extent, i.e.
face.BoundingBox().zmin == .zmax). That single Z value is the face's
own project-space axial position. The chosen invariant is:

    each attachment face's own axial position must fall within the
    generated connection plate's own bounding-box Z-extent (plus a
    small numerical tolerance).

WHY THIS IS THE RIGHT (AND NON-REDUNDANT) CHECK — an honest finding,
not an assumption: for the currently-established two-member
end-to-end splice fixture, where Member A's END and Member B's START
coincide at exactly the same project-space Z, empirical testing showed
that 7L's own existing contact check (touching-union arity +
meaningful X/Y overlap, evaluated against each member's ENTIRE solid)
ALREADY implies this Z-alignment for any ConnectionLocation or
MemberPlacement change that still passes it — because the only Z
where a thin plate can simultaneously touch both members' disjoint
solids is right at their shared interface. In other words, for this
fixture, a wrong ConnectionLocation or a moved member is already
caught upstream by build_two_member_connection_assembly() (7K/7L)
before this validator would ever run. That is not a weakness of this
module — it is the correct, honestly-reported consequence of this
fixture's topology, and it is exercised directly in
tests/test_connection_geometry_consistency.py (Cases A/C/E).

The genuinely NEW failure mode this module catches — the one 7L
structurally cannot, because build_two_member_connection_assembly()
never reads attachment data at all — is a WRONG REVIEWED ATTACHMENT:
an explicit surface_reference that resolves to a real face on a real
member, but not the one the actual connection geometry sits against
(e.g. reviewing Member A's START when the connection genuinely sits at
Member A's END). The connection geometry and the wrong attachment are
each independently "valid" — 7K/7L's contact check still passes
(it never looked at the attachment), and 7M's resolver still resolves
a real face (it never looked at the connection geometry) — but the two
now disagree about where the connection actually is. That disagreement
is exactly what this module's invariant detects (see
tests/test_connection_geometry_consistency.py's Case B, the milestone's
own "critical proof").

A SEPARATE X/Y "meaningful overlap between the face and the plate"
check was investigated and deliberately NOT added: because a resolved
END/START face's own bounding box always exactly equals the full
cross-sectional footprint of the member it belongs to, that check
would always agree with 7L's own member-solid-vs-plate meaningful
overlap check — provably redundant for this geometry, not a distinct
protection. Adding it would have been exactly the "second contact
algorithm" this milestone was told not to build.

STEP 5 — WHAT A PASSING RESULT MEANS, AND WHAT IT DOES NOT: a passing
result means only that the generated connection geometry occupies the
expected project-space relationship to the explicitly reviewed
attachment surfaces. It does NOT mean the connection is structurally
adequate, that the plate is correctly sized, that bolt spacing or
capacity is adequate, that welds are adequate, that the connection
complies with NZS 3404 or any other standard, that the member has
adequate capacity, that clearances are adequate, or that the
connection is fabrication-ready.

STEP 3/15 — NO INFERENCE, NO REPAIR: this module never selects a face,
never substitutes END for START or vice versa, never moves a member,
the connection, or the plate, and never mutates any of its inputs. It
only measures already-generated geometry and raises
GeometryValidationError when the measured relationship is inconsistent.

SCOPE (Step 12): this validator is scoped to exactly the geometry
7K/7L/7M already support — a single END_PLATE connection between two
axially-splice members, with attachment faces resolved only as
END/START (Z-normal). It makes no claim about gussets, fin plates,
angle cleats, brackets, welded joints, stiffeners, coping, notches, or
base plates.
"""
from dataclasses import dataclass
from typing import Any

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly

__all__ = ["ConnectionGeometryConsistencyResult", "validate_connection_geometry_against_attachments"]

# A geometric NUMERICAL tolerance — a CAD robustness margin for
# floating-point noise and boundary rounding, NOT an engineering
# fit-up or fabrication tolerance. Framed like connections.py's own
# _HOLE_CUT_MARGIN_MM and two_member_connection.py's own
# _MEANINGFUL_CONTACT_MARGIN_MM, but deliberately much smaller (1mm,
# not 5-10mm): this check tests whether a point (the face's own axial
# position) lies within a range (the plate's own Z-extent), not a
# minimum contact area, so there is no real-world reason it should
# ever need more than float-noise-scale slack for the currently
# supported end-cap-face geometry. Do not confuse this with 7L's
# _MEANINGFUL_CONTACT_MARGIN_MM, which serves a different purpose
# (minimum contact area) on a different check.
GEOMETRIC_ALIGNMENT_TOLERANCE_MM = 1.0


@dataclass(frozen=True)
class ConnectionGeometryConsistencyResult:
    """
    The measured geometry this validator's decision was based on —
    returned on success so callers/tests can inspect the actual
    numbers, never just a boolean.
    """
    member_a_face_position_mm: float
    member_b_face_position_mm: float
    plate_zmin_mm: float
    plate_zmax_mm: float


def _axial_position_mm(face: Any) -> float:
    """
    The resolved attachment face's own position along its normal axis.
    Every face 7M resolves is Z-normal with a degenerate Z-extent
    (zmin == zmax, verified empirically — see module docstring), so
    the midpoint of that degenerate range is exactly the face's own
    project-space axial position.
    """
    bbox = face.BoundingBox()
    return (bbox.zmin + bbox.zmax) / 2.0


def validate_connection_geometry_against_attachments(
    assembly: ReviewedTwoMemberConnectionAssembly,
) -> ConnectionGeometryConsistencyResult:
    """
    Checks that the generated connection plate
    (assembly.connection_geometry.plate) genuinely occupies the
    project-space location implied by the two explicitly resolved
    attachment faces (assembly.resolved_surfaces) — see module
    docstring for the exact invariant and why it was chosen.

    Consumes only already-created, already-generated objects (project-
    space member geometry, resolved attachment faces, generated
    connection geometry) — never parses raw data, never constructs
    members or connections, never infers or substitutes an attachment,
    never repositions anything. Returns a
    ConnectionGeometryConsistencyResult on success; raises
    GeometryValidationError explaining exactly which member's
    attachment is inconsistent, and by how much, on failure.
    """
    plate_bbox = assembly.connection_geometry.plate.val().BoundingBox()
    lo = plate_bbox.zmin - GEOMETRIC_ALIGNMENT_TOLERANCE_MM
    hi = plate_bbox.zmax + GEOMETRIC_ALIGNMENT_TOLERANCE_MM

    positions: dict[str, float] = {}
    for mark, face, attachment in (
        (
            assembly.member_a.mark,
            assembly.resolved_surfaces.member_a_face,
            assembly.resolved_surfaces.member_a_attachment,
        ),
        (
            assembly.member_b.mark,
            assembly.resolved_surfaces.member_b_face,
            assembly.resolved_surfaces.member_b_attachment,
        ),
    ):
        position = _axial_position_mm(face)
        positions[mark] = position
        if not (lo <= position <= hi):
            raise GeometryValidationError(
                f"Connection '{assembly.connection.connection_id}': the reviewed attachment "
                f"'{attachment.surface_reference}' for member '{mark}' resolves to a face at "
                f"Z={position:.3f}mm, which does not lie within the generated connection plate's "
                f"own Z-extent [{plate_bbox.zmin:.3f}, {plate_bbox.zmax:.3f}]mm (tolerance "
                f"{GEOMETRIC_ALIGNMENT_TOLERANCE_MM}mm). The reviewed attachment surface does not "
                "correspond to where the generated connection geometry actually sits. Reported as "
                "an explicit geometric inconsistency — no attachment, member, or connection is "
                "repositioned to force agreement."
            )

    return ConnectionGeometryConsistencyResult(
        member_a_face_position_mm=positions[assembly.member_a.mark],
        member_b_face_position_mm=positions[assembly.member_b.mark],
        plate_zmin_mm=plate_bbox.zmin,
        plate_zmax_mm=plate_bbox.zmax,
    )
