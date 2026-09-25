"""
Milestone J1 — PRODUCTION FLAT -> FL CAD ADMISSION.

Step J0 established, from genuine live Supabase metadata, that
`steel_sections.family` is a PostgreSQL ENUM whose flat-bar value is `FLAT`
(and which REJECTS `FL` and `PL`), and that the four accepted plate identities
are READY_FOR_WRITE as catalogue data. It also established the consequence that
blocked Step J: the CAD engine has no `FLAT` builder, so a `FLAT` row resolved
EXACT and then died at geometry generation.

J1 removes exactly that reason — and nothing else. The catalogue family stays
authoritative; the CAD engine gets an explicitly derived, separately named
family for building a profile. This module is the proof of that admission.

WHAT IS BEING PROVEN, IN ONE SENTENCE
=====================================
An admitted member's section family is still `FLAT` — the value the catalogue,
the drawing and `steel_members.section_family` all carry — while the geometry
is built by the existing `FL` profile builder, which is reached only through the
one explicit table `app/cad_engine/sections.py::CAD_FAMILY_PROJECTION`.

THE FOUR ROWS ARE PREPARED, NOT LIVE — SAID PLAINLY
===================================================
Step J (the catalogue INSERT) has NOT run, and J1 does not run it. The four
plate rows therefore do not exist in the live table. This module composes them
exactly as the accepted live convention shapes a FLAT row — `name`, `family`,
`width`, `thickness`, `weight_per_metre` populated, every OTHER column NULL,
the live lowercase-x spelling — and injects them into the GENUINE
`SectionMatcher` alongside the pinned 218 live rows. Everything downstream of
the lookup is the real production code, unmodified: the real matcher, the real
7A member adapter, the real `generate_geometry`, the real `sections.py`
builders. Nothing in this module re-implements a production decision.

WHAT THIS MODULE DOES NOT DO
============================
  * It performs ZERO database writes. No INSERT/UPDATE/DELETE, no schema
    change, no migration. The live catalogue is not touched.
  * It adds ZERO catalogue rows. The four rows are injected into an in-memory
    matcher, never persisted.
  * It changes NO engineering property. Widths, thicknesses and weights go in
    exactly as the accepted evidence records them; nothing is derived, and a
    missing weight stays None — never 0, never calculated.
  * It does not make `FLAT` a CAD vocabulary word (`PROFILE_BUILDERS` is
    unchanged and does not contain it), and it does not generalise the mapping
    into a "closest supported family" search.
  * It does not alter SectionMatcher resolution, EXACT/SUFFIX_FALLBACK/NONE
    semantics, the E1-E5 conflict paths, the DXF path, or the pipeline.

FAIL-CLOSED
===========
Every proof here is anchored to the pinned live capture (sha256-checked) and to
the real production modules. If the capture is absent, the module SKIPS rather
than inventing reference data. If the capture's sha changes, it FAILS. If the
production boundary ever stops admitting `FLAT` — or starts admitting something
else — the tests here fail rather than quietly passing.

Mutation coverage (section 9) mutates ONLY module-level constants, result
objects and test doubles. The production database is never touched.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The pinned live capture Step H/J0 have used throughout — the genuine reference
# rows. The four identities are added ON TOP of these; the live table itself is
# never modified by this milestone.
PINNED_218_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
PINNED_218_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
PINNED_218_ROW_COUNT = 218
PINNED_218_DIGEST = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"

# The authoritative live family for a plate, and the CAD engine's own word for
# the same shape — two vocabularies, one explicit mapping between them.
SOURCE_FAMILY_FLAT = "FLAT"
CAD_FAMILY_FL = "FL"

# The Step J0-established enum, pinned here so this module cannot silently
# drift away from the schema facts it depends on.
SECTION_FAMILY_ENUM = ("UB", "UC", "PFC", "EA", "UA", "RHS", "SHS", "CHS", "FLAT", "OTHER")

ALL_COLUMNS = (
    "name", "family", "depth", "flange_width", "flange_thickness",
    "web_thickness", "weight_per_metre", "width", "thickness",
    "outside_diameter", "leg_size",
)


@dataclass(frozen=True)
class PreparedPlate:
    """
    One of the four accepted Step G/H identities, as a future Step J would write
    it. `live_name` is the live lowercase-x house spelling; `drawing_spellings`
    are what the engineer's drawing actually prints; `length_mm` values for
    180x20FL and 250x12FL are the genuine evidence lengths (340mm / 155mm
    printed alongside 9.6kg / 3.7kg in Step F), and the other two are arbitrary
    but explicit — never derived from anything.
    """
    live_name: str
    drawing_spellings: tuple[str, ...]
    width: float
    thickness: float
    weight_per_metre: float | None
    length_mm: float
    mass_check_kg: float | None = None


PREPARED = (
    PreparedPlate("130x12FL", ("130X12FL",), 130.0, 12.0, None, 400.0),
    PreparedPlate("180x20FL", ("180X20FL",), 180.0, 20.0, 28.3, 340.0, 9.6),
    PreparedPlate("250x12FL", ("250X12FL",), 250.0, 12.0, 23.6, 155.0, 3.7),
    PreparedPlate("90x10FL", ("90X10FL",), 90.0, 10.0, None, 250.0),
)


def _prepared_row(plate: PreparedPlate) -> dict:
    """
    The row exactly as the live FLAT convention shapes it: the five populated
    columns, everything else NULL. Not one value is invented here — width,
    thickness and weight come straight off the accepted evidence, and `None`
    stays `None`.
    """
    return {
        "name": plate.live_name,
        "family": SOURCE_FAMILY_FLAT,
        "depth": None,
        "flange_width": None,
        "flange_thickness": None,
        "web_thickness": None,
        "weight_per_metre": plate.weight_per_metre,
        "width": plate.width,
        "thickness": plate.thickness,
        "outside_diameter": None,
        "leg_size": None,
    }


# ===========================================================================
# The genuine production modules, under the established test-only env boundary
# ===========================================================================

TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "fake-test-signature-not-a-real-key"
)


@pytest.fixture(scope="module")
def production():
    """The REAL production modules, under the test-only configuration boundary.

    `app/config.py` reads `os.environ` at import time, so the environment is
    saved and restored immediately, AND every module this import adds to
    sys.modules is removed at teardown — only the app's own modules, because
    popping a C extension (cadquery/numpy/ezdxf) breaks it for the rest of the
    process.

    That teardown is not tidiness, it is what keeps the pre-existing
    `tests/test_smoke_imports.py` SUPABASE_URL failures HONEST: those tests fail
    because `app.config` cannot be imported from a bare environment, and a
    module that left `app.config` cached in sys.modules would silently turn
    those failures green. This module must not do that.
    """
    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        modules = SimpleNamespace(
            section_matcher=importlib.import_module("app.engineering_data.section_matcher"),
            interface=importlib.import_module("app.cad_engine.interface"),
            sections=importlib.import_module("app.cad_engine.sections"),
            adapter=importlib.import_module("app.cad_engine.real_member_adapter"),
            source_of_truth=importlib.import_module("app.engineering_data.source_of_truth"),
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    yield modules

    for name in set(sys.modules) - before:
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


class _InjectedResult:
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
    """Reference rows are injected; nothing here opens a connection."""

    def __init__(self, rows):
        self.rows = list(rows)

    def table(self, name):
        return _InjectedQuery(self)


def _matcher_with(rows, production):
    """A REAL SectionMatcher over injected rows — no network, no live client."""
    section_matcher = production.section_matcher
    saved = section_matcher.supabase
    section_matcher.supabase = _InjectedClient(rows)
    try:
        return section_matcher.SectionMatcher()
    finally:
        section_matcher.supabase = saved


class _CountingMatcher:
    """
    The real matcher, wrapped so every catalogue lookup is counted. Used to
    prove the admitted path performs exactly ONE lookup — there is no second
    lookup anywhere that could replace the identity or the properties.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls: list = []

    def resolve(self, name):
        self.calls.append(name)
        return self._inner.resolve(name)

    def match(self, name):
        self.calls.append(name)
        return self._inner.match(name)


