"""
Milestone 7U — Real Reviewed Connection -> Fabrication Drawing: an
INTEGRATION milestone proving the complete existing real-data path
already reaches the validated two-member connection drawing, end to
end, with no new geometry rule, no new connection type, and nothing
invented to paper over a genuine data gap:

    real member row A/B (steel_members shape)
            |
    real_member_to_validated_member() (7A)
            |
    ValidatedSteelMember A/B -> generate_geometry() -> place_member_geometry() (7I)
            |
    reviewed connection record (connections/connection_plates/bolt_groups shape)
            |
    real_connection_to_validated_connection() (7D)
            |
    ValidatedConnection
            |
    reviewed connection-detail record (attachments: [...])
            |
    reviewed_connection_detail_to_attachments() (7N)
            |
    ConnectionAttachment A/B
            |
    build_reviewed_two_member_connection_assembly() (7O)
            |
    ReviewedTwoMemberConnectionAssembly
            |
    generate_fabrication_drawing_from_reviewed_assembly() (7S/7T)
            |   -- validate_reviewed_connection_assembly() (7R; composes 7P/7Q) --
            v
    two-member connection fabrication drawing PDF (7T)

STEP 1/2 FINDING: `tests/test_reviewed_connection_assembly.py`'s own
`_build_primary_assembly()` (7O) ALREADY IS this exact chain, calling
every one of the real adapters above unchanged and never hand-building
a ValidatedConnection/ValidatedSteelMember/ConnectionAttachment inside
the test — 7O, 7P, 7Q, 7R, 7S, and 7T all reuse it unmodified for
their own fixtures. 7U reuses it too, rather than re-implementing an
equivalent orchestration a second time (that would be exactly the kind
of duplicated integration logic this milestone's own Step 2 forbids).
What 7U adds beyond 7O-7T: (a) carrying that assembly through the
existing 7S/7T drawing gate (proven here for the first time against
data built via the REAL adapters rather than only 7O-7T's own internal
fixtures), (b) explicit, monkeypatch-based proof that each real
adapter is genuinely invoked with genuine real-shaped input (not
merely called in the right order by a test), and (c) the four real-data
integration failure modes Step 8 requires, each traced to its actual
originating adapter/gate rather than re-testing 7K-7Q's own geometry
rules a second time.

FIXTURE PROVENANCE (Step 3, critical — read before trusting any value
below): Member A (`REAL-UB-CAD-001`, 310UB40, 4000mm) is the
established REAL fixture (7B/7C/7E/7F/7G) — mark, section, and length
all trace to the real extraction/pipeline row shape. Member B (`L2`,
250PFC, 3000mm) has a REAL mark and REAL section (the sole real
occurrence of "L2" in the 54-row Arkles Strand extraction, page 14 —
see tests/test_real_multi_member_cad.py's own module docstring); its
length_mm is explicitly HUMAN-REVIEWED/SUPPLEMENTED, since every real
Arkles row has length_mm=None. The reviewed connection (180x250x12mm
end plate, 4xO22mm holes, position=END) and its attachments
(REAL-UB-CAD-001->END, L2->START) and its project-space location
(z=3994) are all explicitly HUMAN-REVIEWED/SUPPLEMENTED test fixture
data — see real_connection_adapter.py's own module docstring for WHY:
today's AI extraction schema has no field for connection position, no
numeric hole-void diameter, and (per 7N's own docstring) no attachment
surface concept at all. None of this is presented as, or confused
with, an actual extracted Arkles connection detail, and the resulting
PDF is never called a complete or fabrication-ready Arkles drawing.

ANTI-HARDCODING (Step 10): `test_renderer_reflects_different_real_
reviewed_member_data` builds a second, equally real-adapter-derived
assembly with Member B's length overridden to 3500mm (an existing,
already-exercised HUMAN-REVIEWED/SUPPLEMENTED override — see 7O's own
`test_6_member_b_length_change_leaves_attachment_unchanged`) and
confirms the resulting PDF's actual rendered length and project-space
elevation span change accordingly — proving the renderer responds to
whatever the real member adapter actually measured, not a value baked
into the drawing layer.
"""
import datetime

