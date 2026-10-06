"""
J47 — THE PRODUCTION REVIEW ROUTE, proved over the genuine chain and the real boundary.

WHAT THIS FILE IS

The proof of `POST /production/review/{project_id}/connections/{package_id}/resolve`: that
an authenticated project owner can record one human review of one connection of their own
project, that nobody else can, and that the side effects it performs happen in the one order
that is safe — and do NOT happen when any step before them did not.

The route is the FIRST thing in this application that performs a real production write. So
almost nothing here is about a feature: it is about the ORDER, the REFUSALS and the failure
semantics, because those are what make the writes safe:

    the working directory is validated BEFORE the claim       (a broken deployment must not
                                                              take a claim from a reviewer
                                                              who could have used it)
    the tasks are validated BEFORE any answer is applied      (a half-recorded set of human
                                                              answers is never possible)
    the revision is re-read BEFORE it is written              (a stale view never records a
                                                              second opinion)
    the pointer is written LAST                               (it is a claim about a
                                                              deliverable, so the deliverable
                                                              must already exist)
    the claim is released on EVERY path                       (a review that failed still
                                                              frees the project)

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the route and its HTTP status mapping, the J19 identity and authorization
              boundary, J44's claim module, J46's resumption and its ten-key agreement
              guard and task-ownership seam, J22's snapshot BUILDER (the double below calls
              it, so a resolved state that could not be represented is refused rather than
              stored), J45's working-directory preflight, path derivation and upload
              validation, J47's own pointer writer and its refusal to store a local path,
              and the credentials — genuine ES256 tokens verified against a real
              in-process JWKS document (J19's own harness).

    DOUBLED   1. `resolve_project_connection`. It is 7AJ's, it is called and never
                 re-implemented (section J proves that from the source), and its own
                 behaviour — every gate, the dispatch, the verification — is the subject of
                 the 7AJ/7AF/7AG suites. What THIS file is about is the route's composition
                 around it, so the resolver is replaced by one whose outcome the test
                 chooses: AUTO/verified, AUTO/unverified, AUTO/dispatched-but-unreadable,
                 REVIEW, CONFIRM. Without that substitution the failure orderings below
                 could only be tested by breaking a real gate.

              2. The database, the storage bucket and the clock — the three things no test
                 in this repository performs, exactly as J19, J22, J44, J45 and J46 double
                 them. Every double RECORDS what it was asked, so "this step did not run"
                 is asserted over a call that could have happened.

WHAT MUTATION TESTS A-Q ARE

They are not extra tests. Each one takes a property this file asserts, removes or inverts
the thing that protects it, and shows the assertion above goes RED. A guard that cannot
fail is not a guard, and the mutation is the only evidence that the guard is load-bearing.

WHAT THIS FILE DOES NOT CLAIM

It performs no live write. It reads no real project, records no real revision, uploads no
real object, takes no real claim, and calls no Anthropic API and no extraction. The genuine
end-to-end proof over a real project is the controlled live review, which J47 stops at the
gate of rather than performing — see the milestone report.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import json
import pathlib
import types

import pytest

import app.production_review_resolution as resolution
import app.cad_engine.project_workflow as project_workflow
from app.cad_engine.connection_review_snapshot import build_review_snapshot
from app.production_fabrication_artifact import (
    ARTIFACT_REFUSED_NO_WORKING_DIR,
    ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
    ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
    ArtifactRefused,
    connection_workspace,
    durable_object_path,
)
from app.production_fabrication_pointer import PointerRefused
from app.production_review.project_review_claim import ClaimRefused
from app.production_review.project_workflow_resumption import (
    RESOLUTION_TARGET_UNKNOWN,
    RESOLUTION_TASK_DUPLICATED,
    RESOLUTION_TASK_FOREIGN_CONNECTION,
    RESOLUTION_TASK_NOT_OF_THIS_PROJECT,
    RESUMPTION_EXPECTED_REVISION_AHEAD,
    RESUMPTION_EXPECTED_REVISION_STALE,
    RESUMPTION_HISTORY_GAP,
    ResolutionTaskRefused,
    ResumptionRefused,
    validate_history,
)

from tests import production_review_auth as auth
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j23_page_extraction_capture as j23
from tests import test_real_world_j24a_revision_zero_producer as j24a

#: J4's own module-scoped fixture, aliased so this file's tests can take it as a
#: parameter exactly as J25's do. It is a fixture and NOT a module: nothing at this
#: file's import time may touch `production.main`.
production = j4.production

REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "production_review_resolution.py"
POINTER_PATH = REPO / "app" / "production_fabrication_pointer.py"
MAIN_PATH = REPO / "app" / "main.py"
PRODUCTION_REVIEW_DIR = REPO / "app" / "production_review"

ROUTE_PATH = "/production/review/{project_id}/connections/{package_id}/resolve"

#: A canonical UUID, because J45's durable identity derives one — and the ROUTE, not the
#: test, is what requires that. The Arkles document is re-recorded under this id below.
PROJECT = "11111111-2222-4333-8444-555555555555"
OWNER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_OWNER = "99999999-8888-4777-8666-555555555555"

ARCHIVE_PROJECT = j24a.ARKLES_PROJECT
PACKAGE = "RP-0001"
OTHER_PACKAGE = "RP-0002"

TASK = "RP-0001-T01"
FOREIGN_TASK = "RP-0002-T01"

FABRICATION_PDF = b"%PDF-1.4\n% the verified artifact\n%%EOF\n"


# ===========================================================================
# The doubles. Every one of them RECORDS what it was asked.
# ===========================================================================
class _Log:
    """One ordered record of everything the request touched.

    The order is the property under test in section C, so it is captured in ONE list
    shared by every double rather than inferred afterwards from separate counters.
    """

    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, event: str) -> None:
        self.events.append(event)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(self.events)


class _ClaimStore:
    """J44's two calls, doubled, recording the tokens they were given.

    `acquire` answers with a token the test chooses; `release` records EVERY token it was
    asked to release, which is what makes "the exact token obtained" and "never a second
    release" assertable rather than described.
    """

    def __init__(self, log, *, refusal: ClaimRefused | None = None,
                 release_refusal: ClaimRefused | None = None, token="claim-token-1") -> None:
        self.log = log
        self.refusal = refusal
        self.release_refusal = release_refusal
        self.token = token
        self.acquires: list[tuple[str, str]] = []
        self.releases: list[tuple[str, str]] = []

    def acquire(self, project_id, holder, *, lease_seconds=None, client=None):
        if self.refusal is not None:
            raise self.refusal
        self.log.record("claim")
        self.acquires.append((project_id, holder))
        return types.SimpleNamespace(
            project_id=project_id,
            claim_token=self.token,
            acquired_at="2026-09-28T00:00:00+00:00",
            lease_expires_at="2026-09-28T00:15:00+00:00",
            took_over=False,
        )

    def release(self, project_id, claim_token, *, client=None):
        self.releases.append((project_id, claim_token))
        if self.release_refusal is not None:
            raise self.release_refusal
        self.log.record("release")


class _ResolutionStore:
    """J22's persistence, doubled — with J22's OWN builder doing the writing.

    The write path is not faked into existence: `record_project_review` calls
    `build_review_snapshot`, so a resolved workflow that J22 could not represent is
    REFUSED here exactly as it would be in production. What is doubled is only where the
    rows would go, and WHICH REVISION the project's chain is already at.

    `head` is that revision — the value J22's own reader answers with, and the value
    `record_project_review` hands the builder as `previous_revision`. The default, 0, is
    the state J22's writer requires before it will accept a first resolve: its builder
    computes `expected = 0 if previous_revision is None else previous_revision + 1`, and a
    resolve advances the workflow to 1. `head=None` models the other state, a project whose
    chain has recorded nothing.

    ⚠ THOSE TWO STATES CANNOT BOTH EXIST UNDER J46. `validate_history` refuses any chain
    containing a recorded revision 0 (`revision < 1` → `RESUMPTION_HISTORY_GAP`), so the
    chain J22's writer will accept is precisely the chain J46's reader will not read. It is
    the finding of this milestone and it is proved in `TestTheChainContradiction` below,
    over the genuine modules, with no double in the refusal path. The success-path tests
    here therefore run against a store in a state J22 can write and J46 cannot read — which
    is stated rather than hidden, because it is the whole of what the route is blocked on.
    """

    def __init__(self, log, *, head=0, evidence_rows=None, fail: Exception | None = None,
                 revision_override=None) -> None:
        self.log = log
        self.head = head
        # The fingerprint covers exactly these two tables and refuses an absent one, so
        # the double answers the shape the production reader answers with rather than an
        # empty mapping the builder would reject for the wrong reason.
        self.evidence_rows = (
            evidence_rows if evidence_rows is not None
            else {"steel_members": [], "connections": []}
        )
        self.fail = fail
        self.revision_override = revision_override
        self.calls: list[str] = []
        self.recorded: list[tuple[str, int, tuple[str, ...]]] = []

    def latest_recorded_revision(self, project_id, *, client=None):
        self.calls.append("latest_recorded_revision")
        if self.revision_override is not None:
            return self.revision_override
        # None when nothing has been recorded — the exact shape of the real reader, and
        # the reason the route normalizes the absence to revision 0 before comparing.
        return self.head

    def project_evidence_rows(self, project_id, *, repository=None):
        self.calls.append("project_evidence_rows")
        return self.evidence_rows

    def record_project_review(self, binding, workflow, *, evidence_rows,
                              evidence_run_ids=(), client=None):
        self.calls.append("record_project_review")
        if self.fail is not None:
            raise self.fail
        snapshot = build_review_snapshot(
            workflow,
            project_id=binding.project_id,
            evidence_rows=evidence_rows,
            evidence_run_ids=evidence_run_ids,
            previous_revision=self.head,
        )
        self.log.record("record")
        self.recorded.append(
            (binding.project_id, snapshot.review_revision, tuple(evidence_run_ids))
        )
        return snapshot


class _PointerClient:
    """The pointer writer's client: one `.table().update().eq().execute()`."""

    def __init__(self, log, *, matched=True, error: Exception | None = None) -> None:
        self.log = log
        self.matched = matched
        self.error = error
        self.updates: list[tuple[str, dict, str]] = []

    def table(self, name):
        assert name == "projects", name
        return self

    def update(self, values):
        self.pending = (values,)
        return self

    def eq(self, column, value):
        assert column == "id", column
        self.updates.append((column, dict(self.pending[0]), value))
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        self.log.record("pointer")
        return types.SimpleNamespace(data=[{"id": self.updates[-1][2]}] if self.matched else [])


class _Storage:
    """What `client.storage.from_(bucket).upload(...)` returns. Records the object."""

    def __init__(self, log, *, error: Exception | None = None) -> None:
        self.log = log
        self.error = error
        self.uploads: list[tuple[str, bytes, dict]] = []

    def upload(self, path, content, file_options=None):
        if self.error is not None:
            raise self.error
        self.log.record("upload")
        self.uploads.append((path, bytes(content), dict(file_options or {})))
        return {"path": path}


class _ArtifactClient:
    """What `client.storage.from_(bucket)` is reached through."""

    def __init__(self, log, *, error: Exception | None = None) -> None:
        self.log = log
        self.buckets: list[str] = []
        self.storage_object = _Storage(log, error=error)

    @property
    def storage(self):
        return self

    def from_(self, bucket):
        self.buckets.append(bucket)
        return self.storage_object


class _Verification:
    """The two fields the route reads from 7AG's manifest, and no more.

    A stand-in rather than a doctored `ArtifactVerificationResult`, and deliberately so:
    the route reads `verification_status` and `artifact_path` and nothing else, so a double
    that supplies exactly those proves the route cannot be depending on the other fourteen
    fields. A real 7AG manifest satisfies this shape — that is what the type is for.
    """

    def __init__(self, *, verification_status, artifact_path) -> None:
        self.verification_status = verification_status
        self.artifact_path = artifact_path


# ===========================================================================
# Building the genuine workflow, and the outcomes the resolver can return.
# ===========================================================================
def _uuid_store(project_id=PROJECT):
    """The genuine Arkles document, re-recorded under a canonical UUID project id.

    The document is J24A's own (three connections, every one REVIEW at revision 0), which
    is what makes an AUTO outcome non-vacuous: nothing in revision 0 is AUTO, so an AUTO
    below can only have come from what the route composed.
    """
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


