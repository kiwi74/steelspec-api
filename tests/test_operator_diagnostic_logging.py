"""
Wave 3H Phase A — the operator diagnostic destination at the two existing failure
boundaries (`app.main.run_extraction`, `app.pipeline.parse_pdf_and_save`).

WHAT THIS PHASE IS
==================
One behaviour is added and nothing else: when an unexpected exception reaches either
existing broad handler, an ERROR-level record carrying `exc_info` is emitted through the
standard library's `logging`. The traceback is the diagnostic; the record's message is a
fixed SteelSpec-owned sentence that interpolates nothing.

WHAT WAVE 3J PHASE B CHANGED HERE
=================================
When this file was written (Wave 3H Phase A) the handlers still persisted `str(e)[:500]`,
and the tests below pinned that value to prove Phase A had not moved it. Phase B has now
moved it deliberately: the persisted value is the SteelSpec-owned classification, and the
exception's text is asserted ABSENT from it. The tests keep their original job — proving
the log and the row are separate channels — and now prove it in both directions: the
traceback still carries the exception, and the row still does not.

WHAT IS REAL HERE
=================
  * The real `app.main.run_extraction` and the real `app.pipeline.parse_pdf_and_save`, with
    only their network/DB boundary substituted.
  * The real `logging` module and the real record objects pytest hands back.
  * The real logger objects the two modules expose (`app.main.logger`, `app.pipeline.logger`).

WHAT IS NOT HERE
================
  * No provider is called, no response body is constructed, no credential appears, and no
    Supabase or storage call is made. The only "client" is a recorder.
  * No log content is read from any deployment, and no Railway log is retrieved.
  * The sentinel exceptions are synthetic and local to this module.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import app.main as main_module
import app.pipeline as pipeline_module
from app.validation.failure_classification import (
    EXTRACTION_UNEXPECTED,
    PERSISTED_MESSAGE_FOR,
)

# The classification an exception this suite invents must fall into: it is
# deliberately a type nothing in SteelSpec recognises.
UNEXPECTED_MESSAGE = PERSISTED_MESSAGE_FOR[EXTRACTION_UNEXPECTED]

# The handlers' own sentences. Asserted verbatim, because a message that interpolated
# anything would stop being stable and an operator could no longer filter on it.
MAIN_MESSAGE = "SteelSpec extraction task failed"
PIPELINE_MESSAGE = "SteelSpec PDF drawing-set analysis failed"

# The boundary sentences every test in this file is built around. Each is the whole of
# the record's message: nothing is appended to it, so a sentinel placed in the raised
# exception can never reach it.
SENTINEL_TEXT = "sentinel-do-not-persist-please"


class ExtractionFailed(Exception):
    """A synthetic stand-in for any unexpected failure at either boundary."""


class _RecordingClient:
    """`supabase` as `app.main`'s background path uses it. Records every write."""

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
        return SimpleNamespace(data=[])


class _PipelineRepo:
    """`app.pipeline`'s repository as `parse_pdf_and_save` uses it before the handler.

    Only the calls that precede the boundary under test: the document, the set, the
    drawing and the run are created, and the two failure writes are recorded. Nothing
    here reaches a database.
    """

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


def _raise(exc):
    def _stand_in(*args, **kwargs):
        raise exc
    return _stand_in


def _run_main_boundary(monkeypatch, exc):
    """Drive the REAL `run_extraction` so the sentinel reaches its own handler."""
    client = _RecordingClient()
    monkeypatch.setattr(main_module, "download_from_uploads", _raise(exc))
    monkeypatch.setattr(main_module, "supabase", client)
    main_module.run_extraction("p-1", "drawings/plan.pdf", "PDF", "u-1")
    return client


def _run_pipeline_boundary(monkeypatch, exc):
    """Drive the REAL `parse_pdf_and_save` so the sentinel reaches its own handler."""
    repo = _PipelineRepo()
    monkeypatch.setattr(pipeline_module, "repo", repo)
    monkeypatch.setattr(pipeline_module, "source_bytes", lambda source: b"%PDF-1.4")
    monkeypatch.setattr(pipeline_module, "_run_pipeline", _raise(exc))
    with pytest.raises(type(exc)):
        pipeline_module.parse_pdf_and_save("plan.pdf", "p-1", "u-1", "drawings/plan.pdf")
    return repo


