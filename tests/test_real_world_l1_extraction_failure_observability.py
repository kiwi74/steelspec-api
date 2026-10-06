"""
L1 — EXTRACTION FAILURE OBSERVABILITY.

WHAT THIS FILE IS

The regression proof for the change E2E-002L argued for. L established that a page whose
vision response cannot be parsed was recorded as `parse_failed=True` with the response
itself discarded — and that this made the ONE question worth asking about such a page
unanswerable afterwards:

    did the model answer badly, or did the answer not fit inside `max_tokens`?

Those two have the same stored shape and completely different fixes, and the record
retained nothing that could separate them. This file proves that the failure branch now
keeps exactly two facts — the provider's own `stop_reason` and a bounded prefix of the
response — and that keeping them changed nothing else.

WHAT IT DOES NOT CLAIM

`stop_reason` is RECORDED, never interpreted and never assumed. No assertion here says a
failure's stop reason is `max_tokens`; the tests drive several values, including ones no
provider would send, and assert only that whatever was said is what was kept. A test that
pinned `max_tokens` would manufacture the very finding L could not establish.

The safety contract is asserted in the same file rather than assumed: a failed page is
still `parse_failed`, still carries no members, no connections and no engineering data,
and is still outranked by a page that parsed.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      `analyze_pdf_pages`'s own failure branch, the `PageExtraction` it builds,
              the payload `capture_rows` derives from it, and the selection rule that
              reads it back.

    DOUBLED   the provider (a stand-in whose response and stop reason the test states),
              page rendering, and the page-image upload. No network, no credentials, no
              poppler, no database.
"""
from __future__ import annotations

import os
import pathlib

import pytest

# `app/config.py` reads `os.environ[...]` at IMPORT time, so a placeholder configuration
# must exist for the analyzer to be importable at all. It is supplied only when absent —
# a real environment is never clobbered — and it is deliberately LEFT IN PLACE rather
# than restored afterwards. Other modules in this suite read configuration at REQUEST
# time, not merely at import, so removing it again here would break them depending on the
# order the files happen to be collected in. This file therefore leaves the environment
# as it found it or better, and never emptier.
#
# Nothing below is ever dialled: the provider, the renderer and the page-image upload are
# all doubled, and no call leaves the process.
os.environ.setdefault("SUPABASE_URL", "https://l1-observability.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l1-test-placeholder-not-a-credential")

from app.ai_analysis import pdf_vision_analyzer as analyzer  # noqa: E402
from app.engineering_data import page_extraction_capture as captures  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
ANALYZER_PATH = REPO / "app" / "ai_analysis" / "pdf_vision_analyzer.py"

MODEL = "claude-sonnet-4-6"

# Two shapes a real failure takes. Neither is invented: L's live captures are records of
# exactly this, and the two are indistinguishable in the stored payload before this change.
TRUNCATED = '{"drawing_number": "S101", "drawing_title": "Steel Details", "members": [{"mark": "B1", "section": "310UB40"'
PROSE = "I'm sorry, but I can't analyse this drawing — the page appears to be blank."

GOOD_JSON = (
    '{"drawing_number": "S101", "drawing_title": "Steel Details", "revision": "B",'
    ' "members": [{"mark": "B1", "section": "310UB40"}],'
    ' "connections": [{"members": ["B1", "C1"], "type": "bolted"}]}'
)


# ======================================================================================
# The doubled provider.
# ======================================================================================
class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    """One provider response, with the stop reason the test states."""

    def __init__(self, text: str, stop_reason):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _Client:
    def __init__(self, response: _Response) -> None:
        self.messages = _Messages(response)


def _drive(monkeypatch, text: str, stop_reason, *, pages: int = 1):
    """Runs the REAL `analyze_pdf_pages` against one stated provider response.

    Returns `(pages_returned, messages)` so a caller can assert both what the analyzer
    produced and what it actually asked for.
    """
    response = _Response(text, stop_reason)

    # `analyze_pdf_pages` constructs `Anthropic(...)` itself, so the stand-in is the
    # class, not an instance: one factory that answers with this response and records
    # what it was asked.
    monkeypatch.setattr(analyzer, "ANTHROPIC_API_KEY", "test-key-not-a-credential")
    monkeypatch.setattr(analyzer, "Anthropic", lambda **kwargs: _Client(response))
    monkeypatch.setattr(analyzer, "render_pages_to_png",
                        lambda filepath, max_pages, first_page=1: [b"png"] * pages)
    monkeypatch.setattr(analyzer, "upload_page_image",
                        lambda *a, **k: "stored/nowhere.png")

    returned = analyzer.analyze_pdf_pages(
        "not-read-because-rendering-is-doubled", "user-1", "project-1", "drawing-1", 10,
    )
    return returned, response


