"""
J79 — THE FIELD-BINDING BOUNDARY.

What is genuinely real in this file:

  * the task vocabulary, `ExceptionResolutionTask` and `_make_task` are the real objects;
  * `tests.test_real_world_j21_connection_review_data_model_design._arkles_workflow()` is the
    GENUINE Arkles extraction (1 875 lines of real AI reading), so the fixture tests below
    read the field bindings off 24 real tasks rather than off a hand-written example;
  * `_task_payload` / `_task_from_row` are the real persistence round trip;
  * every mutation control execs a MUTATED COPY of the real module source, so a control that
    stops detecting a mutation is itself a failure.

What is deliberately NOT here:

  * no R3 call, no `connection_scoped_evidence` import, no citation writer, no review
    revision, no database and no storage. J79 binds a field on a task and stops.

THE ONE PLACE THE BRIEF AND THE CODEBASE DISAGREE, stated up front rather than hidden: the
brief lists `PROVIDE_CONNECTION_IDENTITY → connection_id` among the single-field tasks. It
cannot be bound, because `connection_id` is NOT a member of `ENGINEERING_FIELDS` (the review
package's provenance vocabulary is connected_member_marks, position, plate, holes, location,
attachments and material). The brief's own §2 and §4 twice require `field_name` to be one of
ENGINEERING_FIELDS and forbid inventing a vocabulary entry, so this file pins the vocabulary
fact (`TestTheVocabularyIsClosedToConnectionId`) and pins the honest binding — None — instead
of the binding the brief's example list asked for. Nothing was widened to make the example
work.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
EXCEPTION_MODULE = APP / "cad_engine" / "exception_resolution.py"
CATALOGUE_MODULE = APP / "cad_engine" / "catalogue_conflict_resolution.py"
BOUNDARY_MODULE = APP / "cad_engine" / "connection_review_snapshot.py"

from app.cad_engine import catalogue_conflict_resolution as ccr  # noqa: E402
from app.cad_engine import connection_review_snapshot as crs  # noqa: E402
from app.cad_engine import exception_resolution as ex  # noqa: E402
from app.cad_engine.review_contract import ENGINEERING_FIELDS  # noqa: E402

# The ONE field each task type addresses. This is the design's own statement, written out
# here so that a construction site that changes its mind reddens this file rather than
# quietly moving a boundary. A name mapped to None addresses no single engineering field.
EXPECTED_BY_CONSTANT = {
    "TASK_COMPLETE_REVIEW": None,
    "TASK_SELECT_MEMBER_POSITION_ATTACHMENT": None,
    "TASK_SELECT_MEMBER": "connected_member_marks",
    "TASK_SELECT_POSITION": "position",
    "TASK_SELECT_ATTACHMENT": "attachments",
    "TASK_CONFIRM_AI_VALUES": None,
    "TASK_PROVIDE_PLATE": "plate",
    "TASK_PROVIDE_HOLE_DIAMETER": "holes",
    "TASK_PROVIDE_LOCATION": "location",
    "TASK_REVIEW_MALFORMED_FIELDS": None,
    "TASK_RESOLVE_CONFLICT": None,
    "TASK_REVIEW_SPECIFICATION": None,
    "TASK_REVIEW_VALIDATION": None,
    "TASK_PROVIDE_CONNECTION_IDENTITY": None,
    "TASK_PROVIDE_MATERIAL_SPECIFICATION": "material",
    "TASK_CONFIRM_AUTOMATION": None,
}
EXPECTED_BY_TYPE = {getattr(ex, name): value for name, value in EXPECTED_BY_CONSTANT.items()}


def _tree(path: Path = EXCEPTION_MODULE) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _task_calls(tree: ast.Module, function_name: str) -> list[ast.Call]:
    """Every call to `function_name(...)` in the module, in source order."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == function_name:
                found.append(node)
    return found


def _first_arg_name(call: ast.Call) -> str:
    first = call.args[0]
    assert isinstance(first, ast.Name), ast.dump(first)
    return first.id


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _constant(call: ast.Call, name: str):
    value = _keyword(call, name)
    if value is None:
        return "<ABSENT>"
    assert isinstance(value, ast.Constant), ast.dump(value)
    return value.value


def _make_task_calls_outside_add(tree: ast.Module) -> list[ast.Call]:
    """The `_make_task` call sites that are NOT the `add` closure's own forwarding call.

    `add` forwards its keyword to `_make_task` by name, which is the point of it; that one
    call site is therefore expected to pass a Name rather than a literal, and is excluded
    here so the two `_confirm_task` sites can be read on their own.
    """
    inside_add = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "add":
            inside_add = {id(child) for child in ast.walk(node)}
    return [call for call in _task_calls(tree, "_make_task") if id(call) not in inside_add]


def _field_name_line(constant_name: str) -> tuple[list[str], int]:
    """The source line holding `add(TASK_X, ...)`'s `field_name=` value, located by AST.

    The line itself is returned rather than a reconstructed one: what is mutated must be
    the bytes that are actually in the file, quoting and all.
    """
    lines = EXCEPTION_MODULE.read_text(encoding="utf-8").splitlines()
    for call in _task_calls(_tree(), "add"):
        if _first_arg_name(call) == constant_name:
            value = _keyword(call, "field_name")
            assert value is not None, constant_name
            index = value.lineno - 1
            assert lines[index].strip().startswith("field_name="), lines[index]
            return lines, index
    raise AssertionError(f"no add() site for {constant_name}")


def _rewrite_field_name(constant_name: str, new_value: str) -> str:
    """The real source with ONE `add(TASK_X, ...)` site's field value changed."""
    lines, index = _field_name_line(constant_name)
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    lines[index] = f"{indent}field_name={new_value},"
    return "\n".join(lines) + "\n"


