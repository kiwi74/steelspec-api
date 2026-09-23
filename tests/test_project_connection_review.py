"""
Milestone 7X — tests for the project-level connection review queue
(app.cad_engine.project_connection_review).

FIXTURE PROVENANCE (read before trusting any value below): every connection
here is a SYNTHETIC, AI-SHAPED TEST object using the keys of the real AI
schema (detail_reference, grid_reference, connects_members, connection_type,
bolts, plates, welds, confidence, plus the pipeline-injected page_num). No AI
produced them and no drawing contains them. Three candidates (X, Y, Z) are
deliberately different in member marks, source pages, detail/grid references,
plate dimensions, bolt sizes and reviewed hole patterns, so no test depends
on one fixture. The reviewer supplements are SYNTHETIC / TEST-ONLY decisions
("approved" is the review-WORKFLOW status 7V requires, not engineering
approval); no real reviewer made them and nothing here proves any value is
correct engineering.
"""
import ast
import copy
import dataclasses
import inspect
from pathlib import Path

import pypdf
import pytest

import app.cad_engine.project_connection_review as project_module
from app.cad_engine.assembly import PlacedMember
from app.cad_engine.connection_review_package import (
    REVIEW_STATE_AI_EXTRACTED,
    REVIEW_STATE_READY_FOR_PIPELINE,
    REVIEW_STATE_REVIEW_REQUIRED,
    ConnectionReviewSupplement,
    build_reviewed_connection_specification,
)
from app.cad_engine.interface import generate_geometry
from app.cad_engine.placement import place_member_geometry
from app.cad_engine.project_connection_review import (
    PROJECT_REVIEW_STATES,
    SIGNAL_IDENTICAL_AI_EXTRACTION_CONTENT,
    SIGNAL_SAME_DETAIL_REFERENCE,
    SIGNAL_SAME_GRID_AND_MEMBERS,
    STATE_BLOCKED,
    SUMMARY_SCOPE_STATEMENT,
    build_project_review_summary,
    create_project_connection_collection,
    find_possible_duplicates,
    ordered_candidates,
    ready_reviewed_connections,
    update_candidate_supplement,
    with_known_member_marks,
)
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from app.cad_engine.reviewed_connection_assembly import build_reviewed_two_member_connection_assembly
from app.cad_engine.reviewed_connection_drawing_gate import generate_fabrication_drawing_from_reviewed_assembly
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests.test_connection_attachment import make_member_a_placement, make_member_b_placement
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher

KNOWN = ["A", "B", "C", "D", "L2", "L3", "REAL-UB-CAD-001", "C1", "C2"]

HOLES_4 = {"quantity": 4, "diameter_mm": 22.0, "vertical_spacing_mm": 140.0, "horizontal_spacing_mm": 90.0}
HOLES_2 = {"quantity": 2, "diameter_mm": 20.0, "vertical_spacing_mm": 100.0}
HOLES_6 = {"quantity": 4, "diameter_mm": 26.0, "vertical_spacing_mm": 180.0, "horizontal_spacing_mm": 110.0}


def make_raw(marks, page, detail, grid, thickness=12, width=180, depth=250, bolt="M20", quantity=4, **overrides):
    raw = {
        "detail_reference": detail, "grid_reference": grid, "connects_members": list(marks),
        "connection_type": "bolted",
        "bolts": [{"quantity": quantity, "size": bolt, "grade": "8.8"}],
        "plates": [{"type": "end_plate", "thickness_mm": thickness, "width_mm": width, "depth_mm": depth}],
        "confidence": 90, "page_num": page,
    }
    raw.update(overrides)
    return raw


def raw_x(**kw):  # members A,B  page 3
    return make_raw(["A", "B"], 3, "D1", "A/1", **kw)


def raw_y(**kw):  # members C,D  page 7, different plate and bolts
    return make_raw(["C", "D"], 7, "D2", "C/4", thickness=16, width=160, depth=220, bolt="M16", quantity=2, **kw)


def raw_z(**kw):  # members L2,L3  page 9, different again
    return make_raw(["L2", "L3"], 9, "D3", "E/2", thickness=20, width=200, depth=300, bolt="M24", quantity=6, **kw)


def reviewer(marks, connection_id, holes=HOLES_4, first_surface="END", second_surface="START", **overrides):
    """A SYNTHETIC full reviewer decision set for a candidate whose AI marks and plate are correct."""
    base = dict(
        connection_id=connection_id, review_status="approved", position="END", holes=copy.deepcopy(holes),
        location={"x": 0.0, "y": 0.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0},
        attachments=[{"member_mark": marks[0], "surface_reference": first_surface},
                     {"member_mark": marks[1], "surface_reference": second_surface}],
        confirmed_ai_fields=frozenset({"connected_member_marks", "plate"}),
    )
    base.update(overrides)
    return ConnectionReviewSupplement(**base)


def collection(raws, known=KNOWN, **kw):
    return create_project_connection_collection(raws, known_member_marks=known, **kw)


def row(summary, review_package_id):
    return next(r for r in summary.rows if r.review_package_id == review_package_id)


def state_of(col, review_package_id):
    return row(build_project_review_summary(col), review_package_id).state


# =============================================================================
# Deliverable 1/9/10 — the collection, source identity, traceability, scope
# =============================================================================
def test_empty_project_yields_an_empty_summary_that_claims_nothing_about_the_project():
    summary = build_project_review_summary(create_project_connection_collection())
    assert summary.total_candidates == 0 and summary.rows == () and summary.duplicates == ()
    assert summary.state_counts == {state: 0 for state in PROJECT_REVIEW_STATES}
    assert summary.ready_for_7v == summary.blocked == summary.missing_information == 0
    assert ready_reviewed_connections(create_project_connection_collection()) == ()
    assert summary.scope_statement == SUMMARY_SCOPE_STATEMENT


