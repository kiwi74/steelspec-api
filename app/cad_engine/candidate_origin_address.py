"""
J72 — THE CANDIDATE ORIGIN ADDRESS.

A review item's `evidence` payload records WHERE its connection candidate was read from.
J72 adds the last two terms of that address:

    analysis_run_id    the analysis run whose page reading produced the candidate
    candidate_index    the candidate's ZERO-BASED position in THAT page's own
                       `raw_connections` array, as recorded in `page_extraction_captures`

Together with the `drawing_id`/`page_number` the reading already carries, that is the
address J71's deferred R3 rule needs to name one candidate without guessing:

    drawing_id + page_number + analysis_run_id + candidate_index

WHAT `candidate_index` IS NOT. It is the array position and nothing else. It is not a
connection id, not an engineering identity, not durable across re-extraction, not a
review-package identity, not a revision, not an authority signal, not a confidence, not
a ranking, not a winner, and not a cross-document identity. Two candidates at the same
index in two different runs are two different candidates; the run is what makes the
position an address rather than a collision. In particular it is NOT `submission_index`
and NOT the number in `RP-####`: those are positions in a FLATTENED, multi-page list
built from submission order, and the two numbers diverge the moment a second page
contributes a candidate. Nothing here ever converts one into the other.

THE ONE RULE THAT MAKES IT SAFE. The index is recorded only where the producer still
holds the page the candidate was read from — the position in that page's own array. A
producer that cannot prove it records NOTHING (`origins_for_page` returns None for every
candidate rather than a plausible number), and the payload is left exactly as it was:
the two keys stay ABSENT rather than being written as null, because a null would state
that an origin was looked for and found to be nothing, which is a different claim. A
historical item is therefore untouched and stays loadable — this module has no backfill
and no repair path, and reading a payload that predates J72 returns None.

VALIDATION IS STRICT AND REFUSES RATHER THAN COERCING. `candidate_index` must be a real
`int` that is not a `bool` and is not negative: `True`, `1.0` and `"1"` are each refused,
never quietly converted. A run id must be a non-empty string. An index outside the array
it names is refused rather than clamped, and a payload stating only one of the two terms
is refused rather than completed.

This module is pure: it imports nothing from the application, reads no clock, no
database, no storage and no file, and writes nothing anywhere.
"""
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CANDIDATE_ARRAY_KEY",
    "CANDIDATE_ORIGIN_REFUSALS",
    "ORIGIN_INDEX_INVALID",
    "ORIGIN_INDEX_OUT_OF_RANGE",
    "ORIGIN_KEYS",
    "ORIGIN_RUN_ABSENT",
    "CANDIDATE_ORIGIN_RUN_KEY",
    "CANDIDATE_ORIGIN_INDEX_KEY",
    "CandidateOrigin",
    "CandidateOriginRefused",
    "is_recorded_index",
    "is_recorded_run",
    "origin_at_page_index",
    "origin_of",
    "origins_for_page",
    "with_origin",
]

# The two terms, under the names the reading path records them by. They are NOT injected
# into the flattened candidate payload: the reading path injects only `page_num` there, and
# carries the origin on the candidate object beside it. They reach the recorded evidence on
# the review item, alongside `source_drawing_id` and `source_page`.
CANDIDATE_ORIGIN_RUN_KEY = "analysis_run_id"
CANDIDATE_ORIGIN_INDEX_KEY = "candidate_index"
ORIGIN_KEYS: tuple[str, ...] = (CANDIDATE_ORIGIN_RUN_KEY, CANDIDATE_ORIGIN_INDEX_KEY)

# The array a `candidate_index` is an index INTO — the page reading's own recorded
# array. Named here rather than imported: the storage layer is not this layer's
# dependency.
CANDIDATE_ARRAY_KEY = "raw_connections"

# Why an origin was refused. Three codes, no fourth, and no code means "guessed".
#
# There is deliberately no code for "half an address". A payload naming only the index
# fails as ORIGIN_RUN_ABSENT and one naming only the run fails as ORIGIN_INDEX_INVALID,
# so each refusal names the term that is actually missing; a separate "incomplete" code
# would be a fourth word for the same two failures and would tell a reader less.
ORIGIN_RUN_ABSENT = "ORIGIN_RUN_ABSENT"
ORIGIN_INDEX_INVALID = "ORIGIN_INDEX_INVALID"
ORIGIN_INDEX_OUT_OF_RANGE = "ORIGIN_INDEX_OUT_OF_RANGE"
CANDIDATE_ORIGIN_REFUSALS: tuple[str, ...] = (
    ORIGIN_RUN_ABSENT,
    ORIGIN_INDEX_INVALID,
    ORIGIN_INDEX_OUT_OF_RANGE,
)


class CandidateOriginRefused(ValueError):
    """An origin that could not be stated truthfully. Carries the code that says which."""

    def __init__(self, code: str, statement: str):
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


def is_recorded_run(value: Any) -> bool:
    """Whether `value` states an analysis run. A blank string states nothing.

    Deliberately narrow: anything that is not a string is not a run id, so `7`, `None`
    and a list never pass as one.
    """
    return isinstance(value, str) and bool(value.strip())


