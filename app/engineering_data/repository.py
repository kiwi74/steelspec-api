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
from app.engineering_data.pdf_annotation_evidence import ANNOTATION_COLUMNS, ANNOTATION_TABLE
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


def create_drawing(drawing_set_id: str, file_name: str, storage_path: str,
                   document_id: str | None = None) -> dict:
    """One analysis lineage over a source document.

    `document_id` (Milestone J61) is the durable document this lineage read. It defaults to
    `None` so that a caller which has no document identity — a legacy path, or a double
    standing in for this function — keeps writing exactly the row it wrote before. The
    column is nullable for the same reason and stays nullable.

    `discipline` is written here and is deliberately NOT migrated or reinterpreted by J61:
    it is a separate, later decision (J60 section 4), and nothing in this module reads it.
    """
    return supabase.table("drawings").insert({
        "drawing_set_id": drawing_set_id,
        "file_name": file_name,
        "storage_path": storage_path,
        "discipline": "structural",  # assumed for this milestone; multi-file discipline detection is later
        "document_id": document_id,
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


def insert_pdf_annotation_occurrences(rows: list[dict]) -> int:
    """Records one PDF reading's annotation occurrences, in a SINGLE insert. Returns how many.

    WIRED, by Milestone J36, at exactly one place: `app.pipeline._record_annotation_evidence`,
    which every extraction path calls immediately after `_record_page_captures` and before
    that run's first engineering write. J28 built this transport and bound it to nothing; J36
    binds it, and the statement written stays the statement it was.

    What J36 did NOT change is what a failure here means. The caller wraps this call — and
    only this call — so a refusal from the J28 layers, or a store that is not present at all,
    leaves the run exactly as it would have been: the capture is already recorded, the
    engineering rows are written, the coverage record advances, and the page's retryability
    is what it was. Nothing reads this table back to make an engineering decision.

    One statement for the whole reading, so a reading is recorded together or not at all:
    PostgREST executes a single insert in one transaction, and a half-recorded reading would
    be indistinguishable from a reading that genuinely found fewer occurrences.

    There is no per-row loop and no update, delete or upsert anywhere in this function — the
    table is append-only and this is its only writer. A re-extraction under a new rule set is
    a new `extractor_version` and therefore new rows; a RE-READ of a page is a new
    `analysis_run_id` and therefore new rows too, which is what a retry or a repeated
    continuation window produces. The two are different facts and the key records both.

    Within one reading, two occurrences of a page that round to the same hundredth of a point
    collide on (drawing, page, run, coordinate, version) and refuse the whole insert rather
    than one of them being kept: silently keeping one would be silently dropping evidence.
    That is unchanged by the attempt dimension — the run is the same for both rows.
    """
    if not rows:
        return 0
    supabase.table("pdf_annotation_occurrences").insert(rows).execute()
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
# Project documents (Milestone J61) — the durable identity of one source document.
# =============================================================================
# J60 established the distinction this section exists to make storable:
#
#     project  = the real job
#     document = one source document belonging to that job
#     drawing  = one analysis lineage over a document
#
# Before J61 the only thing standing for "which document" was a `drawings` row, which is
# created afresh by EVERY whole-document run (`create_drawing` above). That made a repeated
# extraction of one document indistinguishable from a second document — the condition
# `plan_continuation` refuses with CONTINUATION_DRAWING_SET_UNRESOLVED. A document row is a
# fact about the FILE; a drawing row is a fact about an ATTEMPT at reading it.
#
# IDENTITY IS CONTENT. A document's identity is `(project_id, content_sha256)` — the
# SHA-256 of its own bytes, computed by the one hashing rule the drawing-reading extractor
# exposes as `source_document_sha256`, and called from the pipeline. This module never
# imports that rule: it is handed a hash or it is handed nothing. The filename, the
# storage path and the page count are ATTRIBUTES: they are recorded, and they are never
# what makes two documents the same or different.
#
# NULL IS NOT A WILDCARD. A document whose bytes were never hashed — every document that
# existed before J61, and every DXF project, which has no document row at all — carries
# `content_sha256 = NULL`, which means IDENTITY NOT PROVEN. No caller may read it as
# "matches anything", and nothing in this module returns one document for another on the
# strength of it.
#
# ROLE IS ASSERTED, NEVER INFERRED. Nothing here derives a role from a filename, a page
# count, a drawing title, a path or any model output, and there is no code in this
# milestone that assigns one: every document created by J61 is UNKNOWN, and a role is a
# later, human-supplied fact.

#: The document roles J60's design identifies. A vocabulary, not a decision: J61 stores
#: UNKNOWN for every document it creates, and nothing in this codebase chooses one.
DOCUMENT_ROLES = (
    "TRANSMITTAL",
    "STRUCTURAL_GA",
    "ASSEMBLY",
    "FABRICATION",
    "ISOMETRIC",
    "DETAIL",
    "SCHEDULE",
    "SPECIFICATION",
    "ARCHITECTURAL",
    "UNKNOWN",
)

#: What a document's role is until a human says otherwise.
DOCUMENT_ROLE_UNKNOWN = "UNKNOWN"

#: The columns a document is read back as. Explicit rather than "*", like every other read
#: in this module: a caller gets exactly the persisted facts and cannot quietly acquire a
#: decision.
DOCUMENT_COLUMNS = (
    "id,project_id,storage_path,file_name,source_format,byte_size,page_count,"
    "content_sha256,role,revision_label,supersedes_document_id,created_at"
)


def _document_for_content(project_id: str, content_sha256: str) -> dict | None:
    """The document of this project with exactly these bytes, or None.

    The `(project_id, content_sha256)` lookup is the identity rule itself, so it lives in
    one place. It is only ever asked a NON-NULL hash: a NULL hash proves nothing about
    sameness and must never select a row.
    """
    rows = (
        supabase.table("project_documents")
        .select(DOCUMENT_COLUMNS)
        .eq("project_id", project_id)
        .eq("content_sha256", content_sha256)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def create_project_document(project_id: str, *, storage_path: str, file_name: str,
                            source_format: str | None = None, byte_size: int | None = None,
                            page_count: int | None = None, content_sha256: str | None = None,
                            role: str = DOCUMENT_ROLE_UNKNOWN,
                            revision_label: str | None = None,
                            supersedes_document_id: str | None = None) -> dict:
    """The project's document identity for these bytes — created once, never duplicated.

    IDEMPOTENT BY CONTENT. When `content_sha256` is given and a document of this project
    already carries it, that row is returned and nothing is inserted: re-reading the same
    file is the same document, however many times it is read. That is the property that
    makes a repeated extraction a repeat rather than a second document. The database holds
    the same rule as a partial unique index, so two runs racing to insert one document
    cannot both succeed; the loser re-reads and returns the winner's row.

    A document is NEVER created from a path alone when a hash is available, and NOTHING here
    hashes anything: the caller supplies `content_sha256`, because the caller is the only
    place the document's bytes are in hand. A caller with no proven hash passes `None` and
    gets a document whose identity is explicitly unproven.

    `page_count` is the document's own count when the caller already knows it, and `None`
    otherwise — never a guess, and never 0 to stand in for one.
    """
    if content_sha256 is not None:
        existing = _document_for_content(project_id, content_sha256)
        if existing is not None:
            return existing

    row = {
        "project_id": project_id,
        "storage_path": storage_path,
        "file_name": file_name,
        "source_format": source_format,
        "byte_size": byte_size,
        "page_count": page_count,
        "content_sha256": content_sha256,
        "role": role,
        "revision_label": revision_label,
        "supersedes_document_id": supersedes_document_id,
    }
    try:
        return supabase.table("project_documents").insert(row).execute().data[0]
    except Exception:
        # The unique index on (project_id, content_sha256) refused a concurrent identical
        # insert. The identity is the pair, so the row that won IS this document.
        if content_sha256 is not None:
            existing = _document_for_content(project_id, content_sha256)
            if existing is not None:
                return existing
        raise


def project_documents_for_project(project_id: str) -> list[dict]:
    """Every source document this project has, as its own rows state them.

    Ordered by creation, which is the order they were first read in — an ordering of
    RECORDED FACT and not a ranking: nothing here says the first is authoritative, and no
    caller may read it as a preference. A project with more than one document is not
    ambiguous to this function; it is ambiguous to a caller that has not said which one it
    means.
    """
    return (
        supabase.table("project_documents")
        .select(DOCUMENT_COLUMNS)
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
        .data
    ) or []


def document_for_drawing(drawing_id: str) -> dict | None:
    """The document one analysis lineage read, or None when it names none.

    The hop J60 identified as already sufficient for document provenance: `drawings
    .document_id` is the only edge that had to be added, and every evidence row reaches its
    document through the drawing it already names (`source_drawing_id`). `None` is the
    honest answer for a drawing created before J61 or by the DXF path, and it is the absence
    of a document rather than a blank one.
    """
    drawings = (
        supabase.table("drawings")
        .select("document_id")
        .eq("id", drawing_id)
        .execute()
        .data
    ) or []
    if not drawings:
        return None
    document_id = drawings[0].get("document_id")
    if not document_id:
        return None
    rows = (
        supabase.table("project_documents")
        .select(DOCUMENT_COLUMNS)
        .eq("id", document_id)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def update_document_role(project_id: str, document_id: str, role: str) -> dict | None:
    """Records the role a human ASSERTED for one of THIS PROJECT's documents.

    Milestone J64, and the only write a role has ever had here: one column, set to the
    value the caller asserted. It is scoped by the PAIR — the id and the project the
    document belongs to — because a document is only ever reachable through its project,
    and a caller that named the wrong project updates nothing rather than rewriting
    another project's document. The database's own CHECK is the last barrier behind the
    vocabulary check the boundary performs, and it stays exactly as J61 declared it.

    Nothing else is written. `storage_path`, `file_name`, `content_sha256`, `page_count`,
    `revision_label` and `supersedes_document_id` are untouched, no row is inserted, none
    is deleted and no revision of any kind is created: a role is a fact about the
    document, not a new version of it.

    Returns the row the store itself reports after the write, or `None` when no document
    of this project carries that id — so the caller is told what the database did rather
    than what it was asked to do.
    """
    rows = (
        supabase.table("project_documents")
        .update({"role": role})
        .eq("id", document_id)
        .eq("project_id", project_id)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def update_document_page_count(document_id: str, page_count: int | None) -> None:
    """Records the document's own page count once a reading has established it.

    Only the count, and only ever the count the run actually established: the column is
    nullable and `None` is stored as the absence it is, exactly as `update_drawing_meta`
    treats the same fact on the drawing. No other field of a document is rewritten — an
    identity is not a thing to be updated.
    """
    supabase.table("project_documents").update({"page_count": page_count}).eq(
        "id", document_id
    ).execute()


def drawing_by_id(drawing_id: str) -> dict | None:
    """One drawing's own row, or None. A read, and only a read.

    Milestone L19. The lineage a document selects is a DRAWING, so selecting one has to be
    able to look one up by the id a caller stated. Nothing is inferred from what comes
    back: the caller compares the row's own `document_id` and its set's `project_id`
    against the address it was given, and refuses rather than adjusting.
    """
    rows = (
        supabase.table("drawings")
        .select("id,drawing_set_id,document_id,file_name,page_count,drawing_number,"
                "drawing_title,revision")
        .eq("id", drawing_id)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def selected_drawing_id_for_document(document_id: str) -> str | None:
    """The drawing this document has been told to be reviewed from, or None.

    Milestone L19. `None` is the ordinary state and means exactly one thing: no lineage has
    been selected, so the document behaves as it did before this column existed. It is NOT
    an invitation to choose — a reader that infers a lineage from it has misread it, and
    the reconstruction is the reader that must not.
    """
    rows = (
        supabase.table("project_documents")
        .select("selected_drawing_id")
        .eq("id", document_id)
        .execute()
        .data
    ) or []
    return rows[0].get("selected_drawing_id") if rows else None


def set_document_selected_drawing(document_id: str, drawing_id: str) -> dict | None:
    """Records the ONE lineage this document is reviewed from. One column, one row.

    Milestone L19, and the only write a selection has ever had here. It is scoped by the
    document's own id, so a caller that named the wrong document updates nothing rather
    than rewriting another document's selection. The database's own guard — added by this
    milestone's migration — is the last barrier behind the ownership checks the boundary
    performs, and it stays exactly as declared: a document may name a drawing of ITSELF, in
    its OWN project, and nothing else.

    Nothing else is written. No drawing, set, run or reading row is touched, no revision is
    recorded, no review is opened, no claim is taken and no artifact is produced: a
    selection is a fact about which reading counts, not a new reading.

    Returns the row the store itself reports after the write, or `None` when no document
    carries that id — so the caller is told what the database did rather than what it was
    asked to do.
    """
    rows = (
        supabase.table("project_documents")
        .update({"selected_drawing_id": drawing_id})
        .eq("id", document_id)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


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


def analysis_runs_for_drawing_set(drawing_set_id: str) -> list[dict]:
    """Every analysis run recorded against one drawing set, oldest first.

    A run's `status` is the run's own outcome — the pipeline writes `"completed"` when a
    whole-document read finished and `"failed"` when it did not — so this is the per-run
    state a project-level aggregation needs, and the only place it is recorded.

    E2E-001N. Read-only: nothing here decides what a run's status MEANS, and a caller that
    reads an empty list has learned that no run was recorded, not that one succeeded.

    `started_at` is the run's own creation timestamp and the table's only one. This function
    named `created_at` until E2E-002D, which `analysis_runs` has never had: PostgREST refused
    the select with `42703`, and because that refusal is classified as a database error the
    first genuine E2E extraction reported itself as failing for a reason SteelSpec could not
    classify. The column names here are asserted against the table's real shape by
    `tests/test_real_world_e2e002d_analysis_run_columns.py`, which is the check whose absence
    let an unverified name ship.
    """
    return (
        supabase.table("analysis_runs")
        .select("id,drawing_set_id,status,started_at")
        .eq("drawing_set_id", drawing_set_id)
        .order("started_at")
        .execute()
        .data
    ) or []


def document_extraction_states(project_id: str) -> list[dict]:
    """Each of a project's documents beside the state of the runs that read it.

    E2E-001N. It composes reads that already existed rather than introducing a second
    account of anything: the documents come from `project_documents_for_project`, the
    lineage from `drawing_sets_for_project` → `drawings_for_drawing_set`, and the run
    outcome from `analysis_runs_for_drawing_set`.

    Each row is `{"document_id", "file_name", "run_status"}`, where `run_status` is
    `"completed"`, `"failed"`, `"running"` (a run exists but has reached neither terminal
    state), or `None` when NO run has been recorded for that document at all. `None` and
    `"running"` are different facts and are deliberately not merged: one says nothing has
    read this document yet, the other says something is reading it.

    A document read by more than one run reports the LATEST run's status, because a
    re-extraction is a later attempt at the same document and the newest attempt is the
    one that describes it. Nothing here ranks documents against each other.
    """
    run_status_by_document: dict[str, str | None] = {}
    for drawing_set in drawing_sets_for_project(project_id):
        statuses = [
            run.get("status") for run in analysis_runs_for_drawing_set(drawing_set["id"])
        ]
        latest = statuses[-1] if statuses else None
        if latest in ("completed", "failed") or latest is None:
            state = latest
        else:
            state = "running"
        for drawing in drawings_for_drawing_set(drawing_set["id"]):
            document_id = drawing.get("document_id")
            if document_id:
                run_status_by_document[document_id] = state

    return [
        {
            "document_id": document["id"],
            "file_name": document.get("file_name"),
            "run_status": run_status_by_document.get(document["id"]),
        }
        for document in project_documents_for_project(project_id)
    ]


def drawings_for_drawing_set(drawing_set_id: str) -> list[dict]:
    """The drawings of one drawing set, with the source file each was read from.

    `document_id` (Milestone J61) is read beside `storage_path` and `page_count` because it
    is the same kind of fact: a property of the row as stored. It is NULL for a drawing
    created before J61 and for a drawing that named no document, and a caller must read that
    NULL as "no document is named here" rather than as any particular document.
    """
    return (
        supabase.table("drawings")
        .select("id,drawing_set_id,file_name,storage_path,page_count,document_id")
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


# =============================================================================
# Reads (Milestone J29) — the recorded PDF annotation evidence, read back.
# =============================================================================
# J28 built the annotation evidence layer; J36 later bound its write transport to the
# extraction pipeline, so this table is now written by runs. This is the read half of the
# same boundary: one statement, one project, the columns the storage rules declare and no
# others. Nothing here decides anything — in particular it does not decide which reading
# stands for a page. That rule is `authoritative_occurrences` in the storage layer, and a
# caller that skipped it would be reading superseded readings as though they were current.

def pdf_annotation_occurrences_for_project(project_id: str) -> list[dict]:
    """Every recorded PDF annotation occurrence of this project, every reading included.

    One project, all of its drawings, all pages, all attempts — the rows the evidence
    layer recorded, returned exactly as stored. Which of them stand for a page is not
    decided here: `app.engineering_data.pdf_annotation_evidence.authoritative_occurrences`
    is the only thing that decides it, and it is the only thing that should.

    An occurrence is a mark drawn on a page. This read returns no member, no placement
    and no identity of any kind, because the table holds none.
    """
    return (
        supabase.table(ANNOTATION_TABLE)
        .select(",".join(ANNOTATION_COLUMNS))
        .eq("project_id", project_id)
        .execute()
        .data
    ) or []
