"""
J81 — THE PRODUCTION READ-PATH CONSUMER BINDING: THE PROOFS.

WHAT THIS FILE IS

The verification half of J81. The production read port
(`app/production_recorded_readings.py`) and its binding into the workflow read entry
(`app/production_connection_review.build_workflow_review`) were implemented before this file
existed; this file is what turns "it was implemented" into "it was proved", and it is
deliberately built so that each way this seam can be got wrong is a red test rather than a
matter of opinion:

  A  the read path does not reach J80 — or reaches R3 beside it. Area A pins the port's
     import set, pins that the port names neither R3 nor the resolver module, and pins the
     READ ENTRY's call by recording the seam the production code actually reaches;
  B  the address is read from somewhere other than the persisted item. Area B moves every
     other structure the item carries, and records which drawings the loader was asked for;
  C  a SUPERSEDED attempt is substituted for the cited one. Area C proves both that the port
     answers the older attempt and that the current-attempt selection would answer differently,
     so the case discriminates rather than merely passing;
  D  a legacy item is "repaired". Area D proves an item with no recorded origin keeps R3's
     `RUN_ABSENT`, and that the field half stays independent of it;
  E  the field is derived from the task type, the answer type, the AI value or the order.
     Area E moves every one of those and pins the binding, and pins that the presentation
     layer's derivation (`ReviewTaskInfo.field`, `_TASK_FIELDS`) is used nowhere;
  F  the boundary is bypassed by a reconstructed candidate. Area F drives the substitution as
     a MUTATION CONTROL against an exec'd copy of the real port, so the control goes red if it
     ever stops discriminating;
  G  something is written. Area G fences the port's source, proves a call creates no file and
     mutates neither argument, and drives the WHOLE read path through the real route with the
     write tripwires armed;
  H  the mutation route is wired. Area H pins the frozen route set and proves the resolution
     route names no part of this milestone;
  I  the live deployment moved. Area I reads the real project live, read-only.

WHAT IS REAL HERE, AND WHAT IS NOT

  * the persisted item is built by the system's OWN decode path — a row shaped exactly by
    `ITEM_TABLE_COLUMNS` goes through `snapshot_item_from_row`, and its task rows go through
    the system's own `_task_payload` encoder first;
  * the captures are rows in the storage layer's own `CAPTURE_COLUMNS` shape, and the
    MUTATION CONTROL hands them to the REAL `authoritative_captures`, which is what makes the
    substitution it performs a genuine one rather than a stand-in;
  * the production read entry is the module's own `build_workflow_review`, driven through
    J25's request harness — the same doubles a request uses, with the same write tripwires
    armed — so "the read path reaches J80" is checked on the path a request takes;
  * the live proofs call the real loader (`page_extraction_captures_for_drawing`) and the
    real store read, read-only.

  * NOT here: no engine write, no review revision, no field-evidence row, no migration, no
    route, no storage object, no fabrication call.

THE ONE PLACE THE BRIEF AND THE CODEBASE DISAGREE, stated here rather than hidden. The
brief's §6 and §9 name `2389c115-664f-4fe8-4b76-ca07aac3719d` as the correct project and call
the other id transposed. It is the other way round: `...-4fe4-8b76-...` is the real project
and `...-4fe8-4b76-...` is the transposition (J78 proved the transposition does not exist in
the deployment at all). This file uses the REAL id, and the live read settles the fact rather
than a comment doing so.

THE ONE DELIBERATE WORD-CHOICE, for the same reason J79 and J80 had one: J66's checkpoint
fence requires that the case-insensitive substring "citation" appear in exactly three modules
under `app/`. This milestone's concept is that word, so the port describes it in the
codebase's own neighbouring vocabulary ("field reading", "recorded address") instead, and
area G re-asserts the three-module fence from here. The fence was NOT loosened to fit this
milestone — the milestone was worded to fit the fence.
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

import app.production_connection_review as workflow_review
import app.production_recorded_readings as port
from app.cad_engine import connection_review_snapshot as crs
from app.cad_engine import exception_resolution as ex
from app.cad_engine import review_contract as contract
from app.cad_engine.cited_candidate_resolution import (
    FIELD_BOUND,
    FIELD_NOT_SINGLE,
    FIELD_STATES,
    resolve_cited_reading,
)
from app.cad_engine.connection_scoped_evidence import (
    REASON_EVIDENCE_PAGE_ABSENT,
    REASON_RUN_ABSENT,
    RESOLVED_CANDIDATE_INDEX,
    ConnectionScopedReading,
    Unresolved,
    resolve_candidate_at_recorded_address,
)
from app.cad_engine.review_contract import ENGINEERING_FIELDS
from app.engineering_data import connection_review_repository as review_store
from app.engineering_data.page_extraction_capture import authoritative_captures

from tests import test_real_world_j20_connection_review_persistence_gap as j20
from tests import test_real_world_j22_connection_review_persistence as j22
from tests import test_real_world_j24a_revision_zero_producer as j24a
from tests import test_real_world_j25_connection_review_surface as j25
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j80_cited_candidate_resolution as j80

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
PORT = APP / "production_recorded_readings.py"
PORT_SOURCE = PORT.read_text(encoding="utf-8")
READ_ENTRY = APP / "production_connection_review.py"
CONSUMER = APP / "cad_engine" / "cited_candidate_resolution.py"
RESOLUTION_ROUTE = APP / "production_review_resolution.py"

PORT_PATH = "app/production_recorded_readings.py"

#: The real deployment. `...-4fe4-8b76-...`, NOT the transposition the brief names.
PROJECT = j80.PROJECT
BRIEF_PROJECT = j80.BRIEF_PROJECT
DRAWING = j80.DRAWING
OTHER_DRAWING = j80.OTHER_DRAWING
RUN_OLD = j80.RUN_OLD
RUN_NEW = j80.RUN_NEW

#: The live field-evidence table's name and the fabrication pointer, read from the modules
#: that own them rather than retyped here.
CITATION_TABLE = crs.CITATION_TABLE
POINTER_COLUMN = j80.POINTER_COLUMN
FABRICATION_BUCKET = j80.FABRICATION_BUCKET
PINNED_MIGRATIONS = j80.PINNED_MIGRATIONS

#: The six fence files J81's new production caller had to be DECLARED in, each of which holds
#: between one and two such guards — seven guards across six files. Every one of them is
#: asserted here to be an exact one-element comparison rather than a widened one.
DECLARED_FENCES = (
    APP / ".." / "tests" / "test_real_world_j71_connection_scoped_evidence.py",
    APP / ".." / "tests" / "test_real_world_j72_candidate_origin_address.py",
    APP / ".." / "tests" / "test_real_world_j74_persisted_candidate_address.py",
    APP / ".." / "tests" / "test_real_world_j77_persisted_candidate_origin_carry_forward.py",
    APP / ".." / "tests" / "test_real_world_j79_field_binding_boundary.py",
    APP / ".." / "tests" / "test_real_world_j80_cited_candidate_resolution.py",
)

production = j4.production


# ===========================================================================
# The fixtures: a persisted item, and a loader that records what it was asked for.
# ===========================================================================
def _snapshot(*items, project_id=PROJECT, revision=0):
    """An assembled snapshot over the items given, in the order given."""
    return crs.ReviewSnapshot(
        project_id=project_id,
        review_revision=revision,
        project_status="review",
        evidence_identity={},
        evidence_run_ids=(),
        items=tuple(items),
    )


def _loader(rows, *, asked=None):
    """J23's loader shape: every row for the drawing asked for, nothing filtered out."""
    def load(drawing_id):
        if asked is not None:
            asked.append(drawing_id)
        return [row for row in rows if row["drawing_id"] == drawing_id]

    return load


