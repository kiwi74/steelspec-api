"""
J69 — MULTI-DOCUMENT EVIDENCE CONFLICT DETECTION (J67 slice S2).

WHAT THIS FILE IS

The proof of `app/cad_engine/evidence_conflict_detection.py`: that a genuine disagreement
between two comparable readings of one engineering field is DETECTED, that an apparent
disagreement which is not one (the same value twice, the same member set in another order,
a nominal string beside a numeric one) is NOT, and that nothing about a role, a document or a
rank can influence either answer.

    recorded references ──▶ canonical_value() ──▶ _disagreement_between() ──▶ state

WHAT IS REAL, AND WHAT IS NOT

    REAL      the module itself — every canonicaliser, every refusal, every rule — read from
              the module the application imports, and mutated from its own source in the
              last section. The vocabularies it copies are pinned to the modules that own
              them: `connection_review_package` (the plate keys), `connection_location` /
              `reviewed_connection_specification` (the six-number location form),
              `reviewed_connection_attachment` (the two attachment keys),
              `connections.SUPPORTED_CONNECTION_POSITIONS` (the closed pair), and J68's own
              `reconciliation_state` (the state vocabulary, the evidence kinds, the hole
              rule and the human-resolution rule).

    NOT REAL  layer A. There is NO evidence loader here and there is none in the module:
              `reconcile_field` is handed the readings, so nothing in this file reads a
              database, a bucket, the network, a PDF or a live row. Every address, every
              recording and every reading below is built in the test. That is deliberate —
              J66's evidence table holds zero rows in production and has no writer, so a test
              that read it would be measuring an empty table and calling the result proof.

WHAT THE MUTATIONS ARE

They are not extra tests. Each removes the one thing that protects a property and shows the
assertion goes red: the conflict comparison itself, the set-equivalence of member marks, the
refusal to read a number out of page text, the precedence of a human resolution, the absence
of a canonical form for identity, the closed pair a position must lie in, and the refusal to
silently drop an unrecognised plate key. A guard that cannot fail is not a guard, and this
milestone's central claim — that it detects disagreement without inventing it — is only worth
something if it can be shown to be enforceable.

WHAT THIS FILE DOES NOT CLAIM

It does not claim the Selby Ø18-versus-Ø22 case is resolved, or decided, or even reachable.
It does not claim a conflict means a connection is wrong. It does not claim two recordings of
one field from two documents will be found: only that IF two comparable readings disagree,
the disagreement is reported with both sides preserved and no winner chosen.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import subprocess
import sys
import types
from typing import Any

import pytest

from app.cad_engine import evidence_conflict_detection as ecd
from app.cad_engine import reconciliation_state as rs
from app.cad_engine import review_contract as contract
from app.cad_engine.connection_review_package import (
    _PLATE_DIMENSION_KEYS,
    _PLATE_KEYS,
)
from app.cad_engine.exception_resolution import CONNECTION_POSITION_CHOICES
from app.cad_engine.reviewed_connection_attachment import reviewed_connection_detail_to_attachments
from app.cad_engine.reviewed_connection_specification import (
    ReviewedConnectionSpecification,
    reviewed_connection_specification_to_location_record,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "app" / "cad_engine" / "evidence_conflict_detection.py"

# J67's eight fields, in J67's order. Pinned as a literal so a field can neither be dropped
# from the comparison layer nor silently added to it without this file changing.
J67_FIELDS = (
    "connection_id",
    "connected_member_marks",
    "position",
    "plate",
    "holes",
    "location",
    "attachments",
    "material",
)

# The seven fields that HAVE a comparable form. `connection_id` is absent on purpose: an
# identifier is never AI output, and two identities are not two engineering values.
COMPARABLE_FIELDS = tuple(field for field in J67_FIELDS if field != "connection_id")


# --------------------------------------------------------------------------------------
# The test's own builders. Nothing below reaches a store: an address, a recording and a
# reading are plain values the caller of a pure layer would supply.
# --------------------------------------------------------------------------------------
def _address(page: int = 7, *, document: str | None = None, run: str = "run-1",
             annotation: tuple[Any, Any, str] | None = None,
             anchor: Any = ("detail-24",)) -> ecd.EvidenceAddress:
    x, y, version = annotation if annotation else (None, None, None)
    return ecd.EvidenceAddress(
        drawing_id=f"drawing-{page}",
        page_number=page,
        analysis_run_id=run,
        document_id=document,
        annotation_x=x,
        annotation_y=y,
        extractor_version=version,
        anchor=anchor,
    )


def _reference(field: str, ordinal: int, kind: str, address: ecd.EvidenceAddress):
    return ecd.FieldEvidenceReference(
        field=field, ordinal=ordinal, evidence_kind=kind, address=address,
    )


def _reading(address: ecd.EvidenceAddress, value: Any) -> ecd.EvidenceReading:
    return ecd.EvidenceReading(address=address, value=value)


def _two_sources(field: str, left: Any, right: Any, **kwargs):
    """Two SOURCE recordings of one field, at two addresses, carrying two values."""
    first, second = _address(7), _address(8)
    return ecd.reconcile_field(
        field,
        [_reference(field, 1, "SOURCE", first), _reference(field, 2, "SOURCE", second)],
        [_reading(first, left), _reading(second, right)],
        **kwargs,
    )


# =============================================================================
# A. THE VOCABULARY — this module's copies, each pinned to the module that owns it.
# =============================================================================
class TestThePinnedVocabulary:
    def test_the_plate_keys_are_the_review_packages_own(self):
        """COPIED because both names are private to their module. A copy is only safe if it
        cannot drift, so this is the pin."""
        assert ecd.PLATE_KEYS == _PLATE_KEYS
        assert ecd.PLATE_DIMENSION_KEYS == _PLATE_DIMENSION_KEYS
        assert ecd.PLATE_DIMENSION_KEYS == ("width_mm", "depth_mm", "thickness_mm")
        assert set(ecd.PLATE_DIMENSION_KEYS) < set(ecd.PLATE_KEYS)

    def test_the_location_keys_are_the_existing_location_records_own(self):
        """Pinned to the record the existing 7J adapter consumes, produced by the existing
        conversion rather than restated from a docstring."""
        record = reviewed_connection_specification_to_location_record(
            ReviewedConnectionSpecification(connection_id="C1")
        )
        # The record is FLAT: the identity plus the six coordinates, in this order.
        assert tuple(key for key in record if key != "connection_id") == ecd.LOCATION_KEYS
        assert ecd.LOCATION_KEYS == ("x", "y", "z", "rotation_x", "rotation_y", "rotation_z")

    def test_the_attachment_keys_are_the_ones_the_existing_adapter_reads(self):
        """Pinned by DRIVING the existing 7N adapter: a record carrying exactly these two
        keys converts, and a record missing one does not."""
        good = reviewed_connection_detail_to_attachments(
            {"attachments": [{"member_mark": "B1", "surface_reference": "END"}]}, ["B1"],
        )
        assert len(good) == 1
        assert ecd.ATTACHMENT_KEYS == ("member_mark", "surface_reference")
        for key in ecd.ATTACHMENT_KEYS:
            entry = {"member_mark": "B1", "surface_reference": "END"}
            del entry[key]
            with pytest.raises(Exception):
                reviewed_connection_detail_to_attachments({"attachments": [entry]}, ["B1"])

    def test_the_closed_pair_is_the_existing_position_vocabulary(self):
        from app.cad_engine import connections as geometry

        assert ecd.POSITION_CHOICES == CONNECTION_POSITION_CHOICES
        assert ecd.POSITION_CHOICES == geometry.SUPPORTED_CONNECTION_POSITIONS
        assert ecd.POSITION_CHOICES == ("START", "END")

    def test_the_state_vocabulary_is_j68s_and_was_not_re_declared(self):
        """J69 introduces no state names of its own: it reports J68's vocabulary, so a
        reader of one reconciliation never has to learn a second."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for name in ("UNCITED", "DIRECT", "DERIVED", "CONFLICTED", "HUMAN_RESOLVED"):
            assert f'RECONCILIATION_{name} = "' not in source, name
        assert rs.RECONCILIATION_STATES == (
            "UNCITED", "DIRECT", "DERIVED", "CONFLICTED", "HUMAN_RESOLVED",
        )

    def test_the_evidence_kinds_are_j68s_own_imported_names(self):
        """J69 does not carry a third copy of the evidence-kind pair: J68 already holds the
        pinned copy, and this module imports it from there."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "from app.cad_engine import reconciliation_state as rs" in source
        assert "EVIDENCE_KIND_SOURCE" not in source.replace("rs.", "")
        assert rs.EVIDENCE_KINDS == ("SOURCE", "DERIVATION")

    def test_the_canonicaliser_table_covers_exactly_the_comparable_fields(self):
        """`connection_id` is absent, and its ABSENCE is the rule: an identifier has no
        comparable form, so two identities can never be reported as disagreeing."""
        assert set(ecd._CANONICALISERS) == set(COMPARABLE_FIELDS)
        assert rs.CONNECTION_ID_FIELD not in ecd._CANONICALISERS
        assert tuple(rs.RECONCILIATION_FIELDS) == J67_FIELDS


# =============================================================================
# B. THE CANONICAL FORMS — where a value becomes comparable, and where it does not.
# =============================================================================
class TestTheCanonicalForms:
    def test_an_unknown_field_is_refused_deterministically(self):
        for unknown in ("grid_reference", "holes_mm", "", "HOLES", None, 7):
            with pytest.raises(ValueError) as excinfo:
                ecd.canonical_value(unknown, "anything")
            assert list(rs.RECONCILIATION_FIELDS)[0] in str(excinfo.value)
            with pytest.raises(ValueError) as again:
                ecd.canonical_value(unknown, "anything")
            assert str(again.value) == str(excinfo.value)

    def test_member_marks_are_a_set_and_order_carries_nothing(self):
        assert ecd.canonical_value("connected_member_marks", ["B2", "B1"]) == \
            ecd.canonical_value("connected_member_marks", ["B1", "B2"])
        assert ecd.canonical_value("connected_member_marks", ("B1", "B2")) == \
            ecd.canonical_value("connected_member_marks", ["B1", "B2"])
        assert ecd.canonical_value("connected_member_marks", ["B1", "B3"]) != \
            ecd.canonical_value("connected_member_marks", ["B1", "B2"])

    def test_a_member_list_that_is_not_a_list_of_marks_has_no_comparable_form(self):
        for bad in ([], "B1", None, ["B1", None], ["B1", "  "], ["B1", 7], {"B1"}):
            assert ecd.canonical_value("connected_member_marks", bad) is None, bad

    def test_a_position_must_be_one_of_the_existing_closed_pair(self):
        assert ecd.canonical_value("position", "START") == "START"
        assert ecd.canonical_value("position", "END") == "END"
        for not_a_position in ("MIDDLE", "start", "S", "", "  START  ", None, 7, ["START"]):
            assert ecd.canonical_value("position", not_a_position) is None, not_a_position

    def test_a_plate_is_comparable_only_when_it_is_complete_and_recognised(self):
        complete = {"type": "END_PLATE", "thickness_mm": 10, "width_mm": 100, "depth_mm": 80}
        assert ecd.canonical_value("plate", complete) is not None
        assert ecd.canonical_value("plate", complete) == ecd.canonical_value("plate", dict(complete))
        # An INCOMPLETE plate has no comparable form: comparing it against a complete one
        # would report an omission as a disagreement about engineering content.
        for missing in ecd.PLATE_KEYS:
            incomplete = {k: v for k, v in complete.items() if k != missing}
            assert ecd.canonical_value("plate", incomplete) is None, missing
        # An unrecognised key is refused rather than silently dropped.
        assert ecd.canonical_value("plate", dict(complete, extra="x")) is None
        # A non-numeric dimension, and a non-string type, are refused.
        assert ecd.canonical_value("plate", dict(complete, thickness_mm="10")) is None
        assert ecd.canonical_value("plate", dict(complete, thickness_mm=True)) is None
        assert ecd.canonical_value("plate", dict(complete, thickness_mm=float("nan"))) is None
        assert ecd.canonical_value("plate", dict(complete, type=None)) is None
        assert ecd.canonical_value("plate", complete) != \
            ecd.canonical_value("plate", dict(complete, thickness_mm=12))

    def test_a_hole_value_is_comparable_only_as_an_explicit_number(self):
        assert ecd.canonical_value("holes", {"diameter_mm": 22}) == 22
        assert ecd.canonical_value("holes", {"diameter_mm": 22.0}) == 22
        assert ecd.canonical_value("holes", {"diameter_mm": 18}) != \
            ecd.canonical_value("holes", {"diameter_mm": 22})

    def test_l_no_nominal_hole_text_ever_has_a_comparable_form(self):
        for nominal in ("M20", "22mm holes", "18mm holes", "M20 bolts", "22", "Ø22",
                        "ALL HOLES Ø22 mm UNO", " 22 "):
            assert ecd.canonical_value("holes", {"diameter_mm": nominal}) is None, nominal
            assert ecd.canonical_value("holes", nominal) is None, nominal
        for not_a_diameter in ({}, {"quantity": 4}, {"diameter_mm": None},
                               {"diameter_mm": True}, {"diameter_mm": [22]}, [22], 22):
            assert ecd.canonical_value("holes", not_a_diameter) is None, not_a_diameter

    def test_l_no_text_is_ever_parsed_out_of_a_value(self):
        """'22mm holes' must not become 22. The canonicalisers call no parser, and the hole
        rule is J68's own — this pins that this module did not grow one of its own."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for parser in ("re.findall", "re.search", "re.match", "re.compile", "split(",
                       "strip('0123456789", "Decimal"):
            assert parser not in source, parser
        assert ecd.canonical_value("holes", {"diameter_mm": "22"}) is None

    def test_a_location_is_comparable_only_as_all_six_explicit_numbers(self):
        complete = {"x": 1, "y": 2, "z": 3, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0}
        assert ecd.canonical_value("location", complete) is not None
        for missing in ecd.LOCATION_KEYS:
            partial = {k: v for k, v in complete.items() if k != missing}
            assert ecd.canonical_value("location", partial) is None, missing
        assert ecd.canonical_value("location", dict(complete, x=None)) is None
        assert ecd.canonical_value("location", dict(complete, x="1")) is None
        assert ecd.canonical_value("location", dict(complete, extra=1)) is None
        assert ecd.canonical_value("location", complete) != \
            ecd.canonical_value("location", dict(complete, x=2))

    def test_attachments_are_a_set_of_member_surface_pairs(self):
        one = {"member_mark": "B1", "surface_reference": "END"}
        two = {"member_mark": "B2", "surface_reference": "START"}
        assert ecd.canonical_value("attachments", [one, two]) == \
            ecd.canonical_value("attachments", [two, one])
        assert ecd.canonical_value("attachments", [one]) != \
            ecd.canonical_value("attachments", [{"member_mark": "B1", "surface_reference": "START"}])
        # A surface outside the existing closed pair, a duplicate member, a malformed entry
        # and an unrecognised key are all refused rather than compared.
        assert ecd.canonical_value("attachments", [dict(one, surface_reference="MIDDLE")]) is None
        assert ecd.canonical_value("attachments", [one, dict(one)]) is None
        assert ecd.canonical_value("attachments", [{"member_mark": "B1"}]) is None
        assert ecd.canonical_value("attachments", [dict(one, extra=1)]) is None
        assert ecd.canonical_value("attachments", []) is None
        assert ecd.canonical_value("attachments", "B1") is None

    def test_material_is_compared_verbatim_and_never_normalised(self):
        assert ecd.canonical_value("material", "300") == "300"
        # Different spellings are different values: this layer does not decide they are the
        # same grade — that is a fact about the readings a human should see.
        assert ecd.canonical_value("material", "300") != ecd.canonical_value("material", "Grade 300")
        assert ecd.canonical_value("material", "300") != ecd.canonical_value("material", "300 ")
        for absent in (None, "", "   ", 300, ["300"]):
            assert ecd.canonical_value("material", absent) is None, absent

    def test_connection_id_has_no_comparable_form_at_all(self):
        """§6: an identifier is NEVER AI output, so no reading of it can establish one, and
        two identities are not two engineering values that could disagree."""
        for identity in ("C1", "RP-0001", "  C1  ", "", None, 7):
            assert ecd.canonical_value("connection_id", identity) is None, identity


