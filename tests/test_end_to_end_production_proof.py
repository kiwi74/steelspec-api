"""
Milestone 7AH — tests for the end-to-end production proof
(app.cad_engine.end_to_end_production_proof), the integration milestone
that runs the complete SteelSpec production loop twice through ONLY the
genuine existing stages and records exactly where either run reached:

    Path A (AUTO):   a complete reviewed connection fixture (real member
                     context: rows, placements, section matcher)
                     -> 7AA evaluate_reviewed_connection_for_automation()
                     -> 7Z decision AUTO
                     -> 7AE evaluate_fabrication_output_gate()
                     -> 7AF dispatch_fabrication_drawing()
                     -> a real PDF on disk
                     -> 7AG verify_drawing_artifact() against the actual
                        reviewed assembly
                     -> expected outcome VERIFIED

    Path B (ARKLES): the REAL Arkles Strand page-extraction capture
                     -> 7Y intake_page_extractions()
                     -> 7X collection
                     -> 7AB evaluate_project_for_automation()
                     -> 7AA per candidate WITHOUT member context (none
                        exists for the raw candidates — none is invented)
                     -> 7Z REVIEW for every candidate
                     -> 7AE evaluate_project_fabrication_output_gate()
                     -> 7AF dispatch_project_fabrication_drawings()
                     -> zero files, no output directory
                     -> 7AG verify_project_drawing_outputs()
                     -> expected outcome NO_ARTIFACT

These tests prove:

  - The acceptance matrix for both paths — every stage decision, output
    status and verification status recorded verbatim from the genuine
    stage results, with the proof's own checks all passing.
  - The complete artifact lifecycle for AUTO (no artifact before dispatch
    -> 7AF creates -> real non-empty file -> 7AG reads and verifies ->
    SHA-256 recorded -> page count recorded -> identity and geometry
    checks pass) and the blocked lifecycle for Arkles (clean directory ->
    full project pipeline -> all REVIEW -> zero files -> the output
    directory is never created -> all NO_ARTIFACT).
  - The 7Y scope facts and the genuine 7Z blocker codes preserved through
    the chain (the TEST hard-codes the known Arkles acceptance values;
    the proof module reads them from the actual results).
  - The anti-false-positive properties: an absent artifact can never read
    VERIFIED, a wrong assembly identity fails verification, Arkles can
    never accidentally generate even with an output directory supplied,
    zero connections is never reported as a successful production run,
    and a proof whose AUTO chain does not genuinely reach AUTO cannot
    pass.
  - Determinism, loud input validation, input immutability, and import
    purity (no Supabase/AI/network/CAD imports, no drawing generator
    import, no hard-coded engineering values).

FIXTURE PROVENANCE: the AUTO fixture is the established synthetic 7Z
fixture (actual schema keys, not produced by any AI) with the genuine
7O/7R member-context fixtures; the ARKLES path uses the real captured
page-extraction JSON through the existing 7Y intake. Nothing here
re-implements any validation or verification rule — every assertion
reads the genuine 7AG/7AB/7AE/7AF results.

A passing proof does NOT mean production-ready, Arkles-ready or
engineering-approved; it means the chain executed and the records agree.
"""
import ast
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.cad_engine.end_to_end_production_proof as end_to_end
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    dispatch_fabrication_drawing,
)
from app.cad_engine.drawing_output_verification import (
    CHECK_FAILED,
    CHECK_FILE_EXISTS,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_NO_ARTIFACT,
    VERIFICATION_STATUS_VERIFIED,
    verify_drawing_artifact,
)
from app.cad_engine.end_to_end_production_proof import run_end_to_end_production_proof
from app.cad_engine.fabrication_output_gate import evaluate_fabrication_output_gate
from tests.test_automation_gate import _reviewed_package
from tests.test_fabrication_output_gate import _auto_pipeline
from tests.test_project_automation import _member_placements, _member_rows
from tests.test_project_extraction_intake import (
    CAPTURE_PATH,
    load_capture,
    needs_real_capture,
    real_known_marks,
)
from tests.test_real_multi_member_cad import make_multi_member_matcher

REPO = Path(__file__).resolve().parents[1]
PROJECT_ID = "PROJ-7AH"
SOURCE_DRAWING_ID = "ARKLES-STRAND"
DRAWING_SET_PAGE_COUNT = 41

