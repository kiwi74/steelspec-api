"""
J18 — PDF PAGE-LEVEL EXCEPTION SURFACE.

J17 gave a page whose response could not be read an IDENTITY and a way to read
exactly that page again, and left the capability where it was: a route.

    PDF coverage: total=60 analysed=60 parse_failed=1 not_analysed=0
    PDF parse failures: pages=37
    POST /retry-extraction/{project_id}?page=37

J18 puts that capability where a human looks, as an EXPLICIT ACTION and nothing
else:

    GET  /pages                  every page this drawing set's committed record
                                 names as unread — one exception per page, each
                                 carrying the page number the RECORD states
    POST /pages/{page}/retry     a request to read THAT page again, through the
                                 existing J17 retry contract

WHAT THIS FILE PROVES
---------------------
* A page exception is projected from the committed record: one page is one
  exception, three pages are three independent ones, and every page number comes
  from the record itself — never from a count, an index or an ordering.
* The page's STATE is the retry contract's own: `retryable` where J17 accepts the
  page, and otherwise the code J17 would refuse it with, stated with J17's own
  reason — so the surface cannot offer an action the retry would then refuse for
  a reason already decidable from the record.
* A record that cannot be listed is not listed PARTIALLY: a missing half, or two
  halves that disagree, states a code the milestone that owns it already uses and
  carries no exceptions.
* RENDERING THE SURFACE DOES NOTHING. Loading it, refreshing it and re-rendering
  it read no page, make no AI call, write no evidence and issue no retry — proved
  against a vision stage, a page-count reader and a retry contract that all fail
  loudly if they are reached.
* The human action targets the bound project and the addressed page, reads exactly
  that page, and performs the retry through the GENUINE J17 contract — this
  milestone contains no retry algorithm at all (structurally: the session cannot
  import the pipeline, and the retry entry is injected).
* Several failed pages stay independent: retrying one moves that one, leaves the
  others listed and their evidence untouched.
* A retry that fails again leaves its page listed and retryable, a request that
  was only accepted is stated as accepted, and a refusal is stated as the code and
  reason J17 refused with — never as "retry started" and never as success.
* J13 remains the only project-status authority: the surface states the status it
  is given and the status J17's own result carries, and derives nothing.
* J16's continuation contract, J17's vocabulary, the schema and the requirements
  are untouched, and nothing here decides an engineering question.

THE BOUNDARY
------------
The record, the refusal and the read are the GENUINE production ones. The harness
is J17's — which is J16's, which is J13's and J4's: a real PDF written by pypdf,
the real `page_count_of`, the real `SectionMatcher` over the pinned reference
index, the real validation, row construction and J13 derivation, with only the
two external stages (the AI call and the reference read) replaced. This file adds
ONE double of its own — the retry CONTRACT handed to the surface — and that
double only records its calls and delegates to `app.pipeline.retry_pdf_page`
through J17's own `doc.retry`, so the read it performs is the production read.

Nothing here reaches the network, needs an AI key, or writes production data.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import re
import tokenize
from collections import Counter

import pytest

from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j16_pdf_continuation as j16
from tests import test_real_world_j17_parse_failure_retry as j17
from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production

REPO = j13.REPO
DONE = j13.DONE
REVIEW = j13.REVIEW
MIGRATIONS = j13.MIGRATIONS

SESSION_PATH = REPO / "app" / "review_ui" / "session.py"
WEB_PATH = REPO / "app" / "review_ui" / "web.py"
RENDER_PATH = REPO / "app" / "review_ui" / "render.py"
CONTRACT_PATH = REPO / "app" / "cad_engine" / "page_exception_contract.py"
VIEW_MODEL_PATH = REPO / "app" / "cad_engine" / "page_exception_view_model.py"

SHORT_PAGES = j17.SHORT_PAGES
FAILED_PAGE = j17.FAILED_PAGE

# The surface's own state string, and the one `app/main.py` reports for a retry
# request it merely accepted.
RETRYABLE = "retryable"
RETRY_STARTED = "retry_started"

# The three codes the surface can state about a record it cannot list, each
# imported by the contract from the milestone that already refuses that state.
RECORD_REFUSAL_CODES = (
    "CONTINUATION_NO_COVERAGE_RECORD",
    "RETRY_FAILURES_UNRECORDED",
    "RETRY_FAILURES_CONFLICT",
)

# The banner a rendered result carries, as the full class attribute `render.py`
# writes it. Matching the attribute (rather than the bare word) is what makes a
# NEGATIVE assertion meaningful: the stylesheet defines all three tones on every
# page, so the barest substring test would never fail.
SUCCESS_BANNER = 'class="card success-banner banner"'
FAIL_BANNER = 'class="card fail-banner banner"'
ATTENTION_BANNER = 'class="card attention-banner banner"'

# What the surface's own modules must never contain. Read against CODE only (see
# `_code_only`): a docstring that says "no Supabase client here" is not one, and a
# probe that could not tell the difference would push the modules to stop
# explaining themselves.
FORBIDDEN_IN_PURE_MODULES = (
    "fastapi", "anthropic", "supabase", "httpx", "requests", "urllib",
    "subprocess", "socket", "threading", "asyncio", "backgroundtasks",
    "psycopg", "sqlalchemy", "difflib", "rapidfuzz", "fuzz", "levenshtein",
    "getenv", "environ", "open(", ".write(", "uuid4", "random.", "time.time",
    "pypdf", "pdf_vision", "app.pipeline",
    "plan_retry", "retry_pdf_page", "page_count_of",
    "analyze_pdf_pages", "check_page_window", "continue_pdf_extraction",
)
# (`check_retry_request` is deliberately NOT in that list: the contract's whole
# point is that J17's own decision function is what decides a page's state. What
# must not happen is DEFINING one here, which this file checks elsewhere.)

# The engineering chain: a page-exception surface that reached any of these would
# be deciding something a human or another milestone owns.
FORBIDDEN_ENGINEERING_MODULES = (
    "app.cad_engine.automation_gate",
    "app.cad_engine.automation_pipeline",
    "app.cad_engine.drawing_dispatch",
    "app.cad_engine.fabrication_output_gate",
    "app.cad_engine.fabrication_package",
    "app.cad_engine.project_automation",
    "app.cad_engine.exception_resolution",
    "app.cad_engine.project_workflow",
    "app.pipeline",
)

TWO_FAILED = (2, 4)
THREE_FAILED = (2, 4, 7)


# ===========================================================================
# Reading source the way a purity claim needs it read.
# ===========================================================================
def _code_only(path) -> str:
    """A module's source with comments and string literals blanked out.

    A scan for "does this module import a database client" must look at what the
    module DOES. The prose of a module that states its own boundaries names the
    things it refuses to touch, so scanning the raw text would punish a module for
    documenting itself — and the fix would be to delete the explanation, which is
    the wrong direction. (The review UI's own purity test splits its source the
    same way, for the same reason.)
    """
    source = path.read_text(encoding="utf-8")
    lines = [list(line) for line in source.splitlines()]
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (start_row, start_col), (end_row, end_col) = token.start, token.end
        for row in range(start_row, end_row + 1):
            line = lines[row - 1]
            first = start_col if row == start_row else 0
            last = end_col if row == end_row else len(line)
            for index in range(first, min(last, len(line))):
                line[index] = " "
    return "\n".join("".join(line) for line in lines)


def _imports(path) -> set[str]:
    imported = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def _section(path, marker) -> str:
    """A module from one of its milestone markers onward.

    `session.py` and `render.py` both carry the 7AK connection chain as well as
    this milestone's surface, and the connection chain's vocabulary (a resolve
    control, a grade field, an exception) is not this surface's. Reading this
    surface's own section is what makes "it names no engineering field" a claim
    about the surface rather than about the module it shares.
    """
    source = path.read_text(encoding="utf-8")
    assert marker in source, marker
    return source.split(marker, 1)[1]


def _j18_session_section() -> str:
    return _section(SESSION_PATH, "# J18 — the page-exception surface")


def _j18_render_section() -> str:
    return _section(RENDER_PATH, "# The page-exception surface (J18)")


def _still_present(before, after) -> None:
    """Asserts every row that existed before still exists, unchanged, in `after`.

    Row by row and counted, rather than looked up by an id: the repository doubles
    key their rows the way their table does, and a review-item row's identity is
    its own pair of columns, not an `id` every double assigns. What is being
    claimed is only this: nothing that already existed was rewritten, resolved or
    re-statused by the surface. Rows the retry ADDS are its own evidence, and are
    not this helper's business.
    """
    remaining = Counter(row_key(row) for row in after)
    for row in before:
        key = row_key(row)
        assert remaining[key] > 0, row
        remaining[key] -= 1


def row_key(row):
    return json.dumps(row, sort_keys=True, default=str)


# ===========================================================================
# The harness: J17's, driven exactly as J17 drives it.
# ===========================================================================
@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    """Binds J16's page helpers to the real production `PageExtraction`, which
    J16's own autouse fixture does only for its own module."""
    saved = j16._PAGE
    j16._PAGE = production.PageExtraction
    yield
    j16._PAGE = saved


