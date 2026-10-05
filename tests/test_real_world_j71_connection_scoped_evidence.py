"""
J71 — CONNECTION-SCOPED EVIDENCE RESOLUTION: the proof file.

WHAT THIS FILE IS

The proof for `app/cad_engine/connection_scoped_evidence.py` — the pure resolver that turns
a recorded evidence address into ONE raw connection candidate belonging to the review item
that recorded it, or refuses by name.

The module is exercised against the GENUINE objects it was designed for: the real
`ReviewFieldCitation` value object for the address, the real `ReviewSnapshotItem` for the
item, and the committed Selby page readings under `tests/data/` for the evidence. Nothing
here invents a parallel representation of any of them.

WHAT IS REAL, AND WHAT IS NOT

Real: every address term, every reason code, every rule, and the Selby readings' own numbers
(32 distinct pages over one document read in windows, one of which — page 26 — was read
twice). Not real: the `drawing_id` and `analysis_run_id` on the Selby rows are supplied by
the helpers below. They are PROMOTED COLUMNS of a recorded reading, not payload keys — a
payload file carries neither — so a test that needs a recorded reading has to state them.
One document read in windows is one `drawing_id` with one `analysis_run_id` per attempt,
which is exactly what those columns mean, and the resolver treats both as opaque names
either way.

WHAT THE MUTATIONS ARE

Five controlled defects, each registered in `sys.modules` before it executes (the module
uses `from __future__ import annotations`, so a frozen dataclass resolves its annotations
through `sys.modules[cls.__module__]` at class-creation time) and each shown to redden the
tests that pin the property it removes. They are the evidence that those tests are
load-bearing rather than decorative.

WHAT THIS FILE DOES NOT CLAIM

It does not claim the resolver is wired into anything. It is not: no module under `app/`
imports it, and a test says so. It does not claim the candidate's array position is an
identity: R3 (J74) reads a position where the review ITEM itself recorded one, and returns
what that position holds in the cited reading — the position stays an observation about a
reading, and never becomes a name for a connection.
And it resolves no engineering disagreement: the Selby Ø18/Ø22 question and the
Transmittal-versus-drawing-set coverage question are untouched by everything below, because
the resolver answers "which reading", never "which is right".
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import re
import subprocess
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from app.cad_engine import connection_scoped_evidence as cse
from app.cad_engine.connection_review_snapshot import ReviewFieldCitation, ReviewSnapshotItem

#: Assembled rather than written out, so that a scan over this file cannot find this file's
#: own list of the words it is looking for.
_IO_TOKENS = ("op" "en(", "print(", "environ", "getenv", "requests", "httpx", "supabase",
              "postgrest", "storage3", "anthropic", "claude", ".table(", ".rpc(",
              "insert into", "create table", "put_object", "upload(", "execute", " sql")
REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "app"
MODULE_PATH = APP / "cad_engine" / "connection_scoped_evidence.py"
SNAPSHOT_MODULE = APP / "cad_engine" / "connection_review_snapshot.py"
PACKAGE_MODULE = APP / "cad_engine" / "connection_review_package.py"
VISION_MODULE = APP / "ai_analysis" / "pdf_vision_analyzer.py"
DATA = REPO / "tests" / "data"


# =============================================================================
# Builders. The address is the REAL value object; the item is a stand-in for the
# one attribute this resolver reads, with the genuine object proved separately.
# =============================================================================
def _address(**overrides: Any) -> ReviewFieldCitation:
    """A recorded evidence address in the product's own shape."""
    fields: dict[str, Any] = {
        "project_id": "2389c115-664f-4fe4-8b76-ca07aac3719d",
        "review_revision": 0,
        "review_package_id": "RP-0004",
        "field_name": "material",
        "ordinal": 1,
        "citation_kind": "SOURCE",
        "document_id": "doc-fab",
        "drawing_id": "drawing-selby",
        "page_number": 2,
        "analysis_run_id": "run-a",
        "annotation_x": None,
        "annotation_y": None,
        "extractor_version": None,
        "anchor": ("detail-24",),
    }
    fields.update(overrides)
    return ReviewFieldCitation(**fields)


@dataclass(frozen=True)
class _Item:
    """A review item, in the only shape this resolver reads: its recorded evidence."""
    evidence: dict[str, Any]
    review_package_id: Any = "RP-0004"


def _item(page: Any = None, detail: Any = None, grid: Any = None, **extra: Any) -> _Item:
    evidence: dict[str, Any] = {
        cse.PROVENANCE_PAGE_KEY: page,
        cse.PROVENANCE_DETAIL_KEY: detail,
        cse.PROVENANCE_GRID_KEY: grid,
        "source_drawing_id": "drawing-selby",
        "drawing_number": "002",
    }
    evidence.update(extra)
    return _Item(evidence=evidence)


def _snapshot_item(page: Any = 2, detail: Any = "VIEW B-B", grid: Any = None):
    """The product's OWN review item, built through its real constructor."""
    return ReviewSnapshotItem(
        review_package_id="RP-0004", connection_id="C1", decision="REVIEW",
        output_status=None, verification_status=None, last_processed_revision=None,
        blocker_codes=(), warning_codes=(),
        ai_readings={"material": "300"},
        evidence={
            "source_drawing_id": "drawing-selby", "drawing_number": "002",
            cse.PROVENANCE_PAGE_KEY: page, cse.PROVENANCE_DETAIL_KEY: detail,
            cse.PROVENANCE_GRID_KEY: grid,
        },
        provenance={"material": "AI_EXTRACTED"}, tasks=(), generated_files=(),
    )


def _reading(drawing_id: str, page_number: int, run_id: str, candidates: list[Any],
             **extra: Any):
    """A recorded page reading in the storage layer's own column shape."""
    row = {
        "drawing_id": drawing_id,
        "drawing_set_id": "ds-selby",
        "project_id": "2389c115-664f-4fe4-8b76-ca07aac3719d",
        "page_number": page_number,
        "analysis_run_id": run_id,
        "model": "pdf-vision",
        "parse_failed": False,
        "payload": {
            "page_number": page_number,
            "drawing_number": f"{page_number:03d}",
            "revision": "A",
            "raw_members": [],
            "raw_connections": candidates,
            "parse_failed": False,
        },
    }
    row.update(extra)
    return row


def _candidate(detail: Any = None, grid: Any = None, members: Any = None,
               material: Any = None):
    return {
        "detail_reference": detail,
        "grid_reference": grid,
        "connects_members": ["002"] if members is None else members,
        "connection_type": "bolted",
        "welds": [],
        "material": material,
        "confidence": 88,
    }


def _two() -> list[Any]:
    return [_candidate(detail="VIEW A-A"), _candidate(detail="VIEW B-B")]


def _decision(outcome: Any) -> dict[str, Any]:
    """What a resolution DECIDED, with the address terms it merely carries removed.

    The address is proved separately (`test_the_address_is_carried_unchanged`); comparing
    it again here would only restate that it is carried, and would hide the thing these
    tests are about — that a term the resolver never consults makes no difference to the
    answer.
    """
    record = dataclasses.asdict(outcome)
    for carried in ("field_name", "review_package_id", "document_id", "annotation_x",
                    "annotation_y", "extractor_version", "anchor"):
        record.pop(carried, None)
    return record


