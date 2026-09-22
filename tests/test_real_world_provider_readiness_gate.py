"""
Milestone 7B9 — REAL-WORLD EXTERNAL PROVIDER READINESS GATE PROOF.

Proves the provider_readiness_gate module — the deterministic, provider-neutral
gate between the proven 7B7/7B8 assistant boundary and ANY future real external
provider — over the genuine PROJ-7AV-SELBY 51-candidate workload.

Proven here:

  - the requirement inventory is pinned (19 provider facts + 6 local controls,
    classifications per the accepted discovery split) and carries no ordering
    semantics, no numerical composite and no automatic provider choice;
  - the gate fails closed: a fresh gate is NOT_READY, malformed input is
    refused loudly, SATISFIED provider facts without a source are refused,
    optional requirements never block, and nothing is auto-satisfied;
  - the wire projection is exactly role + released fields: bookkeeping never
    crosses, observations stay byte-verbatim, cross-candidate leakage is
    impossible, and the 7B7 permission table remains the single source of
    truth;
  - the gate knows no provider, can never mutate engineering state (the
    genuine resolution APIs refuse its records), and the real RP-0009 journey
    is untouched by gate activity;
  - determinism, structural safety (AST), and the full 7B5–7B8 regression.

The same genuine captures the 7Y..7B8 chain uses drive every real-world proof;
no product module, no existing test and no fixture is modified.
"""

import ast
import dataclasses
from collections import Counter
from pathlib import Path

import pytest

import app.cad_engine.provider_readiness_gate as gate_module
from app.cad_engine.assistant_message_boundary import (
    ROLE_DESCRIBE_OBSERVATION,
    ROLE_DRAFT_ACKNOWLEDGMENT,
    ROLE_EVIDENCE_NAVIGATION,
    ROLE_EXPLAIN_TASK,
    AssistantRequest,
    AssistantRequestField,
    build_assistant_request,
)
from app.cad_engine.assistant_session import (
    PROFILE_NORMAL,
    RESULT_DISPLAY_MESSAGE,
    StandInAssistant,
    build_session_requests,
    run_session,
)
from app.cad_engine.exception_resolution import (
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
    TASK_PROVIDE_PLATE,
    apply_human_resolution,
)
from app.cad_engine.project_workflow import resolve_project_connection
from app.cad_engine.provider_readiness_gate import (
    CLASSIFICATION_BEFORE_ANY_REAL_CALL,
    CLASSIFICATION_BEFORE_PRODUCTION_USE,
    CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION,
    CLASSIFICATION_OPTIONAL_LATER,
    CLASSIFICATIONS,
    GATE_NOT_READY,
    GATE_READY,
    KIND_LOCAL_CONTROL,
    KIND_PROVIDER_FACT,
    KINDS,
    LOCAL_CONTROL_IDS,
    PROVIDER_FACT_IDS,
    REQUIREMENT_CLASSIFICATIONS,
    REQUIREMENT_IDS,
    REQUIREMENT_INVENTORY,
    STATUS_SATISFIED,
    STATUS_UNSATISFIED,
    STATUS_UNVERIFIED,
    STATUSES,
    GateDetermination,
    RequirementEvaluation,
    RequirementRecord,
    WireProjection,
    build_wire_projection,
    evaluate_readiness,
)
from app.cad_engine.review_contract import build_project_review_contract
from app.cad_engine.reviewer_workload_reduction import (
    CATEGORY_ADMINISTRATIVE,
    CATEGORY_ASSISTABLE,
    CATEGORY_EVIDENCE_LOOKUP,
    CATEGORY_HUMAN_ENGINEERING_DECISION,
    build_reviewer_workload_baseline,
)
from tests.test_real_world_assistant_message_boundary import (
    RP0001_CONFIRM_VALUE,
    RP0008_HOLE_READING,
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
    / "provider_readiness_gate.py"
)
GATE_SOURCE = MODULE_FILE.read_text()

# The 7B7 permission rows, restated here ONLY as test expectations — the gate
# module has no second permission table (proven by its import surface).
ROLE_FIELD_ROWS = {
    ROLE_EXPLAIN_TASK: {
        "task_id", "task_type", "category", "proposal", "reviewer_remaining",
        "confirmation_reviewer_action", "confirmation_system_never",
        "why_human", "missing_information", "judgement_constitutes",
    },
    ROLE_DESCRIBE_OBSERVATION: {
        "task_id", "task_type", "category", "current_value", "provenance",
    },
    ROLE_DRAFT_ACKNOWLEDGMENT: {
        "task_id", "task_type", "category", "proposal", "reviewer_remaining",
    },
    ROLE_EVIDENCE_NAVIGATION: {
        "task_id", "task_type", "category", "source_anchor",
    },
    "QUEUE_SUMMARY": {"candidate_count", "task_count", "pending_task_count"},
    "DOCUMENTATION_QA": set(),
}

FORBIDDEN_IMPORTS = {
    "time", "random", "threading", "asyncio", "subprocess", "socket", "http",
    "urllib", "requests", "anthropic", "openai", "supabase", "os", "sys", "io",
    "json", "re", "pathlib", "config", "datetime",
}
ALLOWED_IMPORTS = {"dataclasses", "app.cad_engine.assistant_message_boundary"}
FORBIDDEN_FRAGMENTS = (
    "exception_resolution", "project_workflow", "review_contract",
    "reviewer_assistance", "reviewer_workload_reduction", "assistant_session",
    "fabrication", "drawing", "production_acceptance", "fabricator_acceptance",
    "automation", "supabase",
)
FORBIDDEN_ACTION_NAMES = {
    "resolve", "apply", "submit", "approve", "confirm", "set_value",
    "set_provenance", "generate", "write", "save", "choose", "select",
    "recommend", "integrate", "score", "weight", "rank", "prioritise",
    "triage",
}


