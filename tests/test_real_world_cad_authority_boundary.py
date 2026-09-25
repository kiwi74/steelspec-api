"""
CAD SECTION AUTHORITY BOUNDARY — a catalogue lookup may confirm a member's
section identity; it may never change it.

WHAT THIS FILE PROVES

app/cad_engine/real_member_adapter.py:103 performed its own catalogue lookup on
the member's recorded section identity:

    section_properties = matcher.match(section_name)

For the overwhelming majority of members that is a harmless confirmation — the
catalogue answers with the very row the member already names. But `match()`
resolves by suffix fallback, so for a member whose section is "310UB40" it
answers with the "310UB40.4" row, and the adapter built that different section's
geometry, silently, well before its own "Refusing to substitute or approximate
another section" guard was ever reached. That is an authority leak: the CAD
engine re-opened a question production had already answered.

The rule now enforced, in that one place (`_authorised_catalogue_row`):

    a lookup at this boundary must answer FOR the member's already-decided
    section identity. If it answers with a DIFFERENT section, or reports that
    it got there by anything other than an exact hit, the member is refused
    with the module's existing GeometryValidationError vocabulary. No
    substitution, no approximation, no invented E1 verdict.

THE RULE IS STRUCTURAL, NOT A LIST OF NAMES. It is not "reject 310UB40". The
sweep at the end of this file derives every substitution trap in the live
218-row catalogue MECHANICALLY — 35 of them, every UB and UC callout a real
drawing habitually writes without its decimal ("310UB40", "310UB46", "200UC46",
...), each one answered by the catalogue with a different section — and proves
each is refused, naming the row that was refused. Alongside it, all 218 sections
are swept to prove none can ever be accepted as a different section. A member
row is only ever accepted when the catalogue answered with the section that
member already was.

REAL PRODUCTION, NOT A HAND-BUILT SHORTCUT. The genuine captured rows are
pinned verbatim below and the genuine SectionMatcher is built by its own
constructor against an injected repository double (no network, no credentials);
the end-to-end case drives the REAL app/pipeline.py `_run_pipeline` and feeds the
row it genuinely persisted into the REAL adapter.

SCOPE: this milestone closes the CAD boundary's second lookup only. The E1
source-of-truth question (is the fallback candidate maybe the right section after
all, on the drawing's own evidence?) is deliberately not answered here, exactly
as the previous milestone stated — a fallback is refused, not adjudicated.
"""

import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedSteelMember, generate_geometry
from app.cad_engine.real_member_adapter import (
    _identity_key,  # the drift-guarded identity relation, used by the sweeps below
    real_member_to_validated_member,
)

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# The captured live evidence this file is anchored to — the same 218-row
# capture the matcher regression, the production authority milestone and the E1
# tests all pin.
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
# Pinned VERBATIM from the capture — the same four rows the production
# authority milestone pins. 310UB46.2 and 310UB40.4 are the two sections the
# suffix-fallback confusion is about; 25x25x3EA is the case/whitespace witness
# (the catalogue spells it lower-case-x while the matcher's canonical form is
# "25X25X3EA" — an exact match must NOT re-spell it, and the identity rule must
# NOT treat the spelling difference as a substitution).
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

# The families app/cad_engine/sections.py actually admits to a profile builder —
# used only to explain WHY a given refusal is a family refusal rather than an
# authority refusal. Never re-listed as the source of truth for support.
#
# "FLAT" is listed here as a SOURCE family value (what steel_sections.family
# actually stores), not as a builder key: Milestone J1 admitted it through
# sections.CAD_FAMILY_PROJECTION, which is why the live sweep below now accepts
# the 23 real flat-bar rows this set previously saw refused as unsupported.
SUPPORTED_FAMILIES = frozenset(
    {"PFC", "UB", "SHS", "RHS", "PL", "FL", "FLAT", "EA"}
)


def _pinned_index():
    return [copy.deepcopy(row) for row in PINNED_ROWS]


def _read_capture():
    if not SNAPSHOT_PATH.exists():
        pytest.skip(
            f"the captured live section evidence is not present at {SNAPSHOT_PATH} — "
            "the catalogue-wide sweep requires it (the pinned-row proofs still run)"
        )
    raw = SNAPSHOT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SNAPSHOT_SHA256, (
        "the capture at /tmp does not match the sha256 this file pins — refusing to "
        "assert against unverified evidence"
    )
    rows = json.loads(raw)
    assert len(rows) == SNAPSHOT_ROW_COUNT, (
        f"the capture holds {len(rows)} rows; this file's proofs are anchored to "
        f"{SNAPSHOT_ROW_COUNT}."
    )
    return rows