def test_the_summary_never_claims_completeness_or_approval():
    names = {f.name for f in dataclasses.fields(build_project_review_summary(collection([raw_x()])))}
    assert not [n for n in names if any(w in n for w in ("complete", "approved", "all_found", "interpreted"))]
    for phrase in ("does not claim", "every connection", "connection-complete", "fully interpreted",
                   "not engineering approval"):
        assert phrase in SUMMARY_SCOPE_STATEMENT, phrase
    assert not [n for n in dir(project_module) if "APPROVED" in n.upper() or "FABRICATION_READY" in n.upper()]


def test_one_ai_connection_is_preserved_with_full_source_traceability():
    raw = raw_x(welds=[{"type": "fillet", "size_mm": 8}])
    col = create_project_connection_collection(
        [raw], project_id="PROJECT-T", source_drawing_id="DRAWING-T", drawing_number="S101", known_member_marks=KNOWN)
    summary = build_project_review_summary(col)
    r = summary.rows[0]
    ex = col.candidates[0].extraction

    assert summary.total_candidates == 1 and r.review_package_id == "RP-0001"
    assert (r.source_page, r.detail_reference, r.grid_reference) == (3, "D1", "A/1")
    assert r.extracted_member_references == ("A", "B") and r.generic_connection_type == "bolted"
    assert r.confidence == 90
    assert (ex.project_id, ex.source_drawing_id, ex.drawing_number) == ("PROJECT-T", "DRAWING-T", "S101")
    assert ex.bolts[0].size == "M20" and ex.plates[0].thickness_mm == 12 and ex.welds[0].size_mm == 8
    assert r.state == REVIEW_STATE_AI_EXTRACTED and r.report.ready_for_pipeline is False


def test_no_connection_identity_is_ever_invented_and_the_two_identities_stay_separate():
    col = collection([raw_x(), raw_y()])
    assert [c.source_identity for c in col.candidates] == [None, None]          # the AI supplies none
    assert [c.review_package_id for c in col.candidates] == ["RP-0001", "RP-0002"]
    assert all(c.review_package_id != c.source_identity for c in col.candidates)

    supplied = collection([raw_x(), raw_y()], source_identities=["DB-77", None])
    assert [c.source_identity for c in supplied.candidates] == ["DB-77", None]
    assert supplied.candidates[0].review_package_id == "RP-0001"                 # unaffected by the source id


def test_source_identities_must_align_one_to_one_and_are_never_guessed():
    with pytest.raises(ValueError, match="align one-to-one"):
        collection([raw_x(), raw_y()], source_identities=["only-one"])


def test_the_original_ai_extraction_is_preserved_and_the_raw_objects_are_not_mutated():
    raws = [raw_x(), raw_y()]
    before = copy.deepcopy(raws)
    col = collection(raws)
    assert raws == before
    raws[0]["bolts"][0]["size"] = "M99"
    assert col.candidates[0].extraction.bolts[0].size == "M20"


def test_a_candidate_has_no_settable_state_it_is_always_derived():
    assert not {"state", "status", "approved"} & {f.name for f in dataclasses.fields(project_module.ConnectionCandidate)}
    params = set(inspect.signature(create_project_connection_collection).parameters)
    assert not {"state", "status", "approved", "review_status"} & params


@pytest.mark.parametrize("make_raws", [lambda: [raw_x()], lambda: [raw_x(), raw_y(), raw_z()]])
def test_candidates_are_kept_independent(make_raws):
    col = collection(make_raws())
    assert len(col.candidates) == len(make_raws())
    assert len({id(c.package) for c in col.candidates}) == len(col.candidates)


# =============================================================================
# Deliverable 2 — deterministic presentation order that carries no engineering meaning
# =============================================================================
def test_presentation_order_uses_source_page_detail_and_grid_with_missing_values_last():
    raws = [
        make_raw(["A", "B"], None, "D0", "g"),
        make_raw(["A", "B"], 2, None, "g"),
        make_raw(["A", "B"], 2, "D5", "g"),
        make_raw(["A", "B"], 1, "Z9", "g"),
        make_raw(["A", "B"], 2, "D1", "g"),
    ]
    order = [(c.extraction.source_page, c.extraction.detail_reference) for c in ordered_candidates(collection(raws))]
    assert order == [(1, "Z9"), (2, "D1"), (2, "D5"), (2, None), (None, "D0")]


def test_ties_are_broken_by_submission_order_and_ordering_is_repeatable():
    col = collection([raw_x(), raw_x(), raw_x()])  # identical source facts
    assert [c.review_package_id for c in ordered_candidates(col)] == ["RP-0001", "RP-0002", "RP-0003"]
    assert build_project_review_summary(col) == build_project_review_summary(col)


def test_sorting_never_changes_which_id_belongs_to_which_candidate():
    col = collection([raw_z(), raw_x(), raw_y()])
    assert [(c.review_package_id, c.extraction.source_page) for c in col.candidates] == \
        [("RP-0001", 9), ("RP-0002", 3), ("RP-0003", 7)]                      # submission order = ids
    assert [(c.review_package_id, c.extraction.source_page) for c in ordered_candidates(col)] == \
        [("RP-0002", 3), ("RP-0003", 7), ("RP-0001", 9)]                      # same bindings, presentation order


def _by_page(summary):
    return {r.source_page: r for r in summary.rows}


def test_collection_order_never_affects_engineering_semantics():
    """The same candidates and reviewer decisions, submitted in opposite orders, mean exactly the same thing."""
    decisions = {3: reviewer(["A", "B"], "CONN-X"), 7: reviewer(["C", "D"], "CONN-Y", HOLES_2, "START", "END")}

    def build(raws):
        col = collection(raws)
        for c in col.candidates:
            page = c.extraction.source_page
            if page in decisions:
                col = update_candidate_supplement(col, c.review_package_id, decisions[page])
        return col

    forward, backward = build([raw_x(), raw_y(), raw_z()]), build([raw_z(), raw_y(), raw_x()])
    rows_f, rows_b = _by_page(build_project_review_summary(forward)), _by_page(build_project_review_summary(backward))
    assert rows_f.keys() == rows_b.keys() == {3, 7, 9}
    for page in (3, 7, 9):
        assert rows_f[page].report == rows_b[page].report
        assert rows_f[page].state == rows_b[page].state
    spec_f = {r.specification.connection_id: r.specification for r in ready_reviewed_connections(forward)}
    spec_b = {r.specification.connection_id: r.specification for r in ready_reviewed_connections(backward)}
    assert spec_f == spec_b and set(spec_f) == {"CONN-X", "CONN-Y"}


