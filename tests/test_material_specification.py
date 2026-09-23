"""
Milestone 7AT — Material Specification End-to-End.

7AS exposed the gap: material specification does not enter the SteelSpec
extraction/review chain, so every fabrication drawing carries MATERIAL NOT
SPECIFIED — even when the source genuinely states a grade. 7AT closes the
transport gap WITHOUT changing the existing honest behaviour:

  - the AI schema may report `material` when the source page explicitly
    states one (verbatim, never normalised, never inferred);
  - `material` is a first-class (OPTIONAL) engineering field: it flows
    through 7W's review package, 7V's reviewed specification, 7AC's
    resolution contract, 7AD's rerun, 7Z/7AA, 7AE's gate evidence, 7AF's
    dispatch, the drawing's title block, 7AG's verification, 7AQ's
    acceptance trace and 7AR's packaged audit — and 7AS then reads it from
    the actual drawing;
  - a source that states no grade still yields MATERIAL NOT SPECIFIED —
    7AS's exact existing finding is preserved (nothing is invented);
  - a reviewer may supply a grade only through an explicitly-requested
    review task (request_material_specification=True), labelled
    HUMAN_SUPPLEMENTED, traced by 7AQ, and never emitted by default.

The three end-to-end proofs required by the brief:

  Case A (negative, real Arkles): the genuine capture states no material;
  no material task exists by default; the drawings say MATERIAL NOT
  SPECIFIED and 7AS reports the unchanged finding.
  Case B (positive, controlled synthetic source): a clearly-marked
  synthetic page states "MATERIAL: 300PLUS"; the AI reports it; the
  reviewer CONFIRMS it (HUMAN_REVIEWED); it reaches the drawing and 7AS
  finds no material gap.
  Case C (human-supplemented, real Arkles): the reviewer supplies a grade
  through the explicitly-requested PROVIDE_MATERIAL_SPECIFICATION task
  (HUMAN_SUPPLEMENTED); it reaches the drawing and 7AS clears — with the
  full audit trail (task -> blocker -> provenance -> AI observation).

Nothing here weakens 7AS, adds a standards database, invents provenance
vocabulary, or commits any result to git.
"""

import dataclasses
import hashlib
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

import app.cad_engine.fabricator_acceptance as fa
import app.cad_engine.production_acceptance as production_acceptance
import app.cad_engine.project_workflow as workflow_module
import app.review_ui.render as render_module
from app.cad_engine.automation_gate import (
    AUTOMATION_BLOCKER_PROVENANCE,
    AUTOMATION_DECISION_AUTO,
    AUTOMATION_DECISION_REVIEW,
)
from app.cad_engine.automation_pipeline import evaluate_reviewed_connection_for_automation
from app.cad_engine.connection_review_package import (
    CATEGORY_HUMAN_REVIEWED,
    CATEGORY_HUMAN_SUPPLEMENTED,
    CATEGORY_MISSING,
    CATEGORY_NEEDS_CONFIRMATION,
    ENGINEERING_FIELDS,
    ConnectionReviewSupplement,
    ai_connection_to_extraction,
    build_review_report,
    build_reviewed_connection_specification,
    create_review_package,
)
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import VERIFICATION_STATUS_VERIFIED
from app.cad_engine.exception_resolution import (
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_MATERIAL_VALUE,
    TASK_CONFIRM_AI_VALUES,
    TASK_PROVIDE_CONNECTION_IDENTITY,
    TASK_PROVIDE_MATERIAL_SPECIFICATION,
    HumanResolution,
    build_exception_resolution_package,
)
from app.cad_engine.fabrication_package import PACKAGE_STATUS_READY, build_fabrication_package
from app.cad_engine.production_acceptance import ACCEPTANCE_STATUS_ACCEPTED, accept_production_job
from app.cad_engine.project_automation import evaluate_project_for_automation
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.review_contract import (
    build_connection_review_contract,
    build_project_review_contract,
)
from app.cad_engine.review_view_model import render_connection_view, render_project_view
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    check_reviewed_connection_specification_completeness,
)
from app.review_ui.render import task_input_names, unsupported_tasks
from app.review_ui.session import ResolutionInputError, parse_task_answer
from tests.test_automation_gate import _reviewed_package
from tests.test_exception_resolution import _tasks
from tests.test_fabricator_acceptance import MATERIAL_FINDING, _answers, _copied_package, _write_pdf
from tests.test_project_automation import _collection, _evaluate, _member_rows
from tests.test_project_extraction_intake import needs_real_capture, real_known_marks
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    CONNECTION_IDENTITIES,
    DRAWING_SET_PAGE_COUNT,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _pdfs,
    _sha256,
    _workflow_built,
)
from tests.test_real_world_exception_proof import (
    ARKLES_REAL_AI_EXTRACTION,
    HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE,
    HUMAN_SUPPLIED_ATTACHMENTS,
    HUMAN_SUPPLIED_HOLES,
    HUMAN_SUPPLIED_LOCATION,
    HUMAN_SUPPLIED_MEMBER_MARKS,
    HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
    HUMAN_SUPPLIED_MEMBER_ROWS,
    HUMAN_SUPPLIED_PLATE,
    HUMAN_SUPPLIED_POSITION,
    HUMAN_SUPPLIED_SECTION_MATCHER,
    _answer,
)
from tests.test_resolution_rerun import _rerun
from tests.test_reviewed_connection_specification import make_synthetic_test_spec

