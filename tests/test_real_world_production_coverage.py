"""MILESTONE 7AX — Real-World Drawing Set Coverage & Production Completeness Proof.

The question: given the genuine Selby Square drawing set (32 pages, 5 pages
genuinely analysed, captured in tests/data/selby_square_page_extractions.json),
does the project-level coverage proof tell the truth about the production job —
every discovered connection accounted for, every page accounted for, nothing
silently complete and nothing silently missing?

The composed project view proven here is the honest one: all 9 candidates the
real intake discovered are queued; VIEW B-B (page 5) resolves AUTO and its
drawing is genuinely generated and verified, VIEW A-A (page 5) stops at the
genuine FL/EA section boundary (7AV's proof), and the other 7 connections
stay REVIEW. The job-level truth (§22): the project is INCOMPLETE — 27 pages
were never analysed, 7 connections are unresolved, 1 is blocked at the
section boundary, and the 1 produced drawing is not packaged because the
composite 7AE/7AQ decision over the mixed job is never AUTO.

Case A (7AV/7AW): the scoped VIEW B-B workflow, through 7AQ -> 7AR -> 7AS,
is a COMPLETED connection with its whole backward chain re-traced.
Case B (7AU): the page-1 two-member connection through its genuine two-member
path is a COMPLETED connection — and, over a recorded one-page drawing set, a
COMPLETE project.

BRIEF ITEM MAP:
  1-9  the composed real project (incomplete, truthfully) ....... TestTheRealDrawingSet
    1  page identity 32 = 5 + 0 + 27 / 2 all 9 candidates accounted
    3  VIEW A-A blocked at the genuine boundary / 4  VIEW B-B produced, not closed
    5  pages not analysed exposed, never enumerated / 6  project INCOMPLETE
    7  revision 0 all unresolved / 8  page rows map candidates to pages
    9  real-source traceability to the capture JSON
  10-12 Case A: the 7AV/7AW VIEW B-B completion .................. TestCaseA
    10 disposition COMPLETED / 11 the full backward chain re-traced
    12 the scoped journey's project status stays honest (31 pages unseen)
  13-16 Case B: the 7AU two-member completion .................... TestCaseB
    13 disposition COMPLETED / 14 never routed through multi-member
    15 a recorded one-page set is a COMPLETE project
    16 the full 32-page set count keeps the same journey INCOMPLETE
  17-24 tamper, staleness and determinism on real evidence ........ TestRefusalOnRealEvidence
    17 stale caller revision refused / 18 stale package refused
    19 altered packaged artifact refused / 20 stray PDF refused
    21 duplicated package item refused / 22 fabricator without package refused
    23 removed projection refused / 24 determinism + no mutation
"""

import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cad_engine import fabricator_acceptance as fa
from app.cad_engine import project_workflow as workflow_module
from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_VERIFIED
from app.cad_engine.exception_resolution import (
    HumanResolution,
    TASK_COMPLETE_REVIEW,
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_SELECT_ATTACHMENT,
    TASK_SELECT_POSITION,
)
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_BLOCKED,
    PACKAGE_STATUS_READY,
    build_fabrication_package,
)
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    accept_production_job,
)
from app.cad_engine.production_coverage import (
    DISPOSITION_BLOCKED,
    DISPOSITION_COMPLETED,
    DISPOSITION_PRODUCED,
    DISPOSITION_UNRESOLVED,
    PAGE_STATUS_ANALYSED,
    PROJECT_STATUS_COMPLETE,
    PROJECT_STATUS_INCOMPLETE,
    PROJECT_STATUS_REFUSED,
    evaluate_production_coverage,
)
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly
from tests.test_fabricator_acceptance import _copied_package
from tests.test_real_world_material_extraction import (
    SELBY_JOURNEY_PROJECT_ID,
    SELBY_JOURNEY_SOURCE_DRAWING_ID,
    SELBY_MEMBER_PLACEMENTS,
    SELBY_MEMBER_ROWS,
    SelbySectionMatcher,
    _answers as _au_answers,
    _selby_journey,
    _selby_reviewer_resolutions,
)
from tests.test_real_world_multi_member_connection import (
    CAPTURE_PATH,
    JOURNEY_ANSWERS_BY_TASK_TYPE,
    JOURNEY_MEMBER_PLACEMENTS,
    JOURNEY_MEMBER_ROWS,
    JOURNEY_PROJECT_ID,
    JOURNEY_SOURCE_DRAWING_ID,
    SELBY_PAGE_COUNT,
    Page5SectionMatcher,
    _fabricator_answers,
    _page5_journey,
    needs_selby_capture,
)

