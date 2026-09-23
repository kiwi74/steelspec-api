"""
Milestone 7R — a small, explicit validation GATE establishing, in one
call, that a ReviewedTwoMemberConnectionAssembly has passed every
currently-required geometric consistency check.

STEP 1 FINDING — which checks already run, and where:

  - 7K/7L (contact): already runs DURING construction of the assembly
    itself — app.cad_engine.two_member_connection.
    build_two_member_connection_assembly() raises GeometryValidationError
    if the plate does not genuinely contact both members (touching-union
    arity + meaningful-overlap). Consequence: a
    ReviewedTwoMemberConnectionAssembly object can only exist at all if
    7K/7L's check already passed — this gate never needs to (and
    cannot usefully) re-run it; the object's mere existence is the
    proof.
  - 7P (position): validate_connection_geometry_against_attachments()
    (app.cad_engine.connection_geometry_consistency) is an explicit
    POST-BUILD validator, not run automatically during construction.
  - 7Q (plane/normal): validate_connection_interface_planes_and_normals()
    (app.cad_engine.connection_interface_consistency) is also a
    POST-BUILD validator — and it already CALLS 7P's function
    internally, first, before adding its own normal check (see that
    module's own docstring, Step 7 of Milestone 7Q).

CONSEQUENCE FOR THIS GATE (Step 3): calling 7Q's validator is already
sufficient to run BOTH 7P's and 7Q's checks, in the correct order,
exactly once each. This module therefore calls ONLY
validate_connection_interface_planes_and_normals() — never calling 7P
separately, which would run its position check a second time for no
benefit. The full, single dependency chain this gate represents is:

    ReviewedTwoMemberConnectionAssembly already exists
        => 7K/7L contact already satisfied (construction-time)
    validate_connection_interface_planes_and_normals(assembly)
        => 7P position check (called first, internally, by 7Q)
        => 7Q normal check (this module's own addition)
    => ReviewedConnectionValidationResult

STEP 4/6 — meaning, precisely: this gate does not repair, infer, or
soften anything — every GeometryValidationError raised by 7P or 7Q
propagates unchanged; this module wraps nothing and adds no new
rejection rule of its own. A successful result means only:

    Geometric consistency of the explicitly reviewed connection
    assembly has been validated against the currently-supported CAD
    geometry rules.

It does NOT mean structural adequacy, engineering approval, code
compliance, fabrication readiness, correct bolt capacity, correct weld
design, correct plate thickness, correct member capacity, or correct
AI interpretation of anything.

STEP 5 — result contract: the simplest option that fits the existing
architecture is chosen — a small immutable result naming which layers
were validated and wrapping 7Q's own already-returned measurement
result (ProjectSpaceInterfaceConsistencyResult) by reference, never
copying or duplicating its fields into a second, parallel
representation.
"""
from dataclasses import dataclass

from app.cad_engine.connection_interface_consistency import (
    ProjectSpaceInterfaceConsistencyResult,
    validate_connection_interface_planes_and_normals,
)
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly

__all__ = ["ReviewedConnectionValidationResult", "validate_reviewed_connection_assembly"]

# The geometric consistency layers this gate confirms, in the actual
# order they are satisfied — 7K/7L at construction time (already true
# by the time an assembly object exists), then 7P and 7Q via the
# single composed call below. Not a status framework — a fixed,
# documented, three-element label tuple.
VALIDATED_LAYERS = (
    "7K/7L member-plate contact (already satisfied at assembly construction time)",
    "7P attachment-face position consistency",
    "7Q project-space interface plane/normal consistency",
)


@dataclass(frozen=True)
class ReviewedConnectionValidationResult:
    """
    Confirms only that geometric consistency of the explicitly
    reviewed connection assembly has been validated against the
    currently-supported CAD geometry rules — see module docstring for
    the precise boundary of what this does and does not mean.
    """
    connection_id: str
    layers_validated: tuple[str, ...]
    interface_result: ProjectSpaceInterfaceConsistencyResult


def validate_reviewed_connection_assembly(
    assembly: ReviewedTwoMemberConnectionAssembly,
) -> ReviewedConnectionValidationResult:
    """
    Runs every currently-required geometric consistency check against
    `assembly`, in the single correct order (see module docstring),
    and returns a ReviewedConnectionValidationResult on success.

    Raises GeometryValidationError — propagated unchanged from 7Q (and,
    via 7Q's own internal call, from 7P) — on any inconsistency. Never
    catches, wraps, or converts a failure into a successful result;
    never repairs, infers, or mutates anything.
    """
    interface_result = validate_connection_interface_planes_and_normals(assembly)

    return ReviewedConnectionValidationResult(
        connection_id=assembly.connection.connection_id,
        layers_validated=VALIDATED_LAYERS,
        interface_result=interface_result,
    )