# ======================================================================================
# 1. The main boundary emits the record
# ======================================================================================
class TestTheExtractionBoundaryLogs:

    def test_an_error_record_is_emitted_with_exception_information(self, monkeypatch, caplog):
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_main_boundary(monkeypatch, exc)

        records = [r for r in caplog.records if r.name == "app.main"]
        assert len(records) == 1
        record = records[0]

        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"
        # `exc_info` present is what gives the operator the traceback — the whole point
        # of this phase. Asserted by identity, so the record provably carries THIS failure.
        assert record.exc_info is not None
        assert record.exc_info[1] is exc

    def test_the_message_is_the_stable_steel_spec_sentence(self, monkeypatch, caplog):
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_main_boundary(monkeypatch, exc)

        record = next(r for r in caplog.records if r.name == "app.main")
        assert record.getMessage() == MAIN_MESSAGE

    def test_the_message_interpolates_nothing_from_the_exception(self, monkeypatch, caplog):
        """The message and the traceback are separate channels. This is the assertion
        that fails if anyone ever writes `logger.exception(str(e))` or an f-string."""
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_main_boundary(monkeypatch, exc)

        record = next(r for r in caplog.records if r.name == "app.main")
        assert SENTINEL_TEXT not in record.getMessage()
        assert exc.args == (SENTINEL_TEXT,)

    def test_the_traceback_is_what_carries_the_exception_text(self, monkeypatch, caplog):
        """The complement of the test above: the operator is not losing the detail, it is
        simply not in the message. `exc_info` renders it."""
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_main_boundary(monkeypatch, exc)

        record = next(r for r in caplog.records if r.name == "app.main")
        assert SENTINEL_TEXT in caplog.text

    def test_the_logger_is_the_modules_own(self):
        assert main_module.logger.name == "app.main"

    def test_no_handler_was_configured_on_the_logger(self):
        """No configuration was added, which is why the record reaches the process's own
        stream through the standard library's default handling."""
        assert main_module.logger.handlers == []
        assert main_module.logger.propagate is True


# ======================================================================================
# 2. The pipeline boundary emits the record, and the exception still propagates
# ======================================================================================
class TestThePipelineBoundaryLogs:

    def test_an_error_record_is_emitted_with_exception_information(self, monkeypatch, caplog):
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_pipeline_boundary(monkeypatch, exc)

        records = [r for r in caplog.records if r.name == "app.pipeline"]
        assert len(records) == 1
        record = records[0]

        assert record.levelno == logging.ERROR
        assert record.exc_info is not None
        assert record.exc_info[1] is exc

    def test_the_message_is_the_stable_steel_spec_sentence(self, monkeypatch, caplog):
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_pipeline_boundary(monkeypatch, exc)

        record = next(r for r in caplog.records if r.name == "app.pipeline")
        assert record.getMessage() == PIPELINE_MESSAGE

    def test_the_message_interpolates_nothing_from_the_exception(self, monkeypatch, caplog):
        exc = ExtractionFailed(SENTINEL_TEXT)
        with caplog.at_level(logging.ERROR):
            _run_pipeline_boundary(monkeypatch, exc)

        record = next(r for r in caplog.records if r.name == "app.pipeline")
        assert SENTINEL_TEXT not in record.getMessage()

    def test_the_logger_is_the_modules_own(self):
        assert pipeline_module.logger.name == "app.pipeline"

    def test_no_handler_was_configured_on_the_logger(self):
        assert pipeline_module.logger.handlers == []
        assert pipeline_module.logger.propagate is True

    def test_the_exception_is_still_re_raised_after_the_record(self, monkeypatch, caplog):
        """Logging is added to the handler, not substituted for the `raise`: the outer
        boundary must still see the same exception object.

        Driven inline rather than through the helper, because the helper asserts the raise
        itself and would leave this test with nothing to catch."""
        exc = ExtractionFailed(SENTINEL_TEXT)
        monkeypatch.setattr(pipeline_module, "repo", _PipelineRepo())
        monkeypatch.setattr(pipeline_module, "source_bytes", lambda source: b"%PDF-1.4")
        monkeypatch.setattr(pipeline_module, "_run_pipeline", _raise(exc))

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ExtractionFailed) as raised:
                pipeline_module.parse_pdf_and_save(
                    "plan.pdf", "p-1", "u-1", "drawings/plan.pdf")

        assert raised.value is exc
        # The record was emitted on the way out, before the re-raise.
        assert [r.name for r in caplog.records if r.name == "app.pipeline"] == ["app.pipeline"]


