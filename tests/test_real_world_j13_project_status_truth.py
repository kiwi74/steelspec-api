"""
J13 — PROJECT STATUS TRUTH.

Before this milestone both successful extraction paths wrote the constant
"review" into `projects.status`: app/drawing_reading/dxf_parser.py and
app/pipeline.py each hardcoded it. Nothing anywhere wrote any other value, so
every project — including one whose every member and connection was explicitly
resolved — was parked in a state that cannot be told apart from "a human still
has to look at this". The lifecycle had no exit.

J13 replaces the constant with a derivation over evidence the extraction
persisted, and the rule it implements is deliberately strict:

    "all required evidence is explicitly resolved"  ==  done
    anything else                                   ==  review

NOT "no obvious problems" == done. Absence is not completeness: a review state
the row does not state, a state this repository's vocabulary does not contain, an
unmatched section, a J11 discard, or a drawing that yielded nothing at all each
leave the project at "review".

WHAT THIS FILE DOES NOT DO
--------------------------
It does not implement reviewer resolution, does not touch the /review mount or
app/cad_engine/, does not change what either extractor extracts, and does not
make the DXF path qualify for "done" — that path states no member review state,
so it fail-closes to "review", and this file pins that as the correct outcome
rather than a defect.

THE BOUNDARY
------------
The production modules are imported under a deliberately fake, JWT-shaped
configuration and every module-level Supabase client is replaced by an
in-process double, so no test here can reach the network even by mistake (the
guard raises rather than connecting). The pure derivation needs no environment at
all. No live catalogue is read and no production data is written.

The 11 pre-existing tests/test_smoke_imports.py failures (app/config.py reads its
environment at import time) MUST keep failing honestly, so every module this file
imports is removed from sys.modules again at teardown.
"""
import ast
import builtins
import copy
import dataclasses
import os
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)
ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")

# The pinned catalogue rows the matchers are given — the same three live rows the
# J11 and DXF-boundary suites pin, kept here so this file is self-contained.
PINNED_ROWS = (
    {"name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
     "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
     "leg_size": None, "thickness": None, "width": None, "outside_diameter": None},
    {"name": "310UB46.2", "family": "UB", "depth": 307.0, "flange_width": 166.0,
     "flange_thickness": 11.8, "web_thickness": 6.7, "weight_per_metre": 46.2,
     "leg_size": None, "thickness": None, "width": None, "outside_diameter": None},
    {"name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
     "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
     "leg_size": None, "thickness": None, "width": None, "outside_diameter": None},
)

EXACT_TOKEN = "310UB46.2"       # a real catalogue key -> EXACT
UNMATCHED_TOKEN = "360UB50"     # a real-looking token the pinned catalogue lacks -> NONE
GEOMETRY_LAYER = "S-BEAM"
TEXT_LAYER = "S-TEXT"
CONNECTION_LAYER = "S-CONN"
LINE_LENGTH = 3000.0
EXACT_MEMBER_WEIGHT_KG = 138.6

DONE = "done"
REVIEW = "review"

# The repository's extraction vocabulary, as discovered and reported by J13.
ACCEPTED_STATUS = "extracted"
BLOCKING_STATUS = "review_required"

# The project columns each path writes. J13 adds NO column to either payload —
# only the VALUE of `status` changes.
DXF_PROJECT_PAYLOAD_KEYS = {
    "status", "total_members", "total_unique_sections", "total_connections",
    "total_weight_kg", "total_weight_tonnes", "unmatched_sections", "warnings",
}
PDF_PROJECT_PAYLOAD_KEYS = DXF_PROJECT_PAYLOAD_KEYS | {
    "engineer_reference", "structural_engineer",
}
PDF_MEMBER_ROW_KEYS = {
    "project_id", "mark", "section_name", "section_name_raw", "section_family",
    "section_resolution", "section_substituted_candidate", "reference_data_identity",
    "length_mm", "grade", "quantity", "weight_per_metre", "total_weight_kg",
    "confidence", "confidence_score", "source_page", "source_drawing_id",
    "extraction_method", "review_status", "detail_reference", "notes",
}

MODULE_PATH = REPO / "app" / "validation" / "project_status.py"
# The migrations that exist. J13 authored the first three of them and no more; the fourth
# arrived with J22 and touches neither `projects.status` nor anything J13 derives; the fifth
# arrived with J23 and is not this milestone's either — a raw AI reading is explicitly NOT
# evidence, so it is not a table `projects.status` is derived from and the derivation is
# untouched by it. The sixth arrived with J28 and is not this milestone's either: a PDF
# annotation occurrence is a reading of the drawing, and `projects.status` is derived from
# what was persisted as engineering evidence, which this table is not and is read by nothing.
# The eighth arrived with J61, and it is not this milestone's either: it creates one row per
# SOURCE DOCUMENT a project has and adds one nullable pointer from `drawings` to it. Nothing
# in it reads or writes `projects.status`, its derivation, or any input to it.
# The list is pinned so that a migration appearing here unremarked fails this file rather than
# passing quietly — which is exactly what happened when J22 added one, again when J23 did, and
# again when J28 did, and again when J44 did, and again when J61 did, and again when J66 did.
# J28A is the bookkeeping step that recorded the J28 name here; J44, J61 and J66 each recorded
# their own.
MIGRATIONS = (
    "20260924000000_j5_section_resolution_truth.sql",
    "20260924010000_j6_reference_data_identity.sql",
    "20260924020000_j8b_connection_plate_evidence_nullability.sql",
    "20260925000000_j22_connection_review_persistence.sql",
    "20260925010000_j23_page_extraction_captures.sql",
    "20260927000000_j28_pdf_annotation_occurrences.sql",
    "20260928000000_j44_project_review_claims.sql",
    "20260929000000_j61_project_documents.sql",
    "20260929010000_j66_field_evidence_citations.sql",
    "20261006000000_l19_selected_extraction_lineage.sql",
)


