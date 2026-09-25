"""
J11 — DXF EXTRACTION DROP ACCOUNTABILITY AND PERSISTENCE PARITY.

Before this milestone the DXF reader could see evidence the drawing stated and
fail to turn it into a row with NOTHING said anywhere: no row, no review item,
no warning, no unmatched-section record, no count, no report disclosure. The
callout vanished in a `continue` and the schedule simply looked smaller.

This file drives the GENUINE production modules — app.drawing_reading.dxf_parser
(the real reader), app.validation.dxf_drop_accounting (the real vocabulary and
collector), app.report.pdf_generator and app.main (the real report path) — over
genuinely written DXF files, and asserts that a discard is DETECTED, COUNTED,
REASONED, PERSISTED and REPORTED, and that nothing is invented to stand in for
what was discarded.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not widen the reader's vocabulary, change what is extracted, or make the
round-trip extraction succeed. Fixture D (the SteelSpec writer's own output) is
expected to extract NOTHING — the claim tested there is that the drawing's own
evidence is accounted for rather than silently reported as zero.

THE BOUNDARY
------------
Both production modules are imported under a deliberately fake, JWT-shaped
configuration and each module's Supabase client is replaced by an in-process
double, so no test here can reach the network even by mistake (the guard raises
rather than connecting). No live catalogue is read and no production data is
written: the section catalogue used by the reader is the pinned row set below,
supplied through the same injection seam the J4/J7 files use.

The 11 pre-existing tests/test_smoke_imports.py failures (app/config.py reads
its environment at import time) MUST keep failing honestly, so every module this
file imports is removed from sys.modules again at teardown.
"""
import ast
import copy
import io
import os
import sys
import types
from pathlib import Path

import ezdxf
import pytest

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# The deliberately fake configuration boundary. supabase-py validates the key
# SHAPE at import, so this placeholder is JWT-shaped but is not a credential and
# is never sent anywhere (every client is replaced before any use).
# ---------------------------------------------------------------------------
TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)
ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")

# ---------------------------------------------------------------------------
# The pinned catalogue rows the reader is given. They are the same three live
# rows tests/test_real_world_dxf_authority_boundary.py pins from the captured
# /tmp snapshot (every key the live table carries, including the nulls), kept
# here so this file is self-contained. 250PFC is the section the genuine writer
# is asked to draw in fixture D.
# ---------------------------------------------------------------------------
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

# The layers the production classifier reads, and one it does not.
GEOMETRY_LAYER = "S-BEAM"
TEXT_LAYER = "S-TEXT"
CONNECTION_LAYER = "S-CONN"
UNREAD_LAYER = "ANNO"
WRITER_LAYER = "0"

LINE_LENGTH = 3000.0
# The exact weight the reader has always calculated for a 3000 mm 310UB46.2
# member (46.2 kg/m) — pinned so "extraction is unchanged" is a number.
EXACT_MEMBER_WEIGHT_KG = 138.6

# The four reasons this file can provoke from a real drawing, and the category
# each one belongs to (app/validation/dxf_drop_accounting.REASON_CATEGORY).
NO_PAIRED_LINE = "NO_PAIRED_LINE"
UNRECOGNISED_TEXT_LAYER = "UNRECOGNISED_TEXT_LAYER"
UNSUPPORTED_SOURCE_GEOMETRY = "UNSUPPORTED_SOURCE_GEOMETRY"
UNSUPPORTED_CALLOUT_SHAPE = "UNSUPPORTED_CALLOUT_SHAPE"
NO_CHILD_EVIDENCE_PARSED = "NO_CHILD_EVIDENCE_PARSED"
NO_MEMBER_WITHIN_THRESHOLD = "NO_MEMBER_WITHIN_THRESHOLD"

# The project columns the DXF path writes. J11 adds NO column and NO table: the
# accounting travels in the two array columns the PDF path already writes.
PROJECT_PAYLOAD_KEYS = {
    "status", "total_members", "total_unique_sections", "total_connections",
    "total_weight_kg", "total_weight_tonnes", "unmatched_sections", "warnings",
}
TABLES_THE_DXF_PATH_MAY_WRITE = {
    "steel_members", "connections", "bolt_groups", "weld_details",
    "connection_plates", "connection_members", "projects",
}


# ===========================================================================
# The repository-boundary doubles. No test here touches the network.
# ===========================================================================
class _FakeExecuteResult:
    """The single shape the production code reads: result.data."""

    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Answers SectionMatcher.__init__'s one query, in-process."""

    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name

    def select(self, columns="*"):
        self._client.queries.append((self._table, columns))
        return self

    def execute(self):
        self._client.executed += 1
        return _FakeExecuteResult(copy.deepcopy(self._client.rows))


class _FakeSupabaseClient:
    """The deterministic catalogue double standing in for the matcher's client."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _RecordingQuery:
    """
    The parser's OWN Supabase calls: insert()/update().eq().execute(). Every call
    is recorded and answered in-process, so a test can read exactly which rows
    and which project columns the production parser tried to persist.
    """

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
            "table": self._table,
            "op": self._op,
            "payload": self._payload,
            "filters": list(self._filters),
        })
        if self._op == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            self._client.inserted.setdefault(self._table, []).extend(copy.deepcopy(rows))
            return _FakeExecuteResult([
                {**row, "id": f"{self._table}-{index}"}
                for index, row in enumerate(rows)
            ])
        return _FakeExecuteResult([])


class _RecordingSupabaseClient:
    """The double standing in for the parser's module-level client."""

    def __init__(self):
        self.calls = []
        self.inserted = {}

    def table(self, name):
        return _RecordingQuery(self, name)

    def rows_inserted_into(self, table):
        return self.inserted.get(table, [])


class _ReportQuery:
    """
    One of the three SELECTs app/report/pdf_generator.py makes.

    It has no write method at all: the report generator is a reader, and this
    double can only hand it rows the production extraction already produced.
    """

    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        self._client.queries.append(self._table)
        return _FakeExecuteResult(self._client.tables[self._table])


class _ReportSupabase:
    """The report generator's read boundary, fed from captured production rows."""

    def __init__(self, project, members, connections):
        self.tables = {
            "projects": project,
            "steel_members": members,
            "connections": connections,
        }
        self.queries = []

    def table(self, name):
        return _ReportQuery(self, name)


