"""
Milestone E1 — SOURCE-OF-TRUTH BOUNDARY + SAFE RECONCILIATION: focused proofs.

Proven here, unit by unit:

  * the adopted source-of-truth rule (one verbatim constant carrying
    the three clauses: drawing authoritative for what was specified,
    catalogue authoritative only where it does not conflict, and any
    conflict stays REVIEW/BLOCKED — never a silent choice);
  * the explicit deterministic live-schema -> local mappings: EA
    leg_size -> width and family FLAT -> FL. Source records are never
    mutated or renamed; the geometry builders keep receiving the
    local canonical fields; a live EA row without a leg_size is
    refused (no guessing);
  * the safe enrichment of the six conflict-free weight-only rows
    (150PFC, 200PFC, 300PFC, 250UB25.7, 250UB37.3, 310UB46.2) —
    complete live-snapshot geometry, weight agreement, builder
    construction, no approximations;
  * 100PFC's deterministic precision handling: local 8.3 preserved,
    live 8.33 recorded as source evidence, and the weight difference
    alone is never an engineering conflict;
  * the two HARD conflicts: 250X90PFC vs live 250PFC, and the genuine
    capture 310UB40 (flange thickness 11.8) vs live 310UB40.4 (10.2).
    Neither row is collapsed or overwritten; both verdicts are
    CONFLICT_BLOCKED / BLOCKED_REVIEW with both recorded values
    verbatim; the ".0"-".9" name fallback remains a name-resolution
    mechanism and can never bypass the conflict boundary;
  * unsupported families (UC/CHS/UA) stay refused;
  * the catalogue provenance taxonomy: every provenance key is one of
    the five fixed kinds, and no row claims PRODUCTION_PROVEN or
    HUMAN_CONFIRMED without the evidence;
  * snapshot cross-verification against the user-captured live
    snapshot at /tmp (an honest skip when it is not present — the
    always-on literals below are the same rows, so no assertion
    depends on the file).

No test here modifies any product module, the production matcher, the
DXF path, any existing gate, or the live snapshot.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from app.cad_engine import sections
from app.cad_engine.interface import generate_geometry
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.engineering_data.section_catalogue import (
    CATALOGUE,
    CatalogueMatcher,
    PROVENANCE_BOTH,
    PROVENANCE_KINDS,
    PROVENANCE_LIVE_CATALOGUE_EVIDENCE,
)
from app.engineering_data.source_of_truth import (
    GEOMETRY_DEFINING_FIELDS,
    LIVE_TO_LOCAL_FAMILY,
    RESOLUTION_BLOCKED_REVIEW,
    RESOLUTION_NO_CONFLICT,
    SOURCE_OF_TRUTH_RULE,
    VERDICT_AGREEMENT,
    VERDICT_CONFLICT_BLOCKED,
    evaluate_section_evidence,
    map_live_section_to_local,
)

SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"

# The live rows these proofs exercise, taken verbatim from the
# Milestone E live reconciliation (the snapshot evidence). They are
# also cross-checked against the snapshot itself when it is present
# (TestSnapshotCrossVerification).
LIVE_EA_90X90X10 = {
    "name": "90x90x10EA", "family": "EA", "leg_size": 90.0,
    "thickness": 10.0, "weight_per_metre": 13.3,
}
LIVE_FLAT_100X10 = {
    "name": "100x10FL", "family": "FLAT", "width": 100.0,
    "thickness": 10.0, "weight_per_metre": 7.85,
}
LIVE_250PFC = {
    "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
}
LIVE_310UB40_4 = {
    "name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
}
LIVE_100PFC = {
    "name": "100PFC", "family": "PFC", "depth": 100.0, "flange_width": 50.0,
    "flange_thickness": 7.0, "web_thickness": 4.5, "weight_per_metre": 8.33,
}

ENRICHED_NAMES = (
    "150PFC", "200PFC", "300PFC", "250UB25.7", "250UB37.3", "310UB46.2",
)

ENRICHED_EXPECTED = {
    "150PFC": {"name": "150PFC", "family": "PFC", "depth": 150.0,
               "flange_width": 75.0, "flange_thickness": 9.5,
               "web_thickness": 6.0, "weight_per_metre": 17.7,
               "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE},
    "200PFC": {"name": "200PFC", "family": "PFC", "depth": 200.0,
               "flange_width": 75.0, "flange_thickness": 12.0,
               "web_thickness": 6.0, "weight_per_metre": 22.9,
               "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE},
    "300PFC": {"name": "300PFC", "family": "PFC", "depth": 300.0,
               "flange_width": 90.0, "flange_thickness": 16.0,
               "web_thickness": 8.0, "weight_per_metre": 40.1,
               "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE},
    "250UB25.7": {"name": "250UB25.7", "family": "UB", "depth": 248.0,
                  "flange_width": 124.0, "flange_thickness": 8.0,
                  "web_thickness": 5.0, "weight_per_metre": 25.7,
                  "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE},
    "250UB37.3": {"name": "250UB37.3", "family": "UB", "depth": 256.0,
                  "flange_width": 146.0, "flange_thickness": 10.9,
                  "web_thickness": 6.6, "weight_per_metre": 37.3,
                  "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE},
    "310UB46.2": {"name": "310UB46.2", "family": "UB", "depth": 307.0,
                  "flange_width": 166.0, "flange_thickness": 11.8,
                  "web_thickness": 6.7, "weight_per_metre": 46.2,
                  "provenance": PROVENANCE_LIVE_CATALOGUE_EVIDENCE},
}


def _adapter_member_row(name):
    """A bare member identity wrapper — every section dimension comes
    from the CATALOGUE row, never from this helper (the same shape the
    existing dimensionless-row adapter proofs use)."""
    return {
        "mark": "E1", "section_name": name, "section_name_raw": name,
        "section_family": CATALOGUE[name]["family"], "length_mm": 1000,
        "grade": "300", "quantity": 1, "review_status": "approved",
        "source_page": 1, "source_drawing_id": "E1-D",
    }


def _live_fallback_resolve(token, live_rows):
    """The production matcher's own name-resolution semantics over a
    live-shaped index: normalise -> exact -> ".0"-".9" suffix fallback
    only when the name has no dot. Returns (resolved_name, row) or
    (None, None). Deliberately name-resolution ONLY — it carries no
    source-of-truth authority (the boundary is separate)."""
    base = re.sub(r"\s+", "", token.strip().upper())
    by_norm = {re.sub(r"\s+", "", row["name"].strip().upper()): row
               for row in live_rows}
    if base in by_norm:
        return base, by_norm[base]
    if "." not in base:
        for digit in range(10):
            if base + f".{digit}" in by_norm:
                return base + f".{digit}", by_norm[base + f".{digit}"]
    return None, None


def _snapshot_rows():
    if not SNAPSHOT_PATH.exists():
        pytest.skip("the user-captured live snapshot is not present at "
                    "/tmp/steelspec_live_steel_sections_218.json — cross-verification "
                    "requires that evidence")
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256
    rows = json.loads(raw)
    return {row["name"]: row for row in rows}


# =============================================================================
# the adopted rule
# =============================================================================

class TestSourceOfTruthRule:

    def test_the_rule_is_one_verbatim_constant(self):
        assert isinstance(SOURCE_OF_TRUTH_RULE, str)
        assert "drawing is authoritative" in SOURCE_OF_TRUTH_RULE
        assert "catalogue is authoritative" in SOURCE_OF_TRUTH_RULE
        assert "MUST NOT silently choose one" in SOURCE_OF_TRUTH_RULE
        assert "REVIEW/BLOCKED" in SOURCE_OF_TRUTH_RULE
        assert "explicit human decision" in SOURCE_OF_TRUTH_RULE

    def test_the_conflict_verdict_never_silently_chooses(self):
        verdict = evaluate_section_evidence(
            dict(CATALOGUE["310UB40"]), LIVE_310UB40_4)
        assert verdict.status == VERDICT_CONFLICT_BLOCKED
        assert verdict.resolution == RESOLUTION_BLOCKED_REVIEW
        # both recorded values ride verbatim on the verdict
        field, capture_value, catalogue_value = verdict.conflicting_fields[0]
        assert field == "flange_thickness"
        assert capture_value == 11.8
        assert catalogue_value == 10.2

    def test_weight_is_not_a_geometry_defining_field(self):
        assert "weight_per_metre" not in GEOMETRY_DEFINING_FIELDS


# =============================================================================
# EA mapping — leg_size -> width, explicit and deterministic
# =============================================================================

class TestEAMapping:

    def test_leg_size_maps_deterministically_to_width(self):
        adapted = map_live_section_to_local(LIVE_EA_90X90X10)
        assert adapted["width"] == 90.0
        assert adapted["thickness"] == 10.0
        assert adapted["family"] == "EA"

    def test_the_source_row_is_never_mutated_or_renamed(self):
        source = dict(LIVE_EA_90X90X10)
        map_live_section_to_local(source)
        assert source == LIVE_EA_90X90X10
        assert source["leg_size"] == 90.0

    def test_geometry_receives_the_local_canonical_fields(self):
        adapted = map_live_section_to_local(LIVE_EA_90X90X10)
        assert "leg_size" not in adapted
        assert "width" in adapted and "thickness" in adapted

    def test_the_ea_builder_builds_from_the_adapted_row(self):
        adapted = map_live_section_to_local(LIVE_EA_90X90X10)
        profile = sections.build_ea_profile(adapted, "E1")
        assert profile is not None

    def test_no_guessing_when_the_leg_size_is_missing(self):
        with pytest.raises(ValueError, match="never guesses"):
            map_live_section_to_local({"name": "X", "family": "EA", "thickness": 5.0})

    def test_repeated_mapping_is_equivalent(self):
        assert map_live_section_to_local(LIVE_EA_90X90X10) == \
            map_live_section_to_local(LIVE_EA_90X90X10)


# =============================================================================
# FLAT mapping — FLAT -> FL, explicit and controlled
# =============================================================================

class TestFLATMapping:

    def test_flat_maps_explicitly_to_fl(self):
        assert LIVE_TO_LOCAL_FAMILY.get("FLAT") == "FL"
        adapted = map_live_section_to_local(LIVE_FLAT_100X10)
        assert adapted["family"] == "FL"

    def test_the_source_family_is_never_renamed(self):
        source = dict(LIVE_FLAT_100X10)
        map_live_section_to_local(source)
        assert source["family"] == "FLAT"

    def test_geometry_fields_remain_unchanged(self):
        adapted = map_live_section_to_local(LIVE_FLAT_100X10)
        assert adapted["width"] == 100.0
        assert adapted["thickness"] == 10.0
        assert adapted["weight_per_metre"] == 7.85

    def test_the_plate_builder_builds_from_the_adapted_row(self):
        adapted = map_live_section_to_local(LIVE_FLAT_100X10)
        profile = sections.build_plate_profile(adapted, "E1")
        assert profile is not None

    def test_other_families_pass_through_verbatim(self):
        adapted = map_live_section_to_local(LIVE_250PFC)
        assert adapted == LIVE_250PFC

    def test_the_mapping_table_is_explicit_and_minimal(self):
        assert dict(LIVE_TO_LOCAL_FAMILY) == {"FLAT": "FL"}


# =============================================================================
# safe enrichment — the six conflict-free rows
# =============================================================================

class TestSafeEnrichment:

    @pytest.mark.parametrize("name", ENRICHED_NAMES)
    def test_each_approved_row_is_exactly_the_live_snapshot_values(self, name):
        assert dict(CATALOGUE[name]) == ENRICHED_EXPECTED[name]

    @pytest.mark.parametrize("name", ENRICHED_NAMES)
    def test_each_approved_row_builds_geometry(self, name):
        profile = sections.PROFILE_BUILDERS[CATALOGUE[name]["family"]](
            dict(CATALOGUE[name]), "E1")
        assert profile is not None

    @pytest.mark.parametrize("name", ENRICHED_NAMES)
    def test_each_approved_row_flows_through_the_adapter_to_geometry(self, name):
        validated = real_member_to_validated_member(
            _adapter_member_row(name), CatalogueMatcher())
        assert validated.section_properties == dict(CATALOGUE[name])
        geometry = generate_geometry(validated)
        assert geometry.solid is not None

    def test_no_approximations_the_weights_are_untouched(self):
        for name, expected in ENRICHED_EXPECTED.items():
            assert CATALOGUE[name]["weight_per_metre"] == expected["weight_per_metre"], name

    def test_200UC46_2_was_not_enriched(self):
        assert set(CATALOGUE["200UC46.2"]) == {"name", "family", "weight_per_metre"}

    def test_250PFC_was_not_enriched(self):
        assert set(CATALOGUE["250PFC"]) == {"name", "family", "weight_per_metre"}


# =============================================================================
# 100PFC — precision discrepancy handled deterministically, never a conflict
# =============================================================================

class Test100PFC:

    def test_the_local_weight_is_preserved_and_the_live_value_recorded(self):
        row = dict(CATALOGUE["100PFC"])
        assert row["weight_per_metre"] == 8.3
        assert row["live_catalogue_weight_per_metre"] == 8.33
        assert row["provenance"] == PROVENANCE_BOTH

    def test_the_dimensions_are_complete(self):
        row = dict(CATALOGUE["100PFC"])
        assert row["depth"] == 100.0 and row["flange_width"] == 50.0
        assert row["flange_thickness"] == 7.0 and row["web_thickness"] == 4.5

    def test_the_precision_difference_is_never_a_geometry_conflict(self):
        verdict = evaluate_section_evidence(dict(CATALOGUE["100PFC"]), LIVE_100PFC)
        assert verdict.status == VERDICT_AGREEMENT
        assert verdict.conflicting_fields == ()
        assert verdict.resolution == RESOLUTION_NO_CONFLICT

    def test_the_row_builds_geometry(self):
        profile = sections.build_pfc_profile(dict(CATALOGUE["100PFC"]), "E1")
        assert profile is not None


# =============================================================================
# HARD conflict — 250X90PFC vs live 250PFC, never collapsed
# =============================================================================

class Test250PFCConflict:

    def test_the_capture_row_is_unchanged(self):
        assert dict(CATALOGUE["250X90PFC"]) == {
            "name": "250X90PFC", "family": "PFC", "depth": 250.0,
            "flange_width": 90.0, "flange_thickness": 15.0,
            "web_thickness": 8.0, "weight_per_metre": 35.5,
        }

    def test_the_live_row_was_not_merged_into_the_catalogue(self):
        assert set(CATALOGUE["250PFC"]) == {"name", "family", "weight_per_metre"}
        assert CATALOGUE["250PFC"]["weight_per_metre"] == 35.5

    def test_the_conflict_is_detected_field_by_field(self):
        verdict = evaluate_section_evidence(dict(CATALOGUE["250X90PFC"]), LIVE_250PFC)
        assert verdict.status == VERDICT_CONFLICT_BLOCKED
        assert verdict.resolution == RESOLUTION_BLOCKED_REVIEW
        assert verdict.conflicting_fields == (
            ("flange_thickness", 15.0, 12.0),
            ("web_thickness", 8.0, 7.0),
        )

    def test_visual_name_similarity_cannot_collapse_the_two_rows(self):
        matcher = CatalogueMatcher()
        assert matcher.match("250X90PFC") == dict(CATALOGUE["250X90PFC"])
        assert matcher.match("250PFC") == dict(CATALOGUE["250PFC"])
        assert matcher.match("250X90PFC") != matcher.match("250PFC")
        assert CATALOGUE["250X90PFC"]["flange_thickness"] != LIVE_250PFC["flange_thickness"]
        assert CATALOGUE["250X90PFC"]["web_thickness"] != LIVE_250PFC["web_thickness"]

    def test_the_conflicting_weights_stay_distinct(self):
        assert CATALOGUE["250X90PFC"]["weight_per_metre"] == 35.5
        assert LIVE_250PFC["weight_per_metre"] == 31.8
        assert CATALOGUE["250PFC"]["weight_per_metre"] == 35.5


# =============================================================================
# HARD conflict — capture 310UB40 (11.8) vs live 310UB40.4 (10.2)
# =============================================================================

class Test310UB40Conflict:

    def test_the_capture_value_11_8_is_preserved(self):
        assert CATALOGUE["310UB40"]["flange_thickness"] == 11.8

    def test_the_conflict_with_the_live_row_is_detected(self):
        verdict = evaluate_section_evidence(dict(CATALOGUE["310UB40"]), LIVE_310UB40_4)
        assert verdict.status == VERDICT_CONFLICT_BLOCKED
        assert verdict.resolution == RESOLUTION_BLOCKED_REVIEW
        assert verdict.conflicting_fields == (("flange_thickness", 11.8, 10.2),)

    def test_no_silent_overwrite_in_either_direction(self):
        live_row = dict(LIVE_310UB40_4)
        evaluate_section_evidence(dict(CATALOGUE["310UB40"]), live_row)
        assert CATALOGUE["310UB40"]["flange_thickness"] == 11.8
        assert live_row["flange_thickness"] == 10.2

    def test_the_name_fallback_cannot_bypass_the_conflict_boundary(self):
        # Name resolution (the production matcher's exact + ".0"-".9"
        # fallback) resolves 310UB40 -> 310UB40.4 over a live-shaped
        # index — and the boundary still blocks: the fallback identifies
        # a row, it never authorises its geometry for the capture.
        resolved_name, resolved_row = _live_fallback_resolve(
            "310UB40", [LIVE_310UB40_4, LIVE_250PFC])
        assert resolved_name == "310UB40.4"
        assert resolved_row["flange_thickness"] == 10.2
        verdict = evaluate_section_evidence(dict(CATALOGUE["310UB40"]), resolved_row)
        assert verdict.status == VERDICT_CONFLICT_BLOCKED
        assert verdict.resolution == RESOLUTION_BLOCKED_REVIEW

    def test_the_fallback_still_resolves_names_inside_the_catalogue(self):
        # The fallback remains a name-resolution mechanism: matching
        # works exactly as before, for both the capture row and a
        # dotted query.
        matcher = CatalogueMatcher()
        assert matcher.match("310UB40") == dict(CATALOGUE["310UB40"])
        assert matcher.match("310UB 40") == dict(CATALOGUE["310UB40"])

    def test_the_boundary_blocks_but_never_merges(self):
        verdict = evaluate_section_evidence(dict(CATALOGUE["310UB40"]), LIVE_310UB40_4)
        assert verdict.conflicting_fields == (("flange_thickness", 11.8, 10.2),)
        assert "11.8" not in str(LIVE_310UB40_4)
        assert "10.2" not in str(dict(CATALOGUE["310UB40"]))


# =============================================================================
# unsupported families stay refused
# =============================================================================

class TestUnsupportedFamilies:

    @pytest.mark.parametrize("family", ["UC", "CHS", "UA"])
    def test_no_builder_exists_for_the_unsupported_families(self, family):
        assert family not in sections.PROFILE_BUILDERS

    def test_uc_stays_weight_only_in_the_catalogue(self):
        assert set(CATALOGUE["200UC46.2"]) == {"name", "family", "weight_per_metre"}

    def test_no_chs_ua_or_shs_rows_exist_in_the_catalogue(self):
        for name in CATALOGUE:
            assert "CHS" not in name and "UA" not in name and "SHS" not in name


# =============================================================================
# provenance taxonomy
# =============================================================================

class TestProvenanceTaxonomy:

    def test_the_taxonomy_has_exactly_the_five_fixed_kinds(self):
        assert len(PROVENANCE_KINDS) == 5
        assert set(PROVENANCE_KINDS) == {
            "CAPTURE_EVIDENCE", "LIVE_CATALOGUE_EVIDENCE", "BOTH",
            "HUMAN_CONFIRMED", "PRODUCTION_PROVEN",
        }

    def test_every_provenance_key_uses_a_known_kind(self):
        for name in CATALOGUE:
            row = dict(CATALOGUE[name])
            if "provenance" in row:
                assert row["provenance"] in PROVENANCE_KINDS, name

    def test_the_six_enriched_rows_declare_live_catalogue_evidence(self):
        for name in ENRICHED_NAMES:
            assert CATALOGUE[name]["provenance"] == PROVENANCE_LIVE_CATALOGUE_EVIDENCE, name

    def test_100pfc_declares_both(self):
        assert CATALOGUE["100PFC"]["provenance"] == PROVENANCE_BOTH

    def test_no_row_claims_production_or_human_confirmation_without_evidence(self):
        for name in CATALOGUE:
            row = dict(CATALOGUE[name])
            assert row.get("provenance") not in ("HUMAN_CONFIRMED", "PRODUCTION_PROVEN"), name

    def test_carrying_provenance_never_implies_production_proven(self):
        for name in ENRICHED_NAMES:
            assert CATALOGUE[name]["provenance"] != "PRODUCTION_PROVEN"


# =============================================================================
# snapshot cross-verification (honest skip when the evidence is absent)
# =============================================================================

class TestSnapshotCrossVerification:

    def test_the_snapshot_is_the_captured_218_row_evidence(self):
        rows = _snapshot_rows()
        assert len(rows) == 218
        by_family = {}
        for row in rows.values():
            by_family[row["family"]] = by_family.get(row["family"], 0) + 1
        assert by_family == {
            "UB": 28, "UC": 13, "PFC": 10, "EA": 40,
            "SHS": 38, "RHS": 32, "CHS": 34, "FLAT": 23,
        }

    @pytest.mark.parametrize("name,expected", [
        ("150PFC", ENRICHED_EXPECTED["150PFC"]),
        ("200PFC", ENRICHED_EXPECTED["200PFC"]),
        ("300PFC", ENRICHED_EXPECTED["300PFC"]),
        ("250UB25.7", ENRICHED_EXPECTED["250UB25.7"]),
        ("250UB37.3", ENRICHED_EXPECTED["250UB37.3"]),
        ("310UB46.2", ENRICHED_EXPECTED["310UB46.2"]),
    ])
    def test_enriched_dimensions_equal_the_snapshot_rows(self, name, expected):
        rows = _snapshot_rows()
        live_row = rows[name]
        for field in ("depth", "flange_width", "flange_thickness",
                      "web_thickness", "weight_per_metre"):
            assert live_row.get(field) == expected[field], (name, field)

    def test_100pfc_snapshot_row_differs_only_in_weight_precision(self):
        rows = _snapshot_rows()
        live_row = rows["100PFC"]
        assert live_row["weight_per_metre"] == 8.33
        assert live_row["depth"] == CATALOGUE["100PFC"]["depth"]
        assert live_row["flange_thickness"] == CATALOGUE["100PFC"]["flange_thickness"]
        assert live_row["weight_per_metre"] != CATALOGUE["100PFC"]["weight_per_metre"]

    def test_the_snapshot_250pfc_row_is_not_in_the_catalogue_geometry(self):
        rows = _snapshot_rows()
        assert rows["250PFC"]["weight_per_metre"] == 31.8
        assert rows["250PFC"]["flange_thickness"] == 12.0
        assert CATALOGUE["250PFC"]["weight_per_metre"] == 35.5
        assert "depth" not in CATALOGUE["250PFC"]
        verdict = evaluate_section_evidence(dict(CATALOGUE["250X90PFC"]), rows["250PFC"])
        assert verdict.status == VERDICT_CONFLICT_BLOCKED

    def test_the_snapshot_310ub40_4_row_conflicts_with_the_capture_row(self):
        rows = _snapshot_rows()
        assert rows["310UB40.4"]["flange_thickness"] == 10.2
        assert CATALOGUE["310UB40"]["flange_thickness"] == 11.8
        verdict = evaluate_section_evidence(dict(CATALOGUE["310UB40"]), rows["310UB40.4"])
        assert verdict.status == VERDICT_CONFLICT_BLOCKED
        assert verdict.resolution == RESOLUTION_BLOCKED_REVIEW