@pytest.fixture()
def parse_failures(production):
    import app.validation.parse_failures as module

    return module


@pytest.fixture()
def coverage(production):
    import app.validation.page_coverage as module

    return module


@pytest.fixture()
def contract_module(production):
    import app.cad_engine.page_exception_contract as module

    return module


@pytest.fixture()
def view_model(production):
    import app.cad_engine.page_exception_view_model as module

    return module


@pytest.fixture()
def session(production):
    """The review session layer, imported inside the boundary like every other app
    module this file touches."""
    import app.review_ui.session as module

    return module


@pytest.fixture()
def document(production, monkeypatch, tmp_path):
    return j17._fixture_function(j17.document)(production, monkeypatch, tmp_path)


@pytest.fixture()
def project(document, production, parse_failures, monkeypatch):
    """J17's project harness: J16's document plus J17's own retry entry points."""
    return j17._fixture_function(j17.project)(
        document, production, parse_failures, monkeypatch,
    )


@pytest.fixture(autouse=True)
def _clear_surface(session):
    """No test inherits another's binding: the session is in-process state."""
    session.clear_page_exceptions()
    yield
    session.clear_page_exceptions()


class _RetryContract:
    """The retry contract the surface is handed.

    This is the ONLY double this file adds. It records the page it was asked for
    and delegates to the GENUINE production retry through J17's own wiring — so
    the validation, the read, the reconciliation and the refusals a test observes
    are J17's, and the surface's own contribution to them is exactly "it called
    this, with one page number".
    """

    def __init__(self, doc):
        self.doc = doc
        self.calls = []

    def __call__(self, page_number):
        self.calls.append(page_number)
        return self.doc.retry(page_number=page_number)


def _prepare(doc, monkeypatch, production, *, failed=(FAILED_PAGE,), **kwargs):
    """Installs the vision stage and reads the first window through the genuine
    run, so the record under test is one production wrote."""
    reads = j17._install(doc, monkeypatch, production, failed=set(failed), **kwargs)
    doc.first_window()
    return reads


def _bind(session, doc, retry_entry=None, project_status=None):
    """Binds the surface to this project's own committed record."""
    session.bind_page_exceptions(
        project_id=doc.project_id,
        warnings=j17._warnings(doc),
        project_status=(
            project_status if project_status is not None
            else doc.repository.projects[doc.project_id]["status"]
        ),
        retry_entry=retry_entry if retry_entry is not None else (lambda page_number: None),
    )


def _client():
    from fastapi.testclient import TestClient

    import app.review_ui.web as web_module

    return TestClient(web_module.review_app)


def _build(contract_module, *, coverage, failures, project_id="p-1", project_status=REVIEW):
    return contract_module.build_page_exception_contract(
        project_id=project_id, coverage=coverage, failures=failures,
        project_status=project_status,
    )


def _retry_targets(text):
    """Every retry target the rendered page offers, in page order."""
    return re.findall(r'<form method="post" action="/pages/(\d+)/retry">', text)


def _unread_numbers(text):
    """The page numbers the rendered page states as unread, in page order."""
    return re.findall(r"Unread page (\d+)", text)