# =============================================================================
# C. THE COMPARISON AND THE STATE (brief items A–N, Q, R).
# =============================================================================
class TestTheStates:
    def test_a_no_recordings_is_uncited(self):
        for field in J67_FIELDS:
            result = ecd.reconcile_field(field)
            assert (result.state, result.references, result.competing, result.unresolved) == \
                ("UNCITED", (), (), ()), field

    def test_b_one_source_is_direct(self):
        for field in COMPARABLE_FIELDS:
            address = _address(7)
            result = ecd.reconcile_field(
                field, [_reference(field, 1, "SOURCE", address)],
                [_reading(address, _any_value_for(field))],
            )
            assert result.state == "DIRECT", field
            assert len(result.competing) == 1

    def test_b_one_source_with_no_readable_value_is_still_direct_and_says_so(self):
        address = _address(7)
        result = ecd.reconcile_field("holes", [_reference("holes", 1, "SOURCE", address)])
        assert result.state == "DIRECT"
        assert result.competing == ()
        assert result.unresolved == (address,)

    def test_c_two_identical_source_values_are_direct(self):
        result = _two_sources("holes", {"diameter_mm": 22}, {"diameter_mm": 22})
        assert result.state == "DIRECT"
        assert len(result.competing) == 2
        assert result.unresolved == ()

    def test_d_two_identical_source_values_from_different_documents_are_direct(self):
        """The case J67 §4 named: two documents stating the same value are NOT a conflict.
        The words 'FAB' and 'ASM' appear nowhere — a document is an id here, and no role, no
        name and no path is read."""
        first, second = _address(7, document="doc-fab"), _address(8, document="doc-asm")
        result = ecd.reconcile_field(
            "holes",
            [_reference("holes", 1, "SOURCE", first), _reference("holes", 2, "SOURCE", second)],
            [_reading(first, {"diameter_mm": 22}), _reading(second, {"diameter_mm": 22})],
        )
        assert result.state == "DIRECT"
        assert [entry.value for entry in result.competing] == [22, 22]

    def test_e_two_genuinely_different_comparable_values_are_conflicted(self):
        result = _two_sources("holes", {"diameter_mm": 18}, {"diameter_mm": 22})
        assert result.state == "CONFLICTED"
        assert [entry.value for entry in result.competing] == [18, 22]
        assert result.unresolved == ()

    def test_m_numeric_diameter_eighteen_against_numeric_diameter_twenty_two(self):
        """The brief's own example, in the only form this system can compare."""
        assert _two_sources("holes", {"diameter_mm": 18}, {"diameter_mm": 22}).state == "CONFLICTED"
        assert _two_sources("holes", {"diameter_mm": 22.0}, {"diameter_mm": 18.0}).state == \
            "CONFLICTED"

    def test_n_a_numeric_value_against_nominal_text_invents_no_conflict(self):
        """§7: when one side is numeric and the other is merely nominal TEXT, no conflict may
        be manufactured by parsing. The nominal side is UNRESOLVED."""
        result = _two_sources("holes", {"diameter_mm": 22}, {"diameter_mm": "18mm holes"})
        assert result.state == "DIRECT"
        assert len(result.competing) == 1
        assert len(result.unresolved) == 1
        # And the reverse order says the same thing.
        assert _two_sources("holes", {"diameter_mm": "18mm holes"}, {"diameter_mm": 22}).state \
            == "DIRECT"
        # Two nominal strings are not a disagreement either.
        assert _two_sources("holes", {"diameter_mm": "18mm holes"},
                            {"diameter_mm": "22mm holes"}).state == "DIRECT"

    def test_n_the_intended_selby_case_produces_no_conflict_and_is_not_decided(self):
        """The intended future case is a fabrication reading and an assembly reading that
        state different diameters. Where both sides are NOMINAL TEXT, this layer reports NO
        conflict — it does not decide the case, and it does not parse the text to reach the
        answer it might prefer.

        HONESTY ABOUT THE FIXTURE: this is NOT the Selby capture. No fixture in this repo
        holds a citable Selby hole reading on either side — the strings used here are the
        same nominal strings this codebase names elsewhere in prose, and the two addresses
        are constructed here. So §16's condition ("only if existing fixture evidence can
        support it") is not met, and the result below is the RULE applied to a synthetic
        case, not a statement about Selby. That the real case is unreachable is the point:
        no numeric hole diameter exists anywhere in the current evidence model."""
        fab = _address(7, document="doc-fab")
        asm = _address(29, document="doc-asm")
        result = ecd.reconcile_field(
            "holes",
            [_reference("holes", 1, "SOURCE", fab), _reference("holes", 2, "SOURCE", asm)],
            [_reading(fab, "18mm holes"), _reading(asm, "22mm holes")],
        )
        assert result.state == "DIRECT"
        assert result.unresolved == (fab, asm)

    def test_f_three_sources_where_two_agree_and_one_disagrees_are_conflicted(self):
        first, second, third = _address(7), _address(8), _address(9)
        result = ecd.reconcile_field(
            "holes",
            [_reference("holes", 1, "SOURCE", first), _reference("holes", 2, "SOURCE", second),
             _reference("holes", 3, "SOURCE", third)],
            [_reading(first, {"diameter_mm": 22}), _reading(second, {"diameter_mm": 22}),
             _reading(third, {"diameter_mm": 18})],
        )
        assert result.state == "CONFLICTED"
        # ALL THREE sides are preserved, including the two that agree.
        assert [entry.value for entry in result.competing] == [22, 22, 18]
        assert [entry.address for entry in result.competing] == [first, second, third]

    def test_g_the_competing_evidence_is_ordered_deterministically(self):
        """RECORDING order, whatever order the caller supplied the references in. The order
        is the order the facts were recorded in and is compared with nothing."""
        addresses = [_address(page) for page in (7, 8, 9)]
        values = [{"diameter_mm": 18}, {"diameter_mm": 22}, {"diameter_mm": 20}]
        references = [_reference("holes", index + 1, "SOURCE", address)
                      for index, address in enumerate(addresses)]
        readings = [_reading(address, value) for address, value in zip(addresses, values)]

        import random

        baseline = None
        for seed in range(8):
            shuffled = list(zip(references, readings))
            random.Random(seed).shuffle(shuffled)
            result = ecd.reconcile_field(
                "holes", [pair[0] for pair in shuffled], [pair[1] for pair in shuffled],
            )
            snapshot = [(entry.address, entry.value) for entry in result.competing]
            assert result.state == "CONFLICTED"
            baseline = baseline or snapshot
            assert snapshot == baseline
        assert [entry.value for entry in ecd.reconcile_field(
            "holes", references, readings).competing] == [18, 22, 20]

    def test_r_the_same_value_at_different_addresses_is_never_a_conflict(self):
        """Two pages, two runs, two documents, two anchors, two occurrence positions — the
        SAME value is the same value. Nothing about WHERE it was read can create a
        disagreement."""
        variants = [
            (_address(7), _address(7, run="run-2")),
            (_address(7), _address(7, anchor=("detail-99",))),
            (_address(7), _address(7, annotation=("41.50", "190.00", "pdf-annotations-1"))),
            (_address(7, document="doc-a"), _address(7, document="doc-b")),
            (_address(7, anchor=()), _address(7, anchor=("S207",))),
        ]
        for left, right in variants:
            result = ecd.reconcile_field(
                "material",
                [_reference("material", 1, "SOURCE", left),
                 _reference("material", 2, "SOURCE", right)],
                [_reading(left, "300"), _reading(right, "300")],
            )
            assert result.state == "DIRECT", (left, right)
            assert len(result.competing) == 2

    def test_h_a_derivation_remains_derived(self):
        address = _address(7)
        result = ecd.reconcile_field(
            "material", [_reference("material", 1, "DERIVATION", address)],
            [_reading(address, "300")],
        )
        assert result.state == "DERIVED"

    def test_i_mixed_source_and_derivation_stays_derived_when_the_values_agree(self):
        """§14: kind multiplicity is NOT a disagreement. Two recordings that state the same
        value are not in conflict because one of them is a derivation."""
        source, derivation = _address(7), _address(8)
        result = ecd.reconcile_field(
            "material",
            [_reference("material", 1, "SOURCE", source),
             _reference("material", 2, "DERIVATION", derivation)],
            [_reading(source, "300"), _reading(derivation, "300")],
        )
        assert result.state == "DERIVED"
        assert len(result.competing) == 2

    def test_i_a_genuine_disagreement_between_a_source_and_a_derivation_is_conflicted(self):
        """§14's other half: what makes a conflict is DISAGREEMENT between comparable
        evidence, and a derivation that states a different value is comparable evidence."""
        source, derivation = _address(7), _address(8)
        result = ecd.reconcile_field(
            "material",
            [_reference("material", 1, "SOURCE", source),
             _reference("material", 2, "DERIVATION", derivation)],
            [_reading(source, "300"), _reading(derivation, "355")],
        )
        assert result.state == "CONFLICTED"
        assert [entry.value for entry in result.competing] == ["300", "355"]

    def test_j_a_human_resolution_overrides_a_conflict(self):
        for provenance, answers in (
            ("HUMAN_REVIEWED", ("FIELD_DECISION",)),
            ("HUMAN_SUPPLEMENTED", ("CONFIRMED_FIELDS",)),
        ):
            result = _two_sources(
                "holes", {"diameter_mm": 18}, {"diameter_mm": 22},
                provenance=provenance, resolved_answer_types=answers,
            )
            assert result.state == "HUMAN_RESOLVED", (provenance, answers)
            # The competing readings are STILL preserved: a resolution is a later fact, not
            # a reason to erase what the readings said.
            assert [entry.value for entry in result.competing] == [18, 22]

    def test_j_an_ai_reading_never_overrides_a_conflict(self):
        result = _two_sources(
            "holes", {"diameter_mm": 18}, {"diameter_mm": 22},
            provenance="AI_EXTRACTED", resolved_answer_types=("FIELD_DECISION",),
        )
        assert result.state == "CONFLICTED"

    def test_j_a_half_human_resolution_does_not_override_a_conflict(self):
        """J68's rule requires BOTH halves and fails closed. Provenance alone, and a resolved
        answer alone, each leave the conflict standing."""
        assert _two_sources(
            "holes", {"diameter_mm": 18}, {"diameter_mm": 22},
            provenance="HUMAN_REVIEWED",
        ).state == "CONFLICTED"
        assert _two_sources(
            "holes", {"diameter_mm": 18}, {"diameter_mm": 22},
            resolved_answer_types=("FIELD_DECISION",),
        ).state == "CONFLICTED"

    def test_k_connection_id_never_becomes_a_source_conflict(self):
        """§6: identity is human-owned, and no source-document conflict state exists for it.
        Two conflicting identities are reported as an UNCITED identity, not a conflict."""
        first, second = _address(7), _address(8)
        result = ecd.reconcile_field(
            "connection_id",
            [_reference("connection_id", 1, "SOURCE", first),
             _reference("connection_id", 2, "SOURCE", second)],
            [_reading(first, "C1"), _reading(second, "C2")],
        )
        assert result.state == "UNCITED"
        assert result.competing == ()
        assert len(result.unresolved) == 2

    def test_k_a_reviewer_supplied_identity_follows_j68s_semantics(self):
        first, second = _address(7), _address(8)
        result = ecd.reconcile_field(
            "connection_id",
            [_reference("connection_id", 1, "SOURCE", first),
             _reference("connection_id", 2, "SOURCE", second)],
            [_reading(first, "C1"), _reading(second, "C2")],
            reviewer_supplied=True,
        )
        assert result.state == "HUMAN_RESOLVED"

    def test_q_missing_evidence_is_never_read_as_agreement(self):
        """A cited address with no reading at all is UNRESOLVED, and one readable value
        beside one unreadable one is NOT agreement — it is one value and no comparison."""
        first, second, third = _address(7), _address(8), _address(9)
        references = [_reference("holes", index + 1, "SOURCE", address)
                      for index, address in enumerate((first, second, third))]
        result = ecd.reconcile_field(
            "holes", references, [_reading(first, {"diameter_mm": 22})],
        )
        assert result.state == "DIRECT"
        assert len(result.competing) == 1
        assert result.unresolved == (second, third)

    def test_q_a_missing_value_beside_two_agreeing_values_is_not_a_conflict(self):
        """§13: missing material is NOT automatically a conflict. Two recordings stating the
        same grade beside one that states nothing at all is one value and one OMISSION — the
        omission is reported as unresolved, never as disagreement."""
        first, second, third = _address(7), _address(8), _address(9)
        result = ecd.reconcile_field(
            "material",
            [_reference("material", 1, "SOURCE", first),
             _reference("material", 2, "SOURCE", second),
             _reference("material", 3, "SOURCE", third)],
            [_reading(first, "300"), _reading(second, "300"), _reading(third, None)],
        )
        assert result.state == "DIRECT"
        assert [entry.value for entry in result.competing] == ["300", "300"]
        assert result.unresolved == (third,)

    def test_the_material_comparison_is_verbatim_even_when_a_value_looks_like_something_else(self):
        """This layer knows no material vocabulary, so it does not filter values by one: two
        recordings that differ are reported, whatever the strings look like. Deciding that one
        of them is not a grade is a judgement about engineering content, and this module does
        not make judgements about engineering content."""
        assert _two_sources("material", "300", "M20").state == "CONFLICTED"

    def test_every_field_can_reach_every_non_conflicted_state(self):
        """A sweep: over the canonical-form space, no field ever reports CONFLICTED for a
        single reading, and connection_id never reports it at all."""
        values = {
            "connected_member_marks": [["B1", "B2"], ["B2", "B1"], []],
            "position": ["START", "END", "MIDDLE"],
            "plate": [{"type": "T", "thickness_mm": 10, "width_mm": 100, "depth_mm": 80}, {}],
            "holes": [{"diameter_mm": 18}, {"diameter_mm": "18mm holes"}],
            "location": [{"x": 1, "y": 2, "z": 3, "rotation_x": 0, "rotation_y": 0,
                          "rotation_z": 0}],
            "attachments": [[{"member_mark": "B1", "surface_reference": "END"}]],
            "material": ["300", 300],
        }
        for field in J67_FIELDS:
            for value in values.get(field, ["C1", 7, None]):
                address = _address(7)
                result = ecd.reconcile_field(
                    field, [_reference(field, 1, "SOURCE", address)], [_reading(address, value)],
                )
                assert result.state in rs.RECONCILIATION_STATES, (field, value)
                assert result.state != "CONFLICTED", (field, value)


