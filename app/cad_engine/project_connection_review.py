"""
Milestone 7X — a project-level review QUEUE over the 7W connection review
packages. It orchestrates review; it interprets nothing and generates no CAD:

    AI-extracted connection candidates (one raw AI object each)
            |
    create_project_connection_collection()     --> ProjectConnectionReviewCollection
            |                                       (one 7W ConnectionReviewPackage per candidate)
    update_candidate_supplement()               <-- the reviewer's decisions, one candidate at a time
            |
    build_project_review_summary()              --> ProjectConnectionReviewSummary
            |                                       (7W's build_review_report() per candidate, derived
            |                                        states, possible duplicates, counts)
    ready_reviewed_connections()                --> the subset genuinely ready for 7V
            |
    7V -> existing 7O assembly -> 7R -> 7S/7T drawing        (all unchanged, all outside this module)

It can say: "these are the connections the AI found, this is what is known
about each, this is what is missing, this needs human review, and this is
ready for human completion." It cannot say — and never claims — that the AI
found every connection, that the project is connection-complete, or that the
drawing set has been interpreted (SUMMARY_SCOPE_STATEMENT).

REUSE, NOT DUPLICATION: each candidate holds a 7W ConnectionReviewPackage
(extraction + reviewer supplement + known_member_marks) — the one and only
representation of a reviewed connection. Every fact about a candidate comes
from 7W's build_review_report() (project-member validation, historical AI
errors, provenance, readiness via 7V's own check); nothing is re-derived
here. 7D/7J/7N/7P/7Q/7R/7S/7T are neither called nor re-implemented.

IDENTITY (Deliverable 1) — two different things, never conflated:
  - SOURCE_IDENTITY (`source_identity`): an identifier that genuinely exists
    outside this module (e.g. a persisted connection id) and was SUPPLIED by
    the caller. The AI schema has none. It is None when not supplied and is
    never invented, defaulted or derived.
  - REVIEW_PACKAGE_ID (`review_package_id`): a label this module assigns so a
    reviewer can address one candidate ("RP-0003"). It is derived only from
    SUBMISSION ORDER — the position at which the candidate was handed in —
    and is scoped to this collection. It is NOT from the drawing, does not
    identify an engineering connection, and does not depend on presentation
    order (sorting never changes an id). No existing project convention
    identifies a connection before persistence, so this is a deliberate new,
    clearly labelled, collection-scoped addressing key (see report note).

ORDERING (Deliverable 2): ordered_candidates() sorts by real source facts —
source page, detail reference, grid reference — with missing values last and
submission order as the final tie-break. It is presentation order ONLY: it
never feeds any engineering value. START/END, attachments, hole geometry and
project-space location come solely from the reviewer's supplement, exactly as
in 7W; there is no code path from position-in-a-list to any of them.

DUPLICATES (Deliverable 3): the same detail can legitimately appear on
several pages (repeated sheets, a typical detail applied at many places, or
the AI detecting the same detail twice) and no authoritative identity exists
to tell those cases apart. So nothing is ever merged, dropped or altered.
Candidates are only REPORTED as possible duplicates, pair by pair, with the
weak signals that matched (exact match only; blank values never match):
SAME_DETAIL_REFERENCE, SAME_GRID_AND_MEMBERS, IDENTICAL_AI_EXTRACTION_CONTENT.
IDENTICAL_AI_EXTRACTION_CONTENT is strict: it fires only when the ENTIRE
preserved AI extraction (AIExtractedConnection — every field, compared
generically via dataclasses.fields, so nothing is hand-picked or can go stale)
is identical: source information, references, members in the order extracted,
type, bolts, plates, welds, AI confidence, malformed and unrecognised fields.
Values are compared exactly as reported (90 differs from 90.0). Only the
extraction is compared, never a reviewer's supplement, so human corrections
cannot alter this historical comparison. Because source information is part of
the extraction, the same reading found on two DIFFERENT pages is not
"identical" — such repeats are still caught by the two weaker signals when a
detail reference, or a grid reference and members, are present.
A warning changes no extraction, supplement, report, state or specification.
One project-level fact IS enforced: two candidates the reviewer has given the
same connection_id cannot both proceed (DUPLICATE_CONNECTION_ID -> BLOCKED),
because a connection_id must identify one connection downstream.

STATE MODEL (Deliverable 5) — derived from 7W's report, never asserted:
  READY_FOR_PIPELINE  7W says ready (7V's own check, project-member gate
                      included) and no project-level issue remains. This is
                      "ready for 7V" — NOT engineering approval.
  BLOCKED             not ready, and at least one CURRENT issue exists:
                      unresolved project-member marks, an ambiguous or
                      incomplete AI plate, a malformed AI field, an
                      unconfirmable or conflicting reviewer decision, or a
                      duplicate connection_id. Historical AI errors that a
                      reviewer already corrected (AI_UNKNOWN_MEMBER_CORRECTED)
                      never block. Plainly missing fields are NOT blocking —
                      they are the expected gaps every AI candidate has.
  REVIEW_REQUIRED     the reviewer has decided something, nothing anomalous
                      remains, but 7V would still reject (fields still missing).
  AI_EXTRACTED        no reviewer decision yet, nothing anomalous.
The first three names are 7W's own constants, reused; only BLOCKED is new.
There is no "approved" state and no state meaning engineering approval.

HUMAN CORRECTIONS (Deliverable 7/10): update_candidate_supplement() returns a
NEW collection in which only the addressed candidate's supplement differs;
every other candidate is the very same object. The AI extraction is never
touched, so the original AI values and (via 7W) historical unknown marks stay
exactly as extracted; a correction is evidence added beside them.

SCOPE: no CAD is generated, imported or called here (a test inspects this
module's imports and forbids the assembly/drawing entry points at runtime).
"""
import copy
import dataclasses
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from app.cad_engine.candidate_origin_address import CandidateOrigin
from app.cad_engine.connection_review_package import (
    REVIEW_STATE_AI_EXTRACTED,
    REVIEW_STATE_READY_FOR_PIPELINE,
    REVIEW_STATE_REVIEW_REQUIRED,
    AIExtractedConnection,
    ConnectionReviewPackage,
    ConnectionReviewReport,
    ConnectionReviewSupplement,
    ReviewIssue,
    ai_connection_to_extraction,
    build_review_report,
    build_reviewed_connection_specification,
    create_review_package,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    ReviewedConnectionCompletenessResult,
    ReviewedConnectionSpecification,
)

