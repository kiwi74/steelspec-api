"""
Milestone 7AF — REAL DRAWING DISPATCH (tests/test_drawing_dispatch.py)

Proves that the accepted 7AE fabrication-output permission is EXECUTED —
never re-decided — against the EXISTING SteelSpec drawing generator
(the 7S boundary over the 7T PDF builder), through
`app.cad_engine.drawing_dispatch`:

    AUTO    -> the genuine reviewed assembly reaches the existing
               drawing generator -> GENERATED (manifest recorded)
    REVIEW  -> BLOCKED_REVIEW, generator never called, nothing written
    CONFIRM -> BLOCKED_CONFIRMATION, generator never called, nothing
               written, no conversion into AUTO
    failure -> GENERATION_FAILED with the actual error, no fallback

The twelve brief tests plus the established discipline tests: no
injected approval; requested formats respected (DXF has no
reviewed-connection entry and fails honestly); the genuine assembly
object is passed (never rebuilt); mixed projects keep every outcome;
the real Arkles capture generates ZERO drawings; no hardcoded
geometry (the module contains no numeric literal); manifests are
complete, immutable, deterministic; dispatch re-runs no gate and no
validation; imports are pure and the module imports without any secret
environment.
"""
import ast
import dataclasses
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.cad_engine.automation_gate as automation_gate_module
import app.cad_engine.automation_pipeline as automation_pipeline_module
import app.cad_engine.fabrication_output_gate as fabrication_output_gate_module
import app.cad_engine.reviewed_connection_drawing_gate as reviewed_connection_drawing_gate
import app.cad_engine.reviewed_connection_validation_gate as reviewed_connection_validation_gate
from app.cad_engine import drawing_dispatch
from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM, AUTOMATION_DECISION_REVIEW
from app.cad_engine.automation_pipeline import evaluate_reviewed_connection_for_automation
from app.cad_engine.drawing_dispatch import (
    DRAWING_FORMAT_DXF,
    DRAWING_FORMAT_PDF,
    PROJECT_DISPATCH_SCOPE_STATEMENT,
    OUTPUT_STATUS_BLOCKED_CONFIRMATION,
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
    dispatch_fabrication_drawing,
    dispatch_project_fabrication_drawings,
)
from app.cad_engine.fabrication_output_gate import evaluate_fabrication_output_gate, evaluate_project_fabrication_output_gate

from tests.test_automation_gate import _reviewed_package
from tests.test_exception_resolution import _arkles_project
from tests.test_fabrication_output_gate import _auto_pipeline, _pipeline, _project_gate
from tests.test_project_extraction_intake import needs_real_capture


def _recording_entry(record):
    """A test double of the 7S drawing entry: records the call, writes a placeholder file,
    returns its path — so dispatch tests stay fast while still proving call-through."""
    def entry(assembly, output_path, **kwargs):
        record.append((assembly, output_path, kwargs))
        output_path.write_bytes(b"placeholder")
        return output_path
    return entry


def _fuse_entry(record):
    """A test double that must never be called: it records and raises loudly."""
    def entry(assembly, output_path, **kwargs):
        record.append((assembly, output_path, kwargs))
        raise AssertionError("the drawing generator must never be reached")
    return entry


# =============================================================================
# Test 1. AUTO generates through the existing drawing generator
# =============================================================================
def test_AUTO_dispatches_to_the_existing_drawing_generator(monkeypatch, tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)  # the genuine 7AE permission
    assert gate.decision == AUTOMATION_DECISION_AUTO

    calls = []
    real_entry = reviewed_connection_drawing_gate.generate_fabrication_drawing_from_reviewed_assembly

    def call_through(assembly, output_path, **kwargs):
        calls.append(assembly)
        return real_entry(assembly, output_path, **kwargs)

    monkeypatch.setattr(
        reviewed_connection_drawing_gate, "generate_fabrication_drawing_from_reviewed_assembly", call_through,
    )

    result = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=call_through)

    assert result.output_status == OUTPUT_STATUS_GENERATED
    assert result.generation_error is None
    assert result.requested_formats == (DRAWING_FORMAT_PDF,)
    assert result.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert len(calls) == 1  # the existing generator was actually reached
    assert len(result.generated_files) == 1
    written = result.generated_files[0]
    assert written.exists() and written.name == "CONN-REAL-UB-CAD-001-L2-fabrication.pdf"
    assert written.read_bytes().startswith(b"%PDF-")  # a real PDF, not a placeholder


