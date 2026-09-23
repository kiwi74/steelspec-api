"""MILESTONE 7B3 — Real-World Human Exception Review Proof.

The question: can a human reviewer take a REAL Selby Square connection
candidate from the existing 52-candidate REVIEW workload, understand exactly
what is required, provide the missing engineering information through the
existing exception-resolution mechanism, and have that candidate truthfully
progress through the existing workflow?

WHAT IS REAL AND WHAT IS NOT (read this first):
  REAL: the 52-candidate review workload (7B1) over the genuine 32-page Selby
  capture set, the genuine 7AJ workflow, the genuine 7AC resolution boundary,
  the genuine 7AD rerun / 7Z gate / 7AE fabrication gate / 7AF dispatch / 7AG
  verification chain, the genuine 7AQ production acceptance and the genuine
  7AN/7AO/7AP review UI. Every resolution in this file is a HumanResolution
  the way a reviewer would submit one; every answer payload is the 7AV/7AX
  B-B fixture (pinned in the 7AV milestone) and is labeled HUMAN-SUPPLIED.
  NOT REAL / NOT PROVEN HERE: AI correctness, safety of all 52 candidates,
  set completeness, and auto-resolution. 7B3 proves only that a human CAN
  resolve a real exception with the existing system — nothing is automated.

HARD RULES (unchanged from the milestone brief):
  - Human input remains human input: no engineering value is ever inferred
    from common practice, member size, AI confidence or similar candidates.
  - No parallel resolution path: everything goes through 7AC -> 7AD -> 7Z ->
    7AE -> 7AF -> 7AG. No bypass, no override, no force.
  - Only real candidates are used: RP-0001..RP-0052 from the genuine queue.
  - The 52-candidate regression: the queue stays 52 strong with ids,
    provenance and material distribution intact; only candidates a test
    explicitly resolves change (and only through the genuine API).
  - Existing modules (7Y/7W/7X/7Z/7AC/7AD/7AJ/7AK/7AM/7AX/7B1/7B2) stay
    authoritative: this file proves over them and modifies nothing.

BRIEF ITEM MAP:
  Case A — real success .............. TestRealCandidateCaseA (RP-0009, page-5 VIEW B-B)
  Case B — one task left open ........ TestRealCandidateCaseB (RP-0001, no plate)
  Case C — invalid answers refused ... TestRealCandidateCaseC (RP-0002)
  Case D — material stays missing .... TestRealCandidateCaseD (RP-0033/0034/0038/0040/0041/0049)
  end-to-end production acceptance ... TestEndToEndProductionProof (RP-0009)
  existing UI sufficient, driven ...... TestReviewUIVerticalSlice (RP-0009 through the UI)
  the 15 mandated negatives .......... TestNegativeProofs7B3
  52-candidate regression ............ TestReal52CandidateRegression
  determinism / no mutation .......... TestDeterminismAndPurity
"""

import copy
import dataclasses
import hashlib
import re
from pathlib import Path

import pytest

from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
    HumanResolution,
    TASK_COMPLETE_REVIEW,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_HOLE_DIAMETER,
    TASK_PROVIDE_LOCATION,
    TASK_PROVIDE_PLATE,
    TASK_SELECT_ATTACHMENT,
)
from app.cad_engine.exception_workload import build_exception_workload_map
from app.cad_engine.placement import MemberPlacement
from app.cad_engine.production_acceptance import accept_production_job
from app.cad_engine.project_workflow import (
    CrossProjectResolutionError,
    ProjectWorkflowState,
    StaleProjectWorkflowError,
    resolve_project_connection,
    start_project_workflow,
)
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import render_project_view
from app.review_ui import render as render_module
from app.review_ui import session as ui_session
from tests.test_real_world_exception_workload import (
    CODE_TO_TASK_TYPE,
    EXPECTED_IDS,
    REAL_BLOCKER_COUNTS,
    REAL_PAGE_COUNTS,
    REAL_TASK_COUNTS,
)
from tests.test_real_world_incremental_analysis import (
    JOURNEY_MEMBER_PLACEMENTS,
    JOURNEY_MEMBER_ROWS,
    JOURNEY_SOURCE_DRAWING_ID,
    _workflow_over,
)
from tests.test_real_world_multi_member_connection import (
    JOURNEY_ANSWERS_BY_TASK_TYPE,
    JOURNEY_CONNECTION_ID,
)
from tests.test_real_world_production_coverage import _resolutions_for
from tests.test_real_world_review_intake_provenance import (
    PINNED_SHA256,
    _accumulated,
    _intake,
)

# ---------------------------------------------------------------------------
# Pinned real facts (recorded from the genuine workflow; asserted, never rebuilt)
# ---------------------------------------------------------------------------

# Every human answer in this file carries this evidence note: the values are
# TEST-SUPPLIED, standing in for a human reviewer — never machine-derived.
EVIDENCE_7B3 = (
    "HUMAN-SUPPLIED TEST DATA (7B3 — a human reviewer reading drawing "
    "SELBY-C1136 page 5 VIEW B-B); the answers below were supplied by the "
    "test on the human reviewer's behalf."
)

MISSING_MATERIAL_IDS = ("RP-0033", "RP-0034", "RP-0038", "RP-0040",
                        "RP-0041", "RP-0049")

# The exact provenance split after the full RP-0009 resolution: the two
# AI-extracted fields the human CONFIRMED stay HUMAN_REVIEWED (the AI's own
# values, accepted — not replaced), the five fields the human SUPPLIED are
# HUMAN_SUPPLEMENTED, and every other AI reading keeps no human label at all.
RP0009_RESOLVED_PROVENANCE = {
    ("connected_member_marks", "HUMAN_REVIEWED"),
    ("position", "HUMAN_SUPPLEMENTED"),
    ("plate", "HUMAN_SUPPLEMENTED"),
    ("holes", "HUMAN_SUPPLEMENTED"),
    ("location", "HUMAN_SUPPLEMENTED"),
    ("attachments", "HUMAN_SUPPLEMENTED"),
    ("material", "HUMAN_REVIEWED"),
}

# The genuine artifact's byte size as produced by the real generator in this
# environment (pinned from two independent runs; not an engineering property).
RP0009_PDF_NAME = f"{JOURNEY_CONNECTION_ID}-fabrication.pdf"
RP0009_PDF_SIZE = 3981


