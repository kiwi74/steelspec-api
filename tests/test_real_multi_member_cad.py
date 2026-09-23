"""
Milestone 7G — multiple independently extracted real-member records,
from the same project, through the existing 7A adapter and existing
member-level CAD contract, with no identity/geometry/length
cross-contamination:

    real member row A -> 7A adapter -> ValidatedSteelMember A -> CAD A
    real member row B -> 7A adapter -> ValidatedSteelMember B -> CAD B

This is about proving the EXISTING single-member contract
(real_member_to_validated_member() + generate_geometry(member)) can
already be called repeatedly, independently, for more than one member
from the same drawing — not about building a multi-member assembly or
automatic connection resolution. No connections are used anywhere in
this file; every geometry generated here has connection_count == 0,
connections == [], hardware == [] by construction (generate_geometry()
is always called with no `connections` argument).

FIXTURE PROVENANCE:

Member A reuses the established primary real-member fixture unchanged
from 7B/7C/7E/7F (tests/test_real_member_adapter.py's
make_primary_7b_row()): mark REAL-UB-CAD-001, section 310UB40, length
4000mm, review_status approved.

Member B's mark and section come directly from a REAL row in the
Arkles Strand extraction (tests/test_arkles_strand.py's
ARKLES_STRAND_RAW): {"mark": "L2", "section": "250PFC",
"source_page": 14, "confidence": 85}. L2 is deliberately chosen over
marks like BF1/P2/P3/P4 because it has exactly ONE row in the real
54-row extraction — a clean, unambiguous real section match, not one
that silently picks a winner among several conflicting real candidates
for the same mark (those marks are flagged CONFLICTING_MEMBER_DEFINITION
by app/validation/rules.py and are not a fair "clean" fixture to use
here).

Member B's length_mm is NOT part of the real extraction: every one of
the 54 real Arkles rows has length_mm = None (re-confirmed directly
against tests/test_arkles_strand.py below). The value used here is
explicitly a HUMAN-REVIEWED / SUPPLEMENTED figure standing in for a
length a reviewer would confirm from the actual drawing — it is never
presented as extracted data, and the test-data convention established
in 7D/7E/7F (an explicitly-labelled reviewed record, not an untouched
extraction) is reused, not reinvented, here.
"""
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import generate_geometry
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from tests.test_arkles_strand import ARKLES_STRAND_RAW
from tests.test_real_member_adapter import (
    FakeSectionMatcher,
    FULL_PFC_SECTION,
    FULL_UB_SECTION,
    UNSUPPORTED_FAMILY_SECTION,
    make_primary_7b_row,
)

# Confirms the real Arkles row this fixture's mark/section come from,
# and that it really is the sole occurrence of "L2" in the extraction
# (unlike BF1/P2/P3/P4, which carry multiple conflicting real
# candidates for the same mark).
_L2_REAL_ROWS = [r for r in ARKLES_STRAND_RAW if r["mark"] == "L2"]
assert _L2_REAL_ROWS == [{"mark": "L2", "section": "250PFC", "source_page": 14, "confidence": 85}]


def make_multi_member_matcher() -> FakeSectionMatcher:
    return FakeSectionMatcher({"310UB40": FULL_UB_SECTION, "250PFC": FULL_PFC_SECTION})


def make_member_a_row(**overrides) -> dict:
    """Member A — the established primary real member fixture, unchanged."""
    row = make_primary_7b_row()
    row.update(overrides)
    return row


def make_member_b_row(**overrides) -> dict:
    """Member B — real mark/section from Arkles Strand row L2 (page 14);
    length_mm is HUMAN-REVIEWED/SUPPLEMENTED — see module docstring."""
    row = {
        "mark": "L2",
        "section_name": "250PFC",
        "section_name_raw": "250PFC",
        "section_family": "PFC",
        "length_mm": 3000,  # HUMAN-REVIEWED/SUPPLEMENTED — not in the real extraction
        "grade": "300PLUS",
        "quantity": 1,
        "review_status": "approved",
        "source_page": 14,
        "source_drawing_id": "ARKLES-STRAND",
    }
    row.update(overrides)
    return row


def _build(row: dict, matcher) -> tuple:
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    return validated, geometry


def _measure(geometry) -> dict:
    bbox = geometry.solid.val().BoundingBox()
    return {
        "mark": geometry.mark,
        "section_name": geometry.section_name,
        "section_family": geometry.section_family,
        "length_mm": geometry.length_mm,
        "xlen": bbox.xlen,
        "ylen": bbox.ylen,
        "zlen": bbox.zlen,
        "volume": geometry.solid.val().Volume(),
        "solid_count": len(geometry.solid.solids().vals()),
    }


