"""
Wave 3J Phase B — the safe persisted error classification.

THE PROPERTY UNDER TEST
=======================
No value written into `projects.error_message`, `analysis_runs.error_message` or
`drawing_sets.error_message` may be derived from external exception text, a provider
response body, a database response body, a storage response body, a URL path, or any
other external diagnostic content.

HOW IT IS PROVEN
================
Two independent ways, because either one alone would be weak:

  1. DYNAMICALLY, over an adversarial corpus. Real exception objects — carrying real
     sentinels in exactly the places the external systems put real data — are driven
     through the REAL boundaries, and the sentinel is asserted ABSENT from the persisted
     value. Each such test also asserts the sentinel IS in `str(exc)`, so a test cannot
     pass vacuously by constructing an exception that never carried the value.

  2. STATICALLY, over the tree. Every site in `app/` that can write an `error_message`
     is found by parsing the source and is required to be one of exactly two shapes: a
     fixed SteelSpec literal, or a call to `safe_failure_message`. This is the part that
     survives a future seventh writer.

WHAT IS REAL HERE
=================
  * The real `app.main.run_extraction`, `app.main.build_and_store_report`, the real
    `/generate-report/{project_id}` route through a real `TestClient`, and the real
    `app.pipeline.parse_pdf_and_save`.
  * The real exception classes from `anthropic`, `httpx`, `postgrest`, `storage3` and
    `pdf2image`, constructed the way those libraries construct them.
  * The real source tree, read from disk for the static guard.

WHAT IS NOT HERE
================
  * No provider is called and no credential is used. The exception objects are built
    locally; no network request is made to build any of them.
  * No Supabase, storage or database call is made. Every client is a recorder.
  * No production row is read, selected, printed, hashed or modified.
"""
from __future__ import annotations

import ast
import pathlib
import traceback

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from pdf2image.exceptions import PDFPageCountError, PopplerNotInstalledError
from postgrest.exceptions import APIError as PostgrestAPIError
from storage3.exceptions import StorageApiError

import app.main as main_module
import app.pipeline as pipeline_module
from app.cad_engine.production_coverage import PAGE_STATUS_PARSE_FAILED
from app.production_extraction_source import EXTRACTION_INPUT_REFUSALS
from app.storage_download import StorageDownloadTimeout
from app.validation.failure_classification import (
    EXTRACTION_PROVIDER_AUTH,
    EXTRACTION_PROVIDER_CONFIG,
    EXTRACTION_PROVIDER_UNAVAILABLE,
    EXTRACTION_REPORT_FAILED,
    EXTRACTION_SOURCE_UNREADABLE,
    EXTRACTION_STORAGE_UNAVAILABLE,
    EXTRACTION_STORE_FAILED,
    EXTRACTION_UNEXPECTED,
    FAILURE_CLASSIFICATIONS,
    PERSISTED_MESSAGE_FOR,
    SAFE_FAILURE_MESSAGES,
    ProviderNotConfigured,
    ReportGenerationFailed,
    SourceUnreadable,
    classify_failure,
    safe_failure_message,
)
from app.validation.project_status import BLOCKER_PAGE_PARSE_FAILED

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"

# --------------------------------------------------------------------------------------
# Sentinels. Each one is shaped like the real content that would occupy that slot in a
# real failure, and each is distinctive enough that a substring check is meaningful.
# --------------------------------------------------------------------------------------
# The kind of text an external service puts in its own error body.
PROVIDER_SENTINEL = "SENTINEL-PROVIDER-BODY-do-not-persist"
# The kind of text PostgreSQL puts in a constraint-violation `details` field: REAL STORED
# ROW VALUES. This is the exposure that most justifies the milestone.
DB_ROW_SENTINEL = "SENTINEL-STORED-ROW-VALUE-do-not-persist"
# The kind of text a storage URL carries.
URL_SENTINEL = "SENTINEL-STORAGE-PATH-do-not-persist"
# The kind of text a generic exception carries.
GENERIC_SENTINEL = "SENTINEL-EXCEPTION-TEXT-do-not-persist"

ALL_SENTINELS = (PROVIDER_SENTINEL, DB_ROW_SENTINEL, URL_SENTINEL, GENERIC_SENTINEL)

ANTHROPIC_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# ======================================================================================
# Fixtures and helpers — real boundaries, substituted transports
# ======================================================================================
class ExtractionFailed(Exception):
    """A synthetic stand-in for any unexpected failure at either boundary."""


class _RecordingClient:
    """`supabase` as `app.main` uses it. Records every write; touches no database."""

    def __init__(self):
        self.writes: list[tuple[str, dict]] = []
        self._table = None
        self._values = None

    def table(self, name):
        self._table = name
        return self

    def update(self, values):
        self._values = values
        return self

    def eq(self, column, value):
        self.column, self.value = column, value
        return self

    def execute(self):
        self.writes.append((self._table, dict(self._values)))
        from types import SimpleNamespace
        return SimpleNamespace(data=[])

    @property
    def persisted_values(self) -> list[str]:
        return [v["error_message"] for _, v in self.writes if "error_message" in v]


class _PipelineRepo:
    """`app.pipeline`'s repository as `parse_pdf_and_save` uses it before the handler."""

    def __init__(self):
        self.writes: list[tuple[str, dict]] = []
        self._n = 0

    def _next_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    def create_project_document(self, project_id, **fields):
        return {"id": self._next_id("doc")}

    def create_drawing_set(self, project_id, name):
        return {"id": self._next_id("set")}

    def create_drawing(self, drawing_set_id, file_name, storage_path, document_id=None):
        return {"id": self._next_id("drw")}

    def create_analysis_run(self, drawing_set_id, model_used):
        return {"id": self._next_id("run")}

    def update_analysis_run(self, analysis_run_id, **fields):
        self.writes.append(("analysis_runs", dict(fields)))

    def update_drawing_set(self, drawing_set_id, **fields):
        self.writes.append(("drawing_sets", dict(fields)))

    @property
    def persisted_values(self) -> list[str]:
        return [v["error_message"] for _, v in self.writes if "error_message" in v]


def _raise(exc):
    def _stand_in(*args, **kwargs):
        raise exc
    return _stand_in


