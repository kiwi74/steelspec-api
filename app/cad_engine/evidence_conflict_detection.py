"""
J69 — MULTI-DOCUMENT EVIDENCE CONFLICT DETECTION (J67 slice S2).

WHAT THIS FILE IS

The deterministic detector J67 slice S2 designed, and the layer that fills the seam J68 left
open. It answers exactly one question about one engineering field of one reviewed connection:

    "Do the recordings this field's content is accounted for by DISAGREE?"

and it answers it with a state from J68's own vocabulary. It is the C layer of J67's
architecture, with the surrounding layers named for what they are:

    SOURCE DOCUMENT
      ↓
    CAPTURE / EVIDENCE                 the immutable reading, in exactly one copy
      ↓
    J66 RECORDED EVIDENCE REFERENCE    an ADDRESS into that reading          (not read here)
      ↓
    J69  A. evidence loading           the caller supplies the readings     (a seam, below)
         B. canonicalisation           comparable_value()                   (this module)
         C. comparison                 _disagreement_between()              (this module)
         D. reconciliation state       reconcile_field()                    (this module)

It is a DETECTOR. It does not resolve anything.

WHAT IT DOES NOT DO, SAID PLAINLY

  * It does NOT choose a winning reading, rank two recordings, prefer a document, prefer a
    role, or record a "primary source". No function here takes a role or a document, and no
    dataclass here has a field for a winner, a rank, a confidence or a priority. When two
    recordings disagree, BOTH are carried out of the result with the address and the value
    each of them stated, and neither is marked as the one that counts.
  * It does NOT resolve a conflict. A human resolution is the only thing that outranks a
    disagreement, and this module READS that resolution from the existing review record
    (J68's rule, unchanged) rather than creating a mechanism of its own.
  * It does NOT parse anything. "22mm holes", "18mm holes", "M20" and "Ø22" are TEXT. No
    numeric diameter is ever recovered from page text here; the hole rule is J68's own
    `holes_diameter_is_explicit_numeric`, which accepts only an explicit numeric value in the
    structured representation the existing adapter reads.
  * It does NOT invent an equivalence rule. Where two representations cannot be safely
    canonicalised into one comparable form, `comparable_value` returns None, the recording is
    reported as UNRESOLVED, and NO conflict is claimed from it. That is the fail-closed
    direction: an unrecognised shape can hide a disagreement, never manufacture one.
  * It does NOT read the database, the storage layer or the network. Layer A is a seam: the
    caller supplies the readings, because a module in this layer must not know what a
    Supabase client is. See "WHY LOADING IS A SEAM" below.
  * It does NOT touch J66. It names none of J66's vocabulary, imports none of its constants,
    and reads none of its storage — J66's own tree-wide fence stays exact because of that,
    and a test in the J69 file asserts it case-insensitively over this file's whole text.
    The evidence-kind pair is taken from J68, which already carries a pinned copy of it.

WHY LOADING IS A SEAM, AND WHAT IT MEANS FOR WHAT CAN ACTUALLY CONFLICT

A recorded reference names a PAGE READING — (drawing, page, run) — plus, sometimes, one
annotation occurrence and an `anchor` locator. It does NOT name a connection. A page reading
is the whole page: its payload is one extraction result that may carry several connections.

So turning an address into "this field's value, for THIS connection" needs a selection the
address alone does not make, and only the anchor can even suggest it. Nothing in the existing
system performs that selection, and this milestone must not invent one: guessing which
connection on a page a reference meant would be inventing the agreement or disagreement it
then reports.

The consequence is stated rather than hidden: `reconcile_field` detects a disagreement only
where the caller has actually supplied readings for at least two references of one field.
Where it has not, the references are reported as UNRESOLVED and the state falls back to
J68's, which is DIRECT or DERIVED — a statement about the KIND of the recordings, never a
claim that they agree. `FieldReconciliationResult.unresolved` is what keeps that visible, and
a test asserts that a missing reading is never read as agreement.

WHICH FIELDS HAVE A COMPARABLE FORM, AND WHICH DO NOT

`comparable_value` has a canonicaliser for SEVEN of J67's eight fields. `connection_id` has
none, deliberately: an identifier is never AI output (J67 §9), no reading of one can
establish one, and a second identity is not a disagreement about an engineering value — so
two identities can never conflict here. That absence IS the rule, and it is asserted
structurally rather than stated: the canonicaliser table is pinned to exactly the seven
content fields.

The seven, and the EXISTING representation each is canonicalised from — none invented here:

    connected_member_marks  a sequence of marks, compared as a SET. The existing 7N adapter
                            already does exactly this (`seen_marks != set(...)`), so set
                            semantics is the existing engineering meaning, not a new rule.
    position                one of the existing CONNECTION_POSITION_CHOICES ('START'/'END').
                            Prose is never read for it.
    plate                   an object over the existing plate keys, complete. An incomplete
                            plate has no comparable form, because comparing a complete plate
                            against an incomplete one would manufacture a difference out of
                            an omission.
    holes                   an EXPLICIT NUMERIC hole-void diameter. Never page text.
    location                the existing 7J six-number form (x/y/z and three rotations), all
                            six explicit and finite. Never derived from a grid reference.
    attachments             a set of (member, surface) pairs, each surface from the existing
                            closed pair. Never inferred from layout.
    material                the designation VERBATIM. Never normalised, never case-folded.

WHAT IT DOES NOT CLAIM

A CONFLICTED result says two recordings of one field state different values. It says nothing
about which is right, nothing about the connection being wrong, and nothing about the project
being unreviewable. In particular the Selby Ø18-versus-Ø22 case J67 named is NOT resolved
here, NOT decided here, and NOT even reachable from the evidence this system holds today:
the AI connection schema has no hole field at all, so a numeric hole diameter can only arrive
from a caller that states one explicitly.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.cad_engine import reconciliation_state as rs
from app.cad_engine.exception_resolution import CONNECTION_POSITION_CHOICES

__all__ = [
    # the vocabulary this module owns
    "PLATE_KEYS", "PLATE_DIMENSION_KEYS", "LOCATION_KEYS", "ATTACHMENT_KEYS",
    "POSITION_CHOICES",
    # the address and the recording
    "EvidenceAddress", "FieldEvidenceReference", "EvidenceReading",
    # the read model
    "CompetingEvidence", "FieldReconciliationResult",
    # the layers
    "canonical_value", "reconcile_field", "reconcile_fields",
    "human_resolution_stands",
]


# --------------------------------------------------------------------------------------
# The vocabularies this module needs, each a PINNED COPY of one that already exists.
#
# A consistency test in the J69 test file pins every one of them to its source, so a copy
# cannot drift. They are copies rather than imports for two different reasons, stated where
# each one is defined.
# --------------------------------------------------------------------------------------

# The plate keys `app/cad_engine/connection_review_package.py` publishes as `_PLATE_KEYS` and
# `_PLATE_DIMENSION_KEYS` and `app/cad_engine/real_connection_adapter.py` reads. COPIED
# because both names are private to their module: a public module may not reach into another
# module's underscore namespace, and a test pins this pair to the source instead.
PLATE_KEYS = ("type", "thickness_mm", "width_mm", "depth_mm")
PLATE_DIMENSION_KEYS = ("width_mm", "depth_mm", "thickness_mm")

# The existing 7J location form: three translation components in mm and three rotations in
# degrees, in the shared project coordinate system, all six required and none ever defaulted
# (`app/cad_engine/connection_location.py`). COPIED for the same reason, and pinned by test
# to the record `reviewed_connection_specification_to_location_record` produces.
LOCATION_KEYS = ("x", "y", "z", "rotation_x", "rotation_y", "rotation_z")

# The two keys of one 7N attachment entry, which is what the existing adapter reads and what
# the existing 7V shape check requires. COPIED and pinned by test.
ATTACHMENT_KEYS = ("member_mark", "surface_reference")

# The closed pair a `position` may state AND the closed pair an attachment's
# `surface_reference` may name — the existing 7N adapter accepts the very same two values for
# both, so one constant carries both uses rather than two that could drift apart.
#
# Taken from the EXISTING pinned copy `exception_resolution.CONNECTION_POSITION_CHOICES`
# rather than from `app/cad_engine/connections.SUPPORTED_CONNECTION_POSITIONS`, which is the
# authority: that module imports CadQuery, and a comparison layer has no business loading a
# geometry kernel to compare two strings. This is the same trade J68's evidence-kind copy
# records, and the reason is the one `exception_resolution.py` already documents for the very
# same constant.
POSITION_CHOICES = CONNECTION_POSITION_CHOICES


# --------------------------------------------------------------------------------------
# The address, the recording, and the reading.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class EvidenceAddress:
    """
    WHERE a recorded reading is: the same identity the evidence tables use, never a display
    name.

    `drawing_id`, `page_number` and `analysis_run_id` are required together, because a page
    reading is identified by all three — the page number alone is not a page identity, since
    every whole-document run creates a new drawing row, and the run is what makes a re-read a
    NEW READING rather than a collision.

    `document_id` is None when the reading predates document identity or was taken without
    it. None is NOT a wildcard: it matches nothing, and nothing here treats it as "any
    document". It is carried VERBATIM and is never compared, because J61's identity rule is
    not this layer's to reinterpret.

    The three occurrence coordinates are all present or all None — a position is never stated
    without the rule set it was read under, because the pair is what makes the occurrence an
    identity.

    `anchor` is the recorded locator within the reading, carried VERBATIM and never
    interpreted: this layer does not know what an anchor means and must not guess. It is an
    attribute of the address, never a value.
    """
    drawing_id: str
    page_number: int
    analysis_run_id: str
    document_id: str | None = None
    annotation_x: object = None
    annotation_y: object = None
    extractor_version: str | None = None
    anchor: object = ()


@dataclass(frozen=True)
class FieldEvidenceReference:
    """
    ONE recorded reference: an ADDRESS for one engineering field of one reviewed connection,
    plus the KIND of the relation and the recording order.

    There is no value here, and there may never be one. The content stays in exactly one
    copy, in the reading the address names; this layer is handed that reading's value
    separately, as an `EvidenceReading`, and never stores a second copy of it.

    `ordinal` is the RECORDING ORDER J66 gives the references of one field. It orders this
    module's output deterministically and is compared with nothing — it is not a priority, a
    preference or a rank, and the result it produces is the order the facts were recorded in,
    not the order they matter in.
    """
    field: str
    ordinal: int
    evidence_kind: str
    address: EvidenceAddress


@dataclass(frozen=True)
class EvidenceReading:
    """
    What was read AT one address, as the existing evidence carries it — the VALUE half of the
    lookup that `EvidenceAddress` is the key half of.

    `value` is the structured value found there, VERBATIM: a connection's connected member
    marks, its plate object, its material designation, or an explicit reviewer-supplied
    structured value. It is NOT canonicalised here, and nothing is copied out of the evidence
    store: the caller reads it, hands it over, and this module decides only whether it has a
    comparable form.

    Supplying two readings for one address is refused rather than resolved, because two
    readings of one address would make the value at that address ambiguous — and an
    ambiguous value is exactly the kind of thing a disagreement could be manufactured from.
    """
    address: EvidenceAddress
    value: object


# --------------------------------------------------------------------------------------
# The read model.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CompetingEvidence:
    """
    ONE side of a disagreement: the address a value was read at, and the comparable value
    read there.

    This is the whole of what a conflict exposes per side. There is deliberately no `winner`,
    no `preferred`, no `rank`, no `confidence`, no `role` and no `is_primary` — a reader that
    wants to prefer one of these has to decide to, which is the point. `value` is the value
    AT THAT ADDRESS and is never an authoritative value: it is the comparison's operand, and
    it is carried verbatim so a human can see what each reading actually said.
    """
    address: EvidenceAddress
    value: object


@dataclass(frozen=True)
class FieldReconciliationResult:
    """
    ONE field's reconciliation, and every input it was decided from.

    `references` is EVERY recording of the field, in recording order — none is filtered out,
    so a reader can always see what the state was computed over.
    `competing` is every recording that resolved to a comparable value, in recording order,
    EACH WITH ITS OWN VALUE. It is populated whether or not the state is CONFLICTED: when the
    readings agree it is the evidence that they agree, and when they disagree it is both
    sides, preserved. Nothing in it is ranked.
    `unresolved` is every address that was cited but produced no comparable value — no
    reading was supplied, or the value read there has no comparable form. It exists so that
    an unread recording can never be mistaken for one that agreed.
    """
    field: str
    state: str
    references: tuple[FieldEvidenceReference, ...] = ()
    competing: tuple[CompetingEvidence, ...] = ()
    unresolved: tuple[EvidenceAddress, ...] = ()


# --------------------------------------------------------------------------------------
# B. Canonicalisation — the comparable form of a value, or None when there is none.
# --------------------------------------------------------------------------------------
def _finite_number(value: object) -> bool:
    """True exactly when `value` is a real number a comparison may use: not a bool (Python's
    bools are ints, and `True` is not a dimension), not a string, and finite (NaN and the
    infinities are not dimensions, coordinates or diameters)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _canonical_marks(marks_value: object) -> object | None:
    """
    The comparable form of `connected_member_marks`: the SET of the marks.

    A set, because that is already the existing engineering meaning — the 7N attachment
    adapter compares the attachment marks to `set(connected_member_marks)` — so two
    recordings that name the same members in a different order are the SAME value and are not
    a disagreement. Comparison is exact and unforgiving, matching the existing member check:
    nothing is stripped, case-folded or fuzzily resolved.

    A value with no marks, a string (which is a sequence of characters, not a list of marks),
    or any element that is not a non-blank string has no comparable form.
    """
    if isinstance(marks_value, str) or not isinstance(marks_value, (list, tuple)):
        return None
    if not marks_value:
        return None
    for mark in marks_value:
        if not isinstance(mark, str) or not mark.strip():
            return None
    return frozenset(marks_value)


