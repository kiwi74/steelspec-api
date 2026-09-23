"""
Milestone 7A — tests for the boundary between real extracted/persisted
member data (the actual `steel_members` row shape, inspected directly
from app/pipeline.py's _run_pipeline() before it calls
repo.insert_members() — not guessed) and the CAD engine's
ValidatedSteelMember contract.

No app/pipeline.py changes were needed for this milestone (see the
final report), so there is no tests/test_pipeline.py to run — this
file is the adapter's own focused test suite instead.

FakeSectionMatcher below mirrors the exact shape
tests/test_arkles_strand.py's RealisticFakeMatcher already
established for this codebase: a plain name->row dict and a `.match()`
method, duck-type compatible with the real
app.engineering_data.section_matcher.SectionMatcher without touching
Supabase. Several fixtures here deliberately reuse that same
observation — including the real gap it surfaced (a matched section
row that has `family` and `weight_per_metre` but none of the actual
CAD geometry columns) — since that is a genuine, already-proven
failure mode in this project's real reference data, not a hypothetical
one invented for this test file.

Milestone 7C extends the same file with the next boundary: real member
row -> 7A adapter -> ValidatedSteelMember -> generate_geometry() ->
GeneratedMemberGeometry -> the EXISTING PDF generator
(app.drawing_generator.interface.generate_fabrication_drawing_pdf).
That function's own signature already only accepts a
GeneratedMemberGeometry plus presentation metadata (quantity, status,
material, revision, date) — there is no parameter through which a raw
member row or ValidatedSteelMember could reach it — so the
architecture boundary this milestone requires is enforced by the
existing API shape itself, not by anything new added here.
"""
import datetime

import pypdf
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedSteelMember, generate_geometry
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.drawing_generator.interface import generate_fabrication_drawing_pdf


class FakeSectionMatcher:
    def __init__(self, sections: dict):
        self._sections = sections

    def match(self, raw_name: str):
        return self._sections.get(raw_name.strip().upper())


# A fully CAD-capable UB row — every field app.cad_engine.sections.build_ub_profile()
# requires is present. Same synthetic values already established throughout
# tests/test_cad_engine.py's SECTION_310UB40 (explicitly not verified production
# dimensions — see that file's own note).
FULL_UB_SECTION = {
    "name": "310UB40", "family": "UB",
    "depth": 310.0, "flange_width": 165.0,
    "flange_thickness": 12.0, "web_thickness": 8.0,
    "weight_per_metre": 40.4,
}

# A matched section with a supported family but missing the actual CAD
# geometry columns — this is not a hypothetical edge case: it is exactly
# the shape tests/test_arkles_strand.py's RealisticFakeMatcher already
# established (family + weight_per_metre only, no depth/flange_width/etc),
# mirroring the real current steel_sections reference table.
INCOMPLETE_PFC_SECTION = {"name": "250PFC", "family": "PFC", "weight_per_metre": 35.5}

# A matched section whose family has no CAD profile builder at all (UC is
# real-world common but genuinely unsupported by app.cad_engine.sections
# today — see test_arkles_strand.py's P3/200UC46 rows).
UNSUPPORTED_FAMILY_SECTION = {"name": "200UC46.2", "family": "UC", "weight_per_metre": 46.2}


def make_matcher() -> FakeSectionMatcher:
    return FakeSectionMatcher({
        "310UB40": FULL_UB_SECTION,
        "250PFC": INCOMPLETE_PFC_SECTION,
        "200UC46.2": UNSUPPORTED_FAMILY_SECTION,
    })


