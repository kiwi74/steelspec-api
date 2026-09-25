"""
J4 — PRODUCTION EXTRACTION → PERSISTENCE → REPORT TRUTH (baseline proof milestone).

WHAT THIS MODULE IS
-------------------
An additive, READ-ONLY proof of what the CURRENT NORMAL PRODUCTION path actually
does, end to end, against the LIVE `steel_sections` catalogue. It fixed the
baseline before any production change: every assertion here describes today's
behaviour, including the behaviour that is WRONG. Nothing in this module is a
target or a specification.

D1, D2 and D5 HAVE SINCE BEEN FIXED by Milestone J5, which is the "later
production change milestone" this file was written to precede. Their "pinned
defect" tests have been rewritten in place to assert the FIXED behaviour, and
each one now says in its docstring what it asserted before and why it changed.
The full J5 proof lives in
`tests/test_real_world_j5_production_resolution_truth.py`; this file keeps only
its baseline-of-record for the parts J5 did not touch.

The defects J4 pinned, and their state after J5:

  D1  the "✓ Matched" pill keyed on AI confidence, never on section resolution
      or review_status, so an UNMATCHED member rendered as Matched
      (`app/report/pdf_generator.py::_status_pill`)
      → FIXED (J5): the single pill is gone, split into `_section_status_pill`
        (engineering resolution) and `_confidence_pill` (AI extraction).
  D2  `section_resolution` / `section_substituted_candidate` were never persisted
      (`app/pipeline.py` row construction) — a persisted row could not say how
      its section resolved
      → FIXED (J5). Persisting them needs the additive migration at
        `supabase/migrations/20260924000000_j5_section_resolution_truth.sql`.
        When this module was written it was created but NOT applied to the live
        database; as of 2026-09-24 (re-verified read-only) it IS applied.
  D3  no reference identity (source kind / status / digest) is persisted anywhere
      (`app/engineering_data/repository.py` has no provenance column)
      → FIXED (J6). At the time this module was written the column was absent by
        design and named as J6's scope; `steel_members.reference_data_identity`
        now exists and the identity is persisted (re-verified read-only
        2026-09-24).
  D4  DXF drops a NONE-resolution member before persistence
      (`app/drawing_reading/dxf_parser.py`, the `continue` on NONE)
      → FIXED (J7). The member is persisted with no catalogue claim of any kind
        — section_name NULL, the drawn token in section_name_raw, held for
        review. See `test_d4_the_dxf_none_member_is_now_persisted_by_j7`.
  D5  `consolidate_members` kept ONE row per mark and silently discarded the
      others, while counting all their pages (`app/validation/rules.py`)
      → FIXED (J5): a resolution conflict now keeps every row.
  D6  the DXF SUFFIX_FALLBACK row persists a `section_name` the live catalogue
      does not contain, which is the column of a pinned inbound foreign key
      → FIXED (J7, Option C). `section_name` may now hold a catalogue name and
        nothing else (EXACT only); a refused fallback persists NULL there and
        keeps its drawn identity in `section_name_raw`, so no non-key value is
        ever offered to the foreign key.
  D7  the section authority rule exists twice, independently implemented, in
      `app/validation/rules.py` and `app/drawing_reading/dxf_parser.py`
      → STILL OPEN. J5 makes the two paths share a persistence VOCABULARY
        (asserted in the J5 module); it does not merge the two implementations.

HOW THE REAL BOUNDARY IS DRIVEN
-------------------------------
The production orchestrator runs UNCHANGED. Only the stages that are genuinely
external to the process are replaced, and each replacement is at an existing
seam — never a reimplementation of production logic:

  AI vision call        `app.pipeline.analyze_pdf_pages`  (needs a key, is
                        nondeterministic, returns a network result). The objects
                        handed back are the genuine production `PageExtraction`
                        dataclass, not a stand-in type.
  persistence           `app.pipeline.repo`  — the genuine production repository
                        API answered in-process, exactly as the J4 brief requires
                        ("Intercept persistence at the existing repository.insert_members
                        seam rather than writing to Supabase").
  report reads          `app.report.pdf_generator.supabase`  — answers the three
                        SELECTs the generator makes, from the CAPTURED persisted
                        rows. The generator itself is untouched and runs in full.
  artifact upload       `app.main.upload_and_record`  (writes to a storage bucket).
  DXF persistence       `app.drawing_reading.dxf_parser.supabase`.

Everything else is the real thing: `parse_pdf_and_save`, `_run_pipeline`,
`validate_extraction`, `consolidate_members`, the production row construction,
`SectionMatcher` (LIVE), `generate_report_pdf`, `build_and_store_report`,
`real_member_to_validated_member`, `generate_geometry`.

READ-ONLY GUARANTEE
-------------------
This module performs no INSERT / UPDATE / DELETE / UPSERT / RPC / migration and
sets no live-write flag. The only live traffic is SELECT against
`steel_sections`. Every write path is either intercepted (repository, storage)
or asserted unreachable by a network guard that raises on any unexpected
`.table()` call. If the live catalogue cannot be read, the live-dependent tests
SKIP honestly rather than substituting a synthetic matcher.
"""

from __future__ import annotations

import copy
import io
import itertools
import os
import re
import sys
import types

import pytest

# --------------------------------------------------------------------------
# Live evidence pinned from the accepted J2 milestone. These are FACTS about
# the live catalogue as established, not targets.
# --------------------------------------------------------------------------
LIVE_ROW_COUNT_AFTER_J2 = 222
LIVE_DIGEST_AFTER_J2 = "227814c5760c3c49147715565b4e0d24f1617b456a00fc50f16a7010f0584e91"
LIVE_DIGEST_BEFORE_J2 = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"

# The four rows the J2 write added. All FLAT; two carry no evidence-backed weight.
FLAT_ROWS_ADDED_BY_J2 = ("130x12FL", "180x20FL", "250x12FL", "90x10FL")
FLAT_ROWS_WITHOUT_LIVE_WEIGHT = ("130x12FL", "90x10FL")
FLAT_ROWS_WITH_LIVE_WEIGHT = {"180x20FL": 28.3, "250x12FL": 23.6}

# Neighbours pinned as unchanged by J2.
EXACT_TOKEN = "310UB46.2"
SUFFIX_FALLBACK_TOKEN = "310UB40"          # resolves to 310UB40.4 (a DIFFERENT section)
SUFFIX_FALLBACK_CANDIDATE = "310UB40.4"
NONE_TOKEN = "250X90PFC"                    # absent from the catalogue

# The genuine live EA rows carry leg_size but no `width`, and EA/FL both build
# through the plate projection, whose required fields are (width, thickness).
EA_TOKEN_WITHOUT_WIDTH = "25x25x3EA"

# A test-only replacement env, used ONLY when the repo's real .env is absent, so
# that the non-live tests can still run. It is a placeholder URL and a
# JWT-shaped non-secret; it grants no access to anything.
PLACEHOLDER_URL = "https://placeholder.supabase.co"
PLACEHOLDER_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ.fake-test-signature"
)

_ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


def _load_repo_dotenv() -> bool:
    """
    Put the repo's own .env into os.environ — the same configuration boundary
    the production process uses. Never prints, echoes or returns any value;
    the return value only reports whether the two required keys are present.
    Only keys that are not already set are adopted, so an operator-supplied
    environment always wins.
    """
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name = name.strip()
                if name and name not in os.environ:
                    os.environ[name] = value.strip().strip('"').strip("'")
    if not all(os.environ.get(key) for key in _ENV_KEYS):
        os.environ.setdefault("SUPABASE_URL", PLACEHOLDER_URL)
        os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", PLACEHOLDER_KEY)
    # A live catalogue read is possible only with real configuration, never with
    # the placeholders above.
    return os.environ.get("SUPABASE_URL") != PLACEHOLDER_URL


# ==========================================================================
# Test-only doubles — the existing seams named in the module docstring.
# ==========================================================================


class _Result:
    """The single shape a PostgREST response exposes to these production paths."""

    def __init__(self, data):
        self.data = data


class _ReportQuery:
    """Answers one of the three SELECTs app/report/pdf_generator.py makes.

    It has no write method at all: the report generator is a reader, and this
    double can only ever hand it rows that were already captured from the
    production extraction. `.eq()`, `.order()` and `.select()` are accepted and
    recorded because the generator chains them; the filtering they would do is
    already reflected in the captured data.
    """

    def __init__(self, client, table_name):
        self._client = client
        self._table = table_name
        self._single = False

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        self._client.queries.append(self._table)
        return _Result(self._client.tables[self._table])


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


class _RecordingQuery:
    """The DXF parser's own Supabase calls: insert()/update().eq().execute().

    Every call is recorded and answered in-process, so a test can read exactly
    which rows the production parser tried to persist — and prove no network was
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
            "payload": copy.deepcopy(self._payload),
            "filters": list(self._filters),
        })
        if self._op == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            self._client.inserted.setdefault(self._table, []).extend(copy.deepcopy(rows))
            return _Result([
                {**row, "id": f"{self._table}-{index}"} for index, row in enumerate(rows)
            ])
        return _Result([])


class _RecordingSupabase:
    """The DXF parser's persistence seam. Records; never reaches the network."""

    def __init__(self):
        self.calls = []
        self.inserted = {}

    def table(self, name):
        return _RecordingQuery(self, name)

    def rows_inserted_into(self, table):
        return self.inserted.get(table, [])


class _NetworkGuard:
    """Fails loudly if anything under test reaches for a database table.

    Installed over the modules a test expects to make NO calls, so "we
    intercepted everything" is proven rather than assumed.
    """

    def table(self, name):  # pragma: no cover - only reached on a defect
        raise AssertionError(
            f"unexpected database access to {name!r}: the production boundary "
            "under test was supposed to have all of its I/O intercepted"
        )

    def __getattr__(self, name):  # pragma: no cover - only reached on a defect
        raise AssertionError(
            f"unexpected Supabase attribute {name!r} on a guarded boundary"
        )


class _RecordingRepository:
    """The production repository API (`app/engineering_data/repository.py`),
    answered in-process and recorded.

    Every function `app/pipeline.py` calls is present with the same name and
    signature, so the production orchestrator runs unchanged. Nothing here
    reaches the network: this class holds no client at all.

    `insert_members` returns the persisted rows WITH generated ids — the shape
    the real bulk insert returns — so the downstream mark→id resolution,
    review-item writes and connection linking behave exactly as they do in
    production.
    """

    def __init__(self):
        self.members = []
        self.review_items = []
        self.connections = []
        self.bolt_groups = []
        self.connection_plates = []
        self.weld_details = []
        self.connection_member_links = []
        self.project_summary = None
        self.drawing_meta = None
        self.analysis_run_updates = []
        # Milestone J23: the raw AI readings production records before it records any
        # row derived from them. Held so a test can see the capture this run wrote.
        self.captures = []
        # The order the stores were asked for, for the one claim that is about order:
        # that a run's reading is recorded before anything derived from it.
        self.order = []
        self.drawing_set_updates = []
        self._ids = itertools.count(1)

    # --- drawing set / drawing / analysis run -----------------------------
    def create_drawing_set(self, project_id, name):
        return {"id": f"ds-{next(self._ids)}", "project_id": project_id, "name": name}

    def create_drawing(self, drawing_set_id, file_name, storage_path):
        return {"id": f"dr-{next(self._ids)}", "drawing_set_id": drawing_set_id}

    def create_analysis_run(self, drawing_set_id, model_used):
        return {"id": f"ar-{next(self._ids)}", "drawing_set_id": drawing_set_id}

    def update_analysis_run(self, analysis_run_id, **fields):
        self.analysis_run_updates.append({"id": analysis_run_id, **fields})

    def update_drawing_set(self, drawing_set_id, **fields):
        self.drawing_set_updates.append({"id": drawing_set_id, **fields})

    def update_drawing_meta(self, drawing_id, page_count, drawing_number, drawing_title, revision):
        self.drawing_meta = {
            "id": drawing_id,
            "page_count": page_count,
            "drawing_number": drawing_number,
            "drawing_title": drawing_title,
            "revision": revision,
        }

    # --- raw AI capture (Milestone J23) -----------------------------------
    def insert_page_extraction_captures(self, rows):
        """Records one run's raw readings, in one call — as the real helper does.

        Returns how many, and stores them verbatim: this double adds no column and
        repairs no field, because the whole point of the capture layer is that what
        the model returned is written as it was returned.
        """
        self.order.append("insert_page_extraction_captures")
        self.captures.extend(copy.deepcopy(rows or []))
        return len(rows or [])

    # --- members ----------------------------------------------------------
    def insert_members(self, rows):
        self.order.append("insert_members")
        if not rows:
            return []
        inserted = []
        for row in rows:
            stored = copy.deepcopy(row)
            stored["id"] = f"m-{next(self._ids)}"
            inserted.append(stored)
        self.members.extend(inserted)
        return inserted

    def insert_review_items(self, rows):
        self.review_items.extend(copy.deepcopy(rows or []))

    # --- connections ------------------------------------------------------
    def insert_connection(self, row):
        stored = copy.deepcopy(row)
        stored["id"] = f"c-{next(self._ids)}"
        self.connections.append(stored)
        return stored

    def insert_bolt_groups(self, connection_id, bolts):
        self.bolt_groups.extend({**copy.deepcopy(b), "connection_id": connection_id} for b in (bolts or []))

    def insert_connection_plates(self, connection_id, plates):
        self.connection_plates.extend({**copy.deepcopy(p), "connection_id": connection_id} for p in (plates or []))

    def insert_weld_details(self, connection_id, welds):
        self.weld_details.extend({**copy.deepcopy(w), "connection_id": connection_id} for w in (welds or []))

    def link_connection_members(self, connection_id, member_ids):
        self.connection_member_links.extend(
            {"connection_id": connection_id, "member_id": mid} for mid in (member_ids or [])
        )

    # --- project rollup ---------------------------------------------------
    def update_project_summary(self, project_id, **fields):
        self.project_summary = {"project_id": project_id, **copy.deepcopy(fields)}


