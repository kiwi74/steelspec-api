"""
J80 — THE R3 PERSISTED-EVIDENCE CONSUMER: THE PROOFS.

WHAT THIS FILE IS

The verification half of J80. The consumer boundary (`app/cad_engine/
cited_candidate_resolution.py`) was implemented before this file existed; this file is what
turns "it was implemented" into "it was proved", and it is deliberately built so that the
FIVE ways this seam can be got wrong are each a red test rather than a matter of opinion:

  1  the consumer does not DELEGATE but re-implements: area A pins R3's own object, and pins
     the module's delegation as a single-statement function body read out of the AST, so
     "it just calls R3" is checked rather than asserted;
  2  the consumer reads the address from somewhere other than the persisted item: area B
     moves every other structure the item carries and proves the answer follows `evidence`;
  3  the consumer substitutes another ATTEMPT for the cited one (area D), which is the one
     substitution that would silently answer with a reading the item never named — and it is
     driven as a MUTATION CONTROL (area H) against an exec'd copy of the real module, so the
     control goes red if the case ever stops discriminating;
  4  the consumer derives the field from the task type, the answer type, the AI value or the
     order (area G), which J79 replaced with the task's own statement;
  5  the consumer writes something (area I) or gets wired into the product (area J).

WHAT IS REAL HERE, AND WHAT IS NOT

  * the persisted item is built by the system's OWN decode path: a row shaped exactly by
    `ITEM_TABLE_COLUMNS` goes through `snapshot_item_from_row`, and its task rows go through
    the system's own `_task_payload` encoder first. A missing column is a real refusal here,
    not a `KeyError` in a helper;
  * the captures are rows in the storage layer's own `CAPTURE_COLUMNS` shape, and the
    mutation control hands them to the REAL `authoritative_captures`, which is what makes
    the substitution it performs a genuine one rather than a stand-in;
  * `page_extraction_captures_for_drawing` is the real loader, called live, read-only, in
    area 6;
  * the frozen route set is J20's own.

  * NOT here: no engine writes, no revision, no citation row, no migration, no route, no
    fabrication call. The consumer is a reader and this file holds it to that.

THE ONE PLACE THE BRIEF AND THE CODEBASE DISAGREE, stated here rather than hidden. The
brief's §6 says the correct live project is `2389c115-664f-4fe8-4b76-ca07aac3719d` and calls
the other id transposed. It is the other way round: `...-4fe8-4b76-...` is the transposition
(J78 proved it does not exist in the deployment at all), and `...-4fe4-8b76-...` is the real
project. This file uses the REAL id, states the fact in a test rather than in a comment, and
never touches the transposition.

THE ONE DELIBERATE WORD-CHOICE, for the same reason J79 had one: J66's checkpoint fence
requires that the case-insensitive substring "citation" appear in exactly three modules
under `app/`. This milestone's concept is that word, so the module describes it in the
codebase's own neighbouring vocabulary ("field-evidence record") instead, and area I
re-asserts the three-module fence from here. The fence was NOT loosened to fit this
milestone — the milestone was worded to fit the fence.
"""
from __future__ import annotations

import ast
import copy
import sys
import uuid
from pathlib import Path

import pytest

from app.cad_engine import connection_review_snapshot as crs
from app.cad_engine import exception_resolution as ex
from app.cad_engine import review_contract as contract
from app.cad_engine.cited_candidate_resolution import (
    FIELD_BOUND,
    FIELD_NOT_SINGLE,
    FIELD_STATES,
    CitedCandidateReading,
    cited_field_for_task,
    resolve_cited_candidate,
    resolve_cited_reading,
)
from app.cad_engine.connection_scoped_evidence import (
    REASON_AMBIGUOUS_PAGE,
    REASON_CANDIDATE_ABSENT,
    REASON_CANDIDATE_INDEX_INVALID,
    REASON_CANDIDATE_INDEX_OUT_OF_RANGE,
    REASON_EVIDENCE_PAGE_ABSENT,
    REASON_RUN_ABSENT,
    RESOLVED_CANDIDATE_INDEX,
    ConnectionScopedReading,
    Unresolved,
    resolve_candidate_at_recorded_address,
)
from app.cad_engine.review_contract import ENGINEERING_FIELDS

from tests import test_real_world_j20_connection_review_persistence_gap as j20
from tests import test_real_world_j4_production_extraction_report_truth as j4

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
CONSUMER = APP / "cad_engine" / "cited_candidate_resolution.py"
SOURCE = CONSUMER.read_text(encoding="utf-8")
CONSUMER_MODULE = "app.cad_engine.cited_candidate_resolution"

#: The real deployment. `...-4fe4-8b76-...`, NOT the transposition the brief names.
PROJECT = "2389c115-664f-4fe4-8b76-ca07aac3719d"
BRIEF_PROJECT = "2389c115-664f-4fe8-4b76-ca07aac3719d"

DRAWING = "11111111-1111-4111-8111-111111111111"
OTHER_DRAWING = "22222222-2222-4222-8222-222222222222"
RUN_OLD = "2830d3bc-6918-4550-b860-f9047a342899"
RUN_NEW = "9c1f0000-1111-4222-8333-444455556666"
RUN_GONE = "77aa77aa-7777-4777-8777-777777777777"

#: The live citation table's name, read out of the module that owns it rather than retyped.
CITATION_TABLE = crs.CITATION_TABLE

#: The fabrication pointer column and bucket, read out of the modules that own them.
POINTER_COLUMN = "fab_drawings_pdf_path"
FABRICATION_BUCKET = "fabrication-drawings"

#: The nine migrations this milestone must not have added to.
PINNED_MIGRATIONS = (
    "20260924000000_j5_section_resolution_truth.sql",
    "20260924010000_j6_reference_data_identity.sql",
    "20260924020000_j8b_connection_plate_evidence_nullability.sql",
    "20260925000000_j22_connection_review_persistence.sql",
    "20260925010000_j23_page_extraction_captures.sql",
    "20260927000000_j28_pdf_annotation_occurrences.sql",
    "20260928000000_j44_project_review_claims.sql",
    "20260929000000_j61_project_documents.sql",
    "20260929010000_j66_field_evidence_citations.sql",
)

production = j4.production


# ===========================================================================
# The fixtures: rows in the storage layer's own shapes.
# ===========================================================================
def _capture(
    *,
    drawing_id=DRAWING,
    page_number=12,
    run_id=RUN_OLD,
    candidates=(),
    captured_at="2026-09-28T19:00:00+00:00",
    parse_failed=False,
):
    """One recorded page reading, in `CAPTURE_COLUMNS`' own shape. A row that is missing one
    of these keys is refused by the real `authoritative_captures`, which the mutation control
    calls — so the shape here is load-bearing rather than decorative."""
    return {
        "drawing_id": drawing_id,
        "drawing_set_id": "33333333-3333-4333-8333-333333333333",
        "project_id": PROJECT,
        "page_number": page_number,
        "analysis_run_id": run_id,
        "model": "claude-sonnet-5",
        "parse_failed": parse_failed,
        "payload": {"raw_connections": list(candidates)},
        "captured_at": captured_at,
    }


