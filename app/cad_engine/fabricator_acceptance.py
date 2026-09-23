"""Milestone 7AS — Fabricator Acceptance Test / Real-World Deliverable Validation.

7AR proved that an accepted production job produces a deterministic
fabrication drawing package. 7AS asks the question the package itself
cannot answer:

    If we hand the package to a competent steel fabricator, can they
    understand what each drawing represents, identify the members and
    the connection, understand the connection geometry, and identify
    any information that prevents fabrication — without SteelSpec
    inventing or silently filling gaps?

This is an ACCEPTANCE/EVALUATION boundary, not a pipeline stage:

    FabricationDrawingPackage (7AR — the frozen deliverable record)
            |   + the actual packaged PDFs on disk (the deliverable)
            v
    evaluate_fabricator_acceptance()            (7AS)
            |   acceptance_answers = the reviewer's per-checklist
            |   verdicts (PASS / FAIL / NOT_APPLICABLE + findings)
            v
    FabricatorAcceptance (frozen)
        ACCEPTED / NOT_ACCEPTED / REFUSED

MILESTONE 7AV: the checklist's member rows are sized per drawing from
the drawing's own recorded evidence — the recorded attachment
semantics — so a connection with three or more recorded members gets
one MEMBER_x_IDENTIFIED row per member (MEMBER C, MEMBER D, ...),
each checked against its own recorded mark and the drawing's own
member row. The two-member baseline and the established single-member
declaration are unchanged (NOT_APPLICABLE on a member row beyond the
first declares that row genuinely absent for that drawing; a row the
drawing itself shows can never be declared away), and the
reconciliation matrix below is UNCHANGED — a PASS can still never
invent missing evidence, so a member row the deliverable omits is
either honestly declared away, FAILed with a finding, or refuses the
evaluation.

HARD RULES (never broken):

  * NO VERDICT INJECTION. There is no force/status/approved parameter;
    the result is derived from the package records, the actual PDF
    text, the package files on disk, and the acceptance answers.
  * THE EVALUATION OPERATES ON THE DELIVERABLE. Every drawing's
    evidence is scanned from the actual packaged PDF text plus the
    package's recorded engineering decisions — never from the 7AR
    `observed` projection (which came from the same PDF and would be
    circular), never from filenames alone, never from file existence
    alone. A tampered manifest, a missing or rehashed drawing file and
    an unparsable PDF each refuse the evaluation.
  * NOTHING IS INVENTED. A missing value is reported missing — never
    converted (a bolt size is never a hole diameter, and a timber size
    is never a hole diameter), never inferred from visual proximity (a
    plate drawn near a member end is not "an END connection"), never
    defaulted. The evaluation never mutates the package, the workflow,
    or any file.
  * THE REVIEWER IS THE AUTHORITY FOR JUDGMENT. The evaluator derives
    deterministic EVIDENCE (PRESENT / MISSING / CONTRADICTED) and
    deterministic findings; whether a gap blocks fabrication is the
    reviewer's FAIL verdict, which must carry a finding. A PASS over
    missing or contradicted evidence is a REFUSED evaluation — a PASS
    can never invent missing evidence. A NOT_APPLICABLE answer is only
    allowed where the evidence is genuinely absent.
  * PROVENANCE AND AI VALUES PASS THROUGH UNTOUCHED. The result
    references the package audit; it never rewrites provenance labels
    or AI observations. The existing vocabulary
    (AI_EXTRACTED / HUMAN_REVIEWED / HUMAN_SUPPLEMENTED) is the only
    one used.
  * DETERMINISTIC. Same package + same answers -> identical frozen
    result. No timestamps, no randomness, no mutation, no I/O beyond
    reading the deliverable files.

RECONCILIATION MATRIX (answer x evidence; anything else is a REFUSED
evaluation — invalid input is a refusal, never silently continued):

    answer           PRESENT        MISSING        CONTRADICTED
    PASS             accepted       REFUSED        REFUSED
    FAIL             accepted*      accepted*      accepted*
    NOT_APPLICABLE   REFUSED        accepted       REFUSED
    * the finding is required on every FAIL
"""

import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.cad_engine.exception_resolution import (
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
    TASK_SELECT_POSITION,
)
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_READY,
    FabricationDrawingPackage,
)
from app.cad_engine.multi_member_connection import MEMBER_LABEL_LETTERS, member_label
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
)

__all__ = [
    "ANSWER_PASS", "ANSWER_FAIL", "ANSWER_NOT_APPLICABLE", "ANSWER_VALUES",
    "EVIDENCE_PRESENT", "EVIDENCE_MISSING", "EVIDENCE_CONTRADICTED", "EVIDENCE_STATUSES",
    "FINDING_AMBIGUITY", "FINDING_CONTRADICTION", "FINDING_MISSING_INFORMATION",
    "FINDING_NOTE", "FINDING_KINDS",
    "CHECKLIST_ITEM_PROJECT_IDENTIFIED", "CHECKLIST_ITEM_CONNECTION_IDENTIFIED",
    "CHECKLIST_ITEM_MEMBER_A_IDENTIFIED", "CHECKLIST_ITEM_MEMBER_B_IDENTIFIED",
    "CHECKLIST_ITEM_CONNECTION_LOCATION", "CHECKLIST_ITEM_PLATE_INFORMATION",
    "CHECKLIST_ITEM_HOLE_INFORMATION", "CHECKLIST_ITEM_UNITS_CLEAR",
    "CHECKLIST_ITEM_DRAWING_IDENTITY", "CHECKLIST_ITEM_AMBIGUITY",
    "CHECKLIST_ITEM_VISUAL_CONTRADICTION", "CHECKLIST_ITEM_FABRICATION_BLOCKING",
    "CHECKLIST_ITEM_ENGINEERING_ADMIN", "CHECKLIST_ITEM_TRACEABILITY",
    "CHECKLIST_ITEM_PACKAGE_COMPLETE",
    "CHECKLIST_ITEMS", "DRAWING_CHECKLIST_ITEMS", "CHECKLIST_QUESTIONS",
    "member_checklist_items", "drawing_checklist_items_for", "checklist_question",
    "FABRICATOR_ACCEPTANCE_SCOPE_STATEMENT",
    "FabricatorChecklistAnswer", "FabricatorDrawingAnswers", "FabricatorAcceptanceAnswers",
    "FabricatorAcceptanceFinding", "FabricatorChecklistResult", "FabricatorDrawingAcceptance",
    "FabricatorAcceptanceSummary", "FabricatorAcceptance",
    "evaluate_fabricator_acceptance", "fabricator_acceptance_report",
]

# Acceptance answers (the brief's suggested vocabulary).
ANSWER_PASS = "PASS"
ANSWER_FAIL = "FAIL"
ANSWER_NOT_APPLICABLE = "NOT_APPLICABLE"
ANSWER_VALUES = (ANSWER_PASS, ANSWER_FAIL, ANSWER_NOT_APPLICABLE)

# Evidence statuses: what the deliverable itself supports, before the
# reviewer's judgment.
EVIDENCE_PRESENT = "PRESENT"
EVIDENCE_MISSING = "MISSING"
EVIDENCE_CONTRADICTED = "CONTRADICTED"
EVIDENCE_STATUSES = (EVIDENCE_PRESENT, EVIDENCE_MISSING, EVIDENCE_CONTRADICTED)

