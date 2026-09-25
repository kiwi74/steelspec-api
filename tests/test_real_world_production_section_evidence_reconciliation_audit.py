"""
Step F — REAL-WORLD SECTION EVIDENCE RECONCILIATION AUDIT.

Step E measured WHICH real-world section tokens production cannot resolve, and
put the 25 tokens whose family the live catalogue does support into a
MISSING_CATALOGUE_ROW list. It proposed no row and made no recommendation —
deliberately: "knowing that 310UB42 shares '310' with three real rows does not
make '310UB42' a section that should exist."

This milestone asks the next question of exactly that same 25-token set, and
answers nothing else:

    is there reliable evidence ALREADY IN THIS REPOSITORY sufficient to add an
    exact catalogue row?

One classification per candidate, from five:

  A EXACTLY_SUPPORTED_BY_EXISTING_EVIDENCE
      The drawing's own printed statement and an accepted catalogue row from a
      prior milestone state the section's identifying dimensions, and the
      drawing's printed record does not contradict itself.
  B PARTIAL_EVIDENCE_REQUIRES_HUMAN_REVIEW
      Something is stated — the drawing prints the designation — but nothing
      existing can be reconciled with it, or the printed record disagrees with
      itself. A human decides; this audit will not.
  C CONFLICTING_EVIDENCE
      Two accepted records state DIFFERENT values for the same section. Adding a
      row would first require resolving that conflict, which is not this
      milestone's work.
  D NO_SUFFICIENT_EVIDENCE
      The drawing does not print this designation anywhere. The token exists
      only in a capture.
  E ALREADY_COVERED_BY_EXISTING_ROW
      What the drawing itself prints picks out exactly one existing live row.
      There is nothing to add.

WHAT IS EVIDENCE, AND WHAT IS NOT
The single most useful distinction here is between the DRAWING'S OWN PRINT and
the CAPTURE'S TOKEN. A capture row is an extraction of a drawing; where the two
disagree the drawing's text layer is the evidence and the token is the claim.
Several candidates are capture-side misreads — `250UC46` for a printed `250PFC`,
`23UB37` for a printed `250UB37`, `89x89 SHS` for a printed `89x5 SHS` — and one
is a capture-side merge (`1200PFC` for a printed run `DB1200 PFC`). Classifying
those as gaps in the catalogue would be repeating an extraction error back as a
catalogue requirement, so the pinned record carries the capture token and the
printed designation SEPARATELY, and where they differ the difference is the
finding.

Evidence used, in the milestone's own order of preference:

  1. genuine captured drawing evidence (tests/data/*.json, and the live Arkles
     workload rows in tests/test_arkles_strand.py::ARKLES_STRAND_RAW);
  2. the live steel_sections rows (the pinned 218-row snapshot);
  3. catalogue fixtures already accepted by prior milestones
     (app/engineering_data/section_catalogue.py — Milestones C, D, E1);
  4. existing engineering-data definitions
     (app/engineering_data/source_of_truth.py, section_matcher.py);
  5. the two genuine source drawings the repository itself pins, read for their
     printed text layer only;
  6. each drawing's own printed rows, used as each other's reference.

No external source is introduced and no web research is performed.

NO DERIVED ENGINEERING FACTS
This audit invents no engineering value. It does not calculate a section's mass
from its dimensions, does not interpolate between sections, does not infer a
thickness, a flange or a web from a family convention, does not turn a naming
pattern into a row, and never treats a suffix fallback or a family match as an
identity. Where a candidate's numbers exist only in the token's spelling, that is
reported as exactly that and grounds no A and no E.

Two derived quantities DO appear, and both are labelled DERIVED in item 11 of
every candidate that has them:

  * a printed mass restated per metre — the drawing's printed kilograms divided
    by the drawing's printed length. Both inputs are printed; no dimension is
    used and no property is asserted.
  * a printed row's kilograms per printed cubic millimetre of its printed size,
    used only as a self-consistency screen among the DRAWING'S OWN printed rows
    of the same family.

Neither ever sources a classification. The screens can only DEMOTE a candidate
to B; they can never promote one to A or E. The one material finding they produce
(`300x8FL`) is reported as a disagreement inside the drawing's own printed record
that a human must resolve.

THE AUTHORITY RULES THIS FILE ENFORCES ON ITSELF

  * the audit runs through the genuine production `SectionMatcher`, refused
    otherwise by Step E's own authority guard, and against the genuine Step E
    report measured over the same pinned reference rows;
  * the candidate set is Step E's measured list — a hand-picked set of tokens is
    refused, because an audit over chosen candidates is a recommendation wearing
    an audit's clothes;
  * every pinned member must be a genuine occurrence in Step E's corpus;
  * a coverage claim requires the row to be in the token's OWN family and to
    carry EVERY number the token claims — a family match or a shared leading
    dimension is never an identity;
  * a measured conflict suppresses any coverage claim for that candidate, which
    is the milestone's `250X90PFC` requirement expressed as a rule rather than a
    promise;
  * every pinned printed run is re-read from the real PDF by the tests;
  * nothing is written: no catalogue row, no production file, no capture.

WHAT THIS FILE DOES NOT DO
It adds no catalogue row. It does not modify SectionMatcher, pipeline.py, CAD
authority, DXF authority, E1-E5, tests/data or Supabase. It does not repair an
extraction. Its output is evidence classification, not implementation.
"""

from __future__ import annotations

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

from tests import test_real_world_production_section_coverage_audit as coverage

# ===========================================================================
# Step E is the evidence set — re-exported, never re-measured
# ===========================================================================
REPO = coverage.REPO
CAPTURE_DIR = coverage.CAPTURE_DIR
CAPTURE_FILES = coverage.CAPTURE_FILES

SNAPSHOT_PATH = coverage.SNAPSHOT_PATH
SNAPSHOT_SHA256 = coverage.SNAPSHOT_SHA256
SNAPSHOT_ROW_COUNT = coverage.SNAPSHOT_ROW_COUNT
LIVE_DIGEST = coverage.LIVE_DIGEST

TEST_SUPABASE_URL = coverage.TEST_SUPABASE_URL
TEST_SERVICE_ROLE_KEY = coverage.TEST_SERVICE_ROLE_KEY

EXACT = coverage.EXACT
SUFFIX_FALLBACK = coverage.SUFFIX_FALLBACK
NONE = coverage.NONE
REASON_MISSING_ROW = coverage.REASON_MISSING_ROW
CATALOGUE_FAMILY_OF_CODE = coverage.CATALOGUE_FAMILY_OF_CODE
SOURCE_ARKLES_WORKLOAD = coverage.SOURCE_ARKLES_WORKLOAD

Occurrence = coverage.Occurrence
build_corpus = coverage.build_corpus
build_audit = coverage.build_audit
AuditAuthorityError = coverage.AuditAuthorityError
audit_authority_violations = coverage.audit_authority_violations
PRODUCTION_MATCHER = coverage.PRODUCTION_MATCHER

# The proof-only local catalogue (Milestones C / D / E1) — evidence source 3,
# read only. Its digest is pinned by the accepted E1 tests; this audit refuses to
# cite a catalogue that has moved.
LOCAL_CATALOGUE_MODULE = "app.engineering_data.section_catalogue"
LOCAL_CATALOGUE_DIGEST = "7f9a08b3167ff8d4ddada10f807d5b5874337ef76e6b8baaaf5dab9dd3c7652c"

# The E1 source-of-truth boundary, which already keeps the two 250-class PFC
# records distinct and is therefore the repository's own record of the conflict
# this milestone must not disturb.
SOURCE_OF_TRUTH_MODULE = "app.engineering_data.source_of_truth"


def _normalise(text: str) -> str:
    """The matcher's own normalisation — whitespace stripped, upper-cased."""
    return re.sub(r"\s+", "", text.strip().upper())


def _numbers(text: str) -> tuple[float, ...]:
    """Every number the text literally carries. No reading, no inference."""
    return tuple(float(found) for found in re.findall(r"\d+(?:\.\d+)?", text))


def _same(left, right) -> bool:
    return abs(float(left) - float(right)) < 1e-9


# ===========================================================================
# The candidate set — the milestone's 20 names plus Step E's variant spellings
# ===========================================================================
MILESTONE_NAMED_CANDIDATES = (
    "110UB48", "200UB30", "290UB 37", "310UB42", "23UB37",
    "200UC", "250UC46",
    "1200PFC", "150X75PFC", "250X90PFC", "259PFC",
    "130X12FL", "180X20FL", "250X12FL", "300x8FL", "90x10FL",
    "75x6.0SHS", "89x5 SHS", "89x89 SHS",
    "90X10EA",
)

# The exact duplicate / variant spelling of a named token that is itself a
# distinct raw token in Step E's corpus. The milestone requires both spellings to
# be classified; neither is folded into the other.
STEP_E_VARIANT_SPELLINGS = ("130x12FL", "150x75PFC", "180x20FL", "250x90PFC", "90x10EA")

PINNED_CANDIDATE_COUNT = 25


def step_e_candidate_tokens(step_e_report) -> tuple[str, ...]:
    """The raw tokens Step E itself put in the missing-row list."""
    return tuple(raw_token for raw_token, *_ in step_e_report.reconciliation_candidates)


# ===========================================================================
# Classifications
# ===========================================================================
CLASS_EXACTLY_SUPPORTED = "EXACTLY_SUPPORTED_BY_EXISTING_EVIDENCE"
CLASS_PARTIAL = "PARTIAL_EVIDENCE_REQUIRES_HUMAN_REVIEW"
CLASS_CONFLICTING = "CONFLICTING_EVIDENCE"
CLASS_NO_EVIDENCE = "NO_SUFFICIENT_EVIDENCE"
CLASS_ALREADY_COVERED = "ALREADY_COVERED_BY_EXISTING_ROW"

CLASSIFICATIONS = (
    CLASS_EXACTLY_SUPPORTED,
    CLASS_PARTIAL,
    CLASS_CONFLICTING,
    CLASS_NO_EVIDENCE,
    CLASS_ALREADY_COVERED,
)

CLASSIFICATION_LETTERS = {
    CLASS_EXACTLY_SUPPORTED: "A",
    CLASS_PARTIAL: "B",
    CLASS_CONFLICTING: "C",
    CLASS_NO_EVIDENCE: "D",
    CLASS_ALREADY_COVERED: "E",
}

# ===========================================================================
# Where a stated value comes from — the vocabulary item 11 reports in
# ===========================================================================
# The drawing's own text layer, in one of the three positions it prints a
# section in.
FROM_PRINTED_SIZE_CELL = "PRINTED_SIZE_CELL"
FROM_PRINTED_DIMENSION_LABEL = "PRINTED_DIMENSION_LABEL"
FROM_PRINTED_SCHEDULE_DESIGNATION = "PRINTED_SCHEDULE_DESIGNATION"
# A genuine captured connection-plate record — a different capture field than the
# section token, extracted from the same drawing's detail.
FROM_CAPTURED_PLATE_RECORD = "CAPTURED_CONNECTION_PLATE_RECORD"
# An accepted row of a prior milestone (Milestones C / D / E1), or a live row.
FROM_PRIOR_MILESTONE = "PRIOR_ACCEPTED_MILESTONE_ROW"
FROM_LIVE_ROW = "LIVE_REFERENCE_ROW"
# The token's own spelling, and only that: nothing in the repository states these
# numbers about a section; they are a reading of the token text.
FROM_TOKEN_SPELLING_ONLY = "TOKEN_SPELLING_ONLY"

PRINTED_PROVENANCES = (
    FROM_PRINTED_SIZE_CELL,
    FROM_PRINTED_DIMENSION_LABEL,
    FROM_PRINTED_SCHEDULE_DESIGNATION,
)
CAPTURE_PROVENANCES = (FROM_CAPTURED_PLATE_RECORD,)
ROW_PROVENANCES = (FROM_PRIOR_MILESTONE, FROM_LIVE_ROW)
STATED_PROVENANCES = PRINTED_PROVENANCES + CAPTURE_PROVENANCES + ROW_PROVENANCES
SPELLING_PROVENANCES = (FROM_TOKEN_SPELLING_ONLY,)
PROVENANCES = STATED_PROVENANCES + SPELLING_PROVENANCES

# ===========================================================================
# Why a candidate is (or is not) covered by a row that already exists
# ===========================================================================
COVERAGE_NONE = "NO_COVERAGE_ROUTE"
COVERAGE_VALUE_AGREEMENT = "COVERAGE_VALUE_AGREEMENT"
COVERAGE_MATCHER_RESOLUTION = "COVERAGE_MATCHER_RESOLUTION"

COVERAGE_BASES = (COVERAGE_VALUE_AGREEMENT, COVERAGE_MATCHER_RESOLUTION, COVERAGE_NONE)

# ===========================================================================
# The self-consistency screens — derived, and never a promotion
# ===========================================================================
MASS_SCREEN_TOLERANCE = 0.05

SCREEN_AGREES = "AGREES_WITHIN_TOLERANCE"
SCREEN_DISAGREES = "DISAGREES_OUTSIDE_TOLERANCE"
SCREEN_NO_REFERENCE_WEIGHT = "NO_REFERENCE_WEIGHT"
SCREEN_NO_PRINTED_MASS = "NO_PRINTED_MASS"
SCREEN_NOT_APPLICABLE = "NOT_APPLICABLE_FOR_THIS_FAMILY"

SCREEN_VERDICTS = (
    SCREEN_AGREES,
    SCREEN_DISAGREES,
    SCREEN_NO_REFERENCE_WEIGHT,
    SCREEN_NO_PRINTED_MASS,
    SCREEN_NOT_APPLICABLE,
)

DERIVED_RESTATEMENT = "DERIVED_RESTATED_KG_PER_METRE"
DERIVED_VOLUME_PROXY = "DERIVED_KG_PER_PRINTED_MM3"

# ===========================================================================
# The fields a conflict is measured over, and the fields a family identifies by
# ===========================================================================
GEOMETRY_DEFINING_FIELDS = (
    "depth", "flange_width", "flange_thickness", "web_thickness",
    "width", "thickness", "leg_size", "outside_diameter",
)

# A recorded weight is not geometry (E1's boundary says so), but two records of
# the same section disagreeing on it is still a conflict a row cannot be added
# over.
MILESTONE_CONFLICT_FIELDS = GEOMETRY_DEFINING_FIELDS + ("weight_per_metre",)

IDENTIFYING_FIELDS = {
    "UB": ("depth",),
    "UC": ("depth",),
    "PFC": ("depth", "flange_width"),
    "SHS": ("width", "thickness"),
    "EA": ("leg_size", "thickness"),
    "FL": ("width", "thickness"),
}

# A schedule designation names a section in the catalogue's own naming
# convention: the number before the family code and the number after it. The
# repository's rows make that convention explicit (200UB29.8 records weight
# 29.8; 310UB40.4 records 40.4), so a designation's numbers may be reported under
# these two role names, honestly, without claiming a dimension.
DESIGNATION_FIELDS = ("designation_series", "designation_weight")

# ===========================================================================
# The two drawings the repository itself pins
# ===========================================================================
@dataclass(frozen=True)
class SourceDrawing:
    """A genuine source drawing, with the repository file that pins it."""
    name: str
    path: Path
    pages: int
    pinned_in: str


SELBY = SourceDrawing(
    name="Selby Square shop drawings (FABs.pdf)",
    path=Path("/Users/chad/Downloads/FABs.pdf"),
    pages=32,
    pinned_in="tests/test_real_world_material_extraction.py",
)
ARKLES = SourceDrawing(
    name="Arkles Strand consent set (24633 33 Arkles Strand - Plans 17.08.26 (lodged).pdf)",
    path=Path("/Users/chad/Downloads/24633 33 Arkles Strand - Plans 17.08.26 (lodged).pdf"),
    pages=41,
    pinned_in="tests/test_project_extraction_intake.py",
)
SOURCE_DRAWINGS = (SELBY, ARKLES)
DRAWING_BY_NAME = {drawing.name: drawing for drawing in SOURCE_DRAWINGS}

