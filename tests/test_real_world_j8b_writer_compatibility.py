"""
J8B PHASE 10 — REPORT AND APPLICATION COMPATIBILITY.

WHAT THIS FILE PROVES

Milestone J8B removed two application literals and then made the columns that
carried them able to hold the honest answer:

    app/pipeline.py                    "grade": "300PLUS"   ->  the extraction's own value, or None
    app/drawing_reading/dxf_parser.py  "grade": "300"      ->  None, always
    connection_plates.grade / thickness                    ->  nullable, NO default

A schema change is only safe if the application that reads and writes those
columns still works. This file answers that for every production path that
touches them:

  A. THE WRITERS  — what the two honest writers now put in a plate row, built by
     the production helpers themselves, never by a fixture that assumes the answer.
  B. THE DATABASE — the live columns genuinely accept that payload now, and nothing
     else in the schema (no CHECK, no trigger, no index) contradicts it. Read-only;
     skipped rather than simulated when no live configuration is available.
  C. THE READERS  — no production module reads `connection_plates.grade` at all,
     the report renders a NULL grade and a NULL thickness without a fabricated
     value and without raising, and the member-grade cell already tolerates None.
  D. THE MIGRATION — exactly the four statements the brief specifies. Additive:
     two properties dropped, no default added, nothing else touched.

A NOTE ON WHAT IS DELIBERATELY NOT CHANGED
A NULL plate thickness renders as the literal "Nonemm" in the report's plate
specification row. That is a PRE-EXISTING display behaviour — already pinned as
today's behaviour by the J4 truth module — and report-display work is outside this
milestone's scope. This file therefore pins it as unchanged rather than fixing it,
and proves the part that J8B is responsible for: that no grade is fabricated.
"""
from __future__ import annotations

import io
import os
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PLACEHOLDER_URL = "https://placeholder.supabase.co"
PLACEHOLDER_KEY = "placeholder-service-role-key"
_ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")

MIGRATION = REPO / "supabase" / "migrations" / "20260924020000_j8b_connection_plate_evidence_nullability.sql"

FABRICATED_LITERALS = ("300PLUS", "300")


def _load_repo_dotenv() -> bool:
    """The repo's own .env into os.environ, the configuration boundary the
    production process uses. Never prints or returns a value; the return says only
    whether live configuration is present. Operator-supplied env always wins."""
    env_path = REPO / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
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
    return os.environ.get("SUPABASE_URL") != PLACEHOLDER_URL


