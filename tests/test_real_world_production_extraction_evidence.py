"""
PRODUCTION EXTRACTION EVIDENCE — the drawing states it, or it is unknown.

WHAT THIS FILE PROVES

app/pipeline.py is the ONLY place production turns an AI page extraction into a
persisted `steel_members` / `connection_plates` row. Two behaviours there turned
ABSENT drawing evidence into apparently authoritative engineering values:

    "grade": "300PLUS"                        every member, always
    p.get("thickness_mm") or 0                every plate with no thickness

The first invents a material designation the drawing may never have stated. The
second collapses "not shown on this page" into a measured 0 mm plate — and a
persisted 0 is indistinguishable downstream from a real dimension.

The precedence this file pins:

    drawing evidence  >  absence of evidence  >  catalogue defaults
                      >  historical assumptions  >  hardcoded defaults

so that:
    - a grade the extraction reported survives VERBATIM;
    - a grade it did not report is persisted as NULL, not as a default;
    - a positive plate thickness survives;
    - an absent or invalid one is NULL, never 0.

WHAT IS REAL HERE

The real production path is exercised end to end: the real `app/pipeline.py`
`_run_pipeline`, the real `validate_extraction`, the real `SectionMatcher`
against an injected repository double (no network), with the vision stage — the
only genuinely external stage — replaced by the real `PageExtraction` dataclass,
and the persistence stage replaced by a recording double that invents nothing.

The real Arkles Strand capture (`tests/data/arkles_strand_page_extractions.json`)
is asserted against verbatim. It is the decisive evidence for the grade rule:
its 105 member rows carry NO material or grade key at all, and its 3 connection
rows carry no plate data — so before this milestone every one of its members was
persisted with a fabricated "300PLUS". Nothing in this file writes to the capture.

SCOPE — deliberately NOT in this milestone (reported, not silently expanded):
    - `connection_plates.grade`, hardcoded "300" two lines below the thickness
      rule, is the same class of defect and is LEFT ALONE here;
    - `app/drawing_reading/dxf_parser.py` hardcodes the same grades on the DXF
      import path, which is an accepted authority boundary;
    - the extraction persistence gap, catalogue versioning, and any Supabase
      schema change.
"""
import ast
import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_project_extraction_intake import CAPTURE_PATH, load_capture
from tests.test_real_world_production_section_authority import (
    LIVE_310UB40_4,
    LIVE_310UB46_2,
    PINNED_ROWS,
    TEST_SERVICE_ROLE_KEY,
    TEST_SUPABASE_URL,
    _arm_cad_boundary,
    _FakeSupabaseClient,
    _member,
    _NetworkGuardClient,
    _pinned_index,
    modules,  # the real production modules, imported under the test env boundary
)

REPO = Path(__file__).resolve().parent.parent
PIPELINE_PATH = REPO / "app" / "pipeline.py"

# The exact key set one persisted steel_members row has today. Pinned so that a
# field added or removed by this milestone — or by any later one — fails here.
#
# MILESTONE J5 ADDED EXACTLY TWO KEYS, and this pin did its job: it is how the
# change was noticed rather than absorbed. `section_resolution` records HOW the
# member's section resolved (EXACT / SUFFIX_FALLBACK / NONE) and
# `section_substituted_candidate` records the catalogue section a refused suffix
# fallback offered instead. Both are NULL for a row written by a matcher that
# cannot report a resolution, and both are NULL for every row persisted before
# J5 — no value is invented for history. They are additions only: no key was
# removed, renamed or changed by J5.
#
# MILESTONE J6 ADDED EXACTLY ONE KEY, and this pin again did its job. The
# persisted row now also carries `reference_data_identity` — the source_kind /
# identity_status / digest of the catalogue rows the resolving matcher actually
# loaded, carried verbatim from that matcher and never recomputed downstream.
# It is NULL for a row written by a matcher that records no identity, and NULL
# for every row persisted before J6 — no identity is invented for history.
# Addition only: no key was removed, renamed or changed by J6.
PERSISTED_MEMBER_KEYS = frozenset({
    "project_id", "mark", "section_name", "section_name_raw", "section_family",
    "section_resolution", "section_substituted_candidate", "reference_data_identity",
    "length_mm", "grade", "quantity", "weight_per_metre", "total_weight_kg",
    "confidence", "confidence_score", "source_page", "source_drawing_id",
    "extraction_method", "review_status", "detail_reference", "notes",
})