def _all_ready_records():
    """The complete set of 25 records with every requirement SATISFIED, each
    provider fact carrying a source. Built by the TEST, never by the gate."""
    records = []
    for requirement_id, kind, classification in REQUIREMENT_INVENTORY:
        if kind == KIND_PROVIDER_FACT:
            records.append(RequirementRecord(
                requirement_id, kind, classification, STATUS_SATISFIED,
                f"documented source for {requirement_id}", ""))
        else:
            records.append(RequirementRecord(
                requirement_id, kind, classification, STATUS_SATISFIED, "",
                "proven by SteelSpec's local test suite"))
    return tuple(records)


def _with_status(records, requirement_id, status, source=None):
    """A copy of `records` with one requirement's status (and source) changed."""
    changed = []
    for record in records:
        if record.requirement_id == requirement_id:
            changed.append(RequirementRecord(
                record.requirement_id, record.kind, record.classification,
                status, record.source if source is None else source, record.note))
        else:
            changed.append(record)
    return tuple(changed)


def _without(records, requirement_id):
    return tuple(record for record in records
                 if record.requirement_id != requirement_id)


def _descr(assistance, package_id, task_type):
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
def genuine_requests(real_assistance):
    return build_session_requests(real_assistance)


# =============================================================================
# 1. Module surface: pure, provider-neutral, structurally incapable of scoring
#    or mutation.
# =============================================================================

class TestModuleSurface:
    def test_imports_within_whitelist(self):
        modules = set()
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules <= ALLOWED_IMPORTS

    def test_no_io_or_network_imports(self):
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN_IMPORTS

    def test_cannot_name_mutation_or_other_machinery(self):
        modules = set()
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            assert all(fragment not in module
                       for fragment in FORBIDDEN_FRAGMENTS)

    def test_function_surface_is_exactly_the_gate_api(self):
        defs = {node.name for node in ast.walk(ast.parse(GATE_SOURCE))
                if isinstance(node, ast.FunctionDef)}
        assert defs == {"build_wire_projection", "evaluate_readiness",
                        "_validate_records"}

    def test_no_forbidden_action_names(self):
        defs = [node.name for node in ast.walk(ast.parse(GATE_SOURCE))
                if isinstance(node, ast.FunctionDef)]
        for name in defs:
            for forbidden in FORBIDDEN_ACTION_NAMES:
                assert forbidden not in name

    def test_dataclass_fields_carry_no_scoring_names(self):
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.AnnAssign) and \
                            isinstance(child.target, ast.Name):
                        for forbidden in ("score", "weight", "rank",
                                          "priority", "choose", "select"):
                            assert forbidden not in child.target.id

    def test_dataclasses_have_no_methods(self):
        for node in ast.parse(GATE_SOURCE).body:
            if isinstance(node, ast.ClassDef):
                assert all(not isinstance(child, ast.FunctionDef)
                           for child in node.body)

    def test_evaluate_readiness_uses_no_numbers(self):
        # No numeric threshold, percentage or weight can exist inside the gate
        # logic. Booleans (blocks/required flags) are the only int-like
        # constants and are honest gate logic, not scoring.
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.FunctionDef) and \
                    node.name in {"evaluate_readiness", "_validate_records"}:
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant):
                        assert not isinstance(child.value, (int, float)) or \
                            isinstance(child.value, bool)

    def test_no_io_calls(self):
        forbidden_calls = {"open", "print", "sleep", "input", "eval", "exec"}
        forbidden_attrs = {"read", "write", "read_text", "write_text", "save",
                           "getenv", "mkdir", "unlink", "iterdir"}
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in forbidden_calls
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in forbidden_attrs

    def test_no_provider_names_or_credentials_in_source(self):
        lowered = GATE_SOURCE.lower()
        for token in ("anthropic", "openai", "jey", "jev", "https://",
                      "http://", "api_key", "apikey", "token"):
            assert token not in lowered

    def test_inventory_has_no_ordering_semantics(self):
        # The semantic inventory is frozensets; the authored tuple is
        # presentation order only (deterministic iteration).
        assert isinstance(REQUIREMENT_IDS, frozenset)
        assert isinstance(PROVIDER_FACT_IDS, frozenset)
        assert isinstance(LOCAL_CONTROL_IDS, frozenset)
        assert isinstance(REQUIREMENT_CLASSIFICATIONS, frozenset)
        assert isinstance(CLASSIFICATIONS, frozenset)
        assert isinstance(STATUSES, frozenset)
        assert isinstance(KINDS, frozenset)


# =============================================================================
# 2. The pinned requirement inventory.
# =============================================================================

