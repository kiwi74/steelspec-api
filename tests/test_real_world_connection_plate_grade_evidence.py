"""
CONNECTION PLATE GRADE EVIDENCE — the drawing states it, or it is None.

WHAT THIS FILE PROVES

app/pipeline.py is the only place production turns an AI page extraction into a
persisted `connection_plates` row. One behaviour there manufactured a plate grade
the drawing never stated:

    "grade": "300"        every plate, always, two lines below the thickness rule

Step C fixed the two adjacent defects and named this one as the next directly
adjacent case. It is the same class: a fabricated value in a persisted row is
indistinguishable downstream from a stated one, and nothing can tell them apart.

The precedence this file pins — unchanged, and the only one this field may follow:

    drawing evidence  >  absence of evidence  >  catalogue defaults
                      >  historical assumptions  >  hardcoded defaults

so that:
    - a plate grade the extraction reported survives VERBATIM;
    - a plate grade it did not report is persisted as NULL, not as a default;
    - the connection's material, the member grades, the plate's own dimensions,
      the catalogue row, the project, the filename and every convention are NOT
      plate-grade evidence and never populate this field.

WHAT IS REAL HERE

The real production path is exercised end to end: the real `app/pipeline.py`
`_run_pipeline`, the real `validate_extraction`, the real `SectionMatcher`
against an injected repository double (no network), with the vision stage — the
only genuinely external stage — replaced by the real `PageExtraction` dataclass,
and the persistence stage replaced by the recording double that invents nothing.
Both are the Step C harness, imported rather than copied.

The real Selby Square captures are asserted against verbatim, and they are the
decisive evidence. `selby_square_pages_16_20_extractions.json` page 17 holds a
connection that states `material: "300"` — a REAL connection material — carrying
a REAL plate `{"type": "end_plate", "thickness_mm": 20, "width_mm": 180,
"depth_mm": 340}` that states no grade. Across all six Selby captures and the
Arkles capture, the only plate keys that ever appear are type, thickness_mm,
width_mm and depth_mm: the extraction model gives a plate no material field at
all. Under the old behaviour every one of those real plates was persisted with a
fabricated "300". Nothing in this file writes to a capture.

The mutations rebuild the defect as REAL SOURCE — a throwaway copy of
app/pipeline.py, imported and executed — so they prove the checks fire on a
genuine build rather than on a description of one.

THE DXF PATH'S OWN HARDCODED PLATE GRADE (Milestone J8B)

This file's first version deliberately left that one out of scope, and its guard
against a second writer matched only the repository spelling
`insert_connection_plates`. The DXF import path writes this table DIRECTLY —
`supabase.table("connection_plates").insert(...)` in
app/drawing_reading/dxf_parser.py — so the guard could not see the very writer it
was written to worry about, and that writer really did persist a hardcoded
`"grade": "300"` for every plate it extracted. J8B removed that literal, and the
guard below now finds every production writer of this table by AST rather than by
one spelling.

SCOPE — still deliberately NOT in this milestone (reported, not silently
expanded): bolt-grade placeholders, display formatting, catalogue versioning, the
extraction-persistence gap, and any further Supabase schema change.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from tests.test_project_extraction_intake import CAPTURE_PATH, load_capture
from tests.test_real_world_production_extraction_evidence import (
    PIPELINE_PATH,
    PERSISTED_PLATE_KEYS,
    REPO,
    _FakeSupabaseClient,
    _connection,
    _plate,
    _pinned_index,
    _RecordingRepo,
    run_production,
)
from tests.test_real_world_production_section_authority import (
    LIVE_310UB40_4,
    LIVE_310UB46_2,
    PINNED_ROWS,
    _arm_cad_boundary,
    _member,
    modules,
)

# The real captures that carry plates. Page 17 of the first states a material for
# the connection that owns one of them; nothing anywhere states a plate grade.
SELBY_WITH_CONNECTION_MATERIAL = REPO / "tests" / "data" / "selby_square_pages_16_20_extractions.json"
SELBY_WITH_MORE_PLATES = REPO / "tests" / "data" / "selby_square_pages_26_30_extractions.json"

needs_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason=f"the real Arkles capture is not present at {CAPTURE_PATH.relative_to(REPO)}",
)
needs_selby = pytest.mark.skipif(
    not SELBY_WITH_CONNECTION_MATERIAL.exists(),
    reason="the real Selby Square captures are not present under tests/data",
)


# ======================================================================================
# The focused rules, as pure functions over the REAL run's output and the REAL source.
#
# These are the checks the mutation tests re-run against a deliberately unfixed
# build. They are written from the RULE ("a persisted value is a claim about the
# drawing"), never from the implementation, so restoring the old behaviour has to
# fail them.
# ======================================================================================
def _stated_plate_grades(raw_connections: list[dict]) -> set[str]:
    """Every designation a PLATE itself stated, across the whole extraction."""
    return {
        value
        for c in raw_connections
        for p in (c.get("plates") or [])
        for value in (p.get("grade"), p.get("material"))
        if isinstance(value, str) and value.strip()
    }


def _fabricated_plate_grades(raw_connections: list[dict], persisted_plates: list[dict]) -> list[dict]:
    """Every persisted plate grade that NO plate stated — a claim the drawing never made."""
    stated = _stated_plate_grades(raw_connections)
    return [
        p for p in persisted_plates
        if p.get("grade") is not None and p["grade"] not in stated
    ]


def _literal_grade_values(source: str) -> list[str]:
    """Source-level: every dict literal in app/pipeline.py that states a grade as a
    hardcoded string. A persisted grade is evidence, so it can only come from the
    extraction — a literal in the source is a fabricated one, whichever field it
    lands in."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in ("grade", "material"):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.append(value.value)
    return found


