"""
Milestone 7D0 — JEV PROVIDER CONTRACT DISCOVERY (tests).

Proves the discovery record is exactly what the milestone permits: a frozen,
deterministic, network-free, credential-free, environment-independent
evidence record over the five UNVERIFIED 7C0 requirements; evidence
classifications drawn from exactly VERIFIED / UNVERIFIED / NOT_APPLICABLE /
CONFLICTING; every consulted source recorded; uncertainty preserved and
never upgraded into SATISFIED; no decision, no authorization, no gate
mutation and no adapter/client/registry surface. Prior boundaries
(7B9/7C0/7C1/7C2/7C3) are regression-proven inside this file.
"""

import ast
import dataclasses
import os
from pathlib import Path

import pytest

from app.cad_engine.integration_authorization import (
    AUTHORIZATION_NOT_AUTHORIZED,
    authorize_selection,
)
from app.cad_engine.jev_contract_discovery import (
    DISCOVERY_CONFLICTING,
    DISCOVERY_NOT_APPLICABLE,
    DISCOVERY_REQUIREMENT_IDS,
    DISCOVERY_STATUSES,
    DISCOVERY_UNVERIFIED,
    DISCOVERY_VERIFIED,
    ContractDiscovery,
    DiscoveryEvidence,
    jev_contract_discovery,
)
from app.cad_engine.provider_fact_capture import (
    PROVIDER_NAME,
    STATUS_UNVERIFIED,
    evaluate_capture,
    jev_fact_capture,
)
from app.cad_engine.provider_readiness_gate import (
    GATE_NOT_READY,
    REQUIREMENT_IDS,
    REQUIREMENT_INVENTORY,
    evaluate_readiness,
)
from app.cad_engine.provider_selection_decision import (
    DECISION_SELECT,
    DECISIONS,
    record_provider_selection,
)
from app.cad_engine.provider_selection_projection import (
    build_selection_projection,
)

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "app" / "cad_engine" / "jev_contract_discovery.py")

REPORT_BLOCKERS = frozenset({
    "SECURITY_CONTROLS",
    "TIMEOUT_CANCELLATION_SEMANTICS",
    "AVAILABILITY_SLO",
    "DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
})

EVIDENCE_FIELDS = (
    "requirement_id", "provider", "evidence_status", "finding",
    "source", "source_version", "retrieved_on", "limitations",
)

CONFIRMATION_PREFIXES = tuple(sorted(DISCOVERY_REQUIREMENT_IDS))


def _module_source():
    return MODULE_PATH.read_text(encoding="utf-8")


def _module_tree():
    return ast.parse(_module_source())


def _discovery():
    return jev_contract_discovery()


def _evidence_by_id():
    return {e.requirement_id: e for e in _discovery().evidence}


def _capture():
    return jev_fact_capture()


def _determination():
    return evaluate_capture()


def _record(decision=DECISION_SELECT):
    return record_provider_selection(
        capture=_capture(), determination=_determination(),
        decision=decision)


# --------------------------------------------------------------------------------------
# 1. Module surface: discovery data only.
# --------------------------------------------------------------------------------------
class TestModuleSurface:
    def test_imports_are_only_dataclasses_and_the_provider_name(self):
        tree = _module_tree()
        assert not [n for n in tree.body if isinstance(n, ast.Import)]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module in {
                    "dataclasses",
                    "app.cad_engine.provider_fact_capture",
                }, f"unexpected import from {node.module!r}"

    def test_function_surface_is_exactly_the_discovery_function(self):
        tree = _module_tree()
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert names == {"jev_contract_discovery"}

    def test_no_io_process_env_or_clock_calls(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        forbidden = {
            "open", "print", "sleep", "input", "eval", "exec", "getenv",
            "environ", "read", "write", "read_text", "write_text", "mkdir",
            "unlink", "time", "random", "uuid", "datetime", "socket",
        }
        assert names.isdisjoint(forbidden), f"forbidden names used: {names & forbidden}"

    def test_the_record_types_are_frozen_plain_data(self):
        for cls in (DiscoveryEvidence, ContractDiscovery):
            assert dataclasses.is_dataclass(cls)
            assert cls.__dataclass_params__.frozen
        tree = _module_tree()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                assert not [n for n in node.body
                            if isinstance(n, ast.FunctionDef)]

    def test_no_credential_named_fields(self):
        fragments = {"key", "token", "secret", "password", "credential"}
        for cls in (DiscoveryEvidence, ContractDiscovery):
            for field in dataclasses.fields(cls):
                assert not any(
                    fragment in field.name.lower() for fragment in fragments
                ), f"field {cls.__name__}.{field.name} is credential-shaped"

    def test_no_adapter_client_or_registry_surface(self):
        # The prose may discuss adapter design (the sufficiency statement is
        # required); the surface check is structural: exactly the two frozen
        # dataclasses, nothing that could act as a client, adapter or
        # registry.
        tree = _module_tree()
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        assert len(classes) == 2

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
                    "a decision literal appears in the discovery layer; "
                    "discovery must never record a decision"
                )

    def test_the_module_does_not_import_the_gate_or_the_decision_layers(self):
        source = _module_source()
        for fragment in ("provider_readiness_gate", "provider_selection_",
                         "integration_authorization"):
            assert fragment not in source, fragment


