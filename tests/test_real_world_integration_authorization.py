"""
Milestone 7C3 — INTEGRATION AUTHORIZATION BOUNDARY (tests).

Proves the deterministic system rule: AUTHORIZED exactly for
SELECT + READY; all five other combinations NOT_AUTHORIZED; the source
7C1 record is preserved verbatim and never mutated; forged or invalid
records are refused loudly; and the layer carries no network, I/O,
environment, credential, subprocess or clock surface. Prior boundaries
(7B9/7C0/7C1/7C2) are regression-proven inside this file.
"""

import ast
import dataclasses
import os
from pathlib import Path

import pytest

from app.cad_engine.integration_authorization import (
    AUTHORIZATION_AUTHORIZED,
    AUTHORIZATION_NOT_AUTHORIZED,
    AUTHORIZATION_RESULTS,
    AuthorizationRecord,
    authorize_selection,
)
from app.cad_engine.provider_fact_capture import (
    CONTROL_OWNER,
    PROVIDER_NAME,
    CapturedFact,
    ProviderFactCapture,
    build_requirement_records,
    evaluate_capture,
    jev_fact_capture,
)
from app.cad_engine.provider_readiness_gate import (
    GATE_NOT_READY,
    GATE_READY,
    KIND_PROVIDER_FACT,
    REQUIREMENT_INVENTORY,
    STATUS_SATISFIED,
    evaluate_readiness,
)
from app.cad_engine.provider_selection_decision import (
    DECISION_DECLINE,
    DECISION_DEFER,
    DECISION_SELECT,
    DECISIONS,
    ProviderSelectionRecord,
    record_provider_selection,
)
from app.cad_engine.provider_selection_projection import (
    build_selection_projection,
)

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "app" / "cad_engine" / "integration_authorization.py")

REPORT_BLOCKERS = frozenset({
    "SECURITY_CONTROLS",
    "TIMEOUT_CANCELLATION_SEMANTICS",
    "AVAILABILITY_SLO",
    "DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
})

SOURCE_FIELDS = (
    "provider", "capture_digest", "readiness_overall", "blocking_reasons",
    "decision", "rationale", "selected_against_not_ready",
)


def _module_source():
    return MODULE_PATH.read_text(encoding="utf-8")


def _module_tree():
    return ast.parse(_module_source())


def _capture():
    return jev_fact_capture()


def _determination():
    return evaluate_capture()


def _jev_record(decision):
    return record_provider_selection(
        capture=_capture(), determination=_determination(),
        decision=decision)


def _ready_capture():
    """The established synthetic fully-SATISFIED capture: every provider
    fact carries a source, so the genuine gate reads READY."""
    facts = []
    for requirement_id, kind, classification in REQUIREMENT_INVENTORY:
        is_fact = kind == KIND_PROVIDER_FACT
        facts.append(CapturedFact(
            provider="Example Provider" if is_fact else CONTROL_OWNER,
            requirement_id=requirement_id,
            kind=kind,
            classification=classification,
            status=STATUS_SATISFIED,
            description="a documented fact",
            source=("https://example-provider.example/docs"
                    if is_fact else ""),
            source_type="Official documentation" if is_fact else "",
            note="",
        ))
    return ProviderFactCapture(provider="Example Provider", facts=tuple(facts))


def _ready_record(decision):
    capture = _ready_capture()
    return record_provider_selection(
        capture=capture,
        determination=evaluate_readiness(build_requirement_records(capture)),
        decision=decision)


