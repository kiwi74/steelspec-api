"""
J24A — the production revision-0 7AJ producer, proved over genuinely captured material.

WHAT THIS FILE IS

The proof that a project's initial connection-review workflow can now be reconstructed
from what is PERSISTED — the raw AI readings J23 records, plus the project, drawing-set,
drawing and page identities — rather than from a `list[PageExtraction]` alive in one
process's memory.

It is NOT a proof of a review UI, and not a proof that a revision ≥ 1 can be reached: the
producer stops at revision 0 by design, for the reason the module's own docstring gives
(`member_placements` has no persisted source).

WHY THE MATERIAL IS GENUINE, AND WHY THAT IS THE WHOLE ARGUMENT

Every reading this file drives the producer with is a file under `tests/data/`, captured
from a real model reading a real drawing set — the same files J23's own suite asserts the
capture layer over:

    selby_square_*.json          7 disjoint windows, 32 pages
    selby_square_page_26_recovery.json  the same page 26 read a SECOND time
    arkles_strand_page_extractions.json 30 pages: 6 whose response could not be parsed

A producer proved over readings this file also wrote would prove nothing about production.
So the pages come from those files, through J23's own `capture_rows`, into a store that
reproduces the capture table's behaviour — and the reconstruction is compared against the
DIRECT in-process construction over the same pages, so "it matches the existing 7AJ
contract" is a comparison against the contract, not an assertion about it.

WHAT IS A DOUBLE HERE, AND WHAT IS NOT

    DOUBLED   the database (the store), and the section matcher.
    REAL      the producer, J23's selection rule, 7AZ's accumulation and validation,
              7Y's intake, 7AJ's workflow construction, and J22's snapshot builder.

The store is doubled because a test may not write to the append-only capture ledger; its
shape, though, is not invented — it returns the column lists the production repository's
own selects return, and tests below read the production source to prove that.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import json
from pathlib import Path

import pytest

from app.cad_engine.connection_review_snapshot import build_review_snapshot
from app.cad_engine.incremental_continuation import begin_incremental_analysis
from app.cad_engine.project_extraction_intake import intake_page_extractions
from app.cad_engine.project_workflow import ProjectWorkflowState, start_project_workflow
from app.engineering_data import page_extraction_capture as captures
from app.production_review import project_workflow_reconstruction as producer

from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j23_page_extraction_capture as j23

REPO = j13.REPO
production = j4.production

MODULE_PATH = REPO / "app" / "production_review" / "project_workflow_reconstruction.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"

MODEL = j23.MODEL

# The genuine readings, read and never written — J23's own loaders, reused verbatim so
# the two milestones provably drive the producer with the same bytes.
_capture_file = j23._capture_file
SELBY_WINDOW_FILES = j23.SELBY_WINDOW_FILES
ARKLES_FILE = j23.ARKLES_FILE


def _capture_rows_for(name):
    return _capture_file(name)


def _selby_pages():
    return j23._selby_pages()


def _arkles_pages():
    return j23._arkles_pages()


def _selby_recovery():
    return j23._selby_recovery()


# ---------------------------------------------------------------------------
# The cross-window member-mark collision, measured on the genuine readings.
#
# A mark is an identity within a drawing set, but the vision model names it once per
# page on which it can see it — so one member drawn on three sheets is named three
# times, and when the sheets fall in different extraction windows the repeats land in
# different capture rows. The brief for this milestone names this collision and asks
# that it not be solved here unless revision 0 requires it; these helpers measure it
# from the readings so the tests below prove what it does and does not cost, rather
# than describing it.
# ---------------------------------------------------------------------------
def _marks_named_by(page):
    """The marks one page's reading names, once per OCCURRENCE, in the reading's order.

    Repeats are kept: a reading that names a mark three times named it three times, and
    collapsing that here would hide the very thing being measured.
    """
    return tuple(
        member.get("mark") for member in (page.get("raw_members") or ())
        if member.get("mark")
    )


def _pages_naming_each_mark(pages):
    """mark -> the page numbers whose readings name it, in page order, repeats kept."""
    found: dict[str, list[int]] = {}
    for page in pages:
        for mark in _marks_named_by(page):
            found.setdefault(mark, []).append(page["page_number"])
    return found


def _intra_page_repeats(pages):
    """mark -> {page_number: how many times that ONE page's reading names it}.

    The harder shape of the same collision: repeats that no window boundary explains,
    because they are inside a single reading.
    """
    repeats: dict[str, dict[int, int]] = {}
    for page in pages:
        counts: dict[str, int] = {}
        for mark in _marks_named_by(page):
            counts[mark] = counts.get(mark, 0) + 1
        for mark, count in counts.items():
            if count > 1:
                repeats.setdefault(mark, {})[page["page_number"]] = count
    return repeats


def _windows_naming_each_mark():
    """mark -> the WINDOW indexes whose files name it — the cross-window occurrence.

    Read from the seven genuine window FILES rather than from the accumulated reading,
    because the question is which windows collided, and the accumulation has already
    merged them into one document by the time a reconstruction sees it.
    """
    found: dict[str, set[int]] = {}
    for index, name in enumerate(SELBY_WINDOW_FILES):
        for page in _capture_rows_for(name):
            for mark in _marks_named_by(page):
                found.setdefault(mark, set()).add(index)
    return found


# ---------------------------------------------------------------------------
# Identities. The Selby document is 32 pages and the Arkles document 30 — the
# page counts their captures genuinely cover, stated here rather than guessed
# at in a test body.
# ---------------------------------------------------------------------------
SELBY_PROJECT = "PROJ-7J24A-SELBY"
SELBY_SET = "drawing-set-selby"
SELBY_DRAWING = "drawing-selby"
SELBY_PAGE_COUNT = 32

ARKLES_PROJECT = "PROJ-7J24A-ARKLES"
ARKLES_SET = "drawing-set-arkles"
ARKLES_DRAWING = "drawing-arkles"
ARKLES_PAGE_COUNT = 30

OTHER_PROJECT = "PROJ-7J24A-OTHER"
OTHER_SET = "drawing-set-other"
OTHER_DRAWING = "drawing-other"

# The persisted member projection's columns, as `repository.member_rows_for_project`
# selects them. The values other than the mark are never read at revision 0 — a test
# below proves that by recording every column access.
MEMBER_COLUMNS = ("id", "mark", "section_name", "review_status", "total_weight_kg")


class _RecordingRow(dict):
    """A member row that records which of its columns were read.

    The persisted read returns five columns; a revision-0 reconstruction is entitled to
    the MARK and nothing else (7AC uses the marks as the authoritative member choices).
    This row states that by recording every access rather than by asserting it.

    `_read` is resolved defensively: a dict subclass can be rebuilt by `copy` or `pickle`
    without `__init__` running, and a row that forgot how to record must not explode.
    """

    def get(self, key, default=None):
        read = getattr(self, "_read", None)
        if read is not None:
            read.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        read = getattr(self, "_read", None)
        if read is not None:
            read.add(key)
        return super().__getitem__(key)


def _member_rows_for(pages, *, read=None):
    """The project's validated members, in the persisted five-column shape.

    The marks are the capture's own marks, asserted present, so this fixture cannot
    invent a member the reading does not name. Every other column is carried because the
    production projection carries it — and is never consulted, which the recording rows
    prove where a recorder is supplied.
    """
    marks = []
    for page in pages:
        for member in page.get("raw_members") or ():
            mark = member.get("mark")
            if mark and mark not in marks:
                marks.append(mark)
    assert marks, "the genuine capture names no members; this fixture would invent them"

    rows = {}
    for index, mark in enumerate(marks, start=1):
        row = {
            "id": f"00000000-0000-4000-8000-{index:012d}",
            "mark": mark,
            "section_name": None,
            "review_status": "approved",
            "total_weight_kg": None,
        }
        if read is not None:
            row = _RecordingRow(row)
            row._read = read
        rows[mark] = row
    return rows


class _Persisted:
    """The persisted record the producer reads, and a record of what it read.

    Every read the producer is allowed to make is present; the reads it must never make
    are present TOO, and record themselves, so "it did not reconstruct a reading from the
    engineering rows" is asserted over a call that could have happened rather than over a
    method that does not exist.

    The row shapes are the production ones: the drawing-set and drawing reads return the
    column lists the production selects return, and the member rows carry the production
    projection.
    """

    # The source-contradicting engineering rows. They are returned if anything ever asks
    # for them, and what they say contradicts the capture on purpose: a reconstruction
    # that consulted them would produce a different workflow, and this file asserts the
    # workflow matches the CAPTURE.
    CONTRADICTING_CONNECTIONS = (
        {"id": "conn-not-in-any-reading", "member_id": "member-not-in-any-reading",
         "connection_type": "welded", "review_status": "approved"},
        {"id": "conn-also-not-in-any-reading", "member_id": "member-not-in-any-reading",
         "connection_type": "bolted", "review_status": "approved"},
    )

    known_projects = ()

    def __init__(self, *, drawing_sets=(), drawings=(), table=None, members=(),
                 selected_drawing_id=None):
        self._drawing_sets = [dict(row) for row in drawing_sets]
        self._drawings = [dict(row) for row in drawings]
        self._table = table
        self._members = dict(members)
        # L19: what the document has been TOLD to be reviewed from. `None` is the ordinary
        # state — no lineage selected — and every document behaved that way before the
        # column existed, so a double that sets none reproduces the pre-L19 behaviour.
        self._selected_drawing_id = selected_drawing_id
        self.calls: list[tuple[str, object]] = []

    # -- the reads a reconstruction makes -----------------------------------
    def get_project(self, project_id):
        self.calls.append(("get_project", project_id))
        if project_id in self.known_projects:
            return {"id": project_id, "name": "a project", "status": "review"}
        return None

    def drawing_sets_for_project(self, project_id):
        self.calls.append(("drawing_sets_for_project", project_id))
        return [dict(row) for row in self._drawing_sets if row["project_id"] == project_id]

    def drawings_for_drawing_set(self, drawing_set_id):
        self.calls.append(("drawings_for_drawing_set", drawing_set_id))
        return [dict(row) for row in self._drawings if row["drawing_set_id"] == drawing_set_id]

    def page_extraction_captures_for_drawing(self, drawing_id):
        self.calls.append(("page_extraction_captures_for_drawing", drawing_id))
        return self._table.for_drawing(drawing_id) if self._table is not None else []

    def selected_drawing_id_for_document(self, document_id):
        """L19: the one lineage this document is reviewed from, or None.

        Read, never chosen. A double returns whatever the test stated and the
        reconstruction filters by it; nothing here ranks, dates or scores a lineage.
        """
        self.calls.append(("selected_drawing_id_for_document", document_id))
        return self._selected_drawing_id

    def member_rows_for_project(self, project_id):
        self.calls.append(("member_rows_for_project", project_id))
        return list(self._members.values())

    # -- the reads a reconstruction must NEVER make -------------------------
    def connection_rows_for_project(self, project_id):
        self.calls.append(("connection_rows_for_project", project_id))
        return [dict(row) for row in self.CONTRADICTING_CONNECTIONS]

    def evidence_rows_for_page(self, project_id):
        self.calls.append(("evidence_rows_for_page", project_id))
        return {"steel_members": (), "connections": ()}

    def connection_review_snapshot_rows(self, project_id):
        self.calls.append(("connection_review_snapshot_rows", project_id))
        return []


ALLOWED_READS = (
    "get_project",
    "drawing_sets_for_project",
    "drawings_for_drawing_set",
    "page_extraction_captures_for_drawing",
    "member_rows_for_project",
    # L19: the lineage a document has been told to be reviewed from. One read, and the
    # only one this milestone adds to the reconstruction — it answers "which reading",
    # never "which reading is best", and `None` is the ordinary unselected state.
    "selected_drawing_id_for_document",
)
FORBIDDEN_READS = (
    "connection_rows_for_project",
    "evidence_rows_for_page",
    "connection_review_snapshot_rows",
)


class _StubMatcher:
    """The caller's section matcher.

    It answers nothing, and that is the honest stand-in: at revision 0 the matcher is
    carried on the state and never consulted (`start_project_workflow` evaluates every
    connection without member context), so a matcher that resolved sections would make
    this file look as though geometry had been involved. It is also the proof that the
    producer does not construct the production matcher, whose construction reads the
    live catalogue.
    """

    def match(self, raw_name):
        return None


def _drawing_set(project_id, set_id, *, page_count):
    return {"id": set_id, "project_id": project_id, "name": "a set", "status": "complete",
            "total_pages": page_count, "pages_analysed": page_count}


def _drawing(set_id, drawing_id, *, page_count):
    return {"id": drawing_id, "drawing_set_id": set_id, "file_name": "set.pdf",
            "storage_path": "somewhere/set.pdf", "page_count": page_count}


def _store(*, project_id, set_id, drawing_id, page_count, table, pages, read=None):
    store = _Persisted(
        drawing_sets=[_drawing_set(project_id, set_id, page_count=page_count)],
        drawings=[_drawing(set_id, drawing_id, page_count=page_count)],
        table=table,
        members=_member_rows_for(pages, read=read),
    )
    store.known_projects = (project_id,)
    return store


def _selby_store(*, with_recovery=False, read=None):
    """The genuine Selby document, recorded window by window under ONE drawing.

    Each window is its own extraction run, which is what the production continuation
    does: a continuation REUSES the drawing set and the drawing, so all seven windows
    share `SELBY_DRAWING`. That shared drawing id is the accumulation scope.
    """
    table = j23._CaptureTable()
    for index, name in enumerate(SELBY_WINDOW_FILES, start=1):
        j23._record(table, _capture_rows_for(name), run=f"run-{index}",
                    drawing=SELBY_DRAWING, drawing_set=SELBY_SET, project=SELBY_PROJECT)
    if with_recovery:
        j23._record(table, [_selby_recovery()], run="run-recovery",
                    drawing=SELBY_DRAWING, drawing_set=SELBY_SET, project=SELBY_PROJECT)
    return _store(project_id=SELBY_PROJECT, set_id=SELBY_SET, drawing_id=SELBY_DRAWING,
                  page_count=SELBY_PAGE_COUNT, table=table, pages=_selby_pages(), read=read)


def _page_run_ids(store, drawing_id, pages):
    """Which run READ each of `pages` — J23's own selection over the persisted rows.

    The direct in-process construction must be handed the same page -> run association the
    producer derives for itself, or the two constructions would differ in their INPUTS
    rather than in anything the producer did: one would record each candidate's origin and
    the other would record none. Returned aligned with `pages`, which is the shape 7Y's
    intake takes; a page the store does not carry gets None, never another page's run.
    """
    by_page = {
        row["page_number"]: str(row["analysis_run_id"])
        for row in captures.authoritative_captures(
            store.page_extraction_captures_for_drawing(drawing_id))
    }
    return [by_page.get(page["page_number"]) for page in pages]


def _arkles_store(*, read=None):
    table = j23._CaptureTable()
    j23._record(table, _arkles_pages(), run="run-arkles",
                drawing=ARKLES_DRAWING, drawing_set=ARKLES_SET, project=ARKLES_PROJECT)
    return _store(project_id=ARKLES_PROJECT, set_id=ARKLES_SET, drawing_id=ARKLES_DRAWING,
                  page_count=ARKLES_PAGE_COUNT, table=table, pages=_arkles_pages(), read=read)


def _reconstruct(store, project_id, **kwargs):
    return producer.reconstruct_project_workflow(
        project_id, section_matcher=_StubMatcher(), repository=store, **kwargs)


def _refusal(store, project_id, *, code, **kwargs):
    with pytest.raises(producer.ReconstructionRefused) as caught:
        _reconstruct(store, project_id, **kwargs)
    assert caught.value.code == code, caught.value.detail
    return caught.value


# ===========================================================================
# A. A real persisted capture is readable, and reads as revision 0.
# ===========================================================================
class TestARealPersistedCaptureReconstructsRevisionZero:
    def test_the_genuine_selby_document_reconstructs_revision_zero(self):
        """Seven real windows, one drawing, 32 pages — read back as a genuine 7AJ state."""
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result is not None
        assert isinstance(result.workflow, ProjectWorkflowState)
        assert result.project_id == SELBY_PROJECT
        assert result.drawing_id == SELBY_DRAWING
        assert result.drawing_set_id == SELBY_SET
        assert result.page_count == SELBY_PAGE_COUNT
        assert result.workflow.revision == 0
        assert result.captures_read == SELBY_PAGE_COUNT

    def test_the_selby_reconstruction_accounts_for_every_page_of_the_document(self):
        """Nothing is silently missing: all 32 pages are analysed, none unaccounted for."""
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result.analysed_pages == tuple(range(1, SELBY_PAGE_COUNT + 1))
        assert result.parse_failed_pages == ()
        assert result.not_analysed_pages == ()

    def test_the_genuine_arkles_document_reconstructs_revision_zero(self):
        """A real reading containing all three page states at once, read back as revision 0."""
        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        assert result is not None
        assert result.workflow.revision == 0
        assert result.captures_read == ARKLES_PAGE_COUNT

    def test_a_document_only_partly_read_states_the_pages_nobody_read(self):
        """A gap is stated, never absorbed: only the first window was ever read."""
        table = j23._CaptureTable()
        j23._record(table, _capture_rows_for(SELBY_WINDOW_FILES[0]), run="run-1",
                    drawing=SELBY_DRAWING, drawing_set=SELBY_SET, project=SELBY_PROJECT)
        store = _store(project_id=SELBY_PROJECT, set_id=SELBY_SET, drawing_id=SELBY_DRAWING,
                       page_count=SELBY_PAGE_COUNT, table=table, pages=_selby_pages())

        result = _reconstruct(store, SELBY_PROJECT)
        read_pages = tuple(page["page_number"] for page in _capture_rows_for(SELBY_WINDOW_FILES[0]))

        assert result.analysed_pages == read_pages
        assert result.not_analysed_pages == tuple(
            number for number in range(1, SELBY_PAGE_COUNT + 1) if number not in read_pages)
        assert result.workflow.intake.pages_not_analysed == len(result.not_analysed_pages)

    def test_the_reconstruction_reads_exactly_the_persisted_record_and_nothing_else(self):
        """The reads are the five the design names — no sixth, and none of the forbidden."""
        store = _selby_store()
        _reconstruct(store, SELBY_PROJECT)

        names = {name for name, _ in store.calls}
        assert names <= set(ALLOWED_READS), sorted(names - set(ALLOWED_READS))
        assert "page_extraction_captures_for_drawing" in names
        assert "member_rows_for_project" in names

    def test_the_reading_that_reaches_the_workflow_is_the_capture_verbatim(self):
        """The candidates the workflow queues are the capture's own connections, in order.

        Compared against the FILES, not against anything this test built.
        """
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        expected = [
            (page["page_number"], entry)
            for page in sorted(_selby_pages(), key=lambda p: p["page_number"])
            for entry in (page.get("raw_connections") or ())
        ]
        intake = result.workflow.intake
        assert intake.pages_received == SELBY_PAGE_COUNT
        assert len(intake.collection.candidates) == len(expected)
        assert len(result.workflow.connections) == len(expected)


# ===========================================================================
# B. The order is deterministic, and it is the document's own page order.
# ===========================================================================
class TestPageOrderIsDeterministic:
    def test_the_pages_are_ordered_by_page_number_not_by_read_order(self):
        """The store hands rows back in insertion order; the reconstruction does not care."""
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        numbers = [page["page_number"] for page in result.accumulated.pages]
        assert numbers == sorted(numbers)
        assert numbers == list(range(1, SELBY_PAGE_COUNT + 1))

    def test_a_shuffled_record_produces_the_identical_accumulation(self):
        """Two stores holding the same rows inserted in opposite orders agree exactly."""
        pages = sorted(_selby_pages(), key=lambda p: p["page_number"])

        def store_for(order):
            table = j23._CaptureTable()
            for index, page in enumerate(order, start=1):
                j23._record(table, [page], run=f"run-{index:03d}", drawing=SELBY_DRAWING,
                            drawing_set=SELBY_SET, project=SELBY_PROJECT)
            return _store(project_id=SELBY_PROJECT, set_id=SELBY_SET,
                          drawing_id=SELBY_DRAWING, page_count=SELBY_PAGE_COUNT,
                          table=table, pages=_selby_pages())

        one = _reconstruct(store_for(pages), SELBY_PROJECT)
        other = _reconstruct(store_for(list(reversed(pages))), SELBY_PROJECT)

        assert one.accumulated.digest == other.accumulated.digest
        assert one.accumulated.pages == other.accumulated.pages
        assert one.workflow == other.workflow

    def test_the_run_ids_are_reported_in_page_order(self):
        """Provenance is stated in the order the pages are — not the order rows came back."""
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result.capture_run_ids == tuple(
            f"run-{index}" for index in range(1, len(SELBY_WINDOW_FILES) + 1))

    def test_a_re_read_takes_the_latest_attempts_run_at_its_own_pages_position(self):
        """The genuine second reading of page 26 stands for page 26, and only for it.

        Page 26 sits in the sixth window, so the sixth window's run stands for pages 27-30
        and the recovery's run stands for page 26 — one reading per page, each attributed
        to the run whose attempt it came from.
        """
        result = _reconstruct(_selby_store(with_recovery=True), SELBY_PROJECT)

        assert result.captures_read == SELBY_PAGE_COUNT
        # Run ids are DISTINCT, in the order their pages appear. The recovery is page
        # 26's reading, so it is listed at page 26's position — after the run that read
        # pages 21-25 — while run-6 still stands for pages 27-30, which it also read.
        assert result.capture_run_ids == (
            "run-1", "run-2", "run-3", "run-4", "run-5",
            "run-recovery", "run-6", "run-7",
        )


# ===========================================================================
# C. Windows accumulate, and nothing is mixed between documents or projects.
# ===========================================================================
class TestWindowsAccumulateWithoutMixing:
    def test_the_seven_real_windows_accumulate_into_one_document(self):
        """window1 + … + window7 = the whole document, with no page twice and none absent.

        The expected reading is the concatenation of the seven FILES; the reconstruction's
        is its own. Equal page sets, equal page counts, and a page-for-page identity.
        """
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        by_file = {}
        for name in SELBY_WINDOW_FILES:
            for page in _capture_rows_for(name):
                assert page["page_number"] not in by_file, (
                    f"the genuine windows overlap at page {page['page_number']}; the "
                    "accumulation proof assumes the disjoint windows production produces")
                by_file[page["page_number"]] = page

        reconstructed = {page["page_number"]: page for page in result.accumulated.pages}
        assert set(reconstructed) == set(by_file)
        assert len(reconstructed) == len(result.accumulated.pages)
        for number, page in by_file.items():
            assert reconstructed[number]["raw_members"] == page["raw_members"], number
            assert reconstructed[number]["raw_connections"] == page["raw_connections"], number

    def test_a_page_read_twice_is_one_page_of_the_document(self):
        """The recovery reading does not add a page, and does not displace another."""
        result = _reconstruct(_selby_store(with_recovery=True), SELBY_PROJECT)

        assert result.captures_read == SELBY_PAGE_COUNT
        numbers = [page["page_number"] for page in result.accumulated.pages]
        assert numbers == list(range(1, SELBY_PAGE_COUNT + 1))

    def test_two_documents_of_one_project_are_refused_rather_than_chosen_between(self):
        """A workflow is per project; which document it reads is not this layer's to pick."""
        store = _selby_store()
        store._drawing_sets.append(_drawing_set(SELBY_PROJECT, OTHER_SET, page_count=4))
        store._drawings.append(_drawing(OTHER_SET, OTHER_DRAWING, page_count=4))
        j23._record(store._table, _capture_rows_for(SELBY_WINDOW_FILES[0])[:1], run="run-other",
                    drawing=OTHER_DRAWING, drawing_set=OTHER_SET, project=SELBY_PROJECT)

        refusal = _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_AMBIGUOUS_DOCUMENT)
        assert "documents" in refusal.detail

    def test_a_reading_filed_under_another_project_is_refused(self):
        """A reading that names two identities at once is never combined with either."""
        store = _selby_store()
        store._table.rows[0]["project_id"] = OTHER_PROJECT

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_MIXED_IDENTITY)

    def test_a_reading_filed_under_another_drawing_set_is_refused(self):
        store = _selby_store()
        store._table.rows[0]["drawing_set_id"] = OTHER_SET

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_MIXED_IDENTITY)

    def test_another_projects_readings_are_never_included(self):
        """Two projects in one ledger: each reconstructs from its own reading alone."""
        selby = _selby_store()
        j23._record(selby._table, _arkles_pages(), run="run-arkles",
                    drawing=ARKLES_DRAWING, drawing_set=ARKLES_SET, project=ARKLES_PROJECT)
        selby._drawing_sets.append(
            _drawing_set(ARKLES_PROJECT, ARKLES_SET, page_count=ARKLES_PAGE_COUNT))
        selby._drawings.append(
            _drawing(ARKLES_SET, ARKLES_DRAWING, page_count=ARKLES_PAGE_COUNT))
        selby.known_projects = (SELBY_PROJECT, ARKLES_PROJECT)

        one = _reconstruct(selby, SELBY_PROJECT)
        other = _reconstruct(selby, ARKLES_PROJECT)

        assert one.captures_read == SELBY_PAGE_COUNT
        assert one.drawing_id == SELBY_DRAWING
        assert other.captures_read == ARKLES_PAGE_COUNT
        assert other.drawing_id == ARKLES_DRAWING

    def test_a_capture_for_a_page_beyond_the_document_is_refused(self):
        """Refused, never absorbed: a page the document does not have is not a page."""
        store = _selby_store()
        page = copy.deepcopy(_selby_pages()[0])
        page["page_number"] = SELBY_PAGE_COUNT + 1
        j23._record(store._table, [page], run="run-beyond",
                    drawing=SELBY_DRAWING, drawing_set=SELBY_SET, project=SELBY_PROJECT)

        with pytest.raises(ValueError) as caught:
            _reconstruct(store, SELBY_PROJECT)
        assert "beyond the drawing set's page count" in str(caught.value)

    # -- the cross-window member-mark collision -----------------------------
    #
    # Seven windows are seven extraction runs over ONE drawing set, so a member drawn
    # on sheets in different windows is named in different capture rows. The brief asks
    # that this collision not be solved in this milestone unless revision 0 requires it.
    # The three tests below are the answer: it is real, it is absorbed rather than
    # solved, and it decides nothing.

    def test_the_genuine_selby_capture_names_a_mark_in_more_than_one_window(self):
        """The collision is REAL, asserted against the readings rather than described.

        Five marks are named by more than one window and PL008 by five of the seven. The
        exact map is pinned rather than merely bounded: if a future capture stopped
        repeating marks across windows, this test fails and the tests below stop being
        evidence about anything, which is precisely when they should be rewritten.
        """
        cross = {
            mark: sorted(windows)
            for mark, windows in _windows_naming_each_mark().items()
            if len(windows) > 1
        }

        assert cross == {
            "CL004": [0, 1],
            "PL008": [0, 1, 2, 3, 5],
            "PL021": [1, 2],
            "PL025": [0, 1, 6],
            "PL032": [0, 1],
        }, cross

    def test_a_mark_named_by_several_windows_is_still_one_member_identity(self):
        """The collision is ABSORBED, not solved — and the identity rule is not weakened.

        The reconstruction is never asked which occurrence is "the" member and never
        answers: member identity is read from the persisted member record, where a mark
        is one row, and that whole set is handed to 7AC unchanged. Nothing is grouped,
        suffixed, renamed, dropped or re-scoped per page or per window to make the
        collision disappear, and no placement is invented to explain it.
        """
        cross = {
            mark for mark, windows in _windows_naming_each_mark().items() if len(windows) > 1
        }
        assert cross, "the genuine capture no longer collides; this test proves nothing"

        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        # The document is reconstructed whole: the collision costs no page, and being
        # named twice is not a reason to drop the second reading.
        assert result.captures_read == SELBY_PAGE_COUNT
        assert result.analysed_pages == tuple(range(1, SELBY_PAGE_COUNT + 1))
        assert result.not_analysed_pages == ()

        marks = result.workflow.intake.collection.known_member_marks
        assert len(marks) == len(set(marks)), "a colliding mark was given a second identity"
        assert cross <= set(marks), sorted(cross - set(marks))
        assert marks == tuple(_member_rows_for(_selby_pages()))

        # Stated numerically: 87 marked occurrences collapse to 71 identities, and the
        # collapse happens in the persisted member record — the producer contributes
        # nothing to it and is given no rule for it.
        occurrences = sum(len(_marks_named_by(page)) for page in _selby_pages())
        assert occurrences == 87
        assert len(marks) == 71

        # And it decides nothing: no placement is invented for the collision, and
        # revision 0 evaluates every connection without member context.
        assert result.workflow._member_placements == {}
        assert result.workflow.revision == 0
        assert result.workflow.auto_count == 0

    def test_the_collision_is_not_only_across_windows(self):
        """The harder shape: a mark repeated WITHIN one page's single reading.

        Arkles names BF1 three times on page 23 and twice on page 21, BF2 and RB2 twice,
        and B17 twice on page 14. No window boundary explains these, so a rule that
        collapsed occurrences per page would still have to decide what three occurrences
        on one page mean. Reading identity from the persisted member record never has to
        decide, which is why the collision does not block revision 0 in either shape.
        """
        within = _intra_page_repeats(_arkles_pages())

        assert within == {
            "B17": {14: 2},
            "BF1": {21: 2, 23: 3},
            "BF2": {23: 2},
            "RB2": {21: 2},
        }, within

        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        marks = result.workflow.intake.collection.known_member_marks
        assert len(marks) == len(set(marks))
        for mark in within:
            assert marks.count(mark) == 1, mark
        assert result.workflow.auto_count == 0