def _any_value_for(field: str) -> Any:
    """One readable value for each comparable field, for the 'one SOURCE is DIRECT' sweep."""
    return {
        "connected_member_marks": ["B1"],
        "position": "START",
        "plate": {"type": "T", "thickness_mm": 10, "width_mm": 100, "depth_mm": 80},
        "holes": {"diameter_mm": 22},
        "location": {"x": 1, "y": 2, "z": 3, "rotation_x": 0, "rotation_y": 0, "rotation_z": 0},
        "attachments": [{"member_mark": "B1", "surface_reference": "END"}],
        "material": "300",
    }[field]


# =============================================================================
# D. ROLES AND AUTHORITY (brief items O, P).
# =============================================================================
class TestNoRoleDecidesAnything:
    def test_o_no_dataclass_in_the_read_model_can_hold_a_winner(self):
        """The read model has no field for a winner, a rank, a confidence, a priority, a
        primary source or a role — so no result can express one."""
        for model in (ecd.CompetingEvidence, ecd.FieldReconciliationResult,
                      ecd.FieldEvidenceReference, ecd.EvidenceReading, ecd.EvidenceAddress):
            names = {field.name for field in dataclasses.fields(model)}
            for forbidden in ("winner", "rank", "confidence", "priority", "primary",
                              "preferred", "authoritative", "chosen", "role", "document_role",
                              "authority", "score"):
                assert not any(forbidden in name for name in names), (model.__name__, forbidden)

    def test_o_no_function_in_the_module_takes_a_role_or_a_document(self):
        import inspect

        for name, value in vars(ecd).items():
            if name.startswith("_") or not callable(value):
                continue
            if isinstance(value, type):
                continue
            for parameter in inspect.signature(value).parameters:
                assert "role" not in parameter.lower(), (name, parameter)
                assert "document" not in parameter.lower(), (name, parameter)
                assert "winner" not in parameter.lower(), (name, parameter)

    def test_o_no_identifier_in_the_module_names_a_role_or_a_document(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
            elif isinstance(node, ast.arg):
                identifiers.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                identifiers.add(node.name)
        for name in sorted(identifiers):
            lowered = name.lower()
            for forbidden in ("role", "winner", "rank", "precedence", "primary", "authority"):
                assert forbidden not in lowered, (name, forbidden)

    def test_p_every_competing_side_is_preserved_whichever_role_proposed_it(self):
        """There is no role anywhere in the input, so no role can suppress a side. Three
        readings are three sides, and every value is carried out."""
        addresses = [_address(page) for page in (7, 8, 9)]
        result = ecd.reconcile_field(
            "material",
            [_reference("material", index + 1, "SOURCE", address)
             for index, address in enumerate(addresses)],
            [_reading(address, value) for address, value in
             zip(addresses, ["300", "355", "300"])],
        )
        assert result.state == "CONFLICTED"
        assert len(result.competing) == 3
        assert sorted(entry.value for entry in result.competing) == ["300", "300", "355"]

    def test_p_a_reading_is_never_dropped_because_another_disagrees(self):
        first, second = _address(7), _address(8)
        result = ecd.reconcile_field(
            "holes",
            [_reference("holes", 1, "SOURCE", first), _reference("holes", 2, "SOURCE", second),
             _reference("holes", 3, "SOURCE", first)],
            [_reading(first, {"diameter_mm": 22}), _reading(second, {"diameter_mm": 18})],
        )
        assert len(result.references) == 3
        assert len(result.competing) == 3  # the repeated address is compared twice


# =============================================================================
# E. THE EXISTING BEHAVIOUR THIS MUST NOT DISTURB (brief items W, and §19).
# =============================================================================
class TestTheBoundariesAreUntouched:
    def test_the_module_names_none_of_j66s_vocabulary(self):
        """J66 asserts that exactly three modules in `app/` name its vocabulary at all, which
        is how it makes 'no production writer exists' a checkable fact rather than a promise.
        This module must not break that fence and must not edit J66 to widen it — so this is
        the same assertion, read over this file's whole text, case-insensitively, exactly as
        the J66 fence reads it."""
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        assert "citation" not in source
        for name in ("citation_source", "citation_derivation", "citation_kinds",
                     "connection_review_item_citations",
                     "persist_connection_review_citations"):
            assert name not in source, name
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert "citation" not in alias.name.lower(), alias.name

    def test_the_j66_tree_wide_fence_would_still_hold_with_this_module_present(self):
        """J66's own area-39 assertion, recomputed here over the tree as it is NOW. If this
        passes and J66's test passes, the fence is exact with the new module in the tree."""
        naming = sorted(
            str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "app").rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/cad_engine/review_contract.py",
            "app/engineering_data/connection_review_repository.py",
        ]

    def test_a_recording_from_j66s_own_read_model_lines_up_field_for_field(self):
        """The address shape is J66's, not a second one: every address column of the real
        `ReviewFieldCitation` has a home in `EvidenceAddress`, verbatim."""
        from app.cad_engine.connection_review_snapshot import ReviewFieldCitation

        recorded = ReviewFieldCitation(
            project_id="p-1", review_revision=0, review_package_id="RP-0001",
            field_name="holes", ordinal=2, citation_kind="SOURCE",
            document_id="doc-1", drawing_id="drawing-7", page_number=7,
            analysis_run_id="run-1", annotation_x="41.50", annotation_y="190.00",
            extractor_version="pdf-annotations-1", anchor=("detail-24",),
        )
        address = ecd.EvidenceAddress(
            drawing_id=recorded.drawing_id, page_number=recorded.page_number,
            analysis_run_id=recorded.analysis_run_id, document_id=recorded.document_id,
            annotation_x=recorded.annotation_x, annotation_y=recorded.annotation_y,
            extractor_version=recorded.extractor_version, anchor=recorded.anchor,
        )
        for column in ("drawing_id", "page_number", "analysis_run_id", "document_id",
                       "annotation_x", "annotation_y", "extractor_version", "anchor"):
            assert getattr(address, column) == getattr(recorded, column), column
        reference = _reference(recorded.field_name, recorded.ordinal,
                               recorded.citation_kind, address)
        assert reference.evidence_kind in rs.EVIDENCE_KINDS
        assert {f.name for f in dataclasses.fields(ecd.EvidenceAddress)} == {
            "drawing_id", "page_number", "analysis_run_id", "document_id", "annotation_x",
            "annotation_y", "extractor_version", "anchor",
        }

    def test_j68_is_unchanged_and_still_detects_no_conflict(self):
        """§19/§21 W: J68 is not modified. Its flag is still False, its seam still returns
        False, and its state rule still never reports a conflict over the same evidence."""
        assert rs.CONFLICT_DETECTION_IMPLEMENTED is False
        for field in rs.RECONCILIATION_FIELDS:
            for kinds in (("SOURCE", "SOURCE"), ("SOURCE", "DERIVATION")):
                assert rs.field_state(rs.FieldReviewEvidence(
                    field=field, evidence_kinds=kinds,
                )) != rs.RECONCILIATION_CONFLICTED

    def test_j66s_own_standing_rule_is_unchanged(self):
        class _Recorded:
            field_name = "holes"
            citation_kind = "SOURCE"

        standings = contract.field_standings([_Recorded()])
        by_field = {standing.field: standing.standing for standing in standings}
        assert by_field["holes"] == contract.STANDING_DIRECT
        assert set(by_field.values()) <= {
            contract.STANDING_UNCITED, contract.STANDING_DIRECT, contract.STANDING_DERIVED,
        }

    def test_the_module_adds_no_review_revision_and_writes_nothing(self):
        """§18's preference, asserted over the text: no revision is produced and no store is
        reached — there is no writer verb, no client and no table name anywhere in the file.
        (The docstring NAMES the storage layer while saying it is not read; the assertions are
        therefore about write mechanisms, and the import graph is checked separately.)"""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("review_revision", "review_package_id", "persist_", "insert into",
                          ".table(", ".rpc(", "create table", "put_object", "upload("):
            assert forbidden not in source, forbidden