def test_connection_order_never_determines_start_end_attachment_or_location():
    unreviewed = collection([raw_x(), raw_y(), raw_z()])
    for c in unreviewed.candidates:  # first, middle and last position alike
        spec = build_reviewed_connection_specification(c.package)
        assert spec.position is None and spec.attachments == [] and spec.location is None and spec.holes is None

    # ...and a reviewer's explicit, unusual choice is honoured exactly, independent of where the candidate sits.
    col = collection([raw_x(), raw_y()])
    col = update_candidate_supplement(col, "RP-0002", reviewer(["C", "D"], "CONN-Y", HOLES_2, "START", "END"))
    spec = ready_reviewed_connections(col)[0].specification
    assert [(a["member_mark"], a["surface_reference"]) for a in spec.attachments] == [("C", "START"), ("D", "END")]
    assert spec.position == "END" and spec.location["z"] == 3994.0
    assert build_reviewed_connection_specification(col.candidates[0].package).attachments == []  # the other: untouched


# =============================================================================
# Deliverable 3 — possible duplicates are reported, never merged
# =============================================================================
def test_duplicate_looking_candidates_are_both_kept_and_reported_never_merged():
    col = collection([raw_x(), raw_x()])
    pairs = find_possible_duplicates(col)
    assert len(pairs) == 1 and (pairs[0].first_review_package_id, pairs[0].second_review_package_id) == \
        ("RP-0001", "RP-0002")
    assert set(pairs[0].signals) == {SIGNAL_SAME_DETAIL_REFERENCE, SIGNAL_SAME_GRID_AND_MEMBERS,
                                     SIGNAL_IDENTICAL_AI_EXTRACTION_CONTENT}
    summary = build_project_review_summary(col)
    assert summary.total_candidates == 2 and summary.possible_duplicate_pairs == 1
    assert row(summary, "RP-0001").possible_duplicate_of == ("RP-0002",)
    assert row(summary, "RP-0002").possible_duplicate_of == ("RP-0001",)


def test_the_same_detail_on_multiple_pages_is_reported_as_a_possible_duplicate_only():
    # Same detail reference on two pages, but different grid, members and plates: only the weak detail signal.
    col = collection([make_raw(["A", "B"], 3, "D15", "A/1"), make_raw(["C", "D"], 11, "D15", "C/4", thickness=16)])
    (pair,) = find_possible_duplicates(col)
    assert pair.signals == (SIGNAL_SAME_DETAIL_REFERENCE,)
    assert build_project_review_summary(col).total_candidates == 2


def test_the_same_reading_on_different_pages_is_not_identical_extraction_content():
    """Source information is part of the preserved extraction, so a repeat on another page is not 'identical'."""
    col = collection([make_raw(["A", "B"], 3, None, None), make_raw(["A", "B"], 12, None, None)])
    assert find_possible_duplicates(col) == ()   # no detail/grid reference either, so no weaker signal fires
    # ...but with a detail reference the weaker, honestly-named signal still reports the repeat.
    col = collection([make_raw(["A", "B"], 3, "D15", None), make_raw(["A", "B"], 12, "D15", None)])
    (pair,) = find_possible_duplicates(col)
    assert pair.signals == (SIGNAL_SAME_DETAIL_REFERENCE,)


def test_blank_references_and_empty_shells_never_match_each_other():
    col = collection([make_raw(["A", "B"], 1, None, None), make_raw(["C", "D"], 2, "", "  "), {}, {}])
    assert find_possible_duplicates(col) == ()


def test_materially_different_candidates_are_not_flagged():
    assert find_possible_duplicates(collection([raw_x(), raw_y(), raw_z()])) == ()


def test_a_duplicate_warning_alters_no_engineering_data_state_or_specification():
    alone = collection([raw_x()])
    alone = update_candidate_supplement(alone, "RP-0001", reviewer(["A", "B"], "CONN-X"))
    with_twin = collection([raw_x(), raw_x()])
    with_twin = update_candidate_supplement(with_twin, "RP-0001", reviewer(["A", "B"], "CONN-X"))
    with_twin = update_candidate_supplement(with_twin, "RP-0002", reviewer(["A", "B"], "CONN-X-TWIN"))

    a, b = build_project_review_summary(alone), build_project_review_summary(with_twin)
    assert row(b, "RP-0001").report == row(a, "RP-0001").report
    assert row(b, "RP-0001").state == row(a, "RP-0001").state == REVIEW_STATE_READY_FOR_PIPELINE
    assert row(b, "RP-0002").state == REVIEW_STATE_READY_FOR_PIPELINE      # a warning does not block or drop it
    assert row(b, "RP-0001").possible_duplicate_of == ("RP-0002",)
    assert [r.review_package_id for r in ready_reviewed_connections(with_twin)] == ["RP-0001", "RP-0002"]


def test_two_candidates_given_the_same_connection_id_cannot_both_proceed():
    col = collection([raw_x(), raw_y()])
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-SAME"))
    col = update_candidate_supplement(col, "RP-0002", reviewer(["C", "D"], "CONN-SAME", HOLES_2))
    summary = build_project_review_summary(col)
    for pid in ("RP-0001", "RP-0002"):
        assert row(summary, pid).state == STATE_BLOCKED
        assert [i.code for i in row(summary, pid).blocking_issues] == ["DUPLICATE_CONNECTION_ID"]
    assert ready_reviewed_connections(col) == () and summary.ready_for_7v == 0


# =============================================================================
# Deliverable 4/5 — states and the summary, all derived
# =============================================================================
def test_unreviewed_candidates_are_ai_extracted_and_a_reviewed_one_becomes_ready():
    col = collection([raw_x(), raw_y()])
    assert {r.state for r in build_project_review_summary(col).rows} == {REVIEW_STATE_AI_EXTRACTED}
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-X"))
    summary = build_project_review_summary(col)
    assert row(summary, "RP-0001").state == REVIEW_STATE_READY_FOR_PIPELINE
    assert row(summary, "RP-0002").state == REVIEW_STATE_AI_EXTRACTED


