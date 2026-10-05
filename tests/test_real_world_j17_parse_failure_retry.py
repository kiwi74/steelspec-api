"""
J17 — PDF PARSE-FAILURE IDENTITY AND SAFE RETRY.

Milestone J15 recorded HOW MANY pages of a drawing set were read without a
readable response. That count is enough to keep such a project out of "done" and
not enough to act on: a count names no page, so the page that failed could not be
found again, and J16 proved the consequence — the only retry the system had was
to re-read the WINDOW that contained it, whose other pages are already persisted,
which the window contract correctly refuses.

J17 gives a failed page an identity and a way to read exactly that page again:

    PDF coverage: total=60 analysed=60 parse_failed=1 not_analysed=0
    PDF parse failures: pages=37

    two lines, ONE call      the page numbers and the count that counts them are
                             written together, so they cannot be persisted apart
    POST /retry-extraction/…?page=37
                             reads page 37 and nothing else
    reconciled               a page stops being named as failed by being read
                             successfully, and by nothing else

WHAT THIS FILE PROVES
---------------------
* A failed page is IDENTIFIED, not merely counted: the record names it, the two
  halves agree, and a reader that finds them disagreeing acts on neither.
* A retry reads exactly the requested page, persists only that page's evidence,
  and leaves every other page's evidence untouched.
* A retry that fails again leaves the page exactly as failed as it was, invents
  no evidence, changes nothing about the drawing set, and leaves the page
  retryable — it is not a terminal state.
* Several failed pages are independent: retrying one moves only that one, and the
  project stays blocked from "done" until every named page is resolved.
* A page that is not recorded as failed is refused BEFORE anything is read, so a
  retry cannot re-read a page that already has an answer or duplicate its rows.
* J16's continuation contract is neither weakened nor bypassed: a parse failure
  never made a continuation impossible, a continuation still cannot skip pages,
  and a retry does not move the boundary a continuation is derived from.
* Evidence already persisted for a page the record calls failed is refused by
  exact identity (drawing + page) rather than duplicated by guessing.
* Every refusal reads nothing, writes nothing and moves nothing — proven by
  comparing the whole persisted shape before and after.
* No migration, and no column a previous milestone had not already written.

THE BOUNDARY
------------
Every test but the pure-contract classes drives the GENUINE production function —
`retry_pdf_page` — over a REAL PDF written by pypdf, with the real `page_count_of`
(it reads that document), the real `SectionMatcher` (over the pinned reference
index the accepted section-authority harness uses, so nothing here goes near the
network), the real validation, the real row construction and the real J13
derivation. Only the two EXTERNAL stages are replaced: the AI call, by a
page-honest stand-in that returns exactly the pages a real renderer would return
for the page it is asked for, and the reference read behind the matcher, by the
pinned index.

Nothing here reaches the network, needs an AI key, or writes production data.
The harness, the doubles and the fixtures are J4's, J13's and J16's, reused rather
than restated.
"""

from __future__ import annotations

import ast
import copy
import re
from urllib.parse import unquote

import httpx
import pytest
from yarl import URL

from tests import production_review_auth as auth
from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j16_pdf_continuation as j16
from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production
authority = j16.authority

REPO = j13.REPO
DONE = j13.DONE
REVIEW = j13.REVIEW

PIPELINE_PATH = REPO / "app" / "pipeline.py"
MAIN_PATH = REPO / "app" / "main.py"
PARSE_FAILURES_PATH = REPO / "app" / "validation" / "parse_failures.py"
PAGE_WINDOWS_PATH = REPO / "app" / "validation" / "page_windows.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"

COVERAGE_LINE_PREFIX = "PDF coverage: "
FAILURES_LINE_PREFIX = "PDF parse failures: "

# A drawing set inside the cap with one failed page is the smallest case in which
# a retry is meaningful at all.
SHORT_PAGES = 10
FAILED_PAGE = 4

# The brief's own interaction case: a drawing set larger than one extraction
# window, with a failure inside the FIRST window — the case in which a
# continuation must not silently move past the page that failed.
LONG_PAGES = 75
WINDOW_SIZE = j16.WINDOW_SIZE  # 30, pinned by J16's harness

# The refusal vocabulary of a retry: this milestone's own, plus the J16 codes it
# reuses for the rules that are the same rule.
RETRY_CODES = (
    "RETRY_PAGE_MALFORMED",
    "RETRY_FAILURES_UNRECORDED",
    "RETRY_FAILURES_CONFLICT",
    "RETRY_PAGE_BEYOND_DOCUMENT",
    "RETRY_PAGE_NOT_FAILED",
    "RETRY_EVIDENCE_ALREADY_PERSISTED",
    "RETRY_MEMBER_MARK_COLLISION",
    "RETRY_READ_MISMATCH",
)

REUSED_J16_CODES = (
    "CONTINUATION_SOURCE_MISMATCH",
    "CONTINUATION_DRAWING_SET_UNRESOLVED",
    "CONTINUATION_NO_COVERAGE_RECORD",
    "CONTINUATION_RECORD_CONFLICT",
    "CONTINUATION_DOCUMENT_TOTAL_MISMATCH",
    "COVERAGE_UNUSABLE",
)


@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    """Binds J16's page helpers to the real production `PageExtraction`.

    J16's own autouse fixture does this for its module only, so a test here that
    builds a page through `j16._page` must bind it for this module too.
    """
    saved = j16._PAGE
    j16._PAGE = production.PageExtraction
    yield
    j16._PAGE = saved


@pytest.fixture()
def parse_failures(production):
    """The J17 contract, imported under the `production` fixture's own
    configuration boundary so it is removed from sys.modules by that fixture's
    teardown like every other app module this file touches."""
    import app.validation.parse_failures as module

    return module


@pytest.fixture()
def coverage(production):
    import app.validation.page_coverage as module

    return module


# ===========================================================================
# The test-only repository seam: J16's serving double, plus J17's one read.
# ===========================================================================
class _RetryRepository(j16._ServingRepository):
    """J16's double plus `evidence_rows_for_page`, answered from its own rows.

    The read exists so a retry can ask whether a page the record calls failed
    already has evidence persisted against it. It is answered here from the rows
    the production code wrote through this same object — never from rows a test
    typed in — and it filters by EXACT identity (source drawing + source page),
    which is the rule the production read applies.
    """

    def evidence_rows_for_page(self, drawing_id, page_number):
        self.reads.append("evidence_rows_for_page")
        rows = []
        for table, stored in (("steel_members", self.members), ("connections", self.connections)):
            for row in stored:
                if (row.get("source_drawing_id") == drawing_id
                        and row.get("source_page") == page_number):
                    rows.append({"table": table, "id": row["id"]})
        return rows


def _fixture_function(fixture):
    """The plain function behind a pytest fixture."""
    return fixture._get_wrapped_function()


@pytest.fixture()
def document(production, monkeypatch, tmp_path):
    """J16's document harness, driven exactly as J16 drives it.

    pytest resolves a fixture by name within its own module, so this file cannot
    simply request J16's; it calls J16's function with J16's own collaborators.
    The harness here IS J16's — one real PDF writer, one page-honest stand-in,
    one serving repository double, one pinned reference index — rather than a
    copy of it that could drift from it.
    """
    import app.validation.page_windows as windows_module

    return _fixture_function(j16.document)(production, windows_module, monkeypatch, tmp_path)


