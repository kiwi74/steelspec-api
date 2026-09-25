"""
LIVE REFERENCE-DATA IDENTITY — the production SectionMatcher's provenance.

WHAT THIS FILE PINS

app/engineering_data/section_matcher.py::SectionMatcher is the only section
matcher production uses, and it reads the live Supabase `steel_sections` table.
That table is LIVE and UNVERSIONED: it carries no declared catalogue version,
and no content hash makes one exist. Before this milestone the matcher said
nothing at all about which reference dataset it had read — the pipeline's
`catalogue_version` was None because the live matcher declared nothing, which
made "no provenance exists" and "provenance exists but the source is formally
unversioned" the same empty value.

This file pins the contract that separates them:

    SectionMatcher().reference_identity -> ReferenceIdentity(
        source_kind       = SOURCE_LIVE_SUPABASE   "LIVE_SUPABASE"
        identity_status   = IDENTITY_UNVERSIONED   "UNVERSIONED"
        reference_data_digest = sha256 of the rows actually loaded
    )

    SectionMatcher().catalogue_version -> None, DECLARED (no formal version)

THE TWO QUESTIONS THIS FILE KEEPS APART

    A. reference-data provenance — which dataset did the matcher consult?
       Recorded here, for every matcher, resolved token or not.
    B. section / member provenance — which catalogue row supplied a member's
       standard properties? NOT this milestone, and NOT this object. On a
       refused suffix-fallback the answer is "none", and nothing here may be
       read as claiming otherwise.

WHAT THIS FILE DOES NOT DO: it does not carry the provenance downstream. No
acceptance, package, dispatch or deliverable structure is touched, and no
section-level provenance is introduced — SectionMatch keeps exactly its four
fields, which one test below pins structurally.

THE AUTHORITY RULE, AND HOW IT IS PROVEN

The provenance object must never participate in section authority. This file
proves that three ways, none of which is a comment:

  * structurally — an AST scan shows the matching methods (match, resolve,
    _match_candidate, _normalise) never mention the provenance, the type or the
    digest function, so no code path can consult it;
  * behaviourally — a hostile stand-in is spliced into a live matcher's
    provenance slot and every resolution outcome is asserted unchanged; and the
    slot is then set to None ("no provenance exists") and the same outcomes are
    asserted again;
  * by vocabulary — the two vocabulary values contain no digits, so neither can
    be read as a version, and `catalogue_version` is never the digest.

THE MUTATIONS THIS FILE DEFEATS (run and discarded during the milestone, never
committed): returning the digest from `catalogue_version` — "a hash is a
version" — and letting the provenance promote a SUFFIX_FALLBACK to EXACT. The
first fails the declared-absence tests; the second fails the fallback tests.

HOW THE REAL MATCHER IS EXERCISED (no production change, no algorithm copy)

SectionMatcher.__init__(self) takes no arguments and reads the MODULE-LEVEL
`supabase` client. That module global is the single injection point, and these
tests replace ONLY that name — via monkeypatch, undone automatically — with a
deterministic double that answers the one query __init__ makes
(.table("steel_sections").select("*").execute().data). Everything downstream is
untouched production code: the real __init__ builds the real index, the real
resolve() performs the real exact-then-suffix resolution, and the real __init__
computes the real digest.

SAFETY: on import the module global is additionally set to a guard client that
raises on any use, so an unpatched SectionMatcher() fails loudly instead of
reaching the network. No network, no credentials: app/config.py reads
os.environ[...] at IMPORT time, so the production module is imported once under
a test-only configuration boundary with a fake, JWT-shaped placeholder — the
established pattern at tests/test_real_world_production_section_matcher.py:111.
The environment is restored immediately after the import and every module this
import adds to sys.modules is removed at module teardown, so the pre-existing
SUPABASE_URL smoke-import baseline is neither fixed nor worsened.

tests/data is not touched — the 218-row capture is read from /tmp, and its
three relevant rows are ALSO pinned verbatim below, so this file's assertions
run deterministically without the capture present.
"""

import ast
import copy
import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import random
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The captured live evidence this file is anchored to (read-only).
# ---------------------------------------------------------------------------
SNAPSHOT_PATH = Path("/tmp/steelspec_live_steel_sections_218.json")
SNAPSHOT_SHA256 = "e66f179b0e6f98b252fac240c1a0563172e4e7137014b2c59766621429452aa4"
SNAPSHOT_ROW_COUNT = 218

# The digest of that capture under this milestone's canonicalisation rule,
# pinned so a change to the rule is a deliberate, visible act rather than a
# silent re-identification of the reference data. Cross-checked below against a
# hash this file recomputes itself from the raw bytes, so the pin cannot be a
# transcription of the implementation's own output.
SNAPSHOT_DIGEST = "f125fbda14e064f50e5463a9c3efe77d37fb004e526a89b9a33cb782b4e613df"

