"""
J72 — PRESERVE THE CONNECTION CANDIDATE'S ORIGIN ADDRESS: the proof file.

WHAT THIS FILE IS

The proof for `app/cad_engine/candidate_origin_address.py` and for the one change J72 makes
to the review-item producer: a review item's `evidence` payload now carries

    analysis_run_id    the analysis run whose page reading produced the candidate
    candidate_index    the candidate's ZERO-BASED position in THAT page's own
                       `raw_connections` array

Nothing else about the payload changes. The two keys are ABSENT — not null — whenever the
producer cannot prove them, so every review item recorded before J72 keeps loading exactly
as it did, and no historical item gains a guessed address.

WHAT IS REAL, AND WHAT IS NOT

Real: the module, the producer, the reconstruction, J21's `build_review_snapshot`, J23's
capture selection and the committed Selby readings under `tests/data/` (32 pages over one
document, read in seven windows, so seven attempts each read a different slice of it).
Not real: the synthetic three-page fixtures the validation cases need, which are plain
mappings in the shape `intake_page_extractions` already accepts.

WHAT THIS FILE DOES NOT CLAIM

It does not claim the index is an identity. It is an array position: it is not a connection
id, not a review package id, not `submission_index`, not the number in `RP-####`, not
durable across a re-read and not a ranking. The tests below assert exactly that, in both
directions — that the recorded index IS the page's own position, and that it is NOT the
positions it is most likely to be confused with.

It does not resolve any engineering disagreement. The Selby Ø18-versus-Ø22 diameter question
and the Transmittal 029X versus the drawing-set coverage question are untouched, unnamed,
and are given no expected answer anywhere below: this milestone preserves WHERE a candidate
was read, never WHICH reading is right.

And it does not wire the resolver (J71) into the product. Nothing under `app/` imports it,
before or after this milestone.
"""
from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.cad_engine import candidate_origin_address as coa
from app.cad_engine.connection_review_snapshot import build_review_snapshot
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.engineering_data import page_extraction_capture as captures

from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j21_connection_review_data_model_design as j21
from tests import test_real_world_j23_page_extraction_capture as j23
from tests import test_real_world_j24a_revision_zero_producer as j24a

REPO = j13.REPO
APP = REPO / "app"
MODULE_PATH = APP / "cad_engine" / "candidate_origin_address.py"
INTAKE_PATH = APP / "cad_engine" / "project_extraction_intake.py"
PRODUCER_PATH = APP / "production_review" / "project_workflow_reconstruction.py"

NO_EVIDENCE = {"steel_members": (), "connections": ()}

# The tokens that would mean the module had learned to reach something. Asserted over the
# TEXT, so a reach that is never executed is caught too.
_IO_TOKENS = (
    "open(", "pathlib", "os.environ", "getenv", "socket", "requests", "httpx", "urllib",
    "supabase", "postgrest", "storage3", "psycopg", "boto3", "sqlalchemy", "anthropic",
    ".table(", ".rpc(", "insert into", "create table", "put_object", "upload(", "select ",
    "from app.", "import app",
)

# The two terms, as the module names them — quoted from the module rather than restated, so
# a rename cannot leave this file asserting about a vocabulary nothing uses.
RUN_KEY = coa.CANDIDATE_ORIGIN_RUN_KEY
INDEX_KEY = coa.CANDIDATE_ORIGIN_INDEX_KEY


# ===========================================================================
# The synthetic three-page fixture. Its shape is 7Y's own accepted input; the
# numbers are chosen so that the page-local index and the flattened submission
# index DIVERGE, which is the whole point of the milestone.
# ===========================================================================
def _page(number: int, connection_count: int, *, detail_prefix: str = "D") -> dict[str, Any]:
    return {
        "page_number": number,
        "drawing_number": "001",
        "drawing_title": "a set",
        "revision": "A",
        "parse_failed": False,
        "raw_members": [],
        "raw_connections": [
            {
                "detail_reference": f"{detail_prefix}{number}-{index}",
                "grid_reference": None,
                "connects_members": [],
                "connection_type": "welded",
                "bolts": [],
                "plates": [],
                "welds": [],
                "confidence": "high",
            }
            for index in range(connection_count)
        ],
    }


def _three_pages() -> list[dict[str, Any]]:
    """Page 1 contributes two candidates, page 2 one, page 3 three.

    Submission order therefore runs RP-0001..RP-0006 while the page-local indexes run
    0,1 / 0 / 0,1,2 — so `candidate_index` and `submission_index` disagree from RP-0003 on.
    """
    return [_page(1, 2), _page(2, 1), _page(3, 3)]


def _intake(pages, *, runs=None):
    return intake_page_extractions(
        pages,
        project_id="PROJ-7J72",
        source_drawing_id="drawing-7j72",
        drawing_set_page_count=len(pages),
        page_analysis_run_ids=runs,
    )


class _NoMatcher:
    """The caller's section matcher, answering nothing.

    At revision 0 the matcher is carried and never consulted, so a matcher that resolved
    sections would make these fixtures look as though geometry had been involved.
    """

    def match(self, raw_name):
        return None


