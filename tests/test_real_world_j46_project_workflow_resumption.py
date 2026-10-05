"""
J46 — production review resumption, the replay guard and resolution safety.

WHAT THIS FILE IS

The proof that a project's current review workflow can be reconstructed from revision 0
plus the revisions J22 persisted, by a process that did not produce them — and that the
reconstruction REPLAYS what was recorded instead of re-running any of it.

WHY THE MATERIAL IS GENUINE, AND WHY THAT IS THE WHOLE ARGUMENT

Revision 0 is not built here. It comes from J24A's own producer over the genuine Arkles
capture (`tests/data/arkles_strand_page_extractions.json`, 30 real pages, 6 of them
unparsable) through J24A's own suite, reused verbatim:

    from tests import test_real_world_j24a_revision_zero_producer as j24a

That matters more here than anywhere: the whole claim of this milestone is that REVISION 0
IS REVIEW AND THE RECORDED REVISIONS ARE SOMETHING ELSE. A reconstruction this file had
written itself could agree with a history this file had also written itself and prove
nothing. So revision 0 is the real one — three connections (RP-0001..RP-0003), every one of
them REVIEW, none with a connection id, an output status, a verification status, a
generated file or a processed revision — and the recorded revisions below are recorded ON
TOP of it.

The consequence is that every historical-outcome test in this file is non-vacuous. A
recomputation from revision 0 cannot produce AUTO, cannot produce CONFIRM, cannot produce
GENERATED and cannot produce VERIFIED, because revision 0 says REVIEW for all three
connections. When a replayed state shows AUTO/GENERATED/VERIFIED it can only have come
from the recorded rows — which is exactly the property being asserted.

WHAT IS A DOUBLE HERE, AND WHAT IS NOT

    DOUBLED   the persisted review chain (the `_Ledger`), and the section matcher.
    REAL      J24A's producer over the real capture, J22's snapshot builder and row
              decoder, J22's `_state_from_item` codec, 7AJ's `_assemble_state`
              bookkeeping, 7AJ's `apply_human_resolution`, the ten-key comparator and
              the whole resumption module.

The ledger is a stand-in for ROWS, not for the codec: it emits header/item rows in the
production column shapes and every snapshot it hands the resumption module is decoded by
J22's own `snapshot_from_rows`. A test may not write to the append-only review ledger, so
the rows are built in memory — but nothing downstream of the rows is faked.

The ledger is also deliberately a double that can be CORRUPTED: several tests below mutate
a copy of the recorded rows (a duplicated item, a missing item, two advanced connections,
a partial resolution payload) and prove the replay refuses rather than repairs. A double
that can only be well-formed cannot evidence fail-closed behaviour.
"""
from __future__ import annotations

import ast
import copy
import importlib
import sys
import types
from pathlib import Path

import pytest

from app.cad_engine import connection_review_snapshot as snapshot_module
from app.cad_engine import exception_resolution
from app.cad_engine.connection_review_snapshot import snapshot_from_rows
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_FIELD_DECISION,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
)
from app.cad_engine.fabrication_output_gate import (
    OUTPUT_BLOCKER_SPECIFICATION,
    OUTPUT_WARNING_CONFIRMATION_DEFERRED,
)
from app.production_review import project_workflow_resumption as resumption

from tests import test_real_world_j24a_revision_zero_producer as j24a
from tests.test_real_world_j19_production_review_binding import _code_only

REPO = j24a.REPO
MODULE_PATH = REPO / "app" / "production_review" / "project_workflow_resumption.py"

# The genuine recorded reason codes. Their VALUES do not matter to replay — a resumed
# state must return whatever a revision recorded — which is why they are drawn from the
# existing gate vocabulary rather than invented: a replay that consulted the generator or
# the gate to find out what a code should be would be re-running it.
RECORDED_BLOCKER = OUTPUT_BLOCKER_SPECIFICATION
RECORDED_WARNING = OUTPUT_WARNING_CONFIRMATION_DEFERRED

# The three historical decision classes, the dispatch outcomes and the verification
# outcomes a recorded revision may carry. Spelled as the modules that own them spell them.
AUTO = "AUTO"
CONFIRM = "CONFIRM"
REVIEW = "REVIEW"
GENERATED = "GENERATED"
GENERATION_FAILED = "GENERATION_FAILED"
VERIFIED = "VERIFIED"
VERIFICATION_FAILED = "FAILED"
NOT_VERIFIABLE = "NOT_VERIFIABLE"
NO_ARTIFACT = "NO_ARTIFACT"

# A valid payload per answer type, used to record a genuine human answer against a genuine
# task. Every one of these is accepted by `apply_human_resolution` for the Arkles package's
# own tasks — proved in `TestK` below, which applies each of them.
_ANSWER_FOR = {
    ANSWER_APPROVE_REVIEW: None,
    ANSWER_AUTOMATION_CONFIRMATION: None,
    ANSWER_POSITION_VALUE: "START",
    ANSWER_CONNECTION_IDENTITY: "C-1",
    ANSWER_MATERIAL_VALUE: "300",
    ANSWER_ACKNOWLEDGMENT: "recorded by the reviewer",
    ANSWER_MEMBER_SELECTION: ("M1",),
    ANSWER_CONFIRMED_FIELDS: ("material",),
    ANSWER_ATTACHMENTS_VALUE: ({"member_mark": "M1", "surface_reference": "TOP"},),
    ANSWER_PLATE_VALUE: {"type": "FLAT", "thickness_mm": 10.0},
    ANSWER_HOLES_VALUE: {"quantity": 4, "diameter_mm": 20.0},
    ANSWER_LOCATION_VALUE: {"x": 0.0, "y": 0.0, "z": 0.0},
    ANSWER_MEMBER_POSITION_ATTACHMENTS: (
        ("M1",), "START", ({"member_mark": "M1", "surface_reference": "TOP"},),
    ),
    ANSWER_FIELD_DECISION: ("material", "supply", "300"),
}