# ===========================================================================
# A. The page-exception projection.
# ===========================================================================
class TestThePageExceptionProjection:
    def test_one_failed_page_is_one_exception_with_its_own_number(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        contract = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 1, 0),
            failures=parse_failures.failures_of([37]),
        )
        view = view_model.render_page_exception_list_view(contract)

        assert contract.record_refusal is None
        assert [e.page_number for e in contract.exceptions] == [37]
        assert len(view.exceptions) == 1
        assert view.exceptions[0].page_number == 37
        assert view.exceptions[0].state == RETRYABLE
        assert view.exceptions[0].state_label == "Awaiting retry"
        assert [a.action for a in view.exceptions[0].actions] == ["RETRY_PAGE"]
        assert [a.label for a in view.exceptions[0].actions] == ["Retry page 37"]

    def test_the_briefs_three_pages_are_three_independent_exceptions(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        contract = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 3, 0),
            failures=parse_failures.failures_of([12, 37, 44]),
        )
        view = view_model.render_page_exception_list_view(contract)

        assert [e.page_number for e in view.exceptions] == [12, 37, 44]
        assert [a.label for e in view.exceptions for a in e.actions] == [
            "Retry page 12", "Retry page 37", "Retry page 44",
        ]

    def test_a_page_number_comes_from_the_record_and_not_from_its_position(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        """A surface that numbered its own list would call the first failed page
        "page 1". The record names page 7 — and page 12 comes before page 44
        because the RECORD's order says so."""
        one = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 1, 0),
            failures=parse_failures.failures_of([7]),
        )
        assert [
            e.page_number
            for e in view_model.render_page_exception_list_view(one).exceptions
        ] == [7]

        two = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 2, 0),
            failures=parse_failures.failures_of([12, 44]),
        )
        numbers = [
            e.page_number
            for e in view_model.render_page_exception_list_view(two).exceptions
        ]
        assert numbers == [12, 44]
        for position, number in enumerate(numbers, start=1):
            assert number != position

    def test_the_record_is_printed_as_the_record_states_it(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        coverage_record = coverage.PageCoverage(60, 60, 3, 0)
        failures = parse_failures.failures_of([12, 37, 44])
        view = view_model.render_page_exception_list_view(
            _build(contract_module, coverage=coverage_record, failures=failures)
        )

        assert view.coverage_line == coverage_record.as_line()
        assert view.failures_line == failures.as_line()
        assert view.coverage_line.startswith("PDF coverage: ")
        assert view.failures_line.startswith("PDF parse failures: ")

    def test_a_record_that_names_no_page_states_that_and_invents_none(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        contract = _build(
            contract_module,
            coverage=coverage.PageCoverage(10, 10, 0, 0),
            failures=parse_failures.failures_of(()),
        )
        view = view_model.render_page_exception_list_view(contract)

        assert contract.record_refusal is None
        assert view.exceptions == ()
        assert contract.failures.as_line() == "PDF parse failures: pages=none"

    @pytest.mark.parametrize("half", ["coverage", "failures", "disagreement"])
    def test_a_record_that_cannot_be_listed_is_never_listed_partially(
        self, coverage, parse_failures, contract_module, view_model, half,
    ):
        if half == "coverage":
            contract = _build(
                contract_module, coverage=None, failures=parse_failures.failures_of([37]),
            )
            expected = "CONTINUATION_NO_COVERAGE_RECORD"
        elif half == "failures":
            contract = _build(
                contract_module,
                coverage=coverage.PageCoverage(60, 60, 1, 0), failures=None,
            )
            expected = "RETRY_FAILURES_UNRECORDED"
        else:
            # The two halves disagree: the count says one, the record names two.
            contract = _build(
                contract_module,
                coverage=coverage.PageCoverage(60, 60, 1, 0),
                failures=parse_failures.failures_of([12, 37]),
            )
            expected = "RETRY_FAILURES_CONFLICT"
        view = view_model.render_page_exception_list_view(contract)

        assert contract.record_refusal == expected
        assert contract.exceptions == ()
        assert view.exceptions == ()
        assert view.record_refusal_message
        import app.review_ui.render as render_module

        text = render_module.render_page_exceptions_page(view)
        assert expected in text
        assert "Unread page" not in text

    def test_a_page_j17_would_refuse_is_listed_with_j17s_own_code_and_no_action(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        """A page the RECORD names but that cannot currently be read again is still
        an exception — it is one of the pages that keeps the drawing set
        incomplete. Its state is J17's refusal code, its reason is J17's own
        reason, and it offers no action: the surface never offers a retry that J17
        would refuse for a reason already decidable from the record."""
        coverage_record = coverage.PageCoverage(10, 10, 1, 0)
        failures = parse_failures.failures_of([99])
        contract = _build(contract_module, coverage=coverage_record, failures=failures)
        view = view_model.render_page_exception_list_view(contract)

        decision = parse_failures.check_retry_request(
            page_number=99, coverage=coverage_record, failures=failures,
        )
        assert decision.refusal_code == "RETRY_PAGE_BEYOND_DOCUMENT"
        assert [e.page_number for e in view.exceptions] == [99]
        assert view.exceptions[0].state == decision.refusal_code
        assert view.exceptions[0].state_label == decision.refusal_code
        assert view.exceptions[0].detail == decision.detail
        assert view.exceptions[0].actions == ()

    def test_an_unknown_status_is_shown_as_itself_and_an_absent_one_is_unknown(
        self, coverage, parse_failures, contract_module, view_model,
    ):
        """7AM's rule, applied here: a status this surface has no label for is
        printed verbatim rather than renamed — and an absent one is not a status."""
        unknown_value = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 1, 0),
            failures=parse_failures.failures_of([37]),
            project_status="something-new",
        )
        assert (
            view_model.render_page_exception_list_view(unknown_value).status_label
            == "something-new"
        )

        absent = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 1, 0),
            failures=parse_failures.failures_of([37]),
            project_status=None,
        )
        view = view_model.render_page_exception_list_view(absent)
        assert view.project_status == "unknown"
        assert view.status_label == "Unknown"

    def test_the_contract_states_no_code_of_its_own(self, contract_module):
        """Every refusal code the surface can state is IMPORTED from the milestone
        that decides it: the contract module assigns no code of its own, so it
        cannot name a state the retry would name differently."""
        tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"))
        assigned = {
            node.targets[0].id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        assert assigned == {
            "PAGE_EXCEPTION_TYPE_PARSE_FAILURE",
            "PAGE_EXCEPTION_STATE_RETRYABLE",
            "ACTION_RETRY_PAGE",
            "PAGE_EXCEPTION_SCOPE_STATEMENT",
        }, sorted(assigned)
        # The one state it owns is not a refusal code, and the codes it can state
        # are J16's and J17's own spellings — IMPORTED, never restated. No string
        # literal anywhere in the module spells one, so none can be built inline
        # either; and every code the surface can show is a name it took from the
        # milestone that decides it.
        assert contract_module.PAGE_EXCEPTION_STATE_RETRYABLE == RETRYABLE
        assert contract_module.PAGE_EXCEPTION_STATE_RETRYABLE not in j17.RETRY_CODES
        literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for name in RECORD_REFUSAL_CODES + j17.RETRY_CODES:
            assert name not in literals, name
        imported_names = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert set(RECORD_REFUSAL_CODES) <= imported_names
        assert "app.validation.parse_failures" in _imports(CONTRACT_PATH)
        assert "app.validation.page_windows" in _imports(CONTRACT_PATH)

    def test_the_contract_offers_exactly_one_action(self, contract_module):
        """A page that could not be read has exactly one remedy. There is no
        resolve, no approve, no override, no regenerate and no review action."""
        assert contract_module.ACTION_RETRY_PAGE == "RETRY_PAGE"
        source = _code_only(CONTRACT_PATH)
        for absent in ("ACTION_RESOLVE", "ACTION_APPROVE", "ACTION_OVERRIDE",
                       "ACTION_GENERATE", "ACTION_REVIEW", "ACTION_CONFIRM",
                       "ACTION_DISMISS", "ACTION_IGNORE"):
            assert absent not in source, absent

    def test_the_projection_is_pure(self):
        """Neither module reads, writes, decides or imports anything that could."""
        for path in (CONTRACT_PATH, VIEW_MODEL_PATH):
            assert _imports(path) <= {
                "dataclasses",
                "app.validation.page_coverage",
                "app.validation.page_windows",
                "app.validation.parse_failures",
                "app.validation.project_status",
                "app.cad_engine.page_exception_contract",
                "app.cad_engine.review_view_model",
            }, (path.name, sorted(_imports(path)))
            source = _code_only(path).lower()
            for token in FORBIDDEN_IN_PURE_MODULES:
                assert token not in source, (path.name, token)


# ===========================================================================
# B. Loading the surface does nothing at all.
# ===========================================================================
class TestLoadingTheSurfaceDoesNothing:
    def _tripwires(self, production, monkeypatch):
        """Every stage a retry would reach, replaced by one that fails loudly."""
        def forbidden(name):
            def blow_up(*args, **kwargs):
                raise AssertionError(f"rendering the surface reached {name}")
            return blow_up

        monkeypatch.setattr(production.pipeline, "page_count_of", forbidden("page_count_of"))
        monkeypatch.setattr(production.pipeline, "plan_retry", forbidden("plan_retry"))
        monkeypatch.setattr(production.pipeline, "retry_pdf_page", forbidden("retry_pdf_page"))

    def test_rendering_reads_no_page_calls_no_model_and_writes_nothing(
        self, project, production, monkeypatch, session,
    ):
        doc = project(pages=SHORT_PAGES)
        reads = _prepare(doc, monkeypatch, production)
        self._tripwires(production, monkeypatch)
        reads_before = len(reads)
        evidence_before = j17._evidence_state(doc)
        runs_before = j17._run_state(doc)
        contract = _RetryContract(doc)
        _bind(session, doc, contract)

        for _ in range(2):
            view = session.page_exception_view()
            assert [e.page_number for e in view.exceptions] == [FAILED_PAGE]
            session.render_page_exceptions_page()
            response = _client().get("/pages")
            assert response.status_code == 200
            assert _retry_targets(response.text) == [str(FAILED_PAGE)]

        # Zero pages rendered, zero model calls, zero retry requests.
        assert len(reads) == reads_before
        assert contract.calls == []
        # Zero evidence writes of any kind.
        assert j17._evidence_state(doc) == evidence_before
        assert j17._run_state(doc) == runs_before
        assert j17._failures_record(doc).page_numbers == (FAILED_PAGE,)

    def test_the_surface_cannot_retry_by_being_rendered(
        self, project, production, monkeypatch, session,
    ):
        """The contract is given an entry that raises if it is ever CALLED, so a
        surface that retried while rendering could not render at all."""
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)

        def never(page_number):
            raise AssertionError("rendering the surface issued a retry request")

        _bind(session, doc, never)
        session.render_page_exceptions_page()
        session.page_exception_view()
        assert _client().get("/pages").status_code == 200

    def test_the_rendered_page_names_exactly_the_pages_the_record_states(
        self, project, production, monkeypatch, session,
    ):
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)
        _bind(session, doc, _RetryContract(doc))

        text = _client().get("/pages").text

        assert _unread_numbers(text) == [str(FAILED_PAGE)]
        assert _retry_targets(text) == [str(FAILED_PAGE)]
        assert f"/pages/{FAILED_PAGE + 1}/retry" not in text
        # The record's own two lines are on the page, verbatim.
        assert j17._coverage_line(doc).as_line() in text
        assert j17._failures_record(doc).as_line() in text
        # And the page says what it is not.
        assert "Nothing on this page reads the drawing set" in text

    def test_an_unbound_session_states_no_page_rather_than_an_empty_list(self, session):
        text = session.render_page_exceptions_page()

        assert "No project's page-exception record is bound" in text
        assert "Unread page" not in text

    def test_no_control_is_rendered_for_a_page_that_cannot_be_retried(
        self, coverage, parse_failures, session,
    ):
        """The record names a page past the end of the document: it is listed as an
        exception with J17's own code, and no control is rendered for it."""
        coverage_record = coverage.PageCoverage(10, 10, 1, 0)
        failures = parse_failures.failures_of([99])
        session.bind_page_exceptions(
            project_id="p-1",
            warnings=[coverage_record.as_line(), failures.as_line()],
            project_status=REVIEW,
            retry_entry=lambda page_number: pytest.fail("no control, so no request"),
        )

        text = session.render_page_exceptions_page()

        assert "RETRY_PAGE_BEYOND_DOCUMENT" in text
        assert _retry_targets(text) == []
        assert _unread_numbers(text) == ["99"]
        assert "No action is available for this page right now." in text


