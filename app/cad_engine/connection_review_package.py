"""
Milestone 7W — the explicit, typed, traceable boundary between AI
EXTRACTION of a connection and the HUMAN-REVIEWED specification the 7V
gate accepts. This module generates no geometry and makes no
engineering decision:

    raw AI connection object (app.ai_analysis.pdf_vision_analyzer schema)
            |
    ai_connection_to_extraction()        --> AIExtractedConnection (lossless, typed)
            |
    create_review_package()              --> ConnectionReviewPackage
            |                                 (extraction + reviewer supplement)
    build_review_report()                --> ConnectionReviewReport
            |                                 (found / missing / needs-confirmation /
            |                                  human-reviewed / human-supplemented /
            |                                  provenance / issues / state)
    build_reviewed_connection_specification()
            |
    ReviewedConnectionSpecification                              (7V, unchanged)
            |
    check_reviewed_connection_specification_completeness()        (7V, unchanged)
            |
    existing 7D/7J/7N adapters -> 7O assembly -> 7R -> 7S/7T drawing   (unchanged)

WHAT THE AI ACTUALLY PRODUCES (verified against
app/ai_analysis/pdf_vision_analyzer.py's EXTRACTION_SYSTEM_PROMPT, not
inferred from later CAD modules): a connection object has exactly
`detail_reference`, `grid_reference`, `connects_members` (member mark
strings as labelled on the drawing), `connection_type` (a generic
string such as "bolted"), `bolts[]` (`quantity`, `size` — a NOMINAL
designation such as "M20" — and `grade`), `plates[]` (`type`,
`thickness_mm`, `width_mm`, `depth_mm`), `welds[]` (`type`, `size_mm`)
and `confidence`. app/pipeline.py additionally tags each object with
`page_num`, and supplies `drawing_id` and `project_id`; a persisted
connection also gets a database id and a workflow `review_status`
("extracted" or "review_required"). Nothing else exists upstream.

WHAT IT DOES NOT PRODUCE — the downstream gaps this module makes
explicit (each verified against the schema above): an authoritative
connection_id (assigned at persistence, never by the AI); START/END
position; project-space connection location; an attachment surface for
each member; a numeric hole-void diameter, hole spacing or hole-pattern
geometry; and any human review. Of the six engineering-content fields 7V
requires (REQUIRED_PROVENANCE_FIELDS), the AI schema can supply
material for exactly TWO — `connected_member_marks` (from
`connects_members`) and `plate` (from `plates[]`, and only when exactly
one plate is present and it carries all three dimensions). `position`,
`holes`, `location` and `attachments` are AI_NOT_EXTRACTED by
construction; `holes` in particular is never built from `bolts[]`,
because a nominal size ("M20") is not a hole diameter.

PROVENANCE (reused, never redefined — the vocabulary is 7V's own):
AI_EXTRACTED / HUMAN_REVIEWED / HUMAN_SUPPLEMENTED. The only addition is
EXTRACTION_STATUS_AI_NOT_EXTRACTED, a statement about what the AI did
(not a provenance label; 7V would reject it on a specification, which is
correct — it must never label a value). Rules:
  - A value taken from the AI stays AI_EXTRACTED until the reviewer
    EXPLICITLY confirms it (ConnectionReviewSupplement.confirmed_ai_fields),
    which relabels it HUMAN_REVIEWED. Passing validation, passing 7V, or
    being complete never changes a label; nothing is upgraded automatically.
  - A value the reviewer supplies is HUMAN_SUPPLEMENTED (including when it
    overrides an AI value).
  - Confirming and supplying the same field is ambiguous and is rejected
    (an issue is recorded and the field stays unresolved).
  - A field the AI did not extract cannot be "confirmed" — only supplied.
  - 7V's own gate then rejects any specification still carrying an
    AI_EXTRACTED required field: an AI-only package cannot masquerade as
    a reviewed one.

STATES: AI_EXTRACTED (no reviewer decisions yet), REVIEW_REQUIRED
(decisions made, but 7V still rejects the result) and READY_FOR_PIPELINE.
READY_FOR_PIPELINE is reached only by actually running 7V's own
completeness check on the built specification, and means only that. It
is NOT engineering approval, structural adequacy, code compliance or
fabrication readiness (READY_FOR_PIPELINE_MEANING), and this module
defines no state with any of those meanings. Likewise 7V's
review_status "approved" is a review-WORKFLOW status the reviewer must
set explicitly; this module never sets or defaults it.

PROJECT MEMBER IDENTITY: when `known_member_marks` are supplied, the FINAL
connected-member set is validated against them whatever its provenance —
AI-extracted, HUMAN_REVIEWED or HUMAN_SUPPLEMENTED. Supplying a value
establishes where it came from, not that it names a real project member.
An unknown mark is never removed, replaced or "corrected": it stays in the
specification with its original label, is named in `issues` and
`ConnectionReviewReport.unknown_member_marks`, and blocks READY_FOR_PIPELINE.

AUDIT TRAIL: a reviewer IS allowed to correct an AI extraction error by
supplying a valid member set — the package is then ready, the final
connected_member_marks are the reviewer's, and the AI's unknown mark is
never passed into the specification, 7V or CAD. But the historical fact
must not vanish: `unknown_member_marks` is HISTORICAL (every unknown mark
ever encountered, AI-supplied or reviewer-supplied, kept after a
correction), `current_unknown_member_marks` is what is STILL unresolved
(only these block readiness), and a corrected AI mark is recorded as a
non-blocking `AI_UNKNOWN_MEMBER_CORRECTED` issue, alongside the untouched
`ai_value`/`extraction_status` of the field entry. The final value's
provenance is the reviewer's (HUMAN_SUPPLEMENTED); the original AI value
stays AI-originated in the extraction.
7V is not modified — it validates a reviewed specification and has no
project to resolve marks against — so the gate lives in this module, and
`attempt_7v_completeness()` is the route to downstream-ready objects. With
no `known_member_marks` nothing can be validated, and an advisory says so.
Matching is exact (no stripping or case folding).

ANTI-INFERENCE: no field here is ever derived from list/member order,
bolt nominal size, grid or detail references, page order, connection
type, confidence, or geometry. There is no code path from any of those
to position, location, attachments or holes. The reviewer supplies them.

ARCHITECTURAL FINDINGS (reported, not fixed here): app/pipeline.py's
persistence step coerces missing values before they are stored
(`quantity or 1`, `thickness or 0`, bolt_size "?") and silently drops
`connects_members` marks that do not resolve to an inserted member. A
review package built from the PERSISTED rows can therefore not tell
"the AI said 1" from "the AI said nothing". This module is built from
the raw AI object for that reason; the unknown-member check
(`known_member_marks`) reports what the persistence step would silently
discard.
"""
import copy
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    REQUIRED_PROVENANCE_FIELDS,
    ReviewedConnectionCompletenessResult,
    ReviewedConnectionSpecification,
    check_reviewed_connection_specification_completeness,
)