def _readings(snapshot, rows=(), *, asked=None):
    return port.read_recorded_field_readings(snapshot, captures_for_drawing=_loader(rows, asked=asked))


def _code_only(source: str) -> str:
    """The source with EVERY docstring removed — module, class and function.

    `j80._code_only` removes the module docstring only, which is enough for the module it was
    written for. Both modules this milestone fences explain themselves at the point of the code
    ("the rule is `authoritative_captures`, and handing that SELECTION to the consumer…"), so a
    fence that kept those sentences would fail on the code's own explanation of why it does not
    do the thing. The tree is unparsed rather than sliced, so comments go too.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _address_facts(reading):
    """The facts R3 established about the CANDIDATE — not the facts the item supplied.

    `ConnectionScopedReading` carries the item's own package id and field name beside the
    resolved address, so two readings of the same address from differently-labelled items
    differ. Comparing these four is comparing the resolution, which is what the authority
    claim is about.
    """
    outcome = reading.outcome
    return (outcome.analysis_run_id, outcome.candidate_position, outcome.matched_by,
            outcome.candidate)


def _only(readings, package_id=None):
    matched = [r for r in readings if package_id is None or r.review_package_id == package_id]
    assert len(matched) == 1, matched
    return matched[0]


def _resolved(value):
    assert isinstance(value, ConnectionScopedReading), value
    return value


def _reason(value):
    assert isinstance(value, Unresolved), value
    return value.reason_code


def _two_attempt_captures():
    return j80._two_attempt_captures()


@pytest.fixture()
def surface(production, monkeypatch):
    """J25's request harness: the real application, the real route, real doubles.

    It arms the write tripwires and patches the three read seams a request uses — the store
    client, the section matcher, and (added by J81) the capture history. Reusing it means the
    J81 proofs run on the path a request takes rather than on a re-built approximation.
    """
    return j25._surface(
        production, monkeypatch, store=j24a._selby_store(),
        projects={j25.SELBY_PROJECT: j25._project_row(j25.SELBY_PROJECT, j25.SELBY_OWNER)},
        owner=j25.SELBY_OWNER,
    )


# ===========================================================================
# A. THE PRODUCTION READ PATH REACHES J80, AND R3 ONLY THROUGH IT.
# ===========================================================================
class TestATheReadPathReachesJ80:
    def test_a1_the_port_imports_j80_and_nothing_else_that_could_answer_r3(self):
        """The strongest available form of "it goes through the boundary": the port's whole
        import set is pinned, so a future edit that reached the resolver, the storage layer
        or the database would have to change this list first."""
        imported: set[str] = set()
        for node in ast.walk(ast.parse(PORT_SOURCE)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert imported == {
            "__future__",
            "dataclasses",
            "typing",
            "app.cad_engine.cited_candidate_resolution",
        }, imported

    def test_a2_the_port_never_names_r3_or_the_module_it_lives_in(self):
        """A port that imported the resolver would be a SECOND path to the same answer, which
        is exactly what binding to the boundary is supposed to prevent."""
        code = _code_only(PORT_SOURCE)
        for forbidden in (
            "resolve_candidate_at_recorded_address",
            "connection_scoped_evidence",
            "resolve_connection_field_reading",
            "authoritative_captures",
        ):
            assert forbidden not in code, forbidden

    def test_a3_every_reading_is_j80s_own_answer(self):
        """The port adds no rule and removes none: for each task the reading is what J80's own
        `resolve_cited_reading` returns for the same item, task id and history."""
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name="plate"),),
        )
        reading = _only(_readings(_snapshot(item), captures))
        theirs = resolve_cited_reading(item, "T-1", captures)
        assert reading.reading == theirs
        assert type(reading.reading) is type(theirs)
        assert reading.field_state == theirs.field_state == FIELD_BOUND
        assert reading.field_name == theirs.field_name == "plate"
        assert reading.outcome == theirs.outcome

    def test_a4_a_recorded_outcome_is_r3s_own_object_not_a_copy_of_it(self):
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        reading = _only(_readings(_snapshot(item), captures))
        assert reading.outcome == resolve_candidate_at_recorded_address(item, captures)
        assert type(reading.outcome) is ConnectionScopedReading
        assert _resolved(reading.outcome).matched_by == RESOLVED_CANDIDATE_INDEX

    def test_a5_the_production_read_entry_calls_the_port_with_the_stores_own_snapshot(
        self, surface, monkeypatch
    ):
        """Behavioural, and recorded: the read entry hands the port the snapshot the store
        returned and the module's OWN capture seam. Nothing is rebuilt in between — the same
        object identity is asserted, so a caller that assembled a second snapshot beside the
        store's would fail here."""
        state = j25._recorded(surface, j25.SELBY_PROJECT)
        calls = []
        real = port.read_recorded_field_readings

        def recorder(snapshot, *, captures_for_drawing):
            calls.append((snapshot, captures_for_drawing))
            return real(snapshot, captures_for_drawing=captures_for_drawing)

        monkeypatch.setattr(workflow_review, "read_recorded_field_readings", recorder)
        review = j25._composition(surface, recorded_state=state)

        assert len(calls) == 1, calls
        assert calls[0][0] is state.snapshot
        assert calls[0][1] is workflow_review.capture_history_for_drawing
        assert review.recorded_field_readings == real(
            state.snapshot, captures_for_drawing=workflow_review.capture_history_for_drawing
        )

    def test_a6_the_read_entry_reads_nothing_when_nothing_is_recorded(self, surface, monkeypatch):
        """A project with no persisted state has no persisted ITEM to read an address off, so
        the port is not called at all — there is nothing to answer FOR, and the empty tuple is
        the honest answer rather than a reading of a history no item named."""
        def refuse(*args, **kwargs):
            raise AssertionError("the port was called with no persisted state")

        monkeypatch.setattr(workflow_review, "read_recorded_field_readings", refuse)
        review = j25._composition(surface)
        assert review.persisted_code == review_store.NO_PERSISTED_CONNECTION_REVIEW_STATE
        assert review.persisted_view is None
        assert review.persisted_revisions == ()
        assert review.recorded_field_readings == ()

    def test_a7_the_capture_seam_is_j23s_own_whole_history_read(self):
        """Read out of the AST rather than asserted in prose: the seam's body is one
        function-local import of `page_extraction_captures_for_drawing` and one return of its
        call. A seam that applied a per-page rule, or that reached the repository at module
        import, could not have this body."""
        function = _function_node(READ_ENTRY, "capture_history_for_drawing")
        body = [node for node in function.body if not isinstance(node, ast.Expr)]
        assert len(body) == 2, ast.dump(function)
        imported, returned = body
        assert isinstance(imported, ast.ImportFrom)
        assert imported.module == "app.engineering_data.repository"
        assert imported.col_offset > 0, "the import must be on use, not at module import"
        assert [alias.name for alias in imported.names] == ["page_extraction_captures_for_drawing"]
        assert isinstance(returned, ast.Return)
        call = returned.value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Name) and call.func.id == \
            "page_extraction_captures_for_drawing"
        assert [arg.id for arg in call.args] == ["drawing_id"]

    def test_a8_the_read_entry_names_no_page_selection_rule_in_its_code(self):
        """`authoritative_captures` is the rule that decides which attempt STANDS for a page,
        and the read entry's CODE never names it. (Its docstrings do, to say why it is the
        wrong input — the same reason J80's consumer names it in prose only.)"""
        code = _code_only(READ_ENTRY.read_text(encoding="utf-8"))
        for forbidden in ("authoritative_captures", "captured_at"):
            assert forbidden not in code, forbidden

    def test_a9_the_route_reaches_the_port_only_through_the_read_entry(self):
        """`app/main.py` is the route. It calls the read entry's own two functions and names
        neither the port nor the boundary, so there is one path and not two."""
        source = (APP / "main.py").read_text(encoding="utf-8")
        assert "render_workflow_review(build_workflow_review(project_id))" in source
        assert "production_recorded_readings" not in source
        assert "cited_candidate_resolution" not in source
        assert "resolve_cited_reading" not in source


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in {path.name}")


