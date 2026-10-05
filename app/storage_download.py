"""
Bounded, fail-closed reads of a source file from Supabase Storage.

WHY THIS MODULE EXISTS (Milestone J37C-8P established it, J37C-8Q fixes it)
--------------------------------------------------------------------------
The extraction paths read the uploaded source file with

    supabase.storage.from_("uploads").download(storage_path)

That call takes no timeout argument, and the only deadline in force behind it is
storage3's *per-read* socket deadline of 20 s — `storage3.constants.DEFAULT_TIMEOUT`,
surfaced through `supabase.ClientOptions.storage_client_timeout`, handed to httpx and
finally set on the socket by httpcore before each `recv`. Nothing in the stack imposes
a *total* deadline: every httpx timeout field (connect/read/write/pool) is per-operation.

Two facts made that 20 s deadline the wrong bound for this workload:

  * the 22,001,076-byte Arkles PDF needs ~3.5 minutes at this host's measured egress
    (~102 KiB/s), so the deadline is far tighter than the transfer it must survive;
  * it is tripped by a *stall* — 20 s with no bytes — not by a slow-but-progressing
    transfer, so it fails intermittently and for a reason the message ("The read
    operation timed out") does not explain.

J37C-8O failed on exactly that bound, before `create_drawing_set` was reached.

WHAT THIS DOES INSTEAD
----------------------
It performs the same read — same bucket, same path helper, same authenticated session
and same endpoint construction as storage3's own `download()` — under TWO finite bounds:

  * PER-READ  `STORAGE_READ_TIMEOUT_SECONDS` — enforced by httpx/httpcore on the socket
    read (the mechanism that was already in force, at a value that tolerates a stalled
    read rather than forbidding a slow transfer).
  * TOTAL     `STORAGE_DOWNLOAD_DEADLINE_SECONDS` — enforced here, in the calling thread,
    against a monotonic clock between received chunks.

Neither bound is unbounded, neither is a retry, and neither is a background thread or a
signal. A read that exceeds either one raises; the caller's existing failure handling
runs unchanged (the extraction path marks the project failed; the continuation and retry
paths read nothing and write nothing, and deliberately do not touch project status).

The bytes returned are exactly what `download()` returned: the whole object body.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import httpx
from storage3._sync.file_api import relative_path_to_parts
from storage3.exceptions import StorageApiError

# The bucket every extraction path reads its source file from.
SOURCE_BUCKET = "uploads"

# --- Named bounds (Milestone J37C-8Q). Values are engineering choices, not magic
# --- numbers, and each is justified against the measured transfer.
#
# 120 s: 6x the 20 s per-read deadline this replaces, and ~57% of the ~3.5 minutes the
# 22 MB source file needs at observed egress. Long enough to ride out a stall, short
# enough that a genuinely dead connection fails in minutes rather than never.
STORAGE_READ_TIMEOUT_SECONDS = 120.0
# 900 s: ~4.3x the observed ~210.7 s transfer, i.e. headroom for a ~4x slower uplink
# while still bounding the download to 15 minutes. "Approximately" is honest: the
# deadline is checked after each accumulated STORAGE_DOWNLOAD_CHUNK_BYTES, so a
# transfer that exceeds it fails at the first such boundary after it — no later than
# the deadline plus one read timeout (<= 1020 s), and it cannot run forever.
STORAGE_DOWNLOAD_DEADLINE_SECONDS = 900.0
# 30 s: connection establishment and pool checkout, both far below the transfer bounds
# and far above the ~100 ms observed. Finite, so a black-holed connect still fails.
STORAGE_CONNECT_TIMEOUT_SECONDS = 30.0
STORAGE_POOL_TIMEOUT_SECONDS = 30.0
STORAGE_WRITE_TIMEOUT_SECONDS = 120.0
# 64 KiB: one chunk's worth of streaming, small enough that the deadline is checked
# often relative to the deadline itself, large enough not to matter for throughput.
STORAGE_DOWNLOAD_CHUNK_BYTES = 64 * 1024


class StorageDownloadTimeout(Exception):
    """The bounded read exceeded `STORAGE_DOWNLOAD_DEADLINE_SECONDS`.

    Raised only for the TOTAL bound. The PER-READ bound is enforced by httpx inside the
    transport, so a stalled read surfaces as the dependency's own timeout exception —
    the same exception, with the same message, this path raised before this module
    existed. Both are failures; both leave the caller's handling unchanged.
    """


def download_from_uploads(
    path: str,
    *,
    client: Any = None,
    bucket: str = SOURCE_BUCKET,
    read_timeout: float = STORAGE_READ_TIMEOUT_SECONDS,
    deadline: float = STORAGE_DOWNLOAD_DEADLINE_SECONDS,
    chunk_bytes: int = STORAGE_DOWNLOAD_CHUNK_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """Download `path` from `bucket` under a per-read bound and a total bound.

    Parameters
    ----------
    path
        Object path within the bucket, as stored on the project row
        (`projects.uploaded_file_path`).
    client
        The application's Supabase client. Defaults to `app.supabase_client.supabase`,
        imported lazily so this module can be imported without credentials. The client's
        own storage session is reused, so the request carries the same authenticated
        headers and the same connection pool as every other storage call.
    bucket, read_timeout, deadline, chunk_bytes, clock
        Injected for tests; production callers use the module defaults.

    Raises
    ------
    StorageDownloadTimeout
        The total deadline was exceeded. No bytes are returned.
    httpx.TimeoutException / httpx.ReadError
        The per-read deadline (or connection, or write) was exceeded. Propagated
        unchanged from the transport.
    StorageApiError
        The storage API answered a non-2xx, mapped exactly as storage3's own
        `download()` maps it.
    """
    if client is None:  # pragma: no cover - production path; tests inject a client
        from app.supabase_client import supabase as client

    proxy = client.storage.from_(bucket)
    url = proxy._base_url.joinpath("object", proxy.id, *relative_path_to_parts(path))
    request_timeout = httpx.Timeout(
        connect=STORAGE_CONNECT_TIMEOUT_SECONDS,
        read=read_timeout,
        write=STORAGE_WRITE_TIMEOUT_SECONDS,
        pool=STORAGE_POOL_TIMEOUT_SECONDS,
    )

    started = clock()
    body = bytearray()
    with proxy._client.stream(
        "GET", str(url), headers=dict(proxy._headers), timeout=request_timeout
    ) as response:
        _raise_for_status(response)
        for chunk in response.iter_bytes(chunk_bytes):
            body.extend(chunk)
            elapsed = clock() - started
            if elapsed > deadline:
                raise StorageDownloadTimeout(
                    f"storage download exceeded the {deadline:g}s total deadline after "
                    f"{elapsed:.1f}s ({len(body)} bytes received): {url.path}"
                )
    return bytes(body)


def _raise_for_status(response: httpx.Response) -> None:
    """Map a non-2xx to StorageApiError exactly as storage3's `download()` does.

    Kept identical so that a missing or forbidden object fails with the same exception
    and message it produced before this module existed.
    """
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = _storage_api_error(exc.response)
        if detail is not None:
            raise detail from exc
        raise


def _storage_api_error(response: httpx.Response) -> Optional[StorageApiError]:
    """The storage API's error envelope, or None if the body is not that envelope."""
    try:
        payload = response.json()
        return StorageApiError(payload["message"], payload["error"], payload["statusCode"])
    except Exception:
        return None
