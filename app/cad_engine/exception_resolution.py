"""
Milestone 7AC — EXCEPTION RESOLUTION CONTRACT: the backend-only,
deterministic contract that converts an existing 7AB project automation
result into structured human-review tasks, and converts structured
human answers back into the EXISTING 7W review-supplement mechanism:

    7AB ProjectAutomationResult + 7X collection
            |
    build_exception_resolution_package()       --> ExceptionResolutionPackage
            |                                       (one task set per connection, in
            |                                        submission order; factual scope/warnings)
    future UI presents tasks, human answers
            |
    apply_human_resolution()                   --> the same package, task RESOLVED
            |
    build_resolved_supplement()                --> the EXISTING 7W ConnectionReviewSupplement
            |                                       (answers applied to its existing fields)
            |
    7X update_candidate_supplement() -> 7V -> 7AA -> 7R -> 7Z     (all unchanged, outside this module)

HARD RULES:

  - NO INVENTION. A task never fabricates dimensions, hole diameters,
    plate values, member identities, positions, locations, attachment
    surfaces, bolt quantities or grades. Where the existing data has
    no value, the task says so (current_ai_value is None) and the
    answer remains unresolved until a human supplies it.
  - AI DATA IS PRESERVED EXACTLY. "M12" stays "M12", "SQ4 12mm" stays
    "SQ4 12mm", and neither is ever converted into a hole diameter
    ("Ø" never appears anywhere). current_ai_value is lossless.
  - NO PARALLEL ENGINEERING SYSTEM. The only correction
    representation is the existing 7W ConnectionReviewSupplement:
    build_resolved_supplement() writes the human's answers onto its
    existing fields (position, plate, holes, location, attachments,
    connected_member_marks, confirmed_ai_fields, review_status,
    connection_id), with the exact semantics 7W already defines
    (supplied value -> HUMAN_SUPPLEMENTED; confirmed field ->
    HUMAN_REVIEWED; confirm+supply on one field -> the existing
    conflict rule).
  - CONNECTION IDENTITY IS REVIEWER-SUPPLIED, NEVER INVENTED. The AI
    never supplies a connection identity (7W: "assigned at
    persistence, or given by the reviewer"), and 7Z never blocks on
    one. A REVIEW candidate whose supplement carries no connection
    identity gets exactly one PROVIDE_CONNECTION_IDENTITY task,
    addressing no blocker code; its answer is recorded on the
    supplement with no provenance label, exactly as 7W documents. An
    identity is never derived from page data, member marks or list
    order.
  - NO BLOCKER IS HIDDEN. Every 7Z blocker of a REVIEW connection is
    carried verbatim on its connection group, and every blocker code
    appears in the blocker_codes of at least one task. Tasks may
    group blockers only when one human answer genuinely supplies the
    data for all of them (SELECT_MEMBER_POSITION_ATTACHMENT); the
    grouped task lists every code it addresses.
  - NO BYPASS. Resolving a task records only that the human supplied
    the requested information. It changes no gate outcome: the
    existing 7V/7R/7Z/7AA gates re-decide when the project is
    re-evaluated. A resolution can never force AUTO, and never makes
    an invalid answer valid.
  - THREE KINDS OF ATTENTION, kept distinct: REVIEW tasks (missing or
    unresolved engineering information), the CONFIRM_AUTOMATION task
    (complete information awaiting explicit human confirmation), and
    warnings (carried verbatim, never converted into tasks).
  - ALLOWED CHOICES COME ONLY FROM AUTHORITATIVE EXISTING CONTEXT.
    Member choices come from the supplied member_rows (validated
    members) or the collection's known_member_marks — never from AI
    strings; with no context the choices are (). Position and
    attachment-surface choices are the existing geometry vocabulary
    START/END (app.cad_engine.connections.SUPPORTED_CONNECTION_POSITIONS,
    which the 7N adapter also uses for surface references; kept as a
    reference copy here because that module imports CadQuery — a
    consistency test pins the copy to the source).
  - DETERMINISTIC, READ-ONLY, NO I/O. No network, no Claude, no
    database, no FastAPI, no CAD geometry, no drawing generation.
    Everything returned is frozen plain data (no CadQuery objects).
    Inputs are never mutated; repeated builds are equal.
  - NO ENGINEERING JUDGEMENT. 7AC asks only for the inputs the
    existing gates require; whether an answer is engineering-correct
    is decided by 7V/7R/7Z alone.

BOUNDARY: a RESOLVED task means only "the human supplied the requested
information for this task". It is not engineering approval, and this
package never claims the drawing set is complete, that every
connection was found, or that anything is fabrication-ready.
"""
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_CONFLICT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_MALFORMED_FIELDS,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_PROVENANCE,
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_CONFIRM,
    AUTOMATION_DECISION_REVIEW,
    AUTOMATION_REASON_CONFIRMATION_REQUESTED,
    AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED,
    AutomationFinding,
)
from app.cad_engine.automation_pipeline import AutomationValidationFailure
from app.cad_engine.connection_review_package import (
    ENGINEERING_FIELDS,
    ConnectionReviewPackage,
    ConnectionReviewSupplement,
    build_review_report,
)
from app.cad_engine.project_automation import ProjectAutomationResult, ProjectIntakeScope
from app.cad_engine.project_connection_review import ProjectConnectionReviewCollection
from app.cad_engine.reviewed_connection_specification import REVIEW_STATUS_APPROVED

__all__ = [
    # task types (stable, machine-readable, mapped to existing 7Z blockers)
    "TASK_COMPLETE_REVIEW", "TASK_SELECT_MEMBER", "TASK_SELECT_POSITION",
    "TASK_PROVIDE_PLATE", "TASK_PROVIDE_HOLE_DIAMETER", "TASK_PROVIDE_LOCATION",
    "TASK_SELECT_ATTACHMENT", "TASK_SELECT_MEMBER_POSITION_ATTACHMENT",
    "TASK_CONFIRM_AI_VALUES", "TASK_REVIEW_MALFORMED_FIELDS", "TASK_RESOLVE_CONFLICT",
    "TASK_REVIEW_SPECIFICATION", "TASK_REVIEW_VALIDATION", "TASK_CONFIRM_AUTOMATION",
    "TASK_PROVIDE_CONNECTION_IDENTITY",
    "EXCEPTION_TASK_TYPES",
    # answer types (what a human resolution carries for each task type)
    "ANSWER_APPROVE_REVIEW", "ANSWER_MEMBER_SELECTION", "ANSWER_POSITION_VALUE",
    "ANSWER_PLATE_VALUE", "ANSWER_HOLES_VALUE", "ANSWER_LOCATION_VALUE",
    "ANSWER_ATTACHMENTS_VALUE", "ANSWER_MEMBER_POSITION_ATTACHMENTS",
    "ANSWER_CONFIRMED_FIELDS", "ANSWER_FIELD_DECISION", "ANSWER_ACKNOWLEDGMENT",
    "ANSWER_AUTOMATION_CONFIRMATION", "ANSWER_CONNECTION_IDENTITY",
    # field-decision actions (the existing 7W conflict-resolution semantics)
    "FIELD_DECISION_CONFIRM_AI", "FIELD_DECISION_SUPPLY", "FIELD_DECISION_CLEAR",
    # status
    "STATUS_OPEN", "STATUS_RESOLVED",
    "CONNECTION_POSITION_CHOICES",
    "EXCEPTION_RESOLUTION_SCOPE_STATEMENT",
    "ExceptionResolutionTask", "ConnectionExceptionTasks", "ExceptionResolutionPackage",
    "HumanResolution",
    "build_exception_resolution_package", "apply_human_resolution", "build_resolved_supplement",
]