# =============================================================================
# Tests 2-3. REVIEW and CONFIRM never generate
# =============================================================================
def test_REVIEW_never_generates(tmp_path):
    pipeline = _pipeline(_reviewed_package(holes=None))
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_REVIEW

    calls = []
    out_dir = tmp_path / "out"
    result = dispatch_fabrication_drawing(gate, pipeline, out_dir, drawing_entry=_fuse_entry(calls))

    assert result.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
    assert result.generation_error is None
    assert result.generated_files == ()
    assert calls == []
    assert not out_dir.exists()  # not even the output directory was created


def test_CONFIRM_never_generates_and_is_never_converted_to_AUTO(tmp_path):
    pipeline = _pipeline(_reviewed_package(), require_confirmation=("ENGINEER",))
    gate = evaluate_fabrication_output_gate(pipeline)
    assert gate.decision == AUTOMATION_DECISION_CONFIRM

    calls = []
    out_dir = tmp_path / "out"
    result = dispatch_fabrication_drawing(gate, pipeline, out_dir, drawing_entry=_fuse_entry(calls))

    assert result.output_status == OUTPUT_STATUS_BLOCKED_CONFIRMATION
    assert result.decision == AUTOMATION_DECISION_CONFIRM  # the manifest keeps the true decision
    assert result.generated_files == ()
    assert calls == []
    assert not out_dir.exists()


# =============================================================================
# Test 4. No injected approval
# =============================================================================
def test_no_injected_approval_parameters():
    single = inspect.signature(dispatch_fabrication_drawing).parameters
    project = inspect.signature(dispatch_project_fabrication_drawings).parameters
    assert set(single) == {
        "gate_result", "pipeline_result", "output_dir", "requested_formats",
        "drawing_entry", "multi_member_drawing_entry", "pdf_kwargs",
    }
    assert set(project) == {
        "project_gate_result", "pipeline_results", "output_dir", "requested_formats",
        "drawing_entry", "multi_member_drawing_entry", "pdf_kwargs",
    }
    for parameters in (single, project):
        for forbidden in ("approved", "approve", "validation_passed", "validation_evidence", "decision"):
            assert forbidden not in parameters


# =============================================================================
# Test 5. Generator failure is reported honestly
# =============================================================================
def test_generator_failure_is_reported_honestly(tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)

    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("DXF writer exploded")

    result = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=exploding_entry)

    assert result.output_status == OUTPUT_STATUS_GENERATION_FAILED
    assert result.generation_error == "RuntimeError: DXF writer exploded"  # the actual error, deterministically
    assert result.generated_files == ()
    assert list(tmp_path.iterdir()) == []  # no successful output is reported or written

    # An entry that returns without writing is NOT a successful drawing either.
    def silent_entry(assembly, output_path, **kwargs):
        return output_path

    result = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=silent_entry)
    assert result.output_status == OUTPUT_STATUS_GENERATION_FAILED
    assert "FileNotFoundError: the drawing entry returned without writing" in result.generation_error
    assert result.generated_files == ()