def test_partial_review_is_review_required_not_blocked():
    col = update_candidate_supplement(collection([raw_x()]), "RP-0001", ConnectionReviewSupplement(position="END"))
    r = build_project_review_summary(col).rows[0]
    assert r.state == REVIEW_STATE_REVIEW_REQUIRED and r.blocking_issues == ()


def test_state_counts_partition_the_candidates_and_are_derived_from_the_review_facts():
    col = collection([raw_x(), raw_y(), raw_z(), {}])
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-X"))                  # READY
    col = update_candidate_supplement(col, "RP-0002", ConnectionReviewSupplement(position="END"))      # REVIEW_REQUIRED
    col = update_candidate_supplement(col, "RP-0003",
                                      reviewer(["L2", "GHOST"], "CONN-Z", confirmed_ai_fields=frozenset({"plate"}),
                                               connected_member_marks=["L2", "GHOST"]))                 # BLOCKED
    summary = build_project_review_summary(col)
    assert summary.state_counts == {REVIEW_STATE_AI_EXTRACTED: 1, REVIEW_STATE_REVIEW_REQUIRED: 1,
                                    REVIEW_STATE_READY_FOR_PIPELINE: 1, STATE_BLOCKED: 1}
    assert sum(summary.state_counts.values()) == summary.total_candidates == 4
    assert (summary.ready_for_7v, summary.blocked) == (1, 1)


def test_missing_information_reflects_the_downstream_gaps_of_each_candidate():
    # 7AT: a source that genuinely lacks a material grade leaves "material" as a
    # recorded downstream gap — informational, never blocking readiness.
    col = update_candidate_supplement(collection([raw_x(), raw_y()]), "RP-0001", reviewer(["A", "B"], "CONN-X"))
    summary = build_project_review_summary(col)
    assert row(summary, "RP-0001").report.missing == ("material",)
    assert row(summary, "RP-0002").report.missing == ("position", "holes", "location", "attachments", "material")
    assert summary.missing_information == 2


def test_not_reviewable_shells_are_counted_apart_from_reviewable_candidates():
    summary = build_project_review_summary(collection([raw_x(), {}, {"page_num": 4}]))
    assert summary.total_candidates == 3
    assert (summary.ready_for_human_review, summary.not_reviewable) == (1, 2)
    assert summary.state_counts[REVIEW_STATE_AI_EXTRACTED] == 3  # an empty shell is still a candidate, still unreviewed


def test_no_ai_connections_found_is_reported_as_zero_candidates_only():
    summary = build_project_review_summary(create_project_connection_collection([], known_member_marks=KNOWN))
    assert summary.total_candidates == 0 and summary.member_context_supplied is True
    assert "does not claim" in summary.scope_statement


def test_low_confidence_is_preserved_but_never_changes_state_or_readiness():
    low = build_project_review_summary(collection([raw_x(confidence=15)])).rows[0]
    high = build_project_review_summary(collection([raw_x(confidence=100)])).rows[0]
    assert (low.confidence, high.confidence) == (15, 100)
    assert low.state == high.state == REVIEW_STATE_AI_EXTRACTED and low.report.ready_for_pipeline is False
    reviewed = update_candidate_supplement(collection([raw_x(confidence=15)]), "RP-0001", reviewer(["A", "B"], "CONN-X"))
    assert build_project_review_summary(reviewed).rows[0].state == REVIEW_STATE_READY_FOR_PIPELINE


def test_missing_source_page_and_detail_reference_are_reported_and_do_not_block_review():
    col = collection([make_raw(["A", "B"], None, None, "A/1"), raw_y()])
    summary = build_project_review_summary(col)
    gapped = next(r for r in summary.rows if r.extracted_member_references == ("A", "B"))
    assert gapped.source_gaps == ("source_page", "detail_reference") and gapped.state == REVIEW_STATE_AI_EXTRACTED
    assert (summary.missing_source_page, summary.missing_detail_reference) == (1, 1)
    assert summary.rows[-1] is gapped  # missing values sort last


def test_multiple_provenance_states_are_counted_across_the_project():
    col = collection([raw_x(), raw_y(), {}])
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-X"))
    summary = build_project_review_summary(col)
    assert summary.provenance_counts == {
        PROVENANCE_AI_EXTRACTED: 2,          # Y's unreviewed members + plate
        PROVENANCE_HUMAN_REVIEWED: 2,        # X's confirmed members + plate
        PROVENANCE_HUMAN_SUPPLEMENTED: 4,    # X's position, holes, location, attachments
    }
    assert (summary.human_supplemented, summary.human_reviewed) == (1, 1)


# =============================================================================
# Deliverable 6 — project member context is 7W's, not re-implemented here
# =============================================================================
def test_known_member_marks_reach_every_7w_package_and_7w_stays_the_authority(monkeypatch):
    seen = []
    original = project_module.build_review_report
    monkeypatch.setattr(project_module, "build_review_report",
                        lambda package: seen.append(package.known_member_marks) or original(package))
    build_project_review_summary(collection([raw_x(), raw_y()], known=["A", "B", "C", "D"]))
    assert seen == [("A", "B", "C", "D")] * 2
    assert not hasattr(project_module, "_unknown_marks")  # no second copy of the member-validation rule


def test_an_unknown_ai_member_is_a_current_blocking_project_member_issue():
    summary = build_project_review_summary(collection([make_raw(["A", "GHOST"], 3, "D1", "A/1"), raw_y()]))
    r = next(r for r in summary.rows if r.source_page == 3)
    assert r.state == STATE_BLOCKED and r.report.current_unknown_member_marks == ("GHOST",)
    assert summary.current_project_member_issues == 1 and summary.blocked == 1


