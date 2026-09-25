"""
PRODUCTION SECTION MATCHER — the suffix-fallback defect, captured test-first.

WHAT THIS FILE PINS (CURRENT PRODUCTION BEHAVIOUR, BEFORE ANY FIX)

app/engineering_data/section_matcher.py's SectionMatcher.match() is the ONLY
section-identity decision the production pipeline makes
(app/pipeline.py:88 -> app/validation/rules.py:194). Its stated contract is
name resolution: normalise, try the requested token exactly, then — only when
the normalised token contains no "." — try that same token with a ".0"..".9"
suffix (section_matcher.py:20-25).

The second step is not a normalisation. It is a SUBSTITUTION: when the drawing
names a section the live steel_sections table does not carry, the matcher
returns a DIFFERENT row, and app/validation/rules.py:197 then writes that
row's `name` into `section_name` — the authoritative identity. The drawing's
own token survives only in `section_name_raw`.

The captured live evidence (/tmp/steelspec_live_steel_sections_218.json,
sha256 e66f179b..., the same 218-row capture the Milestone E1 tests pin at
tests/test_real_world_e1_source_of_truth.py:70-71) shows both outcomes on real
data:

    310UB40   -> 310UB40.4   the table has no 310UB40 row, so the fallback
                             substitutes a different section's identity
    250X90PFC -> no match    the table has no 250X90PFC row at all

The first is a silent substitution of one section for another. The second is
the opposite failure: a correctly-read section the catalogue cannot recognise.
This file captures BOTH, exactly as production behaves today. It asserts the
defect; it does not endorse it.

WHEN THE AUTHORITY BOUNDARY IS IMPLEMENTED, THESE ASSERTIONS ARE EXPECTED TO
CHANGE (310UB40 must stop resolving to 310UB40.4). This file is then UPDATED
to the new behaviour — never deleted, and never xfailed, because an xfail
would hide the fix and let the defect return unnoticed.

THE ADDITIVE RESOLUTION CONTRACT (implemented; asserted in the second half)

SectionMatcher now ALSO exposes resolve(raw_name) -> SectionMatch, which
reports which route produced the row — EXACT, SUFFIX_FALLBACK or NONE — while
preserving the requested drawing token. It is a thin wrapper over the SAME
private primitive match() uses (_match_candidate), so the fallback exists in
exactly one place and the two entry points cannot drift apart. match() itself
is unchanged: same situations, same object, same fallback behind it. The
resolution tests below pin that contract, including that resolve() issues no
second query and that the route it reports is contingent on the fallback
actually firing — so removing or changing the fallback fails these tests.

NOTE ON SCOPE: production does NOT map 250X90PFC to 250PFC, and nothing here
claims it does. The 250X90PFC-vs-250PFC pairing is an E1 capture-vs-live
reconciliation comparison; the production matcher never bridges the two, and
the negative tests below prove exactly that.

HOW THE REAL MATCHER IS EXERCISED (no production change, no algorithm copy)

SectionMatcher.__init__(self) takes no arguments and reads the MODULE-LEVEL
`supabase` client (section_matcher.py:12). That module global is therefore the
single injection point. These tests replace ONLY that name — via monkeypatch,
undone automatically after each test — with a deterministic recording double
that answers the one query __init__ makes
(.table("steel_sections").select("*").execute().data).

Everything downstream of that boundary is untouched production code: the real
__init__ builds the real lookup index from the supplied rows, and the real
match() performs the real exact-then-suffix resolution. The fallback algorithm
is NOT reproduced here — the tests call match() and observe what it returns.
Where a proof needs the matcher's own index, it reads the index the real
__init__ actually built (matcher._lookup) rather than recomputing it.

DEPENDENCIES ISOLATED (all at the repository boundary only)

  * Supabase client -> the recording double above; no client call is made.
  * app.config env  -> os.environ[...] is read at IMPORT time
                       (app/config.py:7-8), so the production module is
                       imported under a test-only configuration boundary with
                       a fake, JWT-shaped placeholder — the established
                       pattern at tests/test_review_ui_integration.py:72-93.
                       The environment is restored immediately after the
                       import, and every module this import adds to
                       sys.modules is removed at module teardown, so the
                       pre-existing 11 SUPABASE_URL smoke-import failures are
                       neither fixed nor worsened.
  * database query  -> never issued; the double answers in-process.

SAFETY: on import the module global is additionally set to a guard client that
raises on any use, so an unpatched SectionMatcher() fails loudly instead of
reaching the network. No network. No credentials. tests/data is not touched —
the capture is read from /tmp, and so that this regression runs everywhere, its
three relevant rows are ALSO pinned verbatim below and cross-checked against
the capture whenever the capture is present.
"""

