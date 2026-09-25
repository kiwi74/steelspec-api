"""
Step E — REAL-WORLD PRODUCTION SECTION COVERAGE AUDIT (read-only).

WHAT THIS FILE IS. A descriptive audit, not a fix and not a gate. It takes the
section tokens that genuine SteelSpec workloads actually produced, asks the
GENUINE production matcher what each one resolves to, and reports:

    A. how many distinct real-world section tokens exist
    B. how many resolve EXACTLY / via SUFFIX_FALLBACK / NONE
    C. the exact token list
    D. the fallback list, with the catalogue row each one was offered
    E. the unresolved list, grouped by reason
    F. the section-family breakdown, where the family is determinable
    G. the source workload / fixture breakdown
    H. the known cases, each measured rather than assumed
    I. the tokens that would need catalogue reconciliation
    J. the tokens genuinely unsupported by the current production builders
    K. the ambiguous / noisy tokens that need human interpretation

Nothing is ranked. A count in one bucket is not a priority, a score or a
judgement — it is a count, and this milestone fixes nothing it measures.

WHY A SEPARATE AUDIT AND NOT A COUNT INSIDE A TEST. Because the question is
"what does production do today", the answer has to come from production's own
matcher and production's own vocabulary, and it has to be measurable again
later against the same evidence. So this file:

  * drives app.engineering_data.section_matcher.SectionMatcher — the real
    class, resolving through its real resolve() — against the captured live
    steel_sections rows. It never uses a local or proof-only matcher as the
    production authority, and it refuses to run at all if handed one (see
    audit_authority_violations / AuditAuthorityError).
  * reads the resolution vocabulary from the matcher's own RESOLUTIONS and the
    non-steel vocabulary from app.validation.rules' own classify_member, so the
    report cannot drift into a private vocabulary of its own.
  * never repairs, never adds a catalogue row, never touches a production file.
    The autouse guard at the bottom hashes every production module and every
    captured fixture around this module and fails if anything moved.

THE SEMANTICS THIS FILE REFUSES TO BLUR:

  * A SUFFIX_FALLBACK is NOT an exact supported section. The drawing token is
    NOT the catalogue row that was offered; the token's own row does not exist.
    The report states that on every fallback entry, and the integrity rules
    reject any fallback whose offered row IS the drawing token — so a fallback
    can never be counted as an exact section, silently or otherwise.
  * NONE is never reported as a resolution of any kind. A NONE entry carries no
    offered row, ever — asserted for every entry rather than for a sample.
  * An unresolved token gets a reason only where the evidence establishes one.
    Where it does not, the reason is UNKNOWN_REASON. A plausible-sounding story
    is not evidence, and this audit does not write one: it reports what the
    matcher did, never what a token "probably meant".

THE REAL INPUTS. Every token below was extracted by the production vision
pipeline from a genuine drawing, or is one of the 54 real rows pulled from the
live Arkles Strand project. No synthetic section name appears in the coverage
report; the raw drawing token is preserved exactly as extracted, including its
spelling, case and spacing. Synthetic values appear only in the explicit
edge-case tests at the end, which are marked as such.
"""
import copy
import hashlib
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.validation.rules import is_timber_or_non_steel
from tests.test_arkles_strand import ARKLES_STRAND_RAW

# ---------------------------------------------------------------------------
# Where the evidence lives
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent
CAPTURE_DIR = REPO / "tests" / "data"

# Pinned so that adding a capture is a deliberate act: this audit measures a
# FIXED evidence set, and a changed set is a different audit, not a new number.
CAPTURE_FILES = (
    "arkles_strand_page_extractions.json",
    "selby_square_page_26_recovery.json",
    "selby_square_page_extractions.json",
    "selby_square_pages_11_15_extractions.json",
    "selby_square_pages_16_20_extractions.json",
    "selby_square_pages_21_25_extractions.json",
    "selby_square_pages_26_30_extractions.json",
    "selby_square_pages_31_32_extractions.json",
    "selby_square_pages_6_10_extractions.json",
)

# The 54 rows pulled from the live Arkles Strand project — a genuine production
# workload rather than an extraction fixture: these are rows the pipeline
# actually persisted.
SOURCE_ARKLES_WORKLOAD = "ARKLES_STRAND_RAW (live project 0d05e86d-5437-40ab-a36c-a3a0d049a88d)"

# The captured live reference rows, and the digest this audit refuses to
# measure against anything else. Same capture and same pin as the accepted
# production section-authority tests.
SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
SNAPSHOT_ROW_COUNT = 218
LIVE_DIGEST = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"

TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "fake-test-signature-not-a-real-key"
)

# ---------------------------------------------------------------------------
# The two vocabularies this audit reports in — both taken from production
# ---------------------------------------------------------------------------
EXACT = "EXACT"
SUFFIX_FALLBACK = "SUFFIX_FALLBACK"
NONE = "NONE"

# Why a token resolved to nothing. EXACT and SUFFIX_FALLBACK entries carry no
# reason at all: they resolved, so there is nothing unresolved to explain.
REASON_NON_STEEL = "EXTRACTION_VOCABULARY_NON_STEEL"
REASON_NO_DESIGNATION = "EXTRACTION_VOCABULARY_NO_SECTION_DESIGNATION"
REASON_MALFORMED = "MALFORMED_OR_AMBIGUOUS_TOKEN"
REASON_UNSUPPORTED_FAMILY = "UNSUPPORTED_SECTION_FAMILY"
REASON_MISSING_ROW = "MISSING_CATALOGUE_ROW"
REASON_UNKNOWN = "UNKNOWN_REASON"
REASONS = (
    REASON_NON_STEEL,
    REASON_NO_DESIGNATION,
    REASON_MALFORMED,
    REASON_UNSUPPORTED_FAMILY,
    REASON_MISSING_ROW,
    REASON_UNKNOWN,
)

# The section-family codes a real drawing token can carry — the same
# vocabulary the production matcher's regexes and app/validation/rules.py's
# MARK_ANOMALY_HINT are written in.
FAMILY_CODES = ("UB", "UC", "PFC", "SHS", "RHS", "CHS", "EA", "UA", "FL", "PL")

# A flat bar is written "FL" on a drawing; the live catalogue stores the family
# under its own name, "FLAT". That mapping is the catalogue's, not an
# inference: FLAT is the family the live FL rows actually carry.
CATALOGUE_FAMILY_OF_CODE = {"FL": "FLAT"}

# Two or more designations joined into one token, or an explicit separator. The
# matcher resolves one section, so a token naming several is ambiguous by
# construction.
COMPOUND_MARKERS = ("&", ",", " AND ", "+")

MULTIPLE_FAMILY_CODES = "MULTIPLE_FAMILY_CODES"
NO_FAMILY_CODE = "NO_FAMILY_CODE"


def _canonical(text: str) -> str:
    """The matcher's own normalisation — whitespace stripped, upper-cased."""
    return re.sub(r"\s+", "", text.strip().upper())


# ===========================================================================
# The corpus — every section token the real workloads recorded, verbatim
# ===========================================================================
@dataclass(frozen=True)
class Occurrence:
    """One place a token really appeared. Never synthesised."""
    raw_token: str
    source: str
    page: int | None
    mark: str | None