def _live_rows() -> list:
    """The pinned live capture — this module reconciles against real rows or not at all."""
    if not PINNED_218_PATH.exists():
        pytest.skip(
            f"the captured live section rows are not present at {PINNED_218_PATH} — "
            "J1 verifies against the real reference rows or not at all"
        )
    raw = PINNED_218_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PINNED_218_SHA256, (
        "the live catalogue capture does not match the sha256 this module pins — "
        "refusing to verify against unverified reference data"
    )
    return json.loads(raw)


def _prepared_matcher(production):
    """The genuine matcher over the pinned live rows PLUS the four prepared rows."""
    return _matcher_with(_live_rows() + [_prepared_row(p) for p in PREPARED], production)


def _member_row(plate: PreparedPlate, *, name: str | None = None,
                mark: str = "M001", review_status: str | None = None) -> dict:
    """A real steel_members-shaped row, as app/pipeline.py builds one."""
    return {
        "mark": mark,
        "section_name": plate.live_name if name is None else name,
        "section_name_raw": plate.drawing_spellings[0],
        "length_mm": plate.length_mm,
        "grade": "300",
        "review_status": review_status,
        "source_page": 5,
        "source_drawing_id": "DWG-1",
    }


def _admit(production, plate: PreparedPlate, matcher=None, **kwargs):
    """
    The genuine chain: real steel_members row -> real 7A adapter -> real
    generate_geometry. Both production admission gates run, because both are on
    this path.
    """
    matcher = matcher if matcher is not None else _prepared_matcher(production)
    validated = production.adapter.real_member_to_validated_member(
        _member_row(plate, **kwargs), matcher
    )
    geometry = production.interface.generate_geometry(validated)
    return validated, geometry


def _bbox(geometry):
    box = geometry.solid.val().BoundingBox()
    return (round(box.xlen, 6), round(box.ylen, 6), round(box.zlen, 6))


def _snapshot(row: dict) -> dict:
    """Every catalogue column, recorded verbatim — the property-integrity witness."""
    return {column: row.get(column) for column in ALL_COLUMNS}


def _dict_values_for_key(relative_path: str, key: str) -> list[str]:
    """
    Every dict-literal VALUE bound to `key` in a source file, unparsed. Used to
    read the persisted `section_family` expressions out of the pipeline and DXF
    paths as source facts rather than as claims about them.
    """
    path = REPO_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for literal_key, value in zip(node.keys, node.values):
            if isinstance(literal_key, ast.Constant) and literal_key.value == key:
                found.append(ast.unparse(value))
    return found


# ===========================================================================
# 1. The mapping itself — one entry, explicit, immutable, fail-closed
# ===========================================================================


class TestTheMappingItself:
    def test_the_projection_holds_exactly_one_entry(self, production):
        projection = production.sections.CAD_FAMILY_PROJECTION
        assert dict(projection) == {SOURCE_FAMILY_FLAT: CAD_FAMILY_FL}
        assert len(projection) == 1

    def test_the_projection_is_immutable(self, production):
        projection = production.sections.CAD_FAMILY_PROJECTION
        assert isinstance(projection, MappingProxyType)
        with pytest.raises(TypeError):
            projection["UC"] = "FL"  # type: ignore[index]

    def test_a_source_family_projects_to_its_one_explicit_entry(self, production):
        assert production.sections.cad_family_for(SOURCE_FAMILY_FLAT) == CAD_FAMILY_FL

    def test_already_supported_families_are_identity_not_projection(self, production):
        """
        Every family the engine already supported must be admitted exactly as
        before — by identity, not by the projection table. This is what makes
        J1's change provably additive.
        """
        for family in ("UB", "PFC", "SHS", "RHS", "PL", "FL", "EA"):
            assert production.sections.cad_family_for(family) == family
            assert family not in production.sections.CAD_FAMILY_PROJECTION

    def test_flat_was_not_added_to_the_cad_vocabulary(self, production):
        """
        The admission is a projection, not a registration: `FLAT` must NOT
        become a PROFILE_BUILDERS key, or the CAD vocabulary and the container
        family list would both silently gain a word.
        """
        assert SOURCE_FAMILY_FLAT not in production.sections.PROFILE_BUILDERS
        assert sorted(production.sections.PROFILE_BUILDERS) == [
            "EA", "FL", "PFC", "PL", "RHS", "SHS", "UB"
        ]

    def test_the_flat_projection_lands_on_the_existing_plate_builder(self, production):
        sections = production.sections
        assert (sections.PROFILE_BUILDERS[sections.cad_family_for(SOURCE_FAMILY_FLAT)]
                is sections.build_plate_profile)
        assert sections.PROFILE_BUILDERS["FL"] is sections.PROFILE_BUILDERS["PL"]

    @pytest.mark.parametrize("value", [None, "", "flat", "Flat", "UC", "CHS", "UA",
                                       "OTHER", "PL ", " KNOWN", 7, 7.0, True,
                                       ("FLAT",), ["FLAT"], {"family": "FLAT"}])
    def test_anything_else_fails_closed(self, production, value):
        """
        Unknown, missing, misspelled, non-string and unhashable values all
        refuse. `PLAT`/`flat` are NOT folded into the mapping — this is an
        exact-value table, not a normalising match.
        """
        assert production.sections.cad_family_for(value) is None

    def test_the_mapping_takes_nothing_but_the_family(self, production):
        """
        The decision's ONLY input is the source family. There is no second
        parameter for a section name, a geometry hint, a builder list, or a
        caller-supplied CAD family to enter through.
        """
        signature = inspect.signature(production.sections.cad_family_for)
        assert list(signature.parameters) == ["source_family"]

    def test_it_agrees_with_the_evidenced_e1_table(self, production):
        """
        The one other place in the repository that states this mapping is the
        E1 audit table, which is never called from app/. Pinned together so the
        two evidenced statements cannot drift apart.
        """
        e1 = production.source_of_truth.LIVE_TO_LOCAL_FAMILY
        assert e1[SOURCE_FAMILY_FLAT] == production.sections.CAD_FAMILY_PROJECTION[SOURCE_FAMILY_FLAT]

    def test_the_pinned_schema_facts_still_hold(self, production):
        """J1 depends on J0's facts; if they are edited away, this fails."""
        assert SOURCE_FAMILY_FLAT in SECTION_FAMILY_ENUM
        assert CAD_FAMILY_FL not in SECTION_FAMILY_ENUM
        assert "PL" not in SECTION_FAMILY_ENUM


