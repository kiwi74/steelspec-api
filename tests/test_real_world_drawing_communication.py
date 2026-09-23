"""MILESTONE 7AW — Real-World Fabrication Drawing Communication Proof.

The question: does the ACTUAL fabrication drawing communicate the reviewed
engineering information clearly and truthfully — rather than relying on
hidden workflow/package/audit records? The drawing itself is the authority
(§1): every positive assertion below reads the generated PDF's extracted
text layer with the real PDF parser (§17). A value that exists only in the
internal data structures counts as NOT communicated.

The primary proof is the genuine 7AV Selby journey: the real page-5 VIEW B-B
four-member connection (005/310UB40, PL018/160x10PL, PL032/165x10PL,
PL025/81x10PL, material 300) through the unmodified chain —

    7Y intake -> 7AB -> 7AC tasks -> human resolutions -> 7AD rerun
    -> 7AA/7Z -> 7AE gate -> 7AF dispatch -> 7AG verification
    -> 7AQ production acceptance -> 7AR fabrication package
    -> 7AS fabricator acceptance

— with the delivered CONN-SELBY-VIEW-BB-fabrication.pdf re-read as the
fabricator would read it.

WHAT THE DRAWING NOW COMMUNICATES (the 7AW change): the reviewed connection
location ('CONNECTION LOCATION' + 'X 0, Y 0, Z 5017 mm, RX 0°, RY 0°, RZ 0°'
— ConnectionLocation's own documented mm/degrees semantics, never relabelled),
the reviewed per-member attachment semantics (ATTACH END per member row),
a deterministic PAGE 1 OF 1 in the title block, and NO STATUS cell at all
(the former STATUS TEST default was an unrecorded claim and is removed
truthfully). MATERIAL 300 still travels the reviewed record; plate, holes
and pattern are printed; welds and connection type stay omitted because the
reviewed record carries none — the generator manufactures nothing (§10).

BRIEF ITEM MAP (§16: 24 items):
  1-17 real positive case ................................... TestTheRealDrawingCommunicates
    1  genuine 7AV artifact / 2 marks / 3 sections / 4 lengths
    5  material 300 (reviewed record) / 6 plate / 7 holes / 8 welds not invented
    9  location / 10 attachments / 11 page number / 12 title-block identity
    13 STATUS TEST removed truthfully / 14 7AG verifies / 15 7AQ accepts
    16 7AR packages / 17 7AS independently accepts the improved drawing
  18-21 negatives ........................................... TestHonestOmissions
    18 absent material stays NOT SPECIFIED + genuine 7AS finding
    19 absent location flagged, never manufactured
    20 absent attachments flagged, never manufactured
    21 no invented engineering meaning anywhere on the drawing
  22-24 regression .......................................... TestRegressions
    22 two-member drawing valid and 7AS green (7AU)
    23 7AU material proof green
    24 7AV multi-member proof green + repeatability x2 (§18)
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.cad_engine import fabricator_acceptance as fa
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import (
    PIPELINE_STAGE_ATTACHMENTS,
    PIPELINE_STAGE_LOCATION,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.exception_resolution import (
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
    TASK_SELECT_ATTACHMENT,
)
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_READY,
    build_fabrication_package,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    accept_production_job,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_HUMAN_REVIEWED,
)
from app.drawing_generator.interface import generate_fabrication_drawing_pdf
from tests.test_drawing_generator import make_ub_geometry
from tests.test_fabricator_acceptance import _answers as _fa_answers
from tests.test_material_specification import _strip_per_save_metadata
from tests.test_project_workflow import _full_sequence
from tests.test_real_world_material_extraction import (
    _answers as _au_answers,
    _selby_journey,
)
from tests.test_real_world_multi_member_connection import (
    JOURNEY_ANSWERS_BY_TASK_TYPE,
    JOURNEY_PACKAGE_ID,
    _assert_review_with,
    _fabricator_answers,
    _negative_case,
    _page5_capture,
    _page5_journey,
    _view_b_b_entry,
    needs_selby_capture,
)


# ---------------------------------------------------------------------------
# Helpers: the actual PDF text is the authority (§17).
# ---------------------------------------------------------------------------
def _pdf_lines(pdf_path):
    """The drawing's extracted text layer, stripped — exactly what a reader
    (and 7AS) sees; empty lines dropped."""
    return [line.strip() for line in "\n".join(
        page.extract_text() or "" for page in PdfReader(pdf_path).pages
    ).splitlines() if line.strip()]


def _label_value(lines, label):
    """The value following a title-block label, the way 7AS reads it."""
    return lines[lines.index(label) + 1]


def _single_member_pdf_lines(tmp_path):
    """A bare single-member drawing: no reviewed connection record exists, so
    no location/attachment semantics may appear."""
    out = tmp_path / "single-member.pdf"
    generate_fabrication_drawing_pdf(make_ub_geometry(), out, quantity=1)
    return _pdf_lines(out)


@pytest.fixture(scope="module")
def communicated_drawing(tmp_path_factory):
    """The genuine 7AV journey through 7AS, with the actual delivered PDF's
    text — the drawing itself is the evidence (§1)."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7aw"))
    journey = _page5_journey(tmp)
    pdf_path = Path(journey.workflow.generated_files[0])
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_fabricator_answers(package))
    lines = _pdf_lines(pdf_path)
    record = journey.workflow.connection_records[0]
    return SimpleNamespace(
        tmp=tmp, journey=journey, pdf_path=pdf_path, lines=lines,
        text="\n".join(lines).upper(), acceptance=acceptance,
        package=package, result=result, record=record,
        assembly=record.pipeline.reviewed_assembly,
    )