# The deliberately fake, well-formed configuration boundary. supabase-py
# validates the key SHAPE at import, so the placeholder is JWT-shaped but is
# not a credential and is never sent anywhere (the client is replaced before
# any use). Same values as the sibling matcher test file.
TEST_SUPABASE_URL = "https://placeholder.supabase.co"
TEST_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)

# ---------------------------------------------------------------------------
# The three rows this file relies on, pinned VERBATIM from the capture above
# (every key the live table carries, including the nulls). The capture has no
# "310UB40" row and no "250X90PFC" row — that absence is the premise of the
# fallback and unknown-section tests.
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

# The token that resolves EXACTLY, the token only the suffix fallback answers,
# and the token nothing answers — the three authority outcomes, on real data.
TOKEN_EXACT = "310UB46.2"
TOKEN_FALLBACK = "310UB40"
TOKEN_UNKNOWN = "250X90PFC"
OFFERED_FALLBACK_ROW = "310UB40.4"

# The four fields SectionMatch carries — pinned so that SECTION-level
# provenance cannot be introduced without this file failing.
SECTION_MATCH_FIELDS = ("drawing_token", "drawing_token_raw", "catalogue_row", "resolution")

# Names that would mean the matching path, or the digest, had consulted
# something outside the rows themselves.
BANNED_IN_MATCHING = ("reference_identity", "ReferenceIdentity", "reference_data_digest",
                      "_reference_identity", "catalogue_version")
BANNED_IN_DIGEST = ("os", "time", "socket", "uuid", "Path", "environ", "now", "today",
                    "utcnow", "requests", "supabase", "getenv")


def _pinned_index():
    return [copy.deepcopy(row) for row in PINNED_ROWS]


def _read_capture():
    """The captured rows, or an honest skip when the capture is not present."""
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


def _independent_digest(rows):
    """
    The canonicalisation rule, written out AGAIN here, deliberately.

    This is not a copy of the implementation: it is the rule restated in the
    test, so that a silent change to how reference data is fingered fails here
    and has to be argued for. It is written from the rule's description (sorted
    keys, preserved values, sorted rows, no runtime input), not by reading the
    implementation's source.
    """
    per_row = sorted(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in rows
    )
    payload = "steel_sections\n" + "\n".join(per_row)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The repository-boundary double — no network code of any kind.
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
        # A detached copy, mirroring the real client: what the matcher indexes
        # can never be mutated through this double.
        return _FakeExecuteResult(copy.deepcopy(self._client.rows))


class _FakeSupabaseClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
        self.executed = 0

    def table(self, name):
        return _FakeQuery(self, name)


class _NetworkGuardClient:
    """An UNPATCHED SectionMatcher() fails loudly here instead of reaching out."""

    def table(self, name):  # pragma: no cover - only reached on a test bug
        raise AssertionError(
            f"no test in this file may touch a live Supabase client (asked for table "
            f"{name!r}); build the matcher through the build_real_matcher fixture so the "
            "repository boundary is replaced first."
        )


