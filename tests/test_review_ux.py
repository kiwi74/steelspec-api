"""
Milestone 7AP — REVIEW UI PRODUCTION UX / EXCEPTION REVIEW EXPERIENCE.

These tests pin the presentation contract of the redesigned review UI,
while the HARD ARCHITECTURAL RULE stays the one 7AN/7AO proved: the pages
consume `ProjectReviewView` / `ConnectionReviewView` only, every number
and label on the page comes from the view (never recomputed), AI values
and evidence are byte-exact, blocker/task relationships are the view's
own, failures stay failures, and the only actions are the contract's.

Everything here is presentational: no new workflow states, no new
business rules, no new blockers, no new answer types, no bypass actions.
"""

import dataclasses

import pytest
from fastapi.testclient import TestClient

import app.review_ui.render as render_module
import app.review_ui.session as session_module
import app.review_ui.web as web_module
from app.cad_engine.exception_resolution import (
    ANSWER_APPROVE_REVIEW,
    ANSWER_AUTOMATION_CONFIRMATION,
)
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import (
    BlockerView,
    render_connection_view,
    render_project_view,
)
from tests.test_exception_resolution import ARKLES_BLOCKER_CODES
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _resolve,
    _start,
    _state,
)
from tests.test_review_ui import _card, _form_for


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    """Revision 0 -> 3 through the GENUINE workflow, plus the output dir."""
    out = tmp_path_factory.mktemp("7ap-sequence")
    return _full_sequence(out), out


@pytest.fixture(scope="module")
def failures(tmp_path_factory):
    """The two genuine failure states through the existing drawing_entry seam."""

    def exploding_entry(assembly, output_path, **kwargs):
        raise RuntimeError("simulated generator failure for the 7AP test")

    def garbage_entry(assembly, output_path, **kwargs):
        from pathlib import Path
        Path(output_path).write_bytes(b"this is deliberately not a pdf")

    out_gen = tmp_path_factory.mktemp("7ap-genfail")
    _, _, built_a, w0a = _start()
    w2a = _resolve(
        _resolve(w0a, built_a, "RP-0001", out_gen),
        built_a, "RP-0002", out_gen, drawing_entry=exploding_entry,
    )
    out_ver = tmp_path_factory.mktemp("7ap-verfail")
    _, _, built_b, w0b = _start()
    w2b = _resolve(
        _resolve(w0b, built_b, "RP-0001", out_ver),
        built_b, "RP-0002", out_ver, drawing_entry=garbage_entry,
    )
    return {
        "generation_failure": (w2a, out_gen),
        "verification_failure": (w2b, out_ver),
    }


def _project_view(workflow):
    return render_project_view(build_project_review_contract(workflow))


def _connection_view(workflow, package_id):
    return render_connection_view(build_connection_review_contract(workflow, package_id))


def _bind(workflow, out_dir):
    session_module.bind_workflow(workflow, out_dir)