import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The captured live evidence this file is anchored to.
# ---------------------------------------------------------------------------
SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
SNAPSHOT_ROW_COUNT = 218

# The deliberately fake, well-formed configuration boundary. supabase-py
# validates the key SHAPE at import, so the placeholder is JWT-shaped but is
# not a credential and is never sent anywhere (the client is replaced before
# any use). Same values as tests/test_review_ui_integration.py:72-79.
TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)

# ---------------------------------------------------------------------------
# The three rows this regression relies on, pinned VERBATIM from the capture
# above (every key the live table carries, including the nulls). Pinned so the
# regression runs deterministically with no /tmp dependency; cross-checked
# against the capture itself by TestThePinnedRowsAreTheCapturedRows, so they
# can never silently drift from the real evidence.
#
# The capture deliberately does NOT contain a "310UB40" row and does NOT
# contain any "250X90PFC" row — that absence is the premise of this whole file.
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
        "the capture at /tmp does not match the sha256 this file (and the Milestone E1 "
        "tests) pinned — refusing to assert against unverified evidence"
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# The repository-boundary double. It answers exactly the one query the real
# SectionMatcher.__init__ makes, and records that it happened, so a test can
# prove the real matcher ran against the supplied rows rather than a live
# client. It cannot reach the network: it has no network code at all.
# ---------------------------------------------------------------------------
class _FakeExecuteResult:
    """The shape SectionMatcher.__init__ reads: result.data."""

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
        # A detached copy, mirroring the real client: what the matcher indexes
        # can never be mutated through this double, and the fixtures stay clean.
        return _FakeExecuteResult(copy.deepcopy(self._client.rows))


class _FakeSupabaseClient:
    """The deterministic double standing in for the module-level client."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _NetworkGuardClient:
    """
    Installed on the production module for the duration of this file so that an
    UNPATCHED SectionMatcher() fails loudly here instead of reaching the
    network. It exists to make 'no network' structural, not merely intended.
    """

    def table(self, name):  # pragma: no cover - only reached on a test bug
        raise AssertionError(
            f"no test in this file may touch a live Supabase client (asked for table "
            f"{name!r}); build the matcher through the build_real_matcher fixture so the "
            "repository boundary is replaced first."
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def section_matcher_module():
    """
    The REAL production module, imported once under a test-only configuration
    boundary, with its module-level client replaced by the network guard.

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
        module = importlib.import_module("app.engineering_data.section_matcher")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_client = module.supabase
    module.supabase = _NetworkGuardClient()
    try:
        yield module
    finally:
        module.supabase = real_client
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)


@pytest.fixture()
def build_real_matcher(section_matcher_module, monkeypatch):
    """
    Returns build(rows) -> (matcher, double): a REAL SectionMatcher whose only
    external dependency has been replaced at the repository boundary. The
    production module is not modified — monkeypatch restores the module global
    after each test.
    """

    def build(rows):
        double = _FakeSupabaseClient(rows)
        monkeypatch.setattr(section_matcher_module, "supabase", double)
        matcher = section_matcher_module.SectionMatcher()
        return matcher, double

    return build


@pytest.fixture()
def pinned_index():
    return _pinned_index()


@pytest.fixture()
def captured_rows():
    return _read_capture()


@pytest.fixture()
def captured_by_name(captured_rows):
    return {row["name"]: row for row in captured_rows}


# ===========================================================================
# The pinned rows really are the captured rows
# ===========================================================================
class TestThePinnedRowsAreTheCapturedRows:

    def test_the_capture_is_the_pinned_218_row_evidence(self, captured_rows):
        assert len(captured_rows) == SNAPSHOT_ROW_COUNT

    @pytest.mark.parametrize("name", PINNED_NAMES)
    def test_each_pinned_row_is_byte_identical_to_the_capture(self, captured_by_name, name):
        pinned = next(row for row in PINNED_ROWS if row["name"] == name)
        assert captured_by_name[name] == pinned, (
            f"the pinned {name!r} row has drifted from the capture — the pinned literals in "
            "this file must stay verbatim copies of the real evidence"
        )

    def test_the_capture_confirms_every_premise_this_file_relies_on(self, captured_by_name):
        # The two absences are the whole point: the fallback can only fire
        # because 310UB40 is missing, and 250X90PFC cannot match at all.
        assert "310UB40" not in captured_by_name
        assert "250X90PFC" not in captured_by_name
        # ...and the rows the outcomes land on really are present.
        assert "310UB40.4" in captured_by_name
        assert "310UB46.2" in captured_by_name
        assert "250PFC" in captured_by_name