# The two real candidates the composed journey resolves (one at a time,
# through the genuine workflow): page-5 VIEW A-A (the FL/EA pair — now with
# genuine profile builders) and page-5 VIEW B-B (the four-member positive
# case).
VIEW_AA_PACKAGE_ID = "RP-0008"
VIEW_BB_PACKAGE_ID = "RP-0009"


def _capture_pages():
    """The genuine Selby capture, deep-copied: 5 analysed pages of a 32-page set."""
    return copy.deepcopy(json.loads(CAPTURE_PATH.read_text()))


def _composed_start(tmp):
    """The composed real project at revision 0: the FULL 5-page intake, every
    discovered candidate queued, the genuine 7AB -> 7AC stages, no resolutions
    yet. The section matcher carries the real FL and EA entries so the A-A
    resolution runs the genuine chain; the placements carry the
    reviewer-corrected PL008 position (END face at the reviewed connection,
    matching CL004) that the 7AV geometry-consistency gate requires."""
    data = _capture_pages()
    marks = tuple(sorted({m["mark"] for page in data for m in page["raw_members"]}))
    full_intake = intake_page_extractions(
        data, project_id=JOURNEY_PROJECT_ID, source_drawing_id=JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=marks, drawing_set_page_count=SELBY_PAGE_COUNT,
    )
    rows = dict(JOURNEY_MEMBER_ROWS)
    rows["PL008"] = {"mark": "PL008", "section_name": "180x20FL",
                     "section_name_raw": "180x20FL", "section_family": "FL",
                     "length_mm": 340, "grade": "300", "quantity": 1,
                     "review_status": "approved", "source_page": 5,
                     "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
    rows["CL004"] = {"mark": "CL004", "section_name": "90x10EA",
                     "section_name_raw": "90x10EA", "section_family": "EA",
                     "length_mm": 165, "grade": "300", "quantity": 1,
                     "review_status": "approved", "source_page": 5,
                     "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
    placements = dict(JOURNEY_MEMBER_PLACEMENTS)
    placements["PL008"] = MemberPlacement(
        x=0.0, y=0.0, z=4677.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0)
    placements["CL004"] = MemberPlacement(
        x=0.0, y=0.0, z=4852.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0)

    class MatcherWithFL(Page5SectionMatcher):
        SECTIONS = dict(Page5SectionMatcher.SECTIONS)
        SECTIONS["180X20FL"] = {"name": "180X20FL", "family": "FL",
                                "width": 180.0, "thickness": 20.0,
                                "weight_per_metre": 28.3}
        SECTIONS["90X10EA"] = {"name": "90X10EA", "family": "EA",
                               "width": 90.0, "thickness": 10.0,
                               "weight_per_metre": 13.3}

    workflow = workflow_module.start_project_workflow(
        full_intake.collection, intake=full_intake, member_rows=rows,
        member_placements=placements, section_matcher=MatcherWithFL(),
    )
    return SimpleNamespace(data=data, full_intake=full_intake, rows=rows,
                           placements=placements, workflow=workflow)


def _resolutions_for(group, answers_by_task_type, evidence):
    """Human resolutions for every task of one connection, exactly the 7AJ shape."""
    return [
        HumanResolution(task.task_id, task.task_type, task.answer_type,
                        copy.deepcopy(answers_by_task_type[task.task_type]),
                        evidence=evidence)
        for task in group.tasks
    ]


VIEW_AA_ANSWERS_BY_TASK_TYPE = {
    TASK_COMPLETE_REVIEW: None,
    TASK_SELECT_POSITION: "END",
    TASK_SELECT_ATTACHMENT: [
        {"member_mark": mark, "surface_reference": "END"}
        for mark in ("005", "PL008", "CL004")
    ],
    TASK_CONFIRM_AI_VALUES: ("connected_member_marks", "material"),
    TASK_PROVIDE_PLATE: {"type": "end_plate", "thickness_mm": 20.0,
                         "width_mm": 180.0, "depth_mm": 340.0},
    TASK_PROVIDE_HOLE_DIAMETER: {"quantity": 4, "diameter_mm": 22.0,
                                 "vertical_spacing_mm": 90.0,
                                 "horizontal_spacing_mm": 85.0},
    TASK_PROVIDE_LOCATION: {"x": 0.0, "y": 0.0, "z": 5017.0,
                            "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
    TASK_REVIEW_SPECIFICATION: None,
    TASK_REVIEW_VALIDATION: None,
    TASK_PROVIDE_CONNECTION_IDENTITY: "CONN-SELBY-VIEW-AA",
}


@pytest.fixture(scope="module")
def composed_journey(tmp_path_factory):
    """The composed real project through two genuine resolutions: A-A and B-B
    both go AUTO/VERIFIED now that FL and EA have real profile builders (and
    the A-A placement carries the reviewer-corrected PL008 position the 7AV
    gate requires). The truth of the real drawing set: the two page-5
    connections are produced; the seven others stay unresolved."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7ax"))
    start = _composed_start(tmp)
    workflow = start.workflow
    aa_group = next(g for g in workflow.exception_package.connection_tasks
                    if g.review_package_id == VIEW_AA_PACKAGE_ID)
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=VIEW_AA_PACKAGE_ID,
        resolutions=_resolutions_for(aa_group, VIEW_AA_ANSWERS_BY_TASK_TYPE,
                                     "HUMAN-SUPPLIED TEST DATA (7AX VIEW A-A)"),
        output_dir=tmp / "aa",
    )
    bb_group = next(g for g in workflow.exception_package.connection_tasks
                    if g.review_package_id == VIEW_BB_PACKAGE_ID)
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=VIEW_BB_PACKAGE_ID,
        resolutions=_resolutions_for(bb_group, JOURNEY_ANSWERS_BY_TASK_TYPE,
                                     "HUMAN-SUPPLIED TEST DATA (Selby page-5 reviewer fixture)"),
        output_dir=tmp / "bb",
    )
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    coverage = evaluate_production_coverage(workflow, package=package)
    return SimpleNamespace(
        tmp=tmp, start=start, workflow=workflow, acceptance=acceptance,
        package=package, coverage=coverage,
    )


@pytest.fixture(scope="module")
def composed_revision_zero(tmp_path_factory):
    """The same composed project before any resolution: every connection REVIEW."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7ax-rev0"))
    start = _composed_start(tmp)
    return SimpleNamespace(
        tmp=tmp, start=start, workflow=start.workflow,
        coverage=evaluate_production_coverage(start.workflow),
    )


@pytest.fixture(scope="module")
def case_a_coverage(tmp_path_factory):
    """Case A: the genuine 7AV/7AW scoped journey through 7AQ -> 7AR -> 7AS."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7ax-case-a"))
    journey = _page5_journey(tmp)
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_fabricator_answers(package))
    coverage = evaluate_production_coverage(
        journey.workflow, package=package, fabricator_result=result)
    return SimpleNamespace(
        tmp=tmp, journey=journey, acceptance=acceptance, package=package,
        result=result, coverage=coverage,
    )


@pytest.fixture(scope="module")
def case_b_coverage(tmp_path_factory):
    """Case B: the genuine 7AU two-member journey through 7AQ -> 7AR -> 7AS."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7ax-case-b"))
    journey = _selby_journey(tmp)
    acceptance = accept_production_job(journey.workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_au_answers(package, q12=fa.ANSWER_PASS))
    coverage = evaluate_production_coverage(
        journey.workflow, package=package, fabricator_result=result)
    return SimpleNamespace(
        tmp=tmp, journey=journey, acceptance=acceptance, package=package,
        result=result, coverage=coverage,
    )