# =============================================================================
# Test 6. Requested formats are respected (the actual generator API)
# =============================================================================
def test_requested_formats_are_respected(tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)

    # PDF-only: a PDF and nothing else.
    calls = []
    result = dispatch_fabrication_drawing(
        gate, pipeline, tmp_path / "pdf", drawing_entry=_recording_entry(calls),
    )
    assert result.output_status == OUTPUT_STATUS_GENERATED
    assert len(result.generated_files) == 1
    assert result.generated_files[0].suffix == ".pdf"
    assert not any(path.suffix == ".dxf" for path in (tmp_path / "pdf").iterdir())

    # DXF-only: the existing reviewed-connection generator has no DXF entry -> honest failure,
    # no PDF is produced as a substitute, no fallback drawing appears.
    calls = []
    result = dispatch_fabrication_drawing(
        gate, pipeline, tmp_path / "dxf",
        requested_formats=(DRAWING_FORMAT_DXF,), drawing_entry=_recording_entry(calls),
    )
    assert result.output_status == OUTPUT_STATUS_GENERATION_FAILED
    assert "DXF" in result.generation_error
    assert result.generated_files == ()
    assert calls == []
    assert list((tmp_path / "dxf").iterdir()) == []

    # Both requested: the PDF IS produced and recorded, the DXF failure is reported — never hidden.
    calls = []
    result = dispatch_fabrication_drawing(
        gate, pipeline, tmp_path / "both",
        requested_formats=(DRAWING_FORMAT_PDF, DRAWING_FORMAT_DXF), drawing_entry=_recording_entry(calls),
    )
    assert result.output_status == OUTPUT_STATUS_GENERATION_FAILED
    assert len(result.generated_files) == 1 and result.generated_files[0].suffix == ".pdf"
    assert "DXF" in result.generation_error
    assert result.generated_files[0].exists()

    # Unknown or empty format requests are programming errors, loud.
    with pytest.raises(ValueError):
        dispatch_fabrication_drawing(gate, pipeline, tmp_path, requested_formats=("step",))
    with pytest.raises(ValueError):
        dispatch_fabrication_drawing(gate, pipeline, tmp_path, requested_formats=())


# =============================================================================
# Test 7. The genuine reviewed assembly reaches the generator
# =============================================================================
def test_genuine_reviewed_assembly_reaches_the_generator(tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)
    assert pipeline.reviewed_assembly is not None

    received = []
    result = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=_recording_entry(received))

    assert result.output_status == OUTPUT_STATUS_GENERATED
    assert len(received) == 1
    # The EXACT genuine assembly object from the accepted pipeline — never a rebuild, never a copy.
    assert received[0][0] is pipeline.reviewed_assembly

    # The default entry is the existing 7S production boundary itself.
    default_entry = inspect.signature(dispatch_fabrication_drawing).parameters["drawing_entry"].default
    assert default_entry is reviewed_connection_drawing_gate.generate_fabrication_drawing_from_reviewed_assembly


# =============================================================================
# Tests 8-9. Mixed projects keep every outcome
# =============================================================================
def test_mixed_project_AUTO_AUTO_REVIEW_keeps_every_outcome(tmp_path):
    collection, project_result, pipelines, project_gate = _project_gate(
        _reviewed_package(), _reviewed_package(), _reviewed_package(holes=None),
    )
    assert [o.gate_result.decision for o in project_gate.connection_results] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_REVIEW,
    ]

    result = dispatch_project_fabrication_drawings(
        project_gate, pipelines, tmp_path, drawing_entry=_recording_entry([]),
    )

    assert result.generated_count == 2
    assert result.blocked_review_count == 1
    assert result.blocked_confirmation_count == 0
    assert result.generation_failed_count == 0
    assert len(result.connection_outputs) == 3

    first, second, third = result.connection_outputs
    assert (first.review_package_id, second.review_package_id, third.review_package_id) == (
        "RP-0001", "RP-0002", "RP-0003",
    )
    assert first.dispatch_result.output_status == OUTPUT_STATUS_GENERATED
    assert second.dispatch_result.output_status == OUTPUT_STATUS_GENERATED
    # The blocked connection stays fully visible — never collapsed, never omitted.
    assert third.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
    assert third.dispatch_result.decision == AUTOMATION_DECISION_REVIEW
    assert third.dispatch_result.generated_files == ()
    assert project_gate.connection_results[2].gate_result.blockers  # its genuine 7Z blockers remain
    assert PROJECT_DISPATCH_SCOPE_STATEMENT in result.summary
    assert "RP-0003: BLOCKED_REVIEW — no files" in result.summary


