"""
STAGE 7K-G — THE LEGACY REVIEW UI IS NOT REACHABLE UNLESS A DEPLOYMENT ASKS FOR IT.

WHAT THIS FILE IS

`/review` is the 7AN review UI: an in-process slice with NO authentication whose six routes
sit outside the J19 boundary every `/production/review/...` route passes through. It is
inert only because nothing in the service ever binds a session to it — a property of what is
CURRENTLY CALLED, not of the routes themselves. This file proves the gate that replaces that
accident with a rule: the mount exists only when a deployment explicitly opts in, and the
safe state is the DEFAULT one.

WHAT IS REAL, AND WHAT IS ONLY CONFIGURED

    REAL     `app.main`, its route table, its middleware, the mount decision, and the real
             request handling of both the legacy paths and the authenticated ones.

    SYNTHETIC the configuration VALUES. The application is imported under a placeholder
             Supabase URL and key so the repository's own `.env` is neither read nor
             needed, and every `app.*` module is put back exactly as it was found.

SELF-CONTAINED ON PURPOSE. This module imports nothing but the standard library, pytest and
Starlette, and carries its own synthetic-configuration context manager and its own
no-network guard. It is part of a narrowly scoped security release and must not depend on
test helpers that live outside it.

WHAT THIS FILE DOES NOT CLAIM

Nothing here says the DEPLOYED process has the opt-in unset; that is a property of Railway's
environment, which is not read here. What is proven is that this application does not mount
the surface unless it is asked to, and that the authenticated review routes are identical
either way.

No outbound connection is made: `TestClient` drives the app over an in-process ASGI
transport, and the guard below refuses any socket the process might otherwise open.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Synthetic configuration: a placeholder URL and a JWT-shaped non-secret, the same
#: published values `tests/test_review_ui_integration.py` already uses. They grant access to
#: nothing; they exist so `app.config`'s import-time reads succeed.
PLACEHOLDER_URL = "https://placeholder.supabase.co"
PLACEHOLDER_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ.fake-test-signature"
)

_REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
_OPT_IN_KEY = "REVIEW_UI_ENABLED"

#: The six routes `app/review_ui/web.py` declares, by the address a caller would use. Each
#: is a genuine request, not a string: "not registered" is asserted by asking for it.
LEGACY_ROUTES = (
    ("GET", "/review/"),
    ("GET", "/review/connections/CN-1"),
    ("POST", "/review/connections/CN-1/resolve"),
    ("POST", "/review/refresh"),
    ("GET", "/review/pages"),
    ("POST", "/review/pages/1/retry"),
)

#: The deployed opt-in value. It is the ONLY one that enables the mount.
ENABLING_VALUE = "true"

#: Values that must NOT enable it. A permissive truthiness test would enable the surface on
#: several of these — which is the whole reason the check is an equality against one value.
NON_ENABLING_VALUES = ("", " ", "1", "0", "yes", "on", "false", "no", "enable", "TRUE!")

#: Paths under the authenticated boundary, asserted explicitly so that "unchanged" means a
#: named route with a named method rather than merely "the same set as before".
AUTHENTICATED_ROUTES = (
    ("GET", "/production/review/{project_id}/review"),
    ("POST", "/production/review/{project_id}/open"),
    ("POST", "/production/review/{project_id}/connections/{package_id}/resolve"),
    ("GET", "/production/review/{project_id}/workflow"),
)

PROJECT = "11111111-2222-4333-8444-555555555555"


class NetworkAccessAttempted(BaseException):
    """A test tried to reach a live service.

    Deliberately NOT an `Exception`: the application's read paths treat a failed read as an
    engineering state and swallow `Exception` into a placeholder value, so an Exception-based
    guard could be absorbed by the very code it guards.
    """


def _deny_outbound_connection(monkeypatch):
    """Refuses every outbound connection attempt made by this test."""
    import socket

    def _refuse(*_args, **_kwargs):
        raise NetworkAccessAttempted(
            "a test attempted an outbound connection; the live service must be doubled"
        )

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _app_modules() -> list[str]:
    return [name for name in sys.modules if name == "app" or name.startswith("app.")]


@contextlib.contextmanager
def application_under(review_ui_enabled):
    """`app.main` imported with the opt-in set to `review_ui_enabled`.

    `None` means the variable is ABSENT — the state of a deployment that never sets it, and
    the state this milestone is about.

    The order is the guarantee: snapshot, EVICT every cached `app.*` module (so the import
    cannot inherit an application built under a different answer), OVERWRITE the two
    required keys with placeholders (never `setdefault`, which would leave a real value in
    place), set or clear the opt-in, and only then import. Teardown restores the module
    objects and the environment exactly, including removing keys that were absent before,
    so nothing is left for another test to inherit.
    """
    saved_env = {key: os.environ.get(key) for key in _REQUIRED_ENV + (_OPT_IN_KEY,)}
    saved_modules = {name: sys.modules[name] for name in _app_modules()}
    for name in saved_modules:
        del sys.modules[name]
    os.environ["SUPABASE_URL"] = PLACEHOLDER_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = PLACEHOLDER_KEY
    if review_ui_enabled is None:
        os.environ.pop(_OPT_IN_KEY, None)
    else:
        os.environ[_OPT_IN_KEY] = review_ui_enabled
    try:
        import app.config as config
        import app.main as main

        yield main.app, config
    finally:
        for name in _app_modules():
            del sys.modules[name]
        sys.modules.update(saved_modules)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture()
def app_without_the_flag():
    """The application as a deployment that never opted in gets it."""
    with application_under(None) as (application, _config):
        yield application


@pytest.fixture()
def app_with_the_flag():
    """The application as a deployment that set `REVIEW_UI_ENABLED=true` gets it."""
    with application_under(ENABLING_VALUE) as (application, _config):
        yield application


@pytest.fixture(autouse=True)
def _no_outbound_connection(monkeypatch):
    """Whatever a test here is asked to do, it may not open a socket."""
    _deny_outbound_connection(monkeypatch)


def _mount_paths(application) -> list[str]:
    return [route.path for route in application.routes if isinstance(route, Mount)]


def _declared(application) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in application.routes
        for method in (getattr(route, "methods", None) or ())
    }


# ======================================================================================
# A. DISABLED BY DEFAULT — the state a deployment gets without asking
# ======================================================================================
class TestDisabledByDefault:
    def test_the_legacy_application_is_not_mounted(self, app_without_the_flag):
        assert _mount_paths(app_without_the_flag) == []

    def test_the_review_path_is_not_found(self, app_without_the_flag):
        assert TestClient(app_without_the_flag).get("/review/").status_code == 404

    @pytest.mark.parametrize("method,path", LEGACY_ROUTES, ids=[p for _, p in LEGACY_ROUTES])
    def test_no_legacy_route_is_reachable(self, app_without_the_flag, method, path):
        """Each of the six is asked for by its real address. A 404 for every one of them is
        the property that matters: not "the UI refuses", but "there is nothing here"."""
        response = TestClient(app_without_the_flag).request(method, path)
        assert response.status_code == 404

    @pytest.mark.parametrize("value", NON_ENABLING_VALUES)
    def test_a_value_that_is_not_the_opt_in_leaves_it_disabled(self, value):
        with application_under(value) as (application, _config):
            assert _mount_paths(application) == []
            assert TestClient(application).get("/review/").status_code == 404


# ======================================================================================
# B. THE EXPLICIT OPT-IN — what a deployment that asks for it gets
# ======================================================================================
class TestExplicitOptIn:
    def test_the_legacy_application_is_mounted(self, app_with_the_flag):
        assert _mount_paths(app_with_the_flag) == ["/review"]

    @pytest.mark.parametrize("method,path", LEGACY_ROUTES, ids=[p for _, p in LEGACY_ROUTES])
    def test_every_legacy_route_is_reachable_again(self, app_with_the_flag, method, path):
        """All six exist once the opt-in is stated. Each answers with the session layer's
        own "nothing is bound" page — which is the second half of the guarantee: opting in
        makes the surface REACHABLE, and nothing else makes it bound."""
        response = TestClient(app_with_the_flag).request(method, path)
        assert response.status_code == 200

    @pytest.mark.parametrize("value", (ENABLING_VALUE, "TRUE", " true ", "TrUe"))
    def test_the_opt_in_is_read_case_insensitively_and_trimmed(self, value):
        with application_under(value) as (application, _config):
            assert _mount_paths(application) == ["/review"]


# ======================================================================================
# C. THE AUTHENTICATED REVIEW ROUTES ARE NOT AFFECTED
#
# The mount and the J19 boundary are independent: `/production/review/...` is declared on
# `app` itself, not on the sub-application.
# ======================================================================================
class TestTheAuthenticatedRoutesAreUnaffected:
    @pytest.mark.parametrize("method,path", AUTHENTICATED_ROUTES)
    def test_the_production_route_is_registered_without_the_flag(
        self, app_without_the_flag, method, path
    ):
        assert (method, path) in _declared(app_without_the_flag)

    @pytest.mark.parametrize("method,path", AUTHENTICATED_ROUTES)
    def test_the_production_route_is_registered_with_the_flag(
        self, app_with_the_flag, method, path
    ):
        assert (method, path) in _declared(app_with_the_flag)

    def test_the_flag_changes_nothing_under_the_production_boundary(self):
        """Every route whose path is under `/production/` is identical either way — the
        mount adds a route and alters nothing that already existed."""
        with application_under(None) as (off, _c):
            without = {r for r in _declared(off) if r[1].startswith("/production/")}
        with application_under(ENABLING_VALUE) as (on, _c):
            with_flag = {r for r in _declared(on) if r[1].startswith("/production/")}
        assert without == with_flag
        assert without, "the production boundary must not be empty, or this proves nothing"

    def test_the_read_route_still_requires_a_credential_without_the_flag(
        self, app_without_the_flag
    ):
        """Registered is not the same as reachable: the J19 boundary is what stands in front
        of it, and the gate does not touch it."""
        response = TestClient(app_without_the_flag).get(f"/production/review/{PROJECT}/review")
        assert response.status_code == 401


# ======================================================================================
# D. NO CONFIGURATION IS LEFT BEHIND
# ======================================================================================
class TestNothingIsLeftBehind:
    def test_the_opt_in_does_not_leak_into_the_environment(self):
        """The import decision is made from the environment, so a test that sets it must put
        it back — otherwise a later module would inherit an opt-in it never asked for."""
        before = os.environ.get(_OPT_IN_KEY)
        with application_under(ENABLING_VALUE):
            assert os.environ[_OPT_IN_KEY] == ENABLING_VALUE
        assert os.environ.get(_OPT_IN_KEY) == before

    def test_a_module_imported_under_the_flag_is_not_left_cached(self):
        """An application module built with the mount would otherwise be handed to whatever
        imports `app.main` next, and the gate would depend on test order."""
        before = {name: sys.modules[name] for name in _app_modules()}
        with application_under(ENABLING_VALUE):
            pass
        assert {name: sys.modules[name] for name in _app_modules()} == before


# ======================================================================================
# E. THE DECISIVE FORM — A GENUINELY CLEAN PROCESS
#
# Everything above reasons within one interpreter, where a module could in principle be left
# cached. This spawns a fresh one with nothing monkeypatched: the application exactly as a
# deployment that never opted in gets it, and the authenticated read route present beside it
# so the absence is demonstrably a gate rather than a broken import.
# ======================================================================================
def test_a_clean_interpreter_without_the_opt_in_has_no_mount():
    script = (
        "import os\n"
        f"os.environ['SUPABASE_URL'] = {PLACEHOLDER_URL!r}\n"
        f"os.environ['SUPABASE_SERVICE_ROLE_KEY'] = {PLACEHOLDER_KEY!r}\n"
        "os.environ.pop('REVIEW_UI_ENABLED', None)\n"
        "from app.main import app\n"
        "paths = {r.path for r in app.routes}\n"
        "assert '/health' in paths, paths\n"
        "assert '/production/review/{project_id}/review' in paths, paths\n"
        "assert '/review' not in paths, paths\n"
        "print('7KG-NO-MOUNT-OK')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "7KG-NO-MOUNT-OK" in completed.stdout