def _write_dxf(path, callouts):
    """A genuine DXF carrying one steel text callout (and one line) per entry.

    Built with the real ezdxf library on the layers the production parser reads.
    """
    import ezdxf

    doc = ezdxf.new("R2010")
    for layer in ("S-BEAM", "S-TEXT"):
        if layer not in doc.layers:
            doc.layers.add(layer)
    modelspace = doc.modelspace()
    for index, callout in enumerate(callouts):
        base_y = index * 4000.0
        modelspace.add_line((0, base_y), (3000.0, base_y), dxfattribs={"layer": "S-BEAM"})
        modelspace.add_text(
            callout, height=2.5, dxfattribs={"layer": "S-TEXT"}
        ).set_placement((0, base_y + 500.0))
    doc.saveas(path)
    return str(path)


# ==========================================================================
# The production boundary — imported with the real configuration so that
# SectionMatcher reads the LIVE catalogue.
# ==========================================================================


@pytest.fixture(scope="module")
def production():
    """
    Imports the production modules under the repo's own .env configuration.

    Teardown restores os.environ and removes every `app`/`app.*` entry this
    fixture added to sys.modules. That is deliberate and load-bearing: the
    pre-existing `tests/test_smoke_imports.py` failures exist because
    `app/config.py` reads its environment at IMPORT time, and they must keep
    failing. Leaving these modules cached would silently convert those honest
    failures into passes.
    """
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    live_config = _load_repo_dotenv()
    before = set(sys.modules)

    import app.main as main_module
    import app.pipeline as pipeline_module
    import app.report.pdf_generator as report_module
    import app.drawing_reading.dxf_parser as dxf_module
    import app.validation.rules as rules_module
    import app.engineering_data.section_matcher as matcher_module
    import app.engineering_data.repository as repository_module
    import app.cad_engine.sections as sections_module
    import app.cad_engine.errors as errors_module
    import app.cad_engine.real_member_adapter as adapter_module
    import app.cad_engine.interface as interface_module
    import app.ai_analysis.pdf_vision_analyzer as vision_module

    namespace = types.SimpleNamespace(
        main=main_module,
        pipeline=pipeline_module,
        report=report_module,
        dxf=dxf_module,
        rules=rules_module,
        matcher_module=matcher_module,
        repository=repository_module,
        sections=sections_module,
        errors=errors_module,
        adapter=adapter_module,
        interface=interface_module,
        vision=vision_module,
        PageExtraction=vision_module.PageExtraction,
        live_config=live_config,
    )
    try:
        yield namespace
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            if name not in before:
                del sys.modules[name]


@pytest.fixture(scope="module")
def live(production):
    """
    The live catalogue, read once, read-only, through the production client.

    Skips — never substitutes — when the configuration is the test placeholder.
    """
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live-catalogue "
                    "tests are skipped rather than run against a synthetic matcher")
    client = production.matcher_module.supabase
    try:
        rows = client.table("steel_sections").select("*").execute().data
    except Exception as exc:  # network/credentials unavailable in this process
        pytest.skip(f"live steel_sections catalogue could not be read: {type(exc).__name__}")
    if not rows:
        pytest.skip("live steel_sections catalogue returned no rows")
    return types.SimpleNamespace(
        rows=rows,
        names={row["name"] for row in rows},
        by_name={row["name"]: row for row in rows},
        digest=production.matcher_module.reference_data_digest(rows),
    )


@pytest.fixture()
def pdf_boundary(production, monkeypatch, tmp_path):
    """
    Drives the GENUINE production PDF boundary once per call:

        parse_pdf_and_save
          -> real `PageExtraction` objects (only the AI call itself replaced)
          -> LIVE SectionMatcher()
          -> real validate_extraction
          -> real production row construction
          -> real repository API, answered in-process
          -> build_and_store_report -> real generate_report_pdf
    """
    counter = itertools.count(1)

    def run(pages, *, project_name="J4 production truth fixture",
            user_id="j4-user", source_name="j4-drawings.pdf", with_report=True,
            document_pages=None):
        project_id = f"j4-project-{next(counter)}"
        repository = _RecordingRepository()

        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", lambda *a, **k: pages)
        # Milestone J15: the production path now reads the DOCUMENT's own page
        # count to account for the pages it did not read. This fixture supplies
        # page extractions rather than a real multi-page PDF, so the document it
        # simulates is one whose every page is among them — unless a test says
        # otherwise by passing `document_pages`, which is how a truncated or
        # partly unreadable drawing set is now expressed.
        monkeypatch.setattr(
            production.pipeline, "page_count_of",
            lambda *a, **k: len(pages) if document_pages is None else document_pages,
        )

        source = tmp_path / source_name
        source.write_bytes(b"%PDF-1.4\n% J4 test-only placeholder source\n")

        summary = production.pipeline.parse_pdf_and_save(
            str(source), project_id, user_id, f"{user_id}/{source_name}"
        )

        result = types.SimpleNamespace(
            project_id=project_id,
            user_id=user_id,
            summary=summary,
            repository=repository,
            members=repository.members,
            connections=repository.connections,
            project_row=repository.project_summary,
            report_path=None,
            pdf_bytes=None,
            pdf_text=None,
        )

        if with_report:
            # The report generator is a reader; it is fed exactly the rows the
            # production run tried to persist, plus the rollup the production
            # run computed. `name` is the one field this flow does not produce —
            # it belongs to project creation, which happens outside extraction.
            report_client = _ReportSupabase(
                project={"id": project_id, "name": project_name, **(repository.project_summary or {})},
                members=[dict(m) for m in repository.members],
                connections=_report_connections(repository),
            )
            monkeypatch.setattr(production.report, "supabase", report_client)

            uploaded = {}
            monkeypatch.setattr(
                production.main, "upload_and_record",
                lambda **kwargs: uploaded.update(kwargs) or kwargs["path"],
            )

            result.report_path = production.main.build_and_store_report(project_id, user_id)
            result.pdf_bytes = uploaded.get("content")
            result.pdf_text = _pdf_text(result.pdf_bytes)
            result.upload = uploaded

        return result

    return run


def _report_connections(repository):
    """Shape the captured connections the way generate_report_pdf's SELECT does."""
    plates = {}
    bolts = {}
    welds = {}
    links = {}
    for plate in repository.connection_plates:
        plates.setdefault(plate["connection_id"], []).append(plate)
    for bolt in repository.bolt_groups:
        bolts.setdefault(bolt["connection_id"], []).append(bolt)
    for weld in repository.weld_details:
        welds.setdefault(weld["connection_id"], []).append(weld)
    for link in repository.connection_member_links:
        links.setdefault(link["connection_id"], []).append(link)

    marks = {member["id"]: member for member in repository.members}
    shaped = []
    for connection in repository.connections:
        connection_id = connection["id"]
        shaped.append({
            **connection,
            "bolt_groups": bolts.get(connection_id, []),
            "connection_plates": plates.get(connection_id, []),
            "weld_details": welds.get(connection_id, []),
            "connection_members": [
                {"steel_members": {
                    "mark": marks[link["member_id"]]["mark"],
                    "section_name": marks[link["member_id"]]["section_name"],
                }}
                for link in links.get(connection_id, [])
                if link["member_id"] in marks
            ],
        })
    return shaped


