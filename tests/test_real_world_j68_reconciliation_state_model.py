"""
J68 — RECONCILIATION STATE MODEL (J67 slice S1).

WHAT THIS FILE IS

The proof of `app/cad_engine/reconciliation_state.py`: that the pure state model J67
designed exists, is deterministic, is immutable, is refused for a field it does not
describe, and — the part that matters most — that it does NOT detect conflicts.

    FIELD EVIDENCE (kinds, provenance, resolved answers)
      └── field_state() ──▶ UNCITED | DIRECT | DERIVED | CONFLICTED | HUMAN_RESOLVED

WHAT IS REAL, AND WHAT IS NOT

    REAL      the module itself — every constant, every matrix row, every rule, read from
              the module the application imports, and mutated from its own source in
              section G. The vocabularies it reuses are read from the modules that own
              them: `review_contract` (evidence kinds — against which the model's COPY is
              pinned below, engineering fields, the three standings),
              `reviewed_connection_specification` (provenance labels),
              `exception_resolution` (task status, answer types, and the task dataclass the
              resolved-answer reader consumes), and `repository.DOCUMENT_ROLES` (the stored
              role vocabulary J64's live CHECK enforces).

    NOT REAL  there is nothing else. This milestone has no database, no storage, no HTTP,
              no extraction and no server: the model is pure by construction and section F
              proves it two ways — by the module's own imports, and by a fresh interpreter.
              No live row is read, written or created.

WHAT THE MUTATIONS ARE

They are not extra tests. Each removes the one thing that protects a property and shows the
assertion goes red: the conflict seam, the DERIVED fail-closed rule, the human-provenance
rule, the numeric-diameter rule, the unknown-field refusal, and the `connection_id` special
case. A guard that cannot fail is not a guard, and the brief's central requirement — that
J68 must NOT detect conflicts — is only worth anything if it can be shown to be enforceable.

WHAT THIS FILE DOES NOT CLAIM

It does not claim a connection is reviewable, complete, fabricable or approved. It reads no
Selby document, resolves no Selby conflict, writes no citation, creates no review revision
and changes no existing behaviour: section E pins the J66 vocabulary it must not disturb.
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

from app.cad_engine import reconciliation_state as rs
from app.cad_engine.connection_review_package import AIExtractedBolt
from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_FIELD_DECISION,
    STATUS_OPEN,
    STATUS_RESOLVED,
    ExceptionResolutionTask,
    HumanResolution,
)
from app.cad_engine.review_contract import (
    CITATION_DERIVATION,
    CITATION_KINDS,
    CITATION_SOURCE,
    ENGINEERING_FIELDS,
    STANDING_DERIVED,
    STANDING_DIRECT,
    STANDING_UNCITED,
    field_standings,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
    REQUIRED_PROVENANCE_FIELDS,
    SUPPORTED_PROVENANCE_LABELS,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "app" / "cad_engine" / "reconciliation_state.py"

# The eight fields J67 §3 established, in J67's order. Pinned as a literal so a field can
# neither be dropped from the model nor silently added to it without this file changing.
J67_MATRIX_FIELDS = (
    "connection_id",
    "connected_member_marks",
    "position",
    "plate",
    "holes",
    "location",
    "attachments",
    "material",
)


# The seven engineering-content fields the recorded-evidence rule applies to.
# `connection_id` is excluded on purpose: it is an identifier, not engineering content, and
# no reading can supply one — so its state is never DIRECT or DERIVED (see `test_k`).
EVIDENCE_STATED_FIELDS = tuple(
    field for field in J67_MATRIX_FIELDS if field != "connection_id"
)


def _evidence(field: str, **kwargs: Any) -> rs.FieldReviewEvidence:
    return rs.FieldReviewEvidence(field=field, **kwargs)


def _resolved(task_id: str, task_type: str, answer_type: str, answer: Any) -> ExceptionResolutionTask:
    """A genuinely RESOLVED task in the existing record's own shape."""
    return ExceptionResolutionTask(
        task_id=task_id,
        task_type=task_type,
        blocker_codes=(),
        question="q",
        current_ai_value=None,
        answer_type=answer_type,
        allowed_choices=(),
        evidence_requirement="e",
        status=STATUS_RESOLVED,
        resolution=HumanResolution(
            task_id=task_id, task_type=task_type, answer_type=answer_type, answer=answer,
        ),
    )