def build_corpus() -> tuple[Occurrence, ...]:
    """
    Every (token, source, page, mark) in the fixed evidence set.

    A member's `section` field is the ONE section-designation position the
    extraction contract has, so that is what is read. A connection's plates
    carry a plate TYPE ("end plate", "web plate") — a different field answering
    a different question. connection_context_tokens() measures what the
    connection level actually holds, so the audit states that no section token
    is hiding there instead of assuming it.
    """
    found = sorted(p.name for p in CAPTURE_DIR.glob("*.json"))
    if found != list(CAPTURE_FILES):
        raise AssertionError(
            "the capture fixture set changed — this audit measures a fixed evidence "
            "set and refuses to silently measure a different one.\n"
            f"  pinned: {list(CAPTURE_FILES)}\n  found:  {found}"
        )

    occurrences = []
    for name in CAPTURE_FILES:
        for page in json.loads((CAPTURE_DIR / name).read_text()):
            if not isinstance(page, dict):
                continue
            for member in page.get("raw_members") or []:
                if isinstance(member, dict) and member.get("section"):
                    occurrences.append(Occurrence(
                        raw_token=member["section"],
                        source=name,
                        page=page.get("page_number"),
                        mark=member.get("mark"),
                    ))
    for row in ARKLES_STRAND_RAW:
        occurrences.append(Occurrence(
            raw_token=row["section"],
            source=SOURCE_ARKLES_WORKLOAD,
            page=row.get("source_page"),
            mark=row.get("mark"),
        ))

    if not occurrences:
        raise AssertionError("the audit found no real-world section tokens to measure")
    return tuple(occurrences)


def connection_context_tokens() -> tuple[str, ...]:
    """The plate TYPE strings the connection level records, measured."""
    found = set()
    for name in CAPTURE_FILES:
        for page in json.loads((CAPTURE_DIR / name).read_text()):
            if not isinstance(page, dict):
                continue
            for connection in page.get("raw_connections") or []:
                for plate in (connection.get("plates") or []):
                    plate_type = plate.get("type")
                    if isinstance(plate_type, str) and plate_type.strip():
                        found.add(plate_type)
    return tuple(sorted(found))


# ===========================================================================
# The production authority — the real matcher, or no audit at all
# ===========================================================================
PRODUCTION_MATCHER = ("app.engineering_data.section_matcher", "SectionMatcher")


class AuditAuthorityError(RuntimeError):
    """The audit was handed something that is not the production authority."""


def audit_authority_violations(matcher) -> tuple[str, ...]:
    """
    Every way the thing being audited could be something other than the genuine
    production matcher. A local or proof-only matching engine is not the
    production authority: it has its own rows, its own normalisation and no
    fallback route, so auditing through it would report a coverage production
    does not have.
    """
    problems = []
    cls = type(matcher)
    if (cls.__module__, cls.__qualname__) != PRODUCTION_MATCHER:
        problems.append(
            f"the audit's matcher is {cls.__module__}.{cls.__qualname__}, not the "
            f"production {'.'.join(PRODUCTION_MATCHER)} — a proof-only or local "
            "catalogue matcher is not the production authority"
        )
    if not callable(getattr(matcher, "resolve", None)):
        problems.append(
            "the audit's matcher has no resolve() — it cannot report which route "
            "produced a row, so a suffix fallback could not be told from an exact hit"
        )
    if getattr(matcher, "reference_identity", None) is None:
        problems.append(
            "the audit's matcher reports no reference identity — the audit could not "
            "name the dataset it consulted"
        )
    if not isinstance(getattr(matcher, "_lookup", None), dict):
        problems.append(
            "the audit's matcher exposes none of the rows it loaded — the audit could "
            "not establish the catalogue's own families, and would have to guess them"
        )
    return tuple(problems)


# ===========================================================================
# The audit record
# ===========================================================================
@dataclass(frozen=True)
class TokenAudit:
    """One distinct drawing token, and the matcher's own answer for it."""
    raw_token: str
    drawing_token: str
    resolution: str
    offered_row_name: str | None
    offered_family: str | None
    family_code: str | None
    family_catalogue_name: str | None
    family_in_catalogue: bool
    reason: str | None
    occurrences: tuple[Occurrence, ...]

    @property
    def offered_row_is_the_token(self) -> bool:
        return (
            self.offered_row_name is not None
            and _canonical(self.offered_row_name) == self.drawing_token
        )

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({o.source for o in self.occurrences}))

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({o.page for o in self.occurrences if o.page is not None}))


@dataclass(frozen=True)
class KnownCase:
    """A token the milestone named, with the result the real matcher gives."""
    token: str
    resolution: str
    offered_row_name: str | None
    stated_by_the_milestone: bool


# Measured in this milestone against the genuine matcher and the captured live
# rows. The three the milestone stated in advance are marked as such; the other
# two are reported as measured, with no expectation assumed for them.
KNOWN_CASES = (
    KnownCase("310UB46.2", EXACT, "310UB46.2", True),
    KnownCase("310UB40", SUFFIX_FALLBACK, "310UB40.4", True),
    KnownCase("250X90PFC", NONE, None, True),
    KnownCase("200UB25", SUFFIX_FALLBACK, "200UB25.4", False),
    KnownCase("150X75PFC", NONE, None, False),
)