def _resolved_state(workflow, *, package_id=PACKAGE, decision, output_status,
                    verification_status, generated_files=(), connection_id="C-1",
                    blockers=(), warnings=()):
    """The next workflow state, as 7AJ would return it — every other connection untouched.

    Built by `dataclasses.replace` over the GENUINE revision-0 state, so the object the
    route handles is a real `ProjectWorkflowState` and not a shape invented for the test.
    `failure` is 7AG's own dispatched-but-unreadable outcome: a real artifact path with a
    verification status that is not VERIFIED.
    """
    index = [c.package_id for c in workflow.connections].index(package_id)
    connections = list(workflow.connections)
    connections[index] = dataclasses.replace(
        connections[index],
        connection_id=connection_id,
        decision=decision,
        output_status=output_status,
        verification_status=verification_status,
        generated_files=tuple(generated_files),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        last_processed_revision=workflow.revision + 1,
    )
    return dataclasses.replace(
        workflow, revision=workflow.revision + 1, connections=tuple(connections),
    )


def _with_record(state, *, package_id=PACKAGE, verification):
    """The same state, carrying a stage record whose 7AG manifest is the one given."""
    index = [c.package_id for c in state.connections].index(package_id)
    records = list(state.connection_records)
    records[index] = dataclasses.replace(
        records[index], verification_result=verification,
    )
    return dataclasses.replace(state, connection_records=tuple(records))


#: "no outcome was set", as distinct from "the outcome is None" — a test that wants to
#: observe the resolver NOT being called must still install one that would record a call.
_UNSET = object()


def _resolver(state, log=None):
    """A stand-in for 7AJ's resolver that returns the state the test chose.

    It records its own call as the `resolve` step of the order, and it records the
    arguments it was given — so "what the route passed the resolver" and "when the
    resolver ran" are both asserted rather than described.
    """
    calls = []

    def resolve(workflow, *, package_id, resolutions, output_dir, expected_revision=None,
                drawing_entry=None):
        if log is not None:
            log.record("resolve")
        calls.append({
            "package_id": package_id,
            "resolutions": tuple(resolutions),
            "output_dir": output_dir,
            "expected_revision": expected_revision,
        })
        return state

    resolve.calls = calls
    return resolve


# ===========================================================================
# The request, the binding, and the wired composition.
# ===========================================================================
def _body(**overrides):
    body = {
        "expected_revision": 0,
        "resolutions": [{
            "task_id": TASK,
            "task_type": "COMPLETE_REVIEW",
            "answer_type": "APPROVE_REVIEW",
            "answer": True,
            "evidence": "checked against the revision drawing",
        }],
    }
    body.update(overrides)
    return body


def _reviewer(user_id=OWNER):
    """A genuine `ReviewerIdentity`, with the issuer derived the way production does it.

    `supabase_issuer` is this application's own derivation (never a hand-typed `iss`),
    and it is given a URL directly so that importing it never touches `app.config`.
    """
    from app.production_review.identity import ReviewerIdentity, supabase_issuer

    return ReviewerIdentity(
        user_id=user_id, role="authenticated",
        issuer=supabase_issuer("https://placeholder.supabase.co"), expires_at=2**31,
    )


def _app_config():
    """`app.config`, importable.

    `app.config` reads `os.environ["SUPABASE_URL"]` at IMPORT, so a bare pytest process
    cannot import it at all. The repository's `.env` fills the keys this process does not
    already have — `load_dotenv` does not override, so a real environment always wins.
    When there is no `.env` either, the test skips rather than fails: the subject is the
    preflight's use of the configuration, not the presence of credentials. (J45's own
    tests use the same helper for the same reason.)
    """
    try:
        import dotenv

        dotenv.load_dotenv(REPO / ".env")
        import app.config as config
    except KeyError as exc:
        pytest.skip(f"app.config reads {exc} at import and no .env is available")
    return config


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
    """One fully wired composition: the real function, every collaborator doubled."""

    def __init__(self, monkeypatch, *, working_dir, state=_UNSET, head=0,
                 store=None, evidence_rows=None, store_fail=None, revision_override=None,
                 claim_refusal=None, release_refusal=None, pointer_error=None,
                 storage_error=None, project_id=PROJECT, user_id=OWNER,
                 verification=None, artifact_bytes=FABRICATION_PDF):
        self.monkeypatch = monkeypatch
        self.log = _Log()
        self.project_id = project_id
        self.store = store if store is not None else _uuid_store(project_id)
        self.working_dir = working_dir
        self.artifact_bytes = artifact_bytes

        self.review_store = _ResolutionStore(
            self.log, head=head, evidence_rows=evidence_rows, fail=store_fail,
            revision_override=revision_override,
        )
        self.claims = _ClaimStore(
            self.log, refusal=claim_refusal, release_refusal=release_refusal,
        )
        self.pointer = _PointerClient(self.log, error=pointer_error)
        self.artifacts = _ArtifactClient(self.log, error=storage_error)
        self.binding = _binding(self.store, project_id=project_id, user_id=user_id)

        self.workflow = _reconstruction(self.store, project_id).workflow
        self.resolver = None
        monkeypatch.setattr(resolution, "_review_store", lambda: self.review_store)
        # J44's two calls, as the route's own module names them. `project_workflow` holds
        # no copy of them: the claim is the route's, not the resolver's.
        monkeypatch.setattr(resolution, "acquire_project_review_claim", self.claims.acquire)
        monkeypatch.setattr(resolution, "release_project_review_claim", self.claims.release)
        if state is not _UNSET:
            self.set_outcome(state)

    def set_outcome(self, state):
        """The outcome 7AJ's resolver will return, and its call recorded as `resolve`."""
        self.resolver = _resolver(state, self.log)
        self.monkeypatch.setattr(
            resolution, "resolve_project_connection", self.resolver,
        )
        return self.resolver

    def run(self, *, body=None, package_id=PACKAGE):
        request = resolution.parse_resolution_request(body if body is not None else _body())
        return resolution.resolve_production_connection(
            binding=self.binding,
            package_id=package_id,
            request=request,
            artifact_client=self.artifacts,
            review_client="review-client",
            claim_client=None,
            pointer_client=self.pointer,
            section_matcher=j24a._StubMatcher(),
            repository=self.store,
            history=(),
            working_dir=self.working_dir,
        )

    def auto_state(self, *, verified=True, artifact_path=None, blockers=()):
        """An AUTO outcome whose artifact is (or is not) a VERIFIED one.

        The unverified case is 7AG's OWN failing verdict, `VERIFICATION_STATUS_FAILED`
        — what `drawing_output_verification` records when it parsed the artifact and a
        check did not hold — and the artifact is still written to the workspace and still
        named by the manifest, exactly as a failed verification leaves it. So the refusal
        that follows is on the STATUS and not on the absence of a path.
        """
        from app.cad_engine.drawing_output_verification import (
            VERIFICATION_STATUS_FAILED,
            VERIFICATION_STATUS_VERIFIED,
        )

        path = artifact_path
        if path is None:
            path = connection_workspace(self.project_id, PACKAGE, working_dir=self.working_dir)
            path = pathlib.Path(path) / f"{PACKAGE}-fabrication.pdf"
            path.write_bytes(self.artifact_bytes)
            path = str(path)
        status = VERIFICATION_STATUS_VERIFIED if verified else VERIFICATION_STATUS_FAILED
        return _with_record(
            _resolved_state(
                self.workflow, decision="AUTO", output_status="GENERATED",
                verification_status=status,
                generated_files=(str(path),) if path else (), blockers=blockers,
            ),
            verification=_Verification(
                verification_status=status,
                artifact_path=pathlib.Path(path) if path else None,
            ),
        )


@pytest.fixture(scope="module")
def arkles_workflow():
    """The genuine revision-0 workflow under a UUID project id, built once."""
    return _reconstruction().workflow


@pytest.fixture()
def working_dir(tmp_path):
    directory = tmp_path / "artifact-working-dir"
    directory.mkdir()
    return directory


@pytest.fixture()
def wired(monkeypatch, working_dir, arkles_workflow):
    """A wired composition whose resolver returns the state the test sets afterwards."""
    return _Wired(monkeypatch, working_dir=working_dir)


# ===========================================================================
# A. THE REQUEST — what a client may say, and what it may not.
# ===========================================================================
class TestWhatAClientMayNotSay:
    """The body is `expected_revision` and `resolutions`. Everything else is refused."""

    @pytest.mark.parametrize("field", resolution.SERVER_OWNED_FIELDS)
    def test_a_server_owned_field_in_the_body_is_refused_by_name(self, field):
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(**{field: "anything"}))
        assert caught.value.code == resolution.INPUT_REFUSED_SERVER_OWNED_FIELD
        assert field in caught.value.statement

    def test_the_server_owned_list_is_not_short(self):
        """The refusal above is only as good as the list it is driven from."""
        for field in ("project_id", "connection_id", "package_id", "claim_token",
                      "artifact_path", "output_status", "verification_status", "decision",
                      "reviewer", "reviewer_id", "user_id"):
            assert field in resolution.SERVER_OWNED_FIELDS, field

    def test_an_unknown_field_is_refused(self):
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(wat="x"))
        assert caught.value.code == resolution.INPUT_REFUSED_UNKNOWN_FIELD

    @pytest.mark.parametrize("value", [None, "0", 1.0, True, -1.5])
    def test_a_revision_that_is_not_a_whole_number_is_refused(self, value):
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(expected_revision=value))
        assert caught.value.code == resolution.INPUT_REFUSED_EXPECTED_REVISION

    def test_a_missing_revision_is_refused(self):
        """A request that does not say which revision it decided against cannot be judged
        stale, and defaulting it to the current one would let a stale click be applied."""
        body = _body()
        del body["expected_revision"]
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(body)
        assert caught.value.code == resolution.INPUT_REFUSED_EXPECTED_REVISION

    @pytest.mark.parametrize("value", [None, {}, "x", 3])
    def test_resolutions_that_are_not_a_list_are_refused(self, value):
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(resolutions=value))
        assert caught.value.code == resolution.INPUT_REFUSED_NO_RESOLUTIONS

    def test_an_empty_resolution_list_is_refused(self):
        """A connection is resolved at most once; an empty request would consume that
        single attempt and process nothing."""
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(resolutions=[]))
        assert caught.value.code == resolution.INPUT_REFUSED_NO_RESOLUTIONS

    @pytest.mark.parametrize("missing", ["task_id", "task_type", "answer_type", "answer"])
    def test_a_resolution_missing_a_required_field_is_refused(self, missing):
        entry = _body()["resolutions"][0]
        del entry[missing]
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(resolutions=[entry]))
        assert caught.value.code == resolution.INPUT_REFUSED_MISSING_TASK_FIELD
        assert missing in caught.value.statement

    @pytest.mark.parametrize("field", ["task_id", "task_type", "answer_type"])
    def test_a_blank_task_identity_is_refused(self, field):
        entry = _body()["resolutions"][0]
        entry[field] = "   "
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(resolutions=[entry]))
        assert caught.value.code == resolution.INPUT_REFUSED_MISSING_TASK_FIELD

    def test_a_resolution_naming_a_server_owned_field_is_refused(self):
        entry = _body()["resolutions"][0]
        entry["connection_id"] = "C-9"
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(resolutions=[entry]))
        assert caught.value.code == resolution.INPUT_REFUSED_SERVER_OWNED_FIELD

    def test_a_non_string_evidence_is_refused(self):
        entry = _body()["resolutions"][0]
        entry["evidence"] = 7
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(resolutions=[entry]))
        assert caught.value.code == resolution.INPUT_REFUSED_MISSING_TASK_FIELD

    def test_a_body_that_is_not_an_object_is_refused(self):
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(["not", "an", "object"])
        assert caught.value.code == resolution.INPUT_REFUSED_NOT_A_MAPPING

    def test_evidence_is_optional_and_defaults_to_empty(self):
        entry = _body()["resolutions"][0]
        del entry["evidence"]
        request = resolution.parse_resolution_request(_body(resolutions=[entry]))
        assert request.resolutions[0].evidence == ""

    def test_a_valid_body_becomes_human_resolutions_verbatim(self):
        """The parser states what a resolution REQUEST is; it does not interpret answers."""
        request = resolution.parse_resolution_request(_body())
        assert request.expected_revision == 0
        assert len(request.resolutions) == 1
        answer = request.resolutions[0]
        assert (answer.task_id, answer.task_type, answer.answer_type) == (
            TASK, "COMPLETE_REVIEW", "APPROVE_REVIEW",
        )
        assert answer.answer is True
        assert answer.evidence == "checked against the revision drawing"

    def test_the_parsed_request_carries_no_client_supplied_identity_or_status(self):
        request = resolution.parse_resolution_request(_body())
        fields = {f.name for f in dataclasses.fields(resolution.ResolutionRequest)}
        assert fields == {"expected_revision", "resolutions"}
        answer_fields = {f.name for f in dataclasses.fields(type(request.resolutions[0]))}
        for forbidden in ("connection_id", "project_id", "decision", "output_status",
                          "verification_status", "artifact_path"):
            assert forbidden not in answer_fields, forbidden


