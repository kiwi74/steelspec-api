"""
Milestone 7AO — PRODUCTION REVIEW UI INTEGRATION BOUNDARY.

Proves that the real SteelSpec application (app.main:app) now serves the
proven 7AN review UI through a single mount, while:

  - every existing route and its behavior are preserved;
  - the review UI remains a pure consumer of the 7AM view model, with all
    state changes flowing through the 7AJ public workflow boundary;
  - truthful 7AN error semantics survive the mount (stale, duplicate,
    unknown, generation failure, verification failure — never fake success);
  - the real Arkles capture renders and resolves through the mounted app;
  - no secrets reach rendered HTML and no bypass actions appear anywhere;
  - the pre-existing Supabase import failures are NOT worsened (the app is
    only imported here under a test-only configuration boundary, and every
    module that boundary introduces is removed from sys.modules again).

The import-time SUPABASE_URL requirement lives in app/config.py
(os.environ[...] at module import) — unchanged by 7AO. The 11 smoke-import
failures remain exactly as they were; the subprocess test below proves the
application imports and mounts correctly once the real configuration
boundary is provided.
"""

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.review_ui.render as render_module
import app.review_ui.session as session_module
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_PLATE_VALUE,
    TASK_PROVIDE_CONNECTION_IDENTITY,
)
from app.cad_engine.project_workflow import ProjectWorkflowState
from app.cad_engine.review_contract import build_connection_review_contract
from tests.test_exception_resolution import ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    CONNECTION_IDENTITIES,
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _resolve,
    _start,
    _state,
)
from tests.test_real_world_exception_proof import (
    HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE,
    _answer,
)

ROOT = Path(__file__).resolve().parents[1]

# The deliberately fake-but-well-formed configuration boundary used by the
# tests: supabase-py's create_client validates the key SHAPE at import, so the
# placeholder is a JWT-shaped string that is not a real credential (fake
# signature, no network is ever touched). The service-role value doubles as
# the secret marker that must never appear in rendered HTML.
TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_MARKER = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)

FORBIDDEN_ACTION_PHRASES = (
    "force approve", "force generate", "mark verified", "skip review",
    "override blocker", "bypass validation", "generate anyway",
    "<button>approve", "approve</button>", "<button>generate",
)


@pytest.fixture()
def real_app(monkeypatch):
    """The REAL application object (app.main:app), imported under the test
    configuration boundary. Teardown removes everything this import added
    to sys.modules, so the pre-existing smoke-import failures keep failing
    for exactly the same reason as before 7AO."""
    before = set(sys.modules)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", TEST_SERVICE_ROLE_MARKER)
    main = importlib.import_module("app.main")
    yield main.app
    for name in set(sys.modules) - before:
        del sys.modules[name]


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    """Revision 0 -> 3 through the GENUINE workflow (7AJ fixture)."""
    out = tmp_path_factory.mktemp("7ao-sequence")
    return _full_sequence(out), out


@pytest.fixture(scope="module")
def failures(tmp_path_factory):
    """The two genuine failure states, produced through the existing
    drawing_entry seam exactly as in 7AL/7AM/7AN."""

    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("simulated generator failure for the 7AO test")

    def garbage_entry(assembly, output_path, **kwargs):
        Path(output_path).write_bytes(b"this is deliberately not a pdf")

    out_gen = tmp_path_factory.mktemp("7ao-genfail")
    _, _, built_a, w0a = _start()
    w2a = _resolve(
        _resolve(w0a, built_a, "RP-0001", out_gen),
        built_a, "RP-0002", out_gen, drawing_entry=exploding_entry,
    )
    out_ver = tmp_path_factory.mktemp("7ao-verfail")
    _, _, built_b, w0b = _start()
    w2b = _resolve(
        _resolve(w0b, built_b, "RP-0001", out_ver),
        built_b, "RP-0002", out_ver, drawing_entry=garbage_entry,
    )
    return {
        "generation_failure": (w2a, out_gen),
        "verification_failure": (w2b, out_ver),
    }


# =============================================================================
# Form plumbing — the same genuine human-supplied payloads as 7AI/7AN,
# encoded exactly as the UI's own inputs expect.
# =============================================================================
def _encode_task(task, package_id):
    names = render_module.task_input_names(task)
    value = HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[task.task_type]
    if task.task_type == TASK_PROVIDE_CONNECTION_IDENTITY:
        value = CONNECTION_IDENTITIES[package_id]
    if task.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        marks, position, attachments = value
        return {
            names[0]: ",".join(marks),
            names[1]: position,
            names[2]: "\n".join(
                f"{a['member_mark']}|{a['surface_reference']}" for a in attachments
            ),
        }
    if task.answer_type in (ANSWER_PLATE_VALUE, ANSWER_HOLES_VALUE, ANSWER_LOCATION_VALUE):
        return {names[0]: "\n".join(f"{key}: {v}" for key, v in value.items())}
    if task.answer_type in (ANSWER_APPROVE_REVIEW, ANSWER_ACKNOWLEDGMENT):
        return {names[0]: ""}
    if task.answer_type == ANSWER_ATTACHMENTS_VALUE:
        return {names[0]: "\n".join(
            f"{a['member_mark']}|{a['surface_reference']}" for a in value
        )}
    return {names[0]: value}


