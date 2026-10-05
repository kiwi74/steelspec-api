-- Milestone J44 — the project-review claim.
--
-- WHY: J42 established that single-writer is NOT a safe assumption for this deployment.
-- Every route in app/main.py is a sync `def`, so Starlette runs it in a threadpool and two
-- requests can be in flight inside ONE uvicorn worker before replicas are even considered;
-- `Dockerfile` passes no `--workers`, `railway.json` declares no replica count, and the
-- container filesystem is ephemeral. A production review operation therefore needs a
-- mechanism that stops two requests from doing the same expensive work at the same time.
--
-- WHAT THIS IS NOT. This table is a LIVENESS mechanism and nothing else. It is NOT an
-- authorization mechanism, and it is NOT the correctness barrier. Correctness stays exactly
-- where J22 put it: the transaction-scoped project advisory lock and the sequential review
-- revision rule inside `record_connection_review_snapshot` (SNAPSHOT_REFUSED_REVISION_GAP),
-- plus 7AJ's own stale-revision and one-shot-connection guards. A claim that is lost,
-- stolen, expired or never taken at all can produce wasted work and an orphan artifact, and
-- it can produce NO wrong review state, because it never writes review state.
--
-- WHAT: one table and two functions.
--   1. public.project_review_claims                 — one mutable row per project.
--   2. public.acquire_project_review_claim(...)     — acquire, refuse, or take over, atomically.
--   3. public.release_project_review_claim(...)     — release, by token, atomically.
--
-- No existing table is read, written or altered by this migration. No column is added to any
-- existing table. No review state, project row, snapshot, item, capture, member, connection
-- or artifact is touched. Immediately after it is applied every project still holds exactly
-- zero claims, which is the same state as "no claim": the read path treats an absent row and
-- an inactive row identically.
--
-- ======================================================================================
-- THE MUTABLE-TABLE EXCEPTION (deliberate, and the only one in this schema)
-- ======================================================================================
--
-- Every other table this project has created is APPEND-ONLY: each carries a
-- `before update or delete` trigger that refuses every role, and each has UPDATE, DELETE and
-- TRUNCATE revoked from service_role as well, so the property does not rest on the trigger
-- alone. They are not named individually here, and deliberately so: each of those names
-- belongs to the migration that created it, and this file borrows none of them.
--
-- THIS TABLE IS MUTABLE BY DESIGN, and it is the first and only such table here. Stated
-- plainly, because a reader who has learned the rule above will otherwise assume it holds
-- and be wrong:
--
--   * It contains NO engineering state. No decision, no status, no reading, no count.
--   * It contains NO human decision. No answer, no resolution, no evidence, no provenance.
--   * It contains NO evidence of any kind, and nothing derived from a drawing.
--   * J22 remains the immutable review ledger. Everything a review records — what a human
--     answered, what the gate decided, what was generated and verified — lives there, under
--     append-only protection that this file does not touch, weaken or reach.
--   * Claim mutation is NECESSARY for liveness. Acquire-with-takeover and release ARE
--     updates; a claim that cannot be updated cannot expire, cannot be released and cannot
--     be taken over after a crash, which would leave a project permanently blocked.
--   * Therefore this table deliberately has NO append-only trigger, and it is the one table
--     where service_role is granted UPDATE. Nothing else in this schema is relaxed.
--
-- The information cost is real and is accepted: a claim leaves no history. Which holder
-- held a claim, and when, is not recorded. Nothing needs it — the claim decides nothing and
-- proves nothing, and the review it guarded is fully recorded in J22 regardless. What IS
-- kept is enough to answer the only question a caller asks: is this project's claim active,
-- and if not, when did the previous one stop being active (`acquired_at`, `lease_expires_at`).
--
-- ======================================================================================
-- WHY THERE IS NO `released_at` COLUMN
-- ======================================================================================
--
-- J43 proposed six columns including a nullable `released_at`. It was reconsidered here and
-- REMOVED. What the design actually requires is four rules:
--
--   * an active claim is the current unexpired one;
--   * release must invalidate the current token;
--   * an expired claim may be taken over;
--   * a stale holder must not be able to release a successor's claim.
--
-- With `released_at`, "is this claim free?" is a CONJUNCTION of two columns
-- (`released_at is null and lease_expires_at > now()`), acquire has three branches
-- (absent / released / expired), and release must write one column while the other keeps
-- saying something else. Without it, "free" is one predicate on one column —
-- `lease_expires_at <= clock_timestamp()` — release is expressed by the same predicate it is
-- read by, and acquire has two branches (absent / inactive), because "released" and
-- "expired" are the same state: FREE. No consumer anywhere in the design needs to tell those
-- two apart; both mean exactly one thing, and the claim never records why it ended.
--
-- The price is stated rather than hidden: RELEASE WRITES AN EXPIRY IN THE PAST, so
-- `lease_expires_at` after a release reports when the claim stopped being valid rather than
-- the lease the holder was originally granted. That is a truthful reading of the column's
-- single job — when is this claim valid until — and it is why the CHECK below is `>=` rather
-- than `>`: a released row has `lease_expires_at` equal to the release instant, and the
-- invariant the table can honestly promise is that a claim never stops being valid before it
-- began.
--
-- The `holder` column is therefore also "the last holder", not "the current holder", and it
-- is never read to decide anything. It is provenance for an operator reading the row, and
-- nothing else. It is never returned by either function.
--
-- ======================================================================================
-- WHY clock_timestamp() AND NOT now()
-- ======================================================================================
--
-- `now()` is the TRANSACTION START time and is frozen for the whole transaction. A lease is
-- a wall-clock concept: it exists to bound how long a crashed holder blocks a project, and a
-- transaction that took a minute to reach the lease comparison must not be told that no time
-- has passed. `clock_timestamp()` is the actual current instant, which is what a lease means.
--
-- It is also what makes the expiry/takeover transition testable at all: with `now()` a
-- single transaction can never observe its own lease expiring, however long it sleeps.
--
-- The column DEFAULT on `acquired_at` stays `now()`, matching J22's `recorded_at` convention
-- for a creation timestamp; the functions always supply explicit values, so the default is a
-- safety net rather than the mechanism.
--
-- ======================================================================================
-- WHY ONE RPC PER OPERATION, AND WHY A CLAIM TABLE AT ALL
-- ======================================================================================
--
-- J42 audit 2 rejected a database lock that SPANS the Python work: PostgREST maps one HTTP
-- request to one transaction, so a transaction-scoped lock in a separate request is released
-- the instant that request returns, and a session-scoped lock is meaningless because the
-- pool hands the next request an arbitrary connection. A lock that cannot span the operation
-- cannot protect it. A claim BRACKETS the operation instead: acquired by one short
-- transaction, released by another, with an expiry so a crashed holder cannot block a
-- project forever.
--
-- `acquire` is one function and not a Python read-then-write for J22's own reason: a
-- read-then-write check is a race, and the copy of the rule that runs first is the one that
-- can be wrong. All three outcomes (create, refuse, take over) are one row under one lock in
-- one transaction.
--
-- The lock is `pg_advisory_xact_lock(hashtextextended(p_project_id::text, 0))` — the SAME
-- project-keyed derivation J22's writer uses, deliberately, so the two mechanisms agree
-- about what "the same project" means and can never deadlock against each other.
--
-- A NOTE ON `for update`, WHICH J22 COULD NOT USE. J22's first deployed writer locked with
-- `select ... for update` and was uncallable through REST, because that clause needs UPDATE
-- privilege and J22's own grant section revoked it from service_role (SQLSTATE 42501; the
-- correction is recorded in that file's header). THIS table may use it: UPDATE is granted
-- here by design, because a mutable table has to be updatable. The row lock is taken in
-- addition to the advisory lock so that the read-then-write inside `acquire` is serialised
-- against `release`, which takes no advisory lock of its own.
--
-- ======================================================================================
-- WHY RELEASE TAKES NO ADVISORY LOCK, AND WHY IT MATCHES ON THE TOKEN
-- ======================================================================================
--
-- Holder identity is NOT sufficient authority to release. The same reviewer can have two
-- requests in flight, and the older one must not release a claim the newer one legitimately
-- holds. `claim_token` is a fresh uuid on every acquisition, so a release that matches on it
-- can only ever release the acquisition it was given.
--
-- That token is also what makes release safe against a concurrent takeover WITHOUT a lock:
-- release's UPDATE and acquire's takeover UPDATE contend on the same row. The loser
-- re-evaluates its WHERE clause against the winner's committed row, and the token no longer
-- matches, so it affects zero rows and refuses. A lost update is not reachable here.
--
-- The guard is inside the UPDATE, not before it, so that two concurrent releases of the same
-- token behave exactly like two sequential ones: the first releases, the second finds
-- nothing to release and refuses. One rule, one race behaviour, whether or not the calls
-- overlap.
--
-- Release reuses the release instant as the expiry, which also means the token still matches
-- afterwards — so a repeat release is refused as NOT_ACTIVE rather than NOT_HOLDER, and that
-- distinction is precise: "you held this and it is over" is a different finding from "this is
-- not yours".
--
-- ======================================================================================
-- RLS AND GRANTS
-- ======================================================================================
--
-- RLS is enabled with NO policy, exactly as J22's two tables are. anon and authenticated can
-- read nothing and write nothing. Supabase's default privileges grant new public tables to
-- both roles, so the explicit revokes below are the load-bearing lines, not the enable.
--
-- service_role holds SELECT, INSERT and UPDATE — the minimum that lets each function's body
-- do its job, since both functions are SECURITY INVOKER (the default; `security definer` is
-- deliberately NOT used anywhere in this schema). DELETE and TRUNCATE are revoked: no
-- operation in the design removes a claim row, and withholding the privilege means a claim
-- can never be erased by a later milestone that thinks it is tidying up.
--
-- The consequence is recorded here so it is not discovered later: a claim row, once created,
-- cannot be deleted through the REST surface by anyone. It does not need to be — it is
-- reused for the project's lifetime and an inactive row is indistinguishable from an absent
-- one to every operation here. A disposable claim row is therefore not a thing this schema
-- can clean up, and this file's own live verification is a transaction ending in `rollback;`
-- for exactly that reason.
--
-- EXECUTE is revoked from public, anon and authenticated and granted only to service_role,
-- so the only path in is the server-side service-role client that re-enforces
-- `projects.user_id` — the repository's single authorization path.
--
-- ======================================================================================
-- AUTHORIZATION: WHAT THIS FILE DOES NOT DO
-- ======================================================================================
--
-- Neither function takes an owner, a role, a user, an email or a project owner. Neither
-- function reads `projects.user_id` or any other table. A claim is acquired AFTER the caller
-- has authenticated and been authorized, and this table contributes nothing to that decision
-- — it cannot grant access to a project, and it cannot deny access to one either. A caller
-- who cannot prove ownership never reaches this table, because the claim is not an
-- authorization step and is never used as one.
--
-- `p_holder` is provenance, recorded so an operator can read the row, and it is supplied by
-- the server from the verified token's subject — never from a request body. Its only
-- constraint is that it must be non-empty; it is never matched, never compared, and never
-- returned.
--
-- The refusal for an active claim deliberately does NOT name the holder. Following
-- `authorize_project`'s own rule that "a refusal never restates the other owner's id", the
-- caller learns that a review is in progress and when the lease ends, and nothing about who
-- holds it.
--
-- ======================================================================================
-- EXECUTION STATE
-- ======================================================================================
--
-- APPLIED STATE: this file has been applied to the intended SteelSpec database. The apply,
-- the read-only verification performed afterwards (16 checks) and the controlled live claim
-- tests are recorded in the J44 report. Applying it again is idempotent — it was re-applied
-- after this header was last edited, and the verification was re-run afterwards and passed
-- unchanged — so a re-apply changes no object and no row.
--
-- This header documents no credentials, no project reference and no connection detail: the
-- apply is a credentialed operator action, and the record of it is the J44 report.