def _canonical_position(position_value: object) -> object | None:
    """
    The comparable form of `position`: one of the existing closed pair, exactly.

    'START' and 'END' are the only values the existing adapters accept. Nothing is inferred
    from prose and no orientation is inferred from page geometry, so a value that is not one
    of the pair has no comparable form.
    """
    if not isinstance(position_value, str) or position_value not in POSITION_CHOICES:
        return None
    return position_value


def _canonical_plate(plate_value: object) -> object | None:
    """
    The comparable form of `plate`: a COMPLETE plate, as a deterministic key/value tuple.

    Complete, because the existing review model treats an incomplete plate as unusable
    (`INCOMPLETE_AI_PLATE`, no candidate) — and because comparing a complete plate against an
    incomplete one would report an OMISSION as a disagreement about engineering content.

    An object carrying a key outside the existing plate vocabulary has no comparable form
    either: dropping it would silently compare a value this layer does not understand.
    """
    if not isinstance(plate_value, Mapping):
        return None
    if any(key not in PLATE_KEYS for key in plate_value):
        return None
    if any(key not in plate_value for key in PLATE_KEYS):
        return None
    plate_type = plate_value["type"]
    if not isinstance(plate_type, str) or not plate_type.strip():
        return None
    for key in PLATE_DIMENSION_KEYS:
        if not _finite_number(plate_value[key]):
            return None
    return tuple((key, plate_value[key]) for key in PLATE_KEYS)