# Finding kinds: missing and contradictory information are distinct
# failures; ambiguity has a deterministic reason or it is not labelled
# ambiguity; notes are recorded observations that never block on their
# own (the reviewer's answer decides).
FINDING_AMBIGUITY = "AMBIGUITY"
FINDING_CONTRADICTION = "CONTRADICTION"
FINDING_MISSING_INFORMATION = "MISSING_INFORMATION"
FINDING_NOTE = "NOTE"
FINDING_KINDS = (FINDING_AMBIGUITY, FINDING_CONTRADICTION, FINDING_MISSING_INFORMATION, FINDING_NOTE)

# The acceptance checklist (the brief's fifteen base questions — the
# member rows are sized per drawing, see MILESTONE 7AV below).
CHECKLIST_ITEM_PROJECT_IDENTIFIED = "PROJECT_IDENTIFIED"
CHECKLIST_ITEM_CONNECTION_IDENTIFIED = "CONNECTION_IDENTIFIED"
CHECKLIST_ITEM_MEMBER_A_IDENTIFIED = "MEMBER_A_IDENTIFIED"
CHECKLIST_ITEM_MEMBER_B_IDENTIFIED = "MEMBER_B_IDENTIFIED"
CHECKLIST_ITEM_CONNECTION_LOCATION = "CONNECTION_LOCATION_UNDERSTOOD"
CHECKLIST_ITEM_PLATE_INFORMATION = "PLATE_INFORMATION"
CHECKLIST_ITEM_HOLE_INFORMATION = "HOLE_INFORMATION"
CHECKLIST_ITEM_UNITS_CLEAR = "UNITS_CLEAR"
CHECKLIST_ITEM_DRAWING_IDENTITY = "DRAWING_IDENTITY_CLEAR"
CHECKLIST_ITEM_AMBIGUITY = "NO_AMBIGUITY"
CHECKLIST_ITEM_VISUAL_CONTRADICTION = "NO_VISUAL_CONTRADICTION"
CHECKLIST_ITEM_FABRICATION_BLOCKING = "NO_FABRICATION_BLOCKING_INFORMATION"
CHECKLIST_ITEM_ENGINEERING_ADMIN = "ENGINEERING_ADMIN_DISTINCT"
CHECKLIST_ITEM_TRACEABILITY = "TRACEABLE"
CHECKLIST_ITEM_PACKAGE_COMPLETE = "PACKAGE_COMPLETE"

CHECKLIST_ITEMS = (
    CHECKLIST_ITEM_PROJECT_IDENTIFIED,
    CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
    CHECKLIST_ITEM_MEMBER_A_IDENTIFIED,
    CHECKLIST_ITEM_MEMBER_B_IDENTIFIED,
    CHECKLIST_ITEM_CONNECTION_LOCATION,
    CHECKLIST_ITEM_PLATE_INFORMATION,
    CHECKLIST_ITEM_HOLE_INFORMATION,
    CHECKLIST_ITEM_UNITS_CLEAR,
    CHECKLIST_ITEM_DRAWING_IDENTITY,
    CHECKLIST_ITEM_AMBIGUITY,
    CHECKLIST_ITEM_VISUAL_CONTRADICTION,
    CHECKLIST_ITEM_FABRICATION_BLOCKING,
    CHECKLIST_ITEM_ENGINEERING_ADMIN,
    CHECKLIST_ITEM_TRACEABILITY,
    CHECKLIST_ITEM_PACKAGE_COMPLETE,
)
DRAWING_CHECKLIST_ITEMS = tuple(
    item for item in CHECKLIST_ITEMS if item != CHECKLIST_ITEM_PACKAGE_COMPLETE
)


