"""
Milestone J44 — THE PROJECT REVIEW CLAIM, TESTED.

WHAT WAS PROVEN LIVE, AND IS NOT RE-PROVEN HERE
===============================================
Three things were proven against the REAL database, and a test cannot reproduce them
without writing to a live project:

  * THE MIGRATION, applied and read back: 16/16 read-only checks over the table, its
    columns, its constraints, its grants, both functions' signatures and both ACLs.
  * THE STATE MACHINE, driven through the committed functions inside one transaction that
    ended in `rollback;`: 16/16 transitions, from a first acquisition to a one-second lease
    expiring into a takeover, with the claim table empty afterwards.
  * TWO CONCURRENT SESSIONS: an acquire on a project whose claim transaction was still open
    waited 4.6s for it against a 5ms control, and a release returned in 1ms while that same
    lock was held — the claim serialises acquisition, and deliberately does not serialise
    release.

WHAT THIS FILE TESTS
====================
Everything that does NOT need a live write:

  1. THE MIGRATION IS THE DESIGNED MECHANISM. Read from the migration file itself: one
     transaction, one table, five columns, the two keys, the two checks, no append-only
     trigger, the grants, both signatures and their EXECUTE grants.
  2. THE ACCESSOR CALLS THE DESIGNED FUNCTIONS. Against an in-process stand-in for the
     service-role client: the exact function names, the exact parameters, and what the
     accessor returns.
  3. THE ACCESSOR TRANSLATES WHAT THE DATABASE REFUSES. Every refusal code, the lease expiry
     carried on an active refusal, the holder never leaked, and an unnamed failure re-raised
     unchanged.
  4. THE ACCESSOR ADDS NO RULE OF ITS OWN. An empty holder and an out-of-range lease reach
     the database rather than being shadowed by a second, weaker copy of its rules.
  5. THE BOUNDARIES. The module imports nothing from this application, names no part of the
     system it does not own, and no route was added.
  6. THE LIVE DEPLOYMENT, READ-ONLY. Refusal-only calls, which cannot write anything: the
     real deployment refuses an unknown project, an empty holder, an out-of-range lease and
     an unheld release with the designed codes, and refuses a DELETE outright.

The double in section 2 restates the deployed functions' rules. It is NOT the authority for
any of them — the real functions were exercised live — it exists so that the accessor's
transport and translation can be driven without a live write, because this table cannot be
cleaned up through the REST surface by design: `delete` and `truncate` are revoked, so a
committed claim row is permanent until its lease expires.
"""

from __future__ import annotations

import ast
import inspect
import re
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.production_review import project_review_claim as claim

from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "supabase" / "migrations" / "20260928000000_j44_project_review_claims.sql"
MODULE_PATH = Path(claim.__file__)

#: A project id that exists in the live database. The live tests read a real one anyway;
#: this is the value the double is seeded with.
PROJECT_ID = "61de72a2-fd45-491e-a722-dca7afa20361"

#: A token that cannot be mistaken for a real acquisition.
TOKEN = "11111111-2222-3333-4444-555555555555"


# =============================================================================
# HELPERS
# =============================================================================
def _statements(sql: str) -> list[str]:
    """The migration's executable lines: no blanks, no comment-only lines."""
    return [
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]


def _function_sql(sql: str, name: str) -> str:
    """One function's DDL, from its `create or replace` to its closing `$$;`.

    Anchored on the CREATE and not on the name: a migration header that discusses a function
    by name would otherwise open the slice hundreds of lines too early.
    """
    start = sql.index(f"create or replace function public.{name}(")
    end = sql.index("\n$$;", start)
    return sql[start:end]


def _code_only(path: Path) -> str:
    """The module's CODE, with every docstring removed.

    The docstrings are where this module explains what it does NOT do — what it knows nothing
    about, and why. Searching them for the names of the parts it disowns would find the
    disavowals and call them dependencies.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(row) -> bool:
    return datetime.fromisoformat(row["lease_expires_at"]) <= _now()


# =============================================================================
# THE DOUBLE — an in-process stand-in for the service-role client.
# =============================================================================
class _ApiError(Exception):
    """The error shape `postgrest-py` raises for a call the database refused."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = None
        self.hint = hint


class _Result:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, store, fn, params):
        self._store = store
        self._fn = fn
        self._params = params

    def execute(self):
        return self._store.call(self._fn, self._params)