def test_an_unknown_reviewer_supplied_member_is_blocked_too():
    col = update_candidate_supplement(collection([raw_x()]), "RP-0001", reviewer(
        ["A", "GHOST"], "CONN-X", connected_member_marks=["A", "GHOST"], confirmed_ai_fields=frozenset({"plate"})))
    r = build_project_review_summary(col).rows[0]
    assert r.state == STATE_BLOCKED and r.report.current_unknown_member_marks == ("GHOST",)
    assert ready_reviewed_connections(col) == ()


def test_an_ai_unknown_mark_corrected_by_the_reviewer_is_ready_and_stays_traceable():
    col = collection([make_raw(["A", "GHOST"], 3, "D1", "A/1")])
    col = update_candidate_supplement(col, "RP-0001", reviewer(
        ["A", "B"], "CONN-X", connected_member_marks=["A", "B"], confirmed_ai_fields=frozenset({"plate"})))
    summary = build_project_review_summary(col)
    r = summary.rows[0]
    assert r.state == REVIEW_STATE_READY_FOR_PIPELINE and r.blocking_issues == ()
    assert summary.current_project_member_issues == 0 and summary.blocked == 0
    # History survives, in the row and in the untouched extraction.
    assert r.extracted_member_references == ("A", "GHOST")
    assert r.report.unknown_member_marks == ("GHOST",) and r.report.current_unknown_member_marks == ()
    assert "AI_UNKNOWN_MEMBER_CORRECTED" in {i.code for i in r.report.issues}
    assert col.candidates[0].extraction.connected_member_references == ("A", "GHOST")
    # ...but only the reviewer's final value is passed downstream.
    (ready,) = ready_reviewed_connections(col)
    assert ready.specification.connected_member_marks == ["A", "B"]
    assert "GHOST" not in repr(ready.specification) and "GHOST" not in repr(ready.completeness)


def test_missing_known_member_context_is_reported_not_guessed():
    col = collection([make_raw(["A", "GHOST"], 3, "D1", "A/1")], known=None)
    summary = build_project_review_summary(col)
    assert summary.member_context_supplied is False and summary.member_references_unchecked == 1
    assert summary.rows[0].report.current_unknown_member_marks == () and summary.rows[0].state == REVIEW_STATE_AI_EXTRACTED


def test_supplying_project_members_later_revalidates_without_mutating_the_original_collection():
    col = update_candidate_supplement(
        collection([raw_x()], known=None), "RP-0001",
        reviewer(["A", "GHOST"], "CONN-X", connected_member_marks=["A", "GHOST"], confirmed_ai_fields=frozenset({"plate"})))
    assert state_of(col, "RP-0001") == REVIEW_STATE_READY_FOR_PIPELINE       # nothing to check against yet
    checked = with_known_member_marks(col, ["A", "B"])
    assert state_of(checked, "RP-0001") == STATE_BLOCKED and checked.known_member_marks == ("A", "B")
    assert col.known_member_marks is None and state_of(col, "RP-0001") == REVIEW_STATE_READY_FOR_PIPELINE


# =============================================================================
# Deliverable 7/10 — human corrections touch exactly one candidate
# =============================================================================
def test_correcting_one_connection_does_not_change_an_unrelated_one():
    col = collection([make_raw(["A", "GHOST"], 3, "D1", "A/1"), make_raw(["C", "D"], 7, "D2", "C/4", thickness=16)])
    before = build_project_review_summary(col)
    corrected = update_candidate_supplement(col, "RP-0001", reviewer(
        ["A", "B"], "CONN-A", connected_member_marks=["A", "B"], confirmed_ai_fields=frozenset({"plate"})))
    after = build_project_review_summary(corrected)

    assert row(before, "RP-0001").state == STATE_BLOCKED and row(after, "RP-0001").state == REVIEW_STATE_READY_FOR_PIPELINE
    assert row(after, "RP-0002") == row(before, "RP-0002")           # the unrelated candidate is unchanged
    assert corrected.candidates[1] is col.candidates[1]              # literally the same object
    assert corrected.candidates[0].extraction is col.candidates[0].extraction  # the AI extraction is never replaced


def test_updating_one_of_three_candidates_leaves_the_other_two_unchanged_and_the_original_collection_intact():
    col = collection([raw_x(), raw_y(), raw_z()])
    snapshot = copy.deepcopy(col)
    updated = update_candidate_supplement(col, "RP-0002", reviewer(["C", "D"], "CONN-Y", HOLES_2))
    assert col == snapshot                                                        # the original is untouched
    assert updated.candidates[0] is col.candidates[0] and updated.candidates[2] is col.candidates[2]
    summary = build_project_review_summary(updated)
    assert [row(summary, p).state for p in ("RP-0001", "RP-0002", "RP-0003")] == \
        [REVIEW_STATE_AI_EXTRACTED, REVIEW_STATE_READY_FOR_PIPELINE, REVIEW_STATE_AI_EXTRACTED]


def test_a_reviewers_later_edits_to_their_own_dicts_cannot_change_the_collection():
    supplement = reviewer(["A", "B"], "CONN-X")
    col = update_candidate_supplement(collection([raw_x()]), "RP-0001", supplement)
    supplement.holes["diameter_mm"] = 999.0
    supplement.location["z"] = -1.0
    (ready,) = ready_reviewed_connections(col)
    assert ready.specification.holes["diameter_mm"] == 22.0 and ready.specification.location["z"] == 3994.0


def test_an_unknown_review_package_id_is_a_programming_error():
    with pytest.raises(ValueError, match="RP-9999"):
        update_candidate_supplement(collection([raw_x()]), "RP-9999", reviewer(["A", "B"], "C"))


def test_a_correction_is_evidence_added_beside_the_ai_extraction_never_over_it():
    col = update_candidate_supplement(collection([raw_x()]), "RP-0001", reviewer(
        ["A", "B"], "CONN-X", plate={"type": "end_plate", "thickness_mm": 99, "width_mm": 99, "depth_mm": 99},
        confirmed_ai_fields=frozenset({"connected_member_marks"})))
    ex = col.candidates[0].extraction
    assert ex.plates[0].thickness_mm == 12 and ex.plates[0].width_mm == 180      # the AI's plate, as extracted
    (ready,) = ready_reviewed_connections(col)
    assert ready.specification.plate["thickness_mm"] == 99                        # the reviewer's, in the spec only
    assert ready.specification.provenance["plate"] == PROVENANCE_HUMAN_SUPPLEMENTED


