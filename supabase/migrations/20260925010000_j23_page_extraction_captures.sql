-- ======================================================================================
-- J23 — the durable raw AI-capture layer.
--
-- WHAT THIS TABLE IS
--
-- One row per (source page, extraction attempt): what the vision model actually returned
-- for that page, preserved verbatim. It is the record of the READING, not a second
-- interpretation of it.
--
-- The connection-review workflow's authoritative input is exactly this reading — the
-- per-page `raw_members` / `raw_connections` the model returned. Until this migration that
-- reading had no persisted source anywhere: `PageExtraction`
-- (app/ai_analysis/pdf_vision_analyzer.py:92) is a plain in-memory dataclass whose list
-- lives only in the frame of its caller, and on a parse failure line 208 of that module
-- `continue`s past the page having discarded the model's response entirely. The review read
-- model records the consequence in its own prose, in the module that reads a project for
-- the review surface: the review contract projects an in-process capture that no table
-- stores.
--
-- WHAT THIS TABLE IS NOT
--
-- It is NOT the interpreted evidence. `connections`, `steel_members`, `review_items` and
-- their children remain the downstream projections, and they are NOT a source for this
-- table in either direction — nothing here is derived from them, and nothing here is
-- normalised, merged, deduplicated, reinterpreted or repaired on the way in. The whole
-- point is to preserve what those tables lose.
--
-- Nothing in this file reads or writes those tables, and no trigger is placed on them.
-- The one place a capture must never be added is the per-page evidence reader
-- (`repository.evidence_rows_for_page`): a parse-failed page legitimately HAS a capture, so
-- counting one as evidence would make every recorded failure look like evidence already
-- persisted and would refuse every retry permanently.
--
-- WHY THE KEY IS (drawing_id, page_number, analysis_run_id)
--
-- `analysis_runs` already records exactly one row per extraction invocation — one for the
-- whole-document run, one per continuation window, one per page retry. So the pair
-- (drawing, page, run) is the ATTEMPT identity, and re-reading a page needs no supersede
-- column and no second protocol: a retry is a new run, therefore a new row, and the earlier
-- attempt is never touched. The history of a page's readings is preserved by construction.
--
-- `drawing_id` leads the key because `page_number` alone is NOT a page identity. Every
-- whole-document run creates a NEW `drawings` row (app/pipeline.py creates the drawing set
-- and the drawing on each invocation), so the same page number recurs across documents and
-- only (drawing_id, page_number) names a page. Leading with `drawing_id` also makes the
-- primary key the replay query's own access path — "every capture of this drawing, in page
-- order" — which is why NO further index is added: this is the only query the table has,
-- and it is already indexed by the key that has to exist anyway.
--
-- A partial unique index on (drawing_id, page_number) where not parse_failed was
-- considered and deliberately NOT added. It would turn "no duplicate successful capture"
-- into a schema fact, but it would also hard-refuse a legitimate re-read of a page that
-- already succeeded — and re-reading is exactly what this layer must be able to record.
-- Duplicate-resistance belongs in the selection rule below, in the reader, not here.
--
-- WHICH READING STANDS FOR A PAGE
--
-- A query, not a column: the latest attempt that parsed, ordered by
-- (captured_at, analysis_run_id); only when no attempt of a page has ever parsed does the
-- latest attempt stand, and it is the parse-failed one. A permanently unparseable page
-- therefore stays explicitly represented and is never silently an empty success.
--
-- The order is taken from THIS row's own `captured_at`, never from the parent run.
-- `analysis_runs` is mutable (its `model_used`, `status` and `completed_at` are all
-- updated in place) and `completed_at` is NULL for any run whose update never ran, which
-- includes the run of a failed continuation. An append-only ledger must not derive its
-- ordering from a table that can be rewritten, so `captured_at` is a column here, filled by
-- the database's own default and never sent by the writer. Two captures for one page cannot
-- share a transaction (the key forbids it), so `captured_at` is distinct per attempt and
-- (captured_at, analysis_run_id) is a total order.
--
-- `model` is copied onto this row rather than joined from `analysis_runs.model_used` for
-- the same reason in reverse: the run's value can be rewritten, this row's cannot, so the
-- copy is the provenance that cannot be edited through the back door. The writer takes it
-- from the same constant the run was created with at that call site, never from
-- configuration read at display time — re-deriving it later would report today's configured
-- model for an old reading.
--
-- json, NOT jsonb — the J21 and J22 precedent. jsonb rewrites the stored value (it sorts
-- keys and drops duplicates), and this column exists precisely to hold what the model
-- returned. The payload crosses as a structured JSON value and is stored as `json`.
-- ======================================================================================

begin;

