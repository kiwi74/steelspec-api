"""
Milestone 7AY — Incremental Real-World Drawing Set Analysis Proof.

Given the accepted capture of the real Selby Square drawing set — pages 1-5,
tests/data/selby_square_page_extractions.json — and a NEW genuine extraction
of pages 6-10 (tests/data/selby_square_pages_6_10_extractions.json, produced
by scripts/capture_real_pdf_extraction.py --first-page 6 --max-pages 5
through the production vision path), this module combines the two evidence
sets and proves, through the existing 7Y intake and 7AX coverage layers:

  PRESERVATION — the prior evidence is preserved field-for-field. A
  deterministic digest pins the supplied prior pages; combining refuses to
  run against prior evidence whose digest does not match the recorded one
  (modified previous evidence is detected, never absorbed), and every prior
  candidate must appear unchanged — same review package id, submission
  order, source identity and extraction content, judged by the review
  queue's own canonical form — at the head of the combined queue.

  ACCOUNTING — every new page is recorded in exactly one state (ANALYSED or
  PARSE_FAILED — 7AX's vocabulary, never a third), every candidate the
  combined intake produces round-trips to exactly one recorded
  page-extraction entry and every entry to exactly one candidate (a
  bijection: nothing invented, nothing silently dropped), and the totals
  balance as combined = prior + new - identity merges, where the merge
  count is COMPUTED from the existing identity mechanism (which reports
  possible duplicates but never merges them) — never assumed to be zero.

  INTEGRATION — the combined intake flows through 7AX's production coverage:
  the caller evaluates the genuine combined workflow and supplies the
  before/after ProductionCoverage objects; this module renders them
  truthfully. New candidates stay in whatever review state the workflow
  recorded — nothing here resolves, approves or dispatches anything.

WHAT THIS MODULE NEVER DOES: no vision calls, no extraction, no engineering
transformation of AI values (a raw "M12" stays "M12", a raw "300" stays
"300" — no section/plate/location/START-END/welding/bolting inference), no
review decisions, no workflow resolution, no file writes — the existing
capture JSON is read-only input, and the preservation digest proves it.
No numeric literals appear in this module (AST-pinned by test): every page
number, count and threshold is supplied or discovered, never hard-coded.
"""

import copy
import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from app.cad_engine.connection_review_package import ai_connection_to_extraction
from app.cad_engine.production_coverage import (
    PAGE_STATUS_ANALYSED,
    PAGE_STATUS_PARSE_FAILED,
    ProductionCoverage,
)
from app.cad_engine.project_connection_review import (
    PossibleDuplicatePair,
    _canonical,
    find_possible_duplicates,
)
from app.cad_engine.project_extraction_intake import (
    ProjectExtractionIntake,
    intake_page_extractions,
)

__all__ = [
    "CombinedEvidence", "IncrementalAnalysisResult", "NewCandidateRecord",
    "PageStateRecord", "PreservationProof", "ProvenanceReport",
    "analyze_incremental", "build_combined_intake", "combine_page_extractions",
    "evidence_digest", "new_page_state_records", "verify_candidate_provenance",
]


@dataclasses.dataclass(frozen=True)
class PageStateRecord:
    """One new page's accounting: exactly one of 7AX's page states, with the
    page's own discovered entry counts. A parse-failed page contributes no
    usable entries; an analysed page with no connections is genuinely empty."""
    page_number: Any
    status: str
    member_entries: int
    connection_entries: int


@dataclasses.dataclass(frozen=True)
class NewCandidateRecord:
    """One NEW candidate the combined intake produced from the new pages:
    its queue identity, its source page and backing extraction entry, and
    its raw AI values VERBATIM (key order preserved, never transformed)."""
    review_package_id: str
    source_page: Any
    provenance: str
    raw_ai_values: tuple[tuple[str, Any], ...]


@dataclasses.dataclass(frozen=True)
class CombinedEvidence:
    """The two evidence sets combined: prior pages first (submission order
    preserves their queue identity), new pages appended, everything
    deep-copied. Pages keep the capture's own container form (a JSON list,
    as json.loads produced it), so the combined evidence compares equal to
    the supplied captures directly. Carries the preservation digests of
    both sets."""
    prior_pages: list[Mapping[str, Any]]
    new_pages: list[Mapping[str, Any]]
    combined_pages: list[Mapping[str, Any]]
    prior_digest: str
    new_digest: str
    new_page_range: tuple[Any, Any]