@dataclass(frozen=True)
class SectionCoverageAudit:
    """The frozen result. A record to read — it has no way to change anything."""
    corpus: tuple[Occurrence, ...]
    tokens: tuple[TokenAudit, ...]
    catalogue_rows: tuple[dict, ...]
    catalogue_established: bool
    catalogue_families: tuple[str, ...]
    reference_source_kind: str
    reference_identity_status: str
    reference_data_digest: str | None
    connection_tokens: tuple[str, ...]
    known_case_results: tuple[tuple[str, str, str | None, bool, bool], ...]

    # --- A / B -----------------------------------------------------------
    @property
    def occurrence_count(self) -> int:
        return len(self.corpus)

    @property
    def raw_token_count(self) -> int:
        return len(self.tokens)

    @property
    def normalised_token_count(self) -> int:
        return len({t.drawing_token for t in self.tokens})

    def _count(self, tokens) -> dict:
        counts = {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 0}
        for token in tokens:
            counts[token.resolution] += 1
        return counts

    @property
    def counts_by_resolution(self) -> dict:
        """Counts over the distinct tokens AS EXTRACTED (raw, verbatim)."""
        return self._count(self.tokens)

    @property
    def counts_by_resolution_normalised(self) -> dict:
        """Counts over distinct normalised tokens — the matcher's own unit."""
        first = {}
        for token in self.tokens:
            first.setdefault(token.drawing_token, token)
        return self._count(first.values())

    # --- C / D / E -------------------------------------------------------
    @property
    def exact(self) -> tuple[TokenAudit, ...]:
        return tuple(t for t in self.tokens if t.resolution == EXACT)

    @property
    def fallbacks(self) -> tuple[TokenAudit, ...]:
        return tuple(t for t in self.tokens if t.resolution == SUFFIX_FALLBACK)

    @property
    def unresolved(self) -> tuple[TokenAudit, ...]:
        return tuple(t for t in self.tokens if t.resolution == NONE)

    @property
    def counts_by_reason(self) -> dict:
        counts = {reason: 0 for reason in REASONS}
        for token in self.unresolved:
            if token.reason in counts:
                counts[token.reason] += 1
        return counts

    def unresolved_with_reason(self, reason: str) -> tuple[TokenAudit, ...]:
        return tuple(t for t in self.unresolved if t.reason == reason)

    # --- F ---------------------------------------------------------------
    @property
    def family_breakdown(self) -> tuple[tuple[str, dict], ...]:
        """
        Per family code the token itself carries, the resolution counts. A token
        carrying no recognised code is reported under NO_FAMILY_CODE, and one
        carrying two different codes under MULTIPLE_FAMILY_CODES — neither is
        silently dropped, nor attributed to one family.
        """
        buckets = {}
        for token in self.tokens:
            if token.family_code is None:
                label = (
                    MULTIPLE_FAMILY_CODES if _token_family_codes(token.raw_token)
                    else NO_FAMILY_CODE
                )
            else:
                label = token.family_code
            buckets.setdefault(label, []).append(token)
        return tuple((label, self._count(buckets[label])) for label in sorted(buckets))

    # --- G ---------------------------------------------------------------
    @property
    def source_breakdown(self) -> tuple[tuple[str, int, int, dict], ...]:
        """(source, distinct raw tokens, occurrences, counts by resolution)."""
        rows = []
        for source in sorted({o.source for o in self.corpus}):
            tokens = [t for t in self.tokens if source in t.sources]
            occurrences = sum(1 for o in self.corpus if o.source == source)
            rows.append((source, len(tokens), occurrences, self._count(tokens)))
        return tuple(rows)

    # --- H ---------------------------------------------------------------
    @property
    def known_cases(self) -> tuple[tuple[str, str, str | None, bool, bool], ...]:
        """
        (token, measured resolution, offered row, stated by the milestone,
        present in the audited corpus). Every one is MEASURED — including the
        milestone's stated case, which the captures do not actually contain.
        Presence is measured too: a token the milestone named is not thereby a
        token the real captures produced.
        """
        return self.known_case_results

    # --- I / J / K -------------------------------------------------------
    def _family_rows(self, family: str | None) -> tuple[dict, ...]:
        return tuple(row for row in self.catalogue_rows if row.get("family") == family)

    def _sharing_leading_dimension(self, drawing_token: str, family: str | None) -> tuple[str, ...]:
        leading = re.match(r"[\d.]+", drawing_token)
        if leading is None:
            return ()
        prefix = leading.group(0)
        return tuple(sorted(
            row["name"] for row in self._family_rows(family)
            if re.match(r"[\d.]+", row["name"]) and re.match(r"[\d.]+", row["name"]).group(0) == prefix
        ))

    @property
    def reconciliation_candidates(self) -> tuple[tuple[str, str, int, tuple[str, ...]], ...]:
        """
        I. Unresolved tokens whose family the catalogue DOES support, with the
        measured evidence around the gap: how many rows that family has, and
        which of them share the token's leading dimension.

        Those two facts are recorded so a later milestone can decide what
        reconciliation is evidence-backed. They are not a recommendation, and
        this audit proposes no row: knowing that 310UB42 shares "310" with three
        real rows does not make "310UB42" a section that should exist.
        """
        rows = []
        for token in self.unresolved_with_reason(REASON_MISSING_ROW):
            family = token.family_catalogue_name
            rows.append((
                token.raw_token,
                family,
                len(self._family_rows(family)),
                self._sharing_leading_dimension(token.drawing_token, family),
            ))
        return tuple(rows)

    @property
    def unsupported_by_production_builders(self) -> tuple[tuple[str, str], ...]:
        """
        J. Tokens whose family is not in the live catalogue at all — no row and
        no family. Nothing in the current builders can resolve these, because
        the reference data they would resolve against does not exist.
        """
        return tuple(
            (t.raw_token, t.family_code)
            for t in self.unresolved_with_reason(REASON_UNSUPPORTED_FAMILY)
        )

    @property
    def needing_human_interpretation(self) -> tuple[tuple[str, str], ...]:
        """
        K. Tokens no evidence can resolve, with the sub-reason that puts each
        one there — reported per sub-reason so that "the production predicate
        already says this is not steel" is never mixed in with "a human has to
        decide what section this was meant to be".
        """
        wanted = (REASON_NON_STEEL, REASON_NO_DESIGNATION, REASON_MALFORMED, REASON_UNKNOWN)
        return tuple(
            (t.raw_token, t.reason)
            for t in self.tokens
            if t.resolution == NONE and t.reason in wanted
        )

    # --- reader-facing evidence ------------------------------------------
    def printable_summary(self) -> str:
        """A deterministic, complete rendering of A–K, for the report."""
        lines = []
        add = lines.append
        add("=" * 78)
        add("REAL-WORLD PRODUCTION SECTION COVERAGE AUDIT")
        add("=" * 78)
        add(f"reference source      : {self.reference_source_kind}")
        add(f"reference status      : {self.reference_identity_status}")
        add(f"reference digest      : {self.reference_data_digest}")
        add(f"catalogue rows loaded : {len(self.catalogue_rows)}"
            f" (established: {self.catalogue_established})")
        add(f"catalogue families    : {', '.join(self.catalogue_families)}")

        counts = self.counts_by_resolution
        norm = self.counts_by_resolution_normalised
        add("")
        add(f"A. distinct real-world section tokens : {self.raw_token_count} raw, "
            f"{self.normalised_token_count} normalised, {self.occurrence_count} occurrences")
        add(f"B. by resolution : EXACT {counts[EXACT]}  SUFFIX_FALLBACK {counts[SUFFIX_FALLBACK]}"
            f"  NONE {counts[NONE]}    (normalised: {norm[EXACT]}/{norm[SUFFIX_FALLBACK]}/{norm[NONE]})")

        add("")
        add(f"C. EXACT ({len(self.exact)}) — the drawing token IS the catalogue row")
        for token in self.exact:
            add(f"     {token.raw_token!r} -> {token.offered_row_name!r} "
                f"[{token.offered_family}] {','.join(token.sources)} pages {list(token.pages)}")

        add("")
        add(f"D. SUFFIX_FALLBACK ({len(self.fallbacks)}) — the drawing token is NOT the offered row")
        for token in self.fallbacks:
            add(f"     {token.raw_token!r} -> offered {token.offered_row_name!r} "
                f"(token {token.drawing_token!r} != row "
                f"{_canonical(token.offered_row_name or '')!r}) [{token.offered_family}] "
                f"{','.join(token.sources)} pages {list(token.pages)}")

        add("")
        add(f"E. NONE ({len(self.unresolved)}) by reason")
        for reason in REASONS:
            entries = self.unresolved_with_reason(reason)
            if not entries:
                continue
            add(f"     {reason} ({len(entries)})")
            for token in entries:
                add(f"        {token.raw_token!r}")

        add("")
        add("F. by family code")
        for label, family_counts in self.family_breakdown:
            add(f"     {label:24} EXACT {family_counts[EXACT]}  "
                f"FALLBACK {family_counts[SUFFIX_FALLBACK]}  NONE {family_counts[NONE]}")

        add("")
        add("G. by source")
        for source, distinct, occurrences, source_counts in self.source_breakdown:
            add(f"     {source:48} tokens {distinct:3} occurrences {occurrences:3} "
                f"({source_counts[EXACT]}/{source_counts[SUFFIX_FALLBACK]}/{source_counts[NONE]})")

        add("")
        add("H. known cases (measured against the genuine matcher)")
        for token, resolution, offered, stated, present in self.known_cases:
            add(f"     {token:12} {resolution:16} offered={str(offered):14} "
                f"stated_by_milestone={stated} in_corpus={present}")

        add("")
        add(f"I. catalogue reconciliation candidates ({len(self.reconciliation_candidates)})")
        for token, family, row_count, sharing in self.reconciliation_candidates:
            add(f"     {token:16} family {family:5} rows {row_count:3} "
                f"same leading dimension: {list(sharing)}")

        add("")
        add(f"J. unsupported by the current production builders "
            f"({len(self.unsupported_by_production_builders)})")
        for token, family in self.unsupported_by_production_builders:
            add(f"     {token:16} family code {family} (absent from the live catalogue)")

        add("")
        add(f"K. requiring human interpretation ({len(self.needing_human_interpretation)})")
        for token, reason in self.needing_human_interpretation:
            add(f"     {token!r} — {reason}")

        add("")
        add(f"connection-level tokens (a plate TYPE, not a section): {list(self.connection_tokens)}")
        add("=" * 78)
        return "\n".join(lines)


def _measure_token(tokens: tuple[TokenAudit, ...], raw_token: str) -> TokenAudit:
    for token in tokens:
        if token.raw_token == raw_token:
            return token
    raise KeyError(raw_token)


# ===========================================================================
# Classification — the reason, and only from the evidence
# ===========================================================================
def _token_family_codes(raw_token: str) -> tuple[str, ...]:
    """Every family code the token itself carries, in the order it carries them."""
    return tuple(
        run for run in re.findall(r"[A-Z]+", raw_token.upper()) if run in FAMILY_CODES
    )


def _token_family_code(raw_token: str) -> str | None:
    """The token's family code, or None when it names none or names several."""
    codes = set(_token_family_codes(raw_token))
    return codes.pop() if len(codes) == 1 else None