class _HostileProvenance:
    """
    A stand-in that a compromised provenance object might be: it claims a
    formal version, offers to authorise rows, and can be called to promote a
    resolution. Splicing it in must change NOTHING, because nothing reads it.
    """

    def __init__(self):
        self.source_kind = "LIVE_SUPABASE"
        self.identity_status = "VERSIONED"
        self.reference_data_digest = "0" * 64
        self.catalogue_version = "2026.09"

    def authorise(self, row):
        return row

    def promote(self, resolution):
        return "EXACT"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def section_matcher_module():
    """
    The REAL production module, imported once under a test-only configuration
    boundary, with its module-level client replaced by the network guard.
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
    """build(rows) -> (matcher, double): a REAL SectionMatcher over supplied rows."""

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
def real_matcher(build_real_matcher, pinned_index):
    matcher, _ = build_real_matcher(pinned_index)
    return matcher


@pytest.fixture()
def captured_rows():
    return _read_capture()


# =============================================================================
# 1. The provenance object exists and states the truth about the source.
# =============================================================================
class TestTheProvenanceObjectExists:

    def test_a_real_matcher_exposes_a_reference_identity(self, real_matcher, section_matcher_module):
        identity = real_matcher.reference_identity
        assert isinstance(identity, section_matcher_module.ReferenceIdentity)
        assert dataclasses.is_dataclass(identity)

    def test_the_source_kind_is_the_live_supabase_table(self, real_matcher, section_matcher_module):
        assert section_matcher_module.SOURCE_LIVE_SUPABASE == "LIVE_SUPABASE"
        assert real_matcher.reference_identity.source_kind == "LIVE_SUPABASE"

    def test_the_identity_status_is_unversioned(self, real_matcher, section_matcher_module):
        assert section_matcher_module.IDENTITY_UNVERSIONED == "UNVERSIONED"
        assert real_matcher.reference_identity.identity_status == "UNVERSIONED"

    def test_the_vocabulary_is_a_closed_set_the_module_owns(self, section_matcher_module):
        assert section_matcher_module.SOURCE_KINDS == (section_matcher_module.SOURCE_LIVE_SUPABASE,)
        assert section_matcher_module.IDENTITY_STATUSES == (section_matcher_module.IDENTITY_UNVERSIONED,)

    def test_the_provenance_names_the_table_the_matcher_actually_read(
            self, build_real_matcher, pinned_index):
        matcher, double = build_real_matcher(pinned_index)
        assert matcher.reference_identity is not None
        assert double.queries == [("steel_sections", "*")]
        assert double.executed == 1

    def test_a_matcher_that_read_no_rows_still_states_where_it_read_from(
            self, build_real_matcher, section_matcher_module):
        matcher, _ = build_real_matcher([])
        identity = matcher.reference_identity
        assert identity.source_kind == section_matcher_module.SOURCE_LIVE_SUPABASE
        assert identity.identity_status == section_matcher_module.IDENTITY_UNVERSIONED


# =============================================================================
# 2. The provenance is immutable, and an invalid one cannot be constructed.
# =============================================================================
class TestTheProvenanceIsImmutable:

    def test_the_object_is_frozen(self, real_matcher):
        with pytest.raises(dataclasses.FrozenInstanceError):
            real_matcher.reference_identity.identity_status = "VERSIONED"

    def test_the_binding_on_the_matcher_cannot_be_replaced(self, real_matcher):
        # A property with no setter: the slot cannot be rebound on a live
        # matcher by assignment, so a caller cannot swap a matcher's stated
        # provenance for another one.
        with pytest.raises(AttributeError):
            real_matcher.reference_identity = None

    def test_two_matchers_over_the_same_rows_agree(self, build_real_matcher, pinned_index):
        first, _ = build_real_matcher(pinned_index)
        second, _ = build_real_matcher(copy.deepcopy(pinned_index))
        assert first.reference_identity == second.reference_identity
        assert first.reference_identity.reference_data_digest == \
            second.reference_identity.reference_data_digest

    def test_the_object_carries_no_callables(self, real_matcher):
        # A value to be read, never something that can be invoked — so it
        # cannot be called to authorise, promote or decide anything.
        for name in dir(real_matcher.reference_identity):
            if name.startswith("__"):
                continue
            assert not callable(getattr(real_matcher.reference_identity, name)), (
                f"the provenance object exposes a callable ({name}); it must be readable "
                "only."
            )

    def test_an_unknown_source_kind_cannot_be_constructed(self, section_matcher_module):
        with pytest.raises(ValueError):
            section_matcher_module.ReferenceIdentity(source_kind="LOCAL_FILE",
                                                     identity_status="UNVERSIONED")

    def test_a_versioned_status_cannot_be_constructed(self, section_matcher_module):
        # There is no VERSIONED status and no field to carry a version, because
        # the live source has none. A version is a new status added with the
        # field that holds it — never a value smuggled into this one.
        with pytest.raises(ValueError):
            section_matcher_module.ReferenceIdentity(source_kind="LIVE_SUPABASE",
                                                     identity_status="VERSIONED")

    def test_none_cannot_stand_in_for_a_status(self, section_matcher_module):
        with pytest.raises(ValueError):
            section_matcher_module.ReferenceIdentity(source_kind="LIVE_SUPABASE",
                                                     identity_status=None)

    def test_a_non_digest_cannot_stand_in_for_the_content_fingerprint(self, section_matcher_module):
        with pytest.raises(ValueError):
            section_matcher_module.ReferenceIdentity(source_kind="LIVE_SUPABASE",
                                                     identity_status="UNVERSIONED",
                                                     reference_data_digest="2026.09")


# =============================================================================
# 3. No catalogue version is invented for an unversioned source.
# =============================================================================
class TestNoCatalogueVersionIsInvented:

    def test_the_matcher_declares_no_catalogue_version(self, real_matcher):
        # Declared absence, not accident: the attribute now EXISTS and says
        # None. getattr(...) therefore still yields None, exactly as before.
        assert "catalogue_version" in dir(real_matcher)
        assert real_matcher.catalogue_version is None
        assert getattr(real_matcher, "catalogue_version", None) is None

    def test_the_digest_is_never_returned_as_a_version(self, real_matcher):
        digest = real_matcher.reference_identity.reference_data_digest
        assert digest is not None
        assert real_matcher.catalogue_version != digest

    def test_neither_vocabulary_value_can_be_read_as_a_version(self, section_matcher_module):
        # A version claim needs a number. Neither the source kind nor the
        # identity status carries one, so neither can be mistaken for a
        # declared version.
        for value in (section_matcher_module.SOURCE_LIVE_SUPABASE,
                      section_matcher_module.IDENTITY_UNVERSIONED):
            assert not any(character.isdigit() for character in value), value

    def test_the_production_matcher_declares_none_while_a_real_catalogue_declares_a_version(
            self, real_matcher):
        # The None is not "nothing anywhere has a version": the local,
        # test-only catalogue genuinely declares one. The LIVE source does not,
        # and that difference is what this milestone records.
        from app.engineering_data.section_catalogue import CATALOGUE_VERSION
        assert CATALOGUE_VERSION.startswith("local-section-catalogue@")
        assert real_matcher.catalogue_version is None
        assert real_matcher.catalogue_version != CATALOGUE_VERSION

    def test_the_declared_absence_is_not_the_same_as_knowing_nothing(self, real_matcher):
        # The whole point of the milestone: "no provenance exists" is the
        # absence of the object; "unversioned" is a populated object that says
        # so, and still carries the observed content identity.
        identity = real_matcher.reference_identity
        assert identity is not None
        assert identity.source_kind == "LIVE_SUPABASE"
        assert identity.identity_status == "UNVERSIONED"
        assert identity.reference_data_digest is not None


# =============================================================================
# 4. The content digest: deterministic, order-free, value-sensitive, runtime-free.
# =============================================================================
class TestTheContentDigest:

    def test_repeated_construction_over_the_same_rows_is_stable(self, build_real_matcher, pinned_index):
        digests = {
            build_real_matcher(copy.deepcopy(pinned_index))[0]
            .reference_identity.reference_data_digest
            for _ in range(5)
        }
        assert len(digests) == 1

    def test_the_database_row_order_does_not_change_the_digest(self, build_real_matcher, pinned_index):
        shuffled = copy.deepcopy(pinned_index)
        random.Random(20260923).shuffle(shuffled)
        assert shuffled != pinned_index, "the shuffle must actually reorder the rows"
        ordered, _ = build_real_matcher(pinned_index)
        reordered, _ = build_real_matcher(shuffled)
        assert ordered.reference_identity.reference_data_digest == \
            reordered.reference_identity.reference_data_digest

    def test_the_key_order_inside_a_row_does_not_change_the_digest(self, build_real_matcher, pinned_index):
        reversed_keys = [
            {key: row[key] for key in reversed(list(row))}
            for row in copy.deepcopy(pinned_index)
        ]
        assert [list(row) for row in reversed_keys] != [list(row) for row in pinned_index]
        original, _ = build_real_matcher(pinned_index)
        rewritten, _ = build_real_matcher(reversed_keys)
        assert original.reference_identity.reference_data_digest == \
            rewritten.reference_identity.reference_data_digest

    def test_a_changed_meaningful_value_changes_the_digest(self, build_real_matcher, pinned_index):
        changed = copy.deepcopy(pinned_index)
        changed[1]["weight_per_metre"] = 46.3
        before, _ = build_real_matcher(pinned_index)
        after, _ = build_real_matcher(changed)
        assert before.reference_identity.reference_data_digest != \
            after.reference_identity.reference_data_digest

    def test_no_numeric_rounding_hides_a_change(self, build_real_matcher, pinned_index):
        # A difference far below any display precision must still be a
        # difference in identity: the digest preserves the value as loaded.
        changed = copy.deepcopy(pinned_index)
        changed[1]["depth"] = 307.000000001
        before, _ = build_real_matcher(pinned_index)
        after, _ = build_real_matcher(changed)
        assert before.reference_identity.reference_data_digest != \
            after.reference_identity.reference_data_digest

    def test_a_changed_null_changes_the_digest(self, build_real_matcher, pinned_index):
        # null is data, not absent data: filling one in is a real change.
        changed = copy.deepcopy(pinned_index)
        assert changed[0]["leg_size"] is None
        changed[0]["leg_size"] = 0.0
        before, _ = build_real_matcher(pinned_index)
        after, _ = build_real_matcher(changed)
        assert before.reference_identity.reference_data_digest != \
            after.reference_identity.reference_data_digest

    def test_no_field_is_silently_omitted(self, build_real_matcher, pinned_index):
        # Every key the table carries contributes: changing any one of them —
        # including one the matching algorithm never reads — changes the digest.
        base, _ = build_real_matcher(pinned_index)
        base_digest = base.reference_identity.reference_data_digest
        for key in PINNED_ROWS[0]:
            changed = copy.deepcopy(pinned_index)
            original = changed[0][key]
            # A DIFFERENT value of the same kind, so the change is one the
            # digest must see and not one the matcher cannot index (the row's
            # `name` is its lookup key and must stay a string).
            changed[0][key] = "changed" if (original is None or isinstance(original, str)) else None
            assert changed[0][key] != original, key
            other, _ = build_real_matcher(changed)
            assert other.reference_identity.reference_data_digest != base_digest, (
                f"changing {key!r} did not change the digest — that field is being omitted"
            )

    def test_adding_or_duplicating_a_row_changes_the_digest(self, build_real_matcher, pinned_index):
        base, _ = build_real_matcher(pinned_index)
        base_digest = base.reference_identity.reference_data_digest

        extended, _ = build_real_matcher(pinned_index + [copy.deepcopy(LIVE_250PFC)])
        assert extended.reference_identity.reference_data_digest != base_digest

    def test_the_digest_is_a_pure_function_of_the_rows(self, real_matcher, section_matcher_module, pinned_index):
        # Only the rows are input: the same list, hashed directly, gives the
        # digest the matcher recorded, and re-running it changes nothing.
        assert section_matcher_module.reference_data_digest(pinned_index) == \
            real_matcher.reference_identity.reference_data_digest
        assert section_matcher_module.reference_data_digest(copy.deepcopy(pinned_index)) == \
            section_matcher_module.reference_data_digest(pinned_index)

    def test_the_digest_function_names_no_runtime_source(self, section_matcher_module):
        # Structural: the digest's own body cannot read a clock, an
        # environment, a path, a socket or a client — so no runtime value can
        # enter the fingerprint.
        source = inspect.getsource(section_matcher_module.reference_data_digest)
        tree = ast.parse(_dedented(source))
        named = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert not (named & set(BANNED_IN_DIGEST)), (
            f"the digest function references {sorted(named & set(BANNED_IN_DIGEST))}; it "
            "must be a function of the rows alone"
        )


def _dedented(source: str) -> str:
    import textwrap
    return textwrap.dedent(source)


# =============================================================================
# 5. EXACT — 310UB46.2 — identity and provenance both unchanged.
# =============================================================================
class TestAnExactMatchIsUnchanged:

    def test_the_resolution_is_exact_and_the_identity_is_the_drawn_section(
            self, real_matcher, section_matcher_module):
        outcome = real_matcher.resolve(TOKEN_EXACT)
        assert outcome.resolution == section_matcher_module.RESOLUTION_EXACT
        assert outcome.drawing_token == TOKEN_EXACT
        assert outcome.catalogue_row["name"] == TOKEN_EXACT

    def test_the_provenance_is_unchanged_by_resolving(self, real_matcher):
        before = real_matcher.reference_identity
        real_matcher.resolve(TOKEN_EXACT)
        after = real_matcher.reference_identity
        assert after is before
        assert after == before

    def test_the_provenance_is_not_the_matched_row(self, real_matcher):
        outcome = real_matcher.resolve(TOKEN_EXACT)
        identity = real_matcher.reference_identity
        assert identity is not outcome.catalogue_row
        assert identity != outcome.catalogue_row
        assert not any(
            getattr(identity, field.name) is outcome.catalogue_row
            for field in dataclasses.fields(identity)
        )

    def test_the_match_contract_gained_no_provenance_field(self, section_matcher_module):
        # SECTION-level provenance is deliberately NOT introduced here: the
        # SectionMatch contract is byte-for-byte the four fields it had.
        names = tuple(field.name for field in dataclasses.fields(section_matcher_module.SectionMatch))
        assert names == SECTION_MATCH_FIELDS

    def test_match_and_resolve_still_agree(self, real_matcher):
        assert real_matcher.match(TOKEN_EXACT)["name"] == \
            real_matcher.resolve(TOKEN_EXACT).catalogue_row["name"]


# =============================================================================
# 6. SUFFIX_FALLBACK — 310UB40 — the offered row is refused and supplies nothing.
# =============================================================================
class TestARefusedFallbackCarriesNoSectionProvenance:

    def test_the_resolution_is_the_fallback_and_the_identity_is_the_drawn_token(
            self, real_matcher, section_matcher_module):
        outcome = real_matcher.resolve(TOKEN_FALLBACK)
        assert outcome.resolution == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        assert outcome.drawing_token == TOKEN_FALLBACK
        assert outcome.catalogue_row["name"] == OFFERED_FALLBACK_ROW

    def test_the_provenance_describes_the_source_only_and_never_the_offered_row(self, real_matcher):
        outcome = real_matcher.resolve(TOKEN_FALLBACK)
        identity = real_matcher.reference_identity
        assert identity.source_kind == "LIVE_SUPABASE"
        assert identity.identity_status == "UNVERSIONED"
        # Nothing in the provenance names the refused row, and no field of it
        # is or contains the refused row.
        assert OFFERED_FALLBACK_ROW not in repr(identity)
        for field in dataclasses.fields(identity):
            value = getattr(identity, field.name)
            assert value is not outcome.catalogue_row
            assert OFFERED_FALLBACK_ROW != value

    def test_resolving_the_fallback_does_not_change_the_provenance(self, real_matcher):
        before = real_matcher.reference_identity
        real_matcher.resolve(TOKEN_FALLBACK)
        assert real_matcher.reference_identity is before

    def test_the_provenance_is_identical_to_a_matchers_that_never_saw_the_token(
            self, build_real_matcher, pinned_index):
        # Reference-data provenance is a property of the dataset consulted, not
        # of any token: a matcher that resolved the fallback and one that never
        # asked state exactly the same thing.
        untroubled, _ = build_real_matcher(pinned_index)
        asked, _ = build_real_matcher(pinned_index)
        asked.resolve(TOKEN_FALLBACK)
        assert asked.reference_identity == untroubled.reference_identity

    def test_the_offered_row_reaches_the_caller_only_as_the_refused_candidate(self, real_matcher):
        outcome = real_matcher.resolve(TOKEN_FALLBACK)
        # The row is available so a boundary can refuse it BY NAME; it is not
        # presented anywhere as this member's provenance.
        assert outcome.catalogue_row["name"] == OFFERED_FALLBACK_ROW
        assert outcome.catalogue_row is not real_matcher.reference_identity

    def test_the_fallback_algorithm_is_still_the_one_that_fires(self, real_matcher):
        assert TOKEN_FALLBACK not in real_matcher._lookup
        assert OFFERED_FALLBACK_ROW in real_matcher._lookup
        assert real_matcher.match(TOKEN_FALLBACK) is real_matcher._lookup[OFFERED_FALLBACK_ROW]
        assert real_matcher.resolve(TOKEN_FALLBACK).drawing_token == TOKEN_FALLBACK


# =============================================================================
# 7. NONE — 250X90PFC — the provenance creates no match.
# =============================================================================
class TestAnUnknownSectionCreatesNothing:

    def test_nothing_resolves_and_no_row_is_returned(self, real_matcher, section_matcher_module):
        outcome = real_matcher.resolve(TOKEN_UNKNOWN)
        assert outcome.resolution == section_matcher_module.RESOLUTION_NONE
        assert outcome.catalogue_row is None
        assert real_matcher.match(TOKEN_UNKNOWN) is None

    def test_the_provenance_still_describes_the_source_it_consulted(self, real_matcher):
        outcome = real_matcher.resolve(TOKEN_UNKNOWN)
        identity = real_matcher.reference_identity
        assert outcome.catalogue_row is None
        assert identity is not None
        assert identity.source_kind == "LIVE_SUPABASE"
        assert identity.identity_status == "UNVERSIONED"

    def test_the_provenance_adds_no_key_to_the_index(self, real_matcher):
        before = set(real_matcher._lookup)
        real_matcher.resolve(TOKEN_UNKNOWN)
        assert set(real_matcher._lookup) == before
        assert TOKEN_UNKNOWN not in real_matcher._lookup

    def test_resolving_an_unknown_token_changes_no_provenance(self, real_matcher):
        before = real_matcher.reference_identity
        real_matcher.resolve(TOKEN_UNKNOWN)
        assert real_matcher.reference_identity is before


# =============================================================================
# 8. The provenance cannot alter section authority.
# =============================================================================
class TestTheProvenanceCannotAlterAuthority:

    TOKENS = (TOKEN_EXACT, TOKEN_FALLBACK, TOKEN_UNKNOWN, "310UB40.4", "250PFC", "200UC46")

    @staticmethod
    def _fingerprint(matcher):
        return [
            (token,
             matcher.resolve(token).resolution,
             (matcher.resolve(token).catalogue_row or {}).get("name"),
             matcher.resolve(token).drawing_token)
            for token in TestTheProvenanceCannotAlterAuthority.TOKENS
        ]

    def test_a_hostile_provenance_leaves_every_resolution_identical(self, real_matcher, monkeypatch):
        clean = self._fingerprint(real_matcher)
        monkeypatch.setattr(real_matcher, "_reference_identity", _HostileProvenance())
        assert self._fingerprint(real_matcher) == clean

    def test_no_provenance_at_all_leaves_every_resolution_identical(self, real_matcher, monkeypatch):
        # "No provenance exists" — the state the honest design says is the
        # ABSENCE of the object. Setting it to None changes nothing either,
        # which is the behavioural proof that nothing reads it.
        clean = self._fingerprint(real_matcher)
        monkeypatch.setattr(real_matcher, "_reference_identity", None)
        assert self._fingerprint(real_matcher) == clean

    def test_the_fallback_cannot_be_promoted_by_swapping_the_provenance(
            self, real_matcher, section_matcher_module, monkeypatch):
        monkeypatch.setattr(real_matcher, "_reference_identity", _HostileProvenance())
        outcome = real_matcher.resolve(TOKEN_FALLBACK)
        assert outcome.resolution == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        assert outcome.drawing_token == TOKEN_FALLBACK

    def test_the_unknown_cannot_be_promoted_by_swapping_the_provenance(
            self, real_matcher, section_matcher_module, monkeypatch):
        monkeypatch.setattr(real_matcher, "_reference_identity", _HostileProvenance())
        outcome = real_matcher.resolve(TOKEN_UNKNOWN)
        assert outcome.resolution == section_matcher_module.RESOLUTION_NONE
        assert outcome.catalogue_row is None

    def test_the_matching_methods_never_mention_the_provenance(self, section_matcher_module):
        # Structural, and the strongest of the three: no matching method names
        # the provenance, its type, the digest function or catalogue_version,
        # so there is no expression in the matching path that could read them.
        for name in ("match", "resolve", "_match_candidate", "_normalise", "get_section_regex"):
            source = inspect.getsource(getattr(section_matcher_module.SectionMatcher, name))
            tree = ast.parse(_dedented(source))
            mentioned = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            }
            assert not (mentioned & set(BANNED_IN_MATCHING)), (
                f"SectionMatcher.{name} references "
                f"{sorted(mentioned & set(BANNED_IN_MATCHING))}; the matching path must never "
                "consult the provenance"
            )

    def test_the_two_vocabularies_cannot_be_confused(self, section_matcher_module):
        # The resolution vocabulary is unchanged, and disjoint from the
        # provenance vocabulary: no string in one is a string in the other.
        assert section_matcher_module.RESOLUTIONS == ("EXACT", "SUFFIX_FALLBACK", "NONE")
        provenance_values = set(section_matcher_module.SOURCE_KINDS) | \
            set(section_matcher_module.IDENTITY_STATUSES)
        assert not (set(section_matcher_module.RESOLUTIONS) & provenance_values)

    def test_the_matcher_still_decides_the_same_three_outcomes(self, real_matcher, section_matcher_module):
        # The end-to-end statement of the rule, with no monkeypatching at all.
        assert real_matcher.resolve(TOKEN_EXACT).resolution == section_matcher_module.RESOLUTION_EXACT
        assert real_matcher.resolve(TOKEN_FALLBACK).resolution == \
            section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        assert real_matcher.resolve(TOKEN_UNKNOWN).resolution == section_matcher_module.RESOLUTION_NONE


# =============================================================================
# 9. Backward compatibility: the matcher's published behaviour is intact.
# =============================================================================
class TestTheExistingContractIsIntact:

    def test_match_still_returns_the_indexed_row_itself(self, real_matcher):
        assert real_matcher.match(TOKEN_EXACT) is real_matcher._lookup[TOKEN_EXACT]
        assert real_matcher.match(TOKEN_FALLBACK) is real_matcher._lookup[OFFERED_FALLBACK_ROW]

    def test_match_still_returns_none_when_nothing_resolves(self, real_matcher):
        assert real_matcher.match(TOKEN_UNKNOWN) is None

    def test_the_index_is_still_built_from_the_loaded_rows(self, real_matcher, pinned_index):
        assert set(real_matcher._lookup) == {
            row["name"].strip().upper().replace(" ", "") for row in pinned_index
        }
        assert set(real_matcher._lookup) == set(PINNED_NAMES)

    def test_the_section_regex_is_unchanged(self, real_matcher):
        pattern = real_matcher.get_section_regex()
        assert pattern.findall("310UB46.2") == ["310UB46.2"]
        assert pattern.findall("250PFC") == ["250PFC"]
        assert pattern.findall("75x75x6EA") == ["75x75x6EA"]
        assert pattern.findall("100x8FL") == ["100x8FL"]

    def test_one_query_is_still_all_the_matcher_issues(self, build_real_matcher, pinned_index):
        matcher, double = build_real_matcher(pinned_index)
        matcher.match(TOKEN_EXACT)
        matcher.resolve(TOKEN_FALLBACK)
        matcher.resolve(TOKEN_UNKNOWN)
        assert double.executed == 1
        assert double.queries == [("steel_sections", "*")]


# =============================================================================
# 10. No network, no credentials.
# =============================================================================
class TestNoNetwork:

    def test_an_unpatched_matcher_fails_loudly_instead_of_reaching_out(self, section_matcher_module):
        assert type(section_matcher_module.supabase).__name__ == "_NetworkGuardClient"

    def test_the_guards_are_not_the_live_client(self, section_matcher_module):
        from tests.test_real_world_production_section_matcher import _NetworkGuardClient as _Other
        assert isinstance(section_matcher_module.supabase, _NetworkGuardClient)
        assert not isinstance(section_matcher_module.supabase, _Other)

    def test_this_file_never_constructs_a_client(self):
        # Structural, not textual: this file's only credential is the
        # published placeholder, and it CALLS no client constructor at all.
        tree = ast.parse(Path(__file__).read_text())
        called = {
            (node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", ""))
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert "create_client" not in called
        assert TEST_SERVICE_ROLE_KEY.endswith("fake-test-signature")
        assert TEST_SUPABASE_URL.startswith("https://placeholder.")


# =============================================================================
# 11. The real capture — the pinned digest, on real live data.
# =============================================================================
class TestTheRealCapture:

    def test_the_capture_is_the_one_this_file_pins(self, captured_rows):
        assert len(captured_rows) == SNAPSHOT_ROW_COUNT

    def test_the_real_capture_digests_to_the_pinned_value(self, captured_rows):
        assert len(SNAPSHOT_DIGEST) == 64
        from app.engineering_data.section_matcher import reference_data_digest
        assert reference_data_digest(captured_rows) == SNAPSHOT_DIGEST

    def test_the_pinned_digest_matches_an_independent_recomputation(self, captured_rows):
        # The rule restated in this file must agree with the pinned value, so
        # the pin is evidence rather than a transcription of the output.
        assert _independent_digest(captured_rows) == SNAPSHOT_DIGEST

    def test_the_real_matcher_records_that_digest(self, build_real_matcher, captured_rows):
        matcher, _ = build_real_matcher(captured_rows)
        assert matcher.reference_identity.reference_data_digest == SNAPSHOT_DIGEST
        assert matcher.reference_identity.source_kind == "LIVE_SUPABASE"
        assert matcher.reference_identity.identity_status == "UNVERSIONED"
        assert matcher.catalogue_version is None

    def test_the_real_capture_digest_survives_reordering(self, build_real_matcher, captured_rows):
        shuffled = copy.deepcopy(captured_rows)
        random.Random(7).shuffle(shuffled)
        assert shuffled != captured_rows
        matcher, _ = build_real_matcher(shuffled)
        assert matcher.reference_identity.reference_data_digest == SNAPSHOT_DIGEST

    def test_changing_one_real_row_changes_the_digest(self, build_real_matcher, captured_rows):
        changed = copy.deepcopy(captured_rows)
        changed[0]["weight_per_metre"] = (changed[0]["weight_per_metre"] or 0) + 1
        matcher, _ = build_real_matcher(changed)
        assert matcher.reference_identity.reference_data_digest != SNAPSHOT_DIGEST

    def test_every_real_section_keeps_its_identity_and_its_provenance(
            self, build_real_matcher, captured_rows, section_matcher_module):
        matcher, _ = build_real_matcher(captured_rows)
        before = matcher.reference_identity
        for row in captured_rows:
            name = row["name"]
            outcome = matcher.resolve(name)
            assert outcome.resolution == section_matcher_module.RESOLUTION_EXACT, name
            assert outcome.catalogue_row["name"] == name
            assert matcher.reference_identity is before

    def test_the_real_fallback_and_unknown_outcomes_are_unchanged(
            self, build_real_matcher, captured_rows, section_matcher_module):
        matcher, _ = build_real_matcher(captured_rows)
        fallback = matcher.resolve(TOKEN_FALLBACK)
        assert fallback.resolution == section_matcher_module.RESOLUTION_SUFFIX_FALLBACK
        assert fallback.drawing_token == TOKEN_FALLBACK
        assert fallback.catalogue_row["name"] == OFFERED_FALLBACK_ROW
        unknown = matcher.resolve(TOKEN_UNKNOWN)
        assert unknown.resolution == section_matcher_module.RESOLUTION_NONE
        assert unknown.catalogue_row is None
        assert matcher.reference_identity.identity_status == "UNVERSIONED"
