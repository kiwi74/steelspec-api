"""
J7 — PRODUCTION SECTION-RESOLUTION TRUTH, OPTION C.

WHAT CHANGED
------------
`steel_members.section_name` is FK-BOUND to `steel_sections(name)`. Two of the
three ways a drawn section can resolve produced a value that column may not
hold:

  * a REFUSED substitution (SUFFIX_FALLBACK) persisted the DRAWN token, which is
    by construction not a catalogue key;
  * a member the catalogue did not answer for at all (NONE) was discarded on the
    DXF path before persistence, so the drawing's own evidence disappeared.

J7 (Option C) makes one identity rule hold on both production paths, without a
new column, without a migration, and without touching the foreign key:

  EXACT            section_name = the CATALOGUE's name for that section
                   section_name_raw = the drawn token, verbatim
                   section_resolution = "EXACT", candidate NULL
  SUFFIX_FALLBACK  section_name = NULL   (the drawn token is not a key)
                   section_name_raw = the drawn token, verbatim
                   section_resolution = "SUFFIX_FALLBACK", candidate = the row
                   the catalogue offered, as provenance only
  NONE             section_name = NULL   (the catalogue offered nothing)
                   section_name_raw = the drawn token, verbatim
                   section_resolution = "NONE", candidate NULL

So `section_name` now holds a section THE CATALOGUE CARRIES and nothing else,
and every member the drawing states is persisted — with its drawn identity
intact in the FK-free column — instead of being renamed, substituted or dropped.

WHAT THIS MODULE PROVES
-----------------------
The focused suite the J7 brief asks for, A to K: the three resolutions on both
paths, the four FLAT rows, the EA row, NULL-weight honesty, the same-mark
conflict, PDF/DXF semantic parity, the grade hardcode, a batch regression
against the old silent `continue`, and the J6 reference identity.

WHERE THE TRUTH COMES FROM
--------------------------
Every run drives the GENUINE production boundary — the real DXF parser and the
real pipeline over the LIVE catalogue (the J4 fixtures, reused verbatim) — with
persistence intercepted at the existing repository/parser seam. No database is
written to, no catalogue row is added or changed, and no test here needs the
J5/J6 columns to have any particular default: the payloads are read directly.

The boundary, doubles and fixtures are J4's, reused rather than restated — see
`tests/test_real_world_j4_production_extraction_report_truth.py`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests import test_real_world_j4_production_extraction_report_truth as j4

# The proven J4 seams, reused. `production` imports with the repo's real .env
# (so SectionMatcher is genuinely live) and tears `app.*` out of sys.modules
# afterwards, which is what keeps the pre-existing smoke-import failures honest.
production = j4.production
live = j4.live
pdf_boundary = j4.pdf_boundary

_member = j4._member
_page = j4._page
_single_row = j4._single_row

_rows_for = j4._rows_for
_write_dxf = j4._write_dxf
_RecordingSupabase = j4._RecordingSupabase

EXACT_TOKEN = j4.EXACT_TOKEN                            # 310UB46.2
SUFFIX_FALLBACK_TOKEN = j4.SUFFIX_FALLBACK_TOKEN        # 310UB40
SUFFIX_FALLBACK_CANDIDATE = j4.SUFFIX_FALLBACK_CANDIDATE  # 310UB40.4
NONE_TOKEN = j4.NONE_TOKEN                              # 250X90PFC

# A NONE token the matcher's own regex extracts verbatim. 250X90PFC does not
# survive that regex (it is matched as "90PFC"), which is a pre-existing
# property of the extraction vocabulary and NOT part of J7 — so the parity test
# uses a control token as well, and states the difference rather than hiding it.
UNTRUNCATED_NONE_TOKEN = "310UB99"

# The four FLAT rows added to the live catalogue by J2, and the EA row the J4
# capture uses for its case witness. Spelled as the LIVE TABLE spells them —
# lowercase "x" — which is what the persisted section_name must reproduce.
FLAT_ROWS = ("130x12FL", "180x20FL", "250x12FL", "90x10FL")
EA_TOKEN = "25x25x3EA"

# The line the J4 `_write_dxf` helper draws for each callout is 3000 mm.
CALL_OUT_LENGTH_M = 3.0

RESOLUTION_EXACT = "EXACT"
RESOLUTION_SUFFIX_FALLBACK = "SUFFIX_FALLBACK"
RESOLUTION_NONE = "NONE"


# ==========================================================================
# Driving the two production paths through their existing seams.
# ==========================================================================


@pytest.fixture(autouse=True)
def _bind_page_factory(production):
    """Binds J4's `_page` helper to the real production PageExtraction.

    J4 does this for its own module; the helper is reused here rather than
    restated, so the same binding has to be in scope in this module too.
    """
    previous = j4._PAGE_FACTORY
    j4._PAGE_FACTORY = production.PageExtraction
    yield
    j4._PAGE_FACTORY = previous


def _dxf_run(production, monkeypatch, tmp_path, callouts, name):
    """One genuine DXF through the real parser against the LIVE catalogue.

    Returns (summary, member rows the parser tried to persist, recorder). The
    recorder is returned too so a test can prove how many insert calls were
    made, not just what was in them.
    """
    recorder = _RecordingSupabase()
    monkeypatch.setattr(production.dxf, "supabase", recorder)
    path = _write_dxf(tmp_path / name, list(callouts))
    summary = production.dxf.parse_dxf_and_save(str(path), f"j7-{name}")
    return summary, recorder.rows_inserted_into("steel_members"), recorder


def _dxf_row(production, monkeypatch, tmp_path, token, name=None):
    """The single DXF member row for one drawn token."""
    _, rows, _ = _dxf_run(
        production, monkeypatch, tmp_path, [token], name or f"{token.replace('.', '_')}.dxf",
    )
    assert len(rows) == 1, f"expected exactly one persisted row for {token!r}, got {len(rows)}"
    return rows[0]


def _pdf_row(production, pdf_boundary, token, **kwargs):
    """The single persisted PDF member row for one drawn token."""
    result = pdf_boundary([_page(1, [_member("M1", token, **kwargs)])])
    return _single_row(result.members, "M1")


def _authority_fields(row):
    """A member's section AUTHORITY, on either path, in a comparable form.

    Deliberately excludes every path-specific field (marks, source layers,
    confidence, J6 bookkeeping): this is the set of values that must mean the
    same thing whichever reader the drawing came from.
    """
    return {
        "section_name_is_none": row["section_name"] is None,
        "resolution": row["section_resolution"],
        "candidate_is_none": row.get("section_substituted_candidate") is None,
        "family_is_none": row.get("section_family") is None,
        "weight_is_none": row.get("weight_per_metre") is None,
        "total_is_none": row.get("total_weight_kg") is None,
        "needs_review": row.get("review_status") == "review_required",
        "reference_identity": row.get("reference_data_identity"),
    }


def _live_projection(production):
    """The J6 projection of the identity the LIVE matcher reports."""
    matcher = production.matcher_module.SectionMatcher()
    return production.matcher_module.reference_data_projection(matcher.reference_identity)


# ==========================================================================
# A — EXACT: the catalogue's own name, and only that, may identify the member
# ==========================================================================


class TestAExactKeepsTheCatalogueIdentity:
    def test_the_persisted_identity_is_the_catalogues_own_name(self, production, monkeypatch, tmp_path, live):
        row = _dxf_row(production, monkeypatch, tmp_path, EXACT_TOKEN)

        assert EXACT_TOKEN in live.names, "precondition: the catalogue carries this section"
        assert row["section_name"] == "310UB46.2"
        assert row["section_name_raw"] == "310UB46.2"
        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["section_substituted_candidate"] is None

    def test_the_catalogue_enrichment_is_used_exactly_as_the_catalogue_reports_it(
        self, production, monkeypatch, tmp_path, live
    ):
        row = _dxf_row(production, monkeypatch, tmp_path, EXACT_TOKEN)
        carried = live.by_name["310UB46.2"]

        assert carried["family"] == "UB" and carried["weight_per_metre"] == 46.2
        assert row["section_family"] == "UB"
        assert row["weight_per_metre"] == 46.2
        assert row["total_weight_kg"] == round(CALL_OUT_LENGTH_M * 46.2, 2) == 138.6

    def test_an_exact_member_is_not_held_for_review_on_either_path(
        self, production, monkeypatch, tmp_path, pdf_boundary
    ):
        """The `review_status` of an EXACT member, stated for both paths.

        The PDF path writes the state by name ("extracted"). The DXF path
        carries no review vocabulary at all, which is the contract its accepted
        boundary suite pins — and which reads the same way downstream, because
        every gate in this system tests for "review_required" (see
        app/cad_engine/real_member_adapter.py). Neither is held for review.
        """
        dxf_row = _dxf_row(production, monkeypatch, tmp_path, EXACT_TOKEN)
        pdf_row = _pdf_row(production, pdf_boundary, EXACT_TOKEN)

        assert "review_status" not in dxf_row
        assert dxf_row.get("review_status") != "review_required"
        assert pdf_row["review_status"] == "extracted"

    def test_the_j6_reference_identity_is_preserved_on_an_exact_member(
        self, production, monkeypatch, tmp_path
    ):
        row = _dxf_row(production, monkeypatch, tmp_path, EXACT_TOKEN)

        assert row["reference_data_identity"] == _live_projection(production)


# ==========================================================================
# B — SUFFIX_FALLBACK: refused as identity, FK-safe as a payload
# ==========================================================================


class TestBSuffixFallbackIsRefusedAndFkSafe:
    def test_the_persisted_row_states_the_refusal_field_for_field(
        self, production, monkeypatch, tmp_path, live
    ):
        assert SUFFIX_FALLBACK_TOKEN not in live.names, "precondition: not a catalogue key"
        assert SUFFIX_FALLBACK_CANDIDATE in live.names

        row = _dxf_row(production, monkeypatch, tmp_path, SUFFIX_FALLBACK_TOKEN)

        assert row["section_name"] is None
        assert row["section_name_raw"] == "310UB40"
        assert row["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert row["section_substituted_candidate"] == "310UB40.4"
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        assert row["review_status"] == "review_required"

    def test_neither_identity_is_substituted_for_the_other(
        self, production, monkeypatch, tmp_path
    ):
        row = _dxf_row(production, monkeypatch, tmp_path, SUFFIX_FALLBACK_TOKEN)

        # Not the drawn token: it is not a catalogue key. Not the candidate: that
        # is a DIFFERENT section, and adopting it would rename the member.
        assert row["section_name"] != "310UB40"
        assert row["section_name"] != "310UB40.4"
        assert row["section_name_raw"] != "310UB40.4"
        # Nothing about the refused row's PROPERTIES came across either: the
        # candidate is named in exactly two places — the provenance column that
        # exists to record what was refused, and the human-readable note. It is
        # NOT the member's identity, family or weight.
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        mentions = sorted(
            key for key, value in row.items() if "310UB40.4" in str(value)
        )
        assert mentions == ["notes", "section_substituted_candidate"]

    def test_the_payload_is_fk_safe_against_the_live_catalogue(
        self, production, monkeypatch, tmp_path, live
    ):
        """The strongest read-only statement available: every value this parser
        offers to the FK-bound column is a key of the live catalogue — or NULL,
        which no foreign key rejects."""
        _, rows, _ = _dxf_run(
            production, monkeypatch, tmp_path,
            [EXACT_TOKEN, SUFFIX_FALLBACK_TOKEN, NONE_TOKEN], "fk-safe.dxf",
        )

        assert len(rows) == 3
        offered = [row["section_name"] for row in rows]
        assert offered.count(None) == 2
        for value in offered:
            assert value is None or value in live.names

    def test_the_candidate_survives_only_as_refusal_provenance(
        self, production, monkeypatch, tmp_path
    ):
        row = _dxf_row(production, monkeypatch, tmp_path, SUFFIX_FALLBACK_TOKEN)

        assert row["section_substituted_candidate"] == "310UB40.4"
        assert "310UB40.4" in row["notes"]
        assert "310UB40" in row["notes"]
        # The note is the production one, not a new dialect invented here.
        assert row["notes"] == production.rules.SUBSTITUTION_NOTE.format(
            token="310UB40", candidate="310UB40.4",
        )


# ==========================================================================
# C — NONE: persisted, and claiming nothing
# ==========================================================================


class TestCNoneIsPersistedWithoutAClaim:
    def test_the_member_is_persisted_rather_than_discarded(
        self, production, monkeypatch, tmp_path, live
    ):
        assert NONE_TOKEN not in live.names, "precondition: absent from the catalogue"
        summary, rows, _ = _dxf_run(
            production, monkeypatch, tmp_path, [NONE_TOKEN], "none.dxf",
        )

        assert len(rows) == 1, "the old `continue` discarded this member entirely"
        assert summary["members_extracted"] == 1

    def test_the_persisted_row_claims_no_catalogue_identity(
        self, production, monkeypatch, tmp_path
    ):
        row = _dxf_row(production, monkeypatch, tmp_path, NONE_TOKEN)

        assert row["section_name"] is None
        assert row["section_resolution"] == RESOLUTION_NONE
        assert row["section_substituted_candidate"] is None
        assert row["section_family"] is None
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None
        assert row["review_status"] == "review_required"

    def test_the_drawn_token_is_retained_verbatim_by_the_extractor(
        self, production, monkeypatch, tmp_path
    ):
        """What "the raw drawn token" means on this path, measured rather than
        assumed: the persisted value is the token the matcher's own regex
        extracted — the same value that went into `section_name` before J7, so
        nothing that previously survived now fails to. The matcher's vocabulary
        truncates 250X90PFC to 90PFC; that is a pre-existing extraction property,
        pinned by the J4/J5 suites and untouched by J7, not a J7 regression. The
        untruncated control token proves the retention itself is verbatim.
        """
        truncated = _dxf_row(production, monkeypatch, tmp_path, NONE_TOKEN)
        control = _dxf_row(production, monkeypatch, tmp_path, UNTRUNCATED_NONE_TOKEN)

        assert truncated["section_name_raw"] == "90PFC"
        assert control["section_name_raw"] == "310UB99"
        assert control["section_name"] is None
        assert control["section_resolution"] == RESOLUTION_NONE

    def test_the_live_raw_token_is_the_one_the_old_contract_persisted(
        self, production, monkeypatch, tmp_path
    ):
        """The exact value the pre-J7 row carried in `section_name` — so this
        milestone moved the column, it did not lose the token."""
        matcher = production.matcher_module.SectionMatcher()
        extracted = matcher.get_section_regex().findall(NONE_TOKEN)[0]
        row = _dxf_row(production, monkeypatch, tmp_path, NONE_TOKEN)

        assert extracted == "90PFC"
        assert row["section_name_raw"] == extracted

    def test_the_j6_reference_identity_is_retained(self, production, monkeypatch, tmp_path):
        row = _dxf_row(production, monkeypatch, tmp_path, NONE_TOKEN)

        assert row["reference_data_identity"] == _live_projection(production)


# ==========================================================================
# D — FLAT: all four live rows, through the genuine live matcher
# ==========================================================================


class TestDFlatRowsResolveExactly:
    @pytest.mark.parametrize("catalogue_name", FLAT_ROWS)
    def test_each_flat_row_resolves_to_its_own_catalogue_identity(
        self, production, monkeypatch, tmp_path, live, catalogue_name
    ):
        assert catalogue_name in live.names, f"{catalogue_name} missing from the live catalogue"
        assert live.by_name[catalogue_name]["family"] == "FLAT"

        row = _dxf_row(
            production, monkeypatch, tmp_path, catalogue_name, f"flat-{catalogue_name}.dxf",
        )

        assert row["section_resolution"] == RESOLUTION_EXACT
        # The identity IS the catalogue's spelling, and it is FK-safe because it
        # is literally a key of the table the FK points at.
        assert row["section_name"] == catalogue_name
        assert row["section_name"] in live.names
        assert row["section_name_raw"] == catalogue_name
        assert row["section_family"] == "FLAT"
        assert row["section_substituted_candidate"] is None

    @pytest.mark.parametrize(
        "catalogue_name,weight",
        [("130x12FL", None), ("180x20FL", 28.3), ("250x12FL", 23.6), ("90x10FL", None)],
    )
    def test_the_weight_semantics_are_the_catalogues_own(
        self, production, monkeypatch, tmp_path, live, catalogue_name, weight
    ):
        """Two of the four carry an evidenced weight and two carry none. The
        catalogue's value is used as-is, including its absence: a row with no
        weight gets no weight and NO total, never a substituted or fabricated
        number.

        The catalogue's value is asserted first, so this test fails loudly if the
        live table changes rather than silently following it.
        """
        assert live.by_name[catalogue_name]["weight_per_metre"] == weight

        row = _dxf_row(
            production, monkeypatch, tmp_path, catalogue_name, f"flat-w-{catalogue_name}.dxf",
        )

        assert row["weight_per_metre"] == weight
        if weight is None:
            assert row["total_weight_kg"] is None
        else:
            assert row["total_weight_kg"] == round(CALL_OUT_LENGTH_M * weight, 2)


# ==========================================================================
# E — EA: an exact identity whose catalogue row is honest about what it lacks
# ==========================================================================


class TestEExactIdentityWithMissingProperties:
    DIMENSION_COLUMNS = (
        "depth", "flange_width", "flange_thickness", "web_thickness", "outside_diameter",
    )

    def test_the_identity_is_exact_and_the_properties_are_the_catalogues_own(
        self, production, monkeypatch, tmp_path, live
    ):
        assert live.by_name[EA_TOKEN]["family"] == "EA"
        assert live.by_name[EA_TOKEN]["weight_per_metre"] == 1.12

        row = _dxf_row(production, monkeypatch, tmp_path, EA_TOKEN, "ea.dxf")

        assert row["section_resolution"] == RESOLUTION_EXACT
        assert row["section_name"] == EA_TOKEN
        assert row["section_name_raw"] == EA_TOKEN
        assert row["section_family"] == "EA"
        assert row["weight_per_metre"] == 1.12
        assert row["total_weight_kg"] == round(CALL_OUT_LENGTH_M * 1.12, 2)

    def test_the_missing_dimensions_are_not_invented_on_the_persisted_row(
        self, production, monkeypatch, tmp_path, live
    ):
        """This catalogue row carries a leg size and a thickness and NOTHING
        else — no depth, no flange dimensions, no outside diameter. The member
        row must make no claim about them: absent catalogue properties are
        absent here, not defaulted, not zeroed, not invented.
        """
        catalogue_row = live.by_name[EA_TOKEN]
        for column in self.DIMENSION_COLUMNS:
            assert catalogue_row[column] is None, f"precondition: {column} has no value"

        row = _dxf_row(production, monkeypatch, tmp_path, EA_TOKEN, "ea-dimensions.dxf")

        for column in self.DIMENSION_COLUMNS:
            assert column not in row, f"the persisted row invented a {column!r}"
        # Nor any of the other names the catalogue uses for a dimension it lacks.
        for column in ("width", "thickness", "leg_size"):
            assert column not in row, f"the persisted row copied {column!r} from nowhere"
        assert "plate" not in json.dumps(row)


# ==========================================================================
# F — same mark, two different resolutions: both records survive
# ==========================================================================


class TestFSameMarkConflict:
    def test_both_evidence_records_survive_with_their_resolutions_intact(
        self, production, pdf_boundary
    ):
        """A mark confirmed on one page and UNRESOLVED on another is a mark the
        drawing set does not settle. Neither row may be reduced to the other, and
        the surviving NONE row must still hold its unresolved state and its token.

        This conflict lives on the PDF path: it is `validate_extraction` ->
        `consolidate_members` that groups pages by mark. The DXF path gives every
        callout its own mark, so it has no such grouping to exercise.
        """
        result = pdf_boundary([
            _page(1, [_member("M1", EXACT_TOKEN, length_mm=3000.0)]),
            _page(2, [_member("M1", NONE_TOKEN, length_mm=3000.0)]),
        ])
        rows = _rows_for(result.members, "M1")

        assert len(rows) == 2, "the unresolved evidence was discarded in favour of the confirmed row"
        by_resolution = {row["section_resolution"]: row for row in rows}
        assert set(by_resolution) == {RESOLUTION_EXACT, RESOLUTION_NONE}

        confirmed = by_resolution[RESOLUTION_EXACT]
        assert confirmed["section_name"] == EXACT_TOKEN
        assert confirmed["section_name_raw"] == EXACT_TOKEN
        assert confirmed["weight_per_metre"] == 46.2

        unresolved = by_resolution[RESOLUTION_NONE]
        assert unresolved["section_name"] is None
        assert unresolved["section_name_raw"] == NONE_TOKEN
        assert unresolved["section_family"] is None
        assert unresolved["total_weight_kg"] is None

        # Neither is treated as agreed, and the project says which one it cannot
        # identify — the unresolved token, not the confirmed one.
        assert {row["review_status"] for row in rows} == {"review_required"}
        assert result.project_row["unmatched_sections"] == [NONE_TOKEN]
        assert any(
            "resolves differently" in message for message in result.project_row["warnings"]
        ), "the conflict is reported rather than silently resolved"

    def test_a_refused_substitution_is_not_reported_as_an_unmatched_section(
        self, production, pdf_boundary
    ):
        """The other empty-section_name state, kept distinct: the catalogue DID
        answer a refused token, with a different section, so the project must not
        report it as one it cannot identify."""
        result = pdf_boundary([_page(1, [_member("M1", SUFFIX_FALLBACK_TOKEN)])])
        row = _single_row(result.members, "M1")

        assert row["section_name"] is None
        assert row["section_resolution"] == RESOLUTION_SUFFIX_FALLBACK
        assert result.project_row["unmatched_sections"] == []


# ==========================================================================
# G — PDF/DXF parity: the authority fields mean the same thing on both paths
# ==========================================================================


class TestGTheTwoPathsAgreeOnAuthority:
    @pytest.mark.parametrize(
        "token", [EXACT_TOKEN, SUFFIX_FALLBACK_TOKEN, UNTRUNCATED_NONE_TOKEN]
    )
    def test_the_three_core_cases_carry_equivalent_authority(
        self, production, monkeypatch, tmp_path, pdf_boundary, token
    ):
        dxf_row = _dxf_row(production, monkeypatch, tmp_path, token, f"parity-{token}.dxf")
        pdf_row = _pdf_row(production, pdf_boundary, token)

        assert _authority_fields(dxf_row) == _authority_fields(pdf_row)
        # ...and the token itself, where both extractors read it the same way.
        assert dxf_row["section_name_raw"] == pdf_row["section_name_raw"] == token

    def test_the_one_real_difference_is_the_dxf_extractors_own_vocabulary(
        self, production, monkeypatch, tmp_path, pdf_boundary
    ):
        """Stated, not hidden: for 250X90PFC the two paths persist different raw
        text, and the reason is the matcher's regex — not the authority rule.

        The PDF path stores the section text the AI stage reported. The DXF path
        stores the token the matcher's own regex extracted from the drawn text,
        which truncates this callout to "90PFC" — the identical value the pre-J7
        contract put in `section_name`. Both rows describe the same unresolved
        member; only the drawing text each path retains differs.
        """
        dxf_row = _dxf_row(production, monkeypatch, tmp_path, NONE_TOKEN, "parity-truncated.dxf")
        pdf_row = _pdf_row(production, pdf_boundary, NONE_TOKEN)

        assert dxf_row["section_name_raw"] == "90PFC"
        assert pdf_row["section_name_raw"] == NONE_TOKEN
        assert _authority_fields(dxf_row) == _authority_fields(pdf_row)


# ==========================================================================
# H — grade: the drawing's evidence or nothing at all
# ==========================================================================

# The literal J7 removed from the member payload, and its plate sibling, which
# Milestone J8B removed from the same module. Both are asserted as TEXT where
# they lived, and as a structural rule over the whole module, because either
# shape can be spelled in more than one way and only one of them is quoted here.
DXF_MEMBER_GRADE_LITERAL = '"grade": "300PLUS"'
DXF_PLATE_GRADE_LITERAL = '"grade": "300"'
_GRADE_KEYS = ("grade", "material")


def _literal_grade_values(source: str) -> list[str]:
    """
    Every dict literal in `source` that states a grade or a material as a
    hardcoded string.

    A persisted grade is a claim about a drawing, so a literal in production
    source is a fabricated one: it is written for every row the literal's path
    produces, whether or not that row's drawing stated anything. This scans
    PRODUCTION source only — the mutation controls under tests/ assert against
    these same literals deliberately, and are not this rule's business.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value in _GRADE_KEYS
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                found.append(value.value)
    return found