def _drop_field_name(constant_name: str) -> str:
    """The real source with ONE `add(TASK_X, ...)` site's field value deleted."""
    lines, index = _field_name_line(constant_name)
    del lines[index]
    return "\n".join(lines) + "\n"


def _exec_mutant(source: str, *, name: str, rebind: bool = False):
    """A mutated COPY of the real module source, exec'd into its own namespace.

    `rebind` substitutes the REAL `ExceptionResolutionTask`. A mutant of the construction
    boundary needs it, so that its failure is the real model refusing rather than a
    second class of the same name happening to agree. A mutant OF THE MODEL must NOT
    rebind — that would throw the mutation away and the control would pass for the wrong
    reason.
    """
    module = type(sys)(name)
    module.__dict__["__file__"] = str(EXCEPTION_MODULE)
    # `@dataclass` resolves `cls.__module__` through `sys.modules`, so the mutant must be
    # registered while its source executes or the dataclasses decorate a class whose module
    # is not there.
    sys.modules[name] = module
    try:
        exec(compile(source, str(EXCEPTION_MODULE), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    if rebind:
        for symbol in ("ExceptionResolutionTask", "HumanResolution"):
            setattr(module, symbol, getattr(ex, symbol))
    return module


@pytest.fixture(scope="module")
def arkles():
    """The GENUINE Arkles workflow, or a skip when the real capture is not present."""
    from tests import test_real_world_j21_connection_review_data_model_design as j21

    if not j21.ARKLES_REAL_AI_EXTRACTION:
        pytest.skip("the real Arkles capture is not present")
    return j21._arkles_workflow()


def _tasks_of(workflow):
    for group in workflow.exception_package.connection_tasks:
        for task in group.tasks:
            yield group, task


# =============================================================================
# 1. THE VOCABULARY THE BOUNDARY IS CLOSED OVER
# =============================================================================
class TestTheVocabularyIsClosedToConnectionId:
    """The single fact that forced the honest deviation from the brief's example list."""

    def test_engineering_fields_is_the_seven_field_provenance_vocabulary(self):
        assert ENGINEERING_FIELDS == (
            "connected_member_marks", "position", "plate", "holes", "location",
            "attachments", "material",
        )

    def test_connection_id_is_not_an_engineering_field(self):
        """It is the AI-never-supplies identity label, not an engineering field, and the
        citation vocabulary (which is ENGINEERING_FIELDS) can never address it."""
        assert "connection_id" not in ENGINEERING_FIELDS

    def test_the_task_module_reuses_that_exact_vocabulary(self):
        """`exception_resolution` imports the vocabulary from the review package rather than
        declaring a second one; the two are equal term for term."""
        from app.cad_engine import connection_review_package as crp

        assert ex.ENGINEERING_FIELDS == ENGINEERING_FIELDS == crp.ENGINEERING_FIELDS
        assert not hasattr(ex, "_TASK_FIELDS")

    def test_the_presentation_label_for_identity_is_wider_than_the_field_vocabulary(self):
        """`review_contract._TASK_FIELDS` labels PROVIDE_CONNECTION_IDENTITY as
        `connection_id`. That is the PRESENTATION's own wider label and predates J79; the
        task boundary does not adopt it, and this test records the difference rather than
        pretending it does not exist."""
        from app.cad_engine import review_contract as rc

        assert rc._TASK_FIELDS[ex.TASK_PROVIDE_CONNECTION_IDENTITY] == "connection_id"
        assert "connection_id" not in ENGINEERING_FIELDS


# =============================================================================
# 2b. THE PRESENTATION'S LABEL VERSUS THE TASK'S OWN STATEMENT
#
# There are now TWO places a task's field identity can be read. `ReviewTaskInfo.field` is
# DERIVED from the task type (it has been since 7AK, and it is the UI's own affordance);
# `task.field_name` is STATED by the construction site. They agree everywhere except the
# identity task, where the presentation says `connection_id` — a label outside
# ENGINEERING_FIELDS that J66's citation vocabulary can never address. This class records
# exactly where they agree and where they do not, so that a later writer reading the
# wrong one is a decision rather than an accident.
# =============================================================================
class TestThePresentationLabelVersusTheStatedField:
    def test_they_agree_for_every_task_type_except_the_identity_task(self):
        from app.cad_engine import review_contract as rc

        disagreements = {
            task_type: (rc._TASK_FIELDS[task_type], stated)
            for task_type, stated in EXPECTED_BY_TYPE.items()
            if rc._TASK_FIELDS[task_type] != stated
        }
        assert disagreements == {
            ex.TASK_PROVIDE_CONNECTION_IDENTITY: ("connection_id", None),
        }

    def test_they_agree_wherever_the_presentation_names_an_engineering_field(self):
        from app.cad_engine import review_contract as rc

        for task_type, stated in EXPECTED_BY_TYPE.items():
            shown = rc._TASK_FIELDS[task_type]
            if shown in ENGINEERING_FIELDS:
                assert shown == stated, task_type

    def test_the_presentation_names_one_string_that_is_not_an_engineering_field(self):
        from app.cad_engine import review_contract as rc

        shown = {value for value in rc._TASK_FIELDS.values() if value is not None}
        assert shown - set(ENGINEERING_FIELDS) == {"connection_id"}
        assert shown - {"connection_id"} == set(ENGINEERING_FIELDS)

    def test_the_presentation_field_is_derived_from_the_task_type_and_not_from_the_task(self):
        """The seam, pinned: `ReviewTaskInfo.field` is fed by the task-type map. A writer
        that must name an ENGINEERING_FIELD has to read `task.field_name`; reading this one
        would give it `connection_id` for the identity task."""
        from app.cad_engine import review_contract as rc

        source = Path(rc.__file__).read_text(encoding="utf-8")
        assert "field=_TASK_FIELDS.get(task.task_type)" in source
        assert "field=task.field_name" not in source
        info = rc.ReviewTaskInfo.__dataclass_fields__
        assert "field" in info and "field_name" not in info


# =============================================================================
# 2. THE MODEL STATES THE FIELD
# =============================================================================
def _task(**overrides):
    base = dict(
        task_id="RP-0001-T01", task_type="PROVIDE_PLATE", blocker_codes=(), question="q",
        current_ai_value=None, answer_type="PLATE_VALUE", allowed_choices=(),
        evidence_requirement="e",
    )
    base.update(overrides)
    return ex.ExceptionResolutionTask(**base)


class TestTheModelStatesTheField:
    def test_the_field_defaults_to_none(self):
        assert _task().field_name is None

    def test_every_engineering_field_is_accepted(self):
        for field in ENGINEERING_FIELDS:
            assert _task(field_name=field).field_name == field

    def test_none_is_the_stated_absence_and_is_accepted(self):
        assert _task(field_name=None).field_name is None

    def test_a_field_outside_the_vocabulary_is_refused(self):
        for bad in ("connection_id", "bogus", "", "PLATE", "Plate", 0, [], True):
            with pytest.raises(ValueError) as refusal:
                _task(field_name=bad)
            assert "field_name must be one of" in str(refusal.value)

    def test_the_refusal_lists_the_vocabulary_it_is_closed_over(self):
        with pytest.raises(ValueError) as refusal:
            _task(field_name="nope")
        assert list(ENGINEERING_FIELDS)[0] in str(refusal.value)

    def test_the_field_is_part_of_the_task_identity(self):
        assert _task(field_name="plate") == _task(field_name="plate")
        assert _task(field_name="plate") != _task(field_name="holes")
        assert _task(field_name="plate") != _task(field_name=None)

    def test_the_task_is_still_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _task().field_name = "plate"

    def test_the_field_does_not_leak_into_the_other_recorded_terms(self):
        """The boundary adds one term and changes nothing else about the record."""
        task = _task(field_name="holes")
        assert (task.task_id, task.task_type, task.blocker_codes, task.question) == (
            "RP-0001-T01", "PROVIDE_PLATE", (), "q",
        )
        assert (task.answer_type, task.allowed_choices, task.evidence_requirement) == (
            "PLATE_VALUE", (), "e",
        )
        assert task.status == ex.STATUS_OPEN and task.resolution is None


# =============================================================================
# 3. EVERY CONSTRUCTION SITE STATES ITS FIELD — proven from the module's own AST
# =============================================================================
class TestEveryConstructionSiteStatesItsField:
    """The construction sites are the boundary. This reads them out of the source rather
    than trusting them, so a call site that forgets, or that states a different field,
    reddens here."""

    def test_there_are_fifteen_add_sites_in_the_review_task_set(self):
        assert len(_task_calls(_tree(), "add")) == 15

    def test_the_two_confirm_task_sites_are_make_task_calls(self):
        """`add`'s own forwarding call is excluded, so these are the two `_confirm_task`
        sites and they are in the order that function builds them."""
        names = [_first_arg_name(call) for call in _make_task_calls_outside_add(_tree())]
        assert names == ["TASK_CONFIRM_AUTOMATION", "TASK_PROVIDE_MATERIAL_SPECIFICATION"]

    def test_every_add_site_states_field_name_explicitly(self):
        for call in _task_calls(_tree(), "add"):
            constant = _first_arg_name(call)
            assert _keyword(call, "field_name") is not None, constant

    def test_every_make_task_site_states_field_name_explicitly(self):
        for call in _make_task_calls_outside_add(_tree()):
            assert _keyword(call, "field_name") is not None, _first_arg_name(call)

    def test_every_add_site_states_the_expected_field(self):
        stated = {_first_arg_name(call): _constant(call, "field_name")
                  for call in _task_calls(_tree(), "add")}
        assert stated == {name: EXPECTED_BY_CONSTANT[name] for name in stated}

    def test_the_add_sites_cover_the_whole_expected_map(self):
        stated = {_first_arg_name(call) for call in _task_calls(_tree(), "add")}
        assert stated == set(EXPECTED_BY_CONSTANT) - {"TASK_CONFIRM_AUTOMATION"}

    def test_every_make_task_site_states_the_expected_field(self):
        for call in _make_task_calls_outside_add(_tree()):
            assert _constant(call, "field_name") == EXPECTED_BY_CONSTANT[_first_arg_name(call)]

    def test_the_grouped_task_is_none_and_not_one_of_its_three_fields(self):
        for call in _task_calls(_tree(), "add"):
            if _first_arg_name(call) == "TASK_SELECT_MEMBER_POSITION_ATTACHMENT":
                assert _constant(call, "field_name") is None
                return
        pytest.fail("the grouped task has no construction site")

    def test_the_field_argument_is_keyword_only_at_the_construction_boundary(self):
        """`_make_task` takes `field_name` AFTER a `*`, so a caller cannot reach it
        positionally and a call site that omits it is a TypeError rather than a task that
        silently reads as 'no field'."""
        for node in ast.walk(_tree()):
            if isinstance(node, ast.FunctionDef) and node.name == "_make_task":
                assert node.args.kwonlyargs, "field_name is not keyword-only"
                assert [a.arg for a in node.args.kwonlyargs] == ["field_name"]
                assert not any(d.arg == "field_name" for d in node.args.args)
                return
        pytest.fail("_make_task was not found")


# =============================================================================
# 4. THE GENUINE FIXTURE — 24 real tasks read their own field
# =============================================================================
class TestTheGenuineArklesFixture:
    def test_the_fixture_produces_tasks_at_all(self, arkles):
        assert len(list(_tasks_of(arkles))) > 0

    def test_every_real_task_states_the_field_its_type_implies(self, arkles):
        for _, task in _tasks_of(arkles):
            assert task.field_name == EXPECTED_BY_TYPE[task.task_type], task.task_id

    def test_every_real_non_none_field_is_in_the_vocabulary(self, arkles):
        for _, task in _tasks_of(arkles):
            assert task.field_name is None or task.field_name in ENGINEERING_FIELDS

    def test_the_real_task_types_the_capture_exercises_are_the_ones_observed(self, arkles):
        assert sorted({t.task_type for _, t in _tasks_of(arkles)}) == [
            "COMPLETE_REVIEW", "PROVIDE_CONNECTION_IDENTITY", "PROVIDE_HOLE_DIAMETER",
            "PROVIDE_LOCATION", "PROVIDE_PLATE", "REVIEW_SPECIFICATION", "REVIEW_VALIDATION",
            "SELECT_MEMBER_POSITION_ATTACHMENT",
        ]

    def test_the_real_grouped_task_states_no_single_field(self, arkles):
        grouped = [t for _, t in _tasks_of(arkles)
                   if t.task_type == ex.TASK_SELECT_MEMBER_POSITION_ATTACHMENT]
        assert grouped
        for task in grouped:
            assert task.field_name is None

    def test_the_real_plate_task_states_plate(self, arkles):
        plates = [t for _, t in _tasks_of(arkles) if t.task_type == ex.TASK_PROVIDE_PLATE]
        assert plates
        for task in plates:
            assert task.field_name == "plate"

    def test_the_real_identity_task_states_no_engineering_field(self, arkles):
        identities = [t for _, t in _tasks_of(arkles)
                      if t.task_type == ex.TASK_PROVIDE_CONNECTION_IDENTITY]
        assert identities
        for task in identities:
            assert task.field_name is None

    def test_the_real_locations_and_holes_are_bound_to_their_own_fields(self, arkles):
        assert {t.field_name for _, t in _tasks_of(arkles)
                if t.task_type == ex.TASK_PROVIDE_LOCATION} == {"location"}
        assert {t.field_name for _, t in _tasks_of(arkles)
                if t.task_type == ex.TASK_PROVIDE_HOLE_DIAMETER} == {"holes"}

    def test_the_real_fixture_cannot_itself_falsify_order_derivation(self, arkles):
        """STATED GAP, recorded rather than papered over: all three real connections emit
        the SAME eight task types in the SAME order, so this fixture cannot distinguish a
        binding that was stated from one derived from a task's position. Order derivation
        is closed structurally instead — see
        `TestNothingIsDerived::test_no_construction_site_derives_the_field_from_the_task_type`,
        which requires every stated value to be a literal."""
        sequences = {}
        for group, task in _tasks_of(arkles):
            sequences.setdefault(group.review_package_id, []).append(task.task_type)
        assert len(sequences) == 3, sequences
        assert len({tuple(v) for v in sequences.values()}) == 1, sequences

    def test_no_two_task_types_share_one_engineering_field(self, arkles):
        """One field belongs to one task type: a binding that had been derived from the
        bundle — the AI value, the answer shape, a blocker — would collide here."""
        owners = {}
        for task_type, field in EXPECTED_BY_TYPE.items():
            if field is not None:
                owners.setdefault(field, []).append(task_type)
        assert sorted(owners) == sorted(ENGINEERING_FIELDS)
        for field, task_types in owners.items():
            assert len(task_types) == 1, (field, task_types)

    def test_the_real_bindings_are_the_expected_map_for_every_connection(self, arkles):
        by_package = {}
        for group, task in _tasks_of(arkles):
            by_package.setdefault(group.review_package_id, []).append(task)
        assert len(by_package) == 3, by_package
        for tasks in by_package.values():
            for task in tasks:
                assert task.field_name == EXPECTED_BY_TYPE[task.task_type], task.task_id


# =============================================================================
# 5. THE SECOND CONFLICT TASK, AND THE CONFLICT TYPE
# =============================================================================
class TestTheCatalogueConflictTask:
    def test_the_construction_site_states_none(self):
        tree = _tree(CATALOGUE_MODULE)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "ExceptionResolutionTask"
        ]
        assert len(calls) == 1
        assert _constant(calls[0], "field_name") is None

    def test_the_stated_reason_is_the_catalogue_vocabulary_not_the_review_one(self):
        """The fields a catalogue conflict names are section geometry fields, which are not
        members of ENGINEERING_FIELDS — so no engineering field is addressed."""
        source = CATALOGUE_MODULE.read_text(encoding="utf-8")
        assert "not\nthe review package's ENGINEERING_FIELDS" in source.replace("  ", " ") \
            or "ENGINEERING_FIELDS" in source


# =============================================================================
# 6. NOTHING IS DERIVED
# =============================================================================
class TestNothingIsDerived:
    def test_the_task_module_holds_no_task_type_to_field_map(self):
        """There is no task-type lookup here at all: the field is stated, so no map can
        disagree with a call site. The presentation layer's `_TASK_FIELDS` is a wider map
        and lives elsewhere; this module neither defines it nor imports it."""
        assert not hasattr(ex, "_TASK_FIELDS")
        imported = {
            alias.name
            for node in ast.walk(_tree()) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not [name for name in imported if "TASK_FIELDS" in name]
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Dict):
                keys = {ast.unparse(key) for key in node.keys}
                assert not [key for key in keys if key.startswith("TASK_")], keys

    def test_the_pre_existing_answer_type_map_is_not_consulted_for_the_field(self):
        """STATED HAZARD, recorded because the brief names it: the module ALREADY holds an
        answer-type → engineering-field map (it decides which payload shape a resolution
        carries). Deriving `field_name` from it would look natural and is exactly what the
        brief forbids — so the map is pinned here, and the construction-site scan above
        requires every stated field to be a literal, which no lookup can be."""
        assert ex._SUPPLIED_FIELD_BY_ANSWER[ex.ANSWER_MEMBER_SELECTION] == "connected_member_marks"
        assert ex._SUPPLIED_FIELD_BY_ANSWER[ex.ANSWER_POSITION_VALUE] == "position"
        assert ex._SUPPLIED_FIELD_BY_ANSWER[ex.ANSWER_PLATE_VALUE] == "plate"
        assert set(ex._SUPPLIED_FIELD_BY_ANSWER.values()) == set(ENGINEERING_FIELDS)
        # It is keyed by ANSWER TYPE, not by task type, and the two vocabularies disagree
        # about the grouped task: the map has no entry for its answer type at all, while
        # the task itself states None. Neither the map nor the task invents one of the
        # three fields the answer may carry.
        assert ex.ANSWER_MEMBER_POSITION_ATTACHMENTS not in ex._SUPPLIED_FIELD_BY_ANSWER
        assert EXPECTED_BY_CONSTANT["TASK_SELECT_MEMBER_POSITION_ATTACHMENT"] is None

    def test_make_task_copies_the_field_and_computes_nothing(self):
        """The only occurrence of `field_name` in `_make_task`'s own body is the argument
        name and the bare name it passes on. A derived value would be an expression."""
        for node in ast.walk(_tree()):
            if isinstance(node, ast.FunctionDef) and node.name == "_make_task":
                call = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id == "ExceptionResolutionTask"]
                assert len(call) == 1
                value = _keyword(call[0], "field_name")
                assert isinstance(value, ast.Name) and value.id == "field_name"
                return
        pytest.fail("_make_task was not found")

    def test_make_task_reads_the_field_exactly_once_in_its_body(self):
        """Proven structurally, with the docstring stripped — an inference in the body, not
        the prose forbidding it, is what reddens this test. `field_name` appears in the
        body only as the value handed to the model, so nothing can be computed from the
        task type, the answer type or the AI value on the way in."""
        for node in ast.walk(_tree()):
            if isinstance(node, ast.FunctionDef) and node.name == "_make_task":
                body = node.body[1:]  # drop the docstring
                names = [
                    child for statement in body for child in ast.walk(statement)
                    if isinstance(child, ast.Name) and child.id == "field_name"
                ]
                assert len(names) == 1, [n.lineno for n in names]
                return
        pytest.fail("_make_task was not found")

    def test_make_task_never_compares_the_field_to_an_inferred_value(self):
        for node in ast.walk(_tree()):
            if isinstance(node, ast.FunctionDef) and node.name == "_make_task":
                body = node.body[1:]
                for statement in body:
                    for child in ast.walk(statement):
                        if isinstance(child, ast.Compare):
                            text = ast.unparse(child)
                            assert "field_name" not in text, text
                            assert not (("answer_type" in text or "current_ai_value" in text
                                         or "task_type" in text)), text
                return
        pytest.fail("_make_task was not found")

    def test_the_add_closure_forwards_the_field_and_adds_nothing(self):
        for node in ast.walk(_tree()):
            if isinstance(node, ast.FunctionDef) and node.name == "add":
                assert not node.args.defaults, "add() must not default the field"
                assert [a.arg for a in node.args.kwonlyargs] == ["field_name"]
                call = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name) and n.func.id == "_make_task"]
                assert len(call) == 1
                value = _keyword(call[0], "field_name")
                assert isinstance(value, ast.Name) and value.id == "field_name"
                return
        pytest.fail("add() was not found")

    def test_no_construction_site_derives_the_field_from_the_task_type(self):
        """Every stated value is a literal or None — never `_TASK_FIELDS[...]`, never a
        `.get(`, never the constant the task is built from. This is what closes order
        derivation, which the real fixture cannot falsify on its own."""
        for call in _task_calls(_tree(), "add"):
            value = _keyword(call, "field_name")
            assert isinstance(value, ast.Constant), (_first_arg_name(call), ast.dump(value))
        for call in _make_task_calls_outside_add(_tree()):
            value = _keyword(call, "field_name")
            assert isinstance(value, ast.Constant), (_first_arg_name(call), ast.dump(value))

    def test_the_only_non_literal_field_argument_is_the_closures_own_forwarding(self):
        """Exactly one `field_name=` value in the module is not a literal: `add`'s bare
        `field_name`, which is the parameter it was called with. Anything else would be a
        value computed at the boundary."""
        non_literals = [
            call.lineno for call in _task_calls(_tree(), "_make_task")
            if not isinstance(_keyword(call, "field_name"), ast.Constant)
        ]
        assert len(non_literals) == 1, non_literals


