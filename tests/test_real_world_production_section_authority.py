"""
PRODUCTION SECTION AUTHORITY — the catalogue may enrich a member, never rename it.

WHAT THIS FILE PROVES

app/validation/rules.py is the ONE place production matches a section
(app/pipeline.py:88 drives it). Before this milestone it wrote the CATALOGUE
row's name into `section_name` — the authoritative engineering identity — so a
drawing stating "310UB40" was persisted as "310UB40.4", a different section,
and its catalogue properties were adopted as if the drawing had said so. The
drawing's own token survived only in `section_name_raw`.

The rule now enforced, in that one place:

    EXACT           the catalogue row IS the drawn section (its own house
                    spelling of it) -> identity kept, enrichment permitted
    SUFFIX_FALLBACK the catalogue row is a DIFFERENT section -> refused as
                    identity AND as properties; member held for review
    NONE            unchanged: no section_name, existing unmatched/review path

The real production path is exercised end to end here: the real SectionMatcher
(against an injected repository double, no network), the real
validate_extraction, and the real app/pipeline.py `_run_pipeline` persistence —
with the vision call, the only genuinely external stage, replaced by the real
PageExtraction dataclass. Nothing is asserted against a hand-built result
object.

SOURCE-OF-TRUTH LIMITATION (deliberate, stated plainly): this is NOT E1 wired
into production. E1's evaluate_section_evidence() needs drawing-side
dimensional evidence to compare against the catalogue, and the current PDF AI
schema does not capture reliable member geometry. So the rule here is the
simpler safe one above — a fallback is refused, not adjudicated. An
evidence-backed fallback resolution is a later milestone, once the extraction
schema genuinely carries the geometry such a comparison needs.

WHAT IS *NOT* FIXED HERE (verified, not assumed — and not mistaken for done):
app/cad_engine/real_member_adapter.py:103 performs its OWN catalogue lookup,
`matcher.match(section_name)`, on the very section_name this pipeline now
persists correctly. Because match() still resolves by suffix fallback, a row
whose section_name is "310UB40" would match the 310UB40.4 row there and be
built as that section — silently, before the adapter's own
"Refusing to substitute or approximate another section" guard is ever reached.
That path belongs to the milestone drawing flows, not to this PDF pipeline: the
production function proven reachable here (`_run_pipeline`) stops at
persistence and never enters the CAD engine at all. Closing the adapter's
second lookup is the next milestone's work, deliberately not done here.
"""

import ast
import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# The captured live evidence this file is anchored to — the same 218-row
# capture the matcher regression and the Milestone E1 tests pin.
# ---------------------------------------------------------------------------
SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
SNAPSHOT_ROW_COUNT = 218

TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)

# ---------------------------------------------------------------------------
# Pinned VERBATIM from the capture. 310UB46.2 and 310UB40.4 are the two
# sections the fallback confusion is about; 25x25x3EA is the case/whitespace
# witness — the catalogue spells it lower-case-x, the matcher's canonical form
# is "25X25X3EA", and an exact match must NOT re-spell it.
# ---------------------------------------------------------------------------
LIVE_310UB46_2 = {
    "name": "310UB46.2", "family": "UB", "depth": 307.0, "flange_width": 166.0,
    "flange_thickness": 11.8, "web_thickness": 6.7, "weight_per_metre": 46.2,
    "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
}
LIVE_310UB40_4 = {
    "name": "310UB40.4", "family": "UB", "depth": 304.0, "flange_width": 165.0,
    "flange_thickness": 10.2, "web_thickness": 6.1, "weight_per_metre": 40.4,
    "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
}
LIVE_250PFC = {
    "name": "250PFC", "family": "PFC", "depth": 250.0, "flange_width": 90.0,
    "flange_thickness": 12.0, "web_thickness": 7.0, "weight_per_metre": 31.8,
    "leg_size": None, "thickness": None, "width": None, "outside_diameter": None,
}
LIVE_25x25x3EA = {
    "name": "25x25x3EA", "family": "EA", "depth": None, "flange_width": None,
    "flange_thickness": None, "web_thickness": None, "weight_per_metre": 1.12,
    "leg_size": 25.0, "thickness": 3.0, "width": None, "outside_diameter": None,
}