class TestHGradeIsEvidenceOrNothing:
    @pytest.mark.parametrize(
        "token", [EXACT_TOKEN, SUFFIX_FALLBACK_TOKEN, NONE_TOKEN, UNTRUNCATED_NONE_TOKEN]
    )
    def test_the_dxf_payload_states_its_absence_explicitly(
        self, production, monkeypatch, tmp_path, token
    ):
        """This path reads section tokens, not material callouts, so it has no
        grade evidence for any member. That absence is written BY KEY, which is
        also what makes the row independent of any database default: the column
        is supplied by this payload, not manufactured for it.
        """
        row = _dxf_row(production, monkeypatch, tmp_path, token, f"grade-{token}.dxf")

        assert "grade" in row, "the column is left to the database rather than stated"
        assert row["grade"] is None
        assert "300PLUS" not in json.dumps(row)

    def test_the_hardcoded_dxf_member_grade_is_gone_from_the_source(
        self, production, monkeypatch, tmp_path
    ):
        """The literal J7 removed, asserted where it lived."""
        source = (
            Path(production.dxf.__file__).read_text(encoding="utf-8")
        )
        assert '"grade": "300PLUS"' not in source
        assert "'grade': '300PLUS'" not in source

    def test_the_hardcoded_dxf_plate_grade_is_gone_from_the_source(self, production):
        """The plate sibling of the literal above, removed by Milestone J8B.

        The same module carried a second hardcode two lines below the member one,
        in the plate construction: every plate the DXF path wrote stated "300",
        including the plates whose callout stated no grade at all — and the plate
        callout carries a type and a thickness and nothing else, so no plate this
        path writes ever had a grade to state.
        """
        source = Path(production.dxf.__file__).read_text(encoding="utf-8")

        assert DXF_PLATE_GRADE_LITERAL not in source
        assert "'grade': '300'" not in source
        assert _literal_grade_values(source) == [], (
            "the DXF module states a grade as a source literal"
        )

    def test_neither_production_plate_writer_states_a_grade_literal(self, production):
        """Parity for the field the two paths share.

        Both production modules that can write a `connection_plates` row are
        scanned by the same rule: the PDF path reads its grade from the extraction
        (`app.pipeline._extracted_plate_grade`, pinned in
        test_real_world_connection_plate_grade_evidence.py), and the DXF path has
        no plate-grade evidence to read, so it states None. Neither may carry a
        literal that would be written for every plate regardless.
        """
        for module in (production.pipeline, production.dxf):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert _literal_grade_values(source) == [], module.__name__

    def test_the_plate_grade_source_rule_detects_the_old_behaviour(self, production):
        """The control: the rule fires on the source J8B replaced, and stays
        quiet on the source it left. The old plate construction is reconstructed
        as source, so this checks the CHECK rather than recording a file."""
        old_plate_construction = (
            "def parse_plates(text):\n"
            "    for plate_type, thickness in re.findall(plate_pattern, text, re.IGNORECASE):\n"
            '        result["plates"].append({"plate_type": plate_type,'
            ' "thickness": float(thickness), "grade": "300"})\n'
        )
        assert _literal_grade_values(old_plate_construction) == ["300"]

        source = Path(production.dxf.__file__).read_text(encoding="utf-8")
        assert _literal_grade_values(source) == []
        # The rule reads production source, so the mutation controls that state
        # these literals on purpose — under tests/ — are untouched by it and must
        # stay where they are.
        controls = (
            Path(__file__).resolve().parents[1]
            / "tests" / "test_real_world_connection_plate_grade_evidence.py"
        ).read_text(encoding="utf-8")
        assert 'PLATE_GRADE_DEFAULT = \'"grade": "300",\'' in controls
        assert 'MEMBER_GRADE_DEFAULT = \'"grade": "300PLUS",\'' in controls

    def test_the_pdf_path_retains_an_explicit_grade_verbatim(self, production, pdf_boundary):
        row = _pdf_row(production, pdf_boundary, EXACT_TOKEN, material="AS/NZS 3678-300")

        assert row["grade"] == "AS/NZS 3678-300"
        assert "300PLUS" not in json.dumps(row)

    def test_the_pdf_path_persists_absence_when_the_drawing_stated_no_grade(
        self, production, pdf_boundary
    ):
        row = _pdf_row(production, pdf_boundary, EXACT_TOKEN)

        assert "grade" in row
        assert row["grade"] is None
        assert "300PLUS" not in json.dumps(row)

    def test_the_two_paths_agree_on_the_resolution_fields_whatever_the_grade(
        self, production, monkeypatch, tmp_path, pdf_boundary
    ):
        with_material = _pdf_row(
            production, pdf_boundary, EXACT_TOKEN, material="AS/NZS 3678-300",
        )
        without_material = _pdf_row(production, pdf_boundary, EXACT_TOKEN)

        assert _authority_fields(with_material) == _authority_fields(without_material)
        assert with_material["grade"] != without_material["grade"]