# ---------------------------------------------------------------------------
# Repository-boundary doubles. Same shape as the production authority
# milestone's, kept local so this file stands alone.
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
    """Stands in for app.engineering_data.repository: records what production
    asked it to store, and invents nothing."""

    def __init__(self):
        self.inserted = []
        self.calls = []
        self.captures = None

    def insert_page_extraction_captures(self, rows):
        # Milestone J23: production records the raw AI reading before it records any
        # row derived from it, so this call happens on every genuine path. Recorded
        # like the others; this file asserts about members, not about captures.
        self.calls.append("insert_page_extraction_captures")
        self.captures = copy.deepcopy(rows)
        return len(rows)

    def insert_members(self, rows):
        self.calls.append("insert_members")
        self.inserted = copy.deepcopy(rows)
        return [
            {"id": f"member-{i}", "mark": r.get("mark"), "review_status": r.get("review_status")}
            for i, r in enumerate(rows)
        ]

    def insert_review_items(self, items):
        self.calls.append("insert_review_items")
        return items

    def update_drawing_meta(self, *args):
        self.calls.append("update_drawing_meta")

    def update_project_summary(self, project_id, **kwargs):
        self.calls.append("update_project_summary")


# ---------------------------------------------------------------------------
# Matchers used to isolate each half of the rule. All of them are the shapes
# this codebase already uses: a transparent proxy over the genuine matcher, and
# a plain match()-only double (what every pre-existing test double is).
# ---------------------------------------------------------------------------
class _RecordingMatcher:
    """
    A transparent proxy over a GENUINE SectionMatcher that records every
    question the adapter asks it — so "one lookup, with the member's own decided
    identity" is asserted rather than assumed.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def resolve(self, raw_name):
        self.calls.append(("resolve", raw_name))
        return self._inner.resolve(raw_name)

    def match(self, raw_name):
        self.calls.append(("match", raw_name))
        return self._inner.match(raw_name)


class _ReportingMatcher:
    """
    Reports a chosen resolution for a chosen row — the only way to test the two
    halves of the rule independently. Uses the REAL SectionMatch record, so the
    contract's own invariants still apply to it.
    """

    def __init__(self, section_match_cls, resolution, row):
        self._cls = section_match_cls
        self._resolution = resolution
        self._row = row

    def resolve(self, raw_name):
        return self._cls(
            drawing_token=raw_name, drawing_token_raw=raw_name,
            catalogue_row=self._row, resolution=self._resolution,
        )


class _UnknownResolutionMatcher:
    """
    Reports a resolution this build of the boundary cannot recognise at all.
    SectionMatch itself refuses to construct such a record (that is one of its
    invariants), so this is a duck-typed stand-in — the only way to test that an
    unrecognised resolution fails closed rather than being assumed harmless.
    """

    def __init__(self, row):
        self._row = row

    def resolve(self, raw_name):
        return SimpleNamespace(
            resolution="SOME_FUTURE_RESOLUTION_THIS_BUILD_DOES_NOT_KNOW",
            catalogue_row=self._row,
            drawing_token=raw_name,
            drawing_token_raw=raw_name,
        )


class _AnsweringMatcher:
    """
    A match()-only matcher — the shape every pre-existing double in this
    codebase has — that answers ANY name with one fixed row. Used to prove the
    identity rule holds for callers that cannot report a resolution.
    """

    def __init__(self, row):
        self._row = row
        self.calls = []

    def match(self, raw_name):
        self.calls.append(raw_name)
        return dict(self._row) if self._row is not None else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def modules():
    """
    The REAL production modules that need a configured environment to import
    (app/config.py reads os.environ at import time). The environment is restored
    immediately; at teardown only the app's own modules are removed, because
    popping a C extension (cadquery/numpy) breaks it for the rest of the
    process — the same rule the production authority milestone established.
    """
    saved = {k: os.environ.get(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        section_matcher = importlib.import_module("app.engineering_data.section_matcher")
        pipeline = importlib.import_module("app.pipeline")
        adapter = importlib.import_module("app.cad_engine.real_member_adapter")
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
            section_matcher=section_matcher, pipeline=pipeline, adapter=adapter,
            PageExtraction=analyzer.PageExtraction,
        )
    finally:
        section_matcher.supabase = real_client
        for name in set(sys.modules) - before:
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)


@pytest.fixture()
def make_real_matcher(modules, monkeypatch):
    """build(index_rows=None) -> a GENUINE SectionMatcher, built by its own
    constructor against the injected rows. Defaults to the four pinned rows."""

    def build(index_rows=None):
        monkeypatch.setattr(
            modules.section_matcher, "supabase",
            _FakeSupabaseClient(_pinned_index() if index_rows is None else index_rows),
        )
        return modules.section_matcher.SectionMatcher()

    return build


@pytest.fixture()
def captured_rows():
    return _read_capture()


@pytest.fixture()
def persisted_row(modules, make_real_matcher, monkeypatch):
    """
    run(section_token) -> the row app/pipeline.py GENUINELY persisted for a
    drawing that stated that section. The real `_run_pipeline` runs end to end;
    only the vision stage is replaced, and it is replaced with the real
    PageExtraction dataclass.
    """

    def run(section_token, mark="B1"):
        make_real_matcher()  # puts the pinned index under the pipeline's own SectionMatcher()
        pages = [modules.PageExtraction(
            page_number=1, drawing_number="S101", drawing_title="Framing Plan",
            revision="A",
            raw_members=[{
                "mark": mark, "section": section_token, "source_page": 1,
                "length_mm": 6000, "confidence": 95,
            }],
            raw_connections=[],
        )]
        monkeypatch.setattr(modules.pipeline, "analyze_pdf_pages", lambda *a, **k: pages)
        # Milestone J15: the simulated document has exactly the one page above,
        # so its coverage is complete. This file is about the CAD authority
        # boundary, and it must not start failing for a coverage reason.
        monkeypatch.setattr(modules.pipeline, "page_count_of", lambda *a, **k: 1)
        repo = _RecordingRepo()
        monkeypatch.setattr(modules.pipeline, "repo", repo)

        modules.pipeline._run_pipeline(
            "unused.pdf", "project-1", "user-1", "drawing-set-1", "drawing-1",
            analysis_run_id="run-1",
        )
        rows = [r for r in repo.inserted if r["mark"] == mark]
        assert len(rows) == 1, f"expected exactly one persisted row for {mark!r}, got {len(rows)}"
        return rows[0]

    return run


def _member_row(section_name, **overrides):
    """
    A real `steel_members` row — the shape app/pipeline.py's `_run_pipeline`
    builds. `review_status` defaults to "approved" deliberately: every test
    below must be refused (or accepted) on the SECTION rule alone, never
    because an unrelated precondition happened to fire first.
    """
    row = {
        "mark": "CAD-AUTH-001",
        "section_name": section_name,
        "section_name_raw": section_name,
        "section_family": None,
        "length_mm": 4000.0,
        "grade": "300PLUS",
        "quantity": 1,
        "review_status": "approved",
        "source_page": 1,
        "source_drawing_id": "CAD-AUTH-TEST",
    }
    row.update(overrides)
    return row


def _identity(name):
    """The adapter's own identity relation — reused, never re-derived here."""
    return _identity_key(name)


