-- ======================================================================================
-- Milestone J66 — FIELD-LEVEL EVIDENCE CITATIONS.
--
-- WHAT THIS FILE IS
--
-- The storage half of J65's accepted design: one row per FIELD of one connection review
-- item saying WHERE that field's content came from. It is the third table in the review
-- chain J21 designed and J22 implemented --
--
--     connection_review_snapshots   one row per (project, revision)              J22
--     connection_review_items       one row per connection at that revision      J22
--     connection_review_item_citations  one row per FIELD of one such item       J66  <- here
--
-- -- and the first one keyed below the level of the connection.
--
-- IT IS NOT CROSS-DOCUMENT RECONCILIATION
--
-- Nothing here reconciles two documents, ranks two sources, picks a winner between them,
-- matches a field automatically, parses a transmittal, or enforces that a field is cited
-- at all. It records citations and reads them back. Every one of those other things is a
-- later milestone's decision, and this file must not be read as having taken any of them.
--
-- ======================================================================================
-- A CITATION IS AN ADDRESS, NOT A VALUE
-- ======================================================================================
--
-- There is deliberately no `value`, `chosen_value`, `confidence`, `rank`, `is_primary`,
-- `winner` or `resolved` column, and there is no `document_role` or `drawing_number`
-- column either. A citation says "this field's content is accounted for by THAT reading,
-- at THAT place in it"; the content itself stays in the immutable evidence it points at,
-- in exactly one copy. A second copy here would be a second truth that could disagree with
-- the first, and the disagreement would be undetectable because both would be stored.
--
-- `anchor` is NOT such a copy: it is the locator within the cited reading — the drawn
-- token, the detail reference, the nearby text — recorded so a human can find again what
-- the citation was taken from. It is an attribute OF THE ADDRESS.
--
-- `document_role` is absent for a second reason: it is J64's ASSERTION, it lives on the
-- document, and it is written by a human. Copying it here would freeze one reading of it
-- into an append-only row that can never be corrected.
--
-- ======================================================================================
-- WHY THE EVIDENCE-IDENTITY COLUMNS ARE NULLABLE, AND ALL-OR-NOTHING
-- ======================================================================================
--
-- A citation is always AT LEAST a page reading, so `drawing_id`, `page_number` and
-- `analysis_run_id` are NOT NULL and carry a foreign key to the reading itself. A citation
-- that came from an annotation occurrence adds the three coordinates that make that
-- occurrence an identity — `annotation_x`, `annotation_y`, `extractor_version` — and the
-- CHECK below requires all three or none: a citation cannot name a position without naming
-- the rule set it was read under, because the pair is what the occurrence table's own key
-- is. None of the three being set is the honest statement "this field is accounted for by
-- the page reading, and not by one particular occurrence on it".
--
-- A citation is never reducible to a page number alone: the page reading is identified by
-- (drawing, page, run), and the run is what makes a re-read a NEW READING rather than a
-- collision. `drawing_number` is deliberately not a column. It is a READING the model
-- returned ("001" on one page, "C1136 022X" on another in the same genuine set) and it is
-- already stored, verbatim, in the cited capture's payload; a reader that wants it obtains
-- it from the evidence the citation names. As a key it would be wrong, and as a copy it
-- would be a second, uncorrectable truth.
--
-- ======================================================================================
-- DOCUMENT IDENTITY: NULL IS NOT A WILDCARD
-- ======================================================================================
--
-- `document_id` is nullable and its NULL means "the reading this cites predates document
-- identity or was taken without it" — nothing more. It does not mean "any document", and
-- nothing may read it as a match. J61's identity rule is untouched by this file: no
-- content hash is computed, compared, backfilled or added to any table, and a NULL
-- `content_sha256` continues to match exactly nothing.
--
-- ======================================================================================
-- WHY THE FOREIGN KEYS, AND WHY A TRIGGER AS WELL
-- ======================================================================================
--
-- 1. `(project_id, review_revision, review_package_id)` -> `connection_review_items`, the
--    item's own primary key, `on delete restrict`. This is the composite item foreign key
--    J65 approved and this file may not omit. It is what makes "this citation belongs to
--    that reviewed connection" a database fact, and it is also the citation's OWNER: the
--    citation's review revision is the item's, not a second number that could drift.
--
-- 2. `(drawing_id, page_number, analysis_run_id)` -> `page_extraction_captures`, its own
--    primary key, `on delete restrict`. A citation to a page reading that does not exist
--    is not a citation; it fails here with 23503 and nothing is written.
--
-- 3. `(drawing_id, page_number, analysis_run_id, annotation_x, annotation_y,
--    extractor_version)` -> `pdf_annotation_occurrences`, its own primary key,
--    `on delete restrict`. When the three coordinates are NULL this constraint is
--    satisfied without inspecting anything — SQL's default MATCH SIMPLE treats a
--    constraint with any NULL column as satisfied — which is exactly the behaviour
--    wanted: the six-tuple is enforced when it is stated and is inert when it is not.
--
-- 4. `document_id` -> `project_documents`, `on delete restrict`. Nullable; see above.
--
-- WHY A TRIGGER IS STILL NEEDED, AND WHAT IT CLOSES. `drawings` does not carry
-- `project_id` — it reaches its project one hop up through `drawing_sets`. So foreign keys
-- 2 and 3 prove that the cited reading EXISTS, and foreign key 1 proves the item is this
-- project's, but nothing in the constraint set connects the two: an insert naming project
-- A's review item while citing project B's page reading passes every foreign key above.
-- That is the one hole, and `connection_review_item_citations_ownership` below closes it by
-- reading the cited row's own `project_id` and refusing when it is not the citation's.
-- A citation from project A to evidence belonging to project B is impossible to insert.
--
-- The guard is a trigger rather than a column for the reason just given: the fact it
-- enforces lives one hop away and cannot be made a constraint without adding a redundant
-- project-inclusive unique index to an existing evidence table, which J65 refused and this
-- file must not do. No existing evidence table is altered, indexed, or touched by this
-- migration in any way.
--
-- ======================================================================================
-- APPEND-ONLY, FOR EVERY ROLE, WITH NO MUTABLE EXCEPTION
-- ======================================================================================
--
-- A recorded citation is never rewritten and never removed. `ordinal` exists only to give
-- the rows of one field a RECORDING ORDER and therefore a stable primary key — it is not a
-- priority, not a rank and not a preference, and no reader may treat it as one. A later
-- reading of a field is a NEW ROW with the next ordinal, never an edit of an old one.
--
-- The trigger fires for every role including service_role, which bypasses RLS, so the
-- property is a database fact rather than a convention; and the privilege layer states the
-- same thing independently by revoking UPDATE, DELETE **and TRUNCATE** from service_role
-- (TRUNCATE because a row-level trigger cannot see it). There is no mutable-table exception
-- here: unlike `project_documents`, nothing about a citation is established after it is
-- recorded.
--
-- ======================================================================================
-- RLS AND GRANTS
-- ======================================================================================
--
-- RLS is enabled with ZERO policies: no browser role reads or writes this table, and the
-- only path in is the server-side service-role client, which re-enforces the project's
-- owner one hop away through the review item. The explicit revokes are the load-bearing
-- lines rather than the enable, because Supabase's default privileges grant new public
-- tables to anon and authenticated.
--
-- The writer function is SECURITY INVOKER (the default — `security definer` is deliberately
-- NOT used) and EXECUTE is revoked from public, anon and authenticated. It takes no owner,
-- role or user column: every authorization decision stays where the review boundary already
-- put it.
--
-- ======================================================================================
-- EXECUTION STATE
-- ======================================================================================
--
-- AUTHORED: as authored this file was NOT APPLIED. It was then applied ONCE.
--
-- APPLIED: 2026-09-29, live, by a single `db query --file` execution of this file against the
-- linked project — the same credentialed endpoint the J8B, J22 and J61 headers record. No
-- migration runner, CLI push or CI job executed it and no migration tracking table records it.
--
-- POST-APPLICATION STATE, read back read-only, not inferred:
--   * pg_class.relrowsecurity for the new table is true, and there is no policy on it;
--   * pg_trigger lists exactly two non-internal triggers, `..._ownership` and `..._append_only`;
--   * pg_constraint lists exactly ten: the primary key, the four foreign keys and the five
--     CHECKs, with the COMPOSITE ITEM FOREIGN KEY named `..._item_fkey` among them;
--   * information_schema.role_table_grants: anon and authenticated hold nothing; service_role
--     holds INSERT, SELECT, REFERENCES and TRIGGER — the same four it holds on J22's two
--     tables, because the REFERENCES and TRIGGER grants are Supabase's default privileges for
--     a new public table and neither J22 nor this file revokes them. UPDATE, DELETE and
--     TRUNCATE are revoked, which is the property that matters;
--   * has_function_privilege: only service_role can EXECUTE any of the three functions;
--   * the table holds ZERO rows, for every project. Nothing was backfilled, revision 0 is
--     untouched, and its evidence digest still reads
--     61f18266bdc200cabec400c1fbb51140b6ba5965f9c3730e5fa1111a73727cb3.
--
-- This file is NOT re-applied by any test or task. `create table if not exists` is
-- idempotent, but `create trigger` is not, so a blind second application would fail on the
-- triggers.
-- ======================================================================================