PERSISTED_PLATE_KEYS = ("plate_type", "thickness", "width", "depth", "grade")

needs_real_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason=f"the real Arkles capture is not present at {CAPTURE_PATH.relative_to(REPO)}",
)


# ======================================================================================
# The focused rules, as pure functions over the REAL run's output.
#
# These are the checks the mutation tests re-run against a deliberately
# unfixed build. They are written from the RULE ("a persisted value is a claim
# about the drawing"), never from the implementation, so restoring the old
# behaviour has to fail them.
# ======================================================================================
def _fabricated_grades(raw_members: list[dict], persisted_rows: list[dict]) -> list[dict]:
    """Every persisted grade that NO extraction reported — a claim the drawing never made."""
    stated = {
        value
        for m in raw_members
        for value in (m.get("material"), m.get("grade"))
        if isinstance(value, str) and value.strip()
    }
    return [r for r in persisted_rows if r.get("grade") is not None and r["grade"] not in stated]


def _zero_thicknesses(persisted_plates: list[dict]) -> list[dict]:
    """Every persisted plate thickness of zero. Zero is not a structural plate thickness,
    so a persisted 0 can only have come from an absent or invalid reading."""
    return [p for p in persisted_plates if p.get("thickness") == 0]


def _hardcoded_grade_literals(source: str) -> list[str]:
    """Source-level: every '300PLUS' literal in app/pipeline.py. A fabricated grade
    default is a literal, and there is no legitimate reason for one to be here."""
    return [
        node.value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == "300PLUS"
    ]