def _capture_row(page):
    """The payload the page becomes in `page_extraction_captures` — the real path."""
    rows = captures.capture_rows(
        [page], analysis_run_id="run-1", drawing_id="drawing-1",
        drawing_set_id="set-1", project_id="project-1", model=MODEL,
    )
    assert len(rows) == 1
    return rows[0]


# ======================================================================================
# A. The failure is still a failure.
# ======================================================================================
class TestTheFailureIsStillAFailure:
    @pytest.mark.parametrize("text", [TRUNCATED, PROSE])
    def test_a_response_that_cannot_be_parsed_is_recorded_as_a_parse_failure(
        self, monkeypatch, text
    ):
        pages, _ = _drive(monkeypatch, text, "max_tokens")
        assert len(pages) == 1
        assert pages[0].parse_failed is True
        assert pages[0].page_number == 1

    @pytest.mark.parametrize("text", [TRUNCATED, PROSE])
    def test_a_failed_page_carries_no_members_and_no_connections(self, monkeypatch, text):
        """The substitution this layer must never make, asserted at the source."""
        pages, _ = _drive(monkeypatch, text, "end_turn")
        assert pages[0].raw_members == []
        assert pages[0].raw_connections == []

    @pytest.mark.parametrize("text", [TRUNCATED, PROSE])
    def test_a_failed_page_states_no_drawing_metadata(self, monkeypatch, text):
        """Nothing is salvaged from an unparseable response — not even a readable prefix."""
        pages, _ = _drive(monkeypatch, text, None)
        assert pages[0].drawing_number is None
        assert pages[0].drawing_title is None
        assert pages[0].revision is None


# ======================================================================================
# B. The two facts that were being discarded are now kept.
# ======================================================================================
class TestTheFailureIsDiagnosable:
    def test_the_failure_payload_carries_the_providers_stop_reason(self, monkeypatch):
        pages, _ = _drive(monkeypatch, TRUNCATED, "max_tokens")
        row = _capture_row(pages[0])
        assert row["payload"]["stop_reason"] == "max_tokens"

    def test_the_stop_reason_is_whatever_the_provider_said_and_never_assumed(
        self, monkeypatch
    ):
        """The value is RECORDED, not predicted.

        Four different reasons — including an empty one and one no provider sends — and
        every one is stored as itself. Nothing here asserts that a failure means
        `max_tokens`: that is the finding L could not reach, and a test must not invent
        it on the record's behalf.
        """
        for reason in ("max_tokens", "end_turn", "stop_sequence", "something-new"):
            pages, _ = _drive(monkeypatch, TRUNCATED, reason)
            assert _capture_row(pages[0])["payload"]["stop_reason"] == reason

    def test_an_absent_stop_reason_is_stored_as_absent_not_as_a_null(self, monkeypatch):
        """A provider that says nothing is not reported as having said something.

        The key is ABSENT rather than present-and-null, which is this module's rule
        everywhere: absence is recorded as absence, so a null can never be read as a
        value somebody measured.
        """
        for nothing in (None, ""):
            pages, _ = _drive(monkeypatch, TRUNCATED, nothing)
            payload = _capture_row(pages[0])["payload"]
            assert "stop_reason" not in payload
            # The response itself was still recorded — only the reason was silent.
            assert payload["raw_response_excerpt"] == TRUNCATED

    def test_an_empty_response_is_a_finding_and_is_not_dropped(self, monkeypatch):
        """`""` is a recorded fact, not an absent one: the model answered nothing.

        Distinct from "nothing was recorded", which is what the key's absence means —
        which is exactly why an empty string is kept while a null is not.
        """
        pages, _ = _drive(monkeypatch, "", "end_turn")
        assert pages[0].parse_failed is True
        payload = _capture_row(pages[0])["payload"]
        assert payload["raw_response_excerpt"] == ""
        assert payload["raw_members"] == []
        assert payload["raw_connections"] == []

    def test_the_failure_payload_carries_a_prefix_of_the_response(self, monkeypatch):
        pages, _ = _drive(monkeypatch, TRUNCATED, "max_tokens")
        excerpt = _capture_row(pages[0])["payload"]["raw_response_excerpt"]
        assert isinstance(excerpt, str)
        assert excerpt
        assert TRUNCATED.startswith(excerpt)

    def test_the_excerpt_shows_how_the_json_broke_not_merely_that_it_did(self, monkeypatch):
        """The point of keeping a prefix: a truncated tail looks different from prose."""
        truncated, _ = _drive(monkeypatch, TRUNCATED, "max_tokens")
        prose, _ = _drive(monkeypatch, PROSE, "end_turn")
        a = _capture_row(truncated[0])["payload"]["raw_response_excerpt"]
        b = _capture_row(prose[0])["payload"]["raw_response_excerpt"]
        assert a.startswith('{"drawing_number"')
        assert not b.startswith("{")
        assert a != b