# ===========================================================================
# B. THE WORKING DIRECTORY — validated before the claim, and creating nothing.
# ===========================================================================
class TestTheWorkingDirectoryPreflight:
    """A deployment that could not store an artifact must refuse while owning nothing."""

    def test_an_unset_working_directory_is_refused(self, monkeypatch):
        """The `None` case is the production one: no override was injected, so the value
        comes from the deployment's configuration, and unset means refused."""
        config = _app_config()
        monkeypatch.setattr(config, "ARTIFACT_WORKING_DIR", None)
        with pytest.raises(ArtifactRefused) as caught:
            resolution.require_artifact_working_directory(None)
        assert caught.value.code == ARTIFACT_REFUSED_NO_WORKING_DIR

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_a_blank_working_directory_is_refused(self, value):
        with pytest.raises(ArtifactRefused) as caught:
            resolution.require_artifact_working_directory(value)
        assert caught.value.code == ARTIFACT_REFUSED_NO_WORKING_DIR

    def test_a_relative_working_directory_is_refused(self):
        with pytest.raises(ArtifactRefused) as caught:
            resolution.require_artifact_working_directory("artifacts")
        assert caught.value.code == ARTIFACT_REFUSED_RELATIVE_WORKING_DIR

    def test_a_missing_directory_is_refused_rather_than_created(self, tmp_path):
        """J45's own layer CREATES it; this preflight runs before the claim and creates
        nothing, so a configured-but-absent directory is refused here."""
        missing = tmp_path / "not-made-yet"
        with pytest.raises(ArtifactRefused) as caught:
            resolution.require_artifact_working_directory(str(missing))
        assert caught.value.code == ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR
        assert not missing.exists(), "the preflight created the directory"

    def test_a_file_is_not_a_working_directory(self, tmp_path):
        target = tmp_path / "a-file"
        target.write_text("not a directory")
        with pytest.raises(ArtifactRefused) as caught:
            resolution.require_artifact_working_directory(str(target))
        assert caught.value.code == ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR

    def test_an_unwritable_directory_is_refused(self, tmp_path):
        import os
        import stat

        target = tmp_path / "read-only"
        target.mkdir()
        target.chmod(stat.S_IRUSR | stat.S_IXUSR)
        if os.access(target, os.W_OK):
            pytest.skip("this process can write to a 0500 directory (running as root)")
        try:
            with pytest.raises(ArtifactRefused) as caught:
                resolution.require_artifact_working_directory(str(target))
            assert caught.value.code == ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR
        finally:
            target.chmod(stat.S_IRWXU)

    def test_a_usable_directory_is_returned_absolute(self, working_dir):
        assert resolution.require_artifact_working_directory(str(working_dir)) == working_dir

    def test_the_route_refuses_before_the_claim_when_the_directory_is_unusable(
        self, monkeypatch, tmp_path
    ):
        """The ORDER, not the refusal. A request that could never store an artifact must
        not take a claim from a reviewer who could have used it."""
        wired = _Wired(monkeypatch, working_dir=str(tmp_path / "absent"))
        wired.set_outcome(None)
        with pytest.raises(ArtifactRefused):
            wired.run()
        assert wired.claims.acquires == [], "a claim was taken for a request that cannot store"
        assert wired.claims.releases == []
        assert wired.log.order == ()
        assert wired.review_store.calls == []
        # Requirement 50, stated as the live state it is about: a refusal that happens
        # before the claim cannot have left anything behind anywhere — no revision, no
        # object in the bucket, and no project pointed at one.
        assert wired.artifacts.buckets == [], "the storage client was reached at all"
        assert wired.artifacts.storage_object.uploads == [], "an artifact was uploaded"
        assert wired.pointer.updates == [], "a project was pointed at an artifact"

    def test_the_route_refuses_before_resolution_when_the_directory_is_unusable(
        self, monkeypatch, tmp_path, arkles_workflow
    ):
        wired = _Wired(monkeypatch, working_dir=str(tmp_path / "absent"))
        resolver = wired.set_outcome(None)
        with pytest.raises(ArtifactRefused):
            wired.run()
        assert resolver.calls == [], "a resolution ran with nowhere to put its artifact"


# ===========================================================================
# C. THE ORDER — the whole reason this route is safe.
# ===========================================================================
class TestTheOrder:
    def test_an_auto_outcome_does_everything_in_the_one_safe_order(self, wired):
        """claim -> resolve -> upload -> record -> pointer -> release."""
        wired.set_outcome(wired.auto_state())
        outcome = wired.run()

        assert wired.log.order == ("claim", "resolve", "upload", "record", "pointer", "release")
        assert outcome.decision == "AUTO"
        assert outcome.output_status == "GENERATED"
        assert outcome.verification_status == "VERIFIED"
        assert outcome.pointer_recorded is True
        assert outcome.claim_release == resolution.CLAIM_RELEASED

    def test_the_resolver_is_called_with_the_revision_the_resume_established(self, wired):
        """The resume's head IS the resolver's expected revision, so its own optimistic
        guard is exercised against the persisted head rather than against nothing."""
        resolver = wired.set_outcome(wired.auto_state())
        wired.run()
        assert resolver.calls[0]["expected_revision"] == 0
        assert resolver.calls[0]["package_id"] == PACKAGE
        assert [r.task_id for r in resolver.calls[0]["resolutions"]] == [TASK]

    def test_a_review_outcome_never_uploads_and_never_points(self, wired, monkeypatch):
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        outcome = wired.run()

        assert wired.log.order == ("claim", "resolve", "record", "release")
        assert wired.artifacts.storage_object.uploads == []
        assert wired.pointer.updates == []
        assert outcome.decision == "REVIEW"
        assert outcome.durable_artifact_path is None
        assert outcome.pointer_recorded is False
        assert outcome.generated_files == ()

    def test_a_confirm_outcome_never_uploads_and_never_points(self, wired, monkeypatch):
        state = _resolved_state(
            wired.workflow, decision="CONFIRM", output_status="BLOCKED_CONFIRMATION",
            verification_status=None,
        )
        wired.set_outcome(state)
        outcome = wired.run()

        assert wired.log.order == ("claim", "resolve", "record", "release")
        assert wired.artifacts.storage_object.uploads == []
        assert wired.pointer.updates == []
        assert outcome.decision == "CONFIRM"

    def test_the_revision_is_recorded_even_for_an_outcome_that_generated_nothing(
        self, wired, monkeypatch
    ):
        """The review HAPPENED. A connection that produced no artifact is still a resolved
        connection, and the record of the human decision is the point."""
        state = _resolved_state(
            wired.workflow, decision="CONFIRM", output_status="BLOCKED_CONFIRMATION",
            verification_status=None,
        )
        wired.set_outcome(state)
        outcome = wired.run()
        assert wired.review_store.recorded == [(PROJECT, 1, ("run-arkles",))]
        assert outcome.review_revision == 1

    def test_the_capture_run_ids_reach_the_record_verbatim(self, wired, monkeypatch):
        """The provenance the reconstruction stated, carried to J22 without a second read."""
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        wired.run()
        _, _, run_ids = wired.review_store.recorded[0]
        assert run_ids == ("run-arkles",)


