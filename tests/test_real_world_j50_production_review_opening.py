"""
J50 — THE PRODUCTION REVIEW BASELINE-OPENING OPERATION.

WHAT THIS FILE IS

The proof of `POST /production/review/{project_id}/open`: that an authenticated project
owner can open their own project's review — recording the reconstruction's revision-0
baseline — that nobody else can, that opening is idempotent, that a second open consumes no
revision, and that the operation does nothing except that.

J48 established the revision contract (a project's first recorded revision is 0, and J22's
writer accepts it); J49 established that no production caller recorded it, and that without
one a review cannot start at all — J22's builder derives `expected = 0` from an empty chain
while the first human resolution is already revision 1, so revision 0 can never be a side
effect of a resolution. This file is the proof of the explicit operation.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the route and its HTTP status mapping, the J19 identity and authorization
              boundary (genuine ES256 tokens against an in-process JWKS document), J46's
              resumption over the genuine Arkles reconstruction, J22's snapshot BUILDER —
              the double below calls it, so a baseline J22 could not represent is refused
              rather than stored — and the J24A producer underneath the resumption.

    DOUBLED   the database: where J22's rows would go, which revisions the project has
              already recorded, and whether the revision can be read back. Every double
              RECORDS what it was asked, so "this step did not run" is asserted over a call
              that could have happened.

WHAT IS NOT HERE, AND WHY THAT IS THE POINT

There is no claim double, no working-directory fixture, no artifact double, no Storage
double and no resolver substitution — because the operation has none of them. A module that
tried to take a claim, read `ARTIFACT_WORKING_DIR` or generate an artifact would fail in
these tests rather than pass them, and section G proves from the source that it cannot.

WHAT THE MUTATIONS ARE

They are not extra tests. Each takes a property asserted above, removes or inverts the
thing that protects it, and shows the assertion goes RED. A guard that cannot fail is not a
guard.

WHAT THIS FILE DOES NOT CLAIM

It writes no live baseline, opens no live review, takes no claim, uploads nothing, reads no
real project and calls no Anthropic API. `ARTIFACT_WORKING_DIR` is never read or set.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import types

import pytest

import app.production_review_opening as opening
from app.cad_engine.connection_review_snapshot import (
    SNAPSHOT_REFUSED_NO_REVIEW_LAYER,
    SNAPSHOT_REFUSED_REVISION_GAP,
    SnapshotRefused,
    build_review_snapshot,
    snapshot_from_rows,
)
from app.engineering_data.connection_review_repository import REVIEW_STATE_RECORDED
from app.production_review.project_workflow_resumption import (
    RESUMPTION_HISTORY_GAP,
    ResumptionRefused,
)

from tests import production_review_auth as auth
from tests import test_real_world_j21_connection_review_data_model_design as j21
from tests import test_real_world_j23_page_extraction_capture as j23
from tests import test_real_world_j24a_revision_zero_producer as j24a
from tests import test_real_world_j4_production_extraction_report_truth as j4

#: J4's own module-scoped fixture, aliased so this file's tests can take it as a parameter
#: exactly as J19/J25/J47's do. It is a fixture and NOT a module: nothing at this file's
#: import time may touch `production.main`.
production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "production_review_opening.py"
MAIN_PATH = REPO / "app" / "main.py"
PRODUCTION_REVIEW_DIR = REPO / "app" / "production_review"

ROUTE_PATH = "/production/review/{project_id}/open"

PROJECT = "11111111-2222-4333-8444-555555555555"
OWNER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_OWNER = "99999999-8888-4777-8666-555555555555"
PACKAGE = "RP-0001"

#: The two tables J21's evidence fingerprint covers, and the only shape J22's builder
#: accepts. An empty row set is the honest one here: these doubles record a REVISION, and
#: the evidence identity is the fingerprint OF the rows rather than a claim about them.
EVIDENCE_ROWS = {"steel_members": [], "connections": []}

#: The names this module must never reach for. A generation, a verification, an upload, a
#: pointer, a resolution, a claim, a working directory and an AI call are all authorities
#: that exist elsewhere and are none of an open review's business.
FORBIDDEN_AUTHORITIES = (
    "acquire_project_review_claim",
    "release_project_review_claim",
    "ClaimRefused",
    "require_artifact_working_directory",
    "connection_workspace",
    "upload_verified_artifact",
    "durable_object_path",
    "record_fabrication_pointer",
    "resolve_project_connection",
    "dispatch_fabrication_drawing",
    "verify_drawing_artifact",
    "generate_report_pdf",
    "ARTIFACT_WORKING_DIR",
    "storage",
    "anthropic",
    "supabase_client",
    "pypdf",
    "fitz",
)


# ===========================================================================
# The doubles. Every one of them RECORDS what it was asked.
# ===========================================================================
class _Log:
    """One ordered record of what the composition asked the store to do."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, event: str) -> None:
        self.events.append(event)