PLATE_GRADE_CALL = '"grade": _extracted_plate_grade(p)'
MEMBER_GRADE_DEFAULT = '"grade": "300PLUS",'
PLATE_GRADE_DEFAULT = '"grade": "300",'


def _unfixed_plate_grade_source(source: str) -> str:
    """The pre-milestone source, reconstructed by inverting exactly this milestone's
    edit — the 'restore the old behaviour' mutation, as text."""
    unfixed = source.replace(PLATE_GRADE_CALL, PLATE_GRADE_DEFAULT)
    assert unfixed != source, "the plate-grade edit anchor moved"
    return unfixed


# ======================================================================================
# MUTATION TRANSFORMS — the old behaviour, restored as real source.
#
# Each returns a complete, importable copy of app/pipeline.py. They are executed
# from a throwaway file (see `run_mutated`), so a mutation is a genuine build of
# the defect rather than an assertion about one.
# ======================================================================================
M3_INHERIT_MEMBER_GRADE = '''

def _member_grade_for(connection, member_rows):
    """MUTATION M3 — a plate inherits the grade of a member it connects."""
    linked = set(connection.get("connects_members") or [])
    for row in member_rows:
        if row.get("mark") in linked and row.get("grade"):
            return row["grade"]
    return None

'''

M5_FROM_DIMENSIONS = '''

def _grade_from_dimensions(plate):
    """MUTATION M5 — a grade derived from the plate's own dimensions."""
    thickness = plate.get("thickness_mm")
    return "300" if thickness and thickness <= 16 else "300PLUS"

'''


def _mutate(source: str, *edits: tuple[str, str]) -> str:
    """Apply (anchor, replacement) edits, each asserted to have landed."""
    for anchor, replacement in edits:
        assert anchor in source, f"mutation anchor moved: {anchor!r}"
        source = source.replace(anchor, replacement)
    return source


def _m1_restores_the_hardcoded_literal(source: str) -> str:
    return _mutate(source, (PLATE_GRADE_CALL, PLATE_GRADE_DEFAULT))


def _m2_restores_it_through_an_equivalent_default(source: str) -> str:
    return _mutate(source, (PLATE_GRADE_CALL, '"grade": _extracted_plate_grade(p) or "300",'))


def _m3_inherits_the_member_grade(source: str) -> str:
    return _mutate(
        source,
        ("def _persist_connections(", M3_INHERIT_MEMBER_GRADE + "def _persist_connections("),
        (PLATE_GRADE_CALL,
         '"grade": _extracted_plate_grade(p) or _member_grade_for(c, member_rows),'),
    )


def _m4_inherits_the_connection_material(source: str) -> str:
    return _mutate(
        source,
        (PLATE_GRADE_CALL, '"grade": _extracted_plate_grade(p) or c.get("material"),'),
    )


def _m5_derives_it_from_the_dimensions(source: str) -> str:
    return _mutate(
        source,
        ("def _run_pipeline(", M5_FROM_DIMENSIONS + "def _run_pipeline("),
        (PLATE_GRADE_CALL, '"grade": _extracted_plate_grade(p) or _grade_from_dimensions(p),'),
    )