# ===========================================================================
# 2. THE CHAIN — cases A-E: the four identities reach the FLAM builder
# ===========================================================================


class TestTheGenuineChain:
    @pytest.mark.parametrize("plate", PREPARED, ids=[p.live_name for p in PREPARED])
    def test_flat_resolves_exact_and_generates_real_plate_geometry(self, production, plate):
        """Case A / C / D / E: EXACT -> source family FLAT -> CAD family FL -> built."""
        matcher = _prepared_matcher(production)

        match = matcher.resolve(plate.live_name)
        assert match.resolution == "EXACT"
        assert match.catalogue_row is not None
        assert match.catalogue_row["family"] == SOURCE_FAMILY_FLAT

        validated, geometry = _admit(production, plate, matcher)

        # the source family is authoritative and unchanged...
        assert validated.section_properties["family"] == SOURCE_FAMILY_FLAT
        assert geometry.section_family == SOURCE_FAMILY_FLAT
        # ...while the CAD family is a separate, derived projection
        assert (production.sections.cad_family_for(validated.section_properties["family"])
                == CAD_FAMILY_FL)
        # ...and the REAL geometry is a plate of exactly the recorded dimensions
        assert _bbox(geometry) == (plate.width, plate.thickness, plate.length_mm)
        assert geometry.solid.val().Volume() == pytest.approx(
            plate.width * plate.thickness * plate.length_mm
        )
        assert len(geometry.solid.val().Solids()) == 1

    @pytest.mark.parametrize("plate", PREPARED, ids=[p.live_name for p in PREPARED])
    def test_the_drawing_spelling_reaches_the_same_row_and_the_same_geometry(
        self, production, plate
    ):
        """Case B, for every identity: the printed uppercase token resolves to it."""
        matcher = _prepared_matcher(production)
        spelling = plate.drawing_spellings[0]

        via_drawing = matcher.resolve(spelling)
        via_live = matcher.resolve(plate.live_name)
        assert via_drawing.resolution == "EXACT"
        assert via_drawing.catalogue_row == via_live.catalogue_row
        assert via_drawing.catalogue_row["family"] == SOURCE_FAMILY_FLAT

        _, geometry = _admit(production, plate, matcher, name=spelling)
        assert _bbox(geometry) == (plate.width, plate.thickness, plate.length_mm)
        assert geometry.section_family == SOURCE_FAMILY_FLAT

    def test_the_four_identities_are_not_yet_live(self, production):
        """
        The honest premise: the four rows are prepared, not present. If this
        ever starts passing because they WERE written, the milestone that wrote
        them owns the change — this module must not silently assume Step J ran.
        """
        live_only = _matcher_with(_live_rows(), production)
        for plate in PREPARED:
            assert live_only.resolve(plate.live_name).resolution == "NONE"
            assert live_only.resolve(plate.live_name).catalogue_row is None
            assert live_only.resolve(plate.drawing_spellings[0]).resolution == "NONE"

    def test_exactly_one_catalogue_lookup_happens(self, production):
        """
        'There must be no second lookup that can replace identity or
        properties.' Counted on the real matcher: exactly one resolve() per
        member, for that member's own decided section — and the properties CAD
        received are that same lookup's row, value for value.
        """
        plate = PREPARED[2]  # 250x12FL
        counting = _CountingMatcher(_prepared_matcher(production))
        validated, geometry = _admit(production, plate, counting)

        assert counting.calls == [plate.live_name]
        assert validated.section_properties == counting.resolve(plate.live_name).catalogue_row
        assert _bbox(geometry) == (plate.width, plate.thickness, plate.length_mm)

    def test_a_missing_weight_does_not_stop_geometry(self, production):
        """
        130x12FL and 90x10FL carry no evidenced weight. That must not block
        geometry, and it must not become zero anywhere.
        """
        for plate in (p for p in PREPARED if p.weight_per_metre is None):
            validated, geometry = _admit(production, plate)
            assert validated.section_properties["weight_per_metre"] is None
            assert _bbox(geometry) == (plate.width, plate.thickness, plate.length_mm)

    @pytest.mark.parametrize("plate", [p for p in PREPARED if p.mass_check_kg is not None],
                             ids=lambda p: p.live_name)
    def test_the_evidenced_weights_are_the_recorded_ones(self, production, plate):
        """
        The accepted weights ride through unchanged, and they are independently
        corroborated by the masses Step F printed beside these members: the
        recorded weight over the genuine evidence length reproduces the printed
        mass to the one decimal place it was printed to. That is exactly why the
        derived near-misses (28.2353 / 23.8710) must never appear — they are the
        UNROUNDED density product, not the evidenced figure.
        """
        validated, _ = _admit(production, plate)
        recorded = validated.section_properties["weight_per_metre"]
        assert recorded == plate.weight_per_metre
        assert round(recorded * (plate.length_mm / 1000.0), 1) == plate.mass_check_kg
        for derived_near_miss in (28.2353, 23.8710):
            assert recorded != derived_near_miss


# ===========================================================================
# 3. REFUSALS — cases F, G, I, J and the review gate
# ===========================================================================