class _ReviewStore:
    """J22's persistence, doubled — with J22's OWN builder doing the writing.

    What is doubled is where the rows would go, WHICH REVISIONS the project has already
    recorded, and whether the written revision can be read back. The write RULE is not
    doubled: `record_project_review` performs J22's own pre-read, calls `build_review_
    snapshot` with it, and then applies the deployed RPC's own arithmetic —
    `coalesce(max(review_revision) + 1, 0)` compared against the revision being written —
    so a revision that would leave a gap is refused here for exactly the reason it would be
    refused in production.
    """

    #: J22's own code, read from J22 rather than restated: the state this store reports when
    #: it holds a revision is the state J22 names.
    REVIEW_STATE_RECORDED = REVIEW_STATE_RECORDED

    def __init__(self, log, *, head=None, baseline=None, evidence_rows=None, fail=None,
                 read_back=True, gap=None) -> None:
        self.log = log
        # `head=None` models a project whose chain has recorded NOTHING; `head=0` models one
        # whose revision-0 baseline is recorded; `head=2` one that has advanced twice past
        # it. These are the three states section C is about.
        self.recorded: list[int] = [] if head is None else list(range(head + 1))
        self.snapshots: dict[int, object] = (
            {0: baseline} if baseline is not None else {}
        )
        self.evidence_rows = EVIDENCE_ROWS if evidence_rows is None else evidence_rows
        self.fail = fail
        self.read_back = read_back
        # "race": the winner's write landed first, so THIS write is refused as a gap and a
        # revision now exists. "unexplained": the same refusal with NOTHING recorded
        # afterwards, which is the case the module must not report as an open review.
        self.gap = gap
        self.calls: list[str] = []
        self.writes = 0
        self.written_run_ids: list[tuple[str, ...]] = []
        self.written_snapshots: list[object] = []
        self.evidence_repository = None

    def latest_recorded_revision(self, project_id, *, client=None):
        self.calls.append("latest_recorded_revision")
        return max(self.recorded) if self.recorded else None

    def project_evidence_rows(self, project_id, *, repository=None):
        self.calls.append("project_evidence_rows")
        self.evidence_repository = repository
        return self.evidence_rows

    def record_project_review(self, binding, workflow, *, evidence_rows,
                              evidence_run_ids=(), client=None):
        self.calls.append("record_project_review")
        self.log.record("record")
        if self.gap == "race":
            self.gap = None
            self.recorded.append(0)
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_REVISION_GAP, "the review chain advanced",
            )
        if self.gap == "unexplained":
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_REVISION_GAP, "no revision is recorded",
            )
        if self.fail is not None:
            raise self.fail
        # J22's own pre-read, then J22's own builder.
        previous_revision = self.latest_recorded_revision(binding.project_id, client=client)
        snapshot = build_review_snapshot(
            workflow,
            project_id=binding.project_id,
            evidence_rows=evidence_rows,
            evidence_run_ids=evidence_run_ids,
            previous_revision=previous_revision,
        )
        expected = max(self.recorded) + 1 if self.recorded else 0
        if snapshot.review_revision != expected:
            raise SnapshotRefused(
                SNAPSHOT_REFUSED_REVISION_GAP,
                f"the chain is at {expected - 1} and this snapshot is "
                f"revision {snapshot.review_revision}",
            )
        self.recorded.append(snapshot.review_revision)
        self.snapshots[snapshot.review_revision] = snapshot
        self.written_snapshots.append(snapshot)
        self.written_run_ids.append(tuple(evidence_run_ids))
        self.writes += 1
        return snapshot

    def load_review_snapshot(self, project_id, review_revision, *, client=None):
        self.calls.append(f"load_review_snapshot:{review_revision}")
        if not self.read_back:
            return None
        stored = self.snapshots.get(review_revision)
        if stored is None:
            return None
        # Assembled the way J22's own reader assembles it, from the rows it wrote.
        return snapshot_from_rows(*stored.to_rows())


def _uuid_store(project_id=PROJECT):
    """The genuine Arkles document, re-recorded under a canonical UUID project id."""
    table = j23._CaptureTable()
    j23._record(
        table, j24a._arkles_pages(), run="run-arkles",
        drawing=j24a.ARKLES_DRAWING, drawing_set=j24a.ARKLES_SET, project=project_id,
    )
    return j24a._store(
        project_id=project_id, set_id=j24a.ARKLES_SET, drawing_id=j24a.ARKLES_DRAWING,
        page_count=j24a.ARKLES_PAGE_COUNT, table=table, pages=j24a._arkles_pages(),
    )


def _reconstruction(store=None, project_id=PROJECT):
    store = store if store is not None else _uuid_store(project_id)
    reconstruction = j24a._reconstruct(store, project_id)
    assert reconstruction is not None, "the Arkles document reconstructs"
    return reconstruction


def _reviewer(user_id=OWNER):
    """A genuine `ReviewerIdentity`, with the issuer derived the way production does it."""
    from app.production_review.identity import ReviewerIdentity, supabase_issuer

    return ReviewerIdentity(
        user_id=user_id, role="authenticated",
        issuer=supabase_issuer("https://placeholder.supabase.co"), expires_at=2**31,
    )


def _binding(store=None, project_id=PROJECT, user_id=OWNER):
    """A GENUINE binding: J19's own reader, rule and binder over the Arkles record."""
    from app.production_review.authorization import authorize_project
    from app.production_review.binding import bind_project_review
    from app.production_review.project_read import read_project_record

    store = store if store is not None else _uuid_store(project_id)
    project_row = {"id": project_id, "user_id": user_id, "status": "review"}
    identity = _reviewer(user_id)
    record = read_project_record(project_id, project_row=project_row, repository=store)
    assert record is not None
    return bind_project_review(
        identity=identity, record=record,
        decision=authorize_project(identity, project_row),
    )


class _Wired:
    """One fully wired composition: the real function, every collaborator doubled.

    Note what is NOT here, because the operation does not have it: a claim store, a working
    directory, an artifact client, a Storage bucket and a resolver. A module that reached
    for any of them would raise here rather than pass.
    """

    def __init__(self, monkeypatch, *, head=None, baseline=None, evidence_rows=None,
                 fail=None, read_back=True, gap=None, project_id=PROJECT, user_id=OWNER):
        self.monkeypatch = monkeypatch
        self.log = _Log()
        self.project_id = project_id
        self.project_store = _uuid_store(project_id)
        self.reviews = _ReviewStore(
            self.log, head=head, baseline=baseline, evidence_rows=evidence_rows,
            fail=fail, read_back=read_back, gap=gap,
        )
        self.binding = _binding(self.project_store, project_id=project_id, user_id=user_id)
        monkeypatch.setattr(opening, "_review_store", lambda: self.reviews)

    def run(self, *, history=(), section_matcher=None, repository=None):
        return opening.open_project_review(
            binding=self.binding,
            review_client="review-client",
            section_matcher=(
                j24a._StubMatcher() if section_matcher is None else section_matcher
            ),
            repository=self.project_store if repository is None else repository,
            history=history,
        )


@pytest.fixture(scope="module")
def arkles():
    """The genuine revision-0 reconstruction under this file's project id, built once."""
    return _reconstruction()


@pytest.fixture()
def baseline(arkles):
    """The revision-0 baseline J22's own builder produces for it."""
    return build_review_snapshot(
        arkles.workflow, project_id=PROJECT, evidence_rows=EVIDENCE_ROWS,
        evidence_run_ids=arkles.capture_run_ids, previous_revision=None,
    )