# ===========================================================================
# B. THE PERSISTED ITEM IS THE AUTHORITY FOR THE ADDRESS.
# ===========================================================================
class TestBThePersistedItemIsTheAuthority:
    def test_b1_the_loader_is_asked_for_the_drawings_the_items_themselves_name(self):
        """The load path is addressed BY DRAWING, and by the drawing each persisted item's own
        evidence names — never by the reconstruction's drawing, another item's drawing, or a
        caller's guess."""
        old_drawing_captures = j80._capture(
            run_id=RUN_OLD, candidates=[j80._candidate("A1")]
        )
        other_drawing_captures = j80._capture(
            drawing_id=OTHER_DRAWING, run_id=RUN_OLD, candidates=[j80._candidate("Z7")]
        )
        first = j80._item(
            evidence=j80._evidence(candidate_index=0, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-A"),),
            package_id="RP-0001",
        )
        second = j80._item(
            evidence=j80._evidence(
                candidate_index=0, analysis_run_id=RUN_OLD, source_drawing_id=OTHER_DRAWING
            ),
            tasks=(j80._task("T-B"),),
            package_id="RP-0002",
        )
        asked: list[str] = []
        readings = _readings(
            _snapshot(first, second),
            [old_drawing_captures, other_drawing_captures],
            asked=asked,
        )
        assert asked == [DRAWING, OTHER_DRAWING]
        assert _only(readings, "RP-0001").outcome.candidate["member_mark"] == "A1"
        assert _only(readings, "RP-0002").outcome.candidate["member_mark"] == "Z7"

    def test_b2_one_loading_per_distinct_drawing_however_many_tasks_cite_it(self):
        """Three tasks of one item share ONE history read: the history is per drawing, not per
        task, so the loader is asked once and the same reading answers all three. A port that
        re-read per task would ask three times, which the recorded list would show."""
        captures = [j80._capture(
            run_id=RUN_OLD, candidates=[j80._candidate("A1"), j80._candidate("B2")],
        )]
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"), j80._task("T-2"), j80._task("T-3")),
        )
        asked: list[str] = []
        readings = _readings(_snapshot(item), captures, asked=asked)
        assert asked == [DRAWING]
        assert len(readings) == 3
        assert {r.outcome.candidate["member_mark"] for r in readings} == {"B2"}
        assert {r.task_id for r in readings} == {"T-1", "T-2", "T-3"}

    def test_b3_every_other_persisted_field_moves_and_the_answer_does_not(self):
        """Decision, statuses, blockers, warnings, the AI readings, the provenance, the
        package id and the generated files are all moved. The recorded address is unmoved, and
        so is the answer — which is what "the item's EVIDENCE is the authority" means."""
        captures = _two_attempt_captures()
        base_row = j80._item_row(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        moved_row = dict(base_row)
        moved_row.update(
            decision="ACCEPTED",
            output_status="GENERATED",
            verification_status="VERIFIED",
            last_processed_revision=7,
            blocker_codes=["SOMETHING"],
            warning_codes=["ELSE"],
            ai_readings={"material": "300"},
            provenance={"source": "a-different-one"},
            review_package_id="RP-9999",
            generated_files=["/tmp/x.pdf"],
        )
        base = port.read_recorded_field_readings(
            _snapshot(crs.snapshot_item_from_row(base_row)),
            captures_for_drawing=_loader(captures),
        )
        moved = port.read_recorded_field_readings(
            _snapshot(crs.snapshot_item_from_row(moved_row)),
            captures_for_drawing=_loader(captures),
        )
        assert base[0].review_package_id != moved[0].review_package_id, "the item really moved"
        assert _address_facts(base[0]) == _address_facts(moved[0])
        assert _resolved(base[0].outcome).candidate["member_mark"] == "B2"
        assert _resolved(moved[0].outcome).candidate["member_mark"] == "B2"
        assert (base[0].field_state, moved[0].field_state) == (FIELD_NOT_SINGLE, FIELD_NOT_SINGLE)

    def test_b4_the_items_own_evidence_moves_and_the_answer_moves_with_it(self):
        """The complement of b3: change the evidence and ONLY the evidence, and the answer
        changes — so the address being read really is the item's own evidence."""
        captures = _two_attempt_captures()
        first = j80._item(
            evidence=j80._evidence(candidate_index=0, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        second = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        assert _resolved(_only(_readings(_snapshot(first), captures)).outcome).candidate[
            "member_mark"
        ] == "A1"
        assert _resolved(_only(_readings(_snapshot(second), captures)).outcome).candidate[
            "member_mark"
        ] == "B2"

    def test_b5_an_item_that_names_no_drawing_loads_no_history_and_r3_states_it(self):
        """An item whose evidence names no drawing has no history to load — there is no
        drawing to load one FOR. The loader is not called for it, and the empty history is
        what R3 is given, so R3 states the absence in its own words."""
        item = j80._item(evidence={"detail_reference": "3"}, tasks=(j80._task("T-1"),))
        asked: list[str] = []
        readings = _readings(_snapshot(item), _two_attempt_captures(), asked=asked)
        assert asked == []
        assert _only(readings).outcome == resolve_candidate_at_recorded_address(item, ())

    def test_b6_the_accessors_and_the_reading_cannot_drift(self):
        """The wrapper stores no copy of any fact: every accessor reads through to J80's own
        object, so there is one value and no second one to disagree with it."""
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name="plate"),),
            connection_id="c-1",
        )
        reading = _only(_readings(_snapshot(item), captures))
        assert reading.task_id is reading.reading.task_id
        assert reading.field_name is reading.reading.field_name
        assert reading.outcome is reading.reading.outcome
        assert reading.connection_id == "c-1"
        assert reading.review_package_id == item.review_package_id


