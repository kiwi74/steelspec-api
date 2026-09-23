"""
Milestone 7AJ — PROJECT WORKFLOW & INCREMENTAL EXCEPTION PROOF.

The workflow layer that sits ABOVE the existing connection-level chain
and maintains the truthful project/job state a SteelSpec dashboard
would show. It decides NOTHING and reruns only what changed:

    7Y  intake (real extraction, never modified)
        -> 7AB evaluate_project_for_automation()      [no member context]
        -> 7AC build_exception_resolution_package()   [member context gives
                                                       authoritative choices]
        ->     human resolutions recorded ONE TASK AT A TIME through
               apply_human_resolution() — the existing contract
        -> 7AD rerun_connection_after_resolutions()   [the connection-level
                                                       rerun: ONLY the
                                                       resolved connection]
        ->     the genuine 7AA pipeline object is re-obtained from the
               7AD-rebuilt package with the SAME member context (7AD's
               decision stays the authority; the two MUST agree)
        -> 7AE evaluate_project_fabrication_output_gate() over the
               composite of every connection's CURRENT pipeline — so the
               project-level decision stays truthful (a mixed project is
               never globally AUTO)
        -> 7AF the genuine per-connection dispatch entry, fed the
               composite gate's genuine per-connection outcome for the
               connection that changed — nothing else is re-dispatched
        -> 7AG the genuine per-connection verification entry, fed the
               dispatch manifest and the genuine reviewed assembly

WHY THE PROJECT-LEVEL DISPATCH/VERIFICATION LOOPS ARE NOT USED: the
dispatch module documents that dispatching a connection again writes
the same path and REPLACES the earlier file. Incremental processing
requires generating only the newly eligible connection, so this module
executes the per-connection entries (which the project-level loops
themselves call) for the changed connection alone. Unaffected
connections keep their stored records — never regenerated, never
re-verified, never rebuilt.

THE REVISION MODEL: every workflow state carries a monotonically
increasing integer revision (never a timestamp). Starting a project is
revision 0; each processed resolution advances it by one. The revision
proves what changed: only the resolved connection's
`last_processed_revision` moves; every other connection keeps its
previous state object untouched. Optimistic concurrency: a caller that
holds an older revision (`expected_revision`) is REFUSED — a stale
human decision is never applied to a newer project state.

HARD RULES:

  - THE ENGINEERING TRUTH STAYS DOWNSTREAM. This module calls only the
    existing public entries (7AB/7AC/7AD/7AA/7AE/7AF/7AG) and stores
    their genuine frozen results. It never calls the drawing generator,
    never calls 7Z, never fabricates a gate/dispatch/verification
    record, never invents a decision vocabulary, and has no parameter
    by which a caller can inject a decision, a validation verdict, an
    output status or a verification result.
  - ONE CONNECTION AT A TIME, EACH AT MOST ONCE. A resolve addresses
    exactly one package; resolutions for any other package (or any
    other project) are REFUSED loudly; a connection already processed
    is REFUSED loudly — no duplicate production, no downgrade, no
    silent re-processing.
  - FAILURES ARE ATOMIC AND HONEST. Validation errors (stale revision,
    unknown package, foreign resolutions, payload shape) raise BEFORE
    anything runs: no state change, no files, no revision advance.
    Generation and verification failures flow through the genuine 7AF/
    7AG result semantics and are recorded as what they are — never as
    success, never hidden, and never repaired.
  - UNRESOLVED CONNECTIONS STAY EXACTLY AS THEY WERE. Their visible
    state objects are carried through untouched (same instance), their
    records are never replaced, and nothing downstream ever sees a
    rebuilt version of them.
  - DETERMINISTIC, READ-ONLY, FROZEN. Every state is an immutable
    frozen dataclass of plain recorded data; inputs are never mutated;
    two runs of the same inputs produce equal projections. No network,
    no AI model, no database, no environment variables, no UI.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import (
    AutomationPipelineResult,
    evaluate_reviewed_connection_for_automation,
)
from app.cad_engine.drawing_dispatch import (
    DrawingDispatchResult,
    dispatch_fabrication_drawing,
)
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
    ArtifactVerificationResult,
    verify_drawing_artifact,
)
from app.cad_engine.exception_resolution import (
    ExceptionResolutionPackage,
    HumanResolution,
    apply_human_resolution,
    build_exception_resolution_package,
)
from app.cad_engine.fabrication_output_gate import (
    FabricationOutputGateResult,
    evaluate_project_fabrication_output_gate,
)
from app.cad_engine.project_automation import (
    ProjectAutomationResult,
    evaluate_project_for_automation,
)
from app.cad_engine.project_connection_review import (
    ProjectConnectionReviewCollection,
    require_submission_identities,
)
from app.cad_engine.project_extraction_intake import ProjectExtractionIntake
from app.cad_engine.resolution_rerun import (
    ConnectionRerunOutcome,
    rerun_connection_after_resolutions,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    ReviewedConnectionSpecification,
)

__all__ = [
    "PROJECT_WORKFLOW_SCOPE_STATEMENT",
    "ProjectWorkflowError", "StaleProjectWorkflowError",
    "UnknownProjectPackageError", "CrossProjectResolutionError",
    "ConnectionAlreadyProcessedError", "WorkflowStageFailureError",
    "ProjectConnectionState", "ProjectConnectionRecord", "ProjectWorkflowState",
    "start_project_workflow", "resolve_project_connection", "refresh_project_workflow",
]

PROJECT_WORKFLOW_SCOPE_STATEMENT = (
    "This state is SteelSpec's project view: where each connection stands, what changed, what was "
    "generated, and what still needs human attention. It decides nothing — every decision, output "
    "and verification recorded here came from the existing automation, output-gate, dispatch and "
    "verification stages. Human review is exception clearing, not manual redrawing."
)


class ProjectWorkflowError(ValueError):
    """Workflow-orchestration error: raised before any processing or file output, so the
    prior immutable state stays exactly as it was."""


class StaleProjectWorkflowError(ProjectWorkflowError):
    """The caller holds an older revision than the workflow's current one."""