def _truthiness_thickness_reads(source: str) -> list[int]:
    """Source-level: line numbers where `thickness_mm` is read through an `or`
    fallback — truthiness deciding whether an engineering dimension exists."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        for operand in node.values:
            if not (isinstance(operand, ast.Call) and isinstance(operand.func, ast.Attribute)):
                continue
            if operand.func.attr != "get" or not operand.args:
                continue
            first = operand.args[0]
            if isinstance(first, ast.Constant) and first.value == "thickness_mm":
                hits.append(node.lineno)
    return hits


def _unfixed_pipeline_source(source: str) -> str:
    """The pre-milestone source, reconstructed by inverting exactly this milestone's
    two edits — the 'restore the old behaviour' mutation, as text."""
    unfixed = source.replace('"grade": _extracted_grade(m),', '"grade": "300PLUS",')
    assert unfixed != source, "the grade edit anchor moved"
    unfixed = unfixed.replace(
        '"thickness": _extracted_plate_thickness(p),',
        '"thickness": p.get("thickness_mm") or 0,',
    )
    assert unfixed != source, "the thickness edit anchor moved"
    return unfixed


# ======================================================================================
# The repository double — the persistence boundary. Records exactly what production
# asked it to store, and invents nothing. Extends the section-authority file's double
# with the connection tables, which that file never reaches (it passes no connections).
# ======================================================================================
class _RecordingRepo:
    def __init__(self):
        self.calls = []
        self.inserted = []
        self.review_items = []
        self.connections = []
        self.bolt_groups = []
        self.plates = []
        self.welds = []
        self.member_links = []
        self.project_summary = None
        self.captures = None

    def _record(self, name, payload):
        self.calls.append(name)
        return payload

    def insert_members(self, rows):
        self._record("insert_members", rows)
        self.inserted = copy.deepcopy(rows)
        # The inserted rows come back as the PERSISTED rows — every column the
        # row was written with, plus the generated id — which is what the real
        # bulk insert returns. A continuation (Milestone J16) links a later
        # window's connections against the persisted rows, and J13's review
        # states are read back from them, so a double that dropped columns would
        # be testing a thinner row than production ever sees.
        return [{"id": f"member-{i}", **copy.deepcopy(r)} for i, r in enumerate(rows)]

    def insert_page_extraction_captures(self, rows):
        # Milestone J23: production now records the raw AI reading before it records
        # any row derived from it. This double records that call like every other and
        # returns the count, so a test built on it sees the genuine write order.
        self._record("insert_page_extraction_captures", rows)
        self.captures = copy.deepcopy(rows)
        return len(rows)

    def insert_review_items(self, items):
        self._record("insert_review_items", items)
        self.review_items.extend(copy.deepcopy(items))

    def insert_connection(self, row):
        self._record("insert_connection", row)
        self.connections.append(copy.deepcopy(row))
        return {"id": f"connection-{len(self.connections)}"}

    def insert_bolt_groups(self, connection_id, rows):
        self._record("insert_bolt_groups", rows)
        self.bolt_groups.extend(copy.deepcopy(rows))

    def insert_connection_plates(self, connection_id, rows):
        self._record("insert_connection_plates", rows)
        self.plates.extend(copy.deepcopy(rows))

    def insert_weld_details(self, connection_id, rows):
        self._record("insert_weld_details", rows)
        self.welds.extend(copy.deepcopy(rows))

    def link_connection_members(self, connection_id, member_ids):
        self._record("link_connection_members", member_ids)
        self.member_links.extend(copy.deepcopy(member_ids))

    def update_drawing_meta(self, *args):
        self.calls.append("update_drawing_meta")

    def update_project_summary(self, project_id, **kwargs):
        self._record("update_project_summary", kwargs)
        self.project_summary = dict(kwargs)


class _ProductionRun:
    """Everything one real production run persisted, for a test to assert on."""

    def __init__(self, summary, repo):
        self.summary = summary
        self.repo = repo

    @property
    def members(self):
        return self.repo.inserted

    @property
    def plates(self):
        return self.repo.plates

    def member_for(self, mark):
        rows = [r for r in self.members if r["mark"] == mark]
        assert len(rows) == 1, f"expected exactly one persisted member for mark {mark!r}, got {len(rows)}"
        return rows[0]

    @property
    def grades(self):
        return sorted({r["grade"] for r in self.members}, key=lambda g: (g is not None, g))


@pytest.fixture()
def run_production(modules, monkeypatch):
    """
    run(raw_members, raw_connections=()) -> _ProductionRun, driving the REAL
    app/pipeline.py `_run_pipeline`. The vision stage is the only replaced
    stage, and it is replaced with the real PageExtraction dataclass.
    """
    armed = {"cad": False}

    def run(raw_members, raw_connections=(), index_rows=None):
        double = _FakeSupabaseClient(index_rows if index_rows is not None else _pinned_index())
        monkeypatch.setattr(modules.section_matcher, "supabase", double)

        if not armed["cad"]:
            _arm_cad_boundary(monkeypatch)
            armed["cad"] = True

        page = modules.PageExtraction(
            page_number=1, drawing_number="S101", drawing_title="Framing Plan",
            revision="A", raw_members=list(raw_members), raw_connections=list(raw_connections),
        )
        monkeypatch.setattr(modules.pipeline, "analyze_pdf_pages", lambda *a, **k: [page])
        # Milestone J15: the simulated document has exactly the one page above,
        # so its coverage is complete and this file keeps testing what it is
        # named for rather than a coverage rule.
        monkeypatch.setattr(modules.pipeline, "page_count_of", lambda *a, **k: 1)

        repo = _RecordingRepo()
        monkeypatch.setattr(modules.pipeline, "repo", repo)

        summary = modules.pipeline._run_pipeline(
            "unused.pdf", "project-1", "user-1", "drawing-set-1", "drawing-1",
            analysis_run_id="run-1",
        )
        return _ProductionRun(summary, repo)

    return run


def _plate(**overrides) -> dict:
    plate = {"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}
    plate.update(overrides)
    return plate


def _connection(section_marks=("B1",), **overrides) -> dict:
    conn = {
        "detail_reference": "D15", "grid_reference": "A-B/2", "connects_members": list(section_marks),
        "connection_type": "bolted", "confidence": 90,
    }
    conn.update(overrides)
    return conn


# ======================================================================================
# The capture, verbatim.
# ======================================================================================
@pytest.fixture(scope="module")
def arkles_capture():
    return load_capture(CAPTURE_PATH)


# ======================================================================================
# THE SOURCE RULES — no fabricated grade, no truthiness on a dimension.
# ======================================================================================
class TestTheProductionSourceStatesNoFabricatedValue:

    def test_no_grade_default_literal_survives_in_the_pipeline(self):
        assert _hardcoded_grade_literals(PIPELINE_PATH.read_text()) == []

    def test_no_dimension_is_read_through_truthiness(self):
        assert _truthiness_thickness_reads(PIPELINE_PATH.read_text()) == []

    def test_the_source_rules_detect_the_old_behaviour(self):
        """The controls: the checks above are only meaningful if they fire on the
        source this milestone replaced."""
        unfixed = _unfixed_pipeline_source(PIPELINE_PATH.read_text())
        assert _hardcoded_grade_literals(unfixed) == ["300PLUS"]
        assert _truthiness_thickness_reads(unfixed) != []


# ======================================================================================
# GRADE
# ======================================================================================
class TestGradeEvidence:

    def test_a_stated_grade_survives_verbatim(self, run_production):
        run = run_production([{**_member("310UB46.2"), "material": "S355"}])
        assert run.member_for("B1")["grade"] == "S355"

    def test_a_stated_grade_is_never_normalised(self, run_production):
        """The extraction preserves the page's own wording; so does persistence."""
        for stated in ("AS/NZS 3678-300", "300 PLUS", "grade 300", "S355JR"):
            run = run_production([{**_member("310UB46.2"), "material": stated}])
            assert run.member_for("B1")["grade"] == stated

    def test_a_member_with_no_stated_grade_is_persisted_as_unknown(self, run_production):
        run = run_production([_member("310UB46.2")])
        assert run.member_for("B1")["grade"] is None

    @pytest.mark.parametrize("absent", [
        {},                       # no material evidence at all
        {"material": None},       # reported null
        {"material": ""},         # reported blank
        {"material": "   "},      # reported whitespace
        {"material": 300},        # not a designation the drawing stated
    ])
    def test_absent_grade_evidence_is_never_filled_in(self, run_production, absent):
        run = run_production([{**_member("310UB46.2"), **absent}])
        assert run.member_for("B1")["grade"] is None

    def test_the_real_capture_states_no_member_grade_anywhere(self, arkles_capture):
        """The premise, asserted against the capture rather than assumed."""
        keys = {key for page in arkles_capture for m in page["raw_members"] for key in m}
        assert not keys & {"material", "grade"}
        member_rows = [m for page in arkles_capture for m in page["raw_members"]]
        assert len(member_rows) == 105

    @needs_real_capture
    def test_no_real_member_gains_a_grade_the_drawing_never_stated(self, run_production, arkles_capture):
        raw = [m for page in arkles_capture for m in page["raw_members"]]
        run = run_production(raw)
        assert run.members, "the capture must persist rows for this proof to mean anything"
        assert run.grades == [None]
        assert _fabricated_grades(raw, run.members) == []
        assert "300PLUS" not in json.dumps(run.members)

    def test_grade_is_not_inferred_from_the_section_family(self, run_production):
        """310UB46.2 resolves EXACTLY and its catalogue row carries family "UB" —
        the family is real enrichment; the grade is not derived from it."""
        run = run_production([{**_member("310UB46.2"), "section_family": "UB"}])
        row = run.member_for("B1")
        assert row["section_family"] == "UB"
        assert row["grade"] is None

    def test_grade_is_not_inferred_from_the_catalogue_row(self, run_production):
        """A matched catalogue row enriches weight/family ONLY. Asserted on the
        row's own columns, so a future catalogue column cannot quietly become a
        grade source without failing here."""
        assert not any(
            "grade" in key.lower() or "material" in key.lower() for key in PINNED_ROWS[0]
        ), "the reference row now carries grade-like data; this rule must be re-argued"
        run = run_production([_member("310UB46.2")])
        row = run.member_for("B1")
        assert row["weight_per_metre"] == LIVE_310UB46_2["weight_per_metre"]
        assert row["section_family"] == LIVE_310UB46_2["family"]
        assert row["grade"] is None

    def test_grade_is_not_inferred_from_the_connection_material(self, run_production):
        """A page that states a material for a CONNECTION must not stamp it onto
        the members it joins — the extraction reports it for the connection, and
        the association is not the member's own evidence."""
        run = run_production(
            [_member("310UB46.2", mark="B1"), _member("250PFC", mark="C1")],
            [_connection(("B1", "C1"), material="300")],
        )
        assert run.repo.connections[0] is not None       # the connection material is real evidence…
        for mark in ("B1", "C1"):
            assert run.member_for(mark)["grade"] is None  # …but it is not the member's grade

    def test_no_project_or_material_default_exists_to_be_used(self, run_production):
        """There is no default to fall back to: a run with no material evidence
        anywhere persists no grade anywhere."""
        run = run_production(
            [_member("310UB46.2", mark="B1"), _member("250PFC", mark="C1"), _member("310UB40", mark="B2")],
            [_connection(("B1", "C1"))],
        )
        assert run.grades == [None]