__all__ = [
    "STATE_BLOCKED", "PROJECT_REVIEW_STATES", "SUMMARY_SCOPE_STATEMENT",
    "SIGNAL_SAME_DETAIL_REFERENCE", "SIGNAL_SAME_GRID_AND_MEMBERS", "SIGNAL_IDENTICAL_AI_EXTRACTION_CONTENT",
    "ConnectionCandidate", "ProjectConnectionReviewCollection", "PossibleDuplicatePair",
    "CandidateReview", "ProjectConnectionReviewSummary", "ReadyReviewedConnection",
    "create_project_connection_collection", "update_candidate_supplement", "with_known_member_marks",
    "ordered_candidates", "find_possible_duplicates", "build_project_review_summary",
    "ready_reviewed_connections",
]

STATE_BLOCKED = "BLOCKED"
PROJECT_REVIEW_STATES = (
    REVIEW_STATE_AI_EXTRACTED, REVIEW_STATE_REVIEW_REQUIRED, REVIEW_STATE_READY_FOR_PIPELINE, STATE_BLOCKED,
)

SIGNAL_SAME_DETAIL_REFERENCE = "SAME_DETAIL_REFERENCE"
SIGNAL_SAME_GRID_AND_MEMBERS = "SAME_GRID_AND_MEMBERS"
SIGNAL_IDENTICAL_AI_EXTRACTION_CONTENT = "IDENTICAL_AI_EXTRACTION_CONTENT"

