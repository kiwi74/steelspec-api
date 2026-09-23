"""
Milestone B — PAGE-26 PARSE_FAILED RECOVERY PROOF.

The real Selby Square page 26 was parse-failed in the genuine captures;
the reviewer's genuine re-capture of that page succeeded (pages: 1,
parse_failed: 0, members: 1, connections: 1, connections carrying
material: NONE). This file proves the recovery was merged into the
26-30 slice transparently, through the unchanged capture machinery:

  * the recovery file is kept as verbatim evidence (pinned digest) and
    holds exactly one valid-schema page-26 entry;
  * the merged 26-30 slice carries exactly that entry as its page 26;
  * pages 27-30 still hash to their pre-merge values — byte-unchanged;
  * the genuine accumulated chain now reads 32 analysed / 0 parse_failed;
  * page 26's material remains absent — nothing is invented;
  * the genuine parse-failure behaviour is intact on synthetic pages.

Nothing here modifies any product module, any existing fixture or any
other test file; every assertion is pinned to the genuine captured
data, never derived by hand.
"""

import hashlib
import json

import pytest

from app.cad_engine import incremental_continuation as ic
from tests.test_real_world_incremental_analysis import _synthetic_page
from tests.test_real_world_multi_member_connection import SELBY_PAGE_COUNT
from tests.test_real_world_review_intake_provenance import (
    DATA,
    PINNED_SHA256,
    _accumulated,
    _intake,
    _page,
    needs_all_captures,
)

RECOVERY_FILE = DATA / "selby_square_page_26_recovery.json"
MERGED_FILE = DATA / "selby_square_pages_26_30_extractions.json"

# The recovery file is verbatim evidence of the genuine retry — pinned,
# never regenerated.
RECOVERY_FILE_SHA256 = (
    "cba3fc60b3caba4f643a071adf14e2ad0c6dfab57c347e008baaef7693788360")

# sha256 of json.dumps(entry): the pre-merge digests of the four
# untouched pages of the 26-30 slice, computed before the merge.
PRE_MERGE_ENTRY_SHA256 = {
    27: "0f069a546f6a4762f1e87f068aebbd63afbd17dcdbfd7a502bf0bb5acdb3ef36",
    28: "757aa9102e5502c88dc21aae232fd7c41004ba60dbd0c0dd0cc98f2c61df0295",
    29: "b12c68cee62dd6b98c52323eff1738cdfe4de4ef78d43767b6c9fce2b6bb54e8",
    30: "4aeff87848e35b766e83b13a18fba194992b2b1d0b393a90dbe503fb527ee77f",
}

needs_recovery_capture = pytest.mark.skipif(
    not RECOVERY_FILE.exists(),
    reason="the genuine page-26 re-capture is required")


def _entry_digest(entry):
    return hashlib.sha256(json.dumps(entry).encode()).hexdigest()


def _merged_entries():
    return json.loads(MERGED_FILE.read_text())


def _recovery_entries():
    return json.loads(RECOVERY_FILE.read_text())


def _merged_page(number):
    return next(p for p in _merged_entries() if p["page_number"] == number)


# =============================================================================
# the recovery file: verbatim, exactly one valid-schema page-26 entry
# =============================================================================

@needs_all_captures
@needs_recovery_capture
class TestRecoveryFileIsVerbatimEvidence:

    def test_file_digest_is_pinned(self):
        assert hashlib.sha256(
            RECOVERY_FILE.read_bytes()).hexdigest() == RECOVERY_FILE_SHA256

    def test_exactly_one_page_26_entry(self):
        entries = _recovery_entries()
        assert len(entries) == 1
        assert entries[0]["page_number"] == 26
        assert entries[0]["parse_failed"] is False

    def test_entry_passes_the_genuine_slice_validation(self):
        # The recovery entry satisfies the unchanged capture machinery:
        # it runs through begin_incremental_analysis like any genuine slice.
        state = ic.begin_incremental_analysis(
            _recovery_entries(),
            drawing_set_first_page=1, drawing_set_page_count=SELBY_PAGE_COUNT)
        assert 26 in state.analysed_page_numbers
        assert 26 not in state.parse_failed_page_numbers

    def test_entry_carries_exactly_the_retry_results(self):
        entry = _recovery_entries()[0]
        assert entry["drawing_number"] == "026"
        assert entry["drawing_title"] == "Urban Fabrication"
        assert entry["revision"] == "A"
        assert entry["raw_members"] == [{
            "mark": "026", "member_type": "beam", "section": "200UB25",
            "quantity": 1, "length_mm": 3699, "grid_reference": None,
            "detail_reference": None, "confidence": 97,
        }]
        assert entry["raw_connections"] == [{
            "detail_reference": None, "grid_reference": None,
            "connects_members": ["026"], "connection_type": "welded/bolted",
            "bolts": [], "plates": [],
            "welds": [{"type": "fillet", "size_mm": 6}],
            "material": None, "confidence": 82,
        }]

    def test_retry_captured_the_page_image(self):
        image = (RECOVERY_FILE.parent / "selby_square_page_26_recovery_page_images"
                 / "page-26.png")
        assert image.exists()
        assert image.stat().st_size > 0