def make_real_member_row(**overrides) -> dict:
    """
    The real `steel_members` row shape, exactly as app/pipeline.py's
    _run_pipeline() builds `members_to_insert` entries before calling
    repo.insert_members() — see that function for the source of this
    shape.
    """
    row = {
        "mark": "REAL-UB-001",
        "section_name": "310UB40",
        "section_name_raw": "310UB40",
        "section_family": "UB",
        "length_mm": 4000.0,
        "grade": "300PLUS",
        "quantity": 1,
        "weight_per_metre": 40.4,
        "confidence": "high",
        "confidence_score": 97,
        "source_page": 12,
        "source_drawing_id": "drawing-abc",
        "extraction_method": "vision_claude",
        "review_status": "extracted",
        "detail_reference": None,
        "notes": None,
    }
    row.update(overrides)
    return row


# === TEST A: valid real member -> ValidatedSteelMember ===
def test_valid_real_member_becomes_validated_steel_member():
    row = make_real_member_row()
    result = real_member_to_validated_member(row, make_matcher())

    assert isinstance(result, ValidatedSteelMember)
    assert result.mark == "REAL-UB-001"
    assert result.section == "310UB40"
    assert result.length_mm == 4000.0
    assert result.section_properties == FULL_UB_SECTION
    assert result.validation_status == "extracted"
    assert result.connection_refs == []


# === TEST B: missing length ===
def test_missing_length_is_rejected():
    row = make_real_member_row(mark="REAL-UB-002", length_mm=None)
    with pytest.raises(GeometryValidationError, match="length_mm"):
        real_member_to_validated_member(row, make_matcher())


# === TEST C: zero length ===
def test_zero_length_is_rejected():
    row = make_real_member_row(length_mm=0)
    with pytest.raises(GeometryValidationError, match="positive"):
        real_member_to_validated_member(row, make_matcher())


# === TEST D: negative length ===
def test_negative_length_is_rejected():
    row = make_real_member_row(length_mm=-500.0)
    with pytest.raises(GeometryValidationError, match="positive"):
        real_member_to_validated_member(row, make_matcher())


# === Non-numeric length must not be silently coerced ===
def test_non_numeric_length_is_rejected():
    row = make_real_member_row(length_mm="four metres")
    with pytest.raises(GeometryValidationError, match="length_mm"):
        real_member_to_validated_member(row, make_matcher())


# === Boolean length must not be silently treated as 1/0 ===
def test_boolean_length_is_rejected():
    row = make_real_member_row(length_mm=True)
    with pytest.raises(GeometryValidationError, match="length_mm"):
        real_member_to_validated_member(row, make_matcher())


# === TEST E: missing section ===
def test_missing_section_name_is_rejected():
    row = make_real_member_row(section_name=None)
    with pytest.raises(GeometryValidationError, match="section name"):
        real_member_to_validated_member(row, make_matcher())


# === TEST F: unsupported/unmatched section ===
def test_unmatched_section_is_rejected_not_substituted():
    row = make_real_member_row(section_name="235UB37")  # not in make_matcher()'s table
    with pytest.raises(GeometryValidationError, match="not a recognised entry"):
        real_member_to_validated_member(row, make_matcher())


def test_matched_but_unsupported_family_is_rejected():
    row = make_real_member_row(section_name="200UC46.2")  # matches, but family UC has no CAD builder
    with pytest.raises(GeometryValidationError, match="no supported CAD profile builder"):
        real_member_to_validated_member(row, make_matcher())


# === TEST G: matched section, supported family, but missing CAD geometry fields ===
def test_matched_section_missing_required_geometry_is_rejected_downstream():
    # The adapter itself succeeds — mark/length/section-match are all fine.
    row = make_real_member_row(section_name="250PFC")
    result = real_member_to_validated_member(row, make_matcher())
    assert result.section_properties == INCOMPLETE_PFC_SECTION

    # generate_geometry() — the existing, authoritative check — is what actually
    # rejects it, per this module's explicit design (see its module docstring):
    # missing-field checking is not duplicated in the adapter.
    with pytest.raises(GeometryValidationError, match="missing required PFC geometry field"):
        generate_geometry(result)


