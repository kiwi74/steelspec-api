"""
Milestone 7AF — REAL DRAWING DISPATCH: the execution layer that
carries an accepted 7AE fabrication-output permission to the EXISTING
SteelSpec drawing generator.

    7AE FabricationOutputGateResult (AUTO / CONFIRM / REVIEW)
            |
    dispatch_fabrication_drawing()          (per connection)
            |   REVIEW  -> BLOCKED_REVIEW         (generator never called)
            |   CONFIRM -> BLOCKED_CONFIRMATION   (generator never called)
            |   AUTO    -> existing drawing generator (7S boundary ->
            |              generate_connection_fabrication_drawing_pdf)
            v
    DrawingDispatchResult (frozen manifest)
            |
    dispatch_project_fabrication_drawings() (per project, all outcomes kept)
            v
    ProjectDrawingDispatchResult (frozen manifest)

7AF EXECUTES PERMISSION. IT NEVER DECIDES IT. Every engineering
question — review status, provenance, member identity, position,
plate, hole diameter, location, attachment, specification, validation,
geometry, AI confidence — was decided upstream by 7V/7R/7Z/7AA/7AE;
7AF re-checks none of them and calls none of those gates.

HARD RULES:

  - 7AE REMAINS THE ONLY PERMISSION BOUNDARY. The decision consumed
    here is the genuine FabricationOutputGateResult's `decision`
    field. There is deliberately NO `approved`, NO `validation_passed`
    and NO `decision` parameter anywhere in this API — a caller cannot
    inject authorization, and 7AF cannot self-authorize.
  - REVIEW NEVER GENERATES. BLOCKED_REVIEW, the generator untouched.
  - CONFIRM NEVER GENERATES. BLOCKED_CONFIRMATION, the generator
    untouched. 7AF never asks the user for confirmation and never
    converts CONFIRM into AUTO: only a genuine subsequent 7AE decision
    of AUTO (obtained upstream by rerunning the normal pipeline) may
    reach the generator.
  - GENUINE REVIEWED ASSEMBLY ONLY. The drawing generator receives
    exactly pipeline_result.reviewed_assembly — the project-space
    geometry the accepted automation pipeline built (the 7O
    ReviewedTwoMemberConnectionAssembly for two-member connections,
    or 7AV's ReviewedMultiMemberConnectionAssembly for three or
    more). Nothing is reconstructed from raw AI data, rebuilt from
    marks, or faked; no dimension, coordinate, hole size, plate size,
    member length or attachment location is invented here (this
    module contains no numeric literal at all).
  - THE EXISTING GENERATOR IS REUSED, NEVER DUPLICATED. The default
    entry for a two-member assembly is the 7S production boundary
    generate_fabrication_drawing_from_reviewed_assembly(), which runs
    the existing 7R re-check and then the existing 7T PDF builder;
    for a multi-member assembly it is 7AV's drawing gate
    generate_fabrication_drawing_from_reviewed_multi_member_assembly()
    (which runs 7AV's per-member validation re-check and then the
    same drawing primitives the 7T builder uses). 7AF writes no
    DXF/PDF/STEP construction code of its own. The entries are
    injectable (`drawing_entry`, `multi_member_drawing_entry`) for
    tests, nothing else.
  - NO FALLBACK DRAWING, EVER. If the requested format has no existing
    generator entry (the reviewed-connection drawing generator exists
    for PDF only; the single-member DXF path is not a connection
    drawing), or the generator raises, the dispatch reports
    GENERATION_FAILED with a deterministic representation of the
    actual error. No simplified, placeholder, generic, approximate or
    partial drawing is ever produced.
  - FAILURES ARE HONEST. A failed generator call is never reported as
    GENERATED; files that WERE written before a failure stay recorded
    in generated_files, and the manifest never hides them.
  - THE MANIFEST RECORDS, IT DOES NOT GRANT. Immutable frozen
    plain-data results; deterministic repeated dispatches; no hidden
    global state, no AI calls, no database calls, no network, no
    uploads (app/export/storage_export.py stays untouched), no
    mutation of any reviewed assembly or pipeline result.
  - OUTPUT NAMING: output files are named by connection identity
    (f"{connection_id}-fabrication.pdf"); dispatching the same
    connection twice therefore writes the same path — the later
    dispatch replaces the earlier file, and each manifest records its
    own outcome honestly.
  - PROJECT DISPATCH KEEPS EVERY OUTCOME. Every input connection has
    exactly one output outcome — blocked connections are never
    collapsed, omitted or downgraded, and the project manifest never
    describes the project as generated or fabrication-ready.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.cad_engine.automation_gate import (
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import (
    AutomationPipelineResult,
    ReferenceDataIdentity,
)
from app.cad_engine.fabrication_output_gate import (
    FabricationOutputGateResult,
    ProjectFabricationOutputGateResult,
)
from app.cad_engine.multi_member_connection import (
    ReviewedMultiMemberConnectionAssembly,
    generate_fabrication_drawing_from_reviewed_multi_member_assembly,
)
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly
from app.cad_engine.reviewed_connection_drawing_gate import (
    generate_fabrication_drawing_from_reviewed_assembly,
)

__all__ = [
    "OUTPUT_STATUS_BLOCKED_REVIEW", "OUTPUT_STATUS_BLOCKED_CONFIRMATION",
    "OUTPUT_STATUS_GENERATED", "OUTPUT_STATUS_GENERATION_FAILED", "OUTPUT_STATUSES",
    "DRAWING_FORMAT_PDF", "DRAWING_FORMAT_DXF", "KNOWN_DRAWING_FORMATS",
    "PROJECT_DISPATCH_SCOPE_STATEMENT",
    "DrawingDispatchResult", "ProjectConnectionDrawingOutcome", "ProjectDrawingDispatchResult",
    "dispatch_fabrication_drawing", "dispatch_project_fabrication_drawings",
]

OUTPUT_STATUS_BLOCKED_REVIEW = "BLOCKED_REVIEW"
OUTPUT_STATUS_BLOCKED_CONFIRMATION = "BLOCKED_CONFIRMATION"
OUTPUT_STATUS_GENERATED = "GENERATED"
OUTPUT_STATUS_GENERATION_FAILED = "GENERATION_FAILED"
OUTPUT_STATUSES = (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_BLOCKED_CONFIRMATION,
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
)

# The formats SteelSpec knows. The EXISTING reviewed-connection drawing
# generator produces PDF only (7T); the single-member DXF path is not a
# connection drawing, so a "dxf" request for a connection fails honestly.
DRAWING_FORMAT_PDF = "pdf"
DRAWING_FORMAT_DXF = "dxf"
KNOWN_DRAWING_FORMATS = (DRAWING_FORMAT_PDF, DRAWING_FORMAT_DXF)

PROJECT_DISPATCH_SCOPE_STATEMENT = (
    "This manifest records what happened for every supplied connection. It grants no "
    "permission, never describes the project as generated or fabrication-ready, and keeps "
    "every blocked connection visible."
)


@dataclass(frozen=True)
class DrawingDispatchResult:
    """
    The immutable manifest of one dispatch: the 7AE decision that was
    executed, the outcome status, the formats requested, the files the
    existing drawing generator actually wrote, and — when the status is
    GENERATION_FAILED — a deterministic representation of the actual
    error. `catalogue_version` is the genuine 7AA pipeline result's own
    recorded catalogue version (None when the producing run used a
    matcher that declares none) — so the record OF a produced drawing
    identifies which section catalogue its geometry came from.
    `reference_identity` is the 7AA result's own reference-data identity
    (source kind, formal identity status, and the digest of the reference
    rows the matcher loaded), carried through unchanged so the record of a
    produced drawing states WHICH reference data it came from. It is None
    exactly when the producing run recorded none. The digest is a content
    fingerprint, never a catalogue version, and nothing here recomputes it.
    Plain frozen data only; it records, it never grants.
    """
    connection_id: str | None
    decision: str
    output_status: str
    requested_formats: tuple[str, ...]
    generated_files: tuple[Path, ...]
    generation_error: str | None
    catalogue_version: str | None = None
    reference_identity: ReferenceDataIdentity | None = None


@dataclass(frozen=True)
class ProjectConnectionDrawingOutcome:
    """
    One connection's dispatch outcome in the project's own submission
    order: the 7X identity plus its dispatch manifest.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    dispatch_result: DrawingDispatchResult