# ======================================================================================
# 3. Nothing about the persisted value moved in this phase
# ======================================================================================
class TestThePersistedWriteIsUnchanged:

    def test_the_extraction_boundary_persists_the_classification_not_the_text(self, monkeypatch):
        """Wave 3J Phase B. The exception this suite raises carries a sentinel; the row
        must carry the classification and none of the sentinel."""
        exc = ExtractionFailed(SENTINEL_TEXT)
        client = _run_main_boundary(monkeypatch, exc)

        assert client.writes == [
            ("projects", {"status": "failed", "error_message": UNEXPECTED_MESSAGE})
        ]
        assert SENTINEL_TEXT not in client.writes[0][1]["error_message"]
        assert str(exc)[:500] not in client.writes[0][1]["error_message"]

    def test_the_pipeline_boundary_persists_the_classification_not_the_text(self, monkeypatch):
        exc = ExtractionFailed(SENTINEL_TEXT)
        repo = _run_pipeline_boundary(monkeypatch, exc)

        assert repo.writes == [
            ("analysis_runs", {"status": "failed", "error_message": UNEXPECTED_MESSAGE}),
            ("drawing_sets", {"status": "failed", "error_message": UNEXPECTED_MESSAGE}),
        ]
        for _table, payload in repo.writes:
            assert SENTINEL_TEXT not in payload["error_message"]

    def test_the_two_boundaries_still_agree_on_one_failure(self, monkeypatch, caplog):
        """One exception, two writes, one value — and both columns get it from a single
        call, so two columns describing one failure cannot disagree about it."""
        exc = ExtractionFailed(SENTINEL_TEXT)
        repo = _PipelineRepo()

        monkeypatch.setattr(pipeline_module, "repo", repo)
        monkeypatch.setattr(pipeline_module, "source_bytes", lambda source: b"%PDF-1.4")
        monkeypatch.setattr(pipeline_module, "_run_pipeline", _raise(exc))
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ExtractionFailed):
                pipeline_module.parse_pdf_and_save(
                    "plan.pdf", "p-1", "u-1", "drawings/plan.pdf")

        values = {payload["error_message"] for _, payload in repo.writes}
        assert values == {UNEXPECTED_MESSAGE}
        assert [table for table, _ in repo.writes] == ["analysis_runs", "drawing_sets"]

    def test_the_steel_spec_owned_literals_are_unchanged(self, monkeypatch, caplog):
        """The two fixed sentences in `run_extraction` are SteelSpec's own words, are not
        derived from any exception, and are not touched by this milestone."""
        for source_format, expected in (
            ("IFC", "IFC extraction isn't wired up yet — DXF, DWG, and PDF are supported."),
            ("STEP", "Unsupported source format: STEP"),
        ):
            client = _RecordingClient()
            monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
            monkeypatch.setattr(main_module, "supabase", client)
            with caplog.at_level(logging.ERROR):
                main_module.run_extraction("p-1", "drawings/plan.dxf", source_format, "u-1")

            assert client.writes == [
                ("projects", {"status": "failed", "error_message": expected})
            ]
            # A handled branch is not a boundary failure: nothing is logged for it.
            assert [r for r in caplog.records if r.name == "app.main"] == []

    def test_a_hostile_source_format_cannot_reshape_the_message(self, monkeypatch):
        """The format comes from the project row and is interpolated into a sentence a
        reader sees, so it is admitted only as a short ASCII alphanumeric token. Anything
        else is replaced by a fixed word rather than reproduced.

        This is not exception text — but it is still a stored value being placed inside a
        SteelSpec sentence, and the same rule applies: the value may not reshape it.

        The two path-separator values are here because they are the ones that actually
        exercised the defect. `run_extraction` built a temp-file SUFFIX from the same
        value (`f".{source_format.lower()}"`), so `"a/b"` reached `NamedTemporaryFile`,
        which raised `FileNotFoundError` on a directory that does not exist — before the
        format branch was ever consulted. The row then recorded EXTRACTION_UNEXPECTED for
        a value that is not an exception at all. The format is now normalized once, at the
        top, so both of its uses — the suffix and the sentence — receive the same admitted
        token."""
        for hostile in (
            "STEP\nEXTRACTION_UNEXPECTED: forged",
            "<script>alert(1)</script>",
            "x" * 4096,
            "STEP' OR 1=1--",
            "a/b",
            "../../etc/passwd",
            None,
            12345,
            b"PDF",
        ):
            client = _RecordingClient()
            monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
            monkeypatch.setattr(main_module, "supabase", client)
            main_module.run_extraction("p-1", "drawings/plan.dxf", hostile, "u-1")

            assert client.writes == [
                ("projects", {"status": "failed",
                              "error_message": "Unsupported source format: unrecognised"})
            ], f"hostile format {hostile!r} reshaped the persisted row"

    def test_the_normalized_label_is_what_reaches_the_temp_file_name(self, monkeypatch):
        """The suffix and the sentence are the same token. Before Wave 3J Phase B the
        suffix was the RAW stored value, which made the project row able to shape a
        filesystem path as well as a sentence. Asserted on the real call, by capturing
        the suffix `NamedTemporaryFile` is handed."""
        seen: list[str] = []
        real_ntf = main_module.tempfile.NamedTemporaryFile

        def _capture(*args, **kwargs):
            seen.append(kwargs.get("suffix"))
            return real_ntf(*args, **kwargs)

        monkeypatch.setattr(main_module.tempfile, "NamedTemporaryFile", _capture)
        monkeypatch.setattr(main_module, "download_from_uploads", lambda *a, **k: b"x")
        monkeypatch.setattr(main_module, "supabase", _RecordingClient())

        main_module.run_extraction("p-1", "drawings/plan.dxf", "../../etc/passwd", "u-1")
        assert seen == [".unrecognised"]

        # A legitimate format still reaches the suffix unchanged. The branch this selects
        # is stopped at its first step, so no parser and no repository is reached.
        seen.clear()
        monkeypatch.setattr(main_module, "supabase", _RecordingClient())
        monkeypatch.setattr(main_module, "parse_dxf_and_save", _raise(ExtractionFailed("stop")))
        main_module.run_extraction("p-1", "drawings/plan.dxf", "DXF", "u-1")
        assert seen == [".dxf"]