@pytest.fixture(scope="module")
def au_drawing(tmp_path_factory):
    """The genuine 7AU two-member Selby journey through 7AS (regression)."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7au-7aw"))
    journey = _selby_journey(tmp)
    pdf_path = Path(journey.workflow.generated_files[0])
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_au_answers(package, q12=fa.ANSWER_PASS))
    return SimpleNamespace(
        tmp=tmp, journey=journey, pdf_path=pdf_path,
        lines=_pdf_lines(pdf_path), acceptance=acceptance,
        package=package, result=result,
    )


@pytest.fixture(scope="module")
def arkles_package(tmp_path_factory):
    """The genuine Arkles production chain through 7AR: the real package
    whose drawings genuinely carry NO material grade (negative proof)."""
    tmp = Path(tmp_path_factory.mktemp("arkles-7aw"))
    states = _full_sequence(tmp)
    package = build_fabrication_package(
        accept_production_job(states[-1]), output_dir=tmp / "pkg")
    return SimpleNamespace(tmp=tmp, package=package)


# =============================================================================
# Items 1-17: the real positive case — the drawing communicates the reviewed
# record, read back from the actual PDF.
# =============================================================================
@needs_selby_capture
class TestTheRealDrawingCommunicates:
    # 1 — the artifact under test is the genuine 7AV deliverable.
    def test_the_drawing_is_the_genuine_7av_artifact(self, communicated_drawing):
        d = communicated_drawing
        assert d.pdf_path.name == "CONN-SELBY-VIEW-BB-fabrication.pdf"
        # 7AF recorded exactly this artifact; 7AG verified exactly this one.
        verification = d.record.verification_result
        assert verification.artifact_path == d.pdf_path
        assert verification.verification_status == VERIFICATION_STATUS_VERIFIED
        assert verification.dispatch_output_status == OUTPUT_STATUS_GENERATED

    # 2 — all four member marks, in the connection's own member order.
    def test_all_four_member_marks_are_printed(self, communicated_drawing):
        lines = communicated_drawing.lines
        expected = {"MEMBER A": "005", "MEMBER B": "PL018",
                    "MEMBER C": "PL032", "MEMBER D": "PL025"}
        assert [_label_value(lines, label) for label in expected] == list(expected.values())

    # 3 — every member section name.
    def test_every_member_section_is_printed(self, communicated_drawing):
        lines = communicated_drawing.lines
        for section in ("310UB40", "160x10PL", "165x10PL", "81x10PL"):
            assert section in lines, section
        assert _label_value(lines, "SECTION") == "310UB40"

    # 4 — every member length.
    def test_every_member_length_is_printed(self, communicated_drawing):
        lines = communicated_drawing.lines
        for length in ("5017 mm", "298 mm", "304 mm", "80 mm"):
            assert length in lines, length

    # 5 — MATERIAL 300 comes from the reviewed record, not raw extraction.
    def test_material_300_is_on_the_drawing_from_the_reviewed_record(
            self, communicated_drawing):
        assert _label_value(communicated_drawing.lines, "MATERIAL") == "300"
        conn = next(c for c in communicated_drawing.acceptance.connections
                    if c.package_id == JOURNEY_PACKAGE_ID)
        assert conn.trace.contract.ai_material == "300"
        provenance = {e.field: e.provenance
                      for e in conn.trace.contract.provenance}
        assert provenance["material"] == PROVENANCE_HUMAN_REVIEWED

    # 6 — the plate.
    def test_the_reviewed_plate_is_communicated(self, communicated_drawing):
        assert "PLATE: 160 × 298 × 10 mm" in communicated_drawing.lines

    # 7 — the holes and their pattern.
    def test_the_reviewed_holes_are_communicated(self, communicated_drawing):
        assert "HOLES: 2 × Ø18" in communicated_drawing.lines
        assert "PATTERN: 100 V" in communicated_drawing.lines

    # 8 — welds: NOT in the reviewed record, so NOT printed (truthful
    # omission — the raw capture's weld note was never reviewed).
    def test_welds_are_not_invented(self, communicated_drawing):
        assert "WELD" not in communicated_drawing.text

    # 9 — the reviewed location, in ConnectionLocation's established
    # mm/degrees semantics — never relabelled as FROM BASE / FROM TOP.
    def test_the_reviewed_location_is_printed_with_established_semantics(
            self, communicated_drawing):
        lines = communicated_drawing.lines
        value = _label_value(lines, "CONNECTION LOCATION")
        assert value == "X 0, Y 0, Z 5017 mm, RX 0°, RY 0°, RZ 0°"
        reviewed = JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_PROVIDE_LOCATION]
        location = communicated_drawing.assembly.location
        assert (location.x, location.y, location.z) == (
            reviewed["x"], reviewed["y"], reviewed["z"])
        assert (location.rotation_x, location.rotation_y, location.rotation_z) == (
            reviewed["rotation_x"], reviewed["rotation_y"], reviewed["rotation_z"])

    # 10 — the reviewed per-member attachment semantics, printed per member
    # row and pairing exactly with the reviewed answers.
    def test_reviewed_attachment_semantics_are_printed_per_member(
            self, communicated_drawing):
        lines = communicated_drawing.lines
        rows = {}
        for index, label in enumerate(lines):
            if label in {"MEMBER A", "MEMBER B", "MEMBER C", "MEMBER D"}:
                rows[lines[index + 1]] = lines[index + 7]
        reviewed = {entry["member_mark"]: entry["surface_reference"]
                    for entry in JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_SELECT_ATTACHMENT]}
        assert rows == reviewed
        assert {ref for _, ref in rows.items()} == {"END"}
        attachments = communicated_drawing.assembly.attachments
        assert {a.member_mark: a.surface_reference for a in attachments} == reviewed

    # 11 — the page number is part of the actual PDF artifact.
    def test_page_number_is_part_of_the_pdf(self, communicated_drawing):
        lines = communicated_drawing.lines
        assert "PAGE" in lines
        assert _label_value(lines, "PAGE") == "1 OF 1"

    # 12 — title-block identity fields preserved, new fields additive.
    def test_title_block_identity_fields_are_preserved(self, communicated_drawing):
        lines = communicated_drawing.lines
        assert _label_value(lines, "DRAWING NO.") == "FAB-CONN-SELBY-VIEW-BB"
        assert _label_value(lines, "REV") == "A"
        assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", _label_value(lines, "DATE"))
        assert _label_value(lines, "PROJECT") == "PROJ-7AV-SELBY"
        assert _label_value(lines, "SOURCE DRAWING") == "SELBY-C1136"
        assert _label_value(lines, "UNITS") == "mm"
        assert _label_value(lines, "SCALE") == "NTS"

    # 13 — the misleading STATUS TEST is gone; no approval state is invented
    # and none is claimed.
    def test_status_test_is_removed_truthfully(self, communicated_drawing):
        lines = communicated_drawing.lines
        assert "STATUS" not in lines
        assert "TEST" not in lines
        assert "APPROVED" not in communicated_drawing.text
        assert "FOR FABRICATION" not in communicated_drawing.text

    # 14 — 7AG verified the actual delivered artifact.
    def test_7ag_verifies_the_actual_artifact(self, communicated_drawing):
        verification = communicated_drawing.record.verification_result
        assert verification.verification_status == VERIFICATION_STATUS_VERIFIED
        assert verification.page_count == 1
        assert verification.failures == ()

    # 15 — 7AQ accepts the production job.
    def test_7aq_accepts_the_production_job(self, communicated_drawing):
        assert communicated_drawing.acceptance.status == ACCEPTANCE_STATUS_ACCEPTED

    # 16 — 7AR packages the verified drawing.
    def test_7ar_packages_the_drawing(self, communicated_drawing):
        package = communicated_drawing.package
        assert package.status == PACKAGE_STATUS_READY
        assert len(package.items) == 1
        packaged = Path(package.manifest_path).parent / package.items[0].packaged_path
        assert packaged.read_bytes() == communicated_drawing.pdf_path.read_bytes()

    # 17 — 7AS INDEPENDENTLY accepts the improved drawing by re-reading the
    # actual PDF: the location is now visible on the drawing, the page marker
    # and the STATUS-TEST notes are gone, and nothing substitutes for the
    # reviewer's own PASS answers.
    def test_7as_independently_accepts_the_improved_drawing(
            self, communicated_drawing):
        result = communicated_drawing.result
        assert result.status == ACCEPTANCE_STATUS_ACCEPTED
        results = {r.checklist_item: r for r in result.drawings[0].results}
        location = results[fa.CHECKLIST_ITEM_CONNECTION_LOCATION]
        assert location.answer == fa.ANSWER_PASS
        assert location.evidence_status == fa.EVIDENCE_PRESENT
        assert location.visible_in_drawing is True
        assert location.evaluator_findings == ()
        identity = results[fa.CHECKLIST_ITEM_DRAWING_IDENTITY]
        assert identity.answer == fa.ANSWER_PASS
        assert identity.evaluator_findings == ()          # no page-marker NOTE
        admin = results[fa.CHECKLIST_ITEM_ENGINEERING_ADMIN]
        assert admin.answer == fa.ANSWER_PASS
        assert admin.evaluator_findings == ()             # no STATUS-TEST NOTE
        assert result.drawings[0].findings == ()
        assert result.summary.findings_total == 0
        assert result.summary.fabrication_blockers == 0
        assert result.summary.ambiguity_findings == 0
        assert result.summary.total_drawings == 1
        assert result.summary.accepted_drawings == 1


# =============================================================================
# Items 18-21: truthful omissions — absent information stays absent, flagged,
# never manufactured.
# =============================================================================
class TestHonestOmissions:
    # 18 — absent material: the real Arkles drawings print MATERIAL NOT
    # SPECIFIED and 7AS reports the genuine finding. No grade is invented
    # (no material decision exists in the recorded resolutions).
    def test_absent_material_stays_not_specified_and_is_a_genuine_finding(
            self, arkles_package):
        package = arkles_package.package
        pdf_path = Path(package.manifest_path).parent / package.items[0].packaged_path
        lines = _pdf_lines(pdf_path)
        assert _label_value(lines, "MATERIAL") == "NOT SPECIFIED"
        result = fa.evaluate_fabricator_acceptance(
            package, acceptance_answers=_fa_answers(package))
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert result.summary.ambiguity_findings == 0
        for drawing in result.drawings:
            assert drawing.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
            assert len(drawing.findings) == 1
            finding = drawing.findings[0]
            assert finding.kind == fa.FINDING_MISSING_INFORMATION
            assert (finding.checklist_item
                    == fa.CHECKLIST_ITEM_FABRICATION_BLOCKING)
        decisions = dict(package.items[0].audit)["human_decisions"]
        assert all(d["task_type"] != TASK_PROVIDE_MATERIAL_SPECIFICATION
                   for d in decisions)

    # 19 — absent location: the engine REVIEWs at 7J_LOCATION ("a missing
    # coordinate is never defaulted to zero"), and a drawing with no reviewed
    # connection record prints no CONNECTION LOCATION at all.
    @needs_selby_capture
    def test_absent_location_is_flagged_and_never_manufactured(self, tmp_path):
        answers = {k: v for k, v in JOURNEY_ANSWERS_BY_TASK_TYPE.items()
                   if k != TASK_PROVIDE_LOCATION}
        record, rerun = _negative_case(
            "7aw-no-location", _view_b_b_entry(_page5_capture()), answers,
            tmp_path=tmp_path)
        _assert_review_with(
            record, rerun, PIPELINE_STAGE_LOCATION,
            "A missing coordinate is never defaulted to zero",
            {AUTOMATION_BLOCKER_LOCATION, AUTOMATION_BLOCKER_SPECIFICATION,
             AUTOMATION_BLOCKER_VALIDATION})
        assert rerun.decision == AUTOMATION_DECISION_REVIEW
        assert "CONNECTION LOCATION" not in _single_member_pdf_lines(tmp_path)

    # 20 — absent attachments: the engine REVIEWs at 7N_ATTACHMENT, and a
    # drawing with no reviewed attachment record prints no ATTACH semantic.
    @needs_selby_capture
    def test_absent_attachments_are_flagged_and_never_manufactured(
            self, tmp_path):
        answers = {k: v for k, v in JOURNEY_ANSWERS_BY_TASK_TYPE.items()
                   if k != TASK_SELECT_ATTACHMENT}
        record, rerun = _negative_case(
            "7aw-no-attachments", _view_b_b_entry(_page5_capture()), answers,
            tmp_path=tmp_path)
        _assert_review_with(
            record, rerun, PIPELINE_STAGE_ATTACHMENTS,
            "no attachment information is recorded",
            {AUTOMATION_BLOCKER_ATTACHMENT, AUTOMATION_BLOCKER_SPECIFICATION,
             AUTOMATION_BLOCKER_VALIDATION})
        assert "ATTACH" not in _single_member_pdf_lines(tmp_path)

    # 21 — no invented engineering meaning anywhere on the real drawing: no
    # coordinate semantics the record does not establish, no approval claim,
    # no raw-extraction grade, no un-reviewed weld/connection-type wording.
    @needs_selby_capture
    def test_no_invented_engineering_meaning_anywhere_on_the_real_drawing(
            self, communicated_drawing):
        text = communicated_drawing.text
        for invented in ("FROM BASE", "FROM TOP", "APPROVED",
                         "FOR FABRICATION", "300PLUS", "WELD", "WELDED",
                         "BOLTED"):
            assert invented not in text, invented
        # Every printed semantic equals the reviewed record's value.
        reviewed = JOURNEY_ANSWERS_BY_TASK_TYPE[TASK_PROVIDE_LOCATION]
        location = communicated_drawing.assembly.location
        assert (location.x, location.y, location.z,
                location.rotation_x, location.rotation_y, location.rotation_z) == (
            reviewed["x"], reviewed["y"], reviewed["z"],
            reviewed["rotation_x"], reviewed["rotation_y"], reviewed["rotation_z"])
        assert {a.surface_reference
                for a in communicated_drawing.assembly.attachments} == {"END"}


# =============================================================================
# Items 22-24: regressions — the earlier real-world proofs stay green.
# =============================================================================
@needs_selby_capture
class TestRegressions:
    # 22 — the two-member 7AU drawing stays valid and 7AS stays green (the
    # two-member title block gained the same communication fields).
    def test_two_member_drawing_stays_valid_and_7as_stays_green(
            self, au_drawing):
        lines = au_drawing.lines
        assert _label_value(lines, "MATERIAL") == "300"
        assert "CONNECTION LOCATION" in lines
        assert "ATTACH" in lines
        assert "PAGE" in lines and "1 OF 1" in lines
        assert "STATUS" not in lines
        assert au_drawing.acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        assert au_drawing.result.status == ACCEPTANCE_STATUS_ACCEPTED
        assert au_drawing.result.summary.findings_total == 0
        assert au_drawing.result.drawings[0].findings == ()

    # 23 — the 7AU material proof stays green: AI_EXTRACTED "300" confirmed
    # HUMAN_REVIEWED (value unchanged) travels the chain onto the drawing.
    def test_7au_material_proof_stays_green(self, au_drawing):
        conn = next(c for c in au_drawing.acceptance.connections)
        assert conn.trace.contract.ai_material == "300"
        provenance = {e.field: e.provenance
                      for e in conn.trace.contract.provenance}
        assert provenance["material"] == PROVENANCE_HUMAN_REVIEWED
        assert "300PLUS" not in "\n".join(au_drawing.lines).upper()

    # 24 — the 7AV multi-member proof stays green and the improved drawing
    # repeats byte-identically modulo ReportLab's per-save metadata (§18).
    def test_7av_proof_stays_green_and_the_drawing_repeats(
            self, communicated_drawing, tmp_path):
        repeat = _page5_journey(tmp_path / "repeat")
        record = repeat.workflow.connection_records[0]
        assert record.rerun_outcome.decision == AUTOMATION_DECISION_AUTO
        assert record.gate_result.decision == AUTOMATION_DECISION_AUTO
        assert [m.mark for m in record.pipeline.reviewed_assembly.members] == [
            "005", "PL018", "PL032", "PL025"]
        repeat_pdf = Path(repeat.workflow.generated_files[0])
        assert repeat_pdf.name == communicated_drawing.pdf_path.name
        assert _pdf_lines(repeat_pdf) == communicated_drawing.lines
        assert (_strip_per_save_metadata(repeat_pdf.read_bytes())
                == _strip_per_save_metadata(
                    communicated_drawing.pdf_path.read_bytes()))