# ======================================================================================
# THICKNESS
# ======================================================================================
class TestPlateThicknessEvidence:

    def test_an_explicit_positive_thickness_survives(self, run_production):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate()])])
        assert [p["thickness"] for p in run.plates] == [12]

    @pytest.mark.parametrize("value", [1, 6.0, 12, 22.5, 0.5])
    def test_every_positive_thickness_is_preserved_exactly(self, run_production, value):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate(thickness_mm=value)])])
        assert run.plates[0]["thickness"] == value

    @pytest.mark.parametrize("absent", [
        {"thickness_mm": None},
        {},
    ])
    def test_missing_thickness_stays_unknown(self, run_production, absent):
        plate = {k: v for k, v in _plate().items() if k != "thickness_mm"}
        plate.update(absent)
        run = run_production([_member("310UB46.2")], [_connection(plates=[plate])])
        assert run.plates[0]["thickness"] is None
        assert _zero_thicknesses(run.plates) == []

    @pytest.mark.parametrize("invalid", [
        0, 0.0, -1, -12.5, "12", "twelve", True, False, [], {}, float("nan"),
        float("inf"), float("-inf"), None,
    ])
    def test_invalid_thickness_fails_closed_rather_than_becoming_a_value(self, run_production, invalid):
        """Zero, negatives and non-numbers are not plate thicknesses. Each is
        refused as a value; none is silently converted into 0 or a string."""
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate(thickness_mm=invalid)])])
        assert run.plates[0]["thickness"] is None
        assert _zero_thicknesses(run.plates) == []

    def test_zero_is_never_persisted_as_a_structural_thickness(self, run_production):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate(thickness_mm=0)])])
        assert run.plates[0]["thickness"] != 0
        assert run.plates[0]["thickness"] is None

    def test_an_invalid_plate_keeps_the_fields_the_drawing_did_state(self, run_production):
        """Fail-closed is not delete: the plate is evidenced by its type and
        dimensions, so the row is kept and only the unreadable value withheld."""
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[_plate(thickness_mm=0, width_mm=180, depth_mm=250)])],
        )
        plate = run.plates[0]
        assert plate["plate_type"] == "end_plate"
        assert plate["width"] == 180 and plate["depth"] == 250
        assert tuple(plate) == PERSISTED_PLATE_KEYS

    def test_no_run_persists_a_zero_thickness_anywhere(self, run_production, arkles_capture):
        real_members = [m for page in arkles_capture for m in page["raw_members"]][:1] if arkles_capture else []
        run = run_production(
            real_members or [_member("310UB46.2")],
            [_connection(plates=[_plate(thickness_mm=0)]),
             _connection(plates=[{"type": "base_plate", "width_mm": 200, "depth_mm": 200}])],
        )
        assert _zero_thicknesses(run.plates) == []
        assert [p["thickness"] for p in run.plates] == [None, None]