# ===========================================================================
# C. The human retry action.
# ===========================================================================
class TestTheHumanRetryAction:
    def _prepared(self, project, production, monkeypatch, session):
        doc = project(pages=SHORT_PAGES)
        reads = _prepare(doc, monkeypatch, production)
        contract = _RetryContract(doc)
        _bind(session, doc, contract)
        return doc, reads, contract

    def test_the_action_reads_exactly_the_addressed_page_of_the_bound_project(
        self, project, production, monkeypatch, session,
    ):
        doc, reads, contract = self._prepared(project, production, monkeypatch, session)
        reads_before = len(reads)

        response = _client().post(f"/pages/{FAILED_PAGE}/retry")

        assert response.status_code == 200
        # The contract it was handed was called with that page and no other.
        assert contract.calls == [FAILED_PAGE]
        # And the genuine retry read one page: the addressed one.
        assert len(reads) == reads_before + 1
        assert reads[-1] == {
            "first_page": FAILED_PAGE, "max_pages": 1, "store_page_image": False,
        }

    def test_the_action_uses_the_existing_j17_contract(
        self, project, production, monkeypatch, session,
    ):
        """There is no second retry algorithm: the entry IS J17's, and the page
        reports J17's own outcome literal and J17's own reconciled record."""
        doc, _reads, _contract = self._prepared(project, production, monkeypatch, session)

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "Result state: PARSED" in text
        assert j17._failures_record(doc).is_empty
        assert j17._coverage_line(doc).is_complete
        assert "PDF parse failures: pages=none" in text

    def test_the_surface_layer_holds_no_retry_algorithm_at_all(self, session):
        """Structural, not promised: the session layer cannot import the pipeline,
        and defines no retry, no page validation and no record arithmetic."""
        source = _code_only(SESSION_PATH)
        assert "app.pipeline" not in _imports(SESSION_PATH)
        defined = {
            node.name for node in ast.walk(ast.parse(SESSION_PATH.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)
        }
        for absent in ("plan_retry", "retry_pdf_page", "check_retry_request", "page_count_of",
                       "analyze_pdf_pages", "validate_extraction"):
            assert absent not in defined, absent
        # The refusal is recognised by the shape J17 documents, not by importing
        # the class that raises it (which would pull the pipeline into the UI).
        assert "RetryRefused" not in source

    def test_the_project_and_the_page_come_from_the_binding_and_the_request(
        self, project, production, monkeypatch, session,
    ):
        """The retry acts on the BOUND project and the ADDRESSED page — nothing is
        inferred from a list position or from what the surface happens to show."""
        doc, _reads, contract = self._prepared(project, production, monkeypatch, session)
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

        _client().post(f"/pages/{FAILED_PAGE}/retry")

        assert contract.calls == [FAILED_PAGE]
        assert contract.doc.project_id == doc.project_id
        assert doc.repository.projects[doc.project_id]["status"] == DONE


# ===========================================================================
# D. Several failed pages are independent.
# ===========================================================================
class TestSeveralFailedPages:
    def _prepared(self, project, production, monkeypatch, session, failed=THREE_FAILED):
        doc = project(pages=SHORT_PAGES)
        reads = _prepare(doc, monkeypatch, production, failed=failed)
        contract = _RetryContract(doc)
        _bind(session, doc, contract)
        return doc, reads, contract

    def test_three_failed_pages_are_three_exceptions(
        self, project, production, monkeypatch, session,
    ):
        doc, _reads, _contract = self._prepared(project, production, monkeypatch, session)

        text = _client().get("/pages").text

        assert _unread_numbers(text) == [str(n) for n in THREE_FAILED]
        assert _retry_targets(text) == [str(n) for n in THREE_FAILED]
        assert j17._failures_record(doc).page_numbers == THREE_FAILED

    def test_retrying_one_page_moves_only_that_page(
        self, project, production, monkeypatch, session,
    ):
        doc, reads, contract = self._prepared(project, production, monkeypatch, session)
        sources_before = j17._sources(doc)
        reads_before = len(reads)
        others_before = [
            copy.deepcopy(row) for row in doc.repository.members
            if row["source_page"] in (2, 7)
        ]

        response = _client().post(f"/pages/{FAILED_PAGE}/retry")

        # One request, one page read, and the other pages' evidence untouched.
        assert contract.calls == [FAILED_PAGE]
        assert len(reads) == reads_before + 1
        assert reads[-1]["first_page"] == FAILED_PAGE
        assert [
            row for row in doc.repository.members if row["source_page"] in (2, 7)
        ] == others_before
        assert sorted(set(j17._sources(doc)) - set(sources_before)) == [FAILED_PAGE]
        # The record still names the other two, and the page still lists them.
        assert j17._failures_record(doc).page_numbers == (2, 7)
        assert _unread_numbers(response.text) == ["2", "7"]
        assert _retry_targets(response.text) == ["2", "7"]

    def test_a_page_that_was_read_leaves_the_list_and_the_others_stay(
        self, project, production, monkeypatch, session,
    ):
        doc, _reads, _contract = self._prepared(
            project, production, monkeypatch, session, failed=TWO_FAILED,
        )

        text = _client().post("/pages/4/retry").text

        assert _unread_numbers(text) == ["2"]
        assert _retry_targets(text) == ["2"]
        assert "/pages/4/retry" not in text
        assert j17._failures_record(doc).page_numbers == (2,)
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

    def test_the_record_changes_only_by_the_page_that_was_read(
        self, project, production, monkeypatch, session,
    ):
        """A retry is ADDITIVE to the accounting: it changes how many READ pages had
        no readable response, and neither the analysed count, the not-analysed
        count nor the document's own page count."""
        doc, _reads, _contract = self._prepared(
            project, production, monkeypatch, session, failed=TWO_FAILED,
        )
        before = j17._coverage_line(doc)

        _client().post(f"/pages/{FAILED_PAGE}/retry")

        after = j17._coverage_line(doc)
        assert (after.total_pages, after.analysed_pages, after.not_analysed_pages) == (
            before.total_pages, before.analysed_pages, before.not_analysed_pages,
        )
        assert after.parse_failed_pages == before.parse_failed_pages - 1


# ===========================================================================
# E. A successful retry.
# ===========================================================================
class TestASuccessfulRetry:
    def _prepared(self, project, production, monkeypatch, session):
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)
        _bind(session, doc, _RetryContract(doc))
        return doc

    def test_the_page_leaves_the_list_and_the_success_is_j17s_own(
        self, project, production, monkeypatch, session,
    ):
        doc = self._prepared(project, production, monkeypatch, session)
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "Result state: PARSED" in text
        assert SUCCESS_BANNER in text
        assert FAIL_BANNER not in text
        assert "Read again successfully" in text
        assert _retry_targets(text) == []
        assert "No page of this drawing set is recorded as having failed to parse." in text

    def test_the_projects_status_is_still_j13s_derivation(
        self, project, production, monkeypatch, session,
    ):
        """The surface states a status; it never derives one. Before the action it
        states the bound status, and afterwards the status J17's own result carries
        — which is J13's derivation over the persisted rows."""
        doc = self._prepared(project, production, monkeypatch, session)
        assert session.page_exception_view().project_status == REVIEW
        assert session.page_exception_view().status_label == "Needs review"

        session.submit_page_retry(FAILED_PAGE)

        assert doc.repository.projects[doc.project_id]["status"] == DONE
        assert session.page_exception_view().project_status == DONE
        assert session.page_exception_view().status_label == "Done"

    def test_a_successful_retry_resolves_no_connection_and_approves_no_evidence(
        self, project, production, monkeypatch, session,
    ):
        """The read's own outcome — members, connections, review items — is J17's.
        What the surface must not have done is decide anything about them: nothing
        that already existed was altered, and afterwards the page lists no
        exception to act on."""
        doc = self._prepared(project, production, monkeypatch, session)
        connections_before = copy.deepcopy(doc.repository.connections)
        items_before = copy.deepcopy(doc.repository.review_items)
        members_before = copy.deepcopy(doc.repository.members)
        project_row_before = copy.deepcopy(doc.repository.projects[doc.project_id])

        session.submit_page_retry(FAILED_PAGE)

        _still_present(connections_before, doc.repository.connections)
        _still_present(items_before, doc.repository.review_items)
        _still_present(members_before, doc.repository.members)
        # The project row keeps its identity, and the status on it is J13's own
        # re-derivation from the persisted evidence — the one field the READ
        # rewrote is the record, from J17's own reconciled counts.
        project_row_after = doc.repository.projects[doc.project_id]
        assert project_row_after["id"] == project_row_before["id"]
        assert project_row_after["user_id"] == project_row_before["user_id"]
        assert project_row_after["status"] == DONE
        assert j17._failures_record(doc).is_empty
        assert j17._coverage_line(doc).parse_failed_pages == 0
        assert session.page_exception_view().exceptions == ()