# --------------------------------------------------------------------------------------
# 2. The evidence vocabulary: exactly four classifications.
# --------------------------------------------------------------------------------------
class TestEvidenceVocabulary:
    def test_exactly_four_classifications_exist(self):
        assert DISCOVERY_STATUSES == {
            DISCOVERY_VERIFIED, DISCOVERY_UNVERIFIED,
            DISCOVERY_NOT_APPLICABLE, DISCOVERY_CONFLICTING,
        }
        assert DISCOVERY_VERIFIED == "VERIFIED"
        assert DISCOVERY_UNVERIFIED == "UNVERIFIED"
        assert DISCOVERY_NOT_APPLICABLE == "NOT_APPLICABLE"
        assert DISCOVERY_CONFLICTING == "CONFLICTING"
        assert len(DISCOVERY_STATUSES) == 4

    def test_every_evidence_status_is_in_the_vocabulary(self):
        for evidence in _discovery().evidence:
            assert evidence.evidence_status in DISCOVERY_STATUSES


# --------------------------------------------------------------------------------------
# 3. Coverage: exactly the five in-scope requirements, all UNVERIFIED.
# --------------------------------------------------------------------------------------
class TestCoverage:
    def test_scope_is_exactly_the_five_unverified_requirements(self):
        assert DISCOVERY_REQUIREMENT_IDS == REPORT_BLOCKERS | {"STREAMING_BEHAVIOUR"}
        assert _discovery().scope_requirement_ids == tuple(
            sorted(DISCOVERY_REQUIREMENT_IDS))
        assert {e.requirement_id
                for e in _discovery().evidence} == DISCOVERY_REQUIREMENT_IDS
        assert len(_discovery().evidence) == 5

    def test_every_scoped_requirement_is_a_genuine_7b9_requirement(self):
        assert DISCOVERY_REQUIREMENT_IDS <= REQUIREMENT_IDS
        assert len(REQUIREMENT_INVENTORY) == 25

    def test_all_five_remain_unverified_after_discovery(self):
        for evidence in _discovery().evidence:
            assert evidence.evidence_status == DISCOVERY_UNVERIFIED, (
                f"{evidence.requirement_id} was upgraded without the "
                "authoritative evidence to support it"
            )

    def test_no_evidence_was_manufactured_as_satisfied(self):
        # No row may claim VERIFIED: nothing authoritative establishes the
        # missing operational semantics, and discovery never upgrades on
        # plausibility.
        assert all(
            e.evidence_status != DISCOVERY_VERIFIED
            for e in _discovery().evidence)

    def test_no_not_applicable_or_conflicting_classification_appears(self):
        # None of the five requirements is genuinely inapplicable to an API
        # provider, and no two authoritative sources disagreed, so the
        # record says neither — honestly.
        assert all(
            e.evidence_status != DISCOVERY_NOT_APPLICABLE
            and e.evidence_status != DISCOVERY_CONFLICTING
            for e in _discovery().evidence)

    def test_the_optional_streaming_finding_notes_it_is_non_blocking(self):
        streaming = _evidence_by_id()["STREAMING_BEHAVIOUR"]
        assert "OPTIONAL_LATER" in streaming.limitations
        assert streaming.evidence_status == DISCOVERY_UNVERIFIED

    def test_every_row_carries_the_jev_provider_identity(self):
        for evidence in _discovery().evidence:
            assert evidence.provider == PROVIDER_NAME
        assert _discovery().provider == PROVIDER_NAME


