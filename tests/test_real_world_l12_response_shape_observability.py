"""
L12 — SUCCESS-PATH RESPONSE-SHAPE OBSERVABILITY.

WHAT THIS FILE IS

L11 could not settle whether the deployed L8 parser had actually recovered a
fenced-contract-plus-prose response, because a SUCCESSFUL parse recorded nothing about the
response it came from: the L1 diagnostics (`stop_reason`, `raw_response_excerpt`) are
written on the failure path only. Both of these left the same trace —

    clean JSON          -> parser -> success
    fenced JSON + prose -> L8     -> success

— so "the parser recovered this" could not be told from "the provider answered plainly
this time". More extractions could not have fixed that; only observability could.

This file proves the added diagnostic: a successful page now records the SHAPE of the
response it was read out of, under a fixed four-word vocabulary, and nothing else about
its behaviour changes.

WHAT IT DOES NOT CLAIM

`response_shape` is a statement about a RESPONSE, not about a drawing. It is not a
contract key, the parser never consults it, and this file asserts that it cannot be read
as members, connections or any other engineering evidence. The two shapes that always
parsed (`clean_json`, `fenced_json`) are deliberately recorded as *not* evidence of L8:
only `fenced_json_with_trailing_prose` — and `unknown`, which means "not the plain case" —
say that the parser had to work around what surrounded the object.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("SUPABASE_URL", "https://l12-shape.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l12-test-placeholder-not-a-credential")

import pytest  # noqa: E402

from app.ai_analysis import pdf_vision_analyzer as analyzer  # noqa: E402
from app.ai_analysis.pdf_vision_analyzer import (  # noqa: E402
    RESPONSE_SHAPE_CLEAN_JSON,
    RESPONSE_SHAPE_FENCED_JSON,
    RESPONSE_SHAPE_FENCED_JSON_WITH_TRAILING_PROSE,
    RESPONSE_SHAPE_UNKNOWN,
    RESPONSE_SHAPES,
    _response_shape,
)
from app.engineering_data import page_extraction_capture as captures  # noqa: E402

CONTRACT = '{"drawing_number": "A1", "drawing_title": "Test", "revision": "A", "members": [], "connections": []}'
OTHER = '{"drawing_number": "A2", "drawing_title": "Other", "revision": "B", "members": [], "connections": []}'
PROSE = "This page is a specification/notes sheet and holds no steel members."
MODEL = "claude-sonnet-4-6"


# ======================================================================================
# A. The classifier itself, on the raw response.
# ======================================================================================
class TestTheShapeVocabulary:
    def test_the_vocabulary_is_fixed(self):
        assert RESPONSE_SHAPES == (
            "clean_json", "fenced_json", "fenced_json_with_trailing_prose", "unknown",
        )

    def test_clean_json(self):
        assert _response_shape(CONTRACT) == RESPONSE_SHAPE_CLEAN_JSON

    def test_fenced_json(self):
        assert _response_shape(f"```json\n{CONTRACT}\n```") == RESPONSE_SHAPE_FENCED_JSON

    def test_bare_fence_without_the_json_hint(self):
        assert _response_shape(f"```\n{CONTRACT}\n```") == RESPONSE_SHAPE_FENCED_JSON

    def test_fenced_json_with_trailing_prose(self):
        """The decisive word: the shape L8 exists to recover."""
        assert _response_shape(f"```json\n{CONTRACT}\n```\n\n{PROSE}") == \
            RESPONSE_SHAPE_FENCED_JSON_WITH_TRAILING_PROSE

    def test_unfenced_json_with_trailing_prose_is_the_same_kind_of_finding(self):
        assert _response_shape(f"{CONTRACT}\n\n{PROSE}") == \
            RESPONSE_SHAPE_FENCED_JSON_WITH_TRAILING_PROSE

    def test_prose_before_the_object_is_unknown_not_a_named_shape(self):
        """A recoverable shape, but not one of the four words — so it is not dressed as one."""
        assert _response_shape(f"Here is the extraction:\n\n{CONTRACT}") == RESPONSE_SHAPE_UNKNOWN

    def test_several_objects_is_unknown(self):
        assert _response_shape(f"{CONTRACT}\n\n{OTHER}") == RESPONSE_SHAPE_UNKNOWN

    def test_no_object_is_unknown(self):
        assert _response_shape(PROSE) == RESPONSE_SHAPE_UNKNOWN
        assert _response_shape("") == RESPONSE_SHAPE_UNKNOWN

    def test_the_shape_is_read_off_the_raw_text_not_off_what_was_parsed(self):
        """Two responses that parse to the SAME object are told apart."""
        plain = _response_shape(f"```json\n{CONTRACT}\n```")
        with_prose = _response_shape(f"```json\n{CONTRACT}\n```\n\n{PROSE}")
        assert plain != with_prose


# ======================================================================================
# B. The whole chain: provider response -> analyzer -> capture payload.
# ======================================================================================
class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text, stop_reason):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Client:
    def __init__(self, response):
        class _M:
            def create(_self, **kwargs):
                return response
        self.messages = _M()


def drive(monkeypatch, text, stop_reason=None):
    """Runs the REAL `analyze_pdf_pages` against one stated provider response."""
    response = _Response(text, stop_reason)
    monkeypatch.setattr(analyzer, "ANTHROPIC_API_KEY", "test-key-not-a-credential")
    monkeypatch.setattr(analyzer, "Anthropic", lambda **kw: _Client(response))
    monkeypatch.setattr(analyzer, "render_pages_to_png",
                        lambda filepath, max_pages, first_page=1: [b"png"])
    monkeypatch.setattr(analyzer, "upload_page_image", lambda *a, **k: "stored/nowhere.png")
    return analyzer.analyze_pdf_pages("not-read", "u", "p", "d", 10)


def payload_of(page):
    rows = captures.capture_rows([page], analysis_run_id="run-1", drawing_id="d",
                                 drawing_set_id="s", project_id="p", model=MODEL)
    assert len(rows) == 1
    return rows[0]["payload"], rows[0]


class TestTheChainFromResponseToStoredPayload:
    @pytest.mark.parametrize("text,expected", [
        (CONTRACT, RESPONSE_SHAPE_CLEAN_JSON),
        (f"```json\n{CONTRACT}\n```", RESPONSE_SHAPE_FENCED_JSON),
        (f"```json\n{CONTRACT}\n```\n\n{PROSE}", RESPONSE_SHAPE_FENCED_JSON_WITH_TRAILING_PROSE),
        (CONTRACT + "\n\n" + PROSE, RESPONSE_SHAPE_FENCED_JSON_WITH_TRAILING_PROSE),
    ])
    def test_a_successful_page_stores_the_shape_it_came_from(self, monkeypatch, text, expected):
        page = drive(monkeypatch, text, "end_turn")[0]
        assert page.parse_failed is False
        assert page.response_shape == expected
        payload, _ = payload_of(page)
        assert payload["response_shape"] == expected

    def test_the_decisive_case_is_recovered_and_labelled(self, monkeypatch):
        """The exact thing L11 could not prove: prose after the object, read successfully."""
        page = drive(monkeypatch, f"```json\n{CONTRACT}\n```\n\n{PROSE}", "end_turn")[0]
        assert page.parse_failed is False
        assert page.drawing_number == "A1"
        assert page.raw_members == [] and page.raw_connections == []
        assert page.response_shape == RESPONSE_SHAPE_FENCED_JSON_WITH_TRAILING_PROSE

    def test_every_successful_page_records_a_shape_from_the_vocabulary(self, monkeypatch):
        for text in (CONTRACT, f"```json\n{CONTRACT}\n```", f"```json\n{CONTRACT}\n```\n\n{PROSE}"):
            page = drive(monkeypatch, text)[0]
            assert page.response_shape in RESPONSE_SHAPES


# ======================================================================================
# C. Nothing that parsed before behaves differently.
# ======================================================================================
class TestTheSevenExtractionKeysAreUnchanged:
    def test_a_successful_reading_is_still_the_models_own(self, monkeypatch):
        page = drive(monkeypatch, f"```json\n{CONTRACT}\n```\n\n{PROSE}", "end_turn")[0]
        assert page.page_number == 1
        assert page.drawing_number == "A1"
        assert page.drawing_title == "Test"
        assert page.revision == "A"
        assert page.raw_members == []
        assert page.raw_connections == []
        assert page.parse_failed is False

    def test_the_diagnostic_is_not_a_contract_key(self):
        assert "response_shape" not in analyzer.CONTRACT_KEYS
        assert set(analyzer.CONTRACT_KEYS) == {
            "drawing_number", "drawing_title", "revision", "members", "connections"}

    def test_the_diagnostic_cannot_be_read_as_members_or_connections(self, monkeypatch):
        """It is a word about a response. Nothing may fold it into engineering data."""
        page = drive(monkeypatch, f"```json\n{CONTRACT}\n```\n\n{PROSE}", "end_turn")[0]
        payload, _ = payload_of(page)
        assert payload["raw_members"] == []
        assert payload["raw_connections"] == []
        # The shape word appears once, in its own field, and nowhere among the readings.
        assert page.response_shape not in json.dumps(page.raw_members)
        assert page.response_shape not in json.dumps(page.raw_connections)

    def test_the_shape_does_not_make_an_object_look_like_a_contract(self):
        """A response carrying the diagnostic keys is not thereby contract-shaped."""
        not_a_contract = '{"response_shape": "clean_json", "stop_reason": "end_turn"}'
        with pytest.raises(json.JSONDecodeError):
            analyzer._extract_json(not_a_contract)


# ======================================================================================
# D. Nothing that failed behaves differently.
# ======================================================================================
class TestFailureBehaviourIsUnchanged:
    def test_a_truncated_response_still_fails_and_records_no_shape(self, monkeypatch):
        page = drive(monkeypatch, '```json\n{"drawing_number": "A1", "members": [', "max_tokens")[0]
        assert page.parse_failed is True
        assert page.response_shape is None
        payload, row = payload_of(page)
        assert row["parse_failed"] is True
        assert "response_shape" not in payload          # absent, never a null
        assert payload["raw_members"] == [] and payload["raw_connections"] == []

    def test_the_l1_failure_diagnostics_are_intact(self, monkeypatch):
        truncated = '```json\n{"drawing_number": "A1", "members": ['
        page = drive(monkeypatch, truncated, "max_tokens")[0]
        assert page.stop_reason == "max_tokens"
        assert page.raw_response_excerpt == truncated[:analyzer.DIAGNOSTIC_EXCERPT_CHARS]
        payload, _ = payload_of(page)
        assert payload["stop_reason"] == "max_tokens"
        assert payload["raw_response_excerpt"] == truncated[:analyzer.DIAGNOSTIC_EXCERPT_CHARS]
        assert len(payload["raw_response_excerpt"]) <= 500

    def test_arbitrary_json_is_still_rejected(self, monkeypatch):
        page = drive(monkeypatch, '{"foo": "bar"}')[0]
        assert page.parse_failed is True and page.response_shape is None

    def test_multiple_contract_objects_are_still_rejected(self, monkeypatch):
        page = drive(monkeypatch, f"{CONTRACT}\n\n{OTHER}")[0]
        assert page.parse_failed is True and page.response_shape is None

    def test_an_array_containing_a_contract_is_still_rejected(self, monkeypatch):
        page = drive(monkeypatch, f"[{CONTRACT}]")[0]
        assert page.parse_failed is True and page.response_shape is None

    def test_a_failure_never_becomes_a_success(self, monkeypatch):
        for text in ('{"foo": "bar"}', f"{CONTRACT}\n\n{OTHER}", f"[{CONTRACT}]", "not json"):
            assert drive(monkeypatch, text)[0].parse_failed is True


# ======================================================================================
# E. The payload is stored only when there is something to store.
# ======================================================================================
class TestPayloadShape:
    def test_a_failed_payload_gains_nothing(self, monkeypatch):
        page = drive(monkeypatch, "not json", "end_turn")[0]
        payload, _ = payload_of(page)
        assert sorted(payload) == [
            "drawing_number", "drawing_title", "page_number", "parse_failed",
            "raw_connections", "raw_members", "raw_response_excerpt", "revision", "stop_reason",
        ]

    def test_a_successful_payload_keeps_its_seven_keys_and_adds_the_shape(self, monkeypatch):
        """The reading is untouched; the shape is added beside it and nothing else is."""
        page = drive(monkeypatch, CONTRACT)[0]
        payload, _ = payload_of(page)
        seven = {"page_number", "drawing_number", "drawing_title", "revision",
                 "raw_members", "raw_connections", "parse_failed"}
        assert seven <= set(payload)
        assert set(payload) - seven == {"response_shape"}
        assert payload["response_shape"] in RESPONSE_SHAPES

    def test_an_absent_diagnostic_is_absent_rather_than_null(self):
        """A directly-built reading has no shape to state, so it states none."""
        page = analyzer.PageExtraction(page_number=1, parse_failed=True)
        payload, _ = payload_of(page)
        for name in ("stop_reason", "raw_response_excerpt", "response_shape"):
            assert name not in payload

    def test_a_plain_mapping_is_still_recorded_verbatim(self):
        """Read-back rows and fixtures are never adjusted."""
        row = {"page_number": 1, "drawing_number": None, "drawing_title": None,
               "revision": None, "raw_members": [], "raw_connections": [],
               "parse_failed": False}
        payload, _ = payload_of(row)
        assert payload == row
