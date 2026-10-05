"""
J77 — PERSISTED CANDIDATE ORIGIN CARRY-FORWARD: the proof file.

WHAT THIS FILE IS

J72 gave a review item's `evidence` payload two more terms — the analysis run whose page
reading produced the candidate, and the candidate's ZERO-BASED position in that page's own
`raw_connections` array. J76 measured what that costs: a revision is never built from the
previous revision but from a FRESH reconstruction of the project's persisted readings, so
every connection is re-emitted by every revision from the collection that stands today. An
untouched connection whose page has been read again would therefore be re-addressed at the
reading that stands for the page NOW, which may not be the reading the revision being
extended recorded.

J77 is the carry-forward: `build_review_snapshot` takes the previous revision's items and
either confirms each recorded origin against what this reconstruction derives, acquires one
where the connection is genuinely being processed, or REFUSES. This file is its proof.

WHAT IS REAL, AND WHAT IS NOT

Real: the two modules J77 changes (`app/cad_engine/connection_review_snapshot.py` and
`app/engineering_data/connection_review_repository.py`), J72's own `origin_of`/`with_origin`,
J74's own `replay_revision`/`resume_project_workflow`, J24A's genuine reconstruction over the
committed Selby readings, and the deployed route modules this file only reads.

Not real, and named as such where it appears: the synthetic three-page fixture the
deterministic cases are stated over (a plain mapping in the shape `intake_page_extractions`
already accepts, as J72's own file uses), and the ROUTE ordering assertions, which are
textual rather than executed — no request is served by this file.

WHAT THIS FILE DOES NOT CLAIM

  * It does not claim origin equality IS candidate equivalence. Two equal origins name the
    same immutable capture row and the same position in its array, so equality IMPLIES the
    same candidate; the converse is false, and page 26 proves it — its two attempts state
    byte-identical candidates, so an inequality there is two names for what a reviewer would
    call one drawing. The refusal is deliberately conservative and this file measures that
    over-refusal rather than hiding it.
  * It does not solve the page-26 over-refusal. It records it as the accepted cost of a rule
    that never substitutes one reading for another.
  * It does not settle any engineering question, does not wire R3, and does not backfill the
    live project's revision 0. The live project is READ, in the last section, and nothing here
    writes to it.
  * It does not claim a connection identity beyond the positional `RP-####` the system
    already has. It invents none.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
import types
from dataclasses import replace
from typing import Any

import pytest

from app.cad_engine import candidate_origin_address as coa
from app.cad_engine import connection_review_snapshot as model
from app.cad_engine.candidate_origin_address import CandidateOriginRefused
from app.cad_engine.connection_review_snapshot import (
    ReviewSnapshotItem,
    SnapshotRefused,
    build_review_snapshot,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.project_workflow import ProjectConnectionState, start_project_workflow
from app.production_review.project_workflow_resumption import (
    AGREEMENT_KEYS,
    replay_revision,
    resume_project_workflow,
)

from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j22_connection_review_persistence as j22
from tests import test_real_world_j24a_revision_zero_producer as j24a

REPO = j13.REPO
APP = REPO / "app"
SNAPSHOT_PATH = APP / "cad_engine" / "connection_review_snapshot.py"
STORE_PATH = APP / "engineering_data" / "connection_review_repository.py"
RESOLUTION_PATH = APP / "production_review_resolution.py"
OPENING_PATH = APP / "production_review_opening.py"

NO_EVIDENCE = {"steel_members": (), "connections": ()}

#: The two terms J72 records, quoted from the module that owns them rather than restated.
RUN_KEY = coa.CANDIDATE_ORIGIN_RUN_KEY
INDEX_KEY = coa.CANDIDATE_ORIGIN_INDEX_KEY

#: The one refusal J77 adds.
DIVERGED = model.SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED

PROJECT = "PROJ-7J77"


# ===========================================================================
# The synthetic three-page fixture — the deterministic cases are stated over it.
# Page 1 contributes two candidates, page 2 one, page 3 three, so the flattened
# submission order (RP-0001..RP-0006) and every page's own local index disagree.
# ===========================================================================
def _page(number: int, count: int) -> dict[str, Any]:
    return {
        "page_number": number,
        "drawing_number": "001",
        "drawing_title": "a set",
        "revision": "A",
        "parse_failed": False,
        "raw_members": [],
        "raw_connections": [
            {
                "detail_reference": f"D{number}-{index}",
                "grid_reference": None,
                "connects_members": [],
                "connection_type": "welded",
                "bolts": [],
                "plates": [],
                "welds": [],
                "confidence": "high",
            }
            for index in range(count)
        ],
    }


def _pages() -> list[dict[str, Any]]:
    return [_page(1, 2), _page(2, 1), _page(3, 3)]


class _NoMatcher:
    """The caller's section matcher, answering nothing — so nothing here looks as though
    geometry had been involved (the matcher is carried and never consulted at revision 0)."""

    def match(self, raw_name: Any) -> None:
        return None


def _workflow(pages=None, *, run_ids=("run-A", "run-A", "run-B")) -> Any:
    pages = _pages() if pages is None else pages
    intake = intake_page_extractions(
        pages,
        project_id=PROJECT,
        source_drawing_id="drawing-7j77",
        drawing_set_page_count=len(pages),
        page_analysis_run_ids=run_ids,
    )
    return start_project_workflow(
        intake.collection, intake=intake, member_rows={}, member_placements={},
        section_matcher=_NoMatcher(),
    )


def _snapshot(workflow, *, previous_revision=None, prior_items=None):
    return build_review_snapshot(
        workflow, project_id=PROJECT, evidence_rows=NO_EVIDENCE,
        previous_revision=previous_revision, prior_items=prior_items,
    )


def _by_package(snapshot) -> dict[str, ReviewSnapshotItem]:
    return {item.review_package_id: item for item in snapshot.items}


def _advanced(workflow, snapshot, package_id: str):
    """The genuine NEXT revision: one connection processed, the rest untouched.

    Built by advancing exactly one item's `last_processed_revision` and replaying it
    through J74's own `replay_revision`, which is the only way a revision above 0 exists
    in this system. Nothing about the collection changes, which is the point.
    """
    items = tuple(
        replace(item, last_processed_revision=snapshot.review_revision + 1)
        if item.review_package_id == package_id
        else item
        for item in snapshot.items
    )
    return replay_revision(
        workflow, snapshot=replace(snapshot, review_revision=snapshot.review_revision + 1,
                                   items=items),
    )


def _origin_of(item: ReviewSnapshotItem):
    return coa.origin_of(item.evidence)


def _stated(item: ReviewSnapshotItem) -> bool:
    """Whether the item's payload states a candidate origin AT ALL — the distinction J72
    keeps between the two keys being ABSENT and being null."""
    return bool(set(coa.ORIGIN_KEYS) & set(item.evidence))


# ===========================================================================
# 1. REVISION 0 IS UNCHANGED (D1, D15)
# ===========================================================================
class TestRevisionZeroIsUnchanged:
    def test_d1_the_revision_zero_path_is_byte_identical_with_and_without_the_argument(self):
        """`prior_items` defaults to None, and None IS the revision-0 path: the call that
        existed before J77 and the call that names no prior produce the same snapshot."""
        workflow = _workflow()

        before = _snapshot(workflow)
        explicit_none = _snapshot(workflow, prior_items=None)

        assert before == explicit_none
        assert [dict(item.evidence) for item in before.items] == [
            dict(item.evidence) for item in explicit_none.items
        ]

    def test_d1_revision_zero_records_the_derived_origin_exactly_as_j72_did(self):
        """Every connection's origin is the one its own page's reading states — the page's
        local index, not the submission index, and the page's own run."""
        snapshot = _snapshot(_workflow())

        assert [
            (item.review_package_id, item.evidence[RUN_KEY], item.evidence[INDEX_KEY])
            for item in snapshot.items
        ] == [
            ("RP-0001", "run-A", 0),
            ("RP-0002", "run-A", 1),
            ("RP-0003", "run-A", 0),
            ("RP-0004", "run-B", 0),
            ("RP-0005", "run-B", 1),
            ("RP-0006", "run-B", 2),
        ]

    def test_d15_a_candidate_the_producer_cannot_address_states_nothing(self):
        """The live revision-0 shape: with no run stated, the two keys are ABSENT — never
        null, and never filled in from the reading that stands for the page."""
        workflow = _workflow([_page(1, 2)], run_ids=(None,))
        snapshot = _snapshot(workflow)

        for item in snapshot.items:
            assert sorted(item.evidence) == [
                "detail_reference", "drawing_number", "grid_reference",
                "source_drawing_id", "source_page",
            ], item.review_package_id
            assert RUN_KEY not in item.evidence and INDEX_KEY not in item.evidence
        assert coa.origin_of(snapshot.items[0].evidence) is None