# === review_required rows are rejected regardless of otherwise-valid length/section ===
def test_review_required_member_is_rejected():
    row = make_real_member_row(review_status="review_required")
    with pytest.raises(GeometryValidationError, match="review_required"):
        real_member_to_validated_member(row, make_matcher())


# === Untrustworthy mark (missing, blank, or the pipeline's own "?" sentinel) ===
def test_missing_mark_is_rejected():
    row = make_real_member_row(mark=None)
    with pytest.raises(GeometryValidationError, match="mark"):
        real_member_to_validated_member(row, make_matcher())


def test_unrecorded_mark_sentinel_is_rejected():
    row = make_real_member_row(mark="?")  # app/pipeline.py's own "mark not extracted" sentinel
    with pytest.raises(GeometryValidationError, match="mark"):
        real_member_to_validated_member(row, make_matcher())


# === TEST H: real member data reaches the existing CAD engine ===
def test_valid_real_member_reaches_existing_cad_engine(tmp_path):
    row = make_real_member_row(mark="REAL-UB-H", length_mm=4000.0)
    validated = real_member_to_validated_member(row, make_matcher())

    geometry = generate_geometry(validated)

    assert geometry.mark == "REAL-UB-H"
    assert geometry.section_family == "UB"
    assert abs(geometry.length_mm - 4000.0) < 1e-6

    bbox = geometry.solid.val().BoundingBox()
    assert abs(bbox.zlen - 4000.0) < 1e-6         # member length, not invented
    assert abs(bbox.xlen - 165.0) < 1e-6           # actual flange_width from the matched section
    assert abs(bbox.ylen - 310.0) < 1e-6           # actual depth from the matched section
    assert len(geometry.solid.solids().vals()) == 1
    assert geometry.connection_count == 0          # no connections in this milestone


# === Step 9: length is authoritative, never confused with section depth/width ===
def test_member_length_is_never_confused_with_section_dimensions():
    row = make_real_member_row(mark="REAL-UB-LEN", length_mm=4000.0)
    validated = real_member_to_validated_member(row, make_matcher())
    geometry = generate_geometry(validated)

    # FULL_UB_SECTION's own depth (310) and flange_width (165) are both very
    # different numbers from the member length (4000) — if the implementation
    # ever accidentally used a section dimension as the length, this would fail.
    assert geometry.length_mm == 4000.0
    assert geometry.length_mm != FULL_UB_SECTION["depth"]
    assert geometry.length_mm != FULL_UB_SECTION["flange_width"]

    bbox = geometry.solid.val().BoundingBox()
    assert abs(bbox.zlen - 4000.0) < 1e-6  # the longitudinal extent, before any connections


# === Step 10: a realistic failure shaped exactly like the real current dataset ===
def test_realistic_arkles_shaped_member_without_length_is_rejected():
    # Every one of the real 54 Arkles Strand rows has length_mm = None today —
    # see tests/test_arkles_strand.py's ARKLES_STRAND_RAW and its own final
    # assertion. This reproduces one such row (BF1, matched to 310UB46.2) in
    # the real stored-row shape, with no invented length.
    row = make_real_member_row(
        mark="BF1", section_name="310UB46.2", section_name_raw="310UB46",
        length_mm=None, review_status="extracted",
    )
    matcher = FakeSectionMatcher({
        "310UB46.2": {"name": "310UB46.2", "family": "UB", "weight_per_metre": 46.2},
    })

    with pytest.raises(GeometryValidationError, match="length_mm"):
        real_member_to_validated_member(row, matcher)


