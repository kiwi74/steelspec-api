"""
Milestone C — VERSIONED LOCAL SECTION CATALOGUE: focused proofs.

The catalogue module (app/engineering_data/section_catalogue.py) and
its CatalogueMatcher are proven here, unit by unit:

  * version identity: content-addressed (no fake semver), the digest
    re-derivable from the documented recipe, stable across reimports,
    and changed by any content change;
  * contents: exactly the 32 seeded rows — 8 fully-traced dimensioned
    (the inspection's "six rows" resolved explicitly as eight, the
    three PL variants never collapsed) + 14 evidence-backed plate rows
    (width/thickness from the drawings' own section labels, NO weight
    key, Milestone D) + 7 live-catalogue-enriched rows (complete
    geometry from the live snapshot, verified before enrichment,
    Milestone E1) + 3 weight-only rows carrying NO dimension keys at
    all;
  * immutability: the mapping and every row refuse mutation; match()
    returns detached plain dicts, so callers can never corrupt the
    catalogue;
  * matching: the live SectionMatcher's proven semantics —
    whitespace-collapsing case-insensitive normalisation, exact first,
    then the ".0"..".9" fallback only when the name has no dot;
    unknown / None / non-str / blank fail closed with None;
  * dimensionless rows fail closed (matching succeeds, catalogue
    presence is true, geometry refuses to guess — the critical test);
  * unsupported families (UC/CHS/UA/SHS) stay refused even when
    catalogue-present;
  * offline CAD: the 22 genuine-member-driven dimensioned rows — the
    eight fully-traced plus the fourteen evidence-backed plates —
    drive the genuine adapter -> generate_geometry() ->
    place_member_geometry() path with the genuine captured member
    rows, no synthetic dimensions (the seven E1-enriched rows have no
    genuine member rows with real lengths; their adapter/geometry
    proofs live in tests/test_real_world_e1_source_of_truth.py);
  * no runtime I/O: the module's imports are stdlib-only, no open().

No test here modifies any product module; the two genuine product
changes this milestone makes (catalogue_version on the 7AA result and
the 7AF drawing manifest) are proven end-to-end in
tests/test_real_world_local_catalogue_workflow.py.
"""

import ast
import hashlib
import importlib
import json

import pytest

from app.cad_engine import sections
from app.cad_engine.errors import (
    GeometryValidationError,
    UnsupportedSectionFamilyError,
)
from app.cad_engine.interface import (
    ValidatedSteelMember,
    generate_geometry,
)
from app.cad_engine.placement import MemberPlacement, place_member_geometry
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.engineering_data import section_catalogue as catalogue_module
from app.engineering_data.section_catalogue import (
    CATALOGUE,
    CATALOGUE_DIGEST,
    CATALOGUE_VERSION,
    CatalogueMatcher,
)

# The genuine captured member rows the offline-CAD proof drives — imported
# from the real-world fixture modules so no dimension or length here is
# invented. The FL/EA rows mirror the genuine 7AX page-5 A-A rows exactly
# (PL008 180X20FL x 340 mm, CL004 90X10EA x 165 mm).
from tests.test_real_world_material_extraction import SELBY_MEMBER_ROWS  # noqa: E402
from tests.test_real_world_multi_member_connection import JOURNEY_MEMBER_ROWS  # noqa: E402

GENUINE_FL_EA_MEMBER_ROWS = {
    "PL008": {"mark": "PL008", "section_name": "180X20FL", "section_name_raw": "180X20FL",
              "section_family": "FL", "length_mm": 340, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 5,
              "source_drawing_id": "SELBY-C1136"},
    "CL004": {"mark": "CL004", "section_name": "90X10EA", "section_name_raw": "90X10EA",
              "section_family": "EA", "length_mm": 165, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 5,
              "source_drawing_id": "SELBY-C1136"},
}