# ======================================================================================
# C. The excerpt is bounded — the whole response is never stored.
# ======================================================================================
class TestTheExcerptIsBounded:
    def test_the_bound_is_a_positive_whole_number_of_characters(self):
        assert isinstance(analyzer.DIAGNOSTIC_EXCERPT_CHARS, int)
        assert not isinstance(analyzer.DIAGNOSTIC_EXCERPT_CHARS, bool)
        assert analyzer.DIAGNOSTIC_EXCERPT_CHARS > 0

    def test_an_over_long_response_is_cut_to_exactly_the_bound(self, monkeypatch):
        """The bound is enforced, not merely intended."""
        huge = "x" * (analyzer.DIAGNOSTIC_EXCERPT_CHARS * 40)
        pages, _ = _drive(monkeypatch, huge, "max_tokens")
        excerpt = _capture_row(pages[0])["payload"]["raw_response_excerpt"]
        assert len(excerpt) == analyzer.DIAGNOSTIC_EXCERPT_CHARS

    def test_the_whole_response_is_never_persisted(self, monkeypatch):
        """A response far larger than the bound never reaches the payload whole."""
        huge = "y" * (analyzer.DIAGNOSTIC_EXCERPT_CHARS * 40)
        pages, _ = _drive(monkeypatch, huge, "max_tokens")
        row = _capture_row(pages[0])
        assert huge not in str(row["payload"])
        assert len(row["payload"]["raw_response_excerpt"]) < len(huge)

    def test_a_short_response_is_kept_whole_rather_than_padded(self, monkeypatch):
        pages, _ = _drive(monkeypatch, PROSE, "end_turn")
        assert _capture_row(pages[0])["payload"]["raw_response_excerpt"] == PROSE


# ======================================================================================
# D. Nothing about a successful extraction changed.
# ======================================================================================
class TestASuccessfulExtractionIsUnchanged:
    def test_a_parsed_response_is_still_parsed_into_a_reading(self, monkeypatch):
        pages, _ = _drive(monkeypatch, GOOD_JSON, "end_turn")
        page = pages[0]
        assert page.parse_failed is False
        assert page.drawing_number == "S101"
        assert page.drawing_title == "Steel Details"
        assert page.revision == "B"
        assert page.raw_members == [{"mark": "B1", "section": "310UB40"}]
        assert page.raw_connections == [{"members": ["B1", "C1"], "type": "bolted"}]

    def test_a_successful_page_records_no_failure_diagnostics(self, monkeypatch):
        """The FAILURE diagnostics belong to a failure; a success states neither.

        Asserted as key ABSENCE, which is the strong form: a page that parsed stores no
        `stop_reason` and no excerpt, so nothing about a failure leaks into a reading.

        L12 adds one key to a successful payload — `response_shape`, which describes what
        surrounded the object rather than the object — and the exact-key pin below was
        widened by that one name and nothing else. The claim this test makes is unchanged:
        the two failure diagnostics are absent from a success.
        """
        pages, _ = _drive(monkeypatch, GOOD_JSON, "max_tokens")
        page = pages[0]
        assert page.parse_failed is False
        assert page.stop_reason is None
        assert page.raw_response_excerpt is None

        payload = _capture_row(page)["payload"]
        assert "stop_reason" not in payload
        assert "raw_response_excerpt" not in payload
        assert sorted(payload) == [
            "drawing_number", "drawing_title", "page_number", "parse_failed",
            "raw_connections", "raw_members", "response_shape", "revision",
        ]

    def test_the_reading_a_successful_page_stores_is_still_the_models_own(self, monkeypatch):
        pages, _ = _drive(monkeypatch, GOOD_JSON, "end_turn")
        payload = _capture_row(pages[0])["payload"]
        for key in ("page_number", "drawing_number", "drawing_title", "revision",
                    "raw_members", "raw_connections", "parse_failed"):
            assert key in payload
        assert payload["raw_members"] == [{"mark": "B1", "section": "310UB40"}]
        assert payload["raw_connections"] == [{"members": ["B1", "C1"], "type": "bolted"}]

    def test_a_code_fenced_response_is_still_parsed(self, monkeypatch):
        """The defensive fence strip is untouched by this change."""
        pages, _ = _drive(monkeypatch, f"```json\n{GOOD_JSON}\n```", "end_turn")
        assert pages[0].parse_failed is False
        assert pages[0].drawing_number == "S101"