class _NetworkGuardClient:
    """
    Installed on each production module for the duration of this file so that an
    UNPATCHED dependency fails loudly here instead of reaching the network. It
    exists to make "no network" structural, not merely intended.
    """

    def table(self, name):  # pragma: no cover - only reached on a test bug
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
    """
    The REAL production modules, imported once under the test-only configuration
    boundary, with each module's client global replaced by the network guard.
    """
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        import app.drawing_reading.dxf_parser as dxf_module
        import app.engineering_data.section_matcher as matcher_module
        import app.report.pdf_generator as report_module
        import app.main as main_module
        import app.validation.dxf_drop_accounting as drop_module
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_clients = {
        "dxf": dxf_module.supabase,
        "matcher": matcher_module.supabase,
        "report": report_module.supabase,
        "main": main_module.supabase,
    }
    dxf_module.supabase = _NetworkGuardClient()
    matcher_module.supabase = _NetworkGuardClient()
    report_module.supabase = _NetworkGuardClient()
    main_module.supabase = _NetworkGuardClient()

    namespace = types.SimpleNamespace(
        dxf=dxf_module,
        matcher=matcher_module,
        report=report_module,
        main=main_module,
        drop=drop_module,
    )
    try:
        yield namespace
    finally:
        dxf_module.supabase = real_clients["dxf"]
        matcher_module.supabase = real_clients["matcher"]
        report_module.supabase = real_clients["report"]
        main_module.supabase = real_clients["main"]
        # Only app-prefixed modules are removed: the C extensions this suite
        # imports (cadquery/numpy/ezdxf) must survive for the rest of the run.
        for name in set(sys.modules) - before:
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


@pytest.fixture()
def build_real_matcher(modules, monkeypatch):
    """build(rows) -> (matcher, double): a REAL SectionMatcher with only its
    external dependency replaced."""

    def build(rows):
        double = _FakeSupabaseClient(rows)
        monkeypatch.setattr(modules.matcher, "supabase", double)
        matcher = modules.matcher.SectionMatcher()
        return matcher, double

    return build


@pytest.fixture()
def real_matcher(build_real_matcher):
    matcher, _ = build_real_matcher(copy.deepcopy(list(PINNED_ROWS)))
    return matcher


@pytest.fixture()
def recorder(modules, monkeypatch):
    """The parser's own client, replaced by a recording, in-process double."""
    double = _RecordingSupabaseClient()
    monkeypatch.setattr(modules.dxf, "supabase", double)
    return double


def _project_payload(recorder, project_id):
    """
    The single `projects` UPDATE this run made, asserted to be exactly one and to
    be scoped to this project — the persistence path, read verbatim.
    """
    updates = [call for call in recorder.calls if call["table"] == "projects"]
    assert len(updates) == 1, f"expected exactly one project update, got {len(updates)}"
    assert updates[0]["op"] == "update"
    assert updates[0]["filters"] == [("id", project_id)]
    return updates[0]["payload"]


@pytest.fixture()
def run_dxf(tmp_path, modules, recorder, real_matcher):
    """
    Runs the GENUINE production reader over a genuinely written DXF and returns
    the summary it reported, the project columns it persisted, the rows it
    inserted, and the recorder itself.
    """

    def run(write, *, project_id="j11-project", matcher=None):
        path = write(tmp_path)
        summary = modules.dxf.parse_dxf_and_save(str(path), project_id,
                                                 matcher=matcher or real_matcher)
        return types.SimpleNamespace(
            summary=summary,
            payload=_project_payload(recorder, project_id),
            recorder=recorder,
            members=recorder.rows_inserted_into("steel_members"),
            connections=recorder.rows_inserted_into("connections"),
            project_id=project_id,
            path=path,
        )

    return run


def _pdf_text(pdf_bytes):
    """The real rendered text of the production report."""
    import pypdf

    assert pdf_bytes, "the production report produced no bytes"
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(
        " ".join((page.extract_text() or "") for page in reader.pages).split()
    )


@pytest.fixture()
def report_of(modules, monkeypatch, tmp_path):
    """
    Drives the GENUINE report path over the rows a production extraction
    produced:

        projects row (as the reader persisted it) + steel_members + connections
          -> real generate_report_pdf
          -> real build_and_store_report (the same function app.main calls)

    Returns (report_path, rendered_text).
    """
    counter = {"n": 0}

    def build(payload, *, project_id="j11-project", members=(), connections=(),
              name="J11 test-only project"):
        counter["n"] += 1
        client = _ReportSupabase(
            project={"id": project_id, "name": name, **payload},
            members=[dict(m) for m in members],
            connections=list(connections),
        )
        monkeypatch.setattr(modules.report, "supabase", client)
        captured = {}
        monkeypatch.setattr(
            modules.main, "upload_and_record",
            lambda **kwargs: captured.update(kwargs) or kwargs["path"],
        )
        path = modules.main.build_and_store_report(project_id, "j11-user")
        assert captured.get("bucket") == "reports"
        return path, _pdf_text(captured["content"])

    return build


@pytest.fixture()
def run_and_report(run_dxf, report_of):
    """A production extraction AND the report generated from what it persisted."""

    def run(write, *, project_id="j11-project", matcher=None):
        result = run_dxf(write, project_id=project_id, matcher=matcher)
        report_path, text = report_of(
            result.payload, project_id=project_id, members=result.members,
            connections=result.connections,
        )
        result.report_path = report_path
        result.text = text
        return result

    return run


# ===========================================================================
# The fixtures themselves — genuinely written DXF files.
# ===========================================================================
def _new_doc(*layers):
    doc = ezdxf.new("R2010")
    for layer in layers:
        if layer not in doc.layers:
            doc.layers.add(layer)
    return doc


def write_callouts(path, callouts, *, with_lines=True, text_layer=TEXT_LAYER,
                   line_layer=GEOMETRY_LAYER, y_step=4000.0):
    """
    One steel LINE and one TEXT callout per entry, on the layers the production
    classifier reads, each text sitting 500 mm from its own line so the
    production pairing associates them one-to-one. `with_lines=False` writes the
    callouts with NO geometry at all — the real-world case where the drawing
    states a section and nothing can be paired with it.
    """
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER, CONNECTION_LAYER, UNREAD_LAYER)
    msp = doc.modelspace()
    for index, callout in enumerate(callouts):
        y = index * y_step
        if with_lines:
            msp.add_line((0, y), (LINE_LENGTH, y), dxfattribs={"layer": line_layer})
        msp.add_text(callout, height=2.5,
                     dxfattribs={"layer": text_layer, "insert": (0, y + 500.0)})
    doc.saveas(str(path))
    return path


def write_mixed_drawing(path, paired, unpaired):
    """
    Paired callouts (each with its own line) followed by unpaired ones, placed
    far from every line — so the drawing is partly readable and partly not, and
    the accounting has to report both truthfully in one run.
    """
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER)
    msp = doc.modelspace()
    for index, callout in enumerate(paired):
        y = index * 4000.0
        msp.add_line((0, y), (LINE_LENGTH, y), dxfattribs={"layer": GEOMETRY_LAYER})
        msp.add_text(callout, height=2.5,
                     dxfattribs={"layer": TEXT_LAYER, "insert": (0, y + 500.0)})
    base = len(paired) * 4000.0 + 20000.0
    for index, callout in enumerate(unpaired):
        msp.add_text(callout, height=2.5,
                     dxfattribs={"layer": TEXT_LAYER,
                                 "insert": (0, base + index * 4000.0)})
    doc.saveas(str(path))
    return path