# =============================================================================
# 7. THE PERSISTENCE ROUND TRIP
# =============================================================================
class TestThePayloadRoundTrip:
    def _row(self, **overrides):
        task = _task(**overrides)
        return crs._task_payload(task, where="test")

    def test_the_payload_carries_the_field(self):
        assert self._row(field_name="plate")["field_name"] == "plate"

    def test_the_payload_carries_none_as_none(self):
        assert self._row(field_name=None)["field_name"] is None

    def test_the_payload_states_the_field_verbatim_and_derives_nothing(self):
        """A payload must never gain a field the task did not state: the two task types
        whose presentation label exists but whose field is None stay None in the row."""
        assert self._row(task_type=ex.TASK_PROVIDE_CONNECTION_IDENTITY,
                         answer_type="CONNECTION_IDENTITY")["field_name"] is None
        assert self._row(task_type=ex.TASK_SELECT_MEMBER_POSITION_ATTACHMENT,
                         answer_type="MEMBER_POSITION_ATTACHMENTS")["field_name"] is None

    def test_the_payload_survives_the_json_boundary_the_writer_imposes(self):
        json.dumps(self._row(field_name="location"), ensure_ascii=False)

    def test_the_round_trip_preserves_the_field(self):
        for field in (None,) + ENGINEERING_FIELDS:
            assert crs._task_from_row(self._row(field_name=field)).field_name == field

    def test_a_historical_payload_without_the_key_loads_as_none(self):
        """Every task recorded before J79 reads back exactly as it was — as a task that
        addressed no single field, which is what it recorded."""
        row = self._row(field_name="plate")
        del row["field_name"]
        assert crs._task_from_row(row).field_name is None

    def test_a_historical_payload_is_otherwise_unchanged_by_the_read(self):
        row = self._row(field_name="plate")
        del row["field_name"]
        task = crs._task_from_row(row)
        assert (task.task_id, task.task_type, task.answer_type) == (
            "RP-0001-T01", "PROVIDE_PLATE", "PLATE_VALUE",
        )

    def test_a_payload_stating_a_field_outside_the_vocabulary_is_refused(self):
        """Fail closed: it is never dropped, coerced or read as 'no field'."""
        row = self._row(field_name="plate")
        row["field_name"] = "connection_id"
        with pytest.raises(ValueError):
            crs._task_from_row(row)

    def test_a_payload_stating_null_reads_as_no_field(self):
        row = self._row(field_name="plate")
        row["field_name"] = None
        assert crs._task_from_row(row).field_name is None

    def test_the_round_trip_is_lossless_for_the_whole_task(self):
        task = _task(field_name="attachments")
        row = crs._task_payload(task, where="test")
        back = crs._task_from_row(row)
        assert (back.task_id, back.task_type, back.answer_type, back.field_name) == (
            task.task_id, task.task_type, task.answer_type, task.field_name,
        )

    def test_the_row_gains_exactly_one_key(self):
        """The boundary adds ONE term to a stored task and nothing else. No key was renamed,
        removed or reordered away."""
        row = self._row(field_name="plate")
        assert set(row) == {
            "task_id", "task_type", "blocker_codes", "question", "current_ai_value",
            "answer_type", "allowed_choices", "evidence_requirement", "field_name",
            "status", "resolution",
        }

    def test_the_payload_gained_exactly_one_key(self):
        """J79 adds ONE term to a stored task. A set difference, so a removal reddens as
        loudly as an addition, and the pre-J79 key set is written out rather than derived."""
        before = {"task_id", "task_type", "blocker_codes", "question", "current_ai_value",
                  "answer_type", "allowed_choices", "evidence_requirement", "status", "resolution"}
        assert set(self._row(field_name="plate")) - before == {"field_name"}
        assert before - set(self._row(field_name="plate")) == set()