# ===========================================================================
# F. A retry that does not succeed.
# ===========================================================================
class TestARetryThatDoesNotSucceed:
    def _prepared(self, project, production, monkeypatch, session, **kwargs):
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production, **kwargs)
        _bind(session, doc, _RetryContract(doc))
        return doc

    def test_a_page_that_still_cannot_be_read_stays_listed_and_retryable(
        self, project, production, monkeypatch, session,
    ):
        doc = self._prepared(
            project, production, monkeypatch, session, fails_again={FAILED_PAGE},
        )

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "Result state: PARSE_FAILED" in text
        assert FAIL_BANNER in text
        assert SUCCESS_BANNER not in text
        assert "Still could not be read" in text
        assert _unread_numbers(text) == [str(FAILED_PAGE)]
        assert _retry_targets(text) == [str(FAILED_PAGE)]
        # The record is unchanged: the page is still named, and the failure still
        # keeps the project out of "done".
        assert j17._failures_record(doc).page_numbers == (FAILED_PAGE,)
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

    def test_the_failure_reason_is_not_hidden(self, project, production, monkeypatch, session):
        doc = self._prepared(
            project, production, monkeypatch, session, fails_again={FAILED_PAGE},
        )

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "its response still could not be parsed as engineering evidence" in text
        assert "the record is unchanged and the page is still unread" in text
        assert j17._failures_record(doc).as_line() in text
        assert j17._coverage_line(doc).as_line() in text

    def test_a_failed_retry_invents_no_evidence(self, project, production, monkeypatch, session):
        doc = self._prepared(
            project, production, monkeypatch, session, fails_again={FAILED_PAGE},
        )
        evidence_before = j17._evidence_state(doc)
        runs_before = j17._run_state(doc)

        _client().post(f"/pages/{FAILED_PAGE}/retry")

        assert j17._evidence_state(doc) == evidence_before
        # The ATTEMPT is recorded as one run of one page — J17's own bookkeeping,
        # and the only thing a failed retry adds.
        assert j17._run_state(doc) != runs_before
        assert len(doc.repository.analysis_runs) == len(runs_before["analysis_runs"]) + 1

    def test_an_accepted_request_is_never_presented_as_an_outcome(
        self, project, production, monkeypatch, session,
    ):
        """The shape `app/main.py` answers with: the read has not happened yet when
        a caller sees this. The surface states that, and claims nothing."""
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)

        def dispatched(page_number):
            return {
                "status": RETRY_STARTED, "project_id": doc.project_id,
                "page_number": page_number,
            }

        _bind(session, doc, dispatched)
        before = j17._evidence_state(doc)

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "Result state: retry_started" in text
        assert "Retry started" in text
        assert "the read has not reported yet" in text
        assert SUCCESS_BANNER not in text
        assert FAIL_BANNER not in text
        assert ATTENTION_BANNER in text
        # The page is still an exception, and nothing changed.
        assert _retry_targets(text) == [str(FAILED_PAGE)]
        assert j17._evidence_state(doc) == before

    def test_an_unreadable_result_states_nothing(self, project, production, monkeypatch, session):
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)
        _bind(session, doc, lambda page_number: ["not", "a", "result"])

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "Retry result not understood" in text
        assert SUCCESS_BANNER not in text
        assert _retry_targets(text) == []

    def test_an_entry_that_raises_something_other_than_a_refusal_claims_nothing(
        self, project, production, monkeypatch, session,
    ):
        """A read that could not be completed is not a refusal and not an outcome:
        it is stated as the failure it was, with nothing claimed about the page."""
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)

        def broken(page_number):
            raise RuntimeError("the model could not be reached")

        _bind(session, doc, broken)
        before = j17._evidence_state(doc)

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "RuntimeError" in text
        assert "the model could not be reached" in text
        assert "Nothing is claimed about the page" in text
        assert SUCCESS_BANNER not in text
        assert j17._evidence_state(doc) == before


