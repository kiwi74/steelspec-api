"""
J15 — PDF PRODUCTION COVERAGE TRUTH.

The production PDF path read pages and reported only what it READ. A 35-page
drawing set analysed up to a 30-page cap was persisted as a 30-page drawing set
(`total_pages = pages_processed`, app/pipeline.py), a page whose vision response
could not be parsed was indistinguishable from a page that was read and found to
contain no steel, and every project therefore carried a status derived from
evidence whose completeness had never been checked against the drawing set it
came from.

J15 makes the run account for every page of the uploaded document, and makes
that accounting a precondition of "done":

    TOTAL_PAGES          the document's own page count (read from the file)
    ANALYSED_PAGES       pages rendered, uploaded and put to the vision model
    PARSE_FAILED_PAGES   analysed pages whose response could not be read
    NOT_ANALYSED_PAGES   document pages this run never reached

    total == analysed + not_analysed        and        parse_failed ⊆ analysed

WHAT THIS FILE PROVES
---------------------
The real page count survives the processing cap; the pages beyond it are
recorded as not analysed rather than folded into the total; a parse failure is
counted, persisted, and kept distinct from an analysed page with no steel; no
incomplete or unprovable coverage can reach "done"; a fully read drawing set
still can; the J13 blockers are unchanged; the DXF path is untouched; and no
migration, column or engineering value was invented to make any of it work.

THE BOUNDARY
------------
Only the AI call is replaced — with the `PageExtraction` objects the vision
stage would have returned. Everything else is the production code: the real
`parse_pdf_and_save`/`_run_pipeline`, the real coverage arithmetic, the real
`page_count_of` reading a REAL PDF written by pypdf itself, the real repository
API answered in-process, and the real J13 derivation. No network call is made,
no AI key is needed, no live catalogue is read, and no production data is
written. The doubles and fixtures are J4's and J13's, reused rather than
restated; see those files for their own contracts.
"""

from __future__ import annotations

import ast
import itertools
import types
from pathlib import Path

import pytest

from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j4_production_extraction_report_truth as j4

production = j4.production
pdf_boundary = j4.pdf_boundary

REPO = j13.REPO
EXACT_TOKEN = j13.EXACT_TOKEN
UNMATCHED_TOKEN = j13.UNMATCHED_TOKEN
DONE = j13.DONE
REVIEW = j13.REVIEW
ACCEPTED_STATUS = j13.ACCEPTED_STATUS
BLOCKING_STATUS = j13.BLOCKING_STATUS

# J11's own vocabulary, as its suite pins it. Written here rather than imported
# because this file only needs one spelling of it: a line that must NOT be read
# back as a coverage record.
DXF_DROP_LINE = "DXF extraction: 1 drawing evidence item was not extracted."

COVERAGE_MODULE_PATH = REPO / "app" / "validation" / "page_coverage.py"
ANALYZER_PATH = REPO / "app" / "ai_analysis" / "pdf_vision_analyzer.py"
PIPELINE_PATH = REPO / "app" / "pipeline.py"
DXF_PARSER_PATH = REPO / "app" / "drawing_reading" / "dxf_parser.py"
CAD_COVERAGE_PATH = REPO / "app" / "cad_engine" / "production_coverage.py"

# The brief's own worked example.
DOCUMENT_PAGES = 35
CAPPED_ANALYSED = 30

_PAGE = None


@pytest.fixture(scope="module", autouse=True)
def _bind_page_factory(production):
    """Binds the real production PageExtraction into the `_page` helpers."""
    global _PAGE
    _PAGE = production.PageExtraction
    yield
    _PAGE = None


def _page(number, members, *, connections=()):
    """One genuine production `PageExtraction`, as the vision stage reports it."""
    return _PAGE(
        page_number=number,
        drawing_number="J15-DWG-001",
        drawing_title="J15 coverage truth",
        revision="A",
        raw_members=list(members),
        raw_connections=list(connections),
    )


def _failed_page(number):
    """The object the analyzer appends when a page's response cannot be read —
    and nothing more than that: it states no drawing and no content."""
    return _PAGE(page_number=number, parse_failed=True)


def _empty_page(number):
    """A page that WAS read, on which the drawing states no steel."""
    return _PAGE(page_number=number, raw_members=[], raw_connections=[])