# =============================================================================
# A. THE VOCABULARIES — the model's own, and the three it reuses.
# =============================================================================
class TestTheStateVocabulary:
    def test_the_five_states_are_exactly_the_five_j67_named(self):
        assert rs.RECONCILIATION_STATES == (
            rs.RECONCILIATION_UNCITED,
            rs.RECONCILIATION_DIRECT,
            rs.RECONCILIATION_DERIVED,
            rs.RECONCILIATION_CONFLICTED,
            rs.RECONCILIATION_HUMAN_RESOLVED,
        )
        assert rs.RECONCILIATION_STATES == (
            "UNCITED", "DIRECT", "DERIVED", "CONFLICTED", "HUMAN_RESOLVED",
        )

    def test_the_three_shared_names_are_pinned_equal_to_j66s_standings(self):
        """The model re-declares J66's three state names rather than editing J66 — so the
        pair must agree, and this is what stops them drifting apart unnoticed."""
        assert rs.RECONCILIATION_UNCITED == STANDING_UNCITED
        assert rs.RECONCILIATION_DIRECT == STANDING_DIRECT
        assert rs.RECONCILIATION_DERIVED == STANDING_DERIVED

    def test_the_evidence_kinds_are_pinned_equal_to_j66s(self):
        """The model COPIES J66's two evidence kinds rather than importing them, so that no
        fourth file in `app/` names J66's vocabulary and J66's own tree-wide fence stays
        exact. A copy is only safe if it cannot drift: this is the pin. See the module
        docstring, and `test_the_isolation_is_what_keeps_the_j66_fence_exact` below for the
        reason the copy exists at all."""
        assert rs.EVIDENCE_KIND_SOURCE == CITATION_SOURCE
        assert rs.EVIDENCE_KIND_DERIVATION == CITATION_DERIVATION
        assert rs.EVIDENCE_KINDS == CITATION_KINDS
        assert rs.EVIDENCE_KINDS == ("SOURCE", "DERIVATION")

    def test_the_isolation_is_what_keeps_the_j66_fence_exact(self):
        """
        J66 asserts that exactly three modules in `app/` name its vocabulary at all, which
        is how J66 makes 'no production writer exists' a checkable fact. J68 must not break
        that fence and must not edit J66 to widen it — so this model names none of it: not
        the module's constants, not the storage table, not the word. This test reads the
        model's own source, case-insensitively, which is exactly how the J66 fence reads it.
        """
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        assert "citation" not in source
        for name in ("CITATION_SOURCE", "CITATION_DERIVATION", "CITATION_KINDS",
                     "connection_review_item_citations", "persist_connection_review_citations"):
            assert name.lower() not in source, name
        # And the model does not import J66's vocabulary either — the copy is local.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert not alias.name.startswith("CITATION_"), alias.name

    def test_the_two_new_names_do_not_exist_in_j66(self):
        """J68 adds CONFLICTED and HUMAN_RESOLVED as its OWN vocabulary. J66 must not have
        grown them: the brief forbids changing it, and a reader of J66 must still find
        exactly three standings."""
        import app.cad_engine.review_contract as contract

        declared = {
            name for name in dir(contract)
            if name.startswith("STANDING_")
        }
        assert declared == {"STANDING_UNCITED", "STANDING_DIRECT", "STANDING_DERIVED"}
        for name in declared:
            assert getattr(contract, name) not in (
                rs.RECONCILIATION_CONFLICTED, rs.RECONCILIATION_HUMAN_RESOLVED,
            )

    def test_conflict_detection_is_declared_absent(self):
        assert rs.CONFLICT_DETECTION_IMPLEMENTED is False

    def test_the_reused_vocabularies_are_the_existing_ones(self):
        assert rs.HUMAN_PROVENANCE_LABELS == (
            PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED,
        )
        assert PROVENANCE_AI_EXTRACTED not in rs.HUMAN_PROVENANCE_LABELS
        for label in rs.HUMAN_PROVENANCE_LABELS:
            assert label in SUPPORTED_PROVENANCE_LABELS, label

    def test_the_non_resolving_answer_types_are_the_three_that_touch_no_field(self):
        assert set(rs.NON_RESOLVING_ANSWER_TYPES) == {
            ANSWER_APPROVE_REVIEW, ANSWER_ACKNOWLEDGMENT, ANSWER_AUTOMATION_CONFIRMATION,
        }
        for answer_type in rs.NON_RESOLVING_ANSWER_TYPES:
            assert answer_type not in rs.RESOLVING_ANSWER_TYPES
        assert ANSWER_FIELD_DECISION in rs.RESOLVING_ANSWER_TYPES

    def test_roles_are_j64s_stored_vocabulary_and_none_is_invented(self):
        from app.engineering_data import repository as store

        for role in rs.PROPOSING_ROLE_VOCABULARY:
            assert role in store.DOCUMENT_ROLES, role