# ==========================================================================
# I — a NULL catalogue weight stays NULL: no `or 0` conversion, either path
# ==========================================================================


class TestINullWeightIsNotZero:
    @pytest.mark.parametrize("catalogue_name", ["130x12FL", "90x10FL"])
    def test_a_member_with_no_evidenced_weight_has_no_weight_and_no_total(
        self, production, monkeypatch, tmp_path, live, catalogue_name
    ):
        assert live.by_name[catalogue_name]["weight_per_metre"] is None

        row = _dxf_row(
            production, monkeypatch, tmp_path, catalogue_name, f"nullweight-{catalogue_name}.dxf",
        )

        assert row["section_name"] == catalogue_name       # the identity is still exact
        assert row["weight_per_metre"] is None
        # NOT zero. A persisted 0 is indistinguishable from a section that really
        # weighs nothing, and downstream it reads as a measurement.
        assert row["total_weight_kg"] is None
        assert row["total_weight_kg"] != 0

    def test_the_project_total_treats_the_missing_weight_as_not_calculated(
        self, production, monkeypatch, tmp_path
    ):
        summary, rows, _ = _dxf_run(
            production, monkeypatch, tmp_path, [UNTRUNCATED_NONE_TOKEN], "not-calculated.dxf",
        )

        # Member level: absent. Project level: a sum over nothing, which this
        # system already reports as 0 — the two must not be confused, so both
        # halves are asserted.
        assert rows[0]["total_weight_kg"] is None
        assert summary["total_weight_kg"] == 0
        assert summary["unique_sections"] == 0

    def test_the_pdf_path_also_refuses_to_zero_a_missing_weight(self, production, pdf_boundary):
        row = _pdf_row(production, pdf_boundary, "130x12FL")

        assert row["section_name"] == "130x12FL"
        assert row["weight_per_metre"] is None
        assert row["total_weight_kg"] is None

    def test_a_known_weight_still_produces_a_total(self, production, monkeypatch, tmp_path):
        row = _dxf_row(production, monkeypatch, tmp_path, "180x20FL", "known-weight.dxf")

        assert row["weight_per_metre"] == 28.3
        assert row["total_weight_kg"] == round(CALL_OUT_LENGTH_M * 28.3, 2)