def write_polyline_member(path, callout, *, layers=(GEOMETRY_LAYER,)):
    """
    A member drawn as POLYLINES — the shape SteelSpec itself writes — with its
    section callout beside it and no LINE anywhere: exactly what the reader's
    LINE-only geometry vocabulary cannot read.
    """
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER)
    msp = doc.modelspace()
    for layer in layers:
        msp.add_lwpolyline([(0, 0), (LINE_LENGTH, 0), (LINE_LENGTH, 300), (0, 300)],
                           dxfattribs={"layer": layer})
    msp.add_polyline2d([(0, 4000), (LINE_LENGTH, 4000), (LINE_LENGTH, 4300)],
                       dxfattribs={"layer": layers[-1]})
    msp.add_text(callout, height=2.5,
                 dxfattribs={"layer": TEXT_LAYER, "insert": (0, 500.0)})
    doc.saveas(str(path))
    return path


def write_unread_layer_text(path, texts, *, layer=UNREAD_LAYER):
    """Text on a layer the production classifier does not read as member text."""
    doc = _new_doc(layer)
    msp = doc.modelspace()
    for index, text in enumerate(texts):
        msp.add_text(text, height=2.5,
                     dxfattribs={"layer": layer, "insert": (0, index * 4000.0)})
    doc.saveas(str(path))
    return path


def write_connection_symbols_only(tmp):
    """
    Connection-layer geometry with no callout text at all: a hole circle and a
    leader. This reader reads connection callouts as TEXT only and has never
    read a circle as evidence, so this is deliberately NOT reported as a discard
    (see the docstring of app/validation/dxf_drop_accounting) — the test that
    uses it exists to pin that boundary rather than to leave it implicit.
    """
    path = tmp / "symbols.dxf"
    doc = _new_doc(CONNECTION_LAYER)
    msp = doc.modelspace()
    msp.add_circle((0, 0), 12.0, dxfattribs={"layer": CONNECTION_LAYER})
    msp.add_line((0, 0), (0, 300), dxfattribs={"layer": CONNECTION_LAYER})
    doc.saveas(str(path))
    return path


def write_connection_callouts(path, callouts, *, member_token="310UB46.2",
                              first_y=90000.0):
    """
    One steel LINE, one member annotation on the text layer, and one
    CONNECTION-layer callout per entry — far from the member, so the production
    association finds nothing to link them to.
    """
    doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER, CONNECTION_LAYER)
    msp = doc.modelspace()
    msp.add_line((0, 0), (LINE_LENGTH, 0), dxfattribs={"layer": GEOMETRY_LAYER})
    msp.add_text(member_token, height=2.5,
                 dxfattribs={"layer": TEXT_LAYER, "insert": (0, 500.0)})
    for index, callout in enumerate(callouts):
        msp.add_text(callout, height=2.5,
                     dxfattribs={"layer": CONNECTION_LAYER,
                                 "insert": (0, first_y + index * 400.0)})
    doc.saveas(str(path))
    return path


@pytest.fixture()
def writer_dxf(tmp_path):
    """
    The GENUINE SteelSpec fabrication writer's own DXF, produced by
    app.drawing_generator.interface.generate_fabrication_drawings from a real
    geometry built by app.cad_engine.interface.generate_geometry. Nothing about
    the writer or the geometry is stubbed; the file is written to disk and read
    back by the production reader.
    """
    try:
        from app.cad_engine.interface import ValidatedSteelMember, generate_geometry
        from app.drawing_generator.interface import generate_fabrication_drawings
    except ImportError as exc:  # pragma: no cover - environment without the engine
        pytest.skip(f"the geometry/drawing engine is not installed: {exc}")

    section_properties = {
        "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
        "flange_thickness": 15.0, "web_thickness": 8.0, "weight_per_metre": 35.5,
    }
    member = ValidatedSteelMember(
        mark="J11-WRITER-1", section="250PFC", length_mm=3000.0, material="300PLUS",
        orientation=None, connection_refs=[], source_refs=[{"page": 1}],
        validation_status="extracted", section_properties=section_properties,
    )
    geometry = generate_geometry(member)
    return generate_fabrication_drawings(geometry, tmp_path / "J11-WRITER-1.dxf")