KNOWN_SCOPE_LINE = (
    "7Y scope: pages_received = 30; parse_failures = 6; "
    "pages_not_analysed = 11; drawing_set_page_count = 41"
)

# The known Arkles acceptance value: the exact nine genuine 7Z blocker codes
# the real capture produces for every candidate, in 7AE's own order. The
# PROOF reads these from the actual automation results; the TEST asserts the
# known set because that is an acceptance condition.
EXPECTED_BLOCKER_CODES = (
    AUTOMATION_BLOCKER_REVIEW_STATUS,
    AUTOMATION_BLOCKER_MEMBER_IDENTITY,
    AUTOMATION_BLOCKER_POSITION,
    AUTOMATION_BLOCKER_PLATE,
    AUTOMATION_BLOCKER_HOLE_DIAMETER,
    AUTOMATION_BLOCKER_LOCATION,
    AUTOMATION_BLOCKER_ATTACHMENT,
    AUTOMATION_BLOCKER_SPECIFICATION,
    AUTOMATION_BLOCKER_VALIDATION,
)


def _run_proof(tmp_path, *, arkles_pages=None, **overrides):
    """The genuine AUTO fixture + the real Arkles capture through the proof's public API."""
    kwargs = dict(
        output_dir=tmp_path,
        auto_fixture=_reviewed_package(),
        arkles_pages=load_capture(CAPTURE_PATH) if arkles_pages is None else arkles_pages,
        arkles_project_id=PROJECT_ID,
        arkles_source_drawing_id=SOURCE_DRAWING_ID,
        arkles_known_member_marks=real_known_marks(),
        arkles_drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )
    kwargs.update(overrides)
    return run_end_to_end_production_proof(**kwargs)


# =============================================================================
# The acceptance matrix — both paths, every stage decision recorded verbatim
# =============================================================================
@needs_real_capture
def test_auto_path_acceptance_matrix(tmp_path):
    """7AA PASS / 7Z AUTO / 7AE AUTO / 7AF GENERATED / 7AG VERIFIED -> PASS."""
    result = _run_proof(tmp_path)
    auto = result.auto_path

    assert result.passed
    assert auto.path_name == "AUTO"
    assert auto.expected_outcome == VERIFICATION_STATUS_VERIFIED
    assert auto.actual_outcome == VERIFICATION_STATUS_VERIFIED
    assert auto.passed and auto.failures == ()

    # Path A supplies a bare reviewed package — no 7X candidate id exists.
    assert auto.review_package_id == ()
    assert auto.automation_decision == (AUTOMATION_DECISION_AUTO,)
    assert auto.output_status == (OUTPUT_STATUS_GENERATED,)
    assert auto.verification_status == (VERIFICATION_STATUS_VERIFIED,)
    assert len(auto.generated_files) == 1
    assert auto.generated_files[0].endswith("-fabrication.pdf")

    outcome = auto.connection_outcomes[0]
    assert outcome.review_package_id is None
    assert outcome.connection_id == "CONN-REAL-UB-CAD-001-L2"
    assert outcome.automation_decision == AUTOMATION_DECISION_AUTO
    assert outcome.output_status == OUTPUT_STATUS_GENERATED
    assert outcome.verification_status == VERIFICATION_STATUS_VERIFIED

    # Every genuine stage ran without exception, and the decisions are the
    # genuine stage values, not proof-invented ones.
    for line in ("7AA: pipeline completed", "7AE: gate evaluated",
                 "7AF: dispatch recorded", "7AG: verification recorded"):
        assert line in auto.summary, auto.summary
    assert "7Z decision: AUTO" in auto.summary
    assert "7AE decision: AUTO" in auto.summary


