"""
Milestone 7AG — REAL OUTPUT VERIFICATION (tests/test_drawing_output_verification.py)

Proves that the ACTUAL artifact 7AF's dispatch recorded is inspected
independently — exists / regular / non-empty / hashed (SHA-256) /
opened with the real PDF parser / structurally complete / non-blank /
identity-checked / compared against the genuine reviewed assembly's
own plain-data fields — through `app.cad_engine.drawing_output_verification`:

    GENERATED + real drawing           -> VERIFIED (every check PASSED)
    missing / empty / corrupt / blank  -> FAILED with the failed check
    BLOCKED_REVIEW / BLOCKED_CONFIRMATION / GENERATION_FAILED
                                       -> NO_ARTIFACT (nothing parsed)
    valid drawing, no assembly to      -> NOT_VERIFIABLE (correspondence
    compare against                        never asserted on faith)

The twelve brief tests plus the established discipline tests: SHA-256
is a deterministic artifact identity; mutation changes the hash; the
verification never mutates the artifact (hash_before == hash_after);
mixed projects keep every outcome and are never claimed verified; the
real Arkles capture has zero artifacts to verify; results are frozen
and deterministic; programming errors are loud; imports are pure and
the module imports without any secret environment.
"""
import ast
import dataclasses
import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.cad_engine import drawing_output_verification
from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_REVIEW
from app.cad_engine.automation_pipeline import evaluate_reviewed_connection_for_automation
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_CONFIRMATION,
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
    dispatch_fabrication_drawing,
    dispatch_project_fabrication_drawings,
)
from app.cad_engine.drawing_output_verification import (
    ARTIFACT_VERIFICATION_SCOPE_STATEMENT,
    CHECK_ARTIFACT_HASH,
    CHECK_DRAWING_CONTENT_PRESENT,
    CHECK_FILE_EXISTS,
    CHECK_FILE_NONEMPTY,
    CHECK_FORMAT_READABLE,
    CHECK_FORMAT_STRUCTURE_VALID,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
    CHECK_NOT_VERIFIABLE_FROM_ARTIFACT,
    CHECK_PASSED,
    PROJECT_VERIFICATION_SCOPE_STATEMENT,
    VERIFICATION_CHECK_CODES,
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_NOT_VERIFIABLE,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
    verify_drawing_artifact,
    verify_project_drawing_outputs,
)
from app.cad_engine.fabrication_output_gate import (
    evaluate_fabrication_output_gate,
    evaluate_project_fabrication_output_gate,
)

from tests.test_automation_gate import _reviewed_package
from tests.test_drawing_dispatch import _fuse_entry, _recording_entry
from tests.test_exception_resolution import _arkles_project
from tests.test_fabrication_output_gate import _auto_pipeline, _pipeline, _project_gate
from tests.test_project_extraction_intake import needs_real_capture


def _dispatch_with(tmp_path, entry, *, require_confirmation=()):
    """A genuine 7AE AUTO gate + 7AF dispatch through the given entry double."""
    pipeline = _pipeline(_reviewed_package(), require_confirmation=require_confirmation)
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_AUTO
    result = dispatch_fabrication_drawing(gate, pipeline, tmp_path / "out", drawing_entry=entry)
    return pipeline, gate, result


@pytest.fixture(scope="module")
def real_dispatch(tmp_path_factory):
    """One genuine dispatch through the EXISTING drawing generator — the real PDF."""
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)
    out_dir = tmp_path_factory.mktemp("7ag-real")
    result = dispatch_fabrication_drawing(gate, pipeline, out_dir)
    assert result.output_status == OUTPUT_STATUS_GENERATED
    return pipeline, gate, result


# =============================================================================
# Test 1. A real generated PDF verifies end to end
# =============================================================================
def test_real_generated_pdf_verifies(real_dispatch):
    pipeline, _gate, result = real_dispatch
    verified = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)

    assert verified.verification_status == VERIFICATION_STATUS_VERIFIED
    assert verified.dispatch_output_status == OUTPUT_STATUS_GENERATED
    assert verified.connection_id == "CONN-REAL-UB-CAD-001-L2"

    path = result.generated_files[0]
    assert verified.artifact_path == path
    assert verified.artifact_format == "pdf"
    assert verified.file_size_bytes == path.stat().st_size
    assert verified.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert verified.page_count == 1

    assert verified.failures == ()
    assert [check.code for check in verified.checks] == list(VERIFICATION_CHECK_CODES)
    assert all(check.status == CHECK_PASSED for check in verified.checks)
    assert VERIFICATION_STATUS_VERIFIED in "\n".join(verified.summary)