class _FakeStore:
    """Two functions and one mutable row, standing in for the service-role client.

    It restates the deployed rules — the holder check, the lease bounds, the foreign key, the
    active claim, the takeover of an expired claim, and the token-matched release — and it is
    NOT the authority for any of them. What it is for is driving the ACCESSOR: the refusals
    it raises are the refusals the accessor must translate, and the parameters it records are
    what would have crossed the wire.
    """

    def __init__(self, projects=(PROJECT_ID,)):
        self.projects = set(projects)
        self.claims: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def rpc(self, fn, params):
        self.calls.append(("rpc", fn, dict(params)))
        return _Rpc(self, fn, params)

    def rpcs(self):
        return [call for call in self.calls if call[0] == "rpc"]

    def hold(self, project_id, holder, token, *, expires_in=900):
        """A claim the store already holds. A negative `expires_in` is a lease that ran out."""
        start = _now()
        self.claims[project_id] = {
            "holder": holder,
            "claim_token": token,
            "acquired_at": start.isoformat(),
            "lease_expires_at": (start + timedelta(seconds=expires_in)).isoformat(),
        }
        return self.claims[project_id]

    def call(self, fn, params):
        if fn == claim.ACQUIRE_FUNCTION:
            return self._acquire(params)
        if fn == claim.RELEASE_FUNCTION:
            return self._release(params)
        raise _ApiError("PGRST202", f"Could not find the function public.{fn}")

    def _acquire(self, params):
        project = params["p_project_id"]
        holder = params["p_holder"]
        lease = params["p_lease_seconds"]
        if holder is None or holder.strip() == "":
            raise _ApiError(
                "P0001",
                "CLAIM_REFUSED_NO_HOLDER: a claim must name the authenticated holder it was "
                "acquired for; an empty holder is not an unidentified one",
            )
        if lease is None or lease < 1 or lease > 86400:
            raise _ApiError(
                "P0001",
                f"CLAIM_REFUSED_LEASE: a lease must be between 1 second and 24 hours; "
                f"{lease} was offered",
            )
        if project not in self.projects:
            raise _ApiError(
                "23503",
                'insert or update on table "project_review_claims" violates foreign key '
                'constraint "project_review_claims_project_fkey"',
            )
        existing = self.claims.get(project)
        took_over = existing is not None and _expired(existing)
        if existing is not None and not took_over:
            raise _ApiError(
                "P0001",
                f"CLAIM_REFUSED_ACTIVE: project {project} is already being reviewed under an "
                f"active claim, which expires at {existing['lease_expires_at']}",
                hint=f"lease_expires_at={existing['lease_expires_at']}",
            )
        row = self.hold(project, holder, str(uuid.uuid4()), expires_in=lease)
        return _Result({
            "claim_token": row["claim_token"],
            "acquired_at": row["acquired_at"],
            "lease_expires_at": row["lease_expires_at"],
            "took_over": took_over,
        })

    def _release(self, params):
        project = params["p_project_id"]
        token = params["p_claim_token"]
        existing = self.claims.get(project)
        if existing is None or existing["claim_token"] != token:
            raise _ApiError(
                "P0001",
                f"CLAIM_REFUSED_NOT_HOLDER: project {project} holds no claim that this token "
                "acquired",
            )
        if _expired(existing):
            raise _ApiError(
                "P0001",
                f"CLAIM_REFUSED_NOT_ACTIVE: project {project} holds a claim that was acquired "
                "with this token and is no longer active",
            )
        # Release writes an expiry in the past; a released claim is free.
        existing["lease_expires_at"] = _now().isoformat()
        return _Result(None)


@pytest.fixture()
def fake():
    return _FakeStore()


@pytest.fixture(scope="module")
def sql():
    assert MIGRATION.exists(), f"the J44 migration is missing: {MIGRATION}"
    return MIGRATION.read_text()