# ===========================================================================
# A. THE REQUEST SURFACE — an opening request carries nothing.
# ===========================================================================
class TestWhatAClientMayNotSay:
    def test_an_absent_body_is_the_request(self):
        assert opening.parse_opening_request(None) is None

    def test_an_empty_object_is_the_request(self):
        assert opening.parse_opening_request({}) is None

    def test_the_only_permitted_field_is_the_document(self):
        """The URL supplies the project, the token the reviewer, the reconstruction the
        revision, and the persisted record which document a reading belongs to. So no field
        of this operation is a client's to supply — EXCEPT `document_id` with J61, and only
        because a project may hold more than one source document and the reconstruction is
        entitled to refuse rather than choose between them.

        Naming a document states WHICH document the opening is about. It states nothing
        about a revision, a decision, a path or an artifact, and it is optional: every
        request that named nothing before still names nothing and still opens the same
        project at revision 0.

        This assertion changed in J61 from `()` to the one name below, and the change is
        exactly that addition — the field list is still pinned, so a second field appearing
        unremarked still fails here."""
        assert opening.REQUEST_FIELDS == ("document_id",)
        assert "document_id" not in opening.SERVER_OWNED_FIELDS

    def test_a_named_document_that_is_not_a_name_is_refused(self):
        """A document is named by a non-empty string or not at all. `None` is the absence of
        a name, and every other value is refused rather than coerced — a blank string and a
        number are not document identities, and quietly treating one as "no document" would
        make a mistyped request look like a request that meant nothing."""
        for value in ("", "   ", 1, True, ["a"], {"a": 1}):
            with pytest.raises(opening.OpeningInputRefused) as refused:
                opening.parse_opening_request({"document_id": value})
            assert refused.value.code == opening.INPUT_REFUSED_DOCUMENT_INVALID

    def test_a_named_document_is_returned_verbatim(self):
        """The name is handed on exactly as it was given, and nothing is looked up here."""
        assert opening.parse_opening_request(
            {"document_id": "2f1c9a34-0000-4000-8000-000000000001"}) == (
            "2f1c9a34-0000-4000-8000-000000000001")

    def test_naming_no_document_is_the_same_request_as_before(self):
        """`None` and an absent key both mean "I am not naming one", which is what every
        request before J61 meant."""
        assert opening.parse_opening_request({"document_id": None}) is None
        assert opening.parse_opening_request(None) is None
        assert opening.parse_opening_request({}) is None

    @pytest.mark.parametrize("name", [
        "project_id", "connection_id", "package_id", "reviewer", "reviewer_id", "user_id",
        "claim_token", "revision", "expected_revision", "review_revision", "decision",
        "output_status", "verification_status", "artifact_path", "output_path",
        "working_dir", "evidence_rows", "evidence_run_ids",
    ])
    def test_every_server_owned_field_is_refused_by_name(self, name):
        with pytest.raises(opening.OpeningInputRefused) as refused:
            opening.parse_opening_request({name: "anything"})
        assert refused.value.code == opening.INPUT_REFUSED_SERVER_OWNED_FIELD
        assert name in refused.value.statement

    @pytest.mark.parametrize("body", [[1, 2], "a body", 3, True, ("x",)])
    def test_a_body_that_is_not_an_object_is_refused(self, body):
        with pytest.raises(opening.OpeningInputRefused) as refused:
            opening.parse_opening_request(body)
        assert refused.value.code == opening.INPUT_REFUSED_NOT_A_MAPPING

    def test_any_other_field_is_refused_as_unknown(self):
        with pytest.raises(opening.OpeningInputRefused) as refused:
            opening.parse_opening_request({"anything": 1})
        assert refused.value.code == opening.INPUT_REFUSED_UNKNOWN_FIELD
        assert "anything" in refused.value.statement

    def test_the_refusal_vocabulary_is_the_modules_own(self):
        """Four input refusals since J61, not three: a named document that is not a name is
        refused as an input error like any other, and the set is still pinned so that a
        fifth appearing unremarked fails here."""
        assert set(opening.INPUT_REFUSALS) == {
            opening.INPUT_REFUSED_NOT_A_MAPPING,
            opening.INPUT_REFUSED_SERVER_OWNED_FIELD,
            opening.INPUT_REFUSED_UNKNOWN_FIELD,
            opening.INPUT_REFUSED_DOCUMENT_INVALID,
        }
        assert set(opening.OPENING_REFUSALS) == {
            opening.OPENING_REFUSED_PROJECT_UNKNOWN,
            opening.OPENING_REFUSED_BASELINE_NOT_RECORDED,
            opening.OPENING_REFUSED_BASELINE_UNREADABLE,
        }

    def test_a_parsed_request_reaches_no_store(self, monkeypatch):
        """Parsing is a pure function: nothing is read or changed by it."""
        called = []
        monkeypatch.setattr(
            opening, "_review_store", lambda: called.append("store") or pytest.fail("read")
        )
        assert opening.parse_opening_request({}) is None
        assert called == []


# ===========================================================================
# B. THE EMPTY PROJECT — the baseline is recorded, at revision 0, once.
# ===========================================================================
class TestAnEmptyProjectIsOpened:
    def test_the_review_is_opened_at_revision_zero(self, monkeypatch, arkles):
        wired = _Wired(monkeypatch, head=None)
        outcome = wired.run()
        assert outcome.recorded_now is True
        assert outcome.review_revision == 0
        assert outcome.review_state == REVIEW_STATE_RECORDED
        assert outcome.project_id == PROJECT
        assert wired.reviews.writes == 1
        assert wired.reviews.recorded == [0], "the chain starts at revision 0"

    def test_the_recorded_baseline_is_j22s_own_builder_output(self, monkeypatch, baseline):
        """The write is J22's own construction: the snapshot the store holds is the one
        `build_review_snapshot` produces for the reconstruction, field for field."""
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        assert wired.reviews.written_snapshots[0].to_rows() == baseline.to_rows()
        assert wired.reviews.snapshots[0].review_revision == 0

    def test_the_evidence_run_ids_are_the_reconstructions(self, monkeypatch, arkles):
        assert arkles.capture_run_ids, "the Arkles capture has a run id"
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        assert wired.reviews.written_run_ids == [tuple(arkles.capture_run_ids)]

    def test_the_evidence_rows_come_from_j22s_own_source(self, monkeypatch):
        """`project_evidence_rows` is J22's, asked with the repository the caller supplied —
        this module assembles no evidence of its own."""
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        assert "project_evidence_rows" in wired.reviews.calls
        assert wired.reviews.evidence_repository is wired.project_store

    def test_the_reconstruction_is_j46s_with_expected_revision_zero(self, monkeypatch):
        """No second reconstruction: J46 is called, from the URL's project, over an empty
        history, with the revision an open requires and nothing else.

        J61 added one further argument to that call — the document the request named — and
        this spy carries it so that the argument is ASSERTED rather than merely tolerated:
        a request that named no document must hand J46 no document, which is the same call
        this test asserted before the field existed."""
        seen = {}
        real = opening.resume_project_workflow

        def recording(project_id, *, section_matcher, expected_revision=None,
                      repository=None, history=None, document_id=None):
            seen.update(
                project_id=project_id, expected_revision=expected_revision,
                history=history, repository=repository, section_matcher=section_matcher,
                document_id=document_id,
            )
            return real(
                project_id, section_matcher=section_matcher,
                expected_revision=expected_revision, repository=repository, history=history,
                document_id=document_id,
            )

        monkeypatch.setattr(opening, "resume_project_workflow", recording)
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        assert seen["project_id"] == PROJECT
        assert seen["expected_revision"] == opening.BASELINE_REVISION == 0
        assert seen["history"] == ()
        assert seen["repository"] is wired.project_store
        assert isinstance(seen["section_matcher"], j24a._StubMatcher)
        assert seen["document_id"] is None

    def test_the_order_of_the_whole_operation(self, monkeypatch):
        """The state is read, the reconstruction follows, the write follows that, and the
        read-back is last — and J22's own pre-read happens inside its own write."""
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        assert wired.reviews.calls == [
            "latest_recorded_revision",     # the module's own state read
            "project_evidence_rows",        # J22's evidence source, for the write's argument
            "record_project_review",        # J22's write, which then performs...
            "latest_recorded_revision",     # ...its OWN pre-read
            "load_review_snapshot:0",       # and the baseline is read back
        ]

    def test_the_read_back_is_what_reports_the_revision(self, monkeypatch):
        """`recorded_now` is claimed only after the persisted revision 0 was read again, and
        the revision reported is the one that was read."""
        read_back = {}
        wired = _Wired(monkeypatch, head=None)

        def recording_load(project_id, review_revision, *, client=None):
            value = _ReviewStore.load_review_snapshot(
                wired.reviews, project_id, review_revision, client=client,
            )
            read_back["snapshot"] = value
            return value

        monkeypatch.setattr(wired.reviews, "load_review_snapshot", recording_load)
        outcome = wired.run()
        assert read_back["snapshot"] is not None
        assert outcome.review_revision == read_back["snapshot"].review_revision

    def test_the_written_baseline_carries_the_project_and_its_own_status(
        self, monkeypatch, arkles
    ):
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        snapshot = wired.reviews.snapshots[0]
        assert snapshot.project_id == PROJECT
        assert snapshot.evidence_run_ids == tuple(arkles.capture_run_ids)
        assert len(snapshot.items) == len(arkles.workflow.connections)
        assert snapshot.to_rows()[0]["review_revision"] == 0

    def test_no_artifact_is_generated_stored_or_pointed_at(self, monkeypatch, tmp_path):
        """The composition was given no artifact client, no storage and no working
        directory, and produced no file anywhere."""
        before = sorted(p.name for p in pathlib.Path(tmp_path).iterdir())
        wired = _Wired(monkeypatch, head=None)
        wired.run()
        assert sorted(p.name for p in pathlib.Path(tmp_path).iterdir()) == before
        assert wired.log.events == ["record"], (
            "the only recorded event is J22's write"
        )


