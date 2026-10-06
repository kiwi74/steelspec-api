"""
J16 — PDF PRODUCTION CONTINUATION.

A PDF drawing set larger than `MAX_PDF_PAGES` used to have exactly one honest
outcome in production: the first window was read, and (since J15) the pages that
were never reached were recorded as NOT_ANALYSED. There was no way to read them.
Re-POSTing /extract was not a way either — it creates a SECOND drawing set, a
second drawing and a second analysis run, and re-inserts every member and
connection row for the same project.

J16 adds the missing mechanism, and nothing else: the next window of pages is
read, once, in a controlled step, and the committed coverage record advances by
exactly what was read.

    window          a closed 1-based page range, at most MAX_PDF_PAGES wide
    boundary        analysed_pages + 1 — the next page nobody has read
    the contract    a window is accepted only if it IS the next one

WHAT THIS FILE PROVES
---------------------
* A drawing set inside the cap is untouched: same pages, same rows, same status,
  and a continuation of it is refused because there is nothing left to read.
* A longer drawing set reads its first window, records the pages it did not
  reach, stays in "review" rather than "failed", and states the next boundary.
* Each continuation reads ONLY its window, persists ONLY that window's evidence,
  and leaves the earlier windows' evidence where it is.
* Coverage accumulates truthfully and the identity still holds at every step;
  the first window's counters are carried forward rather than restated.
* A window is refused by name when it is backwards, overlapping, skipping,
  beyond the document, wider than one extraction, malformed, states a different
  page count, or when the document is already fully read.
* A refused window reads nothing, writes nothing and moves nothing — proven by
  comparing the whole persisted shape before and after.
* A repeated continuation does not duplicate one engineering row.
* A different source file, an ambiguous drawing set, a missing coverage record
  and a record that disagrees with itself are all refused before anything is
  read.
* A page whose response could not be parsed is recorded through J15's coverage
  contract, contributes no evidence, keeps the project out of "done" — and the
  WINDOW that contained it cannot be read again, because its other pages are
  already persisted and the boundary has moved past them. (Reading ONE page
  again is Milestone J17's: `app/validation/parse_failures.py` names the pages
  that failed, and `POST /retry-extraction/{project_id}?page=N` reads one.)
* J13 remains the only authority over the project's status: the persisted status
  is derived from all the evidence, old and new.

THE BOUNDARY
------------
Every test but the pure-contract class drives the GENUINE production functions —
`parse_pdf_and_save` and `continue_pdf_extraction` — over a REAL PDF written by
pypdf, with the real `page_count_of` (it reads that document), the real
`SectionMatcher` (over the pinned reference index the accepted Step C harness
uses, so nothing here goes near the network), the real validation, the real row
construction and the real J13 derivation. Only the two EXTERNAL stages are
replaced: the AI call, by a window-honest stand-in that returns exactly the
pages a real renderer would return for the window it is asked for (and clamps at
the end of the document exactly as pdf2image does), and the reference read
behind the matcher, by the pinned index.

Nothing here reaches the network, needs an AI key, or writes production data.
The doubles and fixtures are J4's, J13's and the section-authority harness',
reused rather than restated.
"""

from __future__ import annotations

import ast
import copy
import itertools
import re
import types

import pytest

from app.engineering_data import page_extraction_capture as captures

from tests import production_review_auth as auth
from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_production_section_authority as authority

production = j4.production

REPO = j13.REPO
EXACT_TOKEN = j13.EXACT_TOKEN
DONE = j13.DONE
REVIEW = j13.REVIEW
ACCEPTED_STATUS = j13.ACCEPTED_STATUS

PIPELINE_PATH = REPO / "app" / "pipeline.py"
WINDOWS_PATH = REPO / "app" / "validation" / "page_windows.py"
MAIN_PATH = REPO / "app" / "main.py"

# The brief's own worked example: a 35-page drawing set, read in windows of 30.
DOCUMENT_PAGES = 35
WINDOW_SIZE = 30
FIRST_WINDOW = (1, 30)
SECOND_WINDOW = (31, 35)

# A three-window document, to prove the middle window and the final partial one
# are ordinary cases rather than the shape of one example.
LONG_DOCUMENT_PAGES = 75
LONG_WINDOWS = ((1, 30), (31, 60), (61, 75))

REFUSAL_CODES = (
    "COVERAGE_UNUSABLE",
    "CONTINUATION_DOCUMENT_TOTAL_MISMATCH",
    "CONTINUATION_DRAWING_SET_UNRESOLVED",
    "CONTINUATION_MEMBER_MARK_COLLISION",
    "CONTINUATION_NO_COVERAGE_RECORD",
    "CONTINUATION_RECORD_CONFLICT",
    "CONTINUATION_SOURCE_MISMATCH",
    "WINDOW_ALREADY_COMPLETE",
    "WINDOW_BACKWARD",
    "WINDOW_BEYOND_DOCUMENT",
    "WINDOW_MALFORMED",
    "WINDOW_OVERLAP",
    "WINDOW_SKIP",
    "WINDOW_TOO_LARGE",
    "WINDOW_WRONG_TOTAL",
)

_PAGE = None


@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    """Binds the real production PageExtraction into this file's page helpers."""
    global _PAGE
    _PAGE = production.PageExtraction
    yield
    _PAGE = None


@pytest.fixture()
def windows(production):
    """The J16 continuation contract, imported under the `production` fixture's
    own configuration boundary so it is removed from sys.modules by its teardown
    like every other app module this file touches."""
    import app.validation.page_windows as windows_module

    return windows_module


@pytest.fixture()
def coverage(production):
    import app.validation.page_coverage as coverage_module

    return coverage_module


def _page(number, members, *, connections=()):
    """One genuine production `PageExtraction`, as the vision stage reports it."""
    return _PAGE(
        page_number=number,
        drawing_number="J16-DWG-001",
        drawing_title="J16 continuation",
        revision="A",
        raw_members=list(members),
        raw_connections=list(connections),
    )


def _failed_page(number):
    """What the analyzer appends when a page's response cannot be read: a page
    that WAS rendered and put to the model, whose answer could not be read."""
    return _PAGE(page_number=number, parse_failed=True)


def _default_members(number):
    """One resolving member per page, marked by the page it was read from.

    Distinct page numbers give distinct marks, which is what a real drawing set
    read in windows looks like: pages 31-35 are new pages, not repeats of pages
    1-30. A test that wants a repeated mark asks for one explicitly.
    """
    return [j4._member(f"M{number}", EXACT_TOKEN)]