# =============================================================================
# 1. THE MIGRATION IS THE DESIGNED MECHANISM
# =============================================================================
class TestTheMigrationIsTheDesignedMechanism:
    def test_the_migration_is_one_transaction_that_reloads_the_rest_schema(self, sql):
        statements = _statements(sql)
        assert statements[0] == "begin;", statements[0]
        assert statements[-1] == "commit;", statements[-1]
        assert sql.count("\nbegin;") == 1
        assert sql.count("\ncommit;") == 1
        assert "notify pgrst, 'reload schema';" in statements

    def test_it_creates_the_claim_table_and_no_other_table(self, sql):
        assert re.findall(r"create table if not exists ([\w.]+)", sql) == [
            "public.project_review_claims"
        ]

    def test_the_five_designed_columns_are_the_only_ones(self, sql):
        body = sql.partition("create table if not exists public.project_review_claims (")[2]
        table, _, _ = body.partition("\n);")
        assert re.findall(r"^ {4}(\w+) +(uuid|text|timestamptz|integer)\b", table, re.M) == [
            ("project_id", "uuid"),
            ("holder", "text"),
            ("claim_token", "uuid"),
            ("acquired_at", "timestamptz"),
            ("lease_expires_at", "timestamptz"),
        ]
        for name in ("project_id", "holder", "claim_token", "acquired_at", "lease_expires_at"):
            line = re.search(r"^ {4}" + name + r" +.*$", table, re.M)
            assert line and "not null" in line.group(0), name

    def test_project_id_is_the_primary_key_and_the_token_is_unique(self, sql):
        assert "primary key (project_id)" in sql
        assert "unique (claim_token)" in sql
        assert re.search(r"claim_token +uuid +not null default gen_random_uuid\(\)", sql)

    def test_the_project_reference_restricts_deletion(self, sql):
        assert "references public.projects (id) on delete restrict" in sql
        assert "on delete cascade" not in sql

    def test_the_two_checks_state_the_two_content_rules(self, sql):
        assert "check (holder <> '')" in sql
        assert "check (lease_expires_at >= acquired_at)" in sql

    def test_there_is_no_released_column_because_free_is_one_predicate(self, sql):
        """J44 AUDIT 1: `released_at` was considered and rejected. Release writes an expiry
        in the past, so `lease_expires_at <= clock_timestamp()` is the whole of "free" — one
        predicate, two branches, and no second state to keep consistent with the first."""
        body = sql.partition("create table if not exists public.project_review_claims (")[2]
        table, _, _ = body.partition("\n);")
        columns = [
            name
            for name, _ in re.findall(
                r"^ {4}(\w+) +(uuid|text|timestamptz|integer)\b", table, re.M
            )
        ]
        assert "released_at" not in columns, columns
        assert len(columns) == 5, columns

    def test_no_secondary_index_is_created(self, sql):
        assert "create index" not in sql.lower()

    def test_rls_is_enabled_with_no_policy_and_no_append_only_trigger(self, sql):
        assert "alter table public.project_review_claims enable row level security;" in sql
        assert "create policy" not in sql.lower()
        # The claim row is MUTABLE by design: release and takeover both UPDATE it. An
        # append-only trigger would make the mechanism unimplementable, and its absence is
        # the mutable-table exception J44 AUDIT 2 requires this file to state.
        assert "create trigger" not in sql.lower()
        assert "never rewritten and never removed" not in sql
        assert "THE MUTABLE-TABLE EXCEPTION" in sql

    def test_the_service_role_may_write_the_claim_but_never_remove_it(self, sql):
        statements = _statements(sql)
        assert (
            "revoke all on table public.project_review_claims from public, anon, authenticated;"
            in statements
        )
        assert (
            "grant select, insert, update on table public.project_review_claims to service_role;"
            in statements
        )
        assert (
            "revoke delete, truncate on table public.project_review_claims from service_role;"
            in statements
        )

    def test_both_functions_have_the_designed_signatures_and_are_invoker_rights(self, sql):
        acquire = _function_sql(sql, "acquire_project_review_claim")
        assert re.search(r"p_project_id +uuid,", acquire)
        assert re.search(r"p_holder +text,", acquire)
        assert re.search(r"p_lease_seconds +integer", acquire)
        assert "returns json" in acquire
        release = _function_sql(sql, "release_project_review_claim")
        assert re.search(r"p_project_id +uuid,", release)
        assert re.search(r"p_claim_token +uuid", release)
        assert "returns void" in release
        # J22's lesson: the default INVOKER rights are relied on, and nothing escalates to
        # SECURITY DEFINER.
        assert "security definer" not in "\n".join(_statements(sql)).lower()

    def test_acquire_serialises_on_the_project_and_reads_the_wall_clock(self, sql):
        acquire = _function_sql(sql, "acquire_project_review_claim")
        assert "pg_advisory_xact_lock(hashtextextended(p_project_id::text, 0))" in acquire
        assert "for update" in acquire
        assert "clock_timestamp()" in acquire
        # now() is the transaction's start time, frozen for the whole transaction, so a lease
        # compared with it could never be observed expiring inside the transaction that tests
        # it. Only the acquisition stamp may use now(), as its column default.
        assert "now()" not in acquire.replace("default now()", "")
        assert "lease_expires_at <= now()" not in sql

    def test_release_matches_the_token_and_takes_no_lock(self, sql):
        release = _function_sql(sql, "release_project_review_claim")
        assert "and claim_token = p_claim_token" in release
        assert "clock_timestamp()" in release
        # Release takes no advisory lock: the guarded UPDATE plus the token match already make
        # concurrent and sequential repeat releases behave identically, and a release that
        # queued behind an in-flight acquisition would block a reviewer for no reason.
        assert "pg_advisory" not in release

    def test_the_two_functions_are_the_only_ones_created(self, sql):
        assert re.findall(r"create or replace function public\.(\w+)", sql) == [
            "acquire_project_review_claim",
            "release_project_review_claim",
        ]

    def test_execute_is_granted_to_the_service_role_and_to_no_one_else(self, sql):
        statements = [line.lstrip() for line in _statements(sql)]
        joined = "\n".join(statements)
        assert (
            "revoke all on function public.acquire_project_review_claim(uuid, text, integer)\n"
            "from public, anon, authenticated;"
        ) in joined
        assert (
            "revoke all on function public.release_project_review_claim(uuid, uuid)\n"
            "from public, anon, authenticated;"
        ) in joined
        assert (
            "grant execute on function public.acquire_project_review_claim(uuid, text, integer)\n"
            "to service_role;"
        ) in joined
        assert (
            "grant execute on function public.release_project_review_claim(uuid, uuid)\n"
            "to service_role;"
        ) in joined

    def test_the_migration_alters_no_existing_table_and_writes_no_row_of_its_own(self, sql):
        ddl = sql.partition("create or replace function")[0]
        for statement in _statements(ddl):
            assert not statement.startswith(("insert into", "delete from", "drop ")), statement
            assert not statement.startswith("update "), statement
            assert not statement.lower().startswith("alter table public.projects"), statement
        # `projects` is named once, by the foreign key, and never written.
        assert sum("public.projects (id)" in line for line in _statements(sql)) == 1

    def test_the_migration_names_no_engineering_state(self, sql):
        """The claim knows a project, a holder, a token and two instants. Nothing else."""
        for disowned in (
            "connection_review_snapshots",
            "connection_review_items",
            "steel_members",
            "review_revision",
            "review_package_id",
            "generated_files",
            "page_extractions",
        ):
            assert disowned not in "\n".join(_statements(sql)), disowned