begin;

-- --------------------------------------------------------------------------------------
-- 1. The claim row: one per project, mutable, reused for the project's lifetime.
-- --------------------------------------------------------------------------------------
create table if not exists public.project_review_claims (
    project_id       uuid        not null,

    -- Provenance only: the authenticated reviewer's subject as the server supplies it from
    -- the verified token. Never matched, never compared, never returned, never an authority.
    holder           text        not null,

    -- Fresh on every acquisition, so a release can only ever release the acquisition it was
    -- given. This is what stops a stale or superseded holder from releasing a successor.
    claim_token      uuid        not null default gen_random_uuid(),

    -- The instant this claim began, and the instant it stops being valid. There is no
    -- `released_at`: release writes an expiry in the past, and an inactive claim is free.
    acquired_at      timestamptz not null default now(),
    lease_expires_at timestamptz not null,

    constraint project_review_claims_pkey
        primary key (project_id),

    -- A token that could repeat is not a token. Uniqueness is not needed to make release
    -- correct — release matches on (project_id, claim_token) — but it makes "this token
    -- identifies exactly one acquisition" a fact rather than a hope.
    constraint project_review_claims_token_key
        unique (claim_token),

    -- The same FK J22's snapshot header uses, with the same `on delete restrict`: a cascade
    -- would silently destroy the record that a claim exists, and `restrict` makes the
    -- deletion fail loudly instead. Nothing deletes a `projects` row today.
    constraint project_review_claims_project_fkey
        foreign key (project_id) references public.projects (id) on delete restrict,

    -- An empty holder is not an unidentified holder, it is a caller that supplied nothing.
    constraint project_review_claims_holder_check
        check (holder <> ''),

    -- A claim never stops being valid before it began. `>=` and not `>` because release
    -- reuses this column to record the instant the claim stopped being valid (see the
    -- header): a released row is exactly the boundary case, and it is a legitimate state.
    constraint project_review_claims_lease_check
        check (lease_expires_at >= acquired_at)
);