class UnknownProjectPackageError(ProjectWorkflowError):
    """The package id is not a connection of this project."""


class CrossProjectResolutionError(ProjectWorkflowError):
    """A resolution addresses tasks of another connection or another project."""


class ConnectionAlreadyProcessedError(ProjectWorkflowError):
    """The connection was already resolved/processed; a connection is processed at most once."""


class WorkflowStageFailureError(ProjectWorkflowError):
    """Two genuine stage results disagree; the workflow never proceeds on contradictory evidence."""


@dataclass(frozen=True)
class ProjectConnectionState:
    """
    The visible projection of one connection: the latest genuine
    decision (the initial 7AB/7Z decision, or the 7AD rerun's decision
    once processed), its output and verification statuses (None until
    the connection was actually dispatched/verified), the artifacts it
    produced, its latest blocker/warning codes, and the workflow
    revision at which it was processed (None = never processed — its
    state object has been carried through untouched since revision 0).
    """
    package_id: str
    connection_id: str | None
    decision: str
    output_status: str | None
    verification_status: str | None
    generated_files: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    last_processed_revision: int | None


@dataclass(frozen=True)
class ProjectConnectionRecord:
    """
    The preserved genuine stage evidence behind one connection's
    projection: the current 7AA pipeline object (the initial one, or
    the one re-obtained from the 7AD-rebuilt package after a resolve),
    the 7AD rerun outcome, and — once the connection was processed —
    the 7AE gate result the dispatch consumed, the 7AF dispatch
    manifest and the 7AG verification manifest. Unaffected connections
    keep the SAME record objects across revisions: these are evidence,
    never recomputed. Excluded from state equality so the visible
    projection compares cleanly (records contain artifact paths).
    """
    pipeline: AutomationPipelineResult | None
    rerun_outcome: ConnectionRerunOutcome | None
    gate_result: FabricationOutputGateResult | None
    dispatch_result: DrawingDispatchResult | None
    verification_result: ArtifactVerificationResult | None


