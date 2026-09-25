"""
Milestone J13 — the project lifecycle's terminal state, DERIVED from evidence.

Before this module, both successful extraction paths wrote the constant
`"review"` into `projects.status` (app/drawing_reading/dxf_parser.py,
app/pipeline.py). A project whose every member and connection was explicitly
resolved therefore read exactly like one waiting on a human, and no code
anywhere could produce any other value — the lifecycle had no exit.

This module is the exit. It answers one question:

    does the evidence this extraction persisted PROVE that nothing is left
    for a human to look at?

and the answer is yes only when every piece of that evidence is explicitly
present and explicitly complete.

THE RULE IS NOT "no obvious problems" == done. It is "all required evidence is
explicitly resolved" == done. The difference is the whole point: anything this
function cannot read as complete — a status it does not recognise, a status the
row does not state at all, an unmatched section, a J11 discard, a drawing that
yielded nothing at all — leaves the project at "review". Absence is not
evidence of completeness, and this module never treats it as such.

This module is PURE. It performs no Supabase call, no network call, no
filesystem access, no AI call, no geometry, no catalogue lookup, no reviewer
action and no mutation. Its inputs are the persisted evidence handed to it and
its output is a status — which is what makes every rule below unit-testable
without a database.
"""

from dataclasses import dataclass
from typing import Iterable, Mapping

from app.validation.dxf_drop_accounting import headline_from_warnings
from app.validation.page_coverage import PageCoverage

__all__ = [
    "COMPLETE_REVIEW_STATUSES",
    "ProjectStatusDecision",
    "STATUS_DONE",
    "STATUS_REVIEW",
    "derive_project_status",
    "review_status_of",
]


# ======================================================================================
# The vocabulary — read out of the repository, not invented here.
# ======================================================================================
# Both extraction paths write exactly two member/connection review states, and
# they are the only two the repository's extraction code produces or compares:
#
#     app/validation/rules.py:200,218,232        "extracted" / "review_required"
#     app/pipeline.py:266,310,344,364            "extracted" / "review_required"
#     app/drawing_reading/dxf_parser.py:480      "review_required"
#
# and the one comparison either path makes is for the blocking value
# (app/pipeline.py:403). "extracted" is the repository's own name for a record
# the extractor does not hold for a human — the accepted, terminal state of the
# EXTRACTION lifecycle:
#
#     tests/test_reviewed_connection_specification.py:110
#         review_status="extracted",  # the repo's own status for raw,
#                                     # unreviewed pipeline output
#
# So `"extracted"` is the accepted state and `"review_required"` blocks. No new
# state is invented below, and none is renamed.
#
# "approved" is deliberately NOT in this set. It exists in the repository
# (app/cad_engine/reviewed_connection_specification.py:REVIEW_STATUS_APPROVED)
# as a REVIEWER-WORKFLOW decision — "a reviewer accepted this record". It is
# written only by app/cad_engine/, which is not reachable from either
# extraction path, so no row produced by an extraction carries it. Treating it
# as terminal here would mean writing a rule for a value extraction cannot
# produce; leaving it out fails closed. When a milestone makes reviewer
# resolution production-reachable, this set is the one line that milestone
# changes.
COMPLETE_REVIEW_STATUSES = frozenset({"extracted"})

# The explicit blocking state. Named so the derivation can say WHICH of the two
# failure modes it saw: an explicit request for review, or the absence of any
# statement at all.
REVIEW_REQUIRED_STATUS = "review_required"

STATUS_DONE = "done"
STATUS_REVIEW = "review"

# Deterministic blocker codes, in the order they are reported. Every one of
# them is a reason the project may NOT be called done.
BLOCKER_NO_EXTRACTED_EVIDENCE = "NO_EXTRACTED_EVIDENCE"
BLOCKER_MEMBER_REVIEW_REQUIRED = "MEMBER_REVIEW_REQUIRED"
BLOCKER_MEMBER_REVIEW_STATUS_MISSING = "MEMBER_REVIEW_STATUS_MISSING"
BLOCKER_MEMBER_REVIEW_STATUS_UNKNOWN = "MEMBER_REVIEW_STATUS_UNKNOWN"
BLOCKER_CONNECTION_REVIEW_REQUIRED = "CONNECTION_REVIEW_REQUIRED"
BLOCKER_CONNECTION_REVIEW_STATUS_MISSING = "CONNECTION_REVIEW_STATUS_MISSING"
BLOCKER_CONNECTION_REVIEW_STATUS_UNKNOWN = "CONNECTION_REVIEW_STATUS_UNKNOWN"
BLOCKER_UNMATCHED_SECTIONS = "UNMATCHED_SECTIONS"
BLOCKER_DXF_DROPPED_EVIDENCE = "DXF_DROPPED_EVIDENCE"

# Milestone J15 — the drawing set itself. These three say the run did not read
# the whole of what it was given, which is a precondition of every other claim
# here: no finding about a member can describe a page nobody looked at.
BLOCKER_PAGE_COVERAGE_UNKNOWN = "PAGE_COVERAGE_UNKNOWN"
BLOCKER_PAGE_NOT_ANALYSED = "PAGE_NOT_ANALYSED"
BLOCKER_PAGE_PARSE_FAILED = "PAGE_PARSE_FAILED"


@dataclass(frozen=True)
class ProjectStatusDecision:
    """The derived status, and the evidence that produced it.

    `blockers` is empty exactly when `status` is `"done"`. It exists for tests,
    diagnostics and any future reader that needs to say WHY a project is held —
    it is NOT persisted, and no new project column carries it: the project
    record's shape is unchanged by this milestone.
    """

    status: str
    blockers: tuple[str, ...]

    @property
    def is_done(self) -> bool:
        return self.status == STATUS_DONE

    def __str__(self) -> str:
        return self.status