# =============================================================================
# Tests 2-4. Missing / empty / corrupt artifacts FAIL with the failed check
# =============================================================================
def test_missing_file_fails_with_FILE_EXISTS(real_dispatch, tmp_path):
    _pipeline_result, _gate, result = real_dispatch
    missing = dataclasses.replace(
        result, generated_files=(tmp_path / "never-recorded-fabrication.pdf",),
    )

    verified = verify_drawing_artifact(missing)

    assert verified.verification_status == VERIFICATION_STATUS_FAILED
    assert [failure.code for failure in verified.failures] == [CHECK_FILE_EXISTS]
    assert verified.checks == verified.failures  # verification stopped where the artifact stops
    assert verified.artifact_path == tmp_path / "never-recorded-fabrication.pdf"
    assert verified.sha256 is None and verified.page_count is None
    assert "never hunts for a substitute" in verified.failures[0].detail


def test_empty_file_fails_with_FILE_NONEMPTY(tmp_path):
    def empty_entry(assembly, output_path, **kwargs):
        output_path.write_bytes(b"")
        return output_path

    _pipeline, _gate, result = _dispatch_with(tmp_path, empty_entry)
    assert result.output_status == OUTPUT_STATUS_GENERATED

    verified = verify_drawing_artifact(result)

    assert verified.verification_status == VERIFICATION_STATUS_FAILED
    assert [failure.code for failure in verified.failures] == [CHECK_FILE_NONEMPTY]
    assert verified.file_size_bytes == 0
    # The empty artifact is still located and its identity still recorded.
    assert verified.sha256 == hashlib.sha256(b"").hexdigest()


def test_corrupt_pdf_fails_parsing_and_is_never_repaired(real_dispatch, tmp_path):
    pipeline, _gate, result = real_dispatch
    real_bytes = result.generated_files[0].read_bytes()

    def corrupt_entry(assembly, output_path, **kwargs):
        output_path.write_bytes(b"%PDF-1.4\n" + b"this is not a real pdf " * 20)
        return output_path

    _pipeline, _gate, corrupt_result = _dispatch_with(tmp_path, corrupt_entry)
    corrupt_path = corrupt_result.generated_files[0]
    before = corrupt_path.read_bytes()

    verified = verify_drawing_artifact(corrupt_result)

    assert verified.verification_status == VERIFICATION_STATUS_FAILED
    assert [failure.code for failure in verified.failures] == [CHECK_FORMAT_READABLE]
    assert "not a readable PDF" in verified.failures[0].detail
    assert "NOT repaired" in verified.failures[0].detail
    assert corrupt_path.read_bytes() == before  # the artifact was never repaired or rewritten

    # A truncated real PDF fails too — at parsing or at the trailer check.
    truncated = tmp_path / "truncated-fabrication.pdf"
    truncated.write_bytes(real_bytes[: len(real_bytes) // 2])
    truncated_result = dataclasses.replace(result, generated_files=(truncated,))
    verified = verify_drawing_artifact(truncated_result)
    assert verified.verification_status == VERIFICATION_STATUS_FAILED
    assert verified.failures[0].code in (CHECK_FORMAT_READABLE, CHECK_FORMAT_STRUCTURE_VALID)
    assert truncated.read_bytes() == real_bytes[: len(real_bytes) // 2]


def test_valid_but_blank_pdf_fails_drawing_content(tmp_path):
    def blank_entry(assembly, output_path, **kwargs):
        from reportlab.pdfgen import canvas
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer)
        c.showPage()
        c.save()
        output_path.write_bytes(buffer.getvalue())
        return output_path

    _pipeline, _gate, result = _dispatch_with(tmp_path, blank_entry)

    verified = verify_drawing_artifact(result)

    # It IS a technically valid one-page PDF — the deeper content check fails it.
    assert verified.verification_status == VERIFICATION_STATUS_FAILED
    assert [failure.code for failure in verified.failures] == [CHECK_DRAWING_CONTENT_PRESENT]
    assert "blank" in verified.failures[0].detail
    readable = next(check for check in verified.checks if check.code == CHECK_FORMAT_READABLE)
    assert readable.status == CHECK_PASSED
    assert verified.page_count == 1


# =============================================================================
# Tests 5-7. SHA-256: deterministic identity, mutation detection, no mutation
# =============================================================================
def test_sha256_is_a_deterministic_artifact_identity(real_dispatch):
    pipeline, _gate, result = real_dispatch
    path = result.generated_files[0]

    first = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)
    second = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)

    assert first == second and repr(first) == repr(second)
    assert first.sha256 == second.sha256
    assert first.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    hash_check = next(check for check in first.checks if check.code == CHECK_ARTIFACT_HASH)
    assert first.sha256 in hash_check.detail
    assert "not an engineering" in hash_check.detail  # never described as engineering proof