MUTATIONS = {
    "M1": _m1_restores_the_hardcoded_literal,
    "M2": _m2_restores_it_through_an_equivalent_default,
    "M3": _m3_inherits_the_member_grade,
    "M4": _m4_inherits_the_connection_material,
    "M5": _m5_derives_it_from_the_dimensions,
}


def _import_pipeline_copy(source: str, path: Path):
    """Import a throwaway copy of the pipeline and return the module. Never touches
    the working tree: the copy lives in pytest's tmp_path."""
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("pipeline_under_mutation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def run_mutated(modules, monkeypatch, tmp_path):
    """
    run(source, raw_members, raw_connections=()) -> the recording repo, driving a
    throwaway COPY of app/pipeline.py built from `source`, through the same
    persistence double every other test here uses.
    """
    armed = {"cad": False}

    def run(source, raw_members, raw_connections=()):
        mutated = _import_pipeline_copy(source, tmp_path / "pipeline_under_mutation.py")

        monkeypatch.setattr(modules.section_matcher, "supabase", _FakeSupabaseClient(_pinned_index()))
        if not armed["cad"]:
            _arm_cad_boundary(monkeypatch)
            armed["cad"] = True

        page = modules.PageExtraction(
            page_number=1, drawing_number="S101", drawing_title="Framing Plan",
            revision="A", raw_members=list(raw_members), raw_connections=list(raw_connections),
        )
        monkeypatch.setattr(mutated, "analyze_pdf_pages", lambda *a, **k: [page])
        # Milestone J15: the simulated document is exactly this one page, so its
        # coverage is complete and the run under mutation is judged on the plate
        # grade rules this file is about.
        monkeypatch.setattr(mutated, "page_count_of", lambda *a, **k: 1)

        repo = _RecordingRepo()
        monkeypatch.setattr(mutated, "repo", repo)

        mutated._run_pipeline("unused.pdf", "project-1", "user-1", "drawing-set-1", "drawing-1",
                              analysis_run_id="run-1")
        return repo

    return run


# ======================================================================================
# THE SOURCE RULES — no fabricated plate grade, and no literal to fabricate one from.
# ======================================================================================
# ======================================================================================
# EVERY production writer of `connection_plates`, found by SHAPE rather than by one
# spelling.
#
# A persisted grade is only trustworthy if the set of places that can write one is
# known, and the previous guard here knew one spelling — `insert_connection_plates`
# — which is why it could not see the DXF path writing the same table directly.
# The detector below recognises every shape a writer takes in this repository:
#
#     X.table("connection_plates").insert(...)      direct Supabase, verb-spelled
#     X.table("connection_plates").upsert(...)      the same, and its siblings
#     repo.insert_connection_plates(...)            through the repository helper
#     <SQL string> "insert into connection_plates"  raw SQL, if one ever appears
#
# A `.select(...)` on the same table is a READ and is deliberately not reported.
# ======================================================================================
CONNECTION_PLATES = "connection_plates"
_TABLE_WRITE_VERBS = frozenset(
    {"insert", "upsert", "update", "delete", "insert_many", "bulk_insert", "upsert_many"}
)
_REPOSITORY_WRITE_WORDS = ("insert", "upsert", "update", "delete", "write", "save")
_SQL_WRITE_RE = re.compile(
    r"\b(insert\s+into|update|delete\s+from)\s+[\w\".]*connection_plates\b", re.IGNORECASE
)


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The nodes that are docstrings — prose ABOUT the rule, never the rule."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _is_connection_plates_table_call(node: ast.AST) -> bool:
    """`<anything>.table("connection_plates")`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "table"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == CONNECTION_PLATES
    )


def _connection_plates_writers(source: str) -> list[str]:
    """
    Every statement in `source` that WRITES rows into connection_plates, as
    "<shape>:<spelling>" so that a failure names the shape that appeared.
    """
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    writers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and _SQL_WRITE_RE.search(node.value):
                writers.append("sql:write")
            continue
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in _TABLE_WRITE_VERBS:
            if _is_connection_plates_table_call(node.func.value):
                writers.append(f"direct-supabase:{node.func.attr}")
            continue
        if (isinstance(node.func, ast.Attribute)
                and CONNECTION_PLATES in node.func.attr
                and any(word in node.func.attr for word in _REPOSITORY_WRITE_WORDS)):
            writers.append(f"repository:{node.func.attr}")
    return sorted(writers)


# The complete set of production writers of this table, with the reason each one
# is allowed to exist. A new module here is a FAILURE, not a maintenance task:
# every writer is a place a plate grade can be asserted about a drawing.
EXPECTED_CONNECTION_PLATE_WRITERS = {
    # The PDF path. Its plate grade comes from the extraction
    # (app.pipeline._extracted_plate_grade), never from a literal.
    "app/pipeline.py": ["repository:insert_connection_plates"],
    # The repository helper's own body — the single place the PDF path's rows
    # actually reach the table, and reachable from app/pipeline.py only.
    "app/engineering_data/repository.py": ["direct-supabase:insert"],
    # The DXF path. It states no plate grade at all (J8B): the callout carries a
    # type and a thickness and nothing else.
    "app/drawing_reading/dxf_parser.py": ["direct-supabase:insert"],
}


class TestEveryWriterOfConnectionPlatesIsKnown:

    def _observed(self) -> dict[str, list[str]]:
        observed = {}
        for path in sorted((REPO / "app").rglob("*.py")):
            writers = _connection_plates_writers(path.read_text())
            if writers:
                observed[path.relative_to(REPO).as_posix()] = writers
        return observed

    def test_the_writers_are_exactly_the_known_ones(self):
        """The guard the DXF defect walked past: it matched one spelling, so a
        module that wrote this table any other way was invisible to it."""
        observed = self._observed()
        unexpected = {
            module: writers for module, writers in observed.items()
            if EXPECTED_CONNECTION_PLATE_WRITERS.get(module) != writers
        }
        assert observed == EXPECTED_CONNECTION_PLATE_WRITERS, (
            "a production writer of connection_plates appeared, disappeared or changed "
            f"shape: {unexpected}"
        )

    def test_the_detector_sees_the_write_the_old_guard_could_not(self):
        """The control, over the REAL source of the REAL writer. The old detector
        is reproduced here exactly as it stood, and must find nothing in a module
        that really writes this table."""
        dxf = (REPO / "app" / "drawing_reading" / "dxf_parser.py").read_text()
        assert "direct-supabase:insert" in _connection_plates_writers(dxf)

        old_guard = [
            node for node in ast.walk(ast.parse(dxf))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert_connection_plates")
        ]
        assert old_guard == [], "the old spelling-only guard was not actually blind here"

    @pytest.mark.parametrize("source,expected", [
        ('db.table("connection_plates").insert(rows).execute()', ["direct-supabase:insert"]),
        ('db.table("connection_plates").upsert(rows).execute()', ["direct-supabase:upsert"]),
        ('db.table("connection_plates").update({"grade": None}).eq("id", i).execute()',
         ["direct-supabase:update"]),
        ('db.table("connection_plates").delete().eq("id", i).execute()',
         ["direct-supabase:delete"]),
        ('repo.insert_connection_plates(connection_id, rows)', ["repository:insert_connection_plates"]),
        ('db.rpc("q", {"sql": "insert into connection_plates (id) values (1)"})', ["sql:write"]),
        # Not writes, and not this table: neither may be reported.
        ('rows = db.table("connection_plates").select("*").execute()', []),
        ('db.table("steel_members").insert(rows).execute()', []),
        ('db.table("connection_members").insert(links).execute()', []),
    ])
    def test_the_detector_covers_every_write_shape(self, source, expected):
        assert _connection_plates_writers(source) == expected


class TestTheProductionSourceStatesNoFabricatedPlateGrade:

    def test_no_dict_literal_in_the_pipeline_states_a_grade(self):
        assert _literal_grade_values(PIPELINE_PATH.read_text()) == []

    def test_the_persisted_plate_grade_comes_from_the_extraction_helper(self):
        assert PLATE_GRADE_CALL in PIPELINE_PATH.read_text()

    def test_the_source_rule_detects_the_old_behaviour(self):
        """The controls: the check above is only meaningful if it fires on the
        source this milestone replaced, and stays quiet on the one Step C left."""
        source = PIPELINE_PATH.read_text()
        assert set(_literal_grade_values(_unfixed_plate_grade_source(source))) == {"300"}
        assert _literal_grade_values(source) == []
        # Step C's own edit is untouched by this milestone: the member default is
        # still gone from the same source this rule now reads.
        assert MEMBER_GRADE_DEFAULT not in source


# ======================================================================================
# EXPLICIT EVIDENCE — a plate grade the drawing states survives, verbatim.
# ======================================================================================
class TestExplicitPlateGradeEvidence:

    @pytest.mark.parametrize("stated", [
        "300",
        "300PLUS",
        "AS/NZS 3678-300",
        "300 PLUS",
        "Grade 350",
        "S355JR",
    ])
    def test_an_explicit_plate_grade_survives_verbatim(self, run_production, stated):
        run = run_production(
            [_member("310UB46.2")], [_connection(plates=[_plate(grade=stated)])],
        )
        assert run.plates[0]["grade"] == stated

    def test_a_plate_material_key_is_explicit_evidence_too(self, run_production):
        """Whichever key a plate states its own designation under is the plate's
        evidence, read the same way and preserved in the drawing's wording."""
        run = run_production(
            [_member("310UB46.2")], [_connection(plates=[_plate(material="AS/NZS 3678-300")])],
        )
        assert run.plates[0]["grade"] == "AS/NZS 3678-300"

    @pytest.mark.parametrize("absent", [
        {},                        # no grade evidence at all
        {"grade": None},           # reported null
        {"grade": ""},             # reported blank
        {"grade": "   "},          # reported whitespace
        {"grade": "\t\n"},         # reported whitespace, another shape
        {"grade": 300},            # not a designation the drawing stated
        {"grade": []},             # not a designation the drawing stated
        {"grade": {}},             # not a designation the drawing stated
        {"grade": True},           # a bool is not a designation
        {"material": None},        # the other key, reported null
    ])
    def test_absent_grade_evidence_persists_unknown(self, run_production, absent):
        plate = _plate()
        plate.pop("grade", None)
        plate.update(absent)
        run = run_production([_member("310UB46.2")], [_connection(plates=[plate])])
        assert run.plates[0]["grade"] is None


# ======================================================================================
# NO INVENTION — nothing else may populate the field.
# ======================================================================================
class TestNoPlateGradeIsInvented:

    def test_no_run_persists_the_300_literal(self, run_production):
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[{"type": "end_plate"}, _plate(thickness_mm=20)])],
        )
        assert "300" not in json.dumps(run.plates)

    def test_no_run_persists_the_300plus_literal(self, run_production):
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[{"type": "base_plate", "width_mm": 200, "depth_mm": 200}])],
        )
        assert "300PLUS" not in json.dumps(run.plates)

    def test_a_plate_grade_is_not_inherited_from_a_member_grade(self, run_production):
        """A member joined by the connection states a grade. The plate does not,
        so the plate persists none — while the member keeps its own."""
        run = run_production(
            [{**_member("310UB46.2", mark="B1"), "material": "AS/NZS 3678-300"}],
            [_connection(("B1",), plates=[_plate()])],
        )
        assert run.member_for("B1")["grade"] == "AS/NZS 3678-300"   # real member evidence
        assert run.plates[0]["grade"] is None                       # not the plate's

    def test_a_plate_grade_is_not_inherited_from_the_connection_material(self, run_production):
        """The extraction states material for the CONNECTION. A plate is not the
        connection and the contract gives it no material field, so that
        association does not exist and the plate's grade is None."""
        run = run_production(
            [_member("310UB46.2", mark="B1")],
            [_connection(("B1",), material="300PLUS", plates=[_plate()])],
        )
        assert run.plates[0]["grade"] is None

    def test_a_plate_grade_is_not_inherited_from_an_unrelated_material(self, run_production):
        """The same connection material, two plates, neither stating a grade."""
        run = run_production(
            [_member("310UB46.2", mark="B1")],
            [_connection(("B1",), material="300",
                         plates=[_plate(), _plate(type="web plate", thickness_mm=10)])],
        )
        assert [p["grade"] for p in run.plates] == [None, None]

    def test_a_plate_grade_is_not_derived_from_the_catalogue_row(self, run_production):
        """A matched catalogue row enriches weight/family ONLY. Asserted on the
        row's own columns, so a future catalogue column cannot quietly become a
        plate grade source without failing here."""
        assert not any(
            "grade" in key.lower() or "material" in key.lower() for key in PINNED_ROWS[0]
        ), "the reference row now carries grade-like data; this rule must be re-argued"
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate()])])
        assert run.member_for("B1")["weight_per_metre"] == LIVE_310UB46_2["weight_per_metre"]
        assert run.plates[0]["grade"] is None

    @pytest.mark.parametrize("dimensions", [
        {"thickness_mm": 10},
        {"thickness_mm": 20},
        {"thickness_mm": 20, "width_mm": 180, "depth_mm": 340},
        {"thickness_mm": 6, "width_mm": 100},
        {"thickness_mm": 40, "width_mm": 400, "depth_mm": 400},
    ])
    def test_a_plate_grade_is_not_derived_from_the_plate_dimensions(self, run_production, dimensions):
        run = run_production(
            [_member("310UB46.2")], [_connection(plates=[{"type": "end_plate", **dimensions}])],
        )
        assert run.plates[0]["grade"] is None

    def test_there_is_no_default_to_fall_back_to(self, run_production):
        """A run with no plate-grade evidence anywhere persists no plate grade
        anywhere, across every plate shape the extraction can produce."""
        run = run_production(
            [_member("310UB46.2", mark="B1")],
            [_connection(("B1",), material="300",
                         plates=[{"type": "end_plate"},
                                 _plate(),
                                 {"type": "base_plate", "thickness_mm": 20}])],
        )
        assert [p["grade"] for p in run.plates] == [None, None, None]


