"""
THE DXF AUTHORITY BOUNDARY — the drawing states the section, the catalogue may
only confirm it.

WHAT THIS FILE PINS

app/drawing_reading/dxf_parser.py used to ask the reference catalogue
`matcher.match(matches[0])` and then write `section["name"]` into
`section_name` — the member's authoritative identity — and the same row's
`weight_per_metre` into `weight_per_metre`/`total_weight_kg`. That is fine for
an exact answer and WRONG for a substitution: when the catalogue's table
carries no row for a bare token, SectionMatcher's ".0"..".9" suffix fallback
answers with a DIFFERENT section, so the DXF path would have persisted

    310UB40  ->  section_name "310UB40.4", family "UB", 40.4 kg/m

for a member the drawing called 310UB40 — a different section's identity and a
different section's tonnage, straight into steel_members. `match()` reports
only the row, and a fallback row is indistinguishable from an exact one, which
is why the substitution was invisible to the parser.

THE RULE NOW: the drawn token is the identity. The catalogue may ENRICH a
member it actually answered for; where it answered with a different section,
that row is refused as identity AND as properties alike, and the member is held
for review. The parser asks `matcher.resolve(token)` — which reports the ROUTE
— and branches explicitly on EXACT / SUFFIX_FALLBACK / NONE. It never calls
match() to decide a section at all (asserted structurally below).

WHY THE CASES ARE ANCHORED TO REAL EVIDENCE

The captured live section table (/tmp/steelspec_live_steel_sections_218.json,
sha256 e66f179b... — the same 218-row capture the Milestone E1 tests and the
accepted CAD-authority boundary pin) really does behave this way:

    310UB46.2  EXACT            the drawn token IS a catalogue key
    310UB40    SUFFIX_FALLBACK  the table carries no 310UB40 row; the fallback
                                offers the 310UB40.4 row instead
    250X90PFC  NONE             the table carries no 250X90PFC row at all, and
                                production does NOT bridge 250X90PFC -> 250PFC

The three rows this file relies on are pinned verbatim and cross-checked
against the capture whenever it is present, so the regression is deterministic
with no /tmp dependency and still cannot drift from the real evidence.

HOW THE REAL MATCHER AND THE REAL PARSER ARE EXERCISED

Same verified approach as tests/test_real_world_production_section_matcher.py:
SectionMatcher.__init__ reads the MODULE-LEVEL `supabase` client, so that
module global is the single injection point; it is replaced (via monkeypatch,
undone automatically) with a deterministic double that answers the one query
__init__ makes. Everything downstream is untouched production code: the real
__init__ builds the real lookup index, the real match()/resolve() perform the
real exact-then-suffix resolution, and the fallback algorithm is NOT
reproduced here — the tests ask the matcher and observe what it returns.

The parser is then driven for real: a genuine DXF file is written with ezdxf,
read back by ezdxf through the production parse_dxf_and_save(), and the same
production module extracts the token with the matcher's own get_section_regex()
and persists through its own Supabase calls — which are answered by a recording
double, so no network is touched. There is no fake parser and no reimplemented
extraction anywhere in this file.

THE get_section_regex() COMPATIBILITY QUESTION

It does not block this milestone. The production SectionMatcher DOES expose
get_section_regex() (section_matcher.py), and the DXF path is therefore
exercisable end to end here — so the authority rule below is pinned against the
REAL parser rather than around a broken one. Nothing in this file changes or
works around that method; a test records that the parser obtains its regex from
the matcher it was given, so the extraction vocabulary stays the matcher's.

DEPENDENCIES ISOLATED (all at the repository boundary only)

  * Supabase client   -> the recording double (writes) and the index double
                         (matcher construction); no client call is made.
  * app.config env    -> os.environ[...] is read at IMPORT time
                         (app/config.py), so the production modules are
                         imported under a test-only configuration boundary with
                         a fake, JWT-shaped placeholder — the pattern
                         established at tests/test_review_ui_integration.py.
                         The environment is restored immediately, and every
                         module the import adds is removed at teardown, so the
                         pre-existing 11 SUPABASE_URL smoke-import failures are
                         neither fixed nor worsened.
  * database query    -> never issued; the doubles answer in-process.
  * ezdxf             -> the real library, against a real file in tmp_path.

SAFETY: on import, each production module's client global is additionally
replaced with a guard that raises on any use, so an unpatched dependency fails
loudly here instead of reaching the network. No network. No credentials.
tests/data is not touched — the capture is read from /tmp.
"""

import ast
import copy
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

import ezdxf
import pytest

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# The captured live evidence this file is anchored to.
# ---------------------------------------------------------------------------
SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
SNAPSHOT_ROW_COUNT = 218

# The deliberately fake, well-formed configuration boundary. supabase-py
# validates the key SHAPE at import, so the placeholder is JWT-shaped but is
# not a credential and is never sent anywhere (both clients are replaced before
# any use). Same values as tests/test_real_world_production_section_matcher.py.
TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)

# ---------------------------------------------------------------------------
# The three rows this file relies on, pinned VERBATIM from the capture above
# (every key the live table carries, including the nulls) — the same pinned
# literals the accepted production-matcher and CAD-authority files use. The
# capture deliberately contains NO "310UB40" row and NO "250X90PFC" row: that
# absence is the premise of the whole file.
# ---------------------------------------------------------------------------
LIVE_310UB40_4 = {
    "name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
    "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
}
LIVE_310UB46_2 = {
    "name": "310UB46.2", "family": "UB", "depth": 307.0, "flange_width": 166.0,
    "flange_thickness": 11.8, "web_thickness": 6.7, "weight_per_metre": 46.2,
    "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
}
LIVE_250PFC = {
    "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
    "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
}

PINNED_ROWS = (LIVE_310UB40_4, LIVE_310UB46_2, LIVE_250PFC)
PINNED_NAMES = ("310UB40.4", "310UB46.2", "250PFC")

# The identity relation the matcher itself uses to key its index — read here as
# an EQUALITY relation only, never as a matching rule (it generates no candidate
# and applies no fallback), so a comparison made with it can only ever be
# stricter than the matcher's own.
def _normalise(name):
    return re.sub(r"\s+", "", str(name).strip().upper())


def _pinned_index():
    return [copy.deepcopy(row) for row in PINNED_ROWS]