# ===========================================================================
# The boundary's vocabulary is the matcher's own vocabulary
# ===========================================================================
class TestTheBoundaryUsesTheMatchersOwnVocabulary:

    def test_the_exact_spelling_is_the_matchers_own(self, modules):
        # The adapter compares a plain string rather than importing the matcher
        # module (which would drag app.config's import-time env read into every
        # CAD test). Safe only while the two spellings are the same value — so
        # that is asserted here, not assumed.
        assert modules.adapter.RESOLUTION_EXACT == modules.section_matcher.RESOLUTION_EXACT

    def test_the_identity_relation_is_the_matchers_own_normalisation(self, modules):
        # _identity_key must decide "same section, spelled differently?" exactly
        # as the catalogue decides it, or the rule would refuse (or accept)
        # sections the matcher itself would not.
        normalise = modules.section_matcher.SectionMatcher._normalise
        for token in (
            "310UB46.2", "310ub46.2", " 310UB46.2 ", "310 UB46.2",
            "25x25x3EA", "25X25X3EA", "250PFC", "310UB40", "310UB40.4",
        ):
            assert modules.adapter._identity_key(token) == normalise(None, token)

    def test_the_identity_relation_is_equality_only(self, modules):
        adapter = modules.adapter
        # Same section, different spelling -> the same identity.
        assert adapter._denotes_the_same_section("25X25X3EA", "25x25x3EA")
        assert adapter._denotes_the_same_section(" 310UB46.2 ", "310ub46.2")
        # A different section is never the same identity — there is no fallback
        # here, no candidate generation, nothing that could make it so.
        assert not adapter._denotes_the_same_section("310UB40.4", "310UB40")
        assert not adapter._denotes_the_same_section("310UB46.2", "310UB40")
        assert not adapter._denotes_the_same_section(None, "310UB40.")
        assert not adapter._denotes_the_same_section("", "310UB40")