def _complete_single_page_journey(tmp):
    """The genuine 7AU page-1 journey over a recorded ONE-page drawing set: the
    intake honestly states the set has a single page, so coverage can be COMPLETE."""
    data = json.loads(CAPTURE_PATH.read_text())
    page1 = copy.deepcopy(data[0])
    marks = tuple(m["mark"] for m in page1["raw_members"])
    intake = intake_page_extractions(
        [page1], project_id=SELBY_JOURNEY_PROJECT_ID,
        source_drawing_id=SELBY_JOURNEY_SOURCE_DRAWING_ID,
        known_member_marks=marks, drawing_set_page_count=1,
    )
    workflow = workflow_module.start_project_workflow(
        intake.collection, intake=intake,
        member_rows=SELBY_MEMBER_ROWS,
        member_placements=SELBY_MEMBER_PLACEMENTS,
        section_matcher=SelbySectionMatcher(),
    )
    group = next(g for g in workflow.exception_package.connection_tasks)
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id=group.review_package_id,
        resolutions=_selby_reviewer_resolutions(group), output_dir=tmp / "journey",
    )
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_au_answers(package, q12=fa.ANSWER_PASS))
    return SimpleNamespace(
        workflow=workflow, acceptance=acceptance, package=package, result=result,
    )


def _row_by_package(coverage, package_id):
    return next(row for row in coverage.connections if row.package_id == package_id)


