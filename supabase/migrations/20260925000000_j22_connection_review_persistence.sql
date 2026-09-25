-- Milestone J22 — connection-review persistence.
--
-- WHY: J21 designed (and proved, over the genuine Arkles capture) the minimum durable
-- state a connection review leaves behind, and J20 established that today no such state
-- survives a request: the 7AJ workflow lives in one process, and a resolved human answer
-- exists nowhere once that process ends. This file is the storage half of closing that
-- gap. It creates the two tables J21 specified, the one function that writes them
-- atomically, and the one trigger that keeps them append-only.
--
-- WHAT: exactly three things.
--   1. public.connection_review_snapshots — the revision header (one row per
--      (project, 7AJ revision)) with its primary key.
--   2. public.connection_review_items   — the per-connection state at that revision, keyed
--      (project, revision, review_package_id), with a composite foreign key to the header.
--   3. public.record_connection_review_snapshot(...) — the single writer: one header and
--      all of its items, in one transaction, refusing a revision that is not the project's
--      next one. Plus public.connection_review_append_only(), a before-update-or-delete
--      trigger on both tables.
--
-- No data movement of any kind: no row of any existing table is read, written, altered or
-- copied, no column is added to any existing table, and no historical project is
-- backfilled. Immediately after this migration every existing project (Arkles, Selby, and
-- every other) has exactly zero connection-review rows — a project with no rows has NO
-- persisted review state, which is a state the read path must report and never invent
-- (J21 Decision B).
--
-- ======================================================================================
-- WHY THESE COLUMN TYPES
-- ======================================================================================
--
-- * `json`, NEVER `jsonb`, for all thirteen payload columns. This is J21 Decision A and it
--   is load-bearing, not stylistic. A jsonb column normalises: it re-orders object keys,
--   drops duplicate keys and discards the input text. 7AK's contract is built from
--   `repr()` display strings in places, and the genuine capture in
--   tests/test_real_world_j21_connection_review_data_model_design.py already diverges on
--   key order ({'type': 'end_plate', ...} vs {'depth_mm': 250, ...}); a jsonb column would
--   silently re-render a human's recorded answer. `json` stores the text it was given.
--
-- * Payloads therefore reach this function as TEXT and are cast `::json` INSIDE it. The
--   text is a character string on the wire, so nothing between the caller's `json.dumps`
--   and this cast can re-order a key: PostgREST parses the body, but it parses it into a
--   *string scalar*, and a string scalar's characters survive transport unchanged. Passing
--   a parsed object instead would hand the payload to the transport's own serialiser, whose
--   key order is not ours to promise. `->` and `->>` on a `json` value likewise return the
--   original text, so the item payloads never round-trip through any normaliser either.
--
-- * NULL means "this never happened", never a status. `output_status`,
--   `verification_status`, `connection_id` and `last_processed_revision` are all nullable
--   and all carry that single meaning: the connection was never dispatched, never verified,
--   has no identity a human supplied, was never processed. Every status VALUE is a non-NULL
--   string from its owning module's vocabulary, so "not attempted" can never be read as one
--   of them. `recorded_at` is the one column that is not a 7AK field: it is temporal
--   provenance, defaulted by the database, read by nothing the contract exposes.
--
-- * The CHECK vocabularies are the owning modules' own constants, not retyped literals:
--     decision            -> AUTOMATION_DECISIONS   (app/cad_engine/automation_gate.py:136)
--     output_status       -> OUTPUT_STATUSES        (app/cad_engine/drawing_dispatch.py:123)
--     verification_status -> VERIFICATION_STATUSES  (app/cad_engine/drawing_output_verification.py:130)
--     project_status      -> AUTOMATION_DECISIONS   (app/cad_engine/fabrication_output_gate.py:513-519
--                                                     assigns package_decision from exactly these three,
--                                                     and 7AK sets project_status=workflow.project_decision)
--   A vocabulary change in any of those modules must fail a test that reads this file,
--   never silently diverge from it. A CHECK on a nullable column needs no `is null or`:
--   SQL passes a CHECK whose expression evaluates to unknown.
--
-- ======================================================================================
-- WHY THE FOREIGN KEYS
-- ======================================================================================
--
-- `project_id` -> `projects(id)` is INCLUDED, `on delete restrict`.
--
--   * Included, because it is the only thing that makes "the review belongs to this
--     project" a fact the database enforces rather than a claim the caller makes. An
--     insert naming a project that does not exist fails with 23503 and the writer maps
--     that to J21's SNAPSHOT_REFUSED_PROJECT_UNKNOWN — a refusal the FK provides for free,
--     and one no amount of Python can provide as reliably.
--   * It is the SAME relationship the production review boundary authorizes on
--     (projects.user_id), one hop away. No second ownership column, no membership table,
--     no reviewer role.
--   * `on delete restrict` rather than the CASCADE the four existing project-child tables
--     (connections, drawing_sets, review_items, steel_members) use. A cascade is the right
--     rule for derived extraction output, which can simply be re-extracted. It is the wrong
--     rule here: a review snapshot records a HUMAN DECISION, and a cascade would let a
--     project deletion silently destroy the only copy of it. Restrict makes that deletion
--     fail loudly instead. Nothing in the repository deletes a `projects` row today, so
--     this is inert now and deliberate later.
--   * The brief's "no cascade that would undermine append-only history" is exactly this
--     decision, taken on the one FK the brief asked about.
--
-- `(project_id, review_revision)` -> `connection_review_snapshots` is INCLUDED on the item
-- table, with the same `on delete restrict`, so an item can never outlive or precede its
-- header. Restrict is again the honest rule (the append-only trigger below fires before
-- either could ever be reached), and it costs nothing: no delete is expected to succeed.
--
-- ======================================================================================
-- WHY NO INDEX
-- ======================================================================================
--
-- J21 declared DECLARED_INDEXES = () and this migration creates none. The read path
-- performs exactly two lookups and a primary key serves both: one snapshot at one revision
-- (the whole PK), and the highest revision of one project (the PK's leading column). The
-- item table's PK likewise leads with (project_id, review_revision), which is the composite
-- FK's referencing side. The only other FK is on the header's `project_id`, whose
-- referenced rows are never deleted (restrict), so no referencing index is owed. An index
-- no demonstrated query requires would be a guess about a access pattern that does not
-- exist yet.
--
-- ======================================================================================
-- WHY THE WRITER IS A FUNCTION AND NOT A CLIENT-SIDE SEQUENCE
-- ======================================================================================
--
-- A header with half its items is not a partially recorded revision; it is a WRONG one —
-- it claims a project's review state at revision N while listing some of its connections.
-- The application's existing writer style (`supabase.table(x).insert(...).execute()`) is
-- one HTTP round trip per statement, so a client-side sequence cannot promise this. A
-- plpgsql function body is one implicit transaction: the header insert and every item
-- insert commit together or not at all, and a failure the application cannot anticipate
-- (a CHECK, the FK, the PK, the revision guard) rolls all of them back with no cleanup and
-- no half-written revision to detect. PostgREST calls it as one RPC.
--
-- The function also owns the revision rule, because it is the only place the rule can be
-- enforced without a race. It reads the project's highest recorded revision, refuses
-- anything that is not the next one (rev 0 for a project with none), and its refusal
-- message carries J21's own code SNAPSHOT_REFUSED_REVISION_GAP — a duplicate and a gap are
-- the same finding, because both mean "this is not the project's next revision". A
-- transaction-scoped ADVISORY lock at the top serialises writers on the same project; it is
-- not `for update`, because that would require the UPDATE privilege this file revokes from
-- the writer's own role (see the correction recorded under EXECUTION STATE).
--
-- ======================================================================================
-- WHY THE APPEND-ONLY TRIGGER
-- ======================================================================================
--
-- J21's APPEND_ONLY_RULE is "no UPDATE, no DELETE and no upsert: a later review is a new
-- (project_id, review_revision) row, an evidence change makes an old snapshot STALE, and a
-- stale snapshot stays exactly as it was recorded". A module that simply never calls
-- update or delete does not establish that property — it only exercises it. The trigger
-- does: it fires for EVERY role, including service_role, which bypasses RLS. So the
-- property is a database fact rather than a convention, and a later milestone cannot
-- quietly acquire an update path.
--
-- The cost is stated plainly because it is real: a snapshot row cannot be deleted, by
-- anyone, through SQL. Removing one now requires a DDL change (dropping the trigger), which
-- is the deliberate friction an audit ledger should have. This milestone's own live
-- verification is a single transaction that ends in `rollback;` for exactly this reason.
--
-- ======================================================================================
-- RLS AND GRANTS
-- ======================================================================================
--
-- RLS is enabled on both tables with NO policy. That is J21's RLS_RULE: anon and
-- authenticated can read nothing and write nothing, and the only path in is the
-- server-side service-role client, which re-enforces projects.user_id (the repository's
-- single authorization path). The explicit revokes below state that rather than relying on it:
-- Supabase's default privileges grant new public tables to anon/authenticated, so the
-- revoke is the load-bearing line, not the enable. No reviewer role is created.
--
-- The writer function is SECURITY INVOKER (the default — `security definer` is deliberately
-- NOT used) and EXECUTE is revoked from public/anon/authenticated, so only service_role can
-- call it. The function reads no other table and takes no owner, role or user column: every
-- authorization decision stays where the review boundary already put it.
--
-- EXECUTION STATE. Two states, both recorded.
--
-- AUTHORED STATE: as authored this file was NOT APPLIED. It is the smallest migration the
-- change requires, and applying it is a credentialed operator decision — the file was not
-- written as an executed artefact.
--
-- APPLIED STATE: it WAS applied on 2026-09-25, through the Supabase Management API SQL
-- endpoint (`POST /v1/projects/{ref}/database/query`) — the credentialed mechanism this
-- project uses for live statements (the same one recorded in the J8B migration header). No
-- migration runner, CLI or CI job executed it, no migration tracking table records it, and
-- this header claims no mechanism beyond that. The authored state above is retained because
-- it is still part of the record; the change is live.
--
-- VERIFIED READ-ONLY AFTERWARDS, on both surfaces:
--   * information_schema.columns: both tables' columns exist with the types and
--     nullability above; every payload column reports data_type 'json' (never 'jsonb');
--     recorded_at is timestamptz with default now().
--   * pg_constraint: the two primary keys, the composite FK and the projects FK with
--     confdeltype 'r', and the four CHECK constraints, all as written here.
--   * pg_class: relrowsecurity true on both; pg_indexes: exactly the two primary keys and
--     no index beyond them (no index name in this file appears in pg_indexes).
--   * zero rows in both tables, and every pre-existing table's row counts unchanged
--     (steel_sections 222, steel_members 128, connections 5, connection_plates 3).
--   * one live transaction exercised the writer end to end — a genuine header plus item
--     through the RPC, the JSON text preserved byte for byte, revision 1 added without
--     touching revision 0, and then SIX refusals: a duplicate revision and a gap (revision
--     guard), an update and a delete (append-only trigger), a project delete (FK restrict)
--     and an unknown project (FK), plus two vocabulary violations (CHECK) — and it ended in
--     `rollback;`, so the live tables are still empty and no real project's review state
--     was manufactured.
--   * role_table_grants: anon and authenticated hold NOTHING on either table; service_role
--     holds INSERT and SELECT only — its UPDATE, DELETE and TRUNCATE were revoked, so the
--     append-only property holds on the privilege layer as well as at the trigger.
--
-- APPLIED-STATE CORRECTION, 2026-09-25. The version of this file that was first applied
-- used `select ... for update` to serialise writers, and that made the writer UNCALLABLE
-- through the REST surface: row locking needs UPDATE privilege on the table, while this
-- file revokes exactly that from service_role, so every RPC call failed with SQLSTATE
-- 42501 ("permission denied for table connection_review_snapshots") before reaching any of
-- the behaviour above. Two things about that are worth keeping:
--   * The live probe recorded above did NOT catch it, because it ran as the table owner,
--     whose privileges are not the application's. A probe that borrows the owner's rights
--     proves the SQL, not the deployment. The correction was found by calling the deployed
--     function through the same client the application uses.
--   * The fix is an advisory lock (`pg_advisory_xact_lock`), which needs no table
--     privilege and additionally serialises two concurrent FIRST writers — a case `for
--     update` could not cover, since there is no row to lock. It is stronger, not weaker.
-- The corrected `create or replace function public.record_connection_review_snapshot(...)`
-- body was applied live through the same Management API endpoint; the tables, constraints,
-- triggers and both privilege layers were already correct and were not re-applied.
--
-- RE-RUNNING THIS FILE IS NOT EXPECTED and is not safe to do blindly: the CREATE ... IF NOT
-- EXISTS clauses make the tables idempotent, but `create or replace function` would
-- overwrite the live function with the authored text — which is identical today — and the
-- `create trigger` statements would fail on an existing trigger. This file is not re-applied
-- by any test or task.

begin;

-- --------------------------------------------------------------------------------------
-- 1. The revision header.
-- --------------------------------------------------------------------------------------
create table if not exists public.connection_review_snapshots (
    project_id        uuid        not null,
    review_revision   integer     not null,
    evidence_identity json        not null,
    evidence_run_ids  json        not null,
    project_status    text        not null,
    recorded_at       timestamptz not null default now(),

    constraint connection_review_snapshots_pkey
        primary key (project_id, review_revision),

    constraint connection_review_snapshots_project_fkey
        foreign key (project_id) references public.projects (id) on delete restrict,

    -- 7AJ's counter: 0 when the workflow starts, +1 per resolve/refresh. Negative is
    -- not a revision this workflow can produce.
    constraint connection_review_snapshots_revision_check
        check (review_revision >= 0),

    -- AUTOMATION_DECISIONS, verbatim. See the header for why project_status is that
    -- vocabulary.
    constraint connection_review_snapshots_project_status_check
        check (project_status in ('AUTO', 'CONFIRM', 'REVIEW'))
);

-- --------------------------------------------------------------------------------------
-- 2. The per-connection state at that revision.
-- --------------------------------------------------------------------------------------
create table if not exists public.connection_review_items (
    project_id              uuid    not null,
    review_revision         integer not null,
    review_package_id       text    not null,

    -- NULL = no identity was ever supplied. NOT a uuid column: the identity is "assigned
    -- at persistence, or given by the reviewer" (app/cad_engine/connection_review_package.py,
    -- ConnectionReviewSupplement), and the genuine value the J21 proof records is
    -- "CONN-ARKLES-001". A uuid column would reject the very value the accepted design
    -- stores. It is text here for the same reason it is str | None in 7AJ's
    -- ProjectConnectionState.
    connection_id           text,

    decision                text    not null,

    -- NULL = never dispatched / never verified / never processed. Every one of these
    -- three has a non-NULL vocabulary and the two are not the same state.
    output_status           text,
    verification_status     text,
    last_processed_revision integer,

    blocker_codes           json    not null,
    warning_codes           json    not null,
    ai_readings             json    not null,
    evidence                json    not null,
    provenance              json    not null,
    tasks                   json    not null,
    generated_files         json    not null,

    constraint connection_review_items_pkey
        primary key (project_id, review_revision, review_package_id),

    constraint connection_review_items_snapshot_fkey
        foreign key (project_id, review_revision)
        references public.connection_review_snapshots (project_id, review_revision)
        on delete restrict,

    -- AUTOMATION_DECISIONS (app/cad_engine/automation_gate.py:136).
    constraint connection_review_items_decision_check
        check (decision in ('AUTO', 'CONFIRM', 'REVIEW')),

    -- OUTPUT_STATUSES (app/cad_engine/drawing_dispatch.py:123).
    constraint connection_review_items_output_status_check
        check (output_status in ('BLOCKED_REVIEW', 'BLOCKED_CONFIRMATION', 'GENERATED',
                                 'GENERATION_FAILED')),

    -- VERIFICATION_STATUSES (app/cad_engine/drawing_output_verification.py:130).
    constraint connection_review_items_verification_status_check
        check (verification_status in ('VERIFIED', 'FAILED', 'NOT_VERIFIABLE',
                                       'NO_ARTIFACT'))
);