def test_mutation_changes_the_artifact_identity(real_dispatch, tmp_path):
    pipeline, _gate, result = real_dispatch
    path = result.generated_files[0]
    original_hash = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly).sha256

    mutated = tmp_path / "mutated-fabrication.pdf"
    payload = bytearray(path.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    mutated.write_bytes(payload)
    mutated_result = dataclasses.replace(result, generated_files=(mutated,))

    verified = verify_drawing_artifact(mutated_result, assembly=pipeline.reviewed_assembly)

    assert verified.sha256 == hashlib.sha256(mutated.read_bytes()).hexdigest()
    assert verified.sha256 != original_hash  # the identity no longer matches
    # The untouched original still carries its original identity.
    assert verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly).sha256 == original_hash


def test_verification_never_mutates_the_artifact(real_dispatch):
    pipeline, _gate, result = real_dispatch
    path = result.generated_files[0]
    before = path.read_bytes()

    verified = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)

    after = path.read_bytes()
    assert before == after  # hash_before == hash_after: verification is strictly read-only
    assert verified.sha256 == hashlib.sha256(after).hexdigest()


# =============================================================================
# Tests 8-9. Blocked and failed dispatches have no artifact to verify
# =============================================================================
def test_blocked_review_and_confirmation_are_never_parsed(tmp_path):
    review_pipeline = _pipeline(_reviewed_package(holes=None))
    review_gate = evaluate_fabrication_output_gate(review_pipeline)
    assert review_gate.decision == AUTOMATION_DECISION_REVIEW

    calls = []
    review_dir = tmp_path / "review"
    review_result = dispatch_fabrication_drawing(
        review_gate, review_pipeline, review_dir, drawing_entry=_fuse_entry(calls),
    )
    verified = verify_drawing_artifact(review_result)

    assert verified.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert verified.dispatch_output_status == OUTPUT_STATUS_BLOCKED_REVIEW
    assert verified.checks == () and verified.failures == ()
    assert verified.artifact_path is None and verified.artifact_format is None
    assert verified.file_size_bytes is None and verified.sha256 is None
    assert verified.page_count is None
    assert calls == [] and not review_dir.exists()  # nothing was generated, nothing was parsed

    confirm_pipeline = _pipeline(_reviewed_package(), require_confirmation=("ENGINEER",))
    confirm_gate = evaluate_fabrication_output_gate(confirm_pipeline)
    calls = []
    confirm_dir = tmp_path / "confirm"
    confirm_result = dispatch_fabrication_drawing(
        confirm_gate, confirm_pipeline, confirm_dir, drawing_entry=_fuse_entry(calls),
    )
    verified = verify_drawing_artifact(confirm_result)

    assert verified.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert verified.dispatch_output_status == OUTPUT_STATUS_BLOCKED_CONFIRMATION
    assert verified.checks == () and verified.sha256 is None
    assert calls == [] and not confirm_dir.exists()


def test_generation_failed_is_never_reinterpreted_as_generated(tmp_path):
    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("DXF writer exploded")

    _pipeline, _gate, result = _dispatch_with(tmp_path, exploding_entry)
    assert result.output_status == OUTPUT_STATUS_GENERATION_FAILED

    verified = verify_drawing_artifact(result)

    assert verified.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert verified.dispatch_output_status == OUTPUT_STATUS_GENERATION_FAILED
    assert verified.checks == () and verified.artifact_path is None
    assert "RuntimeError: DXF writer exploded" in verified.summary[-1]
    assert "never reinterpreted" in verified.summary[-1]


