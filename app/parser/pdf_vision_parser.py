"""
PDF drawing vision parser — the first-milestone implementation of
the "PDF structural drawing analysis" feature.

Scope, deliberately narrow (per the build spec's own recommended
sequencing): PDF -> Steel Member Schedule with source page + a
plain confidence score. Connection extraction, cross-sheet
reasoning, and the split-pane source viewer are NOT built yet —
see the module docstring at the bottom for what's intentionally
deferred and why.

Storage hierarchy: one drawing_sets row per analysis (currently one
per upload — multi-PDF grouping is a later milestone), containing
one drawings row for the uploaded PDF, containing one drawing_pages
row per rendered page. Extracted members reference the drawing via
a real foreign key (source_drawing_id) rather than a free-text
drawing number, so "view source" can always resolve back to the
exact file and page.

Pipeline:
  1. Render every page of the uploaded PDF to a PNG (native PDF
     text alone is not reliable for drawings — position, symbols,
     and schedule tables matter more than raw text).
  2. Upload each rendered page to a private Storage bucket and
     record it in drawing_pages, so a future "view source" UI can
     display exactly what the model saw.
  3. Send each page image to Claude with a strict extraction-only
     system prompt: never invent, never design, mark missing data
     as NOT SPECIFIED, and always report a confidence figure.
  4. Parse the model's structured JSON response, match each
     reported section against the steel_sections reference table,
     and write real rows into steel_members with source_page,
     source_drawing_id, and confidence_score attached.
  5. Record one review_items row per extracted member, and roll the
     whole run up into an analysis_runs record for history.
"""
import base64
import json
import os
import re
from datetime import datetime, timezone
from io import BytesIO

from pdf2image import convert_from_path
from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY, PDF_VISION_MODEL, MAX_PDF_PAGES
from app.parser.section_matcher import SectionMatcher
from app.supabase_client import supabase

EXTRACTION_SYSTEM_PROMPT = """You are analysing a structural engineering or architectural drawing page for steel fabrication information.

STRICT RULES — these are non-negotiable:
- Extract only information explicitly present on this drawing page. Do not design, calculate, assume, or invent anything.
- If a value is not shown on the page, use null — never guess or infer a typical value.
- If something is ambiguous or you are not confident, still report your best reading but lower the confidence score accordingly.
- Every steel member you report must actually appear on this page — do not report members you recall from a different page.
- Do not assume mark-letter conventions (e.g. "B" does not always mean beam). Use context on the page itself.

For this page, identify:
1. Drawing metadata if visible: drawing number, title, revision.
2. Every identifiable steel member: mark, member type if stated, section/size, quantity if stated, length in mm if explicitly dimensioned (else null), grid or level reference if shown, detail reference if shown.

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
  ]
}

If this page has no steel member information at all (e.g. it's a cover sheet or an unrelated architectural elevation), respond with:
{"drawing_number": null, "drawing_title": null, "revision": null, "members": []}
"""