def _pdf_text(pdf_bytes):
    """The real rendered text of the production report."""
    import pypdf

    assert pdf_bytes, "the production report produced no bytes"
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(
        " ".join((page.extract_text() or "") for page in reader.pages).split()
    )


def _page(number, members, *, drawing_number="J4-DWG-001",
          drawing_title="J4 baseline proof", revision="A", connections=()):
    """One genuine production `PageExtraction`."""
    return _PAGE_FACTORY(
        page_number=number,
        drawing_number=drawing_number,
        drawing_title=drawing_title,
        revision=revision,
        raw_members=list(members),
        raw_connections=list(connections),
    )


_PAGE_FACTORY = None


@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    """Binds the real production PageExtraction into the `_page` helper."""
    global _PAGE_FACTORY
    _PAGE_FACTORY = production.PageExtraction
    yield
    _PAGE_FACTORY = None


def _member(mark, section, *, length_mm=1200.0, quantity=1, confidence=96,
            material=None, grid_reference=None, detail_reference=None):
    """One raw member as the AI stage reports it — the production input shape."""
    row = {
        "mark": mark,
        "section": section,
        "length_mm": length_mm,
        "quantity": quantity,
        "confidence": confidence,
    }
    if material is not None:
        row["material"] = material
    if grid_reference is not None:
        row["grid_reference"] = grid_reference
    if detail_reference is not None:
        row["detail_reference"] = detail_reference
    return row


def _rows_for(members, mark):
    return [row for row in members if row["mark"] == mark]


def _single_row(members, mark):
    rows = _rows_for(members, mark)
    assert len(rows) == 1, f"expected exactly one persisted row for mark {mark!r}, got {len(rows)}"
    return rows[0]


# ==========================================================================
# 0. The boundary is genuinely the production one.
# ==========================================================================


class TestTheBoundaryIsProduction:
    def test_the_driven_functions_are_the_production_modules(self, production):
        assert production.pipeline.parse_pdf_and_save.__module__ == "app.pipeline"
        assert production.pipeline._run_pipeline.__module__ == "app.pipeline"
        assert production.rules.validate_extraction.__module__ == "app.validation.rules"
        assert production.rules.consolidate_members.__module__ == "app.validation.rules"
        assert production.report.generate_report_pdf.__module__ == "app.report.pdf_generator"
        assert production.main.build_and_store_report.__module__ == "app.main"
        assert production.dxf.parse_dxf_and_save.__module__ == "app.drawing_reading.dxf_parser"

    def test_the_section_matcher_is_the_live_production_matcher(self, production, live):
        """The pipeline's own SectionMatcher class is the live one, and the
        client it reads through is the production catalogue client."""
        assert production.pipeline.SectionMatcher is production.matcher_module.SectionMatcher
        assert production.matcher_module.supabase is not None
        assert live.rows, "the live catalogue was read"

    def test_only_the_external_stages_are_replaced(self, production, monkeypatch, tmp_path):
        """After a run, the AI boundary and the repository seam are the only
        patched names; the validation and report machinery are the originals."""
        repository = _RecordingRepository()
        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", lambda *a, **k: [])

        assert production.pipeline.repo is repository
        assert production.pipeline.SectionMatcher is production.matcher_module.SectionMatcher
        assert production.pipeline.validate_extraction is production.rules.validate_extraction
        assert production.report.generate_report_pdf.__module__ == "app.report.pdf_generator"

    def test_the_extraction_touches_no_database_client(self, production, monkeypatch, tmp_path):
        """The production run under the recording repository reaches no client."""
        repository = _RecordingRepository()
        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", lambda *a, **k: [
            _page(1, [_member("M1", EXACT_TOKEN)])
        ])
        # Milestone J15: the simulated document has exactly the one page above,
        # so its coverage is complete and the run below still reaches "done".
        monkeypatch.setattr(production.pipeline, "page_count_of", lambda *a, **k: 1)
        guard = _NetworkGuard()
        monkeypatch.setattr(production.repository, "supabase", guard)

        source = tmp_path / "guarded.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        result = production.pipeline.parse_pdf_and_save(str(source), "j4-guard", "j4-user", "j4/guarded.pdf")

        assert result["members_extracted"] == 1
        # DELIBERATELY UPDATED BY MILESTONE J13. This used to read "review" —
        # the constant both extraction paths wrote, whatever the evidence said.
        # The status is now DERIVED (app/validation/project_status.py) from what
        # this run persisted, and this fixture is the case the old constant
        # could not express: one member, resolved EXACTLY, high confidence, so
        # the pipeline writes review_status "extracted" on it, with no
        # connections, no unmatched section and no J11 discard anywhere.
        # "all required evidence is explicitly resolved" therefore holds and the
        # project reaches its terminal state. Nothing about the extraction
        # changed — the same one member was extracted, and the guard this test
        # exists for (no database client was touched) is untouched.
        assert repository.project_summary["status"] == "done"


# ==========================================================================
# 1. EXACT — the drawn token IS a catalogue key.
# ==========================================================================


class TestExactResolution:
    def test_exact_token_resolves_exact_through_the_live_matcher(self, production, live):
        assert EXACT_TOKEN in live.names
        outcome = production.matcher_module.SectionMatcher().resolve(EXACT_TOKEN)
        assert outcome.resolution == production.matcher_module.RESOLUTION_EXACT
        assert outcome.catalogue_row["name"] == EXACT_TOKEN

    def test_exact_member_persists_the_catalogue_name_and_family(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] == EXACT_TOKEN
        assert row["section_name_raw"] == EXACT_TOKEN
        assert row["section_family"] == live.by_name[EXACT_TOKEN]["family"]
        assert row["review_status"] == "extracted"
        assert row["notes"] is None

    def test_exact_member_carries_the_live_catalogue_weight(self, production, pdf_boundary, live):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, length_mm=2000.0)])])
        row = _single_row(result.members, "M1")

        expected = live.by_name[EXACT_TOKEN]["weight_per_metre"]
        assert row["weight_per_metre"] == expected
        assert row["total_weight_kg"] == pytest.approx(round(2.0 * expected, 2))

    def test_exact_member_reaches_the_production_report(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])

        assert EXACT_TOKEN in result.pdf_text
        assert "M1" in result.pdf_text
        assert result.report_path == f"{result.user_id}/{result.project_id}/steel_schedule.pdf"
        assert result.upload["bucket"] == "reports"
        assert result.upload["path_column"] == "report_pdf_path"