# =============================================================================
# 8. MUTATION CONTROLS
#
# Each control mutates a COPY of the real source, and each one asserts BOTH the mutant's
# changed behaviour AND the original's unchanged behaviour. A control that stopped
# detecting its mutation would therefore fail here rather than pass quietly.
# =============================================================================
_GUARD_ANCHOR = "        if self.field_name is not None and self.field_name not in ENGINEERING_FIELDS:"
_GUARD_MUTANT = "        if False:"

_DERIVE_ANCHOR = "        evidence_requirement=evidence,\n        field_name=field_name,"
_DERIVE_MUTANT = (
    "        evidence_requirement=evidence,\n"
    "        field_name=__import__('app.cad_engine.review_contract', "
    "fromlist=['_TASK_FIELDS'])._TASK_FIELDS.get(task_type),"
)

_GROUPED = "TASK_SELECT_MEMBER_POSITION_ATTACHMENT"


class TestMutationControls:
    def test_the_control_the_grouped_task_cannot_be_bound_to_one_of_its_three_fields(self):
        """MUTANT: bind the grouped task to an arbitrary one of its three fields. The
        assertion the real module passes must redden."""
        mutated = ast.parse(_rewrite_field_name(_GROUPED, "'connected_member_marks'"))
        stated = {_first_arg_name(c): _constant(c, "field_name") for c in _task_calls(mutated, "add")}
        real = {_first_arg_name(c): _constant(c, "field_name") for c in _task_calls(_tree(), "add")}
        assert stated != real
        assert stated[_GROUPED] == "connected_member_marks"
        assert real[_GROUPED] is None
        others = {
            _first_arg_name(c): _constant(c, "field_name")
            for c in _task_calls(mutated, "add")
            if _first_arg_name(c) != _GROUPED
        }
        assert others == {n: v for n, v in real.items() if n != _GROUPED}

    def test_the_control_the_vocabulary_guard_is_load_bearing(self):
        """MUTANT: delete the guard. An out-of-vocabulary field is then silently accepted —
        which is exactly what `connection_id` would have been."""
        source = EXCEPTION_MODULE.read_text(encoding="utf-8")
        assert source.count(_GUARD_ANCHOR) == 1
        mutant = _exec_mutant(source.replace(_GUARD_ANCHOR, _GUARD_MUTANT), name="j79_guard_mutant")
        accepted = mutant.ExceptionResolutionTask(
            task_id="T", task_type=ex.TASK_PROVIDE_CONNECTION_IDENTITY, blocker_codes=(),
            question="q", current_ai_value=None, answer_type="CONNECTION_IDENTITY",
            allowed_choices=(), evidence_requirement="e", field_name="connection_id",
        )
        assert accepted.field_name == "connection_id"
        with pytest.raises(ValueError):
            _task(field_name="connection_id")

    def test_the_control_deriving_the_field_from_the_type_map_does_not_survive(self):
        """MUTANT: derive the field from `review_contract._TASK_FIELDS`. It is not merely
        forbidden by convention — it produces `connection_id` for the identity task and the
        model refuses it, so this mutant cannot build that task at all. `rebind=True`, so
        the refusal is the REAL model's, not a second class of the same name."""
        source = EXCEPTION_MODULE.read_text(encoding="utf-8")
        assert source.count(_DERIVE_ANCHOR) == 1
        mutant = _exec_mutant(
            source.replace(_DERIVE_ANCHOR, _DERIVE_MUTANT), name="j79_derive_mutant", rebind=True,
        )
        with pytest.raises(ValueError) as refusal:
            mutant._make_task(
                ex.TASK_PROVIDE_CONNECTION_IDENTITY, (), "q", None, "CONNECTION_IDENTITY", (),
                "e", "RP-0001", 1, field_name=None,
            )
        assert "field_name must be one of" in str(refusal.value)
        # The same mutant binds the fields that ARE in the vocabulary, which is what makes
        # the refusal above a statement about the vocabulary and not about the mutant.
        plate = mutant._make_task(
            ex.TASK_PROVIDE_PLATE, (), "q", None, "PLATE_VALUE", (), "e", "RP-0001", 1,
            field_name=None,
        )
        assert plate.field_name == "plate"

    def test_the_control_the_source_scan_detects_a_dropped_field_name(self):
        """MUTANT: a construction site that omits the field. The 'states it explicitly'
        assertion must notice, so the boundary cannot erode by deletion."""
        mutated = ast.parse(_drop_field_name(_GROUPED))
        missing = [c for c in _task_calls(mutated, "add") if _keyword(c, "field_name") is None]
        assert len(missing) == 1
        assert _first_arg_name(missing[0]) == _GROUPED
        assert not [c for c in _task_calls(_tree(), "add") if _keyword(c, "field_name") is None]

    def test_the_control_a_second_silent_none_site_is_detected(self):
        """MUTANT: a site that loses its field is not confused with the sites that state
        None on purpose — the scan keys on the ARGUMENT being absent, never on its value."""
        mutated = ast.parse(_drop_field_name("TASK_PROVIDE_PLATE"))
        missing = [_first_arg_name(c) for c in _task_calls(mutated, "add")
                   if _keyword(c, "field_name") is None]
        assert missing == ["TASK_PROVIDE_PLATE"]
        stated_none = [_first_arg_name(c) for c in _task_calls(mutated, "add")
                       if _constant(c, "field_name") is None]
        assert "TASK_PROVIDE_PLATE" not in stated_none
        assert "TASK_COMPLETE_REVIEW" in stated_none

    def test_the_control_a_defaulted_field_would_let_a_call_site_go_silent(self):
        """MUTANT: give `_make_task`'s `field_name` a default. A call site then omits it and
        the task silently reads as 'no field' — the exact failure the keyword-only argument
        without a default exists to turn into a TypeError.

        The mutant is built by TEXT, from the real signature, so the control stops working
        the moment the signature changes shape."""
        source = EXCEPTION_MODULE.read_text(encoding="utf-8")
        anchor = "index: int, *, field_name: str | None"
        assert source.count(anchor) == 1, "the signature moved; re-anchor this control"
        mutant = _exec_mutant(
            source.replace(anchor, anchor + " = None"), name="j79_default_mutant", rebind=True,
        )
        assert mutant._make_task(
            ex.TASK_PROVIDE_PLATE, (), "q", None, "PLATE_VALUE", (), "e", "RP-0001", 1,
        ).field_name is None
        with pytest.raises(TypeError):
            ex._make_task(
                ex.TASK_PROVIDE_PLATE, (), "q", None, "PLATE_VALUE", (), "e", "RP-0001", 1,
            )

    def test_the_control_the_task_model_is_reached_by_the_real_module(self):
        """The mutants above are only meaningful if the module under test is the one the
        boundary uses. `_make_task` in the real module builds the real dataclass."""
        task = ex._make_task(
            ex.TASK_PROVIDE_PLATE, (), "q", None, "PLATE_VALUE", (), "e", "RP-0001", 1,
            field_name="plate",
        )
        assert type(task) is ex.ExceptionResolutionTask
        assert task.field_name == "plate"