# ===========================================================================
# The repository-boundary doubles. No test here touches the network.
# ===========================================================================
class _FakeExecuteResult:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Answers the matcher's one catalogue read, in-process."""

    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name

    def select(self, columns="*"):
        self._client.queries.append((self._table, columns))
        return self

    def execute(self):
        self._client.executed += 1
        # A detached copy, mirroring the real client: what the matcher indexes
        # can never be mutated through this double, and the fixtures stay clean.
        return _FakeExecuteResult(copy.deepcopy(self._client.rows))


class _FakeSupabaseClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _RecordingQuery:
    """The parser's own insert/update calls, recorded and answered in-process."""

    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name
        self._op = None
        self._payload = None
        self._filters = []

    def insert(self, payload):
        self._op = "insert"
        self._payload = copy.deepcopy(payload)
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = copy.deepcopy(payload)
        return self

    def select(self, columns="*"):
        self._op = "select"
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def execute(self):
        self._client.calls.append({
            "table": self._table, "op": self._op,
            "payload": self._payload, "filters": list(self._filters),
        })
        if self._op == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            self._client.inserted.setdefault(self._table, []).extend(copy.deepcopy(rows))
            return _FakeExecuteResult([
                {**row, "id": f"{self._table}-{index}"} for index, row in enumerate(rows)
            ])
        return _FakeExecuteResult([])


class _RecordingSupabaseClient:
    def __init__(self):
        self.calls = []
        self.inserted = {}

    def table(self, name):
        return _RecordingQuery(self, name)

    def rows_inserted_into(self, table):
        return self.inserted.get(table, [])


class _RecordingRepository:
    """The production repository API answered in-process and recorded.

    Every function app/pipeline.py calls is present under the same name with the
    same shape, so the production orchestrator runs unchanged and nothing it
    does can reach a client.
    """

    def __init__(self):
        self.calls = []
        self.project_summary = None
        self.inserted_members = []
        self.inserted_connections = []
        self.captures = []
        # Milestone J61: the source documents recorded, and the document each drawing write
        # named. Neither is an input to the status this file is about.
        self.documents = []
        self.documents_by_content = {}
        self.document_links = []

    def _record(self, name):
        self.calls.append(name)

    def create_drawing_set(self, project_id, name):
        self._record("create_drawing_set")
        return {"id": "drawing-set-1"}

    def create_drawing(self, drawing_set_id, file_name, storage_path, document_id=None):
        # Milestone J61: the drawing is created pointing at the source document the run read.
        # The argument is accepted and recorded because the production call carries it.
        self._record("create_drawing")
        self.document_links.append(document_id)
        return {"id": "drawing-1", "document_id": document_id}

    def create_project_document(self, project_id, *, storage_path, file_name,
                                source_format=None, byte_size=None, page_count=None,
                                content_sha256=None, role="UNKNOWN", revision_label=None,
                                supersedes_document_id=None):
        # Milestone J61. This file is about the status J13 derives from persisted EVIDENCE,
        # and a source document's identity is not evidence: the row is recorded here and is
        # read by nothing in the derivation. The contract is the production one — a proven
        # hash is the identity, so the same bytes are the same document.
        self._record("create_project_document")
        existing = self.documents_by_content.get((project_id, content_sha256))
        if content_sha256 is not None and existing is not None:
            return dict(existing)
        row = {
            "id": f"document-{len(self.documents) + 1}",
            "project_id": project_id,
            "storage_path": storage_path,
            "file_name": file_name,
            "source_format": source_format,
            "byte_size": byte_size,
            "page_count": page_count,
            "content_sha256": content_sha256,
            "role": role,
            "revision_label": revision_label,
            "supersedes_document_id": supersedes_document_id,
        }
        self.documents.append(row)
        if content_sha256 is not None:
            self.documents_by_content[(project_id, content_sha256)] = row
        return dict(row)

    def update_document_page_count(self, document_id, page_count):
        self._record("update_document_page_count")

    def create_analysis_run(self, drawing_set_id, model_used):
        self._record("create_analysis_run")
        return {"id": "analysis-run-1"}

    def update_analysis_run(self, analysis_run_id, **fields):
        self._record("update_analysis_run")

    def update_drawing_set(self, drawing_set_id, **fields):
        self._record("update_drawing_set")

    def update_drawing_meta(self, drawing_id, page_count, drawing_number,
                            drawing_title, revision):
        self._record("update_drawing_meta")

    def insert_page_extraction_captures(self, rows):
        # Milestone J23: the raw AI reading is recorded before any row derived from it.
        # This file is about the status that is derived from persisted evidence, and a
        # capture is deliberately NOT evidence — so it is recorded and never counted.
        self._record("insert_page_extraction_captures")
        self.captures.extend(copy.deepcopy(rows or []))
        return len(rows or [])

    def insert_members(self, rows):
        self._record("insert_members")
        inserted = [{**row, "id": f"member-{index}"} for index, row in enumerate(rows)]
        self.inserted_members.extend(copy.deepcopy(inserted))
        return inserted

    def insert_review_items(self, rows):
        self._record("insert_review_items")

    def insert_connection(self, row):
        self._record("insert_connection")
        inserted = {**row, "id": f"connection-{len(self.inserted_connections)}"}
        self.inserted_connections.append(copy.deepcopy(inserted))
        return inserted

    def insert_bolt_groups(self, connection_id, bolts):
        self._record("insert_bolt_groups")

    def insert_connection_plates(self, connection_id, plates):
        self._record("insert_connection_plates")

    def insert_weld_details(self, connection_id, welds):
        self._record("insert_weld_details")

    def link_connection_members(self, connection_id, member_ids):
        self._record("link_connection_members")

    def update_project_summary(self, project_id, **fields):
        self._record("update_project_summary")
        self.project_summary = {"project_id": project_id, **copy.deepcopy(fields)}