def _write_pdf(path, pages):
    """A REAL PDF of `pages` blank pages, written by pypdf itself.

    Not a fixture and not a stub: `page_count_of` must read a genuine document,
    so the document has to be one.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as handle:
        writer.write(handle)
    return str(path)


def _write_unreadable(path):
    """The placeholder bytes J4 already uses: a file that is not a PDF.

    A drawing set whose OWN page count cannot be established — the state that
    must be recorded as unknown rather than assumed to match what was read.
    """
    path.write_bytes(b"%PDF-1.4\n% J15 test-only placeholder source\n")
    return str(path)


@pytest.fixture()
def coverage(production):
    """The J15 coverage contract, imported after `production` so that it is
    added to sys.modules under that fixture's own boundary and removed by its
    teardown like every other app module this file touches."""
    import app.validation.page_coverage as coverage_module

    return coverage_module


@pytest.fixture()
def derive(production):
    """The genuine J13 derivation, exactly as the production PDF path calls it."""
    return production.pipeline.derive_project_status


@pytest.fixture()
def run_document(production, monkeypatch, tmp_path):
    """Drives the GENUINE production PDF boundary over a REAL written document.

        parse_pdf_and_save
          -> real page accounting, with `page_count_of` reading the real file
          -> real validate_extraction over the reported pages
          -> real production row construction
          -> real repository API, answered in-process
          -> real J13 status derivation

    Only the AI call itself is replaced: `analyze_pdf_pages` returns the
    `PageExtraction` objects the vision stage would have returned, so a run that
    was capped, or that hit pages it could not read, is expressed as data rather
    than by weakening any production code.
    """
    counter = itertools.count(1)

    def run(page_extractions, *, document_pages, readable=True, project_id=None):
        project_id = project_id or f"j15-project-{next(counter)}"
        repository = j4._RecordingRepository()
        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(
            production.pipeline, "analyze_pdf_pages",
            lambda *a, **k: list(page_extractions),
        )

        source = tmp_path / f"j15-{next(counter)}.pdf"
        if readable:
            _write_pdf(source, document_pages)
        else:
            _write_unreadable(source)

        summary = production.pipeline.parse_pdf_and_save(
            str(source), project_id, "j15-user", f"j15-user/{source.name}"
        )
        return types.SimpleNamespace(
            project_id=project_id,
            summary=summary,
            repository=repository,
            analysis_run=repository.analysis_run_updates[-1],
            drawing_set=repository.drawing_set_updates[-1],
            drawing_meta=repository.drawing_meta,
            project_row=repository.project_summary,
            members=repository.members,
        )

    return run


def _capped_pages(count=CAPPED_ANALYSED):
    """The pages a capped run reports: one resolving member on each, starting
    at page 1 and stopping at the cap."""
    return [_page(number, [j4._member(f"M{number}", EXACT_TOKEN)])
            for number in range(1, count + 1)]


# ===========================================================================
# 1. The coverage contract — the four quantities and the one identity.
# ===========================================================================
class TestTheCoverageContract:
    def test_the_four_quantities_and_the_identity(self, coverage):
        """The brief's own worked example: 35 pages, 30 analysed, 2 of those
        unread — and five pages that were never looked at."""
        result = coverage.coverage_of(
            total_pages=DOCUMENT_PAGES,
            page_numbers=range(1, CAPPED_ANALYSED + 1),
            parse_failed_page_numbers=(4, 9),
        )
        assert result.total_pages == DOCUMENT_PAGES
        assert result.analysed_pages == CAPPED_ANALYSED
        assert result.parse_failed_pages == 2
        assert result.not_analysed_pages == 5
        assert result.analysed_pages + result.not_analysed_pages == result.total_pages
        assert result.is_complete is False

    def test_a_gapless_read_of_the_whole_set_is_complete(self, coverage):
        result = coverage.coverage_of(
            total_pages=10, page_numbers=range(1, 11), parse_failed_page_numbers=(),
        )
        assert (result.total_pages, result.analysed_pages,
                result.not_analysed_pages, result.parse_failed_pages) == (10, 10, 0, 0)
        assert result.is_complete is True

    def test_a_single_unreadable_page_is_not_a_complete_coverage(self, coverage):
        result = coverage.coverage_of(
            total_pages=10, page_numbers=range(1, 11), parse_failed_page_numbers=(7,),
        )
        assert result.parse_failed_pages == 1
        assert result.not_analysed_pages == 0
        assert result.is_complete is False

    def test_an_unknown_page_count_is_not_a_complete_one(self, coverage):
        """`None` is a real answer — the document did not state a page count this
        run could read — and it may never be filled in with what was read."""
        result = coverage.coverage_of(total_pages=None, page_numbers=[1, 2, 3])
        assert result.total_pages is None
        assert result.analysed_pages == 3
        assert result.not_analysed_pages is None
        assert result.is_known is False
        assert result.identity_holds is False
        assert result.is_complete is False

    @pytest.mark.parametrize("total,pages,failed", [
        (2, [1, 2, 3], ()),            # more pages read than the document has
        (10, [1, 11], ()),             # a page outside the document's range
        (10, [1, 1], ()),              # the same page twice
        (0, [1], ()),                  # a "page count" that is not one
        (-3, [1], ()),                 # neither is a negative one
        (10, [1], (2,)),               # a failure on a page never analysed
    ])
    def test_counts_that_contradict_each_other_collapse_to_unknown(
        self, coverage, total, pages, failed
    ):
        """A record whose own counts disagree proves nothing, so the total is
        refused rather than one side being trusted over the other."""
        result = coverage.coverage_of(
            total_pages=total, page_numbers=pages, parse_failed_page_numbers=failed,
        )
        assert result.total_pages is None
        assert result.not_analysed_pages is None
        assert result.is_complete is False

    def test_the_persisted_line_round_trips(self, coverage):
        for result in (
            coverage.coverage_of(total_pages=10, page_numbers=range(1, 11)),
            coverage.coverage_of(
                total_pages=DOCUMENT_PAGES, page_numbers=range(1, CAPPED_ANALYSED + 1),
                parse_failed_page_numbers=(4, 9),
            ),
            coverage.coverage_of(total_pages=None, page_numbers=[1, 2]),
        ):
            line = result.as_line()
            assert line.startswith(coverage.COVERAGE_PREFIX)
            assert coverage.coverage_from_warnings([line]) == result, line

    def test_the_unreadable_line_names_what_it_does_not_know(self, coverage):
        line = coverage.coverage_of(total_pages=None, page_numbers=[1, 2]).as_line()
        assert f"total={coverage.COVERAGE_UNKNOWN}" in line
        assert f"not_analysed={coverage.COVERAGE_UNKNOWN}" in line

    def test_the_reader_refuses_anything_it_did_not_write(self, coverage):
        """Absence and corruption are the same answer here: no coverage record."""
        assert coverage.coverage_from_warnings([]) is None
        assert coverage.coverage_from_warnings(None) is None
        assert coverage.coverage_from_warnings([DXF_DROP_LINE]) is None
        assert coverage.coverage_from_warnings(
            ["PDF coverage: total=10 analysed=8 not_analysed=2"]
        ) is None
        assert coverage.coverage_from_warnings(
            ["PDF coverage: total=10 analysed=x parse_failed=0 not_analysed=0"]
        ) is None
        assert coverage.coverage_from_warnings(
            ["PDF coverage: total=10 analysed=10 parse_failed=0 not_analysed=-1"]
        ) is None


# ===========================================================================
# 2. The document's own page count — read, not inferred.
# ===========================================================================
class TestTheDocumentPageCount:
    def test_the_count_is_read_from_the_document_itself(self, production, tmp_path):
        for pages in (1, 7, DOCUMENT_PAGES):
            path = _write_pdf(tmp_path / f"j15-{pages}.pdf", pages)
            assert production.pipeline.page_count_of(path) == pages

    def test_the_count_is_not_the_processing_cap(self, production, tmp_path):
        """The count is the DOCUMENT's, so it is the same number whatever the cap
        is — which is exactly why the two could be confused before."""
        path = _write_pdf(tmp_path / "j15-long.pdf", DOCUMENT_PAGES)
        assert production.pipeline.page_count_of(path) == DOCUMENT_PAGES
        assert production.pipeline.page_count_of(path) != CAPPED_ANALYSED

    def test_the_count_does_not_consult_the_processing_cap(self):
        """`page_count_of` reads the file; it does not decide how much of it to
        read. Nothing in its body names the cap."""
        tree = ast.parse(ANALYZER_PATH.read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "page_count_of"
        )
        named = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
        assert "MAX_PDF_PAGES" not in named

    def test_a_document_that_states_no_page_count_returns_none(self, production, tmp_path):
        """Fail-closed and total: no exception raised, and no number invented."""
        not_a_pdf = tmp_path / "j15-not-a-pdf.pdf"
        not_a_pdf.write_text("this is not a PDF")
        for name, path in (
            ("placeholder", _write_unreadable(tmp_path / "j15-placeholder.pdf")),
            ("missing", str(tmp_path / "j15-absent.pdf")),
            ("text", str(not_a_pdf)),
        ):
            assert production.pipeline.page_count_of(path) is None, name

    def test_the_extraction_still_asks_for_the_cap_and_nothing_more(self):
        """J15 adds accounting; it does not change how much is read. The AI
        stage is still called with the configured cap, with the same arguments."""
        source = PIPELINE_PATH.read_text()
        assert (
            "analyze_pdf_pages(filepath, user_id, project_id, drawing_id, MAX_PDF_PAGES)"
            in source
        )


# ===========================================================================
# 3. The production path — real document, real accounting, real derivation.
# ===========================================================================
class TestTheProductionPath:
    def test_a_fully_read_drawing_set_reaches_done(self, run_document):
        """The case the old accounting could not tell apart from a truncated one:
        three pages on the document, all three read, nothing held."""
        run = run_document(
            [
                _page(1, [j4._member("M1", EXACT_TOKEN)]),
                _page(2, [j4._member("M2", EXACT_TOKEN)]),
                _page(3, []),
            ],
            document_pages=3,
        )
        assert run.project_row["status"] == DONE
        assert run.summary["total_pages"] == 3
        assert run.summary["analysed_pages"] == 3
        assert run.summary["not_analysed_pages"] == 0
        assert run.summary["parse_failed_pages"] == 0
        assert run.project_row["status"] == DONE

    def test_a_drawing_set_longer_than_the_run_is_not_done(self, run_document):
        """The brief's example, through the production path: 35 pages on the
        document, 30 read, and the five never reached are stated as such."""
        run = run_document(_capped_pages(), document_pages=DOCUMENT_PAGES)
        assert run.summary["total_pages"] == DOCUMENT_PAGES
        assert run.summary["analysed_pages"] == CAPPED_ANALYSED
        assert run.summary["not_analysed_pages"] == 5
        assert run.project_row["status"] == REVIEW
        assert run.project_row["status"] == REVIEW

    def test_the_persisted_accounting_is_the_true_one(self, run_document):
        """`total_pages` is no longer silently rewritten to `pages_processed` —
        in the run record, the drawing set, the drawing, or the project."""
        run = run_document(_capped_pages(), document_pages=DOCUMENT_PAGES)
        assert run.analysis_run["total_pages"] == DOCUMENT_PAGES
        assert run.analysis_run["pages_processed"] == CAPPED_ANALYSED
        assert run.drawing_set["total_pages"] == DOCUMENT_PAGES
        assert run.drawing_set["pages_analysed"] == CAPPED_ANALYSED
        assert run.drawing_meta["page_count"] == DOCUMENT_PAGES
        assert run.summary["total_pages"] == DOCUMENT_PAGES
        assert run.summary["pages_processed"] == CAPPED_ANALYSED

    def test_the_coverage_record_is_persisted_on_the_project(self, coverage, run_document):
        run = run_document(_capped_pages(), document_pages=DOCUMENT_PAGES)
        stored = coverage.coverage_from_warnings(run.project_row["warnings"])
        assert stored is not None, run.project_row["warnings"]
        assert (stored.total_pages, stored.analysed_pages,
                stored.not_analysed_pages) == (DOCUMENT_PAGES, CAPPED_ANALYSED, 5)
        assert stored.is_complete is False

    def test_a_complete_run_states_its_completeness(self, coverage, run_document):
        """A complete coverage is written down too. Its absence must be able to
        mean "no record kept", not "nothing worth saying"."""
        run = run_document([_page(1, [j4._member("M1", EXACT_TOKEN)])], document_pages=1)
        stored = coverage.coverage_from_warnings(run.project_row["warnings"])
        assert stored is not None
        assert stored.is_complete is True

    def test_a_page_that_could_not_be_read_is_recorded_as_a_failure(self, run_document):
        run = run_document(
            [_page(1, [j4._member("M1", EXACT_TOKEN)]), _failed_page(2)],
            document_pages=2,
        )
        assert run.summary["parse_failed_pages"] == 1
        assert run.summary["not_analysed_pages"] == 0
        assert run.summary["analysed_pages"] == 2
        assert run.project_row["status"] == REVIEW

    def test_a_failed_page_is_not_an_empty_page(self, run_document):
        """The distinction the milestone turns on. Same document, same members,
        same arithmetic — one page read and empty, versus one page whose answer
        could not be read. Only the second is a gap."""
        first = [_page(1, [j4._member("M1", EXACT_TOKEN)])]
        read_and_empty = run_document(first + [_empty_page(2)], document_pages=2)
        unreadable = run_document(first + [_failed_page(2)], document_pages=2)

        assert read_and_empty.summary["parse_failed_pages"] == 0
        assert read_and_empty.project_row["status"] == DONE

        assert unreadable.summary["parse_failed_pages"] == 1
        assert unreadable.project_row["status"] == REVIEW
        # The failure is a reading failure, not a finding about the drawing: it
        # invents no member, no section and no unmatched token.
        assert unreadable.summary["members_extracted"] == 1
        assert unreadable.project_row["unmatched_sections"] == []
        assert [row["mark"] for row in unreadable.members] == ["M1"]

    def test_a_failed_page_is_not_a_drawing_that_states_nothing(self, run_document):
        """Both runs report zero members and neither may be called done — but
        only one of them has a reading failure to report."""
        unreadable = run_document([_failed_page(1)], document_pages=1)
        assert unreadable.summary["members_extracted"] == 0
        assert unreadable.summary["parse_failed_pages"] == 1
        assert unreadable.project_row["status"] == REVIEW

        empty_and_read = run_document([_empty_page(1)], document_pages=1)
        assert empty_and_read.summary["members_extracted"] == 0
        assert empty_and_read.summary["parse_failed_pages"] == 0
        # The whole document was read, so coverage is complete — and the project
        # is held anyway, by J13's own NO_EXTRACTED_EVIDENCE: a page that was
        # read and contained no steel is not a coverage problem, and this run's
        # coverage says exactly that.
        assert empty_and_read.summary["total_pages"] == 1
        assert empty_and_read.summary["analysed_pages"] == 1
        assert empty_and_read.summary["not_analysed_pages"] == 0
        assert empty_and_read.project_row["status"] == REVIEW

    def test_an_unreadable_document_is_not_assumed_to_be_fully_read(
        self, coverage, run_document
    ):
        """When the document's own page count cannot be established, the run says
        so — it does not fall back to the number of pages it read."""
        run = run_document(
            [_page(1, [j4._member("M1", EXACT_TOKEN)])],
            document_pages=1, readable=False,
        )
        assert run.summary["total_pages"] is None
        assert run.summary["not_analysed_pages"] is None
        assert run.summary["analysed_pages"] == 1
        assert run.project_row["status"] == REVIEW
        # Absence is persisted as absence: no 0 is written as if it were a count.
        assert "total_pages" not in run.analysis_run
        assert "total_pages" not in run.drawing_set
        assert run.drawing_meta["page_count"] is None
        stored = coverage.coverage_from_warnings(run.project_row["warnings"])
        assert stored is not None and stored.total_pages is None

    def test_the_project_payload_shape_is_unchanged(self, run_document):
        """J15 adds no column: the coverage record rides `warnings`, which
        already carries what a run did not read (the accepted J11 precedent)."""
        run = run_document([_page(1, [j4._member("M1", EXACT_TOKEN)])], document_pages=1)
        assert set(run.project_row) == j13.PDF_PROJECT_PAYLOAD_KEYS | {"project_id"}

    @pytest.mark.parametrize("behaviour", ["raises", "renders_nothing"])
    def test_a_run_that_read_nothing_at_all_still_fails(
        self, production, monkeypatch, tmp_path, behaviour
    ):
        """The global failure path, unchanged by J15: a run that could not read
        the document at ALL is a failure, not an empty drawing set — no project
        summary is written, so nothing may be called done."""
        repository = j4._RecordingRepository()

        def analyzer(*args, **kwargs):
            if behaviour == "raises":
                raise RuntimeError("the vision stage could not be reached")
            return []

        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", analyzer)
        source = _write_pdf(tmp_path / "j15-unreadable.pdf", 3)

        with pytest.raises(RuntimeError):
            production.pipeline.parse_pdf_and_save(
                str(source), "j15-failed", "j15-user", "j15-user/unreadable.pdf"
            )

        assert repository.analysis_run_updates[-1]["status"] == "failed"
        assert repository.drawing_set_updates[-1]["status"] == "failed"
        assert repository.project_summary is None

    def test_a_failed_page_adds_no_review_item_of_its_own(self, run_document):
        """A page that could not be read produces no engineering record — it is
        accounted for in the coverage, not invented into review_items."""
        clean = run_document([_page(1, [j4._member("M1", EXACT_TOKEN)])], document_pages=1)
        failed = run_document(
            [_page(1, [j4._member("M1", EXACT_TOKEN)]), _failed_page(2)],
            document_pages=2,
        )
        assert len(failed.repository.review_items) == len(clean.repository.review_items)
        assert [item["item_type"] for item in failed.repository.review_items] \
            == [item["item_type"] for item in clean.repository.review_items]


# ===========================================================================
# 4. J13's derivation — extended, not redesigned.
# ===========================================================================
class TestTheJ13Integration:
    def test_no_coverage_record_blocks_done(self, coverage, derive):
        """The fail-closed default: a caller that states no coverage has not
        shown that the whole drawing set was read."""
        decision = derive(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
            unmatched_sections=[], warnings=[],
        )
        assert decision.status == REVIEW
        assert decision.blockers == ("PAGE_COVERAGE_UNKNOWN:1",)

    def test_each_coverage_failure_has_its_own_blocker(self, coverage, derive):
        def decide(record):
            return derive(
                member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
                unmatched_sections=[], warnings=[], coverage=record,
            ).blockers

        assert decide(coverage.coverage_of(total_pages=None, page_numbers=[1])) == (
            "PAGE_COVERAGE_UNKNOWN:1",
        )
        assert decide(coverage.coverage_of(
            total_pages=DOCUMENT_PAGES, page_numbers=range(1, CAPPED_ANALYSED + 1),
        )) == ("PAGE_NOT_ANALYSED:5",)
        assert decide(coverage.coverage_of(
            total_pages=1, page_numbers=[1], parse_failed_page_numbers=[1],
        )) == ("PAGE_PARSE_FAILED:1",)
        assert decide(coverage.PageCoverage(
            total_pages=10, analysed_pages=8, parse_failed_pages=0, not_analysed_pages=0,
        )) == ("PAGE_COVERAGE_UNKNOWN:1",)

    def test_a_complete_coverage_leaves_the_j13_blockers_alone(self, coverage, derive):
        complete = coverage.coverage_of(total_pages=1, page_numbers=[1])

        def decide(**evidence):
            return derive(coverage=complete, **evidence).blockers

        assert decide(member_review_statuses=[ACCEPTED_STATUS],
                      connection_review_statuses=[ACCEPTED_STATUS],
                      unmatched_sections=[], warnings=[]) == ()
        assert decide(member_review_statuses=[BLOCKING_STATUS],
                      connection_review_statuses=[], unmatched_sections=[],
                      warnings=[]) == ("MEMBER_REVIEW_REQUIRED:1",)
        assert decide(member_review_statuses=[], connection_review_statuses=[],
                      unmatched_sections=[], warnings=[]) == ("NO_EXTRACTED_EVIDENCE:1",)
        assert decide(member_review_statuses=[ACCEPTED_STATUS],
                      connection_review_statuses=[],
                      unmatched_sections=[UNMATCHED_TOKEN],
                      warnings=[]) == ("UNMATCHED_SECTIONS:1",)
        assert decide(member_review_statuses=[ACCEPTED_STATUS],
                      connection_review_statuses=[], unmatched_sections=[],
                      warnings=[DXF_DROP_LINE]) == ("DXF_DROPPED_EVIDENCE:1",)

    def test_the_lifecycle_still_has_exactly_two_states(self, coverage, derive):
        complete = coverage.coverage_of(total_pages=1, page_numbers=[1])
        assert (j13.DONE, j13.REVIEW) == (DONE, REVIEW)
        assert derive(
            member_review_statuses=[ACCEPTED_STATUS], connection_review_statuses=[],
            unmatched_sections=[], warnings=[], coverage=complete,
        ).status == DONE

    def test_a_complete_coverage_and_a_held_record_is_still_held(self, pdf_boundary):
        run = pdf_boundary([_page(1, [j4._member("M1", EXACT_TOKEN, confidence=50)])])
        assert run.members[0]["review_status"] == BLOCKING_STATUS
        assert run.project_row["status"] == REVIEW
        assert run.summary["parse_failed_pages"] == 0

    def test_the_j4_boundary_can_express_a_truncated_document(self, pdf_boundary):
        """J4's own seam, extended by one argument: the fixture the J4/J5/J6/J7
        suites share can now state a document longer than the run."""
        clean = pdf_boundary([_page(1, [j4._member("M1", EXACT_TOKEN)])])
        assert clean.summary["members_extracted"] == 1
        assert clean.project_row["status"] == DONE

        truncated = pdf_boundary(
            [_page(1, [j4._member("M1", EXACT_TOKEN)])], document_pages=4,
        )
        assert truncated.project_row["status"] == REVIEW
        assert truncated.summary["not_analysed_pages"] == 3

    def test_the_coverage_reaches_the_human_facing_report(self, coverage, pdf_boundary):
        """Not merely internal: the production report states it, so a reader of
        the PDF can see that the drawing set was not fully read."""
        run = pdf_boundary([_page(1, [j4._member("M1", EXACT_TOKEN)])], document_pages=4)
        assert coverage.COVERAGE_PREFIX.strip() in run.pdf_text, run.pdf_text
        assert "not_analysed=3" in run.pdf_text


# ===========================================================================
# 5. No schema change, no widening, no DXF or cad_engine behaviour.
# ===========================================================================
class TestScopeAndPurity:
    def test_no_migration_was_added(self):
        present = sorted(path.name for path in (REPO / "supabase" / "migrations").iterdir())
        assert present == sorted(j13.MIGRATIONS)

    def test_the_coverage_module_is_pure(self):
        tree = ast.parse(COVERAGE_MODULE_PATH.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported == {"dataclasses", "typing"}, sorted(imported)

    def test_the_coverage_module_contains_no_ddl_and_reaches_nothing(self):
        source = COVERAGE_MODULE_PATH.read_text()
        for keyword in ("create table", "alter table", "create function", "create trigger"):
            assert keyword not in source.lower(), keyword
        forbidden = {
            "supabase", "requests", "httpx", "urlopen", "socket", "subprocess",
            "os", "sys", "open", "Path", "PdfReader", "Anthropic", "create_client",
        }
        tree = ast.parse(source)
        named = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                named.add(node.id)
            elif isinstance(node, ast.Attribute):
                named.add(node.attr)
        assert not (named & forbidden), sorted(named & forbidden)

    def test_the_pipeline_did_not_reach_into_cad_engine(self):
        """cad_engine models page coverage for its OWN operator-supplied intake.
        The production PDF path states its coverage from what it read; it does
        not import that model, and does not need to."""
        source = PIPELINE_PATH.read_text()
        assert "cad_engine" not in source
        assert "production_coverage" not in source
        assert CAD_COVERAGE_PATH.exists()

    def test_the_dxf_path_states_no_coverage(self):
        """J15 is a PDF-path milestone. The DXF reader is not touched, writes no
        coverage vocabulary, and calls the derivation with the same four
        evidence inputs it always did."""
        source = DXF_PARSER_PATH.read_text()
        for token in ("page_coverage", "coverage_of", "coverage_from_warnings",
                      "COVERAGE_PREFIX", "coverage="):
            assert token not in source, token
        tree = ast.parse(source)
        call = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "derive_project_status"
        )
        assert [keyword.arg for keyword in call.keywords] == [
            "member_review_statuses", "connection_review_statuses",
            "unmatched_sections", "warnings",
        ]

    def test_the_dxf_run_itself_is_unchanged(self, coverage, production, monkeypatch, tmp_path):
        """The genuine DXF reader, run end to end: same status, same payload
        keys, and no coverage record anywhere in what it persists."""
        double = j13._RecordingSupabaseClient()
        monkeypatch.setattr(production.dxf, "supabase", double)
        monkeypatch.setattr(
            production.matcher_module, "supabase",
            j13._FakeSupabaseClient(list(j13.PINNED_ROWS)),
        )
        path = j4._write_dxf(tmp_path / "j15.dxf", [EXACT_TOKEN])
        summary = production.dxf.parse_dxf_and_save(
            str(path), "j15-dxf-project",
            matcher=production.matcher_module.SectionMatcher(),
        )

        updates = [call for call in double.calls
                   if call["table"] == "projects" and call["op"] == "update"]
        assert len(updates) == 1
        payload = updates[0]["payload"]
        assert set(payload) == j13.DXF_PROJECT_PAYLOAD_KEYS
        assert payload["status"] == REVIEW
        assert coverage.coverage_from_warnings(payload["warnings"]) is None
        assert summary["members_extracted"] == 1