@dataclasses.dataclass(frozen=True)
class PreservationProof:
    """What combining proves about the prior evidence (nothing changed
    silently) and what it discloses (context growth is named, never hidden)."""
    prior_digest: str
    expected_prior_digest: str | None
    prior_candidate_count: int
    preserved_candidate_count: int
    new_known_member_marks: tuple[str, ...]
    duplicate_pairs: tuple[PossibleDuplicatePair, ...]


@dataclasses.dataclass(frozen=True)
class ProvenanceReport:
    """The bijection proof between recorded extraction entries and collection
    candidates, judged by the review queue's canonical form. `verified`
    requires exact multiset equality of content in both directions."""
    verified: bool
    candidates: int
    entries: int
    unusable_entries: int
    untraced_candidate_ids: tuple[str, ...]
    untraced_entry_positions: tuple[tuple[Any, int], ...]
    multiplicity_mismatches: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class IncrementalAnalysisResult:
    combined: CombinedEvidence
    page_states: tuple[PageStateRecord, ...]
    prior_intake: ProjectExtractionIntake
    combined_intake: ProjectExtractionIntake
    preservation: PreservationProof
    provenance: ProvenanceReport
    new_candidates: tuple[NewCandidateRecord, ...]
    coverage_before: ProductionCoverage
    coverage_after: ProductionCoverage
    report: str


def _is_positive_integer(value: Any) -> bool:
    """True for a positive non-bool int, checked without writing a numeric literal."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and bool(value)
        and abs(value) == value
    )


def _as_mapping(page: Any) -> Mapping[str, Any]:
    """A page as a plain mapping: the captured JSON form, or a PageExtraction."""
    if dataclasses.is_dataclass(page) and not isinstance(page, type):
        return dataclasses.asdict(page)
    if isinstance(page, Mapping):
        return dict(page)
    raise TypeError(
        f"page evidence must be a mapping or a PageExtraction (got {type(page).__name__})")


def _page_number(page: Mapping[str, Any]) -> Any:
    return page.get("page_number")


def _first(items: Sequence[Any]) -> Any:
    return next(iter(items), None)


def _last(items: Sequence[Any]) -> Any:
    return next(reversed(items), None)


def evidence_digest(pages: Sequence[Any]) -> str:
    """A deterministic digest of page-extraction evidence: page numbers, parse
    status, drawing identity and the raw AI content (members and connections),
    in the review queue's own canonical form. Equal digests mean the evidence
    is equal field-for-field."""
    canonical = _canonical([_as_mapping(p) for p in pages])
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def combine_page_extractions(
    prior_pages: Sequence[Any],
    new_pages: Sequence[Any],
    *,
    first_new_page: Any,
    last_new_page: Any,
    expected_prior_digest: str | None = None,
) -> CombinedEvidence:
    """
    Combines the accepted prior evidence with the new pages, prior first.
    REFUSES (ValueError naming the reason) on any of:
      - a page number recorded more than once within either set;
      - a new page whose number already appears in the prior evidence;
      - a new page outside the declared [first_new_page, last_new_page]
        range (an invented page is never absorbed);
      - prior evidence whose digest does not match the recorded
        `expected_prior_digest` (modified previous evidence is detected);
      - a declared range that is not two positive integers, first not
        after last.
    Both inputs are deep-copied: nothing here mutates the caller's data.
    """
    prior = [copy.deepcopy(_as_mapping(p)) for p in prior_pages]
    new = [copy.deepcopy(_as_mapping(p)) for p in new_pages]
    if (not (_is_positive_integer(first_new_page) and _is_positive_integer(last_new_page))
            or last_new_page < first_new_page):
        raise ValueError(
            "the declared new-page range must be two positive integers with the first not after the last")
    prior_numbers = [_page_number(p) for p in prior]
    new_numbers = [_page_number(p) for p in new]
    seen_prior: set[Any] = set()
    for number in prior_numbers:
        if number in seen_prior:
            raise ValueError(f"the prior evidence records page {number} more than once")
        seen_prior.add(number)
    seen_new: set[Any] = set()
    for number in new_numbers:
        if number in seen_new:
            raise ValueError(f"the new evidence records page {number} more than once")
        seen_new.add(number)
        if number in seen_prior:
            raise ValueError(f"new page {number} duplicates a page of the prior evidence")
        if not _is_positive_integer(number) or number < first_new_page or number > last_new_page:
            raise ValueError(f"new page {number} lies outside the declared new-page range")
    digest = evidence_digest(prior)
    if expected_prior_digest is not None and digest != expected_prior_digest:
        raise ValueError("the supplied prior evidence does not match the recorded preservation digest")
    return CombinedEvidence(
        prior_pages=prior,
        new_pages=new,
        combined_pages=prior + new,
        prior_digest=digest,
        new_digest=evidence_digest(new),
        new_page_range=(first_new_page, last_new_page),
    )


def new_page_state_records(new_pages: Sequence[Any]) -> tuple[PageStateRecord, ...]:
    """Exactly one state per new page — ANALYSED unless the extraction
    recorded a parse failure, which stays PARSE_FAILED. Counts are the
    page's own; a parse-failed page contributes no usable entries."""
    return tuple(
        PageStateRecord(
            page_number=_page_number(page),
            status=PAGE_STATUS_PARSE_FAILED if page.get("parse_failed") else PAGE_STATUS_ANALYSED,
            member_entries=len(page.get("raw_members") or ()),
            connection_entries=len(page.get("raw_connections") or ()),
        )
        for page in (_as_mapping(p) for p in new_pages)
    )