def _candidate(mark: str, *, detail: str = "3", grid: str = "B/2"):
    """One connection candidate as a page reading states it, in the reading's own keys."""
    return {
        "source_page": 12,
        "detail_reference": detail,
        "grid_reference": grid,
        "member_mark": mark,
    }


def _task(
    task_id,
    *,
    task_type=ex.TASK_PROVIDE_MATERIAL_SPECIFICATION,
    answer_type=ex.ANSWER_MATERIAL_VALUE,
    question="What material?",
    current_ai_value=None,
    blocker_codes=(),
    allowed_choices=(),
    field_name=None,
):
    return ex.ExceptionResolutionTask(
        task_id=task_id,
        task_type=task_type,
        blocker_codes=tuple(blocker_codes),
        question=question,
        current_ai_value=current_ai_value,
        answer_type=answer_type,
        allowed_choices=tuple(allowed_choices),
        evidence_requirement="",
        field_name=field_name,
    )


def _item_row(
    *,
    evidence,
    tasks=(),
    package_id="RP-0001",
    connection_id=None,
    decision="REVIEW",
):
    """A review item row, shaped by the model's OWN column list — so a column this milestone
    forgot is a refusal from `snapshot_item_from_row` rather than a silently absent key."""
    row = {name: None for name, _, _, _ in crs.ITEM_TABLE_COLUMNS}
    row.update(
        project_id=PROJECT,
        review_revision=0,
        review_package_id=package_id,
        connection_id=connection_id,
        decision=decision,
        output_status=None,
        verification_status=None,
        last_processed_revision=None,
        blocker_codes=[],
        warning_codes=[],
        ai_readings={},
        evidence=evidence,
        provenance={},
        tasks=[crs._task_payload(task, where="j80") for task in tasks],
        generated_files=[],
    )
    return row


def _item(*, evidence, tasks=(), **kwargs):
    """The PERSISTED item: the product of the system's own decode path, not a hand-made
    object with the same field names."""
    return crs.snapshot_item_from_row(_item_row(evidence=evidence, tasks=tasks, **kwargs))


def _evidence(**over):
    """A recorded address. The two provenance keys are the reading's own names; the two
    further terms are the evidence model's."""
    address = {
        "source_drawing_id": DRAWING,
        "source_page": 12,
        "detail_reference": "3",
        "grid_reference": "B/2",
        "drawing_number": "S-1042",
    }
    address.update(over)
    return address


def _resolved(value):
    assert isinstance(value, ConnectionScopedReading), value
    return value


def _reason(value):
    assert isinstance(value, Unresolved), value
    return value.reason_code


#: The item that cites ONE candidate on a page read by TWO attempts — older attempt first in
#: the array, newer second, so a "latest wins" reading of the array is a different answer.
def _two_attempt_captures():
    return [
        _capture(
            run_id=RUN_OLD,
            captured_at="2026-09-28T19:00:00+00:00",
            candidates=[_candidate("A1"), _candidate("B2", detail="7")],
        ),
        _capture(
            run_id=RUN_NEW,
            captured_at="2026-09-29T08:00:00+00:00",
            candidates=[_candidate("A1"), _candidate("X9", detail="7"), _candidate("Y8")],
        ),
    ]


def _citing_item(index=1, run_id=RUN_OLD, **evidence_over):
    return _item(
        evidence=_evidence(candidate_index=index, analysis_run_id=run_id, **evidence_over)
    )


# ===========================================================================
# A. EXACT DELEGATION — the seam adds nothing and removes nothing.
# ===========================================================================
class TestATheSeamIsExactlyR3:
    def test_a1_the_delegation_is_one_statement_and_that_statement_is_the_call(self):
        """The strongest available form of "it just calls R3": the function body is a single
        `return`, and the expression it returns is a direct call to the resolver with the two
        parameters in their own order. Nothing can be added to this body without going red."""
        tree = ast.parse(SOURCE)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_cited_candidate"
        )
        body = [node for node in function.body if not isinstance(node, ast.Expr)]
        assert len(body) == 1, ast.dump(function)
        statement = body[0]
        assert isinstance(statement, ast.Return)
        call = statement.value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Name)
        assert call.func.id == "resolve_candidate_at_recorded_address"
        assert [arg.id for arg in call.args] == ["item", "captures"]
        assert call.keywords == []

    def test_a2_a_resolved_candidate_is_r3s_own_object_field_for_field(self):
        captures = _two_attempt_captures()
        item = _citing_item(index=1, run_id=RUN_OLD)

        mine = _resolved(resolve_cited_candidate(item, captures))
        theirs = _resolved(resolve_candidate_at_recorded_address(item, captures))

        assert mine == theirs
        assert type(mine) is type(theirs)
        assert mine.matched_by == theirs.matched_by == RESOLVED_CANDIDATE_INDEX
        assert mine.candidate == theirs.candidate
        assert mine.candidate_position == theirs.candidate_position == 1
        assert mine.anchor == theirs.anchor and mine.document_id == theirs.document_id

    @pytest.mark.parametrize(
        "evidence, expected",
        [
            ({"candidate_index": 0, "analysis_run_id": RUN_OLD}, None),          # resolved
            ({"candidate_index": 0}, REASON_RUN_ABSENT),                          # C
            ({"candidate_index": "1", "analysis_run_id": RUN_OLD},
             REASON_CANDIDATE_INDEX_INVALID),                                     # F
            ({"candidate_index": 9, "analysis_run_id": RUN_OLD},
             REASON_CANDIDATE_INDEX_OUT_OF_RANGE),                                # F
            ({"analysis_run_id": RUN_OLD}, REASON_AMBIGUOUS_PAGE),                # no position
        ],
    )
    def test_a3_every_answer_is_r3s_own_answer(self, evidence, expected):
        """Including the refusals: a refusal the consumer answered differently would be a
        rule added in the seam."""
        captures = [_capture(run_id=RUN_OLD, candidates=[_candidate("A1"), _candidate("B2")])]
        item = _item(evidence=_evidence(**evidence))
        mine = resolve_cited_candidate(item, captures)
        theirs = resolve_candidate_at_recorded_address(item, captures)
        assert mine == theirs
        assert type(mine) is type(theirs)
        if expected is not None:
            assert _reason(mine) == expected
        else:
            assert isinstance(mine, ConnectionScopedReading)

    def test_a4_a_page_that_was_never_read_is_refused_in_r3s_own_words(self):
        item = _item(evidence=_evidence(candidate_index=0, analysis_run_id=RUN_OLD))
        result = resolve_cited_candidate(item, [])
        assert _reason(result) == REASON_EVIDENCE_PAGE_ABSENT
        assert result == resolve_candidate_at_recorded_address(item, [])

    def test_a5_the_seam_is_idempotent(self):
        captures = _two_attempt_captures()
        item = _citing_item()
        assert resolve_cited_candidate(item, captures) == resolve_cited_candidate(item, captures)

    def test_a6_it_mutates_neither_argument(self):
        captures = _two_attempt_captures()
        item = _citing_item()
        captures_before, item_before = copy.deepcopy(captures), copy.deepcopy(item)
        resolve_cited_candidate(item, captures)
        assert captures == captures_before
        assert item == item_before

    def test_a7_an_unrecorded_position_still_runs_r3_which_delegates_to_r1(self):
        """R3-1: an item that records no position is answered by the rules above, unchanged.
        The seam must not make that case a refusal, and must not make it a fifth rule."""
        captures = [_capture(run_id=RUN_OLD, candidates=[_candidate("A1")])]
        item = _item(evidence=_evidence(analysis_run_id=RUN_OLD))
        result = _resolved(resolve_cited_candidate(item, captures))
        assert result.matched_by == "RESOLVED_SINGLE_CANDIDATE"
        assert result.analysis_run_id == RUN_OLD


