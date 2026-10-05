-- ======================================================================================
-- J28 — the durable PDF annotation-evidence layer.
--
-- WHAT THIS TABLE IS
--
-- One row per ANNOTATION OCCURRENCE: a mark-shaped text run the PDF itself contains, at the
-- device coordinate its own text-showing operator placed it. It is the record of the
-- READING — what the drawing says and where it says it — preserved without interpretation.
--
-- The reading is produced by app/drawing_reading/pdf_annotation_extractor.py, which reads
-- the PDF's own content stream: text-showing operators, their text matrices and CTMs, and
-- the stroked vector geometry. No AI extraction result and no database row is consulted to
-- produce it, and the source PDF's own SHA-256 is the only hash involved.
--
-- WHAT THIS TABLE IS NOT
--
-- It is NOT a member table, and this is the entire point of the milestone.
--
--   * `mark_candidate` is a CANDIDATE, never a decision. The grammar the extractor applies
--     is a shape test on characters, and the real Arkles set proves what that admits:
--     `S207` as a candidate 14 times on one page, `X4`, `X1`-`X6`, `W01`, `SG8`, `D19`,
--     `SDM049`, `SET153`, `UB37`. Sheet codes, grid references and product codes are all
--     mark-shaped. Nothing in this schema can promote one.
--   * TWO ROWS ARE TWO ANNOTATIONS. They are not two members, and two rows with the same
--     `mark_candidate` are not one member drawn twice. There is no `member_id`, no
--     `placement_id`, no `same_member` and no `is_steel_member` column here, and there may
--     never be one: the J28 proof measured a real page where the same mark appears at
--     several coordinates, and nothing in the PDF says whether those are one member, three
--     members, or three views of one member.
--   * An overprint is NOT a duplicate row. Two byte-identical operators at one point are
--     ONE occurrence carrying `duplicate_operator_count = 1`. That count is the only place
--     the overprint survives, which is exactly why it is a column and not a discarded
--     detail: the extractor's own deduplication would otherwise leave no trace that the
--     drawing drew the same text twice.
--
-- Nothing here is derived from `steel_members`, `connections`, `review_items` or
-- `connections`' children, and nothing is normalised, merged or repaired on the way in.
-- No trigger is placed on those tables and this file reads none of them.
--
-- There is NO production writer. J28 does not wire this table into /extract,
-- /continue-extraction, /retry-extraction, the J23 capture, `steel_members` persistence,
-- the production review surface or the connection-review surface. It exists so that a
-- later milestone can let a human review genuine PDF evidence instead of a model's reading
-- of it.
--
-- WHY THE KEY IS (drawing_id, page_number, analysis_run_id, annotation_x, annotation_y,
-- extractor_version)
--
-- The occurrence identity is the ATTEMPT, the drawing, and the position on the page.
-- `page_number` alone is NOT a page identity: every whole-document run creates a NEW
-- `drawings` row (app/pipeline.py creates the drawing set and the drawing on each
-- invocation), so the same page number recurs across documents and only
-- (drawing_id, page_number) names a page.
--
-- `analysis_run_id` is the ATTEMPT dimension, and it is deliberately the identity J23
-- already uses rather than a second one invented here. `analysis_runs` records one row per
-- extraction invocation — one for the whole-document run, one per continuation window, one
-- per page retry, including the retries that parsed nothing — and
-- `page_extraction_captures` already keys a page reading by
-- (drawing_id, page_number, analysis_run_id). A page read twice is TWO READINGS, and without
-- this column the second one could not be recorded at all.
--
-- That matters because the extractor is DETERMINISTIC: COORDINATE_PRECISION = 2 and a fixed
-- rule set mean a re-read of one page under one version produces exactly the coordinates the
-- first read produced. On the columns alone, the second reading of a parse-failed page — the
-- single most ordinary thing the retry route does — would collide with the first on every
-- occurrence, and the whole insert would be refused. The attempt belongs in the key because
-- the same rules over the same bytes produce the same occurrence, and that is a fact about
-- the reading, not a reason to discard it.
--
-- The coordinate is stored as `numeric(9,2)` because the extractor already rounds to
-- COORDINATE_PRECISION = 2 decimal places and reports that value. Storing it at exactly
-- that precision means the stored number IS the evidence rather than a second rounding of
-- it, so occurrence identity never depends on a comparison tolerance and two readings of one
-- PDF produce identical keys. 0.01 pt is two orders of magnitude finer than any tolerance on
-- these drawings.
--
-- `extractor_version` completes the key because A RULE SET IS PART OF THE READING. The
-- extractor's thresholds — the 4.8 pt text-merge gap, the 0.75 pt same-line tolerance, the
-- 8 pt schedule median pitch — are heuristic, and a reading taken under different ones is a
-- different reading, not a correction of the old one. Carrying the version means a
-- re-extraction APPENDS and the earlier reading stays legible; it never rewrites it.
--
-- `analysis_run_id` is provenance, NOT chronology. Two runs are never ordered by their UUIDs
-- — an id is a name, not a time — and nothing in this schema or in `authoritative_occurrences`
-- compares them.
--
-- A consequence, stated plainly: two occurrences WITHIN ONE READING that round to the same
-- hundredth of a point still collide on this key and the whole insert is refused. That is
-- deliberate, and it is unchanged by the attempt dimension — the run is the same for both
-- rows. Merging them would silently destroy the one thing this layer guarantees — that a row
-- is an occurrence that was actually found, at a position the PDF actually gave — and
-- dropping one would be silently discarding evidence. On the real Arkles set there are zero
-- such collisions across the 653 occurrences the J28 reading reports on those 11 pages.
--
-- WHICH READING STANDS FOR A PAGE
--
-- A query, not a column. For each (drawing, page), the rows of the reading with the greatest
-- (extracted_at, extractor_version); every earlier reading stays recorded. The order is taken
-- from THIS row's own `extracted_at`, never from `drawings` or `analysis_runs`: both of those
-- are updated in place (`update_drawing` rewrites the page count, number, title and revision,
-- and `update_analysis_run` rewrites status and counters), and an append-only ledger must not
-- derive its ordering from a table that can be rewritten. `analysis_run_id` names WHICH
-- attempt produced a reading; it never decides which reading stands.
--
-- `extracted_at` defaulting to `now()` is what makes it a batch identity rather than a
-- per-row accident: `now()` is fixed for the duration of a transaction, and the writer
-- inserts one reading in a single statement.
--
-- json, NOT jsonb — the J21, J22 and J23 precedent. jsonb rewrites the stored value (it
-- sorts keys and drops duplicates). The payload is a structured JSON value stored as `json`.
--
-- PRIVILEGES
--
-- Append-only and read-only, for every role. No browser role is admitted: RLS is enabled
-- with zero policies, so no policy could admit one even if a grant existed.
-- ======================================================================================