# ===========================================================================
# The persisted chain — a stand-in for J22's rows that decodes through J22.
# ===========================================================================
class _Ledger:
    """A project's recorded review revisions, as ROWS, decoded by J22's own reader.

    Revision 0 is the reconstruction's, and is not in here: the first recorded revision is
    1. `advance` records the next revision, moving one connection and carrying the whole
    project picture forward — which is what a J22 revision is, one item per connection.
    """

    def __init__(self, workflow, *, project_status=None):
        self.project_id = workflow.project_id
        self.order = tuple(connection.package_id for connection in workflow.connections)
        self.task_ids = {
            group.review_package_id: tuple(task.task_id for task in group.tasks)
            for group in workflow.exception_package.connection_tasks
        }
        self.task_types = {
            task.task_id: task.task_type
            for group in workflow.exception_package.connection_tasks
            for task in group.tasks
        }
        self.answer_types = {
            task.task_id: task.answer_type
            for group in workflow.exception_package.connection_tasks
            for task in group.tasks
        }
        self.state = {
            connection.package_id: self._baseline(connection)
            for connection in workflow.connections
        }
        self.answers = {package_id: [] for package_id in self.order}
        self.project_status = project_status or workflow.project_decision
        self.rows = []

    @staticmethod
    def _baseline(connection):
        return {
            "connection_id": connection.connection_id,
            "decision": connection.decision,
            "output_status": connection.output_status,
            "verification_status": connection.verification_status,
            "last_processed_revision": connection.last_processed_revision,
            "blocker_codes": list(connection.blockers),
            "warning_codes": list(connection.warnings),
            "generated_files": list(connection.generated_files),
        }

    # -- recording ---------------------------------------------------------
    def advance(self, package_id, **overrides):
        """Records the next revision, advancing one connection. Returns that revision."""
        revision = len(self.rows) + 1
        assert package_id in self.state, package_id
        state = dict(self.state[package_id])
        state.update(overrides)
        state["last_processed_revision"] = revision
        self.state[package_id] = state
        self.rows.append(self._page(revision))
        return revision

    def record_answer(self, package_id, task_id):
        """Records one human answer, as it would have been persisted on its item."""
        answer_type = self.answer_types[task_id]
        payload = {
            "task_id": task_id,
            "task_type": self.task_types[task_id],
            "answer_type": answer_type,
            "answer": copy.deepcopy(_ANSWER_FOR[answer_type]),
            "evidence": "recorded by a human reviewer",
        }
        self.answers[package_id].append(payload)
        return payload

    def _page(self, revision):
        items = []
        for package_id in self.order:
            state = self.state[package_id]
            recorded = {answer["task_id"]: answer for answer in self.answers[package_id]}
            tasks = [
                {"task_id": task_id, "resolution": recorded.pop(task_id, None)}
                for task_id in self.task_ids[package_id]
            ]
            # An answer recorded against a task this connection does NOT own is still
            # persisted on this item. The ledger keeps such a row rather than dropping it:
            # it is the corrupt record the ownership seam exists to refuse, and a double
            # that silently cleaned it up could not evidence that refusal.
            tasks.extend(
                {"task_id": task_id, "resolution": answer}
                for task_id, answer in recorded.items()
            )
            items.append({
                "project_id": self.project_id,
                "review_revision": revision,
                "review_package_id": package_id,
                "connection_id": state["connection_id"],
                "decision": state["decision"],
                "output_status": state["output_status"],
                "verification_status": state["verification_status"],
                "last_processed_revision": state["last_processed_revision"],
                "blocker_codes": list(state["blocker_codes"]),
                "warning_codes": list(state["warning_codes"]),
                "ai_readings": {},
                "evidence": {},
                "provenance": {},
                "tasks": tasks,
                "generated_files": list(state["generated_files"]),
            })
        header = {
            "project_id": self.project_id,
            "review_revision": revision,
            "project_status": self.project_status,
            "evidence_identity": {},
            "evidence_run_ids": [],
        }
        return header, items

    # -- reading -----------------------------------------------------------
    @property
    def snapshots(self):
        """The chain as the persistence layer would hand it back: decoded rows."""
        return tuple(snapshot_from_rows(header, items) for header, items in self.rows)

    def page(self, revision=1):
        """A MUTABLE copy of one revision's rows, for the corruption tests."""
        header, items = self.rows[revision - 1]
        return copy.deepcopy(header), copy.deepcopy(items)


def _resume(store, reconstruction, *, history, **kwargs):
    """The seam under test, driven the way a caller drives it."""
    return resumption.resume_project_workflow(
        reconstruction.project_id,
        section_matcher=j24a._StubMatcher(),
        repository=store,
        history=history,
        **kwargs,
    )


def _refused(store, reconstruction, *, history, code, **kwargs):
    with pytest.raises(resumption.ResumptionRefused) as caught:
        _resume(store, reconstruction, history=history, **kwargs)
    assert caught.value.code == code, caught.value.detail
    return caught.value


def _task_refused(store, reconstruction, *, history, code, **kwargs):
    """The seam's own refusal type — a resolution problem is not a resumption one."""
    with pytest.raises(resumption.ResolutionTaskRefused) as caught:
        _resume(store, reconstruction, history=history, **kwargs)
    assert caught.value.code == code, caught.value.detail
    return caught.value


def _other(value):
    """A value that is never equal to the one given — for the divergence tests."""
    if value is None:
        return "SOMETHING ELSE"
    if isinstance(value, tuple):
        return value + ("EXTRA",)
    if isinstance(value, int):
        return value + 1
    return f"{value}!"


@pytest.fixture(scope="module")
def arkles():
    """The genuine revision-0 workflow: J24A's store and its reconstruction of it."""
    store = j24a._arkles_store()
    reconstruction = j24a._reconstruct(store, j24a.ARKLES_PROJECT)
    assert reconstruction is not None
    return store, reconstruction


@pytest.fixture()
def ledger(arkles):
    return _Ledger(arkles[1].workflow)


# ===========================================================================
# A. An empty history is revision 0 — and is not a refusal.
# ===========================================================================
class TestAAnEmptyHistoryIsRevisionZero:
    def test_a_project_with_no_recorded_revision_resumes_at_revision_zero(self, arkles):
        store, reconstruction = arkles

        result = _resume(store, reconstruction, history=())

        assert result is not None
        assert result.project_id == j24a.ARKLES_PROJECT
        assert result.head_revision == 0
        assert result.revisions == ()
        assert result.agreements == ()

    def test_an_empty_history_returns_the_revision_zero_state_untouched(self, arkles):
        """No revision was replayed, so nothing was replayed ON it either.

        The seam calls J24A's producer itself, so the object returned is that producer's
        own revision-0 state — compared here against an independently built one, field for
        field, because "the same as what the producer gives" is the claim, and a claim of
        identity between two constructions would be a claim about Python objects.
        """
        store, reconstruction = arkles

        result = _resume(store, reconstruction, history=())

        assert result.workflow == reconstruction.workflow
        assert result.workflow.connections == reconstruction.workflow.connections
        assert result.workflow.connection_records == reconstruction.workflow.connection_records
        assert result.workflow.revision == 0

    def test_the_revision_zero_workflow_is_the_genuine_arkles_capture(self, arkles):
        """The baseline every historical-outcome test below depends on."""
        _, reconstruction = arkles
        workflow = reconstruction.workflow

        assert [connection.package_id for connection in workflow.connections] == [
            "RP-0001", "RP-0002", "RP-0003",
        ]
        assert {connection.decision for connection in workflow.connections} == {REVIEW}
        assert {connection.output_status for connection in workflow.connections} == {None}
        assert {connection.verification_status for connection in workflow.connections} == {None}
        assert {connection.last_processed_revision for connection in workflow.connections} == {None}
        assert workflow.project_decision == REVIEW

    def test_the_head_of_an_empty_chain_is_zero(self):
        assert resumption._head_revision(()) == 0

    def test_an_injected_history_means_the_persisted_chain_is_never_read(self, arkles, monkeypatch):
        """`history` is injectable, and injecting it must not fall through to a read."""
        store, reconstruction = arkles
        reads = []
        monkeypatch.setattr(
            snapshot_module, "snapshot_from_rows", snapshot_module.snapshot_from_rows,
        )
        monkeypatch.setattr(
            resumption, "_read_history",
            lambda project_id: reads.append(project_id) or (),
        )

        _resume(store, reconstruction, history=())

        assert reads == []


