"""
Milestone 7Q — strengthening 7P's connection-geometry-consistency
check from a bare global-Z bounding-box comparison into an explicit
project-space PLANE + NORMAL consistency check between the resolved
attachment faces and the generated connection interface itself.

    resolved attachment face (7M/7N)        generated connection plate (7K/7L)
            |                                          |
            +------- validate_connection_interface_planes_and_normals() -------+
                                        |
                       ProjectSpaceInterfaceConsistencyResult

STEP 1 FINDINGS (empirical, verified in throwaway sandbox scripts
before any code was written — see the exact numbers quoted below):

  1. The connection plate is built LOCALLY (build_end_plate_with_holes(),
     unchanged, 7K) as a box [0,width]x[0,height]x[0,thickness], then
     positioned into project space by two_member_connection.py via
     `plate_solid.moved(cq.Location(location.x, location.y, location.z,
     location.rotation_x, location.rotation_y, location.rotation_z))`
     — ConnectionLocation.rotation_x/y/z DO reach the generated
     geometry (confirmed by reading two_member_connection.py directly);
     they have simply never been exercised as nonzero in any fixture
     across 7J-7P.
  2. The plate's own two large flat faces — `plate.faces("<Z")` and
     `plate.faces(">Z")` — each return EXACTLY ONE face, reusing the
     exact same, already-proven idiom connections.py's own
     `_ordered_plate_hole_records()` has relied on since Milestone 6D.
  3. Measured directly for the established 7O/7P fixture: plate `<Z`
     face at project Z=3994, normal EXACTLY (0,0,-1); plate `>Z` face
     at Z=4006, normal EXACTLY (0,0,+1). Member A's resolved END face:
     Z=4000, normal (0,0,+1). Member B's resolved START face: Z=4000,
     normal (0,0,-1). dot(A-END, plate `<Z`) = -1.0 exactly; dot(B-START,
     plate `>Z`) = -1.0 exactly — two mating faces facing each other.

WHY faces("<Z")/faces(">Z") IS SAFE HERE, AND WHY IT IS NOT GENERALIZED
FURTHER: correct only when the plate's own local Z axis stays aligned
with global Z after the connection's own placement, i.e. when
ConnectionLocation.rotation_x == rotation_y == 0 (and likewise for
each member's own placement, matching 7L/7M's existing restriction).
That scope is enforced explicitly — _require_supported_rotation_scope()
REJECTS rather than silently mis-measures a rotation combination
outside it. A validated, explicit limitation, not invented
generalized geometry logic.

STEP 5 — THE GEOMETRIC INVARIANT (two logically independent checks):

  1. POSITION: reused from 7P (validate_connection_geometry_against_
     attachments(), called unchanged, not duplicated) — does each
     resolved attachment face's own axial position fall within the
     generated plate's overall Z-extent?
  2. NORMAL: does each resolved attachment face's normal OPPOSE the
     normal of the SPECIFIC plate interface face physically nearest to
     that member? (dot product <= -1.0 + NORMAL_CONSISTENCY_TOLERANCE)

     PAIRING (which of the plate's two known faces belongs to which
     member) is decided using the MEMBER'S OWN SOLID bounding-box
     midpoint — not the member's single attachment-face position, and
     not the plate-face normal. This matters for two concrete reasons,
     both verified empirically before this design was finalised:
       (a) for the established symmetric splice fixture, BOTH members'
           attachment faces sit at the exact same project-space Z
           (4000) — equidistant from both plate faces (6mm each way) —
           so pairing by attachment-face position alone is genuinely
           AMBIGUOUS (a tie) for this fixture;
       (b) pairing by "whichever plate face gives the better-matching
           normal" was considered and REJECTED as circular — it would
           make the normal check trivially always pass, since the
           pairing step would silently pick whichever answer the
           validation step wanted.
     The member's own solid spans a LARGE, unambiguous Z range
     entirely on one side of the interface (Member A: [0,4000],
     midpoint 2000; Member B: [4000,7000], midpoint 5500) — comparing
     that midpoint to the plate's two face positions resolves the tie
     correctly and without circularity, verified directly: Member A's
     midpoint (2000) is closer to the plate's `<Z` face (3994, distance
     1994) than to `>Z` (4006, distance 2006); Member B's midpoint
     (5500) is closer to `>Z` (4006, distance 1494) than to `<Z` (3994,
     distance 1506). This is never a search among an arbitrary set of
     faces, and never a substitute for the explicit attachment
     reference, which is resolved first, unconditionally, by the
     unmodified 7M resolver — this pairing only decides which of the
     plate's own two (already fully characterized) sides that
     already-resolved face relates to.

STEP 7 — HOW THIS RELATES TO 7P (app/cad_engine/
connection_geometry_consistency.py, left completely unmodified, called
by this module rather than duplicated): 7Q SUPPLEMENTS 7P. 7Q calls
7P's own validate_connection_geometry_against_attachments() first —
reusing its position check exactly, not reimplementing it — then adds
the normal-consistency check 7P has no concept of at all.

AN HONEST FINDING ABOUT WHERE FAILURES ACTUALLY GET CAUGHT: given the
current attachment vocabulary (only END/START) and this fixture's
topology, every WRONG-attachment case constructible today (e.g.
reviewing Member A's START when the real interface is at its END)
already fails 7P's position check first — because each member's two
candidate faces (its own END and START) are never near each other in
Z (0mm vs 4000mm for Member A; 4000mm vs 7000mm for Member B), so a
wrong choice always lands far outside the plate's own Z-range too.
Verified directly: for both wrong-attachment fixtures used in this
milestone's tests, the resolved face's Z position is >1990mm outside
the plate's own range. This means 7Q's own normal check is not, today,
independently load-bearing for REJECTING a case that 7P/7K/7L do not
already reject — but it IS independently computed and independently
verified to also disagree in exactly those cases (dot product +1.0
instead of <= -0.99, confirmed by direct measurement — see
tests/test_connection_interface_consistency.py's dedicated geometric
tests of the underlying dot product, run separately from the composed
end-to-end validator). Its value is (a) a genuinely new, physically
meaningful invariant (mating-surface normal opposition) that 7P cannot
express at all, and (b) a check that would become uniquely load-bearing
the moment a future connection geometry or attachment vocabulary
produced a case where position could coincidentally match while
orientation did not — a case the current architecture cannot
construct, so it is not claimed to be tested here.

Boundary statement (unchanged in spirit from 7P): a passing result
means only that the generated connection interface geometry occupies a
compatible project-space plane and normal relationship with the
explicitly reviewed attachment faces. It does NOT mean the connection
is structurally adequate, correctly sized, code-compliant, or
fabrication-ready.
"""
from dataclasses import dataclass
from typing import Any