def _drive_main(monkeypatch, exc) -> _RecordingClient:
    """Drive the REAL `run_extraction` so `exc` reaches its own broad handler."""
    client = _RecordingClient()
    monkeypatch.setattr(main_module, "download_from_uploads", _raise(exc))
    monkeypatch.setattr(main_module, "supabase", client)
    main_module.run_extraction("p-1", "drawings/plan.pdf", "PDF", "u-1")
    return client


def _drive_pipeline(monkeypatch, exc) -> _PipelineRepo:
    """Drive the REAL `parse_pdf_and_save` so `exc` reaches its own broad handler."""
    repo = _PipelineRepo()
    monkeypatch.setattr(pipeline_module, "repo", repo)
    monkeypatch.setattr(pipeline_module, "source_bytes", lambda source: b"%PDF-1.4")
    monkeypatch.setattr(pipeline_module, "_run_pipeline", _raise(exc))
    with pytest.raises(type(exc)):
        pipeline_module.parse_pdf_and_save("plan.pdf", "p-1", "u-1", "drawings/plan.pdf")
    return repo


def _drive_both(monkeypatch, exc) -> list[str]:
    """Every persisted value the two boundaries produce for one failure."""
    return _drive_main(monkeypatch, exc).persisted_values + \
        _drive_pipeline(monkeypatch, exc).persisted_values


def _assert_no_sentinel(values: list[str], *sentinels: str, context: str = "") -> None:
    assert values, f"nothing was persisted at all {context}".strip()
    for value in values:
        for sentinel in sentinels:
            assert sentinel not in value, (
                f"{context}: external content {sentinel!r} reached the persisted value "
                f"{value!r}"
            )
        assert value in SAFE_FAILURE_MESSAGES, (
            f"{context}: {value!r} is not one of the frozen SteelSpec messages"
        )


# ======================================================================================
# A / B / C — a sentinel in an external exception cannot enter any of the three columns
# ======================================================================================
class TestExternalExceptionTextCannotEnterAnyColumn:
    """The three columns named by the invariant, each one asserted separately, because
    the brief is explicit that they must not be assumed to move together."""

    def test_a_projects_carries_no_exception_text(self, monkeypatch):
        exc = ExtractionFailed(GENERIC_SENTINEL)
        assert GENERIC_SENTINEL in str(exc)  # the sentinel is really on the object

        client = _drive_main(monkeypatch, exc)
        _assert_no_sentinel(client.persisted_values, GENERIC_SENTINEL, context="projects")

    def test_b_analysis_runs_carries_no_exception_text(self, monkeypatch):
        exc = ExtractionFailed(GENERIC_SENTINEL)
        repo = _drive_pipeline(monkeypatch, exc)

        values = [v["error_message"] for t, v in repo.writes if t == "analysis_runs"]
        _assert_no_sentinel(values, GENERIC_SENTINEL, context="analysis_runs")

    def test_c_drawing_sets_carries_no_exception_text(self, monkeypatch):
        exc = ExtractionFailed(GENERIC_SENTINEL)
        repo = _drive_pipeline(monkeypatch, exc)

        values = [v["error_message"] for t, v in repo.writes if t == "drawing_sets"]
        _assert_no_sentinel(values, GENERIC_SENTINEL, context="drawing_sets")

    def test_the_repr_and_args_forms_of_the_sentinel_are_absent_too(self, monkeypatch):
        """Absence of the raw text is not enough on its own: a writer that persisted
        `repr(exc)` or `exc.args[0][:500]` would still be leaking. Both forms are checked,
        against every column, for a value that is hostile to substring matching."""
        exc = ExtractionFailed(f"{GENERIC_SENTINEL} 'quoted\\n")
        values = _drive_both(monkeypatch, exc)

        for value in values:
            for form in (str(exc), repr(exc), str(exc.args), str(exc.args[0])):
                assert form not in value, f"{form!r} reached {value!r}"

    def test_the_two_columns_of_one_failure_cannot_disagree(self, monkeypatch):
        """`analysis_runs` and `drawing_sets` describe one failure. They are written from
        a single call, so they cannot describe it two different ways."""
        repo = _drive_pipeline(monkeypatch, ExtractionFailed(GENERIC_SENTINEL))
        values = {v["error_message"] for _, v in repo.writes}

        assert [t for t, _ in repo.writes] == ["analysis_runs", "drawing_sets"]
        assert values == {PERSISTED_MESSAGE_FOR[EXTRACTION_UNEXPECTED]}


# ======================================================================================
# D — a provider RESPONSE BODY cannot enter any persisted column
# ======================================================================================
class TestProviderResponseBodiesCannotEnterAnyColumn:
    """Built through the SDK's own constructor, so the sentinel sits in the `body` and in
    `str(exc)` exactly as a real 4xx would put it there."""

    @staticmethod
    def _bad_request(body_sentinel: str) -> anthropic.BadRequestError:
        body = {"type": "error", "error": {"type": "invalid_request_error",
                                           "message": body_sentinel}}
        response = httpx.Response(400, request=ANTHROPIC_REQUEST, json=body)
        return anthropic.BadRequestError(body_sentinel, response=response, body=body)

    def test_the_provider_body_is_on_the_exception_and_absent_from_every_column(self, monkeypatch):
        exc = self._bad_request(PROVIDER_SENTINEL)
        # The premise: the SDK really does expose this text. Without this assertion the
        # test could pass by never having carried the value at all.
        assert PROVIDER_SENTINEL in str(exc)

        values = _drive_both(monkeypatch, exc)
        _assert_no_sentinel(values, PROVIDER_SENTINEL, context="provider body")

    def test_the_provider_body_field_is_absent_even_in_its_structured_forms(self, monkeypatch):
        """`exc.body` and `exc.response.text` are the two structured routes to the same
        content. A writer that walked either would leak; both are checked."""
        exc = self._bad_request(PROVIDER_SENTINEL)
        assert PROVIDER_SENTINEL in str(exc.body)
        assert PROVIDER_SENTINEL in exc.response.text

        for value in _drive_both(monkeypatch, exc):
            assert PROVIDER_SENTINEL not in value