import pypdf
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.reviewed_connection_drawing_gate import generate_fabrication_drawing_from_reviewed_assembly
from tests.test_real_member_adapter import FakeSectionMatcher, FULL_PFC_SECTION, FULL_UB_SECTION, UNSUPPORTED_FAMILY_SECTION
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row
from tests.test_real_multi_member_connection import make_multi_member_connection_record
from tests.test_reviewed_connection_assembly import _build_primary_assembly
from tests.test_reviewed_connection_attachment import make_reviewed_connection_detail_record

import tests.test_reviewed_connection_assembly as assembly_chain_module


def _pdf_circles_and_paths(path):
    """Same raw-content-stream inspection tests/test_drawing_generator.py and
    tests/test_two_member_connection_drawing.py already use."""
    reader = pypdf.PdfReader(path)
    content = reader.pages[0].get_contents().get_data().decode("latin-1")
    curve_ops = content.count(" c\n") + content.count(" c ")
    line_ops = content.count(" l\n") + content.count(" l ")
    return curve_ops, line_ops


def _matcher_with_unsupported_family() -> FakeSectionMatcher:
    """
    The established primary matcher (7G's make_multi_member_matcher())
    plus the existing, already-proven-unsupported "200UC46.2"/UC entry
    from tests/test_real_member_adapter.py — no new section is
    invented; UC is genuinely unsupported by app.cad_engine.sections
    today (see that file's own module docstring).
    """
    return FakeSectionMatcher({
        "310UB40": FULL_UB_SECTION, "250PFC": FULL_PFC_SECTION, "200UC46.2": UNSUPPORTED_FAMILY_SECTION,
    })


# === 1. Complete real-member + reviewed-connection path generates a two-member PDF ===
def test_complete_real_data_path_generates_two_member_pdf(tmp_path):
    assembly, *_ = _build_primary_assembly()

    out_path = generate_fabrication_drawing_from_reviewed_assembly(
        assembly, tmp_path / "out.pdf", revision="A", date=datetime.date(2026, 1, 1),
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    reader = pypdf.PdfReader(out_path)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()

    # Member marks, sections, lengths (Step 9)
    assert "REAL-UB-CAD-001" in text and "310UB40" in text and "4000" in text
    assert "L2" in text and "250PFC" in text and "3000" in text
    # Connection detail + plate representation (Step 9)
    assert "CONNECTION ELEVATION" in text
    assert "CONNECTION DETAIL" in text
    assert "PLATE:" in text and "180" in text and "250" in text and "12" in text
    assert "22" in text  # hole diameter, from the reviewed record's real numeric value

    # Actual hole representation, not text alone (Step 9): 4 real holes x 4
    # Bezier curves each — the same raw-content-stream proof 7T established.
    curve_ops, line_ops = _pdf_circles_and_paths(out_path)
    assert curve_ops == 16
    assert line_ops > 0


# === 2. Real member adapter is actually used ===
def test_real_member_adapter_is_actually_invoked(monkeypatch):
    calls = []
    original = assembly_chain_module.real_member_to_validated_member

    def tracking(row, matcher):
        calls.append(row.get("mark"))
        return original(row, matcher)

    monkeypatch.setattr(assembly_chain_module, "real_member_to_validated_member", tracking)

    _build_primary_assembly()

    assert calls == ["REAL-UB-CAD-001", "L2"]  # both real member rows, in build order


# === 3. Real connection adapter is actually used ===
def test_real_connection_adapter_is_actually_invoked(monkeypatch):
    calls = []
    original = assembly_chain_module.real_connection_to_validated_connection

    def tracking(record):
        calls.append(record.get("connection_id"))
        return original(record)

    monkeypatch.setattr(assembly_chain_module, "real_connection_to_validated_connection", tracking)

    _build_primary_assembly()

    assert calls == ["CONN-REAL-UB-CAD-001-L2"]


# === 4. Reviewed attachment adapter is actually used ===
def test_reviewed_attachment_adapter_is_actually_invoked(monkeypatch):
    calls = []
    original = assembly_chain_module.reviewed_connection_detail_to_attachments

    def tracking(record, connected_member_marks):
        calls.append((record.get("connection_id"), tuple(sorted(connected_member_marks))))
        return original(record, connected_member_marks)

    monkeypatch.setattr(assembly_chain_module, "reviewed_connection_detail_to_attachments", tracking)

    _build_primary_assembly()

    assert calls == [("CONN-REAL-UB-CAD-001-L2", ("L2", "REAL-UB-CAD-001"))]


# === 5. 7R executes before drawing generation, on the real-data path ===
def test_7r_gate_executes_before_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    assembly, *_ = _build_primary_assembly()
    calls = []
    original_validate = gate_module.validate_reviewed_connection_assembly

    def tracking_validate(a):
        calls.append("validate")
        return original_validate(a)

    monkeypatch.setattr(gate_module, "validate_reviewed_connection_assembly", tracking_validate)

    generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    assert calls == ["validate"]


# === 6. Case A: incomplete real connection fails through the real connection adapter ===
def test_incomplete_connection_fails_through_real_connection_adapter():
    connection_record = make_multi_member_connection_record(position=None)  # missing required position
    with pytest.raises(GeometryValidationError, match="no explicit position"):
        _build_primary_assembly(connection_record=connection_record)


# === 7. Case B: invalid attachment fails through the reviewed attachment contract ===
def test_invalid_attachment_fails_through_reviewed_attachment_contract():
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "MIDDLE"},  # not a supported value
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    with pytest.raises(GeometryValidationError, match="unsupported surface_reference"):
        _build_primary_assembly(detail_record=detail_record)