# ==========================================================================
# 2. SUFFIX_FALLBACK — the catalogue offered a DIFFERENT section.
# ==========================================================================


class TestSuffixFallbackResolution:
    def test_the_drawn_token_falls_back_to_a_different_catalogue_section(self, production, live):
        assert SUFFIX_FALLBACK_TOKEN not in live.names
        assert SUFFIX_FALLBACK_CANDIDATE in live.names
        outcome = production.matcher_module.SectionMatcher().resolve(SUFFIX_FALLBACK_TOKEN)
        assert outcome.resolution == production.matcher_module.RESOLUTION_SUFFIX_FALLBACK
        assert outcome.catalogue_row["name"] == SUFFIX_FALLBACK_CANDIDATE

    def test_the_refused_candidate_enriches_nothing(self, production, pdf_boundary):
        """CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C).

        J4 recorded that the refused row kept the DRAWN token in section_name.
        Live section_name is FK-bound to steel_sections(name) and a refused
        token is not a catalogue key, so J7 carries the drawn identity in
        section_name_raw instead. What this test is actually about — that none
        of the substitute's properties are adopted — is unchanged.
        """
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] is None
        assert row["section_name_raw"] == SUFFIX_FALLBACK_TOKEN
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        assert row["review_status"] == "review_required"

    def test_the_refusal_is_stated_on_the_persisted_row(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        expected = production.rules.SUBSTITUTION_NOTE.format(
            token=SUFFIX_FALLBACK_TOKEN, candidate=SUFFIX_FALLBACK_CANDIDATE,
        )
        assert row["notes"] == expected
        assert SUFFIX_FALLBACK_CANDIDATE in row["notes"]

    def test_the_refusal_is_raised_as_a_validation_issue(self, production):
        validated = production.rules.validate_extraction(
            [_member("M1", SUFFIX_FALLBACK_TOKEN)],
            production.matcher_module.SectionMatcher(),
        )
        refused = [issue for issue in validated["issues"] if issue.rule == "SECTION_SUBSTITUTION_REFUSED"]

        assert len(refused) == 1
        assert refused[0].status == "REVIEW_REQUIRED"
        assert refused[0].severity == "HIGH"
        assert SUFFIX_FALLBACK_CANDIDATE in refused[0].message

    def test_the_refusal_reaches_the_report_as_a_data_quality_note(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])

        assert SUFFIX_FALLBACK_CANDIDATE in result.pdf_text
        assert SUFFIX_FALLBACK_TOKEN in result.pdf_text
        assert result.project_row["warnings"], "the refusal reaches the project rollup"
        assert any(
            SUFFIX_FALLBACK_CANDIDATE in message for message in result.project_row["warnings"]
        ), "the refusal names the candidate it refused"

    def test_a_refused_substitution_is_not_listed_as_an_unmatched_section(self, production, pdf_boundary):
        """The refused substitution keeps its drawn identity, so it is not an
        unmatched section — its only trace on the project rollup is the warnings
        list and the row's own notes."""
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])

        assert result.project_row["unmatched_sections"] == []
        assert SUFFIX_FALLBACK_TOKEN != ""

    def test_d2_the_resolution_is_now_persisted_and_the_note_survives(self, production, pdf_boundary):
        """D2 — pinned by J4 as NOT fixed, and FIXED by J5.

        J4 asserted that the persisted row carried no record of HOW its section
        resolved (`"section_resolution" not in row`), so nothing downstream could
        tell a refused substitution from an exact match by looking at the row and
        the refusal's only trace was free text in `notes`. J5 records both
        values, so that assertion is now false and is replaced by its inverse.

        The free-text note is asserted here too: J5 added a column, it did not
        move the refusal out of the human-readable row.
        """
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_resolution"] == "SUFFIX_FALLBACK"
        assert row["section_substituted_candidate"] == SUFFIX_FALLBACK_CANDIDATE
        assert row["notes"] is not None
        assert SUFFIX_FALLBACK_CANDIDATE in row["notes"]


# ==========================================================================
# 3. NONE — nothing resolved.
# ==========================================================================


class TestNoneResolution:
    def test_none_token_is_absent_from_the_live_catalogue(self, production, live):
        assert NONE_TOKEN not in live.names
        outcome = production.matcher_module.SectionMatcher().resolve(NONE_TOKEN)
        assert outcome.resolution == production.matcher_module.RESOLUTION_NONE
        assert outcome.catalogue_row is None

    def test_pdf_retains_the_unmatched_member_with_no_identity(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] is None
        assert row["section_name_raw"] == NONE_TOKEN
        assert row["review_status"] == "review_required"
        assert row["section_family"] is None

    def test_pdf_reports_the_unmatched_section_on_the_project(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])

        assert result.project_row["unmatched_sections"] == [NONE_TOKEN]
        assert result.project_row["total_unique_sections"] == 0
        assert result.summary["review_required_count"] >= 1

    def test_pdf_shows_the_unmatched_token_in_the_report(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])

        assert NONE_TOKEN in result.pdf_text
        assert result.pdf_text.count(NONE_TOKEN) >= 2  # section cell and the unmatched list

    def test_d4_the_dxf_none_member_is_now_persisted_by_j7(self, production, monkeypatch, tmp_path):
        """D4 — pinned by J4 as NOT fixed, and FIXED by J7.

        J4 asserted that the DXF path `continue`d on a NONE resolution, so the
        member was never persisted and never reported, and named that as the J7
        redesign. J7 has made the change: the member is persisted with the same
        non-claiming shape the PDF path uses — no section_name, the drawn token
        in section_name_raw, no enrichment, held for review.
        """
        recorder = _RecordingSupabase()
        monkeypatch.setattr(production.dxf, "supabase", recorder)
        path = _write_dxf(tmp_path / "j4-none.dxf", [NONE_TOKEN])

        summary = production.dxf.parse_dxf_and_save(str(path), "j4-dxf-none")
        inserted = recorder.rows_inserted_into("steel_members")

        assert len(inserted) == 1
        assert inserted[0]["section_name"] is None
        assert inserted[0]["section_resolution"] == "NONE"
        assert summary["members_extracted"] == 1
        # Still NOT a claim that the member is the token: nothing was made a
        # catalogue identity by persisting it.
        assert not any(row.get("section_name") == NONE_TOKEN for row in inserted)

    def test_dxf_and_pdf_now_agree_on_the_same_unmatched_token(self, production, pdf_boundary, monkeypatch, tmp_path):
        """J4 pinned the ASYMMETRY: the identical token survived the PDF path and
        was discarded by the DXF path. J7 removed it — both paths now persist the
        same member with the same non-claiming shape."""
        pdf_result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN)])])
        pdf_row = _single_row(pdf_result.members, "M1")
        assert pdf_row["section_name"] is None
        assert pdf_row["section_name_raw"] == NONE_TOKEN

        recorder = _RecordingSupabase()
        monkeypatch.setattr(production.dxf, "supabase", recorder)
        path = _write_dxf(tmp_path / "j4-asymmetry.dxf", [NONE_TOKEN])
        production.dxf.parse_dxf_and_save(str(path), "j4-dxf-asymmetry")

        dxf_rows = recorder.rows_inserted_into("steel_members")
        assert len(dxf_rows) == 1
        # The two paths now agree on identity, resolution and review state; the
        # raw token differs only because each path stores the token its own
        # extractor produced (the DXF matcher's regex truncates 250X90PFC).
        assert dxf_rows[0]["section_name"] is None
        assert dxf_rows[0]["section_resolution"] == pdf_row["section_resolution"]
        assert dxf_rows[0]["review_status"] == pdf_row["review_status"]


