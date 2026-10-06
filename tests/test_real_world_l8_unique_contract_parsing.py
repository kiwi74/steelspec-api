"""
L8 — SAFE UNIQUE-CONTRACT JSON NORMALIZATION.

WHAT THIS FILE IS

The regression proof for the parser change L7 identified and the project accepted. Before
it, `_extract_json` stripped one leading and one trailing code fence and then required the
ENTIRE remainder of the response to be a single JSON value. Two things followed:

  * a response that was a correct extraction followed by a sentence of explanation was
    thrown away entirely — L5 and L6 caught the provider doing exactly that, and found it
    happens on 3 of 5 identical trials for one page; and

  * a response that was an arbitrary object, an array, or a contract wrapped in another
    object was ACCEPTED, and silently became a page that apparently held nothing. That is
    the more dangerous half, and it is the half this file is mostly about.

The accepted rule, implemented in `_extract_json`: scan for COMPLETE top-level JSON
objects, keep those carrying all five contract keys, and accept only when exactly one
exists. Zero candidates and two-or-more candidates both raise.

WHAT THIS FILE DOES NOT CLAIM

It does not touch truncation. A response cut off mid-object has no complete contract
object and is still refused — the `TestNothingIsRepaired` section asserts that, and the
token ceiling remains a separate, unmade decision (production `max_tokens` is 3000 and
this milestone did not change it).

WHAT IS REAL

`_extract_json`, `_complete_json_objects` and `CONTRACT_KEYS` are imported from the
production module and exercised as they are. The two shapes in `TestRealObservedShapes`
are the bounded 500-character excerpts actually captured from production in L5 — verbatim,
with nothing invented to fill what the bound cut off.
"""
from __future__ import annotations

import json
import os

# `app/config.py` reads os.environ at import time, so placeholder configuration must exist
# for the analyzer to be importable at all. Supplied only when absent — a real environment
# is never clobbered — and deliberately LEFT IN PLACE, because other modules in this suite
# read configuration at request time rather than at import.
os.environ.setdefault("SUPABASE_URL", "https://l8-parser.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l8-test-placeholder-not-a-credential")

import pytest  # noqa: E402

from app.ai_analysis.pdf_vision_analyzer import (  # noqa: E402
    CONTRACT_KEYS, _complete_json_objects, _extract_json,
)


def contract(**overrides) -> str:
    """A response body carrying the full contract, as JSON text."""
    body = {
        "drawing_number": "A1", "drawing_title": "Test", "revision": "A",
        "members": [], "connections": [],
    }
    body.update(overrides)
    return json.dumps(body)


OTHER = contract(drawing_number="A2", drawing_title="Other", revision="B")
EMPTY = contract(drawing_number=None, drawing_title=None, revision=None)
PROSE = "This is explanatory prose."
FENCED = lambda body: f"```json\n{body}\n```"  # noqa: E731


# ======================================================================================
# A. Shapes that are a reading and must be accepted.
# ======================================================================================
class TestAcceptedShapes:
    def test_pure_json(self):
        assert _extract_json(contract())["drawing_number"] == "A1"

    def test_fenced_json(self):
        assert _extract_json(FENCED(contract()))["drawing_number"] == "A1"

    def test_json_followed_by_prose(self):
        """The shape L5 caught production losing."""
        assert _extract_json(f"{contract()}\n\n{PROSE}")["drawing_number"] == "A1"

    def test_fenced_json_followed_by_prose(self):
        """The exact shape of both real L5 failures."""
        assert _extract_json(f"{FENCED(contract())}\n\n{PROSE}")["drawing_number"] == "A1"

    def test_prose_before_json(self):
        assert _extract_json(f"Here is the extraction:\n\n{contract()}")["drawing_number"] == "A1"

    def test_prose_before_fenced_json(self):
        assert _extract_json(f"Here is the extraction:\n\n{FENCED(contract())}")["drawing_number"] == "A1"

    def test_empty_contract_followed_by_prose(self):
        """A page with nothing on it is a reading, and the explanation beside it is not a reason to lose it."""
        result = _extract_json(f"{FENCED(EMPTY)}\n\nThis page contains no steel members.")
        assert result["members"] == [] and result["connections"] == []
        assert result["drawing_number"] is None

    def test_exactly_one_contract_among_unrelated_json_objects(self):
        """Unrelated objects are ignored rather than competing — they are not candidate answers."""
        text = '{"foo": "bar"}\n\n' + contract() + '\n\n{"baz": [1, 2]}'
        assert _extract_json(text)["drawing_number"] == "A1"

    def test_contract_after_json_looking_prose_object(self):
        text = contract() + '\n\nFor reference, another object would be:\n\n{"foo": "bar"}'
        assert _extract_json(text)["drawing_number"] == "A1"

    def test_braces_in_prose_around_a_valid_contract(self):
        """A `{this}` in the commentary is text, not a candidate, and does not hide the reading."""
        text = f"Note {{this}} and {{that}} are not JSON.\n\n{contract()}\n\n{{done}}"
        assert _extract_json(text)["drawing_number"] == "A1"

    def test_additional_keys_are_allowed(self):
        text = contract(extra_field="ignored", confidence=72)
        assert _extract_json(text)["drawing_number"] == "A1"