# ===========================================================================
# 1 + 5. An EXACT member still works, and keeps its exact identity
# ===========================================================================
class TestAnExactMemberIsUnchanged:

    def test_an_exact_member_still_reaches_a_genuine_cad_solid(self, make_real_matcher):
        matcher = make_real_matcher()
        validated = real_member_to_validated_member(_member_row("310UB46.2"), matcher)

        assert isinstance(validated, ValidatedSteelMember)
        assert validated.section == "310UB46.2"
        assert validated.section_properties["name"] == "310UB46.2"

        geometry = generate_geometry(validated)
        assert geometry.section_name == "310UB46.2"
        assert geometry.section_family == "UB"
        assert geometry.length_mm == 4000.0
        assert len(geometry.solid.solids().vals()) == 1

        bbox = geometry.solid.val().BoundingBox()
        assert abs(bbox.zlen - 4000.0) < 1e-6
        assert abs(bbox.xlen - LIVE_310UB46_2["flange_width"]) < 1e-6  # 166
        assert abs(bbox.ylen - LIVE_310UB46_2["depth"]) < 1e-6          # 307

    def test_the_geometry_identity_is_the_exact_section_and_no_other(self, make_real_matcher):
        geometry = generate_geometry(
            real_member_to_validated_member(_member_row("310UB46.2"), make_real_matcher())
        )

        # The two neighbouring sections differ in their real dimensions, and the
        # substituted one IS in the index the adapter was given — so reading the
        # built solid back actually discriminates them, rather than passing
        # against an empty catalogue.
        assert LIVE_310UB46_2["flange_width"] != LIVE_310UB40_4["flange_width"]
        assert LIVE_310UB46_2["depth"] != LIVE_310UB40_4["depth"]

        assert geometry.section_name == "310UB46.2"
        bbox = geometry.solid.val().BoundingBox()
        assert abs(bbox.xlen - LIVE_310UB46_2["flange_width"]) < 1e-6   # 166, not 165
        assert abs(bbox.ylen - LIVE_310UB46_2["depth"]) < 1e-6          # 307, not 304

    def test_a_case_and_whitespace_variant_is_still_the_same_identity(self, make_real_matcher):
        # 167 of the 218 catalogue names are not in the matcher's canonical
        # form — the rule must not mistake the catalogue's own house spelling
        # for a substitution. This member was decided as "25x25x3EA" and the
        # catalogue carries it under exactly that spelling.
        matcher = make_real_matcher()
        validated = real_member_to_validated_member(_member_row("25x25x3EA"), matcher)
        assert validated.section == "25x25x3EA"
        assert validated.section_properties["name"] == "25x25x3EA"

    def test_an_exact_member_through_the_pipeline_keeps_its_decision(self, persisted_row):
        # The row production actually persisted for a drawing stating 310UB46.2.
        row = persisted_row("310UB46.2")
        assert row["section_name"] == "310UB46.2"
        assert row["review_status"] == "extracted"
        assert row["section_family"] == "UB"
        assert row["weight_per_metre"] == 46.2