# =============================================================================
# The committed Selby readings, read-only.
# =============================================================================
WINDOW_1_5 = "selby_square_page_extractions.json"
WINDOW_6_10 = "selby_square_pages_6_10_extractions.json"
WINDOW_11_15 = "selby_square_pages_11_15_extractions.json"
WINDOW_16_20 = "selby_square_pages_16_20_extractions.json"
WINDOW_21_25 = "selby_square_pages_21_25_extractions.json"
WINDOW_26_30 = "selby_square_pages_26_30_extractions.json"
WINDOW_31_32 = "selby_square_pages_31_32_extractions.json"
RECOVERY_26 = "selby_square_page_26_recovery.json"

SELBY_FILES = (WINDOW_1_5, WINDOW_6_10, WINDOW_11_15, WINDOW_16_20, WINDOW_21_25,
               WINDOW_26_30, WINDOW_31_32, RECOVERY_26)

#: The one document those windows are of. A `drawings` row is one lineage over one
#: document, and a window is an ATTEMPT at it — so the windows share a drawing and differ
#: by run, which is what J23 records and what this suite relies on.
SELBY_DRAWING = "drawing-selby-view-bb"


def _run_of(name: str) -> str:
    return f"run-{name.removesuffix('.json')}"


def _selby_readings() -> list[dict[str, Any]]:
    """Every committed Selby reading, wrapped in the recorded-reading column shape."""
    rows: list[dict[str, Any]] = []
    for name in SELBY_FILES:
        pages = json.loads((DATA / name).read_text(encoding="utf-8"))
        for page in pages:
            rows.append(_reading(
                SELBY_DRAWING, page["page_number"], _run_of(name),
                page.get("raw_connections") or [],
            ))
    return rows


def _selby_page(run_name: str, page_number: int) -> dict[str, Any]:
    wanted = [row for row in _selby_readings()
              if row["drawing_id"] == SELBY_DRAWING
              and row["page_number"] == page_number
              and row["analysis_run_id"] == _run_of(run_name)]
    assert len(wanted) == 1, (run_name, page_number, len(wanted))
    return wanted[0]


def _candidates_on(run_name: str, page_number: int) -> list[dict[str, Any]]:
    return _selby_page(run_name, page_number)["payload"]["raw_connections"]


def _selby(run_name: str, page_number: int, **provenance: Any):
    """Resolve one page of the real fixture under the real address."""
    return cse.resolve_connection_field_reading(
        _item(**provenance),
        _address(drawing_id=SELBY_DRAWING, page_number=page_number,
                 analysis_run_id=_run_of(run_name)),
        [_selby_page(run_name, page_number)],
    )


# =============================================================================
# The mutation harness.
# =============================================================================
def _mutant(old: str, new: str, *, name: str = "j71_mutant") -> types.ModuleType:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(MODULE_PATH)
    sys.modules[name] = module
    try:
        exec(compile(source.replace(old, new), "<j71-mutant>", "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


#: R2's final refusal, removed: a page whose provenance matches nothing silently takes its
#: first candidate. This is the "fallback" the brief forbids.
_NO_AMBIGUITY = (
    "    if len(matched) == 1:\n"
    "        return _resolved(reference, item, matched[0], candidates[matched[0]],\n"
    "                         RESOLVED_PROVENANCE)\n",
    "    if len(matched) == 1:\n"
    "        return _resolved(reference, item, matched[0], candidates[matched[0]],\n"
    "                         RESOLVED_PROVENANCE)\n"
    "    if not matched:\n"
    "        return _resolved(reference, item, 0, candidates[0], RESOLVED_PROVENANCE)\n",
)

#: Exact equality replaced by string coercion, so `22` and `"22"` become the same value.
_COERCIVE_EQUALITY = (
    "    return left == right\n",
    "    return str(left) == str(right)\n",
)

#: "States nothing" made to state everything, so an item's absent value matches a
#: candidate's absent value — NULL matching NULL.
_NOTHING_IS_STATED = (
    "    if value is None:\n"
    "        return True\n"
    "    return isinstance(value, str) and not value.strip()\n",
    "    return False\n",
)

#: The cited attempt's reading replaced by any reading of the page when the cited one is
#: absent — the silent substitution the brief forbids.
_RUN_SUBSTITUTION = (
    "    if not cited:\n",
    "    if not cited:\n"
    "        cited = list(page_readings)\n"
    "    if len(cited) == 0:\n",
)

#: The snapshot of the candidate removed, so a returned reading aliases the caller's object.
_NO_SNAPSHOT = (
    '        object.__setattr__(self, "candidate", copy.deepcopy(self.candidate))\n',
    '        object.__setattr__(self, "candidate", self.candidate)\n',
)


# =============================================================================
# 1. The pinned vocabulary.
# =============================================================================
def _declared_annotations(path: pathlib.Path, class_name: str) -> set[str]:
    """The annotated field names of one class, read by syntax tree — never imported."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)}
    raise AssertionError(f"no class {class_name!r} in {path}")


class TestThePinnedVocabulary:
    def test_the_six_refusals_are_exactly_the_declared_six(self):
        # J74 grew the closed set from four to six: a recorded position that is not one, and
        # a recorded position the cited reading does not reach. The original four keep their
        # order and their names; the two additions are appended, never interleaved.
        assert cse.REASON_CODES == (
            "EVIDENCE_PAGE_ABSENT", "AMBIGUOUS_PAGE", "CANDIDATE_ABSENT", "RUN_ABSENT",
            "CANDIDATE_INDEX_INVALID", "CANDIDATE_INDEX_OUT_OF_RANGE",
        )

    def test_a_seventh_refusal_cannot_be_stated(self):
        for code in ("", "OTHER", "ambiguous_page", "AMBIGUOUS_PAGE ",
                     "CANDIDATE_INDEX_OUT_OF_RANGE ", "candidate_index_invalid"):
            with pytest.raises(ValueError):
                cse.Unresolved(reason_code=code, detail="x")

    def test_there_are_exactly_three_resolution_rules(self):
        assert cse.RESOLUTION_RULES == (
            "RESOLVED_SINGLE_CANDIDATE", "RESOLVED_PROVENANCE", "RESOLVED_CANDIDATE_INDEX",
        )

    def test_a_fourth_rule_cannot_be_stated(self):
        with pytest.raises(ValueError):
            cse.ConnectionScopedReading(
                field_name="material", review_package_id="RP-0004", drawing_id="d",
                page_number=2, analysis_run_id="r", document_id=None, annotation_x=None,
                annotation_y=None, extractor_version=None, anchor=(), candidate_position=0,
                matched_by="RESOLVED_BY_PROXIMITY", candidate=_candidate(),
            )

    def test_the_capture_key_names_are_the_storage_layers_own(self):
        """The four promoted-column names are quoted, not imported — so they are pinned
        here to the module that owns them, and a rename there reddens this rather than
        silently making every lookup miss."""
        from app.engineering_data import page_extraction_capture as capture

        for name in (cse.CAPTURE_DRAWING_KEY, cse.CAPTURE_PAGE_KEY, cse.CAPTURE_RUN_KEY,
                     cse.CAPTURE_PAYLOAD_KEY):
            assert name in capture.CAPTURE_COLUMNS, name

    def test_the_candidate_array_key_is_the_page_extractions_own_field(self):
        """`raw_connections` is read off a page reading's payload — a `PageExtraction`
        recorded as `dataclasses.asdict`. The copy is pinned to that dataclass's own field
        list by syntax tree, so the vision analyser is never imported here."""
        fields = _declared_annotations(VISION_MODULE, "PageExtraction")
        assert cse.CANDIDATE_ARRAY_KEY in fields, sorted(fields)

    def test_the_provenance_keys_are_the_recorded_ones(self):
        """`source_page`, `detail_reference` and `grid_reference` are the names the review
        layer records about a connection's own source: the first is the snapshot's own
        recorded evidence key, the other two are the AI candidate's own keys."""
        snapshot = SNAPSHOT_MODULE.read_text(encoding="utf-8")
        for name in cse.PROVENANCE_KEYS:
            assert f'"{name}"' in snapshot, name

        from app.cad_engine import connection_review_package as package

        source = PACKAGE_MODULE.read_text(encoding="utf-8")
        for name in (cse.PROVENANCE_DETAIL_KEY, cse.PROVENANCE_GRID_KEY):
            assert f"{name}: " in source, name
        assert package._KNOWN_CONNECTION_KEYS >= {cse.PROVENANCE_DETAIL_KEY,
                                                 cse.PROVENANCE_GRID_KEY}

    def test_the_address_terms_are_the_recorded_addresses_own_columns(self):
        """A resolved reading carries J69's `EvidenceAddress` names verbatim, so a caller
        can build that address from the result without translating between vocabularies."""
        recorded = {field.name for field in dataclasses.fields(ReviewFieldCitation)}
        assert set(cse.ADDRESS_TERMS) <= recorded
        # And the three required terms are exactly the three that identify a page reading.
        assert set(cse.REQUIRED_ADDRESS_TERMS) == {
            cse.CAPTURE_DRAWING_KEY, cse.CAPTURE_PAGE_KEY, cse.CAPTURE_RUN_KEY,
        }

    def test_the_module_states_no_engineering_field_vocabulary_at_all(self):
        """It cannot prefer one value over another if it never names a value. The eight
        specification fields, and every dispute about them, are outside its vocabulary."""
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        for field in ("connection_id", "connected_member_marks", "plate", "holes",
                      "location", "attachments", "material", "welds", "bolts"):
            assert field not in source, field


# =============================================================================
# 2. R0 — the address. (brief items 3, 4)
# =============================================================================
class TestTheAddressIsRequired:
    def test_a_missing_page_number_is_not_a_page(self):
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(page_number=None), [_reading("d", 2, "r", _two())])
        assert isinstance(outcome, cse.Unresolved)
        assert outcome.reason_code == cse.REASON_EVIDENCE_PAGE_ABSENT

    def test_a_missing_drawing_is_not_a_page(self):
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(drawing_id=None), [_reading("d", 2, "r", _two())])
        assert outcome.reason_code == cse.REASON_EVIDENCE_PAGE_ABSENT

    def test_a_missing_attempt_is_run_absent_not_a_page_failure(self):
        """brief item 4. A page and a drawing without an attempt is a different fact from a
        page that does not exist, and the two codes say so."""
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id=None), [_reading("d", 2, "r", _two())])
        assert outcome.reason_code == cse.REASON_RUN_ABSENT

    def test_a_blank_attempt_is_absent(self):
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="   "), [_reading("d", 2, "r", _two())])
        assert outcome.reason_code == cse.REASON_RUN_ABSENT

    def test_a_blank_page_is_absent(self):
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(page_number=""), [_reading("d", 2, "r", _two())])
        assert outcome.reason_code == cse.REASON_EVIDENCE_PAGE_ABSENT

    def test_the_page_refusal_is_decided_before_the_attempt_refusal(self):
        """With no page AND no attempt there is nothing to look for, so the page code is
        the one that is stated. The precedence is fixed rather than incidental."""
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(page_number=None, analysis_run_id=None), [])
        assert outcome.reason_code == cse.REASON_EVIDENCE_PAGE_ABSENT

    def test_no_readings_at_all_is_a_broken_caller_and_not_an_absent_page(self):
        """An empty set of readings is stated as an empty sequence. `None` is not an empty
        set, and treating it as one would make a missing argument look like absent
        evidence."""
        with pytest.raises(ValueError, match="no recorded readings were supplied"):
            cse.resolve_connection_field_reading(_item(), _address(), None)


