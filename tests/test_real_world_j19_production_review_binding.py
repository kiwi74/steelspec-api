"""
J19 — PRODUCTION REVIEW BINDING.

J18 established the page-level exception surface — which pages of a drawing set
could not be read, and the one action a human may take on each — and bound it to
nothing: the review UI's session layer is a deliberately in-process slice with no
authentication, and nothing under `app/` ever loaded a real project into it. The
only way to read a page again was to know the route.

J19 binds that surface to production, through the identity and authorization this
application ALREADY has:

    GET  /production/review/{project_id}
        one project's page-level exceptions, read from its persisted record
    POST /production/review/{project_id}/pages/{page_number}/retry
        the existing J17 retry, for exactly that page of exactly that project

WHAT THIS FILE PROVES
---------------------
A. AUTHENTICATION — a request with no verifiable Supabase Auth access token reads
   nothing and mutates nothing, on the production review surface AND on the four
   project routes that already existed. A token this project did not issue, a
   token for another issuer or audience, a non-user token, a token with no subject
   and a token with no expiry are each refused, each by name.
B. AUTHORIZATION — an authenticated reviewer may reach a project exactly when the
   project's own `user_id` is their own subject: the rule the database already
   enforces in 37 RLS policies, restated in the process that bypasses RLS. No
   query parameter, no form field, no token claim and no client-supplied role
   changes it.
C. PROJECT BINDING — the production page is built from the project's persisted
   record through the EXISTING chain (J18 contract -> 7AM view -> the 7AN
   renderer), and is what the internal review UI renders, at another mount.
D. READ-ONLY LOAD — loading a project reads the project row and its drawing set
   and nothing else, performs no extraction, no AI call, no retry, no evidence
   write, no report and no status derivation, and leaves the persisted shape
   byte-identical.
E. J18 PAGE EXCEPTION SURFACE — `pages=12,37,44` are shown as three exceptions
   with their exact numbers preserved, in the record's own order, and a record
   that cannot be listed states why instead of listing nothing.
F. J17 RETRY HANDOFF — the retry route calls the EXISTING J17 authority for the
   addressed page of the bound project, and reports J17's own answer: an accepted
   request is stated as accepted, a refusal as its own code and reason.
G. J13 STATUS AUTHORITY — the status shown is the one J13 persisted, verbatim;
   J19 re-derives nothing and invents no blocker, even when the persisted status
   and the page's own record disagree.
H. CROSS-PROJECT ISOLATION — a project id cannot be substituted to escape
   authorization.
I. MULTI-USER ISOLATION — two reviewers' requests interleaved in one process each
   see their own project and only it: there is no process-global binding.
J. NO AUTOMATIC MUTATION — rendering is not an action, and the ONLY mutating path
   is the explicit per-page retry.
K. EXISTING REVIEW CONTRACT COMPATIBILITY — the internal 7AN/7AO surface is
   unchanged, and the production path is the same renderer over the same objects.
L. SCOPE GUARD — no second retry implementation, no second status source, no new
   table read, no migration, no new column, no AI, no extraction.

THE BOUNDARY
------------
Every test drives the GENUINE production path: the real `app.main` application
with its real dependency, the real authorization rule, the real read model, the
real J18 contract and renderer, and — where a retry is performed — the real
`plan_retry` and `retry_pdf_page` over a REAL PDF written by pypdf. Two external
stages are replaced, as they are throughout this suite: the AI call (a page-honest
stand-in) and the reference read behind the section matcher (the pinned index).
The one credential the HTTP layer reads is a real ES256 access token, signed by a
key generated for this session and published as a real JWKS document — see
`tests/production_review_auth.py`, which also states exactly which stage is
substituted and why.

Nothing here reaches the network, needs an AI key or writes production data. The
harness, the doubles and the fixtures are J4's, J16's and J17's, reused rather
than restated.
"""

from __future__ import annotations

import ast
import io
import itertools
import re
import tokenize
import types
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from yarl import URL

from tests import production_review_auth as auth
from tests import test_real_world_j16_pdf_continuation as j16
from tests import test_real_world_j17_parse_failure_retry as j17
from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "app" / "production_review"

COVERAGE_PREFIX = "PDF coverage: "
FAILURES_PREFIX = "PDF parse failures: "

# The brief's own interaction case: three pages of one drawing set whose response
# could not be read, inside a document large enough to need two windows.
PROJECT_PAGES = 45
FAILED_PAGES = (12, 37, 44)
OTHER_PAGE = 5

# The two routes J19 adds, and the four that already existed and now require the
# same credential.
PRODUCTION_SURFACE = "/production/review/{project_id}"
PRODUCTION_RETRY = "/production/review/{project_id}/pages/{page_number}/retry"
GATED_PROJECT_ROUTES = (
    "/extract/{project_id}",
    "/continue-extraction/{project_id}",
    "/retry-extraction/{project_id}",
    "/generate-report/{project_id}",
)

# The migrations that exist. J19 authored none — the authorization relationship already
# existed — and this list is pinned so that a migration appearing here unremarked fails this
# file rather than passing quietly, which is what happened when J22 added the fourth and again
# when J23 added the fifth. J23's capture table holds no owner column and no second
# authorization system: it is reached through the project's own rows, exactly as everything
# else here is, so the relationship this milestone documents is unchanged. J28 added the sixth
# by the same rule: its annotation-occurrence table holds no owner column either, is reached
# through `drawings` and `projects`, and adds no way to authorize anything — so the
# relationship this milestone documents is still unchanged. J44 added the seventh and J61 the
# eighth by that same rule, and neither holds an owner column either.
EXISTING_MIGRATIONS = (
    "20260924000000_j5_section_resolution_truth.sql",
    "20260924010000_j6_reference_data_identity.sql",
    "20260924020000_j8b_connection_plate_evidence_nullability.sql",
    "20260925000000_j22_connection_review_persistence.sql",
    "20260925010000_j23_page_extraction_captures.sql",
    "20260927000000_j28_pdf_annotation_occurrences.sql",
    # J44's, added by J44. It places one claim row keyed by project. It carries no owner, no
    # role and no membership, so the authorization relationship this milestone is about is
    # still exactly the one `projects.user_id` expresses.
    "20260928000000_j44_project_review_claims.sql",
    # J61's, added by J61 by the same rule: it gives a source document of a project an
    # identity and points a `drawings` row at it. It carries no owner, no role and no
    # membership, and it is reached through the project's own rows, so the authorization
    # relationship this milestone is about is still exactly `projects.user_id`.
    "20260929000000_j61_project_documents.sql",
    # J66's, added by J66 by the same rule: it records WHERE one field of one reviewed
    # connection came from. It carries no owner, no role and no membership of its own — its
    # citation hangs from the review item through a composite foreign key, and that item is
    # reached through the project's own rows — so the authorization relationship this
    # milestone is about is still exactly `projects.user_id`.
    "20260929010000_j66_field_evidence_citations.sql",
    "20261006000000_l19_selected_extraction_lineage.sql",
)


# ===========================================================================
# The harness — J16's document, J17's retry repository, one production client.
# ===========================================================================
@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    """Binds J16's page helpers to the real production `PageExtraction`.

    J16's own autouse fixture does this for its module only, so a test here that
    builds a page through J16's helpers must bind it for this module too — the same
    two lines J17's module writes for itself.
    """
    saved = j16._PAGE
    j16._PAGE = production.PageExtraction
    yield
    j16._PAGE = saved