# --------------------------------------------------------------------------------------
# Task types — one per kind of human input the existing gates require, mapped to the
# 7Z blocker codes they address (a task addresses at least one; grouping lists all).
# --------------------------------------------------------------------------------------
TASK_COMPLETE_REVIEW = "COMPLETE_REVIEW"                        # REVIEW_STATUS
TASK_SELECT_MEMBER = "SELECT_MEMBER"                            # MEMBER_IDENTITY
TASK_SELECT_POSITION = "SELECT_POSITION"                        # POSITION
TASK_PROVIDE_PLATE = "PROVIDE_PLATE"                            # PLATE
TASK_PROVIDE_HOLE_DIAMETER = "PROVIDE_HOLE_DIAMETER"            # HOLE_DIAMETER
TASK_PROVIDE_LOCATION = "PROVIDE_LOCATION"                      # LOCATION
TASK_SELECT_ATTACHMENT = "SELECT_ATTACHMENT"                    # ATTACHMENT
TASK_SELECT_MEMBER_POSITION_ATTACHMENT = "SELECT_MEMBER_POSITION_ATTACHMENT"  # the three above, grouped
TASK_CONFIRM_AI_VALUES = "CONFIRM_AI_VALUES"                    # PROVENANCE
TASK_REVIEW_MALFORMED_FIELDS = "REVIEW_MALFORMED_FIELDS"        # MALFORMED_FIELDS
TASK_RESOLVE_CONFLICT = "RESOLVE_CONFLICT"                      # CONFLICT
TASK_REVIEW_SPECIFICATION = "REVIEW_SPECIFICATION"              # SPECIFICATION
TASK_REVIEW_VALIDATION = "REVIEW_VALIDATION"                    # VALIDATION
TASK_CONFIRM_AUTOMATION = "CONFIRM_AUTOMATION"                  # a 7Z CONFIRM decision (no blockers)
TASK_PROVIDE_CONNECTION_IDENTITY = "PROVIDE_CONNECTION_IDENTITY"  # not a blocker: identity is never AI output
TASK_PROVIDE_MATERIAL_SPECIFICATION = "PROVIDE_MATERIAL_SPECIFICATION"  # not a blocker: material is never required
EXCEPTION_TASK_TYPES = (
    TASK_COMPLETE_REVIEW, TASK_SELECT_MEMBER, TASK_SELECT_POSITION,
    TASK_PROVIDE_PLATE, TASK_PROVIDE_HOLE_DIAMETER, TASK_PROVIDE_LOCATION,
    TASK_SELECT_ATTACHMENT, TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_CONFIRM_AI_VALUES, TASK_REVIEW_MALFORMED_FIELDS, TASK_RESOLVE_CONFLICT,
    TASK_REVIEW_SPECIFICATION, TASK_REVIEW_VALIDATION, TASK_CONFIRM_AUTOMATION,
    TASK_PROVIDE_CONNECTION_IDENTITY, TASK_PROVIDE_MATERIAL_SPECIFICATION,
)

# --------------------------------------------------------------------------------------
# Answer types — the structured payload a HumanResolution carries for each task type.
# Payloads are validated for SHAPE only (types); engineering validity is 7V/7R/7Z's job.
# --------------------------------------------------------------------------------------
ANSWER_APPROVE_REVIEW = "APPROVE_REVIEW"                        # payload: None
ANSWER_MEMBER_SELECTION = "MEMBER_SELECTION"                    # tuple[str, ...] of marks
ANSWER_POSITION_VALUE = "POSITION_VALUE"                        # str (START/END vocabulary)
ANSWER_PLATE_VALUE = "PLATE_VALUE"                              # dict: type/thickness_mm/width_mm/depth_mm
ANSWER_HOLES_VALUE = "HOLES_VALUE"                              # dict: quantity/diameter_mm/spacings
ANSWER_LOCATION_VALUE = "LOCATION_VALUE"                        # dict: x/y/z/rotation_x/y/z
ANSWER_ATTACHMENTS_VALUE = "ATTACHMENTS_VALUE"                  # tuple of {member_mark, surface_reference}
ANSWER_MEMBER_POSITION_ATTACHMENTS = "MEMBER_POSITION_ATTACHMENTS"  # (marks, position, attachments)
ANSWER_CONFIRMED_FIELDS = "CONFIRMED_FIELDS"                    # tuple[str, ...] of engineering fields
ANSWER_FIELD_DECISION = "FIELD_DECISION"                        # (field, action, value)
ANSWER_ACKNOWLEDGMENT = "ACKNOWLEDGMENT"                        # str | None (optional human note)
ANSWER_AUTOMATION_CONFIRMATION = "AUTOMATION_CONFIRMATION"      # payload: None
ANSWER_CONNECTION_IDENTITY = "CONNECTION_IDENTITY"              # str: the reviewer-given connection identifier
ANSWER_MATERIAL_VALUE = "MATERIAL_VALUE"                          # str: the material designation, verbatim

# FIELD_DECISION actions — exactly the existing 7W conflict semantics: a field may be
# confirmed as the AI value, supplied by the reviewer, or neither — never both.
FIELD_DECISION_CONFIRM_AI = "confirm_ai"   # keep the AI value, relabelled HUMAN_REVIEWED; drop a supplied value
FIELD_DECISION_SUPPLY = "supply"           # reviewer value, HUMAN_SUPPLEMENTED; drop the confirmation
FIELD_DECISION_CLEAR = "clear"             # drop both — the field falls back to its AI value, unconfirmed
FIELD_DECISION_ACTIONS = (FIELD_DECISION_CONFIRM_AI, FIELD_DECISION_SUPPLY, FIELD_DECISION_CLEAR)

STATUS_OPEN = "OPEN"
STATUS_RESOLVED = "RESOLVED"