@dataclass(frozen=True)
class ProjectWorkflowState:
    """
    The immutable project workflow state at one revision. The counts
    are computed from `connections` (never asserted);
    `project_decision` is the composite fabrication-output gate's own
    package decision over every connection's CURRENT pipeline — the
    existing project semantics, never a new vocabulary;
    `generated_files` is the ordered union of the per-connection
    artifacts; `blocked_count` is the number of connections currently
    blocked from automatic fabrication output (REVIEW + CONFIRM).
    """
    project_id: str | None
    revision: int
    connections: tuple[ProjectConnectionState, ...]
    project_decision: str
    generated_files: tuple[str, ...]
    review_count: int
    confirm_count: int
    auto_count: int
    verified_count: int
    blocked_count: int
    summary: str
    connection_records: tuple[ProjectConnectionRecord, ...] = field(
        default=(), repr=False, compare=False,
    )
    _collection: ProjectConnectionReviewCollection | None = field(
        default=None, repr=False, compare=False,
    )
    _initial: ProjectAutomationResult | None = field(default=None, repr=False, compare=False)
    _exception_package: ExceptionResolutionPackage | None = field(
        default=None, repr=False, compare=False,
    )
    _member_rows: Mapping[str, Mapping[str, object]] | None = field(
        default=None, repr=False, compare=False,
    )
    _member_placements: Mapping[str, object] | None = field(default=None, repr=False, compare=False)
    _section_matcher: object = field(default=None, repr=False, compare=False)
    _intake: ProjectExtractionIntake | None = field(default=None, repr=False, compare=False)

    @property
    def collection(self) -> ProjectConnectionReviewCollection | None:
        """The existing 7X review collection this workflow runs on. Read-only access for
        presentation layers (7AK); the stored field itself stays private."""
        return self._collection

    @property
    def intake(self) -> ProjectExtractionIntake | None:
        """The 7Y intake this workflow was started from. Read-only access for coverage
        layers (7AX); the stored field itself stays private."""
        return self._intake

    @property
    def exception_package(self) -> ExceptionResolutionPackage | None:
        """The current exception-resolution contract, updated as human resolutions are
        recorded. Read-only access for presentation layers (7AK); the stored field itself
        stays private."""
        return self._exception_package


# --------------------------------------------------------------------------------------
# Projection helpers — pure functions over the recorded data, never new evidence.
# --------------------------------------------------------------------------------------
def _counts(connections: Sequence[ProjectConnectionState]) -> tuple[int, int, int, int]:
    review = sum(1 for c in connections if c.decision == AUTOMATION_DECISION_REVIEW)
    confirm = sum(1 for c in connections if c.decision == AUTOMATION_DECISION_CONFIRM)
    auto = sum(1 for c in connections if c.decision == AUTOMATION_DECISION_AUTO)
    verified = sum(1 for c in connections if c.verification_status == VERIFICATION_STATUS_VERIFIED)
    return review, confirm, auto, verified


