"""
Milestone 7C1 — PROVIDER SELECTION DECISION BOUNDARY (tests).

Proves the recording layer records a HUMAN decision against the genuine,
unmodified 7B9 readiness: SELECT never overrides NOT_READY, DECLINE/DEFER
never mutate the gate, invalid decisions are refused loudly, the decision
can never be inferred by the system, and the layer is deterministic,
network-free, environment-free and credential-free.
"""

import ast
import dataclasses
import inspect
import os
from pathlib import Path

import pytest

from app.cad_engine import provider_selection_decision as psd
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
    KIND_LOCAL_CONTROL,
    KIND_PROVIDER_FACT,
    REQUIREMENT_INVENTORY,
    STATUS_SATISFIED,
    GateDetermination,
    evaluate_readiness,
)
from app.cad_engine.provider_selection_decision import (
    DECISION_DECLINE,
    DECISION_DEFER,
    DECISION_SELECT,
    DECISIONS,
    ProviderSelectionRecord,
    capture_digest,
    record_provider_selection,
)

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "app" / "cad_engine" / "provider_selection_decision.py")

REPORT_BLOCKERS = frozenset({
    "SECURITY_CONTROLS",
    "TIMEOUT_CANCELLATION_SEMANTICS",
    "AVAILABILITY_SLO",
    "DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
})


def _module_source():
    return MODULE_PATH.read_text(encoding="utf-8")


def _module_tree():
    return ast.parse(_module_source())


def _capture():
    return jev_fact_capture()


def _determination():
    return evaluate_capture()


def _record(decision=DECISION_SELECT, rationale=""):
    return record_provider_selection(
        capture=_capture(),
        determination=_determination(),
        decision=decision,
        rationale=rationale,
    )


def _ready_capture():
    """A synthetic, fully SATISFIED capture — every provider fact carries a
    source — so the genuine gate reads READY. Used only to prove the
    SELECT-over-READY path and that the layer is not Jev-locked."""
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