# ===========================================================================
# 2. A RECORDED ORIGIN IS CARRIED OR THE SNAPSHOT IS REFUSED (D2, D3, D4, D5, D12)
# ===========================================================================
class TestARecordedOriginIsCarriedOrRefused:
    def _pair(self, *, prior_run=None, prior_index=None):
        """A revision-0 snapshot, and the genuine revision-1 workflow that follows it.

        `prior_run`/`prior_index` restate ONE item's recorded origin — the shape a
        previous revision would have recorded — without touching the reconstruction, so a
        divergence is stated by the PREVIOUS revision rather than by this one.
        """
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")

        prior = _by_package(base)
        if prior_run is not None or prior_index is not None:
            target = prior["RP-0001"]
            evidence = dict(target.evidence)
            evidence[RUN_KEY] = prior_run if prior_run is not None else evidence[RUN_KEY]
            evidence[INDEX_KEY] = (
                prior_index if prior_index is not None else evidence[INDEX_KEY]
            )
            prior["RP-0001"] = replace(target, evidence=evidence)
        return workflow_one, prior

    def test_d2_a_prior_origin_that_agrees_is_carried_verbatim(self):
        """The touched connection's origin and all five untouched ones are carried
        byte-for-byte — the recorded address is what this revision answers."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")

        recorded = _snapshot(workflow_one, previous_revision=0, prior_items=_by_package(base))

        assert [dict(item.evidence) for item in recorded.items] == [
            dict(item.evidence) for item in base.items
        ]
        assert [item.last_processed_revision for item in recorded.items] == [
            1, None, None, None, None, None,
        ]

    def test_d3_a_different_analysis_run_is_refused(self):
        """RUN-B where this reconstruction derives RUN-A: the two may name different
        candidates even when their contents agree, so the snapshot is refused."""
        workflow_one, prior = self._pair(prior_run="RUN-B")

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert refused.value.code == DIVERGED
        statement = refused.value.statement
        assert "RP-0001" in statement
        assert "RUN-B" in statement and "run-A" in statement

    def test_d4_a_different_candidate_index_is_refused(self):
        workflow_one, prior = self._pair(prior_index=5)

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert refused.value.code == DIVERGED
        assert "index 5" in refused.value.statement
        assert "index 0" in refused.value.statement

    def test_d5_both_terms_differing_is_the_same_refusal(self):
        workflow_one, prior = self._pair(prior_run="RUN-B", prior_index=5)

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert refused.value.code == DIVERGED

    def test_the_refusal_names_its_connection_and_its_two_addresses_and_no_more(self):
        """It reports the package id, the recorded origin and the derived one. It does not
        report another connection's id, and it does not echo the payload beside them."""
        workflow_one, prior = self._pair(prior_run="RUN-B", prior_index=5)

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        statement = refused.value.statement
        for other in ("RP-0002", "RP-0003", "RP-0004", "RP-0005", "RP-0006"):
            assert other not in statement, other
        for echoed in ("D1-0", "detail_reference", "welded", "confidence"):
            assert echoed not in statement, echoed

    def test_d12_a_half_stated_prior_origin_propagates_j72s_refusal_unchanged(self):
        """A payload stating ONE term is refused by the rule that owns that vocabulary
        (`origin_of`), never completed here and never quietly read as 'no prior'."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")

        for missing in (RUN_KEY, INDEX_KEY):
            prior = _by_package(base)
            stripped = {key: value for key, value in prior["RP-0001"].evidence.items()
                        if key != missing}
            prior["RP-0001"] = replace(prior["RP-0001"], evidence=stripped)

            with pytest.raises(CandidateOriginRefused) as refused:
                _snapshot(workflow_one, previous_revision=0, prior_items=prior)

            assert refused.value.code == (
                coa.ORIGIN_RUN_ABSENT if missing == RUN_KEY else coa.ORIGIN_INDEX_INVALID
            )

    def test_a_prior_payload_stating_null_for_both_terms_reads_as_no_origin_at_all(self):
        """The other half of D12, and it is inherited rather than chosen: `origin_of`
        answers None for a payload that states neither term AS A RECORDED TERM, so a null
        pair and an absent pair are the same answer — 'the producer could not address this
        candidate'. The consequence is stated rather than hidden: an untouched connection
        whose previous revision wrote a null pair keeps NO origin, and the keys are ABSENT
        rather than null when it is carried, which is J72's normal form for both."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = _by_package(base)
        prior["RP-0001"] = replace(
            prior["RP-0001"],
            evidence={**prior["RP-0001"].evidence, RUN_KEY: None, INDEX_KEY: None},
        )
        prior["RP-0002"] = replace(
            prior["RP-0002"],
            evidence={**prior["RP-0002"].evidence, RUN_KEY: None, INDEX_KEY: None},
        )

        recorded = _snapshot(workflow_one, previous_revision=0, prior_items=prior)
        by_id = _by_package(recorded)

        assert coa.origin_of(by_id["RP-0001"].evidence) == coa.CandidateOrigin("run-A", 0)
        assert not _stated(by_id["RP-0002"])
        assert by_id["RP-0002"].evidence == {
            key: value for key, value in prior["RP-0002"].evidence.items()
            if key not in coa.ORIGIN_KEYS
        }

    def test_a_prior_origin_missing_one_key_is_refused_by_j72_and_never_completed(self):
        """And the keys being present-with-a-null in ONE place only is *not* the same
        answer — but it is not J77's question either. What J77 guarantees is that it never
        repairs a partly-stated address: the refusal, or the J72 answer, propagates
        unchanged, and no branch here fills a missing term in from the reconstruction."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = _by_package(base)
        prior["RP-0001"] = replace(
            prior["RP-0001"],
            evidence={**prior["RP-0001"].evidence, RUN_KEY: None},
        )

        # An index is recorded, a run is null: the address is half-stated, and J72 reads
        # the null run as a run that was NOT recorded rather than completing it from the
        # reconstruction. The refusal reaches the caller unchanged.
        with pytest.raises(CandidateOriginRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert refused.value.code == coa.ORIGIN_RUN_ABSENT

        # And the same payload handed straight to J72 answers identically — J77 adds no
        # rule of its own about half-stated addresses, it only declines to repair them.
        with pytest.raises(CandidateOriginRefused) as direct:
            coa.origin_of({"detail_reference": "D1-0", RUN_KEY: None, INDEX_KEY: 0})
        assert direct.value.code == refused.value.code


# ===========================================================================
# 3. THE TWO REVISIONS MUST NAME THE SAME PACKAGES (D8, D9)
# ===========================================================================
class TestTheCoverMustMatch:
    def _base_and_workflow(self):
        base = _snapshot(_workflow())
        return base, _advanced(_workflow(), base, "RP-0001")

    def test_d8_a_package_the_previous_revision_recorded_and_this_one_does_not(self):
        """A package that has DISAPPEARED is a lifecycle change this layer does not decide,
        and it invents no deletion semantics to cover one."""
        base, workflow_one = self._base_and_workflow()
        prior = _by_package(base)
        prior["RP-0009"] = prior["RP-0002"]

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert refused.value.code == DIVERGED
        assert "RP-0009" in refused.value.statement

    def test_d9_a_package_this_workflow_has_and_the_previous_revision_did_not(self):
        base, workflow_one = self._base_and_workflow()
        prior = _by_package(base)
        del prior["RP-0006"]

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert refused.value.code == DIVERGED
        assert "RP-0006" in refused.value.statement

    def test_the_cover_is_checked_before_a_single_item_is_built(self):
        """A prior that names a package this workflow does not have is refused for the
        COVER, and the statement says so rather than naming a candidate address."""
        base, workflow_one = self._base_and_workflow()
        prior = _by_package(base)
        prior["RP-0009"] = prior["RP-0002"]

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert "do not describe the same package ids" in refused.value.statement

    def test_a_prior_item_that_is_not_an_item_is_refused_by_type(self):
        """A previous revision is handed over as it was recorded, never as something that
        merely resembles an item."""
        base, workflow_one = self._base_and_workflow()
        prior = _by_package(base)
        prior["RP-0002"] = dict(base.items[1].evidence)

        with pytest.raises(TypeError) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert "RP-0002" in str(refused.value)

    def test_the_revision_arithmetic_is_decided_before_the_cover_is(self):
        """A caller whose chain moved under it still gets the GAP refusal it already
        handles: a workflow that is not the successor of the named prior is a gap, not a
        divergence, and the two are never confused for one another."""
        base = _snapshot(_workflow())
        prior = _by_package(base)
        prior["RP-0009"] = prior["RP-0002"]

        with pytest.raises(SnapshotRefused) as refused:
            # The revision-0 workflow against a chain at revision 0: expected 1, got 0.
            _snapshot(_workflow(), previous_revision=0, prior_items=prior)

        assert refused.value.code == model.SNAPSHOT_REFUSED_REVISION_GAP


# ===========================================================================
# 4. AN ABSENT ORIGIN IS ACQUIRED ONLY BY PROCESSING THE CONNECTION (D6, D7)
#
# The prior here is the payload shape J72 documents for a revision recorded BEFORE it —
# or one whose producer could not address the candidate — produced by J72's OWN
# `with_origin(evidence, None)` rather than by editing a dict by hand. This is the case
# `prior_items` must survive without inventing an address for it.
# ===========================================================================
class TestAnAbsentOriginIsAcquiredOnlyByProcessing:
    def _prior_without_origins(self, base):
        return {
            item.review_package_id: replace(
                item, evidence=coa.with_origin(item.evidence, None)
            )
            for item in base.items
        }

    def test_the_stripped_prior_really_states_nothing(self):
        base = _snapshot(_workflow())
        prior = self._prior_without_origins(base)

        assert all(not _stated(item) for item in prior.values())
        assert all(coa.origin_of(item.evidence) is None for item in prior.values())

    def test_d6_an_untouched_connection_with_no_prior_origin_states_nothing(self):
        """Manufacturing an address here would state that a revision which recorded none
        had recorded one. The keys stay ABSENT, exactly as J72 leaves them."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = self._prior_without_origins(base)

        recorded = _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        for item in recorded.items:
            if item.review_package_id == "RP-0001":
                continue
            assert not _stated(item), item.review_package_id
            assert item.last_processed_revision is None

    def test_d7_the_connection_this_revision_processes_acquires_its_origin(self):
        """`last_processed_revision == revision` is the one point at which an origin is
        acquired, because it is the one point at which the connection is evaluated."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = self._prior_without_origins(base)

        recorded = _snapshot(workflow_one, previous_revision=0, prior_items=prior)
        acquired = _by_package(recorded)["RP-0001"]

        assert _stated(acquired)
        assert coa.origin_of(acquired.evidence) == coa.CandidateOrigin(
            analysis_run_id="run-A", candidate_index=0
        )
        assert acquired.last_processed_revision == 1

    def test_the_acquired_origin_is_the_derived_one_and_not_the_submission_position(self):
        """Page 3's first candidate is RP-0004 and its page-local index is 0 — the address
        that gets acquired is the page's own, never the flattened position."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0004")
        prior = self._prior_without_origins(base)

        recorded = _snapshot(workflow_one, previous_revision=0, prior_items=prior)

        assert coa.origin_of(_by_package(recorded)["RP-0004"].evidence) == \
            coa.CandidateOrigin(analysis_run_id="run-B", candidate_index=0)