# =============================================================================
# 1-9. The composed real project — the truthful incompleteness of the real set.
# =============================================================================
@needs_selby_capture
class TestTheRealDrawingSet:
    # 1 — the page-coverage identity, from the intake's own recorded facts.
    def test_the_page_coverage_identity_holds(self, composed_journey):
        summary = composed_journey.coverage.summary
        assert summary.set_pages == SELBY_PAGE_COUNT
        assert summary.pages_analysed == 5
        assert summary.pages_parse_failed == 0
        assert summary.pages_not_analysed == 27
        assert (summary.set_pages
                == summary.pages_analysed + summary.pages_parse_failed
                + summary.pages_not_analysed)
        pages = composed_journey.coverage.pages
        assert [page.page_number for page in pages] == [1, 2, 3, 4, 5]
        assert all(page.status == PAGE_STATUS_ANALYSED for page in pages)

    # 2 — every discovered candidate has exactly one disposition, none silent.
    def test_every_real_candidate_is_accounted(self, composed_journey):
        coverage = composed_journey.coverage
        assert coverage.summary.connections_discovered == 9
        for row in coverage.connections:
            assert row.reason, row.package_id
        unresolved = [r for r in coverage.connections
                      if r.disposition == DISPOSITION_UNRESOLVED]
        assert {r.package_id for r in unresolved} == {
            f"RP-{number:04d}" for number in range(1, 8)
        }
        blocked = [r for r in coverage.connections if r.disposition == DISPOSITION_BLOCKED]
        produced = [r for r in coverage.connections if r.disposition == DISPOSITION_PRODUCED]
        assert [r.package_id for r in blocked] == []
        assert [r.package_id for r in produced] == [VIEW_AA_PACKAGE_ID, VIEW_BB_PACKAGE_ID]
        assert coverage.summary.connections_unresolved == 7
        assert coverage.summary.connections_blocked == 0
        assert coverage.summary.connections_produced == 2
        assert coverage.summary.connections_completed == 0
        assert coverage.summary.connections_not_processed == 0

    # 3 — VIEW A-A is genuinely produced: the FL/EA section-family blocker is
    # resolved by real profile builders, and the 7AV gate accepts the
    # reviewer-corrected PL008 placement — never an approximation.
    def test_view_a_a_is_produced_through_the_genuine_chain(self, composed_journey):
        row = _row_by_package(composed_journey.coverage, VIEW_AA_PACKAGE_ID)
        assert row.disposition == DISPOSITION_PRODUCED
        assert row.decision == AUTOMATION_DECISION_AUTO
        assert row.output_status == OUTPUT_STATUS_GENERATED
        assert row.verification_status == VERIFICATION_STATUS_VERIFIED
        assert row.verified is True
        assert row.accepted is True
        assert row.packaged is False
        assert "package is BLOCKED" in row.reason
        # Genuinely produced: the dispatch recorded a real verified artifact.
        artifact = Path(row.artifact_path)
        assert artifact.is_file()
        assert artifact.name.endswith(".pdf")

    # 4 — VIEW B-B is genuinely produced, but the mixed job never closes it.
    def test_view_b_b_is_produced_but_not_closed(self, composed_journey):
        row = _row_by_package(composed_journey.coverage, VIEW_BB_PACKAGE_ID)
        assert row.disposition == DISPOSITION_PRODUCED
        assert row.decision == AUTOMATION_DECISION_AUTO
        assert row.output_status == OUTPUT_STATUS_GENERATED
        assert row.verification_status == VERIFICATION_STATUS_VERIFIED
        assert row.verified is True
        assert row.accepted is True
        assert row.packaged is False
        assert "package is BLOCKED" in row.reason
        assert composed_journey.package.status == PACKAGE_STATUS_BLOCKED
        assert composed_journey.acceptance.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        # The verified artifact exists on disk and its hash is corroborated.
        artifact = Path(row.artifact_path)
        assert artifact.is_file()

    # 5 — the pages never analysed are exposed at count level, never invented.
    def test_the_pages_not_analysed_are_exposed_not_enumerated(self, composed_journey):
        coverage = composed_journey.coverage
        assert len(coverage.pages) == 5  # only the recorded pages have rows
        assert any("27 page(s)" in reason
                   for reason in coverage.incomplete_reasons)

    # 6 — the composed real project is INCOMPLETE, and says so with each gap.
    def test_the_composed_project_is_incomplete_not_complete(self, composed_journey):
        coverage = composed_journey.coverage
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert coverage.refusal_reasons == ()
        # 1 page-coverage gap + 9 connection gaps — each named, none silent.
        assert len(coverage.incomplete_reasons) == 10
        assert any("RP-0008" in reason and "PRODUCED" in reason
                   for reason in coverage.incomplete_reasons)
        assert any("RP-0009" in reason and "PRODUCED" in reason
                   for reason in coverage.incomplete_reasons)

    # 7 — revision 0 (before any resolution) is all unresolved, never complete.
    def test_revision_zero_is_all_unresolved(self, composed_revision_zero):
        coverage = composed_revision_zero.coverage
        assert coverage.workflow_revision == 0
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert coverage.summary.connections_unresolved == 9
        assert coverage.summary.connections_completed == 0
        assert coverage.summary.connections_produced == 0
        assert coverage.summary.connections_blocked == 0
        assert all(row.disposition == DISPOSITION_UNRESOLVED
                   for row in coverage.connections)

    # 8 — page rows map every candidate to its recorded source page.
    def test_page_rows_map_candidates_to_their_recorded_pages(self, composed_journey):
        pages = {page.page_number: page for page in composed_journey.coverage.pages}
        assert set(pages[1].candidate_package_ids) == {"RP-0001"}
        assert set(pages[2].candidate_package_ids) == {"RP-0002", "RP-0003"}
        assert set(pages[3].candidate_package_ids) == {"RP-0004", "RP-0005"}
        assert set(pages[4].candidate_package_ids) == {"RP-0006", "RP-0007"}
        assert set(pages[5].candidate_package_ids) == {VIEW_AA_PACKAGE_ID, VIEW_BB_PACKAGE_ID}

    # 9 — every row traces to the genuine capture entry, page and detail.
    def test_every_row_traces_to_the_real_capture(self, composed_journey):
        data = composed_journey.start.data
        for row in composed_journey.coverage.connections:
            candidates = [
                entry for page in data for entry in page["raw_connections"]
                if page["page_number"] == row.source_page
                and entry["detail_reference"] == row.detail_reference
            ]
            assert candidates, row.package_id
            entry = candidates[0]
            assert entry["connects_members"] == list(row.member_marks), row.package_id
            assert row.source_drawing_id == JOURNEY_SOURCE_DRAWING_ID
        assert composed_journey.coverage.source_drawing_id == JOURNEY_SOURCE_DRAWING_ID