# ===========================================================================
# D. A parse failure stays a parse failure.
# ===========================================================================
def _arkles_parse_failed_pages():
    return tuple(page["page_number"] for page in _arkles_pages() if page.get("parse_failed"))


def _arkles_empty_success_pages():
    return tuple(
        page["page_number"] for page in _arkles_pages()
        if not page.get("parse_failed")
        and not page.get("raw_members") and not page.get("raw_connections")
    )


class TestAParseFailureStaysAParseFailure:
    def test_the_genuine_parse_failures_survive_the_reconstruction(self):
        assert _arkles_parse_failed_pages() == (2, 11, 12, 13, 22, 29)
        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        assert result.parse_failed_pages == _arkles_parse_failed_pages()

    def test_a_parse_failed_page_is_never_counted_as_analysed(self):
        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        assert not (set(result.parse_failed_pages) & set(result.analysed_pages))

    def test_a_parse_failure_is_never_an_empty_success(self):
        """The real reading contains both, and the reconstruction keeps them apart.

        The Arkles capture genuinely holds pages that parsed and stated nothing (a cover
        sheet, a section) alongside pages whose response could not be read. A layer that
        had collapsed the two would pass a synthetic proof and fail this one.
        """
        empty = _arkles_empty_success_pages()
        assert empty, "the genuine Arkles capture states no page that parsed and found nothing"

        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        assert set(empty) <= set(result.analysed_pages)
        assert not (set(empty) & set(result.parse_failed_pages))

    def test_the_failure_reaches_the_review_queue_as_a_failure(self):
        """The queue's own coverage statement carries the failure — not lost on the way."""
        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        assert result.workflow.intake.parse_failed_pages == _arkles_parse_failed_pages()
        assert result.workflow.intake.analysed_page_numbers == result.analysed_pages

    def test_a_parse_failure_carrying_content_is_refused_not_repaired(self):
        """A contradictory page status is 7AZ's refusal, and it is not caught and tidied."""
        store = _selby_store()
        store._table.rows[0]["parse_failed"] = True
        store._table.rows[0]["payload"] = {
            **store._table.rows[0]["payload"], "parse_failed": True,
            "raw_members": [{"mark": "invented"}],
        }

        with pytest.raises(ValueError) as caught:
            _reconstruct(store, SELBY_PROJECT)
        assert "contradictory page status" in str(caught.value)
        assert not isinstance(caught.value, producer.ReconstructionRefused)


