"""
Milestone 7AI — REAL-WORLD EXCEPTION RESOLUTION PROOF.

Proves the real-world exception-resolution workflow on the REAL Arkles
Strand page-extraction capture, using ONLY the genuine existing stages:

    real Arkles page-extraction capture (never modified, never cleaned)
        -> 7Y  intake_page_extractions()
        -> 7AB evaluate_project_for_automation()          [no member context]
        -> 7AC build_exception_resolution_package()       [member context gives
                                                            authoritative choices]
        ->     human resolutions applied ONE TASK AT A TIME through
               apply_human_resolution() — the existing 7AC contract,
               never bypassed
        -> 7AD rerun_project_after_resolutions()          [WITH member context]
        ->     the genuine 7AA pipeline results are re-obtained from the
               7AD-rebuilt packages (these are the objects 7AE/7AF
               require; 7AD's decision remains the authority and the two
               MUST agree — a disagreement is recorded as a failure)
        -> 7AE evaluate_project_fabrication_output_gate()
        -> 7AF dispatch_project_fabrication_drawings()    [never the drawing
                                                            generator directly]
        -> 7AG verify_project_drawing_outputs()           [genuine assemblies
                                                            only — none are
                                                            invented]

The expected acceptance matrix is ONE candidate fully resolved by the
human reviewer — so exactly one connection re-decides AUTO, passes the
7AE gate as AUTO, has its fabrication drawing genuinely GENERATED and
its artifact genuinely VERIFIED — while every unresolved candidate
stays REVIEW through the rerun and the gate, is BLOCKED_REVIEW at
dispatch and produces NO_ARTIFACT. A mixed project is never globally
AUTO: the project-level gate decision stays REVIEW. `passed` is True
only when that matrix holds and no stage or consistency check failed.

Nothing here is an engineering engine and nothing is redesigned. The
caller supplies NO engineering values: only the raw capture, the member
context, the human resolutions and the identity of the one candidate
they resolved. There is NO parameter by which a caller can inject a
validation verdict, an automation decision, a fabrication-output
decision or a verification result — every recorded value comes from the
genuine stage results. The module hard-codes no scope numbers, no
blocker lists, no member marks, no human values: every such fact is
read from the actual stage results, and the module only checks
structural consistency (tests assert the known Arkles acceptance
values; this module never does).

HARD RULES:
  * The raw extraction is read, never modified: no value is silently
    "cleaned", converted or repaired. Nominal bolt sizes are AI
    readings, never hole diameters.
  * Human resolutions address ONLY the selected candidate; any
    resolution whose task does not belong to it is a programming error.
  * Connection identity is reviewer-supplied (7AC PROVIDE_CONNECTION_
    IDENTITY) — it is never derived from page data, member marks or
    list order.
  * No fabrication record is fabricated and no verification is run
    against a hand-built dispatch result.
  * A passing proof does NOT mean production-ready, Arkles-ready or
    engineering-approved. It means the human-exception workflow
    executed end to end and the records agree: genuine AI uncertainty
    became structured review tasks, explicit human resolutions were
    accepted, and the resolved connection then ran through the SAME
    deterministic production pipeline as an automatic job. Human
    review is exception clearing, not manual redrawing.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import evaluate_reviewed_connection_for_automation
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    ProjectDrawingDispatchResult,
    dispatch_project_fabrication_drawings,
)
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
    ProjectArtifactVerificationResult,
    verify_project_drawing_outputs,
)
from app.cad_engine.exception_resolution import (
    ExceptionResolutionPackage,
    HumanResolution,
    apply_human_resolution,
    build_exception_resolution_package,
)
from app.cad_engine.fabrication_output_gate import (
    ProjectFabricationOutputGateResult,
    evaluate_project_fabrication_output_gate,
)
from app.cad_engine.project_automation import (
    ProjectAutomationResult,
    evaluate_project_for_automation,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.resolution_rerun import (
    RefusedResolution,
    ResolutionRerunResult,
    rerun_project_after_resolutions,
)

PROOF_SCOPE_STATEMENT = (
    "The real-world exception-resolution proof: genuine Arkles uncertainty became "
    "structured review tasks; explicit human resolutions were accepted; the resolved "
    "connection ran the same deterministic production pipeline as an automatic job. "
    "Human review is exception clearing, not manual redrawing."
)


@dataclass(frozen=True)
class RealWorldConnectionOutcome:
    """
    One connection's complete record across the whole workflow, in the
    collection's own submission order.

    The AI-produced side (all values verbatim from the raw extraction —
    nothing here is cleaned, converted or invented): `source_identity`,
    `source_page`, `detail_reference`, `ai_member_references`,
    `ai_bolt_readings`, and the initial 7AB/7Z findings.

    The human-resolution side: `resolutions_applied` /
    `resolutions_refused` exactly as the genuine 7AD rerun recorded
    them (a refused resolution never reaches the rebuilt package).

    The rerun side: `rerun_decision`, `rerun_blocker_codes`,
    `validation_passed`, `specification_accepted`, `assembly_built` —
    the genuine 7AD/7AA signals.

    The production side: `gate_decision` (7AE), `output_status` (7AF),
    `verification_status` (7AG), and the artifact facts — `connection_id`
    and every artifact field are None unless the genuine stages actually
    produced them.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    source_page: object                       # the raw capture's page number, verbatim
    detail_reference: object                  # the AI's detail reference, verbatim
    ai_member_references: tuple[str, ...]     # the AI's member readings, verbatim
    ai_bolt_readings: tuple[str, ...]         # the AI's bolt readings, verbatim (repr)
    initial_decision: str                     # 7Z before any human resolution
    initial_blocker_codes: tuple[str, ...]
    resolutions_applied: tuple[HumanResolution, ...]
    resolutions_refused: tuple[RefusedResolution, ...]
    rerun_decision: str
    rerun_blocker_codes: tuple[str, ...]
    validation_passed: bool
    specification_accepted: bool
    assembly_built: bool
    gate_decision: str
    output_status: str
    verification_status: str
    connection_id: str | None
    artifact_path: str | None
    artifact_sha256: str | None
    artifact_size_bytes: int | None
    artifact_page_count: int | None


