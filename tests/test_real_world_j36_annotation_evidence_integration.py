"""
J36 — the PDF's own annotation reading, wired into the extraction paths.

WHAT THIS FILE IS

The proof that Milestone J28's annotation evidence layer is now written BY a run, at the
one position J36 chose, on all four extraction paths — and that wiring it changed nothing
else about what those paths mean.

WHAT IT HAS TO PROVE, AND WHY EACH HALF IS SEPARATE

Two claims, and a proof of one is worthless as a proof of the other:

  1. THE READING IS TAKEN. Every path reads the source PDF's own annotation occurrences
     beside the AI's, attributes them to the drawing, the project and the `analysis_run_id`
     that invocation already created, for exactly the pages that invocation read, and files
     the digest of the exact bytes it read.

  2. NOTHING ELSE MOVED. No member, no connection, no review item, no capture, no
     `parse_failed` flag, no coverage record, no project status, no retryability and no
     `error_message` is affected — by a J28 that succeeded, by a J28 that refused, or by a
     J28 whose store does not exist.

The second is asserted the only way it can be honestly asserted: by driving the SAME
document through the SAME paths twice, once with J28 working and once with it broken, and
comparing every engineering artifact the two runs produced. An assertion that only checked
"the run did not raise" would pass just as well against a J28 that had eaten a member write.

WHERE THE FAILURE CASES COME FROM

The two J28 refusals are the layers' own exception types, raised where the layers raise
them. The store-absence case is the client's own code (`PGRST205`) and its own wording
("schema cache"), which is the condition the J29 read surface already treats as an absent
store. Every OTHER database failure — a foreign-key refusal, a duplicate key, a permission
refusal, the append-only trigger, and an unreachable database — must propagate, and each is
asserted to.

The material is genuine: the readings fed to the pipeline are the real captured Arkles and
Selby extractions under `tests/data/`, and the documents the annotation reader is pointed at
are real PDFs carrying real drawn marks, so the rows asserted on below were found by the
real extractor in a real content stream.

NOT IN SCOPE, AND NOT CLAIMED

No migration is applied by this file and none is needed for it: the store is a test double.
Nothing here reaches a database. The J28 migration remains unapplied, and a J28 whose table
does not exist is one of the cases proved below.
"""
from __future__ import annotations

import copy
import hashlib
import io
import types
from pathlib import Path

import pytest

from app.drawing_reading import pdf_annotation_extractor as extractor
from app.engineering_data import pdf_annotation_evidence as storage

from tests import test_real_world_j13_project_status_truth as j13
from tests import test_real_world_j16_pdf_continuation as j16
from tests import test_real_world_j17_parse_failure_retry as j17
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_production_section_authority as authority
from tests.test_real_world_j23_page_extraction_capture import (
    ARKLES_FILE,
    SELBY_RECOVERY_FILE,
    SELBY_WINDOW_FILES,
    _capture_file,
)

REPO = j13.REPO
production = j4.production

PIPELINE_PATH = REPO / "app" / "pipeline.py"
MAIN_PATH = REPO / "app" / "main.py"
EXTRACTOR_PATH = REPO / "app" / "drawing_reading" / "pdf_annotation_extractor.py"
STORAGE_PATH = REPO / "app" / "engineering_data" / "pdf_annotation_evidence.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"
CONNECTION_REVIEW_PATH = REPO / "app" / "engineering_data" / "connection_review_repository.py"
MIGRATIONS = REPO / "supabase" / "migrations"
J28_MIGRATION = MIGRATIONS / "20260927000000_j28_pdf_annotation_occurrences.sql"

CAPTURE_CALL = "insert_page_extraction_captures"
ANNOTATION_CALL = "insert_pdf_annotation_occurrences"


# ===========================================================================
# The genuine readings. Read, never constructed.
# ===========================================================================
def _arkles_pages():
    return _capture_file(ARKLES_FILE)


def _selby_pages():
    pages = []
    for name in SELBY_WINDOW_FILES:
        pages.extend(_capture_file(name))
    return pages


def _selby_recovery():
    return _capture_file(SELBY_RECOVERY_FILE)[0]


def _code_only(text: str) -> str:
    """The file's code and signatures, with every docstring blanked.

    A claim about what a module DOES must not be satisfied — or defeated — by what its
    prose says. Every module in this repository documents its own design at length, so a
    scan over raw text would be asserting about the documentation, not the code.
    """
    import ast

    tree = ast.parse(text)
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
                node.body[0].value, ast.Constant
            ) and isinstance(node.body[0].value.value, str):
                target = node.body[0]
                for number in range(target.lineno, target.end_lineno + 1):
                    lines[number - 1] = ""
    return "\n".join(lines)