def _canonical_holes(holes_value: object) -> object | None:
    """
    The comparable form of `holes`: an explicit NUMERIC hole-void diameter, or nothing.

    J68's rule, reused rather than restated. A NOMINAL BOLT SIZE IS NOT A HOLE DIAMETER:
    'M20', '22mm holes', '18mm holes' and even the numeric string '22' are TEXT, and text has
    no comparable form here. There is deliberately no parsing, because recovering a number
    from a page's words would be this module deciding what the drawing meant — and it would
    then report the difference between two strings as a disagreement about a diameter.
    """
    if not rs.holes_diameter_is_explicit_numeric(holes_value):
        return None
    return holes_value["diameter_mm"]


def _canonical_location(location_value: object) -> object | None:
    """
    The comparable form of `location`: the existing 7J six-number form, all six explicit.

    All six or nothing, matching the existing location adapter, which refuses a missing
    coordinate rather than defaulting it to zero. Nothing is derived from a grid reference, a
    page position or a drawing layout: a location this layer did not read explicitly is a
    location it must not compare.
    """
    if not isinstance(location_value, Mapping):
        return None
    if any(key not in LOCATION_KEYS for key in location_value):
        return None
    if any(key not in location_value for key in LOCATION_KEYS):
        return None
    for key in LOCATION_KEYS:
        if not _finite_number(location_value[key]):
            return None
    return tuple(location_value[key] for key in LOCATION_KEYS)