@dataclass(frozen=True)
class RealWorldExceptionProofResult:
    """
    The whole proof record. Every stage result is the genuine existing
    object, kept verbatim so nothing the stages produced is hidden:
    the initial 7AB result, the 7AC contract as built, the 7AC contract
    after the human resolutions were recorded, the genuine 7AD rerun,
    the 7AE project gate, the 7AF project dispatch and the 7AG project
    verification.

    `selected_package_id` is the ONE candidate the caller resolved.
    `passed` is True only when the acceptance matrix holds — exactly the
    selected connection re-decides AUTO and runs through the 7AE/7AF/7AG
    chain to a VERIFIED artifact, every other connection stays REVIEW
    end to end with NO_ARTIFACT, the mixed project is never globally
    AUTO, and no stage or consistency check failed.
    """
    initial_result: ProjectAutomationResult | None
    resolution_package: ExceptionResolutionPackage | None
    resolved_package: ExceptionResolutionPackage | None
    resolution_result: ResolutionRerunResult | None
    fabrication_result: ProjectFabricationOutputGateResult | None
    drawing_result: ProjectDrawingDispatchResult | None
    verification_result: ProjectArtifactVerificationResult | None
    connection_outcomes: tuple[RealWorldConnectionOutcome, ...]
    selected_package_id: str
    passed: bool
    summary: tuple[str, ...]


def _check(failures: list[str], label: str, ok: bool) -> None:
    """Records one acceptance check. Failures accumulate, never abort — the
    result must prove exactly where the workflow failed."""
    if not ok:
        failures.append(label)


def _failed_at(furthest_stage: str) -> str:
    return f"FAILED_AT_{furthest_stage}"