# ===========================================================================
# CASE 1 — 310UB40 resolves to 310UB40.4 by the suffix fallback
# ===========================================================================
class TestCase1TheSuffixFallbackSubstitutesA310UB40:

    def test_it_resolves_to_the_suffixed_row_rather_than_the_requested_token(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        resolved = matcher.match("310UB40")
        assert resolved is not None
        assert resolved["name"] == "310UB40.4"

    def test_the_resolution_is_demonstrably_a_suffix_fallback_not_an_exact_match(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        # The index the REAL __init__ built — read, not reproduced. The token
        # the drawing states is absent from it; the suffixed candidate is not.
        # So the row the matcher returns cannot be an exact lookup of that token.
        assert "310UB40" not in matcher._lookup
        assert "310UB40.4" in matcher._lookup
        resolved = matcher.match("310UB40")
        assert resolved["name"] == "310UB40.4"
        assert resolved["name"] != "310UB40"

    def test_the_same_index_answers_a_dotted_query_exactly_and_the_bare_token_by_fallback(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        # A token containing a dot admits no suffix candidate, so it can only
        # ever be an exact lookup. The bare token reaches the SAME row by a
        # different route — which is precisely why the substitution is
        # invisible downstream: nothing on the returned row records which route
        # was taken.
        exact = matcher.match("310UB40.4")
        fallback = matcher.match("310UB40")
        assert exact["name"] == "310UB40.4"
        assert fallback["name"] == "310UB40.4"
        assert exact == fallback

    def test_a_different_base_is_never_matched(self, build_real_matcher):
        # The mechanism extends the REQUESTED token with a suffix; it does not
        # search the index for similar names. A table carrying only a different
        # 310UB section must therefore not satisfy a request for 310UB40. This
        # is the discriminator against the looser prefix-matching test doubles
        # used elsewhere in this suite.
        matcher, _ = build_real_matcher([copy.deepcopy(LIVE_310UB46_2)])
        assert matcher.match("310UB40") is None


# ===========================================================================
# CASE 2 — an exact section resolves exactly
# ===========================================================================
class TestCase2AnExactSectionResolvesExactly:

    def test_310ub46_2_resolves_to_itself(self, build_real_matcher, pinned_index):
        matcher, _ = build_real_matcher(pinned_index)
        resolved = matcher.match("310UB46.2")
        assert resolved is not None
        assert resolved["name"] == "310UB46.2"

    def test_normalisation_is_identity_preserving_and_separate_from_the_fallback(
        self, build_real_matcher, pinned_index
    ):
        # Case/whitespace normalisation changes nothing about WHICH section is
        # meant — it is the legitimate half of the contract, and it is what the
        # suffix step must not be confused with.
        matcher, _ = build_real_matcher(pinned_index)
        assert matcher.match("310ub46.2")["name"] == "310UB46.2"
        assert matcher.match("  310UB46.2  ")["name"] == "310UB46.2"
        assert matcher.match("310 UB 46.2 ")["name"] == "310UB46.2"


# ===========================================================================
# CASE 3 — 250X90PFC matches nothing (and is NOT mapped to 250PFC)
# ===========================================================================
class TestCase3AnUnknownSectionReturnsNoMatch:

    def test_250x90pfc_returns_no_match(self, build_real_matcher, pinned_index):
        matcher, _ = build_real_matcher(pinned_index)
        assert matcher.match("250X90PFC") is None

    def test_the_negative_is_meaningful_because_a_pfc_row_is_present(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        # The index DOES carry a PFC row, and it resolves exactly when asked
        # for by its own name. The drawing's token still matches nothing: the
        # matcher never bridges 250X90PFC to 250PFC, and production does not
        # map one to the other.
        assert "250PFC" in matcher._lookup
        assert matcher.match("250PFC")["name"] == "250PFC"
        assert matcher.match("250X90PFC") is None


# ===========================================================================
# The real production matcher was exercised — not a double, not a copy
# ===========================================================================
class TestTheRealProductionMatcherWasExercised:

    def test_the_class_under_test_is_the_production_class(
        self, build_real_matcher, pinned_index, section_matcher_module
    ):
        matcher, _ = build_real_matcher(pinned_index)
        assert type(matcher) is section_matcher_module.SectionMatcher
        assert type(matcher).__module__ == "app.engineering_data.section_matcher"

    def test_the_index_was_built_by_the_real_constructor_from_the_supplied_rows(
        self, build_real_matcher, pinned_index
    ):
        matcher, double = build_real_matcher(pinned_index)
        assert double.queries == [("steel_sections", "*")]
        assert double.executed == 1
        # Exactly the rows that were supplied, keyed by their own names —
        # proving the real __init__ did the real indexing.
        assert set(matcher._lookup) == set(PINNED_NAMES)

    def test_matching_is_in_process_and_consults_no_client(
        self, build_real_matcher, pinned_index
    ):
        matcher, double = build_real_matcher(pinned_index)
        matcher.match("310UB40")
        matcher.match("310UB46.2")
        matcher.match("250X90PFC")
        # Still just the one index-building query: match() is pure lookup.
        assert double.executed == 1
        assert double.queries == [("steel_sections", "*")]

    def test_an_unpatched_client_would_fail_loudly_rather_than_reach_the_network(
        self, section_matcher_module
    ):
        with pytest.raises(AssertionError, match="may touch a live Supabase client"):
            section_matcher_module.supabase.table("steel_sections")


# ===========================================================================
# The same three cases against the FULL captured 218-row evidence
# ===========================================================================
class TestAgainstTheFullCapturedSnapshot:

    def test_310ub40_resolves_to_310ub40_4(self, build_real_matcher, captured_rows):
        matcher, _ = build_real_matcher(captured_rows)
        assert "310UB40" not in matcher._lookup
        assert matcher.match("310UB40")["name"] == "310UB40.4"

    def test_310ub46_2_resolves_exactly(self, build_real_matcher, captured_rows):
        matcher, _ = build_real_matcher(captured_rows)
        assert matcher.match("310UB46.2")["name"] == "310UB46.2"

    def test_250x90pfc_returns_no_match(self, build_real_matcher, captured_rows):
        matcher, _ = build_real_matcher(captured_rows)
        assert matcher.match("250X90PFC") is None

    def test_the_whole_capture_carries_no_310ub40_and_no_250x90pfc_row(
        self, captured_by_name
    ):
        assert "310UB40" not in captured_by_name
        assert "250X90PFC" not in captured_by_name


# ===========================================================================
# The additive resolution contract — resolve() reports the ROUTE that produced
# the row, through the SAME algorithm match() uses
# ===========================================================================
class TestTheResolutionContractReportsTheRoute:

    def test_case_1_310ub40_is_reported_as_a_suffix_fallback_and_keeps_the_token(
        self, build_real_matcher, pinned_index, section_matcher_module
    ):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("310UB40")
        assert outcome.resolution == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        assert outcome.catalogue_row is not None
        assert outcome.catalogue_row["name"] == "310UB40.4"
        # The identity reported is the DRAWING's token, never the row's name.
        assert outcome.drawing_token == "310UB40"
        assert outcome.drawing_token != outcome.catalogue_row["name"]

    def test_case_2_310ub46_2_is_reported_as_exact(
        self, build_real_matcher, pinned_index, section_matcher_module
    ):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("310UB46.2")
        assert outcome.resolution == section_matcher_module.RESOLUTION_EXACT
        assert outcome.catalogue_row is not None
        assert outcome.catalogue_row["name"] == "310UB46.2"
        assert outcome.drawing_token == "310UB46.2"

    def test_case_3_250x90pfc_is_reported_as_none_with_no_row(
        self, build_real_matcher, pinned_index, section_matcher_module
    ):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("250X90PFC")
        assert outcome.resolution == section_matcher_module.RESOLUTION_NONE
        assert outcome.catalogue_row is None
        # The token is preserved even when nothing resolves at all.
        assert outcome.drawing_token == "250X90PFC"

    @pytest.mark.parametrize("raw_name", ["310UB40", "310UB46.2", "250X90PFC", "310ub40"])
    def test_every_outcome_is_exactly_one_of_the_three_declared_routes(
        self, build_real_matcher, pinned_index, section_matcher_module, raw_name
    ):
        matcher, _ = build_real_matcher(pinned_index)
        assert len(section_matcher_module.RESOLUTIONS) == 3
        assert matcher.resolve(raw_name).resolution in section_matcher_module.RESOLUTIONS

    def test_the_reported_route_is_the_one_the_index_actually_took(
        self, build_real_matcher, pinned_index, section_matcher_module
    ):
        # EXACT means the requested token IS an index key. SUFFIX_FALLBACK means
        # it is NOT, but a ".0"..".9" extension of it is. Both are asserted
        # against the index the REAL __init__ built, so the route the matcher
        # reports can be checked independently of its own bookkeeping.
        matcher, _ = build_real_matcher(pinned_index)
        assert "310UB46.2" in matcher._lookup
        assert "310UB40" not in matcher._lookup
        assert "310UB40.4" in matcher._lookup
        assert (
            matcher.resolve("310UB46.2").resolution
            == section_matcher_module.RESOLUTION_EXACT
        )
        assert (
            matcher.resolve("310UB40").resolution
            == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        )
        assert (
            matcher.resolve("250X90PFC").resolution
            == section_matcher_module.RESOLUTION_NONE
        )

    def test_the_route_is_the_same_against_the_full_captured_snapshot(
        self, build_real_matcher, captured_rows, section_matcher_module
    ):
        matcher, _ = build_real_matcher(captured_rows)
        assert (
            matcher.resolve("310UB40").resolution
            == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        )
        assert (
            matcher.resolve("310UB46.2").resolution
            == section_matcher_module.RESOLUTION_EXACT
        )
        assert (
            matcher.resolve("250X90PFC").resolution
            == section_matcher_module.RESOLUTION_NONE
        )


# ===========================================================================
# match() is unchanged, and the two entry points agree on the selected row
# ===========================================================================
class TestTheTwoEntryPointsAgreeAndMatchIsUnchanged:

    @pytest.mark.parametrize(
        "raw_name", ["310UB40", "310UB46.2", "250X90PFC", "310ub40", "  250PFC  "]
    )
    def test_resolve_selects_exactly_the_row_match_selects(
        self, build_real_matcher, pinned_index, raw_name
    ):
        matcher, _ = build_real_matcher(pinned_index)
        assert matcher.resolve(raw_name).catalogue_row == matcher.match(raw_name)

    def test_match_behaviour_is_unchanged_for_all_three_cases(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        assert matcher.match("310UB40")["name"] == "310UB40.4"
        assert matcher.match("310UB46.2")["name"] == "310UB46.2"
        assert matcher.match("250X90PFC") is None

    def test_match_still_returns_the_live_index_row_itself(
        self, build_real_matcher, pinned_index
    ):
        # The pre-existing contract is preserved down to object identity:
        # match() hands back the very object the index holds, not a copy of it.
        matcher, _ = build_real_matcher(pinned_index)
        assert matcher.match("310UB40") is matcher._lookup["310UB40.4"]
        assert matcher.match("310UB46.2") is matcher._lookup["310UB46.2"]
        assert matcher.match("250X90PFC") is None

    def test_an_unmatched_resolution_is_none_where_match_is_none(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher([copy.deepcopy(LIVE_310UB46_2)])
        # Same index, two entry points: the negative case agrees on both.
        assert matcher.match("310UB40") is None
        assert matcher.resolve("310UB40").catalogue_row is None


# ===========================================================================
# resolve() is pure lookup — it adds no query and reaches no client
# ===========================================================================
class TestResolveIsPureLookup:

    def test_resolving_issues_no_additional_query(
        self, build_real_matcher, pinned_index
    ):
        matcher, double = build_real_matcher(pinned_index)
        matcher.resolve("310UB40")
        matcher.resolve("310UB46.2")
        matcher.resolve("250X90PFC")
        # Still exactly the one index-building query __init__ made.
        assert double.executed == 1
        assert double.queries == [("steel_sections", "*")]

    def test_resolve_and_match_together_add_no_query(
        self, build_real_matcher, pinned_index
    ):
        matcher, double = build_real_matcher(pinned_index)
        for raw_name in ("310UB40", "310UB46.2", "250X90PFC"):
            matcher.match(raw_name)
            matcher.resolve(raw_name)
        assert double.executed == 1
        assert double.queries == [("steel_sections", "*")]


# ===========================================================================
# SectionMatch is a coherent, frozen record — incoherent ones are refused
# ===========================================================================
class TestSectionMatchRefusesIncoherentOutcomes:

    def test_a_none_outcome_cannot_carry_a_row(self, section_matcher_module):
        with pytest.raises(ValueError):
            section_matcher_module.SectionMatch(
                drawing_token="X",
                drawing_token_raw="X",
                catalogue_row=dict(LIVE_250PFC),
                resolution=section_matcher_module.RESOLUTION_NONE,
            )

    @pytest.mark.parametrize("route", ["EXACT", "SUFFIX_FALLBACK"])
    def test_a_matched_outcome_cannot_lack_a_row(self, section_matcher_module, route):
        with pytest.raises(ValueError):
            section_matcher_module.SectionMatch(
                drawing_token="X",
                drawing_token_raw="X",
                catalogue_row=None,
                resolution=getattr(section_matcher_module, f"RESOLUTION_{route}"),
            )

    def test_an_unknown_route_cannot_be_constructed(self, section_matcher_module):
        with pytest.raises(ValueError):
            section_matcher_module.SectionMatch(
                drawing_token="X",
                drawing_token_raw="X",
                catalogue_row=dict(LIVE_250PFC),
                resolution="PROBABLY",
            )

    def test_the_record_is_frozen(self, build_real_matcher, pinned_index):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("310UB40")
        with pytest.raises(AttributeError):
            outcome.resolution = "EXACT"

    def test_the_catalogue_row_is_a_detached_copy(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("310UB40")
        outcome.catalogue_row["name"] = "MUTATED"
        # The matcher's index is untouched, and a later resolve is unaffected.
        assert matcher._lookup["310UB40.4"]["name"] == "310UB40.4"
        assert matcher.resolve("310UB40").catalogue_row["name"] == "310UB40.4"


# ===========================================================================
# The drawing token is preserved — it is never replaced by the row's name
# ===========================================================================
class TestTheDrawingTokenIsPreserved:

    @pytest.mark.parametrize("raw_name", ["310UB40", "310UB46.2", "250X90PFC"])
    def test_the_reported_token_is_the_callers_own_token(
        self, build_real_matcher, pinned_index, raw_name
    ):
        matcher, _ = build_real_matcher(pinned_index)
        assert matcher.resolve(raw_name).drawing_token == raw_name

    def test_the_raw_token_the_caller_passed_is_kept_exactly(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("  310 UB 40  ")
        assert outcome.drawing_token_raw == "  310 UB 40  "
        # ...while the reported identity is the matcher's own canonical form,
        # which is the same token — not the catalogue row's name.
        assert outcome.drawing_token == "310UB40"
        assert outcome.drawing_token != outcome.catalogue_row["name"]

    def test_the_token_is_not_the_catalogue_name_under_substitution(
        self, build_real_matcher, pinned_index
    ):
        matcher, _ = build_real_matcher(pinned_index)
        outcome = matcher.resolve("310UB40")
        assert outcome.catalogue_row["name"] == "310UB40.4"
        assert outcome.drawing_token == "310UB40"
        assert outcome.drawing_token_raw == "310UB40"
        # The one thing the resolution may never do: silently adopt the row's
        # name as the drawing's identity.
        assert outcome.drawing_token != outcome.catalogue_row["name"]


# ===========================================================================
# The reported route is load-bearing — it depends on the fallback firing
# ===========================================================================
class TestTheReportedRouteIsLoadBearing:
    """
    The candidate generation is never reproduced here: the matcher is simply
    run against two indexes differing ONLY by whether the ".4" row exists, and
    the reported route must differ accordingly. So if the existing suffix
    fallback were removed or changed, the SUFFIX_FALLBACK assertion below would
    become NONE and this test would fail.
    """

    def test_the_suffix_fallback_report_is_contingent_on_the_dotted_row_existing(
        self, build_real_matcher, section_matcher_module
    ):
        without_dotted, _ = build_real_matcher([copy.deepcopy(LIVE_310UB46_2)])
        with_dotted, _ = build_real_matcher(_pinned_index())
        assert (
            without_dotted.resolve("310UB40").resolution
            == section_matcher_module.RESOLUTION_NONE
        )
        assert (
            with_dotted.resolve("310UB40").resolution
            == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        )

    def test_no_route_is_reported_without_the_evidence_that_route_requires(
        self, build_real_matcher, pinned_index, section_matcher_module
    ):
        matcher, _ = build_real_matcher(pinned_index)
        for raw_name in ("310UB40", "310UB46.2", "250X90PFC"):
            outcome = matcher.resolve(raw_name)
            if outcome.resolution == section_matcher_module.RESOLUTION_NONE:
                assert outcome.catalogue_row is None
            else:
                assert outcome.catalogue_row is not None