begin;

-- --------------------------------------------------------------------------------------
-- 1. The occurrence.
-- --------------------------------------------------------------------------------------
create table if not exists public.pdf_annotation_occurrences (
    -- The document the page belongs to. An explicit relationship using the identities that
    -- already exist: no new owner column, no membership table, no second authorization
    -- system. `drawing_set_id` is deliberately NOT carried — it is one hop up through
    -- `drawings` and no reader of this table needs it.
    drawing_id          uuid        not null,
    project_id          uuid        not null,

    -- The extraction invocation this reading belongs to — the attempt. The same identity J23
    -- records its page readings under, not a second one: it is created once per invocation
    -- by app/pipeline.py and is what makes a re-read of a page a NEW READING rather than a
    -- collision. NULL is not permitted: an occurrence is never recorded without the attempt
    -- that found it, and a refused attempt has no run and therefore writes no row.
    analysis_run_id     uuid        not null,

    -- The source page, 1-based, exactly as the extractor was asked for it. Not a reading
    -- of the page's own title block; the page the operators were read from.
    page_number         integer     not null,

    -- The device coordinates of the mark token's first glyph origin, in the top-left
    -- convention the extractor reports. `annotation_y` is the top-left value, NOT the PDF's
    -- own bottom-left value — the two differ by the media box height and confusing them is
    -- the easiest mistake this schema can invite.
    annotation_x        numeric(9,2) not null,
    annotation_y        numeric(9,2) not null,

    -- The rule set the reading was taken under. Part of the key: see the header.
    extractor_version   text        not null,

    -- The token the grammar matched, or NULL when the run was designation-shaped but could
    -- not be tokenised (`CCAH3.2treated` on the real set). A CANDIDATE, never a decision.
    mark_candidate      text,

    -- False exactly for those untokenisable fragments. NOT NULL because "the run opened
    -- with something the grammar could not read" and "the field was not filled in" are
    -- different states and this column is the only thing that separates them.
    mark_readable       boolean     not null,

    -- How many DISTINCT text-showing operators the run was assembled from, and how many
    -- extra byte-identical copies were collapsed before assembly. Together they account for
    -- every raw operator: operator_count + duplicate_operator_count is what the PDF drew.
    operator_count          integer not null,
    duplicate_operator_count integer not null,

    -- The schedule-column classification: TRUE when the occurrence sits in a column of
    -- marks at a schedule-like median pitch, FALSE when the page has a schedule and this is
    -- not in one, NULL when the page has no schedule column and the question does not
    -- arise. It is a HEURISTIC classification, never a claim that a physical member exists.
    schedule_row_candidate  boolean,

    -- Whether a closed drawn rectangle was found around the mark, and whether a drawn chain
    -- of strokes terminates on it. `leader_present` is NULL when no box was found, because
    -- the leader question is only asked of a box. A leader endpoint is an ANNOTATION FACT:
    -- it is not a member end, and nothing may bind it to a grid, level or detail.
    tag_box_present     boolean     not null,
    leader_present      boolean,

    -- The bytes this reading was taken from, and the thresholds it was taken under.
    -- Provenance, not identity: two drawings of the same PDF are two drawings.
    source_pdf_sha256   text        not null,
    rule_set            json        not null,

    -- The occurrence verbatim as the extractor serialises it: text matrices, CTM, font,
    -- font size, the raw text run, tag box geometry, leader endpoint and chain, and the
    -- nearby text runs with their measured distances. The promoted columns above are copies
    -- of values in here, so a reader may select on them without reading json; the json is
    -- the complete record and the two are built together from one occurrence.
    evidence            json        not null,

    -- When this reading was recorded. The database's own default, never sent by the writer,
    -- and deliberately NOT part of the occurrence identity: two extractions of one PDF must
    -- be comparable byte for byte.
    extracted_at        timestamptz not null default now(),

    constraint pdf_annotation_occurrences_pkey
        primary key (drawing_id, page_number, analysis_run_id, annotation_x, annotation_y,
                     extractor_version),

    -- The attempt. `on delete restrict` for the same reason J23 uses it: an analysis run that
    -- produced occurrences is part of the record of them, and deleting the run would leave
    -- readings that nothing can be attributed to.
    constraint pdf_annotation_occurrences_run_fkey
        foreign key (analysis_run_id) references public.analysis_runs (id) on delete restrict,

    constraint pdf_annotation_occurrences_drawing_fkey
        foreign key (drawing_id) references public.drawings (id) on delete restrict,

    constraint pdf_annotation_occurrences_project_fkey
        foreign key (project_id) references public.projects (id) on delete restrict,

    constraint pdf_annotation_occurrences_page_number_check
        check (page_number >= 1),

    constraint pdf_annotation_occurrences_x_check
        check (annotation_x >= 0),

    constraint pdf_annotation_occurrences_y_check
        check (annotation_y >= 0),

    constraint pdf_annotation_occurrences_operator_count_check
        check (operator_count >= 1),

    constraint pdf_annotation_occurrences_duplicate_count_check
        check (duplicate_operator_count >= 0),

    -- An unreadable occurrence has no token, and this is the same invariant
    -- `occurrence_rows` refuses in Python, expressed where it cannot be bypassed. A row
    -- marked unreadable that nonetheless carries a candidate would make every reader that
    -- selects on `mark_candidate` believe the grammar had read something it had not.
    constraint pdf_annotation_occurrences_readable_check
        check (mark_readable or mark_candidate is null),

    constraint pdf_annotation_occurrences_version_check
        check (length(extractor_version) > 0),

    -- Provenance that is present but not a digest is worse than provenance that is absent:
    -- it looks checkable and is not.
    constraint pdf_annotation_occurrences_sha_check
        check (source_pdf_sha256 ~ '^[0-9a-f]{64}$')
);

