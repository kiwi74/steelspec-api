"""
Milestone 7V — one authoritative, explicit data contract for a
HUMAN-REVIEWED connection specification, sitting between AI/drawing
extraction and the existing real connection -> CAD -> drawing
pipeline:

    AI-extracted evidence (incomplete, by design — see Step 1 finding)
            |
    HUMAN REVIEW / SUPPLEMENTATION
            |
    ReviewedConnectionSpecification (this module's typed contract)
            |
    check_reviewed_connection_specification_completeness()
            |   1. shape check (malformed input -> incomplete, never a crash)
            |   2. review-authority gate (review_status + per-field provenance)
            |   3. the EXISTING adapters below (never re-implemented here)
            v
    real_connection_to_validated_connection()            (7D)
    real_connection_location_to_connection_location()     (7J, unchanged)
    reviewed_connection_detail_to_attachments()            (7N, unchanged)
            |
    build_reviewed_two_member_connection_assembly()        (7O, unchanged)
            |
    validate_reviewed_connection_assembly()                 (7R, unchanged)
            |
    generate_fabrication_drawing_from_reviewed_assembly()   (7S/7T, unchanged)

STEP 1 FINDING — the actual current AI schema, quoted directly from
app/ai_analysis/pdf_vision_analyzer.py's own EXTRACTION_SYSTEM_PROMPT
(not assumed): a connection object contains exactly
`detail_reference`, `grid_reference`, `connects_members`,
`connection_type` (a generic string like "bolted" — never trusted for
CAD purposes, see real_connection_adapter.py's own docstring),
`bolts[]` (`quantity`, `size`, `grade` — NOT a numeric hole diameter),
`plates[]` (`type`, `thickness_mm`, `width_mm`, `depth_mm`), `welds[]`,
`confidence`. It has NO `connection_id` (assigned only once persisted
— a downstream, non-AI step), NO `position` (START/END), NO
project-space location (x/y/z/rotations), and NO attachment/surface
concept at all (7N's own docstring already established this same
gap). This module does not change any of that — it only defines the
explicit shape a human reviewer fills in once they supply what the AI
layer structurally cannot.

STEP 2 — WHY THIS CONTRACT LOOKS THE WAY IT DOES: `plate` and `holes`
below are plain dicts, not further-nested dataclasses, deliberately
matching the exact shape `real_connection_to_validated_connection()`
already reads from `record["plates"][0]`/`record["bolts"][0]` — the
same convention app.cad_engine.interface.ValidatedConnection itself
already uses (`plates: list[dict]`, `bolts: list[dict]`). This is the
smallest contract that adds a stable, explicit, typed ENVELOPE around
the three existing raw record shapes (7D's connection record, 7J's
location record, 7N's detail record) without inventing a second,
parallel geometry-field vocabulary alongside the one those adapters
already define and validate.

STEP 4 — WHY COMPLETENESS DOES NOT DUPLICATE GEOMETRY-FIELD VALIDATION:
every numeric/field requirement (positive finite plate dimensions,
numeric hole diameter, explicit position, finite location, attachment
coverage, ...) already lives in real_connection_adapter.py (7D),
connection_location.py (7J), and reviewed_connection_attachment.py
(7N). Re-implementing "is width_mm a positive finite number" a second
time here would create exactly the kind of competing source of truth
this project has avoided since 7A. Step 3 of
check_reviewed_connection_specification_completeness() therefore
converts the specification into the three existing raw record shapes
(pure reshaping) and ATTEMPTS the three existing adapter calls,
catching GeometryValidationError. (The 7V correction pass fixed one
genuine gap at its source rather than here: 7D's `value <= 0` checks
accepted NaN/+infinity, and `int(quantity)` crashed on NaN/infinity or
silently truncated 4.5 — they now reject non-finite and non-integral
values. See real_connection_adapter._is_positive_finite_number().)
This function never calls 7M's resolve_connection_attachment_surfaces(),
7K/7L's build_two_member_connection_assembly(), 7P, 7Q, or 7R — those
require an assembled two-member geometry (actual placed members) this
function never builds; completeness answers "is this specification
ready to ENTER that pipeline", never "is the resulting geometry
consistent" or "is it structurally adequate" (Step 9).

7V CORRECTION PASS — THE THREE LAYERS OF THIS CHECK, kept distinct:

  1. SHAPE (malformed input). `plate="end_plate"`, `holes="bolts"`,
     `location="here"`, attachment entries that are not objects, a
     `connected_member_marks` that is not a list of strings, or a
     `provenance` that is not a {str: str} mapping are EXPECTED
     malformed inputs, checked with explicit isinstance tests and
     reported as an ordinary incomplete result. No broad
     `except Exception` exists anywhere in this module: a genuine
     programming error still raises.

  2. REVIEW AUTHORITY (is this information reviewed?). One small,
     explicit rule — a specification is review-authoritative only if:
       (a) `review_status == REVIEW_STATUS_APPROVED` ("approved") — the
           repo's existing vocabulary is "extracted" (raw pipeline
           output), "review_required" (flagged low confidence) and
           "approved" (a reviewer accepted the record); anything else,
           including a missing status, "pending_review", "extracted"
           or "review_required", is AI-extracted information still
           awaiting review. NOTE: "approved" is this pipeline's
           existing review-WORKFLOW status. It is not engineering
           approval, not code compliance, and not fabrication
           readiness.
       (b) every field in REQUIRED_PROVENANCE_FIELDS (the six fields
           that carry the engineering content a reviewer must own:
           connected_member_marks, position, plate, holes, location,
           attachments) has a provenance label; every label in
           `provenance` is one of SUPPORTED_PROVENANCE_LABELS; and
           none of the required fields is labelled AI_EXTRACTED.
           HUMAN_REVIEWED (a reviewer confirmed a value) and
           HUMAN_SUPPLEMENTED (a reviewer supplied a value the AI could
           not) are both acceptable and are never rewritten into one
           another, nor into AI_EXTRACTED.
     connection_id and review_status themselves are deliberately NOT in
     REQUIRED_PROVENANCE_FIELDS: connection_id is a persistence-time
     identifier and review_status is the gate itself — neither is an
     engineering value a reviewer "supplies".

  3. GEOMETRY-FIELD COMPLETENESS (can the existing adapters accept it?).
     The unchanged 7D/7J/7N adapters, as described above.

PROVENANCE AND CAD: provenance is validated and RETAINED here — the
completeness result carries a copy so a later audit can still see which
fields were AI-extracted, human-reviewed or human-supplemented — but it
is never passed into any adapter, ValidatedConnection, ConnectionLocation,
ConnectionAttachment or geometry object. CAD geometry stays concerned
only with authoritative numeric geometry: for any two specifications
that differ only in HUMAN_REVIEWED-vs-HUMAN_SUPPLEMENTED labels, the
validated connection, location and attachments are identical.

SCOPE: these layers answer "was this reviewed, and can the software
process it?" — never "is the connection structurally adequate?".
Geometric consistency remains 7K/7L/7P/7Q/7R; engineering adequacy is a
separate, future, human concern.
"""
from dataclasses import dataclass, field
from typing import Any

