"""
Milestone J46 — PRODUCTION REVIEW RESUMPTION, REPLAY GUARD AND RESOLUTION SAFETY.

WHAT THIS IS
============
The state/resumption bridge between the components that already exist:

    project_workflow_reconstruction   revision 0 from persisted capture   (J24A)
    connection_review_repository      the persisted review chain           (J22)
    connection_review_snapshot        the replay codec and the projection  (J21/J22)
    project_workflow                  the state and its pure assembly      (7AJ)

It answers one question, and only one:

    what is this project's review workflow NOW, reconstructed from revision 0
    plus the revisions J22 persisted — without the Python process that
    produced them, and without regenerating any of their work?

Every connection state it returns comes out of a persisted row. Nothing is
re-run, re-gated, re-dispatched, re-verified, re-generated or uploaded: the
historical outcomes recorded in J22 are authoritative for replay, and this
module's whole job is to hand them back as a genuine 7AJ state.

WHY NOTHING IS RE-EXECUTED
==========================
The obvious implementation of "resume" is to replay each revision by calling
`resolve_project_connection` again. That would be wrong in three separate ways,
and the reasons are worth stating because each one is a real engineering fact,
not a preference:

  * It would generate a SECOND fabrication drawing for a connection that
    already has one. 7AJ processes a connection AT MOST ONCE for exactly this
    reason; re-resolving would duplicate production.
  * A historical generation FAILURE would be re-run and might succeed, or a
    historical verification FAILURE might pass. Either way the replay would
    silently rewrite what actually happened, which is the opposite of
    resumption.
  * It would touch the network, the filesystem and the artifact layer purely
    to answer a question about the past.

So replay is a STATE TRANSITION over recorded rows, never a re-execution.

WHAT A RESUMED WORKFLOW IS, AND WHAT IT HONESTLY IS NOT
=======================================================
The returned `ProjectWorkflowState` carries the persisted projection of every
connection — the nine fields J22 records — and the persisted project decision,
assembled by 7AJ's OWN pure bookkeeping (`_assemble_state` recomputes counts and
the summary from the connection states; it generates nothing).

It does NOT carry the genuine 7AA pipeline objects for connections that were
already resolved. Those objects were never persisted anywhere — J22 records
their PROJECTION, not the objects — so a resumed workflow cannot supply them.
The record for such a connection is therefore assembled EMPTY, which is the
truth about what is recoverable, rather than a rebuilt object standing in for
one that is gone. This matters to J47: the composite gate needs a live pipeline
per connection, so a resumed workflow alone cannot resolve the NEXT connection
without re-obtaining the historical ones. That gap is reported, not papered
over — see the J46 report.

THE TEN-KEY AGREEMENT GUARD
===========================
`ProjectConnectionState` is compared to a persisted item through an explicit
ten-key plain-data projection (`AGREEMENT_KEYS`), never by object equality:

    review_revision          the containing snapshot's header
    review_package_id        \
    connection_id             |
    decision                  |
    output_status             |
    verification_status       |  the persisted item
    generated_files           |
    blocker_codes             |
    warning_codes             |
    last_processed_revision  /

Two reasons it is a projection rather than `==`. The first is that the objects
being compared are of different types (a `ProjectConnectionState` and a
`ReviewSnapshotItem`) and a shared-key comparison is the only well-defined one.
The second is that these states are downstream of CAD work: an object may carry
an OCCT/CadQuery handle that differs by identity while every plain value agrees,
and object equality would report that as a disagreement. The guard compares
plain data and reports WHICH key diverged, so a disagreement names its cause
instead of printing two reprs.

WITHIN THIS MODULE the guard is a regression check on the adapter: the replayed
connection is decoded by J22's own `_state_from_item`, so an agreement failure
means this module's projection has drifted from the codec it decodes with. Its
DISCRIMINATING use is J47's: comparing a freshly computed result against
persisted history before recording it. The comparator itself is exercised
against a divergence in every one of the ten keys.

RESOLUTION TASK OWNERSHIP
=========================
7AJ already refuses, inline, a resolution whose task id is not a task of the
target package. That guard lives inside `resolve_project_connection`, which J46
must not modify, and it does not reject a DUPLICATED task id — a list naming the
same task twice passes it. J46 adds the reusable pre-validation seam instead:
`validate_resolution_task_ownership`, which a caller runs BEFORE the resolver so
that an invalid request is refused before any part of it is applied. It refuses
a duplicated task, a task of another connection in the same project, and a task
of no connection of this project — and it never consults another project, which
is why "unknown" and "another project's" are one predicate here: a task id that
belongs to no connection of this project is refused, and looking further would
itself be the cross-project read the guard exists to prevent.

WHAT THIS MODULE NEVER DOES
===========================
No writes of any kind: no insert, no snapshot, no claim, no artifact, no upload,
no download, no generation, no dispatch, no verification, no AI call, no
extraction, no retry. No route, no schema, no migration, no second revision
source, no second codec and no second decision rule. It reads persisted rows and
does arithmetic over what they say.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from app.cad_engine.connection_review_snapshot import (
    ReviewSnapshot,
    ReviewSnapshotItem,
    _state_from_item,
)
from app.cad_engine.exception_resolution import HumanResolution, apply_human_resolution
from app.cad_engine.project_workflow import (
    ProjectConnectionRecord,
    ProjectConnectionState,
    ProjectWorkflowState,
    _assemble_state,
)

__all__ = [
    "AGREEMENT_KEYS",
    "RESUMPTION_REFUSALS",
    "RESOLUTION_REFUSALS",
    "RESUMPTION_DISAGREES_WITH_HISTORY",
    "RESUMPTION_DUPLICATE_REVISION",
    "RESUMPTION_EXPECTED_REVISION_AHEAD",
    "RESUMPTION_EXPECTED_REVISION_INVALID",
    "RESUMPTION_EXPECTED_REVISION_STALE",
    "RESUMPTION_HISTORY_GAP",
    "RESUMPTION_INCOMPLETE_SNAPSHOT",
    "RESUMPTION_NOT_ONE_CONNECTION",
    "RESUMPTION_PACKAGE_UNKNOWN",
    "RESUMPTION_RESOLUTION_UNREADABLE",
    "RESUMPTION_SNAPSHOT_NOT_OF_THIS_PROJECT",
    "RESOLUTION_TARGET_UNKNOWN",
    "RESOLUTION_TASK_DUPLICATED",
    "RESOLUTION_TASK_FOREIGN_CONNECTION",
    "RESOLUTION_TASK_NOT_OF_THIS_PROJECT",
    "AgreementReport",
    "ResolutionTaskRefused",
    "ResumedProjectWorkflow",
    "ResumptionRefused",
    "agreement_projection_from_item",
    "agreement_projection_from_state",
    "agreement_reports",
    "check_expected_revision",
    "compare_agreement",
    "replay_revision",
    "resolutions_from_item",
    "resume_project_workflow",
    "task_index_from_exception_package",
    "validate_history",
    "validate_resolution_task_ownership",
]

#: The ten keys the agreement guard compares, in the order a divergence is reported. The
#: order is fixed so that a state diverging in several keys reports the same one every
#: time — a guard whose answer depends on dict iteration order is not a deterministic one.
AGREEMENT_KEYS = (
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

# ---------------------------------------------------------------- resumption refusals
RESUMPTION_HISTORY_GAP = "RESUMPTION_HISTORY_GAP"
RESUMPTION_DUPLICATE_REVISION = "RESUMPTION_DUPLICATE_REVISION"
RESUMPTION_EXPECTED_REVISION_INVALID = "RESUMPTION_EXPECTED_REVISION_INVALID"
RESUMPTION_EXPECTED_REVISION_AHEAD = "RESUMPTION_EXPECTED_REVISION_AHEAD"
RESUMPTION_EXPECTED_REVISION_STALE = "RESUMPTION_EXPECTED_REVISION_STALE"
RESUMPTION_PACKAGE_UNKNOWN = "RESUMPTION_PACKAGE_UNKNOWN"
RESUMPTION_INCOMPLETE_SNAPSHOT = "RESUMPTION_INCOMPLETE_SNAPSHOT"
RESUMPTION_NOT_ONE_CONNECTION = "RESUMPTION_NOT_ONE_CONNECTION"
RESUMPTION_SNAPSHOT_NOT_OF_THIS_PROJECT = "RESUMPTION_SNAPSHOT_NOT_OF_THIS_PROJECT"
RESUMPTION_RESOLUTION_UNREADABLE = "RESUMPTION_RESOLUTION_UNREADABLE"
RESUMPTION_DISAGREES_WITH_HISTORY = "RESUMPTION_DISAGREES_WITH_HISTORY"

RESUMPTION_REFUSALS = (
    RESUMPTION_HISTORY_GAP,
    RESUMPTION_DUPLICATE_REVISION,
    RESUMPTION_EXPECTED_REVISION_INVALID,
    RESUMPTION_EXPECTED_REVISION_AHEAD,
    RESUMPTION_EXPECTED_REVISION_STALE,
    RESUMPTION_PACKAGE_UNKNOWN,
    RESUMPTION_INCOMPLETE_SNAPSHOT,
    RESUMPTION_NOT_ONE_CONNECTION,
    RESUMPTION_SNAPSHOT_NOT_OF_THIS_PROJECT,
    RESUMPTION_RESOLUTION_UNREADABLE,
    RESUMPTION_DISAGREES_WITH_HISTORY,
)

# ------------------------------------------------------- resolution-ownership refusals
RESOLUTION_TARGET_UNKNOWN = "RESOLUTION_TARGET_UNKNOWN"
RESOLUTION_TASK_NOT_OF_THIS_PROJECT = "RESOLUTION_TASK_NOT_OF_THIS_PROJECT"
RESOLUTION_TASK_FOREIGN_CONNECTION = "RESOLUTION_TASK_FOREIGN_CONNECTION"
RESOLUTION_TASK_DUPLICATED = "RESOLUTION_TASK_DUPLICATED"

RESOLUTION_REFUSALS = (
    RESOLUTION_TARGET_UNKNOWN,
    RESOLUTION_TASK_NOT_OF_THIS_PROJECT,
    RESOLUTION_TASK_FOREIGN_CONNECTION,
    RESOLUTION_TASK_DUPLICATED,
)


class ResumptionRefused(ValueError):
    """No state was resumed, for the stated reason.

    A refusal is fail-closed: a caller that receives one has no resumed workflow, no
    partial one, and nothing it may act on.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ResolutionTaskRefused(ValueError):
    """A resolution was not applied, because a task it names is not the target's.

    Raised by the pre-validation seam, BEFORE any part of the request is applied, so a
    caller can never end up with a half-recorded set of human answers.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class AgreementReport:
    """The result of comparing two ten-key projections.

    `agrees` is the whole answer; `diverged_key` names the first key that disagreed, in
    `AGREEMENT_KEYS` order, and is None when they agree. `detail` states the two values
    for that key — the comparison is never reported as a bare boolean, because "these
    disagree" without saying where is not something a reviewer can act on.
    """

    agrees: bool
    diverged_key: str | None = None
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class ResumedProjectWorkflow:
    """One project's workflow, resumed from revision 0 plus its persisted revisions.

    `workflow` is a genuine 7AJ `ProjectWorkflowState` at `head_revision`. `revisions` is
    the persisted chain that was replayed, ascending, and `agreements` holds one report
    per replayed revision — the replay's own record that each replayed state agreed with
    the rows it was replayed from.

    `capture_run_ids` (added by J47) is the reconstruction's own provenance — the
    extraction runs the readings behind revision 0 came from, in page order, exactly as
    J24A states them. It is carried so that a caller recording the NEXT revision does not
    have to reconstruct the project a second time to state where its evidence came from.
    It is not a replay concern: it is not one of `AGREEMENT_KEYS`, nothing about the
    replay depends on it, and it is empty exactly when the reconstruction states none.
    """

    project_id: str
    workflow: ProjectWorkflowState
    head_revision: int
    revisions: tuple[int, ...]
    agreements: tuple[AgreementReport, ...]
    capture_run_ids: tuple[str, ...] = ()


# --------------------------------------------------------------------------------------
# The ten-key projection and its comparison.
# --------------------------------------------------------------------------------------
def _plain(value: Any) -> Any:
    """The plain-data form of one projection value.

    A sequence becomes a tuple of plain values, so a stored list and a rebuilt tuple
    compare equal; everything else is returned as it is. This is the whole of the
    "objects may differ by identity" defence: the guard never compares two objects, it
    compares the plain values a caller can read off them.
    """
    if isinstance(value, (list, tuple)):
        return tuple(_plain(item) for item in value)
    return value


def agreement_projection_from_state(
    *, review_revision: int, connection: ProjectConnectionState
) -> dict[str, Any]:
    """The ten-key projection of one rebuilt 7AJ connection state."""
    return {
        "review_revision": review_revision,
        "review_package_id": connection.package_id,
        "connection_id": connection.connection_id,
        "decision": connection.decision,
        "output_status": connection.output_status,
        "verification_status": connection.verification_status,
        "generated_files": _plain(connection.generated_files),
        "blocker_codes": _plain(connection.blockers),
        "warning_codes": _plain(connection.warnings),
        "last_processed_revision": connection.last_processed_revision,
    }


def agreement_projection_from_item(
    snapshot: ReviewSnapshot, item: ReviewSnapshotItem
) -> dict[str, Any]:
    """The ten-key projection of one persisted item, with its header's revision.

    The revision is the SNAPSHOT's, never the item's: a J22 item carries no revision of
    its own, and reading one from anywhere else would be a second revision source.
    """
    return {
        "review_revision": snapshot.review_revision,
        "review_package_id": item.review_package_id,
        "connection_id": item.connection_id,
        "decision": item.decision,
        "output_status": item.output_status,
        "verification_status": item.verification_status,
        "generated_files": _plain(item.generated_files),
        "blocker_codes": _plain(item.blocker_codes),
        "warning_codes": _plain(item.warning_codes),
        "last_processed_revision": item.last_processed_revision,
    }


def compare_agreement(left: Mapping, right: Mapping) -> AgreementReport:
    """Compares two projections on the ten keys, naming the first that diverges.

    Deterministic and side-effect free: it reads both mappings, compares the ten keys in
    `AGREEMENT_KEYS` order, and returns. It never compares whole objects, so two states
    carrying different CAD handles but the same plain values agree.
    """
    for key in AGREEMENT_KEYS:
        left_value = _plain(left.get(key))
        right_value = _plain(right.get(key))
        if left_value != right_value:
            return AgreementReport(
                agrees=False,
                diverged_key=key,
                detail=(
                    f"{key} disagrees: replayed {left_value!r} vs persisted {right_value!r}"
                ),
            )
    return AgreementReport(agrees=True)


# --------------------------------------------------------------------------------------
# History validation and the expected-revision guard.
# --------------------------------------------------------------------------------------
def _real_int(value: Any) -> bool:
    """True when the value is a whole number — a bool is not one, here or anywhere."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_history(revisions: Sequence[int]) -> tuple[int, ...]:
    """The persisted revision chain, refused unless it is contiguous from 1.

    The chain starts at 1 because revision 0 is the RECONSTRUCTED state: it is produced
    by J24A from the persisted capture and is not itself a recorded review revision. The
    first recorded revision is the first resolve, which advances the workflow to 1.

    A gap cannot be replayed — the state between the two revisions was never persisted,
    so "what was the review state at revision N?" would have no answer — and renumbering
    a revision to close the gap would be a second revision system, which J21 forbids.
    A duplicate is refused for the same reason a second source would be: two rows
    claiming one revision are two answers to one question.
    """
    values = tuple(revisions)
    for revision in values:
        if not _real_int(revision) or revision < 1:
            raise ResumptionRefused(
                RESUMPTION_HISTORY_GAP,
                f"the persisted chain holds {revision!r}; a recorded review revision is a "
                "whole number of at least 1, because revision 0 is the reconstructed state "
                "and is not itself recorded",
            )

    ordered = tuple(sorted(values))
    if len(set(ordered)) != len(ordered):
        duplicates = sorted({r for r in ordered if ordered.count(r) > 1})
        raise ResumptionRefused(
            RESUMPTION_DUPLICATE_REVISION,
            f"revision(s) {duplicates} were read more than once; two rows for one revision "
            "are two answers to one question, and neither is chosen here",
        )

    expected = tuple(range(1, len(ordered) + 1))
    if ordered != expected:
        missing = sorted(set(expected) - set(ordered))
        raise ResumptionRefused(
            RESUMPTION_HISTORY_GAP,
            f"the persisted chain is {list(ordered)} where a contiguous chain from 1 is "
            f"{list(expected)}; revision(s) {missing} were never recorded, and the state "
            "between two revisions cannot be replayed from the rows that exist",
        )
    return ordered