class _NetworkGuardClient:
    """Installed on each production module so an UNPATCHED dependency fails
    loudly here instead of reaching the network. It makes "no network"
    structural rather than merely intended."""

    def __init__(self):
        self.touched = []

    def table(self, name):  # pragma: no cover - only reached on a test bug
        self.touched.append(name)
        raise AssertionError(
            f"no test in this file may touch a live Supabase client (asked for table "
            f"{name!r}); build the dependency through the fixtures so the repository "
            "boundary is replaced first."
        )


# ===========================================================================
# Fixtures
# ===========================================================================
@pytest.fixture(scope="module")
def modules():
    """The REAL production modules, imported once under the test-only
    configuration boundary, with each module's client global guarded.

    app/config.py reads os.environ[...] at import time, so the placeholder
    environment must exist for the import to happen at all; it is restored
    immediately and every module this file adds to sys.modules is removed at
    teardown, so the pre-existing SUPABASE_URL smoke-import baseline is
    unchanged.
    """
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        import app.validation.project_status as status_module
        import app.validation.dxf_drop_accounting as drop_module
        import app.validation.page_coverage as coverage_module
        import app.drawing_reading.dxf_parser as dxf_module
        import app.engineering_data.section_matcher as matcher_module
        import app.ai_analysis.pdf_vision_analyzer as vision_module
        import app.pipeline as pipeline_module
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    guards = {"dxf": _NetworkGuardClient(), "matcher": _NetworkGuardClient()}
    real_clients = {"dxf": dxf_module.supabase, "matcher": matcher_module.supabase}
    dxf_module.supabase = guards["dxf"]
    matcher_module.supabase = guards["matcher"]

    namespace = types.SimpleNamespace(
        status=status_module, drop=drop_module, dxf=dxf_module,
        matcher=matcher_module, vision=vision_module, pipeline=pipeline_module,
        coverage=coverage_module,
        guards=guards,
    )
    try:
        yield namespace
    finally:
        dxf_module.supabase = real_clients["dxf"]
        matcher_module.supabase = real_clients["matcher"]
        # Only app-prefixed modules are removed: the C extensions this suite
        # imports (cadquery/numpy/ezdxf) must survive for the rest of the run.
        for name in set(sys.modules) - before:
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


@pytest.fixture()
def decide(modules):
    """decide(**evidence) -> the status string, for the truth-table tests.

    DELIBERATELY UPDATED BY MILESTONE J15: the derivation now also needs the
    drawing set's coverage, so this fixture states a COMPLETE one. These tests
    are about what the member and connection evidence alone supports, and they
    keep asking exactly that; coverage is a separate axis, pinned — including
    its fail-closed default, where no coverage record at all is stated — in
    tests/test_real_world_j15_pdf_coverage_truth.py. A caller may pass
    `coverage=` to override it.
    """
    complete = modules.coverage.coverage_of(total_pages=1, page_numbers=[1])

    def call(**evidence):
        evidence.setdefault("coverage", complete)
        return modules.status.derive_project_status(**evidence).status

    return call


@pytest.fixture()
def decide_with_blockers(modules):
    """The full decision, so a test can also read WHY the project is held."""

    complete = modules.coverage.coverage_of(total_pages=1, page_numbers=[1])

    def call(**evidence):
        evidence.setdefault("coverage", complete)
        return modules.status.derive_project_status(**evidence)

    return call


@pytest.fixture()
def build_real_matcher(modules, monkeypatch):
    def build(rows):
        monkeypatch.setattr(modules.matcher, "supabase", _FakeSupabaseClient(rows))
        return modules.matcher.SectionMatcher()

    return build


@pytest.fixture()
def real_matcher(build_real_matcher):
    """The production matcher over the pinned real rows."""
    return build_real_matcher(copy.deepcopy(list(PINNED_ROWS)))


