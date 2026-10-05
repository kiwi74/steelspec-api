"""
Milestone 7AN — REVIEW UI VERTICAL SLICE.

These tests prove the UI is exactly what the brief demands: a presentation
and submission boundary over the existing chain, with NO second source of
truth. Every page is rendered from a 7AM view model; every state change is
produced by the 7AJ workflow through its public API; failures are displayed
as failures; the only actions are the contract's own; and the real Arkles
capture flows through the UI from initial REVIEW state to a genuine
AUTO/GENERATED/VERIFIED connection.

The UI layer under test is app/review_ui (render.py — pure presentation;
session.py — the only workflow-touching layer; web.py — the FastAPI entry).
"""

import ast
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.review_ui.render as render_module
import app.review_ui.session as session_module
import app.review_ui.web as web_module
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_PLATE_VALUE,
    TASK_PROVIDE_CONNECTION_IDENTITY,
)
from app.cad_engine.project_workflow import (
    ProjectWorkflowState,
    resolve_project_connection,
)
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import (
    TaskView,
    render_connection_view,
    render_project_view,
)
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

# Scoped to button/control text so legitimate view content (e.g. the answer
# type "approve_review") cannot trip them.
FORBIDDEN_ACTION_NAMES = (
    "force approve", "force generate", "mark verified", "skip review",
    "override blocker", "bypass validation", "generate anyway",
    "<button>approve", "approve</button>", "<button>generate",
)
ALLOWED_ACTION_WORDS = ("Review", "Resolve", "Refresh")


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    """Revision 0 -> 3 through the GENUINE workflow, plus the output dir."""
    out = tmp_path_factory.mktemp("7an-sequence")
    return _full_sequence(out), out