@pytest.fixture()
def project(document, production, parse_failures, monkeypatch):
    """J16's document harness, with J17's retry entry points added.

    J16's fixture builds its repository from the module-global name
    `_ServingRepository`; substituting this subclass for the duration of the test
    is how J17's one extra read reaches it. Everything else — the real PDF, the
    real `page_count_of`, the pinned reference index, the genuine production
    functions — is J16's and is not restated here.
    """
    monkeypatch.setattr(j16, "_ServingRepository", _RetryRepository)

    def build(**kwargs):
        doc = document(**kwargs)
        user_id = doc.repository.projects[doc.project_id]["user_id"]
        doc.retry = lambda **kw: production.pipeline.retry_pdf_page(
            doc.source, doc.project_id, user_id, doc.storage_path, **kw
        )
        doc.plan_retry = lambda **kw: production.pipeline.plan_retry(
            doc.project_id, doc.storage_path, **kw
        )
        doc.refused_retry = parse_failures.RetryRefused
        return doc

    return build


def _reader(doc, *, failed=(), fails_again=(), members_for=None, connections_for=None):
    """The vision stage, replaced: exactly the page(s) it is asked for.

        failed       pages whose response could not be read when a WINDOW was
                     read — how a parse failure gets into the record in the first
                     place.
        fails_again  pages that fail ONE MORE time when read ON THEIR OWN: the
                     retry that failed. Consumed by the read it applies to, so a
                     test can retry the page once more and watch it succeed —
                     which is what "a failed retry is not terminal" means.

    Every call is recorded, including whether the caller asked for the page's
    image to be stored again, and each failing page comes back as the object
    `app/ai_analysis/pdf_vision_analyzer.py` returns for a page that WAS rendered
    and put to the model whose answer could not be read.
    """
    calls = []
    page = j16._page
    failed_page = j16._failed_page
    unreadable = set(fails_again)

    def read(filepath, user_id, project_id, drawing_id, max_pages, *,
             first_page=1, store_page_image=True):
        calls.append({
            "first_page": first_page,
            "max_pages": max_pages,
            "store_page_image": store_page_image,
        })
        last_page = min(first_page + max_pages - 1, doc.pages)
        pages = []
        for number in range(first_page, last_page + 1):
            if max_pages > 1:
                unreadable_here = number in failed
            else:
                unreadable_here = number in unreadable
                unreadable.discard(number)
            if unreadable_here:
                pages.append(failed_page(number))
            else:
                pages.append(page(
                    number,
                    (members_for or j16._default_members)(number),
                    connections=(connections_for or (lambda n: ()))(number),
                ))
        return pages

    return calls, read


def _install(doc, monkeypatch, production, **kwargs):
    """Replaces the vision stage for this test and returns every call it gets."""
    calls, read = _reader(doc, **kwargs)
    monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", read)
    return calls


def _warnings(doc):
    return doc.repository.projects[doc.project_id]["warnings"] or []


def _lines(doc, prefix):
    return [w for w in _warnings(doc) if isinstance(w, str) and w.startswith(prefix)]


def _failures_record(doc):
    """The persisted failure identity, read back the way production reads it."""
    from app.validation.parse_failures import parse_failures_from_warnings

    return parse_failures_from_warnings(_warnings(doc))


def _coverage_line(doc):
    return j16._coverage_line(doc)


def _drawing_id(doc):
    return next(iter(doc.repository.drawings.values()))["id"]


def _sources(doc):
    return sorted(row["source_page"] for row in doc.repository.members)


def _evidence_state(doc):
    """Every persisted engineering fact.

    A refusal and a retry that failed again must move NONE of it: not a member,
    not a connection, not a counter, not a warning, not the project's status.
    """
    return {
        "members": copy.deepcopy(doc.repository.members),
        "connections": copy.deepcopy(doc.repository.connections),
        "review_items": copy.deepcopy(doc.repository.review_items),
        "drawing_set_updates": copy.deepcopy(doc.repository.drawing_set_updates),
        "drawing_meta": copy.deepcopy(doc.repository.drawing_meta),
        "project_summary": copy.deepcopy(doc.repository.project_summary),
        "project_row": copy.deepcopy(doc.repository.projects[doc.project_id]),
        "drawings": copy.deepcopy(doc.repository.drawings),
        "drawing_sets": copy.deepcopy(doc.repository.drawing_sets),
    }


def _run_state(doc):
    """The run history: which reads happened and what each run row recorded."""
    return {
        "analysis_runs": copy.deepcopy(doc.repository.analysis_runs),
        "analysis_run_updates": copy.deepcopy(doc.repository.analysis_run_updates),
    }


def _refused(exception_info):
    return exception_info.value.code, exception_info.value.detail


def _call_text(text, open_index):
    """The text of a call, from its opening parenthesis to the one closing it."""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index]
    raise AssertionError("unbalanced call — the source being scanned is not a call")


