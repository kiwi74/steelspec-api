"""
Milestone 7C2 — PROVIDER SELECTION RECORD CONSUMPTION (tests).

Proves the read-only projection layer over frozen 7C1 records: verbatim
preservation of every recorded field, deterministic canonical ordering,
duplicate refusal without collapse, empty-collection behaviour, no mutation
of source records, no decision-making surface, and no network, environment
or credential access. Prior boundaries (7B9/7C0/7C1) are regression-proven
inside this file.
"""

import ast
import dataclasses
import os
from pathlib import Path

import pytest

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
    SelectionCollection,
    SelectionProjection,
    build_selection_collection,
    build_selection_projection,
)

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "app" / "cad_engine" / "provider_selection_projection.py")

REPORT_BLOCKERS = frozenset({
    "SECURITY_CONTROLS",
    "TIMEOUT_CANCELLATION_SEMANTICS",
    "AVAILABILITY_SLO",
    "DEPRECATION_VERSION_CHANGE_BEHAVIOUR",
})

PROJECTION_FIELDS = (
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


def _jev_record(decision, rationale=""):
    return record_provider_selection(
        capture=_capture(), determination=_determination(),
        decision=decision, rationale=rationale)


def _ready_capture():
    """A synthetic, fully SATISFIED capture so the genuine gate reads READY
    — used only to prove the READY path stays READY through projection."""
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


def _ready_record(decision=DECISION_SELECT):
    capture = _ready_capture()
    return record_provider_selection(
        capture=capture,
        determination=evaluate_readiness(build_requirement_records(capture)),
        decision=decision)


# --------------------------------------------------------------------------------------
# 1. Module surface: consumption only.
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

    def test_function_surface_is_only_projection_and_collection(self):
        tree = _module_tree()
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert names == {
            "_validate_record", "_collection_key",
            "build_selection_projection", "build_selection_collection",
        }

    def test_no_io_process_env_or_clock_calls(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        forbidden = {
            "open", "print", "sleep", "input", "eval", "exec", "getenv",
            "environ", "read", "write", "read_text", "write_text", "mkdir",
            "unlink", "time", "random", "uuid", "datetime", "socket",
        }
        assert names.isdisjoint(forbidden), f"forbidden names used: {names & forbidden}"

    def test_no_decision_literals_exist_in_the_module(self):
        # Docstrings may name the decisions in prose; executable string
        # constants may not — the projection must never create a decision.
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
                    "a decision literal appears in the consumption layer; "
                    "the projection must never create a decision"
                )

    def test_projection_and_collection_are_frozen_plain_data(self):
        for cls in (SelectionProjection, SelectionCollection):
            assert dataclasses.is_dataclass(cls)
            assert cls.__dataclass_params__.frozen
        tree = _module_tree()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                assert not [n for n in node.body
                            if isinstance(n, ast.FunctionDef)]

    def test_no_credential_named_fields(self):
        fragments = {"key", "token", "secret", "password", "credential"}
        for cls in (SelectionProjection, SelectionCollection):
            for field in dataclasses.fields(cls):
                assert not any(
                    fragment in field.name.lower() for fragment in fragments
                ), f"field {cls.__name__}.{field.name} is credential-shaped"


# --------------------------------------------------------------------------------------
# 2. Projection of one record: verbatim, frozen, never a new decision.
# --------------------------------------------------------------------------------------
class TestProjection:
    def test_projection_preserves_every_field_verbatim(self):
        record = _jev_record(DECISION_SELECT, rationale="audit note")
        projection = build_selection_projection(record)
        for field in PROJECTION_FIELDS:
            assert getattr(projection, field) == getattr(record, field), field

    def test_select_against_not_ready_stays_visibly_select_and_not_ready(self):
        projection = build_selection_projection(_jev_record(DECISION_SELECT))
        assert projection.decision == DECISION_SELECT
        assert projection.readiness_overall == GATE_NOT_READY == "NOT_READY"
        assert projection.selected_against_not_ready is True
        assert len(projection.blocking_reasons) == 4
        assert {reason.split()[3] for reason in projection.blocking_reasons} == REPORT_BLOCKERS

    def test_decline_and_defer_remain_distinct(self):
        decline = build_selection_projection(_jev_record(DECISION_DECLINE))
        defer = build_selection_projection(_jev_record(DECISION_DEFER))
        assert decline.decision == DECISION_DECLINE
        assert defer.decision == DECISION_DEFER
        assert decline.decision != defer.decision
        assert decline.readiness_overall == defer.readiness_overall == GATE_NOT_READY
        assert decline.selected_against_not_ready is False
        assert defer.selected_against_not_ready is False

    def test_a_ready_record_remains_ready(self):
        projection = build_selection_projection(_ready_record(DECISION_SELECT))
        assert projection.readiness_overall == GATE_READY == "READY"
        assert projection.blocking_reasons == ()
        assert projection.selected_against_not_ready is False
        assert projection.provider == "Example Provider"

    def test_projection_is_frozen(self):
        projection = build_selection_projection(_jev_record(DECISION_SELECT))
        with pytest.raises(dataclasses.FrozenInstanceError):
            projection.decision = DECISION_DEFER
        with pytest.raises(dataclasses.FrozenInstanceError):
            projection.readiness_overall = GATE_READY

    def test_the_source_record_is_never_mutated_by_projection(self):
        record = _jev_record(DECISION_SELECT, rationale="keep me")
        before = dataclasses.asdict(record)
        build_selection_projection(record)
        assert dataclasses.asdict(record) == before

    def test_projection_cannot_transform_not_ready(self):
        projection = build_selection_projection(_jev_record(DECISION_SELECT))
        assert projection.readiness_overall != GATE_READY
        assert projection.readiness_overall == GATE_NOT_READY
        assert len(projection.blocking_reasons) == 4

    def test_two_projections_of_the_same_record_are_identical(self):
        record = _jev_record(DECISION_DEFER)
        assert (build_selection_projection(record)
                == build_selection_projection(record))
        assert (repr(build_selection_projection(record))
                == repr(build_selection_projection(record)))

    def test_non_record_is_refused(self):
        with pytest.raises(TypeError, match="record must be a ProviderSelectionRecord"):
            build_selection_projection("not a record")

    def test_record_with_an_invalid_decision_is_refused(self):
        forged = ProviderSelectionRecord(
            provider="Jev (TypeSafe AI)",
            capture_digest="a" * 64,
            readiness_overall=GATE_NOT_READY,
            blocking_reasons=(),
            decision="MAYBE",
            rationale="",
            selected_against_not_ready=False,
        )
        with pytest.raises(ValueError, match="invalid decision"):
            build_selection_projection(forged)

    def test_record_with_an_invalid_readiness_is_refused(self):
        forged = ProviderSelectionRecord(
            provider="Jev (TypeSafe AI)",
            capture_digest="a" * 64,
            readiness_overall="ALMOST_READY",
            blocking_reasons=(),
            decision=DECISION_DEFER,
            rationale="",
            selected_against_not_ready=False,
        )
        with pytest.raises(ValueError, match="invalid readiness_overall"):
            build_selection_projection(forged)


# --------------------------------------------------------------------------------------
# 3. Collections: deterministic order, duplicates refused, records preserved.
# --------------------------------------------------------------------------------------
class TestCollection:
    def test_empty_collection(self):
        collection = build_selection_collection(())
        assert isinstance(collection, SelectionCollection)
        assert collection.projections == ()

    def test_single_record_collection(self):
        record = _jev_record(DECISION_SELECT)
        collection = build_selection_collection((record,))
        assert len(collection.projections) == 1
        assert collection.projections[0] == build_selection_projection(record)

    def test_collection_of_all_three_decisions_keeps_them_distinct(self):
        collection = build_selection_collection((
            _jev_record(DECISION_SELECT),
            _jev_record(DECISION_DECLINE),
            _jev_record(DECISION_DEFER),
        ))
        decisions = [p.decision for p in collection.projections]
        assert set(decisions) == set(DECISIONS)
        assert len(decisions) == 3

    def test_ordering_is_the_documented_canonical_key(self):
        collection = build_selection_collection((
            _jev_record(DECISION_SELECT),
            _jev_record(DECISION_DECLINE),
            _jev_record(DECISION_DEFER),
        ))
        # Same provider/digest/readiness: the key orders by decision.
        assert [p.decision for p in collection.projections] == [
            DECISION_DECLINE, DECISION_DEFER, DECISION_SELECT,
        ]

    def test_ordering_is_independent_of_input_order(self):
        shuffled = build_selection_collection((
            _jev_record(DECISION_DEFER),
            _jev_record(DECISION_SELECT),
            _jev_record(DECISION_DECLINE),
        ))
        ordered = build_selection_collection((
            _jev_record(DECISION_DECLINE),
            _jev_record(DECISION_SELECT),
            _jev_record(DECISION_DEFER),
        ))
        assert shuffled == ordered
        assert shuffled.projections == ordered.projections

    def test_multiple_providers_are_all_preserved(self):
        collection = build_selection_collection((
            _jev_record(DECISION_SELECT),
            _ready_record(DECISION_SELECT),
        ))
        providers = {p.provider for p in collection.projections}
        assert providers == {PROVIDER_NAME, "Example Provider"}
        assert len(collection.projections) == 2
        # Canonical order: "Example Provider" < "Jev (TypeSafe AI)".
        assert collection.projections[0].provider == "Example Provider"

    def test_distinct_decisions_about_the_same_provider_are_preserved(self):
        # Explicitly justified: a DECLINE followed by a SELECT is two
        # distinct recorded decisions, never one merged history.
        collection = build_selection_collection((
            _jev_record(DECISION_DECLINE, rationale="first review"),
            _jev_record(DECISION_SELECT, rationale="after remediation"),
        ))
        assert len(collection.projections) == 2
        assert [p.rationale for p in collection.projections] == [
            "first review", "after remediation",
        ]

    def test_exact_duplicate_records_are_refused_not_collapsed(self):
        record = _jev_record(DECISION_SELECT)
        with pytest.raises(ValueError, match="duplicate selection record"):
            build_selection_collection((record, record))

    def test_non_tuple_input_is_refused(self):
        with pytest.raises(TypeError, match="records must be a tuple"):
            build_selection_collection([_jev_record(DECISION_SELECT)])

    def test_non_record_element_is_refused(self):
        with pytest.raises(TypeError, match="record must be a ProviderSelectionRecord"):
            build_selection_collection((_jev_record(DECISION_SELECT), "not a record"))

    def test_collection_is_frozen(self):
        collection = build_selection_collection((_jev_record(DECISION_SELECT),))
        with pytest.raises(dataclasses.FrozenInstanceError):
            collection.projections = ()

    def test_source_records_are_never_mutated_by_collection(self):
        records = (_jev_record(DECISION_SELECT), _jev_record(DECISION_DEFER))
        before = [dataclasses.asdict(r) for r in records]
        build_selection_collection(records)
        assert [dataclasses.asdict(r) for r in records] == before

    def test_collection_projections_match_individually_built_projections(self):
        records = (_jev_record(DECISION_SELECT), _jev_record(DECISION_DECLINE))
        collection = build_selection_collection(records)
        expected = sorted(
            (build_selection_projection(r) for r in records),
            key=lambda p: (p.provider, p.capture_digest, p.readiness_overall,
                           p.decision, p.rationale, p.blocking_reasons,
                           p.selected_against_not_ready))
        assert collection.projections == tuple(expected)

    def test_no_new_decision_can_appear_in_a_collection(self):
        records = (_jev_record(DECISION_DEFER), _ready_record(DECISION_SELECT))
        collection = build_selection_collection(records)
        assert {p.decision for p in collection.projections} <= set(DECISIONS)


# --------------------------------------------------------------------------------------
# 4. Determinism and auditability.
# --------------------------------------------------------------------------------------
class TestDeterminism:
    def test_collection_built_twice_is_identical(self):
        records = (_jev_record(DECISION_SELECT), _jev_record(DECISION_DEFER))
        first = build_selection_collection(records)
        second = build_selection_collection(records)
        assert first == second
        assert repr(first) == repr(second)

    def test_projections_and_collections_are_hashable(self):
        records = (_jev_record(DECISION_SELECT), _jev_record(DECISION_DEFER))
        collection = build_selection_collection(records)
        expected = sorted(
            (build_selection_projection(r) for r in records),
            key=lambda p: (p.provider, p.capture_digest, p.readiness_overall,
                           p.decision, p.rationale, p.blocking_reasons,
                           p.selected_against_not_ready))
        assert [hash(p) for p in collection.projections] == [hash(p) for p in expected]
        assert len({hash(p) for p in collection.projections}) == 2

    def test_projection_content_is_a_complete_audit_view(self):
        record = _jev_record(DECISION_SELECT, rationale="human note")
        projection = build_selection_projection(record)
        assert projection.provider == PROVIDER_NAME
        assert projection.capture_digest == record.capture_digest
        assert projection.readiness_overall == GATE_NOT_READY
        assert projection.blocking_reasons == record.blocking_reasons
        assert projection.decision == DECISION_SELECT
        assert projection.rationale == "human note"
        assert projection.selected_against_not_ready is True


# --------------------------------------------------------------------------------------
# 5. No network, no environment, no credentials.
# --------------------------------------------------------------------------------------
class TestNoNetworkEnvCredentials:
    def test_module_reads_no_environment(self):
        tree = _module_tree()
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "getenv" not in names
        assert "environ" not in names

    def test_collections_are_identical_under_an_empty_environment(self, monkeypatch):
        records = (_jev_record(DECISION_SELECT),)
        before = build_selection_collection(records)
        monkeypatch.setattr(os, "environ", {})
        assert build_selection_collection(records) == before

    def test_no_credentials_are_embedded(self):
        for fragment in ("api_key =", "token =", "password =", "secret ="):
            assert fragment not in _module_source()


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
        assert DECISIONS == {DECISION_SELECT, DECISION_DECLINE, DECISION_DEFER}
        record = _jev_record(DECISION_SELECT)
        assert record.readiness_overall == GATE_NOT_READY
        assert record.selected_against_not_ready is True
        assert len(record.blocking_reasons) == 4