def _persisted_store(project_id):
    """J25's own recorded-band fixture, kept in the store double this file can drive.

    `j25._recorded` persists into J22's fake store and hands back the STATE; the route needs
    the store itself, so the same three genuine steps are taken here and the fake store is
    returned with its tables. Nothing is hand-built: the snapshot is `build_review_snapshot`'s
    and the rows are J22's writer's.
    """
    workflow = j24a._reconstruct(j24a._selby_store(), project_id).workflow
    fake = j22._FakeStore(projects=(project_id,))
    snapshot = j25.build_review_snapshot(
        workflow, project_id=project_id, evidence_rows=j22.NO_EVIDENCE, previous_revision=None,
    )
    assert j25.REAL_PERSIST(snapshot, client=fake) is snapshot
    return fake


def _capture_single(mark: str, *, run_id=RUN_OLD, page_number=12):
    return j80._capture(
        run_id=run_id, page_number=page_number, candidates=[j80._candidate(mark)]
    )


# ===========================================================================
# C. THE CITED ATTEMPT IS THE ONE READ — never the latest one.
# ===========================================================================
class TestCTheCitedAttemptIsTheOneRead:
    def test_c1_the_item_cites_the_older_attempt_and_the_older_attempt_answers(self):
        """The fixture is built so a "latest wins" reading is a DIFFERENT answer: the older
        attempt holds B2 at position 1 and the newer holds X9 there."""
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        result = _resolved(_only(_readings(_snapshot(item), captures)).outcome)
        assert result.analysis_run_id == RUN_OLD
        assert result.matched_by == RESOLVED_CANDIDATE_INDEX
        assert result.candidate["member_mark"] == "B2"
        assert result.candidate_position == 1

    def test_c2_the_current_attempt_selection_would_answer_differently(self):
        """The discrimination, stated rather than assumed: the REAL `authoritative_captures`
        over these rows keeps the NEWER attempt, whose position 1 holds X9. A port that fed
        the selection to R3 could not answer B2."""
        captures = _two_attempt_captures()
        standing = authoritative_captures(captures)
        assert [row["analysis_run_id"] for row in standing] == [RUN_NEW]
        assert standing[0]["payload"]["raw_connections"][1]["member_mark"] == "X9"

    def test_c3_the_port_is_given_the_whole_history_and_not_a_selection_of_it(self):
        """The loader is asked once for the drawing and its whole list is handed on: both
        attempts reach R3, which is what lets R3 find the one the item named."""
        captures = _two_attempt_captures()
        given: list[list] = []

        def load(drawing_id):
            rows = [row for row in captures if row["drawing_id"] == drawing_id]
            given.append(rows)
            return rows

        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        port.read_recorded_field_readings(_snapshot(item), captures_for_drawing=load)
        assert len(given) == 1
        assert len(given[0]) == 2
        assert {row["analysis_run_id"] for row in given[0]} == {RUN_OLD, RUN_NEW}

    def test_c4_a_superseded_attempt_stays_selectable_for_the_item_that_cites_it(self):
        """The same page read AGAIN, with the older attempt first in the array: the newer
        reading is the one the selection rule keeps, and the item still gets the older one."""
        older = _capture_single("B2", run_id=RUN_OLD)
        newer = _capture_single("X9", run_id=RUN_NEW)
        item = j80._item(
            evidence=j80._evidence(candidate_index=0, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        result = _resolved(_only(_readings(_snapshot(item), [older, newer])).outcome)
        assert result.analysis_run_id == RUN_OLD
        assert result.candidate["member_mark"] == "B2"


# ===========================================================================
# D. A LEGACY ITEM IS NOT REPAIRED.
# ===========================================================================
class TestDAMissingOriginIsPreserved:
    def test_d1_an_item_with_no_recorded_attempt_keeps_r3s_run_absent(self):
        """The live revision-0 items predate J72 and record no attempt. Their candidate half
        reads as `RUN_ABSENT`, which is the true answer to "which attempt did this item's own
        evidence name?" — none. Nothing here fills one in."""
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1),
            tasks=(j80._task("T-1", field_name="plate"),),
        )
        reading = _only(_readings(_snapshot(item), captures))
        assert _reason(reading.outcome) == REASON_RUN_ABSENT
        assert reading.outcome == resolve_candidate_at_recorded_address(item, captures)

    def test_d2_the_field_half_is_unaffected_by_the_missing_attempt(self):
        """The two axes are independent in BOTH directions: a task that states a field still
        states it when the evidence establishes no candidate, and a task that states none
        still gets the same refusal."""
        captures = _two_attempt_captures()
        bound = j80._item(
            evidence=j80._evidence(candidate_index=1),
            tasks=(j80._task("T-1", field_name="plate"),),
            package_id="RP-0001",
        )
        unbound = j80._item(
            evidence=j80._evidence(candidate_index=1),
            tasks=(j80._task("T-2"),),
            package_id="RP-0002",
        )
        readings = _readings(_snapshot(bound, unbound), captures)
        assert (_only(readings, "RP-0001").field_state, _only(readings, "RP-0001").field_name) \
            == (FIELD_BOUND, "plate")
        assert (_only(readings, "RP-0002").field_state, _only(readings, "RP-0002").field_name) \
            == (FIELD_NOT_SINGLE, None)
        assert {_reason(r.outcome) for r in readings} == {REASON_RUN_ABSENT}

    def test_d3_a_recorded_attempt_over_a_history_that_holds_no_such_attempt(self):
        """Both items DO record an attempt, and only one of them names a drawing that was ever
        read. The first resolves against the history; the second is answered by R3's own rule
        for a history holding no such attempt. The port supplies no fallback in either case —
        the missing history is not filled from another drawing, another attempt or another
        item's reading."""
        captures = [_capture_single("A1")]
        read_drawing = j80._item(
            evidence=j80._evidence(candidate_index=0, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),), package_id="RP-0001",
        )
        never_read = j80._item(
            evidence=j80._evidence(
                candidate_index=0, analysis_run_id=RUN_OLD, source_drawing_id=OTHER_DRAWING
            ),
            tasks=(j80._task("T-2"),), package_id="RP-0002",
        )
        readings = _readings(_snapshot(read_drawing, never_read), captures)
        assert _resolved(_only(readings, "RP-0001").outcome).candidate["member_mark"] == "A1"
        # R3 answers this one with its own page-level reason, because the drawing it was asked
        # about has no recorded reading of that page at all. Which of R3's reasons applies is
        # R3's business; the port's claim is only that it carries R3's answer verbatim.
        assert _reason(_only(readings, "RP-0002").outcome) == REASON_EVIDENCE_PAGE_ABSENT
        assert _only(readings, "RP-0002").outcome == resolve_candidate_at_recorded_address(
            never_read, []
        )
        assert not isinstance(_only(readings, "RP-0002").outcome, ConnectionScopedReading)