def _is_compound(raw_token: str) -> bool:
    upper = raw_token.upper()
    if any(marker in upper for marker in COMPOUND_MARKERS):
        return True
    return len(set(_token_family_codes(raw_token))) > 1


def reason_for_unresolved(
    raw_token: str,
    family_code: str | None,
    catalogue_families: frozenset | None,
    is_non_steel: bool,
) -> str:
    """
    Why the matcher resolved nothing — established, or UNKNOWN_REASON.

    Every branch is decided by evidence this audit actually holds: the
    production predicate's own verdict, the token's own structure, and the live
    catalogue's own family set. The order matters, because a token can be more
    than one of these, and the first the evidence establishes is the honest
    answer:

      1. the production predicate classifies it as non-steel material
      2. it names more than one designation, or joins them explicitly
      3. it carries no recognised section family code at all
      4. the catalogue's own families were NOT established -> UNKNOWN_REASON
      5. its family exists on no live catalogue row
      6. its family exists, but the token and its ".0"..".9" extensions do not
      7. nothing above is established -> UNKNOWN_REASON

    Branch 4 is the one that matters for honesty: with no established catalogue,
    the audit cannot tell "this family is unsupported" from "this row is
    missing", and both would be a guess about a dataset it never read. So it
    declines, per token, rather than rounding to a plausible reason.

    Nothing here decides what a token MEANT. A token whose family is supported
    and whose row is absent is MISSING_CATALOGUE_ROW — not "a typo", not "an
    old size", not "a misread dimension". Those would be stories, and this audit
    reports evidence.
    """
    if is_non_steel:
        return REASON_NON_STEEL
    if _is_compound(raw_token):
        return REASON_MALFORMED
    if family_code is None:
        return REASON_NO_DESIGNATION
    if catalogue_families is None:
        return REASON_UNKNOWN
    catalogue_family = CATALOGUE_FAMILY_OF_CODE.get(family_code, family_code)
    if catalogue_family not in catalogue_families:
        return REASON_UNSUPPORTED_FAMILY
    return REASON_MISSING_ROW


def _audit_token(matcher, raw_token: str, catalogue_families: frozenset | None) -> TokenAudit:
    match = matcher.resolve(raw_token)
    offered = match.catalogue_row
    family_code = _token_family_code(raw_token)
    catalogue_family = (
        CATALOGUE_FAMILY_OF_CODE.get(family_code, family_code) if family_code else None
    )
    reason = None
    if match.resolution == NONE:
        reason = reason_for_unresolved(
            raw_token, family_code, catalogue_families, is_timber_or_non_steel(raw_token)
        )
    return TokenAudit(
        raw_token=raw_token,
        drawing_token=match.drawing_token,
        resolution=match.resolution,
        offered_row_name=(offered or {}).get("name"),
        offered_family=(offered or {}).get("family"),
        family_code=family_code,
        family_catalogue_name=catalogue_family,
        family_in_catalogue=bool(
            catalogue_families is not None
            and catalogue_family is not None
            and catalogue_family in catalogue_families
        ),
        reason=reason,
        occurrences=(),
    )


def build_audit(matcher, corpus: tuple[Occurrence, ...], rules) -> SectionCoverageAudit:
    """
    Ask the production matcher about every real token, and record its answers.

    Refuses to run against anything but the production matcher and the
    production non-steel predicate: a report about production coverage is
    worthless if it was measured through a different engine or a different
    vocabulary. Adds no row, changes no row, and offers no route by which a
    resolution could be promoted — the resolution recorded IS the one the
    matcher returned.
    """
    problems = audit_authority_violations(matcher)
    if problems:
        raise AuditAuthorityError(
            "refusing to audit production coverage through a non-production matcher: "
            + "; ".join(problems)
        )
    if rules.is_timber_or_non_steel is not is_timber_or_non_steel:
        raise AuditAuthorityError(
            "refusing to audit through a non-production non-steel predicate: the "
            "reason for an unresolved token would come from a different vocabulary "
            "than the one production classifies with"
        )

    rows = tuple(dict(row) for row in matcher._lookup.values())
    catalogue_families = (
        frozenset(row["family"] for row in rows if row.get("family") is not None)
        if rows else None
    )

    answers = {}
    grouped: dict[str, list[Occurrence]] = {}
    for occurrence in corpus:
        if occurrence.raw_token not in answers:
            answers[occurrence.raw_token] = _audit_token(
                matcher, occurrence.raw_token, catalogue_families
            )
        grouped.setdefault(occurrence.raw_token, []).append(occurrence)

    # The named cases are measured through the same matcher, whether or not a
    # capture happens to contain them — so a stated production behaviour is
    # recorded as evidence about the matcher, not as a claim about the corpus.
    known_cases = []
    for case in KNOWN_CASES:
        if case.token in answers:
            measured = replace(answers[case.token], occurrences=tuple(grouped[case.token]))
        else:
            measured = _audit_token(matcher, case.token, catalogue_families)
        known_cases.append((
            case.token,
            measured.resolution,
            measured.offered_row_name,
            case.stated_by_the_milestone,
            case.token in answers,
        ))

    identity = matcher.reference_identity
    return SectionCoverageAudit(
        corpus=corpus,
        tokens=tuple(
            replace(answers[token], occurrences=tuple(grouped[token]))
            for token in sorted(grouped)
        ),
        catalogue_rows=rows,
        catalogue_established=catalogue_families is not None,
        catalogue_families=tuple(sorted(catalogue_families or ())),
        reference_source_kind=identity.source_kind,
        reference_identity_status=identity.identity_status,
        reference_data_digest=identity.reference_data_digest,
        connection_tokens=connection_context_tokens(),
        known_case_results=tuple(known_cases),
    )


# ===========================================================================
# Integrity — every way this audit could be lying, as a pure function
# ===========================================================================
def audit_violations(report: SectionCoverageAudit) -> tuple[str, ...]:
    """
    Every way the report could misrepresent the matcher's own answer. Empty
    means: the report says exactly what the matcher said.

    This is the rule the promotion mutations must break, and it is written so
    that neither of them CAN pass:
      * EXACT requires an offered row whose normalised name IS the token, so a
        fallback relabelled EXACT is rejected even when a row is attached;
      * SUFFIX_FALLBACK requires an offered row that is the token PLUS a
        ".0"..".9" extension, so a fallback whose row is the token itself is
        rejected, and so is relabelling a fallback as an exact hit;
      * NONE requires no offered row at all, so an unresolved token relabelled
        as resolved cannot quietly carry one.
    """
    problems = []
    seen = set()
    for token in report.tokens:
        label = repr(token.raw_token)
        if token.resolution not in (EXACT, SUFFIX_FALLBACK, NONE):
            problems.append(f"{label}: unknown resolution {token.resolution!r}")
            continue
        if token.raw_token in seen:
            problems.append(f"{label}: reported more than once")
        seen.add(token.raw_token)
        if not token.occurrences:
            problems.append(f"{label}: reported with no real occurrence")
        for occurrence in token.occurrences:
            if _canonical(occurrence.raw_token) != token.drawing_token:
                problems.append(
                    f"{label}: the drawing token reported is {token.drawing_token!r}, "
                    f"not the matcher's own normalisation of {occurrence.raw_token!r}"
                )

        if token.resolution == EXACT:
            if token.reason is not None:
                problems.append(f"{label}: resolved EXACTLY and still carries a reason")
            if token.offered_row_name is None:
                problems.append(f"{label}: reported EXACT with no catalogue row")
            elif _canonical(token.offered_row_name) != token.drawing_token:
                problems.append(
                    f"{label}: reported EXACT, but the catalogue answered "
                    f"{token.offered_row_name!r} — that is a substitution, not an exact "
                    "section, and the token's own row does not exist"
                )
        elif token.resolution == SUFFIX_FALLBACK:
            if token.reason is not None:
                problems.append(f"{label}: resolved via fallback and still carries a reason")
            if token.offered_row_name is None:
                problems.append(f"{label}: reported SUFFIX_FALLBACK with no offered row")
            else:
                if token.offered_row_is_the_token:
                    problems.append(
                        f"{label}: reported SUFFIX_FALLBACK, but the offered row IS the "
                        "drawing token — an exact hit was reported as a fallback"
                    )
                if "." in token.drawing_token:
                    problems.append(
                        f"{label}: the token already carries a decimal, so the suffix "
                        "route cannot have produced it"
                    )
                if not re.fullmatch(
                    re.escape(token.drawing_token) + r"\.[0-9]", _canonical(token.offered_row_name)
                ):
                    problems.append(
                        f"{label}: the offered row {token.offered_row_name!r} is not a "
                        f'".0"..".9" extension of the drawing token {token.drawing_token!r}'
                    )
        else:
            if token.offered_row_name is not None:
                problems.append(
                    f"{label}: reported NONE but carries the offered row "
                    f"{token.offered_row_name!r} — an unresolved token was given a row"
                )
            if token.reason not in REASONS:
                problems.append(f"{label}: reported NONE with reason {token.reason!r}")

    counts = report.counts_by_resolution
    if sum(counts.values()) != report.raw_token_count:
        problems.append(
            f"the resolution counts {counts} do not account for all "
            f"{report.raw_token_count} distinct tokens"
        )
    reasons = report.counts_by_reason
    if sum(reasons.values()) != counts[NONE]:
        problems.append(
            f"the reason counts {reasons} do not account for all {counts[NONE]} "
            "unresolved tokens"
        )

    measured_cases = {row[0]: row for row in report.known_case_results}
    for case in KNOWN_CASES:
        row = measured_cases.get(case.token)
        if row is None:
            problems.append(f"known case {case.token!r} was never measured by the audit")
            continue
        if row[1] != case.resolution:
            problems.append(
                f"known case {case.token!r} now resolves {row[1]!r}, not the audited "
                f"{case.resolution!r} — the production evidence changed"
            )
        if row[2] != case.offered_row_name:
            problems.append(
                f"known case {case.token!r} is now offered {row[2]!r}, not the audited "
                f"{case.offered_row_name!r} — the catalogue answered differently"
            )
    return tuple(problems)