PINNED_ROWS = (LIVE_310UB46_2, LIVE_310UB40_4, LIVE_250PFC, LIVE_25x25x3EA)
PINNED_NAMES = tuple(row["name"] for row in PINNED_ROWS)


def _pinned_index():
    return [copy.deepcopy(row) for row in PINNED_ROWS]


def _read_capture():
    if not SNAPSHOT_PATH.exists():
        pytest.skip(
            f"the captured live section evidence is not present at {SNAPSHOT_PATH} — "
            "the full-capture assertions require it (the pinned-row assertions still run)"
        )
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256, (
        "the capture at /tmp does not match the sha256 this file pins — refusing to "
        "assert against unverified evidence"
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Repository-boundary doubles. Same shape as the matcher regression's, kept
# local so this file stands alone.
# ---------------------------------------------------------------------------
class _FakeExecuteResult:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _FakeQuery:
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
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _NetworkGuardClient:
    """Makes 'no network' structural: an unpatched matcher fails loudly here."""

    def table(self, name):  # pragma: no cover - only reached on a test bug
        raise AssertionError(
            f"no test in this file may touch a live Supabase client (asked for table "
            f"{name!r}); build the matcher through the injected repository double."
        )


class _RecordingRepo:
    """
    Stands in for app.engineering_data.repository — the persistence stage. It
    records exactly what production asked it to store, and invents nothing.
    """

    def __init__(self):
        self.calls = []
        self.inserted = []
        self.review_items = []
        self.project_summary = None
        self.captures = None

    def insert_page_extraction_captures(self, rows):
        # Milestone J23: production records the raw AI reading before it records any
        # row derived from it, so this call happens on every genuine path. Recorded
        # like the others; this file asserts about sections, not about captures.
        self.calls.append("insert_page_extraction_captures")
        self.captures = copy.deepcopy(rows)
        return len(rows)

    def insert_members(self, rows):
        self.calls.append("insert_members")
        self.inserted = copy.deepcopy(rows)
        # Every persisted column plus the generated id, as the real bulk insert
        # returns it (see the same double in the extraction-evidence harness).
        return [{"id": f"member-{i}", **copy.deepcopy(r)} for i, r in enumerate(rows)]

    def insert_review_items(self, items):
        self.calls.append("insert_review_items")
        self.review_items = copy.deepcopy(items)
        return items

    def update_drawing_meta(self, *args):
        self.calls.append("update_drawing_meta")

    def update_project_summary(self, project_id, **kwargs):
        self.calls.append("update_project_summary")
        self.project_summary = dict(kwargs)


# ---------------------------------------------------------------------------
# The CAD / fabrication boundary, armed to fail loudly if the production PDF
# path ever reaches it. These are the documented entry points the milestone
# drawing flows use — nothing here changes them.
# ---------------------------------------------------------------------------
CAD_BOUNDARY = (
    ("app.cad_engine.real_member_adapter", "real_member_to_validated_member"),
    ("app.cad_engine.automation_pipeline", "evaluate_reviewed_connection_for_automation"),
    ("app.cad_engine.fabrication_output_gate", "evaluate_fabrication_output_gate"),
    ("app.cad_engine.drawing_dispatch", "dispatch_fabrication_drawing"),
)


def _arm_cad_boundary(monkeypatch):
    def _refuse(*args, **kwargs):  # pragma: no cover - only reached on a regression
        raise AssertionError(
            "the PDF production pipeline reached the CAD/fabrication boundary — it must "
            "stop at persistence, and a refused section substitution must never be drawn."
        )

    for module_name, attribute in CAD_BOUNDARY:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, attribute, _refuse)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def modules():
    """
    The REAL production modules, imported once under a test-only configuration
    boundary (app/config.py reads os.environ at import time). The environment
    is restored immediately and every module this import adds to sys.modules is
    removed at teardown, so the pre-existing SUPABASE_URL smoke-import baseline
    is neither fixed nor worsened.
    """
    from types import SimpleNamespace

    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        pipeline = importlib.import_module("app.pipeline")
        rules = importlib.import_module("app.validation.rules")
        section_matcher = importlib.import_module("app.engineering_data.section_matcher")
        analyzer = importlib.import_module("app.ai_analysis.pdf_vision_analyzer")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_client = section_matcher.supabase
    section_matcher.supabase = _NetworkGuardClient()
    try:
        yield SimpleNamespace(
            pipeline=pipeline, rules=rules, section_matcher=section_matcher,
            analyzer=analyzer, PageExtraction=analyzer.PageExtraction,
        )
    finally:
        section_matcher.supabase = real_client
        # Restore the app's own import state, and ONLY the app's. Arming the CAD
        # boundary above pulls in cadquery/ezdxf/numpy, and a C extension cannot
        # be re-initialised in the same process — popping those would break the
        # very smoke-import baseline this teardown exists to protect.
        for name in set(sys.modules) - before:
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