# ===========================================================================
# G. A synchronous refusal is surfaced as the refusal it is.
# ===========================================================================
class TestASynchronousRefusal:
    def _prepared(self, project, production, monkeypatch, session):
        doc = project(pages=SHORT_PAGES)
        reads = _prepare(doc, monkeypatch, production)
        _bind(session, doc, _RetryContract(doc))
        return doc, reads

    def test_a_page_that_was_not_failed_is_refused_with_j17s_own_code(
        self, project, production, monkeypatch, session, parse_failures,
    ):
        doc, reads = self._prepared(project, production, monkeypatch, session)
        evidence_before = j17._evidence_state(doc)
        runs_before = j17._run_state(doc)
        reads_before = len(reads)

        text = _client().post(f"/pages/{FAILED_PAGE + 1}/retry").text

        decision = parse_failures.check_retry_request(
            page_number=FAILED_PAGE + 1,
            coverage=j17._coverage_line(doc),
            failures=j17._failures_record(doc),
        )
        assert decision.refusal_code == "RETRY_PAGE_NOT_FAILED"
        # J17's own code and J17's own reason, with no claim of a retry that did
        # not happen: not "retry started", not a success.
        assert "Result state: RETRY_PAGE_NOT_FAILED" in text
        assert decision.detail in text
        assert SUCCESS_BANNER not in text
        assert "Retry started" not in text
        # Nothing was read, nothing was written, and the record is unchanged.
        assert len(reads) == reads_before
        assert j17._evidence_state(doc) == evidence_before
        assert j17._run_state(doc) == runs_before

    def test_the_evidence_collision_case_is_surfaced_and_not_hidden(
        self, project, production, monkeypatch, session,
    ):
        """The state only the read-time evidence can decide: the record names the
        page as failed, and the page's evidence is already persisted (a crash
        between the two writes). J17 refuses rather than inserting a second copy,
        and the surface states that refusal."""
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)
        doc.repository.members.append({
            "id": "m-crash", "project_id": doc.project_id, "mark": f"M{FAILED_PAGE}",
            "section_name": "310UB46.2", "review_status": "extracted", "total_weight_kg": 0.0,
            "source_page": FAILED_PAGE, "source_drawing_id": j17._drawing_id(doc),
        })
        contract = _RetryContract(doc)
        _bind(session, doc, contract)
        before = j17._evidence_state(doc)

        text = _client().post(f"/pages/{FAILED_PAGE}/retry").text

        assert "Result state: RETRY_EVIDENCE_ALREADY_PERSISTED" in text
        assert SUCCESS_BANNER not in text
        assert j17._evidence_state(doc) == before
        assert contract.calls == [FAILED_PAGE]

    def test_a_refusal_leaves_the_record_and_the_offered_actions_as_they_were(
        self, project, production, monkeypatch, session,
    ):
        """The record still lists the page and still offers the action, because the
        record is what decides both. A refusal is a fact about this attempt, not a
        rewrite of the page's state."""
        doc, _reads = self._prepared(project, production, monkeypatch, session)

        text = _client().post(f"/pages/{FAILED_PAGE + 1}/retry").text

        assert _unread_numbers(text) == [str(FAILED_PAGE)]
        assert _retry_targets(text) == [str(FAILED_PAGE)]
        assert j17._failures_record(doc).page_numbers == (FAILED_PAGE,)


