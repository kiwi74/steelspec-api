"""
Milestone E5 — tests for RESOLVED CONFLICT -> FABRICATOR-FACING
DELIVERABLE -> INDEPENDENT ACCEPTANCE
(app.cad_engine.resolved_conflict_deliverable), the milestone that
gathers one explicitly human-resolved catalogue conflict's ALREADY
VERIFIED E4 drawing artifact into a deterministic, fabricator-facing
deliverable whose conflict provenance is recoverable from the
deliverable itself, and evaluates that deliverable independently:

    E1..E2  SOURCE CONFLICT -> 7AC -> 7AD human resolution -> 7Z
        -> E3 RESOLVED GEOMETRY   (resolved_conflict_geometry)
        -> E4 VERIFIED PDF        (resolved_conflict_drawing; 7AG VERIFIED)
        -> E5 DELIVERABLE         build_resolved_conflict_deliverable():
                                  a byte copy of the RECORDED artifact +
                                  a deterministic manifest.json
        -> E5 ACCEPTANCE          evaluate_resolved_conflict_deliverable():
                                  evidence from the real files on disk,
                                  reconciled through the 7AS matrix
        -> ACCEPTED / NOT_ACCEPTED / REFUSED

These tests prove:

  - All four conflict x decision combinations reach a deliverable whose
    packaged PDF is BYTE-IDENTICAL to the artifact 7AG verified (§1-5):
    E5 copies the recorded bytes and never regenerates, repairs or
    creates a second drawing.
  - The manifest is complete and deterministic (§6-14): both source
    rows, both names, the conflicting fields with both values, the E1
    verdict, the E2 human decision and rationale, the selected source,
    the resolved geometry, the drawing identity and the recorded 7AG
    verification evidence — with no clock value, no random id and no
    filesystem path beyond the deliberately recorded artifact identity.
  - Acceptance is independent and truthful (§15-19): the genuine case is
    ACCEPTED; a genuine gap is NOT_ACCEPTED with named findings; and the
    7AS reconciliation rules hold — PASS can never cover MISSING or
    CONTRADICTED, NOT_APPLICABLE can never cover PRESENT, and invalid
    input REFUSES rather than continuing.
  - Fail-closed (§20-28): an unresolved conflict, a KEEP_BOTH decision,
    a non-VERIFIED artifact, a missing artifact, a hash mismatch, a
    substituted artifact, a stale identity, a tampered PDF and a
    malformed PDF all refuse or block, and NOTHING is written — not even
    a partial output directory.
  - Nothing is invented (§29-32): material stays "NOT SPECIFIED" and is
    reported missing; no production acceptance exists; no
    HUMAN_CONFIRMED or PRODUCTION_PROVEN provenance appears; and the
    synthetic evidence note survives verbatim.
  - The module is additive and pure (§33-38): a frozen import whitelist,
    frozen public records, byte-deterministic output, no network /
    environment / clock / randomness, no production module referencing
    it, and the protected production files untouched.

FIXTURE PROVENANCE: the conflicts, their captured and live evidence and
their synthetic human resolutions are the SAME verbatim evidence as the
E2/E3/E4 proofs (imported from the E3 test module — the real Selby
evidence does not establish either side, so every decision here is
explicitly a synthetic TEST input, never presented as a real
engineering decision). The connection-level reviewer values (plate,
holes, location) are the established synthetic 7E/7H/7N fixture values.
A passing proof does NOT mean production-ready, fabrication-ready or
engineering-approved; it means the chain executed, the deliverable
carries the truth, and the records agree.
"""
import ast
import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.cad_engine import catalogue_conflict_resolution as ccr
from app.cad_engine import resolved_conflict_deliverable as e5
from app.cad_engine import resolved_conflict_drawing as e4
from app.cad_engine.drawing_output_verification import (
    VERIFICATION_CHECK_CODES,
    VERIFICATION_STATUS_NOT_VERIFIABLE,
    VERIFICATION_STATUS_VERIFIED,
)
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_BLOCKED,
    PACKAGE_STATUS_READY,
    PACKAGE_STATUS_REFUSED,
)
from app.cad_engine.fabricator_acceptance import (
    ANSWER_FAIL,
    ANSWER_NOT_APPLICABLE,
    ANSWER_PASS,
    CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
    EVIDENCE_CONTRADICTED,
    EVIDENCE_MISSING,
    EVIDENCE_PRESENT,
    FabricatorChecklistAnswer,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
)
from app.engineering_data.section_catalogue import PROVENANCE_KINDS
from tests.test_real_world_e3_conflict_geometry_integration import (
    LIVE_250PFC,
    LIVE_310UB40_4,
    SYNTHETIC_NOTE,
    _conflict_a,
    _conflict_b,
    _redecided,
)

E5_MODULE_PATH = Path(e5.__file__)
APP_DIR = Path(e5.__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parent

# The established synthetic 7E/7H/7N connection fixture values — connection
# context around the conflict's members, never section geometry.
PLATE = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
HOLES = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}
B_KWARGS = dict(member_a_length_mm=1500.0, member_b_length_mm=1500.0, location_z=1494.0)

# The E2/E3/E4 fixture's recorded rationale, verbatim (the E3 helper's default).
RATIONALE = "the engineer checked the source drawing"

# The protected production surface. E5 must not modify, import or wire
# any of it.
PROTECTED_PATHS = (
    "app/pipeline.py",
    "app/main.py",
    "app/review_ui",
    "app/ai_analysis",
    "app/drawing_generator",
    "app/engineering_data/section_catalogue.py",
)

# The exact import surface of the E5 module (§33).
FROZEN_IMPORTS = frozenset({
    "app.cad_engine.catalogue_conflict_resolution",
    "app.cad_engine.drawing_output_verification",
    "app.cad_engine.fabrication_package",
    "app.cad_engine.fabricator_acceptance",
    "app.cad_engine.production_acceptance",
    "app.cad_engine.resolved_conflict_drawing",
    "app.cad_engine.resolved_conflict_geometry",
    "copy",
    "dataclasses",
    "hashlib",
    "json",
    "pathlib",
    "pypdf",
    "re",
    "typing",
})