# ======================================================================================
# AUTHORITY — Step C's boundaries, and the section rules, are unchanged.
# ======================================================================================
class TestTheAuthorityBoundaryIsUnchanged:

    def test_the_thickness_rule_from_step_c_still_holds(self, run_production):
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[_plate(thickness_mm=20),
                                 {"type": "web plate"},
                                 {"type": "base_plate", "thickness_mm": 0}])],
        )
        assert [p["thickness"] for p in run.plates] == [20, None, None]

    def test_an_exact_match_still_enriches_and_still_states_no_plate_grade(self, run_production):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate()])])
        row = run.member_for("B1")
        assert row["section_name"] == LIVE_310UB46_2["name"]
        assert row["weight_per_metre"] == LIVE_310UB46_2["weight_per_metre"]
        assert row["grade"] is None
        assert run.plates[0]["grade"] is None

    def test_a_refused_suffix_fallback_still_enriches_nothing(self, run_production):
        """310UB40 is not in the catalogue; the offered row is 310UB40.4, a
        DIFFERENT section. Refused as identity and as properties alike — and it
        cannot supply a plate grade either."""
        run = run_production([_member("310UB40")], [_connection(plates=[_plate()])])
        row = run.member_for("B1")
        # CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C): the drawn
        # token stays the member's identity in section_name_raw, while
        # section_name — the FK-bound column — is NULL, because "310UB40" is not
        # a key of steel_sections and the candidate is a different section. The
        # refusal is proved the same way it always was: the row never adopts the
        # offered row's name.
        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["section_name"] != LIVE_310UB40_4["name"]
        assert row["section_family"] is None and row["weight_per_metre"] is None
        assert row["review_status"] == "review_required"
        assert run.plates[0]["grade"] is None

    def test_an_unmatched_section_is_unchanged(self, run_production):
        run = run_production([_member("NOT-A-SECTION")], [_connection(plates=[_plate()])])
        row = run.member_for("B1")
        assert row["section_name"] is None and row["grade"] is None
        assert row["review_status"] == "review_required"
        assert run.plates[0]["grade"] is None