# ======================================================================================
# E — a database response body cannot enter any persisted column
# ======================================================================================
class TestDatabaseResponseBodiesCannotEnterAnyColumn:
    """The sharpest case in the milestone. `postgrest.APIError.__str__` renders the whole
    envelope, and `details` on a unique-constraint violation contains REAL STORED ROW
    VALUES. Before Phase B that text could be persisted, and the frontend renders this
    column verbatim."""

    @staticmethod
    def _unique_violation(row_value: str) -> PostgrestAPIError:
        return PostgrestAPIError({
            "message": 'duplicate key value violates unique constraint "steel_members_pkey"',
            "code": "23505",
            "details": f"Key (mark)=({row_value}) already exists.",
            "hint": None,
        })

    def test_the_row_value_is_on_the_exception_and_absent_from_every_column(self, monkeypatch):
        exc = self._unique_violation(DB_ROW_SENTINEL)
        # The premise: the real row value really is reachable, through `str` AND through
        # the structured `details` attribute.
        assert DB_ROW_SENTINEL in str(exc)
        assert DB_ROW_SENTINEL in exc.details

        values = _drive_both(monkeypatch, exc)
        _assert_no_sentinel(values, DB_ROW_SENTINEL, context="database body")

    def test_the_sqlstate_is_readable_but_selects_nothing(self, monkeypatch):
        """The classifier is allowed to inspect a structured database code. It does, and
        the locked vocabulary contains no database classification for it to select among
        — which is a decision, not an oversight: `EXTRACTION_UNEXPECTED` is the honest
        answer, and inventing a database code here would be inventing a taxonomy."""
        exc = self._unique_violation(DB_ROW_SENTINEL)
        assert exc.code == "23505"

        assert classify_failure(exc) == EXTRACTION_UNEXPECTED
        assert safe_failure_message(exc) == PERSISTED_MESSAGE_FOR[EXTRACTION_UNEXPECTED]

    def test_a_storage_api_error_is_not_claimed_as_a_known_storage_failure(self, monkeypatch):
        """The same `StorageApiError` type is raised by the SOURCE download and by the
        OUTPUT uploads, and the exception alone cannot say which. Claiming
        `EXTRACTION_STORAGE_UNAVAILABLE` for an upload would state something false about
        it; claiming `EXTRACTION_STORE_FAILED` for a download would too. The catch-all is
        vague but true. This is the documented fidelity gap, asserted rather than hidden."""
        exc = StorageApiError(f"object not found: {URL_SENTINEL}", "404", 404)
        assert URL_SENTINEL in str(exc)

        assert classify_failure(exc) == EXTRACTION_UNEXPECTED
        for value in _drive_both(monkeypatch, exc):
            assert URL_SENTINEL not in value
            assert value in SAFE_FAILURE_MESSAGES


# ======================================================================================
# F — the classifier is TOTAL: an unknown exception is always classified, never raised
# ======================================================================================
class TestTheClassifierIsTotal:
    def test_an_unknown_exception_is_unexpected(self):
        assert classify_failure(ExtractionFailed("anything")) == EXTRACTION_UNEXPECTED

    def test_no_exception_makes_the_classifier_raise(self):
        """A classifier that raised would replace the real failure with a second one, at
        exactly the moment the first one is being recorded."""
        request = ANTHROPIC_REQUEST
        corpus = [
            ValueError("v"),
            KeyError("k"),
            RuntimeError("r"),
            OSError("o"),
            MemoryError(),
            RecursionError(),
            anthropic.APIError("base", request=request, body=None),
            anthropic.APIResponseValidationError(
                httpx.Response(200, request=request), body=None),
            StorageApiError("s", "500", 500),
            PostgrestAPIError({"message": "m"}),
            PostgrestAPIError({}),
            SystemExit(1),
            KeyboardInterrupt(),
            # The degenerate shapes of a hand-built stand-in: no args at all, and a
            # non-string arg. Neither may make a caller's exception path raise.
            ExtractionFailed(),
            ExtractionFailed(12345, {"unhashable": object()}),
        ]
        for exc in corpus:
            assert classify_failure(exc) in FAILURE_CLASSIFICATIONS, repr(exc)
            assert safe_failure_message(exc) in SAFE_FAILURE_MESSAGES, repr(exc)

    def test_the_codomain_is_exactly_the_eight_locked_codes(self):
        assert len(FAILURE_CLASSIFICATIONS) == 8
        assert set(FAILURE_CLASSIFICATIONS) == set(PERSISTED_MESSAGE_FOR)
        assert SAFE_FAILURE_MESSAGES == frozenset(PERSISTED_MESSAGE_FOR.values())
        assert len(SAFE_FAILURE_MESSAGES) == 8

    def test_every_message_is_prefixed_by_its_own_code(self):
        """So an operator can filter on the machine-readable prefix without matching
        prose, and a reader still gets a sentence."""
        for code, message in PERSISTED_MESSAGE_FOR.items():
            assert message.startswith(f"{code}: ")

    def test_every_message_is_the_code_and_one_steel_spec_sentence(self):
        """Nothing else is in there — no interpolation, no placeholder, no field that a
        caller is expected to fill in."""
        for message in SAFE_FAILURE_MESSAGES:
            assert "{" not in message and "}" not in message
            assert "%s" not in message


# ======================================================================================
# G / H / I — provider failures dispatch by TYPE
# ======================================================================================
def _status_error(cls, status: int, sentinel: str):
    body = {"error": {"message": sentinel}}
    response = httpx.Response(status, request=ANTHROPIC_REQUEST, json=body)
    return cls(sentinel, response=response, body=body)


