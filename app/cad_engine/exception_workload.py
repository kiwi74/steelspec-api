"""
Milestone 7B2 — REAL CANDIDATE EXCEPTION WORKLOAD MAP.

A pure, deterministic, project-level view over the 7AK review contract:
it groups the project's candidates by the contract's own existing fields —
blocker code, task type, source page, material, decision, output and
verification state — exposing counts, candidate IDs, pending tasks and
the actions the contract already authorises. It answers only one
question:

    "What does the current review workload look like, as a whole?"

It never answers "what should be done first". There is no prioritisation:
no risk, confidence, severity-of-work, importance, ease, urgency or any
weighted ranking — such concepts do not exist in this module's input and
are never invented. Ordering follows explicit deterministic rules only
(see ORDERING below) and carries no implication of importance.

HARD RULES:

  - CONSUMES THE CONTRACT ONLY. The sole input is a ProjectReviewContract
    produced by the existing 7AK contract layer; workflow internals are
    never read.
  - A VIEW, NEVER A SECOND SOURCE OF TRUTH. Every projected value is
    copied verbatim from the contract; the map is a frozen snapshot.
    Candidate identity (RP-0001..RP-0051) is preserved exactly — never
    renumbered, merged, deduplicated, reordered or replaced.
  - NO DECISIONS, NO NEW VOCABULARY. No new codes, task types,
    provenance labels, statuses or material values. Unknown contract
    values are projected verbatim, never dropped, never guessed.
  - MISSING IS MISSING. A candidate without a material stays in the
    missing-material group; nothing is filled in.
  - IMMUTABLE AND DETERMINISTIC. Frozen dataclasses only; the same
    contract always maps to the same map; a built map never changes
    (point-in-time truth; a stale map stays visibly stale).
  - REFUSES CONTRADICTION. A contract whose candidates are not unique or
    whose blocker/task pairings are inconsistent is refused at build
    time — the map never projects a contradictory workload silently.

ORDERING (deterministic; implies no importance):
  - rows follow the contract's own item order (submission order)
  - candidate IDs within a group follow contract order
  - groups are sorted by name (blocker codes, task types, actions),
    by ascending page number, and materials by value with the missing
    group last
"""

from collections import Counter
from dataclasses import dataclass

from app.cad_engine.review_contract import ProjectReviewContract


@dataclass(frozen=True)
class WorkloadCandidateRow:
    """One candidate as the workload sees it — a verbatim projection of the
    fields the workload map groups by. Values are copied exactly from the
    contract item; nothing is derived or interpreted."""
    candidate_id: str
    source_page: int | None
    source_drawing_id: str | None
    revision: int
    decision: str
    blocker_codes: tuple[str, ...]
    task_types: tuple[str, ...]
    pending_task_types: tuple[str, ...]
    material: str | None
    output_status: str | None
    verification_status: str | None
    available_actions: tuple[str, ...]


@dataclass(frozen=True)
class WorkloadCodeGroup:
    """All candidates carrying a given blocker code."""
    blocker_code: str
    task_type: str | None
    count: int
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkloadTaskGroup:
    """All candidates carrying a given task type, split by whether the task
    still needs action (required and not yet resolved)."""
    task_type: str
    count: int
    pending_count: int
    candidate_ids: tuple[str, ...]
    pending_candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkloadPageGroup:
    """All candidates sourced from a given drawing page."""
    source_page: int
    count: int
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkloadMaterialGroup:
    """All candidates sharing a material value; `material` is None for the
    genuinely-missing group (missing is never filled)."""
    material: str | None
    count: int
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExceptionWorkloadMap:
    """The frozen workload map: rows in contract (submission) order plus the
    deterministic groupings. A view only — the contract remains the sole
    source of truth."""
    project_id: str | None
    revision: int
    candidate_count: int
    rows: tuple[WorkloadCandidateRow, ...]
    decision_counts: tuple[tuple[str, int], ...]
    blocker_code_groups: tuple[WorkloadCodeGroup, ...]
    task_type_groups: tuple[WorkloadTaskGroup, ...]
    page_groups: tuple[WorkloadPageGroup, ...]
    material_groups: tuple[WorkloadMaterialGroup, ...]
    output_status_counts: tuple[tuple[str | None, int], ...]
    verification_status_counts: tuple[tuple[str | None, int], ...]
    requires_action_count: int
    available_action_counts: tuple[tuple[str, int], ...]
    source_drawing_ids: tuple[str, ...]
    summary: tuple[str, ...]


def _counts_by(values):
    """(value, count) pairs sorted by value, with None last — deterministic
    and independent of first-appearance order."""
    items = Counter(values).items()
    return tuple(
        (value, count)
        for value, count in sorted(items, key=lambda pair: (pair[0] is None, pair[0])))


def _verify_contract(contract):
    """Refuse a contradictory contract before projecting it: candidate ids
    must be unique, every blocker must reference a task type the candidate
    actually carries, and one code always means the same task type across
    candidates."""
    seen = set()
    code_task_types = {}
    for item in contract.items:
        if item.package_id in seen:
            raise ValueError(
                f"duplicate candidate id {item.package_id!r} — the workload map "
                "refuses a contract whose candidates are not unique")
        seen.add(item.package_id)
        task_types = {task.task_type for task in item.tasks}
        for blocker in item.blockers:
            if blocker.task_type is not None and blocker.task_type not in task_types:
                raise ValueError(
                    f"candidate {item.package_id!r} has blocker {blocker.code!r} "
                    f"referencing task type {blocker.task_type!r}, which does not "
                    "appear among the candidate's tasks — the workload map refuses "
                    "inconsistent blocker/task pairings")
            previous = code_task_types.setdefault(blocker.code, blocker.task_type)
            if previous != blocker.task_type:
                raise ValueError(
                    f"blocker code {blocker.code!r} maps to task type "
                    f"{blocker.task_type!r} on candidate {item.package_id!r} but "
                    f"{previous!r} elsewhere — the workload map refuses a blocker "
                    "code that changes meaning across candidates")