# The existing connection-geometry vocabulary (app.cad_engine.connections.py
# SUPPORTED_CONNECTION_POSITIONS), which the 7N adapter also uses as the only accepted
# attachment surface references. Referenced rather than imported because that module
# imports CadQuery; a consistency test pins this copy to the source.
CONNECTION_POSITION_CHOICES = ("START", "END")

EXCEPTION_RESOLUTION_SCOPE_STATEMENT = (
    "This package describes only the human inputs the existing review and automation gates require "
    "for the candidates that were supplied. RESOLVED means only that the human supplied the requested "
    "information for a task — it is not engineering approval and it changes no gate outcome; the "
    "existing 7V/7R/7Z gates re-decide when the project is re-evaluated. Nothing here claims that the "
    "drawing set is complete, that every connection was found, or that anything is fabrication-ready."
)


@dataclass(frozen=True)
class ExceptionResolutionTask:
    """
    One structured question a human must answer before the existing
    system can continue, in a shape a future UI can render directly.

    `blocker_codes` lists EVERY 7Z blocker this task addresses (a
    grouped task lists all of them); `question` is the human question;
    `current_ai_value` is the relevant existing value, lossless (None
    means the existing data has nothing — the task is unresolved until
    a human supplies the value); `answer_type` is the payload shape a
    HumanResolution must carry; `allowed_choices` holds only
    authoritative existing choices (() when there are none — a UI
    must then accept a free answer, never a guessed list);
    `evidence_requirement` says what kind of evidence is required.
    `status` is OPEN or RESOLVED; `resolution` is set exactly when
    RESOLVED.

    `field_name` (J79) is the ONE engineering field this task addresses, or
    None. It is the review package's own vocabulary (`ENGINEERING_FIELDS`)
    and is STATED by the code that builds the task — never derived from
    `task_type`, `current_ai_value`, `answer_type`, blocker order or task
    order, and never guessed for a task that addresses several fields or
    none. None therefore means "this task does not address exactly one
    engineering field", which is a fact about the task and not a gap: a
    grouped task, a whole-connection task and a conflict task all state it
    truthfully. It defaults to None so that every task constructed before
    this field existed — including a payload recorded before it existed —
    reads back exactly as it was, and it is refused rather than coerced
    when it names something that is not an engineering field.
    """
    task_id: str
    task_type: str
    blocker_codes: tuple[str, ...]
    question: str
    current_ai_value: Any
    answer_type: str
    allowed_choices: tuple[str, ...]
    evidence_requirement: str
    field_name: str | None = None
    status: str = STATUS_OPEN
    resolution: "HumanResolution | None" = None

    def __post_init__(self) -> None:
        # Fail closed, and say so in the package's own vocabulary: a field name that is not
        # one of ENGINEERING_FIELDS is a caller's error and is never coerced, dropped or
        # reinterpreted as "no field". None is the stated absence and passes.
        if self.field_name is not None and self.field_name not in ENGINEERING_FIELDS:
            raise ValueError(
                f"field_name must be one of {list(ENGINEERING_FIELDS)} or None "
                f"(got {self.field_name!r}); a task states the field it addresses and a "
                "value outside the vocabulary is never coerced into one."
            )