# =============================================================================
# Controlled fixtures — clearly-marked synthetic source data, and test data
# the TEST supplies playing the reviewer. "300PLUS" is an EXAMPLE designation
# here, never a default: any explicit string must travel (see the S355 and
# AS/NZS 3678-300 cases below).
# =============================================================================
POSITIVE_PROJECT_ID = "PROJ-7AT-SYN"
POSITIVE_SOURCE_DRAWING_ID = "STEELSPEC-7AT-SYN-001"
POSITIVE_MATERIAL = "300PLUS"
POSITIVE_CONNECTION_ID = "CONN-7AT-POS-001"

SUPPLEMENTED_PROJECT_ID = "PROJ-7AT-SUP"
HUMAN_SUPPLIED_MATERIAL = "300PLUS"  # the reviewer's own answer — test data, never extracted

# A SYNTHETIC single-page source extraction: the page states exactly one
# connection fact the controlled fixture exists for — its material. Nothing
# else is AI-extracted, so every other field follows the existing review
# path untouched. Not Arkles data; not produced by any AI.
SYNTHETIC_POSITIVE_PAGES = (
    {
        "page_number": 1,
        "drawing_number": POSITIVE_SOURCE_DRAWING_ID,
        "drawing_title": "7AT controlled positive fixture — synthetic page extraction",
        "revision": "A",
        "raw_members": [],
        "raw_connections": [
            {
                "connection_type": "end plate connection",
                "material": POSITIVE_MATERIAL,
            },
        ],
        "parse_failed": False,
    },
)


def _positive_intake():
    return intake_page_extractions(
        [dict(page) for page in SYNTHETIC_POSITIVE_PAGES],
        project_id=POSITIVE_PROJECT_ID,
        source_drawing_id=POSITIVE_SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
        drawing_set_page_count=1,
    )


def _positive_workflow_built():
    """7Y -> 7AB -> 7AC for the controlled positive fixture (no material flag —
    the AI already observed the material)."""
    intake = _positive_intake()
    initial = evaluate_project_for_automation(intake.collection, intake=intake)
    built = build_exception_resolution_package(
        initial, intake.collection, member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
    )
    return intake, initial, built


def _positive_resolutions(built):
    """Every task of the single synthetic candidate answered: the standard
    reviewer fixture plus the AI material explicitly CONFIRMED (Case B)."""
    group = next(g for g in built.connection_tasks if g.review_package_id == "RP-0001")
    tasks = _tasks(group)
    expected = set(HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE) | {TASK_CONFIRM_AI_VALUES}
    assert {t.task_type for t in group.tasks} == expected
    resolutions = []
    for task in group.tasks:
        if task.task_type == TASK_CONFIRM_AI_VALUES:
            value = ("material",)  # confirm the AI's material, nothing else
        elif task.task_type == TASK_PROVIDE_CONNECTION_IDENTITY:
            value = POSITIVE_CONNECTION_ID
        else:
            value = HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[task.task_type]
        resolutions.append(_answer(task, value))
    return resolutions


def _start_positive():
    intake, initial, built = _positive_workflow_built()
    workflow = workflow_module.start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
    )
    return intake, initial, built, workflow


def _workflow_built_with_material_request():
    """The real Arkles capture with request_material_specification=True —
    the explicitly-requested human-supplementation route (Case C)."""
    intake = intake_page_extractions(
        list(ARKLES_REAL_AI_EXTRACTION),
        project_id=SUPPLEMENTED_PROJECT_ID,
        source_drawing_id=SOURCE_DRAWING_ID,
        known_member_marks=real_known_marks(),
        drawing_set_page_count=DRAWING_SET_PAGE_COUNT,
    )
    initial = evaluate_project_for_automation(intake.collection, intake=intake)
    built = build_exception_resolution_package(
        initial, intake.collection, member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        request_material_specification=True,
    )
    return intake, initial, built


def _resolutions_with_material(built, package_id):
    """The standard reviewer fixture plus the reviewer's material answer."""
    group = next(g for g in built.connection_tasks if g.review_package_id == package_id)
    expected = set(HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE) | {TASK_PROVIDE_MATERIAL_SPECIFICATION}
    assert {t.task_type for t in group.tasks} == expected
    resolutions = []
    for task in group.tasks:
        if task.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION:
            value = HUMAN_SUPPLIED_MATERIAL
        elif task.task_type == TASK_PROVIDE_CONNECTION_IDENTITY:
            value = CONNECTION_IDENTITIES[package_id]
        else:
            value = HUMAN_SUPPLIED_ANSWERS_BY_TASK_TYPE[task.task_type]
        resolutions.append(_answer(task, value))
    return resolutions