@dataclass(frozen=True)
class ProjectDrawingDispatchResult:
    """
    The immutable project dispatch manifest. Counts are DERIVED from
    connection_outputs, never asserted; every input connection has
    exactly one outcome, and blocked/failed connections are never
    collapsed or omitted.
    """
    project_id: str | None
    connections_total: int
    generated_count: int
    blocked_review_count: int
    blocked_confirmation_count: int
    generation_failed_count: int
    connection_outputs: tuple[ProjectConnectionDrawingOutcome, ...]
    summary: tuple[str, ...]


def _blocked_result(
    gate_result: FabricationOutputGateResult,
    status: str,
    requested_formats: tuple[str, ...],
    catalogue_version: str | None,
    reference_identity: ReferenceDataIdentity | None = None,
) -> DrawingDispatchResult:
    return DrawingDispatchResult(
        connection_id=gate_result.connection_id,
        decision=gate_result.decision,
        output_status=status,
        requested_formats=requested_formats,
        generated_files=(),
        generation_error=None,
        catalogue_version=catalogue_version,
        reference_identity=reference_identity,
    )


def _failed_result(
    gate_result: FabricationOutputGateResult,
    requested_formats: tuple[str, ...],
    generated_files: tuple[Path, ...],
    error: str,
    catalogue_version: str | None,
    reference_identity: ReferenceDataIdentity | None = None,
) -> DrawingDispatchResult:
    return DrawingDispatchResult(
        connection_id=gate_result.connection_id,
        decision=gate_result.decision,
        output_status=OUTPUT_STATUS_GENERATION_FAILED,
        requested_formats=requested_formats,
        generated_files=generated_files,
        generation_error=error,
        catalogue_version=catalogue_version,
        reference_identity=reference_identity,
    )