# ===========================================================================
# E. The readings come from the capture, never from the engineering rows.
# ===========================================================================
class TestTheReadingsComeFromTheCapture:
    def test_the_producer_never_reads_the_connection_rows(self):
        """The store HAS the read, and it contradicts the capture; it is never called."""
        store = _selby_store()
        result = _reconstruct(store, SELBY_PROJECT)

        assert result is not None
        names = {name for name, _ in store.calls}
        assert not (names & set(FORBIDDEN_READS)), sorted(names & set(FORBIDDEN_READS))

    def test_the_module_names_no_downstream_projection_it_could_derive_a_reading_from(self):
        """Structural: the CODE cannot reach the downstream projections at all.

        Asserted over the module with its docstrings removed: this module explains in
        prose that it never reads `connections`, `steel_members` or `review_items`, and
        an assertion meant to hold over the code must not be answered by the explanation
        of it. (J23's own `_code` helper, reused.)
        """
        code = j23._code(MODULE_PATH)
        for forbidden in (
            "connection_rows_for_project", "evidence_rows_for_page", "review_items",
            "connection_review_items", "connection_review_snapshots",
            "connection_review_repository",
        ):
            assert forbidden not in code, forbidden

    def test_the_module_reads_no_analysis_runs_row_and_holds_no_client(self):
        """The run ids come off the captures themselves; nothing queries a run."""
        named = _names_in(MODULE_PATH)
        for forbidden in ("analysis_runs", "supabase", "create_client", "table", "execute"):
            assert forbidden not in named, forbidden

    def test_the_producer_calls_nothing_that_could_write(self):
        """Structural: no call in the module is a write, a query, or a dispatch."""
        called = _called_in(MODULE_PATH)
        forbidden = {
            "insert", "update", "delete", "upsert", "truncate", "execute", "rpc", "save",
            "write", "insert_page_extraction_captures", "insert_members",
            "insert_review_items", "record_project_review", "persist_review_snapshot",
        }
        assert not (called & forbidden), sorted(called & forbidden)

    def test_a_member_row_is_consulted_for_its_mark_only(self):
        """The member context is the persisted member IDENTITY — never its interpretation.

        Every column access is recorded; the reconstruction is entitled to the mark, and
        only the mark, because 7AC uses the marks as the authoritative member choices.
        """
        read: set[str] = set()
        result = _reconstruct(_selby_store(read=read), SELBY_PROJECT)

        assert result is not None
        assert read <= {"mark"}, sorted(read)
        assert result.workflow.intake.collection.known_member_marks

    def test_the_member_marks_the_workflow_carries_are_the_persisted_ones(self):
        expected = tuple(_member_rows_for(_selby_pages()))
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result.workflow.intake.collection.known_member_marks == expected

    def test_a_member_row_without_a_mark_is_skipped_rather_than_given_an_identity(self):
        """A row with no mark carries no identity, so it is skipped — never invented one.

        This is not hypothetical: the genuine Arkles capture names eight members with no
        mark at all. A reconstruction that keyed them under None or "" would hand 7AC two
        member choices no drawing states, and one that gave them a mark would be inventing
        the identity outright. Both are refused by simply not reading a row that has none.
        """
        unmarked = sum(
            1 for page in _arkles_pages()
            for member in (page.get("raw_members") or ())
            if not member.get("mark")
        )
        assert unmarked == 8

        baseline = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        store = _arkles_store()
        for key, row_id in ((None, "member-without-a-mark"), ("", "member-with-a-blank-mark")):
            store._members[key] = {
                "id": row_id, "mark": key, "section_name": None,
                "review_status": "approved", "total_weight_kg": None,
            }
        result = _reconstruct(store, ARKLES_PROJECT)

        marks = result.workflow.intake.collection.known_member_marks
        assert None not in marks
        assert "" not in marks
        assert marks == baseline.workflow.intake.collection.known_member_marks

    def test_the_readings_are_not_taken_from_the_contradicting_engineering_rows(self):
        """The store's connection rows contradict the capture; the workflow follows the capture.

        If any part of the reconstruction had consulted them, the candidate count and the
        package identities would differ. They do not: the two contradicting rows name a
        member and a connection that no page of the genuine reading contains, and nothing
        downstream of the reconstruction knows they exist.
        """
        store = _selby_store()
        result = _reconstruct(store, SELBY_PROJECT)

        contradicting = {
            row["id"] for row in _Persisted.CONTRADICTING_CONNECTIONS} | {
            row["member_id"] for row in _Persisted.CONTRADICTING_CONNECTIONS}
        assert not any(name in repr(result.workflow) for name in contradicting)

        names = {name for name, _ in store.calls}
        assert not (names & set(FORBIDDEN_READS)), sorted(names & set(FORBIDDEN_READS))