# =============================================================================
# B. THE FIELD VOCABULARY AND THE AUTHORITY MATRIX (brief items A, B, O).
# =============================================================================
class TestTheFieldVocabulary:
    def test_a_every_known_specification_field_is_represented(self):
        assert rs.RECONCILIATION_FIELDS == J67_MATRIX_FIELDS
        # connection_id first, then J66's own engineering-field expression, unchanged.
        assert rs.RECONCILIATION_FIELDS == ("connection_id",) + ENGINEERING_FIELDS
        assert ENGINEERING_FIELDS == REQUIRED_PROVENANCE_FIELDS + ("material",)

    def test_a_the_matrix_has_exactly_one_row_per_field_in_field_order(self):
        assert isinstance(rs.FIELD_AUTHORITY, tuple)
        assert tuple(row.field for row in rs.FIELD_AUTHORITY) == rs.RECONCILIATION_FIELDS
        assert len(rs.FIELD_AUTHORITY) == len(set(rs.RECONCILIATION_FIELDS))

    def test_a_specification_metadata_is_not_modelled_as_an_engineering_field(self):
        """`source_page`, `source_drawing_id`, `review_status` and `provenance` live on the
        specification but are NOT fields a state is computed for — they describe the record,
        not its content."""
        for metadata in ("source_page", "source_drawing_id", "review_status", "provenance"):
            assert metadata not in rs.RECONCILIATION_FIELDS

    def test_b_an_unknown_field_is_refused_deterministically(self):
        for unknown in ("grid_reference", "detail_reference", "holes_mm", "", "HOLES"):
            with pytest.raises(ValueError) as excinfo:
                rs.field_authority(unknown)
            # Deterministic: the refusal names the fields, and repeating it says the same
            # thing. A default row would silently describe a field the architecture did not.
            assert list(rs.RECONCILIATION_FIELDS)[0] in str(excinfo.value)
            with pytest.raises(ValueError) as again:
                rs.field_authority(unknown)
            assert str(again.value) == str(excinfo.value)

    def test_b_an_unknown_field_has_no_state_computed_for_it(self):
        for unknown in ("grid_reference", None, 7):
            with pytest.raises(ValueError):
                rs.field_state(_evidence(unknown))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            rs.field_states([_evidence("grid_reference")])

    def test_o_the_matrix_is_immutable(self):
        assert dataclasses.is_dataclass(rs.FieldAuthority)
        assert rs.FieldAuthority.__dataclass_params__.frozen is True
        row = rs.field_authority("holes")
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.field = "plate"  # type: ignore[misc]
        with pytest.raises(TypeError):
            rs.FIELD_AUTHORITY[0] = rs.FIELD_AUTHORITY[1]  # type: ignore[index]

    def test_o_the_matrix_is_deterministic(self):
        assert rs.field_authority("holes") == rs.field_authority("holes")
        assert rs.field_authority("holes") is rs.field_authority("holes")
        for row in rs.FIELD_AUTHORITY:
            assert isinstance(row.proposing_roles, tuple)
            assert list(row.proposing_roles) == sorted(row.proposing_roles)
            assert row.completeness_rule and row.unresolved_condition
            assert row.human_ownership_required is True

    def test_o_the_matrix_carries_only_what_j67_established(self):
        expected = {
            "field", "proposing_roles", "multiple_sources_expected", "disagreement_possible",
            "human_ownership_required", "completeness_rule", "unresolved_condition",
        }
        assert {f.name for f in dataclasses.fields(rs.FieldAuthority)} == expected

    def test_the_transmittal_proposes_nothing(self):
        """J67 established that a transmittal can establish no connection field. The role is
        in the vocabulary and appears on no row — an asserted absence, not an oversight."""
        assert rs.ROLE_TRANSMITTAL in rs.PROPOSING_ROLE_VOCABULARY
        proposing = {role for row in rs.FIELD_AUTHORITY for role in row.proposing_roles}
        assert rs.ROLE_TRANSMITTAL not in proposing
        assert proposing == {
            rs.ROLE_ASSEMBLY, rs.ROLE_FABRICATION, rs.ROLE_ISOMETRIC, rs.ROLE_STRUCTURAL_GA,
        }

    def test_connection_id_is_the_only_field_no_role_can_propose(self):
        for row in rs.FIELD_AUTHORITY:
            if row.field == rs.CONNECTION_ID_FIELD:
                assert row.proposing_roles == ()
            else:
                assert row.proposing_roles, row.field