def dispatch_fabrication_drawing(
    gate_result: FabricationOutputGateResult,
    pipeline_result: AutomationPipelineResult,
    output_dir: str | Path,
    *,
    requested_formats: Sequence[str] = (DRAWING_FORMAT_PDF,),
    drawing_entry: Callable[..., Path] = generate_fabrication_drawing_from_reviewed_assembly,
    multi_member_drawing_entry: Callable[..., Path] = generate_fabrication_drawing_from_reviewed_multi_member_assembly,
    pdf_kwargs: Mapping[str, object] | None = None,
) -> DrawingDispatchResult:
    """
    Executes one accepted 7AE fabrication-output permission.

    `gate_result` is the GENUINE FabricationOutputGateResult (7AE's
    decision — never re-derived and never accepted from the caller as a
    string); `pipeline_result` is the genuine 7AA result that produced
    it, whose reviewed_assembly is the only geometry that may reach the
    drawing generator.

      - decision REVIEW  -> BLOCKED_REVIEW, generator never called,
                            nothing written (output_dir not even created)
      - decision CONFIRM -> BLOCKED_CONFIRMATION, generator never
                            called, nothing written
      - decision AUTO    -> the genuine reviewed assembly is passed to
                            the matching drawing generator (default: the
                            7S boundary for two-member assemblies — the
                            existing 7R re-check and the existing 7T PDF
                            builder; 7AV's multi-member drawing gate for
                            3+-member assemblies — the per-member
                            validation re-check and the same drawing
                            primitives) for each requested format; every
                            generator error or format with no existing
                            generator entry yields GENERATION_FAILED
                            with the actual error, and files already
                            written stay recorded — never hidden

    Formats: "pdf" (the existing reviewed-connection generator);
    "dxf" exists only for single members, so a connection "dxf" request
    fails honestly with no fallback drawing. `pdf_kwargs` forwards
    presentation-only title-block fields (status/material/revision/date)
    to the existing generator, exactly as it already accepts them.

    Read-only and deterministic: never mutates the pipeline result, the
    assembly or the gate result; repeated dispatches of the same inputs
    return equal manifests.
    """
    if not isinstance(gate_result, FabricationOutputGateResult):
        raise TypeError(
            f"gate_result must be the genuine FabricationOutputGateResult (got "
            f"{type(gate_result).__name__}); dispatch executes an accepted 7AE permission, "
            "never anything else."
        )
    if not isinstance(pipeline_result, AutomationPipelineResult):
        raise TypeError(
            f"pipeline_result must be an AutomationPipelineResult (got "
            f"{type(pipeline_result).__name__}); only the genuine pipeline's reviewed assembly "
            "may reach the drawing generator."
        )
    if not callable(drawing_entry):
        raise TypeError("drawing_entry must be callable: the existing drawing generator entry.")
    formats = tuple(requested_formats)
    if not formats:
        raise ValueError("requested_formats must name at least one output format.")
    for fmt in formats:
        if fmt not in KNOWN_DRAWING_FORMATS:
            raise ValueError(f"Unknown output format {fmt!r}; known formats: {list(KNOWN_DRAWING_FORMATS)}.")
    pdf_kwargs = dict(pdf_kwargs or {})

    if gate_result.decision == AUTOMATION_DECISION_REVIEW:
        return _blocked_result(
            gate_result, OUTPUT_STATUS_BLOCKED_REVIEW, formats,
            pipeline_result.catalogue_version,
            pipeline_result.reference_identity,
        )
    if gate_result.decision == AUTOMATION_DECISION_CONFIRM:
        return _blocked_result(
            gate_result, OUTPUT_STATUS_BLOCKED_CONFIRMATION, formats,
            pipeline_result.catalogue_version,
            pipeline_result.reference_identity,
        )
    if gate_result.decision != AUTOMATION_DECISION_AUTO:
        return _failed_result(
            gate_result, formats, (),
            f"unrecognized 7AE decision {gate_result.decision!r}; dispatch refuses to generate "
            "without an accepted AUTO permission.",
            pipeline_result.catalogue_version,
            pipeline_result.reference_identity,
        )

    assembly = pipeline_result.reviewed_assembly
    if assembly is None:
        return _failed_result(
            gate_result, formats, (),
            "the authorized pipeline result carries no reviewed assembly; nothing was rebuilt "
            "and nothing was generated.",
            pipeline_result.catalogue_version,
            pipeline_result.reference_identity,
        )
    if isinstance(assembly, ReviewedTwoMemberConnectionAssembly):
        entry = drawing_entry
    elif isinstance(assembly, ReviewedMultiMemberConnectionAssembly):
        entry = multi_member_drawing_entry
    else:
        return _failed_result(
            gate_result, formats, (),
            f"the pipeline result's reviewed_assembly is a {type(assembly).__name__}, not a "
            "ReviewedTwoMemberConnectionAssembly or a ReviewedMultiMemberConnectionAssembly; "
            "dispatch never substitutes a rebuilt assembly.",
        )
    if gate_result.connection_id is None:
        return _failed_result(
            gate_result, formats, (),
            "the accepted 7AE permission carries no connection identity; dispatch refuses to "
            "name an output file from nothing.",
            pipeline_result.catalogue_version,
            pipeline_result.reference_identity,
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []
    errors: list[str] = []
    for fmt in formats:
        if fmt != DRAWING_FORMAT_PDF:
            errors.append(
                "no reviewed-connection DXF entry exists in the drawing generator (DXF exists "
                "only for single members); no fallback drawing is produced"
            )
            continue
        try:
            output_path = output_dir / f"{gate_result.connection_id}-fabrication.pdf"
            entry(assembly, output_path, **pdf_kwargs)
            if not output_path.exists():
                raise FileNotFoundError(f"the drawing entry returned without writing {output_path.name}")
            generated.append(output_path)
        except Exception as error:  # noqa: BLE001 — every failure is recorded, never swallowed
            errors.append(f"{type(error).__name__}: {error}")

    if errors:
        return _failed_result(
            gate_result, formats, tuple(generated), "; ".join(errors),
            pipeline_result.catalogue_version,
            pipeline_result.reference_identity,
        )
    return DrawingDispatchResult(
        connection_id=gate_result.connection_id,
        decision=gate_result.decision,
        output_status=OUTPUT_STATUS_GENERATED,
        requested_formats=formats,
        generated_files=tuple(generated),
        generation_error=None,
        catalogue_version=pipeline_result.catalogue_version,
        reference_identity=pipeline_result.reference_identity,
    )


def dispatch_project_fabrication_drawings(
    project_gate_result: ProjectFabricationOutputGateResult,
    pipeline_results: Mapping[str, AutomationPipelineResult],
    output_dir: str | Path,
    *,
    requested_formats: Sequence[str] = (DRAWING_FORMAT_PDF,),
    drawing_entry: Callable[..., Path] = generate_fabrication_drawing_from_reviewed_assembly,
    multi_member_drawing_entry: Callable[..., Path] = generate_fabrication_drawing_from_reviewed_multi_member_assembly,
    pdf_kwargs: Mapping[str, object] | None = None,
) -> ProjectDrawingDispatchResult:
    """
    Executes the accepted project-level 7AE permission: dispatches each
    connection through the single-connection rule using the genuine
    per-connection 7AE outcome (project_gate_result.connection_results)
    and the genuine underlying pipeline result.

    `pipeline_results` must align one-to-one with the project gate's
    connection_results by review_package_id (anything else raises) —
    one connection's evidence is never applied to another. Every input
    connection has exactly one outcome; AUTO connections generate their
    own outputs while REVIEW/CONFIRM connections stay blocked, and the
    counts are derived from the outcomes, never asserted.
    """
    if not isinstance(project_gate_result, ProjectFabricationOutputGateResult):
        raise TypeError(
            f"project_gate_result must be the genuine ProjectFabricationOutputGateResult (got "
            f"{type(project_gate_result).__name__})."
        )
    if not isinstance(pipeline_results, Mapping):
        raise TypeError(f"pipeline_results must be a mapping of review_package_id -> "
                        f"AutomationPipelineResult (got {type(pipeline_results).__name__}).")
    expected_ids = [outcome.review_package_id for outcome in project_gate_result.connection_results]
    if sorted(pipeline_results) != sorted(expected_ids):
        raise ValueError(
            "pipeline_results must align one-to-one with the project gate's connection_results "
            "by review_package_id; a connection's dispatch never uses another connection's "
            "pipeline evidence."
        )

    outcomes = tuple(
        ProjectConnectionDrawingOutcome(
            review_package_id=outcome.review_package_id,
            submission_index=outcome.submission_index,
            source_identity=outcome.source_identity,
            dispatch_result=dispatch_fabrication_drawing(
                outcome.gate_result,
                pipeline_results[outcome.review_package_id],
                output_dir,
                requested_formats=requested_formats,
                drawing_entry=drawing_entry,
                multi_member_drawing_entry=multi_member_drawing_entry,
                pdf_kwargs=pdf_kwargs,
            ),
        )
        for outcome in project_gate_result.connection_results
    )

    total = len(outcomes)
    generated_count = len([o for o in outcomes if o.dispatch_result.output_status == OUTPUT_STATUS_GENERATED])
    blocked_review_count = len([o for o in outcomes if o.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_REVIEW])
    blocked_confirmation_count = len([
        o for o in outcomes if o.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_CONFIRMATION
    ])
    generation_failed_count = len([
        o for o in outcomes if o.dispatch_result.output_status == OUTPUT_STATUS_GENERATION_FAILED
    ])

    lines = [
        f"connections_total = {total}",
        f"generated_count = {generated_count}",
        f"blocked_review_count = {blocked_review_count}",
        f"blocked_confirmation_count = {blocked_confirmation_count}",
        f"generation_failed_count = {generation_failed_count}",
        PROJECT_DISPATCH_SCOPE_STATEMENT,
    ]
    for outcome in outcomes:
        lines.append(
            f"{outcome.review_package_id}: {outcome.dispatch_result.output_status} — "
            + (
                ", ".join(str(path.name) for path in outcome.dispatch_result.generated_files)
                if outcome.dispatch_result.generated_files else "no files"
            )
        )

    return ProjectDrawingDispatchResult(
        project_id=project_gate_result.project_id,
        connections_total=total,
        generated_count=generated_count,
        blocked_review_count=blocked_review_count,
        blocked_confirmation_count=blocked_confirmation_count,
        generation_failed_count=generation_failed_count,
        connection_outputs=outcomes,
        summary=tuple(lines),
    )