__all__ = [
    "EXTRACTION_STATUS_AI_EXTRACTED", "EXTRACTION_STATUS_AI_NOT_EXTRACTED",
    "CATEGORY_HUMAN_SUPPLEMENTED", "CATEGORY_HUMAN_REVIEWED", "CATEGORY_NEEDS_CONFIRMATION", "CATEGORY_MISSING",
    "REVIEW_STATE_AI_EXTRACTED", "REVIEW_STATE_REVIEW_REQUIRED", "REVIEW_STATE_READY_FOR_PIPELINE",
    "READY_FOR_PIPELINE_MEANING", "ENGINEERING_FIELDS",
    "AIExtractedBolt", "AIExtractedPlate", "AIExtractedWeld", "AIExtractedConnection",
    "ConnectionReviewSupplement", "ConnectionReviewPackage", "ReviewFieldEntry", "ReviewIssue",
    "ConnectionReviewReport",
    "ai_connection_to_extraction", "create_review_package", "build_reviewed_connection_specification",
    "build_review_report", "attempt_7v_completeness",
]

# What the AI did for a field. AI_EXTRACTED is the same string as 7V's provenance label.
EXTRACTION_STATUS_AI_EXTRACTED = PROVENANCE_AI_EXTRACTED
EXTRACTION_STATUS_AI_NOT_EXTRACTED = "AI_NOT_EXTRACTED"

# The single, mutually exclusive category of each engineering field in a report.
CATEGORY_HUMAN_SUPPLEMENTED = "HUMAN_SUPPLEMENTED"
CATEGORY_HUMAN_REVIEWED = "HUMAN_REVIEWED"
CATEGORY_NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"  # an AI value is present, usable, and not yet confirmed
CATEGORY_MISSING = "MISSING"                        # no usable value from anyone

REVIEW_STATE_AI_EXTRACTED = "AI_EXTRACTED"
REVIEW_STATE_REVIEW_REQUIRED = "REVIEW_REQUIRED"
REVIEW_STATE_READY_FOR_PIPELINE = "READY_FOR_PIPELINE"

READY_FOR_PIPELINE_MEANING = (
    "READY_FOR_PIPELINE means only that the reviewed specification built from this package passed "
    "the 7V completeness boundary (review_status 'approved', human provenance on every required "
    "field, and the existing 7D/7J/7N adapters accepted it) and, when known_member_marks were supplied, "
    "that every connected member mark — AI-extracted, reviewed or reviewer-supplied — matches a known "
    "project member. It is not engineering approval, "
    "structural adequacy, code compliance or fabrication readiness."
)