def _read_capture():
    """The captured rows, or an honest skip when the capture is not present.
    Read-only: the snapshot is never written to or modified."""
    if not SNAPSHOT_PATH.exists():
        pytest.skip(
            f"the captured live section evidence is not present at {SNAPSHOT_PATH} — "
            "the full-capture assertions require it (the pinned-row assertions still run)"
        )
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256, (
        "the capture at /tmp does not match the sha256 this file (and the Milestone E1 tests) "
        "pinned — refusing to assert against unverified evidence"
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# The repository-boundary doubles.
# ---------------------------------------------------------------------------
class _FakeExecuteResult:
    """The shape the production code reads: result.data."""

    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Answers SectionMatcher.__init__'s one query and records that it happened."""

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
    """The deterministic index double standing in for the matcher's client."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _RecordingQuery:
    """
    The parser's OWN Supabase calls: insert()/update().eq().execute(). Every
    call is recorded and answered in-process, so a test can read exactly which
    rows the production parser tried to persist — and prove no network was
    touched, because this double has no network code at all.
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

    # convenience accessors for assertions
    @property
    def inserted_rows(self):
        return self._client.inserted.get(self._table, [])


class _RecordingSupabaseClient:
    """The double standing in for the parser's module-level client."""

    def __init__(self):
        self.calls = []
        self.inserted = {}

    def table(self, name):
        return _RecordingQuery(self, name)

    def rows_inserted_into(self, table):
        return self.inserted.get(table, [])


class _NetworkGuardClient:
    """
    Installed on each production module for the duration of this file so that an
    UNPATCHED dependency fails loudly here instead of reaching the network. It
    exists to make 'no network' structural, not merely intended.
    """

    def table(self, name):  # pragma: no cover - only reached on a test bug
        raise AssertionError(
            f"no test in this file may touch a live Supabase client (asked for table "
            f"{name!r}); build the dependency through the fixtures so the repository "
            "boundary is replaced first."
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def modules():
    """
    The REAL production modules — app.engineering_data.section_matcher and
    app.drawing_reading.dxf_parser — imported once under a test-only
    configuration boundary, with each module's client global replaced by the
    network guard.

    app/config.py reads os.environ[...] at import time, so the placeholder
    environment must exist for the import to happen at all. The environment is
    restored immediately afterwards (this file leaves no configuration behind),
    and every module this import adds to sys.modules is removed at teardown so
    the pre-existing SUPABASE_URL smoke-import baseline is unchanged.
    """
    saved = {key: os.environ.get(key) for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        matcher_module = importlib.import_module("app.engineering_data.section_matcher")
        parser_module = importlib.import_module("app.drawing_reading.dxf_parser")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_matcher_client = matcher_module.supabase
    real_parser_client = parser_module.supabase
    matcher_module.supabase = _NetworkGuardClient()
    parser_module.supabase = _NetworkGuardClient()
    try:
        yield matcher_module, parser_module
    finally:
        matcher_module.supabase = real_matcher_client
        parser_module.supabase = real_parser_client
        # Only app-prefixed modules are removed: the C extensions this suite
        # imports (cadquery/numpy/ezdxf) must survive for the rest of the run.
        for name in set(sys.modules) - before:
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


@pytest.fixture()
def matcher_module(modules):
    return modules[0]


@pytest.fixture()
def parser_module(modules):
    return modules[1]


@pytest.fixture()
def build_real_matcher(matcher_module, monkeypatch):
    """
    Returns build(rows) -> (matcher, double): a REAL SectionMatcher whose only
    external dependency has been replaced at the repository boundary. The
    production module is not modified — monkeypatch restores the module global
    after each test.
    """

    def build(rows):
        double = _FakeSupabaseClient(rows)
        monkeypatch.setattr(matcher_module, "supabase", double)
        matcher = matcher_module.SectionMatcher()
        return matcher, double

    return build


@pytest.fixture()
def real_matcher(build_real_matcher):
    """The production matcher over the pinned real rows."""
    matcher, _ = build_real_matcher(_pinned_index())
    return matcher


@pytest.fixture()
def recorder(parser_module, monkeypatch):
    """The parser's own client, replaced by a recording, in-process double."""
    double = _RecordingSupabaseClient()
    monkeypatch.setattr(parser_module, "supabase", double)
    return double


@pytest.fixture()
def pinned_index():
    return _pinned_index()


@pytest.fixture()
def captured_rows():
    return _read_capture()


@pytest.fixture()
def captured_by_name(captured_rows):
    return {row["name"]: row for row in captured_rows}


# ---------------------------------------------------------------------------
# A REAL DXF, read back by the REAL parser.
# ---------------------------------------------------------------------------
DXF_LINE_LENGTH = 3000.0
DXF_GEOMETRY_LAYER = "S-BEAM"
DXF_TEXT_LAYER = "S-TEXT"
# The layer the production parser classifies as a connection (see
# CONNECTION_LAYER_PATTERNS): only text on one of these becomes a connection.
DXF_CONNECTION_LAYER = "S-CONN"


def write_dxf(path, callouts):
    """
    Writes a genuine DXF carrying one steel LINE and one TEXT callout per
    entry, on the steel/annotation layers the parser classifies — each text
    sitting next to its own line, close enough for the production
    text-to-geometry association to pair them one-to-one. Returns the path.
    """
    doc = ezdxf.new("R2010")
    doc.layers.add(DXF_GEOMETRY_LAYER)
    doc.layers.add(DXF_TEXT_LAYER)
    msp = doc.modelspace()
    for index, callout in enumerate(callouts):
        base_y = index * 4000.0
        msp.add_line((0, base_y), (DXF_LINE_LENGTH, base_y), dxfattribs={"layer": DXF_GEOMETRY_LAYER})
        msp.add_text(
            callout, height=2.5,
            dxfattribs={"layer": DXF_TEXT_LAYER, "insert": (0, base_y + 500.0)},
        )
    doc.saveas(str(path))
    return str(path)


def write_connection_dxf(path, callouts, member_tokens=("310UB46.2",)):
    """
    Writes a genuine DXF carrying one steel LINE, one member annotation per
    token, and one CONNECTION-layer TEXT callout per entry — the layer the
    production parser classifies as a connection, which is the only way a plate
    can reach `connection_plates` through this parser.
    """
    doc = ezdxf.new("R2010")
    for layer in (DXF_GEOMETRY_LAYER, DXF_TEXT_LAYER, DXF_CONNECTION_LAYER):
        doc.layers.add(layer)
    msp = doc.modelspace()
    msp.add_line((0, 0), (DXF_LINE_LENGTH, 0), dxfattribs={"layer": DXF_GEOMETRY_LAYER})
    for index, token in enumerate(member_tokens):
        msp.add_text(
            token, height=2.5,
            dxfattribs={"layer": DXF_TEXT_LAYER, "insert": (0, 500.0 + index * 400.0)},
        )
    for index, callout in enumerate(callouts):
        msp.add_text(
            callout, height=2.5,
            dxfattribs={
                "layer": DXF_CONNECTION_LAYER,
                "insert": (0, 2000.0 + index * 400.0),
            },
        )
    doc.saveas(str(path))
    return str(path)


@pytest.fixture()
def run_dxf(tmp_path, parser_module, recorder):
    """Runs the production parser over a genuinely written DXF."""
    counter = {"n": 0}

    def run(callouts, matcher, project_id="project-dxf-authority"):
        counter["n"] += 1
        path = write_dxf(tmp_path / f"drawing-{counter['n']}.dxf", callouts)
        summary = parser_module.parse_dxf_and_save(path, project_id, matcher=matcher)
        return summary, recorder

    return run


@pytest.fixture()
def run_connection_dxf(tmp_path, parser_module, recorder):
    """Runs the production parser over a DXF whose callouts are CONNECTIONS."""
    counter = {"n": 0}

    def run(callouts, matcher, project_id="project-dxf-plates"):
        counter["n"] += 1
        path = write_connection_dxf(tmp_path / f"connections-{counter['n']}.dxf", callouts)
        summary = parser_module.parse_dxf_and_save(path, project_id, matcher=matcher)
        return summary, recorder

    return run


def persisted_members(recorder):
    return recorder.rows_inserted_into("steel_members")


def persisted_plates(recorder):
    return recorder.rows_inserted_into("connection_plates")


# ===========================================================================
# The parser uses the matcher's OWN resolution vocabulary — no local copy
# ===========================================================================
class TestTheParserUsesTheMatchersOwnVocabulary:

    def test_the_three_resolutions_are_the_matchers_own_constants(
        self, parser_module, matcher_module
    ):
        # Imported, not re-spelled: the parser cannot drift from the matcher's
        # vocabulary because there is no second definition to drift from.
        assert parser_module.RESOLUTION_EXACT is matcher_module.RESOLUTION_EXACT
        assert (
            parser_module.RESOLUTION_SUFFIX_FALLBACK
            is matcher_module.RESOLUTION_SUFFIX_FALLBACK
        )
        assert parser_module.RESOLUTION_NONE is matcher_module.RESOLUTION_NONE

    def test_the_refusal_note_is_the_production_sections_one(
        self, parser_module, matcher_module
    ):
        # One definition of what a refused substitution is told to the
        # engineer, shared with the PDF production path — never paraphrased.
        from app.validation import rules
        assert parser_module.SUBSTITUTION_NOTE is rules.SUBSTITUTION_NOTE

    def test_the_boundary_asks_resolve_and_never_match(self, parser_module, real_matcher):
        # A spy matcher whose match() explodes if it is ever called: the
        # authority boundary must obtain its decision from the resolution
        # report alone, so the row can never speak for the drawing by itself.
        calls = []

        class _SpyMatcher:
            def __init__(self, inner):
                self._inner = inner

            def match(self, raw_name):  # pragma: no cover - must never run
                raise AssertionError(
                    "the DXF authority boundary called match() — the row alone cannot "
                    "show whether it is the drawn section or a substitution"
                )

            def resolve(self, raw_name):
                calls.append(raw_name)
                return self._inner.resolve(raw_name)

        spy = _SpyMatcher(real_matcher)
        decision = parser_module.resolve_drawn_section(spy, "310UB46.2")
        assert calls == ["310UB46.2"]
        assert decision.resolution == parser_module.RESOLUTION_EXACT
        assert decision.identity == "310UB46.2"

    def test_a_matcher_that_cannot_report_a_resolution_is_refused_loudly(
        self, parser_module
    ):
        class _MatchOnlyMatcher:
            def match(self, raw_name):
                return copy.deepcopy(LIVE_310UB46_2)

        with pytest.raises(AttributeError, match="reports HOW each token resolved"):
            parser_module.resolve_drawn_section(_MatchOnlyMatcher(), "310UB46.2")

    def test_none_is_not_a_matcher(self, parser_module):
        with pytest.raises(AttributeError, match="reports HOW each token resolved"):
            parser_module.resolve_drawn_section(None, "310UB46.2")


# ===========================================================================
# TEST 1 — EXACT: identity preserved, enrichment intact, nothing else changed
# ===========================================================================
class TestExactIsUnchanged:

    def test_an_exact_token_keeps_its_identity_and_its_enrichment(
        self, parser_module, real_matcher
    ):
        decision = parser_module.resolve_drawn_section(real_matcher, "310UB46.2")
        assert decision.resolution == parser_module.RESOLUTION_EXACT
        assert decision.identity == "310UB46.2"
        assert decision.token_raw == "310UB46.2"
        assert decision.refused_candidate is None
        # The catalogue answered for the drawn section itself, so its standard
        # properties are available — the existing DXF design, intact.
        assert decision.enrichment_row["name"] == "310UB46.2"
        assert decision.enrichment_row["family"] == "UB"
        assert decision.enrichment_row["weight_per_metre"] == 46.2

    def test_an_exact_member_is_persisted_exactly_as_before(
        self, run_dxf, real_matcher
    ):
        summary, recorder = run_dxf(["310UB46.2"], real_matcher)
        members = persisted_members(recorder)
        assert len(members) == 1
        member = members[0]
        assert member["section_name"] == "310UB46.2"
        assert member["section_name_raw"] == "310UB46.2"
        assert member["section_family"] == "UB"
        assert member["length_mm"] == 3000
        assert member["weight_per_metre"] == 46.2
        assert member["total_weight_kg"] == round(3.0 * 46.2, 2) == 138.6
        assert member["quantity"] == 1
        assert member["source_layer"] == DXF_GEOMETRY_LAYER
        # No refusal vocabulary is attached to an exact member at all.
        assert "review_status" not in member
        assert "notes" not in member
        assert summary["members_extracted"] == 1
        assert summary["unique_sections"] == 1
        assert summary["total_weight_kg"] == 138.6

    def test_the_project_roll_up_is_the_real_member_s_weight(self, run_dxf, real_matcher):
        _, recorder = run_dxf(["310UB46.2"], real_matcher)
        updates = [call for call in recorder.calls if call["table"] == "projects"]
        assert len(updates) == 1
        assert updates[0]["payload"]["total_weight_kg"] == 138.6
        assert updates[0]["payload"]["total_weight_tonnes"] == round(138.6 / 1000, 3)
        # REVIEWED FOR MILESTONE J13 AND LEFT UNCHANGED — but no longer for the
        # reason it was written. This value used to be the constant both
        # extraction paths wrote; it is now DERIVED (app/validation/
        # project_status.py) from the evidence this run persisted, and the
        # derived answer here is the same. The reason is the contract the test
        # immediately above pins: an exact member carries NO review_status key
        # at all, so this reader states no review state for it, and a statement
        # this path never made cannot prove completeness. Absence fails closed.
        # A DXF project can therefore only reach "done" once this path states
        # its review state explicitly, which would change the extraction output
        # and is a separate milestone — not an oversight here.
        assert updates[0]["payload"]["status"] == "review"

    def test_a_differently_spelled_exact_token_keeps_the_existing_behaviour(
        self, parser_module, run_dxf, real_matcher
    ):
        # The catalogue's own spelling is used for an exact hit, exactly as this
        # parser has always done — the drawn text stays recoverable verbatim.
        decision = parser_module.resolve_drawn_section(real_matcher, "310ub46.2")
        assert decision.resolution == parser_module.RESOLUTION_EXACT
        assert decision.identity == "310UB46.2"

        _, recorder = run_dxf(["310ub46.2"], real_matcher)
        member = persisted_members(recorder)[0]
        assert member["section_name"] == "310UB46.2"
        assert member["section_name_raw"] == "310ub46.2"

    def test_an_exact_member_is_not_routed_to_review(self, run_dxf, real_matcher):
        _, recorder = run_dxf(["310UB46.2", "250PFC"], real_matcher)
        members = persisted_members(recorder)
        assert len(members) == 2
        assert all("review_status" not in member for member in members)
        assert {member["section_name"] for member in members} == {"310UB46.2", "250PFC"}


# ===========================================================================
# TEST 2 — SUFFIX_FALLBACK: the drawn token stays the identity
# ===========================================================================
class TestASuffixFallbackIsRefused:

    def test_the_decision_keeps_the_drawn_token_and_refuses_the_row(
        self, parser_module, real_matcher
    ):
        decision = parser_module.resolve_drawn_section(real_matcher, "310UB40")
        assert decision.resolution == parser_module.RESOLUTION_SUFFIX_FALLBACK
        # The identity the drawing states — never the catalogue's substitute.
        assert decision.identity == "310UB40"
        assert decision.identity != "310UB40.4"
        # The row the catalogue offered is refused as enrichment...
        assert decision.enrichment_row is None
        # ...and recorded ONLY so a human can see what was refused.
        assert decision.refused_candidate == "310UB40.4"

    def test_the_substituting_row_really_was_available(
        self, parser_module, real_matcher
    ):
        # The refusal is meaningful, not vacuous: the catalogue DOES answer this
        # token with a different, fully-populated row. That is precisely what
        # must not become the member.
        offered = real_matcher.match("310UB40")
        assert offered["name"] == "310UB40.4"
        assert offered["weight_per_metre"] == 40.4
        assert "310UB40" not in real_matcher._lookup

    def test_the_persisted_member_keeps_the_drawn_identity(
        self, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        members = persisted_members(recorder)
        # The member is PRESERVED — a refusal is not a silent deletion of a
        # member the drawing stated.
        assert len(members) == 1
        member = members[0]
        # Option C (J7): section_name is FK-BOUND to steel_sections(name), and
        # "310UB40" is not a catalogue key — the catalogue's answer for this
        # token is a DIFFERENT section (310UB40.4). The drawn identity is
        # therefore carried in section_name_raw, the column the FK does not
        # govern. This is a deliberate contract evolution, not a relaxation of
        # the refusal: the member still persists, and the substitute still does
        # not become its identity.
        assert member["section_name"] is None
        assert member["section_name_raw"] == "310UB40"

    def test_the_persisted_member_carries_no_substituted_properties(
        self, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        member = persisted_members(recorder)[0]
        # Not the substitute's family, not the substitute's unit weight, and NO
        # tonnage at all — not the substitute's, and not a fabricated zero.
        assert member["section_family"] is None
        assert member["weight_per_metre"] is None
        assert member["total_weight_kg"] is None

    def test_no_substituted_tonnage_reaches_the_project_total(self, run_dxf, real_matcher):
        summary, recorder = run_dxf(["310UB40"], real_matcher)
        # 3 m x 40.4 kg/m = 121.2 would be the substitute's figure. Nothing of
        # the sort is reported, and the sum does not crash on the None.
        assert summary["total_weight_kg"] == 0
        assert summary["total_weight_kg"] != round(3.0 * 40.4, 2)
        updates = [call for call in recorder.calls if call["table"] == "projects"]
        assert updates[0]["payload"]["total_weight_kg"] == 0
        assert updates[0]["payload"]["total_weight_tonnes"] == 0.0

    def test_the_member_is_held_for_review_with_the_production_refusal_note(
        self, parser_module, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        member = persisted_members(recorder)[0]
        assert member["review_status"] == "review_required"
        assert member["notes"] == parser_module.SUBSTITUTION_NOTE.format(
            token="310UB40", candidate="310UB40.4",
        )
        # The note names both identities, so the engineer can act on it.
        assert "310UB40" in member["notes"]
        assert "310UB40.4" in member["notes"]
        assert "has NOT been adopted as this member's identity" in member["notes"]

    def test_the_substituted_name_survives_only_as_the_refusal_provenance(
        self, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        member = persisted_members(recorder)[0]
        # Nothing on the persisted row claims the substitute as identity or as
        # a property; the only mention is the refusal note itself.
        for field in ("section_name", "section_name_raw", "section_family",
                      "weight_per_metre", "total_weight_kg"):
            assert "310UB40.4" not in str(member[field]), field
        assert "310UB40.4" in member["notes"]

    def test_a_refusal_does_not_disturb_a_neighbouring_exact_member(
        self, run_dxf, real_matcher
    ):
        summary, recorder = run_dxf(["310UB46.2", "310UB40"], real_matcher)
        members = persisted_members(recorder)
        assert len(members) == 2
        # Keyed by the DRAWN token — Option C (J7): the refused member's
        # section_name is NULL, so it can no longer be keyed by that column.
        by_name = {member["section_name_raw"]: member for member in members}
        assert set(by_name) == {"310UB46.2", "310UB40"}
        assert by_name["310UB46.2"]["section_name"] == "310UB46.2"
        assert by_name["310UB46.2"]["total_weight_kg"] == 138.6
        assert by_name["310UB40"]["section_name"] is None
        assert by_name["310UB40"]["total_weight_kg"] is None
        assert by_name["310UB40"]["review_status"] == "review_required"
        # The exact member's weight alone is the project total.
        assert summary["total_weight_kg"] == 138.6

    def test_the_refused_candidate_is_never_the_persisted_identity_of_anything(
        self, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        members = persisted_members(recorder)
        identities = {member["section_name"] for member in members}
        assert "310UB40.4" not in identities
        # Option C (J7): no catalogue identity at all is claimed for this
        # member, and the drawn token — not the substitute — is what survives in
        # the FK-free column.
        assert identities == {None}
        assert {member["section_name_raw"] for member in members} == {"310UB40"}
        assert "310UB40.4" not in {member["section_name_raw"] for member in members}

    def test_the_same_refusal_holds_for_another_real_trap(self, run_dxf, real_matcher):
        # A second, independent instance of the same defect shape: the table
        # carries 310UB46.2 but no bare 310UB46 row, so the fallback offers a
        # different section for a different token too.
        assert real_matcher.match("310UB46")["name"] == "310UB46.2"
        _, recorder = run_dxf(["310UB46"], real_matcher)
        member = persisted_members(recorder)[0]
        # Option C (J7): NULL in the FK-bound column, the drawn token in
        # section_name_raw — the same contract the first trap gets.
        assert member["section_name"] is None
        assert member["section_name_raw"] == "310UB46"
        assert member["weight_per_metre"] is None
        assert member["total_weight_kg"] is None
        assert member["review_status"] == "review_required"
        assert "310UB46.2" in member["notes"]


# ===========================================================================
# TEST 3 — NONE: nothing is substituted, nothing is fabricated, and — since
# Milestone J7 — nothing the drawing stated is discarded either.
# ===========================================================================
class TestNoneIsPersistedWithoutACatalogueClaim:

    def test_an_unknown_token_resolves_to_nothing_and_keeps_the_token(
        self, parser_module, real_matcher
    ):
        decision = parser_module.resolve_drawn_section(real_matcher, "250X90PFC")
        assert decision.resolution == parser_module.RESOLUTION_NONE
        assert decision.identity == "250X90PFC"
        assert decision.enrichment_row is None
        assert decision.refused_candidate is None

    def test_the_negative_is_meaningful_because_a_pfc_row_is_present(
        self, parser_module, real_matcher
    ):
        # The table does carry a PFC row and answers it exactly; the drawn token
        # still matches nothing. Production never bridges 250X90PFC -> 250PFC.
        assert "250PFC" in real_matcher._lookup
        assert real_matcher.resolve("250PFC").resolution == parser_module.RESOLUTION_EXACT
        assert real_matcher.resolve("250X90PFC").resolution == parser_module.RESOLUTION_NONE

    def test_the_similar_named_row_is_never_reached(self, parser_module, real_matcher):
        decision = parser_module.resolve_drawn_section(real_matcher, "250X90PFC")
        assert decision.identity != "250PFC"
        assert decision.enrichment_row is None

    def test_an_unmatched_callout_is_persisted_without_a_catalogue_claim(
        self, run_dxf, real_matcher
    ):
        # SUPERSEDED CONTRACT, deliberately changed by Milestone J7 (Option C).
        # This parser used to `continue` on a NONE resolution, so a member the
        # drawing stated vanished from the schedule entirely. It is now
        # persisted — with NO catalogue claim of any kind — and the summary
        # counts it as an extracted member while counting no catalogue section.
        summary, recorder = run_dxf(["250X90PFC"], real_matcher)
        members = persisted_members(recorder)
        assert len(members) == 1
        member = members[0]
        assert member["section_name"] is None
        # "90PFC", not "250X90PFC": the drawn token preserved here is the one
        # the matcher's own regex extracts (documented by the neighbouring
        # regex test), exactly as it was when this value went into section_name.
        assert member["section_name_raw"] == "90PFC"
        assert member["section_resolution"] == "NONE"
        assert member["section_substituted_candidate"] is None
        assert summary["members_extracted"] == 1
        assert summary["unique_sections"] == 0
        assert summary["total_weight_kg"] == 0

    def test_nothing_named_pfc_is_persisted_for_an_unmatched_token(
        self, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["250X90PFC"], real_matcher)
        member = persisted_members(recorder)[0]
        # No catalogue row — the near-named 250PFC included — is claimed,
        # substituted or referenced anywhere on the persisted row.
        assert member["section_name"] is None
        assert member["section_family"] is None
        assert member["weight_per_metre"] is None
        assert member["total_weight_kg"] is None
        assert "PFC" not in str(member.get("section_substituted_candidate"))

    def test_the_extraction_regex_is_the_matchers_own_and_truncates_this_shape(
        self, parser_module, real_matcher
    ):
        # Documented truth about the existing extraction vocabulary: the
        # matcher's own regex has patterns "\\d{2,3}PFC" and
        # "\\d+x\\d+x\\d+(RHS|SHS|EA|UA)", so a 250X90PFC callout is matched as
        # "90PFC" — a token that also resolves to nothing. Either way no
        # substitution and no bridge to 250PFC can occur, and the regex is the
        # matcher's, not a copy invented here.
        regex = real_matcher.get_section_regex()
        assert regex.findall("250X90PFC") == ["90PFC"]
        assert real_matcher.resolve("90PFC").resolution == parser_module.RESOLUTION_NONE

    def test_a_mixed_drawing_keeps_only_the_identities_it_can_justify(
        self, run_dxf, real_matcher
    ):
        # Renamed by Milestone J7: the parser now keeps EVERY member the
        # drawing states (three), while still claiming a catalogue identity for
        # only the one it can justify (310UB46.2).
        summary, recorder = run_dxf(["310UB46.2", "250X90PFC", "310UB40"], real_matcher)
        members = persisted_members(recorder)
        assert {member["section_name"] for member in members} == {"310UB46.2", None}
        # The extracted tokens, verbatim ("250X90PFC" truncates to "90PFC").
        assert {member["section_name_raw"] for member in members} == {
            "310UB46.2", "90PFC", "310UB40",
        }
        assert summary["members_extracted"] == 3
        # ONE catalogue section, not two: unique_sections counts catalogue
        # identities, and the NULL of a refused/unresolved member is the
        # absence of one rather than a section in its own right.
        assert summary["unique_sections"] == 1
        assert summary["total_weight_kg"] == 138.6


# ===========================================================================
# TEST 4 — the REAL production matcher was exercised, not a copy of it
# ===========================================================================
class TestTheRealMatcherWasExercised:

    def test_the_matcher_under_test_is_the_production_class(
        self, real_matcher, matcher_module
    ):
        assert type(real_matcher) is matcher_module.SectionMatcher
        assert type(real_matcher).__module__ == "app.engineering_data.section_matcher"

    def test_the_index_was_built_by_the_real_constructor_from_the_supplied_rows(
        self, build_real_matcher, pinned_index
    ):
        matcher, double = build_real_matcher(pinned_index)
        assert double.queries == [("steel_sections", "*")]
        assert double.executed == 1
        assert set(matcher._lookup) == set(PINNED_NAMES)

    def test_resolving_issues_no_additional_query(self, build_real_matcher, pinned_index):
        matcher, double = build_real_matcher(pinned_index)
        for token in ("310UB40", "310UB46.2", "250X90PFC"):
            matcher.resolve(token)
        assert double.executed == 1

    def test_the_parser_sees_the_matcher_it_was_given(self, run_dxf, build_real_matcher):
        # The parser's section decision comes from the matcher passed in — the
        # same object the boundary tests use — and it takes its extraction regex
        # from that matcher too.
        matcher, _ = build_real_matcher(_pinned_index())
        seen = []
        original = matcher.get_section_regex

        def spy():
            seen.append(True)
            return original()

        matcher.get_section_regex = spy
        summary, recorder = run_dxf(["310UB46.2"], matcher)
        assert seen == [True]
        assert persisted_members(recorder)[0]["section_name"] == "310UB46.2"

    def test_the_fallback_route_is_contingent_on_the_dotted_row_existing(
        self, build_real_matcher, matcher_module
    ):
        # The route reported is the one the index actually took: remove the
        # dotted row and the same token resolves to nothing at all.
        without_dotted, _ = build_real_matcher([copy.deepcopy(LIVE_310UB46_2)])
        with_dotted, _ = build_real_matcher(_pinned_index())
        assert (
            without_dotted.resolve("310UB40").resolution == matcher_module.RESOLUTION_NONE
        )
        assert (
            with_dotted.resolve("310UB40").resolution
            == matcher_module.RESOLUTION_SUFFIX_FALLBACK
        )


# ===========================================================================
# TEST 4b — the plate payload states what the callout stated, and nothing else
# ===========================================================================
class TestThePlatePayloadStatesOnlyWhatTheCalloutStated:
    """
    A plate callout states a TYPE and a THICKNESS in mm and nothing else: the
    production pattern matches exactly those two, and this parser extracts no
    plate material from anywhere. So every plate row this path writes states no
    grade — written BY KEY as None rather than left to a database default or
    filled with the `"grade": "300"` literal the module carried until Milestone
    J8B.

    The invariant is NO EVIDENCE -> NULL. It is deliberately NOT "every plate has
    a grade": a plate whose callout states no grade must persist none, and the
    value that used to be written there was a claim about a drawing that this
    parser never read.
    """

    # Every callout here carries the bolt callout as well, because the parser's
    # connection gate (pre-existing, unchanged by J8B) accepts a callout only if
    # it states M-bolts, the word "plate", or a weld — a bare "Gusset 10mm" is
    # not a connection to this parser at all and is out of this milestone's scope.
    @pytest.mark.parametrize("callout,plate_type,thickness", [
        ("4xM16 Gr8.8 End plate 16mm", "end_plate", 16.0),
        ("Base plate 20mm", "base_plate", 20.0),
        ("4xM20 Gr8.8 Gusset 10mm", "gusset", 10.0),
        ("4xM20 Gr8.8 Stiffener 8mm", "stiffener", 8.0),
        ("4xM16 Gr8.8 Cleat 6mm", "cleat", 6.0),
    ])
    def test_a_plate_states_its_type_and_thickness_and_no_grade(
        self, run_connection_dxf, real_matcher, callout, plate_type, thickness
    ):
        _, recorder = run_connection_dxf([callout], real_matcher)
        plates = persisted_plates(recorder)

        assert len(plates) == 1, callout
        plate = plates[0]
        assert plate["plate_type"] == plate_type
        assert plate["thickness"] == thickness, "an extracted thickness must survive"
        assert plate["grade"] is None
        assert "300" not in json.dumps(plate)

    def test_the_absence_is_stated_by_key_not_left_to_the_database(
        self, run_connection_dxf, real_matcher
    ):
        # An omitted key would be answered by a column default; the payload says
        # what it means, so the row does not depend on the schema's own opinion.
        _, recorder = run_connection_dxf(["End plate 12mm"], real_matcher)
        plate = persisted_plates(recorder)[0]

        assert "grade" in plate, "the column is left to the database rather than stated"
        assert plate["grade"] is None
        assert set(plate) == {"plate_type", "thickness", "grade", "connection_id"}

    def test_no_plate_ever_carries_the_removed_300_literal(
        self, run_connection_dxf, real_matcher
    ):
        _, recorder = run_connection_dxf(
            ["End plate 16mm", "Base plate 20mm", "4xM20 Gr8.8 Gusset 10mm"], real_matcher
        )
        plates = persisted_plates(recorder)

        assert len(plates) == 3, "one row per plate callout"
        assert {plate["grade"] for plate in plates} == {None}
        assert [plate["thickness"] for plate in plates] == [16.0, 20.0, 10.0]

    def test_the_grade_is_not_inherited_from_anything_else_in_the_callout(
        self, run_connection_dxf, real_matcher
    ):
        # The callout really does state a bolt grade (8.8) and the drawing really
        # does state a member section — so neither absence is hypothetical. Both
        # are non-plate evidence and neither may become a plate grade.
        _, recorder = run_connection_dxf(["4xM16 Gr8.8 End plate 16mm"], real_matcher)

        assert [b["bolt_grade"] for b in recorder.rows_inserted_into("bolt_groups")] == ["8.8"]
        assert [m["grade"] for m in persisted_members(recorder)] == [None]
        assert persisted_plates(recorder)[0]["grade"] is None

    def test_a_zero_mm_callout_is_not_persisted_as_a_measured_zero(
        self, run_connection_dxf, real_matcher
    ):
        # Zero is not a thickness any drawing stated, and a persisted 0.0 is read
        # downstream as a real dimension. The row keeps the type the callout did
        # state; only the invalid thickness is withheld — the same rule the PDF
        # path's extraction helper applies.
        _, recorder = run_connection_dxf(["End plate 0mm"], real_matcher)
        plate = persisted_plates(recorder)[0]

        assert plate["plate_type"] == "end_plate"
        assert plate["thickness"] is None
        assert plate["grade"] is None

    def test_a_callout_that_states_no_measurement_yields_no_plate_row(
        self, run_connection_dxf, real_matcher
    ):
        # This parser's whole plate vocabulary is "type + measurement in mm": a
        # callout that states no measurement offers it no plate to write. None is
        # invented for it — no row, no zero, and no grade.
        summary, recorder = run_connection_dxf(["End plate"], real_matcher)

        assert persisted_plates(recorder) == []
        assert summary["connections_extracted"] == 1


# ===========================================================================
# TEST 5 — no network, no credentials, no live client anywhere
# ===========================================================================
class TestNoNetwork:

    def test_the_parser_client_guard_is_live(self, modules):
        parser_module = modules[1]
        with pytest.raises(AssertionError, match="may touch a live Supabase client"):
            parser_module.supabase.table("steel_members")

    def test_the_matcher_client_guard_is_live(self, modules):
        matcher_module = modules[0]
        with pytest.raises(AssertionError, match="may touch a live Supabase client"):
            matcher_module.supabase.table("steel_sections")

    def test_the_whole_run_is_in_process(self, run_dxf, real_matcher):
        summary, recorder = run_dxf(["310UB46.2", "310UB40"], real_matcher)
        assert summary["members_extracted"] == 2
        # Every write went to the in-process double, and the double has no
        # network code at all.
        assert recorder.calls
        assert {call["table"] for call in recorder.calls} <= {
            "steel_members", "connections", "bolt_groups", "weld_details",
            "connection_plates", "connection_members", "projects",
        }

    def test_the_guards_are_not_the_live_client(self, modules):
        # Neither production module's client global is a real Supabase client:
        # both are this file's in-process guards while these tests run.
        for module in modules:
            assert type(module.supabase).__name__ == "_NetworkGuardClient"
            assert type(module.supabase).__module__ != "supabase"


# ===========================================================================
# The rule is STRUCTURAL — no list of names, no second lookup
# ===========================================================================
class TestTheRuleIsStructural:

    def _parser_ast(self):
        return ast.parse((REPO / "app" / "drawing_reading" / "dxf_parser.py").read_text())

    def test_the_parser_never_calls_match_to_decide_a_section(self):
        tree = self._parser_ast()
        match_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "match"
        ]
        # The only .match() left in the module is the layer-classification
        # regex's re.match — no matcher.match() anywhere.
        for call in match_calls:
            assert isinstance(call.func.value, ast.Name) and call.func.value.id == "re", (
                "the DXF parser still calls a matcher's match() to decide a section"
            )

    def test_the_boundary_obtains_and_calls_the_matchers_resolution_report(self):
        tree = self._parser_ast()
        boundary = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_drawn_section"
        )
        obtained = [
            node for node in ast.walk(boundary)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant) and node.args[1].value == "resolve"
        ]
        assert obtained, "resolve_drawn_section must obtain matcher.resolve"
        called = [
            node for node in ast.walk(boundary)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "resolve"
        ]
        assert called, "resolve_drawn_section must call the resolution report"

    def test_the_module_names_no_section_and_no_family(self):
        # Structural, not case-specific: the boundary must not carry a list of
        # section names or families to special-case. Docstrings are excluded —
        # they explain the rule and necessarily cite the real example.
        tree = self._parser_ast()
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
        for literal in literals:
            for name in ("310UB40", "310UB46", "200UC46", "250PFC", "250X90PFC"):
                assert name not in literal, (name, literal)

    def test_every_captured_section_resolves_exactly_and_keeps_its_identity(
        self, parser_module, build_real_matcher, captured_rows
    ):
        matcher, _ = build_real_matcher(captured_rows)
        for row in captured_rows:
            name = row["name"]
            decision = parser_module.resolve_drawn_section(matcher, name)
            assert decision.resolution == parser_module.RESOLUTION_EXACT, name
            assert _normalise(decision.identity) == _normalise(name), name
            assert decision.enrichment_row is not None

    def test_every_substitution_trap_in_the_live_catalogue_is_refused(
        self, parser_module, build_real_matcher, captured_rows
    ):
        matcher, _ = build_real_matcher(captured_rows)
        catalogue_identities = {_normalise(row["name"]) for row in captured_rows}

        traps = {}
        for row in captured_rows:
            name = row["name"]
            if "." not in name:
                continue
            token = name.split(".")[0]
            if _normalise(token) in catalogue_identities:
                continue
            substituted = matcher.match(token)
            if substituted is None:
                continue
            assert _normalise(substituted["name"]) != _normalise(token)
            traps[token] = substituted["name"]

        # Every one of these tokens would have been persisted as that
        # substituted name under the old behaviour.
        assert len(traps) == 35, (
            f"the live catalogue's substitution traps have changed: {sorted(traps)}"
        )
        assert traps["310UB40"] == "310UB40.4"
        assert traps["310UB46"] == "310UB46.2"
        assert traps["200UC46"] == "200UC46.2"

        for token, substituted_name in traps.items():
            decision = parser_module.resolve_drawn_section(matcher, token)
            assert decision.resolution == parser_module.RESOLUTION_SUFFIX_FALLBACK, token
            assert decision.identity == token, token
            assert _normalise(decision.identity) != _normalise(substituted_name), token
            assert decision.enrichment_row is None, token
            assert decision.refused_candidate == substituted_name, token

    def test_no_decision_ever_yields_an_identity_the_drawing_did_not_state(
        self, parser_module, build_real_matcher, captured_rows
    ):
        # The invariant, swept over every catalogue name and every trap token:
        # the identity is either the drawn token itself, or — only on an EXACT
        # resolution — the catalogue's own spelling of that SAME section.
        matcher, _ = build_real_matcher(captured_rows)
        catalogue_identities = {_normalise(row["name"]) for row in captured_rows}
        tokens = sorted(catalogue_identities) + sorted(
            row["name"].split(".")[0] for row in captured_rows if "." in row["name"]
        )
        checked = 0
        for token in tokens:
            decision = parser_module.resolve_drawn_section(matcher, token)
            checked += 1
            if decision.resolution == parser_module.RESOLUTION_EXACT:
                assert _normalise(decision.identity) == _normalise(token), token
            else:
                # The identity is the drawn token in the matcher's own canonical
                # form. Case and whitespace carry no section meaning (see
                # SectionMatch.drawing_token) and the spare spellings are the
                # catalogue's lowercase names, so the one permitted difference is
                # that canonicalisation — never a different section.
                assert decision.identity == _normalise(token), token
                assert _normalise(decision.identity) == _normalise(token), token
                assert decision.enrichment_row is None, token
                if decision.refused_candidate is not None:
                    assert (
                        _normalise(decision.refused_candidate) != _normalise(token)
                    ), token
        assert checked == len(tokens) > 200


# ===========================================================================
# The refusal cannot reach CAD geometry or fabrication
# ===========================================================================
class TestTheRefusalCannotReachGeometryOrFabrication:

    def test_the_dxf_module_imports_no_geometry_or_fabrication_code(self):
        # Structural: nothing in this module can build a solid or a drawing
        # artifact, so the only way a DXF-extracted member reaches geometry is
        # by being read back out of steel_members by another boundary.
        tree = ast.parse((REPO / "app" / "drawing_reading" / "dxf_parser.py").read_text())
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

    def test_the_returned_summary_carries_no_geometry(self, run_dxf, real_matcher):
        # The summary contract, updated DELIBERATELY by Milestone J11: it now
        # also states what was NOT read. The shape it must not lose is still the
        # one this test is named for — these are counts and discard records, not
        # drawing data, and no value here may be used to reconstruct a member.
        summary, _ = run_dxf(["310UB40"], real_matcher)
        assert set(summary) == {
            "members_extracted", "unique_sections", "connections_extracted",
            "total_weight_kg",
            "dropped_evidence_count", "dropped_by_reason", "dropped_evidence",
        }
        # A clean extraction REPORTS that it discarded nothing rather than
        # leaving the reader to assume it: every callout in this fixture is
        # paired with its own line, so any non-zero count here would mean the
        # accounting invented a discard.
        assert summary["dropped_evidence_count"] == 0
        assert summary["dropped_by_reason"] == {}
        assert summary["dropped_evidence"] == []

    def test_the_refused_member_is_refused_again_by_the_cad_authority_boundary(
        self, run_dxf, real_matcher
    ):
        # The end of the chain, using the already-accepted CAD boundary: the row
        # this parser persists for a fallback is refused there — first because
        # it is held for review, and then, with that flag cleared, by the CAD
        # authority rule itself. Under Option C (J7) the row carries section_name
        # NULL, so the refusal is now the boundary's "no matched section name is
        # recorded" rule rather than a name-mismatch rule. The substitute's name
        # does not reach the CAD boundary at all, which is a stronger guarantee
        # than the one this test previously asserted.
        from app.cad_engine.errors import GeometryValidationError
        from app.cad_engine.real_member_adapter import real_member_to_validated_member

        _, recorder = run_dxf(["310UB40"], real_matcher)
        member = persisted_members(recorder)[0]
        row = {
            "mark": member["mark"],
            "section_name": member["section_name"],
            "length_mm": member["length_mm"],
            "review_status": member["review_status"],
        }
        with pytest.raises(GeometryValidationError, match="review_required"):
            real_member_to_validated_member(row, real_matcher)

        row["review_status"] = "approved"
        with pytest.raises(GeometryValidationError) as excinfo:
            real_member_to_validated_member(row, real_matcher)
        assert "no matched section name" in str(excinfo.value)
        # The refused candidate is not even visible to the CAD boundary: Option C
        # keeps it out of section_name entirely, so it can never be mistaken
        # there for this member's identity.
        assert "310UB40.4" not in str(excinfo.value)

    def test_an_exact_member_is_not_refused_by_the_cad_boundary(self, run_dxf, real_matcher):
        from app.cad_engine.real_member_adapter import real_member_to_validated_member

        _, recorder = run_dxf(["310UB46.2"], real_matcher)
        member = persisted_members(recorder)[0]
        validated = real_member_to_validated_member({
            "mark": member["mark"],
            "section_name": member["section_name"],
            "length_mm": member["length_mm"],
        }, real_matcher)
        assert validated.section == "310UB46.2"
        assert validated.section_properties["name"] == "310UB46.2"


# ===========================================================================
# The refusal note is the production one, rendered identically
# ===========================================================================
class TestTheRefusalVocabularyIsTheProductionOne:

    def test_the_dxf_note_is_byte_identical_to_the_pdf_path_s_note(
        self, parser_module, run_dxf, real_matcher
    ):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        member = persisted_members(recorder)[0]
        # Exactly what app/validation/rules.py writes for the same token and
        # candidate on the PDF path — one sentence, one meaning, both paths.
        from app.validation.rules import SUBSTITUTION_NOTE
        assert member["notes"] == SUBSTITUTION_NOTE.format(
            token="310UB40", candidate="310UB40.4",
        )

    def test_the_refusal_uses_the_existing_review_vocabulary(self, run_dxf, real_matcher):
        _, recorder = run_dxf(["310UB40"], real_matcher)
        member = persisted_members(recorder)[0]
        # The same flag app/pipeline.py writes for a refused substitution, which
        # is also what the CAD boundary checks first.
        assert member["review_status"] == "review_required"

    def test_no_new_field_is_invented_on_the_persisted_row(self, run_dxf, real_matcher):
        _, recorder = run_dxf(["310UB46.2", "310UB40"], real_matcher)
        exact, fallback = persisted_members(recorder)
        # The refused row adds exactly the two columns the PDF path already
        # writes for a refused member — nothing else.
        assert set(fallback) - set(exact) == {"review_status", "notes"}
        assert set(exact) <= set(fallback)