# --------------------------------------------------------------------------------------
# 1. Module surface: recording only.
# --------------------------------------------------------------------------------------
class TestModuleSurface:
    def test_imports_are_only_dataclasses_hashlib_and_the_two_prior_boundaries(self):
        tree = _module_tree()
        plain = [n.names[0].name for n in tree.body if isinstance(n, ast.Import)]
        assert plain == ["hashlib"]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in {
                    "dataclasses",
                    "app.cad_engine.provider_readiness_gate",
                    "app.cad_engine.provider_fact_capture",
                }, f"unexpected import from {node.module!r}"

    def test_function_surface_is_exactly_digest_and_record(self):
        tree = _module_tree()
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert names == {"capture_digest", "record_provider_selection"}

    def test_no_io_process_env_or_clock_calls(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        forbidden = {
            "open", "print", "sleep", "input", "eval", "exec", "getenv",
            "environ", "read", "write", "read_text", "write_text", "mkdir",
            "unlink", "time", "random", "uuid", "datetime", "socket",
        }
        assert names.isdisjoint(forbidden), f"forbidden names used: {names & forbidden}"

    def test_no_credential_named_fields(self):
        fragments = {"key", "token", "secret", "password", "credential"}
        for field in dataclasses.fields(ProviderSelectionRecord):
            assert not any(
                fragment in field.name.lower() for fragment in fragments
            ), f"field {field.name} is credential-shaped"

    def test_record_is_frozen_plain_data(self):
        assert dataclasses.is_dataclass(ProviderSelectionRecord)
        assert ProviderSelectionRecord.__dataclass_params__.frozen
        tree = _module_tree()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                assert not [n for n in node.body
                            if isinstance(n, ast.FunctionDef)]

    def test_no_function_returns_a_decision_constant(self):
        tree = _module_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
                assert node.value.value not in DECISIONS, (
                    "a function returns a hard-coded decision; the system "
                    "must never pick one"
                )

    def test_the_decision_argument_has_no_default(self):
        signature = inspect.signature(record_provider_selection)
        assert signature.parameters["decision"].default is inspect.Parameter.empty
        assert signature.parameters["decision"].kind == inspect.Parameter.KEYWORD_ONLY


# --------------------------------------------------------------------------------------
# 2. Decision outcomes: exactly three, exact strings.
# --------------------------------------------------------------------------------------
class TestDecisionOutcomes:
    def test_exactly_three_decisions_exist(self):
        assert DECISIONS == {DECISION_SELECT, DECISION_DECLINE, DECISION_DEFER}
        assert len(DECISIONS) == 3

    def test_decision_constants_are_the_exact_strings(self):
        assert DECISION_SELECT == "SELECT"
        assert DECISION_DECLINE == "DECLINE"
        assert DECISION_DEFER == "DEFER"


# --------------------------------------------------------------------------------------
# 3. Real-world: recording a SELECT over the genuine Jev NOT_READY.
# --------------------------------------------------------------------------------------
class TestRecordingRealJevSelection:
    def test_select_is_recorded_against_the_real_not_ready(self):
        record = _record(DECISION_SELECT)
        assert record.provider == PROVIDER_NAME
        assert record.decision == DECISION_SELECT
        assert record.readiness_overall == GATE_NOT_READY == "NOT_READY"
        assert record.selected_against_not_ready is True
        assert record.rationale == ""

    def test_the_exact_blocking_reasons_are_preserved(self):
        record = _record(DECISION_SELECT)
        determination = _determination()
        assert record.blocking_reasons == determination.not_ready_reasons
        assert len(record.blocking_reasons) == 4
        assert {reason.split()[3] for reason in record.blocking_reasons} == REPORT_BLOCKERS

    def test_the_capture_digest_references_the_frozen_capture(self):
        record = _record(DECISION_SELECT)
        assert record.capture_digest == capture_digest(_capture())
        assert len(record.capture_digest) == 64
        assert all(ch in "0123456789abcdef" for ch in record.capture_digest)

    def test_the_rationale_is_recorded_verbatim(self):
        rationale = "Human note: proceed to sandboxed evaluation only."
        record = _record(DECISION_SELECT, rationale=rationale)
        assert record.rationale == rationale

    def test_the_record_is_frozen(self):
        record = _record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.decision = DECISION_DECLINE
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.readiness_overall = GATE_READY


# --------------------------------------------------------------------------------------
# 4. The semantic rule: SELECT never overrides 7B9.
# --------------------------------------------------------------------------------------
class TestSelectDoesNotOverrideReadiness:
    def test_select_leaves_the_readiness_recorded_as_not_ready(self):
        record = _record(DECISION_SELECT)
        assert record.readiness_overall == GATE_NOT_READY
        assert record.readiness_overall != GATE_READY

    def test_select_does_not_mutate_the_gate(self):
        before = _determination()
        _record(DECISION_SELECT)
        after = evaluate_capture()
        assert after == before
        assert after.overall == GATE_NOT_READY
        assert len(after.not_ready_reasons) == 4

    def test_select_does_not_mutate_the_capture(self):
        before = _capture()
        _record(DECISION_SELECT)
        assert _capture() == before

    def test_the_flag_is_honest_metadata_not_an_override(self):
        record = _record(DECISION_SELECT)
        assert record.selected_against_not_ready is True
        assert record.decision == DECISION_SELECT
        assert record.readiness_overall == GATE_NOT_READY

    def test_a_select_on_a_ready_capture_records_ready_without_the_flag(self):
        capture = _ready_capture()
        determination = evaluate_readiness(build_requirement_records(capture))
        assert determination.overall == GATE_READY
        record = record_provider_selection(
            capture=capture, determination=determination,
            decision=DECISION_SELECT, rationale="ready to proceed")
        assert record.readiness_overall == GATE_READY == "READY"
        assert record.selected_against_not_ready is False
        assert record.blocking_reasons == ()
        assert record.provider == "Example Provider"

    def test_a_select_over_not_ready_never_becomes_ready(self):
        record = _record(DECISION_SELECT)
        assert record.readiness_overall == GATE_NOT_READY
        assert len(record.blocking_reasons) == 4
        assert record.selected_against_not_ready is True


# --------------------------------------------------------------------------------------
# 5. DECLINE and DEFER: recorded, gate untouched.
# --------------------------------------------------------------------------------------
class TestDeclineAndDefer:
    def test_decline_is_recorded_against_not_ready(self):
        record = _record(DECISION_DECLINE)
        assert record.decision == DECISION_DECLINE
        assert record.readiness_overall == GATE_NOT_READY
        assert record.selected_against_not_ready is False
        assert len(record.blocking_reasons) == 4

    def test_defer_is_recorded_against_not_ready(self):
        record = _record(DECISION_DEFER)
        assert record.decision == DECISION_DEFER
        assert record.readiness_overall == GATE_NOT_READY
        assert record.selected_against_not_ready is False
        assert len(record.blocking_reasons) == 4

    def test_decline_and_defer_do_not_mutate_the_gate(self):
        before = _determination()
        _record(DECISION_DECLINE)
        _record(DECISION_DEFER)
        assert evaluate_capture() == before

    def test_the_decision_is_the_only_field_that_changes(self):
        select = _record(DECISION_SELECT)
        defer = _record(DECISION_DEFER)
        assert (select.readiness_overall == defer.readiness_overall
                == GATE_NOT_READY)
        assert select.blocking_reasons == defer.blocking_reasons
        assert select.capture_digest == defer.capture_digest
        assert select.provider == defer.provider
        assert select.decision != defer.decision


# --------------------------------------------------------------------------------------
# 6. Validation: loud refusal, never silent repair.
# --------------------------------------------------------------------------------------
class TestValidation:
    def test_non_string_decision_is_refused(self):
        with pytest.raises(TypeError, match="decision must be a str"):
            record_provider_selection(
                capture=_capture(), determination=_determination(),
                decision=None)

    def test_unknown_decision_is_refused(self):
        with pytest.raises(ValueError, match="invalid decision 'MAYBE'"):
            _record(decision="MAYBE")

    def test_lowercase_select_is_refused(self):
        with pytest.raises(ValueError, match="invalid decision"):
            _record(decision="select")

    def test_empty_decision_is_refused(self):
        with pytest.raises(ValueError, match="invalid decision"):
            _record(decision="")

    def test_missing_decision_is_refused(self):
        with pytest.raises(TypeError):
            record_provider_selection(
                capture=_capture(), determination=_determination())

    def test_non_string_rationale_is_refused(self):
        with pytest.raises(TypeError, match="rationale must be a str"):
            _record(rationale=42)

    def test_non_capture_is_refused(self):
        with pytest.raises(TypeError, match="capture must be a ProviderFactCapture"):
            record_provider_selection(
                capture="not a capture", determination=_determination(),
                decision=DECISION_SELECT)

    def test_non_determination_is_refused(self):
        with pytest.raises(TypeError, match="determination must be a GateDetermination"):
            record_provider_selection(
                capture=_capture(), determination="READY",
                decision=DECISION_SELECT)

    def test_a_forged_ready_determination_is_refused(self):
        forged = GateDetermination(
            overall=GATE_READY, evaluations=(), not_ready_reasons=())
        with pytest.raises(ValueError, match="does not match the genuine"):
            record_provider_selection(
                capture=_capture(), determination=forged,
                decision=DECISION_SELECT)

    def test_a_determination_from_another_capture_is_refused(self):
        ready_determination = evaluate_readiness(
            build_requirement_records(_ready_capture()))
        assert ready_determination.overall == GATE_READY
        with pytest.raises(ValueError, match="does not match the genuine"):
            record_provider_selection(
                capture=_capture(), determination=ready_determination,
                decision=DECISION_SELECT)

    def test_an_incomplete_capture_is_refused(self):
        capture = _capture()
        incomplete = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(f for f in capture.facts
                        if f.requirement_id != "SECURITY_CONTROLS"))
        with pytest.raises(ValueError, match="incomplete"):
            record_provider_selection(
                capture=incomplete,
                determination=GateDetermination(
                    overall=GATE_NOT_READY, evaluations=(), not_ready_reasons=()),
                decision=DECISION_SELECT)