def build_exception_workload_map(contract):
    """Build the deterministic workload map over a 7AK project contract.

    The map is a frozen point-in-time projection: all values are copied
    verbatim from the contract, groups are built by the ordering rules in
    the module docstring, and a contradictory contract is refused rather
    than projected."""
    if not isinstance(contract, ProjectReviewContract):
        raise TypeError(
            f"expected a ProjectReviewContract, got {type(contract).__name__}")
    _verify_contract(contract)

    rows = []
    code_rows = {}
    code_task_types = {}
    task_rows = {}
    page_rows = {}
    material_rows = {}
    for item in contract.items:
        rows.append(WorkloadCandidateRow(
            candidate_id=item.package_id,
            source_page=item.evidence.source_page,
            source_drawing_id=item.evidence.source_drawing_id,
            revision=item.revision,
            decision=item.decision,
            blocker_codes=tuple(blocker.code for blocker in item.blockers),
            task_types=tuple(task.task_type for task in item.tasks),
            pending_task_types=tuple(
                task.task_type for task in item.tasks
                if task.required and not task.resolved),
            material=item.ai_material,
            output_status=item.output_status,
            verification_status=item.verification_status,
            available_actions=tuple(item.available_actions),
        ))
        for blocker in item.blockers:
            code_rows.setdefault(blocker.code, []).append(item.package_id)
            code_task_types[blocker.code] = blocker.task_type
        for task_type in rows[-1].task_types:
            task_rows.setdefault(task_type, []).append(item.package_id)
        if item.evidence.source_page is not None:
            page_rows.setdefault(item.evidence.source_page, []).append(item.package_id)
        material_rows.setdefault(item.ai_material, []).append(item.package_id)

    code_groups = tuple(
        WorkloadCodeGroup(
            blocker_code=code,
            task_type=code_task_types[code],
            count=len(code_rows[code]),
            candidate_ids=tuple(code_rows[code]),
        )
        for code in sorted(code_rows))

    task_groups = tuple(
        WorkloadTaskGroup(
            task_type=task_type,
            count=len(task_rows[task_type]),
            pending_count=sum(
                1 for row in rows
                if row.candidate_id in task_rows[task_type]
                and task_type in row.pending_task_types),
            candidate_ids=tuple(task_rows[task_type]),
            pending_candidate_ids=tuple(
                row.candidate_id for row in rows
                if row.candidate_id in task_rows[task_type]
                and task_type in row.pending_task_types),
        )
        for task_type in sorted(task_rows))

    page_groups = tuple(
        WorkloadPageGroup(
            source_page=page,
            count=len(page_rows[page]),
            candidate_ids=tuple(page_rows[page]),
        )
        for page in sorted(page_rows))

    material_groups = tuple(
        WorkloadMaterialGroup(
            material=material,
            count=len(material_rows[material]),
            candidate_ids=tuple(material_rows[material]),
        )
        for material in sorted(material_rows, key=lambda m: (m is None, m)))

    source_drawing_ids = tuple(sorted(
        {row.source_drawing_id for row in rows if row.source_drawing_id is not None}))

    requires_action_count = sum(
        1 for item in contract.items if item.requires_action)

    summary = []
    summary.append(
        f"{len(rows)} candidates across {len(page_groups)} pages "
        f"of {', '.join(source_drawing_ids) if source_drawing_ids else 'no drawing'}")
    for decision, count in _counts_by(row.decision for row in rows):
        summary.append(f"decision {decision}: {count}")
    for group in code_groups:
        summary.append(f"blocker {group.blocker_code}: {group.count} candidates")
    for group in task_groups:
        summary.append(
            f"task {group.task_type}: {group.count} candidates "
            f"({group.pending_count} pending)")
    for group in page_groups:
        summary.append(f"page {group.source_page}: {group.count} candidates")
    for group in material_groups:
        summary.append(
            f"material {group.material}: {group.count} candidates"
            if group.material is not None
            else f"material missing: {group.count} candidates")
    summary.append(f"{requires_action_count} candidates require action")

    return ExceptionWorkloadMap(
        project_id=contract.project_id,
        revision=contract.revision,
        candidate_count=len(rows),
        rows=tuple(rows),
        decision_counts=_counts_by(row.decision for row in rows),
        blocker_code_groups=code_groups,
        task_type_groups=task_groups,
        page_groups=page_groups,
        material_groups=material_groups,
        output_status_counts=_counts_by(row.output_status for row in rows),
        verification_status_counts=_counts_by(row.verification_status for row in rows),
        requires_action_count=requires_action_count,
        available_action_counts=_counts_by(
            action for row in rows for action in row.available_actions),
        source_drawing_ids=source_drawing_ids,
        summary=tuple(summary),
    )


def workload_candidate(workload_map, candidate_id):
    """The row for one candidate by its exact candidate id.

    Unknown ids are refused — the map never invents a candidate."""
    for row in workload_map.rows:
        if row.candidate_id == candidate_id:
            return row
    raise ValueError(
        f"unknown candidate id {candidate_id!r} — the workload map projects "
        "exactly the candidates its contract carries")