from app.cad_engine.connection_attachment import ConnectionAttachment
from app.cad_engine.connection_location import ConnectionLocation, real_connection_location_to_connection_location
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedConnection
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.reviewed_connection_attachment import reviewed_connection_detail_to_attachments

__all__ = [
    "PROVENANCE_AI_EXTRACTED", "PROVENANCE_HUMAN_REVIEWED", "PROVENANCE_HUMAN_SUPPLEMENTED",
    "SUPPORTED_PROVENANCE_LABELS", "REVIEW_STATUS_APPROVED", "REQUIRED_PROVENANCE_FIELDS",
    "ReviewedConnectionSpecification", "ReviewedConnectionCompletenessResult",
    "reviewed_connection_specification_to_connection_record",
    "reviewed_connection_specification_to_location_record",
    "reviewed_connection_specification_to_detail_record",
    "check_reviewed_connection_specification_completeness",
]

# Provenance labels — validated by the review-authority gate and retained
# on the completeness result; never consumed by any adapter or geometry
# function (see module docstring).
PROVENANCE_AI_EXTRACTED = "AI_EXTRACTED"
PROVENANCE_HUMAN_REVIEWED = "HUMAN_REVIEWED"
PROVENANCE_HUMAN_SUPPLEMENTED = "HUMAN_SUPPLEMENTED"
SUPPORTED_PROVENANCE_LABELS = (PROVENANCE_AI_EXTRACTED, PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED)