# ======================================================================================
# 4. No external logging service, and no route that serves logs
# ======================================================================================
class TestNoNewDestinationWasIntroduced:

    def test_no_third_party_logging_dependency_is_installed(self):
        """The destination is the standard library's own stream handling. If a logging
        vendor were ever added, this is the test that should have to be changed."""
        import importlib.util

        for module in ("sentry_sdk", "logtail", "opentelemetry", "ddtrace", "loguru",
                       "structlog"):
            assert importlib.util.find_spec(module) is None

    def test_the_application_serves_no_log_route(self):
        paths = [route.path for route in main_module.app.routes]
        assert not [p for p in paths if "log" in p.lower()]
        assert not [p for p in paths if "diagnostic" in p.lower()]
        # The one route this milestone could plausibly have added and did not.
        assert "/logs" not in paths
        assert "/health" in paths

    def test_the_records_are_reachable_without_any_logging_configuration(self, monkeypatch, caplog):
        """Captured through pytest's own root handler, with nothing configured on the
        module loggers — the same condition the deployment runs under."""
        exc = ExtractionFailed(SENTINEL_TEXT)
        assert main_module.logger.handlers == []
        with caplog.at_level(logging.ERROR):
            _run_main_boundary(monkeypatch, exc)
        assert [r.name for r in caplog.records if r.name == "app.main"] == ["app.main"]
