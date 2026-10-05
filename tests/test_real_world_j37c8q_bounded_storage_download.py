"""
Milestone J37C-8Q — the bounded, fail-closed Supabase Storage download
(`app.storage_download`), and the pipeline ordering it must not change.

WHAT IS REAL HERE
=================
  * The transport is a REAL `httpx.Client` over a REAL socket to a REAL local HTTP
    server (`127.0.0.1`, port 0). So httpx's per-read deadline is enforced by the
    same httpcore code path that enforces it in production, at the value this
    module configures — not by a mock that raises on cue. The timing measurements
    in the stall and deadline tests are wall-clock measurements.
  * The bucket proxy is the REAL `storage3._sync.file_api.SyncBucketProxy` — the
    object `supabase.storage.from_(...)` returns — carrying the same attribute
    names (`id`, `_base_url`, `_headers`, `_client`) the production path uses, so
    the endpoint construction under test is production's own.
  * The pipeline tests drive the GENUINE `app.main.run_extraction` and
    `app.main.run_continuation`, with only the network boundary substituted.

WHAT IS NOT HERE
================
  * No live Supabase call, no credential of the real project, and no long sleep.
    The authorization header is the literal string `Bearer test-not-a-real-key`,
    the server is a scripted stand-in that answers every path it is given, and the
    bounds under test are injected as milliseconds-scale values. Nothing here
    writes to any database: the only "client" in the pipeline tests is a recorder.
  * No attempt to reproduce the exact production stall message. The 20 s deadline
    that J37C-8O hit surfaced from the TLS layer (CPython's `ssl` raises
    "The read operation timed out"); over plain HTTP the same deadline surfaces as
    `httpx.ReadTimeout` with an empty message. These tests assert the TYPE, the
    BOUND it enforces and the fail-closed CONSEQUENCE — never the string.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
import pytest
from httpx import Headers
from storage3._sync.file_api import SyncBucketProxy, relative_path_to_parts
from storage3.constants import DEFAULT_TIMEOUT as STORAGE3_DEFAULT_TIMEOUT
from yarl import URL

import app.main as main_module
# Wave 3J Phase B — the two assertions below used to pin the RAW exception text into
# `projects.error_message`. They now pin its absence and the safe classification
# instead: the exceptions they raise are unchanged, and their text is still the
# evidence, but it is evidence of what must NOT be persisted.
from app.validation.failure_classification import (
    EXTRACTION_STORAGE_UNAVAILABLE,
    PERSISTED_MESSAGE_FOR,
)
from app.storage_download import (
    SOURCE_BUCKET,
    STORAGE_CONNECT_TIMEOUT_SECONDS,
    STORAGE_DOWNLOAD_DEADLINE_SECONDS,
    STORAGE_READ_TIMEOUT_SECONDS,
    StorageDownloadTimeout,
    download_from_uploads,
)

OBJECT_PATH = "drawings/plan 17.08.pdf"
TEST_AUTH_HEADER = "Bearer test-not-a-real-key"
# The bytes the object body is built from: binary, with NULs, so any accidental
# text handling would show up as a difference rather than pass unnoticed.
PAYLOAD = b"%PDF-1.7\n" + bytes(range(256)) * 8 + b"\n%%EOF\n"


# ---------------------------------------------------------------------------
# A real HTTP server that serves one object body, following a script.
# ---------------------------------------------------------------------------
class _ScriptedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Without this, several small writes can be coalesced by the kernel and arrive
    # as one burst, which would hide the very gaps these tests are built around.
    disable_nagle_algorithm = True

    def log_message(self, *args):  # silence the per-request logging
        pass

    def do_GET(self):
        server = self.server
        server.paths.append(self.path)
        server.authorizations.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(server.content_length))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        for kind, value in server.steps:
            if kind == "stall":
                time.sleep(value)
                continue
            try:
                self.wfile.write(value)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # The client gave up on a stalled read — which is the point of the
                # stall tests. Stop serving this connection; do not report it.
                return


class _ScriptedObjectServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, steps, content_length):
        super().__init__(("127.0.0.1", 0), _ScriptedHandler)
        self.steps = steps
        self.content_length = content_length
        self.paths: list[str] = []
        self.authorizations: list[str | None] = []

    def handle_error(self, request, client_address):
        # A client that aborts mid-body is the expected outcome of the stall and
        # deadline tests: it is a result, not an error to print.
        pass


@contextlib.contextmanager
def _serving(steps, content_length):
    """A live server on an ephemeral port, torn down deterministically."""
    server = _ScriptedObjectServer(steps, content_length)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/storage/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _client_for(base_url, session):
    """A Supabase client whose storage session is `session`.

    The proxy is the real storage3 object, so `download_from_uploads` builds its
    URL, headers and request exactly as it does against the live client.
    """
    proxy = SyncBucketProxy(
        id=SOURCE_BUCKET,
        _base_url=URL(base_url + "/"),
        _headers=Headers({"Authorization": TEST_AUTH_HEADER}),
        _client=session,
    )

    def from_(bucket):
        assert bucket == SOURCE_BUCKET, bucket
        return proxy

    return SimpleNamespace(storage=SimpleNamespace(from_=from_))


def _local(base_url, **kwargs):
    """A client pointed at the local server, plus the session to close."""
    session = httpx.Client(**kwargs)
    return _client_for(base_url, session), session


# ---------------------------------------------------------------------------
# The bound this module replaces — pinned, so the comparison stays meaningful.
# ---------------------------------------------------------------------------
class TestTheBoundBeingReplaced:
    def test_storage3_still_defaults_to_the_20_second_read_deadline(self):
        """J37C-8P measured this. If it changes upstream, this module's choice of
        bounds must be re-justified, and that should start here."""
        assert STORAGE3_DEFAULT_TIMEOUT == 20
        assert STORAGE_READ_TIMEOUT_SECONDS > STORAGE3_DEFAULT_TIMEOUT
        assert STORAGE_DOWNLOAD_DEADLINE_SECONDS > STORAGE_READ_TIMEOUT_SECONDS

    def test_the_storage3_internals_reused_here_are_still_present(self):
        """The module reuses storage3's own endpoint construction rather than
        inventing a second one. If those internals move, this fails loudly here
        instead of silently at extraction time."""
        proxy = SyncBucketProxy(
            id="uploads",
            _base_url=URL("https://example.invalid/storage/v1/"),
            _headers=Headers({"Authorization": TEST_AUTH_HEADER}),
            _client=None,
        )
        for attribute in ("id", "_base_url", "_headers", "_client"):
            assert hasattr(proxy, attribute), attribute
        assert relative_path_to_parts("a/b c.pdf") == ("a", "b c.pdf")
        assert relative_path_to_parts("/a/b.pdf") == ("a", "b.pdf")


# ---------------------------------------------------------------------------
# A–D: the two bounds, over a real socket.
# ---------------------------------------------------------------------------
class TestTheRealTransport:
    def test_a_a_normal_download_returns_the_complete_bytes(self):
        steps = [("chunk", PAYLOAD[:100]), ("chunk", PAYLOAD[100:5000]), ("chunk", PAYLOAD[5000:])]
        with _serving(steps, len(PAYLOAD)) as (server, base_url):
            client, session = _local(base_url)
            try:
                body = download_from_uploads(OBJECT_PATH, client=client, chunk_bytes=64)
            finally:
                session.close()

        assert body == PAYLOAD, "the bytes must be the object body, byte for byte"
        assert isinstance(body, bytes)
        # The endpoint and the authentication are storage3's own, not re-derived.
        assert server.paths == [f"/storage/v1/object/uploads/{OBJECT_PATH.replace(' ', '%20')}"]
        assert unquote(server.paths[0]) == f"/storage/v1/object/{SOURCE_BUCKET}/{OBJECT_PATH}"
        assert server.authorizations == [TEST_AUTH_HEADER]

    def test_b_a_transfer_that_outlasts_the_read_timeout_is_accepted(self):
        """The whole point of J37C-8Q: a slow-but-progressing transfer must not be
        rejected for taking longer than a single read deadline.

        Scaled down and driven by real time: 12 chunks every 0.15 s — each arriving
        far inside the 1.0 s read deadline — over ~1.8 s in total, so the transfer
        outlasts the read deadline it is measured against and still completes.
        """
        read_timeout = 1.0
        body_expected = (PAYLOAD * 12)[:12000]
        steps = []
        for offset in range(0, 12000, 1000):
            steps.append(("stall", 0.15))
            steps.append(("chunk", body_expected[offset : offset + 1000]))
        assert sum(len(value) for kind, value in steps if kind == "chunk") == 12000
        with _serving(steps, 12000) as (server, base_url):
            client, session = _local(base_url)
            try:
                started = time.monotonic()
                body = download_from_uploads(
                    OBJECT_PATH,
                    client=client,
                    read_timeout=read_timeout,
                    deadline=30.0,
                    chunk_bytes=1000,
                )
                elapsed = time.monotonic() - started
            finally:
                session.close()

        assert body == body_expected
        assert elapsed > read_timeout, "the test must outlast the read deadline to mean anything"

    def test_c_a_stall_longer_than_the_read_timeout_fails(self):
        """A read that makes no progress past the deadline fails, and returns nothing."""
        steps = [("chunk", PAYLOAD[:50]), ("stall", 1.5), ("chunk", PAYLOAD[50:])]
        with _serving(steps, len(PAYLOAD)) as (server, base_url):
            client, session = _local(base_url)
            try:
                started = time.monotonic()
                with pytest.raises(httpx.TimeoutException):
                    download_from_uploads(
                        OBJECT_PATH, client=client, read_timeout=0.3, deadline=30.0
                    )
                elapsed = time.monotonic() - started
            finally:
                session.close()

        assert elapsed < 1.0, "it must fail on the read deadline, not wait the stall out"

    def test_d_a_continuously_slow_transfer_past_the_total_deadline_fails(self):
        """Slow is not stalled: every chunk arrives in time, and it still fails —
        because the TOTAL deadline is enforced here, not by the socket."""
        deadline = 0.25
        steps = [
            step
            for i in range(0, 400, 10)
            for step in (("stall", 0.05), ("chunk", PAYLOAD[i : i + 10]))
        ]
        with _serving(steps, 400) as (server, base_url):
            client, session = _local(base_url)
            try:
                started = time.monotonic()
                with pytest.raises(StorageDownloadTimeout) as raised:
                    download_from_uploads(
                        OBJECT_PATH,
                        client=client,
                        read_timeout=5.0,  # no chunk is ever late
                        deadline=deadline,
                        chunk_bytes=10,  # one iteration per chunk the server sends
                    )
                elapsed = time.monotonic() - started
            finally:
                session.close()

        message = str(raised.value)
        assert "exceeded the 0.25s total deadline" in message
        assert "bytes received" in message
        assert elapsed < 1.0, "the deadline, not the script, ended this transfer"
        assert elapsed > deadline - 0.05


# ---------------------------------------------------------------------------
# The bounds as the transport receives them, and the exact comparison.
# ---------------------------------------------------------------------------
class TestTheConfiguredBounds:
    def test_the_request_carries_finite_deadlines_above_the_replaced_default(self):
        """The deadline handed to httpx is the one this module names — read far
        above the 20 s default it replaces, every field still finite."""
        seen = {}

        def handler(request):
            seen["timeout"] = request.extensions.get("timeout")
            return httpx.Response(200, content=b"%PDF-1.7 body")

        session = httpx.Client(transport=httpx.MockTransport(handler))
        client = _client_for("http://127.0.0.1:9/storage/v1", session)
        try:
            assert download_from_uploads(OBJECT_PATH, client=client) == b"%PDF-1.7 body"
        finally:
            session.close()

        assert seen["timeout"] == {
            "connect": STORAGE_CONNECT_TIMEOUT_SECONDS,
            "read": STORAGE_READ_TIMEOUT_SECONDS,
            "write": 120.0,
            "pool": 30.0,
        }
        assert seen["timeout"]["read"] > STORAGE3_DEFAULT_TIMEOUT
        assert all(value is not None and value > 0 for value in seen["timeout"].values())

    def test_the_deadline_is_enforced_at_the_first_chunk_boundary_past_it(self):
        """The comparison is `elapsed > deadline`, evaluated once per received chunk.
        Equality is not a violation; the next chunk past it is.

        The clock is injected here and only here: it pins the comparison exactly, which
        real time cannot do. The wall-clock equivalent — a transfer that really does run
        past its deadline and is refused for it — is `test_d` above, over a real socket.
        """

        class Clock:
            def __init__(self, values):
                self._values = list(values)
                self._last = 0.0

            def __call__(self):
                if self._values:
                    self._last = self._values.pop(0)
                return self._last

        def run(clock, deadline):
            session = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=iter([b"a" * 10, b"b" * 10]))
                )
            )
            client = _client_for("http://127.0.0.1:9/storage/v1", session)
            try:
                return download_from_uploads(
                    OBJECT_PATH,
                    client=client,
                    deadline=deadline,
                    chunk_bytes=10,  # one iteration per source chunk
                    clock=clock,
                )
            finally:
                session.close()

        # start=0.0, chunk 1 at 1.0, chunk 2 at exactly the deadline -> accepted.
        assert run(Clock([0.0, 1.0, 10.0]), 10.0) == b"a" * 10 + b"b" * 10

        # start=0.0, chunk 1 at 1.0, chunk 2 a hair past the deadline -> refused.
        with pytest.raises(StorageDownloadTimeout) as raised:
            run(Clock([0.0, 1.0, 10.0001]), 10.0)
        assert "exceeded the 10s total deadline" in str(raised.value)
        assert "20 bytes received" in str(raised.value)


# ---------------------------------------------------------------------------
# E: the pipeline ordering and the fail-closed consequence.
# ---------------------------------------------------------------------------
class _RecordingClient:
    """`supabase` as `app.main`'s background paths use it: the only write they may
    perform is the project status write, and every write is recorded."""

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


class TestThePipelineOrdering:
    def test_a_failed_download_creates_no_drawing_set_and_no_parse(self, monkeypatch):
        """The download comes first, and a download that fails is the end of the
        background task: nothing downstream of it may run.

        Wave 3J Phase B — the persisted value is the safe classification, and the
        exception's own text (here: a storage URL path) is asserted ABSENT from it.
        The exception raised is deliberately unchanged, so this test still carries
        the same evidence; what it proves about that evidence is now the opposite.
        """

        def forbidden(*args, **kwargs):
            raise AssertionError("the pipeline ran after a failed download")

        raw_text = (
            "storage download exceeded the 900s total deadline after 900.4s "
            "(1,048,576 bytes received): /storage/v1/object/uploads/plan.pdf"
        )

        def failing_download(*args, **kwargs):
            raise StorageDownloadTimeout(raw_text)

        client = _RecordingClient()
        monkeypatch.setattr(main_module, "download_from_uploads", failing_download)
        monkeypatch.setattr(main_module, "parse_pdf_and_save", forbidden)
        monkeypatch.setattr(main_module, "parse_dxf_and_save", forbidden)
        monkeypatch.setattr(main_module, "build_and_store_report", forbidden)
        monkeypatch.setattr(main_module, "supabase", client)

        main_module.run_extraction("p-1", "drawings/plan.pdf", "PDF", "u-1")

        assert client.writes == [
            (
                "projects",
                {
                    "status": "failed",
                    "error_message": PERSISTED_MESSAGE_FOR[EXTRACTION_STORAGE_UNAVAILABLE],
                },
            )
        ]

        # The point of the milestone, asserted on its own line rather than implied by
        # the equality above: the URL the exception carried reached no column.
        persisted = client.writes[0][1]["error_message"]
        assert raw_text not in persisted
        assert "/storage/v1/object/uploads/plan.pdf" not in persisted

    def test_a_stalled_read_keeps_the_existing_failure_behavior(self, monkeypatch):
        """A per-read stall is not caught and reinterpreted here: the transport's own
        exception reaches the existing handler, exactly as it did before J37C-8Q.

        Wave 3J Phase B — the handler still catches the transport's own exception and
        still marks the project failed; what it PERSISTS about it is now the safe
        classification, not the transport's message."""

        def forbidden(*args, **kwargs):
            raise AssertionError("the pipeline ran after a stalled download")

        def stalled(*args, **kwargs):
            raise httpx.ReadTimeout("The read operation timed out")

        client = _RecordingClient()
        monkeypatch.setattr(main_module, "download_from_uploads", stalled)
        monkeypatch.setattr(main_module, "parse_pdf_and_save", forbidden)
        monkeypatch.setattr(main_module, "build_and_store_report", forbidden)
        monkeypatch.setattr(main_module, "supabase", client)

        main_module.run_extraction("p-1", "drawings/plan.pdf", "PDF", "u-1")

        assert client.writes == [
            (
                "projects",
                {
                    "status": "failed",
                    "error_message": PERSISTED_MESSAGE_FOR[EXTRACTION_STORAGE_UNAVAILABLE],
                },
            )
        ]

        persisted = client.writes[0][1]["error_message"]
        assert "The read operation timed out" not in persisted

    def test_the_downloaded_bytes_reach_the_pipeline_unchanged_and_in_order(self, monkeypatch):
        order = []
        seen = {}

        def parser(tmp_path, project_id, user_id, storage_path):
            order.append("parse")
            seen["project_id"] = project_id
            seen["storage_path"] = storage_path
            with open(tmp_path, "rb") as handle:
                seen["bytes"] = handle.read()
            seen["tmp_path"] = tmp_path

        def report(project_id, user_id):
            order.append("report")

        client = _RecordingClient()
        monkeypatch.setattr(
            main_module, "download_from_uploads", lambda *a, **k: PAYLOAD
        )
        monkeypatch.setattr(main_module, "parse_pdf_and_save", parser)
        monkeypatch.setattr(main_module, "build_and_store_report", report)
        monkeypatch.setattr(main_module, "supabase", client)

        main_module.run_extraction("p-1", "drawings/plan.pdf", "PDF", "u-1")

        assert seen["bytes"] == PAYLOAD
        assert seen["storage_path"] == "drawings/plan.pdf"
        assert seen["project_id"] == "p-1"
        assert order == ["parse", "report"], "the report is built after the parse"
        assert client.writes == [], "a successful extraction writes no failure status"
        assert not os.path.exists(seen["tmp_path"]), "the temporary file is cleaned up"

    def test_a_failed_continuation_download_does_not_touch_project_status(self, monkeypatch):
        """The continuation and retry paths deliberately never write project status.
        The new exception must not change that."""

        def failing_download(*args, **kwargs):
            raise StorageDownloadTimeout("storage download exceeded the 900s total deadline")

        def forbidden(*args, **kwargs):
            raise AssertionError("the continuation ran after a failed download")

        client = _RecordingClient()
        monkeypatch.setattr(main_module, "download_from_uploads", failing_download)
        monkeypatch.setattr(main_module, "continue_pdf_extraction", forbidden)
        monkeypatch.setattr(main_module, "build_and_store_report", forbidden)
        monkeypatch.setattr(main_module, "supabase", client)

        with pytest.raises(StorageDownloadTimeout):
            main_module.run_continuation("p-1", "drawings/plan.pdf", "PDF", "u-1")

        assert client.writes == []
