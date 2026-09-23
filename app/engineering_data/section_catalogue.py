"""
Milestone C — the versioned LOCAL section catalogue.

A deterministic, immutable, versioned catalogue of steel_sections
rows that supplies the existing CAD engine WITHOUT Supabase. The live
`SectionMatcher` (app/engineering_data/section_matcher.py, which
loads the steel_sections table from Supabase at construction) and its
production wiring (app/pipeline.py, app/drawing_reading/dxf_parser.py)
are deliberately UNCHANGED: replacing them is a later milestone. This
module only ADDS the local catalogue and its matcher.

CONTENTS — every row is seeded from inspection-established evidence
only. Nothing here was invented from memory, standards, internet
sources or approximate calculation:

  * Eight FULLY-TRACED dimensioned rows, each traced to a genuine
    real-capture fixture (exact values, mm and kg/m):

      310UB40   (Selby page-5, tests/test_real_world_multi_member_connection.py)
      250X90PFC (Selby page-1, tests/test_real_world_material_extraction.py)
      250X12FL  (Selby page-1, tests/test_real_world_material_extraction.py)
      180X20FL  (Selby page-5 A-A, tests/test_real_world_production_coverage.py)
      90X10EA   (Selby page-5 A-A, tests/test_real_world_production_coverage.py)
      160X10PL  (Selby page-5 B-B, tests/test_real_world_multi_member_connection.py)
      165X10PL  (Selby page-5 B-B, tests/test_real_world_multi_member_connection.py)
      81X10PL   (Selby page-5 B-B, tests/test_real_world_multi_member_connection.py)

    The inspection called these "six rows" but listed the three plate
    variants individually. That discrepancy is resolved EXPLICITLY,
    not by collapsing: the catalogue carries all EIGHT distinct rows —
    the six section groups (310UB40, 250X90PFC, 250X12FL, 180X20FL,
    90X10EA, and the page-5 plate group) plus the three separate
    160X10PL / 165X10PL / 81X10PL rows of that plate group, each with
    its own width and weight. The three PL variants stay three rows.

  * Fourteen PLATE rows (Milestone D — Evidence-Backed Plate
    Catalogue Expansion). Width and thickness come from the genuine
    Selby engineer drawings' own section labels — "210x10PL" states
    width 210, thickness 10; "130x12FL" states width 130, thickness
    12. Nothing is derived from memory, standards, another section,
    calculation or the internet. Each row's provenance (drawing page,
    member mark, and where a connection-plate record independently
    corroborates the same width/thickness) is noted per row below.
    NO weight_per_metre key exists on these rows — no genuine
    repository evidence ever recorded a weight for them, and none was
    invented or calculated. Their geometry (the plate builder needs
    exactly width + thickness) is fully supported; their tonnage is
    honestly NOT_CALCULATED wherever a weight is required.

  * Seven rows enriched with the live steel_sections snapshot's
    authoritative dimensions (Milestone E1 — safe geometry
    enrichment): 150PFC, 200PFC, 300PFC, 250UB25.7, 250UB37.3,
    310UB46.2 and 100PFC. Each was verified against the live
    snapshot before enrichment — exact name match, complete
    geometry, weight agreement, compatible family, current builder
    support, and no genuine-capture conflict. Their provenance is
    LIVE_CATALOGUE_EVIDENCE. 100PFC additionally carries BOTH
    provenance: its weight stays the pre-existing local value 8.3
    (the E1 precision decision — the live value 8.33 differs only
    by rounding precision, so the local value is preserved and the
    live value is recorded verbatim as
    live_catalogue_weight_per_metre, never substituted).

  * Three WEIGHT-ONLY rows remain, the live steel_sections seed as
    the existing test doubles document it
    (tests/test_arkles_strand.py::RealisticFakeMatcher): 200UB30.4,
    200UC46.2 and 250PFC — name, family and weight_per_metre only.
    Their dimensions were NEVER observed anywhere in the genuine
    evidence, so NO dimension keys exist on these rows — the keys
    are absent entirely, not zeroed. 200UB30.4 has no live row;
    200UC46.2 is deliberately not enriched (UC stays an unsupported
    family); 250PFC is deliberately not enriched because the live
    250PFC geometry CONFLICTS with the genuine Selby 250X90PFC
    capture (weight 35.5 vs 31.8, flange thickness 15 vs 12, web
    thickness 8 vs 7 — the E1 source-of-truth boundary,
    app/engineering_data/source_of_truth.py, keeps the two records
    distinct). Geometry for the three must fail closed (see the
    adapter/geometry layers' own "Refusing to guess" behaviour).

The three truth distinctions the product already makes are preserved,
not merged:

  * catalogue-present: all 32 rows (CATALOGUE contains the row);
  * geometry-supported: the 29 dimensioned rows whose family has a
    CAD profile builder AND whose dimensions are present
    (app.cad_engine.sections.PROFILE_BUILDERS is the authority,
    never re-listed here);
  * production-proven: rows that have gone through the genuine
    workflow to AUTO -> GENERATED -> VERIFIED -> ACCEPTED ->
    PACKAGED. That is the eight fully-traced rows only — the
    fourteen plate rows and the seven E1-enriched rows are
    catalogue-present, dimensioned and geometry-supported, but NOT
    production-proven (no genuine reviewed production journey
    exists for them yet). 200UC46.2 stays catalogue-present but
    geometry-unsupported (UC has no builder) and
    production-unproven.

Row shape is the existing row-dict contract the builders consume
(dict with name / family / dimension / weight_per_metre keys, mm and
kg/m). Every row carries exactly the keys its evidence supports —
never more: the fully-traced rows carry dimensions and weight; the
fourteen plate rows carry name, family, width and thickness (no
weight key); the three weight-only rows carry name, family and
weight only (no dimension keys). The seven E1-enriched rows carry
their live dimensions and weight plus two additive keys the builders
never read: a `provenance` key from the fixed taxonomy below, and —
on 100PFC only — live_catalogue_weight_per_metre, the recorded live
weight evidence (never substituted into weight_per_metre). 250X12FL
is recorded with family "PL" because that is the row the genuine
Selby page-1 chain actually resolved (its own fixture row); 180X20FL
keeps family "FL" as the genuine FL row. Both dispatch to the plate
profile builder.

PROVENANCE TAXONOMY (Milestone E1): rows added or enriched from E1
onwards carry a `provenance` key naming the evidence kind backing
their content, one of the five constants PROVENANCE_KINDS defines:

  * CAPTURE_EVIDENCE        — values taken verbatim from genuine
    drawing-extraction evidence;
  * LIVE_CATALOGUE_EVIDENCE — values taken verbatim from the live
    steel_sections catalogue evidence (the Milestone E snapshot);
  * BOTH                    — values backed by both capture-side and
    live-catalogue evidence (each recorded separately wherever the
    two differ, never merged);
  * HUMAN_CONFIRMED         — a human reviewer explicitly confirmed
    the value;
  * PRODUCTION_PROVEN       — the row has completed the genuine
    production journey (AUTO -> GENERATED -> VERIFIED -> ACCEPTED ->
    PACKAGED).

Carrying a provenance key never implies production provenance: the
eight fully-traced rows are production-proven per the genuine
workflow evidence alone, and no row here claims PRODUCTION_PROVEN
without it.

VERSION IDENTITY: there is no fake semantic version. The version is
content-addressed — CATALOGUE_DIGEST is the sha256 of the canonical
JSON serialization of the whole catalogue (documented recipe below),
and CATALOGUE_VERSION is a fixed tag derived from that digest. Both
are computed at import time from the row literals above, with no I/O,
so every run of the same code yields the same values, and any change
to any row changes the digest.

RUNTIME BEHAVIOUR: this module performs no filesystem, network,
database, environment-variable or credential access, ever — at import
time or at match time. Its only imports are stdlib (hashlib, json,
re, types, typing).

IMMUTABILITY: CATALOGUE is a MappingProxyType of MappingProxyType
rows — the mapping and every row refuse mutation. CatalogueMatcher
.match() returns a detached plain dict copy (exactly the row-dict
contract the live matcher's callers expect), so callers can never
corrupt the catalogue through a returned row.

MATCHING SEMANTICS: CatalogueMatcher mirrors SectionMatcher.match()
exactly — whitespace-collapsing case-insensitive normalisation, exact
lookup first, then the ".0"..".9" suffix fallback when (and only
when) the normalised name contains no dot. Two deliberate, narrower
differences, both fail-closed: a None / non-str / blank name returns
None (the live matcher raises on None), and unmatched names return
None exactly like the live matcher. get_section_regex() is NOT
reproduced — it is extraction-side vocabulary for the live
pipeline's AI reading, not part of the CAD matching contract, and
nothing in the local-catalogue milestone consumes it.
"""

