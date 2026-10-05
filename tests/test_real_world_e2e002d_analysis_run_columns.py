"""
E2E-002D — THE `analysis_runs` COLUMN CONTRACT.

WHAT THIS FILE IS

The check whose absence let a missing column ship.

E2E-001N added `analysis_runs_for_drawing_set`, which selected and ordered by
`analysis_runs.created_at`. That column has never existed. Nothing caught it: every test of
the reconciliation doubles the repository, and a doubled repository cannot refuse a column
name, so the suite passed 34 focused tests and 205 across the review suites while containing
a query the schema would not accept. The first genuine end-to-end run found it instead —
PostgREST answered `42703`, the classification reported it to a customer-facing page as a
failure SteelSpec "could not classify", and two extraction runs died at a seam that had
nothing to do with their drawings.

So this file asserts the one thing a mocked repository cannot: **the column names the
repository puts in an `analysis_runs` query are columns `analysis_runs` actually has.**

HOW IT KNOWS THE COLUMNS

`ANALYSIS_RUN_COLUMNS` below was read from the deployed table itself — PostgREST
`GET analysis_runs?select=*&limit=2` against the project in this repository's own `.env`,
on 2026-10-06 — and then corroborated inside the repository by the second test, which checks
that every column `create_analysis_run` INSERTS is in the same set. A column named here and
written there is evidence from two directions rather than one.

It deliberately does NOT read the live database. The ordinary suite must not depend on
production being reachable, and a contract test that needs the network is a test that gets
skipped on the day it matters. The set is recorded here, and refreshing it is a deliberate
act with the same query stated above.

WHAT IT WOULD CATCH

Any column name in `analysis_runs_for_drawing_set` that is not one of these — the defect that
shipped, and the whole class of it, rather than the single spelling `created_at`.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"

#: Every column `analysis_runs` has, read from the deployed table on 2026-10-06.
#: Note what is NOT here: `created_at` and `updated_at`. The table's timestamps are
#: `started_at` and `completed_at`.
ANALYSIS_RUN_COLUMNS = frozenset(
    {
        "id",
        "drawing_set_id",
        "status",
        "model_used",
        "pages_processed",
        "total_pages",
        "error_message",
        "started_at",
        "completed_at",
    }
)


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in {REPOSITORY_PATH}")


def _string_args(node: ast.Call) -> list[str]:
    return [
        argument.value
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]


def _query_columns(function_name: str) -> dict[str, list[str]]:
    """Every column `select(...)` names and every column `order(...)` names."""
    found: dict[str, list[str]] = {"select": [], "order": []}
    for node in ast.walk(_function(function_name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in found:
                for literal in _string_args(node):
                    if method == "select":
                        found["select"].extend(
                            part.strip() for part in literal.split(",") if part.strip()
                        )
                    else:
                        found["order"].append(literal.strip())
    return found


def test_the_run_reader_names_only_columns_the_table_has():
    """The defect, and its whole class: a column name the schema would refuse."""
    named = _query_columns("analysis_runs_for_drawing_set")
    unknown = sorted((set(named["select"]) | set(named["order"])) - ANALYSIS_RUN_COLUMNS)
    assert unknown == [], (
        f"analysis_runs_for_drawing_set queries {unknown}, which analysis_runs does not "
        f"have; the table's columns are {sorted(ANALYSIS_RUN_COLUMNS)}"
    )


def test_it_reads_the_run_timestamp_the_table_actually_has():
    """The specific regression: `started_at` is the table's creation timestamp."""
    named = _query_columns("analysis_runs_for_drawing_set")
    assert "started_at" in named["select"]
    assert "started_at" in named["order"]


def test_it_never_names_the_column_that_does_not_exist():
    """A guard on the exact spelling that shipped, stated on its own so a future reader sees
    why it is named here rather than only covered by the set above."""
    named = _query_columns("analysis_runs_for_drawing_set")
    assert "created_at" not in named["select"]
    assert "created_at" not in named["order"]


def test_the_recorded_columns_are_corroborated_by_the_writer():
    """The same set, checked from the other direction: every column `create_analysis_run`
    inserts must be one of these. A set that disagreed with the writer would be a set this
    file had invented rather than read."""
    inserted: set[str] = set()
    for node in ast.walk(_function("create_analysis_run")):
        if isinstance(node, ast.Dict):
            inserted |= {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    assert inserted, "create_analysis_run inserts no columns, so it corroborates nothing"
    assert inserted <= ANALYSIS_RUN_COLUMNS, sorted(inserted - ANALYSIS_RUN_COLUMNS)


def test_the_reader_still_filters_by_the_drawing_set():
    """The correction changed two tokens and must not have changed the filter: the query is
    still one drawing set's runs, addressed by the id it is given — read from the call, not
    from how it happens to be quoted."""
    filters = [
        node
        for node in ast.walk(_function("analysis_runs_for_drawing_set"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "eq"
    ]
    assert len(filters) == 1, "the reader must filter on exactly one column"
    assert _string_args(filters[0]) == ["drawing_set_id"]
