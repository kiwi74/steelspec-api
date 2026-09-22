"""
Milestone 7B8 — REAL-WORLD DETERMINISTIC STAND-IN SESSION PROOF.

Proves the assistant_session module — the genuine CALLER of the committed 7B7
assistant message boundary — against the real 51-candidate Selby workload.
The stand-in defined in the module is the only representation of any future
external assistant: normal, unavailable, slow, malformed, contradictory,
hostile, empty and oversized behaviour must ALL stay outside engineering
state, and the trusted review record must stay byte-identical before and
after any session. The authoritative mutation path (the reviewer's explicit
resolution through the existing 7AC/7AJ workflow) is the only thing that may
change the record, and it must behave exactly as before.

Real-world fixtures are the same genuine captures the whole 7Y..7B7 chain
uses; the 7B6 assistance view and the 7B7 boundary are consumed through their
public APIs only. Nothing here modifies any product module or any existing
fixture or test file.
"""

import ast
import dataclasses
from collections import Counter
from pathlib import Path

import pytest

import app.cad_engine.assistant_session as session_module
from app.cad_engine.assistant_message_boundary import (
    DISPLAY_PREFIX,
    NO_SOURCE_EVIDENCE_TEXT,
    ROLE_DESCRIBE_OBSERVATION,
    ROLE_DRAFT_ACKNOWLEDGMENT,
    ROLE_EVIDENCE_NAVIGATION,
    ROLE_EXPLAIN_TASK,
    AssistantRequest,
    AssistantResponse,
    build_assistant_request,
    parse_assistant_response,
    render_assistant_message,
    response_context_matches,
)
from app.cad_engine.assistant_session import (
    OUTCOME_ATTEMPTED_RESPONSE,
    OUTCOME_TIME_BUDGET_EXCEEDED,
    OUTCOME_UNAVAILABLE,
    PROFILE_CONTRADICTORY,
    PROFILE_EMPTY,
    PROFILE_HOSTILE,
    PROFILE_MALFORMED,
    PROFILE_NORMAL,
    PROFILE_OVERSIZED,
    PROFILE_SLOW,
    PROFILE_UNAVAILABLE,
    PROFILES,
    RESULT_ASSISTANT_UNAVAILABLE,
    RESULT_CONTEXT_MISMATCH,
    RESULT_DISPLAY_MESSAGE,
    RESULT_MALFORMED_RESPONSE,
    RESULT_TIME_BUDGET_EXCEEDED,
    SESSION_MARKER,
    AssistantSession,
    SessionRecord,
    StandInAssistant,
    StandInContent,
    StandInOutcome,
    build_session_requests,
    render_session,
    run_session,
    run_session_turn,
    stand_in_respond,
)
from app.cad_engine.exception_resolution import (
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
    TASK_PROVIDE_PLATE,
    TASK_SELECT_POSITION,
    apply_human_resolution,
)
from app.cad_engine.project_workflow import resolve_project_connection
from app.cad_engine.review_contract import build_project_review_contract
from app.cad_engine.reviewer_assistance import ReviewerAssistance
from app.cad_engine.reviewer_workload_reduction import (
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_ASSISTABLE,
    CATEGORY_EVIDENCE_LOOKUP,
    CATEGORY_HUMAN_ENGINEERING_DECISION,
    build_reviewer_workload_baseline,
)
from tests.test_real_world_assistant_message_boundary import (
    ADVERSARIAL_OUTPUTS,
    EXPLAIN_BASE_FIELDS,
    EXPLAIN_HUMAN_FIELDS,
    RP0001_CONFIRM_VALUE,
    RP0008_HOLE_READING,
    RP0009_HOLE_READING,
    RP0033_MATERIAL_ANCHOR,
    RP0045_PLATE_READING,
)
from tests.test_real_world_exception_workload import EXPECTED_IDS
from tests.test_real_world_human_exception_review import (
    MISSING_MATERIAL_IDS,
    RP0009_PDF_NAME,
    RP0009_PDF_SIZE,
    RP0009_RESOLVED_PROVENANCE,
    _fresh,
    _material_workflow,
    _resolve_full_rp0009,
)
from tests.test_real_world_reviewer_assistance import (
    _assistance,
    _snapshot,
    _task,
)

MODULE_FILE = (
    Path(__file__).resolve().parents[1] / "app" / "cad_engine"
    / "assistant_session.py"
)
SESSION_SOURCE = MODULE_FILE.read_text()

# The permission rows the 7B7 boundary releases per task-scoped role (asserted
# here at session level: the session's sweep must release nothing beyond them).
DESCRIBE_FIELDS = {"task_id", "task_type", "category", "current_value", "provenance"}
DRAFT_FIELDS = {"task_id", "task_type", "category", "proposal", "reviewer_remaining"}
NAVIGATION_FIELDS = {"task_id", "task_type", "category", "source_anchor"}

# Review verdicts a session failure must never silently become.
VERDICTS = {"REVIEW", "AUTO", "VERIFIED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED"}

# Mutation verbs no function in the session module may carry.
FORBIDDEN_ACTION_NAMES = {
    "resolve", "apply", "submit", "approve", "confirm", "set_value",
    "set_provenance", "generate", "reconcile", "synthesize", "average",
    "choose", "pick", "rank", "prioritise", "triage", "write", "save",
}
# Modules the session module must not even be able to name.
FORBIDDEN_MODULE_FRAGMENTS = (
    "exception_resolution", "project_workflow", "review_contract",
    "project_automation", "fabrication", "drawing", "production_acceptance",
    "fabricator_acceptance",
)
# Library modules a pure deterministic module must never import.
FORBIDDEN_LIBRARY_IMPORTS = {
    "time", "random", "threading", "asyncio", "subprocess", "socket", "http",
    "urllib", "requests", "anthropic", "supabase", "os", "sys", "io", "json",
    "re", "pathlib", "datetime", "config",
}
ALLOWED_IMPORTS = {
    "dataclasses",
    "app.cad_engine.assistant_message_boundary",
    "app.cad_engine.reviewer_assistance",
    "app.cad_engine.reviewer_workload_reduction",
}


def _request_fields(request, name):
    return [field.value for field in request.fields if field.name == name]


def _descr(assistance, package_id, task_type):
    """One DESCRIBE request for a real assistable task, through the boundary."""
    task = _task(assistance, package_id, task_type)
    return build_assistant_request(
        assistance, role=ROLE_DESCRIBE_OBSERVATION,
        package_id=package_id, task_id=task.task_id)


@pytest.fixture(scope="module")
def real_assistance():
    return _assistance(_fresh())


@pytest.fixture(scope="module")
def material_assistance():
    return _assistance(_material_workflow())


@pytest.fixture(scope="module")
def normal_stand_in():
    return StandInAssistant(PROFILE_NORMAL)