def test_project_confirmation_keeps_CONFIRM_blocked(tmp_path):
    collection, project_result, pipelines, project_gate = _project_gate(
        _reviewed_package(), _reviewed_package(), holds={"RP-0002": ("FAB RELEASE",)},
    )
    assert [o.gate_result.decision for o in project_gate.connection_results] == [
        AUTOMATION_DECISION_AUTO, AUTOMATION_DECISION_CONFIRM,
    ]

    result = dispatch_project_fabrication_drawings(
        project_gate, pipelines, tmp_path, drawing_entry=_recording_entry([]),
    )

    assert result.generated_count == 1
    assert result.blocked_confirmation_count == 1
    assert result.blocked_review_count == 0
    assert len(result.connection_outputs) == 2
    confirm_outcome = next(o for o in result.connection_outputs if o.review_package_id == "RP-0002")
    assert confirm_outcome.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_CONFIRMATION
    assert confirm_outcome.dispatch_result.generated_files == ()


# =============================================================================
# Test 10. The real Arkles capture generates ZERO drawings
# =============================================================================
@needs_real_capture
def test_real_arkles_generates_zero_fabrication_drawings(tmp_path):
    intake, project_result = _arkles_project()
    pipelines = {
        candidate.review_package_id: evaluate_reviewed_connection_for_automation(candidate.package)
        for candidate in intake.collection.candidates
    }
    project_gate = evaluate_project_fabrication_output_gate(project_result, pipelines)
    assert (project_gate.eligible_count, project_gate.confirm_count, project_gate.review_count) == (0, 0, 3)

    out_dir = tmp_path / "out"
    result = dispatch_project_fabrication_drawings(
        project_gate, pipelines, out_dir, drawing_entry=_fuse_entry([]),
    )

    assert result.connections_total == 3
    assert result.generated_count == 0
    assert result.blocked_review_count == 3
    assert result.blocked_confirmation_count == 0
    assert result.generation_failed_count == 0
    assert all(
        outcome.dispatch_result.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        for outcome in result.connection_outputs
    )
    assert not out_dir.exists()  # zero fabrication drawings — nothing was written anywhere
    # The genuine upstream blockers stay visible on every blocked candidate.
    assert all(outcome.gate_result.blockers for outcome in project_gate.connection_results)


# =============================================================================
# Test 11. No hardcoded geometry
# =============================================================================
def test_no_hardcoded_geometry_anywhere_in_dispatch():
    source = Path(drawing_dispatch.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            raise AssertionError(f"numeric literal {node.value!r} found in drawing_dispatch.py "
                                 "— dispatch must never invent a dimension, coordinate or size")

    # No fixture geometry, no AI values, no section names, no dimension glyphs.
    for forbidden in ("Ø", "M12", "SQ4", "310UB", "250PFC", "REAL-UB", "CONN-"):
        assert forbidden not in source


# =============================================================================
# Test 12. Manifest completeness
# =============================================================================
def test_manifest_completeness(tmp_path):
    collection, project_result, pipelines, project_gate = _project_gate(
        _reviewed_package(), _reviewed_package(holes=None),
    )
    result = dispatch_project_fabrication_drawings(
        project_gate, pipelines, tmp_path, drawing_entry=_recording_entry([]),
    )

    assert result.connections_total == len(project_gate.connection_results) == 2
    assert len(result.connection_outputs) == 2  # every input connection has exactly one outcome
    assert {o.review_package_id for o in result.connection_outputs} == {"RP-0001", "RP-0002"}
    assert result.generated_count + result.blocked_review_count + result.blocked_confirmation_count + \
        result.generation_failed_count == result.connections_total
    for outcome in result.connection_outputs:
        manifest = outcome.dispatch_result
        assert manifest.requested_formats == (DRAWING_FORMAT_PDF,)
        assert isinstance(manifest.generated_files, tuple)
        assert manifest.output_status in (OUTPUT_STATUS_GENERATED, OUTPUT_STATUS_BLOCKED_REVIEW)

    # Single-connection manifest fields are all recorded.
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)
    single = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=_recording_entry([]))
    assert single.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert single.decision == AUTOMATION_DECISION_AUTO
    assert single.output_status == OUTPUT_STATUS_GENERATED
    assert single.generation_error is None


