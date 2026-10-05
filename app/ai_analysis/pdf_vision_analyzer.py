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
from pypdf import PdfReader

from app.config import ANTHROPIC_API_KEY, PDF_VISION_MODEL
from app.engineering_data.repository import upload_page_image
# Wave 3J Phase B — these two conditions previously raised a bare `RuntimeError`,
# which made them indistinguishable by type from each other and from anything else
# in the process. Both subclass `RuntimeError`, so every existing handler (and every
# existing `except RuntimeError`) still catches them and no control flow changed;
# what changed is that the two conditions are now separately nameable, which is
# what lets a persistence boundary classify them without reading their text.
from app.validation.failure_classification import ProviderNotConfigured, SourceUnreadable

EXTRACTION_SYSTEM_PROMPT = """You are analysing a structural engineering or architectural drawing page for steel fabrication information.

STRICT RULES — these are non-negotiable:
- Extract only information explicitly present on this drawing page. Do not design, calculate, assume, or invent anything.
- If a value is not shown on the page, use null — never guess or infer a typical value.
- If something is ambiguous or you are not confident, still report your best reading but lower the confidence score accordingly.
- Every steel member or connection you report must actually appear on this page — do not report items you recall from a different page.
- Do not assume mark-letter conventions (e.g. "B" does not always mean beam). Use context on the page itself.
- Report the mark exactly as labelled — do not append the section size to the mark string. If a callout reads "P1 89x5 SHS", the mark is "P1" and the section is "89x5 SHS", reported as separate fields.
- For connections: only report bolt/plate/weld specifications that are explicitly shown or dimensioned on this page. If a connection is referenced (e.g. "see Detail 4/S102") but not shown here, do not guess its contents — just note the detail reference.
- Material: report a material grade/specification ONLY when it is explicitly stated on this page for the connection (or for the members it joins when clearly associated with them). Preserve the exact wording — never normalise one designation into another (e.g. "AS/NZS 3678-300" is not "300PLUS"). Never infer a grade from the member's section size, member type, project location, convention, history or any other context; null when no material is stated.

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
      "material": "300PLUS" or null,
      "confidence": 88
    }
  ]
}

For "material": the exact designation as printed on the page (e.g. "300PLUS", "AS/NZS 3678-300", "S355") or null when the page states none.

Omit "bolts", "plates", or "welds" arrays entirely (or leave empty) if that information isn't shown for a connection — do not invent placeholder values.

If this page has no steel member or connection information at all (e.g. it's a cover sheet or an unrelated architectural elevation), respond with:
{"drawing_number": null, "drawing_title": null, "revision": null, "members": [], "connections": []}
"""


#: How much of an unparseable response is retained for diagnosis. Long enough to show
#: HOW the JSON broke — a prose preamble, a fence that was not stripped, a tail cut off
#: mid-object — and short enough that a whole page of the model's output is never
#: stored. E2E-002L could not tell "the model answered badly" from "the answer did not
#: fit" because nothing about the response survived the failure; this is the bound on
#: what survives.
DIAGNOSTIC_EXCERPT_CHARS = 500


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
    #: The provider's own reason for ending generation, recorded ONLY for a page whose
    #: response could not be parsed. `max_tokens` here is the whole difference between a
    #: model that answered badly and an answer that did not fit, and no amount of
    #: re-reading an empty payload can tell those apart afterwards. It is recorded, never
    #: interpreted: whatever the provider said is what is kept, including nothing.
    stop_reason: str | None = None
    #: The first `DIAGNOSTIC_EXCERPT_CHARS` characters of a response that could not be
    #: parsed, recorded for the same reason and under the same rule. Diagnosis only: no
    #: reader may treat it as a reading, and a page carrying one is still `parse_failed`
    #: with no members, no connections and no engineering data of any kind.
    raw_response_excerpt: str | None = None