from app.cad_engine.connection_geometry_consistency import validate_connection_geometry_against_attachments
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly

__all__ = [
    "ProjectSpaceInterfaceConsistencyResult",
    "validate_connection_interface_planes_and_normals",
]

# A dimensionless dot-product (cosine) tolerance — NOT a distance, NOT
# an angle in degrees, and NOT the same kind of quantity as 7P's
# GEOMETRIC_ALIGNMENT_TOLERANCE_MM (a millimetre position tolerance)
# or 7L's _MEANINGFUL_CONTACT_MARGIN_MM (a millimetre minimum-contact-
# area margin). Two perfectly opposing unit normals have a dot product
# of exactly -1.0; this allows a small, documented slack for
# floating-point noise only (measured dot products in this module's
# own verification sandbox were exact to at least 1e-10 for every
# in-scope case) — not an allowance for genuine angular misalignment.
NORMAL_CONSISTENCY_TOLERANCE = 0.01


@dataclass(frozen=True)
class ProjectSpaceInterfaceConsistencyResult:
    """The measured project-space geometry this validator's decision was based on."""
    member_a_face_position_mm: float
    member_a_face_normal: tuple[float, float, float]
    member_a_plate_face_position_mm: float
    member_a_plate_face_normal: tuple[float, float, float]
    member_a_normal_dot: float

    member_b_face_position_mm: float
    member_b_face_normal: tuple[float, float, float]
    member_b_plate_face_position_mm: float
    member_b_plate_face_normal: tuple[float, float, float]
    member_b_normal_dot: float


def _require_supported_rotation_scope(assembly: ReviewedTwoMemberConnectionAssembly) -> None:
    """
    This module's face selectors (plate.faces("<Z")/(">Z")) are only
    proven correct when every relevant face stays exactly Z-normal in
    project space — true for a ConnectionLocation with zero
    rotation_x/rotation_y, and for a MemberPlacement with zero
    rotation_x/rotation_y (Z-axis rotation alone leaves a Z-normal
    face's own normal and axial position unchanged — see module
    docstring). Explicitly REJECTS rather than silently producing an
    unverified answer for anything outside that scope.
    """
    connection_id = assembly.connection.connection_id
    loc = assembly.location
    if loc.rotation_x != 0.0 or loc.rotation_y != 0.0:
        raise GeometryValidationError(
            f"Connection '{connection_id}': project-space interface plane/normal validation is "
            f"not supported for a ConnectionLocation with nonzero rotation_x/rotation_y (got "
            f"rotation_x={loc.rotation_x}, rotation_y={loc.rotation_y}). This is a validated scope "
            "limitation, not an unverified generalization."
        )
    for member in (assembly.member_a, assembly.member_b):
        placement = member.placement
        if placement.rotation_x != 0.0 or placement.rotation_y != 0.0:
            raise GeometryValidationError(
                f"Connection '{connection_id}': project-space interface plane/normal validation "
                f"is not supported for member '{member.mark}' with nonzero rotation_x/rotation_y "
                f"(got rotation_x={placement.rotation_x}, rotation_y={placement.rotation_y})."
            )


def _axial_position_mm(face: Any) -> float:
    bbox = face.BoundingBox()
    return (bbox.zmin + bbox.zmax) / 2.0