class TestProviderFailuresDispatchByType:
    @pytest.mark.parametrize("exc", [
        _status_error(anthropic.AuthenticationError, 401, PROVIDER_SENTINEL),
        _status_error(anthropic.PermissionDeniedError, 403, PROVIDER_SENTINEL),
    ])
    def test_g_auth_failures_classify_as_auth(self, exc):
        assert classify_failure(exc) == EXTRACTION_PROVIDER_AUTH

    @pytest.mark.parametrize("exc", [
        _status_error(anthropic.BadRequestError, 400, PROVIDER_SENTINEL),
        _status_error(anthropic.NotFoundError, 404, PROVIDER_SENTINEL),
        ProviderNotConfigured("no key"),
    ])
    def test_h_config_failures_classify_as_config(self, exc):
        assert classify_failure(exc) == EXTRACTION_PROVIDER_CONFIG

    @pytest.mark.parametrize("exc", [
        anthropic.APITimeoutError(request=ANTHROPIC_REQUEST),
        anthropic.APIConnectionError(request=ANTHROPIC_REQUEST),
        _status_error(anthropic.RateLimitError, 429, PROVIDER_SENTINEL),
        _status_error(anthropic.InternalServerError, 500, PROVIDER_SENTINEL),
    ])
    def test_i_transient_provider_failures_classify_as_unavailable(self, exc):
        assert classify_failure(exc) == EXTRACTION_PROVIDER_UNAVAILABLE

    def test_an_unrecognised_status_does_not_get_promoted_into_a_known_one(self):
        """The ordering matters: the generic base type must not swallow a subclass that a
        future SDK adds into a classification it does not belong to. A base `APIError`
        is exactly what "we do not recognise this provider failure" looks like."""
        exc = anthropic.APIError("unknown", request=ANTHROPIC_REQUEST, body=None)
        assert classify_failure(exc) == EXTRACTION_UNEXPECTED

    def test_the_provider_sentinel_is_absent_from_every_column_for_each_of_them(self, monkeypatch):
        """Dispatch correctness and the leak invariant are separate properties; a correct
        classification of the wrong string would still be a leak."""
        for exc in (
            _status_error(anthropic.AuthenticationError, 401, PROVIDER_SENTINEL),
            _status_error(anthropic.InternalServerError, 500, PROVIDER_SENTINEL),
            anthropic.APITimeoutError(request=ANTHROPIC_REQUEST),
        ):
            _assert_no_sentinel(_drive_both(monkeypatch, exc),
                                PROVIDER_SENTINEL, context=type(exc).__name__)


# ======================================================================================
# J — source storage / transport failures
# ======================================================================================
class TestSourceTransportFailures:
    def test_a_bounded_download_timeout_is_a_storage_unavailability(self):
        assert classify_failure(StorageDownloadTimeout("deadline")) == EXTRACTION_STORAGE_UNAVAILABLE

    @pytest.mark.parametrize("exc", [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectTimeout("timed out"),
        httpx.ConnectError("refused"),
        httpx.ReadError("reset"),
    ])
    def test_every_transport_timeout_and_error_is_a_storage_unavailability(self, exc):
        assert classify_failure(exc) == EXTRACTION_STORAGE_UNAVAILABLE

    def test_a_non_2xx_from_storage_is_a_storage_unavailability(self):
        url = f"https://storage.example/v1/object/uploads/{URL_SENTINEL}.pdf"
        response = httpx.Response(503, request=httpx.Request("GET", url))
        exc = httpx.HTTPStatusError("bad", request=response.request, response=response)

        assert classify_failure(exc) == EXTRACTION_STORAGE_UNAVAILABLE
        # `HTTPStatusError` does not put the URL in its message — it carries it on the
        # request/response objects instead, which is the other route a writer could take.
        assert URL_SENTINEL in str(exc.request.url)
        assert URL_SENTINEL in str(exc.response.request.url)

    def test_the_storage_url_is_absent_from_every_column(self, monkeypatch):
        path = f"https://storage.example/v1/object/uploads/{URL_SENTINEL}.pdf"
        exc = StorageDownloadTimeout(
            f"the download of {path} exceeded the deadline")
        assert URL_SENTINEL in str(exc)

        for value in _drive_both(monkeypatch, exc):
            assert URL_SENTINEL not in value
            assert path not in value

    def test_raw_httpx_reaches_us_only_from_our_own_storage_module(self):
        """Why the transport tuple is narrow enough to be correct: the provider SDK
        catches every exception around its own send and re-raises an `APIConnectionError`
        (or `APITimeoutError`), so a raw `httpx` exception escaping the provider is not a
        case the classifier has to handle — it is a case that cannot occur."""
        import inspect

        source = inspect.getsource(anthropic._base_client)
        assert "except Exception as err:" in source
        assert "raise APIConnectionError(request=request) from err" in source


# ======================================================================================
# K / L — the two SteelSpec-owned literals in `run_extraction` are unchanged
# ======================================================================================
class TestTheSteelSpecOwnedLiteralsAreUnchanged:
    def test_k_the_ifc_literal_is_unchanged(self, monkeypatch):
        """Not a failure classification at all: a handled branch reporting an unimplemented
        format. Phase B touched nothing about it."""
        client = _RecordingClient()
        monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
        monkeypatch.setattr(main_module, "supabase", client)
        main_module.run_extraction("p-1", "d.dxf", "IFC", "u-1")

        assert client.writes == [("projects", {
            "status": "failed",
            "error_message": "IFC extraction isn't wired up yet — DXF, DWG, and PDF are supported.",
        })]
        # It is SteelSpec's own sentence, not a classification, and it is not in the
        # frozen set — the two vocabularies are separate on purpose.
        assert client.persisted_values[0] not in SAFE_FAILURE_MESSAGES

    def test_l_the_unsupported_format_branch_is_still_safe_and_still_says_so(self, monkeypatch):
        client = _RecordingClient()
        monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
        monkeypatch.setattr(main_module, "supabase", client)
        main_module.run_extraction("p-1", "d.dxf", "STEP", "u-1")

        assert client.writes == [("projects", {
            "status": "failed",
            "error_message": "Unsupported source format: STEP",
        })]
        # A legitimate format is reported verbatim, so the classification is not so
        # aggressive that it destroys information the branch is supposed to convey.
        assert client.persisted_values[0] not in SAFE_FAILURE_MESSAGES

    def test_a_hostile_stored_format_cannot_reshape_the_persisted_sentence(self, monkeypatch):
        """The stored format is not exception text — but it is an external value being
        placed inside a SteelSpec-owned sentence, so the same rule applies.

        It is also the one external value that reached a FILESYSTEM PATH: the temp-file
        suffix was built from the raw format, so a format containing a separator made
        `NamedTemporaryFile` raise `FileNotFoundError` before the format branch was ever
        reached. The row then recorded EXTRACTION_UNEXPECTED for something that is not an
        exception at all. Normalizing once, before either use, closes both."""
        for hostile in ("STEP\nEXTRACTION_UNEXPECTED: forged", "a/b", "../../etc/passwd",
                        "<script>alert(1)</script>", "x" * 4096, "STEP' OR 1=1--", 12345):
            client = _RecordingClient()
            monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
            monkeypatch.setattr(main_module, "supabase", client)
            main_module.run_extraction("p-1", "d.dxf", hostile, "u-1")

            assert client.writes == [("projects", {
                "status": "failed",
                "error_message": "Unsupported source format: unrecognised",
            })], f"stored format {hostile!r} reshaped the persisted sentence"

            value = client.persisted_values[0]
            assert "EXTRACTION_UNEXPECTED" not in value
            assert "forged" not in value

    def test_the_normalized_token_is_what_reaches_the_temp_file_name(self, monkeypatch):
        """The suffix is the second use of the same value, and the one that made the
        failure above possible."""
        seen: list[str | None] = []
        real_ntf = main_module.tempfile.NamedTemporaryFile

        def _capture(*args, **kwargs):
            seen.append(kwargs.get("suffix"))
            return real_ntf(*args, **kwargs)

        monkeypatch.setattr(main_module.tempfile, "NamedTemporaryFile", _capture)
        monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
        monkeypatch.setattr(main_module, "supabase", _RecordingClient())
        main_module.run_extraction("p-1", "d.dxf", "../../etc/passwd", "u-1")
        assert seen == [".unrecognised"]

        # A legitimate format is still passed through to the suffix untouched.
        seen.clear()
        monkeypatch.setattr(main_module, "supabase", _RecordingClient())
        monkeypatch.setattr(main_module, "parse_dxf_and_save", _raise(ExtractionFailed("stop")))
        main_module.run_extraction("p-1", "d.dxf", "DXF", "u-1")
        assert seen == [".dxf"]

    def test_both_branches_report_status_failed_and_are_not_classifications(self, monkeypatch):
        for source_format in ("IFC", "STEP"):
            client = _RecordingClient()
            monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
            monkeypatch.setattr(main_module, "supabase", client)
            main_module.run_extraction("p-1", "d.dxf", source_format, "u-1")

            table, payload = client.writes[0]
            assert table == "projects"
            assert payload["status"] == "failed"