@needs_real_capture
def test_arkles_path_acceptance_matrix(tmp_path):
    """REVIEW / REVIEW / REVIEW / BLOCKED_REVIEW / NO_ARTIFACT -> PASS."""
    result = _run_proof(tmp_path)
    arkles = result.arkles_path

    assert arkles.path_name == "ARKLES"
    assert arkles.expected_outcome == VERIFICATION_STATUS_NO_ARTIFACT
    assert arkles.actual_outcome == VERIFICATION_STATUS_NO_ARTIFACT
    assert arkles.passed and arkles.failures == ()

    assert arkles.review_package_id == ("RP-0001", "RP-0002", "RP-0003")
    assert arkles.automation_decision == (AUTOMATION_DECISION_REVIEW,) * 3
    assert arkles.output_status == (OUTPUT_STATUS_BLOCKED_REVIEW,) * 3
    assert arkles.verification_status == (VERIFICATION_STATUS_NO_ARTIFACT,) * 3
    assert arkles.generated_files == ()
    assert len(arkles.connection_outcomes) == 3
    for outcome in arkles.connection_outcomes:
        assert outcome.automation_decision == AUTOMATION_DECISION_REVIEW
        assert outcome.output_status == OUTPUT_STATUS_BLOCKED_REVIEW
        assert outcome.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert outcome.generated_files == ()
        assert outcome.connection_id is None  # raw Arkles candidates carry no invented identity

    # The 7Y scope facts are carried verbatim from the actual intake result.
    assert KNOWN_SCOPE_LINE in arkles.summary
    assert "7AB decisions: AUTO = 0; CONFIRM = 0; REVIEW = 3" in arkles.summary
    assert "7AE decision: REVIEW" in arkles.summary
    assert "7AE eligible outputs: 0" in arkles.summary

    # The known blocker set, read from the actual 7AE result, each firing for
    # all three candidates — the proof itself hard-codes none of it.
    blocker_lines = [line for line in arkles.summary if line.startswith("7AE blocker: ")]
    assert blocker_lines == [f"7AE blocker: {code} x 3" for code in EXPECTED_BLOCKER_CODES]


# =============================================================================
# The complete artifact lifecycle (AUTO) and blocked lifecycle (Arkles)
# =============================================================================
@needs_real_capture
def test_auto_artifact_lifecycle(tmp_path):
    """No artifact before dispatch -> 7AF creates -> real non-empty file -> 7AG reads and
    verifies -> SHA-256 recorded -> page count recorded -> identity and geometry pass."""
    assert not (tmp_path / "auto").exists()          # no artifact before dispatch
    auto = _run_proof(tmp_path).auto_path            # the full genuine chain runs
    (pdf,) = auto.generated_files
    artifact = Path(pdf)
    assert artifact.exists() and artifact.is_file() and artifact.stat().st_size > 0
    assert auto.verification_status == (VERIFICATION_STATUS_VERIFIED,)

    sha_line = next(line for line in auto.summary if line.startswith("7AG SHA-256: "))
    digest = sha_line.split(": ", 1)[1]
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)

    page_line = next(line for line in auto.summary if line.startswith("7AG page count: "))
    assert int(page_line.split(": ", 1)[1]) >= 1

    assert "7AG DRAWING_CONTENT_PRESENT: PASSED" in auto.summary
    assert "7AG IDENTITY_VERIFIABLE: PASSED" in auto.summary
    assert "7AG GEOMETRY_FIELDS_VERIFIABLE: PASSED" in auto.summary
    assert f"7AG artifact: {artifact}" in auto.summary
    # The verification ran against the pipeline's OWN reviewed assembly.
    assert "7AG verification status: VERIFIED" in auto.summary


@needs_real_capture
def test_arkles_blocked_lifecycle(tmp_path):
    """Clean directory -> full project pipeline -> all REVIEW -> 7AE -> 7AF -> zero files ->
    the output directory is never created -> 7AG -> all NO_ARTIFACT."""
    assert not (tmp_path / "arkles").exists()
    result = _run_proof(tmp_path)
    arkles = result.arkles_path

    assert arkles.automation_decision == (AUTOMATION_DECISION_REVIEW,) * 3
    assert arkles.output_status == (OUTPUT_STATUS_BLOCKED_REVIEW,) * 3
    assert arkles.generated_files == ()
    assert arkles.verification_status == (VERIFICATION_STATUS_NO_ARTIFACT,) * 3
    assert "7AF output statuses: ('BLOCKED_REVIEW', 'BLOCKED_REVIEW', 'BLOCKED_REVIEW')" in \
        arkles.summary
    assert "7AF generated files: 0" in arkles.summary
    assert "7AG statuses: ('NO_ARTIFACT', 'NO_ARTIFACT', 'NO_ARTIFACT')" in arkles.summary

    # The blocked dispatch wrote nothing: the directory it would have created
    # does not exist, and the proof's own check recorded that fact.
    assert not (tmp_path / "arkles").exists()
    assert "7AF: blocked dispatch created no output directory" not in arkles.failures
    # Every generated file in the whole proof belongs to the AUTO path.
    assert result.generated_files == result.auto_path.generated_files