@pytest.fixture()
def document(production, monkeypatch, tmp_path):
    """J16's document harness, called with J16's own collaborators.

    pytest resolves fixtures per module, so this file cannot simply request J16's
    `document`; it calls J16's function with J16's arguments. The harness IS J16's
    — one real PDF writer, one page-honest stand-in, one serving repository double,
    one pinned reference index — rather than a copy that could drift from it.
    """
    import app.validation.page_windows as windows_module

    return j17._fixture_function(j16.document)(production, windows_module, monkeypatch, tmp_path)


@pytest.fixture()
def project(document, monkeypatch):
    """J16's document harness with J17's one extra read, as J17 builds it."""
    monkeypatch.setattr(j16, "_ServingRepository", j17._RetryRepository)
    return document


@pytest.fixture()
def tokens(production, monkeypatch):
    """This session's ES256 key pair, installed as the application's verifier."""
    return auth.install(monkeypatch, production)


class _Storage:
    """The one storage call the retry task makes, answered from the document's own
    real bytes — never from the network.

    Since J37C-8Q the task reads through `app.storage_download`, which reaches the
    object endpoint through storage3's own bucket proxy rather than through
    `download()`. So this stands in for the proxy and the HTTP client under it — the
    substitution boundary is the NETWORK, not the download call — and the bytes and
    recorded paths are exactly what they were.
    """

    def __init__(self, owner):
        self.owner = owner
        self.id = "uploads"
        self._base_url = URL("https://storage.invalid/storage/v1/")
        self._headers = httpx.Headers({"Authorization": "Bearer test-not-a-real-key"})
        self._client = httpx.Client(transport=httpx.MockTransport(self._serve))

    def _serve(self, request):
        self.owner.paths.append(unquote(request.url.path.split("/object/uploads/", 1)[1]))
        return httpx.Response(200, content=self.owner.source_bytes)

    def from_(self, bucket):
        assert bucket == "uploads", bucket
        return self


class _ProjectQuery:
    """`supabase.table("projects").select(...).eq("id", …).single()` — the one read
    the HTTP layer performs itself, answered from the rows it was given."""

    def __init__(self, rows):
        self.rows = rows
        self.project_id = None

    def select(self, *columns):
        return self

    def eq(self, column, value):
        assert column == "id", column
        self.project_id = value
        return self

    def single(self):
        return self

    def execute(self):
        return j16._Result(self.rows.get(self.project_id))


class _ServiceClient:
    """A client that answers the project read and refuses every other call.

    The HTTP layer must not read members, connections or the drawing set itself:
    it loads the row it authorizes, and the read model loads the drawing set. Any
    other table read is an assertion failure, not a silently answered query.

    It serves EVERY project the test built, from the live rows those projects hold,
    because that is what the real client does — a client that could serve only one
    project could not tell "this request read its own project" from "this request
    read the only project there is".
    """

    def __init__(self, docs, *, source_bytes=None):
        self.rows = {doc.project_id: doc.repository.projects[doc.project_id] for doc in docs}
        self.source_bytes = source_bytes
        self.tables_read = []
        self.paths = []

    def table(self, name):
        self.tables_read.append(name)
        assert name == "projects", f"the HTTP layer read {name!r}"
        return _ProjectQuery(self.rows)

    @property
    def storage(self):
        return _Storage(self)


def _drawings_reader(stores):
    """`drawings_for_drawing_set`, answered by the store that OWNS the drawing set.

    Resolved by the drawing set's own id rather than by trying the stores in turn:
    a read for one project's drawing set can then never be answered out of
    another's, and a read for an id no project owns is a failure rather than an
    empty list, which would look exactly like a drawing set with no drawings.

    Each project is built with its own id space (see `_three_failures`), so an id
    owned by two projects is a harness collision and is raised as one rather than
    resolved to whichever store happened to be last.
    """
    owners = {}
    for store in stores.values():
        for row in store.drawing_sets.values():
            owners.setdefault(row["id"], []).append(store)

    def read(drawing_set_id):
        owning = owners[drawing_set_id]
        assert len(owning) == 1, (
            f"drawing set {drawing_set_id!r} is owned by {len(owning)} projects: "
            "the projects' id spaces are not disjoint"
        )
        return owning[0].drawings_for_drawing_set(drawing_set_id)

    return read


def _document_reader(stores):
    """`document_for_drawing`, answered by the store that OWNS the drawing.

    Milestone J61. The read model resolves a drawing's document through this one
    function, and the same discipline `_drawings_reader` states applies to it: the
    drawing's own id decides which project's store answers, so a request can never
    be served another project's document, and an id no project owns is a failure
    rather than an absent document — which would look exactly like a project whose
    drawing names no document at all.
    """
    owners = {}
    for store in stores.values():
        for row in store.drawings.values():
            owners.setdefault(row["id"], []).append(store)

    def read(drawing_id):
        owning = owners[drawing_id]
        assert len(owning) == 1, (
            f"drawing {drawing_id!r} is owned by {len(owning)} projects: "
            "the projects' id spaces are not disjoint"
        )
        return owning[0].document_for_drawing(drawing_id)

    return read


def _watch(store):
    """Records the project id every drawing-set read on `store` was made FOR.

    The store's own read log names the function that was called, not what it was
    called with, so two projects' reads are indistinguishable in it. This records
    the argument, which is what isolation is actually about.
    """
    seen = []
    read = store.drawing_sets_for_project

    def watched(project_id):
        seen.append(project_id)
        return read(project_id)

    store.drawing_sets_for_project = watched
    return seen


def _three_failures(project, monkeypatch, production, *, project_id="j19-project",
                    owner="j19-reviewer", source_name="j19-drawings.pdf", id_space=1):
    """One project whose committed record names three pages as unread.

    Two windows are read through the GENUINE production path — `parse_pdf_and_save`
    then `continue_pdf_extraction` — with the vision stage returning an unreadable
    response for exactly the brief's three pages. The result is the brief's own
    record shape: a document of 45 pages, all analysed, three of them failed, read
    in two windows.

    The project's owner is then set to `owner`, because ownership is the one fact
    the authorization rule reads and the fixture's own default is J16's.

    `id_space` is the first id the project's rows are numbered from. Each project
    gets its own range, because the doubles number rows from one per repository: two
    projects in one test would otherwise share a drawing-set id, and a read for one
    project's drawing set would be indistinguishable from a read for the other's.
    """
    doc = project(
        pages=PROJECT_PAGES, project_id=project_id, storage_path=f"{owner}/{source_name}",
    )
    doc.repository._ids = itertools.count(id_space)
    j17._install(doc, monkeypatch, production, failed=set(FAILED_PAGES))
    doc.first_window()
    doc.continue_window()
    doc.repository.projects[doc.project_id]["user_id"] = owner
    doc.owner = owner
    return doc