def _summary_lines(
    project_id: str | None,
    revision: int,
    connections: Sequence[ProjectConnectionState],
    project_decision: str,
) -> list[str]:
    review, confirm, auto, verified = _counts(connections)
    blocked = review + confirm
    files = [path for c in connections for path in c.generated_files]
    lines = [
        f"revision = {revision}",
        f"project = {project_id or 'unknown'}; project decision = {project_decision}",
        f"AUTO = {auto}; CONFIRM = {confirm}; REVIEW = {review}; "
        f"VERIFIED = {verified}; blocked = {blocked}",
        f"generated files = {len(files)}",
    ]
    for connection in connections:
        parts = [f"decision={connection.decision}"]
        if connection.output_status is not None:
            parts.append(f"output={connection.output_status}")
        if connection.verification_status is not None:
            parts.append(f"verification={connection.verification_status}")
        if connection.generated_files:
            parts.append("files=" + ", ".join(Path(path).name for path in connection.generated_files))
        if connection.last_processed_revision is not None:
            parts.append(f"last_processed_revision={connection.last_processed_revision}")
        if connection.blockers:
            parts.append("blockers=" + ", ".join(connection.blockers))
        lines.append(f"{connection.package_id}: " + "; ".join(parts))
    lines.append(PROJECT_WORKFLOW_SCOPE_STATEMENT)
    return lines


def _initial_connection_state(candidate, outcome) -> ProjectConnectionState:
    """One connection's untouched revision-0 projection, straight from the genuine 7AB outcome."""
    return ProjectConnectionState(
        package_id=candidate.review_package_id,
        connection_id=candidate.package.supplement.connection_id,
        decision=outcome.decision,
        output_status=None,
        verification_status=None,
        generated_files=(),
        blockers=tuple(finding.code for finding in outcome.blockers),
        warnings=tuple(finding.code for finding in outcome.warnings),
        last_processed_revision=None,
    )


def _processed_connection_state(
    package_id: str,
    rerun_outcome: ConnectionRerunOutcome,
    dispatch_result: DrawingDispatchResult,
    verification_result: ArtifactVerificationResult,
    revision: int,
) -> ProjectConnectionState:
    """One connection's projection after it was genuinely processed at `revision`."""
    return ProjectConnectionState(
        package_id=package_id,
        connection_id=dispatch_result.connection_id,
        decision=rerun_outcome.decision,
        output_status=dispatch_result.output_status,
        verification_status=verification_result.verification_status,
        generated_files=tuple(str(path) for path in dispatch_result.generated_files),
        blockers=tuple(finding.code for finding in rerun_outcome.blockers),
        warnings=tuple(finding.code for finding in rerun_outcome.warnings),
        last_processed_revision=revision,
    )


def _composite_gate(initial, pipeline_by_package_id):
    """The genuine project-level fabrication-output gate over every connection's CURRENT
    pipeline — the existing project semantics, one-to-one by package id (the gate enforces
    the alignment itself). Read-only; nothing is dispatched."""
    return evaluate_project_fabrication_output_gate(initial, pipeline_by_package_id)


def _drawing_material(specification: ReviewedConnectionSpecification | None) -> str | None:
    """
    The material value a drawing may carry, from the reviewed engineering specification:
    present AND owned by a human decision (HUMAN_REVIEWED or HUMAN_SUPPLEMENTED). A missing
    material, or one still labelled AI_EXTRACTED (no human decision behind it), forwards
    nothing — the existing generator then renders MATERIAL NOT SPECIFIED. The value is the
    specification's own verbatim string, never re-derived, converted or dressed up.
    """
    if specification is None or specification.material is None:
        return None
    if specification.provenance.get("material") not in (
        PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED,
    ):
        return None
    return specification.material


def _artifact_presence_notes(connections: Sequence[ProjectConnectionState]) -> list[str]:
    """Cheap existence checks on the RECORDED artifacts (no parsing, no hashing — that is
    verification's job). A missing or replaced artifact is reported, never repaired."""
    notes = []
    for connection in connections:
        for path_str in connection.generated_files:
            path = Path(path_str)
            if not path.exists():
                notes.append(f"{connection.package_id}: recorded artifact {path} is missing")
            elif not path.is_file():
                notes.append(f"{connection.package_id}: recorded artifact {path} is not a regular file")
    return notes