# ===========================================================================
# B. THE PERSISTED ITEM IS THE AUTHORITY FOR THE ADDRESS.
# ===========================================================================
class TestBTheAddressIsTheItemsOwn:
    def test_b1_the_reading_carries_the_items_own_drawing_page_and_attempt(self):
        captures = _two_attempt_captures()
        item = _citing_item(index=1, run_id=RUN_OLD)
        result = _resolved(resolve_cited_candidate(item, captures))
        assert result.drawing_id == DRAWING
        assert result.page_number == 12
        assert result.analysis_run_id == RUN_OLD

    def test_b2_the_recorded_position_selects_the_candidate_it_names(self):
        captures = _two_attempt_captures()
        assert _resolved(resolve_cited_candidate(_citing_item(index=0), captures)).candidate[
            "member_mark"
        ] == "A1"
        assert _resolved(resolve_cited_candidate(_citing_item(index=1), captures)).candidate[
            "member_mark"
        ] == "B2"

    def test_b3_the_reading_carries_the_items_own_package_identity(self):
        """`_resolved` takes `review_package_id` from the item when the reference states none,
        so the resolved reading names the item's own package — a second proof that the item,
        and not a caller's restatement, is what is being read."""
        captures = _two_attempt_captures()
        item = _item(
            evidence=_evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            package_id="RP-0007",
        )
        result = _resolved(resolve_cited_candidate(item, captures))
        assert result.review_package_id == "RP-0007"

    def test_b4_every_other_structure_on_the_item_cannot_move_the_answer(self):
        """The item carries `provenance`, `ai_readings` and `blocker_codes` too. All three are
        replaced with values that point at a DIFFERENT candidate, and the answer does not
        move: the address is `evidence` and nothing else."""
        captures = _two_attempt_captures()
        row = _item_row(evidence=_evidence(candidate_index=1, analysis_run_id=RUN_OLD))
        row["ai_readings"] = {"raw_connections": [{"member_mark": "ZZZ"}]}
        row["provenance"] = {"detail_reference": "999", "grid_reference": "Q/9"}
        row["blocker_codes"] = ["MEMBER_IDENTITY"]
        loud = crs.snapshot_item_from_row(row)

        plain = _citing_item(index=1, run_id=RUN_OLD)
        mine = resolve_cited_candidate(loud, captures)
        assert mine == resolve_cited_candidate(plain, captures)
        assert _resolved(mine).candidate["member_mark"] == "B2"

    def test_b5_a_second_drawing_for_the_same_page_is_not_the_items_drawing(self):
        """The drawing term is compared, not inferred: a capture for another drawing at the
        same page number is not this item's evidence."""
        captures = [
            _capture(drawing_id=OTHER_DRAWING, run_id=RUN_OLD, candidates=[_candidate("A1")])
        ]
        item = _citing_item(index=0, run_id=RUN_OLD)
        assert _reason(resolve_cited_candidate(item, captures)) == REASON_EVIDENCE_PAGE_ABSENT

    def test_b6_a_second_page_of_the_same_drawing_is_not_the_items_page(self):
        captures = [
            _capture(page_number=13, run_id=RUN_OLD, candidates=[_candidate("A1")])
        ]
        item = _citing_item(index=0, run_id=RUN_OLD)
        assert _reason(resolve_cited_candidate(item, captures)) == REASON_EVIDENCE_PAGE_ABSENT


# ===========================================================================
# C. NO RECORDED ATTEMPT.
# ===========================================================================
class TestCNoRecordedAttempt:
    def test_c1_a_position_without_an_attempt_is_run_absent(self):
        captures = _two_attempt_captures()
        item = _item(evidence=_evidence(candidate_index=1))
        result = resolve_cited_candidate(item, captures)
        assert _reason(result) == REASON_RUN_ABSENT
        assert "no attempt" in result.detail

    def test_c2_the_refusal_names_the_absence_rather_than_choosing_an_attempt(self):
        """Two attempts are available and both would resolve. Neither is consulted: the
        refusal is about what the ITEM recorded, not about what the evidence could offer."""
        captures = _two_attempt_captures()
        result = resolve_cited_candidate(_item(evidence=_evidence(candidate_index=1)), captures)
        assert _reason(result) == REASON_RUN_ABSENT
        assert RUN_OLD not in result.detail and RUN_NEW not in result.detail

    def test_c3_a_blank_recorded_attempt_is_absence(self):
        captures = _two_attempt_captures()
        for blank in ("", "   "):
            result = resolve_cited_candidate(
                _item(evidence=_evidence(candidate_index=1, analysis_run_id=blank)), captures
            )
            assert _reason(result) == REASON_RUN_ABSENT, blank

    def test_c4_an_explicit_null_recorded_attempt_is_absence(self):
        captures = _two_attempt_captures()
        result = resolve_cited_candidate(
            _item(evidence=_evidence(candidate_index=1, analysis_run_id=None)), captures
        )
        assert _reason(result) == REASON_RUN_ABSENT


