"""MILESTONE 7AX — synthetic coverage and completeness proof unit tests.

The real-world truths live in tests/test_real_world_production_coverage.py;
this module pins the layer's contract on controlled (Arkles-based) evidence:
the page-accounting identity, every intake contradiction refusing the
evaluation, NOT_PROCESSED candidates surfaced (never silently dropped), the
three genuine job outcomes (produced-without-fabricator / produced-with-
fabricator-gap / completed-but-page-incomplete), every tamper refusal, and
the purity contract — determinism, no mutation, no AI/network/database, no
numeric literals in the module, dispositions drawn only from the recorded
vocabulary.

The Arkles capture used throughout (tests/data/arkles_strand_extraction.json
via tests.test_real_world_exception_proof) records 30 received pages of a
41-page set, 6 of them parse-failed, with 3 connections on page 7 — the
intake's own recorded numbers, never recomputed here.
"""

import ast
import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cad_engine import production_coverage as pc
from app.cad_engine import project_workflow as workflow_module
from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_VERIFIED
from app.cad_engine import fabricator_acceptance as fa
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_READY,
    build_fabrication_package,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    accept_production_job,
)
from app.cad_engine.production_coverage import (
    DISPOSITION_BLOCKED,
    DISPOSITION_COMPLETED,
    DISPOSITION_NOT_PROCESSED,
    DISPOSITION_PRODUCED,
    DISPOSITION_UNRESOLVED,
    PAGE_STATUS_ANALYSED,
    PAGE_STATUS_PARSE_FAILED,
    PROJECT_STATUS_INCOMPLETE,
    PROJECT_STATUS_REFUSED,
    evaluate_production_coverage,
)
from app.cad_engine.project_connection_review import create_project_connection_collection
from app.cad_engine.project_extraction_intake import intake_page_extractions
from tests.test_fabricator_acceptance import _answers as _fa_answers
from tests.test_fabricator_acceptance import _copied_package
from tests.test_project_extraction_intake import real_known_marks
from tests.test_project_workflow import (
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _start,
)
from tests.test_real_world_exception_proof import (
    ARKLES_REAL_AI_EXTRACTION,
    HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
    HUMAN_SUPPLIED_MEMBER_ROWS,
    HUMAN_SUPPLIED_SECTION_MATCHER,
)

REPO = Path(__file__).resolve().parents[1]
COVERAGE_MODULE_PATH = REPO / "app" / "cad_engine" / "production_coverage.py"

# The Arkles capture's own recorded numbers (see module docstring).
ARKLES_SET_PAGES = 41
ARKLES_PARSE_FAILED = 6
ARKLES_ANALYSED = 24
ARKLES_NOT_ANALYSED = 11
ARKLES_CANDIDATES = 3


def _arkles_start():
    """The genuine Arkles project at revision 0 (all three candidates REVIEW)."""
    intake, initial, built, workflow = _start()
    return intake, built, workflow


def _arkles_chain(tmp):
    """Revision 3 -> genuine 7AQ acceptance -> genuine 7AR package."""
    states = _full_sequence(tmp)
    workflow = states[-1]
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    return workflow, acceptance, package


def _scoped_workflow_with_full_intake(tmp):
    """A workflow that queued only the first Arkles candidate while its intake
    recorded all three — the honest NOT_PROCESSED construction."""
    intake, _, _, _ = _start()
    entries = [
        {**conn, "page_num": page["page_number"]}
        for page in ARKLES_REAL_AI_EXTRACTION
        if not page.get("parse_failed")
        for conn in (page.get("raw_connections") or [])
    ]
    scoped_collection = create_project_connection_collection(
        entries[:1], project_id=PROJECT_ID, source_drawing_id=SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
    )
    scoped_intake = dataclasses.replace(intake, collection=scoped_collection)
    workflow = workflow_module.start_project_workflow(
        scoped_collection, intake=scoped_intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )
    return dataclasses.replace(workflow, _intake=intake)


def _tampered_intake(workflow, **changes):
    """The workflow's intake with recorded page facts replaced — the intake's
    own collection is untouched, so the contradiction is isolated."""
    return dataclasses.replace(workflow, _intake=dataclasses.replace(workflow.intake, **changes))


@pytest.fixture(scope="module")
def arkles_no_fabricator(tmp_path_factory):
    """PRODUCED everywhere: verified and accepted, packaged READY, no 7AS yet."""
    tmp = Path(tmp_path_factory.mktemp("7ax-arkles-no-fab"))
    workflow, acceptance, package = _arkles_chain(tmp)
    return SimpleNamespace(
        tmp=tmp, workflow=workflow, acceptance=acceptance, package=package,
        coverage=evaluate_production_coverage(workflow, package=package),
    )