# ===========================================================================
# E. THE FIELD IS THE TASK'S OWN STATEMENT.
# ===========================================================================
class TestETheFieldBindingIsTheTasksOwn:
    @pytest.mark.parametrize("field", ("connected_member_marks", "plate", "material"))
    def test_e1_a_stated_field_is_carried_verbatim(self, field):
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name=field),),
        )
        reading = _only(_readings(_snapshot(item), captures))
        assert reading.field_state == FIELD_BOUND
        assert reading.field_name == field

    def test_e2_a_task_that_states_no_field_is_not_given_one(self):
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        reading = _only(_readings(_snapshot(item), captures))
        assert reading.field_state == FIELD_NOT_SINGLE
        assert reading.field_name is None
        assert isinstance(reading.outcome, ConnectionScopedReading)

    def test_e3_moving_the_task_type_and_the_answer_type_moves_nothing(self):
        """Every other statement the task carries is changed. The field is the task's own J79
        value and is read from it alone, so none of them can move it."""
        captures = _two_attempt_captures()
        first = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name="plate",
                             task_type=ex.TASK_PROVIDE_PLATE,
                             answer_type=ex.ANSWER_PLATE_VALUE),),
        )
        second = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name="plate",
                             task_type=ex.TASK_CONFIRM_AUTOMATION,
                             answer_type=ex.ANSWER_ACKNOWLEDGMENT,
                             current_ai_value={"anything": "at all"},
                             blocker_codes=("X",)),),
        )
        assert _only(_readings(_snapshot(first), captures)).field_name == "plate"
        assert _only(_readings(_snapshot(second), captures)).field_name == "plate"
        assert _only(_readings(_snapshot(second), captures)).field_state == FIELD_BOUND

    def test_e4_the_presentation_layers_derivation_is_used_nowhere(self):
        """J79 replaced the derivation from the task type with the task's own statement. The
        port and the boundary it calls must not reach back for it: neither `ReviewTaskInfo`
        nor its `field` mapping is named in either module's code."""
        for source in (PORT_SOURCE, CONSUMER.read_text(encoding="utf-8")):
            code = _code_only(source)
            assert "ReviewTaskInfo" not in code
            assert "_TASK_FIELDS" not in code
            assert "_task_info" not in code

    def test_e5_the_identity_task_states_no_field_and_gets_none_invented(self):
        """The presentation layer's derivation names `connection_id` for the identity task,
        and `connection_id` is not an engineering field at all. A stored row that states
        nothing for it reads as `FIELD_NOT_SINGLE` — the port does not add the invented
        name back."""
        assert "connection_id" not in ENGINEERING_FIELDS
        assert contract._TASK_FIELDS[ex.TASK_PROVIDE_CONNECTION_IDENTITY] == "connection_id"
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", task_type=ex.TASK_PROVIDE_CONNECTION_IDENTITY),),
        )
        reading = _only(_readings(_snapshot(item), captures))
        assert reading.field_state == FIELD_NOT_SINGLE
        assert reading.field_name is None

    def test_e6_a_stated_field_outranks_the_task_type_it_is_stored_against(self):
        """The mirror of e5: a row that says `plate` against the identity task type is read as
        `plate`. The task's own statement wins because it is the only thing read."""
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name="plate",
                             task_type=ex.TASK_PROVIDE_CONNECTION_IDENTITY),),
        )
        assert _only(_readings(_snapshot(item), captures)).field_name == "plate"

    def test_e7_every_reported_field_is_in_the_vocabulary_or_absent(self):
        """The closed set is the review package's, and this boundary neither widens nor
        narrows it: across every task type the port reports a member of it or nothing."""
        captures = _two_attempt_captures()
        items = [
            j80._item(
                evidence=j80._evidence(candidate_index=0, analysis_run_id=RUN_OLD),
                tasks=(j80._task("T-1", field_name=None, task_type=task_type),),
                package_id=f"RP-{index:04d}",
            )
            for index, task_type in enumerate(contract._TASK_FIELDS.keys())
        ]
        readings = _readings(_snapshot(*items), captures)
        assert len(readings) == len(contract._TASK_FIELDS)
        for reading in readings:
            assert reading.field_state in FIELD_STATES
            if reading.field_state == FIELD_BOUND:
                assert reading.field_name in ENGINEERING_FIELDS
            else:
                assert reading.field_name is None

    def test_e8_the_result_carries_no_derived_verdict(self):
        """There is no `ok`, no `resolved`, no `status` and no derived summary on the wrapper:
        the three facts stay three, so the two interesting combinations stay tellable apart."""
        for derived in ("ok", "resolved", "status", "success", "is_resolved", "summary"):
            assert derived not in {field.name for field in _dataclass_fields(port.RecordedFieldReading)}
        assert {field.name for field in _dataclass_fields(port.RecordedFieldReading)} == {
            "review_package_id", "connection_id", "reading",
        }