# ===========================================================================
# D. TWO ATTEMPTS EXIST AND THE ITEM NAMES THE OLDER ONE.
# ===========================================================================
class TestDTheCitedAttemptIsTheOnlyAttempt:
    def test_d1_the_older_cited_attempt_is_the_one_resolved(self):
        captures = _two_attempt_captures()
        result = _resolved(resolve_cited_candidate(_citing_item(index=1, run_id=RUN_OLD), captures))
        assert result.analysis_run_id == RUN_OLD
        assert result.candidate["member_mark"] == "B2"

    def test_d2_the_newer_attempt_is_not_substituted(self):
        """The same page, the same position, the newer attempt. If the seam had preferred the
        newer reading the answer below would be X9 under RUN_NEW; it is B2 under RUN_OLD."""
        captures = _two_attempt_captures()
        result = _resolved(resolve_cited_candidate(_citing_item(index=1, run_id=RUN_OLD), captures))
        assert result.candidate["member_mark"] != "X9"
        assert result.analysis_run_id != RUN_NEW

    def test_d3_the_newer_attempt_is_reachable_when_the_item_names_it(self):
        """Non-vacuity for D2: the fixture really does hold a different answer under RUN_NEW,
        so D2 is a choice between two live possibilities rather than a collision of one."""
        captures = _two_attempt_captures()
        result = _resolved(resolve_cited_candidate(_citing_item(index=1, run_id=RUN_NEW), captures))
        assert result.analysis_run_id == RUN_NEW
        assert result.candidate["member_mark"] == "X9"

    def test_d4_the_two_attempts_answer_differently(self):
        captures = _two_attempt_captures()
        older = resolve_cited_candidate(_citing_item(index=1, run_id=RUN_OLD), captures)
        newer = resolve_cited_candidate(_citing_item(index=1, run_id=RUN_NEW), captures)
        assert older != newer

    def test_d5_the_candidate_is_carried_verbatim(self):
        """R3 carries the recorded candidate unchanged — `ConnectionScopedReading`'s own
        construction snapshots it (the 7AC precedent), so the assertion is equality with the
        recorded object, which is as much as a snapshotting reading can promise."""
        captures = _two_attempt_captures()
        result = _resolved(resolve_cited_candidate(_citing_item(index=1), captures))
        assert result.candidate == captures[0]["payload"]["raw_connections"][1]


# ===========================================================================
# E. THE CITED ATTEMPT IS MISSING FROM THE HISTORY.
# ===========================================================================
class TestETheCitedAttemptIsMissing:
    def test_e1_the_item_names_an_attempt_the_history_does_not_hold(self):
        captures = _two_attempt_captures()
        result = resolve_cited_candidate(_citing_item(index=1, run_id=RUN_GONE), captures)
        assert _reason(result) == REASON_RUN_ABSENT

    def test_e2_the_refusal_is_r3s_own_detail_unchanged(self):
        captures = _two_attempt_captures()
        item = _citing_item(index=1, run_id=RUN_GONE)
        assert resolve_cited_candidate(item, captures) == resolve_candidate_at_recorded_address(
            item, captures
        )

    def test_e3_a_later_attempt_is_never_quietly_the_cited_one(self):
        """The page WAS read — by two attempts — and the refusal is still stated. This is the
        substitution the whole milestone exists to prevent, and it must not happen even when
        the substitute would have answered."""
        captures = _two_attempt_captures()
        result = resolve_cited_candidate(_citing_item(index=0, run_id=RUN_GONE), captures)
        assert _reason(result) == REASON_RUN_ABSENT
        assert not isinstance(result, ConnectionScopedReading)


# ===========================================================================
# F. A POSITION THAT IS NOT ONE, AND A POSITION THE READING DOES NOT REACH.
# ===========================================================================
class TestFTheRecordedPosition:
    @pytest.mark.parametrize("bad", ["1", "abc", 1.0, -1, -0.5, True, False, [1], {"n": 1}])
    def test_f1_a_position_that_is_not_a_whole_number_is_refused(self, bad):
        captures = _two_attempt_captures()
        item = _item(evidence=_evidence(candidate_index=bad, analysis_run_id=RUN_OLD))
        assert _reason(resolve_cited_candidate(item, captures)) == REASON_CANDIDATE_INDEX_INVALID

    def test_f2_a_boolean_is_not_a_position_even_though_python_counts_it_as_one(self):
        captures = _two_attempt_captures()
        item = _item(evidence=_evidence(candidate_index=True, analysis_run_id=RUN_OLD))
        result = resolve_cited_candidate(item, captures)
        assert _reason(result) == REASON_CANDIDATE_INDEX_INVALID
        assert not isinstance(result, ConnectionScopedReading)

    def test_f3_a_position_past_the_end_is_refused_and_never_clamped(self):
        captures = _two_attempt_captures()
        item = _item(evidence=_evidence(candidate_index=2, analysis_run_id=RUN_OLD))
        result = resolve_cited_candidate(item, captures)
        assert _reason(result) == REASON_CANDIDATE_INDEX_OUT_OF_RANGE
        assert not isinstance(result, ConnectionScopedReading)

    def test_f4_the_last_reachable_position_is_the_one_the_reading_holds(self):
        """Boundary: two candidates, positions 0 and 1, both reachable; 2 is not."""
        captures = _two_attempt_captures()
        assert isinstance(
            resolve_cited_candidate(_citing_item(index=1), captures), ConnectionScopedReading
        )
        assert _reason(
            resolve_cited_candidate(_citing_item(index=2), captures)
        ) == REASON_CANDIDATE_INDEX_OUT_OF_RANGE

    def test_f5_a_very_large_position_is_refused_rather_than_wrapped(self):
        captures = _two_attempt_captures()
        item = _item(evidence=_evidence(candidate_index=10 ** 6, analysis_run_id=RUN_OLD))
        assert _reason(resolve_cited_candidate(item, captures)) == (
            REASON_CANDIDATE_INDEX_OUT_OF_RANGE
        )

    def test_f6_no_position_is_absence_and_not_invalidity(self):
        """The negative control for the whole of area F: an item that records NO position is
        not an item that recorded a bad one. It takes the other entry path entirely, and it
        is refused for what the evidence does — two candidates and no provenance — not for a
        position that was never stated."""
        captures = [_capture(run_id=RUN_OLD, candidates=[_candidate("A1"), _candidate("B2")])]
        item = _item(evidence=_evidence(analysis_run_id=RUN_OLD))
        result = resolve_cited_candidate(item, captures)
        assert _reason(result) == REASON_AMBIGUOUS_PAGE
        assert REASON_CANDIDATE_INDEX_INVALID not in (result.detail or "")

    def test_f7_a_reading_stating_no_candidate_cannot_reach_a_position(self):
        captures = [_capture(run_id=RUN_OLD, candidates=[])]
        item = _item(evidence=_evidence(candidate_index=0, analysis_run_id=RUN_OLD))
        assert _reason(resolve_cited_candidate(item, captures)) == (
            REASON_CANDIDATE_INDEX_OUT_OF_RANGE
        )

    def test_f8_a_missing_position_cannot_reach_a_reading_with_no_candidate(self):
        captures = [_capture(run_id=RUN_OLD, candidates=[])]
        item = _item(evidence=_evidence(analysis_run_id=RUN_OLD))
        assert _reason(resolve_cited_candidate(item, captures)) == REASON_CANDIDATE_ABSENT