# ===========================================================================
# B. One and three recorded revisions — the replay itself.
# ===========================================================================
class TestBTheRecordedChainIsReplayed:
    def test_one_recorded_revision_is_replayed_onto_revision_zero(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",))

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.head_revision == 1
        assert result.revisions == (1,)
        assert result.workflow.revision == 1

    def test_three_recorded_revisions_are_replayed_in_order(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",))
        chain.advance("RP-0002", decision=CONFIRM, output_status="BLOCKED_CONFIRMATION")
        chain.advance("RP-0003", decision=REVIEW)

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.revisions == (1, 2, 3)
        assert result.head_revision == 3
        assert result.workflow.revision == 3

    def test_every_advanced_connection_carries_the_revision_that_advanced_it(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")
        chain.advance("RP-0003")

        result = _resume(store, reconstruction, history=chain.snapshots)
        processed = {
            connection.package_id: connection.last_processed_revision
            for connection in result.workflow.connections
        }

        assert processed == {"RP-0001": 1, "RP-0002": 2, "RP-0003": 3}

    def test_a_connection_a_later_revision_never_touched_keeps_its_recorded_state(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",))
        chain.advance("RP-0002", decision=CONFIRM)
        chain.advance("RP-0003", decision=REVIEW)

        result = _resume(store, reconstruction, history=chain.snapshots)
        first = result.workflow.connections[0]

        assert first.decision == AUTO
        assert first.output_status == GENERATED
        assert first.verification_status == VERIFIED
        assert first.generated_files == ("artifacts/RP-0001.pdf",)
        assert first.last_processed_revision == 1

    def test_the_recorded_project_decision_is_carried_verbatim(self, arkles):
        """The decision is the RECORDED one, never recomputed from the connection mix."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow, project_status=REVIEW)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED)
        chain.advance("RP-0002", decision=CONFIRM)

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.project_decision == REVIEW
        assert result.workflow.auto_count == 1
        assert result.workflow.confirm_count == 1
        assert result.workflow.review_count == 1

    def test_the_replayed_workflow_carries_the_recorded_project_decision_when_it_differs(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow, project_status=REVIEW)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        header["project_status"] = "BLOCKED"
        chain.rows[-1] = (header, items)

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.project_decision == "BLOCKED"

    def test_replay_is_deterministic(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",))
        chain.advance("RP-0002", decision=CONFIRM)

        first = _resume(store, reconstruction, history=chain.snapshots)
        second = _resume(store, reconstruction, history=chain.snapshots)

        assert first.workflow == second.workflow
        assert first.revisions == second.revisions
        assert first.agreements == second.agreements

    def test_an_out_of_order_chain_is_ordered_rather_than_refused(self, arkles):
        """The rows are the chain; the order they are handed over in is not the chain."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")

        result = _resume(store, reconstruction, history=tuple(reversed(chain.snapshots)))

        assert result.revisions == (1, 2)
        assert result.head_revision == 2


# ===========================================================================
# C. History validation — the chain is contiguous from 1, or it is refused.
# ===========================================================================
class TestCTheRecordedChainIsValidatedBeforeItIsReplayed:
    def test_a_contiguous_chain_from_one_is_accepted(self):
        assert resumption.validate_history([1, 2, 3]) == (1, 2, 3)
        assert resumption.validate_history([]) == ()

    def test_a_gap_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")
        del chain.rows[0]

        refusal = _refused(store, reconstruction, history=chain.snapshots,
                           code=resumption.RESUMPTION_HISTORY_GAP)

        assert "2" in refusal.detail and "never recorded" in refusal.detail

    def test_a_duplicated_revision_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        duplicated = chain.snapshots + (chain.snapshots[0],)

        refusal = _refused(store, reconstruction, history=duplicated,
                           code=resumption.RESUMPTION_DUPLICATE_REVISION)

        assert "more than once" in refusal.detail

    def test_revision_zero_inside_the_chain_is_refused(self, arkles):
        """Revision 0 is the reconstructed state and is not itself a recorded revision."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        header["review_revision"] = 0
        chain.rows[-1] = (header, items)

        _refused(store, reconstruction, history=chain.snapshots,
                 code=resumption.RESUMPTION_HISTORY_GAP)

    def test_a_negative_revision_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.validate_history([-1])
        assert caught.value.code == resumption.RESUMPTION_HISTORY_GAP

    def test_a_boolean_revision_is_refused(self):
        """`True == 1` in Python; a revision is a whole number, and a bool is not one."""
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.validate_history([True])
        assert caught.value.code == resumption.RESUMPTION_HISTORY_GAP

    def test_a_non_integer_revision_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.validate_history([1, "2"])
        assert caught.value.code == resumption.RESUMPTION_HISTORY_GAP

    def test_every_declared_refusal_is_distinct_named_and_used(self):
        """A declared code that is never raised would be a refusal that cannot happen."""
        codes = resumption.RESUMPTION_REFUSALS + resumption.RESOLUTION_REFUSALS
        source = MODULE_PATH.read_text(encoding="utf-8")

        assert len(set(codes)) == len(codes)
        assert all(code.startswith("RESUMPTION_") for code in resumption.RESUMPTION_REFUSALS)
        assert all(code.startswith("RESOLUTION_") for code in resumption.RESOLUTION_REFUSALS)
        for code in codes:
            assert source.count(code) >= 2, f"{code} is declared but never raised"

    def test_the_two_refusal_types_are_separate_so_a_caller_can_tell_them_apart(self):
        assert issubclass(resumption.ResumptionRefused, ValueError)
        assert issubclass(resumption.ResolutionTaskRefused, ValueError)
        assert not issubclass(resumption.ResumptionRefused, resumption.ResolutionTaskRefused)
        assert not issubclass(resumption.ResolutionTaskRefused, resumption.ResumptionRefused)


# ===========================================================================
# D. The expected-revision guard.
# ===========================================================================
class TestDTheExpectedRevisionGuard:
    def test_no_expectation_is_always_satisfied(self):
        resumption.check_expected_revision(None, head_revision=0)
        resumption.check_expected_revision(None, head_revision=7)

    def test_an_expectation_equal_to_the_head_succeeds(self):
        resumption.check_expected_revision(0, head_revision=0)
        resumption.check_expected_revision(3, head_revision=3)

    def test_an_expectation_ahead_of_the_head_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.check_expected_revision(4, head_revision=3)
        assert caught.value.code == resumption.RESUMPTION_EXPECTED_REVISION_AHEAD

    def test_an_expectation_behind_the_head_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.check_expected_revision(2, head_revision=3)
        assert caught.value.code == resumption.RESUMPTION_EXPECTED_REVISION_STALE

    def test_a_negative_expectation_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.check_expected_revision(-1, head_revision=0)
        assert caught.value.code == resumption.RESUMPTION_EXPECTED_REVISION_INVALID

    def test_a_boolean_expectation_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.check_expected_revision(True, head_revision=1)
        assert caught.value.code == resumption.RESUMPTION_EXPECTED_REVISION_INVALID

    def test_a_non_integer_expectation_is_refused(self):
        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.check_expected_revision("1", head_revision=1)
        assert caught.value.code == resumption.RESUMPTION_EXPECTED_REVISION_INVALID

    # -- the guard as the caller meets it -----------------------------------
    def test_a_stale_caller_is_refused_over_the_recorded_chain(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")

        _refused(store, reconstruction, history=chain.snapshots, expected_revision=1,
                 code=resumption.RESUMPTION_EXPECTED_REVISION_STALE)

    def test_a_caller_ahead_of_the_recorded_chain_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")

        _refused(store, reconstruction, history=chain.snapshots, expected_revision=2,
                 code=resumption.RESUMPTION_EXPECTED_REVISION_AHEAD)

    def test_a_caller_matching_the_recorded_chain_succeeds(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")

        result = _resume(store, reconstruction, history=chain.snapshots, expected_revision=2)

        assert result.head_revision == 2

    def test_a_caller_expecting_revision_zero_over_an_empty_chain_succeeds(self, arkles):
        store, reconstruction = arkles

        result = _resume(store, reconstruction, history=(), expected_revision=0)

        assert result.head_revision == 0

    def test_the_guard_runs_before_any_replay(self, arkles, monkeypatch):
        """A refused expectation leaves no replayed state behind it."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        replayed = []
        monkeypatch.setattr(
            resumption, "replay_revision",
            lambda workflow, *, snapshot: replayed.append(snapshot) or workflow,
        )

        _refused(store, reconstruction, history=chain.snapshots, expected_revision=9,
                 code=resumption.RESUMPTION_EXPECTED_REVISION_AHEAD)

        assert replayed == []


# ===========================================================================
# E. The ten-key agreement guard.
# ===========================================================================
class TestETheTenKeyAgreementGuard:
    def test_the_guard_compares_exactly_ten_named_keys(self):
        assert resumption.AGREEMENT_KEYS == (
            "review_revision",
            "review_package_id",
            "connection_id",
            "decision",
            "output_status",
            "verification_status",
            "generated_files",
            "blocker_codes",
            "warning_codes",
            "last_processed_revision",
        )

    def test_identical_projections_agree(self):
        projection = {key: None for key in resumption.AGREEMENT_KEYS}
        assert resumption.compare_agreement(projection, dict(projection)).agrees

    def _genuine_pair(self, arkles):
        """A replayed state and the persisted item it was replayed from."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",),
                      blocker_codes=[RECORDED_BLOCKER],
                      warning_codes=[RECORDED_WARNING])
        result = _resume(store, reconstruction, history=chain.snapshots)
        snapshot = chain.snapshots[0]
        connection = result.workflow.connections[0]
        return (
            resumption.agreement_projection_from_state(
                review_revision=snapshot.review_revision, connection=connection
            ),
            resumption.agreement_projection_from_item(snapshot, snapshot.items[0]),
        )

    def test_a_genuine_replay_agrees_on_every_key(self, arkles):
        left, right = self._genuine_pair(arkles)

        report = resumption.compare_agreement(left, right)

        assert report.agrees is True
        assert report.diverged_key is None
        assert report.detail == ""
        assert set(left) == set(right) == set(resumption.AGREEMENT_KEYS)

    def test_each_of_the_ten_keys_diverging_is_reported_by_name(self, arkles):
        """Each key is moved on its own, over projections read off a genuine replay."""
        left, right = self._genuine_pair(arkles)
        baseline = resumption.compare_agreement(left, dict(right))
        assert baseline.agrees

        for key in resumption.AGREEMENT_KEYS:
            diverged = dict(right)
            diverged[key] = _other(right[key])
            assert diverged[key] != right[key], key

            report = resumption.compare_agreement(left, diverged)

            assert report.agrees is False, key
            assert report.diverged_key == key, report.detail
            assert key in report.detail, report.detail
            assert repr(diverged[key]) in report.detail, report.detail

    def test_the_first_diverging_key_in_key_order_is_the_one_reported(self):
        projection = {key: "same" for key in resumption.AGREEMENT_KEYS}
        diverged = dict(projection)
        diverged["warning_codes"] = "different"
        diverged["decision"] = "also different"

        report = resumption.compare_agreement(projection, diverged)

        assert report.diverged_key == "decision"

    def test_the_comparison_is_deterministic_and_does_not_touch_its_inputs(self):
        left = {key: None for key in resumption.AGREEMENT_KEYS}
        right = dict(left)
        right["generated_files"] = ("a.pdf",)
        left_before, right_before = dict(left), dict(right)

        first = resumption.compare_agreement(left, right)
        second = resumption.compare_agreement(left, right)

        assert first == second
        assert left == left_before
        assert right == right_before

    def test_a_stored_list_and_a_rebuilt_tuple_agree(self):
        """The persisted column is a JSON list; the rebuilt state is a tuple."""
        left = {key: None for key in resumption.AGREEMENT_KEYS}
        right = dict(left)
        left["generated_files"] = ("a.pdf",)
        right["generated_files"] = ["a.pdf"]

        assert resumption.compare_agreement(left, right).agrees is True

    def test_cad_object_identity_does_not_falsely_fail_agreement(self):
        """Two states carrying DIFFERENT CAD handles but the same plain data agree.

        This is the whole reason the guard is a projection rather than `==`: these two
        objects are genuinely unequal, and the ten-key comparison is what a caller can
        actually act on.
        """
        import dataclasses

        @dataclasses.dataclass(frozen=True)
        class _Handle:
            """An OCCT/CadQuery-shaped value: never equal to another handle."""

            token: str

            def __eq__(self, other):  # noqa: D105 - identity only, deliberately
                return self is other

            def __hash__(self):
                return id(self)

        @dataclasses.dataclass(frozen=True)
        class _State:
            package_id: str
            connection_id: str | None
            decision: str
            output_status: str | None
            verification_status: str | None
            generated_files: tuple
            blockers: tuple
            warnings: tuple
            last_processed_revision: int | None
            cad: object

        first = _State("RP-0001", "C-1", AUTO, GENERATED, VERIFIED, ("a.pdf",), (), (), 1,
                       _Handle("body-1"))
        second = _State("RP-0001", "C-1", AUTO, GENERATED, VERIFIED, ("a.pdf",), (), (), 1,
                        _Handle("body-2"))

        assert first != second, "the two states are genuinely unequal — the handles differ"
        left = resumption.agreement_projection_from_state(review_revision=1, connection=first)
        right = resumption.agreement_projection_from_state(review_revision=1, connection=second)

        assert resumption.compare_agreement(left, right).agrees is True

    def test_the_revision_is_read_from_the_snapshot_header_not_the_item(self, arkles):
        """A J22 item carries no revision of its own; there is no second source."""
        _, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        snapshot = chain.snapshots[0]

        projection = resumption.agreement_projection_from_item(snapshot, snapshot.items[0])
        assert projection["review_revision"] == snapshot.review_revision

        header, items = chain.page(1)
        header["review_revision"] = 5
        moved = snapshot_from_rows(header, items)
        projection = resumption.agreement_projection_from_item(moved, moved.items[0])

        assert projection["review_revision"] == 5

    def test_a_replay_reports_one_agreement_per_connection(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert len(result.agreements) == 6  # 3 connections x 2 revisions
        assert all(report.agrees for report in result.agreements)

    def test_the_replay_refuses_a_state_that_disagrees_with_the_rows(self, arkles, monkeypatch):
        """The guard is LIVE in the replay, not only callable: prove it can refuse."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")

        real = resumption.agreement_projection_from_state

        def _doctored(*, review_revision, connection):
            projection = real(review_revision=review_revision, connection=connection)
            if connection.package_id == "RP-0002":
                projection["decision"] = "CONFIRM"
            return projection

        monkeypatch.setattr(resumption, "agreement_projection_from_state", _doctored)

        refusal = _refused(store, reconstruction, history=chain.snapshots,
                           code=resumption.RESUMPTION_DISAGREES_WITH_HISTORY)

        assert "RP-0002" in refusal.detail
        assert "decision" in refusal.detail
        assert "persisted record is what happened" in refusal.detail


# ===========================================================================
# F. The three historical decision classes are preserved.
# ===========================================================================
class TestFHistoricalDecisionsArePreserved:
    def test_a_recorded_AUTO_state_is_resumed_as_AUTO(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",))

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.connections[0].decision == AUTO
        # Non-vacuity: revision 0 says REVIEW, so nothing recomputed this.
        assert reconstruction.workflow.connections[0].decision == REVIEW

    def test_a_recorded_CONFIRM_state_is_resumed_as_CONFIRM(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=CONFIRM, output_status="BLOCKED_CONFIRMATION",
                      warning_codes=[RECORDED_WARNING])

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.connections[0].decision == CONFIRM
        assert result.workflow.connections[0].output_status == "BLOCKED_CONFIRMATION"
        assert reconstruction.workflow.connections[0].decision == REVIEW

    def test_a_recorded_REVIEW_state_is_resumed_as_REVIEW(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=REVIEW, output_status="BLOCKED_REVIEW",
                      blocker_codes=[RECORDED_BLOCKER])

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.connections[0].decision == REVIEW
        assert result.workflow.connections[0].blockers == (RECORDED_BLOCKER,)

    def test_the_three_classes_coexist_in_one_resumed_project(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED)
        chain.advance("RP-0002", decision=CONFIRM, output_status="BLOCKED_CONFIRMATION")
        chain.advance("RP-0003", decision=REVIEW, output_status="BLOCKED_REVIEW")

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert [connection.decision for connection in result.workflow.connections] == [
            AUTO, CONFIRM, REVIEW,
        ]
        assert (result.workflow.auto_count, result.workflow.confirm_count,
                result.workflow.review_count) == (1, 1, 1)

    def test_a_decision_the_current_code_would_not_choose_is_still_preserved(self, arkles):
        """AUTO over a connection whose revision-0 blockers were never cleared.

        The recorded row says AUTO; the replayed state says AUTO. Nothing in the replay
        re-evaluates whether AUTO was eligible, because the question "was this eligible?"
        was answered when the row was written, and the row IS the answer.
        """
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      blocker_codes=[RECORDED_BLOCKER])

        result = _resume(store, reconstruction, history=chain.snapshots)
        first = result.workflow.connections[0]

        assert first.decision == AUTO
        assert first.blockers == (RECORDED_BLOCKER,), (
            "a resumed state returns the codes that were recorded, even the ones a "
            "recomputation would have cleared"
        )
        assert reconstruction.workflow.connections[0].decision == REVIEW


# ===========================================================================
# G. A historical generation outcome is replayed, never regenerated.
# ===========================================================================
class TestGHistoricalGenerationIsReplayed:
    def test_a_successful_generation_is_replayed_with_its_artifacts(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001/drawing.pdf",))

        result = _resume(store, reconstruction, history=chain.snapshots)
        first = result.workflow.connections[0]

        assert first.output_status == GENERATED
        assert first.generated_files == ("artifacts/RP-0001/drawing.pdf",)
        assert result.workflow.generated_files == ("artifacts/RP-0001/drawing.pdf",)

    def test_a_FAILED_generation_is_replayed_as_FAILED_and_not_retried(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATION_FAILED,
                      generated_files=(), blocker_codes=[RECORDED_BLOCKER])

        result = _resume(store, reconstruction, history=chain.snapshots)
        first = result.workflow.connections[0]

        assert first.output_status == GENERATION_FAILED
        assert first.verification_status is None
        assert first.generated_files == ()
        assert first.blockers == (RECORDED_BLOCKER,)

    def test_a_connection_that_was_never_dispatched_keeps_a_null_output_status(self, arkles):
        """NULL is 'never attempted' — distinct from every status, and it stays distinct."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=REVIEW)

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.connections[0].output_status is None
        assert result.workflow.generated_files == ()

    def test_the_replay_never_calls_the_generator_or_the_dispatch(self, arkles, monkeypatch):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED)
        chain.advance("RP-0002", decision=AUTO, output_status=GENERATION_FAILED)

        calls = _spies(monkeypatch)

        _resume(store, reconstruction, history=chain.snapshots)

        assert calls == []


# ===========================================================================
# H. A historical verification outcome is replayed, never re-verified.
# ===========================================================================
class TestHHistoricalVerificationIsReplayed:
    @pytest.mark.parametrize("status", [VERIFIED, VERIFICATION_FAILED, NOT_VERIFIABLE,
                                        NO_ARTIFACT])
    def test_every_recorded_verification_outcome_survives_the_replay(self, arkles, status):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=status,
                      generated_files=("artifacts/RP-0001/drawing.pdf",))

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.connections[0].verification_status == status

    def test_a_FAILED_verification_is_not_re_run_into_a_pass(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFICATION_FAILED,
                      generated_files=("artifacts/RP-0001/drawing.pdf",))

        result = _resume(store, reconstruction, history=chain.snapshots)
        first = result.workflow.connections[0]

        assert first.verification_status == VERIFICATION_FAILED
        assert first.verification_status != VERIFIED
        assert result.workflow.verified_count == 0

    def test_a_verified_connection_counts_as_verified(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED)

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.verified_count == 1

    def test_the_replay_never_calls_the_verifier(self, arkles, monkeypatch):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFICATION_FAILED)

        calls = _spies(monkeypatch)

        _resume(store, reconstruction, history=chain.snapshots)

        assert calls == []


# ===========================================================================
# I. A recorded revision that cannot be replayed is refused, not repaired.
# ===========================================================================
class TestIAnUnreplayableRevisionIsRefused:
    def test_a_revision_recording_a_connection_this_project_does_not_have_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        items[1]["review_package_id"] = "RP-9999"
        chain.rows[-1] = (header, items)

        refusal = _refused(store, reconstruction, history=chain.snapshots,
                           code=resumption.RESUMPTION_PACKAGE_UNKNOWN)

        assert "RP-9999" in refusal.detail

    def test_a_revision_missing_a_connection_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        del items[1]
        chain.rows[-1] = (header, items)

        refusal = _refused(store, reconstruction, history=chain.snapshots,
                           code=resumption.RESUMPTION_INCOMPLETE_SNAPSHOT)

        assert "RP-0002" in refusal.detail

    def test_a_revision_naming_one_connection_twice_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        items[1]["review_package_id"] = items[0]["review_package_id"]
        chain.rows[-1] = (header, items)

        _refused(store, reconstruction, history=chain.snapshots,
                 code=resumption.RESUMPTION_INCOMPLETE_SNAPSHOT)

    def test_a_revision_advancing_two_connections_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        items[1]["last_processed_revision"] = 1
        chain.rows[-1] = (header, items)

        refusal = _refused(store, reconstruction, history=chain.snapshots,
                           code=resumption.RESUMPTION_NOT_ONE_CONNECTION)

        assert "advances 2 connection(s)" in refusal.detail

    def test_a_revision_advancing_no_connection_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        for item in items:
            item["last_processed_revision"] = None
        chain.rows[-1] = (header, items)

        _refused(store, reconstruction, history=chain.snapshots,
                 code=resumption.RESUMPTION_NOT_ONE_CONNECTION)

    def test_a_revision_of_another_project_is_refused(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        header, items = chain.page(1)
        header["project_id"] = j24a.SELBY_PROJECT
        chain.rows[-1] = (header, items)

        _refused(store, reconstruction, history=chain.snapshots,
                 code=resumption.RESUMPTION_SNAPSHOT_NOT_OF_THIS_PROJECT)

    def test_a_revision_that_does_not_follow_the_workflow_is_refused_directly(self, arkles):
        """Revision 1 DOES follow revision 0; revision 2 does not, and is refused."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")

        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.replay_revision(reconstruction.workflow, snapshot=chain.snapshots[1])

        assert caught.value.code == resumption.RESUMPTION_HISTORY_GAP
        assert "cannot follow" in caught.value.detail

    def test_a_revision_replayed_twice_is_refused_by_the_same_guard(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        once = resumption.replay_revision(reconstruction.workflow, snapshot=chain.snapshots[0])

        with pytest.raises(resumption.ResumptionRefused) as caught:
            resumption.replay_revision(once, snapshot=chain.snapshots[0])

        assert caught.value.code == resumption.RESUMPTION_HISTORY_GAP

    def test_replaying_a_non_workflow_or_non_snapshot_is_a_type_error(self, arkles):
        _, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")

        with pytest.raises(TypeError):
            resumption.replay_revision(object(), snapshot=chain.snapshots[0])
        with pytest.raises(TypeError):
            resumption.replay_revision(reconstruction.workflow, snapshot=object())

    def test_a_partially_recorded_human_answer_is_refused_rather_than_replayed(self, arkles):
        """An answer missing its payload is never replayed as a blank one."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0001-T01")
        chain.advance("RP-0001")
        header, items = chain.page(1)
        del items[0]["tasks"][0]["resolution"]["answer_type"]
        chain.rows[-1] = (header, items)

        refusal = _refused(store, reconstruction, history=chain.snapshots,
                           code=resumption.RESUMPTION_RESOLUTION_UNREADABLE)

        assert "answer_type" in refusal.detail


# ===========================================================================
# J. Recorded human answers are re-applied to the contract, purely.
# ===========================================================================
class TestJRecordedAnswersAreReappliedPurely:
    def test_a_recorded_answer_is_applied_to_the_exception_contract(self, arkles):
        store, reconstruction = arkles
        before = reconstruction.workflow.exception_package.open_task_count
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0001-T01")
        chain.advance("RP-0001")

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.exception_package.open_task_count == before - 1
        assert result.workflow.exception_package.connection_tasks[0].tasks[0].status == "RESOLVED"

    def test_the_contract_the_replay_started_from_is_not_mutated(self, arkles, monkeypatch):
        """The replayed package is a NEW package; the one it was built from is untouched."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0001-T01")
        chain.advance("RP-0001")

        seen = []
        real = resumption.apply_human_resolution

        def _spy(package, answer):
            seen.append(package)
            return real(package, answer)

        monkeypatch.setattr(resumption, "apply_human_resolution", _spy)

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert len(seen) == 1, "the recorded answer was applied exactly once"
        assert seen[0].connection_tasks[0].tasks[0].status == "OPEN", (
            "the package handed to the applier is still OPEN: the applier returns a new "
            "package rather than recording into its input"
        )
        assert seen[0] is not result.workflow.exception_package
        assert result.workflow.exception_package.connection_tasks[0].tasks[0].status == "RESOLVED"

    def test_every_genuine_answer_type_round_trips_through_the_replay(self, arkles):
        """Each task of the first genuine connection is answered and replayed."""
        store, reconstruction = arkles
        task_ids = [
            task.task_id
            for task in reconstruction.workflow.exception_package.connection_tasks[0].tasks
        ]
        chain = _Ledger(reconstruction.workflow)
        for task_id in task_ids:
            chain.record_answer("RP-0001", task_id)
        chain.advance("RP-0001")

        result = _resume(store, reconstruction, history=chain.snapshots)
        resolved = [
            task.status
            for task in result.workflow.exception_package.connection_tasks[0].tasks
        ]

        assert resolved == ["RESOLVED"] * len(task_ids)

    def test_a_task_left_open_in_history_stays_open(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0001-T02")
        chain.advance("RP-0001")

        result = _resume(store, reconstruction, history=chain.snapshots)
        tasks = result.workflow.exception_package.connection_tasks[0].tasks

        assert tasks[0].status == "OPEN"
        assert tasks[1].status == "RESOLVED"

    def test_no_answer_is_synthesised_for_a_task_history_left_open(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")

        result = _resume(store, reconstruction, history=chain.snapshots)

        assert result.workflow.exception_package.open_task_count == (
            reconstruction.workflow.exception_package.open_task_count
        )
        assert resumption.resolutions_from_item(chain.snapshots[0].items[0]) == ()

    def test_the_task_ownership_seam_runs_on_every_replayed_answer(self, arkles, monkeypatch):
        """A recorded answer naming another connection's task is refused, not applied."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0002-T01")
        chain.advance("RP-0001")
        applied = []
        real = resumption.apply_human_resolution
        monkeypatch.setattr(
            resumption, "apply_human_resolution",
            lambda package, answer: applied.append(answer.task_id) or real(package, answer),
        )

        refusal = _task_refused(store, reconstruction, history=chain.snapshots,
                                code=resumption.RESOLUTION_TASK_FOREIGN_CONNECTION)

        assert "RP-0002-T01" in refusal.detail
        assert applied == [], "the foreign answer was refused before anything applied it"


# ===========================================================================
# K. The resolution-task ownership seam.
# ===========================================================================
class TestKResolutionTaskOwnership:
    def _index(self, arkles):
        _, reconstruction = arkles
        return resumption.task_index_from_exception_package(
            reconstruction.workflow.exception_package
        )

    def _answer(self, task_id, answer_type=ANSWER_APPROVE_REVIEW):
        return exception_resolution.HumanResolution(
            task_id=task_id, task_type="COMPLETE_REVIEW", answer_type=answer_type,
            answer=_ANSWER_FOR[answer_type], evidence="",
        )

    def test_the_index_is_the_connections_own_task_groups(self, arkles):
        index = self._index(arkles)

        assert sorted(index) == ["RP-0001", "RP-0002", "RP-0003"]
        assert index["RP-0001"][0] == "RP-0001-T01"
        assert all(task_id.startswith("RP-0001-") for task_id in index["RP-0001"])

    def test_a_task_of_the_target_connection_is_accepted(self, arkles):
        index = self._index(arkles)

        resumption.validate_resolution_task_ownership(
            package_id="RP-0001", task_index=index,
            resolutions=[self._answer("RP-0001-T01")],
        )

    def test_several_tasks_of_the_target_connection_are_accepted(self, arkles):
        index = self._index(arkles)

        resumption.validate_resolution_task_ownership(
            package_id="RP-0001", task_index=index,
            resolutions=[self._answer("RP-0001-T01"), self._answer("RP-0001-T02")],
        )

    def test_no_resolutions_is_accepted(self, arkles):
        resumption.validate_resolution_task_ownership(
            package_id="RP-0001", task_index=self._index(arkles), resolutions=[],
        )

    def test_a_task_of_another_connection_in_the_same_project_is_refused(self, arkles):
        index = self._index(arkles)

        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=index,
                resolutions=[self._answer("RP-0002-T01")],
            )

        assert caught.value.code == resumption.RESOLUTION_TASK_FOREIGN_CONNECTION
        assert "RP-0002" in caught.value.detail

    def test_a_task_of_no_connection_of_this_project_is_refused(self, arkles):
        """An invented id and another project's id are ONE predicate here.

        The seam consults this project's tasks and nothing else: looking further for a
        task would be the cross-project read the guard exists to prevent.
        """
        index = self._index(arkles)

        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=index,
                resolutions=[self._answer("RP-9999-T01")],
            )

        assert caught.value.code == resumption.RESOLUTION_TASK_NOT_OF_THIS_PROJECT
        assert "RP-9999-T01" in caught.value.detail

    def test_a_task_from_a_store_that_never_held_it_is_refused(self, arkles):
        """The same call, against a project whose connections are the OTHER capture's."""
        foreign_index = {"RP-0001": ("RP-0001-T01",)}

        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=foreign_index,
                resolutions=[self._answer("RP-0001-T02")],
            )

        assert caught.value.code == resumption.RESOLUTION_TASK_NOT_OF_THIS_PROJECT

    def test_the_same_task_named_twice_is_refused(self, arkles):
        """7AJ's own inline guard does not catch this; the seam does."""
        index = self._index(arkles)

        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=index,
                resolutions=[self._answer("RP-0001-T01"), self._answer("RP-0001-T01")],
            )

        assert caught.value.code == resumption.RESOLUTION_TASK_DUPLICATED
        assert "RP-0001-T01" in caught.value.detail

    def test_a_target_that_is_not_a_connection_of_this_project_is_refused(self, arkles):
        index = self._index(arkles)

        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-9999", task_index=index,
                resolutions=[self._answer("RP-9999-T01")],
            )

        assert caught.value.code == resumption.RESOLUTION_TARGET_UNKNOWN

    def test_a_missing_or_non_string_target_is_refused(self, arkles):
        index = self._index(arkles)

        for target in (None, "", 1, True):
            with pytest.raises(resumption.ResolutionTaskRefused) as caught:
                resumption.validate_resolution_task_ownership(
                    package_id=target, task_index=index, resolutions=[],
                )
            assert caught.value.code == resumption.RESOLUTION_TARGET_UNKNOWN

    def test_a_resolution_with_no_task_id_is_refused(self, arkles):
        class _Nameless:
            task_id = None

        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=self._index(arkles),
                resolutions=[_Nameless()],
            )

        assert caught.value.code == resumption.RESOLUTION_TASK_NOT_OF_THIS_PROJECT

    def test_a_refused_request_applies_nothing_at_all(self, arkles, monkeypatch):
        """No partial application: the seam runs before the resolver, and it only reads."""
        _, reconstruction = arkles
        package = reconstruction.workflow.exception_package
        before = [(task.task_id, task.status) for task in package.connection_tasks[0].tasks]
        applied = []
        real_apply = resumption.apply_human_resolution
        monkeypatch.setattr(
            resumption, "apply_human_resolution",
            lambda pkg, answer: applied.append(answer.task_id) or real_apply(pkg, answer),
        )

        with pytest.raises(resumption.ResolutionTaskRefused):
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001",
                task_index=resumption.task_index_from_exception_package(package),
                resolutions=[self._answer("RP-0001-T01"), self._answer("RP-0002-T01")],
            )

        assert applied == []
        assert package is reconstruction.workflow.exception_package
        assert [(task.task_id, task.status) for task in package.connection_tasks[0].tasks] == before

    def test_the_seam_is_exposed_for_a_later_route_to_call_first(self):
        """J47's route calls this BEFORE the resolver; it is public and pure."""
        assert callable(resumption.validate_resolution_task_ownership)
        assert callable(resumption.task_index_from_exception_package)
        assert "validate_resolution_task_ownership" in resumption.__all__

    def test_the_resolver_itself_was_not_modified(self):
        """The seam is an addition beside 7AJ's own guard, never a replacement of it."""
        source = (REPO / "app" / "cad_engine" / "project_workflow.py").read_text()
        assert "CrossProjectResolutionError" in source
        assert "validate_resolution_task_ownership" not in source