class _ProductionRun:
    """Everything one real production run produced, for a test to assert on."""

    def __init__(self, summary, repo, matcher_double, issues, pages):
        self.summary = summary
        self.repo = repo
        self.matcher_double = matcher_double
        self.issues = issues
        self.pages = pages

    @property
    def inserted(self):
        return self.repo.inserted

    def inserted_for(self, mark):
        rows = [r for r in self.inserted if r["mark"] == mark]
        assert len(rows) == 1, f"expected exactly one persisted row for mark {mark!r}, got {len(rows)}"
        return rows[0]

    def issues_for_rule(self, rule):
        return [i for i in self.issues if i.rule == rule]

    @property
    def section_names(self):
        return [r["section_name"] for r in self.inserted]


@pytest.fixture()
def run_production(modules, monkeypatch):
    """
    run(raw_members) -> _ProductionRun, driving the REAL app/pipeline.py
    `_run_pipeline` with the REAL SectionMatcher, the REAL validation engine,
    and an injected repository double. The vision stage is the only thing
    replaced, and it is replaced with the real PageExtraction dataclass.
    """
    armed = {"cad": False}

    def run(raw_members, index_rows=None, pages=None):
        double = _FakeSupabaseClient(index_rows if index_rows is not None else _pinned_index())
        monkeypatch.setattr(modules.section_matcher, "supabase", double)

        if not armed["cad"]:
            _arm_cad_boundary(monkeypatch)
            armed["cad"] = True

        if pages is None:
            pages = [modules.PageExtraction(
                page_number=1, drawing_number="S101", drawing_title="Framing Plan",
                revision="A", raw_members=raw_members, raw_connections=[],
            )]
        monkeypatch.setattr(modules.pipeline, "analyze_pdf_pages", lambda *a, **k: pages)
        # Milestone J15: the simulated document is exactly the pages supplied,
        # so its coverage is complete and this file keeps testing the section
        # authority rules it is named for.
        monkeypatch.setattr(modules.pipeline, "page_count_of", lambda *a, **k: len(pages))

        repo = _RecordingRepo()
        monkeypatch.setattr(modules.pipeline, "repo", repo)

        issues = []
        real_validate = modules.pipeline.validate_extraction

        def recording_validate(raw, matcher):
            outcome = real_validate(raw, matcher)
            issues.extend(outcome["issues"])
            return outcome

        monkeypatch.setattr(modules.pipeline, "validate_extraction", recording_validate)

        summary = modules.pipeline._run_pipeline(
            "unused.pdf", "project-1", "user-1", "drawing-set-1", "drawing-1",
            analysis_run_id="run-1",
        )
        return _ProductionRun(summary, repo, double, issues, pages)

    return run


@pytest.fixture()
def real_matcher(modules, monkeypatch):
    """A genuine SectionMatcher, built by its own constructor against the injected rows."""
    monkeypatch.setattr(modules.section_matcher, "supabase", _FakeSupabaseClient(_pinned_index()))
    return modules.section_matcher.SectionMatcher()


def _member(section, mark="B1", page=1, length_mm=6000, confidence=95):
    return {
        "mark": mark, "section": section, "source_page": page,
        "length_mm": length_mm, "confidence": confidence,
    }