def _pdf_text_from_bytes(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


_PER_SAVE_METADATA = re.compile(
    rb"/ID\s*\[<[0-9a-f]+><[0-9a-f]+>\]"
    rb"|/CreationDate\s*\([^)]*\)"
    rb"|/ModDate\s*\([^)]*\)"
)


def _strip_per_save_metadata(data: bytes) -> bytes:
    """The PDF bytes minus reportlab's per-save metadata: the trailer document
    identifier (/ID) and the creation/modification dates. All three are
    regenerated on every save; every other byte is deterministic."""
    return _PER_SAVE_METADATA.sub(b"", data)


def _conn(acceptance, package_id):
    return next(c for c in acceptance.connections if c.package_id == package_id)


def _item(package):
    return package.items[0]


def _q12_of(result, index=0):
    return {item.checklist_item: item for item in result.drawings[index].results}[
        fa.CHECKLIST_ITEM_FABRICATION_BLOCKING]


# =============================================================================
# Module-scoped end-to-end journeys (genuine stages only).
# =============================================================================
@pytest.fixture(scope="module")
def positive_journey(tmp_path_factory):
    """Case B: synthetic source -> AI material -> review (CONFIRM) -> rerun ->
    AUTO -> 7AE -> 7AF -> 7AG -> 7AQ ACCEPTED -> 7AR READY -> 7AS ACCEPTED."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7at-positive"))
    _, _, built, workflow = _start_positive()
    workflow = workflow_module.resolve_project_connection(
        workflow, package_id="RP-0001",
        resolutions=_positive_resolutions(built), output_dir=tmp,
    )
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_answers(package, q12=fa.ANSWER_PASS))
    return SimpleNamespace(
        tmp=tmp, built=built, workflow=workflow, acceptance=acceptance,
        package=package, result=result,
    )


@pytest.fixture(scope="module")
def negative_journey(tmp_path_factory):
    # Consuming tests carry @needs_real_capture; this fixture runs only for them.
    """Case A: the genuine Arkles capture (no material) through the unchanged
    default chain — drawings say MATERIAL NOT SPECIFIED and 7AS reports its
    existing finding."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7at-negative"))
    _, _, built = _workflow_built()
    states = _full_sequence(tmp)
    workflow = states[-1]
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_answers(package))
    return SimpleNamespace(
        tmp=tmp, built=built, states=states, workflow=workflow,
        acceptance=acceptance, package=package, result=result,
    )


@pytest.fixture(scope="module")
def supplemented_journey(tmp_path_factory):
    # Consuming tests carry @needs_real_capture; this fixture runs only for them.
    """Case C: real Arkles capture + the explicitly-requested material task;
    the reviewer supplies a grade -> HUMAN_SUPPLEMENTED -> drawing shows it ->
    7AS clears."""
    tmp = Path(tmp_path_factory.mktemp("genuine-7at-supplemented"))
    intake, _, built = _workflow_built_with_material_request()
    workflow = workflow_module.start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
        request_material_specification=True,
    )
    states = [workflow]
    for package_id in CANDIDATE_IDS:
        states.append(workflow_module.resolve_project_connection(
            states[-1], package_id=package_id,
            resolutions=_resolutions_with_material(built, package_id),
            output_dir=tmp,
        ))
    workflow = states[-1]
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    result = fa.evaluate_fabricator_acceptance(
        package, acceptance_answers=_answers(package, q12=fa.ANSWER_PASS))
    return SimpleNamespace(
        tmp=tmp, built=built, states=states, workflow=workflow,
        acceptance=acceptance, package=package, result=result,
    )


# =============================================================================
# 1. Extraction and review-package layer (7W / 7Y).
# =============================================================================
def _package(raw_connection, *, supplement=None, **extraction_kwargs):
    extraction = ai_connection_to_extraction(dict(raw_connection), **extraction_kwargs)
    return create_review_package(extraction, supplement)


def _material_entry(package):
    report = build_review_report(package)
    return report, next(e for e in report.entries if e.field == "material")


