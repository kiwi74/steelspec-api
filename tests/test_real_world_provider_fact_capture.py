"""
Milestone 7C0 — PROVIDER FACT-CAPTURE RECORDING (tests).

Proves that the frozen, human-supplied Jev fact capture records the
fact-capture report exactly and that the REAL 7B9 gate consumes it:
NOT_READY, with exactly the four required UNVERIFIED gaps and nothing else.
Also proves the recording layer itself is deterministic, source-honest,
network-free, credential-free and carries no integration surface.
"""

import ast
import dataclasses
import os
from pathlib import Path

import pytest

from app.cad_engine import provider_fact_capture as pfc
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
    CLASSIFICATION_BEFORE_ANY_REAL_CALL,
    CLASSIFICATION_BEFORE_PRODUCTION_USE,
    CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
    CLASSIFICATION_OPTIONAL_LATER,
    GATE_NOT_READY,
    GATE_READY,
    KIND_LOCAL_CONTROL,
    KIND_PROVIDER_FACT,
    LOCAL_CONTROL_IDS,
    PROVIDER_FACT_IDS,
    REQUIREMENT_CLASSIFICATIONS,
    REQUIREMENT_IDS,
    REQUIREMENT_INVENTORY,
    STATUS_SATISFIED,
    STATUS_UNSATISFIED,
    STATUS_UNVERIFIED,
    GateDetermination,
    RequirementRecord,
    evaluate_readiness,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "cad_engine" / "provider_fact_capture.py"

REPORT_BLOCKERS = frozenset({
    "SECURITY_CONTROLS",
    "TIMEOUT_CANCELLATION_SEMANTICS",
    "AVAILABILITY_SLO",
    "DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
})
REPORT_UNVERIFIED = REPORT_BLOCKERS | {"STREAMING_BEHAVIOUR"}


def _module_source():
    return MODULE_PATH.read_text(encoding="utf-8")


def _module_tree():
    return ast.parse(_module_source())


def _capture():
    return jev_fact_capture()


def _records():
    return build_requirement_records()


def _fact(requirement_id, capture=None):
    capture = capture if capture is not None else _capture()
    return next(f for f in capture.facts if f.requirement_id == requirement_id)


def _all_ready_records():
    """One SATISFIED record per inventory requirement (sources on provider
    facts), independent of the capture module — used to prove the gate's own
    READY path is unchanged."""
    return tuple(
        RequirementRecord(
            requirement_id=requirement_id,
            kind=kind,
            classification=classification,
            status=STATUS_SATISFIED,
            source=("official documentation — typesafe.ai" if kind == KIND_PROVIDER_FACT
                    else ""),
        )
        for requirement_id, kind, classification in REQUIREMENT_INVENTORY
    )


