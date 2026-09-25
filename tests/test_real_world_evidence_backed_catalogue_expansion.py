"""
Step G — EVIDENCE-BACKED CATALOGUE EXPANSION (catalogue preparation only).

The milestone after Step F: a VERY SMALL, EVIDENCE-BACKED catalogue expansion in
which ONLY the six candidates Step F classified class A may be added, the six raw
spellings may not become six engineering identities, every property must be
explicitly evidenced, and production must NOT be switched to the local catalogue.

WHAT THIS FILE FOUND, BEFORE WRITING ANY CODE
Step F's six class-A spellings are

    130X12FL   130x12FL   180X20FL   180x20FL   250X12FL   90x10FL

and the accepted local catalogue (Milestones C, D, E1) ALREADY holds exactly the
four canonical rows those spellings resolve to, with exactly the properties the
evidence states:

    130X12FL  family FL  width 130  thickness 12               (Milestone D)
    180X20FL  family FL  width 180  thickness 20  weight 28.3  (Milestone C)
    250X12FL  family PL  width 250  thickness 12  weight 23.6  (Milestone C)
    90X10FL   family FL  width  90  thickness 10               (Milestone D)

Step F classified those six A *because* those accepted rows state the same values
the drawings print — so the expansion set is exactly this and nothing else, and
the honest Step G modification set is EMPTY: no catalogue row is added, no
production file is touched. What this milestone can and does deliver is the proof
that the expansion is exactly right and can be no larger:

  * the six spellings collapse to the four canonical identities through the
    catalogue's OWN normalisation (case- and space-insensitive) — a lowercase
    spelling is not a catalogue name and cannot become a second row (Req 3, 4);
  * every property of every row is explicitly evidenced, per property, by the
    drawing's own printed evidence or by an accepted milestone record, with the
    evidence string naming the real source rather than a fresh constant;
  * no weight and no dimension is calculated, restated or borrowed: the measured
    derived restatements (180X20FL 28.235 against the recorded 28.3, 250X12FL
    23.87 against the recorded 23.6) are named here only so that a test can prove
    the rows do not carry them (Req 6, 7, 8, 11);
  * the 19 other Step-F candidates stay out, 250X90PFC / 150X75PFC / 200UB30 /
    310UB42 explicitly and the rest by class (Req 9, 14, 15, 16, 17);
  * the accepted catalogue, its E1-enriched rows and the E1 conflict record
    (250X90PFC against the live 250PFC) are unchanged to the byte (Req 10, 13);
  * production is NOT redirected: the genuine production SectionMatcher still
    resolves all six spellings to NONE while the local catalogue resolves them,
    and the five named production authority files name no catalogue construct
    (Req 18).

WHAT THIS FILE DOES NOT DO
It adds no row, edits no catalogue file, modifies no production file, no Supabase
schema or data, no capture data and no Jev module. A row being present in the
local catalogue is NOT a production claim: nothing here is production-resolvable,
and this milestone does not perform the production catalogue switch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from tests import test_real_world_production_section_evidence_reconciliation_audit as step_f
from tests import test_section_catalogue as accepted_catalogue_proof

# The Step F fixtures this milestone consumes (imported so pytest resolves the
# whole chain): the evidence layer is Step F's measured audit, never re-measured.
from tests.test_real_world_production_section_evidence_reconciliation_audit import (  # noqa: F401
    audit,
    corpus,
    live_rows,
    modules,
    real_matcher,
    step_e_report,
)

REPO = step_f.REPO
CLASS_A = step_f.CLASS_EXACTLY_SUPPORTED

# ===========================================================================
# The milestone's approved set, and the identities it must resolve to
# ===========================================================================
# The six class-A spellings, exactly as the milestone names them. This is the
# ONLY set the expansion may contain; the gate re-derives their class from Step F
# so that a change in the evidence changes the set.
APPROVED_SPELLINGS = (
    "130X12FL", "130x12FL", "180X20FL", "180x20FL", "250X12FL", "90x10FL",
)

# The four canonical catalogue identities those six spellings resolve to. The two
# lowercase spellings are NOT identities: they resolve to the upper-case row.
IDENTITY_OF_SPELLING = {
    "130X12FL": "130X12FL",
    "130x12FL": "130X12FL",
    "180X20FL": "180X20FL",
    "180x20FL": "180X20FL",
    "250X12FL": "250X12FL",
    "90x10FL": "90X10FL",
}
CANONICAL_IDENTITIES = ("130X12FL", "180X20FL", "250X12FL", "90X10FL")

# The accepted catalogue's own canonical naming convention for plate rows:
# <width>X<thickness><FAMILY>, upper case. Derived from the catalogue by a test.
CANONICAL_NAME_PATTERN = re.compile(r"^[0-9]+X[0-9]+(?:FL|PL)$")

# The accepted family of each identity — the captured family, exactly as the
# accepted Milestone C/D record carries it (250X12FL is the Milestone C row the
# genuine Selby page-1 chain resolved, and is recorded as PL there).
ACCEPTED_FAMILY = {
    "130X12FL": "FL",
    "180X20FL": "FL",
    "250X12FL": "PL",
    "90X10FL": "FL",
}

# The accepted row of each identity, to the key and the value (Req 5, 12).
PINNED_ROWS = {
    "130X12FL": {"name": "130X12FL", "family": "FL", "width": 130.0, "thickness": 12.0},
    "180X20FL": {"name": "180X20FL", "family": "FL", "width": 180.0,
                 "thickness": 20.0, "weight_per_metre": 28.3},
    "250X12FL": {"name": "250X12FL", "family": "PL", "width": 250.0,
                 "thickness": 12.0, "weight_per_metre": 23.6},
    "90X10FL": {"name": "90X10FL", "family": "FL", "width": 90.0, "thickness": 10.0},
}

# Presentation order for a row's properties (deterministic, not alphabetical).
PRESENTATION_ORDER = ("family", "width", "thickness", "weight_per_metre")

# ===========================================================================
# The accepted catalogue state this milestone must not move
# ===========================================================================
ACCEPTED_CATALOGUE_MODULE = step_f.LOCAL_CATALOGUE_MODULE
ACCEPTED_CATALOGUE_DIGEST = step_f.LOCAL_CATALOGUE_DIGEST
ACCEPTED_CATALOGUE_VERSION = f"local-section-catalogue@{ACCEPTED_CATALOGUE_DIGEST[:16]}"
ACCEPTED_CATALOGUE_ROW_COUNT = 32
ACCEPTED_CATALOGUE_FILE = "app/engineering_data/section_catalogue.py"
ACCEPTED_CATALOGUE_FILE_SHA256 = (
    "eddc9609cc8d69f933f31015aecb4115b4c5cc09a6a1e1b4ba5d45784311c5f4"
)
E1_ENRICHED_ROWS = (
    "100PFC", "150PFC", "200PFC", "250UB25.7", "250UB37.3", "300PFC", "310UB46.2",
)

# The E1 conflict record: the accepted capture-side 250X90PFC row and the live
# 250PFC row disagree on exactly these fields, and that must not change.
E1_CONFLICT_TOKEN = "250X90PFC"
E1_CONFLICT_FIELDS = (
    ("flange_thickness", 15.0, 12.0),
    ("web_thickness", 8.0, 7.0),
)
LIVE_250PFC_ROW = {
    "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
}
EXCLUDED_PRESENT_ROWS = {
    "250X90PFC": {"name": "250X90PFC", "family": "PFC", "depth": 250.0,
                  "flange_width": 90.0, "flange_thickness": 15.0,
                  "web_thickness": 8.0, "weight_per_metre": 35.5},
    "90X10EA": {"name": "90X10EA", "family": "EA", "width": 90.0,
                "thickness": 10.0, "weight_per_metre": 13.3},
    "300X8FL": {"name": "300X8FL", "family": "FL", "width": 300.0, "thickness": 8.0},
    "200UB30.4": {"name": "200UB30.4", "family": "UB", "weight_per_metre": 30.4},
}

# ===========================================================================
# The production authority files this milestone must not redirect
# ===========================================================================
PRODUCTION_AUTHORITY_FILES = (
    "app/engineering_data/section_matcher.py",
    "app/pipeline.py",
    "app/drawing_reading/dxf_parser.py",
    "app/cad_engine/real_member_adapter.py",
    "app/validation/rules.py",
)
CATALOGUE_CONSTRUCT_PATTERN = re.compile(
    r"section_catalogue|CatalogueMatcher|CATALOGUE(?:_[A-Z]+)?"
)
JEV_BOUNDARY_FILE = "app/cad_engine/jev_contract_discovery.py"

# The scope guard: the intended modification sets. Production code, Supabase data
# and capture data all live under app/ and tests/data/, which Step F's tree digest
# freezes; the one intended new file is this module.
EXPANSION_FILE = "tests/test_real_world_evidence_backed_catalogue_expansion.py"
INTENDED_PRODUCTION_MODIFICATIONS: tuple[str, ...] = ()
INTENDED_SUPABASE_MODIFICATIONS: tuple[str, ...] = ()
INTENDED_CAPTURE_DATA_MODIFICATIONS: tuple[str, ...] = ()
INTENDED_JEV_MODIFICATIONS: tuple[str, ...] = ()
INTENDED_NEW_FILES = (EXPANSION_FILE,)

# ===========================================================================
# The refusal vocabulary (fixed, asserted by every mutation test)
# ===========================================================================
REASON_NOT_A_STEP_F_CANDIDATE = "NOT_A_STEP_F_CANDIDATE"
REASON_NOT_APPROVED_BY_THE_MILESTONE = "NOT_APPROVED_BY_THE_MILESTONE"
REASON_NOT_CLASS_A = "NOT_CLASS_A"
REASON_NO_CANONICAL_IDENTITY = "NO_CANONICAL_IDENTITY"
REASON_PROPERTY_HAS_NO_EVIDENCE = "PROPERTY_HAS_NO_EVIDENCE"
REASON_VALUE_DISAGREES_WITH_EVIDENCE = "VALUE_DISAGREES_WITH_EVIDENCE"
EXPANSION_REFUSAL_REASONS = (
    REASON_NOT_A_STEP_F_CANDIDATE,
    REASON_NOT_APPROVED_BY_THE_MILESTONE,
    REASON_NOT_CLASS_A,
    REASON_NO_CANONICAL_IDENTITY,
    REASON_PROPERTY_HAS_NO_EVIDENCE,
    REASON_VALUE_DISAGREES_WITH_EVIDENCE,
)
EXCLUDED_BY_CLASS = {
    step_f.CLASS_PARTIAL: ("200UB30", "300x8FL"),
    step_f.CLASS_CONFLICTING: ("250X90PFC", "250x90PFC"),
    step_f.CLASS_NO_EVIDENCE: (
        "110UB48", "290UB 37", "310UB42", "23UB37", "250UC46", "1200PFC", "259PFC",
    ),
    step_f.CLASS_ALREADY_COVERED: (
        "150X75PFC", "150x75PFC", "75x6.0SHS", "89x5 SHS", "89x89 SHS",
        "90X10EA", "90x10EA", "200UC",
    ),
}


# ===========================================================================
# The gate: one spelling -> one evidenced canonical row, or a refusal
# ===========================================================================
def _normalise(name: str) -> str:
    """The accepted catalogue's own normalisation, imported, never restated."""
    return step_f._normalise(name)