-- No secondary index. Every operation addresses one project by primary key, and expiry is
-- evaluated on the row that lookup already found. Nothing scans by `lease_expires_at`.

-- --------------------------------------------------------------------------------------
-- 2. Acquire — create, refuse, or take over, in one transaction.
-- --------------------------------------------------------------------------------------
create or replace function public.acquire_project_review_claim(
    p_project_id    uuid,
    p_holder        text,
    p_lease_seconds integer
)
returns json
language plpgsql
as $$
declare
    v_held     timestamptz;
    v_token    uuid;
    v_acquired timestamptz;
    v_expires  timestamptz;
    v_took     boolean := false;
begin
    -- Shape refusals first, so a malformed call cannot reach the table at all. These are
    -- NOT duplicated in the accessor, and that is deliberate: a caller that skips the
    -- accessor must still not be able to plant an empty holder or a lease that never ends,
    -- so the database states the rule once and the accessor repeats none of it — it passes
    -- both parameters through, which is what keeps CLAIM_REFUSED_NO_HOLDER and
    -- CLAIM_REFUSED_LEASE reachable from the accessor rather than shadowed by it.
    if p_holder is null or btrim(p_holder) = '' then
        raise exception
            'CLAIM_REFUSED_NO_HOLDER: a claim must name the authenticated holder it was '
            'acquired for; an empty holder is not an unidentified one'
            using errcode = 'P0001';
    end if;

    if p_lease_seconds is null or p_lease_seconds < 1 or p_lease_seconds > 86400 then
        raise exception
            'CLAIM_REFUSED_LEASE: a lease must be between 1 second and 24 hours; % was '
            'offered', coalesce(p_lease_seconds::text, 'null')
            using errcode = 'P0001';
    end if;

    -- Serialise on the project, with J22's own key derivation, so this mechanism and the
    -- review writer agree about what "the same project" is and cannot deadlock.
    perform pg_advisory_xact_lock(hashtextextended(p_project_id::text, 0));

    -- The row lock is taken in addition, and it is usable here because UPDATE is granted on
    -- this table (see the header). It serialises this read-then-write against `release`,
    -- which deliberately takes no advisory lock of its own.
    select c.lease_expires_at
      into v_held
      from public.project_review_claims c
     where c.project_id = p_project_id
       for update;

    if not found then
        -- No claim has ever existed for this project. The FK refuses an unknown project
        -- here, which is the only place a project can be unknown to this table.
        insert into public.project_review_claims
            (project_id, holder, acquired_at, lease_expires_at)
        values
            (p_project_id, p_holder, clock_timestamp(),
             clock_timestamp() + make_interval(secs => p_lease_seconds))
        returning claim_token, acquired_at, lease_expires_at
             into v_token, v_acquired, v_expires;

    elsif v_held <= clock_timestamp() then
        -- Inactive: expired, or released (release writes an expiry in the past). One branch,
        -- because those are one state. A fresh token is the point of the takeover — the
        -- previous holder's token must never release this claim.
        update public.project_review_claims
           set holder           = p_holder,
               claim_token      = gen_random_uuid(),
               acquired_at      = clock_timestamp(),
               lease_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds)
         where project_id = p_project_id
        returning claim_token, acquired_at, lease_expires_at
             into v_token, v_acquired, v_expires;
        v_took := true;

    else
        -- Active. The refusal states that a review is in progress and when the lease ends.
        -- It does NOT name the holder: a refusal never restates another identity's details.
        raise exception
            'CLAIM_REFUSED_ACTIVE: project % is already being reviewed under an active '
            'claim, which expires at %', p_project_id, v_held
            using errcode = 'P0001',
                  hint = 'lease_expires_at=' || v_held::text;
    end if;

    return json_build_object(
        'claim_token',      v_token,
        'acquired_at',      v_acquired,
        'lease_expires_at', v_expires,
        'took_over',        v_took
    );