# === Step 4-6: each member passes through 7A alone, no hand-built
# ValidatedSteelMember, geometry non-null, one solid, no connections ===
def test_member_a_identity_and_geometry_no_connections():
    matcher = make_multi_member_matcher()
    validated, geometry = _build(make_member_a_row(), matcher)

    assert validated.mark == "REAL-UB-CAD-001"
    assert geometry.mark == "REAL-UB-CAD-001"
    assert geometry.section_name == "310UB40"
    assert geometry.section_family == "UB"
    assert geometry.length_mm == 4000.0
    assert geometry.solid is not None
    assert len(geometry.solid.solids().vals()) == 1
    assert geometry.connection_count == 0
    assert geometry.connections == []
    assert geometry.hardware == []


def test_member_b_identity_and_geometry_no_connections():
    matcher = make_multi_member_matcher()
    validated, geometry = _build(make_member_b_row(), matcher)

    assert validated.mark == "L2"
    assert geometry.mark == "L2"
    assert geometry.section_name == "250PFC"
    assert geometry.section_family == "PFC"
    assert geometry.length_mm == 3000.0
    assert geometry.solid is not None
    assert len(geometry.solid.solids().vals()) == 1
    assert geometry.connection_count == 0
    assert geometry.connections == []
    assert geometry.hardware == []


# === Step 6: identity isolation between members ===
def test_identity_isolation_between_members():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)

    assert geometry_a.mark != geometry_b.mark
    assert geometry_a.section_name != geometry_b.section_name
    assert geometry_a.section_family != geometry_b.section_family
    assert geometry_a.length_mm != geometry_b.length_mm
    assert geometry_a.mark not in (geometry_b.mark, geometry_b.section_name)
    assert geometry_b.mark not in (geometry_a.mark, geometry_a.section_name)
    assert geometry_a.solid is not geometry_b.solid  # independent objects, never shared