# =============================================================================
# Test 10. Genuine assembly correspondence — and honest NOT_VERIFIABLE
# =============================================================================
def test_genuine_assembly_correspondence_and_not_verifiable_without_assembly(real_dispatch):
    pipeline, _gate, result = real_dispatch
    path = result.generated_files[0]

    # The artifact GENUINELY contains these strings — read independently here.
    from pypdf import PdfReader
    text = " ".join(PdfReader(path).pages[0].extract_text().split())
    for expected in (
        "FAB-CONN-REAL-UB-CAD-001-L2",
        "CONNECTION DETAIL — CONN-REAL-UB-CAD-001-L2",
        "MEMBER A REAL-UB-CAD-001",
        "SECTION 310UB40",
        "LENGTH 4000 mm",
        "MEMBER B L2",
        "SECTION 250PFC",
        "LENGTH 3000 mm",
        "Ø22",
        # Present in the artifact; derived inside the generator from geometry
        # internals, so 7AG deliberately does not re-derive it — asserted here
        # only as a fact about the PDF.
        "PLATE: 180 × 250 × 12 mm",
    ):
        assert expected in text

    verified = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)
    geometry_check = next(
        check for check in verified.checks if check.code == CHECK_GEOMETRY_FIELDS_VERIFIABLE
    )
    assert geometry_check.status == CHECK_PASSED

    # Without the assembly: integrity verified, correspondence honestly NOT asserted.
    bare = verify_drawing_artifact(result)
    assert bare.verification_status == VERIFICATION_STATUS_NOT_VERIFIABLE
    assert bare.failures == ()
    geometry_check = next(
        check for check in bare.checks if check.code == CHECK_GEOMETRY_FIELDS_VERIFIABLE
    )
    assert geometry_check.status == CHECK_NOT_VERIFIABLE_FROM_ARTIFACT
    assert "not evaluated" in geometry_check.detail

    # A tampered assembly (wrong mark) is caught by the comparison.
    wrong = dataclasses.replace(
        pipeline.reviewed_assembly,
        member_b=dataclasses.replace(
            pipeline.reviewed_assembly.member_b,
            geometry=dataclasses.replace(pipeline.reviewed_assembly.member_b.geometry, mark="L9"),
        ),
    )
    wrong_verified = verify_drawing_artifact(result, assembly=wrong)
    assert wrong_verified.verification_status == VERIFICATION_STATUS_FAILED
    geometry_check = next(
        check for check in wrong_verified.checks if check.code == CHECK_GEOMETRY_FIELDS_VERIFIABLE
    )
    assert "MEMBER B L9" in geometry_check.detail


# =============================================================================
# Tests 11-12. Project verification keeps every outcome, never claims verified
# =============================================================================
def test_project_mixed_results_keep_every_outcome(tmp_path):
    base = _auto_pipeline()
    second_pipeline = dataclasses.replace(
        base,
        specification=dataclasses.replace(base.specification, connection_id="CONN-SECOND-UNIT"),
    )
    _collection, _project_result, pipelines, project_gate = _project_gate(
        _reviewed_package(), _reviewed_package(), _reviewed_package(holes=None),
        pipeline_by_id={"RP-0002": second_pipeline},
    )
    assert [o.gate_result.decision for o in project_gate.connection_results] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_REVIEW,
    ]

    dispatch_result = dispatch_project_fabrication_drawings(project_gate, pipelines, tmp_path)
    assert dispatch_result.generated_count == 2 and dispatch_result.blocked_review_count == 1

    # Corrupt the second connection's genuinely generated artifact.
    second_outcome = next(
        o for o in dispatch_result.connection_outputs if o.review_package_id == "RP-0002"
    )
    second_path = second_outcome.dispatch_result.generated_files[0]
    assert second_path.name == "CONN-SECOND-UNIT-fabrication.pdf"
    second_path.write_bytes(b"corrupted beyond parsing")

    verified_project = verify_project_drawing_outputs(
        dispatch_result,
        assemblies={
            "RP-0001": pipelines["RP-0001"].reviewed_assembly,
            "RP-0002": pipelines["RP-0002"].reviewed_assembly,
        },
    )

    assert verified_project.connections_total == 3
    assert verified_project.generated_count == 2
    assert verified_project.verified_count == 1
    assert verified_project.failed_count == 1
    assert verified_project.not_verifiable_count == 0
    assert verified_project.blocked_count == 1
    assert verified_project.verified_count + verified_project.failed_count + \
        verified_project.blocked_count + verified_project.not_verifiable_count == \
        verified_project.connections_total

    by_id = {o.review_package_id: o for o in verified_project.connection_results}
    assert by_id["RP-0001"].verification_result.verification_status == VERIFICATION_STATUS_VERIFIED
    assert by_id["RP-0002"].verification_result.verification_status == VERIFICATION_STATUS_FAILED
    assert by_id["RP-0003"].verification_result.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    assert by_id["RP-0003"].verification_result.dispatch_output_status == OUTPUT_STATUS_BLOCKED_REVIEW

    assert PROJECT_VERIFICATION_SCOPE_STATEMENT in verified_project.summary
    assert any("the project is NOT fully verified" in line for line in verified_project.summary)
    assert "RP-0003: NO_ARTIFACT — no artifact" in verified_project.summary