begin;

-- --------------------------------------------------------------------------------------
-- 1. The citation.
-- --------------------------------------------------------------------------------------
create table if not exists public.connection_review_item_citations (
    -- The composite item foreign key's three columns. Together they ARE the owner: the
    -- cited connection, at the cited revision, of the cited project.
    project_id          uuid    not null,
    review_revision     integer not null,
    review_package_id   text    not null,

    -- WHICH field of that item. The seven-value vocabulary below is the same list
    -- app/cad_engine/connection_review_package.py publishes as ENGINEERING_FIELDS; the two
    -- are meant to be read together and a change to either must fail a test that reads both.
    field_name          text    not null,

    -- RECORDING ORDER ONLY — never priority, never a rank, never a preference. 1-based, so
    -- that "the first citation recorded for this field" is a thing the record can say.
    ordinal             integer not null,

    -- How the field's content relates to the cited reading: SOURCE is the reading the
    -- content was taken from; DERIVATION is a reading the content was computed or
    -- interpreted from. It is a closed two-value vocabulary and carries no quality, no
    -- confidence and no precedence — the derived standing that J65 defined is computed by
    -- the read model and is deliberately NOT stored here.
    citation_kind       text    not null,

    -- NULL means the cited reading predates document identity or was taken without it.
    -- It does NOT mean "any document", and it matches nothing.
    document_id         uuid,

    -- The cited reading, identified exactly as the evidence tables identify it.
    drawing_id          uuid    not null,
    page_number         integer not null,
    analysis_run_id     uuid    not null,

    -- The cited annotation occurrence, when the citation names one. All three or none; see
    -- the header for why the tuple is all-or-nothing and why the run is part of it.
    annotation_x        numeric(9,2),
    annotation_y        numeric(9,2),
    extractor_version   text,

    -- The locator WITHIN the cited reading: the drawn token, the detail reference, the
    -- nearby text — what a human would look for to find the citation's subject again. It is
    -- an attribute of the address, never the field's value.
    anchor              json    not null,

    -- When this citation was recorded. The database's own default, never sent by the writer.
    -- Temporal provenance, read by nothing the contract exposes.
    recorded_at         timestamptz not null default now(),

    constraint connection_review_item_citations_pkey
        primary key (project_id, review_revision, review_package_id, field_name, ordinal),

    -- THE COMPOSITE ITEM FK. J65 approved it and this file may not omit it: it is the
    -- citation's owner and the reason a citation cannot exist for a connection that was
    -- never reviewed.
    constraint connection_review_item_citations_item_fkey
        foreign key (project_id, review_revision, review_package_id)
        references public.connection_review_items (project_id, review_revision, review_package_id)
        on delete restrict,

    -- The page reading. A citation always names one.
    constraint connection_review_item_citations_capture_fkey
        foreign key (drawing_id, page_number, analysis_run_id)
        references public.page_extraction_captures (drawing_id, page_number, analysis_run_id)
        on delete restrict,

    -- The occurrence, when one is named. Inert while the coordinates are NULL.
    constraint connection_review_item_citations_occurrence_fkey
        foreign key (drawing_id, page_number, analysis_run_id, annotation_x, annotation_y,
                     extractor_version)
        references public.pdf_annotation_occurrences (drawing_id, page_number, analysis_run_id,
                                                      annotation_x, annotation_y,
                                                      extractor_version)
        on delete restrict,

    constraint connection_review_item_citations_document_fkey
        foreign key (document_id) references public.project_documents (id) on delete restrict,

    -- ENGINEERING_FIELDS (app/cad_engine/connection_review_package.py:180), verbatim:
    -- REQUIRED_PROVENANCE_FIELDS plus material. An unrecognised field fails here rather than
    -- becoming a silent category.
    constraint connection_review_item_citations_field_name_check
        check (field_name in ('connected_member_marks', 'position', 'plate', 'holes',
                              'location', 'attachments', 'material')),

    constraint connection_review_item_citations_citation_kind_check
        check (citation_kind in ('SOURCE', 'DERIVATION')),

    -- Ordinals are 1-based; there is no zeroth citation of a field.
    constraint connection_review_item_citations_ordinal_check
        check (ordinal >= 1),

    -- Pages are 1-based, exactly as the evidence tables require.
    constraint connection_review_item_citations_page_number_check
        check (page_number >= 1),

    -- ALL THREE OR NONE. A position without its rule set, or a rule set without a position,
    -- would be an occurrence identity that names nothing.
    constraint connection_review_item_citations_occurrence_identity_check
        check ((annotation_x is null) = (annotation_y is null)
               and (annotation_x is null) = (extractor_version is null))
);