def _canonical_attachments(attachments_value: object) -> object | None:
    """
    The comparable form of `attachments`: the SET of (member, surface) pairs.

    A set, because an attachment list is a list of attachments for a set of connected members
    and the existing adapter already compares its marks as a set. Each surface must be one of
    the existing closed pair — an attachment face is never inferred from prose or layout.
    The member mark is stripped, which is what the existing adapter itself does before using
    it; nothing else is normalised.

    A duplicate member, a malformed entry, or an unrecognised surface has no comparable form.
    """
    if isinstance(attachments_value, str) or not isinstance(attachments_value, (list, tuple)):
        return None
    if not attachments_value:
        return None
    pairs: set[tuple[str, str]] = set()
    marks: set[str] = set()
    for entry in attachments_value:
        if not isinstance(entry, Mapping):
            return None
        if any(key not in ATTACHMENT_KEYS for key in entry):
            return None
        mark = entry.get("member_mark")
        surface = entry.get("surface_reference")
        if not isinstance(mark, str) or not mark.strip():
            return None
        if not isinstance(surface, str) or surface.strip() not in POSITION_CHOICES:
            return None
        mark = mark.strip()
        if mark in marks:
            return None
        marks.add(mark)
        pairs.add((mark, surface.strip()))
    return frozenset(pairs)