# ===========================================================================
# 1. THE VOCABULARY — the smallest stable representation, and no engineering
#    value anywhere in it.
# ===========================================================================
class TestTheDropVocabulary:

    def test_every_reason_belongs_to_exactly_one_category(self, modules):
        reasons = {
            NO_PAIRED_LINE, UNRECOGNISED_TEXT_LAYER, UNSUPPORTED_SOURCE_GEOMETRY,
            UNSUPPORTED_CALLOUT_SHAPE, NO_CHILD_EVIDENCE_PARSED,
            NO_MEMBER_WITHIN_THRESHOLD,
        }
        assert reasons <= set(modules.drop.REASON_CATEGORY)
        assert set(modules.drop.REASON_CATEGORY) == reasons

    def test_the_three_categories_are_the_distinction_the_brief_draws(self, modules):
        categories = modules.drop.REASON_CATEGORY
        # Evidence the drawing states that this reader did not turn into a row.
        assert categories[NO_PAIRED_LINE] == modules.drop.CATEGORY_NOT_EXTRACTED
        assert categories[UNRECOGNISED_TEXT_LAYER] == modules.drop.CATEGORY_NOT_EXTRACTED
        assert (categories[NO_CHILD_EVIDENCE_PARSED]
                == modules.drop.CATEGORY_NOT_EXTRACTED)
        # A shape this reader has never read — reported, not called an error.
        assert (categories[UNSUPPORTED_SOURCE_GEOMETRY]
                == modules.drop.CATEGORY_UNSUPPORTED_BY_DESIGN)
        assert (categories[UNSUPPORTED_CALLOUT_SHAPE]
                == modules.drop.CATEGORY_UNSUPPORTED_BY_DESIGN)
        # Extracted, but not linked to anything.
        assert (categories[NO_MEMBER_WITHIN_THRESHOLD]
                == modules.drop.CATEGORY_NOT_ASSOCIATED)

    def test_every_reason_has_a_reader_facing_phrase(self, modules):
        assert set(modules.drop.REASON_PHRASE) == set(modules.drop.REASON_CATEGORY)
        for phrase in modules.drop.REASON_PHRASE.values():
            assert phrase and phrase[0].isupper()

    def test_the_phrase_vocabulary_carries_no_alarming_language(self, modules):
        # The brief's rule: factual language, no implied engineering error. A
        # discarded item is not a failure of the drawing.
        for phrase in modules.drop.REASON_PHRASE.values():
            lowered = phrase.lower()
            for word in ("error", "fail", "invalid", "corrupt", "wrong", "bad "):
                assert word not in lowered, (word, phrase)

    def test_an_unrecognised_reason_is_never_reported_as_an_extraction_failure(self, modules):
        # The vocabulary's own fail-safe: it claims no more about a discard than
        # it knows.
        drop = modules.drop.DXFDrop(kind="SOMETHING_NEW", reason="A_REASON_FROM_LATER")
        assert drop.category == modules.drop.CATEGORY_UNSUPPORTED_BY_DESIGN
        assert drop.phrase == "A_REASON_FROM_LATER"

    def test_a_record_states_what_and_why_and_no_engineering_value(self, modules):
        drop = modules.drop.DXFDrop(
            kind=modules.drop.KIND_MEMBER_CALLOUT, reason=NO_PAIRED_LINE,
            context="310UB46.2",
        )
        assert drop.as_dict() == {
            "kind": "MEMBER_CALLOUT", "reason": "NO_PAIRED_LINE", "context": "310UB46.2",
        }
        # There is no place for a length, a weight, a section or a grade.
        assert set(drop.as_dict()) == {"kind", "reason", "context"}

    def test_an_empty_accounting_says_nothing_at_all(self, modules):
        # Absence of a disclosure means nothing was discarded — never that
        # nothing was checked. A clean extraction must persist no accounting.
        accounting = modules.drop.DXFDropAccounting()
        assert not accounting
        assert len(accounting) == 0
        assert accounting.headline() == ""
        assert accounting.warnings() == []

    def test_the_headline_counts_what_was_not_extracted(self, modules):
        accounting = modules.drop.DXFDropAccounting()
        accounting.record(modules.drop.KIND_MEMBER_CALLOUT, NO_PAIRED_LINE, "310UB46.2")
        assert accounting.headline() == (
            "DXF extraction: 1 drawing evidence item was not extracted."
        )
        accounting.record(modules.drop.KIND_SOURCE_GEOMETRY,
                          UNSUPPORTED_SOURCE_GEOMETRY, "LWPolyline on layer '0'")
        assert accounting.headline() == (
            "DXF extraction: 2 drawing evidence items were not extracted."
        )

    def test_the_headline_separates_a_discard_from_a_missing_association(self, modules):
        accounting = modules.drop.DXFDropAccounting()
        accounting.record(modules.drop.KIND_MEMBER_CALLOUT, NO_PAIRED_LINE, "310UB46.2")
        accounting.record(modules.drop.KIND_CONNECTION_ASSOCIATION,
                          NO_MEMBER_WITHIN_THRESHOLD, "connection at (0, 90000)")
        assert accounting.headline() == (
            "DXF extraction: 1 drawing evidence item was not extracted and 1 "
            "connection was not associated with a member."
        )

    def test_the_persisted_lines_are_aggregated_per_reason_with_exact_counts(self, modules):
        accounting = modules.drop.DXFDropAccounting()
        for token in ("310UB46.2", "310UB40", "250X90PFC"):
            accounting.record(modules.drop.KIND_MEMBER_CALLOUT, NO_PAIRED_LINE, token)
        lines = accounting.warnings()
        assert lines[0] == "DXF extraction: 3 drawing evidence items were not extracted."
        assert lines[1].startswith(
            "Section callouts not extracted (no matching line found): 3"
        )
        assert lines[1].endswith("310UB46.2, 310UB40, 250X90PFC")

    def test_a_flood_of_discards_caps_the_names_but_never_the_count(self, modules):
        accounting = modules.drop.DXFDropAccounting()
        for index in range(13):
            accounting.record(modules.drop.KIND_SOURCE_GEOMETRY,
                              UNSUPPORTED_SOURCE_GEOMETRY, f"LWPolyline on layer 'L{index}'")
        line = accounting.warnings()[1]
        assert ": 13" in line
        assert line.endswith("and 5 more")

    def test_the_order_of_the_records_is_the_drawing_s_own_order(self, modules):
        accounting = modules.drop.DXFDropAccounting()
        accounting.record(modules.drop.KIND_SOURCE_GEOMETRY, UNSUPPORTED_SOURCE_GEOMETRY, "b")
        accounting.record(modules.drop.KIND_MEMBER_CALLOUT, NO_PAIRED_LINE, "a")
        assert [record.reason for record in accounting.drops] == [
            UNSUPPORTED_SOURCE_GEOMETRY, NO_PAIRED_LINE,
        ]
        assert list(accounting.by_reason()) == [UNSUPPORTED_SOURCE_GEOMETRY, NO_PAIRED_LINE]

    def test_the_report_finds_the_accounting_by_its_one_prefix(self, modules):
        assert modules.drop.WARNING_PREFIX == "DXF extraction: "
        warnings = [
            "310UB40: the catalogue offered 310UB40.4 instead (unrelated)",
            "DXF extraction: 1 drawing evidence item was not extracted.",
            "another PDF-path note",
        ]
        assert modules.drop.headline_from_warnings(warnings) == (
            "DXF extraction: 1 drawing evidence item was not extracted."
        )

    def test_a_pdf_path_warning_is_never_mistaken_for_a_dxf_disclosure(self, modules):
        # The disclosure is keyed on the accounting's own prefix, not on the
        # presence of any warning at all — otherwise every PDF project's notes
        # would render as "extraction not complete".
        assert modules.drop.headline_from_warnings([]) is None
        assert modules.drop.headline_from_warnings(None) is None
        assert modules.drop.headline_from_warnings(
            ["310UB40: the catalogue offered 310UB40.4 instead (unrelated)"]
        ) is None


# ===========================================================================
# 2. THE READER USES THE VOCABULARY ITSELF — no second copy to drift from.
# ===========================================================================
class TestTheReaderUsesTheOneVocabulary:

    def test_the_reason_codes_are_the_vocabulary_module_s_own_strings(self, modules):
        assert modules.dxf.REASON_NO_PAIRED_LINE is modules.drop.REASON_NO_PAIRED_LINE
        assert (
            modules.dxf.REASON_UNSUPPORTED_SOURCE_GEOMETRY
            is modules.drop.REASON_UNSUPPORTED_SOURCE_GEOMETRY
        )
        assert (
            modules.dxf.REASON_UNRECOGNISED_TEXT_LAYER
            is modules.drop.REASON_UNRECOGNISED_TEXT_LAYER
        )
        assert (
            modules.dxf.REASON_NO_MEMBER_WITHIN_THRESHOLD
            is modules.drop.REASON_NO_MEMBER_WITHIN_THRESHOLD
        )

    def test_the_reader_defines_no_reason_string_of_its_own(self, modules):
        # Structural: every reason code the reader records is the imported
        # constant, so the persisted strings cannot drift from the phrases and
        # categories defined in one place.
        source = (REPO / "app" / "drawing_reading" / "dxf_parser.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for reason in modules.drop.REASON_CATEGORY:
            assert reason not in literals, f"{reason} is re-spelled in the reader"
        for kind in (modules.drop.KIND_MEMBER_CALLOUT, modules.drop.KIND_SOURCE_GEOMETRY):
            assert kind not in literals, f"{kind} is re-spelled in the reader"

    def test_the_accounting_cannot_reach_geometry_or_fabrication(self, modules):
        # The structural guarantee the J7 boundary test already pins, re-read
        # here because J11 added a new import to this module.
        tree = ast.parse((REPO / "app" / "drawing_reading" / "dxf_parser.py").read_text(
            encoding="utf-8"
        ))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            name for name in imported
            if any(part in name for part in ("cad_engine", "drawing", "fabricat"))
        }
        assert forbidden <= {"app.drawing_reading.dxf_parser"}, sorted(forbidden)