def _dataclass_fields(cls):
    import dataclasses
    assert dataclasses.is_dataclass(cls)
    return dataclasses.fields(cls)


# ===========================================================================
# F. THE SUBSTITUTION IS CAUGHT — a mutation control against the real module.
# ===========================================================================
_CURRENT_READING = '''
def _current_reading(item, task_id, captures):
    """The substitution this milestone must not perform: answer at the CURRENT attempt."""
    from app.engineering_data.page_extraction_capture import authoritative_captures
    evidence = dict(getattr(item, "evidence", {}) or {})
    for row in authoritative_captures(captures):
        if (row["drawing_id"] == evidence.get("source_drawing_id")
                and row["page_number"] == evidence.get("source_page")):
            item = dataclasses.replace(
                item, evidence=dict(evidence, analysis_run_id=row["analysis_run_id"])
            )
    return resolve_cited_reading(item, task_id, captures)
'''

_FIELD_LESS_READING = '''
def _field_less_reading(item, task_id, captures):
    """The derivation J79 replaced: no task states a field, so none is bound."""
    stripped = dataclasses.replace(
        item, tasks=tuple(dict(row, field_name=None) for row in item.tasks)
    )
    return resolve_cited_reading(stripped, task_id, captures)
'''


def _mutant(helper_source: str, *, call: str):
    """The real port with ONE exact edit: the reading comes from a different function.

    Source-level and exec'd in isolation, so the property under test is removed from the real
    module's behaviour rather than faked by a stub that shares none of its structure.
    """
    source = PORT_SOURCE.replace(
        "    reading=resolve_cited_reading(item, task_id, captures),", f"    reading={call},"
    )
    assert source != PORT_SOURCE, "the mutation did not apply"
    # Appended, not prepended: `from __future__` must stay the file's first statement, and a
    # module-level function defined after the module body has the same globals available.
    source = source.rstrip("\n") + "\n\n\n" + helper_source
    module = type(__import__("sys"))(f"j81_mutant_{call.strip('_()')}")
    module.__dict__["__file__"] = str(PORT)
    import sys as _sys
    _sys.modules[module.__name__] = module
    try:
        exec(compile(source, str(PORT), "exec"), module.__dict__)
    finally:
        _sys.modules.pop(module.__name__, None)
    return module


class TestFTheSubstitutionIsCaught:
    def _fixture(self):
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1", field_name="plate"),),
        )
        return captures, item

    def test_f1_the_current_attempt_mutant_answers_the_other_attempt(self):
        captures, item = self._fixture()
        real = _only(_readings(_snapshot(item), captures))
        mutant = _mutant(_CURRENT_READING, call="_current_reading(item, task_id, captures)")
        changed = _only(
            mutant.read_recorded_field_readings(
                _snapshot(item), captures_for_drawing=_loader(captures)
            )
        )
        assert _resolved(real.outcome).analysis_run_id == RUN_OLD
        assert _resolved(real.outcome).candidate["member_mark"] == "B2"
        assert _resolved(changed.outcome).analysis_run_id == RUN_NEW
        assert _resolved(changed.outcome).candidate["member_mark"] == "X9"
        assert changed.outcome != real.outcome

    def test_f2_the_current_attempt_mutant_uses_the_real_selection_rule(self):
        """The control is only a control because the function it calls is genuine: the module
        the mutant reaches for is the storage layer's own, and the answer it produces is the
        one the current attempt holds. If the fixture ever stopped discriminating — both
        attempts holding the same mark — f1 would pass vacuously; this asserts the marks
        differ, so it cannot."""
        captures, item = self._fixture()
        assert captures[0]["payload"]["raw_connections"][1]["member_mark"] == "B2"
        assert captures[1]["payload"]["raw_connections"][1]["member_mark"] == "X9"

    def test_f3_the_field_derivation_mutant_loses_the_bound_field(self):
        """The second control: a port that answered with the pre-J79 behaviour — no task
        stating a field — reports `FIELD_NOT_SINGLE` where the real port reports the task's
        own `plate`."""
        captures, item = self._fixture()
        real = _only(_readings(_snapshot(item), captures))
        mutant = _mutant(_FIELD_LESS_READING, call="_field_less_reading(item, task_id, captures)")
        changed = _only(
            mutant.read_recorded_field_readings(
                _snapshot(item), captures_for_drawing=_loader(captures)
            )
        )
        assert (real.field_state, real.field_name) == (FIELD_BOUND, "plate")
        assert (changed.field_state, changed.field_name) == (FIELD_NOT_SINGLE, None)
        assert changed.outcome == real.outcome, "only the field half may move"

    def test_f4_the_real_port_is_unchanged_by_the_controls(self):
        """The mutants are exec'd copies in their own module objects, and the module the tests
        import is not one of them: the real function is still the real function, and the file
        on disk still holds the source the controls were built from."""
        captures, item = self._fixture()
        mutant = _mutant(_CURRENT_READING, call="_current_reading(item, task_id, captures)")
        assert mutant is not port
        assert mutant.read_recorded_field_readings is not port.read_recorded_field_readings
        assert port.read_recorded_field_readings.__module__ == port.__name__
        assert PORT.read_text(encoding="utf-8") == PORT_SOURCE
        assert _resolved(_only(_readings(_snapshot(item), captures)).outcome).candidate[
            "member_mark"
        ] == "B2"


