"""
Milestone 7AH — END-TO-END PRODUCTION PROOF.

Runs the complete SteelSpec production loop twice, end to end, using ONLY
the genuine existing pipeline stages, and records exactly where either run
reached:

    Path A (AUTO):   a complete reviewed connection fixture with genuine
                     member context (rows, placements, section matcher)
                     -> 7AA evaluate_reviewed_connection_for_automation()
                     -> 7Z decision AUTO
                     -> 7AE evaluate_fabrication_output_gate()
                     -> 7AF dispatch_fabrication_drawing()
                     -> a real PDF on disk
                     -> 7AG verify_drawing_artifact() against the actual
                        reviewed assembly
                     -> expected outcome VERIFIED

    Path B (ARKLES): the REAL Arkles Strand page-extraction capture
                     -> 7Y intake_page_extractions()
                     -> 7X collection
                     -> 7AB evaluate_project_for_automation()
                     -> 7AA per candidate (no member context — none exists
                        for the raw Arkles candidates, none is invented)
                     -> 7Z REVIEW for every candidate
                     -> 7AE evaluate_project_fabrication_output_gate()
                     -> 7AF dispatch_project_fabrication_drawings()
                     -> zero files, no output directory
                     -> 7AG verify_project_drawing_outputs()
                     -> expected outcome NO_ARTIFACT

This module calls the genuine stages and only the genuine stages. It never
substitutes, re-runs, repairs or re-decides anything: no intermediate result
is set by hand, no decision or verification outcome can be injected by a
caller, the drawing generator is never called directly as a substitute for
7AF, and no verification is run against a fabricated dispatch record.
Failures are collected across all acceptance checks (not fail-fast) so the
result proves exactly where a path succeeded or failed.

The proof hard-codes no engineering values: no scope numbers, no blocker
lists, no member marks, no section names, no hole sizes. Every such fact is
read from the actual stage results; the module only checks structural
consistency (e.g. "the dispatch recorded at least one real non-empty PDF").
Tests assert the known Arkles acceptance values; this module never does.

IMPORTANT: a passing proof does NOT mean production-ready, Arkles-ready or
engineering-approved. It means the chain executed and the records agree.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import evaluate_reviewed_connection_for_automation
from app.cad_engine.connection_review_package import ConnectionReviewPackage
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    dispatch_fabrication_drawing,
    dispatch_project_fabrication_drawings,
)
from app.cad_engine.drawing_output_verification import (
    CHECK_DRAWING_CONTENT_PRESENT,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
    CHECK_IDENTITY_VERIFIABLE,
    CHECK_PASSED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
    verify_drawing_artifact,
    verify_project_drawing_outputs,
)
from app.cad_engine.fabrication_output_gate import (
    evaluate_fabrication_output_gate,
    evaluate_project_fabrication_output_gate,
)
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_extraction_intake import intake_page_extractions

PATH_NAME_AUTO = "AUTO"
PATH_NAME_ARKLES = "ARKLES"


@dataclass(frozen=True)
class EndToEndConnectionOutcome:
    """
    One connection's end-to-end record, in the path's processing order.
    Every field is taken verbatim from the genuine stage results; when a
    stage never ran (the chain failed earlier) its field is None.
    """
    review_package_id: str | None   # the 7X candidate id (Path B); None for a bare package (Path A)
    connection_id: str | None
    automation_decision: str | None  # the genuine 7Z decision
    output_status: str | None        # the genuine 7AF dispatch status
    verification_status: str | None  # the genuine 7AG status
    generated_files: tuple[str, ...]  # str() of the dispatch's generated files for this connection
    failures: tuple[str, ...]


@dataclass(frozen=True)
class EndToEndPathResult:
    """
    One path's complete proof record.

    `expected_outcome` is the path's defined acceptance terminal state
    (VERIFIED for the AUTO path, NO_ARTIFACT for the ARKLES path).
    `actual_outcome` is the terminal state actually reached: the genuine
    7AG status when verification ran, else FAILED_AT_<stage> for the
    furthest stage the path reached. `passed` is True only when every
    acceptance check for the path passed.

    The tuple fields are in processing order, aligned with
    `connection_outcomes`, and hold the genuine stage values verbatim —
    nothing here is invented. For Path A `review_package_id` is empty:
    a bare reviewed package has no 7X candidate id (the connection
    identity is carried in `connection_outcomes` and `summary`).
    """
    path_name: str
    expected_outcome: str
    actual_outcome: str
    passed: bool
    review_package_id: tuple[str, ...]
    automation_decision: tuple[str, ...]   # the genuine 7Z decisions, in order
    output_status: tuple[str, ...]         # the genuine 7AF dispatch statuses, in order
    verification_status: tuple[str, ...]   # the genuine 7AG statuses, in order
    connection_outcomes: tuple[EndToEndConnectionOutcome, ...]
    generated_files: tuple[str, ...]       # every file the path's dispatch genuinely generated
    failures: tuple[str, ...]
    summary: tuple[str, ...]


@dataclass(frozen=True)
class EndToEndProductionProofResult:
    """
    The whole proof. `passed` requires BOTH paths to pass their defined
    acceptance criteria. `generated_files` is the union of both paths'
    genuinely generated files.
    """
    auto_path: EndToEndPathResult
    arkles_path: EndToEndPathResult
    passed: bool
    generated_files: tuple[str, ...]
    summary: tuple[str, ...]


def _check(failures: list[str], label: str, ok: bool) -> None:
    """Records one acceptance check. Failures accumulate, never abort — the
    result must prove exactly where a path failed."""
    if not ok:
        failures.append(label)


def _failed_at(furthest_stage: str) -> str:
    return f"FAILED_AT_{furthest_stage}"


def _run_auto_path(
    *,
    output_dir: Path,
    auto_fixture: ConnectionReviewPackage,
    member_rows: Mapping[str, Mapping[str, object]],
    member_placements: Mapping[str, Mapping[str, object]],
    section_matcher: object,
) -> EndToEndPathResult:
    failures: list[str] = []
    summary: list[str] = []
    out_dir = output_dir / "auto"

    pipeline = None
    gate = None
    dispatch = None
    verification = None

    # ------------------------------------------------------------------ 7AA
    try:
        pipeline = evaluate_reviewed_connection_for_automation(
            auto_fixture,
            member_rows=member_rows,
            member_placements=member_placements,
            section_matcher=section_matcher,
        )
        summary.append("7AA: pipeline completed")
    except Exception as exc:  # the proof records, it never repairs
        failures.append(f"STAGE_7AA: evaluate_reviewed_connection_for_automation raised "
                        f"{type(exc).__name__}: {exc}")
        summary.append("STAGE_7AA: failed — see failures")

    if pipeline is not None:
        _check(failures, "7AA: validation_passed is True", pipeline.validation_passed is True)
        _check(failures, "7AA: validation_result exists", pipeline.validation_result is not None)
        _check(failures, "7AA: reviewed_assembly exists", pipeline.reviewed_assembly is not None)
        _check(failures, "7AA: specification exists", pipeline.specification is not None)
        gate_decision = pipeline.automation_gate_result.decision
        _check(failures, "7Z: decision is AUTO", gate_decision == AUTOMATION_DECISION_AUTO)
        summary.append(f"7Z decision: {gate_decision}")
    else:
        failures.append("STAGE_7AE: cannot run — 7AA produced no pipeline result")
        failures.append("STAGE_7AF: cannot run — 7AA produced no pipeline result")
        failures.append("STAGE_7AG: cannot run — 7AA produced no pipeline result")

    # ------------------------------------------------------------------ 7AE
    if pipeline is not None:
        try:
            gate = evaluate_fabrication_output_gate(pipeline)
            summary.append("7AE: gate evaluated")
        except Exception as exc:
            failures.append(f"STAGE_7AE: evaluate_fabrication_output_gate raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AE: failed — see failures")

    if gate is not None:
        _check(failures, "7AE: decision is AUTO", gate.decision == AUTOMATION_DECISION_AUTO)
        _check(failures, "7AE: no blockers", gate.blockers == ())
        summary.append(f"7AE decision: {gate.decision}")
    elif pipeline is not None:
        failures.append("STAGE_7AF: cannot run — 7AE produced no gate result")
        failures.append("STAGE_7AG: cannot run — 7AE produced no gate result")

    # ------------------------------------------------------------------ 7AF
    if gate is not None and pipeline is not None:
        try:
            dispatch = dispatch_fabrication_drawing(gate, pipeline, out_dir)
            summary.append("7AF: dispatch recorded")
        except Exception as exc:
            failures.append(f"STAGE_7AF: dispatch_fabrication_drawing raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AF: failed — see failures")

    if dispatch is not None:
        _check(failures, "7AF: output_status is GENERATED",
               dispatch.output_status == OUTPUT_STATUS_GENERATED)
        existing_files = [p for p in dispatch.generated_files if p.is_file() and p.stat().st_size > 0]
        if not existing_files:
            failures.append("7AF: no generated file exists as a regular file with size > 0")
        for path in dispatch.generated_files:
            if not path.exists():
                failures.append(f"7AF: recorded generated file does not exist: {path}")
            elif not path.is_file():
                failures.append(f"7AF: recorded generated file is not a regular file: {path}")
            elif path.stat().st_size == 0:
                failures.append(f"7AF: recorded generated file is empty: {path}")
        summary.append(f"7AF output status: {dispatch.output_status}")
        summary.append(f"7AF generated files: {len(dispatch.generated_files)} -> "
                       f"{[p.name for p in dispatch.generated_files]}")
    elif pipeline is not None:
        failures.append("STAGE_7AG: cannot run — 7AF produced no dispatch result")

    # ------------------------------------------------------------------ 7AG
    if dispatch is not None and pipeline is not None:
        try:
            verification = verify_drawing_artifact(dispatch, assembly=pipeline.reviewed_assembly)
            summary.append("7AG: verification recorded")
        except Exception as exc:
            failures.append(f"STAGE_7AG: verify_drawing_artifact raised {type(exc).__name__}: {exc}")
            summary.append("STAGE_7AG: failed — see failures")

    if verification is not None:
        _check(failures, "7AG: verification_status is VERIFIED",
               verification.verification_status == VERIFICATION_STATUS_VERIFIED)
        _check(failures, "7AG: artifact path recorded", verification.artifact_path is not None)
        _check(failures, "7AG: SHA-256 recorded", verification.sha256 is not None)
        _check(failures, "7AG: page count recorded and valid",
               verification.page_count is not None and verification.page_count > 0)
        check_statuses = {check.code: check.status for check in verification.checks}
        _check(failures, "7AG: drawing content present",
               check_statuses.get(CHECK_DRAWING_CONTENT_PRESENT) == CHECK_PASSED)
        _check(failures, "7AG: identity verifiable against the reviewed assembly",
               check_statuses.get(CHECK_IDENTITY_VERIFIABLE) == CHECK_PASSED)
        _check(failures, "7AG: geometry fields verifiable",
               check_statuses.get(CHECK_GEOMETRY_FIELDS_VERIFIABLE) == CHECK_PASSED)
        summary.append(f"7AG verification status: {verification.verification_status}")
        summary.append(f"7AG artifact: {verification.artifact_path}")
        summary.append(f"7AG file size: {verification.file_size_bytes} bytes")
        summary.append(f"7AG SHA-256: {verification.sha256}")
        summary.append(f"7AG page count: {verification.page_count}")
        for check in verification.checks:
            summary.append(f"7AG {check.code}: {check.status}")

    connection_id = None
    if dispatch is not None:
        connection_id = dispatch.connection_id
    elif pipeline is not None and pipeline.specification is not None:
        connection_id = pipeline.specification.connection_id

    if verification is not None:
        actual_outcome = verification.verification_status
    else:
        furthest = "7AF" if dispatch is not None else "7AE" if gate is not None else "7AA"
        actual_outcome = _failed_at(furthest)

    expected_outcome = VERIFICATION_STATUS_VERIFIED
    summary.append(f"expected outcome: {expected_outcome} / actual outcome: {actual_outcome}")

    outcome = EndToEndConnectionOutcome(
        review_package_id=None,  # Path A supplies a bare reviewed package, not a 7X candidate
        connection_id=connection_id,
        automation_decision=pipeline.automation_gate_result.decision if pipeline is not None else None,
        output_status=dispatch.output_status if dispatch is not None else None,
        verification_status=verification.verification_status if verification is not None else None,
        generated_files=tuple(str(p) for p in dispatch.generated_files) if dispatch is not None else (),
        failures=tuple(failures),
    )
    return EndToEndPathResult(
        path_name=PATH_NAME_AUTO,
        expected_outcome=expected_outcome,
        actual_outcome=actual_outcome,
        passed=not failures,
        review_package_id=(),
        automation_decision=(outcome.automation_decision,) if outcome.automation_decision else (),
        output_status=(outcome.output_status,) if outcome.output_status else (),
        verification_status=(outcome.verification_status,) if outcome.verification_status else (),
        connection_outcomes=(outcome,),
        generated_files=outcome.generated_files,
        failures=tuple(failures),
        summary=tuple(summary),
    )


def _run_arkles_path(
    *,
    output_dir: Path,
    arkles_pages: Sequence[dict],
    arkles_project_id: str,
    arkles_source_drawing_id: str,
    arkles_known_member_marks: Sequence[str] | None,
    arkles_drawing_set_page_count: int,
) -> EndToEndPathResult:
    failures: list[str] = []
    summary: list[str] = []
    out_dir = output_dir / "arkles"

    intake = None
    project_result = None
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
    except Exception as exc:
        failures.append(f"STAGE_7Y: intake_page_extractions raised {type(exc).__name__}: {exc}")
        summary.append("STAGE_7Y: failed — see failures")

    # ------------------------------------------------------------------ 7AB
    if intake is not None:
        try:
            project_result = evaluate_project_for_automation(intake.collection, intake=intake)
            summary.append("7AB: project evaluated")
        except Exception as exc:
            failures.append(f"STAGE_7AB: evaluate_project_for_automation raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AB: failed — see failures")

    if project_result is not None and intake is not None:
        candidate_ids = tuple(candidate.review_package_id for candidate in intake.collection.candidates)
        _check(failures, "7AB: at least one connection was supplied",
               project_result.connections_total > 0)
        _check(failures, "7AB: candidate count consistent with the collection",
               len(candidate_ids) == project_result.connections_total)
        _check(failures, "7AB: zero AUTO", project_result.auto_count == 0)
        _check(failures, "7AB: zero CONFIRM", project_result.confirm_count == 0)
        _check(failures, "7AB: every candidate REVIEW",
               project_result.review_count == project_result.connections_total)
        summary.append(f"7AB decisions: AUTO = {project_result.auto_count}; "
                       f"CONFIRM = {project_result.confirm_count}; REVIEW = {project_result.review_count}")
        scope = project_result.intake_scope
        summary.append(
            f"7Y scope: pages_received = {scope.pages_received}; "
            f"parse_failures = {len(scope.parse_failed_pages or ())}; "
            f"pages_not_analysed = {scope.pages_not_analysed}; "
            f"drawing_set_page_count = {scope.drawing_set_page_count}"
        )

        # -------------------------------------------------------------- 7AA
        # One genuine pipeline per candidate, exactly as 7AB ran them: no member
        # context exists for the raw Arkles candidates — none is invented here.
        for candidate in intake.collection.candidates:
            try:
                pipelines[candidate.review_package_id] = evaluate_reviewed_connection_for_automation(
                    candidate.package)
            except Exception as exc:
                failures.append(f"STAGE_7AA[{candidate.review_package_id}]: "
                                f"evaluate_reviewed_connection_for_automation raised "
                                f"{type(exc).__name__}: {exc}")
        _check(failures, "7AA: one pipeline result per candidate",
               set(pipelines) == set(candidate_ids))
        if len(pipelines) == len(candidate_ids):
            _check(failures, "7Z: every candidate decision REVIEW",
                   all(pipelines[cid].automation_gate_result.decision == AUTOMATION_DECISION_REVIEW
                       for cid in candidate_ids))
            for cid in candidate_ids:
                summary.append(f"7Z decision {cid}: {pipelines[cid].automation_gate_result.decision}")

    # ------------------------------------------------------------------ 7AE
    if project_result is not None and len(pipelines) == len(candidate_ids) and candidate_ids:
        try:
            project_gate = evaluate_project_fabrication_output_gate(project_result, pipelines)
            summary.append("7AE: project gate evaluated")
        except Exception as exc:
            failures.append(f"STAGE_7AE: evaluate_project_fabrication_output_gate raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AE: failed — see failures")

    if project_gate is not None:
        _check(failures, "7AE: zero automatic outputs", project_gate.eligible_count == 0)
        _check(failures, "7AE: package decision is REVIEW",
               project_gate.package_decision == AUTOMATION_DECISION_REVIEW)
        _check(failures, "7AE: every candidate blocked", project_gate.blocked_outputs == candidate_ids)
        summary.append(f"7AE decision: {project_gate.package_decision}")
        summary.append(f"7AE eligible outputs: {project_gate.eligible_count}")
        for code, count in project_gate.blockers_by_code:
            summary.append(f"7AE blocker: {code} x {count}")
    elif project_result is not None:
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
        _check(failures, "7AF: zero generated files", dispatch.generated_count == 0)
        output_statuses = tuple(o.dispatch_result.output_status for o in dispatch.connection_outputs)
        _check(failures, "7AF: every dispatch blocked as REVIEW",
               len(output_statuses) == project_result.connections_total
               and all(status == OUTPUT_STATUS_BLOCKED_REVIEW for status in output_statuses))
        _check(failures, "7AF: blocked dispatch created no output directory", not out_dir.exists())
        summary.append(f"7AF output statuses: {output_statuses}")
        summary.append(f"7AF generated files: {dispatch.generated_count}")

    # ------------------------------------------------------------------ 7AG
    if dispatch is not None:
        try:
            verification = verify_project_drawing_outputs(dispatch)
            summary.append("7AG: project verification recorded")
        except Exception as exc:
            failures.append(f"STAGE_7AG: verify_project_drawing_outputs raised "
                            f"{type(exc).__name__}: {exc}")
            summary.append("STAGE_7AG: failed — see failures")

    if verification is not None:
        _check(failures, "7AG: every connection NO_ARTIFACT",
               verification.connections_total > 0
               and verification.blocked_count == verification.connections_total
               and all(outcome.verification_result.verification_status
                       == VERIFICATION_STATUS_NO_ARTIFACT
                       for outcome in verification.connection_results))
        summary.append("7AG statuses: "
                       + repr(tuple(o.verification_result.verification_status
                                   for o in verification.connection_results)))
        summary.append(f"7AG counts: verified = {verification.verified_count}; "
                       f"failed = {verification.failed_count}; blocked = {verification.blocked_count}")

    if verification is not None:
        statuses = tuple(o.verification_result.verification_status
                         for o in verification.connection_results)
        actual_outcome = statuses[0] if statuses and len(set(statuses)) == 1 else \
            " / ".join(dict.fromkeys(statuses)) if statuses else VERIFICATION_STATUS_NO_ARTIFACT
    else:
        furthest = ("7AF" if dispatch is not None
                    else "7AE" if project_gate is not None
                    else "7AA" if pipelines
                    else "7AB" if project_result is not None
                    else "7Y")
        actual_outcome = _failed_at(furthest)

    expected_outcome = VERIFICATION_STATUS_NO_ARTIFACT
    summary.append(f"expected outcome: {expected_outcome} / actual outcome: {actual_outcome}")

    by_package_id = {o.review_package_id: o for o in dispatch.connection_outputs} if dispatch else {}
    verification_by_id = {o.review_package_id: o.verification_result
                          for o in verification.connection_results} if verification else {}
    outcomes = []
    for cid in candidate_ids:
        dispatch_outcome = by_package_id.get(cid)
        verification_result = verification_by_id.get(cid)
        outcomes.append(EndToEndConnectionOutcome(
            review_package_id=cid,
            connection_id=dispatch_outcome.dispatch_result.connection_id if dispatch_outcome else None,
            automation_decision=(
                pipelines[cid].automation_gate_result.decision if cid in pipelines else None),
            output_status=dispatch_outcome.dispatch_result.output_status if dispatch_outcome else None,
            verification_status=(
                verification_result.verification_status if verification_result else None),
            generated_files=(),
            failures=(),
        ))

    return EndToEndPathResult(
        path_name=PATH_NAME_ARKLES,
        expected_outcome=expected_outcome,
        actual_outcome=actual_outcome,
        passed=not failures,
        review_package_id=candidate_ids,
        automation_decision=tuple(
            pipelines[cid].automation_gate_result.decision if cid in pipelines else None
            for cid in candidate_ids),
        output_status=tuple(
            by_package_id[cid].dispatch_result.output_status if cid in by_package_id else None
            for cid in candidate_ids),
        verification_status=tuple(
            o.verification_status for o in outcomes),
        connection_outcomes=tuple(outcomes),
        generated_files=(),
        failures=tuple(failures),
        summary=tuple(summary),
    )


def run_end_to_end_production_proof(
    *,
    output_dir: str | Path,
    auto_fixture: ConnectionReviewPackage,
    arkles_pages: Sequence[dict],
    arkles_project_id: str,
    arkles_source_drawing_id: str,
    arkles_known_member_marks: Sequence[str] | None,
    arkles_drawing_set_page_count: int,
    member_rows: Mapping[str, Mapping[str, object]],
    member_placements: Mapping[str, Mapping[str, object]],
    section_matcher: object,
) -> EndToEndProductionProofResult:
    """
    Runs BOTH paths end to end through the genuine production stages and
    returns the complete proof record. Neither path is optional; a caller
    cannot skip one. No parameter lets a caller inject a validation
    verdict, an automation decision, a fabrication-output decision or a
    verification result — every recorded value comes from the genuine
    stage results.

    Path A uses `auto_fixture` with the supplied member context
    (member_rows / member_placements / section_matcher — the genuine
    7O/7R inputs). Path B uses the raw page-extraction capture; it runs
    7AA per candidate WITHOUT member context, exactly as the established
    Arkles evaluation does (none exists for the raw candidates).

    Filesystem: each path's dispatch output goes to its own subdirectory
    of `output_dir` ("auto" / "arkles"), created only by the genuine 7AF
    dispatch when it actually generates — never by this function.
    """
    if not isinstance(output_dir, (str, Path)):
        raise TypeError("output_dir must be a str or Path.")
    if not isinstance(auto_fixture, ConnectionReviewPackage):
        raise TypeError(f"auto_fixture must be a ConnectionReviewPackage "
                        f"(got {type(auto_fixture).__name__}).")
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

    out_dir = Path(output_dir)
    auto_path = _run_auto_path(
        output_dir=out_dir,
        auto_fixture=auto_fixture,
        member_rows=member_rows,
        member_placements=member_placements,
        section_matcher=section_matcher,
    )
    arkles_path = _run_arkles_path(
        output_dir=out_dir,
        arkles_pages=arkles_pages,
        arkles_project_id=arkles_project_id,
        arkles_source_drawing_id=arkles_source_drawing_id,
        arkles_known_member_marks=arkles_known_member_marks,
        arkles_drawing_set_page_count=arkles_drawing_set_page_count,
    )
    return EndToEndProductionProofResult(
        auto_path=auto_path,
        arkles_path=arkles_path,
        passed=auto_path.passed and arkles_path.passed,
        generated_files=auto_path.generated_files + arkles_path.generated_files,
        summary=(
            f"AUTO path: {'PASSED' if auto_path.passed else 'NOT PASSED'} "
            f"(expected {auto_path.expected_outcome!r}, actual {auto_path.actual_outcome!r})",
            f"ARKLES path: {'PASSED' if arkles_path.passed else 'NOT PASSED'} "
            f"(expected {arkles_path.expected_outcome!r}, actual {arkles_path.actual_outcome!r})",
            f"generated files: {len(auto_path.generated_files) + len(arkles_path.generated_files)}",
            "Passing proves the chain executed and the records agree — NOT that any project is "
            "production-ready, Arkles-ready or engineering-approved.",
        ),
    )


__all__ = [
    "PATH_NAME_AUTO",
    "PATH_NAME_ARKLES",
    "EndToEndConnectionOutcome",
    "EndToEndPathResult",
    "EndToEndProductionProofResult",
    "run_end_to_end_production_proof",
]