# ===========================================================================
# 3. FIXTURE A — a recognised section callout with NO nearby LINE. The primary
#    silent drop this milestone exists to end.
# ===========================================================================
class TestACalloutWithNoLineIsRecordedNotLost:

    def test_the_callout_is_counted_and_reasoned(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "a.dxf", ["310UB46.2"],
                                                    with_lines=False))
        assert result.summary["dropped_evidence_count"] == 1
        assert result.summary["dropped_by_reason"] == {NO_PAIRED_LINE: 1}

    def test_the_record_names_the_drawn_token_and_why(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "a.dxf", ["310UB46.2"],
                                                    with_lines=False))
        assert result.summary["dropped_evidence"] == [{
            "kind": "MEMBER_CALLOUT",
            "reason": NO_PAIRED_LINE,
            "context": "310UB46.2",
        }]

    def test_no_member_row_is_invented_for_the_discarded_callout(self, run_dxf):
        # The brief's rule, exactly: do not silently continue, do not invent a
        # length, do not create a member row.
        result = run_dxf(lambda tmp: write_callouts(tmp / "a.dxf", ["310UB46.2"],
                                                    with_lines=False))
        assert result.summary["members_extracted"] == 0
        assert result.recorder.rows_inserted_into("steel_members") == []
        assert result.payload["total_members"] == 0

    def test_no_length_and_no_weight_is_attached_to_the_discard(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "a.dxf", ["310UB46.2"],
                                                    with_lines=False))
        record = result.summary["dropped_evidence"][0]
        assert record["context"] == "310UB46.2"
        assert "3000" not in record["context"]      # no length from the drawing
        assert "kg" not in record["context"]        # no weight from the catalogue
        assert set(record) == {"kind", "reason", "context"}

    def test_an_mtext_callout_is_recorded_by_the_token_it_states(self, run_dxf):
        def write(tmp):
            doc = _new_doc(TEXT_LAYER)
            msp = doc.modelspace()
            msp.add_mtext("310UB46.2\n(12 OFF)", dxfattribs={"layer": TEXT_LAYER})
            doc.saveas(str(tmp / "a-mtext.dxf"))
            return tmp / "a-mtext.dxf"

        result = run_dxf(write)
        assert result.summary["dropped_by_reason"] == {NO_PAIRED_LINE: 1}
        assert result.summary["dropped_evidence"][0]["context"] == "310UB46.2"

    def test_the_discard_is_persisted_on_the_project_and_stated_in_the_summary(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "a.dxf", ["310UB46.2"],
                                                    with_lines=False))
        assert result.payload["warnings"][0] == (
            "DXF extraction: 1 drawing evidence item was not extracted."
        )
        assert result.payload["warnings"][1] == (
            "Section callouts not extracted (no matching line found): 1 — 310UB46.2"
        )

    def test_a_mixed_drawing_reports_both_what_it_read_and_what_it_could_not(self, run_dxf):
        result = run_dxf(lambda tmp: write_mixed_drawing(
            tmp / "mixed.dxf", paired=["310UB46.2"], unpaired=["250PFC"],
        ))
        # What it read is unchanged: the paired member is exactly as before.
        assert result.summary["members_extracted"] == 1
        assert result.summary["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        assert result.members[0]["section_name"] == "310UB46.2"
        assert result.members[0]["length_mm"] == LINE_LENGTH
        # What it could not read is reported, and did not disturb the read.
        assert result.summary["dropped_by_reason"] == {NO_PAIRED_LINE: 1}
        assert result.summary["dropped_evidence"][0]["context"] == "250PFC"


# ===========================================================================
# 4. FIXTURE B — member geometry drawn as a POLYLINE.
# ===========================================================================
class TestPolylineGeometryIsRecorded:

    def test_the_unsupported_shape_is_recorded_on_every_entity(self, run_dxf):
        result = run_dxf(lambda tmp: write_polyline_member(tmp / "b.dxf", "310UB46.2"))
        assert result.summary["dropped_by_reason"] == {
            UNSUPPORTED_SOURCE_GEOMETRY: 2, NO_PAIRED_LINE: 1,
        }

    def test_the_records_name_the_shape_and_where_it_was_drawn(self, run_dxf):
        result = run_dxf(lambda tmp: write_polyline_member(tmp / "b.dxf", "310UB46.2"))
        geometry_records = [
            record for record in result.summary["dropped_evidence"]
            if record["reason"] == UNSUPPORTED_SOURCE_GEOMETRY
        ]
        assert [record["kind"] for record in geometry_records] == [
            "SOURCE_GEOMETRY", "SOURCE_GEOMETRY",
        ]
        assert [record["context"] for record in geometry_records] == [
            f"LWPolyline on layer '{GEOMETRY_LAYER}'",
            f"Polyline on layer '{GEOMETRY_LAYER}'",
        ]

    def test_the_polyline_is_never_turned_into_a_member(self, run_dxf):
        # A statement about SHAPE, not a claim that the entity is a member.
        result = run_dxf(lambda tmp: write_polyline_member(tmp / "b.dxf", "310UB46.2"))
        assert result.summary["members_extracted"] == 0
        assert result.recorder.rows_inserted_into("steel_members") == []
        assert result.payload["total_weight_kg"] == 0

    def test_a_polyline_on_the_writer_s_default_layer_is_still_recorded(self, run_dxf):
        # The layer the genuine writer draws on is recorded like any other: the
        # accounting is about SHAPE, so a layer the classifier would otherwise
        # ignore cannot hide a member drawn as something this reader cannot read.
        result = run_dxf(lambda tmp: write_polyline_member(
            tmp / "b0.dxf", "310UB46.2", layers=(WRITER_LAYER,),
        ))
        assert result.summary["dropped_by_reason"][UNSUPPORTED_SOURCE_GEOMETRY] == 2
        assert [
            record["context"] for record in result.summary["dropped_evidence"]
            if record["reason"] == UNSUPPORTED_SOURCE_GEOMETRY
        ] == ["LWPolyline on layer '0'", "Polyline on layer '0'"]


# ===========================================================================
# 5. FIXTURE C — a section callout on a layer the reader does not read.
# ===========================================================================
class TestTextOnAnUnreadLayerIsRecorded:

    def test_a_section_callout_on_an_unclassified_layer_is_recorded(self, run_dxf):
        result = run_dxf(lambda tmp: write_unread_layer_text(
            tmp / "c.dxf", ["310UB46.2"],
        ))
        assert result.summary["dropped_evidence"] == [{
            "kind": "MEMBER_CALLOUT",
            "reason": UNRECOGNISED_TEXT_LAYER,
            "context": f"310UB46.2 on layer '{UNREAD_LAYER}'",
        }]

    def test_ordinary_annotation_on_an_unread_layer_is_not_a_discard(self, run_dxf):
        # Dimensions, notes, detail tags and title-block text state no section
        # and are not member callouts. Reporting them would call ordinary
        # annotation "not extracted" — a false alarm, which the brief forbids.
        result = run_dxf(lambda tmp: write_unread_layer_text(
            tmp / "c-notes.dxf",
            ["SEE DETAIL 3", "GRID LINE A", "SECTION A-A", "50 mm CLEARANCE",
             "NOTE: WELD ALL ROUND", "rev C 24.09.26"],
        ))
        assert result.summary["dropped_evidence_count"] == 0
        assert result.payload["warnings"] == []

    def test_a_readable_layer_is_not_accounted_as_unread(self, run_dxf):
        # The same token on the layer the reader DOES read is extracted, and no
        # unread-layer record is made for it.
        result = run_dxf(lambda tmp: write_callouts(tmp / "c-ok.dxf", ["310UB46.2"]))
        assert result.summary["members_extracted"] == 1
        assert UNRECOGNISED_TEXT_LAYER not in result.summary["dropped_by_reason"]

    def test_the_unread_callout_is_not_persisted_as_a_member_either(self, run_dxf):
        result = run_dxf(lambda tmp: write_unread_layer_text(tmp / "c.dxf", ["310UB46.2"]))
        assert result.recorder.rows_inserted_into("steel_members") == []
        assert result.payload["unmatched_sections"] == []


# ===========================================================================
# 6. CONNECTION EVIDENCE — extracted, not associated, or not read out.
# ===========================================================================
class TestConnectionDiscardsAreRecorded:

    CALLOUTS = [
        "4xM20 Gr8.8 End plate 16mm",   # readable, but no member within reach
        "WELD ALL ROUND",               # passes the gate, no weld readable in it
        "SEE NOTE 4",                   # states none of the accepted shapes
    ]

    def test_each_connection_discard_carries_its_own_reason(self, run_dxf):
        result = run_dxf(lambda tmp: write_connection_callouts(tmp / "f.dxf", self.CALLOUTS))
        assert result.summary["dropped_by_reason"] == {
            NO_MEMBER_WITHIN_THRESHOLD: 2,
            NO_CHILD_EVIDENCE_PARSED: 1,
            UNSUPPORTED_CALLOUT_SHAPE: 1,
        }

    def test_the_three_distinctions_are_kept_apart(self, modules, run_dxf):
        result = run_dxf(lambda tmp: write_connection_callouts(tmp / "f.dxf", self.CALLOUTS))
        accounting = modules.drop.DXFDropAccounting()
        for record in result.summary["dropped_evidence"]:
            accounting.record(record["kind"], record["reason"], record["context"])
        assert accounting.by_category() == {
            modules.drop.CATEGORY_NOT_ASSOCIATED: 2,
            modules.drop.CATEGORY_NOT_EXTRACTED: 1,
            modules.drop.CATEGORY_UNSUPPORTED_BY_DESIGN: 1,
        }

    def test_the_records_name_what_was_discarded(self, run_dxf):
        result = run_dxf(lambda tmp: write_connection_callouts(tmp / "f.dxf", self.CALLOUTS))
        contexts = {
            record["reason"]: record["context"] for record in result.summary["dropped_evidence"]
        }
        assert contexts[UNSUPPORTED_CALLOUT_SHAPE] == "SEE NOTE 4"
        assert contexts[NO_CHILD_EVIDENCE_PARSED] == "WELD ALL ROUND"
        assert contexts[NO_MEMBER_WITHIN_THRESHOLD].startswith("connection at (")

    def test_the_connections_are_still_extracted_exactly_as_before(self, run_dxf):
        # Accounting is not reinterpretation: every connection the reader always
        # extracted is still extracted, and the member is still read.
        result = run_dxf(lambda tmp: write_connection_callouts(tmp / "f.dxf", self.CALLOUTS))
        assert result.summary["connections_extracted"] == 2
        assert result.summary["members_extracted"] == 1
        assert len(result.recorder.rows_inserted_into("connections")) == 2
        assert result.payload["total_connections"] == 2

    def test_no_member_link_is_invented_for_an_unassociated_connection(self, run_dxf):
        result = run_dxf(lambda tmp: write_connection_callouts(tmp / "f.dxf", self.CALLOUTS))
        assert result.recorder.rows_inserted_into("connection_members") == []

    def test_connection_geometry_alone_is_not_reported_as_a_discard(self, run_dxf):
        # Hole circles and leaders are shapes this reader has never read and has
        # never claimed to read; reporting them would be a false "not extracted".
        result = run_dxf(write_connection_symbols_only)
        assert result.summary["dropped_evidence_count"] == 0
        assert result.summary["connections_extracted"] == 0
        assert result.payload["warnings"] == []

    def test_a_fully_readable_connection_produces_no_accounting(self, run_dxf):
        def write(tmp):
            doc = _new_doc(GEOMETRY_LAYER, TEXT_LAYER, CONNECTION_LAYER)
            msp = doc.modelspace()
            msp.add_line((0, 0), (LINE_LENGTH, 0), dxfattribs={"layer": GEOMETRY_LAYER})
            msp.add_text("310UB46.2", height=2.5,
                         dxfattribs={"layer": TEXT_LAYER, "insert": (0, 500.0)})
            msp.add_text("4xM20 Gr8.8 End plate 16mm", height=2.5,
                         dxfattribs={"layer": CONNECTION_LAYER, "insert": (0, 1200.0)})
            doc.saveas(str(tmp / "conn-ok.dxf"))
            return tmp / "conn-ok.dxf"

        result = run_dxf(write)
        assert result.summary["connections_extracted"] == 1
        assert result.summary["dropped_evidence_count"] == 0
        assert len(result.recorder.rows_inserted_into("connection_members")) == 1


# ===========================================================================
# 7. FIXTURE E — a valid drawing: nothing unexpected, and no accounting at all.
# ===========================================================================
class TestACleanExtractionPersistsNoAccounting:

    def test_the_extraction_is_exactly_what_it_always_was(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "e.dxf", ["310UB46.2"]))
        assert result.summary["members_extracted"] == 1
        assert result.summary["unique_sections"] == 1
        assert result.summary["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        member = result.members[0]
        assert member["mark"] == "M1"
        assert member["section_name"] == "310UB46.2"
        assert member["section_name_raw"] == "310UB46.2"
        assert member["section_resolution"] == "EXACT"
        assert member["length_mm"] == LINE_LENGTH
        assert member["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        # Still no invented grade on this path.
        assert member["grade"] is None

    def test_no_discard_is_reported_and_none_is_persisted(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "e.dxf", ["310UB46.2"]))
        assert result.summary["dropped_evidence_count"] == 0
        assert result.summary["dropped_by_reason"] == {}
        assert result.summary["dropped_evidence"] == []
        assert result.payload["warnings"] == []
        assert result.payload["unmatched_sections"] == []

    def test_the_accounting_adds_no_column_to_the_persisted_row(self, modules, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "e.dxf", ["310UB46.2"]))
        assert set(result.members[0]) == {
            "project_id", "mark", "section_name", "section_name_raw", "length_mm",
            "grade", "quantity", "confidence", "source_layer", "section_resolution",
            "section_substituted_candidate", "reference_data_identity",
            "section_family", "weight_per_metre", "total_weight_kg",
        }