# The accepted catalogue's own non-quantity fields: the family code and the E1
# provenance label. Every OTHER field of a catalogue row is a measured quantity.
NON_QUANTITY_FIELDS = ("family", "provenance")


def catalogue_integrity_violations(
        catalogue: Mapping[str, Mapping[str, Any]],
        provenance_kinds: tuple[str, ...] = ()) -> tuple[str, ...]:
    """
    Structural defects that would make an expansion ambiguous, measured over the
    catalogue handed in:

      * a key that is not the row's own name (the key IS the identity);
      * two keys that normalise to one engineering identity — different
        capitalisation of one section may never become two rows (Req 3);
      * a row with no engineering property at all, or a quantity field that is not
        a number (a computed string, a note, a label);
      * when the accepted provenance vocabulary is supplied, a provenance label
        outside it — a new provenance claim may not be invented (Req 12).
    """
    violations: list[str] = []
    identity_of_key: dict[str, str] = {}
    for name, row in catalogue.items():
        if row.get("name") != name:
            violations.append(
                f"row {name!r} carries name {row.get('name')!r} — the key is the identity"
            )
        identity = _normalise(name)
        if identity in identity_of_key:
            violations.append(
                f"keys {identity_of_key[identity]!r} and {name!r} are one engineering "
                f"identity ({identity!r}): case/format variants may not become two rows"
            )
        else:
            identity_of_key[identity] = name
        properties = {key for key in row if key != "name"}
        if not properties:
            violations.append(f"row {name!r} carries no engineering property at all")
        for field in sorted(properties):
            value = row[field]
            if field in NON_QUANTITY_FIELDS:
                if not isinstance(value, str) or not value:
                    violations.append(
                        f"row {name!r} field {field!r} is not a non-empty label ({value!r})")
                elif field == "provenance" and provenance_kinds and value not in provenance_kinds:
                    violations.append(
                        f"row {name!r} claims provenance {value!r}, which is not one of the "
                        f"accepted vocabulary {provenance_kinds}")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                violations.append(
                    f"row {name!r} field {field!r} is not a measured number ({value!r})")
    return tuple(violations)


def canonical_key(catalogue: Mapping[str, Mapping[str, Any]], spelling: str) -> str | None:
    """
    The catalogue's OWN key for this spelling: exact normalised equality only. A
    '.0'..'.9' suffix fallback is not an identity — it is a match to a different
    designation — so it is never accepted here. This is why 200UB30 cannot become
    a row merely because the weight-only 200UB30.4 exists.
    """
    wanted = _normalise(spelling)
    for name in catalogue:
        if _normalise(name) == wanted:
            return name
    return None


def catalogue_digest(catalogue: Mapping[str, Mapping[str, Any]]) -> str:
    """The accepted catalogue module's own documented digest recipe, re-derived."""
    rows = {name: dict(row) for name, row in catalogue.items()}
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PropertyFinding:
    """
    One property of one candidate row and the evidence for it. `agreed` holds the
    real evidence strings that state this value; `disagreed` holds real evidence
    strings that name the same field with a different value. A property with no
    agreeing evidence was not evidenced; a property with any disagreeing evidence
    is ambiguous, and ambiguity fails closed.
    """
    field: str
    value: Any
    agreed: tuple[str, ...]
    disagreed: tuple[str, ...]

    @property
    def is_evidenced(self) -> bool:
        return bool(self.agreed) and not self.disagreed

    def render(self) -> str:
        if self.disagreed:
            return (f"{self.field} = {self.value!r} REFUSED — evidence disagrees: "
                    + "; ".join(self.disagreed))
        if not self.agreed:
            return f"{self.field} = {self.value!r} REFUSED — no evidence states this property"
        return f"{self.field} = {self.value!r} <- " + "; ".join(self.agreed)


@dataclass(frozen=True)
class Refusal:
    """One requested spelling the expansion refused, with every reason it found."""
    spelling: str
    reasons: tuple[str, ...]
    detail: tuple[str, ...]

    def render(self) -> str:
        return (f"{self.spelling!r} REFUSED [{', '.join(self.reasons)}] "
                + "; ".join(self.detail))


@dataclass(frozen=True)
class ExpansionEntry:
    """One approved spelling, its ONE canonical identity, and its evidence."""
    spelling: str
    canonical_name: str
    classification: str
    properties: tuple[tuple[str, Any], ...]
    findings: tuple[PropertyFinding, ...]
    derived_restatements: tuple[tuple[str, float], ...]

    @property
    def row(self) -> dict:
        return {"name": self.canonical_name, **dict(self.properties)}

    @property
    def weight_finding(self) -> PropertyFinding | None:
        return self.finding("weight_per_metre")

    @property
    def carries_weight(self) -> bool:
        return self.weight_finding is not None

    def finding(self, field: str) -> PropertyFinding | None:
        for finding in self.findings:
            if finding.field == field:
                return finding
        return None

    def render(self) -> str:
        lines = [
            f"- {self.spelling}  ->  {self.canonical_name}  [Step F {self.classification}]"
        ]
        for finding in self.findings:
            lines.append(f"    {finding.render()}")
        for label, value in self.derived_restatements:
            lines.append(f"    ({label} = {value!r} — measured, derived, NOT adopted here)")
        return "\n".join(lines)


