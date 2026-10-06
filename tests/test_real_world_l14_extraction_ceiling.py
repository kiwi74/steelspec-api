"""
L14 — THE PRODUCTION EXTRACTION OUTPUT CEILING.

WHAT THIS FILE IS

The regression pin for one number: the `max_tokens` the real extraction analyzer asks the
provider for. It was 3000 and is now 8000.

WHY 8000, IN ONE LINE

L13 measured 10 hash-verified difficult pages at five ceilings. At 3000, 3 of 10
completed. At 6000, 8 of 10. **At 8000, 10 of 10 completed with no truncation** — and the
largest natural completion ever observed on that corpus, across every measurement taken,
was 6667 output tokens, so 8000 clears the observed maximum by roughly 20%.

WHAT THIS FILE DELIBERATELY DOES NOT CLAIM

It does not assert that 8000 guarantees completion. L13 also measured the provider's own
run-to-run variance on a single unchanged page at roughly ±10%, so any fixed ceiling can
be exceeded by an unusually long response; that is a risk knowingly accepted, not a
property this test can establish. It pins the DECISION — that the analyzer asks for 8000 —
and nothing about what the provider will return.

It also does not read the source text. The value is observed by driving the real
`analyze_pdf_pages` against a stand-in provider and recording what it was actually asked
for, so a change to the number fails this file wherever in the call it is made.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("SUPABASE_URL", "https://l14-ceiling.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l14-test-placeholder-not-a-credential")

import pytest  # noqa: E402

from app.ai_analysis import pdf_vision_analyzer as analyzer  # noqa: E402

#: The ceiling this project decided on, from the L13 measurement.
EXPECTED_MAX_TOKENS = 8000

#: The largest natural completion L13 ever observed, and the reason 8000 was chosen.
LARGEST_OBSERVED_COMPLETION = 6667

GOOD_JSON = (
    '{"drawing_number": "A1", "drawing_title": "Test", "revision": "A",'
    ' "members": [], "connections": []}'
)


class _Provider:
    """A stand-in that answers with valid JSON and RECORDS what it was asked for."""

    def __init__(self, pages):
        self.calls = []
        self._pages = pages

        class _Messages:
            def create(_self, **kwargs):
                self.calls.append(kwargs)
                return type("R", (), {
                    "content": [type("B", (), {"type": "text", "text": GOOD_JSON})()],
                    "stop_reason": "end_turn",
                    "usage": type("U", (), {"input_tokens": 1, "output_tokens": 1})(),
                })()

        self.messages = _Messages()


@pytest.fixture
def provider(monkeypatch):
    def install(pages=1):
        p = _Provider(pages)
        monkeypatch.setattr(analyzer, "ANTHROPIC_API_KEY", "test-key-not-a-credential")
        monkeypatch.setattr(analyzer, "Anthropic", lambda **kw: p)
        monkeypatch.setattr(analyzer, "render_pages_to_png",
                            lambda filepath, max_pages, first_page=1: [b"png"] * pages)
        monkeypatch.setattr(analyzer, "upload_page_image", lambda *a, **k: "stored/nowhere.png")
        return p
    return install


class TestTheProductionOutputCeiling:
    def test_the_analyzer_asks_the_provider_for_8000_output_tokens(self, provider):
        p = provider()
        analyzer.analyze_pdf_pages("not-read", "u", "p", "d", 10)

        assert len(p.calls) == 1
        assert p.calls[0]["max_tokens"] == EXPECTED_MAX_TOKENS

    def test_the_ceiling_is_applied_to_every_page_not_merely_the_first(self, provider):
        """A ceiling set once and not carried down the loop would still pass a one-page test."""
        p = provider(pages=4)
        analyzer.analyze_pdf_pages("not-read", "u", "p", "d", 10)

        assert len(p.calls) == 4
        assert {call["max_tokens"] for call in p.calls} == {EXPECTED_MAX_TOKENS}

    def test_the_ceiling_clears_the_largest_response_ever_measured(self, provider):
        """The decision rests on headroom over the observed maximum, so pin the headroom.

        This is a statement about the L13 corpus, not a guarantee about any future
        response — see the module docstring.
        """
        p = provider()
        analyzer.analyze_pdf_pages("not-read", "u", "p", "d", 10)
        assert p.calls[0]["max_tokens"] > LARGEST_OBSERVED_COMPLETION

    def test_nothing_else_about_the_request_changed(self, provider):
        """The ceiling moved; the model, the prompt and the message shape did not."""
        p = provider()
        analyzer.analyze_pdf_pages("not-read", "u", "p", "d", 10)
        call = p.calls[0]

        assert call["model"] == analyzer.PDF_VISION_MODEL
        assert call["system"] == analyzer.EXTRACTION_SYSTEM_PROMPT
        assert call["messages"][0]["role"] == "user"
        kinds = [part["type"] for part in call["messages"][0]["content"]]
        assert kinds == ["image", "text"]

    def test_the_analyzer_still_parses_what_comes_back(self, provider):
        """A ceiling change must not disturb the reading it exists to make possible."""
        provider()
        page = analyzer.analyze_pdf_pages("not-read", "u", "p", "d", 10)[0]
        assert page.parse_failed is False
        assert page.drawing_number == "A1"
        assert json.loads(GOOD_JSON)["members"] == page.raw_members
