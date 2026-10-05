"""
J50 — THE PRODUCTION REVIEW BASELINE-OPENING OPERATION.

WHY THIS MODULE EXISTS
----------------------
J48 established the revision contract: a project's first recorded review revision is 0,
that revision is the reconstruction's own baseline, and J22's writer ACCEPTS it. J49 then
established the other half of the same fact — no production caller records it. Nothing in
this application could open a project's review, and without an open the review cannot
start at all: J22's builder computes `expected = 0 if previous_revision is None else
previous_revision + 1`, while the first human resolution advances the workflow to revision
1, so against an EMPTY chain the first resolve's own write is refused as a gap. Revision 0
is therefore not something the first resolution could ever have recorded as a side effect
of itself. It can only be recorded explicitly, by this operation.

WHAT IT COMPOSES, AND WHAT IT DOES NOT REIMPLEMENT
--------------------------------------------------
    AUTH              app/production_review/identity.py        (in main.py)
    AUTHORIZATION     app/production_review/authorization.py   (in main.py)
    BINDING           app/production_review/binding.py         (in main.py)
    STATE READ        J22's own `latest_recorded_revision`
    RECONSTRUCTION    app/production_review/project_workflow_resumption.py (J46), which
                      itself calls J24A's producer — never called directly here
    EVIDENCE          J22's own `project_evidence_rows`
    J22 PERSISTENCE   app/engineering_data/connection_review_repository.py
    READ-BACK         J22's own `load_review_snapshot`

No second reconstruction, no second evidence source, no second revision rule and no new
revision parameter. The baseline is revision 0 because the reconstruction is revision 0
and J22's builder is the thing that says so.

WHAT IT NEVER DOES
------------------
No connection resolution, no engineering approval, no generation, no verification, no
Storage upload, no fabrication pointer, no dispatch, no report, no claim, no working
directory, no reviewer-identity column, no Anthropic call, no extraction, no migration.
This module imports none of those authorities, and a test asserts it from the source.

NO J44 CLAIM, AND WHY
---------------------
J44's claim is a liveness device, not the correctness barrier; the barrier is J22's own
advisory-locked append-only write. Opening a review generates no artifact and reads no
human answer, so there is no in-flight state a second request could observe, and taking a
claim would refuse an in-flight resolution held by another reviewer for no correctness
gain. `acquire_project_review_claim` and `release_project_review_claim` are not imported
here at all, which is what makes the absence structural rather than a convention.

NO J45 WORKING-DIRECTORY PREFLIGHT, AND WHY
-------------------------------------------
`require_artifact_working_directory` belongs to the artifact-generating path. Opening a
review must work in a deployment where `ARTIFACT_WORKING_DIR` is unset, and it does: this
module never reads that setting.

IDEMPOTENCY
-----------
The state is read first, from J22's own reader. `latest_recorded_revision` answers None
when the project has recorded nothing, and that is the ONLY state in which anything is
written. When a revision is already recorded — 0, or any later one — the review is already
open: nothing is reconstructed, nothing is written, no revision is consumed, and the
response reports the revision that is actually recorded rather than claiming revision 0.
The number reported is the store's answer, never this module's assumption.

CONCURRENCY
-----------
The initial read is not under any lock and cannot be. Two requests may both observe an
empty chain; J22's write is the barrier, and it refuses the loser with
`SNAPSHOT_REFUSED_REVISION_GAP` — from its own builder when the second write's pre-read
already sees the first baseline, and from the advisory-locked `coalesce(max + 1, 0)`
comparison when both pre-reads landed first. That refusal is not an error to report when a
revision is now recorded: the loser re-reads the head and answers the ordinary already-open
success, because the state the caller asked for holds. A concurrent open that advances the
chain WHILE this request reconstructs surfaces as J46's own `RESUMPTION_EXPECTED_REVISION_
STALE` (409, unchanged); a retry then reads a recorded head and answers already-open. No
new lock and no migration: J22's existing semantics are sufficient.

WHAT A FAILED STEP MEANS
------------------------
Nothing here is partially applied. A refusal before the write wrote nothing and consumed no
revision, so the operation is retryable as itself. The one state this module refuses to
call a success is a write it cannot read back: if J22 accepted the baseline and the
persisted revision 0 cannot be retrieved, that is stated as `BASELINE_UNREADABLE` with the
write named as having happened, rather than reported as an open review. The row is never
deleted or rolled back.

WHAT IT REFUSES TO ACCEPT FROM A CLIENT
---------------------------------------
Nothing. An opening request carries no fields at all: the project comes from the URL, the
reviewer from the access token, the revision from the reconstruction and the evidence from
the project's own persisted rows. A body naming any of them — or naming anything else — is
refused rather than ignored.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

from app.cad_engine.connection_review_snapshot import (
    SNAPSHOT_REFUSED_REVISION_GAP,
    SnapshotRefused,
)
# `ResumptionRefused` is not raised by this module — J46 raises it, and this module lets it
# through unchanged. It is imported and re-exported so that the route's whole refusal
# surface can be named from one place, exactly as J47's module does for its own.
from app.production_review.project_workflow_resumption import (
    ResumptionRefused,
    resume_project_workflow,
)

#: The route this module composes. Declared here rather than only in `app.main` so that
#: "which path opens a project's review" has one answer in the codebase.
ROUTE_PATH = "/production/review/{project_id}/open"

#: The revision an open records, and the only one it can: a project with no recorded
#: revision reconstructs at 0, and J22's builder derives the revision from the workflow
#: rather than from anything passed here. This constant names the contract; it is not a
#: parameter, and nothing in this module could pass a different one.
BASELINE_REVISION = 0

# ---------------------------------------------------------------- request-input refusal
# One vocabulary per module, in J47's own shape. A body may carry exactly one field, and
# every other body raises the question of which KIND of unpermitted field it names.
INPUT_REFUSED_NOT_A_MAPPING = "INPUT_REFUSED_NOT_A_MAPPING"
INPUT_REFUSED_SERVER_OWNED_FIELD = "INPUT_REFUSED_SERVER_OWNED_FIELD"
INPUT_REFUSED_UNKNOWN_FIELD = "INPUT_REFUSED_UNKNOWN_FIELD"
INPUT_REFUSED_DOCUMENT_INVALID = "INPUT_REFUSED_DOCUMENT_INVALID"

INPUT_REFUSALS = (
    INPUT_REFUSED_NOT_A_MAPPING,
    INPUT_REFUSED_SERVER_OWNED_FIELD,
    INPUT_REFUSED_UNKNOWN_FIELD,
    INPUT_REFUSED_DOCUMENT_INVALID,
)

#: The only key an opening request may carry (Milestone J61). `document_id` states WHICH
#: of the project's source documents this review is about; the URL still supplies the
#: project, the token the reviewer, and the reconstruction the revision and its evidence.
#:
#: It is OPTIONAL and an absent body is still the whole of the request, because a project
#: with one readable document is not made to name it: the reconstruction's own default
#: resolves that case exactly as it did before this field existed. A caller that names a
#: document is believed, and a caller that names none has not chosen — this operation never
#: picks one on the caller's behalf.
REQUEST_FIELDS: tuple[str, ...] = ("document_id",)

#: Everything a client may NOT supply, refused BY NAME so that a client which tries to
#: name one is told which, rather than being told only that its body was unexpected.
SERVER_OWNED_FIELDS = (
    "project_id",
    "project",
    "connection_id",
    "package_id",
    "reviewer",
    "reviewer_id",
    "user_id",
    "authorization",
    "token",
    "claim_token",
    "revision",
    "review_revision",
    "expected_revision",
    "recorded_now",
    "review_state",
    "evidence_rows",
    "evidence_run_ids",
    "claim",
    "artifact_path",
    "output_path",
    "output_dir",
    "working_dir",
    "decision",
    "output_status",
    "verification_status",
    "pointer",
    "pointer_path",
)

# ---------------------------------------------------------------- route refusal
OPENING_REFUSED_PROJECT_UNKNOWN = "OPENING_REFUSED_PROJECT_UNKNOWN"
OPENING_REFUSED_BASELINE_NOT_RECORDED = "OPENING_REFUSED_BASELINE_NOT_RECORDED"
OPENING_REFUSED_BASELINE_UNREADABLE = "OPENING_REFUSED_BASELINE_UNREADABLE"

OPENING_REFUSALS = (
    OPENING_REFUSED_PROJECT_UNKNOWN,
    OPENING_REFUSED_BASELINE_NOT_RECORDED,
    OPENING_REFUSED_BASELINE_UNREADABLE,
)


class OpeningInputRefused(ValueError):
    """The request body was not an opening request. Nothing was read or changed."""

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


class OpeningRefused(ValueError):
    """The request was well-formed but the project's own state refused it.

    `baseline_recorded` is carried only on the one refusal where a baseline write
    genuinely landed and could not be read back. It is stated so the caller learns the
    revision exists rather than assuming nothing was written — and the row is never
    deleted, because a recorded revision is a fact and not a mistake to undo.
    """

    def __init__(
        self, code: str, statement: str, *, baseline_recorded: bool = False
    ) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement
        self.baseline_recorded = baseline_recorded


# ======================================================================================
# The request surface.
# ======================================================================================
def parse_opening_request(body: object) -> str | None:
    """The document this opening names, or None. Reads nothing, changes nothing.

    An ABSENT body is the request: `None` is accepted, and so is an empty object, because
    both say the same thing — this caller is not supplying anything, and the project's
    readable document resolves as it always did. Every other body is refused, and a body
    naming a server-controlled field is refused by that field's own name so the caller
    learns which one it should not have sent.

    Returns the `document_id` the caller stated, or `None` when it stated none. An explicit
    `null` states none as completely as an absent key does — it is the only way a JSON client
    can say "no value" for a field it holds — so it is read as the same request rather than as
    a half-stated identity. Every OTHER non-string, and a blank or whitespace-only string, IS
    a half-stated identity: it is refused rather than coerced, stripped or interpreted,
    because a caller who has not chosen is not a caller this operation may choose for.
    """
    if body is None:
        return None
    if not isinstance(body, Mapping):
        raise OpeningInputRefused(
            INPUT_REFUSED_NOT_A_MAPPING,
            f"the request body is {type(body).__name__} rather than an object; opening a "
            "review takes no fields, so there is no shape for it to take",
        )
    owned = sorted(name for name in SERVER_OWNED_FIELDS if name in body)
    if owned:
        raise OpeningInputRefused(
            INPUT_REFUSED_SERVER_OWNED_FIELD,
            f"the request body names {owned}. The project comes from the URL, the "
            "reviewer from the access token, the revision from the reconstruction and the "
            "evidence from the project's own persisted rows — none of them is ever taken "
            "from a caller",
        )
    unknown = sorted(name for name in body if name not in REQUEST_FIELDS)
    if unknown:
        raise OpeningInputRefused(
            INPUT_REFUSED_UNKNOWN_FIELD,
            f"the request body carries {unknown}; opening a review is performed with no "
            "fields at all, or with the document it is about",
        )

    if "document_id" not in body:
        return None
    document_id = body["document_id"]
    if document_id is None:
        return None
    if not (isinstance(document_id, str) and document_id.strip()):
        raise OpeningInputRefused(
            INPUT_REFUSED_DOCUMENT_INVALID,
            f"the request names document_id={document_id!r}; a document is named by a "
            "non-empty string or not at all, and this operation does not choose one",
        )
    return document_id


# ======================================================================================
# The outcome.
# ======================================================================================
@dataclasses.dataclass(frozen=True)
class ProductionReviewOpening:
    """One project's review, open, as the store reports it.

    `review_revision` is the revision J22 HAS RECORDED — read back from the store on the
    path that wrote it, and read from the store's own head on every other path. It is
    never the revision this operation assumed, which is why a project already at revision
    2 is reported at 2 rather than being described as a fresh revision 0.

    `recorded_now` says whether THIS request wrote the baseline. It is the only field that
    differs between the two ways to arrive at an open review, and it is not a status: the
    review is open in both cases, and `review_state` says so with J22's own code.
    """

    project_id: str
    review_state: str
    review_revision: int
    recorded_now: bool

    def to_response(self) -> dict[str, Any]:
        """The wire form. Every field below is one a caller may see.

        There is no claim token, no claim holder, no local path, no credential, no
        artifact identity and no reviewer identity anywhere in this dictionary, and no
        field of this object holds one to be leaked here.
        """
        return {
            "project_id": self.project_id,
            "review_state": self.review_state,
            "review_revision": self.review_revision,
            "recorded_now": self.recorded_now,
        }


# ======================================================================================
# The injectable seams.
# ======================================================================================
def section_matcher_for_review():
    """The section matcher J24A and J46 both require their caller to supply.

    Built per request because the production matcher performs a live catalogue select
    when it is constructed. This is the same seam `app.production_connection_review`
    exposes, reached through this module so the route has one place to replace it.
    """
    from app.production_connection_review import section_matcher_for_review as seam

    return seam()


def _review_store():
    from app.engineering_data import connection_review_repository as store

    return store


# ======================================================================================
# The composition.
# ======================================================================================
def _already_open(store, project_id: str, revision: int) -> ProductionReviewOpening:
    """The one way an open review is reported when this request did not write it."""
    return ProductionReviewOpening(
        project_id=project_id,
        review_state=store.REVIEW_STATE_RECORDED,
        review_revision=revision,
        recorded_now=False,
    )


def open_project_review(
    *,
    binding,
    review_client: Any = None,
    section_matcher: Any = None,
    repository: Any = None,
    history: Any = None,
    document_id: str | None = None,
) -> ProductionReviewOpening:
    """One authorized project's review, opened: its revision-0 baseline is persisted.

    `binding` is a J19 `ProjectReviewBinding` — the caller's proof that an identity was
    established and that project was authorized FOR IT. Taking the binding rather than a
    project id is the authorization boundary expressed as a type: there is no argument
    here that could open someone else's project.

    `document_id` (Milestone J61) names which of the project's source documents the review
    is about, and reaches the reconstruction unchanged. It is `None` by default, which is
    the state every existing caller is in and which resolves exactly as it did before: a
    project with one readable document opens without naming it. This operation never picks
    a document — a project with several and no document named is refused by the
    reconstruction, and that refusal is the honest answer rather than a defect to route
    around. It changes nothing else: the revision, the baseline, the idempotency, the
    absence of a claim and the absence of a working directory are all as they were.

    Idempotent by construction. The state is read from J22 itself, and only an EMPTY chain
    proceeds to a write; every other state is reported as it is, without a reconstruction
    and without consuming a revision. Note that `document_id` is therefore only consulted
    by the path that WRITES: a project already open is reported as it is, whatever document
    this request named, because the recorded revision is the fact and not a proposal.

    Raises
    ------
    OpeningRefused
        The reconstruction found no such project; J22 refused the baseline for a reason
        other than a racing open; or the baseline was written but could not be read back.
    app.production_review.project_workflow_resumption.ResumptionRefused
        The persisted chain cannot be read, or the reconstruction is not at revision 0 —
        J46's own refusal, propagating unchanged rather than being restated here.
    """
    project_id = binding.project_id
    store = _review_store()

    # ---- THE STATE, from J22's own reader. None is the ONLY state that writes.
    recorded = store.latest_recorded_revision(project_id, client=review_client)
    if recorded is not None:
        return _already_open(store, project_id, recorded)

    matcher = section_matcher if section_matcher is not None else section_matcher_for_review()

    # ---- THE RECONSTRUCTION, J46's, which calls J24A's producer itself. `expected_revision
    # ---- = 0` states the contract: a project with no recorded review reconstructs at
    # ---- revision 0. The revision-0 requirement is not re-checked here, because J22's
    # ---- builder refuses anything else against an empty chain by construction; a second
    # ---- check would be a branch no request can reach.
    resumed = resume_project_workflow(
        project_id,
        section_matcher=matcher,
        expected_revision=BASELINE_REVISION,
        repository=repository,
        history=history,
        document_id=document_id,
    )
    if resumed is None:
        raise OpeningRefused(
            OPENING_REFUSED_PROJECT_UNKNOWN,
            f"the reconstruction found no project {project_id!r}, so it has no review to "
            "open",
        )

    # ---- J22 PERSISTENCE. `previous_revision` is J22's own pre-read and is not passed:
    # ---- `record_project_review` performs it, which is what makes the builder's
    # ---- expected-revision arithmetic J22's answer rather than this module's.
    try:
        store.record_project_review(
            binding,
            resumed.workflow,
            evidence_rows=store.project_evidence_rows(project_id, repository=repository),
            evidence_run_ids=resumed.capture_run_ids,
            client=review_client,
        )
    except SnapshotRefused as refused:
        if refused.code != SNAPSHOT_REFUSED_REVISION_GAP:
            raise OpeningRefused(
                OPENING_REFUSED_BASELINE_NOT_RECORDED,
                f"the baseline could not be recorded: {refused.statement}. Nothing was "
                "written and no revision was consumed, so this request may be retried "
                "as itself",
            ) from refused
        # A GAP against an EMPTY chain can only be another request having recorded first —
        # this request's own write cannot leave a gap, because J22's builder derived its
        # expected revision from the very pre-read that returned None. So the head is read
        # again: a revision now recorded is an already-open review, and anything else is a
        # refusal this module does not know how to explain and will not claim.
        now_recorded = store.latest_recorded_revision(project_id, client=review_client)
        if now_recorded is None:
            raise OpeningRefused(
                OPENING_REFUSED_BASELINE_NOT_RECORDED,
                f"the baseline could not be recorded and no revision is now recorded: "
                f"{refused.statement}. Nothing was written",
            ) from refused
        return _already_open(store, project_id, now_recorded)

    # ---- READ-BACK, through J22's own reader. The write is not the evidence: an open
    # ---- review is one whose persisted revision 0 can be read again, which is the only
    # ---- claim this operation makes.
    baseline = store.load_review_snapshot(project_id, BASELINE_REVISION, client=review_client)
    if baseline is None:
        raise OpeningRefused(
            OPENING_REFUSED_BASELINE_UNREADABLE,
            f"the revision-{BASELINE_REVISION} baseline was WRITTEN for {project_id!r} and "
            "could not be read back through the review store; the review is not reported "
            "as open, and the recorded revision is not deleted or rolled back",
            baseline_recorded=True,
        )

    return ProductionReviewOpening(
        project_id=project_id,
        review_state=store.REVIEW_STATE_RECORDED,
        review_revision=baseline.review_revision,
        recorded_now=True,
    )


__all__ = [
    "BASELINE_REVISION",
    "INPUT_REFUSALS",
    "INPUT_REFUSED_DOCUMENT_INVALID",
    "INPUT_REFUSED_NOT_A_MAPPING",
    "INPUT_REFUSED_SERVER_OWNED_FIELD",
    "INPUT_REFUSED_UNKNOWN_FIELD",
    "OPENING_REFUSALS",
    "OPENING_REFUSED_BASELINE_NOT_RECORDED",
    "OPENING_REFUSED_BASELINE_UNREADABLE",
    "OPENING_REFUSED_PROJECT_UNKNOWN",
    "REQUEST_FIELDS",
    "ROUTE_PATH",
    "SERVER_OWNED_FIELDS",
    "OpeningInputRefused",
    "OpeningRefused",
    "ProductionReviewOpening",
    "ResumptionRefused",
    "open_project_review",
    "parse_opening_request",
    "section_matcher_for_review",
]
