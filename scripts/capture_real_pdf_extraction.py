"""
7AU — capture a real PDF page extraction through the REAL vision pipeline.

Runs app.ai_analysis.pdf_vision_analyzer.analyze_pdf_pages() — the genuine
extraction path — and writes the raw PageExtraction list as JSON in the same
format as tests/data/arkles_strand_page_extractions.json (the repository's
captured-real-extraction convention, see tests/test_project_extraction_intake.py).

WHAT IS REAL AND WHAT IS NOT (read this first):

  REAL: the PDF is rendered page-by-page (poppler, 200 dpi) and every page is
  sent to Claude's vision model with the production EXTRACTION_SYSTEM_PROMPT.
  The JSON written is exactly what the model returned — nothing is added,
  removed, edited or back-filled, and no material value is ever injected. If
  the vision call fails, NO capture file is written and the error propagates:
  an extraction JSON is never fabricated.

  SUBSTITUTED ONLY WHEN NECESSARY: page-image persistence. The production
  analyzer uploads each rendered page to Supabase storage under a UUID
  `drawing_id` (the drawings-table row id — a database requirement this
  harness never weakens). That upload is attempted ONLY when Supabase is
  configured AND a valid --storage-drawing-uuid is supplied; otherwise the
  rendered PNGs are saved next to the capture instead. Image storage is
  evidence infrastructure, not extraction; the vision call itself is never
  mocked, patched or bypassed.

  TWO DISTINCT IDENTITIES — do not conflate them:
    --drawing-id            the drawing's HUMAN-READABLE identity (default
                            "SELBY-C1136", the source drawing number prefix).
                            It is evidence-layer identity: it travels in the
                            capture provenance and in LOCAL image paths only.
                            It must never reach a UUID-only repository field.
    --storage-drawing-uuid  a separate technical identifier: a real UUID from
                            the drawings table, used ONLY when page images are
                            uploaded to Supabase. The harness never invents
                            one; without it, images are stored locally.

  NOT HERE: validation, section matching, review, or any engineering
  interpretation. The output is raw PageExtraction data, exactly as the
  analyzer defines it.

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py \
        "/Users/chad/Downloads/FABs.pdf" tests/data/selby_square_page_extractions.json

Optional: --user-id, --project-id, --drawing-id (evidence/labels only),
--storage-drawing-uuid (real Supabase drawings-table UUID), --max-pages
(default 5; the vision API is called once per page), --first-page (default 1;
pages are analysed from this 1-based page number onward, so a later slice of
a drawing set reports its real page numbers without re-analysing earlier
pages).
"""
import argparse
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PLACEHOLDER_SUPABASE_URL = "https://placeholder.supabase.co"
# app.config reads these at module import, and supabase-py validates the key
# SHAPE at client creation; the placeholder is a JWT-shaped string that is not
# a real credential (same convention as tests/test_review_ui_integration.py).
PLACEHOLDER_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ"
    ".fake-test-signature"
)

_UUID_SHAPE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_uuid_shaped(value) -> bool:
    return isinstance(value, str) and bool(_UUID_SHAPE.match(value))


def choose_image_backend(supabase_configured: bool, storage_drawing_uuid, image_dir: Path):
    """Decides where rendered page images are persisted: the production Supabase
    upload, or local PNG files. The ONLY boundary that makes this decision.

    Returns (upload_callable_or_None, backend, reason):
      - (None, "supabase", ...) — the caller must use the production
        upload_page_image UNCHANGED. Chosen only when Supabase is configured
        AND a UUID-shaped storage drawing id was supplied, preserving the
        production upload behaviour and the database's UUID requirement.
      - (local_upload, "local", ...) — save PNGs under image_dir. Chosen
        otherwise: without usable credentials, or without a valid storage
        UUID, no upload is attempted (the harness never invents a UUID and
        never sends a human-readable drawing id into a UUID-only column).
    """
    if supabase_configured and is_uuid_shaped(storage_drawing_uuid):
        return None, "supabase", "Supabase configured and a valid storage drawing UUID supplied."
    if supabase_configured:
        reason = (
            "Supabase is configured but no valid --storage-drawing-uuid was supplied; "
            "a human-readable drawing id must never reach a UUID-only column, so page "
            "images are saved locally."
        )
    else:
        reason = "Supabase is not configured; page images are saved locally."
    def local_upload(user_id, project_id, drawing_id, page_number, image_bytes):
        image_dir.mkdir(parents=True, exist_ok=True)
        (image_dir / f"page-{page_number}.png").write_bytes(image_bytes)
        return f"{user_id}/{project_id}/drawings/{drawing_id}/page-{page_number}.png"
    return local_upload, "local", reason