# --------------------------------------------------------------------------------------
# 1. Module surface: a system rule, nothing else.
# --------------------------------------------------------------------------------------
class TestModuleSurface:
    def test_imports_are_only_dataclasses_and_the_two_prior_boundaries(self):
        tree = _module_tree()
        assert not [n for n in tree.body if isinstance(n, ast.Import)]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in {
                    "dataclasses",
                    "app.cad_engine.provider_readiness_gate",
                    "app.cad_engine.provider_selection_decision",
                }, f"unexpected import from {node.module!r}"

    def test_function_surface_is_exactly_the_authorization_rule(self):
        tree = _module_tree()
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert names == {"authorize_selection"}

    def test_result_vocabulary_is_exactly_two_values(self):
        assert AUTHORIZATION_AUTHORIZED == "AUTHORIZED"
        assert AUTHORIZATION_NOT_AUTHORIZED == "NOT_AUTHORIZED"
        assert AUTHORIZATION_RESULTS == {
            AUTHORIZATION_AUTHORIZED, AUTHORIZATION_NOT_AUTHORIZED,
        }
        assert len(AUTHORIZATION_RESULTS) == 2

    def test_no_io_process_env_or_clock_calls(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        forbidden = {
            "open", "print", "sleep", "input", "eval", "exec", "getenv",
            "environ", "read", "write", "read_text", "write_text", "mkdir",
            "unlink", "time", "random", "uuid", "datetime", "socket",
        }
        assert names.isdisjoint(forbidden), f"forbidden names used: {names & forbidden}"

    def test_no_decision_literals_outside_docstrings(self):
        tree = _module_tree()
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                if (node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings):
                assert node.value not in DECISIONS, (
                    "a decision literal appears in executable code; the "
                    "authorization rule must never introduce a decision"
                )

    def test_authorization_record_is_frozen_plain_data(self):
        assert dataclasses.is_dataclass(AuthorizationRecord)
        assert AuthorizationRecord.__dataclass_params__.frozen
        tree = _module_tree()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                assert not [n for n in node.body
                            if isinstance(n, ast.FunctionDef)]

    def test_no_credential_named_fields(self):
        fragments = {"key", "token", "secret", "password", "credential"}
        for field in dataclasses.fields(AuthorizationRecord):
            assert not any(
                fragment in field.name.lower() for fragment in fragments
            ), f"field {field.name} is credential-shaped"


# --------------------------------------------------------------------------------------
# 2. The six authorization cases.
# --------------------------------------------------------------------------------------
class TestAuthorizationCases:
    def test_select_plus_ready_is_authorized(self):
        auth = authorize_selection(_ready_record(DECISION_SELECT))
        assert auth.result == AUTHORIZATION_AUTHORIZED
        assert auth.reason == ""
        assert auth.readiness_overall == GATE_READY == "READY"
        assert auth.blocking_reasons == ()
        assert auth.selected_against_not_ready is False

    def test_select_plus_not_ready_is_not_authorized(self):
        auth = authorize_selection(_jev_record(DECISION_SELECT))
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED
        assert auth.reason == "readiness is 'NOT_READY'"
        assert auth.readiness_overall == GATE_NOT_READY == "NOT_READY"
        assert len(auth.blocking_reasons) == 4

    def test_decline_plus_ready_is_not_authorized(self):
        auth = authorize_selection(_ready_record(DECISION_DECLINE))
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED
        assert auth.reason == "decision is not 'SELECT'"
        assert auth.readiness_overall == GATE_READY == "READY"

    def test_decline_plus_not_ready_is_not_authorized(self):
        auth = authorize_selection(_jev_record(DECISION_DECLINE))
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED
        assert auth.reason == "decision is not 'SELECT'"
        assert auth.readiness_overall == GATE_NOT_READY

    def test_defer_plus_ready_is_not_authorized(self):
        auth = authorize_selection(_ready_record(DECISION_DEFER))
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED
        assert auth.reason == "decision is not 'SELECT'"
        assert auth.readiness_overall == GATE_READY == "READY"

    def test_defer_plus_not_ready_is_not_authorized(self):
        auth = authorize_selection(_jev_record(DECISION_DEFER))
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED
        assert auth.reason == "decision is not 'SELECT'"
        assert auth.readiness_overall == GATE_NOT_READY

    def test_only_select_plus_ready_can_authorize(self):
        authorized = [
            (decision, readiness)
            for decision in DECISIONS
            for readiness in (GATE_READY, GATE_NOT_READY)
            if authorize_selection(
                _ready_record(decision) if readiness == GATE_READY
                else _jev_record(decision)).result == AUTHORIZATION_AUTHORIZED
        ]
        assert authorized == [(DECISION_SELECT, GATE_READY)]

    def test_select_cannot_override_not_ready(self):
        auth = authorize_selection(_jev_record(DECISION_SELECT))
        assert auth.decision == DECISION_SELECT
        assert auth.readiness_overall == GATE_NOT_READY
        assert auth.selected_against_not_ready is True
        assert len(auth.blocking_reasons) == 4
        assert {reason.split()[3] for reason in auth.blocking_reasons} == REPORT_BLOCKERS
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED

    def test_ready_alone_cannot_authorize_decline_or_defer(self):
        for decision in (DECISION_DECLINE, DECISION_DEFER):
            auth = authorize_selection(_ready_record(decision))
            assert auth.readiness_overall == GATE_READY
            assert auth.result == AUTHORIZATION_NOT_AUTHORIZED


# --------------------------------------------------------------------------------------
# 3. Preservation, immutability, determinism.
# --------------------------------------------------------------------------------------
class TestPreservation:
    def test_all_source_fields_are_preserved_verbatim(self):
        record = _jev_record(DECISION_SELECT)
        auth = authorize_selection(record)
        for field in SOURCE_FIELDS:
            assert getattr(auth, field) == getattr(record, field), field

    def test_ready_path_fields_are_preserved_verbatim(self):
        record = _ready_record(DECISION_SELECT)
        auth = authorize_selection(record)
        for field in SOURCE_FIELDS:
            assert getattr(auth, field) == getattr(record, field), field

    def test_the_source_record_is_never_mutated(self):
        record = _jev_record(DECISION_SELECT)
        before = dataclasses.asdict(record)
        authorize_selection(record)
        assert dataclasses.asdict(record) == before

    def test_the_authorization_record_is_frozen(self):
        auth = authorize_selection(_jev_record(DECISION_SELECT))
        with pytest.raises(dataclasses.FrozenInstanceError):
            auth.result = AUTHORIZATION_AUTHORIZED
        with pytest.raises(dataclasses.FrozenInstanceError):
            auth.readiness_overall = GATE_READY

    def test_repeated_authorization_is_identical(self):
        record = _jev_record(DECISION_SELECT)
        first = authorize_selection(record)
        second = authorize_selection(record)
        assert first == second
        assert repr(first) == repr(second)
        assert dataclasses.asdict(first) == dataclasses.asdict(second)

    def test_authorization_records_are_hashable(self):
        assert len({hash(authorize_selection(_jev_record(d)))
                    for d in DECISIONS}) == 3

    def test_rationale_is_preserved_verbatim(self):
        record = record_provider_selection(
            capture=_capture(), determination=_determination(),
            decision=DECISION_SELECT, rationale="human audit note")
        assert authorize_selection(record).rationale == "human audit note"


# --------------------------------------------------------------------------------------
# 4. Validation: loud refusal, never silent repair.
# --------------------------------------------------------------------------------------
class TestValidation:
    def test_non_record_is_refused(self):
        with pytest.raises(TypeError, match="record must be a ProviderSelectionRecord"):
            authorize_selection("not a record")

    def test_forged_invalid_decision_is_refused(self):
        forged = ProviderSelectionRecord(
            provider="Jev (TypeSafe AI)", capture_digest="a" * 64,
            readiness_overall=GATE_READY, blocking_reasons=(),
            decision="MAYBE", rationale="",
            selected_against_not_ready=False)
        with pytest.raises(ValueError, match="invalid decision"):
            authorize_selection(forged)

    def test_forged_invalid_readiness_is_refused_not_normalized(self):
        forged = ProviderSelectionRecord(
            provider="Jev (TypeSafe AI)", capture_digest="a" * 64,
            readiness_overall="ready-ish", blocking_reasons=(),
            decision=DECISION_SELECT, rationale="",
            selected_against_not_ready=False)
        with pytest.raises(ValueError, match="invalid readiness_overall"):
            authorize_selection(forged)

    def test_forged_non_tuple_blocking_reasons_are_refused(self):
        forged = ProviderSelectionRecord(
            provider="Jev (TypeSafe AI)", capture_digest="a" * 64,
            readiness_overall=GATE_NOT_READY, blocking_reasons="fabricated",
            decision=DECISION_SELECT, rationale="",
            selected_against_not_ready=True)
        with pytest.raises(TypeError, match="blocking_reasons must be a tuple"):
            authorize_selection(forged)

    def test_forged_non_string_reason_elements_are_refused(self):
        forged = ProviderSelectionRecord(
            provider="Jev (TypeSafe AI)", capture_digest="a" * 64,
            readiness_overall=GATE_NOT_READY, blocking_reasons=(42,),
            decision=DECISION_SELECT, rationale="",
            selected_against_not_ready=True)
        with pytest.raises(TypeError, match="blocking_reasons must be a tuple"):
            authorize_selection(forged)

    def test_no_new_decision_can_enter_through_authorization(self):
        record = _jev_record(DECISION_DEFER)
        auth = authorize_selection(record)
        assert auth.decision == DECISION_DEFER
        assert auth.decision in DECISIONS
        assert auth.result in AUTHORIZATION_RESULTS


# --------------------------------------------------------------------------------------
# 5. No network, no environment, no credentials, no provider call.
# --------------------------------------------------------------------------------------
class TestNoNetworkEnvCredentials:
    def test_module_reads_no_environment(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "getenv" not in names
        assert "environ" not in names

    def test_authorization_is_identical_under_an_empty_environment(self, monkeypatch):
        record = _jev_record(DECISION_SELECT)
        before = authorize_selection(record)
        monkeypatch.setattr(os, "environ", {})
        assert authorize_selection(record) == before

    def test_no_credentials_are_embedded(self):
        for fragment in ("api_key =", "token =", "password =", "secret ="):
            assert fragment not in _module_source()

    def test_authorization_calls_nothing(self):
        # The only call targets are dataclass (the frozen record
        # decorator), isinstance, all, frozenset (the frozen value
        # constants), type (in refusal messages), the refusal exception
        # constructors and the frozen record constructor: no provider
        # call, no I/O call, no helper call.
        tree = _module_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                assert isinstance(func, ast.Name) and func.id in {
                    "dataclass", "isinstance", "all", "frozenset", "type",
                    "TypeError", "ValueError", "AuthorizationRecord",
                }, f"unexpected call target {ast.dump(func)[:80]}"

    def test_authorization_never_reconstructs_a_decision(self):
        # A genuine record flows through unchanged; the rule adds only the
        # result and reason — never a different decision.
        record = _jev_record(DECISION_DEFER)
        auth = authorize_selection(record)
        assert auth.decision == record.decision
        assert auth.decision == DECISION_DEFER


# --------------------------------------------------------------------------------------
# 6. The prior boundaries are unchanged.
# --------------------------------------------------------------------------------------
class TestUnchangedBoundaries:
    def test_7b9_gate_still_behaves_identically(self):
        fresh = evaluate_readiness(())
        assert fresh.overall == GATE_NOT_READY
        assert len(fresh.not_ready_reasons) == 24
        assert len(REQUIREMENT_INVENTORY) == 25

    def test_7c0_capture_still_behaves_identically(self):
        assert len(jev_fact_capture().facts) == 25
        determination = evaluate_capture()
        assert determination.overall == GATE_NOT_READY
        assert len(determination.not_ready_reasons) == 4

    def test_7c1_recording_still_behaves_identically(self):
        record = _jev_record(DECISION_SELECT)
        assert record.readiness_overall == GATE_NOT_READY
        assert record.selected_against_not_ready is True
        assert len(record.blocking_reasons) == 4

    def test_7c2_projection_still_behaves_identically(self):
        projection = build_selection_projection(_jev_record(DECISION_SELECT))
        assert projection.decision == DECISION_SELECT
        assert projection.readiness_overall == GATE_NOT_READY
        assert len(projection.blocking_reasons) == 4