def member_checklist_items(count: int) -> tuple[str, ...]:
    """MEMBER_A_IDENTIFIED .. MEMBER_{LETTER}_IDENTIFIED for a member
    count — one identity row per member, from the shared label
    vocabulary the multi-member drawing title block draws (7AV)."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"member count must be a positive int, got {count!r}")
    if count > len(MEMBER_LABEL_LETTERS):
        raise ValueError(
            f"member count {count} exceeds the {len(MEMBER_LABEL_LETTERS)}-label "
            "member vocabulary; the multi-member drawing itself refuses the same "
            "connection at the same boundary"
        )
    return tuple(
        f"MEMBER_{MEMBER_LABEL_LETTERS[index]}_IDENTIFIED" for index in range(count)
    )


def _member_item_letter(item_code: str) -> str | None:
    """The member label letter a MEMBER_x_IDENTIFIED item refers to, or
    None when the item is not a member-identity item."""
    prefix, suffix = "MEMBER_", "_IDENTIFIED"
    if not (item_code.startswith(prefix) and item_code.endswith(suffix)):
        return None
    letter = item_code[len(prefix):-len(suffix)]
    return letter if letter in MEMBER_LABEL_LETTERS else None


def drawing_checklist_items_for(audit: dict) -> tuple[str, ...]:
    """The drawing-level checklist items for one drawing, with the
    member rows sized to the connection's recorded member count — the
    recorded attachment semantics (the same recorded evidence 7AR
    packages and the member evidence below consumes). A record without
    attachment semantics keeps the two-member baseline; so does a
    single-member record: the established single-member declaration is
    unchanged (the reviewer answers NOT_APPLICABLE on the member rows
    beyond the recorded members)."""
    semantics = _attachment_semantics(audit)
    member_count = max(2, len(semantics)) if semantics is not None else 2
    return (
        CHECKLIST_ITEM_PROJECT_IDENTIFIED,
        CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
        *member_checklist_items(member_count),
        CHECKLIST_ITEM_CONNECTION_LOCATION,
        CHECKLIST_ITEM_PLATE_INFORMATION,
        CHECKLIST_ITEM_HOLE_INFORMATION,
        CHECKLIST_ITEM_UNITS_CLEAR,
        CHECKLIST_ITEM_DRAWING_IDENTITY,
        CHECKLIST_ITEM_AMBIGUITY,
        CHECKLIST_ITEM_VISUAL_CONTRADICTION,
        CHECKLIST_ITEM_FABRICATION_BLOCKING,
        CHECKLIST_ITEM_ENGINEERING_ADMIN,
        CHECKLIST_ITEM_TRACEABILITY,
    )


def checklist_question(item_code: str) -> str:
    """The question for one checklist item: the static wording for the
    base items, the member wording for the dynamic member rows."""
    letter = _member_item_letter(item_code)
    if letter is not None:
        return f"Can the reviewer identify Member {letter}?"
    return CHECKLIST_QUESTIONS[item_code]


CHECKLIST_QUESTIONS = {
    CHECKLIST_ITEM_PROJECT_IDENTIFIED: "Can the reviewer identify the project?",
    CHECKLIST_ITEM_CONNECTION_IDENTIFIED: "Can the reviewer identify the connection?",
    CHECKLIST_ITEM_MEMBER_A_IDENTIFIED: "Can the reviewer identify Member A?",
    CHECKLIST_ITEM_MEMBER_B_IDENTIFIED: "Can the reviewer identify Member B?",
    CHECKLIST_ITEM_CONNECTION_LOCATION: "Can the reviewer understand where the connection occurs?",
    CHECKLIST_ITEM_PLATE_INFORMATION: "Can the reviewer identify the connection plate dimensions?",
    CHECKLIST_ITEM_HOLE_INFORMATION: "Can the reviewer identify the holes and the hole pattern?",
    CHECKLIST_ITEM_UNITS_CLEAR: "Are the units clear?",
    CHECKLIST_ITEM_DRAWING_IDENTITY: "Can the reviewer clearly identify the drawing identity?",
    CHECKLIST_ITEM_AMBIGUITY: "Does the drawing contain any ambiguity that could reasonably cause "
                             "a fabricator to stop and ask?",
    CHECKLIST_ITEM_VISUAL_CONTRADICTION: "Does the drawing contradict its represented engineering data?",
    CHECKLIST_ITEM_FABRICATION_BLOCKING: "Is any missing information present that prevents a competent "
                                         "fabricator from producing the connection without making an "
                                         "engineering assumption?",
    CHECKLIST_ITEM_ENGINEERING_ADMIN: "Can the reviewer distinguish engineering information from "
                                      "administrative metadata?",
    CHECKLIST_ITEM_TRACEABILITY: "Can a drawing question be traced back to the underlying review record?",
    CHECKLIST_ITEM_PACKAGE_COMPLETE: "Does the package contain exactly the expected fabrication drawings?",
}

FABRICATOR_ACCEPTANCE_SCOPE_STATEMENT = (
    "7AS evaluates whether the current fabrication drawing package can be understood and used by a "
    "competent steel fabricator without unacceptable ambiguity. The evaluation derives deterministic "
    "evidence from the package records and the actual packaged PDFs, combines it with the reviewer's "
    "explicit per-checklist answers, and never invents missing information. It never re-verifies "
    "geometry (7AG remains authoritative), never regenerates drawings, and never mutates any record. "
    "ACCEPTED here is deliverable usability — not engineering approval, and not a claim that the "
    "drawing is fabrication-ready by itself."
)

# The generator's documented drawing conventions (7T/7AR), restated so the
# deliverable text can be checked without importing new rules.
_DRAWING_NUMBER_PREFIX = "FAB"
# The hole-diameter rendering convention (7AG's expected geometry strings):
# the recorded hole diameter is drawn as "Ø{value}" — the diameter is the
# recorded one, never derived from a bolt size.
_HOLE_DIAMETER_PREFIX = "Ø"
_PLATE_LINE_LABEL = "PLATE:"
_HOLES_LINE_LABEL = "HOLES:"
_PATTERN_LINE_LABEL = "PATTERN:"
_MATERIAL_NOT_SPECIFIED = "NOT SPECIFIED"
_STATUS_TEST = "TEST"
_PAGE_MARKER_PATTERN = re.compile(r"\b(?:PAGE|SHEET)\b|\bOF\b")
_POSITION_PATTERN = re.compile(r"\b(?:START|END)\b")
_CONNECTION_ID_PATTERN = re.compile(r"\bCONN-[A-Z0-9-]+\b")

# Title-block labels the generator draws as label line + value line pairs
# (the same convention 7AR documents).
_TITLE_BLOCK_LABELS = (
    "DRAWING NO.", "REV", "DATE", "PROJECT", "SOURCE DRAWING",
    "SECTION", "LENGTH",
    "MATERIAL", "UNITS", "SCALE", "STATUS",
) + tuple(
    # the member labels are the shared vocabulary the multi-member
    # title block draws (7AV); a two-member drawing carries exactly
    # MEMBER A / MEMBER B.
    member_label(index) for index in range(len(MEMBER_LABEL_LETTERS))
)
_ENGINEERING_LABELS = ("SECTION", "LENGTH", "PLATE:", "HOLES:")
_ADMIN_LABELS = ("DRAWING NO.", "REV", "PROJECT")

_MANIFEST_FILENAME = "project-manifest.json"
_MANIFEST_INDENT = len("  ")
_DRAWINGS_SUBDIR = "drawings"


@dataclass(frozen=True)
class FabricatorChecklistAnswer:
    """One reviewer verdict on one checklist item. A FAIL must carry a
    finding (the reason) — a FAIL without a finding is invalid input."""
    checklist_item: str
    answer: str
    finding: str | None = None


@dataclass(frozen=True)
class FabricatorDrawingAnswers:
    """The reviewer's answers for one packaged drawing, keyed by its
    package drawing number (STEELSPEC-00N)."""
    drawing_number: str
    answers: tuple[FabricatorChecklistAnswer, ...]


@dataclass(frozen=True)
class FabricatorAcceptanceAnswers:
    """The complete reviewer input: per-drawing answers for the
    drawing-level items (the fourteen base items, with the member rows
    sized per drawing — see drawing_checklist_items_for()), and the
    package-level answers (PACKAGE_COMPLETE)."""
    drawing_answers: tuple[FabricatorDrawingAnswers, ...] = ()
    package_answers: tuple[FabricatorChecklistAnswer, ...] = ()


@dataclass(frozen=True)
class FabricatorAcceptanceFinding:
    """One deterministic observation: a gap, a contradiction, an ambiguity
    with a stated reason, or a note. Never a subjective score."""
    checklist_item: str
    kind: str
    detail: str


@dataclass(frozen=True)
class FabricatorChecklistResult:
    """One checklist item's reconciled outcome: the reviewer's answer, the
    evidence the deliverable actually supports, and the findings."""
    checklist_item: str
    question: str
    answer: str
    evidence_status: str
    visible_in_drawing: bool
    finding: str | None
    evaluator_findings: tuple[FabricatorAcceptanceFinding, ...]


@dataclass(frozen=True)
class FabricatorDrawingAcceptance:
    """One packaged drawing's acceptance result and its backward trace
    (existing identifiers and recorded evidence only — referenced, never
    duplicated)."""
    drawing_number: str
    filename: str
    connection_id: str
    status: str
    results: tuple[FabricatorChecklistResult, ...]
    findings: tuple[FabricatorAcceptanceFinding, ...]
    trace: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FabricatorAcceptanceSummary:
    total_drawings: int
    accepted_drawings: int
    not_accepted_drawings: int
    findings_total: int
    fabrication_blockers: int
    ambiguity_findings: int
    final_status: str


@dataclass(frozen=True)
class FabricatorAcceptance:
    """The frozen evaluation result. REFUSED results carry no per-drawing
    acceptance (invalid input is a refusal to evaluate, never a partial
    acceptance); refusal_reasons says exactly why."""
    status: str
    reason: str
    project_id: str | None
    package_status: str
    workflow_revision: int
    caller_revision: int | None
    drawings: tuple[FabricatorDrawingAcceptance, ...]
    summary: FabricatorAcceptanceSummary
    refusal_reasons: tuple[str, ...]
    package_item_results: tuple[FabricatorChecklistResult, ...]


# --------------------------------------------------------------------------------------
# Small deterministic helpers.
# --------------------------------------------------------------------------------------
def _sha256_of_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _format_dim(value) -> str:
    """The generator's dimension rendering: an integral float is drawn
    without its decimal part."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _label_values(lines: list[str]) -> dict[str, list[str]]:
    """Label -> value(s) pairs from the drawing's own text lines (label
    line directly above its value line). SECTION/LENGTH collect one value
    per member, in drawing order."""
    values: dict[str, list[str]] = {}
    for index, line in enumerate(lines):
        if line in _TITLE_BLOCK_LABELS:
            value = ""
            for candidate in lines[index + 1:]:
                if candidate:
                    value = candidate
                    break
            values.setdefault(line, []).append(value)
    return values