# ===========================================================================
# A REAL PDF carrying REAL marks — the material the reader is pointed at.
# ===========================================================================
_MARK = ("B1", 100.0, 700.0)


def _write_annotated_pdf(path, pages, *, mark=_MARK, mark_every_page=True):
    """A real PDF of `pages` pages, with a real drawn mark on each.

    Written by reportlab, so the page carries a genuine text-showing operator in its own
    content stream — which is the only thing `extract_annotations` reads. The mark is
    therefore FOUND, not asserted into existence: a blank page would prove the wiring and
    nothing about the reading, and the reading is half of what this milestone claims.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=letter)
    text, x, y = mark
    for number in range(1, pages + 1):
        if mark_every_page or number == 1:
            sheet.setFont("Helvetica", 10)
            sheet.drawString(x, y, text)
        sheet.showPage()
    sheet.save()
    path.write_bytes(buffer.getvalue())
    return str(path)


# ===========================================================================
# The test-only repository seam: J17's serving double, plus J36's controls.
# ===========================================================================
class _PostgrestRefusal(Exception):
    """A database refusal as the client raises it: a message, and sometimes a code."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


class _J36Repository(j17._RetryRepository):
    """J17's double, with the J36 write answering, controllable and named in `order`.

    Three additions, each because the milestone needs to observe something production now
    does:

      * `insert_pdf_annotation_occurrences` — the J28 write. Recorded in `order` by the J4
        base, and here it can be made to raise, so a test can see what a failed J28 leaves
        behind. A failure injected here is raised INSTEAD of the write, so no row exists.
      * `annotation_rows` — every batch the writer was actually given, verbatim.
      * the three closing writes are named in `order`. The J4 base records only the three
        inserts, and two of J36's claims are about position relative to the CLOSING boundary
        (`update_project_summary`), which no existing double named.
    """

    def __init__(self):
        super().__init__()
        self.annotation_failure = None
        self.annotation_rows = []

    def insert_pdf_annotation_occurrences(self, rows):
        if self.annotation_failure is not None:
            raise self.annotation_failure
        self.annotation_rows.append(copy.deepcopy(rows or []))
        return super().insert_pdf_annotation_occurrences(rows)

    def update_drawing_set(self, drawing_set_id, **fields):
        super().update_drawing_set(drawing_set_id, **fields)
        self.order.append("update_drawing_set")

    def update_analysis_run(self, analysis_run_id, **fields):
        super().update_analysis_run(analysis_run_id, **fields)
        self.order.append("update_analysis_run")

    def update_project_summary(self, project_id, **fields):
        super().update_project_summary(project_id, **fields)
        self.order.append("update_project_summary")


# ===========================================================================
# The harness: the genuine production paths, over a real document.
# ===========================================================================
@pytest.fixture()
def drive(production, monkeypatch, tmp_path):
    """The four production entry points, over a real annotated PDF.

    Nothing is replaced except the vision call itself — the document, its page count, the
    window arithmetic, the run rows, the coverage record, the persistence and the retry
    contract are production's. The annotation reading is production's TOO: it is not
    replaced, and it is the real extractor that finds the rows asserted on below.
    """
    counter = [0]

    def build(*, pages, served, source_name, fails=(), window=30, project_id=None,
              mark_every_page=True, mark=_MARK):
        counter[0] += 1
        project_id = project_id or f"j36-project-{counter[0]}"
        source = _write_annotated_pdf(
            tmp_path / source_name, pages, mark=mark, mark_every_page=mark_every_page)
        storage_path = f"j36-user/{source_name}"

        repository = _J36Repository()
        repository.seed_project(project_id, storage_path=storage_path)

        doc = types.SimpleNamespace(
            project_id=project_id, source=source, storage_path=storage_path,
            repository=repository, user_id="j36-user", pages=pages,
            model=production.pipeline.PDF_VISION_MODEL,
        )

        def serve(mapping, *, failed=()):
            def stand_in(filepath, user_id, pid, drawing_id, max_pages, *, first_page=1,
                         **kwargs):
                last = first_page + max_pages - 1
                out = []
                for number in range(first_page, last + 1):
                    if number in failed:
                        out.append(production.PageExtraction(page_number=number, parse_failed=True))
                    elif number in mapping:
                        payload = mapping[number]
                        out.append(production.PageExtraction(
                            page_number=payload["page_number"],
                            drawing_number=payload["drawing_number"],
                            drawing_title=payload["drawing_title"],
                            revision=payload["revision"],
                            raw_members=copy.deepcopy(payload["raw_members"]),
                            raw_connections=copy.deepcopy(payload["raw_connections"]),
                            parse_failed=payload["parse_failed"],
                        ))
                return out
            monkeypatch.setattr(production.pipeline, "analyze_pdf_pages", stand_in)

        serve({page["page_number"]: page for page in served}, failed=fails)
        monkeypatch.setattr(production.pipeline, "repo", repository)
        monkeypatch.setattr(production.pipeline, "MAX_PDF_PAGES", window)
        # NO NETWORK: the matcher's own reference read is answered from the pinned index.
        monkeypatch.setattr(production.matcher_module, "supabase",
                            authority._FakeSupabaseClient(authority._pinned_index()))

        doc.serve = serve
        doc.extract = lambda: production.pipeline.parse_pdf_and_save(
            source, project_id, "j36-user", storage_path)
        doc.read_next_window = lambda **kw: production.pipeline.continue_pdf_extraction(
            source, project_id, "j36-user", storage_path, **kw)
        doc.retry = lambda **kw: production.pipeline.retry_pdf_page(
            source, project_id, "j36-user", storage_path, **kw)
        return doc

    return build