def is_recorded_index(value: Any) -> bool:
    """Whether `value` states an array position.

    `bool` is excluded on purpose even though it is an `int` subclass: `True` is a flag,
    not position 1, and accepting it would let a boolean wear an address. Floats and
    numeric strings are excluded by the same test — `1.0` and `"1"` are refused, never
    converted, because a coercion here would silently invent an address that was never
    stated.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_candidate_sequence(value: Any) -> bool:
    """Whether `value` is a candidate array rather than a string that happens to behave like one."""
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


@dataclass(frozen=True)
class CandidateOrigin:
    """
    WHERE one candidate was read from: the run that read the page, and the candidate's
    position in that page's own array.

    Constructing one is the validation. There is no `from_string`, no default and no
    `None`-tolerant constructor, so an origin that exists at all is an origin that was
    stated in full.

    The pair is carried VERBATIM and is never compared here: this layer does not decide
    whether two origins are the same candidate, and it holds no opinion about whether a
    candidate still stands.
    """

    analysis_run_id: str
    candidate_index: int

    def __post_init__(self) -> None:
        if not is_recorded_run(self.analysis_run_id):
            raise CandidateOriginRefused(
                ORIGIN_RUN_ABSENT,
                f"a candidate origin needs the analysis run that read the page; "
                f"{self.analysis_run_id!r} is not a run id, and a run is never assumed "
                "from the reading that happens to stand for the page today.")
        if not is_recorded_index(self.candidate_index):
            raise CandidateOriginRefused(
                ORIGIN_INDEX_INVALID,
                f"a candidate index is a zero-based position in one page's own array, "
                f"as an integer; {self.candidate_index!r} "
                f"({type(self.candidate_index).__name__}) is not one, and it is never "
                "coerced — `True`, `1.0` and \"1\" each state something other than a "
                "position.")

    def to_evidence(self) -> dict[str, Any]:
        """The two terms as a payload fragment, ready to be carried beside the reading."""
        return {
            CANDIDATE_ORIGIN_RUN_KEY: self.analysis_run_id,
            CANDIDATE_ORIGIN_INDEX_KEY: self.candidate_index,
        }


def origin_at_page_index(
    candidates: Sequence[Any], index: int, *, analysis_run_id: Any
) -> CandidateOrigin:
    """
    The origin of ONE candidate of ONE page, given the page's own array and the run that
    read it.

    The array is the page's `raw_connections`, at the moment the review item is created —
    never the flattened multi-page list, and never a position recovered afterwards. An
    index outside that array is refused rather than clamped, because clamping would
    invent an address for a candidate that is not there.
    """
    if not _is_candidate_sequence(candidates):
        raise CandidateOriginRefused(
            ORIGIN_INDEX_OUT_OF_RANGE,
            f"a candidate index is a position in one page's own {CANDIDATE_ARRAY_KEY} "
            f"array; {type(candidates).__name__} is not that array.")
    if not is_recorded_index(index):
        raise CandidateOriginRefused(
            ORIGIN_INDEX_INVALID,
            f"{index!r} ({type(index).__name__}) is not a zero-based position.")
    if index >= len(candidates):
        raise CandidateOriginRefused(
            ORIGIN_INDEX_OUT_OF_RANGE,
            f"position {index} is outside a {CANDIDATE_ARRAY_KEY} array of "
            f"{len(candidates)} entries; an out-of-range position names no candidate and "
            "is never clamped to one that exists.")
    return CandidateOrigin(analysis_run_id, index)


def origins_for_page(
    candidates: Sequence[Any], *, analysis_run_id: Any
) -> tuple[CandidateOrigin | None, ...]:
    """
    One origin per candidate of ONE page, in the page's own array order — or None for
    every candidate when the run that read the page is not stated.

    This is the fail-closed seam the producer goes through. A caller that does not hold
    the run records no origin at all rather than substituting the run that stands for the
    page today (J23 selects a standing reading; that is a different question from who
    read this candidate). Nothing is inferred from the candidates' order in a flattened
    list, from the pages' order, or from `RP-####`.
    """
    if not _is_candidate_sequence(candidates):
        raise CandidateOriginRefused(
            ORIGIN_INDEX_OUT_OF_RANGE,
            f"a page's candidates are its own {CANDIDATE_ARRAY_KEY} array; "
            f"{type(candidates).__name__} is not one.")
    if not is_recorded_run(analysis_run_id):
        return (None,) * len(candidates)
    return tuple(
        origin_at_page_index(candidates, index, analysis_run_id=analysis_run_id)
        for index in range(len(candidates))
    )


def origin_of(payload: Mapping[str, Any]) -> CandidateOrigin | None:
    """
    The origin a recorded payload states, or None when it states none.

    Backward compatibility is the point: a payload written before J72 — or one the
    producer could not address — states nothing and reads as None rather than as an
    error, so no historical item is made invalid by this milestone.

    A payload that states ONE of the two terms is refused rather than completed: half an
    address is a producer that lost the other half, and the missing half is not
    recoverable from the half that survived.
    """
    run = payload.get(CANDIDATE_ORIGIN_RUN_KEY)
    index = payload.get(CANDIDATE_ORIGIN_INDEX_KEY)
    if not is_recorded_run(run) and not is_recorded_index(index):
        return None
    return CandidateOrigin(run, index)


def with_origin(payload: Mapping[str, Any], origin: CandidateOrigin | None) -> dict[str, Any]:
    """
    A copy of `payload` carrying the origin — or, when there is none, the payload it
    always had.

    Nothing else in the payload is touched, reordered or repaired. An unproven origin
    leaves the two keys ABSENT rather than writing them as null, so "the producer could
    not address this candidate" and "the producer addressed it and there was nothing
    there" stay distinguishable, and a payload that never had the keys keeps not having
    them.
    """
    carried = copy.deepcopy(dict(payload))
    if origin is None:
        for key in ORIGIN_KEYS:
            carried.pop(key, None)
        return carried
    carried.update(origin.to_evidence())
    return carried