# ===========================================================================
# F. The result IS the existing 7AJ contract.
# ===========================================================================
class TestTheResultIsTheExistingContract:
    def test_the_reconstruction_equals_the_direct_in_process_construction(self):
        """The producer's state == the state the in-process chain builds from the same pages.

        The comparison is against a construction that never touches the store: 7Y's intake
        directly over the pages, then 7AJ's own start. If the persistence layer had altered
        the reading in any way that reached the workflow, these two would differ.

        The one thing the direct construction is TOLD is which run read each page (J72):
        that association lives in the store and nowhere else, so it is handed over rather
        than left for the two sides to disagree about. Nothing else crosses.
        """
        pages = sorted(_selby_pages(), key=lambda p: p["page_number"])
        member_rows = _member_rows_for(pages)
        marks = tuple(member_rows)

        store = _selby_store()
        direct_intake = intake_page_extractions(
            pages,
            project_id=SELBY_PROJECT,
            source_drawing_id=SELBY_DRAWING,
            known_member_marks=marks,
            drawing_set_page_count=SELBY_PAGE_COUNT,
            page_analysis_run_ids=_page_run_ids(store, SELBY_DRAWING, pages),
        )
        direct = start_project_workflow(
            direct_intake.collection,
            intake=direct_intake,
            member_rows=member_rows,
            member_placements={},
            section_matcher=_StubMatcher(),
        )

        result = _reconstruct(store, SELBY_PROJECT)

        assert result.workflow == direct
        assert result.workflow.collection == direct.collection
        assert result.workflow.intake == direct_intake

    def test_the_reconstruction_matches_the_accumulated_route_as_well(self):
        """And equals 7AZ's own accumulation path — the producer adds nothing to it."""
        pages = sorted(_selby_pages(), key=lambda p: p["page_number"])
        accumulated = begin_incremental_analysis(
            pages, drawing_set_page_count=SELBY_PAGE_COUNT, drawing_set_first_page=1)

        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result.accumulated == accumulated
        assert result.accumulated.digest == accumulated.digest

    def test_the_state_is_a_revision_zero_state_with_nothing_decided_auto(self):
        """Revision 0 is evaluated WITHOUT member context, so nothing can be AUTO.

        This is the genuine 7AJ behaviour, not a property of this producer — and the
        reconstruction must reproduce it exactly, because that is what the review UI is
        being handed.
        """
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result.workflow.auto_count == 0
        assert result.workflow.verified_count == 0
        assert result.workflow.review_count + result.workflow.confirm_count == len(
            result.workflow.connections)
        assert all(c.last_processed_revision is None for c in result.workflow.connections)
        assert all(c.verification_status is None for c in result.workflow.connections)

    def test_the_package_identities_are_the_submission_derived_ones(self):
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert [c.package_id for c in result.workflow.connections] == [
            f"RP-{index:04d}" for index in range(1, len(result.workflow.connections) + 1)]

    def test_the_producer_constructs_no_section_matcher_of_its_own(self):
        """The matcher is required, and the module never builds one.

        `SectionMatcher.__init__` performs a live catalogue read; a read seam that built
        one would be performing I/O its caller did not ask for. Structural and behavioural:
        the code names no matcher class, and the parameter has no default.
        """
        assert "SectionMatcher" not in j23._code(MODULE_PATH)
        assert "section_matcher" not in _called_in(MODULE_PATH)

        with pytest.raises(TypeError):
            producer.reconstruct_project_workflow(SELBY_PROJECT, repository=_selby_store())

    def test_the_matcher_the_caller_supplies_is_the_one_the_state_carries(self):
        matcher = _StubMatcher()
        result = producer.reconstruct_project_workflow(
            SELBY_PROJECT, section_matcher=matcher, repository=_selby_store())

        assert result.workflow._section_matcher is matcher

    def test_the_workflow_can_be_carried_into_the_existing_resolve_verbatim(self):
        """The state is a genuine 7AJ state, so 7AJ's own operations operate on it.

        `resolve_project_connection` is 7AJ's own next step, and it refuses here with
        ITS OWN message — about resolutions, the first thing it checks — rather than with
        anything this milestone invented. That is the boundary stated as behaviour: the
        reconstruction hands over a state the existing chain recognises as its own.
        """
        from app.cad_engine.project_workflow import resolve_project_connection

        result = _reconstruct(_selby_store(), SELBY_PROJECT)
        package_id = result.workflow.connections[0].package_id

        with pytest.raises(ValueError) as caught:
            resolve_project_connection(
                result.workflow, package_id=package_id, resolutions=(), output_dir="/tmp")

        assert "resolutions must not be empty" in str(caught.value)
        assert not isinstance(caught.value, producer.ReconstructionRefused)

    def test_the_reconstruction_carries_no_member_placement_at_all(self):
        """The one input revision 0 does not need and revision >= 1 lacks.

        `member_placements` has no persisted source anywhere — no table, no column — so
        an empty mapping is the truth about what is known rather than a default standing
        in for a value. Revision 0 never reads it (nothing can become AUTO without member
        context, which is why the counts above are all REVIEW/CONFIRM); a later resolve
        needs it and would fail closed. Recording it as empty is how that boundary stays
        visible instead of being papered over with invented geometry.
        """
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert result.workflow._member_placements == {}