# === Step 7: geometry isolation, measured independently, never a combined bbox ===
def test_geometry_isolation_measured_independently_per_member():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    _, geometry_b = _build(make_member_b_row(), matcher)

    bbox_a = geometry_a.solid.val().BoundingBox()
    bbox_b = geometry_b.solid.val().BoundingBox()

    # Member A: 310UB40 — flange_width=165, depth=310, length=4000
    assert abs(bbox_a.xlen - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs(bbox_a.ylen - FULL_UB_SECTION["depth"]) < 1e-6
    assert abs(bbox_a.zlen - 4000.0) < 1e-6

    # Member B: 250PFC — flange_width=90, depth=250, length=3000 (reviewed)
    assert abs(bbox_b.xlen - FULL_PFC_SECTION["flange_width"]) < 1e-6
    assert abs(bbox_b.ylen - FULL_PFC_SECTION["depth"]) < 1e-6
    assert abs(bbox_b.zlen - 3000.0) < 1e-6

    assert bbox_a.zlen != bbox_b.zlen
    assert abs(bbox_a.xlen - bbox_b.xlen) > 1.0  # genuinely different cross-sections


# === Step 8: section authority — changing/building Member B never alters Member A ===
def test_section_authority_changing_member_b_does_not_alter_member_a():
    matcher = make_multi_member_matcher()
    _, geometry_a_before = _build(make_member_a_row(), matcher)
    bbox_before = geometry_a_before.solid.val().BoundingBox()

    _, geometry_b = _build(make_member_b_row(), matcher)
    assert geometry_b.section_family == "PFC"  # a genuinely different family from A's UB

    # Member A, rebuilt from its own untouched source row after B exists.
    _, geometry_a_after = _build(make_member_a_row(), matcher)
    bbox_after = geometry_a_after.solid.val().BoundingBox()

    assert abs(bbox_after.xlen - bbox_before.xlen) < 1e-6
    assert abs(bbox_after.ylen - bbox_before.ylen) < 1e-6
    assert abs(bbox_after.zlen - bbox_before.zlen) < 1e-6
    assert geometry_a_after.section_family == "UB"


# === Step 9: length isolation — changing Member B's length only ===
def test_length_isolation_changing_member_b_length_does_not_affect_member_a():
    matcher = make_multi_member_matcher()
    _, geometry_a = _build(make_member_a_row(), matcher)
    bbox_a = geometry_a.solid.val().BoundingBox()

    _, geometry_b_3000 = _build(make_member_b_row(length_mm=3000), matcher)
    _, geometry_b_2500 = _build(make_member_b_row(length_mm=2500), matcher)

    assert geometry_b_3000.length_mm == 3000.0
    assert geometry_b_2500.length_mm == 2500.0
    bbox_b_3000 = geometry_b_3000.solid.val().BoundingBox()
    bbox_b_2500 = geometry_b_2500.solid.val().BoundingBox()
    assert abs(bbox_b_3000.zlen - 3000.0) < 1e-6
    assert abs(bbox_b_2500.zlen - 2500.0) < 1e-6

    # Member B's own section is unaffected by its own length change.
    assert abs(bbox_b_3000.xlen - bbox_b_2500.xlen) < 1e-6
    assert abs(bbox_b_3000.ylen - bbox_b_2500.ylen) < 1e-6

    # Member A, rebuilt after both B variants, is byte/measurement-equivalent.
    _, geometry_a_after = _build(make_member_a_row(), matcher)
    bbox_a_after = geometry_a_after.solid.val().BoundingBox()
    assert abs(bbox_a_after.xlen - bbox_a.xlen) < 1e-6
    assert abs(bbox_a_after.ylen - bbox_a.ylen) < 1e-6
    assert abs(bbox_a_after.zlen - bbox_a.zlen) < 1e-6


# === Step 10: source independence — raw records discardable after 7A ===
def test_source_independence_raw_records_discardable_after_7a():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row()
    row_b = make_member_b_row()

    validated_a = real_member_to_validated_member(row_a, matcher)
    validated_b = real_member_to_validated_member(row_b, matcher)

    row_a.clear()
    row_b.clear()

    geometry_a = generate_geometry(validated_a)
    geometry_b = generate_geometry(validated_b)

    assert geometry_a.mark == "REAL-UB-CAD-001"
    assert geometry_a.length_mm == 4000.0
    assert geometry_b.mark == "L2"
    assert geometry_b.length_mm == 3000.0


# === Step 11: adversarial cross-member contamination test ===
def test_cross_member_contamination_adversarial():
    matcher = make_multi_member_matcher()

    row_a = make_member_a_row()
    _, geometry_a_first = _build(row_a, matcher)
    measurement_first = _measure(geometry_a_first)

    # Repeatedly mutate Member B's OWN source data and regenerate B
    # through the full 7A -> CAD chain several times, deliberately
    # adversarial to any shared mutable state in the adapter/CAD layer.
    _build(make_member_b_row(), matcher)
    _build(make_member_b_row(section_name="310UB40", section_family="UB"), matcher)
    _build(make_member_b_row(length_mm=999), matcher)

    # Member A, regenerated from its own original, still-untouched row.
    _, geometry_a_second = _build(row_a, matcher)
    measurement_second = _measure(geometry_a_second)

    assert measurement_second == measurement_first


# === Step 12: duplicate mark handling — document existing behaviour, not a new rule ===
def test_duplicate_mark_across_rows_currently_passes_through_unvalidated():
    """
    This documents existing behaviour rather than adding a new one:
    real_member_to_validated_member() and generate_geometry() each
    process one row/member at a time, with no notion of a set of
    "already seen" marks across calls. Two source rows sharing the
    same mark but describing genuinely different members (different
    section, different length) each convert and generate CAD
    successfully — nothing in this layer rejects the duplicate.
    Enforcing member-identity uniqueness, if ever required, is a
    pipeline/database-level concern (e.g. a uniqueness constraint or a
    cross-row consolidation pass in app/validation/rules.py), not
    something this milestone adds here.
    """
    matcher = make_multi_member_matcher()
    row_x = make_member_a_row(mark="DUP-MARK", section_name="310UB40", length_mm=4000)
    row_y = make_member_b_row(mark="DUP-MARK", section_name="250PFC", length_mm=3000)

    _, geometry_x = _build(row_x, matcher)
    _, geometry_y = _build(row_y, matcher)

    assert geometry_x.mark == geometry_y.mark == "DUP-MARK"
    assert geometry_x.section_family == "UB"
    assert geometry_y.section_family == "PFC"
    assert geometry_x.length_mm == 4000.0
    assert geometry_y.length_mm == 3000.0


# === Step 13: one invalid member must never affect a valid sibling ===
_INVALID_MEMBER_B_CASES = [
    ("missing_length", {"length_mm": None}, "missing or not numeric"),
    ("zero_length", {"length_mm": 0}, "positive number"),
    ("negative_length", {"length_mm": -100}, "positive number"),
    ("boolean_length", {"length_mm": True}, "missing or not numeric"),
    ("unsupported_section", {"section_name": "200UC46.2"}, "no supported CAD profile builder"),
    ("missing_section", {"section_name": None}, "no matched section name"),
    ("review_required", {"review_status": "review_required"}, "review_required"),
]


@pytest.mark.parametrize(
    "case_id,overrides,match", _INVALID_MEMBER_B_CASES, ids=[c[0] for c in _INVALID_MEMBER_B_CASES],
)
def test_invalid_member_rejected_without_affecting_sibling_valid_member(case_id, overrides, match):
    matcher = make_multi_member_matcher()
    if case_id == "unsupported_section":
        matcher = FakeSectionMatcher({
            "310UB40": FULL_UB_SECTION, "250PFC": FULL_PFC_SECTION,
            "200UC46.2": UNSUPPORTED_FAMILY_SECTION,
        })

    invalid_row = make_member_b_row(**overrides)
    with pytest.raises(GeometryValidationError, match=match):
        real_member_to_validated_member(invalid_row, matcher)

    # Member A, built alongside the invalid member in the same test, is unaffected.
    _, geometry_a = _build(make_member_a_row(), matcher)
    assert geometry_a.mark == "REAL-UB-CAD-001"
    assert geometry_a.section_family == "UB"
    assert geometry_a.length_mm == 4000.0