# ======================================================================================
# THE REAL CAPTURES — what the drawings actually state, verbatim.
# ======================================================================================
@pytest.fixture(scope="module")
def selby_pages():
    return load_capture(SELBY_WITH_CONNECTION_MATERIAL)


def _real_connection_with_a_plate_and_a_material(pages):
    """The page-17 connection: a real stated material AND a real plate."""
    return next(
        c for page in pages for c in (page.get("raw_connections") or [])
        if c.get("plates") and c.get("material")
    )


@needs_selby
class TestTheRealCaptures:

    def test_a_real_connection_states_a_material_and_its_real_plate_states_none(self, selby_pages):
        """The premise, asserted against the capture rather than assumed. This is
        the whole case: real connection material, real plate, no plate grade."""
        connection = _real_connection_with_a_plate_and_a_material(selby_pages)
        assert connection["material"] == "300"
        plate = connection["plates"][0]
        assert plate == {"type": "end_plate", "thickness_mm": 20, "width_mm": 180, "depth_mm": 340}
        assert "grade" not in plate and "material" not in plate

    def test_no_real_plate_in_any_capture_states_a_grade(self):
        """Across every real capture on disk, the plate schema is the extraction
        contract's: a type and its dimensions. There is no plate-grade field."""
        keys = set()
        plates = 0
        for path in sorted((REPO / "tests" / "data").glob("*extractions.json")):
            for page in load_capture(path):
                for c in (page.get("raw_connections") or []):
                    for plate in (c.get("plates") or []):
                        keys |= set(plate)
                        plates += 1
        assert plates > 0, "no real plate exists; this proof would be vacuous"
        assert keys == {"type", "thickness_mm", "width_mm", "depth_mm"}, (
            "a real capture now states a plate key this milestone must account for"
        )

    def test_a_real_plate_persists_no_grade_even_though_its_connection_states_one(
            self, run_production, selby_pages):
        """Page 17, verbatim: the connection states material "300" and owns a real
        end plate. The plate persists None — and keeps every field it did state."""
        connection = _real_connection_with_a_plate_and_a_material(selby_pages)
        run = run_production([], [copy.deepcopy(connection)])
        assert len(run.plates) == 1
        plate = run.plates[0]
        assert plate["grade"] is None
        assert plate["plate_type"] == "end_plate"
        assert plate["thickness"] == 20
        assert plate["width"] == 180 and plate["depth"] == 340

    def test_the_whole_real_selby_captures_persist_no_fabricated_plate_grade(self, run_production):
        pages = load_capture(SELBY_WITH_CONNECTION_MATERIAL) + load_capture(SELBY_WITH_MORE_PLATES)
        raw_members = [m for page in pages for m in (page.get("raw_members") or [])]
        raw_connections = [c for page in pages for c in (page.get("raw_connections") or [])]
        run = run_production(raw_members, raw_connections)
        assert len(run.plates) == 6, "the real capture's plates must all reach persistence"
        assert _fabricated_plate_grades(raw_connections, run.plates) == []
        assert {p["grade"] for p in run.plates} == {None}
        assert '"300"' not in json.dumps(run.plates)

    def test_a_real_plate_that_does_state_a_grade_still_persists_it(self, run_production, selby_pages):
        """The rule is not 'always null'. The SAME real plate row — the one the
        fabricated default was applied to — keeps a designation when the drawing
        states one. (The stated value is the synthetic part; the plate is real.)"""
        connection = _real_connection_with_a_plate_and_a_material(selby_pages)
        real_plate = copy.deepcopy(connection["plates"][0])
        stated = {**real_plate, "grade": "AS/NZS 3678-300"}
        run = run_production([], [{**copy.deepcopy(connection), "plates": [stated]}])
        assert run.plates[0]["grade"] == "AS/NZS 3678-300"
        # …and the same row, with no grade stated, is persisted as unknown.
        without = run_production([], [copy.deepcopy(connection)])
        assert without.plates[0]["grade"] is None

    @needs_capture
    def test_the_real_arkles_capture_has_no_plate_data_to_grade(self):
        """Honest accounting: this capture carries three connections and no plates
        at all, so it proves nothing about a plate grade and is not claimed to."""
        pages = load_capture(CAPTURE_PATH)
        assert [c for page in pages for c in (page.get("raw_connections") or [])]
        assert [p for page in pages for c in (page.get("raw_connections") or [])
                for p in (c.get("plates") or [])] == []