class TestRequirementInventory:
    def test_exact_counts(self):
        assert len(REQUIREMENT_INVENTORY) == 25
        assert len(PROVIDER_FACT_IDS) == 19
        assert len(LOCAL_CONTROL_IDS) == 6
        assert len(REQUIREMENT_IDS) == 25
        assert len(REQUIREMENT_CLASSIFICATIONS) == 25

    def test_facts_and_controls_are_disjoint_and_complete(self):
        assert PROVIDER_FACT_IDS.isdisjoint(LOCAL_CONTROL_IDS)
        assert PROVIDER_FACT_IDS | LOCAL_CONTROL_IDS == REQUIREMENT_IDS
        assert {entry[0] for entry in REQUIREMENT_INVENTORY} == REQUIREMENT_IDS

    def test_before_any_real_call_bucket(self):
        pinned = {
            "DATA_RETENTION", "DATA_TRAINING_USE_POLICY", "SUBPROCESSORS",
            "PRIVACY_CONTRACTUAL_TERMS", "SECURITY_CONTROLS",
        }
        actual = {entry[0] for entry in REQUIREMENT_INVENTORY
                  if entry[2] == CLASSIFICATION_BEFORE_ANY_REAL_CALL}
        assert actual == pinned
        assert all(entry[1] == KIND_PROVIDER_FACT
                   for entry in REQUIREMENT_INVENTORY
                   if entry[2] == CLASSIFICATION_BEFORE_ANY_REAL_CALL)

    def test_before_technical_integration_bucket(self):
        pinned_facts = {
            "API_INPUT_OUTPUT_CONTRACT", "AUTHENTICATION_CREDENTIAL_HANDLING",
            "ERROR_SEMANTICS", "TIMEOUT_CANCELLATION_SEMANTICS", "RATE_LIMITS",
            "REQUEST_RESPONSE_SIZE_LIMITS", "MODEL_VERSION_RECORDING",
        }
        pinned_controls = {
            "WIRE_PROJECTION", "NO_TOOLS", "NO_RETRY_RESEND",
            "STATELESS_TURNS", "RESPONSE_CONTAINMENT", "LOCAL_COST_CAP",
        }
        entries = {entry[0]: entry for entry in REQUIREMENT_INVENTORY
                   if entry[2] == CLASSIFICATION_BEFORE_TECHNICAL_INTEGRATION}
        assert set(entries) == pinned_facts | pinned_controls
        for requirement_id in pinned_facts:
            assert entries[requirement_id][1] == KIND_PROVIDER_FACT
        for requirement_id in pinned_controls:
            assert entries[requirement_id][1] == KIND_LOCAL_CONTROL

    def test_before_production_use_bucket(self):
        pinned = {
            "AVAILABILITY_SLO", "COST_MODEL", "MODEL_VERSION_PINNING",
            "DEPRECATION_VERSION_CHANGE_BEHAVIOUR", "DATA_RESIDENCY",
            "INCIDENT_BREACH_NOTIFICATION_TERMS",
        }
        actual = {entry[0] for entry in REQUIREMENT_INVENTORY
                  if entry[2] == CLASSIFICATION_BEFORE_PRODUCTION_USE}
        assert actual == pinned

    def test_optional_later_bucket(self):
        assert {entry[0] for entry in REQUIREMENT_INVENTORY
                if entry[2] == CLASSIFICATION_OPTIONAL_LATER} == \
            {"STREAMING_BEHAVIOUR"}

    def test_inventory_is_duplicate_free(self):
        ids = [entry[0] for entry in REQUIREMENT_INVENTORY]
        assert len(ids) == len(set(ids))


# =============================================================================
# 3. Record structures: frozen plain data, no methods, no mappings.
# =============================================================================

class TestRequirementRecords:
    def test_records_are_frozen(self):
        record = RequirementRecord(
            "DATA_RETENTION", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED)
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.status = STATUS_SATISFIED
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.source = "mutated"

    def test_determination_is_frozen(self):
        determination = evaluate_readiness(())
        with pytest.raises(dataclasses.FrozenInstanceError):
            determination.overall = GATE_READY
        with pytest.raises(dataclasses.FrozenInstanceError):
            determination.not_ready_reasons = ()

    def test_records_are_not_mappings_or_sequences(self):
        from collections.abc import Mapping
        record = RequirementRecord(
            "DATA_RETENTION", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED)
        determination = evaluate_readiness(())
        assert not isinstance(record, Mapping)
        assert not isinstance(record, (list, tuple))
        assert not isinstance(determination, Mapping)
        assert not isinstance(determination, (list, tuple))

    def test_determination_carries_only_gate_data(self):
        determination = evaluate_readiness(())
        assert set(vars(determination)) == {
            "overall", "evaluations", "not_ready_reasons"}
        evaluation = determination.evaluations[0]
        assert set(vars(evaluation)) == {
            "requirement_id", "kind", "classification", "status", "required",
            "blocks", "reason"}

    def test_hashes_work(self):
        record = RequirementRecord(
            "DATA_RETENTION", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED)
        hash(record)
        determination = evaluate_readiness(())
        hash(determination)
        for evaluation in determination.evaluations:
            hash(evaluation)


# =============================================================================
# 4. Fail-closed semantics: nothing silently becomes READY.
# =============================================================================