def build_combined_intake(
    combined: CombinedEvidence,
    *,
    project_id: str | None,
    source_drawing_id: str | None,
    known_member_marks: Sequence[str] | None,
    drawing_set_page_count: int | None,
) -> ProjectExtractionIntake:
    """The combined pages through the genuine 7Y intake. Prior pages come
    first, so their submission-order review package ids are preserved."""
    return intake_page_extractions(
        combined.combined_pages,
        project_id=project_id,
        source_drawing_id=source_drawing_id,
        known_member_marks=known_member_marks,
        drawing_set_page_count=drawing_set_page_count,
    )


def _entry_expectations(
    combined: CombinedEvidence, intake: ProjectExtractionIntake,
) -> dict[tuple[Any, int], Any]:
    """Every recorded extraction entry re-wrapped exactly as the intake's
    flattening wrapped it (page_num injected, the same project/drawing
    identity), reduced to the review queue's canonical form. Keyed by
    (page_number, entry index) — the entry's position in the evidence."""
    collection = intake.collection
    first_extraction = next(
        (candidate.extraction for candidate in collection.candidates), None)
    drawing_number = (
        first_extraction.drawing_number if first_extraction is not None else None)
    source_drawing_id = (
        first_extraction.source_drawing_id if first_extraction is not None else None)
    expectations: dict[tuple[Any, int], Any] = {}
    for page in combined.combined_pages:
        page_number = _page_number(page)
        for index, entry in enumerate(page.get("raw_connections") or ()):
            if isinstance(entry, dict):
                expectations[(page_number, index)] = _canonical(
                    ai_connection_to_extraction(
                        {**entry, "page_num": page_number},
                        project_id=collection.project_id,
                        source_drawing_id=source_drawing_id,
                        drawing_number=drawing_number,
                    ))
    return expectations