@dataclass(frozen=True)
class ExpansionPlan:
    """
    The expansion: one entry per approved spelling, the refusals for anything
    requested but not added, and the integrity of the catalogue it was built
    against. An ambiguous catalogue yields no entries at all.
    """
    entries: tuple[ExpansionEntry, ...]
    refusals: tuple[Refusal, ...]
    integrity_violations: tuple[str, ...]
    catalogue_size: int
    catalogue_digest: str

    @property
    def is_buildable(self) -> bool:
        return not self.integrity_violations

    @property
    def spellings(self) -> tuple[str, ...]:
        return tuple(entry.spelling for entry in self.entries)

    @property
    def identities(self) -> tuple[str, ...]:
        return tuple(sorted({entry.canonical_name for entry in self.entries}))

    def entry_for(self, spelling: str) -> ExpansionEntry:
        for entry in self.entries:
            if entry.spelling == spelling:
                return entry
        raise KeyError(spelling)

    def refusal_for(self, spelling: str) -> Refusal | None:
        for refusal in self.refusals:
            if refusal.spelling == spelling:
                return refusal
        return None

    def summary(self) -> str:
        return (f"{len(self.entries)} approved spellings -> {len(self.identities)} canonical "
                f"identities; {len(self.refusals)} refusals; catalogue "
                f"{self.catalogue_size} rows @ {self.catalogue_digest[:16]}")

    def render(self) -> str:
        lines = [
            "=" * 78,
            "EVIDENCE-BACKED CATALOGUE EXPANSION (Step G, preparation only)",
            "=" * 78,
            self.summary(),
            "integrity: " + (", ".join(self.integrity_violations) if self.integrity_violations
                             else f"ok ({self.catalogue_size} rows, one identity each)"),
            "This plan adds nothing to production: the rows it points to are the accepted "
            "Milestone C/D catalogue rows, and production still resolves through the live "
            "Supabase-backed SectionMatcher.",
            "",
        ]
        for entry in self.entries:
            lines.append(entry.render())
        for refusal in self.refusals:
            lines.append(refusal.render())
        return "\n".join(lines)


def _drawing_statements(facts) -> dict[str, tuple[tuple[float, str], ...]]:
    """field -> ((value, evidence), ...) for what the DRAWING states for this token."""
    statements: dict[str, list] = {}
    for stated in facts.stated_values:
        if stated.provenance in step_f.PRINTED_PROVENANCES + step_f.CAPTURE_PROVENANCES:
            statements.setdefault(stated.field, []).append(
                (stated.value, f"{stated.provenance}: {stated.evidence}")
            )
    return {field: tuple(items) for field, items in statements.items()}


def _accepted_record_statements(facts) -> dict[str, tuple[tuple[float, str], ...]]:
    """
    field -> ((value, evidence), ...) for what an ACCEPTED record records. The
    evidence string is Step F's own prior-row evidence verbatim — it names the
    milestone and the row — so every citation points at real repository evidence
    instead of at a constant repeated here.
    """
    prior = [stated.evidence for stated in facts.stated_values
             if stated.provenance == step_f.FROM_PRIOR_MILESTONE]
    label = prior[0] if prior else "an accepted prior-milestone row"
    statements: dict[str, list] = {}
    for _name, fields in facts.accepted_row_fields:
        for field, value in fields:
            statements.setdefault(field, []).append(
                (value, f"{label} records {field}={value:g}")
            )
    return {field: tuple(items) for field, items in statements.items()}


def _family_statements(canonical_name: str, family: str) -> tuple[tuple[str, str], ...]:
    """
    The accepted record's own family for a row. The evidence for a row's family is
    the accepted catalogue row itself; for the four identities this milestone
    approves, the pinned ACCEPTED_FAMILY value is a second, independent statement —
    so a catalogue whose family was changed is refused rather than accepted.
    """
    statements = [(family, f"the accepted catalogue row {canonical_name} records "
                           f"family {family!r}")]
    pinned = ACCEPTED_FAMILY.get(canonical_name)
    if pinned is not None:
        statements.append((pinned, f"the accepted milestone record {canonical_name} records "
                                   f"family {pinned!r}"))
    return tuple(statements)