# ===========================================================================
# L. The side-effect boundary.
# ===========================================================================
_SIDE_EFFECT_TARGETS = (
    ("app.cad_engine.automation_pipeline", "evaluate_reviewed_connection_for_automation"),
    ("app.cad_engine.project_automation", "evaluate_project_for_automation"),
    ("app.cad_engine.fabrication_output_gate", "evaluate_project_fabrication_output_gate"),
    ("app.cad_engine.drawing_dispatch", "dispatch_fabrication_drawing"),
    ("app.cad_engine.drawing_output_verification", "verify_drawing_artifact"),
    ("app.cad_engine.resolution_rerun", "rerun_connection_after_resolutions"),
)

# The names the module's CODE must never mention. Prose may — the module's docstring
# names every one of them while explaining why they are not called.
_FORBIDDEN_CALLS = (
    "dispatch_fabrication_drawing", "verify_drawing_artifact",
    "rerun_connection_after_resolutions", "evaluate_reviewed_connection_for_automation",
    "evaluate_project_for_automation", "evaluate_project_fabrication_output_gate",
    "analyze_pdf_pages", "retry_pdf_page", "build_fabrication_package", "upload(",
    "download(", "from_(", "storage", "anthropic", "subprocess", "requests.", "httpx",
    "review_store.insert", "review_store.update", "acquire_project_review_claim",
    "release_project_review_claim",
)