# --------------------------------------------------------------------------------------
# The three workflow operations.
# --------------------------------------------------------------------------------------
def start_project_workflow(
    collection,
    *,
    intake,
    member_rows,
    member_placements,
    section_matcher,
    request_material_specification: bool = False,
) -> ProjectWorkflowState:
    """
    Starts the project workflow at revision 0 from an existing 7X
    review collection and its 7Y intake. Runs the genuine 7AB
    evaluation WITHOUT member context (raw candidates can never become
    AUTO), builds the genuine 7AC exception contract WITH the member
    context (its only source of authoritative member choices), and
    re-obtains — once, for storage — the genuine 7AA pipeline objects
    7AB consumed internally but does not expose (same inputs, so each
    is deterministically equal to the evaluation 7AB just performed;
    a disagreement is a loud internal failure). The initial project
    decision is the composite fabrication-output gate's own package
    decision over those pipelines. Nothing is resolved, dispatched,
    generated or verified here.

    `request_material_specification` (default False) forwards 7AC's
    explicit reviewer-facing request: when true, non-AUTO candidates
    whose review report shows no material value receive one
    PROVIDE_MATERIAL_SPECIFICATION task. The workflow itself never
    decides material — it only carries the caller's review scope.
    """
    if not isinstance(collection, ProjectConnectionReviewCollection):
        raise TypeError(
            f"collection must be a ProjectConnectionReviewCollection (got "
            f"{type(collection).__name__}); the workflow consumes the existing 7X review queue."
        )
    require_submission_identities(collection)
    if not isinstance(intake, ProjectExtractionIntake):
        raise TypeError(
            f"intake must be a ProjectExtractionIntake (got {type(intake).__name__}); the "
            "workflow consumes the existing 7Y intake."
        )
    if not isinstance(member_rows, Mapping) or not isinstance(member_placements, Mapping):
        raise TypeError("member_rows and member_placements must be mappings.")
    if not isinstance(request_material_specification, bool):
        raise TypeError(
            f"request_material_specification must be a bool (got "
            f"{type(request_material_specification).__name__})."
        )

    initial = evaluate_project_for_automation(collection, intake=intake)

    records: list[ProjectConnectionRecord] = []
    initial_pipelines: list[AutomationPipelineResult] = []
    for candidate, outcome in zip(collection.candidates, initial.connection_results):
        pipeline = evaluate_reviewed_connection_for_automation(candidate.package)
        if pipeline.automation_gate_result.decision != outcome.decision:
            raise WorkflowStageFailureError(
                f"for {candidate.review_package_id}, the stored 7AA pipeline decided "
                f"{pipeline.automation_gate_result.decision!r} while 7AB's outcome decided "
                f"{outcome.decision!r}; the workflow never proceeds on disagreeing stage results."
            )
        initial_pipelines.append(pipeline)
        records.append(ProjectConnectionRecord(
            pipeline=pipeline, rerun_outcome=None, gate_result=None,
            dispatch_result=None, verification_result=None,
        ))

    exception_package = build_exception_resolution_package(
        initial, collection, member_rows=member_rows,
        request_material_specification=request_material_specification,
    )

    pipelines_by_id = {
        candidate.review_package_id: pipeline
        for candidate, pipeline in zip(collection.candidates, initial_pipelines)
    }
    project_gate = _composite_gate(initial, pipelines_by_id)

    connections = tuple(
        _initial_connection_state(candidate, outcome)
        for candidate, outcome in zip(collection.candidates, initial.connection_results)
    )
    return _assemble_state(
        initial.project_id, 0, connections, tuple(records),
        project_gate.package_decision, collection, initial, exception_package,
        member_rows, member_placements, section_matcher, extra_lines=(), intake=intake,
    )