# ===========================================================================
# 8. FIXTURE D — the SteelSpec writer's own DXF, read back. The round trip is
#    ACCOUNTED, and extraction is deliberately NOT made to succeed.
# ===========================================================================
class TestTheWriterRoundTripIsAccounted:

    def test_the_real_writer_produces_polylines_and_a_title_block_on_layer_0(
        self, writer_dxf
    ):
        doc = ezdxf.readfile(writer_dxf)
        kinds = {entity.dxftype() for entity in doc.modelspace()}
        assert "LWPOLYLINE" in kinds
        assert "LINE" not in kinds
        assert all(entity.dxf.layer == WRITER_LAYER for entity in doc.modelspace())
        assert any(
            entity.dxftype() == "TEXT" and entity.dxf.text == "SECTION: 250PFC"
            for entity in doc.modelspace()
        )

    def test_the_reader_extracts_nothing_from_its_own_writer_s_output(self, run_dxf,
                                                                     writer_dxf):
        # Deliberately NOT fixed by redesigning the writer or the reader: this
        # milestone makes the condition visible, not successful.
        result = run_dxf(lambda tmp: writer_dxf, project_id="j11-writer")
        assert result.summary["members_extracted"] == 0
        assert result.summary["total_weight_kg"] == 0
        assert result.recorder.rows_inserted_into("steel_members") == []

    def test_the_condition_is_accounted_rather_than_silently_zero(self, run_dxf,
                                                                  writer_dxf):
        result = run_dxf(lambda tmp: writer_dxf, project_id="j11-writer")
        # 3 polylines the writer drew + 1 title-block line stating a section this
        # reader's own vocabulary recognises. Pinned: a change in what the writer
        # emits shows up here on purpose.
        assert result.summary["dropped_by_reason"] == {
            UNSUPPORTED_SOURCE_GEOMETRY: 3, UNRECOGNISED_TEXT_LAYER: 1,
        }
        assert result.summary["dropped_evidence"][-1] == {
            "kind": "MEMBER_CALLOUT",
            "reason": UNRECOGNISED_TEXT_LAYER,
            "context": "SECTION: 250PFC on layer '0'",
        }

    def test_the_project_record_says_the_extraction_was_not_complete(self, run_dxf,
                                                                    writer_dxf):
        result = run_dxf(lambda tmp: writer_dxf, project_id="j11-writer")
        assert result.payload["warnings"][0] == (
            "DXF extraction: 4 drawing evidence items were not extracted."
        )
        # Every source-geometry line names the shape, never a length or a weight.
        assert "Geometry not read (drawn as something other than a LINE): 3" in (
            result.payload["warnings"][1]
        )
        assert "SECTION: 250PFC on layer '0'" in result.payload["warnings"][2]

    def test_the_report_from_the_writer_s_own_output_discloses_it(self, run_and_report,
                                                                  writer_dxf):
        result = run_and_report(lambda tmp: writer_dxf, project_id="j11-writer")
        assert "0 MEMBERS" in result.text
        assert "Extraction not complete." in result.text
        assert "DXF extraction: 4 drawing evidence items were not extracted." in result.text