-- --------------------------------------------------------------------------------------
-- 2. The ownership guard: a citation cannot cross a project boundary.
--
--    Runs BEFORE INSERT, so it also pre-empts the foreign keys: a citation naming a reading
--    that does not exist at all is refused with CITATION_REFUSED_EVIDENCE_UNKNOWN rather
--    than reaching the constraint layer and returning a bare 23503. Both are refusals; the
--    first is the one that says what was wrong.
-- --------------------------------------------------------------------------------------
create or replace function public.connection_review_citation_ownership()
returns trigger
language plpgsql
as $$
declare
    v_capture_project  uuid;
    v_occurrence_project uuid;
    v_document_project uuid;
begin
    -- The cited page reading must belong to the SAME project as the citation. This is the
    -- check the foreign keys cannot make: `drawings` carries no project_id, so the two
    -- halves of the insert are otherwise unconnected.
    select project_id into v_capture_project
      from public.page_extraction_captures
     where drawing_id = new.drawing_id
       and page_number = new.page_number
       and analysis_run_id = new.analysis_run_id;

    if v_capture_project is null then
        raise exception
            'CITATION_REFUSED_EVIDENCE_UNKNOWN: citation of field % cites page reading '
            '(drawing %, page %, run %), which does not exist',
            new.field_name, new.drawing_id, new.page_number, new.analysis_run_id
            using errcode = 'P0001';
    end if;

    if v_capture_project <> new.project_id then
        raise exception
            'CITATION_REFUSED_FOREIGN_EVIDENCE: project % may not cite a page reading that '
            'belongs to project %', new.project_id, v_capture_project
            using errcode = 'P0001';
    end if;

    -- The occurrence, when one is named, must belong to the same project too.
    if new.extractor_version is not null then
        select project_id into v_occurrence_project
          from public.pdf_annotation_occurrences
         where drawing_id = new.drawing_id
           and page_number = new.page_number
           and analysis_run_id = new.analysis_run_id
           and annotation_x = new.annotation_x
           and annotation_y = new.annotation_y
           and extractor_version = new.extractor_version;

        if v_occurrence_project is null then
            raise exception
                'CITATION_REFUSED_EVIDENCE_UNKNOWN: citation of field % cites annotation '
                'occurrence (drawing %, page %, run %, x %, y %, extractor %), which does '
                'not exist',
                new.field_name, new.drawing_id, new.page_number, new.analysis_run_id,
                new.annotation_x, new.annotation_y, new.extractor_version
                using errcode = 'P0001';
        end if;

        if v_occurrence_project <> new.project_id then
            raise exception
                'CITATION_REFUSED_FOREIGN_EVIDENCE: project % may not cite an annotation '
                'occurrence that belongs to project %',
                new.project_id, v_occurrence_project
                using errcode = 'P0001';
        end if;
    end if;

    -- And the document, when one is named — the same rule applied to the same class of
    -- cited evidence. A nullable column is checked only when it is stated.
    if new.document_id is not null then
        select project_id into v_document_project
          from public.project_documents
         where id = new.document_id;

        if v_document_project is null then
            raise exception
                'CITATION_REFUSED_EVIDENCE_UNKNOWN: citation of field % cites document %, '
                'which does not exist', new.field_name, new.document_id
                using errcode = 'P0001';
        end if;

        if v_document_project <> new.project_id then
            raise exception
                'CITATION_REFUSED_FOREIGN_EVIDENCE: project % may not cite a document that '
                'belongs to project %', new.project_id, v_document_project
                using errcode = 'P0001';
        end if;
    end if;

    return new;