# The genuine captured rows for the fourteen plate rows (Milestone D) —
# each row mirrors its capture entry verbatim: mark, section spelling
# (case preserved from the drawing), length_mm, quantity and page. The
# width/thickness never comes from this file — it comes from the
# CATALOGUE row these members resolve.
GENUINE_PLATE_MEMBER_ROWS = {
    "PL020": {"mark": "PL020", "section_name": "143x6PL", "section_name_raw": "143x6PL",
              "section_family": "PL", "length_mm": 186, "grade": "300", "quantity": 2,
              "review_status": "approved", "source_page": 11,
              "source_drawing_id": "SELBY-C1136"},
    "PL033": {"mark": "PL033", "section_name": "145X12PL", "section_name_raw": "145X12PL",
              "section_family": "PL", "length_mm": 230, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 7,
              "source_drawing_id": "SELBY-C1136"},
    "PL007": {"mark": "PL007", "section_name": "210x10PL", "section_name_raw": "210x10PL",
              "section_family": "PL", "length_mm": 282, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 14,
              "source_drawing_id": "SELBY-C1136"},
    "PL035": {"mark": "PL035", "section_name": "228x10PL", "section_name_raw": "228x10PL",
              "section_family": "PL", "length_mm": 313, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 18,
              "source_drawing_id": "SELBY-C1136"},
    "PL022": {"mark": "PL022", "section_name": "140x10PL", "section_name_raw": "140x10PL",
              "section_family": "PL", "length_mm": 295, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 29,
              "source_drawing_id": "SELBY-C1136"},
    "PL013": {"mark": "PL013", "section_name": "64x8PL", "section_name_raw": "64x8PL",
              "section_family": "PL", "length_mm": 133, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 30,
              "source_drawing_id": "SELBY-C1136"},
    "PL024": {"mark": "PL024", "section_name": "133x10PL", "section_name_raw": "133x10PL",
              "section_family": "PL", "length_mm": 203, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 31,
              "source_drawing_id": "SELBY-C1136"},
    "PL023": {"mark": "PL023", "section_name": "81x16PL", "section_name_raw": "81x16PL",
              "section_family": "PL", "length_mm": 219, "grade": "300", "quantity": 4,
              "review_status": "approved", "source_page": 32,
              "source_drawing_id": "SELBY-C1136"},
    "PL002": {"mark": "PL002", "section_name": "130X10FL", "section_name_raw": "130X10FL",
              "section_family": "FL", "length_mm": 210, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 3,
              "source_drawing_id": "SELBY-C1136"},
    "PL029": {"mark": "PL029", "section_name": "130x12FL", "section_name_raw": "130x12FL",
              "section_family": "FL", "length_mm": 225, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 2,
              "source_drawing_id": "SELBY-C1136"},
    "PL027": {"mark": "PL027", "section_name": "90x10FL", "section_name_raw": "90x10FL",
              "section_family": "FL", "length_mm": 220, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 2,
              "source_drawing_id": "SELBY-C1136"},
    "PL005": {"mark": "PL005", "section_name": "100x10FL", "section_name_raw": "100x10FL",
              "section_family": "FL", "length_mm": 230, "grade": "300", "quantity": 2,
              "review_status": "approved", "source_page": 14,
              "source_drawing_id": "SELBY-C1136"},
    "PL001": {"mark": "PL001", "section_name": "75x8FL", "section_name_raw": "75x8FL",
              "section_family": "FL", "length_mm": 237, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 13,
              "source_drawing_id": "SELBY-C1136"},
    "PL003": {"mark": "PL003", "section_name": "300x8FL", "section_name_raw": "300x8FL",
              "section_family": "FL", "length_mm": 300, "grade": "300", "quantity": 1,
              "review_status": "approved", "source_page": 13,
              "source_drawing_id": "SELBY-C1136"},
}

DIMENSIONED_NAMES = (
    "310UB40", "250X90PFC", "250X12FL", "180X20FL", "90X10EA",
    "160X10PL", "165X10PL", "81X10PL",
)

# Milestone D — the fourteen evidence-backed plate rows: width/thickness
# from the genuine Selby drawing labels, family exactly as captured
# (PL stays PL, FL stays FL), NO weight_per_metre key.
PLATE_ONLY_NAMES = (
    "143X6PL", "145X12PL", "210X10PL", "228X10PL",
    "140X10PL", "64X8PL", "133X10PL", "81X16PL",
    "130X10FL", "130X12FL", "90X10FL", "100X10FL",
    "75X8FL", "300X8FL",
)

PLATE_ONLY_VALUES = {
    "143X6PL": ("PL", 143.0, 6.0),
    "145X12PL": ("PL", 145.0, 12.0),
    "210X10PL": ("PL", 210.0, 10.0),
    "228X10PL": ("PL", 228.0, 10.0),
    "140X10PL": ("PL", 140.0, 10.0),
    "64X8PL": ("PL", 64.0, 8.0),
    "133X10PL": ("PL", 133.0, 10.0),
    "81X16PL": ("PL", 81.0, 16.0),
    "130X10FL": ("FL", 130.0, 10.0),
    "130X12FL": ("FL", 130.0, 12.0),
    "90X10FL": ("FL", 90.0, 10.0),
    "100X10FL": ("FL", 100.0, 10.0),
    "75X8FL": ("FL", 75.0, 8.0),
    "300X8FL": ("FL", 300.0, 8.0),
}

WEIGHT_ONLY_NAMES = (
    "200UB30.4", "200UC46.2", "250PFC",
)

# Milestone E1 — the seven live-catalogue-enriched rows: complete
# geometry from the live steel_sections snapshot, each verified before
# enrichment (exact name, complete geometry, weight agreement,
# compatible family, current builder support, no genuine-capture
# conflict). 100PFC keeps its local weight 8.3 with the live 8.33
# recorded as evidence, never substituted.
ENRICHED_NAMES = (
    "100PFC", "150PFC", "200PFC", "300PFC",
    "250UB25.7", "250UB37.3", "310UB46.2",
)