# ===========================================================================
# 5. THE GENUINE SELBY FIXTURES (D10, D11)
#
# Real readings, real reconstruction (J24A's, unmodified), real replay (J74's). The only
# thing this file supplies is the previous revision's ITEMS, which is exactly the argument
# J77 adds.
# ===========================================================================
@pytest.fixture(scope="module")
def selby_no_recovery():
    """The documented read: seven windows, one drawing, 32 pages."""
    return j24a._reconstruct(j24a._selby_store(), j24a.SELBY_PROJECT)


@pytest.fixture(scope="module")
def selby_with_recovery():
    """The page-26 re-read: the SAME document with page 26 read a second time, so the
    recovery's run — not the window's — is the reading that stands for page 26."""
    return j24a._reconstruct(
        j24a._selby_store(with_recovery=True), j24a.SELBY_PROJECT
    )


def _selby_snapshot(workflow, *, previous_revision=None, prior_items=None):
    return build_review_snapshot(
        workflow, project_id=j24a.SELBY_PROJECT, evidence_rows=NO_EVIDENCE,
        previous_revision=previous_revision, prior_items=prior_items,
    )


def _on_page(snapshot, page: int) -> list[ReviewSnapshotItem]:
    return [item for item in snapshot.items if item.evidence["source_page"] == page]