end;
$$;

create trigger connection_review_item_citations_ownership
    before insert on public.connection_review_item_citations
    for each row execute function public.connection_review_citation_ownership();

-- --------------------------------------------------------------------------------------
-- 3. Append-only, for every role. One trigger on both events, so no path exists that could
--    rewrite or remove a recorded citation.
-- --------------------------------------------------------------------------------------
create or replace function public.connection_review_citation_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception
        'CITATION_REFUSED_APPEND_ONLY: % on %.% is refused; a recorded citation is never '
        'rewritten and never removed', tg_op, tg_table_schema, tg_table_name
        using errcode = 'P0001';
end;
$$;

create trigger connection_review_item_citations_append_only
    before update or delete on public.connection_review_item_citations
    for each row execute function public.connection_review_citation_append_only();

-- --------------------------------------------------------------------------------------
-- 4. The one writer: citations for ONE reviewed connection, or nothing.
--
--    INSERT ONLY. The function contains no update, no delete, no upsert, no replace and no
--    overwrite, and it writes no review snapshot and no review item. It validates through
--    the constraints above and the ownership trigger rather than restating either in
--    Python: a refusal the database can make is a refusal that cannot be bypassed.
--
--    It performs NO revision check of its own. The citation's revision is the ITEM's
--    revision, and the composite foreign key is what enforces that — a revision the project
--    never recorded has no item to hang from and is refused.
-- --------------------------------------------------------------------------------------
create or replace function public.record_connection_review_citations(
    p_project_id        uuid,
    p_review_revision   integer,
    p_review_package_id text,
    p_citations         text
)
returns void
language plpgsql
as $$
declare
    v_citation json;