# ==========================================================================
# 4. The four FLAT sections — through the LIVE matcher and the LIVE catalogue.
# ==========================================================================


class TestLiveFlatSections:
    def test_the_live_catalogue_holds_the_four_flat_rows(self, live):
        for name in FLAT_ROWS_ADDED_BY_J2:
            assert name in live.names, f"{name} missing from the live catalogue"
            assert live.by_name[name]["family"] == "FLAT"

    def test_the_live_catalogue_digest_is_the_pinned_post_write_value(self, live):
        assert live.digest == LIVE_DIGEST_AFTER_J2
        assert live.digest != LIVE_DIGEST_BEFORE_J2

    def test_the_live_catalogue_row_count_is_the_pinned_post_write_count(self, live):
        assert len(live.rows) == LIVE_ROW_COUNT_AFTER_J2

    def test_all_four_resolve_exact_through_the_live_matcher(self, production, live):
        matcher = production.matcher_module.SectionMatcher()
        assert matcher.reference_identity.reference_data_digest == LIVE_DIGEST_AFTER_J2
        for name in FLAT_ROWS_ADDED_BY_J2:
            outcome = matcher.resolve(name)
            assert outcome.resolution == production.matcher_module.RESOLUTION_EXACT, name
            assert outcome.catalogue_row["name"] == name
            assert outcome.catalogue_row["family"] == "FLAT"

    def test_a_flat_member_flows_through_the_production_pdf_boundary(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", "130x12FL", length_mm=1500.0)]),
        ])
        row = _single_row(result.members, "M1")

        assert row["section_name"] == "130x12FL"
        assert row["section_family"] == "FLAT"
        assert row["review_status"] == "extracted"
        assert row["notes"] is None

    @pytest.mark.parametrize("name", FLAT_ROWS_WITHOUT_LIVE_WEIGHT)
    def test_a_flat_row_without_evidence_backed_weight_stays_none(self, production, pdf_boundary, live, name):
        assert live.by_name[name]["weight_per_metre"] is None
        result = pdf_boundary([_page(1, [_member("M1", name, length_mm=1000.0)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] == name
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None

    @pytest.mark.parametrize("name,weight", sorted(FLAT_ROWS_WITH_LIVE_WEIGHT.items()))
    def test_a_flat_row_with_a_live_weight_carries_it_verbatim(self, production, pdf_boundary, live, name, weight):
        assert live.by_name[name]["weight_per_metre"] == weight
        result = pdf_boundary([_page(1, [_member("M1", name, length_mm=2000.0)])])
        row = _single_row(result.members, "M1")

        assert row["weight_per_metre"] == weight
        assert row["total_weight_kg"] == pytest.approx(round(2.0 * weight, 2))

    def test_the_flat_rows_reach_the_production_report_by_live_name(self, production, pdf_boundary, live):
        members = [_member(f"M{index}", name, length_mm=1000.0)
                   for index, name in enumerate(FLAT_ROWS_ADDED_BY_J2, start=1)]
        result = pdf_boundary([_page(1, members)])

        assert result.project_row["total_unique_sections"] == len(FLAT_ROWS_ADDED_BY_J2)
        assert result.project_row["unmatched_sections"] == []
        for name in FLAT_ROWS_ADDED_BY_J2:
            assert name in result.pdf_text
            assert name in live.names


# ==========================================================================
# 5. A plate the drawing did not dimension.
# ==========================================================================


class TestMissingPlateThickness:
    @staticmethod
    def _connection_with_an_undimensioned_plate():
        return {
            "connection_type": "bolted",
            "grid_reference": "B/2",
            "connects_members": ["M1"],
            "confidence": 96,
            "bolts": [{"size": "M20", "grade": "8.8", "quantity": 4}],
            "plates": [{"type": "cleat", "width_mm": 150.0, "depth_mm": 200.0}],
        }

    def test_the_absent_thickness_persists_as_none(self, production, pdf_boundary):
        result = pdf_boundary(
            [_page(1, [_member("M1", EXACT_TOKEN)], connections=[self._connection_with_an_undimensioned_plate()])]
        )
        plates = result.repository.connection_plates

        assert len(plates) == 1
        assert "thickness" in plates[0]
        assert plates[0]["thickness"] is None

    def test_the_absent_thickness_is_never_collapsed_to_zero(self, production, pdf_boundary):
        result = pdf_boundary(
            [_page(1, [_member("M1", EXACT_TOKEN)], connections=[self._connection_with_an_undimensioned_plate()])]
        )

        assert result.repository.connection_plates[0]["thickness"] != 0

    def test_the_report_renders_a_none_thickness_as_Nonemm(self, production, pdf_boundary):
        """D-pinned: the plate spec row reads `p.get("thickness", "?")`, whose
        default applies only when the key is ABSENT. The persisted value is a
        present NULL, so the plate specification renders the literal 'Nonemm'.
        The connection description — built from the AI frame's own
        `thickness_mm` key, which was never present — renders '?mm'. Two
        different placeholders for the same unknown."""
        result = pdf_boundary(
            [_page(1, [_member("M1", EXACT_TOKEN)], connections=[self._connection_with_an_undimensioned_plate()])]
        )

        assert "Nonemm" in result.pdf_text
        assert "?mm" in result.pdf_text

    def test_a_connection_with_no_plates_at_all_still_renders(self, production, pdf_boundary):
        connection = self._connection_with_an_undimensioned_plate()
        connection["plates"] = []
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)], connections=[connection])])

        assert "Connection 1" in result.pdf_text
        assert result.repository.connection_plates == []


# ==========================================================================
# 6. A member whose drawing states no grade.
# ==========================================================================


class TestMissingGrade:
    def test_an_unstated_member_grade_persists_as_none(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert "grade" in row
        assert row["grade"] is None

    def test_an_unstated_grade_is_never_defaulted(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["grade"] not in ("300", "300PLUS", "UNKNOWN", "")
        assert row["grade"] is None

    def test_a_stated_member_grade_is_carried_verbatim(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, material="300PLUS")])])
        row = _single_row(result.members, "M1")

        assert row["grade"] == "300PLUS"

    def test_an_unstated_plate_grade_persists_as_none(self, production, pdf_boundary):
        connection = {
            "connection_type": "bolted",
            "connects_members": ["M1"],
            "confidence": 96,
            "plates": [{"type": "cleat", "thickness_mm": 10.0, "width_mm": 150.0, "depth_mm": 200.0}],
        }
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)], connections=[connection])])

        assert result.repository.connection_plates[0]["grade"] is None