@pytest.fixture()
def run_dxf(tmp_path, modules, monkeypatch, real_matcher):
    """Runs the GENUINE production reader over a genuinely written DXF, with the
    reader's own client replaced by a recording, in-process double."""
    double = _RecordingSupabaseClient()
    monkeypatch.setattr(modules.dxf, "supabase", double)
    counter = {"n": 0}

    def run(write, *, project_id="j13-project"):
        counter["n"] += 1
        path = write(tmp_path / f"j13-{counter['n']}.dxf")
        summary = modules.dxf.parse_dxf_and_save(str(path), project_id, matcher=real_matcher)
        updates = [call for call in double.calls
                   if call["table"] == "projects" and call["op"] == "update"]
        assert len(updates) == 1, f"expected exactly one project update, got {len(updates)}"
        assert updates[0]["filters"] == [("id", project_id)]
        return types.SimpleNamespace(
            summary=summary, payload=updates[0]["payload"], recorder=double,
            members=double.rows_inserted_into("steel_members"),
            connections=double.rows_inserted_into("connections"),
        )

    return run


@pytest.fixture()
def run_pdf(tmp_path, modules, monkeypatch):
    """Runs the GENUINE production PDF orchestrator over supplied page
    extractions, with the repository and the vision stage replaced in-process."""

    def run(pages, *, project_id="j13-pdf-project", document_pages=None):
        repository = _RecordingRepository()
        monkeypatch.setattr(modules.pipeline, "repo", repository)
        monkeypatch.setattr(modules.pipeline, "analyze_pdf_pages", lambda *a, **k: list(pages))
        # Milestone J15: the simulated document is one whose every page is among
        # the extractions supplied — so this fixture still exercises what it was
        # written to exercise, and a truncated or partly unreadable drawing set
        # is expressed by passing `document_pages` explicitly.
        monkeypatch.setattr(
            modules.pipeline, "page_count_of",
            lambda *a, **k: len(pages) if document_pages is None else document_pages,
        )
        monkeypatch.setattr(modules.matcher, "supabase",
                            _FakeSupabaseClient(copy.deepcopy(list(PINNED_ROWS))))
        source = tmp_path / "j13.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        result = modules.pipeline.parse_pdf_and_save(
            str(source), project_id, "j13-user", "j13/j13.pdf"
        )
        return types.SimpleNamespace(
            result=result, repository=repository, summary=repository.project_summary,
            members=repository.inserted_members,
            connections=repository.inserted_connections,
        )

    return run


def _page(modules, number, members, *, connections=()):
    """One genuine production PageExtraction."""
    return modules.vision.PageExtraction(
        page_number=number,
        drawing_number="J13-DWG-001",
        drawing_title="J13 status truth",
        revision="A",
        raw_members=list(members),
        raw_connections=list(connections),
    )


def _member(mark, section, *, length_mm=1200.0, quantity=1, confidence=96):
    return {
        "mark": mark, "section": section, "length_mm": length_mm,
        "quantity": quantity, "confidence": confidence,
    }


def _connection(*, confidence=96):
    return {
        "connection_type": "bolted",
        "confidence": confidence,
        "bolts": [{"size": "M20", "grade": "8.8", "quantity": 4}],
        "connects_members": [],
    }


# ---------------------------------------------------------------------------
# DXF fixtures — genuinely written files, read by the genuine reader.
# ---------------------------------------------------------------------------
def _new_doc(*layers):
    import ezdxf

    doc = ezdxf.new("R2010")
    for layer in layers:
        doc.layers.add(layer)
    return doc


def _write_paired(path, callouts=(EXACT_TOKEN,)):
    """A clean drawing: every callout sits beside its own LINE."""
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER)
    msp = doc.modelspace()
    for index, callout in enumerate(callouts):
        base_y = index * 4000.0
        msp.add_line((0, base_y), (LINE_LENGTH, base_y),
                     dxfattribs={"layer": GEOMETRY_LAYER})
        msp.add_text(callout, height=2.5,
                     dxfattribs={"layer": TEXT_LAYER, "insert": (0, base_y + 500.0)})
    doc.saveas(str(path))
    return path


def _write_unmatched(path):
    """A drawing whose only callout the catalogue cannot answer for."""
    return _write_paired(path, callouts=(UNMATCHED_TOKEN,))


def _write_with_a_drop(path):
    """A drawing with one paired callout and one the reader cannot pair — the
    J11 primary discard."""
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER)
    msp = doc.modelspace()
    msp.add_line((0, 0), (LINE_LENGTH, 0), dxfattribs={"layer": GEOMETRY_LAYER})
    msp.add_text(EXACT_TOKEN, height=2.5,
                 dxfattribs={"layer": TEXT_LAYER, "insert": (0, 500.0)})
    msp.add_text("310UB40", height=2.5,
                 dxfattribs={"layer": TEXT_LAYER, "insert": (0, 9000.0)})
    doc.saveas(str(path))
    return path


def _write_with_a_connection(path):
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER, CONNECTION_LAYER)
    msp = doc.modelspace()
    msp.add_line((0, 0), (LINE_LENGTH, 0), dxfattribs={"layer": GEOMETRY_LAYER})
    msp.add_text(EXACT_TOKEN, height=2.5,
                 dxfattribs={"layer": TEXT_LAYER, "insert": (0, 500.0)})
    msp.add_text("4xM20 Gr8.8 End plate 16mm", height=2.5,
                 dxfattribs={"layer": CONNECTION_LAYER, "insert": (0, 2000.0)})
    doc.saveas(str(path))
    return path