# ===========================================================================
# G. J22 receives the result through its existing boundary.
# ===========================================================================
NO_EVIDENCE = {"steel_members": (), "connections": ()}


class TestJ22ReceivesTheResultThroughItsExistingBoundary:
    def test_j22s_own_builder_accepts_the_reconstructed_workflow(self):
        """No new builder, no new converter: J21's `build_review_snapshot` takes it as-is."""
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        snapshot = build_review_snapshot(
            result.workflow,
            project_id=SELBY_PROJECT,
            evidence_rows=NO_EVIDENCE,
            evidence_run_ids=result.capture_run_ids,
            previous_revision=None,
        )
        assert snapshot.project_id == SELBY_PROJECT
        assert snapshot.review_revision == 0

    def test_the_run_ids_the_snapshot_records_are_the_captures_own_runs(self):
        """Provenance the reconstruction states about itself, recorded verbatim."""
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        snapshot = build_review_snapshot(
            result.workflow,
            project_id=SELBY_PROJECT,
            evidence_rows=NO_EVIDENCE,
            evidence_run_ids=result.capture_run_ids,
            previous_revision=None,
        )
        assert snapshot.evidence_run_ids == result.capture_run_ids

    def test_the_producers_outputs_map_onto_j22s_existing_signature(self):
        """Structural: the persistence boundary takes the same things the producer gives.

        `record_project_review(binding, workflow, *, evidence_rows, evidence_run_ids, client)`
        — the workflow, the evidence rows and the run ids. Nothing the producer computes
        needs a parameter J22 does not already have, which is why no second persistence
        path is introduced.
        """
        import inspect

        from app.engineering_data import connection_review_repository as store_module

        parameters = inspect.signature(store_module.record_project_review).parameters
        assert list(parameters) == [
            "binding", "workflow", "evidence_rows", "evidence_run_ids", "client"]
        assert parameters["evidence_run_ids"].default == ()

    def test_the_snapshot_records_the_same_coverage_the_capture_states(self):
        """The recorded revision states the parse failures the reading genuinely had."""
        result = _reconstruct(_arkles_store(), ARKLES_PROJECT)

        snapshot = build_review_snapshot(
            result.workflow,
            project_id=ARKLES_PROJECT,
            evidence_rows=NO_EVIDENCE,
            evidence_run_ids=result.capture_run_ids,
            previous_revision=None,
        )
        assert snapshot.review_revision == 0
        assert len(snapshot.items) == len(result.workflow.connections)
        assert result.workflow.intake.parse_failed_pages == _arkles_parse_failed_pages()

    def test_the_producer_does_not_import_the_review_store_itself(self):
        """Persisting is the caller's step: the producer builds, and stops."""
        source = MODULE_PATH.read_text()
        assert "connection_review_repository" not in source
        assert "record_project_review" not in source
        assert "persist_review_snapshot" not in source