class TestExtractionLayer:
    def test_explicit_ai_material_is_kept_verbatim_and_awaits_confirmation(self):
        report, entry = _material_entry(_package({"material": "AS/NZS 3678-300"}))
        assert entry.ai_value == "AS/NZS 3678-300" and entry.current_value == "AS/NZS 3678-300"
        assert entry.category == CATEGORY_NEEDS_CONFIRMATION
        assert entry.provenance == PROVENANCE_AI_EXTRACTED
        assert "material" in report.found and "material" in report.needs_confirmation
        assert report.provenance["material"] == PROVENANCE_AI_EXTRACTED

    def test_material_is_never_normalised_or_converted(self):
        spec = build_reviewed_connection_specification(
            _package({"material": "AS/NZS 3678-300"}))
        assert spec.material == "AS/NZS 3678-300" and spec.material != "300PLUS"

    @pytest.mark.parametrize("raw, extraction_kwargs", [
        ({"connects_members": ["310UB40"], "confidence": 95}, {}),
        ({"connects_members": ["250PFC"]}, {}),
        ({"bolts": [{"size": "M20", "grade": "8.8"}]}, {}),  # a bolt grade is never material
        ({"confidence": 100}, {"project_id": "PROJ-NZ-WGTN", "source_drawing_id": "NZ-300PLUS-STD"}),
        ({}, {"drawing_number": "300PLUS-DWG-01"}),  # the filename states nothing
    ])
    def test_material_is_never_inferred_from_context(self, raw, extraction_kwargs):
        package = _package(raw, **extraction_kwargs)
        report, entry = _material_entry(package)
        assert entry.ai_value is None and entry.category == CATEGORY_MISSING
        assert entry.needs_supplementation is True
        assert "material" in report.missing
        assert "material" not in report.provenance
        assert build_reviewed_connection_specification(package).material is None

    def test_missing_material_is_missing_never_invented(self):
        package = _package({"connects_members": ["L2", "L3"]},
                           project_id="PROJ-X", source_drawing_id="DW-X")
        report, entry = _material_entry(package)
        assert entry.category == CATEGORY_MISSING and entry.ai_value is None
        assert build_reviewed_connection_specification(package).material is None
        assert "material" in report.missing and "material" in report.needs_supplementation

    def test_malformed_material_is_preserved_and_never_coerced(self):
        raw = {"material": {"grade": "300PLUS"}}
        package = _package(raw)
        report, entry = _material_entry(package)
        assert package.extraction.malformed_fields["material"] == {"grade": "300PLUS"}
        assert package.extraction.material is None
        assert entry.ai_value == {"grade": "300PLUS"}  # the raw reading, never coerced
        assert entry.category == CATEGORY_MISSING
        assert "MALFORMED_AI_FIELD" in [i.code for i in report.issues]

    def test_supplied_material_is_human_supplemented_and_labelled(self):
        package = _package({}, supplement=ConnectionReviewSupplement(material="S355"))
        report, entry = _material_entry(package)
        assert entry.category == CATEGORY_HUMAN_SUPPLEMENTED
        assert entry.provenance == PROVENANCE_HUMAN_SUPPLEMENTED
        assert entry.current_value == "S355"
        spec = build_reviewed_connection_specification(package)
        assert spec.material == "S355"
        assert spec.provenance["material"] == PROVENANCE_HUMAN_SUPPLEMENTED

    def test_supplied_material_overrides_ai_value_with_explicit_note(self):
        package = _package(
            {"material": POSITIVE_MATERIAL},
            supplement=ConnectionReviewSupplement(material="250PLUS"),
        )
        report, entry = _material_entry(package)
        assert entry.category == CATEGORY_HUMAN_SUPPLEMENTED
        assert entry.current_value == "250PLUS"
        assert entry.ai_value == POSITIVE_MATERIAL  # the AI observation is retained
        assert "overrides" in entry.note

    def test_confirming_ai_material_yields_human_reviewed_not_supplemented(self):
        package = _package(
            {"material": "AS/NZS 3678-300"},
            supplement=ConnectionReviewSupplement(
                confirmed_ai_fields=frozenset(("material",))),
        )
        report, entry = _material_entry(package)
        assert entry.category == CATEGORY_HUMAN_REVIEWED
        assert entry.provenance == PROVENANCE_HUMAN_REVIEWED
        assert entry.current_value == "AS/NZS 3678-300"  # a confirm never relabels the value
        assert report.provenance["material"] == PROVENANCE_HUMAN_REVIEWED

    def test_confirming_and_supplying_material_is_a_conflict_never_silent(self):
        package = _package(
            {"material": POSITIVE_MATERIAL},
            supplement=ConnectionReviewSupplement(
                material="250PLUS", confirmed_ai_fields=frozenset(("material",))),
        )
        report, entry = _material_entry(package)
        assert "CONFIRM_AND_SUPPLY_CONFLICT" in [i.code for i in report.issues]
        # Neither decision silently wins: the field reverts to the unconfirmed AI value.
        assert entry.category == CATEGORY_NEEDS_CONFIRMATION
        assert entry.current_value == POSITIVE_MATERIAL

    def test_two_pages_each_keep_their_own_material_verbatim(self):
        pages = [
            {**SYNTHETIC_POSITIVE_PAGES[0], "page_number": 1,
             "raw_connections": [{"material": "300PLUS"}]},
            {**SYNTHETIC_POSITIVE_PAGES[0], "page_number": 2,
             "raw_connections": [{"material": "S355"}]},
        ]
        intake = intake_page_extractions(
            pages, project_id="PROJ-TWO", source_drawing_id="DW-TWO",
            known_member_marks=(), drawing_set_page_count=2,
        )
        assert len(intake.collection.candidates) == 2
        materials = [
            next(e for e in build_review_report(c.package).entries
                 if e.field == "material").ai_value
            for c in intake.collection.candidates
        ]
        assert materials == ["300PLUS", "S355"]  # verbatim, never cross-chosen


# =============================================================================
# 2. The review-authority rule (7V): present material must carry a label.
# =============================================================================
class TestSpecificationAuthority:
    def test_material_without_provenance_label_is_rejected_by_7v(self):
        spec = make_synthetic_test_spec(material="300PLUS")  # provenance has no "material"
        result = check_reviewed_connection_specification_completeness(spec)
        assert result.error is not None and "material" in result.error

    def test_material_with_human_label_passes_the_authority_rule(self):
        base = make_synthetic_test_spec()
        spec = dataclasses.replace(
            base, material="300PLUS",
            provenance={**base.provenance, "material": PROVENANCE_HUMAN_SUPPLEMENTED},
        )
        result = check_reviewed_connection_specification_completeness(spec)
        assert result.error is None

    def test_ai_extracted_material_is_not_a_7v_shape_failure(self):
        # 7V rejects UNLABELLED material; a material still labelled AI_EXTRACTED
        # passes 7V (the 7Z PROVENANCE blocker is the authority that keeps
        # unconfirmed AI values out of automation).
        base = make_synthetic_test_spec()
        spec = dataclasses.replace(
            base, material="300PLUS",
            provenance={**base.provenance, "material": PROVENANCE_AI_EXTRACTED},
        )
        result = check_reviewed_connection_specification_completeness(spec)
        assert result.error is None