# --------------------------------------------------------------------------------------
# 4. Evidence quality: findings, sources, retrieval dates, limitations.
# --------------------------------------------------------------------------------------
class TestEvidenceQuality:
    def test_every_finding_is_recorded(self):
        for evidence in _discovery().evidence:
            assert evidence.finding.strip(), evidence.requirement_id

    def test_every_consulted_source_is_recorded_even_when_unverified(self):
        # The record must say exactly where the absence of evidence was
        # established — which authoritative pages were consulted.
        for evidence in _discovery().evidence:
            assert evidence.source.strip(), evidence.requirement_id

    def test_every_row_records_the_retrieval_date(self):
        for evidence in _discovery().evidence:
            assert evidence.retrieved_on == "2026-09-22"

    def test_every_row_records_its_limitations(self):
        # Preservation of uncertainty: what the source does NOT establish
        # is part of the record, for every requirement.
        for evidence in _discovery().evidence:
            assert evidence.limitations.strip(), evidence.requirement_id

    def test_limitations_never_claim_what_sources_do_not_establish(self):
        # The three traps the brief names must all be disclaimed where the
        # sources could be misread as establishing them.
        timeout = _evidence_by_id()["TIMEOUT_CANCELLATION_SEMANTICS"]
        availability = _evidence_by_id()["AVAILABILITY_SLO"]
        assert "not provider-side" in timeout.limitations
        assert "not by itself an availability SLO" in availability.limitations

    def test_the_status_page_is_recorded_as_measurement_not_commitment(self):
        availability = _evidence_by_id()["AVAILABILITY_SLO"]
        assert "no SLA" in availability.finding
        assert "measured" in availability.finding.lower()
        assert "99.838%" in availability.finding

    def test_the_sdk_timeout_budget_is_recorded_as_client_side(self):
        timeout = _evidence_by_id()["TIMEOUT_CANCELLATION_SEMANTICS"]
        assert "timeout=30.0" in timeout.finding
        assert "client-side" in timeout.finding
        assert "RetryPolicy" in timeout.finding

    def test_the_jaggedness_statement_is_recorded_as_improvement_not_policy(self):
        deprecation = _evidence_by_id()["DEPRECATION_VERSION_CHANGE_BEHAVIOUR"]
        assert "fixed in later versions" in deprecation.finding.lower()
        assert "not a deprecation" in deprecation.finding

    def test_the_security_finding_records_the_404_and_the_shell(self):
        security = _evidence_by_id()["SECURITY_CONTROLS"]
        assert "404" in security.finding
        assert "shell" in security.finding
        assert "SOC 2" in security.finding


# --------------------------------------------------------------------------------------
# 5. Preservation of uncertainty: discovery never upgrades, never decides.
# --------------------------------------------------------------------------------------
class TestPreservationOfUncertainty:
    def test_no_authoritative_source_supports_an_upgrade(self):
        # If any of the five were upgraded, the source fields would have to
        # carry authoritative support — the record asserts the opposite.
        for evidence in _discovery().evidence:
            assert evidence.evidence_status == DISCOVERY_UNVERIFIED

    def test_no_conflicting_sources_were_found_and_none_were_invented(self):
        assert _discovery().conflicting_sources == ()

    def test_direct_confirmation_needed_is_recorded_per_requirement(self):
        confirmations = _discovery().needs_direct_confirmation
        assert len(confirmations) == 5
        for confirmation in confirmations:
            assert confirmation.split(":", 1)[0] in DISCOVERY_REQUIREMENT_IDS
        assert {c.split(":", 1)[0] for c in confirmations} == (
            DISCOVERY_REQUIREMENT_IDS)

    def test_the_adapter_sufficiency_statement_is_explicit(self):
        sufficiency = _discovery().adapter_design_sufficiency
        assert "NOT sufficient" in sufficiency
        assert "direct confirmation from Jev" in sufficiency

    def test_the_evidence_changes_no_gate_outcome(self):
        # Discovery is evidence capture: the genuine gate over the genuine
        # capture still reads NOT_READY with exactly the same four reasons.
        determination = evaluate_capture()
        assert determination.overall == GATE_NOT_READY
        assert len(determination.not_ready_reasons) == 4
        assert {reason.split()[3]
                for reason in determination.not_ready_reasons} == REPORT_BLOCKERS

    def test_the_five_requirements_remain_unverified_in_7c0(self):
        capture = _capture()
        by_id = {f.requirement_id: f for f in capture.facts}
        for requirement_id in DISCOVERY_REQUIREMENT_IDS:
            assert by_id[requirement_id].status == STATUS_UNVERIFIED, requirement_id


