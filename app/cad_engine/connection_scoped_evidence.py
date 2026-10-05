"""
J71 — CONNECTION-SCOPED EVIDENCE RESOLUTION. A pure, deterministic, read-only resolver.

WHAT PROBLEM THIS SOLVES

J69 detects that two recorded pieces of evidence for one ReviewedConnectionSpecification
field disagree. Its evidence-loading step is a SEAM: it accepts readings from its caller,
because a J66 address names a PAGE READING and a page reading can carry several connection
candidates, so "the value of this field" is not determined by the address alone.

J71 fills that seam — and only that seam. Given

  * a review ITEM (one already-scoped connection at one revision),
  * a recorded evidence REFERENCE (the address a field's evidence was recorded at), and
  * the recorded page readings,

it answers ONE question: which single raw connection candidate, inside the cited page
reading, belongs to that item? It returns that candidate verbatim, or it refuses with a
named reason code. It never decides a value, never compares two values, never chooses a
winner, and never writes anything.

WHAT IT DELIBERATELY DOES NOT DO

  * It does not establish connection identity. The item already has it: the recorded
    reference is a child of the item, and the item is one connection. Nothing here
    merges, splits, renumbers or re-derives a connection.
  * It does not resolve across runs. An explicitly cited `analysis_run_id` is the only
    run consulted; another attempt's reading of the same page is never substituted for
    it, even when that attempt is the one another layer would call authoritative.
  * It does not use geometry. `annotation_x` / `annotation_y` are carried verbatim and
    are never consulted: they locate evidence on a page, and nothing in the evidence
    model establishes that a position encodes which connection a reading belongs to.
  * It does not interpret `anchor`. It is carried verbatim, exactly as J69 carries it.
  * It never reconstructs a candidate's array index from submission order, review
    numbering, page order, page adjacency, proximity or anything else. A position is read
    only where the review ITEM itself recorded one (R3, below); where the recorded evidence
    cannot single out one candidate, the answer is a refusal — never a guess.
  * It does not copy engineering values. The candidate is handed on as the recording
    holds it; the value stays in exactly one place.

WHY THIS MODULE IS STANDALONE

J66's own tree-wide guard permits exactly three modules in `app/` to name the vocabulary
J66 owns. This module is not one of them, so it names the recorded address a `reference`
throughout — the same word J69 uses for its address-plus-kind pair — and it names the
recorded page reading by the key names the storage layer already uses rather than by
importing a second vocabulary. J71 imports no application module at all: it is a pure
function over structures its caller supplies, and a test pins that.

THE SIX REFUSALS

Every way this resolver can fail to establish one candidate has a name, and the names are
closed: a refusal the model cannot express does not exist. `EVIDENCE_PAGE_ABSENT` — the
cited page was never read under any attempt. `RUN_ABSENT` — the page WAS read, but not by
the cited attempt. `CANDIDATE_ABSENT` — the cited reading states no connection candidate.
`AMBIGUOUS_PAGE` — the reading states several and the recorded evidence does not single
out exactly one of them (which is also the answer when the item's own recorded page
contradicts the cited page, since then nothing on this page can be shown to be its
evidence). `CANDIDATE_INDEX_INVALID` — the item recorded a position and it is not one.
`CANDIDATE_INDEX_OUT_OF_RANGE` — the item recorded a position and the cited reading does
not reach it. None of the six is a fallback: there is no seventh path.

R3 — THE PERSISTED CANDIDATE ADDRESS

`resolve_candidate_at_recorded_address` is the third rule, and it exists because one page
reading can hold several candidates that no recorded provenance can tell apart. It reads
the candidate's origin from the ITEM's own recorded evidence — the address J72 records when
a candidate is lifted out of its page reading:

    source_drawing_id, source_page, analysis_run_id, candidate_index

All four are read from the persisted item and from nowhere else: not from a reconstructed
workflow, not from submission order or `RP-####`, not from task order, not from the order
candidates arrive in, and not from a coordinate. Where that address is complete, R3 returns
the candidate at that exact position inside that exact attempt's reading. Where a term is
absent, malformed or out of range it refuses by name — it never completes a half-written
address, never clamps a position to one that exists, and never falls back to another
attempt. Where the item records NO position at all — every item recorded before J72, and
every item a producer could not address — R3 is not the rule that answers: the existing
R1/R2 rules run unchanged, exactly as they did before it existed.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ======================================================================================
# 1. The vocabulary this module reads and states. Every literal is pinned by a test to the
#    module that owns it, so no copy here can drift from its source unnoticed.
# ======================================================================================
# The three promoted columns that identify a recorded page reading, and the payload column
# that holds the reading itself. These are the storage layer's own `CAPTURE_COLUMNS` names,
# quoted rather than imported: that module is not this layer's dependency, and a test pins
# the four to its list.
CAPTURE_DRAWING_KEY = "drawing_id"
CAPTURE_PAGE_KEY = "page_number"
CAPTURE_RUN_KEY = "analysis_run_id"
CAPTURE_PAYLOAD_KEY = "payload"

#: The payload key holding this run's connection candidates, in the order the reading
#: states them. This is `PageExtraction.raw_connections`' own field name
#: (`app/ai_analysis/pdf_vision_analyzer.py`); a test pins the copy to that dataclass.
CANDIDATE_ARRAY_KEY = "raw_connections"

# The recorded provenance a review item carries about its own connection. `source_page` is
# `AIExtractedConnection.source_page`'s name; the other two are the candidate's own keys.
PROVENANCE_PAGE_KEY = "source_page"
PROVENANCE_DETAIL_KEY = "detail_reference"
PROVENANCE_GRID_KEY = "grid_reference"

#: The provenance keys compared under R2, in the order they are stated. The first is
#: compared against the READING's page (a candidate carries no page of its own — its page
#: is the reading's), the other two against the candidate's own values.
PROVENANCE_KEYS = (PROVENANCE_PAGE_KEY, PROVENANCE_DETAIL_KEY, PROVENANCE_GRID_KEY)

# The two further terms a review item records about where its candidate came from. Both are
# the evidence model's own names: `source_drawing_id` is the recorded drawing (the reading's
# own `drawing_id`, restated on the item by the same producer, never renamed here), and
# `candidate_index` is the position the producer recorded INTO the page. Neither enters
# `PROVENANCE_KEYS`: R2 compares those against a candidate's own values, and a candidate
# carries neither of these — adding them there would make every R2 match fail.
PROVENANCE_DRAWING_KEY = "source_drawing_id"
CANDIDATE_POSITION_KEY = "candidate_index"

#: The four terms R3 reads, all of them from the item's own recorded evidence, in the order
#: they are stated. A recorded address is these and nothing else: where one is missing, the
#: address is incomplete and is refused rather than completed from another source.
RECORDED_ORIGIN_KEYS = (
    PROVENANCE_DRAWING_KEY,
    PROVENANCE_PAGE_KEY,
    CAPTURE_RUN_KEY,
    CANDIDATE_POSITION_KEY,
)

# The three address terms a resolution requires. `drawing_id` and `page_number` locate the
# reading; `analysis_run_id` fixes the ATTEMPT, which is what makes a re-read a new reading
# rather than a collision.
REQUIRED_ADDRESS_TERMS = (CAPTURE_DRAWING_KEY, CAPTURE_PAGE_KEY, CAPTURE_RUN_KEY)

# The address a resolution carries on to its caller. These are the address terms J66
# records and J69's `EvidenceAddress` names, verbatim, so a caller can hand them on
# without translating between two vocabularies. `anchor` is last because it is the one
# that is never interpreted.
ADDRESS_TERMS = (
    CAPTURE_DRAWING_KEY, CAPTURE_PAGE_KEY, CAPTURE_RUN_KEY, "document_id",
    "annotation_x", "annotation_y", "extractor_version", "anchor",
)

# --------------------------------------------------------------------------------------
# The reason codes. Closed, named, and never a fallback.
# --------------------------------------------------------------------------------------
REASON_EVIDENCE_PAGE_ABSENT = "EVIDENCE_PAGE_ABSENT"
REASON_AMBIGUOUS_PAGE = "AMBIGUOUS_PAGE"
REASON_CANDIDATE_ABSENT = "CANDIDATE_ABSENT"
REASON_RUN_ABSENT = "RUN_ABSENT"
REASON_CANDIDATE_INDEX_INVALID = "CANDIDATE_INDEX_INVALID"
REASON_CANDIDATE_INDEX_OUT_OF_RANGE = "CANDIDATE_INDEX_OUT_OF_RANGE"

#: Exactly the six refusals this resolver can state: the four about the evidence itself, in
#: the brief's own order, then the two about a recorded position. A test pins the tuple, so
#: a seventh refusal cannot be added without it being declared here.
REASON_CODES = (
    REASON_EVIDENCE_PAGE_ABSENT,
    REASON_AMBIGUOUS_PAGE,
    REASON_CANDIDATE_ABSENT,
    REASON_RUN_ABSENT,
    REASON_CANDIDATE_INDEX_INVALID,
    REASON_CANDIDATE_INDEX_OUT_OF_RANGE,
)

#: The rule that established a resolved candidate. This is a fact about the RESOLUTION and
#: never a quality, a rank or a preference: a caller may use it to decide how much it wants
#: to rely on the answer, which is exactly why it is stated rather than hidden.
RESOLVED_SINGLE_CANDIDATE = "RESOLVED_SINGLE_CANDIDATE"
RESOLVED_PROVENANCE = "RESOLVED_PROVENANCE"
RESOLVED_CANDIDATE_INDEX = "RESOLVED_CANDIDATE_INDEX"

#: The three resolutions, pinned.
RESOLUTION_RULES = (
    RESOLVED_SINGLE_CANDIDATE,
    RESOLVED_PROVENANCE,
    RESOLVED_CANDIDATE_INDEX,
)


class _Absent:
    """A sentinel, so that an explicitly `None` term is not confused with a missing one."""


_ABSENT = _Absent()


# ======================================================================================
# 2. Reading the caller's structures. Nothing here is interpreted; every value is either
#    present and carried verbatim, or absent and refused by name.
# ======================================================================================
def _term(source: Any, name: str, default: Any = None) -> Any:
    """One named value from a mapping OR an object, or `default` when it has none.

    Both forms occur in this system and both are genuine: a recorded evidence reference is
    the review layer's own address object (an object with attributes), while a recorded page
    reading read back from the store is a plain mapping. Accepting both is what lets this
    module be called with the real objects it was designed for, without importing either.
    """
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _required_term(source: Any, name: str, *, where: str) -> Any:
    """A term a resolution cannot proceed without, refused LOUDLY when it is missing.

    This is deliberately not one of the four reason codes. A reason code is a statement
    about the EVIDENCE — the page was not read, the run is not recorded. A structure that
    is not shaped like a reading at all is a statement about the CALLER, and treating it
    as "no evidence" would make a genuinely absent page indistinguishable from a broken
    read. The storage layer refuses the same way, for the same reason.
    """
    value = _term(source, name, _ABSENT)
    if value is _ABSENT:
        raise ValueError(
            f"the {where} carries no {name!r}: every recorded reading is addressed by "
            f"({', '.join(REQUIRED_ADDRESS_TERMS)}), and a structure without one is not a "
            "reading — it is never treated as an absent one."
        )
    return value


def _refused_term(value: Any) -> bool:
    """True when a term states nothing, so it can neither match nor gate anything.

    `None` and a blank string state nothing; every other value (including a number, and
    including a string that merely looks odd) is a stated value and is carried on.
    """
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


# ======================================================================================
# 3. The read model. Two immutable outcomes, and nothing that could be mistaken for a
#    judgement: no confidence, no rank, no authority, no winner, no role priority.
# ======================================================================================
@dataclass(frozen=True)
class Unresolved:
    """
    This evidence does not establish ONE candidate for this item — and why, by name.

    `reason_code` is one of `REASON_CODES` and is refused at construction when it is not,
    so an outcome outside the closed vocabulary cannot be built even by accident.
    `detail` is a deterministic sentence built from the address the caller supplied; it
    carries no evidence value and states no conclusion beyond the code.
    """

    reason_code: str
    detail: str

    def __post_init__(self) -> None:
        if self.reason_code not in REASON_CODES:
            raise ValueError(
                f"{self.reason_code!r} is not a resolution refusal "
                f"(codes: {list(REASON_CODES)}); this model states a closed set, so a code "
                "outside it is a caller's error and never a new outcome."
            )


@dataclass(frozen=True)
class ConnectionScopedReading:
    """
    ONE raw connection candidate, resolved to the connection item that cited it.

    The candidate is held VERBATIM, equal to the recording but never the caller's own
    object, so a reader cannot mutate a returned reading back into the recorded evidence.
    No part of it is copied out, renamed, canonicalised or given a comparable form: turning
    a raw reading into a comparable value is J69's layer B, and it is deliberately not
    this layer's business.

    The address terms are the reference's own, carried unchanged, so a caller can build
    J69's `EvidenceAddress` from them without translating. `anchor` is carried VERBATIM and
    is never interpreted. `annotation_x` / `annotation_y` are carried for the same reason
    and are NEVER consulted: this module takes no view on whether a position means anything.

    `candidate_position` is the position of the candidate in THIS reading's array. It is an
    observation about where the candidate was found, and nothing more: it is not an
    identity, it is not compared with anything, and it is NOT comparable across readings —
    a later reading of the same page may state the same candidate at a different position.
    R3 reads a position only where the review ITEM itself recorded one, and returns the
    candidate that position holds in that reading; it never carries this observation back
    out as though it were one.

    `matched_by` names the rule that established the candidate — `RESOLVED_SINGLE_CANDIDATE`
    when the reading stated exactly one, `RESOLVED_PROVENANCE` when the recorded provenance
    singled out one of several, `RESOLVED_CANDIDATE_INDEX` when the item's own recorded
    address named the position (R3). It is a fact about the resolution, never a score.
    """

    field_name: Any
    review_package_id: Any
    drawing_id: Any
    page_number: Any
    analysis_run_id: Any
    document_id: Any
    annotation_x: Any
    annotation_y: Any
    extractor_version: Any
    anchor: Any
    candidate_position: int
    matched_by: str
    candidate: Mapping

    def __post_init__(self) -> None:
        if self.matched_by not in RESOLUTION_RULES:
            raise ValueError(
                f"{self.matched_by!r} is not a resolution rule "
                f"(rules: {list(RESOLUTION_RULES)}); the two rules are the whole of this "
                "model's resolution behaviour."
            )
        # The candidate is snapshotted at construction: a later mutation of the caller's
        # payload cannot change what a resolved reading says it found.
        object.__setattr__(self, "candidate", copy.deepcopy(self.candidate))


# ======================================================================================
# 4. The address, and the page reading it names.
# ======================================================================================
def _address_of(reference: Any) -> tuple[Any, Any, Any] | Unresolved:
    """The three required address terms, or the refusal that says which one is missing.

    `drawing_id` and `page_number` are checked first and together: without a page there is
    nothing to look for, and `EVIDENCE_PAGE_ABSENT` states exactly that. `analysis_run_id`
    is checked second, because a page that WAS read under another attempt is a different
    fact from a page that was never read.
    """
    drawing_id = _term(reference, CAPTURE_DRAWING_KEY)
    page_number = _term(reference, CAPTURE_PAGE_KEY)
    run_id = _term(reference, CAPTURE_RUN_KEY)
    if _refused_term(drawing_id) or _refused_term(page_number):
        return _unresolved(
            REASON_EVIDENCE_PAGE_ABSENT,
            f"the recorded address states no drawing and page to resolve "
            f"(drawing_id={drawing_id!r}, page_number={page_number!r})",
        )
    if _refused_term(run_id):
        return _unresolved(
            REASON_RUN_ABSENT,
            f"the recorded address names drawing {drawing_id!r} page {page_number!r} but "
            "no attempt to read it; the attempt is what makes a re-read a new reading, so "
            "no other attempt is consulted in its place",
        )
    return drawing_id, page_number, run_id


def _readings_for(captures: Any, drawing_id: Any, page_number: Any) -> list[Any]:
    """Every recorded reading OF this page, under any attempt — the page's whole history.

    Returning the whole history rather than the resolve set is what lets the two distinct
    refusals be told apart: "this page was never read" and "this page was read, but not by
    the attempt that was cited" are different facts, and only the first is a missing page.
    Nothing is resolved from this list except the one entry whose attempt was cited.
    """
    if captures is None:
        raise ValueError(
            "no recorded readings were supplied; an empty set of readings is stated as an "
            "empty sequence, so that an absent argument is never read as an absent page."
        )
    found = []
    for row in captures:
        row_drawing = _required_term(row, CAPTURE_DRAWING_KEY, where="recorded reading")
        row_page = _required_term(row, CAPTURE_PAGE_KEY, where="recorded reading")
        _required_term(row, CAPTURE_RUN_KEY, where="recorded reading")
        if _same_value(row_drawing, drawing_id) and _same_value(row_page, page_number):
            found.append(row)
    return found


def _same_value(left: Any, right: Any) -> bool:
    """Exact equality, and nothing else.

    No coercion, no case folding, no whitespace collapsing, no numeric parsing: a term
    that is not literally the other term is a different term. This is what keeps every
    comparison in this module honest — a rule that "helpfully" made `22` and `"22"` the
    same would be deciding engineering meaning, which is not this layer's to decide.
    """
    return left == right


def _cited_reading(captures: Any, drawing_id: Any, page_number: Any, run_id: Any
                   ) -> Any | Unresolved:
    """The ONE reading of this page under the cited attempt, or the refusal that says why.

    Every rule that resolves anything from a page goes through here, so all of them tell
    "this page was never read" and "this page was read, but not by the cited attempt" apart
    in exactly the same words, and none of them can quietly look at another attempt.
    """
    page_readings = _readings_for(captures, drawing_id, page_number)
    if not page_readings:
        return _unresolved(
            REASON_EVIDENCE_PAGE_ABSENT,
            f"no recorded reading exists for drawing {drawing_id!r} page {page_number!r}; "
            "an unread page states no candidate, so nothing on another page is consulted "
            "in its place",
        )

    cited = [row for row in page_readings
             if _same_value(_required_term(row, CAPTURE_RUN_KEY, where="recorded reading"),
                            run_id)]
    if not cited:
        return _unresolved(
            REASON_RUN_ABSENT,
            f"drawing {drawing_id!r} page {page_number!r} was read, but not by attempt "
            f"{run_id!r}; the cited attempt is the only one consulted and is never "
            "replaced by another",
        )
    if len(cited) > 1:
        raise ValueError(
            f"{len(cited)} recorded readings for drawing {drawing_id!r} page "
            f"{page_number!r} attempt {run_id!r}; one attempt reads a page once, so this "
            "is a duplicated recording and never an ambiguity to resolve."
        )
    return cited[0]


# ======================================================================================
# 5. The candidates, and the three rules that select one.
# ======================================================================================
def _candidates_of(reading: Any) -> Sequence[Any]:
    """The connection candidates one recorded reading states, in the order it states them."""
    payload = _required_term(reading, CAPTURE_PAYLOAD_KEY, where="recorded reading")
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"a recorded reading's {CAPTURE_PAYLOAD_KEY!r} must be a mapping "
            f"(got {type(payload).__name__}); a reading that is not a mapping cannot be "
            "searched for a candidate."
        )
    stated = payload.get(CANDIDATE_ARRAY_KEY)
    if stated is None:
        return ()
    if isinstance(stated, (str, bytes)) or not isinstance(stated, Sequence):
        raise ValueError(
            f"a recorded reading's {CANDIDATE_ARRAY_KEY!r} must be a sequence of "
            f"candidate objects (got {type(stated).__name__})."
        )
    for candidate in stated:
        if not isinstance(candidate, Mapping):
            raise ValueError(
                f"a candidate must be an object (got {type(candidate).__name__}); a "
                "candidate that is not one cannot carry the provenance this rule reads."
            )
    return stated


def _recorded_provenance(item: Any) -> dict[str, Any]:
    """The provenance a review item records about its own connection, restricted to the
    keys that state something.

    A key that states nothing is DROPPED rather than compared: it cannot discriminate
    between candidates, and letting it match would let an item that records nothing look
    like an item that records a value. Nothing is normalised on the way in.
    """
    evidence = _term(item, "evidence")
    stated: dict[str, Any] = {}
    for name in PROVENANCE_KEYS:
        value = _term(evidence, name)
        if not _refused_term(value):
            stated[name] = value
    return stated


def _candidate_matches(candidate: Mapping, page_number: Any, wanted: Mapping) -> bool:
    """Whether one candidate's own provenance states every value the item recorded.

    The page is compared against the READING's page, because a candidate carries no page
    of its own — its page is the reading's. Both sides must state the same value. A key
    missing from `wanted` is not checked at all, and a key present in `wanted` that the
    candidate does not state is a NON-match: NULL never matches a value, and NULL never
    matches NULL.
    """
    for name, item_value in wanted.items():
        candidate_value = page_number if name == PROVENANCE_PAGE_KEY else _term(candidate, name)
        if _refused_term(candidate_value):
            return False
        if not _same_value(item_value, candidate_value):
            return False
    return True


def _unresolved(code: str, detail: str) -> Unresolved:
    return Unresolved(reason_code=code, detail=detail)


def _resolved(reference: Any, item: Any, position: int, candidate: Mapping,
              matched_by: str) -> ConnectionScopedReading:
    """The one construction of a resolved reading. Address terms come from the reference,
    so a resolution can only ever carry the address it was resolved AT — the reading it was
    found in is that address, and is never restated from a second source."""
    return ConnectionScopedReading(
        field_name=_term(reference, "field_name"),
        review_package_id=_term(reference, "review_package_id",
                                _term(item, "review_package_id")),
        drawing_id=_term(reference, CAPTURE_DRAWING_KEY),
        page_number=_term(reference, CAPTURE_PAGE_KEY),
        analysis_run_id=_term(reference, CAPTURE_RUN_KEY),
        document_id=_term(reference, "document_id"),
        annotation_x=_term(reference, "annotation_x"),
        annotation_y=_term(reference, "annotation_y"),
        extractor_version=_term(reference, "extractor_version"),
        anchor=_term(reference, "anchor", ()),
        candidate_position=position,
        matched_by=matched_by,
        candidate=candidate,
    )


# ======================================================================================
# 6. The resolver.
# ======================================================================================
def resolve_connection_field_reading(item: Any, reference: Any, captures: Any
                                     ) -> ConnectionScopedReading | Unresolved:
    """
    Which single recorded connection candidate on a page is the evidence for one field of
    one already-scoped review item.

    `item` is the review item — the connection, already scoped by its own identity. Only
    its recorded `evidence` is read, and only for the provenance keys in `PROVENANCE_KEYS`.
    `reference` is the recorded evidence address: the three terms in
    `REQUIRED_ADDRESS_TERMS` are required, the terms in `ADDRESS_TERMS` are carried
    verbatim, and `field_name` / `review_package_id` are carried when present so a caller
    knows what the reading is evidence FOR. `captures` is the recorded page readings — a
    sequence of mappings in the storage layer's own column shape.

    A mapping or an object is accepted for the first two, because both forms genuinely
    occur in this system. A mapping is required for a recording, because a recording read
    back from the store is a mapping.

    Returns the one candidate, or an `Unresolved` naming why none was established. The
    three rules run in strict order and there is no fourth path:

      R0  the address must state a drawing, a page and an attempt;
      R1  the cited attempt's READING of that page must state exactly one candidate;
      R2  where it states several, the item's own recorded provenance must single out
          exactly one of them, by exact equality;
      otherwise, a refusal.

    These rules read the address the CALLER supplies. An item that recorded where its own
    candidate came from is answered by `resolve_candidate_at_recorded_address` instead —
    a separate entry point, not a path through this one.

    It is idempotent, it takes no database, storage or network dependency, it calls no
    model, and it mutates none of its arguments.
    """
    address = _address_of(reference)
    if isinstance(address, Unresolved):
        return address
    drawing_id, page_number, run_id = address

    reading = _cited_reading(captures, drawing_id, page_number, run_id)
    if isinstance(reading, Unresolved):
        return reading

    candidates = _candidates_of(reading)
    if not candidates:
        return _unresolved(
            REASON_CANDIDATE_ABSENT,
            f"the reading of drawing {drawing_id!r} page {page_number!r} under attempt "
            f"{run_id!r} states no connection candidate",
        )
    if len(candidates) == 1:
        return _resolved(reference, item, 0, candidates[0], RESOLVED_SINGLE_CANDIDATE)

    wanted = _recorded_provenance(item)
    matched = [position for position, candidate in enumerate(candidates)
               if _candidate_matches(candidate, page_number, wanted)]
    if len(matched) == 1:
        return _resolved(reference, item, matched[0], candidates[matched[0]],
                         RESOLVED_PROVENANCE)

    if not wanted:
        stated = (f"the item records none of {list(PROVENANCE_KEYS)}")
    else:
        stated = (f"the item records {sorted(wanted)}, which "
                  f"{'matches no candidate' if not matched else 'matches several'}")
    return _unresolved(
        REASON_AMBIGUOUS_PAGE,
        f"the reading of drawing {drawing_id!r} page {page_number!r} under attempt "
        f"{run_id!r} states {len(candidates)} candidates and {stated}; exactly one is "
        "required, and none is guessed",
    )


# ======================================================================================
# 7. R3 — the address the ITEM itself recorded. A second entry point, never a fallback of
#    the first: where an item records no position it is the rules above that answer, and
#    where it records one this function answers and the rules above are not consulted.
# ======================================================================================
def _is_recorded_position(value: Any) -> bool:
    """Whether a recorded position is one: a whole number counted from zero.

    Nothing else is one. A boolean is not one even though Python counts it as an integer,
    a fractional number is not one, a negative is not one, and a string is not one however
    much it looks like a number — each of those is refused by name rather than converted.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _reference_from_recorded_evidence(evidence: Any) -> dict[str, Any]:
    """The address a review item records, restated in the shape an address is read in.

    A RENAME and nothing else: the two terms the evidence model names `source_drawing_id`
    and `source_page` become the two the reading model names for the very same values. A
    term the item does not state stays absent, so it is refused by name rather than filled
    in from somewhere else. `review_package_id` is deliberately not restated here, so a
    resolved reading still takes it from the item itself.
    """
    return {
        CAPTURE_DRAWING_KEY: _term(evidence, PROVENANCE_DRAWING_KEY),
        CAPTURE_PAGE_KEY: _term(evidence, PROVENANCE_PAGE_KEY),
        CAPTURE_RUN_KEY: _term(evidence, CAPTURE_RUN_KEY),
    }