# ===========================================================================
# H. No engineering decision.
# ===========================================================================
class TestNoEngineeringDecision:
    def test_the_pure_modules_reach_no_engineering_chain(self):
        """The projection and its view model import no gate, no automation, no
        dispatch, no fabrication and no resolution, and name no engineering field
        of the connection chain."""
        for path in (CONTRACT_PATH, VIEW_MODEL_PATH):
            imported = _imports(path)
            for module in FORBIDDEN_ENGINEERING_MODULES:
                assert module not in imported, (path.name, module)
            source = _code_only(path).lower()
            for absent in ("automation", "fabrication", "dispatch", "resolve_connection",
                           "submit_resolution", "review_status", "section_name",
                           "grade", "thickness", "weld", "bolt", "plate"):
                assert absent not in source, (path.name, absent)

    def test_the_page_rendering_names_no_engineering_field(self):
        """`render.py` carries the 7AK connection chain as well, so this reads the
        page-exception section alone: those three functions say what a page is and
        offer the one action, and nothing else."""
        lowered = _j18_render_section().lower()
        for absent in ("automation", "fabrication", "dispatch", "resolve_connection",
                       "approve", "override", "generate", "review_status", "section_name",
                       "grade", "thickness", "weld", "bolt", "plate", "member"):
            assert absent not in lowered, absent
        # And `render.py` imports nothing new for this surface: the page-exception
        # contract and its view model, alongside what the 7AN pages already needed.
        assert _imports(RENDER_PATH) == {
            "html",
            "app.cad_engine.exception_resolution",
            "app.cad_engine.page_exception_contract",
            "app.cad_engine.page_exception_view_model",
            "app.cad_engine.review_view_model",
        }, sorted(_imports(RENDER_PATH))

    def test_the_page_action_is_the_only_mutating_route_the_surface_adds(self):
        """One action exists on this surface, and it is named for a page. There is
        no bulk action, no project-level retry and no second endpoint that could
        change anything."""
        source = WEB_PATH.read_text(encoding="utf-8")
        routes = re.findall(r'@review_app\.(get|post)\("([^"]+)"', source)
        added = {(method, path) for method, path in routes if path.startswith("/pages")}
        assert added == {
            ("get", "/pages"), ("post", "/pages/{page_number}/retry"),
        }, sorted(added)
        assert "retry_all" not in source and "retry-all" not in source

    def test_the_j18_section_of_the_session_names_no_engineering_field(self):
        """No member dimension, no section, no grade, no geometry, no connection
        decision: the page actions and the record's own two lines are all the
        surface's own code says.

        (Scoped to the J18 section because the module also carries the 7AK
        connection chain, whose vocabulary is not this surface's.)"""
        section = _j18_session_section()
        for absent in ("section_name", "grade", "thickness", "weld", "bolt", "plate",
                       "member_mark", "hole", "geometry", "dimension",
                       "resolve_connection", "submit_resolution", "approve",
                       "override", "generate"):
            assert absent not in section, absent

    def test_a_page_exception_is_not_a_connection_and_is_never_reviewable(
        self, coverage, parse_failures, contract_module,
    ):
        """The reason this milestone is a dedicated extension: a parse failure has
        no package, no decision, no task and no evidence, so it cannot be put into
        the connection review queue."""
        contract = _build(
            contract_module,
            coverage=coverage.PageCoverage(60, 60, 1, 0),
            failures=parse_failures.failures_of([37]),
        )
        exception = contract.exceptions[0]
        for absent in ("package_id", "decision", "tasks", "evidence", "provenance"):
            assert not hasattr(exception, absent), absent
        assert exception.available_actions == (contract_module.ACTION_RETRY_PAGE,)
        assert "review_contract" not in _imports(CONTRACT_PATH)

    def test_the_surfaces_action_vocabulary_is_one_action(self, contract_module):
        assert contract_module.ACTION_RETRY_PAGE == "RETRY_PAGE"
        declared = set()
        for path in (CONTRACT_PATH, VIEW_MODEL_PATH):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("ACTION_"):
                        declared.add(target.id)
        assert declared == {"ACTION_RETRY_PAGE"}, sorted(declared)