@pytest.fixture()
def captured_by_name():
    return {row["name"]: row for row in _read_capture()}


# ===========================================================================
# The two vocabularies are the same vocabulary
# ===========================================================================
class TestTheAuthorityRuleUsesTheMatchersOwnVocabulary:

    def test_rules_and_the_matcher_agree_on_the_fallback_spelling(self, modules):
        # rules.py compares a plain string rather than importing the matcher
        # module (which would drag app.config's import-time env read into every
        # validation test). That shortcut is safe only while the two spellings
        # are the same value — so that is asserted here, not assumed.
        assert modules.rules.SUFFIX_FALLBACK == modules.section_matcher.RESOLUTION_SUFFIX_FALLBACK

    def test_the_production_matcher_reports_a_resolution(self, real_matcher):
        assert hasattr(real_matcher, "resolve")
        assert real_matcher.resolve("310UB40").resolution == "SUFFIX_FALLBACK"

    def test_a_matcher_that_cannot_report_a_resolution_is_answered_the_legacy_way(self, modules):
        # The compatibility seam for the pre-existing match()-only test doubles.
        # It is asserted to be exactly that: no resolution, the row, nothing else.
        class _LegacyMatcher:
            def match(self, raw_name):
                return {"name": "310UB40.4", "family": "UB", "weight_per_metre": 40.4}

        resolution, token, row = modules.rules.match_member_section(_LegacyMatcher(), "310UB40")
        assert resolution is None
        assert token is None
        assert row["name"] == "310UB40.4"

    def test_the_legacy_branch_is_not_reachable_from_the_real_matcher(self, modules, real_matcher):
        resolution, token, row = modules.rules.match_member_section(real_matcher, "310UB40")
        assert resolution == modules.section_matcher.RESOLUTION_SUFFIX_FALLBACK
        assert resolution is not None  # production never takes the legacy branch
        assert token == "310UB40"
        assert row["name"] == "310UB40.4"