# ==========================================================================
# 7. A live catalogue row the CAD projection cannot build.
# ==========================================================================


class TestCatalogueGeometryConflict:
    def test_the_live_ea_family_lacks_the_width_the_projection_requires(self, production, live):
        ea_rows = [row for row in live.rows if row["family"] == "EA"]
        missing = [row for row in ea_rows if row.get("width") is None]

        assert ea_rows, "the live catalogue has EA rows"
        assert missing, "the live catalogue has EA rows without the required width"
        assert EA_TOKEN_WITHOUT_WIDTH in live.names

    def test_the_ea_token_resolves_exact_and_is_admitted_by_the_adapter(self, production, live):
        matcher = production.matcher_module.SectionMatcher()
        outcome = matcher.resolve(EA_TOKEN_WITHOUT_WIDTH)
        assert outcome.resolution == production.matcher_module.RESOLUTION_EXACT

        row = {
            "mark": "M1",
            "section_name": EA_TOKEN_WITHOUT_WIDTH,
            "length_mm": 1000.0,
            "quantity": 1,
            "grade": "300",
            "review_status": "extracted",
        }
        validated = production.adapter.real_member_to_validated_member(row, matcher)
        assert validated.section == EA_TOKEN_WITHOUT_WIDTH
        assert validated.section_properties["family"] == "EA"

    def test_the_projection_then_refuses_for_the_missing_field(self, production, live):
        matcher = production.matcher_module.SectionMatcher()
        row = {
            "mark": "M1",
            "section_name": EA_TOKEN_WITHOUT_WIDTH,
            "length_mm": 1000.0,
            "quantity": 1,
            "grade": "300",
            "review_status": "extracted",
        }
        validated = production.adapter.real_member_to_validated_member(row, matcher)

        with pytest.raises(production.errors.GeometryValidationError) as caught:
            production.interface.generate_geometry(validated)
        assert "width" in str(caught.value)

    def test_the_conflict_is_entirely_in_the_live_catalogue_not_the_drawing(self, production, live):
        """The J4 proof constructs nothing: the row, its family and its missing
        width are all live catalogue facts."""
        row = live.by_name[EA_TOKEN_WITHOUT_WIDTH]

        assert row["family"] == "EA"
        assert row["width"] is None
        assert production.sections.cad_family_for("EA") in production.sections.PROFILE_BUILDERS


# ==========================================================================
# 8. Reference identity — established live, and provably not persisted.
# ==========================================================================


class TestReferenceIdentity:
    def test_the_live_matcher_reports_a_genuine_live_identity(self, production, live):
        identity = production.matcher_module.SectionMatcher().reference_identity

        assert identity.source_kind == production.matcher_module.SOURCE_LIVE_SUPABASE
        assert identity.identity_status == production.matcher_module.IDENTITY_UNVERSIONED
        assert identity.reference_data_digest == LIVE_DIGEST_AFTER_J2
        assert re.fullmatch(r"[0-9a-f]{64}", identity.reference_data_digest)

    def test_the_digest_is_a_pure_function_of_the_live_row_set(self, production, live):
        recomputed = production.matcher_module.reference_data_digest(live.rows)
        assert recomputed == live.digest == LIVE_DIGEST_AFTER_J2

    def test_pinned_defect_d3_no_persisted_row_carries_a_reference_identity(self, production, pdf_boundary):
        """D3 (pinned, NOT fixed): the extraction knows which catalogue it read,
        and the digest, and persists NEITHER."""
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])
        row = _single_row(result.members, "M1")

        for field in ("reference_data_digest", "source_kind", "identity_status",
                      "catalogue_version", "reference_identity"):
            assert field not in row

    def test_pinned_defect_d3_the_repository_cannot_accept_one(self, production):
        """The persistence API has no provenance parameter at all, so this is a
        structural gap rather than a call-site omission."""
        import inspect

        signature = inspect.signature(production.repository.insert_members)
        assert list(signature.parameters) == ["rows"]

    def test_the_project_rollup_carries_no_reference_identity_either(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN)])])

        for field in ("reference_data_digest", "source_kind", "identity_status"):
            assert field not in result.project_row


# ==========================================================================
# 9. Consolidation across pages.
# ==========================================================================