FROZEN_PUBLIC_API = frozenset({
    "RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT",
    "DECISION_PROVENANCE_SYNTHETIC",
    "DELIVERABLE_MATERIAL_NOT_SPECIFIED",
    "CHECKLIST_ITEM_CONFLICT_DISCLOSED",
    "CHECKLIST_ITEM_SELECTED_AUTHORITY",
    "CHECKLIST_ITEM_HUMAN_DECISION",
    "CHECKLIST_ITEM_SYNTHETIC_PROVENANCE",
    "CHECKLIST_ITEM_MATERIAL_STATED",
    "DELIVERABLE_CHECKLIST_ITEMS",
    "REQUIRED_DELIVERABLE_ITEMS",
    "ResolvedConflictAcceptanceAnswers",
    "ResolvedConflictDeliverable",
    "ResolvedConflictAcceptance",
    "build_resolved_conflict_deliverable",
    "evaluate_resolved_conflict_deliverable",
})


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _a_capture():
    return _redecided(_conflict_a(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)


def _a_catalogue():
    return _redecided(_conflict_a(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)


def _b_capture():
    return _redecided(_conflict_b(), ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE)


def _b_catalogue():
    return _redecided(_conflict_b(), ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE)


def _produce(conflict, output_dir, **overrides):
    """One genuine E4 run, with the established synthetic connection fixture."""
    kwargs = dict(output_dir=output_dir, plate=PLATE, holes=HOLES, location_z=994.0)
    kwargs.update(overrides)
    return e4.produce_resolved_conflict_drawing(conflict, **kwargs)


def _build(result, output_dir, **overrides):
    """One E5 deliverable build over a genuine E4 result."""
    kwargs = dict(resolution_evidence=SYNTHETIC_NOTE)
    kwargs.update(overrides)
    return e5.build_resolved_conflict_deliverable(result, output_dir=output_dir, **kwargs)


def _ready(tmp_path, conflict=None, **produce_overrides):
    """A READY deliverable plus its E4 result, from the genuine chain."""
    result = _produce(conflict or _a_capture(), tmp_path / "e4", **produce_overrides)
    return _build(result, tmp_path / "e5"), result


def _answers(fail=(), not_applicable=(e5.CHECKLIST_ITEM_MATERIAL_STATED,), passed=()):
    """A complete, valid reviewer answer set: everything PASSes except the
    named items — material is NOT_APPLICABLE by default, because the
    deliverable genuinely does not state one and a PASS over missing
    evidence is a matrix violation, not an opinion (§17)."""
    out = []
    for item in e5.DELIVERABLE_CHECKLIST_ITEMS:
        if item in fail:
            out.append(FabricatorChecklistAnswer(item, ANSWER_FAIL, f"the reviewer could not confirm {item}"))
        elif item in not_applicable:
            out.append(FabricatorChecklistAnswer(item, ANSWER_NOT_APPLICABLE))
        else:
            out.append(FabricatorChecklistAnswer(item, ANSWER_PASS))
    return e5.ResolvedConflictAcceptanceAnswers(answers=tuple(out))


def _manifest(deliverable):
    return json.loads(Path(deliverable.manifest_path).read_text())


def _manifest_bytes(deliverable):
    return Path(deliverable.manifest_path).read_bytes()


def _pdf_text(path):
    return PdfReader(str(path)).pages[0].extract_text()


def _label_value(text, label):
    """The value line following a title-block label line, or None."""
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line == label and index + 1 < len(lines):
            return lines[index + 1]
    return None


def _tree():
    return ast.parse(E5_MODULE_PATH.read_text())


def _code_identifiers():
    """Every identifier the module's CODE mentions: imported names, module
    references and attribute accesses. Docstrings, comments and prose are
    deliberately excluded — the module is allowed to name the things it
    refuses to do."""
    tree = _tree()
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    identifiers |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            identifiers |= {alias.name for alias in node.names}
            identifiers |= {alias.asname for alias in node.names if alias.asname}
        elif isinstance(node, ast.ImportFrom):
            identifiers.add(node.module)
            identifiers |= {alias.name for alias in node.names}
    return identifiers


def _identifiers_of(path):
    """Every identifier a module's CODE mentions: imported names, module
    references and attribute accesses. Docstrings and comments excluded."""
    tree = ast.parse(Path(path).read_text())
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    identifiers |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            identifiers |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            identifiers.add(node.module or "")
            identifiers |= {alias.name for alias in node.names}
    return identifiers


def _code_string_literals():
    """Every string constant in the module EXCEPT the docstrings."""
    tree = _tree()
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _evidence(acceptance, item):
    return next(r for r in acceptance.results if r.checklist_item == item)


# =============================================================================
# 1-4. Every conflict x decision combination reaches a deliverable.
# =============================================================================
class TestAllFourCombinationsReachADeliverable:
    @pytest.mark.parametrize("label,conflict_fn,decision,expected_name,produce_kwargs", [
        ("1. Conflict A / CAPTURE authoritative", _a_capture, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "250X90PFC", {}),
        ("2. Conflict A / live CATALOGUE authoritative", _a_catalogue, ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE, "250PFC", {}),
        ("3. Conflict B / CAPTURE authoritative", _b_capture, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "310UB40", B_KWARGS),
        ("4. Conflict B / live CATALOGUE authoritative", _b_catalogue, ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE, "310UB40.4", B_KWARGS),
    ])
    def test_every_combination_produces_a_deliverable(
        self, tmp_path, label, conflict_fn, decision, expected_name, produce_kwargs,
    ):
        deliverable, result = _ready(tmp_path, conflict_fn(), **produce_kwargs)

        assert deliverable.status == PACKAGE_STATUS_READY, (label, deliverable.reason)
        assert deliverable.acceptance.accepted is True
        assert deliverable.conflict_id == result.conflict.conflict_id
        assert deliverable.connection_id == result.connection_id == f"E4-CONN-{result.conflict.conflict_id}"
        assert deliverable.selected_source == result.selected_source
        assert deliverable.artifact_path and Path(deliverable.artifact_path).is_file()
        assert deliverable.manifest_path and Path(deliverable.manifest_path).is_file()
        assert all(check.status == "PASSED" for check in deliverable.checks), [
            (c.code, c.status) for c in deliverable.checks
        ]

        manifest = _manifest(deliverable)
        assert manifest["conflict"]["captured_name"] != manifest["conflict"]["catalogue_name"]
        assert manifest["conflict"]["conflicting_fields"], label
        assert manifest["resolved_geometry"]["section_name"] == expected_name
        assert manifest["decision_provenance"]["kind"] == e5.DECISION_PROVENANCE_SYNTHETIC


# =============================================================================
# 5. The packaged PDF is the recorded artifact's bytes, byte for byte.
# =============================================================================
class TestPackagedArtifactIdentity:
    def test_packaged_pdf_bytes_are_exactly_the_recorded_artifact_bytes(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        recorded = Path(result.dispatch_result.generated_files[0])
        packaged = Path(deliverable.artifact_path)

        assert packaged.name == recorded.name
        assert packaged.read_bytes() == recorded.read_bytes()
        assert hashlib.sha256(packaged.read_bytes()).hexdigest() == deliverable.artifact_sha256
        assert deliverable.artifact_sha256 == result.verification_result.sha256
        assert deliverable.artifact_size_bytes == recorded.stat().st_size

    def test_no_second_drawing_is_created_and_the_recorded_artifact_is_untouched(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        recorded = Path(result.dispatch_result.generated_files[0])
        before = recorded.read_bytes()

        # The deliverable lives in its own tree; the source artifact is unchanged.
        assert Path(deliverable.artifact_path).parent != recorded.parent
        assert recorded.read_bytes() == before
        assert list((tmp_path / "e5" / "drawings").glob("*.pdf")) == [Path(deliverable.artifact_path)]

    def test_the_module_never_calls_the_generator(self):
        """The E5 module has no path to a drawing generator at all."""
        names = {node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(_tree()) if isinstance(node, ast.Attribute)}
        for token in ("produce_resolved_conflict_drawing", "dispatch_fabrication_drawing",
                      "generate_fabrication_drawing_pdf", "verify_drawing_artifact"):
            assert token not in names
            assert token not in attributes


# =============================================================================
# 6. The manifest is deterministic — same E4 result, identical bytes.
# =============================================================================
class TestManifestDeterminism:
    def test_identical_manifest_bytes_across_repeated_builds(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        first = _build(result, tmp_path / "first")
        second = _build(result, tmp_path / "second")

        assert _manifest_bytes(first) == _manifest_bytes(second)
        assert Path(first.artifact_path).read_bytes() == Path(second.artifact_path).read_bytes()

    def test_manifest_carries_no_clock_and_no_filesystem_path(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        blob = _manifest_bytes(deliverable).decode()

        assert str(tmp_path) not in blob
        assert "/var/" not in blob and "/tmp" not in blob
        assert not any(token in blob for token in ("timestamp", "created_at", "generated_at"))
        assert not re.search(r"(19|20)\d{2}-\d{2}-\d{2}", blob)
        # The drawing carries a DATE cell. Whatever that date is, it is a
        # clock value and must never be projected into the deliverable.
        drawn_date = _label_value(_pdf_text(deliverable.artifact_path), "DATE")
        assert drawn_date and re.fullmatch(r"\d{2}/\d{2}/\d{4}", drawn_date)
        assert drawn_date not in blob
        # The only filesystem-dependent values are the artifact identity.
        manifest = json.loads(blob)
        assert manifest["drawing"]["artifact_filename"] == Path(deliverable.artifact_path).name
        assert set(manifest["drawing"]["verification_checks"][0]) == {"code", "status"}


# =============================================================================
# 7-14. What the manifest preserves.
# =============================================================================
class TestProvenancePreservation:
    def test_both_source_rows_are_carried_completely(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        conflict = _manifest(deliverable)["conflict"]

        assert conflict["captured_values"]["name"] == "250X90PFC"
        assert conflict["catalogue_values"]["name"] == "250PFC"
        assert conflict["captured_values"] == dict(result.conflict.captured_values)
        assert conflict["catalogue_values"] == dict(result.conflict.catalogue_values)
        assert conflict["captured_values"] != conflict["catalogue_values"]

    def test_conflicting_fields_carry_both_values(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        fields = {
            entry["field"]: (entry["captured_value"], entry["catalogue_value"])
            for entry in _manifest(deliverable)["conflict"]["conflicting_fields"]
        }

        assert fields == {"flange_thickness": (15.0, 12.0), "web_thickness": (8.0, 7.0)}
        assert fields == {
            field: (captured, catalogue)
            for field, captured, catalogue in result.conflict.conflicting_fields
        }
        assert all(captured != catalogue for captured, catalogue in fields.values())

    @pytest.mark.parametrize("conflict_fn,decision,expected_source,expected_name", [
        (_a_capture, ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE, "CAPTURE", "250X90PFC"),
        (_a_catalogue, ccr.HUMAN_DECISION_LIVE_CATALOGUE_AUTHORITATIVE, "CATALOGUE", "250PFC"),
    ])
    def test_selected_authority_is_recorded_and_matches_the_resolved_row(
        self, tmp_path, conflict_fn, decision, expected_source, expected_name,
    ):
        deliverable, result = _ready(tmp_path, conflict_fn())
        manifest = _manifest(deliverable)

        assert manifest["selected_source"] == expected_source
        assert deliverable.selected_source == expected_source
        assert manifest["resolved_geometry"]["section_name"] == expected_name
        assert manifest["resolved_geometry"]["resolved_section_row"]["name"] == expected_name
        assert manifest["resolved_geometry"]["resolved_section_row"] == dict(
            result.resolved_geometry.resolved_section_row
        )
        # The drawing itself carries the selected section in both member rows.
        text = _pdf_text(deliverable.artifact_path)
        assert text.count(f"\n{expected_name}\n") == 2

    def test_the_human_decision_and_rationale_are_recorded(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        conflict = _manifest(deliverable)["conflict"]

        assert conflict["human_decision"] == ccr.HUMAN_DECISION_CAPTURE_AUTHORITATIVE
        assert conflict["human_decision"] in ccr.HUMAN_DECISIONS
        assert conflict["human_rationale"] == RATIONALE
        assert conflict["resulting_decision"] == "CAPTURE_GEOMETRY_APPLIED"
        assert conflict["resolution_status"] == "RESOLVED"
        assert conflict["conflict_state"] == "CONFLICT_HUMAN_RESOLVED"
        assert conflict["original_verdict"] == result.conflict.original_verdict
        assert deliverable.acceptance.conflict_id == result.conflict.conflict_id

    def test_the_decision_is_declared_synthetic_test_evidence(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        provenance = _manifest(deliverable)["decision_provenance"]

        assert provenance["kind"] == e5.DECISION_PROVENANCE_SYNTHETIC
        assert "not a real engineering decision" in provenance["statement"]
        assert provenance["kind"] not in PROVENANCE_KINDS
        # The conflict's own E2 scope statement is carried verbatim — the
        # deliverable restates no boundary of its own making.
        assert _manifest(deliverable)["conflict_scope_statement"] == (
            ccr.CATALOGUE_CONFLICT_SCOPE_STATEMENT
        )
        assert "no real conflict is marked production-resolved" in (
            _manifest(deliverable)["conflict_scope_statement"]
        )

    def test_the_resolved_geometry_is_carried(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        geometry = _manifest(deliverable)["resolved_geometry"]

        assert geometry["mark"] == "E4-M1"
        assert geometry["resolved_section_row"]["flange_thickness"] == 15.0
        assert geometry["resolved_section_row"]["web_thickness"] == 8.0
        assert deliverable.manifest is not None

    def test_the_drawing_identity_is_carried(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        drawing = _manifest(deliverable)["drawing"]
        connection_id = deliverable.connection_id

        assert drawing["connection_id"] == connection_id
        assert drawing["drawing_number"] == f"FAB-{connection_id}"
        assert drawing["artifact_filename"] == f"{connection_id}-fabrication.pdf"
        text = _pdf_text(deliverable.artifact_path)
        assert f"CONNECTION DETAIL — {connection_id}" in text
        assert f"FAB-{connection_id}" in text

    def test_the_recorded_7ag_verification_evidence_is_carried(self, tmp_path):
        deliverable, result = _ready(tmp_path)
        drawing = _manifest(deliverable)["drawing"]

        assert drawing["verification_status"] == VERIFICATION_STATUS_VERIFIED
        assert drawing["verification_status"] == result.verification_result.verification_status
        assert drawing["artifact_sha256"] == result.verification_result.sha256
        assert drawing["page_count"] == result.verification_result.page_count
        codes = [check["code"] for check in drawing["verification_checks"]]
        assert codes == [check.code for check in result.verification_result.checks]
        assert set(codes) <= set(VERIFICATION_CHECK_CODES)
        assert all(check["status"] == "PASSED" for check in drawing["verification_checks"])


# =============================================================================
# 15-19. Acceptance is independent, truthful and rule-bound.
# =============================================================================
class TestIndependentAcceptance:
    def test_genuine_case_is_accepted(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        assert acceptance.deliverable_status == PACKAGE_STATUS_READY
        assert acceptance.conflict_id == deliverable.conflict_id
        assert acceptance.connection_id == deliverable.connection_id
        assert acceptance.refusal_reasons == ()
        assert len(acceptance.results) == len(e5.DELIVERABLE_CHECKLIST_ITEMS)
        assert all(r.answer == ANSWER_PASS or r.answer == ANSWER_NOT_APPLICABLE
                   for r in acceptance.results)
        # The material really is unstated, and it is reported, not filled in.
        material = _evidence(acceptance, e5.CHECKLIST_ITEM_MATERIAL_STATED)
        assert material.evidence_status == EVIDENCE_MISSING
        assert [f.kind for f in material.evaluator_findings] == ["MISSING_INFORMATION"]
        assert any("evidence missing: 1" in line for line in acceptance.summary)

    def test_genuine_not_accepted_carries_named_findings(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable,
            acceptance_answers=_answers(fail=(e5.CHECKLIST_ITEM_HOLE_INFORMATION,)),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert acceptance.refusal_reasons == ()
        named = [f for f in acceptance.findings if f.checklist_item == e5.CHECKLIST_ITEM_HOLE_INFORMATION]
        assert named and all("could not confirm" in f.detail for f in named)
        assert e5.CHECKLIST_ITEM_HOLE_INFORMATION in acceptance.reason
        assert any("reviewer failures: 1" in line for line in acceptance.summary)
        # The failed item's own evidence is still PRESENT — the reviewer's
        # judgement is recorded as an answer, never written into the evidence.
        assert _evidence(acceptance, e5.CHECKLIST_ITEM_HOLE_INFORMATION).evidence_status == EVIDENCE_PRESENT

    def test_pass_can_never_cover_missing_evidence(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable,
            acceptance_answers=_answers(not_applicable=(), passed=(e5.CHECKLIST_ITEM_MATERIAL_STATED,)),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()
        assert any("MATERIAL_STATED over missing evidence" in r for r in acceptance.refusal_reasons)

    def test_contradicted_evidence_can_never_receive_pass(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        forged = dataclasses.replace(deliverable, connection_id="E4-CONN-CONFLICT-FORGED")
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            forged, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()
        assert any(
            "over contradicted evidence" in reason for reason in acceptance.refusal_reasons
        )

    def test_contradicted_evidence_is_reported_not_passed(self, tmp_path):
        """With answers that are themselves valid over contradicted evidence
        (a FAIL carrying its finding), the contradiction is REPORTED and
        the verdict is NOT_ACCEPTED — never silently accepted."""
        deliverable, _ = _ready(tmp_path)
        forged = dataclasses.replace(deliverable, conflict_id="CONFLICT-250X90PFC-vs-9ZZ")
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            forged,
            acceptance_answers=_answers(fail=(e5.CHECKLIST_ITEM_TRACEABILITY,)),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        traceable = _evidence(acceptance, e5.CHECKLIST_ITEM_TRACEABILITY)
        assert traceable.evidence_status == EVIDENCE_CONTRADICTED
        assert [f.kind for f in traceable.evaluator_findings] == ["CONTRADICTION"]
        assert "TRACEABLE evidence is contradicted" in acceptance.reason

    def test_present_evidence_can_never_receive_not_applicable(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable,
            acceptance_answers=_answers(not_applicable=(
                e5.CHECKLIST_ITEM_MATERIAL_STATED, e5.CHECKLIST_ITEM_TRACEABILITY,
            )),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()
        assert any(
            "NOT_APPLICABLE for TRACEABLE while the evidence is present" in r
            for r in acceptance.refusal_reasons
        )

    def test_evidence_is_derived_from_the_artifact_not_the_manifest_alone(self, tmp_path):
        """A manifest that claims an item while the drawing does not carry it
        is CONTRADICTED, not PRESENT: the drawing's own text is the source."""
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )

        for item in (CHECKLIST_ITEM_CONNECTION_IDENTIFIED, e5.CHECKLIST_ITEM_PLATE_INFORMATION,
                     e5.CHECKLIST_ITEM_HOLE_INFORMATION, e5.CHECKLIST_ITEM_DRAWING_IDENTITY,
                     e5.CHECKLIST_ITEM_MEMBER_A_IDENTIFIED, e5.CHECKLIST_ITEM_MEMBER_B_IDENTIFIED):
            assert _evidence(acceptance, item).evidence_status == EVIDENCE_PRESENT
            assert _evidence(acceptance, item).visible_in_drawing is True
        # The conflict items are record-level: present, but not in the drawing.
        assert _evidence(acceptance, e5.CHECKLIST_ITEM_CONFLICT_DISCLOSED).visible_in_drawing is False


# =============================================================================
# 20-28. Fail-closed: nothing is written when the deliverable cannot be trusted.
# =============================================================================
class TestFailClosed:
    def _assert_nothing_written(self, deliverable, output_dir):
        assert deliverable.status in (PACKAGE_STATUS_REFUSED, PACKAGE_STATUS_BLOCKED)
        assert deliverable.manifest == ()
        assert deliverable.artifact_path is None
        assert deliverable.manifest_path is None
        assert deliverable.deliverable_dir is None
        assert not output_dir.exists(), "a refused or blocked build left output behind"

    def test_unresolved_conflict_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        unresolved = dataclasses.replace(result, conflict=_conflict_a())
        deliverable = _build(unresolved, tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_REFUSED
        assert "not an accepted human-resolved conflict" in deliverable.reason

    def test_keep_both_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        keep_both = _redecided(_conflict_a(), ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE)
        deliverable = _build(dataclasses.replace(result, conflict=keep_both), tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        # KEEP_BOTH is not a source selection: no geometry was applied, so
        # the conflict is still blocked and nothing may be delivered.
        assert deliverable.status == PACKAGE_STATUS_REFUSED
        assert ccr.accept_resolved_conflict(keep_both).accepted is False
        assert keep_both.resulting_decision not in (
            ccr.RESULTING_DECISION_CAPTURE_GEOMETRY, ccr.RESULTING_DECISION_CATALOGUE_GEOMETRY,
        )
        assert keep_both.human_decision == ccr.HUMAN_DECISION_KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE

    def test_a_non_verified_artifact_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        not_verified = dataclasses.replace(
            result,
            verification_result=dataclasses.replace(
                result.verification_result,
                verification_status=VERIFICATION_STATUS_NOT_VERIFIABLE,
            ),
        )
        deliverable = _build(not_verified, tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert "not 'VERIFIED'" in deliverable.reason

    def test_a_missing_artifact_blocks_and_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        Path(result.dispatch_result.generated_files[0]).unlink()
        deliverable = _build(result, tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_BLOCKED
        assert "is no longer present" in deliverable.reason
        assert any(c.code == "SOURCE_ARTIFACT_PRESENT" and c.status == "FAILED" for c in deliverable.checks)

    def test_a_hash_mismatch_blocks_and_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        artifact = Path(result.dispatch_result.generated_files[0])
        artifact.write_bytes(artifact.read_bytes() + b"\n% appended")
        deliverable = _build(result, tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_BLOCKED
        assert "no longer match the SHA-256" in deliverable.reason
        assert any(c.code == "SOURCE_SHA256_MATCHES_RECORDED" and c.status == "FAILED"
                   for c in deliverable.checks)

    def test_a_substituted_artifact_blocks_and_writes_nothing(self, tmp_path):
        """Another connection's genuinely-verified drawing is not this
        connection's drawing: the recorded filename must carry the
        connection identity."""
        result = _produce(_a_capture(), tmp_path / "e4")
        other = _produce(_b_capture(), tmp_path / "e4-b", **B_KWARGS)
        substituted = dataclasses.replace(
            result,
            dispatch_result=dataclasses.replace(
                result.dispatch_result,
                generated_files=tuple(other.dispatch_result.generated_files),
            ),
            verification_result=other.verification_result,
        )
        deliverable = _build(substituted, tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_BLOCKED
        assert "does not carry the connection identity" in deliverable.reason

    def test_a_stale_artifact_identity_is_refused_and_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        deliverable = _build(result, tmp_path / "e5", expected_artifact_sha256="0" * 64)

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_REFUSED
        assert "stale deliverable request" in deliverable.reason

    def test_a_malformed_artifact_blocks_and_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        artifact = Path(result.dispatch_result.generated_files[0])
        artifact.write_bytes(b"%PDF-1.4\nthis is not a real PDF body\n")
        garbage_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        forged = dataclasses.replace(
            result,
            verification_result=dataclasses.replace(result.verification_result, sha256=garbage_sha),
        )
        deliverable = _build(forged, tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_BLOCKED
        assert any(c.code == "PDF_OPENS" and c.status == "FAILED" for c in deliverable.checks)

    def test_contradictory_provenance_is_refused_and_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        deliverable = _build(dataclasses.replace(result, connection_id="E4-CONN-NOT-DERIVED"),
                             tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_REFUSED
        assert "does not derive from the conflict identity" in deliverable.reason

    def test_a_missing_conflict_identity_is_refused_and_writes_nothing(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        blank = dataclasses.replace(result.conflict, conflict_id="")
        deliverable = _build(dataclasses.replace(result, conflict=blank), tmp_path / "e5")

        self._assert_nothing_written(deliverable, tmp_path / "e5")
        assert deliverable.status == PACKAGE_STATUS_REFUSED
        assert "no usable conflict identity" in deliverable.reason

    # ---- and the same rigour on the evaluation side.
    def test_a_tampered_packaged_pdf_is_refused(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        artifact = Path(deliverable.artifact_path)
        artifact.write_bytes(artifact.read_bytes() + b"\n% tampered")
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()
        assert "no longer match the deliverable's recorded SHA-256" in acceptance.reason
        assert any("hashes to" in r for r in acceptance.refusal_reasons)

    def test_a_tampered_manifest_is_refused(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        manifest_path = Path(deliverable.manifest_path)
        manifest_path.write_bytes(_manifest_bytes(deliverable).replace(b"250X90PFC", b"250X90PFX", 1))
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()
        assert "does not match the deliverable's recorded evidence" in acceptance.reason
        assert any("differ from the deliverable's recorded projection" in r
                   for r in acceptance.refusal_reasons)

    def test_a_missing_packaged_artifact_is_refused(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        Path(deliverable.artifact_path).unlink()
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()

    def test_a_malformed_packaged_artifact_is_refused(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        artifact = Path(deliverable.artifact_path)
        artifact.write_bytes(b"%PDF-1.4\nnot a real pdf\n")
        # Keep the recorded identity honest so the parse is what fails.
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()

    def test_a_non_ready_deliverable_cannot_be_evaluated(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        refused = _build(dataclasses.replace(result, conflict=_conflict_a()), tmp_path / "e5")
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            refused, acceptance_answers=_answers(),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_REFUSED
        assert acceptance.results == ()
        assert acceptance.deliverable_status == PACKAGE_STATUS_REFUSED


# =============================================================================
# 29-32. Nothing is invented.
# =============================================================================
class TestNothingIsInvented:
    def test_material_remains_not_specified_and_is_reported(self, tmp_path):
        deliverable, _ = _ready(tmp_path)

        assert _manifest(deliverable)["material"] == e5.DELIVERABLE_MATERIAL_NOT_SPECIFIED
        assert "NOT SPECIFIED" in _pdf_text(deliverable.artifact_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )
        assert _evidence(acceptance, e5.CHECKLIST_ITEM_MATERIAL_STATED).evidence_status == EVIDENCE_MISSING

    def test_a_reviewer_fail_on_material_blocks_without_inventing_one(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable,
            acceptance_answers=_answers(
                fail=(e5.CHECKLIST_ITEM_MATERIAL_STATED,),
                not_applicable=(),
            ),
        )

        assert acceptance.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert "NOT SPECIFIED" in _pdf_text(deliverable.artifact_path)

    def test_no_production_acceptance_is_fabricated(self, tmp_path):
        from app.cad_engine.production_acceptance import ProductionJobAcceptance

        deliverable, _ = _ready(tmp_path)
        assert type(deliverable.acceptance) is ccr.ConflictAcceptance
        assert not isinstance(deliverable.acceptance, ProductionJobAcceptance)
        assert deliverable.acceptance.accepted is True

        # The module's CODE names none of the production-chain objects: it
        # neither builds one, imports one, nor has a parameter for one.
        identifiers = _code_identifiers()
        literals = set(_code_string_literals())
        for token in ("ProductionJobAcceptance", "ProjectWorkflowState",
                      "FabricationDrawingPackage", "accept_production_job",
                      "build_fabrication_package", "evaluate_fabricator_acceptance",
                      "ProjectArtifactVerificationResult"):
            assert token not in identifiers, token
            assert token not in literals, token
        # Nor is the 7AQ review-revision concurrency model ever implied.
        assert not hasattr(deliverable, "workflow_revision")
        assert not hasattr(deliverable, "project_id")

    def test_no_engineering_provenance_is_fabricated(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        blob = _manifest_bytes(deliverable).decode()

        assert "HUMAN_CONFIRMED" not in blob
        assert "PRODUCTION_PROVEN" not in blob
        assert e5.DECISION_PROVENANCE_SYNTHETIC not in PROVENANCE_KINDS
        assert "SYNTHETIC_TEST_EVIDENCE" in blob
        # The module's own provenance label is the one it records; it is not
        # any existing catalogue provenance kind.
        literals = set(_code_string_literals())
        assert e5.DECISION_PROVENANCE_SYNTHETIC in literals
        # The only places an engineering-provenance label may appear in the
        # module's code at all are the evaluator's REFUSAL guard — the check
        # that a manifest claiming one is contradicted, never recorded. If a
        # future edit writes one anywhere else, this fails.
        named = {lit for lit in literals if lit in PROVENANCE_KINDS}
        assert named == {"HUMAN_CONFIRMED", "PRODUCTION_PROVEN"}, sorted(named)
        for token in PROVENANCE_KINDS:
            assert token not in _code_identifiers(), token
        # The deliverable claims exactly one provenance, and it is not any
        # existing catalogue provenance kind. (The authority labels are also
        # absent from the bytes; the remaining kinds are excluded from that
        # check because "BOTH" is an ordinary word the E2 scope statement
        # legitimately contains inside KEEP_BOTH.)
        assert set(_manifest(deliverable)["decision_provenance"].values()) & set(PROVENANCE_KINDS) == set()
        for token in ("HUMAN_CONFIRMED", "PRODUCTION_PROVEN", "CAPTURE_EVIDENCE",
                      "LIVE_CATALOGUE_EVIDENCE"):
            assert token not in blob, token

    def test_the_synthetic_evidence_note_survives_verbatim(self, tmp_path):
        deliverable, _ = _ready(tmp_path)

        assert _manifest(deliverable)["decision_provenance"]["evidence_note"] == SYNTHETIC_NOTE
        on_disk = json.loads(Path(deliverable.manifest_path).read_text())
        assert on_disk["decision_provenance"]["evidence_note"] == SYNTHETIC_NOTE
        # And it is the conflict record's own note, not a restatement.
        assert on_disk["decision_provenance"]["kind"] == e5.DECISION_PROVENANCE_SYNTHETIC

    def test_an_unstated_evidence_note_is_reported_absent_not_invented(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        deliverable = _build(result, tmp_path / "e5", resolution_evidence=None)

        assert deliverable.status == PACKAGE_STATUS_READY
        assert _manifest(deliverable)["decision_provenance"]["evidence_note"] is None

    def test_the_scope_statements_say_what_this_is_not(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        scope = e5.RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT

        assert scope == deliverable.scope_statement
        assert "INDEPENDENT resolved-conflict deliverable" in scope
        assert "not a production job package" in scope
        assert "synthetic test evidence" in scope
        assert _manifest(deliverable)["scope_statement"] == scope


# =============================================================================
# 33-36. Module purity, frozen API, determinism, no ambient inputs.
# =============================================================================
class TestModulePurity:
    def test_import_whitelist_is_exactly_frozen(self):
        modules = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module)

        assert modules == FROZEN_IMPORTS
        assert not any(m.startswith("app.pipeline") or m.startswith("app.main")
                       or m.startswith("app.review_ui") for m in modules)

    def test_the_public_api_is_exactly_frozen(self):
        assert set(e5.__all__) == FROZEN_PUBLIC_API
        for name in e5.__all__:
            assert hasattr(e5, name), name

    def test_public_records_are_frozen(self, tmp_path):
        for record in (e5.ResolvedConflictDeliverable, e5.ResolvedConflictAcceptance,
                       e5.ResolvedConflictAcceptanceAnswers):
            assert dataclasses.is_dataclass(record)
            assert record.__dataclass_params__.frozen

        deliverable, _ = _ready(tmp_path)
        with pytest.raises(dataclasses.FrozenInstanceError):
            object.__setattr__  # noqa: B018 - the assignment below is the assertion
            deliverable.status = "ACCEPTED"
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            acceptance.status = PACKAGE_STATUS_READY

    def test_determinism_of_every_derived_value(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        first = _build(result, tmp_path / "first")
        second = _build(result, tmp_path / "second")

        assert first.checks == second.checks
        assert first.manifest == second.manifest
        assert first.artifact_sha256 == second.artifact_sha256
        left = e5.evaluate_resolved_conflict_deliverable(first, acceptance_answers=_answers())
        right = e5.evaluate_resolved_conflict_deliverable(second, acceptance_answers=_answers())
        assert left == right

    def test_no_network_environment_clock_or_randomness(self):
        tree = _tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        forbidden = {"environ", "random", "time", "datetime", "os", "subprocess",
                     "socket", "urllib", "uuid", "tempfile", "getenv", "now", "utcnow"}

        assert names & forbidden == set()
        assert attributes & forbidden == set()
        assert called & {"open", "eval", "exec", "compile", "__import__", "input"} == set()
        # File access is only through pathlib.
        assert "write_bytes" in attributes and "read_bytes" in attributes


# =============================================================================
# 37-38. No production wiring; the protected surface is untouched.
# =============================================================================
class TestNoProductionWiring:
    def test_no_production_module_references_e5(self):
        """No module under app/ imports, names or calls E5 — in CODE. A
        comment or docstring that merely mentions the milestone is not
        wiring; an import or an identifier is."""
        offenders = []
        for path in sorted(APP_DIR.rglob("*.py")):
            if path.resolve() == E5_MODULE_PATH.resolve():
                continue
            identifiers = _identifiers_of(path)
            if identifiers & FROZEN_PUBLIC_API or "resolved_conflict_deliverable" in identifiers:
                offenders.append(str(path.relative_to(REPO_ROOT)))

        assert offenders == []

    def test_the_e5_module_does_not_reach_into_the_protected_surface(self):
        source = E5_MODULE_PATH.read_text()
        for token in ("app.pipeline", "app.main", "app.review_ui", "app.ai_analysis",
                      "app.drawing_generator"):
            assert token not in source, token
        # And no production entry point is invoked.
        for token in ("dispatch_fabrication_drawing", "verify_drawing_artifact",
                      "evaluate_fabrication_output_gate", "generate_geometry",
                      "real_member_to_validated_member", "create_review_package"):
            assert token not in source, token

    def test_the_protected_files_are_still_present_and_do_not_mention_e5(self):
        for relative in PROTECTED_PATHS:
            path = REPO_ROOT / relative
            assert path.exists(), relative
            if path.is_file():
                assert "resolved_conflict_deliverable" not in path.read_text(), relative

    def test_e5_is_not_imported_by_another_test_module(self):
        """Only this proof file may IMPORT the E5 module. (E3/E4's purity
        scans name the module in a comment to record the exemption; a
        comment is not an import.)"""
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in sorted((REPO_ROOT / "tests").glob("*.py"))
            if path.name != Path(__file__).name
            and "resolved_conflict_deliverable" in _identifiers_of(path)
        ]
        assert offenders == []


# =============================================================================
# Additional: input validation, vocabulary reuse and evidence sourcing.
# =============================================================================
class TestInputValidationAndVocabulary:
    def test_build_rejects_wrong_types_loudly(self, tmp_path):
        result = _produce(_a_capture(), tmp_path / "e4")
        with pytest.raises(TypeError):
            e5.build_resolved_conflict_deliverable("not a result", output_dir=tmp_path / "x")
        with pytest.raises(TypeError):
            e5.build_resolved_conflict_deliverable(result, output_dir=str(tmp_path / "x"))
        with pytest.raises(TypeError):
            e5.build_resolved_conflict_deliverable(result, output_dir=tmp_path / "x",
                                                   resolution_evidence=123)
        with pytest.raises(TypeError):
            e5.build_resolved_conflict_deliverable(result, output_dir=tmp_path / "x",
                                                   expected_artifact_sha256=123)
        assert not (tmp_path / "x").exists()

    def test_evaluate_rejects_wrong_types_loudly(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        with pytest.raises(TypeError):
            e5.evaluate_resolved_conflict_deliverable(deliverable, acceptance_answers=())
        with pytest.raises(TypeError):
            e5.evaluate_resolved_conflict_deliverable(deliverable, acceptance_answers=None)

    def test_invalid_answer_sets_refuse_and_never_partially_evaluate(self, tmp_path):
        deliverable, _ = _ready(tmp_path)
        valid = _answers()
        bad_sets = {
            "empty": e5.ResolvedConflictAcceptanceAnswers(),
            "unknown item": e5.ResolvedConflictAcceptanceAnswers(answers=(
                FabricatorChecklistAnswer("NOT_A_REAL_ITEM", ANSWER_PASS),
            )),
            "invalid answer value": e5.ResolvedConflictAcceptanceAnswers(answers=(
                FabricatorChecklistAnswer(e5.CHECKLIST_ITEM_TRACEABILITY, "MAYBE"),
            )),
            "fail without a finding": e5.ResolvedConflictAcceptanceAnswers(answers=tuple(
                FabricatorChecklistAnswer(r.checklist_item, ANSWER_FAIL)
                if r.checklist_item == e5.CHECKLIST_ITEM_TRACEABILITY else
                FabricatorChecklistAnswer(r.checklist_item, r.answer)
                for r in valid.answers
            )),
            "duplicate item": e5.ResolvedConflictAcceptanceAnswers(answers=(
                valid.answers[0], valid.answers[0],
            )),
            "malformed record": e5.ResolvedConflictAcceptanceAnswers(answers=(
                valid.answers[0], "not an answer",
            )),
        }
        for label, answers in bad_sets.items():
            acceptance = e5.evaluate_resolved_conflict_deliverable(
                deliverable, acceptance_answers=answers,
            )
            assert acceptance.status == ACCEPTANCE_STATUS_REFUSED, label
            assert acceptance.results == (), label
            assert acceptance.refusal_reasons, label

    def test_the_checklist_reuses_existing_7as_items_and_defines_only_new_ones(self, tmp_path):
        from app.cad_engine.fabricator_acceptance import CHECKLIST_ITEMS

        reused = set(e5.DELIVERABLE_CHECKLIST_ITEMS) & set(CHECKLIST_ITEMS)
        assert reused == {
            CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
            "MEMBER_A_IDENTIFIED", "MEMBER_B_IDENTIFIED", "PLATE_INFORMATION",
            "HOLE_INFORMATION", "DRAWING_IDENTITY_CLEAR", "TRACEABLE",
        }
        new = set(e5.DELIVERABLE_CHECKLIST_ITEMS) - set(CHECKLIST_ITEMS)
        assert new == {
            e5.CHECKLIST_ITEM_CONFLICT_DISCLOSED, e5.CHECKLIST_ITEM_SELECTED_AUTHORITY,
            e5.CHECKLIST_ITEM_HUMAN_DECISION, e5.CHECKLIST_ITEM_SYNTHETIC_PROVENANCE,
            e5.CHECKLIST_ITEM_MATERIAL_STATED,
        }
        # The reused items keep their existing 7AS questions verbatim.
        from app.cad_engine.fabricator_acceptance import checklist_question
        deliverable, _ = _ready(tmp_path)
        acceptance = e5.evaluate_resolved_conflict_deliverable(
            deliverable, acceptance_answers=_answers(),
        )
        for result in acceptance.results:
            if result.checklist_item in CHECKLIST_ITEMS:
                assert result.question == checklist_question(result.checklist_item)

    def test_the_deliverable_checks_reuse_existing_7ar_codes(self, tmp_path):
        from app.cad_engine.fabrication_package import CHECK_STATUSES

        deliverable, _ = _ready(tmp_path)
        assert any(c.code for c in deliverable.checks)
        for check in deliverable.checks:
            assert check.status in CHECK_STATUSES
            assert isinstance(check.detail, str) and check.detail

    def test_required_items_exclude_only_material(self):
        assert set(e5.REQUIRED_DELIVERABLE_ITEMS) == (
            set(e5.DELIVERABLE_CHECKLIST_ITEMS) - {e5.CHECKLIST_ITEM_MATERIAL_STATED}
        )

    def test_the_evidence_note_is_preserved_for_both_conflicts(self, tmp_path):
        for factory, kwargs in ((_a_capture, {}), (_b_capture, B_KWARGS)):
            deliverable, _ = _ready(tmp_path / factory.__name__, factory(), **kwargs)
            assert _manifest(deliverable)["decision_provenance"]["evidence_note"] == SYNTHETIC_NOTE

    def test_the_live_evidence_is_never_the_local_weight_only_row(self, tmp_path):
        """A CAPTURE decision must not silently pick up the live row (and
        vice versa): the resolved row is the SELECTED source's own values."""
        capture, _ = _ready(tmp_path / "c", _a_capture())
        catalogue, _ = _ready(tmp_path / "l", _a_catalogue())

        captured_row = _manifest(capture)["resolved_geometry"]["resolved_section_row"]
        catalogue_row = _manifest(catalogue)["resolved_geometry"]["resolved_section_row"]
        assert captured_row["name"] == "250X90PFC" and captured_row["flange_thickness"] == 15.0
        assert catalogue_row["name"] == "250PFC" and catalogue_row["flange_thickness"] == 12.0
        assert dict(LIVE_250PFC) == {k: catalogue_row[k] for k in LIVE_250PFC}
        assert dict(LIVE_310UB40_4)["name"] not in ("310UB40",)