# ======================================================================================
# REGRESSION — the real path, and the shape it persists.
# ======================================================================================
class TestTheProductionPathIsUnchangedApartFromTheGrade:

    def test_the_real_pipeline_path_is_the_one_under_test(self, run_production):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate()])])
        assert "insert_connection_plates" in run.repo.calls
        assert "insert_connection" in run.repo.calls
        assert "update_project_summary" in run.repo.calls
        assert run.summary["connections_extracted"] == 1

    def test_the_persisted_plate_row_has_exactly_the_same_keys_and_order(self, run_production):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate()])])
        assert tuple(run.plates[0]) == PERSISTED_PLATE_KEYS

    def test_no_unrelated_plate_field_changed(self, run_production):
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[_plate(type="flange plate", thickness_mm=20,
                                        width_mm=180, depth_mm=340)])],
        )
        plate = run.plates[0]
        assert plate["plate_type"] == "flange plate"
        assert plate["thickness"] == 20
        assert plate["width"] == 180
        assert plate["depth"] == 340
        assert plate["grade"] is None

    def test_plates_are_still_attached_to_their_own_connection(self, run_production):
        run = run_production(
            [_member("310UB46.2", mark="B1")],
            [_connection(("B1",), plates=[_plate()]),
             _connection(("B1",), plates=[_plate(type="web plate", thickness_mm=10)])],
        )
        assert [p["plate_type"] for p in run.plates] == ["end_plate", "web plate"]
        assert run.repo.calls.count("insert_connection_plates") == 2
        assert run.repo.connections[0]["connection_type"] == "bolted"