# === Connection independence: existing single/multi-connection CAD behaviour
# is reachable, unmodified, once a real member becomes a ValidatedSteelMember ===
def test_real_member_can_still_carry_connections_via_existing_cad_api():
    from app.cad_engine.interface import ValidatedConnection

    row = make_real_member_row(mark="REAL-UB-CONN", length_mm=4000.0)
    validated = real_member_to_validated_member(row, make_matcher())
    validated.connection_refs = ["conn-1"]  # the adapter itself never adds connections (out of scope)

    connection = ValidatedConnection(
        connection_id="conn-1", connected_members=["REAL-UB-CONN"],
        plates=[{"width": 90.0, "height": 250.0, "thickness": 10.0}],
        bolts=[{"diameter": 18.0, "quantity": 2, "vertical_spacing": 150.0}],
        welds=[], dimensions=None, source_refs=[{"page": 1}], validation_status="extracted",
        connection_type="END_PLATE",
    )
    geometry = generate_geometry(validated, connections=[connection])

    assert geometry.connection_count == 1
    assert len(geometry.solid.solids().vals()) == 1


# ===================================================================
# Milestone 7B — real member -> 7A adapter -> ValidatedSteelMember ->
# generate_geometry() -> actual CadQuery solid. Every test below goes
# through real_member_to_validated_member() explicitly (never a
# hand-built ValidatedSteelMember for the primary fixtures) — the
# whole point of 7B is proving this exact chain, not a shortcut around
# the 7A adapter.
# ===================================================================

# A full, CAD-capable PFC row — every field app.cad_engine.sections.build_pfc_profile()
# requires is present. Same synthetic values already established in
# tests/test_cad_engine.py's SECTION_250PFC.
FULL_PFC_SECTION = {
    "name": "250PFC", "family": "PFC",
    "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 15.0, "web_thickness": 8.0,
    "weight_per_metre": 35.5,
}


def make_primary_7b_row(**overrides) -> dict:
    """
    The exact primary 7B integration fixture, in the real stored-row
    shape (see make_real_member_row()'s own docstring for where that
    shape comes from). Deliberately carries NO CAD geometry fields
    (no depth/flange_width/flange_thickness/web_thickness) — those are
    not part of the real steel_members contract, and this fixture's
    whole purpose is proving the adapter obtains them from the section
    matcher, not from this dict.
    """
    row = {
        "mark": "REAL-UB-CAD-001",
        "section_name": "310UB40",
        "section_name_raw": "310UB40",
        "section_family": "UB",
        "length_mm": 4000,
        "grade": "300PLUS",
        "quantity": 1,
        "review_status": "approved",
        "source_page": 1,
        "source_drawing_id": "REAL-CAD-TEST",
    }
    row.update(overrides)
    return row


# === Definition-of-done 1-5: real row -> adapter -> ValidatedSteelMember ->
# generate_geometry() -> a genuine, single-solid CadQuery result ===
def test_primary_real_member_reaches_a_genuine_cad_solid():
    row = make_primary_7b_row()
    assert "depth" not in row and "flange_width" not in row  # no duplicated CAD geometry in the fixture

    validated = real_member_to_validated_member(row, make_matcher())
    geometry = generate_geometry(validated)

    assert geometry.solid is not None
    solids = geometry.solid.solids().vals()
    assert len(solids) == 1  # exactly one connected steel solid — not zero, not several


# === Definition-of-done 6: cross-section dimensions come from the existing
# CAD section builder / matched engineering data, axis orientation verified
# from the actual solid rather than assumed ===
def test_primary_real_member_cross_section_matches_matched_ub_geometry():
    validated = real_member_to_validated_member(make_primary_7b_row(), make_matcher())
    geometry = generate_geometry(validated)

    bbox = geometry.solid.val().BoundingBox()
    # Axis convention confirmed against tests/test_cad_engine.py's own
    # established proof (test_geometry_uses_supplied_dimensions_not_hardcoded):
    # flange_width -> X, depth -> Y, length -> Z. Not assumed here — this
    # test would fail immediately if that convention ever changed.
    assert abs(bbox.xlen - FULL_UB_SECTION["flange_width"]) < 1e-6  # 165mm
    assert abs(bbox.ylen - FULL_UB_SECTION["depth"]) < 1e-6          # 310mm


