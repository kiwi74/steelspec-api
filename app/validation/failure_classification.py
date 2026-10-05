"""
Wave 3J Phase B — the persisted failure classification.

THE INVARIANT THIS MODULE EXISTS TO ENFORCE
===========================================
No value written into `projects.error_message`, `analysis_runs.error_message` or
`drawing_sets.error_message` may be derived from external exception text, a
provider response body, a database response body, a storage response body, a URL
path, or any other external diagnostic content.

THE MECHANISM: A FROZEN CODOMAIN, NOT CAREFUL CALL SITES
========================================================
`safe_failure_message` is TOTAL and its return value is drawn from a frozen,
finite, SteelSpec-owned set. That is the whole of the enforcement. The property
holds because of what the function can RETURN, not because of what a caller
passes: the invariant survives a caller that hands it a hostile object, and it
survives a future seventh writer that forgets why the rule exists.

The classifier may therefore INSPECT structure — an exception's type, a
PostgREST SQLSTATE — without weakening anything, because inspection feeds a
mapping whose outputs are constants. Reading is unconstrained; writing is total.

WHAT IS NEVER READ
==================
`str(exc)`, `repr(exc)`, `exc.args`, `exc.message`, `exc.details`, `exc.hint`,
`exc.json()`, `exc._raw_error`, any URL or path, and any response body. The
traceback — which carries all of that — belongs to the operator channel that
Wave 3H Phase A established, and reaches stderr through `logger.exception`. It
must never reach a database column.

WHY SOME EXCEPTIONS CARRY NO MESSAGE
====================================
`SourceUnreadable`, `ProviderNotConfigured` and `ReportGenerationFailed` are
raised at sites that previously raised a bare `RuntimeError`. All three subclass
`RuntimeError`, so every existing `except RuntimeError` still catches them and no
caller's control flow changed. `ReportGenerationFailed` is deliberately raised
with a fixed SteelSpec-owned sentence and the original exception attached as its
cause: the detail is preserved for the operator through the cause chain, and the
raised object itself never carries external text that a future writer could be
tempted to persist.
"""
from __future__ import annotations

import anthropic
import httpx
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFPopplerTimeoutError,
    PDFSyntaxError,
    PopplerNotInstalledError,
)
from postgrest.exceptions import APIError as PostgrestAPIError
from storage3.exceptions import StorageApiError

from app.storage_download import StorageDownloadTimeout

# ======================================================================================
# The locked vocabulary
# ======================================================================================
# Every persisted failure value is one of these eight codes. The strings are
# SteelSpec-owned, are never interpolated, and are frozen by `SAFE_FAILURE_MESSAGES`
# below. `EXTRACTION_INPUT_REFUSED_*` (app/production_extraction_source.py) is a
# DIFFERENT vocabulary — request-validation refusals returned to a caller, never
# persisted here — and must stay separate from this one.

# Source/input storage download or transport unavailable.
EXTRACTION_STORAGE_UNAVAILABLE = "EXTRACTION_STORAGE_UNAVAILABLE"
# SteelSpec-generated output could not be stored/uploaded after processing.
EXTRACTION_STORE_FAILED = "EXTRACTION_STORE_FAILED"
# Provider authentication/permission failures.
EXTRACTION_PROVIDER_AUTH = "EXTRACTION_PROVIDER_AUTH"
# Provider/config/request-shape failures.
EXTRACTION_PROVIDER_CONFIG = "EXTRACTION_PROVIDER_CONFIG"
# Transient provider failures.
EXTRACTION_PROVIDER_UNAVAILABLE = "EXTRACTION_PROVIDER_UNAVAILABLE"
# PDF/source rendering or reading failures.
EXTRACTION_SOURCE_UNREADABLE = "EXTRACTION_SOURCE_UNREADABLE"
# Report-generation failure.
EXTRACTION_REPORT_FAILED = "EXTRACTION_REPORT_FAILED"
# Safe catch-all for every otherwise-unclassified exception.
EXTRACTION_UNEXPECTED = "EXTRACTION_UNEXPECTED"