# === 8. Case D: unsupported real member section/family fails through the real member adapter ===
def test_unsupported_member_family_fails_through_real_member_adapter():
    member_a_row = make_member_a_row(section_name="200UC46.2")  # matches, but UC has no CAD builder
    with pytest.raises(GeometryValidationError, match="no supported CAD profile builder"):
        _build_primary_assembly(member_a_row=member_a_row, matcher=_matcher_with_unsupported_family())


# === 9. Case C: an assembly that builds but fails 7P/7Q blocks drawing generation ===
def test_geometry_validation_failure_blocks_drawing_generation(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    # A valid enum surface_reference (not case B's invalid string) that is
    # nonetheless geometrically wrong for this fixture — the assembly
    # itself builds fine (7K/7L never inspect attachments), but 7P/7Q
    # (composed through 7R) reject it.
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    pdf_calls = []
    monkeypatch.setattr(
        gate_module, "generate_connection_fabrication_drawing_pdf",
        lambda *a, **k: pdf_calls.append(True) or None,
    )

    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError, match="REAL-UB-CAD-001"):
        generate_fabrication_drawing_from_reviewed_assembly(assembly, out_path)

    assert pdf_calls == []  # the drawing generator was never reached
    assert not out_path.exists()


# === 10. No partial PDF after any failure ===
def test_no_partial_pdf_after_failure(tmp_path):
    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError):
        generate_fabrication_drawing_from_reviewed_assembly(assembly, out_path)

    assert not out_path.exists()
    assert list(tmp_path.iterdir()) == []  # no partial/temp artifact of any kind was left behind


# === 11. No fallback to the old member-only drawing on failure ===
def test_no_fallback_to_member_only_drawing_on_failure(tmp_path, monkeypatch):
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module

    detail_record = make_reviewed_connection_detail_record(attachments=[
        {"member_mark": "REAL-UB-CAD-001", "surface_reference": "START"},
        {"member_mark": "L2", "surface_reference": "START"},
    ])
    assembly, *_ = _build_primary_assembly(detail_record=detail_record)

    calls = []
    monkeypatch.setattr(
        gate_module, "generate_connection_fabrication_drawing_pdf",
        lambda *a, **k: calls.append("two-member") or None,
    )

    out_path = tmp_path / "should_not_exist.pdf"
    with pytest.raises(GeometryValidationError):
        generate_fabrication_drawing_from_reviewed_assembly(assembly, out_path)

    assert calls == []
    assert not out_path.exists()
    assert not hasattr(gate_module, "generate_fabrication_drawing_pdf")