def _last_attempt(calls):
    """The calls of the LAST extraction attempt, taken off the shared `order` list.

    A document driven through two invocations leaves both attempts in one list, and
    `list.index` finds the first. Every ordering claim below is about one invocation, so it
    is made on the tail that begins at that invocation's own capture write — the write every
    path makes first.
    """
    first = calls.index(CAPTURE_CALL)
    second = calls.index(CAPTURE_CALL, first + 1)
    return calls[second:]


def _before(calls, first, second):
    assert first in calls, f"{first} was never called; the ordering claim is vacuous"
    assert second in calls, f"{second} was never called; the ordering claim is vacuous"
    return calls.index(first) < calls.index(second)


def _engineering_state(repository):
    """Every engineering artifact one run produced, for a two-run comparison."""
    return {
        "members": repository.members,
        "review_items": repository.review_items,
        "connections": repository.connections,
        "bolt_groups": repository.bolt_groups,
        "connection_plates": repository.connection_plates,
        "weld_details": repository.weld_details,
        "connection_member_links": repository.connection_member_links,
        "project_summary": repository.project_summary,
        "captures": repository.captures,
    }


# ===========================================================================
# A. The whole-document path writes J28 between the capture and the members.
# ===========================================================================
class TestTheWholeDocumentPath:
    def test_the_annotation_reading_is_taken_between_the_capture_and_the_members(self, drive):
        """P1: captures → J28 → engineering. The order the four paths must all make.

        Read off the sequence production actually produced, not off the source: a run whose
        J28 write sat after `insert_members` would satisfy every other test in this file.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-order-whole.pdf",
                    window=len(pages))
        doc.extract()

        calls = doc.repository.order
        assert _before(calls, CAPTURE_CALL, ANNOTATION_CALL)
        assert _before(calls, ANNOTATION_CALL, "insert_members")

    def test_the_annotation_reading_precedes_the_project_summary(self, drive):
        """T: the closing boundary is written after the reading, never before it."""
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-order-whole-2.pdf",
                    window=len(pages))
        doc.extract()

        assert _before(doc.repository.order, ANNOTATION_CALL, "update_project_summary")

    def test_every_page_this_run_read_is_the_page_scope_it_asked_the_reader_for(
        self, drive, production, monkeypatch
    ):
        """E: the whole document, page by page, and the identity the rows carry.

        The scope is spied on at the reader itself, so what is asserted is the pages
        production ASKED FOR — an implementation that asked for the whole document and then
        filtered could not pass this.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-scope-whole.pdf",
                    window=len(pages))

        seen = []
        real = production.pipeline.extract_annotations

        def spy(pdf, *, pages=None, include_geometry=True):
            seen.append((str(pdf), None if pages is None else list(pages)))
            return real(pdf, pages=pages, include_geometry=include_geometry)

        monkeypatch.setattr(production.pipeline, "extract_annotations", spy)
        doc.extract()

        assert len(seen) == 1, seen
        filepath, asked = seen[0]
        assert filepath == doc.source, "the reading must be taken from this run's own source"
        assert asked == list(range(1, len(pages) + 1))

        drawing_id = next(iter(doc.repository.drawings))
        run_id = next(iter(doc.repository.analysis_runs))
        rows = doc.repository.annotation_rows[-1]
        assert rows, "the document carries real marks; the reading found none"
        assert {row["page_number"] for row in rows} == set(range(1, len(pages) + 1))
        for row in rows:
            assert row["drawing_id"] == drawing_id
            assert row["project_id"] == doc.project_id
            assert row["analysis_run_id"] == run_id
            assert row["extractor_version"] == extractor.EXTRACTOR_VERSION

    def test_the_recorded_digest_is_the_digest_of_the_exact_bytes_read(self, drive):
        """F: no second hashing rule, and no re-derivation of the document.

        The extractor opens the file and hashes what it read; the rows must carry that
        value. Asserted against an independent hash of the same file.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-digest.pdf",
                    window=len(pages))
        doc.extract()

        digest = hashlib.sha256(Path(doc.source).read_bytes()).hexdigest()
        rows = doc.repository.annotation_rows[-1]
        assert rows
        assert {row["source_pdf_sha256"] for row in rows} == {digest}
        assert digest == extractor.extract_annotations(Path(doc.source).read_bytes()).source_pdf_sha256


# ===========================================================================
# B/C/D. The three other paths, each at P1.
# ===========================================================================
class TestTheContinuationPath:
    def test_the_window_is_read_and_written_before_the_coverage_commit(self, drive):
        """S: captures → J28 → members → … → the commit, on the window path.

        The commit is `update_project_summary`, which carries the coverage line the next
        boundary is derived from. After it the window is answered and never re-read, so a
        reading not yet taken could never be taken.
        """
        pages = _arkles_pages()
        doc = drive(pages=30, served=pages, source_name="j36-order-window.pdf", window=28)
        doc.extract()
        doc.read_next_window()

        attempt = _last_attempt(doc.repository.order)
        assert _before(attempt, CAPTURE_CALL, ANNOTATION_CALL)
        assert _before(attempt, ANNOTATION_CALL, "insert_members")
        assert _before(attempt, ANNOTATION_CALL, "update_project_summary")

    def test_only_the_windows_own_pages_are_asked_for(self, drive, production, monkeypatch):
        """The page scope is the invocation's, not the document's.

        Window two of the Arkles set is pages 29-30. A reading asked for all 30 pages here
        would record rows for 28 pages the commit says were read by an earlier run.
        """
        pages = _arkles_pages()
        doc = drive(pages=30, served=pages, source_name="j36-scope-window.pdf", window=28)
        doc.extract()

        seen = []
        real = production.pipeline.extract_annotations

        def spy(pdf, *, pages=None, include_geometry=True):
            seen.append(None if pages is None else list(pages))
            return real(pdf, pages=pages, include_geometry=include_geometry)

        monkeypatch.setattr(production.pipeline, "extract_annotations", spy)
        doc.read_next_window()

        assert seen == [[29, 30]], seen
        rows = doc.repository.annotation_rows[-1]
        assert {row["page_number"] for row in rows} == {29, 30}

    def test_the_windows_run_is_the_one_the_window_wrote(self, drive):
        """E, on the window path: the attempt identity is the run this invocation created."""
        pages = _arkles_pages()
        doc = drive(pages=30, served=pages, source_name="j36-run-window.pdf", window=28)
        doc.extract()
        before = dict(doc.repository.analysis_runs)
        doc.read_next_window()

        new_runs = {run for run in doc.repository.analysis_runs if run not in before}
        assert len(new_runs) == 1
        run_id = next(iter(new_runs))
        assert run_id in doc.repository.analysis_runs
        rows = doc.repository.annotation_rows[-1]
        assert rows
        assert {row["analysis_run_id"] for row in rows} == {run_id}


class TestTheRetryPath:
    def test_a_successful_retry_reads_the_page_before_clearing_the_failure(self, drive):
        """R: captures → J28 → members → … → the write that stops naming the page failed.

        That write is what makes the page permanently answered, so it must come last.
        """
        served = {page["page_number"]: page for page in _selby_pages()}
        recovery = _selby_recovery()
        doc = drive(pages=32, served=list(served.values()), source_name="j36-order-retry.pdf",
                    window=32, fails=(26,))
        doc.extract()

        doc.serve({**served, 26: recovery})
        doc.retry(page_number=26)

        attempt = _last_attempt(doc.repository.order)
        assert _before(attempt, CAPTURE_CALL, ANNOTATION_CALL)
        assert _before(attempt, ANNOTATION_CALL, "insert_members")
        assert _before(attempt, ANNOTATION_CALL, "update_project_summary")

    def test_the_retry_asks_for_exactly_the_page_it_was_named_for(self, drive, production,
                                                                 monkeypatch):
        """The retry reads one page, so one page is what the reader is asked about."""
        served = {page["page_number"]: page for page in _selby_pages()}
        recovery = _selby_recovery()
        doc = drive(pages=32, served=list(served.values()), source_name="j36-scope-retry.pdf",
                    window=32, fails=(26,))
        doc.extract()

        seen = []
        real = production.pipeline.extract_annotations

        def spy(pdf, *, pages=None, include_geometry=True):
            seen.append(None if pages is None else list(pages))
            return real(pdf, pages=pages, include_geometry=include_geometry)

        monkeypatch.setattr(production.pipeline, "extract_annotations", spy)
        doc.serve({**served, 26: recovery})
        doc.retry(page_number=26)

        assert seen == [[26]], seen
        rows = doc.repository.annotation_rows[-1]
        assert {row["page_number"] for row in rows} == {26}

    def test_a_retry_that_fails_again_is_still_read_and_recorded(self, drive, production):
        """Q: the parse-failed branch returns before engineering, so it needs its own call.

        A page whose AI response could not be parsed is exactly the page a human is sent to,
        and the file itself was read perfectly well — the bytes do not fail to parse. Its
        annotation reading is therefore taken, and taken BEFORE the run is closed.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-failed-retry.pdf",
                    window=len(pages))
        doc.extract()
        doc.repository.order.clear()
        doc.repository.annotation_rows.clear()

        outcome = doc.retry(page_number=11)  # the same genuinely unreadable page

        assert outcome["outcome"] == "PARSE_FAILED"
        assert outcome["retryable"] is True
        assert outcome["members_extracted"] == 0
        attempt = doc.repository.order
        assert _before(attempt, CAPTURE_CALL, ANNOTATION_CALL)
        assert _before(attempt, ANNOTATION_CALL, "update_analysis_run")
        rows = doc.repository.annotation_rows[-1]
        assert {row["page_number"] for row in rows} == {11}


