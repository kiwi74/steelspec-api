"""MILESTONE 7B4 — Real-World Reviewer Evidence Sufficiency Proof.

The question: when a human reviewer is resolving one of the 52 real Selby
Square connection exceptions, does SteelSpec present enough source evidence
and existing extracted information for the reviewer to make an explicit
engineering decision without reconstructing the relevant information from the
entire original drawing set?

WHAT THIS FILE PROVES AND DOES NOT PROVE:
  PROVEN: what the reviewer RECEIVES for every one of the 52 real candidates
  — candidate identity, source drawing, source page, detail/grid reference
  where recorded, the AI's extracted values verbatim (including material),
  the unresolved tasks with their questions and current AI values, the
  provenance labels, the blocker/task links, and the available actions. It
  also proves, against the genuine 32-page capture evidence, that every
  source reference SteelSpec names is REAL (every page exists, every page
  has a capture image, every recorded detail and grid reference genuinely
  occurs on its recorded page). And it proves the reviewer can always
  distinguish "missing" from "AI observed" for every field.
  NOT PROVEN HERE: whether the AI decided the correct engineering answer.
  That answer must remain NO — this file never supplies, suggests or
  evaluates an engineering value. Where the evidence needed for a decision
  lives only in the original drawing (position, attachment surface,
  location, and most plate/hole values), that is classified
  SOURCE_REFERENCE_ONLY — the reviewer must consult the drawing, and
  SteelSpec's job is to give them the correct drawing, page, detail and
  everything it legitimately knows. Manual source inspection is legitimate
  human engineering review, not a UI failure.

HARD RULES (unchanged from the milestone brief):
  - Read-only evidence audit over the genuine chain; the ONLY product
    change in this milestone is the smallest additive one the brief
    authorises: the 7AK contract's ai_material (an existing AI observation)
    was already consumed by 7B2's workload map but was omitted from the 7AM
    ExtractedValuesView and therefore from the UI's "AI-extracted values"
    card. It is now passed through verbatim (view field `material`, one
    render row) — never transformed, never invented, missing stays missing.
  - No new engineering logic, no suggested answers, no prioritisation, no
    risk/score/ranking anywhere.
  - Provenance vocabulary unchanged (AI_EXTRACTED / HUMAN_REVIEWED /
    HUMAN_SUPPLEMENTED); an AI observation is never labelled human-owned
    unless a human confirms or supplies it.
  - The 52-candidate regression: exactly 52, RP-0001..RP-0052 exactly once,
    SELBY-C1136 preserved, page-26 exactly one recovered candidate,
    46 x "300" + 6 missing material, 7B2 distributions unchanged, 7B3
    behaviour unchanged.
  - Canonical capture fixtures are never modified.

BRIEF ITEM MAP:
  Phase 2 evidence matrix .......... TestEvidenceContractMatrix
  Phase 4 source-page check ........ TestSourcePageLocatability
  Phase 5 task classification ...... TestTaskLevelClassification
  Phase 6/7 material exposure ...... TestMaterialExposure
  mandated real examples ........... TestRealCandidateExamples
  the 10 mandated negatives ........ TestNegativeProofs7B4
  52-candidate regression .......... TestReal52CandidateRegression
  determinism / purity ............. TestDeterminismAndPurity
"""

import copy
import dataclasses
import hashlib
from pathlib import Path

import pytest