SUMMARY_SCOPE_STATEMENT = (
    "This summary describes only the connection candidates that were supplied from AI extraction. It does not "
    "claim that the AI found every connection in the drawing set, that the project is connection-complete, or "
    "that the drawing set has been fully interpreted. 'Ready for 7V' means only that a candidate's reviewed "
    "specification passed the 7W/7V completeness boundary; it is not engineering approval, structural adequacy, "
    "code compliance or fabrication readiness."
)

# 7W issue codes that record HISTORY (a reviewer already corrected the AI) rather than a current problem.
_HISTORICAL_ISSUE_CODES = frozenset({"AI_UNKNOWN_MEMBER_CORRECTED"})


# --------------------------------------------------------------------------------------
# Deliverable 1 — the project-level collection
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConnectionCandidate:
    """
    One AI-extracted connection candidate, kept independently. `package` is the
    7W ConnectionReviewPackage — the extraction (never overwritten), the
    reviewer's supplement, and the known project members. `source_identity` is
    caller-supplied or None (never invented); `review_package_id` and
    `submission_index` are assigned from submission order and carry no
    engineering meaning.

    `origin` (J72) is WHERE this candidate was read from — the analysis run and
    the candidate's own position in that page's `raw_connections` array. It is
    None when the caller could not state it, and it is never derived here: a
    candidate the caller could not address is carried without an address rather
    than with a plausible one. Note that it is NOT `submission_index`: that is a
    position in the flattened multi-page submission order, and the two numbers
    agree only while a single page contributes every candidate.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    package: ConnectionReviewPackage
    origin: CandidateOrigin | None = None

    @property
    def extraction(self) -> AIExtractedConnection:
        return self.package.extraction


@dataclass(frozen=True)
class ProjectConnectionReviewCollection:
    """The candidates in SUBMISSION order (immutable), plus the project's known member marks (or None)."""
    candidates: tuple[ConnectionCandidate, ...] = ()
    known_member_marks: tuple[str, ...] | None = None
    project_id: str | None = None


def _review_package_id(index: int) -> str:
    return f"RP-{index + 1:04d}"


def require_submission_identities(collection: ProjectConnectionReviewCollection) -> None:
    """
    A candidate's review_package_id is DERIVED from its submission index
    (RP-{index+1:04d}), never supplied: a collection whose candidates
    disagree with their submission-derived identities is refused before
    any workflow consumes it — a renamed or renumbered candidate can
    never enter the review queue silently.
    """
    for candidate in collection.candidates:
        if candidate.review_package_id != _review_package_id(candidate.submission_index):
            raise ValueError(
                f"candidate {candidate.review_package_id!r} records submission index "
                f"{candidate.submission_index}; its submission-derived identity is "
                f"{_review_package_id(candidate.submission_index)!r} — candidate identity "
                "comes from submission order and can never be renamed or renumbered")


def create_project_connection_collection(
    raw_connections: Sequence[dict[str, Any]] = (),
    *,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
    drawing_number: str | None = None,
    known_member_marks: Collection[str] | None = None,
    source_identities: Sequence[str | None] | None = None,
    origins: Sequence[CandidateOrigin | None] | None = None,
) -> ProjectConnectionReviewCollection:
    """
    Wraps every raw AI connection object (the shape app/pipeline.py flattens into
    `connections_raw`, `page_num` included) into a candidate with an unreviewed
    7W package. `source_identities`, if given, must align one-to-one with
    `raw_connections`; without it every source_identity is None. The raw
    objects are not mutated. Order of `raw_connections` is submission order only.

    `origins` (J72), if given, must likewise align one-to-one: each is the
    candidate's `CandidateOrigin` or None where the caller could not address it.
    They are carried VERBATIM. Nothing here recomputes one from `index`, from
    `page_num`, or from the neighbour it was submitted beside — an origin arrives
    already stated by the producer that held the page, or it does not arrive.
    """
    if source_identities is not None and len(source_identities) != len(raw_connections):
        raise ValueError(
            f"source_identities has {len(source_identities)} entries for {len(raw_connections)} connections; "
            "they must align one-to-one (identities are never guessed)."
        )
    if origins is not None and len(origins) != len(raw_connections):
        raise ValueError(
            f"origins has {len(origins)} entries for {len(raw_connections)} connections; "
            "they must align one-to-one (an origin is never taken from a neighbouring candidate)."
        )
    known = None if known_member_marks is None else tuple(known_member_marks)
    candidates = []
    for index, raw in enumerate(raw_connections):
        extraction = ai_connection_to_extraction(
            raw, project_id=project_id, source_drawing_id=source_drawing_id, drawing_number=drawing_number,
        )
        candidates.append(ConnectionCandidate(
            review_package_id=_review_package_id(index),
            submission_index=index,
            source_identity=None if source_identities is None else source_identities[index],
            package=create_review_package(extraction, None, known),
            origin=None if origins is None else origins[index],
        ))
    return ProjectConnectionReviewCollection(tuple(candidates), known, project_id)