# =============================================================================
# 3. R1 — the single-candidate page. (brief item 1; Selby item a)
# =============================================================================
class TestTheSingleCandidatePage:
    def test_one_candidate_resolves(self):
        only = _candidate(detail="VIEW A-A", material="300")
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(), [_reading("drawing-selby", 2, "run-a", [only])])
        assert isinstance(outcome, cse.ConnectionScopedReading)
        assert outcome.matched_by == cse.RESOLVED_SINGLE_CANDIDATE
        assert outcome.candidate_position == 0

    def test_the_candidate_is_carried_verbatim(self):
        only = _candidate(detail="VIEW A-A", material="300")
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(), [_reading("drawing-selby", 2, "run-a", [only])])
        assert dict(outcome.candidate) == only

    def test_the_address_is_carried_unchanged(self):
        address = _address(annotation_x="41.50", annotation_y="190.00",
                           extractor_version="j28.1", anchor=("detail-24",))
        outcome = cse.resolve_connection_field_reading(
            _item(), address, [_reading("drawing-selby", 2, "run-a", [_candidate()])])
        for name in cse.ADDRESS_TERMS:
            assert getattr(outcome, name) == getattr(address, name), name
        assert outcome.field_name == "material"
        assert outcome.review_package_id == "RP-0004"

    def test_the_reading_selected_is_the_cited_attempts_own(self):
        """Two readings of one page; the cited one is the one that answers."""
        first = _candidate(detail="ONE")
        second = _candidate(detail="TWO")
        rows = [_reading("drawing-selby", 2, "run-a", [first]),
                _reading("drawing-selby", 2, "run-b", [second])]
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="run-b"), rows)
        assert outcome.analysis_run_id == "run-b"
        assert dict(outcome.candidate) == second

    def test_one_candidate_resolves_even_when_the_recorded_provenance_contradicts_it(self):
        """R1 precedes R2 and the order is a rule, not an accident. With exactly one
        candidate there is nothing for provenance to choose between, so a contradicting
        recording cannot make the page unanswerable — and it cannot silently select a
        different candidate either, because there is no other candidate to select."""
        only = _candidate(detail="VIEW A-A")
        outcome = cse.resolve_connection_field_reading(
            _item(page=99, detail="SOMETHING ELSE"), _address(),
            [_reading("drawing-selby", 2, "run-a", [only])])
        assert outcome.matched_by == cse.RESOLVED_SINGLE_CANDIDATE
        assert dict(outcome.candidate) == only

    def test_a_page_that_states_no_candidate_at_all_is_candidate_absent(self):
        """brief item 2 — and the Selby fixture's page 12 is exactly this page."""
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(), [_reading("drawing-selby", 2, "run-a", [])])
        assert outcome.reason_code == cse.REASON_CANDIDATE_ABSENT

    def test_a_payload_that_omits_the_array_states_no_candidate_either(self):
        """Absent and empty are the same statement: the reading records no connection."""
        row = _reading("drawing-selby", 2, "run-a", [])
        del row["payload"]["raw_connections"]
        outcome = cse.resolve_connection_field_reading(_item(), _address(), [row])
        assert outcome.reason_code == cse.REASON_CANDIDATE_ABSENT

    def test_a_page_that_was_never_read_is_evidence_page_absent(self):
        """brief item 3."""
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(page_number=999),
            [_reading("drawing-selby", 2, "run-a", [_candidate()])])
        assert outcome.reason_code == cse.REASON_EVIDENCE_PAGE_ABSENT

    def test_a_different_drawing_is_a_different_page(self):
        """brief item 15 — the drawing is half the page's identity, and the page number
        alone is not a page (J23)."""
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(drawing_id="drawing-other"),
            [_reading("drawing-selby", 2, "run-a", [_candidate()])])
        assert outcome.reason_code == cse.REASON_EVIDENCE_PAGE_ABSENT