end;
$$;

-- --------------------------------------------------------------------------------------
-- 3. Release — by token, and only by token.
-- --------------------------------------------------------------------------------------
create or replace function public.release_project_review_claim(
    p_project_id  uuid,
    p_claim_token uuid
)
returns void
language plpgsql
as $$
declare
    v_token   uuid;
    v_expires timestamptz;
begin
    -- The guard is INSIDE the update, so a concurrent duplicate release behaves exactly like
    -- a sequential one: the second finds nothing to release and refuses. Doing the check in
    -- a separate statement first would make the two call orders behave differently.
    update public.project_review_claims
       set lease_expires_at = clock_timestamp()
     where project_id = p_project_id
       and claim_token = p_claim_token
       and lease_expires_at > clock_timestamp();

    if found then
        return;
    end if;

    -- Nothing was released. Say precisely why, and name no holder.
    select c.claim_token, c.lease_expires_at
      into v_token, v_expires
      from public.project_review_claims c
     where c.project_id = p_project_id;

    if v_token is null then
        raise exception
            'CLAIM_REFUSED_NOT_HOLDER: project % holds no claim that this token acquired',
            p_project_id
            using errcode = 'P0001';
    end if;

    if v_token <> p_claim_token then
        raise exception
            'CLAIM_REFUSED_NOT_HOLDER: project % is not held by this token; a superseded or '
            'unknown token never releases the current claim', p_project_id
            using errcode = 'P0001';
    end if;

    -- The token matches and yet nothing was released, which has exactly one cause: the claim
    -- is no longer active. Release reuses the expiry column to record that fact, so a second
    -- release with the same token lands here rather than releasing twice.
    raise exception
        'CLAIM_REFUSED_NOT_ACTIVE: project % is no longer under an active claim (it expired '
        'at %); an inactive claim is already free and is not released again',
        p_project_id, v_expires
        using errcode = 'P0001';