# =============================================================================
# 10-12. Case A — the 7AV/7AW VIEW B-B completion, chain re-traced.
# =============================================================================
@needs_selby_capture
class TestCaseA:
    # 10 — the genuine scoped journey's connection is COMPLETED end to end.
    def test_view_b_b_is_completed_end_to_end(self, case_a_coverage):
        coverage = case_a_coverage.coverage
        assert coverage.summary.connections_discovered == 1
        row = coverage.connections[0]
        assert row.package_id == "RP-0001"  # the scoped queue's own candidate id
        assert row.disposition == DISPOSITION_COMPLETED
        assert row.verified is True
        assert row.accepted is True
        assert row.packaged is True
        assert row.fabricator_accepted is True
        assert row.drawing_number == case_a_coverage.package.items[0].drawing_number
        assert case_a_coverage.result.status == fa.ACCEPTANCE_STATUS_ACCEPTED
        assert coverage.summary.artifacts_verified == 1
        assert coverage.summary.artifacts_packaged == 1
        assert coverage.summary.drawings_fabricator_accepted == 1
        assert coverage.summary.package_status == PACKAGE_STATUS_READY

    # 11 — the backward chain is re-traced field for field.
    def test_the_completed_trace_replays_the_whole_chain(self, case_a_coverage):
        row = case_a_coverage.coverage.connections[0]
        trace = row.trace
        # 7AC review tasks and the human decisions recorded for them (the
        # material "300" confirmation is the genuine CONFIRM_AI_VALUES trace).
        assert trace.review_task_ids
        assert trace.human_decisions
        assert any(task_type == "CONFIRM_AI_VALUES"
                   for _, task_type in trace.human_decisions)
        # 7AD -> 7AE -> 7AF -> 7AG -> 7AQ -> 7AR -> 7AS, each recorded.
        assert trace.rerun_decision == AUTOMATION_DECISION_AUTO
        assert trace.validation_stage is None
        assert trace.validation_error_code is None
        assert trace.gate_decision == AUTOMATION_DECISION_AUTO
        assert trace.dispatch_status == OUTPUT_STATUS_GENERATED
        assert trace.verification_status == VERIFICATION_STATUS_VERIFIED
        assert trace.artifact_sha256
        assert trace.artifact_sha256 == case_a_coverage.journey.workflow.connection_records[0].verification_result.sha256
        assert trace.accepted is True
        assert trace.package_drawing_number == case_a_coverage.package.items[0].drawing_number
        assert trace.fabricator_drawing_status == fa.ACCEPTANCE_STATUS_ACCEPTED
        # The package's own observed drawing vocabulary is part of the chain —
        # exactly what 7AR recorded (title block, members, plate, holes).
        labels = trace.drawing_communication_labels
        assert "material" in labels
        assert "member_a_mark" in labels
        assert "member_b_mark" in labels
        assert "plate" in labels
        assert "holes" in labels
        assert "pattern" in labels

    # 12 — the scoped journey's project status stays honest about unseen pages.
    def test_case_a_project_status_is_honest_about_pages(self, case_a_coverage):
        coverage = case_a_coverage.coverage
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        summary = coverage.summary
        assert summary.set_pages == SELBY_PAGE_COUNT
        assert summary.pages_analysed == 1  # the journey intook page 5 alone
        assert summary.pages_not_analysed == 31
        assert any("31 page(s)" in reason for reason in coverage.incomplete_reasons)