def _extract_json(text: str) -> dict:
    """Claude is instructed to return only JSON, but strip code fences defensively in case it adds them."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(cleaned)


def _render_pages_to_png(filepath: str, max_pages: int) -> list[bytes]:
    """Render each PDF page to a PNG at a resolution good enough for a vision model to read drawing text."""
    images = convert_from_path(filepath, dpi=200, fmt="png")
    pages = []
    for img in images[:max_pages]:
        buf = BytesIO()
        img.save(buf, format="PNG")
        pages.append(buf.getvalue())
    return pages


def _upload_page_image(user_id: str, project_id: str, drawing_id: str, page_number: int, image_bytes: bytes) -> str:
    """Stores the rendered page so a future 'view source' UI can display exactly what the model analysed."""
    path = f"{user_id}/{project_id}/drawings/{drawing_id}/page-{page_number}.png"
    supabase.storage.from_("drawing-pages").upload(
        path, image_bytes, file_options={"content-type": "image/png", "upsert": "true"}
    )
    supabase.table("drawing_pages").insert({
        "drawing_id": drawing_id,
        "page_number": page_number,
        "image_storage_path": path,
        "has_text_layer": False,  # native text extraction isn't wired into this milestone yet
    }).execute()
    return path


def parse_pdf_and_save(filepath: str, project_id: str, user_id: str, storage_path: str) -> dict:
    """
    Parse a PDF drawing set with Claude vision and write extracted
    members into Supabase against the given project_id.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "PDF drawing analysis requires ANTHROPIC_API_KEY to be set on the API service. "
            "Get one at console.anthropic.com and add it as a Railway environment variable."
        )

    file_name = os.path.basename(storage_path)

    # === Set up the drawing_sets -> drawings -> analysis_runs bookkeeping ===
    # One drawing set and one drawing per upload for this milestone —
    # grouping several PDFs into one set is a later milestone.
    drawing_set = supabase.table("drawing_sets").insert({
        "project_id": project_id,
        "name": file_name,
        "status": "processing",
    }).execute().data[0]
    drawing_set_id = drawing_set["id"]

    drawing = supabase.table("drawings").insert({
        "drawing_set_id": drawing_set_id,
        "file_name": file_name,
        "storage_path": storage_path,
        "discipline": "structural",  # assumed for this milestone; multi-file discipline detection is later
    }).execute().data[0]
    drawing_id = drawing["id"]

    analysis_run = supabase.table("analysis_runs").insert({
        "drawing_set_id": drawing_set_id,
        "status": "running",
        "model_used": PDF_VISION_MODEL,
    }).execute().data[0]
    analysis_run_id = analysis_run["id"]

    try:
        result = _run_extraction(filepath, project_id, user_id, drawing_set_id, drawing_id)
    except Exception as e:
        supabase.table("analysis_runs").update({
            "status": "failed", "error_message": str(e)[:500],
        }).eq("id", analysis_run_id).execute()
        supabase.table("drawing_sets").update({
            "status": "failed", "error_message": str(e)[:500],
        }).eq("id", drawing_set_id).execute()
        raise

    supabase.table("analysis_runs").update({
        "status": "completed",
        "pages_processed": result["pages_processed"],
        "total_pages": result["pages_processed"],
        "completed_at": "now()",
    }).eq("id", analysis_run_id).execute()

    supabase.table("drawing_sets").update({
        "status": "analyzed",
        "total_pages": result["pages_processed"],
        "pages_analysed": result["pages_processed"],
        "members_found": result["members_extracted"],
        "review_required_count": result["review_required_count"],
    }).eq("id", drawing_set_id).execute()

    return result


