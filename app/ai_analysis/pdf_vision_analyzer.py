"""
AI analysis module — the ONLY place in the codebase that talks to
Claude's vision API. Its job is narrow and deliberate: render PDF
pages to images, ask Claude what's on each page, and hand back
exactly what it said.

This module does NOT:
  - match sections against the steel_sections reference table
  - decide what's steel vs. timber vs. junk
  - resolve duplicate/conflicting marks across pages
  - write anything to steel_members, connections, or any other
    engineering-data table

Those are the validation and engineering_data modules' jobs. Keeping
this module "dumb" (it reports what it saw, nothing more) means the
one component making non-deterministic AI calls has the smallest
possible surface area, and every decision downstream of it is
ordinary, testable Python logic.
"""
import base64
import json
import re
from dataclasses import dataclass, field
from io import BytesIO

from pdf2image import convert_from_path
from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY, PDF_VISION_MODEL
from app.engineering_data.repository import upload_page_image

EXTRACTION_SYSTEM_PROMPT = """You are analysing a structural engineering or architectural drawing page for steel fabrication information.

STRICT RULES — these are non-negotiable:
- Extract only information explicitly present on this drawing page. Do not design, calculate, assume, or invent anything.
- If a value is not shown on the page, use null — never guess or infer a typical value.
- If something is ambiguous or you are not confident, still report your best reading but lower the confidence score accordingly.
- Every steel member or connection you report must actually appear on this page — do not report items you recall from a different page.
- Do not assume mark-letter conventions (e.g. "B" does not always mean beam). Use context on the page itself.
- Report the mark exactly as labelled — do not append the section size to the mark string. If a callout reads "P1 89x5 SHS", the mark is "P1" and the section is "89x5 SHS", reported as separate fields.
- For connections: only report bolt/plate/weld specifications that are explicitly shown or dimensioned on this page. If a connection is referenced (e.g. "see Detail 4/S102") but not shown here, do not guess its contents — just note the detail reference.

For this page, identify:
1. Drawing metadata if visible: drawing number, title, revision.
2. Every identifiable steel member: mark, member type if stated, section/size, quantity if stated, length in mm if explicitly dimensioned (else null), grid or level reference if shown, detail reference if shown.
3. Every identifiable connection: which member marks it joins, connection type, and any bolt/plate/weld specifications explicitly shown.

Respond with ONLY valid JSON, no other text, in exactly this shape:
{
  "drawing_number": "S101" or null,
  "drawing_title": "Framing Plan" or null,
  "revision": "B" or null,
  "members": [
    {
      "mark": "B8",
      "member_type": "beam",
      "section": "250UB37",
      "quantity": 1,
      "length_mm": null,
      "grid_reference": "A-B/2",
      "detail_reference": "D15",
      "confidence": 92
    }
  ],
  "connections": [
    {
      "detail_reference": "D15",
      "grid_reference": "A-B/2",
      "connects_members": ["B8", "C1"],
      "connection_type": "bolted",
      "bolts": [{"quantity": 4, "size": "M20", "grade": "8.8"}],
      "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
      "welds": [{"type": "fillet", "size_mm": 8}],
      "confidence": 88
    }
  ]
}

Omit "bolts", "plates", or "welds" arrays entirely (or leave empty) if that information isn't shown for a connection — do not invent placeholder values.

If this page has no steel member or connection information at all (e.g. it's a cover sheet or an unrelated architectural elevation), respond with:
{"drawing_number": null, "drawing_title": null, "revision": null, "members": [], "connections": []}
"""


@dataclass
class PageExtraction:
    """Exactly what Claude reported for one page — untouched, unmatched, unvalidated."""
    page_number: int
    drawing_number: str | None = None
    drawing_title: str | None = None
    revision: str | None = None
    raw_members: list[dict] = field(default_factory=list)
    raw_connections: list[dict] = field(default_factory=list)
    parse_failed: bool = False


def _extract_json(text: str) -> dict:
    """Claude is instructed to return only JSON, but strip code fences defensively in case it adds them."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(cleaned)


def render_pages_to_png(filepath: str, max_pages: int) -> list[bytes]:
    """Render each PDF page to a PNG at a resolution good enough for a vision model to read drawing text."""
    images = convert_from_path(filepath, dpi=200, fmt="png")
    pages = []
    for img in images[:max_pages]:
        buf = BytesIO()
        img.save(buf, format="PNG")
        pages.append(buf.getvalue())
    return pages


def analyze_pdf_pages(
    filepath: str, user_id: str, project_id: str, drawing_id: str, max_pages: int
) -> list[PageExtraction]:
    """
    Renders every page, asks Claude what it sees, and returns the raw
    per-page results. No matching, no validation, no database writes
    to engineering-data tables (page images ARE persisted here, since
    that's source-of-truth storage, not an engineering interpretation).
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "PDF drawing analysis requires ANTHROPIC_API_KEY to be set on the API service. "
            "Get one at console.anthropic.com and add it as a Railway environment variable."
        )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    pages = render_pages_to_png(filepath, max_pages)
    if not pages:
        raise RuntimeError("Could not render any pages from this PDF.")

    results: list[PageExtraction] = []

    for page_num, page_bytes in enumerate(pages, start=1):
        upload_page_image(user_id, project_id, drawing_id, page_num, page_bytes)

        b64_image = base64.standard_b64encode(page_bytes).decode("utf-8")
        response = client.messages.create(
            model=PDF_VISION_MODEL,
            max_tokens=3000,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_image}},
                    {"type": "text", "text": f"This is page {page_num} of the drawing set. Extract per the instructions."},
                ],
            }],
        )

        raw_text = "".join(block.text for block in response.content if block.type == "text")
        try:
            page_data = _extract_json(raw_text)
        except (json.JSONDecodeError, AttributeError):
            # A single unparseable page shouldn't kill the whole extraction —
            # record it as failed and keep going with the rest of the set.
            results.append(PageExtraction(page_number=page_num, parse_failed=True))
            continue

        results.append(PageExtraction(
            page_number=page_num,
            drawing_number=page_data.get("drawing_number"),
            drawing_title=page_data.get("drawing_title"),
            revision=page_data.get("revision"),
            raw_members=page_data.get("members", []),
            raw_connections=page_data.get("connections", []),
        ))

    return results