# ======================================================================================
# M — the J17 retry writer is BYTE-FOR-BYTE semantically unchanged
# ======================================================================================
class TestTheRetryWriterIsUntouched:
    """W6. The brief singles this site out: it "remains COMPLETELY UNCHANGED". Its message
    is a SteelSpec-owned f-string that interpolates one integer page number, it is written
    with `status="completed"`, and it is NOT a failure classification. Routing it through
    `safe_failure_message` would have been a lifecycle change disguised as a security fix.
    """

    RETRY_SOURCE = APP_DIR / "pipeline.py"

    def _retry_write(self) -> ast.Call:
        """The `repo.update_analysis_run(...)` call in pipeline.py whose value interpolates
        a page number. Found by structure, not by line number, so it keeps working if the
        file moves."""
        tree = ast.parse(self.RETRY_SOURCE.read_text())
        found = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "update_analysis_run":
                continue
            for kw in node.keywords:
                if (kw.arg == "error_message" and isinstance(kw.value, ast.JoinedStr)
                        and any(isinstance(v, ast.FormattedValue) for v in kw.value.values)):
                    found.append(node)
        assert len(found) == 1, f"expected exactly one interpolating retry write, got {len(found)}"
        return found[0]

    def test_the_retry_message_interpolates_only_an_integer_page_number(self):
        call = self._retry_write()
        keyword = next(k for k in call.keywords if k.arg == "error_message")

        names = {
            v.value.id
            for v in keyword.value.values
            if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
        }
        assert names == {"page"}, (
            f"the retry message now interpolates {names}; it is allowed to interpolate "
            f"only the page number"
        )

    def test_the_retry_message_is_not_a_failure_classification(self):
        """Proving the two vocabularies are still separate at the one site that could
        plausibly have been swept into the new one."""
        call = self._retry_write()
        keyword = next(k for k in call.keywords if k.arg == "error_message")

        rendered = "".join(
            part.value if isinstance(part, ast.Constant) else "{page}"
            for part in keyword.value.values
        )
        for message in SAFE_FAILURE_MESSAGES:
            assert message not in rendered
        assert "could not be parsed as engineering" in rendered

    def test_the_retry_write_still_reports_completed_not_failed(self):
        call = self._retry_write()
        status = next(k for k in call.keywords if k.arg == "status")

        assert isinstance(status.value, ast.Constant) and status.value.value == "completed"

    def test_the_retry_path_is_not_routed_through_the_classifier(self):
        source = self.RETRY_SOURCE.read_text()
        retry_start = source.index("def retry_pdf_page")
        retry_body = source[retry_start:]
        end = retry_body.find("\ndef ", 1)
        retry_body = retry_body[:end if end != -1 else len(retry_body)]

        assert "safe_failure_message" not in retry_body


# ======================================================================================
# N — the page-level parse-failure vocabulary is untouched
# ======================================================================================
class TestTheParseFailureVocabularyIsUntouched:
    """The brief forbids introducing `EXTRACTION_PARSE_FAILED`. Coverage, retry and
    project status are driven by `PARSE_FAILED` / `PAGE_PARSE_FAILED`, which are a
    different mechanism reaching different columns; the new vocabulary must not have
    collided with it."""

    def test_the_coverage_and_status_constants_are_unchanged(self):
        assert PAGE_STATUS_PARSE_FAILED == "PARSE_FAILED"
        assert BLOCKER_PAGE_PARSE_FAILED == "PAGE_PARSE_FAILED"

    def test_no_parse_failure_code_was_created(self):
        for forbidden in ("EXTRACTION_PARSE_FAILED", "EXTRACTION_NOT_CONFIGURED"):
            assert forbidden not in FAILURE_CLASSIFICATIONS
            assert forbidden not in PERSISTED_MESSAGE_FOR
            assert not any(m.startswith(forbidden) for m in SAFE_FAILURE_MESSAGES)

    def test_the_milestone_created_no_module_wide_parse_failed_name(self):
        """The forbidden name must not appear anywhere in the new module, in any form."""
        module_source = (APP_DIR / "validation" / "failure_classification.py").read_text()
        assert "PARSE_FAILED" not in module_source