# =============================================================================
# C. THE STATE RULES (brief items C–L).
# =============================================================================
class TestTheStateRules:
    def test_c_no_recorded_evidence_is_uncited(self):
        for field in rs.RECONCILIATION_FIELDS:
            assert rs.field_state(_evidence(field)) == rs.RECONCILIATION_UNCITED, field
            assert rs.field_state(_evidence(field, evidence_kinds=())) == rs.RECONCILIATION_UNCITED

    def test_d_all_source_is_direct(self):
        for field in EVIDENCE_STATED_FIELDS:
            assert rs.field_state(
                _evidence(field, evidence_kinds=("SOURCE",))
            ) == rs.RECONCILIATION_DIRECT, field
        # connection_id is the one field the citation rule does NOT reach: no citation of it
        # can be a supporting address (see test_k).
        assert rs.field_state(
            _evidence(rs.CONNECTION_ID_FIELD, evidence_kinds=("SOURCE",))
        ) == rs.RECONCILIATION_UNCITED

    def test_e_any_derivation_is_derived(self):
        for field in EVIDENCE_STATED_FIELDS:
            assert rs.field_state(
                _evidence(field, evidence_kinds=("DERIVATION",))
            ) == rs.RECONCILIATION_DERIVED, field
        assert rs.field_state(
            _evidence(rs.CONNECTION_ID_FIELD, evidence_kinds=("DERIVATION",))
        ) == rs.RECONCILIATION_UNCITED

    def test_f_mixed_source_and_derivation_is_derived_not_direct(self):
        """The mixed field takes the WEAKER standing — a derivation can never be presented
        as though the content were taken directly from a reading."""
        for kinds in (
            ("SOURCE", "DERIVATION"),
            ("DERIVATION", "SOURCE"),
            ("SOURCE", "SOURCE", "DERIVATION"),
        ):
            assert rs.field_state(
                _evidence("holes", evidence_kinds=kinds)
            ) == rs.RECONCILIATION_DERIVED, kinds

    def test_f_an_unknown_evidence_kind_fails_closed_to_derived(self):
        """An unrecognised kind must never make a field look MORE directly sourced than it
        is, so it is treated as a derivation rather than skipped."""
        for kinds in (("SOMETHING_ELSE",), ("SOURCE", "SOMETHING_ELSE"), ("source",)):
            assert rs.field_state(
                _evidence("plate", evidence_kinds=kinds)
            ) == rs.RECONCILIATION_DERIVED, kinds

    def test_g_two_source_recordings_are_not_conflicted(self):
        assert rs.field_state(
            _evidence("holes", evidence_kinds=("SOURCE", "SOURCE"))
        ) == rs.RECONCILIATION_DIRECT

    def test_g_two_source_recordings_from_different_documents_are_not_conflicted(self):
        """
        J67 §4: two SOURCE recordings from two documents must NOT automatically become
        CONFLICTED. The model cannot see documents at all — `FieldReviewEvidence` carries no
        document, drawing, page, path, role or ordinal — so document origin cannot influence
        a state even in principle. The guarantee is structural, not incidental, and the two
        checks below are what make it structural.
        """
        kinds = ("SOURCE", "SOURCE")
        assert rs.field_state(_evidence("holes", evidence_kinds=kinds)) == rs.RECONCILIATION_DIRECT
        assert rs.field_state(_evidence("material", evidence_kinds=kinds)) == rs.RECONCILIATION_DIRECT

        evidence_fields = {f.name for f in dataclasses.fields(rs.FieldReviewEvidence)}
        assert evidence_fields == {
            "field", "evidence_kinds", "provenance", "resolved_answer_types",
            "reviewer_supplied",
        }
        for name in evidence_fields:
            assert not any(
                word in name for word in ("document", "drawing", "page", "path", "role", "ordinal")
            ), name

    def test_h_conflicted_is_representable_but_never_inferred(self):
        assert rs.RECONCILIATION_CONFLICTED in rs.RECONCILIATION_STATES
        assert rs.CONFLICT_DETECTION_IMPLEMENTED is False
        # Every reachable state, over every combination of the evidence this model accepts.
        for field in rs.RECONCILIATION_FIELDS:
            for kinds in ((), ("SOURCE",), ("DERIVATION",), ("SOURCE", "SOURCE"),
                          ("SOURCE", "DERIVATION")):
                for provenance in (None, PROVENANCE_AI_EXTRACTED, PROVENANCE_HUMAN_REVIEWED,
                                   PROVENANCE_HUMAN_SUPPLEMENTED):
                    for answers in ((), (ANSWER_FIELD_DECISION,), ("RESOLVING_OTHER",)):
                        for supplied in (False, True):
                            state = rs.field_state(_evidence(
                                field, evidence_kinds=kinds, provenance=provenance,
                                resolved_answer_types=answers, reviewer_supplied=supplied,
                            ))
                            assert state in rs.RECONCILIATION_STATES
                            assert state != rs.RECONCILIATION_CONFLICTED, (
                                field, kinds, provenance, answers, supplied,
                            )

    def test_i_human_provenance_with_a_resolved_answer_is_human_resolved(self):
        for provenance in (PROVENANCE_HUMAN_REVIEWED, PROVENANCE_HUMAN_SUPPLEMENTED):
            for answer in (ANSWER_FIELD_DECISION, ANSWER_CONNECTION_IDENTITY):
                assert rs.field_state(_evidence(
                    "plate", provenance=provenance, resolved_answer_types=(answer,),
                )) == rs.RECONCILIATION_HUMAN_RESOLVED, (provenance, answer)

    def test_i_a_human_resolution_outranks_whatever_the_recorded_evidence_said(self):
        """A ruling is the LATER fact, so it wins over the standing the evidence supports."""
        for kinds in ((), ("SOURCE",), ("DERIVATION",), ("SOURCE", "SOURCE")):
            assert rs.field_state(_evidence(
                "holes", evidence_kinds=kinds,
                provenance=PROVENANCE_HUMAN_SUPPLEMENTED,
                resolved_answer_types=(ANSWER_FIELD_DECISION,),
            )) == rs.RECONCILIATION_HUMAN_RESOLVED, kinds

    def test_i_both_halves_are_required(self):
        """Provenance alone, and a resolved answer alone, each leave the field NOT resolved.
        J67's rule is 'human provenance AND resolved review state', and it fails closed."""
        assert rs.field_state(_evidence(
            "plate", provenance=PROVENANCE_HUMAN_REVIEWED,
        )) != rs.RECONCILIATION_HUMAN_RESOLVED
        assert rs.field_state(_evidence(
            "plate", resolved_answer_types=(ANSWER_FIELD_DECISION,),
        )) != rs.RECONCILIATION_HUMAN_RESOLVED

    def test_j_ai_extracted_never_produces_human_resolved(self):
        for answers in ((), (ANSWER_FIELD_DECISION,), (ANSWER_CONNECTION_IDENTITY,)):
            for supplied in (False, True):
                state = rs.field_state(_evidence(
                    "plate", provenance=PROVENANCE_AI_EXTRACTED,
                    resolved_answer_types=answers, reviewer_supplied=supplied,
                ))
                assert state != rs.RECONCILIATION_HUMAN_RESOLVED, (answers, supplied)
        assert rs.has_human_provenance(PROVENANCE_AI_EXTRACTED) is False
        assert rs.has_human_provenance(None) is False
        assert rs.has_human_provenance("ANYTHING_ELSE") is False

    def test_j_an_ai_only_field_with_source_citations_is_direct_not_resolved(self):
        assert rs.field_state(_evidence(
            "holes", evidence_kinds=("SOURCE", "SOURCE"),
            provenance=PROVENANCE_AI_EXTRACTED,
        )) == rs.RECONCILIATION_DIRECT

    def test_k_connection_id_requires_human_ownership(self):
        row = rs.field_authority(rs.CONNECTION_ID_FIELD)
        assert row.proposing_roles == ()
        assert row.human_ownership_required is True
        assert row.disagreement_possible is False
        assert "never AI output" in row.unresolved_condition

        # No evidence can support it: not a citation of either kind, not a resolved answer,
        # not a human provenance label on a value nobody can label.
        for kwargs in (
            {"evidence_kinds": ("SOURCE",)},
            {"evidence_kinds": ("SOURCE", "SOURCE")},
            {"evidence_kinds": ("DERIVATION",)},
            {"resolved_answer_types": (ANSWER_CONNECTION_IDENTITY,)},
            {"provenance": PROVENANCE_HUMAN_REVIEWED},
        ):
            state = rs.field_state(_evidence(rs.CONNECTION_ID_FIELD, **kwargs))
            assert state == rs.RECONCILIATION_UNCITED, kwargs

        # The documented special case: identity carries NO provenance label (7W's own
        # contract), so the reviewer-supplied value itself is the signal.
        assert rs.field_state(_evidence(
            rs.CONNECTION_ID_FIELD, reviewer_supplied=True,
        )) == rs.RECONCILIATION_HUMAN_RESOLVED

    def test_k_a_blank_identity_is_not_an_identity(self):
        for blank in ("", "   ", "\t\n", None, 7, ["S1"]):
            assert rs.connection_identity_is_supplied(blank) is False, blank
        for good in ("C1", "  C1  ", "RP-0001"):
            assert rs.connection_identity_is_supplied(good) is True, good

    def test_l_holes_with_nominal_strings_are_not_numeric_diameters(self):
        for nominal in ("M20", "22mm holes", "18mm holes", "M20 bolts", "22", "Ø22"):
            assert rs.holes_diameter_is_explicit_numeric({"diameter_mm": nominal}) is False, nominal
            assert rs.holes_diameter_is_explicit_numeric(nominal) is False, nominal

    def test_l_no_hole_diameter_parsing_exists(self):
        """'18mm holes' must not become 18, and '22mm holes' must not become 22 — there is no
        parsing anywhere in the model, so no nominal string can produce a diameter."""
        assert rs.holes_diameter_is_explicit_numeric({"diameter_mm": "18mm holes"}) is False
        source = MODULE_PATH.read_text(encoding="utf-8")
        for parser in ("re.findall", "re.search", "re.match", "float(", "int(", "Decimal", "split("):
            assert parser not in source, parser

    def test_l_an_explicit_numeric_diameter_is_accepted(self):
        for good in ({"diameter_mm": 22}, {"diameter_mm": 18}, {"diameter_mm": 22.0},
                     {"diameter_mm": 0}, {"quantity": 4, "diameter_mm": 22}):
            assert rs.holes_diameter_is_explicit_numeric(good) is True, good

    def test_l_a_non_diameter_is_refused(self):
        for bad in (
            {},
            {"quantity": 4},
            {"diameter_mm": None},
            {"diameter_mm": True},          # a bool is not a diameter
            {"diameter_mm": False},
            {"diameter_mm": float("nan")},  # neither is a non-finite float
            {"diameter_mm": float("inf")},
            {"diameter_mm": [22]},
            [22],
        ):
            assert rs.holes_diameter_is_explicit_numeric(bad) is False, bad

    def test_l_holes_are_complete_only_with_a_numeric_diameter_and_a_human(self):
        assert rs.holes_are_complete({"diameter_mm": 22}, provenance=PROVENANCE_HUMAN_REVIEWED)
        assert rs.holes_are_complete({"diameter_mm": 22}, provenance=PROVENANCE_HUMAN_SUPPLEMENTED)
        # Nominal page text, however a human labelled it, is still not a diameter.
        assert not rs.holes_are_complete(
            {"diameter_mm": "22mm holes"}, provenance=PROVENANCE_HUMAN_REVIEWED,
        )
        # A numeric string is refused too: accepting it would mean the model had interpreted.
        assert not rs.holes_are_complete(
            {"diameter_mm": "22"}, provenance=PROVENANCE_HUMAN_REVIEWED,
        )
        # And a diameter nobody owns is not complete either.
        assert not rs.holes_are_complete({"diameter_mm": 22}, provenance=PROVENANCE_AI_EXTRACTED)
        assert not rs.holes_are_complete({"diameter_mm": 22}, provenance=None)