@pytest.fixture(scope="module")
def arkles_fabricator_gap(tmp_path_factory):
    """PRODUCED everywhere with the fabricator's honest NOT_ACCEPTED (material
    gap) on record: the drawings exist but the job did not close."""
    tmp = Path(tmp_path_factory.mktemp("7ax-arkles-gap"))
    workflow, acceptance, package = _arkles_chain(tmp)
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_fa_answers(package))
    return SimpleNamespace(
        tmp=tmp, workflow=workflow, acceptance=acceptance, package=package,
        result=result,
        coverage=evaluate_production_coverage(
            workflow, package=package, fabricator_result=result),
    )


@pytest.fixture(scope="module")
def arkles_completed(tmp_path_factory):
    """COMPLETED per connection — yet the project stays INCOMPLETE: 17 of the
    drawing set's pages (11 never analysed + 6 parse-failed) have no coverage."""
    tmp = Path(tmp_path_factory.mktemp("7ax-arkles-done"))
    workflow, acceptance, package = _arkles_chain(tmp)
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_fa_answers(package, q12=fa.ANSWER_PASS))
    return SimpleNamespace(
        tmp=tmp, workflow=workflow, acceptance=acceptance, package=package,
        result=result,
        coverage=evaluate_production_coverage(
            workflow, package=package, fabricator_result=result),
    )


# =============================================================================
# 1. The page-accounting identity — recorded facts, never recomputed numbers.
# =============================================================================
class TestPageAccountingIdentity:
    def test_identity_holds_for_a_custom_intake_with_parse_failures(self, tmp_path):
        pages = [
            {"page_number": 1, "raw_connections": [], "drawing_number": "DW-1"},
            {"page_number": 2, "raw_connections": [], "drawing_number": "DW-1",
             "parse_failed": True},
            {"page_number": 3, "raw_connections": [], "drawing_number": "DW-1",
             "parse_failed": True},
        ]
        intake = intake_page_extractions(
            pages, project_id="PROJ-7AX-EMPTY", source_drawing_id="D-EMPTY",
            drawing_set_page_count=10,
        )
        workflow = workflow_module.start_project_workflow(
            intake.collection, intake=intake,
            member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
            member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
            section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
        )
        coverage = evaluate_production_coverage(workflow)
        summary = coverage.summary
        # received == analysed + parse_failed, and the set identity holds.
        assert summary.set_pages == 10
        assert summary.pages_analysed == 1
        assert summary.pages_parse_failed == 2
        assert summary.pages_not_analysed == 7
        assert summary.set_pages == (
            summary.pages_analysed + summary.pages_parse_failed
            + summary.pages_not_analysed)
        # The parse-failed page carries its honest row; the unanalysed pages
        # are accounted at count level only.
        assert [p.status for p in coverage.pages] == [
            PAGE_STATUS_ANALYSED, PAGE_STATUS_PARSE_FAILED, PAGE_STATUS_PARSE_FAILED]
        # No candidates, no refusals: INCOMPLETE with each gap named.
        assert summary.connections_discovered == 0
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert coverage.refusal_reasons == ()

    def test_identity_holds_for_the_genuine_arkles_intake(self, tmp_path):
        intake, _, workflow = _arkles_start()
        coverage = evaluate_production_coverage(workflow)
        summary = coverage.summary
        assert summary.set_pages == ARKLES_SET_PAGES
        assert summary.pages_analysed == ARKLES_ANALYSED
        assert summary.pages_parse_failed == ARKLES_PARSE_FAILED
        assert summary.pages_not_analysed == ARKLES_NOT_ANALYSED
        assert summary.set_pages == (
            summary.pages_analysed + summary.pages_parse_failed
            + summary.pages_not_analysed)
        # The intake's own recorded page numbers, in recorded order.
        assert [p.page_number for p in coverage.pages] == list(
            intake.analysed_page_numbers + intake.parse_failed_pages)