# =============================================================================
# 3. The resolution contract (7AC) and rerun (7AD).
# =============================================================================
class TestResolutionContract:
    def test_material_task_is_emitted_only_when_requested_and_material_is_missing(self):
        # AI material present -> not in report.missing -> never a material task.
        intake, initial, _ = _positive_workflow_built()
        built = build_exception_resolution_package(
            initial, intake.collection, member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
            request_material_specification=True,
        )
        group = next(g for g in built.connection_tasks if g.review_package_id == "RP-0001")
        assert TASK_PROVIDE_MATERIAL_SPECIFICATION not in {
            t.task_type for t in group.tasks
        }

    def test_request_material_specification_must_be_bool(self):
        intake, initial, _ = _positive_workflow_built()
        with pytest.raises(TypeError):
            build_exception_resolution_package(
                initial, intake.collection, member_rows=None,
                request_material_specification="yes",
            )

    @needs_real_capture
    def test_default_arkles_package_has_no_material_task(self):
        _, _, built = _workflow_built()
        for group in built.connection_tasks:
            types = [t.task_type for t in group.tasks]
            assert TASK_PROVIDE_MATERIAL_SPECIFICATION not in types
            assert all(t.answer_type != ANSWER_MATERIAL_VALUE for t in group.tasks)
            assert len(group.tasks) == 8

    @needs_real_capture
    def test_requested_material_task_is_appended_last_with_its_own_vocabulary(self):
        _, _, default_built = _workflow_built()
        _, _, built = _workflow_built_with_material_request()
        for default_group, group in zip(default_built.connection_tasks, built.connection_tasks):
            default_types = [t.task_type for t in default_group.tasks]
            types = [t.task_type for t in group.tasks]
            # Existing tasks keep their ids and order; material is appended last.
            assert types[:8] == default_types
            assert len(types) == 9
            material_task = group.tasks[-1]
            assert material_task.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION
            assert material_task.answer_type == ANSWER_MATERIAL_VALUE
            assert material_task.blocker_codes == ()  # deliberately NOT a 7Z blocker
            assert "material" in material_task.question.lower()
            assert "MATERIAL NOT SPECIFIED" in material_task.evidence_requirement
            assert "never inferred" in material_task.evidence_requirement

    @needs_real_capture
    def test_7ac_refuses_empty_whitespace_and_non_string_material_answers(self, tmp_path):
        intake, _, built = _workflow_built_with_material_request()
        workflow = workflow_module.start_project_workflow(
            intake.collection,
            intake=intake,
            member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
            member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
            section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
            request_material_specification=True,
        )
        group = next(g for g in built.connection_tasks
                     if g.review_package_id == "RP-0001")
        tasks = list(group.tasks)
        good = _resolutions_with_material(built, "RP-0001")
        for bad in ("   ", 42):
            resolutions = [
                _answer(t, r.answer) if t.answer_type != ANSWER_MATERIAL_VALUE
                else HumanResolution(t.task_id, t.task_type, t.answer_type, bad, "?")
                for t, r in zip(tasks, good)
            ]
            with pytest.raises(ValueError):
                workflow_module.resolve_project_connection(
                    workflow, package_id="RP-0001", resolutions=resolutions,
                    output_dir=tmp_path,
                )
        # Nothing was processed: the workflow is unchanged and no artifacts exist.
        assert workflow.revision == 0
        assert list(tmp_path.glob("*.pdf")) == []

    def test_7ad_refuses_bad_material_even_if_shape_validation_is_bypassed(self):
        # 7AD re-checks every resolution against the task vocabulary itself — a
        # material resolution smuggled past the 7AC payload validation is refused.
        collection, built = self._flagged_ad_package()
        group = built.connection_tasks[0]
        task = _tasks(group)[TASK_PROVIDE_MATERIAL_SPECIFICATION]

        smuggled = dataclasses.replace(
            task,
            resolution=HumanResolution(task.task_id, task.task_type, task.answer_type, "   ", "?"),
        )
        tampered = dataclasses.replace(
            built,
            connection_tasks=(
                dataclasses.replace(
                    group,
                    tasks=tuple(t if t.task_id != task.task_id else smuggled for t in group.tasks),
                ),
            ),
        )

        rerun = _rerun(tampered, collection)
        outcome = rerun.outcomes[0]
        assert outcome.decision == AUTOMATION_DECISION_REVIEW
        assert len(outcome.resolutions_refused) == 1
        assert "material" in outcome.resolutions_refused[0].reason
        assert task.task_id in outcome.remaining_task_ids
        assert outcome.rebuilt_package.supplement.material is None

    @staticmethod
    def _flagged_ad_package():
        """A REVIEW-outcome package with the material task explicitly requested —
        the same closed-loop inputs the 7AD tests use, plus the 7AT flag."""
        collection = _collection(_reviewed_package(holes=None), project_id="PROJ-7AT")
        result = _evaluate(collection)
        built = build_exception_resolution_package(
            result, collection, member_rows=_member_rows(),
            request_material_specification=True,
        )
        return collection, built


# =============================================================================
# 4. Dispatch guard (7AJ) and acceptance tracing (7AQ).
# =============================================================================
class TestDispatchGuard:
    def test_only_human_owned_material_reaches_the_drawing(self):
        base = make_synthetic_test_spec()
        assert workflow_module._drawing_material(None) is None
        plain = dataclasses.replace(base, material="300PLUS")  # no provenance entry
        assert workflow_module._drawing_material(plain) is None
        ai_only = dataclasses.replace(
            base, material="300PLUS",
            provenance={**base.provenance, "material": PROVENANCE_AI_EXTRACTED},
        )
        assert workflow_module._drawing_material(ai_only) is None
        reviewed = dataclasses.replace(
            base, material="300PLUS",
            provenance={**base.provenance, "material": PROVENANCE_HUMAN_REVIEWED},
        )
        assert workflow_module._drawing_material(reviewed) == "300PLUS"
        supplemented = dataclasses.replace(
            base, material="S355",
            provenance={**base.provenance, "material": PROVENANCE_HUMAN_SUPPLEMENTED},
        )
        assert workflow_module._drawing_material(supplemented) == "S355"