# The six engineering-content fields 7V requires, plus the one OPTIONAL
# engineering field this package reviews: `material`. Material is deliberately
# NOT one of REQUIRED_PROVENANCE_FIELDS — a connection without a specified
# material still proceeds to output carrying MATERIAL NOT SPECIFIED on the
# drawing, and the fabricator acceptance layer reports it. It IS an
# engineering field here so that a material value (AI-extracted, reviewer-
# confirmed or reviewer-supplied) flows through review, provenance, the
# specification, the fabrication gates and the drawing dispatch exactly like
# every other reviewed value.
ENGINEERING_FIELDS = REQUIRED_PROVENANCE_FIELDS + ("material",)

# The only fields the current AI schema can supply values for (see module docstring).
_AI_CAPABLE_FIELDS = ("connected_member_marks", "plate", "material")

# The plate keys real_connection_adapter.py reads (a consistency test pins this to the adapter).
_PLATE_DIMENSION_KEYS = ("width_mm", "depth_mm", "thickness_mm")
_PLATE_KEYS = ("type", "thickness_mm", "width_mm", "depth_mm")

_KNOWN_CONNECTION_KEYS = {
    "detail_reference", "grid_reference", "connects_members", "connection_type",
    "bolts", "plates", "welds", "material", "confidence",
}
_PIPELINE_INJECTED_KEYS = {"page_num"}  # added by app/pipeline.py, not by the AI


# --------------------------------------------------------------------------------------
# Deliverable 1 — AI extraction domain model
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AIExtractedBolt:
    """One `bolts[]` entry exactly as the AI reported it. `size` is a NOMINAL designation."""
    quantity: Any = None
    size: Any = None
    grade: Any = None
    extra: dict[str, Any] = field(default_factory=dict)  # any key the schema does not define


@dataclass(frozen=True)
class AIExtractedPlate:
    """One `plates[]` entry exactly as the AI reported it."""
    type: Any = None
    thickness_mm: Any = None
    width_mm: Any = None
    depth_mm: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIExtractedWeld:
    """One `welds[]` entry exactly as the AI reported it."""
    type: Any = None
    size_mm: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIExtractedConnection:
    """
    What the AI produced for one connection — preserved without being
    converted into any stronger engineering representation. Values are
    kept exactly as reported (never coerced, defaulted or normalised).

    Source identity (project_id, source_drawing_id, source_page,
    drawing_number, persisted_review_status) is NOT AI output: the
    pipeline supplies it. It is carried here because it is needed to
    trace the record, and defaults to None when not available.

    `malformed_fields` keeps the raw value of any schema field whose
    shape was not usable (e.g. `bolts` was not a list of objects), and
    `unrecognised_fields` any extra top-level keys — both so nothing the
    AI produced is silently lost.
    """
    project_id: str | None = None
    source_drawing_id: str | None = None
    source_page: int | None = None
    drawing_number: str | None = None
    persisted_review_status: str | None = None
    detail_reference: Any = None
    grid_reference: Any = None
    connected_member_references: tuple[str, ...] = ()
    generic_connection_type: Any = None
    bolts: tuple[AIExtractedBolt, ...] = ()
    plates: tuple[AIExtractedPlate, ...] = ()
    welds: tuple[AIExtractedWeld, ...] = ()
    confidence: Any = None
    malformed_fields: dict[str, Any] = field(default_factory=dict)
    unrecognised_fields: dict[str, Any] = field(default_factory=dict)
    # Material (7AT): the grade/specification the AI read on the page, exactly as
    # stated — None when the page stated none, the raw value in malformed_fields
    # when the reading was not a usable designation. Never inferred, never normalised.
    material: Any = None


def _record_tuple(raw: dict, key: str, cls: type, known_keys: tuple[str, ...], malformed: dict) -> tuple:
    value = raw.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        malformed[key] = copy.deepcopy(value)
        return ()
    records = []
    for entry in value:
        known = {k: copy.deepcopy(entry[k]) for k in known_keys if k in entry}
        extra = {k: copy.deepcopy(v) for k, v in entry.items() if k not in known_keys}
        records.append(cls(**known, extra=extra))
    return tuple(records)