class TestTheGenuineSelbyFixtures:
    def test_d10_page_13s_recorded_indices_survive_the_next_revision(self, selby_no_recovery):
        """Page 13 of the genuine reading holds three candidates: the first states
        `VIEW A-A`, the second and third state neither a detail nor a grid reference — so
        their ADDRESS is the only thing that tells them apart. Their page-local indices are
        0, 1 and 2; their submission positions are 21, 22 and 23. Carry-forward preserves
        the address the previous revision recorded, so index 1 stays 1 and index 2 stays 2.
        """
        base = _selby_snapshot(selby_no_recovery.workflow)
        page_13 = [(item.review_package_id, item.evidence[INDEX_KEY])
                   for item in _on_page(base, 13)]

        assert page_13 == [("RP-0021", 0), ("RP-0022", 1), ("RP-0023", 2)]
        assert [item.evidence["detail_reference"] for item in _on_page(base, 13)] == [
            "VIEW A-A", None, None,
        ]
        # The address is the page's own, not the submission position.
        assert [int(item.review_package_id.split("-")[1]) - 1 for item in _on_page(base, 13)] \
            == [20, 21, 22]

        workflow_one = _advanced(
            selby_no_recovery.workflow, base, base.items[0].review_package_id
        )
        carried = _selby_snapshot(
            workflow_one, previous_revision=0, prior_items=_by_package(base)
        )

        assert [(item.review_package_id, item.evidence[INDEX_KEY])
                for item in _on_page(carried, 13)] == page_13
        assert [dict(item.evidence) for item in carried.items] == [
            dict(item.evidence) for item in base.items
        ]

    def test_d11_page_26s_two_attempts_are_a_refusal(self, selby_no_recovery,
                                                    selby_with_recovery):
        """Page 26 failed when its window was read and was read again on its own. The
        previous revision recorded the WINDOW's run; this reconstruction derives the
        RECOVERY's — so the two name different candidates, and the snapshot is refused.

        This is the deliberately conservative case: the two attempts state byte-identical
        candidates below, so nothing about the candidate can tell them apart. What the rule
        does here is REFUSE, and the refusal is the accepted cost of never substituting one
        reading for another.
        """
        base = _selby_snapshot(selby_no_recovery.workflow)
        recorded = _on_page(base, 26)
        assert [(item.review_package_id, item.evidence[RUN_KEY], item.evidence[INDEX_KEY])
                for item in recorded] == [("RP-0041", "run-6", 0)]

        workflow_one = _advanced(
            selby_with_recovery.workflow, base, base.items[0].review_package_id
        )
        with pytest.raises(SnapshotRefused) as refused:
            _selby_snapshot(
                workflow_one, previous_revision=0, prior_items=_by_package(base)
            )

        assert refused.value.code == DIVERGED
        statement = refused.value.statement
        assert "RP-0041" in statement
        assert "run-6" in statement and "run-recovery" in statement

    def test_the_page_26_attempts_state_byte_identical_candidates(self):
        """Why the refusal is an OVER-refusal, measured rather than asserted: the recovery
        adds no candidate and changes none, so a reviewer would call the two one drawing.
        The rule cannot know that, and does not guess it."""
        window = [p for p in j24a._capture_rows_for(j24a.SELBY_WINDOW_FILES[5])
                  if p["page_number"] == 26][0]
        recovery = j24a._selby_recovery()

        assert window["raw_connections"] == recovery["raw_connections"]
        assert len(window["raw_connections"]) == 1

    def test_a_re_read_that_leaves_the_reconstruction_alone_is_carried(self,
                                                                      selby_no_recovery):
        """The other side of D11: a revision-1 workflow built over the SAME reading carries
        every recorded origin, page 26 included — the refusal is about a DIVERGENCE, not
        about a second reading existing."""
        base = _selby_snapshot(selby_no_recovery.workflow)
        workflow_one = _advanced(
            selby_no_recovery.workflow, base, base.items[0].review_package_id
        )

        carried = _selby_snapshot(
            workflow_one, previous_revision=0, prior_items=_by_package(base)
        )

        assert [dict(item.evidence) for item in carried.items] == [
            dict(item.evidence) for item in base.items
        ]
        assert len(carried.items) == 52