# ===========================================================================
# I. J16's continuation contract is untouched.
# ===========================================================================
class TestJ16IsUntouched:
    def test_the_window_and_retry_vocabulary_is_unchanged(self, production, parse_failures):
        import app.validation.page_windows as windows

        for name in j16.REFUSAL_CODES:
            assert getattr(windows, name) == name
            assert name in windows.__all__
        for name in j17.RETRY_CODES:
            assert getattr(parse_failures, name) == name

    def test_a_surface_retry_does_not_move_the_continuation_boundary(
        self, project, production, monkeypatch, session,
    ):
        """A retry is ADDITIVE to the boundary: it changes how many analysed pages
        had no readable response, and neither the analysed count, the not-analysed
        count nor the document's own total."""
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)
        _bind(session, doc, _RetryContract(doc))
        before = j17._coverage_line(doc)

        session.submit_page_retry(FAILED_PAGE)

        after = j17._coverage_line(doc)
        assert after.analysed_pages == before.analysed_pages
        assert after.not_analysed_pages == before.not_analysed_pages
        assert after.total_pages == before.total_pages
        assert after.is_complete

    def test_the_surface_does_not_reach_the_window_contract(self):
        for path in (SESSION_PATH, RENDER_PATH, VIEW_MODEL_PATH, CONTRACT_PATH):
            source = _code_only(path)
            for absent in ("check_page_window", "plan_continuation",
                           "continue_pdf_extraction", "WINDOW_SIZE", "WINDOW_",
                           "MAX_PDF_PAGES", "accumulate_coverage"):
                assert absent not in source, (path.name, absent)

    def test_the_surface_adds_no_route_to_the_continuation_endpoint(self):
        source = WEB_PATH.read_text(encoding="utf-8")
        for absent in ("retry-extraction", "continue", "window"):
            assert absent not in source, absent


# ===========================================================================
# J. Scope — what this milestone did not do.
# ===========================================================================
class TestScope:
    def test_no_migration_was_added(self):
        """J18 added none — the page-exception surface reads the record J15/J17 already
        write. J22 later added a fourth migration; this surface reads none of it, so the
        claim stands over the schema as it now is. J23 added a fifth, which this surface
        also reads none of: the surface is served from the coverage and failure records in
        `projects.warnings`, and it neither reads nor writes a capture. J28 added a sixth,
        which this surface also reads none of: the surface is still served from those two
        records, and a PDF annotation occurrence is neither of them. (J28A is the
        bookkeeping step that recorded the name here.) J44 added a seventh, which this surface
        reads none of either: a claim row is not a coverage record and not a failure record,
        and the surface is still served entirely from `projects.warnings`. J61 added an eighth,
        which this surface reads none of either: a source document's identity is not a coverage
        record and not a failure record, and it moves no page count and no failure line. The
        eight names are pinned exactly."""
        assert tuple(sorted(MIGRATIONS)) == (
            "20260924000000_j5_section_resolution_truth.sql",
            "20260924010000_j6_reference_data_identity.sql",
            "20260924020000_j8b_connection_plate_evidence_nullability.sql",
            "20260925000000_j22_connection_review_persistence.sql",
            "20260925010000_j23_page_extraction_captures.sql",
            "20260927000000_j28_pdf_annotation_occurrences.sql",
            "20260928000000_j44_project_review_claims.sql",
            "20260929000000_j61_project_documents.sql",
            "20260929010000_j66_field_evidence_citations.sql",
            "20261006000000_l19_selected_extraction_lineage.sql",
        )
        present = sorted(path.name for path in (REPO / "supabase" / "migrations").iterdir())
        assert present == sorted(MIGRATIONS)

    def test_no_new_dependency_was_introduced(self):
        requirements = (REPO / "requirements.txt").read_text(encoding="utf-8").lower()
        for absent in ("fuzzywuzzy", "thefuzz", "rapidfuzz", "psycopg", "sqlalchemy",
                       "celery", "apscheduler", "jinja2", "streamlit", "dash", "flask"):
            assert absent not in requirements, absent

    def test_the_surface_does_no_other_milestones_work(self):
        """No DXF, DWG, IFC, authentication, client-side code or database work."""
        for path in (CONTRACT_PATH, VIEW_MODEL_PATH, SESSION_PATH, WEB_PATH, RENDER_PATH):
            source = _code_only(path).lower()
            for absent in ("dxf", "dwg", "ifc", "jevestack", "oauth", "jwt", "password",
                           "<script", "react", "vue", "sqlalchemy", "fabrication",
                           "pdf_generator", "backgroundtasks"):
                assert absent not in source, (path.name, absent)

    def test_the_review_ui_exposes_the_j18_names_and_nothing_more(self, session):
        for name in ("bind_page_exceptions", "clear_page_exceptions", "is_page_exceptions_bound",
                     "page_exception_view", "render_page_exceptions_page",
                     "submit_page_retry"):
            assert name in session.__all__, name
        # The existing 7AN surface is intact alongside it.
        for name in ("bind_workflow", "submit_resolution", "refresh"):
            assert name in session.__all__, name

    def test_the_durable_record_is_the_only_authority_the_surface_reads(
        self, project, production, monkeypatch, session,
    ):
        """The surface is bound to the RECORD, not to a run's in-memory intake: a
        record missing a half states no page, even where the run's own intake holds
        a failed page. Nothing here consults anything but the two persisted lines."""
        doc = project(pages=SHORT_PAGES)
        _prepare(doc, monkeypatch, production)
        session.bind_page_exceptions(
            project_id=doc.project_id,
            # The failures line alone: the coverage record that would make it
            # listable is absent from the record.
            warnings=[j17._failures_record(doc).as_line()],
            project_status=REVIEW,
            retry_entry=lambda page_number: None,
        )

        view = session.page_exception_view()
        assert view.record_refusal == "CONTINUATION_NO_COVERAGE_RECORD"
        assert view.exceptions == ()
        text = session.render_page_exceptions_page()
        assert _retry_targets(text) == []
        assert _unread_numbers(text) == []