# The one review_status value that means "a reviewer accepted this record" in
# this repo's existing vocabulary (pipeline.py: "extracted" / "review_required";
# reviewed fixtures: "approved"). A review-workflow status only.
REVIEW_STATUS_APPROVED = "approved"

# The fields carrying engineering content a reviewer must own — each needs a
# provenance label, and none may still be AI_EXTRACTED.
REQUIRED_PROVENANCE_FIELDS = (
    "connected_member_marks", "position", "plate", "holes", "location", "attachments",
)


@dataclass
class ReviewedConnectionSpecification:
    """
    The authoritative envelope for everything the EXISTING real
    connection pipeline (7D + 7J + 7N) needs. Every field defaults to
    `None`/empty so this same type can represent a genuinely
    AI-extracted-only record (most fields absent — see module
    docstring's Step 1 finding) just as well as a complete
    HUMAN-REVIEWED/SUPPLEMENTED one; nothing here defaults a missing
    engineering value to anything other than "absent". This dataclass
    performs no validation itself — check_reviewed_connection_
    specification_completeness() does, and a specification only becomes
    pipeline-ready by passing it (which requires review_status ==
    "approved" and human provenance on every required field).

    Field groups:
      - Identity: connection_id.
      - Review state: review_status ("approved" to be pipeline-ready).
      - Connected members: connected_member_marks — a list of strings,
        order preserved exactly as supplied; no primary/secondary
        meaning is attached to position (matching 7D's own precedent).
      - Connection position: position ("START"/"END", no default).
      - Plate: plate — {"type", "width_mm", "depth_mm", "thickness_mm"},
        the exact shape real_connection_to_validated_connection() reads
        from record["plates"][0].
      - Holes: holes — {"quantity", "diameter_mm",
        "vertical_spacing_mm", "horizontal_spacing_mm"}, the exact
        shape read from record["bolts"][0]. `diameter_mm` must be an
        explicit numeric hole-void diameter — never a nominal bolt
        size string (see real_connection_adapter.py's own docstring).
      - Connection project-space location: location — {"x", "y", "z",
        "rotation_x", "rotation_y", "rotation_z"}, the exact shape
        real_connection_location_to_connection_location() reads.
      - Attachments: attachments — a list of
        {"member_mark", "surface_reference"} dicts, the exact shape
        reviewed_connection_detail_to_attachments() reads.
      - Provenance: provenance — {field_name: label} using the three
        PROVENANCE_* constants; see module docstring.
      - Material (7AT): material — the connection's material designation
        as a verbatim string, or None when genuinely not specified. It is
        deliberately NOT a required provenance field: a specification
        without a material is still review-authoritative (the drawing then
        carries MATERIAL NOT SPECIFIED and the fabricator acceptance layer
        reports it). When material IS present it must carry a provenance
        label, like every other reviewed value.
    """
    connection_id: str | None = None
    review_status: str | None = None
    connected_member_marks: list[str] = field(default_factory=list)
    position: str | None = None
    plate: dict[str, Any] | None = None
    holes: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    source_page: int | None = None
    source_drawing_id: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    material: str | None = None


def reviewed_connection_specification_to_connection_record(spec: ReviewedConnectionSpecification) -> dict:
    """
    Pure reshaping into the exact raw record shape
    real_connection_to_validated_connection() (7D) already consumes —
    no field is inspected, required, or defaulted here. A missing
    `spec.plate`/`spec.holes` becomes an empty list (never a
    fabricated single-entry list), which 7D's own existing checks
    already reject with a clear message. Expects a shape-valid
    specification; check_reviewed_connection_specification_completeness()
    performs that shape check before calling this.
    """
    return {
        "connection_id": spec.connection_id,
        "connected_member_marks": list(spec.connected_member_marks),
        "position": spec.position,
        "plates": [dict(spec.plate)] if spec.plate is not None else [],
        "bolts": [dict(spec.holes)] if spec.holes is not None else [],
        "review_status": spec.review_status,
        "source_page": spec.source_page,
        "source_drawing_id": spec.source_drawing_id,
    }