# =============================================================================
# Discipline: no gate/validation rerun, purity, immutability, loud errors
# =============================================================================
def test_dispatch_reruns_no_gate_and_no_validation(monkeypatch, tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)

    def boom(*args, **kwargs):
        raise AssertionError("7AF must never call this")

    # 7AF imports none of these functions; the spies prove it calls none of them.
    monkeypatch.setattr(automation_gate_module, "evaluate_automation_gate", boom)
    monkeypatch.setattr(automation_pipeline_module, "evaluate_reviewed_connection_for_automation", boom)
    monkeypatch.setattr(fabrication_output_gate_module, "evaluate_fabrication_output_gate", boom)
    monkeypatch.setattr(reviewed_connection_validation_gate, "validate_reviewed_connection_assembly", boom)

    result = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=_recording_entry([]))
    assert result.output_status == OUTPUT_STATUS_GENERATED


def test_inputs_never_mutated_outputs_frozen_and_dispatch_deterministic(tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)
    assembly_before = pipeline.reviewed_assembly
    gate_before = gate

    first = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=_recording_entry([]))
    second = dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry=_recording_entry([]))

    assert first == second and repr(first) == repr(second)
    assert pipeline.reviewed_assembly is assembly_before  # never rebuilt, never replaced
    assert gate is gate_before
    assert pipeline.automation_gate_result.decision == AUTOMATION_DECISION_AUTO
    assert pipeline.validation_passed is True

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.output_status = OUTPUT_STATUS_GENERATION_FAILED
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(first, "_not_a_field", "boom")


def test_programming_errors_propagate_loudly(tmp_path):
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)

    with pytest.raises(TypeError):
        dispatch_fabrication_drawing("not a gate result", pipeline, tmp_path)
    with pytest.raises(TypeError):
        dispatch_fabrication_drawing(gate, "not a pipeline result", tmp_path)
    with pytest.raises(TypeError):
        dispatch_fabrication_drawing(gate, pipeline, tmp_path, drawing_entry="not callable")

    collection, project_result, pipelines, project_gate = _project_gate(_reviewed_package())
    with pytest.raises(TypeError):
        dispatch_project_fabrication_drawings("not a project gate", pipelines, tmp_path)
    with pytest.raises(TypeError):
        dispatch_project_fabrication_drawings(project_gate, [("RP-0001", pipeline)], tmp_path)
    with pytest.raises(ValueError):
        dispatch_project_fabrication_drawings(project_gate, {}, tmp_path)
    with pytest.raises(ValueError):
        dispatch_project_fabrication_drawings(project_gate, {"RP-0001": pipeline, "RP-0002": pipeline}, tmp_path)


def test_drawing_dispatch_module_imports_are_pure():
    source = Path(drawing_dispatch.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported == {
        "app.cad_engine.automation_gate",                # 7Z decision constants only — never its evaluate function
        "app.cad_engine.automation_pipeline",            # the pipeline RESULT type only — never re-run
        "app.cad_engine.fabrication_output_gate",        # the 7AE result types only — never re-evaluated
        "app.cad_engine.multi_member_connection",        # the multi-member assembly type (7AV)
        "app.cad_engine.reviewed_connection_assembly",   # the genuine assembly type
        "app.cad_engine.reviewed_connection_drawing_gate",  # the existing 7S drawing entry — the only generation seam
        "collections.abc",
        "dataclasses",
        "pathlib",
    }
    lowered = source.lower()
    for forbidden in ("supabase", "anthropic", "fastapi"):
        assert forbidden not in lowered


def test_module_imports_without_any_secret_environment():
    code = "import app.cad_engine.drawing_dispatch; print('ok')"
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