# =============================================================================
# 2. THE ACCESSOR CALLS THE DESIGNED FUNCTIONS
# =============================================================================
class TestTheAccessorCallsTheDesignedFunctions:
    def test_acquire_calls_the_designed_function_with_the_designed_parameters(self, fake):
        claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)
        assert fake.rpcs() == [
            ("rpc", claim.ACQUIRE_FUNCTION, {
                "p_project_id": PROJECT_ID,
                "p_holder": "reviewer-sub-1",
                "p_lease_seconds": claim.DEFAULT_LEASE_SECONDS,
            })
        ]

    def test_the_lease_default_is_the_designed_quarter_hour(self):
        assert claim.DEFAULT_LEASE_SECONDS == 900

    def test_a_caller_supplied_lease_is_sent_unchanged(self, fake):
        claim.acquire_project_review_claim(
            PROJECT_ID, "reviewer-sub-1", lease_seconds=60, client=fake
        )
        assert fake.rpcs()[0][2]["p_lease_seconds"] == 60

    def test_acquire_returns_what_the_database_returned_and_nothing_it_computed(self, fake):
        got = claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)
        stored = fake.claims[PROJECT_ID]
        assert isinstance(got, claim.ProjectReviewClaim)
        assert got.project_id == PROJECT_ID
        assert got.claim_token == stored["claim_token"]
        assert got.acquired_at == stored["acquired_at"]
        assert got.lease_expires_at == stored["lease_expires_at"]

    def test_a_first_acquisition_is_not_a_takeover(self, fake):
        got = claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)
        assert got.took_over is False
        assert got.claim_token

    def test_an_expired_claim_is_taken_over_and_that_is_not_an_error(self, fake):
        """The claim is a liveness device: a holder whose process died leaves a claim that
        expires on its own, and the next reviewer takes it over. `took_over` reports that it
        happened; nothing about it is exceptional."""
        fake.hold(PROJECT_ID, "a-reviewer-who-died", TOKEN, expires_in=-60)
        got = claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)
        assert got.took_over is True
        assert got.claim_token != TOKEN
        assert fake.claims[PROJECT_ID]["holder"] == "reviewer-sub-1"

    def test_release_calls_the_designed_function_with_the_token(self, fake):
        fake.hold(PROJECT_ID, "reviewer-sub-1", TOKEN)
        claim.release_project_review_claim(PROJECT_ID, TOKEN, client=fake)
        assert fake.rpcs() == [
            ("rpc", claim.RELEASE_FUNCTION, {
                "p_project_id": PROJECT_ID,
                "p_claim_token": TOKEN,
            })
        ]

    def test_release_returns_nothing_so_it_can_never_be_read_as_a_state(self, fake):
        fake.hold(PROJECT_ID, "reviewer-sub-1", TOKEN)
        assert claim.release_project_review_claim(PROJECT_ID, TOKEN, client=fake) is None

    def test_release_matches_the_token_and_never_the_holder(self, fake):
        """Two reviewers can share a name and a name is not a secret: matching on the holder
        would let one review session end another's."""
        fake.hold(PROJECT_ID, "reviewer-sub-1", TOKEN)
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.release_project_review_claim(
                PROJECT_ID, str(uuid.uuid4()), client=fake
            )
        assert refused.value.code == claim.CLAIM_REFUSED_NOT_HOLDER
        assert not _expired(fake.claims[PROJECT_ID])

    def test_the_client_is_imported_on_use_and_not_at_module_import(self):
        tree = ast.parse(MODULE_PATH.read_text())
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        assert not [name for name in names if name.startswith("app")], names
        assert "from app.supabase_client import supabase" in MODULE_PATH.read_text()

    def test_the_lease_bounds_exported_are_the_databases_own(self):
        assert claim.LEASE_BOUNDS_SECONDS == (1, 86400)