def reviewed_connection_specification_to_location_record(spec: ReviewedConnectionSpecification) -> dict:
    """
    Pure reshaping into the exact raw record shape
    real_connection_location_to_connection_location() (7J) already
    consumes. A missing `spec.location` (or a missing individual
    coordinate within it) becomes `None` for that field — never 0.0 —
    which 7J's own existing check already rejects explicitly.
    """
    location = spec.location or {}
    return {
        "connection_id": spec.connection_id,
        "x": location.get("x"),
        "y": location.get("y"),
        "z": location.get("z"),
        "rotation_x": location.get("rotation_x"),
        "rotation_y": location.get("rotation_y"),
        "rotation_z": location.get("rotation_z"),
    }


def reviewed_connection_specification_to_detail_record(spec: ReviewedConnectionSpecification) -> dict:
    """
    Pure reshaping into the exact raw record shape
    reviewed_connection_detail_to_attachments() (7N) already consumes.
    """
    return {
        "connection_id": spec.connection_id,
        "review_status": spec.review_status,
        "attachments": [dict(a) for a in spec.attachments],
    }


@dataclass(frozen=True)
class ReviewedConnectionCompletenessResult:
    """
    Answers exactly one question: was this specification explicitly
    reviewed, and does it contain every field the EXISTING pipeline
    requires to be attempted? It does NOT answer whether the connection
    is structurally adequate, code-compliant, or fabrication-ready —
    those questions belong to 7P/7Q/7R (geometric consistency only) and,
    beyond this software entirely, to a qualified engineer.

    On success, carries the already-converted, already-validated
    ValidatedConnection/ConnectionLocation/ConnectionAttachment objects
    the existing adapters produced — a caller proceeds with THESE
    objects directly, never by hand-building new ones or re-running the
    adapters a second time. When the review-authority gate or the shape
    check fails, all three are None: nothing pipeline-usable exists for
    an unreviewed or malformed specification.

    `provenance` is a COPY of the specification's provenance mapping
    (empty if it was malformed), retained so a later audit can still see
    which fields were AI-extracted, human-reviewed or human-supplemented.
    It is metadata only — it was never passed to any adapter, and no
    geometry object carries it.
    """
    is_complete: bool
    validated_connection: ValidatedConnection | None
    connection_location: ConnectionLocation | None
    attachments: list[ConnectionAttachment] | None
    error: str | None
    provenance: dict[str, str] = field(default_factory=dict)


def _label(spec: ReviewedConnectionSpecification) -> str:
    connection_id = spec.connection_id
    return f"Connection '{connection_id}'" if connection_id else "Connection (unlabelled)"


def _shape_error(spec: ReviewedConnectionSpecification) -> str | None:
    """
    Explicit isinstance checks for the EXPECTED malformed-input cases —
    the ones that would otherwise raise ValueError/TypeError/
    AttributeError while the specification is reshaped into the three
    raw adapter records. Returns a message, or None if the structure is
    sound. Says nothing about whether the VALUES inside are acceptable —
    that remains the adapters' job.
    """
    label = _label(spec)

    marks = spec.connected_member_marks
    if not isinstance(marks, list) or not all(isinstance(m, str) for m in marks):
        return f"{label}: connected_member_marks must be a list of strings (got {marks!r})."

    for name in ("plate", "holes", "location"):
        value = getattr(spec, name)
        if value is not None and not isinstance(value, dict):
            return f"{label}: {name} must be an object with named fields or absent (got {value!r})."

    attachments = spec.attachments
    if not isinstance(attachments, list):
        return f"{label}: attachments must be a list of objects (got {attachments!r})."
    for i, entry in enumerate(attachments):
        if not isinstance(entry, dict):
            return f"{label}: attachment entry {i} must be an object with member_mark/surface_reference (got {entry!r})."

    provenance = spec.provenance
    if not isinstance(provenance, dict):
        return f"{label}: provenance must be a mapping of field name to label (got {provenance!r})."
    for key, value in provenance.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return f"{label}: provenance entries must be string field name -> string label (got {key!r}: {value!r})."

    return None


