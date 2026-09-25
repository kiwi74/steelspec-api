"""
Engineering data repository — the only module that writes extracted
structural information into Supabase. Pure persistence: it takes
already-decided values and stores them. It does not call the AI, and
it does not decide what's valid — that's the validation module's job,
upstream of this one in the pipeline.

Milestone J16 added the READ half of this module (the `*_for_*` functions at
the end): a continuation of an extraction that stopped at the page cap has to
know what the project, its drawing set and its persisted members already state
before it may add a window to them. These reads decide nothing — they return
columns exactly as stored, and the caller treats a missing row or a NULL as the
absence it is.
"""
from app.engineering_data.page_extraction_capture import CAPTURE_COLUMNS
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


def update_drawing_meta(drawing_id: str, page_count: int | None, drawing_number: str | None,
                          drawing_title: str | None, revision: str | None) -> None:
    """Records the drawing's own page count — `None` when the document did not
    state one that could be read (the column is nullable, so absence is stored
    as absence)."""
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


def insert_page_extraction_captures(rows: list[dict]) -> int:
    """Records one run's raw AI readings, in a SINGLE insert statement. Returns how many.

    One statement for the whole run, so a run's readings are recorded together or not
    at all: PostgREST executes a single insert in one transaction, and a half-recorded
    run would be indistinguishable from a run that genuinely read fewer pages.

    There is no per-page loop and no update, delete or upsert anywhere in this
    function — the table is append-only and this is its only writer. Re-reading a page
    is a new run and therefore a new row; a repeat of the same (drawing, page, run) is
    refused by the primary key rather than silently ignored, because that pair IS the
    attempt and a second reading by the same run is a mistake, not an attempt.
    """
    if not rows:
        return 0
    supabase.table("page_extraction_captures").insert(rows).execute()
    return len(rows)


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


# =============================================================================
# Reads (Milestone J16) — what a continuation must know before it adds to it.
# =============================================================================
# The column lists are explicit rather than "*": this module returns exactly the
# persisted facts its caller uses, so a read cannot quietly become a decision.

def get_project(project_id: str) -> dict | None:
    """The project row as persisted, or None when there is no such project.

    `None` is the absence of a project, never a blank project: a caller that
    cannot find one has nothing to continue and says so.
    """
    return supabase.table("projects").select("*").eq("id", project_id).single().execute().data or None


def drawing_sets_for_project(project_id: str) -> list[dict]:
    """Every drawing set this project has, with the counters it carries.

    A project normally has exactly one PDF drawing set. A caller that continues
    an extraction must refuse to choose between several (see J16): picking one
    would be deciding WHICH document the new pages belong to.
    """
    return (
        supabase.table("drawing_sets")
        .select("id,project_id,name,status,total_pages,pages_analysed")
        .eq("project_id", project_id)
        .execute()
        .data
    ) or []


def drawings_for_drawing_set(drawing_set_id: str) -> list[dict]:
    """The drawings of one drawing set, with the source file each was read from."""
    return (
        supabase.table("drawings")
        .select("id,drawing_set_id,file_name,storage_path,page_count")
        .eq("drawing_set_id", drawing_set_id)
        .execute()
        .data
    ) or []


def member_rows_for_project(project_id: str) -> list[dict]:
    """Every persisted steel member of this project, with its identity and state.

    `total_weight_kg` is read because a project summary is a SUM over its rows,
    not a per-run number that a later window could add to without re-reading
    them; `mark` is read because it is the identity the pipeline already
    consolidates members on within one run.
    """
    return (
        supabase.table("steel_members")
        .select("id,mark,section_name,review_status,total_weight_kg")
        .eq("project_id", project_id)
        .execute()
        .data
    ) or []


def connection_rows_for_project(project_id: str) -> list[dict]:
    """Every persisted connection of this project, with the review state it states."""
    return (
        supabase.table("connections")
        .select("review_status")
        .eq("project_id", project_id)
        .execute()
        .data
    ) or []


def evidence_rows_for_page(drawing_id: str, page_number: int) -> list[dict]:
    """Every persisted evidence row ALREADY attributed to one page of one drawing.

    Milestone J17. Both evidence tables carry the page a row was read from
    (`source_page`) and the drawing it belongs to (`source_drawing_id`), so
    "does this page already have evidence?" is answerable by exact identity —
    no name matching, no approximation. A retry asks this before it reads,
    because a page the record names as failed is a page that produced no
    evidence: rows here mean an earlier attempt persisted evidence and did not
    live to record it, and inserting again would duplicate it.

    Returns one `{"table", "id"}` dict per row rather than whole rows: the
    caller decides whether to retry, and never re-reads this evidence to make
    an engineering decision from it.
    """
    rows = []
    for table in ("steel_members", "connections"):
        for row in (
            supabase.table(table)
            .select("id")
            .eq("source_drawing_id", drawing_id)
            .eq("source_page", page_number)
            .execute()
            .data
        ) or []:
            rows.append({"table": table, "id": row["id"]})
    return rows


# =============================================================================
# Raw AI capture (Milestone J23) — the reading, not the interpretation.
# =============================================================================
# NOTE, and it is the one thing here that must never change: a capture is NOT
# evidence, so `page_extraction_captures` is deliberately absent from the table
# list in `evidence_rows_for_page` above. A parse-failed page legitimately HAS a
# capture, so counting one as evidence would make every recorded failure read as
# "evidence was already persisted" and would refuse every retry, permanently.

def page_extraction_captures_for_drawing(drawing_id: str) -> list[dict]:
    """Every raw AI reading ever recorded of this drawing, EVERY attempt included.

    All windows, all retries — a re-read is a new attempt and an earlier attempt is
    never overwritten, so this is the page's whole reading history and not just its
    current state. The rows are returned as stored: which of them stands for a page
    is decided by `app.engineering_data.page_extraction_capture.authoritative_captures`,
    never here, because this module returns facts and decides nothing.
    """
    return (
        supabase.table("page_extraction_captures")
        .select(",".join(CAPTURE_COLUMNS))
        .eq("drawing_id", drawing_id)
        .execute()
        .data
    ) or []
