"""Milestone 7AS — Fabricator Acceptance Test / Real-World Deliverable
Validation.

The primary proof is the GENUINE Arkles production workflow: the real
extraction -> review -> resolution -> rerun -> gates -> drawings ->
verification -> acceptance -> fabrication package chain, evaluated
through `evaluate_fabricator_acceptance` against the actual packaged
PDFs and the reviewer's explicit checklist answers. Every other fixture
is clearly synthetic and secondary.

The expected honest real-world outcome: the genuine package evaluation
is NOT_ACCEPTED — the drawing records MATERIAL NOT SPECIFIED and no
material grade exists anywhere in the delivered record, so the fixture
reviewer FAILs the fabrication-blocking question. The deterministic
evaluator notes (location recorded but not printed on the drawing, no
page marker, STATUS TEST) remain visible but never substitute for the
reviewer's judgment.
"""

import ast
import dataclasses
import hashlib
import inspect
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from app.cad_engine import fabricator_acceptance as fa
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_BLOCKED,
    PACKAGE_STATUS_READY,
    PACKAGE_STATUS_REFUSED,
    FabricationDrawingPackage,
    FabricationPackageItem,
    FabricationPackageSummary,
    build_fabrication_package,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
    accept_production_job,
)
from tests.test_project_workflow import (
    CONNECTION_IDENTITIES,
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _pdf_text,
    _sha256,
)

MODULE_PATH = Path("app/cad_engine/fabricator_acceptance.py")
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")

MATERIAL_FINDING = (
    "MATERIAL NOT SPECIFIED: the drawing and the package record carry no material "
    "grade; a fabricator cannot order or weld plate without assuming one."
)