# =============================================================================
# 2. Intake contradictions refuse the evaluation — never quietly re-counted.
# =============================================================================
class TestIntakeContradictions:
    def test_a_duplicated_analysed_page_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, analysed_page_numbers=(1, 1, 2), pages_received=3,
            parse_failed_pages=(), drawing_set_page_count=3, pages_not_analysed=0)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("more than once" in reason for reason in coverage.refusal_reasons)

    def test_a_page_both_analysed_and_parse_failed_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, analysed_page_numbers=(1, 2), pages_received=3,
            parse_failed_pages=(2,), drawing_set_page_count=3, pages_not_analysed=0)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("as both analysed and parse-failed" in reason
                   for reason in coverage.refusal_reasons)

    def test_a_pages_received_mismatch_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, analysed_page_numbers=(1, 2), pages_received=3,
            parse_failed_pages=(), drawing_set_page_count=2, pages_not_analysed=0)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("received page" in reason for reason in coverage.refusal_reasons)

    def test_a_page_beyond_the_recorded_set_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, analysed_page_numbers=(1, 5), pages_received=2,
            parse_failed_pages=(), drawing_set_page_count=2, pages_not_analysed=0)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("beyond the recorded set" in reason
                   for reason in coverage.refusal_reasons)

    def test_a_pages_not_analysed_mismatch_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, analysed_page_numbers=(1,), pages_received=1,
            parse_failed_pages=(), drawing_set_page_count=5, pages_not_analysed=9)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("identity set == analysed + parse-failed + not-analysed is broken"
                   in reason for reason in coverage.refusal_reasons)

    def test_a_non_integer_page_number_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, analysed_page_numbers=("page-one",), pages_received=1,
            parse_failed_pages=(), drawing_set_page_count=1, pages_not_analysed=0)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("not a positive integer" in reason
                   for reason in coverage.refusal_reasons)

    def test_an_unanalysed_page_count_without_a_set_count_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = _tampered_intake(
            workflow, drawing_set_page_count=None, pages_not_analysed=4)
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("without a drawing set page count" in reason
                   for reason in coverage.refusal_reasons)


# =============================================================================
# 3. NOT_PROCESSED — a discovered candidate never queued is surfaced, never
#    silently dropped.
# =============================================================================
class TestNotProcessed:
    def test_intake_only_candidates_are_not_processed_never_dropped(self, tmp_path):
        workflow = _scoped_workflow_with_full_intake(tmp_path)
        coverage = evaluate_production_coverage(workflow)
        rows = {row.package_id: row for row in coverage.connections}
        assert set(rows) == {"RP-0001", "RP-0002", "RP-0003"}
        assert rows["RP-0001"].disposition == DISPOSITION_UNRESOLVED
        assert rows["RP-0002"].disposition == DISPOSITION_NOT_PROCESSED
        assert rows["RP-0003"].disposition == DISPOSITION_NOT_PROCESSED
        assert "never queued for review" in rows["RP-0002"].reason
        assert rows["RP-0002"].verified is False
        assert rows["RP-0002"].accepted is None
        assert rows["RP-0002"].decision is None
        # The source page row carries all three candidates.
        page_rows = {p.page_number: p for p in coverage.pages}
        assert set(page_rows[7].candidate_package_ids) == {"RP-0001", "RP-0002", "RP-0003"}
        summary = coverage.summary
        assert summary.connections_discovered == 3
        assert summary.connections_not_processed == 2
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert any("RP-0002" in reason and "NOT_PROCESSED" in reason
                   for reason in coverage.incomplete_reasons)

    def test_every_disposition_is_drawn_from_the_recorded_vocabulary(self, tmp_path):
        workflow = _scoped_workflow_with_full_intake(tmp_path)
        coverage = evaluate_production_coverage(workflow)
        assert all(row.disposition in pc.DISPOSITIONS for row in coverage.connections)
        assert pc.DISPOSITIONS == (
            DISPOSITION_COMPLETED, DISPOSITION_PRODUCED, DISPOSITION_UNRESOLVED,
            DISPOSITION_BLOCKED, DISPOSITION_NOT_PROCESSED,
        )