# ===========================================================================
# Test doubles — the repository boundary, injected (no network, no live table)
# ===========================================================================
class _FakeExecuteResult:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, client):
        self._client = client

    def select(self, columns="*"):
        self._client.queries.append(columns)
        return self

    def execute(self):
        return _FakeExecuteResult(copy.deepcopy(self._client.rows))


class _FakeSupabaseClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def table(self, name):
        return _FakeQuery(self)


class _NetworkGuardClient:
    """Makes 'no live table' structural: an unpatched matcher fails loudly here."""

    def table(self, name):  # pragma: no cover - only on a test bug
        raise AssertionError(
            f"this audit may not touch a live Supabase client (asked for {name!r}); the "
            "rows are injected, and the audit reports only what it was given"
        )


def _live_rows() -> list:
    if not SNAPSHOT_PATH.exists():
        pytest.skip(
            f"the captured live section evidence is not present at {SNAPSHOT_PATH} — "
            "the coverage audit requires the real reference rows"
        )
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256, (
        "the live catalogue capture at /tmp does not match the sha256 this audit pins — "
        "refusing to measure coverage against unverified reference data"
    )
    return json.loads(raw)


# ===========================================================================
# Fixtures
# ===========================================================================
@pytest.fixture(scope="module")
def modules():
    """
    The REAL production modules, imported under a test-only configuration
    boundary (app/config.py reads os.environ at import time), with the same
    restore-and-pop teardown the accepted production tests use — the
    pre-existing SUPABASE_URL smoke-import baseline is neither fixed nor
    worsened.
    """
    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        section_matcher = importlib.import_module("app.engineering_data.section_matcher")
        rules = importlib.import_module("app.validation.rules")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_client = section_matcher.supabase
    section_matcher.supabase = _NetworkGuardClient()
    try:
        yield SimpleNamespace(section_matcher=section_matcher, rules=rules)
    finally:
        section_matcher.supabase = real_client
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def live_rows():
    return _live_rows()


@pytest.fixture(scope="module")
def corpus():
    return build_corpus()


@pytest.fixture()
def real_matcher(modules, live_rows):
    """A genuine SectionMatcher, built by its own constructor against the live rows."""
    saved = modules.section_matcher.supabase
    modules.section_matcher.supabase = _FakeSupabaseClient(copy.deepcopy(live_rows))
    try:
        return modules.section_matcher.SectionMatcher()
    finally:
        modules.section_matcher.supabase = saved


@pytest.fixture()
def build(modules, live_rows, corpus):
    """
    Build the audit against a genuine SectionMatcher, given the stated rows.
    Injecting other rows is how the mutations prove the audit detects changed
    catalogue evidence — the class under test is always the real one.
    """
    def _build(rows=None):
        saved = modules.section_matcher.supabase
        modules.section_matcher.supabase = _FakeSupabaseClient(
            copy.deepcopy(live_rows if rows is None else rows)
        )
        try:
            matcher = modules.section_matcher.SectionMatcher()
        finally:
            modules.section_matcher.supabase = saved
        return build_audit(matcher, corpus, modules.rules)
    return _build


@pytest.fixture(scope="module")
def report(modules, live_rows, corpus):
    """The audit, built once against the genuine matcher and the live rows."""
    saved = modules.section_matcher.supabase
    modules.section_matcher.supabase = _FakeSupabaseClient(copy.deepcopy(live_rows))
    try:
        return build_audit(modules.section_matcher.SectionMatcher(), corpus, modules.rules)
    finally:
        modules.section_matcher.supabase = saved


# ===========================================================================
# The audit runs through the production authority, and only through it
# ===========================================================================
class TestTheAuditRunsThroughTheProductionAuthority:
    def test_the_matcher_is_the_genuine_production_matcher(self, real_matcher):
        assert type(real_matcher).__module__ == PRODUCTION_MATCHER[0]
        assert type(real_matcher).__qualname__ == PRODUCTION_MATCHER[1]
        assert audit_authority_violations(real_matcher) == ()

    def test_the_resolution_vocabulary_is_the_matchers_own(self, report, modules):
        assert modules.section_matcher.RESOLUTIONS == (EXACT, SUFFIX_FALLBACK, NONE)
        every = {t.resolution for t in report.tokens}
        for resolution in (EXACT, SUFFIX_FALLBACK, NONE):
            assert resolution in every

    def test_the_non_steel_predicate_is_the_production_one(self, modules):
        assert is_timber_or_non_steel is modules.rules.is_timber_or_non_steel
        assert modules.rules.is_timber_or_non_steel.__module__ == "app.validation.rules"

    def test_every_unresolved_token_carries_productions_own_classification(self, report, modules):
        """
        The audit's reasons cannot contradict the classification production
        already makes: a token this audit calls NON_STEEL must be
        'non_steel_material' to classify_member, and every other unresolved
        token must be 'unmatched_steel'.
        """
        for token in report.unresolved:
            classified = modules.rules.classify_member(token.raw_token, None)
            if token.reason == REASON_NON_STEEL:
                assert classified == "non_steel_material", token.raw_token
            else:
                assert classified == "unmatched_steel", (token.raw_token, classified)

    def test_the_reference_identity_is_captured_and_is_not_a_version(self, report):
        assert report.reference_source_kind == "LIVE_SUPABASE"
        assert report.reference_identity_status == "UNVERSIONED"
        assert report.reference_data_digest == LIVE_DIGEST
        assert report.catalogue_established is True
        assert len(report.catalogue_rows) == SNAPSHOT_ROW_COUNT

    def test_the_audit_reads_the_reference_rows_and_nothing_else(self, modules, live_rows, corpus):
        client = _FakeSupabaseClient(copy.deepcopy(live_rows))
        saved = modules.section_matcher.supabase
        modules.section_matcher.supabase = client
        try:
            build_audit(modules.section_matcher.SectionMatcher(), corpus, modules.rules)
        finally:
            modules.section_matcher.supabase = saved
        assert client.queries == ["*"]