def check_expected_revision(expected_revision: Any, *, head_revision: int) -> None:
    """Refuses an expectation the persisted chain does not satisfy.

    `None` means the caller asserts nothing and is always satisfied. Otherwise the
    expectation must equal the persisted head exactly: an expectation AHEAD of the chain
    is a caller that believes in a review nobody recorded, and one BEHIND it is a caller
    holding a stale view. Both are refused rather than reconciled, because the only
    authority on which revision a project is at is J22's own rows.
    """
    if expected_revision is None:
        return
    if not _real_int(expected_revision) or expected_revision < 0:
        raise ResumptionRefused(
            RESUMPTION_EXPECTED_REVISION_INVALID,
            f"expected_revision was {expected_revision!r}; a review revision is a "
            "non-negative whole number",
        )
    if expected_revision > head_revision:
        raise ResumptionRefused(
            RESUMPTION_EXPECTED_REVISION_AHEAD,
            f"the caller expects revision {expected_revision} but the project's persisted "
            f"review chain ends at {head_revision}; a revision nobody recorded is not "
            "resumed",
        )
    if expected_revision < head_revision:
        raise ResumptionRefused(
            RESUMPTION_EXPECTED_REVISION_STALE,
            f"the caller expects revision {expected_revision} but the project has since "
            f"reached {head_revision}; a stale view of a review is never resumed as if it "
            "were current",
        )