# =============================================================================
# 4. The three genuine job outcomes over the same Arkles evidence.
# =============================================================================
class TestArklesJobOutcomes:
    def test_verified_and_packaged_without_a_fabricator_is_produced(self,
                                                                    arkles_no_fabricator):
        coverage = arkles_no_fabricator.coverage
        assert arkles_no_fabricator.acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        assert arkles_no_fabricator.package.status == PACKAGE_STATUS_READY
        assert coverage.summary.connections_produced == 3
        assert coverage.summary.connections_completed == 0
        assert coverage.summary.artifacts_verified == 3
        assert coverage.summary.artifacts_packaged == 3
        assert coverage.summary.drawings_fabricator_accepted == 0
        for row in coverage.connections:
            assert row.disposition == DISPOSITION_PRODUCED
            assert row.verified is True and row.accepted is True
            assert row.packaged is True
            assert row.fabricator_accepted is None
            assert "no fabricator acceptance was recorded" in row.reason
        assert coverage.status == PROJECT_STATUS_INCOMPLETE

    def test_the_fabricators_honest_gap_keeps_everything_produced(self,
                                                                  arkles_fabricator_gap):
        coverage = arkles_fabricator_gap.coverage
        assert arkles_fabricator_gap.result.status == fa.ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert coverage.summary.connections_produced == 3
        assert coverage.summary.drawings_fabricator_accepted == 0
        for row in coverage.connections:
            assert row.disposition == DISPOSITION_PRODUCED
            assert row.packaged is True
            assert row.fabricator_accepted is False
            assert "fabricator recorded NOT_ACCEPTED" in row.reason
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert coverage.refusal_reasons == ()

    def test_completed_connections_still_leave_the_project_incomplete(self,
                                                                      arkles_completed):
        coverage = arkles_completed.coverage
        assert arkles_completed.result.status == fa.ACCEPTANCE_STATUS_ACCEPTED
        summary = coverage.summary
        assert summary.set_pages == ARKLES_SET_PAGES
        assert summary.pages_analysed == ARKLES_ANALYSED
        assert summary.pages_parse_failed == ARKLES_PARSE_FAILED
        assert summary.pages_not_analysed == ARKLES_NOT_ANALYSED
        assert summary.connections_discovered == ARKLES_CANDIDATES
        assert summary.connections_completed == 3
        assert summary.artifacts_verified == 3
        assert summary.artifacts_packaged == 3
        assert summary.drawings_fabricator_accepted == 3
        assert summary.package_status == PACKAGE_STATUS_READY
        # 17 un-covered pages + zero connection gaps = exactly two reasons,
        # both about pages — the connection work is genuinely done.
        assert coverage.status == PROJECT_STATUS_INCOMPLETE
        assert len(coverage.incomplete_reasons) == 2
        assert any("never analysed" in reason for reason in coverage.incomplete_reasons)
        assert any("failed to parse" in reason for reason in coverage.incomplete_reasons)

    def test_the_completed_traces_replay_the_recorded_chain(self, arkles_completed):
        for row in arkles_completed.coverage.connections:
            trace = row.trace
            assert trace.review_task_ids
            assert trace.human_decisions
            # Every recorded human decision belongs to one of the connection's
            # own review tasks — nothing foreign, nothing invented.
            assert {task_id for task_id, _ in trace.human_decisions} <= set(
                trace.review_task_ids)
            assert trace.rerun_decision == AUTOMATION_DECISION_AUTO
            assert trace.validation_stage is None
            assert trace.validation_error_code is None
            assert trace.gate_decision == AUTOMATION_DECISION_AUTO
            assert trace.dispatch_status == OUTPUT_STATUS_GENERATED
            assert trace.verification_status == VERIFICATION_STATUS_VERIFIED
            assert trace.artifact_sha256
            assert trace.accepted is True
            assert trace.package_drawing_number == row.drawing_number
            assert trace.fabricator_drawing_status == fa.ACCEPTANCE_STATUS_ACCEPTED