# =============================================================================
# 3. THE ACCESSOR TRANSLATES WHAT THE DATABASE REFUSES
# =============================================================================
class TestTheAccessorTranslatesWhatTheDatabaseRefuses:
    def test_an_active_claim_is_refused_with_its_code_and_lease_expiry(self, fake):
        held = fake.hold(PROJECT_ID, "first-holder", TOKEN)
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.acquire_project_review_claim(PROJECT_ID, "second-holder", client=fake)
        assert refused.value.code == claim.CLAIM_REFUSED_ACTIVE
        assert refused.value.lease_expires_at == held["lease_expires_at"]
        assert "already being reviewed" in refused.value.statement

    def test_a_refusal_never_names_the_holder(self, fake):
        """The claim records who holds it. The refusal repeats that it is held and until when,
        and never by whom: the holder column is provenance, not a message."""
        fake.hold(PROJECT_ID, "first-holder", TOKEN)
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.acquire_project_review_claim(PROJECT_ID, "second-holder", client=fake)
        assert "first-holder" not in str(refused.value)
        assert "second-holder" not in str(refused.value)

    def test_an_unknown_project_is_refused_as_project_unknown(self, fake):
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.acquire_project_review_claim(str(uuid.uuid4()), "reviewer-sub-1", client=fake)
        assert refused.value.code == claim.CLAIM_REFUSED_PROJECT_UNKNOWN
        assert refused.value.lease_expires_at is None

    def test_a_wrong_token_is_refused_as_not_holder(self, fake):
        fake.hold(PROJECT_ID, "first-holder", TOKEN)
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.release_project_review_claim(PROJECT_ID, str(uuid.uuid4()), client=fake)
        assert refused.value.code == claim.CLAIM_REFUSED_NOT_HOLDER

    def test_a_repeat_release_is_refused_and_not_reported_as_idempotent(self, fake):
        """J44 AUDIT 5, decided explicitly: repeating a release is REFUSED. A claim already
        released is not one this call released, and reporting success would make a double
        release indistinguishable from a single one."""
        fake.hold(PROJECT_ID, "first-holder", TOKEN)
        claim.release_project_review_claim(PROJECT_ID, TOKEN, client=fake)
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.release_project_review_claim(PROJECT_ID, TOKEN, client=fake)
        assert refused.value.code == claim.CLAIM_REFUSED_NOT_ACTIVE

    def test_a_superseded_token_cannot_release_its_successor(self, fake):
        fake.hold(PROJECT_ID, "stale-holder", TOKEN, expires_in=-60)
        fresh = claim.acquire_project_review_claim(PROJECT_ID, "current-holder", client=fake)
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.release_project_review_claim(PROJECT_ID, TOKEN, client=fake)
        assert refused.value.code == claim.CLAIM_REFUSED_NOT_HOLDER
        assert not _expired(fake.claims[PROJECT_ID])
        assert fake.claims[PROJECT_ID]["claim_token"] == fresh.claim_token

    def test_a_refusal_is_an_error_so_no_caller_can_read_it_as_success(self, fake):
        with pytest.raises(claim.ClaimRefused):
            claim.release_project_review_claim(PROJECT_ID, str(uuid.uuid4()), client=fake)
        assert issubclass(claim.ClaimRefused, ValueError)

    def test_a_failure_the_module_cannot_name_is_re_raised_unchanged(self, fake):
        """Returning a refusal here would be inventing an explanation. A failure that is not
        one of the designed refusals is left as the failure it was."""
        def explode(fn, params):
            raise _ApiError("PGRST301", "JWT expired")

        fake.call = explode
        with pytest.raises(_ApiError) as raised:
            claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)
        assert raised.value.code == "PGRST301"

    def test_a_raised_refusal_of_another_vocabulary_is_not_claimed_as_one_of_ours(self, fake):
        def explode(fn, params):
            raise _ApiError("P0001", "SNAPSHOT_REFUSED_REVISION_GAP: something else entirely")

        fake.call = explode
        with pytest.raises(_ApiError):
            claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)

    def test_a_result_that_is_neither_a_claim_nor_a_refusal_is_not_describable(self, fake):
        fake.call = lambda fn, params: _Result(None)
        with pytest.raises(RuntimeError) as wrong:
            claim.acquire_project_review_claim(PROJECT_ID, "reviewer-sub-1", client=fake)
        assert "no claim" in str(wrong.value)

    def test_every_translated_code_is_one_of_the_modules_own_constants(self, fake):
        fake.hold(PROJECT_ID, "first-holder", TOKEN)
        codes = set()
        with pytest.raises(claim.ClaimRefused) as active:
            claim.acquire_project_review_claim(PROJECT_ID, "second-holder", client=fake)
        codes.add(active.value.code)
        with pytest.raises(claim.ClaimRefused) as not_holder:
            claim.acquire_project_review_claim(str(uuid.uuid4()), "second-holder", client=fake)
        codes.add(not_holder.value.code)
        with pytest.raises(claim.ClaimRefused) as no_holder:
            claim.acquire_project_review_claim(PROJECT_ID, "", client=fake)
        codes.add(no_holder.value.code)
        with pytest.raises(claim.ClaimRefused) as lease:
            claim.acquire_project_review_claim(PROJECT_ID, "h", lease_seconds=0, client=fake)
        codes.add(lease.value.code)
        assert codes == {
            claim.CLAIM_REFUSED_ACTIVE,
            claim.CLAIM_REFUSED_PROJECT_UNKNOWN,
            claim.CLAIM_REFUSED_NO_HOLDER,
            claim.CLAIM_REFUSED_LEASE,
        }