# ---------------------------------------------------------------------------
# Fixtures and helpers.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def genuine(tmp_path_factory):
    """The real Arkles journey: four workflow states (rev 0 -> rev 3), the
    early packages (not READY — unresolved connections) and the final READY
    package with the actual packaged PDFs."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7as"))
    states = _full_sequence(tmp)
    early = [
        build_fabrication_package(accept_production_job(state), output_dir=tmp / f"early-{index}")
        for index, state in enumerate(states[:-1])
    ]
    package = build_fabrication_package(
        accept_production_job(states[-1]), output_dir=tmp / "pkg")
    return SimpleNamespace(tmp=tmp, states=states, early=early, package=package)


def _answers(pkg, *, q12=fa.ANSWER_FAIL, q12_finding=MATERIAL_FINDING,
             overrides=None, findings=None, package_answer=None):
    """The fixture reviewer's answers: PASS on everything except the
    fabrication-blocking question, which FAILs with the material finding —
    unless overridden per checklist item."""
    overrides = overrides or {}
    findings = findings or {}
    blocks = []
    for item in pkg.items:
        answers = []
        for code in fa.CHECKLIST_ITEMS:
            if code == fa.CHECKLIST_ITEM_PACKAGE_COMPLETE:
                continue
            if code in overrides:
                answer = overrides[code]
            elif code == fa.CHECKLIST_ITEM_FABRICATION_BLOCKING:
                answer = q12
            else:
                answer = fa.ANSWER_PASS
            finding = None
            if answer == fa.ANSWER_FAIL:
                finding = findings.get(code)
                if finding is None and code == fa.CHECKLIST_ITEM_FABRICATION_BLOCKING:
                    finding = q12_finding
            answers.append(fa.FabricatorChecklistAnswer(code, answer, finding))
        blocks.append(fa.FabricatorDrawingAnswers(item.drawing_number, tuple(answers)))
    pkg_answers = (package_answer if package_answer is not None
                   else fa.FabricatorChecklistAnswer(fa.CHECKLIST_ITEM_PACKAGE_COMPLETE,
                                                     fa.ANSWER_PASS))
    return fa.FabricatorAcceptanceAnswers(tuple(blocks), (pkg_answers,))


def _mutate_decisions(pkg, index, task_type, *, drop=False, answer=None):
    """Replaces one recorded human decision inside the package item's audit
    (a model-level tamper — the files on disk stay untouched)."""
    item = pkg.items[index]
    audit = dict(item.audit)
    decisions = [dict(decision) for decision in audit["human_decisions"]]
    if drop:
        decisions = [decision for decision in decisions
                     if decision["task_type"] != task_type]
    else:
        for decision in decisions:
            if decision["task_type"] == task_type:
                decision["answer"] = answer
    audit["human_decisions"] = decisions
    items = list(pkg.items)
    items[index] = dataclasses.replace(item, audit=tuple(audit.items()))
    return dataclasses.replace(pkg, items=tuple(items))


def _copied_package(package, tmp_path):
    """A byte-copy of the package directory with the frozen projection
    re-pointed at it — lets file-tamper tests mutate disk state without
    touching the shared genuine fixture."""
    source = Path(package.manifest_path).parent
    destination = Path(tmp_path) / "copy"
    shutil.copytree(source, destination)
    return dataclasses.replace(
        package, manifest_path=str(destination / "project-manifest.json"))


# ---------------------------------------------------------------------------
# The synthetic (secondary) fixture: a hand-built READY package whose PDF
# carries a complete title block, including material and page markers.
# ---------------------------------------------------------------------------
SYNTH_DEFAULT_LINES = (
    "SteelSpec — synthetic fabricator acceptance fixture (built by hand for 7AS tests)",
    "DRAWING NO.",
    "FAB-CONN-SYN-001",
    "REV",
    "A",
    "DATE",
    "2026-01-01",
    "PROJECT",
    "PROJ-SYN",
    "SOURCE DRAWING",
    "SYN-SOURCE",
    "MEMBER A",
    "MS1",
    "SECTION",
    "310UB40",
    "LENGTH",
    "4000 mm",
    "MEMBER B",
    "MS2",
    "SECTION",
    "250PFC",
    "LENGTH",
    "3000 mm",
    "PLATE: 180 × 250 × 12 mm",
    "HOLES: 4 × Ø22",
    "PATTERN: 90 H × 140 V",
    "MATERIAL",
    "300PLUS",
    "UNITS",
    "mm",
    "SCALE",
    "NTS",
    "STATUS",
    "ISSUED",
    "PAGE 1 OF 1",
    "LOCATION: MEMBER B START — z = 3994 mm",
    "CONNECTION DETAIL — CONN-SYN-001",
)


def _write_pdf(path, lines):
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    y = 780
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 14
    pdf.save()


def _synth_decisions(*, marks=("MS1", "MS2"), plate=(180, 250, 12),
                     holes=(4, 22.0, 90.0, 140.0), connection_id="CONN-SYN-001"):
    """The synthetic record's human decisions — the same shape as the real
    package audit (7AR's _item_audit), but with synthetic values only."""
    return [
        {"task_type": "COMPLETE_REVIEW", "task_id": "T-001", "blocker_codes": [],
         "answer": None, "applied": True},
        {"task_type": "SELECT_MEMBER_POSITION_ATTACHMENT", "task_id": "T-002",
         "blocker_codes": [], "applied": True,
         "answer": [[marks[0], marks[1]], "END",
                    [{"member_mark": marks[0], "surface_reference": "END"},
                     {"member_mark": marks[1], "surface_reference": "START"}]]},
        {"task_type": "PROVIDE_PLATE", "task_id": "T-003", "blocker_codes": [],
         "applied": True,
         "answer": {"type": "end_plate", "thickness_mm": plate[2],
                    "width_mm": plate[0], "depth_mm": plate[1]}},
        {"task_type": "PROVIDE_HOLE_DIAMETER", "task_id": "T-004", "blocker_codes": [],
         "applied": True,
         "answer": {"quantity": holes[0], "diameter_mm": holes[1],
                    "horizontal_spacing_mm": holes[2], "vertical_spacing_mm": holes[3]}},
        {"task_type": "PROVIDE_LOCATION", "task_id": "T-005", "blocker_codes": [],
         "applied": True,
         "answer": {"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0,
                    "rotation_y": 0.0, "rotation_z": 0.0}},
        {"task_type": "PROVIDE_CONNECTION_IDENTITY", "task_id": "T-006",
         "blocker_codes": [], "applied": True, "answer": connection_id},
        {"task_type": "REVIEW_SPECIFICATION", "task_id": "T-007", "blocker_codes": [],
         "answer": None, "applied": True},
        {"task_type": "REVIEW_VALIDATION", "task_id": "T-008", "blocker_codes": [],
         "answer": None, "applied": True},
    ]


def _synthetic_package(tmp_path, *, lines=None, decisions=None,
                       project_id="PROJ-SYN", connection_id="CONN-SYN-001",
                       workflow_revision=9):
    out = Path(tmp_path) / "synth"
    drawings_dir = out / "drawings"
    drawings_dir.mkdir(parents=True)
    pdf = drawings_dir / "STEELSPEC-SYN-001.pdf"
    _write_pdf(pdf, SYNTH_DEFAULT_LINES if lines is None else lines)
    data = pdf.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if decisions is None:
        decisions = _synth_decisions(connection_id=connection_id)
    audit = (
        ("package_id", "RP-SYN-001"),
        ("acceptance", {"accepted": True, "reason": "synthetic fixture",
                        "decision": "ACCEPT", "output_status": "GENERATED",
                        "verification_status": "VERIFIED"}),
        ("drawing_evidence", {"source_drawing_id": "SYN-SOURCE",
                              "drawing_number": "SYN-DWG-001", "source_page": 9,
                              "detail_reference": "D-1", "grid_reference": "G-1"}),
        ("ai_observations", {"note": "synthetic", "member_references": ["MS1", "MS2"],
                             "bolt_readings": [], "plate_readings": [], "weld_readings": [],
                             "malformed_readings": [], "unrecognised_readings": [],
                             "connection_type": None, "confidence": None}),
        ("field_provenance", [["member_a", "HUMAN_SUPPLEMENTED"],
                              ["member_b", "HUMAN_SUPPLEMENTED"]]),
        ("human_decisions", decisions),
        ("verification", {"status": "VERIFIED", "recorded_sha256": sha, "page_count": 1,
                          "checks": [["GEOMETRY_FIELDS_VERIFIABLE", "PASSED"]]}),
    )
    item = FabricationPackageItem(
        drawing_number="STEELSPEC-SYN-001", connection_id=connection_id,
        package_id="RP-SYN-001", filename="STEELSPEC-SYN-001.pdf",
        source_artifact=str(pdf), source_filename="STEELSPEC-SYN-001.pdf",
        artifact_sha256=sha, recorded_sha256=sha, page_count=1,
        verification_status="VERIFIED", dispatch_output_status="GENERATED",
        checks=(), observed=(), audit=audit,
        packaged_path="drawings/STEELSPEC-SYN-001.pdf",
    )
    manifest = (
        ("package_schema", "steelspec-fabrication-package-1"),
        ("scope_statement",
         "synthetic fixture built by hand for the 7AS acceptance tests — never "
         "produced by the production workflow"),
        ("project", {"project_id": project_id, "source_drawing_id": "SYN-SOURCE",
                     "workflow_revision": workflow_revision,
                     "acceptance_status": "ACCEPTED"}),
        ("drawings", [{"drawing_number": "STEELSPEC-SYN-001",
                       "connection_id": connection_id,
                       "filename": "STEELSPEC-SYN-001.pdf"}]),
        ("summary", {"final_package_status": "READY", "total_drawing_artifacts": 1}),
        ("issues", []),
    )
    package = FabricationDrawingPackage(
        status=PACKAGE_STATUS_READY, reason="synthetic fixture",
        project_id=project_id, source_drawing_id="SYN-SOURCE",
        workflow_revision=workflow_revision, acceptance_status=ACCEPTANCE_STATUS_ACCEPTED,
        caller_revision=None, items=(item,),
        manifest_path=str(out / "project-manifest.json"),
        summary=FabricationPackageSummary(
            total_accepted_connections=1, total_drawing_artifacts=1,
            verified_artifacts=1, missing_artifacts=0, failed_artifacts=0,
            package_issues=0, final_package_status=PACKAGE_STATUS_READY),
        issues=(), manifest=manifest,
    )
    (out / "project-manifest.json").write_text(
        json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return package


def _synth_answers(*, overrides=None, findings=None, package_answer=None):
    overrides = overrides or {}
    findings = findings or {}
    answers = []
    for code in fa.CHECKLIST_ITEMS:
        if code == fa.CHECKLIST_ITEM_PACKAGE_COMPLETE:
            continue
        answer = overrides.get(code, fa.ANSWER_PASS)
        finding = findings.get(code) if answer == fa.ANSWER_FAIL else None
        answers.append(fa.FabricatorChecklistAnswer(code, answer, finding))
    block = fa.FabricatorDrawingAnswers("STEELSPEC-SYN-001", tuple(answers))
    pkg_answers = (package_answer if package_answer is not None
                   else fa.FabricatorChecklistAnswer(fa.CHECKLIST_ITEM_PACKAGE_COMPLETE,
                                                     fa.ANSWER_PASS))
    return fa.FabricatorAcceptanceAnswers((block,), (pkg_answers,))


def _evaluate(pkg, answers):
    return fa.evaluate_fabricator_acceptance(pkg, acceptance_answers=answers)


# ---------------------------------------------------------------------------
# 1. Request validity.
# ---------------------------------------------------------------------------
class TestRequestValidity:
    def test_wrong_package_type_raises_type_error(self, genuine):
        with pytest.raises(TypeError, match="FabricationDrawingPackage"):
            _evaluate("not a package", _answers(genuine.package))

    def test_wrong_answers_type_raises_type_error(self, genuine):
        with pytest.raises(TypeError, match="FabricatorAcceptanceAnswers"):
            _evaluate(genuine.package, "not answers")

    def test_expected_revision_type_guards(self, genuine):
        answers = _answers(genuine.package)
        with pytest.raises(TypeError):
            fa.evaluate_fabricator_acceptance(
                genuine.package, acceptance_answers=answers, expected_revision="3")
        with pytest.raises(TypeError):
            fa.evaluate_fabricator_acceptance(
                genuine.package, acceptance_answers=answers, expected_revision=3.5)

    def test_no_verdict_injection_parameters(self):
        signature = inspect.signature(fa.evaluate_fabricator_acceptance)
        assert set(signature.parameters) == {
            "fabrication_package", "acceptance_answers", "expected_revision"}
        for name in signature.parameters:
            assert name not in ("force", "force_accept", "status", "approved",
                                "fabrication_ready", "override")
        assert "force_accept" not in MODULE_SOURCE
        assert "approved=" not in MODULE_SOURCE
        assert "fabrication_ready" not in MODULE_SOURCE


# ---------------------------------------------------------------------------
# 2. The genuine Arkles journey (§16, §27): unresolved -> resolved.
# ---------------------------------------------------------------------------
class TestRealArklesJourney:
    def test_unresolved_journey_is_refused(self, genuine):
        assert len(genuine.states) == 4
        assert len(genuine.early) == 3
        for package in genuine.early:
            assert package.status != PACKAGE_STATUS_READY
            result = _evaluate(package, _answers(package))
            assert result.status == ACCEPTANCE_STATUS_REFUSED
            assert "not READY" in result.reason
            assert result.drawings == ()

    def test_genuine_answers_produce_not_accepted(self, genuine):
        result = _evaluate(genuine.package, _answers(genuine.package))
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert result.project_id == PROJECT_ID
        assert result.package_status == PACKAGE_STATUS_READY
        assert result.workflow_revision == 3
        assert result.caller_revision is None
        assert len(result.drawings) == 3
        assert [drawing.filename for drawing in result.drawings] == [
            "STEELSPEC-001.pdf", "STEELSPEC-002.pdf", "STEELSPEC-003.pdf"]
        assert [drawing.connection_id for drawing in result.drawings] == list(
            CONNECTION_IDENTITIES.values())
        assert all(drawing.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
                   for drawing in result.drawings)
        # MILESTONE 7AW: the drawings now print their reviewed
        # location/attachment semantics and PAGE 1 OF 1, and no STATUS cell —
        # so the former ambiguity/note findings are genuinely gone. What
        # remains is the honest material gap: one evaluator MISSING
        # INFORMATION plus the reviewer's own FAIL finding per drawing.
        assert result.summary == fa.FabricatorAcceptanceSummary(
            total_drawings=3, accepted_drawings=0, not_accepted_drawings=3,
            findings_total=6, fabrication_blockers=3, ambiguity_findings=0,
            final_status=ACCEPTANCE_STATUS_NOT_ACCEPTED)
        assert fa.CHECKLIST_ITEM_FABRICATION_BLOCKING in result.reason
        assert "MATERIAL" in result.reason
        assert result.refusal_reasons == ()
        assert result.package_item_results[0].answer == fa.ANSWER_PASS

    def test_per_drawing_evidence_is_honest(self, genuine):
        result = _evaluate(genuine.package, _answers(genuine.package))
        present_items = (
            fa.CHECKLIST_ITEM_PROJECT_IDENTIFIED, fa.CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
            fa.CHECKLIST_ITEM_MEMBER_A_IDENTIFIED, fa.CHECKLIST_ITEM_MEMBER_B_IDENTIFIED,
            fa.CHECKLIST_ITEM_CONNECTION_LOCATION, fa.CHECKLIST_ITEM_PLATE_INFORMATION,
            fa.CHECKLIST_ITEM_HOLE_INFORMATION, fa.CHECKLIST_ITEM_UNITS_CLEAR,
            fa.CHECKLIST_ITEM_DRAWING_IDENTITY, fa.CHECKLIST_ITEM_AMBIGUITY,
            fa.CHECKLIST_ITEM_VISUAL_CONTRADICTION, fa.CHECKLIST_ITEM_FABRICATION_BLOCKING,
            fa.CHECKLIST_ITEM_ENGINEERING_ADMIN, fa.CHECKLIST_ITEM_TRACEABILITY,
        )
        for drawing in result.drawings:
            by_code = {item.checklist_item: item for item in drawing.results}
            for code in present_items:
                assert by_code[code].evidence_status == fa.EVIDENCE_PRESENT, code
            # MILESTONE 7AW: the real drawings now print their reviewed
            # ATTACH semantics in their own text, so the connection location
            # evidence is visible IN the drawing — the evaluator reads the
            # artifact itself and says so
            assert by_code[fa.CHECKLIST_ITEM_CONNECTION_LOCATION].visible_in_drawing is True
            assert by_code[fa.CHECKLIST_ITEM_PLATE_INFORMATION].visible_in_drawing is True
            assert by_code[fa.CHECKLIST_ITEM_HOLE_INFORMATION].visible_in_drawing is True
            q12 = by_code[fa.CHECKLIST_ITEM_FABRICATION_BLOCKING]
            assert q12.answer == fa.ANSWER_FAIL
            assert q12.finding == MATERIAL_FINDING
            assert [finding.kind for finding in q12.evaluator_findings] == [
                fa.FINDING_MISSING_INFORMATION]

    def test_deterministic_findings_once_per_source_item(self, genuine):
        result = _evaluate(genuine.package, _answers(genuine.package))
        # MILESTONE 7AW: with location/attachment semantics and PAGE 1 OF 1
        # now drawn, and no STATUS cell drawn, the only evaluator finding
        # left is the material gap — once per drawing, on the item that
        # derived it.
        expected = [
            (fa.FINDING_MISSING_INFORMATION, fa.CHECKLIST_ITEM_FABRICATION_BLOCKING),
        ]
        for drawing in result.drawings:
            assert [(finding.kind, finding.checklist_item) for finding in drawing.findings] \
                == expected
        drawing = result.drawings[0]
        location = next(item for item in drawing.results
                        if item.checklist_item == fa.CHECKLIST_ITEM_CONNECTION_LOCATION)
        assert location.visible_in_drawing is True
        assert location.evaluator_findings == ()
        identity = next(item for item in drawing.results
                        if item.checklist_item == fa.CHECKLIST_ITEM_DRAWING_IDENTITY)
        assert identity.evaluator_findings == ()
        admin = next(item for item in drawing.results
                     if item.checklist_item == fa.CHECKLIST_ITEM_ENGINEERING_ADMIN)
        assert admin.evaluator_findings == ()

    def test_provenance_and_ai_values_pass_through_untouched(self, genuine):
        result = _evaluate(genuine.package, _answers(genuine.package))
        audit = dict(genuine.package.items[0].audit)
        ai = audit["ai_observations"]
        # the real AI reading carries the M12 bolt size — verbatim
        assert any("M12" in str(reading) for reading in ai["bolt_readings"])
        # the evaluation never restates AI values and never converts: the
        # drawn Ø22 is the RECORDED hole diameter 22.0, not a conversion
        report_and_findings = fa.fabricator_acceptance_report(result) + " ".join(
            finding.detail for drawing in result.drawings for finding in drawing.findings)
        assert "M12" not in report_and_findings
        assert "Ø12" not in report_and_findings
        hole_decision = next(decision for decision in audit["human_decisions"]
                             if decision["task_type"] == "PROVIDE_HOLE_DIAMETER")
        assert hole_decision["answer"]["diameter_mm"] == 22.0
        assert "Ø22" in _pdf_text(Path(genuine.package.items[0].source_artifact))
        # provenance labels stay the existing vocabulary
        assert all(entry[1] in ("AI_EXTRACTED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED")
                   for entry in audit["field_provenance"])
        trace = dict(result.drawings[0].trace)
        labels = {label.strip() for label in trace["provenance"].split(",")}
        assert labels and labels <= {
            "AI_EXTRACTED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED"}
        # this real slice was fully human-supplemented — the trace says so
        # verbatim instead of inventing a richer provenance
        assert labels == {"HUMAN_SUPPLEMENTED"}

    def test_trace_references_existing_identifiers_only(self, genuine):
        result = _evaluate(genuine.package, _answers(genuine.package))
        trace = dict(result.drawings[0].trace)
        assert set(trace) == {
            "drawing", "packaged_connection", "source_artifact", "verification_7ag",
            "dispatch_7af", "fabrication_gate_7ae", "rerun_7ad", "tasks_7ac",
            "provenance", "ai_observations", "source_drawing"}
        assert SOURCE_DRAWING_ID in trace["source_drawing"]
        assert "VERIFIED" in trace["verification_7ag"]
        assert "GENERATED" in trace["dispatch_7af"]
        assert trace["source_artifact"].endswith(".pdf")
        assert trace["rerun_7ad"].startswith("8 recorded human resolution(s)")
        assert trace["ai_observations"] == "preserved verbatim in the package audit"

    def test_report_lines_are_plain_and_factual(self, genuine):
        result = _evaluate(genuine.package, _answers(genuine.package))
        report = fa.fabricator_acceptance_report(result)
        for number in ("001", "002", "003"):
            assert f"Connection: CONN-ARKLES-{number}" in report
            assert f"Drawing: STEELSPEC-{number}.pdf" in report
        assert "NO_FABRICATION_BLOCKING_INFORMATION: FAIL (evidence: PRESENT)" in report
        assert "Reviewer finding:" in report
        assert "AMBIGUITY:" in report
        assert "Result: NOT_ACCEPTED" in report
        assert not re.search(r"\b20\d\d\b", report)


# ---------------------------------------------------------------------------
# 3. Repeatability (§23): the frozen result is byte-for-byte the same.
# ---------------------------------------------------------------------------
class TestRepeatability:
    def test_identical_evaluation_results(self, genuine):
        answers = _answers(genuine.package)
        first = _evaluate(genuine.package, answers)
        second = _evaluate(genuine.package, answers)
        assert first == second

    def test_report_is_deterministic(self, genuine):
        answers = _answers(genuine.package)
        first = fa.fabricator_acceptance_report(_evaluate(genuine.package, answers))
        second = fa.fabricator_acceptance_report(_evaluate(genuine.package, answers))
        assert first == second
        assert not re.search(r"\b20\d\d\b", first)


# ---------------------------------------------------------------------------
# 4. Malformed acceptance answers are REFUSED, never partially accepted.
# ---------------------------------------------------------------------------
class TestAnswersValidation:
    def _replace_answer(self, answers, drawing_index, item_code, **changes):
        blocks = list(answers.drawing_answers)
        block = blocks[drawing_index]
        replaced = []
        for answer in block.answers:
            replaced.append(dataclasses.replace(answer, **changes)
                            if answer.checklist_item == item_code else answer)
        blocks[drawing_index] = fa.FabricatorDrawingAnswers(
            block.drawing_number, tuple(replaced))
        return fa.FabricatorAcceptanceAnswers(tuple(blocks), answers.package_answers)

    def test_unknown_checklist_item_is_refused(self, genuine):
        answers = self._replace_answer(
            _answers(genuine.package), 0, fa.CHECKLIST_ITEM_UNITS_CLEAR,
            checklist_item="MADE_UP_ITEM")
        result = _evaluate(genuine.package, answers)
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "unknown checklist item 'MADE_UP_ITEM'" in result.refusal_reasons[0]

    def test_duplicate_checklist_item_is_refused(self, genuine):
        base = _answers(genuine.package)
        block = base.drawing_answers[0]
        extra = fa.FabricatorChecklistAnswer(
            fa.CHECKLIST_ITEM_UNITS_CLEAR, fa.ANSWER_PASS)
        blocks = list(base.drawing_answers)
        blocks[0] = fa.FabricatorDrawingAnswers(
            block.drawing_number, block.answers + (extra,))
        result = _evaluate(
            genuine.package,
            fa.FabricatorAcceptanceAnswers(tuple(blocks), base.package_answers))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "duplicate answer" in result.reason

    def test_unknown_drawing_is_refused(self, genuine):
        base = _answers(genuine.package)
        blocks = list(base.drawing_answers)
        blocks.append(fa.FabricatorDrawingAnswers("STEELSPEC-999", blocks[0].answers))
        result = _evaluate(
            genuine.package,
            fa.FabricatorAcceptanceAnswers(tuple(blocks), base.package_answers))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "unknown drawing STEELSPEC-999" in result.reason

    def test_duplicate_drawing_block_is_refused(self, genuine):
        base = _answers(genuine.package)
        blocks = list(base.drawing_answers)
        blocks.append(base.drawing_answers[0])
        result = _evaluate(
            genuine.package,
            fa.FabricatorAcceptanceAnswers(tuple(blocks), base.package_answers))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "duplicate acceptance answers" in result.reason

    def test_missing_checklist_item_is_refused(self, genuine):
        base = _answers(genuine.package)
        blocks = list(base.drawing_answers)
        blocks[0] = fa.FabricatorDrawingAnswers(
            base.drawing_answers[0].drawing_number, base.drawing_answers[0].answers[1:])
        result = _evaluate(
            genuine.package,
            fa.FabricatorAcceptanceAnswers(tuple(blocks), base.package_answers))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "missing acceptance answers" in result.reason

    def test_missing_drawing_answers_are_refused(self, genuine):
        base = _answers(genuine.package)
        result = _evaluate(
            genuine.package,
            fa.FabricatorAcceptanceAnswers(base.drawing_answers[:2], base.package_answers))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "missing acceptance answers for drawing" in result.reason

    def test_invalid_answer_value_is_refused(self, genuine):
        answers = self._replace_answer(
            _answers(genuine.package), 0, fa.CHECKLIST_ITEM_UNITS_CLEAR, answer="MAYBE")
        result = _evaluate(genuine.package, answers)
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "invalid answer 'MAYBE'" in result.reason

    def test_fail_without_finding_is_refused(self, genuine):
        answers = self._replace_answer(
            _answers(genuine.package), 0, fa.CHECKLIST_ITEM_UNITS_CLEAR,
            answer=fa.ANSWER_FAIL, finding=None)
        result = _evaluate(genuine.package, answers)
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "FAIL without a finding" in result.reason

    def test_malformed_nested_objects_are_refused(self, genuine):
        base = _answers(genuine.package)
        blocks = list(base.drawing_answers)
        blocks[0] = fa.FabricatorDrawingAnswers(
            base.drawing_answers[0].drawing_number,
            base.drawing_answers[0].answers[:-1] + (("UNITS_CLEAR", "PASS"),))
        result = _evaluate(
            genuine.package,
            fa.FabricatorAcceptanceAnswers(tuple(blocks), base.package_answers))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "malformed acceptance answers" in result.reason

    def test_package_level_answer_validation_is_refused(self, genuine):
        base = _answers(genuine.package)
        result = _evaluate(
            genuine.package, fa.FabricatorAcceptanceAnswers(base.drawing_answers, ()))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "missing package-level acceptance answers" in result.reason

        result = _evaluate(genuine.package, fa.FabricatorAcceptanceAnswers(
            base.drawing_answers,
            (fa.FabricatorChecklistAnswer("NOT_A_THING", fa.ANSWER_PASS),)))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "unknown package-level checklist item" in result.reason

        result = _evaluate(genuine.package, fa.FabricatorAcceptanceAnswers(
            base.drawing_answers,
            (fa.FabricatorChecklistAnswer(fa.CHECKLIST_ITEM_PACKAGE_COMPLETE,
                                          fa.ANSWER_FAIL, None),)))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "FAIL without a finding" in result.reason


# ---------------------------------------------------------------------------
# 5. Evidence negatives (§21): the reconciliation matrix refuses a PASS or
# NOT_APPLICABLE that contradicts the deliverable's evidence.
# ---------------------------------------------------------------------------
class TestEvidenceNegatives:
    def test_pass_over_missing_attachment_is_refused(self, genuine):
        missing = _mutate_decisions(
            genuine.package, 0, "SELECT_MEMBER_POSITION_ATTACHMENT", drop=True)
        result = _evaluate(missing, _answers(missing))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert fa.CHECKLIST_ITEM_CONNECTION_LOCATION in result.refusal_reasons[0]
        assert "missing" in result.refusal_reasons[0]

    def test_not_applicable_over_missing_evidence_is_allowed(self, genuine):
        missing = genuine.package
        for index in range(len(genuine.package.items)):
            missing = _mutate_decisions(
                missing, index, "SELECT_MEMBER_POSITION_ATTACHMENT", drop=True)
        answers = _answers(missing, overrides={
            fa.CHECKLIST_ITEM_CONNECTION_LOCATION: fa.ANSWER_NOT_APPLICABLE})
        result = _evaluate(missing, answers)
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED  # Q12 still FAILs
        drawing = result.drawings[0]
        location = next(item for item in drawing.results
                        if item.checklist_item == fa.CHECKLIST_ITEM_CONNECTION_LOCATION)
        assert location.answer == fa.ANSWER_NOT_APPLICABLE
        assert location.evidence_status == fa.EVIDENCE_MISSING

    def test_tampered_hole_diameter_contradiction(self, genuine):
        contradicted = _mutate_decisions(
            genuine.package, 0, "PROVIDE_HOLE_DIAMETER",
            answer={"quantity": 4, "diameter_mm": 20.0,
                    "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0})
        result = _evaluate(contradicted, _answers(contradicted))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert fa.CHECKLIST_ITEM_HOLE_INFORMATION in result.reason
        assert "contradicted" in result.reason

        answers = _answers(contradicted, overrides={
            fa.CHECKLIST_ITEM_HOLE_INFORMATION: fa.ANSWER_FAIL,
            fa.CHECKLIST_ITEM_AMBIGUITY: fa.ANSWER_FAIL,
            fa.CHECKLIST_ITEM_VISUAL_CONTRADICTION: fa.ANSWER_FAIL},
            findings={fa.CHECKLIST_ITEM_HOLE_INFORMATION:
                      "the recorded hole diameter contradicts the drawing",
                      fa.CHECKLIST_ITEM_AMBIGUITY:
                      "contradiction must be resolved before fabrication",
                      fa.CHECKLIST_ITEM_VISUAL_CONTRADICTION:
                      "hole diameter contradicts the record"})
        result = _evaluate(contradicted, answers)
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        drawing = result.drawings[0]
        by_code = {item.checklist_item: item for item in drawing.results}
        holes = by_code[fa.CHECKLIST_ITEM_HOLE_INFORMATION]
        assert holes.evidence_status == fa.EVIDENCE_CONTRADICTED
        assert [finding.kind for finding in holes.evaluator_findings] == [
            fa.FINDING_CONTRADICTION]
        assert by_code[fa.CHECKLIST_ITEM_VISUAL_CONTRADICTION].evidence_status == \
            fa.EVIDENCE_CONTRADICTED

    def test_tampered_member_mark_contradiction(self, genuine):
        contradicted = _mutate_decisions(
            genuine.package, 0, "SELECT_MEMBER_POSITION_ATTACHMENT",
            answer=[["LX", "L3"], "END",
                    [{"member_mark": "LX", "surface_reference": "END"},
                     {"member_mark": "L3", "surface_reference": "START"}]])
        answers = _answers(contradicted, overrides={
            fa.CHECKLIST_ITEM_MEMBER_A_IDENTIFIED: fa.ANSWER_FAIL,
            fa.CHECKLIST_ITEM_AMBIGUITY: fa.ANSWER_FAIL,
            fa.CHECKLIST_ITEM_VISUAL_CONTRADICTION: fa.ANSWER_FAIL},
            findings={fa.CHECKLIST_ITEM_MEMBER_A_IDENTIFIED:
                      "the drawing's member mark contradicts the record",
                      fa.CHECKLIST_ITEM_AMBIGUITY: "member contradiction unresolved",
                      fa.CHECKLIST_ITEM_VISUAL_CONTRADICTION:
                      "member mark contradicts the record"})
        result = _evaluate(contradicted, answers)
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        member_a = next(item for item in result.drawings[0].results
                        if item.checklist_item == fa.CHECKLIST_ITEM_MEMBER_A_IDENTIFIED)
        assert member_a.evidence_status == fa.EVIDENCE_CONTRADICTED
        assert member_a.evaluator_findings[0].kind == fa.FINDING_CONTRADICTION

    def test_tampered_plate_depth_contradiction_is_refused(self, genuine):
        contradicted = _mutate_decisions(
            genuine.package, 0, "PROVIDE_PLATE",
            answer={"type": "end_plate", "thickness_mm": 12, "width_mm": 180,
                    "depth_mm": 240})
        result = _evaluate(contradicted, _answers(contradicted))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert fa.CHECKLIST_ITEM_PLATE_INFORMATION in result.reason

    def test_pass_over_contradicted_project_is_refused(self, genuine):
        contradicted = dataclasses.replace(genuine.package, project_id="PROJ-OTHER")
        result = _evaluate(contradicted, _answers(contradicted))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert fa.CHECKLIST_ITEM_PROJECT_IDENTIFIED in result.reason

    def test_not_applicable_over_present_evidence_is_refused(self, genuine):
        answers = _answers(genuine.package, overrides={
            fa.CHECKLIST_ITEM_PROJECT_IDENTIFIED: fa.ANSWER_NOT_APPLICABLE})
        result = _evaluate(genuine.package, answers)
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "NOT_APPLICABLE" in result.reason
        assert "present" in result.reason


# ---------------------------------------------------------------------------
# 6. Tampered deliverable files and incoherent package state are REFUSED.
# ---------------------------------------------------------------------------
class TestFileIntegrity:
    def test_tampered_manifest_is_refused(self, genuine, tmp_path):
        copied = _copied_package(genuine.package, tmp_path)
        Path(copied.manifest_path).write_text("tampered", encoding="utf-8")
        result = _evaluate(copied, _answers(copied))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "manifest" in result.reason
        assert "tampered" in result.reason

    def test_missing_manifest_is_refused(self, genuine, tmp_path):
        copied = _copied_package(genuine.package, tmp_path)
        Path(copied.manifest_path).unlink()
        result = _evaluate(copied, _answers(copied))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "manifest" in result.reason

    def test_missing_drawing_file_is_refused(self, genuine, tmp_path):
        copied = _copied_package(genuine.package, tmp_path)
        (Path(copied.manifest_path).parent / "drawings" / "STEELSPEC-001.pdf").unlink()
        result = _evaluate(copied, _answers(copied))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "STEELSPEC-001.pdf" in result.reason

    def test_replaced_drawing_bytes_are_refused(self, genuine, tmp_path):
        copied = _copied_package(genuine.package, tmp_path)
        drawing = Path(copied.manifest_path).parent / "drawings" / "STEELSPEC-001.pdf"
        drawing.write_bytes(b"not a pdf at all")
        result = _evaluate(copied, _answers(copied))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "artifact hash" in result.reason

    def test_substituted_drawing_is_refused(self, genuine, tmp_path):
        copied = _copied_package(genuine.package, tmp_path)
        drawings = Path(copied.manifest_path).parent / "drawings"
        (drawings / "STEELSPEC-001.pdf").write_bytes(
            (drawings / "STEELSPEC-002.pdf").read_bytes())
        result = _evaluate(copied, _answers(copied))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "artifact hash" in result.reason

    def test_duplicate_drawing_identity_is_refused(self, genuine):
        items = list(genuine.package.items)
        items[1] = dataclasses.replace(items[1], connection_id=items[0].connection_id)
        incoherent = dataclasses.replace(genuine.package, items=tuple(items))
        result = _evaluate(incoherent, _answers(incoherent))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "duplicate drawing identities" in result.reason

    def test_stale_caller_revision_is_refused(self, genuine):
        result = fa.evaluate_fabricator_acceptance(
            genuine.package, acceptance_answers=_answers(genuine.package),
            expected_revision=2)
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "revision" in result.reason
        assert result.caller_revision == 2
        assert result.workflow_revision == 3

    def test_unaccepted_job_package_is_refused(self, genuine):
        unaccepted = dataclasses.replace(
            genuine.package, acceptance_status=ACCEPTANCE_STATUS_NOT_ACCEPTED)
        result = _evaluate(unaccepted, _answers(unaccepted))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "accepted production job" in result.reason

    def test_blocked_package_is_refused(self, genuine):
        blocked = dataclasses.replace(
            genuine.package, status=PACKAGE_STATUS_BLOCKED, reason="fixture")
        result = _evaluate(blocked, _answers(blocked))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert "not READY" in result.reason


# ---------------------------------------------------------------------------
# 7. The synthetic ACCEPTED path (secondary fixture, §16): a package whose
# drawing genuinely answers every question is ACCEPTED.
# ---------------------------------------------------------------------------
class TestSyntheticHappyPath:
    def test_full_acceptance(self, tmp_path):
        package = _synthetic_package(tmp_path)
        result = _evaluate(package, _synth_answers())
        assert result.status == ACCEPTANCE_STATUS_ACCEPTED
        assert result.summary == fa.FabricatorAcceptanceSummary(
            total_drawings=1, accepted_drawings=1, not_accepted_drawings=0,
            findings_total=0, fabrication_blockers=0, ambiguity_findings=0,
            final_status=ACCEPTANCE_STATUS_ACCEPTED)
        drawing = result.drawings[0]
        assert drawing.status == ACCEPTANCE_STATUS_ACCEPTED
        assert drawing.findings == ()
        by_code = {item.checklist_item: item for item in drawing.results}
        assert all(item.answer == fa.ANSWER_PASS for item in drawing.results)
        assert by_code[fa.CHECKLIST_ITEM_CONNECTION_LOCATION].visible_in_drawing is True
        assert by_code[fa.CHECKLIST_ITEM_FABRICATION_BLOCKING].evidence_status == \
            fa.EVIDENCE_PRESENT
        assert result.package_item_results[0].answer == fa.ANSWER_PASS
        assert "complete" in result.reason

    def test_synthetic_report_is_accepted(self, tmp_path):
        package = _synthetic_package(tmp_path)
        result = _evaluate(package, _synth_answers())
        report = fa.fabricator_acceptance_report(result)
        assert "Drawing: STEELSPEC-SYN-001.pdf" in report
        assert "PROJECT_IDENTIFIED: PASS (evidence: PRESENT)" in report
        assert "PACKAGE_COMPLETE: PASS (evidence: PRESENT)" in report
        assert "Result: ACCEPTED" in report

    def test_single_member_connection_is_accepted(self, tmp_path):
        # no MEMBER B row on the drawing; the reviewer declares the
        # connection genuinely single-member with NOT_APPLICABLE
        lines = SYNTH_DEFAULT_LINES[:17] + SYNTH_DEFAULT_LINES[23:]
        package = _synthetic_package(tmp_path, lines=lines)
        answers = _synth_answers(overrides={
            fa.CHECKLIST_ITEM_MEMBER_B_IDENTIFIED: fa.ANSWER_NOT_APPLICABLE})
        result = _evaluate(package, answers)
        assert result.status == ACCEPTANCE_STATUS_ACCEPTED
        by_code = {item.checklist_item: item for item in result.drawings[0].results}
        member_b = by_code[fa.CHECKLIST_ITEM_MEMBER_B_IDENTIFIED]
        assert member_b.answer == fa.ANSWER_NOT_APPLICABLE
        assert member_b.evidence_status == fa.EVIDENCE_MISSING
        assert by_code[fa.CHECKLIST_ITEM_FABRICATION_BLOCKING].evidence_status == \
            fa.EVIDENCE_PRESENT

    def test_package_completeness_fail_is_not_accepted(self, tmp_path):
        package = _synthetic_package(tmp_path)
        answers = _synth_answers(package_answer=fa.FabricatorChecklistAnswer(
            fa.CHECKLIST_ITEM_PACKAGE_COMPLETE, fa.ANSWER_FAIL,
            "the drawing set omits the expected erection sheet"))
        result = _evaluate(package, answers)
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert fa.CHECKLIST_ITEM_PACKAGE_COMPLETE in result.reason
        assert result.drawings[0].status == ACCEPTANCE_STATUS_ACCEPTED


# ---------------------------------------------------------------------------
# 8. Synthetic negatives: a drawing that cannot answer a question is MISSING
# evidence — FAIL yields NOT_ACCEPTED with the finding, PASS is REFUSED.
# ---------------------------------------------------------------------------
class TestSyntheticNegatives:
    def test_unclear_units(self, tmp_path):
        lines = SYNTH_DEFAULT_LINES[:28] + SYNTH_DEFAULT_LINES[30:]
        package = _synthetic_package(tmp_path, lines=lines)
        result = _evaluate(package, _synth_answers(overrides={
            fa.CHECKLIST_ITEM_UNITS_CLEAR: fa.ANSWER_FAIL},
            findings={fa.CHECKLIST_ITEM_UNITS_CLEAR:
                      "the drawing carries no UNITS value"}))
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        units = next(item for item in result.drawings[0].results
                     if item.checklist_item == fa.CHECKLIST_ITEM_UNITS_CLEAR)
        assert units.evidence_status == fa.EVIDENCE_MISSING
        assert units.finding == "the drawing carries no UNITS value"

        result = _evaluate(package, _synth_answers())
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert fa.CHECKLIST_ITEM_UNITS_CLEAR in result.reason

    def test_missing_plate_information(self, tmp_path):
        lines = list(SYNTH_DEFAULT_LINES)
        lines.remove("PLATE: 180 × 250 × 12 mm")
        package = _synthetic_package(tmp_path, lines=lines)
        result = _evaluate(package, _synth_answers(overrides={
            fa.CHECKLIST_ITEM_PLATE_INFORMATION: fa.ANSWER_FAIL,
            fa.CHECKLIST_ITEM_FABRICATION_BLOCKING: fa.ANSWER_FAIL},
            findings={fa.CHECKLIST_ITEM_PLATE_INFORMATION:
                      "the plate dimensions are not shown",
                      fa.CHECKLIST_ITEM_FABRICATION_BLOCKING:
                      "the plate information is missing from the drawing"}))
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        plate = next(item for item in result.drawings[0].results
                     if item.checklist_item == fa.CHECKLIST_ITEM_PLATE_INFORMATION)
        assert plate.evidence_status == fa.EVIDENCE_MISSING

        result = _evaluate(package, _synth_answers())
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert any(fa.CHECKLIST_ITEM_PLATE_INFORMATION in reason
                   for reason in result.refusal_reasons)

    def test_missing_hole_information(self, tmp_path):
        lines = [line for line in SYNTH_DEFAULT_LINES
                 if not line.startswith(("HOLES:", "PATTERN:"))]
        package = _synthetic_package(tmp_path, lines=lines)
        result = _evaluate(package, _synth_answers())
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert any(fa.CHECKLIST_ITEM_HOLE_INFORMATION in reason
                   for reason in result.refusal_reasons)

    def test_missing_material_is_reported_not_invented(self, tmp_path):
        lines = list(SYNTH_DEFAULT_LINES)
        lines.remove("MATERIAL")
        lines.remove("300PLUS")
        package = _synthetic_package(tmp_path, lines=lines)
        result = _evaluate(package, _synth_answers(
            overrides={fa.CHECKLIST_ITEM_FABRICATION_BLOCKING: fa.ANSWER_FAIL},
            findings={fa.CHECKLIST_ITEM_FABRICATION_BLOCKING: MATERIAL_FINDING}))
        assert result.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        q12 = next(item for item in result.drawings[0].results
                   if item.checklist_item == fa.CHECKLIST_ITEM_FABRICATION_BLOCKING)
        assert q12.evidence_status == fa.EVIDENCE_PRESENT  # geometry is complete…
        assert q12.evaluator_findings[0].kind == fa.FINDING_MISSING_INFORMATION  # …material is not
        assert "MATERIAL NOT SPECIFIED" in q12.evaluator_findings[0].detail

    def test_wrong_drawing_number_is_missing_evidence(self, tmp_path):
        lines = list(SYNTH_DEFAULT_LINES)
        lines[2] = "FAB-999"
        package = _synthetic_package(tmp_path, lines=lines)
        result = _evaluate(package, _synth_answers())
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert fa.CHECKLIST_ITEM_DRAWING_IDENTITY in result.reason

    def test_no_connection_identity_tokens(self, tmp_path):
        lines = list(SYNTH_DEFAULT_LINES)
        lines[2] = "FAB-999"
        lines.remove("CONNECTION DETAIL — CONN-SYN-001")
        package = _synthetic_package(tmp_path, lines=lines)
        result = _evaluate(package, _synth_answers(overrides={
            fa.CHECKLIST_ITEM_DRAWING_IDENTITY: fa.ANSWER_FAIL},
            findings={fa.CHECKLIST_ITEM_DRAWING_IDENTITY: "no drawing number"}))
        assert result.status == ACCEPTANCE_STATUS_REFUSED
        assert any(fa.CHECKLIST_ITEM_CONNECTION_IDENTIFIED in reason
                   for reason in result.refusal_reasons)

    def test_refused_report_lists_the_reasons(self, tmp_path):
        package = _synthetic_package(tmp_path)
        result = _evaluate(package, _synth_answers(overrides={
            fa.CHECKLIST_ITEM_UNITS_CLEAR: fa.ANSWER_FAIL}))
        report = fa.fabricator_acceptance_report(result)
        assert "Result: REFUSED" in report
        assert "FAIL without a finding" in report


# ---------------------------------------------------------------------------
# 9. Purity: the module invents nothing, imports nothing extra, writes
# nothing, and carries no engineering literals.
# ---------------------------------------------------------------------------
class TestModulePurity:
    def test_no_engineering_literals_in_the_module(self):
        for forbidden in ("ARKLES", "RP-000", "PROJ-7AJ", "M12", "SQ4", "Ø12",
                          "310UB40", "250PFC", "SYSTEM_DERIVED", "300PLUS", "LX"):
            assert forbidden not in MODULE_SOURCE, forbidden
        assert not re.search(r"\b(180|250|12|22|4000|3000|7000|140|90|45|55|41|310|30|3994)\b",
                             MODULE_SOURCE)
        assert not re.search(r"\bL2\b|\bL3\b", MODULE_SOURCE)
        # the only conversion-shaped tokens allowed are the generator's own
        # documented rendering conventions — never applied to a bolt size
        assert "Ø12" not in MODULE_SOURCE

    def test_module_has_no_write_operations(self):
        for op in ("write_text", "write_bytes", "open(", ".mkdir", ".unlink",
                   ".rename", ".replace(", "shutil", ".save("):
            assert op not in MODULE_SOURCE, op

    def test_module_imports_only_cad_engine_pypdf_and_stdlib(self):
        tree = ast.parse(MODULE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in {
                    "dataclasses", "hashlib", "json", "re", "io", "pathlib"}
                    for alias in node.names), node.names
            elif isinstance(node, ast.ImportFrom):
                root = node.module.split(".")[0]
                assert root in {"app", "pypdf", "dataclasses", "io", "pathlib"}, node.module

    def test_scope_statement_is_honest(self):
        statement = fa.FABRICATOR_ACCEPTANCE_SCOPE_STATEMENT
        assert "7AS" in statement
        assert "never invents missing information" in statement
        assert "not engineering approval" in statement
        assert "fabrication-ready" in statement
        assert "never re-verifies" in statement
        assert "never mutates" in statement