import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

__all__ = [
    "CATALOGUE",
    "CATALOGUE_DIGEST",
    "CATALOGUE_VERSION",
    "CatalogueMatcher",
    "PROVENANCE_CAPTURE_EVIDENCE",
    "PROVENANCE_LIVE_CATALOGUE_EVIDENCE",
    "PROVENANCE_BOTH",
    "PROVENANCE_HUMAN_CONFIRMED",
    "PROVENANCE_PRODUCTION_PROVEN",
    "PROVENANCE_KINDS",
]

# Milestone E1 — the fixed provenance taxonomy: a row carrying a
# `provenance` key uses exactly one of these five values, so future
# additions never blur where their content came from. The values are
# labels, not a ranking; see the module docstring for each meaning.
PROVENANCE_CAPTURE_EVIDENCE = "CAPTURE_EVIDENCE"
PROVENANCE_LIVE_CATALOGUE_EVIDENCE = "LIVE_CATALOGUE_EVIDENCE"
PROVENANCE_BOTH = "BOTH"
PROVENANCE_HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
PROVENANCE_PRODUCTION_PROVEN = "PRODUCTION_PROVEN"
PROVENANCE_KINDS = (
    PROVENANCE_CAPTURE_EVIDENCE,
    PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    PROVENANCE_BOTH,
    PROVENANCE_HUMAN_CONFIRMED,
    PROVENANCE_PRODUCTION_PROVEN,
)