@pytest.fixture(scope="module")
def failures(tmp_path_factory):
    """The two genuine failure states: RP-0001 resolved for real, then RP-0002
    hits the existing drawing_entry seam."""
    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("simulated generator failure for the 7AN test")

    def garbage_entry(assembly, output_path, **kwargs):
        Path(output_path).write_bytes(b"this is deliberately not a pdf")

    out_gen = tmp_path_factory.mktemp("7an-genfail")
    _, _, built_a, w0a = _start()
    w2a = _resolve(
        _resolve(w0a, built_a, "RP-0001", out_gen),
        built_a, "RP-0002", out_gen, drawing_entry=exploding_entry,
    )
    out_ver = tmp_path_factory.mktemp("7an-verfail")
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
# Form plumbing shared with the slice: the SAME human-supplied answers the
# 7AI/7AJ proofs use, encoded exactly as the UI's inputs expect.
# =============================================================================
def _encode_task(task, package_id):
    """One contract task -> {form field: text} using the UI's own naming."""
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
    """The complete resolution form for one connection, as the page renders it."""
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
# The real Arkles vertical slice, end to end.
# =============================================================================
@needs_real_capture
class TestReviewUI:
    # -------------------------------------------------------------------------
    # Initial project — real values, nothing rewritten.
    # -------------------------------------------------------------------------
    def test_project_page_at_rev0_shows_the_real_arkles_state(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        assert PROJECT_ID in page
        assert "Revision 0" in page
        assert ">Needs review</span>" in page  # the view's own status label
        assert "3 connections need review; 0 connections verified; 0 connections remain" in page
        # Counts come from the view's own CountsView — never recomputed.
        assert "Counts: 3 review · 0 confirmation · 0 automated · 3 blocked" in page
        assert "<strong>3</strong><span>connections</span>" in page
        assert "<strong>3</strong><span>need attention</span>" in page
        assert "<strong>0</strong><span>verified</span>" in page
        review_section = page[page.index("Needs your attention"):]
        for package_id in CANDIDATE_IDS:
            assert package_id in review_section
        assert page.index("RP-0001") < page.index("RP-0002") < page.index("RP-0003")
        assert review_section.count("Needs review") >= 3
        # The attention cards show the view's own blocker counts (9 each).
        assert page.count("9 issues") == 3
        assert page.count("Review →") == 3

    def test_connection_detail_at_rev0_exposes_the_real_data(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        assert "<h1>RP-0001</h1>" in page
        assert ">Needs review</span>" in page  # decision chip from the view
        assert "Needs your attention" in page
        assert "9 items need resolution" in page
        for code in ARKLES_BLOCKER_CODES:
            assert code in page
        # Evidence exactly as the capture has it — nothing manufactured.
        assert f"source drawing {SOURCE_DRAWING_ID}; page 7" in page
        # AI values byte-exact: no decoration, no unit inference, no Ø.
        assert "'M12'" in page and "Ø" not in page and "M12 bolt" not in page
        # Provenance: none invented at rev 0.
        assert "None recorded." in page
        # All eight real tasks, in the view's order, with their answer types.
        assert all(f"RP-0001-T0{i}" in page for i in range(1, 9))
        assert "answer type MEMBER_POSITION_ATTACHMENTS" in page
        assert "answer type PLATE_VALUE" in page
        assert "answer type HOLES_VALUE" in page
        assert "answer type LOCATION_VALUE" in page
        assert "answer type ACKNOWLEDGMENT" in page
        assert "answer type APPROVE_REVIEW" in page
        assert "answer type CONNECTION_IDENTITY" in page
        # The resolution form carries the CURRENT revision for the workflow.
        assert 'name="revision" value="0"' in page
        assert "Resolve connection" in page
        # Rev 0 has no identity yet — the page says so, it never invents one.
        assert "Connection ID not yet supplied" in page

    def test_ai_values_are_lossless_on_the_pages(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0003")
        assert "SQ4 12mm" in page
        assert "12 mm hole" not in page and "Ø" not in page
        # The AI's own representation strings survive untouched.
        assert "AIExtractedBolt" in page

    def test_no_bypass_actions_appear_anywhere(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        project_page = session_module.render_project_page()
        detail_page = session_module.render_connection_page("RP-0001")
        stale_page = render_module.render_message_page(
            "Stale revision — resolution refused", "x", refresh_form=True,
        )
        for page in (project_page, detail_page, stale_page):
            lowered = page.lower()
            for forbidden in FORBIDDEN_ACTION_NAMES:
                assert forbidden not in lowered
            assert "<button>Generate" not in page
            assert "name=\"verify\"" not in page.lower()
        # The only forms point at the two legitimate workflow operations.
        assert project_page.count("<form") == 1  # Refresh only
        assert "/refresh" in project_page
        assert detail_page.count("<form") == 1  # Resolution only
        assert "/connections/RP-0001/resolve" in detail_page
        # Every action word used is one of the contract's own actions.
        for action_word in ALLOWED_ACTION_WORDS:
            assert action_word in project_page or action_word in detail_page

    # -------------------------------------------------------------------------
    # Genuine resolution through the UI -> the workflow does the state change.
    # -------------------------------------------------------------------------
    def test_genuine_resolution_through_the_ui(self, tmp_path):
        # A fresh pristine workflow and a fresh artifacts dir, so the PDF
        # assertions see only what THIS submission produced.
        out_fresh = tmp_path / "ui-resolve"
        _, _, built, w0 = _start()
        _bind(w0, out_fresh)
        before = session_module.current_workflow()
        page = session_module.submit_resolution("RP-0001", _form_for(w0, "RP-0001"))
        after = session_module.current_workflow()
        assert isinstance(after, ProjectWorkflowState)
        assert after is not before  # the workflow produced the next state
        assert after.revision == 1
        rp1 = _state(after, "RP-0001")
        assert (rp1.decision, rp1.output_status, rp1.verification_status) == (
            "AUTO", "GENERATED", "VERIFIED",
        )
        # Every other connection is the SAME state object — untouched.
        assert after.connections[1] is before.connections[1]
        assert after.connections[2] is before.connections[2]
        assert _state(after, "RP-0002").decision == "REVIEW"
        assert _state(after, "RP-0003").decision == "REVIEW"
        # The refreshed-by-workflow page reflects exactly that.
        assert "2 connections need review; 1 connection verified; 0 connections remain" in page
        completed = page[page.index("Completed / verified"):]
        row1 = _card(completed, "CONN-ARKLES-001")
        assert "Automated" in row1 and "Generated" in row1 and "Verified" in row1
        review_section = page[page.index("Needs your attention"):page.index("Completed / verified")]
        assert "RP-0002" in review_section and "RP-0003" in review_section
        assert review_section.index("RP-0002") < review_section.index("RP-0003")
        assert "RP-0001" not in review_section
        # A real artifact exists, named by the workflow's own identity.
        pdfs = sorted(Path(out_fresh).glob("*-fabrication.pdf"))
        assert [p.name for p in pdfs] == ["CONN-ARKLES-001-fabrication.pdf"]

    def test_state_change_came_from_the_workflow_not_from_the_ui(self, sequence, tmp_path):
        states, out = sequence
        # The UI is bound to the pristine workflow and renders it.
        _bind(states[0], out)
        page_before = session_module.render_project_page()
        assert "Revision 0" in page_before
        # An OUTSIDE actor changes state through the real workflow API only.
        _, _, built, _ = _start()
        new_workflow = _resolve(states[0], built, "RP-0001", out)
        # The UI is handed nothing but the new workflow...
        _bind(new_workflow, out)
        # ...and its page changes because the workflow changed.
        page_after = session_module.render_project_page()
        assert page_after != page_before
        assert "Revision 1" in page_after
        assert "1 connection verified" in page_after
        assert session_module.current_workflow() is new_workflow
        # No UI-specific state injection exists to consult: running the fixed
        # chain workflow -> contract -> view -> page as a pure function gives
        # the same page as the session render.
        assert render_module.render_project_page(
            render_project_view(build_project_review_contract(new_workflow))
        ) == page_after

    def test_other_connections_remain_unchanged_after_one_resolution(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        session_module.submit_resolution("RP-0001", _form_for(states[0], "RP-0001"))
        w1 = session_module.current_workflow()
        # Both remaining connections still demand review, with the SAME state
        # objects the initial workflow carried.
        assert w1.connections[1] is states[0].connections[1]
        assert w1.connections[2] is states[0].connections[2]
        page = session_module.render_project_page()
        assert page.count("Review →") == 2

    # -------------------------------------------------------------------------
    # Refresh.
    # -------------------------------------------------------------------------
    def test_refresh_regenerates_without_mutating_workflow_state(self, sequence):
        states, out = sequence
        _bind(states[1], out)
        view_before = dataclasses.asdict(session_module.project_view())
        page = session_module.refresh()
        refreshed = session_module.current_workflow()
        # Refresh never advances the revision and never changes the view.
        assert refreshed.revision == states[1].revision == 1
        assert all(
            current is previous
            for current, previous in zip(refreshed.connections, states[1].connections)
        )
        assert dataclasses.asdict(session_module.project_view()) == view_before
        assert "Revision 1" in page

    # -------------------------------------------------------------------------
    # Stale revision and duplicate refusals.
    # -------------------------------------------------------------------------
    def test_stale_resolution_is_refused_shown_and_recoverable(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        session_module.submit_resolution("RP-0001", _form_for(states[0], "RP-0001"))
        assert session_module.current_workflow().revision == 1
        # A form from an OLD page (revision 0) for the NEXT connection.
        stale_form = _form_for(states[1], "RP-0002", revision="0")
        page = session_module.submit_resolution("RP-0002", stale_form)
        assert "This review is out of date" in page
        assert "at revision 1" in page and "Nothing was processed" in page
        assert "Refresh review" in page
        # The workflow state is untouched; the UI did not pretend success.
        w = session_module.current_workflow()
        assert w.revision == 1
        assert _state(w, "RP-0002").decision == "REVIEW"
        # Refresh recovers the current truth.
        recovered = session_module.refresh()
        assert "Revision 1" in recovered
        assert _card(recovered, "RP-0002") and "Needs review" in _card(recovered, "RP-0002")

    def test_duplicate_resolution_is_refused(self, tmp_path):
        # Fresh workflow: resolve RP-0001 for real, then try again at the
        # CURRENT revision — the workflow's one-shot semantics refuse it.
        out_dup = tmp_path / "ui-duplicate"
        _, _, built, w0 = _start()
        _bind(w0, out_dup)
        first = session_module.submit_resolution("RP-0001", _form_for(w0, "RP-0001"))
        assert "1 connection verified" in first
        current = session_module.current_workflow()
        page = session_module.submit_resolution(
            "RP-0001", _form_for(current, "RP-0001"),
        )
        assert "Resolution refused" in page
        assert "already processed" in page
        assert session_module.current_workflow().revision == 1
        assert len(sorted(Path(out_dup).glob("*-fabrication.pdf"))) == 1

    # -------------------------------------------------------------------------
    # Failure states are displayed as failures.
    # -------------------------------------------------------------------------
    def test_generation_failure_is_displayed_as_failure(self, failures):
        workflow, out = failures["generation_failure"]
        _bind(workflow, out)
        page = session_module.render_project_page()
        assert "1 connection needs review; 1 connection verified; 1 connection remains" in page
        card2 = _card(page, "CONN-ARKLES-002")
        assert "Automated" in card2
        assert "Generation failed" in card2 and "No artifact" in card2
        assert "Verified" not in card2 and "Generated" not in card2
        # The genuinely verified connection still shows its real truth.
        card1 = _card(page, "CONN-ARKLES-001")
        assert "Verified" in card1
        # Detail page: same truth, and no actions invented for the failure.
        detail = session_module.render_connection_page("RP-0002")
        assert "Generation failed" in detail and "No artifact" in detail
        assert "Verified" not in detail.replace("No actions available", "")
        assert "No actions available for this connection." in detail

    def test_verification_failure_is_displayed_as_failure(self, failures):
        workflow, out = failures["verification_failure"]
        _bind(workflow, out)
        page = session_module.render_project_page()
        card2 = _card(page, "CONN-ARKLES-002")
        assert "Generated" in card2 and "Verification failed" in card2
        assert "Verified" not in card2
        # The failed artifact stays on record — file existence is not success.
        detail = session_module.render_connection_page("RP-0002")
        assert "Verification failed" in detail
        assert "CONN-ARKLES-002-fabrication.pdf" in detail
        assert "No actions available for this connection." in detail

    # -------------------------------------------------------------------------
    # Unknown packages, unbound sessions, unsupported answer types.
    # -------------------------------------------------------------------------
    def test_unknown_connection_page(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-999")
        assert "Unknown connection" in page and "RP-999" in page
        refused = session_module.submit_resolution("RP-999", {"revision": "0"})
        assert "Resolution refused" in refused
        assert session_module.current_workflow().revision == 0

    def test_unbound_session_pages(self, sequence):
        session_module.clear_workflow()
        assert "No review workflow is bound" in session_module.render_project_page()
        assert "No review workflow is bound" in session_module.submit_resolution(
            "RP-0001", {"revision": "0"},
        )
        assert "No review workflow is bound" in session_module.refresh()
        _bind(sequence[0][0], sequence[1])  # leave the session bound for later tests

    def test_unsupported_answer_type_is_never_silently_accepted(self):
        task = TaskView(
            task_id="T-X", task_type="X", title="t", description="d", field=None,
            required=True, resolved=False, current_value=None, allowed_options=(),
            evidence_requirement="e", resolution_value=None, resolution_evidence=None,
            answer_type="FIELD_DECISION",
        )
        inputs = render_module._task_inputs(task)
        assert "not supported" in inputs and "FIELD_DECISION" in inputs
        with pytest.raises(session_module.ResolutionInputError) as error:
            session_module.parse_task_answer(task, {"task_T-X": "whatever"})
        assert "FIELD_DECISION" in str(error.value)
        assert "FIELD_DECISION" not in render_module.SUPPORTED_ANSWER_TYPES

    # -------------------------------------------------------------------------
    # The input plumbing round-trips the fixture answers without inventing,
    # dropping or transforming a single value.
    # -------------------------------------------------------------------------
    def test_form_inputs_round_trip_all_arkles_task_payloads(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        contract = build_connection_review_contract(states[0], "RP-0001")
        for task in contract.tasks:
            form = _encode_task(task, "RP-0001")
            parsed = session_module.parse_task_answer(task, form)
            expected = HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[task.task_type]
            if task.task_type == TASK_PROVIDE_CONNECTION_IDENTITY:
                expected = CONNECTION_IDENTITIES["RP-0001"]
            assert parsed == expected, task.task_type

    def test_malformed_inputs_are_refused_before_the_workflow_runs(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        contract = build_connection_review_contract(states[0], "RP-0001")
        grouped = next(
            t for t in contract.tasks if t.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS
        )
        plate = next(t for t in contract.tasks if t.answer_type == ANSWER_PLATE_VALUE)
        identity = next(t for t in contract.tasks if t.answer_type == ANSWER_CONNECTION_IDENTITY)
        names = render_module.task_input_names
        with pytest.raises(session_module.ResolutionInputError):
            session_module.parse_task_answer(grouped, {
                names(grouped)[0]: "L2,L3", names(grouped)[1]: "END",
                names(grouped)[2]: "L2|END\nbroken-line",
            })
        with pytest.raises(session_module.ResolutionInputError):
            session_module.parse_task_answer(grouped, {
                names(grouped)[0]: "", names(grouped)[1]: "END",
                names(grouped)[2]: "L2|END",
            })
        with pytest.raises(session_module.ResolutionInputError):
            session_module.parse_task_answer(plate, {names(plate)[0]: ""})
        with pytest.raises(session_module.ResolutionInputError):
            session_module.parse_task_answer(identity, {names(identity)[0]: "  "})
        # A refused submission never reaches the workflow: state unchanged.
        bad_form = _form_for(states[0], "RP-0001")
        bad_form[names(plate)[0]] = ""
        page = session_module.submit_resolution("RP-0001", bad_form)
        assert "Resolution not submitted" in page
        assert session_module.current_workflow().revision == 0
        # A non-numeric revision is refused too.
        page = session_module.submit_resolution(
            "RP-0001", _form_for(states[0], "RP-0001", revision="abc"),
        )
        assert "Resolution not submitted" in page
        assert session_module.current_workflow().revision == 0

    # -------------------------------------------------------------------------
    # Determinism and ordering.
    # -------------------------------------------------------------------------
    def test_rendering_is_deterministic(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        view = session_module.project_view()
        assert render_module.render_project_page(view) == render_module.render_project_page(view)
        assert session_module.render_project_page() == session_module.render_project_page()
        detail_a = session_module.render_connection_page("RP-0001")
        _bind(states[0], out)  # re-bind the same workflow
        assert session_module.render_connection_page("RP-0001") == detail_a

    def test_ordering_follows_the_view_model(self, sequence):
        states, out = sequence
        _bind(states[1], out)
        page = session_module.render_project_page()
        review_section = page[page.index("Needs your attention"):page.index("Completed / verified")]
        assert review_section.index("RP-0002") < review_section.index("RP-0003")
        completed = page[page.index("Completed / verified"):]
        assert completed.index("CONN-ARKLES-001") < len(completed)

    # -------------------------------------------------------------------------
    # The web entry point works end to end.
    # -------------------------------------------------------------------------
    def test_routes_serve_the_slice(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        client = TestClient(web_module.review_app)
        response = client.get("/")
        assert response.status_code == 200
        assert PROJECT_ID in response.text
        response = client.get("/connections/RP-0001")
        assert response.status_code == 200
        assert "AUTOMATION_BLOCKER_MEMBER_IDENTITY" in response.text
        response = client.post("/refresh")
        assert response.status_code == 200
        assert "Revision 0" in response.text
        response = client.post(
            "/connections/RP-0001/resolve", data=_form_for(states[0], "RP-0001"),
        )
        assert response.status_code == 200
        assert session_module.current_workflow().revision == 1
        assert "1 connection verified" in response.text
        assert Path(out, "CONN-ARKLES-001-fabrication.pdf").exists()
        response = client.get("/connections/RP-999")
        assert response.status_code == 200
        assert "Unknown connection" in response.text

    def test_stale_route_submission_shows_the_stale_page(self, sequence):
        states, out = sequence
        _bind(states[1], out)
        client = TestClient(web_module.review_app)
        response = client.post(
            "/connections/RP-0002/resolve", data=_form_for(states[1], "RP-0002", revision="0"),
        )
        assert response.status_code == 200
        assert "This review is out of date" in response.text
        assert session_module.current_workflow().revision == 1


# =============================================================================
# The render layer consumes ONLY the view model; the session layer touches the
# workflow only through its public API.
# =============================================================================
RENDER_EXPECTED_IMPORTS = {
    "html", "app.cad_engine.exception_resolution", "app.cad_engine.review_view_model",
    # J18: the page-exception surface renders a view model of its own, and the
    # one action constant it maps to a control.
    "app.cad_engine.page_exception_contract", "app.cad_engine.page_exception_view_model",
}
SESSION_EXPECTED_IMPORTS = {
    "json", "pathlib", "app.cad_engine.exception_resolution",
    "app.cad_engine.project_workflow", "app.cad_engine.review_contract",
    "app.cad_engine.review_view_model", "app.review_ui",
    # J18: the page-exception contract + view model, and the two record readers
    # that own the halves of the committed record. The pipeline is NOT here: the
    # retry contract is injected, so this layer cannot contain a retry algorithm.
    "app.cad_engine.page_exception_contract", "app.cad_engine.page_exception_view_model",
    "app.validation.page_coverage", "app.validation.parse_failures",
}
WEB_EXPECTED_IMPORTS = {"fastapi", "fastapi.responses", "app.review_ui"}


def _source_without_docstrings(path):
    """The source with every module/class/function docstring blanked, so
    forbidden-token scans judge CODE and not prose."""
    text = Path(path).read_text()
    lines = text.splitlines(keepends=True)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and ast.get_docstring(node, clean=False) is not None:
            first = node.body[0]
            for index in range(first.lineno - 1, first.end_lineno):
                lines[index] = ""
    return "".join(lines).lower()


def _import_set(path):
    tree = ast.parse(Path(path).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def _called_names(path):
    tree = ast.parse(Path(path).read_text())
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _forbidden(source, tokens):
    for token in tokens:
        assert token not in source, token


def test_render_layer_import_purity():
    path = Path(render_module.__file__)
    source = _source_without_docstrings(path)
    assert _import_set(path) == RENDER_EXPECTED_IMPORTS
    _forbidden(source, (
        "project_workflow", "review_contract", "resolve_project_connection",
        "refresh_project_workflow", "build_project_review_contract",
        "build_connection_review_contract", "render_project_view",
        "render_connection_view", "fastapi", "anthropic", "supabase", "getenv(",
        "random.", "time.", "uuid", "socket", "os.", "sys.",
        "pathlib", "open(", "testclient",
    ))
    allowed = {
        "render_project_page", "render_connection_detail_page", "render_message_page",
        "render_unbound_page", "task_input_names", "unsupported_tasks",
        "_task_inputs", "_task_sections", "_blocker_items", "_connection_card",
        "_extracted_items", "_output_section", "_chip", "_stat",
        "_project_action_controls", "_labelled", "_text_input", "_textarea",
        "_esc", "_esc_attr", "_page",
        # J25's atoms: the provenance rows and evidence card shared with the connection
        # detail page, and the read-only connection section the review page repeats.
        "_provenance_rows", "_evidence_block", "_review_section", "_review_sections",
        # J18's page-exception atoms.
        "_page_exception_notice", "_page_exception_card",
        # J29's annotation-evidence atom: one table row per recorded occurrence.
        "_annotation_evidence_rows",
        # J19's back link: it names the surface's own root, which is "/" unless a
        # mounted production surface is the one rendering.
        "_backlink",
        "isinstance", "len", "str", "tuple", "frozenset",
    }
    outside = _called_names(path) - allowed
    assert outside == set(), outside


def test_session_layer_import_purity_and_no_private_access():
    path = Path(session_module.__file__)
    source = _source_without_docstrings(path)
    assert _import_set(path) == SESSION_EXPECTED_IMPORTS
    _forbidden(source, (
        "fastapi", "anthropic", "supabase", "getenv(", "random.",
        "time.", "uuid", "socket", "os.", "sys.", "requests", "httpx",
    ))
    # The session layer must not reach into any private attribute of the
    # workflow: everything flows through the public functions only. (The one
    # tolerated dunder is type(...).__name__ in bind_workflow's TypeError.)
    tree = ast.parse(Path(path).read_text())
    private = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr.startswith("_") and node.attr != "__name__"
    }
    assert private == set(), private
    # The only workflow operations called are the two public boundaries.
    workflow_calls = _called_names(path) & {
        "resolve_project_connection", "refresh_project_workflow",
        "build_project_review_contract", "build_connection_review_contract",
        "render_project_view", "render_connection_view",
    }
    assert workflow_calls == {"resolve_project_connection", "refresh_project_workflow",
                              "build_project_review_contract", "build_connection_review_contract",
                              "render_project_view", "render_connection_view"}


def test_web_layer_import_purity():
    path = Path(web_module.__file__)
    source = _source_without_docstrings(path)
    assert _import_set(path) == WEB_EXPECTED_IMPORTS
    _forbidden(source, (
        "anthropic", "getenv(", "random.", "time.", "uuid", "socket",
        "os.", "sys.", "resolve_project_connection", "refresh_project_workflow",
        "build_project_review_contract", "build_connection_review_contract",
        "render_project_view", "render_connection_view", "humanresolution",
    ))


def test_review_ui_imports_without_any_secret_environment():
    env = {
        key: value for key, value in os.environ.items()
        if not any(secret in key.upper() for secret in ("ANTHROPIC", "SUPABASE", "FIREWORKS", "OPENAI"))
    }
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import app.review_ui"],
        env=env, cwd=root, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "SUPABASE" not in completed.stderr and "ANTHROPIC" not in completed.stderr