# --------------------------------------------------------------------------------------
# Resolution task ownership — the reusable pre-validation seam.
# --------------------------------------------------------------------------------------
def task_index_from_exception_package(exception_package: Any) -> dict[str, tuple[str, ...]]:
    """The connection-task contract as plain data: review package id -> its task ids.

    The adapter the seam is called with. Reading the package's own grouping rather than
    flattening every task into one set is the entire point: which CONNECTION a task
    belongs to is exactly what is being validated.
    """
    index: dict[str, tuple[str, ...]] = {}
    for group in exception_package.connection_tasks:
        index[group.review_package_id] = tuple(task.task_id for task in group.tasks)
    return index


def validate_resolution_task_ownership(
    *,
    package_id: Any,
    task_index: Mapping[str, Sequence[str]],
    resolutions: Sequence[Any],
) -> None:
    """Refuses any resolution naming a task that is not the target connection's.

    Run BEFORE the resolver, so a refused request leaves nothing behind. Four refusals:

      * the target package is not a connection of this project;
      * the same task id is named twice — 7AJ's inline guard does not catch this, and a
        request that answers one question twice is ambiguous rather than merely wrong;
      * the task belongs to ANOTHER connection of this project;
      * the task belongs to no connection of this project, which is the same predicate
        whether the id was invented or belongs to another project: this seam consults one
        project's tasks and looking further would be the cross-project read it exists to
        prevent.
    """
    if not isinstance(package_id, str) or not package_id:
        raise ResolutionTaskRefused(
            RESOLUTION_TARGET_UNKNOWN,
            f"the target package was {package_id!r}; a resolution is validated against the "
            "connection it addresses",
        )
    if package_id not in task_index:
        raise ResolutionTaskRefused(
            RESOLUTION_TARGET_UNKNOWN,
            f"{package_id!r} is not a connection of this project (connections: "
            f"{sorted(task_index)}); nothing was validated and nothing was applied",
        )

    own = tuple(task_index[package_id])
    seen: list[str] = []
    for resolution in resolutions:
        task_id = getattr(resolution, "task_id", None)
        if not isinstance(task_id, str) or not task_id:
            raise ResolutionTaskRefused(
                RESOLUTION_TASK_NOT_OF_THIS_PROJECT,
                f"a resolution carries task id {task_id!r}; a task id is a non-empty str",
            )
        seen.append(task_id)

    duplicates = sorted({task_id for task_id in seen if seen.count(task_id) > 1})
    if duplicates:
        raise ResolutionTaskRefused(
            RESOLUTION_TASK_DUPLICATED,
            f"task id(s) {duplicates} were named more than once; a request that answers one "
            "question twice is refused rather than applied twice",
        )

    foreign = [task_id for task_id in seen if task_id not in own]
    if foreign:
        elsewhere = sorted(
            other for other, tasks in task_index.items()
            if other != package_id and any(task_id in tasks for task_id in foreign)
        )
        if elsewhere:
            raise ResolutionTaskRefused(
                RESOLUTION_TASK_FOREIGN_CONNECTION,
                f"resolution task id(s) {foreign} belong to {elsewhere}, not to "
                f"{package_id}; a resolution recorded for one connection is never applied "
                "to another",
            )
        raise ResolutionTaskRefused(
            RESOLUTION_TASK_NOT_OF_THIS_PROJECT,
            f"resolution task id(s) {foreign} address no task of {package_id} in this "
            f"project (its tasks are {list(own)}); a resolution is only ever applied to the "
            "connection whose task it answers",
        )