# === Definition-of-done 7: longitudinal dimension is exactly the supplied length_mm ===
def test_primary_real_member_length_is_exactly_4000():
    validated = real_member_to_validated_member(make_primary_7b_row(), make_matcher())
    geometry = generate_geometry(validated)

    assert geometry.length_mm == 4000.0
    bbox = geometry.solid.val().BoundingBox()
    assert abs(bbox.zlen - 4000.0) < 1e-6  # longitudinal extent, before any connections/hardware


# === Definition-of-done 13: no connections or hardware exist on the primary result ===
def test_primary_real_member_has_no_connections_or_hardware():
    validated = real_member_to_validated_member(make_primary_7b_row(), make_matcher())
    geometry = generate_geometry(validated)

    assert geometry.connection_count == 0
    assert geometry.connection_types == []
    assert geometry.hardware == []
    assert geometry.connections == []


# === Definition-of-done 8: length authority / anti-cheating — changing length_mm
# changes ONLY the longitudinal CAD dimension, cross-section stays fixed ===
def test_length_authority_different_lengths_produce_different_cad_length():
    matcher = make_matcher()

    row_4000 = make_primary_7b_row(mark="REAL-UB-CAD-001", length_mm=4000)
    row_2750 = make_primary_7b_row(mark="REAL-UB-CAD-002", length_mm=2750)

    geometry_4000 = generate_geometry(real_member_to_validated_member(row_4000, matcher))
    geometry_2750 = generate_geometry(real_member_to_validated_member(row_2750, matcher))

    assert geometry_4000.length_mm == 4000.0
    assert geometry_2750.length_mm == 2750.0

    bbox_4000 = geometry_4000.solid.val().BoundingBox()
    bbox_2750 = geometry_2750.solid.val().BoundingBox()
    assert abs(bbox_4000.zlen - 4000.0) < 1e-6
    assert abs(bbox_2750.zlen - 2750.0) < 1e-6

    # Cross-section is identical regardless of length — both use the same matched section.
    assert abs(bbox_4000.xlen - bbox_2750.xlen) < 1e-6
    assert abs(bbox_4000.ylen - bbox_2750.ylen) < 1e-6