# Each capture fixture was produced from one of those drawings, and the page
# ranges below are the ranges each capture file covers — the repository's own
# record of what came from where.
CAPTURE = {
    "arkles": "arkles_strand_page_extractions.json",
    "recover26": "selby_square_page_26_recovery.json",
    "selby": "selby_square_page_extractions.json",
    "selby6_10": "selby_square_pages_6_10_extractions.json",
    "selby11_15": "selby_square_pages_11_15_extractions.json",
    "selby16_20": "selby_square_pages_16_20_extractions.json",
    "selby21_25": "selby_square_pages_21_25_extractions.json",
    "selby26_30": "selby_square_pages_26_30_extractions.json",
    "selby31_32": "selby_square_pages_31_32_extractions.json",
}


def _selby_capture_by_page() -> dict:
    mapping = {}
    for capture, pages in (
        ("selby", range(1, 6)),
        ("selby6_10", range(6, 11)),
        ("selby11_15", range(11, 16)),
        ("selby16_20", range(16, 21)),
        ("selby21_25", range(21, 26)),
        ("selby26_30", range(26, 31)),
        ("selby31_32", range(31, 33)),
    ):
        for page in pages:
            mapping[page] = capture
    return mapping


SELBY_CAPTURE_BY_PAGE = _selby_capture_by_page()

SOURCE_DRAWING_OF = {name: SELBY.name for name in CAPTURE.values() if name != CAPTURE["arkles"]}
SOURCE_DRAWING_OF[CAPTURE["arkles"]] = ARKLES.name
SOURCE_DRAWING_OF[SOURCE_ARKLES_WORKLOAD] = ARKLES.name


# ===========================================================================
# The pinned evidence
# ===========================================================================
@dataclass(frozen=True)
class PrintedMass:
    """
    A weight the drawing itself prints for one member, with the printed length and
    printed quantity printed beside it. The restatement per metre is derived and
    labelled as such wherever it is used.
    """
    mass_kg: float
    length_mm: float
    quantity: int
    evidence: str

    @property
    def label(self) -> str:
        return f"{self.mass_kg} kg printed for {self.length_mm} mm x qty {self.quantity}"


@dataclass(frozen=True)
class SourceMember:
    """
    One genuine occurrence: the capture's row, and — recorded SEPARATELY — the
    designations the drawing's own text layer prints for that member's mark. The
    two are not assumed to agree; where they differ, the difference is the finding.
    """
    source: str
    page: int
    mark: str
    captured: str
    printed: tuple[str, ...]
    printed_run: str
    mass: PrintedMass | None = None

    @property
    def drawing(self) -> str:
        return SOURCE_DRAWING_OF[self.source]

    @property
    def reference(self) -> str:
        return f"{self.drawing} p{self.page} mark {self.mark!r}"

    @property
    def print_states_the_token(self) -> bool:
        """Does the drawing print THIS token, exactly (case- and space-insensitive)?"""
        wanted = _normalise(self.captured)
        return any(_normalise(text) == wanted for text in self.printed)


@dataclass(frozen=True)
class StatedValue:
    """A value about the section, and where it comes from."""
    field: str
    value: float
    provenance: str
    evidence: str

    @property
    def is_stated(self) -> bool:
        return self.provenance in STATED_PROVENANCES

    def render(self) -> str:
        return f"{self.field} = {self.value:g} [{self.provenance}] ({self.evidence})"


@dataclass(frozen=True)
class CandidatePin:
    """
    Everything this audit claims to know about one candidate, all of it measured
    from the repository before the audit runs.

    `pick_values` are the values the DRAWING states for the section — the only
    values a coverage claim may rest on. A candidate whose numbers exist only in
    its own token spelling has none.
    """
    raw_token: str
    family_code: str
    pick_values: tuple[float, ...]
    stated: tuple[StatedValue, ...]
    members: tuple[SourceMember, ...]
    prior_rows: tuple[str, ...]
    expected: str
    next_action: str
    note: str = ""

    @property
    def normalised_token(self) -> str:
        return _normalise(self.raw_token)

    @property
    def family_catalogue_name(self) -> str:
        return CATALOGUE_FAMILY_OF_CODE.get(self.family_code, self.family_code)

    @property
    def token_is_printed(self) -> bool:
        """Does the drawing print this candidate's own token anywhere?"""
        return any(member.print_states_the_token for member in self.members)

    @property
    def printed_designations(self) -> tuple[str, ...]:
        found = []
        for member in self.members:
            for text in member.printed:
                if text not in found:
                    found.append(text)
        return tuple(found)

    @property
    def printed_runs(self) -> tuple[str, ...]:
        found = []
        for member in self.members:
            if member.printed_run not in found:
                found.append(member.printed_run)
        return tuple(found)

    @property
    def printed_masses(self) -> tuple[PrintedMass, ...]:
        return tuple(member.mass for member in self.members if member.mass is not None)

    @property
    def pages(self) -> tuple[tuple[str, int, str], ...]:
        return tuple((member.drawing, member.page, member.mark) for member in self.members)


@dataclass(frozen=True)
class PrintedReferenceRow:
    """A shop-material-list row printed on one of the drawings, used as a peer."""
    drawing: str
    page: int
    mark: str
    size: str
    length_mm: float
    quantity: int
    mass_kg: float
    run: str


def _member(source, page, mark, captured, printed, run, mass=None):
    # A printed mass cannot be detached from the run it was printed in: if the
    # caller did not restate the run, the member's own run is its evidence.
    if mass is not None and not mass.evidence:
        mass = replace(mass, evidence=run)
    return SourceMember(
        source=source, page=page, mark=mark, captured=captured,
        printed=tuple(printed), printed_run=run, mass=mass,
    )


def _mass(kg, length_mm, quantity=1, run=""):
    return PrintedMass(mass_kg=kg, length_mm=length_mm, quantity=quantity, evidence=run)


def _selby_member(page, mark, captured, printed, run, mass=None):
    return _member(CAPTURE[SELBY_CAPTURE_BY_PAGE[page]], page, mark, captured,
                   printed, run, mass)


def _arkles_member(page, mark, captured, printed, run, mass=None):
    return _member(CAPTURE["arkles"], page, mark, captured, printed, run, mass)


def _project_member(page, mark, captured, printed, run, mass=None):
    return _member(SOURCE_ARKLES_WORKLOAD, page, mark, captured, printed, run, mass)


# --- the drawings' own runs, verbatim from their text layers -----------------
# Selby's shop material list prints "MARK SIZE GRADE LENGTH QTY AREA WEIGHT",
# grade 300 throughout; every run below is one row of that list.
SELBY_POSTS_HEADER = "MARK SIZE GRADE LENGTH QTY AREA WEIGHT MM M2 KG"

# The Arkles plans carry the post schedule as separate text-layer lines, and wrap
# "PORTAL FRAME" across two of them — hence the collapsed spellings below. The
# tests re-read each run from the PDF.
ARKLES_POSTS_RUN = ("STEEL POSTS P1 89x5 SHS P2 250PFC P3 200UC46 "
                    "P4 300PFC P5 250UB37")
ARKLES_P15_RUN = "P2 250 PFC P2 250 PFC P1 89x5 SHS P3 200UC46 P3 200UC46"
ARKLES_P15_BF1_RUN = "PORTAL FRAM E BF1 - 310UB40"
ARKLES_P21_RUN = "FLOOR BEAMS DB1200 PFC DB2 2/200x63 hySPAN"
ARKLES_P23_RUN = "B7 BF1 250PFC BF1 310UB40 BF2 250UB37 BF2 200UB30"
ARKLES_P27_RUN = "B8 250UB 37 30 10 10"
ARKLES_P29_RUN = "BF2 P3 200UC46 BF2 200UB30 RB5 200PFC"

