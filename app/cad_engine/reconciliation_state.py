"""
J68 — RECONCILIATION STATE MODEL (J67 slice S1).

WHAT THIS FILE IS

The pure, deterministic state model J67 designed and nothing more: a closed state
vocabulary, J67's field-authority matrix as immutable data, and the rules that say which
state one engineering field of one reviewed connection is in.

    FIELD
      ├── recorded evidence kinds            ──▶ UNCITED / DIRECT / DERIVED
      ├── human provenance + a resolved answer ──▶ HUMAN_RESOLVED
      └── CONFLICTED                          representable, DELIBERATELY NOT INFERRED HERE

It is a MODEL, not a reader. It does not read the recorded evidence, does not read the
review record, does not touch the database, does not write anything, and does not decide
anything about a real connection. A caller assembles `FieldReviewEvidence` from whatever the
existing record already carries and asks this module for a state.

WHY IT DOES NOT IMPORT J66'S KIND VOCABULARY

The two evidence kinds (`SOURCE`, `DERIVATION`) are declared HERE as plain strings rather
than imported from `app/cad_engine/review_contract.py`, and a consistency test pins this
copy to J66's constants. The reason is a J66 fence this milestone must not disturb: a J66
test asserts that exactly three modules in `app/` name that vocabulary at all,
because that is how J66 makes "no production writer exists" checkable rather than promised.
A module that imported the names would put a fourth name-bearing file in the tree and break
a fence J66 owns. This is the same trade the existing 7AC module already documents for
`CONNECTION_POSITION_CHOICES` — "referenced rather than imported … a consistency test pins
this copy to the source" — and it is the design J67 §2 asked for: a model that can express
the concept WITHOUT changing J66, translated at the boundary later.

Everything else IS imported, never restated: the engineering-field expression (7W/J66), the
provenance vocabulary (7V) and the resolved-review vocabulary (7AC).

WHAT IT DELIBERATELY DOES NOT DO

  * It does NOT detect conflicts. `CONFLICTED` is a state this vocabulary can express and
    J68 has no rule that produces it (`CONFLICT_DETECTION_IMPLEMENTED` is False, asserted).
    Two SOURCE readings — even from two different documents — are DIRECT, because this model
    cannot see documents at all: `FieldReviewEvidence` carries no document, drawing, role,
    page or path, so document origin cannot influence a state. That is what makes J67's §4
    guarantee STRUCTURAL rather than a promise. Deciding that two recordings DISAGREE is J67
    slice S2's work, and it needs the recordings themselves, which a state model must not
    copy (see below).
  * It does NOT copy, normalise, compare, rank, choose or convert any value. It holds no
    value at all except as the input to three explicit, narrow predicates, each of which
    answers a yes/no question and returns nothing derived from the value.
  * It does NOT let a document role produce a winner or select a document. No function here
    takes a role or a document. The role names appear only inside the authority data, as the
    roles J67 established CAN PROPOSE a field — a statement of expectation, never a
    precedence, and never consulted by any state rule.
  * It does NOT change J66. Nothing in this module reads, writes or names J66's tables.

WHAT IT DOES NOT CLAIM

Nothing here says a connection is reviewable, complete, fabricable, safe or approved. A
state says only WHERE one field's content is accounted for at one revision. `connection_id`
and `holes` carry explicit, narrow caveats recorded on their own predicates and matrix rows.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_FIELD_DECISION,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
    STATUS_RESOLVED,
    ExceptionResolutionTask,
)
from app.cad_engine.review_contract import ENGINEERING_FIELDS
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)

__all__ = [
    # the state vocabulary
    "RECONCILIATION_UNCITED", "RECONCILIATION_DIRECT", "RECONCILIATION_DERIVED",
    "RECONCILIATION_CONFLICTED", "RECONCILIATION_HUMAN_RESOLVED", "RECONCILIATION_STATES",
    "CONFLICT_DETECTION_IMPLEMENTED",
    # the evidence-kind vocabulary (a pinned copy of J66's; see the module docstring)
    "EVIDENCE_KIND_SOURCE", "EVIDENCE_KIND_DERIVATION", "EVIDENCE_KINDS",
    # the field vocabulary
    "CONNECTION_ID_FIELD", "RECONCILIATION_FIELDS",
    # the role vocabulary (data only: no function here consumes a role)
    "ROLE_TRANSMITTAL", "ROLE_STRUCTURAL_GA", "ROLE_ASSEMBLY", "ROLE_FABRICATION",
    "ROLE_ISOMETRIC", "PROPOSING_ROLE_VOCABULARY",
    # the human-resolution vocabulary, carried from the existing record
    "HUMAN_PROVENANCE_LABELS", "RESOLVING_ANSWER_TYPES", "NON_RESOLVING_ANSWER_TYPES",
    "resolved_answer_types",
    # the model
    "FieldReviewEvidence", "FieldAuthority", "FieldReconciliationState", "FIELD_AUTHORITY",
    "is_reconciliation_field", "field_authority", "has_human_provenance", "is_human_resolved",
    "field_state", "field_states",
    # the narrow completeness predicates (the only value-shaped questions asked)
    "holes_diameter_is_explicit_numeric", "holes_are_complete",
    "connection_identity_is_supplied",
]


# --------------------------------------------------------------------------------------
# The state vocabulary.
#
# The first three names are the SAME states J66 already computes from a field's recorded
# evidence, re-declared here so this model owns its vocabulary and can be extended without
# editing J66. A test pins the three equal to review_contract's STANDING_* values, so the
# pair cannot drift. CONFLICTED and HUMAN_RESOLVED are the two J68 adds as REPRESENTABLE
# states; only HUMAN_RESOLVED has a rule that produces it.
# --------------------------------------------------------------------------------------
RECONCILIATION_UNCITED = "UNCITED"
RECONCILIATION_DIRECT = "DIRECT"
RECONCILIATION_DERIVED = "DERIVED"
RECONCILIATION_CONFLICTED = "CONFLICTED"
RECONCILIATION_HUMAN_RESOLVED = "HUMAN_RESOLVED"

RECONCILIATION_STATES = (
    RECONCILIATION_UNCITED,
    RECONCILIATION_DIRECT,
    RECONCILIATION_DERIVED,
    RECONCILIATION_CONFLICTED,
    RECONCILIATION_HUMAN_RESOLVED,
)

# J68 records NO conflict rule. This is a stated, asserted fact rather than a comment: a
# reader (or a test) can ask whether conflict detection exists and get a definite answer.
# J67 slice S2 turns this True when it lands, and this module's `_state_is_conflicted` is
# the single seam it replaces.
CONFLICT_DETECTION_IMPLEMENTED = False


# --------------------------------------------------------------------------------------
# The evidence-kind vocabulary — a PINNED COPY of J66's, not an import.
#
# How the recorded evidence relates to a field: SOURCE is the reading the content was taken
# from, DERIVATION is a reading it was computed or interpreted from. The pair is CLOSED and
# carries no quality, no confidence, no rank and no precedence — which of two sources is
# better is a question this milestone deliberately does not answer. A consistency test pins
# these two values to `review_contract`'s own constants; see the module docstring for why
# they are copied rather than imported.
# --------------------------------------------------------------------------------------
EVIDENCE_KIND_SOURCE = "SOURCE"
EVIDENCE_KIND_DERIVATION = "DERIVATION"
EVIDENCE_KINDS = (EVIDENCE_KIND_SOURCE, EVIDENCE_KIND_DERIVATION)


# --------------------------------------------------------------------------------------
# The field vocabulary.
#
# connection_id first — it is the identity every other field hangs off, and the one field
# with a rule of its own (see `field_state`) — then the seven engineering-content fields in
# the order the existing vocabulary already publishes, taken from the one authoritative list
# rather than restated. A field added to the engineering vocabulary joins this model
# automatically.
# --------------------------------------------------------------------------------------
CONNECTION_ID_FIELD = "connection_id"
RECONCILIATION_FIELDS = (CONNECTION_ID_FIELD,) + ENGINEERING_FIELDS


# --------------------------------------------------------------------------------------
# The role vocabulary — READ FROM J64's stored list, and never inferred.
#
# These five are the document roles J67's field-authority matrix names as able to PROPOSE
# at least one field. They are carried as plain strings so this module imports no store; a
# test pins every one of them to `repository.DOCUMENT_ROLES`, the same list J64's live
# CHECK constraint enforces, so a role cannot be invented here.
#
# ROLE_TRANSMITTAL is deliberately in the vocabulary and deliberately proposes NO field:
# J67 established that a transmittal can establish nothing about a connection's fields and
# speaks only to document-set coverage. That absence is asserted, not accidental.
# --------------------------------------------------------------------------------------
ROLE_TRANSMITTAL = "TRANSMITTAL"
ROLE_STRUCTURAL_GA = "STRUCTURAL_GA"
ROLE_ASSEMBLY = "ASSEMBLY"
ROLE_FABRICATION = "FABRICATION"
ROLE_ISOMETRIC = "ISOMETRIC"

PROPOSING_ROLE_VOCABULARY = (
    ROLE_TRANSMITTAL,
    ROLE_STRUCTURAL_GA,
    ROLE_ASSEMBLY,
    ROLE_FABRICATION,
    ROLE_ISOMETRIC,
)


# --------------------------------------------------------------------------------------
# The human-resolution vocabulary, carried from the existing record.
# --------------------------------------------------------------------------------------
# A provenance label that means a HUMAN owns this field's content. AI_EXTRACTED is
# deliberately absent: an AI reading is not a human resolution, however it was obtained.
HUMAN_PROVENANCE_LABELS = (PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED)

# The existing answer types that establish a HUMAN RESOLUTION of a field, taken from 7AC's
# vocabulary. ANSWER_FIELD_DECISION is the existing field-decision shape (the
# confirm/supply/clear action on one field); the rest are the single- and grouped-field value
# answers, with the two grouped answers included because each resolves several fields at once.
#
# Three members of the existing vocabulary are deliberately ABSENT, because none of them
# resolves a field: ANSWER_APPROVE_REVIEW sets the review STATUS, and ANSWER_ACKNOWLEDGMENT
# and ANSWER_AUTOMATION_CONFIRMATION change no field at all (7W's own contract — they record
# that a human looked, not that a human decided). Naming them here would let a connection be
# reported as resolved on the strength of an acknowledgment.
RESOLVING_ANSWER_TYPES = (
    ANSWER_FIELD_DECISION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_POSITION_VALUE,
    ANSWER_PLATE_VALUE,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_CONNECTION_IDENTITY,
)

# The existing answer types that resolve NO field. They are named so the omission above is
# visible and testable rather than an unexplained gap in the tuple.
NON_RESOLVING_ANSWER_TYPES = (
    ANSWER_APPROVE_REVIEW,
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_AUTOMATION_CONFIRMATION,
)


# --------------------------------------------------------------------------------------
# The model.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldReviewEvidence:
    """
    Everything the EXISTING review record already carries about ONE engineering field,
    reduced to primitives a state can be computed from — and nothing value-shaped.

    `evidence_kinds` is the field's recorded evidence kinds, in recording order
    (`SOURCE` / `DERIVATION`, J66's closed pair). The KIND is all this model takes: it never
    sees the reading, the document or the value, so it cannot compare two readings, and it
    cannot prefer one document to another. A kind outside the pair is tolerated and fails
    closed (see `field_state`).

    `provenance` is the field's existing provenance label (7V), verbatim, or None.

    `resolved_answer_types` are the answer types of the field's RESOLVED review tasks
    (7AC), verbatim. An OPEN task's answer is not a resolution and must not be passed here.

    `reviewer_supplied` says whether the existing record carries a reviewer-supplied value
    for this field. It exists because identity is not an engineering field: `connection_id`
    carries NO provenance label (7W's documented contract — an identifier, not an
    engineering value), so provenance alone cannot show that a human supplied it. For every
    other field this flag is redundant with the provenance label and is not consulted.
    """
    field: str
    evidence_kinds: tuple[str, ...] = ()
    provenance: str | None = None
    resolved_answer_types: tuple[str, ...] = ()
    reviewer_supplied: bool = False


@dataclass(frozen=True)
class FieldAuthority:
    """
    J67's field-authority matrix row for ONE field: what the architecture says about who can
    propose it, whether more than one proposal is expected, whether proposals can legitimately
    disagree, whether a human must own it, and the completeness / unresolved statements.

    `proposing_roles` is a SORTED tuple and its order carries NO authority. It answers "who
    can propose this field", never "whose proposal wins". Nothing in this module reads it to
    produce a state, and no function here takes a role at all.

    `multiple_sources_expected` and `disagreement_possible` are properties of the EVIDENCE
    situation, not of any reading: they say a field will normally be proposed more than once
    and that those proposals may legitimately differ. `disagreement_possible` is where a
    conflict would be EXPECTED to be possible — it is not a claim that one exists, and this
    milestone computes no conflict from it.

    `human_ownership_required` is True for every field, including `connection_id`, which no
    role can propose at all and which therefore ONLY a human can supply.
    """
    field: str
    proposing_roles: tuple[str, ...]
    multiple_sources_expected: bool
    disagreement_possible: bool
    human_ownership_required: bool
    completeness_rule: str
    unresolved_condition: str


# J67's matrix, verbatim in substance and in field order. Read every `proposing_roles` as
# "may propose", never "ranks above". No role appears as a winner anywhere in this module.
FIELD_AUTHORITY = (
    FieldAuthority(
        field=CONNECTION_ID_FIELD,
        proposing_roles=(),
        multiple_sources_expected=False,
        disagreement_possible=False,
        human_ownership_required=True,
        completeness_rule=(
            "a reviewer-supplied connection identifier, non-empty after stripping, unique "
            "within the project"
        ),
        unresolved_condition=(
            "no reviewer-supplied identity exists; the field has no proposing role, so it "
            "can never become complete from evidence — identity is never AI output"
        ),
    ),
    FieldAuthority(
        field="connected_member_marks",
        proposing_roles=(ROLE_ASSEMBLY, ROLE_FABRICATION, ROLE_STRUCTURAL_GA),
        multiple_sources_expected=True,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule="every mark resolves to a validated project member",
        unresolved_condition=(
            "any mark does not resolve to a validated member (the existing member-identity "
            "blocker)"
        ),
    ),
    FieldAuthority(
        field="position",
        proposing_roles=(ROLE_FABRICATION, ROLE_ISOMETRIC),
        multiple_sources_expected=False,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule=(
            "one of the existing CONNECTION_POSITION_CHOICES ('START'/'END'), chosen by a "
            "reviewer"
        ),
        unresolved_condition="no reviewer-selected position",
    ),
    FieldAuthority(
        field="plate",
        proposing_roles=(ROLE_ASSEMBLY, ROLE_FABRICATION),
        multiple_sources_expected=True,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule="a plate mapping the existing 7D adapter accepts",
        unresolved_condition="no plate is present, or a present plate carries no human provenance",
    ),
    FieldAuthority(
        field="holes",
        proposing_roles=(ROLE_ASSEMBLY, ROLE_FABRICATION, ROLE_STRUCTURAL_GA),
        multiple_sources_expected=True,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule=(
            "an EXPLICIT NUMERIC hole-void diameter (see "
            "`holes_diameter_is_explicit_numeric`), supplied by a reviewer"
        ),
        unresolved_condition=(
            "no explicit numeric diameter exists. A nominal bolt size is not a hole "
            "diameter: 'M20', '22mm holes' and '18mm holes' are page text, and nothing "
            "here parses them"
        ),
    ),
    FieldAuthority(
        field="location",
        proposing_roles=(ROLE_FABRICATION, ROLE_STRUCTURAL_GA),
        multiple_sources_expected=True,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule="the project-space location the existing adapter accepts",
        unresolved_condition=(
            "no location is present — it is never derived, because the existing member "
            "placements are deliberately not populated at the baseline revision"
        ),
    ),
    FieldAuthority(
        field="attachments",
        proposing_roles=(ROLE_ASSEMBLY, ROLE_FABRICATION),
        multiple_sources_expected=True,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule="one attachment per connected member, each resolving to a validated face",
        unresolved_condition="an attachment that does not resolve to a validated face",
    ),
    FieldAuthority(
        field="material",
        proposing_roles=(ROLE_ASSEMBLY, ROLE_FABRICATION, ROLE_STRUCTURAL_GA),
        multiple_sources_expected=True,
        disagreement_possible=True,
        human_ownership_required=True,
        completeness_rule=(
            "the material designation, verbatim, WHEN one is stated; absence is legal and "
            "is not an incompleteness"
        ),
        unresolved_condition=(
            "a material value is present but carries no human provenance label. Absence is "
            "NOT this condition: an unspecified material is reported downstream as MATERIAL "
            "NOT SPECIFIED rather than treated as unresolved"
        ),
    ),
)


@dataclass(frozen=True)
class FieldReconciliationState:
    """One field's reconciliation state. A state, and nothing else: no value, no ranking."""
    field: str
    state: str


def is_reconciliation_field(field: object) -> bool:
    """True exactly when `field` is one of the fields this model describes."""
    return isinstance(field, str) and field in RECONCILIATION_FIELDS


def field_authority(field: str) -> FieldAuthority:
    """
    The authority row for one field. An unknown field is REFUSED rather than given a
    default: a default row would let a caller compute a state for a field the architecture
    never described, and the state would then be a claim about a field this model does not
    know. The message names the known fields so the refusal is actionable.
    """
    for authority in FIELD_AUTHORITY:
        if authority.field == field:
            return authority
    raise ValueError(
        f"{field!r} is not a reconciliation field (fields: {list(RECONCILIATION_FIELDS)}); "
        "no state is computed for a field this model does not describe."
    )


def has_human_provenance(provenance: str | None) -> bool:
    """
    True exactly when a provenance label says a HUMAN owns this field's content. A label
    outside the existing vocabulary is not a human provenance — and `AI_EXTRACTED`, the
    label every unreviewed reading carries, is explicitly NOT one.
    """
    return provenance in HUMAN_PROVENANCE_LABELS


def is_human_resolved(evidence: FieldReviewEvidence) -> bool:
    """
    True when the EXISTING record shows a human resolved this field: a human provenance
    label AND at least one resolved answer. Both halves are required — provenance alone can
    exist without a task ever having been answered, and an answer alone leaves the content
    unlabelled. Requiring both is J67's own wording ("human provenance and resolved review
    state"), and it fails closed: a field missing either half is not reported as resolved.

    `connection_id` is the documented special case (J67 §9): it is an IDENTIFIER, not an
    engineering field, so it carries no provenance label at all, and the signal that a human
    supplied it is the reviewer-supplied value itself. Reading the engineering rule onto it
    would leave every supplied identity permanently unresolved.
    """
    if evidence.field == CONNECTION_ID_FIELD:
        return bool(evidence.reviewer_supplied)
    return has_human_provenance(evidence.provenance) and bool(evidence.resolved_answer_types)


def _evidence_standing(kinds: Iterable[str]) -> str:
    """
    The standing a field's recorded evidence KINDS alone support — J66's rule, restated over
    this model's vocabulary and failing closed in the same two directions:

      * any kind that is not SOURCE makes the field DERIVED, so a field with even one
        derivation takes the weaker standing and a derivation can never be presented as a
        direct reading;
      * a kind this module does not recognise is treated as a derivation rather than
        skipped, so an unknown kind can never make a field look MORE directly sourced than
        it is;
      * all SOURCE is DIRECT — which is a statement about the KIND of the recordings and NOT
        a statement that the recorded readings agree. This model cannot know whether they
        agree, and does not guess;
      * no recorded evidence is UNCITED.
    """
    kinds = tuple(kinds)
    if not kinds:
        return RECONCILIATION_UNCITED
    if any(kind != EVIDENCE_KIND_SOURCE for kind in kinds):
        return RECONCILIATION_DERIVED
    return RECONCILIATION_DIRECT


def _state_is_conflicted(evidence: FieldReviewEvidence) -> bool:
    """
    The single seam J67 slice S2 replaces. J68 records NO conflict rule, so this is False
    for every evidence — including two SOURCE readings, and including two that came from
    different documents, which this model cannot even see.

    It exists as a named function rather than an inline `False` so that the guard is a thing
    a mutation test can remove and watch an assertion go red: a guard that cannot fail is
    not a guard, and "J68 does not detect conflicts" must be provable rather than asserted.
    """
    return False


def field_state(evidence: FieldReviewEvidence) -> str:
    """
    ONE field's reconciliation state, decided in this order:

      1. an already-conflicted field stays CONFLICTED until a human rules — the rule is J68's
         `_state_is_conflicted`, which records no conflict at all;
      2. a human resolution wins: provenance plus a resolved answer means a reviewer has
         ruled on this field, and a ruling OUTRANKS whatever the recorded evidence said,
         because it is the later fact. `connection_id` is the documented special case (see
         `is_human_resolved`);
      3. `connection_id`, still unsupplied, is UNCITED — never DIRECT or DERIVED, because no
         recorded evidence can be a supporting address for an identifier;
      4. otherwise the evidence kinds decide, by J66's rule (`_evidence_standing`).

    A field outside the vocabulary is REFUSED, exactly as J66 refuses evidence of one:
    silently answering would compute a state over a record this model cannot read.
    """
    if not is_reconciliation_field(evidence.field):
        raise ValueError(
            f"{evidence.field!r} is not a reconciliation field "
            f"(fields: {list(RECONCILIATION_FIELDS)}); no state is computed over evidence "
            "this model cannot read."
        )
    if _state_is_conflicted(evidence):
        return RECONCILIATION_CONFLICTED
    if is_human_resolved(evidence):
        return RECONCILIATION_HUMAN_RESOLVED
    if evidence.field == CONNECTION_ID_FIELD:
        # The documented special case, made STRUCTURAL rather than cosmetic: identity is
        # never AI output, so no recorded reading of it can be a supporting address and none
        # may raise it above UNCITED. Reporting a DIRECT identity would assert that a reading
        # supplied an identifier — the one thing J67 §9 says cannot happen. The state is
        # HUMAN_RESOLVED when a reviewer supplied one (handled above) and UNCITED otherwise,
        # whatever any recorded evidence says.
        return RECONCILIATION_UNCITED
    return _evidence_standing(evidence.evidence_kinds)


def field_states(evidences: Iterable[FieldReviewEvidence]) -> tuple[FieldReconciliationState, ...]:
    """
    EVERY reconciliation field's state, in the field vocabulary's own order, so a field with
    no evidence is STATED as UNCITED rather than omitted — the rule J66's `field_standings`
    already follows.

    Evidence for a field OUTSIDE the vocabulary is refused rather than dropped: a state
    computed while silently discarding part of the record would be a claim about evidence
    this model never saw. At most one evidence per field is accepted; a duplicate is a
    caller error, because two entries for one field leave the state ambiguous.
    """
    by_field: dict[str, FieldReviewEvidence] = {}
    for evidence in evidences:
        if not is_reconciliation_field(evidence.field):
            raise ValueError(
                f"{evidence.field!r} is not a reconciliation field "
                f"(fields: {list(RECONCILIATION_FIELDS)}); no state is computed over "
                "evidence this model cannot read."
            )
        if evidence.field in by_field:
            raise ValueError(
                f"{evidence.field!r} has more than one evidence entry; a field has exactly "
                "one reconciliation state."
            )
        by_field[evidence.field] = evidence
    return tuple(
        FieldReconciliationState(
            field,
            field_state(by_field[field]) if field in by_field
            else RECONCILIATION_UNCITED,
        )
        for field in RECONCILIATION_FIELDS
    )


def resolved_answer_types(tasks: Iterable[ExceptionResolutionTask]) -> tuple[str, ...]:
    """
    The answer types of the RESOLVED tasks among `tasks` — the shape `FieldReviewEvidence`
    expects, read from the EXISTING task records rather than from a caller's summary of them.

    Only a RESOLVED task counts. An OPEN task's question is not an answer, and a task that
    is OPEN because a reviewer started and did not finish must not make its field look
    resolved. A RESOLVED task always carries its stored resolution (7AC sets the two
    together); one that somehow did not is skipped rather than guessed at.
    """
    return tuple(
        task.resolution.answer_type
        for task in tasks
        if task.status == STATUS_RESOLVED and task.resolution is not None
    )


# --------------------------------------------------------------------------------------
# The narrow completeness predicates.
#
# These are the ONLY places in this module that look at a value, and each answers a yes/no
# question and returns nothing derived from it. None parses, coerces, normalises, converts
# or repairs anything.
# --------------------------------------------------------------------------------------
def holes_diameter_is_explicit_numeric(holes_value: object) -> bool:
    """
    True exactly when `holes_value` carries an explicit NUMERIC hole-void diameter — the
    value the existing 7D adapter reads as `diameter_mm`.

    A NOMINAL BOLT SIZE IS NOT A HOLE DIAMETER, and this predicate is where that boundary is
    enforced for the state model. 'M20', '22mm holes' and '18mm holes' are STRINGS, so they
    are not numeric and this returns False for every one of them. There is deliberately NO
    parsing: a numeric string like '22' is also refused, because accepting it would mean this
    model had interpreted a page's text into an engineering value, which is exactly the
    conversion the review boundary exists to prevent.

    A bool is refused (Python's bools are ints, and `True` is not a diameter) and a
    non-finite float is refused (NaN and infinity are not diameters).
    """
    if not isinstance(holes_value, Mapping):
        return False
    if "diameter_mm" not in holes_value:
        return False
    diameter = holes_value["diameter_mm"]
    if isinstance(diameter, bool):
        return False
    if not isinstance(diameter, (int, float)):
        return False
    return math.isfinite(diameter)


def holes_are_complete(holes_value: object, *, provenance: str | None) -> bool:
    """
    The ONE field-specific completeness rule J68 encodes, because J67 pinned it for this
    field alone: `holes` is complete exactly when an explicit numeric hole-void diameter
    exists AND a human owns the value.

    Both halves are required and neither is inferred. A page that says 'ALL HOLES Ø22 mm
    UNO', or a bolt schedule that says 'M20', leaves this False — not because the reading is
    wrong, but because no reviewer has yet said what the hole diameter IS. The other seven
    fields' completeness rules stay stated in the matrix and are decided by the existing 7V /
    7D adapters, which this model does not re-implement.
    """
    return holes_diameter_is_explicit_numeric(holes_value) and has_human_provenance(provenance)


def connection_identity_is_supplied(identity: object) -> bool:
    """
    True exactly when `identity` is a reviewer-supplied connection identifier: a string with
    at least one non-whitespace character.

    Identity is NEVER AI output (J67 §9). No extraction produces one, so this predicate has
    no AI-side counterpart and no role can satisfy it — which is the point. A blank or
    whitespace-only string is not an identity, and neither is any non-string.
    """
    return isinstance(identity, str) and bool(identity.strip())