FAILURE_CLASSIFICATIONS: tuple[str, ...] = (
    EXTRACTION_STORAGE_UNAVAILABLE,
    EXTRACTION_STORE_FAILED,
    EXTRACTION_PROVIDER_AUTH,
    EXTRACTION_PROVIDER_CONFIG,
    EXTRACTION_PROVIDER_UNAVAILABLE,
    EXTRACTION_SOURCE_UNREADABLE,
    EXTRACTION_REPORT_FAILED,
    EXTRACTION_UNEXPECTED,
)

# The complete persisted string for each code: the code itself as a stable,
# machine-readable prefix, then one SteelSpec-owned sentence. A reader can filter
# on `EXTRACTION_PROVIDER_AUTH` without matching prose, and a human sees a
# sentence that is true without knowing anything about the exception.
PERSISTED_MESSAGE_FOR: dict[str, str] = {
    EXTRACTION_STORAGE_UNAVAILABLE: (
        f"{EXTRACTION_STORAGE_UNAVAILABLE}: the source file could not be read from storage."
    ),
    EXTRACTION_STORE_FAILED: (
        f"{EXTRACTION_STORE_FAILED}: the generated output could not be stored."
    ),
    EXTRACTION_PROVIDER_AUTH: (
        f"{EXTRACTION_PROVIDER_AUTH}: the drawing service rejected SteelSpec's credentials."
    ),
    EXTRACTION_PROVIDER_CONFIG: (
        f"{EXTRACTION_PROVIDER_CONFIG}: the drawing service rejected SteelSpec's request configuration."
    ),
    EXTRACTION_PROVIDER_UNAVAILABLE: (
        f"{EXTRACTION_PROVIDER_UNAVAILABLE}: the drawing service was temporarily unavailable."
    ),
    EXTRACTION_SOURCE_UNREADABLE: (
        f"{EXTRACTION_SOURCE_UNREADABLE}: the uploaded drawing could not be read or rendered."
    ),
    EXTRACTION_REPORT_FAILED: (
        f"{EXTRACTION_REPORT_FAILED}: the project report could not be generated."
    ),
    EXTRACTION_UNEXPECTED: (
        f"{EXTRACTION_UNEXPECTED}: the extraction failed for a reason SteelSpec could not classify."
    ),
}

# The frozen codomain. `safe_failure_message` can return nothing else, and the
# tests assert that over an adversarial corpus rather than over the call sites.
SAFE_FAILURE_MESSAGES: frozenset[str] = frozenset(PERSISTED_MESSAGE_FOR.values())


# ======================================================================================
# The exception types this module owns
# ======================================================================================
# Each subclasses `RuntimeError` so that it is a drop-in replacement for a raise
# site that previously raised a bare `RuntimeError` — every existing handler keeps
# catching it. None of them ever needs to carry external text: the operator gets
# the detail from the cause chain and the Phase A traceback.

class SourceUnreadable(RuntimeError):
    """The uploaded source could not be read or rendered into pages.

    Raised by the PDF reading path for conditions that are about the FILE — an
    empty render, a malformed or unreadable document — as distinct from a failure
    of the service that was asked to read it.
    """


class ProviderNotConfigured(RuntimeError):
    """The drawing service was asked to work without being configured for it.

    Currently the missing-`ANTHROPIC_API_KEY` condition. It is a configuration
    fault, not a source fault and not a transient one.
    """


class ReportGenerationFailed(RuntimeError):
    """A project report could not be generated or stored.

    Deliberately carries a fixed SteelSpec-owned sentence and nothing else. The
    originating exception is attached with `raise ... from exc`, so an operator
    reading the Phase A traceback sees the full cause; a reader of the project
    sees only the classification.
    """


# ======================================================================================
# Dispatch
# ======================================================================================
# Explicit type dispatch, most specific first. Every tuple is checked before the
# base classes it specializes, and an unrecognised type falls through to the
# catch-all at the bottom — so an exception this module has never heard of, an
# unknown provider subclass, or a future SDK addition all classify safely.

# AuthenticationError and PermissionDeniedError are siblings under APIStatusError;
# both mean the same thing to an operator, and the same action fixes either.
_PROVIDER_AUTH = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
)

# A request the provider refused as malformed, or an endpoint/model it could not
# find, or SteelSpec asking without a key: all three are configuration faults.
_PROVIDER_CONFIG = (
    anthropic.BadRequestError,
    anthropic.NotFoundError,
    ProviderNotConfigured,
)