def ai_connection_to_extraction(
    raw_connection: dict[str, Any],
    *,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
    source_page: int | None = None,
    drawing_number: str | None = None,
    persisted_review_status: str | None = None,
) -> AIExtractedConnection:
    """
    Wraps one raw AI connection object into an AIExtractedConnection,
    losslessly and without mutating it. `source_page` falls back to the
    `page_num` key app/pipeline.py injects; every other identity value
    is an explicit keyword because the AI never produces it.
    """
    if not isinstance(raw_connection, dict):
        raise TypeError(f"raw_connection must be a dict (got {type(raw_connection).__name__}).")

    malformed: dict[str, Any] = {}

    marks_raw = raw_connection.get("connects_members")
    marks: tuple[str, ...] = ()
    if marks_raw is not None:
        if isinstance(marks_raw, list) and all(isinstance(m, str) for m in marks_raw):
            marks = tuple(marks_raw)
        else:
            malformed["connects_members"] = copy.deepcopy(marks_raw)

    # Material: an explicitly stated designation is kept VERBATIM (never stripped,
    # normalised or converted). A non-string or blank reading is not a usable
    # designation and is preserved as malformed rather than silently dropped or coerced.
    material_raw = raw_connection.get("material")
    material: Any = None
    if material_raw is not None:
        if isinstance(material_raw, str) and material_raw.strip():
            material = material_raw
        else:
            malformed["material"] = copy.deepcopy(material_raw)

    return AIExtractedConnection(
        project_id=project_id,
        source_drawing_id=source_drawing_id,
        source_page=source_page if source_page is not None else raw_connection.get("page_num"),
        drawing_number=drawing_number,
        persisted_review_status=persisted_review_status,
        detail_reference=copy.deepcopy(raw_connection.get("detail_reference")),
        grid_reference=copy.deepcopy(raw_connection.get("grid_reference")),
        connected_member_references=marks,
        generic_connection_type=copy.deepcopy(raw_connection.get("connection_type")),
        bolts=_record_tuple(raw_connection, "bolts", AIExtractedBolt, ("quantity", "size", "grade"), malformed),
        plates=_record_tuple(raw_connection, "plates", AIExtractedPlate, _PLATE_KEYS, malformed),
        welds=_record_tuple(raw_connection, "welds", AIExtractedWeld, ("type", "size_mm"), malformed),
        confidence=copy.deepcopy(raw_connection.get("confidence")),
        malformed_fields=malformed,
        unrecognised_fields={
            k: copy.deepcopy(v) for k, v in raw_connection.items()
            if k not in _KNOWN_CONNECTION_KEYS and k not in _PIPELINE_INJECTED_KEYS
        },
        material=material,
    )


# --------------------------------------------------------------------------------------
# Deliverable 5 — human supplementation path
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConnectionReviewSupplement:
    """
    Everything a human reviewer explicitly decides or supplies. Every
    field defaults to None/empty and NOTHING is ever inferred for an
    omitted field.

      - A value supplied for an engineering field becomes that field's
        value, labelled HUMAN_SUPPLEMENTED.
      - A field named in `confirmed_ai_fields` keeps the AI's value and
        is relabelled HUMAN_REVIEWED (only possible where the AI actually
        extracted a usable value).
      - `review_status` is the reviewer's workflow decision; 7V accepts
        only "approved". It is never defaulted here.
      - `connection_id` is an identifier (assigned at persistence, or
        given by the reviewer) — it is not AI output and not an
        engineering-content field, so 7V requires no provenance for it.

    The values mirror the reviewed-specification structure exactly:
    plate {type, width_mm, depth_mm, thickness_mm}; holes {quantity,
    diameter_mm, vertical_spacing_mm[, horizontal_spacing_mm]}; location
    {x, y, z, rotation_x, rotation_y, rotation_z}; attachments
    [{member_mark, surface_reference}].
    """
    connection_id: str | None = None
    review_status: str | None = None
    connected_member_marks: Sequence[str] | None = None
    position: str | None = None
    plate: dict[str, Any] | None = None
    holes: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    attachments: Sequence[dict[str, Any]] | None = None
    material: str | None = None
    confirmed_ai_fields: frozenset[str] = frozenset()

    @property
    def has_review_decisions(self) -> bool:
        """True once the reviewer has decided anything (connection_id alone is not a review decision)."""
        return bool(self.confirmed_ai_fields) or any(
            value is not None for value in (
                self.review_status, self.connected_member_marks, self.position, self.plate,
                self.holes, self.location, self.attachments, self.material,
            )
        )


@dataclass(frozen=True)
class ConnectionReviewPackage:
    """
    The deterministic unit handed to a human reviewer and, once decisions
    are recorded, into 7V: the AI extraction, the reviewer's supplement,
    and (optionally) the project's known member marks so references the
    persistence step would silently drop can be reported instead.
    """
    extraction: AIExtractedConnection
    supplement: ConnectionReviewSupplement = field(default_factory=ConnectionReviewSupplement)
    known_member_marks: tuple[str, ...] | None = None