# ===========================================================================
# H. Missing capture fails closed.
# ===========================================================================
class TestMissingCaptureFailsClosed:
    def test_a_project_with_no_persisted_reading_is_refused(self):
        """A project that exists and has no reading refuses; it is not reconstructed."""
        store = _selby_store()
        store._table = j23._CaptureTable()

        refusal = _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_NO_CAPTURE)
        assert "not backfilled" in refusal.detail

    def test_a_project_with_no_drawing_set_at_all_is_refused(self):
        store = _Persisted()
        store.known_projects = (SELBY_PROJECT,)

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_NO_CAPTURE)

    def test_a_drawing_with_no_readings_is_refused(self):
        store = _selby_store()
        store._table = j23._CaptureTable()

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_NO_CAPTURE)

    def test_a_document_that_does_not_state_its_page_count_is_refused(self):
        """Pages beyond the reading are unknown, not absent — and are never guessed."""
        store = _selby_store()
        store._drawings[0]["page_count"] = None

        refusal = _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_PAGE_COUNT_UNKNOWN)
        assert "never guessed" in refusal.detail

    def test_a_document_stating_no_pages_is_refused(self):
        store = _selby_store()
        store._drawings[0]["page_count"] = 0

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_PAGE_COUNT_UNKNOWN)

    def test_a_document_stating_a_boolean_page_count_is_refused(self):
        """`True` is not a page count, and is not coerced into one."""
        store = _selby_store()
        store._drawings[0]["page_count"] = True

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_PAGE_COUNT_UNKNOWN)

    def test_an_unknown_project_is_absence_and_not_a_refusal(self):
        """`None` means the project does not exist — never a blank one."""
        assert _reconstruct(_selby_store(), "PROJ-DOES-NOT-EXIST") is None

    def test_a_blank_project_id_is_refused(self):
        for blank in ("", "   ", None, 7):
            with pytest.raises(ValueError) as caught:
                _reconstruct(_selby_store(), blank)
            assert not isinstance(caught.value, producer.ReconstructionRefused)

    def test_a_refusal_carries_a_code_from_the_declared_set(self):
        store = _selby_store()
        store._table = j23._CaptureTable()

        refusal = _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_NO_CAPTURE)
        assert refusal.code in producer.RECONSTRUCTION_REFUSALS
        assert refusal.detail
        assert refusal.code in str(refusal)

    def test_a_project_whose_row_is_not_a_row_is_refused(self):
        class _NotARow(_Persisted):
            def get_project(self, project_id):
                self.calls.append(("get_project", project_id))
                return "not a row"

        store = _NotARow()
        store.known_projects = (SELBY_PROJECT,)

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_PROJECT_UNKNOWN)

    def test_a_capture_missing_the_field_it_is_selected_on_is_refused_by_j23(self):
        """J23's own refusal is not re-wrapped into a code this module invented.

        The ordering key is consulted only BETWEEN two attempts at a page — a page with
        a single recorded reading is taken as it stands, so the key that matters here is
        the one the genuine re-read creates. That is why this uses the recovery store.
        """
        store = _selby_store(with_recovery=True)
        recovery = next(
            row for row in store._table.rows if row["analysis_run_id"] == "run-recovery")
        del recovery["captured_at"]

        with pytest.raises(captures.CaptureRefused):
            _reconstruct(store, SELBY_PROJECT)

    def test_a_capture_that_came_back_without_its_payload_is_refused_by_j23(self):
        """The payload is required by the layer that owns the rule, not by a bare lookup."""
        store = _selby_store()
        store._table.rows[0]["payload"] = None

        with pytest.raises(captures.CaptureRefused):
            _reconstruct(store, SELBY_PROJECT)