# --------------------------------------------------------------------------------------
# Reading the recorded answers back.
# --------------------------------------------------------------------------------------
def _resolution_from_payload(payload: Mapping, *, where: str) -> HumanResolution:
    """One persisted resolution payload as a `HumanResolution`, or a refusal.

    Every field is read and none is defaulted: a payload missing an answer is refused
    rather than replayed as a blank one, because a blank human answer and an unrecorded
    one would then look the same.
    """
    if not isinstance(payload, Mapping):
        raise ResumptionRefused(
            RESUMPTION_RESOLUTION_UNREADABLE,
            f"{where}: the recorded resolution is {type(payload).__name__} rather than a "
            "mapping",
        )
    missing = [
        name for name in ("task_id", "task_type", "answer_type", "answer")
        if name not in payload
    ]
    if missing:
        raise ResumptionRefused(
            RESUMPTION_RESOLUTION_UNREADABLE,
            f"{where}: the recorded resolution is missing {missing}; a replayed answer is "
            "never assembled from a partial payload",
        )
    return HumanResolution(
        task_id=payload["task_id"],
        task_type=payload["task_type"],
        answer_type=payload["answer_type"],
        answer=payload["answer"],
        evidence=payload.get("evidence", ""),
    )


def resolutions_from_item(item: ReviewSnapshotItem) -> tuple[HumanResolution, ...]:
    """The human answers one persisted item records, in task order.

    A task that is still OPEN records no resolution and contributes none. This reads the
    answers that were actually given; it never synthesises one for an unanswered task.
    """
    answers: list[HumanResolution] = []
    for index, task in enumerate(item.tasks):
        payload = task.get("resolution") if isinstance(task, Mapping) else None
        if payload is None:
            continue
        answers.append(
            _resolution_from_payload(
                payload, where=f"{item.review_package_id}#task[{index}]"
            )
        )
    return tuple(answers)