def resolve_project_connection(
    workflow,
    *,
    package_id,
    resolutions,
    output_dir,
    expected_revision=None,
    drawing_entry=None,
) -> ProjectWorkflowState:
    """
    Resolves ONE connection: records the human resolutions through the
    existing 7AC contract, re-runs ONLY that connection through the
    existing connection-level rerun (7AD -> 7AA -> 7Z), re-obtains its
    genuine 7AA pipeline object from the rebuilt package (7AD's
    decision stays the authority), evaluates the composite
    fabrication-output gate over every connection's current pipeline,
    dispatches ONLY the changed connection through the existing
    per-connection dispatch entry (the composite gate's genuine
    outcome for it), verifies ONLY its artifact through the existing
    per-connection verification entry with its genuine reviewed
    assembly, and returns the next immutable state (revision + 1).

    Every other connection keeps its previous state object and record
    untouched — nothing is re-run, re-gated, re-dispatched,
    re-verified or rebuilt for it.

    `expected_revision`, when given, is the revision the caller holds;
    if it is not the workflow's current revision the operation is
    REFUSED (a stale human decision is never applied to a newer
    project state). A connection is processed AT MOST ONCE: a second
    resolve attempt is refused — no duplicate drawing, no duplicate
    artifact, no downgrade. `drawing_entry` is the existing dispatch
    test seam, forwarded unchanged (None = the dispatch's own default
    entry).

    Validation failures raise BEFORE anything runs: no state change,
    no files, no revision advance. A generation or verification
    failure flows through the genuine 7AF/7AG result semantics and is
    recorded as what it is.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__}); a "
            "connection is resolved against the existing project workflow state."
        )
    if not isinstance(package_id, str):
        raise TypeError("package_id must be a str.")
    if not isinstance(resolutions, (list, tuple)) or not all(
            isinstance(resolution, HumanResolution) for resolution in resolutions):
        raise TypeError("resolutions must be a sequence of HumanResolution.")
    if not isinstance(output_dir, (str, Path)):
        raise TypeError("output_dir must be a str or Path.")
    if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
        raise TypeError("expected_revision must be an int or None.")
    if drawing_entry is not None and not callable(drawing_entry):
        raise TypeError("drawing_entry must be callable or None.")

    # ------------------------------------------------ optimistic concurrency
    if expected_revision is not None and expected_revision != workflow.revision:
        raise StaleProjectWorkflowError(
            f"the project workflow is at revision {workflow.revision} but the caller holds "
            f"revision {expected_revision}; a stale human decision is never applied to a newer "
            "project state."
        )

    # ------------------------------------------------- unknown package guard
    index = next(
        (i for i, connection in enumerate(workflow.connections)
         if connection.package_id == package_id),
        None,
    )
    if index is None:
        raise UnknownProjectPackageError(
            f"{package_id!r} is not a connection of this project (connections: "
            f"{[c.package_id for c in workflow.connections]}); nothing was processed."
        )

    # ----------------------------------------------------- one-shot semantics
    current = workflow.connections[index]
    if current.last_processed_revision is not None:
        raise ConnectionAlreadyProcessedError(
            f"{package_id} was already processed at revision "
            f"{current.last_processed_revision} (decision now {current.decision!r}, output "
            f"{current.output_status!r}); a connection is resolved at most once — re-resolving "
            "it could never improve a recorded result and must not duplicate production."
        )
    if not resolutions:
        raise ValueError(
            "resolutions must not be empty; a resolve that records no human answers processes "
            "nothing and is refused rather than silently consuming the connection's single "
            "resolution attempt."
        )

    # ------------------------------------- cross-package / cross-project guard
    group = next(
        group for group in workflow._exception_package.connection_tasks
        if group.review_package_id == package_id
    )
    own_task_ids = {task.task_id for task in group.tasks}
    foreign = [resolution.task_id for resolution in resolutions
               if resolution.task_id not in own_task_ids]
    if foreign:
        raise CrossProjectResolutionError(
            f"resolution task id(s) {foreign} do not address a task of {package_id} in this "
            "project; a resolution recorded for one connection (or one project) is never "
            "applied to another."
        )

    # ------------------------------- record the answers through the existing contract
    exception_package = workflow._exception_package
    for resolution in resolutions:
        exception_package = apply_human_resolution(exception_package, resolution)
    group = next(
        group for group in exception_package.connection_tasks
        if group.review_package_id == package_id
    )

    # -------------------------------- the genuine connection-level rerun — ONLY this one
    candidate = next(
        candidate for candidate in workflow._collection.candidates
        if candidate.review_package_id == package_id
    )
    rerun_outcome = rerun_connection_after_resolutions(
        group,
        candidate.package,
        member_rows=workflow._member_rows,
        member_placements=workflow._member_placements,
        section_matcher=workflow._section_matcher,
    )

    # ----------------------- re-obtain the genuine 7AA object 7AE/7AF require,
    # ----------------------- from the 7AD-rebuilt package, SAME member context
    pipeline = evaluate_reviewed_connection_for_automation(
        rerun_outcome.rebuilt_package,
        member_rows=workflow._member_rows,
        member_placements=workflow._member_placements,
        section_matcher=workflow._section_matcher,
    )
    if pipeline.automation_gate_result.decision != rerun_outcome.decision:
        raise WorkflowStageFailureError(
            f"the rerun decided {rerun_outcome.decision!r} for {package_id} but the re-obtained "
            f"7Z decision is {pipeline.automation_gate_result.decision!r}; the workflow never "
            "proceeds on disagreeing stage results."
        )

    # ----------------- the genuine composite project gate over CURRENT pipelines
    pipelines_by_id = {
        connection.package_id: (
            pipeline if connection.package_id == package_id else record.pipeline
        )
        for connection, record in zip(workflow.connections, workflow.connection_records)
    }
    project_gate = _composite_gate(workflow._initial, pipelines_by_id)
    gate_result = next(
        outcome.gate_result for outcome in project_gate.connection_results
        if outcome.review_package_id == package_id
    )

    # ---------- dispatch ONLY the connection that changed, consuming the composite
    # ---------- gate's genuine per-connection outcome (see the module docstring)
    # Job identity (Milestone 7AR): the workflow's own project id and the rebuilt
    # package's recorded source drawing id are forwarded as presentation-only
    # title-block fields (7AF's documented pdf_kwargs channel) so every drawing
    # names the job it belongs to. Identifiers only — never engineering values,
    # and a missing identifier is omitted, never invented.
    # Material (Milestone 7AT): the reviewed specification's material is forwarded
    # through the same presentation-only channel ONLY when a human decision owns it
    # (HUMAN_REVIEWED / HUMAN_SUPPLEMENTED). A missing material, or one still
    # AI_EXTRACTED, forwards nothing — the existing generator renders MATERIAL NOT
    # SPECIFIED, and the fabricator acceptance layer reports exactly that.
    pdf_kwargs: dict[str, object] = {}
    if workflow.project_id is not None:
        pdf_kwargs["project_id"] = workflow.project_id
    source_drawing_id = rerun_outcome.rebuilt_package.extraction.source_drawing_id
    if source_drawing_id is not None:
        pdf_kwargs["source_drawing_id"] = source_drawing_id
    material = _drawing_material(pipeline.specification)
    if material is not None:
        pdf_kwargs["material"] = material
    dispatch_kwargs: dict[str, object] = {}
    if drawing_entry is not None:
        dispatch_kwargs["drawing_entry"] = drawing_entry
    if pdf_kwargs:
        dispatch_kwargs["pdf_kwargs"] = pdf_kwargs
    dispatch_result = dispatch_fabrication_drawing(
        gate_result, pipeline, output_dir, **dispatch_kwargs,
    )

    # ---------- verify ONLY the artifact just dispatched, against the genuine
    # ---------- reviewed assembly (None means integrity-only checks — 7AG's rule)
    verification_result = verify_drawing_artifact(
        dispatch_result, assembly=pipeline.reviewed_assembly,
    )

    # ------------------------------------------------- the next immutable state
    new_revision = workflow.revision + 1
    new_connections: list[ProjectConnectionState] = []
    new_records: list[ProjectConnectionRecord] = []
    for connection, record in zip(workflow.connections, workflow.connection_records):
        if connection.package_id == package_id:
            new_connections.append(_processed_connection_state(
                package_id, rerun_outcome, dispatch_result, verification_result, new_revision,
            ))
            new_records.append(ProjectConnectionRecord(
                pipeline=pipeline, rerun_outcome=rerun_outcome, gate_result=gate_result,
                dispatch_result=dispatch_result, verification_result=verification_result,
            ))
        else:
            new_connections.append(connection)  # the SAME object — never rebuilt
            new_records.append(record)

    return _assemble_state(
        workflow.project_id, new_revision, tuple(new_connections), tuple(new_records),
        project_gate.package_decision, workflow._collection, workflow._initial,
        exception_package, workflow._member_rows, workflow._member_placements,
        workflow._section_matcher, extra_lines=(), intake=workflow._intake,
    )


def refresh_project_workflow(
    workflow,
    *,
    output_dir,
) -> ProjectWorkflowState:
    """
    Re-projects the project view WITHOUT processing anything: the
    composite fabrication-output gate is re-evaluated over the stored
    pipelines (read-only), the counts and summary are recomputed from
    the stored connection states, and the recorded artifacts are
    checked for presence (existence only — no parsing, no hashing, no
    re-verification). The revision does not change; nothing is
    re-run, re-dispatched, re-generated or re-verified.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__})."
        )
    if not isinstance(output_dir, (str, Path)):
        raise TypeError("output_dir must be a str or Path.")

    pipelines_by_id = {
        connection.package_id: record.pipeline
        for connection, record in zip(workflow.connections, workflow.connection_records)
    }
    project_gate = _composite_gate(workflow._initial, pipelines_by_id)

    notes = _artifact_presence_notes(workflow.connections)
    if notes:
        extra = [
            f"refresh: {len(notes)} recorded artifact(s) missing or not regular files; "
            "the workflow never repairs or regenerates them",
        ]
        extra.extend(f"refresh: {note}" for note in notes)
    else:
        extra = ["refresh: every recorded artifact is present"]
    return _assemble_state(
        workflow.project_id, workflow.revision, workflow.connections,
        workflow.connection_records, project_gate.package_decision, workflow._collection,
        workflow._initial, workflow._exception_package, workflow._member_rows,
        workflow._member_placements, workflow._section_matcher, extra_lines=tuple(extra),
        intake=workflow._intake,
    )


# --------------------------------------------------------------------------------------
# State assembly — pure bookkeeping over recorded results.
# --------------------------------------------------------------------------------------
def _assemble_state(
    project_id,
    revision,
    connections,
    records,
    project_decision,
    collection,
    initial,
    exception_package,
    member_rows,
    member_placements,
    section_matcher,
    *,
    extra_lines,
    intake=None,
) -> ProjectWorkflowState:
    review, confirm, auto, verified = _counts(connections)
    lines = _summary_lines(project_id, revision, connections, project_decision)
    lines.extend(extra_lines)
    return ProjectWorkflowState(
        project_id=project_id,
        revision=revision,
        connections=tuple(connections),
        project_decision=project_decision,
        generated_files=tuple(
            path for connection in connections for path in connection.generated_files
        ),
        review_count=review,
        confirm_count=confirm,
        auto_count=auto,
        verified_count=verified,
        blocked_count=review + confirm,
        summary="\n".join(lines),
        connection_records=tuple(records),
        _collection=collection,
        _initial=initial,
        _exception_package=exception_package,
        _member_rows=member_rows,
        _member_placements=member_placements,
        _section_matcher=section_matcher,
        _intake=intake,
    )