def update_candidate_supplement(
    collection: ProjectConnectionReviewCollection,
    review_package_id: str,
    supplement: ConnectionReviewSupplement,
) -> ProjectConnectionReviewCollection:
    """
    Records the reviewer's decisions for ONE candidate. Returns a new collection
    in which only that candidate differs; every other candidate is the same
    object. The AI extraction is never touched. The supplement is copied, so
    later edits to the caller's dicts cannot change the collection. An unknown
    id is a programming error and raises ValueError.
    """
    updated: list[ConnectionCandidate] = []
    found = False
    for candidate in collection.candidates:
        if candidate.review_package_id == review_package_id:
            found = True
            new_package = dataclasses.replace(candidate.package, supplement=copy.deepcopy(supplement))
            updated.append(dataclasses.replace(candidate, package=new_package))
        else:
            updated.append(candidate)
    if not found:
        raise ValueError(f"No candidate has review_package_id {review_package_id!r}.")
    return dataclasses.replace(collection, candidates=tuple(updated))


def with_known_member_marks(
    collection: ProjectConnectionReviewCollection, known_member_marks: Collection[str] | None,
) -> ProjectConnectionReviewCollection:
    """The same collection with the project member context replaced (passed straight to every 7W package)."""
    known = None if known_member_marks is None else tuple(known_member_marks)
    return dataclasses.replace(
        collection,
        known_member_marks=known,
        candidates=tuple(
            dataclasses.replace(c, package=dataclasses.replace(c.package, known_member_marks=known))
            for c in collection.candidates
        ),
    )


# --------------------------------------------------------------------------------------
# Deliverable 2 — deterministic presentation order
# --------------------------------------------------------------------------------------
def _present(value: Any) -> tuple[int, Any]:
    return (0, value) if isinstance(value, str) and value.strip() else (1, "")


def _presentation_key(candidate: ConnectionCandidate) -> tuple:
    page = candidate.extraction.source_page
    page_key = (0, page) if isinstance(page, int) and not isinstance(page, bool) else (1, 0)
    return (
        page_key, _present(candidate.extraction.detail_reference), _present(candidate.extraction.grid_reference),
        candidate.submission_index,
    )


def ordered_candidates(collection: ProjectConnectionReviewCollection) -> tuple[ConnectionCandidate, ...]:
    """
    Candidates in presentation order: source page, then detail reference, then grid reference (missing
    values last, exact string comparison, no natural-sort guessing), then submission order. For
    review/report display ONLY — it never determines any engineering value.
    """
    return tuple(sorted(collection.candidates, key=_presentation_key))


# --------------------------------------------------------------------------------------
# Deliverable 3 — possible duplicates (reported, never merged)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PossibleDuplicatePair:
    """Two candidates that look alike, and the weak signals that matched. A warning only."""
    first_review_package_id: str   # the earlier submission
    second_review_package_id: str
    signals: tuple[str, ...]


def _member_set(extraction: AIExtractedConnection) -> frozenset[str]:
    return frozenset(extraction.connected_member_references)