@needs_real_capture
def test_real_arkles_verifies_zero_artifacts(tmp_path):
    intake, project_result = _arkles_project()
    pipelines = {
        candidate.review_package_id: evaluate_reviewed_connection_for_automation(candidate.package)
        for candidate in intake.collection.candidates
    }
    project_gate = evaluate_project_fabrication_output_gate(project_result, pipelines)

    out_dir = tmp_path / "out"
    dispatch_result = dispatch_project_fabrication_drawings(
        project_gate, pipelines, out_dir, drawing_entry=_fuse_entry([]),
    )
    assert dispatch_result.generated_count == 0

    verified_project = verify_project_drawing_outputs(dispatch_result)

    assert verified_project.connections_total == 3
    assert verified_project.generated_count == 0
    assert verified_project.verified_count == 0
    assert verified_project.failed_count == 0
    assert verified_project.not_verifiable_count == 0
    assert verified_project.blocked_count == 3
    assert all(
        outcome.verification_result.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        for outcome in verified_project.connection_results
    )
    assert all(
        outcome.verification_result.checks == ()
        for outcome in verified_project.connection_results
    )
    assert not out_dir.exists()  # nothing generated, nothing parsed anywhere


# =============================================================================
# Discipline: loud errors, frozen deterministic results, pure imports
# =============================================================================
def test_programming_errors_propagate_loudly(tmp_path):
    pipeline, _gate, result = _dispatch_with(tmp_path, _recording_entry([]))

    with pytest.raises(TypeError):
        verify_drawing_artifact("not a dispatch result")
    with pytest.raises(TypeError):
        verify_drawing_artifact(result, assembly=object())
    with pytest.raises(ValueError):
        verify_drawing_artifact(dataclasses.replace(result, output_status="WEIRD"))

    # GENERATED with no recorded files is a manifest inconsistency -> FAILED.
    no_files = dataclasses.replace(result, generated_files=())
    verified = verify_drawing_artifact(no_files)
    assert verified.verification_status == VERIFICATION_STATUS_FAILED
    assert [failure.code for failure in verified.failures] == [CHECK_FILE_EXISTS]
    assert verified.artifact_path is None and verified.sha256 is None

    _collection, _project_result, pipelines, project_gate = _project_gate(_reviewed_package())
    project_dispatch = dispatch_project_fabrication_drawings(
        project_gate, pipelines, tmp_path / "p", drawing_entry=_recording_entry([]),
    )
    with pytest.raises(TypeError):
        verify_project_drawing_outputs("not a project dispatch")
    with pytest.raises(TypeError):
        verify_project_drawing_outputs(
            project_dispatch, assemblies=[("RP-0001", pipelines["RP-0001"].reviewed_assembly)],
        )


def test_results_frozen_and_deterministic(real_dispatch):
    pipeline, _gate, result = real_dispatch

    first = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)
    second = verify_drawing_artifact(result, assembly=pipeline.reviewed_assembly)
    assert first == second and repr(first) == repr(second)

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.verification_status = VERIFICATION_STATUS_FAILED
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.checks[0].status = CHECK_PASSED

    assert ARTIFACT_VERIFICATION_SCOPE_STATEMENT in first.summary
    assert "not an engineering" in " ".join(check.detail for check in first.checks)


def test_drawing_output_verification_module_imports_are_pure():
    source = Path(drawing_output_verification.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported == {
        "app.cad_engine.drawing_dispatch",           # the genuine 7AF manifest types/statuses only
        "app.cad_engine.multi_member_connection",    # the multi-member assembly type (7AV)
        "app.cad_engine.reviewed_connection_assembly",  # the genuine assembly type only
        "collections.abc",
        "dataclasses",
        "hashlib",                                   # SHA-256 artifact identity
        "pathlib",
        "pypdf",                                     # the real PDF parser
        "re",
    }
    lowered = source.lower()
    for forbidden in ("supabase", "anthropic", "fastapi", "cadquery", "reportlab", "ezdxf"):
        assert forbidden not in lowered
    # No hardcoded fixture geometry or AI values — verification invents nothing.
    for forbidden in ("310ub", "250pfc", "real-ub", "conn-real", "Ø22", "m12"):
        assert forbidden not in lowered


def test_module_imports_without_any_secret_environment():
    code = "import app.cad_engine.drawing_output_verification; print('ok')"
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"SUPABASE_URL", "SUPABASE_KEY", "ANTHROPIC_API_KEY"}
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