def create_review_package(
    extraction: AIExtractedConnection,
    supplement: ConnectionReviewSupplement | None = None,
    known_member_marks: Collection[str] | None = None,
) -> ConnectionReviewPackage:
    return ConnectionReviewPackage(
        extraction=extraction,
        supplement=supplement if supplement is not None else ConnectionReviewSupplement(),
        known_member_marks=None if known_member_marks is None else tuple(known_member_marks),
    )


# --------------------------------------------------------------------------------------
# Deliverables 2/3/4/6 — the review report
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReviewFieldEntry:
    """
    One engineering-content field (one of 7V's six), fully accounted for:
      - extraction_status: AI_EXTRACTED or AI_NOT_EXTRACTED — what the AI did.
      - ai_value: exactly what the AI produced for it (None if nothing).
      - current_value: the value the built specification will carry (None if none).
      - provenance: AI_EXTRACTED / HUMAN_REVIEWED / HUMAN_SUPPLEMENTED, or None
        when there is no value.
      - category: HUMAN_SUPPLEMENTED / HUMAN_REVIEWED / NEEDS_CONFIRMATION / MISSING.
      - needs_supplementation: True when the AI cannot provide a usable value
        and the reviewer has not supplied one.
    """
    field: str
    extraction_status: str
    ai_value: Any
    current_value: Any
    provenance: str | None
    category: str
    needs_supplementation: bool
    note: str


@dataclass(frozen=True)
class ReviewIssue:
    code: str
    field: str
    message: str


@dataclass(frozen=True)
class ConnectionReviewReport:
    """
    Structured, deterministic answer to: what did the AI extract, what is
    missing, what needs confirmation, what did the human review or
    supplement, where did each value come from, and can this attempt 7V?
    """
    entries: tuple[ReviewFieldEntry, ...]
    found: tuple[str, ...]                # fields for which the AI produced something
    missing: tuple[str, ...]              # fields with no usable value
    needs_confirmation: tuple[str, ...]   # usable AI value, not yet confirmed
    needs_supplementation: tuple[str, ...]
    human_reviewed: tuple[str, ...]
    human_supplemented: tuple[str, ...]
    provenance: dict[str, str]            # field -> label, for fields that have a value
    advisories: dict[str, str]            # AI reference information preserved but never engineering content
    identity_missing: tuple[str, ...]     # identity the AI never supplies (connection_id) and no one has yet
    unknown_member_marks: tuple[Any, ...]          # HISTORICAL: every unknown mark encountered, AI or reviewer;
                                                   # retained even after a reviewer replaces it
    current_unknown_member_marks: tuple[Any, ...]  # CURRENT: unknown marks still unresolved (these block readiness)
    issues: tuple[ReviewIssue, ...]
    state: str
    state_meaning: str
    completeness: ReviewedConnectionCompletenessResult
    ready_for_pipeline: bool


@dataclass(frozen=True)
class _Resolution:
    spec: ReviewedConnectionSpecification
    entries: tuple[ReviewFieldEntry, ...]
    issues: tuple[ReviewIssue, ...]
    unknown_member_marks: tuple[Any, ...]          # HISTORICAL: every unknown mark ever encountered (AI or reviewer)
    current_unknown_member_marks: tuple[Any, ...]  # CURRENT: unknown marks still unresolved after the reviewer's decisions
    final_unknown_member_marks: tuple[Any, ...]    # unknown marks in the member set the specification will carry


def _as_list(value: Any) -> Any:
    """Copies a reviewer-supplied sequence into a list; anything else is passed through for 7V's shape check."""
    if isinstance(value, (list, tuple)):
        return [copy.deepcopy(item) for item in value]
    return copy.deepcopy(value)


def _unknown_marks(marks: Any, known: Collection[Any] | None) -> tuple[Any, ...]:
    """
    The distinct marks in `marks` (first-occurrence order) that exactly match no entry of `known`.
    Returns () when there is nothing to validate against (`known` is None) or `marks` is not a
    sequence (7V's shape check rejects that separately). Matching is exact and deliberately
    unforgiving: nothing is stripped, case-folded or fuzzily resolved, and membership uses ==
    (never hashing) so a malformed element cannot raise. A blank or non-string mark is not a
    known member either.
    """
    if known is None or not isinstance(marks, (list, tuple)):
        return ()
    known_list = list(known)
    unknown: list[Any] = []
    for mark in marks:
        if mark not in known_list and mark not in unknown:
            unknown.append(mark)
    return tuple(unknown)