# ===========================================================================
# G. NOTHING IS WRITTEN.
# ===========================================================================
class TestGNothingIsWritten:
    def test_g1_the_port_reads_no_database_and_writes_nothing(self):
        code = _code_only(PORT_SOURCE)
        for forbidden in (
            "insert(", "update(", "upsert(", "delete(", "rpc(", "table(", ".select(",
            "supabase", "execute(", "storage", "upload(", "download(", "from_(",
        ):
            assert forbidden not in code, forbidden

    def test_g2_the_port_reaches_no_model_no_file_and_no_fabrication_module(self):
        code = _code_only(PORT_SOURCE)
        for forbidden in (
            "anthropic", "openai", "httpx", "requests.", "subprocess", "pypdf",
            "production_fabrication", "fabrication", "artifact",
        ):
            assert forbidden not in code, forbidden

    def test_g3_reading_creates_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        _readings(_snapshot(item), captures)
        assert list(tmp_path.iterdir()) == []

    def test_g4_the_call_mutates_neither_the_snapshot_nor_the_history(self):
        captures = _two_attempt_captures()
        item = j80._item(
            evidence=j80._evidence(candidate_index=1, analysis_run_id=RUN_OLD),
            tasks=(j80._task("T-1"),),
        )
        snapshot = _snapshot(item)
        before = copy.deepcopy((snapshot, captures))
        _readings(snapshot, captures)
        assert (snapshot, captures) == before

    def test_g5_the_whole_read_path_through_the_real_route_writes_nothing(
        self, production, monkeypatch
    ):
        """The strongest available form of "the binding writes nothing": the REAL route is
        requested with the write tripwires armed and a read-only store client, over a project
        whose RECORDED items cite a drawing — so all three joins run, including J81's.

        The store double answers the route's read with the genuine persisted fixture and
        records the read sequence; a write would raise from the double's own `__getattr__`
        before the tripwires were even reached. The capture seam is substituted by a recording
        loader so the J81 read is visible here — the point being that it IS a read.
        """
        persisted = _persisted_store(j25.SELBY_PROJECT)
        store = j25._ReadOnlyStore(tables=persisted.tables)
        surface = j25._surface(
            production, monkeypatch, store=j24a._selby_store(),
            projects={j25.SELBY_PROJECT: j25._project_row(j25.SELBY_PROJECT, j25.SELBY_OWNER)},
            owner=j25.SELBY_OWNER, recorded=store,
        )
        asked: list[str] = []
        monkeypatch.setattr(
            workflow_review, "capture_history_for_drawing",
            _loader(_two_attempt_captures(), asked=asked),
        )
        response = surface.http.get(surface.url())
        assert response.status_code == 200, response.text
        assert asked, "the J81 read did not run on this request; this proof would be vacuous"
        # Every call is a gate of the read sequence. The double's own `__getattr__` raises with
        # the write method's name, so a write could not appear here as anything else.
        assert store.calls, "the route did not read the recorded band at all"
        assert {call[0] for call in store.calls} == {"table", "select", "eq"}, store.calls

    def test_g6_the_read_path_names_neither_the_revision_writer_nor_the_row_writer(self):
        """Stated as the two objects it would leave behind if it wrote — a second revision and
        a recorded field-evidence row — and neither writer's name occurs anywhere in the read
        entry or the port. `test_g5` is the behavioural half of the same claim; this is the
        structural one, and it holds for the whole module rather than for one request."""
        assert review_store.WRITER_FUNCTION not in READ_ENTRY.read_text(encoding="utf-8")
        assert review_store.CITATION_WRITER_FUNCTION not in READ_ENTRY.read_text(encoding="utf-8")
        assert review_store.WRITER_FUNCTION not in PORT_SOURCE
        assert review_store.CITATION_WRITER_FUNCTION not in PORT_SOURCE
        for source in (PORT_SOURCE, READ_ENTRY.read_text(encoding="utf-8")):
            assert "persist_review_snapshot" not in _code_only(source)
            assert "record_project_review" not in _code_only(source)

    def test_g7_the_frozen_migrations_are_unchanged(self):
        found = sorted(path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert found == sorted(PINNED_MIGRATIONS), found

    def test_g8_no_migration_names_this_milestone(self):
        for path in (REPO / "supabase" / "migrations").glob("*.sql"):
            assert "j81" not in path.read_text(encoding="utf-8").lower(), path.name

    def test_g9_the_j66_three_module_fence_still_holds_with_this_port_present(self):
        """The port is a fourth module in this area of the tree, so the fence it must fit is
        re-asserted from here rather than assumed."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/cad_engine/review_contract.py",
            "app/engineering_data/connection_review_repository.py",
        ], naming


# ===========================================================================
# H. THE ROUTE BOUNDARY — the mutation route is not wired to any of this.
# ===========================================================================
class TestHTheRouteBoundary:
    def test_h1_the_route_set_is_unchanged(self, production):
        assert j20._declared_routes(production.main.app) == j20.FROZEN_ROUTES

    def test_h2_no_route_path_names_any_part_of_this_milestone(self, production):
        paths = [path for _, path in j20._declared_routes(production.main.app)]
        assert not any("recorded" in path.lower() for path in paths), paths
        assert not any("reading" in path.lower() for path in paths), paths

    def test_h3_the_resolution_mutation_route_names_no_part_of_this_milestone(self):
        """The route that takes a human decision is the one that must not have grown a
        reader. Its source is read, not its behaviour, so this holds whether or not the route
        is ever exercised."""
        source = RESOLUTION_ROUTE.read_text(encoding="utf-8")
        for forbidden in (
            "production_recorded_readings",
            "cited_candidate_resolution",
            "resolve_cited_reading",
            "read_recorded_field_readings",
            "capture_history_for_drawing",
        ):
            assert forbidden not in source, forbidden

    def test_h4_the_read_entry_does_not_reach_the_resolution_route(self):
        source = READ_ENTRY.read_text(encoding="utf-8")
        for forbidden in ("resolve_project_connection", "record_project_review",
                          "refresh_project_workflow", "resolve_project_connection_from_revision"):
            assert forbidden not in _code_only(source), forbidden

    def test_h5_the_port_has_exactly_one_caller_and_it_is_the_read_entry(self):
        """The list is exact, so a second production module reaching the port — a route, a
        service, a pipeline — has to be declared here rather than absorbed."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "production_recorded_readings" in path.read_text(encoding="utf-8")
        )
        assert naming == ["app/production_connection_review.py"], naming