# =============================================================================
# The project dashboard: identity, view-supplied stats, attention first.
# =============================================================================
@needs_real_capture
class TestDashboardPresentation:
    def test_masthead_shows_identity_revision_and_status(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        assert "SteelSpec review" in page
        assert f"<h1>{PROJECT_ID}</h1>" in page
        assert "Revision 0" in page
        # The status chip is the view's own status label.
        assert ">Needs review</span>" in page

    def test_stats_are_the_view_model_values_and_nothing_else(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        view = session_module.project_view()
        total = len(view.review_items) + len(view.completed_items)
        assert f"<strong>{total}</strong><span>connections</span>" in page
        assert f"<strong>{view.requiring_attention}</strong><span>need attention</span>" in page
        assert f"<strong>{view.verified}</strong><span>verified</span>" in page
        counts = view.counts
        assert (
            f"Counts: {counts.review} review · {counts.confirmation} confirmation"
            f" · {counts.auto} automated · {counts.blocked} blocked"
        ) in page
        # The brief's own wording: the view's summary line, verbatim.
        assert view.summary in page

    def test_attention_cards_use_the_contract_summary_verbatim(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        view = session_module.project_view()
        for connection in view.review_items:
            assert connection.summary in page
            assert connection.identity.package_id in page

    def test_dashboard_does_not_dump_blocker_details(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        view = session_module.project_view()
        for connection in list(view.review_items) + list(view.completed_items):
            for blocker in connection.blockers:
                # Titles and messages live on the detail page — the dashboard
                # carries only the contract's own summary (codes included).
                assert blocker.title not in page
                assert blocker.message not in page

    def test_attention_section_comes_first_and_holds_only_attention_items(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        assert page.index("Needs your attention") < page.index("Completed / verified")
        review_section = page[page.index("Needs your attention"):page.index("Completed / verified")]
        for package_id in CANDIDATE_IDS:
            assert package_id in review_section
        assert page.index("RP-0001") < page.index("RP-0002") < page.index("RP-0003")
        # 9 blockers each, straight from each view's own blockers.
        assert review_section.count("9 issues") == 3
        assert review_section.count("Review →") == 3

    def test_completed_section_shows_only_view_supplied_output(self, sequence):
        states, out = sequence
        _bind(states[1], out)
        page = session_module.render_project_page()
        completed = page[page.index("Completed / verified"):]
        card = _card(completed, "CONN-ARKLES-001")
        # The three statuses come from the view's own labels — no more, no less.
        assert "Automated" in card and "Generated" in card and "Verified" in card
        assert "issues" not in card  # nothing needs attention
        assert "Details →" in card
        # Review cards never claim verification.
        review_section = page[page.index("Needs your attention"):page.index("Completed / verified")]
        assert "Verified" not in review_section


# =============================================================================
# The connection detail page: identity -> attention -> evidence -> AI ->
# provenance -> tasks -> submit, everything from the view alone.
# =============================================================================
@needs_real_capture
class TestDetailPresentation:
    def test_identity_header_never_invents_a_connection_id(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        assert "<h1>RP-0001</h1>" in page
        assert ">Needs review</span>" in page  # decision chip from the view
        assert "<dt>Revision</dt><dd>0</dd>" in page
        # Rev 0 has no identity — the page says so honestly.
        assert "Connection ID not yet supplied" in page

    def test_sections_follow_the_review_order(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        order = [
            "Needs your attention",
            "Drawing evidence",
            "AI-extracted values",
            "Provenance",
            "Resolve connection",
        ]
        positions = [page.index(marker) for marker in order]
        assert positions == sorted(positions)
        assert page.index("Resolve connection") < page.index("Evidence note (optional)")

    def test_attention_banner_counts_and_blocker_content(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        view = _connection_view(states[0], "RP-0001")
        assert "Needs your attention" in page
        assert f"{len(view.blockers)} items need resolution" in page
        for blocker in view.blockers:
            # Title, message, severity and code — the view's own fields.
            assert blocker.title in page
            assert blocker.message in page
            assert blocker.code in page
            assert blocker.severity_label in page

    def test_blocker_task_links_are_the_views_own_relationship(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        view = _connection_view(states[0], "RP-0001")
        task_by_type = {task.task_type: task.task_id for task in view.tasks}
        linked = 0
        for blocker in view.blockers:
            if blocker.task_type is None:
                continue
            task_id = task_by_type[blocker.task_type]
            assert f'href="#task-{task_id}"' in page
            assert f'id="task-{task_id}"' in page
            linked += 1
        assert linked > 0
        # A blocker with NO task in the view gets NO link — the relationship
        # is never reconstructed from codes.
        orphan = dataclasses.replace(
            view, blockers=(
                BlockerView(
                    code="X-ORPHAN", title="No task attached", message="m",
                    severity="BLOCKING", severity_label="Blocking",
                    field=None, task_type=None,
                ),
            ),
        )
        orphan_page = render_module.render_connection_detail_page(
            _project_view(states[0]), orphan,
        )
        assert "#task-" not in orphan_page
        assert "No task attached" in orphan_page

    def test_drawing_evidence_is_verbatim(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        view = _connection_view(states[0], "RP-0001")
        assert view.evidence.evidence_text in page
        assert f"source drawing {SOURCE_DRAWING_ID}; page 7" in page
        # No guessed grid references or invented detail markers.
        assert "Grid reference" not in page

    def test_ai_values_are_labelled_observations_and_lossless(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0003")
        assert "AI-extracted values" in page
        assert "not confirmed engineering facts" in page
        # Byte-exact: no unit conversions, no decoration, no Ø.
        assert "SQ4 12mm" in page
        assert "12 mm hole" not in page
        assert "Ø" not in page
        assert "AIExtractedBolt" in page

    def test_provenance_section_shows_actual_values_or_nothing(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        rev0 = session_module.render_connection_page("RP-0001")
        assert "None recorded." in rev0
        # After the genuine resolution the view supplies the real provenance.
        view = _connection_view(states[1], "RP-0001")
        _bind(states[1], out)
        page = session_module.render_connection_page("RP-0001")
        assert "Provenance" in page
        for entry in view.provenance:
            assert entry.field in page
            assert entry.provenance_label in page

    def test_tasks_are_a_numbered_sequence_from_the_views_own_state(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        view = _connection_view(states[0], "RP-0001")
        assert len(view.tasks) == 8
        # Each task appears in the view's order (titles also occur inside
        # other tasks' descriptions, so ordering is pinned on the ids).
        positions = [page.index(f'id="task-{task.task_id}"') for task in view.tasks]
        assert positions == sorted(positions)
        assert page.count('id="task-RP-0001-T0') == 8
        # Completed state is the task's own resolved flag — all open at rev 0.
        assert page.count(">unanswered</span>") == 8
        assert page.count(">answered</span>") == 0

    def test_submit_area_has_one_primary_button_and_the_current_revision(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        assert 'name="revision" value="0"' in page
        assert 'action="/connections/RP-0001/resolve"' in page
        assert page.count('<button class="btn btn-primary">') == 1
        assert "<button class=\"btn btn-primary\">Resolve connection</button>" in page
        lowered = page.lower()
        for banned in ("<button>approve", "force approve", "force generate",
                       "mark verified", "override", "generate anyway"):
            assert banned not in lowered

    def test_unsupported_answer_type_is_stated_and_no_form_is_offered(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        view = _connection_view(states[0], "RP-0001")
        # The real Arkles tasks are all supported — nothing is unsupported.
        assert render_module.unsupported_tasks(view) == ()
        unsupported = dataclasses.replace(
            view, tasks=view.tasks + (
                type(view.tasks[0])(
                    task_id="RP-0001-T99", task_type="X", title="t", description="d",
                    field=None, required=True, resolved=False, current_value=None,
                    allowed_options=(), evidence_requirement="e",
                    resolution_value=None, resolution_evidence=None,
                    answer_type="FIELD_DECISION",
                ),
            ),
        )
        page = render_module.render_connection_detail_page(
            _project_view(states[0]), unsupported,
        )
        assert "FIELD_DECISION" in page
        assert "cannot be resolved" in page
        assert "<form" not in page  # never silently gathered

    def test_success_banner_only_when_the_view_reports_verified(self, sequence):
        states, out = sequence
        _bind(states[1], out)
        verified = session_module.render_connection_page("RP-0001")
        assert "Connection verified" in verified
        assert "Verified" in verified
        # The view now supplies the connection identity, so the page shows it.
        assert "CONN-ARKLES-001" in verified
        assert "Connection ID not yet supplied" not in verified
        # The still-open connection shows no success anywhere.
        open_page = session_module.render_connection_page("RP-0002")
        assert "Connection verified" not in open_page
        assert ">Needs review</span>" in open_page

    def test_failure_banners_show_the_failure_not_a_traceback(self, failures):
        workflow, out = failures["generation_failure"]
        _bind(workflow, out)
        page = session_module.render_connection_page("RP-0002")
        assert "Output status" in page
        assert "Generation failed" in page and "No artifact" in page
        assert "Connection verified" not in page
        assert "Traceback" not in page and "RuntimeError" not in page
        assert "No actions available for this connection." in page

    def test_stale_page_explains_and_offers_refresh(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        session_module.submit_resolution("RP-0001", _form_for(states[0], "RP-0001"))
        stale_form = _form_for(states[1], "RP-0002", revision="0")
        page = session_module.submit_resolution("RP-0002", stale_form)
        assert "This review is out of date" in page
        assert "Another change has updated this project" in page
        assert "at revision 1" in page and "Nothing was processed" in page
        assert "Refresh review" in page and "/refresh" in page
        assert "Traceback" not in page
        # The workflow state is untouched — the page never faked a success.
        assert session_module.current_workflow().revision == 1
        assert _state(session_module.current_workflow(), "RP-0002").decision == "REVIEW"

    def test_refresh_is_a_secondary_button_never_primary(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        assert '<button class="btn">Refresh</button>' in page
        # Nothing primary on the dashboard — the Refresh button is a plain one.
        assert '<button class="btn btn-primary"' not in page
        detail = session_module.render_connection_page("RP-0001")
        assert detail.count('<button class="btn btn-primary">') == 1  # only "Resolve connection"
        assert 'class="btn">Resolve connection' not in detail

    def test_back_to_project_navigation(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        detail = session_module.render_connection_page("RP-0001")
        assert '<a href="/">&#8592; Back to project</a>' in detail
        message = render_module.render_message_page("x", "y", refresh_form=True)
        assert '<a href="/">&#8592; Back to project</a>' in message

    def test_accessibility_markers(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_connection_page("RP-0001")
        view = _connection_view(states[0], "RP-0001")
        # Every control has a label bound to its id. (Tasks whose answer type
        # needs no input — a pure human sign-off — legitimately have none.)
        no_input_types = (ANSWER_APPROVE_REVIEW, ANSWER_AUTOMATION_CONFIRMATION)
        for task in view.tasks:
            for name in render_module.task_input_names(task):
                if task.answer_type in no_input_types:
                    assert f'<input' not in render_module._task_inputs(task)
                    continue
                assert f'<label for="{name}">' in page
                assert f'id="{name}"' in page
        assert '<label class="field-label" for="evidence">' in page
        # Buttons are buttons; the cards are anchors, not fake buttons.
        assert '<div class="btn' not in page
        # Keyboard focus is visible and the layout degrades to one column.
        assert ":focus-visible" in page
        assert "@media (max-width: 640px)" in page
        # Exactly one h1 per page.
        assert page.count("<h1>") == 1

    def test_all_styling_is_local_and_there_is_no_client_side_runtime(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        pages = [
            session_module.render_project_page(),
            session_module.render_connection_page("RP-0001"),
            render_module.render_message_page("x", "y", refresh_form=True),
            render_module.render_unbound_page(),
        ]
        for page in pages:
            assert "<style>" in page
            assert 'rel="stylesheet"' not in page
            assert "<script" not in page
            assert "https://" not in page
            assert "cdn" not in page.lower()
            assert "react" not in page.lower()

    def test_rendering_remains_deterministic(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        assert session_module.render_project_page() == session_module.render_project_page()
        assert session_module.render_connection_page("RP-0001") == \
            session_module.render_connection_page("RP-0001")


# =============================================================================
# §30 acceptance: the real Arkles capture, rev 0 -> genuine resolution.
# =============================================================================
@needs_real_capture
class TestRealArklesAcceptance:
    def test_rev0_dashboard_and_detail_show_the_full_exception_review(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.render_project_page()
        assert "3 connections need review; 0 connections verified; 0 connections remain" in page
        assert "<strong>3</strong><span>need attention</span>" in page
        assert "<strong>0</strong><span>verified</span>" in page
        assert page.count("9 issues") == 3
        detail = session_module.render_connection_page("RP-0001")
        assert "9 items need resolution" in detail
        assert "Connection ID not yet supplied" in detail
        assert "M12" in detail and "Ø" not in detail
        assert f"source drawing {SOURCE_DRAWING_ID}; page 7" in detail
        assert detail.count('id="task-RP-0001-T0') == 8
        for code in ARKLES_BLOCKER_CODES:
            assert code in detail

    def test_after_a_genuine_resolution_the_ui_reflects_the_workflow_truth(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        page = session_module.submit_resolution("RP-0001", _form_for(states[0], "RP-0001"))
        assert "Revision 1" in page
        assert "2 connections need review; 1 connection verified; 0 connections remain" in page
        completed = page[page.index("Completed / verified"):]
        card = _card(completed, "CONN-ARKLES-001")
        assert "Automated" in card and "Generated" in card and "Verified" in card
        review_section = page[page.index("Needs your attention"):page.index("Completed / verified")]
        assert "RP-0001" not in review_section
        assert "RP-0002" in review_section and "RP-0003" in review_section
        # The verified detail page: success banner, real identity, resolved
        # tasks — all from the workflow-produced view.
        detail = session_module.render_connection_page("RP-0001")
        assert "Connection verified" in detail
        assert "CONN-ARKLES-001" in detail
        assert detail.count(">answered</span>") == 8
        assert detail.count('id="task-RP-0001-T0') == 8
        assert "Provenance" in detail
        # RP-0002 is untouched by the resolution — still a full review.
        rp2 = session_module.render_connection_page("RP-0002")
        assert "Needs your attention" in rp2
        assert "9 items need resolution" in rp2
        assert "Connection ID not yet supplied" in rp2
        assert "Connection verified" not in rp2


# =============================================================================
# The UX renders over HTTP unchanged (web layer untouched by 7AP).
# =============================================================================
@needs_real_capture
class TestUXOverHTTP:
    def test_pages_serve_with_the_new_presentation(self, sequence):
        states, out = sequence
        _bind(states[0], out)
        client = TestClient(web_module.review_app)
        page = client.get("/").text
        assert "SteelSpec review" in page
        assert page.count("9 issues") == 3
        detail = client.get("/connections/RP-0001").text
        assert "Resolve connection" in detail
        response = client.post(
            "/connections/RP-0001/resolve", data=_form_for(states[0], "RP-0001"),
        )
        assert response.status_code == 200
        assert "1 connection verified" in response.text
        assert "Completed / verified" in response.text
