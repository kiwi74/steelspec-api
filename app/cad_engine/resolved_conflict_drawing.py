"""
Milestone E4 — RESOLVED CONFLICT -> FABRICATION DRAWING -> VERIFICATION:
an explicitly human-resolved catalogue conflict feeds the E3 selected
source geometry through the existing fabrication-output machinery and
produces a genuinely generated, genuinely verified fabrication drawing
artifact — and an unresolved conflict produces nothing.

Lifecycle proven here (additive — nothing is wired into the production
pipeline, matcher, DXF path, review UI, Supabase layer or any project
workflow):

    SOURCE CONFLICT -> 7AC -> 7AD human resolution -> 7Z re-decision
                                                          (E2)
        -> E3 RESOLVED GEOMETRY      resolved_conflict_geometry:
                                     selected source row + pinned matcher
        -> 7AA                       evaluate_reviewed_connection_for_automation()
                                     with the E3 source-pinned matcher and
                                     synthetic member rows built ONLY from
                                     the selected source evidence
        -> 7AE                       evaluate_fabrication_output_gate()
        -> 7AF                       dispatch_fabrication_drawing()
        -> 7AG                       verify_drawing_artifact()
        -> VERIFIED ARTIFACT         a real PDF on disk with a recorded
                                     SHA-256 and verification checks

Unresolved: SOURCE CONFLICT -> 7AC -> no human resolution -> 7Z blocked
-> no E3 geometry -> no 7AA run -> no 7AE permission -> no 7AF dispatch
-> no PDF -> no 7AG verification -> NO ARTIFACT. The entry raises while
the conflict is blocked, BEFORE any downstream call, so no stage can
even be invoked with authorised geometry.

THE NO-RELOOKUP RULE (unchanged from E3, now enforced across the whole
drawing path): every section dimension in the entire chain comes from
the selected source's own evidence on the conflict record, and the only
matcher anywhere in the chain is the E3 ResolvedSectionMatcher pinned
to that exact evidence. The existing drawing path requires a matcher
(7AA takes the same caller-supplied matcher 7A consumes) — E4 supplies
the E3 source-pinned matcher, so a live-authoritative decision whose
selected name also exists locally as a weight-only row can never fall
through to that row or any other ambient catalogue row. There is no
parameter and no path by which another matcher or catalogue could
enter.

HARD RULES:

  - THE E3 RESOLVED GEOMETRY IS THE ONLY GEOMETRY DOWNSTREAM. The two
    synthetic member rows are built with E3's own
    build_synthetic_member_row() helper (section dimensions only from
    the selected source evidence) and the pinned matcher is E3's own
    build_resolved_matcher() result. The pipeline's adapter, geometry
    generation, assembly, validation, drawing dispatch and drawing
    verification all consume those — never any re-lookup.
  - BLOCKED MEANS NO PATH AT ALL. While the re-decision has not
    applied one source — unresolved, un-re-decided or KEEP_BOTH — the
    entry raises before any 7AA/7AE/7AF/7AG call and nothing is
    written anywhere (no output directory, no PDF, no verification).
  - GENUINE STAGES ONLY. 7AA, 7AE, 7AF and 7AG are the existing
    implementations, called unchanged; every decision recorded on the
    result is the genuine stage's own value, never re-derived or
    injected here. If the genuine chain stops short of AUTO, the
    result records REVIEW/BLOCKED/NO_ARTIFACT truthfully.
  - SYNTHETIC DECISIONS ARE TEST INPUTS, NEVER REAL ENGINEERING
    DECISIONS. This module consumes a decision the E2 boundary
    recorded; it never records one, never upgrades provenance and
    never claims an actual engineer chose anything.
"""

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.cad_engine.automation_pipeline import (
    AutomationPipelineResult,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.catalogue_conflict_resolution import CatalogueConflict
from app.cad_engine.connection_review_package import (
    ConnectionReviewPackage,
    ConnectionReviewSupplement,
    ai_connection_to_extraction,
    create_review_package,
)
from app.cad_engine.drawing_dispatch import (
    DrawingDispatchResult,
    dispatch_fabrication_drawing,
)
from app.cad_engine.drawing_output_verification import (
    ArtifactVerificationResult,
    verify_drawing_artifact,
)
from app.cad_engine.fabrication_output_gate import (
    FabricationOutputGateResult,
    evaluate_fabrication_output_gate,
)
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.resolved_conflict_geometry import (
    ResolvedMemberGeometry,
    ResolvedSectionMatcher,
    build_resolved_matcher,
    build_synthetic_member_row,
    resolve_member_geometry,
    selected_source,
)
from app.cad_engine.reviewed_connection_specification import REVIEW_STATUS_APPROVED

__all__ = [
    "E4_MEMBER_MARK_A",
    "E4_MEMBER_MARK_B",
    "ResolvedConflictDrawingResult",
    "build_connection_identity",
    "produce_resolved_conflict_drawing",
]

# The two synthetic connected-member marks for every E4 connection.
# Identity labels only — every section dimension still comes from the
# conflict record's selected source evidence, never from these strings.
E4_MEMBER_MARK_A = "E4-M1"
E4_MEMBER_MARK_B = "E4-M2"


def _freeze_row(row: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """A detached, sorted tuple projection of one member row — the immutable
    shape the frozen E4 result carries (the live dicts exist only inside
    the production call)."""
    return tuple(sorted((key, copy.deepcopy(value)) for key, value in row.items()))


def build_connection_identity(conflict: CatalogueConflict) -> str:
    """
    The synthetic connection identity for one conflict, derived from
    the conflict record's own id — never from any catalogue, member or
    matcher. Raises for anything that is not a CatalogueConflict.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    return f"E4-CONN-{conflict.conflict_id}"


@dataclass(frozen=True)
class ResolvedConflictDrawingResult:
    """
    The complete E4 end-to-end record for one re-decided conflict: the
    conflict record itself (both original source rows, the conflicting
    fields, the E1 blocked verdict, the E2 human decision, the rationale
    and the 7Z re-decision all recoverable), the selected source, the
    E3 resolved member geometry, the synthetic reviewed connection
    package, the frozen member rows and placements the pipeline
    consumed, the source-pinned matcher, and the four genuine stage
    results — 7AA pipeline, 7AE gate, 7AF dispatch, 7AG verification —
    including the artifact identity and recorded SHA-256.
    """
    conflict: CatalogueConflict
    selected_source: str
    resolved_geometry: ResolvedMemberGeometry
    connection_id: str
    package: ConnectionReviewPackage
    member_rows: tuple[tuple[str, tuple[tuple[str, Any], ...]], ...]
    member_placements: tuple[tuple[str, MemberPlacement], ...]
    matcher: ResolvedSectionMatcher
    pipeline_result: AutomationPipelineResult
    gate_result: FabricationOutputGateResult
    dispatch_result: DrawingDispatchResult
    verification_result: ArtifactVerificationResult
    output_dir: Path


def produce_resolved_conflict_drawing(
    conflict: CatalogueConflict,
    *,
    output_dir: Path,
    position: str = "END",
    plate: Mapping[str, Any],
    holes: Mapping[str, Any],
    location_z: float,
    member_a_surface: str = "END",
    member_b_surface: str = "START",
    member_a_length_mm: float = 1000.0,
    member_b_length_mm: float = 1000.0,
    grade: str = "300",
) -> ResolvedConflictDrawingResult:
    """
    The E4 integration: an explicitly human-resolved conflict runs the
    genuine fabrication-output chain 7AA -> 7AE -> 7AF -> 7AG and the
    resulting drawing artifact is genuinely verified. While the
    conflict is blocked this raises before ANY downstream call.

    SOURCE AUTHORITY: both member rows are built with E3's
    build_synthetic_member_row() helper — every section dimension comes
    from the selected source's own evidence on the conflict record —
    and the only matcher in the entire path is the E3
    ResolvedSectionMatcher pinned to that same evidence, handed to 7AA
    as the caller-supplied section matcher. No ambient catalogue, no
    re-lookup and no other row can enter anywhere. 7AA, 7AE, 7AF and
    7AG are the existing implementations called unchanged, and every
    decision recorded is the genuine stage's own.

    `plate`, `holes`, `location_z`, the member lengths and the surfaces
    are connection-level SYNTHETIC reviewer values supplied by the
    caller (this module holds no engineering defaults for them): they
    describe the connection around the conflict's members, not the
    section geometry, and are deliberately never derived from the
    conflict record. Member B is placed so its start surface meets
    member A's end surface (member B z = member A length); the caller
    supplies the connection location z the 7J adapter consumes.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    if not isinstance(output_dir, Path):
        raise TypeError(
            f"output_dir must be a pathlib.Path (got {type(output_dir).__name__})."
        )

    # Blocked states raise HERE — before the E3 member path and before any
    # 7AA/7AE/7AF/7AG call, so nothing downstream can even be invoked.
    source = selected_source(conflict)
    resolved_geometry = resolve_member_geometry(
        conflict, mark=E4_MEMBER_MARK_A, length_mm=member_a_length_mm, grade=grade,
    )
    matcher = build_resolved_matcher(conflict)

    rows = {
        E4_MEMBER_MARK_A: build_synthetic_member_row(
            conflict, mark=E4_MEMBER_MARK_A, length_mm=member_a_length_mm, grade=grade,
        ),
        E4_MEMBER_MARK_B: build_synthetic_member_row(
            conflict, mark=E4_MEMBER_MARK_B, length_mm=member_b_length_mm, grade=grade,
        ),
    }
    placements = {
        E4_MEMBER_MARK_A: MemberPlacement(),
        E4_MEMBER_MARK_B: MemberPlacement(z=member_a_length_mm),
    }

    connection_id = build_connection_identity(conflict)
    extraction = ai_connection_to_extraction({})
    supplement = ConnectionReviewSupplement(
        connection_id=connection_id,
        review_status=REVIEW_STATUS_APPROVED,
        connected_member_marks=[E4_MEMBER_MARK_A, E4_MEMBER_MARK_B],
        position=position,
        plate=copy.deepcopy(dict(plate)),
        holes=copy.deepcopy(dict(holes)),
        location={
            "x": 0.0, "y": 0.0, "z": location_z,
            "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0,
        },
        attachments=[
            {"member_mark": E4_MEMBER_MARK_A, "surface_reference": member_a_surface},
            {"member_mark": E4_MEMBER_MARK_B, "surface_reference": member_b_surface},
        ],
    )
    package = create_review_package(
        extraction, supplement, known_member_marks=(E4_MEMBER_MARK_A, E4_MEMBER_MARK_B),
    )

    # The four genuine stages, unchanged, in their existing order.
    pipeline_result = evaluate_reviewed_connection_for_automation(
        package,
        member_rows=rows,
        member_placements=placements,
        section_matcher=matcher,
    )
    gate_result = evaluate_fabrication_output_gate(pipeline_result)
    dispatch_result = dispatch_fabrication_drawing(gate_result, pipeline_result, output_dir)
    verification_result = verify_drawing_artifact(
        dispatch_result, assembly=pipeline_result.reviewed_assembly,
    )

    return ResolvedConflictDrawingResult(
        conflict=conflict,
        selected_source=source,
        resolved_geometry=resolved_geometry,
        connection_id=connection_id,
        package=package,
        member_rows=(
            (E4_MEMBER_MARK_A, _freeze_row(rows[E4_MEMBER_MARK_A])),
            (E4_MEMBER_MARK_B, _freeze_row(rows[E4_MEMBER_MARK_B])),
        ),
        member_placements=(
            (E4_MEMBER_MARK_A, placements[E4_MEMBER_MARK_A]),
            (E4_MEMBER_MARK_B, placements[E4_MEMBER_MARK_B]),
        ),
        matcher=matcher,
        pipeline_result=pipeline_result,
        gate_result=gate_result,
        dispatch_result=dispatch_result,
        verification_result=verification_result,
        output_dir=output_dir,
    )