# =============================================================================
# 4. THE ACCESSOR ADDS NO RULE OF ITS OWN
# =============================================================================
class TestTheAccessorAddsNoRuleOfItsOwn:
    def test_an_empty_holder_reaches_the_database_and_is_refused_there(self, fake):
        """The empty-holder rule is a CONTENT rule the database states as
        CLAIM_REFUSED_NO_HOLDER. A Python check here would shadow it and become the second,
        weaker copy — the copy that runs first, and therefore the one that can be wrong."""
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.acquire_project_review_claim(PROJECT_ID, "", client=fake)
        assert refused.value.code == claim.CLAIM_REFUSED_NO_HOLDER
        assert fake.rpcs()[0][2]["p_holder"] == ""

    def test_a_lease_outside_the_bounds_reaches_the_database(self, fake):
        for offered in (0, -1, 86401):
            with pytest.raises(claim.ClaimRefused) as refused:
                claim.acquire_project_review_claim(
                    PROJECT_ID, "reviewer-sub-1", lease_seconds=offered, client=fake
                )
            assert refused.value.code == claim.CLAIM_REFUSED_LEASE
            assert fake.rpcs()[-1][2]["p_lease_seconds"] == offered

    def test_the_module_enforces_no_lease_bound_of_its_own(self):
        body = _code_only(MODULE_PATH).partition("def acquire_project_review_claim")[2]
        acquire = body.partition("def release_project_review_claim")[0]
        assert "86400" not in acquire, "the lease bound belongs to the database"
        assert "LEASE_BOUNDS_SECONDS" not in acquire

    def test_python_level_type_errors_are_type_errors_and_reach_no_database(self, fake):
        with pytest.raises(TypeError):
            claim.acquire_project_review_claim(None, "reviewer-sub-1", client=fake)
        with pytest.raises(TypeError):
            claim.acquire_project_review_claim(PROJECT_ID, None, client=fake)
        with pytest.raises(TypeError):
            claim.acquire_project_review_claim(
                PROJECT_ID, "reviewer-sub-1", lease_seconds="900", client=fake
            )
        with pytest.raises(TypeError):
            claim.acquire_project_review_claim(
                PROJECT_ID, "reviewer-sub-1", lease_seconds=True, client=fake
            )
        with pytest.raises(TypeError):
            claim.release_project_review_claim(PROJECT_ID, None, client=fake)
        assert fake.rpcs() == [], "a Python-level type error must never reach the database"

    def test_the_holder_is_required_and_can_never_be_defaulted(self):
        holder = inspect.signature(claim.acquire_project_review_claim).parameters["holder"]
        assert holder.default is inspect.Parameter.empty
        assert holder.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD

    def test_no_function_here_accepts_a_request_or_an_identity_payload(self):
        """The module is given a holder by a caller that has already authenticated it. It
        takes no request object, no token and no identity document it could read one from."""
        tree = ast.parse(MODULE_PATH.read_text())
        parameters = {
            argument.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            for argument in node.args.args + node.args.kwonlyargs
        }
        assert not (
            parameters & {"request", "body", "payload", "header", "headers", "claims"}
        ), parameters