class TestRefusals:
    def test_case_f_250x90pfc_stays_none_and_still_refuses(self, production):
        matcher = _prepared_matcher(production)
        match = matcher.resolve("250X90PFC")
        assert match.resolution == "NONE"
        assert match.catalogue_row is None

        row = {"mark": "M009", "section_name": "250X90PFC", "section_name_raw": "250X90PFC",
               "length_mm": 1000.0, "grade": "300", "review_status": None}
        with pytest.raises(production.adapter.GeometryValidationError) as caught:
            production.adapter.real_member_to_validated_member(row, matcher)
        assert "recognised entry" in str(caught.value)
        assert SOURCE_FAMILY_FLAT not in str(caught.value)

    def test_case_g_310ub40_stays_a_suffix_fallback_and_still_refuses(self, production):
        matcher = _prepared_matcher(production)
        match = matcher.resolve("310UB40")
        assert match.resolution == "SUFFIX_FALLBACK"
        assert match.catalogue_row is not None

        row = {"mark": "M010", "section_name": "310UB40", "section_name_raw": "310UB40",
               "length_mm": 1000.0, "grade": "300", "review_status": None}
        with pytest.raises(production.adapter.GeometryValidationError) as caught:
            production.adapter.real_member_to_validated_member(row, matcher)
        assert "SUFFIX_FALLBACK" in str(caught.value)

    def test_case_i_pl_is_not_newly_mapped(self, production):
        """
        PL still builds — it always did — but its admission is IDENTITY, not the
        projection table. J1 must not have quietly made PL a mapped family.
        """
        sections = production.sections
        assert "PL" not in sections.CAD_FAMILY_PROJECTION
        assert sections.cad_family_for("PL") == "PL"

        row = {"name": "PL-TEST", "family": "PL", "width": 200.0, "thickness": 10.0}
        member = production.interface.ValidatedSteelMember(
            mark="M011", section="PL-TEST", length_mm=500.0, material="300",
            orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted", section_properties=row,
        )
        geometry = production.interface.generate_geometry(member)
        assert geometry.section_family == "PL"
        assert _bbox(geometry) == (200.0, 10.0, 500.0)

    def test_case_i_fl_is_not_newly_mapped_either(self, production):
        sections = production.sections
        assert "FL" not in sections.CAD_FAMILY_PROJECTION
        assert sections.cad_family_for("FL") == "FL"

    @pytest.mark.parametrize("family", ["UC", "CHS", "UA", "OTHER", "UB2", "FLAT ",
                                        "flat", "FLATX", ""])
    def test_case_j_any_other_family_fails_closed(self, production, family):
        """
        Case J, at both admission gates. Note `FLAT ` (trailing space) and
        `flat` refuse: the mapping is exact-valued, so nothing gets in by
        looking approximately right.
        """
        sections = production.sections
        assert sections.cad_family_for(family) is None

        row = {"name": "TEST-X", "family": family, "depth": 200.0, "width": 200.0}
        member = production.interface.ValidatedSteelMember(
            mark="M012", section="TEST-X", length_mm=500.0, material="300",
            orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted", section_properties=row,
        )
        with pytest.raises(production.interface.UnsupportedSectionFamilyError):
            production.interface.generate_geometry(member)

    def test_a_review_required_member_is_still_refused(self, production):
        plate = PREPARED[0]
        with pytest.raises(production.adapter.GeometryValidationError) as caught:
            _admit(production, plate, review_status="review_required")
        assert "review_required" in str(caught.value)

    def test_the_adapter_and_the_interface_make_the_same_admission_decision(self, production):
        """
        Two gates, one rule. Both read the same table, so no family can be
        admitted by one and refused by the other.
        """
        sections = production.sections
        for family in ("UB", "PFC", "SHS", "RHS", "PL", "FL", "EA", "FLAT",
                       "UC", "CHS", "UA", "OTHER", None, "flat"):
            admissible = sections.cad_family_for(family) is not None
            assert admissible == (family in sections.PROFILE_BUILDERS
                                  or family in sections.CAD_FAMILY_PROJECTION)


class TestTheReviewGateIsNotWeakened:
    """
    Section 10's real worry: a refused section must not be turned into a LATE
    geometry error, where a human sees a crash instead of a review flag. Both
    refused sections must stop at the admission/identity boundary, before any
    geometry is attempted — and they must stop with the ordinary
    GeometryValidationError, not the family error.
    """

    @pytest.mark.parametrize("token", ["250X90PFC", "310UB40"])
    def test_the_refusal_is_early_and_is_not_a_family_error(self, production, token):
        matcher = _prepared_matcher(production)
        row = {"mark": "M013", "section_name": token, "section_name_raw": token,
               "length_mm": 1000.0, "grade": "300", "review_status": None}

        with pytest.raises(production.adapter.GeometryValidationError) as caught:
            production.adapter.real_member_to_validated_member(row, matcher)

        # exact type: proves this is the ordinary refusal, not the CAD family
        # refusal wearing a different hat, and not a geometry error
        assert type(caught.value) is production.adapter.GeometryValidationError
        assert "no supported CAD profile builder" not in str(caught.value)

    def test_an_unmatched_section_never_reaches_a_builder(self, production):
        """
        Nothing about J1 gives an unmatched token a route to geometry: the
        adapter's single lookup returns nothing and stops there.
        """
        matcher = _prepared_matcher(production)
        for token in ("250X90PFC", "300X8FL", "200UB30", "90X10EA"):
            assert matcher.resolve(token).catalogue_row is None
            row = {"mark": "M014", "section_name": token, "section_name_raw": token,
                   "length_mm": 1000.0, "grade": "300", "review_status": None}
            with pytest.raises(production.adapter.GeometryValidationError):
                production.adapter.real_member_to_validated_member(row, matcher)


# ===========================================================================
# 4. SOURCE-FAMILY INTEGRITY (section 8)
# ===========================================================================


class TestSourceFamilyIntegrity:
    @pytest.mark.parametrize("plate", PREPARED, ids=[p.live_name for p in PREPARED])
    def test_the_catalogue_row_still_says_flat_after_admission(self, production, plate):
        """
        `catalogue_row["family"] == "FLAT"` must NOT have become `"FL"`.
        """
        matcher = _prepared_matcher(production)
        match = matcher.resolve(plate.live_name)
        row = match.catalogue_row
        before = _snapshot(row)

        _admit(production, plate, matcher)

        assert row["family"] == SOURCE_FAMILY_FLAT
        assert row["family"] != CAD_FAMILY_FL
        assert _snapshot(row) == before
        # and the matcher still answers the same thing afterwards
        assert matcher.resolve(plate.live_name).catalogue_row["family"] == SOURCE_FAMILY_FLAT

    def test_the_matched_row_object_is_never_mutated(self, production):
        """
        The exact dict handed to CAD is unchanged — checked on the very object
        the adapter received, not on a copy of it. (The matcher already hands
        out a detached copy of its index entry — `SectionMatch.catalogue_row` —
        so the row CAD sees is a copy of a copy, and the index itself is
        unreachable from here. That is a stronger guarantee than J1 needed.)"""
        plate = PREPARED[0]
        matcher = _prepared_matcher(production)
        holder = {}

        class _CapturingMatcher:
            def resolve(self, name):
                outcome = matcher.resolve(name)
                holder["row"] = outcome.catalogue_row
                return outcome

        _admit(production, plate, _CapturingMatcher())
        row = holder["row"]
        assert row["family"] == SOURCE_FAMILY_FLAT
        assert row == _prepared_row(plate)

    def test_cad_family_is_a_separate_projection_not_a_field(self, production):
        """
        The CAD family is DERIVED, never stored. Nothing carries a `cad_family`
        key — not the catalogue row, not the validated member, not the generated
        geometry — so there is nothing for a caller to set or for a downstream
        consumer to mistake for catalogue data.
        """
        sections, interface = production.sections, production.interface
        validated, geometry = _admit(production, PREPARED[2])

        assert "cad_family" not in validated.section_properties
        assert "family" in validated.section_properties
        assert sections.cad_family_for(validated.section_properties["family"]) == CAD_FAMILY_FL
        assert "cad_family" not in {f.name for f in dataclasses.fields(interface.ValidatedSteelMember)}
        assert "cad_family" not in {f.name for f in dataclasses.fields(interface.GeneratedMemberGeometry)}
        assert not hasattr(geometry, "cad_family")
        assert not hasattr(validated, "cad_family")

    def test_the_generated_geometry_reports_the_authoritative_family(self, production):
        """
        The strongest form of 'source authority preserved': the OUTPUT of CAD
        still names the catalogue family, so a report, a manifest or a drawing
        built from this geometry cannot accidentally claim the member is an
        `FL`-family section.
        """
        for plate in PREPARED:
            _, geometry = _admit(production, plate)
            assert geometry.section_family == SOURCE_FAMILY_FLAT
            assert geometry.section_name == plate.live_name
            assert geometry.section_family != CAD_FAMILY_FL

    def test_the_authoritative_row_is_what_decides_not_the_section_name(self, production):
        """
        Two rows with the same FLAT-looking name but different authoritative
        families must be admitted as what they ARE.
        """
        sections, interface = production.sections, production.interface

        ub_row = {"name": "130X12FL", "family": "UB", "depth": 130.0,
                  "flange_width": 130.0, "flange_thickness": 12.0, "web_thickness": 8.0}
        member = interface.ValidatedSteelMember(
            mark="M020", section="130X12FL", length_mm=400.0, material="300",
            orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted", section_properties=ub_row,
        )
        geometry = interface.generate_geometry(member)
        assert geometry.section_family == "UB"
        assert sections.cad_family_for(ub_row["family"]) == "UB"
        box = geometry.solid.val().BoundingBox()
        assert (round(box.xlen, 6), round(box.ylen, 6)) == (130.0, 130.0)  # an I, not a plate

        plate_row = _prepared_row(PREPARED[0])
        member = interface.ValidatedSteelMember(
            mark="M021", section="ugly-name-with-FL-in-it", length_mm=400.0, material="300",
            orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted", section_properties=dict(plate_row, name="ugly"),
        )
        geometry = interface.generate_geometry(member)
        assert geometry.section_family == SOURCE_FAMILY_FLAT  # the family decided, not the name

        # a name that merely ENDS in FL does not make a family admissible
        assert sections.cad_family_for("UC") is None
        assert sections.cad_family_for("91X10FL") is None