# === 12. No mutation of source records or generated geometry ===
def test_no_mutation_of_source_records_or_generated_geometry(tmp_path):
    matcher = None  # use the default established matcher via _build_primary_assembly
    row_a = make_member_a_row()
    row_b = make_member_b_row()
    connection_record = make_multi_member_connection_record()
    detail_record = make_reviewed_connection_detail_record()

    assembly, *_ = _build_primary_assembly(
        member_a_row=row_a, member_b_row=row_b,
        connection_record=connection_record, detail_record=detail_record, matcher=matcher,
    )
    bbox_a_before = assembly.member_a.geometry.solid.val().BoundingBox()
    plate_bbox_before = assembly.connection_geometry.plate.val().BoundingBox()
    row_a_before = dict(row_a)
    connection_record_before = dict(connection_record)

    generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")

    bbox_a_after = assembly.member_a.geometry.solid.val().BoundingBox()
    plate_bbox_after = assembly.connection_geometry.plate.val().BoundingBox()
    assert abs(bbox_a_after.zmax - bbox_a_before.zmax) < 1e-6
    assert abs(plate_bbox_after.zmin - plate_bbox_before.zmin) < 1e-6
    assert row_a == row_a_before  # the raw real member row was never mutated by any adapter
    assert connection_record == connection_record_before


# === 13. Repeated valid generation from equivalent real-data assemblies is consistent ===
def test_repeated_valid_generation_from_real_data_is_consistent(tmp_path):
    assembly_1, *_ = _build_primary_assembly()
    assembly_2, *_ = _build_primary_assembly()

    out_1 = generate_fabrication_drawing_from_reviewed_assembly(
        assembly_1, tmp_path / "out1.pdf", date=datetime.date(2026, 1, 1),
    )
    out_2 = generate_fabrication_drawing_from_reviewed_assembly(
        assembly_2, tmp_path / "out2.pdf", date=datetime.date(2026, 1, 1),
    )

    text_1 = pypdf.PdfReader(out_1).pages[0].extract_text()
    text_2 = pypdf.PdfReader(out_2).pages[0].extract_text()
    assert text_1 == text_2
    assert _pdf_circles_and_paths(out_1) == _pdf_circles_and_paths(out_2)


# === 14. Anti-hardcoding (Step 10): renderer reflects different real/reviewed member data ===
def test_renderer_reflects_different_real_reviewed_member_data(tmp_path):
    """
    A second HUMAN-REVIEWED/SUPPLEMENTED Member B length (3500mm instead
    of the primary fixture's 3000mm — the exact override 7O's own
    `test_6_member_b_length_change_leaves_attachment_unchanged` already
    proved safe), carried through the same real member adapter, produces
    a genuinely different drawing: a different rendered length AND a
    different actual project-space elevation span (Member A's own end,
    unaffected, to Member B's own new end) — both measured, not assumed.
    """
    assembly, *_ = _build_primary_assembly(member_b_row=make_member_b_row(length_mm=3500))
    assert assembly.member_b.geometry.length_mm == 3500.0

    out_path = generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / "out.pdf")
    text = pypdf.PdfReader(out_path).pages[0].extract_text()

    assert "3500" in text
    assert "3000" not in text.replace("3500", "")  # the old 3000mm value is genuinely gone, not just added-to

    bbox_b = assembly.member_b.geometry.solid.val().BoundingBox()
    assert abs(bbox_b.zmax - 7500.0) < 1e-6  # 4000 (placement.z) + 3500 (new length), measured from real geometry