def _ai_view(field_name: str, extraction: AIExtractedConnection, known: set[str] | None):
    """(ai_value, candidate, blocking_code, blocking_message) for one field, from the AI extraction only."""
    if field_name == "connected_member_marks":
        if "connects_members" in extraction.malformed_fields:
            raw = copy.deepcopy(extraction.malformed_fields["connects_members"])
            return raw, None, "MALFORMED_AI_FIELD", "the AI's connects_members was not a list of strings"
        refs = list(extraction.connected_member_references)
        if not refs:
            return None, None, None, None
        if known is not None:
            unknown = [m for m in refs if m not in known]
            if unknown:
                return refs, refs, "UNKNOWN_MEMBER_REFERENCE", (
                    f"AI member reference(s) {unknown} do not match any known project member — the "
                    "persistence step would silently drop them"
                )
        return refs, refs, None, None

    if field_name == "plate":
        if "plates" in extraction.malformed_fields:
            raw = copy.deepcopy(extraction.malformed_fields["plates"])
            return raw, None, "MALFORMED_AI_FIELD", "the AI's plates was not a list of objects"
        plates = [{k: getattr(p, k) for k in _PLATE_KEYS if getattr(p, k) is not None} for p in extraction.plates]
        if not plates:
            return None, None, None, None
        if len(plates) > 1:
            return copy.deepcopy(plates), None, "AMBIGUOUS_MULTIPLE_PLATES", (
                f"the AI extracted {len(plates)} plates but the pipeline consumes exactly one end plate; "
                "no plate is selected by position or order — the reviewer must supply the plate"
            )
        plate = plates[0]
        missing_dims = [k for k in _PLATE_DIMENSION_KEYS if k not in plate]
        if missing_dims:
            return copy.deepcopy(plate), None, "INCOMPLETE_AI_PLATE", (
                f"the AI's plate is missing {missing_dims}; missing dimensions are never estimated"
            )
        return copy.deepcopy(plate), copy.deepcopy(plate), None, None

    if field_name == "material":
        if "material" in extraction.malformed_fields:
            raw = copy.deepcopy(extraction.malformed_fields["material"])
            return raw, None, "MALFORMED_AI_FIELD", "the AI's material was not a usable designation"
        value = extraction.material
        if value is None:
            return None, None, None, None
        # An explicitly stated grade is kept VERBATIM — never stripped, normalised or
        # converted into another designation. Like every other AI value it is
        # usable but unconfirmed: it awaits explicit human confirmation.
        return value, value, None, None

    return None, None, None, None  # the AI schema has no value for this field