# ===========================================================================
# 2. A SUFFIX_FALLBACK member is refused, structurally
# ===========================================================================
class TestASuffixFallbackMemberIsRefused:

    def test_the_trap_is_real_in_the_pinned_index(self, make_real_matcher):
        # Establishes the premise of every refusal test below: a member whose
        # section is "310UB40" WOULD have been answered with the 310UB40.4 row
        # by the very matcher the adapter is given. This is not a hypothetical.
        matcher = make_real_matcher()
        assert matcher.match("310UB40")["name"] == "310UB40.4"
        assert matcher.resolve("310UB40").resolution == "SUFFIX_FALLBACK"

    def test_the_adapter_refuses_a_fallback_preserved_section(self, make_real_matcher):
        with pytest.raises(GeometryValidationError) as excinfo:
            real_member_to_validated_member(_member_row("310UB40"), make_real_matcher())
        # Refused on the SECTION rule, not on some unrelated precondition.
        message = str(excinfo.value)
        assert "310UB40" in message
        assert "never change it" in message

    def test_no_substituted_section_geometry_can_be_reached(self, make_real_matcher):
        # The adapter is the only producer of the object generate_geometry()
        # consumes, and it produced nothing — so there is no path by which
        # 310UB40.4 geometry could be built for this member.
        with pytest.raises(GeometryValidationError):
            real_member_to_validated_member(_member_row("310UB40"), make_real_matcher())
        # And the trap that would have produced it is still live in the index:
        assert make_real_matcher().match("310UB40")["name"] == "310UB40.4"

    def test_it_is_refused_even_when_the_member_looks_fully_approved(self, make_real_matcher):
        # review_status="approved" (the fixture's default) is exactly the state
        # in which the old code built the substituted geometry. The authority
        # rule must not depend on the review flag happening to be set. The
        # refusal here is the matcher's own report of HOW it got there — the
        # more precise of the two statements, and the one a resolving matcher
        # makes. (Class 4 isolates the identity half of the rule on its own,
        # for matchers that cannot report a resolution at all.)
        row = _member_row("310UB40", review_status="approved", length_mm=4000.0)
        with pytest.raises(GeometryValidationError, match="SUFFIX_FALLBACK"):
            real_member_to_validated_member(row, make_real_matcher())

    def test_the_refusal_names_both_identities_so_a_human_can_act(self, make_real_matcher):
        with pytest.raises(GeometryValidationError) as excinfo:
            real_member_to_validated_member(_member_row("310UB40"), make_real_matcher())
        message = str(excinfo.value)
        assert "'310UB40'" in message      # what the member is
        assert "310UB40.4" in message      # what the catalogue offered, and was refused

    def test_the_real_arkles_fallback_case_is_refused_too(self, make_real_matcher):
        # "310UB46" -> "310UB46.2" is the same defect on the real Arkles Strand
        # data (the previous milestone's "310UB46.2 identity" fixture is the
        # substituted name this produced). Nothing about the rule is specific to
        # one section size.
        matcher = make_real_matcher()
        assert matcher.match("310UB46")["name"] == "310UB46.2"
        with pytest.raises(GeometryValidationError, match="310UB46.2"):
            real_member_to_validated_member(_member_row("310UB46"), matcher)

    def test_the_genuine_persisted_row_is_refused_by_the_pipeline_s_own_flag(self, persisted_row, make_real_matcher):
        # End to end: the production pipeline persists this member with
        # review_status = review_required. That row, fed to the real adapter, is
        # refused.
        #
        # CONTRACT CHANGED DELIBERATELY BY MILESTONE J7 (Option C). Before J7
        # the pipeline wrote section_name = the DRAWING's token here. It no
        # longer may: section_name is FK-bound to steel_sections(name), and
        # "310UB40" is not a key of that table — the refused row is 310UB40.4.
        # The member's drawn identity now lives in section_name_raw, and its
        # section_name is NULL. Everything this test is about is unchanged: the
        # row still carries the drawing's own section in raw, still refuses the
        # catalogue's candidate as identity AND as properties, and is still held
        # for review, which is what the adapter refuses.
        row = persisted_row("310UB40")
        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["review_status"] == "review_required"
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None

        with pytest.raises(GeometryValidationError, match="review_required"):
            real_member_to_validated_member(row, make_real_matcher())

    def test_the_genuine_persisted_row_is_refused_by_the_authority_rule_itself(self, persisted_row, make_real_matcher):
        # The same genuine row, with only the review flag cleared — i.e. the
        # state a human approval would produce. The section rule must still hold
        # on its own, or the boundary would depend on a flag set somewhere else
        # rather than on the authority rule itself.
        row = dict(persisted_row("310UB40"))
        row["review_status"] = "approved"

        # The row's own identity is the DRAWING's token, carried in
        # section_name_raw (CONTRACT CHANGED DELIBERATELY BY MILESTONE J7,
        # Option C: section_name is FK-bound and may hold only a catalogue key,
        # which "310UB40" is not — so it is NULL here). The substituted name
        # survives only as prose in `notes`, plus the provenance column that
        # exists to record the refusal. Nowhere does the row claim to be
        # 310UB40.4 as its identity.
        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        assert "310UB40.4" in (row["notes"] or "")

        with pytest.raises(GeometryValidationError) as excinfo:
            real_member_to_validated_member(row, make_real_matcher())
        # The refusal is unchanged; what names it is not. Before J7 this row
        # carried "310UB40" in section_name, so the catalogue rule fired later
        # and its message could name the candidate it would not accept
        # (310UB40.4). Under Option C the row carries NULL — the FK-safe value —
        # so the boundary refuses one step earlier, for the reason that is now
        # literally true of the row: no matched section name is recorded. The
        # candidate is not lost, it is provenance (asserted on `notes` above);
        # it is simply not this member's identity, and never becomes it.
        assert "no matched section name" in str(excinfo.value)
        assert "310UB40.4" not in str(excinfo.value)