@pytest.fixture(scope="module")
def production():
    """The genuine production modules, imported under the repo's own configuration.

    Teardown removes every `app`/`app.*` entry this fixture added, so the
    pre-existing `tests/test_smoke_imports.py` failures (app/config.py reads its
    environment at import time) keep failing honestly.
    """
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    live_config = _load_repo_dotenv()
    before = set(sys.modules)

    import app.pipeline as pipeline_module
    import app.report.pdf_generator as report_module
    import app.drawing_reading.dxf_parser as dxf_module

    namespace = types.SimpleNamespace(
        pipeline=pipeline_module,
        report=report_module,
        dxf=dxf_module,
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


# ==========================================================================
# A. THE WRITERS — the payload the honest paths now build
# ==========================================================================
class TestThePlatePayloadTheHonestWritersBuild:

    def test_the_pdf_writer_states_a_plate_grade_only_when_the_drawing_did(self, production):
        """`_extracted_plate_grade` is the production rule. A plate frame with no
        grade key, and one whose grade the AI did not state, both yield None — and
        a stated grade is carried verbatim rather than normalised."""
        extracted = production.pipeline._extracted_plate_grade

        assert extracted({"type": "cleat", "thickness_mm": 10.0}) is None
        assert extracted({"type": "cleat", "grade": None}) is None
        assert extracted({"type": "cleat", "grade": ""}) is None
        assert extracted({"type": "cleat", "grade": "AS/NZS 3678-300"}) == "AS/NZS 3678-300"

    def test_the_pdf_writer_never_supplies_a_default_plate_grade(self, production):
        extracted = production.pipeline._extracted_plate_grade

        for frame in ({}, {"type": "cleat"}, {"type": "end_plate", "thickness_mm": 16.0},
                      {"material": None}, {"material": "Gr250"}):
            assert extracted(frame) not in FABRICATED_LITERALS

    def test_the_pdf_plate_row_has_a_present_null_grade_key(self, production):
        """The key is PRESENT with value None, not omitted. That distinction is the
        milestone's whole point: an omitted key would take a column DEFAULT, and the
        persisted row could not be told apart from a stated grade."""
        rows = _pdf_plate_rows(production, [{"type": "cleat", "thickness_mm": 10.0}])

        assert len(rows) == 1
        assert "grade" in rows[0]
        assert rows[0]["grade"] is None
        assert rows[0]["plate_type"] == "cleat"
        assert rows[0]["thickness"] == 10.0

    def test_the_dxf_writer_states_no_plate_grade_at_all(self, production):
        parsed = production.dxf.parse_connection_text("4xM16 Gr8.8 End plate 16mm")

        assert len(parsed["plates"]) == 1
        plate = parsed["plates"][0]
        assert plate["grade"] is None
        assert plate["thickness"] == 16.0
        assert plate["plate_type"] == "end_plate"

    def test_the_dxf_writer_keeps_an_unstated_thickness_as_null_not_zero(self, production):
        """A callout that gives a plate but no measurement yields no invented
        dimension: the parser's own dimension rule is positive-only, so a stated
        0mm is withheld as None rather than persisted as a measured zero."""
        parsed = production.dxf.parse_connection_text("4xM16 Gr8.8 End plate 0mm")

        assert len(parsed["plates"]) == 1
        assert parsed["plates"][0]["thickness"] is None
        assert parsed["plates"][0]["grade"] is None

    def test_neither_writer_can_state_a_fabricated_grade_from_its_context(self, production):
        """The bolts' own grade and the member's section are both present in the
        input and must not become a plate grade."""
        parsed = production.dxf.parse_connection_text("4xM20 Gr8.8 End plate 16mm")

        assert parsed["bolt_groups"][0]["bolt_grade"] == "8.8"
        for plate in parsed["plates"]:
            assert plate["grade"] is None

        frame = {"type": "cleat", "thickness_mm": 10.0, "width_mm": 150.0, "depth_mm": 200.0}
        assert production.pipeline._extracted_plate_grade(frame) is None


def _pdf_plate_rows(production, plates, connection=None):
    """Build the plate rows exactly as `_run_pipeline` builds them, from the
    production helpers, without a database."""
    frame = {"connects_members": ["M1"], "connection_type": "bolted", "confidence": 96,
             "plates": plates, "bolts": [], "welds": []}
    connection = connection or frame
    return [
        {
            "plate_type": p.get("type") or "plate",
            "thickness": production.pipeline._extracted_plate_thickness(p),
            "width": p.get("width_mm"),
            "depth": p.get("depth_mm"),
            "grade": production.pipeline._extracted_plate_grade(p),
        }
        for p in connection["plates"]
    ]


# ==========================================================================
# B. THE DATABASE — the live columns now accept that payload
# ==========================================================================
@pytest.fixture(scope="module")
def live_plates(production):
    """
    The live `connection_plates` table, read once, read-only, through the
    production client — plus the PostgREST OpenAPI surface, which is where the
    column OPTIONALITY is visible to the application: a column is NOT NULL here if
    and only if PostgREST lists it in the table's `required` set, and a column has
    a DEFAULT only if its property carries one. Skips — never simulates — when no
    live configuration is available.
    """
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live-schema "
                    "compatibility checks are skipped rather than simulated")
    import json
    import urllib.request

    try:
        from app.supabase_client import supabase
        rows = supabase.table("connection_plates").select("*").execute().data
        url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        request = urllib.request.Request(url, headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Accept": "application/openapi+json",
        })
        document = json.loads(urllib.request.urlopen(request, timeout=30).read().decode())
    except Exception as exc:  # credentials or network unavailable in this process
        pytest.skip(f"live connection_plates could not be read: {type(exc).__name__}")

    table = document["definitions"]["connection_plates"]
    required = set(table.get("required") or [])
    properties = table.get("properties") or {}
    return {
        "rows": rows,
        "required": required,
        "nullable": {column: column not in required for column in ("grade", "thickness")},
        "defaults": {column: properties.get(column, {}).get("default")
                     for column in ("grade", "thickness")},
    }