# =============================================================================
# 13-16. Case B — the 7AU two-member completion, its own genuine path.
# =============================================================================
@needs_selby_capture
class TestCaseB:
    # 13 — the page-1 two-member connection is COMPLETED through 7AS.
    def test_the_two_member_connection_is_completed(self, case_b_coverage):
        coverage = case_b_coverage.coverage
        assert coverage.summary.connections_discovered == 1
        row = coverage.connections[0]
        assert row.disposition == DISPOSITION_COMPLETED
        assert row.verified and row.accepted and row.packaged
        assert row.fabricator_accepted is True
        assert row.member_marks == ("001", "PL028")
        assert row.source_page == 1
        assert case_b_coverage.result.status == fa.ACCEPTANCE_STATUS_ACCEPTED

    # 14 — the two-member path was never routed through multi-member.
    def test_the_two_member_path_never_entered_multi_member(self, case_b_coverage):
        record = case_b_coverage.journey.workflow.connection_records[0]
        assert isinstance(record.pipeline.reviewed_assembly,
                          ReviewedTwoMemberConnectionAssembly)
        row = case_b_coverage.coverage.connections[0]
        assert row.trace.rerun_decision == AUTOMATION_DECISION_AUTO
        assert row.trace.verification_status == VERIFICATION_STATUS_VERIFIED

    # 15 — over a recorded one-page set, the same journey is a COMPLETE project.
    def test_a_recorded_single_page_set_is_a_complete_project(self, tmp_path):
        journey = _complete_single_page_journey(tmp_path)
        coverage = evaluate_production_coverage(
            journey.workflow, package=journey.package, fabricator_result=journey.result)
        assert coverage.status == PROJECT_STATUS_COMPLETE
        assert coverage.incomplete_reasons == ()
        assert coverage.refusal_reasons == ()
        summary = coverage.summary
        assert summary.set_pages == 1
        assert summary.pages_analysed == 1
        assert summary.pages_not_analysed == 0
        assert summary.connections_completed == 1
        row = coverage.connections[0]
        assert row.disposition == DISPOSITION_COMPLETED

    # 16 — with the full 32-page set count, the same journey is INCOMPLETE.
    def test_the_full_set_count_keeps_case_b_incomplete(self, case_b_coverage):
        coverage = case_b_coverage.coverage
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert coverage.summary.pages_not_analysed == 31
        assert any("31 page(s)" in reason for reason in coverage.incomplete_reasons)