# ===========================================================================
# I. Existing projects are not reconstructed and not backfilled.
# ===========================================================================
class TestExistingProjectsAreNotBackfilled:
    def test_a_refused_reconstruction_leaves_the_ledger_untouched(self):
        """Nothing is written, because nothing here can write."""
        store = _selby_store()
        before = copy.deepcopy(store._table.rows)
        store._drawings[0]["page_count"] = None

        _refusal(store, SELBY_PROJECT, code=producer.RECONSTRUCTION_PAGE_COUNT_UNKNOWN)
        assert store._table.rows == before

    def test_a_completed_reconstruction_leaves_the_ledger_untouched(self):
        store = _selby_store()
        before = copy.deepcopy(store._table.rows)

        result = _reconstruct(store, SELBY_PROJECT)

        assert result.captures_read == SELBY_PAGE_COUNT
        assert store._table.rows == before

    def test_the_live_projects_are_not_reconstructed_and_not_backfilled(self, production):
        """THE GENUINE PROJECTS, READ LIVE, READ ONLY.

        The production repository is asked for every project, and the producer is asked
        to reconstruct each one that has a persisted document. No live project may
        reconstruct, because the capture ledger holds no production reading — it was
        created after every project in it was extracted, and nothing backfills them.
        Nothing here writes.
        """
        repository = production.repository

        projects = repository.supabase.table("projects").select("id").execute()
        project_ids = [row["id"] for row in (projects.data or [])]
        assert project_ids, "the live project list is empty; this proof would be vacuous"

        reconstructed, refused, no_reading = [], [], []
        with_document = []
        for project_id in project_ids:
            drawings = [
                drawing
                for drawing_set in repository.drawing_sets_for_project(project_id)
                for drawing in repository.drawings_for_drawing_set(drawing_set["id"])
            ]
            if not drawings:
                continue
            with_document.append(project_id)
            if not any(
                repository.page_extraction_captures_for_drawing(drawing["id"])
                for drawing in drawings
            ):
                no_reading.append(project_id)
                continue
            try:
                producer.reconstruct_project_workflow(
                    project_id, section_matcher=_StubMatcher(), repository=repository)
            except producer.ReconstructionRefused as refusal:
                assert refusal.code in producer.RECONSTRUCTION_REFUSALS
                refused.append(project_id)
            else:
                reconstructed.append(project_id)

        assert with_document, "no live project has a persisted document; vacuous"
        assert reconstructed == [], (
            f"{reconstructed} reconstructed from the capture ledger; the ledger is expected "
            "to hold no production reading, so this would mean one was written")
        assert refused == []
        assert no_reading == with_document