def _surface(production, monkeypatch, docs, tokens, *, user_id=None, source=False,
             real_retry=False):
    """The production surface under test: the real app, one authenticated client.

    `docs` is one project or several; the client and the read model serve all of
    them by id, so a request that reads a project other than its own is visible as
    a read of the wrong rows rather than impossible to express.

    `user_id` mints the token for someone OTHER than the first project's owner,
    which is how a cross-account request is expressed. `source` answers the retry
    task's storage read from the document's own bytes so a retry can actually be
    performed; `real_retry` leaves the retry task in place instead of recording it.
    """
    from fastapi.testclient import TestClient

    docs = [docs] if hasattr(docs, "project_id") else list(docs)
    first = docs[0]
    stores = {doc.project_id: doc.repository for doc in docs}

    monkeypatch.setattr(
        production.main, "supabase",
        _ServiceClient(docs, source_bytes=(Path(first.source).read_bytes() if source else None)),
    )
    # The read model's own reads, answered by each project's own store: exactly the
    # three repository functions it calls, and no fourth.
    monkeypatch.setattr(
        production.repository, "get_project",
        lambda pid: stores[pid].get_project(pid) if pid in stores else None,
    )
    monkeypatch.setattr(
        production.repository, "drawing_sets_for_project",
        lambda pid: stores[pid].drawing_sets_for_project(pid) if pid in stores else [],
    )
    monkeypatch.setattr(
        production.repository, "drawings_for_drawing_set",
        _drawings_reader(stores),
    )
    monkeypatch.setattr(
        production.repository, "document_for_drawing",
        _document_reader(stores),
    )
    reports = []
    monkeypatch.setattr(
        production.main, "build_and_store_report",
        lambda project_id, uid: reports.append(project_id),
    )
    dispatched = []
    if not real_retry:
        monkeypatch.setattr(
            production.main, "run_retry", lambda *args, **kwargs: dispatched.append(args),
        )

    owner = first.repository.projects[first.project_id]["user_id"]
    return types.SimpleNamespace(
        doc=first,
        docs=docs,
        owner=owner,
        url=f"/production/review/{first.project_id}",
        retry=lambda page: f"/production/review/{first.project_id}/pages/{page}/retry",
        path=lambda doc, page: f"/production/review/{doc.project_id}/pages/{page}/retry",
        get=lambda doc: f"/production/review/{doc.project_id}",
        rows=production.main.supabase.rows,
        client=production.main.supabase,
        http=TestClient(production.main.app, headers=tokens.headers(user_id or owner)),
        bare=TestClient(production.main.app),
        other=TestClient(production.main.app, headers=tokens.headers("some-other-reviewer")),
        dispatched=dispatched,
        reports=reports,
        tokens=tokens,
    )


def _record(doc):
    """The project's persisted record, as the modules that own it read it back."""
    return {
        "coverage_line": j17._lines(doc, COVERAGE_PREFIX)[0],
        "failures_line": j17._lines(doc, FAILURES_PREFIX)[0],
        "failures": j17._failures_record(doc),
        "status": doc.repository.projects[doc.project_id].get("status"),
    }


def _persisted(doc):
    """Everything a request must not move."""
    return {**j17._evidence_state(doc), **j17._run_state(doc)}


def _page_numbers(text):
    """The page numbers the surface states as unread, in the order it states them."""
    return [int(number) for number in re.findall(r"Unread page (\d+)", text)]


def _forms(text):
    """Every form target the page offers, in page order."""
    return re.findall(r'<form method="post" action="([^"]+)">', text)


def _read_model(doc, production=None):
    """The record the production read model produces for one project.

    Built by calling the read model with the row the HTTP layer authorizes and the
    store that serves it, so a test can render the same view the route renders
    without going through HTTP.
    """
    import app.production_review.project_read as read_module

    del production
    return read_module.read_project_record(
        doc.project_id,
        project_row=doc.repository.projects[doc.project_id],
        repository=doc.repository,
    )


def _blank(text: str) -> str:
    return "".join(" " if character != "\n" else "\n" for character in text)


def _code_only(path):
    """A module's source with docstrings, comments and string literals blanked.

    Docstrings are where this milestone EXPLAINS the records it reads and the
    vocabulary it refuses to own, so scanning raw source would flag the prose. Only
    what the module actually executes is scanned.
    """
    source = Path(path).read_text()
    out = list(source.splitlines(keepends=True))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        (start_row, start_col), (end_row, end_col) = token.start, token.end
        if start_row == end_row:
            line = out[start_row - 1]
            out[start_row - 1] = (
                line[:start_col] + _blank(line[start_col:end_col]) + line[end_col:]
            )
            continue
        for row in range(start_row, end_row + 1):
            line = out[row - 1]
            if row == start_row:
                out[row - 1] = line[:start_col] + _blank(line[start_col:])
            elif row == end_row:
                out[row - 1] = _blank(line[:end_col]) + line[end_col:]
            else:
                out[row - 1] = _blank(line)
    return "".join(out)