# ===========================================================================
# 1. The vocabulary J13 found in the repository — documented, not invented.
# ===========================================================================
class TestTheVocabularyDiscovered:
    def test_the_one_accepted_extraction_state_is_the_repositorys_own_value(self, modules):
        """The terminal state is the value the extraction paths themselves use
        for a record they do not hold for a human — nothing new is coined."""
        assert modules.status.COMPLETE_REVIEW_STATUSES == frozenset({ACCEPTED_STATUS})
        assert BLOCKING_STATUS not in modules.status.COMPLETE_REVIEW_STATUSES

    def test_the_module_invents_no_review_state_of_its_own(self):
        """Every review-status literal that appears in the module is one of the
        two the production extractors already write. No third state is added."""
        literals = {
            node.value for node in ast.walk(ast.parse(MODULE_PATH.read_text()))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        candidates = {
            "extracted", "review_required", "approved", "pending_review", "in_review",
            "APPROVED", "COMPLETE", "RESOLVED", "reviewed", "accepted",
        }
        assert literals & candidates == {ACCEPTED_STATUS, BLOCKING_STATUS}

    def test_both_extractors_write_only_the_vocabulary_the_derivation_knows(
        self, modules, run_dxf, run_pdf
    ):
        """Read off the rows the extractors ACTUALLY persist: the two values the
        derivation consumes are the two values extraction produces, and no other
        review state reaches a member or a connection row."""
        dxf_run = run_dxf(_write_with_a_connection)
        pdf_run = run_pdf([_page(modules, 1, [
            _member("M1", EXACT_TOKEN),
            _member("M2", EXACT_TOKEN, confidence=50),
        ])])

        observed = set()
        for rows in (dxf_run.members, dxf_run.connections,
                     pdf_run.members, pdf_run.connections):
            for row in rows:
                if "review_status" in row:
                    observed.add(row["review_status"])

        assert observed == {ACCEPTED_STATUS, BLOCKING_STATUS}

    def test_approved_is_not_treated_as_the_extraction_terminal_state(self, decide):
        """`approved` is a REVIEWER-workflow state (app/cad_engine), written by no
        extraction path. It is therefore not this derivation's terminal state, and
        it fails closed rather than being guessed into one."""
        assert decide(
            member_review_statuses=["approved"], connection_review_statuses=[],
            unmatched_sections=[], warnings=[],
        ) == REVIEW


# ===========================================================================
# 2. The derivation rule — the truth table.
# ===========================================================================
class TestTheDerivationRule:
    def test_an_explicitly_complete_member_with_no_blockers_is_done(self, decide):
        assert decide(member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
                      unmatched_sections=[], warnings=[]) == DONE

    def test_an_explicitly_complete_connection_with_no_blockers_is_done(self, decide):
        assert decide(member_review_statuses=[ACCEPTED_STATUS],
                      connection_review_statuses=[ACCEPTED_STATUS],
                      unmatched_sections=[], warnings=[]) == DONE

    def test_a_member_held_for_review_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[BLOCKING_STATUS], connection_review_statuses=[],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("MEMBER_REVIEW_REQUIRED:1",)

    def test_a_missing_member_review_status_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[None], connection_review_statuses=[],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("MEMBER_REVIEW_STATUS_MISSING:1",)

    def test_an_unknown_member_review_status_blocks(self, decide_with_blockers):
        """A value this repository's vocabulary does not contain — including one a
        future milestone might introduce — is not evidence of completeness."""
        decision = decide_with_blockers(
            member_review_statuses=["pending_review"], connection_review_statuses=[],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("MEMBER_REVIEW_STATUS_UNKNOWN:1",)

    def test_a_blank_member_review_status_is_a_missing_statement(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=["   "], connection_review_statuses=[],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("MEMBER_REVIEW_STATUS_MISSING:1",)

    def test_a_held_connection_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS],
            connection_review_statuses=[BLOCKING_STATUS],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("CONNECTION_REVIEW_REQUIRED:1",)

    def test_a_missing_connection_review_status_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[None],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("CONNECTION_REVIEW_STATUS_MISSING:1",)

    def test_an_unknown_connection_review_status_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS],
            connection_review_statuses=["something_new"],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("CONNECTION_REVIEW_STATUS_UNKNOWN:1",)

    def test_a_non_string_status_fails_closed(self, decide):
        """A value a row cannot legitimately carry is not evidence of anything —
        and so not evidence of completeness."""
        for value in (42, {"status": "extracted"}, ["extracted"], True):
            assert decide(
                member_review_statuses=[value], connection_review_statuses=[],
                unmatched_sections=[], warnings=[],
            ) == REVIEW

    def test_an_unmatched_section_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
            unmatched_sections=[UNMATCHED_TOKEN], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("UNMATCHED_SECTIONS:1",)

    def test_empty_unmatched_sections_do_not_by_themselves_block(self, decide):
        assert decide(member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
                      unmatched_sections=[], warnings=[]) == DONE

    def test_every_member_explicitly_complete_is_done(self, decide):
        assert decide(member_review_statuses=[ACCEPTED_STATUS] * 5,
                      connection_review_statuses=[ACCEPTED_STATUS] * 3,
                      unmatched_sections=[], warnings=[]) == DONE

    def test_one_incomplete_member_among_complete_ones_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS, BLOCKING_STATUS, ACCEPTED_STATUS],
            connection_review_statuses=[], unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("MEMBER_REVIEW_REQUIRED:1",)

    def test_one_incomplete_connection_among_complete_ones_blocks(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS],
            connection_review_statuses=[ACCEPTED_STATUS, BLOCKING_STATUS],
            unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("CONNECTION_REVIEW_REQUIRED:1",)

    def test_the_blocker_counts_are_by_kind_and_the_order_is_stable(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[None, "future_state", BLOCKING_STATUS],
            connection_review_statuses=[None],
            unmatched_sections=["A", "B"], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == (
            "MEMBER_REVIEW_REQUIRED:1", "MEMBER_REVIEW_STATUS_MISSING:1",
            "MEMBER_REVIEW_STATUS_UNKNOWN:1", "CONNECTION_REVIEW_STATUS_MISSING:1",
            "UNMATCHED_SECTIONS:2",
        )

    def test_done_carries_no_blockers_and_review_always_carries_some(self, decide_with_blockers):
        """`is_done` is exactly the emptiness of the blocker list — there is no
        second definition of completeness anywhere."""
        done = decide_with_blockers(member_review_statuses=[ACCEPTED_STATUS],
                                    connection_review_statuses=[], unmatched_sections=[],
                                    warnings=[])
        assert done.is_done and done.blockers == () and str(done) == DONE
        held = decide_with_blockers(member_review_statuses=[BLOCKING_STATUS],
                                    connection_review_statuses=[], unmatched_sections=[],
                                    warnings=[])
        assert not held.is_done and held.blockers and str(held) == REVIEW

    def test_the_decision_is_immutable(self, decide_with_blockers):
        decision = decide_with_blockers(member_review_statuses=[ACCEPTED_STATUS],
                                        connection_review_statuses=[], unmatched_sections=[],
                                        warnings=[])
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.status = REVIEW


# ===========================================================================
# 3. Zero evidence — absence, not an empty drawing.
# ===========================================================================
class TestTheZeroEvidenceCase:
    def test_a_project_that_extracted_nothing_is_not_done(self, decide_with_blockers):
        """The extraction contract states what it extracted; it never states that
        the source was empty. Nothing at all is the absence of evidence."""
        decision = decide_with_blockers(member_review_statuses=[],
                                        connection_review_statuses=[],
                                        unmatched_sections=[], warnings=[])
        assert decision.status == REVIEW
        assert decision.blockers == ("NO_EXTRACTED_EVIDENCE:1",)

    def test_members_with_no_connections_is_a_legitimate_complete_outcome(self, decide):
        """ "No connection details were identified in this file" is a reportable
        outcome the report already states (app/report/pdf_generator.py), so a
        member schedule with no connections is not treated as unknown."""
        assert decide(member_review_statuses=[ACCEPTED_STATUS],
                      connection_review_statuses=[], unmatched_sections=[],
                      warnings=[]) == DONE

    def test_connections_with_no_members_are_judged_on_their_own_evidence(self, decide):
        """Not zero objects — a positive claim was made about each connection, so
        the per-row rule applies rather than the empty case."""
        assert decide(member_review_statuses=[],
                      connection_review_statuses=[ACCEPTED_STATUS],
                      unmatched_sections=[], warnings=[]) == DONE


# ===========================================================================
# 4. Purity — no database, no network, no filesystem, no AI.
# ===========================================================================
class TestTheFunctionIsPure:
    def test_the_module_imports_nothing_that_could_reach_outside(self):
        tree = ast.parse(MODULE_PATH.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported == {
            "dataclasses", "typing",
            "app.validation.dxf_drop_accounting",
            # Milestone J15: the drawing set's own coverage record. That module
            # is held to the same purity by its own pinned import set in
            # tests/test_real_world_j15_pdf_coverage_truth.py, so this import
            # cannot become the way out of this module either.
            "app.validation.page_coverage",
        }, sorted(imported)

    def test_the_module_names_no_client_or_io_entry_point(self):
        forbidden = {
            "supabase", "requests", "httpx", "urlopen", "socket", "subprocess",
            "os", "sys", "open", "Path", "ezdxf", "Anthropic", "create_client",
        }
        tree = ast.parse(MODULE_PATH.read_text())
        named = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
        assert not (named & forbidden), sorted(named & forbidden)

    def test_the_module_is_not_on_the_geometry_or_fabrication_path(self):
        """The reader's own structural guard forbids it importing geometry or
        fabrication code; the derivation must not become the way around that."""
        tree = ast.parse(MODULE_PATH.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert not [name for name in imported
                    if any(part in name for part in ("cad_engine", "drawing", "fabricat"))]

    def test_calling_it_touches_no_client(self, modules):
        guard = _NetworkGuardClient()
        real = modules.dxf.supabase
        modules.dxf.supabase = guard
        try:
            modules.status.derive_project_status(
                member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
                unmatched_sections=[], warnings=[])
        finally:
            modules.dxf.supabase = real
        assert guard.touched == []

    def test_calling_it_opens_no_file(self, modules, monkeypatch):
        def explode(*args, **kwargs):  # pragma: no cover - only on a failure
            raise AssertionError("the derivation attempted filesystem access")

        monkeypatch.setattr(builtins, "open", explode)
        # Milestone J15: coverage is handed to the derivation as an already-read
        # record, never re-derived from the file — which is why "done" is still
        # reachable with every filesystem entry point closed.
        decision = modules.status.derive_project_status(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
            unmatched_sections=[], warnings=[],
            coverage=modules.coverage.coverage_of(total_pages=1, page_numbers=[1]),
        )
        assert decision.status == DONE

    def test_it_is_deterministic_for_the_same_evidence(self, modules):
        evidence = dict(
            member_review_statuses=[ACCEPTED_STATUS, BLOCKING_STATUS],
            connection_review_statuses=[None],
            unmatched_sections=["X"],
            warnings=["DXF extraction: 1 drawing evidence item was not extracted."],
        )
        assert (modules.status.derive_project_status(**evidence)
                == modules.status.derive_project_status(**evidence))

    def test_it_does_not_mutate_the_evidence_it_is_given(self, modules):
        members = [ACCEPTED_STATUS, None]
        connections = [BLOCKING_STATUS]
        unmatched = ["X"]
        warnings = ["DXF extraction: 1 drawing evidence item was not extracted."]
        modules.status.derive_project_status(
            member_review_statuses=members, connection_review_statuses=connections,
            unmatched_sections=unmatched, warnings=warnings)
        assert members == [ACCEPTED_STATUS, None]
        assert connections == [BLOCKING_STATUS]
        assert unmatched == ["X"]
        assert warnings == ["DXF extraction: 1 drawing evidence item was not extracted."]


# ===========================================================================
# 5. J11 interaction — through J11's own contract, not by matching prose.
# ===========================================================================
class TestTheJ11Interaction:
    def test_a_j11_discard_prevents_done(self, decide_with_blockers):
        decision = decide_with_blockers(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
            unmatched_sections=[],
            warnings=["DXF extraction: 1 drawing evidence item was not extracted.",
                      "Section callouts not extracted (no matching line found): 1 — 310UB40"])
        assert decision.status == REVIEW
        assert decision.blockers == ("DXF_DROPPED_EVIDENCE:1",)

    def test_no_j11_warning_does_not_by_itself_block(self, decide):
        assert decide(member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
                      unmatched_sections=[], warnings=[]) == DONE

    def test_the_j11_rule_reads_the_accounting_s_own_contract(self, modules):
        """The derivation asks J11's own helper whether an accounting line is
        present, rather than pattern-matching the text itself."""
        assert "headline_from_warnings" in MODULE_PATH.read_text()
        assert modules.status.headline_from_warnings is modules.drop.headline_from_warnings
        # A non-accounting warning is not a discard: the PDF path's own
        # validation findings share this column and must not be read as one.
        assert modules.drop.headline_from_warnings(
            ["Mark 'M1' has conflicting section definitions across the drawing set: ['A', 'B']"]
        ) is None

    def test_a_pdf_validation_warning_is_not_mistaken_for_a_discard(self, decide):
        assert decide(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
            unmatched_sections=[],
            warnings=["12 record(s) appear to describe timber or other non-steel material "
                      "and were excluded from the steel schedule: T1, T2"],
        ) == DONE

    def test_the_j11_case_is_exactly_what_the_reader_persists(self, run_dxf):
        """The unit rule above is fed by the real reader: a run that discarded
        evidence persists the accounting line the rule keys off."""
        result = run_dxf(_write_with_a_drop)
        persisted = result.payload["warnings"]
        assert persisted and persisted[0].startswith("DXF extraction:")
        assert result.summary["dropped_evidence_count"] == 1
        assert result.payload["status"] == REVIEW


# ===========================================================================
# 6. DXF production-path integration.
# ===========================================================================
class TestTheDXFPathIntegration:
    def test_a_clean_dxf_project_stays_at_review_and_the_reason_is_the_evidence(self, run_dxf):
        """This reader states no member review state at all (the contract its
        boundary suite pins: "review_status" is absent from an exact member), so a
        DXF run does not prove completeness and fail-closes. Derived, not
        hardcoded — and the outcome is still "review"."""
        result = run_dxf(_write_paired)
        assert "review_status" not in result.members[0]
        assert result.payload["status"] == REVIEW

    def test_the_payload_shape_is_unchanged_by_the_derivation(self, run_dxf):
        result = run_dxf(_write_paired)
        assert set(result.payload) == DXF_PROJECT_PAYLOAD_KEYS

    def test_an_unmatched_dxf_section_stays_at_review(self, run_dxf):
        result = run_dxf(_write_unmatched)
        assert result.payload["unmatched_sections"] == [UNMATCHED_TOKEN]
        assert result.payload["status"] == REVIEW

    def test_a_dxf_connection_cannot_prove_completeness_either(self, run_dxf):
        result = run_dxf(_write_with_a_connection)
        assert result.payload["total_connections"] == 1
        assert "review_status" not in result.connections[0]
        assert result.payload["status"] == REVIEW

    def test_the_extraction_itself_is_unchanged(self, run_dxf):
        """Only the status value moved. The member this reader extracted is the
        same member it extracted before this milestone, field for field."""
        result = run_dxf(_write_paired)
        member = result.members[0]
        assert member["section_name"] == EXACT_TOKEN
        assert member["length_mm"] == LINE_LENGTH
        assert member["weight_per_metre"] == 46.2
        assert member["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        assert result.summary["members_extracted"] == 1
        assert result.summary["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        assert result.payload["total_members"] == 1
        assert result.payload["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        assert result.payload["total_weight_tonnes"] == round(EXACT_MEMBER_WEIGHT_KG / 1000, 3)

    def test_no_table_named_for_the_status_is_written(self, run_dxf):
        result = run_dxf(_write_paired)
        tables = {call["table"] for call in result.recorder.calls}
        assert not [name for name in tables if "status" in name]


# ===========================================================================
# 7. PDF production-path integration.
# ===========================================================================
class TestThePDFPathIntegration:
    def test_a_clean_pdf_extraction_can_now_reach_done(self, modules, run_pdf):
        """This path states a review state on every member it writes, so a run
        whose members all came back "extracted" has the evidence to prove
        completeness — and the project is no longer parked at "review"."""
        run = run_pdf([_page(modules, 1, [_member("M1", EXACT_TOKEN)])])
        assert run.members[0]["review_status"] == ACCEPTED_STATUS
        assert run.summary["status"] == DONE

    def test_a_low_confidence_pdf_member_stays_at_review(self, modules, run_pdf):
        run = run_pdf([_page(modules, 1, [_member("M1", EXACT_TOKEN, confidence=50)])])
        assert run.members[0]["review_status"] == BLOCKING_STATUS
        assert run.summary["status"] == REVIEW

    def test_an_unresolved_pdf_member_stays_at_review(self, modules, run_pdf):
        run = run_pdf([_page(modules, 1, [_member("M1", UNMATCHED_TOKEN)])])
        assert run.members[0]["review_status"] == BLOCKING_STATUS
        assert run.summary["unmatched_sections"] == [UNMATCHED_TOKEN]
        assert run.summary["status"] == REVIEW

    def test_a_complete_pdf_connection_does_not_hold_the_project(self, modules, run_pdf):
        run = run_pdf([_page(modules, 1, [_member("M1", EXACT_TOKEN)],
                             connections=[_connection()])])
        assert run.connections[0]["review_status"] == ACCEPTED_STATUS
        assert run.summary["status"] == DONE

    def test_a_low_confidence_pdf_connection_holds_the_project(self, modules, run_pdf):
        run = run_pdf([_page(modules, 1, [_member("M1", EXACT_TOKEN)],
                             connections=[_connection(confidence=50)])])
        assert run.connections[0]["review_status"] == BLOCKING_STATUS
        assert run.summary["status"] == REVIEW

    def test_the_pdf_payload_shape_is_unchanged_by_the_derivation(self, modules, run_pdf):
        run = run_pdf([_page(modules, 1, [_member("M1", EXACT_TOKEN)])])
        assert set(run.summary) == PDF_PROJECT_PAYLOAD_KEYS | {"project_id"}

    def test_the_persisted_member_row_is_otherwise_unchanged(self, modules, run_pdf):
        """The row this path writes is the row it wrote before: same keys, same
        values. Only the project's status value moved."""
        run = run_pdf([_page(modules, 1, [_member("M1", EXACT_TOKEN)])])
        member = run.members[0]
        assert set(member) == PDF_MEMBER_ROW_KEYS | {"id"}
        assert member["section_name"] == EXACT_TOKEN
        assert member["section_name_raw"] == EXACT_TOKEN
        assert member["section_family"] == "UB"
        assert member["weight_per_metre"] == 46.2
        assert member["grade"] is None
        assert member["extraction_method"] == "vision_claude"

    def test_a_pdf_extraction_that_found_nothing_is_not_done(self, modules, run_pdf):
        run = run_pdf([_page(modules, 1, [])])
        assert run.result["members_extracted"] == 0
        assert run.summary["total_members"] == 0
        assert run.summary["status"] == REVIEW


# ===========================================================================
# 8. No schema change, no migration, no new column.
# ===========================================================================
class TestNoSchemaChange:
    def test_no_migration_was_added(self):
        present = sorted(path.name for path in (REPO / "supabase" / "migrations").iterdir())
        assert present == sorted(MIGRATIONS)

    def test_no_migration_touches_the_status_this_milestone_derives(self):
        """This milestone's claim is unchanged: `projects.status` is DERIVED, and no
        migration — J13's three or J22's one — alters it."""
        for path in sorted((REPO / "supabase" / "migrations").glob("*.sql")):
            text = path.read_text(encoding="utf-8").lower()
            assert "alter table public.projects" not in text, path.name
            assert "projects.status" not in text, path.name

    def test_the_module_contains_no_ddl(self):
        source = MODULE_PATH.read_text().lower()
        for keyword in ("create table", "alter table", "create function", "create trigger"):
            assert keyword not in source, keyword

    def test_the_lifecycle_vocabulary_is_still_two_values(self, modules):
        """The derivation adds no third state: it answers "done" or "review"."""
        assert {modules.status.STATUS_DONE, modules.status.STATUS_REVIEW} == {DONE, REVIEW}


# ===========================================================================
# 9. No network — structurally.
# ===========================================================================
class TestNoNetwork:
    def test_the_guards_themselves_refuse(self):
        """A fresh guard, so this test cannot disturb the module-level guards'
        own record of what the production code touched."""
        guard = _NetworkGuardClient()
        with pytest.raises(AssertionError):
            guard.table("projects")
        assert guard.touched == ["projects"]

    def test_neither_client_was_reached_unpatched(self, modules, run_dxf):
        result = run_dxf(_write_paired)
        assert result.payload["status"] == REVIEW
        for guard in modules.guards.values():
            assert guard.touched == []