# Every import in the module, module-level AND nested, with the names bound. A second
# source of truth, a second codec, a client, a generator or an AI call would all have to
# appear here first — so this pin is the module's whole dependency surface.
_EXPECTED_IMPORTS = frozenset({
    ("__future__", ("annotations",)),
    ("dataclasses", ()),
    ("collections.abc", ("Mapping", "Sequence")),
    ("typing", ("Any",)),
    ("app.cad_engine.connection_review_snapshot",
     ("ReviewSnapshot", "ReviewSnapshotItem", "_state_from_item")),
    ("app.cad_engine.exception_resolution", ("HumanResolution", "apply_human_resolution")),
    ("app.cad_engine.project_workflow",
     ("ProjectConnectionRecord", "ProjectConnectionState", "ProjectWorkflowState",
      "_assemble_state")),
    ("app.engineering_data", ("connection_review_repository",)),
    ("app.production_review.project_workflow_reconstruction",
     ("reconstruct_project_workflow",)),
})

# The two imports that are bound INSIDE a function rather than at module level.
_NESTED_IMPORTS = frozenset({
    ("app.engineering_data", ("connection_review_repository",)),
    ("app.production_review.project_workflow_reconstruction",
     ("reconstruct_project_workflow",)),
})


def _imports(tree):
    """Every import in the tree as (module, names bound), module-level and nested."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add((alias.name, ()))
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            found.add((module, tuple(sorted(alias.name for alias in node.names))))
    return found


class _Recorder:
    """A call recorder installed where a stage lives."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