# ===========================================================================
# D. THE CLAIM — taken after authentication and authorization, released always.
# ===========================================================================
class TestTheClaim:
    def test_a_refused_claim_reads_and_changes_nothing(self, monkeypatch, working_dir,
                                                       arkles_workflow):
        wired = _Wired(
            monkeypatch, working_dir=working_dir,
            claim_refusal=ClaimRefused(
                "CLAIM_REFUSED_ACTIVE", "another reviewer holds this project",
                lease_expires_at="2026-09-28T00:15:00+00:00",
            ),
        )
        with pytest.raises(ClaimRefused) as caught:
            wired.run()
        assert caught.value.code == "CLAIM_REFUSED_ACTIVE"
        assert wired.resolver is None, "no resolver outcome was ever installed"
        assert wired.log.order == ()
        assert wired.review_store.calls == []
        assert wired.claims.releases == [], "a claim that was never taken was released"

    def test_the_release_uses_the_exact_token_that_was_obtained(self, wired, monkeypatch):
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        wired.claims.token = "token-issued-by-this-request"
        wired.run()
        assert wired.claims.releases == [(PROJECT, "token-issued-by-this-request")]
        assert wired.claims.acquires[0][1] == OWNER

    def test_the_claim_is_released_when_the_resolution_itself_fails(self, wired, monkeypatch):
        def explode(*args, **kwargs):
            raise RuntimeError("the resolver broke")

        monkeypatch.setattr(resolution, "resolve_project_connection", explode)
        with pytest.raises(RuntimeError):
            wired.run()
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_the_claim_is_released_when_the_record_fails(self, monkeypatch, working_dir,
                                                         arkles_workflow):
        state = _resolved_state(
            arkles_workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired = _Wired(
            monkeypatch, working_dir=working_dir, state=state,
            store_fail=RuntimeError("the store broke"),
        )
        with pytest.raises(resolution.ResolutionRouteRefused):
            wired.run()
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    @pytest.mark.parametrize(
        "code", ["CLAIM_REFUSED_NOT_ACTIVE", "CLAIM_REFUSED_NOT_HOLDER"]
    )
    def test_a_release_that_was_already_free_is_recorded_rather_than_raised(
        self, wired, monkeypatch, code
    ):
        """Neither means this request may release something else: there is no second
        release and no release by holder."""
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        wired.claims.release_refusal = ClaimRefused(code, "the lease moved on")
        outcome = wired.run()
        assert outcome.claim_release == code
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_the_outcome_never_names_the_claim_token(self, wired, monkeypatch):
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        outcome = wired.run()
        assert "claim-token-1" not in json.dumps(outcome.to_response())

    def test_the_claim_is_taken_only_after_authorization_in_the_source(self):
        """The composition takes a binding, not a project id — so there is no argument
        that could resolve a connection of someone else's project. Authorization ran in
        `app.main` before this function was reached; this asserts the ROUTE's own order."""
        source = MAIN_PATH.read_text(encoding="utf-8")
        body = source[source.index("def production_connection_resolve"):]
        assert body.index("_authorized_project") < body.index("_bound_review")
        assert body.index("_bound_review") < body.index("resolve_production_connection")


# ===========================================================================
# E. TASK OWNERSHIP — validated before any answer is applied.
# ===========================================================================
class TestTaskOwnership:
    def test_a_task_of_another_connection_is_refused_before_the_resolver_runs(self, wired):
        resolver = wired.set_outcome(None)
        with pytest.raises(ResolutionTaskRefused) as caught:
            wired.run(body=_body(resolutions=[{
                "task_id": FOREIGN_TASK, "task_type": "COMPLETE_REVIEW",
                "answer_type": "APPROVE_REVIEW", "answer": True, "evidence": "",
            }]))
        assert caught.value.code == RESOLUTION_TASK_FOREIGN_CONNECTION
        assert resolver.calls == [], "a resolution ran with a foreign task"
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_a_task_of_no_connection_of_this_project_is_refused(self, wired):
        with pytest.raises(ResolutionTaskRefused) as caught:
            wired.run(body=_body(resolutions=[{
                "task_id": "RP-9999-T01", "task_type": "COMPLETE_REVIEW",
                "answer_type": "APPROVE_REVIEW", "answer": True, "evidence": "",
            }]))
        assert caught.value.code == RESOLUTION_TASK_NOT_OF_THIS_PROJECT

    def test_the_same_task_named_twice_is_refused(self, wired):
        """7AJ's own inline guard does not catch this; a request that answers one question
        twice is ambiguous rather than merely wrong."""
        entry = _body()["resolutions"][0]
        with pytest.raises(ResolutionTaskRefused) as caught:
            wired.run(body=_body(resolutions=[entry, copy.deepcopy(entry)]))
        assert caught.value.code == RESOLUTION_TASK_DUPLICATED

    def test_a_package_that_is_not_a_connection_is_refused(self, wired):
        with pytest.raises(ResolutionTaskRefused) as caught:
            wired.run(package_id="RP-9999")
        assert caught.value.code == RESOLUTION_TARGET_UNKNOWN

    def test_nothing_is_recorded_when_the_ownership_check_refuses(self, wired):
        with pytest.raises(ResolutionTaskRefused):
            wired.run(package_id="RP-9999")
        assert wired.review_store.recorded == []
        assert wired.artifacts.storage_object.uploads == []
        assert wired.pointer.updates == []


# ===========================================================================
# F. THE OUTCOMES — what a failed step means for every step after it.
# ===========================================================================
class TestTheOutcomes:
    def test_an_auto_whose_verification_failed_does_not_upload_or_point(
        self, wired, monkeypatch
    ):
        """7AG looked and did not verify it. No bytes are stored and the project is not
        pointed at anything — but the review is still recorded as what it was."""
        state = wired.auto_state(verified=False)
        wired.set_outcome(state)
        outcome = wired.run()

        assert wired.artifacts.storage_object.uploads == []
        assert wired.pointer.updates == []
        assert outcome.failures == (resolution.ARTIFACT_NOT_VERIFIED,)
        assert outcome.durable_artifact_path is None
        assert outcome.pointer_recorded is False
        assert wired.review_store.recorded, "the review itself was not recorded"

    def test_a_generation_failure_is_a_completed_review_that_delivers_nothing(
        self, wired, monkeypatch
    ):
        """7AF never produced a drawing. That is a FINISHED review, not an exception: it
        is recorded with the failure as its own output status, and nothing is stored."""
        from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATION_FAILED
        from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_NO_ARTIFACT

        # A partial file the generator wrote before it failed. It is on disk, and 7AF's
        # manifest names it — and it is still not a deliverable.
        partial = pathlib.Path(wired.working_dir) / f"{PACKAGE}-fabrication.pdf"
        partial.write_bytes(FABRICATION_PDF)
        state = _with_record(
            _resolved_state(
                wired.workflow, decision="AUTO",
                output_status=OUTPUT_STATUS_GENERATION_FAILED,
                verification_status=VERIFICATION_STATUS_NO_ARTIFACT,
                generated_files=(str(partial),),
                blockers=(OUTPUT_STATUS_GENERATION_FAILED,),
            ),
            # 7AG's own verdict for a dispatch that recorded GENERATION_FAILED: no
            # artifact, and NOT_VERIFIABLE is never substituted for it.
            verification=_Verification(
                verification_status=VERIFICATION_STATUS_NO_ARTIFACT, artifact_path=None,
            ),
        )
        wired.set_outcome(state)
        outcome = wired.run()

        assert wired.artifacts.storage_object.uploads == [], "a failed generation was stored"
        assert wired.pointer.updates == [], "the project was pointed at a failed generation"
        assert outcome.durable_artifact_path is None
        assert outcome.decision == "AUTO"
        assert outcome.output_status == OUTPUT_STATUS_GENERATION_FAILED
        assert outcome.blocker_codes == (OUTPUT_STATUS_GENERATION_FAILED,)
        assert outcome.generated_files == (), (
            "a local partial file was reported to the caller as a deliverable"
        )
        assert outcome.failures == (resolution.ARTIFACT_NOT_VERIFIED,)
        # The failure is a COMPLETED review. It was RECORDED — with its own status, and
        # not a success substituted for it — so the next request replays it instead of
        # generating again.
        assert wired.review_store.calls.count("record_project_review") == 1
        assert [(project, revision) for project, revision, _ in wired.review_store.recorded] == [
            (PROJECT, 1)
        ]
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_an_unreadable_verified_artifact_does_not_upload(self, wired, monkeypatch):
        """A VERIFIED manifest whose file cannot be read is a real state — the working
        directory can be cleaned between the verification and the upload."""
        state = wired.auto_state(artifact_path=str(wired.working_dir / "gone.pdf"))
        wired.set_outcome(state)
        outcome = wired.run()

        assert wired.artifacts.storage_object.uploads == []
        assert wired.pointer.updates == []
        assert outcome.failures == (resolution.ARTIFACT_READ_FAILED,)
        assert outcome.durable_artifact_path is None

    def test_an_upload_failure_records_the_review_and_points_at_nothing(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        wired = _Wired(
            monkeypatch, working_dir=working_dir,
            storage_error=RuntimeError("the bucket refused it"),
        )
        state = wired.auto_state()
        wired.set_outcome(state)
        outcome = wired.run()

        assert wired.artifacts.storage_object.uploads == []
        assert wired.pointer.updates == []
        assert outcome.failures == (resolution.ARTIFACT_UPLOAD_FAILED,)
        assert outcome.durable_artifact_path is None
        assert wired.review_store.recorded, "the review was lost with the upload"

    def test_a_pointer_failure_leaves_the_recorded_revision_standing(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        """The revision was already correct; a pointer that failed is not repaired by
        rewriting the revision, and no second revision is invented."""
        wired = _Wired(
            monkeypatch, working_dir=working_dir,
            pointer_error=RuntimeError("the update did not land"),
        )
        state = wired.auto_state()
        wired.set_outcome(state)
        outcome = wired.run()

        assert outcome.pointer_recorded is False
        assert outcome.failures == (resolution.POINTER_WRITE_FAILED,)
        assert outcome.durable_artifact_path == durable_object_path(OWNER, PROJECT, PACKAGE)
        assert len(wired.review_store.recorded) == 1, "a second revision was written"
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_a_pointer_refusal_is_reported_as_a_failed_pointer_not_a_success(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        wired = _Wired(monkeypatch, working_dir=working_dir)
        state = wired.auto_state()
        wired.set_outcome(state)
        wired.review_store.fail = None
        outcome = wired.run()
        assert outcome.pointer_recorded is True
        assert outcome.failures == ()

    def test_a_record_failure_after_a_successful_upload_names_the_orphan(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        """The upload returned and then the revision could not be recorded. The durable
        artifact EXISTS and is stated, so a caller learns of a stored-but-unpointed
        object rather than assuming nothing was written."""
        wired = _Wired(
            monkeypatch, working_dir=working_dir,
            store_fail=RuntimeError("the revision was not recorded"),
        )
        state = wired.auto_state()
        wired.set_outcome(state)
        with pytest.raises(resolution.ResolutionRouteRefused) as caught:
            wired.run()

        assert caught.value.code == resolution.RESOLUTION_REFUSED_NOT_PERSISTED
        assert caught.value.durable_artifact_path == durable_object_path(
            OWNER, PROJECT, PACKAGE
        )
        assert wired.pointer.updates == [], "the project was pointed at an unrecorded review"
        assert wired.artifacts.storage_object.uploads[0][0] == durable_object_path(
            OWNER, PROJECT, PACKAGE
        )

    def test_a_skipped_pointer_after_a_record_failure_is_not_reported_as_done(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        wired = _Wired(
            monkeypatch, working_dir=working_dir,
            store_fail=RuntimeError("nope"),
        )
        state = wired.auto_state()
        wired.set_outcome(state)
        with pytest.raises(resolution.ResolutionRouteRefused):
            wired.run()
        assert wired.pointer.updates == []
        assert wired.log.order == ("claim", "resolve", "upload", "release")

    def test_the_stored_bytes_are_the_verified_bytes(self, wired, monkeypatch):
        """The route reads the file 7AG's OWN manifest names, and uploads those bytes."""
        state = wired.auto_state()
        wired.set_outcome(state)
        wired.run()
        path, content, options = wired.artifacts.storage_object.uploads[0]
        assert content == FABRICATION_PDF
        assert options["upsert"] == "true"
        assert path == durable_object_path(OWNER, PROJECT, PACKAGE)

    def test_the_local_path_never_reaches_the_response(self, wired, monkeypatch):
        """A local working path is where the artifact was BUILT. What a caller may act on
        is what was STORED, and the response carries only that."""
        state = wired.auto_state()
        wired.set_outcome(state)
        outcome = wired.run()
        rendered = json.dumps(outcome.to_response())
        assert str(wired.working_dir) not in rendered
        assert "-fabrication.pdf" not in rendered
        assert outcome.generated_files == (durable_object_path(OWNER, PROJECT, PACKAGE),)

    def test_the_pointer_is_written_with_the_durable_identity_and_only_that(
        self, wired, monkeypatch
    ):
        state = wired.auto_state()
        wired.set_outcome(state)
        wired.run()
        column, values, row = wired.pointer.updates[0]
        assert column == "id"
        assert row == PROJECT
        assert values == {
            "fab_drawings_pdf_path": durable_object_path(OWNER, PROJECT, PACKAGE),
        }


# ===========================================================================
# G. THE REVISION BARRIER — the one check that decides the record.
# ===========================================================================
class TestTheRevisionBarrier:
    def test_a_revision_that_moved_during_the_request_refuses_the_write(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        """The re-read is the strongest ownership question this route can ask, and it is
        the one that decides the record."""
        state = _resolved_state(
            arkles_workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired = _Wired(
            monkeypatch, working_dir=working_dir, state=state,
            revision_override=4,
        )
        with pytest.raises(resolution.ResolutionRouteRefused) as caught:
            wired.run()
        assert caught.value.code == resolution.RESOLUTION_REFUSED_CONCURRENT_REVISION
        assert wired.review_store.recorded == []
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_a_project_with_no_recorded_revision_is_at_revision_zero(self, wired):
        """`latest_recorded_revision` answers None when nothing is recorded, and the route
        normalizes that absence to the 0 it means before comparing — so an empty chain and
        a chain at revision 0 are not told apart by the re-read, which is the point: the
        re-read asks whether the chain MOVED, not how it is spelled."""
        empty = _ResolutionStore(_Log(), head=None)
        at_zero = _ResolutionStore(_Log(), head=0)
        assert empty.latest_recorded_revision(PROJECT) is None
        assert at_zero.latest_recorded_revision(PROJECT) == 0
        # The route reads both as the same head, so a project that has recorded nothing
        # is not mistaken for one whose chain moved.
        assert (empty.latest_recorded_revision(PROJECT) or 0) == 0
        assert (at_zero.latest_recorded_revision(PROJECT) or 0) == 0

    def test_a_re_read_that_still_answers_none_is_not_a_moved_chain(self, monkeypatch,
                                                                   working_dir,
                                                                   arkles_workflow):
        """The re-read compares the normalized absence, so a store whose chain is empty
        passes it — and is refused by J22's OWN builder one line later, not by the
        re-read. See `TestTheChainContradiction` for why that refusal is unavoidable."""
        state = _resolved_state(
            arkles_workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired = _Wired(monkeypatch, working_dir=working_dir, state=state, head=None)
        with pytest.raises(resolution.ResolutionRouteRefused) as caught:
            wired.run()
        assert caught.value.code == resolution.RESOLUTION_REFUSED_CONCURRENT_REVISION
        assert "recorded review chain is at None" in caught.value.statement
        assert wired.review_store.recorded == []

    def test_a_stale_expected_revision_is_refused_before_the_resolver(
        self, wired
    ):
        resolver = wired.set_outcome(None)
        with pytest.raises(ResumptionRefused) as caught:
            wired.run(body=_body(expected_revision=3))
        assert caught.value.code == RESUMPTION_EXPECTED_REVISION_AHEAD
        assert resolver.calls == []
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    def test_a_revision_gap_at_the_write_is_a_conflict_not_a_fault(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        """J22's own gap refusal means another writer recorded between this request's
        re-read and its write — the race the re-read narrows and the write itself closes.
        It is stated as a conflict so a caller retries it."""
        from app.cad_engine.connection_review_snapshot import SnapshotRefused

        state = _resolved_state(
            arkles_workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired = _Wired(
            monkeypatch, working_dir=working_dir, state=state,
            store_fail=SnapshotRefused("SNAPSHOT_REFUSED_REVISION_GAP", "the chain moved"),
        )
        with pytest.raises(resolution.ResolutionRouteRefused) as caught:
            wired.run()
        assert caught.value.code == resolution.RESOLUTION_REFUSED_CONCURRENT_REVISION


# ===========================================================================
# G2. THE FINDING — J22's writer and J46's reader cannot both be satisfied.
# ===========================================================================
class TestTheChainContradiction:
    """THE FINDING OF THIS MILESTONE, proved over the genuine modules.

    THE TWO RULES
    -------------
    J22, `build_review_snapshot`: the revision recorded is the workflow's own revision,
    and it must be exactly the one revision the project's recorded chain admits —
    `expected = 0 if previous_revision is None else previous_revision + 1`. 7AJ's
    `resolve_project_connection` returns the next state at `revision + 1`, so the FIRST
    resolve of any project produces revision 1. For J22 to accept it, the project's chain
    must already be at revision 0 — so SOMETHING must have persisted revision 0.

    J46, `validate_history`: "The chain starts at 1 because revision 0 is the
    RECONSTRUCTED state: it is produced by J24A from the persisted capture and is not
    itself a recorded review revision." It raises `RESUMPTION_HISTORY_GAP` for any chain
    containing a recorded revision below 1. So a chain that CONTAINS revision 0 is
    precisely the chain J46 will not read.

    THE CONTRADICTION
    -----------------
    The chain J22's writer requires is the chain J46's reader refuses. Neither module is
    wrong on its own terms — they disagree about whether revision 0 is a RECORDED review
    revision — and J47 is forbidden from changing either (a J22 semantic change and a
    substantive J46 replay change are both HARD STOP items). So the route cannot record a
    review on ANY project: with an empty chain J22 refuses the first resolve, and with a
    chain J46 can read, J22 has already refused every revision in it.

    THE CONSEQUENCE FOR THIS ROUTE
    ------------------------------
    Every success-path test in this file runs against a store whose `head` is 0 — the
    state J22's writer requires and J46's reader cannot produce. That is stated here
    rather than hidden: the route's own logic is fully exercised, and the ONE thing the
    route cannot do against real persisted evidence is write the first revision. The
    milestone is therefore reported as `J47_FINDINGS`, not `J47_READY_...`.
    """

    def test_j46_refuses_every_chain_that_contains_a_recorded_revision_zero(self):
        for chain in ((0,), (0, 1), (0, 1, 2)):
            with pytest.raises(ResumptionRefused) as caught:
                validate_history(chain)
            assert caught.value.code == RESUMPTION_HISTORY_GAP, chain

    def test_j46_reads_only_the_chains_that_start_at_one(self):
        assert validate_history(()) == ()
        assert validate_history((1,)) == (1,)
        assert validate_history((1, 2)) == (1, 2)

    def test_j22_refuses_the_first_resolve_unless_revision_zero_is_recorded(
        self, arkles_workflow
    ):
        """The other half, from the genuine builder: 7AJ returns revision + 1, so the
        first resolve is revision 1, and revision 1 against an empty chain is a gap."""
        from app.cad_engine.connection_review_snapshot import (
            SNAPSHOT_REFUSED_REVISION_GAP,
            SnapshotRefused,
        )

        first = _resolved_state(
            arkles_workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        assert first.revision == 1, "7AJ returns the next state, and the first is 1"

        rows = {"steel_members": [], "connections": []}
        with pytest.raises(SnapshotRefused) as caught:
            build_review_snapshot(
                first, project_id=PROJECT, evidence_rows=rows, previous_revision=None,
            )
        assert caught.value.code == SNAPSHOT_REFUSED_REVISION_GAP

        # ...and the ONLY chain it will accept revision 1 against is the one J46 above
        # refused: a chain already recorded at revision 0.
        recorded = build_review_snapshot(
            first, project_id=PROJECT, evidence_rows=rows, previous_revision=0,
        )
        assert recorded.review_revision == 1

    def test_the_two_rules_admit_no_chain_together(self, arkles_workflow):
        """Stated as the conjunction it is: for each state a real project can be in, one
        of the two modules refuses. There is no third state."""
        rows = {"steel_members": [], "connections": []}
        first = _resolved_state(
            arkles_workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )

        # State A — the project has recorded nothing (J22's reader answers None, and a
        # reconstruction has no history). J46 reads it; J22 refuses the write.
        assert validate_history(()) == ()
        with pytest.raises(ValueError):
            build_review_snapshot(
                first, project_id=PROJECT, evidence_rows=rows, previous_revision=None,
            )

        # State B — revision 0 IS recorded, so J22 accepts the write. That chain is the
        # one J46 refuses to read, so the project can never be resumed again.
        build_review_snapshot(
            first, project_id=PROJECT, evidence_rows=rows, previous_revision=0,
        )
        with pytest.raises(ResumptionRefused) as caught:
            validate_history((0, 1))
        assert caught.value.code == RESUMPTION_HISTORY_GAP


# ===========================================================================
# G3. THE HISTORY — a recorded outcome is REPLAYED by J46, never regenerated.
# ===========================================================================
def _resume_over(state, *, previous_revision=0, project_id=PROJECT):
    """A GENUINE resume of a project whose entire history is one recorded revision.

    The revision is written by J22's own builder and read back by J46's own reader, over
    the same Arkles reconstruction every other test uses, so what comes out is the
    projection the persisted record actually carries — not a state this file assembled.
    """
    from app.production_review.project_workflow_resumption import resume_project_workflow

    snapshot = build_review_snapshot(
        state, project_id=project_id,
        evidence_rows={"steel_members": [], "connections": []},
        previous_revision=previous_revision,
    )
    resumed = resume_project_workflow(
        project_id,
        section_matcher=j24a._StubMatcher(),
        expected_revision=previous_revision + 1,
        repository=_uuid_store(project_id),
        history=(snapshot,),
    )
    assert resumed is not None
    return resumed, snapshot


class TestTheHistory:
    """Requirements 31 and 32: a historical failure is replayed, never regenerated.

    Two things have to be true for "replayed" to mean anything, and both are asserted:
    the resumed project state carries the recorded failure VERBATIM, and the connection it
    belongs to cannot be put through generation a second time — 7AJ's own one-shot guard
    reads the `last_processed_revision` J46 restored from the record and refuses.
    """

    def _recorded(self, workflow, *, output_status, verification_status, blockers=()):
        state = _with_record(
            _resolved_state(
                workflow, decision="AUTO", output_status=output_status,
                verification_status=verification_status, blockers=blockers,
            ),
            verification=_Verification(
                verification_status=verification_status, artifact_path=None,
            ),
        )
        return _resume_over(state)

    @staticmethod
    def _position(workflow):
        return [c.package_id for c in workflow.connections].index(PACKAGE)

    def test_a_recorded_generation_failure_is_replayed_and_not_regenerated(
        self, arkles_workflow
    ):
        from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATION_FAILED
        from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_NO_ARTIFACT

        resumed, snapshot = self._recorded(
            arkles_workflow,
            output_status=OUTPUT_STATUS_GENERATION_FAILED,
            verification_status=VERIFICATION_STATUS_NO_ARTIFACT,
            blockers=(OUTPUT_STATUS_GENERATION_FAILED,),
        )
        index = self._position(resumed.workflow)
        replayed = resumed.workflow.connections[index]

        assert resumed.head_revision == 1
        assert replayed.decision == "AUTO"
        assert replayed.output_status == OUTPUT_STATUS_GENERATION_FAILED
        assert replayed.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
        assert replayed.blockers == (OUTPUT_STATUS_GENERATION_FAILED,)
        assert replayed.generated_files == ()

        # REGENERATED would leave a stage behind — a 7AA pipeline, a 7AF dispatch, a 7AG
        # verification. The advanced connection's record is assembled EMPTY, because
        # those objects were never persisted and J46 invents nothing it did not read.
        # There is nothing here that could have re-run a generator.
        record = resumed.workflow.connection_records[index]
        assert (
            record.pipeline, record.rerun_outcome, record.gate_result,
            record.dispatch_result, record.verification_result,
        ) == (None, None, None, None, None)

    def test_a_recorded_verification_failure_is_replayed_and_not_reverified(
        self, arkles_workflow
    ):
        from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_FAILED

        # A verification failure is NOT a generation failure: 7AF produced a file and
        # 7AG looked at it and did not accept it. The recorded manifest still names the
        # artifact — and the replayed state still records the failure.
        resumed, snapshot = self._recorded(
            arkles_workflow, output_status="GENERATED",
            verification_status=VERIFICATION_STATUS_FAILED, blockers=("ARTIFACT_CHECK_FAILED",),
        )
        index = self._position(resumed.workflow)
        replayed = resumed.workflow.connections[index]

        assert replayed.output_status == "GENERATED"
        assert replayed.verification_status == VERIFICATION_STATUS_FAILED
        assert replayed.blockers == ("ARTIFACT_CHECK_FAILED",)
        assert resumed.workflow.connection_records[index].verification_result is None, (
            "the replayed state carries a verification RESULT; nothing re-verified"
        )

    def test_a_recorded_failure_cannot_be_put_through_generation_a_second_time(
        self, arkles_workflow, tmp_path
    ):
        """The one-shot guard, over the genuine resolver, on a replayed failure.

        This is what makes "do not blindly retry generation on the next request" true: a
        new request for the same connection resumes a state whose `last_processed_revision`
        is already set, and 7AJ refuses it before any pipeline runs.
        """
        from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATION_FAILED
        from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_NO_ARTIFACT
        from app.cad_engine.project_workflow import (
            ConnectionAlreadyProcessedError,
            resolve_project_connection,
        )

        resumed, snapshot = self._recorded(
            arkles_workflow,
            output_status=OUTPUT_STATUS_GENERATION_FAILED,
            verification_status=VERIFICATION_STATUS_NO_ARTIFACT,
            blockers=(OUTPUT_STATUS_GENERATION_FAILED,),
        )
        replayed = resumed.workflow.connections[self._position(resumed.workflow)]
        assert replayed.last_processed_revision == 1, (
            "a replayed revision must restore the revision it was processed at, or the "
            "one-shot guard below could never fire"
        )

        request = resolution.parse_resolution_request(_body(expected_revision=1))
        with pytest.raises(ConnectionAlreadyProcessedError) as caught:
            resolve_project_connection(
                resumed.workflow,
                package_id=PACKAGE,
                resolutions=list(request.resolutions),
                output_dir=str(tmp_path / "never-used"),
                expected_revision=1,
            )
        assert "already processed at revision 1" in str(caught.value)


# ===========================================================================
# G4. THE INDEPENDENCE — a request needs nothing from the previous process.
# ===========================================================================
class TestTheIndependence:
    """Requirement 30: state comes from the reconstruction and the record, and nowhere
    else. Two independently built compositions that are given the same inputs answer the
    same, and the route module holds no state between them."""

    #: Names a module may hold at module level without holding state: an immutable
    #: literal is a constant. Everything the route module defines is one of these.
    IMMUTABLE = (ast.Constant, ast.Tuple)

    def test_the_route_module_holds_no_module_level_mutable_state(self):
        """A module-global dict, list or set — or a rebound module-level name — is the
        shape an in-memory workflow dependency would take. There is none, so there is
        nothing an earlier request could have left behind for a later one to read."""
        tree = ast.parse(MODULE_PATH.read_text())
        mutable = []
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if "__all__" in names:
                # The module's export list: a list by convention, and a constant by
                # content — it names the exports above and holds no request's data.
                continue
            if isinstance(value, self.IMMUTABLE):
                continue
            mutable.append(value)
        assert mutable == [], (
            "module-level state a later request could read: "
            f"{[ast.unparse(node) for node in mutable]}"
        )

    def test_two_independent_compositions_answer_identically(self, monkeypatch, tmp_path):
        """Two compositions, built separately, sharing nothing, answering the same.

        The second request is not a continuation of the first: it has its own store, its
        own claim client, its own pointer and its own working directory, and its store
        holds no history — yet it resumes at the same revision and produces the same
        outcome, because everything it needed came from the reconstruction and the record.
        """
        built = []
        for index in range(2):
            directory = tmp_path / f"working-{index}"
            directory.mkdir()
            wired = _Wired(monkeypatch, working_dir=directory)
            wired.set_outcome(wired.auto_state())
            built.append((wired, wired.run()))

        (first_wired, first), (second_wired, second) = built
        for attribute in ("store", "review_store", "claims", "pointer", "artifacts"):
            assert getattr(first_wired, attribute) is not getattr(second_wired, attribute), (
                f"the two requests shared their {attribute}; the second could have read "
                "the first's work"
            )
        assert first_wired.working_dir != second_wired.working_dir
        assert first == second, "the same request answered differently the second time"
        assert first.review_revision == 1, (
            "the second request did not inherit the first's revision"
        )
        assert first.to_response() == second.to_response()
        # ...and the response carries no absolute path from either composition's own
        # working directory, which is the one thing that DOES differ between them.
        body = json.dumps(first.to_response())
        assert "working-0" not in body and "working-1" not in body
        assert str(tmp_path) not in body


# ===========================================================================
# H. THE HTTP SURFACE — statuses, refusals, and what the response may contain.
# ===========================================================================
class _Http:
    """The real application, the real route, and authenticated clients.

    The COMPOSITION is doubled here on purpose, and only the composition: this section is
    about the ROUTE's mapping from a stated refusal to a status, and the composition's own
    behaviour is section C-H above. Doubling it is what keeps this section from performing
    a real claim, a real upload and a real revision just to look at a status code.
    """

    def __init__(self, production, monkeypatch, *, projects, owner=OWNER, store=None):
        self.production = production
        self.main = production.main
        self.store = store if store is not None else _uuid_store()
        self.projects = projects
        monkeypatch.setattr(production.main, "supabase", _ProjectsClient(projects))
        for name in j24a.ALLOWED_READS:
            monkeypatch.setattr(production.repository, name, getattr(self.store, name))
        tokens = auth.install(monkeypatch, production)
        from fastapi.testclient import TestClient

        self.tokens = tokens
        self.owner = owner
        self.http = TestClient(production.main.app, headers=tokens.headers(OWNER))
        self.bare = TestClient(production.main.app)
        self.other = TestClient(production.main.app, headers=tokens.headers(OTHER_OWNER))
        self.url = ROUTE_PATH.replace("{project_id}", PROJECT).replace("{package_id}", PACKAGE)


class _ProjectQuery:
    """One `projects` statement, addressed by id.

    The HTTP layer makes exactly two statements about `projects` and no others: the read
    `_authorized_project` performs to authorize the request, and — when the composition
    is NOT doubled — the J47 pointer writer's single-column update. Both answer from the
    rows this client was given, so the shape of the answer (`data` is a row for a
    `.single()` read and a LIST for an update, empty when nothing matched) is the real
    client's shape rather than a convenience.
    """

    def __init__(self, rows):
        self.rows = rows
        self.project_id = None
        self.values = None

    def select(self, *columns):
        assert self.values is None, "a statement cannot both read and write"
        self.columns = columns
        return self

    def update(self, values):
        self.values = values
        return self

    def eq(self, column, value):
        assert column == "id", column
        self.project_id = value
        return self

    def single(self):
        return self

    def execute(self):
        row = self.rows.get(self.project_id)
        if self.values is None:
            return types.SimpleNamespace(data=row)
        if row is None:
            return types.SimpleNamespace(data=[])
        row.update(self.values)
        return types.SimpleNamespace(data=[dict(row)])


class _ProjectsClient:
    """`supabase`, for the tables this route may touch: `projects`, and nothing else.

    Every other table is an assertion failure rather than a silently answered query —
    the HTTP layer reads the row it authorizes and writes the one pointer, and the
    composition's own reads happen through the repository it is given.
    """

    def __init__(self, rows):
        self.rows = rows
        self.tables_read = []

    def table(self, name):
        self.tables_read.append(name)
        assert name == "projects", f"the composition read {name!r} itself"
        return _ProjectQuery(self.rows)


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

    monkeypatch.setattr(main, "resolve_production_connection", explode)


class TestTheHttpSurface:
    def test_no_credential_is_401(self, http):
        assert http.bare.post(http.url, json=_body()).status_code == 401

    def test_another_reviewers_credential_is_403(self, http):
        assert http.other.post(http.url, json=_body()).status_code == 403

    def test_an_unknown_project_is_404(self, http):
        url = http.url.replace(PROJECT, "12121212-3434-4545-8686-787878787878")
        assert http.http.post(url, json=_body()).status_code == 404

    def test_a_malformed_body_is_422_and_reaches_no_composition(self, http, monkeypatch):
        called = []
        monkeypatch.setattr(
            http.main, "resolve_production_connection",
            lambda **kwargs: called.append(kwargs),
        )
        response = http.http.post(http.url, json={"expected_revision": 0, "wat": 1})
        assert response.status_code == 422
        assert response.json()["detail"]["refusal"] == resolution.INPUT_REFUSED_UNKNOWN_FIELD
        assert called == []

    @pytest.mark.parametrize("field", ["project_id", "connection_id", "claim_token",
                                       "artifact_path", "decision", "verification_status"])
    def test_a_server_owned_field_is_422_over_http(self, http, field):
        response = http.http.post(http.url, json=_body(**{field: "x"}))
        assert response.status_code == 422
        assert response.json()["detail"]["refusal"] == (
            resolution.INPUT_REFUSED_SERVER_OWNED_FIELD
        )

    def test_a_refused_claim_is_409_and_names_no_holder(self, http, monkeypatch):
        _route_raises(http.main, monkeypatch, ClaimRefused(
            "CLAIM_REFUSED_ACTIVE", "another reviewer holds this project",
            lease_expires_at="2026-09-28T00:15:00+00:00",
        ))
        response = http.http.post(http.url, json=_body())
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["refusal"] == "CLAIM_REFUSED_ACTIVE"
        assert detail["lease_expires_at"] == "2026-09-28T00:15:00+00:00"
        assert "holder" not in json.dumps(detail).lower().replace("another reviewer holds", "")

    def test_a_stale_revision_is_409(self, http, monkeypatch):
        _route_raises(http.main, monkeypatch, ResumptionRefused(
            RESUMPTION_EXPECTED_REVISION_STALE, "the chain moved on",
        ))
        assert http.http.post(http.url, json=_body()).status_code == 409

    def test_a_foreign_task_is_422(self, http, monkeypatch):
        _route_raises(http.main, monkeypatch, ResolutionTaskRefused(
            RESOLUTION_TASK_FOREIGN_CONNECTION, "that task is another's",
        ))
        assert http.http.post(http.url, json=_body()).status_code == 422

    @pytest.mark.parametrize("code,status", [
        (resolution.RESOLUTION_REFUSED_PROJECT_UNKNOWN, 404),
        (resolution.RESOLUTION_REFUSED_NO_SUCH_CONNECTION, 404),
        (resolution.RESOLUTION_REFUSED_NO_REVIEW_LAYER, 409),
        (resolution.RESOLUTION_REFUSED_CONCURRENT_REVISION, 409),
        (resolution.RESOLUTION_REFUSED_NOT_PERSISTED, 500),
    ])
    def test_each_route_refusal_has_its_own_status(self, http, monkeypatch, code, status):
        _route_raises(http.main, monkeypatch, resolution.ResolutionRouteRefused(code, "stated"))
        response = http.http.post(http.url, json=_body())
        assert response.status_code == status
        assert response.json()["detail"]["refusal"] == code

    def test_an_unusable_working_directory_is_500(self, http, monkeypatch):
        _route_raises(http.main, monkeypatch, ArtifactRefused(
            ARTIFACT_REFUSED_NO_WORKING_DIR, "nothing is configured",
        ))
        response = http.http.post(http.url, json=_body())
        assert response.status_code == 500
        assert response.json()["detail"]["refusal"] == ARTIFACT_REFUSED_NO_WORKING_DIR

    def test_a_stale_workflow_is_409(self, http, monkeypatch):
        _route_raises(http.main, monkeypatch, project_workflow.StaleProjectWorkflowError("stale"))
        assert http.http.post(http.url, json=_body()).status_code == 409

    def test_an_already_processed_connection_is_409(self, http, monkeypatch):
        _route_raises(http.main, monkeypatch, project_workflow.ConnectionAlreadyProcessedError("done"))
        assert http.http.post(http.url, json=_body()).status_code == 409

    def test_an_answer_the_contract_refuses_is_422(self, http, monkeypatch):
        """The existing exception-resolution contract raises a plain ValueError; every
        named refusal above is itself a ValueError, which is why that clause is last."""
        _route_raises(http.main, monkeypatch, ValueError("that answer type is not this task's"))
        response = http.http.post(http.url, json=_body())
        assert response.status_code == 422
        assert response.json()["detail"]["refusal"] == "RESOLUTION_REFUSED_BY_THE_CONTRACT"

    def test_a_successful_resolution_is_200_with_the_outcome(self, http, monkeypatch):
        outcome = resolution.ProductionResolutionOutcome(
            project_id=PROJECT, review_revision=1, review_package_id=PACKAGE,
            connection_id="C-1", decision="AUTO", output_status="GENERATED",
            verification_status="VERIFIED",
            generated_files=(durable_object_path(OWNER, PROJECT, PACKAGE),),
            blocker_codes=(), warning_codes=(),
            durable_artifact_path=durable_object_path(OWNER, PROJECT, PACKAGE),
            pointer_recorded=True, claim_release=resolution.CLAIM_RELEASED,
        )
        monkeypatch.setattr(
            http.main, "resolve_production_connection", lambda **kwargs: outcome,
        )
        response = http.http.post(http.url, json=_body())
        assert response.status_code == 200
        assert response.json()["review_revision"] == 1
        assert response.json()["pointer_recorded"] is True

    def test_the_route_passes_only_the_authorized_scope_to_the_composition(
        self, http, monkeypatch
    ):
        """The binding is the authorization proof; the package, the request and — since
        E2E-002G — the document scope are the only other things the composition is given.
        No project id, no reviewer, no path.

        The scope is not a fourth identity: it is the caller's own statement of WHICH
        document the review is about, already checked against this project's documents
        before the call. The list stays exact, so a further argument still has to be
        declared here."""
        seen = {}

        def capture(**kwargs):
            seen.update(kwargs)
            raise resolution.ResolutionRouteRefused(
                resolution.RESOLUTION_REFUSED_PROJECT_UNKNOWN, "stop here",
            )

        monkeypatch.setattr(http.main, "resolve_production_connection", capture)
        http.http.post(http.url, json=_body())
        assert sorted(seen) == ["binding", "document_id", "package_id", "request"]
        assert seen["package_id"] == PACKAGE
        assert seen["binding"].project_id == PROJECT

    def test_the_response_never_carries_a_local_path_or_a_token(self, http, monkeypatch):
        outcome = resolution.ProductionResolutionOutcome(
            project_id=PROJECT, review_revision=1, review_package_id=PACKAGE,
            connection_id=None, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None, generated_files=(), blocker_codes=(), warning_codes=(),
            durable_artifact_path=None, pointer_recorded=False,
            claim_release=resolution.CLAIM_RELEASED,
        )
        monkeypatch.setattr(
            http.main, "resolve_production_connection", lambda **kwargs: outcome,
        )
        rendered = json.dumps(http.http.post(http.url, json=_body()).json())
        for forbidden in ("claim", "token", "/tmp", "/Users", "/private", ".pdf"):
            assert forbidden not in rendered.replace("claim_release", ""), forbidden


# ===========================================================================
# I. THE POINTER WRITER — one column, and only the durable identity.
# ===========================================================================
class TestThePointerWriter:
    def test_a_local_path_is_refused_rather_than_stored(self):
        """The mistake this rule exists to prevent: pointing a project at the file the
        generator WROTE instead of the object that was STORED."""
        from app.production_fabrication_pointer import record_fabrication_pointer

        with pytest.raises(PointerRefused) as caught:
            record_fabrication_pointer(
                user_id=OWNER, project_id=PROJECT, connection_id=PACKAGE,
                durable_path=f"{PACKAGE}-fabrication.pdf", client=_PointerClient(_Log()),
            )
        assert caught.value.code == "POINTER_REFUSED_NOT_THE_DURABLE_IDENTITY"

    def test_a_missing_path_is_refused(self):
        from app.production_fabrication_pointer import record_fabrication_pointer

        with pytest.raises(PointerRefused):
            record_fabrication_pointer(
                user_id=OWNER, project_id=PROJECT, connection_id=PACKAGE,
                durable_path=None, client=_PointerClient(_Log()),
            )

    def test_an_update_that_matched_no_row_is_not_a_pointer_that_was_written(self):
        """PostgREST answers an UPDATE that matched nothing with an empty list and no
        error at all, so the check is the difference between a pointer and a claim."""
        from app.production_fabrication_pointer import record_fabrication_pointer

        client = _PointerClient(_Log(), matched=False)
        with pytest.raises(PointerRefused) as caught:
            record_fabrication_pointer(
                user_id=OWNER, project_id=PROJECT, connection_id=PACKAGE,
                durable_path=durable_object_path(OWNER, PROJECT, PACKAGE), client=client,
            )
        assert caught.value.code == "POINTER_REFUSED_PROJECT_UNKNOWN"

    def test_a_matched_update_returns_the_identity_that_was_stored(self):
        from app.production_fabrication_pointer import record_fabrication_pointer

        client = _PointerClient(_Log())
        stored = record_fabrication_pointer(
            user_id=OWNER, project_id=PROJECT, connection_id=PACKAGE,
            durable_path=durable_object_path(OWNER, PROJECT, PACKAGE), client=client,
        )
        assert stored == durable_object_path(OWNER, PROJECT, PACKAGE)
        assert client.updates[0][1] == {"fab_drawings_pdf_path": stored}

    def test_it_owns_one_mutation_and_no_second_storage_path(self):
        source = POINTER_PATH.read_text(encoding="utf-8")
        code = _code_only(POINTER_PATH)
        for forbidden in ("upload", "generate", "verify", "bucket", "storage",
                          "insert(", "delete(", "upsert("):
            assert forbidden not in code, forbidden
        assert "update(" in code
        # The one column and the one table, named as constants so no caller substitutes.
        assert 'POINTER_COLUMN = "fab_drawings_pdf_path"' in source
        assert 'POINTER_TABLE = "projects"' in source


# ===========================================================================
# J. THE SOURCE — the route composes the existing authorities and re-implements none.
# ===========================================================================
def _function_source(path, name):
    """One function's own source text, comments and docstrings included.

    The ORDER of two statements inside a function is not something a behavioural test can
    see when both are correct; it is something the source states. Mutating this text and
    re-evaluating the same predicate is what shows the predicate discriminates.
    """
    source = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} is not defined in {path}")


def _before(text, first, second):
    """True when `first` is mentioned before `second` in `text`."""
    return text.index(first) < text.index(second)


def _swap(text, first, second):
    """`text` with the two named statements exchanged, for a mutation test."""
    lines = text.splitlines(keepends=True)
    a = next(i for i, line in enumerate(lines) if _mentions(line, first))
    b = next(i for i, line in enumerate(lines) if _mentions(line, second))
    lines[a], lines[b] = lines[b], lines[a]
    return "".join(lines)


def _mentions(line, name):
    """A line that CALLS `name` — matched as a call so a docstring mention is not one."""
    return f"{name}(" in line and not line.lstrip().startswith(("#", '"', "'", "*"))


def _code_only(path):
    """The module's code with comments and docstrings removed.

    Prose that DESCRIBES what must not be used is not a use of it: the docstrings here
    name `pypdf`, `anthropic` and a local path precisely in order to say what this module
    does not do. The guard is over the executable text.
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
        # Whitespace is not a token, so a separator has to be reinserted — but only where
        # Python would need one. Without this, `update(` would read as `update (` and a
        # guard over the CALL would never match the call it is guarding.
        if pieces and pieces[-1][-1:] not in ("", "\n") and (
            pieces[-1][-1].isalnum() or pieces[-1][-1] == "_"
        ) and (text[:1].isalnum() or text[:1] == "_"):
            pieces.append(" ")
        pieces.append(text)
    return "".join(pieces)


class TestTheSource:
    def test_the_route_calls_the_existing_resolver_and_never_a_generator(self):
        code = _code_only(MODULE_PATH)
        assert "resolve_project_connection" in code
        for forbidden in ("dispatch_fabrication_drawing", "verify_drawing_artifact",
                          "generate_fabrication_drawing", "output_gate"):
            assert forbidden not in code, forbidden

    def test_the_route_calls_no_ai_and_reads_no_document(self):
        """The production review works from already persisted extraction evidence."""
        code = _code_only(MODULE_PATH)
        for forbidden in ("anthropic", "openai", "pypdf", "fitz", "pdf2image",
                          "subprocess", "extract", "requests.", "httpx"):
            assert forbidden not in code, forbidden

    def test_the_route_creates_no_second_storage_path(self):
        """`upload_verified_artifact` remains the only thing that writes an artifact, and
        the bucket and the object path stay J45's."""
        code = _code_only(MODULE_PATH)
        for forbidden in ("bucket", "storage.from_", "create_client", "supabase_client"):
            assert forbidden not in code, forbidden

    def test_the_route_module_sits_outside_the_production_review_package(self):
        """J19's scope guard over `app/production_review/*.py` forbids the tokens this
        route legitimately needs. It is the seventh module beside the six, not a change to
        any of them."""
        assert MODULE_PATH.parent == REPO / "app"
        assert not MODULE_PATH.is_relative_to(PRODUCTION_REVIEW_DIR)

    def test_the_route_writes_no_query_of_its_own(self):
        code = _code_only(MODULE_PATH)
        for forbidden in ("insert(", "update(", "upsert(", "delete(", "select("):
            assert forbidden not in code, forbidden

    def test_the_route_adds_no_migration_and_no_column(self):
        migrations = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
        assert not [p for p in migrations if "j47" in p.name.lower()], migrations

    def test_the_new_route_is_registered_once_and_as_a_post(self, production):
        routes = [
            route for route in production.main.app.routes
            if getattr(route, "path", "") == ROUTE_PATH
        ]
        assert len(routes) == 1
        assert routes[0].methods == {"POST"}

    def test_the_route_is_the_only_mutating_route_under_production_review(self, production):
        mutating = {
            getattr(route, "path"): route.methods
            for route in production.main.app.routes
            if getattr(route, "path", "").startswith("/production/")
            and getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}
        }
        assert set(mutating) == {
            "/production/review/{project_id}/pages/{page_number}/retry",
            # J50: the second mutating route, declared here rather than absorbed. It
            # records the project's revision-0 review baseline — a write, and one this
            # route's caller cannot aim at anything else: no connection is named, no
            # decision is taken and no artifact is produced.
            "/production/review/{project_id}/open",
            # L19: the fourth — declared here rather than absorbed. It writes one column of
            # one source document's row: which extraction lineage that document is reviewed
            # from. It takes no decision about a connection, opens no review, records no
            # revision and produces no artifact.
            "/production/review/{project_id}/documents/{document_id}/selected-drawing",
            # J64: the third, declared here rather than absorbed. It records the role a
            # human asserts for one of the project's OWN source documents — one column of
            # one `project_documents` row, scoped by the project in the path. It accepts
            # no project, no path, no identity and no role value outside the stored
            # vocabulary, it opens no review, and it selects nothing for any extraction.
            "/production/review/{project_id}/document-role",
            ROUTE_PATH,
        }

    def test_the_route_module_imports_the_workflow_resolver_from_7aj(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add((node.module, tuple(a.name for a in node.names)))
        assert (
            "app.cad_engine.project_workflow", ("resolve_project_connection",),
        ) in imported


# ===========================================================================
# K-Q. THE MUTATIONS — each guard shown to be able to fail.
# ===========================================================================
class TestTheMutations:
    """Each mutation removes the thing a guard above protects and shows it go red.

    A guard that cannot fail is not a guard. For the ORDER guards the mutation is a
    genuine reordering of what runs (or of the source that decides it) and the assertion
    above is re-evaluated against the mutated arrangement; for the REFUSAL guards the
    mutation is the acceptance the guard exists to prevent. Every one of them is
    evaluated with the same predicate the unmutated assertion uses, so what is shown is
    that the predicate DISCRIMINATES and not merely that it holds.
    """

    # --- K. the pointer writer cannot be replaced by an upload-capable path -----------
    def test_mutation_k_a_pointer_writer_that_could_upload(self):
        """The writer's whole safety property is that it has nothing to upload WITH. The
        mutation puts an uploader in the module and the guard's predicate — 'this module
        imports nothing that stores an object' — is re-evaluated against it."""
        from app import production_fabrication_pointer as pointer

        def uploads_anything(source_text):
            """The guard: does this module name a way to put an object in a bucket?"""
            tree = ast.parse(source_text)
            reached = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    reached.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    reached.add(node.module or "")
                    reached.update(alias.name for alias in node.names)
            # `app.supabase_client` is deliberately NOT forbidden: it is a DATABASE
            # client, and one column on one row is exactly what this writer is for. What
            # is forbidden is anything that can put an OBJECT somewhere.
            forbidden = ("upload_verified_artifact", "storage", "create_client")
            return sorted(
                name for name in reached
                if any(token in name.lower() for token in forbidden)
            )

        real_source = pathlib.Path(pointer.__file__).read_text(encoding="utf-8")
        assert uploads_anything(real_source) == [], (
            "the pointer writer already names something that could store an object"
        )
        assert "upload" not in _code_only(pathlib.Path(pointer.__file__))

        # The mutation: the same module, with an uploader imported. The ONLY difference
        # is the import, so what the predicate reports is the module's capability.
        mutated = real_source.replace(
            "from app.production_fabrication_artifact import durable_object_path",
            "from app.production_fabrication_artifact import durable_object_path\n"
            "from app.production_fabrication_artifact import upload_verified_artifact",
            1,
        )
        assert mutated != real_source, "the mutation anchor was not found"
        assert uploads_anything(mutated) == ["upload_verified_artifact"], (
            "the guard does not notice an upload-capable pointer writer — which is what "
            "this mutation exists to rule out"
        )

    # --- L. capture_run_ids cannot come from another source ---------------------------
    def test_mutation_l_capture_run_ids_from_another_source(
        self, wired, monkeypatch, arkles_workflow
    ):
        """J22 receives the RECONSTRUCTION's own run ids, carried through the resume. The
        mutation substitutes a different source for them and the guard's comparison —
        'what J22 received is what J24A reconstructed' — goes red."""
        reconstruction = _reconstruction(wired.store, PROJECT)
        assert reconstruction.capture_run_ids, "the Arkles capture has a run id"

        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        wired.run()
        _, _, run_ids = wired.review_store.recorded[0]
        assert run_ids == tuple(reconstruction.capture_run_ids), (
            "J22 did not receive the reconstruction's own run ids"
        )

        # The mutation: a resume whose ids came from somewhere else. Everything else about
        # the resumed object is J46's own, so the ONLY change is the run ids' source.
        real_resume = resolution.resume_project_workflow

        def other_source(*args, **kwargs):
            resumed = real_resume(*args, **kwargs)
            if resumed is None:
                return None
            return dataclasses.replace(resumed, capture_run_ids=("invented-run",))

        monkeypatch.setattr(resolution, "resume_project_workflow", other_source)
        wandered = _Wired(monkeypatch, working_dir=wired.working_dir)
        wandered.set_outcome(wandered.auto_state())
        wandered.run()
        _, _, invented = wandered.review_store.recorded[0]
        assert invented != tuple(reconstruction.capture_run_ids), (
            "the guard would not notice run ids reconstructed from another source"
        )
        assert invented == ("invented-run",)

    def test_the_route_names_capture_run_ids_once_and_takes_it_from_the_resume(self):
        """The mutation above only means something if the route reads the field from the
        resumed object rather than manufacturing it. Stated over the source."""
        body = _function_source(resolution.__file__, "_resolve")
        assert "evidence_run_ids=resumed.capture_run_ids" in body
        assert body.count("capture_run_ids") == 1, (
            "a second mention is a second source of evidence-run identity"
        )

    # --- M. the working directory is validated BEFORE the claim ------------------------
    def test_mutation_m_preflight_after_the_claim(self, monkeypatch, tmp_path):
        """Move the preflight after the claim: the ORDER guard must notice, because a
        broken deployment would then take a claim from a reviewer who could have used it."""
        body = _function_source(resolution.__file__, "resolve_production_connection")
        assert _before(body, "require_artifact_working_directory",
                       "acquire_project_review_claim"), (
            "the preflight is not before the claim"
        )
        mutated = _swap(body, "require_artifact_working_directory",
                        "acquire_project_review_claim")
        assert not _before(mutated, "require_artifact_working_directory",
                           "acquire_project_review_claim"), (
            "the ORDER predicate does not discriminate — it holds under the mutation too"
        )

        # And the same property, behaviourally: with the preflight in place no claim is
        # taken; with it removed the claim is.
        wired = _Wired(monkeypatch, working_dir=str(tmp_path / "absent"),
                       state=None)
        with pytest.raises(ArtifactRefused):
            wired.run()
        assert wired.claims.acquires == []

        monkeypatch.setattr(
            resolution, "require_artifact_working_directory", lambda working_dir=None: None,
        )
        wired.claims.acquires.clear()
        with pytest.raises(Exception):
            wired.run()
        assert wired.claims.acquires == [(PROJECT, OWNER)], (
            "the claim is taken once the preflight is removed — so the assertion above is "
            "measuring the preflight's POSITION and not something else"
        )

    # --- N. the pointer is written AFTER the revision is recorded ---------------------
    def test_mutation_n_pointer_before_the_record(self, monkeypatch, working_dir,
                                                  arkles_workflow):
        """Write the pointer before the revision is recorded: the ORDER guard fails,
        because a pointer would then describe a review that is not recorded."""
        expected = ("claim", "resolve", "upload", "record", "pointer", "release")

        wired = _Wired(monkeypatch, working_dir=working_dir)
        wired.set_outcome(wired.auto_state())
        wired.run()
        assert wired.log.order == expected

        real_record = _ResolutionStore.record_project_review

        def pointer_first(self, binding, workflow, **kwargs):
            self.log.record("pointer")
            snapshot = real_record(self, binding, workflow, **kwargs)
            return snapshot

        monkeypatch.setattr(_ResolutionStore, "record_project_review", pointer_first)
        wandered = _Wired(monkeypatch, working_dir=working_dir)
        wandered.set_outcome(wandered.auto_state())
        wandered.run()
        assert wandered.log.order != expected
        # The mutation's own "pointer" marker lands BEFORE the record, and the route's real
        # pointer write lands after it — so the sequence now says the deliverable existed
        # before the review that describes it did.
        assert wandered.log.order == ("claim", "resolve", "upload", "pointer", "record",
                                      "pointer", "release")

    # --- O. no pointer is written for a REVIEW or CONFIRM outcome ---------------------
    def test_mutation_o_a_pointer_written_after_review_or_confirm(
        self, wired, monkeypatch
    ):
        """The pointer is a claim about a DELIVERABLE. A REVIEW or CONFIRM outcome
        produced no artifact, so if the pointer step ran it would point the project at a
        drawing that does not exist. The guard is the AUTO condition standing between the
        two, and the mutation is that condition removed."""
        body = _function_source(resolution.__file__, "_resolve")
        assert _before(
            body, "state.decision == AUTOMATION_DECISION_AUTO", "record_fabrication_pointer"
        ), "the pointer write is not guarded by the decision"
        assert _before(body, "durable is not None", "record_fabrication_pointer")

        for decision in ("REVIEW", "CONFIRM"):
            state = _resolved_state(
                wired.workflow, decision=decision,
                output_status="BLOCKED_REVIEW", verification_status=None,
            )
            wired.set_outcome(state)
            outcome = wired.run()
            assert wired.pointer.updates == [], decision
            assert outcome.pointer_recorded is False
            assert outcome.durable_artifact_path is None
            assert wired.artifacts.storage_object.uploads == []

        # The mutation: the same REVIEW outcome, with the AUTO condition made true. Every
        # other guard is satisfied — the pointer writer would accept this call — so what
        # stops the write is the condition and nothing else.
        monkeypatch.setattr(resolution, "AUTOMATION_DECISION_AUTO", "REVIEW")
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        outcome = wired.run()
        assert outcome.durable_artifact_path is None, (
            "a REVIEW outcome still produced a durable artifact"
        )
        assert wired.pointer.updates == [], (
            "the pointer was written for a review that produced no deliverable"
        )

        # And the guard's predicate, evaluated where the mutation removes the OTHER half:
        # a decision the mutation calls AUTO, with no durable artifact to point at. The
        # pointer step still runs — and still writes nothing, because there is nothing.
        mark = len(wired.log.order)
        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        outcome = wired.run()
        assert wired.log.order[mark:] == ("claim", "resolve", "record", "release"), (
            "a pointer step ran for an outcome that produced no deliverable"
        )
        assert outcome.pointer_recorded is False
        assert wired.pointer.updates == []

    # --- P. a local path is never exposed as generated_files --------------------------
    def test_mutation_p_a_local_path_exposed_as_generated_files(self, wired, monkeypatch):
        """`generated_files` names what was STORED. The mutation puts the generator's own
        working path there — the mistake this rule exists to prevent — and the guard's
        predicate is re-evaluated against the mutated response."""
        state = wired.auto_state()
        wired.set_outcome(state)
        outcome = wired.run()

        local = str(wired.working_dir / f"{PACKAGE}-fabrication.pdf")
        durable = durable_object_path(OWNER, PROJECT, PACKAGE)

        def leaks_local_path(response_text):
            """The guard: does the response carry a working path rather than a stored one?"""
            return "fabrication.pdf" in response_text and str(wired.working_dir) in response_text

        real = json.dumps(outcome.to_response())
        assert not leaks_local_path(real), "the unmutated response already leaks a path"
        assert outcome.generated_files == (durable,)
        assert local not in real

        # The mutation is the substitution itself: the same response, with the local path
        # where the durable identity belongs.
        mutated = dataclasses.replace(outcome, generated_files=(local,))
        assert leaks_local_path(json.dumps(mutated.to_response())), (
            "the guard does not notice a local path in generated_files — so it is not "
            "measuring what it claims to"
        )
        assert local != durable

    # --- Q. no pointer update follows a J22 failure -----------------------------------
    def test_mutation_q_a_pointer_update_after_a_j22_failure(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        """The correctness barrier is J22; the pointer is downstream of it. The guard is
        the record standing between the upload and the pointer write, and the mutation is
        that order reversed."""
        body = _function_source(resolution.__file__, "_resolve")
        assert _before(body, "record_project_review", "record_fabrication_pointer"), (
            "the pointer write is not after the revision is recorded"
        )
        mutated = _swap(body, "record_project_review", "record_fabrication_pointer")
        assert not _before(mutated, "record_project_review", "record_fabrication_pointer"), (
            "the ORDER predicate does not discriminate — it holds under the mutation too"
        )

        # Behaviourally: the revision could not be recorded. The durable artifact exists,
        # the pointer does not, and nothing pretends the review was persisted.
        wired = _Wired(
            monkeypatch, working_dir=working_dir,
            store_fail=RuntimeError("the revision was not recorded"),
        )
        wired.set_outcome(wired.auto_state())
        with pytest.raises(resolution.ResolutionRouteRefused) as caught:
            wired.run()
        assert caught.value.code == resolution.RESOLUTION_REFUSED_NOT_PERSISTED
        assert wired.pointer.updates == [], (
            "the project was pointed at a review that was never recorded"
        )
        assert caught.value.durable_artifact_path == durable_object_path(
            OWNER, PROJECT, PACKAGE
        ), "the orphaned artifact was not named"
        assert wired.claims.releases == [(PROJECT, "claim-token-1")]

    # --- (requirement 27, no upload before verification) ------------------------------
    def test_the_second_line_of_defence_against_an_unverified_upload(
        self, monkeypatch, working_dir, arkles_workflow
    ):
        """Remove the route's own verification check: J45 refuses the artifact anyway."""
        from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_FAILED

        wired = _Wired(monkeypatch, working_dir=working_dir)
        # A real file at a real path, with 7AG's own failing verdict recorded for it.
        # Only the status stands between this artifact and the bucket.
        path = pathlib.Path(working_dir) / f"{PACKAGE}-fabrication.pdf"
        path.write_bytes(FABRICATION_PDF)
        state = _with_record(
            dataclasses.replace(
                _resolved_state(
                    wired.workflow, decision="AUTO", output_status="GENERATED",
                    verification_status=VERIFICATION_STATUS_FAILED,
                ),
                connection_records=wired.workflow.connection_records,
            ),
            verification=_Verification(
                verification_status=VERIFICATION_STATUS_FAILED, artifact_path=path,
            ),
        )
        wired.set_outcome(state)

        outcome = wired.run()
        assert wired.artifacts.storage_object.uploads == []
        assert outcome.failures == (resolution.ARTIFACT_NOT_VERIFIED,)
        assert outcome.durable_artifact_path is None

        # The mutation: a `_deliver_artifact` with the status check removed.
        def status_blind(*, verification, **kwargs):
            if verification is None or verification.artifact_path is None:
                return None, [resolution.ARTIFACT_NOT_VERIFIED]
            return resolution.upload_verified_artifact(
                user_id=kwargs["user_id"], project_id=kwargs["project_id"],
                connection_id=kwargs["package_id"],
                content=pathlib.Path(verification.artifact_path).read_bytes(),
                verification_status=verification.verification_status,
                client=kwargs["artifact_client"],
            ), []

        monkeypatch.setattr(resolution, "_deliver_artifact", status_blind)
        blind = _Wired(monkeypatch, working_dir=working_dir)
        wired.set_outcome(state)
        with pytest.raises(ArtifactRefused) as caught:
            blind.run()
        assert caught.value.code == "ARTIFACT_REFUSED_NOT_VERIFIED", (
            "J45 independently refuses to store an unverified artifact — the route's own "
            "check is the first line of defence and this is the second"
        )
        assert blind.artifacts.storage_object.uploads == []

    # --- (requirement 39, a local path is never a durable identity) -------------------
    def test_storing_a_local_path_in_the_pointer_is_impossible(self):
        """Point the project at the file the generator wrote: the pointer writer refuses,
        so the local path is impossible to store rather than merely discouraged."""
        from app.production_fabrication_pointer import record_fabrication_pointer

        client = _PointerClient(_Log())
        durable = durable_object_path(OWNER, PROJECT, PACKAGE)
        local = f"{PACKAGE}-fabrication.pdf"
        assert local != durable

        with pytest.raises(PointerRefused):
            record_fabrication_pointer(
                user_id=OWNER, project_id=PROJECT, connection_id=PACKAGE,
                durable_path=local, client=client,
            )
        assert client.updates == [], "the refused pointer still reached the database"

    # --- (requirement 11, the claim is released by TOKEN, never by holder) ------------
    def test_the_release_can_only_be_by_the_requests_own_token(self, wired, monkeypatch):
        """Release by holder instead of by token: the release-token guard fails."""
        body = _function_source(resolution.__file__, "_release_claim")
        assert 'claim_token' in body
        assert 'identity.user_id' not in body, "the release names the holder's identity"

        state = _resolved_state(
            wired.workflow, decision="REVIEW", output_status="BLOCKED_REVIEW",
            verification_status=None,
        )
        wired.set_outcome(state)
        wired.claims.token = "issued-to-this-request"
        wired.run()

        assert wired.claims.releases == [(PROJECT, "issued-to-this-request")]
        assert wired.claims.releases != [(PROJECT, OWNER)], (
            "the release is by token; a release by holder would name the owner here"
        )

    # --- (requirement 35, the claim is released on EVERY path out) --------------------
    def test_the_release_is_attempted_on_every_path_out(self, wired, monkeypatch):
        """Skip the finally-release: the always-releases guard fails."""
        body = _function_source(resolution.__file__, "resolve_production_connection")
        assert "finally:" in body and "_release_claim" in body
        release_clause = body[body.index("finally:"):]
        assert "_release_claim" in release_clause, (
            "the release is not inside the finally block"
        )

        def explode(*args, **kwargs):
            raise RuntimeError("the resolver broke")

        monkeypatch.setattr(resolution, "resolve_project_connection", explode)

        with pytest.raises(RuntimeError):
            wired.run()
        assert wired.claims.releases == [(PROJECT, "claim-token-1")], (
            "the release did not happen on the failing path"
        )

        # The mutation: a release that never runs. With it, the guard's own predicate
        # reports nothing released — so the assertion above is measuring the release.
        released = []
        real_release = resolution._release_claim
        monkeypatch.setattr(
            resolution, "_release_claim",
            lambda project_id, claim_token, client: (released.append(claim_token) or
                                                     resolution.CLAIM_RELEASED),
        )
        with pytest.raises(RuntimeError):
            wired.run()
        assert released == ["claim-token-1"], "the release seam was not exercised"
        monkeypatch.setattr(resolution, "_release_claim", real_release)
        released.clear()

        def never(project_id, claim_token, *, client=None):
            return None

        monkeypatch.setattr(resolution, "release_project_review_claim", never)
        with pytest.raises(RuntimeError):
            wired.run()
        assert wired.claims.releases == [(PROJECT, "claim-token-1")], (
            "the double recorded no second release — the call did not reach it"
        )

    # --- (requirement 5/the request contract, a server-owned field can never be taken) -
    def test_a_server_owned_field_can_never_be_accepted_from_the_client(self):
        """Accept `decision` from the client: the input guard fails."""
        with pytest.raises(resolution.ResolutionInputRefused) as caught:
            resolution.parse_resolution_request(_body(decision="AUTO"))
        assert caught.value.code == resolution.INPUT_REFUSED_SERVER_OWNED_FIELD

        # The mutation is the acceptance itself. If the parser ignored the field, a
        # request would exist — so the guard is the difference between a refusal and a
        # review performed with a client-chosen decision.
        request = resolution.parse_resolution_request(_body())
        assert "AUTO" not in json.dumps(dataclasses.asdict(request)), (
            "no server-owned value can be introduced through the body"
        )

# ===========================================================================
# R. WHAT THIS FILE DOES NOT DO.
# ===========================================================================
class TestWhatThisFileDoesNotDo:
    def test_no_test_in_this_file_touches_the_live_project(self):
        """Every composition here is driven by doubles. The genuine end-to-end proof over
        a real project is the controlled live review, which J47 stops at the gate of."""
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        code = _code_only(pathlib.Path(__file__))
        for forbidden in ("create_client", "app.supabase_client"):
            assert forbidden not in code, forbidden
        # The one live-adjacent read is the auth harness's in-process JWKS, which issues
        # and verifies tokens without a network call.
        assert "auth.install" in source