def run_real_world_exception_proof(
    *,
    arkles_pages: Sequence[dict],
    arkles_project_id: str,
    arkles_source_drawing_id: str,
    arkles_known_member_marks: Sequence[str] | None,
    arkles_drawing_set_page_count: int,
    member_rows: Mapping[str, Mapping[str, object]],
    member_placements: Mapping[str, Mapping[str, object]],
    section_matcher: object,
    human_resolutions: Sequence[HumanResolution],
    selected_package_id: str,
    output_dir: str | Path,
) -> RealWorldExceptionProofResult:
    """
    Runs the real-world exception-resolution workflow end to end through
    the genuine stages and returns the complete proof record.

    `human_resolutions` are the explicit human answers, applied one at a
    time through the existing 7AC apply (never bypassed); every one must
    address a task of the candidate `selected_package_id` — anything
    else is a programming error and raises. `member_rows` /
    `member_placements` / `section_matcher` are the member context the
    genuine 7AD rerun and the re-obtained 7AA pipelines use (the same
    inputs, so the two decisions are directly comparable). The initial
    7AB evaluation runs WITHOUT member context, exactly as the
    established Arkles evaluation does (none exists for the raw
    candidates — none is invented here).

    No parameter lets a caller inject a validation verdict, an
    automation decision, a fabrication-output decision or a
    verification result. Filesystem: the genuine 7AF project dispatch
    writes to `output_dir`, which is created only by the dispatch when
    it actually generates — never by this function.
    """
    # ---------------------------------------------------- loud caller errors
    if not isinstance(arkles_pages, (list, tuple)) or not all(
            isinstance(page, dict) for page in arkles_pages):
        raise TypeError("arkles_pages must be a sequence of page dicts.")
    if not isinstance(arkles_project_id, str):
        raise TypeError("arkles_project_id must be a str.")
    if not isinstance(arkles_source_drawing_id, str):
        raise TypeError("arkles_source_drawing_id must be a str.")
    if arkles_known_member_marks is not None and not isinstance(
            arkles_known_member_marks, (list, tuple)):
        raise TypeError("arkles_known_member_marks must be a sequence of str or None.")
    if not isinstance(arkles_drawing_set_page_count, int) or isinstance(
            arkles_drawing_set_page_count, bool):
        raise TypeError("arkles_drawing_set_page_count must be an int.")
    if not isinstance(member_rows, Mapping) or not isinstance(member_placements, Mapping):
        raise TypeError("member_rows and member_placements must be mappings.")
    if not isinstance(human_resolutions, (list, tuple)) or not all(
            isinstance(r, HumanResolution) for r in human_resolutions):
        raise TypeError("human_resolutions must be a sequence of HumanResolution.")
    if not isinstance(selected_package_id, str):
        raise TypeError("selected_package_id must be a str.")
    if not isinstance(output_dir, (str, Path)):
        raise TypeError("output_dir must be a str or Path.")

    failures: list[str] = []
    summary: list[str] = []
    out_dir = Path(output_dir)

    intake = None
    initial = None
    built = None
    resolved = None
    rerun = None
    pipelines: dict[str, object] = {}
    project_gate = None
    dispatch = None
    verification = None
    candidate_ids: tuple[str, ...] = ()

    # ------------------------------------------------------------------- 7Y
    try:
        intake = intake_page_extractions(
            list(arkles_pages),
            project_id=arkles_project_id,
            source_drawing_id=arkles_source_drawing_id,
            known_member_marks=arkles_known_member_marks,
            drawing_set_page_count=arkles_drawing_set_page_count,
        )
        summary.append("7Y: intake built")
    except Exception as exc:  # the proof records, it never repairs
        failures.append(f"STAGE_7Y: intake_page_extractions raised {type(exc).__name__}: {exc}")
        summary.append("STAGE_7Y: failed — see failures")

    # ------------------------------------------------------------------ 7AB
    if intake is not None:
        try:
            initial = evaluate_project_for_automation(intake.collection, intake=intake)
            summary.append("7AB: project evaluated")
        except Exception as exc:
            failures.append(f"STAGE_7AB: evaluate_project_for_automation raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AB: failed — see failures")

    if initial is not None and intake is not None:
        candidate_ids = tuple(candidate.review_package_id for candidate in intake.collection.candidates)
        _check(failures, "7AB: at least one connection was supplied",
               initial.connections_total > 0)
        _check(failures, "7AB: candidate count consistent with the collection",
               len(candidate_ids) == initial.connections_total)
        summary.append(f"7AB decisions: AUTO = {initial.auto_count}; "
                       f"CONFIRM = {initial.confirm_count}; REVIEW = {initial.review_count}")
        scope = initial.intake_scope
        summary.append(
            f"7Y scope: pages_received = {scope.pages_received}; "
            f"parse_failures = {len(scope.parse_failed_pages or ())}; "
            f"pages_not_analysed = {scope.pages_not_analysed}; "
            f"drawing_set_page_count = {scope.drawing_set_page_count}"
        )

    # ------------------------------------------------------------------ 7AC
    if initial is not None and intake is not None:
        try:
            built = build_exception_resolution_package(
                initial, intake.collection, member_rows=member_rows,
            )
            summary.append("7AC: exception-resolution package built")
        except Exception as exc:
            failures.append(f"STAGE_7AC: build_exception_resolution_package raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AC: failed — see failures")

    # Human resolutions — the existing 7AC contract, one answer at a time.
    # Every resolution must address the SELECTED candidate; anything else is a
    # programming error, not something the proof silently accepts.
    if built is not None:
        selected_group = next(
            (group for group in built.connection_tasks
             if group.review_package_id == selected_package_id),
            None,
        )
        if selected_group is None:
            raise ValueError(
                f"selected_package_id {selected_package_id!r} is not a candidate of this "
                f"collection (candidates: {candidate_ids}); no resolutions are applied."
            )
        selected_task_ids = {task.task_id for task in selected_group.tasks}
        for resolution in human_resolutions:
            if resolution.task_id not in selected_task_ids:
                raise ValueError(
                    f"human resolution {resolution.task_id!r} does not address a task of the "
                    f"selected candidate {selected_package_id}; only the selected candidate "
                    "may be resolved."
                )
        resolved = built
        for resolution in human_resolutions:
            resolved = apply_human_resolution(resolved, resolution)
        summary.append(f"7AC: {len(human_resolutions)} human resolution(s) recorded for "
                       f"{selected_package_id}")

    # ------------------------------------------------------------------ 7AD
    if resolved is not None and intake is not None:
        try:
            rerun = rerun_project_after_resolutions(
                resolved,
                intake.collection,
                member_rows=member_rows,
                member_placements=member_placements,
                section_matcher=section_matcher,
            )
            summary.append("7AD: resolution rerun completed")
        except Exception as exc:
            failures.append(f"STAGE_7AD: rerun_project_after_resolutions raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AD: failed — see failures")

    # Re-obtain the genuine 7AA pipeline results the 7AE/7AF stages require —
    # from the 7AD-rebuilt packages, with the SAME member context the rerun
    # used. 7AD's decision stays the authority: a pipeline whose 7Z decision
    # disagrees with the rerun outcome is a recorded failure, never silently
    # overwritten (this is re-obtaining objects, NOT a second decision).
    if rerun is not None:
        for outcome in rerun.outcomes:
            try:
                pipeline = evaluate_reviewed_connection_for_automation(
                    outcome.rebuilt_package,
                    member_rows=member_rows,
                    member_placements=member_placements,
                    section_matcher=section_matcher,
                )
                pipelines[outcome.review_package_id] = pipeline
                if pipeline.automation_gate_result.decision != outcome.decision:
                    failures.append(
                        f"7AD/7AA disagreement for {outcome.review_package_id}: the rerun "
                        f"decided {outcome.decision!r} but the re-obtained 7Z decision is "
                        f"{pipeline.automation_gate_result.decision!r}"
                    )
            except Exception as exc:
                failures.append(f"STAGE_7AA[{outcome.review_package_id}]: "
                                f"evaluate_reviewed_connection_for_automation raised "
                                f"{type(exc).__name__}: {exc}")
        _check(failures, "7AA: one re-obtained pipeline per rerun outcome",
               set(pipelines) == {o.review_package_id for o in rerun.outcomes})

    # ------------------------------------------------------------------ 7AE
    if initial is not None and rerun is not None and len(pipelines) == rerun.connections_total \
            and candidate_ids and set(pipelines) == set(candidate_ids):
        try:
            project_gate = evaluate_project_fabrication_output_gate(initial, pipelines)
            summary.append("7AE: project gate evaluated")
        except Exception as exc:
            failures.append(f"STAGE_7AE: evaluate_project_fabrication_output_gate raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AE: failed — see failures")

    if project_gate is not None:
        summary.append(f"7AE decision: {project_gate.package_decision}")
        summary.append(f"7AE eligible outputs: {project_gate.eligible_count}")
    elif initial is not None:
        failures.append("STAGE_7AF: cannot run — 7AE produced no project gate result")
        failures.append("STAGE_7AG: cannot run — 7AE produced no project gate result")

    # ------------------------------------------------------------------ 7AF
    if project_gate is not None:
        try:
            dispatch = dispatch_project_fabrication_drawings(project_gate, pipelines, out_dir)
            summary.append("7AF: project dispatch recorded")
        except Exception as exc:
            failures.append(f"STAGE_7AF: dispatch_project_fabrication_drawings raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AF: failed — see failures")

    if dispatch is not None:
        summary.append(f"7AF generated files: {dispatch.generated_count}")
        for outcome in dispatch.connection_outputs:
            result = outcome.dispatch_result
            summary.append(f"7AF {outcome.review_package_id}: {result.output_status}"
                           + (f" -> {[p.name for p in result.generated_files]}"
                              if result.generated_files else ""))
    elif project_gate is not None:
        failures.append("STAGE_7AG: cannot run — 7AF produced no dispatch result")

    # ------------------------------------------------------------------ 7AG
    # Only genuine assemblies are handed to the verifier — connections whose
    # pipeline built no assembly are verified for integrity only, exactly as
    # 7AG documents. None are invented here.
    if dispatch is not None:
        assemblies = {
            cid: pipeline.reviewed_assembly
            for cid, pipeline in pipelines.items()
            if getattr(pipeline, "reviewed_assembly", None) is not None
        }
        try:
            verification = verify_project_drawing_outputs(dispatch, assemblies=assemblies)
            summary.append("7AG: project verification recorded")
        except Exception as exc:
            failures.append(f"STAGE_7AG: verify_project_drawing_outputs raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AG: failed — see failures")

    if verification is not None:
        summary.append(f"7AG counts: verified = {verification.verified_count}; "
                       f"failed = {verification.failed_count}; blocked = {verification.blocked_count}")

    # ------------------------------------------------ per-connection records
    by_candidate = {c.review_package_id: c for c in intake.collection.candidates} if intake else {}
    gate_by_id = {o.review_package_id: o.gate_result for o in project_gate.connection_results} \
        if project_gate else {}
    dispatch_by_id = {o.review_package_id: o.dispatch_result
                      for o in dispatch.connection_outputs} if dispatch else {}
    verification_by_id = {o.review_package_id: o.verification_result
                          for o in verification.connection_results} if verification else {}

    outcomes: list[RealWorldConnectionOutcome] = []
    for cid in candidate_ids:
        group = next((g for g in built.connection_tasks if g.review_package_id == cid), None) \
            if built else None
        rerun_outcome = next((o for o in rerun.outcomes if o.review_package_id == cid), None) \
            if rerun else None
        gate_result = gate_by_id.get(cid)
        dispatch_result = dispatch_by_id.get(cid)
        verification_result = verification_by_id.get(cid)
        candidate = by_candidate.get(cid)

        outcomes.append(RealWorldConnectionOutcome(
            review_package_id=cid,
            submission_index=group.submission_index if group is not None else 0,
            source_identity=group.source_identity if group is not None else None,
            source_page=group.source_page if group is not None else None,
            detail_reference=group.detail_reference if group is not None else None,
            ai_member_references=(
                tuple(candidate.extraction.connected_member_references) if candidate else ()),
            ai_bolt_readings=(
                tuple(repr(bolt) for bolt in candidate.extraction.bolts) if candidate else ()),
            initial_decision=group.decision if group is not None else "",
            initial_blocker_codes=(
                tuple(b.code for b in group.blockers) if group is not None else ()),
            resolutions_applied=(
                rerun_outcome.resolutions_applied if rerun_outcome is not None else ()),
            resolutions_refused=(
                rerun_outcome.resolutions_refused if rerun_outcome is not None else ()),
            rerun_decision=rerun_outcome.decision if rerun_outcome is not None else "",
            rerun_blocker_codes=(
                tuple(b.code for b in rerun_outcome.blockers) if rerun_outcome is not None else ()),
            validation_passed=rerun_outcome.validation_passed if rerun_outcome is not None else False,
            specification_accepted=(
                rerun_outcome.specification_accepted if rerun_outcome is not None else False),
            assembly_built=rerun_outcome.assembly_built if rerun_outcome is not None else False,
            gate_decision=gate_result.decision if gate_result is not None else "",
            output_status=dispatch_result.output_status if dispatch_result is not None else "",
            verification_status=(
                verification_result.verification_status if verification_result is not None else ""),
            connection_id=dispatch_result.connection_id if dispatch_result is not None else None,
            artifact_path=(
                str(verification_result.artifact_path)
                if verification_result is not None and verification_result.artifact_path is not None
                else None),
            artifact_sha256=(
                verification_result.sha256 if verification_result is not None else None),
            artifact_size_bytes=(
                verification_result.file_size_bytes if verification_result is not None else None),
            artifact_page_count=(
                verification_result.page_count if verification_result is not None else None),
        ))

        # The AI-produced values stay visible, verbatim — nothing is converted.
        outcome = outcomes[-1]
        summary.append(
            f"{cid}: AI detail_reference = {outcome.detail_reference!r}; "
            f"members = {outcome.ai_member_references}; bolts = {outcome.ai_bolt_readings}"
        )
        summary.append(f"{cid}: initial 7Z = {outcome.initial_decision} "
                       f"({', '.join(outcome.initial_blocker_codes) or 'no blockers'})")
        summary.append(f"{cid}: resolutions applied = {len(outcome.resolutions_applied)}; "
                       f"refused = {len(outcome.resolutions_refused)}")
        summary.append(f"{cid}: rerun 7Z = {outcome.rerun_decision} "
                       f"({', '.join(outcome.rerun_blocker_codes) or 'no blockers'})")
        summary.append(f"{cid}: 7AE = {outcome.gate_decision}; 7AF = {outcome.output_status}; "
                       f"7AG = {outcome.verification_status}")
        if outcome.verification_status == VERIFICATION_STATUS_VERIFIED:
            summary.append(f"7AG artifact: {outcome.artifact_path}")
            summary.append(f"7AG file size: {outcome.artifact_size_bytes} bytes")
            summary.append(f"7AG SHA-256: {outcome.artifact_sha256}")
            summary.append(f"7AG page count: {outcome.artifact_page_count}")

    # ---------------------------------------------------- acceptance matrix
    matrix_failures: list[str] = []
    auto_ids = [o.review_package_id for o in outcomes
                if o.rerun_decision == AUTOMATION_DECISION_AUTO]
    if len(auto_ids) != 1:
        matrix_failures.append(
            f"acceptance matrix: expected exactly one AUTO rerun outcome "
            f"(found {len(auto_ids)}: {auto_ids})")
    elif auto_ids[0] != selected_package_id:
        matrix_failures.append(
            f"acceptance matrix: the AUTO outcome is {auto_ids[0]!r}, not the selected "
            f"{selected_package_id!r}")
    else:
        selected = next(o for o in outcomes if o.review_package_id == selected_package_id)
        _check(matrix_failures, "matrix: the selected connection passed the 7AE gate as AUTO",
               selected.gate_decision == AUTOMATION_DECISION_AUTO)
        _check(matrix_failures, "matrix: the selected connection's drawing was GENERATED",
               selected.output_status == OUTPUT_STATUS_GENERATED)
        _check(matrix_failures, "matrix: the selected connection's artifact was VERIFIED",
               selected.verification_status == VERIFICATION_STATUS_VERIFIED)
        _check(matrix_failures, "matrix: the verified artifact has identity, size, hash and pages",
               bool(selected.connection_id) and selected.artifact_path is not None
               and selected.artifact_size_bytes is not None and selected.artifact_size_bytes > 0
               and bool(selected.artifact_sha256)
               and selected.artifact_page_count is not None and selected.artifact_page_count > 0)
        _check(matrix_failures, "matrix: the selected connection applied no refused resolutions",
               selected.resolutions_refused == ())
    for outcome in outcomes:
        if outcome.review_package_id in auto_ids:
            continue
        _check(matrix_failures, f"matrix: unresolved {outcome.review_package_id} stayed REVIEW "
                                "through the rerun",
               outcome.rerun_decision == AUTOMATION_DECISION_REVIEW)
        _check(matrix_failures, f"matrix: unresolved {outcome.review_package_id} stayed REVIEW "
                                "through the 7AE gate",
               outcome.gate_decision == AUTOMATION_DECISION_REVIEW)
        _check(matrix_failures, f"matrix: unresolved {outcome.review_package_id} was "
                                "BLOCKED_REVIEW and produced no artifact",
               outcome.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
               and outcome.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
               and outcome.artifact_path is None)
    if project_gate is not None:
        _check(matrix_failures, "matrix: the mixed project is never globally AUTO",
               project_gate.package_decision == AUTOMATION_DECISION_REVIEW)

    failures.extend(matrix_failures)
    passed = not failures

    summary.append(f"acceptance matrix: {'PASSED' if not matrix_failures else 'NOT PASSED'}")
    summary.append(PROOF_SCOPE_STATEMENT)

    if rerun is None:
        summary.append("terminal stage: " + _failed_at(
            "7AD" if resolved is not None else "7AC" if built is not None
            else "7AB" if initial is not None else "7Y"))

    return RealWorldExceptionProofResult(
        initial_result=initial,
        resolution_package=built,
        resolved_package=resolved,
        resolution_result=rerun,
        fabrication_result=project_gate,
        drawing_result=dispatch,
        verification_result=verification,
        connection_outcomes=tuple(outcomes),
        selected_package_id=selected_package_id,
        passed=passed,
        summary=tuple(summary),
    )


__all__ = [
    "PROOF_SCOPE_STATEMENT",
    "RealWorldConnectionOutcome",
    "RealWorldExceptionProofResult",
    "run_real_world_exception_proof",
]