-- --------------------------------------------------------------------------------------
-- 3. Append-only, for every role.
-- --------------------------------------------------------------------------------------
create or replace function public.connection_review_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception
        'SNAPSHOT_REFUSED_APPEND_ONLY: % on %.% is refused; a recorded review revision is '
        'never rewritten and never removed', tg_op, tg_table_schema, tg_table_name
        using errcode = 'P0001';
end;
$$;

create trigger connection_review_snapshots_append_only
    before update or delete on public.connection_review_snapshots
    for each row execute function public.connection_review_append_only();

create trigger connection_review_items_append_only
    before update or delete on public.connection_review_items
    for each row execute function public.connection_review_append_only();

-- --------------------------------------------------------------------------------------
-- 4. The one writer: one header and all of its items, or nothing.
-- --------------------------------------------------------------------------------------
create or replace function public.record_connection_review_snapshot(
    p_project_id        uuid,
    p_review_revision   integer,
    p_evidence_identity text,
    p_evidence_run_ids  text,
    p_project_status    text,
    p_items             text
)
returns void
language plpgsql
as $$
declare
    v_expected integer;
    v_item     json;
begin
    -- Serialise writers on one project with a transaction-scoped advisory lock keyed on the
    -- project — NOT `select ... for update`. Row locking requires UPDATE privilege on the
    -- table, and service_role deliberately holds none: the privilege layer at the foot of
    -- this file is part of the append-only property, so the writer must not depend on a
    -- privilege it revoked from itself. (This is not hypothetical. The first version of
    -- this function used `for update` and was DEPLOYED that way; every call through the
    -- REST surface failed with SQLSTATE 42501, "permission denied for table
    -- connection_review_snapshots", because the locking clause is an UPDATE-privileged
    -- operation. The live probe in this file's header did not catch it because it ran as
    -- the table owner. See the execution state below.)
    --
    -- The lock also covers the case row locking could not: two concurrent FIRST writers
    -- have no row to lock, so `for update` serialised nothing for them and left the primary
    -- key to reject one. An advisory lock serialises them before either reads a revision.
    perform pg_advisory_xact_lock(hashtextextended(p_project_id::text, 0));

    select coalesce(max(review_revision) + 1, 0)
      into v_expected
      from public.connection_review_snapshots
     where project_id = p_project_id;

    -- A duplicate (too low) and a gap (too high) are one finding: this is not the
    -- project's next revision. The revision is 7AJ's own and is never renumbered here.
    if p_review_revision <> v_expected then
        raise exception
            'SNAPSHOT_REFUSED_REVISION_GAP: project % records revision(s) up to %; revision % '
            'was offered, and a recorded revision is never overwritten',
            p_project_id, v_expected - 1, p_review_revision
            using errcode = 'P0001';
    end if;

    insert into public.connection_review_snapshots
        (project_id, review_revision, evidence_identity, evidence_run_ids, project_status)
    values
        (p_project_id, p_review_revision, p_evidence_identity::json,
         p_evidence_run_ids::json, p_project_status);

    for v_item in select * from json_array_elements(p_items::json) loop
        insert into public.connection_review_items
            (project_id, review_revision, review_package_id, connection_id, decision,
             output_status, verification_status, last_processed_revision, blocker_codes,
             warning_codes, ai_readings, evidence, provenance, tasks, generated_files)
        values
            (p_project_id,
             p_review_revision,
             v_item->>'review_package_id',
             v_item->>'connection_id',
             v_item->>'decision',
             v_item->>'output_status',
             v_item->>'verification_status',
             (v_item->>'last_processed_revision')::integer,
             v_item->'blocker_codes',
             v_item->'warning_codes',
             v_item->'ai_readings',
             v_item->'evidence',
             v_item->'provenance',
             v_item->'tasks',
             v_item->'generated_files');
    end loop;