# --------------------------------------------------------------------------------------
# 6. Determinism, immutability, auditability.
# --------------------------------------------------------------------------------------
class TestDeterminism:
    def test_two_discoveries_are_identical(self):
        first, second = _discovery(), _discovery()
        assert first == second
        assert repr(first) == repr(second)
        assert dataclasses.asdict(first) == dataclasses.asdict(second)

    def test_the_record_is_frozen(self):
        discovery = _discovery()
        with pytest.raises(dataclasses.FrozenInstanceError):
            discovery.conflicting_sources = ("invented",)
        with pytest.raises(dataclasses.FrozenInstanceError):
            discovery.evidence = ()
        with pytest.raises(dataclasses.FrozenInstanceError):
            _evidence_by_id()["AVAILABILITY_SLO"].evidence_status = (
                DISCOVERY_VERIFIED)

    def test_the_record_is_hashable(self):
        assert len({hash(_discovery())}) == 1
        assert all(hash(e) is not None for e in _discovery().evidence)

    def test_evidence_rows_are_unique_per_requirement(self):
        ids = [e.requirement_id for e in _discovery().evidence]
        assert len(ids) == len(set(ids)) == 5


# --------------------------------------------------------------------------------------
# 7. No network, no environment, no credentials.
# --------------------------------------------------------------------------------------
class TestNoNetworkEnvCredentials:
    def test_module_reads_no_environment(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "getenv" not in names
        assert "environ" not in names

    def test_the_discovery_is_identical_under_an_empty_environment(self, monkeypatch):
        before = _discovery()
        monkeypatch.setattr(os, "environ", {})
        assert _discovery() == before

    def test_no_credentials_are_embedded(self):
        for fragment in ("api_key =", "token =", "password =", "secret ="):
            assert fragment not in _module_source()

    def test_the_module_names_no_network_machinery(self):
        # The import-surface test already proves no networking module is
        # imported; this guards the executable text. ("subprocess" is
        # excluded: the module's own disclaimer and the legal term
        # "subprocessors" legitimately contain it.)
        source = _module_source()
        for fragment in ("requests", "urllib", "http.client", "httpx",
                         "socket"):
            assert fragment not in source, fragment


# --------------------------------------------------------------------------------------
# 8. The prior boundaries are unchanged.
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
        record = _record(DECISION_SELECT)
        assert record.readiness_overall == GATE_NOT_READY
        assert record.selected_against_not_ready is True
        assert len(record.blocking_reasons) == 4

    def test_7c2_projection_still_behaves_identically(self):
        projection = build_selection_projection(_record(DECISION_SELECT))
        assert projection.decision == DECISION_SELECT
        assert projection.readiness_overall == GATE_NOT_READY
        assert len(projection.blocking_reasons) == 4

    def test_7c3_authorization_still_behaves_identically(self):
        auth = authorize_selection(_record(DECISION_SELECT))
        assert auth.result == AUTHORIZATION_NOT_AUTHORIZED
        assert auth.reason == "readiness is 'NOT_READY'"
        assert len(auth.blocking_reasons) == 4
        assert {reason.split()[3] for reason in auth.blocking_reasons} == REPORT_BLOCKERS

    def test_the_discovery_does_not_add_files_to_the_prior_boundaries(self):
        # The discovery module names none of the prior boundary modules in
        # its imports (proven in TestModuleSurface), so it cannot have
        # changed their behaviour — corroborated by the assertions above.
        assert _discovery().provider == PROVIDER_NAME