# =============================================================================
# Anti-false-positive properties
# =============================================================================
def test_absent_artifact_can_never_become_verified(tmp_path):
    """7AF claiming GENERATED with an absent file can never make 7AG read VERIFIED."""
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)
    dispatch = dispatch_fabrication_drawing(gate, pipeline, tmp_path / "stage")
    assert dispatch.output_status == OUTPUT_STATUS_GENERATED

    lying = dataclasses.replace(
        dispatch, generated_files=(tmp_path / "stage" / "absent-fabrication.pdf",))
    result = verify_drawing_artifact(lying, assembly=pipeline.reviewed_assembly)
    assert result.verification_status == VERIFICATION_STATUS_FAILED
    assert next(check for check in result.checks
                if check.code == CHECK_FILE_EXISTS).status == CHECK_FAILED
    assert result.verification_status != VERIFICATION_STATUS_VERIFIED


def test_wrong_assembly_identity_cannot_verify(tmp_path):
    """A genuine PDF verified against the WRONG assembly fails — identity is never assumed."""
    pipeline = _auto_pipeline()
    gate = evaluate_fabrication_output_gate(pipeline)
    dispatch = dispatch_fabrication_drawing(gate, pipeline, tmp_path / "stage")

    wrong = dataclasses.replace(
        pipeline.reviewed_assembly,
        member_b=dataclasses.replace(
            pipeline.reviewed_assembly.member_b,
            geometry=dataclasses.replace(pipeline.reviewed_assembly.member_b.geometry, mark="L9"),
        ),
    )
    result = verify_drawing_artifact(dispatch, assembly=wrong)
    assert result.verification_status == VERIFICATION_STATUS_FAILED
    geometry_check = next(check for check in result.checks
                          if check.code == CHECK_GEOMETRY_FIELDS_VERIFIABLE)
    assert geometry_check.status == CHECK_FAILED
    assert "MEMBER B L9" in geometry_check.detail
    # The proof itself verifies against the pipeline's OWN reviewed assembly,
    # so a wrong identity can never slip through it either.


@needs_real_capture
def test_arkles_cannot_accidentally_generate_even_with_output_dir(tmp_path):
    """Arkles generates ZERO files even though an output directory was supplied."""
    result = _run_proof(tmp_path)
    arkles = result.arkles_path
    assert arkles.generated_files == ()
    assert not (tmp_path / "arkles").exists()
    assert arkles.output_status == (OUTPUT_STATUS_BLOCKED_REVIEW,) * 3
    assert result.generated_files == result.auto_path.generated_files


def test_zero_connections_is_not_a_successful_production_run(tmp_path):
    """Zero connections must not read as a successful run — the existing empty-project
    semantics apply, and the proof fails the path loudly."""
    result = _run_proof(tmp_path, arkles_pages=())
    arkles = result.arkles_path

    assert arkles.passed is False
    assert arkles.actual_outcome == "FAILED_AT_7AB"
    assert "7AB: at least one connection was supplied" in arkles.failures
    assert arkles.generated_files == ()
    assert arkles.verification_status == ()

    # The AUTO path genuinely passed; the proof still reports NOT passed overall.
    assert result.auto_path.passed
    assert result.passed is False
    assert any("AUTO path: PASSED" in line for line in result.summary)
    assert any("ARKLES path: NOT PASSED" in line for line in result.summary)


def test_auto_path_fails_when_the_genuine_chain_does_not_reach_auto(tmp_path):
    """The proof is not vacuously green: without member context the genuine chain stays
    REVIEW/NO_ARTIFACT and the AUTO path fails its acceptance checks."""
    result = _run_proof(
        tmp_path,
        arkles_pages=(),
        member_rows=None,
        member_placements=None,
        section_matcher=None,
    )
    auto = result.auto_path
    assert auto.passed is False
    assert "7Z: decision is AUTO" in auto.failures
    assert "7AE: decision is AUTO" in auto.failures
    assert auto.output_status == (OUTPUT_STATUS_BLOCKED_REVIEW,)
    assert auto.verification_status == (VERIFICATION_STATUS_NO_ARTIFACT,)
    assert auto.actual_outcome == VERIFICATION_STATUS_NO_ARTIFACT
    assert auto.generated_files == ()
    assert result.passed is False