def _info_lines(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        for label in (_PLATE_LINE_LABEL, _HOLES_LINE_LABEL, _PATTERN_LINE_LABEL):
            if line.startswith(label) and label not in found:
                found[label] = line[len(label):].strip()
    return found


def _connection_ids_in(text: str) -> set[str]:
    return set(_CONNECTION_ID_PATTERN.findall(text))


def _decision(audit: dict, task_type: str):
    """One recorded human decision from the package audit (7AR's shape:
    a dict with task_type/answer/...)."""
    for decision in audit.get("human_decisions", []):
        if decision.get("task_type") == task_type:
            return decision
    return None


def _attachment_semantics(audit: dict) -> tuple[str, str] | None:
    """The recorded member marks with their reviewed surface references
    ("<mark> -> END", "<mark> -> START"), from the grouped
    SELECT_MEMBER_POSITION_ATTACHMENT decision — or, when the record
    carries that decision's ungrouped pair instead (the exception-resolution
    vocabulary emitted when the member identity was already known from the
    capture's own member marks), from the SELECT_ATTACHMENT +
    SELECT_POSITION decisions, which record the same semantics in two
    answers — or None when the record carries neither shape."""
    decision = _decision(audit, TASK_SELECT_MEMBER_POSITION_ATTACHMENT)
    if decision is None or decision.get("answer") is None:
        attachments = _decision(audit, TASK_SELECT_ATTACHMENT)
        position = _decision(audit, TASK_SELECT_POSITION)
        if (attachments is None or attachments.get("answer") is None
                or position is None or position.get("answer") is None):
            return None
        attachment_answer = attachments["answer"]
        if not isinstance(attachment_answer, list) or not attachment_answer:
            return None
        try:
            references = [(str(record["member_mark"]), str(record["surface_reference"]))
                          for record in attachment_answer]
        except (KeyError, TypeError, ValueError):
            return None
        return tuple(f"{mark} -> {ref}" for mark, ref in references) or None
    answer = decision["answer"]
    if not isinstance(answer, list) or len(answer) < 3:
        return None
    try:
        marks = [str(entry) for entry in answer[0]]
        position = str(answer[1])
        references = []
        for record in answer[2]:
            references.append((str(record["member_mark"]), str(record["surface_reference"])))
        if len(marks) != len(references):
            return None
        return tuple(f"{mark} -> {ref}" for mark, (_, ref) in zip(marks, references)) or None
    except (KeyError, TypeError, ValueError):
        return None


def _connection_tokens_in_text(text: str) -> set[str]:
    return _connection_ids_in(text)


# --------------------------------------------------------------------------------------
# Evidence: what the deliverable itself supports, per checklist item.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _DrawingEvidence:
    """The deterministic scan of one drawing's deliverable: the PDF text
    (parsed), the package identity fields, and the recorded engineering
    decisions from the package audit."""
    item: object
    normalized: str
    lines: tuple[str, ...]
    label_values: dict
    info_lines: dict
    audit: dict
    recorded_plate: dict | None
    recorded_holes: dict | None
    recorded_attachment: tuple[str, str] | None


def _evidence_status(item_code: str, evidence: _DrawingEvidence, package, block=None) -> tuple:
    """Derives the evidence status (PRESENT / MISSING / CONTRADICTED), the
    drawing-visibility flag and the evaluator findings for one checklist
    item. All checks read the actual PDF text or the package's recorded
    decisions — never filenames alone, never the 7AR observed projection
    (which came from the same PDF)."""
    item = evidence.item
    text = evidence.normalized
    labels = evidence.label_values
    info = evidence.info_lines
    findings: list[FabricatorAcceptanceFinding] = []

    if item_code == CHECKLIST_ITEM_PROJECT_IDENTIFIED:
        values = labels.get("PROJECT", [])
        if not values or not values[0]:
            return EVIDENCE_MISSING, False, tuple(findings)
        if package.project_id is None or values[0] != package.project_id:
            return EVIDENCE_CONTRADICTED, True, (FabricatorAcceptanceFinding(
                item_code, FINDING_CONTRADICTION,
                f"the drawing's PROJECT title-block value {values[0]!r} does not match the "
                f"package project identity {package.project_id!r}",
            ),)
        return EVIDENCE_PRESENT, True, ()

    if item_code == CHECKLIST_ITEM_CONNECTION_IDENTIFIED:
        tokens = _connection_tokens_in_text(text)
        if not tokens:
            return EVIDENCE_MISSING, False, ()
        if tokens != {item.connection_id}:
            return EVIDENCE_CONTRADICTED, True, (FabricatorAcceptanceFinding(
                item_code, FINDING_CONTRADICTION,
                f"the drawing text carries connection identities {sorted(tokens)} but the "
                f"package records {item.connection_id}",
            ),)
        return EVIDENCE_PRESENT, True, ()

    letter = _member_item_letter(item_code)
    if letter is not None:
        label = f"MEMBER {letter}"
        values = labels.get(label, [])
        if not values or not values[0]:
            return EVIDENCE_MISSING, False, ()
        recorded_mark = None
        recorded_semantics = None
        if evidence.recorded_attachment is not None:
            parts = evidence.recorded_attachment
            index = MEMBER_LABEL_LETTERS.index(letter)
            if len(parts) > index:
                recorded_semantics = parts[index]
                recorded_mark = recorded_semantics.split(" ->", 1)[0]
        if recorded_mark is not None and values[0] != recorded_mark:
            return EVIDENCE_CONTRADICTED, True, (FabricatorAcceptanceFinding(
                item_code, FINDING_CONTRADICTION,
                f"the drawing identifies {label} as {values[0]!r} but the package records the "
                f"member attachment {recorded_semantics!r}",
            ),)
        return EVIDENCE_PRESENT, True, ()

    if item_code == CHECKLIST_ITEM_CONNECTION_LOCATION:
        if evidence.recorded_attachment is None:
            return EVIDENCE_MISSING, False, ()
        visible = bool(_POSITION_PATTERN.search(text))
        if visible:
            return EVIDENCE_PRESENT, True, ()
        return EVIDENCE_PRESENT, False, (FabricatorAcceptanceFinding(
            item_code, FINDING_AMBIGUITY,
            "the drawing text does not state where the connection occurs (no position or "
            "attachment semantics); the package records the human-reviewed attachment: "
            + ", ".join(evidence.recorded_attachment),
        ),)

    if item_code == CHECKLIST_ITEM_PLATE_INFORMATION:
        plate_line = info.get(_PLATE_LINE_LABEL)
        if not plate_line:
            return EVIDENCE_MISSING, False, ()
        if evidence.recorded_plate is None:
            return EVIDENCE_PRESENT, True, ()
        dims = {
            "width_mm": evidence.recorded_plate.get("width_mm"),
            "depth_mm": evidence.recorded_plate.get("depth_mm"),
            "thickness_mm": evidence.recorded_plate.get("thickness_mm"),
        }
        missing = [name for name, value in dims.items() if value is None]
        if missing:
            return EVIDENCE_PRESENT, True, ()
        checks = [
            (f"{_format_dim(dims['width_mm'])} ×", "width"),
            (f"× {_format_dim(dims['depth_mm'])} ×", "depth"),
            (f"× {_format_dim(dims['thickness_mm'])} mm", "thickness"),
        ]
        for needle, name in checks:
            if needle not in plate_line:
                return EVIDENCE_CONTRADICTED, True, (FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the drawing's plate line {plate_line!r} does not carry the recorded "
                    f"plate {name} {_format_dim(dims[name + '_mm'])}",
                ),)
        return EVIDENCE_PRESENT, True, ()

    if item_code == CHECKLIST_ITEM_HOLE_INFORMATION:
        holes_line = info.get(_HOLES_LINE_LABEL)
        pattern_line = info.get(_PATTERN_LINE_LABEL)
        if not holes_line or not pattern_line:
            return EVIDENCE_MISSING, False, ()
        if evidence.recorded_holes is None:
            return EVIDENCE_PRESENT, True, ()
        recorded = evidence.recorded_holes
        quantity = recorded.get("quantity")
        diameter = recorded.get("diameter_mm")
        horizontal = recorded.get("horizontal_spacing_mm")
        vertical = recorded.get("vertical_spacing_mm")
        if quantity is None or diameter is None or horizontal is None or vertical is None:
            return EVIDENCE_PRESENT, True, ()
        checks = [
            (f"{_format_dim(quantity)} ×", holes_line, "hole quantity"),
            (f"{_HOLE_DIAMETER_PREFIX}{_format_dim(diameter)}", holes_line, "hole diameter"),
            (f"{_format_dim(horizontal)} H", pattern_line, "horizontal hole spacing"),
            (f"{_format_dim(vertical)} V", pattern_line, "vertical hole spacing"),
        ]
        for needle, source, name in checks:
            if needle not in source:
                return EVIDENCE_CONTRADICTED, True, (FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the drawing's hole information {source!r} does not carry the recorded "
                    f"{name} {_format_dim({'hole quantity': quantity, 'hole diameter': diameter,
                                           'horizontal hole spacing': horizontal,
                                           'vertical hole spacing': vertical}[name])}",
                ),)
        return EVIDENCE_PRESENT, True, ()

    if item_code == CHECKLIST_ITEM_UNITS_CLEAR:
        values = labels.get("UNITS", [])
        if not values or not values[0]:
            return EVIDENCE_MISSING, False, ()
        return EVIDENCE_PRESENT, True, ()

    if item_code == CHECKLIST_ITEM_DRAWING_IDENTITY:
        expected_number = f"{_DRAWING_NUMBER_PREFIX}-{item.connection_id}"
        revision = labels.get("REV", [])
        if expected_number not in text or not revision or not revision[0]:
            return EVIDENCE_MISSING, False, ()
        if _PAGE_MARKER_PATTERN.search(text):
            return EVIDENCE_PRESENT, True, ()
        return EVIDENCE_PRESENT, True, (FabricatorAcceptanceFinding(
            item_code, FINDING_NOTE,
            f"the drawing carries no page x-of-y marker; the package manifest records "
            f"page_count = {item.page_count}",
        ),)

    if item_code == CHECKLIST_ITEM_FABRICATION_BLOCKING:
        material_values = labels.get("MATERIAL", [])
        if not material_values or not material_values[0] or \
                material_values[0] == _MATERIAL_NOT_SPECIFIED:
            findings.append(FabricatorAcceptanceFinding(
                item_code, FINDING_MISSING_INFORMATION,
                "the drawing records MATERIAL NOT SPECIFIED — no material grade is part of "
                "the delivered engineering record",
            ))
        # the blocking elements must all be present and uncontradicted.
        # Every member row the drawing should carry is part of the
        # blocking set, subject to the established single-member
        # declaration — unchanged, and extended to the new rows: a
        # reviewer who recorded NOT_APPLICABLE for a member row beyond
        # the first declares that row genuinely absent for that drawing,
        # so the row is not part of the blocking set. A row the drawing
        # itself shows can never be declared away: NOT_APPLICABLE over
        # present evidence is refused by the reconciliation matrix.
        member_items = [
            code for code in drawing_checklist_items_for(evidence.audit)
            if _member_item_letter(code) is not None
        ]
        blocking_statuses = set(member_items) | {
            CHECKLIST_ITEM_CONNECTION_LOCATION, CHECKLIST_ITEM_PLATE_INFORMATION,
            CHECKLIST_ITEM_HOLE_INFORMATION,
        }
        if block is not None:
            for code in member_items:
                index = MEMBER_LABEL_LETTERS.index(_member_item_letter(code))
                answer = block.get(code)
                if index >= 1 and answer is not None \
                        and answer.answer == ANSWER_NOT_APPLICABLE:
                    blocking_statuses.discard(code)
        for other in blocking_statuses:
            status, _, _ = _evidence_status(other, evidence, package)
            if status != EVIDENCE_PRESENT:
                return EVIDENCE_MISSING, False, tuple(findings)
        return EVIDENCE_PRESENT, False, tuple(findings)

    if item_code == CHECKLIST_ITEM_ENGINEERING_ADMIN:
        engineering_present = any(
            any(labels.get(label, [])) for label in _ENGINEERING_LABELS
        ) or bool(info.get(_PLATE_LINE_LABEL) or info.get(_HOLES_LINE_LABEL))
        admin_present = any(any(labels.get(label, [])) for label in _ADMIN_LABELS)
        if engineering_present and admin_present:
            findings = list(findings)
            status_values = labels.get("STATUS", [])
            if status_values and status_values[0] == _STATUS_TEST:
                findings.append(FabricatorAcceptanceFinding(
                    item_code, FINDING_NOTE,
                    "the title block records STATUS TEST — the workflow's default presentation "
                    "status, recorded verbatim and never dressed up by any layer",
                ))
            return EVIDENCE_PRESENT, True, tuple(findings)
        return EVIDENCE_MISSING, False, tuple(findings)

    if item_code == CHECKLIST_ITEM_TRACEABILITY:
        audit = evidence.audit
        required = {"package_id", "acceptance", "drawing_evidence", "ai_observations",
                    "field_provenance", "human_decisions", "verification"}
        if set(audit) != required or not audit.get("human_decisions"):
            return EVIDENCE_MISSING, False, ()
        return EVIDENCE_PRESENT, False, ()

    if item_code in (CHECKLIST_ITEM_AMBIGUITY, CHECKLIST_ITEM_VISUAL_CONTRADICTION):
        # these aggregate the earlier items' evidence; each finding appears
        # exactly once, on the checklist item that derived it
        contradicted = [
            other for other in drawing_checklist_items_for(evidence.audit)
            if other not in (CHECKLIST_ITEM_AMBIGUITY, CHECKLIST_ITEM_VISUAL_CONTRADICTION,
                             CHECKLIST_ITEM_FABRICATION_BLOCKING)
            and _evidence_status(other, evidence, package)[0] == EVIDENCE_CONTRADICTED
        ]
        if contradicted:
            return EVIDENCE_CONTRADICTED, False, ()
        return EVIDENCE_PRESENT, False, ()

    return EVIDENCE_MISSING, False, ()