def _review_authority_error(spec: ReviewedConnectionSpecification) -> str | None:
    """
    The small, explicit review-authority rule (see module docstring):
    review_status must be "approved", every provenance label must be a
    supported one, every required field must carry a label, and none of
    the required fields may still be AI_EXTRACTED. Assumes _shape_error()
    already passed.
    """
    label = _label(spec)

    if spec.review_status != REVIEW_STATUS_APPROVED:
        return (
            f"{label}: review_status is {spec.review_status!r}; a specification is only "
            f"review-authoritative when review_status is {REVIEW_STATUS_APPROVED!r}. Information "
            "still awaiting review cannot enter the CAD pipeline."
        )

    unknown = {k: v for k, v in spec.provenance.items() if v not in SUPPORTED_PROVENANCE_LABELS}
    if unknown:
        return (
            f"{label}: unknown provenance label(s) {unknown!r}. Supported: "
            f"{list(SUPPORTED_PROVENANCE_LABELS)}."
        )

    missing = [name for name in REQUIRED_PROVENANCE_FIELDS if name not in spec.provenance]
    if missing:
        return (
            f"{label}: no provenance recorded for required field(s) {missing}. Every required "
            "field must be labelled HUMAN_REVIEWED or HUMAN_SUPPLEMENTED."
        )

    ai_only = [name for name in REQUIRED_PROVENANCE_FIELDS if spec.provenance[name] == PROVENANCE_AI_EXTRACTED]
    if ai_only:
        return (
            f"{label}: required field(s) {ai_only} are still AI_EXTRACTED — AI-extracted "
            "information must be reviewed (HUMAN_REVIEWED) or replaced by reviewer-supplied values "
            "(HUMAN_SUPPLEMENTED) before it can enter the CAD pipeline."
        )

    # Material is optional engineering content, but a present value without a provenance
    # label is contradictory: no value may exist without saying who owns it. (A material
    # still labelled AI_EXTRACTED is NOT rejected here — the existing 7Z provenance blocker
    # is the authority that keeps unconfirmed AI values out of fabrication output.)
    if spec.material is not None and "material" not in spec.provenance:
        return (
            f"{label}: material is present ({spec.material!r}) but carries no provenance label; "
            "every reviewed value must be labelled HUMAN_REVIEWED or HUMAN_SUPPLEMENTED (or "
            "AI_EXTRACTED while still awaiting confirmation)."
        )

    return None


def check_reviewed_connection_specification_completeness(
    spec: ReviewedConnectionSpecification,
) -> ReviewedConnectionCompletenessResult:
    """
    1. Shape check — malformed structures become an ordinary incomplete
       result (never a ValueError/TypeError/AttributeError).
    2. Review-authority gate — review_status "approved" plus valid,
       non-AI_EXTRACTED provenance on every required field.
    3. The three existing adapter calls, in order, never re-implementing
       their own field checks; stops at the first failure and reports
       it unchanged.

    A complete result means all three steps passed and returns the real
    adapter outputs directly. It never means the connection is
    structurally adequate. Deterministic and side-effect free: never
    mutates `spec`, never touches CadQuery, never calls
    7M/7K/7L/7P/7Q/7R, and never repairs or infers a missing value.
    """
    provenance_copy = dict(spec.provenance) if isinstance(spec.provenance, dict) else {}

    shape_error = _shape_error(spec)
    if shape_error is not None:
        return ReviewedConnectionCompletenessResult(False, None, None, None, shape_error, provenance_copy)

    authority_error = _review_authority_error(spec)
    if authority_error is not None:
        return ReviewedConnectionCompletenessResult(False, None, None, None, authority_error, provenance_copy)

    connection_record = reviewed_connection_specification_to_connection_record(spec)
    try:
        validated_connection = real_connection_to_validated_connection(connection_record)
    except GeometryValidationError as e:
        return ReviewedConnectionCompletenessResult(False, None, None, None, str(e), provenance_copy)

    location_record = reviewed_connection_specification_to_location_record(spec)
    try:
        connection_location = real_connection_location_to_connection_location(location_record)
    except GeometryValidationError as e:
        return ReviewedConnectionCompletenessResult(
            False, validated_connection, None, None, str(e), provenance_copy,
        )

    detail_record = reviewed_connection_specification_to_detail_record(spec)
    try:
        attachments = reviewed_connection_detail_to_attachments(
            detail_record, validated_connection.connected_members,
        )
    except GeometryValidationError as e:
        return ReviewedConnectionCompletenessResult(
            False, validated_connection, connection_location, None, str(e), provenance_copy,
        )

    return ReviewedConnectionCompletenessResult(
        True, validated_connection, connection_location, attachments, None, provenance_copy,
    )