# ===========================================================================
# G. A page read twice: two attempts, both kept, neither ordered by its id.
# ===========================================================================
class TestRepeatedReadings:
    def test_two_attempts_at_one_page_record_two_readings_under_two_runs(self, drive):
        """The attempt is in the occurrence identity, and the extractor is deterministic.

        Page 26 of the Selby set is read by the whole-document window and read again by the
        retry. The two readings report the SAME coordinates — the drawing did not change —
        so on every other column the second would collide with the first. The run id is what
        keeps them apart, and it is a name, not an order.
        """
        served = {page["page_number"]: page for page in _selby_pages()}
        recovery = _selby_recovery()
        doc = drive(pages=32, served=list(served.values()), source_name="j36-twice.pdf",
                    window=32, fails=(26,))
        doc.extract()
        doc.serve({**served, 26: recovery})
        doc.retry(page_number=26)

        rows = [row for batch in doc.repository.annotation_rows for row in batch]
        page_26 = [row for row in rows if row["page_number"] == 26]
        assert len(page_26) == 2, page_26

        runs = {row["analysis_run_id"] for row in page_26}
        assert len(runs) == 2, "two READINGS of one page must be two attempts"

        # Structurally identical apart from the attempt: same page, same position, same
        # rule set. That is exactly why the attempt has to be in the key.
        positions = {(row["annotation_x"], row["annotation_y"], row["extractor_version"])
                     for row in page_26}
        assert len(positions) == 1, positions
        # The rule set travels with each reading, and is a list of pairs rather than a
        # hashable — compared as values, not as a set of rows.
        assert all(row["rule_set"] for row in page_26), page_26
        assert page_26[0]["rule_set"] == page_26[1]["rule_set"]

    def test_the_two_readings_stand_apart_by_the_rule_the_layer_already_states(self, drive):
        """And the standing rule is untouched by J36: it is still the storage layer's.

        `authoritative_occurrences` orders by `(extracted_at, extractor_version)` — the row's
        own values — and never by the run id. J36 rewrote neither the rule nor the column it
        reads; this asserts the pipeline did not become a second place that decides.
        """
        source = _code_only(PIPELINE_PATH.read_text(encoding="utf-8"))
        assert "authoritative_occurrences" not in source
        assert "analysis_run_id" in source  # it is passed, and only passed
        for name in ("_standing", "_page_key", "_identity_key", "extracted_at"):
            assert name not in source, name