def review_status_of(row: Mapping) -> str | None:
    """The review state a persisted member/connection row states, or None.

    `None` means the row states NO state — which is not the same as stating an
    accepted one, and is the distinction the derivation below turns on. A blank
    string is treated as no statement for the same reason: it is not a value
    this repository's vocabulary contains.
    """
    value = row.get("review_status") if row is not None else None
    return value if isinstance(value, str) and value.strip() else None


def _classify(statuses, *, required: str, missing: str, unknown: str) -> list[str]:
    """The blocker codes one collection of review states produces, or []."""
    counts = {required: 0, missing: 0, unknown: 0}
    for status in statuses:
        # Deliberately exhaustive and fail-closed: only a string that is in the
        # accepted set counts as complete, and the two ways to fail are told
        # apart so a reader can see which one happened.
        if status is None or not (isinstance(status, str) and status.strip()):
            counts[missing] += 1
        elif status in COMPLETE_REVIEW_STATUSES:
            continue
        elif status == REVIEW_REQUIRED_STATUS:
            counts[required] += 1
        else:
            counts[unknown] += 1
    return [f"{code}:{count}" for code, count in counts.items() if count]


def derive_project_status(
    *,
    member_review_statuses: Iterable[str | None],
    connection_review_statuses: Iterable[str | None],
    unmatched_sections: Iterable[str],
    warnings: Iterable[str],
    coverage: PageCoverage | None = None,
) -> ProjectStatusDecision:
    """The project status the persisted evidence supports — `"done"` or `"review"`.

    Inputs are exactly the evidence the extraction persisted: each extracted
    member's and connection's review state, the drawn tokens the catalogue did
    not answer for, the project's warning list, and — since Milestone J15 — how
    much of the uploaded drawing set was actually read. Nothing else is
    consulted, and nothing is inferred from the absence of a complaint.

    `coverage` defaults to `None`, which is NOT "no coverage problem": a caller
    that states no coverage has not shown that the whole drawing set was read,
    and absence of evidence is not evidence of completeness here any more than
    it is anywhere else in this function. `None` therefore blocks the project
    exactly as an incomplete coverage does. Only a coverage record that proves
    every page of the document was analysed and parsed leaves the project
    unblocked.
    """
    members = tuple(member_review_statuses)
    connections = tuple(connection_review_statuses)
    unmatched = tuple(unmatched_sections or ())
    warning_lines = tuple(warnings or ())

    blockers: list[str] = []

    # Milestone J15, first, because it is a precondition of everything below it.
    # The extraction's other findings all describe something a page contained;
    # these describe pages that were never read, or were read and could not be
    # understood. A project is only as complete as the drawing set it was read
    # from, and a per-page reading failure is not an empty page: it is the
    # absence of an answer where an answer was owed.
    if coverage is None:
        blockers.append(f"{BLOCKER_PAGE_COVERAGE_UNKNOWN}:1")
    elif not coverage.is_complete:
        if coverage.not_analysed_pages:
            blockers.append(f"{BLOCKER_PAGE_NOT_ANALYSED}:{coverage.not_analysed_pages}")
        if coverage.parse_failed_pages:
            blockers.append(f"{BLOCKER_PAGE_PARSE_FAILED}:{coverage.parse_failed_pages}")
        # Either the document's own page count was never established, or the
        # record's counts contradict each other. Both are "coverage was not
        # proven", and neither may be read as a smaller number of unread pages.
        if not coverage.identity_holds:
            blockers.append(f"{BLOCKER_PAGE_COVERAGE_UNKNOWN}:1")

    # A drawing that yielded NOTHING is not a completed project. The extraction
    # contract states what it extracted; it never states that the source was
    # genuinely empty, so zero objects is the ABSENCE of evidence rather than
    # evidence of an empty drawing — and absence fails closed. A project with
    # members and no connections is NOT this case: "no connection details were
    # identified in this file" is a legitimate, reportable outcome
    # (app/report/pdf_generator.py), so only the total is treated as unknown.
    if not members and not connections:
        blockers.append(f"{BLOCKER_NO_EXTRACTED_EVIDENCE}:1")

    blockers += _classify(
        members,
        required=BLOCKER_MEMBER_REVIEW_REQUIRED,
        missing=BLOCKER_MEMBER_REVIEW_STATUS_MISSING,
        unknown=BLOCKER_MEMBER_REVIEW_STATUS_UNKNOWN,
    )
    blockers += _classify(
        connections,
        required=BLOCKER_CONNECTION_REVIEW_REQUIRED,
        missing=BLOCKER_CONNECTION_REVIEW_STATUS_MISSING,
        unknown=BLOCKER_CONNECTION_REVIEW_STATUS_UNKNOWN,
    )

    if unmatched:
        blockers.append(f"{BLOCKER_UNMATCHED_SECTIONS}:{len(unmatched)}")

    # Milestone J11, read back through J11's own contract rather than by
    # pattern-matching prose: the accounting's own helper recognises the
    # accounting's own line. A run that discarded evidence is not a run that
    # finished, however cleanly the rest of it extracted.
    if headline_from_warnings(warning_lines) is not None:
        blockers.append(f"{BLOCKER_DXF_DROPPED_EVIDENCE}:1")

    status = STATUS_REVIEW if blockers else STATUS_DONE
    return ProjectStatusDecision(status=status, blockers=tuple(blockers))