# ===========================================================================
# The coverage findings — A through K
# ===========================================================================
class TestTheCoverageFindings:
    def test_a_the_corpus_is_the_real_evidence_set(self, report, corpus):
        assert report.occurrence_count == len(corpus) == 219
        assert report.raw_token_count == 86
        assert report.normalised_token_count == 74
        assert {o.source for o in corpus} == set(CAPTURE_FILES) | {SOURCE_ARKLES_WORKLOAD}
        assert all(o.raw_token for o in corpus)

    def test_b_the_counts_by_resolution_account_for_every_token(self, report):
        assert report.counts_by_resolution == {EXACT: 10, SUFFIX_FALLBACK: 7, NONE: 69}
        assert report.counts_by_resolution_normalised == {
            EXACT: 8, SUFFIX_FALLBACK: 6, NONE: 60,
        }
        assert sum(report.counts_by_resolution.values()) == report.raw_token_count

    def test_c_the_exact_tokens_are_exactly_the_catalogues_own_keys(self, report):
        assert [t.raw_token for t in report.exact] == [
            "100PFC", "100x10FL", "130X10FL", "130x10FL", "150PFC", "200PFC",
            "250 PFC", "250PFC", "300PFC", "75x8FL",
        ]
        for token in report.exact:
            assert _canonical(token.offered_row_name) == token.drawing_token
            assert token.reason is None

    def test_d_the_fallback_tokens_and_the_rows_offered(self, report):
        assert [(t.raw_token, t.offered_row_name) for t in report.fallbacks] == [
            ("200UB25", "200UB25.4"),
            ("200UC46", "200UC46.2"),
            ("250UB 25", "250UB25.7"),
            ("250UB 37", "250UB37.3"),
            ("250UB37", "250UB37.3"),
            ("310UB40", "310UB40.4"),
            ("310UB46", "310UB46.2"),
        ]

    def test_d_a_fallback_is_never_the_drawing_token(self, report):
        """
        The semantic the milestone requires the report to state: for every
        fallback the drawing token is NOT the offered catalogue row. A fallback
        is therefore not an exactly supported section, and the audit counts it
        in its own bucket rather than with the exact hits.
        """
        assert report.fallbacks, "an empty fallback bucket would make this vacuous"
        for token in report.fallbacks:
            assert token.offered_row_is_the_token is False
            assert _canonical(token.offered_row_name) != token.drawing_token
            assert (token.offered_row_name or "").upper().startswith(token.drawing_token)

    def test_e_the_unresolved_tokens_grouped_by_reason(self, report):
        assert report.counts_by_reason == {
            REASON_NON_STEEL: 19,
            REASON_NO_DESIGNATION: 8,
            REASON_MALFORMED: 2,
            REASON_UNSUPPORTED_FAMILY: 15,
            REASON_MISSING_ROW: 25,
            REASON_UNKNOWN: 0,
        }
        for token in report.unresolved:
            assert token.offered_row_name is None
            assert token.reason in REASONS

    def test_f_the_family_breakdown_is_measured(self, report):
        assert dict(report.family_breakdown) == {
            "EA": {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 2},
            "FL": {EXACT: 4, SUFFIX_FALLBACK: 0, NONE: 7},
            MULTIPLE_FAMILY_CODES: {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 1},
            NO_FAMILY_CODE: {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 27},
            "PFC": {EXACT: 6, SUFFIX_FALLBACK: 0, NONE: 6},
            "PL": {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 14},
            "SHS": {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 3},
            "UA": {EXACT: 0, SUFFIX_FALLBACK: 0, NONE: 1},
            "UB": {EXACT: 0, SUFFIX_FALLBACK: 6, NONE: 6},
            "UC": {EXACT: 0, SUFFIX_FALLBACK: 1, NONE: 2},
        }
        assert sum(sum(c.values()) for _, c in report.family_breakdown) == report.raw_token_count

    def test_f_the_real_world_family_vocabulary_present_in_this_corpus(self, report):
        """
        Which family vocabulary the genuine workloads actually used, measured
        rather than assumed: this corpus contains no RHS and no CHS token at
        all, so the audit makes no claim about either.
        """
        present = {label for label, _ in report.family_breakdown}
        assert {"UB", "UC", "PFC", "FL", "PL", "EA", "SHS", "UA"} <= present
        assert "RHS" not in present
        assert "CHS" not in present

    def test_g_the_source_breakdown_covers_every_fixture(self, report):
        breakdown = {row[0]: row[1:] for row in report.source_breakdown}
        assert set(breakdown) == set(CAPTURE_FILES) | {SOURCE_ARKLES_WORKLOAD}
        assert breakdown[SOURCE_ARKLES_WORKLOAD] == (
            30, 54, {EXACT: 6, SUFFIX_FALLBACK: 5, NONE: 19},
        )
        assert breakdown["arkles_strand_page_extractions.json"][:2] == (42, 77)
        assert sum(row[2] for row in report.source_breakdown) == report.occurrence_count

    def test_h_the_known_cases(self, report):
        cases = {row[0]: row for row in report.known_cases}
        assert cases["310UB46.2"][1:3] == (EXACT, "310UB46.2")
        assert cases["310UB40"][1:3] == (SUFFIX_FALLBACK, "310UB40.4")
        assert cases["250X90PFC"][1:3] == (NONE, None)
        # Measured in this milestone; the milestone assumed nothing for these two.
        assert cases["200UB25"][1:3] == (SUFFIX_FALLBACK, "200UB25.4")
        assert cases["150X75PFC"][1:3] == (NONE, None)

    def test_h_only_three_known_cases_were_stated_in_advance(self, report):
        assert {row[0] for row in report.known_cases if row[3]} == {
            "310UB46.2", "310UB40", "250X90PFC",
        }

    def test_h_the_milestones_stated_expectations_were_met(self, modules, live_rows):
        """
        The three expectations the milestone named, checked against the genuine
        matcher directly rather than through the corpus — so the check holds
        whether or not a capture happens to contain the token.
        """
        saved = modules.section_matcher.supabase
        modules.section_matcher.supabase = _FakeSupabaseClient(copy.deepcopy(live_rows))
        try:
            matcher = modules.section_matcher.SectionMatcher()
            exact = matcher.resolve("310UB46.2")
            assert (exact.resolution, exact.catalogue_row["name"]) == (EXACT, "310UB46.2")
            fallback = matcher.resolve("310UB40")
            assert (fallback.resolution, fallback.catalogue_row["name"]) == (
                SUFFIX_FALLBACK, "310UB40.4",
            )
            assert matcher.resolve("250X90PFC").resolution == NONE
        finally:
            modules.section_matcher.supabase = saved

    def test_h_310ub46_2_is_not_actually_in_the_captured_corpus(self, report):
        """
        Honest reporting of the evidence: the milestone's exact case is a stated
        production behaviour, not a token any capture produced. What the real
        workload produced is the bare "310UB46", which resolves only through the
        suffix route.
        """
        cases = {row[0]: row for row in report.known_cases}
        assert cases["310UB46.2"][4] is False
        for token in ("310UB40", "250X90PFC", "200UB25", "150X75PFC"):
            assert cases[token][4] is True, token

    def test_i_the_reconciliation_candidates_are_the_supported_family_gaps(self, report):
        candidates = report.reconciliation_candidates
        assert len(candidates) == 25
        unsupported = {family for _, family in report.unsupported_by_production_builders}
        for _, family, row_count, sharing in candidates:
            assert family not in unsupported
            assert row_count > 0
            assert isinstance(sharing, tuple)
        by_token = {t: (f, n, s) for t, f, n, s in candidates}
        # Measured evidence recorded for a later milestone — not a proposal.
        assert by_token["310UB42"] == ("UB", 28, ("310UB32.0", "310UB40.4", "310UB46.2"))
        assert by_token["200UB30"][:2] == ("UB", 28)
        assert by_token["250X90PFC"] == ("PFC", 10, ("250PFC",))
        assert by_token["89x5 SHS"][:2] == ("SHS", 38)
        assert by_token["90x10EA"][:2] == ("EA", 40)
        assert by_token["180x20FL"][2] == ()

    def test_j_the_tokens_the_builders_cannot_support(self, report):
        unsupported = dict(report.unsupported_by_production_builders)
        assert len(unsupported) == 15
        assert set(unsupported.values()) == {"PL", "UA"}
        assert unsupported["64x8PL"] == "PL"
        assert unsupported["150x100x10UA"] == "UA"
        assert report.catalogue_families == (
            "CHS", "EA", "FLAT", "PFC", "RHS", "SHS", "UB", "UC",
        )

    def test_k_the_tokens_needing_human_interpretation(self, report):
        entries = dict(report.needing_human_interpretation)
        assert len(entries) == 29
        assert sum(1 for r in entries.values() if r == REASON_NON_STEEL) == 19
        assert sum(1 for r in entries.values() if r == REASON_NO_DESIGNATION) == 8
        assert sum(1 for r in entries.values() if r == REASON_MALFORMED) == 2
        assert entries["310UB40 & 250 PFC"] == REASON_MALFORMED
        assert entries["200UB30 & 250UB37"] == REASON_MALFORMED
        assert entries["220"] == REASON_NO_DESIGNATION
        assert entries["3/240x45 SG8"] == REASON_NON_STEEL

    def test_the_connection_context_holds_no_section_token(self, report, modules, live_rows):
        """
        Measured, so the audit can state it: the only strings the connection
        level records in a section-like position are plate TYPES, and none of
        them resolves to a section.
        """
        assert report.connection_tokens == ("end_plate", "flange plate", "web plate")
        saved = modules.section_matcher.supabase
        modules.section_matcher.supabase = _FakeSupabaseClient(copy.deepcopy(live_rows))
        try:
            matcher = modules.section_matcher.SectionMatcher()
            for token in report.connection_tokens:
                assert matcher.resolve(token).resolution == NONE
        finally:
            modules.section_matcher.supabase = saved

    def test_the_report_renders_every_section_and_is_deterministic(self, report):
        first = report.printable_summary()
        assert first == report.printable_summary()
        for heading in ("A. distinct", "B. by resolution", "C. EXACT", "D. SUFFIX_FALLBACK",
                        "E. NONE", "F. by family code", "G. by source", "H. known cases",
                        "I. catalogue reconciliation", "J. unsupported", "K. requiring human"):
            assert heading in first, heading
        print("\n" + first)


