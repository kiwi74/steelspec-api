"""
Top-level orchestrator — the only module that coordinates across
ai_analysis, validation, and engineering_data. main.py stays thin
(HTTP concerns only); this owns the multi-stage flow:

    PDF -> ai_analysis (Claude vision, per page)
        -> validation (classify, consolidate, flag)
        -> engineering_data (persist)

Kept as a single top-level module rather than nested under any one
of the three stages, since it depends on all of them and belongs to
none.
"""
from app.ai_analysis.pdf_vision_analyzer import analyze_pdf_pages
from app.engineering_data import repository as repo
from app.engineering_data.section_matcher import SectionMatcher
from app.validation.rules import validate_extraction
from app.config import PDF_VISION_MODEL, MAX_PDF_PAGES


def parse_pdf_and_save(filepath: str, project_id: str, user_id: str, storage_path: str) -> dict:
    """
    Parses a PDF drawing set and writes validated results into
    Supabase against the given project_id. Same name and signature as
    the function this replaces, so the API layer's call site is
    unaffected by this restructure.
    """
    import os
    file_name = os.path.basename(storage_path)

    drawing_set = repo.create_drawing_set(project_id, file_name)
    drawing_set_id = drawing_set["id"]

    drawing = repo.create_drawing(drawing_set_id, file_name, storage_path)
    drawing_id = drawing["id"]

    analysis_run = repo.create_analysis_run(drawing_set_id, PDF_VISION_MODEL)
    analysis_run_id = analysis_run["id"]

    try:
        result = _run_pipeline(filepath, project_id, user_id, drawing_set_id, drawing_id)
    except Exception as e:
        repo.update_analysis_run(analysis_run_id, status="failed", error_message=str(e)[:500])
        repo.update_drawing_set(drawing_set_id, status="failed", error_message=str(e)[:500])
        raise

    repo.update_analysis_run(
        analysis_run_id, status="completed",
        pages_processed=result["pages_processed"], total_pages=result["pages_processed"],
        completed_at="now()",
    )
    repo.update_drawing_set(
        drawing_set_id, status="analyzed",
        total_pages=result["pages_processed"], pages_analysed=result["pages_processed"],
        members_found=result["members_extracted"], review_required_count=result["review_required_count"],
    )

    return result