# ===========================================================================
# C. IDEMPOTENCY — an open review is reported, never re-opened.
# ===========================================================================
class TestAnOpenReviewStaysOpen:
    def test_a_recorded_baseline_is_not_written_again(self, monkeypatch, baseline):
        wired = _Wired(monkeypatch, head=0, baseline=baseline)
        outcome = wired.run()
        assert outcome.recorded_now is False
        assert outcome.review_revision == 0
        assert outcome.review_state == REVIEW_STATE_RECORDED
        assert wired.reviews.writes == 0
        assert wired.reviews.recorded == [0]

    def test_a_later_revision_is_reported_as_the_revision_that_is_recorded(
        self, monkeypatch, baseline
    ):
        """A project at revision 2 is not described as a fresh revision 0."""
        wired = _Wired(monkeypatch, head=2, baseline=baseline)
        outcome = wired.run()
        assert outcome.review_revision == 2
        assert outcome.recorded_now is False
        assert wired.reviews.writes == 0
        assert wired.reviews.recorded == [0, 1, 2]

    def test_an_open_review_is_neither_reconstructed_nor_written(
        self, monkeypatch, baseline
    ):
        """Nothing at all happens but a read of the state: the reconstruction would raise
        if it were reached, and the store would record the write."""
        def explodes(*args, **kwargs):  # pragma: no cover - only reached by a mutation
            raise AssertionError("an already-open review was reconstructed")

        monkeypatch.setattr(opening, "resume_project_workflow", explodes)
        wired = _Wired(monkeypatch, head=0, baseline=baseline)
        outcome = wired.run()
        assert outcome.review_revision == 0
        assert wired.reviews.calls == ["latest_recorded_revision"]
        assert wired.reviews.writes == 0

    def test_no_revision_is_consumed_by_a_repeat_open(self, monkeypatch, arkles):
        wired = _Wired(monkeypatch, head=None)
        first = wired.run()
        second = wired.run()
        assert first.recorded_now is True and second.recorded_now is False
        assert wired.reviews.recorded == [0], "one baseline, however many opens"
        assert wired.reviews.writes == 1
        assert second.review_revision == 0

    def test_the_reported_state_is_j22s_own_code(self, monkeypatch, arkles):
        wired = _Wired(monkeypatch, head=None)
        outcome = wired.run()
        assert outcome.review_state == opening._review_store().REVIEW_STATE_RECORDED
        assert outcome.to_response()["review_state"] == REVIEW_STATE_RECORDED

    def test_two_opens_over_one_store_leave_exactly_one_baseline(self, monkeypatch, arkles):
        """Two requests, one store: the second reads a recorded revision and writes
        nothing, so the chain holds one revision 0 however the two requests interleave."""
        wired = _Wired(monkeypatch, head=None)
        outcomes = [wired.run(), wired.run()]
        assert [o.recorded_now for o in outcomes] == [True, False]
        assert [o.review_revision for o in outcomes] == [0, 0]
        assert wired.reviews.recorded == [0]
        assert wired.reviews.writes == 1


# ===========================================================================
# D. CONCURRENCY — the WRITE is the barrier, never the read.
# ===========================================================================
class TestTwoOpensAtOnce:
    def test_the_read_is_not_the_barrier(self, monkeypatch, arkles):
        """J50's own model of the race, stated rather than hidden: a store whose first read
        answers an empty chain and whose write then loses. The loser must not report a
        failure, and must not write."""
        wired = _Wired(monkeypatch, head=None, gap="race")
        outcome = wired.run()
        assert outcome.recorded_now is False
        assert outcome.review_revision == 0
        assert wired.reviews.writes == 0
        assert wired.reviews.recorded == [0], "the winner's baseline is the one that exists"
        assert wired.reviews.calls.count("latest_recorded_revision") == 2, (
            "the head was re-read after the refusal"
        )

    def test_a_lost_race_reports_no_failure(self, monkeypatch, arkles):
        """The caller asked for an open review; one exists. It is a success, not a 409."""
        wired = _Wired(monkeypatch, head=None, gap="race")
        assert wired.run().to_response()["recorded_now"] is False

    def test_a_gap_with_nothing_recorded_is_a_genuine_failure(self, monkeypatch, arkles):
        """A gap this request cannot explain is NOT reported as an open review."""
        wired = _Wired(monkeypatch, head=None, gap="unexplained")
        with pytest.raises(opening.OpeningRefused) as refused:
            wired.run()
        assert refused.value.code == opening.OPENING_REFUSED_BASELINE_NOT_RECORDED
        assert wired.reviews.writes == 0

    def test_only_the_gap_refusal_is_treated_as_a_race(self, monkeypatch, arkles):
        """Every other J22 refusal is reported as itself, never swallowed into a success."""
        wired = _Wired(
            monkeypatch, head=None,
            fail=SnapshotRefused(SNAPSHOT_REFUSED_NO_REVIEW_LAYER, "no review layer"),
        )
        with pytest.raises(opening.OpeningRefused) as refused:
            wired.run()
        assert refused.value.code == opening.OPENING_REFUSED_BASELINE_NOT_RECORDED
        assert "no review layer" in refused.value.statement

    def test_no_new_lock_and_no_new_migration(self):
        """The barrier is J22's existing write. Nothing was added to make it work."""
        migrations = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
        assert not [p for p in migrations if "j50" in p.name.lower()], migrations
        code = _code_only(MODULE_PATH)
        for forbidden in ("advisory", "lock", "for update", "insert(", "update(", "select("):
            assert forbidden not in code, forbidden