@dataclass(frozen=True)
class ConnectionExceptionTasks:
    """
    One 7AB connection outcome turned into its task set, plus the
    connection's identity and its 7Z findings carried verbatim — so no
    blocker or warning is ever hidden, whatever the tasks group.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    connection_id: str | None
    source_page: Any
    detail_reference: Any
    decision: str
    validation_passed: bool
    validation_failure: AutomationValidationFailure | None
    blockers: tuple[AutomationFinding, ...]   # the complete 7Z blocker findings, verbatim
    warnings: tuple[AutomationFinding, ...]   # the 7Z per-outcome warnings, verbatim
    tasks: tuple[ExceptionResolutionTask, ...]


@dataclass(frozen=True)
class ExceptionResolutionPackage:
    """
    The whole project's exception-resolution contract. Counts and
    scope come from the 7AB result verbatim; `connection_tasks`
    carries every connection in submission order (AUTO connections
    have zero tasks — no pointless human work is invented);
    `open_task_count` is the number of OPEN tasks; `summary` restates
    the counts factually; `warnings` are the project-level warnings.
    """
    project_id: str | None
    connections_total: int
    auto_count: int
    confirm_count: int
    review_count: int
    intake_scope: ProjectIntakeScope
    connection_tasks: tuple[ConnectionExceptionTasks, ...]
    warnings: tuple[AutomationFinding, ...]
    open_task_count: int
    summary: tuple[str, ...]
    scope_statement: str = EXCEPTION_RESOLUTION_SCOPE_STATEMENT


@dataclass(frozen=True)
class HumanResolution:
    """
    What a human actually answered for one task — immutable, with the
    answer payload snapshot at creation (a later mutation of the
    caller's dicts cannot change it). `evidence` is the human's
    stated source for the answer (free text; may be empty). The
    answer payload's shape is validated against the task's
    answer_type by apply_human_resolution(); whether the answer is
    engineering-valid is decided only by the existing gates.
    """
    task_id: str
    task_type: str
    answer_type: str
    answer: Any
    evidence: str = ""

    def __post_init__(self):
        object.__setattr__(self, "answer", copy.deepcopy(self.answer))
        object.__setattr__(self, "evidence", copy.deepcopy(self.evidence))


# --------------------------------------------------------------------------------------
# Building the package from a 7AB result
# --------------------------------------------------------------------------------------
def _ai_field_value(report, field_name: str) -> Any:
    for entry in report.entries:
        if entry.field == field_name:
            return entry.ai_value
    return None


def _nominal_bolt_value(extraction) -> Any:
    """The AI's bolt readings, lossless: the preserved bolt records, or the raw malformed value."""
    if extraction.bolts:
        return tuple((b.quantity, b.size, b.grade, tuple(sorted(b.extra.items()))) for b in extraction.bolts)
    return copy.deepcopy(extraction.malformed_fields.get("bolts"))


def _member_choices(member_rows: Mapping[str, Mapping[str, object]] | None,
                    collection: ProjectConnectionReviewCollection) -> tuple[str, ...]:
    """Authoritative member choices only: validated member rows, else the collection's known marks, else ()."""
    if member_rows is not None:
        return tuple(member_rows)
    if collection.known_member_marks is not None:
        return tuple(collection.known_member_marks)
    return ()


def _conflict_current_values(package: ConnectionReviewPackage, field_name: str) -> tuple[str, Any, Any]:
    """(field, ai_value, reviewer-supplied value) for one conflicted field, lossless."""
    ai_value = _ai_field_value(build_review_report(package), field_name)
    supplied = getattr(package.supplement, field_name, None)
    return (field_name, ai_value, copy.deepcopy(supplied))


def _make_task(task_type: str, blocker_codes: Sequence[str], question: str, current_ai_value: Any,
               answer_type: str, allowed_choices: Sequence[str], evidence: str,
               package_id: str, index: int, *, field_name: str | None) -> ExceptionResolutionTask:
    """One task, with the field it addresses STATED by its caller (J79).

    `field_name` is keyword-only and has NO default, deliberately: a call site that does not
    say which field its task addresses is a TypeError rather than a task that silently
    reads as "no field". That is the whole difference between a boundary and a convention,
    and it is what keeps the value from being inferred here — this function copies what it
    is told and derives nothing, so no ordering, no answer type and no AI value can leak
    into it.
    """
    return ExceptionResolutionTask(
        task_id=f"{package_id}-T{index:02d}",
        task_type=task_type,
        blocker_codes=tuple(blocker_codes),
        question=question,
        current_ai_value=current_ai_value,
        answer_type=answer_type,
        allowed_choices=tuple(allowed_choices),
        evidence_requirement=evidence,
        field_name=field_name,
    )


def _review_tasks(
    candidate, outcome, member_choices: tuple[str, ...], *,
    request_material_specification: bool = False,
) -> tuple[ExceptionResolutionTask, ...]:
    """The deterministic task set for one REVIEW outcome, in 7Z's own blocker order. Every blocker
    code of the outcome appears in at least one task; each task lists every code it addresses. A
    candidate whose supplement carries no connection identity additionally gets one
    PROVIDE_CONNECTION_IDENTITY task — appended after the blocker tasks so their order and ids
    are unchanged. When `request_material_specification` is true and the candidate's review
    report shows material missing, one PROVIDE_MATERIAL_SPECIFICATION task is appended last (after
    identity, so existing ids never move) — material supplementation is an explicit project
    request, never a standing demand, because missing material is deliberately not a 7Z blocker."""
    package = candidate.package
    report = build_review_report(package)
    extraction = package.extraction
    codes = {b.code for b in outcome.blockers}
    tasks: list[ExceptionResolutionTask] = []
    package_id = candidate.review_package_id

    def add(task_type, blocker_codes, question, current_ai_value, answer_type, choices, evidence,
            *, field_name):
        tasks.append(_make_task(
            task_type, blocker_codes, question, current_ai_value, answer_type, choices, evidence,
            package_id, len(tasks) + 1, field_name=field_name,
        ))

    # 1. Review workflow status — "approved" is the one status 7V accepts; approval alone does
    # not make missing engineering information appear.
    if AUTOMATION_BLOCKER_REVIEW_STATUS in codes:
        current_status = package.supplement.review_status or extraction.persisted_review_status
        add(
            TASK_COMPLETE_REVIEW, (AUTOMATION_BLOCKER_REVIEW_STATUS,),
            "Complete the human review workflow for this connection.",
            current_status,
            ANSWER_APPROVE_REVIEW, (),
            "The existing review contract requires review_status 'approved' and human-owned provenance "
            "on every required engineering field. Approval alone does not make missing engineering "
            "information appear, and the 7V/7Z gates re-check everything once the workflow is complete.",
            field_name=None,
        )

    # 2-4. Member identity, position and attachment surface. When all three are blocked, one
    # question ("where does this connection attach?") genuinely supplies all three, so they group;
    # otherwise each gets its own task. The AI's member references are shown exactly as extracted.
    member_blocked = AUTOMATION_BLOCKER_MEMBER_IDENTITY in codes
    position_blocked = AUTOMATION_BLOCKER_POSITION in codes
    attachment_blocked = AUTOMATION_BLOCKER_ATTACHMENT in codes
    ai_marks = _ai_field_value(report, "connected_member_marks")
    if member_blocked and position_blocked and attachment_blocked:
        add(
            TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
            (AUTOMATION_BLOCKER_MEMBER_IDENTITY, AUTOMATION_BLOCKER_POSITION, AUTOMATION_BLOCKER_ATTACHMENT),
            "Where does this connection attach: which validated project member(s), at which position, "
            "on which attachment surface?",
            ai_marks,
            ANSWER_MEMBER_POSITION_ATTACHMENTS, member_choices,
            "Select the validated structural member(s) this connection attaches to, the explicit "
            "connection position (START/END), and the attachment surface for each identified member. "
            "AI-extracted references are shown exactly as extracted and are never reinterpreted as "
            "structural steel member marks.",
            field_name=None,
        )
    else:
        if member_blocked:
            add(
                TASK_SELECT_MEMBER, (AUTOMATION_BLOCKER_MEMBER_IDENTITY,),
                "Which validated project member(s) does this connection attach to?",
                ai_marks,
                ANSWER_MEMBER_SELECTION, member_choices,
                "Select the validated structural member(s) this connection attaches to. AI-extracted "
                "references are shown exactly as extracted and are never reinterpreted as structural "
                "steel member marks.",
                field_name="connected_member_marks",
            )
        if position_blocked:
            add(
                TASK_SELECT_POSITION, (AUTOMATION_BLOCKER_POSITION,),
                "Where on the member is this connection (position)?",
                None,
                ANSWER_POSITION_VALUE, CONNECTION_POSITION_CHOICES,
                "Provide the explicit connection position required by the reviewed connection contract. "
                "Position is never inferred from list order, page context or member geometry.",
                field_name="position",
            )
        if attachment_blocked:
            add(
                TASK_SELECT_ATTACHMENT, (AUTOMATION_BLOCKER_ATTACHMENT,),
                "Which attachment surface applies to each connected member?",
                None,
                ANSWER_ATTACHMENTS_VALUE, CONNECTION_POSITION_CHOICES,
                "Select the explicit attachment surface/reference for each identified member (the "
                "existing attachment contract accepts START/END). A surface reference is never "
                "defaulted to END or START.",
                field_name="attachments",
            )

    # 5. Provenance — AI values the reviewer has not explicitly confirmed or replaced.
    if AUTOMATION_BLOCKER_PROVENANCE in codes:
        pending = tuple(
            (field, _ai_field_value(report, field)) for field in report.needs_confirmation
        )
        add(
            TASK_CONFIRM_AI_VALUES, (AUTOMATION_BLOCKER_PROVENANCE,),
            "Confirm each AI-extracted value you accept — or replace it with your own value: "
            + ", ".join(f"'{field}'" for field, _ in pending),
            pending,
            ANSWER_CONFIRMED_FIELDS, (),
            "Each field still carrying an AI-extracted value must be explicitly confirmed "
            "(HUMAN_REVIEWED) or replaced by the reviewer (HUMAN_SUPPLEMENTED). Confirming and "
            "supplying the same field is ambiguous and is rejected by the existing review package.",
            field_name=None,
        )

    # 6. Plate — the AI's reading is shown, but a reviewed plate must be explicit.
    if AUTOMATION_BLOCKER_PLATE in codes:
        add(
            TASK_PROVIDE_PLATE, (AUTOMATION_BLOCKER_PLATE,),
            "Is this plate actually present? Provide the explicit plate.",
            _ai_field_value(report, "plate"),
            ANSWER_PLATE_VALUE, (),
            "Provide the explicit plate the reviewed connection contract consumes: exactly one plate "
            "with type, thickness_mm, width_mm and depth_mm. AI-extracted plate readings are shown "
            "exactly as extracted; they are not reviewed values.",
            field_name="plate",
        )

    # 7. Hole diameter — nominal bolt sizes are shown exactly as extracted and are never a diameter.
    if AUTOMATION_BLOCKER_HOLE_DIAMETER in codes:
        add(
            TASK_PROVIDE_HOLE_DIAMETER, (AUTOMATION_BLOCKER_HOLE_DIAMETER,),
            "What is the specified hole diameter?",
            _nominal_bolt_value(extraction),
            ANSWER_HOLES_VALUE, (),
            "Provide the specified hole diameter: an explicit numeric hole-void diameter "
            "(diameter_mm) with quantity and spacing. Nominal bolt sizes are shown exactly as "
            "extracted; a nominal bolt size is not a hole diameter and is never converted into one.",
            field_name="holes",
        )

    # 8. Location — the AI schema has no location field, so there is nothing to show.
    if AUTOMATION_BLOCKER_LOCATION in codes:
        add(
            TASK_PROVIDE_LOCATION, (AUTOMATION_BLOCKER_LOCATION,),
            "Where is this connection located in the project space?",
            None,
            ANSWER_LOCATION_VALUE, (),
            "Provide the explicit project-space location (x/y/z with rotations) required by the "
            "reviewed connection contract. Coordinates are never derived from member placements, "
            "plate dimensions or page position.",
            field_name="location",
        )

    # 9. Malformed AI fields — preserved verbatim, never coerced.
    if AUTOMATION_BLOCKER_MALFORMED_FIELDS in codes:
        malformed = tuple(sorted(extraction.malformed_fields.items()))
        add(
            TASK_REVIEW_MALFORMED_FIELDS, (AUTOMATION_BLOCKER_MALFORMED_FIELDS,),
            "The preserved AI extraction contains unparseable field(s) "
            + ", ".join(f"'{key}'" for key, _ in malformed)
            + ". Review them against the drawing and supply the affected engineering fields explicitly.",
            malformed,
            ANSWER_ACKNOWLEDGMENT, (),
            "Unparseable AI values are preserved verbatim and are never coerced into engineering "
            "fields; the affected fields must be supplied by the reviewer through their own tasks. "
            "This task grants nothing by itself.",
            field_name=None,
        )

    # 10. Conflicting reviewer decisions — one task per conflicted field, resolved through the
    # existing supplement semantics (confirm the AI value, supply your own, or drop both).
    if AUTOMATION_BLOCKER_CONFLICT in codes:
        conflict_issue_codes = {"CONFIRM_AND_SUPPLY_CONFLICT", "UNKNOWN_CONFIRMED_FIELD"}
        for issue in report.issues:
            if issue.code not in conflict_issue_codes:
                continue
            add(
                TASK_RESOLVE_CONFLICT, (AUTOMATION_BLOCKER_CONFLICT,),
                f"Please resolve this conflicting information: {issue.message}",
                _conflict_current_values(package, issue.field),
                ANSWER_FIELD_DECISION, (),
                "The reviewer's decisions for this field conflict and neither is applied by the "
                "existing review package. Decide: confirm the AI value, supply your own value, or "
                "drop both — never both for the same field.",
                # One task per conflicted field, and still None (J79): the issue the task is
                # built from may name a field outside ENGINEERING_FIELDS entirely
                # (`UNKNOWN_CONFIRMED_FIELD`, raised for a name that "is not a reviewable
                # engineering field"), so no single field can be stated uniformly for this
                # task type. The field is not guessed from the issue and not chosen from the
                # answer.
                field_name=None,
            )

    # 11. The 7V completeness rejection, its error carried verbatim.
    if AUTOMATION_BLOCKER_SPECIFICATION in codes:
        specification = next(b for b in outcome.blockers if b.code == AUTOMATION_BLOCKER_SPECIFICATION)
        add(
            TASK_REVIEW_SPECIFICATION, (AUTOMATION_BLOCKER_SPECIFICATION,),
            "Resolve the missing or invalid reviewed engineering fields the specification contract "
            f"rejects: {specification.message}",
            None,
            ANSWER_ACKNOWLEDGMENT, (),
            "Resolve the missing reviewed engineering fields required by the specification contract. "
            "The existing 7V completeness gate remains authoritative; supplying or confirming the "
            "fields it names resolves this task, and this task itself grants nothing.",
            field_name=None,
        )

    # 12. Failed downstream validation — the preserved failure, verbatim.
    if AUTOMATION_BLOCKER_VALIDATION in codes:
        failure = outcome.validation_failure
        detail = (
            f" {failure.stage}: {failure.error_code}: {failure.message}"
            if failure is not None else ""
        )
        add(
            TASK_REVIEW_VALIDATION, (AUTOMATION_BLOCKER_VALIDATION,),
            f"The downstream validation gate (7R) did not pass:{detail} Review the connection's "
            "engineering fields and correct what the failure describes.",
            None,
            ANSWER_ACKNOWLEDGMENT, (),
            "Automated progression requires validated geometry (7R). Correct the reviewed "
            "engineering fields the failure describes; the existing pipeline re-validates and 7Z "
            "re-decides. This task itself grants nothing.",
            field_name=None,
        )

    # 13. Connection identity — the one non-blocker task, appended after the 7Z blocker tasks so
    # their order and ids are unchanged. The AI never supplies connection identity (7W's
    # supplement accepts it "assigned at persistence, or given by the reviewer"), 7Z never blocks
    # on one, and the fabrication-output gate (7AE) requires a specification that carries one —
    # so a candidate without identity gets exactly this task, resolved when the reviewer supplies
    # an identifier. No provenance label is ever attached (7W's contract).
    if package.supplement.connection_id is None:
        add(
            TASK_PROVIDE_CONNECTION_IDENTITY, (),
            "What connection identity should this connection carry?",
            None,
            ANSWER_CONNECTION_IDENTITY, (),
            "Connection identity is never extracted by the AI (7W accepts it 'assigned at "
            "persistence, or given by the reviewer'). Supply the identifier this connection "
            "carries into the fabrication-output chain: a non-empty identifier string. It is "
            "never derived from page data, member marks or list order.",
            # None, and it is not an oversight: `connection_id` is NOT a member of
            # ENGINEERING_FIELDS (the review package's provenance vocabulary is
            # connected_member_marks, position, plate, holes, location, attachments, material).
            # Stating it here would both widen a closed vocabulary and hand every later
            # reader a field name that vocabulary does not contain, so the task states the
            # honest fact instead: it resolves no ENGINEERING_FIELD. The identity it asks
            # for is real and is still asked for — it is simply not one of the seven fields
            # this boundary binds. `review_contract._TASK_FIELDS` labels this task type
            # `connection_id`, which is the PRESENTATION's own wider label; that
            # pre-existing difference is left exactly as it was.
            field_name=None,
        )

    # 14. Material specification — the second non-blocker task, appended after identity so the
    # existing task ids and order never change. Emitted ONLY when the caller asked the reviewer
    # for material (`request_material_specification`) AND the review report shows no material
    # value. Missing material is deliberately NOT a 7Z blocker: a connection without a specified
    # material proceeds to output carrying MATERIAL NOT SPECIFIED, and the fabricator acceptance
    # layer reports that. This task is the explicit, reviewer-facing route for supplying a grade
    # the source drawing does not state — it never appears unless requested.
    if request_material_specification and "material" in report.missing:
        add(
            TASK_PROVIDE_MATERIAL_SPECIFICATION, (),
            "The source drawing states no material grade for this connection. If the project's "
            "own documentation specifies one, supply it exactly as that document states it.",
            None,
            ANSWER_MATERIAL_VALUE, (),
            "Supply the material designation exactly as the project's own documentation states "
            "it (e.g. '300PLUS' or 'AS/NZS 3678-300' — never a converted or paraphrased form). "
            "Material is never inferred from member sections, project conventions, history or "
            "filenames; if no authoritative source states a grade, do not invent one — the "
            "drawing continues to carry MATERIAL NOT SPECIFIED and the production acceptance "
            "reports this task as unresolved.",
            field_name="material",
        )

    remaining = codes - {t for task in tasks for t in task.blocker_codes}
    if remaining:
        # A 7Z blocker this contract does not map would silently vanish; fail loudly instead.
        raise ValueError(
            f"7Z reported blocker code(s) {sorted(remaining)} for {package_id} that the exception "
            "resolution contract has no task for; extend the contract before it can hide a blocker."
        )
    return tuple(tasks)


def _confirm_task(
    candidate, outcome, *, request_material_specification: bool = False,
) -> tuple[ExceptionResolutionTask, ...]:
    items = [r.message for r in outcome.reasons if r.code == AUTOMATION_REASON_CONFIRMATION_REQUESTED]
    if any(r.code == AUTOMATION_REASON_VALIDATION_NOT_EVIDENCED for r in outcome.reasons):
        items.append("validation evidence has not been supplied yet")
    question = "Explicitly confirm this connection may proceed toward automated output"
    question += (": " + "; ".join(items)) if items else "."
    tasks = (
        _make_task(
            TASK_CONFIRM_AUTOMATION, (), question, None, ANSWER_AUTOMATION_CONFIRMATION, (),
            "The reviewed information satisfies every automation requirement; only explicit human "
            "confirmation remains. After confirmation the existing automation pipeline is re-run and "
            "7Z re-decides — the confirmation itself never forces AUTO.",
            candidate.review_package_id, 1,
            field_name=None,
        ),
    )
    # The same explicitly-requested material supplementation as the REVIEW task set: appended
    # AFTER the confirmation task (whose id -T01 never moves) when the review report shows no
    # material value. Missing material is not a blocker — this task is the reviewer-facing route.
    if request_material_specification and "material" in build_review_report(candidate.package).missing:
        tasks += (
            _make_task(
                TASK_PROVIDE_MATERIAL_SPECIFICATION, (),
                "The source drawing states no material grade for this connection. If the project's "
                "own documentation specifies one, supply it exactly as that document states it.",
                None,
                ANSWER_MATERIAL_VALUE, (),
                "Supply the material designation exactly as the project's own documentation states "
                "it (e.g. '300PLUS' or 'AS/NZS 3678-300' — never a converted or paraphrased form). "
                "Material is never inferred from member sections, project conventions, history or "
                "filenames; if no authoritative source states a grade, do not invent one — the "
                "drawing continues to carry MATERIAL NOT SPECIFIED and the production acceptance "
                "reports this task as unresolved.",
                candidate.review_package_id, 2,
                field_name="material",
            ),
        )
    return tasks


def build_exception_resolution_package(
    project_result: ProjectAutomationResult,
    collection: ProjectConnectionReviewCollection,
    *,
    member_rows: Mapping[str, Mapping[str, object]] | None = None,
    request_material_specification: bool = False,
) -> ExceptionResolutionPackage:
    """
    Converts one 7AB project automation result (plus the 7X collection
    it was evaluated from, for the candidates' source facts) into the
    structured exception-resolution contract, deterministically.

    Every connection is represented in submission order: AUTO
    connections get zero tasks; a CONFIRM connection gets exactly one
    confirmation task; a REVIEW connection gets one task set covering
    every 7Z blocker it carries (all findings stay visible on the
    connection group), plus one PROVIDE_CONNECTION_IDENTITY task when
    its supplement carries no connection identity. `member_rows` (the
    validated member context the
    7AA pipeline consumed) is the ONLY source of member choices; the
    collection's known_member_marks are the fallback; with neither,
    member choices are () — nothing is ever guessed from AI strings.

    `request_material_specification` (default False) is the explicit,
    reviewer-facing request to supplement material: when true, every
    non-AUTO candidate whose review report shows no material value gets
    one PROVIDE_MATERIAL_SPECIFICATION task appended after its other
    tasks. It is a scope decision the CALLER makes for the project —
    missing material remains a non-blocker either way, and a task
    requested but never answered stays OPEN so the production
    acceptance reports it. AUTO candidates are never given the task:
    they have no human interaction point, and asking could never be
    answered.

    Read-only and deterministic: nothing is mutated, and two builds
    from equal inputs return equal packages.
    """
    if not isinstance(project_result, ProjectAutomationResult):
        raise TypeError(
            f"project_result must be a ProjectAutomationResult (got {type(project_result).__name__}); "
            "the exception contract consumes the 7AB project result."
        )
    if not isinstance(collection, ProjectConnectionReviewCollection):
        raise TypeError(
            f"collection must be a ProjectConnectionReviewCollection (got {type(collection).__name__})."
        )
    if not isinstance(request_material_specification, bool):
        raise TypeError(
            f"request_material_specification must be a bool (got "
            f"{type(request_material_specification).__name__})."
        )
    if len(project_result.connection_results) != len(collection.candidates):
        raise ValueError(
            f"project_result has {len(project_result.connection_results)} connection results for "
            f"{len(collection.candidates)} collection candidates; they must align one-to-one."
        )
    by_id = {c.review_package_id: c for c in collection.candidates}
    for outcome in project_result.connection_results:
        if outcome.review_package_id not in by_id:
            raise ValueError(
                f"project_result names review_package_id {outcome.review_package_id!r} that is not in "
                "the collection; the exception contract never pairs outcomes with different candidates."
            )

    member_choices = _member_choices(member_rows, collection)
    groups: list[ConnectionExceptionTasks] = []
    for outcome in project_result.connection_results:
        candidate = by_id[outcome.review_package_id]
        extraction = candidate.package.extraction
        if outcome.decision == AUTOMATION_DECISION_REVIEW:
            tasks = _review_tasks(
                candidate, outcome, member_choices,
                request_material_specification=request_material_specification,
            )
        elif outcome.decision == AUTOMATION_DECISION_CONFIRM:
            tasks = _confirm_task(
                candidate, outcome,
                request_material_specification=request_material_specification,
            )
        else:
            tasks = ()  # AUTO: no pointless human work
        groups.append(ConnectionExceptionTasks(
            review_package_id=candidate.review_package_id,
            submission_index=candidate.submission_index,
            source_identity=candidate.source_identity,
            connection_id=candidate.package.supplement.connection_id,
            source_page=extraction.source_page,
            detail_reference=extraction.detail_reference,
            decision=outcome.decision,
            validation_passed=outcome.validation_passed,
            validation_failure=outcome.validation_failure,
            blockers=outcome.blockers,
            warnings=outcome.warnings,
            tasks=tasks,
        ))

    total_tasks = sum(len(g.tasks) for g in groups)
    summary_lines = list(project_result.summary)
    summary_lines.extend([
        f"tasks = {total_tasks}",
        f"open_tasks = {total_tasks}",
        f"connections_needing_attention = {project_result.review_count + project_result.confirm_count}",
        f"SteelSpec found {total_tasks} task(s) that need your attention.",
    ])
    return ExceptionResolutionPackage(
        project_id=project_result.project_id,
        connections_total=project_result.connections_total,
        auto_count=project_result.auto_count,
        confirm_count=project_result.confirm_count,
        review_count=project_result.review_count,
        intake_scope=project_result.intake_scope,
        connection_tasks=tuple(groups),
        warnings=project_result.warnings,
        open_task_count=total_tasks,
        summary=tuple(summary_lines),
    )


# --------------------------------------------------------------------------------------
# Applying a human resolution
# --------------------------------------------------------------------------------------
def _validate_payload(answer_type: str, answer: Any) -> Any:
    """Shape-only validation (types); never an engineering check. Returns the payload to store."""
    if answer_type in (ANSWER_APPROVE_REVIEW, ANSWER_AUTOMATION_CONFIRMATION):
        if answer is not None:
            raise ValueError(f"{answer_type} carries no answer payload (got {answer!r}).")
        return None
    if answer_type == ANSWER_POSITION_VALUE:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"{answer_type} requires a non-empty position string (got {answer!r}).")
        return answer
    if answer_type == ANSWER_CONNECTION_IDENTITY:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(
                f"{answer_type} requires a non-empty identifier string (got {answer!r})."
            )
        return answer
    if answer_type == ANSWER_MATERIAL_VALUE:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(
                f"{answer_type} requires a non-empty material designation string (got {answer!r}). "
                "Material is never invented, defaulted or blanked."
            )
        return answer  # verbatim — never stripped, normalised or converted
    if answer_type == ANSWER_ACKNOWLEDGMENT:
        if answer is not None and not isinstance(answer, str):
            raise ValueError(f"{answer_type} requires a note string or None (got {answer!r}).")
        return answer
    if answer_type in (ANSWER_MEMBER_SELECTION, ANSWER_CONFIRMED_FIELDS):
        if not isinstance(answer, (tuple, list)) or not answer or not all(isinstance(v, str) and v for v in answer):
            raise ValueError(f"{answer_type} requires a non-empty sequence of non-empty strings (got {answer!r}).")
        return tuple(answer)
    if answer_type == ANSWER_ATTACHMENTS_VALUE:
        if not isinstance(answer, (tuple, list)) or not answer or not all(isinstance(a, Mapping) for a in answer):
            raise ValueError(
                f"{answer_type} requires a non-empty sequence of {{member_mark, surface_reference}} "
                f"mappings (got {answer!r})."
            )
        return tuple(copy.deepcopy(dict(a)) for a in answer)
    if answer_type in (ANSWER_PLATE_VALUE, ANSWER_HOLES_VALUE, ANSWER_LOCATION_VALUE):
        if not isinstance(answer, Mapping):
            raise ValueError(f"{answer_type} requires a mapping of field values (got {answer!r}).")
        return copy.deepcopy(dict(answer))
    if answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        if not (isinstance(answer, (tuple, list)) and len(answer) == 3):
            raise ValueError(f"{answer_type} requires (member_marks, position, attachments) (got {answer!r}).")
        marks, position, attachments = answer
        return (
            _validate_payload(ANSWER_MEMBER_SELECTION, marks),
            _validate_payload(ANSWER_POSITION_VALUE, position),
            _validate_payload(ANSWER_ATTACHMENTS_VALUE, attachments),
        )
    if answer_type == ANSWER_FIELD_DECISION:
        if not (isinstance(answer, (tuple, list)) and len(answer) == 3):
            raise ValueError(f"{answer_type} requires (field, action, value) (got {answer!r}).")
        field, action, value = answer
        if not isinstance(field, str) or field not in ENGINEERING_FIELDS:
            raise ValueError(
                f"{answer_type} field must be one of {list(ENGINEERING_FIELDS)} (got {field!r})."
            )
        if action not in FIELD_DECISION_ACTIONS:
            raise ValueError(f"{answer_type} action must be one of {list(FIELD_DECISION_ACTIONS)} (got {action!r}).")
        if action == FIELD_DECISION_SUPPLY and value is None:
            raise ValueError(f"{answer_type} action 'supply' requires a value.")
        return (field, action, copy.deepcopy(value))
    raise ValueError(f"unknown answer_type {answer_type!r}.")


