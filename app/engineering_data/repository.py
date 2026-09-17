"""
Engineering data repository — the only module that writes extracted
structural information into Supabase. Pure persistence: it takes
already-decided values and stores them. It does not call the AI, and
it does not decide what's valid — that's the validation module's job,
upstream of this one in the pipeline.
"""
from app.supabase_client import supabase


def upload_page_image(user_id: str, project_id: str, drawing_id: str, page_number: int, image_bytes: bytes) -> str:
    """Stores a rendered drawing page so the review UI can display exactly what the model analysed."""
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


def create_drawing_set(project_id: str, name: str) -> dict:
    return supabase.table("drawing_sets").insert({
        "project_id": project_id, "name": name, "status": "processing",
    }).execute().data[0]


def create_drawing(drawing_set_id: str, file_name: str, storage_path: str) -> dict:
    return supabase.table("drawings").insert({
        "drawing_set_id": drawing_set_id,
        "file_name": file_name,
        "storage_path": storage_path,
        "discipline": "structural",  # assumed for this milestone; multi-file discipline detection is later
    }).execute().data[0]


def create_analysis_run(drawing_set_id: str, model_used: str) -> dict:
    return supabase.table("analysis_runs").insert({
        "drawing_set_id": drawing_set_id, "status": "running", "model_used": model_used,
    }).execute().data[0]


def update_analysis_run(analysis_run_id: str, **fields) -> None:
    supabase.table("analysis_runs").update(fields).eq("id", analysis_run_id).execute()


def update_drawing_set(drawing_set_id: str, **fields) -> None:
    supabase.table("drawing_sets").update(fields).eq("id", drawing_set_id).execute()


def update_drawing_meta(drawing_id: str, page_count: int, drawing_number: str | None,
                          drawing_title: str | None, revision: str | None) -> None:
    supabase.table("drawings").update({
        "page_count": page_count,
        "drawing_number": drawing_number,
        "drawing_title": drawing_title,
        "revision": revision,
    }).eq("id", drawing_id).execute()


def insert_members(rows: list[dict]) -> list[dict]:
    """Bulk-inserts finalised (already matched + validated) member rows. Returns the inserted rows with real IDs."""
    if not rows:
        return []
    return supabase.table("steel_members").insert(rows).execute().data


def insert_review_items(rows: list[dict]) -> None:
    if rows:
        supabase.table("review_items").insert(rows).execute()


def insert_connection(row: dict) -> dict:
    return supabase.table("connections").insert(row).execute().data[0]


def insert_bolt_groups(connection_id: str, bolts: list[dict]) -> None:
    if bolts:
        supabase.table("bolt_groups").insert(
            [{**b, "connection_id": connection_id} for b in bolts]
        ).execute()


def insert_connection_plates(connection_id: str, plates: list[dict]) -> None:
    if plates:
        supabase.table("connection_plates").insert(
            [{**p, "connection_id": connection_id} for p in plates]
        ).execute()


def insert_weld_details(connection_id: str, welds: list[dict]) -> None:
    if welds:
        supabase.table("weld_details").insert(
            [{**w, "connection_id": connection_id} for w in welds]
        ).execute()


def link_connection_members(connection_id: str, member_ids: list[str]) -> None:
    if member_ids:
        supabase.table("connection_members").insert(
            [{"connection_id": connection_id, "member_id": mid} for mid in member_ids]
        ).execute()


def update_project_summary(project_id: str, **fields) -> None:
    supabase.table("projects").update(fields).eq("id", project_id).execute()