# ======================================================================================
# AUTHORITY — the catalogue may enrich, never manufacture.
# ======================================================================================
class TestTheAuthorityBoundaryIsUnchanged:

    def test_an_exact_match_does_not_manufacture_a_grade(self, run_production):
        run = run_production([_member("310UB46.2")])
        row = run.member_for("B1")
        assert row["section_name"] == LIVE_310UB46_2["name"]      # the catalogue answered for it
        assert row["weight_per_metre"] == LIVE_310UB46_2["weight_per_metre"]
        assert row["grade"] is None                                # …and still no grade

    def test_an_exact_match_does_not_manufacture_a_thickness(self, run_production):
        run = run_production(
            [_member("310UB46.2")], [_connection(plates=[{"type": "end_plate"}])],
        )
        assert run.plates[0]["thickness"] is None

    def test_the_exact_match_still_keeps_the_catalogues_own_spelling(self, run_production):
        run = run_production([_member("310UB46.2")])
        assert run.member_for("B1")["section_name"] == "310UB46.2"
        assert run.member_for("B1")["section_name_raw"] == "310UB46.2"

    def test_a_refused_suffix_fallback_still_enriches_nothing(self, run_production):
        """310UB40 is not in the catalogue; the offered row is 310UB40.4, a
        DIFFERENT section. It is refused as identity and as properties alike —
        including the grade, which is absent, not the refused row's."""
        run = run_production([_member("310UB40")])
        row = run.member_for("B1")
        # The member keeps the section the DRAWING states — in
        # section_name_raw, the FK-free column — and the refused row's name is
        # never adopted, as identity or as properties.
        #
        # CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C): before J7 the
        # drawn token was written to section_name. That column is FK-bound to
        # steel_sections(name), and "310UB40" is not a key of it; only an EXACT
        # resolution may populate it. The refusal this test asserts is
        # unchanged — the offered row's name is still not the member's.
        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["section_name"] != LIVE_310UB40_4["name"]
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["grade"] is None
        assert row["review_status"] == "review_required"

    def test_an_unmatched_section_is_unchanged(self, run_production):
        run = run_production([_member("NOT-A-SECTION")])
        row = run.member_for("B1")
        assert row["section_name"] is None and row["grade"] is None
        assert row["review_status"] == "review_required"

    def test_a_matched_member_and_a_refused_one_are_different_in_the_same_run(self, run_production):
        run = run_production([_member("310UB46.2", mark="OK"), _member("310UB40", mark="NO")])
        assert run.member_for("OK")["weight_per_metre"] == LIVE_310UB46_2["weight_per_metre"]
        assert run.member_for("NO")["weight_per_metre"] is None
        assert run.grades == [None]