class TestFailClosed:
    def test_fresh_gate_is_not_ready(self):
        determination = evaluate_readiness(())
        assert determination.overall == GATE_NOT_READY
        assert len(determination.not_ready_reasons) == 24
        assert len(determination.evaluations) == 25
        assert all(evaluation.required for evaluation in determination.evaluations
                   if evaluation.requirement_id != "STREAMING_BEHAVIOUR")
        assert all(evaluation.status == STATUS_UNVERIFIED
                   for evaluation in determination.evaluations)
        assert not determination.evaluations[
            next(i for i, e in enumerate(determination.evaluations)
                 if e.requirement_id == "STREAMING_BEHAVIOUR")].blocks

    def test_all_satisfied_is_ready(self):
        determination = evaluate_readiness(_all_ready_records())
        assert determination.overall == GATE_READY
        assert determination.not_ready_reasons == ()
        assert all(evaluation.status == STATUS_SATISFIED
                   for evaluation in determination.evaluations)
        assert all(not evaluation.blocks
                   for evaluation in determination.evaluations)

    def test_one_required_fact_missing_blocks(self):
        records = _without(_all_ready_records(), "DATA_RETENTION")
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_NOT_READY
        assert len(determination.not_ready_reasons) == 1
        assert "DATA_RETENTION" in determination.not_ready_reasons[0]
        evaluation = next(e for e in determination.evaluations
                          if e.requirement_id == "DATA_RETENTION")
        assert evaluation.status == STATUS_UNVERIFIED
        assert evaluation.blocks is True

    def test_required_fact_unverified_blocks(self):
        records = _with_status(_all_ready_records(), "DATA_RETENTION",
                               STATUS_UNVERIFIED, source="")
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_NOT_READY
        assert "UNVERIFIED" in determination.not_ready_reasons[0]

    def test_required_fact_unsatisfied_blocks(self):
        records = _with_status(
            _all_ready_records(), "DATA_RETENTION", STATUS_UNSATISFIED,
            source="documented source for DATA_RETENTION")
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_NOT_READY
        assert "UNSATISFIED" in determination.not_ready_reasons[0]

    def test_optional_fact_missing_never_blocks(self):
        records = _without(_all_ready_records(), "STREAMING_BEHAVIOUR")
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_READY
        evaluation = next(e for e in determination.evaluations
                          if e.requirement_id == "STREAMING_BEHAVIOUR")
        assert evaluation.status == STATUS_UNVERIFIED
        assert evaluation.blocks is False
        assert evaluation.reason == ""

    def test_optional_fact_unsatisfied_never_blocks(self):
        records = _with_status(
            _all_ready_records(), "STREAMING_BEHAVIOUR", STATUS_UNSATISFIED,
            source="documented source for STREAMING_BEHAVIOUR")
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_READY
        evaluation = next(e for e in determination.evaluations
                          if e.requirement_id == "STREAMING_BEHAVIOUR")
        assert evaluation.status == STATUS_UNSATISFIED
        assert evaluation.blocks is False

    def test_satisfied_provider_fact_without_source_refused(self):
        records = _with_status(_all_ready_records(), "DATA_RETENTION",
                               STATUS_SATISFIED, source="")
        with pytest.raises(ValueError, match="without a source"):
            evaluate_readiness(records)
        records_blank = _with_status(_all_ready_records(), "DATA_RETENTION",
                                     STATUS_SATISFIED, source="   ")
        with pytest.raises(ValueError, match="without a source"):
            evaluate_readiness(records_blank)

    def test_satisfied_local_control_needs_no_source(self):
        # Local controls are proven by SteelSpec's own tests; their records do
        # not carry a provider documentation source.
        records = _with_status(_all_ready_records(), "WIRE_PROJECTION",
                               STATUS_SATISFIED, source="")
        assert evaluate_readiness(records).overall == GATE_READY

    def test_duplicate_requirement_refused(self):
        record = RequirementRecord(
            "DATA_RETENTION", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED)
        with pytest.raises(ValueError, match="duplicate record"):
            evaluate_readiness((record, record))

    def test_contradictory_duplicates_refused(self):
        first = RequirementRecord(
            "DATA_RETENTION", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_SATISFIED,
            "source one")
        second = RequirementRecord(
            "DATA_RETENTION", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNSATISFIED,
            "source two")
        with pytest.raises(ValueError, match="duplicate record"):
            evaluate_readiness((first, second))

    def test_unknown_requirement_refused(self):
        record = RequirementRecord(
            "NOT_A_REQUIREMENT", KIND_PROVIDER_FACT,
            CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED)
        with pytest.raises(ValueError, match="unknown requirement"):
            evaluate_readiness((record,))

    def test_invalid_status_refused(self):
        with pytest.raises(ValueError, match="invalid status"):
            evaluate_readiness((RequirementRecord(
                "DATA_RETENTION", KIND_PROVIDER_FACT,
                CLASSIFICATION_BEFORE_ANY_REAL_CALL, "MAYBE"),))

    def test_invalid_classification_refused(self):
        with pytest.raises(ValueError, match="invalid classification"):
            evaluate_readiness((RequirementRecord(
                "DATA_RETENTION", KIND_PROVIDER_FACT, "SOON", STATUS_UNVERIFIED),))

    def test_mismatched_classification_refused(self):
        # The inventory classification is fixed: the caller cannot smuggle a
        # required fact into the optional bucket or vice versa.
        with pytest.raises(ValueError, match="does not match"):
            evaluate_readiness((RequirementRecord(
                "DATA_RETENTION", KIND_PROVIDER_FACT,
                CLASSIFICATION_OPTIONAL_LATER, STATUS_UNVERIFIED),))

    def test_invalid_kind_refused(self):
        with pytest.raises(ValueError, match="invalid kind"):
            evaluate_readiness((RequirementRecord(
                "DATA_RETENTION", "GOSSIP",
                CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED),))

    def test_malformed_record_refused(self):
        with pytest.raises(TypeError, match="tuple"):
            evaluate_readiness([RequirementRecord(
                "DATA_RETENTION", KIND_PROVIDER_FACT,
                CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED)])
        with pytest.raises(TypeError, match="RequirementRecord"):
            evaluate_readiness(("not a record",))
        with pytest.raises(TypeError, match="source must be a str"):
            evaluate_readiness((RequirementRecord(
                "DATA_RETENTION", KIND_PROVIDER_FACT,
                CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED,
                source=123),))
        with pytest.raises(TypeError, match="note must be a str"):
            evaluate_readiness((RequirementRecord(
                "DATA_RETENTION", KIND_PROVIDER_FACT,
                CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED,
                note=456),))

    @pytest.mark.parametrize("control_id", sorted(LOCAL_CONTROL_IDS))
    def test_every_failed_local_control_blocks(self, control_id):
        records = _with_status(_all_ready_records(), control_id,
                               STATUS_UNSATISFIED)
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_NOT_READY
        assert len(determination.not_ready_reasons) == 1
        assert control_id in determination.not_ready_reasons[0]
        evaluation = next(e for e in determination.evaluations
                          if e.requirement_id == control_id)
        assert evaluation.kind == KIND_LOCAL_CONTROL
        assert evaluation.blocks is True

    @pytest.mark.parametrize("control_id", sorted(LOCAL_CONTROL_IDS))
    def test_every_missing_local_control_blocks(self, control_id):
        records = _without(_all_ready_records(), control_id)
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_NOT_READY
        assert any(control_id in reason
                   for reason in determination.not_ready_reasons)

    def test_no_silent_repair(self):
        # Every malformed input raises; none of these returns a determination.
        broken_inputs = [
            ("DATA_RETENTION", KIND_PROVIDER_FACT,
             CLASSIFICATION_BEFORE_ANY_REAL_CALL, "MAYBE", "", ""),
            ("DATA_RETENTION", KIND_PROVIDER_FACT,
             CLASSIFICATION_OPTIONAL_LATER, STATUS_UNVERIFIED, "", ""),
            ("UNKNOWN_ID", KIND_PROVIDER_FACT,
             CLASSIFICATION_BEFORE_ANY_REAL_CALL, STATUS_UNVERIFIED, "", ""),
        ]
        for args in broken_inputs:
            with pytest.raises(ValueError):
                evaluate_readiness((RequirementRecord(*args),))