# ===========================================================================
# 9. PERSISTENCE — the accounting survives the existing project path, with no
#    schema change of any kind.
# ===========================================================================
class TestTheAccountingIsPersistedThroughTheExistingPath:

    def test_the_project_update_is_scoped_to_this_project(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "p.dxf", ["310UB46.2"],
                                                    with_lines=False),
                         project_id="j11-scope")
        updates = [call for call in result.recorder.calls if call["table"] == "projects"]
        assert updates[0]["filters"] == [("id", "j11-scope")]

    def test_the_accounting_travels_in_the_existing_columns_only(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "p.dxf", ["310UB46.2"],
                                                    with_lines=False))
        assert set(result.payload) == PROJECT_PAYLOAD_KEYS

    def test_no_new_table_is_written_for_the_accounting(self, run_dxf):
        result = run_dxf(lambda tmp: write_connection_callouts(
            tmp / "p-conn.dxf", TestConnectionDiscardsAreRecorded.CALLOUTS,
        ))
        tables = {call["table"] for call in result.recorder.calls}
        assert tables <= TABLES_THE_DXF_PATH_MAY_WRITE, sorted(tables)
        assert not [name for name in tables if "drop" in name or "account" in name]

    def test_the_persisted_warnings_are_the_module_s_own_lines(self, modules, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "p.dxf",
                                                    ["310UB46.2", "310UB40"],
                                                    with_lines=False))
        assert result.payload["warnings"] == [
            "DXF extraction: 2 drawing evidence items were not extracted.",
            "Section callouts not extracted (no matching line found): 2"
            " — 310UB46.2, 310UB40",
        ]
        # ...which is exactly what the collector produces for the same records,
        # read back out of the summary: no second counting rule anywhere.
        rebuilt = modules.drop.DXFDropAccounting()
        for record in result.summary["dropped_evidence"]:
            rebuilt.record(record["kind"], record["reason"], record["context"])
        assert rebuilt.warnings() == result.payload["warnings"]

    def test_the_summary_counts_agree_with_each_other_and_with_the_records(self, run_dxf):
        result = run_dxf(lambda tmp: write_connection_callouts(
            tmp / "p-count.dxf", TestConnectionDiscardsAreRecorded.CALLOUTS,
        ))
        summary = result.summary
        assert summary["dropped_evidence_count"] == len(summary["dropped_evidence"])
        assert summary["dropped_evidence_count"] == sum(summary["dropped_by_reason"].values())

    def test_a_run_with_discards_still_persists_the_rows_it_did_read(self, run_dxf):
        # Persistence parity cuts both ways: the accounting is written ALONGSIDE
        # the extraction, never instead of it.
        result = run_dxf(lambda tmp: write_mixed_drawing(
            tmp / "p-mixed.dxf", paired=["310UB46.2"], unpaired=["250PFC"],
        ))
        assert result.payload["total_members"] == 1
        assert result.payload["total_weight_kg"] == EXACT_MEMBER_WEIGHT_KG
        # REVIEWED FOR MILESTONE J13 AND LEFT UNCHANGED. The status used to be a
        # constant; it is now derived from the persisted evidence (app/
        # validation/project_status.py), and this fixture is a case where the
        # derivation itself must refuse to say "done": the run discarded a
        # callout, so the extraction does not cover what the drawing states.
        # The derivation reads that through J11's OWN contract —
        # headline_from_warnings() on the line asserted next — rather than by
        # matching this text, so the accounting and the status cannot drift
        # apart.
        assert result.payload["status"] == "review"
        assert result.payload["warnings"][0].startswith("DXF extraction: 1")

    def test_an_unresolved_but_extracted_member_is_not_a_discard(self, run_dxf):
        # The distinction the accounting must not blur: a member the catalogue
        # did not answer for is EXTRACTED (and reported through
        # unmatched_sections, exactly as the PDF path does) — it is not evidence
        # this reader discarded, and the two must not be conflated.
        result = run_dxf(lambda tmp: write_callouts(tmp / "p-none.dxf", ["360UB50"]))
        assert result.summary["members_extracted"] == 1
        assert result.summary["dropped_evidence_count"] == 0
        assert result.payload["unmatched_sections"] == ["360UB50"]
        assert result.payload["warnings"] == []

    def test_the_summary_keeps_the_keys_the_pipeline_reads(self, run_dxf):
        result = run_dxf(lambda tmp: write_callouts(tmp / "p-keys.dxf", ["310UB46.2"]))
        # The pre-existing four keys are untouched in name and meaning; the three
        # accounting keys were added to them (the deliberate update J11 makes).
        assert {
            "members_extracted", "unique_sections", "connections_extracted",
            "total_weight_kg",
        } <= set(result.summary)
        assert {
            "dropped_evidence_count", "dropped_by_reason", "dropped_evidence",
        } <= set(result.summary)