def verify_candidate_provenance(
    combined: CombinedEvidence,
    combined_intake: ProjectExtractionIntake,
) -> ProvenanceReport:
    """
    The bijection proof: every collection candidate round-trips to exactly one
    recorded page-extraction entry (same page, same content judged by the
    review queue's canonical form), and every entry to exactly one candidate.
    An invented candidate, or an entry whose candidate was silently dropped,
    fails the proof and is named. Unusable entries (not objects) are preserved
    by the intake as `unusable_connection_entries` and are counted, never
    turned into candidates.
    """
    expectations = _entry_expectations(combined, combined_intake)
    candidate_counts = Counter(
        _canonical(candidate.extraction)
        for candidate in combined_intake.collection.candidates)
    entry_counts = Counter(expectations.values())
    untraced_ids = tuple(
        candidate.review_package_id
        for candidate in combined_intake.collection.candidates
        if _canonical(candidate.extraction) not in entry_counts)
    untraced_positions = tuple(
        position for position, canonical in expectations.items()
        if canonical not in candidate_counts)
    mismatches = tuple(
        repr(canonical) for canonical in set(candidate_counts - entry_counts) | set(entry_counts - candidate_counts))
    return ProvenanceReport(
        verified=not untraced_ids and not untraced_positions and not mismatches,
        candidates=len(combined_intake.collection.candidates),
        entries=len(expectations),
        unusable_entries=len(combined_intake.unusable_connection_entries),
        untraced_candidate_ids=untraced_ids,
        untraced_entry_positions=untraced_positions,
        multiplicity_mismatches=mismatches,
    )


def _pair_candidates_to_entries(
    combined: CombinedEvidence, intake: ProjectExtractionIntake,
) -> dict[str, tuple[Any, int]]:
    """A deterministic pairing of each candidate to one backing entry, in
    submission order. Display provenance only — the bijection invariant is
    `verify_candidate_provenance`'s multiset equality; where two entries are
    content-identical the pairing is arbitrary but stable."""
    remaining: dict[Any, list[tuple[Any, int]]] = {}
    for position, canonical in _entry_expectations(combined, intake).items():
        remaining.setdefault(canonical, []).append(position)
    pairing: dict[str, tuple[Any, int]] = {}
    for candidate in intake.collection.candidates:
        positions = remaining.get(_canonical(candidate.extraction))
        if positions:
            pairing[candidate.review_package_id] = positions.pop()
    return pairing


def new_candidate_records(
    combined: CombinedEvidence,
    combined_intake: ProjectExtractionIntake,
) -> tuple[NewCandidateRecord, ...]:
    """The candidates the combined intake produced for the NEW pages only —
    identified by their source page, never by assuming the prior candidates
    came first. Each record carries the backing entry's raw AI values verbatim."""
    raw_by_position: dict[tuple[Any, int], dict[str, Any]] = {}
    for page in combined.combined_pages:
        page_number = _page_number(page)
        for index, entry in enumerate(page.get("raw_connections") or ()):
            if isinstance(entry, dict):
                raw_by_position[(page_number, index)] = entry
    pairing = _pair_candidates_to_entries(combined, combined_intake)
    new_numbers = {_page_number(p) for p in combined.new_pages}
    records: list[NewCandidateRecord] = []
    for candidate in combined_intake.collection.candidates:
        source_page = candidate.extraction.source_page
        if source_page not in new_numbers:
            continue
        position = pairing.get(candidate.review_package_id)
        entry_page, entry_index = position if position is not None else (None, None)
        records.append(NewCandidateRecord(
            review_package_id=candidate.review_package_id,
            source_page=source_page,
            provenance=(
                f"page {entry_page} extraction entry {entry_index}"
                if position is not None else "no backing extraction entry"),
            raw_ai_values=(
                tuple((key, copy.deepcopy(value)) for key, value in raw_by_position[position].items())
                if position is not None else ()),
        ))
    return tuple(records)


def preservation_proof(
    combined: CombinedEvidence,
    prior_intake: ProjectExtractionIntake,
    combined_intake: ProjectExtractionIntake,
    *,
    expected_prior_digest: str | None,
) -> PreservationProof:
    """
    Pages 1-5 preserved field-for-field: digest equality was enforced at
    combination; here every prior candidate must appear unchanged at the
    head of the combined queue (same id, submission index, source identity
    and extraction content in the queue's canonical form). Any growth of
    the known-member-marks context is DISCLOSED, never silent.
    """
    prior_candidates = prior_intake.collection.candidates
    combined_candidates = combined_intake.collection.candidates
    if len(combined_candidates) < len(prior_candidates):
        raise ValueError("the combined queue has fewer candidates than the prior evidence records")
    for index, prior in enumerate(prior_candidates):
        after = combined_candidates[index]
        if (prior.review_package_id != after.review_package_id
                or prior.submission_index != after.submission_index
                or prior.source_identity != after.source_identity
                or _canonical(prior.extraction) != _canonical(after.extraction)):
            raise ValueError(
                f"candidate {prior.review_package_id} of the prior evidence is not preserved "
                "field-for-field in the combined queue")
    prior_marks = set(prior_intake.collection.known_member_marks or ())
    new_marks = tuple(
        mark for mark in combined_intake.collection.known_member_marks or ()
        if mark not in prior_marks)
    return PreservationProof(
        prior_digest=combined.prior_digest,
        expected_prior_digest=expected_prior_digest,
        prior_candidate_count=len(prior_candidates),
        preserved_candidate_count=len(prior_candidates),
        new_known_member_marks=new_marks,
        duplicate_pairs=find_possible_duplicates(combined_intake.collection),
    )