def _write_pdf(path, pages):
    """A REAL PDF of `pages` blank pages, written by pypdf itself.

    The continuation derives its boundary from `page_count_of`, which reads the
    document — so the document has to be one.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


# ===========================================================================
# The test-only repository seam: writes recorded AND served back.
# ===========================================================================
class _ServingRepository(j4._RecordingRepository):
    """The production repository API answered in-process, and SERVED back.

    J4's double answers writes and records them. A continuation also READS, and
    what it reads must be what the production code wrote — not rows a test typed
    in — or the accumulation, the boundary and the duplicate protection would be
    proved against a fiction. This subclass keeps every write behaviour of that
    double and adds the five reads `app/engineering_data/repository.py` gained in
    J16, answering them from the rows this same object recorded.

    The only facts it is seeded with are the ones the UPLOAD owns (the file path,
    the source format, the owner), because the pipeline never writes those.
    Everything else — counters, status, warnings, members, connections — exists
    only because production code put it there.

    Each read returns exactly the columns its real select names, so code that
    started depending on a column its select does not name fails here instead of
    being silently served it. A created drawing set carries the live schema's own
    NOT NULL defaults (`total_pages=0`, `pages_analysed=0`), which is what the
    real insert leaves behind.
    """

    def __init__(self):
        super().__init__()
        self.projects = {}
        self.drawing_sets = {}
        self.drawings = {}
        self.analysis_runs = {}
        self.reads = []
        self._capture_instant = 0

    # --- the upload's own facts, which no pipeline function writes ---
    def seed_project(self, project_id, *, storage_path, user_id="j16-user", source_format="PDF"):
        self.projects[project_id] = {
            "id": project_id,
            "uploaded_file_path": storage_path,
            "source_format": source_format,
            "user_id": user_id,
            "status": "processing",
            "warnings": None,
            "unmatched_sections": None,
        }
        return self.projects[project_id]

    # --- writes: recorded by the base double, kept current for the reads ---
    def create_drawing_set(self, project_id, name):
        row = super().create_drawing_set(project_id, name)
        row = {**row, "status": "processing", "total_pages": 0, "pages_analysed": 0}
        self.drawing_sets[row["id"]] = row
        return row

    def update_drawing_set(self, drawing_set_id, **fields):
        super().update_drawing_set(drawing_set_id, **fields)
        self.drawing_sets[drawing_set_id].update(fields)

    def create_drawing(self, drawing_set_id, file_name, storage_path, document_id=None):
        row = super().create_drawing(drawing_set_id, file_name, storage_path, document_id)
        row = {**row, "file_name": file_name, "storage_path": storage_path, "page_count": None}
        self.drawings[row["id"]] = row
        return row

    def update_drawing_meta(self, drawing_id, page_count, drawing_number, drawing_title, revision):
        super().update_drawing_meta(drawing_id, page_count, drawing_number, drawing_title, revision)
        self.drawings[drawing_id].update({
            "page_count": page_count,
            "drawing_number": drawing_number,
            "drawing_title": drawing_title,
            "revision": revision,
        })

    # --- raw AI capture (Milestone J23): written by the base, SERVED here ---
    def insert_page_extraction_captures(self, rows):
        """Stamps `captured_at`, because that is the database's job and nobody else's.

        The base double records exactly what the writer sent, and the writer
        deliberately sends no timestamp: the column's value comes from the table's
        own `default now()`. A read therefore has to supply it, and this is the read
        side. The instant is assigned the way Postgres assigns it — one value for
        the whole statement (`now()` is transaction time, so every row of a single
        insert shares it) and a later value for a later insert. That distinction is
        load-bearing rather than cosmetic: the selection rule orders attempts by
        `(captured_at, analysis_run_id)`, so a double that gave each ROW its own
        instant would make one insert look like several separate attempts.
        """
        rows = rows or []
        self._capture_instant += 1
        instant = f"2026-01-01T00:00:{self._capture_instant:02d}+00:00"
        stamped = [{**copy.deepcopy(row), "captured_at": instant} for row in rows]
        self.order.append("insert_page_extraction_captures")
        self.captures.extend(stamped)
        return len(stamped)

    def page_extraction_captures_for_drawing(self, drawing_id):
        """Every recorded reading of one drawing, as the real select returns them.

        Exactly the persisted column list, every attempt included and none merged:
        which reading stands for a page is decided by
        `app.engineering_data.page_extraction_capture.authoritative_captures`, never
        by a double that quietly pre-selected one.
        """
        self.reads.append("page_extraction_captures_for_drawing")
        return [
            {name: copy.deepcopy(row.get(name)) for name in captures.CAPTURE_COLUMNS}
            for row in self.captures
            if row.get("drawing_id") == drawing_id
        ]

    def create_analysis_run(self, drawing_set_id, model_used):
        row = super().create_analysis_run(drawing_set_id, model_used)
        row = {**row, "drawing_set_id": drawing_set_id, "model_used": model_used, "status": "running"}
        self.analysis_runs[row["id"]] = row
        return row

    def update_analysis_run(self, analysis_run_id, **fields):
        super().update_analysis_run(analysis_run_id, **fields)
        self.analysis_runs[analysis_run_id].update(fields)

    def update_project_summary(self, project_id, **fields):
        super().update_project_summary(project_id, **fields)
        self.projects[project_id].update(fields)

    # --- reads: the J16 half of app/engineering_data/repository.py ---
    def get_project(self, project_id):
        self.reads.append("get_project")
        row = self.projects.get(project_id)
        return copy.deepcopy(row) if row else None

    def drawing_sets_for_project(self, project_id):
        self.reads.append("drawing_sets_for_project")
        columns = ("id", "project_id", "name", "status", "total_pages", "pages_analysed")
        return [
            {name: row.get(name) for name in columns}
            for row in self.drawing_sets.values() if row.get("project_id") == project_id
        ]

    def drawings_for_drawing_set(self, drawing_set_id):
        self.reads.append("drawings_for_drawing_set")
        # The columns the production select names, and J61 added `document_id` to that list.
        # Serving the older five would let a caller start depending on an unselected column
        # and pass here while seeing None in production — which is the failure this double's
        # own doctrine exists to catch.
        columns = ("id", "drawing_set_id", "file_name", "storage_path", "page_count",
                   "document_id")
        return [
            {name: row.get(name) for name in columns}
            for row in self.drawings.values() if row.get("drawing_set_id") == drawing_set_id
        ]

    def document_for_drawing(self, drawing_id):
        """The document THIS lineage names, answered from the documents the run created.

        Milestone J61. The read is two hops in production — the drawing's own
        `document_id`, then the document row — and the double keeps the same two:
        the drawing row it recorded, then the document that same run created. A
        drawing that names nothing (none created before J61, none on the DXF path)
        answers None rather than a blank row, which is the production contract.
        """
        self.reads.append("document_for_drawing")
        drawing = self.drawings.get(drawing_id) or {}
        document_id = drawing.get("document_id")
        if not document_id:
            return None
        # The columns the production select names, and no more.
        columns = ("id", "project_id", "storage_path", "file_name", "source_format",
                   "byte_size", "page_count", "content_sha256", "role", "revision_label",
                   "supersedes_document_id", "created_at")
        for row in self.documents:
            if row.get("id") == document_id:
                return {name: row.get(name) for name in columns}
        return None

    def member_rows_for_project(self, project_id):
        self.reads.append("member_rows_for_project")
        columns = ("id", "mark", "section_name", "review_status", "total_weight_kg")
        return [
            {name: row.get(name) for name in columns}
            for row in self.members if row.get("project_id") == project_id
        ]

    def connection_rows_for_project(self, project_id):
        self.reads.append("connection_rows_for_project")
        return [
            {"review_status": row.get("review_status")}
            for row in self.connections if row.get("project_id") == project_id
        ]


# ===========================================================================
# The production harness: a real document, driven through the real functions.
# ===========================================================================
@pytest.fixture()
def document(production, windows, monkeypatch, tmp_path):
    """Builds one project over a REAL PDF and drives the GENUINE production path.

        parse_pdf_and_save        -> window 1, exactly as production does it
        continue_pdf_extraction   -> the next window, and no other

    Only the AI call is replaced: `analyze_pdf_pages` returns the real
    `PageExtraction` objects a window would have produced, for the window it is
    ASKED for (`first_page`/`max_pages`), clamped at the end of the document the
    way pdf2image clamps. Every call it receives is recorded, so a test can prove
    which window was read — and that a refused request read nothing at all.
    `page_count_of` is NOT replaced: it reads the real PDF.
    """

    def build(*, pages=DOCUMENT_PAGES, members_for=None, connections_for=None,
              source_name="j16-drawings.pdf", storage_path=None, project_id=None, seed=True):
        counter = itertools.count(1)
        project_id = project_id or f"j16-project-{next(counter)}"
        storage_path = storage_path or f"j16-user/{source_name}"
        source = _write_pdf(tmp_path / source_name, pages)
        calls: list[tuple[int, int]] = []

        repository = _ServingRepository()
        if seed:
            repository.seed_project(project_id, storage_path=storage_path)

        def stand_in(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1):
            calls.append((first_page, max_pages))
            last_page = min(first_page + max_pages - 1, pages)
            return [
                _page(
                    number,
                    (members_for or _default_members)(number),
                    connections=(connections_for or (lambda n: ()))(number),
                )
                for number in range(first_page, last_page + 1)
            ]

        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", stand_in)
        # The window size is production configuration, read from the environment.
        # Pinned here so the arithmetic of these tests is the brief's own example.
        monkeypatch.setattr(production.pipeline, "MAX_PDF_PAGES", WINDOW_SIZE)
        # NO NETWORK, at all. The matcher's own reference read is answered from
        # the pinned index the accepted Step C / section-authority harness uses
        # (four rows, including the live 310UB46.2 this file's members resolve
        # to), so the ONLY external stage a test here reaches is one this fixture
        # replaced. The matcher, the validation, the row construction and the
        # J13 derivation are the genuine production code over that index.
        monkeypatch.setattr(
            production.matcher_module, "supabase",
            authority._FakeSupabaseClient(authority._pinned_index()),
        )

        return types.SimpleNamespace(
            project_id=project_id,
            source=source,
            storage_path=storage_path,
            pages=pages,
            repository=repository,
            analyzer_calls=calls,
            # The contract's own exception type, imported through the fixture so
            # this file never imports app modules at collection time.
            refused=windows.ContinuationRefused,
            first_window=lambda: production.pipeline.parse_pdf_and_save(
                source, project_id, "j16-user", storage_path,
            ),
            continue_window=lambda **kwargs: production.pipeline.continue_pdf_extraction(
                source, project_id, "j16-user", storage_path, **kwargs
            ),
            plan=lambda **kwargs: production.pipeline.plan_continuation(
                project_id, storage_path, **kwargs
            ),
        )

    return build


def _state(doc):
    """The whole persisted shape of a project, as a comparable snapshot.

    A refusal decided from persisted state must move NONE of it — not a member,
    not a connection, not a counter, not a warning, not one call to the vision
    stage.
    """
    return {**_persisted(doc), "analyzer_calls": list(doc.analyzer_calls)}


def _persisted(doc):
    """Everything a run writes, and nothing about what it read.

    A refusal decided AFTER the window was read (the cross-window mark
    collision) legitimately shows the read in `analyzer_calls`: the pages were
    fetched and then refused. What must not have moved is any persisted fact.
    """
    return {
        "members": copy.deepcopy(doc.repository.members),
        "connections": copy.deepcopy(doc.repository.connections),
        "review_items": copy.deepcopy(doc.repository.review_items),
        "analysis_run_updates": copy.deepcopy(doc.repository.analysis_run_updates),
        "drawing_set_updates": copy.deepcopy(doc.repository.drawing_set_updates),
        "drawing_meta": copy.deepcopy(doc.repository.drawing_meta),
        "project_summary": copy.deepcopy(doc.repository.project_summary),
        "project_row": copy.deepcopy(doc.repository.projects[doc.project_id]),
        "drawing_sets": copy.deepcopy(doc.repository.drawing_sets),
        "drawings": copy.deepcopy(doc.repository.drawings),
        "analysis_runs": copy.deepcopy(doc.repository.analysis_runs),
    }


def _coverage_line(doc):
    """The one persisted coverage record, read back the way production reads it."""
    from app.validation.page_coverage import coverage_from_warnings

    return coverage_from_warnings(doc.repository.projects[doc.project_id]["warnings"])


def _sources(doc):
    """Every source page the persisted member rows came from."""
    return sorted(row["source_page"] for row in doc.repository.members)


def _refused(exception_info):
    return exception_info.value.code, exception_info.value.detail


# ===========================================================================
# 1. The window contract — what the next window is, and every window that is not.
# ===========================================================================
class TestTheWindowContract:
    def _coverage(self, coverage, *, total=DOCUMENT_PAGES, analysed=0, failed=0):
        return coverage.coverage_of(
            total_pages=total,
            page_numbers=range(1, analysed + 1),
            parse_failed_page_numbers=range(1, failed + 1),
        )

    def test_the_first_window_starts_at_page_one(self, coverage, windows):
        decision = windows.check_page_window(self._coverage(coverage, analysed=0), window_size=WINDOW_SIZE)
        assert decision.accepted
        assert (decision.window.first_page, decision.window.last_page) == FIRST_WINDOW
        assert decision.window.size == WINDOW_SIZE

    def test_the_next_window_begins_at_the_boundary(self, coverage, windows):
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
        )
        assert decision.accepted
        assert (decision.window.first_page, decision.window.last_page) == SECOND_WINDOW
        assert decision.window.size == DOCUMENT_PAGES - WINDOW_SIZE

    def test_a_middle_window_of_a_long_document_is_not_the_last_one(self, coverage, windows):
        for analysed, expected in (
            (0, (1, 30)),
            (30, (31, 60)),
            (60, (61, 75)),
        ):
            decision = windows.check_page_window(
                self._coverage(coverage, total=LONG_DOCUMENT_PAGES, analysed=analysed),
                window_size=WINDOW_SIZE,
            )
            assert decision.accepted
            assert (decision.window.first_page, decision.window.last_page) == expected

    def test_a_fully_read_document_has_no_next_window(self, coverage, windows):
        decision = windows.check_page_window(
            self._coverage(coverage, total=10, analysed=10), window_size=WINDOW_SIZE,
        )
        assert not decision.accepted
        assert decision.refusal_code == windows.WINDOW_ALREADY_COMPLETE

    def test_a_document_the_first_window_covers_completely_is_complete(self, coverage, windows):
        """A drawing set inside the cap: the first window IS the whole document."""
        whole = self._coverage(coverage, total=WINDOW_SIZE, analysed=WINDOW_SIZE)
        assert whole.is_complete
        assert not windows.check_page_window(whole, window_size=WINDOW_SIZE).accepted

    def test_a_document_whose_every_page_was_reached_but_not_read_has_no_next_window(
        self, coverage, windows
    ):
        """Every page rendered, one answer unreadable: the record is incomplete
        (so it is not "complete") and there is still no page left to derive a
        window from. The answer is that there is nothing to continue — this
        milestone cannot read a page a second time."""
        nearly = self._coverage(coverage, total=35, analysed=35, failed=1)
        assert nearly.is_complete is False
        decision = windows.check_page_window(nearly, window_size=WINDOW_SIZE)
        assert decision.refusal_code == windows.WINDOW_ALREADY_COMPLETE
        assert "1 of them" in decision.detail

    def test_a_record_that_states_no_usable_page_count_has_no_boundary(self, coverage, windows):
        unknown = coverage.coverage_of(total_pages=None, page_numbers=[1, 2, 3])
        decision = windows.check_page_window(unknown, window_size=WINDOW_SIZE)
        assert not decision.accepted
        assert decision.refusal_code == windows.COVERAGE_UNUSABLE

    def test_the_window_a_caller_requests_is_the_one_it_is_held_to(self, coverage, windows):
        """Asking for a window cannot obtain a different answer from receiving it:
        the requested window is accepted only when it IS the next one."""
        previous = self._coverage(coverage, analysed=WINDOW_SIZE)
        requested = windows.check_page_window(
            previous, window_size=WINDOW_SIZE,
            requested_first_page=SECOND_WINDOW[0], requested_last_page=SECOND_WINDOW[1],
        )
        assert requested.accepted
        assert requested.window == windows.check_page_window(previous, window_size=WINDOW_SIZE).window

    @pytest.mark.parametrize("first,last", [
        (25, 29),     # entirely before the boundary: those pages are read
        (1, 30),      # the whole first window again
        (30, 30),     # the last page of it
    ])
    def test_a_backwards_window_is_refused(self, coverage, windows, first, last):
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
            requested_first_page=first, requested_last_page=last,
        )
        assert decision.refusal_code == windows.WINDOW_BACKWARD

    @pytest.mark.parametrize("first,last", [(25, 31), (30, 31), (29, 35)])
    def test_an_overlapping_window_is_refused(self, coverage, windows, first, last):
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
            requested_first_page=first, requested_last_page=last,
        )
        assert decision.refusal_code == windows.WINDOW_OVERLAP

    def test_a_skipping_window_is_refused(self, coverage, windows):
        """Pages 31-32 would be left behind by a window that starts at 33."""
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
            requested_first_page=33, requested_last_page=35,
        )
        assert decision.refusal_code == windows.WINDOW_SKIP
        assert "31-32" in decision.detail

    def test_a_window_past_the_end_of_the_document_is_refused(self, coverage, windows):
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
            requested_first_page=31, requested_last_page=40,
        )
        assert decision.refusal_code == windows.WINDOW_BEYOND_DOCUMENT

    def test_a_window_wider_than_one_extraction_is_refused(self, coverage, windows):
        decision = windows.check_page_window(
            self._coverage(coverage, total=200, analysed=30), window_size=WINDOW_SIZE,
            requested_first_page=31, requested_last_page=61,
        )
        assert decision.refusal_code == windows.WINDOW_TOO_LARGE

    @pytest.mark.parametrize("first,last", [
        (31, 30),      # the first page after the last
        (0, 30),       # not a page number
        (-1, 30),
        ("31", 35),    # not a number at all
        (31, "35"),
        (True, 35),    # `True == 1`, which would read as page one
        (None, 30),
    ])
    def test_a_malformed_window_is_refused(self, coverage, windows, first, last):
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
            requested_first_page=first, requested_last_page=last,
        )
        assert decision.refusal_code == windows.WINDOW_MALFORMED

    def test_a_window_for_a_different_document_is_refused(self, coverage, windows):
        decision = windows.check_page_window(
            self._coverage(coverage, analysed=WINDOW_SIZE), window_size=WINDOW_SIZE,
            requested_total_pages=30,
        )
        assert decision.refusal_code == windows.WINDOW_WRONG_TOTAL

    def test_an_unusable_window_size_is_a_configuration_error(self, coverage, windows):
        with pytest.raises(ValueError):
            windows.check_page_window(self._coverage(coverage), window_size=0)

    def test_a_window_read_in_full_advances_the_record(self, coverage, windows):
        previous = self._coverage(coverage, analysed=WINDOW_SIZE, failed=2)
        window = windows.PageWindow(*SECOND_WINDOW)
        accumulated = windows.accumulate_coverage(
            previous, window=window,
            analysed_page_numbers=range(window.first_page, window.last_page + 1),
        )
        assert (accumulated.total_pages, accumulated.analysed_pages,
                accumulated.parse_failed_pages, accumulated.not_analysed_pages) == (
            DOCUMENT_PAGES, DOCUMENT_PAGES, 2, 0)
        assert accumulated.identity_holds
        assert accumulated.is_complete is False      # two pages could not be read

    def test_a_window_read_in_full_with_no_failures_completes_the_record(self, coverage, windows):
        accumulated = windows.accumulate_coverage(
            self._coverage(coverage, analysed=WINDOW_SIZE),
            window=windows.PageWindow(*SECOND_WINDOW),
            analysed_page_numbers=range(SECOND_WINDOW[0], SECOND_WINDOW[1] + 1),
        )
        assert accumulated.is_complete
        assert accumulated.not_analysed_pages == 0

    @pytest.mark.parametrize("pages,failed", [
        ([31, 32, 33], ()),            # a page of the window never came back
        ([31, 32, 33, 34, 35, 36], ()),  # more pages than the window covers
        ([31, 32, 33, 34, 34], ()),    # the same page twice
        ([30, 31, 32, 33, 34], ()),     # a page before the window
        ([31, 32, 33, 34, 35], (30,)),  # a failure on a page never analysed
    ])
    def test_a_window_that_was_not_read_in_full_is_never_accumulated(
        self, coverage, windows, pages, failed
    ):
        """A window is added to the record only when it was read in full: a
        record that can only count cannot have a hole in the middle of it."""
        with pytest.raises(ValueError):
            windows.accumulate_coverage(
                self._coverage(coverage, analysed=WINDOW_SIZE),
                window=windows.PageWindow(*SECOND_WINDOW),
                analysed_page_numbers=pages,
                parse_failed_page_numbers=failed,
            )

    def test_a_complete_or_unusable_record_cannot_be_accumulated_onto(self, coverage, windows):
        for previous in (
            self._coverage(coverage, total=10, analysed=10),
            coverage.coverage_of(total_pages=None, page_numbers=[1]),
        ):
            with pytest.raises(ValueError):
                windows.accumulate_coverage(
                    previous, window=windows.PageWindow(11, 12), analysed_page_numbers=[11, 12],
                )


# ===========================================================================
# 2. The first window — unchanged behaviour, including inside the cap.
# ===========================================================================
class TestTheFirstWindow:
    def test_a_drawing_set_inside_the_cap_is_unchanged(self, document):
        """The ≤cap path, end to end: the same pages, the same rows, the same
        counters and the same terminal status J15 already pinned for it."""
        doc = document(pages=10)
        summary = doc.first_window()

        assert summary["total_pages"] == 10
        assert summary["analysed_pages"] == 10
        assert summary["not_analysed_pages"] == 0
        assert doc.analyzer_calls == [(1, WINDOW_SIZE)]
        assert len(doc.repository.analysis_runs) == 1
        assert len(doc.repository.drawing_sets) == 1
        assert _sources(doc) == list(range(1, 11))
        assert _coverage_line(doc).is_complete

    def test_a_drawing_set_inside_the_cap_cannot_be_continued(self, document):
        doc = document(pages=10)
        doc.first_window()
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        code, _ = _refused(refused)
        assert code == "WINDOW_ALREADY_COMPLETE"
        assert _state(doc) == before

    def test_the_first_window_of_a_long_document_reads_only_the_cap(self, document):
        doc = document()
        summary = doc.first_window()

        assert doc.analyzer_calls == [(1, WINDOW_SIZE)]
        assert summary["total_pages"] == DOCUMENT_PAGES
        assert summary["analysed_pages"] == WINDOW_SIZE
        assert summary["not_analysed_pages"] == DOCUMENT_PAGES - WINDOW_SIZE
        assert _coverage_line(doc).is_complete is False

    def test_the_first_window_records_the_document_not_what_it_read(self, document):
        doc = document()
        doc.first_window()

        drawing_set = next(iter(doc.repository.drawing_sets.values()))
        run = next(iter(doc.repository.analysis_runs.values()))
        assert drawing_set["total_pages"] == DOCUMENT_PAGES
        assert drawing_set["pages_analysed"] == WINDOW_SIZE
        assert run["total_pages"] == DOCUMENT_PAGES
        assert run["pages_processed"] == WINDOW_SIZE
        assert run["status"] == "completed"

    def test_the_first_window_leaves_the_project_in_review_not_failed(self, document):
        """A drawing set with pages still to read is incomplete, not broken."""
        doc = document()
        doc.first_window()

        assert doc.repository.projects[doc.project_id]["status"] == REVIEW
        assert _coverage_line(doc).not_analysed_pages == DOCUMENT_PAGES - WINDOW_SIZE

    def test_the_first_window_states_the_boundary_the_next_one_must_start_at(self, document):
        doc = document()
        doc.first_window()

        record = _coverage_line(doc)
        assert record.analysed_pages + 1 == SECOND_WINDOW[0]

    def test_the_first_window_answers_with_the_window_it_read(self, document):
        doc = document()
        summary = doc.first_window()

        assert summary["pages_processed"] == WINDOW_SIZE
        assert summary["warnings"][-1].endswith(
            f"total={DOCUMENT_PAGES} analysed={WINDOW_SIZE} parse_failed=0 "
            f"not_analysed={DOCUMENT_PAGES - WINDOW_SIZE}"
        )


# ===========================================================================
# 3. The continuation — only its window, and all of it.
# ===========================================================================
class TestContinuation:
    def test_a_continuation_reads_only_the_remaining_pages(self, document):
        doc = document()
        doc.first_window()
        result = doc.continue_window()

        assert doc.analyzer_calls == [(1, WINDOW_SIZE), (31, 5)]
        assert result["window"] == {"first_page": 31, "last_page": 35, "size": 5}
        assert result["pages_processed"] == 5
        assert result["analysed_pages"] == DOCUMENT_PAGES
        assert result["not_analysed_pages"] == 0
        assert result["is_complete"] is True
        assert result["next_window"] is None

    def test_a_continuation_reads_nothing_that_was_already_read(self, document):
        doc = document()
        doc.first_window()
        first_window_members = copy.deepcopy(doc.repository.members)

        doc.continue_window()

        # Every earlier row is still there, unchanged, and the new rows come only
        # from the pages the continuation was asked for.
        assert doc.repository.members[: len(first_window_members)] == first_window_members
        assert _sources(doc) == list(range(1, DOCUMENT_PAGES + 1))
        for row in doc.repository.members:
            assert row["mark"] == f"M{row['source_page']}"

    def test_every_continuation_window_of_a_long_document_is_read_once(self, document):
        doc = document(pages=LONG_DOCUMENT_PAGES)
        doc.first_window()
        analyses = [doc.continue_window() for _ in range(2)]

        assert doc.analyzer_calls == [(1, 30), (31, 30), (61, 15)]
        assert [a["window"] for a in analyses] == [
            {"first_page": 31, "last_page": 60, "size": 30},
            {"first_page": 61, "last_page": 75, "size": 15},
        ]
        assert [a["analysed_pages"] for a in analyses] == [60, 75]
        assert analyses[-1]["is_complete"] is True
        assert _sources(doc) == list(range(1, LONG_DOCUMENT_PAGES + 1))

    def test_coverage_accumulates_truthfully_window_by_window(self, document):
        doc = document(pages=LONG_DOCUMENT_PAGES)
        doc.first_window()
        assert (_coverage_line(doc).analysed_pages, _coverage_line(doc).not_analysed_pages) == (30, 45)

        doc.continue_window()
        record = _coverage_line(doc)
        assert (record.total_pages, record.analysed_pages, record.not_analysed_pages) == (75, 60, 15)
        assert record.identity_holds

        doc.continue_window()
        record = _coverage_line(doc)
        assert (record.total_pages, record.analysed_pages, record.not_analysed_pages) == (75, 75, 0)
        assert record.identity_holds and record.is_complete

        # The counters that describe the document agree with the record that
        # describes how much of it was read — at every step (asserted above by
        # the fact that every continuation was accepted: a disagreement is
        # refused before it is read).
        drawing_set = next(iter(doc.repository.drawing_sets.values()))
        assert drawing_set["pages_analysed"] == record.analysed_pages
        assert drawing_set["total_pages"] == record.total_pages

    def test_a_continuation_leaves_exactly_one_coverage_record(self, document):
        doc = document()
        doc.first_window()
        doc.continue_window()

        lines = [w for w in doc.repository.projects[doc.project_id]["warnings"]
                 if w.startswith("PDF coverage: ")]
        assert len(lines) == 1, lines
        assert "not_analysed=0" in lines[0]

    def test_the_project_reaches_done_only_when_every_page_is_read(self, document):
        doc = document()
        doc.first_window()
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW

        doc.continue_window()
        assert doc.repository.projects[doc.project_id]["status"] == DONE

    def test_the_project_summary_is_a_function_of_the_persisted_rows(self, document):
        """The totals are recomputed over the rows, not added to the previous
        numbers — so a continuation cannot double-count what was already there."""
        doc = document()
        doc.first_window()
        doc.continue_window()

        summary = doc.repository.project_summary
        assert summary["total_members"] == len(doc.repository.members)
        assert summary["total_connections"] == len(doc.repository.connections)
        assert summary["total_weight_kg"] == sum(
            row["total_weight_kg"] or 0 for row in doc.repository.members
        )
        assert summary["total_unique_sections"] == len({
            row["section_name"] for row in doc.repository.members if row["section_name"]
        })

    def test_a_connection_on_a_later_page_links_a_member_read_earlier(self, document):
        """A mark is unique across the whole project (the collision rule), so a
        connection's mark resolves to the row it names wherever it was read —
        including a row the FIRST window persisted."""
        doc = document(
            connections_for=lambda number: (
                [{"connection_type": "bolted", "confidence": 96,
                  "bolts": [{"size": "M20", "grade": "8.8", "quantity": 4}],
                  "connects_members": ["M1"]}]
                if number == 31 else []
            ),
        )
        doc.first_window()
        doc.continue_window()

        linked = doc.repository.connection_member_links
        assert linked, "the connection on page 31 linked nothing"
        page_one = {row["id"] for row in doc.repository.members if row["mark"] == "M1"}
        assert page_one, "the first window persisted no M1 to link to"
        assert {link["member_id"] for link in linked} == page_one
        # The connection row itself carries its own true page, not the window's.
        assert [row["source_page"] for row in doc.repository.connections] == [31]

    def test_a_continuation_writes_no_drawing_metadata(self, document):
        """The continuation never saw page 1, so it states nothing about the
        drawing: whose number, title and revision the first window read stay."""
        doc = document()
        doc.first_window()
        before = copy.deepcopy(doc.repository.drawing_meta)
        doc.continue_window()
        assert doc.repository.drawing_meta == before

    def test_the_first_window_metadata_survives_a_continuation(self, document):
        doc = document()
        doc.first_window()
        doc.continue_window()

        drawing = next(iter(doc.repository.drawings.values()))
        assert drawing["page_count"] == DOCUMENT_PAGES
        assert drawing["drawing_number"] == "J16-DWG-001"
        assert drawing["revision"] == "A"


# ===========================================================================
# 4. A page whose response could not be read.
# ===========================================================================
class TestParseFailures:
    def test_a_parse_failure_is_recorded_and_invents_no_evidence(self, document, monkeypatch, production):
        def members_for(number):
            return [] if number == 32 else _default_members(number)

        doc = document(members_for=members_for)
        doc.first_window()

        # Page 32 comes back as a page that WAS read whose response could not be
        # parsed — the object app/ai_analysis/pdf_vision_analyzer.py appends.
        def stand_in(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1):
            doc.analyzer_calls.append((first_page, max_pages))
            last_page = min(first_page + max_pages - 1, doc.pages)
            return [
                _failed_page(number) if number == 32 else _page(number, _default_members(number))
                for number in range(first_page, last_page + 1)
            ]

        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", stand_in)
        result = doc.continue_window()

        record = _coverage_line(doc)
        assert (record.total_pages, record.analysed_pages,
                record.parse_failed_pages, record.not_analysed_pages) == (35, 35, 1, 0)
        assert record.is_complete is False
        # The failed page contributed no member row and no connection row: a page
        # that could not be read is not a page that stated nothing.
        assert 32 not in _sources(doc)
        assert _sources(doc) == [n for n in range(1, 36) if n != 32]
        assert [row["source_page"] for row in doc.repository.connections] == []
        assert result["parse_failed_pages"] == 1

    def test_a_parse_failure_keeps_the_project_out_of_done(self, document, monkeypatch, production):
        doc = document()
        doc.first_window()

        def stand_in(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1):
            doc.analyzer_calls.append((first_page, max_pages))
            last_page = min(first_page + max_pages - 1, doc.pages)
            return [
                _failed_page(number) if number == 34 else _page(number, _default_members(number))
                for number in range(first_page, last_page + 1)
            ]

        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", stand_in)
        doc.continue_window()

        assert doc.repository.projects[doc.project_id]["status"] == REVIEW
        assert "parse_failed=1" in doc.repository.projects[doc.project_id]["warnings"][-1]

    def test_the_window_that_contained_a_failed_page_cannot_be_read_again(self, document, monkeypatch, production):
        """Why a failed page cannot be repaired by re-reading its WINDOW.

        The window's other pages are already persisted and the boundary has
        moved past them, so the contract refuses the window — correctly, and
        whatever the failed page is. Repairing the page itself is a different
        operation with a different subject (ONE page), which is Milestone J17's:
        it gives the failure an identity (`app/validation/parse_failures.py`) and
        reads that page alone. This test is about the window, not about whether
        a retry exists.
        """
        doc = document()
        doc.first_window()

        def stand_in(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1):
            doc.analyzer_calls.append((first_page, max_pages))
            last_page = min(first_page + max_pages - 1, doc.pages)
            return [
                _failed_page(number) if number == 33 else _page(number, _default_members(number))
                for number in range(first_page, last_page + 1)
            ]

        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", stand_in)
        doc.continue_window()
        before = _state(doc)
        calls_before = len(doc.analyzer_calls)

        # The whole document has now been read (one page unsuccessfully), so
        # there is no window left to ask for; asking for the window that
        # contained the failure is refused for the same reason.
        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        code, _ = _refused(refused)
        assert code == "WINDOW_ALREADY_COMPLETE"
        assert len(doc.analyzer_calls) == calls_before
        assert _state(doc) == before
        assert _coverage_line(doc).parse_failed_pages == 1
        assert doc.repository.projects[doc.project_id]["status"] == REVIEW


# ===========================================================================
# 5. Duplication protection, idempotency, and the refusals decided from state.
# ===========================================================================
class TestRefusalsAndIdempotency:
    def test_a_repeated_continuation_persists_nothing_twice(self, document):
        doc = document()
        doc.first_window()
        doc.continue_window()
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        code, _ = _refused(refused)
        assert code == "WINDOW_ALREADY_COMPLETE"
        assert _state(doc) == before

    def test_a_continuation_that_names_an_already_read_window_is_refused(self, document):
        """The same request, expressed as a window, is refused on the window's own
        terms — and reads nothing."""
        doc = document()
        doc.first_window()
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window(requested_first_page=1, requested_last_page=30)

        code, _ = _refused(refused)
        assert code == "WINDOW_BACKWARD"
        assert _state(doc) == before

    def test_a_partly_read_window_cannot_be_requested_as_a_whole(self, document):
        doc = document()
        doc.first_window()
        doc.continue_window(requested_first_page=31, requested_last_page=32)
        # Pages 33-35 are still unread and 31-32 are persisted: a request for
        # 31-35 now overlaps what was read.
        with pytest.raises(doc.refused) as refused:
            doc.continue_window(requested_first_page=31, requested_last_page=35)
        assert _refused(refused)[0] == "WINDOW_OVERLAP"

    def test_a_member_mark_repeated_across_windows_is_refused(self, document):
        """Member identity across windows has no merge rule, so a repeat is
        reported instead of being silently duplicated or silently merged."""
        doc = document(members_for=lambda number: [j4._member("M1", EXACT_TOKEN)])
        doc.first_window()
        before = _persisted(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        code, detail = _refused(refused)
        assert code == "CONTINUATION_MEMBER_MARK_COLLISION"
        assert "M1" in detail
        # The window WAS read (the collision is only visible once it has been),
        # and then refused: not one persisted fact moved.
        assert len(doc.analyzer_calls) == 2
        assert _persisted(doc) == before

    def test_a_continuation_of_a_different_source_is_refused(self, document, production):
        doc = document()
        doc.first_window()
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            production.pipeline.continue_pdf_extraction(
                doc.source, doc.project_id, "j16-user", "j16-user/some-other-file.pdf",
            )

        assert _refused(refused)[0] == "CONTINUATION_SOURCE_MISMATCH"
        assert _state(doc) == before

    def test_a_file_with_a_different_page_count_is_refused(self, document, tmp_path, production, monkeypatch):
        """A different document with the same name is caught by its page count."""
        doc = document()
        doc.first_window()
        other = _write_pdf(tmp_path / "j16-shrunk.pdf", 20)
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            production.pipeline.continue_pdf_extraction(
                other, doc.project_id, "j16-user", doc.storage_path,
            )

        assert _refused(refused)[0] == "CONTINUATION_DOCUMENT_TOTAL_MISMATCH"
        assert _state(doc) == before

    def test_a_continuation_of_a_project_with_no_coverage_record_is_refused(self, document):
        """A project extracted before J15 states nothing about what it read."""
        doc = document()
        doc.first_window()
        doc.repository.projects[doc.project_id]["warnings"] = ["Some other warning."]
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        assert _refused(refused)[0] == "CONTINUATION_NO_COVERAGE_RECORD"
        assert _state(doc) == before

    def test_a_record_that_disagrees_with_its_counters_is_refused(self, document):
        doc = document()
        doc.first_window()
        drawing_set = next(iter(doc.repository.drawing_sets.values()))
        drawing_set["pages_analysed"] = 29        # the interrupted-commit state
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        code, detail = _refused(refused)
        assert code == "CONTINUATION_RECORD_CONFLICT"
        assert "29" in detail and "30" in detail
        assert _state(doc) == before

    def test_an_ambiguous_drawing_set_is_refused(self, document):
        """Two drawing sets mean the extraction has run against this project
        twice (POST /extract has no idempotency guard): which document the pages
        belong to is ambiguous, so no window is read."""
        doc = document()
        doc.first_window()
        doc.repository.drawing_sets["ds-second"] = {
            "id": "ds-second", "project_id": doc.project_id, "name": "j16-drawings.pdf",
            "status": "analyzed", "total_pages": 35, "pages_analysed": 30,
        }
        before = _state(doc)

        with pytest.raises(doc.refused) as refused:
            doc.continue_window()

        assert _refused(refused)[0] == "CONTINUATION_DRAWING_SET_UNRESOLVED"
        assert _state(doc) == before

    def test_a_project_that_does_not_exist_is_refused(self, document):
        doc = document(seed=False)
        with pytest.raises(doc.refused) as refused:
            doc.continue_window()
        assert _refused(refused)[0] == "CONTINUATION_DRAWING_SET_UNRESOLVED"
        assert doc.analyzer_calls == []

    def test_a_window_that_did_not_come_back_in_full_advances_nothing(self, document, monkeypatch, production):
        """A reading failure is a failed run, not a refused continuation: it
        raises, and the boundary does not move past a page nobody read."""
        doc = document()
        doc.first_window()
        before = _persisted(doc)

        def short(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1):
            doc.analyzer_calls.append((first_page, max_pages))
            return [_page(number, _default_members(number)) for number in range(31, 34)]

        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", short)
        with pytest.raises(ValueError) as failure:
            doc.continue_window()

        assert "not read in full" in str(failure.value)
        # The read happened and is recorded; nothing it produced was persisted.
        assert len(doc.analyzer_calls) == 2
        assert _persisted(doc) == before
        assert _coverage_line(doc).analysed_pages == WINDOW_SIZE

    def test_every_refusal_has_a_name_and_a_reason(self, document):
        doc = document()
        doc.first_window()
        with pytest.raises(doc.refused) as refused:
            doc.continue_window(requested_first_page=31, requested_last_page=40)
        code, detail = _refused(refused)
        assert code in REFUSAL_CODES
        assert detail and code in str(refused.value)

    def test_the_plan_decides_without_reading_anything(self, document):
        """Everything decidable from persisted state is decided before the file
        is opened — which is what lets the HTTP layer refuse synchronously."""
        doc = document()
        doc.first_window()
        plan = doc.plan()
        assert (plan.window.first_page, plan.window.last_page) == SECOND_WINDOW
        assert doc.analyzer_calls == [(1, WINDOW_SIZE)]


# ===========================================================================
# 6. The HTTP surface — the mechanism production actually calls.
# ===========================================================================
class _Result:
    def __init__(self, data):
        self.data = data


class _ProjectQuery:
    """`supabase.table("projects").select(...).eq("id", …).single()` — the one
    read the HTTP layer performs itself, answered from the project row."""

    def __init__(self, project):
        self.project = project
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
        if self.project is None or self.project.get("id") != self.project_id:
            return _Result(None)
        return _Result(copy.deepcopy(self.project) if False else self.project)


class _ProjectClient:
    """A client that answers the project read and refuses every other call.

    The HTTP layer must not read members, connections or the drawing set itself:
    it dispatches, and the pipeline decides.
    """

    def __init__(self, project):
        self.project = project
        self.tables_read = []

    def table(self, name):
        self.tables_read.append(name)
        assert name == "projects", f"the HTTP layer read {name!r}"
        return _ProjectQuery(self.project)


class TestTheHttpSurface:
    def _client(self, production, monkeypatch, repository, project_id):
        """A client authenticated AS THIS PROJECT'S OWNER (Milestone J19).

        Every project route now requires the caller's Supabase Auth access token,
        so the harness mints a genuine ES256 token whose subject is the project's
        own `user_id` — which is exactly what the frontend does. Nothing else about
        these tests changes: they still drive the same routes with the same bodies.
        """
        from fastapi.testclient import TestClient

        auth.install(monkeypatch, production)
        monkeypatch.setattr(production.main, "supabase", _ProjectClient(repository.projects[project_id]))
        row = repository.projects[project_id]
        return TestClient(production.main.app, headers=auth.headers_for(row))

    def test_the_continuation_endpoint_is_registered(self, production):
        paths = {route.path: route for route in production.main.app.routes}
        assert "/continue-extraction/{project_id}" in paths
        assert "POST" in paths["/continue-extraction/{project_id}"].methods
        # The endpoints that were there before it are untouched.
        for path in ("/health", "/extract/{project_id}", "/generate-report/{project_id}"):
            assert path in paths

    def test_a_project_with_nothing_left_to_read_is_refused_by_name(self, document, production, monkeypatch):
        doc = document(pages=10)
        doc.first_window()
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/continue-extraction/{doc.project_id}")

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == "WINDOW_ALREADY_COMPLETE"

    def test_a_continuable_project_is_dispatched_with_its_window(self, document, production, monkeypatch):
        doc = document()
        doc.first_window()
        dispatched = []
        monkeypatch.setattr(
            production.main, "run_continuation",
            lambda *args, **kwargs: dispatched.append((args, kwargs)),
        )
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/continue-extraction/{doc.project_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "continuation_started"
        assert (body["first_page"], body["last_page"]) == SECOND_WINDOW
        assert len(dispatched) == 1
        assert dispatched[0][0][0] == doc.project_id
        assert tuple(dispatched[0][0][-2:]) == SECOND_WINDOW

    def test_a_requested_window_reaches_the_plan(self, document, production, monkeypatch):
        doc = document()
        doc.first_window()
        monkeypatch.setattr(production.main, "run_continuation", lambda *a, **k: None)
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/continue-extraction/{doc.project_id}?first_page=1&last_page=30")

        assert response.status_code == 409
        assert response.json()["detail"]["refusal"] == "WINDOW_BACKWARD"

    def test_a_project_that_is_not_a_pdf_is_refused(self, document, production, monkeypatch):
        doc = document()
        doc.first_window()
        doc.repository.projects[doc.project_id]["source_format"] = "DXF"
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post(f"/continue-extraction/{doc.project_id}")

        assert response.status_code == 400
        assert doc.analyzer_calls == [(1, WINDOW_SIZE)]

    def test_a_missing_project_is_a_404(self, document, production, monkeypatch):
        doc = document(pages=10)
        doc.first_window()
        client = self._client(production, monkeypatch, doc.repository, doc.project_id)

        response = client.post("/continue-extraction/no-such-project")

        assert response.status_code == 404


# ===========================================================================
# 7. Scope, purity, and what this milestone did NOT do.
# ===========================================================================
class TestScopeAndPurity:
    def _imports(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        return imported

    def test_the_window_contract_is_pure(self):
        """Decidable from the committed record and the requested window alone:
        no database, no filesystem, no AI, no rendering."""
        imported = self._imports(WINDOWS_PATH)
        assert imported == {"dataclasses", "app.validation.page_coverage"}, imported

    def test_the_continuation_contract_is_not_a_cad_engine_subsystem(self):
        """The 7AY/7AZ machinery is an in-memory JSON capture combined through the
        CAD review intake; production's substrate is the database, so the
        contract was re-expressed, not imported."""
        assert not any(name.startswith("app.cad_engine") for name in self._imports(WINDOWS_PATH))
        assert not any(name.startswith("app.cad_engine") for name in self._imports(PIPELINE_PATH))

    def test_the_production_pipeline_still_names_no_cad_engine_token(self):
        source = PIPELINE_PATH.read_text(encoding="utf-8")
        assert "cad_engine" not in source

    def test_the_refusal_vocabulary_is_the_contracts_own(self, windows):
        for name in REFUSAL_CODES:
            assert getattr(windows, name) == name
            assert name in windows.__all__

    def test_no_migration_was_added(self):
        """J16 uses the schema as it stands: the coverage record rides
        `projects.warnings` (J15's precedent), the counters and per-window run
        history ride columns that already existed, and member/connection rows
        carry the true source page they always carried.

        J22 later added a fourth migration, and it is not this milestone's:
        nothing in it names a page, a window, a coverage record or a run, so the
        claim above still holds over the schema as it now stands.

        J23 added a fifth, and it is the opposite of this milestone's: the capture
        it records IS per-page, per-window and per-run. It is still not this
        milestone's claim, because it records the READING and changes no window
        rule — `check_page_window` is untouched, and a window's arithmetic is the
        same arithmetic over a schema that now also remembers what was read.

        J28 added a sixth, and it is not this milestone's either: it records PDF
        annotation occurrences read from the drawing, it names no page window,
        and nothing in `app/` reads it. A window's arithmetic is still the same
        arithmetic. (J28A is the bookkeeping step that recorded the name here.)

        J44 added a seventh, and it is not this milestone's either: it places one
        project-keyed row naming who is reviewing a project and until when. It
        names no window and records no reading, so a window's arithmetic is still
        the same arithmetic. J61 added an eighth, which is not this milestone's
        either: it gives one source document an identity and points a drawing at
        it. It moves no window, adds no page to one and records no reading, so the
        arithmetic this file asserts is untouched.

        The eight names are pinned exactly, and a ninth appearing unremarked
        still fails here."""
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
            "20261006000000_l19_selected_extraction_lineage.sql",
        }
        migrations = sorted(path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert migrations == sorted(j13.MIGRATIONS)
        j22 = (REPO / "supabase" / "migrations" /
               "20260925000000_j22_connection_review_persistence.sql").read_text(encoding="utf-8")
        statements = "\n".join(
            line for line in j22.splitlines() if not line.strip().startswith("--")
        )
        for name in ("analysis_runs", "page", "window", "coverage", "warnings", "retry"):
            assert name not in statements.lower(), name

    def test_the_continuation_writes_no_column_the_cap_path_did_not(self):
        """Every column a continuation commits is one the ≤cap path already
        wrote (or a J16 read of one): nothing here invented a field, which is
        why no migration accompanies this milestone."""
        source = PIPELINE_PATH.read_text(encoding="utf-8")
        continuation = source.split("def continue_pdf_extraction", 1)[1]
        continuation = continuation.split("\n    return {", 1)[0]

        written = set()
        for call in re.finditer(r"repo\.(update_drawing_set|update_analysis_run|update_project_summary)\((.*?)\n    \)", continuation, re.S):
            written |= set(re.findall(r"(\w+)=", call.group(2)))
        assert written == {
            "status", "pages_analysed", "members_found", "review_required_count", "total_pages",
            "pages_processed", "completed_at",
            "total_members", "total_unique_sections", "total_connections",
            "total_weight_kg", "total_weight_tonnes", "unmatched_sections", "warnings",
        }, written

    def test_the_repository_reads_are_reads(self):
        """The J16 readers return columns and decide nothing: no UPDATE, no
        INSERT, no DELETE, and no default invented for a missing value."""
        source = (REPO / "app" / "engineering_data" / "repository.py").read_text(encoding="utf-8")
        readers = source.split("# Reads (Milestone J16)", 1)[1]
        for forbidden in (".insert(", ".update(", ".delete(", "upsert"):
            assert forbidden not in readers, forbidden

    def test_the_harness_makes_no_network_call(self, document, production):
        """The reference read behind the matcher is answered in-process, and the
        fake records that it was asked: a test that silently reached the live
        client — and with it the network — cannot pass unnoticed."""
        doc = document(pages=5)
        doc.first_window()

        client = production.matcher_module.supabase
        assert isinstance(client, authority._FakeSupabaseClient)
        assert client.executed > 0, "the matcher never read the reference index"

    def test_the_single_window_path_is_still_reachable_unchanged(self):
        """`parse_pdf_and_save` still exists, still creates the set/drawing/run,
        and the continuation adds a second entry point rather than replacing it."""
        source = PIPELINE_PATH.read_text(encoding="utf-8")
        assert "def parse_pdf_and_save(" in source
        assert "def _run_pipeline(" in source
        assert "def continue_pdf_extraction(" in source
        container = (REPO / "app" / "main.py").read_text(encoding="utf-8")
        assert "parse_pdf_and_save(tmp_path, project_id, user_id, storage_path)" in container
        assert "continue_pdf_extraction(" in container