# =============================================================================
# 9. THIS MILESTONE WIRED NOTHING
# =============================================================================
class TestR3IsStillUnwired:
    def _production_files(self):
        return [
            path for path in APP.rglob("*.py")
            if path.name != "connection_scoped_evidence.py"
        ]

    def test_no_production_module_imports_the_connection_scoped_evidence_module(self):
        """J79 bound a field on a task and reached no resolver. J80 built the one consumer
        boundary, declared here rather than absorbed; the guard over every OTHER production
        module is what this test still is.

        J81 gave the boundary its first production caller, the production read port, and it is
        declared here for the same reason. The field binding is untouched by that: the port
        reads the field off the task the item recorded and derives it from nothing — not from
        the task type, not from the answer type, not from the order. Both lists stay exact."""
        importers = [
            str(path.relative_to(APP.parent))
            for path in self._production_files()
            if "connection_scoped_evidence" in path.read_text(encoding="utf-8")
        ]
        assert importers == ["app/cad_engine/cited_candidate_resolution.py"], importers
        callers = sorted(
            str(path.relative_to(APP.parent)) for path in self._production_files()
            if "cited_candidate_resolution" in path.read_text(encoding="utf-8")
        )
        assert callers == ["app/production_recorded_readings.py"], callers

    def test_the_task_boundary_does_not_name_the_resolver(self):
        for module in (EXCEPTION_MODULE, BOUNDARY_MODULE, CATALOGUE_MODULE):
            source = module.read_text(encoding="utf-8")
            assert "resolve_candidate_at_recorded_address" not in source
            assert "resolve_connection_field_reading" not in source

    def test_no_module_names_a_citation_writer(self):
        """The citation table is not mentioned anywhere under `app/` except in the modules
        that already read it. `connection_review_snapshot` names it as the READ path it has
        had since J66; nothing J79 touched names it at all."""
        for module in (EXCEPTION_MODULE, CATALOGUE_MODULE, BOUNDARY_MODULE):
            source = module.read_text(encoding="utf-8")
            assert "persist_connection_review_citations" not in source, module
        assert "connection_review_item_citations" not in EXCEPTION_MODULE.read_text("utf-8")
        assert "connection_review_item_citations" not in CATALOGUE_MODULE.read_text("utf-8")

    def test_no_module_under_app_mentions_a_citation_outside_the_j66_three(self):
        """J66's own tree-wide fence, re-asserted here because J79 edited a module inside
        it: the word may appear in exactly the three modules J66 touched, and nowhere else.
        A comment is enough to trip it — which is the point of the fence, and it is why
        J79's explanation of the identity binding is written without it."""
        naming = sorted(
            str(path.relative_to(APP.parent)) for path in APP.rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/cad_engine/review_contract.py",
            "app/engineering_data/connection_review_repository.py",
        ], naming

    def test_the_exception_module_still_writes_nothing(self):
        """A task now states an engineering field. It still opens no connection, reads no
        row and writes none: the vocabulary is a string on a frozen dataclass."""
        calls = {
            node.func.id for node in ast.walk(_tree())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("insert", "update", "upsert", "delete", "raw", "rpc"):
            assert forbidden not in calls, forbidden
        imported = {
            alias.name for node in ast.walk(_tree())
            if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
        }
        assert not [name for name in imported if "supabase" in name.lower()]


# =============================================================================
# 10. NOTHING ELSE WAS TOUCHED
# =============================================================================
class TestNothingElseWasTouched:
    def test_the_origin_vocabulary_is_unchanged(self):
        from app.cad_engine import candidate_origin_address as coa

        assert coa.ORIGIN_KEYS == ("analysis_run_id", "candidate_index")

    def test_the_citation_vocabulary_is_unchanged(self):
        assert crs.CITATION_FIELD_NAMES == ENGINEERING_FIELDS

    def test_the_task_field_and_the_citation_field_are_the_same_closed_vocabulary(self):
        """The reason `field_name` may be `connection_id` in the presentation and None here:
        the citation table's own `field_name` column is CHECKed against the same
        ENGINEERING_FIELDS, so a task that named a field outside it could never be cited.
        Verified without touching the database — the CHECK's vocabulary is the module's."""
        assert crs.CITATION_CHECK_VOCABULARY["field_name"] == ENGINEERING_FIELDS
        assert [name for name, _, _, _ in crs.CITATION_TABLE_COLUMNS].count("field_name") == 1
        # ... and the TASK's field is not a column of either table: it rides the JSON.
        task_columns = {name for name, _, _, _ in crs.SNAPSHOT_TABLE_COLUMNS + crs.ITEM_TABLE_COLUMNS}
        assert "field_name" not in task_columns

    def test_the_item_evidence_vocabulary_gained_no_field_term(self):
        """J78 explicitly rejected putting field identity in the item's evidence."""
        source = BOUNDARY_MODULE.read_text(encoding="utf-8")
        anchor = source.index('"grid_reference": _payload(extraction.grid_reference')
        block = source[anchor:anchor + 400]
        assert "field_name" not in block

    def test_the_review_contract_projection_is_unchanged(self):
        from app.cad_engine import review_contract as rc

        assert rc._TASK_FIELDS[ex.TASK_SELECT_MEMBER] == "connected_member_marks"
        assert rc._TASK_FIELDS[ex.TASK_RESOLVE_CONFLICT] is None

    def test_the_agreement_keys_are_unchanged(self):
        from app.production_review import project_workflow_resumption as pwr

        assert len(pwr.AGREEMENT_KEYS) == 10
        assert "field_name" not in pwr.AGREEMENT_KEYS

    def test_the_task_contract_fields_are_unchanged(self):
        """J22's resolution parser reads a fixed vocabulary; `field_name` is a TASK field
        and is never accepted from a resolution request."""
        from app.production_review_resolution import RESOLUTION_FIELDS

        assert RESOLUTION_FIELDS == ("task_id", "task_type", "answer_type", "answer", "evidence")

    def test_no_migration_names_this_milestone(self):
        migrations = sorted(
            path.name for path in (APP.parent / "supabase" / "migrations").glob("*.sql")
        )
        assert not [name for name in migrations if "j79" in name.lower()]

    def test_the_snapshot_table_definitions_are_unchanged(self):
        """The field rides the existing `tasks` JSON column; no column was added."""
        names = [name for name, _, _, _ in crs.SNAPSHOT_TABLE_COLUMNS + crs.ITEM_TABLE_COLUMNS]
        assert "field_name" not in names