# ===========================================================================
# H/I/J. J28 refuses or is absent: the run continues exactly as it would have.
# ===========================================================================
def _drive_pair(drive, source_name, pages, window, project_id="j36-isolation"):
    """Two runs of the identical document and project: one control, one to be broken.

    Identical `project_id` and identical source name, so every identifier either run
    generates is generated the same way and the two are comparable field for field.
    """
    control = drive(pages=pages, served=_arkles_pages(), source_name=source_name,
                    window=window, project_id=project_id)
    control.extract()
    return control


class TestAJ28ThatCannotRunChangesNothing:
    def test_an_extraction_refusal_leaves_every_engineering_artifact_identical(
        self, drive, production, monkeypatch
    ):
        """H: `AnnotationExtractionRefused` — a source that cannot be read as a PDF."""
        pages = _arkles_pages()
        control = _drive_pair(drive, "j36-h.pdf", len(pages), len(pages))
        assert control.repository.annotation_rows[-1], "the control must really have read"

        broken = drive(pages=len(pages), served=pages, source_name="j36-h.pdf",
                       window=len(pages), project_id="j36-isolation")

        def refuse(*args, **kwargs):
            raise extractor.AnnotationExtractionRefused("the source could not be read")

        monkeypatch.setattr(production.pipeline, "extract_annotations", refuse)
        broken.extract()

        assert broken.repository.annotation_rows == []
        assert ANNOTATION_CALL not in broken.repository.order
        assert _engineering_state(broken.repository) == _engineering_state(control.repository)
        assert broken.repository.project_summary == control.repository.project_summary
        assert broken.repository.project_summary["status"] == \
            control.repository.project_summary["status"]

    def test_an_evidence_refusal_leaves_every_engineering_artifact_identical(
        self, drive, production, monkeypatch
    ):
        """I: `AnnotationEvidenceRefused` — a reading the row builder will not carry."""
        pages = _arkles_pages()
        control = _drive_pair(drive, "j36-i.pdf", len(pages), len(pages))

        broken = drive(pages=len(pages), served=pages, source_name="j36-i.pdf",
                       window=len(pages), project_id="j36-isolation")

        def refuse(*args, **kwargs):
            raise storage.AnnotationEvidenceRefused("a reading was offered with no drawing_id")

        monkeypatch.setattr(production.pipeline, "occurrence_rows", refuse)
        broken.extract()

        assert broken.repository.annotation_rows == []
        assert _engineering_state(broken.repository) == _engineering_state(control.repository)

    def test_a_coordinate_the_table_cannot_store_is_refused_before_the_write(self, drive):
        """J37B: the refusal is the STORAGE LAYER's, and production is unaffected by it.

        The mark is drawn outside the page, so the real extractor reports a real negative
        coordinate. Nothing is monkeypatched here: the refusal is the genuine
        `AnnotationEvidenceRefused` the storage layer now raises, and J36 already swallows
        exactly that. The consequence is the one sections H and I prove by force — the capture
        is recorded, nothing reaches the annotation table, and every engineering artifact is
        identical to a run whose reading was inside the domain.

        Before J37B this same reading was carried to the writer and came back as a 23514,
        which propagates by design: the whole extraction failed, and every retry of it failed
        identically, because the extractor is deterministic.
        """
        pages = _arkles_pages()
        control = _drive_pair(drive, "j36-j37b.pdf", len(pages), len(pages))
        assert control.repository.annotation_rows[-1], "the control must really have read"

        poisoned = drive(pages=len(pages), served=pages, source_name="j36-j37b.pdf",
                         window=len(pages), project_id="j36-isolation",
                         mark=("B1", -30.0, 700.0))

        # The poisoning is REAL, and it is the producer that produced it: this milestone
        # changed the storage layer and deliberately left the extractor reporting what the
        # content stream says.
        found = extractor.extract_annotations(poisoned.source, pages=[1]).occurrences
        assert found and all(o.annotation_x == -30.0 for o in found), (
            "the extractor must still report the raw coordinate it read"
        )

        poisoned.extract()

        assert poisoned.repository.annotation_rows == []
        assert ANNOTATION_CALL not in poisoned.repository.order
        assert poisoned.repository.captures == control.repository.captures
        assert _engineering_state(poisoned.repository) == _engineering_state(control.repository)
        assert poisoned.repository.project_summary == control.repository.project_summary
        assert "update_project_summary" in poisoned.repository.order, "the run must complete"

    def test_an_absent_store_is_not_a_failure(self, drive):
        """J: `PGRST205` — the migration is unapplied, so the table is not there.

        The condition the J29 read surface already treats as an absent store. The run must
        complete normally: this is the state production is genuinely in today, and every
        extraction path has to work in it.
        """
        pages = _arkles_pages()
        control = _drive_pair(drive, "j36-j.pdf", len(pages), len(pages))

        broken = drive(pages=len(pages), served=pages, source_name="j36-j.pdf",
                       window=len(pages), project_id="j36-isolation")
        broken.repository.annotation_failure = _PostgrestRefusal(
            "PGRST205",
            "Could not find the table 'public.pdf_annotation_occurrences' in the schema cache",
        )
        broken.extract()

        assert broken.repository.annotation_rows == []
        assert _engineering_state(broken.repository) == _engineering_state(control.repository)
        assert J28_MIGRATION.exists(), "the migration this state describes is unapplied, not absent"

    def test_the_schema_cache_wording_alone_is_the_same_state(self, drive):
        """The client does not always carry a code, so its own wording is the same state."""
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-j-wording.pdf",
                    window=len(pages))
        doc.repository.annotation_failure = _PostgrestRefusal(
            None, "The table was not found in the schema cache.",
        )
        doc.extract()

        assert doc.repository.annotation_rows == []
        assert "update_project_summary" in doc.repository.order, "the run must complete"