-- --------------------------------------------------------------------------------------
-- 2. Append-only, for every role.
-- --------------------------------------------------------------------------------------
create or replace function public.pdf_annotation_occurrence_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception
        'CAPTURE_REFUSED_APPEND_ONLY: % on %.% is refused; a recorded PDF reading is never '
        'rewritten and never removed', tg_op, tg_table_schema, tg_table_name
        using errcode = 'P0001';
end;
$$;

create trigger pdf_annotation_occurrences_append_only
    before update or delete on public.pdf_annotation_occurrences
    for each row execute function public.pdf_annotation_occurrence_append_only();

-- --------------------------------------------------------------------------------------
-- 3. RLS, privileges.
--
-- The server's own role may append a reading and read one, and nothing else. UPDATE and
-- DELETE are revoked so that the append-only property does not rest on the trigger alone,
-- and TRUNCATE is revoked because a row-level trigger cannot see TRUNCATE at all — without
-- that line the table would have a hole the trigger could not close. RLS is enabled with
-- ZERO policies, so no browser role is admitted; this table is never written from a client.
-- (A superuser can still drop the table; that is not a claim this file makes. It claims
-- that no path through the application's own credentials can rewrite or remove a recorded
-- reading.)
-- --------------------------------------------------------------------------------------
alter table public.pdf_annotation_occurrences enable row level security;

revoke all on table public.pdf_annotation_occurrences from public, anon, authenticated;

grant insert, select on table public.pdf_annotation_occurrences to service_role;

revoke update, delete, truncate on table public.pdf_annotation_occurrences from service_role;

revoke all on function public.pdf_annotation_occurrence_append_only()
    from public, anon, authenticated;

commit;

-- The REST surface caches the schema; without this it keeps describing the database as it
-- was (the J8B, J22 and J23 migration headers record the same step for the same reason).
notify pgrst, 'reload schema';