# =============================================================================
# D. THE ROLE AND DOCUMENT BOUNDARIES (brief items M, N).
# =============================================================================
class TestRolesNeverDecide:
    def test_m_no_function_in_the_model_consumes_a_role(self):
        """The structural proof that a role cannot be a winner: no callable in the module
        takes one, so no state can vary with a role."""
        for name, value in vars(rs).items():
            if name.startswith("_") or not callable(value):
                continue
            if isinstance(value, type) or dataclasses.is_dataclass(value):
                continue
            import inspect

            parameters = inspect.signature(value).parameters
            for parameter in parameters:
                assert "role" not in parameter.lower(), (name, parameter)
                assert "document" not in parameter.lower(), (name, parameter)
                assert "drawing" not in parameter.lower(), (name, parameter)

    def test_m_the_proposing_roles_order_carries_no_authority(self):
        """`proposing_roles` answers 'who MAY propose', never 'whose proposal wins'. The
        tuple is sorted, so it encodes no ranking, and the model's rules never read it."""
        for row in rs.FIELD_AUTHORITY:
            assert list(row.proposing_roles) == sorted(row.proposing_roles)
        source = MODULE_PATH.read_text(encoding="utf-8")
        body = source.split("def _evidence_standing", 1)[1]
        assert "proposing_roles" not in body
        assert "ROLE_" not in body

    def test_m_role_multiplicity_creates_only_the_POSSIBILITY_of_disagreement(self):
        """Wherever more than one role may propose a field, disagreement is POSSIBLE; that is
        the whole of what the roles imply. Nowhere does multiplicity imply a resolution."""
        for row in rs.FIELD_AUTHORITY:
            assert row.disagreement_possible == bool(row.proposing_roles), row.field

    def test_m_a_field_with_two_proposing_roles_still_takes_the_weaker_standing(self):
        """plate is proposed by ASSEMBLY and FABRICATION; a DERIVATION recording of it is
        still DERIVED. Whichever role a reading came from, it can never strengthen the
        standing."""
        assert rs.field_authority("plate").proposing_roles == (rs.ROLE_ASSEMBLY, rs.ROLE_FABRICATION)
        assert rs.field_state(
            _evidence("plate", evidence_kinds=("SOURCE", "DERIVATION"))
        ) == rs.RECONCILIATION_DERIVED

    def test_n_no_identifier_in_the_model_names_a_document_or_a_selector(self):
        """A role must never select a document. The model names no document, drawing, path,
        filename or page in any IDENTIFIER — the words appear only in prose."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute, ast.arg)):
                identifiers.add(node.id if isinstance(node, ast.Name) else node.attr
                                if isinstance(node, ast.Attribute) else node.arg)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.add(node.name)
        for name in sorted(identifiers):
            lowered = name.lower()
            for forbidden in ("document", "drawing", "filename", "path", "page",
                              "winner", "rank", "precedence"):
                assert forbidden not in lowered, (name, forbidden)
        # `ANSWER_MEMBER_SELECTION` is an EXISTING 7AC vocabulary name (a tuple of member
        # marks), not a selector — so "select" alone is not forbidden. What must not exist is
        # anything that selects BY a document or a role, which is what this pins.
        for name in sorted(identifiers):
            lowered = name.lower()
            if "select" in lowered:
                assert not any(
                    word in lowered for word in ("document", "drawing", "role", "by_")
                ), name

    def test_n_the_role_vocabulary_is_data_that_no_rule_reads(self):
        """Every ROLE_ constant is referenced only inside the FIELD_AUTHORITY literal and the
        vocabulary tuple — never by a function that produces a state."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        rules = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("_")
            and node.name not in ("resolved_answer_types",)
        }
        for name, node in rules.items():
            used = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
            assert not {u for u in used if u.startswith("ROLE_")}, name