# ----------------------------------------------------------------------
# The catalogue rows — plain dict literals, seeded from the genuine
# fixture evidence named in the module docstring. Nothing below adds,
# derives or renames a value.
# ----------------------------------------------------------------------

_DIMENSIONED_SECTIONS = {
    "310UB40": {
        "name": "310UB40", "family": "UB",
        "depth": 304.0, "flange_width": 165.0,
        "flange_thickness": 11.8, "web_thickness": 6.1,
        "weight_per_metre": 40.4,
    },
    "250X90PFC": {
        "name": "250X90PFC", "family": "PFC",
        "depth": 250.0, "flange_width": 90.0,
        "flange_thickness": 15.0, "web_thickness": 8.0,
        "weight_per_metre": 35.5,
    },
    "250X12FL": {
        "name": "250X12FL", "family": "PL",
        "width": 250.0, "thickness": 12.0,
        "weight_per_metre": 23.6,
    },
    "180X20FL": {
        "name": "180X20FL", "family": "FL",
        "width": 180.0, "thickness": 20.0,
        "weight_per_metre": 28.3,
    },
    "90X10EA": {
        "name": "90X10EA", "family": "EA",
        "width": 90.0, "thickness": 10.0,
        "weight_per_metre": 13.3,
    },
    "160X10PL": {
        "name": "160X10PL", "family": "PL",
        "width": 160.0, "thickness": 10.0,
        "weight_per_metre": 12.6,
    },
    "165X10PL": {
        "name": "165X10PL", "family": "PL",
        "width": 165.0, "thickness": 10.0,
        "weight_per_metre": 13.0,
    },
    "81X10PL": {
        "name": "81X10PL", "family": "PL",
        "width": 81.0, "thickness": 10.0,
        "weight_per_metre": 6.4,
    },
    # --- Milestone D: the fourteen evidence-backed plate rows. Width and
    # thickness are the drawing's own section-label values; provenance per
    # row (page / member mark / corroboration). No weight_per_metre key:
    # none is evidenced anywhere in the repository, none is calculated.
    "143X6PL": {
        # Selby p10 PL021, p11 PL020/PL021
        "name": "143X6PL", "family": "PL",
        "width": 143.0, "thickness": 6.0,
    },
    "145X12PL": {
        # Selby p7 PL033
        "name": "145X12PL", "family": "PL",
        "width": 145.0, "thickness": 12.0,
    },
    "210X10PL": {
        # Selby p8 PL019, p9 PL017, p14 PL007, p15 PL004, p27 PL010/PL012,
        # p28 PL009/PL011 — corroborated by the p28 VIEW A-A and VIEW B-B
        # web-plate records (width_mm 210, thickness_mm 10)
        "name": "210X10PL", "family": "PL",
        "width": 210.0, "thickness": 10.0,
    },
    "228X10PL": {
        # Selby p18 PL035 — corroborated by the p18 VIEW A-A web-plate
        # record (width_mm 228, thickness_mm 10; the member's length_mm 313
        # equals the recorded plate depth_mm 313)
        "name": "228X10PL", "family": "PL",
        "width": 228.0, "thickness": 10.0,
    },
    "140X10PL": {
        # Selby p29 PL022
        "name": "140X10PL", "family": "PL",
        "width": 140.0, "thickness": 10.0,
    },
    "64X8PL": {
        # Selby p30 PL013
        "name": "64X8PL", "family": "PL",
        "width": 64.0, "thickness": 8.0,
    },
    "133X10PL": {
        # Selby p31 PL024
        "name": "133X10PL", "family": "PL",
        "width": 133.0, "thickness": 10.0,
    },
    "81X16PL": {
        # Selby p32 PL023
        "name": "81X16PL", "family": "PL",
        "width": 81.0, "thickness": 16.0,
    },
    "130X10FL": {
        # Selby p3/p4 PL002, p22 PL034
        "name": "130X10FL", "family": "FL",
        "width": 130.0, "thickness": 10.0,
    },
    "130X12FL": {
        # Selby p2 PL029, p3 PL030, p4 PL031
        "name": "130X12FL", "family": "FL",
        "width": 130.0, "thickness": 12.0,
    },
    "90X10FL": {
        # Selby p2 PL027
        "name": "90X10FL", "family": "FL",
        "width": 90.0, "thickness": 10.0,
    },
    "100X10FL": {
        # Selby p14 PL005
        "name": "100X10FL", "family": "FL",
        "width": 100.0, "thickness": 10.0,
    },
    "75X8FL": {
        # Selby p9 PL015/PL016, p13/p15 PL001
        "name": "75X8FL", "family": "FL",
        "width": 75.0, "thickness": 8.0,
    },
    "300X8FL": {
        # Selby p13 PL003
        "name": "300X8FL", "family": "FL",
        "width": 300.0, "thickness": 8.0,
    },
    # --- Milestone E1: safe live-catalogue geometry enrichment. Each
    # row's dimensions are the live steel_sections snapshot values
    # verbatim (the Milestone E reconciliation), attached only after
    # verification — exact name match, complete geometry, weight
    # agreement with the pre-existing local value, compatible family,
    # current builder support, and no genuine-capture conflict.
    # Provenance = LIVE_CATALOGUE_EVIDENCE. Nothing is calculated,
    # inferred or copied from an unrelated live row.
    "150PFC": {
        "name": "150PFC", "family": "PFC",
        "depth": 150.0, "flange_width": 75.0,
        "flange_thickness": 9.5, "web_thickness": 6.0,
        "weight_per_metre": 17.7,
        "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    },
    "200PFC": {
        "name": "200PFC", "family": "PFC",
        "depth": 200.0, "flange_width": 75.0,
        "flange_thickness": 12.0, "web_thickness": 6.0,
        "weight_per_metre": 22.9,
        "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    },
    "300PFC": {
        "name": "300PFC", "family": "PFC",
        "depth": 300.0, "flange_width": 90.0,
        "flange_thickness": 16.0, "web_thickness": 8.0,
        "weight_per_metre": 40.1,
        "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    },
    "250UB25.7": {
        "name": "250UB25.7", "family": "UB",
        "depth": 248.0, "flange_width": 124.0,
        "flange_thickness": 8.0, "web_thickness": 5.0,
        "weight_per_metre": 25.7,
        "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    },
    "250UB37.3": {
        "name": "250UB37.3", "family": "UB",
        "depth": 256.0, "flange_width": 146.0,
        "flange_thickness": 10.9, "web_thickness": 6.6,
        "weight_per_metre": 37.3,
        "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    },
    "310UB46.2": {
        "name": "310UB46.2", "family": "UB",
        "depth": 307.0, "flange_width": 166.0,
        "flange_thickness": 11.8, "web_thickness": 6.7,
        "weight_per_metre": 46.2,
        "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
    },
    # 100PFC: E1 precision decision (approach A). The pre-existing
    # local weight 8.3 is PRESERVED — the live value 8.33 differs
    # only by rounding precision, never an engineering conflict —
    # and the live value is recorded verbatim as source evidence,
    # never substituted. Provenance = BOTH (local weight + live
    # geometry, each kept distinct).
    "100PFC": {
        "name": "100PFC", "family": "PFC",
        "depth": 100.0, "flange_width": 50.0,
        "flange_thickness": 7.0, "web_thickness": 4.5,
        "weight_per_metre": 8.3,
        "live_catalogue_weight_per_metre": 8.33,
        "provenance": PROVENANCE_BOTH,
    },
}

_WEIGHT_ONLY_SECTIONS = {
    # name + family + weight_per_metre ONLY — the live seed rows as the
    # existing test doubles document them. Dimension keys are absent,
    # deliberately, because no genuine evidence ever carried them.
    # 200UB30.4 has no live row; 200UC46.2 is deliberately not enriched
    # (UC stays unsupported); 250PFC is deliberately not enriched — the
    # live 250PFC geometry conflicts with the genuine Selby 250X90PFC
    # capture, and the two records stay distinct (E1 rule, see the
    # module docstring).
    "200UB30.4": {"name": "200UB30.4", "family": "UB", "weight_per_metre": 30.4},
    "200UC46.2": {"name": "200UC46.2", "family": "UC", "weight_per_metre": 46.2},
    "250PFC": {"name": "250PFC", "family": "PFC", "weight_per_metre": 35.5},
}


def _frozen_catalogue() -> Mapping[str, Mapping[str, Any]]:
    """Freeze all rows into one immutable, deterministically ordered mapping."""
    rows = {**_WEIGHT_ONLY_SECTIONS, **_DIMENSIONED_SECTIONS}
    return MappingProxyType({
        name: MappingProxyType(dict(row))
        for name, row in sorted(rows.items())
    })


CATALOGUE: Mapping[str, Mapping[str, Any]] = _frozen_catalogue()


def _canonical_catalogue_json() -> str:
    """The documented content-identity recipe: the whole catalogue as one
    canonical JSON text — outer and inner keys sorted, compact
    separators, unicode preserved — computed from the frozen rows, so
    tests can re-derive the digest independently from CATALOGUE alone."""
    rows = {name: dict(CATALOGUE[name]) for name in CATALOGUE}
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


CATALOGUE_DIGEST = hashlib.sha256(_canonical_catalogue_json().encode("utf-8")).hexdigest()

# Content-addressed version tag — not a fake semantic version: any change
# to any row changes the digest, hence the version. The full digest stays
# available separately for exact-content checks.
CATALOGUE_VERSION = f"local-section-catalogue@{CATALOGUE_DIGEST[:16]}"


class CatalogueMatcher:
    """
    The local-catalogue matcher: the same match() contract as the live
    SectionMatcher (normalise -> exact -> ".0"..".9" fallback), served
    from the immutable CATALOGUE with no network, database, env or
    filesystem access of any kind.

    Returns a detached plain dict copy of the matched row — the exact
    row-dict contract the live matcher's callers (7A adapter, geometry
    builders) consume — so a caller mutating the returned row can
    never corrupt the catalogue.

    An optional `catalogue` mapping may be supplied (a future version
    of the catalogue, or a test fixture); it is frozen the same way.
    Such an instance declares catalogue_version None — the versioned
    identity belongs to this module's CATALOGUE, and an override is by
    definition not it. The default constructor declares
    CATALOGUE_VERSION, which is what the production pipeline records.
    """

    def __init__(self, catalogue: Mapping[str, Mapping[str, Any]] | None = None):
        if catalogue is None:
            self._catalogue = CATALOGUE
            self.catalogue_version: str | None = CATALOGUE_VERSION
        else:
            self._catalogue = MappingProxyType({
                name: MappingProxyType(dict(row))
                for name, row in catalogue.items()
            })
            self.catalogue_version = None

    @staticmethod
    def _normalise(name: str) -> str:
        return re.sub(r"\s+", "", name.strip().upper())

    def match(self, raw_name):
        """The live matcher's proven semantics, fail-closed. Returns a plain
        dict copy of the matched row, or None when nothing matches."""
        if raw_name is None or not isinstance(raw_name, str):
            return None
        base = self._normalise(raw_name)
        if not base:
            return None
        candidates = [base]
        if "." not in base:
            candidates += [base + f".{digit}" for digit in range(10)]
        for candidate in candidates:
            if candidate in self._catalogue:
                return dict(self._catalogue[candidate])
        return None