# =============================================================================
# F. PURITY (brief item V).
# =============================================================================
class TestPurity:
    def test_v_the_comparison_layer_imports_no_io_library(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        allowed_stdlib = {"__future__", "math", "collections", "dataclasses"}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for module in sorted(imported):
            root = module.split(".")[0]
            in_app = module == "app.cad_engine" or module.startswith("app.cad_engine.")
            assert in_app or root in allowed_stdlib, module
        # And specifically NOT the geometry kernel `connections.py` lives behind, which is
        # why the closed pair is taken from the existing pinned copy.
        assert "app.cad_engine.connections" not in imported

    def test_v_the_module_performs_no_io_and_opens_nothing(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                assert name not in ("open", "connect", "execute", "rpc", "table", "print",
                                    "getenv", "environ"), name

    def test_v_a_fresh_interpreter_pulls_no_database_or_http_client(self):
        probe = (
            "import sys\n"
            "import app.cad_engine.evidence_conflict_detection as ecd\n"
            "forbidden = ('supabase', 'httpx', 'requests', 'urllib3', 'postgrest',"
            " 'storage3', 'gotrue', 'psycopg', 'boto3', 'sqlalchemy')\n"
            "bad = sorted(m for m in sys.modules if any(k in m for k in forbidden))\n"
            "print(repr((list(ecd.POSITION_CHOICES), bad)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
        )
        assert result.returncode == 0, result.stderr
        positions, bad = eval(result.stdout.strip())  # noqa: S307 - our own probe output
        assert tuple(positions) == ecd.POSITION_CHOICES
        assert bad == [], bad

    def test_v_the_same_inputs_give_the_same_answer_every_time(self):
        first, second = _address(7), _address(8)
        references = [_reference("holes", 1, "SOURCE", first),
                      _reference("holes", 2, "SOURCE", second)]
        readings = [_reading(first, {"diameter_mm": 22}), _reading(second, {"diameter_mm": 18})]
        baseline = ecd.reconcile_field("holes", references, readings)
        for _ in range(5):
            assert ecd.reconcile_field("holes", references, readings) == baseline

    def test_v_a_readings_order_does_not_change_the_answer(self):
        first, second = _address(7), _address(8)
        references = [_reference("holes", 1, "SOURCE", first),
                      _reference("holes", 2, "SOURCE", second)]
        forwards = ecd.reconcile_field(
            "holes", references,
            [_reading(first, {"diameter_mm": 22}), _reading(second, {"diameter_mm": 18})],
        )
        backwards = ecd.reconcile_field(
            "holes", references,
            [_reading(second, {"diameter_mm": 18}), _reading(first, {"diameter_mm": 22})],
        )
        assert forwards == backwards

    def test_a_reference_to_another_field_is_refused(self):
        with pytest.raises(ValueError) as excinfo:
            ecd.reconcile_field("holes", [_reference("plate", 1, "SOURCE", _address(7))])
        assert "plate" in str(excinfo.value)

    def test_two_references_with_one_recording_order_are_refused(self):
        first, second = _address(7), _address(8)
        with pytest.raises(ValueError):
            ecd.reconcile_field("holes", [
                _reference("holes", 1, "SOURCE", first),
                _reference("holes", 1, "SOURCE", second),
            ])

    def test_two_readings_for_one_address_are_refused(self):
        address = _address(7)
        with pytest.raises(ValueError):
            ecd.reconcile_field(
                "holes", [_reference("holes", 1, "SOURCE", address)],
                [_reading(address, {"diameter_mm": 22}), _reading(address, {"diameter_mm": 18})],
            )

    def test_reconcile_fields_states_every_field(self):
        first, second = _address(7), _address(8)
        results = ecd.reconcile_fields(
            [_reference("holes", 1, "SOURCE", first),
             _reference("holes", 2, "SOURCE", second)],
            [_reading(first, {"diameter_mm": 18}), _reading(second, {"diameter_mm": 22})],
        )
        assert tuple(result.field for result in results) == rs.RECONCILIATION_FIELDS
        by_field = {result.field: result.state for result in results}
        assert by_field["holes"] == "CONFLICTED"
        assert by_field["plate"] == "UNCITED"
        assert by_field["connection_id"] == "UNCITED"

    def test_reconcile_fields_reads_a_human_resolution_from_the_existing_record(self):
        first, second = _address(7), _address(8)
        results = ecd.reconcile_fields(
            [_reference("holes", 1, "SOURCE", first),
             _reference("holes", 2, "SOURCE", second)],
            [_reading(first, {"diameter_mm": 18}), _reading(second, {"diameter_mm": 22})],
            human_resolutions=[("holes", {
                "provenance": "HUMAN_SUPPLEMENTED",
                "resolved_answer_types": ("HOLES_VALUE",),
            })],
        )
        by_field = {result.field: result.state for result in results}
        assert by_field["holes"] == "HUMAN_RESOLVED"

    def test_reconcile_fields_refuses_a_resolution_for_an_unknown_field(self):
        with pytest.raises(ValueError):
            ecd.reconcile_fields(human_resolutions=[("grid_reference", {})])

    def test_the_human_rule_is_j68s_own_and_not_a_second_one(self):
        assert ecd.human_resolution_stands("plate", provenance="HUMAN_REVIEWED",
                                           resolved_answer_types=("FIELD_DECISION",)) is True
        assert ecd.human_resolution_stands("plate", provenance="AI_EXTRACTED",
                                           resolved_answer_types=("FIELD_DECISION",)) is False
        assert ecd.human_resolution_stands("plate", provenance="HUMAN_REVIEWED") is False
        assert ecd.human_resolution_stands("connection_id", reviewer_supplied=True) is True


# =============================================================================
# G. THE MUTATIONS — every property above stands on something that can fail.
# =============================================================================
def _mutant(old: str, new: str, *, name: str = "j69_mutant") -> types.ModuleType:
    """The module with one exact edit applied, executed in isolation.

    Source-level, so the mutation removes the property being asserted rather than faking a
    behaviour, and isolated, so the real module is untouched.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(MODULE_PATH)
    # The module uses `from __future__ import annotations`, so `@dataclass` resolves its
    # field annotations through `sys.modules[cls.__module__]` at class-creation time. The
    # mutant must therefore be registered in sys.modules while it executes, or every frozen
    # dataclass in it raises on import — which would make the mutation prove nothing about the
    # rule it removes. It is removed again straight after, so no mutant leaks.
    sys.modules[name] = module
    try:
        exec(compile(source.replace(old, new), "<j69-mutant>", "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


_CONFLICT_SEAM = """    return len(distinct) > 1
"""

_MARKS_EQUIVALENCE = """    return frozenset(marks_value)
"""

_HOLE_SAFETY = """    if not rs.holes_diameter_is_explicit_numeric(holes_value):
        return None
    return holes_value["diameter_mm"]
"""

_HUMAN_PRECEDENCE = """    if base == rs.RECONCILIATION_HUMAN_RESOLVED:
        state = base
"""

_POSITION_MEMBERSHIP = """    if not isinstance(position_value, str) or position_value not in POSITION_CHOICES:
        return None
"""

_PLATE_EXTRA_KEYS = """    if any(key not in PLATE_KEYS for key in plate_value):
        return None
"""

_IDENTITY_ABSENCE = """    "material": _canonical_material,
}
"""


def _recordings(field: str, left: Any, right: Any):
    first, second = _address(7), _address(8)
    return (
        [_reference(field, 1, "SOURCE", first), _reference(field, 2, "SOURCE", second)],
        [_reading(first, left), _reading(second, right)],
    )


class TestMutations:
    def test_mutation_1_removing_the_conflict_comparison_loses_every_conflict(self):
        """S: the comparison is what makes a disagreement visible. Remove it and section C's
        conflict assertions go red — which is what makes 'J69 detects disagreement' a
        guarantee rather than a claim."""
        mutant = _mutant(_CONFLICT_SEAM, "    return False\n")
        references, readings = _recordings("holes", {"diameter_mm": 18}, {"diameter_mm": 22})
        assert mutant.reconcile_field("holes", references, readings).state == "DIRECT"
        assert ecd.reconcile_field("holes", references, readings).state == "CONFLICTED"

    def test_mutation_2_making_marks_order_sensitive_invents_disagreements(self):
        """T: the equality semantics are what make two orderings of one member set the SAME
        value. Make the comparison order-sensitive and identical readings become a conflict."""
        mutant = _mutant(_MARKS_EQUIVALENCE, "    return tuple(marks_value)\n")
        references, readings = _recordings("connected_member_marks", ["B1", "B2"], ["B2", "B1"])
        assert mutant.reconcile_field(
            "connected_member_marks", references, readings,
        ).state == "CONFLICTED"
        assert ecd.reconcile_field(
            "connected_member_marks", references, readings,
        ).state == "DIRECT"

    def test_mutation_3_parsing_nominal_text_invents_a_hole_conflict(self):
        """U: the refusal to read a number out of page text is what keeps '18mm holes' and
        '22mm holes' from becoming an engineering disagreement. Loosen it and the safety
        assertion goes red."""
        mutant = _mutant(_HOLE_SAFETY, """    if not isinstance(holes_value, Mapping):
        return None
    return holes_value.get("diameter_mm")
""")
        assert mutant.canonical_value("holes", {"diameter_mm": "22mm holes"}) == "22mm holes"
        references, readings = _recordings(
            "holes", {"diameter_mm": "18mm holes"}, {"diameter_mm": "22mm holes"},
        )
        assert mutant.reconcile_field("holes", references, readings).state == "CONFLICTED"
        assert ecd.reconcile_field("holes", references, readings).state == "DIRECT"

    def test_mutation_4_removing_human_precedence_lets_a_conflict_beat_a_reviewer(self):
        mutant = _mutant(_HUMAN_PRECEDENCE, "    if False:\n        state = base\n")
        references, readings = _recordings("holes", {"diameter_mm": 18}, {"diameter_mm": 22})
        assert mutant.reconcile_field(
            "holes", references, readings,
            provenance="HUMAN_REVIEWED", resolved_answer_types=("FIELD_DECISION",),
        ).state == "CONFLICTED"
        assert ecd.reconcile_field(
            "holes", references, readings,
            provenance="HUMAN_REVIEWED", resolved_answer_types=("FIELD_DECISION",),
        ).state == "HUMAN_RESOLVED"

    def test_mutation_5_openening_the_closed_pair_accepts_a_position_from_prose(self):
        mutant = _mutant(_POSITION_MEMBERSHIP, """    if not isinstance(position_value, str):
        return None
""")
        assert mutant.canonical_value("position", "MIDDLE") == "MIDDLE"
        references, readings = _recordings("position", "MIDDLE", "START")
        assert mutant.reconcile_field("position", references, readings).state == "CONFLICTED"
        assert ecd.reconcile_field("position", references, readings).state == "DIRECT"

    def test_mutation_6_dropping_the_extra_key_refusal_silently_compares_a_partial_plate(self):
        mutant = _mutant(_PLATE_EXTRA_KEYS, "    if False:\n        return None\n")
        plate = {"type": "T", "thickness_mm": 10, "width_mm": 100, "depth_mm": 80}
        assert mutant.canonical_value("plate", dict(plate, unrecognised="x")) is not None
        assert ecd.canonical_value("plate", dict(plate, unrecognised="x")) is None

    def test_mutation_7_giving_identity_a_comparable_form_makes_two_ids_conflict(self):
        """The ABSENCE of a canonicaliser for connection_id is the rule. Add one and two
        identities become an engineering disagreement — the thing §6 forbids."""
        mutant = _mutant(_IDENTITY_ABSENCE, """    "material": _canonical_material,
    rs.CONNECTION_ID_FIELD: _canonical_material,
}
""")
        references, readings = _recordings("connection_id", "C1", "C2")
        assert mutant.reconcile_field("connection_id", references, readings).state == "CONFLICTED"
        assert ecd.reconcile_field("connection_id", references, readings).state == "UNCITED"

    def test_the_mutations_are_source_level_and_the_real_module_is_untouched(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for anchor in (_CONFLICT_SEAM, _MARKS_EQUIVALENCE, _HOLE_SAFETY, _HUMAN_PRECEDENCE,
                       _POSITION_MEMBERSHIP, _PLATE_EXTRA_KEYS, _IDENTITY_ABSENCE):
            assert source.count(anchor) == 1
        references, readings = _recordings("holes", {"diameter_mm": 18}, {"diameter_mm": 22})
        assert ecd.reconcile_field("holes", references, readings).state == "CONFLICTED"