def resolve_candidate_at_recorded_address(item: Any, captures: Any
                                          ) -> ConnectionScopedReading | Unresolved:
    """
    Which single recorded connection candidate one review item's OWN recorded address names.

    `item` is the review item; the address is read from its recorded `evidence` and from
    nowhere else — the terms in `RECORDED_ORIGIN_KEYS`, the last two of which a producer
    records only where it holds the page reading's own candidate array. `captures` is the
    recorded page readings, in the storage layer's own column shape.

    This answers a question the rules above cannot: one page reading may state several
    candidates that no recorded provenance tells apart, and where the item recorded WHICH
    one, that recorded position — not a resemblance, not an order, not a coordinate — is
    what settles it. The candidate is returned verbatim, by the same immutable reading,
    under `RESOLVED_CANDIDATE_INDEX`.

    R3 runs in this order, and there is no seventh path:

      R3-1  the item records no position: R3 does not answer at all. The rules above run
            unchanged, so every item recorded before positions were recorded is resolved
            exactly as it was before this function existed — never refused for the absence
            of a term it was never able to state;
      R3-2  the item records a position and it is not a whole number counted from zero:
            `CANDIDATE_INDEX_INVALID`. It is never converted into one;
      R3-3  the item records a position but states no attempt: `RUN_ABSENT`. Another
            attempt is never consulted in its place, not even an authoritative-looking one;
      R3-4  the page reading the address names must exist, under that exact attempt:
            otherwise `EVIDENCE_PAGE_ABSENT` or `RUN_ABSENT`, exactly as above;
      R3-5  the position must be one the reading reaches: otherwise
            `CANDIDATE_INDEX_OUT_OF_RANGE`. It is never clamped, wrapped, or moved onto
            the last, the first or the nearest candidate;
      R3-6  the candidate the recorded position holds is returned, carrying the address the
            item recorded — never one restated from whatever the reading is today.

    It is idempotent, it takes no database, storage or network dependency, it calls no
    model, it decides no engineering value, and it mutates none of its arguments.
    """
    evidence = _term(item, "evidence")
    recorded = _term(evidence, CANDIDATE_POSITION_KEY, _ABSENT)
    if recorded is _ABSENT:
        return resolve_connection_field_reading(
            item, _reference_from_recorded_evidence(evidence), captures
        )
    if not _is_recorded_position(recorded):
        return _unresolved(
            REASON_CANDIDATE_INDEX_INVALID,
            f"the item's recorded address states a candidate position of {recorded!r} "
            f"({type(recorded).__name__}); a position is a whole number counted from zero, "
            "and no other value is converted into one",
        )

    reference = _reference_from_recorded_evidence(evidence)
    recorded_run = _term(reference, CAPTURE_RUN_KEY)
    if _refused_term(recorded_run):
        return _unresolved(
            REASON_RUN_ABSENT,
            f"the item records candidate position {recorded} but states no attempt to "
            "read the page it came from; a position is only a position inside one "
            "attempt's reading, so no other attempt is consulted in its place",
        )

    drawing_id = _term(reference, CAPTURE_DRAWING_KEY)
    page_number = _term(reference, CAPTURE_PAGE_KEY)
    reading = _cited_reading(captures, drawing_id, page_number, recorded_run)
    if isinstance(reading, Unresolved):
        return reading

    candidates = _candidates_of(reading)
    if not 0 <= recorded < len(candidates):
        return _unresolved(
            REASON_CANDIDATE_INDEX_OUT_OF_RANGE,
            f"the item's recorded address names position {recorded} on drawing "
            f"{drawing_id!r} page {page_number!r} under attempt {recorded_run!r}, and that "
            f"reading states {len(candidates)} candidate(s); a position the reading does "
            "not reach is never clamped, wrapped or moved onto another candidate",
        )
    return _resolved(reference, item, recorded, candidates[recorded],
                     RESOLVED_CANDIDATE_INDEX)