# =============================================================================
# Discipline — determinism, loud errors, immutability, import purity
# =============================================================================
@needs_real_capture
def test_proof_is_deterministic(tmp_path):
    first = _run_proof(tmp_path)
    (pdf,) = first.auto_path.generated_files
    first_bytes = Path(pdf).read_bytes()          # captured before the next run overwrites it
    second = _run_proof(tmp_path)
    second_bytes = Path(pdf).read_bytes()

    # Every field except the recorded artifact hash is identical across runs.
    def without_hashes(path):
        return dataclasses.replace(
            path,
            summary=tuple(line for line in path.summary
                          if not line.startswith("7AG SHA-256: ")),
        )
    assert without_hashes(first.auto_path) == without_hashes(second.auto_path)
    assert first.arkles_path == second.arkles_path
    assert first.generated_files == second.generated_files

    # Each run's recorded SHA-256 is the genuine hash of the artifact that run
    # produced — the recording never drifts from the real bytes. (The generated
    # PDF carries a creation timestamp, so two generations are two different
    # files; the proof must say so, not pretend otherwise.)
    recorded_hashes = [
        next(line for line in run.auto_path.summary if line.startswith("7AG SHA-256: "))
        for run in (first, second)
    ]
    assert recorded_hashes[0].split(": ", 1)[1] == hashlib.sha256(first_bytes).hexdigest()
    assert recorded_hashes[1].split(": ", 1)[1] == hashlib.sha256(second_bytes).hexdigest()


def _proof_kwargs(tmp_path):
    return dict(
        output_dir=tmp_path,
        auto_fixture=_reviewed_package(),
        arkles_pages=[],
        arkles_project_id=PROJECT_ID,
        arkles_source_drawing_id=SOURCE_DRAWING_ID,
        arkles_known_member_marks=(),
        arkles_drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
        member_rows=_member_rows(),
        member_placements=_member_placements(),
        section_matcher=make_multi_member_matcher(),
    )


def test_proof_rejects_invalid_inputs_loudly(tmp_path):
    base = _proof_kwargs(tmp_path)
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "output_dir": 123})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "auto_fixture": "not a package"})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "arkles_pages": "not pages"})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "arkles_pages": [{"page_number": 1}, "nope"]})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "arkles_project_id": None})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "arkles_source_drawing_id": 7})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "arkles_drawing_set_page_count": "41"})
    with pytest.raises(TypeError):
        run_end_to_end_production_proof(**{**base, "arkles_drawing_set_page_count": True})


@needs_real_capture
def test_inputs_are_not_mutated(tmp_path):
    pages = load_capture(CAPTURE_PATH)
    before = json.dumps(pages)
    _run_proof(tmp_path, arkles_pages=pages)
    assert json.dumps(pages) == before


ALLOWED_IMPORTS = {
    "app.cad_engine.automation_gate",
    "app.cad_engine.automation_pipeline",
    "app.cad_engine.connection_review_package",
    "app.cad_engine.drawing_dispatch",
    "app.cad_engine.drawing_output_verification",
    "app.cad_engine.fabrication_output_gate",
    "app.cad_engine.project_automation",
    "app.cad_engine.project_extraction_intake",
    "collections.abc",
    "dataclasses",
    "pathlib",
}

# The proof must never import databases, AI, the network, CAD/PDF libraries or
# the drawing generator itself, and must hard-code no engineering values.
FORBIDDEN_TOKENS = (
    "supabase", "anthropic", "fastapi", "sqlalchemy", "cadquery", "reportlab",
    "ezdxf", "pypdf", "pdf2image", "requests", "httpx",
    "reviewed_connection_drawing_gate",
    "310ub", "250pfc", "real-ub", "conn-real", "m12", "sq4",
)


def test_module_imports_are_pure_and_hard_code_no_engineering_values():
    module_path = Path(end_to_end.__file__)
    tree = ast.parse(module_path.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports == ALLOWED_IMPORTS

    lowered = module_path.read_text().lower()
    for token in FORBIDDEN_TOKENS:
        assert token not in lowered, token


def test_module_imports_in_a_subprocess_without_secret_environment():
    code = "import app.cad_engine.end_to_end_production_proof; print('ok')"
    env = {key: value for key, value in os.environ.items()
           if key not in {"SUPABASE_URL", "SUPABASE_KEY", "ANTHROPIC_API_KEY"}}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