# APITimeoutError is a subclass of APIConnectionError; both are listed because they
# are separately reachable and read as separate conditions. All four mean "the
# service could not answer; the same request may succeed later".
_PROVIDER_UNAVAILABLE = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

# PDFInfoNotInstalledError is a subclass of PopplerNotInstalledError; listed for
# completeness. Every one of these is raised by `convert_from_path` before it
# returns, so each reaches a persistence boundary as itself.
_SOURCE_UNREADABLE = (
    SourceUnreadable,
    PopplerNotInstalledError,
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFSyntaxError,
    PDFPopplerTimeoutError,
)

# The source-download transport. `StorageDownloadTimeout` is SteelSpec's own class
# and is raised only by app/storage_download.py; `httpx.TransportError` covers the
# timeouts (TimeoutException is a subclass) and every read/connect/protocol error;
# `httpx.HTTPStatusError` is NOT a TransportError and is added separately, for a
# non-2xx that was not the storage error envelope.
#
# Raw httpx exceptions can only come from our own storage module: the provider SDK
# wraps EVERY transport exception into APIConnectionError (anthropic/_base_client.py
# catches `Exception` around `send`) or APITimeoutError, so none escapes it.
_SOURCE_TRANSPORT = (
    StorageDownloadTimeout,
    httpx.TransportError,
    httpx.HTTPStatusError,
)


def classify_failure(exc: BaseException) -> str:
    """The classification code for `exc`. Total: never raises, never returns None.

    Ordering is load-bearing. The provider tuples are checked before the generic
    `anthropic.APIError` fall-through, so an unknown status does not get silently
    promoted into a known one — it lands in `EXTRACTION_UNEXPECTED`.
    """
    if isinstance(exc, _PROVIDER_AUTH):
        return EXTRACTION_PROVIDER_AUTH
    if isinstance(exc, _PROVIDER_CONFIG):
        return EXTRACTION_PROVIDER_CONFIG
    if isinstance(exc, _PROVIDER_UNAVAILABLE):
        return EXTRACTION_PROVIDER_UNAVAILABLE
    if isinstance(exc, _SOURCE_UNREADABLE):
        return EXTRACTION_SOURCE_UNREADABLE
    if isinstance(exc, ReportGenerationFailed):
        return EXTRACTION_REPORT_FAILED
    if isinstance(exc, _SOURCE_TRANSPORT):
        return EXTRACTION_STORAGE_UNAVAILABLE

    # Database (PostgREST) and storage-API failures.
    #
    # `PostgrestAPIError` exposes structured `.code`, `.details`, `.hint` and
    # `.message`, and its `str()` renders the whole envelope — including `details`,
    # which can contain real row values ("Key (material_name)=(S355JR) already
    # exists."). `StorageApiError` likewise exposes `.code` and `.status`.
    #
    # The locked vocabulary contains no database classification, so there is
    # nothing for a SQLSTATE to select among and none of those properties is read
    # here. `StorageApiError` is left unclassified for a different reason: the same
    # type is raised by the SOURCE download and by the OUTPUT uploads, and the
    # exception alone cannot say which — so reusing EXTRACTION_STORAGE_UNAVAILABLE
    # would state something false about an upload, and EXTRACTION_STORE_FAILED
    # would state something false about a download. A vague-but-true catch-all is
    # the correct answer; per the Phase B brief, an output-store failure that is
    # not currently distinguishable stays here rather than inventing a broad
    # storage classification.
    #
    # If either classification is ever added, this is the single branch that maps
    # a structured code to it.
    if isinstance(exc, (PostgrestAPIError, StorageApiError)):
        return EXTRACTION_UNEXPECTED

    # An exception this module has never heard of. Fail closed.
    return EXTRACTION_UNEXPECTED


def safe_failure_message(exc: BaseException) -> str:
    """The complete SteelSpec-owned persisted string for `exc`.

    This is the ONLY value any writer may put into an `error_message` column. The
    return value is always a member of `SAFE_FAILURE_MESSAGES` — a frozen finite
    set — so it cannot carry exception text, a provider body, a database body, a
    URL or a path, whatever `exc` is.
    """
    return PERSISTED_MESSAGE_FOR[classify_failure(exc)]