# --------------------------------------------------------------------------------------
# The trace: a backward walk using only existing identifiers and evidence.
# --------------------------------------------------------------------------------------
def _drawing_trace(item, audit: dict) -> tuple[tuple[str, str], ...]:
    evidence = audit.get("drawing_evidence", {})
    acceptance = audit.get("acceptance", {})
    verification = audit.get("verification", {})
    provenance = [entry[1] for entry in audit.get("field_provenance", [])]
    human_decisions = audit.get("human_decisions", [])
    task_references = []
    blocker_codes = []
    for decision in human_decisions:
        task_references.append(str(decision.get("task_id", "")))
        for code in decision.get("blocker_codes", []):
            if code not in blocker_codes:
                blocker_codes.append(code)
    return (
        ("drawing", item.drawing_number),
        ("packaged_connection", f"{item.connection_id} (package {audit.get('package_id', '')})"),
        ("source_artifact", item.source_filename),
        ("verification_7ag", f"{verification.get('status', '')}; recorded sha256 "
                             f"{verification.get('recorded_sha256', '')}"),
        ("dispatch_7af", f"{item.dispatch_output_status}"),
        ("fabrication_gate_7ae", f"{acceptance.get('decision', '')}"),
        ("rerun_7ad", f"{len(human_decisions)} recorded human resolution(s)"),
        ("tasks_7ac", ", ".join(task_references)
                      + (f" — blocker codes: {', '.join(blocker_codes)}" if blocker_codes else "")),
        ("provenance", ", ".join(provenance) if provenance else "none recorded"),
        ("ai_observations", "preserved verbatim in the package audit"),
        ("source_drawing",
         f"{evidence.get('source_drawing_id', 'not recorded')} "
         f"page {evidence.get('source_page', 'not recorded')}"),
    )