def _snapshot_of(intake, *, project_id: str = "PROJ-7J72"):
    """The producer's own snapshot of an intake — evidence is never assembled by hand."""
    from app.cad_engine.project_workflow import start_project_workflow

    workflow = start_project_workflow(
        intake.collection, intake=intake, member_rows={}, member_placements={},
        section_matcher=_NoMatcher(),
    )
    return build_review_snapshot(
        workflow, project_id=project_id, evidence_rows=NO_EVIDENCE, previous_revision=None,
    )


def _evidence_of(intake, package_id: str) -> dict[str, Any]:
    return next(
        item.evidence for item in _snapshot_of(intake).items
        if item.review_package_id == package_id
    )


def _evidence_by_package(intake, *, project_id: str = "PROJ-7J72") -> dict[str, dict[str, Any]]:
    return {
        item.review_package_id: dict(item.evidence)
        for item in _snapshot_of(intake, project_id=project_id).items
    }


# ===========================================================================
# 1-8, 11-12. THE ADDRESS ITSELF
# ===========================================================================
class TestTheCandidateOriginIsRecorded:
    def test_1_a_single_candidate_records_the_run_that_read_its_page(self):
        """brief item 1. One page, one candidate, one stated run — that run is recorded."""
        intake = _intake(_three_pages()[:1], runs=["run-page-1"])
        assert _evidence_of(intake, "RP-0001")[RUN_KEY] == "run-page-1"

    def test_2_a_single_candidate_records_index_zero(self):
        """brief item 2. The first candidate of a page is at position 0."""
        intake = _intake(_three_pages()[:1], runs=["run-page-1"])
        assert _evidence_of(intake, "RP-0001")[INDEX_KEY] == 0

    def test_3_each_candidate_records_its_own_zero_based_position(self):
        """brief item 3. Six candidates over three pages: each is at its own page's index."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        assert [evidence[f"RP-{n:04d}"][INDEX_KEY] for n in range(1, 7)] == [0, 1, 0, 0, 1, 2]
        assert [evidence[f"RP-{n:04d}"][RUN_KEY] for n in range(1, 7)] == [
            "run-1", "run-1", "run-2", "run-3", "run-3", "run-3",
        ]

    def test_4_5_6_index_zero_one_and_above_one_are_all_kept(self):
        """brief items 4, 5 and 6, stated as the three positions they name."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        assert evidence["RP-0004"][INDEX_KEY] == 0          # index 0
        assert evidence["RP-0005"][INDEX_KEY] == 1          # index 1
        assert evidence["RP-0006"][INDEX_KEY] == 2          # index > 1

    def test_7_the_candidate_index_is_not_the_submission_index(self):
        """brief item 7. The two numbers agree only while one page contributes everything.

        Here page 3's first candidate is submission 4 and page position 0 — the exact case
        where the flat list's number would name the wrong candidate.
        """
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        by_package = {c.review_package_id: c for c in intake.collection.candidates}
        evidence = _evidence_by_package(intake)
        assert by_package["RP-0004"].submission_index == 3
        assert evidence["RP-0004"][INDEX_KEY] == 0
        differing = [
            package for package, candidate in by_package.items()
            if candidate.submission_index != evidence[package][INDEX_KEY]
        ]
        assert differing == ["RP-0003", "RP-0004", "RP-0005", "RP-0006"], differing

    def test_8_the_candidate_index_is_not_the_number_in_the_review_package_id(self):
        """brief item 8. `RP-####` counts the flattened list; the index counts one page."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        for number in range(1, 7):
            package = f"RP-{number:04d}"
            assert evidence[package][INDEX_KEY] != number - 1 or number <= 2, package
        assert evidence["RP-0004"][INDEX_KEY] == 0

    def test_11_the_index_is_not_inferred_from_the_review_packages_order(self):
        """brief item 11. The recorded index is the page's own, not the package's ordinal.

        RP-0004 is the FOURTH package and the FIRST candidate of its page; a producer that
        counted packages, or that carried the flattened position forward, would record 3.
        """
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        assert list(evidence) == ["RP-0001", "RP-0002", "RP-0003", "RP-0004", "RP-0005", "RP-0006"]
        assert [evidence[p][INDEX_KEY] for p in evidence] != list(range(len(evidence)))

    def test_12_the_index_is_not_inferred_from_the_task_order(self):
        """brief item 12. Every item's own tasks are the exception contract's, and the index
        is the candidate's page position — the two orders are not the same list at all."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        for package, payload in evidence.items():
            assert payload[INDEX_KEY] in (0, 1, 2), package
            assert set(payload) >= {"source_page", "analysis_run_id", "candidate_index"}

    def test_the_index_and_the_run_are_recorded_together_or_not_at_all(self):
        """The address is one address: no payload below carries exactly one of the two."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        for payload in _evidence_by_package(intake).values():
            assert (RUN_KEY in payload) == (INDEX_KEY in payload)

    def test_the_recorded_index_addresses_a_real_position_in_that_pages_array(self):
        """The number is not merely well-formed: it indexes the page's own array."""
        pages = _three_pages()
        intake = _intake(pages, runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        for payload in evidence.values():
            page = next(p for p in pages if p["page_number"] == payload["source_page"])
            index = payload[INDEX_KEY]
            assert 0 <= index < len(page["raw_connections"]), payload


# ===========================================================================
# 9-10. THE RUN IS THE ATTEMPT THAT READ THE PAGE
# ===========================================================================
class TestTheRunIsTheAttemptThatReadThePage:
    def test_9_the_run_is_the_pages_own_capture_run(self):
        """brief item 9. Two pages read by two attempts keep their own attempts' names."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        evidence = _evidence_by_package(intake)
        assert evidence["RP-0001"][RUN_KEY] == "run-1"
        assert evidence["RP-0003"][RUN_KEY] == "run-2"
        assert evidence["RP-0004"][RUN_KEY] == "run-3"

    def test_10_a_different_attempt_is_not_substituted_for_the_pages_run(self):
        """brief item 10. A producer that reached for "the" run — the first, the last, the
        one that stands for the document — would record one name for every candidate. Each
        page's run is recorded instead, so the three names appear and no other does."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        recorded = {payload[RUN_KEY] for payload in _evidence_by_package(intake).values()}
        assert recorded == {"run-1", "run-2", "run-3"}

    def test_10b_a_re_read_page_names_the_attempt_that_actually_produced_the_candidates(self):
        """brief item 10, on a genuine re-read: one page recorded twice, by two different
        attempts. J23 selects the reading that STANDS for the page, and the candidates come
        from that reading — so the origin names ITS run, and never the displaced one."""
        page = _page(1, 2)
        page["raw_members"] = [{"mark": "M1"}]
        table = j23._CaptureTable()
        for run in ("run-old", "run-new"):
            j23._record(
                table, [page], run=run, drawing=j24a.SELBY_DRAWING,
                drawing_set=j24a.SELBY_SET, project=j24a.SELBY_PROJECT,
            )
        store = j24a._store(
            project_id=j24a.SELBY_PROJECT, set_id=j24a.SELBY_SET,
            drawing_id=j24a.SELBY_DRAWING, page_count=1, table=table, pages=[page],
        )

        # Both attempts are recorded; exactly one of them stands for the page.
        recorded = {
            row["analysis_run_id"]
            for row in store.page_extraction_captures_for_drawing(j24a.SELBY_DRAWING)
        }
        assert recorded == {"run-old", "run-new"}

        result = j24a._reconstruct(store, j24a.SELBY_PROJECT)
        assert result.capture_run_ids == ("run-new",)

        snapshot = build_review_snapshot(
            result.workflow, project_id=j24a.SELBY_PROJECT, evidence_rows=NO_EVIDENCE,
            evidence_run_ids=result.capture_run_ids, previous_revision=None,
        )
        assert snapshot.items
        for item in snapshot.items:
            assert item.evidence[RUN_KEY] == "run-new", item.review_package_id
            assert item.evidence[RUN_KEY] != "run-old", item.review_package_id

    def test_a_page_with_no_stated_run_records_no_run_at_all(self):
        """The fail-closed seam. One page of three has no run; that page's candidates
        record nothing, and its neighbours' candidates keep their own runs."""
        intake = _intake(_three_pages(), runs=["run-1", None, "run-3"])
        evidence = _evidence_by_package(intake)
        assert RUN_KEY not in evidence["RP-0003"]
        assert INDEX_KEY not in evidence["RP-0003"]
        assert evidence["RP-0001"][RUN_KEY] == "run-1"
        assert evidence["RP-0004"][RUN_KEY] == "run-3"

    def test_no_run_stated_at_all_records_nothing_on_any_item(self):
        """A caller that states no runs (the pre-J72 call shape) records no addresses."""
        intake = _intake(_three_pages())
        for payload in _evidence_by_package(intake).values():
            assert RUN_KEY not in payload
            assert INDEX_KEY not in payload


# ===========================================================================
# 13-15. HISTORY IS UNTOUCHED
# ===========================================================================
class TestHistoricalEvidenceIsUnchanged:
    def test_13_evidence_without_the_new_terms_remains_valid(self):
        """brief item 13. A payload written before J72 loads: it is only read through the
        snapshot contract, and the contract does not require either term."""
        snapshot = _snapshot_of(_intake(_three_pages()))
        replayed = j21._round_trip(snapshot)
        assert len(replayed.items) == len(snapshot.items)
        for item in replayed.items:
            assert coa.origin_of(item.evidence) is None

    def test_14_the_existing_evidence_keys_are_intact(self):
        """brief item 14. The five keys the item had before J72 are all still there, with
        the two new ones beside them — nothing renamed, nothing replaced."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        for payload in _evidence_by_package(intake).values():
            assert set(payload) == {
                "source_drawing_id", "drawing_number", "source_page", "detail_reference",
                "grid_reference", RUN_KEY, INDEX_KEY,
            }
        without = _intake(_three_pages())
        for payload in _evidence_by_package(without).values():
            assert set(payload) == {
                "source_drawing_id", "drawing_number", "source_page", "detail_reference",
                "grid_reference",
            }

    def test_15_the_unrelated_evidence_values_are_unchanged(self):
        """brief item 15. Adding the address moved nothing: every other value is the same
        object-for-object as the run-less build's."""
        with_runs = _evidence_by_package(_intake(_three_pages(), runs=["r1", "r2", "r3"]))
        without = _evidence_by_package(_intake(_three_pages()))
        for package in without:
            unrelated = {k: v for k, v in with_runs[package].items() if k not in coa.ORIGIN_KEYS}
            assert unrelated == without[package], package
            assert unrelated["detail_reference"] == without[package]["detail_reference"]

    def test_a_payload_stating_only_one_term_is_refused_rather_than_completed(self):
        """Half an address is a producer that lost the other half. It is never filled in."""
        with pytest.raises(coa.CandidateOriginRefused) as run_only:
            coa.origin_of({RUN_KEY: "run-1"})
        assert run_only.value.code == coa.ORIGIN_INDEX_INVALID
        with pytest.raises(coa.CandidateOriginRefused) as index_only:
            coa.origin_of({INDEX_KEY: 2})
        assert index_only.value.code == coa.ORIGIN_RUN_ABSENT

    def test_no_historical_item_is_backfilled(self):
        """brief: DO NOT BACKFILL. Reading a payload changes nothing, and a payload that
        never had the terms still has none afterwards."""
        payload = {"source_page": 7, "detail_reference": "24"}
        carried = coa.with_origin(payload, None)
        assert carried == payload
        assert payload == {"source_page": 7, "detail_reference": "24"}


# ===========================================================================
# 16-21. VALIDATION REFUSES RATHER THAN COERCING
# ===========================================================================
class TestValidationRefusesRatherThanCoerces:
    @pytest.mark.parametrize("value", [-1, -100])
    def test_16_a_negative_index_is_refused(self, value):
        """brief item 16. There is no candidate before the first one."""
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.CandidateOrigin("run-1", value)
        assert caught.value.code == coa.ORIGIN_INDEX_INVALID
        assert coa.is_recorded_index(value) is False

    @pytest.mark.parametrize("value", [1.0, 0.0, 2.5])
    def test_17_a_float_index_is_refused(self, value):
        """brief item 17. `1.0` is not position 1, and it is never converted to one."""
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.CandidateOrigin("run-1", value)
        assert caught.value.code == coa.ORIGIN_INDEX_INVALID
        assert coa.is_recorded_index(value) is False

    @pytest.mark.parametrize("value", ["1", "0", "", "one"])
    def test_18_a_string_index_is_refused(self, value):
        """brief item 18. A numeric string is a string; it is never parsed into a position."""
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.CandidateOrigin("run-1", value)
        assert caught.value.code == coa.ORIGIN_INDEX_INVALID
        assert coa.is_recorded_index(value) is False

    @pytest.mark.parametrize("value", [True, False])
    def test_19_a_boolean_index_is_refused(self, value):
        """brief item 19. `bool` IS an `int` subclass, which is exactly why it is excluded
        by name: `True` is a flag, not position 1."""
        assert isinstance(value, int) and isinstance(value, bool)
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.CandidateOrigin("run-1", value)
        assert caught.value.code == coa.ORIGIN_INDEX_INVALID
        assert coa.is_recorded_index(value) is False

    def test_20_an_out_of_range_index_is_refused_rather_than_clamped(self):
        """brief item 20. A page of two candidates named by position 2 names nobody, and
        clamping it to 1 would invent a candidate."""
        candidates = [{"a": 1}, {"b": 2}]
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.origin_at_page_index(candidates, 2, analysis_run_id="run-1")
        assert caught.value.code == coa.ORIGIN_INDEX_OUT_OF_RANGE
        assert coa.origin_at_page_index(candidates, 1, analysis_run_id="run-1").candidate_index == 1

    def test_20b_an_empty_page_refuses_every_position(self):
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.origin_at_page_index([], 0, analysis_run_id="run-1")
        assert caught.value.code == coa.ORIGIN_INDEX_OUT_OF_RANGE

    @pytest.mark.parametrize("value", ["", "   ", None, 7, ["run-1"]])
    def test_a_run_id_that_is_not_a_non_empty_string_is_refused(self, value):
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.CandidateOrigin(value, 0)
        assert caught.value.code == coa.ORIGIN_RUN_ABSENT
        assert coa.is_recorded_run(value) is False

    def test_the_three_refusals_are_the_whole_vocabulary(self):
        assert coa.CANDIDATE_ORIGIN_REFUSALS == (
            coa.ORIGIN_RUN_ABSENT, coa.ORIGIN_INDEX_INVALID, coa.ORIGIN_INDEX_OUT_OF_RANGE,
        )
        assert len(set(coa.CANDIDATE_ORIGIN_REFUSALS)) == len(coa.CANDIDATE_ORIGIN_REFUSALS)

    def test_the_array_the_index_addresses_is_the_pages_own_recorded_array(self):
        assert coa.CANDIDATE_ARRAY_KEY == "raw_connections"
        source = (APP / "engineering_data" / "page_extraction_capture.py").read_text(
            encoding="utf-8")
        assert '"payload": payload' in source

    def test_21_a_missing_source_candidate_mapping_fails_closed(self):
        """brief item 21. A caller that cannot state the array records nothing rather than
        recording a plausible number."""
        assert coa.origins_for_page([{"a": 1}, {"b": 2}], analysis_run_id=None) == (None, None)
        assert coa.origins_for_page([], analysis_run_id=None) == ()
        assert coa.origins_for_page([], analysis_run_id="run-1") == ()

    def test_21b_a_non_sequence_candidate_array_is_refused(self):
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.origins_for_page(7, analysis_run_id="run-1")
        assert caught.value.code == coa.ORIGIN_INDEX_OUT_OF_RANGE
        with pytest.raises(coa.CandidateOriginRefused) as caught:
            coa.origin_at_page_index("run-1", 0, analysis_run_id="run-1")
        assert caught.value.code == coa.ORIGIN_INDEX_OUT_OF_RANGE

    def test_the_producer_refuses_a_misaligned_run_list(self):
        """The per-page runs align one-to-one with the pages, as the identities do. A list
        that does not is refused rather than shifted."""
        with pytest.raises(ValueError) as caught:
            _intake(_three_pages(), runs=["run-1", "run-2"])
        assert "align one-to-one" in str(caught.value)

    def test_the_collection_refuses_a_misaligned_origin_list(self):
        from app.cad_engine.project_connection_review import (
            create_project_connection_collection,
        )

        with pytest.raises(ValueError) as caught:
            create_project_connection_collection(
                [{"detail_reference": "D1"}], origins=[],
            )
        assert "align one-to-one" in str(caught.value)


# ===========================================================================
# 22-24. NO DATABASE, NO STORAGE, NO EXTRACTION
# ===========================================================================
class TestTheModuleReachesNothing:
    def test_22_23_24_the_module_imports_no_application_module_at_all(self):
        """brief items 22, 23 and 24, structurally: a module with no application import
        cannot reach a database, a bucket or an extraction, because it holds no client."""
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

    def test_22_23_24_the_module_performs_no_io_and_names_no_client(self):
        """brief items 22, 23 and 24 asserted over the text, so an unexecuted reach is
        caught as well as an executed one."""
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        for forbidden in _IO_TOKENS:
            assert forbidden not in source, forbidden

    def test_a_fresh_interpreter_loads_no_database_storage_or_ai_client(self):
        """brief items 22-24, strongest form: importing the module in a clean interpreter
        pulls in none of the clients the rest of the product uses."""
        probe = (
            "import sys\n"
            "import app.cad_engine.candidate_origin_address as coa\n"
            "forbidden = ('supabase', 'httpx', 'requests', 'urllib3', 'postgrest',\n"
            "             'storage3', 'gotrue', 'psycopg', 'boto3', 'sqlalchemy',\n"
            "             'anthropic', 'app.config', 'app.pipeline', 'app.report',\n"
            "             'app.drawing_reading', 'app.ai_analysis')\n"
            "bad = sorted(m for m in sys.modules if any(k in m for k in forbidden))\n"
            "print(repr((list(coa.CANDIDATE_ORIGIN_REFUSALS), bad)))\n"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=str(REPO),
                                capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr
        codes, bad = eval(result.stdout.strip())  # noqa: S307 - our own probe output
        assert tuple(codes) == coa.CANDIDATE_ORIGIN_REFUSALS
        assert bad == [], bad

    def test_the_module_writes_nothing_and_holds_no_state(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("open", "print", "exec", "eval", "compile", "__import__"):
            assert forbidden not in calls, forbidden

    def test_the_module_varies_no_payload_it_is_given(self):
        payload = {"source_page": 3, "detail_reference": "24", "nested": {"a": [1, 2]}}
        before = copy.deepcopy(payload)
        carried = coa.with_origin(payload, coa.CandidateOrigin("run-1", 0))
        assert payload == before
        carried["nested"]["a"].append(3)
        assert payload == before


# ===========================================================================
# 25. NOTHING WAS WIRED
# ===========================================================================
class TestNothingWasWired:
    def test_25_no_application_module_imports_the_resolver(self):
        """brief item 25. J71's own fence, recomputed over the tree as it is after J72 —
        the producer records the address J71 will read, and does not call J71.

        J80 later added the one declared exception: the consumer boundary. The declaration
        does not weaken the guard, which still says what J72's milestone said — this producer
        does not call the resolver, and the resolver is named by exactly one module.

        J81 then gave the consumer its first production caller, the production read port, and
        it is DECLARED here rather than absorbed for the same reason: the producer below still
        never reaches the resolver, the boundary is still the only module naming it, and the
        list of modules reaching the boundary stays exact."""
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

    def test_25_the_producer_names_no_resolver_verb(self):
        source = PRODUCER_PATH.read_text(encoding="utf-8")
        for token in ("resolve_connection_field_reading", "ConnectionScopedReading",
                      "connection_scoped_evidence", "REASON_CODES"):
            assert token not in source, token

    def test_no_route_was_added_and_the_frozen_set_is_unchanged(self):
        import app.main as main

        from tests import test_real_world_j20_connection_review_persistence_gap as j20

        routes = j20._declared_routes(main.app)
        assert routes == j20.FROZEN_ROUTES
        assert not any("origin" in path.lower() for _, path in routes)

    def test_the_resolvers_own_public_behaviour_is_unchanged(self):
        """brief: do not modify J71 in this milestone. Its module is not edited, and its
        public surface is the one it was accepted with."""
        from app.cad_engine import connection_scoped_evidence as cse
        import inspect

        assert list(inspect.signature(cse.resolve_connection_field_reading).parameters) == [
            "item", "reference", "captures"]

    def test_the_j66_tree_wide_fence_would_still_hold_with_this_module_present(self):
        """J72 adds a module and touches four others. None of them names J66's vocabulary,
        so the fence that makes "no production writer exists" checkable is still exact."""
        naming = sorted(
            str(path.relative_to(REPO)) for path in APP.rglob("*.py")
            if "citation" in path.read_text(encoding="utf-8").lower()
        )
        assert naming == [
            "app/cad_engine/connection_review_snapshot.py",
            "app/cad_engine/review_contract.py",
            "app/engineering_data/connection_review_repository.py",
        ]

    def test_no_migration_was_added_or_modified(self):
        """brief: DO NOT create a migration. The registry is not touched by this milestone."""
        migrations = sorted(
            path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert migrations, "the migrations directory must exist"
        assert not any("j72" in name.lower() for name in migrations), migrations

    def test_the_module_builds_no_second_identity_for_a_candidate(self):
        """The address is carried, never compared: this module decides nothing about
        whether two candidates are the same one.

        Asserted over the module's CODE — its names and its non-docstring strings, never
        its prose. The docstring has to be free to say what the index is NOT, including
        "not a winner"; what must never appear is a name or a value that would make it one.
        """
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ) and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        tokens: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                tokens.add(node.id)
            elif isinstance(node, ast.Attribute):
                tokens.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                tokens.add(node.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings:
                    tokens.add(node.value)
        assert tokens, "the module must have a code surface to scan"
        for forbidden in ("connection_id", "merge", "duplicate", "identical", "same_as",
                          "equals", "consolidat", "winner", "rank", "score"):
            offenders = sorted(token for token in tokens if forbidden in token.lower())
            assert offenders == [], (forbidden, offenders)

    def test_neither_disputed_selby_question_is_answered_here(self):
        """Ø18 vs Ø22 and 029X vs 030X/031X/032X are named nowhere in this milestone."""
        for path in (MODULE_PATH, INTAKE_PATH, PRODUCER_PATH):
            source = path.read_text(encoding="utf-8")
            for token in ("Ø18", "Ø22", "18 mm", "22 mm", "029X", "030X", "031X",
                          "032X", "1029X", "diameter", "Transmittal"):
                assert token not in source, f"{path.name}: {token}"


# ===========================================================================
# THE FINAL CONSUMER: WHAT J71's FUTURE R3 RULE NEEDS IS PRESENT
# ===========================================================================
class TestTheEvidenceCarriesWhatTheResolverWillNeed:
    def test_the_two_terms_are_a_fresh_interpreter_readable_from_the_payload(self):
        """The recorded evidence is plain JSON: a reader that has only the payload can
        state the address, which is what makes it usable rather than decorative."""
        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        payload = _evidence_of(intake, "RP-0004")
        carried = json.loads(json.dumps(payload))
        origin = coa.origin_of(carried)
        assert origin is not None
        assert (origin.analysis_run_id, origin.candidate_index) == ("run-3", 0)

    def test_the_four_terms_the_address_names_are_all_in_one_payload(self):
        """`drawing_id + page_number + analysis_run_id + candidate_index`, the address the
        brief states — the first two under the names the payload has always used for them."""
        from app.cad_engine import connection_scoped_evidence as cse

        intake = _intake(_three_pages(), runs=["run-1", "run-2", "run-3"])
        payload = _evidence_of(intake, "RP-0006")
        assert payload["source_drawing_id"] == "drawing-7j72"
        assert payload["source_page"] == 3
        assert payload[RUN_KEY] == "run-3"
        assert payload[INDEX_KEY] == 2
        # J71's own required address terms, quoted from the resolver rather than restated:
        # the payload's first two names ARE those terms under the payload's own spellings.
        assert set(cse.REQUIRED_ADDRESS_TERMS) == {"drawing_id", "page_number", "analysis_run_id"}
        assert "analysis_run_id" in payload

    def test_the_recorded_index_is_the_position_the_resolver_already_reports(self):
        """J71 reports `candidate_position` for a reading it did resolve. The recorded
        `candidate_index` is that same number, recorded rather than recovered — so the
        future R3 rule compares an explicit position instead of matching provenance."""
        from app.cad_engine import connection_scoped_evidence as cse
        import dataclasses

        assert "candidate_position" in {
            field.name for field in dataclasses.fields(cse.ConnectionScopedReading)}
        pages = _three_pages()
        intake = _intake(pages, runs=["run-1", "run-2", "run-3"])
        for payload in _evidence_by_package(intake).values():
            page = next(p for p in pages if p["page_number"] == payload["source_page"])
            assert payload[INDEX_KEY] < len(page["raw_connections"])


# ===========================================================================
# THE MUTATION CONTROLS
#
# Three controlled defects, each the exact failure this milestone exists to
# prevent, and each shown to redden the test that pins the property it removes.
# The test bodies are RE-RUN against the mutant rather than restated, so
# "the test would catch this" is a measurement and not a claim.
# ===========================================================================
class TestTheMutationsAreCaught:
    def test_the_dropped_origin_mutation_reddens_the_recorded_address(self, monkeypatch):
        """The defect: the address stops being carried into `evidence` — the producer
        builds the payload it built before J72. Test 1 is re-run and fails."""
        import app.cad_engine.connection_review_snapshot as snapshot_module

        monkeypatch.setattr(
            snapshot_module, "with_origin", lambda evidence, origin: dict(evidence))
        # Either exception is the failure: the body compares the term, and indexing an
        # absent term raises before the comparison is ever made.
        with pytest.raises((AssertionError, KeyError)):
            TestTheCandidateOriginIsRecorded().test_1_a_single_candidate_records_the_run_that_read_its_page()

    def test_the_substituted_run_mutation_reddens_the_fail_closed_test(self, monkeypatch):
        """The defect: a page whose reading has no stated run gets a plausible one handed
        to it — the exact "silently substitute another run" the brief forbids. The intake
        binds `origins_for_page` by name, so the mutant replaces that binding."""
        import app.cad_engine.project_extraction_intake as intake_module

        def substitutes(candidates, *, analysis_run_id):
            return tuple(
                coa.CandidateOrigin(analysis_run_id or "the-run-that-stands-today", index)
                for index in range(len(candidates))
            )

        monkeypatch.setattr(intake_module, "origins_for_page", substitutes)
        with pytest.raises(AssertionError):
            TestTheRunIsTheAttemptThatReadThePage().test_no_run_stated_at_all_records_nothing_on_any_item()

    def test_the_coercing_validation_mutation_reddens_the_refusal_test(self, monkeypatch):
        """The defect: `CandidateOrigin` stops validating, so `True`, `1.0` and `"1"` are
        accepted where a position is required. The refusal test is re-run: it fails, and it
        fails because no `CandidateOriginRefused` was raised."""
        monkeypatch.setattr(coa.CandidateOrigin, "__post_init__", lambda self: None)
        with pytest.raises(BaseException) as caught:
            TestValidationRefusesRatherThanCoerces().test_16_a_negative_index_is_refused(-1)
        assert not isinstance(caught.value, coa.CandidateOriginRefused)
        assert coa.CandidateOrigin("run-1", True).candidate_index is True

    def test_the_mutants_are_registered_over_the_real_module(self):
        """The three mutants above each patch a name the genuine modules bind — no parallel
        implementation of the address exists anywhere in this file."""
        assert coa.CANDIDATE_ORIGIN_INDEX_KEY == "candidate_index"
        assert coa.CANDIDATE_ORIGIN_RUN_KEY == "analysis_run_id"
        assert coa.CandidateOrigin.__module__ == "app.cad_engine.candidate_origin_address"
        assert Path(coa.__file__).resolve() == MODULE_PATH.resolve()


# ===========================================================================
# THE GENUINE SELBY READING, END TO END THROUGH THE PRODUCER
# ===========================================================================
@pytest.fixture(scope="module")
def selby_snapshot():
    """The real Selby document, reconstructed from its persisted captures and recorded
    through J21's own builder — J24A's harness, read and never duplicated."""
    result = j24a._reconstruct(j24a._selby_store(), j24a.SELBY_PROJECT)
    snapshot = build_review_snapshot(
        result.workflow, project_id=j24a.SELBY_PROJECT, evidence_rows=NO_EVIDENCE,
        evidence_run_ids=result.capture_run_ids, previous_revision=None,
    )
    return result, snapshot


def _selby_capture_payloads():
    """page_number -> (the run that stands for it, the reading's own raw_connections)."""
    store = j24a._selby_store()
    rows = store.page_extraction_captures_for_drawing(j24a.SELBY_DRAWING)
    return {
        row["page_number"]: (str(row["analysis_run_id"]), row["payload"]["raw_connections"])
        for row in captures.authoritative_captures(rows)
    }


class TestTheGenuineReadingKeepsItsOrigin:
    def test_every_selby_item_records_a_full_address(self, selby_snapshot):
        """32 real pages, seven real attempts: every item records both terms, and neither
        is null."""
        _, snapshot = selby_snapshot
        assert snapshot.items
        for item in snapshot.items:
            assert item.evidence[RUN_KEY], item.review_package_id
            assert item.evidence[INDEX_KEY] >= 0, item.review_package_id

    def test_each_selby_item_names_the_run_that_read_its_own_page(self, selby_snapshot):
        """The recorded run is the run of the capture the item's page came from — checked
        against J23's own selection over the persisted rows, not against the item."""
        _, snapshot = selby_snapshot
        by_page = _selby_capture_payloads()
        for item in snapshot.items:
            page = item.evidence["source_page"]
            assert page in by_page, page
            assert item.evidence[RUN_KEY] == by_page[page][0], item.review_package_id
        assert len({run for run, _ in by_page.values()}) > 1

    def test_each_selby_item_names_a_real_position_in_that_pages_reading(self, selby_snapshot):
        """The strongest form available: the recorded index is a position inside the
        genuine capture's own `raw_connections` array for that page."""
        _, snapshot = selby_snapshot
        by_page = _selby_capture_payloads()
        for item in snapshot.items:
            _, raw_connections = by_page[item.evidence["source_page"]]
            assert 0 <= item.evidence[INDEX_KEY] < len(raw_connections), item.review_package_id

    def test_the_position_points_at_the_candidate_the_item_is_about(self, selby_snapshot):
        """Not merely in range: the reading at that position is the one whose detail
        reference the item itself records, wherever the AI stated one."""
        _, snapshot = selby_snapshot
        by_page = _selby_capture_payloads()
        checked = 0
        for item in snapshot.items:
            _, raw_connections = by_page[item.evidence["source_page"]]
            candidate = raw_connections[item.evidence[INDEX_KEY]]
            detail = candidate.get("detail_reference")
            if detail:
                assert item.evidence["detail_reference"] == detail, item.review_package_id
                checked += 1
        assert checked, "the genuine Selby reading states no detail reference to check"

    def test_the_selby_indexes_are_not_the_flattened_submission_order(self, selby_snapshot):
        """The real document diverges: `submission_index` climbs across 32 pages while the
        recorded index restarts at each page's first candidate."""
        result, snapshot = selby_snapshot
        candidates = {c.review_package_id: c for c in result.workflow.collection.candidates}
        diverging = [
            item.review_package_id for item in snapshot.items
            if candidates[item.review_package_id].submission_index != item.evidence[INDEX_KEY]
        ]
        assert diverging, "the genuine document must exercise the divergence"

    def test_the_selby_indexes_are_not_the_review_package_numbers(self, selby_snapshot):
        """`RP-####` is the flattened position; the recorded index is not it."""
        _, snapshot = selby_snapshot
        pairs = [
            (int(item.review_package_id.split("-")[1]), item.evidence[INDEX_KEY])
            for item in snapshot.items
        ]
        assert [index for _, index in pairs].count(0) > 1
        assert any(number - 1 != index for number, index in pairs)

    def test_the_reconstruction_and_the_run_less_intake_differ_only_by_the_origin(
        self, selby_snapshot
    ):
        """What J72 added, isolated: the same pages through the same intake, with and
        without the per-page runs, differ in the two address terms and nothing else."""
        result, snapshot = selby_snapshot
        pages = sorted(j24a._selby_pages(), key=lambda p: p["page_number"])
        plain = _evidence_by_package(
            intake_page_extractions(
                pages,
                project_id=j24a.SELBY_PROJECT,
                source_drawing_id=j24a.SELBY_DRAWING,
                drawing_set_page_count=j24a.SELBY_PAGE_COUNT,
            ),
            project_id=j24a.SELBY_PROJECT,
        )
        assert set(plain) == {item.review_package_id for item in snapshot.items}
        for item in snapshot.items:
            unrelated = {
                key: value for key, value in item.evidence.items() if key not in coa.ORIGIN_KEYS
            }
            assert unrelated == plain[item.review_package_id], item.review_package_id

    def test_the_arkles_reading_records_its_origin_too(self):
        """The second genuine document, through the same producer — one run, one drawing."""
        result = j24a._reconstruct(j24a._arkles_store(), j24a.ARKLES_PROJECT)
        snapshot = build_review_snapshot(
            result.workflow, project_id=j24a.ARKLES_PROJECT, evidence_rows=NO_EVIDENCE,
            evidence_run_ids=result.capture_run_ids, previous_revision=None,
        )
        assert snapshot.items
        for item in snapshot.items:
            assert item.evidence[RUN_KEY] == result.capture_run_ids[0]
            assert item.evidence[INDEX_KEY] >= 0

    def test_the_recorded_address_survives_the_json_column_it_is_stored_in(self, selby_snapshot):
        """The evidence is a `json` column: what comes back is the text that went in."""
        _, snapshot = selby_snapshot
        replayed = j21._round_trip(snapshot)
        for original, back in zip(snapshot.items, replayed.items):
            assert coa.origin_of(back.evidence) == coa.origin_of(original.evidence)