def _render_report(
    *,
    combined: CombinedEvidence,
    page_states: tuple[PageStateRecord, ...],
    prior_intake: ProjectExtractionIntake,
    combined_intake: ProjectExtractionIntake,
    preservation: PreservationProof,
    provenance: ProvenanceReport,
    new_candidates: tuple[NewCandidateRecord, ...],
    coverage_before: ProductionCoverage,
    coverage_after: ProductionCoverage,
    source_description: str,
) -> str:
    """The deterministic §22 report. Every figure is recomputed from the
    supplied evidence; the repeatability section says exactly what is and is
    not repeatable about AI extraction."""
    prior_first = _page_number(_first(combined.prior_pages))
    prior_last = _page_number(_last(combined.prior_pages))
    new_first, new_last = combined.new_page_range
    prior_count = preservation.prior_candidate_count
    new_count = len(new_candidates)
    combined_count = len(combined_intake.collection.candidates)
    merge_count = prior_count + new_count - combined_count
    analysed = [s for s in page_states if s.status == PAGE_STATUS_ANALYSED]
    failed = [s for s in page_states if s.status == PAGE_STATUS_PARSE_FAILED]
    status_by_id = {row.package_id: row for row in coverage_after.connections}
    before = coverage_before.summary
    after = coverage_after.summary
    lines: list[str] = [
        "INCREMENTAL REAL-WORLD DRAWING SET ANALYSIS",
        "",
        f"Source: {source_description}",
        (f"Existing evidence: pages {prior_first}..{prior_last}, preservation digest "
         f"{combined.prior_digest}, {prior_count} candidates"),
        (f"New analysis: pages {new_first}..{new_last} — {len(analysed)} analysed, "
         f"{len(failed)} parse-failed, {new_count} new candidates, new-page digest {combined.new_digest}"),
    ]
    for state in page_states:
        lines.append(
            f"  page {state.page_number}: {state.status} "
            f"({state.member_entries} member entries, {state.connection_entries} connection entries)")
    lines.append(
        f"Combined coverage: {after.pages_analysed} analysed, {after.pages_parse_failed} parse-failed, "
        f"{after.pages_not_analysed} not analysed, {after.connections_discovered} candidates "
        f"({prior_count} prior + {new_count} new - {merge_count} identity merges)")
    lines.append("Candidate delta:")
    if new_candidates:
        for record in new_candidates:
            row = status_by_id.get(record.review_package_id)
            status_text = f"{row.disposition} ({row.reason})" if row is not None else "not covered"
            lines.append(
                f"  {record.review_package_id}  source page {record.source_page}  {record.provenance}  "
                f"workflow status {status_text}")
            lines.append(
                f"    raw AI values (verbatim): "
                f"{json.dumps(dict(record.raw_ai_values), ensure_ascii=True)}")
    else:
        lines.append("  (no new candidates)")
    digest_status = (
        "matched" if preservation.expected_prior_digest == preservation.prior_digest
        else "not supplied" if preservation.expected_prior_digest is None
        else "MISMATCH")
    marks_text = (
        "none" if not preservation.new_known_member_marks
        else ", ".join(preservation.new_known_member_marks))
    provenance_text = "holds" if provenance.verified else (
        f"FAILS — untraced candidates {provenance.untraced_candidate_ids or 'none'}; "
        f"untraced entries {provenance.untraced_entry_positions or 'none'}; "
        f"multiplicity mismatches {provenance.multiplicity_mismatches or 'none'}")
    lines += [
        "Preservation:",
        (f"  prior evidence digest {preservation.prior_digest} ({digest_status}); "
         f"{preservation.preserved_candidate_count} of {preservation.prior_candidate_count} prior candidates "
         "preserved field-for-field in the combined queue"),
        f"  known-member-marks context gained from the new pages: {marks_text}",
        f"  possible-duplicate pairs reported by the identity mechanism: {len(preservation.duplicate_pairs)}",
        f"  provenance bijection: {provenance_text}",
    ]
    for pair in preservation.duplicate_pairs:
        lines.append(
            f"    {pair.first_review_package_id} / {pair.second_review_package_id} — "
            f"{', '.join(pair.signals)}")
    lines += [
        "Repeatability:",
        ("  capture repeatability: the extraction artifact is stable once produced (pinned by the new-page digest); "
         "coverage repeatability: every figure above is recomputed deterministically from the saved evidence; "
         "this report does not claim that two independent AI extraction calls are byte-identical."),
        "Production status (7AX):",
        (f"  before: {before.project_status} ({before.pages_analysed} analysed, "
         f"{before.pages_parse_failed} parse-failed, {before.pages_not_analysed} not analysed, "
         f"{before.connections_discovered} candidates)"),
        (f"  after:  {after.project_status} ({after.pages_analysed} analysed, "
         f"{after.pages_parse_failed} parse-failed, {after.pages_not_analysed} not analysed, "
         f"{after.connections_discovered} candidates)"),
    ]
    for reason in after.incomplete_reasons:
        lines.append(f"    incomplete: {reason}")
    return "\n".join(lines) + "\n"