def _pair_plate_face(member_solid: Any, plate_lo_face: Any, plate_hi_face: Any) -> tuple:
    """
    See module docstring's STEP 5/PAIRING note for why this uses the
    member's own SOLID bounding-box midpoint (never the single
    attachment face, never plate-face normal matching).
    """
    solid_bbox = member_solid.val().BoundingBox()
    solid_mid = (solid_bbox.zmin + solid_bbox.zmax) / 2.0
    lo_pos = _axial_position_mm(plate_lo_face)
    hi_pos = _axial_position_mm(plate_hi_face)
    if abs(solid_mid - lo_pos) <= abs(solid_mid - hi_pos):
        return plate_lo_face, lo_pos
    return plate_hi_face, hi_pos


def _check_member_normal(
    placed_member: Any,
    member_face: Any,
    surface_reference: str,
    plate_lo_face: Any,
    plate_hi_face: Any,
    connection_id: str,
) -> tuple:
    member_pos = _axial_position_mm(member_face)
    member_normal = member_face.normalAt()

    plate_face, plate_pos = _pair_plate_face(placed_member.geometry.solid, plate_lo_face, plate_hi_face)
    plate_normal = plate_face.normalAt()

    dot = member_normal.dot(plate_normal)
    if dot > -1.0 + NORMAL_CONSISTENCY_TOLERANCE:
        raise GeometryValidationError(
            f"Connection '{connection_id}': the reviewed attachment '{surface_reference}' for "
            f"member '{placed_member.mark}' has a face normal that does not oppose the generated "
            f"connection interface face's own normal (dot product {dot:.6f}, expected <= "
            f"{-1.0 + NORMAL_CONSISTENCY_TOLERANCE:.3f}). Two mating surfaces must face each "
            "other, not the same direction — the reviewed attachment does not correspond to a "
            "physically compatible interface with the generated connection geometry."
        )

    return member_pos, member_normal.toTuple(), plate_pos, plate_normal.toTuple(), dot


def validate_connection_interface_planes_and_normals(
    assembly: ReviewedTwoMemberConnectionAssembly,
) -> ProjectSpaceInterfaceConsistencyResult:
    """
    Validates that the reviewed, resolved attachment faces for both
    members of `assembly` occupy project-space planes and normals
    compatible with the generated connection plate's own two interface
    faces — see module docstring for the exact invariant, the pairing
    method, and an honest account of where each failure mode is
    actually caught (7P vs 7Q).

    Calls 7P's validate_connection_geometry_against_attachments()
    first, unchanged — reusing its position check exactly rather than
    duplicating it — then adds the normal-consistency check 7P has no
    concept of. Consumes only already-created, already-generated
    objects; never infers an attachment, never selects a face among an
    arbitrary set, never moves a member, the connection, or the plate,
    and never mutates any input. Returns a
    ProjectSpaceInterfaceConsistencyResult on success; raises
    GeometryValidationError — from 7P if the position check fails, or
    from this module's own check if the normal check fails, or if the
    assembly's rotation configuration is outside this module's
    validated scope.
    """
    _require_supported_rotation_scope(assembly)

    validate_connection_geometry_against_attachments(assembly)  # 7P's position check, reused unchanged

    plate = assembly.connection_geometry.plate
    plate_lo_faces = plate.faces("<Z").vals()
    plate_hi_faces = plate.faces(">Z").vals()
    if len(plate_lo_faces) != 1 or len(plate_hi_faces) != 1:
        raise GeometryValidationError(  # defensive; unreachable for the currently-supported plate geometry
            f"Connection '{assembly.connection.connection_id}': expected exactly one plate face on "
            f"each of <Z/>Z, found {len(plate_lo_faces)}/{len(plate_hi_faces)}."
        )
    plate_lo_face, plate_hi_face = plate_lo_faces[0], plate_hi_faces[0]

    (a_pos, a_normal, a_plate_pos, a_plate_normal, a_dot) = _check_member_normal(
        assembly.member_a,
        assembly.resolved_surfaces.member_a_face,
        assembly.resolved_surfaces.member_a_attachment.surface_reference,
        plate_lo_face, plate_hi_face,
        assembly.connection.connection_id,
    )
    (b_pos, b_normal, b_plate_pos, b_plate_normal, b_dot) = _check_member_normal(
        assembly.member_b,
        assembly.resolved_surfaces.member_b_face,
        assembly.resolved_surfaces.member_b_attachment.surface_reference,
        plate_lo_face, plate_hi_face,
        assembly.connection.connection_id,
    )

    return ProjectSpaceInterfaceConsistencyResult(
        member_a_face_position_mm=a_pos,
        member_a_face_normal=a_normal,
        member_a_plate_face_position_mm=a_plate_pos,
        member_a_plate_face_normal=a_plate_normal,
        member_a_normal_dot=a_dot,
        member_b_face_position_mm=b_pos,
        member_b_face_normal=b_normal,
        member_b_plate_face_position_mm=b_plate_pos,
        member_b_plate_face_normal=b_plate_normal,
        member_b_normal_dot=b_dot,
    )