# ======================================================================================
# S — the two vocabularies are disjoint
# ======================================================================================
class TestTheVocabulariesAreDisjoint:
    def test_no_classification_collides_with_the_coverage_vocabulary(self):
        for message in SAFE_FAILURE_MESSAGES:
            for other in (PAGE_STATUS_PARSE_FAILED, BLOCKER_PAGE_PARSE_FAILED, "PARSED",
                          "ANALYSED", "NOT_ANALYSED", "MATCHED", "NONE"):
                assert other not in message

    def test_no_classification_collides_with_the_input_refusal_vocabulary(self):
        """`EXTRACTION_INPUT_REFUSED_*` is a request-validation vocabulary returned to a
        caller. It is never persisted here, and the two must not be confusable."""
        for message in SAFE_FAILURE_MESSAGES:
            assert not message.startswith("EXTRACTION_INPUT_REFUSED")
            assert "REFUSED" not in message

        assert not (SAFE_FAILURE_MESSAGES & set(EXTRACTION_INPUT_REFUSALS))

    def test_the_classification_codes_are_exactly_the_eight_the_brief_locked(self):
        assert set(FAILURE_CLASSIFICATIONS) == {
            "EXTRACTION_STORAGE_UNAVAILABLE",
            "EXTRACTION_STORE_FAILED",
            "EXTRACTION_PROVIDER_AUTH",
            "EXTRACTION_PROVIDER_CONFIG",
            "EXTRACTION_PROVIDER_UNAVAILABLE",
            "EXTRACTION_SOURCE_UNREADABLE",
            "EXTRACTION_REPORT_FAILED",
            "EXTRACTION_UNEXPECTED",
        }

    def test_the_unreachable_code_is_documented_rather_than_removed(self):
        """`EXTRACTION_STORE_FAILED` is currently unreachable: the only output-store
        exceptions SteelSpec sees are `StorageApiError`, which cannot be attributed to the
        upload rather than the download. The brief sanctions leaving it; removing it would
        silently narrow the locked vocabulary instead."""
        assert EXTRACTION_STORE_FAILED in FAILURE_CLASSIFICATIONS
        assert EXTRACTION_STORE_FAILED not in {
            classify_failure(exc) for exc in (
                StorageApiError("x", "500", 500),
                httpx.HTTPStatusError(
                    "x",
                    request=httpx.Request("GET", "https://s.example/x"),
                    response=httpx.Response(500, request=httpx.Request("GET", "https://s.example/x")),
                ),
            )
        }


# ======================================================================================
# O / P / Q — the report boundary
# ======================================================================================
class TestTheReportBoundary:
    @staticmethod
    def _report_client(monkeypatch, exc):
        """The REAL `/generate-report/{project_id}` route, with only the report builder
        and the authorization seam substituted."""
        monkeypatch.setattr(main_module, "build_and_store_report", _raise(exc))
        monkeypatch.setattr(main_module, "_authorized_project",
                            lambda project_id, reviewer: {"id": project_id, "user_id": "u-1"})
        main_module.app.dependency_overrides[main_module.require_reviewer] = lambda: {"id": "r-1"}
        return TestClient(main_module.app, raise_server_exceptions=False)

    def test_o_a_report_failure_preserves_its_own_identity(self, monkeypatch):
        """`build_and_store_report` re-raises as `ReportGenerationFailed` with the original
        attached as its cause, so the classification names the report step rather than
        collapsing it into the catch-all."""
        original = ExtractionFailed(GENERIC_SENTINEL)
        monkeypatch.setattr(main_module, "generate_report_pdf", _raise(original))
        monkeypatch.setattr(main_module, "upload_and_record",
                            lambda **kwargs: (_ for _ in ()).throw(original))
        client = _RecordingClient()
        monkeypatch.setattr(main_module, "supabase", client)

        with pytest.raises(ReportGenerationFailed) as raised:
            main_module.build_and_store_report("p-1", "u-1")

        assert raised.value.__cause__ is original
        assert safe_failure_message(raised.value) == PERSISTED_MESSAGE_FOR[EXTRACTION_REPORT_FAILED]
        # Report generation writes no status anywhere; a failed one writes nothing at all.
        assert client.writes == []

    def test_o_the_report_failure_message_carries_no_exception_text(self, monkeypatch):
        original = ExtractionFailed(GENERIC_SENTINEL)
        monkeypatch.setattr(main_module, "generate_report_pdf", _raise(original))
        client = _RecordingClient()
        monkeypatch.setattr(main_module, "supabase", client)

        with pytest.raises(ReportGenerationFailed) as raised:
            main_module.build_and_store_report("p-1", "u-1")

        assert GENERIC_SENTINEL not in str(raised.value)

    def test_p_the_report_http_response_carries_no_external_exception_text(self, monkeypatch):
        """`app/main.py:620` before Phase B put `f"{e}"` into a response body. That body is
        read by a client, so it is subject to the same rule as the persisted column."""
        exc = ExtractionFailed(GENERIC_SENTINEL)
        client = self._report_client(monkeypatch, exc)
        try:
            response = client.post("/generate-report/p-1", headers={"Authorization": "Bearer x"})
        finally:
            main_module.app.dependency_overrides.clear()

        assert response.status_code == 500
        assert GENERIC_SENTINEL not in response.text
        detail = response.json()["detail"]
        assert detail in SAFE_FAILURE_MESSAGES
        assert detail == PERSISTED_MESSAGE_FOR[EXTRACTION_UNEXPECTED]

    def test_p_the_response_detail_is_a_classification_for_every_failure_kind(self, monkeypatch):
        for exc, expected in (
            (ExtractionFailed(GENERIC_SENTINEL), EXTRACTION_UNEXPECTED),
            (StorageDownloadTimeout(URL_SENTINEL), EXTRACTION_STORAGE_UNAVAILABLE),
        ):
            client = self._report_client(monkeypatch, exc)
            try:
                response = client.post("/generate-report/p-1",
                                       headers={"Authorization": "Bearer x"})
            finally:
                main_module.app.dependency_overrides.clear()

            assert response.status_code == 500
            assert response.json()["detail"] == PERSISTED_MESSAGE_FOR[expected]
            for sentinel in ALL_SENTINELS:
                assert sentinel not in response.text

    def test_q_the_operator_still_gets_the_traceback_at_the_report_boundary(self, monkeypatch, caplog):
        """Phase A's property, restated at this boundary: the detail was REDIRECTED to the
        operator stream, not deleted. A fix that removed the leak by removing the
        diagnostic would have been a regression dressed as a security improvement."""
        import logging

        original = ExtractionFailed(GENERIC_SENTINEL)
        client = self._report_client(monkeypatch, original)
        try:
            with caplog.at_level(logging.ERROR):
                client.post("/generate-report/p-1", headers={"Authorization": "Bearer x"})
        finally:
            main_module.app.dependency_overrides.clear()

        records = [r for r in caplog.records if r.name == "app.main"]
        assert len(records) == 1
        assert records[0].levelno == logging.ERROR
        assert records[0].getMessage() == "SteelSpec report generation failed"
        assert records[0].exc_info is not None

        # The operator-visible rendering carries the whole cause chain, sentinel included.
        rendered = "".join(traceback.format_exception(*records[0].exc_info))
        assert GENERIC_SENTINEL in rendered
        # And the record's own message still interpolates nothing.
        assert GENERIC_SENTINEL not in records[0].getMessage()

    def test_the_route_no_longer_interpolates_a_raw_exception(self):
        """The exact line the brief named, checked at the source rather than at runtime,
        because a future edit could reintroduce it on a path no test drives."""
        source = (APP_DIR / "main.py").read_text()

        assert 'detail=f"{exc}"' not in source
        assert 'detail=f"{e}"' not in source
        assert "detail=str(exc)" not in source
        assert "detail=repr(" not in source
        assert "detail=safe_failure_message(exc)" in source