# ======================================================================================
# E. The safety contract around a failure is unchanged.
# ======================================================================================
class TestTheSafetyContractIsUnchanged:
    def test_the_capture_still_records_the_page_as_failed(self, monkeypatch):
        pages, _ = _drive(monkeypatch, TRUNCATED, "max_tokens")
        row = _capture_row(pages[0])
        assert row["parse_failed"] is True
        assert row["payload"]["parse_failed"] is True

    def test_the_excerpt_is_not_engineering_data(self, monkeypatch):
        """A prefix is diagnosis. It never becomes a member, a connection or a mark."""
        pages, _ = _drive(monkeypatch, GOOD_JSON[:-40], "max_tokens")
        row = _capture_row(pages[0])
        assert row["parse_failed"] is True
        assert row["payload"]["raw_members"] == []
        assert row["payload"]["raw_connections"] == []
        # The half-written members array is visible in the excerpt and nowhere else.
        assert '"mark": "B1"' in row["payload"]["raw_response_excerpt"]

    def test_a_page_that_parsed_still_outranks_one_that_did_not(self, monkeypatch):
        """The selection rule is untouched: a failure never displaces a real reading."""
        failed, _ = _drive(monkeypatch, TRUNCATED, "max_tokens")
        parsed, _ = _drive(monkeypatch, GOOD_JSON, "end_turn")

        rows = captures.capture_rows(
            [failed[0], parsed[0]], analysis_run_id="run-1", drawing_id="drawing-1",
            drawing_set_id="set-1", project_id="project-1", model=MODEL,
        )
        # `capture_rows` deliberately leaves `captured_at` to the database, so a row
        # being SELECTED carries the instant the read-back supplied. Stated here to set
        # up the adversarial ordering: both readings are page 1, and the parse-failed
        # one is the LATER attempt — so a rule that ranked by recency would pick it.
        rows[0]["captured_at"] = "2026-10-05T21:11:00+00:00"   # the reading that parsed
        rows[1]["captured_at"] = "2026-10-05T21:12:00+00:00"   # the failure, recorded later

        chosen = captures.authoritative_captures(list(reversed(rows)))
        assert len(chosen) == 1
        assert chosen[0]["parse_failed"] is False
        assert chosen[0]["payload"]["raw_members"] == [{"mark": "B1", "section": "310UB40"}]

    def test_a_failed_page_still_yields_no_connection_evidence(self, monkeypatch):
        """The intake reads a failure as a page that could not be read, diagnostics and all."""
        from app.cad_engine import project_extraction_intake as intake

        pages, _ = _drive(monkeypatch, TRUNCATED, "max_tokens")
        payload = _capture_row(pages[0])["payload"]
        assert intake._field(payload, "parse_failed", False) is True


# ======================================================================================
# F. The observability cannot be removed without a test noticing.
# ======================================================================================
class TestTheObservabilityIsPinnedInSource:
    """A source assertion, in the shape this repository already uses elsewhere.

    The behaviour above is proven by execution; this pins the fact that the failure
    branch still PASSES the two diagnostics, so a later refactor that quietly drops them
    fails here rather than silently restoring L's blind spot.
    """

    def _failure_branch(self) -> str:
        source = ANALYZER_PATH.read_text()
        at = source.index("except (json.JSONDecodeError, AttributeError):")
        return source[at:source.index("continue", at)]

    def test_the_failure_branch_records_the_stop_reason(self):
        assert "_stop_reason_of(response)" in self._failure_branch()

    def test_the_failure_branch_records_a_bounded_excerpt(self):
        branch = self._failure_branch()
        assert "raw_response_excerpt=" in branch
        assert "DIAGNOSTIC_EXCERPT_CHARS" in branch

    def test_the_failure_branch_still_records_the_page_as_failed(self):
        branch = self._failure_branch()
        assert "parse_failed=True" in branch
        # It sets nothing else: no members, no connections, no drawing metadata.
        assert "raw_members=" not in branch
        assert "raw_connections=" not in branch

    def test_the_bound_is_not_a_magic_number_inlined_at_the_call_site(self):
        branch = self._failure_branch()
        assert "500" not in branch.replace("DIAGNOSTIC_EXCERPT_CHARS", "")

    def test_the_stop_reason_is_read_defensively_and_never_defaulted(self):
        source = ANALYZER_PATH.read_text()
        at = source.index("def _stop_reason_of(")
        body = source[at:source.index("\n\n\n", at)]
        assert "getattr(response, \"stop_reason\", None)" in body
        # Nothing here maps a reason onto a verdict — the value is recorded, not judged.
        assert "max_tokens" not in body.replace("stop_reason", "")