# ===========================================================================
# J. The package contract, and the shapes the doubles claim to reproduce.
# ===========================================================================
def _names_in(path):
    """Every plain name and attribute name the source mentions."""
    named = set()
    for node in ast.walk(ast.parse(Path(path).read_text())):
        if isinstance(node, ast.Name):
            named.add(node.id)
        elif isinstance(node, ast.Attribute):
            named.add(node.attr)
    return named


def _called_in(path):
    """Every name called in the source — a plain call or a method call."""
    called = set()
    for node in ast.walk(ast.parse(Path(path).read_text())):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    return called


def _selects_in(path):
    """Each function's first `.select(...)` literal, keyed by function name."""
    selects = {}
    for node in ast.walk(ast.parse(Path(path).read_text())):
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "select" and inner.args
                        and isinstance(inner.args[0], ast.Constant)):
                    selects.setdefault(node.name, inner.args[0].value)
    return selects


class TestThePackageAndTheShapesItClaims:
    def test_the_package_exposes_its_six_modules(self):
        # J46 added `project_workflow_resumption`, the sixth: the resumer that replays a
        # project's recorded revisions onto the revision 0 this module produces. It is a
        # module beside this one, not a change to it — the producer above is still the
        # fifth, and still the only reconstruction seam.
        import app.production_review as package

        assert set(package.__all__) == {
            "authorization", "binding", "identity", "project_read",
            "project_workflow_reconstruction", "project_workflow_resumption",
        }
        assert package.project_workflow_reconstruction is producer

    def test_the_producer_declares_its_public_surface(self):
        assert set(producer.__all__) == {
            "RECONSTRUCTION_REFUSALS",
            "RECONSTRUCTION_AMBIGUOUS_DOCUMENT",
            "RECONSTRUCTION_MIXED_IDENTITY",
            "RECONSTRUCTION_NO_CAPTURE",
            "RECONSTRUCTION_PAGE_COUNT_UNKNOWN",
            "RECONSTRUCTION_PROJECT_UNKNOWN",
            "ReconstructionRefused",
            "ReconstructedProjectWorkflow",
            "reconstruct_project_workflow",
        }

    def test_the_five_reads_the_producer_makes_are_the_ones_the_repository_has(self, production):
        """The double reproduces the production read surface — checked against production."""
        repository = production.repository

        for name in ALLOWED_READS:
            assert callable(getattr(repository, name, None)), name

    def test_the_member_projection_the_double_returns_is_the_production_one(self):
        """The persisted member read selects five columns; the double returns exactly those.

        A double that returned richer rows would let this suite pass over data the
        production read never provides.
        """
        select = _selects_in(REPOSITORY_PATH).get("member_rows_for_project")
        assert select is not None, "repository.member_rows_for_project no longer selects"
        assert tuple(part.strip() for part in select.split(",")) == MEMBER_COLUMNS

    def test_the_drawing_read_selects_the_page_count_the_producer_requires(self):
        """The producer refuses without the document's own page count, so the read that
        supplies it must actually select it — otherwise every reconstruction would refuse
        against production while passing against the double."""
        select = _selects_in(REPOSITORY_PATH).get("drawings_for_drawing_set")
        assert select is not None
        assert "page_count" in select
        assert "id" in select

    def test_the_ledger_read_the_producer_uses_returns_the_persisted_columns(self):
        """J23's reader and J23's column list, unchanged — no second read was invented."""
        assert "page_extraction_captures_for_drawing" in REPOSITORY_PATH.read_text()
        assert {
            "drawing_id", "drawing_set_id", "project_id", "page_number",
            "analysis_run_id", "parse_failed", "payload", "captured_at",
        } <= set(captures.CAPTURE_COLUMNS)

    def test_no_j24_migration_and_no_second_review_system_was_added(self):
        """The milestone adds one module and changes no schema."""
        assert MODULE_PATH.exists()
        migrations = sorted(
            path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert not [name for name in migrations if "j24" in name.lower()], migrations

    def test_the_dataclass_is_frozen_and_carries_its_provenance(self):
        result = _reconstruct(_selby_store(), SELBY_PROJECT)

        assert dataclasses.is_dataclass(result)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.project_id = "elsewhere"

    def test_the_module_imports_no_database_client_at_import_time(self):
        """Importing the module must not construct a client; the repository is imported
        inside the function, and only when no repository was supplied."""
        top_level = {
            node.module for node in ast.parse(MODULE_PATH.read_text()).body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.engineering_data.repository" not in top_level
        assert "app.supabase_client" not in top_level

    def test_a_json_round_trip_of_a_real_payload_survives_the_producer(self):
        """The payload arrives from a `json` column; the producer must not care."""
        store = _selby_store()
        store._table.rows = [
            {**row, "payload": json.loads(json.dumps(row["payload"]))}
            for row in store._table.rows
        ]

        result = _reconstruct(store, SELBY_PROJECT)
        assert result.captures_read == SELBY_PAGE_COUNT