# ===========================================================================
# K/O. Everything else propagates. Each code means something J36 must not hide.
# ===========================================================================
class TestEverythingElsePropagates:
    @pytest.mark.parametrize("code,meaning", [
        ("23503", "the run the occurrence is attributed to does not exist"),
        ("23505", "one attempt wrote one occurrence twice — a defect, not a replay"),
        ("42501", "the writer's grant is wrong"),
        ("P0001", "the append-only trigger refused a rewrite"),
    ])
    def test_a_named_database_refusal_is_never_swallowed(self, drive, code, meaning):
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages,
                    source_name=f"j36-{code}.pdf", window=len(pages))
        doc.repository.annotation_failure = _PostgrestRefusal(code, meaning)

        with pytest.raises(_PostgrestRefusal) as raised:
            doc.extract()
        assert raised.value.code == code

    def test_an_unreachable_database_is_not_an_absent_table(self, drive):
        """A network fault is not a missing table, and must not be reported as one.

        The predicate matches the client's absence code and the client's own absence
        wording. A connection that dropped carries neither, so it reaches the caller.
        """
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-network.pdf",
                    window=len(pages))
        doc.repository.annotation_failure = ConnectionError("connection reset by peer")

        with pytest.raises(ConnectionError):
            doc.extract()

    def test_an_unexpected_python_failure_is_never_swallowed(self, drive):
        """O: a defect in the J28 step must be visible, not absorbed as an absence."""
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-unexpected.pdf",
                    window=len(pages))
        doc.repository.annotation_failure = ZeroDivisionError("division by zero")

        with pytest.raises(ZeroDivisionError):
            doc.extract()

    def test_a_propagation_on_the_retry_path_is_not_turned_into_a_refusal(self, drive):
        """The retry's own contract is unchanged: a J28 defect does not read as a refusal.

        `retry_pdf_page` raises `RetryRefused` for the states IT decides. A J28 failure is
        not one of them, and must not be reported as though the page had been answered.
        """
        served = {page["page_number"]: page for page in _selby_pages()}
        recovery = _selby_recovery()
        doc = drive(pages=32, served=list(served.values()), source_name="j36-retry-raise.pdf",
                    window=32, fails=(26,))
        doc.extract()

        doc.serve({**served, 26: recovery})
        doc.repository.annotation_failure = _PostgrestRefusal("23505", "duplicate key value")

        with pytest.raises(_PostgrestRefusal):
            doc.retry(page_number=26)