# ===========================================================================
# 3. Another section can never be adopted, by any matcher
# ===========================================================================
class TestAnotherSectionIsNeverAdopted:

    def test_an_unrelated_catalogue_section_is_not_adopted(self, make_real_matcher):
        # The index carries an unrelated UB and nothing else — the member's own
        # section is not in it. The existing "not a recognised entry" refusal
        # stands, unchanged: an unmatched section is never filled in from a
        # neighbour.
        matcher = make_real_matcher([LIVE_310UB46_2])
        assert matcher.match("310UB40") is None
        with pytest.raises(GeometryValidationError, match="not a recognised entry"):
            real_member_to_validated_member(_member_row("310UB40"), matcher)

    def test_a_match_only_matcher_answering_with_another_section_is_refused(self):
        # A matcher that cannot report a resolution is not exempt from the rule.
        # This is the shape every pre-existing double in this codebase has.
        matcher = _AnsweringMatcher(LIVE_310UB46_2)
        with pytest.raises(GeometryValidationError, match="DIFFERENT section"):
            real_member_to_validated_member(_member_row("310UB40"), matcher)
        assert matcher.calls == ["310UB40"]  # asked once, with the member's own identity

    def test_a_match_only_matcher_answering_with_the_substitution_is_refused(self):
        matcher = _AnsweringMatcher(LIVE_310UB40_4)
        with pytest.raises(GeometryValidationError, match="310UB40.4"):
            real_member_to_validated_member(_member_row("310UB40"), matcher)

    def test_the_production_shaped_arkles_double_is_refused(self):
        # tests/test_arkles_strand.py's RealisticFakeMatcher is a genuine,
        # pre-existing, production-shaped double with its own prefix fallback:
        # it answers "310UB46" with the "310UB46.2" row. Before this milestone
        # the adapter would have built that section from it.
        from tests.test_arkles_strand import RealisticFakeMatcher

        matcher = RealisticFakeMatcher()
        assert matcher.match("310UB46")["name"] == "310UB46.2"
        with pytest.raises(GeometryValidationError) as excinfo:
            real_member_to_validated_member(_member_row("310UB46"), matcher)
        assert "310UB46.2" in str(excinfo.value)

    def test_a_match_only_matcher_answering_correctly_still_works(self):
        # The compatibility half of the same rule: a match()-only matcher that
        # answers FOR the member's section — which is what every pre-existing
        # double, and the source-pinned resolved-conflict matcher, actually do —
        # is unaffected.
        matcher = _AnsweringMatcher(LIVE_310UB46_2)
        validated = real_member_to_validated_member(_member_row("310UB46.2"), matcher)
        assert validated.section == "310UB46.2"
        assert validated.section_properties["name"] == "310UB46.2"

    def test_a_match_only_matcher_answering_nothing_keeps_the_existing_refusal(self):
        matcher = _AnsweringMatcher(None)
        with pytest.raises(GeometryValidationError, match="not a recognised entry"):
            real_member_to_validated_member(_member_row("310UB46.2"), matcher)