# ===========================================================================
# E. REFUSALS — nothing is reported that did not happen.
# ===========================================================================
class TestRefusals:
    def test_a_project_the_reconstruction_does_not_know_is_refused(self, monkeypatch):
        monkeypatch.setattr(opening, "resume_project_workflow", lambda *a, **k: None)
        wired = _Wired(monkeypatch, head=None)
        with pytest.raises(opening.OpeningRefused) as refused:
            wired.run()
        assert refused.value.code == opening.OPENING_REFUSED_PROJECT_UNKNOWN
        assert wired.reviews.writes == 0

    def test_a_j22_refusal_is_not_reported_as_an_open_review(self, monkeypatch):
        wired = _Wired(monkeypatch, head=None, fail=RuntimeError("the store is down"))
        with pytest.raises(RuntimeError):
            wired.run()
        assert wired.reviews.writes == 0
        assert wired.reviews.recorded == []

    def test_an_unreadable_baseline_is_refused_and_the_row_survives(self, monkeypatch):
        """The one failure that names a write that DID land: the row is not deleted, and
        the review is not reported as open."""
        wired = _Wired(monkeypatch, head=None, read_back=False)
        with pytest.raises(opening.OpeningRefused) as refused:
            wired.run()
        assert refused.value.code == opening.OPENING_REFUSED_BASELINE_UNREADABLE
        assert refused.value.baseline_recorded is True
        assert "WRITTEN" in refused.value.statement
        assert wired.reviews.writes == 1, "the write happened"
        assert wired.reviews.recorded == [0], "and the recorded revision was not rolled back"

    def test_a_resumption_refusal_propagates_unchanged(self, monkeypatch):
        """J46's refusals are not restated, renamed or re-mapped here."""
        refusal = ResumptionRefused(RESUMPTION_HISTORY_GAP, "the chain has a gap")

        def refuses(*args, **kwargs):
            raise refusal

        monkeypatch.setattr(opening, "resume_project_workflow", refuses)
        wired = _Wired(monkeypatch, head=None)
        with pytest.raises(ResumptionRefused) as raised:
            wired.run()
        assert raised.value is refusal

    def test_a_refused_open_consumes_no_revision_and_can_be_retried(
        self, monkeypatch, arkles
    ):
        wired = _Wired(
            monkeypatch, head=None,
            fail=SnapshotRefused(SNAPSHOT_REFUSED_NO_REVIEW_LAYER, "not yet"),
        )
        with pytest.raises(opening.OpeningRefused):
            wired.run()
        assert wired.reviews.recorded == [] and wired.reviews.writes == 0
        wired.reviews.fail = None                       # the deployment recovers
        outcome = wired.run()
        assert outcome.recorded_now is True and outcome.review_revision == 0
        assert wired.reviews.recorded == [0]

    def test_the_operation_reads_no_working_directory(self, monkeypatch, arkles):
        """`ARTIFACT_WORKING_DIR` is not read, not required and not touched: opening a
        review must work in a deployment that generates nothing."""
        import os

        monkeypatch.delenv("ARTIFACT_WORKING_DIR", raising=False)
        assert os.environ.get("ARTIFACT_WORKING_DIR") is None
        wired = _Wired(monkeypatch, head=None)
        assert wired.run().recorded_now is True
        assert "ARTIFACT_WORKING_DIR" not in _code_only(MODULE_PATH)


# ===========================================================================
# F. THE RESPONSE — public state only, deterministic, and nothing else.
# ===========================================================================
class TestTheResponse:
    def test_the_response_carries_exactly_four_fields(self, monkeypatch, arkles):
        wired = _Wired(monkeypatch, head=None)
        assert set(wired.run().to_response()) == {
            "project_id", "review_state", "review_revision", "recorded_now",
        }

    def test_the_response_names_no_credential_path_identity_or_token(
        self, monkeypatch, arkles
    ):
        wired = _Wired(monkeypatch, head=None)
        body = json.dumps(wired.run().to_response())
        for absent in (
            OWNER, "review-client", "claim", "token", "Bearer", str(REPO), "/tmp",
            "service_role", "secret", "evidence_run_ids", "run-arkles",
        ):
            assert absent not in body, absent

    def test_the_response_is_deterministic_for_one_state(self, monkeypatch, baseline):
        wired = _Wired(monkeypatch, head=2, baseline=baseline)
        assert wired.run().to_response() == wired.run().to_response()

    def test_the_response_is_the_store_and_nothing_else(self, monkeypatch, arkles):
        wired = _Wired(monkeypatch, head=None)
        outcome = wired.run()
        assert outcome.to_response() == {
            "project_id": PROJECT,
            "review_state": REVIEW_STATE_RECORDED,
            "review_revision": 0,
            "recorded_now": True,
        }


# ===========================================================================
# G. THE SOURCE — the operation is exactly what it says it is.
# ===========================================================================
def _function_source(path, name):
    """One function's own source text, comments and docstrings included."""
    source = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} is not defined in {path}")


def _code_only(path):
    """The module's code with comments and docstrings removed.

    Prose that DESCRIBES what must not be reached for is not a use of it: the docstring
    above names a claim, a working directory and an upload precisely in order to say that
    this module does none of them. The guard is over the executable text.
    """
    import io
    import tokenize

    pieces: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        if token.type in (tokenize.NEWLINE, tokenize.NL):
            pieces.append("\n")
            continue
        text = token.string
        if pieces and pieces[-1][-1:] not in ("", "\n") and (
            pieces[-1][-1].isalnum() or pieces[-1][-1] == "_"
        ) and (text[:1].isalnum() or text[:1] == "_"):
            pieces.append(" ")
        pieces.append(text)
    return "".join(pieces)


def _mentions(line, name):
    """A line that CALLS `name` — matched as a call so a docstring mention is not one."""
    return f"{name}(" in line and not line.lstrip().startswith(("#", '"', "'", "*"))