# === Definition-of-done 9: section authority / anti-cheating — same length,
# different supported section -> different cross-section, same length ===
def test_section_authority_different_sections_produce_different_cross_section():
    matcher = FakeSectionMatcher({
        "310UB40": FULL_UB_SECTION,
        "250PFC": FULL_PFC_SECTION,
    })

    ub_row = make_primary_7b_row(mark="REAL-UB-CAD-003", section_name="310UB40", length_mm=4000)
    pfc_row = make_primary_7b_row(mark="REAL-PFC-CAD-001", section_name="250PFC", length_mm=4000)

    ub_geometry = generate_geometry(real_member_to_validated_member(ub_row, matcher))
    pfc_geometry = generate_geometry(real_member_to_validated_member(pfc_row, matcher))

    assert ub_geometry.section_family == "UB"
    assert pfc_geometry.section_family == "PFC"

    # Length is identical for both — only the matched section changed.
    assert ub_geometry.length_mm == pfc_geometry.length_mm == 4000.0
    ub_bbox = ub_geometry.solid.val().BoundingBox()
    pfc_bbox = pfc_geometry.solid.val().BoundingBox()
    assert abs(ub_bbox.zlen - 4000.0) < 1e-6
    assert abs(pfc_bbox.zlen - 4000.0) < 1e-6

    # Cross-sections genuinely differ, each matching its own matched section's geometry.
    assert abs(ub_bbox.xlen - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs(ub_bbox.ylen - FULL_UB_SECTION["depth"]) < 1e-6
    assert abs(pfc_bbox.xlen - FULL_PFC_SECTION["flange_width"]) < 1e-6
    assert abs(pfc_bbox.ylen - FULL_PFC_SECTION["depth"]) < 1e-6
    assert abs(ub_bbox.xlen - pfc_bbox.xlen) > 1.0  # genuinely different, not a coincidence
    assert abs(ub_bbox.ylen - pfc_bbox.ylen) > 1.0


# === Definition-of-done 11: missing length on the primary-shaped fixture still
# prevents CAD generation, and the failure is never silently swallowed ===
def test_primary_shaped_member_with_missing_length_never_produces_a_solid():
    row = make_primary_7b_row(length_mm=None)
    with pytest.raises(GeometryValidationError, match="length_mm"):
        real_member_to_validated_member(row, make_matcher())
    # No fallback/empty-geometry path exists to fall through to — the adapter
    # call above is the only way to reach generate_geometry() in this flow,
    # and it already raised.


# === Definition-of-done 12: unsupported section on the primary-shaped fixture
# still prevents CAD generation, no substitution ===
def test_primary_shaped_member_with_unsupported_section_never_produces_a_solid():
    row = make_primary_7b_row(section_name="200UC46.2")  # matched, but UC has no CAD builder
    with pytest.raises(GeometryValidationError, match="no supported CAD profile builder"):
        real_member_to_validated_member(row, make_matcher())


# === Optional (Step 16): STEP export of the primary real-member solid, as a
# sanity check only — not itself an acceptance criterion ===
def test_primary_real_member_step_export(tmp_path):
    validated = real_member_to_validated_member(make_primary_7b_row(), make_matcher())
    geometry = generate_geometry(validated)

    out_path = tmp_path / "REAL-UB-CAD-001_310UB40_4000.step"
    geometry.solid.val().exportStep(str(out_path))

    assert out_path.exists()
    assert out_path.stat().st_size > 0


# ===================================================================
# Milestone 7C — real member row -> 7A adapter -> ValidatedSteelMember
# -> generate_geometry() -> GeneratedMemberGeometry -> the EXISTING
# PDF generator (generate_fabrication_drawing_pdf). No new PDF
# construction, no second geometry source — every test below passes
# the actual GeneratedMemberGeometry object straight into the existing
# PDF API, exactly as tests/test_drawing_generator.py's own fixtures
# already do for synthetic geometry.
# ===================================================================

def make_primary_7c_row(**overrides) -> dict:
    """The exact primary 7C fixture — same real stored-row shape as 7B's
    make_primary_7b_row(), with 7C's own mark/source identifiers."""
    row = {
        "mark": "REAL-UB-PDF-001",
        "section_name": "310UB40",
        "section_name_raw": "310UB40",
        "section_family": "UB",
        "length_mm": 4000,
        "grade": "300PLUS",
        "quantity": 1,
        "review_status": "approved",
        "source_page": 1,
        "source_drawing_id": "REAL-PDF-TEST",
    }
    row.update(overrides)
    return row


def _pdf_text(path) -> str:
    reader = pypdf.PdfReader(path)
    return reader.pages[0].extract_text()


def _real_member_pdf(row: dict, matcher, tmp_path, filename: str = "out.pdf", **pdf_kwargs):
    """The full 7C chain in one place: real row -> adapter -> CAD -> PDF.
    Returns (geometry, pdf_path) so callers can inspect both the actual
    GeneratedMemberGeometry and the resulting file."""
    validated = real_member_to_validated_member(row, matcher)
    geometry = generate_geometry(validated)
    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / filename,
        revision="A", date=datetime.date(2026, 1, 1), **pdf_kwargs,
    )
    return geometry, out_path