# --------------------------------------------------------------------------------------
# The replay.
# --------------------------------------------------------------------------------------
def _advanced_item(snapshot: ReviewSnapshot) -> ReviewSnapshotItem:
    """The one connection this revision advanced.

    A J22 revision records the WHOLE project picture — one item per connection — so
    "which connection moved at revision N" is the item whose `last_processed_revision` IS
    N. Exactly one connection is advanced per recorded revision, because 7AJ processes one
    connection per resolve; a revision that moved none, or more than one, is not a state
    this module can replay and is refused rather than guessed at.
    """
    advanced = [
        item for item in snapshot.items
        if item.last_processed_revision == snapshot.review_revision
    ]
    if len(advanced) != 1:
        raise ResumptionRefused(
            RESUMPTION_NOT_ONE_CONNECTION,
            f"revision {snapshot.review_revision} advances {len(advanced)} connection(s) "
            f"({[item.review_package_id for item in advanced]}); exactly one connection is "
            "processed per recorded revision",
        )
    return advanced[0]


def replay_revision(
    workflow: ProjectWorkflowState, *, snapshot: ReviewSnapshot
) -> ProjectWorkflowState:
    """The next state, rebuilt from one persisted revision and nothing else.

    Every connection state comes from the snapshot's own items, decoded by J22's codec,
    and the project decision is the recorded one. The single connection this revision
    advanced also has its recorded answers re-applied to the exception contract, so the
    package a later revision's tasks are read from is the package history actually had.

    The advanced connection's stage record is assembled EMPTY. Its genuine 7AA pipeline,
    7AD rerun, 7AE gate, 7AF dispatch and 7AG verification objects were never persisted —
    J22 records their projection — so a resumed workflow has no honest way to supply them
    and does not invent any. Its PROJECTION, which is what a review is rendered from, is
    complete.
    """
    if not isinstance(workflow, ProjectWorkflowState):
        raise TypeError(
            f"workflow must be a ProjectWorkflowState (got {type(workflow).__name__})."
        )
    if not isinstance(snapshot, ReviewSnapshot):
        raise TypeError(
            f"snapshot must be a ReviewSnapshot (got {type(snapshot).__name__})."
        )
    if snapshot.project_id != workflow.project_id:
        raise ResumptionRefused(
            RESUMPTION_SNAPSHOT_NOT_OF_THIS_PROJECT,
            f"the snapshot belongs to {snapshot.project_id!r} while the workflow belongs to "
            f"{workflow.project_id!r}; a review state is never replayed onto another "
            "project",
        )
    if snapshot.review_revision != workflow.revision + 1:
        raise ResumptionRefused(
            RESUMPTION_HISTORY_GAP,
            f"revision {snapshot.review_revision} cannot follow the workflow's revision "
            f"{workflow.revision}; revisions are replayed in ascending order with no gap",
        )

    by_package: dict[str, ReviewSnapshotItem] = {}
    for item in snapshot.items:
        if item.review_package_id in by_package:
            raise ResumptionRefused(
                RESUMPTION_INCOMPLETE_SNAPSHOT,
                f"revision {snapshot.review_revision} records "
                f"{item.review_package_id!r} more than once; the persisted picture of a "
                "revision names each connection once",
            )
        by_package[item.review_package_id] = item

    known = [connection.package_id for connection in workflow.connections]
    unknown = sorted(set(by_package) - set(known))
    if unknown:
        raise ResumptionRefused(
            RESUMPTION_PACKAGE_UNKNOWN,
            f"revision {snapshot.review_revision} records connection(s) {unknown} that are "
            f"not connections of this project ({known}); a recorded revision is never "
            "replayed onto a project it does not describe",
        )
    missing = sorted(set(known) - set(by_package))
    if missing:
        raise ResumptionRefused(
            RESUMPTION_INCOMPLETE_SNAPSHOT,
            f"revision {snapshot.review_revision} records no state for connection(s) "
            f"{missing}; a partial revision is never replayed as a whole one",
        )

    advanced = _advanced_item(snapshot)

    if workflow._exception_package is None and resolutions_from_item(advanced):
        raise ResumptionRefused(
            RESUMPTION_RESOLUTION_UNREADABLE,
            f"revision {snapshot.review_revision} records human answers for "
            f"{advanced.review_package_id} but the workflow carries no exception "
            "contract to apply them to",
        )

    new_connections = tuple(
        _state_from_item(by_package[connection.package_id])
        for connection in workflow.connections
    )
    new_records = tuple(
        ProjectConnectionRecord(
            pipeline=None,
            rerun_outcome=None,
            gate_result=None,
            dispatch_result=None,
            verification_result=None,
        )
        if connection.package_id == advanced.review_package_id
        else record
        for connection, record in zip(workflow.connections, workflow.connection_records)
    )

    exception_package = workflow._exception_package
    answers = resolutions_from_item(advanced)
    if exception_package is not None:
        validate_resolution_task_ownership(
            package_id=advanced.review_package_id,
            task_index=task_index_from_exception_package(exception_package),
            resolutions=answers,
        )
        for answer in answers:
            exception_package = apply_human_resolution(exception_package, answer)

    replayed = _assemble_state(
        workflow.project_id,
        snapshot.review_revision,
        new_connections,
        new_records,
        snapshot.project_status,
        workflow._collection,
        workflow._initial,
        exception_package,
        workflow._member_rows,
        workflow._member_placements,
        workflow._section_matcher,
        extra_lines=(),
        intake=workflow._intake,
    )

    # The guard, over EVERY connection the revision records — not only the advanced one,
    # because a replay that silently lost a connection's recorded state would otherwise
    # pass by not being looked at.
    for report in agreement_reports(replayed, snapshot):
        if not report.agrees:
            raise ResumptionRefused(
                RESUMPTION_DISAGREES_WITH_HISTORY,
                f"revision {snapshot.review_revision}: {report.detail}. A replay that "
                "disagrees with the rows it was replayed from is refused rather than "
                "returned; the persisted record is what happened.",
            )
    return replayed