# =============================================================================
# 1. The module surface: pure, frozen, no mutation API, no path to state.
# =============================================================================

class TestSessionModuleSurface:
    def test_imports_within_whitelist(self):
        modules = set()
        for node in ast.walk(ast.parse(SESSION_SOURCE)):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules <= ALLOWED_IMPORTS

    def test_no_io_or_network_imports(self):
        for node in ast.walk(ast.parse(SESSION_SOURCE)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN_LIBRARY_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_LIBRARY_IMPORTS

    def test_cannot_name_workflow_machinery(self):
        modules = set()
        for node in ast.walk(ast.parse(SESSION_SOURCE)):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            assert all(fragment not in module
                       for fragment in FORBIDDEN_MODULE_FRAGMENTS)

    def test_no_forbidden_action_names(self):
        defs = [node.name for node in ast.walk(ast.parse(SESSION_SOURCE))
                if isinstance(node, ast.FunctionDef)]
        for name in defs:
            for forbidden in FORBIDDEN_ACTION_NAMES:
                assert forbidden not in name

    def test_function_surface_is_exactly_the_session_api(self):
        defs = {node.name for node in ast.walk(ast.parse(SESSION_SOURCE))
                if isinstance(node, ast.FunctionDef)}
        assert defs == {
            "stand_in_respond", "build_session_requests", "run_session_turn",
            "run_session", "render_session", "_failure_render",
        }

    def test_records_are_frozen(self, real_assistance, normal_stand_in):
        record = run_session_turn(
            build_session_requests(real_assistance)[0], normal_stand_in)
        session = AssistantSession(revision=0, turn_count=1, records=(record,))
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.result_kind = "MUTATED"
        with pytest.raises(dataclasses.FrozenInstanceError):
            session.records = ()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.request = None
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.response = None
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.rendered_text = "mutated"
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.error_text = "mutated"

    def test_session_records_are_not_mappings_or_sequences(self):
        from collections.abc import Mapping
        record = SessionRecord(
            turn=0,
            request=AssistantRequest(
                role=ROLE_EXPLAIN_TASK, package_id=None, task_id=None,
                revision=0, reviewer_question="", fields=()),
            stand_in_kind=OUTCOME_UNAVAILABLE,
            result_kind=RESULT_ASSISTANT_UNAVAILABLE,
            rendered_text="text", error_text="", response=None)
        session = AssistantSession(revision=0, turn_count=1, records=(record,))
        assert not isinstance(record, Mapping)
        assert not isinstance(session, Mapping)
        assert not isinstance(record, (list, tuple))
        assert not isinstance(session, (list, tuple))

    def test_records_have_no_methods(self):
        tree = ast.parse(SESSION_SOURCE)
        record_classes = {"SessionRecord", "AssistantSession", "StandInContent",
                          "StandInOutcome", "StandInAssistant"}
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in record_classes:
                assert all(not isinstance(child, ast.FunctionDef)
                           for child in node.body)

    def test_profiles_are_an_unordered_frozenset(self):
        assert PROFILES == frozenset({
            PROFILE_NORMAL, PROFILE_UNAVAILABLE, PROFILE_SLOW, PROFILE_MALFORMED,
            PROFILE_CONTRADICTORY, PROFILE_HOSTILE, PROFILE_EMPTY,
            PROFILE_OVERSIZED,
        })

    def test_no_value_pattern_matching(self):
        assert "isdigit" not in SESSION_SOURCE
        assert "regex" not in SESSION_SOURCE.lower()
        assert "re.match" not in SESSION_SOURCE
        assert "re.search" not in SESSION_SOURCE

    def test_no_writes_or_calls(self):
        forbidden_calls = {
            "open", "print", "sleep", "input", "eval", "exec", "sorted",
            "reversed", "glob",
        }
        forbidden_attrs = {
            "read", "write", "read_text", "write_text", "read_bytes",
            "write_bytes", "mkdir", "unlink", "iterdir", "save",
        }
        for node in ast.walk(ast.parse(SESSION_SOURCE)):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in forbidden_calls
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in forbidden_attrs

    def test_slow_profile_needs_no_clock(self):
        # The SLOW profile declares the outcome without any timing import or
        # sleep call — the surface checks above pin the imports; the outcome is
        # the declaration itself.
        request = AssistantRequest(
            role=ROLE_EXPLAIN_TASK, package_id=None, task_id=None,
            revision=0, reviewer_question="", fields=())
        outcome = stand_in_respond(StandInAssistant(PROFILE_SLOW), request)
        assert outcome.kind == OUTCOME_TIME_BUDGET_EXCEEDED
        assert outcome.text == ""


# =============================================================================
# 2. The stand-in behaviour profiles.
# =============================================================================

class TestStandInProfiles:
    def test_unknown_profile_refused(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        with pytest.raises(ValueError, match="eight stand-in profiles"):
            stand_in_respond(StandInAssistant("NOT_A_PROFILE"), request)

    def test_non_stand_in_refused(self, real_assistance):
        with pytest.raises(TypeError, match="StandInAssistant"):
            stand_in_respond("not a stand-in", build_session_requests(
                real_assistance)[0])

    def test_non_request_refused(self, normal_stand_in):
        with pytest.raises(TypeError, match="AssistantRequest"):
            stand_in_respond(normal_stand_in, "not a request")

    def test_non_int_turn_refused(self, real_assistance, normal_stand_in):
        with pytest.raises(TypeError, match="int"):
            stand_in_respond(normal_stand_in,
                             build_session_requests(real_assistance)[0],
                             turn="0")

    def test_normal_prose_is_value_free(self, real_assistance, normal_stand_in):
        outcome = stand_in_respond(normal_stand_in,
                                   build_session_requests(real_assistance)[0])
        assert outcome.kind == OUTCOME_ATTEMPTED_RESPONSE
        assert isinstance(outcome.text, str)
        assert not any(character.isdigit() for character in outcome.text)

    def test_unavailable_declares_unavailable(self, real_assistance):
        outcome = stand_in_respond(StandInAssistant(PROFILE_UNAVAILABLE),
                                   build_session_requests(real_assistance)[0])
        assert outcome.kind == OUTCOME_UNAVAILABLE
        assert outcome.text == ""

    def test_malformed_attempts_a_non_string(self, real_assistance):
        outcome = stand_in_respond(StandInAssistant(PROFILE_MALFORMED),
                                   build_session_requests(real_assistance)[0])
        assert outcome.kind == OUTCOME_ATTEMPTED_RESPONSE
        assert not isinstance(outcome.text, str)

    def test_empty_attempts_an_empty_string(self, real_assistance):
        outcome = stand_in_respond(StandInAssistant(PROFILE_EMPTY),
                                   build_session_requests(real_assistance)[0])
        assert outcome.text == ""

    def test_oversized_is_100kb(self, real_assistance):
        outcome = stand_in_respond(StandInAssistant(PROFILE_OVERSIZED),
                                   build_session_requests(real_assistance)[0])
        assert isinstance(outcome.text, str)
        assert len(outcome.text) == 100_000

    def test_hostile_cycles_the_fixed_sequence(self, real_assistance):
        # The built-in HOSTILE profile replays the complete 7B7 adversarial
        # list in its original order, minus the empty output (which is its own
        # EMPTY profile): 16 content outputs + the 100 KB output = 17.
        request = build_session_requests(real_assistance)[0]
        stand_in = StandInAssistant(PROFILE_HOSTILE)
        first = stand_in_respond(stand_in, request, turn=0)
        again = stand_in_respond(stand_in, request, turn=17)
        assert first == again
        assert "22.0" in first.text
        texts = [stand_in_respond(stand_in, request, turn=i).text
                 for i in range(17)]
        assert any(isinstance(text, str) and len(text) == 100_000
                   for text in texts)
        assert len(set(texts)) == 17

    def test_hostile_profile_replays_the_7b7_order(self, real_assistance):
        # The 7B7 adversarial list in its own order (empty excluded): the
        # built-in profile must not reorder, rank or triage anything.
        expected = [text for label, text, claims in ADVERSARIAL_OUTPUTS
                    if text != ""]
        assert len(expected) == 17
        request = build_session_requests(real_assistance)[0]
        stand_in = StandInAssistant(PROFILE_HOSTILE)
        assert [stand_in_respond(stand_in, request, turn=i).text
                for i in range(17)] == expected

    def test_contradictory_alternates(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        stand_in = StandInAssistant(PROFILE_CONTRADICTORY)
        first = stand_in_respond(stand_in, request, turn=0)
        second = stand_in_respond(stand_in, request, turn=1)
        assert first.text != second.text
        assert "matches" in first.text
        assert "contradicts" in second.text
        assert stand_in_respond(stand_in, request, turn=2).text == first.text

    def test_explicit_content_replays_exactly(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        stand_in = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent("exact first"), StandInContent("exact second")))
        assert stand_in_respond(stand_in, request, turn=0).text == "exact first"
        assert stand_in_respond(stand_in, request, turn=1).text == "exact second"
        assert stand_in_respond(stand_in, request, turn=2).text == "exact first"

    def test_stand_in_is_deterministic_and_hashable(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        one = StandInAssistant(PROFILE_NORMAL)
        two = StandInAssistant(PROFILE_NORMAL)
        assert stand_in_respond(one, request, turn=3) == \
            stand_in_respond(two, request, turn=3)
        assert repr(one) == repr(two)
        assert hash(one) == hash(two)


# =============================================================================
# 3. The session flow: the genuine caller of the 7B7 boundary.
# =============================================================================

class TestSessionFlow:
    def test_sweep_builds_only_through_the_boundary(self, real_assistance,
                                                    monkeypatch):
        calls = []
        real_build = session_module.build_assistant_request

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real_build(*args, **kwargs)

        monkeypatch.setattr(session_module, "build_assistant_request", spy)
        build_session_requests(real_assistance)
        assert len(calls) == 863
        assert all(isinstance(args[0], ReviewerAssistance)
                   and "role" in kwargs for args, kwargs in calls)

    def test_session_run_builds_only_through_the_boundary(self, real_assistance,
                                                          monkeypatch):
        calls = []
        real_build = session_module.build_assistant_request

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real_build(*args, **kwargs)

        monkeypatch.setattr(session_module, "build_assistant_request", spy)
        run_session(real_assistance, StandInAssistant(PROFILE_NORMAL))
        assert len(calls) == 863

    def test_turns_parse_render_and_check_only_through_the_boundary(
            self, real_assistance, monkeypatch):
        parsed, rendered, checked = [], [], []
        real_parse = session_module.parse_assistant_response
        real_render = session_module.render_assistant_message
        real_check = session_module.response_context_matches

        def spy_parse(*args, **kwargs):
            parsed.append(args)
            return real_parse(*args, **kwargs)

        def spy_render(*args, **kwargs):
            rendered.append(args)
            return real_render(*args, **kwargs)

        def spy_check(*args, **kwargs):
            checked.append(args)
            return real_check(*args, **kwargs)

        monkeypatch.setattr(session_module, "parse_assistant_response", spy_parse)
        monkeypatch.setattr(session_module, "render_assistant_message", spy_render)
        monkeypatch.setattr(session_module,
                            "response_context_matches", spy_check)
        requests = build_session_requests(real_assistance)[:5]
        session = run_session(real_assistance, StandInAssistant(PROFILE_NORMAL),
                              requests=requests)
        assert len(parsed) == 5 == len(rendered) == len(checked)
        assert session.turn_count == 5
        assert all(args[0] in requests for args in parsed)
        assert all(args[0].request in requests for args in rendered)

    def test_failure_turns_parse_nothing(self, real_assistance, monkeypatch):
        parsed = []
        real_parse = session_module.parse_assistant_response

        def spy_parse(*args, **kwargs):
            parsed.append(args)
            return real_parse(*args, **kwargs)

        monkeypatch.setattr(session_module, "parse_assistant_response", spy_parse)
        requests = build_session_requests(real_assistance)[:3]
        session = run_session(real_assistance,
                              StandInAssistant(PROFILE_UNAVAILABLE),
                              requests=requests)
        assert parsed == []
        assert all(record.result_kind == RESULT_ASSISTANT_UNAVAILABLE
                   for record in session.records)

    def test_full_normal_session_is_all_display_messages(self, real_assistance,
                                                         normal_stand_in):
        session = run_session(real_assistance, normal_stand_in)
        assert session.turn_count == 863
        assert session.revision == 0
        assert all(record.result_kind == RESULT_DISPLAY_MESSAGE
                   for record in session.records)
        assert all(record.rendered_text.startswith(DISPLAY_PREFIX)
                   for record in session.records)
        assert all(record.error_text == "" for record in session.records)
        assert all(record.response is not None
                   and isinstance(record.response, AssistantResponse)
                   for record in session.records)
        assert all("nothing in this message is an engineering answer".lower()
                   not in record.rendered_text.lower()
                   or "context only" in record.rendered_text
                   for record in session.records)

    def test_request_order_is_submission_order(self, real_assistance):
        requests = build_session_requests(real_assistance)
        expected = []
        for candidate in real_assistance.candidates:
            for task in candidate.tasks:
                expected.append((
                    candidate.package_id, task.task_id, ROLE_EXPLAIN_TASK))
                if task.category == CATEGORY_ASSISTABLE:
                    expected.append((
                        candidate.package_id, task.task_id,
                        ROLE_DESCRIBE_OBSERVATION))
                elif task.category == CATEGORY_ADMINISTRATIVE:
                    expected.append((
                        candidate.package_id, task.task_id,
                        ROLE_DRAFT_ACKNOWLEDGMENT))
                elif (task.category == CATEGORY_EVIDENCE_LOOKUP
                        and task.source_anchor
                        and task.source_anchor != NO_SOURCE_EVIDENCE_TEXT):
                    expected.append((
                        candidate.package_id, task.task_id,
                        ROLE_EVIDENCE_NAVIGATION))
        assert [(r.package_id, r.task_id, r.role) for r in requests] == expected
        assert requests[0].package_id == EXPECTED_IDS[0]
        assert requests[-1].package_id == EXPECTED_IDS[-1]

    def test_caller_supplied_requests_must_be_boundary_requests(
            self, real_assistance):
        with pytest.raises(TypeError, match="tuple"):
            run_session(real_assistance, StandInAssistant(PROFILE_NORMAL),
                        requests=[1, 2])
        with pytest.raises(TypeError, match="AssistantRequest"):
            run_session(real_assistance, StandInAssistant(PROFILE_NORMAL),
                        requests=(1,))

    def test_session_refuses_foreign_objects(self, real_assistance):
        with pytest.raises(TypeError, match="ReviewerAssistance"):
            run_session("not assistance", StandInAssistant(PROFILE_NORMAL))
        with pytest.raises(TypeError, match="StandInAssistant"):
            run_session(real_assistance, "not a stand-in")
        with pytest.raises(TypeError, match="AssistantRequest"):
            run_session_turn("not a request", StandInAssistant(PROFILE_NORMAL))
        with pytest.raises(TypeError, match="int"):
            run_session_turn(build_session_requests(real_assistance)[0],
                             StandInAssistant(PROFILE_NORMAL), turn="0")
        with pytest.raises(TypeError, match="AssistantSession"):
            render_session("not a session")


# =============================================================================
# 4. Failure containment: every failure is visible and changes nothing.
# =============================================================================

class TestFailureContainment:
    def test_unavailable_turn_is_a_visible_failure(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        record = run_session_turn(request, StandInAssistant(PROFILE_UNAVAILABLE))
        assert record.result_kind == RESULT_ASSISTANT_UNAVAILABLE
        assert record.stand_in_kind == OUTCOME_UNAVAILABLE
        assert record.response is None
        assert record.error_text
        assert "ASSISTANT_UNAVAILABLE" in record.rendered_text
        assert record.rendered_text.startswith(DISPLAY_PREFIX)

    def test_slow_turn_is_a_visible_failure(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        record = run_session_turn(request, StandInAssistant(PROFILE_SLOW))
        assert record.result_kind == RESULT_TIME_BUDGET_EXCEEDED
        assert record.stand_in_kind == OUTCOME_TIME_BUDGET_EXCEEDED
        assert record.response is None
        assert record.error_text
        assert "TIME_BUDGET_EXCEEDED" in record.rendered_text

    def test_malformed_turn_is_a_visible_failure(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        record = run_session_turn(request, StandInAssistant(PROFILE_MALFORMED))
        assert record.result_kind == RESULT_MALFORMED_RESPONSE
        assert record.response is None
        assert "must be a str" in record.error_text
        assert "MALFORMED_RESPONSE" in record.rendered_text

    def test_context_mismatch_turn_is_a_visible_failure(self, real_assistance,
                                                        tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        task = _task(assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        rev0_request = build_assistant_request(
            assistance, role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0009", task_id=task.task_id)
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        rev1_request = build_assistant_request(
            _assistance(advanced), role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0009", task_id=task.task_id)
        stale = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent("an old message",
                                    answers_request=rev0_request),))
        record = run_session_turn(rev1_request, stale)
        assert record.result_kind == RESULT_CONTEXT_MISMATCH
        assert record.response is None
        assert "different request context" in record.error_text
        assert "CONTEXT_MISMATCH" in record.rendered_text

    def test_unknown_outcome_kind_is_a_visible_failure(self, real_assistance,
                                                       monkeypatch):
        request = build_session_requests(real_assistance)[0]
        monkeypatch.setattr(
            session_module, "stand_in_respond",
            lambda stand_in, req, turn=0: StandInOutcome(kind="WEIRD"))
        record = run_session_turn(request, StandInAssistant(PROFILE_NORMAL))
        assert record.result_kind == RESULT_MALFORMED_RESPONSE
        assert "unrecognised outcome kind" in record.error_text
        assert record.response is None

    def test_failures_never_become_review_verdicts(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        for stand_in, kind in [
                (StandInAssistant(PROFILE_UNAVAILABLE),
                 RESULT_ASSISTANT_UNAVAILABLE),
                (StandInAssistant(PROFILE_SLOW), RESULT_TIME_BUDGET_EXCEEDED),
                (StandInAssistant(PROFILE_MALFORMED), RESULT_MALFORMED_RESPONSE)]:
            record = run_session_turn(request, stand_in)
            assert record.result_kind == kind
            assert record.result_kind not in VERDICTS
            for verdict in VERDICTS:
                assert verdict not in record.error_text

    def test_failure_session_leaves_no_contract_trace(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        session = run_session(assistance, StandInAssistant(PROFILE_UNAVAILABLE))
        assert session.turn_count == 863
        assert all(record.result_kind == RESULT_ASSISTANT_UNAVAILABLE
                   for record in session.records)
        assert _snapshot(workflow, tmp_path) == before
        assert build_project_review_contract(workflow).revision == 0


# =============================================================================
# 5. Contradiction containment: recorded verbatim, never reconciled.
# =============================================================================

class TestContradictionRule:
    def test_successive_messages_disagree(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        stand_in = StandInAssistant(PROFILE_CONTRADICTORY)
        first = run_session_turn(request, stand_in, turn=0)
        second = run_session_turn(request, stand_in, turn=1)
        assert first.result_kind == RESULT_DISPLAY_MESSAGE
        assert second.result_kind == RESULT_DISPLAY_MESSAGE
        assert first.response.text != second.response.text

    def test_both_messages_are_recorded_display_only(self, real_assistance):
        request = build_session_requests(real_assistance)[0]
        session = run_session(
            real_assistance, StandInAssistant(PROFILE_CONTRADICTORY),
            requests=(request, request))
        assert session.turn_count == 2
        assert session.records[0].response.text in \
            render_session(session)
        assert session.records[1].response.text in \
            render_session(session)
        assert session.records[0].request == session.records[1].request == request

    def test_contradiction_against_the_released_observation_is_contained(
            self, real_assistance, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        request = _descr(assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        assert _request_fields(request, "current_value")[0] == \
            RP0001_CONFIRM_VALUE
        before = _snapshot(workflow, tmp_path)
        stand_in = StandInAssistant(
            PROFILE_CONTRADICTORY,
            content=(
                StandInContent("the material reading of 300 is wrong; the "
                               "drawing shows 250."),
                StandInContent("the material reading of 300 is correct."),
            ))
        session = run_session(assistance, stand_in, requests=(request, request))
        assert all(record.result_kind == RESULT_DISPLAY_MESSAGE
                   for record in session.records)
        assert session.records[0].response.text != \
            session.records[1].response.text
        assert "250" in session.records[0].rendered_text
        assert _snapshot(workflow, tmp_path) == before
        assert "250" not in str(_snapshot(workflow, tmp_path))
        # The released observation is untouched by either message.
        assert _request_fields(request, "current_value")[0] == \
            RP0001_CONFIRM_VALUE


# =============================================================================
# 6. Staleness and identity.
# =============================================================================

class TestStalenessIdentity:
    def test_rev0_response_does_not_match_rev1_context(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        task = _task(assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        rev0_request = build_assistant_request(
            assistance, role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0009", task_id=task.task_id)
        rev0_response = parse_assistant_response(rev0_request, "message at rev 0")
        assert response_context_matches(rev0_response, rev0_request) is True
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        rev1_request = build_assistant_request(
            _assistance(advanced), role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0009", task_id=task.task_id)
        assert rev0_request.revision == 0
        assert rev1_request.revision == 1
        assert rev0_request != rev1_request
        assert response_context_matches(rev0_response, rev1_request) is False

    def test_wrong_candidate_and_task_identity_refused(self, real_assistance):
        with pytest.raises(ValueError, match="no candidate"):
            build_assistant_request(
                real_assistance, role=ROLE_EXPLAIN_TASK,
                package_id="RP-XXXX", task_id="T-1")
        task = _task(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        with pytest.raises(ValueError, match="no task"):
            build_assistant_request(
                real_assistance, role=ROLE_EXPLAIN_TASK,
                package_id="RP-0001", task_id="T-NOT-REAL")

    def test_wrong_identity_response_never_attaches(self, real_assistance):
        confirm = _task(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        request_a = build_assistant_request(
            real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0001", task_id=confirm.task_id)
        response = parse_assistant_response(request_a, "text")
        position = _task(real_assistance, "RP-0001", TASK_SELECT_POSITION)
        request_b = build_assistant_request(
            real_assistance, role=ROLE_EXPLAIN_TASK,
            package_id="RP-0001", task_id=position.task_id)
        assert response_context_matches(response, request_b) is False
        stale = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent("text", answers_request=request_a),))
        record = run_session_turn(request_b, stale)
        assert record.result_kind == RESULT_CONTEXT_MISMATCH
        assert record.response is None

    def test_session_activity_never_creates_revisions(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        session = run_session(assistance, StandInAssistant(PROFILE_NORMAL))
        assert session.revision == 0
        assert build_project_review_contract(workflow).revision == 0
        assert _snapshot(workflow, tmp_path) == before
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        assert build_project_review_contract(advanced).revision == 1


# =============================================================================
# 7. Genuine API type safety: session records cannot enter the mutation path.
# =============================================================================

class TestGenuineApiTypeSafety:
    def test_session_objects_refused_by_exception_resolution(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        session = run_session(
            assistance, StandInAssistant(PROFILE_NORMAL),
            requests=build_session_requests(assistance)[:2])
        record = session.records[0]
        before = _snapshot(workflow, tmp_path)
        for foreign in (session, record, render_session(session),
                        StandInOutcome(kind=OUTCOME_ATTEMPTED_RESPONSE,
                                       text="prose")):
            with pytest.raises(TypeError, match="HumanResolution"):
                apply_human_resolution(workflow.exception_package, foreign)
        assert _snapshot(workflow, tmp_path) == before
        assert build_project_review_contract(workflow).revision == 0

    def test_session_objects_refused_by_project_resolution(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        session = run_session(
            assistance, StandInAssistant(PROFILE_NORMAL),
            requests=build_session_requests(assistance)[:2])
        record = session.records[0]
        before = _snapshot(workflow, tmp_path)
        for foreign in (session, record, render_session(session)):
            with pytest.raises(TypeError, match="HumanResolution"):
                resolve_project_connection(
                    workflow, package_id="RP-0001", resolutions=[foreign],
                    output_dir=tmp_path)
        assert _snapshot(workflow, tmp_path) == before
        assert build_project_review_contract(workflow).revision == 0


# =============================================================================
# 8. The required real-world Selby cases, exercised through the session.
# =============================================================================

class TestRequiredSelbyCases:
    def test_rp0001_material_300_verbatim_through_the_session(
            self, real_assistance, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        request = _descr(assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        assert _request_fields(request, "current_value")[0] == \
            RP0001_CONFIRM_VALUE
        before = _snapshot(workflow, tmp_path)
        session = run_session(assistance, StandInAssistant(PROFILE_NORMAL),
                              requests=(request,))
        assert session.records[0].result_kind == RESULT_DISPLAY_MESSAGE
        assert "300" in RP0001_CONFIRM_VALUE
        assert _snapshot(workflow, tmp_path) == before
        # The session's sweep over the same task still releases "300" verbatim.
        for built in build_session_requests(real_assistance):
            if built.package_id == "RP-0001" and \
                    built.role == ROLE_DESCRIBE_OBSERVATION and \
                    built.task_id == request.task_id:
                assert _request_fields(built, "current_value")[0] == \
                    RP0001_CONFIRM_VALUE

    def test_rp0001_human_position_releases_boundary_only(
            self, real_assistance):
        task = _task(real_assistance, "RP-0001", TASK_SELECT_POSITION)
        request = build_assistant_request(
            real_assistance, role=ROLE_EXPLAIN_TASK,
            package_id="RP-0001", task_id=task.task_id)
        assert {field.name for field in request.fields} == EXPLAIN_HUMAN_FIELDS
        assert not any(field.name == "current_value" for field in request.fields)
        assert not any(field.name == "provenance" for field in request.fields)
        with pytest.raises(ValueError, match="existing trusted observation"):
            build_assistant_request(
                real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
                package_id="RP-0001", task_id=task.task_id)
        # The sweep asks this task exactly once, and only to explain it.
        matches = [r for r in build_session_requests(real_assistance)
                   if r.package_id == "RP-0001" and r.task_id == task.task_id]
        assert len(matches) == 1
        assert matches[0].role == ROLE_EXPLAIN_TASK

    def test_rp0001_evidence_lookup_releases_only_the_genuine_anchor(
            self, real_assistance):
        task = _task(real_assistance, "RP-0001", TASK_PROVIDE_PLATE)
        request = build_assistant_request(
            real_assistance, role=ROLE_EVIDENCE_NAVIGATION,
            package_id="RP-0001", task_id=task.task_id)
        assert {field.name for field in request.fields} == NAVIGATION_FIELDS
        assert _request_fields(request, "source_anchor") == [task.source_anchor]
        assert "SELBY-C1136" in task.source_anchor
        with pytest.raises(ValueError, match="existing trusted observation"):
            build_assistant_request(
                real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
                package_id="RP-0001", task_id=task.task_id)

    def test_rp0008_hole_reading_verbatim_no_conversion(self, real_assistance,
                                                        tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        request = _descr(assistance, "RP-0008", TASK_PROVIDE_HOLE_DIAMETER)
        assert _request_fields(request, "current_value")[0] == \
            RP0008_HOLE_READING
        before = _snapshot(workflow, tmp_path)
        conversion = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent("'22mm holes' are 0.866 inches"),))
        session = run_session(assistance, conversion, requests=(request,))
        assert session.records[0].result_kind == RESULT_DISPLAY_MESSAGE
        assert "0.866 inches" in session.records[0].rendered_text
        assert _snapshot(workflow, tmp_path) == before
        assert "0.866" not in str(_snapshot(workflow, tmp_path))
        assert _request_fields(request, "current_value")[0] == \
            RP0008_HOLE_READING

    def test_rp0045_plate_reading_verbatim(self, real_assistance):
        request = _descr(real_assistance, "RP-0045", TASK_PROVIDE_PLATE)
        assert _request_fields(request, "current_value")[0] == \
            RP0045_PLATE_READING
        session = run_session(real_assistance, StandInAssistant(PROFILE_NORMAL),
                              requests=(request,))
        assert session.records[0].result_kind == RESULT_DISPLAY_MESSAGE

    def test_rp0033_missing_material_stays_missing(self, material_assistance,
                                                   tmp_path):
        workflow = _material_workflow()
        task = _task(material_assistance, "RP-0033",
                     TASK_PROVIDE_MATERIAL_SPECIFICATION)
        with pytest.raises(ValueError, match="existing trusted observation"):
            build_assistant_request(
                material_assistance, role=ROLE_DESCRIBE_OBSERVATION,
                package_id="RP-0033", task_id=task.task_id)
        request = build_assistant_request(
            material_assistance, role=ROLE_EVIDENCE_NAVIGATION,
            package_id="RP-0033", task_id=task.task_id)
        assert _request_fields(request, "source_anchor") == \
            [RP0033_MATERIAL_ANCHOR]
        before = _snapshot(workflow, tmp_path)
        hostile = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent("the material grade is 300PLUS"),))
        session = run_session(material_assistance, hostile,
                              requests=(request,))
        assert session.records[0].result_kind == RESULT_DISPLAY_MESSAGE
        assert "300PLUS" in session.records[0].rendered_text
        assert _snapshot(workflow, tmp_path) == before
        assert _task(_assistance(_material_workflow()), "RP-0033",
                     TASK_PROVIDE_MATERIAL_SPECIFICATION).current_value is None

    def test_rp0009_full_journey_through_sessions(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        rev0_requests = tuple(
            request for request in build_session_requests(assistance)
            if request.package_id == "RP-0009")
        assert rev0_requests
        rev0_session = run_session(assistance, StandInAssistant(PROFILE_NORMAL),
                                   requests=rev0_requests)
        assert rev0_session.revision == 0
        assert all(record.result_kind == RESULT_DISPLAY_MESSAGE
                   for record in rev0_session.records)
        assert _snapshot(workflow, tmp_path) == before
        # Only the genuine human resolution advances anything.
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        assert build_project_review_contract(advanced).revision == 1
        rev1_assistance = _assistance(advanced)
        rev1_requests = tuple(
            request for request in build_session_requests(rev1_assistance)
            if request.package_id == "RP-0009")
        rev1_session = run_session(rev1_assistance,
                                   StandInAssistant(PROFILE_NORMAL),
                                   requests=rev1_requests)
        assert rev1_session.revision == 1
        # Rev-0 requests never match the rev-1 context.
        assert all(request.revision == 0 for request in rev0_requests)
        assert all(request.revision == 1 for request in rev1_requests)
        rev0_response = parse_assistant_response(rev0_requests[0],
                                                 "a rev-0 message")
        assert response_context_matches(rev0_response, rev1_requests[0]) \
            is False
        # The genuine result is unchanged by all session activity.
        pdf_path = tmp_path / RP0009_PDF_NAME
        assert pdf_path.exists()
        assert pdf_path.stat().st_size == RP0009_PDF_SIZE
        item = next(i for i in build_project_review_contract(advanced).items
                    if i.package_id == "RP-0009")
        assert {(entry.field, entry.provenance) for entry in item.provenance
                if entry.field in {"connected_member_marks", "position",
                                   "plate", "holes", "location",
                                   "attachments", "material"}} \
            == RP0009_RESOLVED_PROVENANCE
        decisions = {i.decision
                     for i in build_project_review_contract(advanced).items}
        assert decisions == {"REVIEW", "AUTO"}


# =============================================================================
# 9. Hostile output testing: the exact 7B7 adversarial list through the session.
# =============================================================================

class TestHostileReplay:
    def test_every_7b7_adversarial_output_runs_through_the_session(
            self, real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        for label, text, claims in ADVERSARIAL_OUTPUTS:
            stand_in = StandInAssistant(
                PROFILE_HOSTILE,
                content=(StandInContent(text, reference_claims=claims),))
            session = run_session(real_assistance, stand_in,
                                  requests=(request,))
            record = session.records[0]
            assert record.result_kind == RESULT_DISPLAY_MESSAGE, label
            assert record.response.text == text, label
            assert tuple(claim.claimed_text
                         for claim in record.response.reference_claims) \
                == tuple(claims), label
            assert text in record.rendered_text, label
            assert record.rendered_text.startswith(DISPLAY_PREFIX), label

    def test_hostile_replay_changes_no_engineering_state(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        before_blockers = {
            candidate.package_id: candidate.blocker_codes
            for candidate in assistance.candidates}
        request = _descr(assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        for label, text, claims in ADVERSARIAL_OUTPUTS:
            stand_in = StandInAssistant(
                PROFILE_HOSTILE,
                content=(StandInContent(text, reference_claims=claims),))
            run_session(assistance, stand_in, requests=(request,))
        after = _snapshot(workflow, tmp_path)
        assert after == before
        assert build_project_review_contract(workflow).revision == 0
        assert {i.decision for i in build_project_review_contract(workflow).items} \
            == {"REVIEW"}
        after_blockers = {
            candidate.package_id: candidate.blocker_codes
            for candidate in _assistance(workflow).candidates}
        assert after_blockers == before_blockers
        assert list(tmp_path.iterdir()) == []

    def test_hostile_builtin_profile_runs_the_full_sweep(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        session = run_session(assistance, StandInAssistant(PROFILE_HOSTILE))
        assert session.turn_count == 863
        assert all(record.result_kind == RESULT_DISPLAY_MESSAGE
                   for record in session.records)
        hostile_texts = [stand_in_respond(StandInAssistant(PROFILE_HOSTILE),
                                          build_session_requests(assistance)[0],
                                          turn=i).text for i in range(18)]
        assert all(record.response.text in hostile_texts
                   for record in session.records)
        assert _snapshot(workflow, tmp_path) == before

    def test_adversarial_anchor_claim_stays_display_only(
            self, material_assistance, tmp_path):
        # An exact match with a RELEASED anchor is flagged as trusted for
        # display — and still cannot become state.
        workflow = _material_workflow()
        before = _snapshot(workflow, tmp_path)
        task = _task(material_assistance, "RP-0033",
                     TASK_PROVIDE_MATERIAL_SPECIFICATION)
        request = build_assistant_request(
            material_assistance, role=ROLE_EVIDENCE_NAVIGATION,
            package_id="RP-0033", task_id=task.task_id)
        stand_in = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent(
                "the value is on the referenced page",
                reference_claims=(RP0033_MATERIAL_ANCHOR, "page 99")),))
        session = run_session(material_assistance, stand_in,
                              requests=(request,))
        record = session.records[0]
        assert record.result_kind == RESULT_DISPLAY_MESSAGE
        assert record.response.reference_claims[0].matches_trusted_anchor \
            is True
        assert record.response.reference_claims[1].matches_trusted_anchor \
            is False
        assert "display only" in record.rendered_text
        assert _snapshot(workflow, tmp_path) == before
        assert _task(_assistance(_material_workflow()), "RP-0033",
                     TASK_PROVIDE_MATERIAL_SPECIFICATION).current_value is None


# =============================================================================
# 10. The full real 51-candidate workload.
# =============================================================================

class TestRealWorkloadSweep:
    def test_51_candidates_508_tasks_in_submission_order(self, real_assistance):
        assert real_assistance.candidate_count == 51
        assert real_assistance.task_count == 508
        assert [candidate.package_id for candidate in real_assistance.candidates] \
            == list(EXPECTED_IDS)

    def test_request_counts_pinned(self, real_assistance):
        requests = build_session_requests(real_assistance)
        counts = Counter(request.role for request in requests)
        assert counts == {
            ROLE_EXPLAIN_TASK: 508,
            ROLE_DESCRIBE_OBSERVATION: 72,
            ROLE_DRAFT_ACKNOWLEDGMENT: 204,
            ROLE_EVIDENCE_NAVIGATION: 79,
        }
        assert len(requests) == 863

    def test_every_request_identity_is_genuine(self, real_assistance):
        tasks_by_package = {
            candidate.package_id: {task.task_id for task in candidate.tasks}
            for candidate in real_assistance.candidates}
        for request in build_session_requests(real_assistance):
            assert request.package_id in tasks_by_package
            assert request.task_id in tasks_by_package[request.package_id]

    def test_sweep_releases_nothing_beyond_the_permission_rows(
            self, real_assistance):
        for request in build_session_requests(real_assistance):
            names = {field.name for field in request.fields}
            if request.role == ROLE_EXPLAIN_TASK:
                assert names == EXPLAIN_BASE_FIELDS or \
                    names == EXPLAIN_HUMAN_FIELDS
            elif request.role == ROLE_DRAFT_ACKNOWLEDGMENT:
                assert names == DRAFT_FIELDS
            elif request.role == ROLE_EVIDENCE_NAVIGATION:
                assert names == NAVIGATION_FIELDS
            elif request.role == ROLE_DESCRIBE_OBSERVATION:
                # The provenance fields are the contract's own metadata for the
                # task's field and appear only when the task has related
                # provenance entries; the core row is always exactly this.
                assert names <= DESCRIBE_FIELDS
                assert {"task_id", "task_type", "category",
                        "current_value"} <= names

    def test_full_normal_sweep_renders_and_matches_pinned_counts(
            self, real_assistance, normal_stand_in):
        session = run_session(real_assistance, normal_stand_in)
        assert session.turn_count == 863
        rendered = render_session(session)
        assert rendered.startswith(SESSION_MARKER)
        assert "revision 0, 863 turns" in rendered
        assert all(record.rendered_text.startswith(DISPLAY_PREFIX)
                   for record in session.records)
        assert all(record.result_kind == RESULT_DISPLAY_MESSAGE
                   for record in session.records)

    def test_no_ranking_anywhere(self, real_assistance):
        requests = build_session_requests(real_assistance)
        assert [r.package_id for r in requests] == [
            r.package_id for r in build_session_requests(real_assistance)]
        # The order is the contract's own submission order: no sorting call
        # exists anywhere in the module (the surface tests pin the AST), so
        # any order change would have to come from the contract itself.
        for node in ast.walk(ast.parse(SESSION_SOURCE)):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in {"sorted", "sort"}
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in {"sort", "sorted"}


# =============================================================================
# 11. Contract immutability across every profile.
# =============================================================================

class TestContractImmutability:
    def test_each_profile_leaves_contract_byte_identical(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        for profile in (PROFILE_NORMAL, PROFILE_UNAVAILABLE, PROFILE_SLOW,
                        PROFILE_MALFORMED, PROFILE_CONTRADICTORY,
                        PROFILE_HOSTILE, PROFILE_EMPTY, PROFILE_OVERSIZED):
            session = run_session(assistance, StandInAssistant(profile))
            assert session.turn_count == 863, profile
            assert _snapshot(workflow, tmp_path) == before, profile
        assert build_project_review_contract(workflow).revision == 0
        assert {i.decision for i in build_project_review_contract(workflow).items} \
            == {"REVIEW"}
        assert _assistance(workflow).pending_task_count == 508

    def test_consecutive_sessions_leave_contract_identical(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        for _ in range(3):
            run_session(assistance, StandInAssistant(PROFILE_NORMAL))
        assert _snapshot(workflow, tmp_path) == before
        assert _assistance(workflow) == assistance


# =============================================================================
# 12. Display-only proof.
# =============================================================================

class TestDisplayOnlyProof:
    def test_render_session_is_plain_marked_text(self, real_assistance,
                                                 normal_stand_in):
        session = run_session(
            real_assistance, normal_stand_in,
            requests=build_session_requests(real_assistance)[:2])
        rendered = render_session(session)
        assert isinstance(rendered, str)
        assert rendered.startswith(SESSION_MARKER)
        assert "UNTRUSTED, DISPLAY ONLY" in rendered
        assert "NOT PART OF THE REVIEW RECORD" in rendered

    def test_session_record_fields_are_only_display_data(self, real_assistance,
                                                         normal_stand_in):
        session = run_session(
            real_assistance, normal_stand_in,
            requests=build_session_requests(real_assistance)[:1])
        record = session.records[0]
        assert set(vars(record)) == {
            "turn", "request", "stand_in_kind", "result_kind", "rendered_text",
            "error_text", "response"}
        assert isinstance(record.rendered_text, str)
        assert isinstance(record.error_text, str)
        assert record.response is None or isinstance(
            record.response, AssistantResponse)

    def test_session_text_never_enters_the_contract(self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        hostile = StandInAssistant(
            PROFILE_HOSTILE,
            content=(StandInContent("the material grade is 300PLUS"),))
        session = run_session(
            assistance, hostile,
            requests=(_descr(assistance, "RP-0001", TASK_CONFIRM_AI_VALUES),))
        after = str(_snapshot(workflow, tmp_path))
        assert "300PLUS" not in after
        assert "ASSISTANT SESSION" not in after
        assert "the material grade is" not in after
        assert session is not None

    def test_rendered_session_cannot_be_consumed_as_a_resolution(
            self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        session = run_session(
            assistance, StandInAssistant(PROFILE_NORMAL),
            requests=build_session_requests(assistance)[:1])
        before = _snapshot(workflow, tmp_path)
        with pytest.raises(TypeError, match="HumanResolution"):
            apply_human_resolution(workflow.exception_package,
                                   render_session(session))
        with pytest.raises(TypeError, match="HumanResolution"):
            resolve_project_connection(
                workflow, package_id="RP-0001",
                resolutions=[render_session(session)], output_dir=tmp_path)
        assert _snapshot(workflow, tmp_path) == before


# =============================================================================
# 13. Determinism: two independent fresh builds are byte-identical.
# =============================================================================

class TestDeterminism:
    def test_two_independent_builds_identical(self):
        first = _assistance(_fresh())
        second = _assistance(_fresh())
        requests_one = build_session_requests(first)
        requests_two = build_session_requests(second)
        assert requests_one == requests_two
        assert repr(requests_one) == repr(requests_two)
        assert [r.role for r in requests_one] == [r.role for r in requests_two]

    def test_two_independent_sessions_identical(self):
        first = run_session(_assistance(_fresh()),
                            StandInAssistant(PROFILE_NORMAL))
        second = run_session(_assistance(_fresh()),
                             StandInAssistant(PROFILE_NORMAL))
        assert first == second
        assert repr(first) == repr(second)
        assert dataclasses.asdict(first) == dataclasses.asdict(second)
        assert render_session(first) == render_session(second)
        assert [record.request for record in first.records] == \
            [record.request for record in second.records]

    def test_records_hashable(self, real_assistance, normal_stand_in):
        session = run_session(
            real_assistance, normal_stand_in,
            requests=build_session_requests(real_assistance)[:2])
        hash(session)
        for record in session.records:
            hash(record)
            hash(record.request)
            hash(record.response)

    def test_no_files_written(self, real_assistance, normal_stand_in, tmp_path):
        run_session(real_assistance, normal_stand_in)
        render_session(run_session(
            real_assistance, normal_stand_in,
            requests=build_session_requests(real_assistance)[:1]))
        assert list(tmp_path.iterdir()) == []


# =============================================================================
# 14. Regression: the accepted 7B5/7B6/7B7 behaviour is untouched.
# =============================================================================

class TestRegression:
    def test_workload_baseline_unchanged(self, real_assistance):
        contract = build_project_review_contract(_fresh())
        baseline = build_reviewer_workload_baseline(contract)
        categories = Counter(
            record.category
            for candidate in baseline.candidates for record in candidate.tasks)
        assert sum(categories.values()) == 508
        assert categories[CATEGORY_ASSISTABLE] == 72
        assert categories[CATEGORY_HUMAN_ENGINEERING_DECISION] == 153
        assert categories[CATEGORY_ADMINISTRATIVE] == 204
        assert categories[CATEGORY_EVIDENCE_LOOKUP] == 79

    def test_rp0009_genuine_resolution_regression(self, tmp_path):
        workflow = _fresh()
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        contract = build_project_review_contract(advanced)
        assert contract.revision == 1
        assert {item.decision for item in contract.items} == {"REVIEW", "AUTO"}
        pdf_path = tmp_path / RP0009_PDF_NAME
        assert pdf_path.exists()
        assert pdf_path.stat().st_size == RP0009_PDF_SIZE
        item = next(i for i in contract.items if i.package_id == "RP-0009")
        assert {(entry.field, entry.provenance) for entry in item.provenance
                if entry.field in {"connected_member_marks", "position",
                                   "plate", "holes", "location",
                                   "attachments", "material"}} \
            == RP0009_RESOLVED_PROVENANCE

    def test_missing_material_candidates_still_missing(self, material_assistance):
        for package_id in MISSING_MATERIAL_IDS:
            task = _task(material_assistance, package_id,
                         TASK_PROVIDE_MATERIAL_SPECIFICATION)
            assert task.current_value is None

    def test_7b7_boundary_pinned_sweep_still_holds(self, real_assistance):
        counts = {ROLE_EXPLAIN_TASK: 0, ROLE_DESCRIBE_OBSERVATION: 0,
                  ROLE_DRAFT_ACKNOWLEDGMENT: 0, ROLE_EVIDENCE_NAVIGATION: 0}
        for candidate in real_assistance.candidates:
            for task in candidate.tasks:
                build_assistant_request(
                    real_assistance, role=ROLE_EXPLAIN_TASK,
                    package_id=candidate.package_id, task_id=task.task_id)
                counts[ROLE_EXPLAIN_TASK] += 1
                if task.category == CATEGORY_ASSISTABLE:
                    build_assistant_request(
                        real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
                        package_id=candidate.package_id, task_id=task.task_id)
                    counts[ROLE_DESCRIBE_OBSERVATION] += 1
                if task.category == CATEGORY_ADMINISTRATIVE:
                    build_assistant_request(
                        real_assistance, role=ROLE_DRAFT_ACKNOWLEDGMENT,
                        package_id=candidate.package_id, task_id=task.task_id)
                    counts[ROLE_DRAFT_ACKNOWLEDGMENT] += 1
                if (task.category == CATEGORY_EVIDENCE_LOOKUP
                        and task.source_anchor
                        and task.source_anchor != NO_SOURCE_EVIDENCE_TEXT):
                    build_assistant_request(
                        real_assistance, role=ROLE_EVIDENCE_NAVIGATION,
                        package_id=candidate.package_id, task_id=task.task_id)
                    counts[ROLE_EVIDENCE_NAVIGATION] += 1
        assert counts == {ROLE_EXPLAIN_TASK: 508,
                          ROLE_DESCRIBE_OBSERVATION: 72,
                          ROLE_DRAFT_ACKNOWLEDGMENT: 204,
                          ROLE_EVIDENCE_NAVIGATION: 79}

    def test_rp0009_hole_reading_pinned(self, real_assistance):
        request = _descr(real_assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        assert "(('connected_member_marks'" in \
            _request_fields(request, "current_value")[0]
