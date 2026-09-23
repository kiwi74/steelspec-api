"""
Milestone 7AU — the capture-harness boundary tests (scripts/capture_real_pdf_extraction.py).

The harness runs the REAL vision pipeline; the boundary it owns is page-image
PERSISTENCE only. These tests pin that boundary:

  - without usable Supabase credentials (or without a valid storage drawing
    UUID), the production Supabase upload is never attempted — rendered pages
    are saved locally and the genuine vision call still runs;
  - a human-readable drawing id (e.g. "SELBY-C1136") never selects the
    Supabase backend and never reaches a UUID-only repository field;
  - with Supabase configured AND a UUID-shaped storage drawing id, the
    production upload_page_image is used UNCHANGED — production upload
    behaviour and UUID requirements are preserved;
  - when the vision call fails, no extraction JSON is written (nothing is
    ever fabricated); the error propagates instead.

The vision call itself is never mocked in the real path; the analyzer stub
here stands in for app.ai_analysis.pdf_vision_analyzer only so these tests
need no credentials, no poppler and no network.
"""
import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.cad_engine.fabricator_acceptance as fa
from scripts.capture_real_pdf_extraction import (
    capture,
    choose_image_backend,
    is_uuid_shaped,
    storage_drawing_identity,
)
from tests.test_fabricator_acceptance import MATERIAL_FINDING
from tests.test_material_specification import _q12_of, negative_journey
from tests.test_project_extraction_intake import needs_real_capture

# A shape-valid UUID used ONLY as a test technical identifier — it is not a
# real drawings-table row id and represents nothing.
VALID_UUID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
HUMAN_DRAWING_ID = "SELBY-C1136"


@dataclasses.dataclass
class StubPageExtraction:
    page_number: int = 1
    drawing_number: str | None = None
    drawing_title: str | None = None
    revision: str | None = None
    raw_members: list = dataclasses.field(default_factory=list)
    raw_connections: list = dataclasses.field(default_factory=list)
    parse_failed: bool = False


def _stub_analyzer(pages=(), fail_with=None, api_key="test-key"):
    """An analyzer stand-in whose upload_page_image is the production seam:
    capture() must either use it unchanged (Supabase backend) or replace it
    (local backend). Like the real analyzer, it uploads each rendered page
    before that page's vision call; the vision results are the given pages."""
    calls = {}
    upload_calls = []

    def recording_upload(*args, **kwargs):
        upload_calls.append((args, kwargs))
        return "stored/path"

    def analyze_pdf_pages(filepath, user_id, project_id, drawing_id, max_pages, first_page=1):
        calls["analyze"] = dict(
            filepath=filepath, user_id=user_id, project_id=project_id,
            drawing_id=drawing_id, max_pages=max_pages, first_page=first_page,
        )
        if fail_with is not None:
            raise fail_with
        for page in pages:
            holder["module"].upload_page_image(
                user_id, project_id, drawing_id, page.page_number, b""
            )
        return list(pages)

    holder = {}
    module = SimpleNamespace(
        ANTHROPIC_API_KEY=api_key,
        upload_page_image=recording_upload,
        analyze_pdf_pages=analyze_pdf_pages,
    )
    holder["module"] = module
    calls["original_upload"] = recording_upload
    calls["upload_calls"] = upload_calls
    return module, calls


def _run(tmp_path, monkeypatch, *, supabase=False, storage_drawing_uuid=None,
         api_key="test-key", pages=(), fail_with=None):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    if supabase:
        monkeypatch.setenv("SUPABASE_URL", "https://placeholder.supabase.co")
        monkeypatch.setenv(
            "SUPABASE_SERVICE_ROLE_KEY",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
            ".fake-test-signature",
        )
    pdf = tmp_path / "real.pdf"
    pdf.write_bytes(b"not really a pdf - the stub never renders it")
    capture_path = tmp_path / "capture.json"
    module, calls = _stub_analyzer(pages=pages, fail_with=fail_with, api_key=api_key)
    return pdf, capture_path, module, calls


class TestLocalBackendBoundary:
    def test_without_supabase_credentials_the_upload_is_never_attempted(self, tmp_path, monkeypatch):
        pdf, capture_path, module, calls = _run(
            tmp_path, monkeypatch, pages=[StubPageExtraction(page_number=1)]
        )
        capture(pdf, capture_path, user_id="7au", project_id="PROJ-7AU-SELBY",
                drawing_id=HUMAN_DRAWING_ID, storage_drawing_uuid=None, max_pages=1,
                analyzer_module=module)
        assert calls["upload_calls"] == []            # the production seam was never called
        assert module.upload_page_image is not calls["original_upload"]  # replaced by local storage
        saved = tmp_path / "capture_page_images" / "page-1.png"
        assert saved.exists() and saved.read_bytes() == b""  # the local upload wrote the image
        assert json.loads(capture_path.read_text()) == [{"page_number": 1, "drawing_number": None,
                                                        "drawing_title": None, "revision": None,
                                                        "raw_members": [], "raw_connections": [],
                                                        "parse_failed": False}]

    def test_local_capture_still_performs_the_vision_call(self, tmp_path, monkeypatch):
        pdf, capture_path, module, calls = _run(
            tmp_path, monkeypatch,
            pages=[StubPageExtraction(page_number=1), StubPageExtraction(page_number=2)],
        )
        capture(pdf, capture_path, user_id="7au", project_id="PROJ-7AU-SELBY",
                drawing_id=HUMAN_DRAWING_ID, storage_drawing_uuid=None, max_pages=5,
                analyzer_module=module)
        assert calls["analyze"] == dict(
            filepath=str(pdf), user_id="7au", project_id="PROJ-7AU-SELBY",
            drawing_id=HUMAN_DRAWING_ID, max_pages=5, first_page=1,
        )
        data = json.loads(capture_path.read_text())
        assert [p["page_number"] for p in data] == [1, 2]

    def test_env_is_left_exactly_as_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        pdf, capture_path, module, calls = _run(tmp_path, monkeypatch)  # env already absent
        assert "SUPABASE_URL" not in os.environ
        capture(pdf, capture_path, user_id="u", project_id="p",
                drawing_id=HUMAN_DRAWING_ID, storage_drawing_uuid=None, max_pages=1,
                analyzer_module=module)
        assert "SUPABASE_URL" not in os.environ and "SUPABASE_SERVICE_ROLE_KEY" not in os.environ