# ===========================================================================
# 1. The failure record — WHICH pages failed, and the rules that read it.
# ===========================================================================
class TestTheFailureRecord:
    """The pure contract: decidable from text alone, with no database, no
    filesystem, no AI and no rendering anywhere near it."""

    def test_a_failure_record_is_one_deterministic_line(self, parse_failures):
        assert parse_failures.failures_of([44, 12, 37]).as_line() == "PDF parse failures: pages=12,37,44"
        assert parse_failures.failures_of([]).as_line() == "PDF parse failures: pages=none"
        assert parse_failures.failures_of([37, 37]).page_numbers == (37,)

    def test_a_record_states_the_pages_it_names(self, parse_failures):
        record = parse_failures.failures_of([12, 37])
        assert record.count == 2
        assert record.names(37) and not record.names(38)
        assert not record.is_empty
        assert parse_failures.failures_of([]).is_empty

    def test_a_record_can_be_extended_and_repaired(self, parse_failures):
        """A later window ADDS its failures (J16 reads forward, so an earlier
        failure is still a failure), and a successful retry REMOVES exactly one."""
        record = parse_failures.failures_of([12]).including([37, 44])
        assert record.page_numbers == (12, 37, 44)
        assert record.without(37).page_numbers == (12, 44)
        assert record.without(37).count == 2

    def test_a_page_that_was_not_failed_cannot_be_reconciled(self, parse_failures):
        """The writer-side invariant of a successful retry: a page that is not
        named as failed cannot have been repaired, so the record is not rewritten
        on that premise."""
        with pytest.raises(ValueError):
            parse_failures.failures_of([12]).without(99)

    def test_a_record_this_module_did_not_write_is_not_a_record(self, parse_failures):
        """An unreadable record is NO record — including one whose pages are not
        in the canonical order this module writes them in, because a record that
        differs was not written by a run of this code describing those pages."""
        for warnings in (
            [],
            None,
            ["PDF coverage: total=60 analysed=60 parse_failed=1 not_analysed=0"],
            ["PDF parse failures: pages=37,12"],
            ["PDF parse failures: pages=0"],
            ["PDF parse failures: pages=thirty-seven"],
            ["PDF parse failures: forty"],
            [None],
        ):
            assert parse_failures.parse_failures_from_warnings(warnings) is None, warnings

    def test_a_stated_empty_record_is_not_the_same_as_no_record(self, parse_failures):
        """A run that read every page it analysed states that; a run that kept no
        such record states nothing, and the two must not read alike."""
        stated = parse_failures.parse_failures_from_warnings(["PDF parse failures: pages=none"])
        assert stated is not None and stated.is_empty
        assert parse_failures.parse_failures_from_warnings([]) is None

    def test_the_briefs_worked_example_is_representable(self, coverage, parse_failures):
        """total=60, analysed=60, parse_failed=[37], not_analysed=0 — stated as a
        page, not as a count."""
        record = coverage.coverage_of(
            total_pages=60, page_numbers=range(1, 61), parse_failed_page_numbers=[37],
        )
        failures = parse_failures.parse_failures_from_warnings([
            record.as_line(), parse_failures.failures_of([37]).as_line(),
        ])
        assert record.total_pages == 60 and record.analysed_pages == 60
        assert record.parse_failed_pages == 1 and record.not_analysed_pages == 0
        assert record.identity_holds and not record.is_complete
        assert failures.page_numbers == (37,)
        assert failures.count == record.parse_failed_pages

    def test_a_page_can_be_checked_against_both_halves(self, coverage, parse_failures):
        record = coverage.coverage_of(
            total_pages=60, page_numbers=range(1, 61), parse_failed_page_numbers=[37],
        )
        failures = parse_failures.failures_of([37])
        accepted = parse_failures.check_retry_request(
            page_number=37, coverage=record, failures=failures,
        )
        assert accepted.accepted and accepted.page_number == 37

    def test_a_page_number_must_be_one_page_number(self, coverage, parse_failures):
        record = coverage.coverage_of(total_pages=60, page_numbers=range(1, 61))
        for page_number in (None, "37", 0, -1, 37.0, True, False):
            decision = parse_failures.check_retry_request(
                page_number=page_number, coverage=record, failures=parse_failures.failures_of([37]),
            )
            assert not decision.accepted, page_number
            assert decision.refusal_code == "RETRY_PAGE_MALFORMED", page_number

    def test_the_record_must_be_readable_before_a_page_is(self, coverage, parse_failures):
        """A project whose coverage record states failures without naming them
        states a count this milestone cannot act on — the count still keeps the
        project out of "done", and no page can be identified for retry."""
        record = coverage.coverage_of(
            total_pages=60, page_numbers=range(1, 61), parse_failed_page_numbers=[37],
        )
        decision = parse_failures.check_retry_request(
            page_number=37, coverage=record, failures=None,
        )
        assert (decision.refusal_code) == "RETRY_FAILURES_UNRECORDED"

    def test_two_halves_that_disagree_are_acted_on_by_neither(self, coverage, parse_failures):
        record = coverage.coverage_of(
            total_pages=60, page_numbers=range(1, 61), parse_failed_page_numbers=[37],
        )
        disagreement = parse_failures.failures_of([12, 44])
        decision = parse_failures.check_retry_request(
            page_number=12, coverage=record, failures=disagreement,
        )
        assert decision.refusal_code == "RETRY_FAILURES_CONFLICT"
        assert "37" in decision.detail or "1" in decision.detail

    def test_a_page_past_the_document_is_not_a_page(self, coverage, parse_failures):
        record = coverage.coverage_of(
            total_pages=60, page_numbers=range(1, 61), parse_failed_page_numbers=[37],
        )
        decision = parse_failures.check_retry_request(
            page_number=61, coverage=record, failures=parse_failures.failures_of([37]),
        )
        assert decision.refusal_code == "RETRY_PAGE_BEYOND_DOCUMENT"

    def test_a_page_that_is_not_failed_is_not_retryable(self, coverage, parse_failures):
        record = coverage.coverage_of(
            total_pages=60, page_numbers=range(1, 61), parse_failed_page_numbers=[37],
        )
        decision = parse_failures.check_retry_request(
            page_number=20, coverage=record, failures=parse_failures.failures_of([37]),
        )
        assert decision.refusal_code == "RETRY_PAGE_NOT_FAILED"

    def test_an_unusable_coverage_record_cannot_name_a_page(self, coverage, parse_failures):
        """A record whose own counts do not add up states no page count, and a
        page can only be checked against a count that adds up."""
        record = coverage.coverage_of(total_pages=None, page_numbers=[37])
        assert not record.identity_holds
        decision = parse_failures.check_retry_request(
            page_number=37, coverage=record, failures=parse_failures.failures_of([37]),
        )
        assert decision.refusal_code == "COVERAGE_UNUSABLE"

    def test_the_contract_is_pure(self):
        """Decidable from the record and the page number alone: no database, no
        filesystem, no AI, no rendering — and it reuses J16's vocabulary rather
        than restating the rules that are the same rule."""
        tree = ast.parse(PARSE_FAILURES_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported == {
            "dataclasses", "typing", "app.validation.page_windows",
        }, imported


# ===========================================================================
# 2. The persisted record — both halves, one call, agreeing.
# ===========================================================================
class TestThePersistedRecord:
    def test_a_failed_page_is_named_and_counted_together(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()

        lines = _lines(doc, FAILURES_LINE_PREFIX)
        assert len(lines) == 1, lines
        assert lines[0] == f"PDF parse failures: pages={FAILED_PAGE}"
        # The count the coverage line states IS the number of pages named: the
        # two lines are written by one call and cannot be persisted apart.
        assert _coverage_line(doc).parse_failed_pages == _failures_record(doc).count
        # The coverage line is still the last thing written; the identity it
        # states still holds, and a parse failure is inside `analysed`.
        assert _warnings(doc)[-1] == _coverage_line(doc).as_line()
        assert _warnings(doc)[-2] == lines[0]
        assert _coverage_line(doc).identity_holds
        assert _coverage_line(doc).parse_failed_pages < _coverage_line(doc).analysed_pages

    def test_a_run_with_no_failures_still_states_that(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production)
        doc.first_window()

        assert _lines(doc, FAILURES_LINE_PREFIX) == ["PDF parse failures: pages=none"]
        assert _failures_record(doc).is_empty
        assert _coverage_line(doc).is_complete

    def test_the_failure_identity_rides_a_column_that_already_existed(self, project, production, monkeypatch):
        """No migration accompanies this milestone: the record rides
        `projects.warnings`, where J11's drop accounting and J15's coverage
        record already live, and the project's key set is unchanged."""
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()

        assert set(doc.repository.project_summary) == j13.PDF_PROJECT_PAYLOAD_KEYS | {"project_id"}
        # The upload's own facts, plus the columns the run wrote — and nothing
        # new: the failures line rides `warnings`, which already existed.
        assert set(doc.repository.projects[doc.project_id]) == (
            {"id", "uploaded_file_path", "source_format", "user_id"}
            | j13.PDF_PROJECT_PAYLOAD_KEYS
        )

    def test_a_continuation_carries_an_earlier_failure_forward(self, project, production, monkeypatch):
        """A failure recorded in the first window is still a failure after the
        last page has been read: a window that dropped it would be stating that a
        page nobody re-read had been read after all."""
        doc = project(pages=LONG_PAGES)
        _install(doc, monkeypatch, production, failed={17})
        doc.first_window()
        assert _failures_record(doc).page_numbers == (17,)

        doc.continue_window()
        assert _failures_record(doc).page_numbers == (17,)
        assert _coverage_line(doc).parse_failed_pages == 1
        assert len(_lines(doc, FAILURES_LINE_PREFIX)) == 1
        assert len(_lines(doc, COVERAGE_LINE_PREFIX)) == 1


# ===========================================================================
# 3. The retry — one page, and only that page.
# ===========================================================================
class TestTheRetry:
    def test_a_retry_reads_exactly_the_page_it_was_asked_for(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()

        doc.retry(page_number=FAILED_PAGE)

        assert calls[0] == {"first_page": 1, "max_pages": WINDOW_SIZE, "store_page_image": True}
        assert calls[1] == {"first_page": FAILED_PAGE, "max_pages": 1, "store_page_image": False}
        assert len(calls) == 2

    def test_a_retry_does_not_store_the_page_image_a_second_time(self, project, production, monkeypatch):
        """The image is stored BEFORE the model is asked about a page, so a page
        that failed to parse already has the image a reviewer would look at;
        storing it again would record one page of one drawing twice."""
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()

        doc.retry(page_number=FAILED_PAGE)

        assert [call["store_page_image"] for call in calls] == [True, False]

    def test_a_retry_persists_only_that_pages_evidence(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = copy.deepcopy(doc.repository.members)
        assert FAILED_PAGE not in _sources(doc)

        doc.retry(page_number=FAILED_PAGE)

        # Every earlier row is untouched, in place and in order.
        assert doc.repository.members[: len(before)] == before
        assert _sources(doc) == list(range(1, SHORT_PAGES + 1))
        # And the new rows carry the TRUE page they were read from.
        added = doc.repository.members[len(before):]
        assert {row["source_page"] for row in added} == {FAILED_PAGE}
        assert {row["source_drawing_id"] for row in added} == {_drawing_id(doc)}
        assert {row["mark"] for row in added} == {f"M{FAILED_PAGE}"}

    def test_the_page_stops_being_named_as_failed_only_by_being_read(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        assert _coverage_line(doc).parse_failed_pages == 1

        result = doc.retry(page_number=FAILED_PAGE)

        record = _coverage_line(doc)
        # The page was already INSIDE `analysed` — a parse failure is a reading
        # failure of a page that was read — so the retry changes only how many of
        # those pages had no readable response.
        assert (record.total_pages, record.analysed_pages, record.parse_failed_pages,
                record.not_analysed_pages) == (SHORT_PAGES, SHORT_PAGES, 0, 0)
        assert record.identity_holds and record.is_complete
        assert _failures_record(doc).is_empty
        assert _lines(doc, FAILURES_LINE_PREFIX) == ["PDF parse failures: pages=none"]
        assert len(_lines(doc, COVERAGE_LINE_PREFIX)) == 1
        assert result["outcome"] == "PARSED" and result["resolved"] is True
        assert result["parse_failure_pages"] == []
        assert result["pages_processed"] == 1

    def test_the_project_status_is_still_derived_only_by_j13(self, project, production, monkeypatch):
        """No retry status exists: the project's status is derived from the
        persisted rows, exactly as every other path derives it."""
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

        result = doc.retry(page_number=FAILED_PAGE)

        assert result["project_status"] == DONE
        assert doc.repository.projects[doc.project_id]["status"] == DONE

    def test_the_persisted_summary_is_a_function_of_the_persisted_rows(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()

        doc.retry(page_number=FAILED_PAGE)

        summary = doc.repository.project_summary
        assert summary["total_members"] == len(doc.repository.members)
        assert summary["total_connections"] == len(doc.repository.connections)
        assert summary["total_weight_kg"] == sum(
            row["total_weight_kg"] or 0 for row in doc.repository.members
        )

    def test_a_retry_states_nothing_about_the_drawing_itself(self, project, production, monkeypatch):
        """LIMITATION, pinned rather than hidden: a retry writes no drawing-level
        metadata. A drawing whose page 1 failed to parse keeps the unknown it was
        recorded with, even after page 1 has been read — absence persists as
        absence, and the retry invents no drawing number to fill it in."""
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={1})
        doc.first_window()
        before = copy.deepcopy(doc.repository.drawing_meta)

        doc.retry(page_number=1)

        assert doc.repository.drawing_meta == before
        assert doc.repository.drawings[_drawing_id(doc)]["drawing_number"] is None

    def test_a_connection_read_on_a_retried_page_links_the_member_it_names(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(
            doc, monkeypatch, production, failed={FAILED_PAGE},
            connections_for=lambda number: (
                [{"connection_type": "bolted", "confidence": 96,
                  "bolts": [{"size": "M20", "grade": "8.8", "quantity": 4}],
                  "connects_members": [f"M{number}"]}]
                if number == FAILED_PAGE else []
            ),
        )
        doc.first_window()
        assert doc.repository.connections == []

        doc.retry(page_number=FAILED_PAGE)

        assert len(doc.repository.connections) == 1
        connection = doc.repository.connections[0]
        assert connection["source_page"] == FAILED_PAGE
        assert connection["source_drawing_id"] == _drawing_id(doc)
        page_rows = {row["id"] for row in doc.repository.members if row["mark"] == f"M{FAILED_PAGE}"}
        assert {link["member_id"] for link in doc.repository.connection_member_links} == page_rows


# ===========================================================================
# 4. A retry that fails again — not a terminal state.
# ===========================================================================
class TestAFailedRetry:
    def _failed_retry(self, project, production, monkeypatch):
        """The page failed in its window, and the read of it alone fails too.

        `fails_again` is one-shot, so the page is still there to be read a second
        time — which is what the tests below then do.
        """
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE}, fails_again={FAILED_PAGE})
        doc.first_window()
        evidence_before = _evidence_state(doc)
        runs_before = _run_state(doc)
        result = doc.retry(page_number=FAILED_PAGE)
        return doc, result, evidence_before, runs_before

    def test_the_page_stays_exactly_as_failed_as_it_was(self, project, production, monkeypatch):
        doc, result, evidence_before, _ = self._failed_retry(project, production, monkeypatch)

        assert result["outcome"] == "PARSE_FAILED"
        assert result["resolved"] is False and result["retryable"] is True
        assert result["parse_failure_pages"] == [FAILED_PAGE]
        assert _failures_record(doc).page_numbers == (FAILED_PAGE,)
        assert _lines(doc, FAILURES_LINE_PREFIX) == [f"PDF parse failures: pages={FAILED_PAGE}"]
        record = _coverage_line(doc)
        assert (record.analysed_pages, record.parse_failed_pages) == (SHORT_PAGES, 1)
        assert not record.is_complete
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

    def test_it_invents_no_evidence_and_moves_nothing(self, project, production, monkeypatch):
        doc, _, evidence_before, _ = self._failed_retry(project, production, monkeypatch)

        assert _evidence_state(doc) == evidence_before
        assert FAILED_PAGE not in _sources(doc)
        assert doc.repository.connections == []

    def test_the_attempt_is_recorded_as_a_run_of_one_page(self, project, production, monkeypatch):
        doc, _, _, runs_before = self._failed_retry(project, production, monkeypatch)

        runs_after = _run_state(doc)
        new_runs = [run for run in runs_after["analysis_runs"].values()
                    if run["id"] not in runs_before["analysis_runs"]]
        assert len(new_runs) == 1
        updates = runs_after["analysis_run_updates"][len(runs_before["analysis_run_updates"]):]
        assert [u["id"] for u in updates] == [new_runs[0]["id"]]
        assert updates[0]["status"] == "completed"
        assert updates[0]["pages_processed"] == 1
        assert updates[0]["total_pages"] == SHORT_PAGES
        assert f"page {FAILED_PAGE}" in updates[0]["error_message"]

    def test_the_page_can_be_retried_again_and_then_succeeds(self, project, production, monkeypatch):
        """A failed retry must not be permanently terminal: the record it left is
        exactly the record it started with, so the next attempt is an ordinary
        attempt."""
        doc, _, _, _ = self._failed_retry(project, production, monkeypatch)

        second = doc.retry(page_number=FAILED_PAGE)

        assert second["outcome"] == "PARSED" and second["resolved"] is True
        assert _coverage_line(doc).parse_failed_pages == 0
        assert _failures_record(doc).is_empty
        assert FAILED_PAGE in _sources(doc)
        assert doc.repository.projects[doc.project_id]["status"] == DONE


# ===========================================================================
# 5. Several failed pages — independent, and each blocking "done".
# ===========================================================================
class TestSeveralFailedPages:
    FAILED = (12, 37, 44)

    def _document(self, project, production, monkeypatch):
        """A 60-page drawing set read in two windows, with a failure in each."""
        doc = project(pages=60)
        calls = _install(doc, monkeypatch, production, failed={12, 37, 44})
        doc.first_window()
        doc.continue_window()
        return doc, calls

    def test_both_windows_are_read_and_every_failure_is_named(self, project, production, monkeypatch):
        doc, _ = self._document(project, production, monkeypatch)

        record = _coverage_line(doc)
        assert (record.total_pages, record.analysed_pages, record.parse_failed_pages,
                record.not_analysed_pages) == (60, 60, 3, 0)
        assert record.identity_holds and not record.is_complete
        assert _failures_record(doc).page_numbers == self.FAILED
        assert _lines(doc, FAILURES_LINE_PREFIX) == ["PDF parse failures: pages=12,37,44"]
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW
        for page_number in self.FAILED:
            assert page_number not in _sources(doc)

    def test_retrying_one_failure_moves_only_that_one(self, project, production, monkeypatch):
        doc, calls = self._document(project, production, monkeypatch)

        result = doc.retry(page_number=37)

        assert result["outcome"] == "PARSED"
        assert _failures_record(doc).page_numbers == (12, 44)
        assert _coverage_line(doc).parse_failed_pages == 2
        assert _coverage_line(doc).analysed_pages == 60
        assert 37 in _sources(doc)
        assert 12 not in _sources(doc) and 44 not in _sources(doc)
        assert result["parse_failure_pages"] == [12, 44]
        assert calls[-1] == {"first_page": 37, "max_pages": 1, "store_page_image": False}

    def test_the_project_is_blocked_until_every_named_page_is_resolved(self, project, production, monkeypatch):
        doc, _ = self._document(project, production, monkeypatch)

        doc.retry(page_number=44)
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW
        assert _coverage_line(doc).parse_failed_pages == 2

        doc.retry(page_number=12)
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW
        assert _coverage_line(doc).parse_failed_pages == 1

        doc.retry(page_number=37)
        assert _coverage_line(doc).parse_failed_pages == 0
        assert _failures_record(doc).is_empty
        assert _sources(doc) == list(range(1, 61))
        assert doc.repository.projects[doc.project_id]["status"] == DONE

    def test_no_evidence_is_persisted_twice(self, project, production, monkeypatch):
        doc, _ = self._document(project, production, monkeypatch)
        for page_number in self.FAILED:
            doc.retry(page_number=page_number)

        marks = [row["mark"] for row in doc.repository.members]
        assert len(marks) == len(set(marks)) == 60
        assert sorted(row["source_page"] for row in doc.repository.members) == list(range(1, 61))
        assert len(doc.repository.review_items) == 60
        assert doc.repository.project_summary["total_members"] == 60

    def test_each_retry_reads_only_its_own_page(self, project, production, monkeypatch):
        doc, calls = self._document(project, production, monkeypatch)
        for page_number in (37, 12, 44):
            doc.retry(page_number=page_number)

        assert [(c["first_page"], c["max_pages"]) for c in calls] == [
            (1, WINDOW_SIZE), (31, WINDOW_SIZE), (37, 1), (12, 1), (44, 1),
        ]


# ===========================================================================
# 6. Idempotency — a page that already has an answer is refused before any read.
# ===========================================================================
class TestIdempotency:
    def test_retrying_a_page_that_was_never_failed_is_refused_before_any_read(
        self, project, production, monkeypatch,
    ):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = _evidence_state(doc)
        runs_before = _run_state(doc)
        reads_before = len(calls)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=5)

        code, _ = _refused(refused)
        assert code == "RETRY_PAGE_NOT_FAILED"
        assert len(calls) == reads_before
        assert _evidence_state(doc) == before
        assert _run_state(doc) == runs_before

    def test_a_repeated_successful_retry_does_not_duplicate_anything(
        self, project, production, monkeypatch,
    ):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        doc.retry(page_number=FAILED_PAGE)
        before = _evidence_state(doc)
        runs_before = _run_state(doc)
        reads_before = len(calls)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "RETRY_PAGE_NOT_FAILED"
        assert len(calls) == reads_before
        assert _evidence_state(doc) == before
        assert _run_state(doc) == runs_before
        assert len(doc.repository.members) == SHORT_PAGES

    def test_evidence_already_persisted_for_that_exact_page_is_refused(
        self, project, production, monkeypatch,
    ):
        """The crash case: an earlier attempt persisted the page's evidence and
        did not live to clear the record. The record and the evidence disagree,
        and the retry stops rather than inserting a second copy. Identity is the
        drawing AND the page — a row that merely looks similar is not evidence."""
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        doc.repository.members.append({
            "id": "m-crash", "project_id": doc.project_id, "mark": f"M{FAILED_PAGE}",
            "section_name": "310UB46.2", "review_status": "extracted", "total_weight_kg": 0.0,
            "source_page": FAILED_PAGE, "source_drawing_id": _drawing_id(doc),
        })
        before = _evidence_state(doc)
        reads_before = len(calls)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "RETRY_EVIDENCE_ALREADY_PERSISTED"
        assert len(calls) == reads_before
        assert _evidence_state(doc) == before

    def test_a_mark_already_persisted_by_another_page_is_refused_after_the_read(
        self, project, production, monkeypatch,
    ):
        """A mark this project already carries is not this page's member to
        decide: two rows with one mark may be one member detailed on two sheets or
        two members entirely. Refused rather than merged, and refused before any
        write — though the read itself has already happened, so it is a background
        refusal rather than a synchronous one (see the J17 report)."""
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        # A row with the same mark, read from a DIFFERENT page: not evidence for
        # the retried page, but an identity collision all the same.
        doc.repository.members.append({
            "id": "m-other", "project_id": doc.project_id, "mark": f"M{FAILED_PAGE}",
            "section_name": "310UB46.2", "review_status": "extracted", "total_weight_kg": 0.0,
            "source_page": 9, "source_drawing_id": _drawing_id(doc),
        })
        before = _evidence_state(doc)
        runs_before = _run_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "RETRY_MEMBER_MARK_COLLISION"
        assert len(calls) == 2, "the page was read before the collision was known"
        assert calls[1]["first_page"] == FAILED_PAGE
        assert _evidence_state(doc) == before
        assert _run_state(doc) == runs_before


# ===========================================================================
# 7. Source identity and the refusals decided from persisted state.
# ===========================================================================
class TestRefusals:
    def test_the_wrong_source_file_is_refused(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = _evidence_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            production.pipeline.plan_retry(
                doc.project_id, "somebody-else/other.pdf", page_number=FAILED_PAGE,
            )

        assert _refused(refused)[0] == "CONTINUATION_SOURCE_MISMATCH"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_source_whose_page_count_changed_is_refused(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = _evidence_state(doc)
        # The file at the recorded path is no longer the document the record
        # describes: same path, different page count, refused before it is read.
        j16._write_pdf(doc.source, SHORT_PAGES - 2)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "CONTINUATION_DOCUMENT_TOTAL_MISMATCH"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_an_unknown_project_is_refused(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()

        with pytest.raises(doc.refused_retry) as refused:
            production.pipeline.plan_retry(
                "no-such-project", doc.storage_path, page_number=FAILED_PAGE,
            )

        assert _refused(refused)[0] == "CONTINUATION_DRAWING_SET_UNRESOLVED"
        assert len(calls) == 1

    def test_a_page_outside_the_document_is_refused(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = _evidence_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=SHORT_PAGES + 1)

        assert _refused(refused)[0] == "RETRY_PAGE_BEYOND_DOCUMENT"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_page_number_that_is_not_a_page_number_is_refused(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = _evidence_state(doc)

        for page_number in (None, "4", 0, -1, True, 4.0):
            with pytest.raises(doc.refused_retry) as refused:
                doc.retry(page_number=page_number)
            assert _refused(refused)[0] == "RETRY_PAGE_MALFORMED", page_number

        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_failure_count_without_page_numbers_cannot_be_retried(self, project, production, monkeypatch):
        """A record written between J15 and J17 — or by something other than this
        codebase — states how many pages failed and not which. The count still
        keeps the project out of "done"; no page can be identified for retry."""
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        warnings = [w for w in _warnings(doc) if not w.startswith(FAILURES_LINE_PREFIX)]
        doc.repository.projects[doc.project_id]["warnings"] = warnings
        before = _evidence_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "RETRY_FAILURES_UNRECORDED"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_continuation_is_refused_when_the_failures_are_unrecorded(self, project, production, monkeypatch):
        """The window path cannot extend a record whose earlier failures are not
        named: a failures line naming only this window's pages would contradict
        the count it rides beside."""
        doc = project(pages=LONG_PAGES)
        _install(doc, monkeypatch, production, failed={17})
        doc.first_window()
        doc.repository.projects[doc.project_id]["warnings"] = [
            w for w in _warnings(doc) if not w.startswith(FAILURES_LINE_PREFIX)
        ]
        before = _evidence_state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        assert _refused(refused)[0] == "CONTINUATION_FAILURES_UNRECORDED"
        assert _evidence_state(doc) == before

    def test_two_halves_that_disagree_are_refused_by_both_paths(self, project, production, monkeypatch):
        doc = project(pages=LONG_PAGES)
        calls = _install(doc, monkeypatch, production, failed={17})
        doc.first_window()
        contradicted = [f"{FAILURES_LINE_PREFIX}pages=17,18" if w.startswith(FAILURES_LINE_PREFIX) else w
                        for w in _warnings(doc)]
        doc.repository.projects[doc.project_id]["warnings"] = contradicted
        before = _evidence_state(doc)
        reads_before = len(calls)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=17)
        assert _refused(refused)[0] == "RETRY_FAILURES_CONFLICT"

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()
        assert _refused(refused)[0] == "CONTINUATION_RECORD_CONFLICT"

        assert len(calls) == reads_before
        assert _evidence_state(doc) == before

    def test_a_record_this_codebase_cannot_read_names_no_page(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        doc.repository.projects[doc.project_id]["warnings"] = [
            f"{FAILURES_LINE_PREFIX}pages=4,4" if w.startswith(FAILURES_LINE_PREFIX) else w
            for w in _warnings(doc)
        ]
        before = _evidence_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "RETRY_FAILURES_UNRECORDED"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_project_with_no_coverage_record_has_no_failed_page(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        doc.repository.projects[doc.project_id]["warnings"] = ["Some other warning."]
        before = _evidence_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "CONTINUATION_NO_COVERAGE_RECORD"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_stale_drawing_set_counter_is_refused_not_repaired(self, project, production, monkeypatch):
        """A retry re-states the drawing set's counters from the coverage record.
        A set whose counters already disagree with it would be silently repaired
        by that; it is refused instead — the same rule the window path applies."""
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        drawing_set = next(iter(doc.repository.drawing_sets.values()))
        drawing_set["pages_analysed"] = SHORT_PAGES - 1
        before = _evidence_state(doc)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "CONTINUATION_RECORD_CONFLICT"
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_read_that_returns_a_different_page_is_refused(self, project, production, monkeypatch):
        """Evidence may only be persisted under the page it was read from, so a
        reader that answered with a different page writes nothing at all."""
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        before = _evidence_state(doc)

        def wrong_page(filepath, user_id, project_id, drawing_id, max_pages, *,
                       first_page=1, store_page_image=True):
            other = FAILED_PAGE + 1
            return [j16._page(other, j16._default_members(other))]

        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", wrong_page)

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "RETRY_READ_MISMATCH"
        assert _evidence_state(doc) == before


# ===========================================================================
# 8. J16 — the continuation contract is neither weakened nor bypassed.
# ===========================================================================
class TestTheContinuationContractIsIntact:
    def test_a_parse_failure_does_not_make_a_continuation_impossible(
        self, project, production, monkeypatch,
    ):
        """J16's contract refuses a continuation only when no page is left to
        read. An outstanding parse failure is not that: the pages after it are
        still unread, and refusing to read them would strand the drawing set. The
        failure is recorded, carried forward, named, and repairable — it is not
        silent, and it is not a reason to stop reading."""
        doc = project(pages=LONG_PAGES)
        calls = _install(doc, monkeypatch, production, failed={17})
        doc.first_window()
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

        result = doc.continue_window()

        assert result["window"] == {"first_page": 31, "last_page": 60, "size": WINDOW_SIZE}
        assert result["parse_failed_pages"] == 1
        assert _failures_record(doc).page_numbers == (17,)
        assert calls == [
            {"first_page": 1, "max_pages": WINDOW_SIZE, "store_page_image": True},
            {"first_page": 31, "max_pages": WINDOW_SIZE, "store_page_image": True},
        ]

    def test_a_continuation_cannot_skip_the_pages_a_failure_is_on(self, project, production, monkeypatch):
        doc = project(pages=LONG_PAGES)
        calls = _install(doc, monkeypatch, production, failed={17})
        doc.first_window()
        before = _evidence_state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window(requested_first_page=61, requested_last_page=75)

        code, detail = _refused(refused)
        assert code == "WINDOW_SKIP"
        assert "31" in detail
        assert len(calls) == 1
        assert _evidence_state(doc) == before

    def test_a_retry_does_not_move_the_boundary_a_continuation_is_derived_from(
        self, project, production, monkeypatch,
    ):
        """The page was already inside `analysed`, so re-reading it cannot advance
        the boundary: the next window before the retry is the next window after
        it, to the page."""
        doc = project(pages=LONG_PAGES)
        _install(doc, monkeypatch, production, failed={17})
        doc.first_window()
        before = production.pipeline.plan_continuation(doc.project_id, doc.storage_path)

        doc.retry(page_number=17)

        after = production.pipeline.plan_continuation(doc.project_id, doc.storage_path)
        assert (before.window.first_page, before.window.last_page) == (31, 60)
        assert (after.window.first_page, after.window.last_page) == (31, 60)
        assert after.previous_coverage.analysed_pages == 30
        assert after.previous_failures.is_empty

    def test_the_continuation_proceeds_deterministically_after_the_failure_is_repaired(
        self, project, production, monkeypatch,
    ):
        doc = project(pages=LONG_PAGES)
        calls = _install(doc, monkeypatch, production, failed={17})
        doc.first_window()

        assert doc.retry(page_number=17)["resolved"] is True

        second = doc.continue_window()
        third = doc.continue_window()

        assert [second["window"], third["window"]] == [
            {"first_page": 31, "last_page": 60, "size": 30},
            {"first_page": 61, "last_page": 75, "size": 15},
        ]
        record = _coverage_line(doc)
        assert (record.total_pages, record.analysed_pages, record.parse_failed_pages,
                record.not_analysed_pages) == (LONG_PAGES, LONG_PAGES, 0, 0)
        assert record.is_complete
        assert _failures_record(doc).is_empty
        assert _sources(doc) == list(range(1, LONG_PAGES + 1))
        assert doc.repository.projects[doc.project_id]["status"] == DONE
        assert [(c["first_page"], c["max_pages"]) for c in calls] == [
            (1, WINDOW_SIZE), (17, 1), (31, WINDOW_SIZE), (61, 15),
        ]

    def test_the_j16_window_vocabulary_is_untouched(self, production):
        import app.validation.page_windows as windows

        for name in j16.REFUSAL_CODES:
            assert getattr(windows, name) == name
            assert name in windows.__all__


# ===========================================================================
# 9. The HTTP surface — the mechanism production actually calls.
# ===========================================================================
class _Downloads:
    """The one storage call a background task makes, recorded.

    A background task downloads the file it was pointed at and hands it to the
    pipeline; nothing else about the client is needed to test that.

    Since J37C-8Q the task reads through `app.storage_download`, which reaches the
    object endpoint through storage3's own bucket proxy rather than through
    `download()`. So this stands in for the proxy and the HTTP client under it — the
    substitution boundary is the NETWORK, not the download call — and `paths` still
    records the object path the task asked for, which is what these tests assert on.
    """

    def __init__(self, content=b""):
        self.content = content
        self.paths = []
        self.storage = self
        self.id = "uploads"
        self._base_url = URL("https://storage.invalid/storage/v1/")
        self._headers = httpx.Headers({"Authorization": "Bearer test-not-a-real-key"})
        self._client = httpx.Client(transport=httpx.MockTransport(self._serve))

    def _serve(self, request):
        self.paths.append(unquote(request.url.path.split("/object/uploads/", 1)[1]))
        return httpx.Response(200, content=self.content)

    def from_(self, bucket):
        assert bucket == "uploads", bucket
        return self


class TestTheHttpSurface:
    def _client(self, production, monkeypatch, repository, project_id):
        """A client authenticated AS THIS PROJECT'S OWNER (Milestone J19).

        The retry route now requires the caller's Supabase Auth access token, so
        the harness mints a genuine ES256 token whose subject is the project's own
        `user_id`. The retry these tests drive is unchanged.
        """
        from fastapi.testclient import TestClient

        auth.install(monkeypatch, production)
        monkeypatch.setattr(
            production.main, "supabase",
            j16._ProjectClient(repository.projects[project_id]),
        )
        row = repository.projects[project_id]
        return TestClient(production.main.app, headers=auth.headers_for(row))

    def test_the_retry_endpoint_is_registered(self, production):
        paths = {route.path: route for route in production.main.app.routes}
        assert "/retry-extraction/{project_id}" in paths
        assert "POST" in paths["/retry-extraction/{project_id}"].methods
        for path in ("/health", "/extract/{project_id}", "/continue-extraction/{project_id}",
                     "/generate-report/{project_id}"):
            assert path in paths

    def test_a_failed_page_is_accepted_and_dispatched_with_its_page(
        self, project, production, monkeypatch,
    ):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        dispatched = []
        monkeypatch.setattr(
            production.main, "run_retry",
            lambda *args, **kwargs: dispatched.append((args, kwargs)),
        )
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/retry-extraction/{doc.project_id}?page={FAILED_PAGE}")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "status": "retry_started", "project_id": doc.project_id, "page_number": FAILED_PAGE,
        }
        assert len(dispatched) == 1
        assert dispatched[0][0][0] == doc.project_id
        assert dispatched[0][0][-1] == FAILED_PAGE

    def test_a_page_that_was_not_failed_is_refused_by_name(
        self, project, production, monkeypatch,
    ):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/retry-extraction/{doc.project_id}?page=5")

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == "RETRY_PAGE_NOT_FAILED"

    def test_a_missing_page_is_refused_by_name(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/retry-extraction/{doc.project_id}")

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == "RETRY_PAGE_MALFORMED"

    def test_a_page_that_is_not_a_number_is_rejected_before_the_handler(
        self, project, production, monkeypatch,
    ):
        """The framework's own request validation answers a `page` that is not a
        whole number (422) — the one refusal that is not this contract's, and the
        reason it is documented rather than re-implemented."""
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/retry-extraction/{doc.project_id}?page=four")

        assert response.status_code == 422

    def test_a_project_that_is_not_a_pdf_is_refused(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        doc.repository.projects[doc.project_id]["source_format"] = "DXF"
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/retry-extraction/{doc.project_id}?page={FAILED_PAGE}")

        assert response.status_code == 400
        assert len(calls) == 1

    def test_a_missing_project_is_a_404(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        doc.first_window()
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post("/retry-extraction/no-such-project?page=4")

        assert response.status_code == 404

    def test_the_background_task_refuses_a_non_pdf_before_it_downloads(
        self, production, parse_failures,
    ):
        """The background task re-decides the same way the request did, and its
        first decision is made before anything is downloaded — the client this
        test does not replace is never asked for the file."""
        with pytest.raises(parse_failures.RetryRefused) as refused:
            production.main.run_retry("pid", "user/x.pdf", "DXF", "user", 4)

        assert refused.value.code == "CONTINUATION_SOURCE_MISMATCH"

    def test_a_resolved_retry_regenerates_the_report_and_a_failed_one_does_not(
        self, project, production, monkeypatch,
    ):
        """A retry that changed nothing would otherwise overwrite the project's
        existing report with an identical one."""
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        generated = []
        monkeypatch.setattr(
            production.main, "build_and_store_report",
            lambda project_id, user_id: generated.append(project_id),
        )
        # The background task's own two collaborators: the one storage call it
        # makes before the pipeline sees the file, and the pipeline function —
        # which is replaced here because what is under test is the ORDER of the
        # report against the outcome, not the read (that is tested above).
        monkeypatch.setattr(production.main, "supabase", _Downloads())
        monkeypatch.setattr(production.main, "retry_pdf_page", lambda *a, **k: {"resolved": False})

        production.main.run_retry(
            doc.project_id, doc.storage_path, "PDF", "j16-user", FAILED_PAGE,
        )
        assert generated == []

        monkeypatch.setattr(production.main, "retry_pdf_page", lambda *a, **k: {"resolved": True})
        production.main.run_retry(
            doc.project_id, doc.storage_path, "PDF", "j16-user", FAILED_PAGE,
        )
        assert generated == [doc.project_id]
        # One download per attempt, and every attempt pointed at the recorded
        # source and no other file.
        assert production.main.supabase.paths == [doc.storage_path] * 2


# ===========================================================================
# 10. Scope — what this milestone did not do.
# ===========================================================================
class TestScopeAndPurity:
    def _written_columns(self, function_name):
        """Every keyword this function passes to the repository's `update_*` calls.

        Scanned to the call's own closing parenthesis rather than to a fixed
        indentation, because a retry states its columns from two branches at two
        depths — and a column missed by the scan would be a column this test
        could not have caught.
        """
        source = PIPELINE_PATH.read_text(encoding="utf-8")
        body = source.split(f"def {function_name}", 1)[1]
        body = body.split("\n    return {", 1)[0]
        written = set()
        for match in re.finditer(
            r"repo\.(update_drawing_set|update_analysis_run|update_project_summary)\(",
            body,
        ):
            written |= set(re.findall(r"(\w+)=", _call_text(body, match.end() - 1)))
        return written

    def test_the_retry_writes_no_column_a_previous_path_had_not(self):
        """Which is why no migration accompanies this milestone: every column the
        retry states is one the window path or the ≤cap path already wrote."""
        cap = self._written_columns("parse_pdf_and_save")
        window = self._written_columns("continue_pdf_extraction")
        retry = self._written_columns("retry_pdf_page")

        # A retry states exactly what a window states — the same counters, the
        # same record, the same one-call commit — plus the one column the ≤cap
        # path already uses to record an attempt that did not succeed.
        assert retry == window | {"error_message"}, retry
        assert "error_message" in cap, sorted(cap)
        assert retry <= cap | window, retry - (cap | window)

    def test_no_migration_was_added(self):
        """J17 added none: a parse-failed page is a page of the document, and the retry
        reads the record the coverage line already wrote.

        J22 later added a fourth migration, and it is not this milestone's either: a
        parse-failed page is NOT review state, so the retry contract is untouched by it.

        J23 added a fifth. It IS this milestone's subject — a retry records its own
        reading, and a failed retry now leaves a capture where before it left nothing —
        but not this milestone's CLAIM, because it changes no retry rule: the record the
        retry reads (`projects.warnings`), the refusal it issues and the run it creates are
        all exactly as they were. What it adds is a record of the attempt, and a record is
        not a decision.

        J28 added a sixth. It is not this milestone's subject and not its claim either:
        it records PDF annotation occurrences read from the drawing, the retry neither
        writes nor reads one, and the record the retry reads (`projects.warnings`), the
        refusal it issues and the run it creates are still exactly as they were. (J28A is
        the bookkeeping step that recorded the name here.)

        J44 added a seventh. It is not this milestone's subject and not its claim: it places
        one project-keyed claim row and reads no page and no failure, so the record the retry
        reads (`projects.warnings`), the refusal it issues and the run it creates are still
        exactly as they were. J61 added an eighth, which reads no page and no failure either:
        it gives a source document an identity and points a drawing at it, and it writes
        nothing to `projects.warnings`, so the refusal this file pins is unmoved.

        The eight names are pinned exactly."""
        assert set(j13.MIGRATIONS) == {
            "20260924000000_j5_section_resolution_truth.sql",
            "20260924010000_j6_reference_data_identity.sql",
            "20260924020000_j8b_connection_plate_evidence_nullability.sql",
            "20260925000000_j22_connection_review_persistence.sql",
            "20260925010000_j23_page_extraction_captures.sql",
            "20260927000000_j28_pdf_annotation_occurrences.sql",
            "20260928000000_j44_project_review_claims.sql",
            "20260929000000_j61_project_documents.sql",
            "20260929010000_j66_field_evidence_citations.sql",
        }
        migrations = sorted(path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert migrations == sorted(j13.MIGRATIONS)

    def test_the_retry_reads_and_decides_but_the_repository_only_reads(self):
        """The J17 read returns rows and decides nothing: no UPDATE, no INSERT,
        no DELETE, and no default invented for a missing value."""
        source = REPOSITORY_PATH.read_text(encoding="utf-8")
        reader = source.split("def evidence_rows_for_page", 1)[1]
        for forbidden in (".insert(", ".update(", ".delete(", "upsert"):
            assert forbidden not in reader, forbidden

    def test_no_evidence_is_re_identified_by_resemblance(self, production, parse_failures):
        """Member identity across pages has no merge rule in production, so the
        retry refuses a collision rather than matching one: neither the contract
        nor the executor imports, or calls, anything that would compare two marks
        approximately. (The prose says so too, which is why this reads the
        imports rather than the text.)"""
        fuzzy = ("difflib", "fuzz", "levenshtein", "textdistance", "jellyfish")
        for path in (PARSE_FAILURES_PATH, PIPELINE_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
            assert not [name for name in imported
                        if any(token in name.lower() for token in fuzzy)], path.name
            assert not [node for node in ast.walk(tree)
                        if isinstance(node, ast.Name)
                        and any(token in node.id.lower() for token in fuzzy)], path.name

    def test_nothing_here_became_a_cad_engine_subsystem(self):
        """No module of the extraction pipeline reaches into the CAD engine.

        J47's route does, and only in the sense this test is actually about: it imports
        five REFUSAL TYPES so it can answer each with a status, and imports no builder, no
        state and no module object. That exception is stated as the exact set of names
        below rather than as a blanket allowance, and it is confined to `main.py` — the
        pipeline's own two modules still name the CAD engine nowhere at all."""
        for path in (PARSE_FAILURES_PATH, PIPELINE_PATH):
            source = path.read_text(encoding="utf-8")
            assert "cad_engine" not in source, path.name
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                             else [node.module or ""])
                    assert not any(name.startswith("app.cad_engine") for name in names), path.name

    def test_the_one_cad_engine_import_in_the_app_is_refusal_types_only(self):
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        imported = [
            (node.module, tuple(alias.name for alias in node.names))
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("app.cad_engine")
        ]
        assert len(imported) == 1, imported
        module, names = imported[0]
        assert module == "app.cad_engine.project_workflow", module
        for name in names:
            assert name.endswith("Error"), name
        assert MAIN_PATH.read_text(encoding="utf-8").count("cad_engine") == 1

    def test_the_retry_vocabulary_is_the_contracts_own(self, parse_failures):
        for name in RETRY_CODES:
            assert getattr(parse_failures, name) == name
            assert name in parse_failures.__all__

    def test_the_contract_reuses_j16_rather_than_restating_its_rules(self):
        """The rules that ARE J16's — a source file, an ambiguous drawing set, a
        coverage record, a stale counter — keep J16's names and J16's module:
        they are imported or read, not redefined, so the retry and the
        continuation cannot call the same state two different things."""
        source = PARSE_FAILURES_PATH.read_text(encoding="utf-8")
        assert "from app.validation.page_windows import COVERAGE_UNUSABLE" in source
        for name in REUSED_J16_CODES:
            assert f'{name} = "{name}"' not in source, name
        windows = PAGE_WINDOWS_PATH.read_text(encoding="utf-8")
        for name in REUSED_J16_CODES:
            assert f'{name} = "{name}"' in windows, name

    def test_the_retry_refuses_a_state_j16_already_names(self, project, production, monkeypatch):
        """A retry of a page in an ambiguous drawing set is refused with J16's own
        code for it — the retry did not invent a second name for that state."""
        doc = project(pages=SHORT_PAGES)
        calls = _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        # A second drawing set: which document this page belongs to is ambiguous.
        doc.repository.create_drawing_set(doc.project_id, "a second set")

        with pytest.raises(doc.refused_retry) as refused:
            doc.retry(page_number=FAILED_PAGE)

        assert _refused(refused)[0] == "CONTINUATION_DRAWING_SET_UNRESOLVED"
        assert len(calls) == 1

    def test_no_new_dependency_was_introduced(self):
        requirements = (REPO / "requirements.txt").read_text(encoding="utf-8").lower()
        for absent in ("fuzzywuzzy", "thefuzz", "rapidfuzz", "psycopg", "sqlalchemy"):
            assert absent not in requirements

    def test_the_harness_makes_no_network_call(self, project, production, monkeypatch):
        doc = project(pages=SHORT_PAGES)
        _install(doc, monkeypatch, production, failed={FAILED_PAGE})
        doc.first_window()
        doc.retry(page_number=FAILED_PAGE)

        client = production.matcher_module.supabase
        assert isinstance(client, authority._FakeSupabaseClient)
        assert client.executed > 0, "the matcher never read the reference index"