# =============================================================================
# 4. R2 — provenance uniqueness. (brief items 5–9)
# =============================================================================
class TestProvenanceUniqueness:
    def test_one_matching_candidate_resolves(self):
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW B-B"), _address(), rows)
        assert isinstance(outcome, cse.ConnectionScopedReading)
        assert outcome.matched_by == cse.RESOLVED_PROVENANCE
        assert outcome.candidate_position == 1
        assert outcome.candidate["detail_reference"] == "VIEW B-B"

    def test_no_match_at_all_is_ambiguous(self):
        """brief item 6 — the item's recorded provenance describes neither candidate."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW C-C"), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_several_matches_is_ambiguous(self):
        """brief item 7."""
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(grid="N/S"), _candidate(grid="N/S"), _candidate(grid="B/F")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, grid="N/S"), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_an_item_that_records_nothing_cannot_discriminate(self):
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(
            _item(page=None, detail=None, grid=None), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_null_never_matches_null(self):
        """brief item 8 — two candidates that both state nothing are not two candidates
        that state the same thing."""
        rows = [_reading("drawing-selby", 2, "run-a", [_candidate(), _candidate()])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=None, detail=None, grid=None), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_a_null_item_value_does_not_match_a_null_candidate_value(self):
        """The sharp form of the same rule. The item records only the page; one candidate
        states a grid and the other states none. Both match the page, so the answer is a
        refusal — under a rule that let NULL match NULL, the candidate whose grid is absent
        would have been singled out, which is an identity invented from an absence."""
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail=None, grid="A"), _candidate(detail=None, grid=None)])]
        outcome = cse.resolve_connection_field_reading(_item(page=2), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_a_candidate_that_states_nothing_never_matches_a_stated_value(self):
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail=None), _candidate(detail="VIEW B-B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW B-B"), _address(), rows)
        assert outcome.candidate_position == 1

    def test_a_blank_candidate_value_never_matches(self):
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="   "), _candidate(detail="VIEW B-B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="   "), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_a_blank_item_value_never_matches_either(self):
        """A blank is not a value, so it is dropped rather than compared — which means the
        candidate that also states nothing is NOT singled out, and the page stays
        unanswerable. The blank candidate cannot win by being blank."""
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="   "), _candidate(detail="VIEW B-B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail=""), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_no_coercion_between_a_number_and_its_digits(self):
        """Exact equality only. `22` and `"22"` are different recordings, and a rule that
        made them equal would be deciding engineering meaning."""
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="22"), _candidate(detail="B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail=22), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_no_case_folding(self):
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="view b-b"), _candidate(detail="VIEW B-B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="View B-B"), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_no_whitespace_collapsing(self):
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="VIEW  B-B"), _candidate(detail="VIEW B-B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW B-B"), _address(), rows)
        assert outcome.candidate_position == 1

    def test_the_items_recorded_page_must_agree_with_the_cited_page(self):
        """`source_page` is compared against the READING's page, because a candidate
        carries no page of its own. An item that records a different page shares no
        provenance with anything on this page, so nothing here is shown to be its
        evidence."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(
            _item(page=5, detail="VIEW B-B"), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_the_recorded_page_alone_does_not_pick_a_candidate(self):
        """A page-level value is shared by every candidate on the page, so it can never be
        the thing that discriminates between them."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail=None, grid=None), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_the_candidate_order_is_not_an_identity(self):
        """brief item 9 — the SAME two candidates in the opposite order produce the same
        answer, because the answer is decided by provenance and never by position."""
        first = [_candidate(detail="VIEW A-A", material="300"),
                 _candidate(detail="VIEW B-B", material="355")]
        second = list(reversed(first))
        item = _item(page=2, detail="VIEW B-B")
        outcome_a = cse.resolve_connection_field_reading(
            item, _address(), [_reading("drawing-selby", 2, "run-a", first)])
        outcome_b = cse.resolve_connection_field_reading(
            item, _address(), [_reading("drawing-selby", 2, "run-a", second)])
        assert dict(outcome_a.candidate) == dict(outcome_b.candidate)
        assert (outcome_a.candidate["detail_reference"]
                == outcome_b.candidate["detail_reference"] == "VIEW B-B")
        # The position differs, which is exactly why it is not an identity.
        assert (outcome_a.candidate_position, outcome_b.candidate_position) == (1, 0)

    def test_two_identical_candidates_are_not_one_candidate(self):
        """Two candidates stating the same thing are still two candidates: the provenance
        cannot tell them apart, and the answer says so rather than picking one."""
        same = _candidate(detail="VIEW A-A", material="300")
        rows = [_reading("drawing-selby", 2, "run-a", [same, dict(same)])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW A-A"), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE


# =============================================================================
# 5. The exact attempt. (brief items 10, 11)
# =============================================================================
class TestTheExactAttempt:
    def test_the_cited_run_is_the_run_that_is_read(self):
        """brief item 10."""
        rows = [_reading("drawing-selby", 2, "run-a", [_candidate(detail="A")]),
                _reading("drawing-selby", 2, "run-b", [_candidate(detail="B")])]
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="run-a"), rows)
        assert outcome.candidate["detail_reference"] == "A"

    def test_another_attempt_of_the_same_page_is_never_substituted(self):
        """brief item 11. The page was read — by another attempt. That is RUN_ABSENT, and
        the other attempt's reading is not consulted even though it exists and even though
        it may hold the only candidates there are."""
        rows = [_reading("drawing-selby", 2, "run-other", [_candidate(detail="OTHER")])]
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="run-cited"), rows)
        assert outcome.reason_code == cse.REASON_RUN_ABSENT
        assert "run-other" not in outcome.detail

    def test_a_page_read_only_by_the_cited_attempt_still_resolves(self):
        rows = [_reading("drawing-selby", 2, "run-cited", [_candidate(detail="MINE")]),
                _reading("drawing-selby", 3, "run-other", [_candidate(detail="THEIRS")])]
        outcome = cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="run-cited"), rows)
        assert outcome.candidate["detail_reference"] == "MINE"

    def test_a_duplicated_recording_is_a_broken_caller_not_an_ambiguity(self):
        """One attempt reads a page once — that is the storage layer's primary key. Two
        rows for one (drawing, page, attempt) is a duplicated recording, and answering
        either of them would be inventing a preference."""
        rows = [_reading("drawing-selby", 2, "run-a", [_candidate(detail="X")]),
                _reading("drawing-selby", 2, "run-a", [_candidate(detail="Y")])]
        with pytest.raises(ValueError, match="duplicated recording"):
            cse.resolve_connection_field_reading(
                _item(), _address(analysis_run_id="run-a"), rows)


# =============================================================================
# 6. What is never consulted. (brief items 12, 13, 14)
# =============================================================================
class TestWhatIsNeverConsulted:
    def test_the_annotation_coordinates_change_nothing(self):
        """brief item 12. Coordinates locate evidence on a page; nothing in the evidence
        model establishes that a position says which connection a reading is of, so they
        are carried and never compared."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        item = _item(page=2, detail="VIEW B-B")
        without = cse.resolve_connection_field_reading(item, _address(), rows)
        for x, y in (("0.00", "0.00"), ("41.50", "190.00"), (None, None), (12, 34)):
            with_coords = cse.resolve_connection_field_reading(
                item, _address(annotation_x=x, annotation_y=y), rows)
            assert _decision(with_coords) == _decision(without)
            # and the coordinates themselves are carried, not discarded
            assert (with_coords.annotation_x, with_coords.annotation_y) == (x, y)
        assert (without.annotation_x, without.annotation_y) == (None, None)

    def test_coordinates_alone_never_resolve_an_ambiguous_page(self):
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW C-C"),
            _address(annotation_x="41.50", annotation_y="190.00"), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_the_anchor_is_carried_and_never_interpreted(self):
        """brief item 13. No vocabulary for an anchor exists anywhere in the product, so a
        resolver that read one would be inventing its meaning."""
        for anchor in ((), ("detail-24",), {"region": [1, 2]}, "sheet-1", None, 7):
            outcome = cse.resolve_connection_field_reading(
                _item(), _address(anchor=anchor),
                [_reading("drawing-selby", 2, "run-a", [_candidate()])])
            assert outcome.anchor == anchor

    def test_the_anchor_cannot_make_an_ambiguous_page_resolve(self):
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        for anchor in ("VIEW B-B", "detail-24", {"index": 1}, 1, ()):
            outcome = cse.resolve_connection_field_reading(
                _item(page=2, detail="VIEW C-C"), _address(anchor=anchor), rows)
            assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE, anchor

    def test_the_document_id_creates_no_candidate_identity(self):
        """brief item 14. The document pointer is carried and compared with nothing: two
        documents' readings are told apart by (drawing, page, attempt), never by this."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        item = _item(page=2, detail="VIEW B-B")
        baseline = cse.resolve_connection_field_reading(item, _address(), rows)
        for document in (None, "doc-fab", "doc-asm", "doc-that-does-not-exist"):
            outcome = cse.resolve_connection_field_reading(
                item, _address(document_id=document), rows)
            assert _decision(outcome) == _decision(baseline)
            assert outcome.document_id == document  # carried, and compared with nothing

    def test_the_extractor_version_creates_no_candidate_identity(self):
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        item = _item(page=2, detail="VIEW B-B")
        baseline = cse.resolve_connection_field_reading(item, _address(), rows)
        for version in (None, "j28.1", "something-else", "PDF_VISION_1"):
            outcome = cse.resolve_connection_field_reading(
                item, _address(extractor_version=version), rows)
            assert _decision(outcome) == _decision(baseline)
            assert outcome.extractor_version == version  # carried, never consulted

    def test_the_model_neither_ranks_nor_chooses(self):
        """The result may never carry a winner, a rank, a score, an authority or a role."""
        forbidden = {"winner", "rank", "ranking", "confidence", "priority", "primary",
                     "authority", "role", "document_role", "chosen", "chosen_value",
                     "resolved_value", "score", "weight", "preferred", "best"}
        for model in (cse.ConnectionScopedReading, cse.Unresolved):
            assert not ({field.name for field in dataclasses.fields(model)} & forbidden)


# =============================================================================
# 7. Purity. (brief items 16–20)
# =============================================================================
class TestPurity:
    def test_the_supplied_structures_are_never_mutated(self):
        """brief item 16."""
        only = _candidate(detail="VIEW A-A", material="300")
        rows = [_reading("drawing-selby", 2, "run-a", [only])]
        item = _item(page=2, detail="VIEW A-A")
        address = _address()
        before = (json.dumps(rows, sort_keys=True), json.dumps(item.evidence, sort_keys=True))

        cse.resolve_connection_field_reading(item, address, rows)

        assert json.dumps(rows, sort_keys=True) == before[0]
        assert json.dumps(item.evidence, sort_keys=True) == before[1]

    def test_a_returned_candidate_cannot_be_edited_back_into_the_recording(self):
        """The reading holds its own copy, so a caller editing what it was handed cannot
        change the recorded evidence — and cannot change what the reading says it found."""
        only = _candidate(detail="VIEW A-A", material="300")
        rows = [_reading("drawing-selby", 2, "run-a", [only])]
        outcome = cse.resolve_connection_field_reading(_item(), _address(), rows)
        assert outcome.candidate is not rows[0]["payload"]["raw_connections"][0]
        outcome.candidate["material"] = "EDITED"
        assert rows[0]["payload"]["raw_connections"][0]["material"] == "300"
        assert outcome.candidate["material"] == "EDITED"  # the copy moved; the source did not

    def test_the_same_inputs_give_the_byte_identical_answer(self):
        """brief item 20 — deterministic, and idempotent too."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        item, address = _item(page=2, detail="VIEW B-B"), _address()
        baseline = dataclasses.asdict(cse.resolve_connection_field_reading(item, address, rows))
        for _ in range(5):
            again = cse.resolve_connection_field_reading(item, address, rows)
            assert dataclasses.asdict(again) == baseline

    def test_the_order_of_the_recorded_readings_does_not_change_the_answer(self):
        rows = [_reading("drawing-selby", 2, "run-a", [_candidate(detail="A")]),
                _reading("drawing-selby", 3, "run-a", [_candidate(detail="B")])]
        forward = cse.resolve_connection_field_reading(_item(), _address(), rows)
        backward = cse.resolve_connection_field_reading(
            _item(), _address(), list(reversed(rows)))
        assert dataclasses.asdict(forward) == dataclasses.asdict(backward)

    def test_the_module_imports_no_application_module_at_all(self):
        """A resolver with no dependency on the rest of the application cannot be reached
        by one, and cannot break a fence by importing a module that names what it may not."""
        allowed = {"__future__", "copy", "collections", "dataclasses", "typing"}
        imported: set[str] = set()
        for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported, "the module must import something"
        for module in sorted(imported):
            assert module.split(".")[0] in allowed, module

    def test_the_module_performs_no_io_and_names_no_store(self):
        """brief items 17, 18, 19 asserted over the text: no opener, no client, no table,
        no route and no model call exists to reach anything with."""
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        for forbidden in _IO_TOKENS:
            assert forbidden not in source, forbidden

    def test_a_fresh_interpreter_pulls_no_database_http_or_ai_client(self):
        """brief items 17–20, strongest form: importing the resolver in a clean interpreter
        loads none of the clients the rest of the product uses."""
        probe = (
            "import sys\n"
            "import app.cad_engine.connection_scoped_evidence as cse\n"
            "forbidden = ('supabase', 'httpx', 'requests', 'urllib3', 'postgrest',\n"
            "             'storage3', 'gotrue', 'psycopg', 'boto3', 'sqlalchemy',\n"
            "             'anthropic')\n"
            "bad = sorted(m for m in sys.modules if any(k in m for k in forbidden))\n"
            "print(repr((list(cse.REASON_CODES), bad)))\n"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=str(REPO),
                                capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr
        codes, bad = eval(result.stdout.strip())  # noqa: S307 - our own probe output
        assert tuple(codes) == cse.REASON_CODES
        assert bad == [], bad

    def test_no_application_module_imports_the_resolver(self):
        """brief: no production wiring. Nothing under `app/` names this module at all —
        with ONE declared exception, added by J80 and NAMED here rather than absorbed.

        J80 built the consumer boundary (`cited_candidate_resolution`), which is the first
        module in the product to name the resolver. The claim this guard has always made is
        unchanged, and it is asserted in full beneath the declaration: R3 is reached through
        the consumer boundary and nowhere else. The consumer is still the only module that
        names the resolver.

        J81 gave the consumer its FIRST production caller — the production read port
        (`app/production_recorded_readings.py`), which the workflow read route reaches through
        `build_workflow_review`. That is not a second way to R3: the port never names the
        resolver, so the resolver's own importers are still exactly one module, and the list
        of modules reaching the CONSUMER is exact too, so a further one has to be declared
        here rather than absorbed. The guard is updated, not widened: a module that reached R3
        without the boundary would still turn `naming` red, and a second caller of the
        boundary would still turn `callers` red."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "connection_scoped_evidence" in path.read_text(encoding="utf-8")
        )
        assert naming == ["app/cad_engine/cited_candidate_resolution.py"], naming
        callers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "cited_candidate_resolution" in path.read_text(encoding="utf-8")
        )
        assert callers == ["app/production_recorded_readings.py"], callers

    def test_no_route_was_added(self):
        """The resolver is not reachable over HTTP, and no route names it."""
        source = (APP / "main.py").read_text(encoding="utf-8")
        for token in ("connection_scoped_evidence", "resolve_connection_field_reading"):
            assert token not in source, token


# =============================================================================
# 8. The Selby fixture — the real readings, read-only. (brief: the Selby demonstration)
# =============================================================================
class TestTheSelbyFixture:
    def test_the_fixture_still_states_the_pages_this_suite_relies_on(self):
        """Not an expectation about the world: a measurement of the committed readings,
        pinned so that a changed fixture is noticed rather than silently narrowing what
        this suite exercises."""
        readings = _selby_readings()
        by_page: dict[int, list[dict[str, Any]]] = {}
        for row in readings:
            by_page.setdefault(row["page_number"], []).append(row)
        assert {row["drawing_id"] for row in readings} == {SELBY_DRAWING}
        assert len(by_page) == 32
        assert sum(len(row["payload"]["raw_connections"]) for row in readings) == 53

        counts = {page: max(len(r["payload"]["raw_connections"]) for r in rows)
                  for page, rows in by_page.items()}
        assert sorted(page for page, n in counts.items() if n == 1) == [
            1, 7, 10, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 29, 30, 31]
        assert sorted(page for page, n in counts.items() if n >= 2) == [
            2, 3, 4, 5, 6, 8, 9, 11, 13, 14, 15, 27, 28, 32]
        assert sorted(page for page, n in counts.items() if n == 0) == [12]

    def test_a_single_candidate_page_of_the_fixture_resolves(self):
        """Selby item a — page 1 carries exactly one candidate, so the reading itself is
        the answer and no provenance comparison is needed."""
        candidates = _candidates_on(WINDOW_1_5, 1)
        assert len(candidates) == 1
        outcome = _selby(WINDOW_1_5, 1)
        assert isinstance(outcome, cse.ConnectionScopedReading)
        assert outcome.matched_by == cse.RESOLVED_SINGLE_CANDIDATE
        assert dict(outcome.candidate) == candidates[0]

    def test_a_multi_candidate_page_resolves_only_when_provenance_singles_one_out(self):
        """Selby item b — page 2 states `VIEW A-A` and `VIEW B-B`, so an item that records
        one of them resolves and an item that records neither does not."""
        candidates = _candidates_on(WINDOW_1_5, 2)
        assert len(candidates) == 2
        assert [c["detail_reference"] for c in candidates] == ["VIEW A-A", "VIEW B-B"]

        resolved = _selby(WINDOW_1_5, 2, page=2, detail="VIEW B-B")
        assert resolved.matched_by == cse.RESOLVED_PROVENANCE
        assert resolved.candidate["detail_reference"] == "VIEW B-B"

        for detail in ("VIEW C-C", None, "view b-b"):
            refused = _selby(WINDOW_1_5, 2, page=2, detail=detail)
            assert refused.reason_code == cse.REASON_AMBIGUOUS_PAGE, detail

    def test_the_real_page_with_no_candidate_is_candidate_absent(self):
        """Page 12 of the committed readings states zero connections — a real
        CANDIDATE_ABSENT rather than a synthetic one."""
        assert _candidates_on(WINDOW_11_15, 12) == []
        outcome = _selby(WINDOW_11_15, 12)
        assert outcome.reason_code == cse.REASON_CANDIDATE_ABSENT

    def test_the_real_page_whose_provenance_matches_three_candidates_is_ambiguous(self):
        """Page 14 states four candidates, three of which share the grid reference
        `N/S FITTINGS`. The recorded provenance therefore cannot single one out, and the
        resolver refuses rather than taking the first."""
        candidates = _candidates_on(WINDOW_11_15, 14)
        assert len(candidates) == 4
        assert [c["grid_reference"] for c in candidates].count("N/S FITTINGS") == 3
        assert _selby(WINDOW_11_15, 14, grid="N/S FITTINGS").reason_code \
            == cse.REASON_AMBIGUOUS_PAGE

    def test_page_26_was_read_by_two_attempts_and_each_is_answerable_on_its_own(self):
        """The fixture's only re-read page. Both readings state the same single candidate,
        which is exactly why silent substitution would be INVISIBLE from the value alone —
        so the property proved here is structural: the reading returned is the cited
        attempt's own."""
        rows = [row for row in _selby_readings()
                if row["page_number"] == 26 and row["drawing_id"] == SELBY_DRAWING]
        assert len(rows) == 2
        runs = sorted(row["analysis_run_id"] for row in rows)
        assert runs == sorted([_run_of(WINDOW_26_30), _run_of(RECOVERY_26)])
        assert _candidates_on(WINDOW_26_30, 26) == _candidates_on(RECOVERY_26, 26)

        for run in runs:
            outcome = cse.resolve_connection_field_reading(
                _item(), _address(drawing_id=SELBY_DRAWING, page_number=26,
                                  analysis_run_id=run), rows)
            assert outcome.analysis_run_id == run

    def test_an_attempt_that_never_read_the_page_is_run_absent_on_real_data(self):
        """Page 26 was really read — by the 26–30 window and by the recovery pass. A
        citation naming a third window for it is RUN_ABSENT, not a missing page."""
        outcome = cse.resolve_connection_field_reading(
            _item(),
            _address(drawing_id=SELBY_DRAWING, page_number=26,
                     analysis_run_id=_run_of(WINDOW_16_20)),
            [_selby_page(WINDOW_26_30, 26)])
        assert outcome.reason_code == cse.REASON_RUN_ABSENT
        assert _run_of(WINDOW_26_30) not in outcome.detail

    def test_a_page_absent_from_the_cited_window_is_run_absent_on_real_data(self):
        """Page 1 was read — by the first window. A citation naming another window for it
        is not a page that does not exist: that is RUN_ABSENT, and the two are told apart
        on the real fixture, not only on synthetic rows."""
        outcome = cse.resolve_connection_field_reading(
            _item(),
            _address(drawing_id=SELBY_DRAWING, page_number=1,
                     analysis_run_id=_run_of(WINDOW_6_10)),
            [_selby_page(WINDOW_1_5, 1)])
        assert outcome.reason_code == cse.REASON_RUN_ABSENT

    def test_no_disputed_literal_appears_anywhere_and_no_disagreement_is_resolved(self):
        """The Selby Ø18/Ø22 question and the Transmittal-versus-drawing-set coverage
        question are NOT this resolver's to answer, and nothing here answers them. What is
        proved is only that a reading can be selected: the module names neither the disputed
        documents nor a hole diameter, so it has no vocabulary in which to prefer one."""
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        for token in ("029x", "030x", "031x", "032x", "transmittal", "diameter", "ø",
                      "fab", "assembly", "isometric"):
            assert token not in source, token
        # `hole` is checked as a word: the prose legitimately contains `whole`.
        assert re.findall(r"\bhole\b", source) == []

    def test_the_selby_fixture_is_read_and_never_written(self):
        """The fixture is consumed, never written: this suite's own loader is the only
        reader, and every call it makes is a read. Read off the syntax tree rather than by
        substring, so this test's own vocabulary cannot be what it finds."""
        tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
        attributes = {node.func.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert "read_text" in attributes
        assert not (attributes & {"write", "writelines", "unlink", "rmtree", "remove",
                                  "rename", "mkdir", "chmod"})
        # And nothing this file calls by bare name opens a file either.
        assert not [node for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("open", "compile_file")]


# =============================================================================
# 9. The J69 boundary — layer A, filled.
# =============================================================================
class TestTheJ69Boundary:
    """J69's evidence loading is a SEAM it deliberately left open. These tests prove the
    resolver fills it: the candidate it returns becomes a J69 reading and drives J69's own
    comparison, with no change to J69 and no second copy of any value."""

    @staticmethod
    def _address_of(resolved):
        from app.cad_engine import evidence_conflict_detection as ecd

        return ecd.EvidenceAddress(**{name: getattr(resolved, name)
                                      for name in cse.ADDRESS_TERMS})

    @classmethod
    def _reference(cls, resolved, ordinal: int):
        from app.cad_engine import evidence_conflict_detection as ecd

        return ecd.FieldEvidenceReference(field="material", ordinal=ordinal,
                                          evidence_kind="SOURCE",
                                          address=cls._address_of(resolved))

    @classmethod
    def _reading(cls, resolved):
        from app.cad_engine import evidence_conflict_detection as ecd

        return ecd.EvidenceReading(address=cls._address_of(resolved),
                                   value=resolved.candidate["material"])

    @staticmethod
    def _resolve(rows, **address: Any):
        return cse.resolve_connection_field_reading(_item(), _address(**address), rows)

    def test_the_result_builds_j69s_address_field_for_field(self):
        resolved = self._resolve(
            [_reading("drawing-selby", 2, "run-a", [_candidate(material="300")])],
            analysis_run_id="run-a",
            annotation_x="41.50", annotation_y="190.00", extractor_version="j28.1",
            anchor=("detail-24",))
        assert dataclasses.asdict(self._address_of(resolved)) == {
            "drawing_id": "drawing-selby", "page_number": 2,
            "analysis_run_id": "run-a", "document_id": "doc-fab",
            "annotation_x": "41.50", "annotation_y": "190.00",
            "extractor_version": "j28.1", "anchor": ("detail-24",),
        }

    def test_two_documents_that_agree_are_not_conflicted(self):
        """The J70 design's §21 shape: one connection, two documents, one answer."""
        from app.cad_engine import evidence_conflict_detection as ecd

        rows = [_reading("drawing-fab", 12, "run-fab", [_candidate(material="300")]),
                _reading("drawing-asm", 3, "run-asm", [_candidate(material="300")])]
        first = self._resolve(rows, document_id="doc-fab", drawing_id="drawing-fab",
                              page_number=12, analysis_run_id="run-fab")
        second = self._resolve(rows, document_id="doc-asm", drawing_id="drawing-asm",
                               page_number=3, analysis_run_id="run-asm")
        result = ecd.reconcile_field(
            "material",
            [self._reference(first, 1), self._reference(second, 2)],
            [self._reading(first), self._reading(second)],
        )
        assert result.state != "CONFLICTED"
        assert result.unresolved == ()
        assert len(result.competing) == 2

    def test_two_documents_that_disagree_are_conflicted_and_both_sides_survive(self):
        """The whole point of the chain: the disagreement is DETECTED, both addresses are
        preserved, and neither document is chosen."""
        from app.cad_engine import evidence_conflict_detection as ecd

        rows = [_reading("drawing-fab", 12, "run-fab", [_candidate(material="300")]),
                _reading("drawing-asm", 3, "run-asm", [_candidate(material="355")])]
        first = self._resolve(rows, document_id="doc-fab", drawing_id="drawing-fab",
                              page_number=12, analysis_run_id="run-fab")
        second = self._resolve(rows, document_id="doc-asm", drawing_id="drawing-asm",
                               page_number=3, analysis_run_id="run-asm")
        result = ecd.reconcile_field(
            "material",
            [self._reference(first, 1), self._reference(second, 2)],
            [self._reading(first), self._reading(second)],
        )
        assert result.state == "CONFLICTED"
        assert len(result.competing) == 2
        assert {entry.value for entry in result.competing} == {"300", "355"}
        assert {entry.address.document_id for entry in result.competing} == {
            "doc-fab", "doc-asm"}

    def test_the_resolvers_refusal_is_the_channel_j69_already_has(self):
        """A refusal needs no new vocabulary in J69: it simply supplies no reading, and
        J69's own `unresolved` reports the recording it could not compare."""
        from app.cad_engine import evidence_conflict_detection as ecd

        rows = [_reading("drawing-fab", 12, "run-fab", [
            _candidate(detail="A", material="300"), _candidate(detail="B", material="355")])]
        reference = _address(document_id="doc-fab", drawing_id="drawing-fab",
                             page_number=12, analysis_run_id="run-fab")
        refused = cse.resolve_connection_field_reading(
            _item(page=12, detail="NEITHER"), reference, rows)
        assert isinstance(refused, cse.Unresolved)

        # The recorded reference still exists and is still reported; no reading is supplied
        # for it, so J69 says so instead of comparing nothing.
        address = self._address_of(reference)
        result = ecd.reconcile_field(
            "material",
            [ecd.FieldEvidenceReference(field="material", ordinal=1,
                                        evidence_kind="SOURCE", address=address)],
            [],
        )
        assert result.unresolved == (address,)
        assert result.competing == ()

    def test_the_resolver_never_compares_two_candidates_itself(self):
        """The comparison is J69's. The resolver's own output holds ONE candidate and no
        second value, so a disagreement cannot be manufactured here."""
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="VIEW A-A", material="300"),
            _candidate(detail="VIEW B-B", material="355")])]
        outcome = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW B-B"), _address(), rows)
        assert set(dataclasses.asdict(outcome)) == {
            "field_name", "review_package_id", "drawing_id", "page_number",
            "analysis_run_id", "document_id", "annotation_x", "annotation_y",
            "extractor_version", "anchor", "candidate_position", "matched_by", "candidate",
        }