# ===========================================================================
# 6. EXISTING CALLERS ARE UNAFFECTED (D13)
# ===========================================================================
class TestExistingCallersAreUnaffected:
    def test_d13_the_argument_is_optional_and_keyword_only(self):
        parameter = inspect.signature(build_review_snapshot).parameters["prior_items"]
        assert parameter.default is None
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert list(inspect.signature(build_review_snapshot).parameters) == [
            "workflow", "project_id", "evidence_rows", "evidence_run_ids",
            "previous_revision", "prior_items",
        ], "the argument is appended, so every existing positional call is unaffected"

    def test_d13_the_signature_this_milestone_did_not_change_is_unchanged(self):
        """`record_project_review`'s parameter list is pinned by J22, and J77 adds no
        argument to it: the previous revision is read from where it always was."""
        assert list(inspect.signature(j22.store.record_project_review).parameters) == [
            "binding", "workflow", "evidence_rows", "evidence_run_ids", "client",
        ]

    def test_d13_the_only_production_caller_that_names_the_argument_is_the_store(self):
        """Every other construction of a snapshot is unchanged because it simply does not
        pass the argument — which is what 'optional' has to mean in practice."""
        callers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "build_review_snapshot(" in path.read_text(encoding="utf-8")
        )
        assert callers == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/engineering_data/connection_review_repository.py",
        ], callers
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "prior_items" in path.read_text(encoding="utf-8")
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/engineering_data/connection_review_repository.py",
        ], naming