end;
$$;

-- --------------------------------------------------------------------------------------
-- 4. RLS and privileges.
-- --------------------------------------------------------------------------------------
alter table public.project_review_claims enable row level security;

-- No policy is created. anon and authenticated can therefore see and do nothing; only the
-- server-side service-role client can reach the table at all.
revoke all on table public.project_review_claims from public, anon, authenticated;

-- The minimum each function's body needs, and nothing more. UPDATE is granted because this
-- is the schema's one mutable table and both acquire and release are updates by nature.
grant select, insert, update on table public.project_review_claims to service_role;

-- No operation in the design removes a claim row, and an inactive row is indistinguishable
-- from an absent one to every function above. Revoking DELETE and TRUNCATE means a claim
-- cannot be erased by a later milestone that mistakes tidying up for safety.
revoke delete, truncate on table public.project_review_claims from service_role;

revoke all on function public.acquire_project_review_claim(uuid, text, integer)
    from public, anon, authenticated;
revoke all on function public.release_project_review_claim(uuid, uuid)
    from public, anon, authenticated;

grant execute on function public.acquire_project_review_claim(uuid, text, integer)
    to service_role;
grant execute on function public.release_project_review_claim(uuid, uuid)
    to service_role;

-- The REST surface caches the schema; without this it keeps describing the database as it
-- was and the two functions are not callable through PostgREST.
notify pgrst, 'reload schema';

commit;