# =============================================================================
# 5. The wire projection: role + released fields, nothing else.
# =============================================================================

class TestWireProjection:
    def test_projection_contains_exactly_role_and_fields(self, real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(request)
        assert set(vars(projection)) == {"role", "fields"}
        assert projection.role == request.role
        assert projection.fields is request.fields

    def test_projection_is_frozen_plain_data(self, real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(request)
        with pytest.raises(dataclasses.FrozenInstanceError):
            projection.role = "MUTATED"
        with pytest.raises(dataclasses.FrozenInstanceError):
            projection.fields = ()
        from collections.abc import Mapping
        assert not isinstance(projection, Mapping)
        assert not isinstance(projection, (list, tuple))
        hash(projection)
        hash(projection.fields[0])

    def test_projection_refuses_foreign_objects(self):
        with pytest.raises(TypeError, match="AssistantRequest"):
            build_wire_projection("not a request")
        with pytest.raises(TypeError, match="AssistantRequest"):
            build_wire_projection(object())

    def test_projection_carries_no_hidden_request_reference(self,
                                                           real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(request)
        for name in ("request", "package_id", "task_id", "revision",
                     "reviewer_question"):
            assert name not in vars(projection)
        assert not any(name in vars(projection.fields[0]) and
                       getattr(projection.fields[0], name) is request
                       for name in ("request", "owner"))

    def test_released_values_are_byte_verbatim(self, real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(request)
        current_values = [field.value for field in projection.fields
                          if field.name == "current_value"]
        assert current_values == [RP0001_CONFIRM_VALUE]
        assert "300" in current_values[0]

    def test_all_genuine_requests_project(self, genuine_requests,
                                          real_assistance):
        for request in genuine_requests:
            projection = build_wire_projection(request)
            assert projection.role == request.role
            assert projection.fields is request.fields
            assert all(isinstance(field, AssistantRequestField)
                       for field in projection.fields)
        assert len(genuine_requests) == 863


# =============================================================================
# 6. Bookkeeping exclusion: package_id / revision / reviewer_question never
#    cross the wire.
# =============================================================================

class TestBookkeepingExclusion:
    def test_changing_bookkeeping_never_changes_projection(self,
                                                           real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        variants = [
            AssistantRequest(role=request.role, package_id="RP-0001",
                             task_id=None, revision=0, reviewer_question="",
                             fields=request.fields),
            AssistantRequest(role=request.role, package_id="RP-9999",
                             task_id=None, revision=7, reviewer_question="q1",
                             fields=request.fields),
            AssistantRequest(role=request.role, package_id=None, task_id=None,
                             revision=99, reviewer_question="some question",
                             fields=request.fields),
        ]
        projections = [build_wire_projection(variant) for variant in variants]
        assert all(projection == projections[0]
                   for projection in projections[1:])
        assert all(repr(projection) == repr(projections[0])
                   for projection in projections[1:])
        assert projections[0].fields is request.fields

    def test_reviewer_question_never_crosses_even_when_supplied(
            self, real_assistance):
        task = _task(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        plain = build_assistant_request(
            real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0001", task_id=task.task_id)
        questioned = build_assistant_request(
            real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0001", task_id=task.task_id,
            reviewer_question="why does this task exist at all")
        assert build_wire_projection(plain) == \
            build_wire_projection(questioned)
        for projection in (build_wire_projection(plain),
                           build_wire_projection(questioned)):
            assert not any(field.name == "reviewer_question"
                           for field in projection.fields)
            assert not any("reviewer_question" in field.value
                           for field in projection.fields)

    def test_revision_never_crosses(self, real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(request)
        assert not any(field.name == "revision" for field in projection.fields)
        assert not any(field.name == "package_id" for field in projection.fields)


# =============================================================================
# 7. Cross-candidate isolation on the genuine workload.
# =============================================================================

class TestCrossCandidateIsolation:
    def test_projection_contains_only_its_own_task_fields(self,
                                                          genuine_requests):
        for request in genuine_requests:
            projection = build_wire_projection(request)
            assert projection.fields is request.fields

    def test_no_other_candidates_observation_or_identity_leaks(
            self, genuine_requests):
        for request in genuine_requests:
            projection = build_wire_projection(request)
            own = request.package_id
            for field in projection.fields:
                for other in EXPECTED_IDS:
                    if other != own:
                        assert other not in field.value

    def test_observations_of_different_candidates_do_not_mix(
            self, real_assistance):
        first = build_wire_projection(
            _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES))
        second = build_wire_projection(
            _descr(real_assistance, "RP-0008", TASK_PROVIDE_HOLE_DIAMETER))
        assert first.fields != second.fields
        first_values = {field.value for field in first.fields}
        assert "((4, '22mm holes', None, ()),)" not in first_values
        second_values = {field.value for field in second.fields}
        assert RP0001_CONFIRM_VALUE not in second_values

    def test_no_sweep_reordering(self, genuine_requests):
        again = build_session_requests(_assistance(_fresh()))
        assert [request.package_id for request in genuine_requests] == \
            [request.package_id for request in again]
        assert [request.role for request in genuine_requests] == \
            [request.role for request in again]
        assert genuine_requests[0].package_id == EXPECTED_IDS[0]
        assert genuine_requests[-1].package_id == EXPECTED_IDS[-1]


# =============================================================================
# 8. No scoring, weighting or composite of any kind.
# =============================================================================

class TestNoScoring:
    def test_overall_result_is_only_ready_or_not_ready(self):
        assert evaluate_readiness(()).overall == GATE_NOT_READY
        assert evaluate_readiness(_all_ready_records()).overall == GATE_READY
        assert GATE_READY == "READY"
        assert GATE_NOT_READY == "NOT_READY"
        assert GATE_READY != GATE_NOT_READY

    def test_determination_has_no_numeric_field(self):
        for determination in (evaluate_readiness(()),
                              evaluate_readiness(_all_ready_records())):
            for name, value in vars(determination).items():
                assert isinstance(value, (str, tuple))

    def test_evaluations_have_no_numeric_field(self):
        determination = evaluate_readiness(_all_ready_records())
        for evaluation in determination.evaluations:
            for name, value in vars(evaluation).items():
                assert isinstance(value, (str, bool, tuple))

    def test_module_cannot_express_a_numerical_readiness(self):
        # The gate's evaluation code carries no numeric constant at all (no
        # threshold, percentage or weighted sum can exist inside it); the only
        # int-like constants are the honest booleans of per-requirement logic.
        for node in ast.walk(ast.parse(GATE_SOURCE)):
            if isinstance(node, ast.FunctionDef) and \
                    node.name in {"evaluate_readiness", "_validate_records"}:
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant):
                        assert not isinstance(child.value, (int, float)) or \
                            isinstance(child.value, bool)

    def test_no_provider_choice_function_exists(self):
        defs = {node.name for node in ast.walk(ast.parse(GATE_SOURCE))
                if isinstance(node, ast.FunctionDef)}
        for forbidden in ("choose_provider", "approve_provider",
                          "select_provider", "integrate_provider",
                          "recommend_provider", "rank_provider"):
            assert forbidden not in defs

    def test_ready_is_not_approval(self):
        # READY is a recorded determination only. The module text says so and
        # no function or record here can act on it as approval.
        determination = evaluate_readiness(_all_ready_records())
        assert determination.overall == GATE_READY
        assert set(vars(determination)) == {
            "overall", "evaluations", "not_ready_reasons"}
        # The record's own words: the decision to integrate is a human
        # decision this record cannot make.
        assert "cannot make" in GateDetermination.__doc__


# =============================================================================
# 9. The gate knows no provider.
# =============================================================================

class TestNoProviderKnowledge:
    def test_no_provider_identity_anywhere(self):
        assert "provider_name" not in GATE_SOURCE
        assert "provider_url" not in GATE_SOURCE

    def test_sources_are_opaque_metadata(self):
        # A realistic-looking source is carried verbatim as plain metadata —
        # never fetched, never verified beyond being a non-empty string.
        records = _with_status(
            _all_ready_records(), "DATA_RETENTION", STATUS_SATISFIED,
            source="provider documentation, retention section, "
                   "version 2026-09")
        determination = evaluate_readiness(records)
        assert determination.overall == GATE_READY
        evaluation = next(e for e in determination.evaluations
                          if e.requirement_id == "DATA_RETENTION")
        assert evaluation.status == STATUS_SATISFIED
        # The source stays byte-verbatim on the caller's frozen record; the
        # gate never rewrites or re-derives it.
        assert records[0].source == \
            "provider documentation, retention section, version 2026-09"

    def test_gate_result_is_provider_independent(self):
        # The same records produce the same determination regardless of any
        # provider-identifying words in the notes: notes change nothing.
        base = evaluate_readiness(_all_ready_records())
        decorated = []
        for record in _all_ready_records():
            decorated.append(RequirementRecord(
                record.requirement_id, record.kind, record.classification,
                record.status, record.source,
                f"candidate note about {record.requirement_id}"))
        assert evaluate_readiness(tuple(decorated)) == base


# =============================================================================
# 10. Determinism.
# =============================================================================

class TestDeterminism:
    def test_record_input_order_never_changes_the_determination(self):
        records = _all_ready_records()
        reversed_records = tuple(reversed(records))
        forward = evaluate_readiness(records)
        backward = evaluate_readiness(reversed_records)
        assert forward == backward
        assert repr(forward) == repr(backward)
        assert dataclasses.asdict(forward) == dataclasses.asdict(backward)

    def test_two_independent_ready_gates_are_identical(self):
        first = evaluate_readiness(_all_ready_records())
        second = evaluate_readiness(_all_ready_records())
        assert first == second
        assert repr(first) == repr(second)

    def test_two_fresh_gates_are_identical(self):
        first = evaluate_readiness(())
        second = evaluate_readiness(())
        assert first == second
        assert repr(first) == repr(second)
        assert first.not_ready_reasons == second.not_ready_reasons

    def test_equivalent_projections_are_byte_identical(self, real_assistance):
        task = _task(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        request = build_assistant_request(
            real_assistance, role=ROLE_DESCRIBE_OBSERVATION,
            package_id="RP-0001", task_id=task.task_id)
        projection = build_wire_projection(request)
        assert dataclasses.asdict(projection) == {
            "role": projection.role,
            "fields": tuple(dataclasses.asdict(field)
                            for field in projection.fields),
        }
        clone = WireProjection(role=request.role, fields=request.fields)
        assert clone == projection
        assert repr(clone) == repr(projection)

    def test_determination_requires_no_environment(self, tmp_path):
        # Building determinations and projections writes no files and touches
        # no environment; determinism comes from the frozen inputs alone.
        evaluate_readiness(_all_ready_records())
        evaluate_readiness(())
        assert list(tmp_path.iterdir()) == []


# =============================================================================
# 11. Real Selby proof: 51 candidates, 508 tasks, 863 projections.
# =============================================================================

class TestRealSelbyProof:
    def test_51_candidates_508_tasks_in_submission_order(self, real_assistance):
        assert real_assistance.candidate_count == 51
        assert real_assistance.task_count == 508
        assert [candidate.package_id for candidate in real_assistance.candidates] \
            == list(EXPECTED_IDS)

    def test_all_863_genuine_requests_project(self, genuine_requests):
        projections = [build_wire_projection(request)
                       for request in genuine_requests]
        assert len(projections) == 863
        counts = Counter(projection.role for projection in projections)
        assert counts == {
            ROLE_EXPLAIN_TASK: 508,
            ROLE_DESCRIBE_OBSERVATION: 72,
            ROLE_DRAFT_ACKNOWLEDGMENT: 204,
            ROLE_EVIDENCE_NAVIGATION: 79,
        }

    def test_no_projection_contains_file_or_credential_content(
            self, genuine_requests):
        for request in genuine_requests:
            for field in build_wire_projection(request).fields:
                lowered = field.value.lower()
                assert ".pdf" not in lowered
                assert ".dxf" not in lowered
                assert "api_key" not in lowered
                assert "supabase" not in lowered
                assert "sk-" not in lowered

    def test_every_projection_sits_inside_the_7b7_permission_row(
            self, genuine_requests):
        for request in genuine_requests:
            projection = build_wire_projection(request)
            assert {field.name for field in projection.fields} <= \
                ROLE_FIELD_ROWS[request.role]

    def test_22mm_holes_stays_byte_exact(self, real_assistance):
        request = _descr(real_assistance, "RP-0008", TASK_PROVIDE_HOLE_DIAMETER)
        projection = build_wire_projection(request)
        current_values = [field.value for field in projection.fields
                          if field.name == "current_value"]
        assert current_values == [RP0008_HOLE_READING]
        assert current_values[0] == "((4, '22mm holes', None, ()),)"

    def test_300_stays_byte_exact(self, real_assistance):
        request = _descr(real_assistance, "RP-0001", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(request)
        current_values = [field.value for field in projection.fields
                          if field.name == "current_value"]
        assert current_values == [RP0001_CONFIRM_VALUE]
        assert "300" in current_values[0]

    def test_plate_observation_stays_byte_exact(self, real_assistance):
        request = _descr(real_assistance, "RP-0045", TASK_PROVIDE_PLATE)
        projection = build_wire_projection(request)
        current_values = [field.value for field in projection.fields
                          if field.name == "current_value"]
        assert current_values == [RP0045_PLATE_READING]

    def test_missing_material_stays_missing(self, material_assistance):
        task = _task(material_assistance, "RP-0033",
                     TASK_PROVIDE_MATERIAL_SPECIFICATION)
        with pytest.raises(ValueError, match="existing trusted observation"):
            build_assistant_request(
                material_assistance, role=ROLE_DESCRIBE_OBSERVATION,
                package_id="RP-0033", task_id=task.task_id)
        navigation = build_assistant_request(
            material_assistance, role=ROLE_EVIDENCE_NAVIGATION,
            package_id="RP-0033", task_id=task.task_id)
        projection = build_wire_projection(navigation)
        anchors = [field.value for field in projection.fields
                   if field.name == "source_anchor"]
        assert anchors == [RP0033_MATERIAL_ANCHOR]
        assert all(field.name != "current_value" for field in projection.fields)
        assert _task(_assistance(_material_workflow()), "RP-0033",
                     TASK_PROVIDE_MATERIAL_SPECIFICATION).current_value is None

    def test_gate_activity_never_changes_the_contract(self, tmp_path):
        workflow = _fresh()
        before = _snapshot(workflow, tmp_path)
        for records in ((), _all_ready_records()):
            evaluate_readiness(records)
        requests = build_session_requests(_assistance(workflow))
        for request in requests:
            build_wire_projection(request)
        assert _snapshot(workflow, tmp_path) == before
        assert build_project_review_contract(workflow).revision == 0


# =============================================================================
# 12. RP-0009 regression through the gate.
# =============================================================================

class TestRP0009Regression:
    def test_rev0_projection_is_stable_and_gate_activity_changes_nothing(
            self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        before = _snapshot(workflow, tmp_path)
        rev0_request = _descr(assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        projection = build_wire_projection(rev0_request)
        again = build_wire_projection(_descr(assistance, "RP-0009",
                                             TASK_CONFIRM_AI_VALUES))
        assert projection == again
        assert projection.role == ROLE_DESCRIBE_OBSERVATION
        # Gate activity of every kind leaves the workflow untouched.
        evaluate_readiness(())
        evaluate_readiness(_all_ready_records())
        assert _snapshot(workflow, tmp_path) == before
        assert build_project_review_contract(workflow).revision == 0
        assert list(tmp_path.iterdir()) == []

    def test_revision1_projection_differs_only_in_released_content(
            self, tmp_path):
        workflow = _fresh()
        assistance = _assistance(workflow)
        rev0_request = _descr(assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        rev0_projection = build_wire_projection(rev0_request)
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        assert build_project_review_contract(advanced).revision == 1
        rev1_request = _descr(_assistance(advanced), "RP-0009",
                              TASK_CONFIRM_AI_VALUES)
        rev1_projection = build_wire_projection(rev1_request)
        # The released fields legitimately differ across the revision.
        assert rev1_projection.fields != rev0_projection.fields
        # But the same released fields project identically regardless of the
        # bookkeeping revision they are bound to.
        same_fields_different_revision = AssistantRequest(
            role=rev0_request.role, package_id="RP-0009", task_id=None,
            revision=1, reviewer_question="",
            fields=rev0_request.fields)
        assert build_wire_projection(same_fields_different_revision) == \
            rev0_projection
        # And the genuine rev-0 fields are frozen: still byte-identical.
        assert build_wire_projection(rev0_request) == rev0_projection

    def test_gate_records_cannot_resolve_anything(self, tmp_path):
        workflow = _fresh()
        before = _snapshot(workflow, tmp_path)
        determination = evaluate_readiness(_all_ready_records())
        record = _all_ready_records()[0]
        projection = build_wire_projection(
            _descr(_assistance(workflow), "RP-0009", TASK_CONFIRM_AI_VALUES))
        for foreign in (determination, record, projection,
                        determination.evaluations[0]):
            with pytest.raises(TypeError, match="HumanResolution"):
                apply_human_resolution(workflow.exception_package, foreign)
            with pytest.raises(TypeError, match="HumanResolution"):
                resolve_project_connection(
                    workflow, package_id="RP-0009", resolutions=[foreign],
                    output_dir=tmp_path)
        assert _snapshot(workflow, tmp_path) == before
        assert build_project_review_contract(workflow).revision == 0
        assert list(tmp_path.iterdir()) == []

    def test_genuine_resolution_and_artifact_unchanged(self, tmp_path):
        workflow = _fresh()
        evaluate_readiness(())
        evaluate_readiness(_all_ready_records())
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


# =============================================================================
# 13. Regression: 7B5 / 7B6 / 7B7 / 7B8 behaviour is untouched.
# =============================================================================

class TestRegression:
    def test_7b5_workload_baseline_unchanged(self, real_assistance):
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

    def test_7b7_boundary_sweep_unchanged(self, genuine_requests):
        counts = Counter(request.role for request in genuine_requests)
        assert counts == {
            ROLE_EXPLAIN_TASK: 508,
            ROLE_DESCRIBE_OBSERVATION: 72,
            ROLE_DRAFT_ACKNOWLEDGMENT: 204,
            ROLE_EVIDENCE_NAVIGATION: 79,
        }

    def test_7b8_session_unchanged(self, real_assistance):
        session = run_session(real_assistance, StandInAssistant(PROFILE_NORMAL))
        assert session.turn_count == 863
        assert all(record.result_kind == RESULT_DISPLAY_MESSAGE
                   for record in session.records)

    def test_missing_material_candidates_still_missing(self, material_assistance):
        for package_id in MISSING_MATERIAL_IDS:
            assert _task(material_assistance, package_id,
                         TASK_PROVIDE_MATERIAL_SPECIFICATION).current_value \
                is None

    def test_rp0009_hole_reading_pinned(self, real_assistance):
        request = _descr(real_assistance, "RP-0009", TASK_CONFIRM_AI_VALUES)
        assert "(('connected_member_marks'" in \
            [field.value for field in request.fields
             if field.name == "current_value"][0]