class TestTheStoreReadsThePriorRevisionOnce:
    """The wiring: `record_project_review` reads the head, reads THAT revision's items
    once, and hands them to the builder. No second head read, no other revision, no
    substitute for the items."""

    def _store_spies(self, monkeypatch, *, head, prior):
        import app.engineering_data.connection_review_repository as store

        reads: list[tuple] = []
        built: list[dict] = []
        real_build = store.build_review_snapshot

        def latest(project_id, *, client=None):
            reads.append(("latest", project_id))
            return head

        def load(project_id, revision, *, client=None):
            reads.append(("load", project_id, revision))
            return prior(project_id, revision)

        def build(workflow, **kwargs):
            built.append(kwargs)
            return real_build(workflow, **kwargs)

        monkeypatch.setattr(store, "latest_recorded_revision", latest)
        monkeypatch.setattr(store, "load_review_snapshot", load)
        monkeypatch.setattr(store, "build_review_snapshot", build)
        monkeypatch.setattr(
            store, "persist_review_snapshot",
            lambda snapshot, *, client=None: snapshot,
        )
        return reads, built

    def test_the_prior_revision_is_read_once_from_the_same_revision_the_builder_expects(
        self, monkeypatch
    ):
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        reads, built = self._store_spies(
            monkeypatch, head=0, prior=lambda project, revision: (
                base if revision == 0 else None
            ),
        )

        j22.store.record_project_review(
            j22._binding(PROJECT), workflow_one, evidence_rows=NO_EVIDENCE,
            client=j22._FakeStore(projects=(PROJECT,)),
        )

        assert reads == [("latest", PROJECT), ("load", PROJECT, 0)]
        assert built[0]["previous_revision"] == 0
        assert built[0]["prior_items"] == _by_package(base)

    def test_an_unreadable_prior_revision_is_a_refusal_and_never_a_revision_zero_build(
        self, monkeypatch
    ):
        """A snapshot built as though this were revision 0 would re-address every untouched
        connection. The store refuses instead: the chain says the revision exists."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        _reads, built = self._store_spies(
            monkeypatch, head=3, prior=lambda project, revision: None
        )

        with pytest.raises(SnapshotRefused) as refused:
            j22.store.record_project_review(
                j22._binding(PROJECT), workflow_one, evidence_rows=NO_EVIDENCE,
                client=j22._FakeStore(projects=(PROJECT,)),
            )

        assert refused.value.code == model.SNAPSHOT_REFUSED_UNREPRESENTABLE
        assert built == [], "the builder is never reached with an unreadable prior"
        assert "could not be read back" in refused.value.statement

    def test_a_project_with_no_recorded_revision_still_takes_the_revision_zero_path(
        self, monkeypatch
    ):
        workflow = _workflow()
        reads, built = self._store_spies(
            monkeypatch, head=None, prior=lambda project, revision: None
        )

        j22.store.record_project_review(
            j22._binding(PROJECT), workflow, evidence_rows=NO_EVIDENCE,
            client=j22._FakeStore(projects=(PROJECT,)),
        )

        assert reads == [("latest", PROJECT)]
        assert built[0]["previous_revision"] is None
        assert built[0]["prior_items"] is None


# ===========================================================================
# 7. NOTHING SILENTLY SUBSTITUTES THE DERIVED ORIGIN (D14)
#
# A refusal is invisible to a test that only measures successes, so the rule is proved by
# mutating it: if the carry were skipped, or the untouched case acquired an origin, the
# deterministic tests above must go red. These controls make that true rather than assumed.
# ===========================================================================
def _mutant(old: str, new: str, *, name: str = "j77_mutant") -> types.ModuleType:
    source = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(SNAPSHOT_PATH)
    sys.modules[name] = module
    try:
        exec(compile(source.replace(old, new), "<j77-mutant>", "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    # The mutant differs from the real module in BEHAVIOUR only, and the identity of the
    # data model is part of what is not mutated: rebinding the types back to the real ones
    # keeps `isinstance` meaning what it means, so the mutant cannot pass or fail a test on
    # the strength of being a second copy of a class.
    for symbol in ("ReviewSnapshot", "ReviewSnapshotItem"):
        if hasattr(model, symbol):
            setattr(module, symbol, getattr(model, symbol))
    return module


#: The carry skipped entirely: the derived origin is recorded over whatever the previous
#: revision recorded, which is the substitution J77 exists to prevent.
_M_DERIVED_PREFERRED = (
    "        origin = candidate.origin\n"
    "        if prior_items is not None:\n",
    "        origin = candidate.origin\n"
    "        if False and prior_items is not None:\n",
)

#: The untouched case acquiring an origin: `touch` is ignored, so a revision that recorded
#: nothing for a connection manufactures an address for it.
_M_UNTOUCHED_ACQUIRES = (
    "    if prior_origin is None:\n"
    "        return derived_origin if touched else None\n",
    "    if prior_origin is None:\n"
    "        return derived_origin\n",
)

#: The cover check removed: a package appearing or disappearing is recorded against a
#: different project picture instead of being refused.
_M_COVER_UNCHECKED = (
    "        if appeared or disappeared:\n",
    "        if False and (appeared or disappeared):\n",
)


class TestNothingSilentlySubstitutes:
    def test_d14_the_carry_is_the_only_thing_that_decides_a_recorded_origin(self):
        """Stated over the source: one carry helper, one call to it, and it is what the
        loop assigns when a prior revision is in hand."""
        source = SNAPSHOT_PATH.read_text(encoding="utf-8")
        calls = '= _carried_origin(\n'
        assert source.count(calls) == 1, "one call, and it is the assignment"
        assert source.index("        origin = candidate.origin\n") \
            < source.index("        if prior_items is not None:\n") \
            < source.index(calls)

    def test_d14_a_divergence_that_the_rule_refuses_is_silently_RECORDED_under_the_mutation(
        self, selby_no_recovery, selby_with_recovery
    ):
        """The load-bearing control for D11 and for D3/D4/D5. Page 26's recorded run is
        superseded; the real rule refuses, and the mutant — which never consults the
        previous revision — records the superseding run instead."""
        real_base = _selby_snapshot(selby_no_recovery.workflow)
        prior = _by_package(real_base)

        workflow_one = _advanced(
            selby_with_recovery.workflow, real_base, real_base.items[0].review_package_id
        )
        with pytest.raises(SnapshotRefused) as refused:
            _selby_snapshot(workflow_one, previous_revision=0, prior_items=prior)
        assert refused.value.code == DIVERGED

        module = _mutant(*_M_DERIVED_PREFERRED)
        recorded = module.build_review_snapshot(
            workflow_one, project_id=j24a.SELBY_PROJECT, evidence_rows=NO_EVIDENCE,
            previous_revision=0, prior_items=prior,
        )
        page_26 = [item for item in recorded.items if item.evidence["source_page"] == 26]

        assert [(item.review_package_id, item.evidence[RUN_KEY]) for item in page_26] == [
            ("RP-0041", "run-recovery")
        ], "the mutant records the reading that stands today"
        # And the page the re-read did NOT touch is recorded at run-recovery too, under the
        # mutant, only where it was read by that run — nothing else moves.
        assert prior["RP-0041"].evidence[RUN_KEY] == "run-6"

    def test_d14_an_untouched_connection_states_nothing_under_the_real_rule(self):
        """The load-bearing control for D6 and D15."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = {
            item.review_package_id: replace(
                item, evidence=coa.with_origin(item.evidence, None)
            )
            for item in base.items
        }

        real = _snapshot(workflow_one, previous_revision=0, prior_items=prior)
        assert all(
            not _stated(item) for item in real.items if item.review_package_id != "RP-0001"
        )

        module = _mutant(*_M_UNTOUCHED_ACQUIRES, name="j77_mutant_acquire")
        mutated = module.build_review_snapshot(
            workflow_one, project_id=PROJECT, evidence_rows=NO_EVIDENCE,
            previous_revision=0, prior_items=prior,
        )
        derived = {item.review_package_id: coa.origin_of(item.evidence)
                   for item in _snapshot(_workflow()).items}
        assert all(
            _stated(item) and coa.origin_of(item.evidence) == derived[item.review_package_id]
            for item in mutated.items if item.review_package_id != "RP-0001"
        ), "the mutant manufactures an address for every connection it did not evaluate"

    def test_d14_the_cover_check_is_load_bearing(self):
        """The load-bearing control for D8: a package the previous revision recorded and
        this workflow does not have is REFUSED by the real rule, and silently DROPPED once
        the cover check is gone — which is the deletion semantics this layer refuses to
        invent. (Removing the check in the D9 direction instead fails loudly, because the
        lookup below it refuses to read a missing entry as 'no prior': the guard cannot be
        smuggled past by deleting one `raise`.)"""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = _by_package(base)
        prior["RP-0009"] = prior["RP-0002"]

        with pytest.raises(SnapshotRefused) as refused:
            _snapshot(workflow_one, previous_revision=0, prior_items=prior)
        assert refused.value.code == DIVERGED

        module = _mutant(*_M_COVER_UNCHECKED, name="j77_mutant_cover")
        recorded = module.build_review_snapshot(
            workflow_one, project_id=PROJECT, evidence_rows=NO_EVIDENCE,
            previous_revision=0, prior_items=prior,
        )

        assert "RP-0009" not in _by_package(recorded), "the mutant drops a recorded package"
        assert sorted(_by_package(recorded)) == [
            "RP-0001", "RP-0002", "RP-0003", "RP-0004", "RP-0005", "RP-0006",
        ]

    def test_the_cover_check_cannot_be_removed_by_deleting_the_raise(self):
        """The D9 direction: with the check gone, the missing entry is not read as 'no
        prior' — it is a KeyError, so no revision can be recorded against a package the
        previous revision never named."""
        base = _snapshot(_workflow())
        workflow_one = _advanced(_workflow(), base, "RP-0001")
        prior = _by_package(base)
        del prior["RP-0006"]

        module = _mutant(*_M_COVER_UNCHECKED, name="j77_mutant_cover_absent")
        with pytest.raises(KeyError):
            module.build_review_snapshot(
                workflow_one, project_id=PROJECT, evidence_rows=NO_EVIDENCE,
                previous_revision=0, prior_items=prior,
            )

    def test_every_mutation_anchor_is_unique_in_the_module(self):
        source = SNAPSHOT_PATH.read_text(encoding="utf-8")
        for anchor, _ in (_M_DERIVED_PREFERRED, _M_UNTOUCHED_ACQUIRES, _M_COVER_UNCHECKED):
            assert source.count(anchor) == 1, anchor

    def test_no_other_module_substitutes_an_origin_either(self):
        """`with_origin` and `origin_of` are J72's; nothing in `app/` builds a
        `CandidateOrigin` from anything but a page's own array and its run."""
        for path in APP.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "CandidateOrigin(" in source and path != SNAPSHOT_PATH:
                assert path.name == "candidate_origin_address.py", path


