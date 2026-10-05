"""
THE SOURCE DOCUMENT'S OWN IDENTITY — the one place its bytes are hashed.

WHY THIS MODULE EXISTS

J28 computed the SHA-256 of a source document's bytes and records it beside every annotation
occurrence as `source_pdf_sha256`. J61 then gave a document its durable identity from the
SAME rule, on the same bytes, so that there is exactly one definition of "the hash of this
document" and no second implementation exists to disagree with the first.

E2E-001N needed that rule too, for the documents the ingestion path registers. Taking it from
the annotation extractor would have made INGESTION an annotation-layer consumer, which is a
boundary J28 guards exactly and for good reason: the ingestion path has nothing to do with
annotations, and a module that reaches into that layer for a hash is a module that could
later reach into it for something else. So the rule moved here — to a module that depends on
nothing at all.

WHAT IT DEPENDS ON

`hashlib` and nothing else. No annotation evidence, no annotation extraction, no database, no
Supabase, no HTTP, no ingestion, no filesystem. It is a pure function over bytes, so it
cannot acquire a second meaning by acquiring a dependency.

WHAT IT DELIBERATELY IS NOT

It is not a utility dumping ground: it holds one rule, and the rule is the reason J61's
document identity is content and not a filename, a path or a timestamp. A caller with a PATH
still reads it through the annotation extractor's own `source_bytes`, which is where the
"this file cannot be read" refusal lives — that refusal belongs to the layer that reads, not
to the arithmetic.
"""
from __future__ import annotations

from typing import Any

import hashlib

__all__ = ["source_document_sha256_of_bytes"]


def source_document_sha256_of_bytes(source: Any) -> str:
    """The SHA-256 of a source document's own bytes. The ONE hashing rule in this codebase.

    It is a property of the file and not of the machine: no timestamp, no path and no UUID
    enters it, and the same bytes produce the same digest wherever they are read — which is
    what lets a re-read of a file resolve to the document row the first read created rather
    than to a second document.

    The input is bytes already in hand. A caller holding a PATH reads it first, through the
    layer that owns "this source could not be read"; this function never opens anything and
    so can never fail for a reason other than a caller handing it something that is not
    bytes.
    """
    if not isinstance(source, (bytes, bytearray)):
        raise TypeError(
            "source_document_sha256_of_bytes hashes bytes already in hand; a caller "
            "holding a path reads it through the layer that owns that refusal"
        )
    return hashlib.sha256(bytes(source)).hexdigest()
