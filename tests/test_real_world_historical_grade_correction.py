"""
THE HISTORICAL GRADE CORRECTION — pinned, guarded, and provably narrow.

WHAT THIS FILE PROVES

`app/engineering_data/historical_grade_correction.py` corrects ONE frozen set of
rows: the 128 historical `steel_members` rows and the 3 historical
`connection_plates` rows whose `grade` was written by an application literal
rather than read from a drawing. It sets that one column to NULL and nothing else.

The claimed properties, each asserted here against an in-process client double:

  * the row predicate is the PINNED ID SET first and the fabricated value second —
    `grade = '300PLUS'` is never a selector, because the mistake this milestone
    corrects is precisely a value that nothing distinguished from evidence;
  * the update payload contains ONE column, and it is the corrected one;
  * only the pinned ids appear in any statement, and no pinned id is left out;
  * every guard refuses on its own — a missing target, a target outside the set, a
    row already NULL, the wrong count, a hand-built plan, a pre-image that moved
    between planning and execution, and a write that quietly changes another
    column;
  * the post-image is verified from a fresh read: every target NULL, none left
    carrying the fabricated value, the row count unchanged, no other grade value
    changed, and a fingerprint over every non-grade field byte-identical to the
    pre-image.

No test in this file touches a database: the client is a double that implements
exactly the three PostgREST calls the module makes. The live correction is a
separate, recorded, one-off execution.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.engineering_data import historical_grade_correction as correction

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "app" / "engineering_data" / "historical_grade_correction.py"
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")


# ==========================================================================
# The client double — exactly the three calls the module makes, nothing more
# ==========================================================================
class _DoubleQuery:
    def __init__(self, client, table):
        self._client = client
        self._table = table
        self._op = None
        self._payload = None
        self._ids = None
        self._eq = None

    def select(self, columns="*"):
        self._op = "select"
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = copy.deepcopy(payload)
        return self

    def in_(self, column, values):
        assert column == "id", f"the row predicate must be the id, not {column!r}"
        self._ids = list(values)
        return self

    def eq(self, column, value):
        self._eq = (column, value)
        return self

    def execute(self):
        self._client.calls.append({
            "table": self._table,
            "op": self._op,
            "payload": copy.deepcopy(self._payload),
            "ids": None if self._ids is None else list(self._ids),
            "eq": self._eq,
        })
        if self._op == "select":
            return _Result(copy.deepcopy(self._client.tables[self._table]))
        # The value guard is part of the statement, exactly as the module writes
        # it; a double that ignored it would be testing a different predicate.
        target = set(self._ids or [])
        wanted_grade = self._eq[1] if self._eq else None
        for row in self._client.tables[self._table]:
            if row["id"] in target and (self._eq is None or row.get(self._eq[0]) == wanted_grade):
                for key, value in (self._payload or {}).items():
                    row[key] = value
        return _Result([])


class _Result:
    def __init__(self, data):
        self.data = data


class _Double:
    """A client whose rows are plain dicts, so a test can inspect them directly."""

    def __init__(self, members, plates=None):
        self.tables = {"steel_members": members, "connection_plates": plates or []}
        self.calls = []

    def table(self, name):
        return _DoubleQuery(self, name)

    def updates(self, table=None):
        return [c for c in self.calls if c["op"] == "update"
                and (table is None or c["table"] == table)]

    def writes(self):
        return [c for c in self.calls if c["op"] in ("update", "insert", "upsert", "delete")]


DECOY = {
    "mark": "B1",
    "member_type": "beam",
    "section_name": "310UB46.2",
    "section_name_raw": "310UB46.2",
    "length_mm": 6000.0,
    "quantity": 1,
    "weight_per_metre": 46.2,
    "total_weight_kg": 277.2,
    "confidence": "high",
    "notes": None,
    "created_at": "2026-09-17T00:00:00+00:00",
    "review_status": "extracted",
    "section_resolution": "EXACT",
    "reference_data_identity": {"reference_data_source": "LIVE_SUPABASE"},
}


def member_rows(ids, grade=correction.MEMBER_FABRICATED_GRADE, **overrides):
    rows = []
    for index, row_id in enumerate(ids):
        row = {"id": row_id, "grade": grade, **DECOY, "mark": f"B{index + 1}"}
        row.update(overrides)
        rows.append(row)
    return rows


def live_shaped_members():
    """The accepted pre-image shape: 128 pinned rows, all carrying the literal."""
    return member_rows(correction.MEMBER_TARGET.ids)


def live_shaped_plates(grade=correction.PLATE_FABRICATED_GRADE):
    return [
        {"id": row_id, "connection_id": f"conn-{index}", "plate_type": "end_plate",
         "thickness": 12.0 + index, "width": None, "depth": None, "grade": grade}
        for index, row_id in enumerate(correction.PLATE_TARGET.ids)
    ]


# ==========================================================================
# The pinned facts themselves
# ==========================================================================
class TestThePinnedSetsAreWhatTheAuditEstablished:

    def test_the_member_set_is_the_pinned_one(self):
        assert len(correction.pinned_member_ids()) == 128
        assert correction._sha256_of_id_set(correction.pinned_member_ids()) == (
            "edd4bba4071863080f6e70858e7008f76745fc53cb415007b14130dc3439b492"
        )

    def test_the_plate_set_is_the_pinned_one(self):
        assert len(correction.pinned_plate_ids()) == 3
        assert correction._sha256_of_id_set(correction.pinned_plate_ids()) == (
            "5447f145dfc2b484b8bdcebe880137fdc15772d2e011d46926d063660ca596d1"
        )

    def test_a_tampered_id_set_refuses_rather_than_correcting(self, monkeypatch):
        """The control for the digest guard: an id set that is not the pinned one
        must be refused, so a drifted list can never name the wrong rows."""
        tampered = correction.MEMBER_TARGET.ids[:-1] + ("00000000-0000-0000-0000-000000000000",)
        broken = correction._Target(
            label="member", table=correction.MEMBER_TABLE,
            ids=tampered,
            fabricated_grade=correction.MEMBER_FABRICATED_GRADE,
            expected_count=correction.MEMBER_TARGET_COUNT,
            id_set_sha256=correction.MEMBER_ID_SET_SHA256,
        )
        with pytest.raises(correction.CorrectionRefused, match="does not hash"):
            broken.pinned_ids()

    def test_a_short_id_set_refuses(self):
        short = correction._Target(
            label="member", table=correction.MEMBER_TABLE,
            ids=correction.MEMBER_TARGET.ids[:-1],
            fabricated_grade=correction.MEMBER_FABRICATED_GRADE,
            expected_count=correction.MEMBER_TARGET_COUNT,
            id_set_sha256=correction._sha256_of_id_set(correction.MEMBER_TARGET.ids[:-1]),
        )
        with pytest.raises(correction.CorrectionRefused, match="128 distinct ids"):
            short.pinned_ids()


# ==========================================================================
# The guards — each refuses on its own
# ==========================================================================
class TestEveryGuardRefuses:

    def test_a_missing_target_refuses(self):
        rows = live_shaped_members()[:-1]
        with pytest.raises(correction.CorrectionRefused, match="absent from steel_members"):
            correction.plan_member_correction(_Double(rows))

    def test_a_row_outside_the_pinned_set_carrying_the_literal_refuses(self):
        """The guard that stops a value-based sweep: if the literal is on a row the
        audit never accepted, the accepted premise has changed and nothing runs."""
        rows = live_shaped_members()
        rows.append({"id": "99999999-9999-9999-9999-999999999999", "grade": "300PLUS"})
        with pytest.raises(correction.CorrectionRefused, match="outside the pinned"):
            correction.plan_member_correction(_Double(rows))

    def test_a_row_already_null_refuses(self):
        rows = live_shaped_members()
        rows[0]["grade"] = None
        with pytest.raises(correction.CorrectionRefused, match="already NULL"):
            correction.plan_member_correction(_Double(rows))

    def test_a_count_that_is_not_the_accepted_one_refuses(self):
        rows = live_shaped_members()
        rows[0]["grade"] = "AS/NZS 3678-300"     # a stated grade, not the literal
        with pytest.raises(correction.CorrectionRefused, match="carry '300PLUS'|accepted pre-image"):
            correction.plan_member_correction(_Double(rows))

    def test_a_plate_count_that_is_not_three_refuses(self):
        plates = live_shaped_plates()[:-1]
        plates[0]["grade"] = None
        with pytest.raises(correction.CorrectionRefused):
            correction.plan_plate_correction(_Double([], plates))

    def test_a_hand_built_plan_for_another_table_refuses(self):
        plan = correction.CorrectionPlan(
            label="member", table="steel_sections",
            ids=correction.MEMBER_TARGET.ids,
            fabricated_grade=correction.MEMBER_FABRICATED_GRADE,
            expected_count=128, id_set_sha256=correction.MEMBER_ID_SET_SHA256,
            rows_before=0, rows_carrying_the_fabricated_grade=0, rows_already_null=0,
            other_grade_values=(), non_grade_fingerprint="",
        )
        client = _Double(live_shaped_members())
        with pytest.raises(correction.CorrectionRefused, match="not one of the two frozen"):
            correction.apply_correction(client, plan)
        assert client.writes() == []

    def test_a_plan_with_a_reduced_id_set_refuses(self):
        plan = correction.CorrectionPlan(
            label="member", table=correction.MEMBER_TABLE,
            ids=correction.MEMBER_TARGET.ids[:-1],
            fabricated_grade=correction.MEMBER_FABRICATED_GRADE,
            expected_count=128, id_set_sha256=correction.MEMBER_ID_SET_SHA256,
            rows_before=0, rows_carrying_the_fabricated_grade=0, rows_already_null=0,
            other_grade_values=(), non_grade_fingerprint="",
        )
        client = _Double(live_shaped_members())
        with pytest.raises(correction.CorrectionRefused, match="not one of the two frozen"):
            correction.apply_correction(client, plan)
        assert client.writes() == []

    def test_a_pre_image_that_moved_between_planning_and_execution_refuses(self):
        client = _Double(live_shaped_members())
        plan = correction.plan_member_correction(client)
        client.tables["steel_members"][5]["mark"] = "MOVED-AFTER-PLANNING"
        with pytest.raises(correction.CorrectionRefused, match="non-grade content changed"):
            correction.apply_correction(client, plan)
        assert client.writes() == []


# ==========================================================================
# The write itself — one column, pinned ids, never the value alone
# ==========================================================================
class TestTheWriteIsNarrow:

    def test_the_member_correction_sets_only_the_grade_and_verifies(self):
        client = _Double(live_shaped_members())
        before = copy.deepcopy(client.tables["steel_members"])

        plan = correction.plan_member_correction(client)
        result = correction.apply_correction(client, plan)

        assert result.verified
        assert result.rows_requested == 128
        assert result.rows_now_null == 128
        assert result.rows_still_carrying_the_fabricated_grade == 0
        assert result.rows_before == result.rows_after == 128
        assert result.non_grade_fingerprint_before == result.non_grade_fingerprint_after
        assert {row["grade"] for row in client.tables["steel_members"]} == {None}
        # Every non-grade field of every row is untouched.
        for old, new in zip(before, client.tables["steel_members"]):
            assert {k: v for k, v in old.items() if k != "grade"} == {
                k: v for k, v in new.items() if k != "grade"
            }

    def test_the_plate_correction_sets_only_the_grade_and_keeps_its_thicknesses(self):
        client = _Double([], live_shaped_plates())
        before = copy.deepcopy(client.tables["connection_plates"])

        plan = correction.plan_plate_correction(client)
        result = correction.apply_correction(client, plan)

        assert result.verified
        assert result.rows_requested == 3
        assert result.rows_now_null == 3
        assert result.rows_before == result.rows_after == 3
        assert [row["thickness"] for row in client.tables["connection_plates"]] == [12.0, 13.0, 14.0]
        for old, new in zip(before, client.tables["connection_plates"]):
            assert {k: v for k, v in old.items() if k != "grade"} == {
                k: v for k, v in new.items() if k != "grade"
            }

    def test_every_statement_names_the_pinned_ids_and_the_value_guard(self):
        client = _Double(live_shaped_members())
        plan = correction.plan_member_correction(client)
        correction.apply_correction(client, plan)

        pinned = set(correction.MEMBER_TARGET.ids)
        seen: set[str] = set()
        for call in client.updates("steel_members"):
            # id-FIRST: the predicate is the pinned set, and the value only
            # sharpens it. A value-only predicate is the defect, not the fix.
            assert call["ids"] is not None, "a statement selected rows without the pinned ids"
            assert set(call["ids"]) <= pinned, "a statement named a row outside the pinned set"
            assert call["eq"] == ("grade", "300PLUS"), "a statement dropped the current-value guard"
            seen |= set(call["ids"])
        assert seen == pinned, "the pinned set was not covered exactly"

    def test_every_statement_writes_one_column_and_writes_it_to_null(self):
        client = _Double(live_shaped_members())
        correction.apply_correction(client, correction.plan_member_correction(client))

        updates = client.updates("steel_members")
        assert updates, "nothing was written"
        for call in updates:
            assert call["payload"] == {"grade": None}

    def test_no_statement_writes_anything_but_the_grade(self):
        client = _Double(live_shaped_members())
        correction.apply_correction(client, correction.plan_member_correction(client))

        assert {call["op"] for call in client.writes()} == {"update"}
        assert all(
            set(call["payload"]) == {"grade"}
            for call in client.writes()
        )

    def test_the_batches_are_bounded_and_cover_the_set_exactly_once(self):
        client = _Double(live_shaped_members())
        correction.apply_correction(client, correction.plan_member_correction(client))

        updates = client.updates("steel_members")
        assert len(updates) == 4, "128 ids at a batch size of 32"
        assert all(len(call["ids"]) <= 32 for call in updates)
        assert sum(len(call["ids"]) for call in updates) == 128
        listed = [i for call in updates for i in call["ids"]]
        assert len(listed) == len(set(listed)) == 128, "an id was written twice"

    def test_a_write_that_changes_another_column_is_caught(self, monkeypatch):
        """The verification is not decorative: a double that also moves a
        non-grade field must fail the fingerprint comparison."""
        client = _Double(live_shaped_members())
        original_execute = _DoubleQuery.execute

        def dishonest_execute(self):
            result = original_execute(self)
            if self._op == "update":
                self._client.tables[self._table][0]["notes"] = "quietly rewritten"
            return result

        monkeypatch.setattr(_DoubleQuery, "execute", dishonest_execute)
        with pytest.raises(correction.CorrectionRefused, match="did not verify"):
            correction.apply_correction(client, correction.plan_member_correction(client))

    def test_the_plan_reports_other_grade_values_without_touching_them(self):
        """Guards 3-5 are about the fabricated literal. A row carrying a DIFFERENT
        value is not a target — and the correction proves it left it alone."""
        rows = live_shaped_members()
        rows.append({"id": "11111111-1111-1111-1111-111111111111",
                     "grade": "AS/NZS 3678-300", **DECOY})
        client = _Double(rows)

        plan = correction.plan_member_correction(client)
        assert plan.other_grade_values == (("AS/NZS 3678-300", 1),)
        result = correction.apply_correction(client, plan)

        assert result.verified
        assert result.other_grade_values_unchanged
        untouched = [r for r in client.tables["steel_members"] if r["id"].startswith("1111")]
        assert untouched[0]["grade"] == "AS/NZS 3678-300"

    def test_planning_writes_nothing(self):
        client = _Double(live_shaped_members(), live_shaped_plates())
        correction.plan_member_correction(client)
        correction.plan_plate_correction(client)
        assert client.writes() == []


# ==========================================================================
# Narrow by construction — a source rule, not a promise
# ==========================================================================
class TestTheModuleCannotBePointedAnywhereElse:

    def test_the_public_surface_takes_no_table_column_or_value(self):
        import inspect

        for name in ("plan_member_correction", "plan_plate_correction"):
            parameters = list(inspect.signature(getattr(correction, name)).parameters)
            assert parameters == ["client"], name
        assert list(inspect.signature(correction.apply_correction).parameters) == ["client", "plan"]
        # There is no "correct(table, ids, ...)" entry point at all.
        assert not hasattr(correction, "correct")
        assert not hasattr(correction, "correct_rows")

    def test_the_module_names_only_the_two_frozen_tables(self):
        tree = ast.parse(MODULE_SOURCE)
        tables = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.endswith("_members") or (
                isinstance(node, ast.Constant) and node.value == "connection_plates"
            )
        }
        assert tables <= {"steel_members", "connection_plates"}, tables

    def test_the_module_can_only_update_and_only_that_one_column(self):
        tree = ast.parse(MODULE_SOURCE)
        verbs = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "update" in verbs
        assert not verbs & {"insert", "upsert", "delete", "rpc"}, verbs & {"insert", "upsert", "delete", "rpc"}
        # The one dict the write path sends is the corrected column.
        payloads = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Name) and k.id == "CORRECTED_COLUMN" for k in node.keys
            )
        ]
        assert len(payloads) == 1
        assert isinstance(payloads[0].values[0], ast.Name)
        assert payloads[0].values[0].id == "CORRECTED_VALUE"
        assert correction.CORRECTED_COLUMN == "grade"
        assert correction.CORRECTED_VALUE is None

    def test_the_module_creates_no_client_of_its_own(self):
        # It must be drivable against whatever client it is handed, including an
        # in-process double, and must not reach the application's live client.
        assert "app.supabase_client" not in MODULE_SOURCE
        assert "create_client" not in MODULE_SOURCE
        assert "SUPABASE" not in MODULE_SOURCE

    def test_the_guards_precede_the_write_in_the_source(self):
        tree = ast.parse(MODULE_SOURCE)
        apply_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "apply_correction"
        )
        assert len(apply_fn.body) > 1
        update_lines, guard_lines = [], []
        for index, statement in enumerate(apply_fn.body):
            segment = ast.dump(statement)
            if "'update'" in segment:
                update_lines.append(index)
            if "CorrectionRefused" in segment:
                guard_lines.append(index)
        assert update_lines and guard_lines
        assert min(guard_lines) < min(update_lines), "a guard runs after the first write"