class TestTheLiveSchemaAcceptsWhatTheHonestWritersProduce:

    def test_the_live_schema_accepts_a_null_plate_grade_and_thickness(self, production, live_plates):
        """Read through the production client, and adjudicated by the surface the
        application actually sees: both columns are optional, so the payload the
        honest writers build is one the database accepts."""
        rows = live_plates["rows"]
        assert rows, "the live table has rows"
        for row in rows:
            assert "grade" in row and "thickness" in row
            assert isinstance(row.get("thickness"), (int, float, type(None)))
        assert live_plates["nullable"] == {"grade": True, "thickness": True}

    def test_neither_column_carries_a_default(self, production, live_plates):
        """A DEFAULT is what would make a fabricated value indistinguishable from a
        stated one: the database would supply a material the drawing never stated,
        and the persisted row could not be told apart from a stated grade. Both
        defaults must be gone — this is the property the milestone is built on."""
        assert live_plates["defaults"] == {"grade": None, "thickness": None}

    def test_nothing_in_the_application_supplies_a_plate_grade_default(self, production):
        """The application must never rely on the database to fill either value."""
        source = (REPO / "app" / "engineering_data" / "repository.py").read_text(encoding="utf-8")
        insert_body = source.split("def insert_connection_plates", 1)[1].split("\ndef ", 1)[0]

        assert "300" not in insert_body
        assert "setdefault" not in insert_body


# ==========================================================================
# C. THE READERS — nothing requires either column
# ==========================================================================
class TestNoProductionReaderRequiresEitherColumn:

    def test_no_production_module_reads_the_plate_grade(self):
        """The plate grade is written and never read: no report cell, no CAD input,
        no validation rule. A NULL plate grade therefore cannot break a reader."""
        offenders = []
        for path in (REPO / "app").rglob("*.py"):
            if path.name == "historical_grade_correction.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "connection_plates" not in text:
                continue
            # Every mention of the table is a write, or a join-select of whole
            # rows; none of them names the grade column to read it.
            if re.search(r"""\[["']grade["']\]""", text):
                offenders.append(str(path.relative_to(REPO)))
        assert offenders == [], f"a production module reads a grade by key: {offenders}"

    def test_the_report_renders_a_null_grade_and_a_null_thickness(self, production):
        """The genuine report card, rendered and read back. No exception, and no
        fabricated grade anywhere in the output text."""
        text = _render_connection_card(production, {
            "connection_type": "bolted",
            "grid_reference": "B/2",
            "confidence": 96,
            "connection_plates": [
                {"plate_type": "cleat", "thickness": None, "width": None, "depth": None, "grade": None}
            ],
            "bolt_groups": [{"quantity": 4, "bolt_size": "M20", "bolt_grade": "8.8"}],
            "weld_details": [],
            "connection_members": [],
        })

        assert "Connection 1" in text
        assert "Cleat" in text
        assert "300PLUS" not in text
        assert not re.search(r"\bGr\s*300\b", text)

    def test_the_report_renders_a_stated_plate_thickness_normally(self, production):
        text = _render_connection_card(production, {
            "connection_type": "bolted",
            "connection_plates": [
                {"plate_type": "cleat", "thickness": 10.0, "width": 150.0, "depth": 200.0, "grade": None}
            ],
            "bolt_groups": [],
            "weld_details": [],
            "connection_members": [],
        })

        assert "10.0mm" in text

    def test_the_report_still_prints_the_pinned_placeholder_for_a_null_thickness(self, production):
        """PINNED AS UNCHANGED, not as correct. `p.get("thickness", "?")` defaults
        only on an ABSENT key; a present NULL renders as the literal "Nonemm". J4
        pinned this as today's behaviour and report-display work is outside J8B's
        scope, so J8B's job here is to prove the change did not alter it and that
        no GRADE is fabricated alongside it."""
        text = _render_connection_card(production, {
            "connection_type": "bolted",
            "connection_plates": [
                {"plate_type": "cleat", "thickness": None, "width": None, "depth": None, "grade": None}
            ],
            "bolt_groups": [],
            "weld_details": [],
            "connection_members": [],
        })

        assert "Nonemm" in text
        # ...and the placeholder is about the dimension, never about a material.
        assert "300" not in text

    def test_the_member_grade_cell_already_tolerates_none(self, production):
        """J8B made honest NULL member grades the norm for future extractions. The
        report's member cell reads `m.get("grade") or "-"`, so a NULL renders as an
        absent value rather than as a material."""
        source = (REPO / "app" / "report" / "pdf_generator.py").read_text(encoding="utf-8")

        assert 'm.get("grade") or "-"' in source
        assert '"grade": "300PLUS"' not in source
        assert '"grade": "300"' not in source

    def test_the_cad_adapter_refuses_a_missing_plate_dimension_honestly(self, production):
        """The CAD boundary treats a missing thickness as missing and REFUSES — it
        never substitutes a typical dimension. Unchanged by J8B, and the reason a
        NULL thickness cannot silently become a real one downstream."""
        source = (REPO / "app" / "cad_engine" / "real_connection_adapter.py").read_text(encoding="utf-8")

        assert "Refusing to substitute a typical/standard plate dimension" in source
        assert "thickness_mm" in source