# =============================================================================
# E. THE EXISTING BEHAVIOUR THIS MODEL MUST NOT DISTURB.
# =============================================================================
class TestExistingBehaviourIsUntouched:
    def test_j66s_standing_rule_is_unchanged(self):
        """J68 adds a vocabulary; it edits nothing. J66's rule still says what it said."""

        class _Citation:
            def __init__(self, field_name, citation_kind):
                self.field_name = field_name
                self.citation_kind = citation_kind

        assert [s.standing for s in field_standings([])] == [
            STANDING_UNCITED for _ in ENGINEERING_FIELDS
        ]
        assert field_standings(
            [_Citation("plate", "SOURCE")]
        )[ENGINEERING_FIELDS.index("plate")].standing == STANDING_DIRECT
        assert field_standings(
            [_Citation("plate", "SOURCE"), _Citation("plate", "DERIVATION")]
        )[ENGINEERING_FIELDS.index("plate")].standing == STANDING_DERIVED

    def test_j66_still_refuses_a_citation_of_a_field_outside_its_vocabulary(self):
        class _Citation:
            field_name = "grid_reference"
            citation_kind = "SOURCE"

        with pytest.raises(ValueError):
            field_standings([_Citation()])

    def test_the_model_adds_no_review_revision_and_touches_no_store(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("review_revision", "connection_review_items", "persist_",
                          "record_connection_review", "supabase", "storage"):
            assert forbidden not in source, forbidden


# =============================================================================
# F. PURITY (brief item P).
# =============================================================================
class TestPurity:
    def test_p_the_model_imports_nothing_outside_the_pure_engineering_layer(self):
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
            assert module.startswith("app.cad_engine.") or root in allowed_stdlib, module

    def test_p_the_model_performs_no_io_and_opens_nothing(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                assert name not in ("open", "connect", "execute", "rpc", "table", "print",
                                    "getenv", "environ"), name

    def test_p_a_fresh_interpreter_importing_only_the_model_pulls_no_io_library(self):
        """The strongest form: a clean interpreter imports the module and nothing else, and
        no database, HTTP or storage client is in `sys.modules` afterwards."""
        probe = (
            "import sys\n"
            "import app.cad_engine.reconciliation_state as rs\n"
            "forbidden = ('supabase', 'httpx', 'requests', 'urllib3', 'postgrest',"
            " 'storage3', 'gotrue', 'psycopg', 'boto3', 'sqlalchemy')\n"
            "bad = sorted(m for m in sys.modules if any(k in m for k in forbidden))\n"
            "print(repr((rs.RECONCILIATION_STATES, rs.RECONCILIATION_FIELDS, bad)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, result.stderr
        states, fields, bad = eval(result.stdout.strip())  # noqa: S307 - our own probe output
        assert states == rs.RECONCILIATION_STATES
        assert tuple(fields) == rs.RECONCILIATION_FIELDS
        assert bad == [], bad

    def test_p_the_model_holds_no_value_belonging_to_a_connection(self):
        """The only value-shaped inputs are the two narrow predicates' arguments. The
        evidence model carries kinds, labels and flags — never a reading."""
        for field in dataclasses.fields(rs.FieldReviewEvidence):
            assert field.type in (
                "str", "tuple[str, ...]", "str | None", "bool",
            ), field.name

    def test_p_the_model_is_deterministic_across_repeated_calls(self):
        evidence = [_evidence("holes", evidence_kinds=("SOURCE", "SOURCE")), _evidence("plate")]
        first = rs.field_states(evidence)
        for _ in range(5):
            assert rs.field_states(evidence) == first

    def test_field_states_states_every_field_and_refuses_a_duplicate(self):
        states = rs.field_states([_evidence("holes", evidence_kinds=("SOURCE",))])
        assert tuple(s.field for s in states) == rs.RECONCILIATION_FIELDS
        by_field = {s.field: s.state for s in states}
        assert by_field["holes"] == rs.RECONCILIATION_DIRECT
        assert by_field["plate"] == rs.RECONCILIATION_UNCITED
        assert by_field["connection_id"] == rs.RECONCILIATION_UNCITED
        with pytest.raises(ValueError):
            rs.field_states([_evidence("holes"), _evidence("holes")])

    def test_the_resolved_answer_reader_uses_the_existing_record(self):
        """An OPEN task's question is not an answer, and a non-resolving answer type is not a
        resolution of a field — both read from the existing task shape, not restated."""
        open_task = ExceptionResolutionTask(
            task_id="T01", task_type="PROVIDE_PLATE", blocker_codes=(), question="q",
            current_ai_value=None, answer_type=ANSWER_FIELD_DECISION, allowed_choices=(),
            evidence_requirement="e", status=STATUS_OPEN,
        )
        assert rs.resolved_answer_types([open_task]) == ()
        resolved = _resolved("T02", "PROVIDE_PLATE", ANSWER_FIELD_DECISION, ("plate", "supply", {}))
        assert rs.resolved_answer_types([resolved]) == (ANSWER_FIELD_DECISION,)
        assert rs.resolved_answer_types([open_task, resolved]) == (ANSWER_FIELD_DECISION,)
        assert rs.resolved_answer_types([]) == ()

    def test_the_ai_bolt_model_is_not_a_hole_diameter(self):
        """The existing model this must not disturb: an AIExtractedBolt's `size` is a NOMINAL
        designation, and the state model never reads it."""
        bolt = AIExtractedBolt(quantity=4, size="M20")
        assert bolt.size == "M20"
        assert rs.field_state(_evidence("holes")) == rs.RECONCILIATION_UNCITED
        assert not rs.holes_are_complete({"diameter_mm": bolt.size}, provenance=PROVENANCE_HUMAN_REVIEWED)
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "AIExtractedBolt" not in source


# =============================================================================
# G. THE MUTATIONS — every property above stands on something that can fail.
# =============================================================================
def _mutant(old: str, new: str, *, name: str = "j68_mutant") -> types.ModuleType:
    """The model with one exact edit applied, executed in isolation.

    Source-level, so the mutation removes the property being asserted rather than faking a
    behaviour, and isolated, so the real module is untouched.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(MODULE_PATH)
    # The module uses `from __future__ import annotations`, so `@dataclass` resolves its
    # field annotations through `sys.modules[cls.__module__]` at class-creation time. The
    # mutant must therefore be registered in sys.modules while it executes, or every
    # frozen dataclass in it raises on import — which would make the mutation prove nothing
    # about the rule it removes. It is removed again straight after, so no mutant leaks.
    sys.modules[name] = module
    try:
        exec(compile(source.replace(old, new), "<j68-mutant>", "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


_CONFLICT_SEAM = '''    not a guard, and "J68 does not detect conflicts" must be provable rather than asserted.
    """
    return False
'''

_DERIVED_RULE = """    if any(kind != EVIDENCE_KIND_SOURCE for kind in kinds):
        return RECONCILIATION_DERIVED
"""

_HUMAN_RULE = """    return provenance in HUMAN_PROVENANCE_LABELS
"""

_DIAMETER_RULE = """    if not isinstance(diameter, (int, float)):
        return False
    return math.isfinite(diameter)
"""

_UNKNOWN_FIELD_RULE = """    if not is_reconciliation_field(evidence.field):
        raise ValueError(
            f"{evidence.field!r} is not a reconciliation field "
"""

_IDENTITY_SPECIAL_CASE = """    if evidence.field == CONNECTION_ID_FIELD:
        return bool(evidence.reviewer_supplied)
"""


class TestMutations:
    def test_mutation_1_removing_the_conflict_seam_makes_two_sources_conflicted(self):
        """The seam is what stops J68 inferring a conflict. Remove it and section C's G
        assertions go red — which is what makes 'J68 detects no conflict' a guarantee."""
        mutant = _mutant(_CONFLICT_SEAM, '''    not a guard, and "J68 does not detect conflicts" must be provable rather than asserted.
    """
    return len(evidence.evidence_kinds) > 1
''')
        assert mutant.CONFLICT_DETECTION_IMPLEMENTED is False  # the flag is a separate fact
        two_sources = mutant.FieldReviewEvidence(field="holes", evidence_kinds=("SOURCE", "SOURCE"))
        assert mutant.field_state(two_sources) == "CONFLICTED"
        assert rs.field_state(rs.FieldReviewEvidence(
            field="holes", evidence_kinds=("SOURCE", "SOURCE"),
        )) == rs.RECONCILIATION_DIRECT
        # The real module is untouched by that mutation.
        assert rs.field_state(_evidence("holes", evidence_kinds=("SOURCE", "SOURCE"))) != "CONFLICTED"

    def test_mutation_2_weakening_the_derived_rule_makes_a_mixed_field_direct(self):
        mutant = _mutant(_DERIVED_RULE, """    if all(kind != EVIDENCE_KIND_SOURCE for kind in kinds):
        return RECONCILIATION_DERIVED
""")
        mixed = mutant.FieldReviewEvidence(field="holes", evidence_kinds=("SOURCE", "DERIVATION"))
        assert mutant.field_state(mixed) == "DIRECT"  # the weaker standing was lost
        assert rs.field_state(_evidence("holes", evidence_kinds=("SOURCE", "DERIVATION"))) \
            == rs.RECONCILIATION_DERIVED

    def test_mutation_2b_weakening_the_derived_rule_lets_an_unknown_kind_look_direct(self):
        mutant = _mutant(_DERIVED_RULE, """    if any(kind == "DERIVATION" for kind in kinds):
        return RECONCILIATION_DERIVED
""")
        unknown = mutant.FieldReviewEvidence(field="plate", evidence_kinds=("SOMETHING_ELSE",))
        assert mutant.field_state(unknown) == "DIRECT"
        assert rs.field_state(_evidence("plate", evidence_kinds=("SOMETHING_ELSE",))) \
            == rs.RECONCILIATION_DERIVED

    def test_mutation_3_loosening_human_provenance_lets_ai_look_resolved(self):
        mutant = _mutant(_HUMAN_RULE, "    return True\n")
        ai = mutant.FieldReviewEvidence(
            field="plate", provenance="AI_EXTRACTED",
            resolved_answer_types=("FIELD_DECISION",),
        )
        assert mutant.field_state(ai) == "HUMAN_RESOLVED"  # an AI value claimed as a ruling
        assert rs.field_state(_evidence(
            "plate", provenance=PROVENANCE_AI_EXTRACTED,
            resolved_answer_types=(ANSWER_FIELD_DECISION,),
        )) != rs.RECONCILIATION_HUMAN_RESOLVED

    def test_mutation_4_loosening_the_diameter_rule_accepts_nominal_page_text(self):
        mutant = _mutant(_DIAMETER_RULE, "    return True\n")
        assert mutant.holes_diameter_is_explicit_numeric({"diameter_mm": "22mm holes"}) is True
        assert mutant.holes_diameter_is_explicit_numeric({"diameter_mm": "M20"}) is True
        assert rs.holes_diameter_is_explicit_numeric({"diameter_mm": "22mm holes"}) is False

    def test_mutation_5_removing_the_field_guard_computes_a_state_for_an_unknown_field(self):
        mutant = _mutant(_UNKNOWN_FIELD_RULE, "    if False:\n        raise ValueError(\n"
                                              '            f"{evidence.field!r} is not a reconciliation field "\n')
        assert mutant.field_state(mutant.FieldReviewEvidence(field="grid_reference")) == "UNCITED"
        with pytest.raises(ValueError):
            rs.field_state(_evidence("grid_reference"))

    def test_mutation_6_removing_the_identity_special_case_strands_every_identity(self):
        """`connection_id` carries no provenance label, so without the documented special
        case a supplied identity could never be reported as resolved."""
        mutant = _mutant(_IDENTITY_SPECIAL_CASE, "    if False:\n        return False\n")
        supplied = mutant.FieldReviewEvidence(field="connection_id", reviewer_supplied=True)
        assert mutant.field_state(supplied) == "UNCITED"
        assert rs.field_state(_evidence(
            rs.CONNECTION_ID_FIELD, reviewer_supplied=True,
        )) == rs.RECONCILIATION_HUMAN_RESOLVED

    def test_the_mutations_are_source_level_and_the_real_module_is_untouched(self):
        """Every mutant above was compiled from a COPY of the source. The file on disk still
        contains every anchor exactly once, unmutated."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for anchor in (_CONFLICT_SEAM, _DERIVED_RULE, _HUMAN_RULE, _DIAMETER_RULE,
                       _UNKNOWN_FIELD_RULE, _IDENTITY_SPECIAL_CASE):
            assert source.count(anchor) == 1
        assert rs.field_state(
            _evidence("holes", evidence_kinds=("SOURCE", "SOURCE"))
        ) == rs.RECONCILIATION_DIRECT