# ===========================================================================
# G. THE FIELD AXIS — the task's own statement, and nothing else.
# ===========================================================================
class TestGTheFieldComesFromTheTaskItself:
    def _item_with_three_tasks(self):
        return _item(
            evidence=_evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(
                _task("T-MAT", field_name="material"),
                _task(
                    "T-PLATE",
                    task_type=ex.TASK_PROVIDE_PLATE,
                    answer_type=ex.ANSWER_PLATE_VALUE,
                    field_name="plate",
                ),
                _task(
                    "T-MEMBER",
                    task_type=ex.TASK_SELECT_MEMBER,
                    answer_type=ex.ANSWER_MEMBER_SELECTION,
                    field_name="connected_member_marks",
                ),
                _task(
                    "T-WHOLE",
                    task_type=ex.TASK_COMPLETE_REVIEW,
                    answer_type=ex.ANSWER_APPROVE_REVIEW,
                    field_name=None,
                ),
            ),
        )

    @pytest.mark.parametrize(
        "task_id, expected",
        [
            ("T-MAT", "material"),
            ("T-PLATE", "plate"),
            ("T-MEMBER", "connected_member_marks"),
            ("T-WHOLE", None),
        ],
    )
    def test_g1_the_task_states_its_own_field_verbatim(self, task_id, expected):
        item = self._item_with_three_tasks()
        assert cited_field_for_task(item, task_id) == expected

    def test_g2_each_stated_field_is_a_member_of_the_closed_vocabulary(self):
        item = self._item_with_three_tasks()
        for task_id in ("T-MAT", "T-PLATE", "T-MEMBER"):
            assert cited_field_for_task(item, task_id) in ENGINEERING_FIELDS

    def test_g3_a_task_that_states_none_is_not_single_and_not_a_failure(self):
        item = self._item_with_three_tasks()
        reading = resolve_cited_reading(item, "T-WHOLE", _two_attempt_captures())
        assert reading.field_state == FIELD_NOT_SINGLE
        assert reading.field_name is None
        # ...and its EVIDENCE half still resolves: the two halves do not depend on each other.
        assert isinstance(reading.outcome, ConnectionScopedReading)

    def test_g4_a_bound_task_carries_its_field_beside_the_resolved_candidate(self):
        item = self._item_with_three_tasks()
        reading = resolve_cited_reading(item, "T-PLATE", _two_attempt_captures())
        assert reading.field_state == FIELD_BOUND
        assert reading.field_name == "plate"
        assert reading.task_id == "T-PLATE"
        assert isinstance(reading.outcome, ConnectionScopedReading)

    def test_g5_the_field_states_alone_and_are_exactly_two(self):
        assert FIELD_STATES == (FIELD_BOUND, FIELD_NOT_SINGLE)
        assert set(FIELD_STATES) == {"FIELD_BOUND", "FIELD_NOT_SINGLE"}

    def test_g6_a_state_and_a_name_that_disagree_are_refused(self):
        with pytest.raises(ValueError, match="disagree"):
            CitedCandidateReading(
                task_id="T", field_state=FIELD_BOUND, field_name=None,
                outcome=_reason_free_outcome(),
            )
        with pytest.raises(ValueError, match="disagree"):
            CitedCandidateReading(
                task_id="T", field_state=FIELD_NOT_SINGLE, field_name="plate",
                outcome=_reason_free_outcome(),
            )

    def test_g7_a_state_outside_the_closed_set_is_refused(self):
        with pytest.raises(ValueError, match="not a field state"):
            CitedCandidateReading(
                task_id="T", field_state="FIELD_MAYBE", field_name=None,
                outcome=_reason_free_outcome(),
            )

    def test_g8_a_name_outside_the_vocabulary_is_refused_here_too(self):
        """The task model already refuses it; this proves the CONSUMER's own carrier refuses
        it as well, so a name that arrived by some other road is still not carried."""
        with pytest.raises(ValueError, match="field_name must be one of"):
            CitedCandidateReading(
                task_id="T", field_state=FIELD_BOUND, field_name="connection_id",
                outcome=_reason_free_outcome(),
            )

    def test_g9_the_task_type_answer_type_and_ai_value_do_not_move_the_field(self):
        """Every derivation J79 removed, mutated at once: the field is unchanged."""
        stated = _task("T", field_name="plate")
        mutated = _task(
            "T",
            task_type=ex.TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
            answer_type=ex.ANSWER_ACKNOWLEDGMENT,
            question="an entirely different question",
            current_ai_value="material: S355",
            blocker_codes=("PLATE", "MEMBER_IDENTITY"),
            allowed_choices=("GRADE 300", "GRADE 350"),
            field_name="plate",
        )
        plain_item = _item(evidence=_evidence(), tasks=(stated,))
        loud_item = _item(evidence=_evidence(), tasks=(mutated,))
        assert cited_field_for_task(plain_item, "T") == "plate"
        assert cited_field_for_task(loud_item, "T") == "plate"

    def test_g10_the_task_order_does_not_move_the_field(self):
        """A task is addressed by its `task_id`. The same four tasks, in the reverse order,
        state the same four fields."""
        item = TestGTheFieldComesFromTheTaskItself()._item_with_three_tasks()
        reversed_item = _item(
            evidence=_evidence(),
            tasks=tuple(reversed([
                _task("T-MAT", field_name="material"),
                _task("T-PLATE", task_type=ex.TASK_PROVIDE_PLATE,
                      answer_type=ex.ANSWER_PLATE_VALUE, field_name="plate"),
                _task("T-MEMBER", task_type=ex.TASK_SELECT_MEMBER,
                      answer_type=ex.ANSWER_MEMBER_SELECTION,
                      field_name="connected_member_marks"),
                _task("T-WHOLE", task_type=ex.TASK_COMPLETE_REVIEW,
                      answer_type=ex.ANSWER_APPROVE_REVIEW, field_name=None),
            ])),
        )
        for task_id in ("T-MAT", "T-PLATE", "T-MEMBER", "T-WHOLE"):
            assert cited_field_for_task(item, task_id) == cited_field_for_task(reversed_item, task_id)

    def test_g11_an_unknown_task_id_is_refused_and_never_guessed(self):
        item = _item(evidence=_evidence(), tasks=(_task("T-KNOWN", field_name="plate"),))
        with pytest.raises(KeyError, match="never by its position"):
            cited_field_for_task(item, "T-UNKNOWN")

    def test_g12_a_duplicated_task_id_is_refused_rather_than_picked_between(self):
        item = _item(
            evidence=_evidence(),
            tasks=(_task("T", field_name="plate"), _task("T", field_name="material")),
        )
        with pytest.raises(ValueError, match="names one id twice"):
            cited_field_for_task(item, "T")

    def test_g13_the_presentation_labels_map_is_not_what_this_reads(self):
        """`review_contract._TASK_FIELDS` labels the identity task `connection_id`, which is
        NOT an engineering field — it is the derivation J79 removed. The consumer states None
        for that task, so it is provably not reading that map."""
        assert contract._TASK_FIELDS[ex.TASK_PROVIDE_CONNECTION_IDENTITY] == "connection_id"
        assert "connection_id" not in ENGINEERING_FIELDS
        item = _item(
            evidence=_evidence(),
            tasks=(_task(
                "T-ID",
                task_type=ex.TASK_PROVIDE_CONNECTION_IDENTITY,
                answer_type=ex.ANSWER_CONNECTION_IDENTITY,
                field_name=None,
            ),),
        )
        assert cited_field_for_task(item, "T-ID") is None

    def test_g14_the_consumer_names_neither_the_map_nor_the_presentation_type(self):
        used = {node.id for node in ast.walk(ast.parse(SOURCE)) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(ast.parse(SOURCE)) if isinstance(node, ast.Attribute)
        }
        assert "_TASK_FIELDS" not in used | attributes
        assert "ReviewTaskInfo" not in used | attributes
        assert "review_contract" not in used

    def test_g15_the_task_model_still_refuses_a_name_outside_the_vocabulary(self):
        """The consumer's own vocabulary guard is not the only one: the task it reads from
        refuses such a name first, so a bad name cannot reach the consumer through a task."""
        with pytest.raises(ValueError, match="field_name must be one of"):
            _task("T", field_name="connection_id")

    def test_g16_a_row_recorded_before_j79_states_no_field(self):
        """A pre-J79 payload has no `field_name` key. It is read as the absence it recorded,
        not as a failure and not as an inferred field."""
        item = _item(evidence=_evidence(), tasks=(_task("T", field_name="plate"),))
        stripped = dict(item.tasks[0])
        stripped.pop("field_name")
        older = crs.snapshot_item_from_row({
            **{name: None for name, _, _, _ in crs.ITEM_TABLE_COLUMNS},
            "project_id": PROJECT, "review_revision": 0, "review_package_id": "RP-0001",
            "connection_id": None, "decision": "REVIEW", "output_status": None,
            "verification_status": None, "last_processed_revision": None,
            "blocker_codes": [], "warning_codes": [], "ai_readings": {}, "evidence": {},
            "provenance": {}, "tasks": [stripped], "generated_files": [],
        })
        assert cited_field_for_task(older, "T") is None