class TestAcceptanceTracing:
    def test_material_answer_maps_to_the_material_field(self):
        assert production_acceptance._ANSWER_FIELDS[ANSWER_MATERIAL_VALUE] == ("material",)
        resolution = HumanResolution(
            "T", TASK_PROVIDE_MATERIAL_SPECIFICATION, ANSWER_MATERIAL_VALUE, "300PLUS", "?")
        assert production_acceptance._resolution_fields(resolution) == ("material",)
        assert production_acceptance._resolution_requires_human_provenance(resolution) is True

    def test_unconfirmed_ai_material_blocks_automation_never_auto(self):
        # A fully-reviewed package whose material is still AI_EXTRACTED (no human
        # decision) can never drive automation: 7Z's PROVENANCE blocker is the
        # authority, and the dispatch guard forwards nothing.
        extraction = ai_connection_to_extraction(
            {"material": POSITIVE_MATERIAL}, source_page=1)
        supplement = ConnectionReviewSupplement(
            review_status="approved",
            connected_member_marks=list(HUMAN_SUPPLIED_MEMBER_MARKS),
            position=HUMAN_SUPPLIED_POSITION,
            plate=HUMAN_SUPPLIED_PLATE,
            holes=HUMAN_SUPPLIED_HOLES,
            location=HUMAN_SUPPLIED_LOCATION,
            attachments=list(HUMAN_SUPPLIED_ATTACHMENTS),
            connection_id="CONN-UNCONFIRMED",
        )
        package = create_review_package(
            extraction, supplement, known_member_marks=HUMAN_SUPPLIED_MEMBER_MARKS)
        result = evaluate_reviewed_connection_for_automation(
            package,
            member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
            member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
            section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
        )
        gate = result.automation_gate_result
        assert gate.decision == AUTOMATION_DECISION_REVIEW
        assert {f.code for f in gate.blockers} == {AUTOMATION_BLOCKER_PROVENANCE}
        (provenance_finding,) = gate.blockers
        assert "material" in provenance_finding.message
        spec = build_reviewed_connection_specification(package)
        assert spec.provenance["material"] == PROVENANCE_AI_EXTRACTED
        assert workflow_module._drawing_material(spec) is None


# =============================================================================
# 5. Case A — the genuine negative proof (real Arkles, unchanged default chain).
# =============================================================================
@needs_real_capture
class TestNegativeProof:
    def test_initial_package_has_eight_tasks_and_no_material_route(self, negative_journey):
        for group in negative_journey.built.connection_tasks:
            assert len(group.tasks) == 8
            assert all(t.answer_type != ANSWER_MATERIAL_VALUE for t in group.tasks)

    def test_all_three_drawings_carry_material_not_specified(self, negative_journey):
        pdfs = _pdfs(negative_journey.tmp)
        assert len(pdfs) == 3
        for path in pdfs:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            assert "NOT SPECIFIED" in text
            assert "300PLUS" not in text

    def test_7ae_evidence_carries_no_material_line(self, negative_journey):
        for record in negative_journey.workflow.connection_records:
            assert not any("material" in line for line in record.gate_result.evidence_summary)

    def test_7aq_accepts_but_7as_reports_the_unchanged_material_finding(self, negative_journey):
        # 7AQ is unchanged: no material task exists by default, so no open task
        # finding — the job is accepted. 7AS then reports the genuine gap.
        assert negative_journey.acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        result = negative_journey.result
        assert result.status == fa.ACCEPTANCE_STATUS_NOT_ACCEPTED
        assert all(d.status == fa.ACCEPTANCE_STATUS_NOT_ACCEPTED for d in result.drawings)
        for index in range(3):
            q12 = _q12_of(result, index)
            assert q12.answer == fa.ANSWER_FAIL
            assert q12.finding == MATERIAL_FINDING  # the EXACT existing finding, untouched
            assert [f.kind for f in q12.evaluator_findings] == [fa.FINDING_MISSING_INFORMATION]

    def test_arkles_result_was_not_simply_changed_to_300plus(self, negative_journey):
        # The brief's prohibition: the real Arkles outcome must stay MATERIAL
        # NOT SPECIFIED — nothing defaults it to any grade.
        for package_id in CANDIDATE_IDS:
            conn = _conn(negative_journey.acceptance, package_id)
            contract = conn.trace.contract
            assert contract.ai_material is None
            assert not any(e.field == "material" for e in contract.provenance)