# ===========================================================================
# A — EXACT: unchanged identity, unchanged enrichment, unchanged review path
# ===========================================================================
class TestCaseAExactMatchIsUnchanged:

    def test_310ub46_2_keeps_its_identity(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1")])
        row = run.inserted_for("B1")
        assert row["section_name"] == "310UB46.2"
        assert row["section_name"] != "310UB40.4"

    def test_the_drawn_token_remains_recoverable_verbatim(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1")])
        assert run.inserted_for("B1")["section_name_raw"] == "310UB46.2"

    def test_exact_enrichment_still_happens(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1")])
        row = run.inserted_for("B1")
        assert row["section_family"] == "UB"
        assert row["weight_per_metre"] == 46.2
        # 6.0 m x 46.2 kg/m x 1
        assert row["total_weight_kg"] == 277.2

    def test_review_stays_on_the_existing_automatic_path(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1", confidence=95)])
        row = run.inserted_for("B1")
        assert row["review_status"] == "extracted"
        assert row["notes"] is None
        assert not run.issues_for_rule("SECTION_SUBSTITUTION_REFUSED")

    def test_exact_members_still_consolidate_across_agreeing_pages(self, run_production):
        run = run_production([
            _member("310UB46.2", mark="B1", page=1),
            _member("310UB46.2", mark="B1", page=2),
        ])
        row = run.inserted_for("B1")
        assert row["review_status"] == "extracted"
        assert run.summary["review_required_count"] == 0

    def test_an_exact_identity_is_byte_identical_to_the_pre_existing_rule(self, run_production):
        # The rule that used to write matched["name"] is unchanged wherever the
        # catalogue really answered for the drawn section: same section, same
        # spelling. 25x25x3EA is the witness — the catalogue spells it
        # lower-case-x while the matcher's canonical key is 25X25X3EA, and the
        # persisted name must stay the catalogue's, exactly as before.
        run = run_production([_member("25x25x3EA", mark="E1")])
        row = run.inserted_for("E1")
        assert row["section_name"] == "25x25x3EA"
        assert row["section_name"] != "25X25X3EA"
        assert row["weight_per_metre"] == 1.12

    def test_the_snapshot_confirms_the_case_witness_is_real(self, captured_by_name):
        assert captured_by_name["25x25x3EA"]["name"] == "25x25x3EA"


# ===========================================================================
# B — SUFFIX_FALLBACK: refused as identity and as properties
# ===========================================================================
class TestCaseBSuffixFallbackIsRefused:

    def test_the_drawn_token_is_the_authoritative_identity(self, run_production):
        # CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C). The drawn
        # token is still this member's identity — and still not the candidate's —
        # but it is now carried in section_name_raw: live section_name is
        # FK-bound to steel_sections(name), and "310UB40" is not a catalogue key,
        # so it cannot be written there without the FK rejecting it.
        run = run_production([_member("310UB40", mark="B1")])
        row = run.inserted_for("B1")
        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["section_name_raw"] != "310UB40.4"

    def test_no_persisted_row_claims_the_catalogue_section(self, run_production):
        run = run_production([
            _member("310UB40", mark="B1"),
            _member("310UB46.2", mark="B2"),
        ])
        assert "310UB40.4" not in run.section_names

    def test_the_drawn_token_is_still_recoverable_verbatim(self, run_production):
        run = run_production([_member("310UB40", mark="B1")])
        assert run.inserted_for("B1")["section_name_raw"] == "310UB40"

    def test_no_catalogue_properties_are_adopted(self, run_production):
        run = run_production([_member("310UB40", mark="B1")])
        row = run.inserted_for("B1")
        # The row that describes 310UB40.4 carries family UB and 40.4 kg/m.
        # Neither may appear on a member whose drawing said 310UB40.
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None

    def test_the_member_is_held_for_review(self, run_production):
        run = run_production([_member("310UB40", mark="B1", confidence=95)])
        row = run.inserted_for("B1")
        assert row["review_status"] == "review_required"
        assert run.summary["review_required_count"] == 1

    def test_review_is_required_even_when_many_pages_agree(self, run_production):
        # Pages agreeing on a section the catalogue does not carry is not
        # confirmation. Consolidation must not promote it.
        run = run_production([
            _member("310UB40", mark="B1", page=1, confidence=99),
            _member("310UB40", mark="B1", page=2, confidence=99),
            _member("310UB40", mark="B1", page=3, confidence=99),
        ])
        row = run.inserted_for("B1")
        assert row["review_status"] == "review_required"
        # Option C (J7): the drawn token survives in section_name_raw, and no
        # catalogue identity is claimed (see the class above).
        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["weight_per_metre"] is None

    def test_the_refusal_is_reported_under_its_own_rule(self, run_production):
        run = run_production([_member("310UB40", mark="B1")])
        refused = run.issues_for_rule("SECTION_SUBSTITUTION_REFUSED")
        assert len(refused) == 1
        assert refused[0].status == "REVIEW_REQUIRED"
        assert refused[0].severity == "HIGH"
        # ...and it is NOT reported as an ordinary unrecognised format.
        assert not run.issues_for_rule("UNRECOGNISED_SECTION_FORMAT")

    def test_the_message_names_both_the_drawn_token_and_the_refused_candidate(self, run_production):
        run = run_production([_member("310UB40", mark="B1")])
        message = run.issues_for_rule("SECTION_SUBSTITUTION_REFUSED")[0].message
        assert "'310UB40'" in message
        assert "'310UB40.4'" in message
        assert "refused" in message

    def test_the_candidate_survives_only_as_refused_provenance(self, run_production):
        run = run_production([_member("310UB40", mark="B1")])
        row = run.inserted_for("B1")
        assert "310UB40.4" in row["notes"]
        assert "NOT been adopted" in row["notes"]
        assert row["section_name"] != "310UB40.4"
        assert row["section_family"] is None

    def test_the_refusal_note_survives_page_consolidation(self, run_production):
        # consolidate_members rewrites validation_note when pages agree, so the
        # persisted note is composed at the persistence boundary instead.
        run = run_production([
            _member("310UB40", mark="B1", page=1),
            _member("310UB40", mark="B1", page=2),
        ])
        row = run.inserted_for("B1")
        assert "310UB40.4" in row["notes"]
        assert "NOT been adopted" in row["notes"]

    def test_it_is_absent_from_the_project_unmatched_list_and_present_as_a_unique_section(
        self, run_production
    ):
        run = run_production([_member("310UB40", mark="B1")])
        # Still NOT an unmatched section: the catalogue answered this token with
        # a different section, and the engineer was told which. Since Milestone
        # J7 that exclusion is made explicit in app/pipeline.py, because a
        # refused substitution now also leaves section_name NULL and would
        # otherwise be indistinguishable from NONE there.
        assert run.repo.project_summary["unmatched_sections"] == []
        # A unique SECTION is a catalogue identity, and this row claims none
        # (J7 Option C), so the project reports zero unique sections rather than
        # counting the unverified drawn token as one.
        assert run.repo.project_summary["total_unique_sections"] == 0

    def test_a_fallback_does_not_confirm_a_section_the_catalogue_does_carry(
        self, run_production
    ):
        # The same project must still enrich the section the catalogue really
        # answered for — the refusal is targeted, not a blanket downgrade.
        run = run_production([
            _member("310UB40", mark="B1"),
            _member("310UB46.2", mark="B2"),
        ])
        assert run.inserted_for("B1")["weight_per_metre"] is None
        assert run.inserted_for("B2")["weight_per_metre"] == 46.2


# ===========================================================================
# C — NONE: the existing unmatched / review path, unchanged
# ===========================================================================
class TestCaseCUnknownSectionKeepsExistingBehaviour:

    def test_the_drawn_token_is_still_recoverable(self, run_production):
        run = run_production([_member("250X90PFC", mark="B1")])
        assert run.inserted_for("B1")["section_name_raw"] == "250X90PFC"

    def test_section_name_stays_unset_as_it_always_has(self, run_production):
        # Unchanged on purpose: the existing architecture identifies "no
        # section_name at all" as the unmatched state, and app/pipeline.py's
        # unmatched_sections roll-up is built on exactly that.
        run = run_production([_member("250X90PFC", mark="B1")])
        assert run.inserted_for("B1")["section_name"] is None
        assert run.repo.project_summary["unmatched_sections"] == ["250X90PFC"]

    def test_no_catalogue_enrichment_occurs(self, run_production):
        run = run_production([_member("250X90PFC", mark="B1")])
        row = run.inserted_for("B1")
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None

    def test_it_takes_the_existing_unmatched_review_path(self, run_production):
        run = run_production([_member("250X90PFC", mark="B1", confidence=95)])
        assert run.inserted_for("B1")["review_status"] == "review_required"
        assert len(run.issues_for_rule("UNRECOGNISED_SECTION_FORMAT")) == 1

    def test_it_is_not_reported_as_a_refused_substitution(self, run_production):
        run = run_production([_member("250X90PFC", mark="B1")])
        assert not run.issues_for_rule("SECTION_SUBSTITUTION_REFUSED")

    def test_the_catalogue_does_not_bridge_it_to_250pfc(self, run_production):
        run = run_production([_member("250X90PFC", mark="B1")])
        row = run.inserted_for("B1")
        assert row["section_name"] != "250PFC"
        assert row["weight_per_metre"] is None


# ===========================================================================
# D — Regression: a whole project behaves as it did, per member
# ===========================================================================
class TestCaseDWholeProjectRegression:

    def test_the_three_cases_in_one_run(self, run_production):
        run = run_production([
            _member("310UB46.2", mark="B1"),
            _member("310UB40", mark="B2"),
            _member("250X90PFC", mark="B3"),
        ])
        assert run.inserted_for("B1")["section_name"] == "310UB46.2"
        assert run.inserted_for("B1")["review_status"] == "extracted"
        # Option C (J7): B2's drawn token 310UB40 is a catalogue MISS, so it is
        # carried in section_name_raw and the FK-bound column stays NULL.
        assert run.inserted_for("B2")["section_name"] is None
        assert run.inserted_for("B2")["section_name_raw"] == "310UB40"
        assert run.inserted_for("B2")["review_status"] == "review_required"
        assert run.inserted_for("B3")["section_name"] is None
        assert run.inserted_for("B3")["review_status"] == "review_required"
        assert run.summary["members_extracted"] == 3
        assert run.summary["review_required_count"] == 2

    def test_the_project_weight_excludes_the_refused_member(self, run_production):
        run = run_production([
            _member("310UB46.2", mark="B1"),
            _member("310UB40", mark="B2"),
        ])
        # Only the confirmed member contributes: 6.0 m x 46.2 kg/m.
        assert run.summary["total_weight_kg"] == 277.2

    def test_confidence_review_still_fires_on_its_own_terms(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1", confidence=40)])
        row = run.inserted_for("B1")
        assert row["review_status"] == "review_required"
        assert row["weight_per_metre"] == 46.2

    def test_every_review_item_matches_its_members_status(self, run_production):
        run = run_production([
            _member("310UB46.2", mark="B1"),
            _member("310UB40", mark="B2"),
        ])
        # Option C (J7): the identity a member is keyed by is the drawn one in
        # section_name_raw — B2 has no catalogue identity at all.
        by_mark = {r["mark"]: r["section_name_raw"] for r in run.inserted}
        assert by_mark == {"B1": "310UB46.2", "B2": "310UB40"}
        assert run.inserted_for("B2")["section_name"] is None
        assert len(run.repo.review_items) == 2
        assert {i["status"] for i in run.repo.review_items} == {"extracted", "review_required"}

    def test_the_same_three_cases_against_the_full_captured_snapshot(self, run_production):
        run = run_production(
            [_member("310UB46.2", mark="B1"), _member("310UB40", mark="B2"),
             _member("250X90PFC", mark="B3")],
            index_rows=_read_capture(),
        )
        assert len(run.matcher_double.rows) == SNAPSHOT_ROW_COUNT
        assert run.inserted_for("B1")["section_name"] == "310UB46.2"
        # Option C (J7) — and now over the FULL captured snapshot: the refused
        # B2 and the unresolved B3 both leave the FK-bound column NULL, and are
        # told apart by their recorded resolution rather than by that column.
        assert run.inserted_for("B2")["section_name"] is None
        assert run.inserted_for("B2")["section_name_raw"] == "310UB40"
        assert run.inserted_for("B2")["section_resolution"] == "SUFFIX_FALLBACK"
        assert run.inserted_for("B3")["section_name"] is None
        assert run.inserted_for("B3")["section_resolution"] == "NONE"


# ===========================================================================
# The real production matcher was exercised — not a double, not a bypass
# ===========================================================================
class TestTheRealProductionPathWasExercised:

    def test_the_matcher_was_built_by_the_real_constructor_against_injected_rows(
        self, run_production
    ):
        run = run_production([_member("310UB46.2")])
        assert run.matcher_double.queries == [("steel_sections", "*")]
        assert run.matcher_double.executed == 1

    def test_no_further_query_is_made_while_members_are_resolved(self, run_production):
        run = run_production([
            _member("310UB46.2", mark="B1"),
            _member("310UB40", mark="B2"),
            _member("250X90PFC", mark="B3"),
        ])
        assert run.matcher_double.executed == 1
        assert run.matcher_double.queries == [("steel_sections", "*")]

    def test_an_unpatched_client_would_fail_loudly_rather_than_reach_the_network(self, modules):
        with pytest.raises(AssertionError, match="may touch a live Supabase client"):
            modules.section_matcher.supabase.table("steel_sections")

    def test_the_persistence_stage_really_ran(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1")])
        # J23 put the capture first in this order, deliberately: the page's raw
        # AI reading is persisted before anything derived from it, so the record
        # of what the model returned can never depend on the engineering write
        # having succeeded first. The call is present only because this harness
        # supplies an analysis_run_id — a capture is attributed to a run.
        assert run.repo.calls == [
            "insert_page_extraction_captures",
            "insert_members", "insert_review_items", "update_drawing_meta", "update_project_summary",
        ]


# ===========================================================================
# Fail closed: nothing reaches CAD or fabrication from this path
# ===========================================================================
class TestNoCadOrFabricationIsReached:

    def test_pipeline_imports_nothing_from_the_cad_engine(self):
        # The structural half of the proof: app/pipeline.py's own imports are
        # listed by AST, and none of them is a CAD/fabrication module. There is
        # no path from _run_pipeline into the drawing chain.
        tree = ast.parse((REPO / "app" / "pipeline.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            name for name in imported
            if "cad_engine" in name or name.endswith("dxf_parser")
        }
        assert forbidden == set(), f"app/pipeline.py imports CAD/fabrication code: {sorted(forbidden)}"

    def test_pipeline_calls_none_of_the_cad_boundary_entry_points(self):
        # ...and not by a back door either: no call in app/pipeline.py targets
        # any of the armed boundary functions, however it might reach them.
        tree = ast.parse((REPO / "app" / "pipeline.py").read_text())
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        } | {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        boundary_names = {attribute for _, attribute in CAD_BOUNDARY}
        assert called & boundary_names == set()

    def test_the_cad_boundary_guards_are_live(self, modules, monkeypatch):
        # Proves the guards used below are not vacuous.
        _arm_cad_boundary(monkeypatch)
        module = importlib.import_module("app.cad_engine.real_member_adapter")
        with pytest.raises(AssertionError, match="reached the CAD/fabrication boundary"):
            module.real_member_to_validated_member(None)

    def test_a_fallback_member_produces_no_fabrication_artifact(self, run_production):
        # The behavioural half: the whole real production path runs to
        # completion with every CAD/fabrication entry point armed to raise. Had
        # a drawing or fabrication artifact been generated, the armed function
        # would have fired instead of this assertion being reached.
        run = run_production([_member("310UB40", mark="B1")])
        assert run.inserted_for("B1")["review_status"] == "review_required"
        # The run's only outputs are database rows: no artifact path is
        # produced, and the drawing generator was never entered.
        assert not any("path" in key or "artifact" in key or "drawing" in key for key in run.summary)

    def test_an_exact_member_also_produces_no_fabrication_artifact(self, run_production):
        # Stated so the proof is about the PATH, not about the fallback alone:
        # this production function stops at persistence either way.
        run = run_production([_member("310UB46.2", mark="B1")])
        assert run.inserted_for("B1")["review_status"] == "extracted"

    def test_the_status_that_gates_downstream_drawing_is_review_required(self, run_production):
        # The existing fail-closed mechanism: the downstream fabrication gate
        # (app/cad_engine/fabrication_output_gate.py) grants AUTO only over a
        # confirmed record. A refused substitution is never that, so the same
        # status that holds a member for an engineer is the one that withholds
        # the drawing.
        run = run_production([
            _member("310UB40", mark="B1"),
            _member("310UB46.2", mark="B2"),
        ])
        # Option C (J7): keyed by the DRAWN token, since the refused member no
        # longer populates section_name. The statuses — the thing this test is
        # about, and what the downstream gates read — are unchanged.
        assert {r["section_name_raw"]: r["review_status"] for r in run.inserted} == {
            "310UB40": "review_required",
            "310UB46.2": "extracted",
        }
        # The review queue the downstream gates read carries exactly those
        # statuses, in the same order as the members they belong to.
        assert [i["status"] for i in run.repo.review_items] == [
            r["review_status"] for r in run.inserted
        ]


# ===========================================================================
# The refusal is not a blanket downgrade — the catalogue still enriches
# ===========================================================================
class TestTheCatalogueStillEnrichesWhereItMay:

    def test_a_timber_member_is_still_excluded_exactly_as_before(self, run_production):
        run = run_production([
            _member("2/190x45 SG8 H3.2", mark="M10"),
            _member("310UB46.2", mark="B1"),
        ])
        assert run.summary["excluded_non_steel"] == 1
        assert "M10" not in {r["mark"] for r in run.inserted}

    def test_a_missing_length_is_still_not_a_fabricated_zero(self, run_production):
        run = run_production([_member("310UB46.2", mark="B1", length_mm=None)])
        row = run.inserted_for("B1")
        assert row["length_mm"] is None
        assert row["total_weight_kg"] is None
        assert row["weight_per_metre"] == 46.2