# ==========================================================================
# J — batch regression against the old silent `continue`
# ==========================================================================


class TestJOneBatchKeepsEveryMember:
    def test_an_exact_and_an_unresolved_member_land_in_the_same_insert(
        self, production, monkeypatch, tmp_path
    ):
        """The defect J7 removed, stated as one payload: the parser used to
        `continue` past a NONE resolution, so a mixed drawing persisted only the
        members the catalogue answered for — silently. Both must be in the SINGLE
        insert the parser makes.
        """
        summary, rows, recorder = _dxf_run(
            production, monkeypatch, tmp_path,
            [EXACT_TOKEN, NONE_TOKEN], "batch-regression.dxf",
        )

        inserts = [
            call for call in recorder.calls
            if call["table"] == "steel_members" and call["op"] == "insert"
        ]
        assert len(inserts) == 1, "the batch was split across inserts"
        assert len(inserts[0]["payload"]) == 2, "a member was dropped before the insert"

        assert len(rows) == 2
        by_resolution = {row["section_resolution"]: row for row in rows}
        assert set(by_resolution) == {RESOLUTION_EXACT, RESOLUTION_NONE}
        assert by_resolution[RESOLUTION_EXACT]["section_name"] == EXACT_TOKEN
        assert by_resolution[RESOLUTION_NONE]["section_name"] is None
        assert by_resolution[RESOLUTION_NONE]["section_name_raw"] == "90PFC"

        assert summary["members_extracted"] == 2
        assert summary["unique_sections"] == 1
        assert summary["total_weight_kg"] == 138.6

    def test_every_member_of_a_mixed_batch_stays_traceable_to_its_token(
        self, production, monkeypatch, tmp_path
    ):
        _, rows, _ = _dxf_run(
            production, monkeypatch, tmp_path,
            [EXACT_TOKEN, SUFFIX_FALLBACK_TOKEN, NONE_TOKEN], "batch-traceable.dxf",
        )

        # Three distinct marks, three distinct drawn tokens: nothing collapsed.
        assert [row["mark"] for row in rows] == ["M1", "M2", "M3"]
        assert [row["section_name_raw"] for row in rows] == ["310UB46.2", "310UB40", "90PFC"]
        assert [row["section_name"] for row in rows] == ["310UB46.2", None, None]