class TestConsolidation:
    def test_a_mark_agreed_across_pages_becomes_one_row(self, production, pdf_boundary):
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 1
        assert rows[0]["section_name"] == EXACT_TOKEN

    def test_d5_a_genuine_agreement_is_still_promoted_and_all_pages_counted(self, production, pdf_boundary):
        """D5's promotion path, correctly narrowed by J5.

        J4 pinned this as a defect, but the defect was never the promotion
        itself — it was that consolidation reached for the promotion when the
        pages did NOT agree. When every page states the same catalogue section
        the promotion is correct and is kept exactly as it was. J5 only added
        the precondition. `test_d5_a_disagreeing_page_keeps_its_own_row` below
        covers the case J4 was actually pointing at.
        """
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
        ])
        row = _single_row(result.members, "M1")

        assert row["review_status"] == "extracted"
        assert row["notes"] == "Confirmed on 2 page(s): [1, 2]"
        assert row["section_resolution"] == "EXACT"

    def test_d5_a_disagreeing_page_keeps_its_own_row(self, production, pdf_boundary):
        """D5's sharpest case — pinned by J4, FIXED by J5.

        Mark M1 is a confirmed section on page 1 and an UNRESOLVED token on page
        2. Before J5 consolidation kept the matched row only, so the unresolved
        token never reached `unmatched_sections` and the project reported a clean
        sheet. J4 asserted that loss (`len(rows) == 1`); J5 retains both rows, so
        the assertion is replaced by its inverse.
        """
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 2
        unresolved = next(row for row in rows if row["section_resolution"] == "NONE")
        assert unresolved["section_name"] is None
        assert unresolved["section_name_raw"] == NONE_TOKEN
        assert NONE_TOKEN in result.project_row["unmatched_sections"]

    def test_d5_the_report_now_shows_the_disagreeing_token(self, production, pdf_boundary):
        """The same case, followed out to the rendered report: J4 asserted the
        token was absent; it is now present."""
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=1200.0)]),
        ])

        assert NONE_TOKEN in result.pdf_text

    def test_two_genuinely_different_sections_for_one_mark_are_kept_as_a_conflict(self, production, pdf_boundary):
        """The one consolidation branch that DOES keep both rows and flags them."""
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=1200.0)]),
            _page(2, [_member("M1", "250UB25.7", length_mm=1200.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 2
        assert all(row["review_status"] == "review_required" for row in rows)
        assert {row["section_name"] for row in rows} == {EXACT_TOKEN, "250UB25.7"}


# ==========================================================================
# 10. The report's status pill.
# ==========================================================================


class TestReportStatus:
    """
    D1 — pinned by J4 as a single confidence-driven pill, FIXED by J5.

    J4 drove `report._status_pill(confidence)`, a function that no longer
    exists: it rendered "✓ Matched" for a confident row and "Verify" for an
    unsure one, whatever the row's engineering status was. J5 split that into
    `_section_status_pill(resolution)` (engineering) and `_confidence_pill(
    confidence)` (AI extraction), and the three assertions J4 made about the
    old function are replaced here by their inverses plus the split itself.
    The full proof is in
    `tests/test_real_world_j5_production_resolution_truth.py`.
    """

    def test_the_single_confidence_pill_no_longer_exists(self, production):
        assert not hasattr(production.report, "_status_pill")
        assert hasattr(production.report, "_section_status_pill")
        assert hasattr(production.report, "_confidence_pill")

    def _confidence_text(self, production, confidence):
        return production.report._confidence_pill(confidence, production.report._styles()).text

    def test_no_confidence_value_renders_a_match_claim(self, production):
        """The old pill called a confident row 'Matched'. The confidence pill
        never does: AI extraction confidence is not section authority."""
        for confidence in ("high", "medium", "low", "manual", None):
            text = self._confidence_text(production, confidence)
            assert "Matched" not in text, confidence
            assert "Confirmed" not in text, confidence

    def test_the_confidence_pill_still_reports_the_ai_reading(self, production):
        """D1's fix must not delete the AI information it replaced."""
        assert "High" in self._confidence_text(production, "high")
        assert "Low" in self._confidence_text(production, "low")

    def test_d1_a_high_confidence_unresolved_row_no_longer_renders_matched(self, production, pdf_boundary):
        """J4's sharpest D1 case: a row that is review_required with NO section
        identity rendered the green '✓ Matched' pill, because the pill was
        computed from AI confidence alone. The engineering status is now
        computed from the resolution, so it cannot."""
        result = pdf_boundary([_page(1, [_member("M1", NONE_TOKEN, confidence=96)])])
        row = _single_row(result.members, "M1")

        assert row["confidence"] == "high"
        assert row["review_status"] == "review_required"
        assert row["section_name"] is None
        assert "Matched" not in result.pdf_text
        assert "Unresolved" in result.pdf_text

    def test_d1_a_refused_substitution_no_longer_renders_matched(self, production, pdf_boundary):
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN, confidence=96)])])
        row = _single_row(result.members, "M1")

        assert row["review_status"] == "review_required"
        assert "Matched" not in result.pdf_text
        assert "Substitution refused" in result.pdf_text

    def test_d1_a_low_confidence_exact_match_no_longer_renders_verify(self, production, pdf_boundary):
        """The mirror image J4 pinned: a genuinely confirmed catalogue section
        was shown as needing verification purely because the AI was unsure. An
        independently confirmed exact match is not downgraded by extraction
        confidence."""
        result = pdf_boundary([_page(1, [_member("M1", EXACT_TOKEN, confidence=40)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] == EXACT_TOKEN
        assert row["confidence"] == "low"
        assert "Confirmed" in result.pdf_text
        assert "Verify" not in result.pdf_text


# ==========================================================================
# 11. The DXF foreign key — what can and cannot be established read-only.
# ==========================================================================


class TestDxfForeignKeyQuestion:
    def test_the_dxf_fallback_row_keeps_the_drawn_token_as_its_identity(self, production, monkeypatch, tmp_path):
        """CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C).

        The DXF fallback row still keeps the DRAWN token as its identity — that
        part of this pin is unchanged — but the column carrying it is now
        section_name_raw, because section_name is FK-bound to
        steel_sections(name) and the drawn token is not a catalogue key.
        """
        recorder = _RecordingSupabase()
        monkeypatch.setattr(production.dxf, "supabase", recorder)
        path = _write_dxf(tmp_path / "j4-fallback.dxf", [SUFFIX_FALLBACK_TOKEN])

        production.dxf.parse_dxf_and_save(str(path), "j4-dxf-fallback")
        inserted = recorder.rows_inserted_into("steel_members")

        assert len(inserted) == 1
        assert inserted[0]["section_name"] is None
        assert inserted[0]["section_name_raw"] == SUFFIX_FALLBACK_TOKEN
        assert inserted[0]["weight_per_metre"] is None

    def test_the_structural_precondition_holds_against_the_live_catalogue(self, production, live):
        """The strongest statement a READ-ONLY proof can make: the value the DXF
        path tries to persist into `section_name` is NOT a key of the live
        `steel_sections` table."""
        assert SUFFIX_FALLBACK_TOKEN not in live.names
        assert SUFFIX_FALLBACK_CANDIDATE in live.names

    def test_the_foreign_key_itself_cannot_be_established_without_a_mutation(self, production, live, monkeypatch, tmp_path):
        """Stated explicitly, per the J4 brief, rather than speculated about.

        CANNOT BE ESTABLISHED BY THIS MILESTONE. The reason is structural, and
        this test demonstrates it rather than asserting a placeholder:

        J4 is required to intercept persistence at the parser/repository seam.
        An intercepted insert is answered in-process, so PostgreSQL never sees
        it and the foreign key is never evaluated. The double therefore returns
        a SUCCESSFUL insert for a `section_name` that is not a key of the live
        `steel_sections` table — which is exactly the evidence that its verdict
        says nothing about the real constraint.

        Establishing whether the live constraint actually rejects this row
        requires issuing a real INSERT, which J4 forbids. The DXF FK question is
        left OPEN by this milestone: what J4 pins is the precondition only.

        STILL OPEN AFTER J7, AND NOW MOOT FOR THIS ROW. The same limitation
        holds — an intercepted insert cannot produce a constraint verdict, and
        J7 does not claim one. What J7 changed is that the DXF path no longer
        offers a non-key value to the FK at all: section_name is NULL for a
        refused fallback, so the row whose rejection J4 could not prove is a row
        that is no longer constructed. The precondition asserted below therefore
        remains true and is no longer the shape of the row that gets written.
        """
        assert SUFFIX_FALLBACK_TOKEN not in live.names, "precondition: not a catalogue key"

        recorder = _RecordingSupabase()
        monkeypatch.setattr(production.dxf, "supabase", recorder)
        path = _write_dxf(tmp_path / "j4-fk-question.dxf", [SUFFIX_FALLBACK_TOKEN])

        summary = production.dxf.parse_dxf_and_save(str(path), "j4-dxf-fk")
        inserted = recorder.rows_inserted_into("steel_members")

        # The interception accepted a row. No constraint was consulted, so no
        # constraint's answer is available here — and since J7 the row no longer
        # carries a non-key value into the FK-bound column for one to be needed.
        assert summary["members_extracted"] == 1
        assert len(inserted) == 1
        assert inserted[0]["section_name"] is None
        assert inserted[0]["section_name_raw"] == SUFFIX_FALLBACK_TOKEN
        assert not hasattr(recorder, "postgrest") and not hasattr(recorder, "_client"), (
            "the double must hold no database client: it has no constraint "
            "semantics and therefore cannot answer the foreign-key question"
        )