def agreement_reports(
    workflow: ProjectWorkflowState, snapshot: ReviewSnapshot
) -> tuple[AgreementReport, ...]:
    """One report per connection, comparing a rebuilt state against the revision it came from.

    The comparison is per connection rather than per revision so that a divergence names
    both the revision and the connection it is in: a project-level "these disagree" would
    leave a reviewer to find which of the project's connections moved.
    """
    by_package = {item.review_package_id: item for item in snapshot.items}
    reports: list[AgreementReport] = []
    for connection in workflow.connections:
        item = by_package.get(connection.package_id)
        if item is None:
            reports.append(
                AgreementReport(
                    agrees=False,
                    diverged_key="review_package_id",
                    detail=(
                        f"revision {snapshot.review_revision} records no state for "
                        f"connection {connection.package_id}"
                    ),
                )
            )
            continue
        report = compare_agreement(
            agreement_projection_from_state(
                review_revision=snapshot.review_revision, connection=connection
            ),
            agreement_projection_from_item(snapshot, item),
        )
        if not report.agrees:
            reports.append(
                AgreementReport(
                    agrees=False,
                    diverged_key=report.diverged_key,
                    detail=(
                        f"connection {connection.package_id}: {report.detail}"
                    ),
                )
            )
        else:
            reports.append(report)
    return tuple(reports)


