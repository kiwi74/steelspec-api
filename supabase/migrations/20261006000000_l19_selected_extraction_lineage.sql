-- ======================================================================================
-- Milestone L19 — EXPLICIT SELECTION OF ONE EXTRACTION LINEAGE FOR A DOCUMENT.
--
-- WHAT THIS FILE IS
--
-- One nullable column on `project_documents` naming the ONE drawing whose persisted
-- reading is this document's authoritative extraction, plus a guard that stops the column
-- naming a drawing that is not this document's.
--
-- WHY IT EXISTS
--
-- A document accumulates an extraction lineage every time it is read. Four separate
-- readings of one 41-page set produced four drawing lineages under one document row. The
-- reconstruction deliberately refuses to choose between them — it counts the drawings that
-- carry persisted readings and raises its ambiguity refusal when there is more than one —
-- and that refusal is CORRECT and is NOT touched by this file. What was missing was any way
-- for a human to SAY which lineage is the one to review. This adds the saying, and nothing
-- else.
--
-- ======================================================================================
-- WHAT THIS FILE DELIBERATELY DOES NOT DO
-- ======================================================================================
--
-- It does not select anything. The column is NULL for every existing row and no row is
-- updated, backfilled, defaulted or inferred. NULL means "no lineage has been selected",
-- and an unselected document behaves EXACTLY as it does today — including the ambiguity
-- refusal, which remains the only thing that happens when several readings exist and none
-- has been named.
--
-- It does not implement "latest wins", "newest completes", "best quality", "the completed
-- one" or any other rule that picks a lineage by inspecting the lineages. Chronology is not
-- authority here: a later reading may be a partial one, a retry, a failed attempt or a
-- reading taken under a different model, prompt or output ceiling, and inferring intent
-- from order would change what a reviewer sees without anyone deciding it.
--
-- It does not create a version graph, a history, an ancestry, a head pointer or a chain of
-- supersession links between drawings, runs or sets. There is no second table, no link
-- table, and no use of the document-level `supersedes_document_id` — a document is not a
-- lineage and this column is not a document.
--
-- It writes no review state: no revision, no recorded item, no claim and no artifact. It
-- does not open anything.
--
-- ======================================================================================
-- WHY A FOREIGN KEY ALONE WOULD NOT BE ENOUGH, AND WHY A GUARD IS
-- ======================================================================================
--
-- A plain foreign key proves the named drawing EXISTS. It does not prove the drawing
-- belongs to THIS document, because `drawings` reaches its document through a column on
-- itself and a column comparison cannot be expressed as a referential constraint. Without
-- the guard, a document could name another document's drawing — or, through the project
-- hop, another project's.
--
-- The constraint this needs is a composite one, `(selected_drawing_id, id)` against
-- `drawings (id, document_id)`. That would require a redundant unique index on an existing
-- evidence table, which this project has already declined to add for the same class of
-- hole. So the check is a trigger, for the reason the same decision records: the fact being
-- enforced lives one hop away.
--
-- The trigger fires BEFORE INSERT OR UPDATE, so the invariant cannot be established by any
-- path — server, script or hand-written statement — and it permits clearing the column
-- (NULL) as freely as setting it. A drawing this document does not own is refused; there is
-- no fallback, no substitution and no second guess.
--
-- ======================================================================================
-- AUTHORED, NOT APPLIED
-- ======================================================================================
--
-- As authored, this file has NOT been executed against any database, and no migration
-- runner, CLI push or CI job applies it. Nothing reads the column yet in the running
-- service. The reading code that will depend on it lands in the same working tree and is
-- NOT deployed while the column is absent, so the order is: apply this, then deploy the
-- code that reads it.
-- ======================================================================================

begin;

-- --------------------------------------------------------------------------------------
-- 1. The selection. Nullable, no default, no backfill.
-- --------------------------------------------------------------------------------------
alter table public.project_documents
    add column if not exists selected_drawing_id uuid
        references public.drawings(id) on delete restrict;

comment on column public.project_documents.selected_drawing_id is
    'The ONE drawing whose persisted reading is this document''s authoritative extraction. '
    'NULL means no lineage has been selected, and the document behaves exactly as it did '
    'before this column existed. Set only by an explicit human action; never inferred, '
    'never defaulted, never chosen by recency, completeness or quality.';

-- The lookup the reconstruction performs is "the selection for this document", which is
-- already the primary key, so no index is added: a document is read by its own id.

-- --------------------------------------------------------------------------------------
-- 2. The guard: the selection must name a drawing of THIS document, in THIS project.
--
--    `drawings` carries no project of its own — it reaches one through its set — so the
--    project is read the same way the reading path reads it, one hop up.
-- --------------------------------------------------------------------------------------
create or replace function public.project_document_selected_lineage_guard()
returns trigger
language plpgsql
as $$
declare
    v_drawing_document uuid;
    v_drawing_project  uuid;
begin
    -- Nothing selected is the ordinary state and is never inspected.
    if new.selected_drawing_id is null then
        return new;
    end if;

    select d.document_id, s.project_id
      into v_drawing_document, v_drawing_project
      from public.drawings d
      join public.drawing_sets s on s.id = d.drawing_set_id
     where d.id = new.selected_drawing_id;

    if v_drawing_document is null and v_drawing_project is null then
        raise exception
            'LINEAGE_SELECTION_REFUSED_DRAWING_UNKNOWN: document % names drawing %, which '
            'does not exist', new.id, new.selected_drawing_id
            using errcode = 'P0001';
    end if;

    -- A drawing with no document of its own cannot be this document's lineage.
    if v_drawing_document is null or v_drawing_document <> new.id then
        raise exception
            'LINEAGE_SELECTION_REFUSED_FOREIGN_DOCUMENT: document % may not select drawing '
            '%, which belongs to document %', new.id, new.selected_drawing_id,
            coalesce(v_drawing_document::text, '(none)')
            using errcode = 'P0001';
    end if;

    if v_drawing_project <> new.project_id then
        raise exception
            'LINEAGE_SELECTION_REFUSED_FOREIGN_PROJECT: document % of project % may not '
            'select a drawing of project %', new.id, new.project_id, v_drawing_project
            using errcode = 'P0001';
    end if;

    return new;
end;
$$;

create trigger project_document_selected_lineage_guard
    before insert or update of selected_drawing_id on public.project_documents
    for each row execute function public.project_document_selected_lineage_guard();

-- --------------------------------------------------------------------------------------
-- 3. Privileges are unchanged: this is a column on a table the server's own role already
--    updates, so no new grant is stated. RLS is already enabled on the table with zero
--    policies, and this file does not alter either.
-- --------------------------------------------------------------------------------------
revoke all on function public.project_document_selected_lineage_guard()
    from public, anon, authenticated;

commit;

-- The REST surface caches the schema; without this it keeps describing the table as it was.
notify pgrst, 'reload schema';