def _spies(monkeypatch):
    """Instruments every stage the replay must not run. Returns one shared call list.

    The six stages are patched in the modules that OWN them rather than in this module,
    because this module binds none of them — which is itself the property being proved.
    A mutant that bound one and called it would trip these; `TestL` below proves the
    instrumentation can see a call at all by installing it on the one pure function this
    module DOES bind.
    """
    calls = []

    def _record(name, *args, **kwargs):
        calls.append(name)
        return None

    for module_name, attribute in _SIDE_EFFECT_TARGETS:
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), (module_name, attribute)

        def _spy(*args, _name=attribute, **kwargs):
            return _record(_name, *args, **kwargs)

        monkeypatch.setattr(module, attribute, _spy)
    return calls


class TestLNoSideEffects:
    def test_the_modules_code_names_no_generator_verifier_dispatch_or_upload(self):
        code = _code_only(MODULE_PATH)

        for name in _FORBIDDEN_CALLS:
            assert name not in code, f"the module's code mentions {name!r}"

    def test_the_module_imports_exactly_its_own_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))

        assert _imports(tree) == set(_EXPECTED_IMPORTS)

    def test_every_module_level_assignment_is_a_constant(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert isinstance(target, ast.Name), ast.dump(target)
                    assert target.id.isupper() or (
                        target.id.startswith("__") and target.id.endswith("__")
                    ), target.id
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom,
                                   ast.Expr)):
                continue
            else:
                raise AssertionError(f"unexpected module-level {type(node).__name__}")

    def test_the_module_binds_none_of_the_stages_it_must_not_run(self):
        for _, attribute in _SIDE_EFFECT_TARGETS:
            assert not hasattr(resumption, attribute), attribute

    def test_a_full_resume_calls_no_stage(self, arkles, monkeypatch):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0001-T01")
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED)
        chain.advance("RP-0002", decision=AUTO, output_status=GENERATION_FAILED)
        chain.advance("RP-0003", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFICATION_FAILED)

        calls = _spies(monkeypatch)
        result = _resume(store, reconstruction, history=chain.snapshots)

        assert calls == []
        assert result.head_revision == 3

    def test_the_instrumentation_can_see_a_call_it_is_watching_for(self, arkles, monkeypatch):
        """The control: the ONE pure function this module binds IS seen when it runs."""
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.record_answer("RP-0001", "RP-0001-T01")
        chain.advance("RP-0001")

        applied = []
        real = resumption.apply_human_resolution
        monkeypatch.setattr(
            resumption, "apply_human_resolution",
            lambda package, answer: applied.append(answer.task_id) or real(package, answer),
        )

        _resume(store, reconstruction, history=chain.snapshots)

        assert applied == ["RP-0001-T01"], (
            "the replay applies the recorded human answer on the pure contract builder — "
            "and this is the harness seeing a call happen, so the empty lists above are "
            "evidence rather than silence"
        )

    def test_the_persisted_chain_is_read_through_the_persistence_layer_only(self):
        """Two readers, each bound inside the function that reads — never at import time."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        at_module_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                at_module_level.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                at_module_level.add(node.module or "")

        nested = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import):
                    nested.update((alias.name, ()) for alias in inner.names)
                elif isinstance(inner, ast.ImportFrom):
                    nested.add((
                        inner.module or "",
                        tuple(sorted(alias.name for alias in inner.names)),
                    ))

        assert nested == set(_NESTED_IMPORTS)
        assert not any("engineering_data" in module for module in at_module_level), (
            "the persistence layer is bound inside the function that reads; importing it "
            "at module level would open the persistence layer to everything that imports "
            "this module"
        )


# ===========================================================================
# M. The eight mutations — the guards above are shown to be able to fail.
# ===========================================================================
def _mutant(*edits):
    """The module with exact edits applied, executed in isolation.

    Source-level, so what is removed is the property rather than a behaviour faked around
    it, and isolated, so the real module is untouched.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    for old, new in edits:
        assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
        source = source.replace(old, new)
    module = types.ModuleType("j46_mutant")
    # Registered before it runs, because `dataclasses` resolves a field's string
    # annotation through `sys.modules[cls.__module__]` and an unregistered module has no
    # entry to resolve against. Registered under one name for every mutant, so each
    # execution replaces the last.
    sys.modules["j46_mutant"] = module
    exec(compile(source, "<j46-mutant>", "exec"), module.__dict__)
    return module