def _call_line(text, name):
    """The index of the first line of `text` that CALLS `name`.

    Deliberately not a substring search: this module's comments name what it must do, so a
    textual match would find the prose rather than the call. A mutation test that swapped
    two calls would then still "pass" — which is the guard failing, not the code.
    """
    for index, line in enumerate(text.splitlines()):
        if _mentions(line, name):
            return index
    raise AssertionError(f"{name} is never called in this source")


def _before(text, first, second):
    """True when `first` is CALLED before `second` is in `text`."""
    return _call_line(text, first) < _call_line(text, second)


def _text_before(text, first, second):
    """Plain textual order, for guards whose left-hand side is a CONDITION rather than a
    call. Deliberately non-raising: a mutation that removes the text must answer False."""
    first_at, second_at = text.find(first), text.find(second)
    return first_at != -1 and second_at != -1 and first_at < second_at


def _swap(text, first, second):
    """`text` with the two named statements exchanged, for a mutation test."""
    lines = text.splitlines(keepends=True)
    a = next(i for i, line in enumerate(lines) if _mentions(line, first))
    b = next(i for i, line in enumerate(lines) if _mentions(line, second))
    lines[a], lines[b] = lines[b], lines[a]
    return "".join(lines)


def _imported_names(path):
    """Every module and name this file imports, from the AST."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reached.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            reached.add(node.module or "")
            reached.update(alias.name for alias in node.names)
    return reached


class TestTheSource:
    def test_the_module_reaches_for_no_claim_artifact_or_resolution_authority(self):
        code = _code_only(MODULE_PATH)
        for forbidden in FORBIDDEN_AUTHORITIES:
            assert forbidden not in code, forbidden

    def test_the_module_imports_no_such_authority_either(self):
        """The import list is the structural version of the same claim: a name that is
        never imported cannot be called, however the body is later edited."""
        reached = _imported_names(MODULE_PATH)
        for forbidden in FORBIDDEN_AUTHORITIES:
            assert not [name for name in reached if forbidden in name], forbidden

    def test_the_module_sits_outside_the_production_review_package(self):
        """J19's scope guard over `app/production_review/*.py` forbids the vocabulary this
        module legitimately needs (J22's evidence table names among them). It is a module
        BESIDE the package, not a change to any of it."""
        assert MODULE_PATH.parent == REPO / "app"
        assert not MODULE_PATH.is_relative_to(PRODUCTION_REVIEW_DIR)

    def test_the_module_writes_no_query_of_its_own(self):
        code = _code_only(MODULE_PATH)
        for forbidden in ("insert(", "update(", "upsert(", "delete(", "select(", "table("):
            assert forbidden not in code, forbidden

    def test_the_module_declares_the_revision_contract_once(self):
        body = _function_source(MODULE_PATH, "open_project_review")
        assert body.count("expected_revision=") == 1
        assert "expected_revision=BASELINE_REVISION" in body
        assert opening.BASELINE_REVISION == 0
        assert "revision=" not in body.replace("expected_revision=", "").replace(
            "review_revision=", ""
        ), "a revision is passed as a parameter somewhere"

    def test_the_route_lets_j46_read_the_chain_itself(self, production):
        """The replayed chain is never supplied by a caller. The module's default is
        `None`, which J46 reads as "read the persisted chain yourself", and the route
        passes no history at all — so no request can influence which revisions are
        replayed into the workflow a baseline is built from.

        J61 added the parsed document to that call, and this assertion changed with it.
        What is pinned is unchanged in substance: the route passes the binding and the one
        field it parsed, and nothing else — in particular no history, so no request can
        reach the replay. `document_id` states WHICH document the opening is about; it
        selects no revision and supplies no chain."""
        import inspect

        default = inspect.signature(opening.open_project_review).parameters["history"].default
        assert default is None
        body = _function_source(MODULE_PATH, "open_project_review")
        assert "history=history" in body, "the chain is not passed through to J46"
        route = _function_source(MAIN_PATH, "production_review_open")
        assert "open_project_review(binding=binding, document_id=document_id)" in route
        assert "history" not in route
        assert route.count("open_project_review(") == 1, (
            "a second call site is a second opening path"
        )

    def test_the_module_takes_capture_run_ids_from_the_resume(self):
        body = _function_source(MODULE_PATH, "open_project_review")
        assert "evidence_run_ids=resumed.capture_run_ids" in body
        assert body.count("capture_run_ids") == 1, (
            "a second mention is a second source of evidence-run identity"
        )

    def test_the_module_asks_j22_for_the_evidence_rows(self):
        body = _function_source(MODULE_PATH, "open_project_review")
        assert "store.project_evidence_rows(" in body

    def test_the_route_is_registered_once_as_a_post(self, production):
        routes = [
            route for route in production.main.app.routes
            if getattr(route, "path", "") == ROUTE_PATH
        ]
        assert len(routes) == 1
        assert routes[0].methods == {"POST"}
        assert opening.ROUTE_PATH == ROUTE_PATH

    def test_the_route_takes_no_required_body(self, production):
        """An opening request carries nothing, so there is nothing for a client to supply
        and nothing for the route to demand: the body is optional and its declared default
        is `None` — the empty request, not a required payload."""
        import inspect

        params = inspect.signature(production.main.production_review_open).parameters
        assert params["body"].default is not inspect.Parameter.empty, (
            "the body is required: a caller could not open a review with no body at all"
        )
        assert getattr(params["body"].default, "default", "") is None
        assert [name for name, p in params.items()
                if p.default is inspect.Parameter.empty] == ["project_id"], (
            "only the path parameter is required"
        )

    def test_the_route_authorizes_before_it_reads_anything(self):
        source = _function_source(MAIN_PATH, "production_review_open")
        assert _before(source, "_authorized_project", "parse_opening_request")
        assert _before(source, "parse_opening_request", "open_project_review")
        assert _before(source, "_bound_review", "open_project_review")

    def test_the_route_maps_every_opening_refusal_to_a_status(self):
        source = _function_source(MAIN_PATH, "_status_for_opening_refusal")
        assert "OPENING_REFUSED_PROJECT_UNKNOWN" in source
        assert "404" in source

    def test_the_route_catches_the_resumption_refusal_as_a_conflict(self):
        source = _function_source(MAIN_PATH, "production_review_open")
        assert "except ResumptionRefused as refused:" in source
        assert "status_code=409" in source


# ===========================================================================
# H. THE MUTATIONS — each guard shown to be able to fail.
# ===========================================================================
class TestTheMutations:
    """Each mutation removes the thing a guard above protects and shows it go red.

    A guard that cannot fail is not a guard. For the ORDER guards the mutation is a
    genuine reordering of the source that decides it; for the CAPABILITY and CONTRACT
    guards it is the acceptance the guard exists to prevent.
    """

    def test_mutation_a_the_write_before_the_reconstruction(self):
        """Move the write above the reconstruction: the order predicate must notice,
        because a baseline recorded from a workflow nobody resumed is a baseline from
        nowhere."""
        body = _function_source(MODULE_PATH, "open_project_review")
        assert _before(body, "resume_project_workflow", "record_project_review")
        mutated = _swap(body, "resume_project_workflow", "record_project_review")
        assert not _before(mutated, "resume_project_workflow", "record_project_review"), (
            "the ORDER predicate does not discriminate — it holds under the mutation too"
        )

    def test_mutation_b_the_read_back_before_the_write(self):
        """Move the read-back above the write: the success that read-back justifies would
        then be claimed for a revision that had not been written."""
        body = _function_source(MODULE_PATH, "open_project_review")
        assert _before(body, "record_project_review", "load_review_snapshot")
        mutated = _swap(body, "record_project_review", "load_review_snapshot")
        assert not _before(mutated, "record_project_review", "load_review_snapshot"), (
            "the READ-BACK ordering predicate does not discriminate"
        )

    def test_mutation_c_the_state_read_after_the_write(self):
        """Move the state read below the write: opening would always write, and a second
        open would consume a revision the project never asked for. The order predicate is
        the thing that fails, and it has to be a CALL-level predicate to mean anything —
        this module's comments name both calls, so a substring search would find the prose
        and hold anyway, which is the guard failing rather than the code."""
        body = _function_source(MODULE_PATH, "open_project_review")
        assert _before(body, "latest_recorded_revision", "record_project_review")
        mutated = _swap(body, "latest_recorded_revision", "record_project_review")
        assert mutated != body, "the mutation anchor was not found"
        assert not _before(mutated, "latest_recorded_revision", "record_project_review")

    def test_mutation_d_an_authority_imported(self, monkeypatch):
        """The capability guard, mutated: the same module with a claim import added. The
        ONLY difference is the import, so what the guard reports is the module's reach."""
        real_source = MODULE_PATH.read_text(encoding="utf-8")
        reached_now = _imported_names(MODULE_PATH)
        assert not [
            name for name in reached_now
            if any(f in name for f in FORBIDDEN_AUTHORITIES)
        ], "the module already imports one of the authorities"
        mutated = real_source.replace(
            "from app.cad_engine.connection_review_snapshot import (",
            "from app.production_review.project_review_claim import "
            "acquire_project_review_claim\n"
            "from app.cad_engine.connection_review_snapshot import (",
            1,
        )
        assert mutated != real_source, "the mutation anchor was not found"
        reached = set()
        for node in ast.walk(ast.parse(mutated)):
            if isinstance(node, ast.Import):
                reached.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                reached.add(node.module or "")
                reached.update(alias.name for alias in node.names)
        assert [name for name in reached
                if any(f in name for f in FORBIDDEN_AUTHORITIES)] == [
            "acquire_project_review_claim"
        ], "the guard does not notice a module that could take a claim"

    def test_mutation_e_a_workflow_that_is_not_revision_zero(
        self, monkeypatch, tmp_path, arkles
    ):
        """The revision-0 guarantee is J22's builder's, not a parameter of this operation.
        The mutation hands the write a workflow that has already advanced: the builder
        refuses it against an empty chain, and the operation reports a failure rather than
        an open review."""
        import app.cad_engine.project_workflow as project_workflow

        resolved = project_workflow.resolve_project_connection(
            arkles.workflow, package_id=PACKAGE,
            resolutions=j21._human_resolutions(arkles.workflow, PACKAGE),
            output_dir=str(tmp_path), expected_revision=0,
        )
        assert resolved.revision == 1, "the resolver advances the workflow to revision 1"

        real = opening.resume_project_workflow

        def advanced(*args, **kwargs):
            resumed = real(*args, **kwargs)
            return dataclasses.replace(resumed, workflow=resolved)

        monkeypatch.setattr(opening, "resume_project_workflow", advanced)
        wired = _Wired(monkeypatch, head=None)
        with pytest.raises(opening.OpeningRefused) as refused:
            wired.run()
        assert refused.value.code == opening.OPENING_REFUSED_BASELINE_NOT_RECORDED
        assert wired.reviews.recorded == [], (
            "a revision-1 workflow was recorded against an empty chain"
        )

    def test_mutation_f_an_already_open_project_reconstructed_and_written(
        self, monkeypatch, baseline
    ):
        """The idempotency guard, mutated: if the state read did not short-circuit, an
        open review would be reconstructed (which the mutation makes explode) — and the
        property 'an open review is not reconstructed' is what goes red."""
        monkeypatch.setattr(
            opening, "resume_project_workflow",
            lambda *a, **k: pytest.fail("an already-open review was reconstructed"),
        )
        wired = _Wired(monkeypatch, head=0, baseline=baseline)
        assert wired.run().recorded_now is False

        body = _function_source(MODULE_PATH, "open_project_review")
        assert _text_before(body, "if recorded is not None", "resume_project_workflow("), (
            "the short-circuit does not precede the reconstruction"
        )
        # The mutation: the same function with the short-circuit removed. The property goes
        # false, so it was the short-circuit that the property was standing on.
        mutated = body.replace("if recorded is not None:", "if False:  # mutation", 1)
        assert mutated != body, "the mutation anchor was not found"
        assert not _text_before(mutated, "if recorded is not None", "resume_project_workflow(")


# ===========================================================================
# I. THE HTTP SURFACE — statuses, refusals, and what the body may contain.
# ===========================================================================
class _ProjectQuery:
    """One `projects` statement, addressed by id — the read `_authorized_project` makes."""

    def __init__(self, rows):
        self.rows = rows
        self.project_id = None

    def select(self, *columns):
        return self

    def eq(self, column, value):
        assert column == "id", column
        self.project_id = value
        return self

    def single(self):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self.rows.get(self.project_id))


