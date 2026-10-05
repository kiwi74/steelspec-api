"""
J47 — THE PRODUCTION REVIEW RESOLUTION ROUTE.

WHY THIS MODULE EXISTS
----------------------
J44 built the liveness device, J45 the durable storage, J46 the replay. None of
them was reachable from HTTP: J46 says so of itself ("no generator, no verifier,
no dispatch, no upload, no claim, no AI call"), and J45 says so of itself
("Nothing here is reachable from HTTP"). This module is the composition they were
built for — the first request that may perform real production side effects — and
it is the only new thing J47 adds that does.

WHAT IT COMPOSES, AND WHAT IT DOES NOT REIMPLEMENT
--------------------------------------------------
Every step is an existing authority, called in the order the review requires:

    AUTH                       app/production_review/identity.py      (in main.py)
    AUTHORIZATION              app/production_review/authorization.py (in main.py)
    WORKING-DIRECTORY PREFLIGHT   this module
    CLAIM                      app/production_review/project_review_claim.py (J44)
    RESUME                     app/production_review/project_workflow_resumption.py (J46)
    EXPECTED-REVISION CHECK    J46's own `check_expected_revision`, inside `resume`
    PACKAGE VALIDATION         this module's index into the resumed workflow
    TASK OWNERSHIP VALIDATION  J46's reusable pre-validation seam
    RESOLUTION                 app/cad_engine/project_workflow.py (7AJ's resolver)
    LOCAL GENERATION + VERIFICATION   7AF's dispatch and 7AG's verification, both
                               invoked INSIDE the resolver — never called here
    DURABLE STORAGE UPLOAD     app/production_fabrication_artifact.py (J45)
    J22 PERSISTENCE            app/engineering_data/connection_review_repository.py
    FABRICATION POINTER UPDATE app/production_fabrication_pointer.py
    CLAIM RELEASE              J44's token-based release

Nothing here decides a decision, an AUTO eligibility, a provenance requirement, a
review status, a fabrication gate or an evidence interpretation. The reviewer
supplies requested facts; the reviewer never selects AUTO. Human resolution stays
subject to every existing downstream gate, because the gates are the ones that
run. `resolve_project_connection` is called, not copied, and not modified.

THE ORDERING IS THE SAFETY PROPERTY
-----------------------------------
Two orderings are load-bearing and are tested as such:

  * the working-directory preflight runs BEFORE the claim, because a request that
    could never produce an artifact must not consume a revision or take a claim
    from a reviewer who can;
  * the pointer is written LAST, after the upload returned and after J22 accepted
    the revision, because a revision that was recorded and an artifact that was
    stored are each independently true, while a pointer with nothing behind it is
    a claim about a deliverable that does not exist.

THE CORRECTNESS BARRIER IS J22, NOT THE CLAIM
---------------------------------------------
The claim is a liveness and concurrency device; it stops two reviewers working at
once, and it is not what makes the workflow correct. The barrier is J22's own
chain: the revision is re-read immediately before the write and the write itself
refuses a gap, so a request whose view has gone stale fails safely instead of
recording a second opinion about a revision that already happened.

ONE THING THIS ROUTE CANNOT CHECK, STATED RATHER THAN FAKED
-----------------------------------------------------------
J44 exposes `acquire` and `release` and no read: there is no way to ask "is this
token still the live claim?" without either acquiring again or altering J44's
semantics, and J47 may do neither. So the route cannot confirm claim OWNERSHIP
immediately before writing, and does not pretend to. What it does instead is the
thing that actually protects the record: it re-reads the persisted revision and
refuses to write if it moved. A lease that expired and was taken over by another
reviewer therefore costs a liveness guarantee, never a correctness one.

WHAT A FAILED STEP MEANS FOR THE STEPS AFTER IT
-----------------------------------------------
Generation, verification and upload failures are OUTCOMES, not exceptions. A
generation failure is a completed review: the workflow records it, and the route
persists it and reports it. The pointer is skipped for all three, because none of
them produced a durable artifact to point at. A J22 failure is the one write that
must not be worked around — the pointer is skipped, the durable artifact is
reported as stored-but-unpointed rather than deleted, and no second revision is
invented. A pointer failure leaves the recorded revision alone, because the
revision was already correct.

WHAT IT REFUSES TO ACCEPT FROM A CLIENT
---------------------------------------
The body carries `expected_revision` and `resolutions` and nothing else. A
project id, a connection id, a package id, a reviewer identity, a claim token, an
artifact path, an output status, a verification status and a decision are all
server-controlled, and any of them appearing in the body is refused rather than
ignored — a request that tries to name them is answered, not quietly corrected.

WHAT IT NEVER DOES
------------------
No Anthropic call, no extraction, no model, no second status vocabulary, no
second artifact-storage path, no reviewer-identity column, and no migration.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.cad_engine.connection_review_snapshot import (
    SNAPSHOT_REFUSED_NO_REVIEW_LAYER,
    SNAPSHOT_REFUSED_REVISION_GAP,
    SnapshotRefused,
)
from app.cad_engine.exception_resolution import HumanResolution
from app.cad_engine.project_workflow import resolve_project_connection
from app.production_fabrication_artifact import (
    ARTIFACT_REFUSED_NO_WORKING_DIR,
    ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
    ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
    VERIFIED_STATUS,
    ArtifactRefused,
    connection_workspace,
    upload_verified_artifact,
)
from app.production_fabrication_pointer import (
    record_fabrication_pointer,
)
from app.production_review.project_review_claim import (
    ClaimRefused,
    acquire_project_review_claim,
    release_project_review_claim,
)
# `ResumptionRefused` and `ResolutionTaskRefused` are not raised by this module — J46
# raises them, and this module lets them through unchanged. They are imported and
# re-exported so that the route's whole refusal surface can be named from one place, and
# so no caller has to know which layer inside the route is the one that refuses.
from app.production_review.project_workflow_resumption import (
    ResolutionTaskRefused,
    ResumptionRefused,
    resume_project_workflow,
    task_index_from_exception_package,
    validate_resolution_task_ownership,
)

#: The route this module composes. Declared here rather than only in `app.main` so that
#: "which path is the production resolution" has one answer in the codebase.
ROUTE_PATH = "/production/review/{project_id}/connections/{package_id}/resolve"

#: The decision that permits a fabrication artifact to exist. The literal is 7Z's own
#: vocabulary, read from the workflow's recorded decision — never re-derived here.
AUTOMATION_DECISION_AUTO = "AUTO"

# ---------------------------------------------------------------- working-directory refusal
# The codes are J45's, because the rules are J45's: an unset, blank, relative, absent,
# non-directory or unwritable working directory is the SAME condition whichever layer
# notices it, and a second vocabulary for it would be a second answer to one question.
#
# The one difference is deliberate and is the point of the preflight: J45's
# `working_directory()` CREATES the directory, because it is the layer that needs it.
# This preflight runs before the claim and creates nothing, so a configured-but-absent
# directory is refused here where that function would have made it.
WORKING_DIRECTORY_REFUSALS = (
    ARTIFACT_REFUSED_NO_WORKING_DIR,
    ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
    ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
)

# ---------------------------------------------------------------- request-input refusal
INPUT_REFUSED_NOT_A_MAPPING = "INPUT_REFUSED_NOT_A_MAPPING"
INPUT_REFUSED_UNKNOWN_FIELD = "INPUT_REFUSED_UNKNOWN_FIELD"
INPUT_REFUSED_SERVER_OWNED_FIELD = "INPUT_REFUSED_SERVER_OWNED_FIELD"
INPUT_REFUSED_EXPECTED_REVISION = "INPUT_REFUSED_EXPECTED_REVISION"
INPUT_REFUSED_NO_RESOLUTIONS = "INPUT_REFUSED_NO_RESOLUTIONS"
INPUT_REFUSED_RESOLUTION_NOT_A_MAPPING = "INPUT_REFUSED_RESOLUTION_NOT_A_MAPPING"
INPUT_REFUSED_MISSING_TASK_FIELD = "INPUT_REFUSED_MISSING_TASK_FIELD"

INPUT_REFUSALS = (
    INPUT_REFUSED_NOT_A_MAPPING,
    INPUT_REFUSED_UNKNOWN_FIELD,
    INPUT_REFUSED_SERVER_OWNED_FIELD,
    INPUT_REFUSED_EXPECTED_REVISION,
    INPUT_REFUSED_NO_RESOLUTIONS,
    INPUT_REFUSED_RESOLUTION_NOT_A_MAPPING,
    INPUT_REFUSED_MISSING_TASK_FIELD,
)

#: The only two keys a resolution request may carry.
REQUEST_FIELDS = ("expected_revision", "resolutions")

#: The fields one resolution entry may carry.
RESOLUTION_FIELDS = ("task_id", "task_type", "answer_type", "answer", "evidence")
REQUIRED_RESOLUTION_FIELDS = ("task_id", "task_type", "answer_type", "answer")

#: Everything a client may NOT supply, because it is established server-side: the
#: addressed project, the target connection, the reviewer, the claim, the artifact's
#: location, and every status the gates own. Refused by name rather than ignored, so a
#: client that tries to name one is told rather than silently corrected.
SERVER_OWNED_FIELDS = (
    "project_id",
    "connection_id",
    "package_id",
    "reviewer",
    "reviewer_id",
    "user_id",
    "claim_token",
    "artifact_path",
    "output_status",
    "verification_status",
    "decision",
    "resolutions_expected",
)

# ---------------------------------------------------------------- route refusal
RESOLUTION_REFUSED_PROJECT_UNKNOWN = "RESOLUTION_REFUSED_PROJECT_UNKNOWN"
RESOLUTION_REFUSED_NO_SUCH_CONNECTION = "RESOLUTION_REFUSED_NO_SUCH_CONNECTION"
RESOLUTION_REFUSED_NO_REVIEW_LAYER = "RESOLUTION_REFUSED_NO_REVIEW_LAYER"
RESOLUTION_REFUSED_CONCURRENT_REVISION = "RESOLUTION_REFUSED_CONCURRENT_REVISION"
RESOLUTION_REFUSED_NOT_PERSISTED = "RESOLUTION_REFUSED_NOT_PERSISTED"

RESOLUTION_REFUSALS = (
    RESOLUTION_REFUSED_PROJECT_UNKNOWN,
    RESOLUTION_REFUSED_NO_SUCH_CONNECTION,
    RESOLUTION_REFUSED_NO_REVIEW_LAYER,
    RESOLUTION_REFUSED_CONCURRENT_REVISION,
    RESOLUTION_REFUSED_NOT_PERSISTED,
)

# ---------------------------------------------------------------- outcome failure codes
# A failure that is part of a truthfully completed review, rather than a request that
# did not happen. Each names the step that did not deliver, so a caller can tell a
# recorded-but-undelivered artifact from a review that was never recorded.
ARTIFACT_NOT_VERIFIED = "ARTIFACT_NOT_VERIFIED"
ARTIFACT_READ_FAILED = "ARTIFACT_READ_FAILED"
ARTIFACT_UPLOAD_FAILED = "ARTIFACT_UPLOAD_FAILED"
POINTER_WRITE_FAILED = "POINTER_WRITE_FAILED"

OUTCOME_FAILURES = (
    ARTIFACT_NOT_VERIFIED,
    ARTIFACT_READ_FAILED,
    ARTIFACT_UPLOAD_FAILED,
    POINTER_WRITE_FAILED,
)

# ---------------------------------------------------------------- claim release codes
CLAIM_RELEASED = "CLAIM_RELEASED"


class ResolutionInputRefused(ValueError):
    """The request body was not a resolution request. Nothing was read or changed."""

    def __init__(self, code: str, statement: str) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement


class ResolutionRouteRefused(ValueError):
    """The request was well-formed but the project's own state refused it.

    `durable_artifact_path` is carried only on the one refusal where a durable artifact
    genuinely exists and the request still failed — a J22 write that did not land after a
    successful upload. It is stated so the caller learns the artifact is stored but
    unpointed, rather than being left to assume nothing was written anywhere.
    """

    def __init__(
        self, code: str, statement: str, *, durable_artifact_path: str | None = None
    ) -> None:
        super().__init__(f"{code}: {statement}")
        self.code = code
        self.statement = statement
        self.durable_artifact_path = durable_artifact_path


# ======================================================================================
# The working-directory preflight — BEFORE the claim, BEFORE any resolution.
# ======================================================================================
def require_artifact_working_directory(working_dir: object = None) -> Path:
    """The configured artifact working directory, or a refusal. It creates nothing.

    This runs before the project claim is acquired, so a deployment that could not
    produce an artifact refuses the request while it still owns nothing: no claim is
    taken from a reviewer who could have used it, and no revision is consumed by a
    resolution whose AUTO outcome could never have been delivered.

    There is no default, no `/tmp` fallback and no current-working-directory
    fallback. Every one of those would be a decision about where a customer's
    fabrication drawing is written, and the decision is the deployment's to state,
    not this module's to guess.

    Parameters
    ----------
    working_dir
        Overrides `app.config.ARTIFACT_WORKING_DIR`. Injected by tests; production
        callers pass nothing, and the value is then read from the configuration at
        this moment rather than at import.

    Returns
    -------
    pathlib.Path
        The configured directory, absolute, already existing and writable.

    Raises
    ------
    ArtifactRefused
        J45's own codes, because these are J45's own rules:
        `ARTIFACT_REFUSED_NO_WORKING_DIR` when nothing is configured or the value is
        blank; `ARTIFACT_REFUSED_RELATIVE_WORKING_DIR` when it is not absolute;
        `ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR` when it does not exist, is not a
        directory, or cannot be written to. The non-existence case is the one this
        function refuses and `working_directory()` would have created.
    """
    if working_dir is None:
        from app.config import ARTIFACT_WORKING_DIR as working_dir

    if not isinstance(working_dir, (str, os.PathLike)) or (
        isinstance(working_dir, str) and not working_dir.strip()
    ):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_NO_WORKING_DIR,
            "no artifact working directory is configured, so an AUTO outcome could never "
            "be stored. This is a deployment configuration, not a request error, and it "
            "is refused before the claim is taken and before any resolution is applied. "
            "There is no default and no fallback, by design.",
        )

    path = Path(working_dir)
    if not path.is_absolute():
        raise ArtifactRefused(
            ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
            "the artifact working directory must be absolute; a relative path resolves "
            "against whatever directory the process happened to start in, which is how "
            "one deployment's drawings end up beside another's",
        )
    if not path.exists():
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
            f"the configured artifact working directory {str(path)!r} does not exist. This "
            "preflight does not create it: a directory created at request time is one "
            "nobody chose, and the deployment that names it is the one that must have made "
            "it",
        )
    if not path.is_dir():
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
            f"the configured artifact working directory {str(path)!r} exists and is not a "
            "directory",
        )
    if not os.access(path, os.W_OK | os.X_OK):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
            f"the configured artifact working directory {str(path)!r} is not writable by "
            "this process",
        )
    return path


# ======================================================================================
# The request body.
# ======================================================================================
@dataclasses.dataclass(frozen=True)
class ResolutionRequest:
    """One parsed request: the revision the caller holds, and the answers it supplies."""

    expected_revision: int
    resolutions: tuple[HumanResolution, ...]


def _server_owned(payload: Mapping) -> list[str]:
    """Every server-owned field name this mapping names. Reported, never ignored."""
    return sorted(name for name in SERVER_OWNED_FIELDS if name in payload)


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def resolution_from_payload(payload: object, *, where: str) -> HumanResolution:
    """One resolution entry as a `HumanResolution`, or a refusal naming the entry.

    The four task fields are REQUIRED and the two vocabulary fields are checked for
    shape. The ANSWER's own shape is validated by the existing exception-resolution
    contract against the task's `answer_type` — this parser states what a resolution
    REQUEST is, and leaves what an answer MEANS to the layer that already decides it.
    """
    if not isinstance(payload, Mapping):
        raise ResolutionInputRefused(
            INPUT_REFUSED_RESOLUTION_NOT_A_MAPPING,
            f"{where} is {type(payload).__name__} rather than an object; a resolution is "
            "one task's answer",
        )
    owned = _server_owned(payload)
    if owned:
        raise ResolutionInputRefused(
            INPUT_REFUSED_SERVER_OWNED_FIELD,
            f"{where} names {owned}, which the server establishes from the authenticated "
            "request and never accepts from a caller",
        )
    unknown = sorted(
        name for name in payload if name not in RESOLUTION_FIELDS
    )
    if unknown:
        raise ResolutionInputRefused(
            INPUT_REFUSED_UNKNOWN_FIELD,
            f"{where} carries {unknown}, which is not a field of a resolution "
            f"({list(RESOLUTION_FIELDS)})",
        )
    missing = [name for name in REQUIRED_RESOLUTION_FIELDS if name not in payload]
    if missing:
        raise ResolutionInputRefused(
            INPUT_REFUSED_MISSING_TASK_FIELD,
            f"{where} is missing {missing}; a resolution names the task it answers and the "
            "answer it gives, and an answer is never assembled from a partial entry",
        )
    for name in ("task_id", "task_type", "answer_type"):
        if not _non_empty_text(payload[name]):
            raise ResolutionInputRefused(
                INPUT_REFUSED_MISSING_TASK_FIELD,
                f"{where}.{name} is {payload[name]!r}; a task identity and an answer type "
                "are non-empty strings",
            )
    evidence = payload.get("evidence", "")
    if not isinstance(evidence, str):
        raise ResolutionInputRefused(
            INPUT_REFUSED_MISSING_TASK_FIELD,
            f"{where}.evidence is {type(evidence).__name__} rather than a string; the "
            "reviewer's stated source is free text",
        )
    return HumanResolution(
        task_id=payload["task_id"],
        task_type=payload["task_type"],
        answer_type=payload["answer_type"],
        answer=payload["answer"],
        evidence=evidence,
    )


def parse_resolution_request(body: object) -> ResolutionRequest:
    """The body as a `ResolutionRequest`, or a refusal. Reads nothing, changes nothing.

    The revision is REQUIRED and must be a whole number: a request that does not say
    which revision it decided against is a request whose staleness cannot be judged, and
    defaulting it to "the current one" would let a stale click be applied as if it were
    current.
    """
    if not isinstance(body, Mapping):
        raise ResolutionInputRefused(
            INPUT_REFUSED_NOT_A_MAPPING,
            f"the request body is {type(body).__name__} rather than an object",
        )
    owned = _server_owned(body)
    if owned:
        raise ResolutionInputRefused(
            INPUT_REFUSED_SERVER_OWNED_FIELD,
            f"the request body names {owned}. The project comes from the URL, the "
            "connection from the resumed workflow, the reviewer from the access token, "
            "and every status from the gates — none of them is ever taken from a caller",
        )
    unknown = sorted(name for name in body if name not in REQUEST_FIELDS)
    if unknown:
        raise ResolutionInputRefused(
            INPUT_REFUSED_UNKNOWN_FIELD,
            f"the request body carries {unknown}; a resolution request is "
            f"{list(REQUEST_FIELDS)}",
        )

    expected_revision = body.get("expected_revision")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        raise ResolutionInputRefused(
            INPUT_REFUSED_EXPECTED_REVISION,
            f"expected_revision is {expected_revision!r}; it is the review revision this "
            "decision was made against, and a request that does not state one cannot be "
            "judged stale",
        )

    supplied = body.get("resolutions")
    if not isinstance(supplied, Sequence) or isinstance(supplied, (str, bytes)):
        raise ResolutionInputRefused(
            INPUT_REFUSED_NO_RESOLUTIONS,
            f"resolutions is {type(supplied).__name__} rather than a list; a resolve "
            "records the human answers it was given",
        )
    if not supplied:
        raise ResolutionInputRefused(
            INPUT_REFUSED_NO_RESOLUTIONS,
            "resolutions is empty. A connection is resolved at most once, and a resolve "
            "that records no human answer would consume that single attempt and process "
            "nothing",
        )
    answers = tuple(
        resolution_from_payload(entry, where=f"resolutions[{index}]")
        for index, entry in enumerate(supplied)
    )
    return ResolutionRequest(expected_revision=expected_revision, resolutions=answers)


# ======================================================================================
# The outcome.
# ======================================================================================
@dataclasses.dataclass(frozen=True)
class ProductionResolutionOutcome:
    """One completed production resolution, as this request performed it.

    `review_revision` is the revision J22 RECORDED — the workflow's own new revision,
    read back from the snapshot that was written, never the revision the caller held.
    `generated_files` carries DURABLE identities only: for an AUTO that delivered, the
    one Storage object this request stored; for every other outcome, nothing. A local
    working path is never part of this object, and there is no field it could occupy.
    """

    project_id: str
    review_revision: int
    review_package_id: str
    connection_id: str | None
    decision: str
    output_status: str | None
    verification_status: str | None
    generated_files: tuple[str, ...]
    blocker_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    durable_artifact_path: str | None
    pointer_recorded: bool
    claim_release: str
    failures: tuple[str, ...] = ()

    def to_response(self) -> dict[str, Any]:
        """The wire form. Every field below is one a caller may see.

        There is no claim token, no holder, no credential, no local path and no raw
        authorization value anywhere in this dictionary, and no field of this object
        holds one to be leaked here.
        """
        return {
            "project_id": self.project_id,
            "review_revision": self.review_revision,
            "review_package_id": self.review_package_id,
            "connection_id": self.connection_id,
            "decision": self.decision,
            "output_status": self.output_status,
            "verification_status": self.verification_status,
            "generated_files": list(self.generated_files),
            "blocker_codes": list(self.blocker_codes),
            "warning_codes": list(self.warning_codes),
            "durable_artifact_path": self.durable_artifact_path,
            "pointer_recorded": self.pointer_recorded,
            "claim_release": self.claim_release,
            "failures": list(self.failures),
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


def _release_claim(project_id: str, claim_token: str, client) -> str:
    """Release the claim this request took, and say what the release actually was.

    `CLAIM_REFUSED_NOT_ACTIVE` and `CLAIM_REFUSED_NOT_HOLDER` are EXPECTED here and are
    recorded rather than raised: they mean the claim this request wanted free is already
    free or already another reviewer's. Neither is a failure of the work being finished,
    and neither is ever treated as permission to release something else — there is no
    second release, no release by holder, and no retry. Any other failure propagates,
    because a release that broke for a reason this module cannot name is not one it
    should swallow.
    """
    try:
        release_project_review_claim(project_id, claim_token, client=client)
    except ClaimRefused as refused:
        return refused.code
    return CLAIM_RELEASED


# ======================================================================================
# The composition.
# ======================================================================================
def _deliver_artifact(
    *, verification, project_id, user_id, package_id, artifact_client
) -> tuple[str | None, list[str]]:
    """Upload one connection's verified local artifact, or say why none was stored.

    The local path is taken from 7AG's OWN verification manifest — the artifact it
    actually verified — and never composed here. Reading it is the only use this function
    makes of a local path, and it never leaves this function: what is returned is the
    DURABLE identity, which is what a caller may see.
    """
    if verification is None or verification.verification_status != VERIFIED_STATUS:
        return None, [ARTIFACT_NOT_VERIFIED]
    if verification.artifact_path is None:
        return None, [ARTIFACT_NOT_VERIFIED]
    try:
        content = Path(verification.artifact_path).read_bytes()
    except OSError:
        return None, [ARTIFACT_READ_FAILED]
    try:
        durable = upload_verified_artifact(
            user_id=user_id,
            project_id=project_id,
            connection_id=package_id,
            content=content,
            verification_status=verification.verification_status,
            client=artifact_client,
        )
    except ArtifactRefused:
        # J45 refused the bytes or an identifier BEFORE the storage call. That is a
        # refusal this route has nothing to add to, so it is not turned into an outcome.
        raise
    except Exception:
        # Storage itself did not accept the object. The review is still a completed
        # review; what failed is delivery, and it is reported as exactly that.
        return None, [ARTIFACT_UPLOAD_FAILED]
    return durable, []


def _resolve(
    *,
    binding,
    package_id: str,
    request: ResolutionRequest,
    working_root: Path,
    artifact_client,
    review_client,
    pointer_client,
    section_matcher,
    repository,
    history,
) -> ProductionResolutionOutcome:
    """Every step between the claim and the release, in the required order."""
    project_id = binding.project_id

    # ---- RESUME: revision 0 plus the persisted chain, and the expected-revision guard
    resumed = resume_project_workflow(
        project_id,
        section_matcher=section_matcher,
        expected_revision=request.expected_revision,
        repository=repository,
        history=history,
    )
    if resumed is None:
        raise ResolutionRouteRefused(
            RESOLUTION_REFUSED_PROJECT_UNKNOWN,
            f"the reconstruction found no project {project_id!r}, so there is nothing to "
            "resolve",
        )

    # The ten-key agreement guard is NOT restated here, because it has already run and
    # has already refused: `replay_revision` compares every rebuilt state against the
    # rows it was rebuilt from and raises `RESUMPTION_DISAGREES_WITH_HISTORY` on any
    # divergence, so a `ResumedProjectWorkflow` that exists at all is one whose every
    # replayed revision agreed. A second check over `resumed.agreements` would be a
    # branch no request can reach, and a guard that cannot fire is worse than no guard:
    # it reads as protection while proving nothing. The route inherits the refusal
    # instead, and `ResumptionRefused` propagates to the caller as itself.
    workflow = resumed.workflow
    exception_package = workflow.exception_package
    if exception_package is None:
        raise ResolutionRouteRefused(
            RESOLUTION_REFUSED_NO_REVIEW_LAYER,
            f"the resumed workflow for {project_id!r} carries no exception-resolution "
            "contract, so no task can be validated against a connection and no answer "
            "could be recorded",
        )

    # ---- TASK OWNERSHIP: J46's reusable seam, BEFORE any part of the request is applied
    validate_resolution_task_ownership(
        package_id=package_id,
        task_index=task_index_from_exception_package(exception_package),
        resolutions=request.resolutions,
    )

    # ---- PACKAGE VALIDATION: the target is a connection OF THE RESUMED WORKFLOW, and
    # ---- its position comes from the workflow's own order, never from anything a caller
    # ---- said. The ownership seam above already refused a package that is not a
    # ---- connection of this project, so this is expected to be satisfied — but the two
    # ---- sets are not the same derivation (that seam reads the exception contract's task
    # ---- groups, this reads the workflow's connections), and this route does not assume
    # ---- two independently derived groupings of the same project coincide.
    index = next(
        (
            position
            for position, connection in enumerate(workflow.connections)
            if connection.package_id == package_id
        ),
        None,
    )
    if index is None:
        raise ResolutionRouteRefused(
            RESOLUTION_REFUSED_NO_SUCH_CONNECTION,
            f"{package_id!r} is not a connection of this project's resumed workflow "
            f"(connections: {[c.package_id for c in workflow.connections]}); nothing was "
            "resolved and nothing was recorded",
        )

    # ---- RESOLUTION: 7AJ's own resolver, which runs 7AC, 7AD, 7AA, 7Z, 7AE, 7AF and
    # ---- 7AG for this one connection and nothing else. Generation and verification
    # ---- happen inside it; this route calls neither of them.
    #
    # `expected_revision` is the head the resume just established, which IS this
    # workflow's own revision — the resolver's optimistic check is therefore satisfied by
    # construction. It is passed rather than omitted so that the resolver's guard is
    # exercised against the persisted head and not against whatever revision a
    # future reconstruction happens to carry.
    workspace = connection_workspace(project_id, package_id, working_dir=working_root)
    resolved = resolve_project_connection(
        workflow,
        package_id=package_id,
        resolutions=list(request.resolutions),
        output_dir=str(workspace),
        expected_revision=resumed.head_revision,
    )

    # The resolver's own contract is that every other connection keeps its previous state
    # object and record untouched and the addressed one is replaced IN PLACE, so the
    # position found above is the position in the next state.
    state = resolved.connections[index]
    record = resolved.connection_records[index]
    failures: list[str] = []

    # ---- UPLOAD: AUTO only, and only for an artifact that genuinely verified
    durable: str | None = None
    if state.decision == AUTOMATION_DECISION_AUTO:
        durable, failures = _deliver_artifact(
            verification=record.verification_result,
            project_id=project_id,
            user_id=binding.identity.user_id,
            package_id=package_id,
            artifact_client=artifact_client,
        )

    # ---- J22 PERSISTENCE: the correctness barrier, re-read immediately before the write.
    # The re-read is the strongest ownership question this route CAN ask. It cannot ask
    # "is this request still the claim holder?" — see the module docstring — so it asks
    # the one that decides the record instead: is the project still at the revision this
    # request resumed from? If it is not, this request's view of the project is stale and
    # its resolution must not be applied.
    #
    # `latest_recorded_revision` answers None when the project has recorded nothing, and
    # that is revision 0 — the head a first resolution resumes at. Comparing the None
    # itself against 0 would refuse every project's first revision, so the absence is
    # normalized to the 0 it means before it is compared.
    store = _review_store()
    if (store.latest_recorded_revision(project_id, client=review_client) or 0) != resumed.head_revision:
        raise ResolutionRouteRefused(
            RESOLUTION_REFUSED_CONCURRENT_REVISION,
            f"the project's persisted review chain moved while this request was working; "
            f"it was at revision {resumed.head_revision} when this request resumed and is "
            "no longer. Nothing was recorded, and a resolution is never applied twice",
            durable_artifact_path=durable,
        )
    try:
        snapshot = store.record_project_review(
            binding,
            resolved,
            evidence_rows=store.project_evidence_rows(project_id, repository=repository),
            evidence_run_ids=resumed.capture_run_ids,
            client=review_client,
        )
    except SnapshotRefused as refusal:
        # Two of J22's refusals are CONFLICTS rather than faults: a revision gap is
        # another writer having recorded between this request's re-read and its write —
        # the precise race the re-read narrows and the write itself closes — and a
        # missing review layer is a project state, not a broken request. They are stated
        # as conflicts so a caller retries them rather than treating them as a fault.
        if refusal.code == SNAPSHOT_REFUSED_REVISION_GAP:
            raise ResolutionRouteRefused(
                RESOLUTION_REFUSED_CONCURRENT_REVISION,
                f"the project's review chain advanced between this request's re-read and "
                f"its write: {refusal.statement}. Nothing was recorded, and the durable "
                "artifact below — if one is named — was stored but is not pointed at",
                durable_artifact_path=durable,
            ) from refusal
        if refusal.code == SNAPSHOT_REFUSED_NO_REVIEW_LAYER:
            raise ResolutionRouteRefused(
                RESOLUTION_REFUSED_NO_REVIEW_LAYER,
                f"the project carries no review layer to record against: "
                f"{refusal.statement}",
                durable_artifact_path=durable,
            ) from refusal
        raise ResolutionRouteRefused(
            RESOLUTION_REFUSED_NOT_PERSISTED,
            f"the review revision was refused by the persistence layer: "
            f"{refusal.statement}. The revision is the review's own truth and was not "
            "written; the project pointer is deliberately not updated, because it is "
            "downstream of the revision and must never describe a review that was not "
            "recorded",
            durable_artifact_path=durable,
        ) from refusal
    except Exception as failure:
        raise ResolutionRouteRefused(
            RESOLUTION_REFUSED_NOT_PERSISTED,
            f"the review revision could not be recorded: {failure}. The revision is the "
            "review's own truth and was not written; the project pointer is deliberately "
            "not updated, because it is downstream of the revision and must never "
            "describe a review that was not recorded",
            durable_artifact_path=durable,
        ) from failure

    # ---- POINTER: AUTO with a DURABLE artifact, and only after the revision was recorded
    pointer_recorded = False
    if state.decision == AUTOMATION_DECISION_AUTO and durable is not None:
        try:
            record_fabrication_pointer(
                user_id=binding.identity.user_id,
                project_id=project_id,
                connection_id=package_id,
                durable_path=durable,
                client=pointer_client,
            )
        except Exception:
            # The revision stands, the artifact stands, the pointer does not. That is a
            # partial outcome and is reported as one — the revision is never rewritten to
            # describe a pointer that failed, and no second revision is invented.
            failures.append(POINTER_WRITE_FAILED)
        else:
            pointer_recorded = True

    return ProductionResolutionOutcome(
        project_id=project_id,
        review_revision=snapshot.review_revision,
        review_package_id=package_id,
        connection_id=state.connection_id,
        decision=state.decision,
        output_status=state.output_status,
        verification_status=state.verification_status,
        # Durable identities only. A local working path is never placed here, whether or
        # not an artifact was generated, because what a caller may act on is what was
        # STORED.
        generated_files=(durable,) if durable is not None else (),
        blocker_codes=tuple(state.blockers),
        warning_codes=tuple(state.warnings),
        durable_artifact_path=durable,
        pointer_recorded=pointer_recorded,
        claim_release=CLAIM_RELEASED,
        failures=tuple(failures),
    )


def resolve_production_connection(
    *,
    binding,
    package_id: str,
    request: ResolutionRequest,
    artifact_client: Any = None,
    review_client: Any = None,
    claim_client: Any = None,
    pointer_client: Any = None,
    section_matcher: Any = None,
    repository: Any = None,
    history: Sequence | None = None,
    working_dir: object = None,
) -> ProductionResolutionOutcome:
    """One authorized project's resolution of one connection, from claim to release.

    `binding` is a J19 `ProjectReviewBinding` — the caller's proof that an identity was
    established and that project was authorized FOR IT. Taking the binding rather than a
    project id is the authorization boundary expressed as a type: there is no argument
    here that could resolve a connection of someone else's project.

    The working directory is validated FIRST, before the claim: a request that could
    never store an artifact must not take a claim from a reviewer who could, and must
    not consume a revision. The claim is then acquired AFTER authentication and
    authorization (both performed by the caller, in `app.main`), and released on every
    path out of this function.

    Raises
    ------
    ArtifactRefused
        The working directory is unset, blank, relative, absent, not a directory or not
        writable — before the claim and before any resolution; or J45 refused the
        artifact itself.
    ClaimRefused
        Another reviewer holds the project's live claim. Nothing was read or changed.
    ResumptionRefused
        The persisted chain cannot be replayed, or the caller's `expected_revision` is
        stale, ahead or malformed.
    ResolutionTaskRefused
        A named task is not the target connection's, or is named twice, or the target
        package is not a connection of this project. Raised BEFORE any answer is applied.
    ResolutionRouteRefused
        The project is unknown to the reconstruction, its history disagrees with the
        replay, it carries no review layer, its revision moved during the request, or its
        revision could not be recorded.
    app.cad_engine.project_workflow.ProjectWorkflowError
        7AJ's own refusals — an unknown package, an already-processed connection, a
        stale workflow or a disagreeing stage result.
    ValueError
        The existing exception-resolution contract refused an answer against its task.
        Raised before anything is generated.
    """
    project_id = binding.project_id
    matcher = section_matcher if section_matcher is not None else section_matcher_for_review()

    # BEFORE the claim and BEFORE any resolution. Creates nothing.
    working_root = require_artifact_working_directory(working_dir)

    # The lease is J44's own default, not repeated here: a second copy of "15 minutes"
    # would be a second answer to how long a claim lives.
    claim = acquire_project_review_claim(
        project_id, binding.identity.user_id, client=claim_client
    )

    failure: BaseException | None = None
    release: dict[str, str] = {}
    try:
        outcome = _resolve(
            binding=binding,
            package_id=package_id,
            request=request,
            working_root=working_root,
            artifact_client=artifact_client,
            review_client=review_client,
            pointer_client=pointer_client,
            section_matcher=matcher,
            repository=repository,
            history=history,
        )
    except BaseException as raised:
        failure = raised
        raise
    finally:
        try:
            release["code"] = _release_claim(project_id, claim.claim_token, claim_client)
        except Exception:
            # An UNEXPECTED release failure — not one of J44's own refusals, which
            # `_release_claim` already turns into a recorded code. When the request has
            # already failed for a stated reason, that reason is what the caller is owed,
            # and replacing it with a release error would hide the actual failure behind
            # its own cleanup. Nothing here reports the release as successful: the caller
            # gets the original exception, and an un-released claim is freed by J44's own
            # lease expiry.
            if failure is None:
                raise

    return dataclasses.replace(outcome, claim_release=release["code"])


__all__ = [
    "ARTIFACT_NOT_VERIFIED",
    "ARTIFACT_READ_FAILED",
    "ARTIFACT_UPLOAD_FAILED",
    "AUTOMATION_DECISION_AUTO",
    "CLAIM_RELEASED",
    "INPUT_REFUSALS",
    "OUTCOME_FAILURES",
    "POINTER_WRITE_FAILED",
    "REQUEST_FIELDS",
    "REQUIRED_RESOLUTION_FIELDS",
    "RESOLUTION_FIELDS",
    "RESOLUTION_REFUSALS",
    "RESOLUTION_REFUSED_CONCURRENT_REVISION",
    "RESOLUTION_REFUSED_NOT_PERSISTED",
    "RESOLUTION_REFUSED_NO_REVIEW_LAYER",
    "RESOLUTION_REFUSED_NO_SUCH_CONNECTION",
    "RESOLUTION_REFUSED_PROJECT_UNKNOWN",
    "ROUTE_PATH",
    "SERVER_OWNED_FIELDS",
    "WORKING_DIRECTORY_REFUSALS",
    "ProductionResolutionOutcome",
    "ResolutionInputRefused",
    "ResolutionRouteRefused",
    "ResolutionRequest",
    "ResolutionTaskRefused",
    "ResumptionRefused",
    "parse_resolution_request",
    "require_artifact_working_directory",
    "resolution_from_payload",
    "resolve_production_connection",
]