# === Definition-of-done 1-6: real row -> adapter -> CAD -> PDF, and the
# pre-PDF geometry checks Step 4 asks for ===
def test_primary_real_member_pdf_is_valid_and_non_empty(tmp_path):
    row = make_primary_7c_row()
    validated = real_member_to_validated_member(row, make_matcher())
    geometry = generate_geometry(validated)

    # Step 4: confirm the generated geometry itself before it ever reaches the PDF.
    assert geometry.solid is not None
    assert len(geometry.solid.solids().vals()) == 1
    assert geometry.connection_count == 0
    assert geometry.hardware == []
    assert geometry.length_mm == 4000.0

    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "primary.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    reader = pypdf.PdfReader(out_path)  # raises if not a genuinely valid PDF
    assert len(reader.pages) == 1


# === Definition-of-done 7-9: real identity, real section, real length reach the PDF ===
def test_primary_real_member_pdf_contains_real_identity(tmp_path):
    _geometry, out_path = _real_member_pdf(make_primary_7c_row(), make_matcher(), tmp_path)
    text = _pdf_text(out_path)

    assert "REAL-UB-PDF-001" in text
    assert "310UB40" in text
    assert "4000" in text
    assert "UB-TEST-1" not in text  # never the generic synthetic mark


# === Definition-of-done 10: existing standard drawing content, no connection info ===
def test_primary_real_member_pdf_contains_standard_drawing_content(tmp_path):
    _geometry, out_path = _real_member_pdf(make_primary_7c_row(), make_matcher(), tmp_path)
    text = _pdf_text(out_path)

    assert "FABRICATION DRAWING" in text
    assert "ELEVATION" in text
    assert "CROSS SECTION" in text


# === Definition-of-done 14: no connections/hardware in this milestone's drawing ===
def test_primary_real_member_pdf_has_no_connection_detail(tmp_path):
    _geometry, out_path = _real_member_pdf(make_primary_7c_row(), make_matcher(), tmp_path)
    text = _pdf_text(out_path).upper()

    assert "START CONNECTION" not in text
    assert "END CONNECTION" not in text
    assert "BOLTS" not in text
    assert "HOLES" not in text
    assert "CONNECTION" not in text