# --------------------------------------------------------------------------------------
# 1. Module surface: recording only — no integration, no I/O, no clock, no credentials.
# --------------------------------------------------------------------------------------
class TestModuleSurface:
    def test_module_imports_only_dataclasses_and_the_7b9_gate(self):
        tree = _module_tree()
        assert not [n for n in tree.body if isinstance(n, ast.Import)]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in {
                    "dataclasses",
                    "app.cad_engine.provider_readiness_gate",
                }, f"unexpected import from {node.module!r}"

    def test_function_surface_is_exactly_the_three_recording_functions(self):
        tree = _module_tree()
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert names == {"jev_fact_capture", "build_requirement_records",
                         "evaluate_capture"}

    def test_no_function_can_name_an_integration_surface(self):
        tree = _module_tree()
        forbidden = {
            "send", "request", "completion", "inference", "chat", "invoke",
            "retry", "credential", "client", "adapter", "token", "secret",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert not any(
                    fragment in node.name.lower() for fragment in forbidden
                ), f"function {node.name!r} names an integration surface"

    def test_dataclasses_are_frozen_and_method_free(self):
        for cls in (CapturedFact, ProviderFactCapture):
            assert dataclasses.is_dataclass(cls)
            assert cls.__dataclass_params__.frozen
        tree = _module_tree()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                assert not [n for n in node.body
                            if isinstance(n, ast.FunctionDef)], (
                    f"{node.name!r} carries methods; the recording layer is "
                    "plain data only"
                )

    def test_module_has_no_io_process_or_clock_calls(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        forbidden = {
            "open", "print", "sleep", "input", "eval", "exec", "getenv",
            "environ", "read", "write", "read_text", "write_text", "mkdir",
            "unlink", "time", "random", "uuid", "datetime",
        }
        assert names.isdisjoint(forbidden), f"forbidden names used: {names & forbidden}"

    def test_no_access_date_or_clock_artifacts_in_source(self):
        source = _module_source()
        assert "2026-09-22" not in source
        assert "datetime" not in source
        assert "time.time" not in source
        assert "uuid" not in source

    def test_no_credential_named_fields(self):
        fragments = {"key", "token", "secret", "password", "credential"}
        for cls in (CapturedFact, ProviderFactCapture):
            for field in dataclasses.fields(cls):
                assert not any(
                    fragment in field.name.lower() for fragment in fragments
                ), f"field {cls.__name__}.{field.name} is credential-shaped"

    def test_no_workflow_or_artifact_content(self):
        source = _module_source()
        forbidden = {
            "AUTO", "CONFIRM", "GENERATED", "HUMAN_REVIEWED",
            ".pdf", ".dxf", "PL028", "22mm", "RP-00",
        }
        for fragment in forbidden:
            assert fragment not in source, (
                f"recording layer carries workflow/artifact content {fragment!r}"
            )


# --------------------------------------------------------------------------------------
# 2. Completeness: the capture records the whole pinned 7B9 inventory, nothing else.
# --------------------------------------------------------------------------------------
class TestCaptureCompleteness:
    def test_capture_covers_the_pinned_25_requirement_inventory(self):
        capture = _capture()
        assert len(capture.facts) == 25
        assert {f.requirement_id for f in capture.facts} == REQUIREMENT_IDS

    def test_capture_splits_into_19_provider_facts_and_6_local_controls(self):
        capture = _capture()
        provider_ids = {f.requirement_id for f in capture.facts
                        if f.kind == KIND_PROVIDER_FACT}
        control_ids = {f.requirement_id for f in capture.facts
                       if f.kind == KIND_LOCAL_CONTROL}
        assert provider_ids == PROVIDER_FACT_IDS
        assert control_ids == LOCAL_CONTROL_IDS
        assert len(provider_ids) == 19
        assert len(control_ids) == 6

    def test_capture_authored_order_matches_the_inventory_order(self):
        capture = _capture()
        assert [f.requirement_id for f in capture.facts] == [
            entry[0] for entry in REQUIREMENT_INVENTORY
        ]

    def test_classifications_are_pinned_to_the_inventory(self):
        expected = dict(REQUIREMENT_CLASSIFICATIONS)
        for fact in _capture().facts:
            assert fact.classification == expected[fact.requirement_id]

    def test_kinds_are_pinned_to_the_inventory(self):
        expected = {entry[0]: entry[1] for entry in REQUIREMENT_INVENTORY}
        for fact in _capture().facts:
            assert fact.kind == expected[fact.requirement_id]

    def test_no_duplicate_requirement_records(self):
        capture = _capture()
        ids = [f.requirement_id for f in capture.facts]
        assert len(set(ids)) == len(ids) == 25

    def test_capture_is_frozen_plain_data(self):
        capture = _capture()
        with pytest.raises(dataclasses.FrozenInstanceError):
            capture.provider = "something else"
        with pytest.raises(dataclasses.FrozenInstanceError):
            capture.facts[0].status = "tampered"
        assert len({hash(f) for f in capture.facts}) == 25


# --------------------------------------------------------------------------------------
# 3. Statuses: the report's five gaps, the four required blockers, zero UNSATISFIED.
# --------------------------------------------------------------------------------------
class TestRecordedStatuses:
    def test_exactly_the_five_report_gaps_are_unverified(self):
        unverified = {f.requirement_id for f in _capture().facts
                      if f.status == STATUS_UNVERIFIED}
        assert unverified == REPORT_UNVERIFIED

    def test_the_four_required_blockers_are_all_unverified_and_required(self):
        capture = _capture()
        for requirement_id in REPORT_BLOCKERS:
            fact = _fact(requirement_id, capture)
            assert fact.status == STATUS_UNVERIFIED
            assert fact.classification != CLASSIFICATION_OPTIONAL_LATER

    def test_streaming_behaviour_is_unverified_but_optional(self):
        fact = _fact("STREAMING_BEHAVIOUR")
        assert fact.status == STATUS_UNVERIFIED
        assert fact.classification == CLASSIFICATION_OPTIONAL_LATER

    def test_no_unsatisfied_status_anywhere(self):
        assert not [f for f in _capture().facts
                    if f.status == STATUS_UNSATISFIED]

    def test_every_non_gap_requirement_is_satisfied(self):
        for fact in _capture().facts:
            if fact.requirement_id not in REPORT_UNVERIFIED:
                assert fact.status == STATUS_SATISFIED

    def test_all_six_local_controls_are_satisfied(self):
        for fact in _capture().facts:
            if fact.kind == KIND_LOCAL_CONTROL:
                assert fact.status == STATUS_SATISFIED

    def test_unverified_is_recorded_as_absence_of_evidence_not_failure(self):
        for fact in _capture().facts:
            if fact.status != STATUS_UNVERIFIED:
                continue
            text = (fact.description + " " + fact.note + " " + fact.source).lower()
            assert "fail" not in text, (
                f"{fact.requirement_id} records UNVERIFIED as a failure"
            )
            assert "UNVERIFIED" in (fact.description + " " + fact.note)


# --------------------------------------------------------------------------------------
# 4. Source integrity: real references on SATISFIED facts, none fabricated on gaps.
# --------------------------------------------------------------------------------------
class TestSourceIntegrity:
    def test_every_satisfied_provider_fact_carries_a_real_source(self):
        for fact in _capture().facts:
            if fact.kind == KIND_PROVIDER_FACT and fact.status == STATUS_SATISFIED:
                assert fact.source.strip(), fact.requirement_id
                assert "http" in fact.source, fact.requirement_id
                assert "typesafe.ai" in fact.source, fact.requirement_id
                assert fact.source_type.strip(), fact.requirement_id

    def test_every_unverified_fact_has_no_source_at_all(self):
        for fact in _capture().facts:
            if fact.status == STATUS_UNVERIFIED:
                assert fact.source == "", fact.requirement_id
                assert fact.source_type == "", fact.requirement_id

    def test_local_controls_have_empty_sources_and_still_pass_the_gate(self):
        for fact in _capture().facts:
            if fact.kind == KIND_LOCAL_CONTROL:
                assert fact.source == ""
                assert fact.source_type == ""
        assert evaluate_capture().overall == GATE_NOT_READY  # gate ran, no refusal

    def test_sources_name_real_documents(self):
        markers = {
            "api.md", "models.md", "legal/", "status.typesafe.ai",
            "trust.typesafe.ai", "sdk/",
        }
        for fact in _capture().facts:
            if fact.kind == KIND_PROVIDER_FACT and fact.status == STATUS_SATISFIED:
                assert any(marker in fact.source for marker in markers), (
                    f"{fact.requirement_id}: {fact.source!r}"
                )


# --------------------------------------------------------------------------------------
# 5. Gate integration: the REAL 7B9 gate consumes the recorded facts.
# --------------------------------------------------------------------------------------
class TestGateIntegration:
    def test_evaluate_capture_returns_the_real_gate_determination(self):
        determination = evaluate_capture()
        assert isinstance(determination, GateDetermination)
        assert determination.overall == GATE_NOT_READY == "NOT_READY"

    def test_exactly_four_blocking_reasons_naming_the_report_gaps(self):
        determination = evaluate_capture()
        reasons = determination.not_ready_reasons
        assert len(reasons) == 4
        named = {reason.split()[3] for reason in reasons}
        assert named == REPORT_BLOCKERS
        for reason in reasons:
            assert any(
                requirement_id in reason for requirement_id in REPORT_BLOCKERS
            )

    def test_every_blocker_is_required_and_unverified(self):
        for evaluation in evaluate_capture().evaluations:
            if evaluation.blocks:
                assert evaluation.required
                assert evaluation.status == STATUS_UNVERIFIED
                assert evaluation.requirement_id in REPORT_BLOCKERS

    def test_streaming_is_reported_but_never_blocks(self):
        evaluation = next(
            e for e in evaluate_capture().evaluations
            if e.requirement_id == "STREAMING_BEHAVIOUR"
        )
        assert evaluation.required is False
        assert evaluation.blocks is False
        assert evaluation.status == STATUS_UNVERIFIED

    def test_all_other_requirements_do_not_block(self):
        determination = evaluate_capture()
        for evaluation in determination.evaluations:
            if evaluation.requirement_id not in REPORT_BLOCKERS:
                assert evaluation.blocks is False
                if evaluation.requirement_id in REPORT_UNVERIFIED:
                    # only STREAMING_BEHAVIOUR: reported UNVERIFIED, never blocking
                    assert evaluation.status == STATUS_UNVERIFIED
                else:
                    assert evaluation.status == STATUS_SATISFIED

    def test_the_recorded_records_pass_through_the_real_gate_function(self):
        assert evaluate_readiness(_records()) == evaluate_capture()

    def test_evaluate_capture_delegates_to_the_imported_gate_symbol(self, monkeypatch):
        calls = []
        original = pfc.evaluate_readiness

        def spy(records):
            calls.append(records)
            return original(records)

        monkeypatch.setattr(pfc, "evaluate_readiness", spy)
        result = pfc.evaluate_capture()
        assert len(calls) == 1
        assert calls[0] == _records()
        assert result == evaluate_readiness(_records())

    def test_records_are_the_gates_own_requirement_records(self):
        records = _records()
        assert all(isinstance(r, RequirementRecord) for r in records)
        assert len(records) == 25
        by_id = {r.requirement_id: r for r in records}
        plain = _fact("MODEL_VERSION_RECORDING")
        assert by_id["MODEL_VERSION_RECORDING"].note == plain.description
        training = _fact("DATA_TRAINING_USE_POLICY")
        assert by_id["DATA_TRAINING_USE_POLICY"].note == (
            f"{training.description} [note: {training.note}]"
        )

    def test_the_capture_is_the_default_for_every_entry_point(self):
        assert build_requirement_records() == build_requirement_records(_capture())
        assert evaluate_capture() == evaluate_capture(_capture())


# --------------------------------------------------------------------------------------
# 6. Real-world facts: the report's verified findings appear verbatim.
# --------------------------------------------------------------------------------------
class TestRealWorldFacts:
    def test_endpoint_and_bearer_auth_are_recorded(self):
        description = _fact("API_INPUT_OUTPUT_CONTRACT").description
        assert "POST https://api.typesafe.ai/v1/systemone" in description
        assert "Bearer" in description

    def test_versioned_model_id_and_moving_aliases_are_recorded(self):
        description = _fact("MODEL_VERSION_PINNING").description
        assert "jev-1.13.0" in description
        assert "jev-latest" in description
        assert "jev-preview" in description
        assert "pin that version's ID" in description

    def test_no_training_commitment_is_recorded_verbatim(self):
        assert "will not train" in _fact("DATA_TRAINING_USE_POLICY").source

    def test_retention_is_recorded_as_long_as_necessary(self):
        description = _fact("DATA_RETENTION").description
        assert "as long as" in description
        assert "necessary" in description

    def test_telemetry_is_kept_separate_from_no_training(self):
        fact = _fact("DATA_TRAINING_USE_POLICY")
        assert "Telemetry" in fact.note
        assert "learnings" in fact.note
        assert "Telemetry" not in fact.description
        assert "learnings" not in fact.description

    def test_the_four_required_gaps_stay_unverified(self):
        assert REPORT_BLOCKERS <= {
            f.requirement_id for f in _capture().facts
            if f.status == STATUS_UNVERIFIED
        }

    def test_us_hosting_is_recorded(self):
        assert "United States" in _fact("DATA_RESIDENCY").description

    def test_72_hour_breach_notification_is_recorded(self):
        assert "72 hours" in _fact("INCIDENT_BREACH_NOTIFICATION_TERMS").description

    def test_rate_limits_are_recorded(self):
        description = _fact("RATE_LIMITS").description
        assert "250,000" in description
        assert "1,200" in description

    def test_context_window_and_answer_shape_limits_are_recorded(self):
        description = _fact("REQUEST_RESPONSE_SIZE_LIMITS").description
        assert "64k" in description
        assert "32k" in description
        assert "255" in description
        assert "2 to 10" in description

    def test_pricing_is_recorded(self):
        description = _fact("COST_MODEL").description
        assert "$0.042" in description
        assert "free" in description


# --------------------------------------------------------------------------------------
# 7. Determinism: same bytes in, same records out — every time.
# --------------------------------------------------------------------------------------
class TestDeterminism:
    def test_two_captures_are_identical(self):
        first, second = _capture(), _capture()
        assert first == second
        assert repr(first) == repr(second)
        assert dataclasses.asdict(first) == dataclasses.asdict(second)

    def test_two_record_builds_are_identical(self):
        first, second = _records(), _records()
        assert first == second
        assert dataclasses.asdict(first[0]) == dataclasses.asdict(second[0])

    def test_two_evaluations_are_identical(self):
        first, second = evaluate_capture(), evaluate_capture()
        assert first == second
        assert first.not_ready_reasons == second.not_ready_reasons

    def test_capture_and_records_are_hashable(self):
        assert len({hash(f) for f in _capture().facts}) == 25
        assert len({hash(r) for r in _records()}) == 25


# --------------------------------------------------------------------------------------
# 8. Order independence: authored order is presentation only.
# --------------------------------------------------------------------------------------
class TestOrderIndependence:
    def test_reversed_fact_order_yields_the_identical_determination(self):
        capture = _capture()
        reversed_capture = ProviderFactCapture(
            provider=capture.provider, facts=tuple(reversed(capture.facts)))
        assert evaluate_capture(reversed_capture) == evaluate_capture()

    def test_reversed_record_order_yields_the_identical_determination(self):
        assert evaluate_readiness(tuple(reversed(_records()))) == evaluate_capture()


# --------------------------------------------------------------------------------------
# 9. Local-control separation: SteelSpec's six controls are SteelSpec's, not Jev's.
# --------------------------------------------------------------------------------------
class TestLocalControlSeparation:
    def test_controls_are_steelspec_owned(self):
        controls = [f for f in _capture().facts if f.kind == KIND_LOCAL_CONTROL]
        assert {f.requirement_id for f in controls} == LOCAL_CONTROL_IDS
        assert all(f.provider == CONTROL_OWNER for f in controls)
        assert CONTROL_OWNER == "SteelSpec"

    def test_provider_facts_are_provider_owned(self):
        facts = [f for f in _capture().facts if f.kind == KIND_PROVIDER_FACT]
        assert {f.requirement_id for f in facts} == PROVIDER_FACT_IDS
        assert all(f.provider == PROVIDER_NAME for f in facts)

    def test_control_and_provider_ownership_never_mix(self):
        assert CONTROL_OWNER != PROVIDER_NAME

    def test_every_control_notes_it_is_not_a_provider_fact(self):
        for fact in _capture().facts:
            if fact.kind == KIND_LOCAL_CONTROL:
                assert "not a provider fact" in fact.note


# --------------------------------------------------------------------------------------
# 10. Validation: loud refusal, never silent repair.
# --------------------------------------------------------------------------------------
class TestValidationRefusals:
    def test_rejects_non_capture(self):
        with pytest.raises(TypeError):
            build_requirement_records("not a capture")

    def test_rejects_non_fact_elements(self):
        with pytest.raises(TypeError):
            build_requirement_records(
                ProviderFactCapture(provider=PROVIDER_NAME, facts=("not-a-fact",)))

    def test_rejects_non_string_fields(self):
        capture = _capture()
        broken = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(capture.facts[0], source=123)
                if i == 0 else f
                for i, f in enumerate(capture.facts)))
        with pytest.raises(TypeError, match="source must be a str"):
            build_requirement_records(broken)

    def test_rejects_incomplete_capture(self):
        capture = _capture()
        incomplete = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(f for f in capture.facts
                        if f.requirement_id != "SECURITY_CONTROLS"))
        with pytest.raises(ValueError, match="incomplete"):
            build_requirement_records(incomplete)

    def test_rejects_duplicate_requirements(self):
        capture = _capture()
        duplicated = ProviderFactCapture(
            provider=capture.provider,
            facts=(capture.facts[0],) + capture.facts)
        with pytest.raises(ValueError, match="duplicate"):
            build_requirement_records(duplicated)

    def test_rejects_unknown_requirements(self):
        capture = _capture()
        unknown = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(capture.facts[0], requirement_id="MADE_UP")
                if i == 0 else f
                for i, f in enumerate(capture.facts)))
        with pytest.raises(ValueError, match="unknown requirement"):
            build_requirement_records(unknown)

    def test_rejects_satisfied_provider_fact_without_source(self):
        capture = _capture()
        sourceless = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(f, source="")
                if f.requirement_id == "API_INPUT_OUTPUT_CONTRACT" else f
                for f in capture.facts))
        with pytest.raises(ValueError, match="without a source"):
            build_requirement_records(sourceless)

    def test_rejects_unverified_fact_with_a_fabricated_source(self):
        capture = _capture()
        fabricated = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(f, source="some website")
                if f.requirement_id == "SECURITY_CONTROLS" else f
                for f in capture.facts))
        with pytest.raises(ValueError, match="fabricated source"):
            build_requirement_records(fabricated)

    def test_rejects_invalid_status(self):
        capture = _capture()
        broken = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(capture.facts[0], status="MAYBE")
                if i == 0 else f
                for i, f in enumerate(capture.facts)))
        with pytest.raises(ValueError, match="invalid status"):
            build_requirement_records(broken)

    def test_rejects_invalid_kind(self):
        capture = _capture()
        broken = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(capture.facts[0], kind="HUNCH")
                if i == 0 else f
                for i, f in enumerate(capture.facts)))
        with pytest.raises(ValueError, match="invalid kind"):
            build_requirement_records(broken)

    def test_rejects_invalid_classification(self):
        capture = _capture()
        broken = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(capture.facts[0], classification="SOMETIME")
                if i == 0 else f
                for i, f in enumerate(capture.facts)))
        with pytest.raises(ValueError, match="invalid classification"):
            build_requirement_records(broken)

    def test_rejects_classification_that_disagrees_with_the_inventory(self):
        capture = _capture()
        smuggled = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(
                    f, classification=CLASSIFICATION_BEFORE_ANY_REAL_CALL)
                if f.requirement_id == "STREAMING_BEHAVIOUR" else f
                for f in capture.facts))
        with pytest.raises(ValueError, match="does not match the inventory"):
            build_requirement_records(smuggled)

    def test_rejects_kind_that_disagrees_with_the_inventory(self):
        capture = _capture()
        smuggled = ProviderFactCapture(
            provider=capture.provider,
            facts=tuple(
                dataclasses.replace(f, kind=KIND_LOCAL_CONTROL)
                if f.requirement_id == "DATA_RETENTION" else f
                for f in capture.facts))
        with pytest.raises(ValueError, match="does not match the inventory"):
            build_requirement_records(smuggled)