# ===========================================================================
# 4. One lookup, with the member's own decided identity
# ===========================================================================
class TestTheLookupIsSingleAndIdentityBound:

    def test_the_matcher_is_asked_exactly_once_with_the_decided_identity(self, make_real_matcher):
        # section_name is the DECIDED identity ("310UB46.2"); section_name_raw
        # is an earlier, un-decided reading of the same callout. The adapter
        # must resolve the decided identity and must not re-resolve the raw
        # token — the careful spelling in raw is exactly how a "310UB46" ->
        # "310UB46.2" re-resolution would creep back in.
        matcher = _RecordingMatcher(make_real_matcher())
        row = _member_row("310UB46.2", section_name_raw="310UB46")

        validated = real_member_to_validated_member(row, matcher)

        assert validated.section == "310UB46.2"
        assert matcher.calls == [("resolve", "310UB46.2")]
        assert ("match", "310UB46") not in matcher.calls

    def test_the_refused_member_is_also_asked_exactly_once(self, make_real_matcher):
        matcher = _RecordingMatcher(make_real_matcher())
        with pytest.raises(GeometryValidationError):
            real_member_to_validated_member(_member_row("310UB40"), matcher)
        assert matcher.calls == [("resolve", "310UB40")]

    def test_a_non_exact_resolution_is_refused_even_when_the_row_looks_right(self, modules):
        # Isolates the resolution half of the rule: the row returned IS the
        # member's own section, so only the reported resolution is wrong.
        matcher = _ReportingMatcher(
            modules.section_matcher.SectionMatch, "SUFFIX_FALLBACK", LIVE_310UB46_2
        )
        with pytest.raises(GeometryValidationError, match="not an exact one") as excinfo:
            real_member_to_validated_member(_member_row("310UB46.2"), matcher)
        assert "310UB46.2" in str(excinfo.value)  # the row it offered is named

    def test_an_unrecognised_resolution_fails_closed(self):
        # Anything other than an exact hit is refused, including a resolution
        # this build has never heard of — the boundary does not assume that an
        # unknown route is a safe one.
        with pytest.raises(GeometryValidationError, match="SOME_FUTURE_RESOLUTION"):
            real_member_to_validated_member(
                _member_row("310UB46.2"), _UnknownResolutionMatcher(LIVE_310UB46_2)
            )

    def test_a_row_that_is_not_the_decided_identity_is_refused_even_when_reported_exact(self, modules):
        # Isolates the identity half of the rule: the matcher claims an exact
        # hit but hands back a different section. The rule does not take the
        # matcher's word for it.
        matcher = _ReportingMatcher(
            modules.section_matcher.SectionMatch, "EXACT", LIVE_310UB40_4
        )
        with pytest.raises(GeometryValidationError, match="DIFFERENT section"):
            real_member_to_validated_member(_member_row("310UB46.2"), matcher)