# =============================================================================
# 5. THE BOUNDARIES
# =============================================================================
class TestTheBoundaries:
    def test_the_module_imports_nothing_from_this_application_at_module_level(self):
        tree = ast.parse(MODULE_PATH.read_text())
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        assert names == {"__future__", "json", "collections.abc", "dataclasses"}, names

    def test_its_code_names_no_part_of_the_system_it_does_not_own(self):
        code = _code_only(MODULE_PATH)
        for disowned in (
            "connection_review", "snapshot", "resolution", "fabrication", "cad_engine",
            "drawing", "pipeline", "review_ui", "fastapi", "httpx", "requests",
        ):
            assert disowned not in code, disowned

    def test_it_authorises_nothing_and_reads_no_project(self):
        """J43 Decision 3: the claim is acquired AFTER authentication and ownership
        authorization, and never as a substitute for either. That logic lives elsewhere and
        this module must not grow a copy of it."""
        code = _code_only(MODULE_PATH)
        assert "authorize" not in code
        assert "projects" not in code.replace("project_id", "").replace("p_project_id", "")
        names = {
            node.name
            for node in ast.parse(MODULE_PATH.read_text()).body
            if isinstance(node, ast.FunctionDef)
        }
        assert names == {
            "_client", "_text", "_data", "_error_fields", "_lease_from_hint",
            "_refusal_from", "_call", "acquire_project_review_claim",
            "release_project_review_claim",
        }, names

    def test_the_j22_store_does_not_import_the_claim_and_the_claim_does_not_import_it(self):
        store = (REPO / "app" / "engineering_data" / "connection_review_repository.py").read_text()
        assert "project_review_claim" not in store
        assert "connection_review_repository" not in MODULE_PATH.read_text()

    def test_no_route_was_added_and_j19_is_still_the_only_authorization_module(self, production):
        routes = {getattr(route, "path", None) for route in production.main.app.routes}
        assert not [path for path in routes if path and "claim" in path], routes
        rule = [
            str(path.relative_to(REPO))
            for path in sorted((REPO / "app").rglob("*.py"))
            if "def authorize_project" in path.read_text()
        ]
        assert rule == ["app/production_review/authorization.py"], rule

    def test_the_package_wiring_was_left_alone(self):
        """J44 adds ONE module. The package's `__init__` lists its modules explicitly and was
        deliberately not touched; naming this one there is a J45 change, and until then the
        module is imported by path. The gap is reported rather than papered over."""
        init = (REPO / "app" / "production_review" / "__init__.py").read_text()
        assert "project_review_claim" not in init
        assert "project_workflow_reconstruction" in init