def _run_pipeline(filepath: str, project_id: str, user_id: str, drawing_set_id: str, drawing_id: str) -> dict:
    matcher = SectionMatcher()

    # === Stage 1: AI analysis — pure reading, page by page ===
    pages = analyze_pdf_pages(filepath, user_id, project_id, drawing_id, MAX_PDF_PAGES)
    if not pages:
        raise RuntimeError("Could not render any pages from this PDF.")

    drawing_meta = next(
        ({"drawing_number": p.drawing_number, "drawing_title": p.drawing_title, "revision": p.revision}
         for p in pages if p.page_number == 1),
        {"drawing_number": None, "drawing_title": None, "revision": None},
    )

    # Flatten every page's raw members into one list, tagging each with its source page
    # and this drawing's ID, ready for validation to classify/consolidate across pages.
    raw_members = []
    for p in pages:
        for m in p.raw_members:
            raw_members.append({**m, "source_page": p.page_number, "source_drawing_id": drawing_id})

    connections_raw = []
    for p in pages:
        for c in p.raw_connections:
            connections_raw.append({**c, "page_num": p.page_number})

    # === Stage 2: Validation — classify, consolidate, flag ===
    validated = validate_extraction(raw_members, matcher)

    # === Stage 3: Engineering data — persist the validated results ===
    members_to_insert = []
    for m in validated["members"]:
        confidence_score = m.get("confidence")
        if confidence_score is None:
            confidence_tier = "medium"
        elif confidence_score >= 95:
            confidence_tier = "high"
        elif confidence_score >= 80:
            confidence_tier = "medium"
        else:
            confidence_tier = "low"

        review = m.get("review_status") or ("review_required" if (confidence_tier == "low" or m["category"] == "unmatched_steel") else "extracted")

        matched = m.get("matched")
        weight_per_metre = matched.get("weight_per_metre") if matched else None
        length_mm = m.get("length_mm")
        total_weight_kg = (
            round((length_mm / 1000) * weight_per_metre * (m.get("quantity") or 1), 2)
            if (matched and weight_per_metre and length_mm) else None
        )

        members_to_insert.append({
            "project_id": project_id,
            "mark": m.get("mark") or "?",
            "section_name": m.get("section_name"),
            "section_name_raw": m.get("section"),
            "section_family": matched["family"] if matched else None,
            "length_mm": length_mm,
            "grade": "300PLUS",
            "quantity": m.get("quantity") or 1,
            "weight_per_metre": weight_per_metre,
            "total_weight_kg": total_weight_kg,
            "confidence": confidence_tier,
            "confidence_score": confidence_score,
            "source_page": m.get("source_page"),
            "source_drawing_id": m.get("source_drawing_id"),
            "extraction_method": "vision_claude",
            "review_status": review,
            "detail_reference": m.get("detail_reference"),
            "notes": m.get("validation_note") or (f"Grid: {m['grid_reference']}" if m.get("grid_reference") else None),
        })

    inserted_members = repo.insert_members(members_to_insert)

    if inserted_members:
        repo.insert_review_items([
            {"project_id": project_id, "item_type": "steel_member", "item_id": row["id"], "status": row["review_status"]}
            for row in inserted_members
        ])

    # === Connections: resolve mark strings to real member IDs, now that members are inserted ===
    mark_to_id = {row["mark"]: row["id"] for row in inserted_members if row.get("mark")}
    valid_connection_types = {"bolted", "welded", "bolted_and_welded", "unspecified"}
    connections_extracted = 0
    connections_review_required = 0

    for c in connections_raw:
        conn_type = (c.get("connection_type") or "unspecified").lower().replace(" ", "_")
        if conn_type not in valid_connection_types:
            bolts, welds = c.get("bolts") or [], c.get("welds") or []
            conn_type = "bolted_and_welded" if (bolts and welds) else ("bolted" if bolts else "welded" if welds else "unspecified")

        confidence_score = c.get("confidence")
        if confidence_score is None:
            confidence_tier = "medium"
        elif confidence_score >= 95:
            confidence_tier = "high"
        elif confidence_score >= 80:
            confidence_tier = "medium"
        else:
            confidence_tier = "low"
        review = "review_required" if confidence_tier == "low" else "extracted"

        bolts, plates, welds = c.get("bolts") or [], c.get("plates") or [], c.get("welds") or []
        description_bits = (
            [f'{b.get("quantity", "?")}x {b.get("size", "?")} Gr{b.get("grade", "?")}' for b in bolts]
            + [f'{p.get("type", "plate")} {p.get("thickness_mm", "?")}mm' for p in plates]
            + [f'{w.get("size_mm", "?")}mm {w.get("type", "")} weld' for w in welds]
        )

        conn_row = repo.insert_connection({
            "project_id": project_id,
            "connection_type": conn_type,
            "grid_reference": c.get("grid_reference"),
            "detail_reference": c.get("detail_reference"),
            "description": " — ".join(description_bits)[:500] if description_bits else None,
            "confidence": confidence_tier,
            "confidence_score": confidence_score,
            "source_page": c.get("page_num"),
            "source_drawing_id": drawing_id,
            "extraction_method": "vision_claude",
            "review_status": review,
            "notes": "Extracted from PDF drawing via vision analysis",
        })
        connection_id = conn_row["id"]
        connections_extracted += 1
        if review == "review_required":
            connections_review_required += 1

        repo.insert_bolt_groups(connection_id, [
            {"bolt_size": b.get("size") or "?", "bolt_grade": b.get("grade") or "?", "quantity": b.get("quantity") or 1}
            for b in bolts
        ])
        repo.insert_connection_plates(connection_id, [
            {"plate_type": p.get("type") or "plate", "thickness": p.get("thickness_mm") or 0,
             "width": p.get("width_mm"), "depth": p.get("depth_mm"), "grade": "300"}
            for p in plates
        ])
        repo.insert_weld_details(connection_id, [
            {"weld_type": w.get("type") or "fillet", "size": w.get("size_mm")} for w in welds
        ])

        linked_ids = [mark_to_id[mark] for mark in (c.get("connects_members") or []) if mark in mark_to_id]
        repo.link_connection_members(connection_id, linked_ids)

        repo.insert_review_items([{"project_id": project_id, "item_type": "connection", "item_id": connection_id, "status": review}])

    # === Roll up and persist project-level summary, including validation warnings ===
    repo.update_drawing_meta(drawing_id, len(pages), drawing_meta["drawing_number"], drawing_meta["drawing_title"], drawing_meta["revision"])

    total_weight_kg = sum(m["total_weight_kg"] or 0 for m in members_to_insert)
    unique_sections = len(set(m["section_name"] for m in members_to_insert if m["section_name"]))
    members_review_required = sum(1 for m in members_to_insert if m["review_status"] == "review_required")
    review_required_count = members_review_required + connections_review_required

    warning_messages = [issue.message for issue in validated["issues"]]
    unmatched_sections = sorted({
        m["section_name_raw"] for m in members_to_insert
        if not m["section_name"] and m["section_name_raw"]
    })

    repo.update_project_summary(
        project_id,
        status="review",
        total_members=len(members_to_insert),
        total_unique_sections=unique_sections,
        total_connections=connections_extracted,
        total_weight_kg=total_weight_kg,
        total_weight_tonnes=round(total_weight_kg / 1000, 3),
        unmatched_sections=unmatched_sections,
        warnings=warning_messages,
        engineer_reference=drawing_meta["drawing_number"],
        structural_engineer=drawing_meta["drawing_title"],
    )

    return {
        "pages_processed": len(pages),
        "members_extracted": len(members_to_insert),
        "connections_extracted": connections_extracted,
        "unique_sections": unique_sections,
        "review_required_count": review_required_count,
        "total_weight_kg": total_weight_kg,
        "excluded_non_steel": len(validated["excluded"]),
        "warnings": warning_messages,
    }