# ===========================================================================
# The rule is structural — swept across the whole live catalogue
# ===========================================================================
class TestTheRuleIsStructuralNotAListOfNames:

    def test_no_captured_section_can_ever_be_answered_with_another(self, captured_rows, make_real_matcher):
        """
        For every one of the 218 real catalogue sections, a member whose decided
        identity is that section is either accepted WITH that identity or
        refused — never accepted as a different section. This is the invariant
        the whole milestone exists to establish, checked against real data
        rather than a hand-picked list.
        """
        matcher = make_real_matcher(captured_rows)
        accepted = []
        refused_unsupported_family = 0
        for row in captured_rows:
            name = row["name"]
            try:
                validated = real_member_to_validated_member(_member_row(name), matcher)
            except GeometryValidationError as exc:
                refused_unsupported_family += 1
                if row.get("family") in SUPPORTED_FAMILIES:
                    raise AssertionError(
                        f"a supported-family section was refused: {name!r} ({exc})"
                    ) from exc
                continue
            accepted.append(name)
            assert _identity(validated.section_properties["name"]) == _identity(name), (
                f"member '{name}' was answered with "
                f"{validated.section_properties['name']!r} — a substitution"
            )

        # Non-vacuous: the sweep really did accept a large, real set, and the
        # unsupported families are the only refusals.
        assert len(accepted) + refused_unsupported_family == SNAPSHOT_ROW_COUNT
        # CHANGED BY MILESTONE J1 — deliberately, and reported as such. These
        # two counts were 148 / 70 while the CAD family gate read
        # PROFILE_BUILDERS directly: the 23 live rows whose authoritative family
        # is "FLAT" were refused there as an unsupported family. J1's explicit
        # sections.CAD_FAMILY_PROJECTION admits them, so they move from the
        # refusal column into the accepted column — 148 + 23 = 171 accepted,
        # 70 - 23 = 47 refused. Nothing about the invariant above changed: each
        # of those 23 is still accepted only as ITS OWN section (asserted on
        # every acceptance), and no supported-family section is refused (the
        # branch above, which now also covers "FLAT").
        assert len(accepted) == 171
        assert refused_unsupported_family == 47

    def test_every_substitution_trap_in_the_live_catalogue_is_refused(self, captured_rows, make_real_matcher):
        """
        Derives the traps MECHANICALLY from the real data: any token T such that
        (a) the catalogue has no row called T, but (b) the catalogue does carry a
        T.<digit> row, is a token the suffix fallback would substitute — and the
        real matcher is asked, rather than the rule being guessed at here. The
        35 it finds are exactly the UB and UC callouts real drawings write
        without their decimal, including the real Arkles "310UB46" ->
        "310UB46.2". Every one must be refused, naming the row it was offered.
        """
        matcher = make_real_matcher(captured_rows)
        catalogue_identities = {_identity(row["name"]) for row in captured_rows}

        traps = {}
        for row in captured_rows:
            name = row["name"]
            if "." not in name:
                continue
            token = name.split(".")[0]
            if _identity(token) in catalogue_identities:
                continue  # the token has a row of its own: a genuine EXACT member
            substituted = matcher.match(token)
            if substituted is None:
                continue  # nothing was offered: an ordinary unmatched section
            assert _identity(substituted["name"]) != _identity(token)
            traps[token] = substituted["name"]

        assert len(traps) == 35, (
            f"the trap set derived from the pinned capture changed ({len(traps)} found: "
            f"{sorted(traps)}); the sweep is no longer covering what it was written to cover"
        )
        assert traps["310UB40"] == "310UB40.4"
        assert traps["310UB46"] == "310UB46.2"
        assert traps["200UC46"] == "200UC46.2"   # a trap on an unsupported family too

        for token, substituted_name in traps.items():
            with pytest.raises(GeometryValidationError) as excinfo:
                real_member_to_validated_member(_member_row(token), matcher)
            assert substituted_name in str(excinfo.value), (
                f"member '{token}' was refused without naming the refused substitution "
                f"{substituted_name!r}"
            )

    def test_the_sweep_covers_more_than_one_family(self, captured_rows):
        families = {row["family"] for row in captured_rows}
        assert len(families) > 5
        assert {"UB", "PFC", "UC", "RHS"} <= families


# ===========================================================================
# The existing refusals are unchanged
# ===========================================================================
class TestTheExistingRefusalsAreUnchanged:

    def test_no_section_name_is_still_refused(self, make_real_matcher):
        with pytest.raises(GeometryValidationError, match="no matched section name"):
            real_member_to_validated_member(
                _member_row(None), make_real_matcher()
            )

    def test_an_unsupported_family_is_still_refused_the_same_way(self, make_real_matcher):
        # 200UC46.2 is carried by the real catalogue; UC has no CAD builder.
        # The family refusal must still be the family refusal — the identity
        # rule must not have taken its place.
        matcher = make_real_matcher([{
            "name": "200UC46.2", "family": "UC", "weight_per_metre": 46.2,
            "depth": None, "flange_width": None, "flange_thickness": None,
            "web_thickness": None, "leg_size": None, "thickness": None,
            "width": None, "outside_diameter": None,
        }])
        with pytest.raises(GeometryValidationError, match="no supported CAD profile builder"):
            real_member_to_validated_member(_member_row("200UC46.2"), matcher)

    def test_review_required_is_still_refused_first(self, make_real_matcher):
        with pytest.raises(GeometryValidationError, match="review_required"):
            real_member_to_validated_member(
                _member_row("310UB46.2", review_status="review_required"), make_real_matcher()
            )

    def test_a_missing_length_is_still_refused_first(self, make_real_matcher):
        with pytest.raises(GeometryValidationError, match="length_mm"):
            real_member_to_validated_member(
                _member_row("310UB46.2", length_mm=None), make_real_matcher()
            )