def _reason_free_outcome():
    """An R3 outcome for the carrier tests, so that only the FIELD half is under test."""
    return resolve_cited_candidate(
        _item(evidence=_evidence(candidate_index=0, analysis_run_id=RUN_OLD)),
        [_capture(run_id=RUN_OLD, candidates=[_candidate("A1")])],
    )


# ===========================================================================
# H. THE MUTATION CONTROL — a substituting implementation is caught here.
# ===========================================================================
def _mutant_module(source: str, *, name: str):
    """Exec a mutated copy of the REAL consumer module source.

    Registered in `sys.modules` for the duration of the exec, because `@dataclass` resolves
    `cls.__module__` through it; popped afterwards so the real module is what every other
    test imports.
    """
    module = type(sys)(name)
    module.__dict__["__file__"] = str(CONSUMER)
    sys.modules[name] = module
    try:
        exec(compile(source, str(CONSUMER), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


_DELEGATION = "    return resolve_candidate_at_recorded_address(item, captures)"
_SUBSTITUTING = "    return _current_page_reading(item, captures)"
#: The naive consumer, appended to the mutant module below. It is the implementation the
#: brief forbids: it selects the page's CURRENT reading with the real `authoritative_captures`
#: and reads the recorded position out of THAT, so the cited attempt is replaced by whichever
#: attempt stands for the page today. It returns `(attempt, candidate)` so that a test can
#: see both what it answered and which attempt it answered from.
_CURRENT_PAGE_READING = '''

def _current_page_reading(item, captures):
    from app.engineering_data.page_extraction_capture import authoritative_captures
    evidence = getattr(item, "evidence", {}) or {}
    for row in authoritative_captures(captures):
        if (row["drawing_id"] == evidence.get("source_drawing_id")
                and row["page_number"] == evidence.get("source_page")):
            candidates = row["payload"]["raw_connections"]
            return row["analysis_run_id"], candidates[evidence.get("candidate_index")]
    return None, None
'''
_RECONSTRUCTING = (
    "    import copy as _copy\n"
    "    rebuilt = _copy.deepcopy(item)\n"
    "    address = dict(getattr(rebuilt, 'evidence', {}) or {})\n"
    "    address.setdefault('candidate_index', 0)\n"
    "    object.__setattr__(rebuilt, 'evidence', address)\n"
    "    return resolve_candidate_at_recorded_address(rebuilt, captures)"
)


class TestHTheSubstitutingImplementationIsCaught:
    def test_h1_the_anchor_is_present_so_the_mutant_really_is_a_mutant(self):
        assert _DELEGATION in SOURCE
        assert SOURCE.count(_DELEGATION) == 1

    def test_h2_the_current_candidate_implementation_diverges_on_the_superseded_case(self):
        """THE CONTROL. A consumer that resolved against the CURRENT reading of the page
        answers with the newer attempt's candidate. The real one answers with the cited one.
        Same item, same captures, two different answers — so area D's assertion is
        discriminating rather than decorative."""
        mutant = _mutant_module(
            SOURCE.replace(_DELEGATION, _SUBSTITUTING) + _CURRENT_PAGE_READING,
            name="_j80_mutant_a",
        )
        captures = _two_attempt_captures()
        item = _citing_item(index=1, run_id=RUN_OLD)

        correct = _resolved(resolve_cited_candidate(item, captures))
        _attempt, substituting = mutant.resolve_cited_candidate(item, captures)

        assert correct.candidate["member_mark"] == "B2"
        assert substituting["member_mark"] == "X9"
        assert correct.candidate != substituting

    def test_h3_the_substituting_implementation_is_wrong_about_the_attempt_it_cites(self):
        mutant = _mutant_module(
            SOURCE.replace(_DELEGATION, _SUBSTITUTING) + _CURRENT_PAGE_READING,
            name="_j80_mutant_b",
        )
        captures = _two_attempt_captures()
        item = _citing_item(index=1, run_id=RUN_OLD)
        assert mutant.resolve_cited_candidate(item, captures)[0] == RUN_NEW
        assert _resolved(resolve_cited_candidate(item, captures)).analysis_run_id == RUN_OLD

    def test_h4_a_reconstructing_implementation_diverges_when_the_item_records_no_position(self):
        """The other wrong answer: an implementation that rebuilds the item and fills in a
        position answers where the real one refuses. An item that records no position has to
        reach the R1 path, and only the real implementation lets it."""
        mutant = _mutant_module(SOURCE.replace(_DELEGATION, _RECONSTRUCTING), name="_j80_mutant_c")
        captures = [_capture(run_id=RUN_OLD, candidates=[_candidate("A1"), _candidate("B2")])]
        item = _item(evidence=_evidence(analysis_run_id=RUN_OLD))

        correct = resolve_cited_candidate(item, captures)
        reconstructed = mutant.resolve_cited_candidate(item, captures)

        assert _reason(correct) == REASON_AMBIGUOUS_PAGE
        assert isinstance(reconstructed, ConnectionScopedReading)
        assert reconstructed.candidate["member_mark"] == "A1"
        assert correct != reconstructed

    def test_h5_the_mutant_module_is_not_left_in_sys_modules(self):
        assert "_j80_mutant_a" not in sys.modules
        assert CONSUMER_MODULE in sys.modules


# ===========================================================================
# I. NOTHING IS WRITTEN, AND THE J66 FENCE STILL HOLDS.
# ===========================================================================
FORBIDDEN_IN_THE_CONSUMER = (
    ".insert(", ".upsert(", ".update(", ".delete(", ".rpc(", ".table(",
    "storage", "migration", "requests.", "httpx.", "open(",
)


def _code_only(source: str) -> str:
    """The module's source with its docstring removed, so a fence over CODE is not tripped by
    a sentence explaining why the code does not do the thing."""
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    lines = source.splitlines()
    return "\n".join(lines[node.value.end_lineno:])


class TestINothingIsWritten:
    def test_i1_the_consumer_makes_no_write_call_of_any_kind(self):
        code = _code_only(SOURCE)
        for fragment in FORBIDDEN_IN_THE_CONSUMER:
            assert fragment not in code, fragment

    def test_i2_the_consumer_imports_only_the_three_read_only_modules(self):
        imported = set()
        for node in ast.walk(ast.parse(SOURCE)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert imported == {
            "__future__", "dataclasses", "typing",
            "app.cad_engine.connection_review_snapshot",
            "app.cad_engine.connection_scoped_evidence",
            "app.cad_engine.review_contract",
        }, imported

    def test_i3_no_module_under_app_names_this_consumer_at_all(self):
        """Including the consumer's own file, which does not name itself.

        J80 recorded this as "nothing names the consumer". J81 then bound the consumer to the
        production read path, so the fact has ONE declared exception and it is named here
        rather than absorbed: the production read port, which is the only module in the
        product that reaches this consumer. The list stays exact, so a second module
        reaching it — a route, a service, a pipeline — still turns this red."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "cited_candidate_resolution" in path.read_text(encoding="utf-8")
        )
        assert naming == ["app/production_recorded_readings.py"], naming

    def test_i4_the_j66_three_module_fence_still_holds_with_this_module_present(self):
        """The fence J79 had to fit, re-asserted from here rather than assumed: the consumer is
        a reader of the review package, and a fourth module naming that vocabulary would
        redden the fence it is supposed to be downstream of."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/cad_engine/review_contract.py",
            "app/engineering_data/connection_review_repository.py",
        ], naming

    def test_i5_no_migration_was_added(self):
        found = sorted(path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert found == sorted(PINNED_MIGRATIONS), found

    def test_i6_no_migration_names_this_milestone(self):
        for path in (REPO / "supabase" / "migrations").glob("*.sql"):
            assert "j80" not in path.read_text(encoding="utf-8").lower(), path.name

    def test_i7_the_consumer_holds_no_state_between_calls(self):
        """No module-level mutable container: nothing can accumulate across calls, which is
        what a cache of resolved readings would be."""
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.Assign):
                assert not isinstance(node.value, (ast.List, ast.Dict, ast.Set)), ast.dump(node)

    def test_i8_resolving_creates_no_file_and_writes_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolve_cited_reading(
            _item(
                evidence=_evidence(candidate_index=1, analysis_run_id=RUN_OLD),
                tasks=(_task("T", field_name="plate"),),
            ),
            "T",
            _two_attempt_captures(),
        )
        assert list(tmp_path.iterdir()) == []


# ===========================================================================
# J. THE PRODUCTION WIRING FENCE.
# ===========================================================================
class TestJNothingIsWired:
    def test_j1_the_route_set_is_unchanged(self, production):
        assert j20._declared_routes(production.main.app) == j20.FROZEN_ROUTES

    def test_j2_no_route_path_names_the_consumer(self, production):
        paths = [path for _, path in j20._declared_routes(production.main.app)]
        assert not any("cited" in path.lower() for path in paths), paths

    def test_j3_no_caller_exists_anywhere_in_the_product(self, production):
        """The brief's §J: zero production callers from the live resolution route.

        J80 asserted the stronger fact — nothing under `app/` named the consumer at all. J81
        made the consumer reachable, which is this milestone's whole point, and the exception
        is DECLARED rather than absorbed: the production read port, and only it. What §J was
        about still holds literally below — the live RESOLUTION route names no part of this
        milestone, so the mutation path is still not wired to it."""
        callers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "cited_candidate_resolution" in path.read_text(encoding="utf-8")
        )
        assert callers == ["app/production_recorded_readings.py"], callers

    def test_j4_the_live_resolution_route_source_names_no_part_of_this_milestone(self, production):
        """The route that takes a human decision is the one that must not have grown a
        consumer. Its source is read, not its behaviour, so this holds whether or not the
        route is ever exercised."""
        source = (APP / "production_review" / "resolution_route.py")
        if not source.exists():
            candidates = [
                path for path in APP.rglob("*.py")
                if "production/review/{project_id}/connections/" in path.read_text(encoding="utf-8")
                or "connections/{package_id}/resolve" in path.read_text(encoding="utf-8")
            ]
            assert candidates, "the live resolution route could not be located"
            source = candidates[0]
        text = source.read_text(encoding="utf-8")
        for fragment in ("cited_candidate", "resolve_cited_reading", "resolve_cited_candidate"):
            assert fragment not in text, (source.name, fragment)

    def test_j5_no_fabrication_path_names_the_consumer(self):
        for path in APP.rglob("*.py"):
            name = path.name
            if "fabrication" not in name and "drawing" not in name:
                continue
            assert "cited_candidate" not in path.read_text(encoding="utf-8"), name


# ===========================================================================
# 4. THE LOADER IS THE COMPLETE HISTORY, AND IT IS THE ONLY LOAD PATH.
# ===========================================================================
class TestTheLoaderIsTheCompleteHistory:
    def test_the_module_names_the_complete_history_loader_in_its_own_contract(self):
        assert "page_extraction_captures_for_drawing" in SOURCE

    def test_the_loader_returns_every_attempt_and_decides_nothing(self):
        """The claim the seam rests on, checked against the loader's own CODE: it selects
        every row for a drawing and applies no per-page rule. Its docstring NAMES the
        selection rule — to say that the rule is not applied there — so the fence reads the
        body and not the prose."""
        path = APP / "engineering_data" / "repository.py"
        source = path.read_text(encoding="utf-8")
        function = next(
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)
            and node.name == "page_extraction_captures_for_drawing"
        )
        body = list(function.body)
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        code = "\n".join(
            source.splitlines()[body[0].lineno - 1:body[-1].end_lineno]
        )
        assert '.eq("drawing_id", drawing_id)' in code, code
        assert "authoritative_captures" not in code, code
        assert "parse_failed" not in code, code

    def test_the_consumer_never_imports_or_calls_the_authoritative_selection(self):
        tree = ast.parse(SOURCE)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        assert "authoritative_captures" not in names
        assert "authoritative_captures" not in attributes

    def test_the_one_textual_mention_is_prose_and_is_inside_the_docstring(self):
        """The module explains WHY that selection is wrong, and names it once, in the module
        docstring. It must be exactly one mention, and it must be inside the docstring — so
        the fence above cannot be satisfied by an import hidden behind a string."""
        tree = ast.parse(SOURCE)
        docstring_node = tree.body[0]
        assert isinstance(docstring_node, ast.Expr)
        assert isinstance(docstring_node.value, ast.Constant)
        assert isinstance(docstring_node.value.value, str)
        end_line = docstring_node.value.end_lineno
        lines = SOURCE.splitlines()
        mentioning = [
            number for number, line in enumerate(lines, start=1)
            if "authoritative_captures" in line
        ]
        assert len(mentioning) == 1, mentioning
        assert mentioning[0] <= end_line

    def test_the_seam_and_the_loader_compose(self):
        """What the intended caller does, in miniature and with a row set produced by the
        real loader's own shape: item -> its own drawing -> the drawing's whole history ->
        the consumer. Two attempts, the older cited, the older answered."""
        captures = _two_attempt_captures()
        item = _citing_item(index=1, run_id=RUN_OLD)
        drawing_id = item.evidence["source_drawing_id"]
        history = [row for row in captures if row["drawing_id"] == drawing_id]
        assert len(history) == 2
        result = _resolved(resolve_cited_candidate(item, history))
        assert result.analysis_run_id == RUN_OLD