# --------------------------------------------------------------------------------------
# Refusals and the empty results.
# --------------------------------------------------------------------------------------
def _refused(package, reasons, caller_revision) -> FabricatorAcceptance:
    return FabricatorAcceptance(
        status=ACCEPTANCE_STATUS_REFUSED,
        reason="; ".join(reasons),
        project_id=package.project_id,
        package_status=package.status,
        workflow_revision=package.workflow_revision,
        caller_revision=caller_revision,
        drawings=(),
        summary=FabricatorAcceptanceSummary(
            total_drawings=0, accepted_drawings=0, not_accepted_drawings=0,
            findings_total=0, fabrication_blockers=0, ambiguity_findings=0,
            final_status=ACCEPTANCE_STATUS_REFUSED,
        ),
        refusal_reasons=tuple(reasons),
        package_item_results=(),
    )


# --------------------------------------------------------------------------------------
# The public API.
# --------------------------------------------------------------------------------------
def evaluate_fabricator_acceptance(
    fabrication_package,
    *,
    acceptance_answers,
    expected_revision: int | None = None,
) -> FabricatorAcceptance:
    """
    Evaluates one fabricated deliverable — a READY 7AR fabrication
    drawing package and its actual packaged PDFs — against the
    fabricator acceptance checklist and the reviewer's explicit
    acceptance answers.

      - ACCEPTED: every checklist item was answered PASS (or, where the
        evidence is genuinely absent, NOT_APPLICABLE) over present,
        consistent evidence, for every packaged drawing and for the
        package as a whole.
      - NOT_ACCEPTED: the reviewer recorded a FAIL (with a finding) on
        at least one item — a genuine usability/fabrication issue.
      - REFUSED: the evaluation cannot be performed — a stale caller
        revision, a package that is not the READY deliverable of an
        accepted job, a tampered manifest, a missing/rehashed drawing
        file, an unparsable PDF, malformed acceptance answers
        (unknown/duplicate/missing checklist items, FAIL without a
        finding), or a PASS/NOT_APPLICABLE that contradicts the
        deliverable's evidence. Invalid evidence is a refusal, never a
        partial acceptance and never NOT_ACCEPTED.

    Nothing is written, nothing is mutated, nothing is regenerated; the
    AI, the database and the network are never touched. The result is
    derived solely from the package records, the actual PDFs and the
    acceptance answers.
    """
    if not isinstance(fabrication_package, FabricationDrawingPackage):
        raise TypeError(
            "fabrication_package must be the genuine FabricationDrawingPackage "
            f"(got {type(fabrication_package).__name__}); the fabricator acceptance is "
            "evaluated against the delivered package, never anything else."
        )
    if not isinstance(acceptance_answers, FabricatorAcceptanceAnswers):
        raise TypeError(
            "acceptance_answers must be the genuine FabricatorAcceptanceAnswers "
            f"(got {type(acceptance_answers).__name__})."
        )
    if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
        raise TypeError("expected_revision must be an int or None.")

    package = fabrication_package
    refusals: list[str] = []

    if expected_revision is not None and expected_revision != package.workflow_revision:
        return _refused(package, [
            f"the package was produced at workflow revision {package.workflow_revision} but "
            f"the caller holds revision {expected_revision}; a stale acceptance request is "
            "never evaluated against state the caller did not see.",
        ], expected_revision)
    if package.status != PACKAGE_STATUS_READY:
        return _refused(package, [
            f"the fabrication package is {package.status!r}, not READY — there is no "
            "deliverable to accept ({package.reason})",
        ], expected_revision)
    if package.acceptance_status != ACCEPTANCE_STATUS_ACCEPTED:
        return _refused(package, [
            f"the package records acceptance status {package.acceptance_status!r}; the "
            "fabricator acceptance is only evaluated against the deliverable of an "
            "accepted production job",
        ], expected_revision)
    if not package.items:
        return _refused(package, ["the package carries no drawings"], expected_revision)
    if package.manifest_path is None:
        return _refused(package, ["the package records no manifest path"], expected_revision)

    # ---- integrity of the deliverable on disk: the manifest is
    # ---- re-derived from the frozen projection (byte-identical check —
    # ---- the package is never read back as a source of truth), and
    # ---- every packaged drawing must still hash to its recorded
    # ---- artifact hash and parse as a real PDF.
    base = Path(package.manifest_path).parent
    expected_manifest = json.dumps(
        dict(package.manifest), indent=_MANIFEST_INDENT, sort_keys=True,
    ) + "\n"
    try:
        manifest_bytes = (base / _MANIFEST_FILENAME).read_bytes()
    except OSError as error:
        return _refused(package, [
            f"the package manifest could not be read ({type(error).__name__}: {error}); "
            "a tampered deliverable is refused, never evaluated",
        ], expected_revision)
    if manifest_bytes.decode("utf-8") != expected_manifest:
        return _refused(package, [
            "the manifest on disk does not match the package's recorded projection — "
            "tampered package evidence is refused, never evaluated",
        ], expected_revision)

    scanned: list[tuple[object, str, tuple[str, ...], dict, dict, dict]] = []
    identities: dict[str, set] = {"connection": set(), "drawing": set(), "source": set()}
    for item in package.items:
        if item.packaged_path is None or item.artifact_sha256 is None:
            return _refused(package, [
                f"{item.drawing_number} records no packaged path or artifact hash",
            ], expected_revision)
        try:
            data = (base / item.packaged_path).read_bytes()
        except OSError as error:
            return _refused(package, [
                f"the packaged drawing {item.filename} could not be read "
                f"({type(error).__name__}: {error}); a missing drawing is refused, never "
                "evaluated",
            ], expected_revision)
        if _sha256_of_bytes(data) != item.artifact_sha256:
            return _refused(package, [
                f"the packaged drawing {item.filename} no longer matches its recorded "
                "artifact hash — a tampered drawing is refused, never evaluated",
            ], expected_revision)
        try:
            reader = PdfReader(BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:  # noqa: BLE001 — recorded, never repaired
            return _refused(package, [
                f"the packaged drawing {item.filename} could not be parsed as a PDF "
                f"({type(error).__name__}: {error}); a tampered PDF is refused, never "
                "evaluated",
            ], expected_revision)
        identities["connection"].add(item.connection_id)
        identities["drawing"].add(item.drawing_number)
        identities["source"].add(item.source_filename)
        lines = [line.strip() for line in text.splitlines()]
        scanned.append((
            item, _normalized(text), tuple(lines),
            _label_values(lines), _info_lines(lines), dict(item.audit),
        ))
    if len(identities["connection"]) != len(package.items) or \
            len(identities["drawing"]) != len(package.items) or \
            len(identities["source"]) != len(package.items):
        return _refused(package, [
            "the package carries duplicate drawing identities — incoherent package "
            "evidence is refused, never evaluated",
        ], expected_revision)

    # ---- validate the acceptance answers: complete, unambiguous, and
    # ---- only about real checklist items and real packaged drawings.
    # ---- (MILESTONE 7AV: the required per-drawing items are sized
    # ---- from each drawing's own recorded evidence; an unknown
    # ---- drawing falls back to the two-member base set so the
    # ---- unknown-drawing refusal stays the only extra refusal, as
    # ---- before.)
    drawing_items_by_number = {
        item.drawing_number: drawing_checklist_items_for(dict(item.audit))
        for item in package.items
    }
    package_items = {CHECKLIST_ITEM_PACKAGE_COMPLETE}
    by_drawing: dict[str, dict[str, FabricatorChecklistAnswer]] = {}
    for block in acceptance_answers.drawing_answers:
        if not isinstance(block, FabricatorDrawingAnswers):
            refusals.append(
                f"malformed acceptance answers: {type(block).__name__} is not a "
                "FabricatorDrawingAnswers block")
            continue
        if block.drawing_number in by_drawing:
            refusals.append(f"duplicate acceptance answers for drawing {block.drawing_number}")
        by_drawing.setdefault(block.drawing_number, {})
        if block.drawing_number not in {item.drawing_number for item in package.items}:
            refusals.append(
                f"acceptance answers refer to unknown drawing {block.drawing_number}")
        drawing_items = set(
            drawing_items_by_number.get(block.drawing_number, DRAWING_CHECKLIST_ITEMS)
        )
        seen = set()
        for answer in block.answers:
            if not isinstance(answer, FabricatorChecklistAnswer):
                refusals.append(
                    f"malformed acceptance answers: {type(answer).__name__} is not a "
                    f"FabricatorChecklistAnswer on drawing {block.drawing_number}")
                continue
            if answer.checklist_item not in drawing_items:
                refusals.append(
                    f"unknown checklist item {answer.checklist_item!r} on drawing "
                    f"{block.drawing_number}")
            if answer.checklist_item in seen:
                refusals.append(
                    f"duplicate answer for {answer.checklist_item} on drawing "
                    f"{block.drawing_number}")
            seen.add(answer.checklist_item)
            if answer.answer not in ANSWER_VALUES:
                refusals.append(
                    f"invalid answer {answer.answer!r} for {answer.checklist_item} on "
                    f"drawing {block.drawing_number}")
            if answer.answer == ANSWER_FAIL and not (answer.finding and answer.finding.strip()):
                refusals.append(
                    f"FAIL without a finding for {answer.checklist_item} on drawing "
                    f"{block.drawing_number}")
            by_drawing[block.drawing_number][answer.checklist_item] = answer
        missing = drawing_items - set(by_drawing[block.drawing_number])
        if missing:
            refusals.append(
                f"missing acceptance answers for drawing {block.drawing_number}: "
                f"{', '.join(sorted(missing))}")
    missing_drawings = {item.drawing_number for item in package.items} - set(by_drawing)
    if missing_drawings:
        refusals.append(
            f"missing acceptance answers for drawing(s): {', '.join(sorted(missing_drawings))}")

    seen_package_items = set()
    for answer in acceptance_answers.package_answers:
        if not isinstance(answer, FabricatorChecklistAnswer):
            refusals.append(
                f"malformed acceptance answers: {type(answer).__name__} is not a "
                "FabricatorChecklistAnswer")
            continue
        if answer.checklist_item not in package_items:
            refusals.append(f"unknown package-level checklist item {answer.checklist_item!r}")
        if answer.checklist_item in seen_package_items:
            refusals.append(f"duplicate answer for {answer.checklist_item}")
        seen_package_items.add(answer.checklist_item)
        if answer.answer not in ANSWER_VALUES:
            refusals.append(f"invalid answer {answer.answer!r} for {answer.checklist_item}")
        if answer.answer == ANSWER_FAIL and not (answer.finding and answer.finding.strip()):
            refusals.append(f"FAIL without a finding for {answer.checklist_item}")
    if package_items - seen_package_items:
        refusals.append(
            "missing package-level acceptance answers: "
            + ", ".join(sorted(package_items - seen_package_items)))

    if refusals:
        return _refused(package, refusals, expected_revision)

    # ---- evidence per drawing + reconciliation against the answers.
    evidence_by_item: dict[str, dict[str, tuple]] = {}
    for item, normalized, lines, labels, info, audit in scanned:
        evidence_by_item[item.drawing_number] = {}
        evidence = _DrawingEvidence(
            item=item, normalized=normalized, lines=lines, label_values=labels,
            info_lines=info, audit=audit,
            recorded_plate=(_decision(audit, TASK_PROVIDE_PLATE) or {}).get("answer")
            if isinstance((_decision(audit, TASK_PROVIDE_PLATE) or {}).get("answer"), dict)
            else None,
            recorded_holes=(_decision(audit, TASK_PROVIDE_HOLE_DIAMETER) or {}).get("answer")
            if isinstance((_decision(audit, TASK_PROVIDE_HOLE_DIAMETER) or {}).get("answer"), dict)
            else None,
            recorded_attachment=_attachment_semantics(audit),
        )
        block = by_drawing[item.drawing_number]
        for item_code in drawing_items_by_number[item.drawing_number]:
            evidence_by_item[item.drawing_number][item_code] = \
                _evidence_status(item_code, evidence, package, block)

    for block in acceptance_answers.drawing_answers:
        for answer in block.answers:
            status, _, _ = evidence_by_item[block.drawing_number][answer.checklist_item]
            if answer.answer == ANSWER_PASS and status in (EVIDENCE_MISSING, EVIDENCE_CONTRADICTED):
                refusals.append(
                    f"PASS for {answer.checklist_item} on drawing {block.drawing_number} "
                    f"over {status.lower()} evidence — a PASS can never invent missing or "
                    "contradicted evidence")
            if answer.answer == ANSWER_NOT_APPLICABLE and status in (
                    EVIDENCE_PRESENT, EVIDENCE_CONTRADICTED):
                refusals.append(
                    f"NOT_APPLICABLE for {answer.checklist_item} on drawing "
                    f"{block.drawing_number} while the evidence is {status.lower()}")

    if refusals:
        return _refused(package, refusals, expected_revision)

    # ---- build the per-drawing results.
    drawings: list[FabricatorDrawingAcceptance] = []
    for item, normalized, lines, labels, info, audit in scanned:
        block = by_drawing[item.drawing_number]
        results: list[FabricatorChecklistResult] = []
        drawing_findings: list[FabricatorAcceptanceFinding] = []
        for item_code in drawing_items_by_number[item.drawing_number]:
            status, visible, evaluator_findings = evidence_by_item[item.drawing_number][item_code]
            answer = block[item_code]
            # the evaluator's findings are the deterministic observations;
            # the reviewer's FAIL finding stays on its checklist result
            drawing_findings.extend(evaluator_findings)
            results.append(FabricatorChecklistResult(
                checklist_item=item_code,
                question=checklist_question(item_code),
                answer=answer.answer,
                evidence_status=status,
                visible_in_drawing=visible,
                finding=answer.finding if answer.answer == ANSWER_FAIL else None,
                evaluator_findings=evaluator_findings,
            ))
        drawing_status = ACCEPTANCE_STATUS_ACCEPTED if all(
            result.answer in (ANSWER_PASS, ANSWER_NOT_APPLICABLE) for result in results
        ) else ACCEPTANCE_STATUS_NOT_ACCEPTED
        drawings.append(FabricatorDrawingAcceptance(
            drawing_number=item.drawing_number,
            filename=item.filename,
            connection_id=item.connection_id,
            status=drawing_status,
            results=tuple(results),
            findings=tuple(drawing_findings),
            trace=_drawing_trace(item, audit),
        ))

    # ---- package completeness (item 15).
    package_answer = next(
        a for a in acceptance_answers.package_answers
        if a.checklist_item == CHECKLIST_ITEM_PACKAGE_COMPLETE
    )
    package_result = FabricatorChecklistResult(
        checklist_item=CHECKLIST_ITEM_PACKAGE_COMPLETE,
        question=CHECKLIST_QUESTIONS[CHECKLIST_ITEM_PACKAGE_COMPLETE],
        answer=package_answer.answer,
        evidence_status=EVIDENCE_PRESENT,
        visible_in_drawing=False,
        finding=package_answer.finding if package_answer.answer == ANSWER_FAIL else None,
        evaluator_findings=(),
    )

    if package_answer.answer == ANSWER_FAIL:
        overall = ACCEPTANCE_STATUS_NOT_ACCEPTED
        reason = (f"the reviewer recorded FAIL on {CHECKLIST_ITEM_PACKAGE_COMPLETE}: "
                  f"{package_answer.finding}")
    elif all(d.status == ACCEPTANCE_STATUS_ACCEPTED for d in drawings):
        overall = ACCEPTANCE_STATUS_ACCEPTED
        reason = (
            f"every checklist item was answered PASS (or NOT_APPLICABLE where the evidence "
            f"is absent) over present, consistent evidence for all {len(drawings)} drawing(s), "
            "and the package is complete"
        )
    else:
        overall = ACCEPTANCE_STATUS_NOT_ACCEPTED
        first_fail = next(
            (d, r) for d in drawings for r in d.results if r.answer == ANSWER_FAIL
        )
        reason = (
            f"{first_fail[0].drawing_number} — {first_fail[1].checklist_item}: FAIL — "
            f"{first_fail[1].finding or 'no finding recorded'}"
        )

    all_findings = [finding for drawing in drawings for finding in drawing.findings]
    human_fail_findings = sum(
        1 for d in drawings for r in d.results
        if r.answer == ANSWER_FAIL and r.finding
    )
    summary = FabricatorAcceptanceSummary(
        total_drawings=len(drawings),
        accepted_drawings=sum(d.status == ACCEPTANCE_STATUS_ACCEPTED for d in drawings),
        not_accepted_drawings=sum(d.status == ACCEPTANCE_STATUS_NOT_ACCEPTED for d in drawings),
        findings_total=len(all_findings) + human_fail_findings,
        fabrication_blockers=sum(
            r.answer == ANSWER_FAIL and r.checklist_item == CHECKLIST_ITEM_FABRICATION_BLOCKING
            for d in drawings for r in d.results
        ),
        ambiguity_findings=sum(
            f.kind == FINDING_AMBIGUITY for f in all_findings
        ) + sum(
            r.answer == ANSWER_FAIL and r.checklist_item == CHECKLIST_ITEM_AMBIGUITY
            for d in drawings for r in d.results
        ),
        final_status=overall,
    )

    return FabricatorAcceptance(
        status=overall,
        reason=reason,
        project_id=package.project_id,
        package_status=package.status,
        workflow_revision=package.workflow_revision,
        caller_revision=expected_revision,
        drawings=tuple(drawings),
        summary=summary,
        refusal_reasons=(),
        package_item_results=(package_result,),
    )


# --------------------------------------------------------------------------------------
# The human-readable report (also machine-parseable: one line per fact).
# --------------------------------------------------------------------------------------
def fabricator_acceptance_report(acceptance: FabricatorAcceptance) -> str:
    """The deterministic acceptance report — the brief's per-drawing
    checklist, findings, and the factual final result. No subjective
    scores, no timestamps."""
    lines: list[str] = []
    lines.append("SteelSpec 7AS — Fabricator Acceptance")
    lines.append(f"Project: {acceptance.project_id}")
    lines.append(f"Package: {_MANIFEST_FILENAME} ({acceptance.package_status})")
    lines.append(f"Revision: {acceptance.workflow_revision}")
    lines.append("")
    if acceptance.status == ACCEPTANCE_STATUS_REFUSED:
        lines.append("Result: REFUSED")
        for reason in acceptance.refusal_reasons:
            lines.append(f"  {reason}")
        return "\n".join(lines)
    for drawing in acceptance.drawings:
        lines.append(f"Connection: {drawing.connection_id}")
        lines.append(f"Drawing: {drawing.filename}")
        lines.append("")
        for result in drawing.results:
            lines.append(f"{result.checklist_item}: {result.answer}"
                         f" (evidence: {result.evidence_status})")
        for result in drawing.results:
            if result.finding:
                lines.append(f"  Reviewer finding: {result.finding}")
        for finding in drawing.findings:
            lines.append(f"  {finding.kind}: {finding.detail}")
        lines.append("")
    for result in acceptance.package_item_results:
        lines.append(f"{result.checklist_item}: {result.answer}"
                     f" (evidence: {result.evidence_status})")
        if result.finding:
            lines.append(f"  Reviewer finding: {result.finding}")
    lines.append("")
    lines.append(f"Result: {acceptance.status}")
    lines.append(f"Reason: {acceptance.reason}")
    return "\n".join(lines)