def _canonical_material(material_value: object) -> object | None:
    """
    The comparable form of `material`: the designation VERBATIM.

    No stripping, no case folding, no designation lookup. The existing material rule keeps a
    stated grade exactly as it was stated, so two recordings of the same grade in different
    spellings are two different values — which is a fact about the readings and is a
    disagreement a human should see, not one this layer may silently erase.

    An absent material has no comparable form, and that is NOT a conflict: an unspecified
    material is reported downstream as MATERIAL NOT SPECIFIED, and an omission is never a
    disagreement.
    """
    if not isinstance(material_value, str) or not material_value.strip():
        return None
    return material_value


# The canonicaliser of each field that HAS a comparable form. `connection_id` is absent, and
# its absence is the rule rather than an oversight: an identifier is never AI output, no
# reading of one can establish one, and two identities are not two engineering values that
# could disagree. A test pins this table to exactly the seven content fields.
_CANONICALISERS = {
    "connected_member_marks": _canonical_marks,
    "position": _canonical_position,
    "plate": _canonical_plate,
    "holes": _canonical_holes,
    "location": _canonical_location,
    "attachments": _canonical_attachments,
    "material": _canonical_material,
}


def canonical_value(field: str, value: object) -> object | None:
    """
    The COMPARABLE form of one field's value, or None when it has none.

    None is unambiguous: every comparable form this module produces is a set, a tuple, a
    string or a number, so None always means "no comparison is possible from this value" and
    never "the value is empty". A caller may therefore treat None as the fail-closed answer —
    an unrecognised or unparseable value can hide a disagreement, never manufacture one.

    A field outside J67's vocabulary is REFUSED, exactly as J68 refuses it: answering would
    compare a field this model does not describe.
    """
    if not rs.is_reconciliation_field(field):
        raise ValueError(
            f"{field!r} is not a reconciliation field "
            f"(fields: {list(rs.RECONCILIATION_FIELDS)}); no comparison is made for a field "
            "this model does not describe."
        )
    canonicaliser = _CANONICALISERS.get(field)
    return None if canonicaliser is None else canonicaliser(value)