# ===========================================================================
# 8. THE TWO ROUTES THAT CATCH `SnapshotRefused` (requirement C, READ-ONLY)
#
# The new refusal is a REFUSAL, and the routes must treat it as one. This section reads
# the two route modules and changes neither: it asserts that a revision that was not
# recorded cannot leave a success state behind, and cannot update the fabrication pointer
# that is downstream of it.
# ===========================================================================
class TestTheRoutesTreatTheNewRefusalAsARefusal:
    def test_the_resolution_route_does_not_special_case_the_new_code(self):
        """It names no J77 code, so the new refusal takes the generic mapping — which is
        what makes it a refusal rather than a new outcome."""
        source = RESOLUTION_PATH.read_text(encoding="utf-8")
        assert DIVERGED not in source
        assert model.SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED not in source

    def test_the_generic_mapping_is_a_refusal_and_it_precedes_the_pointer_write(self):
        """`SNAPSHOT_REFUSED_REVISION_GAP` and `SNAPSHOT_REFUSED_NO_REVIEW_LAYER` are
        mapped to codes of their own; EVERY other code — the new one included — becomes
        `RESOLUTION_REFUSED_NOT_PERSISTED`. The mapping is written as two named cases and
        a fall-through, so a code added later needs no route change to be refused; and the
        block ends in a `raise`, so no path out of it reaches the pointer write.
        """
        source = RESOLUTION_PATH.read_text(encoding="utf-8")
        catch = source.index("except SnapshotRefused as refusal:")
        pointer = source.index("# ---- POINTER:")
        assert catch < pointer

        between = source[catch:pointer]
        assert "SNAPSHOT_REFUSED_REVISION_GAP" in between
        assert "SNAPSHOT_REFUSED_NO_REVIEW_LAYER" in between
        assert between.count("RESOLUTION_REFUSED_NOT_PERSISTED") == 2, (
            "the generic mapping is one refusal for the coded case and one for the "
            "uncoded case"
        )
        assert between.count("raise ResolutionRouteRefused(") == 4, (
            "three inside the coded block and one for the uncoded failure"
        )
        assert "pointer_recorded" not in between

    def test_a_refused_revision_never_becomes_a_success_outcome(self):
        """The outcome is built after the try/except, so the only way to it is a revision
        that was actually recorded. Stated over the source rather than executed, because
        serving a request is not this file's job."""
        source = RESOLUTION_PATH.read_text(encoding="utf-8")
        assert source.index("return ProductionResolutionOutcome(") > \
            source.index("# ---- POINTER:")
        assert "durable_artifact_path=durable" in source

    def test_the_refusal_says_the_artifact_is_stored_and_not_pointed_at(self):
        """The one thing a refused revision can leave behind is the durable artifact it
        already wrote, and the statement says so in as many words — the pointer, which is
        what a downstream reader acts on, is never moved."""
        source = RESOLUTION_PATH.read_text(encoding="utf-8")
        catch = source.index("except SnapshotRefused as refusal:")
        generic = source[catch:source.index("# ---- POINTER:")]
        assert "the project pointer is deliberately not updated" in generic
        assert "must never describe a review that was not" in generic

    def test_the_opening_route_cannot_reach_the_new_refusal(self):
        """`open_project_review` proceeds only when no revision is recorded, so the prior
        it would carry is None and the divergence check never runs. It also names no J77
        code, so nothing was added to it."""
        source = OPENING_PATH.read_text(encoding="utf-8")
        assert DIVERGED not in source
        guard = source.index("if recorded is not None:")
        assert source.index("return _already_open(store, project_id, recorded)") > guard
        assert "prior_items" not in source