# =============================================================================
# Deliverable 8 — the ready subset, and no CAD inside 7X
# =============================================================================
def test_one_ready_and_one_blocked_connection_yield_a_ready_subset_of_exactly_one():
    col = collection([raw_x(), make_raw(["C", "GHOST"], 7, "D2", "C/4", thickness=16)])
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-X"))
    col = update_candidate_supplement(col, "RP-0002", reviewer(
        ["C", "D"], "CONN-Y", HOLES_2, confirmed_ai_fields=frozenset({"plate", "connected_member_marks"})))
    summary = build_project_review_summary(col)
    assert (row(summary, "RP-0001").state, row(summary, "RP-0002").state) == \
        (REVIEW_STATE_READY_FOR_PIPELINE, STATE_BLOCKED)
    assert [r.review_package_id for r in ready_reviewed_connections(col)] == ["RP-0001"]


def test_the_ready_subset_contains_only_genuinely_7v_ready_specifications():
    col = collection([raw_x(), raw_y(), raw_z(), {}])
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-X"))                    # ready
    col = update_candidate_supplement(col, "RP-0002", reviewer(["C", "D"], "CONN-Y", HOLES_2,
                                                               review_status="pending_review"))         # not approved
    col = update_candidate_supplement(col, "RP-0003", reviewer(["L2", "L3"], "CONN-Z", HOLES_6, position=None))  # gap
    ready = ready_reviewed_connections(col)
    assert [r.review_package_id for r in ready] == ["RP-0001"]
    for item in ready:
        assert item.completeness.is_complete is True and item.completeness.error is None
        assert item.completeness.validated_connection is not None and item.completeness.attachments
        assert item.specification == build_reviewed_connection_specification(
            next(c for c in col.candidates if c.review_package_id == item.review_package_id).package)


@pytest.mark.parametrize("make, marks, holes, page, thickness", [
    (raw_x, ["A", "B"], HOLES_4, 3, 12),
    (raw_y, ["C", "D"], HOLES_2, 7, 16),
    (raw_z, ["L2", "L3"], HOLES_6, 9, 20),
])
def test_each_materially_different_candidate_is_ready_on_its_own_data(make, marks, holes, page, thickness):
    col = update_candidate_supplement(collection([make()]), "RP-0001", reviewer(marks, f"CONN-{page}", holes))
    (ready,) = ready_reviewed_connections(col)
    spec = ready.specification
    assert spec.connected_member_marks == marks and spec.holes == holes
    assert spec.plate["thickness_mm"] == thickness
    assert ready.completeness.validated_connection.connected_members == marks
    assert build_project_review_summary(col).rows[0].source_page == page


def test_the_ready_subset_is_deterministic_and_only_reviewer_final_values_are_passed_on():
    col = collection([make_raw(["A", "GHOST"], 3, "D1", "A/1"), raw_y()])
    col = update_candidate_supplement(col, "RP-0001", reviewer(
        ["A", "B"], "CONN-A", connected_member_marks=["A", "B"], confirmed_ai_fields=frozenset({"plate"})))
    col = update_candidate_supplement(col, "RP-0002", reviewer(["C", "D"], "CONN-Y", HOLES_2))
    first, second = ready_reviewed_connections(col), ready_reviewed_connections(col)
    assert first == second and [r.review_package_id for r in first] == ["RP-0001", "RP-0002"]
    assert "GHOST" not in repr(first)


def test_no_cad_is_generated_by_the_project_review_layer(monkeypatch):
    import app.cad_engine.reviewed_connection_assembly as assembly_module
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module
    import app.cad_engine.two_member_connection as two_member_module
    import app.drawing_generator.interface as drawing_interface
    import app.drawing_generator.pdf_builder as pdf_builder

    def forbidden(*args, **kwargs):
        raise AssertionError("7X must not generate CAD or drawings")

    for module, name in [
        (assembly_module, "build_reviewed_two_member_connection_assembly"),
        (two_member_module, "build_two_member_connection_assembly"),
        (gate_module, "generate_fabrication_drawing_from_reviewed_assembly"),
        (drawing_interface, "generate_connection_fabrication_drawing_pdf"),
        (pdf_builder, "build_connection_pdf"),
    ]:
        monkeypatch.setattr(module, name, forbidden)

    col = update_candidate_supplement(collection([raw_x(), raw_y()]), "RP-0001", reviewer(["A", "B"], "CONN-X"))
    build_project_review_summary(col)
    assert len(ready_reviewed_connections(col)) == 1