def _canonical(value: Any) -> Any:
    """
    A canonical, hashable-free, order-stable form of a preserved AI representation, used ONLY to decide whether two
    extractions are identical. It is driven by `dataclasses.fields()`, so every field the model preserves — including
    any added later — takes part automatically; there is no second list of comparison fields to go stale.

    Nothing is normalised. Values are kept as the AI reported them: 90 and 90.0 differ, True and 1 differ, list order
    is preserved (it is part of what was extracted), and dict entries are ordered by their own canonical form so
    key order never matters. Comparison is by value, never by object identity.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (type(value).__name__,
                tuple((f.name, _canonical(getattr(value, f.name))) for f in dataclasses.fields(value)))
    if isinstance(value, dict):
        return ("dict", tuple(sorted(((_canonical(k), _canonical(v)) for k, v in value.items()), key=repr)))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(_canonical(v) for v in value))
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, tuple(sorted((_canonical(v) for v in value), key=repr)))
    return (type(value).__name__, repr(value))


def _matching_signals(a: AIExtractedConnection, b: AIExtractedConnection) -> tuple[str, ...]:
    signals: list[str] = []
    if _present(a.detail_reference)[0] == 0 and a.detail_reference == b.detail_reference:
        signals.append(SIGNAL_SAME_DETAIL_REFERENCE)
    if (_present(a.grid_reference)[0] == 0 and a.grid_reference == b.grid_reference
            and _member_set(a) and _member_set(a) == _member_set(b)):
        signals.append(SIGNAL_SAME_GRID_AND_MEMBERS)
    # IDENTICAL means the WHOLE preserved AI extraction is identical — every field of AIExtractedConnection
    # (source information, references, members in the order extracted, type, bolts, plates, welds, confidence,
    # malformed and unrecognised fields, and anything added to the model later). Only the extraction is compared:
    # never a reviewer's supplement, so later human corrections cannot change this historical comparison. An
    # empty shell (nothing but source information) is not "identical content" and never matches.
    if _has_reviewable_content(a) and _canonical(a) == _canonical(b):
        signals.append(SIGNAL_IDENTICAL_AI_EXTRACTION_CONTENT)
    return tuple(signals)


def find_possible_duplicates(collection: ProjectConnectionReviewCollection) -> tuple[PossibleDuplicatePair, ...]:
    """
    Every pair of candidates sharing at least one weak-similarity signal, in submission order. Exact
    matching only; blank values never match. Nothing is merged, removed or altered by finding a pair.
    """
    pairs: list[PossibleDuplicatePair] = []
    candidates = collection.candidates
    for i, first in enumerate(candidates):
        for second in candidates[i + 1:]:
            signals = _matching_signals(first.extraction, second.extraction)
            if signals:
                pairs.append(PossibleDuplicatePair(first.review_package_id, second.review_package_id, signals))
    return tuple(pairs)


# --------------------------------------------------------------------------------------
# Deliverables 4/5/10 — per-candidate review rows and the project summary
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CandidateReview:
    """
    One candidate as a reviewer sees it: its identity, its traceable AI source facts (copied by reference from
    the untouched extraction), its 7W report, the derived state, and what is blocking or possibly duplicated.
    """
    review_package_id: str
    source_identity: str | None
    submission_index: int
    source_page: Any
    detail_reference: Any
    grid_reference: Any
    extracted_member_references: tuple[str, ...]   # exactly what the AI reported, even if since corrected
    generic_connection_type: Any
    confidence: Any
    state: str
    report: ConnectionReviewReport
    source_gaps: tuple[str, ...]                   # source_page / detail_reference / grid_reference not available
    blocking_issues: tuple[ReviewIssue, ...]       # current issues only (history never blocks)
    possible_duplicate_of: tuple[str, ...]
    has_reviewable_content: bool


@dataclass(frozen=True)
class ProjectConnectionReviewSummary:
    """
    Counts and rows describing the candidates the AI found — nothing about connections it did not find.
    The count fields are overlapping VIEWS (a candidate can be missing information and blocked at once);
    only `state_counts` is a partition of `total_candidates`.
    """
    total_candidates: int
    ready_for_human_review: int      # has AI-extracted content worth reviewing
    not_reviewable: int              # an empty shell: nothing extracted to review
    missing_information: int         # at least one engineering field still missing
    current_project_member_issues: int
    human_supplemented: int          # reviewer supplied at least one engineering field
    human_reviewed: int              # reviewer confirmed at least one AI value
    ready_for_7v: int
    blocked: int
    state_counts: dict[str, int]
    provenance_counts: dict[str, int]  # engineering fields per provenance label, across all candidates
    possible_duplicate_pairs: int
    member_context_supplied: bool
    member_references_unchecked: int   # candidates whose member marks could not be checked (no known marks)
    missing_source_page: int
    missing_detail_reference: int
    rows: tuple[CandidateReview, ...]  # presentation order
    duplicates: tuple[PossibleDuplicatePair, ...]
    scope_statement: str


def _has_reviewable_content(extraction: AIExtractedConnection) -> bool:
    return bool(
        extraction.connected_member_references or extraction.plates or extraction.bolts or extraction.welds
        or extraction.malformed_fields or extraction.unrecognised_fields
        or extraction.generic_connection_type is not None or extraction.confidence is not None
        or _present(extraction.detail_reference)[0] == 0 or _present(extraction.grid_reference)[0] == 0
    )


def _source_gaps(extraction: AIExtractedConnection) -> tuple[str, ...]:
    gaps = []
    page = extraction.source_page
    if not (isinstance(page, int) and not isinstance(page, bool)):
        gaps.append("source_page")
    if _present(extraction.detail_reference)[0] != 0:
        gaps.append("detail_reference")
    if _present(extraction.grid_reference)[0] != 0:
        gaps.append("grid_reference")
    return tuple(gaps)


def _duplicate_connection_id_issues(collection: ProjectConnectionReviewCollection) -> dict[str, ReviewIssue]:
    """A connection_id must identify ONE connection downstream: candidates given the same id are all blocked."""
    holders: dict[str, list[str]] = {}
    for c in collection.candidates:
        connection_id = c.package.supplement.connection_id
        if connection_id is not None:
            holders.setdefault(connection_id, []).append(c.review_package_id)
    issues: dict[str, ReviewIssue] = {}
    for connection_id, ids in holders.items():
        if len(ids) > 1:
            for package_id in ids:
                issues[package_id] = ReviewIssue(
                    "DUPLICATE_CONNECTION_ID", "connection_id",
                    f"connection_id {connection_id!r} was given to candidates {ids}; a connection_id must "
                    "identify exactly one connection, so none of them can proceed until this is resolved.",
                )
    return issues


def _derive_state(report: ConnectionReviewReport, blocking: tuple[ReviewIssue, ...]) -> str:
    if report.ready_for_pipeline and not blocking:
        return REVIEW_STATE_READY_FOR_PIPELINE
    if blocking:
        return STATE_BLOCKED
    return report.state  # 7W: AI_EXTRACTED (no reviewer decision yet) or REVIEW_REQUIRED


def _build_rows(
    collection: ProjectConnectionReviewCollection, duplicates: tuple[PossibleDuplicatePair, ...],
) -> tuple[CandidateReview, ...]:
    duplicate_of: dict[str, list[str]] = {}
    for pair in duplicates:
        duplicate_of.setdefault(pair.first_review_package_id, []).append(pair.second_review_package_id)
        duplicate_of.setdefault(pair.second_review_package_id, []).append(pair.first_review_package_id)
    id_issues = _duplicate_connection_id_issues(collection)

    rows = []
    for candidate in ordered_candidates(collection):
        report = build_review_report(candidate.package)
        blocking = tuple(i for i in report.issues if i.code not in _HISTORICAL_ISSUE_CODES)
        if candidate.review_package_id in id_issues:
            blocking += (id_issues[candidate.review_package_id],)
        ex = candidate.extraction
        rows.append(CandidateReview(
            review_package_id=candidate.review_package_id,
            source_identity=candidate.source_identity,
            submission_index=candidate.submission_index,
            source_page=ex.source_page,
            detail_reference=ex.detail_reference,
            grid_reference=ex.grid_reference,
            extracted_member_references=ex.connected_member_references,
            generic_connection_type=ex.generic_connection_type,
            confidence=ex.confidence,
            state=_derive_state(report, blocking),
            report=report,
            source_gaps=_source_gaps(ex),
            blocking_issues=blocking,
            possible_duplicate_of=tuple(sorted(duplicate_of.get(candidate.review_package_id, []))),
            has_reviewable_content=_has_reviewable_content(ex),
        ))
    return tuple(rows)


def build_project_review_summary(collection: ProjectConnectionReviewCollection) -> ProjectConnectionReviewSummary:
    """Deterministic, side-effect-free summary; never mutates the collection. Every state is derived."""
    duplicates = find_possible_duplicates(collection)
    rows = _build_rows(collection, duplicates)
    state_counts = {state: sum(1 for r in rows if r.state == state) for state in PROJECT_REVIEW_STATES}
    provenance_counts = {
        label: sum(1 for r in rows for value in r.report.provenance.values() if value == label)
        for label in (PROVENANCE_AI_EXTRACTED, PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED)
    }
    return ProjectConnectionReviewSummary(
        total_candidates=len(rows),
        ready_for_human_review=sum(1 for r in rows if r.has_reviewable_content),
        not_reviewable=sum(1 for r in rows if not r.has_reviewable_content),
        missing_information=sum(1 for r in rows if r.report.missing),
        current_project_member_issues=sum(1 for r in rows if r.report.current_unknown_member_marks),
        human_supplemented=sum(1 for r in rows if r.report.human_supplemented),
        human_reviewed=sum(1 for r in rows if r.report.human_reviewed),
        ready_for_7v=state_counts[REVIEW_STATE_READY_FOR_PIPELINE],
        blocked=state_counts[STATE_BLOCKED],
        state_counts=state_counts,
        provenance_counts=provenance_counts,
        possible_duplicate_pairs=len(duplicates),
        member_context_supplied=collection.known_member_marks is not None,
        member_references_unchecked=sum(1 for r in rows if "member_references" in r.report.advisories),
        missing_source_page=sum(1 for r in rows if "source_page" in r.source_gaps),
        missing_detail_reference=sum(1 for r in rows if "detail_reference" in r.source_gaps),
        rows=rows,
        duplicates=duplicates,
        scope_statement=SUMMARY_SCOPE_STATEMENT,
    )


# --------------------------------------------------------------------------------------
# Deliverable 8 — the subset that may enter 7V
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReadyReviewedConnection:
    """
    A candidate whose reviewed specification satisfied the 7W + 7V requirements. `specification` is 7V's input
    and `completeness` is 7V's own result, whose validated connection, location and attachments the existing
    reviewed-assembly builder consumes. Only reviewer-final values are present — historical AI errors are not.
    """
    review_package_id: str
    source_identity: str | None
    specification: ReviewedConnectionSpecification
    completeness: ReviewedConnectionCompletenessResult


def ready_reviewed_connections(collection: ProjectConnectionReviewCollection) -> tuple[ReadyReviewedConnection, ...]:
    """
    Exactly the candidates in state READY_FOR_PIPELINE, in presentation order (which carries no meaning). Generates
    no CAD: these feed 7V's downstream — reviewed assembly, 7R validation, drawing — which are outside this module.
    """
    by_id = {c.review_package_id: c for c in collection.candidates}
    return tuple(
        ReadyReviewedConnection(
            review_package_id=row.review_package_id,
            source_identity=row.source_identity,
            specification=build_reviewed_connection_specification(by_id[row.review_package_id].package),
            completeness=row.report.completeness,
        )
        for row in _build_rows(collection, find_possible_duplicates(collection))
        if row.state == REVIEW_STATE_READY_FOR_PIPELINE
    )