# --------------------------------------------------------------------------------------
# 7. Determinism and auditability.
# --------------------------------------------------------------------------------------
class TestDeterminism:
    def test_two_records_are_identical(self):
        first, second = _record(), _record()
        assert first == second
        assert repr(first) == repr(second)
        assert dataclasses.asdict(first) == dataclasses.asdict(second)

    def test_two_digests_are_identical(self):
        assert capture_digest(_capture()) == capture_digest(_capture())

    def test_a_different_capture_digests_differently(self):
        assert capture_digest(_capture()) != capture_digest(_ready_capture())

    def test_records_are_hashable(self):
        assert hash(_record(DECISION_SELECT)) == hash(_record(DECISION_SELECT))
        assert len({hash(_record(d)) for d in DECISIONS}) == 3

    def test_the_record_establishes_what_was_decided_against(self):
        record = _record(DECISION_SELECT)
        assert record.provider == PROVIDER_NAME
        assert record.readiness_overall == GATE_NOT_READY
        assert record.blocking_reasons == _determination().not_ready_reasons
        assert record.decision == DECISION_SELECT
        assert record.capture_digest == capture_digest(_capture())


# --------------------------------------------------------------------------------------
# 8. No network, no environment, no credentials.
# --------------------------------------------------------------------------------------
class TestNoNetworkEnvCredentials:
    def test_module_reads_no_environment(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "getenv" not in names
        assert "environ" not in names

    def test_records_are_identical_under_an_empty_environment(self, monkeypatch):
        before = _record(DECISION_SELECT, rationale="unchanged")
        monkeypatch.setattr(os, "environ", {})
        after = record_provider_selection(
            capture=_capture(), determination=_determination(),
            decision=DECISION_SELECT, rationale="unchanged")
        assert after == before

    def test_no_credentials_are_embedded(self):
        for fragment in ("api_key =", "token =", "password =", "secret ="):
            assert fragment not in _module_source()


# --------------------------------------------------------------------------------------
# 9. The prior boundaries are unchanged.
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