def test_the_module_imports_no_cad_assembly_validation_or_drawing_code():
    tree = ast.parse(Path(project_module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {
        "copy", "dataclasses", "collections.abc", "typing",
        "app.cad_engine.connection_review_package", "app.cad_engine.reviewed_connection_specification",
    }


# =============================================================================
# The ready subset enters the EXISTING 7V -> 7O -> 7R -> 7S/7T pipeline unchanged
# =============================================================================
_MEMBERS = {  # SYNTHETIC test members: mark -> (row factory, placement factory)
    "REAL-UB-CAD-001": (make_member_a_row, make_member_a_placement),
    "L2": (make_member_b_row, make_member_b_placement),
    "C1": (lambda: make_member_a_row(mark="C1"), make_member_a_placement),
    "C2": (lambda: make_member_b_row(mark="C2"), make_member_b_placement),
}


def _draw(ready, tmp_path, name):
    matcher = make_multi_member_matcher()
    placed = {}
    for mark in ready.completeness.validated_connection.connected_members:
        row_factory, placement_factory = _MEMBERS[mark]
        validated = real_member_to_validated_member(row_factory(), matcher)
        placement = placement_factory()
        placed[mark] = PlacedMember(mark, place_member_geometry(generate_geometry(validated), placement), placement)
    attachments = {a.member_mark: a for a in ready.completeness.attachments}
    marks = ready.completeness.validated_connection.connected_members
    assembly = build_reviewed_two_member_connection_assembly(
        placed[marks[0]], placed[marks[1]], ready.completeness.validated_connection,
        ready.completeness.connection_location, attachments[marks[0]], attachments[marks[1]],
    )
    return generate_fabrication_drawing_from_reviewed_assembly(assembly, tmp_path / name)


def test_two_materially_different_ready_connections_each_enter_the_existing_pipeline(tmp_path):
    p = make_raw(["REAL-UB-CAD-001", "L2"], 15, "D15", "A-B/2")                                   # 180x250x12, M20
    q = make_raw(["C1", "C2"], 3, "D3", "C/4", thickness=16, width=160, depth=220, bolt="M16", quantity=2)
    col = collection([p, q])
    col = update_candidate_supplement(col, "RP-0001", reviewer(["REAL-UB-CAD-001", "L2"], "CONN-P", HOLES_4))
    col = update_candidate_supplement(col, "RP-0002", reviewer(["C1", "C2"], "CONN-Q", HOLES_2))

    ready = {r.review_package_id: r for r in ready_reviewed_connections(col)}
    assert set(ready) == {"RP-0001", "RP-0002"}
    text_p = pypdf.PdfReader(_draw(ready["RP-0001"], tmp_path, "p.pdf")).pages[0].extract_text()
    text_q = pypdf.PdfReader(_draw(ready["RP-0002"], tmp_path, "q.pdf")).pages[0].extract_text()
    assert "PLATE: 180 × 250 × 12" in text_p and "HOLES: 4 × Ø22" in text_p and "REAL-UB-CAD-001" in text_p
    assert "PLATE: 160 × 220 × 16" in text_q and "HOLES: 2 × Ø20" in text_q and "C1" in text_q
    assert text_p != text_q


# =============================================================================
# IDENTICAL_AI_EXTRACTION_CONTENT compares the WHOLE preserved AI extraction (7X correction).
# It is generic (driven by dataclasses.fields), exact, extraction-only, and still a non-blocking warning.
# =============================================================================
from app.cad_engine.connection_review_package import AIExtractedConnection, ai_connection_to_extraction  # noqa: E402
from app.cad_engine.project_connection_review import ConnectionCandidate, ProjectConnectionReviewCollection  # noqa: E402

IDENTICAL = SIGNAL_IDENTICAL_AI_EXTRACTION_CONTENT


def signals(*raws, **kw):
    """The signals on the pair formed by the first two raws ((): no pair reported)."""
    pairs = find_possible_duplicates(collection(list(raws), **kw))
    return pairs[0].signals if pairs else ()


def combine(*collections):
    """One collection from candidates of several (used to vary collection-level source information)."""
    candidates = []
    for col in collections:
        for c in col.candidates:
            index = len(candidates)
            candidates.append(dataclasses.replace(c, review_package_id=f"RP-{index + 1:04d}", submission_index=index))
    return ProjectConnectionReviewCollection(tuple(candidates), collections[0].known_member_marks)


def test_confidence_difference_is_not_identical_extraction_content():
    pair = signals(raw_x(confidence=90), raw_x(confidence=5))
    assert IDENTICAL not in pair
    assert pair == (SIGNAL_SAME_DETAIL_REFERENCE, SIGNAL_SAME_GRID_AND_MEMBERS)  # the weaker signals still report it


def test_an_extra_preserved_ai_field_difference_is_not_identical_extraction_content():
    assert IDENTICAL not in signals(raw_x(), raw_x(surprise="only-in-the-second"))
    assert IDENTICAL not in signals(raw_x(surprise="only-in-the-first"), raw_x())


def test_the_reported_reproduction_confidence_and_extra_field_together():
    assert IDENTICAL not in signals(raw_x(confidence=90), raw_x(confidence=5, surprise="x"))


def test_genuinely_identical_extractions_still_receive_the_signal():
    a, b = raw_x(confidence=90, surprise="same", welds=[{"type": "fillet", "size_mm": 8}]), \
        raw_x(confidence=90, surprise="same", welds=[{"type": "fillet", "size_mm": 8}])
    assert a is not b and a == b                       # distinct objects: comparison is by value, never identity
    assert IDENTICAL in signals(a, b)


def test_the_same_extra_field_with_different_values_differs_and_with_equal_values_does_not():
    assert IDENTICAL in signals(raw_x(surprise="same"), raw_x(surprise="same"))
    assert IDENTICAL not in signals(raw_x(surprise="one"), raw_x(surprise="two"))
    assert IDENTICAL not in signals(raw_x(surprise={"k": 1}), raw_x(surprise={"k": 2}))
    assert IDENTICAL in signals(raw_x(surprise={"a": 1, "b": 2}), raw_x(surprise={"b": 2, "a": 1}))  # key order is not content


@pytest.mark.parametrize("field_name, changed", [
    ("detail_reference", "D9"), ("grid_reference", "Z/9"), ("connects_members", ["A", "C"]),
    ("connects_members", ["B", "A"]),                          # the order in which members were extracted
    ("connection_type", "welded"), ("confidence", 5), ("confidence", 90.0),   # 90 vs 90.0 as reported
    ("bolts", [{"quantity": 4, "size": "M24", "grade": "8.8"}]),
    ("bolts", [{"quantity": 4, "size": "M20", "grade": "8.8", "finish": "HDG"}]),   # an extra key inside a bolt
    ("bolts", [{"quantity": True, "size": "M20", "grade": "8.8"}]),           # True vs 4
    ("plates", [{"type": "end_plate", "thickness_mm": 16, "width_mm": 180, "depth_mm": 250}]),
    ("plates", [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250, "note": "x"}]),
    ("welds", [{"type": "fillet", "size_mm": 8}]),
    ("bolts", "not-a-list"),                                    # a malformed field, preserved raw
])
def test_every_preserved_field_takes_part_in_the_comparison(field_name, changed):
    base = raw_x(confidence=90)
    other = dict(base)
    other[field_name] = changed
    assert IDENTICAL not in signals(base, other), field_name


def test_identical_malformed_ai_fields_are_identical_but_different_ones_are_not():
    assert IDENTICAL in signals(raw_x(bolts="junk"), raw_x(bolts="junk"))
    assert IDENTICAL not in signals(raw_x(bolts="junk"), raw_x(bolts="other-junk"))
    assert IDENTICAL not in signals(raw_x(bolts="junk"), raw_x())


def test_source_information_is_part_of_the_comparison():
    same_page = signals(raw_x(), raw_x())
    other_page = signals(raw_x(), make_raw(["A", "B"], 12, "D1", "A/1"))
    assert IDENTICAL in same_page and IDENTICAL not in other_page
    for kwargs in ({"source_drawing_id": "DRAWING-2"}, {"drawing_number": "S102"}, {"project_id": "PROJECT-2"}):
        one = create_project_connection_collection([raw_x()], **{"source_drawing_id": "DRAWING-1",
                                                                 "drawing_number": "S101", "project_id": "PROJECT-1"})
        two = create_project_connection_collection([raw_x()], **{"source_drawing_id": "DRAWING-1",
                                                                 "drawing_number": "S101", "project_id": "PROJECT-1",
                                                                 **kwargs})
        (pair,) = find_possible_duplicates(combine(one, two))
        assert IDENTICAL not in pair.signals, kwargs


def test_the_comparison_is_generic_so_every_model_field_takes_part_and_none_can_go_stale():
    extraction = ai_connection_to_extraction(raw_x())
    canonical_fields = {name for name, _ in project_module._canonical(extraction)[1]}
    assert canonical_fields == {f.name for f in dataclasses.fields(AIExtractedConnection)}

    @dataclasses.dataclass(frozen=True)
    class FutureExtraction(AIExtractedConnection):
        future_field: str = "a"        # a field added to the model later: it must count automatically

    assert project_module._canonical(FutureExtraction(future_field="a")) == project_module._canonical(FutureExtraction(future_field="a"))
    assert project_module._canonical(FutureExtraction(future_field="a")) != project_module._canonical(FutureExtraction(future_field="b"))


def test_the_comparison_is_by_value_never_by_identity_and_is_deterministic():
    nan_a, nan_b = raw_x(confidence=float("nan")), raw_x(confidence=float("nan"))
    assert IDENTICAL in signals(nan_a, nan_b)                                    # equal by value even though nan != nan
    col = collection([raw_x(), raw_x()])
    assert find_possible_duplicates(col) == find_possible_duplicates(col)


def test_an_empty_shell_is_still_never_identical_content():
    assert signals({}, {}) == () and signals({"page_num": 4}, {"page_num": 4}) == ()


def test_human_corrections_never_change_the_historical_ai_comparison():
    col = collection([raw_x(), raw_x()])
    before = find_possible_duplicates(col)
    assert IDENTICAL in before[0].signals

    # Completely different reviewer decisions on the two candidates: still identical AI extraction.
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-1", HOLES_4))
    col = update_candidate_supplement(col, "RP-0002", reviewer(
        ["A", "B"], "CONN-2", HOLES_2, "START", "END", position="START",
        location={"x": 500.0, "y": 10.0, "z": 3994.0, "rotation_x": 0.0, "rotation_y": 0.0, "rotation_z": 0.0}))
    assert find_possible_duplicates(col) == before
    # ...even when the reviewer replaced the AI's own members/plate with different values.
    col = update_candidate_supplement(col, "RP-0002", reviewer(
        ["C", "D"], "CONN-2", connected_member_marks=["C", "D"],
        plate={"type": "end_plate", "thickness_mm": 30, "width_mm": 90, "depth_mm": 90},
        confirmed_ai_fields=frozenset()))
    assert find_possible_duplicates(col) == before
    assert col.candidates[0].extraction == col.candidates[1].extraction


def test_identical_human_decisions_cannot_make_different_ai_extractions_identical():
    col = collection([raw_x(confidence=90), raw_x(confidence=5)])
    same = reviewer(["A", "B"], "CONN-SAME")
    col = update_candidate_supplement(col, "RP-0001", same)
    col = update_candidate_supplement(col, "RP-0002", same)
    assert IDENTICAL not in find_possible_duplicates(col)[0].signals


def test_two_materially_different_candidates_and_a_true_copy_of_one():
    pairs = {(p.first_review_package_id, p.second_review_package_id): p.signals
             for p in find_possible_duplicates(collection([raw_x(), raw_y(), raw_y()]))}
    assert pairs == {("RP-0002", "RP-0003"): (SIGNAL_SAME_DETAIL_REFERENCE, SIGNAL_SAME_GRID_AND_MEMBERS, IDENTICAL)}
    assert find_possible_duplicates(collection([raw_x(), raw_y(), raw_z()])) == ()      # nothing similar at all
    # A near-copy of Y that differs in confidence is reported by the weaker signals only.
    near = {p.signals for p in find_possible_duplicates(collection([raw_y(confidence=90), raw_y(confidence=10)]))}
    assert near == {(SIGNAL_SAME_DETAIL_REFERENCE, SIGNAL_SAME_GRID_AND_MEMBERS)}


def test_the_identical_signal_remains_a_non_blocking_warning_that_merges_and_removes_nothing():
    col = collection([raw_x(), raw_x()])
    assert IDENTICAL in find_possible_duplicates(col)[0].signals
    col = update_candidate_supplement(col, "RP-0001", reviewer(["A", "B"], "CONN-1"))
    col = update_candidate_supplement(col, "RP-0002", reviewer(["A", "B"], "CONN-2"))
    summary = build_project_review_summary(col)
    assert summary.total_candidates == 2 and summary.possible_duplicate_pairs == 1
    assert [r.state for r in summary.rows] == [REVIEW_STATE_READY_FOR_PIPELINE] * 2          # neither blocked
    assert [r.review_package_id for r in ready_reviewed_connections(col)] == ["RP-0001", "RP-0002"]  # neither dropped
    assert col.candidates[0].extraction == col.candidates[1].extraction and \
        col.candidates[0] is not col.candidates[1]                                           # neither merged