# --------------------------------------------------------------------------------------
# 11. No network, no environment, no credentials.
# --------------------------------------------------------------------------------------
class TestNoNetworkEnvCredentials:
    def test_module_reads_no_environment(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "getenv" not in names
        assert "environ" not in names

    def test_records_are_identical_under_an_empty_environment(self, monkeypatch):
        before = _records()
        monkeypatch.setattr(os, "environ", {})
        assert _records() == before

    def test_no_network_libraries_can_be_imported(self):
        # Import-statement fragments only: the fact text legitimately contains
        # the word "requests" (the no-training commitment quotes it).
        source = _module_source()
        for fragment in ("import socket", "import http", "import urllib",
                         "import requests", "import httpx", "import aiohttp"):
            assert fragment not in source

    def test_no_credentials_are_embedded(self):
        fragments = {"api_key =", "token =", "password =", "secret ="}
        for fragment in fragments:
            assert fragment not in _module_source()


# --------------------------------------------------------------------------------------
# 12. The 7B9 gate itself is unchanged.
# --------------------------------------------------------------------------------------
class TestGateUnchanged:
    def test_the_7b9_inventory_is_still_pinned_at_25(self):
        assert len(REQUIREMENT_INVENTORY) == 25
        assert len(REQUIREMENT_IDS) == 25
        assert len(PROVIDER_FACT_IDS) == 19
        assert len(LOCAL_CONTROL_IDS) == 6

    def test_a_fresh_gate_still_fails_closed_with_24_reasons(self):
        determination = evaluate_readiness(())
        assert determination.overall == GATE_NOT_READY
        assert len(determination.not_ready_reasons) == 24
        assert len(determination.evaluations) == 25

    def test_an_all_satisfied_gate_still_reads_ready(self):
        determination = evaluate_readiness(_all_ready_records())
        assert determination.overall == GATE_READY == "READY"
        assert determination.not_ready_reasons == ()

    def test_the_gate_still_refuses_a_satisfied_provider_fact_without_source(self):
        records = tuple(
            dataclasses.replace(record, source="")
            if record.requirement_id == "RATE_LIMITS" else record
            for record in _all_ready_records())
        with pytest.raises(ValueError, match="without a source"):
            evaluate_readiness(records)