# ===========================================================================
# A. AUTHENTICATION — who is calling.
# ===========================================================================
class TestAuthentication:
    def test_the_production_routes_are_registered(self, production):
        paths = {route.path for route in production.main.app.routes}

        assert PRODUCTION_SURFACE in paths
        assert PRODUCTION_RETRY in paths

    def test_a_request_with_no_credential_reads_nothing(
        self, project, production, monkeypatch, tokens,
    ):
        """1. An unauthenticated GET cannot access a production project — and does
        not read one either: the refusal precedes the project load."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.bare.get(surface.url)

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_NO_TOKEN"
        assert surface.client.tables_read == []

    def test_an_unauthenticated_retry_mutates_nothing(
        self, project, production, monkeypatch, tokens,
    ):
        """2. No unauthenticated caller can trigger the production retry — on the
        new route or on the route that has always existed."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        before = _persisted(doc)

        on_new = surface.bare.post(surface.retry(37))
        on_old = surface.bare.post(f"/retry-extraction/{doc.project_id}?page=37")

        for response in (on_new, on_old):
            assert response.status_code == 401
            assert response.json()["detail"]["refusal"] == "IDENTITY_NO_TOKEN"
        assert surface.dispatched == []
        assert surface.client.paths == []
        assert surface.client.tables_read == []
        assert _persisted(doc) == before

    @pytest.mark.parametrize("path", GATED_PROJECT_ROUTES)
    def test_every_project_route_requires_a_credential(self, path, production):
        """The four routes that already existed require the same credential: J19
        left no route through which an unauthenticated caller reaches a project."""
        from fastapi.testclient import TestClient

        response = TestClient(production.main.app).post(
            path.format(project_id="any-project")
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_NO_TOKEN"

    @pytest.mark.parametrize(
        "authorization,code",
        (
            ("", "IDENTITY_NO_TOKEN"),
            ("   ", "IDENTITY_NO_TOKEN"),
            ("Basic dXNlcjpwYXNz", "IDENTITY_MALFORMED_TOKEN"),
            ("sometoken", "IDENTITY_MALFORMED_TOKEN"),
            ("Bearer", "IDENTITY_MALFORMED_TOKEN"),
            ("Bearer one two", "IDENTITY_MALFORMED_TOKEN"),
            ("Bearer a,b", "IDENTITY_MALFORMED_TOKEN"),
        ),
    )
    def test_a_header_that_is_not_one_bearer_token_is_refused(
        self, authorization, code, project, production, monkeypatch, tokens,
    ):
        """The header is read strictly: a second credential, a bare token or a
        comma-joined list cannot be smuggled past a caller that read the first."""
        from fastapi.testclient import TestClient

        doc = _three_failures(project, monkeypatch, production)

        response = TestClient(production.main.app).get(
            f"/production/review/{doc.project_id}", headers={"Authorization": authorization},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == code

    def test_a_token_this_project_did_not_issue_is_refused(
        self, project, production, monkeypatch, tokens,
    ):
        """A well-formed ES256 token signed by a key this project does not publish
        does not verify: the key set it names has no such key."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.get(
            surface.url, headers={"Authorization": f"Bearer {tokens.foreign_token(doc.owner)}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_UNVERIFIED"

    def test_an_unsigned_token_is_refused(self, project, production, monkeypatch, tokens):
        """`alg: none` is not an algorithm this application will verify."""
        import jwt

        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        claims = tokens.claims(doc.owner)
        claims.pop("exp", None)
        unsigned = jwt.encode(claims, key=None, algorithm="none")

        response = surface.http.get(
            surface.url, headers={"Authorization": f"Bearer {unsigned}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] in {
            "IDENTITY_UNVERIFIED", "IDENTITY_MALFORMED_TOKEN",
        }

    def test_an_hmac_token_signed_with_the_public_key_is_refused(
        self, project, production, monkeypatch, tokens,
    ):
        """The classic algorithm-confusion forgery: an HS256 token whose HMAC secret
        is the PUBLIC KEY the project publishes, which a verifier that chose its
        algorithm from the token's own header would accept.

        The token is assembled by hand, because PyJWT itself refuses to sign with an
        asymmetric key as an HMAC secret — so `jwt.encode` cannot express the
        forgery. An attacker holding only the public key can, and this is what they
        would produce.
        """
        import base64
        import hashlib
        import hmac
        import json

        from cryptography.hazmat.primitives import serialization

        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        public_pem = tokens.keys.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def segment(value):
            raw = json.dumps(value, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

        signing_input = f"{segment({'alg': 'HS256', 'typ': 'JWT', 'kid': tokens.keys.kid})}." \
                        f"{segment(tokens.claims(doc.owner))}"
        signature = base64.urlsafe_b64encode(
            hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        forged = f"{signing_input}.{signature}"

        response = surface.http.get(
            surface.url, headers={"Authorization": f"Bearer {forged}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_UNVERIFIED"

    @pytest.mark.parametrize(
        "overrides,code",
        (
            ({"iss": "https://another-project.supabase.co/auth/v1"}, "IDENTITY_WRONG_ISSUER"),
            ({"aud": "service_role"}, "IDENTITY_WRONG_AUDIENCE"),
            ({"role": "service_role"}, "IDENTITY_NOT_A_USER_TOKEN"),
            ({"role": "anon"}, "IDENTITY_NOT_A_USER_TOKEN"),
            ({"exp": "tomorrow"}, "IDENTITY_UNVERIFIED"),
        ),
    )
    def test_a_token_that_is_not_this_applications_user_token_is_refused(
        self, overrides, code, project, production, monkeypatch, tokens,
    ):
        """Each refusal is by name.

        A non-integer expiry is refused by the verifier's own required-claim rule
        before the application's own expiry check can see it, so the refusal here is
        the verifier's: a claim of the right name with a value no clock can compare
        is not an expiry.
        """
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.get(
            surface.url,
            headers={"Authorization": f"Bearer {tokens.token(doc.owner, **overrides)}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == code

    def test_a_token_with_no_subject_is_refused(self, project, production, monkeypatch, tokens):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.get(
            surface.url,
            headers={"Authorization": f"Bearer {tokens.token(doc.owner, drop=('sub',))}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_NO_SUBJECT"

    def test_a_token_with_no_expiry_is_refused(self, project, production, monkeypatch, tokens):
        """A credential that never expires is not one this application acts on —
        refused by the verifier's own required-claim rule, not by a claim check."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.get(
            surface.url,
            headers={"Authorization": f"Bearer {tokens.token(doc.owner, drop=('exp',))}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_UNVERIFIED"

    def test_an_expired_token_is_refused(self, project, production, monkeypatch, tokens):
        import time

        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        expired = tokens.token(doc.owner, exp=int(time.time()) - 60)

        response = surface.http.get(
            surface.url, headers={"Authorization": f"Bearer {expired}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_UNVERIFIED"

    def test_a_refusal_never_echoes_the_credential(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        foreign = tokens.foreign_token(doc.owner)

        body = surface.http.get(
            surface.url, headers={"Authorization": f"Bearer {foreign}"},
        ).text

        assert foreign not in body
        assert foreign.split(".")[0] not in body


# ===========================================================================
# B. AUTHORIZATION — which project.
# ===========================================================================
class TestAuthorization:
    def test_the_owner_reaches_their_own_project(self, project, production, monkeypatch, tokens):
        """4. An authenticated, authorized reviewer can access the intended project."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.get(surface.url)

        assert response.status_code == 200
        assert doc.project_id in response.text

    def test_another_accounts_project_is_refused_and_its_record_is_not_read(
        self, project, production, monkeypatch, tokens,
    ):
        """3. An authenticated reviewer cannot reach another account's project.

        The row is loaded — the rule compares the row's owner against the token's
        subject, so there is nothing to compare without it — and NOTHING else is:
        the drawing set the record lives in is never read."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens, user_id="another-account")
        doc.repository.reads.clear()

        response = surface.http.get(surface.url)

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["refusal"] == "ACCESS_NOT_OWNER"
        assert doc.owner not in detail["reason"]
        assert surface.client.tables_read == ["projects"]
        assert doc.repository.reads == []

    def test_the_refusal_is_the_same_for_a_retry(self, project, production, monkeypatch, tokens):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens, user_id="another-account")
        before = _persisted(doc)

        response = surface.http.post(surface.retry(37))

        assert response.status_code == 403
        assert response.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"
        assert surface.dispatched == []
        assert _persisted(doc) == before

    def test_an_unowned_project_is_not_an_owned_one(self, project, production, monkeypatch, tokens):
        """An unrecorded owner is not an ownerless project: with nothing to compare,
        the rule refuses rather than admits."""
        doc = _three_failures(project, monkeypatch, production)
        doc.repository.projects[doc.project_id]["user_id"] = None
        surface = _surface(production, monkeypatch, doc, tokens, user_id="some-reviewer")

        response = surface.http.get(surface.url)

        assert response.status_code == 403
        assert response.json()["detail"]["refusal"] == "ACCESS_PROJECT_OWNER_UNKNOWN"

    def test_an_unknown_project_is_a_404(self, project, production, monkeypatch, tokens):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.get("/production/review/no-such-project")

        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"

    def test_a_client_supplied_role_cannot_grant_access(
        self, project, production, monkeypatch, tokens,
    ):
        """10. No client-supplied role grants access. A token may claim anything it
        likes about itself; the rule reads the PROJECT's owner, not the token."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens, user_id="another-account")
        forger = tokens.token(
            "another-account",
            app_metadata={"role": "admin", "provider": "email"},
            user_metadata={"role": "owner", "plan": "enterprise"},
            owner_user_id=doc.owner,
            owner=doc.owner,
            project_owner=doc.owner,
        )

        response = surface.http.get(surface.url, headers={"Authorization": f"Bearer {forger}"})

        assert response.status_code == 403
        assert response.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"

    def test_no_query_parameter_can_grant_access(self, project, production, monkeypatch, tokens):
        """A project id in the query string does not change WHICH project is read:
        the path is the subject and the rule is applied to the loaded row."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens, user_id="another-account")

        response = surface.http.get(
            surface.url,
            params={"project_id": doc.project_id, "user_id": doc.owner,
                    "owner": doc.owner, "role": "owner", "authenticated": "true"},
        )

        assert response.status_code == 403
        assert response.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"

    def test_no_form_field_can_grant_access(self, project, production, monkeypatch, tokens):
        """11. No hidden field grants access, and no field is read at all: the retry
        route takes its project from the path and its page from the path."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens, user_id="another-account")

        response = surface.http.post(
            surface.retry(37),
            data={"project_id": doc.project_id, "user_id": doc.owner,
                  "reviewer": doc.owner, "page_number": "37", "role": "owner"},
        )

        assert response.status_code == 403
        assert surface.dispatched == []

    def test_the_rule_is_the_projects_own_user_id(self, project, production, monkeypatch, tokens):
        """The rule is `auth.uid() = projects.user_id`. A token whose subject IS the
        owner is admitted with no claim beyond Supabase's own; a token that merely
        CLAIMS the owner while being someone else is refused."""
        doc = _three_failures(project, monkeypatch, production, owner="the-real-owner")
        surface = _surface(production, monkeypatch, doc, tokens)

        allowed = surface.http.get(surface.url)
        refused = surface.http.get(
            surface.url,
            headers={
                "Authorization": f"Bearer {tokens.token('someone-else', owner='the-real-owner')}"
            },
        )

        assert allowed.status_code == 200
        assert refused.status_code == 403
        assert refused.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"


# ===========================================================================
# C. PROJECT BINDING — the existing chain, over production state.
# ===========================================================================
class TestProjectBinding:
    def test_the_production_page_is_the_existing_surface(
        self, project, production, monkeypatch, tokens,
    ):
        """The production page is what the EXISTING chain produces from the same
        record: J18's contract -> 7AM's view -> 7AN's renderer. There is no
        production review UI."""
        import app.cad_engine.page_exception_view_model as view_module
        import app.review_ui.render as render_module

        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        expected = render_module.render_page_exceptions_page(
            view_module.render_page_exception_list_view(
                _read_model(doc).page_exception_contract()
            ),
            action_prefix=f"/production/review/{doc.project_id}",
        )

        response = surface.http.get(surface.url)

        assert response.status_code == 200
        assert response.text == expected

    def test_the_production_page_is_the_internal_page_at_another_mount(
        self, project, production, monkeypatch, tokens,
    ):
        """Substituting the production mount for the internal one turns the
        production page into the internal one, exactly: the two surfaces differ by
        where they are mounted and by nothing else."""
        import app.cad_engine.page_exception_view_model as view_module
        import app.review_ui.render as render_module

        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        prefix = f"/production/review/{doc.project_id}"
        internal = render_module.render_page_exceptions_page(
            view_module.render_page_exception_list_view(
                _read_model(doc).page_exception_contract()
            ),
        )

        unmounted = surface.http.get(surface.url).text
        unmounted = unmounted.replace(f'action="{prefix}/pages/', 'action="/pages/')
        unmounted = unmounted.replace(f'href="{prefix}"', 'href="/"')

        assert unmounted == internal

    def test_the_binding_refuses_a_request_with_no_identity(
        self, project, production, monkeypatch,
    ):
        import app.production_review.authorization as rules
        import app.production_review.binding as binding_module

        doc = _three_failures(project, monkeypatch, production)
        row = doc.repository.projects[doc.project_id]

        with pytest.raises(binding_module.BindingRefused) as refused:
            binding_module.bind_project_review(
                identity=None, record=_read_model(doc),
                decision=rules.authorize_project(None, row),
            )

        assert refused.value.code == "BINDING_UNIDENTIFIED"

    def test_the_binding_refuses_a_record_no_decision_allowed(
        self, project, production, monkeypatch, tokens,
    ):
        """A binding is proof-carrying: an unauthorized caller has nothing to bind,
        so calling the binder directly does not produce one either."""
        import app.production_review.authorization as rules
        import app.production_review.binding as binding_module

        doc = _three_failures(project, monkeypatch, production)
        row = doc.repository.projects[doc.project_id]
        identity = _identity(tokens, "another-account")

        with pytest.raises(binding_module.BindingRefused) as refused:
            binding_module.bind_project_review(
                identity=identity, record=_read_model(doc),
                decision=rules.authorize_project(identity, row),
            )

        assert refused.value.code == "BINDING_NOT_AUTHORIZED"

    def test_a_binding_cannot_carry_two_project_identities(
        self, project, production, monkeypatch, tokens,
    ):
        import app.production_review.authorization as rules
        import app.production_review.binding as binding_module

        doc = _three_failures(project, monkeypatch, production)
        wrong = rules.AccessDecision(
            allowed=True, code=rules.ACCESS_ALLOWED, detail="", project_id="a-different-project",
        )

        with pytest.raises(binding_module.BindingRefused) as refused:
            binding_module.bind_project_review(
                identity=_identity(tokens, doc.owner), record=_read_model(doc), decision=wrong,
            )

        assert refused.value.code == "BINDING_WRONG_PROJECT"


def _identity(tokens, user_id):
    """The identity a real token for `user_id` establishes, through the real rule."""
    import app.production_review.identity as identity_module

    return identity_module.reviewer_from_authorization(
        f"Bearer {tokens.token(user_id)}", verifier=tokens.verifier, issuer=tokens.issuer,
    )


# ===========================================================================
# D. READ-ONLY LOAD — loading a project is not an action.
# ===========================================================================
class TestTheLoadIsReadOnly:
    def test_loading_the_page_reads_the_record_and_nothing_else(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        doc.repository.reads.clear()

        response = surface.http.get(surface.url)

        assert response.status_code == 200
        # The HTTP surface reads the project row it authorizes and no other table.
        assert surface.client.tables_read == ["projects"]
        # The read model reads the project's drawing sets, their drawings, and — since
        # J61 — the document each drawing names, through the repository's own existing
        # reads and no others. The third read is not a new dependency of the read model
        # so much as the other half of the second: a drawing now carries a `document_id`
        # and the record states the document it stands for, which is the whole point of
        # the milestone. It is made only for a drawing that itself names a document.
        assert sorted(doc.repository.reads) == [
            "document_for_drawing", "drawing_sets_for_project", "drawings_for_drawing_set",
        ]

    def test_loading_the_page_changes_nothing(self, project, production, monkeypatch, tokens):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        before = _persisted(doc)
        calls = len(doc.analyzer_calls)

        for _ in range(3):
            assert surface.http.get(surface.url).status_code == 200

        assert _persisted(doc) == before
        assert len(doc.analyzer_calls) == calls
        assert surface.dispatched == []
        assert surface.client.paths == []
        assert surface.reports == []

    def test_loading_the_page_does_not_derive_a_project_status(
        self, project, production, monkeypatch, tokens,
    ):
        """J13 is the sole status authority, and J19 does not consult it: the page
        shows the status J13 already persisted. Deriving one here would be a second
        status source, so a derivation that cannot run proves it is absent."""
        import app.validation.project_status as status_module

        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        def forbidden(*args, **kwargs):
            raise AssertionError("the review load derived a project status")

        monkeypatch.setattr(status_module, "derive_project_status", forbidden)

        text = surface.http.get(surface.url).text

        assert f"status = {_record(doc)['status']}" in text

    def test_loading_the_page_twice_states_the_same_thing(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        first = surface.http.get(surface.url).text
        second = surface.http.get(surface.url).text

        assert first == second


# ===========================================================================
# E. THE J18 PAGE-EXCEPTION SURFACE, over production state.
# ===========================================================================
class TestThePageExceptionSurface:
    def test_the_three_unread_pages_are_shown_with_their_exact_numbers(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert _page_numbers(text) == list(FAILED_PAGES)
        assert "3 unread page(s)" in text

    def test_the_record_is_stated_verbatim(self, project, production, monkeypatch, tokens):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        record = _record(doc)

        text = surface.http.get(surface.url).text

        assert record["coverage_line"] in text
        assert record["failures_line"] in text
        assert f"total={PROJECT_PAGES} analysed={PROJECT_PAGES} parse_failed=3" in text

    def test_the_only_control_is_one_retry_per_unread_page(
        self, project, production, monkeypatch, tokens,
    ):
        """The production page offers the same one action the internal page offers,
        for the same pages, named for the same page — and no project-level retry."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert _forms(text) == [surface.retry(page) for page in FAILED_PAGES]
        assert "Retry page 37" in text

    def test_the_back_link_names_the_production_surface(
        self, project, production, monkeypatch, tokens,
    ):
        """A page served from the production mount links back into it, not to a
        route this surface does not own."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert f'href="/production/review/{doc.project_id}"' in text
        assert 'href="/"' not in text

    def test_a_record_with_no_coverage_states_why_and_lists_no_page(
        self, project, production, monkeypatch, tokens,
    ):
        """A record the contract cannot list is stated as such, with J16's own code
        — never as an empty list, which would present half a record as a fact."""
        doc = _three_failures(project, monkeypatch, production)
        doc.repository.projects[doc.project_id]["warnings"] = []
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert "CONTINUATION_NO_COVERAGE_RECORD" in text
        assert _page_numbers(text) == []
        assert _forms(text) == []

    def test_a_record_that_predates_page_identity_states_why(
        self, project, production, monkeypatch, tokens,
    ):
        """A coverage record that counts failures without naming them keeps the
        project out of done and names no page — and the surface says exactly that,
        with J17's own code for it."""
        doc = _three_failures(project, monkeypatch, production)
        doc.repository.projects[doc.project_id]["warnings"] = [
            f"{COVERAGE_PREFIX}total={PROJECT_PAGES} analysed={PROJECT_PAGES} "
            "parse_failed=2 not_analysed=0",
        ]
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert "RETRY_FAILURES_UNRECORDED" in text
        assert _page_numbers(text) == []


# ===========================================================================
# F. THE J17 RETRY HANDOFF — one page, one authority.
# ===========================================================================
class TestTheRetryHandoff:
    def test_a_failed_page_is_handed_to_the_existing_retry_for_that_project(
        self, project, production, monkeypatch, tokens,
    ):
        """7. The retry targets exactly the bound project, and the page it names."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.post(surface.retry(37))

        assert response.status_code == 200
        assert len(surface.dispatched) == 1
        args = surface.dispatched[0]
        assert args[0] == doc.project_id
        assert args[1] == doc.repository.projects[doc.project_id]["uploaded_file_path"]
        assert args[2] == "PDF"
        assert args[3] == doc.owner
        assert args[-1] == 37

    def test_an_accepted_request_is_stated_as_accepted_not_as_a_success(
        self, project, production, monkeypatch, tokens,
    ):
        """The retry is dispatched, so nothing has been read yet. The page says
        that, and states the record it already held."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.post(surface.retry(37)).text

        assert "Retry started" in text
        assert "Result state: retry_started" in text
        assert _page_numbers(text) == list(FAILED_PAGES)

    def test_a_page_the_record_does_not_name_is_refused_by_j17s_own_code(
        self, project, production, monkeypatch, tokens,
    ):
        """The refusal is J17's, stated verbatim, and nothing was dispatched."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        before = _persisted(doc)

        text = surface.http.post(surface.retry(OTHER_PAGE)).text

        assert "RETRY_PAGE_NOT_FAILED" in text
        assert surface.dispatched == []
        assert _persisted(doc) == before
        assert _page_numbers(text) == list(FAILED_PAGES)

    def test_a_page_past_the_document_is_refused(self, project, production, monkeypatch, tokens):
        """9. A page number cannot escape J17's validation: a page the document does
        not have is refused by J17, and 0 is not a page either."""
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        beyond = surface.http.post(surface.retry(PROJECT_PAGES + 1))
        zero = surface.http.post(surface.retry(0))

        assert "RETRY_PAGE_BEYOND_DOCUMENT" in beyond.text
        assert "RETRY_PAGE_MALFORMED" in zero.text
        assert surface.dispatched == []

    def test_a_page_that_is_not_a_number_is_rejected_before_the_handler(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.post(f"/production/review/{doc.project_id}/pages/four/retry")

        assert response.status_code == 422
        assert surface.dispatched == []

    def test_a_project_that_is_not_a_pdf_is_refused_before_anything_is_queued(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        doc.repository.projects[doc.project_id]["source_format"] = "DXF"
        surface = _surface(production, monkeypatch, doc, tokens)

        response = surface.http.post(surface.retry(37))

        assert response.status_code == 400
        assert surface.dispatched == []

    def test_the_retry_of_another_projects_page_cannot_be_reached(
        self, project, production, monkeypatch, tokens,
    ):
        """8. A project id cannot be substituted in the retry request to escape
        authorization: the path's project is authorized again on this request."""
        first = _three_failures(
            project, monkeypatch, production, project_id="j19-first", owner="j19-owner-a",
            id_space=1000,
        )
        second = _three_failures(
            project, monkeypatch, production, project_id="j19-second", owner="j19-owner-b",
            source_name="j19-other.pdf", id_space=2000,
        )
        surface = _surface(production, monkeypatch, [first, second], tokens)

        response = surface.http.post(surface.path(second, 37))

        assert response.status_code == 403
        assert response.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"
        assert surface.dispatched == []

    def test_a_resolved_retry_leaves_the_other_two_pages_unread(
        self, project, production, monkeypatch, tokens,
    ):
        """The brief's own case: after a successful retry, page 37 stops being an
        outstanding failure while 12 and 44 remain.

        The retry is performed by the GENUINE background task — the real
        `run_retry`, the real `retry_pdf_page`, the real reconciliation and the real
        failure persistence — over the real PDF.
        """
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens, source=True, real_retry=True)

        assert _page_numbers(surface.http.get(surface.url).text) == list(FAILED_PAGES)

        accepted = surface.http.post(surface.retry(37))

        assert accepted.status_code == 200
        assert "Retry started" in accepted.text
        # The task has run by the time the client returns: page 37 was read again.
        assert j17._failures_record(doc).page_numbers == (12, 44)
        assert surface.reports == [doc.project_id]
        assert doc.repository.projects[doc.project_id]["status"] == "review"

        assert _page_numbers(surface.http.get(surface.url).text) == [12, 44]

    def test_a_retry_that_failed_again_leaves_every_page_as_it_was(
        self, project, production, monkeypatch, tokens,
    ):
        """A failed retry is not a terminal state and not a success: the page stays
        named as failed, its neighbours are untouched, and no report is written."""
        doc = _three_failures(project, monkeypatch, production, project_id="j19-still-failing")
        j17._install(doc, monkeypatch, production, fails_again={37})
        surface = _surface(production, monkeypatch, doc, tokens, source=True, real_retry=True)

        response = surface.http.post(surface.retry(37))

        assert response.status_code == 200
        assert j17._failures_record(doc).page_numbers == FAILED_PAGES
        assert surface.reports == []
        assert _page_numbers(surface.http.get(surface.url).text) == list(FAILED_PAGES)

    def test_the_retry_is_dispatched_only_when_explicitly_requested(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        for _ in range(3):
            surface.http.get(surface.url)

        assert surface.dispatched == []


# ===========================================================================
# G. J13 IS THE STATUS AUTHORITY.
# ===========================================================================
class TestTheStatusAuthority:
    def test_the_status_shown_is_the_one_j13_persisted(
        self, project, production, monkeypatch, tokens,
    ):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert doc.repository.projects[doc.project_id]["status"] == "review"
        assert "Needs review" in text
        assert "status = review" in text

    def test_a_done_project_is_not_given_blockers_it_does_not_have(
        self, project, production, monkeypatch, tokens,
    ):
        """If J13 says the project is done, the surface says so. J19 does not
        re-derive a status, disagree with it, or invent a blocker from what the page
        happens to be showing."""
        doc = _three_failures(project, monkeypatch, production)
        doc.repository.projects[doc.project_id]["status"] = "done"
        surface = _surface(production, monkeypatch, doc, tokens)

        text = surface.http.get(surface.url).text

        assert "status = done" in text
        assert "Needs review" not in text

    def test_the_status_is_never_written(self, project, production, monkeypatch, tokens):
        doc = _three_failures(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, doc, tokens)
        before = _persisted(doc)

        surface.http.get(surface.url)
        surface.http.post(surface.retry(OTHER_PAGE))

        assert _persisted(doc) == before
        assert doc.repository.projects[doc.project_id]["status"] == "review"


# ===========================================================================
# H. CROSS-PROJECT ISOLATION.  I. MULTI-USER ISOLATION.
# ===========================================================================
class TestIsolation:
    def _pair(self, project, monkeypatch, production):
        return (
            _three_failures(
                project, monkeypatch, production, project_id="j19-project-a",
                owner="j19-owner-a", source_name="a.pdf", id_space=1000,
            ),
            _three_failures(
                project, monkeypatch, production, project_id="j19-project-b",
                owner="j19-owner-b", source_name="b.pdf", id_space=2000,
            ),
        )

    def test_a_project_id_cannot_be_substituted_to_escape_authorization(
        self, project, production, monkeypatch, tokens,
    ):
        first, second = self._pair(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, [first, second], tokens)
        second.repository.reads.clear()

        refused = surface.http.get(surface.get(second))

        assert refused.status_code == 403
        assert refused.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"
        assert second.repository.reads == []

    def test_each_reviewer_sees_their_own_project(
        self, project, production, monkeypatch, tokens,
    ):
        first, second = self._pair(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, [first, second], tokens)

        mine = surface.http.get(surface.get(first))
        theirs = surface.http.get(
            surface.get(second),
            headers={"Authorization": f"Bearer {tokens.token('j19-owner-b')}"},
        )

        assert mine.status_code == 200
        assert first.project_id in mine.text
        assert second.project_id not in mine.text
        assert theirs.status_code == 200
        assert second.project_id in theirs.text
        assert first.project_id not in theirs.text

    def test_two_reviewers_requests_interleaved_do_not_share_a_binding(
        self, project, production, monkeypatch, tokens,
    ):
        """12. No binding survives across projects or users. Both projects are
        served by the same process and the same application object, with one request
        of each interleaved; each response states its own project and its own
        record, and A's page is identical before and after B was served."""
        first, second = self._pair(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, [first, second], tokens)
        theirs = {"Authorization": f"Bearer {tokens.token('j19-owner-b')}"}
        a_asked = _watch(first.repository)
        b_asked = _watch(second.repository)
        # The reads the window extraction made while building these projects are
        # not part of this question: what is asserted is what each REVIEW request
        # read.
        first.repository.reads.clear()
        second.repository.reads.clear()

        a_one = surface.http.get(surface.get(first)).text
        b_one = surface.http.get(surface.get(second), headers=theirs).text
        a_two = surface.http.get(surface.get(first)).text

        assert a_one == a_two
        assert first.project_id in a_one
        assert second.project_id in b_one
        assert first.project_id not in b_one
        # Each request read its OWN project's drawing set, and no request read the
        # other project's: the id each read was made for is what is asserted, not
        # merely that a read happened.
        assert a_asked == [first.project_id, first.project_id]
        assert b_asked == [second.project_id]
        # And each page stated its own record, read through the existing reads only —
        # the third being J61's document hop, which is made against the same store the
        # drawing was read from (see `_document_reader`), so each request still answered
        # entirely out of its own project.
        assert sorted(set(first.repository.reads)) == [
            "document_for_drawing", "drawing_sets_for_project", "drawings_for_drawing_set",
        ]
        assert sorted(set(second.repository.reads)) == [
            "document_for_drawing", "drawing_sets_for_project", "drawings_for_drawing_set",
        ]

    def test_a_third_reviewer_sees_neither_project(self, project, production, monkeypatch, tokens):
        first, second = self._pair(project, monkeypatch, production)
        surface = _surface(production, monkeypatch, [first, second], tokens)

        for doc in (first, second):
            response = surface.other.get(surface.get(doc))
            assert response.status_code == 403
            assert response.json()["detail"]["refusal"] == "ACCESS_NOT_OWNER"


# ===========================================================================
# K. THE EXISTING REVIEW CONTRACT AND UI ARE UNCHANGED.
# ===========================================================================
class TestTheExistingSurfaceIsIntact:
    def test_the_internal_review_ui_still_renders_its_own_routes(self, production):
        """The 7AN/7AO surface is unchanged: with no prefix its retry control and its
        back link name the routes it has always named."""
        import app.cad_engine.page_exception_contract as contract_module
        import app.cad_engine.page_exception_view_model as view_module
        import app.review_ui.render as render_module
        from app.validation.page_coverage import PageCoverage
        from app.validation.parse_failures import failures_of

        contract = contract_module.build_page_exception_contract(
            project_id="internal-project",
            coverage=PageCoverage(
                total_pages=10, analysed_pages=10, parse_failed_pages=1, not_analysed_pages=0,
            ),
            failures=failures_of((7,)),
            project_status="review",
        )

        text = render_module.render_page_exceptions_page(
            view_module.render_page_exception_list_view(contract),
        )

        assert _forms(text) == ["/pages/7/retry"]
        assert 'href="/"' in text

    def test_the_production_path_adds_no_field_to_the_view(self, project, production, monkeypatch):
        """The production surface renders the EXISTING view: no field was added to it
        for production, and the view the production page renders is the view the
        internal page renders."""
        import app.cad_engine.page_exception_view_model as view_module

        doc = _three_failures(project, monkeypatch, production)

        view = view_module.render_page_exception_list_view(
            _read_model(doc).page_exception_contract()
        )

        assert set(view.__dataclass_fields__) == {
            "project_id", "project_status", "status_label", "summary", "coverage_line",
            "failures_line", "record_refusal", "record_refusal_message", "exceptions",
            "notice",
        }

    def test_there_is_exactly_one_page_exception_renderer(self):
        """The production page is rendered by the renderer that already existed."""
        import app.review_ui.render as render_module

        source = Path(render_module.__file__).read_text()

        assert source.count("def render_page_exceptions_page") == 1

    def test_there_is_exactly_one_retry_interpretation(self):
        """The production surface and the internal review surface both call the ONE
        interpretation of a retry's result, so the two cannot drift apart: the
        function is defined once and called from `submit_page_retry` alone."""
        import app.review_ui.session as session_module

        source = Path(session_module.__file__).read_text()

        assert source.count("def render_page_retry_outcome") == 1
        assert source.count("render_page_retry_outcome(") == 2


# ===========================================================================
# L. SCOPE — what this milestone did not do.
# ===========================================================================
class TestScopeGuard:
    def test_no_second_retry_implementation_exists(self):
        """J19's retry route calls the existing J17 authority. Nothing in the
        production review package reads a page, decides a page's admissibility or
        writes a failure record."""
        for path in sorted(PACKAGE.glob("*.py")):
            code = _code_only(path)
            for forbidden in ("analyze_pdf_pages", "retry_pdf_page", "plan_retry",
                              "check_retry_request", "failures_of(", "PageCoverage(",
                              "page_count_of", "retry_notice("):
                assert forbidden not in code, f"{path.name} contains {forbidden}"

    def test_the_application_holds_exactly_one_retry_dispatch(self):
        """`retry_pdf_page` is called in exactly one place in the application, and it
        is the place it has always been: the background retry task."""
        code = _code_only(REPO / "app" / "main.py")

        assert code.count("retry_pdf_page(") == 1
        assert "analyze_pdf_pages" not in code
        assert "check_retry_request" not in code

    def test_no_second_status_source_exists(self):
        """The package reads no member, connection or review-item row, and derives no
        status: J13 stays the only authority, and the status shown is the persisted
        one."""
        vocabulary = ("derive_project_status", "review_status", "steel_members",
                      "review_items", "COMPLETE_REVIEW_STATUSES", "plan_tier")
        for path in sorted(PACKAGE.glob("*.py")):
            code = _code_only(path)
            for forbidden in vocabulary:
                assert forbidden not in code, f"{path.name} contains {forbidden}"

    def test_no_new_table_or_column_was_invented(self):
        """No authorization column, no membership table, no role: the rule is the one
        the schema already had.

        The names below are the shapes a reviewer/project relationship would need —
        an organization, a membership, a role, a join table. The package names none
        of them, because it needs none of them: `projects.user_id` was already the
        relationship.
        """
        for path in sorted(PACKAGE.glob("*.py")):
            code = _code_only(path)
            for forbidden in ("insert(", "update(", "upsert(", "delete("):
                assert forbidden not in code, f"{path.name} writes with {forbidden}"
            for invented in ("organization_id", "role_id", "membership",
                             "project_members", "project_owners", "profiles",
                             "invitation", "rbac"):
                assert invented not in code, f"{path.name} names {invented}"

    def test_no_migration_was_authored(self):
        """The authorization relationship already existed. No schema change was made
        for J19 — and if one ever is without this list being updated, this test says
        so rather than passing silently. J44's migration is on the list for exactly
        that reason: it was added here when it was written, and the authorization
        scan below still passes over it."""
        migrations = sorted(
            path.name for path in (REPO / "supabase" / "migrations").glob("*.sql")
        )

        assert migrations == sorted(EXISTING_MIGRATIONS)
        for path in (REPO / "supabase").rglob("*.sql"):
            text = path.read_text()
            assert "J19" not in text
            assert "production_review" not in text

    def test_the_read_model_writes_no_sql_and_holds_no_client(self):
        """The read model calls the existing repository functions; it holds no client
        and writes no query of its own."""
        code = _code_only(PACKAGE / "project_read.py")

        for forbidden in (".select(", "table(", "supabase", "execute("):
            assert forbidden not in code, forbidden
        for existing_read in ("get_project", "drawing_sets_for_project",
                              "drawings_for_drawing_set"):
            assert existing_read in code, existing_read

    def test_the_production_review_modules_hold_no_process_global_binding(self):
        """A binding is per request: the package assigns no module-level mutable
        state, so two requests cannot see each other's project."""
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(Path(path).read_text())
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    dunder = target.id.startswith("__") and target.id.endswith("__")
                    assert target.id.isupper() or dunder, (
                        f"{path.name} assigns module state {target.id!r}"
                    )

    def test_the_package_reaches_no_model_a_file_or_the_network(self):
        """Nothing in the package calls a model, opens a drawing, fetches a URL or
        touches storage. `storage_path` is a column the read model reads — the point
        is that it never CALLS storage, so what is forbidden is the access, not the
        name."""
        for path in sorted(PACKAGE.glob("*.py")):
            code = _code_only(path)
            for forbidden in ("anthropic", "openai", "pdf2image", "subprocess",
                              "requests.", "httpx", "pypdf", "fitz", "storage.",
                              ".storage", "from_(", "download(", "upload("):
                assert forbidden not in code, f"{path.name} contains {forbidden}"


# ===========================================================================
# The package's own contract: no engine, no globals, no invention.
# ===========================================================================
class _EmptyRepository:
    """A repository with no drawing sets — for the read model's own tests."""

    def get_project(self, project_id):
        return None

    def drawing_sets_for_project(self, project_id):
        return []

    def drawings_for_drawing_set(self, drawing_set_id):
        return []


class TestThePackageContract:
    def test_the_package_exposes_its_modules(self):
        """The four boundaries J19 established, plus J24A's revision-0 producer.

        J19 pinned four names because four boundaries are what it established: who the
        caller is, which project they may see, what that project's record states, and the
        contract chain. J24A added a fifth module to this package — the read seam that
        reconstructs a project's initial 7AJ workflow from the persisted AI capture — and
        it is an addition beside those four, not a change to any of them: identity,
        authorization, read and binding are untouched by it. The pin is extended here
        rather than relaxed to a subset, so a sixth name still has to be declared.
        """
        import app.production_review as package

        assert set(package.__all__) == {
            "authorization",
            "binding",
            "identity",
            "project_read",
            # J24A: reconstruct a project's revision-0 workflow from persisted capture.
            "project_workflow_reconstruction",
            # J46: resume that workflow from the revisions J22 persisted, without the
            # process that produced them. Declared here, not appended quietly.
            "project_workflow_resumption",
        }

    def test_the_jwks_url_is_derived_from_the_configured_project_url(self, production):
        """Verification uses the project's own published keys at the URL its own
        configuration implies — never a hand-typed host."""
        from app.production_review.identity import supabase_issuer, supabase_jwks_url

        base = production.main.SUPABASE_URL

        assert supabase_issuer(base) == f"{base.rstrip('/')}/auth/v1"
        assert supabase_jwks_url(base) == f"{base.rstrip('/')}/auth/v1/.well-known/jwks.json"
        assert supabase_jwks_url(base) == production.main.supabase_jwks_url(base)

    def test_the_verifier_refuses_a_symmetric_algorithm(self):
        """A verifier that would accept an HMAC algorithm is a verifier that can be
        forged with its own public key, so the algorithm set is not configurable into
        one."""
        from app.production_review.identity import JwksTokenVerifier

        for algorithm in ("HS256", "none", "hs512", "HS1"):
            with pytest.raises(ValueError):
                JwksTokenVerifier(
                    "https://example.supabase.co/auth/v1/.well-known/jwks.json",
                    algorithms=(algorithm,),
                )

    def test_the_verifier_verifies_only_the_projects_own_algorithm(self):
        from app.production_review.identity import ALGORITHMS, JwksTokenVerifier

        verifier = JwksTokenVerifier(
            "https://example.supabase.co/auth/v1/.well-known/jwks.json"
        )

        assert ALGORITHMS == ("ES256",)
        assert verifier.algorithms == ("ES256",)

    def test_the_bearer_reader_takes_exactly_one_credential(self):
        from app.production_review.identity import bearer_token

        assert bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
        assert bearer_token("bearer abc.def.ghi") == "abc.def.ghi"
        for value in (None, "", "abc.def.ghi", "Basic abc", "Bearer a b", "Bearer a,b",
                      "Bearer", "   "):
            assert bearer_token(value) is None

    def test_the_read_model_states_a_missing_half_as_missing(self):
        """A project with no coverage record states no coverage — it does not state a
        complete one, and it does not raise."""
        from app.production_review.project_read import read_project_record

        record = read_project_record(
            "p", project_row={"id": "p", "status": "review", "warnings": None},
            repository=_EmptyRepository(),
        )

        assert record.coverage is None
        assert record.failures is None
        assert record.page_exception_contract().exceptions == ()
        assert record.project_status == "review"

    def test_the_read_model_refuses_a_warnings_value_that_is_not_lines(self):
        """A `warnings` value that is not a list of lines states no warnings: the
        readers are never handed something to iterate character by character."""
        from app.production_review.project_read import read_project_record

        record = read_project_record(
            "p",
            project_row={
                "id": "p", "status": "review",
                "warnings": f"{COVERAGE_PREFIX}total=1 analysed=1 parse_failed=0 not_analysed=0",
            },
            repository=_EmptyRepository(),
        )

        assert record.warnings == ()
        assert record.coverage is None

    def test_the_read_model_states_the_drawing_set_it_read(
        self, project, production, monkeypatch,
    ):
        """The record's drawing set is the project's own, read through the
        repository's existing read and stated with the columns that read names."""
        doc = _three_failures(project, monkeypatch, production)

        record = _read_model(doc)

        assert len(record.drawing_sets) == 1
        drawing_set = record.drawing_sets[0]
        assert drawing_set.total_pages == PROJECT_PAGES
        assert drawing_set.pages_analysed == PROJECT_PAGES
        assert len(drawing_set.drawings) == 1
        assert drawing_set.drawings[0].storage_path == doc.storage_path