# =============================================================================
# 6. Case B — the positive proof (source-derived material, human-confirmed).
# =============================================================================
class TestPositiveProof:
    def test_initial_package_exposes_the_ai_material_through_the_provenance_task(
            self, positive_journey):
        group = next(g for g in positive_journey.built.connection_tasks
                     if g.review_package_id == "RP-0001")
        confirm = _tasks(group)[TASK_CONFIRM_AI_VALUES]
        assert confirm.answer_type == ANSWER_CONFIRMED_FIELDS
        assert confirm.current_ai_value == (("material", POSITIVE_MATERIAL),)

    def test_material_travels_through_every_stage(self, positive_journey):
        journey = positive_journey
        workflow = journey.workflow
        assert workflow.revision == 1
        connection = workflow.connections[0]
        assert connection.decision == AUTOMATION_DECISION_AUTO
        assert connection.output_status == OUTPUT_STATUS_GENERATED
        assert connection.verification_status == VERIFICATION_STATUS_VERIFIED
        # 7AE evidence names the material and its provenance.
        record = workflow.connection_records[0]
        assert any(
            "material" in line and POSITIVE_MATERIAL in line and "HUMAN_REVIEWED" in line
            for line in record.gate_result.evidence_summary
        )
        # 7AQ: accepted, with the confirm decision traced to the material field.
        acceptance = journey.acceptance
        assert acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        conn = _conn(acceptance, "RP-0001")
        assert conn.accepted is True
        assert conn.trace.contract.ai_material == POSITIVE_MATERIAL  # verbatim observation
        assert any(
            e.field == "material" and e.provenance == PROVENANCE_HUMAN_REVIEWED
            for e in conn.trace.contract.provenance
        )
        confirm_decisions = [
            d for d in conn.trace.human_decisions
            if d.task_type == TASK_CONFIRM_AI_VALUES and d.applied
        ]
        assert confirm_decisions
        assert any("material" in d.answer for d in confirm_decisions)
        assert any("material" in d.fields for d in confirm_decisions)

    def test_7ar_package_audits_the_material_and_the_7as_gap_is_closed(self, positive_journey):
        package = positive_journey.package
        assert package.status == PACKAGE_STATUS_READY
        item = _item(package)
        audit = dict(item.audit)
        assert audit["ai_observations"]["material"] == POSITIVE_MATERIAL
        assert ["material", PROVENANCE_HUMAN_REVIEWED] in audit["field_provenance"]
        # The ACTUAL drawing says the grade.
        text = _pdf_text_from_bytes(item.source_bytes)
        assert POSITIVE_MATERIAL in text and "NOT SPECIFIED" not in text
        # 7AS observes the drawing itself: PASS, no material finding.
        result = positive_journey.result
        assert result.status == fa.ACCEPTANCE_STATUS_ACCEPTED
        assert result.refusal_reasons == ()
        q12 = _q12_of(result)
        assert q12.answer == fa.ANSWER_PASS and q12.finding is None


# =============================================================================
# 7. Case C — the human-supplemented proof (explicit task, full audit trail).
# =============================================================================
@needs_real_capture
class TestHumanSupplementedProof:
    def test_requested_task_exists_for_every_candidate(self, supplemented_journey):
        for group in supplemented_journey.built.connection_tasks:
            material_tasks = [t for t in group.tasks
                              if t.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION]
            assert len(material_tasks) == 1
            assert material_tasks[0].answer_type == ANSWER_MATERIAL_VALUE
            assert material_tasks[0] is group.tasks[-1]  # appended last, ids never moved

    def test_supplied_material_reaches_the_drawing_with_human_provenance(
            self, supplemented_journey):
        journey = supplemented_journey
        assert journey.workflow.revision == 3
        pdfs = _pdfs(journey.tmp)
        assert len(pdfs) == 3
        for path in pdfs:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            assert HUMAN_SUPPLIED_MATERIAL in text
            assert "NOT SPECIFIED" not in text

    def test_full_audit_trail_from_task_to_ai_observation(self, supplemented_journey):
        acceptance = supplemented_journey.acceptance
        assert acceptance.status == ACCEPTANCE_STATUS_ACCEPTED
        for package_id in CANDIDATE_IDS:
            conn = _conn(acceptance, package_id)
            assert conn.accepted is True
            decisions = [d for d in conn.trace.human_decisions
                         if d.task_type == TASK_PROVIDE_MATERIAL_SPECIFICATION]
            assert len(decisions) == 1
            decision = decisions[0]
            assert decision.applied is True and decision.refusal_reason is None
            assert decision.answer == HUMAN_SUPPLIED_MATERIAL
            assert decision.fields == ("material",)
            assert decision.field_provenance == (("material", PROVENANCE_HUMAN_SUPPLEMENTED),)
            # The AI genuinely never observed a grade: the contract says so.
            assert conn.trace.contract.ai_material is None
            assert any(
                e.field == "material" and e.provenance == PROVENANCE_HUMAN_SUPPLEMENTED
                for e in conn.trace.contract.provenance
            )

    def test_7ar_audit_and_7as_clear_the_finding(self, supplemented_journey):
        package = supplemented_journey.package
        assert package.status == PACKAGE_STATUS_READY
        for item in package.items:
            audit = dict(item.audit)
            # Honest absence: the AI observation has no material key at all.
            assert "material" not in audit["ai_observations"]
            assert ["material", PROVENANCE_HUMAN_SUPPLEMENTED] in audit["field_provenance"]
            material_decisions = [
                d for d in audit["human_decisions"]
                if d["task_type"] == TASK_PROVIDE_MATERIAL_SPECIFICATION
            ]
            assert material_decisions and material_decisions[0]["applied"] is True
            assert material_decisions[0]["answer"] == HUMAN_SUPPLIED_MATERIAL
            # The packaged bytes hash to their recorded 7AG verification hash.
            assert item.recorded_sha256 == hashlib.sha256(item.source_bytes).hexdigest()
        result = supplemented_journey.result
        assert result.status == fa.ACCEPTANCE_STATUS_ACCEPTED
        for index in range(3):
            q12 = _q12_of(result, index)
            assert q12.answer == fa.ANSWER_PASS and q12.finding is None