# ======================================================================================
# REGRESSION — the production path, and the shape it persists.
# ======================================================================================
class TestTheProductionPathIsUnchangedApartFromTheEvidence:

    def test_the_real_pipeline_path_is_the_one_under_test(self, run_production):
        run = run_production([_member("310UB46.2")])
        assert "insert_members" in run.repo.calls
        assert "update_project_summary" in run.repo.calls
        assert run.summary["members_extracted"] == 1

    def test_the_persisted_member_row_has_exactly_the_same_keys(self, run_production):
        run = run_production([_member("310UB46.2")])
        assert set(run.member_for("B1")) == PERSISTED_MEMBER_KEYS

    def test_no_unrelated_member_field_changed(self, run_production):
        run = run_production([{**_member("310UB46.2", length_mm=6000), "grid_reference": "A-B/2"}])
        row = run.member_for("B1")
        assert row["project_id"] == "project-1"
        assert row["mark"] == "B1"
        assert row["section_name"] == "310UB46.2"
        assert row["section_name_raw"] == "310UB46.2"
        assert row["section_family"] == "UB"
        assert row["length_mm"] == 6000
        assert row["quantity"] == 1
        assert row["weight_per_metre"] == LIVE_310UB46_2["weight_per_metre"]
        assert row["total_weight_kg"] == round((6000 / 1000) * 46.2, 2)
        assert row["confidence"] == "high"
        assert row["confidence_score"] == 95
        assert row["source_page"] == 1
        assert row["source_drawing_id"] == "drawing-1"
        assert row["extraction_method"] == "vision_claude"
        assert row["review_status"] == "extracted"
        assert row["notes"] == "Grid: A-B/2"
        # Milestone J5's two additions, asserted rather than merely tolerated by
        # the key-set pin above. 310UB46.2 is a live catalogue key, so this is an
        # exact resolution and nothing was refused.
        assert row["section_resolution"] == "EXACT"
        assert row["section_substituted_candidate"] is None
        # Milestone J6's one addition, asserted rather than merely tolerated by
        # the key-set pin above. This run's matcher was supplied a pinned index
        # rather than the live table, so the recorded identity is that index's
        # own three values — the point being that the key is present and shaped
        # as the accepted projection, not what the live digest happens to be.
        identity = row["reference_data_identity"]
        assert sorted(identity) == ["identity_status", "reference_data_digest", "source_kind"]
        assert identity["source_kind"] == "LIVE_SUPABASE"
        assert identity["identity_status"] == "UNVERSIONED"
        assert isinstance(identity["reference_data_digest"], str)
        assert len(identity["reference_data_digest"]) == 64

    def test_the_persisted_plate_row_has_exactly_the_same_keys(self, run_production):
        run = run_production([_member("310UB46.2")], [_connection(plates=[_plate()])])
        assert tuple(run.plates[0]) == PERSISTED_PLATE_KEYS

    def test_the_connection_path_is_still_driven(self, run_production):
        run = run_production(
            [_member("310UB46.2", mark="B1")],
            [_connection(("B1",), bolts=[{"quantity": 4, "size": "M20", "grade": "8.8"}],
                         welds=[{"type": "fillet", "size_mm": 8}])],
        )
        assert run.repo.connections[0]["connection_type"] == "bolted"
        assert [b["bolt_size"] for b in run.repo.bolt_groups] == ["M20"]
        assert [w["size"] for w in run.repo.welds] == [8]
        assert run.repo.member_links == ["member-0"]

    @needs_real_capture
    def test_the_whole_real_capture_persists_no_fabricated_value(self, run_production, arkles_capture):
        raw_members = [m for page in arkles_capture for m in page["raw_members"]]
        raw_connections = [c for page in arkles_capture for c in page["raw_connections"]]
        run = run_production(raw_members, raw_connections)
        assert _fabricated_grades(raw_members, run.members) == []
        assert _zero_thicknesses(run.plates) == []
        assert run.grades == [None]

    @needs_real_capture
    def test_a_capture_that_does_state_a_grade_still_persists_it(self, run_production, arkles_capture):
        """The rule is not 'always null'. A real captured steel member row — the
        same row the fabricated default was applied to — keeps a designation the
        drawing states, in the drawing's own wording."""
        real = next(
            m for page in arkles_capture for m in page["raw_members"]
            if m.get("section") == "89x5 SHS" and m.get("mark")
        )
        raw = [{**real, "material": "AS/NZS 3678-300"}]
        run = run_production(raw)
        assert run.members[0]["grade"] == "AS/NZS 3678-300"
        # …and that same row, with no material stated, is persisted as unknown.
        without = run_production([copy.deepcopy(real)])
        assert without.members[0]["grade"] is None