# ===========================================================================
# 6. THE LIVE DEPLOYMENT, READ-ONLY.
# ===========================================================================
@pytest.fixture(scope="module")
def live_client(production):
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live proofs are skipped "
                    "rather than run against a synthetic database")
    client = production.repository.supabase
    try:
        client.table(CITATION_TABLE).select("project_id").limit(1).execute()
    except Exception as exc:  # credentials/network unavailable in this process
        pytest.skip(f"the live deployment could not be read: {type(exc).__name__}")
    return client


def _rows(client, table, columns="*", **filters):
    try:
        query = client.table(table).select(columns)
        for column, value in filters.items():
            query = query.eq(column, value)
        return query.execute().data or []
    except Exception as exc:
        pytest.skip(f"live {table} could not be read: {type(exc).__name__}: {exc}")


class TestTheLiveDeploymentIsUntouched:
    def test_area_6_the_real_project_exists_and_the_transposition_does_not(self, live_client):
        """The brief names the transposed id. A read against it proves which of the two the
        deployment actually holds, so the fact is a checked one rather than a remark."""
        assert len(_rows(live_client, "projects", "id", id=PROJECT)) == 1
        assert _rows(live_client, "projects", "id", id=BRIEF_PROJECT) == []

    def test_area_6_revision_zero_is_the_only_revision_and_holds_three_items(self, live_client):
        headers = _rows(live_client, crs.SNAPSHOT_TABLE, "review_revision", project_id=PROJECT)
        assert sorted(row["review_revision"] for row in headers) == [0]
        items = _rows(
            live_client, crs.ITEM_TABLE, "review_package_id,review_revision", project_id=PROJECT
        )
        assert len(items) == 3, items
        assert {row["review_revision"] for row in items} == {0}
        assert sorted(row["review_package_id"] for row in items) == ["RP-0001", "RP-0002",
                                                                     "RP-0003"]

    def test_area_6_no_item_records_a_candidate_origin(self, live_client):
        for row in _rows(live_client, crs.ITEM_TABLE, "evidence", project_id=PROJECT):
            evidence = row["evidence"] or {}
            assert "candidate_index" not in evidence, evidence
            assert "analysis_run_id" not in evidence, evidence

    def test_area_6_every_stored_task_still_loads_and_states_no_field(self, live_client):
        total, failures, stated = 0, [], []
        for row in _rows(live_client, crs.ITEM_TABLE, "tasks", project_id=PROJECT):
            for payload in row["tasks"] or []:
                total += 1
                try:
                    task = crs._task_from_row(payload)
                except Exception as exc:  # a payload that no longer loads is a failure
                    failures.append((payload.get("task_id"), repr(exc)))
                else:
                    if task.field_name is not None:
                        stated.append(task.field_name)
        assert failures == []
        assert total == 28, total
        assert stated == []

    def test_area_6_the_live_loader_is_the_complete_history_and_the_seam_refuses(self, live_client):
        """The §4 proof, live: the consumer is fed the real `page_extraction_captures_for_
        drawing` history for each item's own drawing, and every item is answered by R3's own
        refusal — no item records a candidate origin, so no candidate is claimed."""
        from app.engineering_data import repository as prod_repository

        items = _rows(live_client, crs.ITEM_TABLE, "*", project_id=PROJECT)
        assert len(items) == 3
        total_captures, reasons = 0, []
        for row in items:
            item = crs.snapshot_item_from_row(row)
            drawing_id = item.evidence["source_drawing_id"]
            captures = prod_repository.page_extraction_captures_for_drawing(drawing_id)
            total_captures += len(captures)
            reasons.append(_reason(resolve_cited_candidate(item, captures)))
        assert total_captures > 0, "the drawing has no recorded reading; this proof is vacuous"
        assert set(reasons) == {REASON_RUN_ABSENT}, reasons

    def test_area_6_the_citation_table_holds_no_row_and_the_fabrication_pointer_is_null(
        self, live_client
    ):
        assert _rows(live_client, CITATION_TABLE, "project_id") == []
        rows = _rows(live_client, "projects", f"id,{POINTER_COLUMN}", id=PROJECT)
        assert rows and rows[0][POINTER_COLUMN] is None, rows
        try:
            objects = live_client.storage.from_(FABRICATION_BUCKET).list()
        except Exception as exc:
            pytest.skip(f"the fabrication bucket could not be listed: {type(exc).__name__}")
        assert objects == [], objects

    def test_area_6_the_pointer_and_bucket_names_still_match_the_modules_that_own_them(self):
        """The two live strings above are read from the modules that own them — by AST, not by
        import, so this proof needs no environment and cannot be fooled by a stale constant."""
        assert _module_constant(
            APP / "production_fabrication_pointer.py", "POINTER_COLUMN"
        ) == POINTER_COLUMN
        assert _module_constant(
            APP / "production_fabrication_artifact.py", "FABRICATION_BUCKET"
        ) == FABRICATION_BUCKET


def _module_constant(path: Path, name: str):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not assigned at module level in {path.name}")


# ===========================================================================
# The brief's own §6 id, pinned as a fact rather than left as a claim in a docstring.
# ===========================================================================
class TestTheBriefsProjectId:
    def test_the_briefs_id_is_two_digits_swapped_and_is_not_the_real_one(self):
        """The last two groups differ: the real id reads `4fe4-8b76` where the brief reads
        `4fe8-4b76`. The `4` and the `8` have changed places, so the two strings carry the
        same characters and neither is a substring of the other — which is exactly why the
        transposition is plausible enough to need a live read to settle."""
        assert BRIEF_PROJECT != PROJECT
        assert sorted(BRIEF_PROJECT) == sorted(PROJECT)
        assert BRIEF_PROJECT.split("-")[:2] == PROJECT.split("-")[:2]
        assert BRIEF_PROJECT.split("-")[2:] != PROJECT.split("-")[2:]
        assert uuid.UUID(PROJECT).version == 4