# === Geometry authority (Step 9/10): the PDF's dimensions are read back from the
# ACTUAL generated CadQuery solid — never independently recomputed from the row ===
def test_primary_real_member_pdf_dimensions_match_generated_geometry(tmp_path):
    row = make_primary_7c_row()
    validated = real_member_to_validated_member(row, make_matcher())
    geometry = generate_geometry(validated)

    # Independently measured from the actual generated solid — not copied from
    # the row, not copied from FULL_UB_SECTION, not assumed.
    bbox = geometry.solid.val().BoundingBox()
    flange_width_mm = round(bbox.xlen, 6)
    depth_mm = round(bbox.ylen, 6)
    length_mm = round(bbox.zlen, 6)
    assert flange_width_mm == 165.0
    assert depth_mm == 310.0
    assert length_mm == 4000.0

    out_path = generate_fabrication_drawing_pdf(
        geometry, tmp_path / "dims.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )
    text = _pdf_text(out_path)

    for value in (flange_width_mm, depth_mm, length_mm):
        assert f"{value:g}" in text

    # The PDF call itself only ever received `geometry` — never `row` or
    # `validated` — so there is no code path by which it could have
    # recomputed these numbers from anything but the generated solid.


# === Definition-of-done 12: length variation reaches both CAD and PDF ===
def test_length_variation_reaches_cad_and_pdf(tmp_path):
    matcher = make_matcher()

    row_4000 = make_primary_7c_row(mark="REAL-UB-PDF-001", length_mm=4000)
    row_2750 = make_primary_7c_row(mark="REAL-UB-PDF-002", length_mm=2750)

    geometry_4000, pdf_4000 = _real_member_pdf(row_4000, matcher, tmp_path, "len4000.pdf")
    geometry_2750, pdf_2750 = _real_member_pdf(row_2750, matcher, tmp_path, "len2750.pdf")

    assert geometry_4000.length_mm == 4000.0
    assert geometry_2750.length_mm == 2750.0

    text_4000 = _pdf_text(pdf_4000)
    text_2750 = _pdf_text(pdf_2750)

    assert "4000" in text_4000
    assert "2750" in text_2750
    assert "2750" not in text_4000  # the old/other length must not leak in
    assert "4000" not in text_2750

    # Cross-section is unaffected by length — same matched section both times.
    bbox_4000 = geometry_4000.solid.val().BoundingBox()
    bbox_2750 = geometry_2750.solid.val().BoundingBox()
    assert abs(bbox_4000.xlen - bbox_2750.xlen) < 1e-6
    assert abs(bbox_4000.ylen - bbox_2750.ylen) < 1e-6
    assert "165" in text_4000 and "165" in text_2750
    assert "310" in text_4000 and "310" in text_2750


# === Definition-of-done 13: section variation reaches both CAD and PDF, length fixed ===
def test_section_variation_reaches_cad_and_pdf(tmp_path):
    matcher = FakeSectionMatcher({
        "310UB40": FULL_UB_SECTION,
        "250PFC": FULL_PFC_SECTION,
    })

    ub_row = make_primary_7c_row(mark="REAL-UB-PDF-003", section_name="310UB40", length_mm=4000)
    pfc_row = make_primary_7c_row(mark="REAL-PFC-PDF-001", section_name="250PFC", length_mm=4000)

    ub_geometry, ub_pdf = _real_member_pdf(ub_row, matcher, tmp_path, "ub.pdf")
    pfc_geometry, pfc_pdf = _real_member_pdf(pfc_row, matcher, tmp_path, "pfc.pdf")

    assert ub_geometry.section_family == "UB"
    assert pfc_geometry.section_family == "PFC"
    assert ub_geometry.length_mm == pfc_geometry.length_mm == 4000.0

    ub_text = _pdf_text(ub_pdf)
    pfc_text = _pdf_text(pfc_pdf)

    # Both drawings report the same 4000mm length.
    assert "4000" in ub_text
    assert "4000" in pfc_text

    # Section identity differs.
    assert "310UB40" in ub_text
    assert "250PFC" in pfc_text
    assert "310UB40" not in pfc_text
    assert "250PFC" not in ub_text

    # Cross-section actually differs, read back from each's own generated solid.
    ub_bbox = ub_geometry.solid.val().BoundingBox()
    pfc_bbox = pfc_geometry.solid.val().BoundingBox()
    assert abs(ub_bbox.xlen - FULL_UB_SECTION["flange_width"]) < 1e-6
    assert abs(ub_bbox.ylen - FULL_UB_SECTION["depth"]) < 1e-6
    assert abs(pfc_bbox.xlen - FULL_PFC_SECTION["flange_width"]) < 1e-6
    assert abs(pfc_bbox.ylen - FULL_PFC_SECTION["depth"]) < 1e-6
    assert f"{FULL_PFC_SECTION['flange_width']:g}" in pfc_text
    assert f"{FULL_PFC_SECTION['depth']:g}" in pfc_text


# === Definition-of-done 15: missing length produces no CAD and no PDF ===
def test_missing_length_produces_no_pdf(tmp_path):
    row = make_primary_7c_row(length_mm=None)
    out_path = tmp_path / "should_not_exist.pdf"

    with pytest.raises(GeometryValidationError, match="length_mm"):
        real_member_to_validated_member(row, make_matcher())

    assert not out_path.exists()  # the adapter raised before CAD or PDF ever ran


# === Definition-of-done 16: unsupported section produces no CAD and no PDF ===
def test_unsupported_section_produces_no_pdf(tmp_path):
    row = make_primary_7c_row(section_name="200UC46.2")  # matched, but UC has no CAD builder
    out_path = tmp_path / "should_not_exist.pdf"

    with pytest.raises(GeometryValidationError, match="no supported CAD profile builder"):
        real_member_to_validated_member(row, make_matcher())

    assert not out_path.exists()