ALL_NAMES = tuple(sorted(DIMENSIONED_NAMES + PLATE_ONLY_NAMES + ENRICHED_NAMES + WEIGHT_ONLY_NAMES))

# The 29 geometry-capable rows — the eight fully-traced rows, the
# fourteen evidence-backed plates and the seven E1-enriched rows:
# every row whose family has a builder AND whose dimension keys are
# present.
ALL_DIMENSIONED_NAMES = DIMENSIONED_NAMES + PLATE_ONLY_NAMES + ENRICHED_NAMES

# The offline-CAD proof below drives each row by a GENUINE captured
# member row with a real length; the seven enriched rows have no
# genuine member rows of that kind, so their adapter/geometry proofs
# live in tests/test_real_world_e1_source_of_truth.py instead.
OFFLINE_CAD_NAMES = DIMENSIONED_NAMES + PLATE_ONLY_NAMES


def _canonical_json(rows):
    """The documented content-identity recipe, re-derived independently."""
    return json.dumps(
        {name: dict(row) for name, row in rows.items()},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _member_row(mark, section_name, family, length_mm):
    return {
        "mark": mark, "section_name": section_name, "section_name_raw": section_name,
        "section_family": family, "length_mm": length_mm, "grade": "300",
        "quantity": 1, "review_status": "approved", "source_page": 1,
        "source_drawing_id": "D-001",
    }


def _validated_member(section_name, *, mark="M1", length_mm=1000.0, section_properties=None):
    return ValidatedSteelMember(
        mark=mark, section=section_name, length_mm=length_mm, material="300",
        orientation=None, connection_refs=[], source_refs=[],
        validation_status="validated", section_properties=section_properties,
    )


# =============================================================================
# version identity — content-addressed, deterministic, no fake semver
# =============================================================================

class TestVersionIdentity:

    def test_version_is_derived_from_the_digest_not_a_fake_semver(self):
        assert CATALOGUE_VERSION == f"local-section-catalogue@{CATALOGUE_DIGEST[:16]}"
        assert len(CATALOGUE_DIGEST) == 64
        assert all(c in "0123456789abcdef" for c in CATALOGUE_DIGEST)

    def test_digest_recomputes_from_the_documented_recipe(self):
        digest = hashlib.sha256(_canonical_json(CATALOGUE).encode("utf-8")).hexdigest()
        assert digest == CATALOGUE_DIGEST

    def test_version_and_digest_are_stable_across_reimports(self):
        # importlib.reload recomputes both from the source literals: the
        # identity is code-determined, not once-assigned.
        version, digest = CATALOGUE_VERSION, CATALOGUE_DIGEST
        reloaded = importlib.reload(catalogue_module)
        assert reloaded.CATALOGUE_VERSION == version
        assert reloaded.CATALOGUE_DIGEST == digest

    def test_any_content_change_changes_the_digest(self):
        changed = {name: dict(row) for name, row in CATALOGUE.items()}
        changed["310UB40"] = dict(changed["310UB40"])
        changed["310UB40"]["weight_per_metre"] = 40.41
        changed_digest = hashlib.sha256(
            _canonical_json(changed).encode("utf-8")).hexdigest()
        assert changed_digest != CATALOGUE_DIGEST

    def test_the_plates_changed_the_digest_and_all_32_are_represented(self):
        # Removing the fourteen plate rows yields the Milestone C
        # catalogue of 18 rows — and a DIFFERENT digest, proving the
        # current one was computed over all 32. Derived from CATALOGUE
        # alone, never asserted against a hard-coded digest or a fake
        # semantic version.
        without_plates = {
            name: dict(row) for name, row in CATALOGUE.items()
            if name not in set(PLATE_ONLY_NAMES)
        }
        assert len(without_plates) == 18
        assert hashlib.sha256(
            _canonical_json(without_plates).encode("utf-8")).hexdigest() != CATALOGUE_DIGEST
        assert set(CATALOGUE) == set(ALL_NAMES)
        assert len(CATALOGUE) == 32


# =============================================================================
# contents — the 32 seeded rows, the six-vs-eight resolution, no invention
# =============================================================================

class TestCatalogueContents:

    def test_exactly_32_rows(self):
        assert len(CATALOGUE) == 32
        assert set(CATALOGUE) == set(ALL_NAMES)

    def test_every_row_name_is_its_own_normalised_key(self):
        for name in CATALOGUE:
            assert CATALOGUE[name]["name"] == name
            assert " " not in name
            assert name == name.upper()

    def test_the_eight_dimensioned_rows_are_exactly_the_traced_values(self):
        assert dict(CATALOGUE["310UB40"]) == {
            "name": "310UB40", "family": "UB", "depth": 304.0,
            "flange_width": 165.0, "flange_thickness": 11.8,
            "web_thickness": 6.1, "weight_per_metre": 40.4,
        }
        assert dict(CATALOGUE["250X90PFC"]) == {
            "name": "250X90PFC", "family": "PFC", "depth": 250.0,
            "flange_width": 90.0, "flange_thickness": 15.0,
            "web_thickness": 8.0, "weight_per_metre": 35.5,
        }
        assert dict(CATALOGUE["250X12FL"]) == {
            "name": "250X12FL", "family": "PL", "width": 250.0,
            "thickness": 12.0, "weight_per_metre": 23.6,
        }
        assert dict(CATALOGUE["180X20FL"]) == {
            "name": "180X20FL", "family": "FL", "width": 180.0,
            "thickness": 20.0, "weight_per_metre": 28.3,
        }
        assert dict(CATALOGUE["90X10EA"]) == {
            "name": "90X10EA", "family": "EA", "width": 90.0,
            "thickness": 10.0, "weight_per_metre": 13.3,
        }
        assert dict(CATALOGUE["160X10PL"]) == {
            "name": "160X10PL", "family": "PL", "width": 160.0,
            "thickness": 10.0, "weight_per_metre": 12.6,
        }
        assert dict(CATALOGUE["165X10PL"]) == {
            "name": "165X10PL", "family": "PL", "width": 165.0,
            "thickness": 10.0, "weight_per_metre": 13.0,
        }
        assert dict(CATALOGUE["81X10PL"]) == {
            "name": "81X10PL", "family": "PL", "width": 81.0,
            "thickness": 10.0, "weight_per_metre": 6.4,
        }

    def test_the_three_plate_variants_are_three_distinct_rows_never_collapsed(self):
        # The inspection's "six rows" listed the plate variants separately;
        # the catalogue resolves that explicitly as EIGHT rows, the three
        # plate variants each carrying its own width and weight.
        names = {"160X10PL", "165X10PL", "81X10PL"}
        assert names <= set(CATALOGUE)
        widths = {CATALOGUE[n]["width"] for n in names}
        weights = {CATALOGUE[n]["weight_per_metre"] for n in names}
        assert widths == {160.0, 165.0, 81.0}
        assert weights == {12.6, 13.0, 6.4}

    def test_the_fourteen_plate_rows_are_exactly_name_family_width_thickness(self):
        # Milestone D: each plate row carries exactly the four keys its
        # drawing evidence supports — the section label's own width and
        # thickness, the family exactly as captured (PL stays PL, FL
        # stays FL, never relabelled) — and nothing else.
        for name in PLATE_ONLY_NAMES:
            family, width, thickness = PLATE_ONLY_VALUES[name]
            assert dict(CATALOGUE[name]) == {
                "name": name, "family": family,
                "width": width, "thickness": thickness,
            }, name

    def test_no_plate_row_carries_a_weight_key(self):
        # Weight honesty: no weight_per_metre is evidenced anywhere in
        # the repository for these rows, so the key is absent entirely —
        # nothing calculated, nothing imported, nothing guessed.
        for name in PLATE_ONLY_NAMES:
            assert "weight_per_metre" not in CATALOGUE[name], name
            assert CATALOGUE[name].get("weight_per_metre") is None, name

    def test_no_accidental_collapse_between_the_plate_rows(self):
        # 143X6PL / 145X12PL / 210X10PL / 228X10PL are four distinct rows
        # with distinct widths; every FL row is distinct by width and/or
        # thickness; none of the fourteen collapses into another row.
        pl_widths = {
            CATALOGUE[name]["width"] for name in
            ("143X6PL", "145X12PL", "210X10PL", "228X10PL")
        }
        assert pl_widths == {143.0, 145.0, 210.0, 228.0}
        fl_pairs = {
            (CATALOGUE[name]["width"], CATALOGUE[name]["thickness"])
            for name in PLATE_ONLY_NAMES if CATALOGUE[name]["family"] == "FL"
        }
        assert fl_pairs == {
            (130.0, 10.0), (130.0, 12.0), (90.0, 10.0), (100.0, 10.0),
            (75.0, 8.0), (300.0, 8.0),
        }
        assert len(set(PLATE_ONLY_NAMES)) == len(PLATE_ONLY_NAMES) == 14

    def test_the_weight_only_rows_carry_no_dimension_keys_at_all(self):
        for name in WEIGHT_ONLY_NAMES:
            assert set(CATALOGUE[name]) == {"name", "family", "weight_per_metre"}, name
            for key in ("depth", "flange_width", "flange_thickness",
                        "web_thickness", "width", "thickness"):
                assert key not in CATALOGUE[name], (name, key)
                assert CATALOGUE[name].get(key) is None, (name, key)

    def test_weight_only_values_are_the_live_seed_values(self):
        expected = {
            "200UB30.4": 30.4, "200UC46.2": 46.2, "250PFC": 35.5,
        }
        for name, weight in expected.items():
            assert CATALOGUE[name]["weight_per_metre"] == weight, name

    def test_the_e1_enriched_rows_are_exactly_the_live_snapshot_values(self):
        # Milestone E1: each enriched row carries the live snapshot's
        # dimensions verbatim, the pre-existing local weight preserved,
        # and its provenance kind. 100PFC additionally records the live
        # weight as source evidence (never substituted).
        expected = {
            "150PFC": {"depth": 150.0, "flange_width": 75.0, "flange_thickness": 9.5,
                       "web_thickness": 6.0, "weight_per_metre": 17.7},
            "200PFC": {"depth": 200.0, "flange_width": 75.0, "flange_thickness": 12.0,
                       "web_thickness": 6.0, "weight_per_metre": 22.9},
            "300PFC": {"depth": 300.0, "flange_width": 90.0, "flange_thickness": 16.0,
                       "web_thickness": 8.0, "weight_per_metre": 40.1},
            "250UB25.7": {"depth": 248.0, "flange_width": 124.0, "flange_thickness": 8.0,
                          "web_thickness": 5.0, "weight_per_metre": 25.7},
            "250UB37.3": {"depth": 256.0, "flange_width": 146.0, "flange_thickness": 10.9,
                          "web_thickness": 6.6, "weight_per_metre": 37.3},
            "310UB46.2": {"depth": 307.0, "flange_width": 166.0, "flange_thickness": 11.8,
                          "web_thickness": 6.7, "weight_per_metre": 46.2},
        }
        for name, values in expected.items():
            row = dict(CATALOGUE[name])
            for key, value in values.items():
                assert row[key] == value, (name, key)
            assert row["provenance"] == "LIVE_CATALOGUE_EVIDENCE", name
        pfc100 = dict(CATALOGUE["100PFC"])
        assert pfc100["depth"] == 100.0 and pfc100["flange_width"] == 50.0
        assert pfc100["flange_thickness"] == 7.0 and pfc100["web_thickness"] == 4.5
        assert pfc100["weight_per_metre"] == 8.3
        assert pfc100["live_catalogue_weight_per_metre"] == 8.33
        assert pfc100["provenance"] == "BOTH"

    def test_250X12FL_is_recorded_as_the_genuine_pl_row(self):
        # The genuine Selby page-1 chain resolved 250X12FL as family "PL"
        # (its own fixture row); 180X20FL keeps family "FL" as the genuine
        # FL row. Both dispatch to the plate profile builder.
        assert CATALOGUE["250X12FL"]["family"] == "PL"
        assert CATALOGUE["180X20FL"]["family"] == "FL"
        assert CATALOGUE["90X10EA"]["family"] == "EA"

    def test_every_dimensioned_family_has_a_profile_builder(self):
        # PROFILE_BUILDERS is the single authority on supported families —
        # the catalogue never re-lists them.
        for name in ALL_DIMENSIONED_NAMES:
            assert CATALOGUE[name]["family"] in sections.PROFILE_BUILDERS, name

    def test_200UC46_2_stays_catalogue_present_but_geometry_unsupported(self):
        assert CATALOGUE["200UC46.2"]["family"] == "UC"
        assert "UC" not in sections.PROFILE_BUILDERS


# =============================================================================
# immutability — the catalogue itself can never be changed through any path
# =============================================================================

class TestImmutability:

    def test_the_catalogue_mapping_is_unmodifiable(self):
        with pytest.raises(TypeError):
            CATALOGUE["NEWSECTION"] = {"name": "NEWSECTION"}  # type: ignore[index]
        with pytest.raises(TypeError):
            del CATALOGUE["310UB40"]  # type: ignore[misc]
        # MappingProxyType carries no mutation API at all — no clear(),
        # no pop(), no update().
        for attribute in ("clear", "pop", "update"):
            assert not hasattr(CATALOGUE, attribute)

    def test_row_contents_are_unmodifiable(self):
        with pytest.raises(TypeError):
            CATALOGUE["310UB40"]["depth"] = 1.0  # type: ignore[index]
        with pytest.raises(TypeError):
            del CATALOGUE["310UB40"]["depth"]  # type: ignore[misc]

    def test_match_returns_a_detached_plain_dict(self):
        matcher = CatalogueMatcher()
        row = matcher.match("310UB40")
        assert type(row) is dict
        assert row == dict(CATALOGUE["310UB40"])

    def test_mutating_a_returned_row_never_corrupts_the_catalogue(self):
        matcher = CatalogueMatcher()
        row = matcher.match("310UB40")
        row["depth"] = 1.0
        del row["flange_width"]
        assert CATALOGUE["310UB40"]["depth"] == 304.0
        assert CATALOGUE["310UB40"]["flange_width"] == 165.0
        assert matcher.match("310UB40") == dict(CATALOGUE["310UB40"])

    def test_repeated_lookups_are_equivalent(self):
        matcher = CatalogueMatcher()
        assert matcher.match("310UB40") == matcher.match("310 UB 40")
        assert matcher.match("310ub40") == matcher.match("310UB40")

    def test_the_new_plate_rows_are_unmodifiable(self):
        with pytest.raises(TypeError):
            CATALOGUE["210X10PL"]["width"] = 1.0  # type: ignore[index]
        with pytest.raises(TypeError):
            del CATALOGUE["130X12FL"]["thickness"]  # type: ignore[misc]

    def test_mutating_a_plate_match_result_never_corrupts_the_catalogue(self):
        matcher = CatalogueMatcher()
        row = matcher.match("210X10PL")
        row["width"] = 1.0
        row["weight_per_metre"] = 99.9  # even a caller-added key never leaks back
        assert CATALOGUE["210X10PL"]["width"] == 210.0
        assert "weight_per_metre" not in CATALOGUE["210X10PL"]
        assert matcher.match("210X10PL") == dict(CATALOGUE["210X10PL"])

    def test_iteration_order_is_deterministic(self):
        assert list(CATALOGUE) == sorted(CATALOGUE)
        assert tuple(CATALOGUE)[0] == "100PFC"


# =============================================================================
# matching semantics — the live matcher's contract, fail-closed
# =============================================================================

class TestExactLookup:

    @pytest.mark.parametrize("name", ALL_NAMES)
    def test_every_catalogue_row_matches_exactly(self, name):
        matcher = CatalogueMatcher()
        assert matcher.match(name) == dict(CATALOGUE[name])

    @pytest.mark.parametrize("name", ALL_NAMES)
    def test_every_catalogue_row_matches_case_insensitively(self, name):
        matcher = CatalogueMatcher()
        assert matcher.match(name.lower()) == dict(CATALOGUE[name])


class TestNormalisation:

    @pytest.mark.parametrize("raw,expected", [
        ("310 UB 40", "310UB40"),
        ("310\tUB  40", "310UB40"),
        ("  250X90PFC  ", "250X90PFC"),
        ("250x90pfc", "250X90PFC"),
        ("160x10pl", "160X10PL"),
        ("210x10pl", "210X10PL"),
        ("  228x10pl  ", "228X10PL"),
        ("130 x 10 fl", "130X10FL"),
    ])
    def test_whitespace_case_and_padding_are_normalised(self, raw, expected):
        matcher = CatalogueMatcher()
        assert matcher.match(raw) == dict(CATALOGUE[expected])

    def test_fail_closed_inputs_return_none(self):
        matcher = CatalogueMatcher()
        assert matcher.match(None) is None
        assert matcher.match(42) is None
        assert matcher.match("") is None
        assert matcher.match("   ") is None


class TestUnknownRefusal:

    @pytest.mark.parametrize("raw", [
        "CHS114.3X6", "89X89X3.5SHS", "75X75X6UA", "250X90PFCX",
        "310UB40.5", "NOTASECTION", "250UB99", "200UC46.5",
    ])
    def test_unknown_names_match_nothing(self, raw):
        assert CatalogueMatcher().match(raw) is None


class TestDotFallback:

    def _row(self, name):
        return {"name": name, "family": "UB", "depth": 304.0,
                "flange_width": 165.0, "flange_thickness": 11.8,
                "web_thickness": 6.1, "weight_per_metre": 40.4}

    def test_the_dot_fallback_matches_dotted_rows(self):
        matcher = CatalogueMatcher({"310UB40.2": self._row("310UB40.2")})
        assert matcher.match("310UB40") == self._row("310UB40.2")
        assert matcher.match("310UB40.2") == self._row("310UB40.2")

    def test_a_dotted_query_never_falls_back(self):
        # "310UB40.7" contains a dot, so only the exact name is tried —
        # the live matcher's own rule, preserved exactly.
        matcher = CatalogueMatcher({"310UB40.2": self._row("310UB40.2")})
        assert matcher.match("310UB40.7") is None

    def test_exact_beats_the_fallback(self):
        exact = dict(self._row("310UB40"))
        suffixed = dict(self._row("310UB40.0"))
        matcher = CatalogueMatcher({"310UB40": exact, "310UB40.0": suffixed})
        assert matcher.match("310UB40") == exact

    @pytest.mark.parametrize("raw,expected", [
        ("250UB37", "250UB37.3"),
        ("200UC46", "200UC46.2"),
    ])
    def test_the_fallback_reaches_the_real_weight_only_rows(self, raw, expected):
        # The live matcher's own semantics: a dotless name falls back to
        # its ".0"..".9" suffixes, so "250UB37" genuinely resolves the
        # catalogue's "250UB37.3" row.
        matcher = CatalogueMatcher()
        assert matcher.match(raw) == dict(CATALOGUE[expected])

    def test_the_first_fallback_suffix_wins(self):
        first = dict(self._row("310UB40.1"))
        second = dict(self._row("310UB40.2"))
        matcher = CatalogueMatcher({"310UB40.1": first, "310UB40.2": second})
        assert matcher.match("310UB40") == first

    def test_the_default_matcher_declares_the_catalogue_version(self):
        assert CatalogueMatcher().catalogue_version == CATALOGUE_VERSION
        # the production pipeline reads exactly this attribute
        assert getattr(CatalogueMatcher(), "catalogue_version", None) == CATALOGUE_VERSION

    def test_an_override_catalogue_declares_no_version(self):
        # An explicitly supplied catalogue is not THE versioned catalogue;
        # it must never masquerade as it.
        matcher = CatalogueMatcher({"310UB40.2": self._row("310UB40.2")})
        assert matcher.catalogue_version is None


# =============================================================================
# dimensionless rows — matching succeeds, geometry fails closed (CRITICAL)
# =============================================================================

class TestDimensionlessRowsFailClosed:

    def test_200UB30_4_matches_with_weights_only_and_no_guessed_dimensions(self):
        row = CatalogueMatcher().match("200UB30.4")
        assert row is not None
        assert set(row) == {"name", "family", "weight_per_metre"}
        assert row["family"] == "UB"
        assert row["weight_per_metre"] == 30.4
        assert row.get("depth") is None
        assert row.get("flange_width") is None

    def test_200UB30_4_reaches_the_adapter_but_geometry_refuses_to_guess(self):
        matcher = CatalogueMatcher()
        row = _member_row("M1", "200UB30.4", "UB", 1000)
        validated = real_member_to_validated_member(row, matcher)
        # catalogue presence is true: the matched row rides on the member
        assert validated.section_properties == dict(CATALOGUE["200UB30.4"])
        with pytest.raises(GeometryValidationError, match="Refusing to guess"):
            generate_geometry(validated)

    def test_the_refusal_names_the_missing_fields(self):
        matcher = CatalogueMatcher()
        validated = real_member_to_validated_member(
            _member_row("M1", "200UB30.4", "UB", 1000), matcher)
        with pytest.raises(GeometryValidationError) as error:
            generate_geometry(validated)
        for missing in ("depth", "flange_width", "flange_thickness", "web_thickness"):
            assert missing in str(error.value)

    @pytest.mark.parametrize("name", ["200UB30.4", "250PFC"])
    def test_every_dimensionless_row_fails_geometry_closed(self, name):
        # 200UC46.2 is excluded: it is refused earlier, by family, as the
        # unsupported-family proof covers. The seven E1-enriched rows are
        # dimensioned now and proven in the E1 source-of-truth suite.
        matcher = CatalogueMatcher()
        validated = real_member_to_validated_member(
            _member_row("M1", name, CATALOGUE[name]["family"], 1000), matcher)
        with pytest.raises(GeometryValidationError):
            generate_geometry(validated)

    def test_no_fallback_guessing_for_a_dimensionless_pfc(self):
        # A weight-only PFC row exists, but no dimensioned cousin is
        # substituted: 250PFC stays dimensionless and fails closed.
        matcher = CatalogueMatcher()
        validated = real_member_to_validated_member(
            _member_row("M1", "250PFC", "PFC", 1000), matcher)
        with pytest.raises(GeometryValidationError, match="Refusing to guess"):
            generate_geometry(validated)


# =============================================================================
# unsupported families — UC/CHS/UA stay refused even when catalogue-present
# =============================================================================

class TestUnsupportedFamiliesStayRefused:

    def test_200UC46_2_matches_but_the_adapter_refuses_the_family(self):
        matcher = CatalogueMatcher()
        assert matcher.match("200UC46.2") is not None
        with pytest.raises(GeometryValidationError,
                           match="no supported CAD profile builder"):
            real_member_to_validated_member(
                _member_row("M1", "200UC46.2", "UC", 1000), matcher)

    def test_200UC46_2_is_refused_at_geometry_dispatch_too(self):
        member = _validated_member(
            "200UC46.2", section_properties=dict(CATALOGUE["200UC46.2"]))
        with pytest.raises(UnsupportedSectionFamilyError):
            generate_geometry(member)

    def test_no_chs_ua_or_shs_rows_exist_in_the_catalogue(self):
        for name in CATALOGUE:
            assert "CHS" not in name and "UA" not in name and "SHS" not in name
        matcher = CatalogueMatcher()
        assert matcher.match("114.3X3.6CHS") is None
        assert matcher.match("75X75X6UA") is None
        assert matcher.match("89X89X5SHS") is None

    @pytest.mark.parametrize("family", ["CHS", "UA"])
    def test_hypothetical_chs_ua_rows_are_refused_by_family_dispatch(self, family):
        member = _validated_member(
            f"114.3X3.6{family}", section_properties={
                "name": f"114.3X3.6{family}", "family": family,
                "depth": 114.3, "thickness": 3.6, "weight_per_metre": 8.2,
            })
        with pytest.raises(UnsupportedSectionFamilyError):
            generate_geometry(member)


# =============================================================================
# offline CAD — all 22 dimensioned rows through the genuine adapter path
# =============================================================================

# name -> (genuine captured member row, expected family)
OFFLINE_CAD_CASES = {
    "310UB40": (JOURNEY_MEMBER_ROWS["005"], "UB"),
    "250X90PFC": (SELBY_MEMBER_ROWS["001"], "PFC"),
    "250X12FL": (SELBY_MEMBER_ROWS["PL028"], "PL"),
    "180X20FL": (GENUINE_FL_EA_MEMBER_ROWS["PL008"], "FL"),
    "90X10EA": (GENUINE_FL_EA_MEMBER_ROWS["CL004"], "EA"),
    "160X10PL": (JOURNEY_MEMBER_ROWS["PL018"], "PL"),
    "165X10PL": (JOURNEY_MEMBER_ROWS["PL032"], "PL"),
    "81X10PL": (JOURNEY_MEMBER_ROWS["PL025"], "PL"),
    # --- Milestone D: the fourteen plate rows, driven by their genuine
    # capture rows (verbatim, above). Width/thickness come from the
    # CATALOGUE row; the length is the drawing's own extracted length_mm.
    "143X6PL": (GENUINE_PLATE_MEMBER_ROWS["PL020"], "PL"),
    "145X12PL": (GENUINE_PLATE_MEMBER_ROWS["PL033"], "PL"),
    "210X10PL": (GENUINE_PLATE_MEMBER_ROWS["PL007"], "PL"),
    "228X10PL": (GENUINE_PLATE_MEMBER_ROWS["PL035"], "PL"),
    "140X10PL": (GENUINE_PLATE_MEMBER_ROWS["PL022"], "PL"),
    "64X8PL": (GENUINE_PLATE_MEMBER_ROWS["PL013"], "PL"),
    "133X10PL": (GENUINE_PLATE_MEMBER_ROWS["PL024"], "PL"),
    "81X16PL": (GENUINE_PLATE_MEMBER_ROWS["PL023"], "PL"),
    "130X10FL": (GENUINE_PLATE_MEMBER_ROWS["PL002"], "FL"),
    "130X12FL": (GENUINE_PLATE_MEMBER_ROWS["PL029"], "FL"),
    "90X10FL": (GENUINE_PLATE_MEMBER_ROWS["PL027"], "FL"),
    "100X10FL": (GENUINE_PLATE_MEMBER_ROWS["PL005"], "FL"),
    "75X8FL": (GENUINE_PLATE_MEMBER_ROWS["PL001"], "FL"),
    "300X8FL": (GENUINE_PLATE_MEMBER_ROWS["PL003"], "FL"),
}


class TestOfflineCad:

    @pytest.mark.parametrize("name", OFFLINE_CAD_NAMES)
    def test_the_genuine_adapter_resolves_the_catalogue_row(self, name):
        member_row, _ = OFFLINE_CAD_CASES[name]
        validated = real_member_to_validated_member(member_row, CatalogueMatcher())
        # the member keeps its own recorded section_name (the genuine rows
        # spell the plates "160x10PL"); the catalogue row rides alongside
        assert validated.section == member_row["section_name"]
        assert validated.section_properties == dict(CATALOGUE[name])

    @pytest.mark.parametrize("name", OFFLINE_CAD_NAMES)
    def test_generate_geometry_produces_the_catalogue_geometry(self, name):
        member_row, family = OFFLINE_CAD_CASES[name]
        validated = real_member_to_validated_member(member_row, CatalogueMatcher())
        geometry = generate_geometry(validated)
        assert geometry.section_name == member_row["section_name"]
        assert geometry.section_family == family
        assert geometry.length_mm == float(member_row["length_mm"])
        assert geometry.solid is not None

    @pytest.mark.parametrize("name", OFFLINE_CAD_NAMES)
    def test_placement_preserves_the_catalogue_geometry(self, name):
        member_row, _ = OFFLINE_CAD_CASES[name]
        validated = real_member_to_validated_member(member_row, CatalogueMatcher())
        geometry = generate_geometry(validated)
        placed = place_member_geometry(
            geometry, MemberPlacement(x=0.0, y=0.0, z=0.0,
                                      rotation_x=0.0, rotation_y=0.0, rotation_z=0.0))
        assert placed is not geometry
        assert placed.section_name == member_row["section_name"]
        assert placed.length_mm == geometry.length_mm

    def test_the_catalogue_never_imports_the_cad_engine(self):
        # The catalogue module must not import the CAD engine at all —
        # PROFILE_BUILDERS dispatch happens inside generate_geometry(), the
        # one authority on families.
        source = open(catalogue_module.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported == {"hashlib", "json", "re", "types", "typing"}


# =============================================================================
# no runtime I/O — the catalogue needs nothing outside the interpreter
# =============================================================================

class TestNoRuntimeIO:

    def test_module_source_performs_no_io_or_network_access(self):
        source = open(catalogue_module.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Name) and node.id == "open"), \
                "the catalogue module must never open files"
            assert not (isinstance(node, ast.Name) and node.id == "environ"), \
                "the catalogue module must never read environment variables"
        for forbidden in ("supabase", "requests", "socket", "os.", "os.environ",
                          "sys.", "pathlib", "subprocess", "urllib"):
            assert forbidden not in source