# ===========================================================================
# 5. PROPERTY INTEGRITY (section 9)
# ===========================================================================


class TestPropertyIntegrity:
    @pytest.mark.parametrize("plate", PREPARED, ids=[p.live_name for p in PREPARED])
    def test_no_column_changes_across_admission_and_generation(self, production, plate):
        matcher = _prepared_matcher(production)
        match = matcher.resolve(plate.live_name)
        before = _snapshot(match.catalogue_row)

        validated, geometry = _admit(production, plate, matcher)

        assert _snapshot(validated.section_properties) == before
        assert _snapshot(match.catalogue_row) == before
        assert _snapshot(_prepared_row(plate)) == before
        assert _bbox(geometry) == (plate.width, plate.thickness, plate.length_mm)

    @pytest.mark.parametrize("plate", PREPARED, ids=[p.live_name for p in PREPARED])
    def test_the_recorded_dimensions_are_exactly_the_evidenced_ones(self, production, plate):
        validated, _ = _admit(production, plate)
        properties = validated.section_properties
        assert properties["width"] == plate.width
        assert properties["thickness"] == plate.thickness
        assert properties["weight_per_metre"] == plate.weight_per_metre
        assert properties["name"] == plate.live_name
        assert properties["family"] == SOURCE_FAMILY_FLAT

    def test_a_missing_value_never_becomes_zero(self, production):
        """
        The one way a projection could quietly fabricate data. Both weightless
        identities must come out the far end still None.
        """
        for plate in (p for p in PREPARED if p.weight_per_metre is None):
            validated, _ = _admit(production, plate)
            assert validated.section_properties["weight_per_metre"] is None
            assert validated.section_properties["weight_per_metre"] != 0
        # and the rows whose shape is NULL outside the five populated columns
        validated, _ = _admit(production, PREPARED[2])
        for column in ("depth", "flange_width", "flange_thickness", "web_thickness",
                       "outside_diameter", "leg_size"):
            assert validated.section_properties[column] is None

    def test_no_weight_is_derived_from_the_geometry(self, production):
        """
        The plate builder never sees a weight, and no weight is manufactured
        from width x thickness x density. Stated as an explicit non-event.
        """
        plate = PREPARED[0]
        validated, _ = _admit(production, plate)
        density_guess = 7.85 * plate.width * plate.thickness / 1000.0
        assert density_guess == pytest.approx(12.246, abs=0.001)  # the figure NOT used
        assert validated.section_properties["weight_per_metre"] is None


# ===========================================================================
# 6. REFERENCE IDENTITY (section 13)
# ===========================================================================


class TestReferenceIdentity:
    def test_the_source_catalogue_is_unchanged(self, production):
        """
        J1 introduces no reference source. The matcher's identity is still the
        live Supabase table, unversioned, exactly as before.
        """
        matcher = _prepared_matcher(production)
        identity = matcher.reference_identity
        assert identity.source_kind == "LIVE_SUPABASE"
        assert identity.identity_status == "UNVERSIONED"
        assert matcher.catalogue_version is None

    def test_the_live_digest_is_still_the_pinned_one(self, production):
        """
        The LIVE table's digest must be untouched by J1. (The digest of the
        live rows PLUS the four prepared rows is a different number by
        construction — that is Step H's measured projection for the future
        write, not evidence about the live table.)
        """
        live = _live_rows()
        assert len(live) == PINNED_218_ROW_COUNT
        assert production.section_matcher.reference_data_digest(live) == PINNED_218_DIGEST

        with_prepared = live + [_prepared_row(p) for p in PREPARED]
        assert production.section_matcher.reference_data_digest(with_prepared) != PINNED_218_DIGEST
        # ...and that projected digest is NOT what any live matcher reports
        assert production.section_matcher.reference_data_digest(live) != \
            production.section_matcher.reference_data_digest(with_prepared)

    def test_admission_changes_no_reference_identity(self, production):
        matcher = _prepared_matcher(production)
        before = matcher.reference_identity, matcher.catalogue_version
        for plate in PREPARED:
            _admit(production, plate, matcher)
        assert (matcher.reference_identity, matcher.catalogue_version) == before

    def test_the_projection_is_not_a_reference_source(self, production):
        """
        `CAD_FAMILY_PROJECTION` is a derived internal representation. It has no
        source_kind, no digest, no version — it cannot be mistaken for a
        catalogue, and it is never consulted when the matcher reports identity.
        """
        projection = production.sections.CAD_FAMILY_PROJECTION
        assert isinstance(projection, MappingProxyType)
        for attribute in ("source_kind", "identity_status", "reference_data_digest",
                          "catalogue_version"):
            assert not hasattr(projection, attribute)
        source = inspect.getsource(production.section_matcher)
        assert "CAD_FAMILY_PROJECTION" not in source
        assert "cad_family_for" not in source


# ===========================================================================
# 7. THE AUTHORITY / SECURITY BOUNDARY (section 14)
# ===========================================================================