def _resolve(package: ConnectionReviewPackage) -> _Resolution:
    extraction, supplement = package.extraction, package.supplement
    known = set(package.known_member_marks) if package.known_member_marks is not None else None
    supplied_by_field = {
        "connected_member_marks": supplement.connected_member_marks,
        "position": supplement.position,
        "plate": supplement.plate,
        "holes": supplement.holes,
        "location": supplement.location,
        "attachments": supplement.attachments,
        "material": supplement.material,
    }
    issues: list[ReviewIssue] = []
    entries: list[ReviewFieldEntry] = []
    values: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    all_unknown: list[Any] = []      # historical: never shrinks when a reviewer corrects an AI mark
    current_unknown: list[Any] = []  # current: only marks that remain unresolved
    final_unknown: tuple[Any, ...] = ()

    for name in sorted(f for f in supplement.confirmed_ai_fields if f not in ENGINEERING_FIELDS):
        issues.append(ReviewIssue("UNKNOWN_CONFIRMED_FIELD", name, f"'{name}' is not a reviewable engineering field."))

    for name in ENGINEERING_FIELDS:
        ai_value, candidate, blocking_code, blocking_message = _ai_view(name, extraction, known)
        # The AI's own unknown member marks are a historical fact of the extraction. Record them now, before
        # any reviewer decision, so a later correction can never make them disappear from the audit trail.
        ai_unknown = _unknown_marks(ai_value, package.known_member_marks) \
            if name == "connected_member_marks" and blocking_code == "UNKNOWN_MEMBER_REFERENCE" else ()
        all_unknown.extend(m for m in ai_unknown if m not in all_unknown)
        supplied = supplied_by_field[name]
        confirmed = name in supplement.confirmed_ai_fields
        extraction_status = (
            EXTRACTION_STATUS_AI_EXTRACTED if ai_value is not None else EXTRACTION_STATUS_AI_NOT_EXTRACTED
        )

        if supplied is not None and confirmed:
            issues.append(ReviewIssue(
                "CONFIRM_AND_SUPPLY_CONFLICT", name,
                f"'{name}' was both confirmed as the AI value and supplied by the reviewer; that is "
                "ambiguous, so neither is applied.",
            ))
            supplied, confirmed = None, False

        value: Any = None
        label: str | None = None
        category: str
        note: str

        if supplied is not None:
            value = _as_list(supplied) if name in ("connected_member_marks", "attachments") else copy.deepcopy(supplied)
            label, category = PROVENANCE_HUMAN_SUPPLEMENTED, CATEGORY_HUMAN_SUPPLEMENTED
            note = "Supplied by the reviewer" + (" (overrides the AI-extracted value)." if ai_value is not None else ".")
            if ai_unknown:
                issues.append(ReviewIssue(
                    "AI_UNKNOWN_MEMBER_CORRECTED", name,
                    f"The AI extracted mark(s) {list(ai_unknown)} that match no known project member; the reviewer "
                    "supplied a different member set, so they are not in the reviewed specification. Retained for "
                    "the extraction audit trail only — this is not a current blocking issue.",
                ))
                note += f" The AI's unknown mark(s) {list(ai_unknown)} were replaced (audit trail only)."
        elif confirmed and candidate is not None and blocking_code is None:
            value = copy.deepcopy(candidate)
            label, category = PROVENANCE_HUMAN_REVIEWED, CATEGORY_HUMAN_REVIEWED
            note = "AI-extracted value explicitly confirmed by the reviewer."
        else:
            if confirmed:
                reason = blocking_message or "the AI did not extract this field, so there is nothing to confirm"
                issues.append(ReviewIssue("CANNOT_CONFIRM", name, f"'{name}' cannot be confirmed: {reason}."))
            if blocking_code is not None:
                issues.append(ReviewIssue(blocking_code, name, f"'{name}': {blocking_message}."))
                current_unknown.extend(m for m in ai_unknown if m not in current_unknown)
            if candidate is not None and blocking_code is None:
                value, label, category = copy.deepcopy(candidate), PROVENANCE_AI_EXTRACTED, CATEGORY_NEEDS_CONFIRMATION
                note = "AI-extracted value awaiting explicit human confirmation."
            else:
                category = CATEGORY_MISSING
                note = blocking_message[0].upper() + blocking_message[1:] + "." if blocking_message else (
                    "Not extracted by the AI schema; the reviewer must supply it."
                    if name not in _AI_CAPABLE_FIELDS else "The AI extracted nothing for this field."
                )

        if name == "connected_member_marks" and label is not None:
            # Provenance says who supplied the marks; it does not make them project members. The value is
            # kept exactly as supplied and keeps its label — it is never removed, replaced or "fixed".
            unknown = _unknown_marks(value, package.known_member_marks)
            if unknown:
                final_unknown = unknown
                all_unknown.extend(m for m in unknown if m not in all_unknown)
                current_unknown.extend(m for m in unknown if m not in current_unknown)
                issues.append(ReviewIssue(
                    "UNKNOWN_MEMBER_REFERENCE", name,
                    f"'{name}' contains mark(s) {list(unknown)} that match no known project member "
                    f"(provenance {label} is unchanged). The value is kept as supplied, never removed or "
                    "replaced, but the package cannot be READY_FOR_PIPELINE against the known project members.",
                ))
                note += f" Contains mark(s) not among the known project members: {list(unknown)}."

        if label is not None:
            values[name] = value
            provenance[name] = label
        entries.append(ReviewFieldEntry(
            field=name, extraction_status=extraction_status, ai_value=ai_value, current_value=value,
            provenance=label, category=category,
            needs_supplementation=(category == CATEGORY_MISSING), note=note,
        ))

    review_status = supplement.review_status if supplement.review_status is not None \
        else extraction.persisted_review_status
    spec = ReviewedConnectionSpecification(
        connection_id=supplement.connection_id,
        review_status=review_status,
        connected_member_marks=values.get("connected_member_marks") or [],
        position=values.get("position"),
        plate=values.get("plate"),
        holes=values.get("holes"),
        location=values.get("location"),
        attachments=values.get("attachments") or [],
        material=values.get("material"),
        source_page=extraction.source_page,
        source_drawing_id=extraction.source_drawing_id,
        provenance=provenance,
    )
    return _Resolution(
        spec=spec, entries=tuple(entries), issues=tuple(issues),
        unknown_member_marks=tuple(all_unknown), current_unknown_member_marks=tuple(current_unknown),
        final_unknown_member_marks=final_unknown,
    )


def build_reviewed_connection_specification(package: ConnectionReviewPackage) -> ReviewedConnectionSpecification:
    """
    The deterministic path into 7V: a plain ReviewedConnectionSpecification
    (never a ValidatedConnection or any CAD object) carrying each value's
    provenance. Building it never upgrades a label, infers a value, or
    sets a review status; whether it may enter the pipeline is decided by
    7V's own check, unchanged.
    """
    return _resolve(package).spec