# ======================================================================================
# R — the static guard: every error_message write site, found by parsing the tree
# ======================================================================================
class TestEveryErrorMessageWriterIsFrozen:
    """The dynamic tests prove today's five writers are safe. This proves there are only
    five, that each one is one of two permitted shapes, and that a sixth cannot appear
    without this test failing."""

    # The only names a SteelSpec-owned f-string is allowed to interpolate, each paired
    # with what must be the SOLE thing binding it. A name is not admitted on trust, and
    # backing it with something else is a one-line change — so the guard resolves the
    # binding rather than the spelling.
    #
    #   format_label  must be bound by a call to `_safe_source_format_label`, which
    #                 returns a short ASCII alphanumeric token or the fixed word
    #                 "unrecognised" — never the stored value as it arrived.
    #   page          must be bound by the retry plan's own integer `page_number`
    #                 (`page = plan.page_number`), which the retry path derives from the
    #                 record, not from any external text.
    ADMITTED_INTERPOLATIONS = {
        "format_label": ("call", "_safe_source_format_label"),
        "page": ("attribute", "page_number"),
    }

    @staticmethod
    def _write_sites() -> list[tuple[str, int, ast.expr]]:
        """Every expression that can reach an `error_message` column, anywhere in `app/`."""
        sites = []
        for path in sorted(APP_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                # `update_*(..., error_message=...)` and `dict(error_message=...)`
                if isinstance(node, ast.keyword) and node.arg == "error_message":
                    sites.append((str(path.relative_to(REPO_ROOT)), node.value.lineno, node.value))
                # `{"error_message": ...}`
                elif isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if isinstance(key, ast.Constant) and key.value == "error_message":
                            sites.append((str(path.relative_to(REPO_ROOT)), value.lineno, value))
        return sites

    @staticmethod
    def _is_classifier_call(node: ast.expr) -> bool:
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "safe_failure_message")

    @classmethod
    def _solely_bound_names(cls, path: pathlib.Path) -> dict[str, ast.expr]:
        """Every name in the file that is assigned, and the single expression binding it.

        `app/pipeline.py` writes `error_message=classification` after binding it one line
        earlier, so that its two columns cannot disagree. Resolving the binding is the
        difference between a guard that models the code and one that models a spelling.
        A name assigned more than once, or from more than one distinct expression, is
        omitted — so a name that is ever rebound cannot be admitted on the strength of one
        good assignment.
        """
        tree = ast.parse(path.read_text(), filename=str(path))
        assigned: dict[str, set[str]] = {}
        values: dict[str, ast.expr] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, set()).add(ast.dump(node.value))
                        values[target.id] = node.value

        return {name: values[name] for name, spellings in assigned.items()
                if len(spellings) == 1}

    @classmethod
    def _parameter_names(cls, path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                names.update(p.arg for p in a.posonlyargs + a.args + a.kwonlyargs)
        return names

    @classmethod
    def _resolve(cls, path: pathlib.Path, value: ast.expr) -> ast.expr:
        """Follow a name to its sole binding, so a caller can judge the binding rather than
        the spelling. An unbound or multiply-bound name resolves to itself and is then
        judged on its own merits — which, for a bare name, it fails."""
        if isinstance(value, ast.Name):
            binding = cls._solely_bound_names(path).get(value.id)
            if binding is not None:
                return binding
        return value

    @classmethod
    def _binding_backs_the_name(cls, name: str, binding: ast.expr) -> bool:
        expected = cls.ADMITTED_INTERPOLATIONS.get(name)
        if expected is None:
            return False
        kind, target = expected
        if kind == "call":
            return (isinstance(binding, ast.Call) and isinstance(binding.func, ast.Name)
                    and binding.func.id == target)
        if kind == "attribute":
            return isinstance(binding, ast.Attribute) and binding.attr == target
        return False  # pragma: no cover - a guard on the guard

    @classmethod
    def _admitted_interpolation_names(cls, path: pathlib.Path) -> set[str]:
        """The interpolated names at this file's write sites that are genuinely admitted,
        with what backs each one actually verified."""
        solely = cls._solely_bound_names(path)
        admitted = set()

        for p, _lineno, raw in cls._write_sites():
            if p != str(path.relative_to(REPO_ROOT)) or not isinstance(raw, ast.JoinedStr):
                continue
            for part in raw.values:
                if not isinstance(part, ast.FormattedValue):
                    continue
                if not isinstance(part.value, ast.Name):
                    continue
                binding = solely.get(part.value.id)
                if binding is not None and cls._binding_backs_the_name(part.value.id, binding):
                    admitted.add(part.value.id)
        return admitted

    def test_the_guard_finds_the_six_known_writer_sites(self):
        """If this count changes, a writer was added or removed and every other test in
        this class has to be re-read before the number is updated. Six is the number the
        Phase B brief names as the existing set."""
        filenames = sorted({path for path, _, _ in self._write_sites()})
        assert filenames == ["app/main.py", "app/pipeline.py"]
        assert len(self._write_sites()) == 6

    def test_every_site_is_a_fixed_literal_or_the_classifier(self):
        unexplained = []
        for path, lineno, raw_value in self._write_sites():
            module = REPO_ROOT / path
            value = self._resolve(module, raw_value)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                continue  # a fixed SteelSpec literal
            if self._is_classifier_call(value):
                continue  # the frozen codomain, directly or through a resolved binding
            if isinstance(value, ast.JoinedStr):
                names = {
                    part.value.id
                    for part in value.values
                    if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name)
                }
                opaque = [
                    part for part in value.values
                    if isinstance(part, ast.FormattedValue) and not isinstance(part.value, ast.Name)
                ]
                admitted = self._admitted_interpolation_names(module)
                if not opaque and names and names <= admitted:
                    continue  # an f-string over admitted SteelSpec-side values only
            unexplained.append(f"{path}:{lineno}")

        assert unexplained == [], (
            "these error_message sites are neither a fixed literal nor safe_failure_message "
            "nor an f-string over admitted values: " + ", ".join(unexplained)
        )

    def test_the_only_bound_names_are_the_ones_the_writers_use(self):
        """Named explicitly, so admitting a second bound name is a deliberate edit here
        rather than a silent widening of the rule."""
        pipeline_bound = {
            name for name, binding in self._solely_bound_names(APP_DIR / "pipeline.py").items()
            if self._is_classifier_call(binding)
        }
        main_bound = {
            name for name, binding in self._solely_bound_names(APP_DIR / "main.py").items()
            if self._is_classifier_call(binding)
        }
        assert pipeline_bound == {"classification"}
        assert main_bound == set()

    def test_the_only_interpolated_names_are_the_admitted_ones_and_are_backed_correctly(self):
        """The names are checked, AND what backs them. This is the assertion that catches a
        one-line change making `format_label` the raw stored value: the name stays legal,
        the binding stops being."""
        for module in (APP_DIR / "main.py", APP_DIR / "pipeline.py"):
            admitted = self._admitted_interpolation_names(module)
            assert admitted <= set(self.ADMITTED_INTERPOLATIONS), (
                f"{module.name} interpolates {admitted - set(self.ADMITTED_INTERPOLATIONS)}"
            )

        # And, named: the two actual interpolation sites and their backings.
        assert self._admitted_interpolation_names(APP_DIR / "main.py") == {"format_label"}
        assert self._admitted_interpolation_names(APP_DIR / "pipeline.py") == {"page"}

    def test_the_binding_that_admits_format_label_is_the_normalizer(self):
        """Directly, on the one binding the whole source-format argument rests on."""
        binding = self._solely_bound_names(APP_DIR / "main.py")["format_label"]

        assert isinstance(binding, ast.Call)
        assert isinstance(binding.func, ast.Name)
        assert binding.func.id == "_safe_source_format_label"
        assert [a.id for a in binding.args if isinstance(a, ast.Name)] == ["source_format"]

    # The readers the brief forbids. Attribute forms, then the two builtins.
    FORBIDDEN_ATTRS = frozenset({"args", "message", "details", "hint", "json", "_raw_error",
                                 "body", "text", "response"})
    FORBIDDEN_BUILTINS = frozenset({"str", "repr"})
    # What an exception is plausibly called at a site that is about to persist it.
    EXCEPTION_NAMES = frozenset({"exc", "e", "err", "error", "exception", "cause"})

    @classmethod
    def _forbidden_reads(cls, source: str, filename: str) -> list[str]:
        """Code, not prose.

        The classifier's docstring deliberately NAMES every forbidden reader, so a
        substring search over the file text would fail on the documentation of the rule.
        This walks the parse tree instead, so prose is invisible to it and only an actual
        read can be reported."""
        found = []
        for node in ast.walk(ast.parse(source, filename=filename)):
            if isinstance(node, ast.Attribute) and node.attr in cls.FORBIDDEN_ATTRS:
                base = node.value.id if isinstance(node.value, ast.Name) else None
                if base in cls.EXCEPTION_NAMES:
                    found.append(f"{filename}:{node.lineno} reads .{node.attr}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in cls.FORBIDDEN_BUILTINS):
                for arg in node.args:
                    name = arg.id if isinstance(arg, ast.Name) else None
                    if name in cls.EXCEPTION_NAMES:
                        found.append(f"{filename}:{node.lineno} calls {node.func.id}({name})")
        return found

    def test_the_classifier_reads_no_exception_text(self):
        source = (APP_DIR / "validation" / "failure_classification.py").read_text()
        found = self._forbidden_reads(source, "app/validation/failure_classification.py")

        assert found == [], "the classifier reads external text: " + ", ".join(found)

        # And the docstring really does name them, so the check above is not vacuous by
        # having nothing to find — the rule is documented in the file it constrains.
        assert "`str(exc)`" in source and "`exc.details`" in source

    def test_no_write_site_reads_exception_text_either(self):
        """The same rule at the sites that actually persist, rather than only in the
        module that classifies."""
        found = []
        for path, lineno, value in self._write_sites():
            source = ast.get_source_segment((REPO_ROOT / path).read_text(), value) or ""
            # A site may be an implicitly concatenated multi-line f-string. Wrapping it in
            # parentheses makes the continuation lines legal standalone, which is exactly
            # how they are legal in the file that contains them.
            try:
                found.extend(self._forbidden_reads(
                    f"_ = (\n{source}\n)", f"{path}:{lineno}"))
            except SyntaxError as exc:  # pragma: no cover - a guard on the guard
                raise AssertionError(
                    f"could not parse the write site at {path}:{lineno}: {exc}"
                ) from exc

        assert found == [], "an error_message site reads external text: " + ", ".join(found)

    def test_the_classifier_is_the_only_source_of_a_persisted_classification(self):
        """The frozen codomain only binds if every writer goes through it. A writer that
        built a classification by any other route — its own map, its own concatenation —
        would bypass it while still looking safe at the call site."""
        importers = sorted(
            str(p.relative_to(REPO_ROOT))
            for p in APP_DIR.rglob("*.py")
            if "safe_failure_message" in p.read_text()
            and p.name != "failure_classification.py"
        )
        # Declared here so widening the blast radius is a deliberate edit: `main.py` and
        # `pipeline.py` import it; `main.py` also names it in a comment.
        assert importers == ["app/main.py", "app/pipeline.py"]

        defined_in = [
            str(p.relative_to(REPO_ROOT))
            for p in APP_DIR.rglob("*.py")
            if "def safe_failure_message" in p.read_text()
        ]
        assert defined_in == ["app/validation/failure_classification.py"]