# ===========================================================================
# 10. THE REPORT — the disclosure, the reasons, and the qualified totals.
# ===========================================================================
def _drop_fixture(tmp):
    """A partly readable drawing: one member extracted, one callout discarded."""
    return write_mixed_drawing(tmp / "r.dxf", paired=["310UB46.2"],
                               unpaired=["310UB40"])


class TestTheReportDisclosesWhatWasNotExtracted:

    def test_the_report_says_the_extraction_was_not_complete(self, run_and_report):
        result = run_and_report(_drop_fixture)
        assert "Extraction not complete." in result.text

    def test_the_disclosure_sits_beside_the_figures_before_the_schedule(self, run_and_report):
        result = run_and_report(_drop_fixture)
        assert result.text.index("Extraction not complete.") < result.text.index(
            "Steel Member Schedule"
        )

    def test_the_report_qualifies_the_totals_it_does_show(self, run_and_report):
        result = run_and_report(_drop_fixture)
        assert "The figures above cover only the members that were extracted." in result.text

    def test_the_report_names_the_reason_and_the_count(self, run_and_report):
        result = run_and_report(_drop_fixture)
        assert "DXF extraction: 1 drawing evidence item was not extracted." in result.text
        assert (
            "Section callouts not extracted (no matching line found): 1 — 310UB40"
            in result.text
        )

    def test_the_report_says_no_more_than_the_accounting_says(self, run_and_report):
        result = run_and_report(_drop_fixture)
        # Factual language only: the disclosure the report renders is the
        # persisted accounting verbatim, and none of it calls a discarded item an
        # error, a failure, or a defect in the drawing. (The data-quality
        # preamble's own "these are not necessarily errors" is report copy, not
        # accounting — J11 made that preamble neutral about its source so it stays
        # true now that it carries the reader's accounting rather than only the
        # validation findings.)
        for line in result.payload["warnings"]:
            assert line in result.text, "the report renders the persisted accounting verbatim"
            lowered = line.lower()
            for word in ("error", "fail", "invalid", "corrupt", "defect"):
                assert word not in lowered, (word, line)
        # And nothing in the report claims the drawing was unreadable or wrong.
        for phrase in ("Extraction failed", "ERROR:", "FAILED", "INVALID", "CORRUPT",
                       "invalid drawing", "corrupt"):
            assert phrase not in result.text, phrase

    def test_the_report_still_shows_the_members_it_did_extract(self, run_and_report):
        result = run_and_report(_drop_fixture)
        assert "310UB46.2" in result.text
        assert "1.4t" not in result.text  # nothing inflated by the missing member
        assert "0.14t" in result.text

    def test_the_report_renders_the_accounting_in_data_quality_notes(self, run_and_report):
        result = run_and_report(_drop_fixture)
        assert "DATA QUALITY NOTES" in result.text
        assert "DXF extraction:" in result.text.split("DATA QUALITY NOTES", 1)[1]

    def test_the_notes_claim_no_validation_authority_for_a_reader_s_accounting(
        self, run_and_report
    ):
        # The DXF reader recorded this, not the validation rules, and the report
        # must not attribute it to a validation system that never saw the file.
        result = run_and_report(_drop_fixture)
        assert "The following was recorded during extraction." in result.text
        assert "The validation system flagged" not in result.text

    def test_a_clean_extraction_renders_no_disclosure_at_all(self, run_and_report):
        result = run_and_report(lambda tmp: write_callouts(tmp / "clean.dxf", ["310UB46.2"]))
        assert result.summary["dropped_evidence_count"] == 0
        assert "Extraction not complete." not in result.text
        assert "DATA QUALITY NOTES" not in result.text

    def test_a_pdf_path_warning_never_renders_as_a_dxf_disclosure(self, modules,
                                                                  report_of):
        # The report finds the accounting by the accounting's own prefix, so a
        # PDF project's notes cannot be misreported as a DXF extraction failure.
        _, text = report_of(
            {"warnings": ["310UB40: the catalogue offered 310UB40.4 instead (unrelated)"],
             "unmatched_sections": ["250X90PFC"]},
            members=[],
        )
        assert "Extraction not complete." not in text
        assert "DATA QUALITY NOTES" in text
        assert "UNMATCHED SECTIONS" in text

    def test_a_report_of_nothing_but_discards_cannot_read_as_an_empty_drawing(self,
                                                                             run_and_report):
        result = run_and_report(lambda tmp: write_callouts(
            tmp / "nothing.dxf", ["310UB46.2"], with_lines=False,
        ))
        assert "0 MEMBERS" in result.text
        assert "Extraction not complete." in result.text
        assert "DXF extraction: 1 drawing evidence item was not extracted." in result.text
        assert "no matching line found" in result.text

    def test_the_report_qualifies_a_total_that_is_a_lower_bound(self, modules, run_and_report):
        # An unresolved member carries NO weight (None, never zero) and the
        # project total is the sum of what could be calculated — the same
        # lower-bound roll-up app/pipeline.py performs for the PDF path. J11 does
        # not change that arithmetic; it makes the reader able to see when the
        # schedule is a subset of the drawing.
        result = run_and_report(lambda tmp: write_mixed_drawing(
            tmp / "lower-bound.dxf", paired=["250X90PFC"], unpaired=["310UB46.2"],
        ))
        assert result.members[0]["total_weight_kg"] is None
        assert result.members[0]["weight_per_metre"] is None
        assert result.payload["total_weight_kg"] == 0
        assert "Extraction not complete." in result.text


# ===========================================================================
# 11. THE WHOLE FILE STAYS IN PROCESS
# ===========================================================================
class TestNoNetwork:

    def test_every_guard_is_live(self, modules):
        for name in ("dxf", "matcher", "report", "main"):
            client = getattr(modules, name).supabase
            assert type(client).__name__ == "_NetworkGuardClient", name
            with pytest.raises(AssertionError, match="may touch a live Supabase client"):
                client.table("steel_sections")

    def test_the_run_and_its_report_never_leave_the_process(self, run_and_report):
        result = run_and_report(lambda tmp: write_callouts(tmp / "net.dxf", ["310UB46.2"]))
        assert result.recorder.calls
        assert result.members
        assert result.text