def _run_extraction(filepath: str, project_id: str, user_id: str, drawing_set_id: str, drawing_id: str) -> dict:
    """The actual page-by-page vision extraction loop, separated out so
    parse_pdf_and_save can wrap it with drawing_sets/analysis_runs bookkeeping."""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    matcher = SectionMatcher()

    pages = _render_pages_to_png(filepath, MAX_PDF_PAGES)
    if not pages:
        raise RuntimeError("Could not render any pages from this PDF.")

    members_to_insert = []
    unmatched_sections = set()
    drawing_meta = {"drawing_number": None, "drawing_title": None, "revision": None}
    counter = 0

    for page_num, page_bytes in enumerate(pages, start=1):
        _upload_page_image(user_id, project_id, drawing_id, page_num, page_bytes)

        b64_image = base64.standard_b64encode(page_bytes).decode("utf-8")
        response = client.messages.create(
            model=PDF_VISION_MODEL,
            max_tokens=2000,
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
            # skip it and keep going with the rest of the drawing set.
            continue

        if page_num == 1:
            drawing_meta = {
                "drawing_number": page_data.get("drawing_number"),
                "drawing_title": page_data.get("drawing_title"),
                "revision": page_data.get("revision"),
            }

        for m in page_data.get("members", []):
            raw_section = m.get("section")
            if not raw_section:
                continue

            matched = matcher.match(raw_section)
            counter += 1
            confidence_score = m.get("confidence")
            # Bucket the numeric score into the existing categorical
            # column so it still drives the report's status pill,
            # while confidence_score keeps the precise figure.
            if confidence_score is None:
                confidence_tier = "medium"
            elif confidence_score >= 95:
                confidence_tier = "high"
            elif confidence_score >= 80:
                confidence_tier = "medium"
            else:
                confidence_tier = "low"

            # Per the spec: newly extracted items are never auto-'confirmed' —
            # that status is reserved for an explicit human review action.
            review = "review_required" if confidence_tier == "low" else "extracted"

            weight_per_metre = matched.get("weight_per_metre") if matched else None
            length_mm = m.get("length_mm")
            total_weight_kg = (
                round((length_mm / 1000) * weight_per_metre * (m.get("quantity") or 1), 2)
                if (matched and weight_per_metre and length_mm) else None
            )

            if not matched:
                unmatched_sections.add(raw_section)

            members_to_insert.append({
                "project_id": project_id,
                "mark": m.get("mark") or f"M{counter}",
                "section_name": matched["name"] if matched else None,
                "section_name_raw": raw_section,
                "section_family": matched["family"] if matched else None,
                "length_mm": length_mm,
                "grade": "300PLUS",
                "quantity": m.get("quantity") or 1,
                "weight_per_metre": weight_per_metre,
                "total_weight_kg": total_weight_kg,
                "confidence": confidence_tier,
                "confidence_score": confidence_score,
                "source_page": page_num,
                "source_drawing_id": drawing_id,
                "extraction_method": "vision_claude",
                "review_status": review,
                "detail_reference": m.get("detail_reference"),
                "notes": f"Grid: {m['grid_reference']}" if m.get("grid_reference") else None,
            })

    # Insert members and capture their real IDs so we can create
    # matching review_items rows referencing them.
    inserted_members = []
    if members_to_insert:
        inserted_members = supabase.table("steel_members").insert(members_to_insert).execute().data

    if inserted_members:
        review_rows = [
            {
                "project_id": project_id,
                "item_type": "steel_member",
                "item_id": row["id"],
                "status": row["review_status"],
            }
            for row in inserted_members
        ]
        supabase.table("review_items").insert(review_rows).execute()

    # Record what we learned about the drawing itself, now that all
    # pages have been processed.
    supabase.table("drawings").update({
        "page_count": len(pages),
        "drawing_number": drawing_meta.get("drawing_number"),
        "drawing_title": drawing_meta.get("drawing_title"),
        "revision": drawing_meta.get("revision"),
    }).eq("id", drawing_id).execute()

    total_weight_kg = sum(m["total_weight_kg"] or 0 for m in members_to_insert)
    unique_sections = len(set(m["section_name"] for m in members_to_insert if m["section_name"]))
    review_required_count = sum(1 for m in members_to_insert if m["review_status"] == "review_required")

    supabase.table("projects").update({
        "status": "review",
        "total_members": len(members_to_insert),
        "total_unique_sections": unique_sections,
        "total_weight_kg": total_weight_kg,
        "total_weight_tonnes": round(total_weight_kg / 1000, 3),
        "unmatched_sections": list(unmatched_sections),
        "engineer_reference": drawing_meta.get("drawing_number"),
        "structural_engineer": drawing_meta.get("drawing_title"),
    }).eq("id", project_id).execute()

    return {
        "pages_processed": len(pages),
        "members_extracted": len(members_to_insert),
        "unique_sections": unique_sections,
        "review_required_count": review_required_count,
        "total_weight_kg": total_weight_kg,
    }


# === DELIBERATELY DEFERRED (see build spec phases not covered here) ===
#
# - Connection extraction from PDF (bolts/plates/welds via vision) — Phase 4.
#   DXF connection extraction is live; PDF connection extraction is not yet.
# - Cross-sheet reasoning linking an architectural mention of a mark to its
#   structural size on a different sheet — Phase 5.
# - A proper drawing register table and multi-file "same project" grouping
#   for architectural + structural + details PDFs uploaded together — Phase 1, 11.
# - The split-pane "click a row, see the highlighted source region" review
#   UI — Phase 7-8. Today the rendered page image is stored, but there's no
#   frontend to view it yet or highlight the specific region a value came from.
# - Revision comparison — Phase 16.
#
# These are the logical next slices once the core loop above is validated
# against real drawings.