# ---------------------------------------------------------------------------
# Helpers — the genuine chain, nothing mocked
# ---------------------------------------------------------------------------

def _fresh():
    """The genuine revision-0 52-candidate workflow over the real 32 pages."""
    return _workflow_over(_intake(_accumulated()))


def _group(workflow, package_id):
    return next(g for g in workflow.exception_package.connection_tasks
                if g.review_package_id == package_id)


def _state(workflow, package_id):
    return next(c for c in workflow.connections if c.package_id == package_id)


def _item(workflow, package_id):
    return next(i for i in build_project_review_contract(workflow).items
                if i.package_id == package_id)


def _resolve_full_rp0009(workflow, output_dir):
    """The complete human resolution of the real page-5 VIEW B-B candidate."""
    group = _group(workflow, "RP-0009")
    return resolve_project_connection(
        workflow, package_id="RP-0009",
        resolutions=_resolutions_for(
            group, copy.deepcopy(JOURNEY_ANSWERS_BY_TASK_TYPE), EVIDENCE_7B3),
        output_dir=output_dir,
    )


def _resolutions_except(group, skip_task_types, evidence=EVIDENCE_7B3):
    """All genuine B-B answers except the named task types left unanswered."""
    return [
        HumanResolution(task.task_id, task.task_type, task.answer_type,
                        copy.deepcopy(JOURNEY_ANSWERS_BY_TASK_TYPE[task.task_type]),
                        evidence=evidence)
        for task in group.tasks if task.task_type not in skip_task_types
    ]


def _record(workflow, package_id):
    """The recorded stage evidence for one candidate (by 7AD rerun identity)."""
    for record in workflow.connection_records:
        outcome = record.rerun_outcome
        if outcome is not None and outcome.review_package_id == package_id:
            return record
    raise AssertionError(f"no rerun record for {package_id}")