# ===========================================================================
# 8. THE FENCES WERE DECLARED, NOT WEAKENED.
# ===========================================================================
class TestTheFencesWereDeclared:
    def test_every_fence_that_claimed_no_caller_now_names_this_one(self):
        """J81 is the first production caller of the J80 boundary, so six guards that asserted
        "no module reaches it" became false by construction. Each was updated to declare the
        caller BY IDENTITY rather than to drop the assertion. This test checks that every one
        of them actually carries the declaration — a fence silently left widened, or simply
        deleted, fails here."""
        for path in DECLARED_FENCES:
            assert PORT_PATH in path.read_text(encoding="utf-8"), path.name

    def test_the_declarations_are_exact_rather_than_loosened(self):
        """Every declaration is an `== [...]` against a ONE-ELEMENT list holding this caller —
        never an `in`, a `startswith`, a length check or a relaxed pattern. A guard that
        admitted this caller without excluding a second one would show up here as an
        occurrence of the module name that is not preceded by `== [`.

        The other guards in these same files (`importers == []`, `reaching_it == []`, …) guard
        OTHER modules and are untouched: this checks the J81 declaration, not every assertion
        in the file, because a fence for J66's or J68's boundary has no business moving for
        this milestone.
        """
        for path in DECLARED_FENCES:
            code = _code_only(path.read_text(encoding="utf-8"))
            assert PORT_PATH in code, f"{path.name} does not declare the J81 caller at all"
            for line in code.splitlines():
                if PORT_PATH not in line:
                    continue
                assert _is_exact_single_element_comparison(line), (path.name, line)


def _is_exact_single_element_comparison(line: str) -> bool:
    """`x == ['app/production_recorded_readings.py']` — either quote style, nothing else.

    `ast.unparse` normalises the quotes to single, but the claim is about the COMPARISON, not
    the punctuation: one element, equality, no membership, no length, no prefix test.
    """
    import re

    return bool(re.fullmatch(
        r"\s*(?:assert\s+)?[A-Za-z_][\w.\[\]()]*\s*==\s*\[\s*['\"]" + re.escape(PORT_PATH)
        + r"['\"]\s*\](?:\s*,\s*[A-Za-z_][\w.]*)?\s*",
        line,
    ))

    def test_the_port_is_reachable_from_a_real_read_entry_and_nowhere_else(self):
        """One caller, and it is a READ entry: the port is not named by a route module, a
        pipeline, a service or a test double outside these proofs."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "production_recorded_readings" in path.read_text(encoding="utf-8")
        )
        assert naming == ["app/production_connection_review.py"], naming


# ===========================================================================
# I. THE LIVE DEPLOYMENT, READ-ONLY.
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


class TestITheLiveDeployment:
    def test_i1_the_real_project_exists_and_the_transposition_does_not(self, live_client):
        assert len(_rows(live_client, "projects", "id", id=PROJECT)) == 1
        assert _rows(live_client, "projects", "id", id=BRIEF_PROJECT) == []

    def test_i2_revision_zero_holds_three_items_and_no_item_records_an_attempt(self, live_client):
        items = _rows(live_client, crs.ITEM_TABLE, "review_package_id,evidence", project_id=PROJECT)
        assert len(items) == 3, items
        assert sorted(row["review_package_id"] for row in items) == [
            "RP-0001", "RP-0002", "RP-0003",
        ]
        for row in items:
            evidence = row["evidence"] or {}
            assert "candidate_index" not in evidence, evidence
            assert "analysis_run_id" not in evidence, evidence

    def test_i3_the_live_read_path_answers_every_task_with_its_own_two_halves(self, live_client):
        """The §9 proof, live: the production read is performed exactly as a request performs
        it — J22's own store read, then J23's own whole-history loader — and every one of the
        live tasks is answered. The candidate half is R3's `RUN_ABSENT` for all of them,
        because no live item recorded an attempt; the field half is `FIELD_NOT_SINGLE` for all
        of them, because no live task stated a field. Both halves are read, neither is
        invented, and the two facts are reported separately.
        """
        state = workflow_review.recorded_review_state(PROJECT)
        assert state.snapshot is not None, state
        assert state.snapshot.review_revision == 0

        readings = workflow_review.read_recorded_field_readings(
            state.snapshot, captures_for_drawing=workflow_review.capture_history_for_drawing,
        )
        assert len(readings) == 28, len(readings)
        assert {r.review_package_id for r in readings} == {"RP-0001", "RP-0002", "RP-0003"}
        assert {r.field_state for r in readings} == {FIELD_NOT_SINGLE}
        assert {r.field_name for r in readings} == {None}
        assert {_reason(r.outcome) for r in readings} == {REASON_RUN_ABSENT}
        for reading in readings:
            assert reading.outcome.reason_code in (REASON_RUN_ABSENT,)

    def test_i4_the_live_history_the_port_is_given_is_not_empty(self, live_client):
        """The proof above would be vacuous if the drawing had no recorded reading at all —
        `RUN_ABSENT` is only a statement about the ITEM if there was a history to look in."""
        items = _rows(live_client, crs.ITEM_TABLE, "evidence", project_id=PROJECT)
        total = 0
        for row in items:
            drawing_id = (row["evidence"] or {}).get("source_drawing_id")
            if drawing_id:
                total += len(workflow_review.capture_history_for_drawing(drawing_id))
        assert total > 0, "no reading history is recorded for the project's drawings"

    def test_i5_no_field_evidence_row_exists_and_the_fabrication_pointer_is_null(self, live_client):
        assert _rows(live_client, CITATION_TABLE, "project_id") == []
        rows = _rows(live_client, "projects", f"id,{POINTER_COLUMN}", id=PROJECT)
        assert rows and rows[0][POINTER_COLUMN] is None, rows
        try:
            objects = live_client.storage.from_(FABRICATION_BUCKET).list()
        except Exception as exc:
            pytest.skip(f"the fabrication bucket could not be listed: {type(exc).__name__}")
        assert objects == [], objects

    def test_i6_the_live_read_left_the_store_where_it_found_it(self, live_client):
        """Read after the reads above: the revision chain, the item count and the task count
        are what they were. Nothing the live proof did was a write, and this is the assertion
        that would catch it if one had been."""
        headers = _rows(live_client, crs.SNAPSHOT_TABLE, "review_revision", project_id=PROJECT)
        assert sorted(row["review_revision"] for row in headers) == [0]
        items = _rows(live_client, crs.ITEM_TABLE, "tasks", project_id=PROJECT)
        assert sum(len(row["tasks"] or []) for row in items) == 28


# ===========================================================================
# The brief's own id, pinned as a fact rather than left as a claim in a docstring.
# ===========================================================================
class TestTheBriefsProjectId:
    def test_the_briefs_id_is_two_digits_swapped_and_is_not_the_real_one(self):
        assert BRIEF_PROJECT != PROJECT
        assert sorted(BRIEF_PROJECT) == sorted(PROJECT)
        assert BRIEF_PROJECT.split("-")[:2] == PROJECT.split("-")[:2]
        assert BRIEF_PROJECT.split("-")[2:] != PROJECT.split("-")[2:]