# =============================================================================
# the merge: only page 26 replaced, everything else byte-unchanged
# =============================================================================

@needs_all_captures
@needs_recovery_capture
class TestTheMergeIsTransparent:

    def test_merged_page_26_is_exactly_the_recovery_entry(self):
        merged = _merged_page(26)
        recovered = _recovery_entries()[0]
        assert merged == recovered
        assert _entry_digest(merged) == _entry_digest(recovered)

    def test_only_page_26_was_replaced(self):
        entries = _merged_entries()
        assert len(entries) == 5
        assert [p["page_number"] for p in entries] == [26, 27, 28, 29, 30]
        # pages 27-30 byte-identical to their pre-merge selves
        for number, digest in PRE_MERGE_ENTRY_SHA256.items():
            assert _entry_digest(_merged_page(number)) == digest, number

    def test_no_page_in_the_merged_slice_declares_parse_failed(self):
        for page in _merged_entries():
            assert page["parse_failed"] is False, page["page_number"]

    def test_merged_file_digest_matches_the_pinned_capture(self):
        # the merged file is the new pinned evidence for the 26-30 slice
        assert hashlib.sha256(
            MERGED_FILE.read_bytes()).hexdigest() == PINNED_SHA256[MERGED_FILE.name]


# =============================================================================
# the genuine chain after recovery: 32 analysed, 0 parse_failed, no invention
# =============================================================================

@needs_all_captures
class TestGenuineChainAfterRecovery:

    def test_accumulated_evidence_is_32_analysed_0_parse_failed(self):
        state = _accumulated()
        assert len(state.analysed_page_numbers) == 32
        assert state.parse_failed_page_numbers == ()
        assert state.not_analysed_page_numbers == ()
        assert 26 in state.analysed_page_numbers
        assert _page(state, 26)["parse_failed"] is False

    def test_page_26_enters_the_intake_as_one_ordinary_candidate(self):
        state = _accumulated()
        intake = _intake(state)
        candidates = intake.collection.candidates
        assert len(candidates) == 52
        page_26 = [c for c in candidates
                   if c.package.extraction.source_page == 26]
        assert len(page_26) == 1
        # submission order: the 41st entry, ids contiguous from RP-0001
        assert candidates[40] is page_26[0]
        assert page_26[0].review_package_id == "RP-0041"

    def test_page_26_material_remains_absent(self):
        state = _accumulated()
        intake = _intake(state)
        candidates = [c for c in intake.collection.candidates
                      if c.package.extraction.source_page == 26]
        assert len(candidates) == 1
        # the retry reported no material, so none is recorded — nothing
        # is invented or promoted
        assert candidates[0].package.extraction.material is None


# =============================================================================
# parse-failure behaviour: unchanged, still fails closed on synthetic pages
# =============================================================================

@needs_all_captures
class TestParseFailureBehaviourIsIntact:

    def test_synthetic_parse_failed_page_still_accounts_as_parse_failed(self):
        state = ic.begin_incremental_analysis(
            [_synthetic_page(1, parse_failed=True), _synthetic_page(2)],
            drawing_set_first_page=1, drawing_set_page_count=SELBY_PAGE_COUNT)
        assert state.parse_failed_page_numbers == (1,)
        assert state.analysed_page_numbers == (2,)

    def test_contradictory_status_is_still_refused(self):
        with pytest.raises(ValueError, match="contradictory page status"):
            ic.begin_incremental_analysis(
                [_synthetic_page(
                    1, parse_failed=True,
                    raw_connections=[{"detail_reference": "INVENTED"}]),
                 _synthetic_page(2)],
                drawing_set_first_page=1, drawing_set_page_count=SELBY_PAGE_COUNT)

    def test_no_real_page_carries_a_parse_failed_flag(self):
        state = _accumulated()
        for page in state.pages:
            assert page["parse_failed"] is False, page["page_number"]