_STATE_FROM_HISTORY = (
    "    new_connections = tuple(\n"
    "        _state_from_item(by_package[connection.package_id])\n"
    "        for connection in workflow.connections\n"
    "    )\n"
)


def _drive(module, store, reconstruction, chain):
    return module.resume_project_workflow(
        reconstruction.project_id,
        section_matcher=j24a._StubMatcher(),
        repository=store,
        history=chain.snapshots,
    )


class TestMTheGuardsCanFail:
    def test_mutation_a_a_chain_with_a_gap_is_caught(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        chain.advance("RP-0002")
        del chain.rows[0]
        _refused(store, reconstruction, history=chain.snapshots,
                 code=resumption.RESUMPTION_HISTORY_GAP)

        mutant = _mutant(("    expected = tuple(range(1, len(ordered) + 1))\n",
                          "    expected = ordered\n"))

        # The mutated chain check accepts a chain with a hole in it ...
        assert mutant.validate_history([2]) == (2,)
        assert mutant.validate_history([1, 3]) == (1, 3)
        # ... and the gap is STILL refused, by the second guard: revision 2 cannot be
        # replayed onto a workflow at revision 0. The property survives the mutation of
        # the first guard, which is a fact about the module and is reported as one.
        with pytest.raises(mutant.ResumptionRefused) as caught:
            _drive(mutant, store, reconstruction, chain)
        assert caught.value.code == mutant.RESUMPTION_HISTORY_GAP
        assert "cannot follow" in caught.value.detail

    def test_mutation_b_an_expectation_ahead_of_the_chain_is_caught(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")
        _refused(store, reconstruction, history=chain.snapshots, expected_revision=5,
                 code=resumption.RESUMPTION_EXPECTED_REVISION_AHEAD)

        mutant = _mutant(
            ("    if expected_revision > head_revision:\n", "    if False:\n"),
            ("    if expected_revision < head_revision:\n", "    if False:\n"),
        )

        accepted = _drive(mutant, store, reconstruction, chain)
        assert accepted.head_revision == 1
        # ...and the stale half of the same guard, on a two-revision chain.
        chain.advance("RP-0002")
        accepted = mutant.resume_project_workflow(
            reconstruction.project_id, section_matcher=j24a._StubMatcher(),
            repository=store, history=chain.snapshots, expected_revision=1,
        )
        assert accepted.head_revision == 2

    def test_mutation_c_a_divergence_from_the_rows_is_caught(self, arkles):
        left = {key: None for key in resumption.AGREEMENT_KEYS}
        right = dict(left)
        right["decision"] = REVIEW
        assert resumption.compare_agreement(left, right).diverged_key == "decision"

        mutant = _mutant(("        if left_value != right_value:\n", "        if False:\n"))

        assert mutant.compare_agreement(left, right).agrees is True, (
            "the mutant reports agreement between two projections that disagree"
        )

    def test_mutation_d_a_replay_that_ignores_the_rows_is_caught(self, arkles, monkeypatch):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFIED,
                      generated_files=("artifacts/RP-0001.pdf",))
        replayed = _resume(store, reconstruction, history=chain.snapshots)
        assert replayed.workflow.connections[0].decision == AUTO

        mutant = _mutant((_STATE_FROM_HISTORY, "    new_connections = tuple(workflow.connections)\n"))

        # The mutant regenerates the state instead of replaying it: either the guard
        # refuses it, or it returns the revision-0 state and the recorded write is lost.
        try:
            ignored = _drive(mutant, store, reconstruction, chain)
        except mutant.ResumptionRefused as refused:
            assert refused.code == mutant.RESUMPTION_DISAGREES_WITH_HISTORY
        else:
            assert ignored.workflow.connections[0].decision == REVIEW, (
                "the mutant lost the AUTO that was recorded"
            )

    def test_mutation_e_a_recorded_verification_failure_is_caught(self, arkles):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001", decision=AUTO, output_status=GENERATED,
                      verification_status=VERIFICATION_FAILED)
        replayed = _resume(store, reconstruction, history=chain.snapshots)
        assert replayed.workflow.connections[0].verification_status == VERIFICATION_FAILED

        mutant = _mutant((
            _STATE_FROM_HISTORY,
            "    new_connections = tuple(\n"
            "        _state_from_item(by_package[connection.package_id])\n"
            "        if by_package[connection.package_id].verification_status != 'FAILED'\n"
            "        else connection\n"
            "        for connection in workflow.connections\n"
            "    )\n",
        ))

        # The mutant re-verifies: the recorded FAILED is dropped and the earlier state
        # stands, which is exactly the historical outcome being rewritten.
        try:
            ignored = _drive(mutant, store, reconstruction, chain)
        except mutant.ResumptionRefused as refused:
            assert refused.code == mutant.RESUMPTION_DISAGREES_WITH_HISTORY
        else:
            assert ignored.workflow.connections[0].verification_status is None, (
                "the mutant dropped the recorded verification failure"
            )

    def test_mutation_f_a_foreign_task_is_caught(self, arkles):
        index = resumption.task_index_from_exception_package(
            arkles[1].workflow.exception_package
        )
        answer = exception_resolution.HumanResolution(
            task_id="RP-0002-T01", task_type="COMPLETE_REVIEW",
            answer_type=ANSWER_APPROVE_REVIEW, answer=None, evidence="",
        )
        with pytest.raises(resumption.ResolutionTaskRefused):
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=index, resolutions=[answer],
            )

        mutant = _mutant((
            "    foreign = [task_id for task_id in seen if task_id not in own]\n",
            "    foreign = []\n",
        ))

        assert mutant.validate_resolution_task_ownership(
            package_id="RP-0001", task_index=index, resolutions=[answer],
        ) is None, "the mutant applies another connection's task to the target"

    def test_mutation_g_a_duplicated_task_is_caught(self, arkles):
        index = resumption.task_index_from_exception_package(
            arkles[1].workflow.exception_package
        )
        answer = exception_resolution.HumanResolution(
            task_id="RP-0001-T01", task_type="COMPLETE_REVIEW",
            answer_type=ANSWER_APPROVE_REVIEW, answer=None, evidence="",
        )
        with pytest.raises(resumption.ResolutionTaskRefused) as caught:
            resumption.validate_resolution_task_ownership(
                package_id="RP-0001", task_index=index, resolutions=[answer, answer],
            )
        assert caught.value.code == resumption.RESOLUTION_TASK_DUPLICATED

        mutant = _mutant((
            "    duplicates = sorted({task_id for task_id in seen if seen.count(task_id) > 1})\n",
            "    duplicates = []\n",
        ))

        assert mutant.validate_resolution_task_ownership(
            package_id="RP-0001", task_index=index, resolutions=[answer, answer],
        ) is None, "the mutant accepts one question answered twice"

    def test_mutation_h_a_stage_call_inside_the_replay_is_caught(self, arkles, monkeypatch):
        store, reconstruction = arkles
        chain = _Ledger(reconstruction.workflow)
        chain.advance("RP-0001")

        calls = _spies(monkeypatch)
        _resume(store, reconstruction, history=chain.snapshots)
        assert calls == []

        mutant = _mutant((
            "    snapshots = tuple(history) if history is not None else _read_history(project_id)\n",
            "    from app.cad_engine.drawing_dispatch import dispatch_fabrication_drawing\n"
            "    dispatch_fabrication_drawing(None, None, None)\n"
            "    snapshots = tuple(history) if history is not None else _read_history(project_id)\n",
        ))

        _drive(mutant, store, reconstruction, chain)

        assert calls == ["dispatch_fabrication_drawing"], (
            "the instrumentation sees the dispatch the mutant performs — so the empty "
            "list on the real module is a measured silence, not an unwatched one"
        )