# ===========================================================================
# P. A J28 that succeeded changed nothing about what the run means.
# ===========================================================================
class TestASuccessfulJ28ChangesNothingElse:
    def test_the_engineering_state_is_identical_with_and_without_the_reading(
        self, drive, production, monkeypatch
    ):
        """The positive half of the isolation claim.

        The same document, project, window and readings, driven twice: once with J28
        writing its rows and once with J28 unable to run at all. Every engineering artifact
        must be equal — and the working run must genuinely have written, or the comparison
        is between two runs that both did nothing.
        """
        pages = _arkles_pages()
        working = drive(pages=len(pages), served=pages, source_name="j36-p.pdf",
                        window=len(pages), project_id="j36-p")
        working.extract()

        without = drive(pages=len(pages), served=pages, source_name="j36-p.pdf",
                        window=len(pages), project_id="j36-p")

        def refuse(*args, **kwargs):
            raise extractor.AnnotationExtractionRefused("no reading available")

        monkeypatch.setattr(production.pipeline, "extract_annotations", refuse)
        without.extract()

        assert working.repository.annotation_rows[-1], "J28 must really have written"
        assert without.repository.annotation_rows == []
        assert _engineering_state(working.repository) == _engineering_state(without.repository)

    def test_the_page_capture_and_the_coverage_record_are_untouched(self, drive):
        """J23's reading and J15/J16's coverage are not this milestone's to change."""
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-p-coverage.pdf",
                    window=len(pages))
        doc.extract()

        assert len(doc.repository.captures) == len(pages)
        assert [row["page_number"] for row in doc.repository.captures] == \
            [page["page_number"] for page in pages]
        summary = doc.repository.project_summary
        assert "analysed" in " ".join(str(v) for v in summary.values())

    def test_a_failed_page_stays_failed_and_stays_retryable(self, drive):
        """`parse_failed` enters J28 not at all: the flag is the coverage record's fact."""
        pages = _arkles_pages()
        doc = drive(pages=len(pages), served=pages, source_name="j36-p-failed.pdf",
                    window=len(pages))
        doc.extract()

        failed = [row["page_number"] for row in doc.repository.captures if row["parse_failed"]]
        assert failed == [2, 11, 12, 13, 22, 29]
        # And J28 read those pages anyway, because the FILE was readable.
        rows = doc.repository.annotation_rows[-1]
        assert set(failed) <= {row["page_number"] for row in rows}