def _ordered_properties(row: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    fields = [field for field in PRESENTATION_ORDER if field in row]
    fields += sorted(field for field in row
                     if field not in PRESENTATION_ORDER and field != "name")
    return tuple((field, row[field]) for field in fields)


def _findings(facts, canonical_name: str, row: Mapping[str, Any]) -> tuple[PropertyFinding, ...]:
    """Every property of a candidate row, with the real evidence for it."""
    drawing = _drawing_statements(facts)
    recorded = _accepted_record_statements(facts)
    findings: list[PropertyFinding] = []
    for field, value in _ordered_properties(row):
        if field == "family":
            statements = _family_statements(canonical_name, value)
        else:
            statements = drawing.get(field, ()) + recorded.get(field, ())
        agreed = tuple(text for candidate, text in statements if candidate == value)
        disagreed = tuple(text for candidate, text in statements if candidate != value)
        findings.append(PropertyFinding(field, value, agreed, disagreed))
    return tuple(findings)


def _derived_restatements(facts) -> tuple[tuple[str, float], ...]:
    """The measured derived restatements Step F computed for this token's prints."""
    return tuple((label, value) for label, value, _detail in facts.derived_values
                 if label == step_f.DERIVED_RESTATEMENT)


def _candidate_facts(audit_obj, spelling: str):
    try:
        return audit_obj.by_token(spelling).facts
    except KeyError:
        return None


def build_expansion(audit_obj, catalogue: Mapping[str, Mapping[str, Any]],
                    spellings: tuple[str, ...] = APPROVED_SPELLINGS,
                    provenance_kinds: tuple[str, ...] = ()) -> ExpansionPlan:
    """
    Build the evidence-backed expansion for the requested spellings.

    The evidence layer is Step F's MEASURED audit; the catalogue is the thing being
    verified. Nothing is written: the plan is immutable data, and the rows it
    points at are the accepted catalogue's own rows. Checks are collected rather
    than short-circuited, so a refusal names every reason it found; ambiguous
    evidence fails closed.
    """
    integrity = catalogue_integrity_violations(catalogue, provenance_kinds)
    digest = catalogue_digest(catalogue)
    if integrity:
        return ExpansionPlan(
            entries=(), refusals=(), integrity_violations=integrity,
            catalogue_size=len(catalogue), catalogue_digest=digest,
        )

    entries: list[ExpansionEntry] = []
    refusals: list[Refusal] = []
    for spelling in spellings:
        facts = _candidate_facts(audit_obj, spelling)
        if facts is None:
            refusals.append(Refusal(
                spelling,
                (REASON_NOT_A_STEP_F_CANDIDATE,),
                (f"{spelling!r} is not one of Step F's measured candidates",),
            ))
            continue

        reasons: list[str] = []
        detail: list[str] = []
        classification = step_f.classify(facts)
        if spelling not in APPROVED_SPELLINGS:
            reasons.append(REASON_NOT_APPROVED_BY_THE_MILESTONE)
            detail.append(
                f"{spelling!r} is not one of the approved class-A spellings this milestone "
                f"may add; Step F measured it {classification}"
            )
        if classification != CLASS_A:
            reasons.append(REASON_NOT_CLASS_A)
            detail.append(
                f"Step F classified {spelling!r} {classification} "
                f"({step_f.CLASSIFICATION_LETTERS[classification]}) — only {CLASS_A} "
                "may be added"
            )

        canonical = canonical_key(catalogue, spelling)
        if canonical is None:
            reasons.append(REASON_NO_CANONICAL_IDENTITY)
            detail.append(
                f"the accepted catalogue holds no exact canonical identity for {spelling!r} "
                "(a '.0'..'.9' suffix fallback is a match to a different designation, not "
                "an identity)"
            )
            refusals.append(Refusal(spelling, tuple(reasons), tuple(detail)))
            continue

        row = catalogue[canonical]
        findings = _findings(facts, canonical, row)
        for finding in findings:
            if finding.disagreed:
                reasons.append(REASON_VALUE_DISAGREES_WITH_EVIDENCE)
                detail.append(
                    f"{canonical} {finding.field} = {finding.value!r} disagrees with "
                    + "; ".join(finding.disagreed)
                )
            elif not finding.agreed:
                reasons.append(REASON_PROPERTY_HAS_NO_EVIDENCE)
                detail.append(
                    f"{canonical} {finding.field} = {finding.value!r} has no evidence "
                    "stating it"
                )
        if reasons:
            refusals.append(Refusal(spelling, tuple(reasons), tuple(detail)))
            continue

        entries.append(ExpansionEntry(
            spelling=spelling,
            canonical_name=canonical,
            classification=classification,
            properties=_ordered_properties(row),
            findings=findings,
            derived_restatements=_derived_restatements(facts),
        ))

    return ExpansionPlan(
        entries=tuple(entries), refusals=tuple(refusals), integrity_violations=(),
        catalogue_size=len(catalogue), catalogue_digest=digest,
    )


# ===========================================================================
# The production-boundary rule: the named files may not name the catalogue
# ===========================================================================
def redirection_violations(sources: Mapping[str, str]) -> tuple[str, ...]:
    """
    A production authority file that names the local catalogue is a redirection:
    production resolves sections through the live Supabase-backed SectionMatcher,
    never through the local catalogue. This is the named, readable rule; the tree
    digest guard below freezes the whole app tree as well.
    """
    violations: list[str] = []
    for path, text in sorted(sources.items()):
        named = sorted(set(CATALOGUE_CONSTRUCT_PATTERN.findall(text)))
        if named:
            violations.append(f"{path} names {', '.join(named)}")
    return tuple(violations)


def _source_text(relative_path: str) -> str:
    return (REPO / relative_path).read_text(encoding="utf-8")


def _current_digests() -> dict:
    return step_f._tree_digests()


def tree_change_violations(frozen: Mapping[str, str],
                           current: Mapping[str, str]) -> tuple[str, ...]:
    """The digest comparison itself, separated so a test can prove it detects a change."""
    return tuple(sorted(
        f"{path} changed" for path, digest in current.items() if frozen.get(path) != digest
    ))


def measured_changes(prefixes: tuple[str, ...] = ()) -> tuple[str, ...]:
    """
    Files whose bytes differ from Step G's import-time freeze of the repository
    tree (Step F's guard, which covers app/**/*.py and tests/data/*.json).
    """
    current = _current_digests()
    frozen = step_f._FROZEN_TREE_AT_IMPORT
    changed = [path for path, digest in current.items() if frozen.get(path) != digest]
    if prefixes:
        changed = [path for path in changed if path.startswith(prefixes)]
    return tuple(sorted(changed))


def _step_e_token(step_e_report_obj, raw_token: str):
    for token in step_e_report_obj.tokens:
        if token.raw_token == raw_token:
            return token
    return None


def _live_250pfc_row() -> dict:
    """The accepted live 250PFC record, taken from the pinned live snapshot."""
    rows = step_f._live_rows()
    matched = [row for row in rows if row.get("name") == "250PFC"]
    assert len(matched) == 1, "the pinned live reference rows must hold exactly one 250PFC"
    for key, value in LIVE_250PFC_ROW.items():
        assert matched[0].get(key) == value, (key, matched[0].get(key))
    return matched[0]


# ===========================================================================
# Fixtures
# ===========================================================================
@dataclass(frozen=True)
class CatalogueHandle:
    """A read-only handle for the tests: the accepted catalogue and a copy builder."""
    accepted: Mapping[str, Mapping[str, Any]]

    def with_rows(self, **replacements) -> dict:
        """A mutable COPY of the accepted catalogue with candidate rows replaced."""
        candidate = {name: dict(row) for name, row in self.accepted.items()}
        candidate.update(replacements)
        return candidate


@pytest.fixture(scope="module")
def catalogues(modules) -> CatalogueHandle:
    return CatalogueHandle(accepted=modules.local_catalogue.CATALOGUE)


@pytest.fixture(scope="module")
def plan_modules(modules):
    """The accepted catalogue and its own provenance vocabulary."""
    return modules.local_catalogue.CATALOGUE, tuple(modules.local_catalogue.PROVENANCE_KINDS)


@pytest.fixture(scope="module")
def plan(audit, plan_modules):
    """The expansion, built from Step F's measured audit and the accepted catalogue."""
    accepted, provenance_kinds = plan_modules
    return build_expansion(audit, accepted, provenance_kinds=provenance_kinds)


@pytest.fixture(scope="module")
def build_plan(audit, plan_modules):
    """Build the expansion: the accepted catalogue and Step F's audit by default."""
    accepted, provenance_kinds = plan_modules

    def _build(catalogue=None, spellings=APPROVED_SPELLINGS, audit_obj=None,
               provenance=None):
        return build_expansion(
            audit if audit_obj is None else audit_obj,
            accepted if catalogue is None else catalogue,
            spellings,
            provenance_kinds if provenance is None else provenance,
        )

    return _build


# ===========================================================================
# Test doubles for the mutations: fabricated EVIDENCE, never fabricated verdicts
# ===========================================================================
def _audit_with_facts(audit_obj, token: str, mutate):
    """
    A copy of the audit in which ONE candidate's measured facts are replaced. The
    classifier reads nothing but those facts, so this is the only way to 'pretend'
    a classification — and every mutation test asserts the verdict actually moved.
    """
    candidates = tuple(
        replace(candidate, facts=mutate(candidate.facts))
        if candidate.pin.raw_token == token else candidate
        for candidate in audit_obj.candidates
    )
    return replace(audit_obj, candidates=candidates)


def _pretend_class_a(facts, accepted_row_name: str, recorded: tuple[tuple[str, float], ...]):
    """
    Fabricate exactly the evidence Step F's class-A rule reads (an accepted row
    stating the token's values, and no conflict). Nothing here fabricates the
    verdict itself — `step_f.classify` still derives it.
    """
    return replace(
        facts,
        token_is_printed=True,
        accepted_row_fields=((accepted_row_name, tuple(recorded)),),
        accepted_rows_stating_the_values=(accepted_row_name,),
        conflicting_rows=(),
        conflict_fields=(),
        conflict_detail="",
        coverage_basis=step_f.COVERAGE_NONE,
        coverage_row=None,
        coverage_resolution=None,
        # The screen is forced to a state that makes no numeric claim (rather than
        # fabricating a passed tolerance check); a DISAGREES screen would still
        # block class A on its own.
        screen_verdict=step_f.SCREEN_NOT_APPLICABLE,
        screen_detail="",
    )


# ===========================================================================
# 1. The expansion is Step F's own six, and nothing else
# ===========================================================================
class TestTheExpansionIsStepsOwnSix:
    def test_the_approved_set_is_exactly_steps_measured_class_a_set(self, audit):
        measured_class_a = tuple(
            candidate.pin.raw_token for candidate in audit.candidates
            if candidate.classification == CLASS_A
        )
        assert len(measured_class_a) == 6
        assert set(measured_class_a) == set(APPROVED_SPELLINGS)
        assert APPROVED_SPELLINGS == (
            "130X12FL", "130x12FL", "180X20FL", "180x20FL", "250X12FL", "90x10FL"
        )

    def test_every_approved_spelling_is_a_step_e_candidate(self, audit, step_e_report):
        candidates = set(step_f.step_e_candidate_tokens(step_e_report))
        assert set(APPROVED_SPELLINGS) <= candidates
        assert set(APPROVED_SPELLINGS) <= set(audit.tokens)

    def test_the_plan_accepts_every_approved_spelling_and_refuses_nothing(self, plan):
        assert plan.is_buildable
        assert plan.integrity_violations == ()
        assert plan.spellings == APPROVED_SPELLINGS
        assert plan.refusals == ()

    def test_the_expansion_has_exactly_four_canonical_identities(self, plan):
        assert plan.identities == CANONICAL_IDENTITIES
        assert len(plan.identities) == 4

    def test_each_variant_pair_resolves_to_one_identity_and_one_row(self, plan):
        for upper, lower in (("130X12FL", "130x12FL"), ("180X20FL", "180x20FL")):
            left, right = plan.entry_for(upper), plan.entry_for(lower)
            assert left.canonical_name == right.canonical_name
            assert left.row == right.row
        for spelling in APPROVED_SPELLINGS:
            assert plan.entry_for(spelling).canonical_name == IDENTITY_OF_SPELLING[spelling]

    def test_the_plan_is_deterministic(self, audit, modules):
        first = build_expansion(audit, modules.local_catalogue.CATALOGUE)
        second = build_expansion(audit, modules.local_catalogue.CATALOGUE)
        assert first.render() == second.render()
        assert first.catalogue_digest == ACCEPTED_CATALOGUE_DIGEST
        for spelling in APPROVED_SPELLINGS:
            assert first.entry_for(spelling).row == dict(
                PINNED_ROWS[IDENTITY_OF_SPELLING[spelling]])


# ===========================================================================
# 2. Canonical identity follows the catalogue's existing convention
# ===========================================================================
class TestCanonicalIdentity:
    def test_the_canonical_name_is_the_catalogues_own_key(self, plan, catalogues):
        for entry in plan.entries:
            assert entry.canonical_name in catalogues.accepted
            assert catalogues.accepted[entry.canonical_name]["name"] == entry.canonical_name
            assert entry.row == dict(catalogues.accepted[entry.canonical_name])

    def test_the_convention_is_derived_from_the_accepted_catalogue(self, catalogues):
        # Every accepted FL/PL plate row follows <width>X<thickness><FAMILY> in upper
        # case; that is the convention, and it is read from the catalogue itself.
        plate_rows = [name for name, row in catalogues.accepted.items()
                      if row.get("family") in ("FL", "PL") and "width" in row]
        assert len(plate_rows) >= 15
        for name in plate_rows:
            assert CANONICAL_NAME_PATTERN.match(name), name
        for identity in CANONICAL_IDENTITIES:
            assert CANONICAL_NAME_PATTERN.match(identity)
        # A lowercase spelling is NOT a canonical name — which is why the variants
        # collapse into the upper-case row instead of becoming rows themselves.
        for spelling in APPROVED_SPELLINGS:
            if spelling != IDENTITY_OF_SPELLING[spelling]:
                assert not CANONICAL_NAME_PATTERN.match(spelling)

    def test_case_variants_are_not_separate_engineering_identities(self, plan, catalogues):
        # The catalogue normalises case AND spacing, so a spelling that differs from
        # its canonical name only in case is a variant of it — not an identity.
        lower = [spelling for spelling in APPROVED_SPELLINGS
                 if spelling != IDENTITY_OF_SPELLING[spelling]]
        assert lower == ["130x12FL", "180x20FL", "90x10FL"]
        assert len(plan.identities) == 4 < len(plan.spellings) == 6
        for spelling in lower:
            assert canonical_key(catalogues.accepted, spelling) == \
                IDENTITY_OF_SPELLING[spelling]
            assert spelling not in catalogues.accepted
            assert plan.entry_for(spelling).canonical_name == \
                IDENTITY_OF_SPELLING[spelling]

    def test_the_identity_is_an_exact_match_never_a_suffix_fallback(self, catalogues, modules):
        matcher = modules.local_catalogue.CatalogueMatcher()
        # The fallback exists, and resolves 200UB30 to the weight-only 200UB30.4 ...
        assert matcher.match("200UB30")["name"] == "200UB30.4"
        # ... and that fallback target is NOT an identity for the token.
        assert canonical_key(catalogues.accepted, "200UB30") is None
        for spelling in APPROVED_SPELLINGS:
            assert canonical_key(catalogues.accepted, spelling) is not None

    def test_no_two_catalogue_keys_share_one_identity_and_the_expansion_adds_none(
            self, plan, catalogues):
        identities = [_normalise(name) for name in catalogues.accepted]
        assert len(set(identities)) == len(identities) == ACCEPTED_CATALOGUE_ROW_COUNT
        assert len(catalogues.accepted) == ACCEPTED_CATALOGUE_ROW_COUNT
        assert plan.catalogue_size == ACCEPTED_CATALOGUE_ROW_COUNT
        assert set(plan.identities) <= set(catalogues.accepted)


# ===========================================================================
# 3. Every property is explicitly evidenced, per property
# ===========================================================================
class TestEveryPropertyIsExplicitlyEvidenced:
    @pytest.mark.parametrize("spelling", APPROVED_SPELLINGS)
    def test_every_property_is_explicitly_evidenced(self, plan, spelling):
        entry = plan.entry_for(spelling)
        assert entry.findings, spelling
        for finding in entry.findings:
            assert finding.agreed, (spelling, finding.field)
            assert not finding.disagreed, (spelling, finding.field)
            assert finding.is_evidenced, (spelling, finding.field)
        assert entry.row == dict(PINNED_ROWS[IDENTITY_OF_SPELLING[spelling]])

    def test_the_drawing_statement_is_the_evidence_for_every_dimension(self, plan, audit):
        for spelling in APPROVED_SPELLINGS:
            entry = plan.entry_for(spelling)
            facts = audit.by_token(spelling).facts
            printed = {stated.field: stated.value for stated in facts.stated_values
                       if stated.provenance == step_f.FROM_PRINTED_SIZE_CELL}
            assert printed == {"width": entry.finding("width").value,
                               "thickness": entry.finding("thickness").value}, spelling
            for field in ("width", "thickness"):
                assert any(step_f.FROM_PRINTED_SIZE_CELL in text
                           for text in entry.finding(field).agreed), (spelling, field)

    def test_the_plate_record_corroborates_180x20fl(self, plan):
        finding = plan.entry_for("180X20FL").finding("width")
        assert any(step_f.FROM_CAPTURED_PLATE_RECORD in text for text in finding.agreed)

    def test_the_weighed_rows_weight_is_the_accepted_records_value(self, plan, audit):
        weighed = {spelling for spelling in APPROVED_SPELLINGS
                   if plan.entry_for(spelling).carries_weight}
        assert weighed == {"180X20FL", "180x20FL", "250X12FL"}
        for spelling in weighed:
            entry = plan.entry_for(spelling)
            assert entry.weight_finding.value == \
                PINNED_ROWS[entry.canonical_name]["weight_per_metre"]
            # The evidence cites the accepted prior-milestone row by its own measured
            # evidence string — the source, not a constant repeated in this file.
            citing = [stated.evidence for stated in audit.by_token(spelling).facts.stated_values
                      if stated.provenance == step_f.FROM_PRIOR_MILESTONE]
            assert citing, spelling
            assert any(text.startswith(citing[0])
                       for text in entry.weight_finding.agreed), spelling

    def test_the_unweighed_rows_carry_no_weight_key(self, plan, catalogues):
        unweighed = {spelling for spelling in APPROVED_SPELLINGS
                     if not plan.entry_for(spelling).carries_weight}
        assert unweighed == {"130X12FL", "130x12FL", "90x10FL"}
        for spelling in unweighed:
            name = IDENTITY_OF_SPELLING[spelling]
            assert "weight_per_metre" not in catalogues.accepted[name]
            assert "weight_per_metre" not in plan.entry_for(spelling).row

    def test_no_row_property_is_evidenced_by_a_derived_value(self, plan):
        for entry in plan.entries:
            for finding in entry.findings:
                for text in finding.agreed:
                    assert step_f.DERIVED_RESTATEMENT not in text
                    assert step_f.DERIVED_VOLUME_PROXY not in text

    def test_the_family_is_the_accepted_records_captured_family(self, plan, catalogues):
        for entry in plan.entries:
            assert entry.row["family"] == ACCEPTED_FAMILY[entry.canonical_name]
            assert catalogues.accepted[entry.canonical_name]["family"] == \
                ACCEPTED_FAMILY[entry.canonical_name]


# ===========================================================================
# 4. Nothing is derived: no weight, no dimension, no neighbouring row
# ===========================================================================
class TestNothingIsDerived:
    @pytest.mark.parametrize("spelling,recorded", (("180X20FL", 28.3), ("250X12FL", 23.6)))
    def test_the_stored_weight_is_not_the_drawings_derived_restatement(
            self, plan, spelling, recorded):
        entry = plan.entry_for(spelling)
        assert entry.weight_finding.value == recorded
        assert entry.derived_restatements, spelling
        restated = None
        for label, value in entry.derived_restatements:
            assert label == step_f.DERIVED_RESTATEMENT
            assert value != recorded, (spelling, value)
            restated = value
        # ... and the restatement really is a close near-miss, so the inequality is
        # a measurement rather than a coincidence.
        assert restated is not None and abs(restated - recorded) < 1.0

    def test_the_plan_returns_the_accepted_rows_verbatim(self, plan, catalogues):
        for entry in plan.entries:
            accepted = dict(catalogues.accepted[entry.canonical_name])
            assert entry.row == accepted
            assert entry.properties == tuple(
                (field, accepted[field]) for field in PRESENTATION_ORDER if field in accepted
            )

    def test_no_neighbouring_row_supplies_a_value(self, plan, catalogues):
        for name in ("130X10FL", "100X10FL"):
            assert name in catalogues.accepted
        # 130X12FL: the neighbour 130X10FL shares the width and differs in
        # thickness — the row's thickness is the drawing's 12, never 10.
        assert plan.entry_for("130X12FL").finding("thickness").value == 12.0
        assert catalogues.accepted["130X10FL"]["thickness"] == 10.0
        # 90X10FL: the neighbour 100X10FL shares the thickness and differs in width.
        assert plan.entry_for("90x10FL").finding("width").value == 90.0
        assert catalogues.accepted["100X10FL"]["width"] == 100.0

    def test_a_computed_weight_would_not_be_adopted(self, plan):
        entry = plan.entry_for("180X20FL")
        derived = dict(entry.derived_restatements)[step_f.DERIVED_RESTATEMENT]
        assert derived == 28.235294117647054
        assert entry.weight_finding.value == 28.3 != derived


# ===========================================================================
# 5. The other Step F candidates stay out
# ===========================================================================
class TestTheOtherCandidatesStayOut:
    @pytest.mark.parametrize("classification,spellings", tuple(EXCLUDED_BY_CLASS.items()))
    def test_every_candidate_outside_class_a_is_refused(self, build_plan, audit, plan,
                                                        classification, spellings):
        assert step_f.EXPECTED_CLASSIFICATIONS  # the expectation table is Step F's own
        for spelling in spellings:
            assert audit.by_token(spelling).classification == classification, spelling
            requested = build_plan(spellings=(spelling,))
            refusal = requested.refusal_for(spelling)
            assert refusal is not None, spelling
            assert REASON_NOT_APPROVED_BY_THE_MILESTONE in refusal.reasons, spelling
            assert REASON_NOT_CLASS_A in refusal.reasons, spelling
            assert classification in " ".join(refusal.detail), spelling
            assert requested.entries == ()
            assert spelling not in plan.spellings
            assert spelling not in plan.identities

    def test_250x90pfc_is_outside_the_expansion_and_stays_conflicting(self, build_plan, audit):
        refusal = build_plan(spellings=("250X90PFC",)).refusal_for("250X90PFC")
        assert REASON_NOT_CLASS_A in refusal.reasons
        assert step_f.CLASS_CONFLICTING in " ".join(refusal.detail)
        assert audit.by_token("250X90PFC").facts.conflicting_rows == ("250PFC",)

    def test_150x75pfc_is_outside_the_expansion_and_its_covered_row_is_untouched(
            self, build_plan, audit, catalogues):
        refusal = build_plan(spellings=("150X75PFC",)).refusal_for("150X75PFC")
        assert REASON_NOT_CLASS_A in refusal.reasons
        assert audit.by_token("150X75PFC").facts.coverage_row == "150PFC"
        assert canonical_key(catalogues.accepted, "150X75PFC") is None
        assert "150X75PFC" not in catalogues.accepted

    def test_200ub30_is_outside_the_expansion_and_its_fallback_row_is_not_an_identity(
            self, build_plan, audit, catalogues, modules):
        refusal = build_plan(spellings=("200UB30",)).refusal_for("200UB30")
        assert REASON_NOT_CLASS_A in refusal.reasons
        assert step_f.CLASS_PARTIAL in " ".join(refusal.detail)
        assert canonical_key(catalogues.accepted, "200UB30") is None
        assert REASON_NO_CANONICAL_IDENTITY in refusal.reasons
        matcher = modules.local_catalogue.CatalogueMatcher()
        assert matcher.match("200UB30")["name"] == "200UB30.4"
        assert dict(catalogues.accepted["200UB30.4"]) == EXCLUDED_PRESENT_ROWS["200UB30.4"]

    def test_310ub42_is_outside_the_expansion_and_has_no_row_at_all(
            self, build_plan, catalogues):
        refusal = build_plan(spellings=("310UB42",)).refusal_for("310UB42")
        assert REASON_NOT_CLASS_A in refusal.reasons
        assert step_f.CLASS_NO_EVIDENCE in " ".join(refusal.detail)
        assert REASON_NO_CANONICAL_IDENTITY in refusal.reasons
        assert canonical_key(catalogues.accepted, "310UB42") is None

    def test_the_excluded_tokens_existing_rows_are_unchanged(self, catalogues):
        # Three excluded candidate tokens ARE catalogue-present as accepted rows;
        # Step G must leave them exactly as they were.
        for name, row in EXCLUDED_PRESENT_ROWS.items():
            assert dict(catalogues.accepted[name]) == row, name
        assert len(EXCLUDED_PRESENT_ROWS) == 4

    def test_a_spelling_outside_step_fs_candidate_set_is_refused_by_name(self, build_plan,
                                                                        catalogues):
        spelling = "130X99FL"
        assert spelling not in step_f.EXPECTED_CLASSIFICATIONS
        assert canonical_key(catalogues.accepted, spelling) is None
        requested = build_plan(spellings=(spelling,))
        refusal = requested.refusal_for(spelling)
        assert refusal is not None
        assert refusal.reasons == (REASON_NOT_A_STEP_F_CANDIDATE,)
        assert requested.entries == ()


# ===========================================================================
# 6. The accepted catalogue is unchanged
# ===========================================================================
class TestTheAcceptedCatalogueIsUnchanged:
    def test_the_digest_version_and_row_count_are_the_accepted_ones(self, catalogues,
                                                                   modules):
        assert modules.local_catalogue.CATALOGUE_DIGEST == ACCEPTED_CATALOGUE_DIGEST
        assert modules.local_catalogue.CATALOGUE_VERSION == ACCEPTED_CATALOGUE_VERSION
        assert catalogue_digest(catalogues.accepted) == ACCEPTED_CATALOGUE_DIGEST
        assert len(catalogues.accepted) == ACCEPTED_CATALOGUE_ROW_COUNT

    def test_the_catalogue_file_is_byte_identical_to_the_accepted_one(self):
        path = REPO / ACCEPTED_CATALOGUE_FILE
        assert hashlib.sha256(path.read_bytes()).hexdigest() == ACCEPTED_CATALOGUE_FILE_SHA256

    def test_the_expansion_rows_are_byte_identical_to_the_accepted_rows(self, catalogues):
        for name, row in PINNED_ROWS.items():
            assert dict(catalogues.accepted[name]) == row, name

    def test_the_milestone_cd_rows_are_unchanged(self, catalogues):
        # Anchored to the accepted Milestone C/D proof module's own tables.
        for name in accepted_catalogue_proof.PLATE_ONLY_NAMES:
            family, width, thickness = accepted_catalogue_proof.PLATE_ONLY_VALUES[name]
            assert dict(catalogues.accepted[name]) == {
                "name": name, "family": family, "width": width, "thickness": thickness,
            }, name
        for name in accepted_catalogue_proof.WEIGHT_ONLY_NAMES:
            assert set(catalogues.accepted[name]) == {"name", "family", "weight_per_metre"}, name

    def test_the_e1_enriched_rows_are_unchanged(self, catalogues, modules):
        enriched = {name: row for name, row in catalogues.accepted.items()
                    if "provenance" in row}
        assert tuple(sorted(enriched)) == E1_ENRICHED_ROWS
        for name, row in enriched.items():
            assert row["provenance"] in modules.local_catalogue.PROVENANCE_KINDS, name
        assert enriched["100PFC"]["weight_per_metre"] == 8.3
        assert enriched["100PFC"]["live_catalogue_weight_per_metre"] == 8.33

    def test_no_row_is_silently_overwritten(self, modules):
        # The catalogue merges a weight-only map and a dimensioned map; a key in
        # both would be silently overwritten. The accepted maps are disjoint, so the
        # merge loses nothing — asserted against the merge itself.
        weight_only = modules.local_catalogue._WEIGHT_ONLY_SECTIONS
        dimensioned = modules.local_catalogue._DIMENSIONED_SECTIONS
        assert set(weight_only) & set(dimensioned) == set()
        assert len(weight_only) + len(dimensioned) == len(modules.local_catalogue.CATALOGUE)
        assert len({**weight_only, **dimensioned}) == len(weight_only) + len(dimensioned)


# ===========================================================================
# 7. The E1 conflict records are untouched
# ===========================================================================
class TestTheE1ConflictRecordsAreUntouched:
    def test_the_conflicting_pair_still_blocks_with_the_same_fields(self, modules, catalogues):
        live = _live_250pfc_row()
        verdict = modules.source_of_truth.evaluate_section_evidence(
            dict(catalogues.accepted[E1_CONFLICT_TOKEN]), live)
        assert verdict.status == modules.source_of_truth.VERDICT_CONFLICT_BLOCKED
        assert verdict.resolution == modules.source_of_truth.RESOLUTION_BLOCKED_REVIEW
        assert verdict.conflicting_fields == E1_CONFLICT_FIELDS

    def test_the_expansion_does_not_resolve_the_conflict(self, plan, catalogues):
        assert E1_CONFLICT_TOKEN not in plan.identities
        assert catalogues.accepted[E1_CONFLICT_TOKEN]["flange_thickness"] == 15.0
        assert catalogues.accepted[E1_CONFLICT_TOKEN]["web_thickness"] == 8.0
        assert catalogues.accepted["250PFC"]["weight_per_metre"] == 35.5

    def test_the_e1_boundary_constants_are_unchanged(self, modules):
        assert modules.source_of_truth.GEOMETRY_DEFINING_FIELDS == (
            "depth", "flange_width", "flange_thickness", "web_thickness",
            "width", "thickness", "leg_size", "outside_diameter",
        )
        assert modules.source_of_truth.LIVE_TO_LOCAL_FAMILY == {"FLAT": "FL"}

    def test_the_expansion_agrees_with_the_drawing_evidence_for_all_four(
            self, audit, catalogues, modules):
        # The positive counterpart: the drawing-side evidence for each identity
        # agrees with its accepted row on every field the drawing states — these
        # four are the ones with no dispute.
        for spelling in APPROVED_SPELLINGS:
            facts = audit.by_token(spelling).facts
            drawing = {stated.field: stated.value for stated in facts.stated_values
                       if stated.provenance in step_f.PRINTED_PROVENANCES
                       + step_f.CAPTURE_PROVENANCES}
            assert drawing, spelling
            verdict = modules.source_of_truth.evaluate_section_evidence(
                drawing, dict(catalogues.accepted[IDENTITY_OF_SPELLING[spelling]]))
            assert verdict.status == modules.source_of_truth.VERDICT_AGREEMENT, spelling
            assert verdict.conflicting_fields == ()


# ===========================================================================
# 8. Production is not redirected
# ===========================================================================
class TestProductionIsNotRedirected:
    def test_the_production_authority_files_name_no_catalogue_construct(self):
        sources = {path: _source_text(path) for path in PRODUCTION_AUTHORITY_FILES}
        assert redirection_violations(sources) == ()
        for path in PRODUCTION_AUTHORITY_FILES:
            assert (REPO / path).exists(), path

    def test_the_production_matcher_returns_none_for_every_approved_spelling(
            self, real_matcher):
        for spelling in APPROVED_SPELLINGS:
            match = real_matcher.resolve(spelling)
            assert match.resolution == step_f.NONE, spelling
            assert match.catalogue_row is None, spelling

    def test_the_local_catalogue_resolves_them_while_production_does_not(
            self, real_matcher, modules):
        local = modules.local_catalogue.CatalogueMatcher()
        for spelling in APPROVED_SPELLINGS:
            row = local.match(spelling)
            assert row is not None, spelling
            assert row["name"] == IDENTITY_OF_SPELLING[spelling]
            assert real_matcher.resolve(spelling).catalogue_row is None, spelling

    def test_the_production_matcher_declares_no_local_catalogue_version(self, real_matcher):
        assert getattr(real_matcher, "catalogue_version", None) is None
        assert real_matcher.reference_identity.source_kind == "LIVE_SUPABASE"
        assert real_matcher.reference_identity.identity_status == "UNVERSIONED"

    def test_the_local_catalogue_declares_only_its_own_version(self, modules):
        assert modules.local_catalogue.CatalogueMatcher().catalogue_version == \
            ACCEPTED_CATALOGUE_VERSION
        # An override catalogue declares None — the versioned identity belongs to
        # this module's CATALOGUE alone, never to a caller's mapping.
        override = modules.local_catalogue.CatalogueMatcher({"X": {"name": "X"}})
        assert override.catalogue_version is None

    def test_step_e_still_measures_none_for_the_six(self, step_e_report):
        for spelling in APPROVED_SPELLINGS:
            token = _step_e_token(step_e_report, spelling)
            assert token is not None, spelling
            assert token.resolution == step_f.NONE, spelling
            assert token.reason == step_f.REASON_MISSING_ROW, spelling

    def test_no_supabase_access_happened_and_the_jev_module_is_clean(self, modules):
        assert isinstance(modules.section_matcher.supabase, step_f._NetworkGuard)
        assert os.environ.get("SUPABASE_URL") != step_f.TEST_SUPABASE_URL
        jev = _source_text(JEV_BOUNDARY_FILE)
        assert "section_catalogue" not in jev
        assert "CatalogueMatcher" not in jev


# ===========================================================================
# 9. The mutations M1 - M8, each one non-vacuous
# ===========================================================================
class TestTheMutationsAreRefused:
    def test_m0_the_unmutated_baseline_builds(self, plan, catalogues):
        assert plan.is_buildable
        assert plan.refusals == ()
        assert len(plan.entries) == len(APPROVED_SPELLINGS)
        assert catalogue_integrity_violations(catalogues.accepted) == ()

    def test_m1_200ub30_relabelled_class_a_is_still_refused(self, audit, build_plan):
        before = audit.by_token("200UB30")
        assert before.classification == step_f.CLASS_PARTIAL
        faked = _pretend_class_a(before.facts, "200UB30", (("weight_per_metre", 30.4),))
        mutated = _audit_with_facts(audit, "200UB30", lambda _facts: faked)
        assert mutated.by_token("200UB30").classification == CLASS_A
        requested = build_plan(spellings=("200UB30",), audit_obj=mutated)
        refusal = requested.refusal_for("200UB30")
        assert refusal is not None
        assert REASON_NOT_APPROVED_BY_THE_MILESTONE in refusal.reasons
        assert REASON_NO_CANONICAL_IDENTITY in refusal.reasons
        assert requested.entries == ()

    def test_m2_250x90pfc_relabelled_class_a_is_still_refused(self, audit, build_plan):
        before = audit.by_token("250X90PFC")
        assert before.classification == step_f.CLASS_CONFLICTING
        faked = _pretend_class_a(
            before.facts, "250X90PFC",
            (("flange_thickness", 15.0), ("web_thickness", 8.0)))
        mutated = _audit_with_facts(audit, "250X90PFC", lambda _facts: faked)
        assert mutated.by_token("250X90PFC").classification == CLASS_A
        requested = build_plan(spellings=("250X90PFC",), audit_obj=mutated)
        refusal = requested.refusal_for("250X90PFC")
        assert refusal is not None
        assert REASON_NOT_APPROVED_BY_THE_MILESTONE in refusal.reasons
        assert requested.entries == ()

    def test_m3_310ub42_relabelled_class_a_is_still_refused(self, audit, build_plan):
        before = audit.by_token("310UB42")
        assert before.classification == step_f.CLASS_NO_EVIDENCE
        faked = _pretend_class_a(
            before.facts, "310UB42", (("depth", 310.0), ("weight_per_metre", 42.0)))
        mutated = _audit_with_facts(audit, "310UB42", lambda _facts: faked)
        assert mutated.by_token("310UB42").classification == CLASS_A
        requested = build_plan(spellings=("310UB42",), audit_obj=mutated)
        refusal = requested.refusal_for("310UB42")
        assert refusal is not None
        assert REASON_NOT_APPROVED_BY_THE_MILESTONE in refusal.reasons
        assert REASON_NO_CANONICAL_IDENTITY in refusal.reasons
        assert requested.entries == ()

    def test_m4_a_calculated_weight_is_refused(self, plan, build_plan, catalogues):
        entry = plan.entry_for("180X20FL")
        derived = dict(entry.derived_restatements)[step_f.DERIVED_RESTATEMENT]
        assert derived != 28.3
        row = PINNED_ROWS["180X20FL"]
        # Remove the explicit weight: an absent weight is legitimate (the accepted
        # 130X12FL row carries none), so the row is accepted without one ...
        spelling = ("180X20FL",)
        stripped = build_plan(catalogues.with_rows(**{
            "180X20FL": {k: v for k, v in row.items() if k != "weight_per_metre"}}),
            spellings=spelling)
        assert stripped.refusal_for("180X20FL") is None
        assert not stripped.entry_for("180X20FL").carries_weight
        # ... and the calculated kg/m, injected as the weight, is refused.
        injected = catalogues.with_rows(**{
            "180X20FL": {**row, "weight_per_metre": derived}})
        refused = build_plan(injected, spellings=spelling)
        refusal = refused.refusal_for("180X20FL")
        assert refusal is not None
        assert REASON_VALUE_DISAGREES_WITH_EVIDENCE in refusal.reasons
        assert "28.3" in " ".join(refusal.detail)
        assert refused.entries == ()

    def test_m5_a_neighbouring_rows_dimension_is_refused(self, plan, build_plan, catalogues):
        # 130X10FL's thickness substituted into 130X12FL: the drawing states 12.
        assert catalogues.accepted["130X10FL"]["thickness"] == 10.0
        mutated = catalogues.with_rows(**{"130X12FL": {
            "name": "130X12FL", "family": "FL", "width": 130.0, "thickness": 10.0}})
        # The substituted row still resolves locally; the evidence is what refuses it.
        assert mutated["130X12FL"]["thickness"] == 10.0
        refused = build_plan(mutated, spellings=("130X12FL",))
        refusal = refused.refusal_for("130X12FL")
        assert refusal is not None
        assert REASON_VALUE_DISAGREES_WITH_EVIDENCE in refusal.reasons
        assert any("thickness" in line for line in refusal.detail)
        assert refused.entries == ()
        assert "130X10FL" not in plan.spellings

    def test_m6_two_rows_for_one_identity_are_refused(self, plan, build_plan, catalogues):
        duplicate = dict(catalogues.accepted["130X12FL"])
        mutated = catalogues.with_rows(**{"130x12FL": duplicate})
        violations = catalogue_integrity_violations(mutated)
        # The duplicate key is caught twice over: the lower-case key is not the row's
        # own name, AND the two keys normalise to one engineering identity.
        assert len(violations) == 2
        assert any("one engineering identity" in line for line in violations)
        assert any("'130X12FL'" in line and "'130x12FL'" in line for line in violations)
        assert any("carries name" in line for line in violations)
        refused = build_plan(mutated)
        assert not refused.is_buildable
        assert refused.entries == ()
        # The unmutated catalogue has none of this, and the plan is buildable.
        assert catalogue_integrity_violations(catalogues.accepted) == ()
        assert plan.is_buildable

    def test_m7_production_redirection_is_caught(self):
        sources = {path: _source_text(path) for path in PRODUCTION_AUTHORITY_FILES}
        assert redirection_violations(sources) == ()
        path = PRODUCTION_AUTHORITY_FILES[0]
        injected = dict(sources)
        injected[path] = sources[path] + (
            "\nfrom app.engineering_data.section_catalogue import CATALOGUE, CatalogueMatcher\n"
            "\ndef _redirected(spelling):\n    return CatalogueMatcher().match(spelling)\n"
        )
        assert injected[path] != sources[path]
        violations = redirection_violations(injected)
        assert len(violations) == 1
        assert violations[0].startswith(path)
        # ... and the tree digest guard would catch the write itself.
        frozen = dict(step_f._FROZEN_TREE_AT_IMPORT)
        current = dict(frozen)
        current[path] = hashlib.sha256(injected[path].encode("utf-8")).hexdigest()
        assert current[path] != frozen[path]
        assert tree_change_violations(frozen, current) == (f"{path} changed",)

    def test_m8_250x90pfc_built_from_the_live_250pfc_row_is_refused(
            self, audit, plan, build_plan, catalogues, modules):
        live = _live_250pfc_row()
        attempted = {"name": "250X90PFC", "family": "PFC", "depth": live["depth"],
                     "flange_width": live["flange_width"],
                     "flange_thickness": live["flange_thickness"],
                     "web_thickness": live["web_thickness"],
                     "weight_per_metre": live["weight_per_metre"]}
        # The accepted catalogue DOES hold a 250X90PFC row — so the refusal here is
        # not "no row exists" but "the evidence for this row is in dispute".
        assert canonical_key(catalogues.accepted, "250X90PFC") == "250X90PFC"
        mutated = catalogues.with_rows(**{"250X90PFC": attempted})
        refused = build_plan(mutated, spellings=("250X90PFC",))
        refusal = refused.refusal_for("250X90PFC")
        assert refusal is not None
        assert REASON_NOT_APPROVED_BY_THE_MILESTONE in refusal.reasons
        assert REASON_VALUE_DISAGREES_WITH_EVIDENCE in refusal.reasons
        assert refused.entries == ()
        assert audit.by_token("250X90PFC").classification == step_f.CLASS_CONFLICTING
        # The evidence the attempt rests on is itself in dispute: the E1 boundary
        # blocks the live 250PFC values against the accepted capture-side record.
        verdict = modules.source_of_truth.evaluate_section_evidence(
            dict(catalogues.accepted["250X90PFC"]), live)
        assert verdict.status == modules.source_of_truth.VERDICT_CONFLICT_BLOCKED
        assert verdict.conflicting_fields == E1_CONFLICT_FIELDS
        assert E1_CONFLICT_TOKEN not in plan.identities


# ===========================================================================
# 10. The scope guard: nothing was written, and production was not touched
# ===========================================================================
class TestTheScopeGuard:
    def test_the_declared_modification_sets_are_empty_and_measured_empty(self):
        assert INTENDED_PRODUCTION_MODIFICATIONS == ()
        assert INTENDED_SUPABASE_MODIFICATIONS == ()
        assert INTENDED_CAPTURE_DATA_MODIFICATIONS == ()
        assert INTENDED_JEV_MODIFICATIONS == ()
        assert measured_changes() == ()
        assert measured_changes(("app/",)) == ()
        assert measured_changes(("tests/data/",)) == ()

    def test_the_production_authority_files_are_unchanged(self):
        for path in PRODUCTION_AUTHORITY_FILES + (ACCEPTED_CATALOGUE_FILE, JEV_BOUNDARY_FILE):
            assert path in step_f._FROZEN_TREE_AT_IMPORT, path
            assert path not in measured_changes(), path

    def test_the_jev_module_is_unchanged(self):
        assert JEV_BOUNDARY_FILE in step_f._FROZEN_TREE_AT_IMPORT
        assert (REPO / JEV_BOUNDARY_FILE).exists()
        assert measured_changes(("app/cad_engine/jev",)) == ()

    def test_this_module_is_the_only_intended_new_file(self):
        assert INTENDED_NEW_FILES == (EXPANSION_FILE,)
        assert Path(__file__).resolve() == (REPO / EXPANSION_FILE).resolve()
        assert (REPO / EXPANSION_FILE).exists()
        assert EXPANSION_FILE not in step_f._FROZEN_TREE_AT_IMPORT

    def test_building_the_plan_writes_nothing(self, plan, catalogues, audit, modules):
        before = _current_digests()
        rebuilt = build_expansion(audit, modules.local_catalogue.CATALOGUE)
        assert rebuilt.render() == plan.render()
        assert rebuilt.catalogue_digest == ACCEPTED_CATALOGUE_DIGEST
        assert _current_digests() == before
        with pytest.raises(TypeError):
            catalogues.accepted["ZZ"] = {}
