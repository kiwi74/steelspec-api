"""
Milestone 7AA — the automation pipeline: the single orchestration seam
connecting the existing reviewed-connection assembly (7O) and
validation gate (7R) to the 7Z automation gate, so that 7Z ALWAYS
receives genuine validation evidence produced by the existing
validation path — never a value the caller invented or guessed:

    ConnectionReviewPackage (already reviewed)
            |
    build_reviewed_connection_specification()      (7W -> 7V specification, unchanged)
            |
    7D real_connection_to_validated_connection()   (existing adapter, unchanged)
    7J real_connection_location_to_connection_location()
    7N reviewed_connection_detail_to_attachments()
            |
    7AA scope: exactly two connected members  -> 7O/7R (unchanged);
    three or more connected members           -> 7AV's multi-member path
       (multi_member_connection.py — the per-member generalization of 7O/7R)
    7A real_member_to_validated_member() + generate_geometry() + 7I place_member_geometry()
            |
    build_reviewed_two_member_connection_assembly()   (7O, unchanged)
    build_reviewed_multi_member_connection_assembly() (7AV, new, beside 7O)
            |
    validate_reviewed_connection_assembly()           (7R, unchanged — composes 7P and 7Q)
    validate_multi_member_connection_assembly()       (7AV, new, beside 7R)
            |
    evaluate_automation_gate(package, validation_passed=<validation outcome>)   (7Z, unchanged)
            |
    AutomationPipelineResult — AUTO / CONFIRM / REVIEW is decided by 7Z, never by this module

HARD RULES:

  - 7Z REMAINS THE FINAL DECISION. This module never writes
    `if validation_passed: AUTO` and never inspects the gate's
    outcome beyond returning it. There is exactly one call to
    evaluate_automation_gate() and exactly one source of
    `validation_passed`: whether validate_reviewed_connection_assembly()
    (7R) actually returned. The pipeline's public API takes NO
    validation_passed parameter — the caller cannot inject one.
  - NOTHING IS WEAKENED, BYPASSED OR REPLACED. Every check runs
    exactly as its owning module implements it; a
    GeometryValidationError from any layer (7D/7J/7N/7A/7I/7O/7R)
    propagates into the result as a preserved failure, and 7Z is then
    called with validation_passed=False. It is never caught and
    converted into a successful validation, and 7R is never
    substituted with a simplified check.
  - FAILURE INFORMATION IS PRESERVED FOR THE UI. A failure records
    (a) the stage that rejected the connection — a stable string
    naming the existing layer (7D_CONNECTION, 7J_LOCATION,
    7N_ATTACHMENT, 7A_MEMBER, 7O_ASSEMBLY, 7R_VALIDATION) or one of
    this module's own two context stages (7AA_SCOPE,
    7AA_MEMBER_CONTEXT); (b) the error_code — the existing exception
    type's own stable name (e.g. "GeometryValidationError"; the
    existing error type carries no code field, so its class name is
    reused, never a parallel vocabulary), or
    AUTOMATION_PIPELINE_INPUT_GAP for the two context stages, which
    are input-context gaps, not engineering findings; (c) the
    message — the existing exception's text, verbatim.
  - NO SECOND COMPLETENESS DEFINITION. The pipeline builds the
    specification via 7W's builder and lets 7D/7J/7N/7O/7R reject
    whatever they reject, in their own words; it never pre-checks the
    7V completeness result or the 7Z report to skip or change a step.
    An incomplete connection fails at the first existing adapter that
    requires the missing field — before any assembly exists.
  - NO ENGINEERING JUDGMENT HERE. The two context stages state only
    this pipeline's own input contract: a connection names two
    members (7O) or three or more (7AV's multi-member path); one or
    zero connected members can be assembled by neither, and nothing
    is inferred, dropped or re-paired to make a connection fit a
    path. The caller must supply the member rows, placements and
    section matcher (this milestone adds no database, so member
    context is an explicit input). "reviewed" status, AI confidence,
    or a passing gate NEVER stand in for geometric validation: if the
    assembly cannot be built or validation rejects,
    validation_passed is False and 7Z cannot return AUTO.
  - DETERMINISTIC, READ-ONLY, NO I/O. No database, no network, no
    Claude, no API route, no file writes, no Supabase, no FastAPI, no
    AI-extraction import. The package and every supplied mapping are
    never mutated; repeated calls return equal results.

BOUNDARY: a successful pipeline result means only that the reviewed
connection's assembly passed the existing geometric consistency gates
and that 7Z then decided AUTO/CONFIRM/REVIEW on that genuine evidence.
It is NOT engineering approval, structural adequacy, code compliance
or fabrication readiness (see 7R's and 7Z's own scope statements).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.cad_engine.assembly import PlacedMember
from app.cad_engine.automation_gate import AutomationGateResult, evaluate_automation_gate
from app.cad_engine.connection_location import real_connection_location_to_connection_location
from app.cad_engine.connection_review_package import (
    ConnectionReviewPackage,
    build_reviewed_connection_specification,
)
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import MemberPlacement, place_member_geometry
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_assembly import (
    ReviewedTwoMemberConnectionAssembly,
    build_reviewed_two_member_connection_assembly,
)
from app.cad_engine.reviewed_connection_attachment import reviewed_connection_detail_to_attachments
from app.cad_engine.reviewed_connection_specification import ReviewedConnectionSpecification
from app.cad_engine.reviewed_connection_validation_gate import (
    ReviewedConnectionValidationResult,
    validate_reviewed_connection_assembly,
)
from app.cad_engine.multi_member_connection import (
    ReviewedMultiMemberConnectionAssembly,
    MultiMemberValidationResult,
    build_reviewed_multi_member_connection_assembly,
    validate_multi_member_connection_assembly,
)

__all__ = [
    "PIPELINE_STAGE_CONNECTION", "PIPELINE_STAGE_LOCATION", "PIPELINE_STAGE_ATTACHMENTS",
    "PIPELINE_STAGE_SCOPE", "PIPELINE_STAGE_MEMBER_CONTEXT", "PIPELINE_STAGE_MEMBER",
    "PIPELINE_STAGE_ASSEMBLY", "PIPELINE_STAGE_VALIDATION",
    "PIPELINE_STAGE_MULTI_ASSEMBLY", "PIPELINE_STAGE_MULTI_VALIDATION", "PIPELINE_STAGES",
    "PIPELINE_GAP_ERROR_CODE",
    "AutomationValidationFailure", "AutomationPipelineResult",
    "evaluate_reviewed_connection_for_automation",
]

# Where a failure happened. Each stage names an existing layer, in pipeline order; the two 7AA_*
# stages are this module's own input-contract statements (not engineering findings), and the two
# 7AV_* stages are 7AV's own multi-member counterpart layers (milestone 7AV, which generalize
# 7O/7R's checks to 3+ members — see multi_member_connection.py).
PIPELINE_STAGE_CONNECTION = "7D_CONNECTION"
PIPELINE_STAGE_LOCATION = "7J_LOCATION"
PIPELINE_STAGE_ATTACHMENTS = "7N_ATTACHMENT"
PIPELINE_STAGE_SCOPE = "7AA_SCOPE"
PIPELINE_STAGE_MEMBER_CONTEXT = "7AA_MEMBER_CONTEXT"
PIPELINE_STAGE_MEMBER = "7A_MEMBER"
PIPELINE_STAGE_ASSEMBLY = "7O_ASSEMBLY"
PIPELINE_STAGE_VALIDATION = "7R_VALIDATION"
PIPELINE_STAGE_MULTI_ASSEMBLY = "7AV_MULTI_ASSEMBLY"
PIPELINE_STAGE_MULTI_VALIDATION = "7AV_MULTI_VALIDATION"
PIPELINE_STAGES = (
    PIPELINE_STAGE_CONNECTION, PIPELINE_STAGE_LOCATION, PIPELINE_STAGE_ATTACHMENTS,
    PIPELINE_STAGE_SCOPE, PIPELINE_STAGE_MEMBER_CONTEXT, PIPELINE_STAGE_MEMBER,
    PIPELINE_STAGE_ASSEMBLY, PIPELINE_STAGE_VALIDATION,
    PIPELINE_STAGE_MULTI_ASSEMBLY, PIPELINE_STAGE_MULTI_VALIDATION,
)

# error_code for the two 7AA_* stages: an input-context gap in THIS pipeline's call, never an
# engineering finding about the connection itself.
PIPELINE_GAP_ERROR_CODE = "AUTOMATION_PIPELINE_INPUT_GAP"


@dataclass(frozen=True)
class AutomationValidationFailure:
    """
    One preserved validation failure: `stage` (which existing layer
    rejected the connection), `error_code` (the existing exception
    type's own name, or PIPELINE_GAP_ERROR_CODE), and `message` (the
    existing exception's text, verbatim — nothing reinterpreted).
    """
    stage: str
    error_code: str
    message: str


@dataclass(frozen=True)
class AutomationPipelineResult:
    """
    The complete outcome of one assembly -> validation -> 7Z run
    (two-member connections via 7O/7R; 3+-member connections via
    7AV's multi-member builder/validator — see
    multi_member_connection.py). The final automation decision lives
    ONLY in `automation_gate_result` (7Z's own result).
    `validation_passed` records what the existing validation path
    actually did. `validation_failure` is set exactly when validation
    did not pass. `reviewed_assembly` is present whenever the
    assembly layer succeeded (even if validation then rejected it —
    the assembly existed, the gate failed); `validation_result` only
    when the matching validation layer returned. `specification` is
    always the 7W-built specification. `catalogue_version` is the
    supplied section matcher's own declared catalogue version (the
    local catalogue's CATALOGUE_VERSION), or None when the matcher
    declares none (live/fake matchers) or none was supplied — it is
    provenance of which section catalogue the member geometry inputs
    were resolved from, recorded here so the 7AF drawing manifest can
    identify it. It is NEVER derived from the AI extraction evidence
    and never touches any evidence digest.
    """
    automation_gate_result: AutomationGateResult
    validation_passed: bool
    validation_failure: AutomationValidationFailure | None
    validation_result: ReviewedConnectionValidationResult | MultiMemberValidationResult | None
    reviewed_assembly: (
        ReviewedTwoMemberConnectionAssembly | ReviewedMultiMemberConnectionAssembly | None
    )
    specification: ReviewedConnectionSpecification
    catalogue_version: str | None = None


def _attempt(stage: str, function):
    """Runs one existing layer; returns (value, None) or (None, failure) with the existing error
    preserved verbatim. Only GeometryValidationError (the documented failure mode of every layer
    composed here) is captured — anything else propagates loudly."""
    try:
        return function(), None
    except GeometryValidationError as error:
        return None, AutomationValidationFailure(stage, type(error).__name__, str(error))


def evaluate_reviewed_connection_for_automation(
    package: ConnectionReviewPackage,
    *,
    member_rows: Mapping[str, Mapping[str, object]] | None = None,
    member_placements: Mapping[str, MemberPlacement] | None = None,
    section_matcher: object = None,
    require_confirmation: Sequence[str] = (),
) -> AutomationPipelineResult:
    """
    Runs the genuine existing validation path for one reviewed
    connection package and hands its outcome to 7Z:

      1. Build the 7V specification via 7W's own builder.
      2. Run the existing adapters unchanged: 7D (connection), 7J
         (location), 7N (attachments). Each rejection is preserved
         and the run concludes with validation_passed=False.
      3. State this pipeline's input contract: a connection names two
         members (7O) or three or more (7AV); the caller must supply a
         member row and a member placement for each connected mark,
         plus a section matcher. Gaps are preserved as
         PIPELINE_GAP_ERROR_CODE failures — never repaired by inference.
      4. Build the members via the existing 7A adapter,
         generate_geometry() and 7I place_member_geometry().
      5. Build the assembly and validate via the matching layers —
         two members: 7O then 7R (unchanged); three or more: 7AV's
         multi-member assembly and validation (multi_member_connection.py,
         the per-member generalization of the same layers). This is
         the one genuine source of validation evidence.
      6. Call 7Z exactly once with validation_passed=True only when
         the matching validation layer (7R or 7AV's) actually
         returned, else False; return its result inside
         AutomationPipelineResult. 7Z alone decides AUTO/CONFIRM/
         REVIEW.

    There is deliberately no `validation_passed` parameter on this
    function: the caller cannot invent or guess validation evidence.

    `member_rows` maps member mark -> the real steel_members row 7A
    consumes; `member_placements` maps member mark -> the explicit
    7I MemberPlacement; `section_matcher` is the caller's section
    matcher (the same one 7A takes). `require_confirmation` forwards
    unchanged to 7Z. Deterministic, read-only, no I/O.

    When the supplied matcher declares a `catalogue_version` attribute
    (the local section catalogue does), that value is recorded on the
    result's `catalogue_version` field — never invented, never read
    from anywhere else.
    """
    if not isinstance(package, ConnectionReviewPackage):
        raise TypeError(
            f"package must be a ConnectionReviewPackage (got {type(package).__name__}); "
            "the pipeline evaluates reviewed packages, never raw AI objects."
        )
    specification = build_reviewed_connection_specification(package)
    # Provenance: the supplied matcher's own declared catalogue version,
    # or None when the matcher declares none. Read from the matcher only —
    # a fake, a live Supabase matcher, or a missing matcher all yield None
    # honestly; the pipeline never substitutes a version of its own.
    catalogue_version = getattr(section_matcher, "catalogue_version", None)

    def conclude(passed, *, failure=None, assembly=None, validation_result=None):
        gate_result = evaluate_automation_gate(
            package, validation_passed=passed, require_confirmation=require_confirmation,
        )
        return AutomationPipelineResult(
            automation_gate_result=gate_result,
            validation_passed=passed,
            validation_failure=failure,
            validation_result=validation_result,
            reviewed_assembly=assembly,
            specification=specification,
            catalogue_version=catalogue_version,
        )

    # 2. Existing adapters, unchanged. Missing plate/hole/location data stays missing: the record
    # builder never fabricates a value for it — the adapter's own rejection is what runs.
    connection_record = {
        "connection_id": specification.connection_id,
        "connected_member_marks": list(specification.connected_member_marks),
        "position": specification.position,
        "plates": [specification.plate] if specification.plate is not None else [],
        "bolts": [{**specification.holes}] if specification.holes is not None else [],
        "source_page": specification.source_page,
        "source_drawing_id": specification.source_drawing_id,
    }
    validated_connection, failure = _attempt(
        PIPELINE_STAGE_CONNECTION, lambda: real_connection_to_validated_connection(connection_record),
    )
    if failure is not None:
        return conclude(False, failure=failure)

    location_record = (
        dict(specification.location)
        if specification.location is not None
        else {"connection_id": specification.connection_id}
    )
    location, failure = _attempt(
        PIPELINE_STAGE_LOCATION,
        lambda: real_connection_location_to_connection_location(location_record),
    )
    if failure is not None:
        return conclude(False, failure=failure)

    detail_record = {
        "connection_id": specification.connection_id,
        "review_status": specification.review_status,
        "attachments": list(specification.attachments),
    }
    attachments, failure = _attempt(
        PIPELINE_STAGE_ATTACHMENTS,
        lambda: reviewed_connection_detail_to_attachments(detail_record, validated_connection.connected_members),
    )
    if failure is not None:
        return conclude(False, failure=failure)

    # 3. This pipeline's input contract — stated, never worked around. Two connected members run
    # 7O (unchanged); three or more run 7AV's explicit multi-member path (multi_member_connection.py,
    # which generalizes 7O/7R's own checks per member); fewer than two can be assembled by neither,
    # and nothing is inferred, dropped or re-paired to make a connection fit a path.
    marks = list(validated_connection.connected_members)
    if len(marks) < 2:
        return conclude(False, failure=AutomationValidationFailure(
            PIPELINE_STAGE_SCOPE, PIPELINE_GAP_ERROR_CODE,
            f"The reviewed connection assembly layers compose two members (7O) or three or more "
            f"(7AV); this connection connects {len(marks)} ({marks!r}), so no assembly can be "
            "built. Nothing is inferred, dropped or re-paired to make it fit.",
        ))
    if section_matcher is None:
        return conclude(False, failure=AutomationValidationFailure(
            PIPELINE_STAGE_MEMBER_CONTEXT, PIPELINE_GAP_ERROR_CODE,
            "No section_matcher was supplied; member section data cannot be resolved without one.",
        ))
    rows = member_rows or {}
    placements = member_placements or {}

    # 4. The member pair via the existing 7A adapter + generate_geometry() + 7I placement.
    placed_members = []
    for mark in marks:
        row = rows.get(mark)
        if row is None:
            return conclude(False, failure=AutomationValidationFailure(
                PIPELINE_STAGE_MEMBER_CONTEXT, PIPELINE_GAP_ERROR_CODE,
                f"No member row was supplied for mark {mark!r} (member_rows did not include it); "
                "member geometry cannot be generated without the member's recorded data.",
            ))
        validated_member, failure = _attempt(
            PIPELINE_STAGE_MEMBER, lambda row=row: real_member_to_validated_member(row, section_matcher),
        )
        if failure is not None:
            return conclude(False, failure=failure)
        geometry, failure = _attempt(
            PIPELINE_STAGE_MEMBER, lambda member=validated_member: generate_geometry(member),
        )
        if failure is not None:
            return conclude(False, failure=failure)
        placement = placements.get(mark)
        if placement is None:
            return conclude(False, failure=AutomationValidationFailure(
                PIPELINE_STAGE_MEMBER_CONTEXT, PIPELINE_GAP_ERROR_CODE,
                f"No member placement was supplied for mark {mark!r} (member_placements did not include "
                "it); a member placement is never inferred from a connection or another member.",
            ))
        placed_geometry, failure = _attempt(
            PIPELINE_STAGE_MEMBER, lambda: place_member_geometry(geometry, placement),
        )
        if failure is not None:
            return conclude(False, failure=failure)
        placed_members.append(PlacedMember(mark=validated_member.mark, geometry=placed_geometry, placement=placement))

    # 5. The genuine validation path: exactly two members -> 7O then 7R (unchanged);
    # three or more -> 7AV's multi-member assembly + validation (the per-member generalization
    # of the same layers). 7N already guarantees the attachments exactly cover the connected
    # marks, so each per-mark match below exists.
    if len(marks) == 2:
        placed_a, placed_b = placed_members
        attachment_a = next(a for a in attachments if a.member_mark == placed_a.mark)
        attachment_b = next(a for a in attachments if a.member_mark == placed_b.mark)

        assembly, failure = _attempt(
            PIPELINE_STAGE_ASSEMBLY,
            lambda: build_reviewed_two_member_connection_assembly(
                placed_a, placed_b, validated_connection, location, attachment_a, attachment_b,
            ),
        )
        if failure is not None:
            return conclude(False, failure=failure)

        validation_result, failure = _attempt(
            PIPELINE_STAGE_VALIDATION, lambda: validate_reviewed_connection_assembly(assembly),
        )
    else:
        ordered_attachments = tuple(
            next(a for a in attachments if a.member_mark == member.mark)
            for member in placed_members
        )
        assembly, failure = _attempt(
            PIPELINE_STAGE_MULTI_ASSEMBLY,
            lambda: build_reviewed_multi_member_connection_assembly(
                tuple(placed_members), validated_connection, location, ordered_attachments,
            ),
        )
        if failure is not None:
            return conclude(False, failure=failure)

        validation_result, failure = _attempt(
            PIPELINE_STAGE_MULTI_VALIDATION,
            lambda: validate_multi_member_connection_assembly(assembly),
        )
    if failure is not None:
        return conclude(False, failure=failure, assembly=assembly)

    # 6. Genuine evidence into 7Z — the one and only source of validation_passed=True.
    return conclude(True, assembly=assembly, validation_result=validation_result)