class _ProjectsClient:
    """`supabase`, for the one table the route may touch: `projects`, and nothing else.

    Every other table is an assertion failure rather than a silently answered query. The
    open reads no table at all for its own work: J22's rows are reached through the store
    this route is given, which in these tests is doubled.
    """

    def __init__(self, rows):
        self.rows = rows
        self.tables_read: list[str] = []

    def table(self, name):
        self.tables_read.append(name)
        assert name == "projects", f"the route read {name!r} itself"
        return _ProjectQuery(self.rows)


class _Http:
    """The real application, the real route, and authenticated clients.

    The COMPOSITION is doubled here on purpose, and only the composition: this section is
    about the ROUTE's mapping from a stated refusal to a status. Doubling it is what keeps
    this section from opening a review merely to look at a status code.
    """

    def __init__(self, production_module, monkeypatch, *, projects, owner=OWNER):
        self.main = production_module.main
        self.projects = projects
        self.client = _ProjectsClient(projects)
        monkeypatch.setattr(self.main, "supabase", self.client)
        tokens = auth.install(monkeypatch, production_module)
        from fastapi.testclient import TestClient

        self.tokens = tokens
        self.owner = owner
        self.http = TestClient(self.main.app, headers=tokens.headers(OWNER))
        self.bare = TestClient(self.main.app)
        self.other = TestClient(self.main.app, headers=tokens.headers(OTHER_OWNER))
        self.url = ROUTE_PATH.replace("{project_id}", PROJECT)