def apply_human_resolution(
    package: ExceptionResolutionPackage, resolution: HumanResolution,
) -> ExceptionResolutionPackage:
    """
    Records one human answer: returns a NEW package in which exactly the
    addressed task is RESOLVED and carries the resolution; every other
    task is the same object. An unknown task_id, an already-resolved
    task, or a payload that does not match the task's answer_type are
    programming errors and raise. Nothing here changes any gate outcome
    and nothing is mutated.
    """
    if not isinstance(resolution, HumanResolution):
        raise TypeError(f"resolution must be a HumanResolution (got {type(resolution).__name__}).")
    target = None
    for group in package.connection_tasks:
        for task in group.tasks:
            if task.task_id == resolution.task_id:
                target = task
                break
        if target is not None:
            break
    if target is None:
        raise ValueError(f"No task has task_id {resolution.task_id!r}.")
    if target.status != STATUS_OPEN:
        raise ValueError(f"Task {target.task_id!r} is already {target.status}; a task is resolved once.")
    if resolution.answer_type != target.answer_type:
        raise ValueError(
            f"Task {target.task_id!r} ({target.task_type}) requires answer_type "
            f"{target.answer_type!r}, got {resolution.answer_type!r}."
        )
    validated = _validate_payload(target.answer_type, resolution.answer)
    stored = HumanResolution(
        task_id=resolution.task_id,
        task_type=target.task_type,
        answer_type=resolution.answer_type,
        answer=validated,
        evidence=resolution.evidence,
    )
    replaced = replace(target, status=STATUS_RESOLVED, resolution=stored)
    new_groups: list[ConnectionExceptionTasks] = []
    for group in package.connection_tasks:
        if any(t.task_id == target.task_id for t in group.tasks):
            group = replace(
                group, tasks=tuple(replaced if t.task_id == target.task_id else t for t in group.tasks),
            )
        new_groups.append(group)
    return replace(
        package,
        connection_tasks=tuple(new_groups),
        open_task_count=package.open_task_count - 1,
        summary=package.summary + (f"{target.task_id}: RESOLVED ({target.task_type}).",),
    )