# --------------------------------------------------------------------------------------
# C. Comparison — whether two comparable readings of one field disagree.
# --------------------------------------------------------------------------------------
def _disagreement_between(compared: tuple[tuple[EvidenceAddress, object], ...]) -> bool:
    """
    True exactly when the comparable readings of ONE field do not all state the same value.

    A single reading cannot disagree with itself, and no reading at all is not agreement —
    it is nothing to compare, which this returns False for and the result reports as
    UNRESOLVED rather than as a match.

    The comparison is exact equality over the canonical forms. It is symmetric — the same
    answer whichever order the readings arrived in — and it consults NO role, no document, no
    kind and no ordinal, so nothing about where a reading came from can decide whether it
    disagrees.
    """
    if len(compared) < 2:
        return False
    distinct: list[object] = []
    for _, value in compared:
        if value not in distinct:
            distinct.append(value)
    return len(distinct) > 1


# --------------------------------------------------------------------------------------
# D. Reconciliation state.
# --------------------------------------------------------------------------------------
def human_resolution_stands(
    field: str,
    *,
    provenance: str | None = None,
    resolved_answer_types: Iterable[str] = (),
    reviewer_supplied: bool = False,
) -> bool:
    """
    True when the EXISTING review record shows a human resolved this field — J68's rule,
    unchanged and reused rather than restated. `connection_id` is J68's documented special
    case and this delegates to it.

    A human resolution is the ONLY thing that outranks a disagreement, and this module reads
    it rather than creating a mechanism of its own: there is no new human-resolution concept
    here, and nothing in this file writes, stores or records one.
    """
    return rs.is_human_resolved(rs.FieldReviewEvidence(
        field=field,
        provenance=provenance,
        resolved_answer_types=tuple(resolved_answer_types),
        reviewer_supplied=reviewer_supplied,
    ))


def _ordered_references(
    field: str, references: Iterable[FieldEvidenceReference],
) -> tuple[FieldEvidenceReference, ...]:
    """
    One field's references in RECORDING order, or a refusal.

    A reference to another field, or two references carrying one ordinal, is refused rather
    than sorted around: the first would compute a state over another field's evidence, and
    the second leaves the recording order ambiguous — and an ambiguous order would make this
    module's output depend on the caller's input order, which is exactly what it must not.
    """
    collected = tuple(references)
    for reference in collected:
        if reference.field != field:
            raise ValueError(
                f"a reference for {reference.field!r} was supplied while reconciling "
                f"{field!r}; a reference belongs to the field it names."
            )
    ordered = tuple(sorted(collected, key=lambda reference: reference.ordinal))
    ordinals = [reference.ordinal for reference in ordered]
    if len(set(ordinals)) != len(ordinals):
        raise ValueError(
            f"{field!r} has two references with the same recording order {sorted(ordinals)}; "
            "the recording order must be unambiguous."
        )
    return ordered


def _readings_by_address(
    readings: Iterable[EvidenceReading],
) -> tuple[tuple[EvidenceAddress, object], ...]:
    """The supplied readings, refusing two for one address: the value at an address must not
    be ambiguous, because an ambiguous value is one a disagreement could be invented from."""
    collected = tuple(readings)
    addresses: list[EvidenceAddress] = []
    for reading in collected:
        if reading.address in addresses:
            raise ValueError(
                "two readings were supplied for one evidence address; the value at an "
                "address is either read once or is not read at all."
            )
        addresses.append(reading.address)
    return tuple((reading.address, reading.value) for reading in collected)