# ===========================================================================
# The audit cannot silently promote a resolution
# ===========================================================================
class TestTheAuditCannotSilentlyPromoteAResolution:
    def test_the_report_passes_every_integrity_rule(self, report):
        assert audit_violations(report) == ()

    def test_no_fallback_is_counted_as_an_exact_supported_section(self, report):
        exact_tokens = {t.raw_token for t in report.exact}
        for token in report.fallbacks:
            assert token.raw_token not in exact_tokens
        assert report.counts_by_resolution[SUFFIX_FALLBACK] == len(report.fallbacks) == 7

    def test_no_unresolved_token_is_counted_as_resolved(self, report):
        for token in report.unresolved:
            assert token.resolution == NONE
            assert token.offered_row_name is None
        assert report.counts_by_resolution[NONE] == len(report.unresolved) == 69

    def test_the_matchers_own_record_refuses_an_exact_without_a_row(self, modules):
        """
        The production record itself cannot express "EXACT with nothing behind
        it", which is why this audit does not rely on the matcher alone: it also
        checks the offered row against the token, so an EXACT carrying the WRONG
        row is caught even though such a record is perfectly constructible.
        """
        constructor = modules.section_matcher.SectionMatch
        with pytest.raises(ValueError):
            constructor(
                drawing_token="250X90PFC", drawing_token_raw="250X90PFC",
                catalogue_row=None, resolution=EXACT,
            )
        with pytest.raises(ValueError):
            constructor(
                drawing_token="250PFC", drawing_token_raw="250PFC",
                catalogue_row=None, resolution=SUFFIX_FALLBACK,
            )

    def test_the_audit_is_reproducible(self, build, report):
        again = build()
        assert [t.raw_token for t in again.tokens] == [t.raw_token for t in report.tokens]
        assert again.counts_by_resolution == report.counts_by_resolution
        assert again.counts_by_reason == report.counts_by_reason
        assert again.printable_summary() == report.printable_summary()
        assert again.reference_data_digest == report.reference_data_digest


# ===========================================================================
# The mutations — each one must break the audit, or the audit proves nothing
# ===========================================================================
def _promote(matcher_module, from_resolution, to_resolution, row=None):
    """Relabel one resolution as another, through the matcher's own record."""
    real = matcher_module.SectionMatcher.resolve

    def promoted(self, raw_name):
        outcome = real(self, raw_name)
        if outcome.resolution == from_resolution:
            return matcher_module.SectionMatch(
                drawing_token=outcome.drawing_token,
                drawing_token_raw=outcome.drawing_token_raw,
                catalogue_row=(outcome.catalogue_row if row is None else row),
                resolution=to_resolution,
            )
        return outcome

    return promoted


def _reporting_lie(module, from_resolution, to_resolution):
    """
    The audit's OWN reporting layer, forced to relabel a resolution. This is
    the other half of M1/M2: those prove the audit catches a matcher that
    promotes a row, and these prove the audit's findings and integrity rules
    fail if the REPORT says EXACT where the matcher said otherwise.
    """
    real = module._audit_token

    def lied(matcher, raw_token, catalogue_families):
        token = real(matcher, raw_token, catalogue_families)
        if token.resolution == from_resolution:
            return replace(token, resolution=to_resolution)
        return token

    return lied