# --------------------------------------------------------------------------------------
# Resolutions -> the existing 7W supplement (the one correction representation)
# --------------------------------------------------------------------------------------
_SUPPLIED_FIELD_BY_ANSWER = {
    ANSWER_MEMBER_SELECTION: "connected_member_marks",
    ANSWER_POSITION_VALUE: "position",
    ANSWER_PLATE_VALUE: "plate",
    ANSWER_HOLES_VALUE: "holes",
    ANSWER_LOCATION_VALUE: "location",
    ANSWER_ATTACHMENTS_VALUE: "attachments",
    ANSWER_MATERIAL_VALUE: "material",
}


def build_resolved_supplement(
    package: ConnectionReviewPackage,
    resolutions: Sequence[HumanResolution],
) -> ConnectionReviewSupplement:
    """
    Writes the human answers onto the EXISTING 7W supplement semantics
    and returns the resulting ConnectionReviewSupplement (a new object;
    `package` is never mutated). This is the entire correction path —
    7AC defines no second correction representation.

    The caller then applies it through the existing mechanism
    (7X update_candidate_supplement / create_review_package) and
    re-runs 7AA/7AB; the 7V/7R/7Z gates re-decide everything. Answers
    are applied in the order given (a later answer to the same field
    overrides an earlier one). Pure confirmations and acknowledgments
    (AUTOMATION_CONFIRMATION, ACKNOWLEDGMENT) change no supplement
    field. A connection-identity answer sets supplement.connection_id
    — an identifier, not an engineering field, so it carries no
    provenance label (7W's documented contract) and takes no part in
    the confirm/supply conflict rule. The existing conflict rule is
    honoured, never bypassed:
    confirming a field drops a supplied value for it, and supplying a
    field drops its confirmation — the ambiguous both-states is never
    produced here.
    """
    if not isinstance(package, ConnectionReviewPackage):
        raise TypeError(f"package must be a ConnectionReviewPackage (got {type(package).__name__}).")
    base = copy.deepcopy(package.supplement)
    confirmed = set(base.confirmed_ai_fields)
    updates: dict[str, Any] = {}
    supplied: dict[str, Any] = {
        "connected_member_marks": base.connected_member_marks,
        "position": base.position,
        "plate": base.plate,
        "holes": base.holes,
        "location": base.location,
        "attachments": base.attachments,
        "material": base.material,
    }

    for resolution in resolutions:
        answer_type, answer = resolution.answer_type, resolution.answer
        if answer_type == ANSWER_APPROVE_REVIEW:
            updates["review_status"] = REVIEW_STATUS_APPROVED
        elif answer_type == ANSWER_CONFIRMED_FIELDS:
            for field in answer:
                confirmed.add(field)
                supplied[field] = None  # confirming means accepting the AI value; never both states
        elif answer_type == ANSWER_FIELD_DECISION:
            field, action, value = answer
            if action == FIELD_DECISION_CONFIRM_AI:
                confirmed.add(field)
                supplied[field] = None
            elif action == FIELD_DECISION_SUPPLY:
                confirmed.discard(field)
                supplied[field] = copy.deepcopy(value)
            else:  # FIELD_DECISION_CLEAR
                confirmed.discard(field)
                supplied[field] = None
        elif answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
            marks, position, attachments = answer
            supplied["connected_member_marks"] = list(marks)
            supplied["position"] = position
            supplied["attachments"] = [dict(a) for a in attachments]
        elif answer_type == ANSWER_CONNECTION_IDENTITY:
            # Identity is an identifier, not an engineering field: no provenance label and no
            # conflict-rule participation (there is no confirm path for it — the AI never
            # supplies one).
            updates["connection_id"] = copy.deepcopy(answer)
        elif answer_type in _SUPPLIED_FIELD_BY_ANSWER:
            field = _SUPPLIED_FIELD_BY_ANSWER[answer_type]
            if field == "attachments":
                supplied[field] = [dict(a) for a in answer]
            elif field == "connected_member_marks":
                supplied[field] = list(answer)
            elif field == "position":
                supplied[field] = copy.deepcopy(answer)  # the existing START/END string, verbatim
            elif field == "material":
                supplied[field] = copy.deepcopy(answer)  # the material designation, verbatim
            else:
                supplied[field] = copy.deepcopy(dict(answer))
        elif answer_type in (ANSWER_AUTOMATION_CONFIRMATION, ANSWER_ACKNOWLEDGMENT):
            continue  # no supplement change; the pipeline re-run decides
        else:
            raise ValueError(f"unknown answer_type {answer_type!r}.")

    updates["confirmed_ai_fields"] = frozenset(confirmed)
    for field, value in supplied.items():
        # None clears the reviewer's supplied value explicitly — confirming a field or dropping both
        # sides of a conflict must remove the supplied value, never leave it to clash again in 7W.
        updates[field] = value
    return replace(base, **updates)