begin
    for v_citation in select * from json_array_elements(p_citations::json) loop
        insert into public.connection_review_item_citations
            (project_id, review_revision, review_package_id, field_name, ordinal,
             citation_kind, document_id, drawing_id, page_number, analysis_run_id,
             annotation_x, annotation_y, extractor_version, anchor)
        values
            (p_project_id,
             p_review_revision,
             p_review_package_id,
             v_citation->>'field_name',
             (v_citation->>'ordinal')::integer,
             v_citation->>'citation_kind',
             (v_citation->>'document_id')::uuid,
             (v_citation->>'drawing_id')::uuid,
             (v_citation->>'page_number')::integer,
             (v_citation->>'analysis_run_id')::uuid,
             (v_citation->>'annotation_x')::numeric,
             (v_citation->>'annotation_y')::numeric,
             v_citation->>'extractor_version',
             v_citation->'anchor');
    end loop;
end;
$$;

-- --------------------------------------------------------------------------------------
-- 5. RLS, privileges.
-- --------------------------------------------------------------------------------------
alter table public.connection_review_item_citations enable row level security;

revoke all on table public.connection_review_item_citations from public, anon, authenticated;

grant insert, select on table public.connection_review_item_citations to service_role;

-- The server's own role may add a citation and read one, and nothing else. TRUNCATE is
-- revoked because a ROW-level trigger cannot see it; without this line the table would have
-- a hole the trigger could not close.
revoke update, delete, truncate on table public.connection_review_item_citations from service_role;

revoke all on function public.connection_review_citation_ownership()
    from public, anon, authenticated;
revoke all on function public.connection_review_citation_append_only()
    from public, anon, authenticated;
revoke all on function public.record_connection_review_citations(uuid, integer, text, text)
    from public, anon, authenticated;

grant execute on function public.record_connection_review_citations(uuid, integer, text, text)
    to service_role;

commit;

-- The REST surface caches the schema; without this it keeps describing the database as it
-- was (the J8B, J22 and J61 migration headers record the same step for the same reason).
notify pgrst, 'reload schema';