# --------------------------------------------------------------------------------------
# Reading the persisted chain.
# --------------------------------------------------------------------------------------
def _read_history(project_id: str) -> tuple[ReviewSnapshot, ...]:
    """Every recorded revision of one project, ascending.

    The reads are the persistence layer's own (`latest_recorded_revision` for the head,
    then `load_review_snapshot` per revision); this module writes no query of its own and
    opens no client at import. A revision whose header exists but whose rows cannot be
    read back is a gap and is refused as one.
    """
    from app.engineering_data import connection_review_repository as review_store

    head = review_store.latest_recorded_revision(project_id)
    if head is None:
        return ()
    snapshots: list[ReviewSnapshot] = []
    for revision in range(1, head + 1):
        snapshot = review_store.load_review_snapshot(project_id, revision)
        if snapshot is None:
            raise ResumptionRefused(
                RESUMPTION_HISTORY_GAP,
                f"the project's review chain reaches revision {head} but revision "
                f"{revision} could not be read back; a chain with an unreadable link is "
                "not resumed",
            )
        snapshots.append(snapshot)
    return tuple(snapshots)


def _head_revision(snapshots: Sequence[ReviewSnapshot]) -> int:
    """The highest recorded revision, or 0 when nothing is recorded.

    An empty history is revision 0: the reconstructed state is the project's review
    state, and no revision has been recorded on top of it.
    """
    return max((snapshot.review_revision for snapshot in snapshots), default=0)