def _completeness(resolution: _Resolution) -> ReviewedConnectionCompletenessResult:
    """
    7V's own, unmodified completeness check on the built specification, plus the one 7W prerequisite
    7V deliberately does not own: project-member identity. 7V validates a reviewed SPECIFICATION and
    never resolves marks against a project; this package is project-aware (known_member_marks), so a
    member set containing a mark that matches no known member is not complete here — whoever supplied
    it. The result then carries no ValidatedConnection/location/attachments (nothing pipeline-usable),
    keeps the provenance copy, and names the unknown marks. 7V itself is not modified.
    """
    result = check_reviewed_connection_specification_completeness(resolution.spec)
    if not resolution.final_unknown_member_marks:
        return result
    message = (
        f"connected_member_marks contains mark(s) {list(resolution.final_unknown_member_marks)} that match no "
        "known project member; supplying a value establishes its provenance, not its project identity."
    )
    error = message if result.is_complete else f"{message} (7V also reports: {result.error})"
    return ReviewedConnectionCompletenessResult(False, None, None, None, error, dict(result.provenance))


def attempt_7v_completeness(package: ConnectionReviewPackage) -> ReviewedConnectionCompletenessResult:
    """
    The only 7W route to downstream-ready objects: 7V's own completeness check on the built
    specification, plus the project-member gate above when known_member_marks were supplied.
    Calling 7V's check directly on build_reviewed_connection_specification()'s output skips the
    project-member gate (7V has no project to resolve against) — go through this function.
    """
    return _completeness(_resolve(package))


def _advisories(
    extraction: AIExtractedConnection, known: tuple[str, ...] | None, supplement: ConnectionReviewSupplement,
) -> dict[str, str]:
    notes: dict[str, str] = {}
    if extraction.generic_connection_type is not None:
        notes["generic_connection_type"] = (
            f"AI connection type {extraction.generic_connection_type!r} is a generic descriptor, not a "
            "complete connection definition; it selects nothing and defines no geometry."
        )
    if extraction.bolts or "bolts" in extraction.malformed_fields:
        sizes = [b.size for b in extraction.bolts if b.size is not None]
        notes["bolts"] = (
            f"AI extracted nominal bolt information (sizes {sizes}, quantities "
            f"{[b.quantity for b in extraction.bolts]}); a nominal bolt size is not a hole diameter and is "
            "never converted into one — the numeric hole pattern must be supplied by the reviewer."
            + (f" {len(extraction.bolts)} bolt groups were extracted; none is selected by order."
               if len(extraction.bolts) > 1 else "")
        )
    if extraction.welds or "welds" in extraction.malformed_fields:
        notes["welds"] = (
            "AI extracted weld information is preserved for reference only; the current pipeline "
            "does not consume weld data."
        )
    if extraction.confidence is not None:
        notes["confidence"] = (
            f"AI confidence {extraction.confidence!r} describes how well the vision model read the page — "
            "it is not an engineering approval and does not change what a reviewer must confirm."
        )
    if known is None and (extraction.connected_member_references or supplement.connected_member_marks):
        notes["member_references"] = (
            "Connected member marks (AI-extracted or reviewer-supplied) were not checked against the project's "
            "known members (no known_member_marks were supplied)."
        )
    return notes


def build_review_report(package: ConnectionReviewPackage) -> ConnectionReviewReport:
    """The structured review-gap report. Deterministic, side-effect free, and never mutates `package`."""
    resolution = _resolve(package)
    completeness = _completeness(resolution)
    entries = resolution.entries

    def names(category: str) -> tuple[str, ...]:
        return tuple(e.field for e in entries if e.category == category)

    if not package.supplement.has_review_decisions:
        state = REVIEW_STATE_AI_EXTRACTED
    elif completeness.is_complete:
        state = REVIEW_STATE_READY_FOR_PIPELINE
    else:
        state = REVIEW_STATE_REVIEW_REQUIRED

    return ConnectionReviewReport(
        entries=entries,
        found=tuple(e.field for e in entries if e.ai_value is not None),
        missing=names(CATEGORY_MISSING),
        needs_confirmation=names(CATEGORY_NEEDS_CONFIRMATION),
        needs_supplementation=tuple(e.field for e in entries if e.needs_supplementation),
        human_reviewed=names(CATEGORY_HUMAN_REVIEWED),
        human_supplemented=names(CATEGORY_HUMAN_SUPPLEMENTED),
        provenance=dict(resolution.spec.provenance),
        advisories=_advisories(package.extraction, package.known_member_marks, package.supplement),
        identity_missing=() if package.supplement.connection_id is not None else ("connection_id",),
        unknown_member_marks=resolution.unknown_member_marks,
        current_unknown_member_marks=resolution.current_unknown_member_marks,
        issues=resolution.issues,
        state=state,
        state_meaning=READY_FOR_PIPELINE_MEANING,
        completeness=completeness,
        ready_for_pipeline=state == REVIEW_STATE_READY_FOR_PIPELINE,
    )