def _extract_json(text: str) -> dict:
    """Claude is instructed to return only JSON, but strip code fences defensively in case it adds them."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(cleaned)


def _stop_reason_of(response) -> str | None:
    """How the provider says generation ended, or None when it says nothing.

    Read defensively and recorded verbatim. A response that carries no stop reason is
    not an error — this is diagnosis, so an absent one is stored as absent rather than
    guessed at or defaulted to a value that would read as a finding.
    """
    reason = getattr(response, "stop_reason", None)
    return reason if isinstance(reason, str) and reason else None


def render_pages_to_png(filepath: str, max_pages: int, *, first_page: int = 1) -> list[bytes]:
    """Render `max_pages` PDF pages starting at 1-based `first_page` to PNGs
    at a resolution good enough for a vision model to read drawing text."""
    images = convert_from_path(
        filepath, dpi=200, fmt="png",
        first_page=first_page, last_page=first_page + max_pages - 1,
    )
    pages = []
    for img in images:
        buf = BytesIO()
        img.save(buf, format="PNG")
        pages.append(buf.getvalue())
    return pages


def page_count_of(filepath: str) -> int | None:
    """How many pages the PDF document itself contains, or None.

    Milestone J15. This is the drawing set's own page count, read from the
    document — NOT the number of pages a run chose to render, and not the
    number it managed to analyse. `render_pages_to_png` above is capped by
    MAX_PDF_PAGES and silently returns fewer images when the document is
    longer than the cap, so the count of rendered pages cannot answer "was the
    whole drawing set read?" Only the document can.

    Deliberately total and fail-closed: an unreadable, encrypted, truncated or
    absent file returns None rather than raising. A page count that could not
    be established is a real answer — it is the answer "coverage cannot be
    proven" — and must not be allowed to abort an extraction whose pages were
    read successfully, nor to be replaced by a count that is merely available.
    """
    try:
        reader = PdfReader(filepath)
        count = len(reader.pages)
    except Exception:
        return None
    return count if count > 0 else None


def analyze_pdf_pages(
    filepath: str, user_id: str, project_id: str, drawing_id: str, max_pages: int,
    *, first_page: int = 1, store_page_image: bool = True,
) -> list[PageExtraction]:
    """
    Renders the pages, asks Claude what it sees, and returns the raw
    per-page results. `max_pages` pages are analysed starting at the
    1-based `first_page` (default 1: the drawing's first pages, as before);
    page numbers stay true — a later slice of the set reports its real
    page numbers. No matching, no validation, no database writes
    to engineering-data tables (page images ARE persisted here, since
    that's source-of-truth storage, not an engineering interpretation).

    `store_page_image=False` reads the pages without storing them again, for a
    caller that is re-reading pages it has already rendered and stored once
    (Milestone J17's single-page retry of a page whose response could not be
    parsed). This function stores each page's image BEFORE it asks the model
    about it, so every page it has ever analysed — a parse failure included —
    already has its stored image; storing it a second time would record one page
    of one drawing twice. The default keeps every existing caller's behaviour
    exactly as it was.
    """
    if not ANTHROPIC_API_KEY:
        raise ProviderNotConfigured(
            "PDF drawing analysis requires ANTHROPIC_API_KEY to be set on the API service. "
            "Get one at console.anthropic.com and add it as a Railway environment variable."
        )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    pages = render_pages_to_png(filepath, max_pages, first_page=first_page)
    if not pages:
        raise SourceUnreadable("Could not render any pages from this PDF.")

    results: list[PageExtraction] = []

    for page_num, page_bytes in enumerate(pages, start=first_page):
        if store_page_image:
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
            #
            # The failure is recorded WITH what the provider said about it and a bounded
            # prefix of what it said, because those are the only two facts that separate
            # a model answering badly from an answer that did not fit — and once this
            # branch was taken, nothing else about the response survived anywhere.
            # Neither is a reading: the page is still parse_failed, with no members and
            # no connections, exactly as before.
            results.append(PageExtraction(
                page_number=page_num,
                parse_failed=True,
                stop_reason=_stop_reason_of(response),
                raw_response_excerpt=raw_text[:DIAGNOSTIC_EXCERPT_CHARS],
            ))
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