class TestIdentityBoundary:
    def test_uuid_shape_detection(self):
        assert is_uuid_shaped(VALID_UUID) is True
        assert is_uuid_shaped(HUMAN_DRAWING_ID) is False
        assert is_uuid_shaped("3f2504e04f8941d39a0c0305e82c3301") is False  # no dashes
        assert is_uuid_shaped("") is False and is_uuid_shaped(None) is False

    def test_human_readable_drawing_id_never_selects_the_supabase_backend(self, tmp_path):
        upload, backend, _ = choose_image_backend(True, HUMAN_DRAWING_ID, tmp_path)
        assert backend == "local" and upload is not None
        upload, backend, _ = choose_image_backend(True, None, tmp_path)
        assert backend == "local" and upload is not None

    def test_configured_supabase_with_a_valid_uuid_selects_the_production_backend(self, tmp_path):
        upload, backend, _ = choose_image_backend(True, VALID_UUID, tmp_path)
        assert backend == "supabase" and upload is None

    def test_the_two_identities_are_never_conflated(self):
        assert storage_drawing_identity("supabase", HUMAN_DRAWING_ID, VALID_UUID) == VALID_UUID
        assert storage_drawing_identity("local", HUMAN_DRAWING_ID, None) == HUMAN_DRAWING_ID


class TestConfiguredSupabaseUnchanged:
    def test_with_supabase_and_uuid_the_production_upload_is_used_unchanged(
        self, tmp_path, monkeypatch
    ):
        pdf, capture_path, module, calls = _run(
            tmp_path, monkeypatch, supabase=True,
            pages=[StubPageExtraction(page_number=1)],
        )
        capture(pdf, capture_path, user_id="7au", project_id="PROJ-7AU-SELBY",
                drawing_id=HUMAN_DRAWING_ID, storage_drawing_uuid=VALID_UUID, max_pages=1,
                analyzer_module=module)
        assert module.upload_page_image is calls["original_upload"]  # NOT replaced
        assert calls["analyze"]["drawing_id"] == VALID_UUID     # only the UUID is handed over
        assert len(calls["upload_calls"]) == 1                  # the production seam ran once
        args, _ = calls["upload_calls"][0]
        assert args == ("7au", "PROJ-7AU-SELBY", VALID_UUID, 1, b"")
        assert not (tmp_path / "capture_page_images").exists()  # no local fallback was created


class TestNoFabrication:
    def test_when_the_vision_call_fails_no_extraction_json_is_written(self, tmp_path, monkeypatch):
        pdf, capture_path, module, calls = _run(
            tmp_path, monkeypatch, fail_with=RuntimeError("vision api failure")
        )
        with pytest.raises(RuntimeError, match="vision api failure"):
            capture(pdf, capture_path, user_id="u", project_id="p",
                    drawing_id=HUMAN_DRAWING_ID, storage_drawing_uuid=None, max_pages=5,
                    analyzer_module=module)
        assert not capture_path.exists()
        image_dir = tmp_path / "capture_page_images"
        assert not image_dir.exists() or not any(image_dir.iterdir())

    def test_missing_api_key_fails_before_any_vision_call_or_write(self, tmp_path, monkeypatch):
        pdf, capture_path, module, calls = _run(tmp_path, monkeypatch, api_key="")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            capture(pdf, capture_path, user_id="u", project_id="p",
                    drawing_id=HUMAN_DRAWING_ID, storage_drawing_uuid=None, max_pages=5,
                    analyzer_module=module)
        assert "analyze" not in calls
        assert not capture_path.exists()


class TestNegativeProofUnaffected:
    @needs_real_capture  # the genuine Arkles capture marker (7Y)
    def test_arkles_negative_proof_is_unchanged_by_the_capture_boundary(
        self, negative_journey
    ):
        result = negative_journey.result
        assert result.status == fa.ACCEPTANCE_STATUS_NOT_ACCEPTED
        q12 = _q12_of(result)
        assert q12.answer == fa.ANSWER_FAIL
        assert q12.finding == MATERIAL_FINDING