# ===========================================================================
# 9. WHAT THIS MILESTONE DID NOT DO
# ===========================================================================
class TestNothingElseWasTouched:
    def test_no_migration_was_added(self):
        migrations = sorted(
            path.name for path in (REPO / "supabase" / "migrations").glob("*.sql")
        )
        assert migrations, "the migrations directory is read, not written"
        assert not [name for name in migrations if "j77" in name.lower()], migrations

    def test_the_two_tables_and_their_columns_are_unchanged(self):
        """J21's design is the schema authority, and J77 adds no table and no column."""
        tables = {model.SNAPSHOT_TABLE, model.ITEM_TABLE, model.CITATION_TABLE}
        columns = {name for name, _, _, _ in model.SNAPSHOT_TABLE_COLUMNS}
        columns |= {name for name, _, _, _ in model.ITEM_TABLE_COLUMNS}
        assert not any("origin" in name for name in columns), columns
        assert tables == {
            "connection_review_snapshots", "connection_review_items",
            "connection_review_item_citations",
        }

    def test_r3_is_still_unwired(self):
        """J74's rule is a second entry point with no production caller, and J77 does not
        give it one. J80 does give it ONE — the consumer boundary, declared here rather than
        absorbed — and the guard still holds: that consumer is the only module naming R3.

        J81 gave the consumer its first production caller — the production read port, which
        reads the address the item's OWN evidence recorded and never carries one forward out
        of a reading. It is declared here rather than absorbed, and both lists stay exact, so
        a second namer of R3 or a second caller of the boundary still turns this red."""
        defining = APP / "cad_engine" / "connection_scoped_evidence.py"
        callers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if path != defining
            and "resolve_candidate_at_recorded_address" in path.read_text(encoding="utf-8")
        )
        assert callers == ["app/cad_engine/cited_candidate_resolution.py"], callers
        reaching_it = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "cited_candidate_resolution" in path.read_text(encoding="utf-8")
        )
        assert reaching_it == ["app/production_recorded_readings.py"], reaching_it

    def test_the_agreement_boundary_is_unchanged(self):
        """`AGREEMENT_KEYS` is the ten-tuple the replay compares. `evidence` is not one of
        them and is not added here: an origin is carried by the snapshot builder, not
        smuggled into the replay's projection."""
        assert AGREEMENT_KEYS == (
            "review_revision", "review_package_id", "connection_id", "decision",
            "output_status", "verification_status", "generated_files", "blocker_codes",
            "warning_codes", "last_processed_revision",
        )
        assert "evidence" not in AGREEMENT_KEYS

        # And the replay cannot see an origin even in principle: the state it compares has
        # no evidence field at all, which is precisely why the divergence has to be caught
        # where the snapshot is built rather than where the agreement is reported.
        fields = {field.name for field in dataclasses.fields(ProjectConnectionState)}
        assert "evidence" not in fields, sorted(fields)
        assert not any("origin" in name for name in fields), sorted(fields)

    def test_the_validator_never_sees_an_origin(self):
        """The snapshot builder is the only layer that knows about origins: neither the
        replay nor the resumption names J72's vocabulary."""
        from app.production_review import project_workflow_resumption as resumption

        source = inspect.getsource(resumption)
        for token in ("origin_of", "with_origin", "CandidateOrigin", "candidate_index",
                      "analysis_run_id"):
            assert token not in source, token

    def test_the_two_modified_modules_are_the_only_ones_that_name_the_new_code(self):
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if DIVERGED in path.read_text(encoding="utf-8")
        )
        assert naming == ["app/cad_engine/connection_review_snapshot.py"], naming
        store = STORE_PATH.read_text(encoding="utf-8")
        assert "SNAPSHOT_REFUSED_UNREPRESENTABLE" in store
        assert model.SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED not in store

    def test_the_store_still_composes_j21_rather_than_restating_it(self):
        """Every refusal the store raises before the database is J21's own code."""
        defined = {
            value for name, value in vars(model).items()
            if name.startswith("SNAPSHOT_REFUSED_") and isinstance(value, str)
        }
        import re

        used = set(re.findall(r"SNAPSHOT_REFUSED_[A-Z_]+", STORE_PATH.read_text("utf-8")))
        assert used <= defined, sorted(used - defined)

    def test_the_new_code_is_the_only_one_this_milestone_adds(self):
        codes = sorted(
            value for name, value in vars(model).items()
            if name.startswith("SNAPSHOT_REFUSED_") and isinstance(value, str)
        )
        assert codes == [
            "SNAPSHOT_REFUSED_DUPLICATE_PACKAGE_ID",
            "SNAPSHOT_REFUSED_NO_REVIEW_LAYER",
            "SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED",
            "SNAPSHOT_REFUSED_PROJECT_MISMATCH",
            "SNAPSHOT_REFUSED_PROJECT_UNKNOWN",
            "SNAPSHOT_REFUSED_REVISION_GAP",
            "SNAPSHOT_REFUSED_UNREPRESENTABLE",
        ], codes

    def test_the_module_still_reaches_no_io(self):
        """J21's purity is unchanged: the builder still opens no database, no socket and no
        file. The one read J77 adds is in the STORE, which already owned the reads."""
        tree = ast.parse(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                module = getattr(node, "module", None)
                for name in ([module] if module else []) + names:
                    assert name is None or not name.split(".")[0] in {
                        "os", "socket", "requests", "httpx", "supabase", "postgrest",
                        "storage3", "anthropic", "psycopg", "pathlib", "subprocess",
                    }, name