end;
$$;

-- --------------------------------------------------------------------------------------
-- 5. RLS, privileges.
-- --------------------------------------------------------------------------------------
alter table public.connection_review_snapshots enable row level security;
alter table public.connection_review_items     enable row level security;

revoke all on table public.connection_review_snapshots from public, anon, authenticated;
revoke all on table public.connection_review_items     from public, anon, authenticated;

grant insert, select on table public.connection_review_snapshots to service_role;
grant insert, select on table public.connection_review_items     to service_role;

-- The server's own role may add a revision and read one, and nothing else. UPDATE and
-- DELETE are revoked so that the append-only property does not rest on the trigger alone,
-- and TRUNCATE is revoked because a ROW-level trigger cannot see TRUNCATE at all — without
-- this line the table would have a hole the trigger could not close. (A superuser can
-- still drop the table; that is not a claim this file makes. It claims that no path
-- through the application's own credentials can rewrite or remove a recorded revision.)
revoke update, delete, truncate on table public.connection_review_snapshots from service_role;
revoke update, delete, truncate on table public.connection_review_items     from service_role;

revoke all on function public.connection_review_append_only()
    from public, anon, authenticated;
revoke all on function public.record_connection_review_snapshot(
    uuid, integer, text, text, text, text) from public, anon, authenticated;

grant execute on function public.record_connection_review_snapshot(
    uuid, integer, text, text, text, text) to service_role;

commit;

-- The REST surface caches the schema; without this it keeps describing the database as it
-- was (the J8B migration header records the same step for the same reason).
notify pgrst, 'reload schema';