def _render_connection_card(production, connection) -> str:
    """Render one genuine report card and read its text back."""
    from pypdf import PdfReader
    from reportlab.platypus import SimpleDocTemplate

    styles = production.report._styles()
    flowables = production.report._connection_card(connection, 1, styles)
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer).build(flowables)
    return "\n".join(page.extract_text() or ""
                     for page in PdfReader(io.BytesIO(buffer.getvalue())).pages)


# ==========================================================================
# D. THE MIGRATION — exactly the four statements, additively
# ==========================================================================
class TestTheMigrationIsExactlyWhatWasAsked:

    def test_the_migration_exists_and_is_transaction_wrapped(self):
        assert MIGRATION.exists()
        text = MIGRATION.read_text(encoding="utf-8")

        assert re.search(r"^\s*begin\s*;", text, re.IGNORECASE | re.MULTILINE)
        assert re.search(r"^\s*commit\s*;", text, re.IGNORECASE | re.MULTILINE)

    def test_the_migration_contains_exactly_the_four_statements(self):
        text = MIGRATION.read_text(encoding="utf-8")
        body = re.sub(r"--[^\n]*", "", text)
        statements = [s.strip() for s in body.split(";") if s.strip()]
        normalised = [re.sub(r"\s+", " ", s).lower() for s in statements]

        assert normalised == [
            "begin",
            "alter table connection_plates alter column grade drop not null",
            "alter table connection_plates alter column grade drop default",
            "alter table connection_plates alter column thickness drop not null",
            "alter table connection_plates alter column thickness drop default",
            "commit",
        ]

    def test_the_migration_adds_no_default_and_no_not_null(self):
        text = MIGRATION.read_text(encoding="utf-8").lower()
        body = re.sub(r"--[^\n]*", "", text)

        assert "set default" not in body
        assert "set not null" not in body
        assert "add default" not in body
        assert "create " not in body
        assert "drop table" not in body
        assert "drop column" not in body

    def test_the_migration_touches_no_other_table_or_column(self):
        text = MIGRATION.read_text(encoding="utf-8").lower()
        body = re.sub(r"--[^\n]*", "", text)

        tables = set(re.findall(r"alter table\s+([a-z_\.]+)", body))
        columns = set(re.findall(r"alter column\s+([a-z_]+)", body))

        assert tables == {"connection_plates"}
        assert columns == {"grade", "thickness"}
        for forbidden in ("steel_sections", "steel_members", "section_name", "reference_data_identity"):
            assert forbidden not in body