def _form_for(workflow, package_id, **overrides):
    contract = build_connection_review_contract(workflow, package_id)
    form = {
        "revision": str(workflow.revision),
        "evidence": _answer(contract.tasks[0], None).evidence,
    }
    for task in contract.tasks:
        form.update(_encode_task(task, package_id))
    form.update(overrides)
    return form


def _card(page, marker):
    """The connection card containing `marker`, and nothing else."""
    start = page.index(marker)
    return page[start:page.index("</a>", start)]


def _bind(workflow, out_dir):
    session_module.bind_workflow(workflow, out_dir)


# =============================================================================
# Application integration — the real app object, its routes, and the mount.
# =============================================================================
class TestMountedApplication:
    def test_existing_routes_are_preserved_with_their_methods(self, real_app):
        paths = {r.path: r for r in real_app.routes}
        assert "/health" in paths
        assert "/extract/{project_id}" in paths
        assert "/generate-report/{project_id}" in paths
        assert "POST" in paths["/extract/{project_id}"].methods
        assert "POST" in paths["/generate-report/{project_id}"].methods
        client = TestClient(real_app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_review_ui_is_mounted_at_review(self, real_app):
        # Starlette represents the mount as a Mount route on /review.
        mounted = [r for r in real_app.routes if r.path == "/review"]
        assert len(mounted) == 1
        # The session is a process-global singleton shared with 7AN's tests:
        # clear it so the unbound page is genuinely what this test sees.
        session_module.clear_workflow()
        client = TestClient(real_app)
        response = client.get("/review/")
        assert response.status_code == 200
        assert "No review workflow is bound" in response.text

    def test_route_registration_matches_the_smoke_test_contract(self, real_app):
        # The pre-existing contract in tests/test_smoke_imports.py: the
        # three original paths must still be registered on the real app.
        paths = {r.path for r in real_app.routes}
        assert "/health" in paths
        assert "/extract/{project_id}" in paths
        assert "/generate-report/{project_id}" in paths


# =============================================================================
# Real Arkles through the mounted application.
# =============================================================================
@needs_real_capture
class TestRealArklesThroughMountedApp:
    def test_rev0_renders_through_the_mounted_app(self, real_app, sequence):
        states, out = sequence
        _bind(states[0], out)
        client = TestClient(real_app)
        page = client.get("/review/").text
        assert PROJECT_ID in page
        assert "Revision 0" in page
        assert "3 connections need review; 0 connections verified; 0 connections remain" in page
        for package_id in CANDIDATE_IDS:
            assert package_id in page
        assert page.count("Review →") == 3
        assert page.count("9 issues") >= 3
        detail = client.get("/review/connections/RP-0001").text
        for code in ARKLES_BLOCKER_CODES:
            assert code in detail
        assert f"source drawing {SOURCE_DRAWING_ID}; page 7" in detail
        assert "'M12'" in detail and "Ø" not in detail
        assert all(f"RP-0001-T0{i}" in detail for i in range(1, 9))
        assert "answer type MEMBER_POSITION_ATTACHMENTS" in detail
        assert 'name="revision" value="0"' in detail

    def test_genuine_resolution_through_the_mounted_app(self, real_app, tmp_path):
        # A fresh pristine workflow and a fresh artifacts dir, so the PDF
        # assertions see only what THIS submission produced.
        out_fresh = tmp_path / "7ao-resolve"
        _, _, built, w0 = _start()
        _bind(w0, out_fresh)
        client = TestClient(real_app)
        before = session_module.current_workflow()
        response = client.post(
            "/review/connections/RP-0001/resolve", data=_form_for(w0, "RP-0001"),
        )
        assert response.status_code == 200
        after = session_module.current_workflow()
        assert isinstance(after, ProjectWorkflowState)
        assert after is not before  # the WORKFLOW produced the next state
        assert after.revision == 1
        assert (_state(after, "RP-0001").decision,
                _state(after, "RP-0001").output_status,
                _state(after, "RP-0001").verification_status) == (
            "AUTO", "GENERATED", "VERIFIED",
        )
        # Every other connection is the SAME state object — untouched.
        assert after.connections[1] is before.connections[1]
        assert after.connections[2] is before.connections[2]
        assert _state(after, "RP-0002").decision == "REVIEW"
        assert _state(after, "RP-0003").decision == "REVIEW"
        # The page rendered from the workflow's own next state.
        page = client.get("/review/").text
        assert "Revision 1" in page
        assert "2 connections need review; 1 connection verified; 0 connections remain" in page
        row1 = _card(page, "CONN-ARKLES-001")
        assert "Automated" in row1 and "Generated" in row1 and "Verified" in row1
        pdfs = sorted(Path(out_fresh).glob("*-fabrication.pdf"))
        assert [p.name for p in pdfs] == ["CONN-ARKLES-001-fabrication.pdf"]

    def test_refresh_through_the_mounted_app(self, real_app, sequence):
        import dataclasses
        states, out = sequence
        _bind(states[1], out)
        view_before = dataclasses.asdict(session_module.project_view())
        client = TestClient(real_app)
        response = client.post("/review/refresh")
        assert response.status_code == 200
        assert "Revision 1" in response.text
        refreshed = session_module.current_workflow()
        assert refreshed.revision == 1
        assert all(
            current is previous
            for current, previous in zip(refreshed.connections, states[1].connections)
        )
        assert dataclasses.asdict(session_module.project_view()) == view_before


# =============================================================================
# Truthful error semantics survive the mount.
# =============================================================================
@needs_real_capture
class TestErrorSemanticsThroughMountedApp:
    def test_stale_resolution_refused_through_the_mounted_app(self, real_app, sequence):
        states, out = sequence
        _bind(states[0], out)
        client = TestClient(real_app)
        client.post("/review/connections/RP-0001/resolve", data=_form_for(states[0], "RP-0001"))
        current = session_module.current_workflow()
        assert current.revision == 1
        response = client.post(
            "/review/connections/RP-0002/resolve",
            data=_form_for(current, "RP-0002", revision="0"),
        )
        assert response.status_code == 200
        assert "This review is out of date" in response.text
        assert "at revision 1" in response.text and "Nothing was processed" in response.text
        assert "/refresh" in response.text  # the required refresh action
        # The workflow state is untouched; the UI did not pretend success.
        w = session_module.current_workflow()
        assert w.revision == 1
        assert _state(w, "RP-0002").decision == "REVIEW"
        recovered = client.post("/review/refresh")
        assert "Revision 1" in recovered.text
        assert "Needs review" in _card(recovered.text, "RP-0002")

    def test_duplicate_resolution_refused_through_the_mounted_app(self, real_app, tmp_path):
        out_dup = tmp_path / "7ao-duplicate"
        _, _, built, w0 = _start()
        _bind(w0, out_dup)
        client = TestClient(real_app)
        first = client.post(
            "/review/connections/RP-0001/resolve", data=_form_for(w0, "RP-0001"),
        )
        assert "1 connection verified" in first.text
        current = session_module.current_workflow()
        response = client.post(
            "/review/connections/RP-0001/resolve", data=_form_for(current, "RP-0001"),
        )
        assert "Resolution refused" in response.text
        assert "already processed" in response.text
        assert session_module.current_workflow().revision == 1
        assert len(sorted(Path(out_dup).glob("*-fabrication.pdf"))) == 1

    def test_unknown_package_refused_through_the_mounted_app(self, real_app, tmp_path):
        # Cross-project resolutions are unexpressible THROUGH THE UI by
        # construction: the session only ever submits package ids of the
        # bound workflow, and any other id is refused as unknown before
        # the workflow is called (the workflow's own CrossProjectResolution
        # refusal is proven at the 7AJ layer).
        out_unknown = tmp_path / "7ao-unknown"
        _, _, built, w0 = _start()
        _bind(w0, out_unknown)
        client = TestClient(real_app)
        page = client.get("/review/connections/RP-999").text
        assert "Unknown connection" in page and "RP-999" in page
        response = client.post(
            "/review/connections/RP-999/resolve", data={"revision": "0"},
        )
        assert "Resolution refused" in response.text
        assert session_module.current_workflow().revision == 0

    def test_generation_failure_displayed_through_the_mounted_app(self, real_app, failures):
        workflow, out = failures["generation_failure"]
        _bind(workflow, out)
        client = TestClient(real_app)
        page = client.get("/review/").text
        assert "1 connection needs review; 1 connection verified; 1 connection remains" in page
        row2 = _card(page, "CONN-ARKLES-002")
        assert "Generation failed" in row2 and "No artifact" in row2
        assert "Verified" not in row2 and "Generated" not in row2
        detail = client.get("/review/connections/RP-0002").text
        assert "Generation failed" in detail
        assert "No actions available for this connection." in detail

    def test_verification_failure_displayed_through_the_mounted_app(self, real_app, failures):
        workflow, out = failures["verification_failure"]
        _bind(workflow, out)
        client = TestClient(real_app)
        page = client.get("/review/").text
        row2 = _card(page, "CONN-ARKLES-002")
        assert "Generated" in row2 and "Verification failed" in row2
        assert "Verified" not in row2
        detail = client.get("/review/connections/RP-0002").text
        assert "Verification failed" in detail
        assert "CONN-ARKLES-002-fabrication.pdf" in detail  # artifact on record, not success
        assert "No actions available for this connection." in detail


# =============================================================================
# Security and bypass boundaries through the mounted app.
# =============================================================================
@needs_real_capture
class TestSecurityBoundaries:
    def test_no_secrets_reach_rendered_html(self, real_app, monkeypatch, sequence):
        # A service-role-style secret is in the process environment; the
        # render chain must never read or echo it (structurally guaranteed
        # by the 7AN purity tests — this is the behavioral counterpart).
        states, out = sequence
        _bind(states[0], out)
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", TEST_SERVICE_ROLE_MARKER)
        client = TestClient(real_app)
        pages = [
            client.get("/review/").text,
            client.get("/review/connections/RP-0001").text,
            session_module.render_project_page(),
            session_module.render_connection_page("RP-0001"),
        ]
        for page in pages:
            assert TEST_SERVICE_ROLE_MARKER not in page
            assert "SUPABASE_SERVICE_ROLE_KEY" not in page
            assert "SUPABASE_URL" not in page
            assert TEST_SUPABASE_URL not in page

    def test_no_bypass_actions_through_the_mounted_app(self, real_app, sequence):
        states, out = sequence
        _bind(states[0], out)
        client = TestClient(real_app)
        pages = [
            client.get("/review/").text,
            client.get("/review/connections/RP-0001").text,
        ]
        for page in pages:
            lowered = page.lower()
            for phrase in FORBIDDEN_ACTION_PHRASES:
                assert phrase not in lowered
        # The only forms point at the two legitimate workflow operations.
        assert client.get("/review/").text.count("<form") == 1  # Refresh only
        assert client.get("/review/connections/RP-0001").text.count("<form") == 1  # Resolution only


# =============================================================================
# Architecture boundary — app.main mounts ONE review UI and touches the
# workflow through nothing but that mount.
# =============================================================================
MAIN_FORBIDDEN = (
    "app.review_ui.session", "app.review_ui.render",
    "resolve_project_connection", "refresh_project_workflow",
    "build_project_review_contract", "build_connection_review_contract",
    "render_project_view", "render_connection_view",
    "projectworkflowstate", "humanresolution", "bind_workflow",
    # §5 — no second persistent UI-side state may appear in the web layer.
    "ui_project_state", "ui_connection_state", "ui_review_status",
    "ui_verified", "ui_blocked", "ui_revision",
)


def _code_source(path):
    """The module source with docstrings blanked — scans judge CODE, not prose."""
    text = Path(path).read_text()
    lines = text.splitlines(keepends=True)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and ast.get_docstring(node, clean=False) is not None:
            first = node.body[0]
            for index in range(first.lineno - 1, first.end_lineno):
                lines[index] = ""
    return "".join(lines).lower()


def test_app_main_mounts_exactly_one_review_ui_and_nothing_else():
    path = ROOT / "app" / "main.py"
    source = Path(path).read_text()
    assert source.count("app.mount(") == 1
    assert "app.mount(\"/review\", review_app)" in source
    code = _code_source(path)
    assert "app.review_ui.web" in code
    for token in MAIN_FORBIDDEN:
        assert token not in code, token
    # The mount happens after the existing app/middleware setup and after
    # the single import of the review application.
    assert code.index("app.review_ui.web") < code.index("app.mount(")


def test_application_imports_and_mounts_in_a_clean_interpreter():
    """§8 startup proof: app.main imports and mounts the review UI under the
    correct configuration boundary — no server, no network, no Railway."""
    script = (
        "import os\n"
        f"os.environ['SUPABASE_URL'] = {TEST_SUPABASE_URL!r}\n"
        f"os.environ['SUPABASE_SERVICE_ROLE_KEY'] = {TEST_SERVICE_ROLE_MARKER!r}\n"
        "from app.main import app\n"
        "paths = {r.path for r in app.routes}\n"
        "assert '/health' in paths\n"
        "assert '/extract/{project_id}' in paths\n"
        "assert '/generate-report/{project_id}' in paths\n"
        "assert '/review' in paths\n"
        "print('7AO-STARTUP-OK')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "7AO-STARTUP-OK" in completed.stdout
    assert "KeyError" not in completed.stderr
    assert "SUPABASE_URL" not in completed.stdout