def reconcile_field(
    field: str,
    references: Iterable[FieldEvidenceReference] = (),
    readings: Iterable[EvidenceReading] = (),
    *,
    provenance: str | None = None,
    resolved_answer_types: Iterable[str] = (),
    reviewer_supplied: bool = False,
) -> FieldReconciliationResult:
    """
    ONE engineering field's reconciliation, decided in this order:

      1. a HUMAN RESOLUTION stands. It is the later fact and the only thing that outranks a
         disagreement, so a field a reviewer has ruled on is never reported as conflicted —
         J67 §17, and the one place this module's order differs from J68's, whose own state
         rule checks its conflict seam first because it has no conflict to check. J68 is not
         modified: this module asks it for the state and applies the precedence here.
      2. otherwise a genuine DISAGREEMENT between two comparable readings is CONFLICTED.
      3. otherwise J68's own state for the same field and the same kinds, which yields
         UNCITED, DIRECT or DERIVED — and never CONFLICTED, because J68 detects no conflict.
         `connection_id` is decided there too, and can never be CONFLICTED: see the module
         docstring for why an identifier has no comparable form at all.

    Every recording is carried out in the result whether or not it could be compared, so an
    unread recording is visible as unread and can never be mistaken for one that agreed.
    """
    if not rs.is_reconciliation_field(field):
        raise ValueError(
            f"{field!r} is not a reconciliation field "
            f"(fields: {list(rs.RECONCILIATION_FIELDS)}); no reconciliation is computed for "
            "a field this model does not describe."
        )

    ordered = _ordered_references(field, references)
    pairs = _readings_by_address(readings)

    compared: list[tuple[EvidenceAddress, object]] = []
    unresolved: list[EvidenceAddress] = []
    for reference in ordered:
        value = None
        for address, reading_value in pairs:
            # Matched by ADDRESS EQUALITY, never by position: the readings arrive in
            # whatever order the loader produced them, and this module's answer must not
            # depend on that order.
            if address == reference.address:
                value = canonical_value(field, reading_value)
                break
        if value is None:
            unresolved.append(reference.address)
        else:
            compared.append((reference.address, value))

    base = rs.field_state(rs.FieldReviewEvidence(
        field=field,
        evidence_kinds=tuple(reference.evidence_kind for reference in ordered),
        provenance=provenance,
        resolved_answer_types=tuple(resolved_answer_types),
        reviewer_supplied=reviewer_supplied,
    ))

    if base == rs.RECONCILIATION_HUMAN_RESOLVED:
        state = base
    elif _disagreement_between(tuple(compared)):
        state = rs.RECONCILIATION_CONFLICTED
    else:
        state = base

    return FieldReconciliationResult(
        field=field,
        state=state,
        references=ordered,
        competing=tuple(CompetingEvidence(address, value) for address, value in compared),
        unresolved=tuple(unresolved),
    )


def reconcile_fields(
    references: Iterable[FieldEvidenceReference] = (),
    readings: Iterable[EvidenceReading] = (),
    *,
    human_resolutions: Iterable[tuple[str, dict]] = (),
) -> tuple[FieldReconciliationResult, ...]:
    """
    EVERY reconciliation field, in J67's field order, so a field with no recording is STATED
    as UNCITED rather than omitted.

    `human_resolutions` is a sequence of `(field, facts)` pairs, where `facts` is the keyword
    payload `human_resolution_stands` takes (`provenance`, `resolved_answer_types`,
    `reviewer_supplied`). A field named twice, a field outside the vocabulary, or an
    unresolved address is refused rather than resolved — the caller's record is not repaired
    here.
    """
    ordered_references = tuple(references)
    ordered_readings = tuple(readings)
    facts: dict[str, dict] = {}
    for resolution_field, payload in human_resolutions:
        if not rs.is_reconciliation_field(resolution_field):
            raise ValueError(
                f"{resolution_field!r} is not a reconciliation field; a human resolution is "
                "read for a field this model describes or not at all."
            )
        if resolution_field in facts:
            raise ValueError(f"{resolution_field!r} has more than one human resolution entry.")
        facts[resolution_field] = dict(payload)

    results = []
    for field in rs.RECONCILIATION_FIELDS:
        results.append(reconcile_field(
            field,
            tuple(reference for reference in ordered_references if reference.field == field),
            ordered_readings,
            **facts.get(field, {}),
        ))
    return tuple(results)