# ==========================================================================
# K — the J6 reference identity is carried identically on every path
# ==========================================================================


class TestKReferenceIdentityIsCarriedVerbatim:
    def test_the_projection_is_the_one_the_live_matcher_reports(
        self, production, monkeypatch, tmp_path, live
    ):
        projection = _live_projection(production)

        assert projection["reference_data_digest"] == live.digest
        assert projection["source_kind"] and projection["identity_status"]

    def test_every_dxf_member_carries_the_same_projected_identity(
        self, production, monkeypatch, tmp_path
    ):
        projection = _live_projection(production)
        _, rows, _ = _dxf_run(
            production, monkeypatch, tmp_path,
            [EXACT_TOKEN, SUFFIX_FALLBACK_TOKEN, NONE_TOKEN], "identity-dxf.dxf",
        )

        assert len(rows) == 3
        for row in rows:
            assert row["reference_data_identity"] == projection

    def test_every_pdf_member_carries_the_same_projected_identity(
        self, production, pdf_boundary
    ):
        projection = _live_projection(production)
        result = pdf_boundary([
            _page(1, [
                _member("M1", EXACT_TOKEN),
                _member("M2", SUFFIX_FALLBACK_TOKEN),
                _member("M3", NONE_TOKEN),
            ]),
        ])

        assert len(result.members) == 3
        for row in result.members:
            assert row["reference_data_identity"] == projection

    def test_the_identity_is_the_same_on_both_paths_for_the_same_member(
        self, production, monkeypatch, tmp_path, pdf_boundary
    ):
        dxf_row = _dxf_row(production, monkeypatch, tmp_path, EXACT_TOKEN, "identity-parity.dxf")
        pdf_row = _pdf_row(production, pdf_boundary, EXACT_TOKEN)

        assert dxf_row["reference_data_identity"] == pdf_row["reference_data_identity"]
        assert dxf_row["reference_data_identity"] == _live_projection(production)