# =============================================================================
# 8. Tamper suite: PDF-only changes and records without source support.
# =============================================================================
class TestTamper:
    def test_pdf_only_change_is_refused_by_7as(self, positive_journey, tmp_path):
        copied = _copied_package(positive_journey.package, tmp_path)
        item = copied.items[0]
        target = Path(copied.manifest_path).parent / "drawings" / item.filename
        _write_pdf(target, ["SteelSpec tampered drawing", "MATERIAL: 250PLUS"])
        result = fa.evaluate_fabricator_acceptance(
            copied, acceptance_answers=_answers(copied, q12=fa.ANSWER_PASS))
        assert result.status == fa.ACCEPTANCE_STATUS_REFUSED
        assert "artifact hash" in result.reason

    def test_material_inserted_into_a_record_without_source_support_is_rejected(self):
        # A material value whose record shows no provenance label never passes
        # 7V's authority rule — a record-only change cannot smuggle a grade in.
        spec = make_synthetic_test_spec(material="300PLUS")  # inserted, unlabelled
        result = check_reviewed_connection_specification_completeness(spec)
        assert result.error is not None and "material" in result.error


# =============================================================================
# 9. Repeatability and purity.
# =============================================================================
class TestRepeatabilityAndPurity:
    def test_positive_journey_is_repeatable_byte_identical(self, positive_journey, tmp_path):
        # Two INDEPENDENT generations of the same positive run are identical:
        # same filename, same extracted drawing text, and byte-identical apart
        # from reportlab's per-save metadata (/ID and the creation/modification
        # dates) — the drawing content itself is fully deterministic. (7AJ's
        # byte-identical proof covered artifacts never rewritten across
        # revisions; this is the two-independent-generations statement.)
        _, _, built, workflow = _start_positive()
        workflow = workflow_module.resolve_project_connection(
            workflow, package_id="RP-0001",
            resolutions=_positive_resolutions(built), output_dir=tmp_path,
        )
        first_path = Path(positive_journey.workflow.connections[0].generated_files[0])
        second_path = Path(workflow.connections[0].generated_files[0])
        assert first_path.name == second_path.name
        first_bytes = first_path.read_bytes()
        second_bytes = second_path.read_bytes()
        assert _pdf_text_from_bytes(first_bytes) == _pdf_text_from_bytes(second_bytes)
        text = _pdf_text_from_bytes(first_bytes)
        assert POSITIVE_MATERIAL in text and "NOT SPECIFIED" not in text
        assert _strip_per_save_metadata(first_bytes) == _strip_per_save_metadata(second_bytes)

    def test_no_material_standards_database_or_second_store_exists(self):
        chain_modules = [
            "app/cad_engine/connection_review_package.py",
            "app/cad_engine/reviewed_connection_specification.py",
            "app/cad_engine/exception_resolution.py",
            "app/cad_engine/resolution_rerun.py",
            "app/cad_engine/project_workflow.py",
            "app/cad_engine/production_acceptance.py",
            "app/cad_engine/fabrication_package.py",
            "app/drawing_generator/pdf_builder.py",
        ]
        for module_path in chain_modules:
            source = Path(module_path).read_text()
            assert "VALID_MATERIALS" not in source, module_path
        for app_file in Path("app").rglob("*.py"):
            source = app_file.read_text()
            assert "material_store" not in source
            assert "materials.json" not in source
        # One authoritative field, appended to the engineering fields — never
        # hidden inside sections, marks, notes or descriptions.
        assert ENGINEERING_FIELDS[-1] == "material"
        assert "material" not in {
            "position", "plate", "holes", "location", "attachments", "connected_member_marks",
        }


# =============================================================================
# 10. The review UI's minimal additive change (§30).
# =============================================================================
@needs_real_capture
class TestReviewUI:
    def test_material_task_renders_and_submits(self):
        intake, _, built = _workflow_built_with_material_request()
        workflow = workflow_module.start_project_workflow(
            intake.collection,
            intake=intake,
            member_rows=HUMAN_SUPPLIED_MEMBER_ROWS,
            member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
            section_matcher=HUMAN_SUPPLIED_SECTION_MATCHER,
            request_material_specification=True,
        )
        project_view = render_project_view(build_project_review_contract(workflow))
        contract = build_connection_review_contract(workflow, "RP-0001")
        connection_view = render_connection_view(contract)
        assert unsupported_tasks(connection_view) == ()  # the material answer is supported
        material_task = next(
            t for t in connection_view.tasks if t.answer_type == ANSWER_MATERIAL_VALUE)
        names = task_input_names(material_task)
        assert names == (f"task_{material_task.task_id}",)
        page = render_module.render_connection_detail_page(project_view, connection_view)
        assert names[0] in page and "Material" in page
        # The submission layer parses the reviewer's text verbatim into the
        # answer the workflow's own validation accepts.
        assert parse_task_answer(material_task, {names[0]: "AS/NZS 3678-300"}) == \
            "AS/NZS 3678-300"
        with pytest.raises(ResolutionInputError):
            parse_task_answer(material_task, {names[0]: "   "})