# =============================================================================
# 5. Tamper and staleness refusals on genuine chain evidence.
# =============================================================================
class TestTamperRefusals:
    def test_a_stale_caller_revision_is_refused(self, arkles_completed):
        coverage = evaluate_production_coverage(
            arkles_completed.workflow,
            expected_revision=arkles_completed.workflow.revision - 1)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stale" in reason for reason in coverage.refusal_reasons)

    def test_a_stale_package_is_refused(self, arkles_completed):
        package = arkles_completed.package
        stale = dataclasses.replace(
            package, workflow_revision=package.workflow_revision - 1)
        coverage = evaluate_production_coverage(
            arkles_completed.workflow, package=stale)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stale package" in reason for reason in coverage.refusal_reasons)

    def test_a_duplicated_package_item_is_refused(self, arkles_completed):
        package = arkles_completed.package
        duplicated = dataclasses.replace(package, items=package.items + (package.items[0],))
        coverage = evaluate_production_coverage(
            arkles_completed.workflow, package=duplicated)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("more than once" in reason for reason in coverage.refusal_reasons)

    def test_a_stray_pdf_is_refused(self, arkles_completed, tmp_path):
        copied = _copied_package(arkles_completed.package, tmp_path)
        stray = Path(copied.manifest_path).parent / "EXTRA-STEELSPEC-999.pdf"
        stray.write_bytes(b"%PDF-1.4 stray")
        coverage = evaluate_production_coverage(
            arkles_completed.workflow, package=copied)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stray PDF" in reason for reason in coverage.refusal_reasons)

    def test_an_altered_packaged_artifact_is_refused(self, arkles_completed, tmp_path):
        copied = _copied_package(arkles_completed.package, tmp_path)
        packaged = Path(copied.manifest_path).parent / copied.items[0].packaged_path
        packaged.write_bytes(packaged.read_bytes() + b"tampered")
        coverage = evaluate_production_coverage(
            arkles_completed.workflow, package=copied)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("no longer matches its recorded package SHA-256" in reason
                   for reason in coverage.refusal_reasons)

    def test_a_removed_connection_is_refused(self, arkles_completed):
        workflow = arkles_completed.workflow
        tampered = dataclasses.replace(workflow, connections=workflow.connections[:-1])
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("refused the job" in reason for reason in coverage.refusal_reasons)

    def test_an_auto_marked_unprocessed_projection_is_refused(self, tmp_path):
        _, _, workflow = _arkles_start()
        tampered = dataclasses.replace(
            workflow,
            connections=tuple(
                dataclasses.replace(state, decision=AUTOMATION_DECISION_AUTO)
                for state in workflow.connections),
        )
        coverage = evaluate_production_coverage(tampered)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("disagrees with the recorded decision" in reason
                   for reason in coverage.refusal_reasons)

    def test_a_fabricator_result_without_a_package_is_refused(self, arkles_completed):
        coverage = evaluate_production_coverage(
            arkles_completed.workflow, fabricator_result=arkles_completed.result)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("without a fabrication package" in reason
                   for reason in coverage.refusal_reasons)

    def test_a_stale_fabricator_acceptance_is_refused(self, arkles_completed):
        result = arkles_completed.result
        stale = dataclasses.replace(result, workflow_revision=result.workflow_revision - 1)
        coverage = evaluate_production_coverage(
            arkles_completed.workflow, package=arkles_completed.package,
            fabricator_result=stale)
        assert coverage.status == PROJECT_STATUS_REFUSED
        assert any("stale fabricator acceptance" in reason
                   for reason in coverage.refusal_reasons)


# =============================================================================
# 6. Determinism, no mutation, and the purity contract.
# =============================================================================
class TestDeterminismAndPurity:
    def test_the_evaluation_is_deterministic(self, arkles_completed):
        first = evaluate_production_coverage(
            arkles_completed.workflow, package=arkles_completed.package,
            fabricator_result=arkles_completed.result)
        second = evaluate_production_coverage(
            arkles_completed.workflow, package=arkles_completed.package,
            fabricator_result=arkles_completed.result)
        assert first == second

    def test_the_evaluation_never_mutates_anything(self, arkles_completed):
        parent = Path(arkles_completed.package.manifest_path).parent

        def snapshot():
            return sorted(
                (path.relative_to(parent).as_posix(), path.stat().st_size)
                for path in parent.rglob("*")
            )

        workflow = arkles_completed.workflow
        before = snapshot()
        evaluate_production_coverage(
            workflow, package=arkles_completed.package,
            fabricator_result=arkles_completed.result)
        assert snapshot() == before  # nothing written, nothing removed
        # The workflow state itself is unchanged (frozen, still the same truth).
        assert workflow == arkles_completed.workflow

    def test_the_module_imports_without_the_environment(self):
        # app.config raises KeyError('SUPABASE_URL') at import time; this module
        # must never pull it in (no AI, no database, no network).
        env = {key: value for key, value in os.environ.items() if key != "SUPABASE_URL"}
        result = subprocess.run(
            [sys.executable, "-c", "import app.cad_engine.production_coverage"],
            env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_the_module_imports_only_pure_engine_and_stdlib(self):
        tree = ast.parse(COVERAGE_MODULE_PATH.read_text())
        forbidden_roots = {"requests", "urllib", "httpx", "socket", "http",
                           "supabase", "app.config"}
        stdlib_roots = {"hashlib", "dataclasses", "pathlib", "typing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in stdlib_roots, alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] in stdlib_roots:
                    continue  # a stdlib from-import, e.g. dataclasses
                assert parts[0] == "app" and len(parts) > 2 \
                    and parts[1] == "cad_engine", node.module
                assert not any(part in forbidden_roots for part in parts), node.module

    def test_the_module_contains_no_numeric_literals(self):
        # The pinned precedent (test_drawing_dispatch): a coverage layer must
        # never invent a dimension, a page number, a count or a size — every
        # number in a result comes from the recorded evidence.
        tree = ast.parse(COVERAGE_MODULE_PATH.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                    and not isinstance(node.value, bool):
                raise AssertionError(
                    f"numeric literal {node.value!r} found in production_coverage.py "
                    "— coverage must never invent a number; page counts and "
                    "dispositions come only from recorded evidence")
            if isinstance(node, ast.Num):
                raise AssertionError(
                    f"numeric literal {node.n!r} found in production_coverage.py")