def analyze_incremental(
    prior_pages: Sequence[Any],
    new_pages: Sequence[Any],
    *,
    source_drawing_id: str,
    project_id: str | None = None,
    prior_known_member_marks: Sequence[str] | None = None,
    combined_known_member_marks: Sequence[str] | None = None,
    drawing_set_page_count: int | None = None,
    first_new_page: Any,
    last_new_page: Any,
    expected_prior_digest: str | None = None,
    coverage_before: ProductionCoverage,
    coverage_after: ProductionCoverage,
) -> IncrementalAnalysisResult:
    """
    The full incremental analysis: combine (refusing duplicates, out-of-range
    pages and modified prior evidence), rebuild the prior and combined intakes
    through the genuine 7Y intake, prove preservation, provenance and page
    accounting, and render the deterministic report over the caller's genuine
    7AX coverage objects. Pure and deterministic — the same inputs produce an
    equal result, report included.
    """
    combined = combine_page_extractions(
        prior_pages, new_pages,
        first_new_page=first_new_page, last_new_page=last_new_page,
        expected_prior_digest=expected_prior_digest,
    )
    prior_intake = intake_page_extractions(
        combined.prior_pages,
        project_id=project_id, source_drawing_id=source_drawing_id,
        known_member_marks=prior_known_member_marks,
        drawing_set_page_count=drawing_set_page_count,
    )
    combined_intake = build_combined_intake(
        combined,
        project_id=project_id, source_drawing_id=source_drawing_id,
        known_member_marks=combined_known_member_marks,
        drawing_set_page_count=drawing_set_page_count,
    )
    preservation = preservation_proof(
        combined, prior_intake, combined_intake,
        expected_prior_digest=expected_prior_digest)
    provenance = verify_candidate_provenance(combined, combined_intake)
    records = new_candidate_records(combined, combined_intake)
    page_states = new_page_state_records(combined.new_pages)
    report = _render_report(
        combined=combined,
        page_states=page_states,
        prior_intake=prior_intake,
        combined_intake=combined_intake,
        preservation=preservation,
        provenance=provenance,
        new_candidates=records,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        source_description=source_drawing_id,
    )
    return IncrementalAnalysisResult(
        combined=combined,
        page_states=page_states,
        prior_intake=prior_intake,
        combined_intake=combined_intake,
        preservation=preservation,
        provenance=provenance,
        new_candidates=records,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        report=report,
    )