class TestTheActivationRoute:
    def test_the_only_activation_input_is_the_authoritative_family(self, production):
        """
        The mapping cannot be switched on by a name, a geometry, a fixture or a
        caller. Enumerated as the four routes section 14 names, each disproved
        against the real function.
        """
        sections = production.sections
        # (a) a section NAME ending in / containing "FL" is not an input at all
        assert sections.cad_family_for("UC") is None
        assert sections.cad_family_for("91X10FL") is None
        assert sections.cad_family_for("250X90PFC") is None
        # (b) geometry is not an input — the function takes one argument only
        assert list(inspect.signature(sections.cad_family_for).parameters) == ["source_family"]
        # (c) a caller cannot supply a CAD family: no such field exists anywhere
        for cls in (production.interface.ValidatedSteelMember,
                    production.interface.GeneratedMemberGeometry):
            assert "cad_family" not in {f.name for f in dataclasses.fields(cls)}
        # (d) family "FL" is admitted by IDENTITY, not by the projection
        assert "FL" not in sections.CAD_FAMILY_PROJECTION
        assert sections.cad_family_for("FL") == "FL"

    def test_projection_and_identity_are_distinguishable(self, production):
        """
        `FLAT` and `FL` are admitted, but they are NOT the same decision — one
        comes from the table, the other does not. Keeping that difference
        observable is what keeps the projection from becoming a substitution
        mechanism.
        """
        sections = production.sections
        assert sections.cad_family_for(SOURCE_FAMILY_FLAT) == CAD_FAMILY_FL
        assert sections.cad_family_for(CAD_FAMILY_FL) == CAD_FAMILY_FL
        assert SOURCE_FAMILY_FLAT in sections.CAD_FAMILY_PROJECTION
        assert CAD_FAMILY_FL not in sections.CAD_FAMILY_PROJECTION

    def test_a_substituted_row_is_refused_even_when_the_caller_claims_exact(self, production):
        """
        J1 adds an admission route; it must not have opened one. The 7A boundary
        refuses a lookup that hands back a DIFFERENT section, and that refusal is
        the only protection when the caller supplies a `match()`-only matcher —
        no resolution report exists there to check. Proven load-bearing both
        ways, because a substituted row carrying `FLAT` would otherwise be
        projected straight into plate geometry under the requested member's name.
        """
        plate = PREPARED[0]  # 130x12FL, the member this lookup is FOR
        decoy = {"name": "SOMETHING-ELSE", "family": SOURCE_FAMILY_FLAT,
                 "width": 999.0, "thickness": 99.0}

        class _SubstitutingMatcher:
            """Reports EXACT, then answers with a different section's row."""

            def resolve(self, name):
                return SimpleNamespace(resolution="EXACT", catalogue_row=dict(decoy))

            def match(self, name):
                return dict(decoy)

        class _MatchOnlyMatcher:
            """No resolution report at all — the identity check is the only gate."""

            def match(self, name):
                return dict(decoy)

        for matcher in (_SubstitutingMatcher(), _MatchOnlyMatcher()):
            with pytest.raises(production.adapter.GeometryValidationError) as caught:
                production.adapter.real_member_to_validated_member(
                    _member_row(plate), matcher
                )
            assert "DIFFERENT section" in str(caught.value)

        # ...and the decoy really was admissible-if-reached: same family, so the
        # family gate would have waved it through. The identity refusal is what
        # stops it, not the family gate.
        assert production.sections.cad_family_for(decoy["family"]) == CAD_FAMILY_FL

    def test_a_family_that_merely_looks_like_flat_is_not_admitted(self, production):
        for lookalike in ("FLAT ", " FLAT", "FLAT\n", "flat", "Flat", "fLaT",
                          "FLAT-12", "FLAT12", "F LAT", "FLA T"):
            assert production.sections.cad_family_for(lookalike) is None

    def test_no_second_family_mapping_exists_in_the_cad_package(self, production):
        """
        A repo-level guard: the production CAD package must contain no other
        family-substitution table hiding beside this one. Any literal dict
        mapping a source family onto `FL` inside app/cad_engine/ would show up
        here.
        """
        offenders = []
        for path in sorted((REPO_ROOT / "app" / "cad_engine").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                values = [v.value for v in node.values if isinstance(v, ast.Constant)]
                if CAD_FAMILY_FL in values and SOURCE_FAMILY_FLAT in keys:
                    offenders.append((path.name, keys))
                if CAD_FAMILY_FL in values and "PL" in keys:
                    offenders.append((path.name, keys))
        assert offenders == [("sections.py", [SOURCE_FAMILY_FLAT])], (
            "exactly one family-projection table must exist in app/cad_engine/, "
            f"and it must be the FLAT one — found: {offenders}"
        )

    def test_the_projection_table_is_bound_once_and_imported_by_nobody(self, production):
        """
        The table is DEFINED in exactly one module and BOUND in exactly one
        place; no other module imports it. Everything else reaches it through
        cad_family_for(), so there is one consumer and one decision. (Other
        modules may mention its NAME inside an error message — that is a
        message, not a binding, and the two are distinguished here by AST.)
        """
        binders = []
        for path in sorted((REPO_ROOT / "app").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if any(alias.name == "CAD_FAMILY_PROJECTION" for alias in node.names):
                        binders.append((relative, "import"))
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id == "CAD_FAMILY_PROJECTION":
                            binders.append((relative, "definition"))
        assert binders == [("app/cad_engine/sections.py", "definition")], binders

    def test_the_only_cad_family_reader_in_production_is_the_shared_function(self, production):
        """
        Both admission gates consult the SAME function. If a future change made
        either gate read the table directly, the two could disagree — this fails
        if that happens.
        """
        callers = []
        for path in sorted((REPO_ROOT / "app").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "cad_family_for(" in text:
                callers.append(path.relative_to(REPO_ROOT).as_posix())
        assert callers == [
            "app/cad_engine/interface.py",
            "app/cad_engine/real_member_adapter.py",
            "app/cad_engine/sections.py",  # its own definition
        ], callers


# ===========================================================================
# 8. DXF PATH (section 11) and PIPELINE PATH (section 12)
# ===========================================================================


class TestTheDxfPathNeedsNoChange:
    """
    Section 11 asks whether the DXF path needs a corresponding projection. It
    does not, and the reason is structural rather than a judgement call: the DXF
    module contains NO family admission at all. It never selects a builder, never
    calls generate_geometry, and never consults PROFILE_BUILDERS — it records the
    catalogue's authoritative family verbatim and stops. The one CAD admission
    boundary it will eventually meet is the shared one, which J1 already covers.
    """

    def test_the_dxf_module_imports_no_cad_engine_module(self):
        path = REPO_ROOT / "app" / "drawing_reading" / "dxf_parser.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        assert not [m for m in imports if m.startswith("app.cad_engine")], imports

    def test_the_dxf_module_never_selects_a_geometry_builder(self):
        text = (REPO_ROOT / "app" / "drawing_reading" / "dxf_parser.py").read_text(encoding="utf-8")
        for forbidden in ("generate_geometry", "PROFILE_BUILDERS", "cad_family_for",
                          "CAD_FAMILY_PROJECTION", "real_member_to_validated_member"):
            assert forbidden not in text

    def test_the_dxf_path_records_the_authoritative_family_verbatim(self):
        """
        The only family the DXF path writes is the catalogue row's own — so a
        live FLAT row persists as FLAT. Read out of the real source: exactly two
        places write `section_family`, and neither is a CAD family.
        """
        sources = _dict_values_for_key("app/drawing_reading/dxf_parser.py", "section_family")
        assert sorted(sources) == ["None", "drawn.enrichment_row['family']"], sources
        for source in sources:
            assert CAD_FAMILY_FL not in source
            assert "cad_family" not in source

    def test_the_dxf_section_regex_is_untouched(self, production):
        """
        Section 11: `get_section_regex()` must not change. Pinned as its exact
        compiled pattern, so any edit to it — however small — fails here. It
        lives on the matcher (the resolution authority), and the DXF path only
        ever consumes it.
        """
        matcher = _prepared_matcher(production)
        pattern = matcher.get_section_regex()
        assert pattern.pattern == (
            r"(?:\d{3}UB\d+\.?\d*)|(?:\d{3}UC\d+\.?\d*)|(?:\d{2,3}PFC)|"
            r"(?:\d+x\d+x\d+\.?\d*(?:RHS|SHS|EA|UA))|(?:\d+\.?\d*x\d+\.?\d*CHS)|"
            r"(?:\d+x\d+FL)"
        )
        assert pattern.flags & re.IGNORECASE
        # ...and the DXF path CONSUMES that pattern rather than defining one
        text = (REPO_ROOT / "app" / "drawing_reading" / "dxf_parser.py").read_text(encoding="utf-8")
        assert "matcher.get_section_regex()" in text
        assert "re.compile" not in text


class TestThePipelinePathNeedsNoChange:
    """
    Section 12: the PDF/vision pipeline must keep persisting the LIVE family.
    Verified against the real source, not asserted from memory.
    """

    def test_the_pipeline_persists_the_matched_family_verbatim(self):
        """
        Read out of the real source: the PDF/vision pipeline writes the MATCHED
        row's own family — the live value — into steel_members.section_family.
        A FLAT row therefore persists as FLAT, exactly as before J1.
        """
        sources = _dict_values_for_key("app/pipeline.py", "section_family")
        assert sources == ["matched['family'] if matched else None"], sources
        assert CAD_FAMILY_FL not in sources[0]

    def test_the_pipeline_has_no_cad_family_decision(self):
        text = (REPO_ROOT / "app" / "pipeline.py").read_text(encoding="utf-8")
        for forbidden in ("cad_family_for", "CAD_FAMILY_PROJECTION", "generate_geometry",
                          "PROFILE_BUILDERS", "cad_family"):
            assert forbidden not in text

    def test_no_cad_family_ever_reaches_persistence(self):
        """
        Scope guard for section 12: no persistence, validation or repository
        module may carry a CAD family. The derived projection stays inside the
        CAD package, so no schema change is implied or required.
        """
        offenders = []
        for path in sorted((REPO_ROOT / "app").rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative.startswith("app/cad_engine/"):
                continue
            text = path.read_text(encoding="utf-8")
            for token in ("cad_family", "CAD_FAMILY_PROJECTION"):
                if token in text:
                    offenders.append((relative, token))
        assert offenders == [], (
            "the CAD family projection must not leak outside app/cad_engine/ — "
            f"persistence and schema are unchanged by J1; found: {offenders}"
        )

    def test_no_new_database_family_is_introduced(self, production):
        """
        `FL` is still not a legal steel_sections family, and J1 does not make it
        one: the projection is a CAD-internal value, never a catalogue value.
        """
        assert CAD_FAMILY_FL not in SECTION_FAMILY_ENUM
        assert SOURCE_FAMILY_FLAT in SECTION_FAMILY_ENUM
        assert (production.sections.CAD_FAMILY_PROJECTION[SOURCE_FAMILY_FLAT]
                not in SECTION_FAMILY_ENUM)


# ===========================================================================
# 9. MUTATION / NON-VACUITY (section 15)
# ===========================================================================


class TestMutationNonVacuity:
    """
    Each test applies one controlled, dangerous change and measures that a J1
    invariant genuinely flips — having first measured that it holds unmutated.
    Mutations touch module-level constants, result objects and test doubles ONLY.
    The production database is never touched, and no production file is edited.

    This is what makes the rest of the module non-vacuous: if the detectors used
    here could not fail, they would be worthless everywhere else too.
    """

    def _projection_detector(self, sections) -> bool:
        """The invariant: FLAT is admitted as FL, and only FLAT is projected."""
        return (sections.cad_family_for(SOURCE_FAMILY_FLAT) == CAD_FAMILY_FL
                and set(sections.CAD_FAMILY_PROJECTION) == {SOURCE_FAMILY_FLAT})

    def test_m0_the_detector_holds_unmutated(self, production):
        assert self._projection_detector(production.sections) is True

    def test_m1_changing_the_mapping_to_pl_is_caught(self, production, monkeypatch):
        sections = production.sections
        monkeypatch.setattr(sections, "CAD_FAMILY_PROJECTION",
                            MappingProxyType({SOURCE_FAMILY_FLAT: "PL"}))
        assert sections.cad_family_for(SOURCE_FAMILY_FLAT) == "PL"
        assert self._projection_detector(sections) is False
        # ...and the geometry would then be built as the OTHER plate family
        assert (sections.PROFILE_BUILDERS[sections.cad_family_for(SOURCE_FAMILY_FLAT)]
                is sections.PROFILE_BUILDERS["PL"] is sections.build_plate_profile)

    def test_m2_removing_the_mapping_is_caught(self, production, monkeypatch):
        sections = production.sections
        monkeypatch.setattr(sections, "CAD_FAMILY_PROJECTION", MappingProxyType({}))
        assert sections.cad_family_for(SOURCE_FAMILY_FLAT) is None
        assert self._projection_detector(sections) is False

        # the four identities would be refused outright — no geometry at all
        row = {"mark": "M030", "section_name": PREPARED[0].live_name, "length_mm": 400.0,
               "grade": "300", "review_status": None}
        with pytest.raises(production.adapter.GeometryValidationError):
            production.adapter.real_member_to_validated_member(
                row, _prepared_matcher(production)
            )

    def test_m3_mapping_another_family_to_fl_is_caught(self, production, monkeypatch):
        sections = production.sections
        monkeypatch.setattr(sections, "CAD_FAMILY_PROJECTION",
                            MappingProxyType({SOURCE_FAMILY_FLAT: CAD_FAMILY_FL, "UC": CAD_FAMILY_FL}))
        assert sections.cad_family_for("UC") == CAD_FAMILY_FL  # the danger is real...
        assert self._projection_detector(sections) is False     # ...and detected
        assert "UC" in sections.CAD_FAMILY_PROJECTION

    def test_m4_changing_the_source_family_from_flat_to_fl_is_caught(self, production):
        """
        The authority detector: the geometry must report the family the
        catalogue gave. A row relabelled `FL` produces a geometry that reports
        `FL` — which the detector catches, because it compares against the
        authoritative `FLAT`.
        """
        plate = PREPARED[0]
        row = dict(_prepared_row(plate), family=CAD_FAMILY_FL)
        member = production.interface.ValidatedSteelMember(
            mark="M031", section=plate.live_name, length_mm=plate.length_mm, material="300",
            orientation=None, connection_refs=[], source_refs=[],
            validation_status="extracted", section_properties=row,
        )
        geometry = production.interface.generate_geometry(member)

        assert geometry.section_family == CAD_FAMILY_FL     # the mutation's effect
        assert geometry.section_family != SOURCE_FAMILY_FLAT  # caught by the J1 detector

    def test_m5_letting_the_310ub40_fallback_build_is_caught(self, production):
        """
        The fallback must stay refused. Demonstrated both ways: the genuine
        lookup refuses, and a matcher that WOULD admit the substituted row
        builds a different section — so the refusal is load-bearing, not vacuous.
        """
        matcher = _prepared_matcher(production)
        row = {"mark": "M032", "section_name": "310UB40", "length_mm": 1000.0,
               "grade": "300", "review_status": None}
        with pytest.raises(production.adapter.GeometryValidationError):
            production.adapter.real_member_to_validated_member(row, matcher)

        # the mutation: a matcher that answers 310UB40 with the substituted row
        # as if it were an exact hit for that name. It BUILDS — proving the
        # genuine refusal was the only thing standing in the way...
        substituted = matcher.resolve("310UB40").catalogue_row
        substituted_dimensions = {k: substituted.get(k) for k in
                                  ("depth", "flange_width", "flange_thickness", "web_thickness")}

        class _LyingMatcher:
            def resolve(self, name):
                return SimpleNamespace(
                    resolution="EXACT",
                    catalogue_row=dict(copy.deepcopy(substituted), name="310UB40"),
                )

        validated = production.adapter.real_member_to_validated_member(row, _LyingMatcher())
        geometry = production.interface.generate_geometry(validated)

        # ...and what it builds is the SUBSTITUTE's section, not the drawing's:
        assert {k: validated.section_properties.get(k) for k in substituted_dimensions} \
            == substituted_dimensions
        # but even then it is UB geometry — a fallback can never become plate
        # geometry, because no family mapping is involved in it at all
        assert validated.section_properties["family"] == "UB"
        assert geometry.section_family == "UB"
        assert geometry.section_family != SOURCE_FAMILY_FLAT
        assert geometry.section_family != CAD_FAMILY_FL

    def test_m6_letting_250x90pfc_bypass_none_is_caught(self, production):
        """
        `250X90PFC` is genuinely absent. Injecting a row that normalises to it
        would flip it to EXACT and let it build — so the NONE assertion is a real
        measurement of the reference data, not a tautology.
        """
        honest = _prepared_matcher(production)
        assert honest.resolve("250X90PFC").resolution == "NONE"

        bypass = _prepared_row(PREPARED[2])
        bypass["name"] = "250X90PFC"
        mutated = _matcher_with(_live_rows() + [bypass], production)
        assert mutated.resolve("250X90PFC").resolution == "EXACT"  # the danger is real
        assert honest.resolve("250X90PFC").resolution == "NONE"    # and the detector catches it

    def test_m7_altering_a_property_during_projection_is_caught(self, production):
        """
        The property-integrity detector is a verbatim snapshot comparison. Any
        changed, zeroed or derived value shows up immediately.
        """
        plate = PREPARED[0]
        matcher = _prepared_matcher(production)
        original = _snapshot(matcher.resolve(plate.live_name).catalogue_row)

        for field, mutated_value in (("width", 0.0), ("thickness", None),
                                     ("weight_per_metre", 0.0), ("family", CAD_FAMILY_FL),
                                     ("name", "130X12FL")):
            assert _snapshot(dict(matcher.resolve(plate.live_name).catalogue_row,
                                  **{field: mutated_value})) != original, field

        validated, _ = _admit(production, plate, matcher)
        assert _snapshot(validated.section_properties) == original  # unmutated in reality

    def test_m8_replacing_the_authoritative_family_output_is_caught(self, production):
        """
        If CAD ever reported its own vocabulary as the member's family, the
        source-family detector would fire. Simulated on the real result object.
        """
        plate = PREPARED[2]
        validated, geometry = _admit(production, plate)
        assert geometry.section_family == SOURCE_FAMILY_FLAT  # unmutated

        replaced = dataclasses.replace(geometry, section_family=CAD_FAMILY_FL)
        assert replaced.section_family != validated.section_properties["family"]
        assert replaced.section_family != SOURCE_FAMILY_FLAT

    def test_the_mutations_left_the_production_modules_clean(self, production):
        """
        Every monkeypatched constant is restored by pytest, and no mutation
        touched a file. Confirmed by re-reading the table through the real
        function after the mutation tests have run.
        """
        sections = production.sections
        assert dict(sections.CAD_FAMILY_PROJECTION) == {SOURCE_FAMILY_FLAT: CAD_FAMILY_FL}
        assert SOURCE_FAMILY_FLAT not in sections.PROFILE_BUILDERS
        assert sections.cad_family_for("UC") is None


# ===========================================================================
# 10. The scope guard — nothing outside the CAD package moved
# ===========================================================================


class TestScopeGuard:
    def test_the_catalogue_row_shape_this_module_prepares_is_the_live_one(self, production):
        """
        The prepared row must match the shape the LIVE plate rows actually have,
        or this module would be proving admission for a row production will
        never write. Compared against the genuine 218-row capture.
        """
        live_flat = [r for r in _live_rows() if r["family"] == SOURCE_FAMILY_FLAT]
        assert len(live_flat) == 23
        live_keys = {k for row in live_flat for k, v in row.items() if v is not None}
        prepared = _prepared_row(PREPARED[1])
        prepared_keys = {k for k, v in prepared.items() if v is not None}
        assert prepared_keys == live_keys
        assert set(prepared) == set(live_flat[0])
        # the live house spelling: lowercase "x", never uppercase
        assert all("x" in r["name"] and "X" not in r["name"] for r in live_flat)
        assert "x" in PREPARED[0].live_name and "X" not in PREPARED[0].live_name

    def test_this_module_can_perform_no_write(self):
        """
        The J1 proof must be incapable of touching production data. This module
        opens no connection, issues no SQL, and performs no filesystem write:
        there is no DB client, no write verb against steel_sections, and no file
        write anywhere in it.
        """
        own = Path(__file__).read_text(encoding="utf-8")
        marker = "def test_this_module_can_perform_no_write"
        scanned = own[: own.index(marker)]  # everything except this guard's own text
        for forbidden in (".insert(", ".update(", ".upsert(", ".delete(", ".execute(",
                          "requests.", "urllib", "subprocess", "write_text", "write_bytes",
                          "os.system", "shutil."):
            assert forbidden not in scanned, forbidden
        # the only filesystem effects are reads of the pinned capture and of
        # production source, and the only module mutation is an in-process
        # monkeypatch that pytest restores
        assert ".read_bytes()" in scanned and ".read_text(" in scanned
        assert "monkeypatch.setattr(sections" in scanned
        assert own.index(marker) > len(own) // 2  # the guard covers the whole module