def _material_workflow():
    """The SAME genuine workflow but started with the existing
    request_material_specification=True flag (7AT) — the architecture's own
    route for a missing-material candidate. Nothing here is new."""
    intake = _intake(_accumulated())
    rows = dict(JOURNEY_MEMBER_ROWS)
    rows["PL008"] = {
        "mark": "PL008", "section_name": "180x20FL", "section_name_raw": "180x20FL",
        "section_family": "FL", "length_mm": 340, "grade": "300", "quantity": 1,
        "review_status": "approved", "source_page": 5,
        "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
    rows["CL004"] = {
        "mark": "CL004", "section_name": "90x10EA", "section_name_raw": "90x10EA",
        "section_family": "EA", "length_mm": 165, "grade": "300", "quantity": 1,
        "review_status": "approved", "source_page": 5,
        "source_drawing_id": JOURNEY_SOURCE_DRAWING_ID}
    placements = dict(JOURNEY_MEMBER_PLACEMENTS)
    placements["PL008"] = MemberPlacement(
        x=0.0, y=0.0, z=4987.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0)
    placements["CL004"] = MemberPlacement(
        x=0.0, y=0.0, z=4852.0, rotation_x=0.0, rotation_y=0.0, rotation_z=0.0)

    from tests.test_real_world_multi_member_connection import Page5SectionMatcher

    class MatcherWithFL(Page5SectionMatcher):
        SECTIONS = dict(Page5SectionMatcher.SECTIONS)
        SECTIONS["180X20FL"] = {"name": "180X20FL", "family": "FL",
                                "width": 180.0, "thickness": 20.0,
                                "weight_per_metre": 28.3}

    return start_project_workflow(
        intake.collection, intake=intake, member_rows=rows,
        member_placements=placements, section_matcher=MatcherWithFL(),
        request_material_specification=True,
    )


@pytest.fixture(autouse=True)
def _isolated_ui_session():
    """The review UI binds one workflow per process — never leak across tests."""
    yield
    if ui_session.is_bound():
        ui_session.clear_workflow()


# ---------------------------------------------------------------------------
# UI form plumbing shared with the slice: the SAME B-B answers encoded exactly
# as the UI's own inputs expect (the 7AN encode pattern, applied to real data).
# ---------------------------------------------------------------------------

def _encode_task(task):
    """One contract task -> {form field: text} using the UI's own naming."""
    names = render_module.task_input_names(task)
    value = copy.deepcopy(JOURNEY_ANSWERS_BY_TASK_TYPE[task.task_type])
    if task.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        marks, position, attachments = value
        return {
            names[0]: ",".join(marks),
            names[1]: position,
            names[2]: "\n".join(
                f"{a['member_mark']}|{a['surface_reference']}" for a in attachments),
        }
    if task.answer_type in (ANSWER_PLATE_VALUE, ANSWER_HOLES_VALUE, ANSWER_LOCATION_VALUE):
        return {names[0]: "\n".join(f"{key}: {v}" for key, v in value.items())}
    if task.answer_type in (ANSWER_APPROVE_REVIEW, ANSWER_ACKNOWLEDGMENT):
        return {names[0]: ""}
    if task.answer_type in (ANSWER_MEMBER_SELECTION, ANSWER_CONFIRMED_FIELDS):
        return {names[0]: ", ".join(value)}
    if task.answer_type == ANSWER_ATTACHMENTS_VALUE:
        return {names[0]: "\n".join(
            f"{a['member_mark']}|{a['surface_reference']}" for a in value)}
    return {names[0]: value}


def _form_for(workflow, package_id, **overrides):
    """The complete resolution form for one connection, as the page renders it."""
    contract = build_connection_review_contract(workflow, package_id)
    form = {
        ui_session.REVISION_FIELD: str(workflow.revision),
        ui_session.EVIDENCE_FIELD: EVIDENCE_7B3,
    }
    for task in contract.tasks:
        form.update(_encode_task(task))
    form.update(overrides)
    return form


def _page_body(html):
    return re.sub(r"<style.*?</style>", "", html, flags=re.S)


# =============================================================================
# Case A — a real candidate fully resolved by a human, through every stage.
# =============================================================================

class TestRealCandidateCaseA:
    """RP-0009 — Selby page-5 VIEW B-B (marks 005/PL018/PL032/PL025, the only
    candidate whose real members all carry supported sections). The human
    answers every open task; the genuine rerun decides; the genuine gates and
    generators follow. Nothing is short-circuited."""

    def test_full_human_resolution_reaches_auto_generated_verified(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        state = _state(advanced, "RP-0009")
        assert (state.decision, state.output_status, state.verification_status) == (
            "AUTO", "GENERATED", "VERIFIED")
        item = _item(advanced, "RP-0009")
        assert item.requires_action is False
        assert item.revision == 1
        assert item.last_processed_revision == 1

    def test_every_task_resolved_including_optional_identity(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        tasks = _item(advanced, "RP-0009").tasks
        assert all(task.resolved for task in tasks)
        assert {t.task_type for t in tasks} == set(JOURNEY_ANSWERS_BY_TASK_TYPE)

    def test_human_identity_recorded_verbatim(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert _state(advanced, "RP-0009").connection_id == JOURNEY_CONNECTION_ID
        assert _item(advanced, "RP-0009").connection_id == JOURNEY_CONNECTION_ID

    def test_provenance_split_is_honest(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        provenance = _item(advanced, "RP-0009").provenance
        assert {(p.field, p.provenance) for p in provenance} == RP0009_RESOLVED_PROVENANCE
        # Exactly the two fields the human CONFIRMED (accepted the AI's own
        # values) are HUMAN_REVIEWED — confirmed, not rewritten.
        reviewed = {p.field for p in provenance if p.provenance == "HUMAN_REVIEWED"}
        assert reviewed == {"connected_member_marks", "material"}
        supplied = {p.field for p in provenance if p.provenance == "HUMAN_SUPPLEMENTED"}
        assert supplied == {"position", "plate", "holes", "location", "attachments"}

    def test_ai_values_stay_verbatim(self, tmp_path):
        before = _item(_fresh(), "RP-0009")
        after = _item(_resolve_full_rp0009(_fresh(), tmp_path), "RP-0009")
        assert after.ai_member_references == before.ai_member_references == (
            "005", "PL018", "PL032", "PL025")
        assert after.ai_material == before.ai_material == "300"
        assert after.ai_bolt_readings == before.ai_bolt_readings
        assert after.ai_weld_readings == before.ai_weld_readings
        assert after.ai_connection_type == before.ai_connection_type
        assert after.ai_confidence == before.ai_confidence

    def test_unconfirmed_ai_fields_gain_no_human_label(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        fields = {p.field for p in _item(advanced, "RP-0009").provenance}
        # Weld readings, bolt readings, connection type and confidence were
        # NOT part of any human answer — they must not silently gain human
        # provenance just because a resolution happened.
        assert not fields & {"bolt_readings", "weld_readings",
                             "connection_type", "confidence"}

    def test_source_identity_unchanged_through_resolution(self, tmp_path):
        before = _item(_fresh(), "RP-0009").evidence
        after = _item(_resolve_full_rp0009(_fresh(), tmp_path), "RP-0009").evidence
        assert (after.source_drawing_id, after.drawing_number,
                after.source_page, after.detail_reference) == (
            before.source_drawing_id, before.drawing_number,
            before.source_page, before.detail_reference)
        assert after.source_page == 5
        assert after.detail_reference == "VIEW B-B"
        assert after.source_drawing_id == "SELBY-C1136"

    def test_artifact_generated_and_named_by_identity(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        files = tuple(_state(advanced, "RP-0009").generated_files)
        assert len(files) == 1
        pdf = Path(files[0])
        assert pdf.name == RP0009_PDF_NAME
        assert pdf.read_bytes().startswith(b"%PDF")
        assert pdf.stat().st_size == RP0009_PDF_SIZE


# =============================================================================
# Case B — one required task left unresolved: blocked, visible, no pretence.
# =============================================================================

class TestRealCandidateCaseB:
    """RP-0001 with every answer EXCEPT the plate: the rerun must block, the
    open task must stay visible, and no artifact may exist."""

    @pytest.fixture
    def incomplete(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0001")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0001",
            resolutions=_resolutions_except(group, {TASK_PROVIDE_PLATE},
                                            "HUMAN-SUPPLIED TEST DATA (7B3 Case B: "
                                            "the reviewer deliberately left the plate answer empty)"),
            output_dir=tmp_path)
        return workflow, advanced, tmp_path

    def test_candidate_stays_review_blocked(self, incomplete):
        _, advanced, _ = incomplete
        state = _state(advanced, "RP-0001")
        assert (state.decision, state.output_status, state.verification_status) == (
            "REVIEW", "BLOCKED_REVIEW", "NO_ARTIFACT")

    def test_unresolved_task_stays_visible(self, incomplete):
        _, advanced, _ = incomplete
        tasks = _item(advanced, "RP-0001").tasks
        plate = next(t for t in tasks if t.task_type == TASK_PROVIDE_PLATE)
        assert plate.resolved is False
        assert all(t.resolved for t in tasks if t.task_type != TASK_PROVIDE_PLATE)

    def test_blockers_name_the_missing_field(self, incomplete):
        _, advanced, _ = incomplete
        codes = set(_state(advanced, "RP-0001").blockers)
        assert "AUTOMATION_BLOCKER_PLATE" in codes

    def test_no_artifact_of_any_kind(self, incomplete):
        _, advanced, tmp = incomplete
        assert _state(advanced, "RP-0001").generated_files == ()
        assert not list(tmp.iterdir())

    def test_provenance_omits_the_unanswered_field(self, incomplete):
        _, advanced, _ = incomplete
        fields = {p.field for p in _item(advanced, "RP-0001").provenance}
        assert "plate" not in fields
        # The answered fields ARE recorded — the system shows what the human
        # did do, not a pretence that the plate was supplied.
        assert "attachments" in fields and "location" in fields


# =============================================================================
# Case C — invalid human answers: the boundary refuses honestly, consumes nothing.
# =============================================================================

class TestRealCandidateCaseC:
    """RP-0002 with answers the boundary must refuse. A refusal means the
    workflow did not advance: revision stays 0, tasks stay open, nothing is
    written, and the candidate remains resolvable afterwards."""

    def test_wrong_answer_type_refused(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0002")
        plate = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_PLATE)
        with pytest.raises(ValueError, match="requires answer_type"):
            resolve_project_connection(workflow, package_id="RP-0002", output_dir=tmp_path,
                resolutions=[HumanResolution(plate.task_id, plate.task_type,
                                             ANSWER_MEMBER_SELECTION, ("005",),
                                             evidence="BAD")])

    def test_malformed_payload_refused(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0002")
        holes = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        with pytest.raises(ValueError, match="requires a mapping"):
            resolve_project_connection(workflow, package_id="RP-0002", output_dir=tmp_path,
                resolutions=[HumanResolution(holes.task_id, holes.task_type,
                                             ANSWER_HOLES_VALUE, "22",
                                             evidence="BAD")])

    def test_foreign_task_id_refused(self, tmp_path):
        workflow = _fresh()
        with pytest.raises(CrossProjectResolutionError, match="do not address a task"):
            resolve_project_connection(workflow, package_id="RP-0002", output_dir=tmp_path,
                resolutions=[HumanResolution(
                    "RP-0009-T05", "PROVIDE_PLATE", ANSWER_PLATE_VALUE,
                    {"type": "end_plate", "thickness_mm": 10.0,
                     "width_mm": 160.0, "depth_mm": 298.0}, evidence="BAD")])

    def test_refusals_change_nothing(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0002")
        plate = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_PLATE)
        for bad in (
            [HumanResolution(plate.task_id, plate.task_type,
                             ANSWER_MEMBER_SELECTION, ("005",), evidence="BAD")],
            [HumanResolution("RP-0009-T05", "PROVIDE_PLATE", ANSWER_PLATE_VALUE,
                             {"type": "end_plate"}, evidence="BAD")],
        ):
            with pytest.raises((ValueError, CrossProjectResolutionError)):
                resolve_project_connection(workflow, package_id="RP-0002",
                                           resolutions=bad, output_dir=tmp_path)
        assert workflow.revision == 0
        assert all(t.status == "OPEN" for t in group.tasks)
        assert not list(tmp_path.iterdir())

    def test_candidate_still_resolvable_after_refusals(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0002")
        plate = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_PLATE)
        with pytest.raises(ValueError):
            resolve_project_connection(workflow, package_id="RP-0002", output_dir=tmp_path,
                resolutions=[HumanResolution(plate.task_id, plate.task_type,
                                             ANSWER_MEMBER_SELECTION, ("005",),
                                             evidence="BAD")])
        # The one-shot was NOT consumed by the refusal: a genuine resolution
        # is accepted afterwards and advances the workflow normally.
        advanced = resolve_project_connection(
            workflow, package_id="RP-0002",
            resolutions=_resolutions_except(group, set(),
                "HUMAN-SUPPLIED TEST DATA (7B3 Case C: valid answers after refusals)"),
            output_dir=tmp_path)
        assert advanced.revision == 1
        # RP-0002's members are not in the Selby member context, so it must
        # stay REVIEW — the answers are accepted, the gates stay honest.
        assert _state(advanced, "RP-0002").decision == "REVIEW"


# =============================================================================
# Case D — the six genuinely missing-material candidates.
# =============================================================================

class TestRealCandidateCaseD:
    """Material is never invented, never defaulted. The default workflow has
    no material task (missing material is deliberately NOT a 7Z blocker), and
    the existing request_material_specification=True flag is the architecture's
    own route for a human-supplied designation."""

    def test_default_workflow_offers_no_material_task(self):
        workflow = _fresh()
        for package_id in MISSING_MATERIAL_IDS:
            task_types = {t.task_type for t in _group(workflow, package_id).tasks}
            assert "PROVIDE_MATERIAL_SPECIFICATION" not in task_types
            assert _item(workflow, package_id).ai_material is None

    def test_material_resolution_refused_without_a_task(self, tmp_path):
        workflow = _fresh()
        with pytest.raises(CrossProjectResolutionError, match="do not address a task"):
            resolve_project_connection(workflow, package_id="RP-0033", output_dir=tmp_path,
                resolutions=[HumanResolution("RP-0033-T99",
                                             "PROVIDE_MATERIAL_SPECIFICATION",
                                             ANSWER_MATERIAL_VALUE, "300PLUS",
                                             evidence="BAD")])

    def test_flag_workflow_appends_material_task_to_exactly_the_six(self):
        workflow = _material_workflow()
        flagged = [
            g.review_package_id for g in workflow.exception_package.connection_tasks
            if any(t.task_type == "PROVIDE_MATERIAL_SPECIFICATION" for t in g.tasks)
        ]
        assert flagged == list(MISSING_MATERIAL_IDS)
        # A candidate whose AI already read "300" gets no material task.
        assert "PROVIDE_MATERIAL_SPECIFICATION" not in {
            t.task_type for t in _group(workflow, "RP-0009").tasks}

    def test_human_material_supply_recorded_verbatim(self, tmp_path):
        workflow = _material_workflow()
        group = _group(workflow, "RP-0033")
        material_task = next(t for t in group.tasks
                             if t.task_type == "PROVIDE_MATERIAL_SPECIFICATION")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0033", output_dir=tmp_path,
            resolutions=[HumanResolution(
                material_task.task_id, material_task.task_type,
                ANSWER_MATERIAL_VALUE, "300PLUS",
                evidence="HUMAN-SUPPLIED TEST DATA (7B3 Case D: the reviewer read "
                         "the PL05 material callout '300PLUS' off the drawing)")])
        record = _record(advanced, "RP-0033")
        assert record.rerun_outcome.rebuilt_package.supplement.material == "300PLUS"
        (trace,) = record.rerun_outcome.resolutions_applied
        assert trace.answer == "300PLUS"
        item = _item(advanced, "RP-0033")
        assert {p.field: p.provenance for p in item.provenance}["material"] == \
            "HUMAN_SUPPLEMENTED"
        # The AI never read a material — ai_material stays None; the human
        # value is recorded as a supplement, not rewritten into the AI field.
        assert item.ai_material is None
        # RP-0033's other tasks are still open — no pretence of completion.
        assert _state(advanced, "RP-0033").decision == "REVIEW"
        assert _state(advanced, "RP-0033").generated_files == ()

    def test_material_never_auto_filled(self, tmp_path):
        workflow = _material_workflow()
        group = _group(workflow, "RP-0034")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0034", output_dir=tmp_path,
            resolutions=_resolutions_except(
                group, {"PROVIDE_MATERIAL_SPECIFICATION"},
                "HUMAN-SUPPLIED TEST DATA (7B3 Case D: everything answered "
                "EXCEPT material)"),
        )
        record = _record(advanced, "RP-0034")
        assert record.rerun_outcome.rebuilt_package.supplement.material is None
        assert _item(advanced, "RP-0034").ai_material is None
        fields = {p.field for p in _item(advanced, "RP-0034").provenance}
        assert "material" not in fields

    def test_material_stays_missing_until_human_supply(self):
        # The six, on the DEFAULT workflow, at revision 0: material absent in
        # the contract, and the workload map groups them under missing.
        workflow = _fresh()
        workload = build_exception_workload_map(build_project_review_contract(workflow))
        missing = next(g for g in workload.material_groups if g.material is None)
        assert missing.candidate_ids == MISSING_MATERIAL_IDS
        assert sum(g.count for g in workload.material_groups if g.material == "300") == 46


# =============================================================================
# End-to-end production proof: RP-0009 through acceptance, truthfully.
# =============================================================================

class TestEndToEndProductionProof:
    """One real candidate through human resolution -> 7AD -> 7Z -> 7AE -> 7AF
    -> 7AG -> production acceptance. The project-level verdict is NOT_ACCEPTED
    because 51 candidates remain unresolved — reported as-is."""

    @pytest.fixture
    def resolved_and_accepted(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        return advanced, accept_production_job(advanced)

    def test_per_connection_acceptance_passes_every_check(self, resolved_and_accepted):
        _, acceptance = resolved_and_accepted
        row = next(c for c in acceptance.connections if c.package_id == "RP-0009")
        assert row.accepted is True
        assert row.decision == "AUTO"
        assert row.output_status == "GENERATED"
        assert row.verification_status == "VERIFIED"
        assert row.artifact_exists is True
        assert row.page_count == 1
        assert all(check.status == "PASSED" for check in row.checks)
        assert not row.failures
        assert row.verified_artifact.endswith(RP0009_PDF_NAME)

    def test_project_acceptance_honestly_not_accepted(self, resolved_and_accepted):
        _, acceptance = resolved_and_accepted
        assert acceptance.status == "NOT_ACCEPTED"
        assert "never processed through the exception-resolution rerun" in acceptance.reason
        assert acceptance.accepted_package_ids == ("RP-0009",)
        assert len(acceptance.unresolved_package_ids) == 51
        assert "RP-0009" not in acceptance.unresolved_package_ids
        assert len(acceptance.verified_artifacts) == 1

    def test_acceptance_trace_carries_every_human_answer_verbatim(self, resolved_and_accepted):
        _, acceptance = resolved_and_accepted
        row = next(c for c in acceptance.connections if c.package_id == "RP-0009")
        decisions = row.trace.human_decisions
        assert len(decisions) == 10
        assert all(d.applied and d.refusal_reason is None for d in decisions)
        by_type = {d.task_type: d for d in decisions}
        expected = copy.deepcopy(JOURNEY_ANSWERS_BY_TASK_TYPE)
        assert by_type["COMPLETE_REVIEW"].answer is None
        assert by_type["SELECT_POSITION"].answer == expected["SELECT_POSITION"]
        assert by_type["SELECT_ATTACHMENT"].answer == tuple(expected["SELECT_ATTACHMENT"])
        assert by_type["CONFIRM_AI_VALUES"].answer == expected["CONFIRM_AI_VALUES"]
        assert by_type["PROVIDE_PLATE"].answer == expected["PROVIDE_PLATE"]
        assert by_type["PROVIDE_HOLE_DIAMETER"].answer == expected["PROVIDE_HOLE_DIAMETER"]
        assert by_type["PROVIDE_LOCATION"].answer == expected["PROVIDE_LOCATION"]
        assert by_type["REVIEW_SPECIFICATION"].answer is None
        assert by_type["REVIEW_VALIDATION"].answer is None
        assert by_type["PROVIDE_CONNECTION_IDENTITY"].answer == JOURNEY_CONNECTION_ID
        assert all(d.evidence == EVIDENCE_7B3 for d in decisions)

    def test_acceptance_refused_at_revision_zero(self):
        acceptance = accept_production_job(_fresh())
        assert acceptance.status == "NOT_ACCEPTED"
        assert acceptance.accepted_package_ids == ()


# =============================================================================
# Phase 3/4 — the EXISTING review UI is sufficient; a human drives it end to end.
# =============================================================================

class TestReviewUIVerticalSlice:
    """No UI change was required by 7B3: identity, provenance, verbatim AI
    values, per-task inputs, staleness protection and honest output state were
    all already rendered by 7AN/7AO/7AP. These tests drive that UI with the
    real 52-candidate workload — including resolving RP-0009 THROUGH the UI."""

    def _bind(self, workflow, out_dir):
        ui_session.bind_workflow(workflow, out_dir)
        return workflow

    def test_dashboard_shows_the_real_workload(self, tmp_path):
        workflow = self._bind(_fresh(), tmp_path)
        page = render_module.render_project_page(
            render_project_view(build_project_review_contract(workflow)))
        assert "52 connections need review; 0 connections verified; 0 connections remain" in page
        assert "Revision 0" in page
        assert "PROJ-7AV-SELBY" in page
        # The project page offers only the workflow's own actions.
        assert "/refresh" in page
        for forbidden in ("<button>Generate", "name=\"verify\"", "force approve"):
            assert forbidden not in page

    def test_detail_page_shows_identity_provenance_tasks_and_ai_values(self, tmp_path):
        workflow = self._bind(_fresh(), tmp_path)
        view = render_project_view(build_project_review_contract(workflow))
        connection = next(c for c in view.review_items
                          if c.identity.package_id == "RP-0009")
        html = render_module.render_connection_detail_page(view, connection)
        body = _page_body(html)
        assert "RP-0009" in body
        assert "SELBY-C1136" in body
        assert "VIEW B-B" in body
        # Every task the human must answer appears with its question.
        for question in (
            "Where on the member is this connection (position)?",
            "Which attachment surface applies to each connected member?",
            "Is this plate actually present?",
            "What is the specified hole diameter?",
            "Where is this connection located in the project space?",
        ):
            assert question in body
        # The AI's own values, verbatim — never rewritten.
        assert ("Current AI value: (('connected_member_marks', "
                "['005', 'PL018', 'PL032', 'PL025']), ('material', '300'))") in body
        # Unverified at revision 0: no verified banner, ever.
        assert "&#10003; Connection verified" not in body

    def test_human_resolves_rp0009_through_the_ui(self, tmp_path):
        out_dir = tmp_path / "ui"
        out_dir.mkdir()
        workflow = self._bind(_fresh(), out_dir)
        before = ui_session.current_workflow()
        page = ui_session.submit_resolution("RP-0009", _form_for(workflow, "RP-0009"))
        after = ui_session.current_workflow()
        assert isinstance(after, ProjectWorkflowState)
        assert after is not before
        assert after.revision == 1
        state = _state(after, "RP-0009")
        assert (state.decision, state.output_status, state.verification_status) == (
            "AUTO", "GENERATED", "VERIFIED")
        # The project page the UI returned reflects exactly that.
        assert "51 connections need review; 1 connection verified; 0 connections remain" in page
        # The refreshed detail page now carries the verified banner — and only
        # now, because 7AG really verified.
        detail = ui_session.render_connection_page("RP-0009")
        assert "&#10003; Connection verified" in _page_body(detail)
        # A real artifact exists, named by the workflow's own identity.
        pdfs = sorted(out_dir.glob("*-fabrication.pdf"))
        assert [p.name for p in pdfs] == [RP0009_PDF_NAME]
        # Every other connection is untouched except the shared revision counter.
        assert after.connections[0] is before.connections[0]
        assert after.connections[2] is before.connections[2]

    def test_stale_ui_submission_refused(self, tmp_path):
        out_dir = tmp_path / "ui"
        out_dir.mkdir()
        workflow = self._bind(_fresh(), out_dir)
        ui_session.submit_resolution("RP-0009", _form_for(workflow, "RP-0009"))
        after_first = ui_session.current_workflow()
        # The page the reviewer had open still says revision 0.
        stale_form = _form_for(workflow, "RP-0002", revision="0")
        page = ui_session.submit_resolution("RP-0002", stale_form)
        assert "This review is out of date" in page
        after_stale = ui_session.current_workflow()
        assert after_stale is after_first
        assert after_stale.revision == 1
        assert _state(after_stale, "RP-0002").decision == "REVIEW"
        assert all(t.status == "OPEN" for t in _group(after_stale, "RP-0002").tasks)

    def test_failure_state_rendered_as_failure(self, tmp_path):
        # An incomplete resolution (the reviewer skipped the plate) must never
        # reach the workflow: the UI's own boundary refuses the empty required
        # field honestly, nothing resolves, no file appears.
        out_dir = tmp_path / "ui"
        out_dir.mkdir()
        workflow = self._bind(_fresh(), out_dir)
        form = _form_for(workflow, "RP-0001")
        plate_contract = build_connection_review_contract(workflow, "RP-0001")
        plate_task = next(t for t in plate_contract.tasks
                          if t.task_type == TASK_PROVIDE_PLATE)
        for name in render_module.task_input_names(plate_task):
            form.pop(name, None)
        page = ui_session.submit_resolution("RP-0001", form)
        assert "Resolution not submitted" in page
        assert "at least one field value is required" in page
        # The workflow is untouched: revision 0, every task open, no files.
        after = ui_session.current_workflow()
        assert after is workflow
        assert after.revision == 0
        assert all(t.status == "OPEN" for t in _group(after, "RP-0001").tasks)
        assert not list(out_dir.iterdir())
        # And the detail page still shows the open plate task — the reviewer
        # can see exactly what was missing.
        detail = ui_session.render_connection_page("RP-0001")
        body = _page_body(detail)
        assert "&#10003; Connection verified" not in body
        assert "Is this plate actually present?" in body

    def test_missing_material_never_shown_as_a_value(self, tmp_path):
        workflow = self._bind(_fresh(), tmp_path)
        view = render_project_view(build_project_review_contract(workflow))
        connection = next(c for c in view.review_items
                          if c.identity.package_id == "RP-0033")
        html = render_module.render_connection_detail_page(view, connection)
        body = _page_body(html)
        # RP-0033's AI section must not carry a material value the AI never
        # read; the CONFIRM question lists only what the AI actually saw.
        assert "('material', '300')" not in body
        assert "'connected_member_marks', 'plate'" in body


# =============================================================================
# The 15 mandated negative proofs.
# =============================================================================

class TestNegativeProofs7B3:
    """Each negative uses the existing boundary as-is; none weakens a
    validation. All use real candidates only."""

    # N1 — a required task left unresolved keeps the candidate blocked.
    def test_n1_required_task_unresolved_blocks(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0005")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0005", output_dir=tmp_path,
            resolutions=_resolutions_except(group, {TASK_PROVIDE_HOLE_DIAMETER}))
        state = _state(advanced, "RP-0005")
        assert (state.decision, state.output_status, state.verification_status) == (
            "REVIEW", "BLOCKED_REVIEW", "NO_ARTIFACT")
        holes = next(t for t in _item(advanced, "RP-0005").tasks
                     if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        assert holes.resolved is False

    # N2 — an invalid human answer is refused by the resolution boundary.
    def test_n2_invalid_answer_refused(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0006")
        holes = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_HOLE_DIAMETER)
        with pytest.raises(ValueError, match="requires a mapping"):
            resolve_project_connection(workflow, package_id="RP-0006", output_dir=tmp_path,
                resolutions=[HumanResolution(holes.task_id, holes.task_type,
                                             ANSWER_HOLES_VALUE, "M20 holes",
                                             evidence="BAD")])

    # N3 — a wrong answer type is refused before anything runs.
    def test_n3_wrong_type_refused(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0007")
        location = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_LOCATION)
        with pytest.raises(ValueError, match="requires answer_type"):
            resolve_project_connection(workflow, package_id="RP-0007", output_dir=tmp_path,
                resolutions=[HumanResolution(location.task_id, location.task_type,
                                             ANSWER_POSITION_VALUE, "END",
                                             evidence="BAD")])
        assert workflow.revision == 0

    # N4 — no engineering value can appear without explicit human input.
    def test_n4_no_fabricated_value_without_human_input(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0003")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0003", output_dir=tmp_path,
            resolutions=[
                HumanResolution(task.task_id, task.task_type, task.answer_type,
                                copy.deepcopy(JOURNEY_ANSWERS_BY_TASK_TYPE[task.task_type]),
                                evidence=EVIDENCE_7B3)
                for task in group.tasks
                if task.task_type in (TASK_COMPLETE_REVIEW, TASK_SELECT_ATTACHMENT)
            ])
        item = _item(advanced, "RP-0003")
        supplied = {p.field for p in item.provenance
                    if p.provenance == "HUMAN_SUPPLEMENTED"}
        assert supplied == {"attachments"}
        # The unanswered engineering fields carry no human provenance at all.
        assert not {p.field for p in item.provenance} & {
            "plate", "holes", "location", "position", "connection_id"}

    # N5 — material is never auto-filled (see also Case D).
    def test_n5_material_never_auto_filled(self, tmp_path):
        workflow = _material_workflow()
        group = _group(workflow, "RP-0038")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0038", output_dir=tmp_path,
            resolutions=_resolutions_except(group, {"PROVIDE_MATERIAL_SPECIFICATION"}))
        record = _record(advanced, "RP-0038")
        assert record.rerun_outcome.rebuilt_package.supplement.material is None
        assert _item(advanced, "RP-0038").ai_material is None

    # N6 — AI_EXTRACTED is never silently converted to HUMAN_* provenance.
    def test_n6_ai_extracted_not_silently_converted(self, tmp_path):
        before = _item(_fresh(), "RP-0009")
        after = _item(_resolve_full_rp0009(_fresh(), tmp_path), "RP-0009")
        before_fields = {p.field for p in before.provenance}
        after_human = {p.field for p in after.provenance
                       if p.provenance in ("HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED")}
        # Only fields named by a human answer may gain a human label.
        answered = {"connected_member_marks", "material", "position",
                    "plate", "holes", "location", "attachments"}
        assert after_human <= answered
        # AI readings the human never touched keep their AI provenance...
        untouched = before_fields - after_human
        assert untouched <= {
            "connected_member_marks", "material", "weld_readings",
            "bolt_readings", "connection_type", "confidence"}

    # N7 — provenance is never fabricated: only the existing vocabulary.
    def test_n7_provenance_vocabulary_only(self, tmp_path):
        for workflow in (_fresh(), _resolve_full_rp0009(_fresh(), tmp_path)):
            for item in build_project_review_contract(workflow).items:
                for entry in item.provenance:
                    assert entry.provenance in (
                        "AI_EXTRACTED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED")

    # N8 — the source page cannot change through resolution.
    def test_n8_source_page_cannot_change(self, tmp_path):
        before = _item(_fresh(), "RP-0008").evidence
        workflow = _fresh()
        group = _group(workflow, "RP-0008")
        plate = next(t for t in group.tasks if t.task_type == TASK_PROVIDE_PLATE)
        advanced = resolve_project_connection(
            workflow, package_id="RP-0008", output_dir=tmp_path,
            resolutions=[HumanResolution(
                plate.task_id, plate.task_type, ANSWER_PLATE_VALUE,
                # A hostile payload carrying provenance-looking extras: shape
                # validation accepts the plate dict; the extra keys are simply
                # not part of the vocabulary and cannot change the evidence.
                {"type": "end_plate", "thickness_mm": 10.0, "width_mm": 160.0,
                 "depth_mm": 298.0, "source_page": 99, "source_drawing_id": "FORGED"},
                evidence=EVIDENCE_7B3)])
        after = _item(advanced, "RP-0008").evidence
        assert after.source_page == before.source_page == 5
        assert {p.field for p in _item(advanced, "RP-0008").provenance} & \
            {"source_page", "source_drawing_id"} == set()

    # N9 — the source drawing cannot change through resolution.
    def test_n9_source_drawing_cannot_change(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert _item(advanced, "RP-0009").evidence.source_drawing_id == "SELBY-C1136"
        assert _item(advanced, "RP-0009").evidence.drawing_number == "001"

    # N10 — candidate identity cannot change through resolution.
    def test_n10_candidate_id_cannot_change(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        ids = tuple(item.package_id
                    for item in build_project_review_contract(advanced).items)
        assert ids == EXPECTED_IDS
        assert tuple(c.package_id for c in advanced.connections) == EXPECTED_IDS
        assert "RP-0009" in ids

    # N11 — a stale revision is never silently accepted.
    def test_n11_stale_revision_refused(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert advanced.revision == 1
        group = _group(advanced, "RP-0010")
        task = group.tasks[0]
        with pytest.raises(StaleProjectWorkflowError, match="stale"):
            resolve_project_connection(
                advanced, package_id="RP-0010", output_dir=tmp_path,
                resolutions=[HumanResolution(task.task_id, task.task_type,
                                             task.answer_type, None,
                                             evidence=EVIDENCE_7B3)],
                expected_revision=0)

    # N12 — an unresolved candidate cannot generate an artifact.
    def test_n12_unresolved_candidate_generates_nothing(self):
        workflow = _fresh()
        assert workflow.generated_files == ()
        assert all(c.generated_files == () for c in workflow.connections)
        assert all(_item(workflow, c.package_id).output_status is None
                   for c in workflow.connections)
        assert all(_item(workflow, c.package_id).verification_status is None
                   for c in workflow.connections)

    # N13 — a blocked candidate cannot bypass 7Z; the API carries no override.
    def test_n13_blocked_candidate_cannot_bypass_gate(self, tmp_path):
        workflow = _fresh()
        group = _group(workflow, "RP-0011")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0011", output_dir=tmp_path,
            resolutions=_resolutions_except(group, {TASK_PROVIDE_LOCATION}))
        state = _state(advanced, "RP-0011")
        assert state.decision == "REVIEW"
        assert "AUTOMATION_BLOCKER_LOCATION" in state.blockers
        assert state.generated_files == ()
        # The resolution input has no decision/override field of any kind.
        assert {f.name for f in dataclasses.fields(HumanResolution)} == {
            "task_id", "task_type", "answer_type", "answer", "evidence"}

    # N14 — a resolution can never bypass the 7AD rerun.
    def test_n14_resolution_always_goes_through_the_rerun(self, tmp_path):
        workflow = _fresh()
        assert all(r.rerun_outcome is None for r in workflow.connection_records)
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        record = _record(advanced, "RP-0009")
        assert record.rerun_outcome.decision == "AUTO"
        assert _state(advanced, "RP-0009").decision == record.rerun_outcome.decision
        # The 51 others have no rerun record — only the resolved candidate was
        # processed, exactly once, through 7AD.
        processed = [r.rerun_outcome.review_package_id for r in
                     advanced.connection_records if r.rerun_outcome is not None]
        assert processed == ["RP-0009"]

    # N15 — an invalid candidate cannot reach production acceptance.
    def test_n15_invalid_candidate_cannot_reach_acceptance(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        acceptance = accept_production_job(advanced)
        assert acceptance.status == "NOT_ACCEPTED"
        assert len(acceptance.unresolved_package_ids) == 51
        for package_id in ("RP-0001", "RP-0033"):
            row = next(c for c in acceptance.connections if c.package_id == package_id)
            assert row.accepted is False


# =============================================================================
# The real 52-candidate regression.
# =============================================================================

class TestReal52CandidateRegression:
    """The queue stays exactly as 7B1/7B2 pinned it: 52 candidates, identity,
    provenance, material distribution and page accounting intact; nothing is
    generated; the canonical capture files are byte-identical."""

    def test_52_candidates_intact_at_revision_zero(self):
        workflow = _fresh()
        contract = build_project_review_contract(workflow)
        assert [i.package_id for i in contract.items] == list(EXPECTED_IDS)
        assert len(contract.items) == 52
        assert all(i.decision == "REVIEW" for i in contract.items)
        assert all(i.requires_action for i in contract.items)
        assert all(i.output_status is None for i in contract.items)
        assert all(i.verification_status is None for i in contract.items)
        assert all(i.revision == 0 for i in contract.items)
        assert workflow.generated_files == ()

    def test_real_distribution_unchanged(self):
        workflow = _fresh()
        workload = build_exception_workload_map(build_project_review_contract(workflow))
        blocker_counts = {g.blocker_code: g.count for g in workload.blocker_code_groups}
        assert blocker_counts == REAL_BLOCKER_COUNTS
        task_counts = {g.task_type: g.count for g in workload.task_type_groups}
        assert task_counts == REAL_TASK_COUNTS
        page_counts = {g.source_page: g.count for g in workload.page_groups}
        assert page_counts == REAL_PAGE_COUNTS
        assert all(g.task_type == CODE_TO_TASK_TYPE[g.blocker_code]
                   for g in workload.blocker_code_groups)

    def test_rp0009_canonical_state_untouched(self):
        item = _item(_fresh(), "RP-0009")
        assert item.connection_id is None
        assert item.ai_material == "300"
        assert item.ai_member_references == ("005", "PL018", "PL032", "PL025")
        assert item.evidence.source_page == 5
        assert item.evidence.detail_reference == "VIEW B-B"
        assert all(not t.resolved for t in item.tasks)

    def test_capture_files_are_the_pinned_evidence(self):
        from tests.test_real_world_review_intake_provenance import CAPTURE_FILES
        for path in CAPTURE_FILES:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == PINNED_SHA256[path.name], path.name

    def test_page_26_carries_exactly_its_one_recovered_candidate(self):
        workload = build_exception_workload_map(
            build_project_review_contract(_fresh()))
        pages = {g.source_page: g.count for g in workload.page_groups}
        # The genuinely recovered page-26 extraction contributes exactly its
        # one real candidate — nothing else changes anywhere.
        assert pages[26] == 1
        assert sum(g.count for g in workload.page_groups) == 52

    def test_only_explicitly_resolved_candidates_change(self, tmp_path):
        before = build_project_review_contract(_fresh())
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        after = build_project_review_contract(advanced)
        for before_item, after_item in zip(before.items, after.items):
            if before_item.package_id == "RP-0009":
                assert after_item.decision == "AUTO"
                continue
            # Every other candidate: substance identical; only the real
            # project-wide revision counter (7B2 test M) advanced.
            expected = {**dataclasses.asdict(before_item), "revision": 1}
            assert dataclasses.asdict(after_item) == expected

    def test_no_fabrication_outputs_anywhere(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        # The only generated file anywhere is the genuine RP-0009 artifact,
        # inside the output directory this test created.
        generated = [f for f in advanced.generated_files]
        assert generated == [str(p) for p in tmp_path.glob("*")]
        assert len(generated) == 1
        # The repository's own source and data trees carry no PDFs.
        for directory in (Path("app"), Path("tests")):
            assert not [p for p in directory.rglob("*.pdf")], directory


# =============================================================================
# Determinism and purity.
# =============================================================================

class TestDeterminismAndPurity:
    """The same real workload always projects the same truth, and a resolved
    state is reproducible from the same answers."""

    def _fingerprint(self, workflow):
        contract = build_project_review_contract(workflow)
        rows = []
        for item in contract.items:
            rows.append((
                item.package_id, item.revision, item.decision, item.output_status,
                item.verification_status, item.connection_id,
                tuple((p.field, p.provenance) for p in item.provenance),
                tuple((t.task_type, t.resolved) for t in item.tasks),
            ))
        return rows

    def test_revision_zero_is_deterministic(self):
        fingerprints = {hashlib.sha256(repr(self._fingerprint(_fresh())).encode()).hexdigest()
                        for _ in range(3)}
        assert len(fingerprints) == 1

    def test_full_resolution_is_reproducible(self, tmp_path):
        def run():
            advanced = _resolve_full_rp0009(_fresh(), tmp_path)
            item = _item(advanced, "RP-0009")
            return (item.decision, item.output_status, item.verification_status,
                    item.connection_id,
                    tuple((p.field, p.provenance) for p in item.provenance))
        first = run()
        second = run()
        assert first == second

    def test_no_writes_outside_the_output_directory(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert set(tmp_path.iterdir()) == {
            p for p in tmp_path.iterdir() if p.name == RP0009_PDF_NAME}
        assert all(str(f).startswith(str(tmp_path))
                   for f in advanced.generated_files)