# --- A: the drawing's printed statement and an accepted prior row agree -------
_PIN_130X12FL = CandidatePin(
    raw_token="130X12FL",
    family_code="FL",
    pick_values=(130.0, 12.0),
    stated=(
        StatedValue("width", 130.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '130X12FL' (p3 mark PL030)"),
        StatedValue("thickness", 12.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '130X12FL' (p3 mark PL030)"),
        StatedValue("width", 130.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone D row 130X12FL"),
        StatedValue("thickness", 12.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone D row 130X12FL"),
    ),
    members=(
        _selby_member(3, "PL030", "130X12FL", ("130X12FL",),
                      "PL030 130X12FL 300 225 1 0.07 2.8", _mass(2.8, 225.0)),
    ),
    prior_rows=("130X12FL",),
    expected=CLASS_EXACTLY_SUPPORTED,
    next_action=(
        "the repository already holds what an exact row needs: the drawing's own SIZE cell "
        "(width 130, thickness 12) and the accepted Milestone D row 130X12FL state the same "
        "two values, and the printed mass agrees with the drawing's own plate rows"
    ),
    note="one member on p3 prints the upper-case label; the same mark prints the lower-case "
         "spelling on p2 and p4 and is classified separately",
)

_PIN_130x12FL = replace(
    _PIN_130X12FL,
    raw_token="130x12FL",
    members=(
        _selby_member(2, "PL029", "130x12FL", ("130X12FL",),
                      "PL029 130X12FL 300 225 1 0.07 2.8", _mass(2.8, 225.0)),
        _selby_member(4, "PL031", "130x12FL", ("130X12FL",),
                      "PL031 130X12FL 300 210 1 0.06 2.6", _mass(2.6, 210.0)),
    ),
    note="variant spelling: the capture reads the same printed label in lower case, which is "
         "why Step E carries it as a distinct raw token",
)

_PIN_180X20FL = CandidatePin(
    raw_token="180X20FL",
    family_code="FL",
    pick_values=(180.0, 20.0),
    stated=(
        StatedValue("width", 180.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '180X20FL' (p6 mark PL008)"),
        StatedValue("thickness", 20.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '180X20FL' (p6 mark PL008)"),
        StatedValue("width", 180.0, FROM_CAPTURED_PLATE_RECORD,
                    "Selby p17 VIEW A-A end_plate record width_mm 180 "
                    "(tests/data/selby_square_pages_16_20_extractions.json)"),
        StatedValue("thickness", 20.0, FROM_CAPTURED_PLATE_RECORD,
                    "Selby p17 VIEW A-A end_plate record thickness_mm 20"),
        StatedValue("width", 180.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 180X20FL (fully traced)"),
        StatedValue("thickness", 20.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 180X20FL (fully traced)"),
    ),
    members=(
        _selby_member(6, "PL008", "180X20FL", ("180X20FL",),
                      "PL008 180X20FL 300 340 1 0.14 9.6", _mass(9.6, 340.0)),
    ),
    prior_rows=("180X20FL",),
    expected=CLASS_EXACTLY_SUPPORTED,
    next_action=(
        "the repository already holds what an exact row needs: the drawing's SIZE cell, a "
        "genuine captured connection-plate record (180 wide x 20 thick x 340 deep) and the "
        "accepted Milestone C row 180X20FL all state width 180 and thickness 20"
    ),
    note="the only candidate corroborated by a captured connection-plate record; the nine "
         "lower-case spellings are classified separately",
)

_PIN_180x20FL = replace(
    _PIN_180X20FL,
    raw_token="180x20FL",
    members=(
        _selby_member(5, "PL008", "180x20FL", ("180X20FL",),
                      "PL008 180X20FL 300 340 1 0.14 9.6", _mass(9.6, 340.0)),
        _selby_member(8, "PL008", "180x20FL", ("180X20FL",),
                      "PL008 180X20FL 300 340 1 0.14 9.6", _mass(9.6, 340.0)),
        _selby_member(9, "PL008", "180x20FL", ("180X20FL",),
                      "PL008 180X20FL 300 340 1 0.14 9.6", _mass(9.6, 340.0)),
        _selby_member(13, "PL008", "180x20FL", ("180X20FL",),
                      "PL008 180X20FL 300 340 1 0.14 9.6", _mass(9.6, 340.0)),
        _selby_member(27, "PL008", "180x20FL", ("180X20FL",),
                      "PL008 180X20FL 300 340 2 0.29 19.2"),
    ),
    note="variant spelling across five further pages. The p27 row prints quantity 2 and a "
         "total mass for two pieces, so its 19.2 kg is deliberately NOT restated as a per-"
         "metre weight — only the quantity-1 rows are screened",
)

_PIN_250X12FL = CandidatePin(
    raw_token="250X12FL",
    family_code="FL",
    pick_values=(250.0, 12.0),
    stated=(
        StatedValue("width", 250.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '250X12FL' (p1 mark PL028)"),
        StatedValue("thickness", 12.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '250X12FL' (p1 mark PL028)"),
        StatedValue("width", 250.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 250X12FL (fully traced)"),
        StatedValue("thickness", 12.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 250X12FL (fully traced)"),
    ),
    members=(
        _selby_member(1, "PL028", "250X12FL", ("250X12FL",),
                      "PL028 250X12FL 300 155 1 0.09 3.7", _mass(3.7, 155.0)),
    ),
    prior_rows=("250X12FL",),
    expected=CLASS_EXACTLY_SUPPORTED,
    next_action=(
        "the repository already holds what an exact row needs: the drawing's SIZE cell and "
        "the accepted Milestone C row 250X12FL state width 250 and thickness 12"
    ),
    note="the accepted row records weight 23.6 kg/m and the drawing's printed mass restates "
         "to 23.9 kg/m; no live 250-wide FLAT row exists",
)

_PIN_300x8FL = CandidatePin(
    raw_token="300x8FL",
    family_code="FL",
    pick_values=(300.0, 8.0),
    stated=(
        StatedValue("width", 300.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '300X8FL' (p13 mark PL003)"),
        StatedValue("thickness", 8.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '300X8FL' (p13 mark PL003)"),
        StatedValue("width", 300.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone D row 300X8FL"),
        StatedValue("thickness", 8.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone D row 300X8FL"),
    ),
    members=(
        _selby_member(13, "PL003", "300x8FL", ("300X8FL",),
                      "PL003 300X8FL 300 300 1 0.09 2.5", _mass(2.5, 300.0)),
    ),
    prior_rows=("300X8FL",),
    expected=CLASS_PARTIAL,
    next_action=(
        "human review of Selby p13 before any row is added: the drawing prints PL003 as a "
        "300X8FL, 300 long, weighing 2.5 kg, while its own rows for the same 8 mm plate print "
        "2.8 kg for two 75x8x300 pieces. One of the drawing's printed values is wrong and "
        "this audit will not choose which"
    ),
    note="the only candidate whose printed size and printed weight disagree with the "
         "drawing's own other printed rows of the same plate thickness",
)

_PIN_90x10FL = CandidatePin(
    raw_token="90x10FL",
    family_code="FL",
    pick_values=(90.0, 10.0),
    stated=(
        StatedValue("width", 90.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '90X10FL' (p2 mark PL027)"),
        StatedValue("thickness", 10.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '90X10FL' (p2 mark PL027)"),
        StatedValue("width", 90.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone D row 90X10FL"),
        StatedValue("thickness", 10.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone D row 90X10FL"),
    ),
    members=(
        _selby_member(2, "PL027", "90x10FL", ("90X10FL",),
                      "PL027 90X10FL 300 220 1 0.05 1.6", _mass(1.6, 220.0)),
    ),
    prior_rows=("90X10FL",),
    expected=CLASS_EXACTLY_SUPPORTED,
    next_action=(
        "the repository already holds what an exact row needs: the drawing's SIZE cell and "
        "the accepted Milestone D row 90X10FL state width 90 and thickness 10"
    ),
    note="neither the accepted row nor any live row records a weight for this plate, so only "
         "the drawing's own printed mass is available to screen it",
)

# --- B: the drawing prints it, and nothing existing can be reconciled with it ---
_PIN_200UB30 = CandidatePin(
    raw_token="200UB30",
    family_code="UB",
    pick_values=(200.0, 30.0),
    stated=(
        StatedValue("designation_series", 200.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles schedule 'BF2 200UB30' (p29; also p23)"),
        StatedValue("designation_weight", 30.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles schedule 'BF2 200UB30' (p29)"),
    ),
    members=(
        _project_member(29, "BF2", "200UB30", ("200UB30",), ARKLES_P29_RUN),
        _arkles_member(23, "BF1", "200UB30", ("250PFC", "310UB40"), ARKLES_P23_RUN),
    ),
    prior_rows=("200UB30.4",),
    expected=CLASS_PARTIAL,
    next_action=(
        "human review: the drawing prints 200UB30 on two pages, the accepted local row "
        "200UB30.4 records weight 30.4 with NO dimensions, and the only live 200-series UB "
        "rows carry 29.8, 25.4, 22.3 and 18.2. Nothing states this designation's dimensions "
        "and this audit will not assume it is one of those rows"
    ),
    note="the milestone named this token specifically to forbid assuming it is 200UB25.4 or "
         "200UB29.8; measured, neither row carries both of its numbers",
)

# --- C: two accepted records state different values for the same section ------
_PIN_250X90PFC = CandidatePin(
    raw_token="250X90PFC",
    family_code="PFC",
    pick_values=(250.0, 90.0),
    stated=(
        StatedValue("depth", 250.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '250X90PFC' (p1 mark 001)"),
        StatedValue("flange_width", 90.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '250X90PFC' (p1 mark 001)"),
        StatedValue("flange_thickness", 15.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 250X90PFC (fully traced)"),
        StatedValue("web_thickness", 8.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 250X90PFC (fully traced)"),
        StatedValue("weight_per_metre", 35.5, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 250X90PFC (fully traced)"),
    ),
    members=(
        _selby_member(1, "001", "250X90PFC", ("250X90PFC",),
                      "001 250X90PFC 300 2613 1 2.21 92.8", _mass(92.8, 2613.0)),
    ),
    prior_rows=("250X90PFC", "250PFC"),
    expected=CLASS_CONFLICTING,
    next_action=(
        "add nothing and merge nothing: the live 250PFC record (flange thickness 12, web "
        "thickness 7, weight 31.8) and the accepted Selby-side record 250X90PFC (flange "
        "thickness 15, web thickness 8, weight 35.5) are two accepted records of the same "
        "section that disagree. The E1 source-of-truth boundary keeps them distinct and this "
        "audit changes nothing about that"
    ),
    note="the milestone's own worked example of a hard conflict. The drawing's printed mass "
         "(92.8 kg over 2613 mm = 35.5 kg/m) agrees with the accepted 250X90PFC record and "
         "not with the live 250PFC row",
)

_PIN_250x90PFC = replace(
    _PIN_250X90PFC,
    raw_token="250x90PFC",
    members=(
        _selby_member(32, "032", "250x90PFC", ("250X90PFC",),
                      "032 250X90PFC 300 6590 1 5.57 233.9", _mass(233.9, 6590.0)),
    ),
    note="variant spelling on a further page: the printed mass restates to 35.5 kg/m, the "
         "same conflict measured against the same live row",
)

# --- D: the drawing never prints this designation -----------------------------
_PIN_110UB48 = CandidatePin(
    raw_token="110UB48",
    family_code="UB",
    pick_values=(),
    stated=(
        StatedValue("designation_series", 110.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states this; the token's own spelling only"),
    ),
    members=(
        _arkles_member(23, "BF1", "110UB48", ("250PFC", "310UB40"), ARKLES_P23_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review of the capture, not of the catalogue: Arkles p23 prints no 110UB48 — "
        "the run reads 'B7 BF1 250PFC BF1 310UB40 BF2 250UB37 BF2 200UB30'. The token is not "
        "a drawing designation and no row can be added from it"
    ),
    note="the captured row's own mark is BF1, whose printed designations are 250PFC and "
         "310UB40; no printed text on the page contains 110",
)

_PIN_290UB37 = CandidatePin(
    raw_token="290UB 37",
    family_code="UB",
    pick_values=(),
    stated=(
        StatedValue("designation_series", 290.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states this; the token's own spelling only"),
    ),
    members=(
        _arkles_member(27, "B8", "290UB 37", ("250UB 37",), ARKLES_P27_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review: Arkles p27 prints 'B8 250UB 37 30 10 10', not 290UB 37. The live "
        "250UB37.3 row records depth 256, and the token's 290 matches no recorded value"
    ),
    note="the token keeps the drawing's own space between the section and its number, which "
         "is why Step E reached it with a space; the 290 is a capture misread of the printed "
         "250",
)

_PIN_310UB42 = CandidatePin(
    raw_token="310UB42",
    family_code="UB",
    pick_values=(310.0, 40.0),
    stated=(
        StatedValue("designation_series", 310.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles p15 label 'PORTAL FRAME BF1 - 310UB40'"),
        StatedValue("designation_weight", 40.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles p15 label 'PORTAL FRAME BF1 - 310UB40'"),
    ),
    members=(
        _project_member(15, "PORTAL FRAME BF1", "310UB42", ("310UB40",),
                        ARKLES_P15_BF1_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review: the drawing prints 310UB40 for this member, not 310UB42. Neither live "
        "candidate is assumed — 310UB40.4 records depth 304 and 310UB46.2 records 307, and "
        "the token's 42 is carried by no row at all"
    ),
    note="the milestone named this token specifically to forbid assuming it is 310UB40.4 or "
         "310UB46.2; measured, neither row carries both of its numbers",
)

_PIN_23UB37 = CandidatePin(
    raw_token="23UB37",
    family_code="UB",
    pick_values=(),
    stated=(
        StatedValue("designation_series", 23.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states this; the token's own spelling only"),
    ),
    members=(
        _arkles_member(23, "BF2", "23UB37", ("250UB37", "200UB30"), ARKLES_P23_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review: Arkles p23 prints 250UB37 and 200UB30 for mark BF2; the token's 23 "
        "matches no printed designation on the page"
    ),
    note="a capture-side truncation of the printed 250UB37 printed beside it, recorded as a "
         "misread and not as a second section",
)

_PIN_250UC46 = CandidatePin(
    raw_token="250UC46",
    family_code="UC",
    pick_values=(),
    stated=(
        StatedValue("designation_series", 250.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states 250UC46; the token's own spelling only"),
        StatedValue("designation_weight", 46.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states 250UC46; the token's own spelling only"),
    ),
    members=(
        _arkles_member(6, "P2", "250UC46", ("250PFC",), ARKLES_POSTS_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review: Arkles p6 prints 'P2 250PFC' — the drawing's channel, not a UC "
        "section. The token invents both the family and the 46, so no row can be added from it"
    ),
    note="Step E's measured shared-leading-dimension rows for this token are 250UC72.9 and "
         "250UC89.5 — neither is 46, and neither is assumed",
)

_PIN_1200PFC = CandidatePin(
    raw_token="1200PFC",
    family_code="PFC",
    pick_values=(),
    stated=(
        StatedValue("designation_series", 1200.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states 1200PFC; the token's own spelling only"),
    ),
    members=(
        _arkles_member(21, "DB1", "1200PFC", ("DB1200 PFC",), ARKLES_P21_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review of Arkles p21: the text layer reads 'FLOOR BEAMS DB1200 PFC "
        "DB2 2/200x63 hySPAN' — the mark DB1 immediately followed by a PFC label, the same "
        "shape as the DB2 entry beside it. No source states a 1200PFC split and no live or "
        "accepted row carries 1200 in this family"
    ),
    note="the captured mark is DB1 and the token is 1200PFC: the '1' of the mark and the "
         "section beside it were merged by the text layer. The merge is reported, not resolved",
)

_PIN_259PFC = CandidatePin(
    raw_token="259PFC",
    family_code="PFC",
    pick_values=(),
    stated=(
        StatedValue("designation_series", 259.0, FROM_TOKEN_SPELLING_ONLY,
                    "no source states this; the token's own spelling only"),
    ),
    members=(
        _arkles_member(23, "BF1", "259PFC", ("250PFC", "310UB40"), ARKLES_P23_RUN),
    ),
    prior_rows=(),
    expected=CLASS_NO_EVIDENCE,
    next_action=(
        "human review: Arkles p23 prints 250PFC for mark BF1. The token's 259 matches no "
        "printed designation, and the live 250PFC row is a different number"
    ),
    note="a capture-side misread of the printed 250PFC on the same member",
)

# --- E: the drawing's own evidence already picks out one existing live row -----
_PIN_150X75PFC = CandidatePin(
    raw_token="150X75PFC",
    family_code="PFC",
    pick_values=(150.0, 75.0),
    stated=(
        StatedValue("depth", 150.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '150X75PFC' (p7 mark 007X)"),
        StatedValue("flange_width", 75.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '150X75PFC' (p7 mark 007X)"),
        StatedValue("depth", 150.0, FROM_PRIOR_MILESTONE,
                    "accepted E1-enriched row 150PFC (live geometry)"),
        StatedValue("flange_width", 75.0, FROM_PRIOR_MILESTONE,
                    "accepted E1-enriched row 150PFC (live geometry)"),
    ),
    members=(
        _selby_member(7, "007X", "150X75PFC", ("150X75PFC",),
                      "007X 150X75PFC 300 2858 1 1.68 50.6", _mass(50.6, 2858.0)),
    ),
    prior_rows=("150PFC",),
    expected=CLASS_ALREADY_COVERED,
    next_action=(
        "add nothing: 150PFC is the only live PFC row whose recorded depth is 150 and whose "
        "recorded flange width is 75, and the drawing's own printed mass (50.6 kg over "
        "2858 mm) restates to 17.70 kg/m against that row's recorded 17.7 kg/m"
    ),
    note="the milestone required this to be MEASURED, not assumed: the row is picked out by "
         "both printed values, not by the PFC family name",
)

_PIN_150x75PFC = replace(
    _PIN_150X75PFC,
    raw_token="150x75PFC",
    members=(
        _selby_member(22, "022X", "150x75PFC", ("150X75PFC",),
                      "022X 150X75PFC 300 3150 1 1.86 55.8", _mass(55.8, 3150.0)),
    ),
    note="variant spelling on a further page: the printed mass restates to 17.71 kg/m — the "
         "same row, measured independently",
)

_PIN_75x60SHS = CandidatePin(
    raw_token="75x6.0SHS",
    family_code="SHS",
    pick_values=(75.0, 6.0),
    stated=(
        StatedValue("width", 75.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '75X6.0SHS' (p2 mark 002)"),
        StatedValue("thickness", 6.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '75X6.0SHS' (p2 mark 002)"),
    ),
    members=(
        _selby_member(2, "002", "75x6.0SHS", ("75X6.0SHS",),
                      "002 75X6.0SHS 300 2603 1 0.74 31.2", _mass(31.2, 2603.0)),
        _selby_member(3, "003", "75x6.0SHS", ("75X6.0SHS",),
                      "003 75X6.0SHS 300 2773 1 0.79 33.3", _mass(33.3, 2773.0)),
        _selby_member(4, "004", "75x6.0SHS", ("75X6.0SHS",),
                      "004 75X6.0SHS 300 2773 1 0.79 33.3", _mass(33.3, 2773.0)),
    ),
    prior_rows=(),
    expected=CLASS_ALREADY_COVERED,
    next_action=(
        "add nothing: 75x75x6.0SHS is the only live SHS row whose recorded width is 75 and "
        "whose recorded thickness is 6.0, and the drawing's printed masses restate to "
        "12.0 kg/m against that row's recorded 12.5 kg/m"
    ),
    note="the accepted local catalogue carries no SHS row for this plate, so the live row is "
         "the evidence and the value pick is what proves it is the right one",
)

_PIN_89x5SHS = CandidatePin(
    raw_token="89x5 SHS",
    family_code="SHS",
    pick_values=(89.0, 5.0),
    stated=(
        StatedValue("width", 89.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles post schedule 'P1 89x5 SHS' (p6/p8/p9/p10/p15/p21)"),
        StatedValue("thickness", 5.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles post schedule 'P1 89x5 SHS' (p6/p8/p9/p10/p15/p21)"),
    ),
    members=(
        _arkles_member(6, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _arkles_member(8, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _arkles_member(9, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _arkles_member(10, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _arkles_member(15, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_P15_RUN),
        _arkles_member(21, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _project_member(6, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _project_member(8, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _project_member(10, "P1", "89x5 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
        _project_member(15, "P1 89x5 SHS", "89x5 SHS", ("89x5 SHS",), ARKLES_P15_RUN),
    ),
    prior_rows=(),
    expected=CLASS_ALREADY_COVERED,
    next_action=(
        "add nothing: 89x89x5.0SHS is the only live SHS row that carries both numbers the "
        "drawing prints for this post (89 and 5). The drawing prints no weight for it, so the "
        "pick rests on the printed designation alone"
    ),
    note="the drawing's own post schedule prints this designation on six plans, and the live "
         "workload persisted the same member on four of them",
)

_PIN_89x89SHS = CandidatePin(
    raw_token="89x89 SHS",
    family_code="SHS",
    pick_values=(89.0, 5.0),
    stated=(
        StatedValue("width", 89.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles p9 post schedule 'P1 89x5 SHS' — the drawing's designation for the "
                    "member whose workload row reads 89x89 SHS"),
        StatedValue("thickness", 5.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles p9 post schedule 'P1 89x5 SHS'"),
    ),
    members=(
        _project_member(9, "P1", "89x89 SHS", ("89x5 SHS",), ARKLES_POSTS_RUN),
    ),
    prior_rows=(),
    expected=CLASS_ALREADY_COVERED,
    next_action=(
        "add nothing: the drawing prints 89x5 SHS for this member, and 89x89x5.0SHS is the "
        "only live row carrying both printed numbers. The token's second 89 is a capture "
        "misread of the printed thickness"
    ),
    note="the captured token is NOT what the drawing prints. The drawing's designation and "
         "the unique row it picks are both recorded here, so the classification rests on the "
         "print rather than on the misread",
)

_PIN_90X10EA = CandidatePin(
    raw_token="90X10EA",
    family_code="EA",
    pick_values=(90.0, 10.0),
    stated=(
        StatedValue("leg_size", 90.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '90X10EA' (p6 mark CL004)"),
        StatedValue("thickness", 10.0, FROM_PRINTED_SIZE_CELL,
                    "Selby shop material list SIZE cell '90X10EA' (p6 mark CL004)"),
        StatedValue("width", 90.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 90X10EA (fully traced; the local row's own "
                    "field name for the leg)"),
        StatedValue("thickness", 10.0, FROM_PRIOR_MILESTONE,
                    "accepted Milestone C row 90X10EA (fully traced)"),
    ),
    members=(
        _selby_member(6, "CL004", "90X10EA", ("90X10EA",),
                      "CL004 90X10EA 300 165 1 0.06 2.1", _mass(2.1, 165.0)),
    ),
    prior_rows=("90X10EA",),
    expected=CLASS_ALREADY_COVERED,
    next_action=(
        "add nothing: 90x90x10EA is the only live EA row whose recorded leg size is 90 and "
        "whose recorded thickness is 10, and the drawing's printed mass restates to "
        "12.7 kg/m against that row's recorded 13.3 kg/m"
    ),
    note="the drawing's label states the leg 90 and the thickness 10; the live row records "
         "them as leg_size and thickness, the accepted local row as width and thickness. Both "
         "records are reported as they name themselves",
)

_PIN_90x10EA = replace(
    _PIN_90X10EA,
    raw_token="90x10EA",
    members=(
        _selby_member(5, "CL004", "90x10EA", ("90X10EA",),
                      "CL004 90X10EA 300 165 1 0.06 2.1", _mass(2.1, 165.0)),
        _selby_member(31, "CL002", "90x10EA", ("90X10EA",),
                      "CL002 90X10EA 300 133 1 0.05 1.7", _mass(1.7, 133.0)),
    ),
    note="variant spelling: two further members print the same SIZE cell, one on another page; "
         "both printed masses restate to 12.7 and 12.8 kg/m",
)

_PIN_200UC = CandidatePin(
    raw_token="200UC",
    family_code="UC",
    pick_values=(200.0, 46.0),
    stated=(
        StatedValue("designation_series", 200.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles p10 post schedule 'P3 200UC46'"),
        StatedValue("designation_weight", 46.0, FROM_PRINTED_SCHEDULE_DESIGNATION,
                    "Arkles p10 post schedule 'P3 200UC46'"),
    ),
    members=(
        _arkles_member(10, "P2", "200UC", ("250PFC",), ARKLES_POSTS_RUN),
        _project_member(10, "P3", "200UC", ("200UC46",), ARKLES_POSTS_RUN),
    ),
    prior_rows=(),
    expected=CLASS_ALREADY_COVERED,
    next_action=(
        "add nothing: the drawing prints 200UC46 for the member whose capture row reads "
        "200UC, and the production matcher already resolves that printed designation to the "
        "existing live row 200UC46.2. The token is a truncation of the print"
    ),
    note="the P2 member's printed designation is 250PFC and is excluded by the family check; "
         "only a printed designation belonging to this token's own family may cover it",
)

CANDIDATE_PINS = (
    # A
    _PIN_130X12FL, _PIN_130x12FL, _PIN_180X20FL, _PIN_180x20FL,
    _PIN_250X12FL, _PIN_300x8FL, _PIN_90x10FL,
    # B
    _PIN_200UB30,
    # C
    _PIN_250X90PFC, _PIN_250x90PFC,
    # D
    _PIN_110UB48, _PIN_290UB37, _PIN_310UB42, _PIN_23UB37,
    _PIN_250UC46, _PIN_1200PFC, _PIN_259PFC,
    # E
    _PIN_150X75PFC, _PIN_150x75PFC, _PIN_75x60SHS, _PIN_89x5SHS,
    _PIN_89x89SHS, _PIN_90X10EA, _PIN_90x10EA, _PIN_200UC,
)

ALL_PINS = CANDIDATE_PINS
PIN_BY_TOKEN = {pin.raw_token: pin for pin in ALL_PINS}
EXPECTED_CLASSIFICATIONS = {pin.raw_token: pin.expected for pin in ALL_PINS}

# The Selby shop material list's own rows, printed, used as the peer reference for
# the derived volume screen. Every one is quantity-1 unless noted; the quantity-2
# row's printed mass is a total for two pieces, which the screen divides back out.
SELBY_PRINTED_REFERENCES = (
    PrintedReferenceRow(SELBY.name, 13, "PL001", "75X8FL", 237.0, 1, 1.1,
                        "PL001 75X8FL 300 237 1 0.04 1.1"),
    PrintedReferenceRow(SELBY.name, 9, "PL016", "75X8FL", 300.0, 2, 2.8,
                        "PL016 75X8FL 300 300 2 0.10 2.8"),
    PrintedReferenceRow(SELBY.name, 13, "PL008", "180X20FL", 340.0, 1, 9.6,
                        "PL008 180X20FL 300 340 1 0.14 9.6"),
    PrintedReferenceRow(SELBY.name, 1, "PL028", "250X12FL", 155.0, 1, 3.7,
                        "PL028 250X12FL 300 155 1 0.09 3.7"),
    PrintedReferenceRow(SELBY.name, 3, "PL030", "130X12FL", 225.0, 1, 2.8,
                        "PL030 130X12FL 300 225 1 0.07 2.8"),
    PrintedReferenceRow(SELBY.name, 5, "CL004", "90X10EA", 165.0, 1, 2.1,
                        "CL004 90X10EA 300 165 1 0.06 2.1"),
    PrintedReferenceRow(SELBY.name, 31, "CL002", "90X10EA", 133.0, 1, 1.7,
                        "CL002 90X10EA 300 133 1 0.05 1.7"),
)

# ===========================================================================
# The report's own vocabulary — 14 items per candidate
# ===========================================================================
REPORT_ITEMS = (
    "1. drawing token",
    "2. normalised token",
    "3. section family",
    "4. Step-E resolution",
    "5. existing live catalogue exact matches",
    "6. existing live catalogue near candidates",
    "7. genuine drawing evidence",
    "8. repository/reference evidence",
    "9. all known dimensions",
    "10. all known weight values",
    "11. source or derived",
    "12. conflicts",
    "13. final evidence classification",
    "14. recommended next action",
)

REPORT_ITEM_COUNT = len(REPORT_ITEMS)


# ===========================================================================
# Measuring the evidence — pure functions over rows that were handed in
# ===========================================================================
def _family_rows(rows, family: str | None) -> tuple[dict, ...]:
    return tuple(row for row in rows if row.get("family") == family)


def _geometry_values(row: dict) -> tuple[float, ...]:
    """The values the row records in the geometry-defining fields, in field order."""
    values = []
    for field in GEOMETRY_DEFINING_FIELDS:
        value = row.get(field)
        if value is not None:
            values.append(float(value))
    return tuple(values)


def _carried_values(row: dict) -> tuple[float, ...]:
    """
    Every number a row carries: the numbers in its name and its recorded geometry.
    A recorded weight is deliberately NOT included — a weight is not a dimension,
    and a token must not be reconciled because a mass happens to match a number.
    """
    return _numbers(row.get("name", "")) + _geometry_values(row)


def _carries(row: dict, values) -> bool:
    carried = _carried_values(row)
    return all(any(_same(value, found) for found in carried) for value in values)


def _carry_rows(rows, family: str | None, values) -> tuple[dict, ...]:
    """Rows of the family that carry every value. Empty values carry nothing."""
    if not values:
        return ()
    return tuple(row for row in rows if row.get("family") == family and _carries(row, values))


def _row_name(row: dict) -> str:
    return str(row.get("name", ""))


def _row_by_name(rows, name: str):
    for row in rows:
        if _row_name(row) == name:
            return row
    return None


def _field_differences(left_row: dict, right_row: dict) -> tuple[tuple[str, object, object], ...]:
    """
    Geometry (and recorded weight) fields on which two records of the same section
    disagree. Only field names BOTH records use are compared; a family whose two
    records name the same role differently is not compared by role, and that
    limitation is stated in the report rather than papered over.
    """
    differences = []
    for field in MILESTONE_CONFLICT_FIELDS:
        left = left_row.get(field)
        right = right_row.get(field)
        if left is None or right is None:
            continue
        if not _same(left, right):
            differences.append((field, left, right))
    return tuple(differences)


def _reference_weight(carried_rows) -> tuple[float, str] | None:
    """The weight the reference rows themselves record, if exactly one records one."""
    recorded = [(row, float(row["weight_per_metre"]))
                for row in carried_rows if row.get("weight_per_metre") is not None]
    if len(recorded) != 1:
        return None
    row, weight = recorded[0]
    return weight, f"live row {_row_name(row)!r} records weight_per_metre {weight:g} kg/m"


def _restated_kg_per_m(mass: PrintedMass) -> float:
    """DERIVED: the drawing's printed kilograms over the drawing's printed length."""
    return mass.mass_kg / (mass.length_mm / 1000.0)


def _mass_screen(masses, reference) -> tuple[str, str, tuple]:
    """
    Compare the drawing's printed masses — restated per metre — against a weight the
    reference rows record. DERIVED, and it can only demote: a disagreement never
    promotes anything.
    """
    if not masses:
        return SCREEN_NO_PRINTED_MASS, "the drawing prints no weight for this member", ()
    derived = tuple(
        (DERIVED_RESTATEMENT, _restated_kg_per_m(mass),
         f"printed {mass.mass_kg} kg / printed {mass.length_mm:g} mm ({mass.evidence!r})")
        for mass in masses
    )
    if reference is None:
        return (SCREEN_NO_REFERENCE_WEIGHT,
                "no reference row records a weight for the section this candidate names",
                derived)
    weight, detail = reference
    low, high = weight * (1 - MASS_SCREEN_TOLERANCE), weight * (1 + MASS_SCREEN_TOLERANCE)
    for _kind, value, formula in derived:
        if not low <= value <= high:
            return (SCREEN_DISAGREES,
                    f"printed mass restates to {value:.3f} kg/m against the recorded "
                    f"{weight:g} kg/m +-{MASS_SCREEN_TOLERANCE:.0%} ({low:.3f}-{high:.3f}); "
                    f"{detail}; {formula}",
                    derived)
    return (SCREEN_AGREES,
            f"every printed mass restates within +-{MASS_SCREEN_TOLERANCE:.0%} of the "
            f"recorded {weight:g} kg/m; {detail}",
            derived)


def _printed_size_volume(printed, length_mm):
    """width x thickness x length for a plate's printed size. None when not a pair."""
    if len(printed) != 2:
        return None
    width, thickness = printed
    return width * thickness * length_mm


def _volume_proxy_screen(pin: CandidatePin, references) -> tuple[str, str, tuple]:
    """
    The drawing's own peers, as a self-consistency screen: a plate's printed weight
    per printed cubic millimetre of its printed size must sit in the same band as
    the drawing's other printed rows of the same family. Only the DRAWING's own rows
    are used, so no external standard and no second source is assumed.

    DERIVED, and it can only demote.
    """
    if pin.family_code not in ("FL", "EA"):
        return (SCREEN_NOT_APPLICABLE,
                f"a {pin.family_code} section is not a plate, so its printed size is not a "
                "printed volume", ())
    peers = [row for row in references
             if row.drawing == SELBY.name and row.size.upper().endswith(pin.family_code)]
    if not peers:
        return SCREEN_NO_REFERENCE_WEIGHT, "the drawing prints no peer rows for this family", ()

    derived = []
    band = []
    for row in peers:
        size = tuple(_numbers(row.size))
        volume = _printed_size_volume(size, row.length_mm)
        if not volume:
            continue
        proxy = row.mass_kg / (volume * row.quantity)
        band.append(proxy)
        derived.append((DERIVED_VOLUME_PROXY, proxy,
                        f"printed {row.mass_kg} kg / (printed size {row.size} x "
                        f"{row.length_mm:g} mm x qty {row.quantity}) ({row.run!r})"))
    if not band:
        return (SCREEN_NO_REFERENCE_WEIGHT,
                "the drawing's peer rows state no usable printed size", tuple(derived))

    low, high = min(band), max(band)
    for member in pin.members:
        if member.mass is None:
            continue
        volume = _printed_size_volume(pin.pick_values, member.mass.length_mm)
        if not volume:
            continue
        proxy = member.mass.mass_kg / (volume * member.mass.quantity)
        derived.append((DERIVED_VOLUME_PROXY, proxy,
                        f"printed {member.mass.mass_kg} kg / (printed size "
                        + "x".join(f"{value:g}" for value in pin.pick_values)
                        + f" x {member.mass.length_mm:g} mm x qty {member.mass.quantity}) "
                        f"({member.mass.evidence!r})"))
        if not low * (1 - MASS_SCREEN_TOLERANCE) <= proxy <= high * (1 + MASS_SCREEN_TOLERANCE):
            return (SCREEN_DISAGREES,
                    f"printed weight per printed volume {proxy:.4g} kg/mm3 falls outside the "
                    f"drawing's own printed rows for this family "
                    f"({low:.4g}-{high:.4g} kg/mm3, +-{MASS_SCREEN_TOLERANCE:.0%})",
                    tuple(derived))
    return (SCREEN_AGREES,
            f"printed weight per printed volume agrees with the drawing's own printed rows "
            f"for this family ({low:.4g}-{high:.4g} kg/mm3)",
            tuple(derived))


# ===========================================================================
# What the audit measured about one candidate
# ===========================================================================
@dataclass(frozen=True)
class EvidenceFacts:
    raw_token: str
    normalised_token: str
    family_code: str
    family_catalogue_name: str
    step_e_resolution: str
    step_e_reason: str
    step_e_family_rows: int
    step_e_sharing: tuple[str, ...]
    stated_values: tuple[StatedValue, ...]
    printed_designations: tuple[str, ...]
    printed_runs: tuple[str, ...]
    members: tuple[SourceMember, ...]
    printed_masses: tuple[PrintedMass, ...]
    token_is_printed: bool
    live_exact_matches: tuple[str, ...]
    live_carriers: tuple[str, ...]
    live_carrier_weights: tuple[tuple[str, float | None], ...]
    live_near_candidates: tuple[tuple[str, float | None], ...]
    accepted_row_fields: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]
    accepted_rows_stating_the_values: tuple[str, ...]
    conflicting_rows: tuple[str, ...]
    conflict_fields: tuple[tuple[str, object, object], ...]
    conflict_detail: str
    coverage_basis: str
    coverage_row: str | None
    coverage_resolution: str | None
    coverage_detail: str
    derived_values: tuple[tuple[str, float, str], ...]
    screen_verdict: str
    screen_detail: str


@dataclass(frozen=True)
class CandidateReconciliation:
    """One candidate, its measured facts, and the classification those facts force."""
    pin: CandidatePin
    facts: EvidenceFacts

    @property
    def classification(self) -> str:
        return classify(self.facts)

    @property
    def letter(self) -> str:
        return CLASSIFICATION_LETTERS[self.classification]

    @property
    def normalised_token(self) -> str:
        return self.facts.normalised_token

    @property
    def summary(self) -> str:
        return f"{self.letter} {self.classification}: {self.pin.raw_token!r}"

    def item(self, number: int) -> str:
        """Item `number` of the milestone's 14-item per-candidate report."""
        if not 1 <= number <= REPORT_ITEM_COUNT:
            raise ValueError(f"this report has {REPORT_ITEM_COUNT} items, not {number}")
        facts = self.facts
        if number == 1:
            seen, parts = set(), []
            for member in self.pin.members:
                key = (member.drawing, member.page, member.mark)
                if key in seen:
                    continue
                seen.add(key)
                parts.append(f"{member.drawing} p{member.page} mark {member.mark!r}")
            return (f"{self.pin.raw_token!r} — {len(self.pin.members)} genuine capture row(s): "
                    + "; ".join(parts))
        if number == 2:
            return self.normalised_token
        if number == 3:
            return (f"{facts.family_code} (live catalogue family "
                    f"{facts.family_catalogue_name!r}, {facts.step_e_family_rows} rows)")
        if number == 4:
            return f"{facts.step_e_resolution} — {facts.step_e_reason}"
        if number == 5:
            if facts.live_exact_matches:
                return ", ".join(repr(name) for name in facts.live_exact_matches)
            return "none — no live row carries this token as its name"
        if number == 6:
            near = ", ".join(
                f"{name!r}" if weight is None else f"{name!r} (weight {weight:g})"
                for name, weight in facts.live_near_candidates
            ) or "none"
            sharing = ", ".join(repr(name) for name in facts.step_e_sharing) or "none"
            return (f"same-family rows carrying one of the stated numbers: {near}; Step E's "
                    f"shared-leading-dimension rows: {sharing}")
        if number == 7:
            lines = []
            for member in self.pin.members:
                if not member.printed_run:
                    continue
                verbatim = ", ".join(repr(text) for text in member.printed)
                printed = (f"prints {verbatim}" if member.print_states_the_token
                           else f"prints {verbatim} — NOT this token")
                mass = "" if member.mass is None else f"; printed weight {member.mass.label}"
                lines.append(f"{member.reference} {printed}{mass}: {member.printed_run!r}")
            return " | ".join(lines) if lines else \
                "no printed text-layer evidence was recorded for this candidate"
        if number == 8:
            parts = []
            if self.pin.prior_rows:
                parts.append("accepted prior-milestone rows cited: "
                             + ", ".join(repr(name) for name in self.pin.prior_rows))
            else:
                parts.append("accepted prior-milestone rows cited: none")
            parts.append(f"live rows carrying every stated value: "
                         f"{', '.join(repr(n) for n in facts.live_carriers) or 'none'}")
            if facts.coverage_row:
                parts.append(f"coverage route: {facts.coverage_basis} via "
                             f"{facts.coverage_row!r}")
            elif facts.coverage_basis == COVERAGE_NONE and facts.conflicting_rows:
                parts.append("coverage route: suppressed by the measured conflict")
            if facts.source_states_the_token_text:
                parts.append("the drawing's own text states this token")
            else:
                parts.append("the drawing's own text does NOT state this token")
            return "; ".join(parts)
        if number == 9:
            if not facts.stated_values:
                return "none — no dimension is stated about this section by any source"
            return " | ".join(value.render() for value in facts.stated_values
                              if "weight" not in value.field)
        if number == 10:
            parts = []
            for name, fields in facts.accepted_row_fields:
                weights = [f"{value:g}" for field, value in fields
                           if field == "weight_per_metre"]
                if weights:
                    parts.append(
                        f"accepted prior-milestone row {name!r} records {weights[0]} kg/m"
                    )
            for member in self.pin.members:
                if member.mass is not None:
                    parts.append(f"the drawing prints {member.mass.label} "
                                 f"({member.mass.evidence!r})")
            for name, weight in facts.live_carrier_weights:
                if weight is not None:
                    parts.append(f"live row {name!r} records {weight:g} kg/m")
            for value in facts.stated_values:
                if value.field == "designation_weight":
                    parts.append(
                        f"the printed designation's own number {value.value:g} (a "
                        "naming-convention reading of the designation, not a recorded weight)"
                    )
            return " | ".join(parts) if parts else \
                "none — no weight is recorded for this section"
        if number == 11:
            stated = [f"{value.render()}" for value in facts.stated_values]
            derived = [f"{kind}: {value:.4g} ({formula})"
                       for kind, value, formula in facts.derived_values]
            return ("stated by a source: " + (" | ".join(stated) if stated else "none")
                    + "  ||  derived — never a source of identity: "
                    + (" | ".join(derived) if derived else "none"))
        if number == 12:
            parts = []
            if facts.conflict_fields:
                parts.append("accepted records disagree: " + "; ".join(
                    f"{field}: {left!r} vs {right!r}"
                    for field, left, right in facts.conflict_fields))
            if facts.conflict_detail:
                parts.append(facts.conflict_detail)
            parts.append(f"self-consistency screen: {facts.screen_verdict} — "
                         f"{facts.screen_detail}")
            return " | ".join(parts)
        if number == 13:
            return f"{self.letter} {self.classification}"
        return self.pin.next_action

    def render(self) -> str:
        lines = ["=" * 78, self.summary, "=" * 78]
        lines.extend(f"  {label:<48} {self.item(number)}"
                     for number, label in enumerate(REPORT_ITEMS, start=1))
        if self.pin.note:
            lines.append(f"  note: {self.pin.note}")
        return "\n".join(lines)


# `source_states_the_token_text` is the same measurement as the pin's — exposed on
# the facts so the report can state it without reaching back into the pin.
def _source_states_the_token(facts: EvidenceFacts) -> bool:
    return facts.token_is_printed


EvidenceFacts.source_states_the_token_text = property(_source_states_the_token)


# ===========================================================================
# The classifier — ordered rules, each one measured
# ===========================================================================
def _rule_conflicting(facts: EvidenceFacts) -> str | None:
    """
    C. Two accepted records of the same section state different values. A conflict
    suppresses any coverage claim: the section's identity is exactly what is in
    dispute, so no row can be added on it until a human resolves it.
    """
    if facts.conflicting_rows:
        return CLASS_CONFLICTING
    return None


def _rule_already_covered(facts: EvidenceFacts) -> str | None:
    """
    E. What the drawing itself provides picks out exactly one existing live row of
    the token's own family, and that row carries every number the token claims.
    Family identity is required and a shared leading dimension is not enough —
    this is the rule the milestone's 310UB42 and 250UC46 examples exist to
    constrain.
    """
    if facts.coverage_basis == COVERAGE_NONE:
        return None
    return CLASS_ALREADY_COVERED


def _rule_exactly_supported(facts: EvidenceFacts) -> str | None:
    """
    A. Nothing existing covers it, but the drawing prints this designation, an
    accepted prior-milestone row states the same identifying values, and the
    drawing's printed record does not contradict itself.
    """
    if not facts.token_is_printed:
        return None
    if not facts.accepted_rows_stating_the_values:
        return None
    for name, fields in facts.accepted_row_fields:
        recorded = dict(fields)
        for value in facts.stated_values:
            if not value.is_stated:
                continue
            if value.provenance == FROM_TOKEN_SPELLING_ONLY:
                continue
            if value.field not in recorded:
                continue
            if not _same(recorded[value.field], value.value):
                return None
    if facts.screen_verdict == SCREEN_DISAGREES:
        return None
    return CLASS_EXACTLY_SUPPORTED


def _rule_partial(facts: EvidenceFacts) -> str | None:
    """
    B. Something is stated but not enough: the drawing prints this designation and
    nothing existing can be reconciled with it, or the printed record disagrees
    with itself. A human decides.
    """
    if facts.token_is_printed:
        return CLASS_PARTIAL
    if facts.screen_verdict == SCREEN_DISAGREES:
        return CLASS_PARTIAL
    if facts.conflict_fields:
        return CLASS_PARTIAL
    return None


def _rule_no_sufficient_evidence(facts: EvidenceFacts) -> str | None:
    """
    D. The drawing does not print this designation, no existing row is picked out by
    anything it states, and no accepted record states its values. The token exists
    only in a capture, so there is nothing a row could be added from.
    """
    return CLASS_NO_EVIDENCE


CLASSIFIER_RULES = (
    _rule_conflicting,
    _rule_already_covered,
    _rule_exactly_supported,
    _rule_partial,
    _rule_no_sufficient_evidence,
)


def classify(facts: EvidenceFacts) -> str:
    """The first rule that fires, in order. The last rule is unconditional."""
    for rule in CLASSIFIER_RULES:
        verdict = rule(facts)
        if verdict is not None:
            return verdict
    raise AssertionError("unreachable: the final classifier rule is unconditional")


# ===========================================================================
# Building the audit
# ===========================================================================
def _coverage_claim(pin: CandidatePin, matcher, rows) -> tuple[str, str | None, str | None, str]:
    """
    The only two routes by which an existing row may be said to cover the token:

      * the values the DRAWING states, when exactly one live row of the token's own
        family carries them all;
      * a designation the DRAWING prints, resolved by the production matcher.

    Both carry the same guard: the row must carry EVERY number the token claims. A
    family match, a suffix fallback and a shared leading dimension are each, on
    their own, not an identity.
    """
    carried = _carry_rows(rows, pin.family_catalogue_name, pin.pick_values)
    if len(carried) == 1:
        row = carried[0]
        if _carries(row, _numbers(pin.raw_token)):
            return (COVERAGE_VALUE_AGREEMENT, _row_name(row), None,
                    f"the drawing states {' and '.join(f'{v:g}' for v in pin.pick_values)} and "
                    f"exactly one live {pin.family_code} row carries them: {_row_name(row)!r}")
        return (COVERAGE_NONE, None, None,
                f"exactly one live row carries the drawing's stated values "
                f"({_row_name(row)!r}) but it does not carry every number of the token "
                f"{pin.raw_token!r}")
    if carried:
        names = ", ".join(repr(_row_name(row)) for row in carried)
        return (COVERAGE_NONE, None, None,
                f"more than one live row carries the drawing's stated values ({names}), so "
                "the values alone do not identify a section")

    for designation in pin.printed_designations:
        match = matcher.resolve(designation)
        row = match.catalogue_row
        if row is None:
            continue
        if row.get("family") != pin.family_catalogue_name:
            continue
        if not _carries(row, _numbers(pin.raw_token)):
            continue
        return (COVERAGE_MATCHER_RESOLUTION, _row_name(row), match.resolution,
                f"the drawing itself prints {designation!r} for this member and the production "
                f"matcher resolves it {match.resolution} to {_row_name(row)!r}, which carries "
                f"every number of the token {pin.raw_token!r}")
    seen = ", ".join(repr(text) for text in pin.printed_designations) or "nothing"
    return (COVERAGE_NONE, None, None,
            "no live row carries the drawing's stated values, and the production matcher "
            f"resolves none of the designations the drawing prints ({seen}) to a row of this "
            f"family carrying the token's numbers")


def build_facts(pin: CandidatePin, matcher, step_e_report, rows, catalogue,
                references=SELBY_PRINTED_REFERENCES) -> EvidenceFacts:
    """Measure everything this audit may say about one candidate."""
    audited = next(token for token in step_e_report.tokens if token.raw_token == pin.raw_token)
    family_rows = _family_rows(rows, pin.family_catalogue_name)

    basis, coverage_row, coverage_resolution, coverage_detail = _coverage_claim(
        pin, matcher, rows
    )
    carried = _carry_rows(rows, pin.family_catalogue_name, pin.pick_values)

    accepted_row_fields = []
    accepted_rows_stating_the_values = []
    for name in pin.prior_rows:
        row = catalogue.get(name)
        if row is None:
            continue
        recorded = tuple((field, float(row[field])) for field in MILESTONE_CONFLICT_FIELDS
                         if row.get(field) is not None)
        accepted_row_fields.append((name, recorded))
        if pin.pick_values and _carries(dict(row), pin.pick_values):
            accepted_rows_stating_the_values.append(name)

    conflicting_rows, conflict_fields, details = [], [], []
    for name, _fields in accepted_row_fields:
        prior = catalogue.get(name)
        if prior is None:
            continue
        for row in carried:
            differences = _field_differences(dict(prior), row)
            if not differences:
                continue
            conflicting_rows.append(_row_name(row))
            for difference in differences:
                if difference not in conflict_fields:
                    conflict_fields.append(difference)
            details.append(
                f"prior-milestone row {name!r} and live row {_row_name(row)!r} are both "
                "records of this section and disagree on "
                + ", ".join(f"{field} ({left!r} vs {right!r})"
                            for field, left, right in differences)
            )
    conflict_detail = "; ".join(details)

    if conflict_fields:
        coverage_basis, coverage_row, coverage_resolution = COVERAGE_NONE, None, None
        coverage_detail = ("suppressed by the measured conflict: " + conflict_detail)

    live_exact = tuple(_row_name(row) for row in rows if _row_name(row) == pin.raw_token)
    near = []
    for row in family_rows:
        if row.get("weight_per_metre") is None:
            continue
        if not pin.pick_values:
            continue
        if _carries(row, pin.pick_values):
            continue
        if any(any(_same(value, number) for number in _carried_values(row))
               for value in pin.pick_values):
            near.append((_row_name(row), float(row["weight_per_metre"])))
    near.sort()

    reference = _reference_weight(carried)
    screen_verdict, screen_detail, derived = _mass_screen(pin.printed_masses, reference)
    if screen_verdict == SCREEN_NO_REFERENCE_WEIGHT:
        proxy_verdict, proxy_detail, proxy_derived = _volume_proxy_screen(pin, references)
        derived = derived + tuple(proxy_derived)
        if proxy_verdict != SCREEN_NOT_APPLICABLE:
            screen_verdict, screen_detail = proxy_verdict, proxy_detail

    return EvidenceFacts(
        raw_token=pin.raw_token,
        normalised_token=pin.normalised_token,
        family_code=pin.family_code,
        family_catalogue_name=pin.family_catalogue_name,
        step_e_resolution=audited.resolution,
        step_e_reason=audited.reason or "resolved",
        step_e_family_rows=len(family_rows),
        step_e_sharing=step_e_sharing_of(step_e_report, pin.raw_token),
        stated_values=pin.stated,
        printed_designations=pin.printed_designations,
        printed_runs=pin.printed_runs,
        members=pin.members,
        printed_masses=pin.printed_masses,
        token_is_printed=pin.token_is_printed,
        live_exact_matches=live_exact,
        live_carriers=tuple(_row_name(row) for row in carried),
        live_carrier_weights=tuple(
            (_row_name(row),
             None if row.get("weight_per_metre") is None else float(row["weight_per_metre"]))
            for row in carried
        ),
        live_near_candidates=tuple(near),
        accepted_row_fields=tuple(accepted_row_fields),
        accepted_rows_stating_the_values=tuple(accepted_rows_stating_the_values),
        conflicting_rows=tuple(sorted(set(conflicting_rows))),
        conflict_fields=tuple(conflict_fields),
        conflict_detail=conflict_detail,
        coverage_basis=basis,
        coverage_row=coverage_row,
        coverage_resolution=coverage_resolution,
        coverage_detail=coverage_detail,
        derived_values=derived,
        screen_verdict=screen_verdict,
        screen_detail=screen_detail,
    )


def step_e_sharing_of(step_e_report, raw_token: str) -> tuple[str, ...]:
    for token, _family, _rows, sharing in step_e_report.reconciliation_candidates:
        if token == raw_token:
            return tuple(sharing)
    return ()


@dataclass(frozen=True)
class EvidenceReconciliationAudit:
    """Every candidate, classified, over one fixed evidence set."""
    rows: tuple[dict, ...]
    live_row_count: int
    live_digest: str
    reference_source_kind: str
    catalogue_digest: str
    corpus_size: int
    step_e_token_count: int
    step_e_counts: tuple[tuple[str, int], ...]
    candidates: tuple[CandidateReconciliation, ...]

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(candidate.pin.raw_token for candidate in self.candidates)

    def by_token(self, raw_token: str) -> CandidateReconciliation:
        for candidate in self.candidates:
            if candidate.pin.raw_token == raw_token:
                return candidate
        raise KeyError(raw_token)

    def by_classification(self, classification: str) -> tuple[CandidateReconciliation, ...]:
        return tuple(c for c in self.candidates if c.classification == classification)

    @property
    def counts_by_classification(self) -> dict:
        return {classification: len(self.by_classification(classification))
                for classification in CLASSIFICATIONS}

    @property
    def letters(self) -> str:
        return "".join(candidate.letter for candidate in self.candidates)

    def summary(self) -> str:
        counts = self.counts_by_classification
        parts = [f"{CLASSIFICATION_LETTERS[classification]}={counts[classification]}"
                 for classification in CLASSIFICATIONS]
        return f"{len(self.candidates)} candidates: " + " ".join(parts)

    def report(self) -> str:
        lines = [
            "=" * 78,
            "REAL-WORLD SECTION EVIDENCE RECONCILIATION AUDIT (Step F)",
            "=" * 78,
            f"live reference rows    : {self.live_row_count}",
            f"live reference digest  : {self.live_digest}",
            f"reference source kind  : {self.reference_source_kind}",
            f"local catalogue digest : {self.catalogue_digest}",
            f"Step E corpus          : {self.corpus_size} occurrences, "
            f"{self.step_e_token_count} tokens",
            "Step E resolutions     : " + ", ".join(f"{k} {v}" for k, v in self.step_e_counts),
            self.summary(),
            "",
            "No catalogue row is added, proposed or named by this report: it classifies "
            "evidence only.",
            "",
        ]
        for candidate in self.candidates:
            lines.append(candidate.render())
        return "\n".join(lines)


def build_reconciliation_audit(matcher, step_e_report, rows, corpus, pins=ALL_PINS,
                               catalogue=None) -> EvidenceReconciliationAudit:
    """
    Classify every candidate from the evidence it was handed.

    Refuses to run through anything but the genuine production matcher, refuses a
    Step E report that is not the genuine audit class measured over the same
    reference rows, and refuses a candidate set that is not Step E's own.
    """
    problems = audit_authority_violations(matcher)
    if problems:
        raise AuditAuthorityError(
            "refusing to reconcile section evidence through a non-production matcher: "
            + "; ".join(problems)
        )
    if not isinstance(step_e_report, coverage.SectionCoverageAudit):
        raise AuditAuthorityError(
            "refusing to reconcile against anything but the genuine Step E coverage audit: "
            "the candidate set and the resolutions must be the measured ones"
        )
    if step_e_report.reference_data_digest != LIVE_DIGEST:
        raise AuditAuthorityError(
            "refusing to reconcile against a Step E report measured over different reference "
            f"rows (digest {step_e_report.reference_data_digest!r}, expected {LIVE_DIGEST!r})"
        )

    pinned = tuple(pin.raw_token for pin in pins)
    steps = step_e_candidate_tokens(step_e_report)
    if set(pinned) != set(steps) or len(pinned) != len(set(pinned)):
        missing = sorted(set(steps) - set(pinned))
        invented = sorted(set(pinned) - set(steps))
        raise AuditAuthorityError(
            "refusing to reconcile a candidate set that is not Step E's measured missing-row "
            f"set (dropped: {missing}; invented: {invented})"
        )

    if catalogue is None:
        catalogue = importlib.import_module(LOCAL_CATALOGUE_MODULE).CATALOGUE
    rows = tuple(dict(row) for row in rows)
    candidates = tuple(
        CandidateReconciliation(
            pin=pin,
            facts=build_facts(pin, matcher, step_e_report, rows, catalogue),
        )
        for pin in pins
    )
    return EvidenceReconciliationAudit(
        rows=rows,
        live_row_count=len(rows),
        live_digest=str(step_e_report.reference_data_digest),
        reference_source_kind=str(step_e_report.reference_source_kind),
        catalogue_digest=str(importlib.import_module(LOCAL_CATALOGUE_MODULE).CATALOGUE_DIGEST),
        corpus_size=len(corpus),
        step_e_token_count=step_e_report.raw_token_count,
        step_e_counts=tuple(sorted(step_e_report.counts_by_resolution.items())),
        candidates=candidates,
    )


def reconciliation_violations(audit: EvidenceReconciliationAudit,
                              corpus: tuple[Occurrence, ...] = ()) -> tuple[str, ...]:
    """
    Every way this audit could be claiming more than the evidence supports. Empty
    means: every classification is the one the pinned evidence forces, every pinned
    member is a genuine occurrence, and no candidate claims coverage it cannot
    measure.
    """
    problems = []
    occurrences = {(occurrence.source, occurrence.page, occurrence.mark,
                    _normalise(occurrence.raw_token)) for occurrence in corpus}
    for candidate in audit.candidates:
        token = candidate.pin.raw_token
        facts = candidate.facts
        classification = candidate.classification
        if classification not in CLASSIFICATIONS:
            problems.append(f"{token}: unknown classification {classification!r}")
            continue
        if classification != candidate.pin.expected:
            problems.append(
                f"{token}: classified {classification!r}, the pinned evidence expects "
                f"{candidate.pin.expected!r}"
            )
        if not candidate.pin.members:
            problems.append(f"{token}: no genuine capture row is recorded — nothing to classify")
        for member in candidate.pin.members:
            key = (member.source, member.page, member.mark, _normalise(member.captured))
            if corpus and key not in occurrences:
                problems.append(
                    f"{token}: pinned member {member.reference} with token "
                    f"{member.captured!r} is not in the audited corpus"
                )
        if classification == CLASS_CONFLICTING:
            if not facts.conflict_fields:
                problems.append(f"{token}: classified CONFLICTING with no measured field conflict")
            if facts.coverage_row is not None:
                problems.append(
                    f"{token}: classified CONFLICTING while still claiming coverage via "
                    f"{facts.coverage_row!r} — a conflict must suppress the coverage claim"
                )
        elif classification == CLASS_ALREADY_COVERED:
            if not facts.coverage_row:
                problems.append(f"{token}: classified ALREADY_COVERED with no row named")
            else:
                row = _row_by_name(audit.rows, facts.coverage_row)
                if row is None:
                    problems.append(f"{token}: coverage row {facts.coverage_row!r} is not a "
                                    "live row")
                elif row.get("family") != candidate.pin.family_catalogue_name:
                    problems.append(
                        f"{token}: coverage row {facts.coverage_row!r} is family "
                        f"{row.get('family')!r}, not {candidate.pin.family_catalogue_name!r}"
                    )
                elif not _carries(row, _numbers(token)):
                    problems.append(
                        f"{token}: coverage row {facts.coverage_row!r} does not carry every "
                        "number the token claims"
                    )
        elif classification == CLASS_EXACTLY_SUPPORTED:
            if not facts.token_is_printed:
                problems.append(f"{token}: classified EXACTLY_SUPPORTED while the drawing does "
                                "not print this designation")
            if not facts.accepted_rows_stating_the_values:
                problems.append(f"{token}: classified EXACTLY_SUPPORTED with no accepted row "
                                "stating the stated values")
            if facts.screen_verdict == SCREEN_DISAGREES:
                problems.append(f"{token}: classified EXACTLY_SUPPORTED while the printed "
                                "record disagrees with itself")
            if facts.coverage_row is not None:
                problems.append(f"{token}: classified EXACTLY_SUPPORTED while an existing row "
                                f"already covers it ({facts.coverage_row!r})")
        elif classification == CLASS_NO_EVIDENCE:
            if facts.token_is_printed:
                problems.append(f"{token}: classified NO_SUFFICIENT_EVIDENCE while the "
                                "drawing's own text prints this designation")
            if facts.coverage_basis != COVERAGE_NONE:
                problems.append(f"{token}: classified NO_SUFFICIENT_EVIDENCE with a coverage "
                                f"basis {facts.coverage_basis!r}")
        elif classification == CLASS_PARTIAL:
            if not (facts.token_is_printed or facts.screen_verdict == SCREEN_DISAGREES
                    or facts.conflict_fields):
                problems.append(f"{token}: classified PARTIAL with nothing partial measured "
                                "about it")
        for value in facts.stated_values:
            if value.provenance not in PROVENANCES:
                problems.append(f"{token}: unknown value provenance {value.provenance!r}")
        if not candidate.pin.next_action:
            problems.append(f"{token}: no recommended next action")
    return tuple(problems)


# ===========================================================================
# Test doubles — the repository boundary, injected (no network, no live table)
# ===========================================================================
class _InjectedResult:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _InjectedQuery:
    def __init__(self, client):
        self._client = client

    def select(self, *columns):
        return self

    def execute(self):
        return _InjectedResult(copy.deepcopy(self._client.rows))


class _InjectedClient:
    """The reference rows are injected; nothing here opens a connection."""

    def __init__(self, rows):
        self.rows = list(rows)

    def table(self, name):
        return _InjectedQuery(self)


class _NetworkGuard:
    def table(self, name):  # pragma: no cover - only reachable on a test bug
        raise AssertionError(
            f"this audit may not touch a live Supabase client (asked for {name!r}): the "
            "reference rows are injected and the audit reports only what it was given"
        )


def _live_rows() -> list:
    if not SNAPSHOT_PATH.exists():
        pytest.skip(
            f"the captured live section evidence is not present at {SNAPSHOT_PATH} — this "
            "audit reconciles against the real reference rows or not at all"
        )
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256, (
        "the live catalogue capture at /tmp does not match the sha256 this audit pins — "
        "refusing to classify evidence against unverified reference data"
    )
    return json.loads(raw)


# ===========================================================================
# Fixtures
# ===========================================================================
@pytest.fixture(scope="module")
def modules():
    """
    The REAL production modules and the REAL local catalogue, imported under the
    test-only configuration boundary (app/config.py reads os.environ at import
    time), with the same restore-and-pop teardown the accepted tests use — the
    pre-existing SUPABASE_URL smoke-import baseline is neither fixed nor worsened.
    """
    saved = {key: os.environ.get(key) for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        section_matcher = importlib.import_module("app.engineering_data.section_matcher")
        rules = importlib.import_module("app.validation.rules")
        local_catalogue = importlib.import_module(LOCAL_CATALOGUE_MODULE)
        source_of_truth = importlib.import_module(SOURCE_OF_TRUTH_MODULE)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_client = section_matcher.supabase
    section_matcher.supabase = _NetworkGuard()
    try:
        yield SimpleNamespace(
            section_matcher=section_matcher,
            rules=rules,
            local_catalogue=local_catalogue,
            source_of_truth=source_of_truth,
        )
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


def _matcher_for(modules, rows):
    """A genuine SectionMatcher, built by its own constructor against these rows."""
    saved = modules.section_matcher.supabase
    modules.section_matcher.supabase = _InjectedClient(copy.deepcopy(rows))
    try:
        return modules.section_matcher.SectionMatcher()
    finally:
        modules.section_matcher.supabase = saved


@pytest.fixture(scope="module")
def real_matcher(modules, live_rows):
    return _matcher_for(modules, live_rows)


@pytest.fixture(scope="module")
def step_e_report(modules, live_rows, corpus):
    """Step E's own audit, measured in this process over the same pinned rows."""
    return build_audit(_matcher_for(modules, live_rows), corpus, modules.rules)


@pytest.fixture(scope="module")
def audit(modules, live_rows, corpus, step_e_report):
    return build_reconciliation_audit(
        _matcher_for(modules, live_rows), step_e_report, live_rows, corpus,
        catalogue=modules.local_catalogue.CATALOGUE,
    )


@pytest.fixture()
def build(modules, live_rows, corpus, step_e_report):
    """Build the audit against a genuine matcher, given the stated rows."""
    def _build(rows=None, pins=ALL_PINS):
        chosen = list(live_rows) if rows is None else list(rows)
        return build_reconciliation_audit(
            _matcher_for(modules, chosen), step_e_report, chosen, corpus, pins,
            catalogue=modules.local_catalogue.CATALOGUE,
        )
    return _build


# ===========================================================================
# The candidate set is Step E's, and nothing is dropped or invented
# ===========================================================================
class TestTheCandidateSetIsStepsOwn:
    def test_the_pinned_count_is_the_milestones_own(self):
        assert len(MILESTONE_NAMED_CANDIDATES) == 20
        assert len(ALL_PINS) == PINNED_CANDIDATE_COUNT
        assert len(set(ALL_PINS)) == PINNED_CANDIDATE_COUNT

    def test_the_pinned_tokens_are_exactly_steps_own(self, step_e_report):
        steps = set(step_e_candidate_tokens(step_e_report))
        assert set(PIN_BY_TOKEN) == steps
        assert len(step_e_report.reconciliation_candidates) == PINNED_CANDIDATE_COUNT

    def test_the_milestones_names_and_the_variant_spellings_partition_the_set(self, step_e_report):
        steps = set(step_e_candidate_tokens(step_e_report))
        named = set(MILESTONE_NAMED_CANDIDATES)
        variants = set(STEP_E_VARIANT_SPELLINGS)
        assert len(named) == 20 and len(variants) == 5
        assert named | variants == steps
        assert names_are_distinct(named, variants)

    def test_no_step_e_candidate_is_silently_dropped(self, audit, step_e_report):
        assert set(audit.tokens) == set(step_e_candidate_tokens(step_e_report))
        assert len(audit.candidates) == PINNED_CANDIDATE_COUNT

    def test_each_variant_spelling_is_the_lower_case_form_of_a_named_token(self, step_e_report):
        steps = set(step_e_candidate_tokens(step_e_report))
        for variant in STEP_E_VARIANT_SPELLINGS:
            assert variant in steps
            assert variant != variant.upper()
            assert variant.upper() in steps, variant
            assert variant.upper() in MILESTONE_NAMED_CANDIDATES, variant

    def test_every_candidate_family_is_one_the_live_catalogue_supports(self, audit, live_rows):
        families = {row.get("family") for row in live_rows}
        for candidate in audit.candidates:
            assert candidate.facts.family_catalogue_name in families, candidate.pin.raw_token
            assert candidate.facts.step_e_resolution == NONE
            assert candidate.facts.step_e_reason == REASON_MISSING_ROW

    def test_the_pinned_expected_classifications_match_the_pins(self):
        assert EXPECTED_CLASSIFICATIONS == {pin.raw_token: pin.expected for pin in ALL_PINS}
        assert set(EXPECTED_CLASSIFICATIONS.values()) <= set(CLASSIFICATIONS)

    def test_the_candidates_are_not_forced_into_one_verdict(self, audit):
        counts = audit.counts_by_classification
        assert sum(counts.values()) == PINNED_CANDIDATE_COUNT
        assert counts[CLASS_EXACTLY_SUPPORTED] < PINNED_CANDIDATE_COUNT
        assert sum(1 for count in counts.values() if count) == len(CLASSIFICATIONS), counts

    def test_every_classification_in_the_milestones_vocabulary_is_used(self, audit):
        assert set(audit.letters) == set(CLASSIFICATION_LETTERS.values())


def names_are_distinct(named, variants):
    """No variant is also a named token: each spelling is classified in its own right."""
    return all(variant not in named for variant in variants)


# ===========================================================================
# The audit is read-only, and it runs through the production authority
# ===========================================================================
class TestTheAuditIsReadOnly:
    def test_the_matcher_is_the_genuine_production_matcher(self, real_matcher):
        assert (type(real_matcher).__module__, type(real_matcher).__qualname__) == \
            PRODUCTION_MATCHER

    def test_the_step_e_report_is_the_genuine_audit_class(self, step_e_report):
        assert isinstance(step_e_report, coverage.SectionCoverageAudit)
        assert step_e_report.reference_data_digest == LIVE_DIGEST

    def test_the_local_catalogue_is_the_accepted_one(self, modules, audit):
        assert modules.local_catalogue.CATALOGUE_DIGEST == LOCAL_CATALOGUE_DIGEST
        assert audit.catalogue_digest == LOCAL_CATALOGUE_DIGEST
        assert len(modules.local_catalogue.CATALOGUE) == 32

    def test_no_catalogue_row_is_added_by_this_audit(self, audit, live_rows):
        assert audit.live_row_count == SNAPSHOT_ROW_COUNT == len(live_rows)
        names = {row["name"] for row in audit.rows}
        assert names == {row["name"] for row in live_rows}
        for token in audit.tokens:
            assert token not in names, f"{token!r} appears as a live row name"

    def test_the_audit_deepcopies_the_rows_it_measures(self, modules, live_rows, corpus,
                                                      step_e_report):
        given = [dict(row) for row in live_rows]
        built = build_reconciliation_audit(
            _matcher_for(modules, given), step_e_report, given, corpus,
            catalogue=modules.local_catalogue.CATALOGUE,
        )
        assert [dict(row) for row in built.rows] == live_rows
        assert len(given) == len(live_rows)

    def test_the_reference_digest_is_the_pinned_one(self, audit, step_e_report):
        assert audit.live_digest == LIVE_DIGEST
        assert audit.reference_source_kind == step_e_report.reference_source_kind
        assert audit.reference_source_kind == "LIVE_SUPABASE"

    def test_the_geometry_field_vocabulary_is_e1s_own(self, modules):
        assert GEOMETRY_DEFINING_FIELDS == modules.source_of_truth.GEOMETRY_DEFINING_FIELDS
        assert "weight_per_metre" not in GEOMETRY_DEFINING_FIELDS
        assert "weight_per_metre" in MILESTONE_CONFLICT_FIELDS

    def test_no_derived_value_is_ever_used_as_a_stated_value(self, audit):
        for candidate in audit.candidates:
            for value in candidate.facts.stated_values:
                assert value.provenance != DERIVED_RESTATEMENT
                assert value.provenance != DERIVED_VOLUME_PROXY
                assert value.provenance in PROVENANCES
            for kind, _value, _formula in candidate.facts.derived_values:
                assert kind in (DERIVED_RESTATEMENT, DERIVED_VOLUME_PROXY)

    def test_the_working_tree_is_unchanged_by_this_module(self):
        assert _tree_digests() == _FROZEN_TREE_AT_IMPORT


# ===========================================================================
# The classifications, candidate by candidate
# ===========================================================================
class TestTheClassifications:
    def test_every_candidate_is_classified_as_its_evidence_forces(self, audit, corpus):
        assert reconciliation_violations(audit, corpus) == ()

    def test_each_pinned_expectation_holds(self, audit):
        for token, expected in EXPECTED_CLASSIFICATIONS.items():
            assert audit.by_token(token).classification == expected, token

    def test_the_measured_distribution(self, audit):
        assert audit.counts_by_classification == {
            CLASS_EXACTLY_SUPPORTED: 6,
            CLASS_PARTIAL: 2,
            CLASS_CONFLICTING: 2,
            CLASS_NO_EVIDENCE: 7,
            CLASS_ALREADY_COVERED: 8,
        }
        assert audit.summary() == "25 candidates: A=6 B=2 C=2 D=7 E=8"

    def test_every_candidate_renders_all_fourteen_items(self, audit):
        for candidate in audit.candidates:
            rendered = candidate.render()
            for label in REPORT_ITEMS:
                assert label in rendered, (candidate.pin.raw_token, label)
            with pytest.raises(ValueError):
                candidate.item(REPORT_ITEM_COUNT + 1)

    def test_the_report_is_deterministic(self, build, audit):
        again = build()
        assert again.letters == audit.letters
        assert again.summary() == audit.summary()
        assert again.report() == audit.report()

    def test_the_report_says_what_it_does_not_do(self, audit):
        report = audit.report()
        assert "No catalogue row is added" in report
        for token in audit.tokens:
            assert token in report

    def test_a_report_over_different_rows_is_a_different_audit(self, build, live_rows):
        thinner = [row for row in live_rows if row["name"] != "89x89x5.0SHS"]
        changed = build(rows=thinner)
        # With the only row that carried the drawing's own 89x5 gone, the candidate the
        # drawing prints falls back to "printed but unreconcilable" — never to silence.
        demoted = changed.by_token("89x5 SHS")
        assert demoted.facts.token_is_printed is True
        assert demoted.classification == CLASS_PARTIAL
        assert demoted.facts.coverage_row is None
        # …and the candidate the drawing never prints has nothing left at all.
        assert changed.by_token("89x89 SHS").classification == CLASS_NO_EVIDENCE
        assert changed.letters != build().letters

    def test_every_stated_value_is_named_by_the_familys_own_roles(self, audit):
        for candidate in audit.candidates:
            family = candidate.pin.family_code
            for value in candidate.facts.stated_values:
                if value.provenance == FROM_PRINTED_SIZE_CELL:
                    assert value.field in IDENTIFYING_FIELDS[family], (
                        candidate.pin.raw_token, value
                    )
                elif value.provenance == FROM_PRINTED_SCHEDULE_DESIGNATION:
                    assert value.field in IDENTIFYING_FIELDS[family] + DESIGNATION_FIELDS, (
                        candidate.pin.raw_token, value
                    )


# ===========================================================================
# The milestone's four critical examples
# ===========================================================================
class TestTheMilestonesCriticalExamples:
    def test_250x90pfc_stays_conflicting_and_is_not_already_covered(self, audit):
        for token in ("250X90PFC", "250x90PFC"):
            candidate = audit.by_token(token)
            assert candidate.classification == CLASS_CONFLICTING
            assert candidate.facts.coverage_row is None
            assert "suppressed by the measured conflict" in candidate.facts.coverage_detail
            fields = {field for field, _left, _right in candidate.facts.conflict_fields}
            assert {"flange_thickness", "web_thickness"} <= fields
            assert "weight_per_metre" in fields
            assert candidate.facts.conflicting_rows == ("250PFC",)

    def test_the_250_class_conflict_reports_both_accepted_values_verbatim(self, audit):
        pairs = {(left, right) for _field, left, right
                 in audit.by_token("250X90PFC").facts.conflict_fields}
        assert (15.0, 12.0) in pairs
        assert (8.0, 7.0) in pairs
        assert (35.5, 31.8) in pairs
        assert not any(left == right for left, right in pairs)
        assert not any(pair in pairs for pair in ((250.0, 250.0), (90.0, 90.0)))

    def test_the_250_class_drawing_mass_agrees_with_the_capture_side_record(self, audit):
        for token in ("250X90PFC", "250x90PFC"):
            restated = [value for kind, value, _formula
                        in audit.by_token(token).facts.derived_values
                        if kind == DERIVED_RESTATEMENT]
            assert restated, token
            assert abs(restated[0] - 35.5) < 0.05, token

    def test_150x75pfc_is_measured_not_assumed(self, audit, live_rows):
        for token in ("150X75PFC", "150x75PFC"):
            candidate = audit.by_token(token)
            assert candidate.facts.live_carriers == ("150PFC",)
            assert candidate.classification == CLASS_ALREADY_COVERED
            assert candidate.facts.coverage_basis == COVERAGE_VALUE_AGREEMENT
            assert candidate.facts.coverage_row == "150PFC"
        others = [row["name"] for row in live_rows
                  if row.get("family") == "PFC" and row["name"] != "150PFC"
                  and row.get("depth") == 150.0]
        assert others == []

    def test_200ub30_is_not_assumed_to_be_200ub254_or_200ub298(self, audit):
        candidate = audit.by_token("200UB30")
        assert candidate.classification == CLASS_PARTIAL
        assert candidate.facts.coverage_basis == COVERAGE_NONE
        assert candidate.facts.coverage_row is None
        assert candidate.facts.live_carriers == ()
        near = {name for name, _weight in candidate.facts.live_near_candidates}
        assert "200UB29.8" in near
        assert candidate.pin.prior_rows == ("200UB30.4",)
        assert candidate.facts.accepted_rows_stating_the_values == ()

    def test_310ub42_is_not_assumed_to_be_310ub404_or_310ub462(self, audit):
        candidate = audit.by_token("310UB42")
        assert candidate.classification == CLASS_NO_EVIDENCE
        assert candidate.facts.coverage_basis == COVERAGE_NONE
        assert candidate.facts.live_carriers == ()
        near = {name for name, _weight in candidate.facts.live_near_candidates}
        assert {"310UB40.4", "310UB46.2"} <= near
        assert candidate.facts.coverage_row is None
        assert candidate.facts.printed_designations == ("310UB40",)
        assert candidate.facts.token_is_printed is False


# ===========================================================================
# What decided each verdict
# ===========================================================================
class TestTheDecidingFindings:
    def test_the_exactly_supported_candidates_name_an_accepted_row(self, audit):
        for token in ("130X12FL", "130x12FL", "180X20FL", "180x20FL", "250X12FL", "90x10FL"):
            candidate = audit.by_token(token)
            assert candidate.classification == CLASS_EXACTLY_SUPPORTED
            assert candidate.facts.accepted_rows_stating_the_values
            assert candidate.facts.screen_verdict in (SCREEN_AGREES, SCREEN_NO_REFERENCE_WEIGHT)

    def test_180x20fl_is_corroborated_by_a_captured_plate_record(self, audit):
        values = [value for value in audit.by_token("180X20FL").facts.stated_values
                  if value.provenance == FROM_CAPTURED_PLATE_RECORD]
        assert {value.value for value in values} == {180.0, 20.0}

    def test_300x8fl_is_partial_because_the_drawing_contradicts_itself(self, audit):
        candidate = audit.by_token("300x8FL")
        assert candidate.classification == CLASS_PARTIAL
        assert candidate.facts.accepted_rows_stating_the_values == ("300X8FL",)
        assert candidate.facts.screen_verdict == SCREEN_DISAGREES
        assert "printed weight per printed volume" in candidate.facts.screen_detail
        assert candidate.facts.token_is_printed is True

    def test_the_partial_candidates_are_exactly_the_printed_but_unreconcilable(self, audit):
        partial = {candidate.pin.raw_token for candidate in audit.by_classification(CLASS_PARTIAL)}
        assert partial == {"200UB30", "300x8FL"}
        for token in partial:
            assert audit.by_token(token).facts.token_is_printed

    def test_the_no_evidence_candidates_are_never_printed_by_the_drawing(self, audit):
        for candidate in audit.by_classification(CLASS_NO_EVIDENCE):
            assert candidate.facts.token_is_printed is False
            assert candidate.facts.coverage_basis == COVERAGE_NONE
            assert candidate.facts.members
            for member in candidate.pin.members:
                assert all(_normalise(member.captured) != _normalise(text)
                           for text in member.printed), candidate.pin.raw_token

    def test_200uc_is_covered_by_the_designation_the_drawing_prints(self, audit):
        candidate = audit.by_token("200UC")
        assert candidate.classification == CLASS_ALREADY_COVERED
        assert candidate.facts.coverage_basis == COVERAGE_MATCHER_RESOLUTION
        assert candidate.facts.coverage_row == "200UC46.2"
        assert candidate.facts.coverage_resolution == SUFFIX_FALLBACK
        assert candidate.facts.token_is_printed is False
        assert candidate.facts.live_carriers == ()

    def test_200uc_ignores_the_other_members_channel_designation(self, audit):
        facts = audit.by_token("200UC").facts
        assert "250PFC" in facts.printed_designations
        assert "200UC46" in facts.printed_designations
        assert facts.coverage_row != "250PFC"

    def test_89x89_shs_is_covered_by_the_drawing_not_by_its_own_token(self, audit):
        candidate = audit.by_token("89x89 SHS")
        assert candidate.classification == CLASS_ALREADY_COVERED
        assert candidate.facts.token_is_printed is False
        assert candidate.facts.coverage_row == "89x89x5.0SHS"
        assert candidate.facts.printed_designations == ("89x5 SHS",)

    def test_89x5_shs_is_covered_by_the_numbers_the_drawing_prints(self, audit):
        candidate = audit.by_token("89x5 SHS")
        assert candidate.classification == CLASS_ALREADY_COVERED
        assert candidate.facts.coverage_row == "89x89x5.0SHS"
        assert candidate.facts.coverage_basis == COVERAGE_VALUE_AGREEMENT

    def test_75x60shs_and_90x10ea_are_covered_by_the_only_rows_that_fit(self, audit):
        for token in ("75x6.0SHS", "90X10EA", "90x10EA"):
            candidate = audit.by_token(token)
            assert candidate.classification == CLASS_ALREADY_COVERED, token
            assert candidate.facts.coverage_basis == COVERAGE_VALUE_AGREEMENT, token
            assert len(candidate.facts.live_carriers) == 1, token

    def test_the_captured_misreads_are_recorded_as_misreads(self, audit):
        expected = {
            "250UC46": "250PFC",
            "259PFC": "250PFC",
            "110UB48": "250PFC",
            "23UB37": "250UB37",
            "290UB 37": "250UB 37",
            "1200PFC": "DB1200 PFC",
            "310UB42": "310UB40",
        }
        for token, printed in expected.items():
            candidate = audit.by_token(token)
            assert printed in candidate.facts.printed_designations, token
            assert candidate.facts.token_is_printed is False, token

    def test_every_coverage_claim_is_field_checked(self, audit):
        for candidate in audit.candidates:
            facts = candidate.facts
            if facts.coverage_row is None:
                continue
            row = _row_by_name(audit.rows, facts.coverage_row)
            assert row is not None
            assert row.get("family") == candidate.pin.family_catalogue_name
            assert _carries(row, _numbers(candidate.pin.raw_token))

    def test_no_uncovered_candidate_claims_a_row(self, audit):
        for candidate in audit.candidates:
            if candidate.classification in (CLASS_NO_EVIDENCE, CLASS_CONFLICTING,
                                            CLASS_EXACTLY_SUPPORTED):
                assert candidate.facts.coverage_row is None, candidate.pin.raw_token


# ===========================================================================
# The pinned evidence is real evidence
# ===========================================================================
class TestThePinnedEvidenceIsReal:
    def test_every_pinned_member_is_a_genuine_occurrence_in_the_corpus(self, corpus):
        occurrences = {(occurrence.source, occurrence.page, occurrence.mark,
                        _normalise(occurrence.raw_token)) for occurrence in corpus}
        for pin in ALL_PINS:
            for member in pin.members:
                key = (member.source, member.page, member.mark, _normalise(member.captured))
                assert key in occurrences, (pin.raw_token, member.reference)

    def test_every_pinned_source_is_a_capture_file_the_repository_holds(self):
        for pin in ALL_PINS:
            for member in pin.members:
                if member.source == SOURCE_ARKLES_WORKLOAD:
                    continue
                assert member.source in CAPTURE_FILES, (pin.raw_token, member.source)

    def test_every_pinned_run_is_printed_on_the_page_it_claims(self):
        texts = _drawing_texts()
        for pin in ALL_PINS:
            for member in pin.members:
                if not member.printed_run:
                    continue
                drawing = DRAWING_BY_NAME[member.drawing]
                text = texts[(member.drawing, member.page)]
                assert _collapse(member.printed_run) in text, (
                    f"{pin.raw_token}: {member.reference} run {member.printed_run!r} "
                    f"is not printed on {drawing.name} p{member.page}"
                )

    def test_every_printed_run_names_the_member_it_is_pinned_for(self):
        for pin in ALL_PINS:
            for member in pin.members:
                if not member.printed_run:
                    continue
                printed = _collapse(member.printed_run)
                if member.source in (CAPTURE["selby"], CAPTURE["selby6_10"],
                                     CAPTURE["selby11_15"], CAPTURE["selby16_20"],
                                     CAPTURE["selby21_25"], CAPTURE["selby26_30"],
                                     CAPTURE["selby31_32"]):
                    parsed = _shop_row(printed)
                    assert parsed is not None, (pin.raw_token, member.printed_run)
                    assert parsed[0] == member.mark, (pin.raw_token, member.printed_run)

    def test_every_pinned_printed_mass_comes_from_a_quantity_one_row(self):
        for pin in ALL_PINS:
            for mass in pin.printed_masses:
                assert mass.quantity == 1, (pin.raw_token, mass.evidence)
                parsed = _shop_row(mass.evidence)
                assert parsed is not None, (pin.raw_token, mass.evidence)
                _mark, size, _grade, length, quantity, _area, kg = parsed
                assert quantity == 1
                assert _same(length, mass.length_mm), (pin.raw_token, mass.evidence)
                assert _same(kg, mass.mass_kg), (pin.raw_token, mass.evidence)
                assert size in pin.printed_designations, (pin.raw_token, mass.evidence)

    def test_the_quantity_two_rows_carry_no_mass_and_are_not_screened(self, audit):
        facts = audit.by_token("180x20FL").facts
        assert len(facts.members) == 5
        assert len(facts.printed_masses) == 4
        for member in facts.members:
            if member.page in (27,):
                assert member.mass is None
                assert "2 0.29 19.2" in member.printed_run

    def test_every_pinned_reference_row_is_a_printed_shop_row(self, audit):
        for row in SELBY_PRINTED_REFERENCES:
            parsed = _shop_row(row.run)
            assert parsed is not None, row.run
            mark, size, grade, length, quantity, _area, kg = parsed
            assert (mark, size, grade) == (row.mark, row.size, 300)
            assert _same(length, row.length_mm)
            assert quantity == row.quantity
            assert _same(kg, row.mass_kg)

    def test_the_reference_rows_are_printed_on_the_selby_drawing(self):
        texts = _drawing_texts()
        for row in SELBY_PRINTED_REFERENCES:
            assert _collapse(row.run) in texts[(SELBY.name, row.page)], row.run

    def test_the_shop_material_list_header_is_printed_on_the_selby_drawing(self, audit):
        texts = _drawing_texts()
        assert _collapse(SELBY_POSTS_HEADER) in texts[(SELBY.name, 13)]

    def test_every_cited_prior_row_exists_in_the_accepted_local_catalogue(self, modules):
        for pin in ALL_PINS:
            for name in pin.prior_rows:
                assert name in modules.local_catalogue.CATALOGUE, (pin.raw_token, name)

    def test_every_pinned_print_matches_the_prior_row_it_cites(self, modules):
        for pin in ALL_PINS:
            for name in pin.prior_rows:
                row = modules.local_catalogue.CATALOGUE[name]
                for value in pin.stated:
                    if value.provenance != FROM_PRIOR_MILESTONE:
                        continue
                    if value.field not in row:
                        continue
                    assert _same(row[value.field], value.value), (pin.raw_token, name, value)

    def test_every_pinned_plate_record_is_a_genuine_capture_record(self):
        found = []
        for name in CAPTURE_FILES:
            for page in json.loads((CAPTURE_DIR / name).read_text()):
                if not isinstance(page, dict):
                    continue
                for connection in page.get("raw_connections") or []:
                    for plate in (connection.get("plates") or []):
                        if plate.get("width_mm") is not None:
                            found.append((name, page.get("page_number"),
                                          float(plate["width_mm"]),
                                          float(plate["thickness_mm"])))
        assert (CAPTURE["selby16_20"], 17, 180.0, 20.0) in found

    def test_both_source_drawings_are_pinned_by_the_repository(self):
        assert SELBY.path.name in (REPO / SELBY.pinned_in).read_text()
        assert ARKLES.path.name in (REPO / ARKLES.pinned_in).read_text()

    def test_the_capture_mapping_covers_the_audited_capture_set(self):
        assert set(CAPTURE.values()) == set(CAPTURE_FILES)
        assert set(SOURCE_DRAWING_OF) >= set(CAPTURE_FILES) | {SOURCE_ARKLES_WORKLOAD}
        for capture, name in CAPTURE.items():
            expected = ARKLES.name if capture == "arkles" else SELBY.name
            assert SOURCE_DRAWING_OF[name] == expected, capture

    def test_the_page_to_capture_mapping_is_the_byte_pinned_one(self):
        assert SELBY_CAPTURE_BY_PAGE[1] == "selby"
        assert SELBY_CAPTURE_BY_PAGE[6] == "selby6_10"
        assert SELBY_CAPTURE_BY_PAGE[13] == "selby11_15"
        assert SELBY_CAPTURE_BY_PAGE[17] == "selby16_20"
        assert SELBY_CAPTURE_BY_PAGE[22] == "selby21_25"
        assert SELBY_CAPTURE_BY_PAGE[28] == "selby26_30"
        assert SELBY_CAPTURE_BY_PAGE[32] == "selby31_32"

    def test_every_pinned_member_is_in_the_capture_file_it_names(self):
        for pin in ALL_PINS:
            for member in pin.members:
                if member.source == SOURCE_ARKLES_WORKLOAD:
                    continue
                pages = json.loads((CAPTURE_DIR / member.source).read_text())
                tokens = set()
                for page in pages:
                    if not isinstance(page, dict) or page.get("page_number") != member.page:
                        continue
                    for entry in (page.get("raw_members") or []):
                        if isinstance(entry, dict) and entry.get("section"):
                            tokens.add((entry.get("mark"), entry["section"]))
                assert (member.mark, member.captured) in tokens, (
                    pin.raw_token, member.source, member.page, member.mark, member.captured
                )

    def test_the_step_e_resolutions_are_the_pinned_none_missing_row(self, audit):
        for candidate in audit.candidates:
            assert candidate.facts.step_e_resolution == NONE
            assert candidate.facts.step_e_reason == REASON_MISSING_ROW


def _collapse(text: str) -> str:
    """Whitespace-collapsed text — the text layer's column padding is not evidence."""
    return re.sub(r"\s+", " ", " ".join(text.splitlines())).strip()


_SHOP_ROW = re.compile(
    r"(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$"
)


def _shop_row(run: str):
    """Parse a Selby shop-material-list run: mark, size, grade, length, qty, area, kg."""
    match = _SHOP_ROW.search(_collapse(run))
    if match is None:
        return None
    mark, size, grade, length, quantity, area, kg = match.groups()
    return mark, size, int(grade), float(length), int(quantity), float(area), float(kg)


_DRAWING_TEXT_CACHE: dict = {}


def _drawing_texts() -> dict:
    """
    The real drawings' text layers, read once. A drawing the process cannot read
    (macOS TCC can refuse ~/Downloads) is an honest skip, not a pass: the pins are
    only meaningful if the pages can actually be checked.
    """
    if _DRAWING_TEXT_CACHE:
        return _DRAWING_TEXT_CACHE
    missing = [drawing.path for drawing in SOURCE_DRAWINGS if not drawing.path.exists()]
    if missing:
        pytest.skip(f"the pinned source drawings are not present here: {missing}")
    from pypdf import PdfReader

    for drawing in SOURCE_DRAWINGS:
        try:
            reader = PdfReader(str(drawing.path))
            for page in range(1, drawing.pages + 1):
                _DRAWING_TEXT_CACHE[(drawing.name, page)] = _collapse(
                    reader.pages[page - 1].extract_text() or ""
                )
        except Exception as error:  # pragma: no cover - macOS TCC / reader failure
            pytest.skip(f"{drawing.name} is not readable in this process: {error}")
    return _DRAWING_TEXT_CACHE


# ===========================================================================
# The classifier's own rules, on synthetic facts
# ===========================================================================
class TestTheClassifierRules:
    def _facts(self, build, token, **changes):
        return replace(build().by_token(token).facts, **changes)

    def test_a_conflict_beats_coverage(self, build):
        facts = self._facts(build, "250X90PFC")
        assert classify(facts) == CLASS_CONFLICTING
        assert classify(replace(facts, conflicting_rows=(), conflict_fields=())) == \
            CLASS_ALREADY_COVERED

    def test_coverage_needs_a_basis(self, build):
        facts = self._facts(build, "200UC")
        assert facts.coverage_basis != COVERAGE_NONE
        assert classify(replace(facts, coverage_basis=COVERAGE_NONE, coverage_row=None)) != \
            CLASS_ALREADY_COVERED

    def test_a_printed_designation_with_nothing_to_reconcile_is_partial(self, build):
        facts = self._facts(build, "200UB30")
        assert facts.token_is_printed
        assert classify(replace(facts, accepted_rows_stating_the_values=())) == CLASS_PARTIAL

    def test_an_unprinted_designation_with_nothing_stated_is_no_evidence(self, build):
        stripped = replace(
            self._facts(build, "200UB30"),
            token_is_printed=False, accepted_rows_stating_the_values=(),
            accepted_row_fields=(), stated_values=(),
            screen_verdict=SCREEN_NO_PRINTED_MASS, screen_detail="", conflict_fields=(),
        )
        assert classify(stripped) == CLASS_NO_EVIDENCE

    def test_exactly_supported_needs_an_accepted_row_stating_the_values(self, build):
        facts = self._facts(build, "130X12FL")
        assert classify(facts) == CLASS_EXACTLY_SUPPORTED
        assert classify(replace(facts, accepted_rows_stating_the_values=())) == CLASS_PARTIAL

    def test_a_self_contradicting_record_demotes_exactly_supported(self, build):
        facts = self._facts(build, "130X12FL")
        demoted = replace(facts, screen_verdict=SCREEN_DISAGREES, screen_detail="synthetic")
        assert classify(demoted) == CLASS_PARTIAL

    def test_a_designation_the_drawing_does_not_print_cannot_be_exactly_supported(self, build):
        facts = self._facts(build, "130X12FL")
        assert classify(replace(facts, token_is_printed=False)) == CLASS_NO_EVIDENCE

    def test_an_accepted_row_that_states_other_values_blocks_exactly_supported(self, build):
        facts = self._facts(build, "130X12FL")
        changed = replace(
            facts,
            accepted_row_fields=(("130X12FL", (("width", 130.0), ("thickness", 11.0))),),
        )
        assert classify(changed) == CLASS_PARTIAL

    def test_family_identity_is_required_for_coverage(self, audit):
        for candidate in audit.by_classification(CLASS_ALREADY_COVERED):
            row = _row_by_name(audit.rows, candidate.facts.coverage_row)
            assert row.get("family") == candidate.pin.family_catalogue_name, candidate.pin.raw_token


# ===========================================================================
# The audit cannot be gamed
# ===========================================================================
class TestTheAuditCannotBeGamed:
    def test_a_relabelled_candidate_fails_the_violations_check(self, audit):
        honest = audit.candidates[0]
        lying = replace(honest, pin=replace(honest.pin, expected=CLASS_ALREADY_COVERED))
        broken = replace(audit, candidates=(lying,) + audit.candidates[1:])
        problems = reconciliation_violations(broken)
        assert any("the pinned evidence expects" in problem for problem in problems)

    def test_a_conflicting_candidate_that_still_claims_coverage_is_a_violation(self, audit):
        candidate = audit.by_token("250X90PFC")
        lying_facts = replace(candidate.facts, coverage_row="250PFC",
                              coverage_basis=COVERAGE_VALUE_AGREEMENT)
        broken = replace(
            audit,
            candidates=tuple(replace(c, facts=lying_facts) if c is candidate else c
                             for c in audit.candidates),
        )
        problems = reconciliation_violations(broken)
        assert any("must suppress the coverage claim" in problem for problem in problems)

    def test_a_candidate_set_that_is_not_steps_own_is_refused(self, modules, live_rows,
                                                             corpus, step_e_report):
        invented = replace(PIN_BY_TOKEN["200UC"], raw_token="200UC99")
        with pytest.raises(AuditAuthorityError) as failure:
            build_reconciliation_audit(
                _matcher_for(modules, live_rows), step_e_report, live_rows, corpus,
                (invented,), modules.local_catalogue.CATALOGUE,
            )
        assert "candidate set" in str(failure.value)

    def test_a_dropped_candidate_is_refused(self, modules, live_rows, corpus, step_e_report):
        with pytest.raises(AuditAuthorityError) as failure:
            build_reconciliation_audit(
                _matcher_for(modules, live_rows), step_e_report, live_rows, corpus,
                ALL_PINS[:-1], modules.local_catalogue.CATALOGUE,
            )
        assert "dropped" in str(failure.value)

    def test_a_step_e_report_over_other_rows_is_refused(self, modules, live_rows, corpus,
                                                       step_e_report):
        elsewhere = replace(step_e_report, reference_data_digest="0" * 64)
        with pytest.raises(AuditAuthorityError) as failure:
            build_reconciliation_audit(
                _matcher_for(modules, live_rows), elsewhere, live_rows, corpus,
                catalogue=modules.local_catalogue.CATALOGUE,
            )
        assert "different reference rows" in str(failure.value)

    def test_a_proof_only_local_matcher_is_refused(self, modules, live_rows, corpus,
                                                  step_e_report):
        local = modules.local_catalogue.CatalogueMatcher(modules.local_catalogue.CATALOGUE)
        with pytest.raises(AuditAuthorityError) as failure:
            build_reconciliation_audit(
                local, step_e_report, live_rows, corpus,
                catalogue=modules.local_catalogue.CATALOGUE,
            )
        assert "non-production matcher" in str(failure.value)

    def test_a_step_e_report_of_the_wrong_class_is_refused(self, modules, live_rows, corpus):
        with pytest.raises(AuditAuthorityError) as failure:
            build_reconciliation_audit(
                _matcher_for(modules, live_rows), SimpleNamespace(), live_rows, corpus,
                catalogue=modules.local_catalogue.CATALOGUE,
            )
        assert "genuine Step E coverage audit" in str(failure.value)

    def test_m1_removing_the_covering_row_removes_the_coverage_claim(self, build, live_rows):
        without = [row for row in live_rows if row["name"] != "200UC46.2"]
        changed = build(rows=without)
        assert changed.by_token("200UC").classification == CLASS_NO_EVIDENCE
        assert changed.by_token("200UC").facts.coverage_row is None

    def test_m2_softening_the_conflicting_row_removes_the_conflict(self, build, live_rows):
        softened = []
        for row in live_rows:
            if row["name"] == "250PFC":
                row = dict(row, flange_thickness=15.0, web_thickness=8.0, weight_per_metre=35.5)
            softened.append(row)
        changed = build(rows=softened)
        candidate = changed.by_token("250X90PFC")
        assert candidate.facts.conflict_fields == ()
        assert candidate.classification == CLASS_ALREADY_COVERED
        assert candidate.facts.coverage_row == "250PFC"

    def test_m3_adding_the_missing_row_changes_the_verdict(self, build, live_rows):
        """
        The mutation the whole milestone exists to make visible: add one row named after
        the token, carrying the token's numbers, and the verdict moves. This row is NOT
        added anywhere — it lives inside one test — and its weight is deliberately the
        accepted row's own 30.4, so the only thing that changes is the missing row.
        """
        invented = dict(name="200UB30", family="UB", depth=200.0, flange_width=133.0,
                        flange_thickness=8.0, web_thickness=5.6, weight_per_metre=30.4)
        added = build(rows=list(live_rows) + [invented])
        candidate = added.by_token("200UB30")
        assert candidate.facts.live_carriers == ("200UB30",)
        assert candidate.facts.conflict_fields == ()
        assert candidate.classification == CLASS_ALREADY_COVERED
        assert added.by_token("310UB42").classification == CLASS_NO_EVIDENCE

    def test_m4_a_row_in_the_wrong_family_covers_nothing(self, build, live_rows):
        """The same values in another family are not this family's section."""
        wrong_family = []
        for row in live_rows:
            if row["name"] == "89x89x5.0SHS":
                row = dict(row, family="CHS")
            wrong_family.append(row)
        changed = build(rows=wrong_family)
        for token in ("89x5 SHS", "89x89 SHS"):
            candidate = changed.by_token(token)
            assert candidate.classification != CLASS_ALREADY_COVERED, token
            assert candidate.facts.coverage_row is None, token
            assert candidate.facts.live_carriers == (), token
        # the candidate the drawing never prints has nothing left at all
        assert changed.by_token("89x89 SHS").classification == CLASS_NO_EVIDENCE

    def test_m5_a_carrier_that_does_not_carry_the_tokens_numbers_covers_nothing(self, build,
                                                                               live_rows):
        """The 310UB40.4 trap: the drawing prints 310UB40, the row exists, and the token
        still claims a 42 that the row does not carry."""
        candidate = build().by_token("310UB42")
        assert candidate.facts.coverage_basis == COVERAGE_NONE
        assert candidate.classification == CLASS_NO_EVIDENCE
        near = "310UB40.4"
        assert near in {name for name, _weight in candidate.facts.live_near_candidates}

    def test_m6_a_fabricated_member_is_a_violation(self, corpus):
        pin = replace(PIN_BY_TOKEN["200UC"],
                      members=(replace(PIN_BY_TOKEN["200UC"].members[0], page=99),))
        problems = _member_violations(pin, corpus)
        assert any("not in the audited corpus" in problem for problem in problems)

    def test_m7_the_audit_never_writes_to_the_local_catalogue(self, modules, audit):
        before = modules.local_catalogue.CATALOGUE_DIGEST
        with pytest.raises(TypeError):
            modules.local_catalogue.CATALOGUE["200UB30"] = {"name": "200UB30"}  # type: ignore[index]
        assert modules.local_catalogue.CATALOGUE_DIGEST == before
        assert "200UB30" not in modules.local_catalogue.CATALOGUE

    def test_the_control_run_passes_every_rule(self, build, corpus, audit):
        control = build()
        assert reconciliation_violations(control, corpus) == ()
        assert control.letters == audit.letters


def _member_violations(pin: CandidatePin, corpus) -> tuple[str, ...]:
    """The corpus-membership rule on its own, so a fabricated member can be exercised."""
    occurrences = {(occurrence.source, occurrence.page, occurrence.mark,
                    _normalise(occurrence.raw_token)) for occurrence in corpus}
    problems = []
    for member in pin.members:
        key = (member.source, member.page, member.mark, _normalise(member.captured))
        if key not in occurrences:
            problems.append(
                f"{pin.raw_token}: pinned member {member.reference} with token "
                f"{member.captured!r} is not in the audited corpus"
            )
    return tuple(problems)


# ===========================================================================
# The read-only guard
# ===========================================================================
def _tree_digests() -> dict:
    digests = {}
    for path in sorted(REPO.joinpath("app").rglob("*.py")):
        digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(CAPTURE_DIR.glob("*.json")):
        digests[str(path.relative_to(REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("tests/test_real_world_production_section_coverage_audit.py",
                 "tests/test_real_world_production_section_evidence_reconciliation_audit.py"):
        path = REPO / name
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


_FROZEN_TREE_AT_IMPORT = _tree_digests()
