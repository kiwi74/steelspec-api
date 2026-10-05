-- ======================================================================================
-- Milestone J61 — PROJECT DOCUMENT IDENTITY.
--
-- ONE TABLE, ONE COLUMN, AND NO CHANGE TO ANY EXISTING BEHAVIOUR.
--
-- WHAT THIS TABLE IS
--
-- One row per SOURCE DOCUMENT a project actually has. It is a fact about the FILE, and it is
-- the identity that every analysis lineage over that file hangs from:
--
--     project  = the real job
--     document = one source document belonging to that job      <- this table
--     drawing  = one analysis lineage over a document
--     run      = one invocation of an analysis
--     capture  = one model reading produced by an invocation
--
-- WHY IT HAD TO EXIST
--
-- Before this migration the only thing standing for "which document" was a `drawings` row,
-- and `drawings` is created afresh by EVERY whole-document run (see the drawing-set creation
-- immediately above the drawing creation in app/pipeline.py, and the comment on it that
-- says so in the code's own words). A repeated extraction of one document and the arrival of
-- a second document therefore produced the same shape of record, which is exactly the
-- condition continuation refuses with CONTINUATION_DRAWING_SET_UNRESOLVED. That refusal is
-- not repaired here; it is now answerable, by naming the document the work is about.
--
-- IDENTITY IS CONTENT
--
-- A document's identity is `(project_id, content_sha256)`: the SHA-256 of the file's own
-- bytes, computed by the ONE hashing rule in app/drawing_reading/pdf_annotation_extractor.py
-- and by nothing else. `file_name`, `storage_path`, `byte_size` and `page_count` are
-- ATTRIBUTES — they are recorded, and they are never what makes two documents the same or
-- different. A re-upload of the same bytes under a different name is the SAME document, and
-- the partial unique index on `(project_id, content_sha256)` below refuses to store it twice.
--
-- NULL IS NOT A WILDCARD
--
-- `content_sha256` is nullable, and NULL means IDENTITY NOT PROVEN. It does not mean
-- "matches anything". Every row this migration backfills carries NULL, and so does every
-- document created by a caller with no proven hash. Because NULLs are not compared by a
-- unique index, the index below is PARTIAL: it constrains rows whose identity IS proven and
-- leaves unproven rows unconstrained, which is the only honest reading of both.
--
-- ROLE IS ASSERTED, NEVER INFERRED
--
-- `role` defaults to UNKNOWN and is constrained to a fixed vocabulary. Nothing in this
-- schema, and nothing in the milestone that introduced it, derives a role from a filename, a
-- page count, a path, a drawing title or any model output. A role is a human-supplied fact
-- and arrives by explicit assertion or not at all. The vocabulary is closed so that an
-- unrecognised string fails here rather than becoming a silent category.
--
-- WHAT THIS MIGRATION IS NOT (the J61 scope boundary, stated in the schema itself)
--
--   * NOT document revisions. There is no `document_revisions` table, no revision tree,
--     no content-hash chain and no backfill of any hash. `revision_label` is a nullable
--     LABEL a human may state and is never derived or compared.
--   * NOT a document graph. `supersedes_document_id` is a single nullable self-reference,
--     and nothing in the codebase populates it in this milestone.
--   * NOT reconciliation, NOT transmittals, NOT a register. There is no cross-document
--     reconciliation table, no transmittal table, no conflict record and no field-level
--     provenance table. None of those exist anywhere in this schema and this file creates
--     none of them.
--   * NOT a rewrite of existing evidence. No column is added to any append-only evidence
--     table — not to one holding a member, a connection, a page reading or a recorded review
--     snapshot — and no row of any of them is touched. Each of those names belongs to the
--     migration that created it and this file borrows none of them, which is the same rule
--     the claim table's own migration states. A reading keeps naming the drawing it was taken
--     over; the document is reached from that drawing, which is the one edge this file adds.
--   * NOT a change to `drawings.discipline`. That column is left exactly as it is, with the
--     same values and the same default. Rewriting an existing column's meaning is a
--     different milestone's decision.
--
-- WHAT CHANGES OBSERVABLY
--
-- Exactly two things, and nothing else:
--   1. one `project_documents` row per existing analysis lineage, backfilled below;
--   2. `drawings.document_id` populated for those rows.
-- Every existing read, refusal, revision number, snapshot, capture, coverage accounting and
-- fabrication behaviour is unchanged by this file. Nothing here writes to a project row, a
-- drawing set, a drawing beyond that one new column, an analysis run, a page reading, a
-- member, a connection or any review table.
--
-- ======================================================================================
-- 1. The table.
-- ======================================================================================
begin;

create table if not exists public.project_documents (
    id uuid primary key default gen_random_uuid(),

    -- The job this document belongs to. A document is per project: the same bytes uploaded to
    -- two projects are two documents, because the pair is the identity and the project half of
    -- it is real.
    project_id uuid not null references public.projects(id) on delete restrict,

    -- Where the file is stored and what it is called. Attributes, recorded verbatim, never
    -- compared.
    storage_path text not null,
    file_name text not null,

    -- The file's own format, upper-case, and its size in bytes. Both nullable: a legacy row
    -- and a row created without the file to hand state the absence rather than a guess.
    source_format text,
    byte_size bigint,

    -- The document's own page count, once a reading has established it. NULL means the count
    -- is not known — never 0, which would be a claim.
    page_count integer,

    -- THE IDENTITY. The SHA-256 of this document's own bytes, lower-case hex, or NULL when
    -- identity is not proven. Nullable on purpose; see the header.
    content_sha256 text,

    -- Asserted, never inferred. See the header.
    role text not null default 'UNKNOWN',

    -- A label a human may state. Never derived, never compared, never required.
    revision_label text,

    -- A single self-reference for a supersession a human may state. Nothing populates it in
    -- this milestone, and a document is never rewritten by it.
    supersedes_document_id uuid references public.project_documents(id) on delete restrict,

    created_at timestamptz not null default now(),

    -- A path or a name that is not a non-empty string is not an identity, and storing one
    -- would let a caller believe a document was recorded when nothing was.
    constraint project_documents_storage_path_check
        check (length(storage_path) > 0),
    constraint project_documents_file_name_check
        check (length(file_name) > 0),

    -- A hash that is present but not a SHA-256 is worse than an absent one: it looks
    -- checkable and is not. NULL is allowed; any other shape is refused.
    constraint project_documents_sha_check
        check (content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$'),

    -- The closed vocabulary. An unrecognised role fails here rather than becoming a silent
    -- category. It is the same list app/engineering_data/repository.py exposes as
    -- DOCUMENT_ROLES, and the two are meant to be read together.
    constraint project_documents_role_check
        check (role in (
            'TRANSMITTAL', 'STRUCTURAL_GA', 'ASSEMBLY', 'FABRICATION', 'ISOMETRIC',
            'DETAIL', 'SCHEDULE', 'SPECIFICATION', 'ARCHITECTURAL', 'UNKNOWN'
        )),

    -- A count that is present is a positive whole number. Zero pages is not a document.
    constraint project_documents_page_count_check
        check (page_count is null or page_count > 0),

    -- A document is never its own predecessor, and nothing else is constrained about the
    -- self-reference: this file does not decide what a supersession means.
    constraint project_documents_supersedes_self_check
        check (supersedes_document_id is null or supersedes_document_id <> id)
);

-- --------------------------------------------------------------------------------------
-- 2. The identity index.
--
-- PARTIAL, and both halves of that word matter. `(project_id, content_sha256)` is the
-- identity rule, so two rows may not carry the same proven identity; and the index is
-- restricted to rows where `content_sha256 is not null`, so the unproven-identity rows this
-- migration backfills are not constrained by it. A unique index over nullable columns would
-- treat NULLs as distinct in any case, but stating the restriction makes the intent legible
-- rather than incidental.
--
-- A partial index is also the reason the create below is `if not exists`: reapplying this
-- file must not fail merely because the index already stands.
-- --------------------------------------------------------------------------------------
create unique index if not exists project_documents_content_identity_idx
    on public.project_documents (project_id, content_sha256)
    where content_sha256 is not null;

-- A project's documents are read together and in creation order.
create index if not exists project_documents_project_created_idx
    on public.project_documents (project_id, created_at);

-- --------------------------------------------------------------------------------------
-- 3. The one edge from an analysis lineage to its document.
--
-- NULLABLE, AND IT MUST STAY NULLABLE. A drawing created before this migration, a drawing
-- created by the DXF path (which has no document row at all), and a drawing whose source
-- could not be identified all name no document, and NULL is the honest statement of that.
-- Making this column NOT NULL would have this migration invent an identity for records whose
-- identity is exactly what is not known.
--
-- `on delete restrict` — a document that an analysis lineage hangs from is never removable
-- as a side effect, and no cascade can silently unlink or remove recorded evidence.
-- --------------------------------------------------------------------------------------
alter table public.drawings
    add column if not exists document_id uuid
        references public.project_documents(id) on delete restrict;

create index if not exists drawings_document_idx
    on public.drawings (document_id);

-- --------------------------------------------------------------------------------------
-- 4. The legacy backfill.
--
-- ONE ROW PER EXISTING ANALYSIS LINEAGE THAT NAMES NO DOCUMENT, and nothing else. This is
-- deliberately the weakest possible reading of the existing record: the pre-J61 drawing row
-- is the only thing that ever stood for a document, so each one becomes one document row.
-- It is NOT a claim that two such rows are the same document — the bytes were never hashed
-- and this file does not hash them. It is NOT a claim about a role: every backfilled row is
-- UNKNOWN. It is NOT a claim about a revision: no label is set.
--
-- WHAT THIS FILE DOES NOT DO, AND MUST NOT BE CHANGED TO DO:
--   * It does not download a source file, and it does not read storage at all.
--   * It does not compute, guess or import a content hash. Every backfilled `content_sha256`
--     is NULL — identity not proven — and there is no hash anywhere in this file.
--   * It does not read, rewrite or derive anything from a page reading, a member, a
--     connection, an analysis run or any review table.
--   * It does not assume a row count. The numbers below are read from the database as it
--     stands; see the assertion.
--
-- IDEMPOTENT. The insert selects only drawings whose `document_id` is still NULL, and it sets
-- that column in the same statement. A second application of this file selects nothing and
-- inserts nothing.
--
-- `created_at` is the drawing's own `uploaded_at` where it has one, so that the recorded
-- order of a project's documents remains the order those lineages were actually created in.
-- Where there is none, the row's creation time is the honest statement available.
-- --------------------------------------------------------------------------------------
-- 4a. One document per unlinked lineage, created and linked ONE LINEAGE AT A TIME.
--
--     A set-based `insert ... returning` joined back on the record's own attributes would
--     have to match a created row to the drawing it came from using those attributes, and
--     two lineages of one project can carry the same storage path, the same file name and
--     the same upload time — a project that was analysed twice is exactly that case. Any such
--     join can pair one document with the wrong lineage, or with two, while every count in
--     the assertion below still adds up. This loop has no such failure mode: the document is
--     created and the lineage is linked inside one iteration, so the correspondence is the
--     iteration itself rather than a predicate that could match more than one row.
--
--     Nothing is assumed about how many lineages there are. The target set is read from the
--     database as it stands, counted, and reported before a single row is created.
--
--     The loop selects only lineages whose `document_id` is still NULL, so a second
--     application of this file has nothing to iterate over and creates nothing.
do $$
declare
    lineage record;
    created_document uuid;
    target_count bigint;
    created_count bigint := 0;
    still_unlinked bigint;
    mismatched bigint;
    dangling bigint;
begin
    select count(*)
      into target_count
      from public.drawings d
      join public.drawing_sets s on s.id = d.drawing_set_id
     where d.document_id is null;

    raise notice
        'J61 backfill: % pre-existing analysis lineage(s) to give a document identity',
        target_count;

    for lineage in
        select d.id, d.storage_path, d.file_name, d.page_count, d.uploaded_at, s.project_id
          from public.drawings d
          join public.drawing_sets s on s.id = d.drawing_set_id
         where d.document_id is null
         order by d.id
    loop
        insert into public.project_documents (
            project_id,
            storage_path,
            file_name,
            source_format,
            byte_size,
            page_count,
            content_sha256,
            role,
            revision_label,
            supersedes_document_id,
            created_at
        ) values (
            lineage.project_id,
            lineage.storage_path,
            lineage.file_name,
            -- The format is the file's own extension, upper-cased, or NULL when the name
            -- states none. Read off the recorded name; inferred from nothing else.
            upper(nullif(substring(lineage.file_name from '\.([A-Za-z0-9]+)$'), '')),
            -- Size is NOT known for a legacy row and is not guessed: nothing was downloaded.
            null,
            -- The lineage's own page count where it has one; NULL where it does not, never 0.
            lineage.page_count,
            -- IDENTITY NOT PROVEN. Not computed, not guessed, not a placeholder.
            null,
            'UNKNOWN',
            null,
            null,
            -- The lineage's own upload time where it has one, so recorded order remains the
            -- order those lineages were actually created in.
            coalesce(lineage.uploaded_at, now())
        )
        returning id into created_document;

        update public.drawings
           set document_id = created_document
         where id = lineage.id;

        created_count := created_count + 1;
    end loop;

    -- 4b. The relationship this backfill claims, asserted rather than assumed: EXACTLY ONE
    --     document row was created for each lineage that had none, every one of those lineages
    --     now names a document, every named document exists, and every named document states
    --     the same project, path and name as the lineage that names it. The comparison is
    --     against the target set counted above, never against a number written into this file,
    --     so it holds for a database with no lineages at all and for one with many, and it is
    --     vacuous — and therefore correct — on a second application.
    if created_count <> target_count then
        raise exception
            'BACKFILL_FAILED_CARDINALITY: % document row(s) were created for % analysis '
            'lineage(s) that had none; the backfill is one document per lineage and a mismatch '
            'means it created a record it did not intend to',
            created_count, target_count
            using errcode = 'P0001';
    end if;

    select count(*)
      into still_unlinked
      from public.drawings d
      join public.drawing_sets s on s.id = d.drawing_set_id
     where d.document_id is null;
    if still_unlinked <> 0 then
        raise exception
            'BACKFILL_FAILED_UNLINKED: % analysis lineage(s) still name no document after the '
            'backfill; every lineage that had none is meant to have been given one',
            still_unlinked
            using errcode = 'P0001';
    end if;

    select count(*)
      into dangling
      from public.drawings d
      left join public.project_documents p on p.id = d.document_id
     where d.document_id is not null and p.id is null;
    if dangling <> 0 then
        raise exception
            'BACKFILL_FAILED_DANGLING: % analysis lineage(s) name a document that does not '
            'exist', dangling
            using errcode = 'P0001';
    end if;

    select count(*)
      into mismatched
      from public.drawings d
      join public.project_documents p on p.id = d.document_id
      join public.drawing_sets s on s.id = d.drawing_set_id
     where p.project_id <> s.project_id
        or p.storage_path <> d.storage_path
        or p.file_name <> d.file_name
        or p.role <> 'UNKNOWN'
        or p.content_sha256 is not null;
    if mismatched <> 0 then
        raise exception
            'BACKFILL_FAILED_MISMATCH: % analysis lineage(s) name a document that states a '
            'different project, path or name, a role this backfill never asserted, or an '
            'identity it never proved', mismatched
            using errcode = 'P0001';
    end if;

    raise notice
        'J61 backfill complete: % document(s) created for % analysis lineage(s)',
        created_count, target_count;
end;
$$;

-- ======================================================================================
-- 5. RLS and privileges.
--
-- Unlike the append-only evidence tables, this table is MUTABLE IN PART, and the reason is
-- narrow: `page_count` is established by the reading of the document rather than by its
-- creation (`update_document_page_count` in app/engineering_data/repository.py writes it
-- once the run has counted the pages). So UPDATE is granted, and it is granted only for the
-- server's own role.
--
-- DELETE and TRUNCATE are revoked. Nothing in the design removes a document, and a document
-- that evidence has been recorded against must not become removable by a later milestone that
-- mistakes tidying up for safety. `on delete restrict` on both foreign keys says the same
-- thing at the constraint level.
--
-- RLS is enabled with ZERO policies, so no browser role is admitted and this table is never
-- reached from a client. There is no policy naming it, which is precisely why it is not
-- reachable: the absence is the control, and it is deliberate.
-- ======================================================================================
alter table public.project_documents enable row level security;

revoke all on table public.project_documents from public, anon, authenticated;

grant select, insert, update on table public.project_documents to service_role;

revoke delete, truncate on table public.project_documents from service_role;

commit;

-- The REST surface caches the schema; without this it keeps describing the database as it was
-- and the new table and the new column on `drawings` are not readable through PostgREST.
notify pgrst, 'reload schema';