# =============================================================================
# 10. The item, against the product's own object.
# =============================================================================
class TestTheGenuineItem:
    def test_the_real_snapshot_item_is_read_without_being_adapted(self):
        """`ReviewSnapshotItem` is the object a review actually records. The resolver reads
        its `evidence` and nothing else, so the genuine object needs no wrapper."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(_snapshot_item(), _address(), rows)
        assert outcome.matched_by == cse.RESOLVED_PROVENANCE
        assert outcome.candidate["detail_reference"] == "VIEW B-B"
        assert outcome.review_package_id == "RP-0004"

    def test_the_real_item_records_its_page_under_source_page(self):
        """The key compared against the reading's page is the review layer's own recorded
        evidence key, not a second spelling invented here."""
        assert cse.PROVENANCE_PAGE_KEY in _snapshot_item().evidence
        assert f'"{cse.PROVENANCE_PAGE_KEY}"' in SNAPSHOT_MODULE.read_text(encoding="utf-8")

    def test_an_item_recording_no_provenance_refuses_rather_than_guessing(self):
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        outcome = cse.resolve_connection_field_reading(_Item(evidence={}), _address(), rows)
        assert outcome.reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_the_refusal_says_which_kind_of_failure_it_is(self):
        """Both refusals are AMBIGUOUS_PAGE, and the detail distinguishes an item that
        recorded nothing from one that recorded values matching nothing — so a reader is
        never told "no match" when the truth is "you told me nothing"."""
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        silent = cse.resolve_connection_field_reading(_Item(evidence={}), _address(), rows)
        wrong = cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW C-C"), _address(), rows)
        assert silent.reason_code == wrong.reason_code == cse.REASON_AMBIGUOUS_PAGE
        assert "matches no candidate" not in silent.detail
        assert "matches no candidate" in wrong.detail


# =============================================================================
# 11. The boundaries this milestone did not touch.
# =============================================================================
class TestTheBoundariesAreUntouched:
    def test_the_j66_tree_wide_fence_would_still_hold_with_this_module_present(self):
        """J66 asserts that exactly three modules in `app/` name the vocabulary J66 owns —
        which is how "no production writer exists" is a checkable fact. This module names
        none of it, so the fence is exact with a fourth new module in the tree."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/cad_engine/review_contract.py",
            "app/engineering_data/connection_review_repository.py",
        ]

    def test_this_module_names_none_of_it_either(self):
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        for token in ("citation", "connection_review_item_citations",
                      "connection_review_citation_ownership", "v_capture_project"):
            assert token not in source, token

    def test_j69s_own_invariants_still_hold(self):
        """J69 was accepted because it names J66's vocabulary nowhere and is imported by
        nothing in production. J71 must not disturb either."""
        conflict_module = APP / "cad_engine" / "evidence_conflict_detection.py"
        assert "citation" not in conflict_module.read_text(encoding="utf-8").lower()
        importers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "evidence_conflict_detection" in path.read_text(encoding="utf-8")
        )
        assert importers == [], importers

    def test_j68_is_consumed_by_j69_and_reached_by_nothing_here(self):
        """The pure state model is consumed, never extended, and this module does not even
        name it."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "reconcil" not in source.lower()
        assert (APP / "cad_engine" / "reconciliation_state.py").exists()

    def test_the_storage_layer_is_imported_by_the_modules_it_always_was(self):
        """The resolver quotes four column names rather than importing the capture module,
        so the storage layer's importer set is unchanged by this milestone."""
        importers = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "page_extraction_capture" in path.read_text(encoding="utf-8")
        )
        assert "app/cad_engine/connection_scoped_evidence.py" not in importers

    def test_the_resolver_adds_no_revision_and_writes_nothing(self):
        """No writer verb, no client, no store, no revision — asserted over the text."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("review_revision", "persist_", "insert into", ".table(", ".rpc(",
                          "create table", "put_object", "upload("):
            assert forbidden not in source, forbidden

    def test_the_recorded_position_is_named_once_and_nothing_is_reconstructed(self):
        """J74 reads a position where the ITEM itself recorded one, so the module does name
        it — once, as a term of the recorded address. It still names no submission order,
        no ordinal and no review numbering, which is what would make the position a
        reconstruction rather than a recording."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert source.count('CANDIDATE_POSITION_KEY = "candidate_index"') == 1
        for token in ("submission_index", "ordinal", "review_revision",
                      "review_package_id\":"):
            assert token not in source, token

    def test_the_result_exposes_the_position_it_found_and_calls_it_nothing_more(self):
        """The one term that could be mistaken for R3 says what it is: an observation about
        THIS reading, not an identity and not comparable across readings."""
        doc = " ".join((cse.ConnectionScopedReading.__doc__ or "").split())
        assert "NOT comparable across readings" in doc
        assert "R3" in doc