-- --------------------------------------------------------------------------------------
-- 1. The capture.
-- --------------------------------------------------------------------------------------
create table if not exists public.page_extraction_captures (
    -- The document the reading belongs to. An explicit relationship down to the page, using
    -- the identities that already exist: no new owner column, no membership table, and no
    -- second authorization system. Ownership remains the project's own owner, decided one
    -- hop away through these same rows, exactly as it is everywhere else.
    drawing_id      uuid        not null,
    drawing_set_id  uuid        not null,
    project_id      uuid        not null,

    -- The source page, 1-based, exactly as the model was told it ("This is page N of the
    -- drawing set", app/ai_analysis/pdf_vision_analyzer.py:196). Not the model's opinion.
    page_number     integer     not null,

    -- Which extraction run produced this reading: the whole-document run, a continuation
    -- window, or a page retry.
    analysis_run_id uuid        not null,

    -- The model that produced this reading, copied from the constant the run was created
    -- with at the same call site.
    model           text        not null,

    -- Distinguishes a page whose response could not be parsed from a page that parsed
    -- cleanly and genuinely contained nothing. The two are different readings and this
    -- column is the only thing that separates them, so it is NOT NULL.
    parse_failed    boolean     not null,

    -- The returned structure, verbatim: exactly `dataclasses.asdict(PageExtraction)` —
    -- page_number, drawing_number, drawing_title, revision, raw_members, raw_connections,
    -- parse_failed. The genuine captures under tests/data/ are already this shape, so a real
    -- payload round-trips with no transformation. `drawing_number` and `drawing_title` are
    -- not decoration: they are the model's own words for the sheet and for the document, and
    -- the pipeline already persists them onto the drawing (`app/pipeline.py` reads them off
    -- page 1 and writes them to `drawings.drawing_number` / `drawings.drawing_title`). They
    -- are stored here as returned, inconsistency included: in the genuine Selby capture
    -- `drawing_number` differs on every one of the 32 pages — "001" … "032", "006x", "007X",
    -- "C1136 022X" — and `drawing_title` is the model's own long form on one page and its
    -- short form on the other 31. That inconsistency is the reading, not a defect to be
    -- normalised away on the way in; this table's whole purpose is to hold it.
    payload         json        not null,

    -- When this attempt was recorded. The database's own default, never sent by the writer.
    captured_at     timestamptz not null default now(),

    constraint page_extraction_captures_pkey
        primary key (drawing_id, page_number, analysis_run_id),

    constraint page_extraction_captures_drawing_fkey
        foreign key (drawing_id) references public.drawings (id) on delete restrict,

    constraint page_extraction_captures_drawing_set_fkey
        foreign key (drawing_set_id) references public.drawing_sets (id) on delete restrict,

    constraint page_extraction_captures_project_fkey
        foreign key (project_id) references public.projects (id) on delete restrict,

    constraint page_extraction_captures_run_fkey
        foreign key (analysis_run_id) references public.analysis_runs (id) on delete restrict,

    -- Pages are 1-based; the model is never asked to read a page 0.
    constraint page_extraction_captures_page_number_check
        check (page_number >= 1)
);

-- --------------------------------------------------------------------------------------
-- 2. Append-only, for every role.
-- --------------------------------------------------------------------------------------
create or replace function public.page_extraction_capture_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception
        'CAPTURE_REFUSED_APPEND_ONLY: % on %.% is refused; a recorded AI reading is never '
        'rewritten and never removed', tg_op, tg_table_schema, tg_table_name
        using errcode = 'P0001';
end;
$$;

create trigger page_extraction_captures_append_only
    before update or delete on public.page_extraction_captures
    for each row execute function public.page_extraction_capture_append_only();

-- --------------------------------------------------------------------------------------
-- 3. RLS, privileges.
--
-- The server's own role may append a reading and read one, and nothing else. UPDATE and
-- DELETE are revoked so that the append-only property does not rest on the trigger alone,
-- and TRUNCATE is revoked because a row-level trigger cannot see TRUNCATE at all — without
-- that line the table would have a hole the trigger could not close. RLS is enabled with
-- zero policies, so no policy admits a browser role. (A superuser can still drop the table;
-- that is not a claim this file makes. It claims that no path through the application's own
-- credentials can rewrite or remove a recorded reading.)
-- --------------------------------------------------------------------------------------
alter table public.page_extraction_captures enable row level security;

revoke all on table public.page_extraction_captures from public, anon, authenticated;

grant insert, select on table public.page_extraction_captures to service_role;

revoke update, delete, truncate on table public.page_extraction_captures from service_role;

revoke all on function public.page_extraction_capture_append_only()
    from public, anon, authenticated;

commit;

-- The REST surface caches the schema; without this it keeps describing the database as it
-- was (the J8B and J22 migration headers record the same step for the same reason).
notify pgrst, 'reload schema';