def storage_drawing_identity(backend: str, drawing_id: str, storage_drawing_uuid) -> str:
    """The drawing identifier handed to the ANALYZER, which forwards it to
    page-image storage. The human-readable drawing id travels only when storage
    is local; when the real Supabase backend is used, the UUID-shaped storage
    identifier is handed over instead. The two identities are never conflated."""
    return storage_drawing_uuid if backend == "supabase" else drawing_id


def capture(
    pdf_path,
    capture_path,
    *,
    user_id: str,
    project_id: str,
    drawing_id: str,
    storage_drawing_uuid,
    max_pages: int,
    first_page: int = 1,
    analyzer_module=None,
) -> None:
    """The genuine vision path -> raw PageExtraction JSON. Raises on failure and
    writes NOTHING unless the vision call completed: no fabricated extraction."""
    supabase_configured = (
        "SUPABASE_URL" in os.environ and "SUPABASE_SERVICE_ROLE_KEY" in os.environ
    )
    # app.config reads these at import time; placeholders satisfy the import when
    # Supabase is not configured locally. Whatever this function adds, it removes
    # again, so a test process's environment is left exactly as it was found.
    added_url = "SUPABASE_URL" not in os.environ
    added_key = "SUPABASE_SERVICE_ROLE_KEY" not in os.environ
    os.environ.setdefault("SUPABASE_URL", PLACEHOLDER_SUPABASE_URL)
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", PLACEHOLDER_SERVICE_ROLE_KEY)
    try:
        if analyzer_module is None:
            sys.path.insert(0, str(REPO))
            from app.ai_analysis import pdf_vision_analyzer  # noqa: PLC0415
            analyzer_module = pdf_vision_analyzer

        if not analyzer_module.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Run with it exported, e.g.\n"
                "    ANTHROPIC_API_KEY=sk-ant-... python scripts/capture_real_pdf_extraction.py ..."
            )
        if first_page < 1 or max_pages < 1:
            raise RuntimeError("--first-page and --max-pages must be positive integers.")

        pdf = Path(pdf_path)
        if not pdf.exists():
            raise RuntimeError(f"PDF not found: {pdf}")
        out = Path(capture_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        image_dir = out.parent / (out.stem + "_page_images")

        upload, backend, reason = choose_image_backend(
            supabase_configured, storage_drawing_uuid, image_dir
        )
        if upload is not None:
            analyzer_module.upload_page_image = upload
        print(reason)

        storage_id = storage_drawing_identity(backend, drawing_id, storage_drawing_uuid)
        print(f"Analysing pages {first_page}-{first_page + max_pages - 1} of {pdf} (real vision calls)...")
        pages = analyzer_module.analyze_pdf_pages(
            str(pdf), user_id, project_id, storage_id, max_pages, first_page=first_page
        )
        out.write_text(
            json.dumps([dataclasses.asdict(p) for p in pages], indent=2) + "\n"
        )
    finally:
        if added_url:
            del os.environ["SUPABASE_URL"]
        if added_key:
            del os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    connections = [c for p in pages for c in p.raw_connections]
    materials = [c.get("material") for c in connections if c.get("material")]
    print(f"Wrote {out}")
    print(f"  pages: {len(pages)}  parse_failed: {sum(1 for p in pages if p.parse_failed)}")
    print(f"  members: {sum(len(p.raw_members) for p in pages)}  connections: {len(connections)}")
    print(f"  connections carrying material: {materials or 'NONE'}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="path to the real PDF to analyse")
    parser.add_argument("capture", help="path where the capture JSON is written")
    parser.add_argument("--user-id", default="7au")
    parser.add_argument("--project-id", default="PROJ-7AU-SELBY")
    parser.add_argument("--drawing-id", default="SELBY-C1136",
                        help="the drawing's human-readable identity (evidence layer; "
                             "never sent to a UUID-only repository field)")
    parser.add_argument("--storage-drawing-uuid", default=None,
                        help="a REAL drawings-table UUID, used only when page images "
                             "are uploaded to Supabase storage")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--first-page", type=int, default=1,
                        help="1-based page number to start analysing from (default 1)")
    args = parser.parse_args(argv)
    try:
        capture(
            args.pdf, args.capture,
            user_id=args.user_id, project_id=args.project_id,
            drawing_id=args.drawing_id, storage_drawing_uuid=args.storage_drawing_uuid,
            max_pages=args.max_pages, first_page=args.first_page,
        )
    except RuntimeError as error:
        print(f"capture failed: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