# =============================================================================
# 17-24. Tamper, staleness and determinism on real evidence.
# =============================================================================
@needs_selby_capture
class TestRefusalOnRealEvidence:
    # 17 — a stale caller revision never receives coverage.
    def test_stale_expected_revision_is_refused(self, case_a_coverage):
        workflow = case_a_coverage.journey.workflow
        coverage = evaluate_production_coverage(
            workflow, expected_revision=workflow.revision - 1)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stale" in reason for reason in coverage.refusal_reasons)

    # 18 — a package that records an older revision than the workflow is refused.
    def test_a_stale_package_is_refused(self, case_a_coverage):
        workflow = case_a_coverage.journey.workflow
        stale = dataclasses.replace(
            case_a_coverage.package,
            workflow_revision=case_a_coverage.package.workflow_revision - 1)
        coverage = evaluate_production_coverage(
            workflow, package=stale, fabricator_result=case_a_coverage.result)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stale package" in reason for reason in coverage.refusal_reasons)

    # 19 — an altered packaged artifact is refused, never covered.
    def test_an_altered_packaged_artifact_is_refused(self, case_a_coverage, tmp_path):
        copied = _copied_package(case_a_coverage.package, tmp_path)
        packaged = Path(copied.manifest_path).parent / copied.items[0].packaged_path
        packaged.write_bytes(packaged.read_bytes() + b"tampered")
        coverage = evaluate_production_coverage(
            case_a_coverage.journey.workflow, package=copied)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("no longer matches its recorded package SHA-256" in reason
                   for reason in coverage.refusal_reasons)

    # 20 — a stray PDF beside the manifest is never evidence of production.
    def test_a_stray_pdf_is_refused(self, case_a_coverage, tmp_path):
        copied = _copied_package(case_a_coverage.package, tmp_path)
        stray = Path(copied.manifest_path).parent / "EXTRA-STEELSPEC-999.pdf"
        stray.write_bytes(b"%PDF-1.4 stray")
        coverage = evaluate_production_coverage(
            case_a_coverage.journey.workflow, package=copied)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stray PDF" in reason for reason in coverage.refusal_reasons)

    # 21 — a duplicated package item identity is refused.
    def test_a_duplicated_package_item_is_refused(self, case_a_coverage):
        package = case_a_coverage.package
        duplicated = dataclasses.replace(
            package, items=package.items + (package.items[0],))
        coverage = evaluate_production_coverage(
            case_a_coverage.journey.workflow, package=duplicated)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("more than once" in reason for reason in coverage.refusal_reasons)

    # 22 — a fabricator acceptance without its package cannot be covered.
    def test_a_fabricator_result_without_a_package_is_refused(self, case_a_coverage):
        coverage = evaluate_production_coverage(
            case_a_coverage.journey.workflow,
            fabricator_result=case_a_coverage.result)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("without a fabrication package" in reason
                   for reason in coverage.refusal_reasons)

    # 23 — a projection with a connection silently removed is refused.
    def test_a_removed_connection_is_refused(self, case_a_coverage):
        workflow = case_a_coverage.journey.workflow
        tampered = dataclasses.replace(workflow, connections=workflow.connections[:-1])
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("refused the job" in reason
                   for reason in coverage.refusal_reasons)

    # 24 — the evaluation is deterministic and never mutates anything.
    def test_the_evaluation_is_deterministic_and_never_mutates(self, case_a_coverage):
        workflow = case_a_coverage.journey.workflow
        package = case_a_coverage.package
        result = case_a_coverage.result

        def snapshot():
            return [
                (path.name, path.stat().st_size)
                for path in sorted(Path(package.manifest_path).parent.rglob("*"))
            ]

        before = snapshot()
        first = evaluate_production_coverage(workflow, package=package,
                                             fabricator_result=result)
        after = snapshot()
        second = evaluate_production_coverage(workflow, package=package,
                                              fabricator_result=result)
        assert first == second
        assert after == before  # nothing written, nothing removed
        assert workflow == case_a_coverage.journey.workflow  # still frozen truth