# ======================================================================================
# B. Shapes that are NOT a reading and must be refused.
# ======================================================================================
class TestRejectedShapes:
    def test_truncated_json(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json('{"drawing_number":"A1","members":[{"mark":"B1"')

    def test_truncated_json_with_a_closing_fence_but_no_closing_brace(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json('```json\n{\n  "drawing_number": "A1",\n  "members": [')

    def test_unterminated_string(self):
        """A quote that never closes ends the string grammar; the object does not complete."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json('{"drawing_number": "A1", "drawing_title": "never closed')

    def test_two_competing_contract_objects(self):
        """Which of them is the reading is exactly what is unstated."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json(f"{contract()}\n\n{OTHER}")

    def test_contract_shaped_example_before_the_real_contract(self):
        """The decoy case: returning the first object here would return the EXAMPLE."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json(f"{OTHER}\n\nThe actual extraction is:\n\n{contract()}")

    def test_json_array_of_contracts(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json(f"[{contract()}, {OTHER}]")

    def test_arbitrary_json_object(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json('{"foo": "bar"}')

    def test_contract_missing_a_required_key(self):
        """The accepted stricter behaviour: a silent default is no longer offered."""
        body = {"drawing_number": "A1", "drawing_title": "T", "revision": "A", "members": []}
        with pytest.raises(json.JSONDecodeError):
            _extract_json(json.dumps(body))

    @pytest.mark.parametrize("missing", CONTRACT_KEYS)
    def test_a_contract_missing_any_one_key_is_refused(self, missing):
        body = {"drawing_number": "A1", "drawing_title": "T", "revision": "A",
                "members": [], "connections": []}
        del body[missing]
        with pytest.raises(json.JSONDecodeError):
            _extract_json(json.dumps(body))

    def test_json_array_holding_exactly_one_contract(self):
        """A one-element array holds one object — counting only braces would accept it."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json(f"[{contract()}]")

    def test_contract_nested_inside_another_object(self):
        """The nested object is not itself a top-level candidate answer."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json(f'{{"result": {contract()}}}')

    def test_no_json_at_all(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json(PROSE)

    def test_empty_response(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("")

    def test_a_bare_json_scalar(self):
        """Valid JSON, but not an object and not a reading."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json("42")


# ======================================================================================
# C. The scanner's grammar — one character of it is one silently wrong answer.
# ======================================================================================
class TestScannerGrammar:
    def test_braces_inside_strings_are_text_not_structure(self):
        body = contract(members=[{"mark": "B{1}", "detail": "{see note}"}])
        result = _extract_json(body)
        assert result["members"][0]["mark"] == "B{1}"

    def test_escaped_quotes_inside_strings(self):
        """`json.dumps` writes the quotes escaped, so the response really contains `\\"`."""
        wanted = 'say "hi" loudly'
        body = contract(members=[{"detail_reference": wanted}])
        assert '\\"' in body
        assert _extract_json(body)["members"][0]["detail_reference"] == wanted

    def test_escaped_backslashes_do_not_end_a_string(self):
        """A doubled backslash is an escaped backslash, not an escape of the closing quote."""
        wanted = "C:\\drawings\\A1"
        body = contract(members=[{"path": wanted}])
        assert "\\\\" in body
        assert _extract_json(body)["members"][0]["path"] == wanted

    def test_a_closing_brace_inside_a_string_does_not_close_the_object(self):
        """If this were mishandled the object would appear to end early and truncate the reading."""
        body = contract(members=[{"mark": "}"}], connections=[{"detail": "{"}])
        result = _extract_json(body)
        assert result["members"][0]["mark"] == "}" and result["connections"][0]["detail"] == "{"

    def test_nested_objects_and_arrays_inside_the_contract(self):
        body = contract(members=[{"nested": {"a": 1}}], connections=[{"bolts": [{"size": "M12"}]}])
        result = _extract_json(body)
        assert result["members"][0]["nested"] == {"a": 1}
        assert result["connections"][0]["bolts"] == [{"size": "M12"}]

    def test_contract_text_inside_a_quoted_string_in_prose(self):
        """A contract quoted inside a string is not a contract; the real one is still found."""
        quoted = json.dumps(contract())
        text = f'The documented schema is {quoted} in our notes.\n\n{contract()}'
        assert _extract_json(text)["drawing_number"] == "A1"

    def test_the_scanner_reports_complete_objects_only(self):
        assert _complete_json_objects('{"a": 1} and {"b": 2}') == ['{"a": 1}', '{"b": 2}']
        assert _complete_json_objects('{"a": 1') == []
        assert _complete_json_objects('no braces here') == []


# ======================================================================================
# D. Nothing is repaired, completed or inferred.
# ======================================================================================
class TestNothingIsRepaired:
    def test_a_truncated_contract_is_never_completed(self):
        """The appended `}` in the old implementation's fence-strip could not do this; nothing may."""
        partial = '{"drawing_number": "A1", "drawing_title": "T", "revision": "A", "members": [], "connections":'
        with pytest.raises(json.JSONDecodeError):
            _extract_json(partial + "\n```")

    def test_a_truncated_object_does_not_leak_into_a_later_one(self):
        """A broken attempt followed by a good one stays one unterminated span, not two objects."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json('{"drawing_number":"A1","members":[{"mark":"B1"\n\n' + contract())

    def test_a_prose_sentence_containing_a_whole_contract_is_not_repaired_into_one(self):
        """Two contract-shaped objects, however they are arranged, are still two."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json(f"Example: {contract()}\n\nActual: {contract()}")


# ======================================================================================
# E. Responses that already parsed must produce the same object they always did.
# ======================================================================================
class TestAcceptedResponsesAreSemanticallyUnchanged:
    @pytest.mark.parametrize("body", [
        {"drawing_number": "A1", "drawing_title": "Test", "revision": "A", "members": [], "connections": []},
        {"drawing_number": None, "drawing_title": None, "revision": None, "members": [], "connections": []},
        {"drawing_number": "S101", "drawing_title": "Framing Plan", "revision": "B",
         "members": [{"mark": "B1", "section": "310UB40", "confidence": 72}],
         "connections": [{"detail_reference": "4/S102", "bolts": [{"size": "M20"}]}]},
    ])
    def test_round_trip_is_identity(self, body):
        """Whatever the old parser returned for these, it was exactly this dict."""
        assert _extract_json(json.dumps(body)) == body
        assert _extract_json(f"```json\n{json.dumps(body)}\n```") == body


# ======================================================================================
# F. The shapes actually observed in production (L5), verbatim.
# ======================================================================================
L5_816A3804_P2 = (
    '```json\n{\n  "drawing_number": null,\n  "drawing_title": null,\n  "revision": null,\n'
    '  "members": [],\n  "connections": []\n}\n```\n\nThis page is a specification/notes sheet '
    "containing general site notes, timber treatment specifications, foundation notes, floor plan "
    "notes, bracing notes, truss designer information, enclosure details, interior notes, and "
    "demolition notes. It does not contain any structural steel member schedules, framing plans, or "
    "connection details with explicitly labelled steel members"
)
L5_6A44EB41_P2 = (
    '```json\n{\n  "drawing_number": null,\n  "drawing_title": null,\n  "revision": null,\n'
    '  "members": [],\n  "connections": []\n}\n```\n\nThis page appears to be a general '
    "specification/notes sheet (containing timber treatment specifications, foundation notes, floor "
    "plan notes, bracing notes, truss designer information, enclosure details, interior notes, and "
    "demolition notes) rather than a structural steel fabrication drawing with identifiable steel "
    "members or connections with marks, sections, and dimensions"
)


class TestRealObservedShapes:
    """The two genuine production failures from L5, as bounded 500-character excerpts.

    Nothing is invented to fill what the 500-character bound cut off: the JSON object is
    complete inside the excerpt and the prose is cut mid-sentence, which is exactly what
    was captured. Both must now be recovered — and recovered CORRECTLY, as the empty
    reading they are, not as some other object.
    """

    @pytest.mark.parametrize("excerpt", [L5_816A3804_P2, L5_6A44EB41_P2])
    def test_the_real_failure_is_recovered(self, excerpt):
        result = _extract_json(excerpt)
        assert result["drawing_number"] is None
        assert result["drawing_title"] is None
        assert result["revision"] is None
        assert result["members"] == []
        assert result["connections"] == []

    @pytest.mark.parametrize("excerpt", [L5_816A3804_P2, L5_6A44EB41_P2])
    def test_the_recovered_object_is_exactly_the_object_in_the_excerpt(self, excerpt):
        inside = excerpt[excerpt.index("{"):excerpt.index("}") + 1]
        assert _extract_json(excerpt) == json.loads(inside)

    @pytest.mark.parametrize("excerpt", [L5_816A3804_P2, L5_6A44EB41_P2])
    def test_the_old_parser_rejected_these_exact_excerpts(self, excerpt):
        """The behaviour this milestone exists to change, asserted as the OLD rule.

        Written out rather than imported, so this file records what the former contract
        was and cannot pass by accident once nothing implements it any more.
        """
        import re
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", excerpt.strip())
        with pytest.raises(json.JSONDecodeError):
            json.loads(cleaned)


# ======================================================================================
# G. L1 compatibility — a refusal is still a refusal.
# ======================================================================================
class TestL1Compatibility:
    def test_a_refusal_raises_the_exception_the_caller_already_handles(self):
        """`analyze_pdf_pages` catches exactly this, so a refusal still becomes parse_failed."""
        for text in ('{"foo": "bar"}', "not json", '{"a": 1', f"[{contract()}]"):
            with pytest.raises(json.JSONDecodeError):
                _extract_json(text)

    def test_every_refusal_is_a_jsondecodeerror_not_a_bare_exception(self):
        for text in ("", "prose", '{"a": 1', f"{contract()}\n{contract()}"):
            try:
                _extract_json(text)
            except Exception as exc:
                assert isinstance(exc, json.JSONDecodeError), type(exc).__name__
            else:
                pytest.fail(f"{text!r} was accepted")