from app.cad_engine.exception_resolution import (
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
from app.cad_engine.exception_workload import build_exception_workload_map
from app.cad_engine.project_workflow import resolve_project_connection
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import (
    ExtractedValuesView,
    render_connection_view,
    render_project_view,
)
from app.review_ui import render as render_module
from tests.test_real_world_exception_workload import (
    REAL_BLOCKER_COUNTS,
    REAL_PAGE_COUNTS,
    REAL_TASK_COUNTS,
)
from tests.test_real_world_human_exception_review import (
    EVIDENCE_7B3,
    MISSING_MATERIAL_IDS,
    RP0009_RESOLVED_PROVENANCE,
    _fresh,
    _group,
    _item,
    _material_workflow,
    _page_body,
    _record,
    _resolve_full_rp0009,
    _resolutions_except,
    _state,
)
from tests.test_real_world_review_intake_provenance import (
    CAPTURE_FILES,
    PINNED_SHA256,
    _accumulated,
)

# ---------------------------------------------------------------------------
# Pinned real facts (recorded from the genuine workflow; asserted, never rebuilt)
# ---------------------------------------------------------------------------

# The five deterministic evidence-sufficiency categories (the milestone's own
# vocabulary — information availability only, never risk/priority/ranking):
SUFFICIENT = "SUFFICIENT"
SOURCE_REFERENCE_ONLY = "SOURCE_REFERENCE_ONLY"
AI_OBSERVATION_PRESENT = "AI_OBSERVATION_PRESENT"
MISSING_SOURCE_EVIDENCE = "MISSING_SOURCE_EVIDENCE"
NOT_APPLICABLE = "NOT_APPLICABLE"

# Sign-off, acknowledgement and identity tasks: by the 7AC contract's own
# wording they require no additional source evidence (identity is "assigned
# at persistence, or given by the reviewer", the ack tasks "grant nothing").
NO_SOURCE_EVIDENCE_TASK_TYPES = frozenset({
    TASK_COMPLETE_REVIEW,
    TASK_REVIEW_SPECIFICATION,
    TASK_REVIEW_VALIDATION,
    TASK_PROVIDE_CONNECTION_IDENTITY,
})

# Per-task-type classification counts over the real 52 candidates (518 tasks):
# AI_OBSERVATION_PRESENT 73, SOURCE_REFERENCE_ONLY 237, NOT_APPLICABLE 208,
# SUFFICIENT 0, MISSING_SOURCE_EVIDENCE 0.
REAL_TASK_CATEGORY_COUNTS = {
    TASK_COMPLETE_REVIEW: (0, 0, 0, 52),
    TASK_CONFIRM_AI_VALUES: (52, 0, 0, 0),
    TASK_PROVIDE_PLATE: (2, 48, 0, 0),
    TASK_PROVIDE_HOLE_DIAMETER: (19, 33, 0, 0),
    TASK_PROVIDE_LOCATION: (0, 52, 0, 0),
    TASK_SELECT_POSITION: (0, 52, 0, 0),
    TASK_SELECT_ATTACHMENT: (0, 52, 0, 0),
    TASK_REVIEW_SPECIFICATION: (0, 0, 0, 52),
    TASK_REVIEW_VALIDATION: (0, 0, 0, 52),
    TASK_PROVIDE_CONNECTION_IDENTITY: (0, 0, 0, 52),
}
# The tuple order per row: (AI_OBSERVATION_PRESENT, SOURCE_REFERENCE_ONLY,
# MISSING_SOURCE_EVIDENCE, NOT_APPLICABLE).

REAL_TOTAL_TASKS = 518
REAL_BLOCKER_LINKS = 466          # every blocker of every candidate links a task
REAL_DETAIL_COUNT = 28            # candidates with a recorded detail reference
REAL_GRID_COUNT = 9               # candidates with a recorded grid reference
REAL_PAGE_IMAGE_COUNT = 32        # one capture image per captured page
REAL_MATERIAL_COUNTS = {"300": 46, None: 6}
REAL_PROVENANCE_AT_REV0 = {"AI_EXTRACTED": 100}

MATERIAL_ROW_300 = "<dt>Material</dt><dd>300</dd>"
MATERIAL_ROW_NONE = "<dt>Material</dt><dd>(none)</dd>"


def _contract(workflow=None):
    return build_project_review_contract(_fresh() if workflow is None else workflow)


def _view(workflow, package_id):
    return render_connection_view(build_connection_review_contract(workflow, package_id))


def _detail_page(workflow, package_id):
    view = render_project_view(build_project_review_contract(workflow))
    connection = next(c for c in view.review_items + view.completed_items
                      if c.identity.package_id == package_id)
    return _page_body(render_module.render_connection_detail_page(view, connection))


def _classify(item, task):
    """The milestone's five categories, deterministically:
    - an AI observation the reviewer must confirm/supplement -> AI_OBSERVATION_PRESENT
    - a task whose own contract requires no additional source evidence -> NOT_APPLICABLE
    - no AI observation but a valid source page reference -> SOURCE_REFERENCE_ONLY
    - no AI observation and no source page to navigate by -> MISSING_SOURCE_EVIDENCE
    Nothing else can make a task SUFFICIENT here: no task carries an explicit
    engineering value a human did not put there, so SUFFICIENT stays empty."""
    if task.current_value is not None:
        return AI_OBSERVATION_PRESENT
    if task.task_type in NO_SOURCE_EVIDENCE_TASK_TYPES:
        return NOT_APPLICABLE
    if item.evidence.source_page is not None:
        return SOURCE_REFERENCE_ONLY
    return MISSING_SOURCE_EVIDENCE


def _capture_pages():
    return {p["page_number"]: p for p in _accumulated().pages}


def _capture_images():
    return {
        p.stem.replace("page-", "")
        for directory in Path("tests/data").glob("*_page_images")
        for p in directory.glob("*.png")
    }


# =============================================================================
# Phase 2 — the read-only 52-candidate evidence matrix
# =============================================================================

class TestEvidenceContractMatrix:
    """For every real candidate, the contract carries: identity, source
    drawing, page, the AI's extracted values verbatim, provenance and the
    unresolved tasks — nothing invented, nothing dropped."""

    def test_every_candidate_has_source_drawing_number_and_page(self):
        contract = _contract()
        assert len(contract.items) == 52
        for item in contract.items:
            assert item.evidence.source_drawing_id == "SELBY-C1136"
            assert item.evidence.source_page is not None
            assert item.evidence.drawing_number == "001"  # the intake's own set-level number

    def test_detail_and_grid_reference_coverage(self):
        items = _contract().items
        assert sum(1 for i in items if i.evidence.detail_reference) == REAL_DETAIL_COUNT
        assert sum(1 for i in items if i.evidence.grid_reference) == REAL_GRID_COUNT

    def test_ai_observation_coverage_counts(self):
        items = _contract().items
        assert sum(1 for i in items if i.ai_member_references) == 52
        assert sum(1 for i in items if i.ai_bolt_readings) == 19
        assert sum(1 for i in items if i.ai_plate_readings) == 4
        assert sum(1 for i in items if i.ai_weld_readings) == 52
        assert sum(1 for i in items if i.ai_connection_type is not None) == 52
        assert sum(1 for i in items if i.ai_confidence is not None) == 52
        assert sum(1 for i in items if i.ai_material is not None) == 46

    def test_material_values_are_verbatim(self):
        counts = {}
        for item in _contract().items:
            counts[item.ai_material] = counts.get(item.ai_material, 0) + 1
        assert counts == REAL_MATERIAL_COUNTS
        # The designation is the AI's own string, byte-for-byte — never a
        # converted, normalised or inferred grade.
        assert all(i.ai_material == "300" for i in _contract().items
                   if i.ai_material is not None)

    def test_provenance_is_ai_extracted_only_at_rev0(self):
        labels = {}
        for item in _contract().items:
            for entry in item.provenance:
                labels[entry.provenance] = labels.get(entry.provenance, 0) + 1
        assert labels == REAL_PROVENANCE_AT_REV0
        # Material provenance exists exactly where the AI read one.
        fields = {}
        for item in _contract().items:
            for entry in item.provenance:
                if entry.field == "material":
                    fields[item.package_id] = entry.provenance
        assert set(fields) == {i.package_id for i in _contract().items if i.ai_material}

    def test_every_blocker_links_to_its_task(self):
        contract = _contract()
        total = sum(len(i.blockers) for i in contract.items)
        linked = sum(1 for i in contract.items for b in i.blockers if b.task_type)
        assert total == REAL_BLOCKER_LINKS
        assert linked == REAL_BLOCKER_LINKS

    def test_exactly_518_tasks_in_total(self):
        assert sum(len(i.tasks) for i in _contract().items) == REAL_TOTAL_TASKS

    def test_every_candidate_requires_action_with_resolve_available(self):
        for item in _contract().items:
            assert item.requires_action
            assert item.available_actions == ("REVIEW", "RESOLVE")


# =============================================================================
# Phase 4 — the actual source-page check (existing capture evidence only)
# =============================================================================

class TestSourcePageLocatability:
    """Every source reference the reviewer receives is REAL: the page exists
    in the genuine captures, a page image exists for it, and every recorded
    detail and grid reference genuinely occurs on its recorded page."""

    def test_every_source_page_exists_in_the_capture(self):
        pages = _capture_pages()
        for item in _contract().items:
            page = pages[item.evidence.source_page]
            assert not page.get("parse_failed"), item.package_id

    def test_every_source_page_has_a_capture_image(self):
        images = _capture_images()
        assert len(images) == REAL_PAGE_IMAGE_COUNT
        for item in _contract().items:
            assert str(item.evidence.source_page) in images, item.package_id

    def test_every_recorded_detail_reference_is_genuine_in_its_page(self):
        pages = _capture_pages()
        checked = 0
        for item in _contract().items:
            detail = item.evidence.detail_reference
            if detail is None:
                continue
            recorded = [c.get("detail_reference") for c in
                        pages[item.evidence.source_page].get("raw_connections") or []]
            assert detail in recorded, (item.package_id, detail)
            checked += 1
        assert checked == REAL_DETAIL_COUNT

    def test_every_recorded_grid_reference_is_genuine_in_its_page(self):
        pages = _capture_pages()
        checked = 0
        for item in _contract().items:
            grid = item.evidence.grid_reference
            if grid is None:
                continue
            recorded = [c.get("grid_reference") for c in
                        pages[item.evidence.source_page].get("raw_connections") or []]
            assert grid in recorded, (item.package_id, grid)
            checked += 1
        assert checked == REAL_GRID_COUNT

    def test_page_26_carries_exactly_its_one_recovered_candidate(self):
        assert _capture_pages()[26]["parse_failed"] is False
        assert [i.evidence.source_page for i in _contract().items].count(26) == 1

    def test_evidence_text_composes_the_source_reference(self):
        assert _view(_fresh(), "RP-0009").evidence.evidence_text == \
            "source drawing SELBY-C1136; drawing number 001; page 5; detail VIEW B-B"
        # No detail recorded -> no detail invented.
        text = _view(_fresh(), "RP-0001").evidence.evidence_text
        assert "detail" not in text
        assert text == "source drawing SELBY-C1136; drawing number 001; page 1"

    def test_capture_sheet_title_and_revision_facts_are_recorded(self):
        """The captures carry per-page sheet numbers, drawing titles and
        revisions. The contract carries the intake's own set-level drawing
        number ('001', the intake's documented choice) plus the exact page —
        enough to find the sheet; this test pins the current truthful
        behaviour rather than re-plumbing the intake."""
        pages = _capture_pages()
        assert pages[2]["drawing_number"] == "002"
        assert pages[22]["drawing_number"] == "C1136 022X"
        assert pages[1]["drawing_title"] == "Urban Fabrication"
        assert pages[27]["drawing_title"] == "7 Selby Square, Ponsonby - Urban Fabrication"
        # Every parsed page is revision A — including page 26, whose genuine
        # recovery capture carries the real sheet facts, never invented.
        assert all(p["revision"] == "A" for p in pages.values())
        assert pages[26]["revision"] == "A"
        assert pages[26]["drawing_number"] == "026"
        assert pages[26]["drawing_title"] == "Urban Fabrication"
        for item in _contract().items:
            assert item.evidence.drawing_number == "001"


# =============================================================================
# Phase 5 — task-by-task evidence classification (deterministic categories)
# =============================================================================

class TestTaskLevelClassification:
    """The 518 real tasks, classified by information availability only. No
    candidate is SUFFICIENT without source navigation — every one of the 52
    needs the drawing for position, attachment surface and location — and no
    task is MISSING_SOURCE_EVIDENCE (every task's source is locatable)."""

    def test_classification_counts_match_the_pinned_matrix(self):
        counts = {}
        for item in _contract().items:
            for task in item.tasks:
                counts[_classify(item, task)] = counts.get(_classify(item, task), 0) + 1
        assert counts == {
            AI_OBSERVATION_PRESENT: 73,
            SOURCE_REFERENCE_ONLY: 237,
            NOT_APPLICABLE: 208,
        }
        assert SUFFICIENT not in counts
        assert MISSING_SOURCE_EVIDENCE not in counts

    def test_per_task_type_category_counts(self):
        contract = _contract()
        observed = {}
        for item in contract.items:
            for task in item.tasks:
                row = observed.setdefault(task.task_type, {})
                row[_classify(item, task)] = row.get(_classify(item, task), 0) + 1
        expected = {
            task_type: {
                category: n for category, n in {
                    AI_OBSERVATION_PRESENT: n_ai, SOURCE_REFERENCE_ONLY: n_src,
                    NOT_APPLICABLE: n_na,
                }.items() if n
            }
            for task_type, (n_ai, n_src, _n_miss, n_na)
            in REAL_TASK_CATEGORY_COUNTS.items()
        }
        assert observed == expected

    def test_every_candidate_requires_source_navigation_for_three_fields(self):
        """Position, attachment surface and location are never AI-extracted
        (the schema has none) — every reviewer must read those off the
        drawing, and the system points them at the right page."""
        for item in _contract().items:
            by_type = {t.task_type: t for t in item.tasks}
            for task_type in (TASK_SELECT_POSITION, TASK_SELECT_ATTACHMENT,
                              TASK_PROVIDE_LOCATION):
                task = by_type[task_type]
                assert task.current_value is None
                assert _classify(item, task) == SOURCE_REFERENCE_ONLY

    def test_position_and_attachment_choices_are_the_authoritative_vocabulary(self):
        for item in _contract().items:
            for task in item.tasks:
                if task.task_type in (TASK_SELECT_POSITION, TASK_SELECT_ATTACHMENT):
                    assert task.allowed_options == ("START", "END")

    def test_nominal_bolt_readings_are_shown_verbatim_never_converted(self):
        """The 19 hole-diameter tasks carry the nominal bolt readings VERBATIM
        in the AI's own wording ('Ø18mm holes', 'M22', '22', '18mm dia holes'
        — ten distinct shapes, exactly as read). They are observations, never
        converted into a diameter answer: no task current value is a
        HOLES_VALUE mapping and none carries a 'diameter_mm' key. The 33
        candidates the AI read no bolt information for show none."""
        from collections import Counter
        contract = _contract()
        readings = Counter()
        without = 0
        for item in contract.items:
            for task in item.tasks:
                if task.task_type != TASK_PROVIDE_HOLE_DIAMETER:
                    continue
                if task.current_value is None:
                    assert item.ai_bolt_readings == ()  # the AI truly read none
                    without += 1
                else:
                    assert task.current_value.startswith("((")  # raw reading tuples
                    assert "diameter_mm" not in task.current_value
                    assert item.ai_bolt_readings != ()
                    readings[task.current_value] += 1
        assert without == 33
        assert readings == {
            "((2, '18', None, ()),)": 3,
            "((4, '22mm holes', None, ()),)": 3,
            "((2, '18mm holes', None, ()),)": 1,
            "((4, '22', None, ()),)": 4,
            "((4, '22mm diameter', None, ()),)": 1,
            "((4, 'M22', None, ()),)": 1,
            "((None, '18mm diameter holes', None, ()),)": 1,
            "((None, '18mm dia holes', None, ()),)": 1,
            "((4, '22mm', None, ()),)": 2,
            "((4, 'Ø18mm holes', None, ()),)": 2,
        }
        assert sum(readings.values()) == 19

    def test_acknowledgement_and_identity_tasks_require_no_source_evidence(self):
        """The ack tasks grant nothing by themselves (their own wording) and
        identity is never AI output — these carry no AI value and demand no
        additional source evidence: NOT_APPLICABLE, exactly as 7AC defines."""
        for item in _contract().items:
            for task in item.tasks:
                if task.task_type in NO_SOURCE_EVIDENCE_TASK_TYPES:
                    assert task.current_value is None
                    assert _classify(item, task) == NOT_APPLICABLE


# =============================================================================
# Phase 6/7 — the material exposure (the one real gap, now closed additively)
# =============================================================================

class TestMaterialExposure:
    """The 7AK contract has always carried ai_material (7B2's workload map
    consumes it); the 7AM view model omitted it, so the UI's 'AI-extracted
    values' card never showed it. The smallest additive change passes it
    through verbatim: view field `material`, one render row. Missing stays
    missing — the six material-less candidates render '(none)', never a
    default."""

    def test_view_material_equals_contract_material_for_all_51(self):
        workflow = _fresh()
        for item in _contract(workflow).items:
            view = _view(workflow, item.package_id)
            assert view.extracted.material == item.ai_material

    def test_ui_renders_the_material_row_with_the_ai_value(self):
        body = _detail_page(_fresh(), "RP-0009")
        assert MATERIAL_ROW_300 in body

    def test_ui_renders_missing_material_honestly(self):
        body = _detail_page(_fresh(), "RP-0033")
        assert MATERIAL_ROW_NONE in body
        assert MATERIAL_ROW_300 not in body
        # The 7B3 truth stays: no tuple-shaped material value appears.
        assert "('material', '300')" not in body

    def test_material_row_is_verbatim_for_every_candidate(self):
        workflow = _fresh()
        for item in _contract(workflow).items:
            body = _detail_page(workflow, item.package_id)
            if item.ai_material is None:
                assert MATERIAL_ROW_NONE in body
                assert MATERIAL_ROW_300 not in body
            else:
                assert MATERIAL_ROW_300 in body
                assert MATERIAL_ROW_NONE not in body

    def test_confirm_task_current_value_stays_verbatim(self):
        body = _detail_page(_fresh(), "RP-0009")
        assert ("Current AI value: (('connected_member_marks', "
                "['005', 'PL018', 'PL032', 'PL025']), ('material', '300'))") in body

    def test_extracted_values_view_stays_frozen_and_additive(self):
        fields = dataclasses.fields(ExtractedValuesView)
        assert [f.name for f in fields] == [
            "member_references", "bolt_readings", "plate_readings",
            "weld_readings", "malformed_readings", "unrecognised_readings",
            "connection_type", "confidence", "material",
        ]
        view = _view(_fresh(), "RP-0001")
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.extracted.material = "400"

    def test_material_workflow_task_asks_for_a_designation_not_a_value(self):
        """With the existing request_material_specification=True flag, the
        six missing-material candidates get a task whose question states the
        source drawing records no grade — the reviewer decides, nothing is
        pre-filled, and the extracted-value card still shows none."""
        workflow = _material_workflow()
        for package_id in MISSING_MATERIAL_IDS:
            task = next(t for t in _group(workflow, package_id).tasks
                        if t.task_type == "PROVIDE_MATERIAL_SPECIFICATION")
            assert task.current_ai_value is None
            assert "states no material grade" in task.question
            assert MATERIAL_ROW_NONE in _detail_page(workflow, package_id)


# =============================================================================
# The mandated real-world examples (no engineering answers anywhere)
# =============================================================================

class TestRealCandidateExamples:
    """RP-0009, RP-0001, one missing-material candidate and two further
    candidates from different pages/patterns — what the reviewer SEES for
    each. No engineering value is asserted, suggested or evaluated."""

    def test_rp0009_page_5_view_b_b(self):
        item = _item(_fresh(), "RP-0009")
        assert item.evidence.source_page == 5
        assert item.evidence.detail_reference == "VIEW B-B"
        assert item.ai_member_references == ("005", "PL018", "PL032", "PL025")
        assert item.ai_material == "300"
        body = _detail_page(_fresh(), "RP-0009")
        assert "page 5; detail VIEW B-B" in body
        assert MATERIAL_ROW_300 in body

    def test_rp0001_page_1_no_detail(self):
        item = _item(_fresh(), "RP-0001")
        assert item.evidence.source_page == 1
        assert item.evidence.detail_reference is None
        assert item.ai_member_references == ("001", "PL028")
        assert item.ai_material == "300"

    def test_rp0033_page_18_missing_material(self):
        item = _item(_fresh(), "RP-0033")
        assert item.evidence.source_page == 18
        assert item.evidence.detail_reference == "VIEW A-A"
        assert item.ai_material is None
        # Its plate reading is clean enough not to need a PROVIDE_PLATE task.
        assert TASK_PROVIDE_PLATE not in {t.task_type for t in item.tasks}
        assert item.ai_plate_readings

    def test_rp0008_page_5_view_a_a(self):
        item = _item(_fresh(), "RP-0008")
        assert item.evidence.source_page == 5
        assert item.evidence.detail_reference == "VIEW A-A"
        assert item.ai_member_references == ("005", "PL008", "CL004")
        assert item.ai_material == "300"

    def test_rp0015_page_9_grid_bearing(self):
        item = _item(_fresh(), "RP-0015")
        assert item.evidence.source_page == 9
        assert item.evidence.detail_reference == "VIEW A-A"
        assert item.evidence.grid_reference == "A-A"
        assert item.ai_material == "300"

    def test_no_engineering_answers_exist_before_a_human_answers(self):
        for package_id in ("RP-0009", "RP-0001", "RP-0033", "RP-0008", "RP-0015"):
            item = _item(_fresh(), package_id)
            assert all(t.resolution_value is None for t in item.tasks)
            assert all(not t.resolved for t in item.tasks)
            # The reviewer-facing fields are questions and AI observations,
            # never proposed engineering answers.
            assert item.connection_id is None


# =============================================================================
# The ten mandated negative proofs
# =============================================================================

class TestNegativeProofs7B4:
    """1 source evidence cannot be fabricated; 2 source page cannot change;
    3 source drawing cannot change; 4 AI observation stays AI_EXTRACTED until
    confirmed; 5 missing evidence stays missing; 6 missing value never becomes
    a proposed value; 7 absent detail reference never invented; 8 stale
    contracts stay stale; 9 candidate identity immutable; 10 provenance
    intact."""

    def test_n1_source_evidence_cannot_be_fabricated(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        item = _item(advanced, "RP-0009")
        # The human's evidence note claims to be a reading of the drawing;
        # the source facts still come only from the extraction record, and
        # the AI's material reading is never rewritten by the confirmation.
        assert item.evidence.source_drawing_id == "SELBY-C1136"
        assert item.evidence.source_page == 5
        assert item.evidence.detail_reference == "VIEW B-B"
        assert item.ai_material == "300"
        view = _view(advanced, "RP-0009")
        assert view.extracted.material == "300"

    def test_n2_source_page_cannot_change_through_resolution(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert _item(advanced, "RP-0009").evidence.source_page == 5

    def test_n3_source_drawing_cannot_change_through_resolution(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert _item(advanced, "RP-0009").evidence.source_drawing_id == "SELBY-C1136"
        assert _item(advanced, "RP-0009").evidence.drawing_number == "001"

    def test_n4_ai_observation_stays_ai_extracted_until_confirmed(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        rp0009 = {p.field: p.provenance for p in _item(advanced, "RP-0009").provenance}
        assert rp0009["material"] == "HUMAN_REVIEWED"  # the human confirmed it
        # Everyone else's material keeps its AI label — confirmation of one
        # candidate never labels another.
        for item in _item(advanced, "RP-0001"), _item(advanced, "RP-0008"):
            assert {p.field: p.provenance for p in item.provenance}["material"] == \
                "AI_EXTRACTED"

    def test_n5_missing_material_stays_missing_without_a_material_answer(self, tmp_path):
        workflow = _material_workflow()
        group = _group(workflow, "RP-0034")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0034", output_dir=tmp_path,
            resolutions=_resolutions_except(
                group, {"PROVIDE_MATERIAL_SPECIFICATION"}, EVIDENCE_7B3),
        )
        item = _item(advanced, "RP-0034")
        assert item.ai_material is None
        assert _view(advanced, "RP-0034").extracted.material is None
        assert "material" not in {p.field for p in item.provenance}

    def test_n6_missing_value_never_becomes_a_proposed_value(self, tmp_path):
        # Revision 0 and after a resolution that never touches material: the
        # UI shows '(none)' — no default, no inferred grade, no placeholder.
        for package_id in MISSING_MATERIAL_IDS:
            assert MATERIAL_ROW_NONE in _detail_page(_fresh(), package_id)
            assert MATERIAL_ROW_300 not in _detail_page(_fresh(), package_id)
        workflow = _material_workflow()
        group = _group(workflow, "RP-0038")
        advanced = resolve_project_connection(
            workflow, package_id="RP-0038", output_dir=tmp_path,
            resolutions=_resolutions_except(
                group, {"PROVIDE_MATERIAL_SPECIFICATION"}, EVIDENCE_7B3),
        )
        assert MATERIAL_ROW_NONE in _detail_page(advanced, "RP-0038")

    def test_n7_absent_detail_reference_is_never_invented(self):
        items = _contract().items
        without = [i for i in items if i.evidence.detail_reference is None]
        assert len(without) == 52 - REAL_DETAIL_COUNT
        for item in without:
            text = _view(_fresh(), item.package_id).evidence.evidence_text
            assert "detail" not in text

    def test_n8_stale_review_contracts_stay_stale(self, tmp_path):
        before = _contract(_fresh())
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        after = _contract(advanced)
        for before_item, after_item in zip(before.items, after.items):
            if before_item.package_id == "RP-0009":
                assert after_item.decision == "AUTO"
                continue
            # Substance identical; only the real project-wide revision
            # counter advanced (7B2 test M) — including the evidence and the
            # new material view field.
            expected = {**dataclasses.asdict(before_item), "revision": 1}
            assert dataclasses.asdict(after_item) == expected

    def test_n9_candidate_identity_is_immutable(self, tmp_path):
        before = [i.package_id for i in _contract(_fresh()).items]
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        after = [i.package_id for i in _contract(advanced).items]
        assert before == after
        assert len(set(before)) == 52

    def test_n10_provenance_remains_intact(self, tmp_path):
        # The view change adds no provenance label; the labels stay exactly
        # the review report's own vocabulary.
        workflow = _fresh()
        for item in _contract(workflow).items:
            for entry in item.provenance:
                assert entry.provenance in (
                    "AI_EXTRACTED", "HUMAN_REVIEWED", "HUMAN_SUPPLEMENTED")
        advanced = _resolve_full_rp0009(workflow, tmp_path)
        assert {(p.field, p.provenance) for p in _item(advanced, "RP-0009").provenance} \
            == RP0009_RESOLVED_PROVENANCE


# =============================================================================
# The 52-candidate regression
# =============================================================================

class TestReal52CandidateRegression:
    """Exactly 52 candidates, identity and distributions exactly as 7B1/7B2
    pinned them; the captures byte-identical; page 26 carrying exactly its
    one recovered candidate; 7B3's behaviour unchanged; no fabrication
    outputs anywhere."""

    def test_52_candidates_intact(self):
        items = _contract().items
        assert [i.package_id for i in items] == [f"RP-{n:04d}" for n in range(1, 53)]
        assert len(items) == 52

    def test_real_distribution_unchanged(self):
        workload = build_exception_workload_map(_contract())
        assert {g.blocker_code: g.count for g in workload.blocker_code_groups} \
            == REAL_BLOCKER_COUNTS
        assert {g.task_type: g.count for g in workload.task_type_groups} \
            == REAL_TASK_COUNTS
        assert {g.source_page: g.count for g in workload.page_groups} \
            == REAL_PAGE_COUNTS
        material_groups = {g.material: g.count for g in workload.material_groups}
        assert material_groups == REAL_MATERIAL_COUNTS

    def test_capture_files_are_the_pinned_evidence(self):
        for path in CAPTURE_FILES:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == PINNED_SHA256[path.name], path.name

    def test_page_26_carries_exactly_its_one_recovered_candidate(self):
        workload = build_exception_workload_map(_contract())
        assert {g.source_page: g.count for g in workload.page_groups}[26] == 1

    def test_7b3_behaviour_unchanged_rp0009_still_auto_generated_verified(
            self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        state = _state(advanced, "RP-0009")
        assert state.decision == "AUTO"
        assert state.output_status == "GENERATED"
        assert state.verification_status == "VERIFIED"
        assert state.generated_files
        # The 7B3 material truth is unchanged: the AI value stays "300" and
        # the human confirmation labels it HUMAN_REVIEWED.
        item = _item(advanced, "RP-0009")
        assert item.ai_material == "300"
        assert {p.field: p.provenance for p in item.provenance}["material"] == \
            "HUMAN_REVIEWED"

    def test_7b3_behaviour_unchanged_missing_material_ui(self):
        body = _detail_page(_fresh(), "RP-0033")
        assert "('material', '300')" not in body
        assert "'connected_member_marks', 'plate'" in body

    def test_no_fabrication_outputs_anywhere(self, tmp_path):
        advanced = _resolve_full_rp0009(_fresh(), tmp_path)
        assert list(advanced.generated_files) == [str(p) for p in tmp_path.glob("*")]
        assert len(advanced.generated_files) == 1
        for directory in (Path("app"), Path("tests")):
            assert not [p for p in directory.rglob("*.pdf")], directory


# =============================================================================
# Determinism and purity
# =============================================================================

class TestDeterminismAndPurity:
    def test_repeated_builds_are_identical(self):
        first = _contract(_fresh())
        for _ in range(2):
            assert _contract(_fresh()) == first

    def test_view_chain_writes_nothing(self, tmp_path):
        import os
        before = set(os.listdir("."))
        workflow = _fresh()
        for item in _contract(workflow).items:
            _detail_page(workflow, item.package_id)
        after = set(os.listdir("."))
        assert after == before

    def test_no_canonical_fixture_is_mutated(self):
        import json
        for path in CAPTURE_FILES:
            digest_before = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest_before == PINNED_SHA256[path.name], path.name
            assert json.loads(path.read_text())  # still parseable, untouched