# =============================================================================
# 6. THE LIVE DEPLOYMENT — READ-ONLY
# =============================================================================
@pytest.fixture(scope="module")
def live_client(production):
    """The real deployment's client. Skips — never substitutes — when the configuration is
    the test placeholder or the network is unavailable."""
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live-deployment tests "
                    "are skipped rather than run against a synthetic database")
    client = production.repository.supabase
    try:
        client.table("project_review_claims").select("*").execute()
    except Exception as exc:  # network/credentials unavailable in this process
        pytest.skip(f"the live claim table could not be read: {type(exc).__name__}")
    return client


@pytest.fixture(scope="module")
def live_project(live_client):
    rows = live_client.table("projects").select("id").limit(1).execute().data
    if not rows:
        pytest.skip("the live database has no project to name in a claim")
    return rows[0]["id"]


class TestTheLiveDeployment:
    """Every call below is a REFUSAL, chosen so that it cannot write. The claim table's
    `delete` and `truncate` are revoked — a committed claim row is permanent until its lease
    expires — so a live test that acquired a claim could not clean up after itself."""

    def test_the_claim_table_is_exposed_on_the_rest_surface_and_empty(self, live_client):
        assert live_client.table("project_review_claims").select("project_id").execute().data == []

    def test_an_unknown_project_is_refused_live_and_claims_nothing(self, live_client):
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.acquire_project_review_claim(
                str(uuid.uuid4()), "j44-live-probe", client=live_client
            )
        assert refused.value.code == claim.CLAIM_REFUSED_PROJECT_UNKNOWN
        assert live_client.table("project_review_claims").select("*").execute().data == []

    def test_an_empty_holder_is_refused_live_by_the_database(self, live_client, live_project):
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.acquire_project_review_claim(live_project, "", client=live_client)
        assert refused.value.code == claim.CLAIM_REFUSED_NO_HOLDER
        assert live_client.table("project_review_claims").select("*").execute().data == []

    def test_a_lease_outside_the_bounds_is_refused_live(self, live_client, live_project):
        for offered in (0, 86401):
            with pytest.raises(claim.ClaimRefused) as refused:
                claim.acquire_project_review_claim(
                    live_project, "j44-live-probe", lease_seconds=offered, client=live_client
                )
            assert refused.value.code == claim.CLAIM_REFUSED_LEASE
        assert live_client.table("project_review_claims").select("*").execute().data == []

    def test_releasing_an_unheld_project_is_refused_live_and_leaves_nothing(
        self, live_client, live_project
    ):
        with pytest.raises(claim.ClaimRefused) as refused:
            claim.release_project_review_claim(
                live_project, str(uuid.uuid4()), client=live_client
            )
        assert refused.value.code == claim.CLAIM_REFUSED_NOT_HOLDER
        assert live_client.table("project_review_claims").select("*").execute().data == []

    def test_the_service_role_cannot_delete_a_claim_live(self, live_client):
        """The revocation is enforced by the deployed database, not merely stated in the
        migration. The filter names a token that cannot exist, so even a database that
        permitted the delete would remove nothing."""
        impossible = "00000000-0000-0000-0000-000000000000"
        with pytest.raises(Exception) as denied:
            live_client.table("project_review_claims").delete().eq(
                "claim_token", impossible
            ).execute()
        assert getattr(denied.value, "code", None) == "42501", denied.value
        assert live_client.table("project_review_claims").select("*").execute().data == []