@pytest.fixture()
def http(production, monkeypatch):
    return _Http(
        production, monkeypatch,
        projects={PROJECT: {"id": PROJECT, "user_id": OWNER, "status": "review"}},
    )


def _route_raises(main, monkeypatch, error):
    """Makes the composition raise `error`, so a status can be looked at in isolation."""

    def explode(*args, **kwargs):
        raise error

    monkeypatch.setattr(main, "open_project_review", explode)


def _route_answers(main, monkeypatch, **fields):
    """Makes the composition answer a chosen outcome, so the route's rendering can be
    looked at without opening anything."""
    outcome = opening.ProductionReviewOpening(
        project_id=fields.get("project_id", PROJECT),
        review_state=fields.get("review_state", REVIEW_STATE_RECORDED),
        review_revision=fields.get("review_revision", 0),
        recorded_now=fields.get("recorded_now", True),
    )
    monkeypatch.setattr(main, "open_project_review", lambda *a, **k: outcome)
    return outcome


class TestTheHttpSurface:
    def test_no_credential_is_401(self, http):
        assert http.bare.post(http.url).status_code == 401

    def test_another_reviewers_credential_is_403(self, http):
        assert http.other.post(http.url).status_code == 403

    def test_an_unknown_project_is_404(self, http):
        url = http.url.replace(PROJECT, "12121212-3434-4545-8686-787878787878")
        assert http.http.post(url).status_code == 404

    def test_an_empty_body_is_the_request(self, http, monkeypatch, production):
        _route_answers(production.main, monkeypatch)
        assert http.http.post(http.url).status_code == 200
        assert http.http.post(http.url, json={}).status_code == 200

    def test_the_body_is_the_only_thing_a_caller_can_say_to_be_refused_for(
        self, http, monkeypatch, production
    ):
        _route_raises(production.main, monkeypatch, AssertionError("reached the store"))
        refused = http.http.post(http.url, json={"project_id": PROJECT})
        assert refused.status_code == 422
        assert refused.json()["detail"]["refusal"] == "INPUT_REFUSED_SERVER_OWNED_FIELD"
        assert http.http.post(http.url, json={"anything": 1}).status_code == 422
        assert http.http.post(http.url, json=[1]).status_code == 422

    def test_a_malformed_body_reaches_no_composition(self, http, monkeypatch, production):
        _route_raises(production.main, monkeypatch, AssertionError("reached the store"))
        assert http.http.post(http.url, json={"expected_revision": 0}).status_code == 422

    def test_the_response_is_the_objects_wire_form(self, http, monkeypatch, production):
        _route_answers(production.main, monkeypatch, review_revision=3, recorded_now=False)
        body = http.http.post(http.url).json()
        assert body == {
            "project_id": PROJECT, "review_state": REVIEW_STATE_RECORDED,
            "review_revision": 3, "recorded_now": False,
        }

    def test_an_unknown_project_from_the_composition_is_404(self, http, monkeypatch, production):
        _route_raises(
            production.main, monkeypatch,
            opening.OpeningRefused(opening.OPENING_REFUSED_PROJECT_UNKNOWN, "no such project"),
        )
        refused = http.http.post(http.url)
        assert refused.status_code == 404
        assert refused.json()["detail"]["refusal"] == opening.OPENING_REFUSED_PROJECT_UNKNOWN

    def test_a_baseline_that_could_not_be_recorded_is_500(self, http, monkeypatch, production):
        _route_raises(
            production.main, monkeypatch,
            opening.OpeningRefused(
                opening.OPENING_REFUSED_BASELINE_NOT_RECORDED, "the store refused it",
            ),
        )
        assert http.http.post(http.url).status_code == 500

    def test_a_baseline_that_could_not_be_read_back_is_500_and_names_the_write(
        self, http, monkeypatch, production
    ):
        _route_raises(
            production.main, monkeypatch,
            opening.OpeningRefused(
                opening.OPENING_REFUSED_BASELINE_UNREADABLE, "written but unreadable",
                baseline_recorded=True,
            ),
        )
        refused = http.http.post(http.url)
        assert refused.status_code == 500
        assert refused.json()["detail"]["baseline_recorded"] is True

    def test_a_resumption_refusal_is_409(self, http, monkeypatch, production):
        _route_raises(
            production.main, monkeypatch,
            ResumptionRefused(RESUMPTION_HISTORY_GAP, "the chain has a gap"),
        )
        refused = http.http.post(http.url)
        assert refused.status_code == 409
        assert refused.json()["detail"]["refusal"] == RESUMPTION_HISTORY_GAP

    def test_the_route_opens_nothing_when_no_request_reaches_it(self, http):
        """401/403 are answered before any composition exists to call at all: the store is
        never reached, and no table but `projects` — the one the authorization reads — is
        ever read by this route itself."""
        assert http.bare.post(http.url).status_code == 401
        assert http.client.tables_read == [], (
            "a request with no credential reached the database"
        )
        assert http.other.post(http.url).status_code == 403
        assert http.client.tables_read == ["projects"], (
            "deciding a 403 takes the project row, and reads nothing else"
        )
        assert set(http.client.tables_read) <= {"projects"}
        # and nothing but `projects` is readable through the client this route is given
        with pytest.raises(AssertionError, match="read"):
            http.client.table("steel_members")


# ===========================================================================
# J. WHAT THIS FILE DOES NOT CLAIM.
# ===========================================================================
class TestWhatThisFileDoesNotDo:
    def test_no_live_project_is_read_and_no_live_revision_is_written(self):
        """Every store in this file is the double above, and the module's own store seam is
        replaced in every test that reaches the composition. Nothing here opens a client."""
        assert _ReviewStore(_Log(), head=None).writes == 0
        code = _code_only(MODULE_PATH)
        for forbidden in ("create_client", "supabase_client", "os.environ"):
            assert forbidden not in code, forbidden

    def test_the_operation_opens_no_review_by_being_imported(self):
        """Importing the module performs no work: every authority it composes is reached
        inside the function, and the store is reached through a seam."""
        assert opening._review_store() is not None     # the module, not a client
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        module_level_calls = [
            node for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        assert module_level_calls == [], "the module calls something at import time"