# ===========================================================================
# The wiring is exactly this, and no more.
# ===========================================================================
class TestTheWiringIsExactlyThisMuch:
    def test_the_pipeline_holds_no_second_transport_and_no_second_identity(self):
        """J36 adds a call, not a mechanism.

        The pipeline reaches the occurrence writer through the `repo` module — the same
        transport J23's capture uses — and the extractor and the row builder directly. It
        opens no client, reads no environment, and decides no identity of its own.
        """
        source = _code_only(PIPELINE_PATH.read_text(encoding="utf-8"))
        for forbidden in ("supabase", "requests", "httpx", "environ", "storage3"):
            assert forbidden not in source, f"app/pipeline.py names {forbidden}"
        assert "repo.insert_pdf_annotation_occurrences(" in source
        assert "extract_annotations(" in source
        assert "occurrence_rows(" in source
        # The J28 pieces it uses are the layers' own, imported, not restated.
        assert "extractor_version" not in source

    def test_the_absence_predicate_matches_the_two_conditions_and_no_others(self):
        """The predicate, read as code: one code, one wording, and nothing else.

        A predicate that matched more would be a predicate that hid failures; this asserts
        the literal it matches on, and that the swallow list is the two J28 refusals.
        """
        source = _code_only(PIPELINE_PATH.read_text(encoding="utf-8"))
        assert 'PGRST205' in source
        assert 'schema cache' in source
        assert "_ANNOTATION_STORE_ABSENT_CODE" in source
        for code in ("23503", "23505", "23514", "42501", "P0001"):
            assert code not in source, code

    def test_the_swallow_site_names_exactly_the_two_j28_refusals(self):
        """Named, not caught by breadth: an `except Exception` alone would swallow anything."""
        text = PIPELINE_PATH.read_text(encoding="utf-8")
        marker = "def _record_annotation_evidence("
        body = text.split(marker, 1)[1].split("\ndef ", 1)[0]

        # The two J28 refusals are named in one clause, and named by type rather than by
        # breadth: a bare `except Exception` here would swallow every database failure too.
        assert "except (AnnotationExtractionRefused, AnnotationEvidenceRefused):" in body
        # What is left is caught only so it can be INSPECTED, and re-raised unless the store
        # is the thing that is absent.
        assert "except Exception as error:" in body
        assert "if _annotation_store_is_absent(error):" in body
        assert "        raise\n" in body, "the unmatched failure must be re-raised"

        # The failure boundary covers exactly the two steps it must: the reading, the row
        # construction and the write, and nothing after them.
        inside = body[body.index("    try:"):body.index("    except (Annotation")]
        assert "extract_annotations(" in inside
        assert "occurrence_rows(" in inside
        assert "repo.insert_pdf_annotation_occurrences(" in inside
        assert inside.index("extract_annotations(") < inside.index("occurrence_rows(") \
            < inside.index("repo.insert_pdf_annotation_occurrences(")

    def test_the_pipeline_imports_both_j28_layers_from_the_layers_themselves(self):
        """J36 imported; it did not copy a rule, a version or a key shape."""
        text = PIPELINE_PATH.read_text(encoding="utf-8")
        assert "from app.drawing_reading.pdf_annotation_extractor import (" in text
        assert "from app.engineering_data.pdf_annotation_evidence import (" in text
        for name in ("AnnotationExtractionRefused", "AnnotationEvidenceRefused",
                     "extract_annotations", "occurrence_rows"):
            assert name in text, name
        # The milestone touched neither layer's semantics.
        for path in (EXTRACTOR_PATH, STORAGE_PATH):
            assert path.exists(), path

    def test_the_migration_is_still_exactly_the_eight_that_existed(self):
        """J36 edits no migration and applies none. The eight are the six J36 found plus
        J44's and J61's, each added by its own milestone and naming itself here so that a
        ninth cannot appear unremarked. J61's gives a source document an identity; it
        touches neither the extractor nor the annotation storage J36 wired in."""
        names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
        assert names == [
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
        ]
        sql = J28_MIGRATION.read_text(encoding="utf-8")
        assert "pdf_annotation_occurrences" in sql

    def test_no_http_surface_reaches_the_annotation_reader(self):
        """J36 is a pipeline milestone: no route, no request-time reading, no new I/O."""
        main = _code_only(MAIN_PATH.read_text(encoding="utf-8"))
        for forbidden in ("pdf_annotation", "extract_annotations", "occurrence_rows"):
            assert forbidden not in main, forbidden

    def test_the_connection_review_store_gained_no_caller_and_no_writer(self):
        """Unrelated to this milestone, and pinned here because J36 touched pipeline.py."""
        source = CONNECTION_REVIEW_PATH.read_text(encoding="utf-8")
        assert "pdf_annotation" not in source
        pipeline = _code_only(PIPELINE_PATH.read_text(encoding="utf-8"))
        assert "connection_review_repository" not in pipeline