# ======================================================================================
# MUTATIONS — the checks above must fail on the behaviour they replaced.
# Throwaway copies only; the working tree is proved untouched.
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

    def test_m1_restoring_the_hardcoded_grade_is_caught(
            self, run_production, modules, monkeypatch):
        """M1 — the old behaviour, restored in a throwaway copy of the SOURCE and
        in a throwaway patch of the running module."""
        source = PIPELINE_PATH.read_text()
        unfixed = _unfixed_pipeline_source(source)
        assert '"grade": "300PLUS",' in unfixed
        assert _hardcoded_grade_literals(unfixed) == ["300PLUS"]

        monkeypatch.setattr(modules.pipeline, "_extracted_grade", lambda member: "300PLUS")
        raw = [_member("310UB46.2")]
        run = run_production(raw)
        assert run.member_for("B1")["grade"] == "300PLUS"
        assert _fabricated_grades(raw, run.members) == run.members, (
            "the focused rule failed to notice the restored hardcoded grade"
        )

    def test_m2_restoring_the_truthiness_thickness_fallback_is_caught(
            self, run_production, modules, monkeypatch):
        """M2 — `p.get("thickness_mm") or 0`, restored."""
        source = PIPELINE_PATH.read_text()
        unfixed = _unfixed_pipeline_source(source)
        assert 'p.get("thickness_mm") or 0' in unfixed
        assert _truthiness_thickness_reads(unfixed) != []

        monkeypatch.setattr(
            modules.pipeline, "_extracted_plate_thickness",
            lambda plate: plate.get("thickness_mm") or 0,
        )
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[{"type": "end_plate", "width_mm": 180, "depth_mm": 250}])],
        )
        assert run.plates[0]["thickness"] == 0
        assert _zero_thicknesses(run.plates) == run.plates, (
            "the focused rule failed to notice the restored truthiness fallback"
        )

    def test_m3_removing_the_missing_grade_protection_is_caught(
            self, run_production, modules, monkeypatch):
        """M3 — a project/material default instead of absence."""
        monkeypatch.setattr(
            modules.pipeline, "_extracted_grade",
            lambda member: member.get("material") or "300PLUS",
        )
        raw = [_member("310UB46.2"), {**_member("250PFC", mark="C1"), "material": None}]
        run = run_production(raw)
        assert _fabricated_grades(raw, run.members) == run.members

    def test_m4_missing_thickness_forced_to_zero_downstream_is_caught(
            self, run_production, modules, monkeypatch):
        """M4 — the collapse reintroduced one stage later, at the row."""
        real = modules.pipeline._extracted_plate_thickness

        def collapsing(plate):
            return real(plate) or 0

        monkeypatch.setattr(modules.pipeline, "_extracted_plate_thickness", collapsing)
        run = run_production(
            [_member("310UB46.2")],
            [_connection(plates=[{"type": "end_plate"}, _plate(thickness_mm=None)])],
        )
        assert [p["thickness"] for p in run.plates] == [0, 0]
        assert _zero_thicknesses(run.plates) == run.plates

    def test_the_unmutated_build_passes_every_focused_rule(self, run_production):
        """The control for all four mutations, on the same run shape."""
        raw = [_member("310UB46.2")]
        run = run_production(
            raw, [_connection(plates=[{"type": "end_plate"}, _plate()])],
        )
        assert _fabricated_grades(raw, run.members) == []
        assert _zero_thicknesses(run.plates) == []
        assert _hardcoded_grade_literals(PIPELINE_PATH.read_text()) == []
        assert _truthiness_thickness_reads(PIPELINE_PATH.read_text()) == []