# ======================================================================================
# MUTATIONS — the checks above must fail on the behaviour they replaced.
# Each mutation is the real source, rebuilt and executed from a throwaway copy.
# The working tree is proved untouched.
# ======================================================================================
class TestMutations:

    @pytest.fixture(autouse=True)
    def the_working_tree_is_untouched(self):
        before = PIPELINE_PATH.read_bytes()
        sha_before = hashlib.sha256(before).hexdigest()
        yield
        after = PIPELINE_PATH.read_bytes()
        assert after == before, "a mutation test modified app/pipeline.py"
        assert hashlib.sha256(after).hexdigest() == sha_before
        assert _literal_grade_values(after.decode()) == []

    @pytest.fixture()
    def source(self):
        return PIPELINE_PATH.read_text()

    def test_the_mutations_change_the_source(self, source):
        """The control: a mutation that changed nothing would prove nothing."""
        for name, mutate in MUTATIONS.items():
            assert mutate(source) != source, f"{name} did not change the source"

    def test_m1_restoring_the_hardcoded_literal_is_caught(self, source, run_mutated):
        """M1 — the old line, `"grade": "300",`, restored exactly."""
        mutated = _m1_restores_the_hardcoded_literal(source)
        assert PLATE_GRADE_DEFAULT in mutated
        assert set(_literal_grade_values(mutated)) == {"300"}
        raw_connections = [_connection(plates=[_plate()])]
        repo = run_mutated(mutated, [_member("310UB46.2")], raw_connections)
        assert repo.plates[0]["grade"] == "300"
        assert _fabricated_plate_grades(raw_connections, repo.plates) == repo.plates

    def test_m2_the_equivalent_default_is_caught(self, source, run_mutated):
        """M2 — the same defect reached through a helper default rather than a
        literal at the row. The source-literal rule cannot see this shape (the
        value is no longer a bare literal), which is precisely why the BEHAVIOURAL
        rule, not the source rule, has to be the one that catches it."""
        mutated = _m2_restores_it_through_an_equivalent_default(source)
        assert _literal_grade_values(mutated) == []
        assert '"300"' in mutated
        raw_connections = [_connection(plates=[_plate()])]
        repo = run_mutated(mutated, [_member("310UB46.2")], raw_connections)
        assert repo.plates[0]["grade"] == "300"
        assert _fabricated_plate_grades(raw_connections, repo.plates) == repo.plates

    def test_m3_inheriting_the_member_grade_is_caught(self, source, run_mutated):
        """M3 — a missing plate grade inherited from a member the connection joins."""
        mutated = _m3_inherits_the_member_grade(source)
        member = {**_member("310UB46.2", mark="B1"), "material": "AS/NZS 3678-300"}
        raw_connections = [_connection(("B1",), plates=[_plate()])]
        repo = run_mutated(mutated, [member], raw_connections)
        assert repo.plates[0]["grade"] == "AS/NZS 3678-300"
        assert _fabricated_plate_grades(raw_connections, repo.plates) == repo.plates

    def test_m4_inheriting_the_connection_material_is_caught(self, source, run_mutated):
        """M4 — a missing plate grade inherited from the connection's material.
        The extraction contract scopes material to the connection and the members
        it joins, never to a plate, so this association does not exist."""
        mutated = _m4_inherits_the_connection_material(source)
        raw_connections = [_connection(("B1",), material="300", plates=[_plate()])]
        repo = run_mutated(mutated, [_member("310UB46.2", mark="B1")], raw_connections)
        assert repo.plates[0]["grade"] == "300"
        assert _fabricated_plate_grades(raw_connections, repo.plates) == repo.plates

    def test_m5_deriving_the_grade_from_the_dimensions_is_caught(self, source, run_mutated):
        """M5 — a grade derived from the plate's own thickness."""
        mutated = _m5_derives_it_from_the_dimensions(source)
        raw_connections = [
            _connection(("B1",), plates=[_plate(thickness_mm=20)]),
            _connection(("B1",), plates=[_plate(type="web plate", thickness_mm=10)]),
        ]
        repo = run_mutated(mutated, [_member("310UB46.2", mark="B1")], raw_connections)
        assert [p["grade"] for p in repo.plates] == ["300PLUS", "300"]
        assert _fabricated_plate_grades(raw_connections, repo.plates) == repo.plates

    def test_the_unmutated_build_passes_every_focused_rule(self, run_production):
        """The control for all five mutations, on the same run shape."""
        raw_connections = [_connection(("B1",), material="300", plates=[_plate(thickness_mm=20)])]
        run = run_production([_member("310UB46.2", mark="B1")], raw_connections)
        assert _fabricated_plate_grades(raw_connections, run.plates) == []
        assert [p["grade"] for p in run.plates] == [None]
        assert _literal_grade_values(PIPELINE_PATH.read_text()) == []