def resume_project_workflow(
    project_id: str,
    *,
    section_matcher: Any,
    expected_revision: Any = None,
    repository: Any = None,
    history: Sequence[ReviewSnapshot] | None = None,
    document_id: str | None = None,
) -> ResumedProjectWorkflow | None:
    """One project's review workflow, resumed from revision 0 plus J22's revisions.

    Returns None when the PROJECT does not exist — the reconstruction seam's own
    convention. Refuses (`ResumptionRefused`, see `RESUMPTION_REFUSALS`) when the project
    exists but its persisted review chain cannot be replayed.

    An EMPTY history is not a refusal: the project simply has no recorded revision, the
    head is 0, and the revision-0 reconstruction is returned unchanged.

    `history` is the persisted chain, and `repository` the project store the
    reconstruction reads through. Both are injectable so that this seam can be exercised
    without a database; neither changes what is read in production, where `history` is
    read from the persistence layer and `repository` defaults to the production store.

    `document_id` (Milestone J61) is passed to the reconstruction unchanged and is `None`
    by default, so every existing caller resumes exactly the workflow it resumed before.
    This module neither chooses a document nor decides what one means: it is the
    reconstruction's own parameter, carried to it and not interpreted here.
    """
    from app.production_review.project_workflow_reconstruction import (
        reconstruct_project_workflow,
    )

    reconstruction = reconstruct_project_workflow(
        project_id, section_matcher=section_matcher, repository=repository,
        document_id=document_id,
    )
    if reconstruction is None:
        return None

    snapshots = tuple(history) if history is not None else _read_history(project_id)
    ordered = validate_history([snapshot.review_revision for snapshot in snapshots])
    head = _head_revision(snapshots)
    check_expected_revision(expected_revision, head_revision=head)

    workflow = reconstruction.workflow
    agreements: list[AgreementReport] = []
    for snapshot in sorted(snapshots, key=lambda item: item.review_revision):
        workflow = replay_revision(workflow, snapshot=snapshot)
        agreements.extend(agreement_reports(workflow, snapshot))

    return ResumedProjectWorkflow(
        project_id=project_id,
        workflow=workflow,
        head_revision=head,
        revisions=ordered,
        agreements=tuple(agreements),
        # The reconstruction's own provenance, carried through verbatim. It is read from
        # the reconstruction this call already performed, never re-queried: a second read
        # could only disagree with the one the workflow was built from.
        capture_run_ids=tuple(reconstruction.capture_run_ids),
    )