class TestMutations:
    """
    Every mutation is applied to a throwaway copy or to the class for the
    duration of one test, and the fixture below proves the working tree was not
    touched: the production modules and the captured fixtures are hashed around
    each test.
    """
    @pytest.fixture(autouse=True)
    def production_tree_is_frozen(self):
        before = _tree_digests()
        yield
        assert _tree_digests() == before, (
            "a mutation test changed a file in the working tree — mutation testing must "
            "never touch the genuine sources or the captured evidence"
        )

    def test_m1_forcing_a_fallback_to_report_as_exact_is_caught(
        self, modules, monkeypatch, build, report
    ):
        assert report.counts_by_resolution[SUFFIX_FALLBACK] == 7
        monkeypatch.setattr(
            modules.section_matcher.SectionMatcher,
            "resolve",
            _promote(modules.section_matcher, SUFFIX_FALLBACK, EXACT),
        )
        mutated = build()
        violations = audit_violations(mutated)
        assert violations, "a fallback reported as EXACT went undetected"
        assert mutated.counts_by_resolution[SUFFIX_FALLBACK] == 0
        assert any("that is a substitution, not an exact section" in v for v in violations)

    def test_m2_forcing_none_to_report_as_exact_is_caught(
        self, modules, monkeypatch, build, report
    ):
        assert report.counts_by_resolution[NONE] == 69
        row = {"name": "250PFC", "family": "PFC"}
        monkeypatch.setattr(
            modules.section_matcher.SectionMatcher,
            "resolve",
            _promote(modules.section_matcher, NONE, EXACT, row=row),
        )
        mutated = build()
        violations = audit_violations(mutated)
        assert violations, "a NONE reported as EXACT went undetected"
        assert mutated.counts_by_resolution[NONE] == 0
        assert any("that is a substitution, not an exact section" in v for v in violations)

    def test_m2b_a_none_promoted_to_a_fallback_is_also_caught(
        self, modules, monkeypatch, build
    ):
        row = {"name": "250PFC", "family": "PFC"}
        monkeypatch.setattr(
            modules.section_matcher.SectionMatcher,
            "resolve",
            _promote(modules.section_matcher, NONE, SUFFIX_FALLBACK, row=row),
        )
        assert audit_violations(build()), "a NONE reported as a suffix fallback went undetected"

    def test_m1_the_audits_own_findings_fail_if_a_fallback_reports_as_exact(
        self, monkeypatch, build, report
    ):
        """M1 at the reporting layer: every audit assertion about that bucket fails."""
        module = sys.modules[__name__]
        monkeypatch.setattr(
            module, "_audit_token", _reporting_lie(module, SUFFIX_FALLBACK, EXACT)
        )
        mutated = build()
        assert mutated.counts_by_resolution[SUFFIX_FALLBACK] == 0
        assert mutated.counts_by_resolution != report.counts_by_resolution
        assert mutated.counts_by_resolution != {EXACT: 10, SUFFIX_FALLBACK: 7, NONE: 69}
        assert audit_violations(mutated)

    def test_m2_the_audits_own_findings_fail_if_a_none_reports_as_exact(
        self, monkeypatch, build, report
    ):
        """M2 at the reporting layer: an unresolved token reported as an exact hit."""
        module = sys.modules[__name__]
        monkeypatch.setattr(module, "_audit_token", _reporting_lie(module, NONE, EXACT))
        mutated = build()
        assert mutated.counts_by_resolution[NONE] == 0
        assert mutated.counts_by_resolution != report.counts_by_resolution
        assert mutated.counts_by_resolution != {EXACT: 10, SUFFIX_FALLBACK: 7, NONE: 69}
        assert audit_violations(mutated)

    def test_m3_a_proof_only_local_matcher_is_refused_by_the_authority_guard(
        self, modules, corpus
    ):
        """
        The audit refuses to run against anything but the production matcher.
        app.engineering_data.section_catalogue.CatalogueMatcher is the
        proof-only local engine: it has no resolve() at all — so it cannot
        report WHICH route produced a row — and it declares a catalogue VERSION
        where the live source is formally UNVERSIONED. Two different datasets
        answering two different questions; auditing coverage through it would
        report a coverage production does not have, so the audit raises.
        """
        local_module = importlib.import_module("app.engineering_data.section_catalogue")
        local = local_module.CatalogueMatcher()
        assert type(local).__module__ != PRODUCTION_MATCHER[0]
        assert not hasattr(local, "resolve")
        assert local.catalogue_version == local_module.CATALOGUE_VERSION
        problems = audit_authority_violations(local)
        assert problems
        assert any("not the production" in p for p in problems)
        assert any("no resolve()" in p for p in problems)
        with pytest.raises(AuditAuthorityError):
            build_audit(local, corpus, modules.rules)

    def test_m4_changing_the_offered_row_for_310ub40_is_detected(self, build, live_rows):
        """
        The catalogue answers 310UB40 with 310UB40.4. Rename only that row and
        the audit's known-case evidence must notice — the offered row is recorded
        evidence, not an incidental detail.
        """
        baseline = build()
        assert _measure_token(baseline.tokens, "310UB40").offered_row_name == "310UB40.4"

        doctored = copy.deepcopy(live_rows)
        for row in doctored:
            if row.get("name") == "310UB40.4":
                row["name"] = "310UB40.9"
        mutated = build(rows=doctored)
        assert _measure_token(mutated.tokens, "310UB40").offered_row_name == "310UB40.9"
        violations = audit_violations(mutated)
        assert any("310UB40" in v and "310UB40.9" in v for v in violations), violations

    def test_m4b_removing_the_offered_row_is_detected(self, build, live_rows):
        doctored = [row for row in copy.deepcopy(live_rows) if row.get("name") != "310UB40.4"]
        assert len(doctored) == SNAPSHOT_ROW_COUNT - 1
        mutated = build(rows=doctored)
        assert _measure_token(mutated.tokens, "310UB40").resolution == NONE
        assert any("310UB40" in v for v in audit_violations(mutated))

    def test_m4c_adding_an_exact_row_for_310ub40_is_also_detected(self, build, live_rows):
        """
        The known-case check is not one-way: a genuine IMPROVEMENT in the
        catalogue is reported too, because the audited evidence is pinned and any
        change to it is a change this audit must be re-run against.
        """
        doctored = copy.deepcopy(live_rows)
        doctored.append({"name": "310UB40", "family": "UB", "depth": 310, "weight_per_metre": 40.0})
        mutated = build(rows=doctored)
        assert _measure_token(mutated.tokens, "310UB40").resolution == EXACT
        assert audit_violations(mutated), "a changed known case went undetected"

    def test_the_control_run_passes_every_rule(self, build):
        assert audit_violations(build()) == ()

    def test_the_working_tree_is_unchanged_by_this_module(self):
        """The read-only claim, stated as evidence rather than as a promise."""
        assert _tree_digests() == _FROZEN_TREE_AT_IMPORT


# ===========================================================================
# Explicit edge cases — synthetic tokens, and clearly marked as such
# ===========================================================================
class TestTheEdgeCasesTheRealCorpusDoesNotContain:
    """
    The coverage report above contains only real tokens. These cases use
    synthetic tokens deliberately, because the real corpus does not exercise
    them and the audit's behaviour on them still has to be defined.
    """
    def test_an_unestablished_catalogue_yields_unknown_reason_not_a_guess(self):
        """
        The declining branch. With no established catalogue the audit cannot
        tell "unsupported family" from "missing row", so it reports neither.
        """
        assert reason_for_unresolved("999UB99", "UB", None, False) == REASON_UNKNOWN
        assert reason_for_unresolved("999PL99", "PL", None, False) == REASON_UNKNOWN
        # ... while the evidence-based reasons still apply, in their own order.
        assert reason_for_unresolved("999UB99", "UB", None, True) == REASON_NON_STEEL
        assert reason_for_unresolved("S101", None, None, False) == REASON_NO_DESIGNATION

    def test_a_synthetic_supported_family_gap_is_a_missing_row(self):
        families = frozenset({"UB", "UC", "PFC", "FLAT"})
        assert reason_for_unresolved("999UB99", "UB", families, False) == REASON_MISSING_ROW
        assert reason_for_unresolved("999PL99", "PL", families, False) == REASON_UNSUPPORTED_FAMILY

    def test_a_synthetic_compound_token_is_ambiguous_whatever_its_family(self):
        families = frozenset({"UB", "PFC"})
        assert reason_for_unresolved("310UB40 & 250 PFC", "UB", families, False) == REASON_MALFORMED
        assert reason_for_unresolved("250UB37, 310UB40", "UB", families, False) == REASON_MALFORMED
        assert reason_for_unresolved("310UB40 AND 250UB37", "UB", families, False) == REASON_MALFORMED
        # Two different family codes with no explicit separator are ambiguous too.
        assert _is_compound("310UB40PFC") is True
        assert _token_family_code("310UB40PFC") is None

    def test_a_synthetic_family_code_that_the_catalogue_never_carries(self):
        assert _token_family_code("150x100x10UA") == "UA"
        assert _token_family_code("133x10PL") == "PL"
        assert _token_family_code("130x10FL") == "FL"
        assert CATALOGUE_FAMILY_OF_CODE["FL"] == "FLAT"
        assert _token_family_code("220") is None
        assert _token_family_code("300FC") is None

    def test_the_audits_normalisation_is_the_matchers_own(self, report, real_matcher):
        for token in report.tokens:
            assert real_matcher._normalise(token.raw_token) == token.drawing_token
            assert _canonical(token.raw_token) == token.drawing_token


# ===========================================================================
# The read-only guard
# ===========================================================================
def _tree_digests() -> dict:
    digests = {}
    for path in sorted(REPO.joinpath("app").rglob("*.py")):
        digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(CAPTURE_DIR.glob("*.json")):
        digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


_FROZEN_TREE_AT_IMPORT = _tree_digests()