# =============================================================================
# 12. Mutation controls — proof that the tests above are load-bearing.
# =============================================================================
class TestMutations:
    def test_mutation_1_removing_the_ambiguity_refusal_reddens_the_ambiguity_tests(self):
        module = _mutant(*_NO_AMBIGUITY)
        rows = [_reading("drawing-selby", 2, "run-a", _two())]
        # A page whose provenance matches nothing now resolves to the first candidate…
        assert isinstance(module.resolve_connection_field_reading(
            _item(page=2, detail="VIEW C-C"), _address(), rows),
            module.ConnectionScopedReading)
        # …which is what the real module refuses.
        assert cse.resolve_connection_field_reading(
            _item(page=2, detail="VIEW C-C"), _address(), rows).reason_code \
            == cse.REASON_AMBIGUOUS_PAGE

    def test_mutation_2_coercive_equality_reddens_the_no_coercion_test(self):
        module = _mutant(*_COERCIVE_EQUALITY)
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail="22"), _candidate(detail="B")])]
        # Under coercion the number 22 and the string "22" become one value…
        assert isinstance(module.resolve_connection_field_reading(
            _item(page=2, detail=22), _address(), rows), module.ConnectionScopedReading)
        # …and the real module keeps them apart.
        assert cse.resolve_connection_field_reading(
            _item(page=2, detail=22), _address(), rows).reason_code \
            == cse.REASON_AMBIGUOUS_PAGE

    def test_mutation_3_letting_null_match_null_reddens_the_null_tests(self):
        module = _mutant(*_NOTHING_IS_STATED)
        rows = [_reading("drawing-selby", 2, "run-a", [
            _candidate(detail=None, grid="A"), _candidate(detail=None, grid=None)])]
        item = _item(page=2, detail=None, grid=None)
        # The item's absent grid now matches the second candidate's absent grid, inventing
        # a unique candidate out of an absence…
        invented = module.resolve_connection_field_reading(item, _address(), rows)
        assert isinstance(invented, module.ConnectionScopedReading)
        assert invented.candidate_position == 1
        # …and the real module refuses, because NULL never matches NULL.
        assert cse.resolve_connection_field_reading(
            item, _address(), rows).reason_code == cse.REASON_AMBIGUOUS_PAGE

    def test_mutation_4_substituting_another_attempt_reddens_the_exact_attempt_test(self):
        module = _mutant(*_RUN_SUBSTITUTION)
        rows = [_reading("drawing-selby", 2, "run-other", [_candidate(detail="OTHER")])]
        substituted = module.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="run-cited"), rows)
        assert substituted.analysis_run_id == "run-cited"  # the ADDRESS still says cited…
        assert substituted.candidate["detail_reference"] == "OTHER"  # …but the evidence is not
        # The real module refuses instead of substituting.
        assert cse.resolve_connection_field_reading(
            _item(), _address(analysis_run_id="run-cited"), rows).reason_code \
            == cse.REASON_RUN_ABSENT

    def test_mutation_5_dropping_the_snapshot_reddens_the_no_mutation_test(self):
        module = _mutant(*_NO_SNAPSHOT)
        only = _candidate(detail="VIEW A-A", material="300")
        rows = [_reading("drawing-selby", 2, "run-a", [only])]
        outcome = module.resolve_connection_field_reading(_item(), _address(), rows)
        outcome.candidate["material"] = "EDITED"
        # The returned reading aliases the recording, so the recording changes under it.
        assert rows[0]["payload"]["raw_connections"][0]["material"] == "EDITED"

    def test_every_mutation_anchor_is_unique_in_the_module(self):
        for old, _ in (_NO_AMBIGUITY, _COERCIVE_EQUALITY, _NOTHING_IS_STATED,
                       _RUN_SUBSTITUTION, _NO_SNAPSHOT):
            assert MODULE_PATH.read_text(encoding="utf-8").count(old) == 1, old
